/*
 * NGINX Markdown Filter Module - Response Eligibility Validation
 *
 * This file implements response eligibility validation to determine
 * if an upstream response should be converted to Markdown.
 *
 * Requirements: FR-02.1, FR-02.2, FR-02.3, FR-02.6, FR-02.7, FR-02.8, FR-10.1
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"
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
 * Marshal an NGINX array of ngx_str_t into a borrowed FFIStr array.
 *
 * The decision logic (method/status/content-type/size/streaming) lives in
 * the Rust core (markdown_decide_eligibility); the C side only collects the
 * request/config inputs.  Configured content-type/stream-type allowlists are
 * stored as ngx_array_t of ngx_str_t whose memory layout differs from FFIStr
 * (field order and the borrowed-slice contract), so they are copied into a
 * pool-allocated FFIStr array that borrows the original byte data for the
 * duration of the FFI call.  No NGINX object is exposed to Rust.
 *
 * Parameters:
 *   pool - request pool used for the borrowed FFIStr array
 *   arr  - source array of ngx_str_t (caller guarantees nelts > 0)
 *
 * Returns:
 *   Pointer to a pool-allocated FFIStr array, or NULL on allocation failure.
 */
static struct FFIStr *
ngx_http_markdown_marshal_str_array(ngx_pool_t *pool, const ngx_array_t *arr)
{
    struct FFIStr   *list;
    const ngx_str_t *src;

    list = ngx_palloc(pool, arr->nelts * sizeof(struct FFIStr));
    if (list == NULL) {
        return NULL;
    }

    src = arr->elts;

    for (ngx_uint_t i = 0; i < arr->nelts; i++) {
        list[i].data = src[i].data;
        list[i].len = src[i].len;
    }

    return list;
}

/*
 * Check response eligibility for Markdown conversion (thin FFI wrapper)
 *
 * The eligibility decision itself (method, status, Range, unbounded
 * streaming, Content-Type allowlist, size limit, and their ordering) is a
 * single source of truth in the Rust core, reached through the
 * markdown_decide_eligibility FFI.  This C function is glue only: it
 * collects request and configuration fields into an FFIEligibilityInput,
 * calls the FFI, and casts the returned u8 back to the C enum.  The u8
 * codes match the ngx_http_markdown_eligibility_t discriminants exactly,
 * so the cast is direct.
 *
 * NGINX-lifecycle concerns stay in C:
 *   - reading request/response fields (method, status, Range header,
 *     Content-Type, Content-Length);
 *   - resolving the effective full-buffer body limit via the shared
 *     resolver (ngx_http_markdown_effective_body_buffer_limit) so that
 *     eligibility and buffering apply the same precedence — this is a
 *     config-precedence resolution over conf/effective-conf fields, not a
 *     decision branch, and the actual size comparison is performed in Rust;
 *   - marshalling the configured allowlists into borrowed FFIStr arrays.
 *
 * Range Requests: 206 Partial Content (with or without a Range header) and
 * a Range request header both map to INELIGIBLE_RANGE.  This routing now
 * lives in the Rust decision; the C side only reports whether a Range
 * header was present and the response status.
 *
 * Fail-open: a NULL request or configuration, or a marshalling allocation
 * failure, yields INELIGIBLE_CONFIG (skip conversion, deliver the upstream
 * response unmodified) — the safe outcome.  The Rust side independently
 * treats a NULL input as INELIGIBLE_CONFIG (Rule 46).
 *
 * Parameters:
 *   r              - NGINX request structure
 *   conf           - Module configuration
 *   filter_enabled - Caller-resolved markdown_filter decision for this request
 *   eff            - Effective per-request configuration snapshot (may be NULL)
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
    FFIEligibilityInput   input;
    const struct FFIStr  *content_types;
    const struct FFIStr  *stream_types;
    uint8_t               code;

    /*
     * Guard NULL here because marshalling dereferences r and conf.  A
     * disabled filter is reported through filter_enabled and decided by the
     * Rust core, but a missing request/config is the safe skip outcome.
     */
    if (r == NULL || conf == NULL) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG;
    }

    content_types = NULL;
    stream_types = NULL;

    if (conf->content_types != NULL && conf->content_types->nelts > 0) {
        content_types = ngx_http_markdown_marshal_str_array(
            r->pool, conf->content_types);
        if (content_types == NULL) {
            return NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG;
        }
    }

    if (conf->stream_types != NULL && conf->stream_types->nelts > 0) {
        stream_types = ngx_http_markdown_marshal_str_array(
            r->pool, conf->stream_types);
        if (stream_types == NULL) {
            return NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG;
        }
    }

    input.filter_enabled = filter_enabled ? 1 : 0;
    input.method_get_or_head =
        (r->method == NGX_HTTP_GET || r->method == NGX_HTTP_HEAD) ? 1 : 0;
    input.has_range_header = (r->headers_in.range != NULL) ? 1 : 0;
    input.status = (uint16_t) r->headers_out.status;
    input.content_type = r->headers_out.content_type.data;
    input.content_type_len = r->headers_out.content_type.len;
    input.content_types = content_types;
    input.content_types_count =
        (content_types != NULL) ? conf->content_types->nelts : 0;
    input.stream_types = stream_types;
    input.stream_types_count =
        (stream_types != NULL) ? conf->stream_types->nelts : 0;
    input.content_length = (int64_t) r->headers_out.content_length_n;
    input.body_limit =
        ngx_http_markdown_effective_body_buffer_limit(eff, conf);

    code = markdown_decide_eligibility(&input);

    return (ngx_http_markdown_eligibility_t) code;
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
