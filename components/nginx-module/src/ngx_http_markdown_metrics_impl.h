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

    /* Path hit metrics (threshold router, grouped to keep field count <= 20) */
    struct {
        ngx_atomic_uint_t fullbuffer;
        ngx_atomic_uint_t incremental;
#ifdef MARKDOWN_STREAMING_ENABLED
        ngx_atomic_uint_t streaming;
#endif
    } path_hits;

    /* Requests entering the decision chain */
    ngx_atomic_uint_t requests_entered;

    /* Skip counters by reason code */
    struct {
        ngx_atomic_uint_t config;
        ngx_atomic_uint_t method;
        ngx_atomic_uint_t status;
        ngx_atomic_uint_t content_type;
        ngx_atomic_uint_t size;
        ngx_atomic_uint_t streaming;
        ngx_atomic_uint_t auth;
        ngx_atomic_uint_t range;
        ngx_atomic_uint_t accept;
    } skips;

    /* Fail-open counter */
    ngx_atomic_uint_t failopen_count;

#ifdef MARKDOWN_STREAMING_ENABLED
    /* Streaming metrics */
    struct {
        ngx_atomic_uint_t requests_total;
        ngx_atomic_uint_t fallback_total;
        ngx_atomic_uint_t succeeded_total;
        ngx_atomic_uint_t failed_total;
        ngx_atomic_uint_t postcommit_error_total;
        ngx_atomic_uint_t precommit_failopen_total;
        ngx_atomic_uint_t precommit_reject_total;
    } streaming;
#endif

    /* Estimated cumulative token savings */
    ngx_atomic_uint_t estimated_token_savings;
} ngx_http_markdown_metrics_snapshot_t;

/*
 * Response buffer size for the metrics endpoint.
 *
 * Estimated current output per format:
 *   JSON:       ~2.0 KiB
 *   Plain text: ~1.5 KiB
 *   Prometheus: ~3.8 KiB (most verbose due to HELP/TYPE lines)
 *
 * The 6 KiB buffer provides ~2.2 KiB headroom above the largest
 * format.  Increase this constant if new metrics push the
 * Prometheus output beyond the limit.
 */
#define NGX_HTTP_MARKDOWN_METRICS_BUF_SIZE  6144

/*
 * Forward declaration: the Prometheus renderer is defined in
 * ngx_http_markdown_prometheus_impl.h but called from the
 * metrics handler below.  Declared here so the call site
 * compiles before the definition is included.
 */
static u_char *
ngx_http_markdown_metrics_write_prometheus(
    u_char *p, u_char *end,
    ngx_http_markdown_metrics_snapshot_t *snapshot);
static u_char *
ngx_http_markdown_metrics_render_response_body(
    ngx_http_request_t *r,
    ngx_buf_t *b,
    ngx_uint_t format,
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_atomic_uint_t conversions_completed,
    ngx_atomic_uint_t conversion_time_avg_ms,
    ngx_atomic_uint_t input_bytes_avg,
    ngx_atomic_uint_t output_bytes_avg);
static ngx_int_t
ngx_http_markdown_metrics_send_response(
    ngx_http_request_t *r,
    ngx_buf_t *b,
    u_char *response_end);

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
    snapshot->path_hits.fullbuffer = metrics->path_hits.fullbuffer;
    snapshot->path_hits.incremental = metrics->path_hits.incremental;
#ifdef MARKDOWN_STREAMING_ENABLED
    snapshot->path_hits.streaming = metrics->path_hits.streaming;
#endif
    snapshot->requests_entered = metrics->requests_entered;
    snapshot->skips.config = metrics->skips.config;
    snapshot->skips.method = metrics->skips.method;
    snapshot->skips.status = metrics->skips.status;
    snapshot->skips.content_type = metrics->skips.content_type;
    snapshot->skips.size = metrics->skips.size;
    snapshot->skips.streaming = metrics->skips.streaming;
    snapshot->skips.auth = metrics->skips.auth;
    snapshot->skips.range = metrics->skips.range;
    snapshot->skips.accept = metrics->skips.accept;
    snapshot->failopen_count = metrics->failopen_count;
#ifdef MARKDOWN_STREAMING_ENABLED
    snapshot->streaming.requests_total =
        metrics->streaming.requests_total;
    snapshot->streaming.fallback_total =
        metrics->streaming.fallback_total;
    snapshot->streaming.succeeded_total =
        metrics->streaming.succeeded_total;
    snapshot->streaming.failed_total =
        metrics->streaming.failed_total;
    snapshot->streaming.postcommit_error_total =
        metrics->streaming.postcommit_error_total;
    snapshot->streaming.precommit_failopen_total =
        metrics->streaming.precommit_failopen_total;
    snapshot->streaming.precommit_reject_total =
        metrics->streaming.precommit_reject_total;
#endif
    snapshot->estimated_token_savings = metrics->estimated_token_savings;
}

static ngx_flag_t ngx_http_markdown_metrics_value_contains(
    ngx_str_t *value, u_char *needle, size_t needle_len);

static u_char  ngx_http_markdown_metrics_accept_json[] = "application/json";
static u_char  ngx_http_markdown_metrics_accept_openmetrics[] =
    "application/openmetrics-text";
static u_char  ngx_http_markdown_metrics_accept_prom_ver[] =
    "version=0.0.4";
static u_char  ngx_http_markdown_metrics_accept_text_plain[] =
    "text/plain";

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

/*
 * Check whether the Accept header explicitly requests
 * Prometheus exposition format.
 *
 * Matches:
 *   application/openmetrics-text
 *   text/plain; version=0.0.4  (both "text/plain" AND
 *       "version=0.0.4" must be present)
 *
 * Does NOT match:
 *   text/plain  (without version — legacy plain text)
 *   application/xml; version=0.0.4  (wrong media type)
 *   version=0.0.4  (no media type at all)
 */
static ngx_flag_t
ngx_http_markdown_metrics_prefers_prometheus(
    ngx_http_request_t *r)
{
    ngx_table_elt_t  *accept_header;

    accept_header = NULL;

#if (NGX_HTTP_HEADERS)
    accept_header = r->headers_in.accept;
#else
    static ngx_str_t accept_name = ngx_string("Accept");

    accept_header =
        ngx_http_markdown_find_request_header(
            r, &accept_name);
#endif

    if (accept_header == NULL) {
        return 0;
    }

    if (ngx_http_markdown_metrics_value_contains(
            &accept_header->value,
            ngx_http_markdown_metrics_accept_openmetrics,
            sizeof(ngx_http_markdown_metrics_accept_openmetrics) - 1))
    {
        return 1;
    }

    /*
     * Match "text/plain; version=0.0.4" — both substrings
     * must be present to avoid false positives from
     * unrelated media types carrying version=0.0.4.
     */
    if (ngx_http_markdown_metrics_value_contains(
            &accept_header->value,
            ngx_http_markdown_metrics_accept_text_plain,
            sizeof(ngx_http_markdown_metrics_accept_text_plain) - 1)
        && ngx_http_markdown_metrics_value_contains(
            &accept_header->value,
            ngx_http_markdown_metrics_accept_prom_ver,
            sizeof(ngx_http_markdown_metrics_accept_prom_ver) - 1))
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
    if (value == NULL || needle == NULL || needle_len == 0
        || value->len < needle_len)
    {
        return 0;
    }

    for (size_t i = 0; i + needle_len <= value->len; i++) {
        if (ngx_strncasecmp(value->data + i, needle, needle_len)
            == 0)
        {
            return 1;
        }
    }

    return 0;
}

/*
 * Output format constants for the metrics handler.
 *
 * Used by ngx_http_markdown_metrics_select_format() and the
 * three-way dispatch switch in the handler.
 */
#define NGX_HTTP_MARKDOWN_METRICS_OUTPUT_TEXT        0
#define NGX_HTTP_MARKDOWN_METRICS_OUTPUT_JSON        1
#define NGX_HTTP_MARKDOWN_METRICS_OUTPUT_PROMETHEUS  2

/*
 * Select metrics output format based on Accept header and
 * markdown_metrics_format configuration.
 *
 * Content negotiation state machine:
 *
 *   auto       + Accept: application/json     -> JSON
 *   auto       + (any other / none)           -> plain text
 *   prometheus + Accept: application/json     -> JSON
 *   prometheus + Accept: openmetrics-text     -> Prometheus
 *   prometheus + Accept: text/plain; v=0.0.4  -> Prometheus
 *   prometheus + (any other / none)           -> plain text
 *
 * When metrics_format is prometheus, Prometheus format is
 * served only for explicit Prometheus-aware Accept values.
 * Plain "text/plain" without version=0.0.4 falls back to
 * the legacy human-readable text format, preserving
 * backward compatibility for operators who curl without
 * a specific Accept header.
 *
 * Returns one of the NGX_HTTP_MARKDOWN_METRICS_OUTPUT_*
 * constants.
 */
static ngx_uint_t
ngx_http_markdown_metrics_select_format(
    ngx_http_request_t *r)
{
    ngx_http_markdown_conf_t  *conf;

    conf = ngx_http_get_module_loc_conf(
        r, ngx_http_markdown_filter_module);

    if (ngx_http_markdown_metrics_prefers_json(r)) {
        return NGX_HTTP_MARKDOWN_METRICS_OUTPUT_JSON;
    }

    if (conf != NULL
        && conf->ops.metrics_format
           == NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS
        && ngx_http_markdown_metrics_prefers_prometheus(r))
    {
        return NGX_HTTP_MARKDOWN_METRICS_OUTPUT_PROMETHEUS;
    }

    return NGX_HTTP_MARKDOWN_METRICS_OUTPUT_TEXT;
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
        "  \"incremental_path_hits\": %uA,\n"
#ifdef MARKDOWN_STREAMING_ENABLED
        "  \"streaming_path_hits\": %uA,\n"
        "  \"streaming\": {\n"
        "    \"requests_total\": %uA,\n"
        "    \"fallback_total\": %uA,\n"
        "    \"succeeded_total\": %uA,\n"
        "    \"failed_total\": %uA,\n"
        "    \"postcommit_error_total\": %uA,\n"
        "    \"precommit_failopen_total\": %uA,\n"
        "    \"precommit_reject_total\": %uA\n"
        "  },\n"
#endif
        "  \"requests_entered\": %uA,\n"
        "  \"skips\": {\n"
        "    \"config\": %uA,\n"
        "    \"method\": %uA,\n"
        "    \"status\": %uA,\n"
        "    \"content_type\": %uA,\n"
        "    \"size\": %uA,\n"
        "    \"streaming\": %uA,\n"
        "    \"auth\": %uA,\n"
        "    \"range\": %uA,\n"
        "    \"accept\": %uA\n"
        "  },\n"
        "  \"failopen_count\": %uA,\n"
        "  \"estimated_token_savings\": %uA\n"
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
        snapshot->path_hits.fullbuffer,
        snapshot->path_hits.incremental,
#ifdef MARKDOWN_STREAMING_ENABLED
        snapshot->path_hits.streaming,
        snapshot->streaming.requests_total,
        snapshot->streaming.fallback_total,
        snapshot->streaming.succeeded_total,
        snapshot->streaming.failed_total,
        snapshot->streaming.postcommit_error_total,
        snapshot->streaming.precommit_failopen_total,
        snapshot->streaming.precommit_reject_total,
#endif
        snapshot->requests_entered,
        snapshot->skips.config,
        snapshot->skips.method,
        snapshot->skips.status,
        snapshot->skips.content_type,
        snapshot->skips.size,
        snapshot->skips.streaming,
        snapshot->skips.auth,
        snapshot->skips.range,
        snapshot->skips.accept,
        snapshot->failopen_count,
        snapshot->estimated_token_savings);
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
        "- Incremental Path Hits: %uA\n"
#ifdef MARKDOWN_STREAMING_ENABLED
        "- Streaming Path Hits: %uA\n"
        "\n"
        "Streaming:\n"
        "- Streaming Requests Total: %uA\n"
        "- Streaming Fallback Total: %uA\n"
        "- Streaming Succeeded Total: %uA\n"
        "- Streaming Failed Total: %uA\n"
        "- Streaming Post-Commit Errors: %uA\n"
        "- Streaming Pre-Commit Fail-Open: %uA\n"
        "- Streaming Pre-Commit Reject: %uA\n"
#endif
        "\n"
        "Decision Chain:\n"
        "- Requests Entered: %uA\n"
        "- Skips (Config): %uA\n"
        "- Skips (Method): %uA\n"
        "- Skips (Status): %uA\n"
        "- Skips (Content-Type): %uA\n"
        "- Skips (Size): %uA\n"
        "- Skips (Streaming): %uA\n"
        "- Skips (Auth): %uA\n"
        "- Skips (Range): %uA\n"
        "- Skips (Accept): %uA\n"
        "- Fail-Open Count: %uA\n"
        "- Estimated Token Savings: %uA\n",
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
        snapshot->path_hits.fullbuffer,
        snapshot->path_hits.incremental,
#ifdef MARKDOWN_STREAMING_ENABLED
        snapshot->path_hits.streaming,
        snapshot->streaming.requests_total,
        snapshot->streaming.fallback_total,
        snapshot->streaming.succeeded_total,
        snapshot->streaming.failed_total,
        snapshot->streaming.postcommit_error_total,
        snapshot->streaming.precommit_failopen_total,
        snapshot->streaming.precommit_reject_total,
#endif
        snapshot->requests_entered,
        snapshot->skips.config,
        snapshot->skips.method,
        snapshot->skips.status,
        snapshot->skips.content_type,
        snapshot->skips.size,
        snapshot->skips.streaming,
        snapshot->skips.auth,
        snapshot->skips.range,
        snapshot->skips.accept,
        snapshot->failopen_count,
        snapshot->estimated_token_savings);
}

/*
 * Render the metrics response body for the negotiated format and set the
 * matching Content-Type header on the request.
 *
 * Returns the end pointer for the rendered body on success.
 * When Prometheus rendering exhausts the fixed-size buffer, NULL is
 * returned so the caller can fail the request with
 * NGX_HTTP_INTERNAL_SERVER_ERROR.
 */
static u_char *
ngx_http_markdown_metrics_render_response_body(
    ngx_http_request_t *r,
    ngx_buf_t *b,
    ngx_uint_t format,
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_atomic_uint_t conversions_completed,
    ngx_atomic_uint_t conversion_time_avg_ms,
    ngx_atomic_uint_t input_bytes_avg,
    ngx_atomic_uint_t output_bytes_avg)
{
    u_char  *p;

    p = b->pos;

    switch (format) {

    case NGX_HTTP_MARKDOWN_METRICS_OUTPUT_JSON:
        /* JSON and plain text share the precomputed aggregate values. */
        p = ngx_http_markdown_metrics_write_json(
                p, b->end, snapshot,
                conversions_completed,
                conversion_time_avg_ms,
                input_bytes_avg,
                output_bytes_avg);
        ngx_str_set(&r->headers_out.content_type,
                     "application/json");
        return p;

    case NGX_HTTP_MARKDOWN_METRICS_OUTPUT_PROMETHEUS:
        /* Prometheus writer reports truncation explicitly via NULL. */
        p = ngx_http_markdown_metrics_write_prometheus(
                p, b->end, snapshot);
        if (p == NULL) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown_metrics: Prometheus output "
                "truncated, buffer too small");
            return NULL;
        }

        ngx_str_set(&r->headers_out.content_type,
                     "text/plain; version=0.0.4; "
                     "charset=utf-8");
        return p;

    default:
        p = ngx_http_markdown_metrics_write_text(
                p, b->end, snapshot,
                conversions_completed,
                conversion_time_avg_ms,
                input_bytes_avg,
                output_bytes_avg);
        ngx_str_set(&r->headers_out.content_type,
                     "text/plain");
        return p;
    }
}

/*
 * Populate response metadata and stream the prepared metrics buffer
 * back to the client.
 */
static ngx_int_t
ngx_http_markdown_metrics_send_response(
    ngx_http_request_t *r,
    ngx_buf_t *b,
    u_char *response_end)
{
    ngx_int_t    rc;
    ngx_chain_t  out;
    size_t       len;

    len = response_end - b->pos;
    b->last = response_end;

    /* Finalize headers only after the body length is known. */
    r->headers_out.status = NGX_HTTP_OK;
    r->headers_out.content_length_n = len;
    r->headers_out.content_type_len =
        r->headers_out.content_type.len;

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
    u_char                               *response_end;
    ngx_uint_t                            format;
    ngx_http_markdown_metrics_snapshot_t  snapshot;
    ngx_atomic_uint_t                     conversions_completed;
    ngx_atomic_uint_t                     conversion_time_avg_ms;
    ngx_atomic_uint_t                     input_bytes_avg;
    ngx_atomic_uint_t                     output_bytes_avg;

    rc = ngx_http_markdown_metrics_validate_request(r);
    if (rc != NGX_OK) {
        return rc;
    }

    /* Negotiate the response format before discarding any request body. */
    format = ngx_http_markdown_metrics_select_format(r);

    rc = ngx_http_discard_request_body(r);
    if (rc != NGX_OK) {
        return rc;
    }

    /* Take one best-effort snapshot and derive all aggregate values from it. */
    ngx_http_markdown_collect_metrics_snapshot(&snapshot);
    ngx_http_markdown_metrics_derive_values(
        &snapshot,
        &conversions_completed,
        &conversion_time_avg_ms,
        &input_bytes_avg,
        &output_bytes_avg);

    /* Render into a fixed-size temporary buffer before sending headers. */
    b = ngx_create_temp_buf(r->pool,
            NGX_HTTP_MARKDOWN_METRICS_BUF_SIZE);
    if (b == NULL) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
            "markdown_metrics: failed to allocate "
            "response buffer");
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    response_end = ngx_http_markdown_metrics_render_response_body(
        r, b, format, &snapshot,
        conversions_completed,
        conversion_time_avg_ms,
        input_bytes_avg,
        output_bytes_avg);
    if (response_end == NULL) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    return ngx_http_markdown_metrics_send_response(
        r, b, response_end);
}

#endif /* NGX_HTTP_MARKDOWN_METRICS_IMPL_H */
