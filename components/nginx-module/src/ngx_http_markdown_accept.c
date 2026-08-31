/*
 * NGINX Markdown Filter Module - Accept Header Negotiation
 *
 * Delegates Accept header content negotiation to the Rust FFI
 * (markdown_negotiate_accept) which implements RFC 9110 §12.5.1
 * q-value comparison and specificity tie-break rules.
 *
 * The C side retains only the NGINX request-level glue:
 * extracting the Accept header from ngx_http_request_t and
 * mapping the FFIAcceptResult.reason code to metrics counters
 * and decision-log reason codes.
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

static u_char  ngx_http_markdown_hdr_accept[] = "Accept";

#define NGX_HTTP_MARKDOWN_ACCEPT_HEADER_MAX  8192


static ngx_flag_t ngx_http_markdown_is_accept_header(
    const ngx_table_elt_t *header);
static ngx_int_t ngx_http_markdown_count_accept_headers(
    ngx_http_request_t *r, ngx_uint_t *count);
static ngx_int_t ngx_http_markdown_collect_accept_header(
    ngx_http_request_t *r, ngx_str_t *out);

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


static ngx_flag_t
ngx_http_markdown_is_accept_header(const ngx_table_elt_t *header)
{
    return header != NULL
           && header->hash != 0
           && header->key.data != NULL
           && header->key.len == sizeof(ngx_http_markdown_hdr_accept) - 1
           && ngx_strncasecmp(header->key.data,
                              ngx_http_markdown_hdr_accept,
                              sizeof(ngx_http_markdown_hdr_accept) - 1) == 0;
}


static ngx_int_t
ngx_http_markdown_count_accept_headers(ngx_http_request_t *r,
    ngx_uint_t *count)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *headers;

    if (r == NULL || count == NULL) {
        return NGX_ERROR;
    }

    *count = 0;
    for (part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return NGX_ERROR;
        }

        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (!ngx_http_markdown_is_accept_header(&headers[i])) {
                continue;
            }

            (*count)++;
            if (*count > 1) {
                return NGX_OK;
            }
        }
    }

    return NGX_OK;
}


/*
 * Compute the combined Accept field length and entry count.
 *
 * Walks the header list once, validating each Accept field-line and
 * accumulating the combined length (RFC 9110 section 5.2: multiple
 * field-lines combine with ", ").  Falls back to the typed
 * r->headers_in.accept singleton when the list scan found nothing.
 *
 * Returns:
 *   NGX_OK       - at least one field-line found; *count / *total_len set
 *   NGX_DECLINED - no Accept field-line present
 *   NGX_ERROR    - malformed entry or combined length above the cap
 */
static ngx_int_t
ngx_http_markdown_accept_fields(ngx_http_request_t *r,
    ngx_uint_t *count, size_t *total_len, ngx_table_elt_t **single_out)
{
    ngx_table_elt_t  *single;
    ngx_list_part_t  *part;
    ngx_table_elt_t  *headers;

    *count = 0;
    *total_len = 0;
    single = NULL;

    for (part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return NGX_ERROR;
        }

        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (!ngx_http_markdown_is_accept_header(&headers[i])) {
                continue;
            }
            if (headers[i].value.len > 0
                && headers[i].value.data == NULL)
            {
                return NGX_ERROR;
            }

            if (*count != 0) {
                if (*total_len > (size_t) -1 - 2) {
                    return NGX_ERROR;
                }
                *total_len += 2;
            }
            if (*total_len > NGX_HTTP_MARKDOWN_ACCEPT_HEADER_MAX
                || headers[i].value.len
                   > NGX_HTTP_MARKDOWN_ACCEPT_HEADER_MAX - *total_len)
            {
                return NGX_ERROR;
            }
            *total_len += headers[i].value.len;

            if (single == NULL) {
                single = &headers[i];
            }
            (*count)++;
        }
    }

#if (NGX_HTTP_HEADERS)
    if (*count == 0 && r->headers_in.accept != NULL) {
        if (r->headers_in.accept->value.len > 0
            && r->headers_in.accept->value.data == NULL)
        {
            return NGX_ERROR;
        }
        single = r->headers_in.accept;
        *count = 1;
        *total_len = single->value.len;
        if (*total_len > NGX_HTTP_MARKDOWN_ACCEPT_HEADER_MAX) {
            return NGX_ERROR;
        }
    }
#endif

    if (*count == 0) {
        return NGX_DECLINED;
    }
    if (single_out != NULL) {
        *single_out = single;
    }
    return NGX_OK;
}


/* Collect all active Accept field-lines in wire order. */
static ngx_int_t
ngx_http_markdown_collect_accept_header(ngx_http_request_t *r,
    ngx_str_t *out)
{
    ngx_table_elt_t  *headers;
    ngx_table_elt_t  *single;
    ngx_list_part_t  *part;
    ngx_uint_t        count;
    ngx_int_t         rc;
    size_t            total_len;
    size_t            written;
    u_char           *data;

    if (r == NULL || out == NULL) {
        return NGX_ERROR;
    }

    out->data = NULL;
    out->len = 0;
    count = 0;
    total_len = 0;
    single = NULL;

    /* Pass 1: validate and measure the combined value. */
    rc = ngx_http_markdown_accept_fields(r, &count, &total_len, &single);
    if (rc == NGX_DECLINED) {
        /* No Accept field-line present; propagate so the caller reports
         * NO_ACCEPT rather than an internal error. */
        return NGX_DECLINED;
    }
    if (rc != NGX_OK) {
        return NGX_ERROR;
    }

    if (count == 1) {
        /* Single field-line fast path: alias the stored value directly. */
        out->data = single->value.data;
        out->len = single->value.len;
        return NGX_OK;
    }

    /* Re-walk the list to combine the field-lines into the pool buffer. */
    data = ngx_pnalloc(r->pool, total_len);
    if (data == NULL) {
        return NGX_ERROR;
    }

    written = 0;
    count = 0;
    for (part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return NGX_ERROR;
        }

        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (!ngx_http_markdown_is_accept_header(&headers[i])) {
                continue;
            }

            if (count != 0) {
                data[written++] = ',';
                data[written++] = ' ';
            }
            if (headers[i].value.len != 0) {
                ngx_memcpy(data + written, headers[i].value.data,
                           headers[i].value.len);
                written += headers[i].value.len;
            }
            count++;
        }
    }

    if (written != total_len) {
        return NGX_ERROR;
    }

    out->data = data;
    out->len = total_len;
    return NGX_OK;
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
    ngx_table_elt_t  *header;
    ngx_uint_t        count;

    if (r == NULL) {
        return NULL;
    }

    if (ngx_http_markdown_count_accept_headers(r, &count) != NGX_OK) {
        return NULL;
    }

#if (NGX_HTTP_HEADERS)
    if (r->headers_in.accept != NULL) {
        return (count <= 1) ? r->headers_in.accept : NULL;
    }
#endif

    header = ngx_http_markdown_find_request_header(
        r,
        (u_char *) ngx_http_markdown_hdr_accept,
        sizeof(ngx_http_markdown_hdr_accept) - 1);

    return (count == 1) ? header : NULL;
}

/*
 * Determine if request should be converted to Markdown.
 *
 * Delegates to markdown_negotiate_accept FFI for RFC 9110 §12.5.1
 * q-value comparison with specificity tie-break rules.
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
    ngx_int_t                accept_rc;
    ngx_str_t                accept_value;
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

    if (r == NULL) {
        if (out_reason != NULL) {
            *out_reason = NEGOTIATE_REASON_NO_ACCEPT;
        }
        return 0;
    }

    accept_header = ngx_http_markdown_get_accept_header(r);
    if (accept_header != NULL) {
        accept_value.data = accept_header->value.data;
        accept_value.len = accept_header->value.len;
    } else {
        accept_rc = ngx_http_markdown_collect_accept_header(
            r, &accept_value);
        if (accept_rc == NGX_DECLINED) {
            if (out_reason != NULL) {
                *out_reason = NEGOTIATE_REASON_NO_ACCEPT;
            }
            return 0;
        }
        if (accept_rc != NGX_OK) {
            if (out_reason != NULL) {
                *out_reason = NEGOTIATE_REASON_INTERNAL_ERROR;
            }
            return 0;
        }
    }

    if (accept_value.len == 0) {
        if (out_reason != NULL) {
            *out_reason = NEGOTIATE_REASON_NO_ACCEPT;
        }
        return 0;
    }

    on_wildcard = (uint8_t)
        ((conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD) ? 1 : 0);

    markdown_negotiate_accept(
        accept_value.data,
        accept_value.len,
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


/* Return whether the selected response representation is determined by
 * Accept negotiation.  Internal negotiation failures are deliberately
 * excluded: that path is a generic fail-open/fail-closed outcome and must
 * not advertise a cache variance that was never successfully negotiated. */
ngx_flag_t
ngx_http_markdown_accept_result_varies(ngx_uint_t reason)
{
    switch (reason) {
    case NEGOTIATE_REASON_NO_ACCEPT:
    case NEGOTIATE_REASON_LOWER_Q:
    case NEGOTIATE_REASON_EXPLICIT_REJECT:
    case NEGOTIATE_REASON_MALFORMED:
        return 1;

    case NEGOTIATE_REASON_CONVERT:
    case NEGOTIATE_REASON_INTERNAL_ERROR:
    default:
        return 0;
    }
}
