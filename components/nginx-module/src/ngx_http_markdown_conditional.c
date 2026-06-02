/*
 * NGINX Markdown Filter Module - Conditional Request Handling
 *
 * This file implements conditional request support (If-None-Match, If-Modified-Since)
 * for Markdown variants. ETag comparison is delegated to the Rust FFI
 * (markdown_check_conditional), while NGINX lifecycle operations
 * (triggering conversion to generate ETag, sending 304 responses)
 * remain on the C side.
 *
 * Requirements: FR-06.1, FR-06.2, FR-06.3, FR-06.6
 * Task: 18.1 Implement If-None-Match handling with configurable behavior
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

static u_char ngx_http_markdown_empty_string[] = "";

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

/**
 * Evaluate and handle an If-None-Match conditional request for a Markdown response.
 *
 * When conditional request support is enabled, this function performs a
 * conversion to generate the Markdown variant ETag, then delegates ETag
 * comparison to the Rust FFI (markdown_check_conditional). If the result
 * is Not-Modified, the 304 response is sent; otherwise the conversion
 * result is stored for the normal response path.
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
    struct FFIConditionalResult cond_result;
    const ngx_table_elt_t   *inm_header;
    const ngx_table_elt_t   *ims_header;
    const u_char            *inm_data;
    size_t                   inm_len;
    const u_char            *ims_data;
    size_t                   ims_len;

    if (conf->policy.conditional_requests == NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: conditional requests disabled, "
                      "skipping If-None-Match");
        return NGX_DECLINED;
    }

    if (conf->policy.conditional_requests == NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: if_modified_since_only mode, "
                      "skipping If-None-Match");
        return NGX_DECLINED;
    }

    /* Check for If-None-Match header before performing conversion */
    inm_header = NULL;
    {
        static u_char  if_none_match_name[] = "If-None-Match";
        inm_header = ngx_http_markdown_find_request_header(
            r, if_none_match_name, sizeof(if_none_match_name) - 1);
    }

    if (inm_header == NULL) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: no If-None-Match header");
        return NGX_DECLINED;
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
                     "markdown: conversion failed during If-None-Match check: "
                     "error_code=%ud message=\"%*s\"",
                     conv_result->error_code,
                     (conv_result->error_message != NULL) ? (ngx_int_t) conv_result->error_len : 0,
                     (conv_result->error_message != NULL) ? conv_result->error_message : ngx_http_markdown_empty_string);

        markdown_result_free(conv_result);

        return NGX_ERROR;
    }

    /*
     * Delegate ETag comparison to Rust FFI.
     *
     * markdown_check_conditional handles weak ETag comparison (W/ prefix
     * stripping, quote normalization) per RFC 7232 §2.3.3 / §3.2.
     * We pass the generated ETag and the If-None-Match / If-Modified-Since
     * header values as raw byte slices.
     */
    inm_data = inm_header->value.data;
    inm_len = inm_header->value.len;

    {
        static u_char  if_modified_since_name[] = "If-Modified-Since";
        ims_header = ngx_http_markdown_find_request_header(
            r, if_modified_since_name, sizeof(if_modified_since_name) - 1);
    }

    if (ims_header != NULL) {
        ims_data = ims_header->value.data;
        ims_len = ims_header->value.len;
    } else {
        ims_data = NULL;
        ims_len = 0;
    }

    markdown_check_conditional(
        inm_data, inm_len,
        conv_result->etag, conv_result->etag_len,
        ims_data, ims_len,
        NULL, 0,
        &cond_result);

    if (cond_result.result_code == 0) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: ETag match, returning 304 Not Modified");

        *result = conv_result;

        return NGX_HTTP_NOT_MODIFIED;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: ETag mismatch, returning 200 with content");

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
 * @return          NGX_OK on success, NGX_ERROR on failure
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
        h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }

        h->hash = 1;
        ngx_str_set(&h->key, "ETag");

        h->value.data = ngx_pnalloc(r->pool, result->etag_len);
        if (h->value.data == NULL) {
            return NGX_ERROR;
        }

        ngx_memcpy(h->value.data, result->etag, result->etag_len);
        h->value.len = result->etag_len;

        r->headers_out.etag = h;

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: 304 response with ETag: \"%V\"", &h->value);
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
