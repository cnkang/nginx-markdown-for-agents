/*
 * NGINX Markdown Filter Module - Conditional Request Handling
 *
 * This file implements conditional request support (If-None-Match, If-Modified-Since)
 * for Markdown variants. Conditional decision policy is delegated to the Rust
 * FFI (markdown_decide_conditional), while NGINX lifecycle operations
 * (triggering conversion to generate ETag, sending 304 responses)
 * remain on the C side.
 *
 * Requirements: FR-06.1, FR-06.2, FR-06.3, FR-06.6
 * Task: 18.1 Implement If-None-Match handling with configurable behavior
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"


#define NGX_HTTP_MARKDOWN_HTTP_DATE_LEN \
    (sizeof("Mon, 28 Sep 1970 06:00:00 GMT") - 1)


/*
 * Find a request header by name in nginx's generic linked-list container.
 *
 * This helper is used for conditional request processing so the parser can
 * inspect `If-None-Match` even when convenience header pointers are absent.
 *
 * Parameters:
 *   r        - the HTTP request whose incoming headers are searched
 *   name     - header name to search for (need not be NUL-terminated)
 *   name_len - length of the header name in bytes
 *
 * Returns:
 *   pointer to the matching header entry, or NULL if not found
 */
static ngx_table_elt_t *
ngx_http_markdown_find_request_header(ngx_http_request_t *r, u_char *name, size_t name_len)
{
    if (r->headers_in.headers.part.nelts == 0) {
        return NULL;
    }

    for (ngx_list_part_t *part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers;

        headers = part->elts;
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
    }

    return NULL;
}

static uint8_t
ngx_http_markdown_conditional_cache_validation(ngx_uint_t mode)
{
    switch (mode) {
    case NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED:
        return 0;
    case NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT:
        return 2;
    case NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE:
    default:
        return 1;
    }
}

static ngx_table_elt_t *
ngx_http_markdown_find_response_header(ngx_http_request_t *r, u_char *name,
    size_t name_len)
{
    if (r->headers_out.headers.part.nelts == 0) {
        return NULL;
    }

    for (ngx_list_part_t *part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers;

        headers = part->elts;
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
    }

    return NULL;
}

static ngx_int_t
ngx_http_markdown_strncasecmp_const(const u_char *s1, const u_char *s2,
    size_t n)
{
    while (n != 0) {
        u_char  c1;
        u_char  c2;

        c1 = ngx_tolower(*s1);
        c2 = ngx_tolower(*s2);

        if (c1 != c2) {
            return c1 - c2;
        }

        s1++;
        s2++;
        n--;
    }

    return 0;
}

static ngx_flag_t
ngx_http_markdown_header_has_cache_directive(const ngx_table_elt_t *header,
    const u_char *directive, size_t directive_len)
{
    const u_char  *p;
    const u_char  *end;

    p = header->value.data;
    end = p + header->value.len;

    while (p < end) {
        while (p < end && (*p == ' ' || *p == '\t' || *p == ',')) {
            p++;
        }

        if ((size_t)(end - p) >= directive_len
            && ngx_http_markdown_strncasecmp_const(
                   p, directive, directive_len) == 0)
        {
            const u_char *after = p + directive_len;

            if (after == end || *after == ',' || *after == ' '
                || *after == '\t')
            {
                return 1;
            }
        }

        while (p < end && *p != ',') {
            p++;
        }
    }

    return 0;
}

static ngx_int_t
ngx_http_markdown_convert_for_conditional(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    struct MarkdownConverterHandle *converter,
    const struct MarkdownOptions *options,
    struct MarkdownResult *conv_result)
{
    /* r is used only inside MARKDOWN_INCREMENTAL_ENABLED; suppress
     * the unused-parameter warning in non-incremental builds. */
    (void) r;

#ifdef MARKDOWN_INCREMENTAL_ENABLED
    if (ctx->processing_path == NGX_HTTP_MARKDOWN_PATH_INCREMENTAL) {
        struct IncrementalConverterHandle *inc_handle;
        uint32_t                          init_rc;
        uint32_t                          feed_rc;
        uint32_t                          fin_rc;

        inc_handle = NULL;
        init_rc = markdown_incremental_new_with_code(
            options, &inc_handle);
        if (init_rc != ERROR_SUCCESS || inc_handle == NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: incremental converter init "
                         "failed during If-None-Match check, "
                         "error_code=%ud", (ngx_uint_t) init_rc);
            return NGX_ERROR;
        }

        feed_rc = markdown_incremental_feed(
            inc_handle, ctx->buffer.data, ctx->buffer.size);
        if (feed_rc != 0) {
            markdown_incremental_free(inc_handle);
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown: incremental feed failed during "
                         "If-None-Match check, error_code=%ud", feed_rc);
            return NGX_ERROR;
        }

        fin_rc = markdown_incremental_finalize(inc_handle, conv_result);
        if (fin_rc != ERROR_SUCCESS) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown: incremental finalize failed during "
                         "If-None-Match check, error_code=%ud", fin_rc);
            /*
             * finalize consumes the handle regardless of success/failure,
             * so do NOT call markdown_incremental_free().  Clean up the
             * result struct (which may hold partial Rust-owned fields).
             */
            markdown_result_free(conv_result);
            return NGX_ERROR;
        }

        return NGX_OK;
    }
#endif

    markdown_convert(converter, ctx->buffer.data, ctx->buffer.size,
                     options, conv_result);
    return NGX_OK;
}


/*
 * Check if the response carries Cache-Control: no-transform.
 *
 * Scans all Cache-Control response headers for the "no-transform"
 * directive (RFC 9111 §5.2.2.6).  The check is case-insensitive
 * per RFC.
 *
 * Parameters:
 *   r - NGINX request (for response header access)
 *
 * Returns:
 *   1 if no-transform is present, 0 otherwise
 */
ngx_flag_t
ngx_http_markdown_has_no_transform(ngx_http_request_t *r)
{
    static u_char       cc_name[] = "Cache-Control";
    static u_char       directive[] = "no-transform";
    size_t              directive_len;

    directive_len = sizeof(directive) - 1;

    for (ngx_list_part_t *part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers;

        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (headers[i].key.len != sizeof(cc_name) - 1
                || ngx_strncasecmp(headers[i].key.data, cc_name,
                                   sizeof(cc_name) - 1) != 0)
            {
                continue;
            }

            if (ngx_http_markdown_header_has_cache_directive(
                    &headers[i], directive, directive_len))
            {
                return 1;
            }
        }
    }

    return 0;
}

/*
 * Gather conditional request headers and resolve the Last-Modified value.
 *
 * Reads If-None-Match, If-Modified-Since, Range from request headers, and
 * Last-Modified from response headers (falling back to
 * r->headers_out.last_modified_time formatted as RFC 1123).  Outputs are
 * written through the caller-provided pointers.
 */
static void
ngx_http_markdown_collect_conditional_headers(ngx_http_request_t *r,
    const ngx_table_elt_t **inm_header, const ngx_table_elt_t **ims_header,
    const ngx_table_elt_t **range_header,
    const u_char **lm_data, size_t *lm_len, u_char *lm_time_buf)
{
    {
        static u_char  if_none_match_name[] = "If-None-Match";
        *inm_header = ngx_http_markdown_find_request_header(
            r, if_none_match_name, sizeof(if_none_match_name) - 1);
    }

    {
        static u_char  if_modified_since_name[] = "If-Modified-Since";
        *ims_header = ngx_http_markdown_find_request_header(
            r, if_modified_since_name, sizeof(if_modified_since_name) - 1);
    }

    {
        static u_char  range_name[] = "Range";
        *range_header = ngx_http_markdown_find_request_header(
            r, range_name, sizeof(range_name) - 1);
    }

    {
        const ngx_table_elt_t  *lm_header;
        static u_char  last_modified_name[] = "Last-Modified";
        lm_header = ngx_http_markdown_find_response_header(
            r, last_modified_name, sizeof(last_modified_name) - 1);

        if (lm_header != NULL) {
            *lm_data = lm_header->value.data;
            *lm_len = lm_header->value.len;
        } else if (r->headers_out.last_modified_time != (time_t) -1) {
            /*
             * No Last-Modified list header, but the dedicated
             * r->headers_out.last_modified_time field is set.  NGINX
             * common paths (static files, upstream with last_modified)
             * populate this field without always adding a list header.
             * Format it as an RFC 1123 HTTP date string so the Rust
             * conditional decision can compare it against
             * If-Modified-Since.
             */
            (void) ngx_http_time(lm_time_buf,
                                 r->headers_out.last_modified_time);
            *lm_data = lm_time_buf;
            *lm_len = NGX_HTTP_MARKDOWN_HTTP_DATE_LEN;

            ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown: formatted last_modified_time=%T "
                          "as \"%*s\"",
                          r->headers_out.last_modified_time,
                          (ngx_int_t) *lm_len, *lm_data);
        } else {
            *lm_data = NULL;
            *lm_len = 0;
        }
    }
}

/*
 * Translate an early (non-entity-ETag) FFI conditional decision outcome
 * into the NGINX return code the caller should return.  Encapsulated as a
 * helper so the main function's cognitive complexity stays below threshold.
 */
static ngx_int_t
ngx_http_markdown_conditional_early_outcome(
    const struct FFIConditionalDecision *cond_decision)
{
    if (cond_decision->outcome == 0) {
        return NGX_HTTP_NOT_MODIFIED;
    }

    if (cond_decision->outcome == 2) {
        return NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT;
    }

    return NGX_DECLINED;
}

/*
 * Evaluate and handle a conditional request for a Markdown response.
 *
 * When full cache validation needs an entity ETag, this function performs a
 * conversion to generate the Markdown variant ETag, then delegates the final
 * decision to Rust FFI (markdown_decide_conditional). IMS-only and IMS fallback
 * decisions do not need conversion and are also delegated to the same FFI path.
 *
 * @param r        The request structure.
 * @param conf     Module configuration controlling conditional request behavior and ETag generation.
 * @param ctx      Request context containing the prepared input buffer and processing path.
 * @param converter Worker-scoped converter handle required for FFI conversion (must not be NULL when conversion is needed).
 * @param result   Output pointer; on successful conversion this will be set to a newly allocated MarkdownResult.
 * @returns        NGX_HTTP_NOT_MODIFIED (304) if the generated ETag matches the client's If-None-Match,
 *                 NGX_DECLINED if no match or processing is skipped,
 *                 NGX_ERROR on failure (parsing, allocation, conversion, or internal errors).
 */
ngx_int_t
ngx_http_markdown_handle_if_none_match(ngx_http_request_t *r,
                                       const ngx_http_markdown_conf_t *conf,
                                       const ngx_http_markdown_ctx_t *ctx,
                                       struct MarkdownConverterHandle *converter,
                                       struct MarkdownResult **result)
{
    struct MarkdownOptions    options;
    struct MarkdownResult    *conv_result;
    struct FFIConditionalInput  cond_input;
    struct FFIConditionalDecision cond_decision;
    const ngx_table_elt_t   *inm_header;
    const ngx_table_elt_t   *ims_header;
    const ngx_table_elt_t   *range_header;
    const u_char            *inm_data;
    size_t                   inm_len;
    const u_char            *ims_data;
    size_t                   ims_len;
    const u_char            *lm_data;
    size_t                   lm_len;
    ngx_flag_t               needs_entity_etag;
    u_char                   lm_time_buf[NGX_HTTP_MARKDOWN_HTTP_DATE_LEN + 1];

    if (conf->policy.conditional_requests == NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: conditional requests disabled, "
                      "skipping If-None-Match");
        return NGX_DECLINED;
    }

    ngx_http_markdown_collect_conditional_headers(
        r, &inm_header, &ims_header, &range_header,
        &lm_data, &lm_len, lm_time_buf);

    if (inm_header != NULL) {
        inm_data = inm_header->value.data;
        inm_len = inm_header->value.len;
    } else {
        inm_data = NULL;
        inm_len = 0;
    }

    if (ims_header != NULL) {
        ims_data = ims_header->value.data;
        ims_len = ims_header->value.len;
    } else {
        ims_data = NULL;
        ims_len = 0;
    }

    if (inm_header == NULL && ims_header == NULL) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: no conditional request headers");
        return NGX_DECLINED;
    }

    memset(&cond_input, 0, sizeof(cond_input));
    memset(&cond_decision, 0, sizeof(cond_decision));
    cond_input.cache_validation = ngx_http_markdown_conditional_cache_validation(
        conf->policy.conditional_requests);
    cond_input.has_range = (range_header != NULL) ? 1 : 0;
    cond_input.no_transform =
        ngx_http_markdown_has_no_transform(r) ? 1 : 0;
    cond_input.if_none_match = inm_data;
    cond_input.if_none_match_len = inm_len;
    cond_input.if_modified_since = ims_data;
    cond_input.if_modified_since_len = ims_len;
    cond_input.last_modified = lm_data;
    cond_input.last_modified_len = lm_len;

    needs_entity_etag =
        (conf->policy.conditional_requests
            == NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT
         && inm_header != NULL);

    if (!needs_entity_etag) {
        markdown_decide_conditional(&cond_input, &cond_decision);
        return ngx_http_markdown_conditional_early_outcome(&cond_decision);
    }

    if (!conf->policy.generate_etag) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: ETag generation disabled, "
                      "cannot perform If-None-Match comparison");
        return NGX_DECLINED;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: If-None-Match present, performing conversion "
                  "to generate ETag for comparison (performance cost)");

    if (!ctx->buffer_initialized || ctx->buffer.size == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: buffer not initialized for If-None-Match check");
        return NGX_ERROR;
    }

    if (converter == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: converter handle is NULL during If-None-Match check");
        return NGX_ERROR;
    }

    if (ngx_http_markdown_prepare_conversion_options(
            r, conf, ctx->effective_conf, &options)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    options.generate_etag = 1;

    conv_result = ngx_pcalloc(r->pool, sizeof(struct MarkdownResult));
    if (conv_result == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate conversion result");
        return NGX_ERROR;
    }

    if (ngx_http_markdown_convert_for_conditional(
            r, ctx, converter, &options, conv_result)
        != NGX_OK)
    {
        ngx_pfree(r->pool, conv_result);
        return NGX_ERROR;
    }

    if (conv_result->error_code != 0) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: conversion failed during conditional check: "
                     "error_code=%ud message=\"%*s\"",
                     conv_result->error_code,
                     (conv_result->error_message != NULL) ? (ngx_int_t) conv_result->error_len : 0,
                     (conv_result->error_message != NULL) ? conv_result->error_message : (u_char *) "");

        markdown_result_free(conv_result);

        return NGX_ERROR;
    }

    cond_input.entity_etag = conv_result->etag;
    cond_input.entity_etag_len = conv_result->etag_len;
    markdown_decide_conditional(&cond_input, &cond_decision);

    if (cond_decision.outcome == 0) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: conditional matched, returning 304 Not Modified");

        *result = conv_result;

        return NGX_HTTP_NOT_MODIFIED;
    }

    if (cond_decision.outcome == 2) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: conditional bypass after conversion "
                      "(Range or no-transform), delivering upstream "
                      "unmodified");

        /*
         * The conversion was performed to generate the ETag, but the
         * conditional decision says Bypass.  Free the conversion result
         * and signal bypass to the caller.
         */
        markdown_result_free(conv_result);

        return NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: conditional proceeded, returning 200 with content");

    *result = conv_result;

    return NGX_DECLINED;
}

/*
 * Send 304 Not Modified response
 *
 * Constructs and sends a 304 Not Modified response with appropriate headers.
 * The response includes:
 * - Status: 304 Not Modified
 * - ETag: The matching ETag
 * - Vary: Accept (for cache correctness)
 * - No body (per HTTP specification)
 *
 * Requirements: FR-06.1, FR-06.3
 *
 * @param r         The request structure
 * @param result    Conversion result (contains ETag)
 * @return          NGX_DONE on success (request finalized), NGX_ERROR on failure,
 *                  or rc from ngx_http_send_header on partial failure
 */
ngx_int_t
ngx_http_markdown_send_304(ngx_http_request_t *r,
                           const struct MarkdownResult *result)
{
    ngx_table_elt_t  *h;
    ngx_int_t         rc;

    r->headers_out.status = NGX_HTTP_NOT_MODIFIED;
    r->headers_out.status_line.len = 0;

    ngx_http_clear_content_length(r);
    r->headers_out.content_length_n = -1;

    if (result != NULL && result->etag != NULL && result->etag_len > 0) {
        rc = ngx_http_markdown_set_etag(r, result->etag, result->etag_len);
        if (rc != NGX_OK) {
            return NGX_ERROR;
        }

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: 304 response with ETag: \"%V\"",
                      &r->headers_out.etag->value);
    }

    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    ngx_str_set(&h->key, "Vary");
    ngx_str_set(&h->value, "Accept");

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: 304 response with Vary: Accept");

    rc = ngx_http_send_header(r);
    if (rc == NGX_ERROR || rc > NGX_OK) {
        return rc;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: 304 Not Modified response sent");

    ngx_http_finalize_request(r, NGX_HTTP_NOT_MODIFIED);
    return NGX_DONE;
}
