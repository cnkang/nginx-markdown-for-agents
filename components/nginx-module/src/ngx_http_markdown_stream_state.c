/*
 * Streaming Fallback State Machine — Decision Engine Implementation
 *
 * Pure-function decision engine for RFC 0008 section 3.  Maps
 * (current_state, event, context) to (new_state, action, reason_code).
 *
 * Design invariants:
 *   1. No allocations, no I/O, no global mutable state.
 *   2. Post-commit irreversibility: once COMMITTED, POST_COMMIT_SAFE_FINISH,
 *      or POST_COMMIT_ABORT, the engine NEVER returns PASS_HTML or REJECT_502.
 *   3. PASSTHROUGH is terminal: any event in PASSTHROUGH stays PASSTHROUGH.
 *   4. Pre-commit HTML fallback requires replay_available AND
 *      !headers_committed.
 *   5. Full-buffer fallback requires within_resource_limits.
 */

#include "ngx_http_markdown_stream_state.h"


/* Function prototypes */

static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_not_eligible(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event);

static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_streaming_candidate(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event);

static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_pre_commit(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event);

static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_pre_commit_replay_unavailable(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event);

static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_committed(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event);

static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_full_buffer(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event);


/*
 * Helper: build a decision struct from components.
 */
static ngx_http_markdown_decision_t
ngx_http_markdown_make_decision(
    ngx_http_markdown_stream_state_e new_state,
    ngx_http_markdown_action_e action,
    ngx_http_markdown_reason_code_e reason)
{
    ngx_http_markdown_decision_t  d;

    d.new_state = new_state;
    d.action = action;
    d.reason = reason;

    return d;
}


/*
 * Helper: attempt full-buffer fallback with resource limit guard.
 *
 * If within resource limits, transitions to FULL_BUFFER_FALLBACK with
 * SWITCH_FULL_BUFFER action.  Otherwise falls back to PASSTHROUGH
 * with PASSTHROUGH action and RESOURCE_LIMIT_EXCEEDED reason.
 */
static ngx_http_markdown_decision_t
ngx_http_markdown_try_full_buffer(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_reason_code_e reason)
{
    if (ctx->within_resource_limits) {
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
            NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
            reason);
    }

    return ngx_http_markdown_make_decision(
        NGX_HTTP_MD_STATE_PASSTHROUGH,
        NGX_HTTP_MD_ACTION_PASSTHROUGH,
        NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED);
}


/*
 * Main entry point: dispatch to per-state handler.
 *
 * The function validates the context pointer and dispatches to the
 * appropriate state-specific handler.  Unrecognized states produce
 * a safe PASSTHROUGH decision.
 */
ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide(ngx_http_markdown_stream_ctx_t *ctx,
                                ngx_http_markdown_stream_event_e event)
{
    if (ctx == NULL) {
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_PASSTHROUGH,
            NGX_HTTP_MD_REASON_NOT_ELIGIBLE);
    }

    switch (ctx->current_state) {

    case NGX_HTTP_MD_STATE_NOT_ELIGIBLE:
        return ngx_http_markdown_stream_decide_not_eligible(
            ctx, event);

    case NGX_HTTP_MD_STATE_STREAMING_CANDIDATE:
        return ngx_http_markdown_stream_decide_streaming_candidate(
            ctx, event);

    case NGX_HTTP_MD_STATE_PRE_COMMIT:
        return ngx_http_markdown_stream_decide_pre_commit(ctx, event);

    case NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE:
        return ngx_http_markdown_stream_decide_pre_commit_replay_unavailable(
            ctx, event);

    case NGX_HTTP_MD_STATE_COMMITTED:
        return ngx_http_markdown_stream_decide_committed(ctx, event);

    case NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK:
        return ngx_http_markdown_stream_decide_full_buffer(ctx, event);

    /*
     * Terminal states: PASSTHROUGH, POST_COMMIT_SAFE_FINISH,
     * POST_COMMIT_ABORT.  Any event stays in the current state.
     * Post-commit terminals must NEVER produce PASS_HTML or
     * REJECT_502.
     */
    case NGX_HTTP_MD_STATE_PASSTHROUGH:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_PASSTHROUGH,
            NGX_HTTP_MD_REASON_NOT_ELIGIBLE);

    case NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
            NGX_HTTP_MD_ACTION_SAFE_FINISH,
            NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);

    case NGX_HTTP_MD_STATE_POST_COMMIT_ABORT:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
            NGX_HTTP_MD_ACTION_ABORT,
            NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);

    default:
        /* Unknown state: safe fallback to passthrough */
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_PASSTHROUGH,
            NGX_HTTP_MD_REASON_NOT_ELIGIBLE);
    }
}


/*
 * NOT_ELIGIBLE state handler (task 2.2).
 *
 * Initial state for all requests.  Only two valid transitions:
 *   - EVENT_ELIGIBLE -> STREAMING_CANDIDATE (begin evaluation)
 *   - EVENT_NOT_ELIGIBLE -> PASSTHROUGH (bypass conversion)
 *
 * All other events are invalid in this state and produce PASSTHROUGH.
 */
static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_not_eligible(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event)
{
    (void) ctx;

    switch (event) {

    case NGX_HTTP_MD_EVENT_ELIGIBLE:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_STREAMING_CANDIDATE,
            NGX_HTTP_MD_ACTION_NONE,
            NGX_HTTP_MD_REASON_ELIGIBLE);

    case NGX_HTTP_MD_EVENT_NOT_ELIGIBLE:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_PASSTHROUGH,
            NGX_HTTP_MD_REASON_NOT_ELIGIBLE);

    default:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_PASSTHROUGH,
            NGX_HTTP_MD_REASON_NOT_ELIGIBLE);
    }
}


/*
 * STREAMING_CANDIDATE state handler (tasks 2.3, 2.8).
 *
 * The request has been deemed eligible.  Valid transitions:
 *   - EVENT_STREAMING_START -> PRE_COMMIT (streaming begins)
 *   - Full-buffer triggers (STRICT_ETAG, LOOK_BEHIND_OVERFLOW,
 *     AUTO_RISK, FULL_DOC_FEATURE) -> FULL_BUFFER_FALLBACK or
 *     PASSTHROUGH depending on resource limits.
 *
 * All other events produce PASSTHROUGH.
 */
static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_streaming_candidate(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event)
{
    switch (event) {

    case NGX_HTTP_MD_EVENT_STREAMING_START:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PRE_COMMIT,
            NGX_HTTP_MD_ACTION_CONTINUE_STREAMING,
            NGX_HTTP_MD_REASON_ELIGIBLE);

    /* Full-buffer triggers with resource limit check (task 2.8) */
    case NGX_HTTP_MD_EVENT_STRICT_ETAG:
        return ngx_http_markdown_try_full_buffer(
            ctx, NGX_HTTP_MD_REASON_STRICT_ETAG);

    case NGX_HTTP_MD_EVENT_LOOK_BEHIND_OVERFLOW:
        return ngx_http_markdown_try_full_buffer(
            ctx, NGX_HTTP_MD_REASON_LOOK_BEHIND_OVERFLOW);

    case NGX_HTTP_MD_EVENT_AUTO_RISK:
        return ngx_http_markdown_try_full_buffer(
            ctx, NGX_HTTP_MD_REASON_AUTO_RISK);

    case NGX_HTTP_MD_EVENT_FULL_DOC_FEATURE:
        return ngx_http_markdown_try_full_buffer(
            ctx, NGX_HTTP_MD_REASON_FULL_DOC_FEATURE);

    default:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_PASSTHROUGH,
            NGX_HTTP_MD_REASON_NOT_ELIGIBLE);
    }
}


/*
 * PRE_COMMIT state handler (tasks 2.4, 2.5, 2.6, 2.8).
 *
 * Streaming has started but headers are not yet committed.
 * The available transitions depend on replay_available and
 * headers_committed flags:
 *
 * When replay_available=true AND headers_committed=false (task 2.4):
 *   - Fallback events -> PASSTHROUGH with PASS_HTML action
 *   - Full-doc feature -> FULL_BUFFER_FALLBACK (if within limits)
 *   - ON_ERROR_PASS -> PASSTHROUGH with PASS_HTML
 *   - ON_ERROR_REJECT -> PASSTHROUGH with REJECT_502
 *
 * When replay_available=false AND headers_committed=false (task 2.5):
 *   - Delegated to PRE_COMMIT_REPLAY_UNAVAILABLE handler
 *
 * EVENT_COMMIT (task 2.6):
 *   - -> COMMITTED with COMMIT_HEADERS action
 *
 * Full-buffer triggers (task 2.8):
 *   - Resource limit check applies
 */
static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_pre_commit(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event)
{
    /*
     * Task 2.6: EVENT_COMMIT transitions to COMMITTED regardless
     * of replay state — the caller has decided to proceed.
     */
    if (event == NGX_HTTP_MD_EVENT_COMMIT) {
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_COMMITTED,
            NGX_HTTP_MD_ACTION_COMMIT_HEADERS,
            NGX_HTTP_MD_REASON_COMMIT_SUCCESS);
    }

    /*
     * Task 2.5: If replay is unavailable but headers are not
     * committed, delegate to the replay-unavailable handler for
     * forced decision semantics.
     */
    if (!ctx->replay_available && !ctx->headers_committed) {
        return ngx_http_markdown_stream_decide_pre_commit_replay_unavailable(
            ctx, event);
    }

    /*
     * Task 2.4: Pre-commit fallback when replay is available
     * and headers are uncommitted.
     */
    if (ctx->replay_available && !ctx->headers_committed) {
        switch (event) {

        case NGX_HTTP_MD_EVENT_PARSER_UNSUITABLE:
            return ngx_http_markdown_make_decision(
                NGX_HTTP_MD_STATE_PASSTHROUGH,
                NGX_HTTP_MD_ACTION_PASS_HTML,
                NGX_HTTP_MD_REASON_PARSER_UNSUITABLE);

        case NGX_HTTP_MD_EVENT_HARD_EXCLUDED:
            return ngx_http_markdown_make_decision(
                NGX_HTTP_MD_STATE_PASSTHROUGH,
                NGX_HTTP_MD_ACTION_PASS_HTML,
                NGX_HTTP_MD_REASON_HARD_EXCLUDED);

        case NGX_HTTP_MD_EVENT_FULL_DOC_FEATURE:
            return ngx_http_markdown_try_full_buffer(
                ctx, NGX_HTTP_MD_REASON_FULL_DOC_FEATURE);

        case NGX_HTTP_MD_EVENT_BUDGET_INIT_FAILURE:
            return ngx_http_markdown_make_decision(
                NGX_HTTP_MD_STATE_PASSTHROUGH,
                NGX_HTTP_MD_ACTION_PASS_HTML,
                NGX_HTTP_MD_REASON_BUDGET_INIT_FAILURE);

        case NGX_HTTP_MD_EVENT_ON_ERROR_PASS:
            return ngx_http_markdown_make_decision(
                NGX_HTTP_MD_STATE_PASSTHROUGH,
                NGX_HTTP_MD_ACTION_PASS_HTML,
                NGX_HTTP_MD_REASON_ON_ERROR_PASS);

        case NGX_HTTP_MD_EVENT_ON_ERROR_REJECT:
            return ngx_http_markdown_make_decision(
                NGX_HTTP_MD_STATE_PASSTHROUGH,
                NGX_HTTP_MD_ACTION_REJECT_502,
                NGX_HTTP_MD_REASON_ON_ERROR_REJECT);

        /* Full-buffer triggers (task 2.8) */
        case NGX_HTTP_MD_EVENT_STRICT_ETAG:
            return ngx_http_markdown_try_full_buffer(
                ctx, NGX_HTTP_MD_REASON_STRICT_ETAG);

        case NGX_HTTP_MD_EVENT_LOOK_BEHIND_OVERFLOW:
            return ngx_http_markdown_try_full_buffer(
                ctx, NGX_HTTP_MD_REASON_LOOK_BEHIND_OVERFLOW);

        case NGX_HTTP_MD_EVENT_AUTO_RISK:
            return ngx_http_markdown_try_full_buffer(
                ctx, NGX_HTTP_MD_REASON_AUTO_RISK);

        case NGX_HTTP_MD_EVENT_RESOURCE_LIMIT:
            return ngx_http_markdown_make_decision(
                NGX_HTTP_MD_STATE_PASSTHROUGH,
                NGX_HTTP_MD_ACTION_PASSTHROUGH,
                NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED);

        default:
            return ngx_http_markdown_make_decision(
                NGX_HTTP_MD_STATE_PASSTHROUGH,
                NGX_HTTP_MD_ACTION_PASSTHROUGH,
                NGX_HTTP_MD_REASON_NOT_ELIGIBLE);
        }
    }

    /*
     * Fallthrough: headers already committed in PRE_COMMIT state
     * should not happen (would be COMMITTED), but handle safely.
     */
    return ngx_http_markdown_make_decision(
        NGX_HTTP_MD_STATE_PASSTHROUGH,
        NGX_HTTP_MD_ACTION_PASSTHROUGH,
        NGX_HTTP_MD_REASON_NOT_ELIGIBLE);
}


/*
 * PRE_COMMIT_REPLAY_UNAVAILABLE state handler (task 2.5).
 *
 * Replay buffer has overflowed but headers are not yet committed.
 * HTML passthrough is no longer available.  The module must either:
 *   - Commit and continue streaming
 *   - Switch to full-buffer (if within limits)
 *   - Reject (if limits exceeded)
 *
 * Valid transitions:
 *   - EVENT_COMMIT -> COMMITTED (proceed with streaming)
 *   - EVENT_REPLAY_OVERFLOW -> FULL_BUFFER_FALLBACK (within limits)
 *                           or PASSTHROUGH/REJECT_502 (exceeded)
 *   - Full-buffer triggers -> resource limit check
 */
static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_pre_commit_replay_unavailable(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event)
{
    switch (event) {

    case NGX_HTTP_MD_EVENT_COMMIT:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_COMMITTED,
            NGX_HTTP_MD_ACTION_COMMIT_HEADERS,
            NGX_HTTP_MD_REASON_COMMIT_SUCCESS);

    case NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW:
        if (ctx->within_resource_limits) {
            return ngx_http_markdown_make_decision(
                NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
                NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
                NGX_HTTP_MD_REASON_REPLAY_OVERFLOW);
        }
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_REJECT_502,
            NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED);

    /* Full-buffer triggers (task 2.8) */
    case NGX_HTTP_MD_EVENT_STRICT_ETAG:
        return ngx_http_markdown_try_full_buffer(
            ctx, NGX_HTTP_MD_REASON_STRICT_ETAG);

    case NGX_HTTP_MD_EVENT_LOOK_BEHIND_OVERFLOW:
        return ngx_http_markdown_try_full_buffer(
            ctx, NGX_HTTP_MD_REASON_LOOK_BEHIND_OVERFLOW);

    case NGX_HTTP_MD_EVENT_AUTO_RISK:
        return ngx_http_markdown_try_full_buffer(
            ctx, NGX_HTTP_MD_REASON_AUTO_RISK);

    case NGX_HTTP_MD_EVENT_FULL_DOC_FEATURE:
        return ngx_http_markdown_try_full_buffer(
            ctx, NGX_HTTP_MD_REASON_FULL_DOC_FEATURE);

    case NGX_HTTP_MD_EVENT_RESOURCE_LIMIT:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_REJECT_502,
            NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED);

    default:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_REJECT_502,
            NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED);
    }
}


/*
 * COMMITTED state handler (task 2.7).
 *
 * Headers have been sent downstream.  The critical safety property
 * applies here: we must NEVER produce PASS_HTML or REJECT_502.
 *
 * Valid transitions:
 *   - EVENT_ERROR:
 *       on_error_policy=pass -> POST_COMMIT_SAFE_FINISH
 *       on_error_policy=reject -> POST_COMMIT_ABORT
 *   - All other events: stay COMMITTED (streaming continues)
 */
static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_committed(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event)
{
    switch (event) {

    case NGX_HTTP_MD_EVENT_ERROR:
        if (ctx->on_error_policy == NGX_HTTP_MARKDOWN_ON_ERROR_PASS) {
            return ngx_http_markdown_make_decision(
                NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
                NGX_HTTP_MD_ACTION_SAFE_FINISH,
                NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);
        }
        /* on_error=reject post-commit: abort (NOT 502) */
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
            NGX_HTTP_MD_ACTION_ABORT,
            NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);

    default:
        /*
         * Non-error events in COMMITTED: streaming continues.
         * Stay in COMMITTED state with no action change.
         */
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_COMMITTED,
            NGX_HTTP_MD_ACTION_CONTINUE_STREAMING,
            NGX_HTTP_MD_REASON_COMMIT_SUCCESS);
    }
}


/*
 * FULL_BUFFER_FALLBACK state handler (task 2.8).
 *
 * The module has switched to full-buffer mode.  This is a stable
 * processing state (not terminal like PASSTHROUGH).  The only
 * meaningful transition is on resource limit exceeded, which
 * forces a passthrough.
 */
static ngx_http_markdown_decision_t
ngx_http_markdown_stream_decide_full_buffer(
    ngx_http_markdown_stream_ctx_t *ctx,
    ngx_http_markdown_stream_event_e event)
{
    (void) ctx;

    switch (event) {

    case NGX_HTTP_MD_EVENT_RESOURCE_LIMIT:
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_PASSTHROUGH,
            NGX_HTTP_MD_ACTION_PASSTHROUGH,
            NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED);

    default:
        /* Stay in full-buffer mode */
        return ngx_http_markdown_make_decision(
            NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
            NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
            NGX_HTTP_MD_REASON_FULL_DOC_FEATURE);
    }
}
