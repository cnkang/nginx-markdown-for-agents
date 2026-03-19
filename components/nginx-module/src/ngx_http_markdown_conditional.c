/*
 * NGINX Markdown Filter Module - Conditional Request Handling
 *
 * This file implements conditional request support (If-None-Match, If-Modified-Since)
 * for Markdown variants. It handles ETag comparison and 304 Not Modified responses.
 *
 * Requirements: FR-06.1, FR-06.2, FR-06.3, FR-06.6
 * Task: 18.1 Implement If-None-Match handling with configurable behavior
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

static u_char ngx_http_markdown_if_none_match_header[] = "If-None-Match";
static u_char ngx_http_markdown_etag_wildcard[] = "*";
static u_char ngx_http_markdown_empty_string[] = "";

/*
 * Find a request header by name in nginx's generic linked-list container.
 *
 * This helper is used for conditional request processing so the parser can
 * inspect `If-None-Match` even when convenience header pointers are absent.
 */
static ngx_table_elt_t *
ngx_http_markdown_find_request_header(ngx_http_request_t *r, u_char *name, size_t name_len)
{
    ngx_list_part_t  *part;

    if (r->headers_in.headers.part.nelts == 0) {
        return NULL;
    }

    for (part = &r->headers_in.headers.part; part != NULL; part = part->next) {
        ngx_table_elt_t  *headers;
        ngx_uint_t        i;

        headers = part->elts;
        for (i = 0; i < part->nelts; i++) {
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
ngx_http_markdown_push_etag_token(ngx_array_t *etags, u_char *data, size_t len)
{
    ngx_str_t  *etag;

    etag = ngx_array_push(etags);
    if (etag == NULL) {
        return NGX_ERROR;
    }

    etag->data = data;
    etag->len = len;
    return NGX_OK;
}

static void
ngx_http_markdown_skip_if_none_match_separators(u_char **cursor, const u_char *end)
{
    while (*cursor < end
           && (**cursor == ' ' || **cursor == '\t' || **cursor == ','))
    {
        (*cursor)++;
    }
}

static ngx_int_t
ngx_http_markdown_parse_quoted_etag(ngx_http_request_t *r,
                                    u_char **cursor,
                                    const u_char *end,
                                    ngx_array_t *etags)
{
    u_char  *start;
    size_t   len;

    (*cursor)++;
    start = *cursor;

    while (*cursor < end && **cursor != '"') {
        (*cursor)++;
    }

    if (*cursor >= end) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: malformed If-None-Match header, "
                     "missing closing quote");
        return NGX_DECLINED;
    }

    len = (size_t) (*cursor - start);
    (*cursor)++;

    return ngx_http_markdown_push_etag_token(etags, start, len);
}

static ngx_int_t
ngx_http_markdown_parse_unquoted_etag(u_char **cursor, const u_char *end,
                                      ngx_array_t *etags)
{
    u_char  *start;
    size_t   len;

    start = *cursor;
    while (*cursor < end
           && **cursor != ','
           && **cursor != ' '
           && **cursor != '\t')
    {
        (*cursor)++;
    }

    len = (size_t) (*cursor - start);
    return ngx_http_markdown_push_etag_token(etags, start, len);
}

/*
 * Parse If-None-Match header
 *
 * Extracts ETag values from the If-None-Match header. The header can contain
 * multiple ETags separated by commas, or the special value "*".
 *
 * Format examples:
 * - If-None-Match: "etag-value"
 * - If-None-Match: "etag1", "etag2", "etag3"
 * - If-None-Match: *
 *
 * @param r         The request structure
 * @param etags     Output array of ETag strings (ngx_str_t)
 * @return          NGX_OK if header parsed, NGX_DECLINED if not present, NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_parse_if_none_match(ngx_http_request_t *r, ngx_array_t **etags)
{
    ngx_table_elt_t  *if_none_match;
    ngx_int_t         rc;
    u_char           *p;
    const u_char     *end;

    if_none_match = ngx_http_markdown_find_request_header(
        r,
        ngx_http_markdown_if_none_match_header,
        sizeof(ngx_http_markdown_if_none_match_header) - 1);

    if (if_none_match == NULL) {
        return NGX_DECLINED;
    }

    *etags = ngx_array_create(r->pool, 4, sizeof(ngx_str_t));
    if (*etags == NULL) {
        return NGX_ERROR;
    }

    /* Parse ETags from header value */
    p = if_none_match->value.data;
    end = p + if_none_match->value.len;

    while (p < end) {
        ngx_http_markdown_skip_if_none_match_separators(&p, end);
        if (p >= end) {
            break;
        }

        if (*p == '*') {
            rc = ngx_http_markdown_push_etag_token(*etags,
                                                   ngx_http_markdown_etag_wildcard,
                                                   1);
            if (rc != NGX_OK) {
                return rc;
            }
            p++;
            continue;
        }

        if (*p == '"') {
            rc = ngx_http_markdown_parse_quoted_etag(r, &p, end, *etags);
        } else {
            rc = ngx_http_markdown_parse_unquoted_etag(&p, end, *etags);
        }

        if (rc != NGX_OK) {
            return rc;
        }
    }

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: parsed If-None-Match with %ui ETags",
                  (*etags)->nelts);

    return NGX_OK;
}

/*
 * Compare ETag with If-None-Match values
 *
 * Checks if the generated ETag matches any of the ETags in the If-None-Match
 * header. Returns NGX_OK if there's a match (should return 304), NGX_DECLINED
 * if no match (should return 200 with content).
 *
 * Special case: If If-None-Match contains "*", it matches any ETag.
 *
 * @param etag          The generated ETag for the Markdown variant
 * @param etag_len      Length of the ETag
 * @param if_none_match Array of ETags from If-None-Match header
 * @return              NGX_OK if match (return 304), NGX_DECLINED if no match
 */
static ngx_int_t
ngx_http_markdown_compare_etag(const u_char *etag, size_t etag_len,
                               ngx_array_t *if_none_match)
{
    ngx_str_t  *client_etag;
    ngx_uint_t  i;
    const u_char *generated_norm;
    size_t        generated_norm_len;
    const u_char *client_norm;
    size_t        client_norm_len;

    if (etag == NULL || etag_len == 0 || if_none_match == NULL) {
        return NGX_DECLINED;
    }

    client_etag = if_none_match->elts;
    generated_norm = etag;
    generated_norm_len = etag_len;

    /*
     * If-None-Match uses weak comparison for GET/HEAD.
     * Normalize both generated and client validators by stripping:
     * 1) Optional weakness prefix: W/
     * 2) Optional surrounding quotes
     */
    if (generated_norm_len >= 2
        && (generated_norm[0] == 'W' || generated_norm[0] == 'w')
        && generated_norm[1] == '/')
    {
        generated_norm += 2;
        generated_norm_len -= 2;
    }
    if (generated_norm_len >= 2
        && generated_norm[0] == '"'
        && generated_norm[generated_norm_len - 1] == '"')
    {
        generated_norm += 1;
        generated_norm_len -= 2;
    }

    for (i = 0; i < if_none_match->nelts; i++) {
        /* Check for wildcard match */
        if (client_etag[i].len == 1 && client_etag[i].data[0] == '*') {
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, NULL, 0,
                          "markdown filter: If-None-Match wildcard match");
            return NGX_OK;
        }

        client_norm = client_etag[i].data;
        client_norm_len = client_etag[i].len;
        if (client_norm_len >= 2
            && (client_norm[0] == 'W' || client_norm[0] == 'w')
            && client_norm[1] == '/')
        {
            client_norm += 2;
            client_norm_len -= 2;
        }
        if (client_norm_len >= 2
            && client_norm[0] == '"'
            && client_norm[client_norm_len - 1] == '"')
        {
            client_norm += 1;
            client_norm_len -= 2;
        }

        if (client_norm_len == generated_norm_len
            && ngx_memcmp(client_norm, generated_norm, generated_norm_len) == 0)
        {
            ngx_log_debug2(NGX_LOG_DEBUG_HTTP, NULL, 0,
                          "markdown filter: ETag match: \"%*s\"",
                          etag_len, etag);
            return NGX_OK;
        }
    }

    /* No match found */
    return NGX_DECLINED;
}

/**
 * Evaluate and handle an If-None-Match conditional request for a Markdown response.
 *
 * Parses the request's If-None-Match header (when conditional request support is enabled),
 * generates an ETag for the current Markdown variant, compares it against the client's
 * ETags, and decides whether the response is not modified. When a conversion is
 * performed, a MarkdownResult is allocated and stored in `*result` for later use
 * (headers/body handling).
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
                                       ngx_http_markdown_conf_t *conf,
                                       ngx_http_markdown_ctx_t *ctx,
                                       struct MarkdownConverterHandle *converter,
                                       struct MarkdownResult **result)
{
    ngx_array_t              *if_none_match_etags;
    ngx_int_t                 rc;
    struct MarkdownOptions    options;
    struct MarkdownResult    *conv_result;

    /* Check configuration mode */
    if (conf->conditional_requests == NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED) {
        /* Conditional requests disabled - skip processing */
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: conditional requests disabled, "
                      "skipping If-None-Match");
        return NGX_DECLINED;
    }

    if (conf->conditional_requests == NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE) {
        /* If-Modified-Since only mode - skip If-None-Match processing */
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: if_modified_since_only mode, "
                      "skipping If-None-Match");
        return NGX_DECLINED;
    }

    /* full_support mode - process If-None-Match */

    /* Parse If-None-Match header */
    rc = ngx_http_markdown_parse_if_none_match(r, &if_none_match_etags);
    if (rc == NGX_DECLINED) {
        /* No If-None-Match header present */
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: no If-None-Match header");
        return NGX_DECLINED;
    }
    if (rc != NGX_OK) {
        /* Parse error */
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: failed to parse If-None-Match header");
        return NGX_ERROR;
    }

    /*
     * PERFORMANCE NOTE: We must perform conversion to generate the ETag
     * for comparison. This has a performance cost:
     * - HTML parsing
     * - Markdown generation
     * - ETag hashing (BLAKE3)
     *
     * This is the trade-off for correct HTTP semantics. Administrators
     * can use if_modified_since_only mode to avoid this cost.
     */

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: If-None-Match present, performing conversion "
                  "to generate ETag for comparison (performance cost)");

    /* Check if ETag generation is enabled */
    if (!conf->generate_etag) {
        /* ETag generation disabled - cannot perform If-None-Match comparison */
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: ETag generation disabled, "
                      "cannot perform If-None-Match comparison");
        return NGX_DECLINED;
    }

    /* Perform conversion to generate ETag */
    /* Note: Buffer should already be filled by body filter */
    if (!ctx->buffer_initialized || ctx->buffer.size == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: buffer not initialized for If-None-Match check");
        return NGX_ERROR;
    }

    /*
     * FFI contract: markdown_convert() requires a valid converter handle.
     * The caller must pass the worker-scoped handle initialized during
     * worker startup; NULL is an invalid input and would return an FFI error.
     */
    if (converter == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: converter handle is NULL during If-None-Match check");
        return NGX_ERROR;
    }

    if (ngx_http_markdown_prepare_conversion_options(r, conf, &options)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    options.generate_etag = 1;

    /* Allocate result structure */
    conv_result = ngx_pcalloc(r->pool, sizeof(struct MarkdownResult));
    if (conv_result == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to allocate conversion result");
        return NGX_ERROR;
    }

    /* Perform conversion — honour the incremental path when enabled. */
#ifdef MARKDOWN_INCREMENTAL_ENABLED
    if (ctx->processing_path == NGX_HTTP_MARKDOWN_PATH_INCREMENTAL) {
        struct IncrementalConverterHandle *inc_handle;
        uint32_t                          feed_rc;
        uint32_t                          fin_rc;

        inc_handle = markdown_incremental_new(&options);
        if (inc_handle == NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: incremental converter init failed "
                         "during If-None-Match check");
            ngx_pfree(r->pool, conv_result);
            return NGX_ERROR;
        }

        feed_rc = markdown_incremental_feed(
            inc_handle, ctx->buffer.data, ctx->buffer.size);
        if (feed_rc != 0) {
            markdown_incremental_free(inc_handle);
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown filter: incremental feed failed during "
                         "If-None-Match check, error_code=%ud", feed_rc);
            ngx_pfree(r->pool, conv_result);
            return NGX_ERROR;
        }

        /* finalize consumes the handle — do NOT call free after this */
        fin_rc = markdown_incremental_finalize(inc_handle, conv_result);
        (void) fin_rc;  /* error_code is checked via conv_result below */
    } else
#endif
    {
        markdown_convert(converter, ctx->buffer.data, ctx->buffer.size,
                         &options, conv_result);
    }

    /* Check conversion result */
    if (conv_result->error_code != 0) {
        /* Conversion failed */
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: conversion failed during If-None-Match check: "
                     "error_code=%ud message=\"%*s\"",
                     conv_result->error_code,
                     (conv_result->error_message != NULL) ? (ngx_int_t) conv_result->error_len : 0,
                     (conv_result->error_message != NULL) ? conv_result->error_message : ngx_http_markdown_empty_string);
        
        /* Free result */
        markdown_result_free(conv_result);
        
        return NGX_ERROR;
    }

    /* Compare generated ETag with If-None-Match values */
    rc = ngx_http_markdown_compare_etag(conv_result->etag, conv_result->etag_len,
                                        if_none_match_etags);

    if (rc == NGX_OK) {
        /* ETag matches - return 304 Not Modified */
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: ETag match, returning 304 Not Modified");

        /* Store result for header updates (ETag, Vary) */
        *result = conv_result;

        return NGX_HTTP_NOT_MODIFIED;
    }

    /* No match - store result for normal response */
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: ETag mismatch, returning 200 with content");

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
ngx_http_markdown_send_304(ngx_http_request_t *r, struct MarkdownResult *result)
{
    ngx_table_elt_t  *h;
    ngx_int_t         rc;

    /* Set status to 304 Not Modified */
    r->headers_out.status = NGX_HTTP_NOT_MODIFIED;
    r->headers_out.status_line.len = 0;  /* NGINX will set default status line */

    /* Clear Content-Length (304 must not have a body) */
    ngx_http_clear_content_length(r);
    r->headers_out.content_length_n = -1;

    /* Set ETag header */
    if (result != NULL && result->etag != NULL && result->etag_len > 0) {
        h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }

        h->hash = 1;
        ngx_str_set(&h->key, "ETag");

        /* Allocate and copy ETag value */
        h->value.data = ngx_pnalloc(r->pool, result->etag_len);
        if (h->value.data == NULL) {
            return NGX_ERROR;
        }

        ngx_memcpy(h->value.data, result->etag, result->etag_len);
        h->value.len = result->etag_len;

        r->headers_out.etag = h;

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: 304 response with ETag: \"%V\"", &h->value);
    }

    /* Add Vary: Accept header */
    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    ngx_str_set(&h->key, "Vary");
    ngx_str_set(&h->value, "Accept");

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: 304 response with Vary: Accept");

    /* Send headers */
    rc = ngx_http_send_header(r);
    if (rc == NGX_ERROR || rc > NGX_OK) {
        return rc;
    }

    /* Finalize request (no body for 304) */
    ngx_http_finalize_request(r, NGX_HTTP_NOT_MODIFIED);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: 304 Not Modified response sent");

    return NGX_OK;
}
