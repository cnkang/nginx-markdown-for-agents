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

static ngx_int_t
ngx_http_markdown_stream_postcommit_send_closing(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const u_char *data, size_t len);

#ifdef MARKDOWN_STREAMING_ENABLED
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
    if (rc == NGX_AGAIN) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                       "markdown postcommit safe_finish: "
                       "terminal chain pending (NGX_AGAIN)");
        return NGX_AGAIN;
    }
    if (rc != NGX_OK && rc != NGX_DONE) {
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

        if (rc == NGX_AGAIN) {
            return NGX_AGAIN;
        }

        if (rc != NGX_OK && rc != NGX_DONE) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                          "markdown postcommit safe_finish: "
                          "failed to send closing bytes (rc=%i)",
                          rc);
            return NGX_ERROR;
        }

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                       "markdown postcommit safe_finish: "
                       "sent %uz closing bytes", close_len);
        return NGX_OK;
    }

    rc = ngx_http_markdown_stream_postcommit_send_terminal(r, ctx);
    if (rc == NGX_AGAIN) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                       "markdown postcommit safe_finish: "
                       "terminal chain pending (NGX_AGAIN), "
                       "no closing bytes path");
        return NGX_AGAIN;
    }
    if (rc != NGX_OK && rc != NGX_DONE) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown postcommit safe_finish: "
                      "failed to send terminal chain");
        return NGX_ERROR;
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

    /* Transition to POST_COMMIT_ABORT */
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_ABORT;

    /*
     * Send terminal chain to close the response.
     * No content bytes are sent — just the empty last_buf marker.
     */
    rc = ngx_http_markdown_stream_postcommit_send_terminal(r, ctx);
    if (rc == NGX_AGAIN) {
        return NGX_AGAIN;
    }
    if (rc != NGX_OK && rc != NGX_DONE) {
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
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    ngx_buf_t    *b;
    ngx_chain_t  *out;
    ngx_int_t     rc;

    if (ctx == NULL) {
        return NGX_ERROR;
    }

#ifdef MARKDOWN_STREAMING_ENABLED
    if (r == r->main && ctx->streaming.main_terminal_sent) {
        return NGX_OK;
    }
#endif

    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        return NGX_ERROR;
    }

    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;
    b->sync = 1;

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        return NGX_ERROR;
    }

    out->buf = b;
    out->next = NULL;

    rc = ngx_http_output_filter(r, out);

    if (rc == NGX_AGAIN) {
#ifdef MARKDOWN_STREAMING_ENABLED
        if (ctx->streaming.pending_output != NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                          "markdown postcommit send_terminal: "
                          "pending output re-entry detected");
            return NGX_ERROR;
        }

        ctx->streaming.pending_output = out;
        ctx->streaming.pending_has_data = 0;
        ctx->streaming.pending_output_bytes = 0;
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
#endif
    }

    if (b->last_buf && (rc == NGX_OK || rc == NGX_DONE)) {
#ifdef MARKDOWN_STREAMING_ENABLED
        ctx->streaming.main_terminal_sent = 1;
#endif
    }

    return rc;
}


/*
 * Send Rust safe-finish closing bytes as a pool-owned terminal chain.
 *
 * Rust owns `data`; this helper copies it before sending so NGINX can retain
 * the output chain after the FFI buffer is released.
 */
static ngx_int_t
ngx_http_markdown_stream_postcommit_send_closing(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const u_char *data, size_t len)
{
    ngx_buf_t    *b;
    ngx_chain_t  *out;
    ngx_int_t     rc;

#ifdef MARKDOWN_STREAMING_ENABLED
    if (r == r->main && ctx->streaming.main_terminal_sent) {
        return NGX_OK;
    }
#endif

    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        return NGX_ERROR;
    }

    b->pos = ngx_palloc(r->pool, len);
    if (b->pos == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(b->pos, data, len);
    b->last = b->pos + len;
    b->memory = 1;
    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        return NGX_ERROR;
    }

    out->buf = b;
    out->next = NULL;

    rc = ngx_http_output_filter(r, out);

    if (rc == NGX_AGAIN) {
#ifdef MARKDOWN_STREAMING_ENABLED
        if (ctx->streaming.pending_output != NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                          "markdown postcommit safe_finish: "
                          "pending output re-entry detected");
            return NGX_ERROR;
        }

        ctx->streaming.pending_output = out;
        ctx->streaming.pending_has_data = 1;
        ctx->streaming.pending_output_bytes = len;
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
#endif
    }

    if (b->last_buf && (rc == NGX_OK || rc == NGX_DONE)) {
#ifdef MARKDOWN_STREAMING_ENABLED
        ctx->streaming.main_terminal_sent = 1;
#endif
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
