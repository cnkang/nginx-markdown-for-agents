/*
 * Streaming Fallback State Machine — Post-commit Error Handler
 *
 * Implements safe-finish and abort paths for post-commit errors in
 * the streaming fallback state machine (streaming fallback state machine, tasks 5.1–5.4).
 *
 * Critical safety property — post-commit irreversibility:
 *   After headers or Markdown bytes are sent (COMMITTED state),
 *   post-commit errors MUST NOT revert to HTML, append HTML,
 *   mix Markdown/HTML, send conflicting Content-Type, or return 502.
 *
 * Design constraint (Component 5):
 *   C must NOT synthesize Markdown closure for Rust-owned
 *   parser/emitter state.  Safe-finish delegates to the Rust
 *   converter finish-mode API (markdown_streaming_safe_finish).
 */

#include "ngx_http_markdown_stream_postcommit.h"
#ifdef MARKDOWN_STREAMING_ENABLED
#include "markdown_converter.h"
#endif


/* Function prototypes */

static ngx_int_t
ngx_http_markdown_stream_postcommit_send_terminal(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx);

#ifdef MARKDOWN_STREAMING_ENABLED
static ngx_int_t
ngx_http_markdown_stream_postcommit_send_closing(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const u_char *data, size_t len);

static ngx_int_t
ngx_http_markdown_stream_postcommit_finish_via_rust(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx);
#endif

static ngx_flag_t
ngx_http_markdown_stream_postcommit_has_html_signature(
    const u_char *data, size_t len);

static ngx_flag_t
ngx_http_markdown_stream_postcommit_casecmp(
    const u_char *s1, const u_char *s2, size_t n);

static ngx_flag_t
ngx_http_markdown_stream_postcommit_tag_boundary(u_char ch);

static ngx_flag_t
ngx_http_markdown_stream_postcommit_space(u_char ch);

static ngx_int_t
ngx_http_markdown_stream_postcommit_handle_send_result(
    ngx_http_request_t *r, ngx_int_t rc, const char *action);


/*
 * Request the Rust finish-mode API to close known Markdown structures.
 *
 * Calls markdown_streaming_safe_finish to emit closing markers for
 * any open Markdown structures (lists, blockquotes, code blocks, etc.).
 * If safe_finish succeeds, the closing bytes are sent downstream
 * before the terminal chain.  If safe_finish fails (or the handle
 * is NULL), returns NGX_ERROR so the caller follows the abort path.
 *
 * Returns:
 *   NGX_OK    - Safe-finish completed, response closed gracefully
 *   NGX_AGAIN - Downstream backpressure; pending output was preserved
 *   NGX_ERROR - Precondition failure, safe-finish failed, or send error
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

#ifdef MARKDOWN_STREAMING_ENABLED
    if (ctx->streaming.handle != NULL) {
        return ngx_http_markdown_stream_postcommit_finish_via_rust(r, ctx);
    }

    /*
     * No Rust handle: distinguish legitimate vs anomalous absence.
     * Legitimate: handle was consumed by a prior finalize/safe_finish.
     * Anomalous: headers committed but handle never created or already
     * aborted — this suggests a logic error in the streaming path.
     */
    if (ctx->stream_sm.headers_committed
        && !ctx->streaming.completion.failure_recorded)
    {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown postcommit safe_finish: "
                      "no Rust handle in COMMITTED state "
                      "(possible logic error), "
                      "falling back to empty terminal");
    } else {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                       "markdown postcommit safe_finish: "
                       "no Rust handle (legitimate, "
                       "handle already consumed), "
                       "sending empty terminal");
    }
#endif

    /* No Rust handle: send terminal chain (empty last_buf) */
    rc = ngx_http_markdown_stream_postcommit_send_terminal(r, ctx);
    rc = ngx_http_markdown_stream_postcommit_handle_send_result(
        r, rc, "safe_finish");
    if (rc != NGX_OK && rc != NGX_DONE) {
#ifdef MARKDOWN_STREAMING_ENABLED
        if (rc != NGX_AGAIN) {
            ctx->streaming.completion.safe_finish_terminal_send_failed = 1;
        }
#endif
        return rc;
    }

    /* Log the post-commit event */
    ngx_http_markdown_stream_postcommit_log(r, ctx,
        "safe_finish", NGX_HTTP_MD_REASON_POST_COMMIT_ERROR);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown postcommit safe_finish: "
                   "response closed gracefully");

    return NGX_OK;
}


#ifdef MARKDOWN_STREAMING_ENABLED
/**
 * Invoke the Rust safe_finish handler and transmit response closure.
 *
 * Calls markdown_streaming_safe_finish to obtain closing bytes. On validation
 * failure, aborts the Rust handle. Sends closing bytes if provided, otherwise
 * sends an empty terminal chain.
 *
 * @return NGX_OK if closure completed, NGX_AGAIN on downstream backpressure,
 *         NGX_ERROR if Rust failed or send operation failed.
 */
static ngx_int_t
ngx_http_markdown_stream_postcommit_finish_via_rust(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    u_char    *close_data = NULL;
    size_t     close_len = 0;
    uint32_t   finish_rc;
    ngx_int_t  rc;

    finish_rc = markdown_streaming_safe_finish(
        ctx->streaming.handle,
        &close_data, &close_len);

    /* Only validation failure leaves the handle unconsumed. */
    if (finish_rc == ERROR_INVALID_INPUT) {
        markdown_streaming_abort(ctx->streaming.handle);
    }
    ctx->streaming.handle = NULL;

    if (finish_rc == POST_COMMIT_ABORT) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown postcommit safe_finish: "
                      "Rust could not safely close structures, "
                      "falling through to abort");
        return NGX_ERROR;
    }

    if (finish_rc != POST_COMMIT_SAFE_FINISH) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown postcommit safe_finish: "
                      "unexpected return code %ui from Rust, "
                      "falling through to abort",
                      (ngx_uint_t) finish_rc);
        return NGX_ERROR;
    }

    /* POST_COMMIT_SAFE_FINISH path */
    if (close_data != NULL && close_len > 0) {
        rc = ngx_http_markdown_stream_postcommit_send_closing(
            r, ctx, close_data, close_len);

        markdown_streaming_output_free(close_data, close_len);

        rc = ngx_http_markdown_stream_postcommit_handle_send_result(
            r, rc, "safe_finish");
        if (rc != NGX_OK && rc != NGX_DONE) {
            if (rc != NGX_AGAIN) {
                ctx->streaming.completion.safe_finish_output_loss = 1;
            }
            return rc;
        }

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                       "markdown postcommit safe_finish: "
                       "sent %uz closing bytes", close_len);
        return NGX_OK;
    }

    rc = ngx_http_markdown_stream_postcommit_send_terminal(r, ctx);
    rc = ngx_http_markdown_stream_postcommit_handle_send_result(
        r, rc, "safe_finish");
    if (rc != NGX_OK && rc != NGX_DONE) {
        if (rc != NGX_AGAIN) {
            /*
             * Rust safe-finish succeeded (no closing bytes needed), but
             * the empty terminal chain send failed definitively.  Flag
             * this so handle_postcommit_error() propagates the send
             * failure instead of treating it as a Rust failure and
             * retrying via abort (Spec case 8: no retry on terminal
             * immediate definitive failure).
             */
            ctx->streaming.completion.safe_finish_terminal_send_failed = 1;
        }
        return rc;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown postcommit safe_finish: "
                   "no closing bytes required");
    return NGX_OK;
}
#endif


/*
 * Protocol-safe abort after honoring pending
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
 *   NGX_AGAIN - Downstream backpressure; terminal output was preserved
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

#ifdef MARKDOWN_STREAMING_ENABLED
    {
        /*
         * One-shot abort metric: record only on the first genuine
         * transition into POST_COMMIT_ABORT when terminal has not
         * already been sent (e.g., by safe_finish).
         *
         * Guard conditions (any true → skip metric):
         *   1. Already in POST_COMMIT_ABORT (idempotent re-entry)
         *   2. Terminal output already delivered for this request type
         *
         * The state transition below is idempotent (same value on
         * re-entry), so we use the pre-transition state to distinguish
         * first entry from re-entry.
         */
        ngx_http_markdown_stream_state_e  prev_state;
        ngx_flag_t                        terminal_already_sent;

        prev_state = ctx->stream_sm.state;

        terminal_already_sent = (r == r->main)
            ? ctx->streaming.main_terminal_sent
            : ctx->streaming.subrequest_terminal_sent;

        /* Transition to POST_COMMIT_ABORT */
        ctx->stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_ABORT;

        if (prev_state != NGX_HTTP_MD_STATE_POST_COMMIT_ABORT
            && !terminal_already_sent)
        {
            ngx_http_markdown_metrics_record_postcommit_abort();
        }
    }
#else
    /* Transition to POST_COMMIT_ABORT */
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_ABORT;
#endif

    /*
     * Send terminal chain to close the response.
     * No content bytes are sent — just the empty last_buf marker.
     */
    rc = ngx_http_markdown_stream_postcommit_send_terminal(r, ctx);
    rc = ngx_http_markdown_stream_postcommit_handle_send_result(
        r, rc, "abort");
    if (rc != NGX_OK && rc != NGX_DONE) {
        return rc;
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
 * Guard — never send HTML after commit.
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
    const ngx_buf_t  *buf;
    size_t            len;

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
     * Scan chain buffers for HTML signatures (best-effort guard).
     *
     * This is a defense-in-depth heuristic, NOT a strong security
     * mechanism.  The primary safety guarantee comes from the state
     * machine preventing HTML from reaching post-commit paths.  This
     * guard catches accidental leaks from upstream bugs.
     *
     * We look for common HTML indicators in the leading prefix of each
     * buffer after skipping an optional UTF-8 BOM and whitespace:
     *   - "<!DOCTYPE" (case-insensitive start)
     *   - "<!--" (HTML comment)
     *   - "<html" tag
     *   - "<head" tag
     *   - "<body" tag
     *   - "<meta" tag
     *   - "<div" tag
     *   - "<script" tag
     *   - "<style" tag
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

        if (ngx_http_markdown_stream_postcommit_has_html_signature(
                buf->pos, len))
        {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                          "markdown postcommit guard: "
                          "HTML signature detected post-commit");
            return NGX_ERROR;
        }
    }

    return NGX_OK;
}


/*
 * Log a post-commit event with structured fields.
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
 * Wrap a pre-built terminal buffer in a chain link, send it through the
 * downstream body filter, and handle the NGX_AGAIN re-entry / terminal-sent
 * bookkeeping shared by both the empty-terminal and closing-bytes paths.
 *
 * The caller is responsible for allocating `b` from r->pool and setting
 * its content/flags (only `b->last_buf` is read here to decide whether
 * `main_terminal_sent` may be latched, per Rule 47: the latch is set only
 * after a successful downstream return, never on NGX_AGAIN).
 *
 * Parameters:
 *   r   - current HTTP request
 *   ctx - module context (streaming state); NULL returns NGX_ERROR
 *   b   - pre-allocated buffer (caller-populated); ownership transfers to the chain
 *   pending_has_data  - 1 if the buffer carries real bytes (closing path),
 *                       0 for the empty terminal chain
 *   pending_output_bytes - byte count to record when buffered on NGX_AGAIN
 *
 * Returns:
 *   NGX_OK / NGX_DONE / NGX_AGAIN as returned by the body filter, or
 *   NGX_ERROR on chain-link allocation failure or pending-output re-entry.
 */
#ifdef MARKDOWN_STREAMING_ENABLED
static void
ngx_http_markdown_stream_postcommit_capture_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *out,
    ngx_flag_t has_data,
    size_t output_bytes)
{
    ctx->streaming.pending_output = out;
    ctx->streaming.pending_meta.has_data = has_data;
    ctx->streaming.pending_meta.bytes = output_bytes;
    ctx->streaming.pending_meta.zero_copy = 0;
    ctx->streaming.pending_meta.main_terminal =
        (r == r->main && out->buf->last_buf);
    ctx->streaming.pending_meta.subrequest_terminal =
        (r != r->main && out->buf->last_in_chain);
    ngx_http_markdown_metrics_record_postcommit_pending(output_bytes);
    r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
}


static void
ngx_http_markdown_stream_postcommit_latch_terminal(
    const ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_buf_t *buf,
    ngx_int_t rc)
{
    if (rc != NGX_OK && rc != NGX_DONE) {
        return;
    }
    if (r == r->main && buf->last_buf) {
        ctx->streaming.main_terminal_sent = 1;
    }
    if (r != r->main && buf->last_in_chain) {
        ctx->streaming.subrequest_terminal_sent = 1;
    }
}
#endif


static ngx_int_t
ngx_http_markdown_stream_postcommit_send_chain(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx, ngx_buf_t *b,
    ngx_flag_t pending_has_data, size_t pending_output_bytes)
{
    ngx_chain_t  *out;
    ngx_int_t     rc;

#ifdef MARKDOWN_STREAMING_ENABLED
    if (ctx->streaming.pending_output != NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown postcommit: "
                      "pending output re-entry detected");
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_INVARIANT;
        return NGX_ERROR;
    }
#endif

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
#ifdef MARKDOWN_STREAMING_ENABLED
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION;
#endif
        return NGX_ERROR;
    }

    out->buf = b;
    out->next = NULL;

    rc = ngx_http_markdown_next_body_filter(r, out);

#ifdef MARKDOWN_STREAMING_ENABLED
    if (rc != NGX_OK && rc != NGX_DONE && rc != NGX_AGAIN) {
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM;
    }
#endif

    if (rc == NGX_AGAIN) {
#ifdef MARKDOWN_STREAMING_ENABLED
        ngx_http_markdown_stream_postcommit_capture_pending(
            r, ctx, out, pending_has_data, pending_output_bytes);
#endif
    }

    if ((rc == NGX_OK || rc == NGX_DONE) && pending_output_bytes > 0) {
        ngx_http_markdown_metrics_record_postcommit_copied_delivery(
            pending_output_bytes);
    }

    /* Rule 47: only latch terminal-delivered state after a successful
     * downstream return, never on NGX_AGAIN.
     * Main request terminal (last_buf) latches main_terminal_sent;
     * subrequest terminal (last_in_chain) latches subrequest_terminal_sent. */
#ifdef MARKDOWN_STREAMING_ENABLED
    ngx_http_markdown_stream_postcommit_latch_terminal(r, ctx, b, rc);
#endif

    return rc;
}


/*
 * Check whether the main terminal has already been sent; if not, allocate
 * a fresh terminal buffer from r->pool.
 *
 * Consolidates the terminal-sent short-circuit + buffer allocation shared
 * by both the empty and closing-bytes terminal senders so the two paths
 * cannot drift apart on the early-return guard or the alloc-failure path.
 *
 * Parameters:
 *   r   - current HTTP request
 *   ctx - module context (may be NULL; caller must guard earlier)
 *   out - on success (return value 1), set to a fresh ngx_buf_t owned by
 *         the caller; on short-circuit (return value 0) left untouched
 *
 * Returns:
 *   1  - buffer allocated, caller should populate and send
 *   0  - terminal already sent, caller should return NGX_OK
 *  -1  - allocation failed, caller should return NGX_ERROR
 */
static ngx_int_t
ngx_http_markdown_stream_postcommit_acquire_terminal_buf(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx, ngx_buf_t **out)
{
    ngx_buf_t  *b;

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * Short-circuit if the request-type-appropriate terminal has already
     * been sent.  Main requests check main_terminal_sent; subrequests
     * check subrequest_terminal_sent.  This prevents a duplicate
     * terminal after the request-type-aware latch has been set by a
     * prior confirmed delivery.
     */
    if (r == r->main && ctx->streaming.main_terminal_sent) {
        return 0;
    }
    if (r != r->main && ctx->streaming.subrequest_terminal_sent) {
        return 0;
    }
#endif

    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        return -1;
    }

    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;

    *out = b;
    return 1;
}


/*
 * Send a terminal chain (empty last_buf=1) to close the response.
 *
 * Allocates a buffer and chain link from the request pool, sets
 * last_buf=1, and sends via the saved downstream body filter.  This closes
 * the HTTP response without sending any additional content bytes.
 *
 * Returns:
 *   NGX_OK    - Terminal chain sent successfully
 *   NGX_ERROR - Allocation failure or output filter error
 */
static ngx_int_t
ngx_http_markdown_stream_postcommit_send_terminal(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    ngx_buf_t   *b;
    ngx_int_t    acquired;

    /* send_terminal is the only path allowed when ctx is NULL: guard
     * before acquiring so the acquire helper never sees a NULL ctx. */
    if (ctx == NULL) {
        return NGX_ERROR;
    }

    acquired = ngx_http_markdown_stream_postcommit_acquire_terminal_buf(
        r, ctx, &b);
    if (acquired == 0) {
        return NGX_OK;
    }
    if (acquired < 0) {
#ifdef MARKDOWN_STREAMING_ENABLED
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION;
#endif
        return NGX_ERROR;
    }

    b->sync = 1;

    return ngx_http_markdown_stream_postcommit_send_chain(r, ctx, b, 0, 0);
}


/*
 * Send Rust safe-finish closing bytes as a pool-owned terminal chain.
 *
 * Rust owns `data`; this helper copies it before sending so NGINX can retain
 * the output chain after the FFI buffer is released.
 */
#ifdef MARKDOWN_STREAMING_ENABLED
static ngx_int_t
ngx_http_markdown_stream_postcommit_send_closing(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const u_char *data, size_t len)
{
    ngx_buf_t   *b;
    ngx_int_t    acquired;

    /* send_closing is only reached from safe_finish, which already
     * validated ctx; acquire the buffer and inline the two non-acquire
     * outcomes (already-sent / alloc-fail) into the same return path
     * used by the populate+send steps below to avoid duplicating the
     * if (acquired == 0) / if (acquired < 0) guard pair. */
    acquired = ngx_http_markdown_stream_postcommit_acquire_terminal_buf(
        r, ctx, &b);
    switch (acquired) {
    case 0:
        return NGX_OK;
    case -1:
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    default: /* 1 — buffer acquired, fall through to populate + send */
        break;
    }

    b->pos = ngx_palloc(r->pool, len);
    if (b->pos == NULL) {
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }

    ngx_memcpy(b->pos, data, len);
    b->last = b->pos + len;
    b->memory = 1;

    return ngx_http_markdown_stream_postcommit_send_chain(r, ctx, b, 1, len);
}
#endif


/*
 * Classify the result of ngx_http_markdown_stream_postcommit_send_terminal
 * (or send_closing) and emit the matching log + return code.
 *
 * Shared by the safe_finish branches so the NGX_AGAIN / non-OK handling
 * cannot drift apart across the two terminal-send call sites.
 *
 * Parameters:
 *   r      - current HTTP request (logging)
 *   rc     - downstream return code from send_terminal/send_closing
 *   action - short context tag interpolated into the log prefix
 *            (e.g. "safe_finish" or "abort")
 *
 * Returns:
 *   rc unchanged when NGX_OK / NGX_DONE / NGX_AGAIN, NGX_ERROR otherwise.
 */
static ngx_int_t
ngx_http_markdown_stream_postcommit_handle_send_result(
    ngx_http_request_t *r, ngx_int_t rc, const char *action)
{
    if (rc == NGX_AGAIN) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                       "markdown postcommit %s: downstream backpressure",
                       action);
        return NGX_AGAIN;
    }

    if (rc != NGX_OK && rc != NGX_DONE) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown postcommit %s: failed to send terminal chain",
                      action);
        return NGX_ERROR;
    }

    return rc;
}


/*
 * Case-insensitive byte comparison for const-safe HTML signature
 * detection.  Avoids const-dropping casts required by
 * ngx_strncasecmp which lacks const qualifiers in its prototype.
 */
static ngx_flag_t
ngx_http_markdown_stream_postcommit_casecmp(
    const u_char *s1, const u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (ngx_tolower(s1[i]) != ngx_tolower(s2[i])) {
            return 0;
        }
    }

    return 1;
}


/*
 * Table-driven tag match: case-insensitive compare of tag name at p[1..]
 * followed by a boundary character (>, /, space, or end-of-buffer).
 * Reduces cognitive complexity of has_html_signature by replacing
 * per-tag if-blocks with a single loop over a tag table.
 */
static ngx_flag_t
ngx_http_markdown_stream_postcommit_match_tag(
    const u_char *p, const u_char *last, const u_char *tag,
    size_t tag_len)
{
    size_t  remaining;

    remaining = (size_t) (last - p);

    if (remaining < tag_len + 1) {
        return 0;
    }

    if (!ngx_http_markdown_stream_postcommit_casecmp(
            p + 1, tag, tag_len))
    {
        return 0;
    }

    if (remaining == tag_len + 1) {
        return 1;
    }

    return ngx_http_markdown_stream_postcommit_tag_boundary(
        p[tag_len + 1]);
}


/*
 * Best-effort heuristic: scan early response bytes for obvious HTML
 * markers after post-commit error recovery.
 *
 * THIS IS NOT A SECURITY BOUNDARY.  It is a diagnostic guard that
 * detects obviously-HTML content that slipped through after a
 * post-commit conversion error.  Known blind spots include:
 *   - HTML entities / encoded HTML (e.g. &lt;html&gt;)
 *   - Tags split across buffer/chunk boundaries
 *   - Tags beyond the scan window (first ~1 KB)
 *   - Non-obvious HTML payloads (SVG, MathML, custom elements)
 *   - HTML comments, CDATA sections, processing instructions
 *
 * Do NOT rely on this function to prevent HTML injection.  The
 * post-commit safety property (no HTML after Markdown commit) is
 * enforced by state-machine invariants, not by content inspection.
 */
static ngx_flag_t
ngx_http_markdown_stream_postcommit_has_html_signature(
    const u_char *data, size_t len)
{
    const u_char       *p;
    const u_char       *last;
    const u_char       *scan_last;
    static const u_char doctype[] = {
        'D', 'O', 'C', 'T', 'Y', 'P', 'E'
    };
    static const u_char tag_names[] =
        "htmlheadbodymetadivscriptstyle";

    static const struct {
        const u_char  *name;
        size_t         len;
    } tags[] = {
        { tag_names + 0,  4 },   /* html */
        { tag_names + 4,  4 },   /* head */
        { tag_names + 8,  4 },   /* body */
        { tag_names + 12, 4 },   /* meta */
        { tag_names + 16, 3 },   /* div */
        { tag_names + 19, 6 },   /* script */
        { tag_names + 25, 5 },   /* style */
    };

    if (data == NULL || len == 0) {
        return 0;
    }

    p = data;
    last = data + len;
    scan_last = data + ((len < 1024) ? len : 1024);

    if ((size_t) (last - p) >= 3
        && p[0] == 0xef && p[1] == 0xbb && p[2] == 0xbf)
    {
        p += 3;
    }

    while (p < scan_last
           && ngx_http_markdown_stream_postcommit_space(*p))
    {
        p++;
    }

    for ( ; p < scan_last; p++) {
        if (*p != '<') {
            continue;
        }

        /* <!DOCTYPE ... > */
        if ((size_t) (last - p) >= 9
            && p[1] == '!'
            && ngx_http_markdown_stream_postcommit_casecmp(
                   p + 2, doctype, 7))
        {
            return 1;
        }

        /* <!-- HTML comment */
        if ((size_t) (last - p) >= 4
            && p[1] == '!' && p[2] == '-' && p[3] == '-')
        {
            return 1;
        }

        for (size_t i = 0;
             i < sizeof(tags) / sizeof(tags[0]); i++)
        {
            if (ngx_http_markdown_stream_postcommit_match_tag(
                    p, last, tags[i].name, tags[i].len))
            {
                return 1;
            }
        }
    }

    return 0;
}


static ngx_flag_t
ngx_http_markdown_stream_postcommit_tag_boundary(u_char ch)
{
    return ch == '>' || ch == '/'
           || ngx_http_markdown_stream_postcommit_space(ch);
}


static ngx_flag_t
ngx_http_markdown_stream_postcommit_space(u_char ch)
{
    return ch == ' ' || ch == '\t' || ch == '\n'
           || ch == '\r' || ch == '\f';
}
