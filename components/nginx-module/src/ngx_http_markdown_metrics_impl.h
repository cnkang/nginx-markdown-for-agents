#ifndef NGX_HTTP_MARKDOWN_METRICS_IMPL_H
#define NGX_HTTP_MARKDOWN_METRICS_IMPL_H

/*
 * Metrics endpoint implementation.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * Isolated here so metrics formatting and access-control behavior can evolve
 * without adding more branching inside the core filter orchestration file.
 */

/*
 * Non-atomic snapshot of the shared metrics counters.
 *
 * Mirrors the layout of ngx_http_markdown_metrics_t but uses plain
 * ngx_atomic_uint_t values instead of ngx_atomic_t, since the snapshot
 * is only read from a single thread after collection.
 *
 * The decompression counters are grouped into an anonymous sub-struct
 * to keep the top-level field count within SonarCloud's 20-field limit
 * while preserving a flat JSON output format.
 */
typedef struct {
    /* Conversion attempt tracking */
    ngx_atomic_uint_t conversions_attempted;
    ngx_atomic_uint_t conversions_succeeded;
    ngx_atomic_uint_t conversions_failed;
    ngx_atomic_uint_t conversions_bypassed;

    /* Failure classification */
    ngx_atomic_uint_t failures_conversion;
    ngx_atomic_uint_t failures_resource_limit;
    ngx_atomic_uint_t failures_system;

    /* Performance metrics */
    ngx_atomic_uint_t conversion_time_sum_ms;
    ngx_atomic_uint_t input_bytes;
    ngx_atomic_uint_t output_bytes;

    /* Latency histogram buckets */
    ngx_atomic_uint_t conversion_latency_le_10ms;
    ngx_atomic_uint_t conversion_latency_le_100ms;
    ngx_atomic_uint_t conversion_latency_le_1000ms;
    ngx_atomic_uint_t conversion_latency_gt_1000ms;

    /* Decompression metrics (grouped to keep top-level field count <= 20) */
    struct {
        ngx_atomic_uint_t attempted;
        ngx_atomic_uint_t succeeded;
        ngx_atomic_uint_t failed;
        ngx_atomic_uint_t gzip;
        ngx_atomic_uint_t deflate;
        ngx_atomic_uint_t brotli;
    } decompressions;

    /* Path hit metrics (threshold router) */
    ngx_atomic_uint_t fullbuffer_path_hits;
    ngx_atomic_uint_t incremental_path_hits;
} ngx_http_markdown_metrics_snapshot_t;

/*
 * Response buffer size for the metrics endpoint.  The current JSON/text
 * output is well under 2 KiB, but we leave headroom for future fields.
 * Increase this constant if new metrics push the output beyond the limit.
 *
 * Estimated current output: ~1.5 KiB (JSON), ~1.2 KiB (text).
 * Last updated when fullbuffer_path_hits / incremental_path_hits were added.
 */
#define NGX_HTTP_MARKDOWN_METRICS_BUF_SIZE  5120

/**
 * Capture a best-effort snapshot of the global metrics counters into the
 * provided snapshot structure.
 *
 * The function zeroes the target snapshot and, if the global metrics instance
 * is available, copies the current values of all atomic counters into it.
 * The snapshot is not guaranteed to be a consistent point-in-time view.
 *
 * @param snapshot Pointer to an ngx_http_markdown_metrics_snapshot_t that will
 *                 be populated with the copied counters (may be left zeroed if
 *                 the global metrics instance is unavailable).
 */
static void
ngx_http_markdown_collect_metrics_snapshot(ngx_http_markdown_metrics_snapshot_t *snapshot)
{
    ngx_http_markdown_metrics_t *metrics;

    /*
     * NOTE: This is a best-effort snapshot, not a consistent point-in-time
     * view.  Individual atomic reads are sequentially consistent, but two
     * fields may reflect different moments if another worker updates the
     * shared counters between reads.  This is acceptable for monitoring
     * and diagnostics purposes.
     */
    ngx_memzero(snapshot, sizeof(ngx_http_markdown_metrics_snapshot_t));

    metrics = ngx_http_markdown_metrics;
    if (metrics == NULL) {
        return;
    }

    snapshot->conversions_attempted = metrics->conversions_attempted;
    snapshot->conversions_succeeded = metrics->conversions_succeeded;
    snapshot->conversions_failed = metrics->conversions_failed;
    snapshot->conversions_bypassed = metrics->conversions_bypassed;
    snapshot->failures_conversion = metrics->failures_conversion;
    snapshot->failures_resource_limit = metrics->failures_resource_limit;
    snapshot->failures_system = metrics->failures_system;
    snapshot->conversion_time_sum_ms = metrics->conversion_time_sum_ms;
    snapshot->input_bytes = metrics->input_bytes;
    snapshot->output_bytes = metrics->output_bytes;
    snapshot->conversion_latency_le_10ms = metrics->conversion_latency_le_10ms;
    snapshot->conversion_latency_le_100ms = metrics->conversion_latency_le_100ms;
    snapshot->conversion_latency_le_1000ms = metrics->conversion_latency_le_1000ms;
    snapshot->conversion_latency_gt_1000ms = metrics->conversion_latency_gt_1000ms;
    snapshot->decompressions.attempted = metrics->decompressions.attempted;
    snapshot->decompressions.succeeded = metrics->decompressions.succeeded;
    snapshot->decompressions.failed = metrics->decompressions.failed;
    snapshot->decompressions.gzip = metrics->decompressions.gzip;
    snapshot->decompressions.deflate = metrics->decompressions.deflate;
    snapshot->decompressions.brotli = metrics->decompressions.brotli;
    snapshot->fullbuffer_path_hits = metrics->fullbuffer_path_hits;
    snapshot->incremental_path_hits = metrics->incremental_path_hits;
}

static ngx_flag_t ngx_http_markdown_metrics_value_contains(
    ngx_str_t *value, u_char *needle, size_t needle_len);

static u_char  ngx_http_markdown_metrics_accept_json[] = "application/json";

/*
 * Validate method and shared-state availability for the metrics handler.
 *
 * Access control remains intentionally strict: the content handler itself
 * only serves loopback clients. NGINX `allow`/`deny` rules can further
 * restrict that location, but they cannot broaden access beyond localhost.
 */
static ngx_int_t
ngx_http_markdown_metrics_validate_request(ngx_http_request_t *r)
{
    if (!(r->method & (NGX_HTTP_GET|NGX_HTTP_HEAD))) {
        return NGX_HTTP_NOT_ALLOWED;
    }

    if (r->connection->sockaddr->sa_family == AF_INET) {
        struct sockaddr_in *sin = (struct sockaddr_in *) r->connection->sockaddr;
        if (ntohl(sin->sin_addr.s_addr) != INADDR_LOOPBACK) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown_metrics: access denied from non-localhost IPv4 address");
            return NGX_HTTP_FORBIDDEN;
        }
    }
#if (NGX_HAVE_INET6)
    else if (r->connection->sockaddr->sa_family == AF_INET6) {
        struct sockaddr_in6 *sin6 = (struct sockaddr_in6 *) r->connection->sockaddr;
        if (!IN6_IS_ADDR_LOOPBACK(&sin6->sin6_addr)) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown_metrics: access denied from non-localhost IPv6 address");
            return NGX_HTTP_FORBIDDEN;
        }
    }
#endif
    else {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown_metrics: access denied from unknown address family");
        return NGX_HTTP_FORBIDDEN;
    }

    if (ngx_http_markdown_metrics == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown_metrics: shared metrics state unavailable");
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    return NGX_OK;
}

/*
 * Content negotiation is intentionally simple: if the client advertises
 * application/json anywhere in Accept, emit the JSON form; otherwise use the
 * plain-text operator-friendly view.
 */
static ngx_flag_t
ngx_http_markdown_metrics_prefers_json(ngx_http_request_t *r)
{
    ngx_table_elt_t *accept_header;

    accept_header = NULL;

#if (NGX_HTTP_HEADERS)
    accept_header = r->headers_in.accept;
#else
    static ngx_str_t accept_name = ngx_string("Accept");

    accept_header = ngx_http_markdown_find_request_header(r, &accept_name);
#endif

    if (accept_header != NULL
        && ngx_http_markdown_metrics_value_contains(
            &accept_header->value,
            ngx_http_markdown_metrics_accept_json,
            sizeof(ngx_http_markdown_metrics_accept_json) - 1))
    {
        return 1;
    }

    return 0;
}

static ngx_flag_t
ngx_http_markdown_metrics_value_contains(ngx_str_t *value,
    u_char *needle,
    size_t needle_len)
{
    size_t  i;

    if (value == NULL || needle == NULL || needle_len == 0
        || value->len < needle_len)
    {
        return 0;
    }

    for (i = 0; i + needle_len <= value->len; i++) {
        if (ngx_strncasecmp(value->data + i, needle, needle_len)
            == 0)
        {
            return 1;
        }
    }

    return 0;
}

/*
 * Derive averages from the raw counters after taking the best-effort snapshot.
 * Division only occurs when the relevant denominator is non-zero.
 */
static void
ngx_http_markdown_metrics_derive_values(
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_atomic_uint_t *conversions_completed,
    ngx_atomic_uint_t *conversion_time_avg_ms,
    ngx_atomic_uint_t *input_bytes_avg,
    ngx_atomic_uint_t *output_bytes_avg)
{
    *conversions_completed = snapshot->conversions_succeeded + snapshot->conversions_failed;
    *conversion_time_avg_ms = (*conversions_completed > 0)
        ? (snapshot->conversion_time_sum_ms / *conversions_completed)
        : 0;
    *input_bytes_avg = (snapshot->conversions_succeeded > 0)
        ? (snapshot->input_bytes / snapshot->conversions_succeeded)
        : 0;
    *output_bytes_avg = (snapshot->conversions_succeeded > 0)
        ? (snapshot->output_bytes / snapshot->conversions_succeeded)
        : 0;
}

/**
 * Render the collected metrics snapshot as a JSON object into the provided buffer.
 *
 * Formats all metric counters, derived aggregates (conversion counts, average times,
 * average I/O), conversion latency buckets, decompression stats, and path-routing hits
 * as a single JSON object written starting at `p` and not past `end`.
 *
 * @param p Pointer to the start position in the buffer where JSON will be written.
 * @param end Pointer to one past the end of the buffer; writing will not exceed this.
 * @param snapshot Pointer to the metrics snapshot containing raw counters to emit.
 * @param conversions_completed Derived total of completed conversions (succeeded + failed).
 * @param conversion_time_avg_ms Derived average conversion time in milliseconds.
 * @param input_bytes_avg Derived average input bytes per successful conversion.
 * @param output_bytes_avg Derived average output bytes per successful conversion.
 * @returns Pointer to the buffer position immediately after the last byte written.
 */
static u_char *
ngx_http_markdown_metrics_write_json(
    u_char *p,
    u_char *end,
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_atomic_uint_t conversions_completed,
    ngx_atomic_uint_t conversion_time_avg_ms,
    ngx_atomic_uint_t input_bytes_avg,
    ngx_atomic_uint_t output_bytes_avg)
{
    return ngx_slprintf(p, end,
        "{\n"
        "  \"conversions_attempted\": %uA,\n"
        "  \"conversions_succeeded\": %uA,\n"
        "  \"conversions_failed\": %uA,\n"
        "  \"conversions_bypassed\": %uA,\n"
        "  \"failures_conversion\": %uA,\n"
        "  \"failures_resource_limit\": %uA,\n"
        "  \"failures_system\": %uA,\n"
        "  \"conversion_time_sum_ms\": %uA,\n"
        "  \"conversion_completed\": %uA,\n"
        "  \"conversion_time_avg_ms\": %uA,\n"
        "  \"input_bytes\": %uA,\n"
        "  \"input_bytes_avg\": %uA,\n"
        "  \"output_bytes\": %uA,\n"
        "  \"output_bytes_avg\": %uA,\n"
        "  \"conversion_latency_buckets\": {\n"
        "    \"le_10ms\": %uA,\n"
        "    \"le_100ms\": %uA,\n"
        "    \"le_1000ms\": %uA,\n"
        "    \"gt_1000ms\": %uA\n"
        "  },\n"
        "  \"decompressions_attempted\": %uA,\n"
        "  \"decompressions_succeeded\": %uA,\n"
        "  \"decompressions_failed\": %uA,\n"
        "  \"decompressions_gzip\": %uA,\n"
        "  \"decompressions_deflate\": %uA,\n"
        "  \"decompressions_brotli\": %uA,\n"
        "  \"fullbuffer_path_hits\": %uA,\n"
        "  \"incremental_path_hits\": %uA\n"
        "}",
        snapshot->conversions_attempted,
        snapshot->conversions_succeeded,
        snapshot->conversions_failed,
        snapshot->conversions_bypassed,
        snapshot->failures_conversion,
        snapshot->failures_resource_limit,
        snapshot->failures_system,
        snapshot->conversion_time_sum_ms,
        conversions_completed,
        conversion_time_avg_ms,
        snapshot->input_bytes,
        input_bytes_avg,
        snapshot->output_bytes,
        output_bytes_avg,
        snapshot->conversion_latency_le_10ms,
        snapshot->conversion_latency_le_100ms,
        snapshot->conversion_latency_le_1000ms,
        snapshot->conversion_latency_gt_1000ms,
        snapshot->decompressions.attempted,
        snapshot->decompressions.succeeded,
        snapshot->decompressions.failed,
        snapshot->decompressions.gzip,
        snapshot->decompressions.deflate,
        snapshot->decompressions.brotli,
        snapshot->fullbuffer_path_hits,
        snapshot->incremental_path_hits);
}

/**
 * Format a metrics snapshot as a human-readable plain-text report into the provided buffer.
 *
 * @param p Pointer to the current write position in the buffer.
 * @param end Pointer to the end of the buffer (one past the last writable byte).
 * @param snapshot Snapshot of atomic metrics to render.
 * @param conversions_completed Total conversions completed (succeeded + failed).
 * @param conversion_time_avg_ms Average conversion time in milliseconds.
 * @param input_bytes_avg Average input size in bytes per successful conversion.
 * @param output_bytes_avg Average output size in bytes per successful conversion.
 * @returns Pointer to the buffer position immediately after the written data; if the buffer was too small the pointer will be equal to `end`.
 */
static u_char *
ngx_http_markdown_metrics_write_text(
    u_char *p,
    u_char *end,
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_atomic_uint_t conversions_completed,
    ngx_atomic_uint_t conversion_time_avg_ms,
    ngx_atomic_uint_t input_bytes_avg,
    ngx_atomic_uint_t output_bytes_avg)
{
    return ngx_slprintf(p, end,
        "Markdown Filter Metrics\n"
        "=======================\n"
        "Conversions Attempted: %uA\n"
        "Conversions Succeeded: %uA\n"
        "Conversions Failed: %uA\n"
        "Conversions Bypassed: %uA\n"
        "Conversions Completed: %uA\n"
        "\n"
        "Failure Breakdown:\n"
        "- Conversion Errors: %uA\n"
        "- Resource Limit Exceeded: %uA\n"
        "- System Errors: %uA\n"
        "\n"
        "Performance:\n"
        "- Total Conversion Time: %uA ms\n"
        "- Average Conversion Time: %uA ms\n"
        "- Total Input Bytes: %uA\n"
        "- Average Input Bytes: %uA\n"
        "- Total Output Bytes: %uA\n"
        "- Average Output Bytes: %uA\n"
        "- Latency <= 10ms: %uA\n"
        "- Latency <= 100ms: %uA\n"
        "- Latency <= 1000ms: %uA\n"
        "- Latency > 1000ms: %uA\n"
        "\n"
        "Decompression Statistics:\n"
        "- Decompressions Attempted: %uA\n"
        "- Decompressions Succeeded: %uA\n"
        "- Decompressions Failed: %uA\n"
        "- Gzip Decompressions: %uA\n"
        "- Deflate Decompressions: %uA\n"
        "- Brotli Decompressions: %uA\n"
        "\n"
        "Path Routing:\n"
        "- Full-Buffer Path Hits: %uA\n"
        "- Incremental Path Hits: %uA\n",
        snapshot->conversions_attempted,
        snapshot->conversions_succeeded,
        snapshot->conversions_failed,
        snapshot->conversions_bypassed,
        conversions_completed,
        snapshot->failures_conversion,
        snapshot->failures_resource_limit,
        snapshot->failures_system,
        snapshot->conversion_time_sum_ms,
        conversion_time_avg_ms,
        snapshot->input_bytes,
        input_bytes_avg,
        snapshot->output_bytes,
        output_bytes_avg,
        snapshot->conversion_latency_le_10ms,
        snapshot->conversion_latency_le_100ms,
        snapshot->conversion_latency_le_1000ms,
        snapshot->conversion_latency_gt_1000ms,
        snapshot->decompressions.attempted,
        snapshot->decompressions.succeeded,
        snapshot->decompressions.failed,
        snapshot->decompressions.gzip,
        snapshot->decompressions.deflate,
        snapshot->decompressions.brotli,
        snapshot->fullbuffer_path_hits,
        snapshot->incremental_path_hits);
}

/*
 * HTTP handler for the /markdown_metrics endpoint.
 *
 * - Only responds to GET and HEAD requests; other methods are rejected with
 *   NGX_HTTP_NOT_ALLOWED.
 * - Access is restricted to localhost: IPv4 and (when enabled) IPv6 source
 *   addresses are checked and non-local clients receive NGX_HTTP_FORBIDDEN.
 * - Collects a best-effort snapshot of the shared markdown metrics counters
 *   via ngx_http_markdown_collect_metrics_snapshot() and derives aggregate
 *   values (such as averages) from that snapshot.
 * - Serializes the resulting metrics into an in-memory buffer in either a
 *   plain-text or JSON format, depending on the request, and sends the data
 *   as the response body.
 *
 * The function is intentionally self-contained so that metrics formatting and
 * access-control policy can evolve without impacting the main filter logic.
 */
static ngx_int_t
ngx_http_markdown_metrics_handler(ngx_http_request_t *r)
{
    ngx_int_t                             rc;
    ngx_buf_t                            *b;
    ngx_chain_t                           out;
    u_char                               *p;
    size_t                                len;
    ngx_flag_t                            json_format;
    ngx_http_markdown_metrics_snapshot_t  snapshot;
    ngx_atomic_uint_t                     conversions_completed;
    ngx_atomic_uint_t                     conversion_time_avg_ms;
    ngx_atomic_uint_t                     input_bytes_avg;
    ngx_atomic_uint_t                     output_bytes_avg;

    rc = ngx_http_markdown_metrics_validate_request(r);
    if (rc != NGX_OK) {
        return rc;
    }

    json_format = ngx_http_markdown_metrics_prefers_json(r);

    rc = ngx_http_discard_request_body(r);
    if (rc != NGX_OK) {
        return rc;
    }

    ngx_http_markdown_collect_metrics_snapshot(&snapshot);
    ngx_http_markdown_metrics_derive_values(&snapshot,
                                            &conversions_completed,
                                            &conversion_time_avg_ms,
                                            &input_bytes_avg,
                                            &output_bytes_avg);

    b = ngx_create_temp_buf(r->pool, NGX_HTTP_MARKDOWN_METRICS_BUF_SIZE);
    if (b == NULL) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown_metrics: failed to allocate response buffer");
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    p = b->pos;
    if (json_format) {
        p = ngx_http_markdown_metrics_write_json(p, b->end,
                                                 &snapshot,
                                                 conversions_completed,
                                                 conversion_time_avg_ms,
                                                 input_bytes_avg,
                                                 output_bytes_avg);
    } else {
        p = ngx_http_markdown_metrics_write_text(p, b->end,
                                                 &snapshot,
                                                 conversions_completed,
                                                 conversion_time_avg_ms,
                                                 input_bytes_avg,
                                                 output_bytes_avg);
    }

    len = p - b->pos;
    b->last = p;

    r->headers_out.status = NGX_HTTP_OK;
    r->headers_out.content_length_n = len;

    if (json_format) {
        ngx_str_set(&r->headers_out.content_type, "application/json");
    } else {
        ngx_str_set(&r->headers_out.content_type, "text/plain");
    }
    r->headers_out.content_type_len = r->headers_out.content_type.len;

    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;

    rc = ngx_http_send_header(r);
    if (rc == NGX_ERROR || rc > NGX_OK || r->header_only) {
        return rc;
    }

    out.buf = b;
    out.next = NULL;
    return ngx_http_output_filter(r, &out);
}

#endif /* NGX_HTTP_MARKDOWN_METRICS_IMPL_H */
