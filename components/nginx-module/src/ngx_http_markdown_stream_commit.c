/*
 * Streaming Fallback State Machine — Header Commit Sequence
 *
 * Implements the atomic header commit sequence for streaming mode.
 * The function completes all eligibility checks and header decisions
 * before applying any mutations.  Mutations are applied in a single
 * final step; if any mutation fails, the commit is aborted with no
 * partial Markdown response committed downstream.
 *
 * Design Property 3 / Requirement 6:
 *   Header decisions MUST be completed before outgoing headers are
 *   mutated.  Header mutation is applied in a single final step
 *   after the decision is known.  If the decision fails before
 *   header send, no partial Markdown response may be committed.
 *
 * Rule 39: multi-step header atomicity — abort on first failure,
 * no partial apply.
 */

#include "ngx_http_markdown_stream_commit.h"


/* Function prototypes */

static ngx_int_t
ngx_http_markdown_stream_commit_remove_content_length(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_set_content_type(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_set_vary(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_remove_etag(
    ngx_http_request_t *r);


/*
 * Execute the streaming header commit sequence (task 4.1).
 *
 * Steps:
 *   1. Validate preconditions (not already committed, ctx valid)
 *   2. Apply mutations in sequence (abort on first failure):
 *      a. Remove Content-Length (task 4.2)
 *      b. Set Content-Type to text/markdown (task 4.3)
 *      c. Set Vary: Accept (task 4.4)
 *      d. Remove upstream ETag (task 4.5)
 *   3. Set committed flag (task 4.6)
 *
 * If any mutation step fails, the function returns NGX_ERROR
 * immediately.  The caller is responsible for fallback handling
 * (the partially-mutated headers must not be sent downstream).
 *
 * Returns:
 *   NGX_OK    - All mutations applied, committed flag set
 *   NGX_ERROR - Precondition failure or mutation error
 */
ngx_int_t
ngx_http_markdown_stream_commit_headers(ngx_http_request_t *r,
                                         ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    /*
     * Precondition: validate arguments.
     */
    if (r == NULL || ctx == NULL) {
        return NGX_ERROR;
    }

    /*
     * Precondition: must not already be committed.
     * Double-commit is a logic error in the caller.
     */
    if (ctx->stream_sm.headers_committed) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "headers already committed");
        return NGX_ERROR;
    }

    /*
     * Precondition: state must be PRE_COMMIT or
     * PRE_COMMIT_REPLAY_UNAVAILABLE to allow commit.
     */
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
     * --- Mutation phase ---
     *
     * Rule 39: abort on first failure, no partial apply.
     * Each step returns NGX_OK or NGX_ERROR.
     */

    /* Task 4.2: Remove Content-Length */
    rc = ngx_http_markdown_stream_commit_remove_content_length(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to remove Content-Length");
        return NGX_ERROR;
    }

    /* Task 4.3: Set Content-Type */
    rc = ngx_http_markdown_stream_commit_set_content_type(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to set Content-Type");
        return NGX_ERROR;
    }

    /* Task 4.4: Set Vary: Accept */
    rc = ngx_http_markdown_stream_commit_set_vary(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to set Vary header");
        return NGX_ERROR;
    }

    /* Task 4.5: Remove upstream ETag */
    rc = ngx_http_markdown_stream_commit_remove_etag(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown stream commit: "
                      "failed to remove ETag");
        return NGX_ERROR;
    }

    /*
     * Task 4.6: All mutations succeeded — set committed flag.
     *
     * Transition state to COMMITTED and mark headers as committed.
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
 * Remove Content-Length header (task 4.2).
 *
 * Streaming responses have unknown final length.  Clear the
 * Content-Length numeric field and invalidate the header entry
 * so NGINX does not emit it downstream.
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
 * Set Content-Type to text/markdown; charset=utf-8 (task 4.3).
 *
 * Uses the shared NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL constant
 * defined in ngx_http_markdown_filter_module.h.  Sets the dedicated
 * r->headers_out.content_type field (NGINX emits Content-Type from
 * this field, not from the headers list).
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

    /* Clear charset so NGINX does not append a redundant charset param */
    r->headers_out.charset.len = 0;
    r->headers_out.charset.data = NULL;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown stream commit: "
                   "set Content-Type: text/markdown; charset=utf-8");

    return NGX_OK;
}


/*
 * Set Vary: Accept response header (task 4.4).
 *
 * Delegates to the shared ngx_http_markdown_add_vary_accept()
 * helper which handles:
 *   - Creating the Vary header if absent
 *   - Appending ", Accept" if Vary exists without Accept
 *   - No-op if Accept is already present
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
 * Remove upstream ETag header (task 4.5).
 *
 * The upstream HTML ETag is invalid for the Markdown variant since
 * the content is different.  Delegates to the shared
 * ngx_http_markdown_set_etag() helper with NULL/0 to clear it.
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on failure (should not occur for clear operation)
 */
static ngx_int_t
ngx_http_markdown_stream_commit_remove_etag(
    ngx_http_request_t *r)
{
    return ngx_http_markdown_set_etag(r, NULL, 0);
}
