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
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf);

/*
 * Clear full-buffer ownership state when request-pool teardown interrupts a
 * backpressured response before the normal resume path can drain it.
 */
static void
ngx_http_markdown_fullbuffer_cleanup(void *data)
{
    ngx_http_markdown_ctx_t  *ctx;

    ctx = data;
    if (ctx == NULL) {
        return;
    }

    ngx_http_markdown_pending_output_set(
        &ctx->fullbuffer.pending_output, NULL);
    ctx->fullbuffer.pending_has_data = 0;
}
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
 * Construct base URL for resolving relative URLs (spec 47 thin wrapper).
 *
 * The trust decision (CIDR matching, Forwarded/X-Forwarded-* precedence,
 * multi-hop handling, host/proto validation, and safe fallback) is a single
 * source of truth in the Rust core, reached through the
 * markdown_decide_base_url FFI.  The C side is glue only: it collects the
 * source IP, request headers, and the http-level trusted-proxy handle, calls
 * the FFI to obtain a validated "scheme://host" authority, and appends the
 * request URI.  No forwarded-header parsing or host validation lives in C.
 *
 * Format: scheme://host/uri
 * Example: https://example.com/docs/page.html
 *
 * Covers: base URL construction from request headers and server config
 * Implements: base_url construction via the Rust trusted-proxy decision
 */
static u_char ngx_http_markdown_hdr_forwarded[] = "Forwarded";
static u_char ngx_http_markdown_hdr_x_forwarded_for[] = "X-Forwarded-For";
static u_char ngx_http_markdown_hdr_x_forwarded_proto[] = "X-Forwarded-Proto";
static u_char ngx_http_markdown_hdr_x_forwarded_host[] = "X-Forwarded-Host";
static u_char ngx_http_markdown_hdr_x_forwarded_port[] = "X-Forwarded-Port";
static const u_char ngx_http_markdown_empty_header_value[] = "";

/* Maximum scheme://host authority length written by the FFI decision. */
#define NGX_HTTP_MARKDOWN_BASE_AUTHORITY_MAX  512

/*
 * Bound each combined forwarding field before allocating request-pool memory.
 * Values above this cap are ignored and the authority decision falls back to
 * the validated direct Host/scheme inputs.
 */
#define NGX_HTTP_MARKDOWN_FORWARDING_INPUT_MAX  8192

/* Internal result used to distinguish a matched value rejected by the cap. */
#define NGX_HTTP_MARKDOWN_FORWARDING_HEADER_TOO_LARGE  (-1001)

/* Match one request header against a case-insensitive field name. */
static ngx_flag_t
ngx_http_markdown_request_header_matches(const ngx_table_elt_t *header,
                                         const u_char *name,
                                         size_t name_len)
{
    return header->key.data != NULL
           && header->key.len == name_len
           && ngx_http_markdown_const_strncasecmp(
                  header->key.data, name, name_len) == 0;
}

/* Find a request header value by name in the generic linked-list storage. */
static const ngx_str_t *
ngx_http_markdown_find_request_header_value(const ngx_http_request_t *r,
                                            const u_char *name,
                                            size_t name_len)
{
    if (r->headers_in.headers.part.nelts == 0) {
        return NULL;
    }

    for (const ngx_list_part_t *part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        const ngx_table_elt_t  *headers;

        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (ngx_http_markdown_request_header_matches(
                    &headers[i], name, name_len))
            {
                return &headers[i].value;
            }
        }
    }

    return NULL;
}

static ngx_int_t
ngx_http_markdown_accumulate_header_value_len(ngx_uint_t match_count,
    size_t value_len, size_t *total_len)
{
    if (match_count > 0) {
        if (*total_len > (size_t) -1 - 2) {
            return NGX_ERROR;
        }
        *total_len += 2;
    }
    if (value_len > (size_t) -1 - *total_len) {
        return NGX_ERROR;
    }
    if (*total_len > NGX_HTTP_MARKDOWN_FORWARDING_INPUT_MAX
        || value_len > NGX_HTTP_MARKDOWN_FORWARDING_INPUT_MAX - *total_len)
    {
        return NGX_HTTP_MARKDOWN_FORWARDING_HEADER_TOO_LARGE;
    }

    *total_len += value_len;
    return NGX_OK;
}

/*
 * Validate matching values and calculate their exact combined length.
 */
static ngx_int_t
ngx_http_markdown_measure_request_header_values(
    const ngx_http_request_t *r, const u_char *name, size_t name_len,
    const ngx_str_t **single_value, ngx_uint_t *match_count,
    size_t *total_len)
{
    const ngx_list_part_t *part;
    const ngx_table_elt_t *headers;

    *single_value = NULL;
    *match_count = 0;
    *total_len = 0;

    part = &r->headers_in.headers.part;
    for (/* void */; part != NULL; part = part->next) {
        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (!ngx_http_markdown_request_header_matches(
                    &headers[i], name, name_len))
            {
                continue;
            }
            if (headers[i].value.len > 0 && headers[i].value.data == NULL) {
                return NGX_ERROR;
            }

            ngx_int_t  rc;
            rc = ngx_http_markdown_accumulate_header_value_len(
                     *match_count, headers[i].value.len, total_len);
            if (rc != NGX_OK) {
                return rc;
            }

            *single_value = &headers[i].value;
            (*match_count)++;
        }
    }

    return NGX_OK;
}

/* Copy validated matching values to an exactly sized output buffer. */
static void
ngx_http_markdown_copy_request_header_values(
    const ngx_http_request_t *r, const u_char *name, size_t name_len,
    u_char *data)
{
    const ngx_list_part_t *part;
    const ngx_table_elt_t *headers;
    ngx_uint_t             copied;
    u_char                *p;

    copied = 0;
    p = data;
    part = &r->headers_in.headers.part;
    for (/* void */; part != NULL; part = part->next) {
        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (!ngx_http_markdown_request_header_matches(
                    &headers[i], name, name_len))
            {
                continue;
            }

            if (copied > 0) {
                *p++ = ',';
                *p++ = ' ';
            }
            if (headers[i].value.len > 0) {
                p = ngx_cpymem(p, headers[i].value.data,
                               headers[i].value.len);
            }
            copied++;
        }
    }
}

/*
 * Collect all active request-header field lines with one name.
 *
 * RFC field lines are combined in wire order with a comma+space separator
 * before crossing the FFI boundary. The first pass validates every value and
 * calculates the exact allocation size with overflow guards; the second pass
 * copies only after the allocation succeeds. A single value remains a
 * zero-copy view into NGINX request storage.
 *
 * Returns NGX_OK with `out` populated, NGX_DECLINED when no matching field
 * line exists, NGX_HTTP_MARKDOWN_FORWARDING_HEADER_TOO_LARGE when a matching
 * value exceeds the input cap, or NGX_ERROR for invalid storage, overflow,
 * or allocation failure.
 */
static ngx_int_t
ngx_http_markdown_collect_request_header_values(
    const ngx_http_request_t *r,
                                                const u_char *name,
                                                size_t name_len,
                                                ngx_str_t *out)
{
    const ngx_str_t  *first_match;
    const ngx_str_t  *single_value;
    ngx_uint_t        match_count;
    size_t            total_len;
    u_char           *data;
    ngx_int_t         rc;

    if (r == NULL || name == NULL || name_len == 0 || out == NULL) {
        return NGX_ERROR;
    }

    out->data = NULL;
    out->len = 0;

    first_match = ngx_http_markdown_find_request_header_value(
        r, name, name_len);
    if (first_match == NULL) {
        return NGX_DECLINED;
    }

    rc = ngx_http_markdown_measure_request_header_values(
        r, name, name_len, &single_value, &match_count, &total_len);
    if (rc != NGX_OK) {
        return rc;
    }

    if (match_count == 0) {
        return NGX_DECLINED;
    }
    if (match_count == 1) {
        *out = *single_value;
        return NGX_OK;
    }

    data = ngx_pnalloc(r->pool, total_len);
    if (data == NULL) {
        return NGX_ERROR;
    }

    ngx_http_markdown_copy_request_header_values(
        r, name, name_len, data);

    out->data = data;
    out->len = total_len;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_collect_forwarding_header(
    const ngx_http_request_t *r, const u_char *name, size_t name_len,
    ngx_str_t *value, ngx_flag_t *present)
{
    const ngx_str_t  *first_match;
    ngx_int_t         rc;

    if (r == NULL || name == NULL || name_len == 0
        || value == NULL || present == NULL) {
        return NGX_ERROR;
    }

    first_match = ngx_http_markdown_find_request_header_value(
        r, name, name_len);
    if (first_match == NULL) {
        *present = 0;
        value->data = NULL;
        value->len = 0;
        return NGX_OK;
    }

    rc = ngx_http_markdown_collect_request_header_values(
        r, name, name_len, value);
    if (rc == NGX_ERROR) {
        return NGX_ERROR;
    }
    if (rc == NGX_DECLINED) {
        *present = 0;
        value->data = NULL;
        value->len = 0;
        return NGX_OK;
    }
    if (rc == NGX_HTTP_MARKDOWN_FORWARDING_HEADER_TOO_LARGE) {
        /* Preserve presence so Rust keeps the malformed-header precedence. */
        *present = 1;
        value->data = NULL;
        value->len = 0;
        return NGX_OK;
    }
    if (rc != NGX_OK) {
        return NGX_ERROR;
    }

    *present = 1;
    return NGX_OK;
}


static void
ngx_http_markdown_set_base_url_header(
    const ngx_str_t *value, ngx_flag_t present,
    const uint8_t **target, uintptr_t *target_len)
{
    if (!present) {
        return;
    }

    *target = value->data != NULL
        ? value->data : ngx_http_markdown_empty_header_value;
    *target_len = value->len;
}

/*
 * Decide the validated "scheme://host" authority via the Rust trusted-proxy
 * decision (markdown_decide_base_url).
 *
 * This is a thin wrapper: it marshals the original transport peer
 * (r->connection->realip when the realip module preserved it, otherwise
 * r->connection->addr_text, plus the AF_UNIX flag), the forwarded request
 * headers, the request Host, and the http-level trusted-proxy CIDR handle
 * into the FFI input, then copies the FFI-produced authority into out_buf.
 * It contains no trust, CIDR-matching, or host-validation branches.
 *
 * Parameters:
 *   r        - HTTP request (NGINX glue source for IP/headers/config)
 *   out_buf  - caller buffer receiving the "scheme://host" authority
 *   out_cap  - capacity of out_buf in bytes
 *   out_len  - set to the number of authority bytes written on success
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR when the FFI rejects the inputs.
 */
static ngx_int_t
ngx_http_markdown_decide_base_authority(const ngx_http_request_t *r,
                                        u_char *out_buf,
                                        size_t out_cap,
                                        size_t *out_len)
{
    const ngx_http_markdown_main_conf_t  *mmcf;
    ngx_str_t                             forwarded;
    ngx_str_t                             x_forwarded_for;
    ngx_str_t                             x_forwarded_proto;
    ngx_str_t                             x_forwarded_host;
    ngx_str_t                             x_forwarded_port;
    FFIBaseUrlInput                       input;
    FFIBaseUrlDecision                    decision;
    uint8_t                               rc;
    ngx_flag_t                             forwarded_present;
    ngx_flag_t                             x_forwarded_for_present;
    ngx_flag_t                             x_forwarded_proto_present;
    ngx_flag_t                             x_forwarded_host_present;
    ngx_flag_t                             x_forwarded_port_present;

    mmcf = ngx_http_get_module_main_conf(r, ngx_http_markdown_filter_module);

    if (ngx_http_markdown_collect_forwarding_header(
            r, ngx_http_markdown_hdr_forwarded,
            sizeof(ngx_http_markdown_hdr_forwarded) - 1,
            &forwarded, &forwarded_present) != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_collect_forwarding_header(
            r, ngx_http_markdown_hdr_x_forwarded_for,
            sizeof(ngx_http_markdown_hdr_x_forwarded_for) - 1,
            &x_forwarded_for, &x_forwarded_for_present) != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_collect_forwarding_header(
            r, ngx_http_markdown_hdr_x_forwarded_proto,
            sizeof(ngx_http_markdown_hdr_x_forwarded_proto) - 1,
            &x_forwarded_proto, &x_forwarded_proto_present) != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_collect_forwarding_header(
            r, ngx_http_markdown_hdr_x_forwarded_host,
            sizeof(ngx_http_markdown_hdr_x_forwarded_host) - 1,
            &x_forwarded_host, &x_forwarded_host_present) != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_collect_forwarding_header(
            r, ngx_http_markdown_hdr_x_forwarded_port,
            sizeof(ngx_http_markdown_hdr_x_forwarded_port) - 1,
            &x_forwarded_port, &x_forwarded_port_present) != NGX_OK)
    {
        return NGX_ERROR;
    }

    markdown_base_url_input_init(&input);

    if (r->connection != NULL) {
        /*
         * Trusted-proxy CIDR evaluation must use the original transport
         * peer, not the realip-resolved address: when the realip module
         * (or PROXY protocol) rewrites connection->addr_text to the
         * X-Forwarded-For-derived client address, the original connecting
         * peer is preserved in connection->realip.  Matching the CIDR
         * against the rewritten addr_text would classify the request by
         * the spoofable client address instead of the actual proxy.
         */
        if (r->connection->realip.len > 0) {
            input.source_ip = r->connection->realip.data;
            input.source_ip_len = r->connection->realip.len;
        } else {
            input.source_ip = r->connection->addr_text.data;
            input.source_ip_len = r->connection->addr_text.len;
        }
        if (r->connection->sockaddr != NULL
            && r->connection->sockaddr->sa_family == AF_UNIX)
        {
            input.is_unix_socket = 1;
        }
    }

    if (mmcf != NULL) {
        input.trusted = mmcf->trusted_proxies;
        input.trusted_configured =
            mmcf->trusted_proxies_configured ? 1 : 0;
    }

    ngx_http_markdown_set_base_url_header(
        &forwarded, forwarded_present,
        &input.forwarded, &input.forwarded_len);
    ngx_http_markdown_set_base_url_header(
        &x_forwarded_for, x_forwarded_for_present,
        &input.x_forwarded_for, &input.x_forwarded_for_len);
    ngx_http_markdown_set_base_url_header(
        &x_forwarded_proto, x_forwarded_proto_present,
        &input.x_forwarded_proto, &input.x_forwarded_proto_len);
    ngx_http_markdown_set_base_url_header(
        &x_forwarded_host, x_forwarded_host_present,
        &input.x_forwarded_host, &input.x_forwarded_host_len);
    ngx_http_markdown_set_base_url_header(
        &x_forwarded_port, x_forwarded_port_present,
        &input.x_forwarded_port, &input.x_forwarded_port_len);
    if (r->headers_in.server.len > 0) {
        input.host = r->headers_in.server.data;
        input.host_len = r->headers_in.server.len;
    }

    /*
     * Pass the direct connection scheme (r->schema) so Host header
     * fallback uses the actual protocol.  Without this, a direct
     * HTTPS request would get an http:// base URL, breaking relative
     * link resolution.
     */
    if (r->schema.len > 0) {
        input.direct_scheme = r->schema.data;
        input.direct_scheme_len = r->schema.len;
    }

    rc = markdown_decide_base_url(&input, out_buf, out_cap, &decision);
    if (rc != DECIDE_BASE_URL_OK) {
        return NGX_ERROR;
    }

    /* Defensive: Rust FFI guarantees base_url_len <= out_cap on OK,
     * but guard the C boundary explicitly (Rule 46). */
    if (decision.base_url_len > out_cap) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: base_url_len %uz exceeds out_cap %uz",
                      (size_t) decision.base_url_len, out_cap);
        return NGX_ERROR;
    }

    *out_len = decision.base_url_len;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: base_url trust decision reason=%ui",
                  (ngx_uint_t) decision.reason);

    return NGX_OK;
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
                     "markdown: base_url length overflow (%s)",
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
    u_char     authority[NGX_HTTP_MARKDOWN_BASE_AUTHORITY_MAX];
    size_t     authority_len;
    u_char    *p;
    size_t     len;

    /* Initialize output */
    base_url->data = NULL;
    base_url->len = 0;

    authority_len = 0;
    if (ngx_http_markdown_decide_base_authority(
            r, authority, sizeof(authority), &authority_len) != NGX_OK)
    {
        return NGX_ERROR;
    }

    len = 0;
    if (ngx_http_markdown_base_url_add_len(r, &len, authority_len,
                                           "authority") != NGX_OK)
    {
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
         * Covers: base_url allocation failure handling
         */
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown: failed to allocate memory for base_url, category=system");
        return NGX_ERROR;
    }

    base_url->data = p;

    /* Copy the validated scheme://host authority */
    p = ngx_cpymem(p, authority, authority_len);

    /* Copy URI */
    p = ngx_cpymem(p, r->uri.data, r->uri.len);

    base_url->len = p - base_url->data;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: constructed base_url: \"%V\"", base_url);

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

/* Record the frozen v1 histogram with the engine selected for this request. */
static void
ngx_http_markdown_record_conversion_latency_for_path(ngx_uint_t path,
                                                     ngx_msec_t elapsed_ms)
{
    ngx_http_markdown_record_conversion_latency(elapsed_ms);

    if (path == NGX_HTTP_MARKDOWN_PATH_STREAMING) {
        NGX_HTTP_MARKDOWN_METRIC_ADD(
            conversion_latency_v1.streaming.sum_ms, elapsed_ms);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency_v1.streaming.count);
        if (elapsed_ms <= 1) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_1ms);
        } else if (elapsed_ms <= 5) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_5ms);
        } else if (elapsed_ms <= 10) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_10ms);
        } else if (elapsed_ms <= 25) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_25ms);
        } else if (elapsed_ms <= 50) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_50ms);
        } else if (elapsed_ms <= 100) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_100ms);
        } else if (elapsed_ms <= 250) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_250ms);
        } else if (elapsed_ms <= 500) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_500ms);
        } else if (elapsed_ms <= 1000) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_1000ms);
        } else if (elapsed_ms <= 5000) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                conversion_latency_v1.streaming.le_5000ms);
        }
        return;
    }

    NGX_HTTP_MARKDOWN_METRIC_ADD(
        conversion_latency_v1.full_buffer.sum_ms, elapsed_ms);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversion_latency_v1.full_buffer.count);
    if (elapsed_ms <= 1) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_1ms);
    } else if (elapsed_ms <= 5) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_5ms);
    } else if (elapsed_ms <= 10) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_10ms);
    } else if (elapsed_ms <= 25) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_25ms);
    } else if (elapsed_ms <= 50) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_50ms);
    } else if (elapsed_ms <= 100) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_100ms);
    } else if (elapsed_ms <= 250) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_250ms);
    } else if (elapsed_ms <= 500) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_500ms);
    } else if (elapsed_ms <= 1000) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_1000ms);
    } else if (elapsed_ms <= 5000) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversion_latency_v1.full_buffer.le_5000ms);
    }
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
                      "markdown: If-None-Match matched, sending 304 Not Modified");

        /* Decision G: the converted representation's weak validator must
         * not describe the source HTML mtime.  ETag (Markdown-derived,
         * strong) is the sole validator; do NOT restore the source
         * last-modified time on the 304. */
        r->headers_out.last_modified_time = (time_t) -1;

        rc = ngx_http_markdown_send_304(r, conditional_result);
        if (conditional_result != NULL) {
            markdown_result_free(conditional_result);
        }

        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        if (rc != NGX_DONE) {
            ngx_http_markdown_record_system_failure(ctx);
            return rc;
        }

        return NGX_HTTP_NOT_MODIFIED;
    }

    if (rc == NGX_ERROR) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: error during If-None-Match processing");

        if (conditional_result != NULL) {
            markdown_result_free(conditional_result);
        }

        ngx_http_markdown_record_system_failure(ctx);

        return ngx_http_markdown_reject_or_fail_open_buffered_response(
            r, ctx, conf,
            "markdown: fail-open strategy - returning original HTML");
    }

    if (rc == NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT) {
        /*
         * Conditional Bypass (Range or no-transform): deliver the
         * upstream response unmodified.  The conversion result (if
         * any) was already freed by handle_if_none_match.
         *
         * This path is a safety net — the header filter should have
         * caught no-transform and Range before buffering.  If we reach
         * here, it means conditional headers were present alongside a
         * bypass condition, and the Rust decision correctly returned
         * Bypass instead of Proceed.
         *
         * Bypass is a protocol/cache semantic, NOT a conversion error.
         * It must NOT go through markdown_error_policy: even when
         * on_error == REJECT, the original upstream response must be
         * delivered unmodified.  Call fail_open directly, not
         * reject_or_fail_open.
         */
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: conditional bypass, delivering "
                      "original buffered response");

        if (conditional_result != NULL) {
            markdown_result_free(conditional_result);
        }

        rc = ngx_http_markdown_fail_open_buffered_response(
            r, ctx,
            "markdown: conditional bypass - returning original HTML");
        /*
         * Bypass is a protocol/cache semantic, NOT a conversion failure
         * or fail-open error. Do NOT increment failopen_count — that
         * metric tracks delivery of failed conversions, not intentional
         * bypasses. The bypass is already recorded via the
         * bypass_no_transform reason code in the header filter.
         */
        return rc;
    }

    if (rc == NGX_DECLINED && conditional_result != NULL) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: If-None-Match did not match, using existing conversion");

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

    markdown_options_init(options);
    if (conf->flavor > UINT32_MAX) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: flavor=%ui exceeds uint32 max, clamping",
                     conf->flavor);
        options->flavor = UINT32_MAX;
    } else {
        options->flavor = (uint32_t) conf->flavor;
    }

    if (conf->timeout > UINT32_MAX) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: timeout=%M exceeds uint32 max, clamping",
                     conf->timeout);
        options->timeout_ms = UINT32_MAX;
    } else {
        options->timeout_ms = (uint32_t) conf->timeout;
    }

    options->generate_etag = conf->policy.generate_etag ? 1U : 0U;
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

    if (conf->advanced.prune_selectors != NULL) {
        options->prune_selectors = conf->advanced.prune_selectors->data;
        options->prune_selector_len = conf->advanced.prune_selectors->len;
    }

    if (conf->advanced.prune_protection_selectors != NULL) {
        options->prune_protection_selectors =
            conf->advanced.prune_protection_selectors->data;
        options->prune_protection_selector_len =
            conf->advanced.prune_protection_selectors->len;
    }

    /*
     * Resolve the effective static conversion_memory budget once for this
     * request. A zero value means no FFI-side constraint, so Rust may use its
     * bounded full-buffer fallback; the normal NGINX default is 64 MiB from
     * markdown_limits conversion_memory=64m. Runtime dynconf cannot replace
     * this static public limit.
     */
    options->memory_budget =
        ngx_http_markdown_effective_memory_budget(eff, conf);
    if (options->memory_budget == NGX_CONF_UNSET_SIZE) {
        options->memory_budget = 0;
    }

    /* This is the fixed flush minimum, not markdown_limits streaming_buffer. */
    options->flush_threshold =
        (NGX_HTTP_MARKDOWN_STREAM_FLUSH_MIN_FIXED > UINT32_MAX)
            ? UINT32_MAX
            : (uint32_t) NGX_HTTP_MARKDOWN_STREAM_FLUSH_MIN_FIXED;

    /*
     * Parse-specific timeout and memory budget.
     * parse_timeout_ms: populated from conf->decompress.parse_timeout (ngx_msec_t).
     * parser_memory_budget: populated from conf->decompress.parser_budget (size_t).
     */
    if (conf->decompress.parse_timeout > UINT32_MAX) {
        options->parse_timeout_ms = UINT32_MAX;
    } else {
        options->parse_timeout_ms = (uint32_t) conf->decompress.parse_timeout;
    }
    options->parser_memory_budget = (uint64_t) conf->decompress.parser_budget;

    if (r->headers_out.content_type.len > 0) {
        options->content_type = r->headers_out.content_type.data;
        options->content_type_len = r->headers_out.content_type.len;
    }

    if (ngx_http_markdown_construct_base_url(r, r->pool, &base_url) == NGX_OK) {
        options->base_url = base_url.data;
        options->base_url_len = base_url.len;
    } else {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: continuing conversion without base_url");
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
    int                               err_len = 0;

    error_category = ngx_http_markdown_classify_error(result->error_code);
    category_str = ngx_http_markdown_error_category_string(error_category);

    /* Store error category in context for decision log emission */
    ctx->error.last_category = error_category;
    ctx->error.has_category = 1;

    ngx_http_markdown_record_conversion_latency_for_path(
        ctx->processing_path, elapsed_ms);
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

    switch (result->error_code) {
        case ERROR_DECOMPRESSION_BUDGET_EXCEEDED:
            NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total);
            ngx_http_markdown_record_decompression_failure_budget(
                ctx->decompression.type);
            NGX_HTTP_MARKDOWN_METRIC_INC(
                perf.decompression_budget_exceeded_total);
            break;
        case ERROR_DECOMPRESSION_FORMAT_ERROR:
            NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.format_error_total);
            ngx_http_markdown_record_decompression_failure_format(
                ctx->decompression.type);
            break;
        case ERROR_DECOMPRESSION_TRUNCATED_INPUT:
            NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.truncated_input_total);
            ngx_http_markdown_record_decompression_failure_truncated(
                ctx->decompression.type);
            break;
        case ERROR_DECOMPRESSION_IO_ERROR:
            NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.io_error_total);
            ngx_http_markdown_record_decompression_failure_io(
                ctx->decompression.type);
            break;
        case ERROR_PARSE_TIMEOUT:
            NGX_HTTP_MARKDOWN_METRIC_INC(
                results.parse_interrupts.parse_timeouts_total);
            break;
        case ERROR_PARSE_BUDGET_EXCEEDED:
            NGX_HTTP_MARKDOWN_METRIC_INC(
                results.parse_interrupts.parse_budget_exceeded_total);
            break;
        default:
            break;
    }
    if (result->error_message != NULL) {
        err_len = (result->error_len > (size_t) INT_MAX)
            ? INT_MAX
            : (int) result->error_len;
    }

    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown: conversion failed, "
                 "error_code=%ud, category=%V, message=\"%*s\", elapsed_ms=%M",
                 result->error_code,
                 category_str,
                 err_len,
                 (result->error_message != NULL) ? result->error_message : ngx_http_markdown_empty_string,
                 elapsed_ms);

    markdown_result_free(result);

    return ngx_http_markdown_reject_or_fail_open_buffered_response(
        r, ctx, conf,
        "markdown: fail-open strategy - returning original HTML");
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
    ctx->error.last_category =
        NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    ctx->error.has_category = 1;
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
                     "markdown: invalid FFI result "
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
    ctx->conversion.succeeded = 1;
    ctx->conversion.input_bytes = ctx->buffer.size;
    ctx->conversion.output_bytes = result->markdown_len;
    ngx_http_markdown_record_conversion_latency_for_path(
        ctx->processing_path, elapsed_ms);
}

/* Record converted bytes and outcome only after downstream accepts the body. */
static void
ngx_http_markdown_record_buffered_delivery_success(
    ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL
        || !ctx->conversion.succeeded
        || ctx->conversion.delivery_recorded)
    {
        return;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_succeeded);
    NGX_HTTP_MARKDOWN_METRIC_ADD(input_bytes, ctx->conversion.input_bytes);
    NGX_HTTP_MARKDOWN_METRIC_ADD(output_bytes, ctx->conversion.output_bytes);
    ctx->conversion.delivery_recorded = 1;
}

/* A definitive post-conversion delivery error is one terminal failure. */
static void
ngx_http_markdown_record_buffered_delivery_failure(
    ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL
        || !ctx->conversion.succeeded
        || ctx->conversion.delivery_recorded
        || ctx->error.has_category)
    {
        return;
    }

    ngx_http_markdown_record_system_failure(ctx);
}

/*
 * Per-path RB-tree helpers removed from production in 0.9.2.
 * Retained under debug guard only.
 */
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
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
#endif /* MARKDOWN_METRICS_PER_PATH_DEBUG */

/*
 * Record per-path metrics for a successful conversion.
 *
 * No-op placeholder since 0.9.2 (per-path metrics removed;
 * the directive was deleted).  Retained only to keep the
 * call sites stable across releases.
 *
 * Parameters:
 *   r          - the HTTP request (provides r->uri as the path key)
 *   conf       - module location configuration
 *   elapsed_ms - conversion elapsed time in milliseconds
 */
static void
ngx_http_markdown_record_per_path_metrics(
    const ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    ngx_msec_t elapsed_ms)
{
    /* Per-path metrics removed in 0.9.2 (directive deleted). */
    (void) r;
    (void) conf;
    (void) elapsed_ms;
    return;
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
                 "markdown: converter not "
                 "initialized, category=system");
    ngx_http_markdown_record_system_failure(ctx);

    return ngx_http_markdown_reject_or_fail_open_buffered_response(
        r, ctx, conf,
        "markdown: fail-open strategy "
        "- returning original HTML");
}


#ifdef MARKDOWN_STREAMING_SHADOW_DEBUG
static ngx_flag_t
ngx_http_markdown_shadow_output_diff(const struct MarkdownResult *fb_result,
                                     const uint8_t *feed_data,
                                     uintptr_t feed_len,
                                     const struct MarkdownResult *st_result)
{
    size_t   total_len;
    const u_char  *fb_ptr;

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
    const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const struct MarkdownResult *fb_result,
    ngx_msec_t fb_elapsed_ms)
{
    struct StreamingConverterHandle  *handle;
    struct MarkdownOptions            options;
    struct MarkdownResult             st_result;
    uint8_t                          *out_data;
    uintptr_t                         out_len;
    ngx_int_t                         opt_rc;
    uint32_t                          init_rc;
    uint32_t                          rc;
    const ngx_time_t                 *tp;
    ngx_msec_t                        shadow_start;
    ngx_msec_t                        shadow_elapsed;

    opt_rc = ngx_http_markdown_prepare_conversion_options(
        r, conf, ctx->effective_conf, &options);
    if (opt_rc != NGX_OK) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
            "markdown: shadow conversion options failed rc=%i",
            opt_rc);
        return;
    }

    /*
     * Record the shadow attempt after conversion options are
     * prepared so shadow_total reflects actual shadow runs, not
     * requests that fail before the streaming engine starts.
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
            "markdown: streaming engine "
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
            "markdown: streaming feed "
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

    markdown_result_init(&st_result);
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
        "markdown: "
        "shadow_streaming_latency_ms=%M "
        "shadow_fullbuffer_latency_ms=%M",
        shadow_elapsed, fb_elapsed_ms);

    /* Suppress unused-variable warnings in non-debug builds where
     * ngx_log_debug2 compiles to nothing. */
    (void) shadow_elapsed;
    (void) fb_elapsed_ms;

    if (rc != ERROR_SUCCESS) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: streaming finalize "
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
     * Record streaming engine peak memory
     * estimate in shadow mode when available.
     *
     * Update the last_peak_memory_bytes gauge
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
            "markdown: peak_memory_bytes=%uz",
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
                "markdown: output diff "
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
#endif /* MARKDOWN_STREAMING_SHADOW_DEBUG */

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

#ifdef MARKDOWN_INCREMENTAL_ENABLED
static ngx_int_t
ngx_http_markdown_execute_incremental_conversion(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const struct MarkdownOptions *options,
    struct MarkdownResult *result,
    ngx_msec_t start_time,
    ngx_msec_t *elapsed_ms)
{
    struct IncrementalConverterHandle *inc_handle;
    uint32_t                           init_rc;
    uint32_t                           feed_rc;
    uint32_t                           fin_rc;
    const ngx_time_t                  *tp;
    ngx_msec_t                         end_time;

    inc_handle = NULL;
    init_rc = markdown_incremental_new_with_code(
        options, &inc_handle);
    if (init_rc != ERROR_SUCCESS || inc_handle == NULL) {
        tp = ngx_timeofday();
        end_time = (ngx_msec_t) (tp->sec * 1000 + tp->msec);
        *elapsed_ms = (end_time >= start_time) ? end_time - start_time : 0;

        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: "
                     "markdown_incremental_new_with_code() "
                     "failed rc=%ud", (ngx_uint_t) init_rc);

        result->error_code = init_rc ? init_rc : ERROR_INVALID_INPUT;
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

    return NGX_OK;
}
#endif


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


    rc = ngx_http_markdown_prepare_conversion_options(
        r, conf, ctx->effective_conf, &options);
    if (rc != NGX_OK) {
        return ngx_http_markdown_reject_or_fail_open_buffered_response(
            r, ctx, conf, NULL);
    }

    tp = ngx_timeofday();
    start_time = (ngx_msec_t) (tp->sec * 1000 + tp->msec);

    markdown_result_init(result);

#ifdef MARKDOWN_INCREMENTAL_ENABLED
    if (ctx->processing_path == NGX_HTTP_MARKDOWN_PATH_INCREMENTAL) {
        rc = ngx_http_markdown_execute_incremental_conversion(
            r, ctx, conf, &options, result, start_time, elapsed_ms);
        if (rc != NGX_OK) {
            return rc;
        }
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

    ngx_http_markdown_record_per_path_metrics(r, conf, *elapsed_ms);

    ngx_http_markdown_record_token_savings_if_enabled(ctx, conf, result);


    return NGX_OK;
}

/* Prepare an empty output buffer for HEAD requests (body omitted). */
static void
ngx_http_markdown_prepare_head_output_buffer(const ngx_http_request_t *r,
                                             ngx_buf_t *b,
                                             const struct MarkdownResult *result)
{
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: HEAD request - omitting response body");

    b->pos = NULL;
    b->last = NULL;
    b->memory = 0;
    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;
    (void) result;
}

/* Copy converted Markdown into a pool-allocated output buffer. */
static ngx_int_t
ngx_http_markdown_prepare_body_output_buffer(ngx_http_request_t *r,
                                             ngx_buf_t *b,
                                             const struct MarkdownResult *result)
{
    if (result->markdown_len > 0) {
        b->pos = ngx_pnalloc(r->pool, result->markdown_len);
        if (b->pos == NULL) {
            ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                         "markdown: failed to allocate output memory, category=system");
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
    return NGX_OK;
}

/* Update response headers and emit the converted Markdown downstream.
 *
 * Rule: alloc-before-send.  All output resources (body buffer, pool copy,
 * chain link) are allocated BEFORE header forwarding.  If any allocation
 * fails, headers have NOT been sent, so the downstream connection does not
 * receive a partial headers-sent-but-no-body response.
 */
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
                  "markdown: conversion succeeded, "
                  "input: %uz bytes, output: %uz bytes, elapsed: %M ms",
                  ctx->buffer.size, result->markdown_len, elapsed_ms);

    /*
     * Step 1: Allocate body buffer BEFORE header forwarding.
     * If this fails, headers have not been sent (safe to return NGX_ERROR).
     */
    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown: failed to allocate output buffer, category=system");
        ngx_http_markdown_record_buffered_delivery_failure(ctx);
        markdown_result_free(result);
        return NGX_ERROR;
    }

    /*
     * Step 2: Copy Rust output into NGINX pool memory BEFORE header
     * forwarding.  prepare_body_output_buffer copies result->markdown
     * into pool memory but we must NOT free result yet — update_headers
     * still needs result->etag and result->markdown_len.
     *
     * We do NOT call markdown_result_free inside prepare_body_output_buffer;
     * instead we defer it until after update_headers + forward_headers.
     */
    if (r->method == NGX_HTTP_HEAD) {
        ngx_http_markdown_prepare_head_output_buffer(r, b, result);
    } else {
        rc = ngx_http_markdown_prepare_body_output_buffer(r, b, result);
        if (rc != NGX_OK) {
            ngx_http_markdown_record_buffered_delivery_failure(ctx);
            markdown_result_free(result);
            return NGX_ERROR;
        }
    }

    /*
     * Step 3: Allocate chain link BEFORE header forwarding.
     */
    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown: failed to allocate output chain, category=system");
        ngx_http_markdown_record_buffered_delivery_failure(ctx);
        markdown_result_free(result);
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = NULL;

    /*
     * Step 4: Now all output resources are ready.
     * Mutate headers (uses result->etag and result->markdown_len).
     */
    rc = ngx_http_markdown_update_headers(r, result, conf);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown: failed to update response headers, "
                     "reason=header_plan_apply_error, category=system");
        ngx_http_markdown_record_system_failure(ctx);
        ngx_http_markdown_log_decision_with_category(
            r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_header_plan_apply_err(),
            ngx_http_markdown_reason_from_error_category(
                NGX_HTTP_MARKDOWN_ERROR_SYSTEM, r->connection->log));
        markdown_result_free(result);
        return ngx_http_markdown_reject_or_fail_open_buffered_response(
            r, ctx, conf,
            "markdown: header plan failure - applying error policy");
    }

    /*
     * Step 5: Release Rust-owned memory now that update_headers
     * has consumed result->etag.  After this, result is invalid.
     */
    markdown_result_free(result);

    /*
     * subrequest: release the buffered HTML backing store now that the
     * converted output has been copied into request-pool memory.
     * For subrequests this frees the (potentially large) HTML
     * buffer before the shared parent pool is destroyed
     * (per-parent-request memory retention).  Idempotent: the pool
     * cleanup registered by buffer_init remains as the fallback.
     */
    ngx_http_markdown_buffer_release(&ctx->buffer);

    /*
     * Step 6: Forward headers downstream (idempotent via headers_forwarded).
     */
    rc = ngx_http_markdown_forward_headers(r, ctx);
    if (rc != NGX_OK && rc != NGX_AGAIN) {
        ngx_http_markdown_record_buffered_delivery_failure(ctx);
        return rc;
    }
    /*
     * Header-chain NGX_AGAIN means "headers accepted": the write filter
     * queued the header block and owns delivery (NGINX core model —
     * header-chain NGX_AGAIN = queued by write filter; proceed with body
     * output).  Returning early here would strand the body: the re-entry
     * sees conversion.attempted && !pending_has_data, clears r->buffered
     * and the client receives headers with Content-Length but zero body
     * bytes (full-buffer truncation under slow clients).
     */

    /*
     * Step 7: Emit body downstream.
     */
    rc = ngx_http_next_body_filter(r, out);

    if (rc == NGX_OK || rc == NGX_DONE) {
        ngx_http_markdown_record_buffered_delivery_success(ctx);
        NGX_HTTP_MARKDOWN_METRIC_INC(results.delivery_count);
        NGX_HTTP_MARKDOWN_METRIC_INC(results.full_buffer_delivery_count);
    }

    if (rc == NGX_AGAIN) {
        ngx_http_markdown_pending_output_set(
            &ctx->fullbuffer.pending_output, out);
        ctx->fullbuffer.pending_has_data = 1;
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;

        /* Backpressure metric: body-filter output returned NGX_AGAIN */
        NGX_HTTP_MARKDOWN_METRIC_INC(perf.backpressure_total);

        /* Watermark gauge: CAS loop for pending output high-water */
        if (b->pos != NULL && b->last > b->pos) {
            NGX_HTTP_MARKDOWN_METRIC_WATERMARK(
                perf.pending_output_high_watermark_bytes,
                (ngx_atomic_t) (b->last - b->pos));
        }
    } else if (rc != NGX_OK && rc != NGX_DONE) {
        ngx_http_markdown_record_buffered_delivery_failure(ctx);
    }

    return rc;
}

/*
 * Resume a full-buffer response after downstream backpressure.
 *
 * The NGINX copy filter retains the unsent portion after returning NGX_AGAIN.
 * Passing the original chain again would append a duplicate copy of that
 * tail.  A NULL input asks the downstream filter chain to drain its existing
 * buffered state.  The request-owned chain pointer remains only as a lifetime
 * anchor and pending-state marker until the downstream chain finishes.
 *
 * Parameters:
 *   r   - current request
 *   ctx - request context with a pending full-buffer response
 *
 * Returns:
 *   NGX_AGAIN while downstream remains blocked; otherwise the downstream
 *   return code after clearing the module's pending state.
 */
static ngx_int_t
ngx_http_markdown_body_filter_resume_pending(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    rc = ngx_http_next_body_filter(r, NULL);
    if (rc == NGX_AGAIN) {
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
        return NGX_AGAIN;
    }

    if (rc == NGX_OK || rc == NGX_DONE) {
        ngx_http_markdown_record_buffered_delivery_success(ctx);
        NGX_HTTP_MARKDOWN_METRIC_INC(results.delivery_count);
        NGX_HTTP_MARKDOWN_METRIC_INC(results.full_buffer_delivery_count);
        /* Backpressure resume: drain completed successfully */
        NGX_HTTP_MARKDOWN_METRIC_INC(perf.backpressure_resume_total);
    }

    ngx_http_markdown_pending_output_set(
        &ctx->fullbuffer.pending_output, NULL);
    ctx->fullbuffer.pending_has_data = 0;
    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;

    return rc;
}

#endif /* NGX_HTTP_MARKDOWN_CONVERSION_IMPL_H */
