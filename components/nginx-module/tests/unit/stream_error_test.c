/*
 * Test: stream_error
 *
 * Validates the streaming error handler integration module (spec 37,
 * tasks 6.1-6.4):
 *
 * 6.1: Pre-commit + on_error=pass  -> PASS_HTML (replay)
 * 6.2: Pre-commit + on_error=reject -> NGX_HTTP_BAD_GATEWAY
 * 6.3: Post-commit + on_error=pass  -> safe_finish (abort fallback)
 * 6.4: Post-commit + on_error=reject -> abort
 */

#include "../include/test_common.h"

/* Pull in base NGINX types from stubs */
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

/* Additional defines */
#ifndef NGX_HTTP_BAD_GATEWAY
#define NGX_HTTP_BAD_GATEWAY  502
#endif

#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
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

/*
 * Test state: track calls to mocked functions.
 */
static int test_output_filter_called;
static int test_output_filter_rc;
static int test_safe_finish_called;
static int test_safe_finish_rc;
static int test_abort_called;
static int test_replay_chain_called;
static ngx_chain_t *test_replay_chain_result;

/* Mocked request infrastructure */
static ngx_log_t             test_log;
static ngx_connection_impl_t test_connection;
static ngx_http_request_t    test_request;

/* Function prototypes */
static void test_setup(void);
static void test_task_6_1_precommit_pass_replay_html(void);
static void test_task_6_2_precommit_reject_502(void);
static void test_task_6_3_postcommit_pass_safe_finish(void);
static void test_task_6_3_postcommit_pass_safe_finish_fails(void);
static void test_task_6_4_postcommit_reject_abort(void);
static void test_null_parameters(void);
static void test_passthrough_state(void);
static ngx_int_t test_stream_on_error(ngx_http_request_t *r,
                                       ngx_http_markdown_ctx_t *ctx,
                                       ngx_http_markdown_conf_t *conf);

/* Mock: ngx_http_output_filter */
ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    UNUSED(r); UNUSED(in);
    test_output_filter_called = 1;
    return test_output_filter_rc;
}

/* Mock: safe_finish */
ngx_int_t
ngx_http_markdown_stream_postcommit_safe_finish(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(r); UNUSED(ctx);
    test_safe_finish_called = 1;
    return test_safe_finish_rc;
}

/* Mock: abort */
ngx_int_t
ngx_http_markdown_stream_postcommit_abort(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(r); UNUSED(ctx);
    test_abort_called = 1;
    return NGX_OK;
}

/* Mock: replay_chain */
ngx_chain_t *
ngx_http_markdown_stream_replay_chain(ngx_http_markdown_ctx_t *ctx,
                                       ngx_pool_t *pool)
{
    UNUSED(ctx); UNUSED(pool);
    test_replay_chain_called = 1;
    return test_replay_chain_result;
}

/* Mock: replay_available */
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

/* Stub: ngx_log_error_core */
void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log,
                   ngx_err_t err, const char *fmt, ...)
{
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);
}

/*
 * Test implementation of ngx_http_markdown_stream_on_error.
 */
static ngx_int_t
test_stream_on_error(ngx_http_request_t *r,
                     ngx_http_markdown_ctx_t *ctx,
                     ngx_http_markdown_conf_t *conf)
{
    ngx_http_markdown_stream_ctx_t    dctx;
    ngx_http_markdown_stream_event_e  event;
    ngx_http_markdown_decision_t      decision;
    ngx_int_t                         rc;

    if (r == NULL || ctx == NULL || conf == NULL) {
        return NGX_ERROR;
    }

    dctx.current_state = ctx->stream_sm.state;
    dctx.replay_available = ngx_http_markdown_stream_replay_available(ctx);
    dctx.headers_committed = ctx->stream_sm.headers_committed;
    dctx.within_resource_limits = 1;
    dctx.on_error_policy = conf->on_error;

    if (ctx->stream_sm.headers_committed) {
        event = NGX_HTTP_MD_EVENT_ERROR;
    } else if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS) {
        event = NGX_HTTP_MD_EVENT_ON_ERROR_PASS;
    } else {
        event = NGX_HTTP_MD_EVENT_ON_ERROR_REJECT;
    }

    decision = ngx_http_markdown_stream_decide(&dctx, event);
    ctx->stream_sm.state = decision.new_state;

    switch (decision.action) {
    case NGX_HTTP_MD_ACTION_PASS_HTML:
        {
            ngx_chain_t *chain = ngx_http_markdown_stream_replay_chain(ctx, r->pool);
            if (chain == NULL) { return NGX_ERROR; }
            r->headers_out.content_type_len = sizeof("text/html") - 1;
            ngx_str_set(&r->headers_out.content_type, "text/html");
            r->headers_out.content_type_lowcase = NULL;
            rc = ngx_http_output_filter(r, chain);
            if (rc == NGX_ERROR) { return NGX_ERROR; }
            return NGX_OK;
        }
    case NGX_HTTP_MD_ACTION_REJECT_502:
        return NGX_HTTP_BAD_GATEWAY;
    case NGX_HTTP_MD_ACTION_SAFE_FINISH:
        rc = ngx_http_markdown_stream_postcommit_safe_finish(r, ctx);
        if (rc != NGX_OK) {
            ngx_http_markdown_stream_postcommit_abort(r, ctx);
        }
        return NGX_OK;
    case NGX_HTTP_MD_ACTION_ABORT:
        ngx_http_markdown_stream_postcommit_abort(r, ctx);
        return NGX_OK;
    case NGX_HTTP_MD_ACTION_PASSTHROUGH:
        return NGX_OK;
    default:
        return NGX_OK;
    }
}


static void test_setup(void)
{
    test_output_filter_called = 0;
    test_output_filter_rc = NGX_OK;
    test_safe_finish_called = 0;
    test_safe_finish_rc = NGX_OK;
    test_abort_called = 0;
    test_replay_chain_called = 0;
    test_replay_chain_result = NULL;
    memset(&test_log, 0, sizeof(test_log));
    memset(&test_connection, 0, sizeof(test_connection));
    memset(&test_request, 0, sizeof(test_request));
    test_connection.log = &test_log;
    test_request.connection = &test_connection;
    test_request.main = &test_request;
    test_request.pool = NULL;
}

static void test_task_6_1_precommit_pass_replay_html(void)
{
    ngx_http_markdown_ctx_t ctx; ngx_http_markdown_conf_t conf;
    ngx_chain_t fc; ngx_buf_t fb; ngx_int_t rc;
    test_setup();
    memset(&ctx, 0, sizeof(ctx)); memset(&conf, 0, sizeof(conf));
    memset(&fc, 0, sizeof(fc)); memset(&fb, 0, sizeof(fb));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    fc.buf = &fb; fc.next = NULL;
    test_replay_chain_result = &fc;
    rc = test_stream_on_error(&test_request, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "6.1: returns NGX_OK");
    TEST_ASSERT(test_replay_chain_called == 1, "6.1: replay_chain called");
    TEST_ASSERT(test_output_filter_called == 1, "6.1: output_filter called");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PASSTHROUGH, "6.1: state PASSTHROUGH");
    TEST_ASSERT(test_request.headers_out.content_type_len == sizeof("text/html") - 1, "6.1: CT=text/html");
    TEST_ASSERT(test_safe_finish_called == 0, "6.1: no safe_finish");
    TEST_ASSERT(test_abort_called == 0, "6.1: no abort");
    TEST_PASS("Task 6.1: pre-commit + pass = replay HTML");
}

static void test_task_6_2_precommit_reject_502(void)
{
    ngx_http_markdown_ctx_t ctx; ngx_http_markdown_conf_t conf; ngx_int_t rc;
    test_setup();
    memset(&ctx, 0, sizeof(ctx)); memset(&conf, 0, sizeof(conf));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    rc = test_stream_on_error(&test_request, &ctx, &conf);
    TEST_ASSERT(rc == NGX_HTTP_BAD_GATEWAY, "6.2: returns 502");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PASSTHROUGH, "6.2: state PASSTHROUGH");
    TEST_ASSERT(test_replay_chain_called == 0, "6.2: no replay");
    TEST_ASSERT(test_output_filter_called == 0, "6.2: no output_filter");
    TEST_ASSERT(test_safe_finish_called == 0, "6.2: no safe_finish");
    TEST_ASSERT(test_abort_called == 0, "6.2: no abort");
    TEST_PASS("Task 6.2: pre-commit + reject = 502");
}

static void test_task_6_3_postcommit_pass_safe_finish(void)
{
    ngx_http_markdown_ctx_t ctx; ngx_http_markdown_conf_t conf; ngx_int_t rc;
    test_setup();
    memset(&ctx, 0, sizeof(ctx)); memset(&conf, 0, sizeof(conf));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    test_safe_finish_rc = NGX_OK;
    rc = test_stream_on_error(&test_request, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "6.3: returns NGX_OK");
    TEST_ASSERT(test_safe_finish_called == 1, "6.3: safe_finish called");
    TEST_ASSERT(test_abort_called == 0, "6.3: no abort");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH, "6.3: state");
    TEST_ASSERT(test_replay_chain_called == 0, "6.3: no replay");
    TEST_PASS("Task 6.3: post-commit + pass = safe_finish");
}

static void test_task_6_3_postcommit_pass_safe_finish_fails(void)
{
    ngx_http_markdown_ctx_t ctx; ngx_http_markdown_conf_t conf; ngx_int_t rc;
    test_setup();
    memset(&ctx, 0, sizeof(ctx)); memset(&conf, 0, sizeof(conf));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    test_safe_finish_rc = NGX_ERROR;
    rc = test_stream_on_error(&test_request, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "6.3f: returns NGX_OK");
    TEST_ASSERT(test_safe_finish_called == 1, "6.3f: safe_finish called");
    TEST_ASSERT(test_abort_called == 1, "6.3f: abort fallback");
    TEST_PASS("Task 6.3: safe_finish fails -> abort");
}

static void test_task_6_4_postcommit_reject_abort(void)
{
    ngx_http_markdown_ctx_t ctx; ngx_http_markdown_conf_t conf; ngx_int_t rc;
    test_setup();
    memset(&ctx, 0, sizeof(ctx)); memset(&conf, 0, sizeof(conf));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    rc = test_stream_on_error(&test_request, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "6.4: returns NGX_OK (NOT 502)");
    TEST_ASSERT(test_abort_called == 1, "6.4: abort called");
    TEST_ASSERT(test_safe_finish_called == 0, "6.4: no safe_finish");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT, "6.4: state");
    TEST_ASSERT(test_replay_chain_called == 0, "6.4: no replay");
    TEST_PASS("Task 6.4: post-commit + reject = abort");
}

static void test_null_parameters(void)
{
    ngx_http_markdown_ctx_t ctx; ngx_http_markdown_conf_t conf;
    test_setup();
    memset(&ctx, 0, sizeof(ctx)); memset(&conf, 0, sizeof(conf));
    TEST_ASSERT(test_stream_on_error(NULL, &ctx, &conf) == NGX_ERROR, "null r");
    TEST_ASSERT(test_stream_on_error(&test_request, NULL, &conf) == NGX_ERROR, "null ctx");
    TEST_ASSERT(test_stream_on_error(&test_request, &ctx, NULL) == NGX_ERROR, "null conf");
    TEST_PASS("Null parameter validation");
}

static void test_passthrough_state(void)
{
    ngx_http_markdown_ctx_t ctx; ngx_http_markdown_conf_t conf; ngx_int_t rc;
    test_setup();
    memset(&ctx, 0, sizeof(ctx)); memset(&conf, 0, sizeof(conf));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PASSTHROUGH;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    rc = test_stream_on_error(&test_request, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "passthrough: NGX_OK");
    TEST_ASSERT(test_replay_chain_called == 0, "passthrough: no replay");
    TEST_ASSERT(test_safe_finish_called == 0, "passthrough: no safe_finish");
    TEST_ASSERT(test_abort_called == 0, "passthrough: no abort");
    TEST_PASS("Passthrough (terminal) state");
}

int main(void)
{
    TEST_SECTION("Stream Error Handler (Spec 37, Tasks 6.1-6.4)");
    test_task_6_1_precommit_pass_replay_html();
    test_task_6_2_precommit_reject_502();
    test_task_6_3_postcommit_pass_safe_finish();
    test_task_6_3_postcommit_pass_safe_finish_fails();
    test_task_6_4_postcommit_reject_abort();
    test_null_parameters();
    test_passthrough_state();
    printf("\n  All stream error handler tests passed\n\n");
    return 0;
}
