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
 * The latency histogram, decompression counters, path-hit counters,
 * skip counters, and per-path counters are each grouped into
 * anonymous sub-structs to keep the top-level field count within
 * SonarCloud's 20-field limit while preserving a flat JSON output
 * format.
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

    /* Latency histogram buckets (grouped to keep top-level field count <= 20) */
    struct {
        ngx_atomic_uint_t le_10ms;
        ngx_atomic_uint_t le_100ms;
        ngx_atomic_uint_t le_1000ms;
        ngx_atomic_uint_t gt_1000ms;
    } conversion_latency;

    /* Decompression metrics (grouped to keep top-level field count <= 20) */
    struct {
        ngx_atomic_uint_t attempted;
        ngx_atomic_uint_t succeeded;
        ngx_atomic_uint_t failed;
        ngx_atomic_uint_t gzip;
        ngx_atomic_uint_t deflate;
        ngx_atomic_uint_t brotli;
        ngx_atomic_uint_t budget_exceeded_total;
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

    /* Conversion result counters */
    struct {
        ngx_atomic_uint_t failopen_count;
        ngx_atomic_uint_t delivery_count;
        ngx_atomic_uint_t decision_count;
        ngx_atomic_uint_t estimated_token_savings;
    } results;

    /* Parse interrupt metrics (v0.7.0) */
    struct {
        ngx_atomic_uint_t parse_timeouts_total;
        ngx_atomic_uint_t parse_budget_exceeded_total;
    } parse_interrupts;

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
        ngx_atomic_uint_t budget_exceeded_total;
        ngx_atomic_uint_t shadow_total;
        ngx_atomic_uint_t shadow_diff_total;
        ngx_atomic_uint_t last_ttfb_ms;
        ngx_atomic_uint_t last_peak_memory_bytes;
    } streaming;
#endif

    /* Per-path metrics (v0.6.0 P1-2) */
    struct {
        ngx_atomic_uint_t path_entries;
        ngx_atomic_uint_t path_conversions;
        ngx_atomic_uint_t path_conversion_time_sum_ms;
        ngx_atomic_uint_t overflow_count;
    } per_path;
} ngx_http_markdown_metrics_snapshot_t;

typedef struct {
    ngx_atomic_uint_t conversions_completed;
    ngx_atomic_uint_t conversion_time_avg_ms;
    ngx_atomic_uint_t input_bytes_avg;
    ngx_atomic_uint_t output_bytes_avg;
} ngx_http_markdown_metrics_derived_t;

#ifndef ngx_str_set
#define ngx_str_set(str, text)                                                    \
    do {                                                                          \
        (str)->len = sizeof(text) - 1;                                            \
        (str)->data = (u_char *) text;                                            \
    } while (0)
#endif

/* C99 declaration visibility for standalone static analysis of this impl header. */
#ifndef ngx_memzero
void ngx_memzero(void *buf, size_t n);
#endif
u_char *ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...);
ngx_int_t ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *out);
ngx_int_t ngx_strncasecmp(u_char *s1, u_char *s2, size_t n);

/*
 * Response buffer size for the metrics endpoint.
 *
 * Estimated current output per format (without per-path entries):
 *   JSON:       ~2.0 KiB
 *   Plain text: ~1.5 KiB
 *   Prometheus: ~3.8 KiB (most verbose due to HELP/TYPE lines)
 *
 * Per-path entries add variable output depending on the number of
 * tracked paths and their URI lengths.  Each path entry is roughly
 * 80-120 bytes across formats.  With a default cardinality limit
 * of 1024 paths at ~100 bytes each, per-path output can reach
 * ~100 KiB.
 *
 * The 128 KiB buffer accommodates aggregate output plus per-path
 * entries for typical deployments.  If the buffer is exhausted,
 * the renderers detect truncation and return an error.
 */
#define NGX_HTTP_MARKDOWN_METRICS_BUF_SIZE  131072

/*
 * Forward declaration: the Prometheus renderer is defined in
 * ngx_http_markdown_prometheus_impl.h but called from the
 * metrics handler below.  Declared here so the call site
 * compiles before the definition is included.
 */
static u_char *
ngx_http_markdown_metrics_write_prometheus(
    u_char *p, u_char *end,
    const ngx_http_markdown_metrics_snapshot_t *snapshot);
static u_char *
ngx_http_markdown_metrics_render_response_body(
    ngx_http_request_t *r,
    ngx_buf_t *b,
    ngx_uint_t format,
    const ngx_http_markdown_metrics_snapshot_t *snapshot,
    const ngx_http_markdown_metrics_derived_t *derived);
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
    const ngx_http_markdown_metrics_t *metrics;

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
    snapshot->conversion_latency.le_10ms =
        metrics->conversion_latency.le_10ms;
    snapshot->conversion_latency.le_100ms =
        metrics->conversion_latency.le_100ms;
    snapshot->conversion_latency.le_1000ms =
        metrics->conversion_latency.le_1000ms;
    snapshot->conversion_latency.gt_1000ms =
        metrics->conversion_latency.gt_1000ms;
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
    snapshot->results.failopen_count = metrics->results.failopen_count;
    snapshot->results.delivery_count = metrics->results.delivery_count;
    snapshot->results.decision_count = metrics->results.decision_count;
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
    snapshot->streaming.budget_exceeded_total =
        metrics->streaming.budget_exceeded_total;
    snapshot->streaming.shadow_total =
        metrics->streaming.shadow_total;
    snapshot->streaming.shadow_diff_total =
        metrics->streaming.shadow_diff_total;
    snapshot->streaming.last_ttfb_ms =
        metrics->streaming.last_ttfb_ms;
    snapshot->streaming.last_peak_memory_bytes =
        metrics->streaming.last_peak_memory_bytes;
#endif
    snapshot->results.estimated_token_savings = metrics->results.estimated_token_savings;

    snapshot->parse_interrupts.parse_timeouts_total =
        metrics->parse_interrupts.parse_timeouts_total;
    snapshot->parse_interrupts.parse_budget_exceeded_total =
        metrics->parse_interrupts.parse_budget_exceeded_total;

    snapshot->decompressions.budget_exceeded_total =
        metrics->decompressions.budget_exceeded_total;

    snapshot->per_path.path_entries =
        metrics->per_path.path_entries;
    snapshot->per_path.path_conversions =
        metrics->per_path.path_conversions;
    snapshot->per_path.path_conversion_time_sum_ms =
        metrics->per_path.path_conversion_time_sum_ms;
    snapshot->per_path.overflow_count =
        metrics->per_path.overflow_count;
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
        const struct sockaddr_in *sin =
            (const struct sockaddr_in *) r->connection->sockaddr;
        if (ntohl(sin->sin_addr.s_addr) != INADDR_LOOPBACK) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown_metrics: access denied from non-localhost IPv4 address");
            return NGX_HTTP_FORBIDDEN;
        }
    }
#if (NGX_HAVE_INET6)
    else if (r->connection->sockaddr->sa_family == AF_INET6) {
        const struct sockaddr_in6 *sin6 =
            (const struct sockaddr_in6 *) r->connection->sockaddr;
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

/*
 * Case-insensitive substring search within an ngx_str_t.
 *
 * Scans `value` for the first occurrence of `needle` using
 * ngx_strncasecmp for each candidate position.  Returns 1 if
 * found, 0 otherwise.
 *
 * Parameters:
 *   value       - string to search within (may be NULL)
 *   needle      - substring to find (may be NULL)
 *   needle_len  - length of needle in bytes
 *
 * Returns:
 *   1 if needle occurs in value, 0 otherwise.
 */
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
    const ngx_http_markdown_conf_t  *conf;

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
    const ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_http_markdown_metrics_derived_t *derived)
{
    derived->conversions_completed =
        snapshot->conversions_succeeded + snapshot->conversions_failed;
    derived->conversion_time_avg_ms = (derived->conversions_completed > 0)
        ? (snapshot->conversion_time_sum_ms / derived->conversions_completed)
        : 0;
    derived->input_bytes_avg = (snapshot->conversions_succeeded > 0)
        ? (snapshot->input_bytes / snapshot->conversions_succeeded)
        : 0;
    derived->output_bytes_avg = (snapshot->conversions_succeeded > 0)
        ? (snapshot->output_bytes / snapshot->conversions_succeeded)
        : 0;
}

/*
 * Per-path RB-tree walk enable macro and forward declarations.
 *
 * These must be at file scope because C rejects block-scope
 * function declarations with static storage class.  Unit tests
 * that lack full NGINX type definitions define the macro to 0
 * before including this header.
 */
#ifndef NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
#define NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED  1
#endif

#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
static u_char *
ngx_http_markdown_json_walk_path_tree(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    u_char *p,
    u_char *end);

static u_char *
ngx_http_markdown_text_walk_path_tree(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    u_char *p,
    u_char *end);
#endif

/**
 * Render the collected metrics snapshot as a JSON object into the provided buffer.
 *
 * Formats all metric counters, derived aggregates (conversion counts, average times,
 * average I/O), conversion latency buckets, decompression stats, and path-routing hits
 * as a JSON object written starting at `p` and not past `end`.
 *
 * When per-path tracking is active, walks the SHM RB-tree under the slab pool
 * mutex to emit individual per-path entries in a "paths" array inside the
 * "per_path" object.
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
    const ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_atomic_uint_t conversions_completed,
    ngx_atomic_uint_t conversion_time_avg_ms,
    ngx_atomic_uint_t input_bytes_avg,
    ngx_atomic_uint_t output_bytes_avg)
{
    /*
     * Emit the metrics payload as a JSON object.
     *
     * The format string covers everything up to and including the
     * per_path aggregate counters.  After the aggregate section,
     * if per-path tracking is active, we walk the SHM RB-tree
     * to emit individual path entries, then close the object.
     *
     * The caller is responsible for detecting truncation (p >= end)
     * after this function returns.
     */
    p = ngx_slprintf(p, end,

        /* Conversion attempt and outcome counters */
        "{\n"
        "  \"conversions_attempted\": %uA,\n"
        "  \"conversions_succeeded\": %uA,\n"
        "  \"conversions_failed\": %uA,\n"
        "  \"conversions_bypassed\": %uA,\n"

        /* Failure classification by root cause */
        "  \"failures_conversion\": %uA,\n"
        "  \"failures_resource_limit\": %uA,\n"
        "  \"failures_system\": %uA,\n"

        /* Performance: timing and I/O volume (raw + derived averages) */
        "  \"conversion_time_sum_ms\": %uA,\n"
        "  \"conversion_completed\": %uA,\n"
        "  \"conversion_time_avg_ms\": %uA,\n"
        "  \"input_bytes\": %uA,\n"
        "  \"input_bytes_avg\": %uA,\n"
        "  \"output_bytes\": %uA,\n"
        "  \"output_bytes_avg\": %uA,\n"

        /* Latency histogram buckets (cumulative, Prometheus-style) */
        "  \"conversion_latency_buckets\": {\n"
        "    \"le_10ms\": %uA,\n"
        "    \"le_100ms\": %uA,\n"
        "    \"le_1000ms\": %uA,\n"
        "    \"gt_1000ms\": %uA\n"
        "  },\n"

        /* Decompression statistics by algorithm */
        "  \"decompressions_attempted\": %uA,\n"
        "  \"decompressions_succeeded\": %uA,\n"
        "  \"decompressions_failed\": %uA,\n"
        "  \"decompressions_gzip\": %uA,\n"
        "  \"decompressions_deflate\": %uA,\n"
        "  \"decompressions_brotli\": %uA,\n"
        "  \"decompression_budget_exceeded_total\": %uA,\n"

        /* Threshold-router path hit counters */
        "  \"fullbuffer_path_hits\": %uA,\n"
        "  \"incremental_path_hits\": %uA,\n"
#ifdef MARKDOWN_STREAMING_ENABLED

        /* Streaming engine counters (feature-gated) */
        "  \"streaming_path_hits\": %uA,\n"
        "  \"streaming\": {\n"
        "    \"requests_total\": %uA,\n"
        "    \"fallback_total\": %uA,\n"
        "    \"succeeded_total\": %uA,\n"
        "    \"failed_total\": %uA,\n"
        "    \"postcommit_error_total\": %uA,\n"
        "    \"precommit_failopen_total\": %uA,\n"
        "    \"precommit_reject_total\": %uA,\n"
        "    \"budget_exceeded_total\": %uA,\n"
        "    \"shadow_total\": %uA,\n"
        "    \"shadow_diff_total\": %uA,\n"
        "    \"last_ttfb_ms\": %uA,\n"
        "    \"last_peak_memory_bytes\": %uA\n"
        "  },\n"
#endif

        /* Decision-chain entry count and per-reason skip counters */
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

        /* Operational totals and per-path aggregate counters */
        "  \"failopen_count\": %uA,\n"
        "  \"delivery_count\": %uA,\n"
        "  \"decision_count\": %uA,\n"
        "  \"estimated_token_savings\": %uA,\n"
        "  \"parse_timeouts_total\": %uA,\n"
        "  \"parse_budget_exceeded_total\": %uA,\n"
        "  \"per_path\": {\n"
        "    \"path_entries\": %uA,\n"
        "    \"path_conversions\": %uA,\n"
        "    \"path_conversion_time_sum_ms\": %uA,\n"
        "    \"overflow_count\": %uA,\n"
        "    \"paths\": [",

        /* Conversion counters */
        snapshot->conversions_attempted,
        snapshot->conversions_succeeded,
        snapshot->conversions_failed,
        snapshot->conversions_bypassed,

        /* Failure classification */
        snapshot->failures_conversion,
        snapshot->failures_resource_limit,
        snapshot->failures_system,

        /* Performance aggregates */
        snapshot->conversion_time_sum_ms,
        conversions_completed,
        conversion_time_avg_ms,
        snapshot->input_bytes,
        input_bytes_avg,
        snapshot->output_bytes,
        output_bytes_avg,

        /* Latency histogram */
        snapshot->conversion_latency.le_10ms,
        snapshot->conversion_latency.le_100ms,
        snapshot->conversion_latency.le_1000ms,
        snapshot->conversion_latency.gt_1000ms,

        /* Decompression stats */
        snapshot->decompressions.attempted,
        snapshot->decompressions.succeeded,
        snapshot->decompressions.failed,
        snapshot->decompressions.gzip,
        snapshot->decompressions.deflate,
        snapshot->decompressions.brotli,
        snapshot->decompressions.budget_exceeded_total,

        /* Path routing */
        snapshot->path_hits.fullbuffer,
        snapshot->path_hits.incremental,
#ifdef MARKDOWN_STREAMING_ENABLED

        /* Streaming counters */
        snapshot->path_hits.streaming,
        snapshot->streaming.requests_total,
        snapshot->streaming.fallback_total,
        snapshot->streaming.succeeded_total,
        snapshot->streaming.failed_total,
        snapshot->streaming.postcommit_error_total,
        snapshot->streaming.precommit_failopen_total,
        snapshot->streaming.precommit_reject_total,
        snapshot->streaming.budget_exceeded_total,
        snapshot->streaming.shadow_total,
        snapshot->streaming.shadow_diff_total,
        snapshot->streaming.last_ttfb_ms,
        snapshot->streaming.last_peak_memory_bytes,
#endif

        /* Decision chain and operational totals */
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
        snapshot->results.failopen_count,
        snapshot->results.delivery_count,
        snapshot->results.decision_count,
        snapshot->results.estimated_token_savings,
        snapshot->parse_interrupts.parse_timeouts_total,
        snapshot->parse_interrupts.parse_budget_exceeded_total,
        snapshot->per_path.path_entries,
        snapshot->per_path.path_conversions,
        snapshot->per_path.path_conversion_time_sum_ms,
        snapshot->per_path.overflow_count);

    /*
     * Per-path individual entries: walk the SHM RB-tree to emit
     * each path as a JSON object inside the "paths" array.
     *
     * The walk emits a trailing comma after each entry.
     * We strip the trailing comma before closing the array
     * by backing up one byte if paths were emitted.
     *
     * Requires full NGINX type definitions; guarded by
     * NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED.
     */
#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
    if (snapshot->per_path.path_entries > 0
        && ngx_http_markdown_metrics_shm_zone != NULL
        && ngx_http_markdown_metrics_shm_zone->data != NULL)
    {
        ngx_shm_zone_t                       *zone;
        ngx_slab_pool_t                      *shpool;
        ngx_http_markdown_metrics_t          *live_metrics;
        const u_char                         *paths_start;

        zone = ngx_http_markdown_metrics_shm_zone;
        live_metrics = (ngx_http_markdown_metrics_t *) zone->data;
        shpool = (ngx_slab_pool_t *) zone->shm.addr;

        paths_start = p;

        ngx_shmtx_lock(&shpool->mutex);

        p = ngx_http_markdown_json_walk_path_tree(
                live_metrics->per_path.path_tree.root,
                &live_metrics->per_path.sentinel,
                p, end);

        ngx_shmtx_unlock(&shpool->mutex);

        /*
         * Strip the trailing comma left by the last entry.
         * If paths were written, p > paths_start and the byte
         * before p is ','.
         */
        if (p > paths_start && *(p - 1) == ',') {
            p--;
        }

        /*
         * Emit the __other__ pseudo-path entry for overflow paths
         * that were dropped when the cardinality limit was reached.
         * This allows operators to see the count of untracked paths
         * without enumerating them.
         */
        if (snapshot->per_path.overflow_count > 0 && p < end) {
            if (p > paths_start) {
                p = ngx_slprintf(p, end, ",");
            }
            p = ngx_slprintf(p, end,
                "\n"
                "      {\"path\":\"__other__\","
                "\"conversions\":%uA,"
                "\"conversion_time_sum_ms\":0,"
                "\"entries\":%uA}",
                snapshot->per_path.overflow_count,
                snapshot->per_path.overflow_count);
        }
    }
#endif /* NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED */

    /* Close the "paths" array, the "per_path" object, and the root object. */
    p = ngx_slprintf(p, end,
        "\n"
        "    ]\n"
        "  }\n"
        "}");

    return p;
}


/**
 * Format a metrics snapshot as a human-readable plain-text report into the provided buffer.
 *
 * When per-path tracking is active, walks the SHM RB-tree under the slab
 * pool mutex to emit individual per-path entries after the aggregate section.
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
    const ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_atomic_uint_t conversions_completed,
    ngx_atomic_uint_t conversion_time_avg_ms,
    ngx_atomic_uint_t input_bytes_avg,
    ngx_atomic_uint_t output_bytes_avg)
{
    /*
     * Emit the full metrics payload as a human-readable plain-text
     * report.  Sections mirror the JSON renderer: conversion counters,
     * failure breakdown, performance aggregates with latency histogram,
     * decompression stats, path routing, streaming counters, and
     * decision-chain skip reasons.
     *
     * After the aggregate per-path section, if per-path tracking is
     * active, walk the SHM RB-tree to emit individual per-path entries.
     *
     * The caller is responsible for detecting truncation (p >= end)
     * after this function returns.
     */
    p = ngx_slprintf(p, end,

        /* Header and conversion outcome summary */
        "Markdown Filter Metrics\n"
        "=======================\n"
        "Conversions Attempted: %uA\n"
        "Conversions Succeeded: %uA\n"
        "Conversions Failed: %uA\n"
        "Conversions Bypassed: %uA\n"
        "Conversions Completed: %uA\n"
        "\n"

        /* Failure classification by root cause */
        "Failure Breakdown:\n"
        "- Conversion Errors: %uA\n"
        "- Resource Limit Exceeded: %uA\n"
        "- System Errors: %uA\n"
        "\n"

        /* Performance: timing, I/O volume, and latency histogram */
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

        /* Decompression statistics by algorithm */
        "Decompression Statistics:\n"
        "- Decompressions Attempted: %uA\n"
        "- Decompressions Succeeded: %uA\n"
        "- Decompressions Failed: %uA\n"
        "- Gzip Decompressions: %uA\n"
        "- Deflate Decompressions: %uA\n"
        "- Brotli Decompressions: %uA\n"
        "\n"

        /* Threshold-router path hit counters */
        "Path Routing:\n"
        "- Full-Buffer Path Hits: %uA\n"
        "- Incremental Path Hits: %uA\n"
#ifdef MARKDOWN_STREAMING_ENABLED

        /* Streaming engine counters (feature-gated) */
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
        "- Streaming Budget Exceeded: %uA\n"
        "- Streaming Shadow Total: %uA\n"
        "- Streaming Shadow Diff Total: %uA\n"
        "- Streaming Last TTFB (ms): %uA\n"
        "- Streaming Peak Memory (bytes): %uA\n"
#endif
        "\n"

        /* Decision-chain entry count and per-reason skip counters */
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
        "- Estimated Token Savings: %uA\n"
        "- Per-Path Entries: %uA\n"
        "- Per-Path Conversions: %uA\n"
        "- Per-Path Conversion Time (ms): %uA\n"
        "- Per-Path Overflow Count: %uA\n",

        /* Conversion counters */
        snapshot->conversions_attempted,
        snapshot->conversions_succeeded,
        snapshot->conversions_failed,
        snapshot->conversions_bypassed,
        conversions_completed,

        /* Failure classification */
        snapshot->failures_conversion,
        snapshot->failures_resource_limit,
        snapshot->failures_system,

        /* Performance aggregates */
        snapshot->conversion_time_sum_ms,
        conversion_time_avg_ms,
        snapshot->input_bytes,
        input_bytes_avg,
        snapshot->output_bytes,
        output_bytes_avg,

        /* Latency histogram */
        snapshot->conversion_latency.le_10ms,
        snapshot->conversion_latency.le_100ms,
        snapshot->conversion_latency.le_1000ms,
        snapshot->conversion_latency.gt_1000ms,

        /* Decompression stats */
        snapshot->decompressions.attempted,
        snapshot->decompressions.succeeded,
        snapshot->decompressions.failed,
        snapshot->decompressions.gzip,
        snapshot->decompressions.deflate,
        snapshot->decompressions.brotli,
        snapshot->decompressions.budget_exceeded_total,

        /* Path routing */
        snapshot->path_hits.fullbuffer,
        snapshot->path_hits.incremental,
#ifdef MARKDOWN_STREAMING_ENABLED

        /* Streaming counters */
        snapshot->path_hits.streaming,
        snapshot->streaming.requests_total,
        snapshot->streaming.fallback_total,
        snapshot->streaming.succeeded_total,
        snapshot->streaming.failed_total,
        snapshot->streaming.postcommit_error_total,
        snapshot->streaming.precommit_failopen_total,
        snapshot->streaming.precommit_reject_total,
        snapshot->streaming.budget_exceeded_total,
        snapshot->streaming.shadow_total,
        snapshot->streaming.shadow_diff_total,
        snapshot->streaming.last_ttfb_ms,
        snapshot->streaming.last_peak_memory_bytes,
#endif

        /* Decision chain and operational totals */
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
        snapshot->results.failopen_count,
        snapshot->results.delivery_count,
        snapshot->results.decision_count,
        snapshot->results.estimated_token_savings,
        snapshot->parse_interrupts.parse_timeouts_total,
        snapshot->parse_interrupts.parse_budget_exceeded_total,
        snapshot->per_path.path_entries,
        snapshot->per_path.path_conversions,
        snapshot->per_path.path_conversion_time_sum_ms,
        snapshot->per_path.overflow_count);

    /*
     * Per-path individual entries: walk the SHM RB-tree to emit
     * each path as a plain-text line after the aggregate section.
     *
     * Requires full NGINX type definitions; guarded by
     * NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED.
     */
#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
    if (snapshot->per_path.path_entries > 0
        && ngx_http_markdown_metrics_shm_zone != NULL
        && ngx_http_markdown_metrics_shm_zone->data != NULL)
    {
        ngx_shm_zone_t                       *zone;
        ngx_slab_pool_t                      *shpool;
        ngx_http_markdown_metrics_t          *live_metrics;

        zone = ngx_http_markdown_metrics_shm_zone;
        live_metrics = (ngx_http_markdown_metrics_t *) zone->data;
        shpool = (ngx_slab_pool_t *) zone->shm.addr;

        p = ngx_slprintf(p, end, "\nPer-Path Details:\n");

        ngx_shmtx_lock(&shpool->mutex);

        p = ngx_http_markdown_text_walk_path_tree(
                live_metrics->per_path.path_tree.root,
                &live_metrics->per_path.sentinel,
                p, end);

        ngx_shmtx_unlock(&shpool->mutex);
    }
#endif /* NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED */

    return p;
}


#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
/*
 * Write a two-character escape sequence (backslash + second char)
 * into the destination buffer.
 *
 * Returns the updated write position, or last if the buffer
 * cannot accommodate two more bytes.
 */
static u_char *
ngx_http_markdown_escape_json_two_char(u_char *dst, u_char *last,
                                       u_char second)
{
    if (dst + 2 > last) {
        return last;
    }

    *dst++ = '\\';
    *dst++ = second;

    return dst;
}


/*
 * Escape a byte string for use inside a JSON string value.
 *
 * JSON requires escaping: " -> \", \ -> \\, control chars (< 0x20) -> \uXXXX.
 *
 * Parameters:
 *   dst  - destination buffer start
 *   last - one past end of destination buffer
 *   src  - source bytes
 *   len  - source length
 *
 * Returns:
 *   Updated write position (clamped to last on overflow).
 */
static u_char *
ngx_http_markdown_escape_json_string(u_char *dst, u_char *last,
                                     const u_char *src, size_t len)
{
    size_t   i;
    u_char   ch;

    i = 0;
    while (i < len && dst < last) {
        ch = src[i];
        i++;

        switch (ch) {
        case '"':
            dst = ngx_http_markdown_escape_json_two_char(dst, last, '"');
            break;
        case '\\':
            dst = ngx_http_markdown_escape_json_two_char(dst, last, '\\');
            break;
        case '\n':
            dst = ngx_http_markdown_escape_json_two_char(dst, last, 'n');
            break;
        case '\r':
            dst = ngx_http_markdown_escape_json_two_char(dst, last, 'r');
            break;
        case '\t':
            dst = ngx_http_markdown_escape_json_two_char(dst, last, 't');
            break;
        default:
            if (ch >= 0x20) {
                *dst++ = ch;
                break;
            }

            /* Control character: emit as \uXXXX */
            if (dst + 6 > last) {
                return last;
            }
            dst = ngx_snprintf(dst, 6, "\\u%04X", (unsigned) ch);
            break;
        }
    }

    return dst;
}


/*
 * Recursive in-order walk of the per-path RB-tree for JSON output.
 *
 * Emits each path as a JSON object inside the "paths" array.
 * The caller is responsible for writing the opening "[" and closing "]".
 * A comma is appended after each entry; the caller strips the trailing
 * comma from the last entry before closing the array.
 *
 * Parameters:
 *   node     - current RB-tree node (or sentinel to terminate)
 *   sentinel - tree sentinel node
 *   p        - current write position in the output buffer
 *   end      - one past end of the buffer
 *
 * Returns:
 *   Updated write position (clamped to end on overflow).
 */
static u_char *
ngx_http_markdown_json_walk_path_tree(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    u_char *p,
    u_char *end)
{
    const ngx_http_markdown_path_metric_node_t  *pnode;

    if (node == sentinel || p >= end) {
        return p;
    }

    p = ngx_http_markdown_json_walk_path_tree(
            node->left, sentinel, p, end);

    if (p < end) {
        pnode = (const ngx_http_markdown_path_metric_node_t *) node;

        p = ngx_slprintf(p, end,
            "\n"
            "      {\"path\": \"");
        p = ngx_http_markdown_escape_json_string(
                p, end, pnode->path, pnode->path_len);
        p = ngx_slprintf(p, end,
            "\", "
            "\"conversions\": %uA, "
            "\"entries\": %uA, "
            "\"conversion_time_ms\": %uA},",
            pnode->conversions,
            pnode->entries,
            pnode->conversion_time_sum_ms);
    }

    p = ngx_http_markdown_json_walk_path_tree(
            node->right, sentinel, p, end);

    return p;
}


/*
 * Recursive in-order walk of the per-path RB-tree for plain-text output.
 *
 * Emits each path as a "- Path[...]: ..." line.
 *
 * Parameters:
 *   node     - current RB-tree node (or sentinel to terminate)
 *   sentinel - tree sentinel node
 *   p        - current write position in the output buffer
 *   end      - one past end of the buffer
 *
 * Returns:
 *   Updated write position (clamped to end on overflow).
 */
static u_char *
ngx_http_markdown_text_walk_path_tree(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    u_char *p,
    u_char *end)
{
    const ngx_http_markdown_path_metric_node_t  *pnode;

    if (node == sentinel || p >= end) {
        return p;
    }

    p = ngx_http_markdown_text_walk_path_tree(
            node->left, sentinel, p, end);

    if (p < end) {
        pnode = (const ngx_http_markdown_path_metric_node_t *) node;

        p = ngx_slprintf(p, end, "- Path[");
        p = ngx_http_markdown_escape_json_string(
                p, end, pnode->path, pnode->path_len);
        p = ngx_slprintf(p, end,
            "]: conversions=%uA entries=%uA "
            "time_ms=%uA\n",
            pnode->conversions,
            pnode->entries,
            pnode->conversion_time_sum_ms);
    }

    p = ngx_http_markdown_text_walk_path_tree(
            node->right, sentinel, p, end);

    return p;
}
#endif /* NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED */


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
    const ngx_http_markdown_metrics_snapshot_t *snapshot,
    const ngx_http_markdown_metrics_derived_t *derived)
{
    u_char  *p;

    p = b->pos;

    switch (format) {

    case NGX_HTTP_MARKDOWN_METRICS_OUTPUT_JSON:
        /* JSON and plain text share the precomputed aggregate values. */
        p = ngx_http_markdown_metrics_write_json(
                p, b->end, snapshot,
                derived->conversions_completed,
                derived->conversion_time_avg_ms,
                derived->input_bytes_avg,
                derived->output_bytes_avg);
        /*
         * Detect truncation: ngx_slprintf returns end when
         * the buffer is exhausted.  Emit a hard failure
         * instead of a partial JSON payload.
         */
        if (p >= b->end) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown_metrics: JSON output "
                "truncated, buffer too small");
            return NULL;
        }
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
                derived->conversions_completed,
                derived->conversion_time_avg_ms,
                derived->input_bytes_avg,
                derived->output_bytes_avg);
        /*
         * Detect truncation: ngx_slprintf returns end when
         * the buffer is exhausted.  Emit a hard failure
         * instead of a partial plain-text payload.
         */
        if (p >= b->end) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown_metrics: plain-text output "
                "truncated, buffer too small");
            return NULL;
        }
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
    ngx_http_markdown_metrics_derived_t   derived;

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
    ngx_http_markdown_metrics_derive_values(&snapshot, &derived);

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
        r, b, format, &snapshot, &derived);
    if (response_end == NULL) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    return ngx_http_markdown_metrics_send_response(
        r, b, response_end);
}

#endif /* NGX_HTTP_MARKDOWN_METRICS_IMPL_H */
