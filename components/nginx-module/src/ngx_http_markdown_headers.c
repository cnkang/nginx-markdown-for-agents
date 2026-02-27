/*
 * NGINX Markdown Filter Module - Header Management
 *
 * This file implements HTTP header manipulation for Markdown responses.
 * It handles updating response headers after successful HTML to Markdown
 * conversion to ensure correct HTTP semantics.
 *
 * Requirements: FR-04.1, FR-04.2, FR-04.3, FR-04.6, FR-04.7, FR-04.8, FR-15.2
 * Task: 17.1 Implement header update function
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

/*
 * Add or update Vary header to include "Accept"
 *
 * The Vary header tells caches that the response varies based on the
 * Accept header, ensuring that HTML and Markdown variants are cached
 * separately.
 *
 * Behavior:
 * - If no Vary header exists: Add "Vary: Accept"
 * - If Vary header exists without "Accept": Append ", Accept"
 * - If Vary header already contains "Accept": No change
 *
 * Requirements: FR-04.2, FR-06.5
 *
 * @param r  The request structure
 * @return   NGX_OK on success, NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    ngx_table_elt_t  *vary;
    ngx_table_elt_t  *h;
    u_char           *p;
    size_t            len;
    ngx_flag_t        has_accept;

    /* Check if Vary header already exists */
    vary = NULL;
    if (r->headers_out.headers.part.nelts > 0) {
        ngx_list_part_t  *part;
        ngx_table_elt_t  *header;
        ngx_uint_t        i;

        part = &r->headers_out.headers.part;
        header = part->elts;

        for (i = 0; /* void */; i++) {
            if (i >= part->nelts) {
                if (part->next == NULL) {
                    break;
                }
                part = part->next;
                header = part->elts;
                i = 0;
            }

            /* Case-insensitive comparison for Vary header */
            if (header[i].key.len == sizeof("Vary") - 1
                && ngx_strncasecmp(header[i].key.data,
                                  (u_char *) "Vary",
                                  sizeof("Vary") - 1) == 0)
            {
                vary = &header[i];
                break;
            }
        }
    }

    if (vary == NULL) {
        /* No Vary header exists - create new one with "Accept" */
        h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }

        h->hash = 1;
        ngx_str_set(&h->key, "Vary");
        ngx_str_set(&h->value, "Accept");

        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: added Vary: Accept header");

        return NGX_OK;
    }

    /* Vary header exists - check if it already contains "Accept" */
    has_accept = 0;
    p = vary->value.data;
    len = vary->value.len;

    /* Simple substring search for "Accept" (case-insensitive) */
    if (len >= 6) {  /* "Accept" is 6 characters */
        size_t i;
        for (i = 0; i <= len - 6; i++) {
            if (ngx_strncasecmp(p + i, (u_char *) "Accept", 6) == 0) {
                /* Check if it's a complete word (not part of another word) */
                if ((i == 0 || p[i-1] == ' ' || p[i-1] == ',') &&
                    (i + 6 == len || p[i+6] == ' ' || p[i+6] == ','))
                {
                    has_accept = 1;
                    break;
                }
            }
        }
    }

    if (has_accept) {
        /* Vary header already contains "Accept" - no change needed */
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: Vary header already contains Accept: \"%V\"",
                      &vary->value);
        return NGX_OK;
    }

    /* Vary header exists but doesn't contain "Accept" - append it */
    if (vary->value.len > ((size_t) -1) - (sizeof(", Accept") - 1)) {
        return NGX_ERROR;
    }
    len = vary->value.len + sizeof(", Accept") - 1;
    p = ngx_pnalloc(r->pool, len);
    if (p == NULL) {
        return NGX_ERROR;
    }

    /* Copy existing Vary value */
    p = ngx_cpymem(p, vary->value.data, vary->value.len);

    /* Append ", Accept" */
    p = ngx_cpymem(p, ", Accept", sizeof(", Accept") - 1);

    /* Update Vary header value */
    vary->value.data = p - len;
    vary->value.len = len;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: updated Vary header: \"%V\"",
                  &vary->value);

    return NGX_OK;
}

/*
 * Set or update ETag header
 *
 * Sets the ETag header for the Markdown variant. The ETag is generated
 * by the Rust conversion engine as a BLAKE3 hash of the Markdown output.
 *
 * The original upstream ETag (if any) is removed because it represents
 * the HTML content, not the Markdown variant. Using the original ETag
 * would violate HTTP semantics and cause cache inconsistencies.
 *
 * Requirements: FR-04.5, FR-06.4
 *
 * @param r         The request structure
 * @param etag      Pointer to ETag string (UTF-8 bytes)
 * @param etag_len  Length of ETag string in bytes
 * @return          NGX_OK on success, NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_set_etag(ngx_http_request_t *r, const u_char *etag, size_t etag_len)
{
    ngx_table_elt_t  *h;
    ngx_list_part_t  *part;
    ngx_table_elt_t  *header;
    ngx_uint_t        i;

    /* Remove any upstream ETag headers from the raw headers list */
    if (r->headers_out.headers.part.nelts > 0) {
        part = &r->headers_out.headers.part;
        header = part->elts;

        for ( ;; ) {
            for (i = 0; i < part->nelts; i++) {
                if (header[i].key.len == sizeof("ETag") - 1
                    && ngx_strncasecmp(header[i].key.data,
                                      (u_char *) "ETag",
                                      sizeof("ETag") - 1) == 0)
                {
                    header[i].hash = 0;
                }
            }

            if (part->next == NULL) {
                break;
            }

            part = part->next;
            header = part->elts;
        }
    }

    if (etag == NULL || etag_len == 0) {
        /* No ETag to set - remove any existing ETag */
        r->headers_out.etag = NULL;
        return NGX_OK;
    }

    /* Create new ETag header */
    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    ngx_str_set(&h->key, "ETag");

    /* Allocate and copy ETag value */
    h->value.data = ngx_pnalloc(r->pool, etag_len);
    if (h->value.data == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(h->value.data, etag, etag_len);
    h->value.len = etag_len;

    /* Update headers_out.etag pointer for NGINX's internal use */
    r->headers_out.etag = h;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: set ETag: \"%V\"", &h->value);

    return NGX_OK;
}

/*
 * Add X-Markdown-Tokens header
 *
 * Adds the X-Markdown-Tokens header with the estimated token count.
 * This helps AI agents manage their context windows by knowing the
 * approximate token count before processing the content.
 *
 * Requirements: FR-15.1, FR-15.2
 *
 * @param r             The request structure
 * @param token_count   Estimated token count
 * @return              NGX_OK on success, NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_add_token_header(ngx_http_request_t *r, uint32_t token_count)
{
    ngx_table_elt_t  *h;
    u_char           *p;

    if (token_count == 0) {
        /* No token count to add */
        return NGX_OK;
    }

    /* Create X-Markdown-Tokens header */
    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    ngx_str_set(&h->key, "X-Markdown-Tokens");

    /* Allocate space for token count string (max 10 digits for uint32_t) */
    h->value.data = ngx_pnalloc(r->pool, NGX_INT32_LEN);
    if (h->value.data == NULL) {
        return NGX_ERROR;
    }

    /* Convert token count to string */
    p = ngx_sprintf(h->value.data, "%ui", token_count);
    h->value.len = p - h->value.data;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: added X-Markdown-Tokens: %ui", token_count);

    return NGX_OK;
}

/*
 * Remove Content-Encoding header
 *
 * Removes the Content-Encoding header from the response because the
 * Markdown output is uncompressed. The upstream response may have been
 * compressed (gzip, br, deflate), but after conversion, the content is
 * plain text.
 *
 * NGINX can re-compress the response downstream if configured with
 * gzip or brotli modules.
 *
 * Requirements: FR-04.4
 *
 * @param r  The request structure
 */
void
ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r)
{
    /* Clear Content-Encoding header */
    r->headers_out.content_encoding = NULL;

    /* Also remove from headers list */
    if (r->headers_out.headers.part.nelts > 0) {
        ngx_list_part_t  *part;
        ngx_table_elt_t  *header;
        ngx_uint_t        i;

        part = &r->headers_out.headers.part;
        header = part->elts;

        for (i = 0; /* void */; i++) {
            if (i >= part->nelts) {
                if (part->next == NULL) {
                    break;
                }
                part = part->next;
                header = part->elts;
                i = 0;
            }

            /* Case-insensitive comparison for Content-Encoding */
            if (header[i].key.len == sizeof("Content-Encoding") - 1
                && ngx_strncasecmp(header[i].key.data,
                                  (u_char *) "Content-Encoding",
                                  sizeof("Content-Encoding") - 1) == 0)
            {
                /* Mark header as deleted by setting hash to 0 */
                header[i].hash = 0;
                ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                              "markdown filter: removed Content-Encoding header");
                break;
            }
        }
    }
}

/*
 * Remove Accept-Ranges header
 *
 * Removes the Accept-Ranges header because Markdown variants do not
 * support range requests in v1. Range requests on Markdown would produce
 * invalid or incomplete Markdown.
 *
 * Requirements: FR-07.1, FR-07.2, FR-07.3
 *
 * @param r  The request structure
 */
static void
ngx_http_markdown_remove_accept_ranges(ngx_http_request_t *r)
{
    /* Prevent downstream header filters from re-adding Accept-Ranges */
    r->allow_ranges = 0;

    /* Clear Accept-Ranges header */
    r->headers_out.accept_ranges = NULL;

    /* Also remove from headers list */
    if (r->headers_out.headers.part.nelts > 0) {
        ngx_list_part_t  *part;
        ngx_table_elt_t  *header;
        ngx_uint_t        i;

        part = &r->headers_out.headers.part;
        header = part->elts;

        for (i = 0; /* void */; i++) {
            if (i >= part->nelts) {
                if (part->next == NULL) {
                    break;
                }
                part = part->next;
                header = part->elts;
                i = 0;
            }

            /* Case-insensitive comparison for Accept-Ranges */
            if (header[i].key.len == sizeof("Accept-Ranges") - 1
                && ngx_strncasecmp(header[i].key.data,
                                  (u_char *) "Accept-Ranges",
                                  sizeof("Accept-Ranges") - 1) == 0)
            {
                /* Mark header as deleted by setting hash to 0 */
                header[i].hash = 0;
                ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                              "markdown filter: removed Accept-Ranges header");
                break;
            }
        }
    }
}

/*
 * Update response headers for Markdown variant
 *
 * This is the main header update function that coordinates all header
 * modifications after successful HTML to Markdown conversion.
 *
 * Header Updates:
 * 1. Set Content-Type to "text/markdown; charset=utf-8" (FR-04.1)
 * 2. Add or update Vary header to include "Accept" (FR-04.2)
 * 3. Update Content-Length to match Markdown output size (FR-04.3)
 * 4. Set ETag if generated (FR-04.5)
 * 5. Add X-Markdown-Tokens header if enabled (FR-15.2)
 * 6. Remove Content-Encoding (FR-04.4)
 * 7. Remove Accept-Ranges (FR-07.3)
 * 8. Preserve Cache-Control, Last-Modified, Set-Cookie (FR-04.6, FR-04.7, FR-04.8)
 *
 * Headers that are preserved (not modified):
 * - Cache-Control: Upstream caching policy is respected
 * - Last-Modified: Represents upstream content modification time
 * - Set-Cookie: Session management must not be interfered with
 *
 * Requirements: FR-04.1, FR-04.2, FR-04.3, FR-04.4, FR-04.5, FR-04.6,
 *               FR-04.7, FR-04.8, FR-15.2
 *
 * @param r             The request structure
 * @param result        Conversion result from Rust engine
 * @param conf          Module configuration
 * @return              NGX_OK on success, NGX_ERROR on failure
 */
ngx_int_t
ngx_http_markdown_update_headers(ngx_http_request_t *r,
                                 struct MarkdownResult *result,
                                 ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    if (r == NULL || result == NULL || conf == NULL) {
        return NGX_ERROR;
    }

    /*
     * 1. Set Content-Type to "text/markdown; charset=utf-8" (FR-04.1, FR-05.5)
     *
     * This tells clients that the response is Markdown encoded in UTF-8.
     * The charset parameter is required because the Rust converter always
     * outputs UTF-8, regardless of the input encoding.
     */
    r->headers_out.content_type.len = sizeof("text/markdown; charset=utf-8") - 1;
    r->headers_out.content_type.data = (u_char *) "text/markdown; charset=utf-8";
    r->headers_out.content_type_len = r->headers_out.content_type.len;
    /*
     * Upstream modules (for example proxy) may leave `headers_out.charset`
     * populated. Keep it cleared here to prevent NGINX core from appending
     * an extra "; charset=..." suffix and producing duplicates.
     */
    r->headers_out.charset.len = 0;
    r->headers_out.charset.data = NULL;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: set Content-Type: text/markdown; charset=utf-8");

    /*
     * 2. Add or update Vary header to include "Accept" (FR-04.2, FR-06.5)
     *
     * The Vary header is critical for correct caching behavior. It tells
     * caches that the response varies based on the Accept header, ensuring
     * that HTML and Markdown variants are cached separately.
     *
     * Without this header, caches might serve HTML to clients requesting
     * Markdown, or vice versa.
     */
    rc = ngx_http_markdown_add_vary_accept(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to add Vary header");
        return NGX_ERROR;
    }

    /*
     * 3. Update Content-Length to match Markdown output size (FR-04.3)
     *
     * The Content-Length must accurately reflect the size of the Markdown
     * output. This is required for:
     * - HTTP/1.1 persistent connections
     * - Progress indicators in clients
     * - Bandwidth accounting
     *
     * We clear the old Content-Length first, then set the new value.
     */
    ngx_http_clear_content_length(r);
    r->headers_out.content_length_n = result->markdown_len;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: set Content-Length: %uz", result->markdown_len);

    /*
     * 4. Set ETag if generated (FR-04.5, FR-06.4)
     *
     * The ETag is a hash of the Markdown output, not the original HTML.
     * This is critical for correct caching:
     * - The original ETag represents the HTML content
     * - The Markdown variant is different content
     * - Using the original ETag would violate HTTP semantics
     *
     * The Rust converter generates the ETag using BLAKE3 hash of the
     * Markdown output, ensuring consistent ETags for identical content.
     */
    if (conf->generate_etag && result->etag != NULL && result->etag_len > 0) {
        rc = ngx_http_markdown_set_etag(r, result->etag, result->etag_len);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to set ETag header");
            return NGX_ERROR;
        }
    } else {
        /* Remove any existing ETag from upstream */
        rc = ngx_http_markdown_set_etag(r, NULL, 0);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to clear ETag header");
            return NGX_ERROR;
        }
    }

    /*
     * 5. Add X-Markdown-Tokens header if enabled (FR-15.1, FR-15.2)
     *
     * This header helps AI agents manage their context windows by providing
     * an estimated token count. The estimate is calculated by the Rust
     * converter using a character count / 4 heuristic.
     *
     * This is an optional feature (v1.1) that can be enabled via configuration.
     */
    if (conf->token_estimate && result->token_estimate > 0) {
        rc = ngx_http_markdown_add_token_header(r, result->token_estimate);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to add X-Markdown-Tokens header");
            /* Non-fatal error - continue without token header */
        }
    }

    /*
     * 6. Remove Content-Encoding (FR-04.4)
     *
     * The upstream response may have been compressed (gzip, br, deflate),
     * but after conversion, the Markdown output is uncompressed plain text.
     *
     * NGINX can re-compress the response downstream if configured with
     * gzip or brotli modules.
     */
    ngx_http_markdown_remove_content_encoding(r);

    /*
     * 7. Remove Accept-Ranges (FR-07.3)
     *
     * Markdown variants do not support range requests in v1. Range requests
     * on Markdown would produce invalid or incomplete Markdown.
     *
     * We remove the Accept-Ranges header to prevent clients from attempting
     * range requests on Markdown variants.
     */
    ngx_http_markdown_remove_accept_ranges(r);

    /*
     * 8. Preserve important upstream headers (FR-04.6, FR-04.7, FR-04.8)
     *
     * The following headers are NOT modified and are passed through from
     * the upstream response:
     *
     * - Cache-Control: Upstream caching policy is respected. The module
     *   does not override caching directives unless dealing with
     *   authenticated content (handled separately in FR-08.3).
     *
     * - Last-Modified: Represents the upstream content modification time.
     *   This is useful for If-Modified-Since conditional requests, which
     *   can be evaluated without performing conversion.
     *
     * - Set-Cookie: Session management and authentication cookies must
     *   not be interfered with. The module passes these through unchanged.
     *
     * These headers are preserved automatically by NGINX's filter chain
     * unless explicitly removed or modified above.
     */

    /*
     * 9. Modify Cache-Control for authenticated content (FR-08.3)
     *
     * If the request is authenticated (Authorization header or auth cookies),
     * modify the Cache-Control header to ensure private caching only.
     *
     * This prevents public caching of personalized content and protects
     * against information leakage through shared caches.
     *
     * Rules:
     * - If no Cache-Control: Add "Cache-Control: private"
     * - If Cache-Control allows public caching: Upgrade to "private"
     * - If Cache-Control is "no-store": Preserve as-is (never downgrade)
     */
    if (ngx_http_markdown_is_authenticated(r, conf)) {
        rc = ngx_http_markdown_modify_cache_control_for_auth(r);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to modify Cache-Control for authenticated content");
            /* Non-fatal error - continue without cache control modification */
        }
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: headers updated successfully");

    return NGX_OK;
}
