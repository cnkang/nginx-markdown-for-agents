/*
 * NGINX Markdown Filter Module - Response Eligibility Validation
 *
 * This file implements response eligibility validation to determine
 * if an upstream response should be converted to Markdown.
 *
 * Requirements: FR-02.1, FR-02.2, FR-02.3, FR-02.6, FR-02.7, FR-02.8, FR-10.1
 */

#include "ngx_http_markdown_filter_module.h"

/*
 * Check if request method is eligible for conversion
 *
 * Only GET and HEAD methods are eligible per FR-02.1.
 *
 * Parameters:
 *   r - NGINX request structure
 *
 * Returns:
 *   1 if method is GET or HEAD
 *   0 otherwise
 */
static ngx_int_t
ngx_http_markdown_check_method(ngx_http_request_t *r)
{
    return (r->method == NGX_HTTP_GET || r->method == NGX_HTTP_HEAD);
}

/*
 * Check if response status is eligible for conversion
 *
 * Only 200 OK status is eligible per FR-02.2.
 * Other status codes (1xx, 206, 3xx, 4xx, 5xx) are not converted.
 *
 * Note: 206 Partial Content is explicitly ineligible per FR-07.1.
 * Converting partial HTML would produce invalid Markdown.
 *
 * Parameters:
 *   r - NGINX request structure
 *
 * Returns:
 *   1 if status is 200
 *   0 otherwise
 */
static ngx_int_t
ngx_http_markdown_check_status(ngx_http_request_t *r)
{
    return (r->headers_out.status == NGX_HTTP_OK);
}

/*
 * Check if request contains Range header
 *
 * Per FR-07.2, requests with Range headers should not be converted
 * because converting partial HTML content would produce invalid or
 * incomplete Markdown.
 *
 * Parameters:
 *   r - NGINX request structure
 *
 * Returns:
 *   1 if Range header is present
 *   0 if Range header is not present
 */
static ngx_int_t
ngx_http_markdown_has_range_header(ngx_http_request_t *r)
{
    ngx_table_elt_t *range_header;
    
    /* Look for Range header in request headers */
    range_header = r->headers_in.range;
    
    return (range_header != NULL);
}

/*
 * Check if Content-Type is eligible for conversion
 *
 * Only text/html (with optional charset) is eligible per FR-02.3.
 *
 * Parameters:
 *   r - NGINX request structure
 *
 * Returns:
 *   1 if Content-Type is text/html
 *   0 otherwise
 */
static ngx_int_t
ngx_http_markdown_check_content_type(ngx_http_request_t *r)
{
    ngx_str_t *content_type;
    
    /* Get Content-Type header */
    if (r->headers_out.content_type.len == 0) {
        return 0;
    }
    
    content_type = &r->headers_out.content_type;
    
    /* Check for text/html (case-insensitive) */
    /* Accept "text/html" or "text/html; charset=..." */
    if (content_type->len >= 9 &&
        ngx_strncasecmp(content_type->data, (u_char *) "text/html", 9) == 0)
    {
        /* Valid if exactly "text/html" or followed by semicolon/space */
        if (content_type->len == 9 ||
            content_type->data[9] == ';' ||
            content_type->data[9] == ' ')
        {
            return 1;
        }
    }
    
    return 0;
}

/*
 * Check if response size is within configured limit
 *
 * Enforces FR-10.1 resource protection by checking Content-Length.
 * If Content-Length is not present (e.g., chunked encoding), this check
 * passes and size will be enforced during buffering.
 *
 * Parameters:
 *   r    - NGINX request structure
 *   conf - Module configuration
 *
 * Returns:
 *   1 if size is within limit or Content-Length not present
 *   0 if size exceeds limit
 */
static ngx_int_t
ngx_http_markdown_check_size_limit(ngx_http_request_t *r,
                                   ngx_http_markdown_conf_t *conf)
{
    off_t content_length;
    
    /* Get Content-Length if present */
    content_length = r->headers_out.content_length_n;
    
    /* If Content-Length not present, pass check (will enforce during buffering) */
    if (content_length < 0) {
        return 1;
    }
    
    /* Check if size exceeds configured limit */
    if ((size_t)content_length > conf->max_size) {
        return 0;
    }
    
    return 1;
}

/*
 * Check if Content-Type indicates unbounded streaming
 *
 * Checks if the response is an unbounded streaming type that should not
 * be converted per FR-02.8. This includes Server-Sent Events and other
 * streaming content types configured in stream_types.
 *
 * Parameters:
 *   r    - NGINX request structure
 *   conf - Module configuration
 *
 * Returns:
 *   1 if response is unbounded streaming (ineligible)
 *   0 if response is not streaming (eligible)
 */
static ngx_int_t
ngx_http_markdown_is_streaming(ngx_http_request_t *r,
                                ngx_http_markdown_conf_t *conf)
{
    ngx_str_t *content_type;
    ngx_str_t *stream_type;
    ngx_uint_t i;
    
    /* Get Content-Type header */
    if (r->headers_out.content_type.len == 0) {
        return 0;
    }
    
    content_type = &r->headers_out.content_type;
    
    /* Check for text/event-stream (Server-Sent Events) */
    if (content_type->len >= 17 &&
        ngx_strncasecmp(content_type->data, (u_char *) "text/event-stream", 17) == 0)
    {
        /* Valid if exactly "text/event-stream" or followed by semicolon/space */
        if (content_type->len == 17 ||
            content_type->data[17] == ';' ||
            content_type->data[17] == ' ')
        {
            return 1;
        }
    }
    
    /* Check configured stream_types exclusion list */
    if (conf->stream_types != NULL) {
        stream_type = conf->stream_types->elts;
        
        for (i = 0; i < conf->stream_types->nelts; i++) {
            /* Check if Content-Type starts with configured stream type */
            if (content_type->len >= stream_type[i].len &&
                ngx_strncasecmp(content_type->data, stream_type[i].data,
                               stream_type[i].len) == 0)
            {
                return 1;
            }
        }
    }
    
    return 0;
}

/*
 * Check response eligibility for Markdown conversion
 *
 * This function performs all eligibility checks to determine if an upstream
 * response should be converted to Markdown. All conditions must be met:
 *
 * 1. Request method is GET or HEAD (FR-02.1)
 * 2. Response status is 200 (FR-02.2)
 * 3. No Range header in request (FR-07.2)
 * 4. Content-Type is text/html (FR-02.3)
 * 5. Response size within configured limit (FR-10.1)
 * 6. Not unbounded streaming (FR-02.8)
 * 7. Conversion enabled in configuration (FR-02.6)
 *
 * Note: Chunked Transfer-Encoding responses are ELIGIBLE per FR-02.7.
 * The module buffers all chunks before conversion. Only unbounded streaming
 * (e.g., Server-Sent Events) is ineligible.
 *
 * Range Requests: Per FR-07.1 and FR-07.2, range requests are not converted
 * because converting partial HTML content would produce invalid or incomplete
 * Markdown. This is detected by:
 * - 206 Partial Content status (caught by status check)
 * - Range header in request (explicit check)
 *
 * Parameters:
 *   r    - NGINX request structure
 *   conf - Module configuration
 *
 * Returns:
 *   Eligibility enum indicating result and reason
 */
ngx_http_markdown_eligibility_t
ngx_http_markdown_check_eligibility(ngx_http_request_t *r,
                                    ngx_http_markdown_conf_t *conf)
{
    /* Check if conversion is enabled in configuration (FR-02.6) */
    if (!conf->enabled) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG;
    }
    
    /* Check request method (FR-02.1) */
    if (!ngx_http_markdown_check_method(r)) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD;
    }
    
    /* Check response status (FR-02.2, FR-07.1) */
    /* This catches 206 Partial Content responses */
    if (!ngx_http_markdown_check_status(r)) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS;
    }
    
    /* Check for Range header in request (FR-07.2) */
    /* Range requests should not be converted even if response is 200 */
    /* because the client expects partial HTML, not Markdown */
    if (ngx_http_markdown_has_range_header(r)) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE;
    }
    
    /* Check for unbounded streaming BEFORE Content-Type check (FR-02.8) */
    /* This allows us to reject streaming content types early */
    if (ngx_http_markdown_is_streaming(r, conf)) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING;
    }
    
    /* Check Content-Type (FR-02.3) */
    if (!ngx_http_markdown_check_content_type(r)) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE;
    }
    
    /* Check response size limit (FR-10.1) */
    if (!ngx_http_markdown_check_size_limit(r, conf)) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE;
    }
    
    /* All checks passed - response is eligible */
    return NGX_HTTP_MARKDOWN_ELIGIBLE;
}

/*
 * Get human-readable string for eligibility result
 *
 * Useful for logging and debugging.
 *
 * Parameters:
 *   eligibility - Eligibility enum value
 *
 * Returns:
 *   Static string describing the eligibility result
 */
const char *
ngx_http_markdown_eligibility_string(ngx_http_markdown_eligibility_t eligibility)
{
    switch (eligibility) {
        case NGX_HTTP_MARKDOWN_ELIGIBLE:
            return "eligible";
        case NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD:
            return "ineligible: method not GET/HEAD";
        case NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS:
            return "ineligible: status not 200";
        case NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE:
            return "ineligible: content-type not text/html";
        case NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE:
            return "ineligible: size exceeds limit";
        case NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING:
            return "ineligible: unbounded streaming";
        case NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH:
            return "ineligible: auth policy denies";
        case NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE:
            return "ineligible: range request";
        case NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG:
            return "ineligible: disabled by config";
        default:
            return "unknown";
    }
}
