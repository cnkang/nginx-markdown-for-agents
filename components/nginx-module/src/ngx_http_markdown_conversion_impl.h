#ifndef NGX_HTTP_MARKDOWN_CONVERSION_IMPL_H
#define NGX_HTTP_MARKDOWN_CONVERSION_IMPL_H

/*
 * Conversion-path helpers.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * These helpers prepare conversion inputs, execute the Rust FFI call, shape
 * the Markdown response, and construct base_url when available.
 */

#include <limits.h>

/*
 * Forward declarations for helpers referenced before their definitions
 * are visible through implementation-header include order.
 */
ngx_int_t ngx_strncasecmp(u_char *s1, u_char *s2, size_t n);
void markdown_result_free(struct MarkdownResult *result);
static void ngx_http_markdown_record_system_failure(
    ngx_http_markdown_ctx_t *ctx);
static void ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_conf_t *conf);
static ngx_int_t ngx_http_markdown_reject_or_fail_open_buffered_response(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, const char *debug_message);

/*
 * Local case-insensitive compare for const-qualified byte slices.
 * Mirrors ngx_strncasecmp semantics without dropping qualifiers.
 */
static ngx_int_t
ngx_http_markdown_const_strncasecmp(const u_char *s1, const u_char *s2,
                                    size_t n)
{
    for (size_t i = 0; i < n; i++) {
        u_char c1;
        u_char c2;

        c1 = ngx_tolower(s1[i]);
        c2 = ngx_tolower(s2[i]);
        if (c1 != c2) {
            return (ngx_int_t) c1 - (ngx_int_t) c2;
        }
    }

    return 0;
}

/*
 * Construct base URL for resolving relative URLs
 *
 * This function constructs the base URL using the following priority order:
 * 1. X-Forwarded-Proto + X-Forwarded-Host (reverse proxy scenario)
 * 2. r->schema + r->headers_in.server (direct connection)
 * 3. server_name from configuration (fallback)
 *
 * The base_url is used by the Rust conversion engine to resolve relative URLs
 * in HTML to absolute URLs in the Markdown output.
 *
 * Format: scheme://host/uri
 * Example: https://example.com/docs/page.html
 *
 * @param r     The request structure
 * @param pool  Memory pool for allocation
 * @param base_url  Output parameter for constructed base URL
 * @return      NGX_OK on success, NGX_ERROR on failure
 *
 * Requirements: Design - URL Resolution, NGINX Integration
 * Task: 14.8 Implement base_url construction with X-Forwarded headers priority
 */
static u_char ngx_http_markdown_hdr_x_forwarded_proto[] = "X-Forwarded-Proto";
static u_char ngx_http_markdown_hdr_x_forwarded_host[] = "X-Forwarded-Host";
static u_char ngx_http_markdown_scheme_http[] = "http";
static u_char ngx_http_markdown_scheme_https[] = "https";

/* Find a request header value by name in the generic linked-list storage. */
static const ngx_str_t *
ngx_http_markdown_find_request_header_value(ngx_http_request_t *r,
                                            const u_char *name,
                                            size_t name_len)
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
            if (headers[i].key.len == name_len
                && ngx_http_markdown_const_strncasecmp(headers[i].key.data,
                                                       name,
                                                       name_len) == 0)
            {
                return &headers[i].value;
            }
        }
    }

    return NULL;
}

/* Return true if the scheme is "http" or "https" (case-insensitive). */
static ngx_flag_t
ngx_http_markdown_scheme_is_http_family(const ngx_str_t *scheme)
{
    return (scheme->len == sizeof(ngx_http_markdown_scheme_http) - 1
            && ngx_strncasecmp(scheme->data,
                               ngx_http_markdown_scheme_http,
                               sizeof(ngx_http_markdown_scheme_http) - 1) == 0)
        || (scheme->len == sizeof(ngx_http_markdown_scheme_https) - 1
            && ngx_strncasecmp(scheme->data,
                               ngx_http_markdown_scheme_https,
                               sizeof(ngx_http_markdown_scheme_https) - 1) == 0);
}

/*
 * Select scheme and host for base URL construction.
 *
 * Priority: X-Forwarded-Proto/Host > request schema/server > server_name.
 */
static ngx_int_t
ngx_http_markdown_select_base_url_parts(ngx_http_request_t *r,
                                        ngx_str_t *scheme,
                                        ngx_str_t *host)
{
    const ngx_str_t                 *x_forwarded_proto;
    const ngx_str_t                 *x_forwarded_host;
    const ngx_http_core_srv_conf_t *cscf;
    const ngx_http_markdown_conf_t *conf;

    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);

    /*
     * Security: Only trust X-Forwarded-* headers when explicitly enabled.
     * Without this guard, a direct client can inject X-Forwarded-Host to
     * redirect all relative URLs in the Markdown output to an attacker-
     * controlled domain (C-01: link poisoning).
     */
    if (conf != NULL && conf->ops.trust_forwarded_headers) {
        x_forwarded_proto = ngx_http_markdown_find_request_header_value(
            r,
            ngx_http_markdown_hdr_x_forwarded_proto,
            sizeof(ngx_http_markdown_hdr_x_forwarded_proto) - 1);
        x_forwarded_host = ngx_http_markdown_find_request_header_value(
            r,
            ngx_http_markdown_hdr_x_forwarded_host,
            sizeof(ngx_http_markdown_hdr_x_forwarded_host) - 1);

        if (x_forwarded_proto != NULL
            && x_forwarded_host != NULL
            && x_forwarded_proto->len > 0
            && x_forwarded_host->len > 0
            && ngx_http_markdown_scheme_is_http_family(x_forwarded_proto))
        {
            *scheme = *x_forwarded_proto;
            *host = *x_forwarded_host;
            return NGX_OK;
        }
    }

    if (r->schema.len > 0 && r->headers_in.server.len > 0) {
        *scheme = r->schema;
        *host = r->headers_in.server;
        return NGX_OK;
    }

    cscf = ngx_http_get_module_srv_conf(r, ngx_http_core_module);
    if (cscf != NULL && cscf->server_name.len > 0) {
        if (r->schema.len > 0) {
            *scheme = r->schema;
        } else {
            scheme->data = ngx_http_markdown_scheme_http;
            scheme->len = sizeof(ngx_http_markdown_scheme_http) - 1;
        }
        *host = cscf->server_name;
        return NGX_OK;
    }

    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown filter: unable to construct base_url, "
                 "no valid scheme/host available");
    return NGX_ERROR;
}

/* Overflow-safe addition helper for base URL length accumulation. */
static ngx_int_t
ngx_http_markdown_base_url_add_len(ngx_http_request_t *r,
                                   size_t *total,
                                   size_t delta,
                                   const char *segment)
{
    if (*total > ((size_t) -1) - delta) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: base_url length overflow (%s)",
                     segment);
        return NGX_ERROR;
    }
    *total += delta;
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_construct_base_url(ngx_http_request_t *r, ngx_pool_t *pool,
    ngx_str_t *base_url)
{
    ngx_str_t  scheme;
    ngx_str_t  host;
    u_char    *p;
    size_t     len;

    /* Initialize output */
    base_url->data = NULL;
    base_url->len = 0;

    if (ngx_http_markdown_select_base_url_parts(r, &scheme, &host) != NGX_OK) {
        return NGX_ERROR;
    }

    len = 0;
    if (ngx_http_markdown_base_url_add_len(r, &len, scheme.len, "scheme") != NGX_OK) {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_base_url_add_len(r, &len, sizeof("://") - 1, "delimiter") != NGX_OK) {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_base_url_add_len(r, &len, host.len, "host") != NGX_OK) {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_base_url_add_len(r, &len, r->uri.len, "uri") != NGX_OK) {
        return NGX_ERROR;
    }
    
    p = ngx_pnalloc(pool, len);
    if (p == NULL) {
        /*
         * Memory allocation failed for base_url - critical system error.
         * 
         * Log level: NGX_LOG_CRIT (critical system failure)
         * Requirements: FR-09.5, FR-09.6, FR-09.7
         */
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to allocate memory for base_url, category=system");
        return NGX_ERROR;
    }

    base_url->data = p;
    
    /* Copy scheme */
    p = ngx_cpymem(p, scheme.data, scheme.len);
    
    /* Add :// */
    *p++ = ':';
    *p++ = '/';
    *p++ = '/';
    
    /* Copy host */
    p = ngx_cpymem(p, host.data, host.len);
    
    /* Copy URI */
    p = ngx_cpymem(p, r->uri.data, r->uri.len);
    
    base_url->len = p - base_url->data;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: constructed base_url: \"%V\"", base_url);

    return NGX_OK;
}

/* Record elapsed conversion time into exactly one discrete latency bucket band. */
static void
ngx_http_markdown_record_conversion_latency(ngx_msec_t elapsed_ms)
{
    NGX_HTTP_MARKDOWN_METRIC_ADD(conversion_time_sum_ms, elapsed_ms);

    if (elapsed_ms <= 10) {
        NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency_le_10ms);
        return;
    }

    if (elapsed_ms <= 100) {
        NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency_le_100ms);
        return;
    }

    if (elapsed_ms <= 1000) {
        NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency_le_1000ms);
        return;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency_gt_1000ms);
}

/*
 * Attempt conditional-request shortcut (If-None-Match / 304).
 *
 * On match returns NGX_HTTP_NOT_MODIFIED; on mismatch populates `result`
 * for reuse.
 */
static ngx_int_t
ngx_http_markdown_resolve_conditional_result(ngx_http_request_t *r,
                                             ngx_http_markdown_ctx_t *ctx,
                                             ngx_http_markdown_conf_t *conf,
                                             struct MarkdownResult *result,
                                             ngx_msec_t *elapsed_ms,
                                             ngx_flag_t *has_result)
{
    struct MarkdownResult *conditional_result;
    ngx_int_t              rc;

    conditional_result = NULL;
    *has_result = 0;

    rc = ngx_http_markdown_handle_if_none_match(
        r, conf, ctx, ngx_http_markdown_converter, &conditional_result);

    if (rc == NGX_HTTP_NOT_MODIFIED) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: If-None-Match matched, sending 304 Not Modified");

        if (ctx != NULL && ctx->has_last_modified_time) {
            r->headers_out.last_modified_time = ctx->source_last_modified_time;
        }

        rc = ngx_http_markdown_send_304(r, conditional_result);
        if (conditional_result != NULL) {
            markdown_result_free(conditional_result);
        }

        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        if (rc != NGX_OK) {
            ngx_http_markdown_record_system_failure(ctx);
            return rc;
        }

        return NGX_HTTP_NOT_MODIFIED;
    }

    if (rc == NGX_ERROR) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: error during If-None-Match processing");

        if (conditional_result != NULL) {
            markdown_result_free(conditional_result);
        }

        ngx_http_markdown_record_system_failure(ctx);

        return ngx_http_markdown_reject_or_fail_open_buffered_response(
            r, ctx, conf,
            "markdown filter: fail-open strategy - returning original HTML");
    }

    if (rc == NGX_DECLINED && conditional_result != NULL) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: If-None-Match did not match, using existing conversion");

        /*
         * Transfer ownership of the Rust-allocated buffers from
         * `conditional_result` to `result` with a shallow `ngx_memcpy()`.
         * `conditional_result` itself was allocated from `r->pool`, so only the
         * wrapper struct is released with `ngx_pfree()`. Callers must later use
         * `markdown_result_free(result)` to release the copied `markdown`,
         * `etag`, and `error_message` buffers.
         */
        ngx_memcpy(result, conditional_result, sizeof(struct MarkdownResult));
        ngx_pfree(r->pool, conditional_result);

        *elapsed_ms = 0;
        *has_result = 1;
    }

    return NGX_OK;
}

/**
 * Fill a MarkdownOptions structure from the location configuration and the HTTP request.
 *
 * Populates option fields (flavor, timeout_ms, generate_etag,
 * estimate_tokens, front_matter) from the provided location config, copies the
 * response Content-Type if present, and attempts to construct and attach a
 * base_url using the request; if base_url construction fails the function
 * continues without it. The options structure is zeroed before population.
 *
 * @param r The current ngx HTTP request used to read response headers and URI.
 * @param conf The location configuration providing default option values.
 * @param options Pointer to a MarkdownOptions struct that will be initialized and populated.
 * @returns NGX_OK on completion (options populated, possibly without a base_url).
 */
ngx_int_t
ngx_http_markdown_prepare_conversion_options(ngx_http_request_t *r,
                                             const ngx_http_markdown_conf_t *conf,
                                             struct MarkdownOptions *options)
{
    ngx_str_t base_url;

    ngx_memzero(options, sizeof(struct MarkdownOptions));
    if (conf->flavor > UINT32_MAX) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: flavor=%ui exceeds uint32 max, clamping",
                     conf->flavor);
        options->flavor = UINT32_MAX;
    } else {
        options->flavor = (uint32_t) conf->flavor;
    }

    if (conf->timeout > UINT32_MAX) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: timeout=%M exceeds uint32 max, clamping",
                     conf->timeout);
        options->timeout_ms = UINT32_MAX;
    } else {
        options->timeout_ms = (uint32_t) conf->timeout;
    }

    options->generate_etag = conf->generate_etag ? 1U : 0U;
    options->estimate_tokens = conf->token_estimate ? 1U : 0U;
    options->front_matter = conf->front_matter ? 1U : 0U;
    options->content_type = NULL;
    options->content_type_len = 0;
    options->base_url = NULL;
    options->base_url_len = 0;
#ifdef MARKDOWN_STREAMING_ENABLED
    options->streaming_budget = conf->streaming_budget;
#else
    options->streaming_budget = 0;
#endif

    if (r->headers_out.content_type.len > 0) {
        options->content_type = r->headers_out.content_type.data;
        options->content_type_len = r->headers_out.content_type.len;
    }

    if (ngx_http_markdown_construct_base_url(r, r->pool, &base_url) == NGX_OK) {
        options->base_url = base_url.data;
        options->base_url_len = base_url.len;
    } else {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: continuing conversion without base_url");
    }

    return NGX_OK;
}

/* Classify, log, and count a conversion failure, then apply error strategy. */
static ngx_int_t
ngx_http_markdown_handle_conversion_failure(ngx_http_request_t *r,
                                            ngx_http_markdown_ctx_t *ctx,
                                            ngx_http_markdown_conf_t *conf,
                                            struct MarkdownResult *result,
                                            ngx_msec_t elapsed_ms)
{
    ngx_http_markdown_error_category_t error_category;
    const ngx_str_t                  *category_str;
    int                               err_len;

    error_category = ngx_http_markdown_classify_error(result->error_code);
    category_str = ngx_http_markdown_error_category_string(error_category);

    /* Store error category in context for decision log emission */
    ctx->last_error_category = error_category;
    ctx->has_error_category = 1;

    ngx_http_markdown_record_conversion_latency(elapsed_ms);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);

    switch (error_category) {
        case NGX_HTTP_MARKDOWN_ERROR_CONVERSION:
            NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);
            break;
        case NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT:
            NGX_HTTP_MARKDOWN_METRIC_INC(failures_resource_limit);
            break;
        case NGX_HTTP_MARKDOWN_ERROR_SYSTEM:
            NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);
            break;
    }

    err_len = 0;
    if (result->error_message != NULL) {
        err_len = (result->error_len > (size_t) INT_MAX)
            ? INT_MAX
            : (int) result->error_len;
    }

    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown filter: conversion failed, "
                 "error_code=%ud, category=%V, message=\"%*s\", elapsed_ms=%M",
                 result->error_code,
                 category_str,
                 err_len,
                 (result->error_message != NULL) ? result->error_message : ngx_http_markdown_empty_string,
                 elapsed_ms);

    markdown_result_free(result);

    ngx_http_markdown_metric_inc_failopen(conf);

    return ngx_http_markdown_reject_or_fail_open_buffered_response(
        r, ctx, conf,
        "markdown filter: fail-open strategy - returning original HTML");
}

/*
 * Record a system-level failure in context and metrics.
 *
 * Centralizes the repeated pattern of setting error category,
 * incrementing conversions_failed and failures_system.
 *
 * Parameters:
 *   ctx - per-request module context
 */
static void
ngx_http_markdown_record_system_failure(
    ngx_http_markdown_ctx_t *ctx)
{
    ctx->last_error_category =
        NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    ctx->has_error_category = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);
}

/* Validate FFI result pointer/length invariants before consuming output. */
static ngx_int_t
ngx_http_markdown_validate_conversion_result(ngx_http_request_t *r,
                                             ngx_http_markdown_ctx_t *ctx,
                                             ngx_http_markdown_conf_t *conf,
                                             struct MarkdownResult *result)
{
    if ((result->markdown == NULL && result->markdown_len > 0)
        || (result->error_message == NULL && result->error_len > 0)
        || (result->etag == NULL && result->etag_len > 0))
    {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: invalid FFI result "
                     "invariants: "
                     "markdown=%p markdown_len=%uz "
                     "etag=%p etag_len=%uz "
                     "error_message=%p error_len=%uz",
                     result->markdown, result->markdown_len,
                     result->etag, result->etag_len,
                     result->error_message, result->error_len);
        ngx_http_markdown_record_system_failure(ctx);
        markdown_result_free(result);
        return ngx_http_markdown_reject_or_fail_open_buffered_response(
            r, ctx, conf, NULL);
    }

    return NGX_OK;
}

/* Update metrics counters after a successful conversion. */
static void
ngx_http_markdown_record_conversion_success(ngx_http_markdown_ctx_t *ctx,
                                            const struct MarkdownResult *result,
                                            ngx_msec_t elapsed_ms)
{
    ctx->conversion_succeeded = 1;
    ngx_http_markdown_record_conversion_latency(elapsed_ms);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_succeeded);
    NGX_HTTP_MARKDOWN_METRIC_ADD(input_bytes, ctx->buffer.size);
    NGX_HTTP_MARKDOWN_METRIC_ADD(output_bytes, result->markdown_len);
}

/*
 * Record a system-level conversion failure when the converter
 * handle is not initialized.
 *
 * Parameters:
 *   r    - NGINX request structure
 *   ctx  - per-request module context
 *   conf - module location configuration
 *
 * Returns:
 *   Result of reject_or_fail_open_buffered_response
 */
static ngx_int_t
ngx_http_markdown_handle_converter_not_initialized(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf)
{
    ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                 "markdown filter: converter not "
                 "initialized, category=system");
    ngx_http_markdown_record_system_failure(ctx);

    ngx_http_markdown_metric_inc_failopen(conf);

    return ngx_http_markdown_reject_or_fail_open_buffered_response(
        r, ctx, conf,
        "markdown filter: fail-open strategy "
        "- returning original HTML");
}


#ifdef MARKDOWN_STREAMING_ENABLED
static ngx_flag_t
ngx_http_markdown_shadow_output_diff(struct MarkdownResult *fb_result,
                                     const uint8_t *feed_data,
                                     uintptr_t feed_len,
                                     struct MarkdownResult *st_result)
{
    size_t   total_len;
    u_char  *fb_ptr;

    total_len = (size_t) feed_len + (size_t) st_result->markdown_len;
    if (total_len != fb_result->markdown_len) {
        return 1;
    }

    if (total_len == 0) {
        return 0;
    }

    fb_ptr = fb_result->markdown;
    if (feed_data != NULL && feed_len > 0) {
        if (ngx_memcmp(feed_data, fb_ptr, feed_len) != 0) {
            return 1;
        }
        fb_ptr += feed_len;
    }

    if (st_result->markdown != NULL
        && st_result->markdown_len > 0
        && ngx_memcmp(st_result->markdown,
                      fb_ptr,
                      st_result->markdown_len) != 0)
    {
        return 1;
    }

    return 0;
}

/*
 * Shadow mode: run the streaming engine on the same input that
 * was just converted by the full-buffer engine, compare outputs,
 * and record metrics/logs.  Any streaming error is isolated and
 * does not affect the client response.
 *
 * Parameters:
 *   r      - NGINX request structure
 *   ctx    - per-request module context (contains decompressed HTML)
 *   conf   - module location configuration
 *   fb_result     - full-buffer conversion result for comparison
 *   fb_elapsed_ms - full-buffer conversion elapsed time in milliseconds
 */
static void
ngx_http_markdown_shadow_compare(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    struct MarkdownResult *fb_result,
    ngx_msec_t fb_elapsed_ms)
{
    struct StreamingConverterHandle  *handle;
    struct MarkdownOptions            options;
    struct MarkdownResult             st_result;
    uint8_t                          *out_data;
    uintptr_t                         out_len;
    uint32_t                          init_rc;
    uint32_t                          rc;
    ngx_time_t                       *tp;
    ngx_msec_t                        shadow_start;
    ngx_msec_t                        shadow_elapsed;

    ngx_http_markdown_prepare_conversion_options(r, conf, &options);

    /*
     * Record shadow attempt unconditionally at entry so
     * shadow_total reflects attempts, not only successful
     * comparisons.  This keeps the shadow_diff_rate formula
     * (shadow_diff_total / shadow_total) well-defined even
     * when the streaming engine fails to initialize.
     */
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.shadow_total);
    ngx_http_markdown_log_decision(r, conf,
        ngx_http_markdown_reason_streaming_shadow());

    tp = ngx_timeofday();
    shadow_start = (ngx_msec_t) (tp->sec * 1000 + tp->msec);

    init_rc = markdown_streaming_new_with_code(
        &options, &handle);
    if (init_rc != ERROR_SUCCESS || handle == NULL) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown shadow: streaming engine "
            "init failed rc=%ui",
            (ngx_uint_t) init_rc);
        return;
    }

    out_data = NULL;
    out_len = 0;

    rc = markdown_streaming_feed(handle,
        ctx->buffer.data, ctx->buffer.size,
        &out_data, &out_len);

    if (rc != ERROR_SUCCESS) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown shadow: streaming feed "
            "error %ui", (ngx_uint_t) rc);
        markdown_streaming_abort(handle);
        if (out_data != NULL) {
            markdown_streaming_output_free(
                out_data, out_len);
        }
        return;
    }

    /*
     * Retain feed output for combined comparison.
     * Do NOT free out_data here — it is concatenated with
     * finalize output below to form the complete streaming
     * result for shadow comparison.
     */

    ngx_memzero(&st_result, sizeof(struct MarkdownResult));
    rc = markdown_streaming_finalize(handle, &st_result);

    tp = ngx_timeofday();
    shadow_elapsed = (ngx_msec_t) (tp->sec * 1000 + tp->msec);
    if (shadow_elapsed >= shadow_start) {
        shadow_elapsed = shadow_elapsed - shadow_start;
    } else {
        shadow_elapsed = 0;
    }

    ngx_log_debug2(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown shadow: "
        "shadow_streaming_latency_ms=%M "
        "shadow_fullbuffer_latency_ms=%M",
        shadow_elapsed, fb_elapsed_ms);

    if (rc != ERROR_SUCCESS) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown shadow: streaming finalize "
            "error %ui", (ngx_uint_t) rc);
        if (out_data != NULL) {
            markdown_streaming_output_free(
                out_data, out_len);
        }
        markdown_result_free(&st_result);
        return;
    }

    /*
     * Log and record peak memory estimate from the streaming
     * engine before freeing the result (lifecycle ordering:
     * read FFI struct fields before calling the free function).
     *
     * Requirement 2.8: record streaming engine peak memory
     * estimate in shadow mode when available.
     *
     * Requirement 3.7: update the last_peak_memory_bytes gauge
     * unconditionally so the gauge reflects the most recent
     * streaming conversion, whether from the primary streaming
     * path or shadow mode.  Skipping the write when the value
     * is zero would leave a stale value from a previous request
     * (Rule 23: gauge metrics should be updated unconditionally
     * on every successful sample).
     */
    if (ngx_http_markdown_metrics != NULL) {
        ngx_http_markdown_metrics->streaming.last_peak_memory_bytes =
            (ngx_atomic_t) st_result.peak_memory_estimate;
    }

    if (st_result.peak_memory_estimate > 0) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown shadow: peak_memory_bytes=%uz",
            (size_t) st_result.peak_memory_estimate);
    }

    /*
     * Compare streaming output (feed + finalize) against
     * full-buffer result.  Compare in-place without building
     * a combined buffer to avoid allocation failure skewing
     * the shadow_total counter.
     *
     * The streaming output is: out_data[0..out_len) followed
     * by st_result.markdown[0..st_result.markdown_len).
     * The full-buffer output is: fb_result->markdown[0..fb_result->markdown_len).
     */
    {
        size_t total_len;

        total_len = (size_t) out_len + (size_t) st_result.markdown_len;
        if (ngx_http_markdown_shadow_output_diff(fb_result, out_data, out_len,
                                                 &st_result)) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                streaming.shadow_diff_total);
            ngx_log_debug2(NGX_LOG_DEBUG_HTTP,
                r->connection->log, 0,
                "markdown shadow: output diff "
                "detected, fullbuffer_len=%uz "
                "streaming_len=%uz",
                (size_t) fb_result->markdown_len,
                total_len);
        }
    }

    if (out_data != NULL) {
        markdown_streaming_output_free(
            out_data, out_len);
    }
    markdown_result_free(&st_result);
}
#endif /* MARKDOWN_STREAMING_ENABLED */

static void
ngx_http_markdown_record_token_savings_if_enabled(
    const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const struct MarkdownResult *result)
{
    ngx_atomic_uint_t  html_tokens;
    ngx_atomic_uint_t  savings;

    if (!(conf->token_estimate
          && result->token_estimate > 0
          && ctx->buffer.size > 0))
    {
        return;
    }

    html_tokens = ctx->buffer.size / 4;
    if (html_tokens <= result->token_estimate) {
        return;
    }

    savings = html_tokens - (ngx_atomic_uint_t) result->token_estimate;
    if (savings > 0) {
        NGX_HTTP_MARKDOWN_METRIC_ADD(estimated_token_savings, savings);
    }
}


/**
 * Execute the Markdown conversion via the Rust FFI and handle success or failure outcomes.
 *
 * Performs the configured conversion (incremental if enabled and selected), measures elapsed
 * time, validates the returned MarkdownResult, records success metrics on success, and routes
 * failures through the module's failure handling which may emit a response or apply the
 * fail-open strategy.
 *
 * @param r      Current ngx HTTP request (used for logging and response handling).
 * @param ctx    Module request context (input buffer and processing path selection).
 * @param conf   Module location configuration (conversion options and limits).
 * @param result Pointer to a MarkdownResult struct that will be populated by the FFI call;
 *               on error the struct's error_code and optional error message fields are set.
 * @param elapsed_ms Pointer to a millisecond value that will be set to the measured conversion
 *                   duration (>= 0).
 *
 * @returns NGX_OK on successful conversion and metrics recording.
 *          NGX_DONE or NGX_ERROR when the conversion produced an error or when response
 *          handling has already been performed by the failure path.
 */
static ngx_int_t
ngx_http_markdown_execute_conversion(ngx_http_request_t *r,
                                     ngx_http_markdown_ctx_t *ctx,
                                     ngx_http_markdown_conf_t *conf,
                                     struct MarkdownResult *result,
                                     ngx_msec_t *elapsed_ms)
{
    struct MarkdownOptions  options;
    const ngx_time_t       *tp;
    ngx_msec_t              start_time;
    ngx_msec_t              end_time;
    ngx_int_t               rc;

    if (ngx_http_markdown_converter == NULL) {
        return ngx_http_markdown_handle_converter_not_initialized(
            r, ctx, conf);
    }

    ngx_http_markdown_prepare_conversion_options(r, conf, &options);

    tp = ngx_timeofday();
    start_time = (ngx_msec_t) (tp->sec * 1000 + tp->msec);

    ngx_memzero(result, sizeof(struct MarkdownResult));

#ifdef MARKDOWN_INCREMENTAL_ENABLED
    if (ctx->processing_path == NGX_HTTP_MARKDOWN_PATH_INCREMENTAL) {
        struct IncrementalConverterHandle *inc_handle;
        uint32_t                          feed_rc;
        uint32_t                          fin_rc;

        inc_handle = markdown_incremental_new(&options);
        if (inc_handle == NULL) {
            tp = ngx_timeofday();
            end_time = (ngx_msec_t) (tp->sec * 1000 + tp->msec);
            *elapsed_ms = (end_time >= start_time) ? end_time - start_time : 0;

            /*
             * markdown_incremental_new() returns NULL on option-decoding
             * failures or internal panics.  The Rust FFI does not expose
             * a separate error channel from this function, so we cannot
             * retrieve the library's error message.  Use
             * ERROR_INVALID_INPUT as the most likely cause (bad options);
             * the Rust layer logs the real reason to stderr.
             */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: markdown_incremental_new() "
                         "returned NULL (option decoding or internal error)");

            result->error_code = ERROR_INVALID_INPUT;
            result->error_message = NULL;
            result->error_len = 0;
            return ngx_http_markdown_handle_conversion_failure(
                r, ctx, conf, result, *elapsed_ms);
        }

        feed_rc = markdown_incremental_feed(
            inc_handle, ctx->buffer.data, ctx->buffer.size);
        if (feed_rc != ERROR_SUCCESS) {
            markdown_incremental_free(inc_handle);

            tp = ngx_timeofday();
            end_time = (ngx_msec_t) (tp->sec * 1000 + tp->msec);
            *elapsed_ms = (end_time >= start_time) ? end_time - start_time : 0;

            result->error_code = feed_rc;
            result->error_message = NULL;
            result->error_len = 0;
            return ngx_http_markdown_handle_conversion_failure(
                r, ctx, conf, result, *elapsed_ms);
        }

        /* finalize consumes the handle — do NOT call free after this */
        fin_rc = markdown_incremental_finalize(inc_handle, result);
        (void) fin_rc;  /* error_code is checked via result->error_code below */
    } else
#endif
    {
        markdown_convert(ngx_http_markdown_converter,
                        ctx->buffer.data,
                        ctx->buffer.size,
                        &options,
                        result);
    }

    tp = ngx_timeofday();
    end_time = (ngx_msec_t) (tp->sec * 1000 + tp->msec);
    if (end_time >= start_time) {
        *elapsed_ms = end_time - start_time;
    } else {
        *elapsed_ms = 0;
    }

    if (result->error_code != ERROR_SUCCESS) {
        return ngx_http_markdown_handle_conversion_failure(
            r, ctx, conf, result, *elapsed_ms);
    }

    rc = ngx_http_markdown_validate_conversion_result(r, ctx, conf, result);
    if (rc != NGX_OK) {
        return rc;
    }

    ngx_http_markdown_record_conversion_success(ctx, result, *elapsed_ms);

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * Shadow mode: after full-buffer conversion succeeds,
     * run the streaming engine on the same input and compare
     * outputs.  Any streaming error is isolated — it does not
     * affect the client response.
     *
     * Only run for the full-buffer path — incremental
     * conversions use a different pipeline and should not
     * be compared against the streaming engine.
     */
    if (conf->streaming_shadow
        && ctx->processing_path != NGX_HTTP_MARKDOWN_PATH_INCREMENTAL)
    {
        ngx_http_markdown_shadow_compare(
            r, ctx, conf, result, *elapsed_ms);
    }
#endif

    ngx_http_markdown_record_token_savings_if_enabled(ctx, conf, result);

    return NGX_OK;
}

/* Prepare an empty output buffer for HEAD requests (body omitted). */
static void
ngx_http_markdown_prepare_head_output_buffer(const ngx_http_request_t *r,
                                             ngx_buf_t *b,
                                             struct MarkdownResult *result)
{
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: HEAD request - omitting response body");

    b->pos = NULL;
    b->last = NULL;
    b->memory = 0;
    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;
    markdown_result_free(result);
}

/* Copy converted Markdown into a pool-allocated output buffer. */
static ngx_int_t
ngx_http_markdown_prepare_body_output_buffer(ngx_http_request_t *r,
                                             ngx_buf_t *b,
                                             struct MarkdownResult *result)
{
    if (result->markdown_len > 0) {
        b->pos = ngx_pnalloc(r->pool, result->markdown_len);
        if (b->pos == NULL) {
            ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                         "markdown filter: failed to allocate output memory, category=system");
            markdown_result_free(result);
            return NGX_ERROR;
        }

        ngx_memcpy(b->pos, result->markdown, result->markdown_len);
        b->last = b->pos + result->markdown_len;
        b->memory = 1;
    } else {
        b->pos = NULL;
        b->last = NULL;
        b->memory = 0;
    }

    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;
    markdown_result_free(result);
    return NGX_OK;
}

/* Update response headers and emit the converted Markdown downstream. */
static ngx_int_t
ngx_http_markdown_send_conversion_output(ngx_http_request_t *r,
                                         ngx_http_markdown_ctx_t *ctx,
                                         const ngx_http_markdown_conf_t *conf,
                                         struct MarkdownResult *result,
                                         ngx_msec_t elapsed_ms)
{
    ngx_int_t   rc;
    ngx_chain_t *out;
    ngx_buf_t   *b;

    (void) elapsed_ms;

    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: conversion succeeded, "
                  "input: %uz bytes, output: %uz bytes, elapsed: %M ms",
                  ctx->buffer.size, result->markdown_len, elapsed_ms);

    rc = ngx_http_markdown_update_headers(r, result, conf);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to update response headers, category=system");
        markdown_result_free(result);
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_forward_headers(r, ctx);
    if (rc != NGX_OK) {
        markdown_result_free(result);
        return rc;
    }

    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to allocate output buffer, category=system");
        markdown_result_free(result);
        return NGX_ERROR;
    }

    if (r->method == NGX_HTTP_HEAD) {
        ngx_http_markdown_prepare_head_output_buffer(r, b, result);
    } else {
        rc = ngx_http_markdown_prepare_body_output_buffer(r, b, result);
        if (rc != NGX_OK) {
            return rc;
        }
    }

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to allocate output chain, category=system");
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = NULL;

    return ngx_http_next_body_filter(r, out);
}

#endif /* NGX_HTTP_MARKDOWN_CONVERSION_IMPL_H */
