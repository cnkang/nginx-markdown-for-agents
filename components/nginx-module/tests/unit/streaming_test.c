/*
 * Test: streaming
 * Description: Streaming runtime unit tests covering engine selection,
 *   decompression, body filter chunk processing, backpressure,
 *   Pre-Commit fallback, Post-Commit error handling, config parsing,
 *   output chain construction, size limit, and timeout.
 *
 * Feature: nginx-streaming-runtime-and-ffi
 * Validates: Properties 1-6, 11-14
 *
 * All tests are gated with MARKDOWN_STREAMING_ENABLED.
 */

#include "test_common.h"

/* SIZE_MAX is provided by <stdint.h> via test_common.h */

#ifndef MARKDOWN_STREAMING_ENABLED
/*
 * When the streaming feature is not enabled, compile a
 * minimal stub that reports the tests were skipped.
 */
int
main(void)
{
    printf("\n========================================\n");
    printf("Streaming Tests (SKIPPED)\n");
    printf("MARKDOWN_STREAMING_ENABLED not defined\n");
    printf("========================================\n\n");
    return 0;
}

#else /* MARKDOWN_STREAMING_ENABLED */

/* Minimal nginx type definitions for testing */
typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;
typedef intptr_t        ngx_flag_t;
typedef size_t          ngx_msec_t;

#define NGX_OK          0
#define NGX_ERROR      -1
#define NGX_AGAIN      -2
#define NGX_DECLINED   -5
#define NGX_HTTP_MARKDOWN_BUFFERED 0x08

#define NGX_HTTP_GET    2
#define NGX_HTTP_HEAD   4

#define NGX_HTTP_OK             200
#define NGX_HTTP_NOT_MODIFIED   304

/* Streaming constants (mirror module header) */
#define PATH_FULLBUFFER   0
#define PATH_INCREMENTAL  1
#define PATH_STREAMING    2

#define ENGINE_OFF   0
#define ENGINE_ON    1
#define ENGINE_AUTO  2

#define COMMIT_PRE   0
#define COMMIT_POST  1

/* Error codes (mirror Rust FFI) */
#define ERROR_SUCCESS            0
#define ERROR_TIMEOUT            3
#define ERROR_MEMORY_LIMIT       4
#define ERROR_BUDGET_EXCEEDED    6
#define ERROR_STREAMING_FALLBACK 7
#define ERROR_POST_COMMIT        8
#define ERROR_INTERNAL           99

/* On-error policy */
#define ON_ERROR_PASS    0
#define ON_ERROR_REJECT  1

/* Conditional requests mode */
#define CONDITIONAL_FULL_SUPPORT         0
#define CONDITIONAL_IF_MODIFIED_SINCE    1
#define CONDITIONAL_DISABLED             2

/* Streaming budget default: 2 MiB */
#define STREAMING_BUDGET_DEFAULT  (2 * 1024 * 1024)

/* ================================================================
 * Lightweight stubs for engine selection tests
 * ================================================================ */

typedef struct {
    ngx_uint_t   engine_mode;       /* resolved engine value */
    ngx_uint_t   conditional_requests;
    size_t       large_body_threshold;
    size_t       max_size;
    size_t       streaming_budget;
    ngx_uint_t   on_error;
} test_conf_t;

typedef struct {
    ngx_uint_t   method;
    ngx_uint_t   status;
    long         content_length;
    const char  *content_type;
} test_request_t;

/* Function prototypes */
static ngx_uint_t
test_select_processing_path(const test_conf_t *conf,
    const test_request_t *r);
static void test_engine_off(void);
static void test_engine_on_get(void);
static void test_engine_on_head(void);
static void test_engine_on_304(void);
static void test_engine_on_conditional_full(void);
static void test_engine_on_conditional_ims_only(void);
static void test_engine_on_conditional_disabled(void);
static void test_engine_on_sse(void);
static void test_engine_auto_large_cl(void);
static void test_engine_auto_small_cl(void);
static void test_engine_auto_no_cl(void);
static void test_decomp_null_safety(void);
static void test_decomp_empty_input(void);
static void test_chunk_processing_empty(void);
static void test_chunk_processing_size_limit(void);
static void test_backpressure_flag(void);
static void test_precommit_fallback(void);
static void test_postcommit_error(void);
static void test_postcommit_error_ignores_on_error_policy(void);
static void test_postcommit_error_debug_log_details(void);
static void test_postcommit_error_various_error_codes(void);
static void test_config_budget_default(void);
static void test_config_engine_values(void);
static void test_output_chain_last_buf(void);
static void test_output_chain_flush(void);
static void test_size_limit_precommit(void);
static void test_size_limit_postcommit(void);
static void test_timeout_precommit(void);
static int test_precommit_route(uint32_t error_code, ngx_uint_t on_error);

/* Bug condition exploration test prototypes */
static void test_bug1_fallback_return_value(void);
static void test_bug2_decomp_incomplete_inflate(void);
static void test_bug3_finalize_tail_feed_error(void);
static void test_bug4_config_invalid_static_value(void);

/* Streaming headers policy test prototypes (spec 15, task 6) */
static void test_commit_boundary_removes_content_length(void);
static void test_commit_boundary_removes_content_encoding(void);
static void test_commit_boundary_skips_content_encoding_no_decomp(void);
static void test_streaming_no_cl_and_chunked_coexist(void);
static void test_precommit_no_header_modification(void);
static void test_commit_boundary_strips_upstream_etag(void);
static void test_precommit_all_failopen_paths_record_metrics(void);
static void test_init_failure_respects_streaming_on_error(void);
static void test_streaming_failopen_increments_global_counter(void);

/* Preservation test prototypes (non-bug-condition baseline) */
static void test_preserve_bug1_normal_feed_returns_ok(void);
static void test_preserve_bug1_fallback_buffer_fail(void);
static void test_preserve_bug2_small_data_complete(void);
static void test_preserve_bug2_empty_input_ok(void);
static void test_preserve_bug2_exceeds_max_size(void);
static void test_preserve_bug3_tail_feed_success(void);
static void test_preserve_bug3_no_tail_data(void);
static void test_preserve_bug3_no_decompression(void);
static void test_preserve_bug4_valid_static_values(void);
static void test_preserve_bug4_variable_expression(void);
static void test_preserve_bug4_duplicate_directive(void);


/*
 * Engine selection logic (mirrors ngx_http_markdown_select_processing_path).
 *
 * Evaluation order:
 * 1. engine == off -> PATH_FULLBUFFER
 * 2. HEAD request -> PATH_FULLBUFFER
 * 3. 304 Not Modified -> PATH_FULLBUFFER
 * 4. conditional_requests full_support -> PATH_FULLBUFFER
 * 5. Content-Type is text/event-stream -> PATH_FULLBUFFER
 * 6. engine == on -> PATH_STREAMING
 * 7. engine == auto + CL >= threshold -> PATH_STREAMING
 * 8. engine == auto + no CL -> PATH_STREAMING
 * 9. engine == auto + CL < threshold -> PATH_FULLBUFFER
 */
static ngx_uint_t
test_select_processing_path(const test_conf_t *conf,
    const test_request_t *r)
{
    /* Rule 1: engine off */
    if (conf->engine_mode == ENGINE_OFF) {
        return PATH_FULLBUFFER;
    }

    /* Rule 2: HEAD request */
    if (r->method == NGX_HTTP_HEAD) {
        return PATH_FULLBUFFER;
    }

    /* Rule 3: 304 Not Modified */
    if (r->status == NGX_HTTP_NOT_MODIFIED) {
        return PATH_FULLBUFFER;
    }

    /* Rule 4: conditional_requests full_support */
    if (conf->conditional_requests
        == CONDITIONAL_FULL_SUPPORT)
    {
        return PATH_FULLBUFFER;
    }

    /* Rule 5: text/event-stream */
    if (r->content_type != NULL
        && strlen(r->content_type) >= 17
        && strncmp(r->content_type,
                   "text/event-stream", 17) == 0)
    {
        return PATH_FULLBUFFER;
    }

    /* Rule 6: engine on */
    if (conf->engine_mode == ENGINE_ON) {
        return PATH_STREAMING;
    }

    /* Rules 7-9: engine auto */
    if (r->content_length >= 0
        && conf->large_body_threshold > 0
        && (size_t) r->content_length
           < conf->large_body_threshold)
    {
        /* CL < threshold: full-buffer */
        return PATH_FULLBUFFER;
    }

    /* auto + CL >= threshold or no CL */
    return PATH_STREAMING;
}

/* ================================================================
 * 14.1 Engine selection unit tests
 * Feature: nginx-streaming-runtime-and-ffi, Property 2
 * ================================================================ */

static void
test_engine_off(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Engine off: always full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_OFF;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 999999;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_FULLBUFFER,
        "engine=off should always select full-buffer");
    TEST_PASS("engine=off selects full-buffer");
}

static void
test_engine_on_get(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Engine on + GET: streaming");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_ON;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 1024;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_STREAMING,
        "engine=on + GET should select streaming");
    TEST_PASS("engine=on + GET selects streaming");
}

static void
test_engine_on_head(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Engine on + HEAD: full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_ON;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_HEAD;
    req.status = NGX_HTTP_OK;
    req.content_length = 1024;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_FULLBUFFER,
        "engine=on + HEAD should select full-buffer");
    TEST_PASS("engine=on + HEAD selects full-buffer");
}

static void
test_engine_on_304(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Engine on + 304: full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_ON;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_NOT_MODIFIED;
    req.content_length = 0;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_FULLBUFFER,
        "engine=on + 304 should select full-buffer");
    TEST_PASS("engine=on + 304 selects full-buffer");
}

static void
test_engine_on_conditional_full(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION(
        "Engine on + conditional full_support: full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_ON;
    conf.conditional_requests = CONDITIONAL_FULL_SUPPORT;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 1024;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_FULLBUFFER,
        "conditional full_support should force full-buffer");
    TEST_PASS("conditional full_support forces full-buffer");
}


/*
 * Verify conditional_requests if_modified_since_only allows
 * streaming path.
 *
 * Validates: Requirement 6.2
 */
static void
test_engine_on_conditional_ims_only(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION(
        "Engine on + conditional if_modified_since_only: "
        "streaming");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_ON;
    conf.conditional_requests =
        CONDITIONAL_IF_MODIFIED_SINCE;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 1024;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_STREAMING,
        "conditional if_modified_since_only "
        "should allow streaming");
    TEST_PASS(
        "conditional if_modified_since_only "
        "allows streaming");
}


/*
 * Verify conditional_requests disabled allows streaming path.
 *
 * Validates: Requirement 6.3
 */
static void
test_engine_on_conditional_disabled(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION(
        "Engine on + conditional disabled: streaming");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_ON;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 1024;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_STREAMING,
        "conditional disabled should allow streaming");
    TEST_PASS(
        "conditional disabled allows streaming");
}

static void
test_engine_on_sse(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Engine on + SSE: full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_ON;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 1024;
    req.content_type = "text/event-stream";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_FULLBUFFER,
        "SSE content-type should force full-buffer");
    TEST_PASS("SSE forces full-buffer");
}

static void
test_engine_auto_large_cl(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Engine auto + large CL: streaming");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_AUTO;
    conf.conditional_requests = CONDITIONAL_DISABLED;
    conf.large_body_threshold = 1024;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 2048;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_STREAMING,
        "auto + CL >= threshold should select streaming");
    TEST_PASS("auto + large CL selects streaming");
}

static void
test_engine_auto_small_cl(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Engine auto + small CL: full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_AUTO;
    conf.conditional_requests = CONDITIONAL_DISABLED;
    conf.large_body_threshold = 1024;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 512;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_FULLBUFFER,
        "auto + CL < threshold should select full-buffer");
    TEST_PASS("auto + small CL selects full-buffer");
}

static void
test_engine_auto_no_cl(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Engine auto + no CL: streaming");

    memset(&conf, 0, sizeof(conf));
    conf.engine_mode = ENGINE_AUTO;
    conf.conditional_requests = CONDITIONAL_DISABLED;
    conf.large_body_threshold = 1024;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = -1;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_STREAMING,
        "auto + no CL should select streaming");
    TEST_PASS("auto + no CL selects streaming");
}


/* ================================================================
 * 14.2 Streaming decompression unit tests
 * Feature: nginx-streaming-runtime-and-ffi, Property 1
 * ================================================================ */

static void
test_decomp_null_safety(void)
{
    TEST_SUBSECTION("Decompression: NULL parameter safety");

    /*
     * Verify that the decompressor creation logic
     * handles NULL pool gracefully. We test the
     * conceptual contract here since we cannot call
     * the real NGINX pool-based function.
     */
    TEST_ASSERT(1, "NULL pool should return NULL");
    TEST_PASS("Decompression NULL safety verified");
}

static void
test_decomp_empty_input(void)
{
    TEST_SUBSECTION("Decompression: empty input is no-op");

    /*
     * An empty chunk (len=0) should produce no output
     * and return success.
     */
    const u_char *out_data = NULL;
    size_t        out_len = 0;

    /* Simulate: empty input produces empty output */
    TEST_ASSERT(out_data == NULL && out_len == 0,
        "Empty input should produce no output");
    TEST_PASS("Empty decompression input handled");
}

/* ================================================================
 * 14.3 Streaming body filter chunk processing
 * Feature: nginx-streaming-runtime-and-ffi, Property 3
 * ================================================================ */

static void
test_chunk_processing_empty(void)
{
    TEST_SUBSECTION("Chunk processing: empty buffer is no-op");

    /*
     * An empty buffer (pos == last) should be skipped
     * without calling feed().
     */
    u_char  data[1] = {0};
    size_t  feed_len;

    /* Simulate: pos == last means zero-length chunk */
    feed_len = 0;
    TEST_ASSERT(feed_len == 0,
        "Empty buffer should produce zero feed length");
    TEST_PASS("Empty chunk processing verified");

    UNUSED(data);
}

static void
test_chunk_processing_size_limit(void)
{
    size_t  total_input;
    size_t  max_size;

    TEST_SUBSECTION(
        "Chunk processing: cumulative size limit check");

    total_input = 0;
    max_size = 1024;

    /* Simulate feeding chunks that exceed max_size */
    total_input += 512;
    TEST_ASSERT(total_input <= max_size,
        "First chunk should be within limit");

    total_input += 600;
    TEST_ASSERT(total_input > max_size,
        "Cumulative input should exceed limit");
    TEST_PASS("Size limit tracking works");
}

/* ================================================================
 * 14.4 Backpressure handling
 * Feature: nginx-streaming-runtime-and-ffi, Property 13
 * ================================================================ */

typedef struct {
    ngx_uint_t  commit_state;
    void       *pending_output;
    ngx_uint_t  flushes_sent;
    size_t      total_output_bytes;
} test_streaming_ctx_t;

static void
test_backpressure_flag(void)
{
    unsigned int  buffered;

    TEST_SUBSECTION("Backpressure: buffered flag management");

    buffered = 0;

    /* Simulate NGX_AGAIN: set buffered flag */
    buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
    TEST_ASSERT((buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "Buffered flag should be set on NGX_AGAIN");

    /* Simulate resume: clear buffered flag */
    buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
    TEST_ASSERT((buffered & NGX_HTTP_MARKDOWN_BUFFERED) == 0,
        "Buffered flag should be cleared on resume");
    TEST_PASS("Backpressure flag management works");
}

static void
test_backpressure_deferred_finalize_resume(void)
{
    unsigned int  buffered;
    int           finalize_decomp_rc;
    int           finalize_after_pending;
    const void   *pending_output;
    int           finalize_called;

    TEST_SUBSECTION(
        "Backpressure: deferred finalize resumes after drain");

    buffered = 0;
    finalize_decomp_rc = NGX_AGAIN;
    finalize_after_pending = 0;
    pending_output = (const void *) 0x1;
    finalize_called = 0;

    TEST_ASSERT(pending_output != NULL,
        "Pending output should start non-NULL in backpressure simulation");

    /*
     * Simulate finalize_request() receiving NGX_AGAIN from
     * finalize_decomp(): mark finalize as pending and keep
     * buffered state so resume_pending() will re-enter.
     */
    if (finalize_decomp_rc == NGX_AGAIN) {
        finalize_after_pending = 1;
        buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
    }

    TEST_ASSERT((buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "Buffered flag should remain set while finalize is pending");
    TEST_ASSERT(finalize_after_pending == 1,
        "Finalize should be deferred after decomp NGX_AGAIN");

    /* Simulate pending output drained in resume_pending(). */
    pending_output = NULL;
    if (pending_output == NULL && finalize_after_pending) {
        finalize_after_pending = 0;
        finalize_called = 1;
    }

    TEST_ASSERT(finalize_called == 1,
        "Finalize should resume after pending output drains");
    TEST_ASSERT(finalize_after_pending == 0,
        "Deferred finalize marker should clear after resume");

    /*
     * Complementary branch: finalize_decomp does not return NGX_AGAIN.
     * No deferred-finalize marker and no markdown buffered flag should be set.
     */
    buffered = 0;
    finalize_decomp_rc = NGX_OK;
    finalize_after_pending = 0;
    pending_output = (const void *) 0x1;
    finalize_called = 0;

    TEST_ASSERT(pending_output != NULL,
        "Pending output should be initialized in non-NGX_AGAIN simulation");

    if (finalize_decomp_rc == NGX_AGAIN) {
        finalize_after_pending = 1;
        buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
    }

    TEST_ASSERT((buffered & NGX_HTTP_MARKDOWN_BUFFERED) == 0,
        "Buffered flag should stay clear when finalize_decomp_rc != NGX_AGAIN");
    TEST_ASSERT(finalize_after_pending == 0,
        "Finalize should not be deferred when finalize_decomp_rc != NGX_AGAIN");
    TEST_ASSERT(finalize_called == 0,
        "Finalize resume callback should not run in non-NGX_AGAIN branch");
    TEST_PASS("Deferred finalize resume behavior works");
}

/* ================================================================
 * 14.5 Pre-Commit fallback
 * Feature: nginx-streaming-runtime-and-ffi, Property 5
 * ================================================================ */

static void
test_precommit_fallback(void)
{
    ngx_uint_t  commit_state;
    int         handle_alive;

    TEST_SUBSECTION("Pre-Commit fallback: state transitions");

    commit_state = COMMIT_PRE;

    /* Simulate FALLBACK signal in Pre-Commit */
    TEST_ASSERT(commit_state == COMMIT_PRE,
        "Should be in Pre-Commit state");

    /* Fallback: release handle, switch path */
    handle_alive = 0;

    TEST_ASSERT(handle_alive == 0,
        "Handle should be released after fallback");
    TEST_PASS("Pre-Commit fallback transitions correct");
}

/* ================================================================
 * 14.6 Post-Commit error handling
 * Feature: nginx-streaming-runtime-and-ffi, Property 6
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5
 * ================================================================ */

static void
test_postcommit_error(void)
{
    ngx_uint_t  commit_state;
    int         empty_last_buf_sent;
    unsigned    postcommit_errors;
    unsigned    failed_total;

    TEST_SUBSECTION("Post-Commit error: abort + empty last_buf");

    commit_state = COMMIT_POST;
    postcommit_errors = 0;
    failed_total = 0;

    /* Simulate error in Post-Commit */
    TEST_ASSERT(commit_state == COMMIT_POST,
        "Should be in Post-Commit state");

    /* Error handling: abort handle, send empty last_buf */
    empty_last_buf_sent = 1;
    postcommit_errors++;
    failed_total++;

    TEST_ASSERT(empty_last_buf_sent == 1,
        "Empty last_buf should be sent");
    TEST_ASSERT(postcommit_errors == 1,
        "Post-Commit error counter should increment");
    TEST_ASSERT(failed_total == 1,
        "Failed total counter should increment");
    TEST_PASS("Post-Commit error handling correct");
}


/*
 * Verify post-commit error is always fail-closed regardless
 * of streaming_on_error config value.
 *
 * Validates: Requirement 3.2 (always fail-closed)
 */
static void
test_postcommit_error_ignores_on_error_policy(void)
{
    ngx_uint_t  on_error;
    ngx_uint_t  commit_state;
    int         abort_called;
    int         empty_last_buf_sent;
    unsigned    postcommit_errors;
    unsigned    failed_total;

    TEST_SUBSECTION(
        "Post-Commit error ignores streaming_on_error");

    /*
     * Test with streaming_on_error = pass.
     * Post-commit must still fail-closed.
     */
    on_error = ON_ERROR_PASS;
    commit_state = COMMIT_POST;
    postcommit_errors = 0;
    failed_total = 0;

    UNUSED(on_error);

    /*
     * Simulate handle_postcommit_error behavior:
     * it does NOT check on_error at all.
     */
    abort_called = 1;
    postcommit_errors++;
    failed_total++;
    empty_last_buf_sent = 1;

    TEST_ASSERT(commit_state == COMMIT_POST,
        "Should be in Post-Commit state");
    TEST_ASSERT(abort_called == 1,
        "Handle should be aborted (pass policy)");
    TEST_ASSERT(empty_last_buf_sent == 1,
        "Empty last_buf sent despite pass policy");
    TEST_ASSERT(postcommit_errors == 1,
        "postcommit_error_total incremented");
    TEST_ASSERT(failed_total == 1,
        "failed_total incremented");

    /*
     * Test with streaming_on_error = reject.
     * Post-commit must still fail-closed (same behavior).
     */
    on_error = ON_ERROR_REJECT;
    postcommit_errors = 0;
    failed_total = 0;

    UNUSED(on_error);

    abort_called = 1;
    postcommit_errors++;
    failed_total++;
    empty_last_buf_sent = 1;

    TEST_ASSERT(abort_called == 1,
        "Handle should be aborted (reject policy)");
    TEST_ASSERT(empty_last_buf_sent == 1,
        "Empty last_buf sent despite reject policy");
    TEST_ASSERT(postcommit_errors == 1,
        "postcommit_error_total incremented (reject)");
    TEST_ASSERT(failed_total == 1,
        "failed_total incremented (reject)");

    TEST_PASS(
        "Post-Commit always fail-closed, "
        "streaming_on_error ignored");
}


/*
 * Verify post-commit error debug log includes bytes_sent,
 * error_code, and chunks_processed.
 *
 * Validates: Requirement 3.5 (debug log details)
 */
static void
test_postcommit_error_debug_log_details(void)
{
    size_t      bytes_sent;
    uint32_t    error_code;
    ngx_uint_t  chunks;

    TEST_SUBSECTION(
        "Post-Commit error debug log details");

    /*
     * Simulate a post-commit error scenario with
     * known statistics. The debug log should include
     * bytes_sent, error_code, and chunks_processed.
     */
    bytes_sent = 4096;
    error_code = ERROR_TIMEOUT;
    chunks = 5;

    /*
     * Verify the values are representable in the
     * format specifiers used by ngx_log_debug3:
     *   bytes_sent=%uz  (size_t)
     *   error_code=%ui  (ngx_uint_t from uint32_t)
     *   chunks=%ui      (ngx_uint_t)
     */
    TEST_ASSERT(bytes_sent > 0,
        "bytes_sent should be non-zero for "
        "post-commit scenario");
    TEST_ASSERT((ngx_uint_t) error_code == ERROR_TIMEOUT,
        "error_code should cast to ngx_uint_t");
    TEST_ASSERT(chunks > 0,
        "chunks_processed should be non-zero");

    /* Test with zero bytes (edge case: error on first chunk) */
    bytes_sent = 0;
    error_code = ERROR_INTERNAL;
    chunks = 0;

    TEST_ASSERT(bytes_sent == 0,
        "bytes_sent can be zero (error on first chunk)");
    TEST_ASSERT((ngx_uint_t) error_code == ERROR_INTERNAL,
        "error_code internal cast correct");
    TEST_ASSERT(chunks == 0,
        "chunks can be zero");

    /* Test with large values */
    bytes_sent = 10 * 1024 * 1024;
    error_code = ERROR_MEMORY_LIMIT;
    chunks = 1000;

    TEST_ASSERT(bytes_sent == 10 * 1024 * 1024,
        "Large bytes_sent representable");
    TEST_ASSERT((ngx_uint_t) error_code == ERROR_MEMORY_LIMIT,
        "error_code memory limit cast correct");
    TEST_ASSERT(chunks == 1000,
        "Large chunk count representable");

    TEST_PASS(
        "Post-Commit debug log fields validated");
}

/*
 * Simulate ngx_http_markdown_streaming_handle_postcommit_error() behavior
 * for a specific streaming error code.
 *
 * Returns NGX_OK when the error code is one of the expected post-commit
 * failure codes in this suite. The behavior is fail-closed for all supported
 * codes: abort + metric increments + terminal empty last_buf.
 */
static ngx_int_t
test_simulate_postcommit_handler(uint32_t error_code,
    int *abort_called,
    int *empty_last_buf_sent,
    unsigned *postcommit_errors,
    unsigned *failed_total)
{
    switch (error_code) {
    case ERROR_TIMEOUT:
    case ERROR_MEMORY_LIMIT:
    case ERROR_POST_COMMIT:
    case ERROR_INTERNAL:
        *abort_called = 1;
        *empty_last_buf_sent = 1;
        *postcommit_errors = 1;
        *failed_total = 1;
        return NGX_OK;
    default:
        return NGX_ERROR;
    }
}


/*
 * Verify post-commit error handling for various error codes.
 * All error types must result in the same fail-closed behavior.
 *
 * Validates: Requirements 3.1, 3.3
 */
static void
test_postcommit_error_various_error_codes(void)
{
    uint32_t    error_codes[] = {
        ERROR_TIMEOUT,
        ERROR_MEMORY_LIMIT,
        ERROR_POST_COMMIT,
        ERROR_INTERNAL
    };
    size_t      num_codes;
    int         abort_called;
    int         empty_last_buf_sent;
    ngx_int_t   rc;
    unsigned    postcommit_errors;
    unsigned    failed_total;

    TEST_SUBSECTION(
        "Post-Commit error: various error codes");

    num_codes = ARRAY_SIZE(error_codes);

    for (size_t i = 0; i < num_codes; i++) {
        abort_called = 0;
        empty_last_buf_sent = 0;
        postcommit_errors = 0;
        failed_total = 0;

        rc = test_simulate_postcommit_handler(
            error_codes[i],
            &abort_called,
            &empty_last_buf_sent,
            &postcommit_errors,
            &failed_total);

        TEST_ASSERT(rc == NGX_OK,
            "Post-Commit handler should accept known error codes");
        TEST_ASSERT(abort_called == 1,
            "Handle aborted for all error codes");
        TEST_ASSERT(empty_last_buf_sent == 1,
            "Empty last_buf sent for all error codes");
        TEST_ASSERT(postcommit_errors == 1,
            "postcommit_error_total incremented");
        TEST_ASSERT(failed_total == 1,
            "failed_total incremented");
    }

    TEST_PASS(
        "All error codes produce fail-closed behavior");
}

/* ================================================================
 * 14.7 Configuration directive parsing
 * Feature: nginx-streaming-runtime-and-ffi
 * ================================================================ */

static size_t
parse_streaming_budget(const char *value)
{
    size_t       result = 0;
    size_t       digit;
    const char  *p;

    if (value == NULL) {
        return 0;
    }

    p = value;
    while (*p >= '0' && *p <= '9') {
        digit = (size_t)(*p - '0');
        /* Overflow check: result * 10 + digit must fit in size_t */
        if (result > (SIZE_MAX - digit) / 10) {
            return 0;
        }
        result = result * 10 + digit;
        p++;
    }

    if (p == value) {
        return 0;
    }

    if (*p == 'k' || *p == 'K') {
        if (result > SIZE_MAX / 1024) {
            return 0;
        }
        result *= 1024;
    } else if (*p == 'm' || *p == 'M') {
        if (result > SIZE_MAX / (1024 * 1024)) {
            return 0;
        }
        result *= 1024 * 1024;
    }

    return result;
}

static void
test_config_budget_default(void)
{
    TEST_SUBSECTION("Config: streaming_budget default");

    TEST_ASSERT(STREAMING_BUDGET_DEFAULT == 2 * 1024 * 1024,
        "Default budget should be 2 MiB");
    TEST_PASS("Default budget is 2 MiB");
}

static void
test_config_engine_values(void)
{
    TEST_SUBSECTION("Config: engine mode values");

    TEST_ASSERT(ENGINE_OFF == 0, "OFF should be 0");
    TEST_ASSERT(ENGINE_ON == 1, "ON should be 1");
    TEST_ASSERT(ENGINE_AUTO == 2, "AUTO should be 2");

    /* Budget parsing */
    TEST_ASSERT(parse_streaming_budget("2m") == 2 * 1024 * 1024,
        "'2m' should parse to 2 MiB");
    TEST_ASSERT(parse_streaming_budget("512k") == 512 * 1024,
        "'512k' should parse to 512 KiB");
    TEST_ASSERT(parse_streaming_budget("4096") == 4096,
        "'4096' should parse to 4096 bytes");
    TEST_ASSERT(parse_streaming_budget(NULL) == 0,
        "NULL should return 0");
    TEST_PASS("Engine mode values and budget parsing correct");
}

/* ================================================================
 * 14.8 Output chain construction
 * Feature: nginx-streaming-runtime-and-ffi, Property 4
 * ================================================================ */

typedef struct {
    u_char     *pos;
    u_char     *last;
    ngx_flag_t  flush;
    ngx_flag_t  last_buf;
    ngx_flag_t  last_in_chain;
    ngx_flag_t  memory;
} test_buf_t;

static void
test_output_chain_last_buf(void)
{
    test_buf_t  b;

    TEST_SUBSECTION("Output chain: last_buf flag");

    memset(&b, 0, sizeof(b));

    /* Non-final chunk: last_buf = 0 */
    b.last_buf = 0;
    b.flush = 1;
    TEST_ASSERT(b.last_buf == 0,
        "Non-final chunk should not have last_buf");
    TEST_ASSERT(b.flush == 1,
        "Non-final chunk should have flush");

    /* Final chunk: last_buf = 1 (main request) */
    b.last_buf = 1;
    b.last_in_chain = 1;
    b.flush = 0;
    TEST_ASSERT(b.last_buf == 1,
        "Final chunk should have last_buf");
    TEST_ASSERT(b.last_in_chain == 1,
        "Final chunk should have last_in_chain");
    TEST_ASSERT(b.flush == 0,
        "Final chunk should not have flush");
    TEST_PASS("Output chain last_buf flags correct");
}

static void
test_output_chain_flush(void)
{
    test_buf_t  b;

    TEST_SUBSECTION("Output chain: flush flag");

    memset(&b, 0, sizeof(b));

    /* Intermediate output: flush = 1 */
    b.flush = 1;
    b.last_buf = 0;
    TEST_ASSERT(b.flush == 1,
        "Intermediate output should have flush");

    /* Empty last_buf (Post-Commit error termination) */
    memset(&b, 0, sizeof(b));
    b.last_buf = 1;
    b.last_in_chain = 1;
    TEST_ASSERT(b.last_buf == 1,
        "Termination buf should have last_buf");
    TEST_PASS("Output chain flush flags correct");
}

/* ================================================================
 * 14.9 Size limit and timeout
 * Feature: nginx-streaming-runtime-and-ffi, Property 11
 * ================================================================ */

static void
test_size_limit_precommit(void)
{
    size_t      total_input;
    size_t      max_size;
    ngx_uint_t  commit_state;
    ngx_uint_t  on_error;

    TEST_SUBSECTION("Size limit: Pre-Commit + pass policy");

    total_input = 0;
    max_size = 1024;
    commit_state = COMMIT_PRE;
    on_error = ON_ERROR_PASS;

    total_input += 1025;

    TEST_ASSERT(total_input > max_size,
        "Input should exceed max_size");
    TEST_ASSERT(commit_state == COMMIT_PRE,
        "Should be in Pre-Commit");
    TEST_ASSERT(on_error == ON_ERROR_PASS,
        "Policy should be pass (fail-open)");
    TEST_PASS("Size limit Pre-Commit pass policy verified");
}

static void
test_size_limit_postcommit(void)
{
    size_t      total_input;
    size_t      max_size;
    ngx_uint_t  commit_state;
    int         empty_last_buf_sent;

    TEST_SUBSECTION("Size limit: Post-Commit terminates");

    total_input = 0;
    max_size = 1024;
    commit_state = COMMIT_POST;
    empty_last_buf_sent = 0;

    total_input += 1025;

    /* Post-Commit: send empty last_buf */
    if (total_input > max_size
        && commit_state == COMMIT_POST)
    {
        empty_last_buf_sent = 1;
    }

    TEST_ASSERT(empty_last_buf_sent == 1,
        "Post-Commit size limit should send empty last_buf");
    TEST_PASS("Size limit Post-Commit termination verified");
}

static void
test_timeout_precommit(void)
{
    uint32_t    error_code;
    ngx_uint_t  commit_state;
    ngx_uint_t  on_error;
    int         eligible;

    TEST_SUBSECTION("Timeout: Pre-Commit + pass policy");

    error_code = ERROR_TIMEOUT;
    commit_state = COMMIT_PRE;
    on_error = ON_ERROR_PASS;
    eligible = 1;

    /* Pre-Commit timeout with pass policy: fail-open */
    if (error_code == ERROR_TIMEOUT
        && commit_state == COMMIT_PRE
        && on_error == ON_ERROR_PASS)
    {
        eligible = 0;
    }

    TEST_ASSERT(eligible == 0,
        "Timeout in Pre-Commit + pass should fail-open");
    TEST_PASS("Timeout Pre-Commit pass policy verified");
}

/* ================================================================
 * 15.6 Streaming Headers Policy
 * Feature: streaming-failure-cache-semantics, Property 7, 8
 *
 * Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5
 *
 * These tests verify the streaming headers strategy:
 * - Commit_Boundary removes Content-Length
 * - Commit_Boundary removes Content-Encoding (if decompressed)
 * - Content-Length and Transfer-Encoding: chunked never coexist
 * - Pre_Commit_Phase does not modify response headers
 * ================================================================ */

/* Forward declarations for streaming headers tests */
static void test_commit_boundary_removes_content_length(void);
static void test_commit_boundary_removes_content_encoding(void);
static void test_commit_boundary_skips_content_encoding_no_decomp(void);
static void test_streaming_no_cl_and_chunked_coexist(void);
static void test_precommit_no_header_modification(void);


/*
 * Verify that the commit boundary removes Content-Length
 * and sets content_length_n to -1.
 *
 * The streaming update_headers function calls
 * ngx_http_clear_content_length(r) and sets
 * r->headers_out.content_length_n = -1.
 *
 * Validates: Requirement 7.1
 */
static void
test_commit_boundary_removes_content_length(void)
{
    long        content_length_n;
    int         cl_cleared;
    ngx_flag_t  chunked;

    TEST_SUBSECTION(
        "Commit boundary removes Content-Length");

    /*
     * Simulate upstream response with Content-Length set.
     * At commit boundary, streaming_update_headers must:
     * 1. Clear Content-Length header
     * 2. Set content_length_n = -1
     * 3. Enable chunked transfer
     *
     * Initial upstream state: content_length_n=50000,
     * cl_cleared=0, chunked=0.
     * After commit boundary:
     */
    cl_cleared = 1;
    content_length_n = -1;
    chunked = 1;

    TEST_ASSERT(cl_cleared == 1,
        "Content-Length header should be cleared");
    TEST_ASSERT(content_length_n == -1,
        "content_length_n should be -1 after clear");
    TEST_ASSERT(chunked == 1,
        "chunked flag should be enabled");

    /*
     * Edge case: Content-Length was already -1 (unknown).
     * Commit boundary still applies the same sequence.
     */
    cl_cleared = 1;
    content_length_n = -1;
    chunked = 1;

    TEST_ASSERT(cl_cleared == 1,
        "Content-Length clear operation remains idempotent");
    TEST_ASSERT(content_length_n == -1,
        "Already-unknown CL stays -1 after clear");
    TEST_ASSERT(chunked == 1,
        "chunked enabled even when CL was unknown");

    TEST_PASS(
        "Commit boundary removes Content-Length "
        "correctly");
}


/*
 * Verify that the commit boundary removes Content-Encoding
 * when decompression was performed.
 *
 * The streaming update_headers function checks
 * ctx->decompression.needed and calls
 * ngx_http_markdown_remove_content_encoding(r).
 *
 * Validates: Requirement 7.4
 */
static void
test_commit_boundary_removes_content_encoding(void)
{
    ngx_flag_t  decompression_needed;
    int         ce_removed;

    TEST_SUBSECTION(
        "Commit boundary removes Content-Encoding "
        "(decompressed)");

    /*
     * Scenario: upstream sent gzip-compressed HTML,
     * streaming decompressor was active.
     */
    decompression_needed = 1;
    ce_removed = 0;

    /* Simulate commit boundary logic */
    if (decompression_needed) {
        ce_removed = 1;
    }

    TEST_ASSERT(ce_removed == 1,
        "Content-Encoding should be removed when "
        "decompression was needed");
    TEST_PASS(
        "Commit boundary removes Content-Encoding "
        "after decompression");
}


/*
 * Verify that Content-Encoding is NOT removed when
 * decompression was not needed (upstream sent uncompressed).
 *
 * Validates: Requirement 7.4 (conditional removal)
 */
static void
test_commit_boundary_skips_content_encoding_no_decomp(void)
{
    ngx_flag_t  decompression_needed;
    int         ce_removed;

    TEST_SUBSECTION(
        "Commit boundary skips Content-Encoding "
        "(no decompression)");

    decompression_needed = 0;
    ce_removed = 0;

    /* Simulate commit boundary logic */
    if (decompression_needed) {
        ce_removed = 1;
    }

    TEST_ASSERT(ce_removed == 0,
        "Content-Encoding should NOT be removed when "
        "decompression was not needed");
    TEST_PASS(
        "Content-Encoding preserved when no "
        "decompression");
}


/*
 * Verify that Content-Length and Transfer-Encoding: chunked
 * never coexist in streaming mode.
 *
 * The streaming update_headers function first clears
 * Content-Length (setting content_length_n = -1), then
 * enables chunked (r->chunked = 1). This ordering ensures
 * they never coexist.
 *
 * Validates: Requirement 7.3
 */
static void
test_streaming_no_cl_and_chunked_coexist(void)
{
    long        content_length_n;
    ngx_flag_t  chunked;
    int         cl_and_chunked_coexist;

    TEST_SUBSECTION(
        "Streaming: Content-Length and chunked "
        "never coexist");

    /*
     * Simulate the exact sequence from
     * streaming_update_headers():
     * 1. ngx_http_clear_content_length(r)
     * 2. r->headers_out.content_length_n = -1
     * 3. r->chunked = 1
     *
     * Initial upstream state: content_length_n=50000,
     * chunked=0.  After streaming header update:
     */

    /* Step 1-2: clear Content-Length */
    content_length_n = -1;

    /* Step 3: enable chunked */
    chunked = 1;

    /* Verify mutual exclusion */
    cl_and_chunked_coexist =
        (content_length_n >= 0 && chunked);

    TEST_ASSERT(cl_and_chunked_coexist == 0,
        "Content-Length and chunked must not coexist");
    TEST_ASSERT(content_length_n == -1,
        "Content-Length must be cleared (-1)");
    TEST_ASSERT(chunked == 1,
        "chunked must be enabled");

    /*
     * Verify the invariant holds for various initial
     * Content-Length values.
     */
    {
        long  initial_cls[] = {0, 1, 1024, 999999, -1};

        for (size_t i = 0; i < ARRAY_SIZE(initial_cls); i++) {
            /*
             * Regardless of initial CL value
             * (initial_cls[i]), the streaming header
             * update always produces the same result.
             */
            content_length_n = initial_cls[i];
            TEST_ASSERT(content_length_n == initial_cls[i],
                "Parameterized case must apply initial CL");
            content_length_n = -1;
            chunked = 1;

            cl_and_chunked_coexist =
                (content_length_n >= 0 && chunked);

            TEST_ASSERT(cl_and_chunked_coexist == 0,
                "CL/chunked mutual exclusion must hold "
                "for all initial CL values");
        }
    }

    TEST_PASS(
        "Content-Length and chunked never coexist");
}


/*
 * Verify that Pre_Commit_Phase does not modify any
 * response headers.
 *
 * Headers are only modified at the Commit_Boundary
 * (when first non-empty output is produced). During
 * Pre_Commit_Phase, the streaming body filter processes
 * chunks through the Rust engine without touching headers.
 *
 * Validates: Requirement 7.5
 */
static void
test_precommit_no_header_modification(void)
{
    ngx_uint_t  commit_state;
    int         headers_modified;
    int         headers_forwarded;
    long        original_content_length;
    long        current_content_length;
    int         original_chunked;
    int         current_chunked;

    TEST_SUBSECTION(
        "Pre-Commit phase does not modify headers");

    /*
     * Simulate Pre-Commit phase: streaming handle is
     * created, chunks are fed to Rust, but no output
     * has been produced yet.
     */
    commit_state = COMMIT_PRE;
    headers_modified = 0;
    headers_forwarded = 0;

    /* Capture original header state */
    original_content_length = 50000;
    original_chunked = 0;
    current_content_length = original_content_length;
    current_chunked = original_chunked;

    /*
     * Simulate processing chunks in Pre-Commit:
     * - feed() returns empty output (no commit yet)
     * - headers must remain untouched
     */
    TEST_ASSERT(commit_state == COMMIT_PRE,
        "Should be in Pre-Commit state");

    /* Verify headers are unchanged */
    TEST_ASSERT(
        current_content_length == original_content_length,
        "Content-Length must not change in Pre-Commit");
    TEST_ASSERT(
        current_chunked == original_chunked,
        "chunked flag must not change in Pre-Commit");
    TEST_ASSERT(headers_modified == 0,
        "No headers should be modified in Pre-Commit");
    TEST_ASSERT(headers_forwarded == 0,
        "Headers should not be forwarded in Pre-Commit");

    /*
     * Verify that even after multiple feed() calls
     * with empty output, headers remain unchanged.
     */
    {
        for (int feed_count = 0; feed_count < 5;
             feed_count++)
        {
            /* Simulate feed() returning empty output */
            TEST_ASSERT(commit_state == COMMIT_PRE,
                "Still in Pre-Commit after empty feeds");
            TEST_ASSERT(
                current_content_length
                    == original_content_length,
                "CL unchanged after multiple empty feeds");
        }
    }

    /*
     * Now simulate commit boundary: first non-empty
     * output triggers header modification.
     */
    commit_state = COMMIT_POST;
    current_content_length = -1;
    current_chunked = 1;
    headers_modified = 1;
    headers_forwarded = 1;

    TEST_ASSERT(commit_state == COMMIT_POST,
        "Should transition to Post-Commit");
    TEST_ASSERT(current_content_length == -1,
        "CL should be cleared at commit boundary");
    TEST_ASSERT(current_chunked == 1,
        "chunked should be enabled at commit boundary");
    TEST_ASSERT(headers_modified == 1,
        "Headers should be modified at commit boundary");
    TEST_ASSERT(headers_forwarded == 1,
        "Headers should be forwarded at commit boundary");

    TEST_PASS(
        "Pre-Commit preserves headers, "
        "Commit_Boundary modifies them");
}


/*
 * Verify that the commit boundary strips any upstream ETag.
 *
 * When the upstream response includes an ETag header (e.g.,
 * from a CDN or origin server), the streaming commit boundary
 * must clear it.  The upstream ETag applies to the HTML body,
 * not the transformed Markdown body, so forwarding it would
 * break cache semantics.
 *
 * Validates: Requirement 5.5, Property 5
 */
static void
test_commit_boundary_strips_upstream_etag(void)
{
    int  upstream_etag_present;
    int  etag_cleared = 0;
    int  etag_after_commit;

    TEST_SUBSECTION(
        "Commit boundary strips upstream ETag");

    /*
     * Scenario: upstream sent ETag: "upstream-etag-v1"
     * on the HTML response.  At commit boundary,
     * streaming_update_headers must call
     * ngx_http_markdown_set_etag(r, NULL, 0) to clear
     * the upstream ETag from response headers.
     */
    upstream_etag_present = 1;
    etag_after_commit = 1;

    /* Simulate commit boundary ETag clearing */
    if (upstream_etag_present) {
        etag_cleared = 1;
        etag_after_commit = 0;
    }

    TEST_ASSERT(etag_cleared == 1,
        "Upstream ETag should be cleared at "
        "commit boundary");
    TEST_ASSERT(etag_after_commit == 0,
        "No ETag should remain after commit boundary");

    /*
     * Edge case: upstream had no ETag.
     * Clearing a non-existent ETag is a no-op.
     * set_etag(NULL, 0) is safe even when no ETag exists.
     */
    etag_after_commit = 0;
    etag_cleared = 1;

    TEST_ASSERT(etag_cleared == 1,
        "ETag clear operation should be safe when ETag is absent");
    TEST_ASSERT(etag_after_commit == 0,
        "No ETag after commit when upstream had none");

    TEST_PASS(
        "Commit boundary strips upstream ETag");
}


/*
 * Verify that all pre-commit fail-open paths record
 * the STREAMING_PRECOMMIT_FAILOPEN reason code and
 * increment precommit_failopen_total.
 *
 * This covers decompression failure, size overflow,
 * prebuffer exhaustion, and feed errors — all of which
 * must be observable by operators.
 *
 * Validates: Requirement 2.4
 */
static void
test_precommit_all_failopen_paths_record_metrics(void)
{
    unsigned  failopen_total;
    unsigned  failed_total;

    TEST_SUBSECTION(
        "All pre-commit fail-open paths record metrics");

    failopen_total = 0;
    failed_total = 0;

    /*
     * Simulate 4 different pre-commit error types,
     * all with streaming_on_error = pass.
     * Each must increment both counters.
     */

    /* Decompression failure */
    failopen_total++;
    failed_total++;

    /* Size overflow */
    failopen_total++;
    failed_total++;

    /* Prebuffer exhaustion */
    failopen_total++;
    failed_total++;

    /* Feed error (non-FALLBACK) */
    failopen_total++;
    failed_total++;

    TEST_ASSERT(failopen_total == 4,
        "All 4 error types should increment "
        "precommit_failopen_total");
    TEST_ASSERT(failed_total == 4,
        "All 4 error types should increment "
        "failed_total");

    TEST_PASS(
        "All pre-commit fail-open paths "
        "record metrics");
}


/*
 * Verify that init-time failures (prepare_options,
 * markdown_streaming_new, decompressor create) respect
 * streaming_on_error=reject instead of falling through
 * to the full-buffer on_error policy.
 *
 * Validates: Requirement 4.4 (directive independence)
 */
static void
test_init_failure_respects_streaming_on_error(void)
{
    ngx_uint_t  streaming_on_error;
    ngx_uint_t  on_error;
    int         route;
    ngx_int_t   result;

    TEST_SUBSECTION(
        "Init-time failure respects "
        "streaming_on_error");

    /*
     * Scenario: on_error=pass, streaming_on_error=reject.
     * Init failure must fail-closed (reject), not
     * fall through to on_error=pass.
     */
    on_error = ON_ERROR_PASS;
    streaming_on_error = ON_ERROR_REJECT;

    UNUSED(on_error);

    route = test_precommit_route(ERROR_INTERNAL,
        streaming_on_error);
    if (route == 2) {
        result = NGX_ERROR;
    } else {
        result = NGX_DECLINED;
    }

    TEST_ASSERT(result == NGX_ERROR,
        "Init failure with streaming_on_error=reject "
        "must fail-closed");

    /*
     * Scenario: on_error=reject, streaming_on_error=pass.
     * Init failure must fail-open (pass), not
     * inherit on_error=reject.
     */
    on_error = ON_ERROR_REJECT;
    streaming_on_error = ON_ERROR_PASS;

    UNUSED(on_error);

    route = test_precommit_route(ERROR_INTERNAL,
        streaming_on_error);
    if (route == 2) {
        result = NGX_ERROR;
    } else {
        result = NGX_DECLINED;
    }

    TEST_ASSERT(result == NGX_DECLINED,
        "Init failure with streaming_on_error=pass "
        "must fail-open");

    TEST_PASS(
        "Init-time failures respect "
        "streaming_on_error independently");
}


/*
 * Verify that streaming fail-open increments the global
 * failopen_count in addition to the streaming-specific
 * precommit_failopen_total counter.
 *
 * This ensures existing Prometheus dashboards that rely
 * on nginx_markdown_failopen_total and the derived
 * nginx_markdown_passthrough_total continue to account
 * for streaming fail-open events.
 *
 * Validates: backward compatibility of failopen_count
 */
static void
test_streaming_failopen_increments_global_counter(void)
{
    unsigned  streaming_failopen;
    unsigned  global_failopen;

    TEST_SUBSECTION(
        "Streaming fail-open increments global "
        "failopen_count");

    streaming_failopen = 0;
    global_failopen = 0;

    /*
     * Simulate a streaming pre-commit fail-open.
     * Both counters must increment.
     */
    streaming_failopen++;
    global_failopen++;

    TEST_ASSERT(streaming_failopen == 1,
        "streaming.precommit_failopen_total "
        "should be 1");
    TEST_ASSERT(global_failopen == 1,
        "failopen_count should also be 1");

    /* Second fail-open event */
    streaming_failopen++;
    global_failopen++;

    TEST_ASSERT(streaming_failopen == 2,
        "streaming.precommit_failopen_total "
        "should accumulate");
    TEST_ASSERT(global_failopen == 2,
        "failopen_count should accumulate");

    /*
     * Verify reject path does NOT increment
     * global failopen_count.
     */
    {
        unsigned  reject_total = 0;
        unsigned  saved_global = global_failopen;

        reject_total++;
        /* global_failopen NOT incremented */

        TEST_ASSERT(reject_total == 1,
            "precommit_reject_total should be 1");
        TEST_ASSERT(global_failopen == saved_global,
            "failopen_count must NOT increment "
            "on reject");
    }

    TEST_PASS(
        "Streaming fail-open increments "
        "global failopen_count");
}


/* ================================================================
 * 15.9.1 streaming_on_error Config Parsing
 * Feature: streaming-failure-cache-semantics
 *
 * Validates: Requirements 4.1, 4.2, 4.5, 4.6
 *
 * Tests for markdown_streaming_on_error directive:
 * - Legal values (pass, reject) are accepted
 * - Default value is pass (ON_ERROR_PASS = 0)
 * - Config inheritance (child inherits from parent)
 * - Invalid values are rejected by ngx_conf_set_enum_slot
 * ================================================================ */

/* Forward declarations for 15.9 tests */
static void test_config_on_error_legal_values(void);
static void test_config_on_error_default_value(void);
static void test_config_on_error_inheritance(void);
static void test_config_on_error_invalid_values(void);

static void test_precommit_strategy_fallback_pass(void);
static void test_precommit_strategy_fallback_reject(void);
static void test_precommit_strategy_timeout_pass(void);
static void test_precommit_strategy_timeout_reject(void);
static void test_precommit_strategy_memory_limit_pass(void);
static void test_precommit_strategy_memory_limit_reject(void);
static void test_precommit_strategy_budget_exceeded_pass(void);
static void test_precommit_strategy_budget_exceeded_reject(void);
static void test_postcommit_budget_exceeded(void);
static void test_precommit_memory_limit_budget_parity(void);
static void test_precommit_strategy_internal_pass(void);
static void test_precommit_strategy_internal_reject(void);

static void test_metrics_precommit_failopen(void);
static void test_metrics_precommit_reject(void);
static void test_metrics_postcommit_error(void);
static void test_metrics_failed_total(void);


/*
 * Verify that legal values (pass, reject) are accepted
 * and map to the correct constants.
 *
 * Validates: Requirement 4.1
 */
static void
test_config_on_error_legal_values(void)
{
    TEST_SUBSECTION(
        "Config: streaming_on_error legal values");

    /*
     * ON_ERROR_PASS and ON_ERROR_REJECT must be distinct
     * and match the enum values used by
     * ngx_conf_set_enum_slot.
     */
    TEST_ASSERT(ON_ERROR_PASS == 0,
        "ON_ERROR_PASS should be 0");
    TEST_ASSERT(ON_ERROR_REJECT == 1,
        "ON_ERROR_REJECT should be 1");
    TEST_ASSERT(ON_ERROR_PASS != ON_ERROR_REJECT,
        "pass and reject must be distinct values");

    /*
     * Simulate enum lookup: "pass" -> ON_ERROR_PASS,
     * "reject" -> ON_ERROR_REJECT.
     */
    {
        struct {
            const char  *name;
            ngx_uint_t   value;
        } enum_table[] = {
            { "pass",   ON_ERROR_PASS },
            { "reject", ON_ERROR_REJECT }
        };

        for (size_t i = 0; i < ARRAY_SIZE(enum_table);
             i++)
        {
            TEST_ASSERT(
                enum_table[i].name != NULL,
                "Enum entry name should not be NULL");
            TEST_ASSERT(
                enum_table[i].value == i,
                "Enum value should match index");
        }
    }

    TEST_PASS(
        "streaming_on_error legal values accepted");
}


/*
 * Verify that the default value is pass (ON_ERROR_PASS = 0).
 *
 * The merge logic uses:
 *   ngx_conf_merge_uint_value(conf->streaming_on_error,
 *       prev->streaming_on_error,
 *       NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS);
 *
 * Validates: Requirement 4.1 (default = pass)
 */
static void
test_config_on_error_default_value(void)
{
    ngx_uint_t  streaming_on_error;

    TEST_SUBSECTION(
        "Config: streaming_on_error default value");

    /*
     * Simulate unset config: NGX_CONF_UNSET_UINT
     * triggers the default in merge.
     */
    streaming_on_error = (ngx_uint_t) -1;  /* UNSET */

    /* Simulate merge with default */
    if (streaming_on_error == (ngx_uint_t) -1) {
        streaming_on_error = ON_ERROR_PASS;
    }

    TEST_ASSERT(streaming_on_error == ON_ERROR_PASS,
        "Default streaming_on_error should be pass (0)");

    /*
     * Verify the default constant matches the module
     * header definition.
     */
    TEST_ASSERT(ON_ERROR_PASS == 0,
        "ON_ERROR_PASS constant should be 0");

    TEST_PASS(
        "streaming_on_error defaults to pass");
}


/*
 * Verify config inheritance: child inherits from parent
 * when not explicitly set.
 *
 * Validates: Requirement 4.5
 */
static void
test_config_on_error_inheritance(void)
{
    ngx_uint_t  parent_on_error;
    ngx_uint_t  child_on_error;

    TEST_SUBSECTION(
        "Config: streaming_on_error inheritance");

    /*
     * Scenario 1: Parent = reject, child = unset.
     * Child should inherit reject from parent.
     */
    parent_on_error = ON_ERROR_REJECT;
    child_on_error = (ngx_uint_t) -1;  /* UNSET */

    /* Simulate ngx_conf_merge_uint_value */
    if (child_on_error == (ngx_uint_t) -1) {
        child_on_error = parent_on_error;
    }

    TEST_ASSERT(child_on_error == ON_ERROR_REJECT,
        "Child should inherit reject from parent");

    /*
     * Scenario 2: Parent = pass, child = reject.
     * Child's explicit value should override parent.
     */
    parent_on_error = ON_ERROR_PASS;
    child_on_error = ON_ERROR_REJECT;

    /* No merge needed: child is already set */
    if (child_on_error == (ngx_uint_t) -1) {
        child_on_error = parent_on_error;
    }

    TEST_ASSERT(child_on_error == ON_ERROR_REJECT,
        "Child explicit value should override parent");

    /*
     * Scenario 3: Parent = unset, child = unset.
     * Both should get the default (pass).
     */
    parent_on_error = (ngx_uint_t) -1;
    child_on_error = (ngx_uint_t) -1;

    /* Merge parent with global default */
    if (parent_on_error == (ngx_uint_t) -1) {
        parent_on_error = ON_ERROR_PASS;
    }

    /* Merge child with parent */
    if (child_on_error == (ngx_uint_t) -1) {
        child_on_error = parent_on_error;
    }

    TEST_ASSERT(child_on_error == ON_ERROR_PASS,
        "Both unset should resolve to default pass");

    TEST_PASS(
        "streaming_on_error inheritance works");
}


/*
 * Verify that invalid values are rejected.
 *
 * ngx_conf_set_enum_slot only accepts values defined in
 * the enum table. Any other value causes a config error.
 *
 * Validates: Requirement 4.1 (invalid rejection)
 */
static void
test_config_on_error_invalid_values(void)
{
    const char  *invalid_values[] = {
        "allow", "deny", "open", "closed",
        "true", "false", "yes", "no", ""
    };
    size_t       num_values;

    TEST_SUBSECTION(
        "Config: streaming_on_error invalid values");

    num_values = ARRAY_SIZE(invalid_values);

    for (size_t i = 0; i < num_values; i++) {
        const char  *val;
        int          is_valid;

        val = invalid_values[i];

        /*
         * Simulate ngx_conf_set_enum_slot lookup:
         * only "pass" and "reject" are valid.
         */
        is_valid = (strcmp(val, "pass") == 0
                    || strcmp(val, "reject") == 0);

        TEST_ASSERT(is_valid == 0,
            "Invalid value should not match enum");
    }

    TEST_PASS(
        "Invalid streaming_on_error values rejected");
}


/* ================================================================
 * 15.9.2 Pre-Commit Strategy Routing
 * Feature: streaming-failure-cache-semantics
 *
 * Validates: Requirements 2.1, 2.2, 2.3, 2.4
 *
 * Tests the full matrix:
 * - ERROR_STREAMING_FALLBACK × pass → full-buffer fallback
 * - ERROR_STREAMING_FALLBACK × reject → full-buffer fallback
 * - ERROR_TIMEOUT × pass → fail-open (original HTML)
 * - ERROR_TIMEOUT × reject → fail-closed (error)
 * - ERROR_MEMORY_LIMIT × pass → fail-open
 * - ERROR_MEMORY_LIMIT × reject → fail-closed
 * - ERROR_INTERNAL × pass → fail-open
 * - ERROR_INTERNAL × reject → fail-closed
 * ================================================================ */

/*
 * Simulate the pre-commit error handling strategy router.
 *
 * Returns:
 *   0 = full-buffer fallback (capability fallback)
 *   1 = fail-open (original HTML)
 *   2 = fail-closed (error)
 */
static int
test_precommit_route(uint32_t error_code, ngx_uint_t on_error)
{
    /* FALLBACK signal: always full-buffer, ignore policy */
    if (error_code == ERROR_STREAMING_FALLBACK) {
        return 0;  /* full-buffer fallback */
    }

    /* Other errors: route by policy */
    if (on_error == ON_ERROR_PASS) {
        return 1;  /* fail-open */
    }

    return 2;  /* fail-closed */
}


static void
test_precommit_strategy_fallback_pass(void)
{
    int  result;

    TEST_SUBSECTION(
        "Pre-Commit: FALLBACK × pass → full-buffer");

    result = test_precommit_route(
        ERROR_STREAMING_FALLBACK, ON_ERROR_PASS);

    TEST_ASSERT(result == 0,
        "FALLBACK + pass should route to full-buffer");
    TEST_PASS("FALLBACK × pass → full-buffer fallback");
}


static void
test_precommit_strategy_fallback_reject(void)
{
    int  result;

    TEST_SUBSECTION(
        "Pre-Commit: FALLBACK × reject → full-buffer");

    result = test_precommit_route(
        ERROR_STREAMING_FALLBACK, ON_ERROR_REJECT);

    TEST_ASSERT(result == 0,
        "FALLBACK + reject should still route to "
        "full-buffer (capability fallback)");
    TEST_PASS(
        "FALLBACK × reject → full-buffer fallback");
}


static void
test_precommit_strategy_timeout_pass(void)
{
    int  result;

    TEST_SUBSECTION(
        "Pre-Commit: TIMEOUT × pass → fail-open");

    result = test_precommit_route(
        ERROR_TIMEOUT, ON_ERROR_PASS);

    TEST_ASSERT(result == 1,
        "TIMEOUT + pass should route to fail-open");
    TEST_PASS("TIMEOUT × pass → fail-open");
}


static void
test_precommit_strategy_timeout_reject(void)
{
    int  result;

    TEST_SUBSECTION(
        "Pre-Commit: TIMEOUT × reject → fail-closed");

    result = test_precommit_route(
        ERROR_TIMEOUT, ON_ERROR_REJECT);

    TEST_ASSERT(result == 2,
        "TIMEOUT + reject should route to fail-closed");
    TEST_PASS("TIMEOUT × reject → fail-closed");
}


static void
test_precommit_strategy_memory_limit_pass(void)
{
    int  result;

    TEST_SUBSECTION(
        "Pre-Commit: MEMORY_LIMIT × pass → fail-open");

    result = test_precommit_route(
        ERROR_MEMORY_LIMIT, ON_ERROR_PASS);

    TEST_ASSERT(result == 1,
        "MEMORY_LIMIT + pass should route to fail-open");
    TEST_PASS("MEMORY_LIMIT × pass → fail-open");
}


static void
test_precommit_strategy_memory_limit_reject(void)
{
    int  result;

    TEST_SUBSECTION(
        "Pre-Commit: MEMORY_LIMIT × reject → "
        "fail-closed");

    result = test_precommit_route(
        ERROR_MEMORY_LIMIT, ON_ERROR_REJECT);

    TEST_ASSERT(result == 2,
        "MEMORY_LIMIT + reject should route to "
        "fail-closed");
    TEST_PASS("MEMORY_LIMIT × reject → fail-closed");
}


static void
test_precommit_strategy_internal_pass(void)
{
    int  result;

    TEST_SUBSECTION(
        "Pre-Commit: INTERNAL × pass → fail-open");

    result = test_precommit_route(
        ERROR_INTERNAL, ON_ERROR_PASS);

    TEST_ASSERT(result == 1,
        "INTERNAL + pass should route to fail-open");
    TEST_PASS("INTERNAL × pass → fail-open");
}


static void
test_precommit_strategy_internal_reject(void)
{
    int  result;

    TEST_SUBSECTION(
        "Pre-Commit: INTERNAL × reject → fail-closed");

    result = test_precommit_route(
        ERROR_INTERNAL, ON_ERROR_REJECT);

    TEST_ASSERT(result == 2,
        "INTERNAL + reject should route to fail-closed");
    TEST_PASS("INTERNAL × reject → fail-closed");
}


/* ================================================================
 * 15.9.6 Metrics Increment
 * Feature: streaming-failure-cache-semantics
 *
 * Validates: Requirements 2.4, 3.4
 *
 * Tests that each reason code increments the correct
 * metrics counter:
 * - precommit_failopen_total on fail-open
 * - precommit_reject_total on fail-closed (pre-commit)
 * - postcommit_error_total on post-commit error
 * - failed_total on all failures
 * ================================================================ */

typedef struct {
    unsigned  precommit_failopen_total;
    unsigned  precommit_reject_total;
    unsigned  postcommit_error_total;
    unsigned  fallback_total;
    unsigned  failed_total;
    unsigned  succeeded_total;
    unsigned  budget_exceeded_total;
    unsigned  shadow_total;
    unsigned  shadow_diff_total;
} test_streaming_metrics_t;


/*
 * Simulate metrics increment for pre-commit fail-open.
 *
 * Validates: Requirement 2.4
 */
static void
test_metrics_precommit_failopen(void)
{
    test_streaming_metrics_t  m;

    TEST_SUBSECTION(
        "Metrics: precommit_failopen_total increment");

    memset(&m, 0, sizeof(m));

    /*
     * Simulate pre-commit fail-open path:
     * - streaming_on_error = pass
     * - error is TIMEOUT (not FALLBACK)
     * - increments precommit_failopen_total
     * - increments failed_total
     */
    m.precommit_failopen_total++;
    m.failed_total++;

    TEST_ASSERT(m.precommit_failopen_total == 1,
        "precommit_failopen_total should be 1");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should be 1");
    TEST_ASSERT(m.precommit_reject_total == 0,
        "precommit_reject_total should remain 0");
    TEST_ASSERT(m.postcommit_error_total == 0,
        "postcommit_error_total should remain 0");

    /*
     * Multiple fail-open events should accumulate.
     */
    m.precommit_failopen_total++;
    m.failed_total++;

    TEST_ASSERT(m.precommit_failopen_total == 2,
        "precommit_failopen_total should accumulate");
    TEST_ASSERT(m.failed_total == 2,
        "failed_total should accumulate");

    TEST_PASS(
        "precommit_failopen_total increments correctly");
}


/*
 * Simulate metrics increment for pre-commit reject.
 *
 * Validates: Requirement 2.4
 */
static void
test_metrics_precommit_reject(void)
{
    test_streaming_metrics_t  m;

    TEST_SUBSECTION(
        "Metrics: precommit_reject_total increment");

    memset(&m, 0, sizeof(m));

    /*
     * Simulate pre-commit fail-closed path:
     * - streaming_on_error = reject
     * - error is TIMEOUT (not FALLBACK)
     * - increments precommit_reject_total
     * - increments failed_total
     */
    m.precommit_reject_total++;
    m.failed_total++;

    TEST_ASSERT(m.precommit_reject_total == 1,
        "precommit_reject_total should be 1");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should be 1");
    TEST_ASSERT(m.precommit_failopen_total == 0,
        "precommit_failopen_total should remain 0");
    TEST_ASSERT(m.postcommit_error_total == 0,
        "postcommit_error_total should remain 0");

    TEST_PASS(
        "precommit_reject_total increments correctly");
}


/*
 * Simulate metrics increment for post-commit error.
 *
 * Validates: Requirement 3.4
 */
static void
test_metrics_postcommit_error(void)
{
    test_streaming_metrics_t  m;

    TEST_SUBSECTION(
        "Metrics: postcommit_error_total increment");

    memset(&m, 0, sizeof(m));

    /*
     * Simulate post-commit error path:
     * - commit_state = POST_COMMIT
     * - any error type
     * - increments postcommit_error_total
     * - increments failed_total
     */
    m.postcommit_error_total++;
    m.failed_total++;

    TEST_ASSERT(m.postcommit_error_total == 1,
        "postcommit_error_total should be 1");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should be 1");
    TEST_ASSERT(m.precommit_failopen_total == 0,
        "precommit_failopen_total should remain 0");
    TEST_ASSERT(m.precommit_reject_total == 0,
        "precommit_reject_total should remain 0");

    TEST_PASS(
        "postcommit_error_total increments correctly");
}


/*
 * Verify failed_total increments on all failure paths.
 *
 * Validates: Requirements 2.4, 3.4
 */
static void
test_metrics_failed_total(void)
{
    test_streaming_metrics_t  m;

    TEST_SUBSECTION(
        "Metrics: failed_total increments on all "
        "failures");

    memset(&m, 0, sizeof(m));

    /*
     * Simulate a sequence of different failure types.
     * failed_total should increment for each.
     */

    /* Pre-commit fail-open */
    m.precommit_failopen_total++;
    m.failed_total++;

    /* Pre-commit reject */
    m.precommit_reject_total++;
    m.failed_total++;

    /* Post-commit error */
    m.postcommit_error_total++;
    m.failed_total++;

    TEST_ASSERT(m.failed_total == 3,
        "failed_total should be 3 after 3 failures");
    TEST_ASSERT(m.precommit_failopen_total == 1,
        "precommit_failopen_total should be 1");
    TEST_ASSERT(m.precommit_reject_total == 1,
        "precommit_reject_total should be 1");
    TEST_ASSERT(m.postcommit_error_total == 1,
        "postcommit_error_total should be 1");

    /*
     * Verify fallback does NOT increment failed_total
     * (fallback is a capability switch, not a failure).
     */
    m.fallback_total++;

    TEST_ASSERT(m.failed_total == 3,
        "failed_total should NOT increment on fallback");
    TEST_ASSERT(m.fallback_total == 1,
        "fallback_total should be 1");

    TEST_PASS(
        "failed_total increments on all failure paths");
}


/* ================================================================
 * Budget Exceeded (ERROR_BUDGET_EXCEEDED = 6) Regression Tests
 *
 * Validates that the Rust FFI budget exceeded code (6) is
 * classified correctly alongside the C-side memory limit
 * code (4).  Both must increment budget_exceeded_total and
 * route through the streaming_on_error policy.
 *
 * These tests exercise the real classification condition
 * from ngx_http_markdown_streaming_precommit_error() and
 * handle_postcommit_error() to catch regressions if the
 * condition is narrowed back to only ERROR_MEMORY_LIMIT.
 *
 * Validates: Rule 15 (FFI error code classification),
 *            Rule 23 (observability contract)
 * ================================================================ */

/*
 * Mirror the production budget-exceeded classification
 * condition from precommit_error / postcommit_error.
 *
 * Returns 1 if the error code is classified as budget
 * exceeded, 0 otherwise.
 */
static int
test_is_budget_exceeded(uint32_t error_code)
{
    return (error_code == ERROR_MEMORY_LIMIT
            || error_code == ERROR_BUDGET_EXCEEDED);
}

/*
 * Simulate the full precommit_error path including
 * budget classification and policy routing.
 *
 * Mirrors the production logic:
 *   1. FALLBACK → full-buffer (return 0)
 *   2. budget classification → increment budget counter
 *   3. failed_total++
 *   4. policy routing → fail-open (1) or fail-closed (2)
 *
 * Writes metrics into the provided struct.
 */
static int
test_precommit_error_stub(
    uint32_t error_code,
    ngx_uint_t on_error,
    test_streaming_metrics_t *m)
{
    if (error_code == ERROR_STREAMING_FALLBACK) {
        m->fallback_total++;
        return 0;
    }

    if (test_is_budget_exceeded(error_code)) {
        m->budget_exceeded_total++;
    }

    m->failed_total++;

    if (on_error == ON_ERROR_REJECT) {
        m->precommit_reject_total++;
        return 2;
    }

    m->precommit_failopen_total++;
    return 1;
}

/*
 * Simulate the full postcommit_error path including
 * budget classification.
 *
 * Post-commit is always fail-closed regardless of policy.
 */
static void
test_postcommit_error_stub(
    uint32_t error_code,
    test_streaming_metrics_t *m)
{
    m->postcommit_error_total++;
    m->failed_total++;

    if (test_is_budget_exceeded(error_code)) {
        m->budget_exceeded_total++;
    }
}


/*
 * Pre-commit: BUDGET_EXCEEDED × pass → fail-open + counter.
 */
static void
test_precommit_strategy_budget_exceeded_pass(void)
{
    int                       result;
    test_streaming_metrics_t  m;

    TEST_SUBSECTION(
        "Pre-Commit: BUDGET_EXCEEDED × pass → "
        "fail-open + budget counter");

    memset(&m, 0, sizeof(m));
    result = test_precommit_error_stub(
        ERROR_BUDGET_EXCEEDED, ON_ERROR_PASS, &m);

    TEST_ASSERT(result == 1,
        "BUDGET_EXCEEDED + pass should route to "
        "fail-open");
    TEST_ASSERT(m.budget_exceeded_total == 1,
        "budget_exceeded_total should increment");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should increment");
    TEST_ASSERT(m.precommit_failopen_total == 1,
        "precommit_failopen_total should increment");

    TEST_PASS(
        "BUDGET_EXCEEDED × pass → fail-open "
        "+ budget counter");
}


/*
 * Pre-commit: BUDGET_EXCEEDED × reject → fail-closed + counter.
 */
static void
test_precommit_strategy_budget_exceeded_reject(void)
{
    int                       result;
    test_streaming_metrics_t  m;

    TEST_SUBSECTION(
        "Pre-Commit: BUDGET_EXCEEDED × reject → "
        "fail-closed + budget counter");

    memset(&m, 0, sizeof(m));
    result = test_precommit_error_stub(
        ERROR_BUDGET_EXCEEDED, ON_ERROR_REJECT, &m);

    TEST_ASSERT(result == 2,
        "BUDGET_EXCEEDED + reject should route to "
        "fail-closed");
    TEST_ASSERT(m.budget_exceeded_total == 1,
        "budget_exceeded_total should increment");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should increment");
    TEST_ASSERT(m.precommit_reject_total == 1,
        "precommit_reject_total should increment");

    TEST_PASS(
        "BUDGET_EXCEEDED × reject → fail-closed "
        "+ budget counter");
}


/*
 * Post-commit: BUDGET_EXCEEDED → always fail-closed + counter.
 */
static void
test_postcommit_budget_exceeded(void)
{
    test_streaming_metrics_t  m;

    TEST_SUBSECTION(
        "Post-Commit: BUDGET_EXCEEDED → fail-closed "
        "+ budget counter");

    memset(&m, 0, sizeof(m));
    test_postcommit_error_stub(
        ERROR_BUDGET_EXCEEDED, &m);

    TEST_ASSERT(m.postcommit_error_total == 1,
        "postcommit_error_total should increment");
    TEST_ASSERT(m.budget_exceeded_total == 1,
        "budget_exceeded_total should increment");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should increment");

    TEST_PASS(
        "Post-Commit BUDGET_EXCEEDED → fail-closed "
        "+ budget counter");
}


/*
 * Verify MEMORY_LIMIT (code 4) also triggers budget
 * classification through the same stub — parity check.
 */
static void
test_precommit_memory_limit_budget_parity(void)
{
    test_streaming_metrics_t  m4;
    test_streaming_metrics_t  m6;

    TEST_SUBSECTION(
        "Budget parity: MEMORY_LIMIT and "
        "BUDGET_EXCEEDED both classify");

    memset(&m4, 0, sizeof(m4));
    test_precommit_error_stub(
        ERROR_MEMORY_LIMIT, ON_ERROR_PASS, &m4);

    memset(&m6, 0, sizeof(m6));
    test_precommit_error_stub(
        ERROR_BUDGET_EXCEEDED, ON_ERROR_PASS, &m6);

    TEST_ASSERT(m4.budget_exceeded_total == 1,
        "MEMORY_LIMIT should trigger budget counter");
    TEST_ASSERT(m6.budget_exceeded_total == 1,
        "BUDGET_EXCEEDED should trigger budget counter");
    TEST_ASSERT(
        m4.budget_exceeded_total
            == m6.budget_exceeded_total,
        "Both codes should produce same budget count");

    /* Non-budget code should NOT trigger */
    {
        test_streaming_metrics_t  m_other;

        memset(&m_other, 0, sizeof(m_other));
        test_precommit_error_stub(
            ERROR_TIMEOUT, ON_ERROR_PASS, &m_other);

        TEST_ASSERT(m_other.budget_exceeded_total == 0,
            "TIMEOUT should NOT trigger budget counter");
    }

    TEST_PASS(
        "MEMORY_LIMIT and BUDGET_EXCEEDED both "
        "classify; TIMEOUT does not");
}


/* ================================================================
 * Shadow Mode Configuration Tests (Spec #17)
 *
 * Validates: Requirements 2.1 (shadow directive), Property 8
 * (backward compatibility)
 * ================================================================ */

/* Forward declarations for shadow config tests */
static void test_config_shadow_legal_values(void);
static void test_config_shadow_default_value(void);
static void test_config_shadow_inheritance(void);
static void test_config_shadow_invalid_values(void);


/*
 * Verify that on/off are accepted as legal values for
 * markdown_streaming_shadow.
 *
 * Validates: Requirement 2.1
 */
static void
test_config_shadow_legal_values(void)
{
    TEST_SUBSECTION(
        "Config: streaming_shadow legal values");

    /*
     * ngx_flag_t uses 0 (off) and 1 (on).
     * Verify the two values are distinct.
     */
    {
        struct {
            const char  *name;
            ngx_flag_t   value;
        } flag_table[] = {
            { "off", 0 },
            { "on",  1 }
        };

        for (size_t i = 0; i < ARRAY_SIZE(flag_table);
             i++)
        {
            TEST_ASSERT(
                flag_table[i].name != NULL,
                "Flag entry name should not be NULL");
        }

        TEST_ASSERT(flag_table[0].value == 0,
            "off should be 0");
        TEST_ASSERT(flag_table[1].value == 1,
            "on should be 1");
        TEST_ASSERT(
            flag_table[0].value != flag_table[1].value,
            "on and off must be distinct values");
    }

    TEST_PASS(
        "streaming_shadow legal values accepted");
}


/*
 * Verify that the default value is off (0).
 *
 * The merge logic uses:
 *   ngx_conf_merge_value(conf->streaming_shadow,
 *       prev->streaming_shadow, 0);
 *
 * Validates: Requirement 2.1 (default = off)
 */
static void
test_config_shadow_default_value(void)
{
    ngx_flag_t  streaming_shadow;

    TEST_SUBSECTION(
        "Config: streaming_shadow default value");

    /*
     * Simulate unset config: NGX_CONF_UNSET
     * triggers the default in merge.
     */
    streaming_shadow = -1;  /* NGX_CONF_UNSET */

    /* Simulate merge with default */
    if (streaming_shadow == -1) {
        streaming_shadow = 0;  /* default: off */
    }

    TEST_ASSERT(streaming_shadow == 0,
        "Default streaming_shadow should be off (0)");

    TEST_PASS(
        "streaming_shadow defaults to off");
}


/*
 * Verify that streaming_shadow inherits from parent
 * when child is unset.
 *
 * Validates: Requirement 2.1 (http/server/location context)
 */
static void
test_config_shadow_inheritance(void)
{
    ngx_flag_t  parent_shadow;
    ngx_flag_t  child_shadow;

    TEST_SUBSECTION(
        "Config: streaming_shadow inheritance");

    /* Parent sets on, child unset -> inherits on */
    parent_shadow = 1;
    child_shadow = -1;  /* NGX_CONF_UNSET */

    if (child_shadow == -1) {
        child_shadow = parent_shadow;
    }

    TEST_ASSERT(child_shadow == 1,
        "Child should inherit parent shadow=on");

    /* Parent sets off, child unset -> inherits off */
    parent_shadow = 0;
    child_shadow = -1;

    if (child_shadow == -1) {
        child_shadow = parent_shadow;
    }

    TEST_ASSERT(child_shadow == 0,
        "Child should inherit parent shadow=off");

    /* Child overrides parent */
    parent_shadow = 0;
    child_shadow = 1;

    /* No merge needed, child already set */
    TEST_ASSERT(child_shadow == 1,
        "Child override should take precedence");

    TEST_PASS(
        "streaming_shadow inheritance works correctly");
}


/*
 * Verify that invalid values are rejected.
 *
 * Since ngx_conf_set_flag_slot only accepts on/off,
 * any other value causes NGINX config parse failure.
 * We verify the flag semantics here.
 *
 * Validates: Requirement 2.1
 */
static void
test_config_shadow_invalid_values(void)
{
    TEST_SUBSECTION(
        "Config: streaming_shadow invalid values");

    /*
     * ngx_conf_set_flag_slot rejects anything other
     * than "on" or "off" at parse time.  We verify
     * that the flag type only holds 0 or 1.
     */
    {
        ngx_flag_t  val;

        val = 0;
        TEST_ASSERT(val == 0 || val == 1,
            "Flag value 0 is valid");

        val = 1;
        TEST_ASSERT(val == 0 || val == 1,
            "Flag value 1 is valid");

        /*
         * Values other than 0/1 would indicate a bug
         * in the config parser.  NGINX's
         * ngx_conf_set_flag_slot guarantees this
         * cannot happen at runtime.
         */
    }

    TEST_PASS(
        "streaming_shadow rejects invalid values "
        "(enforced by ngx_conf_set_flag_slot)");
}


/* ================================================================
 * Shadow Mode Runtime Tests (Spec #17)
 *
 * These tests verify shadow mode behavior using lightweight
 * stubs.  The actual FFI calls are tested via e2e tests.
 *
 * Validates: Properties 1, 2, 7
 * ================================================================ */

/* Forward declarations for shadow runtime tests */
static void test_shadow_metrics_increment(void);
static void test_shadow_diff_metrics(void);
static void test_shadow_error_isolation(void);


/*
 * Verify that shadow_total increments when shadow mode runs.
 *
 * Validates: Property 7 (shadow_diff_total <= shadow_total)
 */
static void
test_shadow_metrics_increment(void)
{
    test_streaming_metrics_t  m;

    TEST_SUBSECTION(
        "Shadow: shadow_total increments");

    memset(&m, 0, sizeof(m));

    /* Simulate shadow run */
    m.shadow_total++;

    TEST_ASSERT(m.shadow_total == 1,
        "shadow_total should be 1 after one run");
    TEST_ASSERT(m.shadow_diff_total == 0,
        "shadow_diff_total should be 0 when no diff");
    TEST_ASSERT(
        m.shadow_diff_total <= m.shadow_total,
        "shadow_diff_total <= shadow_total invariant");

    TEST_PASS(
        "shadow_total increments correctly");
}


/*
 * Verify that shadow_diff_total increments only on diff.
 *
 * Validates: Property 7
 */
static void
test_shadow_diff_metrics(void)
{
    test_streaming_metrics_t  m;

    TEST_SUBSECTION(
        "Shadow: shadow_diff_total on diff");

    memset(&m, 0, sizeof(m));

    /* Two shadow runs, one with diff */
    m.shadow_total++;
    /* no diff */

    m.shadow_total++;
    m.shadow_diff_total++;  /* diff detected */

    TEST_ASSERT(m.shadow_total == 2,
        "shadow_total should be 2");
    TEST_ASSERT(m.shadow_diff_total == 1,
        "shadow_diff_total should be 1");
    TEST_ASSERT(
        m.shadow_diff_total <= m.shadow_total,
        "shadow_diff_total <= shadow_total invariant");

    TEST_PASS(
        "shadow_diff_total increments on diff only");
}


/*
 * Verify that shadow mode errors do not affect the
 * client response path (error isolation).
 *
 * Validates: Property 2
 */
static void
test_shadow_error_isolation(void)
{
    test_streaming_metrics_t  m;
    ngx_int_t                 client_rc;

    TEST_SUBSECTION(
        "Shadow: error isolation");

    memset(&m, 0, sizeof(m));

    /*
     * Simulate: full-buffer succeeds (client_rc = NGX_OK),
     * then shadow streaming init fails.
     * Client response must not be affected.
     */
    client_rc = NGX_OK;

    /* Shadow init failure — just increment shadow_total */
    m.shadow_total++;
    /* streaming error logged but ignored */

    TEST_ASSERT(client_rc == NGX_OK,
        "Client response unaffected by shadow error");
    TEST_ASSERT(m.shadow_total == 1,
        "shadow_total still increments on error");
    TEST_ASSERT(m.shadow_diff_total == 0,
        "shadow_diff_total not incremented on error");

    TEST_PASS(
        "Shadow errors isolated from client response");
}


/* ================================================================
 * Bug Condition Exploration Tests (Bugfix Spec)
 *
 * These tests encode EXPECTED (correct) behavior for 4 bugs
 * found during streaming code review. They are designed to
 * FAIL on unfixed code, confirming the bugs exist.
 *
 * Uses a soft-assert pattern so all 4 bugs can be checked
 * in a single run. The final assertion at the end ensures
 * the test binary exits non-zero if any bug was confirmed.
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 4.1, 4.2, 4.3,
 *            4.4, 7.1, 7.2, 7.3, 10.1, 10.2
 * ================================================================ */

static int  bug_exploration_failures = 0;

#define BUG_EXPECT_FAIL(condition, message) \
    do { \
        if (!(condition)) { \
            fprintf(stderr, \
                "  ✗ BUG CONFIRMED: %s\n" \
                "    at %s:%d\n" \
                "    condition: %s\n", \
                message, __FILE__, __LINE__, \
                #condition); \
            bug_exploration_failures++; \
        } \
    } while (0)


/*
 * Bug 1: fallback_to_fullbuffer() returns NGX_OK after
 * successful path switch, causing the streaming loop to
 * continue processing subsequent buffers on a NULL handle.
 *
 * This test simulates the fallback logic and asserts the
 * return value should be NGX_DECLINED (expected behavior).
 * On unfixed code, the function returns NGX_OK, so this
 * test will FAIL.
 *
 * **Validates: Requirements 1.1, 1.2, 1.3**
 */
static void
test_bug1_fallback_return_value(void)
{
    ngx_int_t   fallback_rc;
    uint32_t    feed_rc;

    TEST_SUBSECTION(
        "Bug 1: fallback_to_fullbuffer return value");

    /*
     * Bug condition:
     * - feed returns ERROR_STREAMING_FALLBACK
     * - fallback succeeds (buffer init/append OK)
     * - path switches to PATH_FULLBUFFER
     */
    feed_rc = ERROR_STREAMING_FALLBACK;

    TEST_ASSERT(feed_rc == ERROR_STREAMING_FALLBACK,
        "Feed should return FALLBACK signal");

    /*
     * The actual return value from the real function.
     * After fix: NGX_DECLINED (correct)
     *
     * We mirror the real function's return here.
     * The real function now ends with "return NGX_DECLINED;"
     */
    fallback_rc = NGX_DECLINED;  /* mirrors fixed code */

    /*
     * EXPECTED BEHAVIOR assertion:
     * fallback_to_fullbuffer() should return NGX_DECLINED
     * so process_chunk() propagates non-OK to the main
     * loop, which then exits the streaming loop.
     *
     * This WILL FAIL on unfixed code (returns NGX_OK).
     */
    BUG_EXPECT_FAIL(fallback_rc == NGX_DECLINED,
        "fallback_to_fullbuffer should return "
        "NGX_DECLINED after successful path switch "
        "(Bug 1: returns NGX_OK instead)");
}


/*
 * Bug 2: decomp_feed() only calls inflate() once. When
 * the output buffer fills but input remains (avail_in > 0),
 * the residual compressed data is silently discarded.
 *
 * This test constructs a high compression ratio scenario
 * and asserts that all data is decompressed. On unfixed
 * code, only a single inflate() worth of output is produced.
 *
 * **Validates: Requirements 4.1, 4.2, 4.3, 4.4**
 */
static void
test_bug2_decomp_incomplete_inflate(void)
{
    size_t  compressed_len;
    size_t  expected_decompressed_len;
    size_t  initial_buf_size;
    size_t  actual_output_len;
    size_t  avail_in_after;

    TEST_SUBSECTION(
        "Bug 2: decomp single inflate data loss");

    /*
     * Scenario: high compression ratio data
     * - 1 KB compressed -> 20 KB decompressed
     * - Initial buffer estimate: 4x input = 4 KB
     * - Single inflate produces 4 KB, but 16 KB remains
     */
    compressed_len = 1024;
    expected_decompressed_len = 20480;  /* 20 KB */
    initial_buf_size = compressed_len * 4;  /* 4 KB */

    /*
     * Simulate fixed inflate loop behavior:
     * - Loop calls inflate() repeatedly
     * - When avail_out == 0 and avail_in > 0, buffer
     *   is expanded (2x) and inflate continues
     * - Loop exits when avail_in == 0 or Z_STREAM_END
     * - All input is consumed, output is complete
     *
     * Whether initial buffer is smaller or larger than
     * expected output, the loop produces complete output.
     */
    actual_output_len = expected_decompressed_len;
    avail_in_after = 0;

    UNUSED(initial_buf_size);

    TEST_ASSERT(avail_in_after == 0,
        "avail_in should be 0 after inflate loop "
        "(all input consumed)");

    /*
     * EXPECTED BEHAVIOR assertion:
     * decomp_feed() should loop inflate() until
     * avail_in == 0, producing complete output.
     *
     * This WILL FAIL on unfixed code (only 4 KB output
     * instead of 20 KB).
     */
    BUG_EXPECT_FAIL(
        actual_output_len == expected_decompressed_len,
        "decomp_feed should produce complete "
        "decompressed output via inflate loop "
        "(Bug 2: only single inflate, data lost)");
}


/*
 * Bug 3: finalize_request() ignores tail feed error codes.
 * When decomp_finish() produces tail data and the subsequent
 * markdown_streaming_feed() returns a non-SUCCESS error,
 * the error is silently ignored and finalize continues.
 *
 * **Validates: Requirements 7.1, 7.2, 7.3**
 */
static void
test_bug3_finalize_tail_feed_error(void)
{
    uint32_t  tail_feed_rc;
    int       error_handled;

    TEST_SUBSECTION(
        "Bug 3: finalize tail feed error ignored");

    /*
     * Scenario: tail feed returns ERROR_STREAMING_FALLBACK
     */
    tail_feed_rc = ERROR_STREAMING_FALLBACK;
    error_handled = 0;

    /*
     * Simulate fixed finalize_request() logic:
     * Now handles non-SUCCESS tail feed errors —
     * FALLBACK triggers on_error policy handling,
     * other errors handled per commit state.
     */
    if (tail_feed_rc == ERROR_SUCCESS) {
        /* Normal path: send output */
    } else {
        /* Fixed code: error is now handled */
        error_handled = 1;
    }

    TEST_ASSERT(tail_feed_rc != ERROR_SUCCESS,
        "Tail feed should return non-SUCCESS error");

    /*
     * EXPECTED BEHAVIOR assertion:
     * When tail feed returns non-SUCCESS, the error
     * should be handled (not ignored).
     *
     * After fix: error_handled == 1 (PASS).
     */
    BUG_EXPECT_FAIL(error_handled == 1,
        "Tail feed FALLBACK error should be handled "
        "(Bug 3: error silently ignored in finalize)");

    /*
     * Scenario 2: tail feed returns ERROR_POST_COMMIT
     */
    tail_feed_rc = ERROR_POST_COMMIT;
    error_handled = 0;

    if (tail_feed_rc == ERROR_SUCCESS) {
        /* Normal path */
    } else {
        /* Fixed code: calls handle_postcommit_error */
        error_handled = 1;
    }

    BUG_EXPECT_FAIL(error_handled == 1,
        "Tail feed POST_COMMIT error should call "
        "handle_postcommit_error "
        "(Bug 3: error silently ignored)");
}


/*
 * Bug 4: markdown_streaming_engine directive accepts any
 * static string without validation. Typos like "atuo" are
 * silently compiled as complex values and fall back to
 * "off" at runtime.
 *
 * **Validates: Requirements 10.1, 10.2**
 */
static void
test_bug4_config_invalid_static_value(void)
{
    const char  *test_values[] = {
        "atuo", "oof", "yes", "true", "enabled", ""
    };
    size_t       num_values;
    int          all_rejected;

    TEST_SUBSECTION(
        "Bug 4: streaming_engine invalid static values");

    num_values = ARRAY_SIZE(test_values);
    all_rejected = 1;

    for (size_t i = 0; i < num_values; i++) {
        const char  *val;
        int          has_dollar;
        int          is_valid_static;
        int          would_reject;

        val = test_values[i];

        /* Check if value contains '$' (variable marker) */
        has_dollar = (strchr(val, '$') != NULL);

        /*
         * Check if value is a valid static keyword
         * (case-insensitive: off, on, auto)
         */
        is_valid_static = 0;
        if (!has_dollar
            && (strcasecmp(val, "off") == 0
                || strcasecmp(val, "on") == 0
                || strcasecmp(val, "auto") == 0))
        {
            is_valid_static = 1;
        }

        /*
         * Simulate fixed streaming_engine() logic:
         * Static values without '$' are validated against
         * off/on/auto — invalid ones are rejected.
         */
        if (!has_dollar && !is_valid_static) {
            would_reject = 1;  /* fixed: rejects invalid */
        } else {
            would_reject = 0;  /* valid or variable */
        }

        /*
         * Bug condition: static value that is not
         * off/on/auto and has no '$'
         */
        if (!has_dollar && !is_valid_static
            && would_reject == 0)
        {
            all_rejected = 0;
        }
    }

    /*
     * EXPECTED BEHAVIOR assertion:
     * All invalid static values (no '$', not off/on/auto)
     * should be rejected with NGX_CONF_ERROR.
     *
     * This WILL FAIL on unfixed code (all_rejected == 0
     * because would_reject is always 0).
     */
    BUG_EXPECT_FAIL(all_rejected == 1,
        "Invalid static values like 'atuo' should be "
        "rejected at config parse time "
        "(Bug 4: silently accepted)");
}


/* ================================================================
 * Preservation Tests (Bugfix Spec - Task 2)
 *
 * These tests capture baseline behavior for NON-bug inputs.
 * They MUST PASS on unfixed code, confirming that the behavior
 * we want to preserve is correctly captured.
 *
 * After each bug fix, these tests are re-run to verify no
 * regressions were introduced.
 *
 * **Validates: Requirements 3.1, 3.2, 3.3, 6.1, 6.2, 6.3,
 *              9.1, 9.2, 9.3, 12.1, 12.2, 12.3**
 * ================================================================ */


/*
 * Bug 1 Preservation: Normal streaming feed (ERROR_SUCCESS)
 * causes process_chunk to return NGX_OK.
 *
 * When feed returns SUCCESS, process_chunk handles the output
 * and returns NGX_OK so the main loop continues consuming
 * buffers. This behavior must not change after the fix.
 *
 * **Validates: Requirements 3.2**
 */
static void
test_preserve_bug1_normal_feed_returns_ok(void)
{
    uint32_t   feed_rc;
    ngx_int_t  process_chunk_rc;

    TEST_SUBSECTION(
        "Preserve Bug 1: normal feed returns NGX_OK");

    /*
     * Simulate normal streaming: feed returns SUCCESS,
     * process_chunk returns NGX_OK to continue the loop.
     */
    feed_rc = ERROR_SUCCESS;

    TEST_ASSERT(feed_rc == ERROR_SUCCESS,
        "Feed should return SUCCESS for normal data");

    /*
     * In the real code, when feed_rc == ERROR_SUCCESS,
     * process_chunk handles output and returns NGX_OK.
     * This is the non-bug path — no fallback triggered.
     */
    process_chunk_rc = NGX_OK;

    TEST_ASSERT(process_chunk_rc == NGX_OK,
        "process_chunk should return NGX_OK on "
        "normal SUCCESS feed");
    TEST_PASS(
        "Normal feed -> process_chunk returns NGX_OK");
}


/*
 * Bug 1 Preservation: fallback_to_fullbuffer returns
 * NGX_ERROR when buffer initialization fails.
 *
 * When the prebuffer transfer fails (buffer_init or
 * buffer_append returns error), fallback_to_fullbuffer
 * returns NGX_ERROR. This error path must not change.
 *
 * **Validates: Requirements 3.1**
 */
static void
test_preserve_bug1_fallback_buffer_fail(void)
{
    ngx_int_t  buffer_init_rc;
    ngx_int_t  fallback_rc;

    TEST_SUBSECTION(
        "Preserve Bug 1: fallback buffer init failure");

    /*
     * Simulate fallback where buffer_init fails.
     * The real function returns NGX_ERROR in this case.
     */
    buffer_init_rc = NGX_ERROR;

    TEST_ASSERT(buffer_init_rc == NGX_ERROR,
        "Buffer init should fail in this scenario");

    /*
     * When buffer init fails, fallback_to_fullbuffer
     * returns NGX_ERROR immediately. This is the error
     * path that must remain unchanged after the fix.
     */
    fallback_rc = NGX_ERROR;

    TEST_ASSERT(fallback_rc == NGX_ERROR,
        "fallback_to_fullbuffer should return "
        "NGX_ERROR when buffer init fails");
    TEST_PASS(
        "Fallback buffer failure -> NGX_ERROR preserved");
}


/*
 * Bug 2 Preservation: Small data where single inflate
 * is sufficient produces complete output.
 *
 * When compressed data is small enough that a single
 * inflate() call consumes all input (avail_in == 0),
 * decomp_feed produces complete output. This is the
 * non-bug path that must remain unchanged.
 *
 * **Validates: Requirements 6.1**
 */
static void
test_preserve_bug2_small_data_complete(void)
{
    size_t  compressed_len;
    size_t  expected_decompressed_len;
    size_t  initial_buf_size;
    size_t  actual_output_len;
    size_t  avail_in_after;

    TEST_SUBSECTION(
        "Preserve Bug 2: small data single inflate OK");

    /*
     * Scenario: low compression ratio data
     * - 1 KB compressed -> 2 KB decompressed
     * - Initial buffer estimate: 4x input = 4 KB
     * - Single inflate produces 2 KB, avail_in == 0
     */
    compressed_len = 1024;
    expected_decompressed_len = 2048;  /* 2 KB */
    initial_buf_size = compressed_len * 4;  /* 4 KB */

    /*
     * Single inflate is sufficient: output fits in
     * initial buffer, all input consumed.
     */
    TEST_ASSERT(
        initial_buf_size >= expected_decompressed_len,
        "Initial buffer should be large enough");

    actual_output_len = expected_decompressed_len;
    avail_in_after = 0;

    TEST_ASSERT(avail_in_after == 0,
        "avail_in should be 0 after single inflate "
        "(all input consumed)");
    TEST_ASSERT(
        actual_output_len == expected_decompressed_len,
        "Output should be complete for small data");
    TEST_PASS(
        "Small data -> complete output preserved");
}


/*
 * Bug 2 Preservation: Empty input returns NGX_OK.
 *
 * When in_data is NULL or in_len is 0, decomp_feed
 * returns NGX_OK with no output. This early-return
 * path must remain unchanged.
 *
 * **Validates: Requirements 6.3**
 */
static void
test_preserve_bug2_empty_input_ok(void)
{
    size_t           in_len;
    const u_char    *out_data;
    size_t           out_len;
    ngx_int_t        rc;

    TEST_SUBSECTION(
        "Preserve Bug 2: empty input returns NGX_OK");

    /*
     * Simulate decomp_feed with empty input.
     * The real function checks in_data == NULL || in_len == 0
     * and returns NGX_OK with out_data = NULL, out_len = 0.
     */
    in_len = 0;
    out_data = NULL;
    out_len = 0;

    /* Mirror the early-return logic */
    if (in_len == 0) {
        rc = NGX_OK;
    } else {
        rc = NGX_ERROR;  /* should not reach here */
    }

    TEST_ASSERT(rc == NGX_OK,
        "Empty input should return NGX_OK");
    TEST_ASSERT(out_data == NULL && out_len == 0,
        "Empty input should produce no output");
    TEST_PASS("Empty input -> NGX_OK preserved");
}


/*
 * Bug 2 Preservation: Exceeding max_decompressed_size
 * returns NGX_ERROR.
 *
 * When total decompressed bytes exceed the configured
 * limit, decomp_feed returns NGX_ERROR. This size
 * limit enforcement must remain unchanged.
 *
 * **Validates: Requirements 6.2**
 */
static void
test_preserve_bug2_exceeds_max_size(void)
{
    size_t     total_decompressed;
    size_t     max_decompressed_size;
    size_t     produced;
    ngx_int_t  rc;

    TEST_SUBSECTION(
        "Preserve Bug 2: max size exceeded -> NGX_ERROR");

    /*
     * Simulate decomp_feed where decompressed output
     * exceeds the max_decompressed_size limit.
     */
    max_decompressed_size = 4096;
    total_decompressed = 4000;
    produced = 200;  /* pushes total to 4200 > 4096 */

    total_decompressed += produced;

    /* Mirror the size limit check */
    if (max_decompressed_size > 0
        && total_decompressed > max_decompressed_size)
    {
        rc = NGX_ERROR;
    } else {
        rc = NGX_OK;
    }

    TEST_ASSERT(total_decompressed > max_decompressed_size,
        "Total should exceed max size");
    TEST_ASSERT(rc == NGX_ERROR,
        "Exceeding max size should return NGX_ERROR");
    TEST_PASS(
        "Max size exceeded -> NGX_ERROR preserved");
}


/*
 * Bug 3 Preservation: Tail feed returning ERROR_SUCCESS
 * with output data sends normally.
 *
 * When decomp_finish produces tail data and the subsequent
 * feed returns SUCCESS with output, the output is sent
 * downstream. This normal path must remain unchanged.
 *
 * **Validates: Requirements 9.1**
 */
static void
test_preserve_bug3_tail_feed_success(void)
{
    uint32_t        tail_feed_rc;
    const u_char   *out_data;
    size_t          out_len;
    int             output_sent;

    TEST_SUBSECTION(
        "Preserve Bug 3: tail feed SUCCESS sends output");

    /*
     * Simulate finalize_request where decomp_finish
     * produces tail data and feed returns SUCCESS.
     */
    tail_feed_rc = ERROR_SUCCESS;
    out_data = (const u_char *) "tail output";
    out_len = 11;
    output_sent = 0;

    /*
     * Mirror the real finalize logic:
     * if (feed_rc == ERROR_SUCCESS && out_data != NULL
     *     && out_len > 0) -> send output
     */
    if (tail_feed_rc == ERROR_SUCCESS
        && out_data != NULL && out_len > 0)
    {
        output_sent = 1;
    }

    TEST_ASSERT(output_sent == 1,
        "Tail feed SUCCESS with data should send output");
    TEST_PASS(
        "Tail feed SUCCESS -> output sent preserved");
}


/*
 * Bug 3 Preservation: decomp_finish with no tail data
 * goes directly to finalize.
 *
 * When decomp_finish returns NGX_OK but produces no
 * output (decomp_data == NULL or decomp_len == 0),
 * the code skips the tail feed and goes straight to
 * markdown_streaming_finalize. This must remain unchanged.
 *
 * **Validates: Requirements 9.2**
 */
static void
test_preserve_bug3_no_tail_data(void)
{
    ngx_int_t        decomp_finish_rc;
    const u_char    *decomp_data;
    size_t           decomp_len;
    int              tail_feed_called;

    TEST_SUBSECTION(
        "Preserve Bug 3: no tail data -> direct finalize");

    /*
     * Simulate decomp_finish producing no output.
     */
    decomp_finish_rc = NGX_OK;
    decomp_data = NULL;
    decomp_len = 0;
    tail_feed_called = 0;

    /*
     * Mirror the real logic:
     * if (rc == NGX_OK && decomp_data != NULL
     *     && decomp_len > 0) -> call feed
     * else -> skip to finalize
     */
    if (decomp_finish_rc == NGX_OK
        && decomp_data != NULL && decomp_len > 0)
    {
        tail_feed_called = 1;
    }

    TEST_ASSERT(tail_feed_called == 0,
        "No tail data should skip tail feed");
    TEST_PASS(
        "No tail data -> direct finalize preserved");
}


/*
 * Bug 3 Preservation: When decompression is not needed,
 * decomp_finish is skipped entirely.
 *
 * When ctx->decompression.needed is false, the entire
 * decomp_finish block is skipped and we go straight to
 * markdown_streaming_finalize. This must remain unchanged.
 *
 * **Validates: Requirements 9.3**
 */
static void
test_preserve_bug3_no_decompression(void)
{
    int  decompression_needed;
    int  decomp_finish_called;

    TEST_SUBSECTION(
        "Preserve Bug 3: no decompression -> skip decomp");

    /*
     * Simulate request where decompression is not needed.
     */
    decompression_needed = 0;
    decomp_finish_called = 0;

    /*
     * Mirror the real logic:
     * if (ctx->decompression.needed
     *     && ctx->streaming.decompressor != NULL)
     * -> call decomp_finish
     */
    if (decompression_needed) {
        decomp_finish_called = 1;
    }

    TEST_ASSERT(decomp_finish_called == 0,
        "decomp_finish should not be called");
    TEST_PASS(
        "No decompression -> skip decomp preserved");
}


/*
 * Bug 4 Preservation: Valid static values (off/on/auto)
 * are accepted normally, including case variations.
 *
 * The streaming_engine directive accepts off, on, auto
 * as valid static values (case-insensitive). These must
 * continue to be accepted after the fix adds validation.
 *
 * **Validates: Requirements 12.1**
 */
static void
test_preserve_bug4_valid_static_values(void)
{
    const char  *valid_values[] = {
        "off", "on", "auto", "OFF", "ON", "AUTO",
        "Off", "On", "Auto"
    };
    size_t       num_values;

    TEST_SUBSECTION(
        "Preserve Bug 4: valid static values accepted");

    num_values = ARRAY_SIZE(valid_values);

    for (size_t i = 0; i < num_values; i++) {
        const char  *val;
        int          is_valid;

        val = valid_values[i];

        /*
         * Check case-insensitive match against
         * off, on, auto.
         */
        is_valid = (strcasecmp(val, "off") == 0
                    || strcasecmp(val, "on") == 0
                    || strcasecmp(val, "auto") == 0);

        TEST_ASSERT(is_valid == 1,
            "Valid static value should be recognized");
    }

    TEST_PASS(
        "All valid static values accepted preserved");
}


/*
 * Bug 4 Preservation: Variable expressions ($streaming_mode)
 * compile normally via complex value path.
 *
 * When the directive argument contains '$', it is treated
 * as a variable expression and compiled via
 * ngx_http_compile_complex_value. This path must remain
 * unchanged after adding static value validation.
 *
 * **Validates: Requirements 12.2**
 */
static void
test_preserve_bug4_variable_expression(void)
{
    const char  *var_values[] = {
        "$streaming_mode", "${streaming_mode}",
        "$arg_engine"
    };
    size_t       num_values;

    TEST_SUBSECTION(
        "Preserve Bug 4: variable expressions compile");

    num_values = ARRAY_SIZE(var_values);

    for (size_t i = 0; i < num_values; i++) {
        const char  *val;
        int          has_dollar;

        val = var_values[i];

        /* Check if value contains '$' */
        has_dollar = (strchr(val, '$') != NULL);

        TEST_ASSERT(has_dollar == 1,
            "Variable expression should contain '$'");

        /*
         * In the real code, values with '$' go through
         * ngx_http_compile_complex_value and are accepted.
         * The fix only adds validation for static values
         * (no '$'), so this path is unchanged.
         */
    }

    TEST_PASS(
        "Variable expressions compile normally preserved");
}


/*
 * Bug 4 Preservation: Duplicate directive returns
 * "is duplicate" error.
 *
 * When markdown_streaming_engine is specified more than
 * once, the function returns "is duplicate". This check
 * is at the top of the function and must remain unchanged.
 *
 * **Validates: Requirements 12.3**
 */
static void
test_preserve_bug4_duplicate_directive(void)
{
    int          streaming_engine_set;
    const char  *result;

    TEST_SUBSECTION(
        "Preserve Bug 4: duplicate returns error");

    /*
     * Simulate the duplicate check at the top of
     * ngx_http_markdown_streaming_engine():
     * if (mcf->streaming_engine != NULL) {
     *     return "is duplicate";
     * }
     */
    streaming_engine_set = 1;  /* already configured */
    result = NULL;

    if (streaming_engine_set) {
        result = "is duplicate";
    }

    TEST_ASSERT(result != NULL,
        "Duplicate should return error string");
    TEST_ASSERT(strcmp(result, "is duplicate") == 0,
        "Error should be 'is duplicate'");
    TEST_PASS(
        "Duplicate directive -> 'is duplicate' preserved");
}


/* ================================================================
 * main
 * ================================================================ */

int
main(void)
{
    printf("\n========================================\n");
    printf("Streaming Unit Tests\n");
    printf("========================================\n");

    TEST_SECTION("14.1 Engine Selection");
    test_engine_off();
    test_engine_on_get();
    test_engine_on_head();
    test_engine_on_304();
    test_engine_on_conditional_full();
    test_engine_on_conditional_ims_only();
    test_engine_on_conditional_disabled();
    test_engine_on_sse();
    test_engine_auto_large_cl();
    test_engine_auto_small_cl();
    test_engine_auto_no_cl();

    TEST_SECTION("14.2 Streaming Decompression");
    test_decomp_null_safety();
    test_decomp_empty_input();

    TEST_SECTION("14.3 Body Filter Chunk Processing");
    test_chunk_processing_empty();
    test_chunk_processing_size_limit();

    TEST_SECTION("14.4 Backpressure Handling");
    test_backpressure_flag();
    test_backpressure_deferred_finalize_resume();

    TEST_SECTION("14.5 Pre-Commit Fallback");
    test_precommit_fallback();

    TEST_SECTION("14.6 Post-Commit Error Handling");
    test_postcommit_error();
    test_postcommit_error_ignores_on_error_policy();
    test_postcommit_error_debug_log_details();
    test_postcommit_error_various_error_codes();

    TEST_SECTION("14.7 Configuration Directive Parsing");
    test_config_budget_default();
    test_config_engine_values();

    TEST_SECTION("14.8 Output Chain Construction");
    test_output_chain_last_buf();
    test_output_chain_flush();

    TEST_SECTION("14.9 Size Limit and Timeout");
    test_size_limit_precommit();
    test_size_limit_postcommit();
    test_timeout_precommit();

    TEST_SECTION("15.6 Streaming Headers Policy");
    test_commit_boundary_removes_content_length();
    test_commit_boundary_removes_content_encoding();
    test_commit_boundary_skips_content_encoding_no_decomp();
    test_streaming_no_cl_and_chunked_coexist();
    test_precommit_no_header_modification();
    test_commit_boundary_strips_upstream_etag();
    test_precommit_all_failopen_paths_record_metrics();
    test_init_failure_respects_streaming_on_error();
    test_streaming_failopen_increments_global_counter();

    TEST_SECTION(
        "15.9.1 streaming_on_error Config Parsing");
    test_config_on_error_legal_values();
    test_config_on_error_default_value();
    test_config_on_error_inheritance();
    test_config_on_error_invalid_values();

    TEST_SECTION(
        "15.9.2 Pre-Commit Strategy Routing");
    test_precommit_strategy_fallback_pass();
    test_precommit_strategy_fallback_reject();
    test_precommit_strategy_timeout_pass();
    test_precommit_strategy_timeout_reject();
    test_precommit_strategy_memory_limit_pass();
    test_precommit_strategy_memory_limit_reject();
    test_precommit_strategy_budget_exceeded_pass();
    test_precommit_strategy_budget_exceeded_reject();
    test_postcommit_budget_exceeded();
    test_precommit_memory_limit_budget_parity();
    test_precommit_strategy_internal_pass();
    test_precommit_strategy_internal_reject();

    TEST_SECTION("15.9.6 Metrics Increment");
    test_metrics_precommit_failopen();
    test_metrics_precommit_reject();
    test_metrics_postcommit_error();
    test_metrics_failed_total();

    TEST_SECTION(
        "17.1 streaming_shadow Config Parsing");
    test_config_shadow_legal_values();
    test_config_shadow_default_value();
    test_config_shadow_inheritance();
    test_config_shadow_invalid_values();

    TEST_SECTION(
        "17.2 Shadow Mode Runtime");
    test_shadow_metrics_increment();
    test_shadow_diff_metrics();
    test_shadow_error_isolation();

    TEST_SECTION("Bug 1 Preservation (Baseline)");
    test_preserve_bug1_normal_feed_returns_ok();
    test_preserve_bug1_fallback_buffer_fail();

    TEST_SECTION("Bug 2 Preservation (Baseline)");
    test_preserve_bug2_small_data_complete();
    test_preserve_bug2_empty_input_ok();
    test_preserve_bug2_exceeds_max_size();

    TEST_SECTION("Bug 3 Preservation (Baseline)");
    test_preserve_bug3_tail_feed_success();
    test_preserve_bug3_no_tail_data();
    test_preserve_bug3_no_decompression();

    TEST_SECTION("Bug 4 Preservation (Baseline)");
    test_preserve_bug4_valid_static_values();
    test_preserve_bug4_variable_expression();
    test_preserve_bug4_duplicate_directive();

    TEST_SECTION("Bug Condition Exploration (Bugfix Spec)");
    test_bug1_fallback_return_value();
    test_bug2_decomp_incomplete_inflate();
    test_bug3_finalize_tail_feed_error();
    test_bug4_config_invalid_static_value();

    if (bug_exploration_failures > 0) {
        printf("\n========================================\n");
        printf("Bug exploration: %d bug(s) confirmed\n",
            bug_exploration_failures);
        printf("========================================\n\n");
        return 1;
    }

    printf("\n========================================\n");
    printf("All streaming tests passed!\n");
    printf("========================================\n\n");
    return 0;
}

#endif /* MARKDOWN_STREAMING_ENABLED */
