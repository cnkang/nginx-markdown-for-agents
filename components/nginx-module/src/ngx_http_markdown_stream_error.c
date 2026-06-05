/*
 * Streaming Fallback State Machine — Error Handler Integration
 *
 * Wires the markdown_on_error configuration directive into the
 * streaming state machine (spec 37, tasks 6.1–6.4).
 *
 * This module is the main entry point called by the body filter when
 * a streaming error occurs.  It populates the decision context from
 * request state, calls the decision engine, and executes the resulting
 * action via the replay, commit, and post-commit modules.
 *
 * Task 6.1: Pre-commit + on_error=pass  -> replay HTML via chain
 * Task 6.2: Pre-commit + on_error=reject -> return 502
 * Task 6.3: Post-commit + on_error=pass  -> safe_finish (abort fallback)
 * Task 6.4: Post-commit + on_error=reject -> abort
 *
 * Design invariant: post-commit paths NEVER return HTML or 502.
 */

#include "ngx_http_markdown_stream_error.h"
#include "ngx_http_markdown_stream_state.h"
#include "ngx_http_markdown_stream_replay.h"
#include "ngx_http_markdown_stream_postcommit.h"


/* Function prototypes */

static ngx_int_t
ngx_http_markdown_stream_error_pass_html(ngx_http_request_t *r,
                                          ngx_http_markdown_ctx_t *ctx);


/*
 * Handle a streaming error using the state machine and on_error policy.
 *
 * Flow:
 *   1. Validate parameters
 *   2. Build decision context from request state
 *   3. Select event based on committed state and on_error policy
 *   4. Call decision engine
 *   5. Update state machine
 *   6. Execute the resulting action
 *
 * Returns:
 *   NGX_OK               - Error handled (HTML replayed or finish/abort)
 *   NGX_HTTP_BAD_GATEWAY - 502 reject (pre-commit + on_error=reject)
 *   NGX_ERROR            - Unrecoverable error
 */
ngx_int_t
ngx_http_markdown_stream_on_error(ngx_http_request_t *r,
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

    /* 1. Populate decision context from request state */
    dctx.current_state = ctx->stream_sm.state;
    dctx.replay_available =
        ngx_http_markdown_stream_replay_available(ctx);
    dctx.headers_committed = ctx->stream_sm.headers_committed;
    dctx.within_resource_limits = 1;
    dctx.on_error_policy = conf->on_error;

    /* 2. Choose event based on committed state and on_error policy */
    if (ctx->stream_sm.headers_committed) {
        /*
         * Post-commit: regardless of on_error policy, the event is
         * ERROR.  The decision engine uses on_error_policy internally
         * to choose safe_finish vs abort.
         */
        event = NGX_HTTP_MD_EVENT_ERROR;
    } else if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS) {
        event = NGX_HTTP_MD_EVENT_ON_ERROR_PASS;
    } else {
        event = NGX_HTTP_MD_EVENT_ON_ERROR_REJECT;
    }

    /* 3. Get decision from engine */
    decision = ngx_http_markdown_stream_decide(&dctx, event);

    /* 4. Update state machine */
    ctx->stream_sm.state = decision.new_state;

    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown stream on_error: "
                   "action=%ui new_state=%ui reason=%ui",
                   (ngx_uint_t) decision.action,
                   (ngx_uint_t) decision.new_state,
                   (ngx_uint_t) decision.reason);

    /* 5. Execute action */
    switch (decision.action) {

    case NGX_HTTP_MD_ACTION_PASS_HTML:
        /*
         * Task 6.1: Pre-commit + pass = replay HTML.
         * Build replay chain, restore Content-Type, send downstream.
         */
        return ngx_http_markdown_stream_error_pass_html(r, ctx);

    case NGX_HTTP_MD_ACTION_REJECT_502:
        /*
         * Task 6.2: Pre-commit + reject = 502.
         * Return NGX_HTTP_BAD_GATEWAY; the caller (body filter)
         * uses this to finalize with 502.
         */
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown stream on_error: "
                      "rejecting with 502 (on_error=reject)");
        return NGX_HTTP_BAD_GATEWAY;

    case NGX_HTTP_MD_ACTION_SAFE_FINISH:
        /*
         * Task 6.3: Post-commit + pass = safe_finish.
         * Attempt graceful Markdown closure via Rust finish-mode.
         * If safe_finish fails, fall back to abort.
         */
        rc = ngx_http_markdown_stream_postcommit_safe_finish(r, ctx);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                          "markdown stream on_error: "
                          "safe_finish failed, falling back "
                          "to abort");
            ngx_http_markdown_stream_postcommit_abort(r, ctx);
        }
        return NGX_OK;

    case NGX_HTTP_MD_ACTION_ABORT:
        /*
         * Task 6.4: Post-commit + reject = abort.
         * Protocol-safe disconnect (no HTML, no 502).
         */
        ngx_http_markdown_stream_postcommit_abort(r, ctx);
        return NGX_OK;

    case NGX_HTTP_MD_ACTION_PASSTHROUGH:
        /*
         * Passthrough: no further action needed, just return OK.
         * This handles edge cases where the state machine produces
         * a passthrough decision (e.g., already in terminal state).
         */
        return NGX_OK;

    default:
        /* Unknown action: treat as handled */
        return NGX_OK;
    }
}


/*
 * Execute the PASS_HTML action: replay buffered HTML downstream.
 *
 * Steps (task 6.1):
 *   1. Build replay chain from ngx_http_markdown_stream_replay_chain()
 *   2. Restore original Content-Type to text/html
 *   3. Send the replay chain downstream via ngx_http_output_filter()
 *   4. State already transitioned to PASSTHROUGH by decision engine
 *
 * Returns:
 *   NGX_OK    - HTML replayed successfully
 *   NGX_ERROR - Replay chain build or send failed
 */
static ngx_int_t
ngx_http_markdown_stream_error_pass_html(ngx_http_request_t *r,
                                          ngx_http_markdown_ctx_t *ctx)
{
    ngx_chain_t  *chain;
    ngx_int_t     rc;

    /* Build the replay chain from buffered upstream bytes */
    chain = ngx_http_markdown_stream_replay_chain(ctx, r->pool);
    if (chain == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream on_error: "
                      "failed to build replay chain "
                      "(empty or allocation failure)");
        return NGX_ERROR;
    }

    /*
     * Restore Content-Type to text/html (the original upstream type).
     *
     * At this point headers have NOT been committed (pre-commit path),
     * so the downstream has not received any Content-Type yet.  The
     * caller is responsible for sending headers with the correct
     * Content-Type before or alongside this chain.  We set the
     * response header here so subsequent header send uses text/html.
     */
    r->headers_out.content_type_len = sizeof("text/html") - 1;
    ngx_str_set(&r->headers_out.content_type, "text/html");
    r->headers_out.content_type_lowcase = NULL;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown stream on_error: "
                   "replaying %uz bytes as HTML",
                   ctx->stream_sm.replay_buf.size);

    /* Send the replayed HTML downstream */
    rc = ngx_http_output_filter(r, chain);
    if (rc == NGX_ERROR) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream on_error: "
                      "output filter failed during HTML replay");
        return NGX_ERROR;
    }

    return NGX_OK;
}
