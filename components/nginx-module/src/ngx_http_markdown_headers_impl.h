/*
 * Shared header-update implementation used by:
 * - ngx_http_markdown_headers.c (production module)
 * - tests/helpers/headers_standalone.c (unit-test harness)
 */

#ifndef NGX_HTTP_MARKDOWN_HEADERS_IMPL_H
#define NGX_HTTP_MARKDOWN_HEADERS_IMPL_H

#ifndef NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
#define NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL 1
#endif

#ifndef NGX_HTTP_MARKDOWN_LOG_DEBUG1
#define NGX_HTTP_MARKDOWN_LOG_DEBUG1(level, log, err, fmt, arg) \
    do {                                                            \
        ngx_log_debug1((level), (log), (err), (fmt), (arg));        \
    } while (0)
#endif

#include "ngx_http_markdown_exports.h"

/*
 * Include the header plan header for the atomic apply function.
 * In standalone test mode, the test harness provides its own stub.
 */
#ifndef NGX_HTTP_MARKDOWN_HEADERS_STANDALONE_TYPES_H
#include "ngx_http_markdown_header_plan.h"
#endif

#ifndef NGX_HTTP_MARKDOWN_SPRINTF_TOKEN
#define NGX_HTTP_MARKDOWN_SPRINTF_TOKEN(buf, token_count) \
    ngx_sprintf((buf), "%ui", (token_count))
#endif

#ifndef ngx_tolower
#define ngx_tolower(c) ((u_char) ((c) >= 'A' && (c) <= 'Z' ? ((c) | 0x20) : (c)))
#endif

static u_char ngx_http_markdown_hdr_vary[] = "Vary";
static u_char ngx_http_markdown_hdr_accept[] = "Accept";
static u_char ngx_http_markdown_hdr_etag[] = "ETag";
static u_char ngx_http_markdown_hdr_content_encoding[] = "Content-Encoding";
static u_char ngx_http_markdown_hdr_accept_ranges[] = "Accept-Ranges";
static u_char ngx_http_markdown_hdr_token_count[] = "X-Markdown-Tokens";
u_char ngx_http_markdown_content_type[] = NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL;
static u_char ngx_http_markdown_vary_suffix[] = ", Accept";

/*
 * Case-insensitive comparison of up to n bytes from two byte strings.
 *
 * Like ngx_strncasecmp, this function compares raw byte pointers and
 * returns early when both characters are NUL (treating it as a match
 * boundary).  Unlike a strict n-byte comparison, encountering NUL in
 * both strings before n bytes yields 0 even if the strings differ
 * beyond the NUL.  This mirrors strncasecmp semantics.
 *
 * Returns 0 when the compared bytes match (case-insensitively) or
 * both bytes are NUL, or the difference of the first mismatching
 * lowercase byte pair.
 *
 * Parameters:
 *   s1 - first byte string
 *   s2 - second byte string
 *   n  - number of bytes to compare
 *
 * Returns:
 *   0 if equal, negative if s1 < s2, positive if s1 > s2
 */
static ngx_int_t
ngx_http_markdown_strncasecmp_const(const u_char *s1, const u_char *s2, size_t n)
{
    ngx_uint_t c1;
    ngx_uint_t c2;

    while (n != 0) {
        c1 = (ngx_uint_t) *s1++;
        c2 = (ngx_uint_t) *s2++;
        c1 = ngx_tolower(c1);
        c2 = ngx_tolower(c2);

        if (c1 == c2) {
            if (c1 == 0) {
                return 0;
            }

            n--;
            continue;
        }

        return c1 - c2;
    }

    return 0;
}

/*
 * Search for a response header by name within a header list part.
 *
 * Walks the linked list of ngx_list_part_t nodes starting from
 * the given part, performing case-insensitive name comparison.
 *
 * part     - first list part to search
 * name     - header name to match (case-insensitive)
 * name_len - length of the header name
 *
 * Returns:
 *   pointer to the matching ngx_table_elt_t on success
 *   NULL if no header with the given name is found
 */
static ngx_table_elt_t *
ngx_http_markdown_find_header_in_part(ngx_list_part_t *part,
                                      const u_char *name,
                                      size_t name_len)
{
    while (part != NULL) {
        ngx_table_elt_t *headers;
        ngx_uint_t i;

        headers = part->elts;
        i = 0;
        while (i < part->nelts) {
            if (headers[i].key.len == name_len
                && ngx_http_markdown_strncasecmp_const(headers[i].key.data,
                                                       name,
                                                       name_len)
                   == 0)
            {
                return &headers[i];
            }
            i++;
        }

        part = part->next;
    }

    return NULL;
}

/*
 * Search for a response header by name in the request's output headers.
 *
 * Convenience wrapper around ngx_http_markdown_find_header_in_part
 * that starts from r->headers_out.headers.part.
 *
 * r        - current HTTP request
 * name     - header name to match (case-insensitive)
 * name_len - length of the header name
 *
 * Returns:
 *   pointer to the matching ngx_table_elt_t on success
 *   NULL if the header list is empty or no match is found
 */
static ngx_table_elt_t *
ngx_http_markdown_find_header(ngx_http_request_t *r, const u_char *name, size_t name_len)
{
    if (r->headers_out.headers.part.nelts == 0) {
        return NULL;
    }

    return ngx_http_markdown_find_header_in_part(&r->headers_out.headers.part,
                                                 name, name_len);
}

/*
 * Invalidate response headers by name within a header list part.
 *
 * Walks the linked list of ngx_list_part_t nodes and sets hash = 0
 * on each matching header, which marks it as removed in NGINX's
 * header output. Optionally stops after the first match and emits
 * a debug log message.
 *
 * r                - current HTTP request (used for logging)
 * part             - first list part to search
 * name             - header name to match (case-insensitive)
 * name_len         - length of the header name
 * stop_after_first - if 1, return after invalidating the first match
 * log_message      - debug message to log per invalidation, or NULL
 */
static void
ngx_http_markdown_invalidate_headers_in_part(const ngx_http_request_t *r,
                                             ngx_list_part_t *part,
                                             const u_char *name,
                                             size_t name_len,
                                             ngx_flag_t stop_after_first,
                                             const char *log_message)
{
    (void) r;

    while (part != NULL) {
        ngx_table_elt_t *headers;
        ngx_uint_t i;

        headers = part->elts;
        i = 0;
        while (i < part->nelts) {
            if (headers[i].key.len != name_len
                || ngx_http_markdown_strncasecmp_const(headers[i].key.data,
                                                       name,
                                                       name_len)
                   != 0)
            {
                i++;
                continue;
            }

            headers[i].hash = 0;
            if (log_message != NULL) {
                ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0, log_message);
            }

            if (stop_after_first) {
                return;
            }
            i++;
        }

        part = part->next;
    }
}

/*
 * Invalidate response headers by name in the request's output headers.
 *
 * Convenience wrapper around ngx_http_markdown_invalidate_headers_in_part
 * that starts from r->headers_out.headers.part.
 *
 * r                - current HTTP request
 * name             - header name to match (case-insensitive)
 * name_len         - length of the header name
 * stop_after_first - if 1, stop after invalidating the first match
 * log_message      - debug message to log per invalidation, or NULL
 */
static void
ngx_http_markdown_invalidate_headers(ngx_http_request_t *r,
                                     const u_char *name,
                                     size_t name_len,
                                     ngx_flag_t stop_after_first,
                                     const char *log_message)
{
    if (r->headers_out.headers.part.nelts == 0) {
        return;
    }

    ngx_http_markdown_invalidate_headers_in_part(r, &r->headers_out.headers.part,
                                                 name, name_len, stop_after_first,
                                                 log_message);
}

/*
 * Check whether a comma-separated header value contains a specific token.
 *
 * Parses the value as a comma-delimited list, trims whitespace from
 * each token, and performs case-insensitive comparison against the
 * target token.
 *
 * value     - the header value string to search
 * token     - the token to look for (case-insensitive)
 * token_len - length of the token
 *
 * Returns:
 *   1 if the token is found in the CSV list
 *   0 otherwise
 */
static ngx_flag_t
ngx_http_markdown_contains_csv_token(const ngx_str_t *value,
                                     const u_char *token,
                                     size_t token_len)
{
    size_t i;

    i = 0;
    while (i < value->len) {
        size_t start;
        size_t end;

        while (i < value->len && (value->data[i] == ' ' || value->data[i] == ',')) {
            i++;
        }

        start = i;
        while (i < value->len && value->data[i] != ',') {
            i++;
        }
        end = i;

        while (end > start && value->data[end - 1] == ' ') {
            end--;
        }

        if (end - start == token_len
            && ngx_http_markdown_strncasecmp_const(value->data + start,
                                                   token,
                                                   token_len)
               == 0)
        {
            return 1;
        }

        if (i < value->len) {
            i++;
        }
    }

    return 0;
}

/*
 * Add or append "Accept" to the Vary response header.
 *
 * If no Vary header exists, creates one with value "Accept".
 * If a Vary header exists but does not already contain the
 * "Accept" token, appends ", Accept" to the existing value.
 * Skips modification if "Accept" is already present.
 *
 * r - current HTTP request
 *
 * Returns:
 *   NGX_OK    on success (header added, appended, or already present)
 *   NGX_ERROR on allocation failure or overflow
 */
ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    ngx_table_elt_t *vary;
    ngx_table_elt_t *h;
    u_char *p;
    size_t len;

    vary = ngx_http_markdown_find_header(r,
                                         ngx_http_markdown_hdr_vary,
                                         sizeof(ngx_http_markdown_hdr_vary) - 1);

    if (vary == NULL) {
        h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }

        h->hash = 1;
        h->key.data = ngx_http_markdown_hdr_vary;
        h->key.len = sizeof(ngx_http_markdown_hdr_vary) - 1;
        h->value.data = ngx_http_markdown_hdr_accept;
        h->value.len = sizeof(ngx_http_markdown_hdr_accept) - 1;

        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: added Vary: Accept header");
        return NGX_OK;
    }

    if (ngx_http_markdown_contains_csv_token(&vary->value,
                                             ngx_http_markdown_hdr_accept,
                                             sizeof(ngx_http_markdown_hdr_accept) - 1))
    {
        NGX_HTTP_MARKDOWN_LOG_DEBUG1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                     "markdown filter: Vary header already contains Accept: \"%V\"",
                                     &vary->value);
        return NGX_OK;
    }

    if (vary->value.len > ((size_t) -1) - (sizeof(ngx_http_markdown_vary_suffix) - 1)) {
        return NGX_ERROR;
    }

    len = vary->value.len + sizeof(ngx_http_markdown_vary_suffix) - 1;
    p = ngx_pnalloc(r->pool, len);
    if (p == NULL) {
        return NGX_ERROR;
    }

    p = ngx_cpymem(p, vary->value.data, vary->value.len);
    p = ngx_cpymem(p,
                   ngx_http_markdown_vary_suffix,
                   sizeof(ngx_http_markdown_vary_suffix) - 1);

    vary->value.data = p - len;
    vary->value.len = len;

    NGX_HTTP_MARKDOWN_LOG_DEBUG1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                 "markdown filter: updated Vary header: \"%V\"",
                                 &vary->value);

    return NGX_OK;
}

/*
 * Set or clear the ETag response header.
 *
 * First invalidates all existing ETag headers in the output list.
 * If etag is NULL or etag_len is 0, clears r->headers_out.etag
 * (removes the ETag). Otherwise, allocates a new header entry
 * with the provided ETag value.
 *
 * r        - current HTTP request
 * etag     - ETag value bytes, or NULL to clear
 * etag_len - length of the ETag value
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on allocation failure
 */
ngx_int_t
ngx_http_markdown_set_etag(ngx_http_request_t *r, const u_char *etag, size_t etag_len)
{
    ngx_table_elt_t *h;

    ngx_http_markdown_invalidate_headers(r,
                                         ngx_http_markdown_hdr_etag,
                                         sizeof(ngx_http_markdown_hdr_etag) - 1,
                                         0,
                                         NULL);

    if (etag == NULL || etag_len == 0) {
        r->headers_out.etag = NULL;
        return NGX_OK;
    }

    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    h->key.data = ngx_http_markdown_hdr_etag;
    h->key.len = sizeof(ngx_http_markdown_hdr_etag) - 1;

    h->value.data = ngx_pnalloc(r->pool, etag_len);
    if (h->value.data == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(h->value.data, etag, etag_len);
    h->value.len = etag_len;
    r->headers_out.etag = h;

    NGX_HTTP_MARKDOWN_LOG_DEBUG1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                 "markdown filter: set ETag: \"%V\"", &h->value);

    return NGX_OK;
}

/*
 * Add the X-Markdown-Tokens response header with the estimated token count.
 *
 * Skips header creation when token_count is 0. Formats the count
 * as a decimal string using NGX_HTTP_MARKDOWN_SPRINTF_TOKEN.
 *
 * r           - current HTTP request
 * token_count - estimated token count to emit
 *
 * Returns:
 *   NGX_OK    on success or when token_count is 0
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_add_token_header(ngx_http_request_t *r, uint32_t token_count)
{
    ngx_table_elt_t *h;
    const u_char *p;

    if (token_count == 0) {
        return NGX_OK;
    }

    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    h->key.data = ngx_http_markdown_hdr_token_count;
    h->key.len = sizeof(ngx_http_markdown_hdr_token_count) - 1;

    h->value.data = ngx_pnalloc(r->pool, NGX_INT32_LEN);
    if (h->value.data == NULL) {
        return NGX_ERROR;
    }

    p = NGX_HTTP_MARKDOWN_SPRINTF_TOKEN(h->value.data, token_count);
    h->value.len = p - h->value.data;

    NGX_HTTP_MARKDOWN_LOG_DEBUG1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                 "markdown filter: added X-Markdown-Tokens: %ui", token_count);

    return NGX_OK;
}

/*
 * Remove the Content-Encoding response header.
 *
 * Clears r->headers_out.content_encoding and invalidates the
 * first Content-Encoding entry in the output header list.
 * Called after decompression so the downstream response does
 * not claim a transfer encoding that no longer applies.
 *
 * r - current HTTP request
 */
void
ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r)
{
    r->headers_out.content_encoding = NULL;

    ngx_http_markdown_invalidate_headers(r,
                                         ngx_http_markdown_hdr_content_encoding,
                                         sizeof(ngx_http_markdown_hdr_content_encoding) - 1,
                                         1,
                                         "markdown filter: removed Content-Encoding header");
}

/*
 * Remove the Accept-Ranges response header.
 *
 * Clears r->allow_ranges and r->headers_out.accept_ranges,
 * then invalidates the first Accept-Ranges entry in the output
 * header list. Prevents clients from requesting byte ranges on
 * the converted Markdown response.
 *
 * r - current HTTP request
 */
static void
ngx_http_markdown_remove_accept_ranges(ngx_http_request_t *r)
{
    r->allow_ranges = 0;
    r->headers_out.accept_ranges = NULL;

    ngx_http_markdown_invalidate_headers(r,
                                         ngx_http_markdown_hdr_accept_ranges,
                                         sizeof(ngx_http_markdown_hdr_accept_ranges) - 1,
                                         1,
                                         "markdown filter: removed Accept-Ranges header");
}

/*
 * Update all response headers for a completed full-buffer conversion.
 *
 * Sets Content-Type to text/markdown; charset=utf-8, adds Vary: Accept,
 * replaces Content-Length with the Markdown body length, sets or clears
 * the ETag based on configuration, adds X-Markdown-Tokens if enabled,
 * removes Content-Encoding and Accept-Ranges, and adjusts Cache-Control
 * for authenticated content when auth cache control is enabled.
 *
 * Atomic plan application with post-plan Content-Length:
 *
 *   The header plan (built by Rust) is applied atomically via
 *   ngx_http_markdown_apply_header_plan().  All plan operations
 *   succeed or all are rolled back.  The plan includes:
 *
 *   - Content-Type (set to text/markdown; charset=utf-8)
 *   - Content-Encoding (delete)
 *   - Content-Length (delete — invalidates the stale original)
 *   - Vary (set to Accept)
 *   - ETag (set-etag-placeholder, if configured)
 *
 *   After successful atomic plan application, the C side sets:
 *
 *   - Content-Length (new value from result->markdown_len)
 *   - X-Markdown-Tokens (if enabled)
 *   - Accept-Ranges (delete)
 *   - Cache-Control (auth modification, if applicable)
 *
 *   This is safe because the plan already committed — if the plan
 *   had failed, we would not reach the post-plan operations.
 *   The post-plan Content-Length set is guaranteed to execute only
 *   after successful plan application.
 *
 * r      - current HTTP request
 * result - completed MarkdownResult from the Rust converter
 * conf   - location configuration
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on NULL arguments or atomic plan failure
 */
ngx_int_t
ngx_http_markdown_update_headers(ngx_http_request_t *r,
                                 const struct MarkdownResult *result,
                                 const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t              rc;
    struct FFIHeaderPlan   plan;

    if (r == NULL || result == NULL || conf == NULL) {
        return NGX_ERROR;
    }

    /*
     * Build header plan from Rust FFI.
     *
     * The plan covers: Content-Type (set), Content-Encoding (delete),
     * Content-Length (delete), Vary (set), and ETag (set-etag-placeholder
     * or omit).
     *
     * Content-Length deletion invalidates the stale original value.
     * The correct post-conversion length is set below after the plan
     * commits successfully.
     */
    markdown_header_plan_init(&plan);
    markdown_build_header_plan(
        ngx_http_markdown_content_type,
        NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN,
        (conf->policy.generate_etag
         && result->etag != NULL
         && result->etag_len > 0) ? 1 : 0,
        &plan);

    /*
     * Apply the plan atomically.  On failure, all changes are
     * rolled back and we return NGX_ERROR.  The plan is freed
     * by ngx_http_markdown_apply_header_plan() in both success
     * and failure paths.
     */
    rc = ngx_http_markdown_apply_header_plan(r, &plan);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown filter: atomic header plan application "
            "failed; all changes rolled back");
        return NGX_ERROR;
    }

    /*
     * Post-plan operations.  These execute only after the atomic
     * plan has committed successfully.
     *
     * Content-Type was set by the plan via the generic SET path.
     * Override with the static buffer for efficiency (the plan
     * entry used pool-copied data which is correct but we prefer
     * the static buffer for the well-known content type).
     */
    r->headers_out.content_type.data = ngx_http_markdown_content_type;
    r->headers_out.content_type.len = NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN;
    r->headers_out.content_type_len = NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN;
    r->headers_out.charset.len = 0;
    r->headers_out.charset.data = NULL;

    /*
     * ETag: if the plan included a SetEtagPlaceholder entry, the
     * atomic applier treated it as a generic SET with empty value.
     * Apply the real ETag value now.
     */
    if (conf->policy.generate_etag
        && result->etag != NULL
        && result->etag_len > 0)
    {
        rc = ngx_http_markdown_set_etag(r,
            result->etag, result->etag_len);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown filter: failed to set ETag "
                "after plan commit");
            return NGX_ERROR;
        }
    } else {
        rc = ngx_http_markdown_set_etag(r, NULL, 0);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown filter: failed to clear ETag "
                "after plan commit");
            return NGX_ERROR;
        }
    }

    /*
     * Add Vary: Accept header.  This is a post-plan operation
     * because it uses ngx_list_push which is NGINX-specific
     * and not part of the Rust plan.
     */
    rc = ngx_http_markdown_add_vary_accept(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown filter: failed to add Vary header");
        return NGX_ERROR;
    }

    /*
     * Set the new Content-Length.  The plan deleted the stale
     * original; now we set the correct post-conversion value.
     * This is guaranteed to execute only after successful plan
     * application.
     */
    ngx_http_clear_content_length(r);
    r->headers_out.content_length_n = result->markdown_len;

    NGX_HTTP_MARKDOWN_LOG_DEBUG1(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown filter: set Content-Length: %uz",
        result->markdown_len);

    if (conf->token_estimate && result->token_estimate > 0) {
        rc = ngx_http_markdown_add_token_header(r,
            result->token_estimate);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown filter: failed to add "
                "X-Markdown-Tokens header");
            return NGX_ERROR;
        }
    }

    ngx_http_markdown_remove_accept_ranges(r);

#if NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
    if (ngx_http_markdown_is_authenticated(r, conf)) {
        rc = ngx_http_markdown_modify_cache_control_for_auth(r);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown filter: failed to modify "
                "Cache-Control for authenticated "
                "content");
            return NGX_ERROR;
        }
    }
#endif

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
        "markdown filter: headers updated successfully");

    return NGX_OK;
}

#endif /* NGX_HTTP_MARKDOWN_HEADERS_IMPL_H */
