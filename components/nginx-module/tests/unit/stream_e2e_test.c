/*
 * Test: stream_e2e
 *
 * E2E-style integration tests for the streaming fallback state machine
 * (streaming fallback state machine, tasks 8.1-8.6).  Unlike the unit tests in stream_error_test.c
 * which test individual functions, these tests exercise the COMPLETE flow
 * from context initialization through the error handler to final outcome.
 *
 * Each test simulates:
 *   1. Context initialization (stream_sm state, replay buffer)
 *   2. Replay buffer init + append (populating data)
 *   3. Error handler invocation (ngx_http_markdown_stream_on_error)
 *   4. Verification of final outcome (return code, state, side effects)
 *
 * Validates: Requirements 8.1-8.6
 */

#include "../include/test_common.h"

/* Pull in base NGINX types from stubs */
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

/* Additional defines needed for compilation */
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
 * Test state: track calls to mocked functions and their behaviour.
 */
static int mock_output_filter_called;
static int mock_output_filter_rc;
static ngx_chain_t *mock_output_filter_chain;

static int mock_safe_finish_called;
static int mock_safe_finish_rc;

static int mock_abort_called;

static int mock_replay_chain_called;
static ngx_chain_t *mock_replay_chain_result;

/* Track content sent through output filter to verify no HTML post-commit */
static u_char *mock_output_data;
static size_t  mock_output_data_len;

/* Mocked request infrastructure */
static ngx_log_t             e2e_log;
static ngx_connection_impl_t e2e_connection;
static ngx_http_request_t    e2e_request;

/* Sample HTML content for replay buffer */
static u_char e2e_html_data[] =
    "<html><body><h1>Hello World</h1><p>Test content</p></body></html>";
static size_t e2e_html_data_len = sizeof(e2e_html_data) - 1;

/* Function prototypes */
static void e2e_setup(void);
static void e2e_init_context_precommit(ngx_http_markdown_ctx_t *ctx,
                                        ngx_http_markdown_conf_t *conf,
                                        ngx_uint_t on_error_policy);
static void e2e_init_context_committed(ngx_http_markdown_ctx_t *ctx,
                                        ngx_http_markdown_conf_t *conf,
                                        ngx_uint_t on_error_policy);
static ngx_int_t e2e_stream_on_error(ngx_http_request_t *r,
                                      ngx_http_markdown_ctx_t *ctx,
                                      ngx_http_markdown_conf_t *conf);
static int e2e_data_contains_html(const u_char *data, size_t len);
static void test_8_1_precommit_fallback_html(void);
static void test_8_2_precommit_reject_502(void);
static void test_8_3_replay_buffer_overflow(void);
static void test_8_4_postcommit_safe_finish(void);
static void test_8_5_postcommit_abort(void);
static void test_8_6_no_mixed_markdown_html(void);


/*
 * Mock: ngx_http_output_filter
 *
 * Records the chain data passed through for content inspection.
 */
ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    UNUSED(r);
    mock_output_filter_called = 1;
    mock_output_filter_chain = in;

    /* Capture the data content for post-commit HTML detection */
    if (in != NULL && in->buf != NULL) {
        mock_output_data = in->buf->pos;
        mock_output_data_len = (size_t)(in->buf->last - in->buf->pos);
    }

    return mock_output_filter_rc;
}

/*
 * Mock: ngx_http_markdown_stream_postcommit_safe_finish
 *
 * Records the call and returns the configured return code.
 * Critically: does NOT output any HTML content.
 */
ngx_int_t
ngx_http_markdown_stream_postcommit_safe_finish(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(r); UNUSED(ctx);
    mock_safe_finish_called = 1;
    return mock_safe_finish_rc;
}

/*
 * Mock: ngx_http_markdown_stream_postcommit_abort
 *
 * Records the call.  Does NOT produce any content output.
 */
ngx_int_t
ngx_http_markdown_stream_postcommit_abort(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(r); UNUSED(ctx);
    mock_abort_called = 1;
    return NGX_OK;
}

/*
 * Mock: ngx_http_markdown_stream_replay_chain
 *
 * Builds a replay chain from the ctx replay buffer data.
 * This simulates the real function's behaviour of constructing
 * an output chain from buffered upstream bytes.
 */
ngx_chain_t *
ngx_http_markdown_stream_replay_chain(ngx_http_markdown_ctx_t *ctx,
                                       ngx_pool_t *pool)
{
    UNUSED(pool);
    mock_replay_chain_called = 1;

    if (ctx == NULL || !ctx->stream_sm.replay_initialized) {
        return NULL;
    }
    if (ctx->stream_sm.replay_buf.size == 0) {
        return NULL;
    }

    return mock_replay_chain_result;
}

/*
 * Mock: ngx_http_markdown_stream_replay_available
 *
 * Returns 1 if the replay buffer is initialized and has not overflowed.
 */
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
 * The full on_error flow -- mirrors the production implementation in
 * ngx_http_markdown_stream_error.c but uses our mocked dependencies.
 *
 * This is the function under test for the E2E integration tests.
 */
static ngx_int_t
e2e_stream_on_error(ngx_http_request_t *r,
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

    /* Step 1: Populate decision context from request state */
    dctx.current_state = ctx->stream_sm.state;
    dctx.replay_available = ngx_http_markdown_stream_replay_available(ctx);
    dctx.headers_committed = ctx->stream_sm.headers_committed;
    dctx.within_resource_limits = 1;
    dctx.on_error_policy = conf->stream.on_error;

    /* Step 2: Choose event based on committed state and policy */
    if (ctx->stream_sm.headers_committed) {
        event = NGX_HTTP_MD_EVENT_ERROR;
    } else if (conf->stream.on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS) {
        event = NGX_HTTP_MD_EVENT_ON_ERROR_PASS;
    } else {
        event = NGX_HTTP_MD_EVENT_ON_ERROR_REJECT;
    }

    /* Step 3: Call decision engine */
    decision = ngx_http_markdown_stream_decide(&dctx, event);

    /* Step 4: Update state machine */
    ctx->stream_sm.state = decision.new_state;

    /* Step 5: Execute action */
    switch (decision.action) {

    case NGX_HTTP_MD_ACTION_PASS_HTML:
        {
            ngx_chain_t *chain;
            chain = ngx_http_markdown_stream_replay_chain(ctx, r->pool);
            if (chain == NULL) {
                return NGX_ERROR;
            }
            r->headers_out.content_type_len = sizeof("text/html") - 1;
            ngx_str_set(&r->headers_out.content_type, "text/html");
            r->headers_out.content_type_lowcase = NULL;
            rc = ngx_http_output_filter(r, chain);
            if (rc == NGX_ERROR) {
                return NGX_ERROR;
            }
            return NGX_OK;
        }

    case NGX_HTTP_MD_ACTION_REJECT_502:
        return NGX_HTTP_BAD_GATEWAY;

    case NGX_HTTP_MD_ACTION_SAFE_FINISH:
        rc = ngx_http_markdown_stream_postcommit_safe_finish(r, ctx);
        if (rc == NGX_AGAIN) {
            return NGX_AGAIN;
        }
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


/*
 * Reset all mocks and test infrastructure.
 */
static void
e2e_setup(void)
{
    mock_output_filter_called = 0;
    mock_output_filter_rc = NGX_OK;
    mock_output_filter_chain = NULL;
    mock_safe_finish_called = 0;
    mock_safe_finish_rc = NGX_OK;
    mock_abort_called = 0;
    mock_replay_chain_called = 0;
    mock_replay_chain_result = NULL;
    mock_output_data = NULL;
    mock_output_data_len = 0;

    memset(&e2e_log, 0, sizeof(e2e_log));
    memset(&e2e_connection, 0, sizeof(e2e_connection));
    memset(&e2e_request, 0, sizeof(e2e_request));
    e2e_connection.log = &e2e_log;
    e2e_request.connection = &e2e_connection;
    e2e_request.main = &e2e_request;
    e2e_request.pool = NULL;
}


/*
 * Initialize a PRE_COMMIT context with replay buffer populated.
 *
 * Simulates the full initialization path:
 *   1. Set state to PRE_COMMIT
 *   2. Mark replay as initialized
 *   3. Populate replay buffer with sample HTML data
 *   4. Set the on_error policy in conf
 */
static void
e2e_init_context_precommit(ngx_http_markdown_ctx_t *ctx,
                            ngx_http_markdown_conf_t *conf,
                            ngx_uint_t on_error_policy)
{
    memset(ctx, 0, sizeof(*ctx));
    memset(conf, 0, sizeof(*conf));

    /* State machine: PRE_COMMIT, headers not committed */
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx->stream_sm.headers_committed = 0;

    /* Replay buffer: initialized with HTML content */
    ctx->stream_sm.replay_initialized = 1;
    ctx->stream_sm.replay_buf.data = e2e_html_data;
    ctx->stream_sm.replay_buf.size = e2e_html_data_len;
    ctx->stream_sm.replay_buf.capacity = 4096;
    ctx->stream_sm.replay_capacity = 4096;

    /* Configuration: streaming on_error policy (0.8.0 model) */
    conf->stream.on_error = on_error_policy;
}


/*
 * Initialize a COMMITTED context (headers already sent).
 *
 * Simulates the state after headers were committed:
 *   1. Set state to COMMITTED
 *   2. Mark headers_committed = 1
 *   3. Set on_error policy
 */
static void
e2e_init_context_committed(ngx_http_markdown_ctx_t *ctx,
                            ngx_http_markdown_conf_t *conf,
                            ngx_uint_t on_error_policy)
{
    memset(ctx, 0, sizeof(*ctx));
    memset(conf, 0, sizeof(*conf));

    /* State machine: COMMITTED, headers already sent */
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx->stream_sm.headers_committed = 1;

    /* No replay buffer needed post-commit */
    ctx->stream_sm.replay_initialized = 0;

    /* Configuration: streaming on_error policy (0.8.0 model) */
    conf->stream.on_error = on_error_policy;
}


/*
 * Helper: check if data contains HTML markers.
 *
 * Scans for common HTML tag patterns (<html, <body, <div, <p, etc.)
 * Used to verify post-commit invariant (no HTML in output).
 */
static int
e2e_data_contains_html(const u_char *data, size_t len)
{
    if (data == NULL || len == 0) {
        return 0;
    }

    for (size_t i = 0; i + 1 < len; i++) {
        if (data[i] == '<') {
            /* Check for opening HTML tags */
            if (i + 2 < len) {
                u_char c = data[i + 1];
                if (c == 'h' || c == 'H' ||
                    c == 'b' || c == 'B' ||
                    c == 'p' || c == 'P' ||
                    c == 'd' || c == 'D' ||
                    c == 's' || c == 'S' ||
                    c == 'a' || c == 'A' ||
                    c == '!') {
                    return 1;
                }
            }
        }
    }
    return 0;
}


/*
 * Task 8.1: Pre-commit fallback returns HTML (on_error=pass)
 *
 * Full E2E flow:
 *   Setup: PRE_COMMIT state, replay_initialized=1,
 *          buffer has HTML data, on_error=pass
 *   Action: Call stream_on_error
 *   Verify: Returns NGX_OK, content type restored to text/html,
 *           replay data sent, state=PASSTHROUGH
 *
 * Validates: Requirements 8.1
 */
static void
test_8_1_precommit_fallback_html(void)
{
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    ngx_chain_t               replay_chain;
    ngx_buf_t                 replay_buf;
    ngx_int_t                 rc;

    e2e_setup();

    /* Step 1: Initialize context -- PRE_COMMIT with replay buffer */
    e2e_init_context_precommit(&ctx, &conf, NGX_HTTP_MARKDOWN_ON_ERROR_PASS);

    /* Step 2: Set up replay chain (simulates real replay_chain build) */
    memset(&replay_buf, 0, sizeof(replay_buf));
    replay_buf.pos = e2e_html_data;
    replay_buf.last = e2e_html_data + e2e_html_data_len;
    replay_buf.start = e2e_html_data;
    replay_buf.end = e2e_html_data + e2e_html_data_len;
    replay_buf.memory = 1;
    replay_buf.last_buf = 1;

    memset(&replay_chain, 0, sizeof(replay_chain));
    replay_chain.buf = &replay_buf;
    replay_chain.next = NULL;
    mock_replay_chain_result = &replay_chain;

    /* Step 3: Invoke the full error handler flow */
    rc = e2e_stream_on_error(&e2e_request, &ctx, &conf);

    /* Step 4: Verify final outcome */
    TEST_ASSERT(rc == NGX_OK,
                "8.1: returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "8.1: final state is PASSTHROUGH");
    TEST_ASSERT(e2e_request.headers_out.content_type_len
                == sizeof("text/html") - 1,
                "8.1: Content-Type restored to text/html");
    TEST_ASSERT(mock_replay_chain_called == 1,
                "8.1: replay chain was built");
    TEST_ASSERT(mock_output_filter_called == 1,
                "8.1: output filter called to send replay data");
    TEST_ASSERT(mock_output_filter_chain == &replay_chain,
                "8.1: correct replay chain sent downstream");
    TEST_ASSERT(mock_safe_finish_called == 0,
                "8.1: no post-commit safe_finish");
    TEST_ASSERT(mock_abort_called == 0,
                "8.1: no post-commit abort");

    TEST_PASS("Task 8.1: Pre-commit fallback returns HTML (on_error=pass)");
}


/*
 * Task 8.2: Pre-commit reject returns 502
 *
 * Full E2E flow:
 *   Setup: PRE_COMMIT state, replay_initialized=1,
 *          buffer has data, on_error=reject
 *   Action: Call stream_on_error
 *   Verify: Returns NGX_HTTP_BAD_GATEWAY (502), state=PASSTHROUGH
 *
 * Validates: Requirements 8.2
 */
static void
test_8_2_precommit_reject_502(void)
{
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    ngx_int_t                 rc;

    e2e_setup();

    /* Step 1: Initialize context -- PRE_COMMIT with replay, on_error=reject */
    e2e_init_context_precommit(&ctx, &conf, NGX_HTTP_MARKDOWN_ON_ERROR_REJECT);

    /* Step 2: Invoke the full error handler flow */
    rc = e2e_stream_on_error(&e2e_request, &ctx, &conf);

    /* Step 3: Verify final outcome */
    TEST_ASSERT(rc == NGX_HTTP_BAD_GATEWAY,
                "8.2: returns 502 (NGX_HTTP_BAD_GATEWAY)");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "8.2: final state is PASSTHROUGH");
    TEST_ASSERT(mock_replay_chain_called == 0,
                "8.2: no replay chain built (rejected)");
    TEST_ASSERT(mock_output_filter_called == 0,
                "8.2: no output sent downstream");
    TEST_ASSERT(mock_safe_finish_called == 0,
                "8.2: no post-commit safe_finish");
    TEST_ASSERT(mock_abort_called == 0,
                "8.2: no post-commit abort");

    TEST_PASS("Task 8.2: Pre-commit reject returns 502");
}


/*
 * Task 8.3: Replay buffer overflow forces decision
 *
 * When the replay buffer overflows (size > capacity) in PRE_COMMIT
 * state, the decision engine enters PRE_COMMIT_REPLAY_UNAVAILABLE
 * semantics and forces a decision:
 *   - With resource limits available -> FULL_BUFFER_FALLBACK
 *   - Without resource limits -> REJECT_502
 *
 * This test exercises the decision engine directly (not through
 * on_error) because REPLAY_OVERFLOW is a distinct event from the
 * error handler events.
 *
 * Validates: Requirements 8.3
 */
static void
test_8_3_replay_buffer_overflow(void)
{
    ngx_http_markdown_stream_ctx_t  dctx;
    ngx_http_markdown_decision_t    decision;

    TEST_SUBSECTION("8.3a: Overflow with resource limits");
    {
        memset(&dctx, 0, sizeof(dctx));
        dctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
        dctx.replay_available = 0;   /* overflow: replay NOT available */
        dctx.headers_committed = 0;
        dctx.within_resource_limits = 1;
        dctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

        decision = ngx_http_markdown_stream_decide(
            &dctx, NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

        TEST_ASSERT(
            decision.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
            "8.3a: transitions to FULL_BUFFER_FALLBACK");
        TEST_ASSERT(
            decision.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
            "8.3a: action is SWITCH_FULL_BUFFER");
        TEST_ASSERT(
            decision.reason == NGX_HTTP_MD_REASON_REPLAY_OVERFLOW,
            "8.3a: reason is REPLAY_OVERFLOW");

        TEST_PASS("Task 8.3a: Overflow + resource limits = FULL_BUFFER_FALLBACK");
    }

    TEST_SUBSECTION("8.3b: Overflow without resource limits");
    {
        memset(&dctx, 0, sizeof(dctx));
        dctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
        dctx.replay_available = 0;   /* overflow: replay NOT available */
        dctx.headers_committed = 0;
        dctx.within_resource_limits = 0;  /* limits exceeded */
        dctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

        decision = ngx_http_markdown_stream_decide(
            &dctx, NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

        TEST_ASSERT(
            decision.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
            "8.3b: transitions to PASSTHROUGH");
        TEST_ASSERT(
            decision.action == NGX_HTTP_MD_ACTION_REJECT_502,
            "8.3b: action is REJECT_502");
        TEST_ASSERT(
            decision.reason == NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED,
            "8.3b: reason is RESOURCE_LIMIT_EXCEEDED");

        TEST_PASS("Task 8.3b: Overflow + no resource limits = REJECT_502");
    }
}


/*
 * Task 8.4: Post-commit safe-finish (no HTML mixed)
 *
 * Full E2E flow:
 *   Setup: COMMITTED state, headers_committed=1, on_error=pass
 *   Action: Call stream_on_error
 *   Verify: Returns NGX_OK, safe_finish executed,
 *           NO HTML in output, state=POST_COMMIT_SAFE_FINISH
 *
 * The critical safety property: after commit, the response MUST
 * NOT contain any HTML content.
 *
 * Validates: Requirements 8.4
 */
static void
test_8_4_postcommit_safe_finish(void)
{
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    ngx_int_t                 rc;

    e2e_setup();

    /* Step 1: Initialize context -- COMMITTED, on_error=pass */
    e2e_init_context_committed(&ctx, &conf, NGX_HTTP_MARKDOWN_ON_ERROR_PASS);
    mock_safe_finish_rc = NGX_OK;

    /* Step 2: Invoke the full error handler flow */
    rc = e2e_stream_on_error(&e2e_request, &ctx, &conf);

    /* Step 3: Verify final outcome */
    TEST_ASSERT(rc == NGX_OK,
                "8.4: returns NGX_OK (not 502)");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
                "8.4: final state is POST_COMMIT_SAFE_FINISH");
    TEST_ASSERT(mock_safe_finish_called == 1,
                "8.4: safe_finish was called");
    TEST_ASSERT(mock_abort_called == 0,
                "8.4: no abort (safe_finish succeeded)");

    /* Critical safety: no HTML was sent post-commit */
    TEST_ASSERT(mock_output_filter_called == 0,
                "8.4: no raw output filter call (no HTML replay)");
    TEST_ASSERT(mock_replay_chain_called == 0,
                "8.4: no replay chain built (post-commit)");

    /* Verify Content-Type was NOT changed to text/html */
    TEST_ASSERT(e2e_request.headers_out.content_type_len == 0,
                "8.4: Content-Type not set to HTML");

    TEST_PASS("Task 8.4: Post-commit safe-finish (no HTML mixed)");
}


/*
 * Task 8.5: Post-commit abort (no HTML mixed)
 *
 * Full E2E flow:
 *   Setup: COMMITTED state, headers_committed=1, on_error=reject
 *   Action: Call stream_on_error
 *   Verify: Returns NGX_OK (NOT 502!), abort executed,
 *           NO HTML in output, state=POST_COMMIT_ABORT
 *
 * Critical: even with on_error=reject, post-commit NEVER returns 502.
 * The abort provides a protocol-safe disconnect instead.
 *
 * Validates: Requirements 8.5
 */
static void
test_8_5_postcommit_abort(void)
{
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    ngx_int_t                 rc;

    e2e_setup();

    /* Step 1: Initialize context -- COMMITTED, on_error=reject */
    e2e_init_context_committed(&ctx, &conf, NGX_HTTP_MARKDOWN_ON_ERROR_REJECT);

    /* Step 2: Invoke the full error handler flow */
    rc = e2e_stream_on_error(&e2e_request, &ctx, &conf);

    /* Step 3: Verify final outcome */
    TEST_ASSERT(rc == NGX_OK,
                "8.5: returns NGX_OK (NOT 502 -- post-commit never rejects)");
    TEST_ASSERT(rc != NGX_HTTP_BAD_GATEWAY,
                "8.5: explicitly NOT 502");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
                "8.5: final state is POST_COMMIT_ABORT");
    TEST_ASSERT(mock_abort_called == 1,
                "8.5: abort was called");
    TEST_ASSERT(mock_safe_finish_called == 0,
                "8.5: no safe_finish (on_error=reject goes direct abort)");

    /* Critical safety: no HTML was sent post-commit */
    TEST_ASSERT(mock_output_filter_called == 0,
                "8.5: no raw output filter call (no HTML replay)");
    TEST_ASSERT(mock_replay_chain_called == 0,
                "8.5: no replay chain built (post-commit)");

    /* Verify Content-Type was NOT changed to text/html */
    TEST_ASSERT(e2e_request.headers_out.content_type_len == 0,
                "8.5: Content-Type not set to HTML");

    TEST_PASS("Task 8.5: Post-commit abort (no HTML mixed)");
}


/*
 * Task 8.6: No response contains mixed Markdown and HTML
 *
 * Property test: For all state/event/policy combinations where the
 * decision engine transitions to a post-commit state, verify that
 * the resulting action NEVER includes HTML content.
 *
 * We exhaustively test all combinations of:
 *   - States: COMMITTED, POST_COMMIT_SAFE_FINISH, POST_COMMIT_ABORT
 *   - Events: all defined events
 *   - Policies: PASS, REJECT
 *
 * For each combination that produces a post-commit state, we verify
 * the action is either SAFE_FINISH or ABORT (never PASS_HTML or
 * any action that would produce HTML output).
 *
 * Validates: Requirements 8.6
 */
static void
test_8_6_no_mixed_markdown_html(void)
{
    ngx_http_markdown_stream_ctx_t  dctx;
    ngx_http_markdown_decision_t    decision;
    ngx_uint_t                      policy;
    int                             total_checks;

    static const ngx_http_markdown_stream_event_e all_events[] = {
        NGX_HTTP_MD_EVENT_ERROR,
        NGX_HTTP_MD_EVENT_ON_ERROR_PASS,
        NGX_HTTP_MD_EVENT_ON_ERROR_REJECT,
        NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW,
        NGX_HTTP_MD_EVENT_RESOURCE_LIMIT,
        NGX_HTTP_MD_EVENT_PARSER_UNSUITABLE,
        NGX_HTTP_MD_EVENT_HARD_EXCLUDED,
        NGX_HTTP_MD_EVENT_FULL_DOC_FEATURE,
        NGX_HTTP_MD_EVENT_BUDGET_INIT_FAILURE,
        NGX_HTTP_MD_EVENT_ELIGIBLE,
        NGX_HTTP_MD_EVENT_NOT_ELIGIBLE,
        NGX_HTTP_MD_EVENT_STREAMING_START,
        NGX_HTTP_MD_EVENT_COMMIT,
        NGX_HTTP_MD_EVENT_STRICT_ETAG,
        NGX_HTTP_MD_EVENT_LOOK_BEHIND_OVERFLOW,
        NGX_HTTP_MD_EVENT_AUTO_RISK
    };
    static const size_t num_events =
        sizeof(all_events) / sizeof(all_events[0]);

    total_checks = 0;

    /*
     * Sweep all COMMITTED state x event x policy combinations.
     * The post-commit irreversibility property must hold for every one.
     */
    for (policy = 0; policy <= 1; policy++) {
        for (size_t e = 0; e < num_events; e++) {
            memset(&dctx, 0, sizeof(dctx));
            dctx.current_state = NGX_HTTP_MD_STATE_COMMITTED;
            dctx.replay_available = 0;
            dctx.headers_committed = 1;
            dctx.within_resource_limits = 1;
            dctx.on_error_policy = policy;

            decision = ngx_http_markdown_stream_decide(
                &dctx, all_events[e]);

            /* Post-commit irreversibility: NEVER PASS_HTML */
            TEST_ASSERT(
                decision.action != NGX_HTTP_MD_ACTION_PASS_HTML,
                "8.6: COMMITTED state NEVER produces PASS_HTML");

            /* Post-commit irreversibility: NEVER REJECT_502 */
            TEST_ASSERT(
                decision.action != NGX_HTTP_MD_ACTION_REJECT_502,
                "8.6: COMMITTED state NEVER produces REJECT_502");

            /* Verify valid post-commit terminal transitions */
            if (decision.new_state == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH
                || decision.new_state == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT) {
                TEST_ASSERT(
                    decision.action == NGX_HTTP_MD_ACTION_SAFE_FINISH
                    || decision.action == NGX_HTTP_MD_ACTION_ABORT,
                    "8.6: post-commit terminal action is safe_finish/abort");
            }

            total_checks++;
        }
    }

    /*
     * Also verify from the terminal post-commit states themselves.
     * Once in POST_COMMIT_SAFE_FINISH or POST_COMMIT_ABORT, any
     * event must stay safe.
     */
    for (policy = 0; policy <= 1; policy++) {
        for (size_t e = 0; e < num_events; e++) {
            /* POST_COMMIT_SAFE_FINISH */
            memset(&dctx, 0, sizeof(dctx));
            dctx.current_state =
                NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH;
            dctx.headers_committed = 1;
            dctx.on_error_policy = policy;

            decision = ngx_http_markdown_stream_decide(
                &dctx, all_events[e]);

            TEST_ASSERT(
                decision.action != NGX_HTTP_MD_ACTION_PASS_HTML,
                "8.6: POST_COMMIT_SAFE_FINISH NEVER produces HTML");
            TEST_ASSERT(
                decision.action != NGX_HTTP_MD_ACTION_REJECT_502,
                "8.6: POST_COMMIT_SAFE_FINISH NEVER produces 502");

            /* POST_COMMIT_ABORT */
            memset(&dctx, 0, sizeof(dctx));
            dctx.current_state = NGX_HTTP_MD_STATE_POST_COMMIT_ABORT;
            dctx.headers_committed = 1;
            dctx.on_error_policy = policy;

            decision = ngx_http_markdown_stream_decide(
                &dctx, all_events[e]);

            TEST_ASSERT(
                decision.action != NGX_HTTP_MD_ACTION_PASS_HTML,
                "8.6: POST_COMMIT_ABORT NEVER produces HTML");
            TEST_ASSERT(
                decision.action != NGX_HTTP_MD_ACTION_REJECT_502,
                "8.6: POST_COMMIT_ABORT NEVER produces 502");

            total_checks += 2;
        }
    }

    /* Also test via E2E flow: post-commit on_error never sends HTML */
    {
        ngx_http_markdown_ctx_t   ctx;
        ngx_http_markdown_conf_t  conf;
        ngx_int_t                 rc;

        /* on_error=pass post-commit */
        e2e_setup();
        e2e_init_context_committed(
            &ctx, &conf, NGX_HTTP_MARKDOWN_ON_ERROR_PASS);
        mock_safe_finish_rc = NGX_OK;
        rc = e2e_stream_on_error(&e2e_request, &ctx, &conf);
        TEST_ASSERT(rc == NGX_OK, "8.6 E2E pass: returns NGX_OK");
        TEST_ASSERT(
            mock_output_data == NULL
            || !e2e_data_contains_html(mock_output_data,
                                        mock_output_data_len),
            "8.6 E2E pass: no HTML in output data");
        total_checks++;

        /* on_error=reject post-commit */
        e2e_setup();
        e2e_init_context_committed(
            &ctx, &conf, NGX_HTTP_MARKDOWN_ON_ERROR_REJECT);
        rc = e2e_stream_on_error(&e2e_request, &ctx, &conf);
        TEST_ASSERT(rc == NGX_OK, "8.6 E2E reject: returns NGX_OK (not 502)");
        TEST_ASSERT(
            mock_output_data == NULL
            || !e2e_data_contains_html(mock_output_data,
                                        mock_output_data_len),
            "8.6 E2E reject: no HTML in output data");
        total_checks++;
    }

    printf("    (%d state/event/policy combinations verified)\n",
           total_checks);
    TEST_PASS("Task 8.6: No response contains mixed Markdown and HTML");
}


int
main(void)
{
    TEST_SECTION("Stream E2E Integration Tests (Spec 37, Tasks 8.1-8.6)");

    test_8_1_precommit_fallback_html();
    test_8_2_precommit_reject_502();
    test_8_3_replay_buffer_overflow();
    test_8_4_postcommit_safe_finish();
    test_8_5_postcommit_abort();
    test_8_6_no_mixed_markdown_html();

    printf("\n  All stream E2E integration tests passed\n\n");
    return 0;
}
