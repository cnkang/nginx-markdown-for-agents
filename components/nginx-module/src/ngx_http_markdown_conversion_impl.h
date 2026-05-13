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
static ngx_http_markdown_otel_span_t *ngx_http_markdown_otel_span_start(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf);
static void ngx_http_markdown_otel_set_str_attr(
    ngx_http_markdown_otel_span_t *span, const u_char *key, size_t key_len,
    const u_char *value, size_t val_len);
static void ngx_http_markdown_otel_set_int_attr(
    ngx_http_markdown_otel_span_t *span, const u_char *key, size_t key_len,
    int64_t value);
static void ngx_http_markdown_otel_span_end(ngx_http_markdown_otel_span_t *span);
static void ngx_http_markdown_otel_span_export(
    ngx_http_markdown_otel_span_t *span, ngx_log_t *log,
    ngx_http_request_t *r);

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
 * 1. X-Forwarded-Proto + X-Forwarded-Host only when
 *    markdown_trust_forwarded_headers is enabled
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
 * Task: 14.8 Implement base_url construction with guarded X-Forwarded headers
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
        const ngx_table_elt_t  *headers;

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
 * Validate characters in an extracted host value.
 *
 * Rejects control characters (0x00-0x1F, 0x7F), spaces, commas, and
 * path separators (/,\).  For non-IPv6 hosts, enforces digit-only
 * port after the first ':'.  For IPv6 bracket literals, only rejects
 * the structural danger characters (port is validated separately
 * after bracket matching).
 *
 * @param host_data  Pointer to the host string (not NUL-terminated).
 * @param host_len   Length of the host string.
 * @param is_ipv6    Whether the host starts with '[' (IPv6 literal).
 * @returns 1 if valid, 0 if invalid.
 */
static ngx_flag_t
ngx_http_markdown_validate_host_chars(const u_char *host_data,
                                      size_t host_len,
                                      ngx_flag_t is_ipv6)
{
    ngx_flag_t  parsing_port = 0;

    for (size_t i = 0; i < host_len; i++) {
        u_char  c = host_data[i];

        if (c < 0x20 || c == 0x7F) {
            return 0;
        }

        if (parsing_port) {
            if (c < '0' || c > '9') {
                return 0;
            }
            continue;
        }

        if (c == '/' || c == '\\' || c == ' ' || c == ',') {
            return 0;
        }

        if (!is_ipv6 && c == ':') {
            if (i == host_len - 1) {
                return 0;
            }
            parsing_port = 1;
        }
    }

    return 1;
}

/*
 * Validate the structure of an IPv6 bracket literal host.
 *
 * Requires a closing ']' and allows an optional :<port> suffix
 * after it (e.g. [::1]:8080).  Returns 1 if valid, 0 if invalid.
 *
 * @param host_data  Pointer to the host string starting with '['.
 * @param host_len   Length of the host string.
 * @returns 1 if valid, 0 if invalid.
 */
static ngx_flag_t
ngx_http_markdown_validate_ipv6_brackets(const u_char *host_data,
                                         size_t host_len)
{
    size_t  bracket_end = 0;

    for (size_t i = 1; i < host_len; i++) {
        if (host_data[i] == ']') {
            bracket_end = i;
            break;
        }
    }

    if (bracket_end == 0) {
        return 0;
    }

    if (bracket_end + 1 < host_len) {
        if (host_data[bracket_end + 1] != ':') {
            return 0;
        }
        for (size_t i = bracket_end + 2; i < host_len; i++) {
            if (host_data[i] < '0' || host_data[i] > '9') {
                return 0;
            }
        }
    }

    return 1;
}

/*
 * Extract and validate the first-hop host from an X-Forwarded-Host value.
 *
 * The header may contain multiple comma-separated values from a chain
 * of proxies.  Only the first (closest trusted proxy) is used.
 * Leading and trailing whitespace is trimmed from the extracted value.
 *
 * On success, validated_host is populated and the function returns NGX_OK.
 * On failure, returns NGX_ERROR (caller should fall through to server name).
 *
 * @param xfh             The raw X-Forwarded-Host header value.
 * @param validated_host  Output: the extracted and validated host.
 * @returns NGX_OK on success, NGX_ERROR on invalid or empty host.
 */
static ngx_int_t
ngx_http_markdown_extract_forwarded_host(const ngx_str_t *xfh,
                                         ngx_str_t *validated_host)
{
    u_char    *host_data;
    size_t     host_len;
    ngx_flag_t is_ipv6;

    /* Find first comma to extract first-hop value. */
    host_len = xfh->len;
    for (size_t i = 0; i < xfh->len; i++) {
        if (xfh->data[i] == ',') {
            host_len = i;
            break;
        }
    }

    /* Trim leading whitespace from extracted host. */
    host_data = xfh->data;
    while (host_len > 0
           && (*host_data == ' ' || *host_data == '\t'))
    {
        host_data++;
        host_len--;
    }

    /* Trim trailing whitespace from extracted host. */
    while (host_len > 0
           && (host_data[host_len - 1] == ' '
               || host_data[host_len - 1] == '\t'))
    {
        host_len--;
    }

    if (host_len == 0) {
        return NGX_ERROR;
    }

    /* Check for IPv6 bracket literal. */
    is_ipv6 = (host_len >= 2 && host_data[0] == '[') ? 1 : 0;

    /* Validate characters (control chars, spaces, path separators, port). */
    if (!ngx_http_markdown_validate_host_chars(host_data, host_len,
                                               is_ipv6))
    {
        return NGX_ERROR;
    }

    /* IPv6 literal: validate bracket structure and optional port. */
    if (is_ipv6
        && !ngx_http_markdown_validate_ipv6_brackets(host_data, host_len))
    {
        return NGX_ERROR;
    }

    validated_host->data = host_data;
    validated_host->len = host_len;
    return NGX_OK;
}

/**
 * Selects the URL scheme and host to use when constructing a base URL for the request.
 *
 * Selection priority (highest to lowest): trusted X-Forwarded-Proto/Host (when enabled) >
 * request r->schema and r->headers_in.server > server_name from core server config.
 *
 * @param r The HTTP request to inspect.
 * @param scheme Out parameter set to the selected scheme string.
 * @param host Out parameter set to the selected host string (may include port or IPv6 brackets).
 * @returns `NGX_OK` if both `scheme` and `host` were successfully selected and written; `NGX_ERROR` otherwise.
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
            ngx_str_t  validated_host;

            *scheme = *x_forwarded_proto;

            if (ngx_http_markdown_extract_forwarded_host(
                    x_forwarded_host, &validated_host) == NGX_OK)
            {
                *host = validated_host;
                return NGX_OK;
            }

            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                          "markdown filter: X-Forwarded-Host "
                          "value \"%V\" rejected (invalid host), "
                          "falling back to server name",
                          x_forwarded_host);
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
    (void) segment;  /* Unused in this implementation */

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
        NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency.le_10ms);
        return;
    }

    if (elapsed_ms <= 100) {
        NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency.le_100ms);
        return;
    }

    if (elapsed_ms <= 1000) {
        NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency.le_1000ms);
        return;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency.gt_1000ms);
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
                                             const ngx_http_markdown_conf_t *conf,
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
 * Populates scalar option fields (flavor, timeout_ms, generate_etag,
 * estimate_tokens, front_matter, streaming_budget) from the provided location
 * config, borrows the response Content-Type if present, and attempts to
 * construct and attach a request-pool base_url. If base_url construction
 * fails, conversion continues without it. The options structure is zeroed
 * before population so every FFI field has an explicit value.
 *
 * Pointer lifetime:
 *   content_type points into NGINX response header storage and base_url points
 *   into request-pool memory. Both remain readable for the synchronous Rust FFI
 *   conversion call and are not owned by Rust.
 *
 * @param r The current ngx HTTP request used to read response headers and URI.
 * @param conf The location configuration providing default option values.
 * @param eff The effective configuration view for per-request consistency;
 *            mutable fields (prune_noise, memory_budget, streaming_budget)
 *            are read from here when non-NULL, falling back to conf otherwise.
 * @param options Pointer to a MarkdownOptions struct that will be initialized and populated.
 * @returns NGX_OK on completion (options populated, possibly without a base_url).
 */
ngx_int_t
ngx_http_markdown_prepare_conversion_options(ngx_http_request_t *r,
                                             const ngx_http_markdown_conf_t *conf,
                                             const ngx_http_markdown_effective_conf_t *eff,
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
    options->streaming_budget =
        ngx_http_markdown_effective_streaming_budget(eff, conf);
#else
    options->streaming_budget = 0;
#endif

    options->prune_noise =
        ngx_http_markdown_effective_prune_noise(eff, conf) ? 1U : 0U;
    options->prune_selectors = NULL;
    options->prune_selector_len = 0;
    options->prune_protection_selectors = NULL;
    options->prune_protection_selector_len = 0;

    if (conf->prune_selectors != NULL) {
        options->prune_selectors = conf->prune_selectors->data;
        options->prune_selector_len = conf->prune_selectors->len;
    }

    if (conf->prune_protection_selectors != NULL) {
        options->prune_protection_selectors =
            conf->prune_protection_selectors->data;
        options->prune_protection_selector_len =
            conf->prune_protection_selectors->len;
    }

    /*
     * Unified memory budget with priority:
     *   explicit per-engine > unified > default
     * For streaming_budget: if streaming_budget was explicitly set
     * (not NGX_CONF_UNSET_SIZE), use it; else if memory_budget is
     * set, use it; else streaming_budget keeps its merge default.
     * For max_size: same priority chain applies.
     */
    options->memory_budget =
        ngx_http_markdown_effective_memory_budget(eff, conf);
    if (options->memory_budget == NGX_CONF_UNSET_SIZE) {
        options->memory_budget = 0;
    }

    if (conf->llm_provider > UINT8_MAX) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown filter: llm_provider=%ui exceeds uint8 range",
                      conf->llm_provider);
        return NGX_ERROR;
    }
    if (conf->chars_per_token_fixed > UINT8_MAX) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown filter: chars_per_token_fixed=%ui exceeds "
                      "uint8 range",
                      conf->chars_per_token_fixed);
        return NGX_ERROR;
    }

    options->llm_provider = (uint8_t) conf->llm_provider;
    options->chars_per_token_fixed = (uint8_t) conf->chars_per_token_fixed;

    /*
     * Apply unified budget to streaming_budget when it was not
     * explicitly set by the operator.
     *
     * Priority: explicit streaming_budget > memory_budget > default
     *
     * streaming_budget_explicit is set during merge_conf when the
     * operator explicitly configured markdown_streaming_budget at
     * this or any parent configuration level.
     */
#ifdef MARKDOWN_STREAMING_ENABLED
    if (ngx_http_markdown_effective_memory_budget(eff, conf)
            != NGX_CONF_UNSET_SIZE
        && !conf->streaming_budget_explicit
        && ngx_http_markdown_effective_streaming_budget(eff, conf)
            == NGX_CONF_UNSET_SIZE)
    {
        options->streaming_budget =
            ngx_http_markdown_effective_memory_budget(eff, conf);
    }
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
                                            const ngx_http_markdown_conf_t *conf,
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
                                             const ngx_http_markdown_conf_t *conf,
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

static const ngx_str_t *
ngx_http_markdown_otel_flavor_name(ngx_uint_t flavor)
{
    static ngx_str_t gfm_name = ngx_string("gfm");
    static ngx_str_t mdx_name = ngx_string("mdx");
    static ngx_str_t org_mode_name = ngx_string("org-mode");
    static ngx_str_t commonmark_name = ngx_string("commonmark");

    switch (flavor) {
    case NGX_HTTP_MARKDOWN_FLAVOR_GFM:
        return &gfm_name;
    case NGX_HTTP_MARKDOWN_FLAVOR_MDX:
        return &mdx_name;
    case NGX_HTTP_MARKDOWN_FLAVOR_ORG_MODE:
        return &org_mode_name;
    case NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK:
    default:
        return &commonmark_name;
    }
}

static const ngx_str_t *
ngx_http_markdown_otel_engine_name(ngx_uint_t path)
{
    static ngx_str_t incremental_name = ngx_string("incremental");
    static ngx_str_t fullbuffer_name = ngx_string("fullbuffer");

    if (path == NGX_HTTP_MARKDOWN_PATH_INCREMENTAL) {
        return &incremental_name;
    }
    return &fullbuffer_name;
}

static void
ngx_http_markdown_otel_teardown_span(ngx_http_request_t *r,
                                     ngx_http_markdown_ctx_t *ctx,
                                     size_t input_bytes,
                                     size_t output_bytes,
                                     int64_t error_code)
{
    if (ctx->otel_span == NULL) {
        return;
    }

    ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
        (const u_char *) "input_bytes", 11, (int64_t) input_bytes);
    ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
        (const u_char *) "output_bytes", 12, (int64_t) output_bytes);
    ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
        (const u_char *) "error_code", 10, error_code);
    ngx_http_markdown_otel_span_end(ctx->otel_span);
    ngx_http_markdown_otel_span_export(ctx->otel_span, r->connection->log, r);
    ctx->otel_span = NULL;
}

/*
 * Determine which child to follow in the per-path RB tree
 * when the hash key matches but the URI path does not.
 *
 * Returns -1 to descend left, +1 to descend right.
 *
 * @param r    The request providing the URI to compare
 * @param node The tree node whose path is compared against
 */
static int
ngx_http_markdown_per_path_cmp(const ngx_http_request_t *r,
                               const ngx_http_markdown_path_metric_node_t *node)
{
    if (r->uri.len < node->path_len) {
        return -1;
    }

    if (r->uri.len > node->path_len) {
        return 1;
    }

    if (ngx_memcmp(r->uri.data, node->path, node->path_len) < 0) {
        return -1;
    }

    return 1;
}

/*
 * Search the per-path RB tree for an existing entry matching the
 * request URI.  On match, atomically increments per-path and
 * aggregate counters and returns NGX_OK.  On miss, returns
 * NGX_DECLINED so the caller can insert a new node.
 *
 * Must be called with shpool->mutex held.  The mutex is NOT
 * released by this function on either return path.
 *
 * @param r          The request (provides r->uri as the lookup key)
 * @param metrics    Shared metrics structure containing the RB tree
 * @param sentinel   The RB tree sentinel node (read-only)
 * @param key        Precomputed hash key for r->uri
 * @param elapsed_ms Conversion elapsed time to record on match
 */
static ngx_int_t
ngx_http_markdown_per_path_lookup_and_update(
    const ngx_http_request_t *r,
    ngx_http_markdown_metrics_t *metrics,
    const ngx_rbtree_node_t *sentinel,
    ngx_uint_t key,
    ngx_msec_t elapsed_ms)
{
    ngx_rbtree_node_t                    *rbnode;
    ngx_http_markdown_path_metric_node_t *node;
    ngx_rbtree_node_t                    *child;
    int                                   cmp;

    rbnode = metrics->per_path.path_tree.root;

    for ( ;; ) {
        if (key < rbnode->key) {
            if (rbnode->left == sentinel) {
                return NGX_DECLINED;
            }
            rbnode = rbnode->left;
            continue;
        }

        if (key > rbnode->key) {
            if (rbnode->right == sentinel) {
                return NGX_DECLINED;
            }
            rbnode = rbnode->right;
            continue;
        }

        node = (ngx_http_markdown_path_metric_node_t *) rbnode;

        if (r->uri.len == node->path_len
            && ngx_memcmp(r->uri.data, node->path,
                          node->path_len) == 0)
        {
            ngx_atomic_fetch_add(&node->conversions, 1);
            ngx_atomic_fetch_add(&node->entries, 1);
            ngx_atomic_fetch_add(&node->conversion_time_sum_ms,
                                 (ngx_atomic_uint_t) elapsed_ms);
            ngx_atomic_fetch_add(
                &metrics->per_path.path_conversions, 1);
            ngx_atomic_fetch_add(
                &metrics->per_path.path_conversion_time_sum_ms,
                (ngx_atomic_uint_t) elapsed_ms);
            return NGX_OK;
        }

        cmp = ngx_http_markdown_per_path_cmp(r, node);
        child = (cmp < 0) ? rbnode->left : rbnode->right;
        if (child == sentinel) {
            return NGX_DECLINED;
        }
        rbnode = child;
    }
}

/*
 * Record per-path metrics for a successful conversion.
 *
 * Looks up the request URI in the shared RB-tree.  If the path
 * is not found and the tree is below cardinality_limit, allocates
 * a new node from the slab pool and inserts it.  If at capacity,
 * increments overflow_count.  Updates per-path and aggregate
 * counters under the slab pool mutex.
 *
 * Parameters:
 *   r          - the HTTP request (provides r->uri as the path key)
 *   conf       - module location configuration
 *   elapsed_ms - conversion elapsed time in milliseconds
 */
static void
ngx_http_markdown_record_per_path_metrics(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    ngx_msec_t elapsed_ms)
{
    ngx_shm_zone_t                       *zone;
    ngx_slab_pool_t                      *shpool;
    ngx_http_markdown_metrics_t          *metrics;
    ngx_http_markdown_path_metric_node_t *node;
    const ngx_rbtree_node_t              *sentinel;
    ngx_uint_t                            hash;
    ngx_uint_t                            key;

    if (!conf->ops.metrics_per_path) {
        return;
    }

    zone = ngx_http_markdown_metrics_shm_zone;
    if (zone == NULL || zone->data == NULL) {
        return;
    }

    metrics = (ngx_http_markdown_metrics_t *) zone->data;
    shpool = (ngx_slab_pool_t *) zone->shm.addr;

    if (r->uri.len == 0 || r->uri.data == NULL) {
        return;
    }

    hash = ngx_hash_key(r->uri.data, r->uri.len);
    key = hash;

    ngx_shmtx_lock(&shpool->mutex);

    sentinel = &metrics->per_path.sentinel;

    if (metrics->per_path.path_tree.root != sentinel
        && ngx_http_markdown_per_path_lookup_and_update(
               r, metrics, sentinel, key, elapsed_ms) == NGX_OK)
    {
        ngx_shmtx_unlock(&shpool->mutex);
        return;
    }

    if ((ngx_uint_t) metrics->per_path.path_entries
        >= metrics->per_path.cardinality_limit)
    {
        ngx_atomic_fetch_add(&metrics->per_path.overflow_count, 1);
        ngx_atomic_fetch_add(&metrics->per_path.path_conversions, 1);
        ngx_atomic_fetch_add(
            &metrics->per_path.path_conversion_time_sum_ms,
            (ngx_atomic_uint_t) elapsed_ms);
        ngx_shmtx_unlock(&shpool->mutex);
        return;
    }

    node = ngx_slab_alloc_locked(shpool,
        sizeof(ngx_http_markdown_path_metric_node_t));
    if (node == NULL) {
        ngx_atomic_fetch_add(&metrics->per_path.path_conversions, 1);
        ngx_atomic_fetch_add(
            &metrics->per_path.path_conversion_time_sum_ms,
            (ngx_atomic_uint_t) elapsed_ms);
        ngx_shmtx_unlock(&shpool->mutex);
        return;
    }

    node->path = ngx_slab_alloc_locked(shpool, r->uri.len);
    if (node->path == NULL) {
        ngx_slab_free_locked(shpool, node);
        ngx_atomic_fetch_add(&metrics->per_path.path_conversions, 1);
        ngx_atomic_fetch_add(
            &metrics->per_path.path_conversion_time_sum_ms,
            (ngx_atomic_uint_t) elapsed_ms);
        ngx_shmtx_unlock(&shpool->mutex);
        return;
    }

    ngx_memcpy(node->path, r->uri.data, r->uri.len);
    node->path_len = r->uri.len;
    node->rbnode.key = key;
    node->conversions = 1;
    node->entries = 1;
    node->conversion_time_sum_ms = (ngx_atomic_t) elapsed_ms;

    ngx_rbtree_insert(&metrics->per_path.path_tree, &node->rbnode);

    ngx_atomic_fetch_add(&metrics->per_path.path_entries, 1);
    ngx_atomic_fetch_add(&metrics->per_path.path_conversions, 1);
    ngx_atomic_fetch_add(&metrics->per_path.path_conversion_time_sum_ms,
                         (ngx_atomic_uint_t) elapsed_ms);

    ngx_shmtx_unlock(&shpool->mutex);
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
    const ngx_http_markdown_conf_t *conf)
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
    const ngx_http_markdown_conf_t *conf,
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

    ngx_http_markdown_prepare_conversion_options(
        r, conf, ctx->effective_conf, &options);

    /*
     * Record shadow attempt unconditionally at entry so
     * shadow_total reflects attempts, not only successful
     * comparisons.  This keeps the shadow_diff_rate formula
     * (shadow_diff_total / shadow_total) well-defined even
     * when the streaming engine fails to initialize.
     */
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.shadow_total);
    ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
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
        (void) total_len;  /* used only in debug log below */
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
        NGX_HTTP_MARKDOWN_METRIC_ADD(results.estimated_token_savings, savings);
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
                                     const ngx_http_markdown_conf_t *conf,
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

    ctx->otel_span = NULL;
    if (conf->ops.otel_enabled) {
        const ngx_str_t *flavor_name;
        const ngx_str_t *engine_name;

        ctx->otel_span = ngx_http_markdown_otel_span_start(r, conf);
        if (ctx->otel_span != NULL) {
            flavor_name = ngx_http_markdown_otel_flavor_name(conf->flavor);
            engine_name = ngx_http_markdown_otel_engine_name(
                ctx->processing_path);
            ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
                (const u_char *) "flavor", 6,
                flavor_name->data, flavor_name->len);
            ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
                (const u_char *) "engine", 6,
                engine_name->data, engine_name->len);
            if (r->uri.len > 0) {
                ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
                    (const u_char *) "uri", 3,
                    r->uri.data, r->uri.len);
            }
        }
    }

    rc = ngx_http_markdown_prepare_conversion_options(
        r, conf, ctx->effective_conf, &options);
    if (rc != NGX_OK) {
        ngx_http_markdown_otel_teardown_span(r, ctx, ctx->buffer.size, 0,
                                             ERROR_INVALID_INPUT);
        return ngx_http_markdown_reject_or_fail_open_buffered_response(
            r, ctx, conf, NULL);
    }

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
        ngx_http_markdown_otel_teardown_span(
            r, ctx, ctx->buffer.size, 0, (int64_t) result->error_code);
        return ngx_http_markdown_handle_conversion_failure(
            r, ctx, conf, result, *elapsed_ms);
    }

    rc = ngx_http_markdown_validate_conversion_result(r, ctx, conf, result);
    if (rc != NGX_OK) {
        ngx_http_markdown_otel_teardown_span(
            r, ctx, ctx->buffer.size, 0, ERROR_INTERNAL);
        return rc;
    }

    ngx_http_markdown_record_conversion_success(ctx, result, *elapsed_ms);

    ngx_http_markdown_record_per_path_metrics(r, conf, *elapsed_ms);

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

    ngx_http_markdown_otel_teardown_span(
        r, ctx, ctx->buffer.size, result->markdown_len, 0);

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
