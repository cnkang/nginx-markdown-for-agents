/*
 * Streaming Fallback State Machine — Post-commit Error Handler
 *
 * Implements safe-finish and abort paths for post-commit errors in
 * the streaming fallback state machine (spec 37, tasks 5.1–5.4).
 *
 * Critical safety property (Requirement 5):
 *   After headers or Markdown bytes are sent (COMMITTED state),
 *   post-commit errors MUST NOT revert to HTML, append HTML,
 *   mix Markdown/HTML, send conflicting Content-Type, or return 502.
 *
 * Design constraint (Component 5):
 *   C must NOT synthesize Markdown closure for Rust-owned
 *   parser/emitter state.  Safe-finish delegates to the Rust
 *   converter finish-mode API (stubbed until spec 38).
 */

#include "ngx_http_markdown_stream_postcommit.h"


/* Function prototypes */

static ngx_int_t
ngx_http_markdown_stream_postcommit_send_terminal(
    ngx_http_request_t *r);


/*
 * Task 5.1: Request Rust finish-mode API to close known Markdown
 * structures.
 *
 * Current implementation is a stub because the Rust streaming
 * converter (spec 38) does not exist yet.  The function:
 *   1. Validates that state is COMMITTED or POST_COMMIT_SAFE_FINISH
 *   2. Logs intent at debug level
 *   3. Transitions state to POST_COMMIT_SAFE_FINISH
 *   4. Sends a last_buf=1 terminal chain to close the response
 *   5. Logs the event with phase=postcommit
 *
 * When spec 38 wires the Rust API, this stub will call the Rust
 * finish-mode function before sending the terminal chain.  If
 * Rust returns an error, this function returns NGX_ERROR and the
 * caller follows the abort path.
 *
 * Returns:
 *   NGX_OK    - Safe-finish completed, response closed
 *   NGX_ERROR - Precondition failure or send error
 */
ngx_int_t
ngx_http_markdown_stream_postcommit_safe_finish(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    if (r == NULL || ctx == NULL) {
        return NGX_ERROR;
    }

    /*
     * Precondition: must be in COMMITTED or already in
     * POST_COMMIT_SAFE_FINISH (idempotent re-entry).
     */
    if (ctx->stream_sm.state != NGX_HTTP_MD_STATE_COMMITTED
        && ctx->stream_sm.state
           != NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH)
    {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown postcommit safe_finish: "
                      "invalid state %ui, expected COMMITTED "
                      "or POST_COMMIT_SAFE_FINISH",
                      (ngx_uint_t) ctx->stream_sm.state);
        return NGX_ERROR;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown postcommit safe_finish: "
                   "initiating graceful close");

    /* Transition to POST_COMMIT_SAFE_FINISH */
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH;

    /*
     * Spec 38 follow-up: call the Rust finish-mode API here to
     * emit closing markers for open Markdown structures.  If the
     * Rust converter handle is NULL or returns error, return
     * NGX_ERROR so the caller falls through to abort.
     *
     * For now, just send the terminal chain (empty last_buf).
     */

    /* Send terminal chain to close the response */
    rc = ngx_http_markdown_stream_postcommit_send_terminal(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown postcommit safe_finish: "
                      "failed to send terminal chain");
        return NGX_ERROR;
    }

    /* Log the post-commit event */
    ngx_http_markdown_stream_postcommit_log(r, ctx,
        "safe_finish", NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown postcommit safe_finish: "
                   "response closed gracefully");

    return NGX_OK;
}


/*
 * Task 5.2: Protocol-safe abort after honoring pending
 * output/backpressure state.
 *
 * Steps:
 *   1. Validate preconditions (must be in committed/post-commit state)
 *   2. Transition to POST_COMMIT_ABORT
 *   3. Send a last_buf=1 terminal chain (no content bytes)
 *   4. Log the abort event
 *
 * The downstream client receives a truncated but valid HTTP response.
 * Content-Length was removed at commit time, so truncation is valid
 * for chunked transfer encoding.
 *
 * Returns:
 *   NGX_OK    - Abort completed, response terminated
 *   NGX_ERROR - Precondition failure or terminal send failed
 */
ngx_int_t
ngx_http_markdown_stream_postcommit_abort(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    if (r == NULL || ctx == NULL) {
        return NGX_ERROR;
    }

    /*
     * Precondition: must be in a post-commit state (COMMITTED,
     * POST_COMMIT_SAFE_FINISH, or already POST_COMMIT_ABORT for
     * idempotent re-entry).
     */
    if (ctx->stream_sm.state != NGX_HTTP_MD_STATE_COMMITTED
        && ctx->stream_sm.state
           != NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH
        && ctx->stream_sm.state
           != NGX_HTTP_MD_STATE_POST_COMMIT_ABORT)
    {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown postcommit abort: "
                      "invalid state %ui, expected post-commit",
                      (ngx_uint_t) ctx->stream_sm.state);
        return NGX_ERROR;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown postcommit abort: "
                   "initiating protocol-safe abort");

    /* Transition to POST_COMMIT_ABORT */
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_ABORT;

    /*
     * Send terminal chain to close the response.
     * No content bytes are sent — just the empty last_buf marker.
     */
    rc = ngx_http_markdown_stream_postcommit_send_terminal(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown postcommit abort: "
                      "failed to send terminal chain");
        return NGX_ERROR;
    }

    /* Log the abort event */
    ngx_http_markdown_stream_postcommit_log(r, ctx,
        "abort", NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown postcommit abort: "
                   "response terminated");

    return NGX_OK;
}


/*
 * Task 5.3: Guard — never send HTML after commit.
 *
 * Checks that the request is in a post-commit state and that no
 * HTML content signatures are present in the output chain.
 *
 * Current implementation validates state preconditions and performs
 * a basic scan for HTML doctype/tag signatures.  Detailed HTML
 * detection will be enhanced when the body filter is wired.
 *
 * Returns:
 *   NGX_OK    - Guard passes, output is safe to send
 *   NGX_ERROR - HTML detected or invalid state
 */
ngx_int_t
ngx_http_markdown_stream_postcommit_guard(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *chain)
{
    ngx_buf_t    *buf;
    size_t        len;
    u_char       *p;
    u_char        doctype[] = "DOCTYPE";
    u_char        html_tag[] = "html";

    if (r == NULL || ctx == NULL) {
        return NGX_ERROR;
    }

    /*
     * Guard only applies in post-commit states.  If not committed
     * yet, the guard is not relevant (pre-commit can still fall
     * back to HTML).
     */
    if (ctx->stream_sm.state != NGX_HTTP_MD_STATE_COMMITTED
        && ctx->stream_sm.state
           != NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH
        && ctx->stream_sm.state
           != NGX_HTTP_MD_STATE_POST_COMMIT_ABORT)
    {
        return NGX_OK;
    }

    /*
     * Scan chain buffers for HTML signatures.
     *
     * We look for common HTML indicators at the start of buffer
     * content:
     *   - "<!DOCTYPE" (case-insensitive start)
     *   - "<html" tag
     *
     * This is a lightweight heuristic.  The full body filter
     * integration (spec 38+) will provide complete detection.
     */
    for (ngx_chain_t *cl = chain; cl != NULL; cl = cl->next) {
        buf = cl->buf;

        if (buf == NULL) {
            continue;
        }

        len = ngx_http_markdown_buf_len_safe(buf);
        if (len == 0) {
            continue;
        }

        p = buf->pos;

        /*
         * Check for HTML signatures.  Only inspect first bytes
         * of each buffer for performance (HTML content typically
         * starts at the beginning of a response or chunk).
         */
        if (len >= 9
            && p[0] == '<'
            && (p[1] == '!' || p[1] == '?')
            && ngx_strncasecmp(p + 2, doctype, 7) == 0)
        {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                          "markdown postcommit guard: "
                          "HTML DOCTYPE detected post-commit");
            return NGX_ERROR;
        }

        if (len >= 5
            && p[0] == '<'
            && ngx_strncasecmp(p + 1, html_tag, 4) == 0)
        {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                          "markdown postcommit guard: "
                          "HTML tag detected post-commit");
            return NGX_ERROR;
        }
    }

    return NGX_OK;
}


/*
 * Task 5.4: Log a post-commit event with structured fields.
 *
 * Emits a structured log entry at NGX_LOG_WARN level containing:
 *   phase=postcommit
 *   action=<action string>
 *   committed=1
 *   reason=<reason code numeric value>
 *   state=<current state numeric value>
 */
void
ngx_http_markdown_stream_postcommit_log(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    const char *action,
    ngx_http_markdown_reason_code_e reason)
{
    if (r == NULL || ctx == NULL || action == NULL) {
        return;
    }

    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                  "markdown stream: "
                  "phase=postcommit action=%s committed=1 "
                  "reason=%ui state=%ui",
                  action,
                  (ngx_uint_t) reason,
                  (ngx_uint_t) ctx->stream_sm.state);
}


/*
 * Send a terminal chain (empty last_buf=1) to close the response.
 *
 * Allocates a buffer and chain link from the request pool, sets
 * last_buf=1, and sends via ngx_http_output_filter.  This closes
 * the HTTP response without sending any additional content bytes.
 *
 * Returns:
 *   NGX_OK    - Terminal chain sent successfully
 *   NGX_ERROR - Allocation failure or output filter error
 */
static ngx_int_t
ngx_http_markdown_stream_postcommit_send_terminal(
    ngx_http_request_t *r)
{
    ngx_buf_t    *b;
    ngx_chain_t   out;

    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        return NGX_ERROR;
    }

    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;
    b->sync = 1;

    out.buf = b;
    out.next = NULL;

    return ngx_http_output_filter(r, &out);
}
