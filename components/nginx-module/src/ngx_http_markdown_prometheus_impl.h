#ifndef NGX_HTTP_MARKDOWN_PROMETHEUS_IMPL_H
#define NGX_HTTP_MARKDOWN_PROMETHEUS_IMPL_H

/*
 * Prometheus text exposition format renderer.
 *
 * WARNING: This header is an implementation detail of the main
 * translation unit (ngx_http_markdown_filter_module.c).  It must
 * NOT be included from any other .c file or used as a standalone
 * compilation unit.
 *
 * Renders a metrics snapshot as Prometheus text exposition format
 * (content type: text/plain; version=0.0.4; charset=utf-8).
 * All label values are compile-time constants — no runtime
 * escaping is needed.
 */

/* C99 declaration visibility for standalone static analysis of this impl header. */
u_char *ngx_slprintf(u_char *buf, u_char *last,
    const char *fmt, ...);

/*
 * Render a metrics snapshot as Prometheus text exposition format.
 *
 * Writes HELP, TYPE, and metric lines for all module counters
 * into the buffer between p and end.  Returns a pointer past
 * the last byte written.
 *
 * Parameters:
 *   p        - Start of writable buffer region
 *   end      - One past the end of the buffer
 *   snapshot - Collected metrics snapshot
 *
 * Returns:
 *   Pointer past the last byte written
 */
static u_char *
ngx_http_markdown_metrics_write_prometheus(
    u_char *p,
    u_char *end,
    ngx_http_markdown_metrics_snapshot_t *snapshot)
{
    /* requests_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_requests_total "
        "Total requests entering the module decision chain.\n"
        "# TYPE nginx_markdown_requests_total counter\n"
        "nginx_markdown_requests_total %uA\n"
        "\n",
        snapshot->requests_entered);

    /* conversions_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_conversions_total "
        "Successful HTML-to-Markdown conversions.\n"
        "# TYPE nginx_markdown_conversions_total counter\n"
        "nginx_markdown_conversions_total %uA\n"
        "\n",
        snapshot->conversions_succeeded);

    /* passthrough_total (derived: skips + fail-open) */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_passthrough_total "
        "Requests not converted "
        "(skipped or failed-open).\n"
        "# TYPE nginx_markdown_passthrough_total "
        "counter\n"
        "nginx_markdown_passthrough_total %uA\n"
        "\n",
        snapshot->conversions_bypassed
            + snapshot->failopen_count);

    /* skips_total{reason=...} */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_skips_total "
        "Requests skipped by reason.\n"
        "# TYPE nginx_markdown_skips_total counter\n"
        "nginx_markdown_skips_total{reason=\"SKIP_METHOD\"}"
        " %uA\n"
        "nginx_markdown_skips_total{reason=\"SKIP_STATUS\"}"
        " %uA\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_CONTENT_TYPE\"} %uA\n"
        "nginx_markdown_skips_total{reason=\"SKIP_SIZE\"}"
        " %uA\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_STREAMING\"} %uA\n"
        "nginx_markdown_skips_total{reason=\"SKIP_AUTH\"}"
        " %uA\n"
        "nginx_markdown_skips_total{reason=\"SKIP_RANGE\"}"
        " %uA\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_ACCEPT\"} %uA\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_CONFIG\"} %uA\n"
        "\n",
        snapshot->skips.method,
        snapshot->skips.status,
        snapshot->skips.content_type,
        snapshot->skips.size,
        snapshot->skips.streaming,
        snapshot->skips.auth,
        snapshot->skips.range,
        snapshot->skips.accept,
        snapshot->skips.config);

    /* failures_total{stage=...} */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_failures_total "
        "Conversion failures by stage.\n"
        "# TYPE nginx_markdown_failures_total counter\n"
        "nginx_markdown_failures_total"
        "{stage=\"FAIL_CONVERSION\"} %uA\n"
        "nginx_markdown_failures_total"
        "{stage=\"FAIL_RESOURCE_LIMIT\"} %uA\n"
        "nginx_markdown_failures_total"
        "{stage=\"FAIL_SYSTEM\"} %uA\n"
        "\n",
        snapshot->failures_conversion,
        snapshot->failures_resource_limit,
        snapshot->failures_system);

    /* failopen_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_failopen_total "
        "Conversions failed with original HTML served "
        "(fail-open).\n"
        "# TYPE nginx_markdown_failopen_total counter\n"
        "nginx_markdown_failopen_total %uA\n"
        "\n",
        snapshot->failopen_count);

    /* large_response_path_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_large_response_path_total "
        "Requests routed to incremental processing path.\n"
        "# TYPE nginx_markdown_large_response_path_total "
        "counter\n"
        "nginx_markdown_large_response_path_total %uA\n"
        "\n",
        snapshot->path_hits.incremental);

#ifdef MARKDOWN_STREAMING_ENABLED
    /* streaming_path_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_streaming_path_total "
        "Requests routed to streaming path.\n"
        "# TYPE nginx_markdown_streaming_path_total "
        "counter\n"
        "nginx_markdown_streaming_path_total %uA\n"
        "\n",
        snapshot->path_hits.streaming);

    /* streaming_total{result=...} */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_streaming_total "
        "Streaming conversion outcomes.\n"
        "# TYPE nginx_markdown_streaming_total counter\n"
        "nginx_markdown_streaming_total"
        "{result=\"success\"} %uA\n"
        "nginx_markdown_streaming_total"
        "{result=\"failed\"} %uA\n"
        "nginx_markdown_streaming_total"
        "{result=\"fallback\"} %uA\n"
        "nginx_markdown_streaming_total"
        "{result=\"postcommit_error\"} %uA\n"
        "\n",
        snapshot->streaming.succeeded_total,
        snapshot->streaming.failed_total,
        snapshot->streaming.fallback_total,
        snapshot->streaming.postcommit_error_total);

    /* streaming_failures_total{stage=...} */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_streaming_failures_total "
        "Detailed streaming pre-commit failures by stage.\n"
        "# TYPE nginx_markdown_streaming_failures_total counter\n"
        "nginx_markdown_streaming_failures_total"
        "{stage=\"precommit_failopen\"} %uA\n"
        "nginx_markdown_streaming_failures_total"
        "{stage=\"precommit_reject\"} %uA\n"
        "\n",
        snapshot->streaming.precommit_failopen_total,
        snapshot->streaming.precommit_reject_total);

    /* streaming_budget_exceeded_total */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_streaming_budget_exceeded_total "
        "Streaming memory budget exceeded count.\n"
        "# TYPE "
        "nginx_markdown_streaming_budget_exceeded_total "
        "counter\n"
        "nginx_markdown_streaming_budget_exceeded_total"
        " %uA\n"
        "\n",
        snapshot->streaming.budget_exceeded_total);

    /* streaming_shadow_total */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_streaming_shadow_total "
        "Shadow mode comparison runs.\n"
        "# TYPE "
        "nginx_markdown_streaming_shadow_total counter\n"
        "nginx_markdown_streaming_shadow_total %uA\n"
        "\n",
        snapshot->streaming.shadow_total);

    /* streaming_shadow_diff_total */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_streaming_shadow_diff_total "
        "Shadow mode output differences detected.\n"
        "# TYPE "
        "nginx_markdown_streaming_shadow_diff_total "
        "counter\n"
        "nginx_markdown_streaming_shadow_diff_total"
        " %uA\n"
        "\n",
        snapshot->streaming.shadow_diff_total);

    /* streaming_ttfb_seconds (gauge) */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_streaming_ttfb_seconds "
        "Last streaming request time-to-first-byte "
        "in seconds (millisecond resolution).\n"
        "# TYPE "
        "nginx_markdown_streaming_ttfb_seconds gauge\n"
        "nginx_markdown_streaming_ttfb_seconds "
        "%uA.%03uA\n"
        "\n",
        snapshot->streaming.last_ttfb_ms / 1000,
        snapshot->streaming.last_ttfb_ms % 1000);

    /* streaming_peak_memory_bytes (gauge) */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_streaming_peak_memory_bytes "
        "Last streaming conversion peak memory estimate.\n"
        "# TYPE "
        "nginx_markdown_streaming_peak_memory_bytes gauge\n"
        "nginx_markdown_streaming_peak_memory_bytes "
        "%uA\n"
        "\n",
        snapshot->streaming.last_peak_memory_bytes);
#endif

    /* input_bytes_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_input_bytes_total "
        "Cumulative HTML input bytes from successful "
        "conversions.\n"
        "# TYPE nginx_markdown_input_bytes_total counter\n"
        "nginx_markdown_input_bytes_total %uA\n"
        "\n",
        snapshot->input_bytes);

    /* output_bytes_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_output_bytes_total "
        "Cumulative Markdown output bytes from successful "
        "conversions.\n"
        "# TYPE nginx_markdown_output_bytes_total counter\n"
        "nginx_markdown_output_bytes_total %uA\n"
        "\n",
        snapshot->output_bytes);

    /* estimated_token_savings_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_estimated_token_savings_total"
        " Estimated cumulative token savings "
        "(requires markdown_token_estimate on).\n"
        "# TYPE "
        "nginx_markdown_estimated_token_savings_total "
        "counter\n"
        "nginx_markdown_estimated_token_savings_total"
        " %uA\n"
        "\n",
        snapshot->estimated_token_savings);

    /* decompressions_total{format=...} */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_decompressions_total "
        "Decompression operations by format.\n"
        "# TYPE nginx_markdown_decompressions_total "
        "counter\n"
        "nginx_markdown_decompressions_total"
        "{format=\"gzip\"} %uA\n"
        "nginx_markdown_decompressions_total"
        "{format=\"deflate\"} %uA\n"
        "nginx_markdown_decompressions_total"
        "{format=\"brotli\"} %uA\n"
        "\n",
        snapshot->decompressions.gzip,
        snapshot->decompressions.deflate,
        snapshot->decompressions.brotli);

    /* decompression_failures_total */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_decompression_failures_total "
        "Failed decompression attempts.\n"
        "# TYPE "
        "nginx_markdown_decompression_failures_total "
        "counter\n"
        "nginx_markdown_decompression_failures_total"
        " %uA\n"
        "\n",
        snapshot->decompressions.failed);

    /*
     * conversion_duration_seconds{le=...}
     *
     * Emitted as cumulative buckets: each le value includes
     * all observations at or below that threshold.
     * le="+Inf" equals the sum of all four discrete counters.
     */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_conversion_duration_seconds "
        "Cumulative conversion count per latency bucket "
        "(not a native Prometheus histogram; "
        "no _sum/_count).\n"
        "# TYPE "
        "nginx_markdown_conversion_duration_seconds gauge\n"
        "nginx_markdown_conversion_duration_seconds"
        "{le=\"0.01\"} %uA\n"
        "nginx_markdown_conversion_duration_seconds"
        "{le=\"0.1\"} %uA\n"
        "nginx_markdown_conversion_duration_seconds"
        "{le=\"1.0\"} %uA\n"
        "nginx_markdown_conversion_duration_seconds"
        "{le=\"+Inf\"} %uA\n",
        snapshot->conversion_latency_le_10ms,
        snapshot->conversion_latency_le_10ms
            + snapshot->conversion_latency_le_100ms,
        snapshot->conversion_latency_le_10ms
            + snapshot->conversion_latency_le_100ms
            + snapshot->conversion_latency_le_1000ms,
        snapshot->conversion_latency_le_10ms
            + snapshot->conversion_latency_le_100ms
            + snapshot->conversion_latency_le_1000ms
            + snapshot->conversion_latency_gt_1000ms);

    /*
     * Detect buffer exhaustion.  ngx_slprintf stops at end
     * without signaling an error, so p == end means the
     * output was silently truncated.  Return NULL to let
     * the caller distinguish truncation from success.
     */
    if (p == end) {
        return NULL;
    }

    return p;
}

#endif /* NGX_HTTP_MARKDOWN_PROMETHEUS_IMPL_H */
