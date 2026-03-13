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

typedef struct {
    ngx_atomic_uint_t conversions_attempted;
    ngx_atomic_uint_t conversions_succeeded;
    ngx_atomic_uint_t conversions_failed;
    ngx_atomic_uint_t conversions_bypassed;
    ngx_atomic_uint_t failures_conversion;
    ngx_atomic_uint_t failures_resource_limit;
    ngx_atomic_uint_t failures_system;
    ngx_atomic_uint_t conversion_time_sum_ms;
    ngx_atomic_uint_t input_bytes;
    ngx_atomic_uint_t output_bytes;
    ngx_atomic_uint_t conversion_latency_le_10ms;
    ngx_atomic_uint_t conversion_latency_le_100ms;
    ngx_atomic_uint_t conversion_latency_le_1000ms;
    ngx_atomic_uint_t conversion_latency_gt_1000ms;
    ngx_atomic_uint_t decompressions_attempted;
    ngx_atomic_uint_t decompressions_succeeded;
    ngx_atomic_uint_t decompressions_failed;
    ngx_atomic_uint_t decompressions_gzip;
    ngx_atomic_uint_t decompressions_deflate;
    ngx_atomic_uint_t decompressions_brotli;
} ngx_http_markdown_metrics_snapshot_t;

/*
 * Response buffer size for the metrics endpoint.  The current JSON/text
 * output is well under 1 KiB, but we leave headroom for future fields.
 * Increase this constant if new metrics push the output beyond the limit.
 */
#define NGX_HTTP_MARKDOWN_METRICS_BUF_SIZE  4096

/*
 * Copy shared atomic counters into a local snapshot struct.
 *
 * This minimizes repeated global reads while formatting the endpoint response
 * and keeps all JSON fields derived from one best-effort capture pass.
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
    snapshot->decompressions_attempted = metrics->decompressions_attempted;
    snapshot->decompressions_succeeded = metrics->decompressions_succeeded;
    snapshot->decompressions_failed = metrics->decompressions_failed;
    snapshot->decompressions_gzip = metrics->decompressions_gzip;
    snapshot->decompressions_deflate = metrics->decompressions_deflate;
    snapshot->decompressions_brotli = metrics->decompressions_brotli;
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

    json_format = 0;
    {
        ngx_table_elt_t *accept_header = NULL;

#if (NGX_HTTP_HEADERS)
        accept_header = r->headers_in.accept;
#else
        static ngx_str_t accept_name = ngx_string("Accept");

        accept_header = ngx_http_markdown_find_request_header(r, &accept_name);
#endif

        if (accept_header != NULL
            && ngx_strstr(accept_header->value.data, "application/json") != NULL)
        {
            json_format = 1;
        }
    }

    rc = ngx_http_discard_request_body(r);
    if (rc != NGX_OK) {
        return rc;
    }

    ngx_http_markdown_collect_metrics_snapshot(&snapshot);
    conversions_completed = snapshot.conversions_succeeded + snapshot.conversions_failed;
    conversion_time_avg_ms = (conversions_completed > 0)
        ? (snapshot.conversion_time_sum_ms / conversions_completed)
        : 0;
    input_bytes_avg = (snapshot.conversions_succeeded > 0)
        ? (snapshot.input_bytes / snapshot.conversions_succeeded)
        : 0;
    output_bytes_avg = (snapshot.conversions_succeeded > 0)
        ? (snapshot.output_bytes / snapshot.conversions_succeeded)
        : 0;

    b = ngx_create_temp_buf(r->pool, NGX_HTTP_MARKDOWN_METRICS_BUF_SIZE);
    if (b == NULL) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown_metrics: failed to allocate response buffer");
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    p = b->pos;
    if (json_format) {
        p = ngx_slprintf(p, b->end,
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
            "  \"decompressions_brotli\": %uA\n"
            "}",
            snapshot.conversions_attempted,
            snapshot.conversions_succeeded,
            snapshot.conversions_failed,
            snapshot.conversions_bypassed,
            snapshot.failures_conversion,
            snapshot.failures_resource_limit,
            snapshot.failures_system,
            snapshot.conversion_time_sum_ms,
            conversions_completed,
            conversion_time_avg_ms,
            snapshot.input_bytes,
            input_bytes_avg,
            snapshot.output_bytes,
            output_bytes_avg,
            snapshot.conversion_latency_le_10ms,
            snapshot.conversion_latency_le_100ms,
            snapshot.conversion_latency_le_1000ms,
            snapshot.conversion_latency_gt_1000ms,
            snapshot.decompressions_attempted,
            snapshot.decompressions_succeeded,
            snapshot.decompressions_failed,
            snapshot.decompressions_gzip,
            snapshot.decompressions_deflate,
            snapshot.decompressions_brotli);
    } else {
        p = ngx_slprintf(p, b->end,
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
            "- Brotli Decompressions: %uA\n",
            snapshot.conversions_attempted,
            snapshot.conversions_succeeded,
            snapshot.conversions_failed,
            snapshot.conversions_bypassed,
            conversions_completed,
            snapshot.failures_conversion,
            snapshot.failures_resource_limit,
            snapshot.failures_system,
            snapshot.conversion_time_sum_ms,
            conversion_time_avg_ms,
            snapshot.input_bytes,
            input_bytes_avg,
            snapshot.output_bytes,
            output_bytes_avg,
            snapshot.conversion_latency_le_10ms,
            snapshot.conversion_latency_le_100ms,
            snapshot.conversion_latency_le_1000ms,
            snapshot.conversion_latency_gt_1000ms,
            snapshot.decompressions_attempted,
            snapshot.decompressions_succeeded,
            snapshot.decompressions_failed,
            snapshot.decompressions_gzip,
            snapshot.decompressions_deflate,
            snapshot.decompressions_brotli);
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
