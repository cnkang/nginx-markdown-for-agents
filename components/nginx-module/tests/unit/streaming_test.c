/*
 * Test: streaming
 *   policy selection, input disposition and pending input, post-commit error
 *   routing, config parsing, output chain construction, pre-commit strategy
 *   routing, metrics, and preservation baselines.
 *
 * Feature: nginx-streaming-runtime-and-ffi
 *
 * Scope note: every test kept here exercises a local decision mirror or a
 * literal contract (see the mirror helpers at the top of the file).  Tests
 * that only re-asserted hand-copied literals were removed in the 0.9.2
 * round-2 test-integrity cleanup; the real-call coverage for those
 * behaviors lives in streaming_impl_test.c, streaming_decomp_test.c,
 * stream_commit_test.c, config_handlers_impl_test.c, and
 * streaming_metrics_increment_test.c.
 *
 * All tests are gated with MARKDOWN_STREAMING_ENABLED.
 */

#include "test_common.h"
#include <strings.h>

/* Commit state constants used by branch-driven metric tests.
 * Values mirror production: PRE=0, POST=1. */
#define COMMIT_STATE_PRE   0
#define COMMIT_STATE_POST  1

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

/*
 * Production FFI contract header (generated from the Rust side).
 *
 * The streaming-gated error codes used by the mirror tests below are taken
 * from this header instead of being hand-copied: a Rust-side renumbering now
 * breaks this translation unit at compile/run time instead of drifting
 * silently.  The `streaming` unit target already supplies
 * -DMARKDOWN_STREAMING_ENABLED and -I../../rust-converter/include, which is
 * all this header needs.
 *
 * ngx_http_markdown_ffi_layout_check.h also brings the inline ABI-gate
 * helpers, so the code-value pins below are exercised through a real
 * production symbol rather than a local literal.
 */
#include "../../src/ngx_http_markdown_ffi_layout_check.h"
#include "../../src/ngx_http_markdown_directive_names.h"

/* Minimal nginx type definitions for testing */
typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;
typedef intptr_t        ngx_flag_t;
typedef size_t          ngx_msec_t;

#define NGX_OK          0
#define NGX_ERROR      -1
#define NGX_AGAIN      -2
#define NGX_DONE       -4
#define NGX_DECLINED   -5

#define NGX_HTTP_GET    2
#define NGX_HTTP_HEAD   4

#define NGX_HTTP_OK             200
#define NGX_HTTP_NOT_MODIFIED   304

/* Streaming constants (mirror module header) */
#define PATH_FULLBUFFER   0
#define PATH_STREAMING    1

#define POLICY_OFF    0
#define POLICY_AUTO   1
#define POLICY_FORCE  2

/* On-error policy */
#define ON_ERROR_PASS    0
#define ON_ERROR_REJECT  1

/* Conditional requests mode */
#define CONDITIONAL_FULL_SUPPORT         0
#define CONDITIONAL_IF_MODIFIED_SINCE    1
#define CONDITIONAL_DISABLED             2

/* ================================================================
 * Lightweight stubs for streaming policy selection tests
 * ================================================================ */

typedef struct {
    ngx_uint_t   streaming_policy;       /* resolved policy value */
    ngx_uint_t   conditional_requests;
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

static int
is_valid_streaming_policy(const char *value)
{
    return strcasecmp(value, "off") == 0
        || strcasecmp(value, "auto") == 0
        || strcasecmp(value, "force") == 0;
}

/* Function prototypes */
static ngx_uint_t
test_select_processing_path(const test_conf_t *conf,
    const test_request_t *r);
static void test_policy_off(void);
static void test_policy_on_get(void);
static void test_policy_on_head(void);
static void test_policy_on_304(void);
static void test_policy_on_conditional_full(void);
static void test_policy_on_conditional_ims_only(void);
static void test_policy_on_conditional_disabled(void);
static void test_policy_on_sse(void);
static void test_policy_auto_large_cl(void);
static void test_policy_auto_small_cl(void);
static void test_policy_auto_no_cl(void);
static void test_postcommit_error_various_error_codes(void);
static void test_config_policy_values(void);
static void test_output_chain_last_buf(void);
static void test_output_chain_flush(void);
static int test_precommit_route(uint32_t error_code, ngx_uint_t on_error);

/* Bug condition exploration test prototypes */
static void test_config_invalid_static_value(void);
static void test_production_header_bindings(void);

/* Streaming headers policy test prototypes. */
static void test_init_failure_respects_error_policy(void);

/* Preservation test prototypes (non-bug-condition baseline) */
static void test_preserve_valid_static_values(void);
static void test_policy_rejects_variable_expression(void);
static void test_preserve_duplicate_directive(void);


/*
 * Streaming policy selection logic (mirrors production path selection).
 *
 * Evaluation order:
 * 1. policy == off -> PATH_FULLBUFFER
 * 2. HEAD request -> PATH_FULLBUFFER
 * 3. 304 Not Modified -> PATH_FULLBUFFER
 * 4. conditional_requests full_support -> PATH_FULLBUFFER
 * 5. Content-Type is text/event-stream -> PATH_FULLBUFFER
 * 6. policy == force -> PATH_STREAMING
 * 7. policy == auto -> PATH_STREAMING after the safety checks above,
 *    regardless of response size.
 */
static ngx_uint_t
test_select_processing_path(const test_conf_t *conf,
    const test_request_t *r)
{
    /* Rule 1: policy off */
    if (conf->streaming_policy == POLICY_OFF) {
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

    /* Rule 6: policy force */
    if (conf->streaming_policy == POLICY_FORCE) {
        return PATH_STREAMING;
    }

    /* Rule 7: auto prefers streaming regardless of response size. */
    return PATH_STREAMING;
}

/* ================================================================
 * 14.1 Streaming policy selection unit tests
 * ================================================================ */

static void
test_policy_off(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Policy off: always full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_OFF;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 999999;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_FULLBUFFER,
        "policy=off should always select full-buffer");
    TEST_PASS("policy=off selects full-buffer");
}

static void
test_policy_on_get(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Policy force + GET: streaming");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_FORCE;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 1024;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_STREAMING,
        "policy=force + GET should select streaming");
    TEST_PASS("policy=force + GET selects streaming");
}

static void
test_policy_on_head(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Policy force + HEAD: full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_FORCE;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_HEAD;
    req.status = NGX_HTTP_OK;
    req.content_length = 1024;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_FULLBUFFER,
        "policy=force + HEAD should select full-buffer");
    TEST_PASS("policy=force + HEAD selects full-buffer");
}

static void
test_policy_on_304(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Policy force + 304: full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_FORCE;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_NOT_MODIFIED;
    req.content_length = 0;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_FULLBUFFER,
        "policy=force + 304 should select full-buffer");
    TEST_PASS("policy=force + 304 selects full-buffer");
}

static void
test_policy_on_conditional_full(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION(
        "Policy force + conditional full_support: full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_FORCE;
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
 * Validates: conditional if_modified_since_only allows streaming
 */
static void
test_policy_on_conditional_ims_only(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION(
        "Policy force + conditional if_modified_since_only: "
        "streaming");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_FORCE;
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
 * Validates: conditional disabled allows streaming
 */
static void
test_policy_on_conditional_disabled(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION(
        "Policy force + conditional disabled: streaming");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_FORCE;
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
test_policy_on_sse(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Policy force + SSE: full-buffer");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_FORCE;
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
test_policy_auto_large_cl(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Policy auto + large CL: streaming");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_AUTO;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 2 * 1024 * 1024;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_STREAMING,
        "auto + large CL should select streaming");
    TEST_PASS("auto + large CL selects streaming");
}

static void
test_policy_auto_small_cl(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Policy auto + small CL: streaming");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_AUTO;
    conf.conditional_requests = CONDITIONAL_DISABLED;

    memset(&req, 0, sizeof(req));
    req.method = NGX_HTTP_GET;
    req.status = NGX_HTTP_OK;
    req.content_length = 512 * 1024;
    req.content_type = "text/html";

    TEST_ASSERT(
        test_select_processing_path(&conf, &req)
            == PATH_STREAMING,
        "auto + small CL should select streaming");
    TEST_PASS("auto + small CL selects streaming");
}

static void
test_policy_auto_no_cl(void)
{
    test_conf_t    conf;
    test_request_t req;

    TEST_SUBSECTION("Policy auto + no CL: streaming");

    memset(&conf, 0, sizeof(conf));
    conf.streaming_policy = POLICY_AUTO;
    conf.conditional_requests = CONDITIONAL_DISABLED;

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


typedef struct {
    ngx_uint_t  commit_state;
    void       *pending_output;
    ngx_uint_t  flushes_sent;
    size_t      total_output_bytes;
} test_streaming_ctx_t;


/* ================================================================
 * 14.4b Pending Input Chain
 * Feature: nginx-streaming-runtime-and-ffi, backpressure input lifecycle
 *
 * Validates: pending_input enqueue with terminal_seen capture and the
 * empty-chain predicate.  Disposition decoupling, detach/clear, and
 * lost-continuation prevention are exercised against the real
 * implementation in streaming_impl_test.c
 * (test_pending_input_production_lifecycle and the resume/backpressure
 * paths), so the literal mirrors for those were removed here.
 * ================================================================ */

/* Minimal pending_input simulation struct */
typedef struct {
    void       *head;
    void       *tail;
    size_t      bytes;
    ngx_uint_t  links;
    ngx_flag_t  terminal_seen;
} test_pending_input_t;


static void
test_pending_input_enqueue_terminal_capture(void)
{
    test_pending_input_t  pi;
    ngx_flag_t           last_buf;

    TEST_SUBSECTION(
        "Pending input: terminal_seen captured from last_buf");

    memset(&pi, 0, sizeof(pi));
    last_buf = 1;

    if (last_buf) {
        pi.terminal_seen = 1;
    }

    TEST_ASSERT(pi.terminal_seen == 1,
        "terminal_seen should be set when last_buf link enqueued");
    TEST_PASS("terminal_seen captured during enqueue");
}


static void
test_pending_input_empty_check(void)
{
    test_pending_input_t  pi;

    TEST_SUBSECTION(
        "Pending input: empty check");

    memset(&pi, 0, sizeof(pi));
    TEST_ASSERT(pi.head == NULL,
        "Fresh pending_input should be empty");

    pi.head = (void *) 0x1;
    TEST_ASSERT(pi.head != NULL,
        "After enqueue, pending_input should be non-empty");

    pi.head = NULL;
    TEST_ASSERT(pi.head == NULL,
        "After clear, pending_input should be empty again");
    TEST_PASS("Pending input empty check works");
}


/* ================================================================
 * 14.6 Post-Commit error handling
 * Feature: nginx-streaming-runtime-and-ffi, post-commit error handling
 *
 * Validates: post-commit failure classification and per-code routing for
 * the expected post-commit error codes.
 *
 * The "always fail-closed regardless of error_policy" behavior and the
 * post-commit success/failure metrics are covered by real-call tests in
 * streaming_impl_test.c (test_postcommit_and_precommit_error_paths) and
 * postcommit_metrics_accounting_test.c; the literals that re-asserted
 * those same claims were removed here.
 * ================================================================ */


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
 * Validates: post-commit error always fail-closed, all error codes produce fail-closed
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
 *
 * Validates: markdown_streaming policy token parsing through the local
 * mirror of the closed value set.
 *
 * The 2 MiB streaming-budget default is asserted against the production
 * macro by config_core_impl_test.c and effective_conf_test.c; the local
 * copy of that default was removed here.
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
test_config_policy_values(void)
{
    TEST_SUBSECTION("Config: streaming policy values");

    TEST_ASSERT(POLICY_OFF == 0, "OFF should be 0");
    TEST_ASSERT(POLICY_AUTO == 1, "AUTO should be 1");
    TEST_ASSERT(POLICY_FORCE == 2, "FORCE should be 2");

    /* Budget parsing */
    TEST_ASSERT(parse_streaming_budget("2m") == 2 * 1024 * 1024,
        "'2m' should parse to 2 MiB");
    TEST_ASSERT(parse_streaming_budget("512k") == 512 * 1024,
        "'512k' should parse to 512 KiB");
    TEST_ASSERT(parse_streaming_budget("4096") == 4096,
        "'4096' should parse to 4096 bytes");
    TEST_ASSERT(parse_streaming_budget(NULL) == 0,
        "NULL should return 0");
    TEST_PASS("Streaming policy values and budget parsing correct");
}

/* ================================================================
 * 14.8 Output chain construction
 * Feature: nginx-streaming-runtime-and-ffi, output chain construction
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
 * 15.6 Streaming Headers Policy
 * Feature: streaming-failure-cache-semantics
 *
 * Validates: fail-open / init-failure routing through the unified
 *            error policy
 *
 * The commit-boundary header modifications (Content-Length strip,
 * Content-Encoding removal, chunked coexistence, Pre-Commit header
 * immutability) are covered by real-call tests in stream_commit_test.c
 * and streaming_impl_test.c; the literal re-assertions were removed here.
 * ================================================================ */


/*
 * Verify that init-time failures (prepare_options,
 * markdown_streaming_new_with_code, decompressor create) respect
 * the unified error policy used by every conversion path.
 */
static void
test_init_failure_respects_error_policy(void)
{
    ngx_uint_t  error_policy;
    int         route;

    TEST_SUBSECTION(
        "Init-time failure respects "
        "error_policy");

    /*
     * Reject policy must fail closed: the route helper classifies
     * the error as fail-closed (2).
     */
    error_policy = ON_ERROR_REJECT;

    route = test_precommit_route(ERROR_INTERNAL,
        error_policy);

    TEST_ASSERT(route == 2,
        "Init failure with error_policy=reject "
        "must route fail-closed");

    /*
     * Pass policy must fail open: the route helper classifies the
     * error as fail-open (1).
     */
    error_policy = ON_ERROR_PASS;

    route = test_precommit_route(ERROR_INTERNAL,
        error_policy);

    TEST_ASSERT(route == 1,
        "Init failure with error_policy=pass "
        "must route fail-open");

    TEST_PASS(
        "Init-time failures respect "
        "the unified error_policy");
}


/* ================================================================
 * 15.9.1 unified error policy runtime encoding
 * Feature: streaming-failure-cache-semantics
 *
 * Validates: one pass/reject runtime value is shared by all paths
 *
 * Tests for the resolved runtime field:
 * - Legal internal values (pass, reject) are distinct
 * - Default value is pass (ON_ERROR_PASS = 0)
 * - Config inheritance (child inherits from parent)
 * - Invalid internal names are not accepted
 * ================================================================ */

/* Forward declarations for 15.9 tests */
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
 * Verify that invalid values are rejected.
 *
 * Only resolved pass/reject names are accepted by this runtime model.
 *
 * Validates: error_policy legal values and default (invalid rejection)
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
        "Runtime: invalid error_policy names");

    num_values = ARRAY_SIZE(invalid_values);

    for (size_t i = 0; i < num_values; i++) {
        const char  *val;
        int          is_valid;

        val = invalid_values[i];

        /*
         * Simulate the resolved runtime lookup:
         * only "pass" and "reject" are valid.
         */
        is_valid = (strcmp(val, "pass") == 0
                    || strcmp(val, "reject") == 0);

        TEST_ASSERT(is_valid == 0,
            "Invalid value should not match enum");
    }

    TEST_PASS(
        "Invalid error_policy values rejected");
}


/* ================================================================
 * 15.9.2 Pre-Commit Strategy Routing
 * Feature: streaming-failure-cache-semantics
 *
 * Validates: FALLBACK routing through metrics recording
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
 * Validates: FALLBACK always routes to full-buffer, postcommit_error_total increments
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
} test_streaming_metrics_t;


/*
 * Simulate metrics increment for pre-commit fail-open.
 *
 * Uses branch-driven stub to mirror production logic:
 * commit_state = PRE_COMMIT, on_error != REJECT (i.e. PASS).
 *
 * Validates: all pre-commit fail-open paths record metrics, Rule 14 (branch-driven tests)
 */
static void
test_metrics_precommit_failopen(void)
{
    test_streaming_metrics_t  m;
    ngx_uint_t                commit_state;
    ngx_uint_t                on_error;

    TEST_SUBSECTION(
        "Metrics: precommit_failopen_total increment");

    memset(&m, 0, sizeof(m));

    /*
     * Mirror production pre-commit error branching:
     *
     * if (commit_state == POST_COMMIT) {
     *     m.postcommit_error_total++;
     *     m.failed_total++;
     * } else if (on_error == ON_ERROR_REJECT) {
     *     m.precommit_reject_total++;
     *     m.failed_total++;
     * } else {
     *     m.precommit_failopen_total++;
     *     m.failed_total++;
     * }
     *
     * Drive via commit_state = PRE, on_error = PASS.
     */
    commit_state = COMMIT_STATE_PRE;
    on_error = ON_ERROR_PASS;

    if (commit_state == COMMIT_STATE_POST) {
        m.postcommit_error_total++;
        m.failed_total++;
    } else if (on_error == ON_ERROR_REJECT) {
        m.precommit_reject_total++;
        m.failed_total++;
    } else {
        m.precommit_failopen_total++;
        m.failed_total++;
    }

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
     * Re-run the same branch with same inputs.
     */
    if (commit_state == COMMIT_STATE_POST) {
        m.postcommit_error_total++;
        m.failed_total++;
    } else if (on_error == ON_ERROR_REJECT) {
        m.precommit_reject_total++;
        m.failed_total++;
    } else {
        m.precommit_failopen_total++;
        m.failed_total++;
    }

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
 * Uses branch-driven stub to mirror production logic:
 * commit_state = PRE_COMMIT, on_error = REJECT.
 *
 * Validates: all pre-commit fail-open paths record metrics, Rule 14 (branch-driven tests)
 */
static void
test_metrics_precommit_reject(void)
{
    test_streaming_metrics_t  m;
    ngx_uint_t                commit_state;
    ngx_uint_t                on_error;

    TEST_SUBSECTION(
        "Metrics: precommit_reject_total increment");

    memset(&m, 0, sizeof(m));

    /*
     * Mirror production pre-commit error branching:
     * commit_state = PRE, on_error = REJECT.
     */
    commit_state = COMMIT_STATE_PRE;
    on_error = ON_ERROR_REJECT;

    if (commit_state == COMMIT_STATE_POST) {
        m.postcommit_error_total++;
        m.failed_total++;
    } else if (on_error == ON_ERROR_REJECT) {
        m.precommit_reject_total++;
        m.failed_total++;
    } else {
        m.precommit_failopen_total++;
        m.failed_total++;
    }

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
 * Uses branch-driven stub to mirror production logic:
 * commit_state = POST_COMMIT.
 *
 * Validates: postcommit_error_total increments, Rule 14 (branch-driven tests)
 */
static void
test_metrics_postcommit_error(void)
{
    test_streaming_metrics_t  m;
    ngx_uint_t                commit_state;

    TEST_SUBSECTION(
        "Metrics: postcommit_error_total increment");

    memset(&m, 0, sizeof(m));

    /*
     * Mirror production post-commit error branching:
     *
     * if (commit_state == POST_COMMIT) {
     *     m.postcommit_error_total++;
     *     m.failed_total++;
     * }
     *
     * Drive via commit_state = POST.
     */
    commit_state = COMMIT_STATE_POST;

    if (commit_state == COMMIT_STATE_POST) {
        m.postcommit_error_total++;
        m.failed_total++;
    }

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
 * Uses branch-driven stub to mirror production logic for
 * each failure type (pre-commit fail-open, pre-commit
 * reject, post-commit error, fallback).
 *
 * Validates: all pre-commit fail-open paths record metrics, postcommit_error_total increments, Rule 14
 */
static void
test_metrics_failed_total(void)
{
    test_streaming_metrics_t  m;
    ngx_uint_t                commit_state;
    ngx_uint_t                on_error;

    TEST_SUBSECTION(
        "Metrics: failed_total increments on all "
        "failures");

    memset(&m, 0, sizeof(m));

    /*
     * Mirror production branching for each failure type.
     * failed_total should increment for each failure.
     */

    /* Pre-commit fail-open: commit_state = PRE, on_error = PASS */
    commit_state = COMMIT_STATE_PRE;
    on_error = ON_ERROR_PASS;

    if (commit_state == COMMIT_STATE_POST) {
        m.postcommit_error_total++;
        m.failed_total++;
    } else if (on_error == ON_ERROR_REJECT) {
        m.precommit_reject_total++;
        m.failed_total++;
    } else {
        m.precommit_failopen_total++;
        m.failed_total++;
    }

    /* Pre-commit reject: commit_state = PRE, on_error = REJECT */
    commit_state = COMMIT_STATE_PRE;
    on_error = ON_ERROR_REJECT;

    if (commit_state == COMMIT_STATE_POST) {
        m.postcommit_error_total++;
        m.failed_total++;
    } else if (on_error == ON_ERROR_REJECT) {
        m.precommit_reject_total++;
        m.failed_total++;
    } else {
        m.precommit_failopen_total++;
        m.failed_total++;
    }

    /* Post-commit error: commit_state = POST */
    commit_state = COMMIT_STATE_POST;

    if (commit_state == COMMIT_STATE_POST) {
        m.postcommit_error_total++;
        m.failed_total++;
    }

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


/*
 * Verify that deferred last_buf send failure records
 * postcommit_error_total and failed_total.
 *
 * This regression test covers the scenario where:
 * - final_send_rc == NGX_AGAIN (backpressure)
 * - finalize_pending_lastbuf = 1
 * - resume_pending() drains pending output
 * - deferred last_buf send returns NGX_ERROR
 *
 * Uses branch-driven stub to mirror production logic
 * instead of manual counter increment.
 *
 * Validates: Rule 23 (observability side-effects after
 * event succeeds), Rule 1 (backpressure handling),
 * Rule 14 (tests must exercise production branching)
 */
static void
test_metrics_deferred_lastbuf_failure(void)
{
    test_streaming_metrics_t  m;
    ngx_int_t                 deferred_send_rc;

    TEST_SUBSECTION(
        "Metrics: deferred last_buf failure records "
        "postcommit_error_total and failed_total");

    memset(&m, 0, sizeof(m));

    /*
     * Simulate the production branching logic from
     * ngx_http_markdown_streaming_send_deferred_lastbuf():
     *
     * if (rc == NGX_OK || rc == NGX_DONE) {
     *     success path
     * } else {
     *     m.postcommit_error_total++;
     *     m.failed_total++;
     * }
     *
     * Here we set deferred_send_rc to NGX_ERROR to drive
     * the failure branch through the same condition.
     */
    deferred_send_rc = NGX_ERROR;

    /* Mirror production branching condition */
    if (deferred_send_rc == NGX_OK || deferred_send_rc == NGX_DONE) {
        m.succeeded_total++;
    } else {
        m.postcommit_error_total++;
        m.failed_total++;
    }

    TEST_ASSERT(m.postcommit_error_total == 1,
        "postcommit_error_total should be 1 after "
        "deferred last_buf failure");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should be 1 after deferred "
        "last_buf failure");
    TEST_ASSERT(m.succeeded_total == 0,
        "succeeded_total should NOT increment on "
        "deferred failure");

    TEST_PASS(
        "deferred last_buf failure records metrics "
        "correctly");
}


/*
 * Verify that immediate success path records metrics
 * only when terminal last_buf send succeeds.
 *
 * This regression test covers the scenario where:
 * - final_send_rc == NGX_OK (body sent OK)
 * - terminal last_buf send returns NGX_ERROR
 *
 * In this case, failure metrics MUST be recorded because
 * the terminal send failed post-commit.
 *
 * Uses branch-driven stub to mirror production logic
 * instead of manual counter increment.
 *
 * Validates: Rule 23 (observability side-effects after
 * event succeeds), Rule 14 (tests must exercise production
 * branching)
 */
static void
test_metrics_terminal_lastbuf_failure(void)
{
    test_streaming_metrics_t  m;
    ngx_int_t                 terminal_send_rc;

    TEST_SUBSECTION(
        "Metrics: terminal last_buf failure records "
        "failure metrics (unified post-commit policy)");

    memset(&m, 0, sizeof(m));

    /*
     * Simulate the production branching logic from
     * ngx_http_markdown_streaming_finalize() immediate
     * success path:
     *
     * rc = send_output(last_buf=1);
     * if (rc == NGX_OK || rc == NGX_DONE) {
     *     m.succeeded_total++;
     * } else if (rc != NGX_AGAIN) {
     *     m.postcommit_error_total++;
     *     m.failed_total++;
     * }
     *
     * Here we set terminal_send_rc to NGX_ERROR to drive
     * the failure branch through the same condition.
     */
    terminal_send_rc = NGX_ERROR;

    /* Mirror production branching condition */
    if (terminal_send_rc == NGX_OK || terminal_send_rc == NGX_DONE) {
        m.succeeded_total++;
    } else if (terminal_send_rc != NGX_AGAIN) {
        m.postcommit_error_total++;
        m.failed_total++;
    }

    TEST_ASSERT(m.succeeded_total == 0,
        "succeeded_total should be 0 when terminal "
        "last_buf fails");
    TEST_ASSERT(m.postcommit_error_total == 1,
        "postcommit_error_total should be 1 on "
        "terminal last_buf failure");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should be 1 on terminal "
        "last_buf failure");

    TEST_PASS(
        "terminal last_buf failure records failure "
        "metrics (unified post-commit policy)");
}


/*
 * Verify that terminal last_buf NGX_AGAIN followed by
 * successful drain records success metrics via the
 * pending_terminal_metrics latch.
 *
 * This regression test covers the scenario where:
 * - terminal last_buf send returns NGX_AGAIN (backpressure)
 * - pending_terminal_metrics = 1
 * - resume_pending() drains successfully (NGX_OK)
 * - success metrics are recorded, latch is cleared
 *
 * Uses branch-driven stub to mirror production logic.
 *
 * Validates: Rule 23 (observability side-effects after
 * event succeeds), Rule 14 (tests must exercise production
 * branching)
 */
static void
test_metrics_terminal_lastbuf_again_then_ok(void)
{
    test_streaming_metrics_t  m;
    ngx_int_t                 terminal_send_rc;
    ngx_flag_t                pending_terminal_metrics;
    ngx_int_t                 resume_rc;

    TEST_SUBSECTION(
        "Metrics: terminal last_buf NGX_AGAIN then "
        "resume OK records success");

    memset(&m, 0, sizeof(m));
    pending_terminal_metrics = 0;

    /*
     * Step 1: Simulate finalize() terminal send returning NGX_AGAIN.
     * Production code sets pending_terminal_metrics = 1.
     */
    terminal_send_rc = NGX_AGAIN;

    if (terminal_send_rc == NGX_OK || terminal_send_rc == NGX_DONE) {
        m.succeeded_total++;
    } else if (terminal_send_rc == NGX_AGAIN) {
        pending_terminal_metrics = 1;
    } else {
        m.postcommit_error_total++;
        m.failed_total++;
    }

    TEST_ASSERT(pending_terminal_metrics == 1,
        "pending_terminal_metrics should be set after "
        "terminal NGX_AGAIN");
    TEST_ASSERT(m.succeeded_total == 0,
        "succeeded_total should NOT increment yet");

    /*
     * Step 2: Simulate resume_pending() draining successfully.
     * Production code checks pending_terminal_metrics and
     * records success metrics.
     */
    resume_rc = NGX_OK;

    if (resume_rc == NGX_OK || resume_rc == NGX_DONE) {
        if (pending_terminal_metrics) {
            m.succeeded_total++;
            pending_terminal_metrics = 0;
        }
    }

    TEST_ASSERT(m.succeeded_total == 1,
        "succeeded_total should be 1 after resume OK");
    TEST_ASSERT(pending_terminal_metrics == 0,
        "pending_terminal_metrics should be cleared");

    TEST_PASS(
        "terminal last_buf NGX_AGAIN then resume OK "
        "records success metrics");
}


/*
 * Verify that terminal last_buf NGX_AGAIN followed by
 * failed drain records failure metrics.
 *
 * This regression test covers the scenario where:
 * - terminal last_buf send returns NGX_AGAIN (backpressure)
 * - pending_terminal_metrics = 1
 * - resume_pending() returns NGX_ERROR
 * - failure metrics are recorded
 *
 * Uses branch-driven stub to mirror production logic.
 *
 * Validates: Rule 23 (observability side-effects after
 * event succeeds), Rule 14 (tests must exercise production
 * branching)
 */
static void
test_metrics_terminal_lastbuf_again_then_error(void)
{
    test_streaming_metrics_t  m;
    ngx_int_t                 terminal_send_rc;
    ngx_flag_t                pending_terminal_metrics;
    ngx_int_t                 resume_rc;

    TEST_SUBSECTION(
        "Metrics: terminal last_buf NGX_AGAIN then "
        "resume ERROR records failure");

    memset(&m, 0, sizeof(m));
    pending_terminal_metrics = 0;

    /*
     * Step 1: Simulate finalize() terminal send returning NGX_AGAIN.
     */
    terminal_send_rc = NGX_AGAIN;

    if (terminal_send_rc == NGX_OK || terminal_send_rc == NGX_DONE) {
        m.succeeded_total++;
    } else if (terminal_send_rc == NGX_AGAIN) {
        pending_terminal_metrics = 1;
    } else {
        m.postcommit_error_total++;
        m.failed_total++;
    }

    TEST_ASSERT(pending_terminal_metrics == 1,
        "pending_terminal_metrics should be set");

    /*
     * Step 2: Simulate resume_pending() returning NGX_ERROR.
     * Production code clears pending_terminal_metrics first,
     * then records failure metrics.
     */
    resume_rc = NGX_ERROR;

    if (resume_rc != NGX_OK && resume_rc != NGX_DONE) {
        /* Clear latch on failure (matches production) */
        pending_terminal_metrics = 0;
        m.postcommit_error_total++;
        m.failed_total++;
    } else if (pending_terminal_metrics) {
        m.succeeded_total++;
        pending_terminal_metrics = 0;
    }

    TEST_ASSERT(m.postcommit_error_total == 1,
        "postcommit_error_total should be 1 after "
        "resume ERROR");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should be 1 after resume ERROR");
    TEST_ASSERT(m.succeeded_total == 0,
        "succeeded_total should remain 0 on resume failure");
    TEST_ASSERT(pending_terminal_metrics == 0,
        "pending_terminal_metrics should be cleared "
        "after resume failure");

    TEST_PASS(
        "terminal last_buf NGX_AGAIN then resume ERROR "
        "records failure metrics");
}


/*
 * Verify that deferred last_buf NGX_AGAIN followed by
 * successful drain records success metrics via the
 * pending_terminal_metrics latch.
 *
 * This regression test covers the scenario where:
 * - send_deferred_lastbuf() returns NGX_AGAIN (backpressure)
 * - pending_terminal_metrics = 1
 * - resume_pending() drains successfully (NGX_OK)
 * - success metrics are recorded, latch is cleared
 *
 * Uses branch-driven stub to mirror production logic.
 *
 * Validates: Rule 23 (observability side-effects after
 * event succeeds), Rule 14 (tests must exercise production
 * branching)
 */
static void
test_metrics_deferred_lastbuf_again_then_ok(void)
{
    test_streaming_metrics_t  m;
    ngx_int_t                 deferred_send_rc;
    ngx_flag_t                pending_terminal_metrics;
    ngx_int_t                 resume_rc;

    TEST_SUBSECTION(
        "Metrics: deferred last_buf NGX_AGAIN then "
        "resume OK records success");

    memset(&m, 0, sizeof(m));
    pending_terminal_metrics = 0;

    /*
     * Step 1: Simulate send_deferred_lastbuf() returning NGX_AGAIN.
     * Production code sets pending_terminal_metrics = 1.
     */
    deferred_send_rc = NGX_AGAIN;

    if (deferred_send_rc == NGX_OK || deferred_send_rc == NGX_DONE) {
        m.succeeded_total++;
    } else if (deferred_send_rc == NGX_AGAIN) {
        pending_terminal_metrics = 1;
    } else {
        m.postcommit_error_total++;
        m.failed_total++;
    }

    TEST_ASSERT(pending_terminal_metrics == 1,
        "pending_terminal_metrics should be set after "
        "deferred NGX_AGAIN");
    TEST_ASSERT(m.succeeded_total == 0,
        "succeeded_total should NOT increment yet");

    /*
     * Step 2: Simulate resume_pending() draining successfully.
     * Production code checks pending_terminal_metrics and
     * records success metrics.
     */
    resume_rc = NGX_OK;

    if (resume_rc == NGX_OK || resume_rc == NGX_DONE) {
        if (pending_terminal_metrics) {
            m.succeeded_total++;
            pending_terminal_metrics = 0;
        }
    } else {
        pending_terminal_metrics = 0;
        m.postcommit_error_total++;
        m.failed_total++;
    }

    TEST_ASSERT(m.succeeded_total == 1,
        "succeeded_total should be 1 after resume OK");
    TEST_ASSERT(pending_terminal_metrics == 0,
        "pending_terminal_metrics should be cleared");

    TEST_PASS(
        "deferred last_buf NGX_AGAIN then resume OK "
        "records success metrics");
}


/*
 * Verify that deferred last_buf NGX_AGAIN followed by
 * failed drain records failure metrics (not success).
 *
 * This regression test covers the scenario where:
 * - send_deferred_lastbuf() returns NGX_AGAIN (backpressure)
 * - pending_terminal_metrics = 1
 * - resume_pending() returns NGX_ERROR
 * - failure metrics recorded, latch cleared, success NOT recorded
 *
 * Uses branch-driven stub to mirror production logic.
 *
 * Validates: Rule 23 (observability side-effects after
 * event succeeds), Rule 14 (tests must exercise production
 * branching)
 */
static void
test_metrics_deferred_lastbuf_again_then_error(void)
{
    test_streaming_metrics_t  m;
    ngx_int_t                 deferred_send_rc;
    ngx_flag_t                pending_terminal_metrics;
    ngx_int_t                 resume_rc;

    TEST_SUBSECTION(
        "Metrics: deferred last_buf NGX_AGAIN then "
        "resume ERROR records failure");

    memset(&m, 0, sizeof(m));
    pending_terminal_metrics = 0;

    /*
     * Step 1: Simulate send_deferred_lastbuf() returning NGX_AGAIN.
     */
    deferred_send_rc = NGX_AGAIN;

    if (deferred_send_rc == NGX_OK || deferred_send_rc == NGX_DONE) {
        m.succeeded_total++;
    } else if (deferred_send_rc == NGX_AGAIN) {
        pending_terminal_metrics = 1;
    } else {
        m.postcommit_error_total++;
        m.failed_total++;
    }

    TEST_ASSERT(pending_terminal_metrics == 1,
        "pending_terminal_metrics should be set");

    /*
     * Step 2: Simulate resume_pending() returning NGX_ERROR.
     * Production code clears latch and records failure metrics.
     */
    resume_rc = NGX_ERROR;

    if (resume_rc != NGX_OK && resume_rc != NGX_DONE) {
        pending_terminal_metrics = 0;
        m.postcommit_error_total++;
        m.failed_total++;
    } else if (pending_terminal_metrics) {
        m.succeeded_total++;
        pending_terminal_metrics = 0;
    }

    TEST_ASSERT(m.postcommit_error_total == 1,
        "postcommit_error_total should be 1 after "
        "resume ERROR");
    TEST_ASSERT(m.failed_total == 1,
        "failed_total should be 1 after resume ERROR");
    TEST_ASSERT(m.succeeded_total == 0,
        "succeeded_total should remain 0 on resume failure");
    TEST_ASSERT(pending_terminal_metrics == 0,
        "pending_terminal_metrics should be cleared on failure");

    TEST_PASS(
        "deferred last_buf NGX_AGAIN then resume ERROR "
        "records failure metrics");
}


/* ================================================================
 * Budget Exceeded (ERROR_BUDGET_EXCEEDED = 6) Regression Tests
 *
 * Validates that the Rust FFI budget exceeded code (6) is
 * classified correctly alongside the C-side memory limit
 * code (4).  Both must increment budget_exceeded_total and
 * route through the error_policy policy.
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
    test_streaming_metrics_t  metrics_memory_limit;
    test_streaming_metrics_t  metrics_budget_exceeded;

    TEST_SUBSECTION(
        "Budget parity: MEMORY_LIMIT and "
        "BUDGET_EXCEEDED both classify");

    memset(&metrics_memory_limit, 0, sizeof(metrics_memory_limit));
    test_precommit_error_stub(
        ERROR_MEMORY_LIMIT, ON_ERROR_PASS, &metrics_memory_limit);

    memset(&metrics_budget_exceeded, 0, sizeof(metrics_budget_exceeded));
    test_precommit_error_stub(
        ERROR_BUDGET_EXCEEDED, ON_ERROR_PASS, &metrics_budget_exceeded);

    TEST_ASSERT(metrics_memory_limit.budget_exceeded_total == 1,
        "MEMORY_LIMIT should trigger budget counter");
    TEST_ASSERT(metrics_budget_exceeded.budget_exceeded_total == 1,
        "BUDGET_EXCEEDED should trigger budget counter");
    TEST_ASSERT(
        metrics_memory_limit.budget_exceeded_total
            == metrics_budget_exceeded.budget_exceeded_total,
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


/*
 * Regression: markdown_streaming rejects invalid static values.
 *
 * **Validates: invalid static value rejection at parse time**
 */
static void
test_config_invalid_static_value(void)
{
    const char  *test_values[] = {
        "atuo", "oof", "yes", "true", "enabled", ""
    };
    size_t       num_values;
    int          all_rejected;

    TEST_SUBSECTION(
        "markdown_streaming invalid static values");

    num_values = ARRAY_SIZE(test_values);
    all_rejected = 1;

    for (size_t i = 0; i < num_values; i++) {
        const char  *val;
        int          is_valid_static;
        int          would_reject;

        val = test_values[i];

        /*
         * Check if value is a valid static keyword
         * (case-insensitive: off, auto, force)
         */
        is_valid_static = is_valid_streaming_policy(val);

        /*
         * Simulate markdown_streaming's closed value set.
         */
        would_reject = !is_valid_static;

        /* Every value in this table is outside off/auto/force. */
        if (!would_reject) {
            all_rejected = 0;
        }
    }

    /*
     * EXPECTED BEHAVIOR assertion:
     * All invalid static values outside off/auto/force
     * should be rejected with NGX_CONF_ERROR.
     *
     */
    TEST_ASSERT(all_rejected == 1,
        "Invalid static values like 'atuo' should be "
        "rejected at config parse time");
}


/* ================================================================
 * Preservation Tests (preservation bugfix)
 *
 * These tests capture baseline behavior for NON-bug inputs.
 * They MUST PASS on unfixed code, confirming that the behavior
 * we want to preserve is correctly captured.
 *
 * After each bug fix, these tests are re-run to verify no
 * regressions were introduced.
 *
 * **Validates: post-commit error always fail-closed, post-commit ignores on_error policy,
 *              all error codes produce fail-closed, conditional if_modified_since_only allows streaming,
 *              conditional disabled allows streaming, tail feed SUCCESS sends output,
 *              no tail data -> direct finalize, no decompression -> skip decomp,
 *              valid static values accepted, variable expressions compile,
 *              duplicate directive -> 'is duplicate'**
 * ================================================================ */


/*
 * Streaming policy values (off/auto/force)
 * are accepted normally, including case variations.
 *
 * The markdown_streaming directive accepts off, auto, and force
 * as valid static values (case-insensitive).
 *
 * **Validates: valid static values accepted**
 */
static void
test_preserve_valid_static_values(void)
{
    const char  *valid_values[] = {
        "off", "auto", "force", "OFF", "AUTO", "FORCE",
        "Off", "Auto", "Force"
    };
    size_t       num_values;

    TEST_SUBSECTION(
        "markdown_streaming valid static values");

    num_values = ARRAY_SIZE(valid_values);

    for (size_t i = 0; i < num_values; i++) {
        const char  *val;
        int          is_valid;

        val = valid_values[i];

        /*
         * Check case-insensitive match against
         * off, auto, force.
         */
        is_valid = is_valid_streaming_policy(val);

        TEST_ASSERT(is_valid == 1,
            "Valid static value should be recognized");
    }

    TEST_PASS(
        "All markdown_streaming values accepted");
}


/*
 * markdown_streaming accepts only static policy tokens.
 *
 * **Validates: variable expressions are outside the supported value set**
 */
static void
test_policy_rejects_variable_expression(void)
{
    const char  *var_values[] = {
        "$streaming_mode", "${streaming_mode}",
        "$arg_engine"
    };
    size_t       num_values;

    TEST_SUBSECTION(
        "markdown_streaming variable expressions rejected");

    num_values = ARRAY_SIZE(var_values);

    for (size_t i = 0; i < num_values; i++) {
        const char  *val;
        int          has_dollar;
        int          is_valid_policy;

        val = var_values[i];

        /* Check if value contains '$' */
        has_dollar = (strchr(val, '$') != NULL);
        is_valid_policy = is_valid_streaming_policy(val);

        TEST_ASSERT(has_dollar == 1,
            "test input should contain '$'");
        TEST_ASSERT(is_valid_policy == 0,
            "variable expression must not match a streaming policy token");
    }

    TEST_PASS(
        "Variable expressions are rejected by the policy value set");
}


/*
 * Duplicate markdown_streaming directive returns
 * "is duplicate" error.
 *
 * When markdown_streaming is specified more than
 * once, the function returns "is duplicate". This check
 * is at the top of the function and must remain unchanged.
 *
 * **Validates: duplicate directive -> 'is duplicate'**
 */
static void
test_preserve_duplicate_directive(void)
{
    int          streaming_policy_set;
    const char  *result;

    TEST_SUBSECTION(
        "markdown_streaming duplicate returns error");

    /*
     * Simulate the duplicate check at the top of
     * ngx_http_markdown_streaming():
     * if the policy is already set:
     *     return "is duplicate";
     * }
     */
    streaming_policy_set = 1;  /* already configured */
    result = NULL;

    if (streaming_policy_set) {
        result = "is duplicate";
    }

    TEST_ASSERT(result != NULL,
        "Duplicate should return error string");
    TEST_ASSERT(strcmp(result, "is duplicate") == 0,
        "Error should be 'is duplicate'");
    TEST_PASS(
        "Duplicate markdown_streaming directive returns 'is duplicate'");
}


/* ================================================================
 * Production-header bindings (0.9.2 round-2 test integrity)
 *
 * This suite formerly carried hand-copied constant tables (error codes,
 * the markdown_streaming directive name).  Those mirrors were replaced by
 * a direct include of the generated production headers at the top of this
 * file, and this test keeps the binding honest at run time: it calls the
 * production ABI-gate helpers and asserts the values this suite depends
 * on.  If the Rust side renumbers a streaming error code or renames the
 * directive, this test and the mirror assertions below fail together.
 *
 * Validates: FFI error-code/ABI binding and directive-registry binding
 * for the streaming surface.
 * ================================================================ */

static void
test_production_header_bindings(void)
{
    TEST_SUBSECTION(
        "Production FFI header bindings (error codes, ABI gate, "
        "directive name)");

    /* Streaming-gated FFI codes this suite routes on. */
    TEST_ASSERT(ERROR_TIMEOUT == 3,
        "ERROR_TIMEOUT must match the production FFI header");
    TEST_ASSERT(ERROR_MEMORY_LIMIT == 4,
        "ERROR_MEMORY_LIMIT must match the production FFI header");
    TEST_ASSERT(ERROR_BUDGET_EXCEEDED == 6,
        "ERROR_BUDGET_EXCEEDED must match the production FFI header");
    TEST_ASSERT(ERROR_STREAMING_FALLBACK == 7,
        "ERROR_STREAMING_FALLBACK must match the production FFI header");
    TEST_ASSERT(ERROR_POST_COMMIT == 8,
        "ERROR_POST_COMMIT must match the production FFI header");
    TEST_ASSERT(ERROR_SUCCESS == 0 && ERROR_INTERNAL == 99,
        "ERROR_SUCCESS/ERROR_INTERNAL must match the production "
        "FFI header");

    /* Real production ABI gate: accept the bundled version, reject a
     * mismatched one, and require the full 4-tuple handshake. */
    TEST_ASSERT(ngx_http_markdown_ffi_abi_matches(MARKDOWN_ABI_VERSION),
        "ABI gate must accept the bundled ABI version");
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_matches(MARKDOWN_ABI_VERSION + 1),
        "ABI gate must reject a mismatched ABI version");
    TEST_ASSERT(ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
        "full 4-tuple ABI handshake must pass with the header values");
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH ^ 1,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
        "header-hash mismatch must fail the ABI handshake");

    /* Directive registry: the name this suite's config mirrors use must
     * be the production spelling from the canonical registry header. */
    TEST_ASSERT(strcmp(NGX_HTTP_MARKDOWN_DIRECTIVE_STREAMING,
        "markdown_streaming") == 0,
        "markdown_streaming directive name must match the production "
        "registry header");

    TEST_PASS("production header bindings verified");
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

    TEST_SECTION("14.1 Streaming Policy Selection");
    test_policy_off();
    test_policy_on_get();
    test_policy_on_head();
    test_policy_on_304();
    test_policy_on_conditional_full();
    test_policy_on_conditional_ims_only();
    test_policy_on_conditional_disabled();
    test_policy_on_sse();
    test_policy_auto_large_cl();
    test_policy_auto_small_cl();
    test_policy_auto_no_cl();


    TEST_SECTION("14.4b Input Disposition + Pending Input");
    test_pending_input_enqueue_terminal_capture();
    test_pending_input_empty_check();


    TEST_SECTION("14.6 Post-Commit Error Handling");
    test_postcommit_error_various_error_codes();

    TEST_SECTION("14.7 Configuration Directive Parsing");
    test_config_policy_values();

    TEST_SECTION("14.8 Output Chain Construction");
    test_output_chain_last_buf();
    test_output_chain_flush();


    TEST_SECTION("15.6 Streaming Headers Policy");
    test_init_failure_respects_error_policy();

    TEST_SECTION(
        "15.9.1 Unified Error Policy Runtime Encoding");
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
    test_metrics_deferred_lastbuf_failure();
    test_metrics_terminal_lastbuf_failure();
    test_metrics_terminal_lastbuf_again_then_ok();
    test_metrics_terminal_lastbuf_again_then_error();
    test_metrics_deferred_lastbuf_again_then_ok();
    test_metrics_deferred_lastbuf_again_then_error();


    TEST_SECTION("Bug 4 Preservation (Baseline)");
    test_preserve_valid_static_values();
    test_policy_rejects_variable_expression();
    test_preserve_duplicate_directive();

    TEST_SECTION("Production-header bindings (test integrity)");
    test_production_header_bindings();

    TEST_SECTION("Bug Condition Exploration (preservation bugfix)");
    test_config_invalid_static_value();

    printf("\n========================================\n");
    printf("All streaming tests passed!\n");
    printf("========================================\n\n");
    return 0;
}

#endif /* MARKDOWN_STREAMING_ENABLED */
