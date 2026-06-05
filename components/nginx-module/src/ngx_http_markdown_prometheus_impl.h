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
 * Aggregate-series label values are compile-time constants.
 * Per-path series include a runtime "path" label; the path value
 * is escaped for Prometheus label syntax (backslash, double-quote,
 * newlines, control characters).
 */

/* C99 declaration visibility for standalone static analysis of this impl header. */
u_char *ngx_slprintf(u_char *buf, u_char *last,
    const char *fmt, ...);

/*
 * Default: per-path RB-tree walk is enabled in production builds.
 * Unit test stubs define this to 0 before including this header.
 */
#ifndef NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
#define NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED  1
#endif

/*
 * Forward declaration: in-order RB-tree walk helper for per-path
 * Prometheus output.  Defined after the main write function.
 * Only available when NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED is 1.
 */
#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
static u_char *
ngx_http_markdown_prometheus_walk_path_tree(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    u_char *p,
    u_char *end);
#endif

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
    const ngx_http_markdown_metrics_snapshot_t *snapshot)
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
            + snapshot->results.failopen_count);

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
        "nginx_markdown_skips_total"
        "{reason=\"SKIPPED_NO_ACCEPT\"} %uA\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIPPED_CONDITIONAL\"} %uA\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_COMPRESSION_PASSTHROUGH\"} %uA\n"
        "\n",
        snapshot->skips.method,
        snapshot->skips.status,
        snapshot->skips.content_type,
        snapshot->skips.size,
        snapshot->skips.streaming,
        snapshot->skips.auth,
        snapshot->skips.range,
        snapshot->skips.accept,
        snapshot->skips.config,
        snapshot->skips.no_accept,
        snapshot->skips.conditional,
        snapshot->skips.compression_passthrough);

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
        snapshot->results.failopen_count);

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

    /* streaming_total{result=...} (mutually exclusive outcomes) */
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
        "\n",
        snapshot->streaming.succeeded_total,
        snapshot->streaming.failed_total,
        snapshot->streaming.fallback_total);

    /* streaming_failures_total{stage=...} */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_streaming_failures_total "
        "Detailed streaming failures by stage.\n"
        "# TYPE nginx_markdown_streaming_failures_total counter\n"
        "nginx_markdown_streaming_failures_total"
        "{stage=\"precommit_failopen\"} %uA\n"
        "nginx_markdown_streaming_failures_total"
        "{stage=\"precommit_reject\"} %uA\n"
        "nginx_markdown_streaming_failures_total"
        "{stage=\"postcommit_error\"} %uA\n"
        "\n",
        snapshot->streaming.precommit_failopen_total,
        snapshot->streaming.precommit_reject_total,
        snapshot->streaming.postcommit_error_total);

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
        "Last streaming conversion peak working-set estimate; "
        "not process RSS.\n"
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
        snapshot->results.estimated_token_savings);

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

    /* decompression_budget_exceeded_total */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_decompression_budget_exceeded_total "
        "Decompression operations that exceeded the configured budget.\n"
        "# TYPE "
        "nginx_markdown_decompression_budget_exceeded_total "
        "counter\n"
        "nginx_markdown_decompression_budget_exceeded_total"
        " %uA\n"
        "\n",
        snapshot->decompressions.budget_exceeded_total);

    /* decompression_format_error_total */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_decompression_format_error_total "
        "Decompression operations that failed due to "
        "invalid format.\n"
        "# TYPE "
        "nginx_markdown_decompression_format_error_total "
        "counter\n"
        "nginx_markdown_decompression_format_error_total"
        " %uA\n"
        "\n",
        snapshot->decompressions.format_error_total);

    /* decompression_truncated_input_total */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_decompression_truncated_input_total "
        "Decompression operations that failed due to "
        "truncated input.\n"
        "# TYPE "
        "nginx_markdown_decompression_truncated_input_total "
        "counter\n"
        "nginx_markdown_decompression_truncated_input_total"
        " %uA\n"
        "\n",
        snapshot->decompressions.truncated_input_total);

    /* decompression_io_error_total */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_decompression_io_error_total "
        "Decompression I/O errors.\n"
        "# TYPE "
        "nginx_markdown_decompression_io_error_total "
        "counter\n"
        "nginx_markdown_decompression_io_error_total"
        " %uA\n"
        "\n",
        snapshot->decompressions.io_error_total);

    /* replay_buffer_errors_total */
    p = ngx_slprintf(p, end,
        "# HELP "
        "nginx_markdown_replay_buffer_errors_total "
        "Replay buffer init or append failures.\n"
        "# TYPE "
        "nginx_markdown_replay_buffer_errors_total "
        "counter\n"
        "nginx_markdown_replay_buffer_errors_total"
        " %uA\n"
        "\n",
        snapshot->results.replay_buffer_errors_total);

    /* parse_timeouts_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_parse_timeouts_total "
        "HTML parsing operations that exceeded the configured deadline.\n"
        "# TYPE nginx_markdown_parse_timeouts_total counter\n"
        "nginx_markdown_parse_timeouts_total %uA\n"
        "\n",
        snapshot->parse_interrupts.parse_timeouts_total);

    /* parse_budget_exceeded_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_parse_budget_exceeded_total "
        "HTML parsing operations that exceeded the parser memory budget.\n"
        "# TYPE nginx_markdown_parse_budget_exceeded_total counter\n"
        "nginx_markdown_parse_budget_exceeded_total %uA\n"
        "\n",
        snapshot->parse_interrupts.parse_budget_exceeded_total);

    /* delivery_total (separate from decision_total) */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_delivery_total "
        "Successful response deliveries after downstream NGX_OK.\n"
        "# TYPE nginx_markdown_delivery_total counter\n"
        "nginx_markdown_delivery_total %uA\n"
        "\n",
        snapshot->results.delivery_count);

    /* decision_total (includes skips and fail-opens) */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_decision_total "
        "Decision engine evaluations (includes skips and fail-opens).\n"
        "# TYPE nginx_markdown_decision_total counter\n"
        "nginx_markdown_decision_total %uA\n"
        "\n",
        snapshot->results.decision_count);

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
        snapshot->conversion_latency.le_10ms,
        snapshot->conversion_latency.le_10ms
            + snapshot->conversion_latency.le_100ms,
        snapshot->conversion_latency.le_10ms
            + snapshot->conversion_latency.le_100ms
            + snapshot->conversion_latency.le_1000ms,
        snapshot->conversion_latency.le_10ms
            + snapshot->conversion_latency.le_100ms
            + snapshot->conversion_latency.le_1000ms
            + snapshot->conversion_latency.gt_1000ms);

    /* per_path_entries */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_per_path_entries "
        "Number of distinct URI paths tracked in per-path metrics.\n"
        "# TYPE nginx_markdown_per_path_entries gauge\n"
        "nginx_markdown_per_path_entries %uA\n"
        "\n",
        snapshot->per_path.path_entries);

    /* per_path_conversions_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_per_path_conversions_total "
        "Total successful conversions recorded in per-path metrics.\n"
        "# TYPE nginx_markdown_per_path_conversions_total counter\n"
        "nginx_markdown_per_path_conversions_total %uA\n"
        "\n",
        snapshot->per_path.path_conversions);

    /* per_path_conversion_time_ms_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_per_path_conversion_time_ms_total "
        "Cumulative conversion time (ms) recorded in per-path metrics.\n"
        "# TYPE nginx_markdown_per_path_conversion_time_ms_total counter\n"
        "nginx_markdown_per_path_conversion_time_ms_total %uA\n"
        "\n",
        snapshot->per_path.path_conversion_time_sum_ms);

    /* per_path_overflow_total */
    p = ngx_slprintf(p, end,
        "# HELP nginx_markdown_per_path_overflow_total "
        "Paths dropped because per-path cardinality limit was reached. "
        "Controlled by markdown_metrics_per_path_cardinality.\n"
        "# TYPE nginx_markdown_per_path_overflow_total counter\n"
        "nginx_markdown_per_path_overflow_total %uA\n"
        "\n",
        snapshot->per_path.overflow_count);

    /*
     * Per-path individual entries: walk the SHM RB-tree to emit
     * series with a "path" label for each tracked URI.
     *
     * The RB-tree is protected by the slab pool mutex.  We acquire
     * the lock, walk in-order via a recursive helper, and release.
     *
     * Only emit if per-path tracking is active (path_entries > 0)
     * and the SHM zone is available.
     *
     * This section requires full NGINX type definitions
      * (ngx_shm_zone_t, ngx_slab_pool_t, etc.) and is guarded
      * by NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED.  Unit tests
      * that lack these types define the macro to 0.
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

        p = ngx_slprintf(p, end,
            "# HELP nginx_markdown_path_conversions_total "
            "Per-path conversion count.\n"
            "# TYPE nginx_markdown_path_conversions_total counter\n"
            "# HELP nginx_markdown_path_conversion_time_ms_total "
            "Per-path cumulative conversion time in ms.\n"
            "# TYPE nginx_markdown_path_conversion_time_ms_total "
            "counter\n");

        ngx_shmtx_lock(&shpool->mutex);

        p = ngx_http_markdown_prometheus_walk_path_tree(
                live_metrics->per_path.path_tree.root,
                &live_metrics->per_path.sentinel,
                p, end);

        ngx_shmtx_unlock(&shpool->mutex);

        /*
         * Emit __other__ pseudo-path series for overflow paths
         * that were dropped when the cardinality limit was reached.
         * This mirrors the JSON __other__ pseudo-path entry.
         */
        if (snapshot->per_path.overflow_count > 0 && p < end) {
            p = ngx_slprintf(p, end,
                "nginx_markdown_path_conversions_total"
                "{path=\"__other__\"} %uA\n"
                "nginx_markdown_path_conversion_time_ms_total"
                "{path=\"__other__\"} 0\n",
                snapshot->per_path.overflow_count);
        }

        if (p < end) {
            *p++ = '\n';
        }
    }
#endif /* NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED */

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


#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
/*
 * Write a two-character escape sequence (backslash + second char)
 * into the destination buffer for Prometheus label syntax.
 *
 * Returns the updated write position, or last if the buffer
 * cannot accommodate two more bytes.
 */
static u_char *
ngx_http_markdown_escape_prom_two_char(u_char *dst, u_char *last,
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
 * Escape a byte string for use as a Prometheus label value.
 *
 * Prometheus label values require escaping: \ -> \\, " -> \", newline -> \n.
 * Carriage return and tab are also escaped for safety.
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
ngx_http_markdown_escape_prometheus_label(u_char *dst, u_char *last,
                                          const u_char *src, size_t len)
{
    size_t   i;
    u_char   ch;

    i = 0;
    while (i < len && dst < last) {
        ch = src[i];
        i++;

        switch (ch) {
        case '\\':
            dst = ngx_http_markdown_escape_prom_two_char(dst, last, '\\');
            break;
        case '"':
            dst = ngx_http_markdown_escape_prom_two_char(dst, last, '"');
            break;
        case '\n':
            dst = ngx_http_markdown_escape_prom_two_char(dst, last, 'n');
            break;
        case '\r':
            dst = ngx_http_markdown_escape_prom_two_char(dst, last, 'r');
            break;
        case '\t':
            dst = ngx_http_markdown_escape_prom_two_char(dst, last, 't');
            break;
        default:
            if (ch >= 0x20) {
                *dst++ = ch;
                break;
            }

            /* Other control characters: emit as \uXXXX */
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
 * Recursive in-order walk of the per-path RB-tree for Prometheus output.
 *
 * Emits two metric lines per node:
 *   nginx_markdown_path_conversions_total{path="..."} <val>
 *   nginx_markdown_path_conversion_time_ms_total{path="..."} <val>
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
ngx_http_markdown_prometheus_walk_path_tree(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    u_char *p,
    u_char *end)
{
    const ngx_http_markdown_path_metric_node_t  *pnode;

    if (node == sentinel || p >= end) {
        return p;
    }

    p = ngx_http_markdown_prometheus_walk_path_tree(
            node->left, sentinel, p, end);

    if (p < end) {
        pnode = (const ngx_http_markdown_path_metric_node_t *) node;

        p = ngx_slprintf(p, end,
            "nginx_markdown_path_conversions_total"
            "{path=\"");
        p = ngx_http_markdown_escape_prometheus_label(
                p, end, pnode->path, pnode->path_len);
        p = ngx_slprintf(p, end, "\"} %uA\n",
            pnode->conversions);

        p = ngx_slprintf(p, end,
            "nginx_markdown_path_conversion_time_ms_total"
            "{path=\"");
        p = ngx_http_markdown_escape_prometheus_label(
                p, end, pnode->path, pnode->path_len);
        p = ngx_slprintf(p, end, "\"} %uA\n",
            pnode->conversion_time_sum_ms);
    }

    p = ngx_http_markdown_prometheus_walk_path_tree(
            node->right, sentinel, p, end);

    return p;
}
#endif /* NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_PROMETHEUS_IMPL_H */
