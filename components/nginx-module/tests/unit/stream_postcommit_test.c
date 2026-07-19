/*
 * Test: stream_postcommit
 *
 * Validates the post-commit safety property for the streaming
 * fallback state machine (streaming fallback state machine, post-commit safety).
 *
 * Property test: exercises the decision engine from COMMITTED state
 * with every possible event and verifies:
 *   - Decision action is NEVER PASS_HTML
 *   - Decision action is NEVER REJECT_502
 *   - Decision action is always SAFE_FINISH, ABORT, or CONTINUE_STREAMING
 *   - New state is always COMMITTED, POST_COMMIT_SAFE_FINISH,
 *     or POST_COMMIT_ABORT
 *
 * Also tests the postcommit guard function.
 *
 * Validates: post-commit never produces PASS_HTML, post-commit never produces REJECT_502
 */

#include "../include/test_common.h"

/* Pull in base NGINX types from stubs */
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif

#define ngx_log_debug0(level, log, err, fmt) \
    do { (void)(level); (void)(log); (void)(err); (void)(fmt); } while (0)

#define ngx_log_debug1(level, log, err, fmt, a1) \
    do { (void)(level); (void)(log); (void)(err); (void)(fmt); (void)(a1); } while (0)

#define ngx_log_debug3(level, log, err, fmt, a1, a2, a3) \
    do { (void)(level); (void)(log); (void)(err); (void)(fmt); (void)(a1); (void)(a2); (void)(a3); } while (0)

#ifndef NGX_HTTP_BAD_GATEWAY
#define NGX_HTTP_BAD_GATEWAY  502
#endif

#ifndef POST_COMMIT_SAFE_FINISH
#define POST_COMMIT_SAFE_FINISH 3
#endif

#ifndef POST_COMMIT_ABORT
#define POST_COMMIT_ABORT 4
#endif

#ifndef NGX_CONF_UNSET
#define NGX_CONF_UNSET (-1)
#endif

#ifndef NGX_CONF_UNSET_UINT
#define NGX_CONF_UNSET_UINT ((ngx_uint_t) -1)
#endif

#ifndef NGX_CONF_UNSET_SIZE
#define NGX_CONF_UNSET_SIZE ((size_t) -1)
#endif

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif

#define MARKDOWN_STREAMING_ENABLED 1

#ifndef ngx_str_set
#define ngx_str_set(str, text)                                    \
    (str)->len = sizeof(text) - 1; (str)->data = (u_char *) text
#endif

#ifndef ngx_strncasecmp
#define ngx_strncasecmp(s1, s2, n) \
    strncasecmp((const char *) (s1), (const char *) (s2), (n))
#endif

#ifndef ngx_memcpy
#define ngx_memcpy(dst, src, n) memcpy((dst), (src), (n))
#endif

#ifndef NGX_HTTP_MARKDOWN_BUFFERED
#define NGX_HTTP_MARKDOWN_BUFFERED 0x08
#endif

typedef intptr_t ngx_err_t;

/* Define structs that the stubs only forward-declare */
struct ngx_log_s { int dummy; };
struct ngx_pool_s { ngx_log_t *log; };
struct ngx_array_s {
    void       *elts;
    ngx_uint_t  nelts;
    size_t      size;
    ngx_uint_t  nalloc;
    ngx_pool_t *pool;
};
struct ngx_shm_zone_s { int dummy; };
struct ngx_module_s { int dummy; };
struct ngx_command_s { int dummy; };
struct ngx_conf_s { ngx_pool_t *pool; };
struct ngx_chain_s { ngx_buf_t *buf; struct ngx_chain_s *next; };
struct ngx_http_complex_value_s { ngx_str_t value; };

typedef struct {
    ngx_log_t *log;
} ngx_connection_impl_t;

typedef struct {
    ngx_str_t     content_type;
    size_t        content_type_len;
    u_char       *content_type_lowcase;
    ngx_uint_t    status;
    off_t         content_length_n;
} ngx_http_headers_out_t;

struct ngx_http_request_s {
    ngx_connection_impl_t  *connection;
    ngx_pool_t             *pool;
    ngx_http_headers_out_t  headers_out;
    struct ngx_http_request_s *main;
    ngx_uint_t              buffered;
};

/* Include the module header for types */
#include "../../src/ngx_http_markdown_filter_module.h"

ngx_module_t ngx_http_markdown_filter_module;

static ngx_int_t (*ngx_http_next_body_filter)(ngx_http_request_t *r,
    ngx_chain_t *in);
#include "../../src/ngx_http_markdown_filter_chain_impl.h"

void
ngx_http_markdown_metrics_record_postcommit_pending(size_t bytes)
{
    UNUSED(bytes);
}

void
ngx_http_markdown_metrics_record_postcommit_copied_delivery(size_t bytes)
{
    UNUSED(bytes);
}

void
ngx_http_markdown_metrics_record_postcommit_abort(void)
{
}

/* Include the decision engine source directly */
#include "../../src/ngx_http_markdown_stream_state.h"
#include "../../src/ngx_http_markdown_stream_state.c"

/* Include the postcommit header for guard function */
#include "../../src/ngx_http_markdown_stream_postcommit.h"

/* Test infrastructure */
static ngx_log_t             test_log;
static struct ngx_pool_s     test_pool;
static ngx_connection_impl_t test_connection;
static ngx_http_request_t    test_request;

/* Track output_filter calls */
static int test_output_filter_called;
static int test_output_filter_rc;
static ngx_chain_t *test_output_filter_chain;
static ngx_buf_t *test_output_filter_buf;
static int test_poison_top_filter_called;

/* Saved downstream body filter used by the production delegation helper. */
static ngx_int_t
test_next_body_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    UNUSED(r);
    test_output_filter_called++;
    test_output_filter_chain = in;
    test_output_filter_buf = (in != NULL) ? in->buf : NULL;
    return test_output_filter_rc;
}

/* Poison top filter: these paths must never re-enter the global chain. */
ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    UNUSED(r);
    UNUSED(in);
    test_poison_top_filter_called++;
    return NGX_ERROR;
}

/* Track ngx_calloc_buf behavior */
static int test_calloc_buf_called;
static ngx_buf_t *test_calloc_buf_result;
static ngx_buf_t  test_calloc_buf_storage;
static ngx_chain_t test_chain_link_storage;
static u_char test_palloc_storage[256];
static int test_palloc_called;
static int test_palloc_fail;
static size_t test_palloc_size;

/* Mock: ngx_calloc_buf */
ngx_buf_t *
ngx_calloc_buf(ngx_pool_t *pool)
{
    UNUSED(pool);
    test_calloc_buf_called++;
    if (test_calloc_buf_result != NULL) {
        return test_calloc_buf_result;
    }
    memset(&test_calloc_buf_storage, 0, sizeof(test_calloc_buf_storage));
    return &test_calloc_buf_storage;
}

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    test_palloc_called++;
    test_palloc_size = size;
    if (test_palloc_fail || size > sizeof(test_palloc_storage)) {
        return NULL;
    }
    memset(test_palloc_storage, 0, sizeof(test_palloc_storage));
    return test_palloc_storage;
}

ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    UNUSED(pool);
    memset(&test_chain_link_storage, 0, sizeof(test_chain_link_storage));
    return &test_chain_link_storage;
}

/* Stub: ngx_log_error_core */
void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log,
                   ngx_err_t err, const char *fmt, ...)
{
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);
}

/* Include markdown_converter.h first for FFI type declarations */
#include "../../src/markdown_converter.h"

static uint32_t test_safe_finish_rc;
static u_char *test_safe_finish_data;
static uintptr_t test_safe_finish_len;
static int test_streaming_abort_called;
static int test_output_free_called;
static u_char *test_output_free_data;
static uintptr_t test_output_free_len;

/* Stub: markdown_streaming_safe_finish */
uint32_t
markdown_streaming_safe_finish(struct StreamingConverterHandle *handle,
    u_char **out_data, uintptr_t *out_len)
{
    UNUSED(handle);
    if (out_data != NULL) *out_data = test_safe_finish_data;
    if (out_len != NULL) *out_len = test_safe_finish_len;
    return test_safe_finish_rc;
}

/* Stub: markdown_streaming_abort */
void
markdown_streaming_abort(struct StreamingConverterHandle *handle)
{
    UNUSED(handle);
    test_streaming_abort_called++;
}

/* Stub: markdown_streaming_output_free */
void
markdown_streaming_output_free(u_char *data, uintptr_t len)
{
    test_output_free_called++;
    test_output_free_data = data;
    test_output_free_len = len;
}

/* Include the postcommit source (for guard function) */
#include "../../src/ngx_http_markdown_stream_postcommit.c"

/*
 * Include the error handler source for on_error-level regression tests.
 * The postcommit test file provides all required stubs (output_filter,
 * calloc_buf, palloc, chain_link, safe_finish, output_free).
 */
#include "../../src/ngx_http_markdown_stream_replay.h"

ngx_chain_t *
ngx_http_markdown_stream_replay_chain(const ngx_http_markdown_ctx_t *ctx,
                                       ngx_pool_t *pool)
{
    UNUSED(ctx); UNUSED(pool);
    return NULL;
}

ngx_flag_t
ngx_http_markdown_stream_replay_available(
    const ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL) { return 0; }
    if (!ctx->stream_sm.replay_initialized) { return 0; }
    if (ctx->stream_sm.replay_buf.size > ctx->stream_sm.replay_capacity) {
        return 0;
    }
    return 1;
}

#include "../../src/ngx_http_markdown_stream_error.c"


static void test_setup(void)
{
    memset(&test_log, 0, sizeof(test_log));
    memset(&test_pool, 0, sizeof(test_pool));
    memset(&test_connection, 0, sizeof(test_connection));
    memset(&test_request, 0, sizeof(test_request));
    test_pool.log = &test_log;
    test_connection.log = &test_log;
    test_request.connection = &test_connection;
    test_request.pool = &test_pool;
    test_request.main = &test_request;
    test_output_filter_called = 0;
    test_output_filter_rc = NGX_OK;
    test_output_filter_chain = NULL;
    test_output_filter_buf = NULL;
    test_poison_top_filter_called = 0;
    ngx_http_next_body_filter = test_next_body_filter;
    test_calloc_buf_called = 0;
    test_calloc_buf_result = NULL;
    memset(&test_calloc_buf_storage, 0, sizeof(test_calloc_buf_storage));
    memset(&test_chain_link_storage, 0, sizeof(test_chain_link_storage));
    memset(test_palloc_storage, 0, sizeof(test_palloc_storage));
    test_palloc_called = 0;
    test_palloc_fail = 0;
    test_palloc_size = 0;
    test_safe_finish_rc = POST_COMMIT_ABORT;
    test_safe_finish_data = NULL;
    test_safe_finish_len = 0;
    test_streaming_abort_called = 0;
    test_output_free_called = 0;
    test_output_free_data = NULL;
    test_output_free_len = 0;
}


/*
 * Property test: from COMMITTED state, iterate over every event
 * and both on_error policies.  Verify the post-commit safety
 * invariant holds for all combinations.
 */
static void test_committed_never_produces_html(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;
    ngx_http_markdown_stream_event_e event;
    ngx_uint_t policy;
    int tested = 0;

    ngx_http_markdown_stream_event_e all_events[] = {
        NGX_HTTP_MD_EVENT_ELIGIBLE,
        NGX_HTTP_MD_EVENT_NOT_ELIGIBLE,
        NGX_HTTP_MD_EVENT_STREAMING_START,
        NGX_HTTP_MD_EVENT_PARSER_UNSUITABLE,
        NGX_HTTP_MD_EVENT_HARD_EXCLUDED,
        NGX_HTTP_MD_EVENT_FULL_DOC_FEATURE,
        NGX_HTTP_MD_EVENT_BUDGET_INIT_FAILURE,
        NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW,
        NGX_HTTP_MD_EVENT_RESOURCE_LIMIT,
        NGX_HTTP_MD_EVENT_STRICT_ETAG,
        NGX_HTTP_MD_EVENT_LOOK_BEHIND_OVERFLOW,
        NGX_HTTP_MD_EVENT_AUTO_RISK,
        NGX_HTTP_MD_EVENT_COMMIT,
        NGX_HTTP_MD_EVENT_ERROR,
        NGX_HTTP_MD_EVENT_ON_ERROR_PASS,
        NGX_HTTP_MD_EVENT_ON_ERROR_REJECT
    };
    size_t num_events = ARRAY_SIZE(all_events);
    size_t i;

    for (policy = 0; policy <= 1; policy++) {
        for (i = 0; i < num_events; i++) {
            event = all_events[i];

            memset(&ctx, 0, sizeof(ctx));
            ctx.current_state = NGX_HTTP_MD_STATE_COMMITTED;
            ctx.on_error_policy = policy;
            ctx.replay_available = 1;
            ctx.headers_committed = 1;
            ctx.within_resource_limits = 1;

            d = ngx_http_markdown_stream_decide(&ctx, event);

            /* Safety invariant: NEVER PASS_HTML */
            TEST_ASSERT(d.action != NGX_HTTP_MD_ACTION_PASS_HTML,
                        "COMMITTED: action != PASS_HTML");

            /* Safety invariant: NEVER REJECT_502 */
            TEST_ASSERT(d.action != NGX_HTTP_MD_ACTION_REJECT_502,
                        "COMMITTED: action != REJECT_502");

            /* Action must be one of valid post-commit actions */
            TEST_ASSERT(
                d.action == NGX_HTTP_MD_ACTION_SAFE_FINISH
                || d.action == NGX_HTTP_MD_ACTION_ABORT
                || d.action == NGX_HTTP_MD_ACTION_CONTINUE_STREAMING,
                "COMMITTED: valid post-commit action");

            /* State must remain in post-commit domain */
            TEST_ASSERT(
                d.new_state == NGX_HTTP_MD_STATE_COMMITTED
                || d.new_state
                   == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH
                || d.new_state
                   == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
                "COMMITTED: state stays in post-commit domain");

            tested++;
        }
    }

    TEST_ASSERT(tested == (int)(num_events * 2),
                "all event/policy combinations tested");
    TEST_PASS("Post-commit never produces HTML (all events)");
}

/*
 * Same property test from POST_COMMIT_SAFE_FINISH and
 * POST_COMMIT_ABORT terminal states.
 */
static void test_post_commit_terminals_never_produce_html(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;
    ngx_http_markdown_stream_state_e terminal_states[] = {
        NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
        NGX_HTTP_MD_STATE_POST_COMMIT_ABORT
    };
    ngx_http_markdown_stream_event_e all_events[] = {
        NGX_HTTP_MD_EVENT_ELIGIBLE,
        NGX_HTTP_MD_EVENT_NOT_ELIGIBLE,
        NGX_HTTP_MD_EVENT_STREAMING_START,
        NGX_HTTP_MD_EVENT_PARSER_UNSUITABLE,
        NGX_HTTP_MD_EVENT_ERROR,
        NGX_HTTP_MD_EVENT_COMMIT,
        NGX_HTTP_MD_EVENT_ON_ERROR_PASS,
        NGX_HTTP_MD_EVENT_ON_ERROR_REJECT
    };
    size_t num_events = ARRAY_SIZE(all_events);
    size_t num_states = ARRAY_SIZE(terminal_states);
    size_t s, i;

    for (s = 0; s < num_states; s++) {
        for (i = 0; i < num_events; i++) {
            memset(&ctx, 0, sizeof(ctx));
            ctx.current_state = terminal_states[s];
            ctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

            d = ngx_http_markdown_stream_decide(
                &ctx, all_events[i]);

            TEST_ASSERT(d.action != NGX_HTTP_MD_ACTION_PASS_HTML,
                        "terminal: action != PASS_HTML");
            TEST_ASSERT(d.action != NGX_HTTP_MD_ACTION_REJECT_502,
                        "terminal: action != REJECT_502");
            TEST_ASSERT(d.new_state == terminal_states[s],
                        "terminal state stays unchanged");
        }
    }

    TEST_PASS("Post-commit terminal states never produce HTML");
}

/* --- Postcommit guard function tests --- */

static void test_guard_passes_non_html(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "# Hello Markdown";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_OK, "guard passes for Markdown content");
    TEST_PASS("Guard passes for non-HTML content");
}

static void test_guard_fails_doctype(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "<!DOCTYPE html><html><body>Hi</body></html>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard fails for DOCTYPE content");
    TEST_PASS("Guard fails for content starting with <!DOCTYPE");
}

static void test_guard_fails_html_tag(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "<html><head></head><body>Hi</body></html>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard fails for <html tag");
    TEST_PASS("Guard fails for content starting with <html");
}

static void test_guard_fails_leading_whitespace_doctype(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = " \r\n\t<!doctype html><html></html>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard fails for whitespace-prefixed doctype");
    TEST_PASS("Guard detects whitespace-prefixed doctype");
}

static void test_guard_fails_bom_prefixed_html(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "\xef\xbb\xbf<html><body>Hi</body></html>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard fails for BOM-prefixed html tag");
    TEST_PASS("Guard detects BOM-prefixed html");
}

static void test_guard_fails_meta_prefix(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "<meta charset=\"utf-8\"><html></html>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard fails for meta-prefixed HTML");
    TEST_PASS("Guard detects meta-prefixed HTML");
}

static void test_guard_passes_non_committed_state(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "<!DOCTYPE html><html>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_OK,
                "guard passes in non-committed state");
    TEST_PASS("Guard passes in non-committed states");
}

static void test_guard_null_params(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    rc = ngx_http_markdown_stream_postcommit_guard(
        NULL, &ctx, NULL);
    TEST_ASSERT(rc == NGX_ERROR, "guard NULL request -> ERROR");

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, NULL, NULL);
    TEST_ASSERT(rc == NGX_ERROR, "guard NULL ctx -> ERROR");

    TEST_PASS("Guard NULL parameters return NGX_ERROR");
}


/* --- safe_finish tests --- */

static void test_safe_finish_happy_path(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_OK, "safe_finish returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.state
                == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
                "state = POST_COMMIT_SAFE_FINISH");
    TEST_ASSERT(test_output_filter_called == 1,
                "send_terminal called saved downstream filter");
    TEST_ASSERT(test_output_filter_chain != NULL,
                "delegation helper forwarded the terminal chain");
    TEST_ASSERT(test_poison_top_filter_called == 0,
                "send_terminal bypassed the poison top filter");
    TEST_ASSERT(test_streaming_abort_called == 0,
                "streaming abort must not be called on success");
    TEST_PASS("safe_finish happy path");
}

static void test_safe_finish_empty_rust_output_sends_terminal(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.streaming.handle =
        (struct StreamingConverterHandle *) (uintptr_t) 0x1;
    test_safe_finish_rc = POST_COMMIT_SAFE_FINISH;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_OK,
                "empty Rust safe_finish output should succeed");
    TEST_ASSERT(test_output_filter_called == 1,
                "empty safe_finish should send terminal chain");
    TEST_ASSERT(test_output_filter_buf != NULL
                && test_output_filter_buf->last_buf == 1,
                "empty safe_finish terminal should set last_buf");
    TEST_ASSERT(test_output_free_called == 0,
                "empty safe_finish should not free a NULL output");
    TEST_ASSERT(ctx.streaming.handle == NULL,
                "safe_finish consumes the streaming handle");
    TEST_ASSERT(test_streaming_abort_called == 0,
                "streaming abort must not be called on empty output success");
    TEST_PASS("safe_finish empty Rust output sends terminal");
}

static void test_safe_finish_copies_rust_output_before_free(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;
    u_char closing[] = "\n```";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.streaming.handle =
        (struct StreamingConverterHandle *) (uintptr_t) 0x1;
    test_safe_finish_rc = POST_COMMIT_SAFE_FINISH;
    test_safe_finish_data = closing;
    test_safe_finish_len = sizeof(closing) - 1;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_OK,
                "non-empty Rust safe_finish output should succeed");
    TEST_ASSERT(test_output_filter_buf != NULL,
                "closing bytes should be sent");
    TEST_ASSERT(test_output_filter_buf->pos != closing,
                "closing bytes should be copied to pool memory");
    TEST_ASSERT(test_palloc_called == 1,
                "pool allocation should be used for closing bytes");
    TEST_ASSERT(test_palloc_size == sizeof(closing) - 1,
                "pool allocation size should match closing length");
    TEST_ASSERT(memcmp(test_output_filter_buf->pos, closing,
                       sizeof(closing) - 1) == 0,
                "pool copy should preserve closing bytes");
    TEST_ASSERT(test_output_free_called == 1,
                "Rust output should be freed exactly once");
    TEST_ASSERT(test_output_free_data == closing,
                "Rust free should use original output pointer");
    TEST_ASSERT(test_output_free_len == sizeof(closing) - 1,
                "Rust free should use original output length");
    TEST_ASSERT(test_streaming_abort_called == 0,
                "streaming abort must not be called on successful copy path");
    TEST_PASS("safe_finish copies Rust output before free");
}

static void test_safe_finish_backpressure_preserves_pending_chain(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;
    u_char closing[] = "\n```";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.streaming.handle =
        (struct StreamingConverterHandle *) (uintptr_t) 0x1;
    test_safe_finish_rc = POST_COMMIT_SAFE_FINISH;
    test_safe_finish_data = closing;
    test_safe_finish_len = sizeof(closing) - 1;
    test_output_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_AGAIN,
                "closing-byte backpressure should return NGX_AGAIN");
    TEST_ASSERT(test_output_filter_called == 1,
                "NGX_AGAIN came from the saved downstream filter");
    TEST_ASSERT(test_poison_top_filter_called == 0,
                "backpressured send bypassed the poison top filter");
    TEST_ASSERT(ctx.streaming.pending_output == test_output_filter_chain,
                "pending chain should be the pool-owned output chain");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 1,
                "pending chain should be marked as data-bearing");
    TEST_ASSERT(ctx.streaming.pending_meta.bytes == sizeof(closing) - 1,
                "pending byte count should match closing length");
    TEST_ASSERT(ctx.streaming.pending_meta.main_terminal == 1,
                "main terminal metadata must survive NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_meta.subrequest_terminal == 0,
                "main terminal must not set subrequest metadata");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 0,
                "NGX_AGAIN must not confirm main terminal delivery");
    TEST_ASSERT((test_request.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
                "request should be marked buffered on backpressure");
    TEST_ASSERT(test_output_free_called == 1,
                "Rust output should still be freed after pool copy");
    TEST_ASSERT(test_streaming_abort_called == 0,
                "streaming abort must not be called on backpressure");
    TEST_PASS("safe_finish backpressure preserves pending chain");
}

static void test_safe_finish_idempotent_reentry(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_OK,
                "safe_finish idempotent returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.state
                == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
                "state stays POST_COMMIT_SAFE_FINISH");
    TEST_PASS("safe_finish idempotent re-entry");
}

static void test_safe_finish_then_abort_does_not_double_send(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);
    TEST_ASSERT(rc == NGX_OK, "safe_finish should succeed");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 1,
                "safe_finish should latch terminal send");

    rc = ngx_http_markdown_stream_postcommit_abort(&test_request, &ctx);
    TEST_ASSERT(rc == NGX_OK, "abort after safe_finish should be OK");
    TEST_ASSERT(test_output_filter_called == 1,
                "abort after safe_finish should not send second terminal");
    TEST_PASS("safe_finish followed by abort is terminal-idempotent");
}

static void test_subrequest_terminal_delivery_lifecycle(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_request_t main_request;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&main_request, 0, sizeof(main_request));
    test_request.main = &main_request;
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);
    TEST_ASSERT(rc == NGX_OK, "subrequest safe_finish should succeed");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 0,
                "subrequest terminal must not latch main terminal state");
    TEST_ASSERT(ctx.streaming.subrequest_terminal_sent == 1,
                "subrequest terminal should latch after confirmed delivery");
    TEST_ASSERT(test_output_filter_buf->last_buf == 0,
                "subrequest terminal must not carry last_buf");
    TEST_ASSERT(test_output_filter_buf->last_in_chain == 1,
                "subrequest terminal must carry last_in_chain");

    rc = ngx_http_markdown_stream_postcommit_abort(&test_request, &ctx);
    TEST_ASSERT(rc == NGX_OK, "subrequest abort after finish should be OK");
    TEST_ASSERT(test_output_filter_called == 1,
                "confirmed subrequest terminal must not be sent twice");

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&main_request, 0, sizeof(main_request));
    test_request.main = &main_request;
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    test_output_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);
    TEST_ASSERT(rc == NGX_AGAIN,
                "subrequest terminal backpressure should propagate");
    TEST_ASSERT(ctx.streaming.subrequest_terminal_sent == 0,
                "NGX_AGAIN must not confirm subrequest terminal delivery");
    TEST_ASSERT(ctx.streaming.pending_meta.main_terminal == 0,
                "subrequest pending chain must not set main metadata");
    TEST_ASSERT(ctx.streaming.pending_meta.subrequest_terminal == 1,
                "subrequest terminal metadata must survive NGX_AGAIN");
    TEST_PASS("subrequest terminal delivery lifecycle is request-aware");
}

static void test_safe_finish_invalid_state(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_ERROR,
                "safe_finish from PRE_COMMIT -> NGX_ERROR");

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PASSTHROUGH;
    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_ERROR,
                "safe_finish from PASSTHROUGH -> NGX_ERROR");
    TEST_PASS("safe_finish invalid state returns NGX_ERROR");
}

static void test_safe_finish_null_params(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(NULL, &ctx);
    TEST_ASSERT(rc == NGX_ERROR, "safe_finish NULL r -> ERROR");

    rc = ngx_http_markdown_stream_postcommit_safe_finish(&test_request, NULL);
    TEST_ASSERT(rc == NGX_ERROR, "safe_finish NULL ctx -> ERROR");
    TEST_PASS("safe_finish NULL parameters return NGX_ERROR");
}

static void test_safe_finish_send_terminal_fails(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    /* Make send_terminal fail */
    test_output_filter_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_ERROR,
                "safe_finish send_terminal fails -> NGX_ERROR");
    TEST_ASSERT(test_streaming_abort_called == 0,
                "streaming abort must not be called when send_terminal fails");
    TEST_PASS("safe_finish send_terminal failure propagates");
}


static void test_safe_finish_invalid_input_aborts_handle(void)
{
    ngx_http_markdown_ctx_t ctx;
    struct StreamingConverterHandle *handle;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    handle = (struct StreamingConverterHandle *) (uintptr_t) 0x1;
    ctx.streaming.handle = handle;
    test_safe_finish_rc = ERROR_INVALID_INPUT;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_ERROR,
                "invalid FFI input should propagate as NGX_ERROR");
    TEST_ASSERT(test_streaming_abort_called == 1,
                "invalid FFI input must explicitly abort the handle");
    TEST_ASSERT(ctx.streaming.handle == NULL,
                "aborted handle must be cleared");
    TEST_PASS("safe_finish invalid input aborts Rust handle");
}

/* --- abort tests --- */

static void test_abort_happy_path(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    rc = ngx_http_markdown_stream_postcommit_abort(&test_request, &ctx);

    TEST_ASSERT(rc == NGX_OK, "abort returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
                "state = POST_COMMIT_ABORT");
    TEST_ASSERT(test_output_filter_called == 1,
                "send_terminal called output_filter");
    TEST_PASS("abort happy path");
}

static void test_abort_from_safe_finish_state(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH;

    rc = ngx_http_markdown_stream_postcommit_abort(&test_request, &ctx);

    TEST_ASSERT(rc == NGX_OK, "abort from SAFE_FINISH returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
                "state = POST_COMMIT_ABORT");
    TEST_PASS("abort from POST_COMMIT_SAFE_FINISH state");
}

static void test_abort_idempotent_reentry(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_ABORT;

    rc = ngx_http_markdown_stream_postcommit_abort(&test_request, &ctx);

    TEST_ASSERT(rc == NGX_OK, "abort idempotent returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
                "state stays POST_COMMIT_ABORT");
    TEST_PASS("abort idempotent re-entry");
}

static void test_abort_invalid_state(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;

    rc = ngx_http_markdown_stream_postcommit_abort(&test_request, &ctx);

    TEST_ASSERT(rc == NGX_ERROR,
                "abort from PRE_COMMIT -> NGX_ERROR");

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_NOT_ELIGIBLE;
    rc = ngx_http_markdown_stream_postcommit_abort(&test_request, &ctx);

    TEST_ASSERT(rc == NGX_ERROR,
                "abort from NOT_ELIGIBLE -> NGX_ERROR");
    TEST_PASS("abort invalid state returns NGX_ERROR");
}

static void test_abort_null_params(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    rc = ngx_http_markdown_stream_postcommit_abort(NULL, &ctx);
    TEST_ASSERT(rc == NGX_ERROR, "abort NULL r -> ERROR");

    rc = ngx_http_markdown_stream_postcommit_abort(&test_request, NULL);
    TEST_ASSERT(rc == NGX_ERROR, "abort NULL ctx -> ERROR");
    TEST_PASS("abort NULL parameters return NGX_ERROR");
}

static void test_abort_send_terminal_fails(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    /* Make send_terminal fail */
    test_output_filter_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_postcommit_abort(&test_request, &ctx);

    TEST_ASSERT(rc == NGX_ERROR,
                "abort send_terminal fails -> NGX_ERROR");
    TEST_PASS("abort send_terminal failure propagates");
}

/* --- postcommit_log tests --- */

static void test_postcommit_log_null_params(void)
{
    test_setup();

    /* Should not crash with NULL params */
    ngx_http_markdown_stream_postcommit_log(NULL, NULL, NULL,
        NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);
    ngx_http_markdown_stream_postcommit_log(&test_request, NULL, "abort",
        NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);
    TEST_PASS("postcommit_log NULL params handled safely");
}

static void test_postcommit_log_happy_path(void)
{
    ngx_http_markdown_ctx_t ctx;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_ABORT;

    /* Should not crash */
    ngx_http_markdown_stream_postcommit_log(
        &test_request, &ctx, "safe_finish",
        NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);

    ngx_http_markdown_stream_postcommit_log(
        &test_request, &ctx, "abort",
        NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);

    TEST_PASS("postcommit_log emits log without crashing");
}

/* --- send_terminal subrequest test --- */

static void test_send_terminal_subrequest(void)
{
    ngx_http_request_t subrequest;
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&subrequest, 0, sizeof(subrequest));
    memset(&ctx, 0, sizeof(ctx));

    subrequest.connection = &test_connection;
    subrequest.pool = &test_pool;
    /* subrequest.main points to a different request */
    subrequest.main = &test_request;
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    rc = ngx_http_markdown_stream_postcommit_abort(&subrequest, &ctx);

    TEST_ASSERT(rc == NGX_OK, "subrequest abort returns NGX_OK");
    /* For subrequest, last_buf should be 0 (not main request) */
    TEST_ASSERT(test_calloc_buf_storage.last_buf == 0,
                "subrequest: last_buf = 0");
    TEST_ASSERT(test_calloc_buf_storage.last_in_chain == 1,
                "subrequest: last_in_chain = 1");
    TEST_PASS("send_terminal handles subrequest correctly");
}

/* --- Guard with multi-buffer chain --- */

static void test_guard_multi_buffer_chain(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf1, buf2;
    ngx_chain_t chain1, chain2;
    ngx_int_t rc;
    u_char data1[] = "# Markdown content";
    u_char data2[] = "<!DOCTYPE html>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf1, 0, sizeof(buf1));
    buf1.pos = data1;
    buf1.last = data1 + sizeof(data1) - 1;
    buf1.memory = 1;
    chain1.buf = &buf1;
    chain1.next = &chain2;

    memset(&buf2, 0, sizeof(buf2));
    buf2.pos = data2;
    buf2.last = data2 + sizeof(data2) - 1;
    buf2.memory = 1;
    chain2.buf = &buf2;
    chain2.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain1);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard detects DOCTYPE in second buffer");
    TEST_PASS("Guard detects HTML in multi-buffer chain");
}

/* --- Guard with short buffer --- */

static void test_guard_short_buffer(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "<htm";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + 4;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_OK,
                "guard passes for short buffer (< 5 bytes)");
    TEST_PASS("Guard passes for buffer shorter than signature");
}

/* --- Guard with NULL buf in chain --- */

static void test_guard_null_buf_in_chain(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_chain_t chain;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    chain.buf = NULL;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_OK, "guard passes with NULL buf");
    TEST_PASS("Guard handles NULL buf in chain");
}

/* --- Guard case-insensitive DOCTYPE --- */

static void test_guard_case_insensitive_doctype(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "<!doctype HTML>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard detects lowercase <!doctype");
    TEST_PASS("Guard case-insensitive DOCTYPE detection");
}


/*
 * Regression test: safe_finish success + close_len == 0 +
 * send_terminal returns NGX_AGAIN.
 *
 * Validates that the terminal-only branch in finish_via_rust()
 * preserves NGX_AGAIN (backpressure) instead of converting it
 * to NGX_ERROR.  Without this fix, downstream backpressure on
 * the terminal buffer would incorrectly trigger the abort path.
 */
static void test_safe_finish_no_closing_bytes_backpressure(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.streaming.handle =
        (struct StreamingConverterHandle *) (uintptr_t) 0x1;

    /* Rust safe_finish succeeds with no closing bytes */
    test_safe_finish_rc = POST_COMMIT_SAFE_FINISH;
    test_safe_finish_data = NULL;
    test_safe_finish_len = 0;

    /* Downstream returns NGX_AGAIN on terminal chain */
    test_output_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(
        &test_request, &ctx);

    TEST_ASSERT(rc == NGX_AGAIN,
                "no-closing-bytes + terminal NGX_AGAIN "
                "should propagate NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
                "pending_output should be set on backpressure");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 0,
                "pending_has_data should be 0 (terminal only)");
    TEST_ASSERT(ctx.streaming.pending_meta.main_terminal == 1,
                "terminal-only metadata must survive NGX_AGAIN");
    TEST_ASSERT(
        (test_request.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "request should be marked buffered");
    TEST_ASSERT(ctx.streaming.handle == NULL,
                "handle should be consumed regardless");
    TEST_PASS("safe_finish no-closing-bytes backpressure preserved");
}


/*
 * Regression test: post-commit terminal-only NGX_AGAIN through on_error.
 *
 * Scenario (exact P0 regression shape from 0.8.0 code review):
 *   - COMMITTED state, on_error=pass
 *   - Rust safe_finish returns POST_COMMIT_SAFE_FINISH
 *   - close_data == NULL, close_len == 0 (no closing Markdown bytes)
 *   - send_terminal / output_filter returns NGX_AGAIN (downstream backpressure)
 *
 * Expected: on_error returns NGX_AGAIN and does NOT fall through to abort.
 * This is critical because terminal-only backpressure is a legitimate
 * pending state that resume_pending() will drain.
 */
static void test_on_error_terminal_only_again_no_abort(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    /* Rust safe_finish succeeds with no closing bytes */
    test_safe_finish_rc = POST_COMMIT_SAFE_FINISH;
    test_safe_finish_data = NULL;
    test_safe_finish_len = 0;

    /* Downstream returns NGX_AGAIN on terminal chain */
    test_output_filter_rc = NGX_AGAIN;

    /* Include the production error handler to test the full path */
    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_AGAIN,
        "terminal-only NGX_AGAIN must propagate, not abort");
    TEST_ASSERT(ctx.stream_sm.state
                == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
        "state must remain POST_COMMIT_SAFE_FINISH");
    TEST_ASSERT(test_output_filter_called == 1,
        "send_terminal should be called exactly once");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "pending_output should be set for resume_pending");
    TEST_PASS("on_error terminal-only NGX_AGAIN preserved (no abort)");
}

/*
 * Guard test: detect <body tag (extended signatures in 0.8.0+).
 */
static void test_guard_fails_body_tag(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "<body class=\"main\">Content</body>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard detects <body tag");
    TEST_PASS("Guard detects <body> tag");
}


/*
 * Guard test: detect <script tag (extended signatures in 0.8.0+).
 */
static void test_guard_fails_script_tag(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "<script>alert('xss')</script>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard detects <script tag");
    TEST_PASS("Guard detects <script> tag");
}


/*
 * Guard test: detect <!-- HTML comment (extended signatures in 0.8.0+).
 */
static void test_guard_fails_html_comment(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_buf_t buf;
    ngx_chain_t chain;
    ngx_int_t rc;
    u_char data[] = "<!-- This is a comment --><div>test</div>";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    buf.memory = 1;
    chain.buf = &buf;
    chain.next = NULL;

    rc = ngx_http_markdown_stream_postcommit_guard(
        &test_request, &ctx, &chain);

    TEST_ASSERT(rc == NGX_ERROR,
                "guard detects <!-- comment");
    TEST_PASS("Guard detects <!-- HTML comment");
}


int main(void)
{
    TEST_SECTION("Post-commit Safety Property (streaming fallback state machine, post-commit safety)");

    test_committed_never_produces_html();
    test_post_commit_terminals_never_produce_html();
    test_guard_passes_non_html();
    test_guard_fails_doctype();
    test_guard_fails_html_tag();
    test_guard_fails_leading_whitespace_doctype();
    test_guard_fails_bom_prefixed_html();
    test_guard_fails_meta_prefix();
    test_guard_passes_non_committed_state();
    test_guard_null_params();
    test_guard_multi_buffer_chain();
    test_guard_short_buffer();
    test_guard_null_buf_in_chain();
    test_guard_case_insensitive_doctype();
    test_guard_fails_body_tag();
    test_guard_fails_script_tag();
    test_guard_fails_html_comment();

    TEST_SECTION("Post-commit safe_finish (safe_finish)");
    test_safe_finish_happy_path();
    test_safe_finish_empty_rust_output_sends_terminal();
    test_safe_finish_copies_rust_output_before_free();
    test_safe_finish_backpressure_preserves_pending_chain();
    test_safe_finish_no_closing_bytes_backpressure();
    test_safe_finish_idempotent_reentry();
    test_safe_finish_then_abort_does_not_double_send();
    test_subrequest_terminal_delivery_lifecycle();
    test_safe_finish_invalid_state();
    test_safe_finish_null_params();
    test_safe_finish_send_terminal_fails();
    test_safe_finish_invalid_input_aborts_handle();

    TEST_SECTION("Post-commit abort (abort)");
    test_abort_happy_path();
    test_abort_from_safe_finish_state();
    test_abort_idempotent_reentry();
    test_abort_invalid_state();
    test_abort_null_params();
    test_abort_send_terminal_fails();

    TEST_SECTION("Post-commit log and send_terminal");
    test_postcommit_log_null_params();
    test_postcommit_log_happy_path();
    test_send_terminal_subrequest();

    TEST_SECTION("Post-commit on_error terminal-only NGX_AGAIN regression");
    test_on_error_terminal_only_again_no_abort();

    printf("\n  All post-commit safety tests passed\n\n");
    return 0;
}
