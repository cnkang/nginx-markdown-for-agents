/*
 * NGINX Markdown Filter Module - Response Eligibility Validation
 *
 * This file implements response eligibility validation to determine
 * if an upstream response should be converted to Markdown.
 *
 * Requirements: FR-02.1, FR-02.2, FR-02.3, FR-02.6, FR-02.7, FR-02.8, FR-10.1
 */

#include "ngx_http_markdown_filter_module.h"
#include <strings.h>

static ngx_str_t ngx_http_markdown_eligible_str = ngx_string("eligible");
static ngx_str_t ngx_http_markdown_ineligible_method_str =
    ngx_string("ineligible: method not GET/HEAD");
static ngx_str_t ngx_http_markdown_ineligible_status_str =
    ngx_string("ineligible: status not 200");
static ngx_str_t ngx_http_markdown_ineligible_content_type_str =
    ngx_string("ineligible: content-type not text/html");
static ngx_str_t ngx_http_markdown_ineligible_size_str =
    ngx_string("ineligible: size exceeds limit");
static ngx_str_t ngx_http_markdown_ineligible_streaming_str =
    ngx_string("ineligible: unbounded streaming");
static ngx_str_t ngx_http_markdown_ineligible_auth_str =
    ngx_string("ineligible: auth policy denies");
static ngx_str_t ngx_http_markdown_ineligible_range_str =
    ngx_string("ineligible: range request");
static ngx_str_t ngx_http_markdown_ineligible_config_str =
    ngx_string("ineligible: disabled by config");
static ngx_str_t ngx_http_markdown_eligibility_unknown_str = ngx_string("unknown");

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
ngx_http_markdown_check_method(const ngx_http_request_t *r)
{
    return (r->method == NGX_HTTP_GET || r->method == NGX_HTTP_HEAD);
}

/*
 * Check if response status is eligible for conversion
 *
 * Only 200 OK is eligible per FR-02.2.
 * Other status codes (1xx, 2xx except 200, 3xx, 4xx, 5xx) are not converted.
 *
 * 206 Partial Content is handled separately in check_eligibility() where
 * it returns INELIGIBLE_RANGE, ensuring the correct reason code regardless
 * of whether the client sent a Range header.
 *
 * Parameters:
 *   r - NGINX request structure
 *
 * Returns:
 *   1 if status is 200
 *   0 otherwise
 */
static ngx_int_t
ngx_http_markdown_check_status(const ngx_http_request_t *r)
{
    return (r->headers_out.status == NGX_HTTP_OK);
}

/*
 * Check whether the request carries a Range header.
 *
 * Range requests must be excluded from markdown conversion because the
 * partial response body would not be valid HTML.
 *
 * Parameters:
 *   r  - HTTP request (must not be NULL)
 *
 * Returns:
 *   NGX_OK if Range header is present, NGX_DECLINED otherwise.
 */
static ngx_int_t
ngx_http_markdown_has_range_header(const ngx_http_request_t *r)
{
    return r->headers_in.range != NULL;
}

/*
 * Check if response Content-Type matches the configured allowlist.
 *
 * If markdown_content_types is configured, matches against that list
 * using prefix + boundary-char semantics (type/subtype must be followed
 * by ';', space, or end-of-string).  If not configured, defaults to
 * text/html only (backward compatible).
 *
 * Parameters:
 *   r    - NGINX request structure
 *   conf - Module configuration
 *
 * Returns:
 *   1 if Content-Type matches the allowlist
 *   0 otherwise
 */
static ngx_int_t
ngx_http_markdown_check_content_type(const ngx_http_request_t *r,
                                     const ngx_http_markdown_conf_t *conf)
{
    static u_char  text_html[] = "text/html";
    const ngx_str_t     *content_type;
    const ngx_str_t     *ct_entry;

    if (r->headers_out.content_type.len == 0) {
        return 0;
    }

    content_type = &r->headers_out.content_type;

    if (conf->content_types != NULL) {
        ct_entry = conf->content_types->elts;

        for (ngx_uint_t i = 0; i < conf->content_types->nelts; i++) {
            if (content_type->len >= ct_entry[i].len
                && ngx_strncasecmp(content_type->data,
                                   ct_entry[i].data,
                                   ct_entry[i].len) == 0
                && (content_type->len == ct_entry[i].len
                    || content_type->data[ct_entry[i].len] == ';'
                    || content_type->data[ct_entry[i].len] == ' '
                    || content_type->data[ct_entry[i].len] == '\t'))
            {
                return 1;
            }
        }

        return 0;
    }

    if (content_type->len >= 9
        && ngx_strncasecmp(content_type->data,
                           text_html, 9) == 0
        && (content_type->len == 9
            || content_type->data[9] == ';'
            || content_type->data[9] == ' '
            || content_type->data[9] == '\t'))
    {
        return 1;
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
ngx_http_markdown_check_size_limit(const ngx_http_request_t *r,
                                   const ngx_http_markdown_conf_t *conf,
                                   const ngx_http_markdown_effective_conf_t *eff)
{
    off_t  content_length;
    size_t body_limit;

    content_length = r->headers_out.content_length_n;

    if (content_length < 0) {
        return 1;
    }

    /*
     * markdown_max_size 0 means unlimited, but a finite memory_budget still
     * bounds the full-buffer path.  Use the shared resolver so eligibility
     * and buffering apply the same precedence for both static and effective
     * request configuration.
     */
    body_limit = ngx_http_markdown_effective_body_buffer_limit(eff, conf);
    if (body_limit == 0) {
        return 1;
    }

    if ((size_t) content_length > body_limit) {
        return 0;
    }

    return 1;
}

/**
 * Detects unbounded streaming responses.
 *
 * Determines whether the response Content-Type indicates an unbounded streaming
 * type. Checks for the built-in text/event-stream type and configured streaming
 * type exclusions.
 *
 * @param r    The HTTP request structure.
 * @param conf Module configuration.
 *
 * @return 1 if the response is an unbounded streaming type, 0 otherwise.
 */
static ngx_int_t
ngx_http_markdown_is_streaming(const ngx_http_request_t *r,
                               const ngx_http_markdown_conf_t *conf)
{
    static u_char  text_event_stream[] = "text/event-stream";
    const ngx_str_t     *content_type;
    const ngx_str_t     *stream_type;
    
    /* Get Content-Type header */
    if (r->headers_out.content_type.len == 0) {
        return 0;
    }
    
    content_type = &r->headers_out.content_type;
    
    /* Check for text/event-stream (Server-Sent Events) */
    if (content_type->len >= 17
        && ngx_strncasecmp(content_type->data,
                           text_event_stream, 17) == 0
        && (content_type->len == 17
            || content_type->data[17] == ';'
            || content_type->data[17] == ' '
            || content_type->data[17] == '\t'))
    {
        return 1;
    }
    
    /* Check configured stream_types exclusion list */
    if (conf->stream_types != NULL) {
        stream_type = conf->stream_types->elts;
        
        for (ngx_uint_t i = 0; i < conf->stream_types->nelts; i++) {
            /* Prefix + boundary match (same semantics as content_types). */
            if (content_type->len >= stream_type[i].len &&
                ngx_strncasecmp(content_type->data, stream_type[i].data,
                               stream_type[i].len) == 0 &&
                (content_type->len == stream_type[i].len
                 || content_type->data[stream_type[i].len] == ';'
                 || content_type->data[stream_type[i].len] == ' '
                 || content_type->data[stream_type[i].len] == '\t'))
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
 * 7. Conversion enabled for this request (FR-02.6), as resolved by caller
 *
 * Note: Chunked Transfer-Encoding responses are ELIGIBLE per FR-02.7.
 * The module buffers all chunks before conversion. Only unbounded streaming
 * (e.g., Server-Sent Events) is ineligible.
 *
 * Range Requests: Per FR-07.1 and FR-07.2, range requests are not converted
 * because converting partial HTML content would produce invalid or incomplete
 * Markdown. This is detected by:
 * - 206 Partial Content status (explicit check returns INELIGIBLE_RANGE)
 * - Range header in request (explicit check returns INELIGIBLE_RANGE)
 *
 * Parameters:
 *   r    - NGINX request structure
 *   conf           - Module configuration
 *   filter_enabled - Caller-resolved markdown_filter decision for this request
 *
 * Returns:
 *   Eligibility enum indicating result and reason
 */
ngx_http_markdown_eligibility_t
ngx_http_markdown_check_eligibility(const ngx_http_request_t *r,
                                    const ngx_http_markdown_conf_t *conf,
                                    ngx_flag_t filter_enabled,
                                    const ngx_http_markdown_effective_conf_t *eff)
{
    /*
     * markdown_filter enablement is resolved once by header filter to avoid
     * repeated evaluation of dynamic expressions in the same request.
     */
    if (conf == NULL || !filter_enabled) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG;
    }
    
    /* Check request method (FR-02.1) */
    if (!ngx_http_markdown_check_method(r)) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD;
    }
    
    /* Check response status (FR-02.2) */
    if (!ngx_http_markdown_check_status(r)) {
        /*
         * 206 Partial Content is routed to INELIGIBLE_RANGE
         * rather than INELIGIBLE_STATUS so the reason code
         * accurately reflects why the response was skipped.
         * This covers bare 206 responses (no Range header)
         * as well as normal range responses.
         */
        if (r->headers_out.status == NGX_HTTP_PARTIAL_CONTENT) {
            return NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE;
        }
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
    
    if (!ngx_http_markdown_check_content_type(r, conf)) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE;
    }
    
    /* Check response size limit (FR-10.1) */
    if (!ngx_http_markdown_check_size_limit(r, conf, eff)) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE;
    }
    
    /* All checks passed - response is eligible */
    return NGX_HTTP_MARKDOWN_ELIGIBLE;
}

/**
 * Determines whether a content type is excluded from streaming conversion.
 *
 * Checks for built-in hard exclusions (text/event-stream,
 * application/x-ndjson, application/stream+json) and any
 * user-configured excluded types. Content-Type parameters (after `;`)
 * are ignored. Matching is case-insensitive and exact.
 *
 * A NULL or empty content_type is treated as not excluded (returns 0),
 * so callers without a Content-Type header will not be short-circuited.
 *
 * @param content_type Content-Type string to check; may be NULL or empty.
 * @param conf Module location configuration.
 * @returns 1 if the content type is excluded, 0 otherwise.
 */
ngx_int_t
ngx_http_markdown_stream_type_excluded(const ngx_str_t *content_type,
    const ngx_http_markdown_conf_t *conf)
{
    static u_char  text_event_stream[] = "text/event-stream";
    static u_char  application_x_ndjson[] = "application/x-ndjson";
    static u_char  application_stream_json[] = "application/stream+json";

    size_t               type_len;
    const ngx_str_t     *entry;
    const u_char        *p;

    /* NULL or empty content type is not excluded */
    if (content_type == NULL || content_type->len == 0
        || content_type->data == NULL)
    {
        return 0;
    }

    /*
     * Normalize: determine the effective type length by stripping
     * Content-Type parameters (everything after ';').
     */
    type_len = content_type->len;
    p = ngx_strlchr(content_type->data,
                    content_type->data + content_type->len, ';');
    if (p != NULL) {
        type_len = (size_t) (p - content_type->data);
    }

    /* Strip HTTP optional whitespace (SP / HTAB) from the type portion. */
    while (type_len > 0
           && (content_type->data[type_len - 1] == ' '
               || content_type->data[type_len - 1] == '\t'))
    {
        type_len--;
    }

    if (type_len == 0) {
        return 0;
    }

    /* Check built-in hard exclusions (cannot be removed by user config) */

    /* text/event-stream (17 chars) */
    if (type_len == 17
        && ngx_strncasecmp(content_type->data, text_event_stream, 17) == 0)
    {
        return 1;
    }

    /* application/x-ndjson (20 chars) */
    if (type_len == 20
        && ngx_strncasecmp(content_type->data,
                           application_x_ndjson, 20) == 0)
    {
        return 1;
    }

    /* application/stream+json (23 chars) */
    if (type_len == 23
        && ngx_strncasecmp(content_type->data,
                           application_stream_json, 23) == 0)
    {
        return 1;
    }

    /* Check user-configured exclusion array */
    if (conf != NULL && conf->stream.excluded_types != NULL) {
        entry = conf->stream.excluded_types->elts;

        for (ngx_uint_t i = 0; i < conf->stream.excluded_types->nelts; i++) {
            if (type_len == entry[i].len
                && ngx_strncasecmp(content_type->data,
                                   entry[i].data,
                                   entry[i].len) == 0)
            {
                return 1;
            }
        }
    }

    return 0;
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
const ngx_str_t *
ngx_http_markdown_eligibility_string(ngx_http_markdown_eligibility_t eligibility)
{
    switch (eligibility) {
        case NGX_HTTP_MARKDOWN_ELIGIBLE:
            return &ngx_http_markdown_eligible_str;
        case NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD:
            return &ngx_http_markdown_ineligible_method_str;
        case NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS:
            return &ngx_http_markdown_ineligible_status_str;
        case NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE:
            return &ngx_http_markdown_ineligible_content_type_str;
        case NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE:
            return &ngx_http_markdown_ineligible_size_str;
        case NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING:
            return &ngx_http_markdown_ineligible_streaming_str;
        case NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH:
            return &ngx_http_markdown_ineligible_auth_str;
        case NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE:
            return &ngx_http_markdown_ineligible_range_str;
        case NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG:
            return &ngx_http_markdown_ineligible_config_str;
        default:
            return &ngx_http_markdown_eligibility_unknown_str;
    }
}
