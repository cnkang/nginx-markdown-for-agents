/*
 * Test: stream_postcommit
 *
 * Validates the post-commit safety property for the streaming
 * fallback state machine (spec 37, task 7.4).
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
 * Validates: Requirements 5.1, 5.2
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
};

/* Include the module header for types */
#include "../../src/ngx_http_markdown_filter_module.h"

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

/* Mock: ngx_http_output_filter (needed by postcommit) */
ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    UNUSED(r); UNUSED(in);
    return NGX_OK;
}

/* Mock: ngx_calloc_buf */
ngx_buf_t *
ngx_calloc_buf(ngx_pool_t *pool)
{
    UNUSED(pool);
    static ngx_buf_t mock_buf;
    memset(&mock_buf, 0, sizeof(mock_buf));
    return &mock_buf;
}

/* Stub: ngx_log_error_core */
void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log,
                   ngx_err_t err, const char *fmt, ...)
{
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);
}

/* Include the postcommit source (for guard function) */
#include "../../src/ngx_http_markdown_stream_postcommit.c"


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


int main(void)
{
    TEST_SECTION("Post-commit Safety Property (Spec 37, Task 7.4)");

    test_committed_never_produces_html();
    test_post_commit_terminals_never_produce_html();
    test_guard_passes_non_html();
    test_guard_fails_doctype();
    test_guard_fails_html_tag();
    test_guard_passes_non_committed_state();
    test_guard_null_params();

    printf("\n  All post-commit safety tests passed\n\n");
    return 0;
}
