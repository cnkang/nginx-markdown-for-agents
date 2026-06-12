/*
 * Test: stream_state
 *
 * Validates all state transitions in the streaming fallback decision
 * engine (streaming fallback state machine, task 7.1).
 *
 * Exercises every valid state transition path through the pure-function
 * decision engine ngx_http_markdown_stream_decide().
 */

#include "../include/test_common.h"

/* Pull in base NGINX types from stubs */
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

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

/* Stub: ngx_log_error_core */
void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log,
                   ngx_err_t err, const char *fmt, ...)
{
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);
}


/* --- NOT_ELIGIBLE transitions --- */

static void test_not_eligible_to_streaming_candidate(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_NOT_ELIGIBLE;

    d = ngx_http_markdown_stream_decide(&ctx, NGX_HTTP_MD_EVENT_ELIGIBLE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_STREAMING_CANDIDATE,
                "NOT_ELIGIBLE + ELIGIBLE -> STREAMING_CANDIDATE");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_NONE,
                "action = NONE");
    TEST_ASSERT(d.reason == NGX_HTTP_MD_REASON_ELIGIBLE,
                "reason = ELIGIBLE");
    TEST_PASS("NOT_ELIGIBLE -> STREAMING_CANDIDATE (on ELIGIBLE)");
}

static void test_not_eligible_to_passthrough(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_NOT_ELIGIBLE;

    d = ngx_http_markdown_stream_decide(&ctx, NGX_HTTP_MD_EVENT_NOT_ELIGIBLE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "NOT_ELIGIBLE + NOT_ELIGIBLE -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("NOT_ELIGIBLE -> PASSTHROUGH (on NOT_ELIGIBLE)");
}

/* --- STREAMING_CANDIDATE transitions --- */

static void test_candidate_to_pre_commit(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_STREAMING_START);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PRE_COMMIT,
                "STREAMING_CANDIDATE + STREAMING_START -> PRE_COMMIT");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_CONTINUE_STREAMING,
                "action = CONTINUE_STREAMING");
    TEST_PASS("STREAMING_CANDIDATE -> PRE_COMMIT (on STREAMING_START)");
}

static void test_candidate_to_full_buffer_with_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;
    ctx.within_resource_limits = 1;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_STRICT_ETAG);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                "STREAMING_CANDIDATE + STRICT_ETAG (limits) -> FULL_BUFFER");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                "action = SWITCH_FULL_BUFFER");
    TEST_PASS("STREAMING_CANDIDATE -> FULL_BUFFER_FALLBACK (STRICT_ETAG + limits)");
}

static void test_candidate_to_passthrough_no_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;
    ctx.within_resource_limits = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_STRICT_ETAG);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "STREAMING_CANDIDATE + STRICT_ETAG (no limits) -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("STREAMING_CANDIDATE -> PASSTHROUGH (STRICT_ETAG, no limits)");
}

/* --- PRE_COMMIT transitions --- */

static void test_pre_commit_to_passthrough_pass_html(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_PARSER_UNSUITABLE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "PRE_COMMIT + PARSER_UNSUITABLE (replay) -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASS_HTML,
                "action = PASS_HTML");
    TEST_PASS("PRE_COMMIT -> PASSTHROUGH/PASS_HTML (parser unsuitable)");
}

static void test_pre_commit_hard_excluded_pass_html(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_HARD_EXCLUDED);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "PRE_COMMIT + HARD_EXCLUDED -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASS_HTML,
                "action = PASS_HTML");
    TEST_PASS("PRE_COMMIT -> PASSTHROUGH/PASS_HTML (hard excluded)");
}

static void test_pre_commit_budget_failure_pass_html(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_BUDGET_INIT_FAILURE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "PRE_COMMIT + BUDGET_INIT_FAILURE -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASS_HTML,
                "action = PASS_HTML");
    TEST_PASS("PRE_COMMIT -> PASSTHROUGH/PASS_HTML (budget failure)");
}

static void test_pre_commit_to_committed(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx, NGX_HTTP_MD_EVENT_COMMIT);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_COMMITTED,
                "PRE_COMMIT + COMMIT -> COMMITTED");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_COMMIT_HEADERS,
                "action = COMMIT_HEADERS");
    TEST_PASS("PRE_COMMIT -> COMMITTED (on COMMIT)");
}

static void test_pre_commit_to_full_buffer(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;
    ctx.within_resource_limits = 1;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_FULL_DOC_FEATURE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                "PRE_COMMIT + FULL_DOC_FEATURE (limits) -> FULL_BUFFER");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                "action = SWITCH_FULL_BUFFER");
    TEST_PASS("PRE_COMMIT -> FULL_BUFFER_FALLBACK (full-doc feature)");
}

/* --- PRE_COMMIT_REPLAY_UNAVAILABLE transitions --- */

static void test_pre_commit_replay_unavail_to_committed(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE;
    ctx.replay_available = 0;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx, NGX_HTTP_MD_EVENT_COMMIT);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_COMMITTED,
                "PRE_COMMIT_REPLAY_UNAVAILABLE + COMMIT -> COMMITTED");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_COMMIT_HEADERS,
                "action = COMMIT_HEADERS");
    TEST_PASS("PRE_COMMIT_REPLAY_UNAVAILABLE -> COMMITTED (on COMMIT)");
}

static void test_pre_commit_replay_unavail_overflow_with_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE;
    ctx.replay_available = 0;
    ctx.headers_committed = 0;
    ctx.within_resource_limits = 1;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                "REPLAY_UNAVAILABLE + OVERFLOW (limits) -> FULL_BUFFER");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                "action = SWITCH_FULL_BUFFER");
    TEST_PASS("PRE_COMMIT_REPLAY_UNAVAILABLE -> FULL_BUFFER (OVERFLOW + limits)");
}

static void test_pre_commit_replay_unavail_overflow_no_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE;
    ctx.replay_available = 0;
    ctx.headers_committed = 0;
    ctx.within_resource_limits = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "REPLAY_UNAVAILABLE + OVERFLOW (no limits) -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_REJECT_502,
                "action = REJECT_502");
    TEST_PASS("PRE_COMMIT_REPLAY_UNAVAILABLE -> PASSTHROUGH/REJECT_502");
}

/* --- COMMITTED transitions --- */

static void test_committed_error_pass_to_safe_finish(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    d = ngx_http_markdown_stream_decide(&ctx, NGX_HTTP_MD_EVENT_ERROR);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
                "COMMITTED + ERROR (pass) -> POST_COMMIT_SAFE_FINISH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SAFE_FINISH,
                "action = SAFE_FINISH");
    TEST_PASS("COMMITTED -> POST_COMMIT_SAFE_FINISH (ERROR + pass)");
}

static void test_committed_error_reject_to_abort(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;

    d = ngx_http_markdown_stream_decide(&ctx, NGX_HTTP_MD_EVENT_ERROR);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
                "COMMITTED + ERROR (reject) -> POST_COMMIT_ABORT");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_ABORT,
                "action = ABORT");
    TEST_PASS("COMMITTED -> POST_COMMIT_ABORT (ERROR + reject)");
}

static void test_committed_non_error_stays(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_STREAMING_START);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_COMMITTED,
                "COMMITTED + non-error -> stays COMMITTED");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_CONTINUE_STREAMING,
                "action = CONTINUE_STREAMING");
    TEST_PASS("COMMITTED -> COMMITTED (non-error events)");
}

/* --- PASSTHROUGH terminal --- */

static void test_passthrough_terminal(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PASSTHROUGH;

    d = ngx_http_markdown_stream_decide(&ctx, NGX_HTTP_MD_EVENT_COMMIT);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "PASSTHROUGH + any event -> stays PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("PASSTHROUGH -> PASSTHROUGH (terminal, any event)");
}

/* --- POST_COMMIT terminals --- */

static void test_post_commit_safe_finish_terminal(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH;

    d = ngx_http_markdown_stream_decide(&ctx, NGX_HTTP_MD_EVENT_ERROR);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
                "POST_COMMIT_SAFE_FINISH stays terminal");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SAFE_FINISH,
                "action = SAFE_FINISH");
    TEST_PASS("POST_COMMIT_SAFE_FINISH -> terminal");
}

static void test_post_commit_abort_terminal(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_POST_COMMIT_ABORT;

    d = ngx_http_markdown_stream_decide(&ctx, NGX_HTTP_MD_EVENT_ERROR);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
                "POST_COMMIT_ABORT stays terminal");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_ABORT,
                "action = ABORT");
    TEST_PASS("POST_COMMIT_ABORT -> terminal");
}

/* --- NULL context --- */

static void test_null_ctx_passthrough(void)
{
    ngx_http_markdown_decision_t d;

    d = ngx_http_markdown_stream_decide(NULL, NGX_HTTP_MD_EVENT_ELIGIBLE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "NULL ctx -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("NULL ctx -> PASSTHROUGH");
}

/* --- PRE_COMMIT delegating to replay-unavailable --- */

static void test_pre_commit_no_replay_delegates(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 0;
    ctx.headers_committed = 0;
    ctx.within_resource_limits = 1;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                "PRE_COMMIT (no replay) + OVERFLOW -> FULL_BUFFER");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                "action = SWITCH_FULL_BUFFER");
    TEST_PASS("PRE_COMMIT (no replay) delegates to replay-unavailable");
}

/* --- Additional coverage tests --- */

static void test_not_eligible_default_event(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_NOT_ELIGIBLE;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_STREAMING_START);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "NOT_ELIGIBLE + default -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("NOT_ELIGIBLE + default event -> PASSTHROUGH");
}

static void test_candidate_default_event(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_NOT_ELIGIBLE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "STREAMING_CANDIDATE + default -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("STREAMING_CANDIDATE + default event -> PASSTHROUGH");
}

static void test_candidate_look_behind_overflow_with_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;
    ctx.within_resource_limits = 1;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_LOOK_BEHIND_OVERFLOW);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                "CANDIDATE + LOOK_BEHIND (limits) -> FULL_BUFFER");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                "action = SWITCH_FULL_BUFFER");
    TEST_PASS("CANDIDATE + LOOK_BEHIND_OVERFLOW (within limits)");
}

static void test_candidate_auto_risk_with_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;
    ctx.within_resource_limits = 1;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_AUTO_RISK);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                "CANDIDATE + AUTO_RISK (limits) -> FULL_BUFFER");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                "action = SWITCH_FULL_BUFFER");
    TEST_PASS("CANDIDATE + AUTO_RISK (within limits)");
}

static void test_candidate_full_doc_feature_with_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;
    ctx.within_resource_limits = 1;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_FULL_DOC_FEATURE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                "CANDIDATE + FULL_DOC (limits) -> FULL_BUFFER");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                "action = SWITCH_FULL_BUFFER");
    TEST_PASS("CANDIDATE + FULL_DOC_FEATURE (within limits)");
}

static void test_candidate_look_behind_overflow_no_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;
    ctx.within_resource_limits = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_LOOK_BEHIND_OVERFLOW);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "CANDIDATE + LOOK_BEHIND (no limits) -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("CANDIDATE + LOOK_BEHIND_OVERFLOW (exceeded limits)");
}

static void test_candidate_auto_risk_no_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;
    ctx.within_resource_limits = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_AUTO_RISK);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "CANDIDATE + AUTO_RISK (no limits) -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("CANDIDATE + AUTO_RISK (exceeded limits)");
}

static void test_candidate_full_doc_feature_no_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;
    ctx.within_resource_limits = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_FULL_DOC_FEATURE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "CANDIDATE + FULL_DOC (no limits) -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("CANDIDATE + FULL_DOC_FEATURE (exceeded limits)");
}

/* --- PRE_COMMIT additional transitions --- */

static void test_pre_commit_on_error_pass(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_ON_ERROR_PASS);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "PRE_COMMIT + ON_ERROR_PASS -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASS_HTML,
                "action = PASS_HTML");
    TEST_PASS("PRE_COMMIT + ON_ERROR_PASS -> PASS_HTML");
}

static void test_pre_commit_on_error_reject(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_ON_ERROR_REJECT);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "PRE_COMMIT + ON_ERROR_REJECT -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_REJECT_502,
                "action = REJECT_502");
    TEST_PASS("PRE_COMMIT + ON_ERROR_REJECT -> REJECT_502");
}

static void test_pre_commit_resource_limit(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_RESOURCE_LIMIT);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "PRE_COMMIT + RESOURCE_LIMIT -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("PRE_COMMIT + RESOURCE_LIMIT -> PASSTHROUGH");
}

static void test_pre_commit_default_event(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_NOT_ELIGIBLE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "PRE_COMMIT + default -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("PRE_COMMIT + default event -> PASSTHROUGH");
}

static void test_pre_commit_strict_etag_with_limits(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 0;
    ctx.within_resource_limits = 1;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_STRICT_ETAG);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                "PRE_COMMIT + STRICT_ETAG (limits) -> FULL_BUFFER");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                "action = SWITCH_FULL_BUFFER");
    TEST_PASS("PRE_COMMIT + STRICT_ETAG (within limits)");
}

static void test_pre_commit_headers_committed_passthrough(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.replay_available = 1;
    ctx.headers_committed = 1;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_PARSER_UNSUITABLE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "PRE_COMMIT + headers_committed -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH (fallthrough)");
    TEST_PASS("PRE_COMMIT with headers_committed falls through");
}

/* --- PRE_COMMIT_REPLAY_UNAVAILABLE additional --- */

static void test_pre_commit_replay_unavail_resource_limit(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE;
    ctx.replay_available = 0;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_RESOURCE_LIMIT);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "REPLAY_UNAVAIL + RESOURCE_LIMIT -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_REJECT_502,
                "action = REJECT_502");
    TEST_PASS("REPLAY_UNAVAILABLE + RESOURCE_LIMIT -> REJECT_502");
}

static void test_pre_commit_replay_unavail_default_event(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE;
    ctx.replay_available = 0;
    ctx.headers_committed = 0;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_NOT_ELIGIBLE);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "REPLAY_UNAVAIL + default -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_REJECT_502,
                "action = REJECT_502");
    TEST_PASS("REPLAY_UNAVAILABLE + default -> REJECT_502");
}

/* --- FULL_BUFFER transitions --- */

static void test_full_buffer_resource_limit(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_RESOURCE_LIMIT);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "FULL_BUFFER + RESOURCE_LIMIT -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("FULL_BUFFER + RESOURCE_LIMIT -> PASSTHROUGH");
}

static void test_full_buffer_other_event(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_STREAMING_START);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                "FULL_BUFFER + other -> stays FULL_BUFFER");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                "action = SWITCH_FULL_BUFFER");
    TEST_PASS("FULL_BUFFER + non-limit event stays FULL_BUFFER");
}

/* --- Unknown state default --- */

static void test_unknown_state_passthrough(void)
{
    ngx_http_markdown_stream_ctx_t ctx;
    ngx_http_markdown_decision_t   d;

    memset(&ctx, 0, sizeof(ctx));
    ctx.current_state = (ngx_http_markdown_stream_state_e) 9999;

    d = ngx_http_markdown_stream_decide(&ctx,
            NGX_HTTP_MD_EVENT_COMMIT);

    TEST_ASSERT(d.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "unknown state -> PASSTHROUGH");
    TEST_ASSERT(d.action == NGX_HTTP_MD_ACTION_PASSTHROUGH,
                "action = PASSTHROUGH");
    TEST_PASS("Unknown state -> safe PASSTHROUGH");
}


int main(void)
{
    TEST_SECTION("Stream State Transitions (Spec 37, Task 7.1)");

    test_not_eligible_to_streaming_candidate();
    test_not_eligible_to_passthrough();
    test_not_eligible_default_event();
    test_candidate_to_pre_commit();
    test_candidate_to_full_buffer_with_limits();
    test_candidate_to_passthrough_no_limits();
    test_candidate_default_event();
    test_candidate_look_behind_overflow_with_limits();
    test_candidate_auto_risk_with_limits();
    test_candidate_full_doc_feature_with_limits();
    test_candidate_look_behind_overflow_no_limits();
    test_candidate_auto_risk_no_limits();
    test_candidate_full_doc_feature_no_limits();
    test_pre_commit_to_passthrough_pass_html();
    test_pre_commit_hard_excluded_pass_html();
    test_pre_commit_budget_failure_pass_html();
    test_pre_commit_to_committed();
    test_pre_commit_to_full_buffer();
    test_pre_commit_on_error_pass();
    test_pre_commit_on_error_reject();
    test_pre_commit_resource_limit();
    test_pre_commit_default_event();
    test_pre_commit_strict_etag_with_limits();
    test_pre_commit_headers_committed_passthrough();
    test_pre_commit_replay_unavail_to_committed();
    test_pre_commit_replay_unavail_overflow_with_limits();
    test_pre_commit_replay_unavail_overflow_no_limits();
    test_pre_commit_replay_unavail_resource_limit();
    test_pre_commit_replay_unavail_default_event();
    test_committed_error_pass_to_safe_finish();
    test_committed_error_reject_to_abort();
    test_committed_non_error_stays();
    test_passthrough_terminal();
    test_post_commit_safe_finish_terminal();
    test_post_commit_abort_terminal();
    test_null_ctx_passthrough();
    test_pre_commit_no_replay_delegates();
    test_full_buffer_resource_limit();
    test_full_buffer_other_event();
    test_unknown_state_passthrough();

    printf("\n  All stream state transition tests passed\n\n");
    return 0;
}
