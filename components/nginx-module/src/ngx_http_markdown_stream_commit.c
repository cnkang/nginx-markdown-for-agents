/*
 * Streaming Fallback State Machine — Header Commit Sequence
 *
 * Implements the atomic header commit sequence for streaming mode.
 * This is the single authoritative path for all streaming header
 * mutations.  No other code path may mutate streaming response headers.
 *
 * Two-phase design (Rule 39):
 *   Phase 1 (fallible): Vary: Accept, ETag removal, auth Cache-Control.
 *     If any step fails, NGX_ERROR is returned before headers are sent
 *     downstream.  Earlier Phase 1 mutations may be present in
 *     headers_out but are invisible because the caller's fallback sends
 *     the original upstream headers.
 *   Phase 2 (infallible): Content-Type, Content-Length, Content-Encoding.
 *     These are pointer/integer writes that cannot fail.
 *   Only after both phases complete are headers_committed and state set.
 *
 * Header commit safety invariant:
 *   Header decisions MUST be completed before outgoing headers are
 *   mutated.  Header mutation is applied in a single final step
 *   after the decision is known.  If the decision fails before
 *   header send, no partial Markdown response may be committed.
 */

#include "ngx_http_markdown_stream_commit.h"


/* Function prototypes */

static ngx_int_t
ngx_http_markdown_stream_commit_remove_content_length(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_maybe_remove_content_encoding(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx);

static ngx_int_t
ngx_http_markdown_stream_commit_set_content_type(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_set_vary(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_remove_etag(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_apply_auth_cache_control(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf);


/*
 * Execute the streaming header commit sequence.
 *
 * Phase 1 (fallible): Vary, ETag, auth Cache-Control.
 *   If any fails, no mutations are visible, NGX_ERROR returned.
 *   The caller can safely fall back to upstream HTML passthrough.
 *
 * Phase 2 (infallible): Content-Type, Content-Length, Content-Encoding.
 *   These are pointer/integer assignments that cannot fail.
 *
 * On success, transitions stream_sm to COMMITTED with
 * headers_committed = 1.
 *
 * Returns:
 *   NGX_OK    - All mutations applied, committed flag set
 *   NGX_ERROR - Precondition failure or fallible-mutation error
 */
ngx_int_t
ngx_http_markdown_stream_commit_headers(ngx_http_request_t *r,
                                         ngx_http_markdown_ctx_t *ctx,
                                         const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    if (r == NULL || ctx == NULL) {
        return NGX_ERROR;
    }

    if (ctx->stream_sm.headers_committed) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "headers already committed");
        return NGX_ERROR;
    }

    if (ctx->stream_sm.state != NGX_HTTP_MD_STATE_PRE_COMMIT
        && ctx->stream_sm.state
           != NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE)
    {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "invalid state %ui for commit",
                      (ngx_uint_t) ctx->stream_sm.state);
        return NGX_ERROR;
    }

    /*
     * --- Phase 1: Fallible header operations ---
     *
     * These operations may fail (e.g. allocation failure in Vary).
     * Order: Vary -> ETag -> auth Cache-Control.
     * If any fails, headers have not been sent downstream.
     *
     * Note: Vary and ETag removal are ordered before Content-Type
     * and Content-Length because they can fail.  Content-Type
     * assignment and Content-Length removal are infallible and
     * belong to Phase 2.
     *
     * Atomicity guarantee: if Vary succeeds but ETag fails, the
     * Vary header HAS been added to the headers_out list.  However,
     * the request is still in pre-commit state and headers have NOT
     * been sent to the network.  The caller's fallback path sends
     * the original upstream headers (Content-Type: text/html), so
     * the extra Vary: Accept is harmless — it does not change
     * content negotiation semantics for an HTML response.
     * Similarly, a removed ETag in the headers list is not visible
     * because the full-buffer/fallback path restores upstream
     * headers before sending.
     */

    rc = ngx_http_markdown_stream_commit_set_vary(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to set Vary header");
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_stream_commit_remove_etag(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to remove ETag");
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_stream_commit_apply_auth_cache_control(
        r, conf);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to apply auth Cache-Control");
        return NGX_ERROR;
    }

    /*
     * --- Phase 2: Infallible mutations ---
     *
     * These operations are pointer/integer assignments that cannot
     * fail.  They are ordered so that Content-Type is set first
     * (establishing the Markdown content type) followed by
     * Content-Length and Content-Encoding removal.
     */

    rc = ngx_http_markdown_stream_commit_set_content_type(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to set Content-Type");
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_stream_commit_remove_content_length(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to remove Content-Length");
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_stream_commit_maybe_remove_content_encoding(
        r, ctx);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to remove Content-Encoding");
        return NGX_ERROR;
    }

    /*
     * All mutations succeeded — set committed flag.
     * After this point, no HTML fallback is possible.
     */
    ctx->stream_sm.headers_committed = 1;
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown stream commit: "
                   "headers committed successfully");

    return NGX_OK;
}


/*
 * Remove Content-Length header.
 *
 * Streaming responses have unknown final length.  Clear the
 * Content-Length numeric field and invalidate the header entry
 * (hash=0, pointer=NULL) per Rule 40 so NGINX does not emit it
 * downstream.
 *
 * Returns:
 *   NGX_OK always (removal cannot fail)
 */
static ngx_int_t
ngx_http_markdown_stream_commit_remove_content_length(
    ngx_http_request_t *r)
{
    r->headers_out.content_length_n = -1;

    if (r->headers_out.content_length != NULL) {
        r->headers_out.content_length->hash = 0;
        r->headers_out.content_length = NULL;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown stream commit: "
                   "removed Content-Length");

    return NGX_OK;
}


/*
 * Remove Content-Encoding header conditionally.
 *
 * Only removes Content-Encoding when decompression was performed or
 * needed for this request.  When no decompression occurred, the
 * upstream encoding is valid and must be preserved.
 *
 * Returns:
 *   NGX_OK always
 */
static ngx_int_t
ngx_http_markdown_stream_commit_maybe_remove_content_encoding(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx)
{
    if (ctx->decompression.needed) {
        ngx_http_markdown_remove_content_encoding(r);
    }

    return NGX_OK;
}


/*
 * Set Content-Type to text/markdown; charset=utf-8.
 *
 * Uses the shared NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL constant.
 * Sets the dedicated r->headers_out.content_type field (NGINX emits
 * Content-Type from this field, not from the headers list).
 *
 * Returns:
 *   NGX_OK always (assignment cannot fail)
 */
static ngx_int_t
ngx_http_markdown_stream_commit_set_content_type(
    ngx_http_request_t *r)
{
    r->headers_out.content_type.data = ngx_http_markdown_content_type;
    r->headers_out.content_type.len = NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN;
    r->headers_out.content_type_len = NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN;

    r->headers_out.charset.len = 0;
    r->headers_out.charset.data = NULL;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown stream commit: "
                   "set Content-Type: text/markdown; charset=utf-8");

    return NGX_OK;
}


/*
 * Set Vary: Accept response header.
 *
 * Delegates to the shared ngx_http_markdown_add_vary_accept()
 * helper.  This is the only fallible operation in the commit
 * sequence that can fail due to pool allocation.
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_stream_commit_set_vary(
    ngx_http_request_t *r)
{
    return ngx_http_markdown_add_vary_accept(r);
}


/*
 * Remove upstream ETag header.
 *
 * The upstream HTML ETag is invalid for the Markdown variant since
 * the content is different.  Delegates to the shared
 * ngx_http_markdown_set_etag() helper with NULL/0 to clear it.
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_stream_commit_remove_etag(
    ngx_http_request_t *r)
{
    return ngx_http_markdown_set_etag(r, NULL, 0);
}


/*
 * Apply authenticated content Cache-Control protection.
 *
 * When the request is authenticated and auth_policy allows
 * conversion, upgrade Cache-Control to at least "private" to prevent
 * shared caches from storing authenticated Markdown content.  This
 * matches the full-buffer path's behavior.
 *
 * Guarded by NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL compile-time
 * gate for parity with the full-buffer path.
 *
 * Returns:
 *   NGX_OK on success (or when compile-time gate is off)
 *   NGX_ERROR on Cache-Control modification failure
 */
static ngx_int_t
ngx_http_markdown_stream_commit_apply_auth_cache_control(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf)
{
#if NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
    ngx_int_t  rc;

    if (conf == NULL) {
        return NGX_OK;
    }

    if (ngx_http_markdown_is_authenticated(r, conf)) {
        rc = ngx_http_markdown_modify_cache_control_for_auth(r);
        if (rc != NGX_OK) {
            return rc;
        }
    }
#else
    (void) r;
    (void) conf;
#endif

    return NGX_OK;
}
