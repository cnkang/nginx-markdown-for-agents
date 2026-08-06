#ifndef NGX_HTTP_MARKDOWN_METRICS_V1_RENDERER_H
#define NGX_HTTP_MARKDOWN_METRICS_V1_RENDERER_H

/*
 * Metrics v1 Prometheus text 0.0.4 renderer.
 *
 * Emits exactly the 11 frozen metric families defined in the checked-in
 * 0.9.2 metrics registry.
 *
 * This renderer replaces ngx_http_markdown_prometheus_impl.h for the
 * 0.9.2 release. JSON and multi-format support are removed; the only
 * output format is Prometheus text exposition format 0.0.4.
 *
 * WARNING: This header is an implementation detail of the main
 * translation unit (ngx_http_markdown_filter_module.c). It must
 * NOT be included from any other .c file or used as a standalone
 * compilation unit.
 *
 * Family list (frozen, exactly 11):
 *   1. nginx_markdown_requests_total (counter)
 *   2. nginx_markdown_conversion_attempts_total (counter)
 *   3. nginx_markdown_conversion_deliveries_total (counter)
 *   4. nginx_markdown_conversion_duration_seconds (histogram)
 *   5. nginx_markdown_input_bytes_total (counter)
 *   6. nginx_markdown_output_bytes_total (counter)
 *   7. nginx_markdown_inflight_requests (gauge)
 *   8. nginx_markdown_streaming_events_total (counter)
 *   9. nginx_markdown_decompression_events_total (counter)
 *  10. nginx_markdown_dynconf_reloads_total (counter)
 *  11. nginx_markdown_build_info (gauge)
 *
 * No per-path labels. No URI labels. No JSON format. No multi-format.
 * All label sets are bounded and enumerable.
 */

/* C99 declaration visibility for standalone static analysis */
u_char *ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...);

/*
 * Histogram bucket boundaries for conversion_duration_seconds.
 * Exactly 10 buckets (requirement: no more than 10).
 */
#define NGX_HTTP_MARKDOWN_METRICS_V1_BUCKET_COUNT  10

/*
 * v1 metrics snapshot structure.
 *
 * This is the reduced metrics structure that carries exactly the
 * data needed to render the 11 frozen families. The existing
 * ngx_http_markdown_metrics_snapshot_t remains as an internal storage
 * shape for counter aggregation; it is not a public renderer or wire
 * contract. The v1 renderer reads from this v1 snapshot.
 */
typedef struct {
    ngx_atomic_uint_t buckets[NGX_HTTP_MARKDOWN_METRICS_V1_BUCKET_COUNT];
    ngx_atomic_uint_t sum_us;
    ngx_atomic_uint_t count;
} ngx_http_markdown_metrics_v1_histogram_t;

typedef struct {
    struct {
        ngx_atomic_uint_t converted;
        ngx_atomic_uint_t skipped_not_eligible;
        ngx_atomic_uint_t skipped_accept;
        ngx_atomic_uint_t skipped_no_accept;
        ngx_atomic_uint_t skipped_conditional;
        ngx_atomic_uint_t skipped_disabled;
        ngx_atomic_uint_t skipped_bypass_no_transform;
        ngx_atomic_uint_t failed_open;
        ngx_atomic_uint_t failed_closed;
        ngx_atomic_uint_t aborted;
    } requests;

    struct {
        ngx_atomic_uint_t full_buffer;
        ngx_atomic_uint_t streaming;
    } attempts;

    struct {
        ngx_atomic_uint_t full_buffer;
        ngx_atomic_uint_t streaming;
    } deliveries;

    ngx_http_markdown_metrics_v1_histogram_t duration_full_buffer;
    ngx_http_markdown_metrics_v1_histogram_t duration_streaming;

    ngx_atomic_uint_t input_bytes;

    ngx_atomic_uint_t output_bytes;

    ngx_atomic_uint_t inflight;

    struct {
        ngx_atomic_uint_t commit;
        ngx_atomic_uint_t fallback;
        ngx_atomic_uint_t safe_finish_start;
        ngx_atomic_uint_t abort_start;
        ngx_atomic_uint_t resume_success;
        ngx_atomic_uint_t resume_failure;
    } streaming_events;

    struct {
        ngx_atomic_uint_t gzip_success;
        ngx_atomic_uint_t gzip_failure_budget;
        ngx_atomic_uint_t gzip_failure_format;
        ngx_atomic_uint_t gzip_failure_truncated;
        ngx_atomic_uint_t gzip_failure_io;
        ngx_atomic_uint_t deflate_success;
        ngx_atomic_uint_t deflate_failure_budget;
        ngx_atomic_uint_t deflate_failure_format;
        ngx_atomic_uint_t deflate_failure_truncated;
        ngx_atomic_uint_t deflate_failure_io;
        ngx_atomic_uint_t brotli_success;
        ngx_atomic_uint_t brotli_failure_budget;
        ngx_atomic_uint_t brotli_failure_format;
        ngx_atomic_uint_t brotli_failure_truncated;
        ngx_atomic_uint_t brotli_failure_io;
    } decompression;

    struct {
        ngx_atomic_uint_t success;
        ngx_atomic_uint_t failure_schema_version;
        ngx_atomic_uint_t failure_unknown_key;
        ngx_atomic_uint_t failure_duplicate_key;
        ngx_atomic_uint_t failure_invalid_type;
        ngx_atomic_uint_t failure_out_of_range;
        ngx_atomic_uint_t failure_size_exceeded;
        ngx_atomic_uint_t failure_parse_error;
        ngx_atomic_uint_t failure_file_error;
    } dynconf_reloads;

    struct {
        const u_char  *version;
        const u_char  *nginx_version_text;
        const u_char  *features;
    } build_info;
} ngx_http_markdown_metrics_v1_snapshot_t;


static u_char *
ngx_http_markdown_metrics_v1_render_histogram(
    u_char *p,
    u_char *end,
    const char *engine,
    const ngx_http_markdown_metrics_v1_histogram_t *histogram)
{
    static const char *bucket_le[
        NGX_HTTP_MARKDOWN_METRICS_V1_BUCKET_COUNT] = {
        "0.001", "0.005", "0.01", "0.025", "0.05",
        "0.1", "0.25", "0.5", "1.0", "5.0"
    };
    ngx_atomic_uint_t  cumulative;
    ngx_atomic_uint_t  sum_seconds;
    ngx_atomic_uint_t  sum_frac;

    if (p == NULL || end == NULL || engine == NULL || histogram == NULL) {
        return NULL;
    }

    cumulative = 0;
    for (ngx_uint_t i = 0;
         i < NGX_HTTP_MARKDOWN_METRICS_V1_BUCKET_COUNT;
         i++)
    {
        cumulative += histogram->buckets[i];
        p = ngx_slprintf(p, end,
            "nginx_markdown_conversion_duration_seconds_bucket"
            "{engine=\"%s\",le=\"%s\"} %uA\n",
            engine, bucket_le[i], cumulative);
        if (p >= end) {
            return NULL;
        }
    }

    p = ngx_slprintf(p, end,
        "nginx_markdown_conversion_duration_seconds_bucket"
        "{engine=\"%s\",le=\"+Inf\"} %uA\n",
        engine, histogram->count);
    if (p >= end) {
        return NULL;
    }

    sum_seconds = histogram->sum_us / 1000000;
    sum_frac = histogram->sum_us % 1000000;
    p = ngx_slprintf(p, end,
        "nginx_markdown_conversion_duration_seconds_sum"
        "{engine=\"%s\"} %uA.%06uA\n",
        engine, sum_seconds, sum_frac);
    if (p >= end) {
        return NULL;
    }

    p = ngx_slprintf(p, end,
        "nginx_markdown_conversion_duration_seconds_count"
        "{engine=\"%s\"} %uA\n",
        engine, histogram->count);
    if (p >= end) {
        return NULL;
    }

    return p;
}

static u_char *
ngx_http_markdown_metrics_v1_render_families_1_to_3(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_v1_snapshot_t *snapshot)
{
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_requests_total "
        "Total requests with terminal outcome "
        "in the module decision chain.\n"
        "# TYPE nginx_markdown_requests_total counter\n"
        "nginx_markdown_requests_total"
        "{outcome=\"converted\",stage=\"conversion\","
        "reason=\"converted\"} %uA\n"
        "nginx_markdown_requests_total"
        "{outcome=\"skipped\",stage=\"eligibility\","
        "reason=\"not_eligible\"} %uA\n"
        "nginx_markdown_requests_total"
        "{outcome=\"skipped\",stage=\"eligibility\","
        "reason=\"skipped_accept\"} %uA\n"
        "nginx_markdown_requests_total"
        "{outcome=\"skipped\",stage=\"eligibility\","
        "reason=\"skipped_no_accept\"} %uA\n"
        "nginx_markdown_requests_total"
        "{outcome=\"skipped\",stage=\"eligibility\","
        "reason=\"skipped_conditional\"} %uA\n"
        "nginx_markdown_requests_total"
        "{outcome=\"skipped\",stage=\"eligibility\","
        "reason=\"disabled\"} %uA\n"
        "nginx_markdown_requests_total"
        "{outcome=\"skipped\",stage=\"eligibility\","
        "reason=\"bypass_no_transform\"} %uA\n"
        "nginx_markdown_requests_total"
        "{outcome=\"failed_open\",stage=\"delivery\","
        "reason=\"failed_open\"} %uA\n"
        "nginx_markdown_requests_total"
        "{outcome=\"failed_closed\",stage=\"delivery\","
        "reason=\"failed_closed\"} %uA\n"
        "nginx_markdown_requests_total"
        "{outcome=\"aborted\",stage=\"delivery\","
        "reason=\"streaming_mid_flight_error\"} %uA\n"
        "\n",
        snapshot->requests.converted,
        snapshot->requests.skipped_not_eligible,
        snapshot->requests.skipped_accept,
        snapshot->requests.skipped_no_accept,
        snapshot->requests.skipped_conditional,
        snapshot->requests.skipped_disabled,
        snapshot->requests.skipped_bypass_no_transform,
        snapshot->requests.failed_open,
        snapshot->requests.failed_closed,
        snapshot->requests.aborted);
    if (p >= end) {
        return NULL;
    }

    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_conversion_attempts_total "
        "Total conversion attempts (engine selection committed).\n"
        "# TYPE nginx_markdown_conversion_attempts_total counter\n"
        "nginx_markdown_conversion_attempts_total"
        "{engine=\"full_buffer\"} %uA\n"
        "nginx_markdown_conversion_attempts_total"
        "{engine=\"streaming\"} %uA\n"
        "\n",
        snapshot->attempts.full_buffer,
        snapshot->attempts.streaming);
    if (p >= end) {
        return NULL;
    }

    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_conversion_deliveries_total "
        "Successful terminal deliveries of converted "
        "Markdown (downstream accepted last_buf).\n"
        "# TYPE nginx_markdown_conversion_deliveries_total counter\n"
        "nginx_markdown_conversion_deliveries_total"
        "{engine=\"full_buffer\"} %uA\n"
        "nginx_markdown_conversion_deliveries_total"
        "{engine=\"streaming\"} %uA\n"
        "\n",
        snapshot->deliveries.full_buffer,
        snapshot->deliveries.streaming);
    if (p >= end) {
        return NULL;
    }

    return p;
}

static u_char *
ngx_http_markdown_metrics_v1_render_families_4_to_7(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_v1_snapshot_t *snapshot)
{
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_conversion_duration_seconds "
        "Duration of conversion operations in seconds.\n"
        "# TYPE nginx_markdown_conversion_duration_seconds histogram\n");
    if (p >= end) {
        return NULL;
    }

    p = ngx_http_markdown_metrics_v1_render_histogram(
        p, end, "full_buffer", &snapshot->duration_full_buffer);
    if (p == NULL) {
        return NULL;
    }
    p = ngx_http_markdown_metrics_v1_render_histogram(
        p, end, "streaming", &snapshot->duration_streaming);
    if (p == NULL) {
        return NULL;
    }
    p = ngx_slprintf(p, end, "\n");
    if (p >= end) {
        return NULL;
    }

    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_input_bytes_total "
        "Total input bytes read for conversion.\n"
        "# TYPE nginx_markdown_input_bytes_total counter\n"
        "nginx_markdown_input_bytes_total %uA\n"
        "\n",
        snapshot->input_bytes);
    if (p >= end) {
        return NULL;
    }

    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_output_bytes_total "
        "Total output bytes delivered downstream after conversion.\n"
        "# TYPE nginx_markdown_output_bytes_total counter\n"
        "nginx_markdown_output_bytes_total %uA\n"
        "\n",
        snapshot->output_bytes);
    if (p >= end) {
        return NULL;
    }

    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_inflight_requests "
        "Number of requests currently undergoing conversion.\n"
        "# TYPE nginx_markdown_inflight_requests gauge\n"
        "nginx_markdown_inflight_requests %uA\n"
        "\n",
        snapshot->inflight);
    if (p >= end) {
        return NULL;
    }

    return p;
}

static u_char *
ngx_http_markdown_metrics_v1_render_families_8_to_9(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_v1_snapshot_t *snapshot)
{
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_streaming_events_total "
        "Streaming lifecycle transition events (closed allowlist).\n"
        "# TYPE nginx_markdown_streaming_events_total counter\n"
        "nginx_markdown_streaming_events_total"
        "{transition=\"commit\",reason=\"converted\"} %uA\n"
        "nginx_markdown_streaming_events_total"
        "{transition=\"fallback\",reason=\"bypass_no_transform\"} %uA\n"
        "nginx_markdown_streaming_events_total"
        "{transition=\"safe_finish_start\",reason=\"converted\"} %uA\n"
        "nginx_markdown_streaming_events_total"
        "{transition=\"abort_start\",reason=\"streaming_mid_flight_error\"} %uA\n"
        "nginx_markdown_streaming_events_total"
        "{transition=\"resume_success\",reason=\"converted\"} %uA\n"
        "nginx_markdown_streaming_events_total"
        "{transition=\"resume_failure\",reason=\"streaming_mid_flight_error\"} %uA\n"
        "\n",
        snapshot->streaming_events.commit,
        snapshot->streaming_events.fallback,
        snapshot->streaming_events.safe_finish_start,
        snapshot->streaming_events.abort_start,
        snapshot->streaming_events.resume_success,
        snapshot->streaming_events.resume_failure);
    if (p >= end) {
        return NULL;
    }

    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_decompression_events_total "
        "Decompression layer completion or failure events.\n"
        "# TYPE nginx_markdown_decompression_events_total counter\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"gzip\",outcome=\"success\",reason=\"ok\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"gzip\",outcome=\"failure\",reason=\"budget_exceeded\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"gzip\",outcome=\"failure\",reason=\"format_error\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"gzip\",outcome=\"failure\",reason=\"truncated_input\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"gzip\",outcome=\"failure\",reason=\"io_error\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"deflate\",outcome=\"success\",reason=\"ok\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"deflate\",outcome=\"failure\",reason=\"budget_exceeded\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"deflate\",outcome=\"failure\",reason=\"format_error\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"deflate\",outcome=\"failure\",reason=\"truncated_input\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"deflate\",outcome=\"failure\",reason=\"io_error\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"brotli\",outcome=\"success\",reason=\"ok\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"brotli\",outcome=\"failure\",reason=\"budget_exceeded\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"brotli\",outcome=\"failure\",reason=\"format_error\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"brotli\",outcome=\"failure\",reason=\"truncated_input\"} %uA\n"
        "nginx_markdown_decompression_events_total"
        "{encoding=\"brotli\",outcome=\"failure\",reason=\"io_error\"} %uA\n"
        "\n",
        snapshot->decompression.gzip_success,
        snapshot->decompression.gzip_failure_budget,
        snapshot->decompression.gzip_failure_format,
        snapshot->decompression.gzip_failure_truncated,
        snapshot->decompression.gzip_failure_io,
        snapshot->decompression.deflate_success,
        snapshot->decompression.deflate_failure_budget,
        snapshot->decompression.deflate_failure_format,
        snapshot->decompression.deflate_failure_truncated,
        snapshot->decompression.deflate_failure_io,
        snapshot->decompression.brotli_success,
        snapshot->decompression.brotli_failure_budget,
        snapshot->decompression.brotli_failure_format,
        snapshot->decompression.brotli_failure_truncated,
        snapshot->decompression.brotli_failure_io);
    if (p >= end) {
        return NULL;
    }

    return p;
}

static u_char *
ngx_http_markdown_metrics_v1_render_families_10_to_11(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_v1_snapshot_t *snapshot)
{
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_dynconf_reloads_total "
        "Dynconf reload attempts by outcome.\n"
        "# TYPE nginx_markdown_dynconf_reloads_total counter\n"
        "nginx_markdown_dynconf_reloads_total"
        "{outcome=\"success\",reason=\"ok\"} %uA\n"
        "nginx_markdown_dynconf_reloads_total"
        "{outcome=\"failure\",reason=\"schema_version\"} %uA\n"
        "nginx_markdown_dynconf_reloads_total"
        "{outcome=\"failure\",reason=\"unknown_key\"} %uA\n"
        "nginx_markdown_dynconf_reloads_total"
        "{outcome=\"failure\",reason=\"duplicate_key\"} %uA\n"
        "nginx_markdown_dynconf_reloads_total"
        "{outcome=\"failure\",reason=\"invalid_type\"} %uA\n"
        "nginx_markdown_dynconf_reloads_total"
        "{outcome=\"failure\",reason=\"out_of_range\"} %uA\n"
        "nginx_markdown_dynconf_reloads_total"
        "{outcome=\"failure\",reason=\"size_exceeded\"} %uA\n"
        "nginx_markdown_dynconf_reloads_total"
        "{outcome=\"failure\",reason=\"parse_error\"} %uA\n"
        "nginx_markdown_dynconf_reloads_total"
        "{outcome=\"failure\",reason=\"file_error\"} %uA\n"
        "\n",
        snapshot->dynconf_reloads.success,
        snapshot->dynconf_reloads.failure_schema_version,
        snapshot->dynconf_reloads.failure_unknown_key,
        snapshot->dynconf_reloads.failure_duplicate_key,
        snapshot->dynconf_reloads.failure_invalid_type,
        snapshot->dynconf_reloads.failure_out_of_range,
        snapshot->dynconf_reloads.failure_size_exceeded,
        snapshot->dynconf_reloads.failure_parse_error,
        snapshot->dynconf_reloads.failure_file_error);
    if (p >= end) {
        return NULL;
    }

    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_build_info "
        "Module build information (always 1).\n"
        "# TYPE nginx_markdown_build_info gauge\n"
        "nginx_markdown_build_info"
        "{version=\"%s\",nginx_version=\"%s\","
        "features=\"%s\"} 1\n"
        "\n",
        snapshot->build_info.version,
        snapshot->build_info.nginx_version_text,
        snapshot->build_info.features);
    if (p >= end) {
        return NULL;
    }

    return p;
}

/*
 * Render the 11 frozen metric families in Prometheus text 0.0.4 format.
 *
 * Writes HELP, TYPE, and metric lines for all 11 families into the
 * buffer between p and end. Returns a pointer past the last byte
 * written, or NULL if the buffer is exhausted.
 *
 * Parameters:
 *   p        - Start of writable buffer region
 *   end      - One past the end of the buffer
 *   snapshot - v1 metrics snapshot (exactly 11 families)
 *
 * Returns:
 *   Pointer past the last byte written, or NULL on buffer overflow
 */
static u_char *
ngx_http_markdown_metrics_v1_render(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_v1_snapshot_t *snapshot)
{
    if (p == NULL || end == NULL || snapshot == NULL) {
        return NULL;
    }

    p = ngx_http_markdown_metrics_v1_render_families_1_to_3(
        p, end, snapshot);
    if (p == NULL) {
        return NULL;
    }

    p = ngx_http_markdown_metrics_v1_render_families_4_to_7(
        p, end, snapshot);
    if (p == NULL) {
        return NULL;
    }

    p = ngx_http_markdown_metrics_v1_render_families_8_to_9(
        p, end, snapshot);
    if (p == NULL) {
        return NULL;
    }

    return ngx_http_markdown_metrics_v1_render_families_10_to_11(
        p, end, snapshot);
}

#endif /* NGX_HTTP_MARKDOWN_METRICS_V1_RENDERER_H */
