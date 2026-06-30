/*
 * NGINX Markdown Filter Module - Accept Header Negotiation
 *
 * Delegates Accept header content negotiation to the Rust FFI
 * (markdown_negotiate_accept) which implements RFC 7231 §5.3.2
 * q-value comparison and RFC 9110 tie-break rules.
 *
 * The C side retains only the NGINX request-level glue:
 * extracting the Accept header from ngx_http_request_t and
 * mapping the FFIAcceptResult.reason code to metrics counters
 * and decision-log reason codes.
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

static u_char  ngx_http_markdown_hdr_accept[] = "Accept";

/*
 * Find a request header by name in nginx's generic linked-list container.
 *
 * Complexity is O(n) over all request headers, which is acceptable because
 * request header counts are typically small and this runs once per decision.
 */
static ngx_table_elt_t *
ngx_http_markdown_find_request_header(ngx_http_request_t *r,
    u_char *name, size_t name_len)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *headers;

    if (r == NULL || name == NULL || name_len == 0) {
        return NULL;
    }

    part = &r->headers_in.headers.part;
    headers = part->elts;

    for ( ;; ) {
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (headers[i].key.len == name_len
                && ngx_strncasecmp(headers[i].key.data, name, name_len) == 0)
            {
                return &headers[i];
            }
        }

        if (part->next == NULL) {
            break;
        }

        part = part->next;
        headers = part->elts;
    }

    return NULL;
}

/*
 * Retrieve the Accept header from the request.
 *
 * Parameters:
 *   r  - HTTP request (may be NULL)
 *
 * Returns:
 *   Pointer to the Accept header entry, or NULL if absent or r is NULL.
 */
static ngx_table_elt_t *
ngx_http_markdown_get_accept_header(ngx_http_request_t *r)
{
    if (r == NULL) {
        return NULL;
    }

#if (NGX_HTTP_HEADERS)
    if (r->headers_in.accept != NULL) {
        return r->headers_in.accept;
    }
#endif

    return ngx_http_markdown_find_request_header(
        r,
        (u_char *) ngx_http_markdown_hdr_accept,
        sizeof(ngx_http_markdown_hdr_accept) - 1);
}

/*
 * Determine if request should be converted to Markdown.
 *
 * Delegates to markdown_negotiate_accept FFI for RFC 7231 §5.3.2
 * q-value comparison with RFC 9110 tie-break rules.
 *
 * The FFIAcceptResult.reason field maps to skip metrics:
 *   0: Convert (text/markdown preferred)
 *   1: No Accept header present
 *   2: text/markdown has lower q-value than text/html
 *   3: text/markdown;q=0 explicit reject
 *   4: Malformed Accept header
 *
 * Parameters:
 *   r          - The request structure
 *   conf       - Module configuration
 *   out_reason - Output: FFI reason code (NEGOTIATE_REASON_*)
 *                Set to NEGOTIATE_REASON_NO_ACCEPT when Accept
 *                header is absent.  May be NULL if caller does
 *                not need the reason.
 *
 * Returns:
 *   1 if should convert, 0 if not
 */
ngx_int_t
ngx_http_markdown_should_convert(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t *out_reason)
{
    const ngx_table_elt_t   *accept_header;
    struct FFIAcceptResult   result;
    uint8_t                  on_wildcard;

    if (conf == NULL) {
        if (out_reason != NULL) {
            *out_reason = NEGOTIATE_REASON_NO_ACCEPT;
        }
        return 0;
    }

    /*
     * markdown_accept force: convert regardless of the Accept header,
     * including when no Accept header is present.  This short-circuits
     * before the header lookup so a missing Accept still converts.
     */
    if (conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_FORCE) {
        if (out_reason != NULL) {
            *out_reason = NEGOTIATE_REASON_CONVERT;
        }
        if (r != NULL) {
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                         "markdown: Accept negotiation: convert "
                         "(accept_policy=force)");
        }
        return 1;
    }

    accept_header = ngx_http_markdown_get_accept_header(r);
    if (accept_header == NULL || accept_header->value.len == 0) {
        if (out_reason != NULL) {
            *out_reason = NEGOTIATE_REASON_NO_ACCEPT;
        }
        return 0;
    }

    on_wildcard = (uint8_t)
        ((conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD) ? 1 : 0);

    markdown_negotiate_accept(
        accept_header->value.data,
        accept_header->value.len,
        on_wildcard,
        &result);

    if (out_reason != NULL) {
        *out_reason = (ngx_uint_t) result.reason;
    }

    if (result.should_convert) {
        ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                     "markdown: Accept negotiation: convert, "
                     "reason=%ud, accept_policy=%ud",
                     (ngx_uint_t) result.reason,
                     (ngx_uint_t) conf->accept_policy);
        return 1;
    }

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                 "markdown: Accept negotiation: skip, reason=%ud",
                 (ngx_uint_t) result.reason);
    return 0;
}
