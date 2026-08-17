#ifndef NGX_HTTP_MARKDOWN_METRICS_IMPL_H
#define NGX_HTTP_MARKDOWN_METRICS_IMPL_H

#include "ngx_http_markdown_metrics_json_perf_impl.h"
#include "ngx_http_markdown_metrics_format.h"
#include "ngx_http_markdown_metrics_config.h"

#include <stdint.h>

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif

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
 * The latency histogram, decompression counters, path-hit counters, skip
 * counters, and debug per-path counters are each grouped into anonymous
 * sub-structs to keep the top-level field count within SonarCloud's 20-field
 * limit while preserving the bounded internal test renderers.
 */
typedef struct {
    ngx_atomic_uint_t le_1ms;
    ngx_atomic_uint_t le_5ms;
    ngx_atomic_uint_t le_10ms;
    ngx_atomic_uint_t le_25ms;
    ngx_atomic_uint_t le_50ms;
    ngx_atomic_uint_t le_100ms;
    ngx_atomic_uint_t le_250ms;
    ngx_atomic_uint_t le_500ms;
    ngx_atomic_uint_t le_1000ms;
    ngx_atomic_uint_t le_5000ms;
    ngx_atomic_uint_t sum_ms;
    ngx_atomic_uint_t count;
} ngx_http_markdown_metrics_v1_source_histogram_t;

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

    /* Per-engine v1 histogram bands and totals. */
    struct {
        ngx_http_markdown_metrics_v1_source_histogram_t full_buffer;
        ngx_http_markdown_metrics_v1_source_histogram_t streaming;
    } conversion_latency_v1;

    /* Decompression metrics (grouped to keep top-level field count <= 20) */
    struct {
        ngx_atomic_uint_t attempted;
        ngx_atomic_uint_t succeeded;
        ngx_atomic_uint_t failed;
        ngx_atomic_uint_t gzip;
        ngx_atomic_uint_t deflate;
        ngx_atomic_uint_t brotli;
        ngx_atomic_uint_t budget_exceeded_total;
        ngx_atomic_uint_t format_error_total;
        ngx_atomic_uint_t truncated_input_total;
        ngx_atomic_uint_t io_error_total;
        struct {
            ngx_atomic_uint_t budget;
            ngx_atomic_uint_t format;
            ngx_atomic_uint_t truncated;
            ngx_atomic_uint_t io;
        } gzip_failures;
        struct {
            ngx_atomic_uint_t budget;
            ngx_atomic_uint_t format;
            ngx_atomic_uint_t truncated;
            ngx_atomic_uint_t io;
        } deflate_failures;
        struct {
            ngx_atomic_uint_t budget;
            ngx_atomic_uint_t format;
            ngx_atomic_uint_t truncated;
            ngx_atomic_uint_t io;
        } brotli_failures;
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
        ngx_atomic_uint_t no_accept;
        ngx_atomic_uint_t conditional;
        ngx_atomic_uint_t compression_passthrough;
    } skips;

    /* Conversion result counters */
    struct {
        ngx_atomic_uint_t failopen_count;
        ngx_atomic_uint_t delivery_count;
        ngx_atomic_uint_t full_buffer_delivery_count;
        ngx_atomic_uint_t decision_count;
        ngx_atomic_uint_t estimated_token_savings;
        ngx_atomic_uint_t replay_buffer_errors_total;

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

        /* Parse interrupt metrics (v0.7.0) */
        struct {
            ngx_atomic_uint_t parse_timeouts_total;
            ngx_atomic_uint_t parse_budget_exceeded_total;
        } parse_interrupts;
    } results;

#ifdef MARKDOWN_STREAMING_ENABLED
    /* Streaming metrics */
    struct {
        ngx_atomic_uint_t requests_total;
        ngx_atomic_uint_t fallback_total;
        ngx_atomic_uint_t succeeded_total;
        ngx_atomic_uint_t commit_total;
        ngx_atomic_uint_t failed_total;
        ngx_atomic_uint_t postcommit_error_total;
        ngx_atomic_uint_t precommit_failopen_total;
        ngx_atomic_uint_t precommit_reject_total;
        ngx_atomic_uint_t budget_exceeded_total;
#ifdef MARKDOWN_STREAMING_SHADOW_DEBUG
        ngx_atomic_uint_t shadow_total;
        ngx_atomic_uint_t shadow_diff_total;
#endif
        ngx_atomic_uint_t last_ttfb_ms;
        ngx_atomic_uint_t last_peak_memory_bytes;

        /* Fallback/failure counters */
        ngx_atomic_uint_t streaming_fallback_precommit_pass;
        ngx_atomic_uint_t streaming_fallback_precommit_reject;
        ngx_atomic_uint_t streaming_failure_postcommit_abort;
        ngx_atomic_uint_t streaming_failure_postcommit_safe_finish;
        ngx_atomic_uint_t terminal_aborted_total;

        /* Engine choice counters (v0.8.0 observability) */
        struct {
            ngx_atomic_uint_t streaming;
            ngx_atomic_uint_t full_buffer;
            ngx_atomic_uint_t passthrough;
            ngx_atomic_uint_t not_eligible;
        } engine_choice;

        /* Candidate and selection counters */
        struct {
            ngx_atomic_uint_t candidate_total;
            ngx_atomic_uint_t true_streaming_selected_total;
            ngx_atomic_uint_t output_bytes_total;
            ngx_atomic_uint_t excluded_content_type_total;
        } selection;
    } streaming;
#endif

    /* Per-path metrics (removed from production in 0.9.2) */
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
    struct {
        ngx_atomic_uint_t path_entries;
        ngx_atomic_uint_t path_conversions;
        ngx_atomic_uint_t path_conversion_time_sum_ms;
        ngx_atomic_uint_t overflow_count;
        ngx_atomic_uint_t unretained_conversions;
        ngx_atomic_uint_t unretained_conversion_time_sum_ms;
    } per_path;
#endif

    /* Performance metrics (backpressure, decompression path, output mode) */
    ngx_http_markdown_metrics_perf_snapshot_t perf;
} ngx_http_markdown_metrics_snapshot_t;

static ngx_atomic_uint_t
ngx_http_markdown_metrics_ms_to_us(ngx_atomic_uint_t milliseconds)
{
    uint64_t maximum;

    maximum = (uint64_t) ((ngx_atomic_uint_t) -1);
    if ((uint64_t) milliseconds > maximum / 1000U) {
        return (ngx_atomic_uint_t) maximum;
    }

    return (ngx_atomic_uint_t) ((uint64_t) milliseconds * 1000U);
}

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

/*
 * Response buffer size for the metrics endpoint.
 *
 * Estimated current Prometheus output (without debug per-path entries):
 *   ~3.8 KiB (most verbose due to HELP/TYPE lines)
 *
 * Per-path entries add variable output depending on the number of
 * tracked paths and their URI lengths.  Each path entry is roughly
 * 80-120 bytes.  With a default cardinality limit
 * of 100 paths at ~100 bytes each, per-path output can reach
 * ~10 KiB.
 *
 * The 128 KiB buffer accommodates aggregate output plus debug per-path
 * entries for typical test deployments. If the buffer is exhausted
 * during per-path detail rendering, paths that do not fit are
 * aggregated into an "__other__" entry so the response remains
 * syntactically complete and the endpoint always returns HTTP 200.
 */
#define NGX_HTTP_MARKDOWN_METRICS_BUF_SIZE  131072

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

static u_char *
ngx_http_markdown_metrics_write_json_conversion(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_snapshot_t *snapshot,
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
        "  \"decompression_budget_exceeded_total\": %uA,\n"
        "  \"decompression_format_error_total\": %uA,\n"
        "  \"decompression_truncated_input_total\": %uA,\n"
        "  \"decompression_io_error_total\": %uA,\n"
        "  \"replay_buffer_errors_total\": %uA,\n",
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
        snapshot->conversion_latency.le_10ms,
        snapshot->conversion_latency.le_100ms,
        snapshot->conversion_latency.le_1000ms,
        snapshot->conversion_latency.gt_1000ms,
        snapshot->decompressions.attempted,
        snapshot->decompressions.succeeded,
        snapshot->decompressions.failed,
        snapshot->decompressions.gzip,
        snapshot->decompressions.deflate,
        snapshot->decompressions.brotli,
        snapshot->decompressions.budget_exceeded_total,
        snapshot->decompressions.format_error_total,
        snapshot->decompressions.truncated_input_total,
        snapshot->decompressions.io_error_total,
        snapshot->results.replay_buffer_errors_total);
}

static u_char *
ngx_http_markdown_metrics_write_json_routing(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_snapshot_t *snapshot)
{
    return ngx_slprintf(p, end,
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
        "    \"precommit_reject_total\": %uA,\n"
        "    \"budget_exceeded_total\": %uA,\n"
        "    \"last_ttfb_ms\": %uA,\n"
        "    \"last_peak_memory_bytes\": %uA\n"
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
        "    \"accept\": %uA,\n"
        "    \"no_accept\": %uA,\n"
        "    \"conditional\": %uA,\n"
        "    \"compression_passthrough\": %uA\n"
        "  },\n"
        "  \"failopen_count\": %uA,\n"
        "  \"delivery_count\": %uA,\n"
        "  \"decision_count\": %uA,\n"
        "  \"estimated_token_savings\": %uA,\n"
        "  \"parse_timeouts_total\": %uA,\n"
        "  \"parse_budget_exceeded_total\": %uA,\n"
        "  \"per_path\": {\n"
        "    \"paths\": [\n",
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
        snapshot->streaming.budget_exceeded_total,
        snapshot->streaming.last_ttfb_ms,
        snapshot->streaming.last_peak_memory_bytes,
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
        snapshot->skips.no_accept,
        snapshot->skips.conditional,
        snapshot->skips.compression_passthrough,
        snapshot->results.failopen_count,
        snapshot->results.delivery_count,
        snapshot->results.decision_count,
        snapshot->results.estimated_token_savings,
        snapshot->results.parse_interrupts.parse_timeouts_total,
        snapshot->results.parse_interrupts.parse_budget_exceeded_total);
}

static void
ngx_http_markdown_collect_v1_latency_snapshot(
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    const ngx_http_markdown_metrics_t *metrics)
{
    snapshot->conversion_latency_v1.full_buffer.le_1ms =
        metrics->conversion_latency_v1.full_buffer.le_1ms;
    snapshot->conversion_latency_v1.full_buffer.le_5ms =
        metrics->conversion_latency_v1.full_buffer.le_5ms;
    snapshot->conversion_latency_v1.full_buffer.le_10ms =
        metrics->conversion_latency_v1.full_buffer.le_10ms;
    snapshot->conversion_latency_v1.full_buffer.le_25ms =
        metrics->conversion_latency_v1.full_buffer.le_25ms;
    snapshot->conversion_latency_v1.full_buffer.le_50ms =
        metrics->conversion_latency_v1.full_buffer.le_50ms;
    snapshot->conversion_latency_v1.full_buffer.le_100ms =
        metrics->conversion_latency_v1.full_buffer.le_100ms;
    snapshot->conversion_latency_v1.full_buffer.le_250ms =
        metrics->conversion_latency_v1.full_buffer.le_250ms;
    snapshot->conversion_latency_v1.full_buffer.le_500ms =
        metrics->conversion_latency_v1.full_buffer.le_500ms;
    snapshot->conversion_latency_v1.full_buffer.le_1000ms =
        metrics->conversion_latency_v1.full_buffer.le_1000ms;
    snapshot->conversion_latency_v1.full_buffer.le_5000ms =
        metrics->conversion_latency_v1.full_buffer.le_5000ms;
    snapshot->conversion_latency_v1.full_buffer.sum_ms =
        metrics->conversion_latency_v1.full_buffer.sum_ms;
    snapshot->conversion_latency_v1.full_buffer.count =
        metrics->conversion_latency_v1.full_buffer.count;
    snapshot->conversion_latency_v1.streaming.le_1ms =
        metrics->conversion_latency_v1.streaming.le_1ms;
    snapshot->conversion_latency_v1.streaming.le_5ms =
        metrics->conversion_latency_v1.streaming.le_5ms;
    snapshot->conversion_latency_v1.streaming.le_10ms =
        metrics->conversion_latency_v1.streaming.le_10ms;
    snapshot->conversion_latency_v1.streaming.le_25ms =
        metrics->conversion_latency_v1.streaming.le_25ms;
    snapshot->conversion_latency_v1.streaming.le_50ms =
        metrics->conversion_latency_v1.streaming.le_50ms;
    snapshot->conversion_latency_v1.streaming.le_100ms =
        metrics->conversion_latency_v1.streaming.le_100ms;
    snapshot->conversion_latency_v1.streaming.le_250ms =
        metrics->conversion_latency_v1.streaming.le_250ms;
    snapshot->conversion_latency_v1.streaming.le_500ms =
        metrics->conversion_latency_v1.streaming.le_500ms;
    snapshot->conversion_latency_v1.streaming.le_1000ms =
        metrics->conversion_latency_v1.streaming.le_1000ms;
    snapshot->conversion_latency_v1.streaming.le_5000ms =
        metrics->conversion_latency_v1.streaming.le_5000ms;
    snapshot->conversion_latency_v1.streaming.sum_ms =
        metrics->conversion_latency_v1.streaming.sum_ms;
    snapshot->conversion_latency_v1.streaming.count =
        metrics->conversion_latency_v1.streaming.count;
}

static void
ngx_http_markdown_collect_decompression_snapshot(
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    const ngx_http_markdown_metrics_t *metrics)
{
    snapshot->decompressions.attempted = metrics->decompressions.attempted;
    snapshot->decompressions.succeeded = metrics->decompressions.succeeded;
    snapshot->decompressions.failed = metrics->decompressions.failed;
    snapshot->decompressions.gzip = metrics->decompressions.gzip;
    snapshot->decompressions.deflate = metrics->decompressions.deflate;
    snapshot->decompressions.brotli = metrics->decompressions.brotli;
    snapshot->decompressions.budget_exceeded_total =
        metrics->decompressions.budget_exceeded_total;
    snapshot->decompressions.format_error_total =
        metrics->decompressions.format_error_total;
    snapshot->decompressions.truncated_input_total =
        metrics->decompressions.truncated_input_total;
    snapshot->decompressions.io_error_total =
        metrics->decompressions.io_error_total;
    snapshot->decompressions.gzip_failures.budget =
        metrics->decompressions.gzip_failures.budget;
    snapshot->decompressions.gzip_failures.format =
        metrics->decompressions.gzip_failures.format;
    snapshot->decompressions.gzip_failures.truncated =
        metrics->decompressions.gzip_failures.truncated;
    snapshot->decompressions.gzip_failures.io =
        metrics->decompressions.gzip_failures.io;
    snapshot->decompressions.deflate_failures.budget =
        metrics->decompressions.deflate_failures.budget;
    snapshot->decompressions.deflate_failures.format =
        metrics->decompressions.deflate_failures.format;
    snapshot->decompressions.deflate_failures.truncated =
        metrics->decompressions.deflate_failures.truncated;
    snapshot->decompressions.deflate_failures.io =
        metrics->decompressions.deflate_failures.io;
    snapshot->decompressions.brotli_failures.budget =
        metrics->decompressions.brotli_failures.budget;
    snapshot->decompressions.brotli_failures.format =
        metrics->decompressions.brotli_failures.format;
    snapshot->decompressions.brotli_failures.truncated =
        metrics->decompressions.brotli_failures.truncated;
    snapshot->decompressions.brotli_failures.io =
        metrics->decompressions.brotli_failures.io;
}

static void
ngx_http_markdown_collect_core_snapshot(
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    const ngx_http_markdown_metrics_t *metrics)
{
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
    snapshot->conversion_latency.le_10ms = metrics->conversion_latency.le_10ms;
    snapshot->conversion_latency.le_100ms =
        metrics->conversion_latency.le_100ms;
    snapshot->conversion_latency.le_1000ms =
        metrics->conversion_latency.le_1000ms;
    snapshot->conversion_latency.gt_1000ms =
        metrics->conversion_latency.gt_1000ms;
    ngx_http_markdown_collect_v1_latency_snapshot(snapshot, metrics);
    ngx_http_markdown_collect_decompression_snapshot(snapshot, metrics);
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
    snapshot->skips.no_accept = metrics->skips.no_accept;
    snapshot->skips.conditional = metrics->skips.conditional;
    snapshot->skips.compression_passthrough =
        metrics->skips.compression_passthrough;
}

#ifdef MARKDOWN_STREAMING_ENABLED
static void
ngx_http_markdown_collect_streaming_snapshot(
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    const ngx_http_markdown_metrics_t *metrics)
{
    snapshot->streaming.requests_total = metrics->streaming.requests_total;
    snapshot->streaming.fallback_total = metrics->streaming.fallback_total;
    snapshot->streaming.succeeded_total = metrics->streaming.succeeded_total;
    snapshot->streaming.commit_total = metrics->streaming.commit_total;
    snapshot->streaming.failed_total = metrics->streaming.failed_total;
    snapshot->streaming.postcommit_error_total =
        metrics->streaming.postcommit_error_total;
    snapshot->streaming.precommit_failopen_total =
        metrics->streaming.precommit_failopen_total;
    snapshot->streaming.precommit_reject_total =
        metrics->streaming.precommit_reject_total;
    snapshot->streaming.budget_exceeded_total =
        metrics->streaming.budget_exceeded_total;
#ifdef MARKDOWN_STREAMING_SHADOW_DEBUG
    snapshot->streaming.shadow_total = metrics->streaming.shadow_total;
    snapshot->streaming.shadow_diff_total =
        metrics->streaming.shadow_diff_total;
#endif
    snapshot->streaming.last_ttfb_ms = metrics->streaming.last_ttfb_ms;
    snapshot->streaming.last_peak_memory_bytes =
        metrics->streaming.last_peak_memory_bytes;
    snapshot->streaming.engine_choice.streaming =
        metrics->streaming.engine_choice.streaming;
    snapshot->streaming.engine_choice.full_buffer =
        metrics->streaming.engine_choice.full_buffer;
    snapshot->streaming.engine_choice.passthrough =
        metrics->streaming.engine_choice.passthrough;
    snapshot->streaming.engine_choice.not_eligible =
        metrics->streaming.engine_choice.not_eligible;
    snapshot->streaming.streaming_fallback_precommit_pass =
        metrics->streaming.streaming_fallback_precommit_pass;
    snapshot->streaming.streaming_fallback_precommit_reject =
        metrics->streaming.streaming_fallback_precommit_reject;
    snapshot->streaming.streaming_failure_postcommit_abort =
        metrics->streaming.streaming_failure_postcommit_abort;
    snapshot->streaming.streaming_failure_postcommit_safe_finish =
        metrics->streaming.streaming_failure_postcommit_safe_finish;
    snapshot->streaming.terminal_aborted_total =
        metrics->streaming.terminal_aborted_total;
    snapshot->streaming.selection.candidate_total =
        metrics->streaming.selection.candidate_total;
    snapshot->streaming.selection.true_streaming_selected_total =
        metrics->streaming.selection.true_streaming_selected_total;
    snapshot->streaming.selection.output_bytes_total =
        metrics->streaming.selection.output_bytes_total;
    snapshot->streaming.selection.excluded_content_type_total =
        metrics->streaming.selection.excluded_content_type_total;
}
#endif

static void
ngx_http_markdown_collect_result_snapshot(
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    const ngx_http_markdown_metrics_t *metrics)
{
    snapshot->results.failopen_count = metrics->results.failopen_count;
    snapshot->results.delivery_count = metrics->results.delivery_count;
    snapshot->results.full_buffer_delivery_count =
        metrics->results.full_buffer_delivery_count;
    snapshot->results.decision_count = metrics->results.decision_count;
    snapshot->results.estimated_token_savings =
        metrics->results.estimated_token_savings;
    snapshot->results.parse_interrupts.parse_timeouts_total =
        metrics->results.parse_interrupts.parse_timeouts_total;
    snapshot->results.parse_interrupts.parse_budget_exceeded_total =
        metrics->results.parse_interrupts.parse_budget_exceeded_total;
    snapshot->results.replay_buffer_errors_total =
        metrics->results.replay_buffer_errors_total;
    snapshot->results.dynconf_reloads.success =
        metrics->results.dynconf_reloads.success;
    snapshot->results.dynconf_reloads.failure_schema_version =
        metrics->results.dynconf_reloads.failure_schema_version;
    snapshot->results.dynconf_reloads.failure_unknown_key =
        metrics->results.dynconf_reloads.failure_unknown_key;
    snapshot->results.dynconf_reloads.failure_duplicate_key =
        metrics->results.dynconf_reloads.failure_duplicate_key;
    snapshot->results.dynconf_reloads.failure_invalid_type =
        metrics->results.dynconf_reloads.failure_invalid_type;
    snapshot->results.dynconf_reloads.failure_out_of_range =
        metrics->results.dynconf_reloads.failure_out_of_range;
    snapshot->results.dynconf_reloads.failure_size_exceeded =
        metrics->results.dynconf_reloads.failure_size_exceeded;
    snapshot->results.dynconf_reloads.failure_parse_error =
        metrics->results.dynconf_reloads.failure_parse_error;
    snapshot->results.dynconf_reloads.failure_file_error =
        metrics->results.dynconf_reloads.failure_file_error;
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
    snapshot->per_path.path_entries = metrics->per_path.path_entries;
    snapshot->per_path.path_conversions = metrics->per_path.path_conversions;
    snapshot->per_path.path_conversion_time_sum_ms =
        metrics->per_path.path_conversion_time_sum_ms;
    snapshot->per_path.overflow_count = metrics->per_path.overflow_count;
    snapshot->per_path.unretained_conversions =
        metrics->per_path.unretained_conversions;
    snapshot->per_path.unretained_conversion_time_sum_ms =
        metrics->per_path.unretained_conversion_time_sum_ms;
#endif
}

static void
ngx_http_markdown_collect_performance_snapshot(
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    const ngx_http_markdown_metrics_t *metrics)
{
    snapshot->perf.inflight.current =
        (ngx_atomic_uint_t) ngx_http_markdown_inflight_current();
    snapshot->perf.inflight.high_watermark =
        (ngx_atomic_uint_t) ngx_http_markdown_inflight_high_watermark();
    snapshot->perf.inflight.overload_total =
        (ngx_atomic_uint_t) ngx_http_markdown_inflight_overload_total();
    snapshot->perf.backpressure_total = metrics->perf.backpressure_total;
    snapshot->perf.backpressure_resume_total =
        metrics->perf.backpressure_resume_total;
    snapshot->perf.pending_output_high_watermark_bytes =
        metrics->perf.pending_output_high_watermark_bytes;
    snapshot->perf.decompression_streaming_total =
        metrics->perf.decompression_streaming_total;
    snapshot->perf.decompression_fullbuffer_total =
        metrics->perf.decompression_fullbuffer_total;
    snapshot->perf.decompression_budget_exceeded_total =
        metrics->perf.decompression_budget_exceeded_total;
    snapshot->perf.zero_copy_output_total = metrics->perf.zero_copy_output_total;
    snapshot->perf.copied_output_total = metrics->perf.copied_output_total;
}

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

    ngx_http_markdown_collect_core_snapshot(snapshot, metrics);
    ngx_http_markdown_collect_result_snapshot(snapshot, metrics);
#ifdef MARKDOWN_STREAMING_ENABLED
    ngx_http_markdown_collect_streaming_snapshot(snapshot, metrics);
#endif

    ngx_http_markdown_collect_performance_snapshot(snapshot, metrics);
}


/* Map one source engine's frozen latency bands into the v1 histogram. */
static void
ngx_http_markdown_metrics_map_v1_histogram(
    const ngx_http_markdown_metrics_v1_source_histogram_t *source,
    ngx_http_markdown_metrics_v1_histogram_t *destination)
{
    if (source == NULL || destination == NULL) {
        return;
    }

    destination->buckets[0] = source->le_1ms;
    destination->buckets[1] = source->le_5ms;
    destination->buckets[2] = source->le_10ms;
    destination->buckets[3] = source->le_25ms;
    destination->buckets[4] = source->le_50ms;
    destination->buckets[5] = source->le_100ms;
    destination->buckets[6] = source->le_250ms;
    destination->buckets[7] = source->le_500ms;
    destination->buckets[8] = source->le_1000ms;
    destination->buckets[9] = source->le_5000ms;
    destination->sum_us = ngx_http_markdown_metrics_ms_to_us(source->sum_ms);
    destination->count = source->count;
}

/*
 * Translate the legacy storage snapshot into the frozen v1 metric contract.
 * The mapping keeps public counters stable while preserving streaming-only
 * fields behind their feature guard and leaves absent snapshots zeroed.
 */
static void
ngx_http_markdown_metrics_to_v1(
    const ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_http_markdown_metrics_v1_snapshot_t *v1)
{
    ngx_atomic_uint_t  failed_closed;
    ngx_atomic_uint_t  latency_count;

    if (v1 == NULL) {
        return;
    }
    ngx_memzero(v1, sizeof(ngx_http_markdown_metrics_v1_snapshot_t));
    if (snapshot == NULL) {
        return;
    }

    /* Request outcomes: subtract fail-open responses from total failures. */
    v1->requests.converted = snapshot->conversions_succeeded;
    v1->requests.skipped_not_eligible = snapshot->skips.method
        + snapshot->skips.status
        + snapshot->skips.content_type
        + snapshot->skips.size
        + snapshot->skips.streaming
        + snapshot->skips.auth
        + snapshot->skips.range;
    v1->requests.skipped_accept = snapshot->skips.accept;
    v1->requests.skipped_no_accept = snapshot->skips.no_accept;
    v1->requests.skipped_conditional = snapshot->skips.conditional;
    v1->requests.skipped_disabled = snapshot->skips.config;
    v1->requests.skipped_bypass_no_transform =
        snapshot->skips.compression_passthrough;
    v1->requests.failed_open = snapshot->results.failopen_count;
#ifdef MARKDOWN_STREAMING_ENABLED
    /* Subtract each classified outcome independently to avoid overflow in
     * the sum of deductions before applying the saturating floor. */
    failed_closed = snapshot->conversions_failed
        >= snapshot->results.failopen_count
        ? snapshot->conversions_failed - snapshot->results.failopen_count
        : 0;
    failed_closed = failed_closed
        >= snapshot->streaming.terminal_aborted_total
        ? failed_closed - snapshot->streaming.terminal_aborted_total
        : 0;
    v1->requests.aborted =
        snapshot->streaming.terminal_aborted_total;
#else
    failed_closed = snapshot->conversions_failed >= snapshot->results.failopen_count
        ? snapshot->conversions_failed - snapshot->results.failopen_count : 0;
    v1->requests.aborted = 0;
#endif
    v1->requests.failed_closed = failed_closed;

    /* Attempts and deliveries use the frozen engine-specific counters. */
    v1->attempts.full_buffer = snapshot->path_hits.fullbuffer
        + snapshot->path_hits.incremental;
#ifdef MARKDOWN_STREAMING_ENABLED
    v1->attempts.streaming = snapshot->path_hits.streaming;
#endif
    v1->deliveries.full_buffer = snapshot->results.full_buffer_delivery_count;
#ifdef MARKDOWN_STREAMING_ENABLED
    v1->deliveries.streaming = snapshot->streaming.succeeded_total;
#endif

    ngx_http_markdown_metrics_map_v1_histogram(
        &snapshot->conversion_latency_v1.full_buffer,
        &v1->duration_full_buffer);
    ngx_http_markdown_metrics_map_v1_histogram(
        &snapshot->conversion_latency_v1.streaming,
        &v1->duration_streaming);
    latency_count = v1->duration_full_buffer.count
        + v1->duration_streaming.count;
    if (latency_count == 0) {
        v1->duration_full_buffer.buckets[2] =
            snapshot->conversion_latency.le_10ms;
        v1->duration_full_buffer.buckets[5] =
            snapshot->conversion_latency.le_100ms;
        v1->duration_full_buffer.buckets[8] =
            snapshot->conversion_latency.le_1000ms;
        v1->duration_full_buffer.sum_us =
            ngx_http_markdown_metrics_ms_to_us(
                snapshot->conversion_time_sum_ms);
        v1->duration_full_buffer.count = snapshot->conversions_succeeded
            + snapshot->conversions_failed;
    }
    /* Preserve byte and inflight gauges after the histogram conversion. */
    v1->input_bytes = snapshot->input_bytes;
    v1->output_bytes = snapshot->output_bytes;
#ifdef MARKDOWN_STREAMING_ENABLED
    v1->output_bytes += snapshot->streaming.selection.output_bytes_total;
    v1->streaming_peak_memory_bytes =
        snapshot->streaming.last_peak_memory_bytes;
#endif
    v1->inflight = snapshot->perf.inflight.current;

#ifdef MARKDOWN_STREAMING_ENABLED
    v1->streaming_events.commit = snapshot->streaming.commit_total;
    v1->streaming_events.fallback = snapshot->streaming.fallback_total;
    v1->streaming_events.safe_finish_start =
        snapshot->streaming.streaming_failure_postcommit_safe_finish;
    v1->streaming_events.abort_start =
        snapshot->streaming.streaming_failure_postcommit_abort;
    v1->streaming_events.resume_success =
        snapshot->perf.backpressure_resume_total;
    /*
     * resume_failure counts streaming failures that reached a resume
     * attempt.  failed_total includes post-commit aborts (delivered then
     * aborted), which the v1 renderer reclassifies into requests.aborted
     * (metrics_impl.h:841-846) and must not inflate resume_failure.
     * Subtract the same terminal_aborted_total with a saturating floor,
     * mirroring the failed_closed deduction above.
     */
    v1->streaming_events.resume_failure = snapshot->streaming.failed_total
        >= snapshot->streaming.terminal_aborted_total
        ? snapshot->streaming.failed_total - snapshot->streaming.terminal_aborted_total
        : 0;
#endif

    v1->decompression.gzip_success = snapshot->decompressions.gzip;
    v1->decompression.gzip_failure_budget =
        snapshot->decompressions.gzip_failures.budget;
    v1->decompression.gzip_failure_format =
        snapshot->decompressions.gzip_failures.format;
    v1->decompression.gzip_failure_truncated =
        snapshot->decompressions.gzip_failures.truncated;
    v1->decompression.gzip_failure_io =
        snapshot->decompressions.gzip_failures.io;
    v1->decompression.deflate_success = snapshot->decompressions.deflate;
    v1->decompression.deflate_failure_budget =
        snapshot->decompressions.deflate_failures.budget;
    v1->decompression.deflate_failure_format =
        snapshot->decompressions.deflate_failures.format;
    v1->decompression.deflate_failure_truncated =
        snapshot->decompressions.deflate_failures.truncated;
    v1->decompression.deflate_failure_io =
        snapshot->decompressions.deflate_failures.io;
    v1->decompression.brotli_success = snapshot->decompressions.brotli;
    v1->decompression.brotli_failure_budget =
        snapshot->decompressions.brotli_failures.budget;
    v1->decompression.brotli_failure_format =
        snapshot->decompressions.brotli_failures.format;
    v1->decompression.brotli_failure_truncated =
        snapshot->decompressions.brotli_failures.truncated;
    v1->decompression.brotli_failure_io =
        snapshot->decompressions.brotli_failures.io;

    /* Dynconf counters are copied without reinterpreting their failure axes. */
    v1->dynconf_reloads.success = snapshot->results.dynconf_reloads.success;
    v1->dynconf_reloads.failure_schema_version =
        snapshot->results.dynconf_reloads.failure_schema_version;
    v1->dynconf_reloads.failure_unknown_key =
        snapshot->results.dynconf_reloads.failure_unknown_key;
    v1->dynconf_reloads.failure_duplicate_key =
        snapshot->results.dynconf_reloads.failure_duplicate_key;
    v1->dynconf_reloads.failure_invalid_type =
        snapshot->results.dynconf_reloads.failure_invalid_type;
    v1->dynconf_reloads.failure_out_of_range =
        snapshot->results.dynconf_reloads.failure_out_of_range;
    v1->dynconf_reloads.failure_size_exceeded =
        snapshot->results.dynconf_reloads.failure_size_exceeded;
    v1->dynconf_reloads.failure_parse_error =
        snapshot->results.dynconf_reloads.failure_parse_error;
    v1->dynconf_reloads.failure_file_error =
        snapshot->results.dynconf_reloads.failure_file_error;

    /* Build metadata is part of the public v1 response contract. */
    v1->build_info.version = (const u_char *) "0.9.2";
    v1->build_info.nginx_version_text = (const u_char *) NGINX_VERSION;
#ifdef MARKDOWN_STREAMING_ENABLED
    v1->build_info.features = (const u_char *) "streaming";
#else
    v1->build_info.features = (const u_char *) "";
#endif
}

/*
 * Enforce the metrics endpoint's loopback-only peer boundary before method
 * handling. NGINX `allow`/`deny` rules can further restrict that location,
 * but they cannot broaden access beyond localhost.
 */
static ngx_int_t
ngx_http_markdown_metrics_check_access(ngx_http_request_t *r)
{
    if (r == NULL || r->connection == NULL
        || r->connection->sockaddr == NULL)
    {
        return NGX_HTTP_FORBIDDEN;
    }

    if (r->connection->sockaddr->sa_family == AF_INET) {
        const struct sockaddr_in *sin =
            (const struct sockaddr_in *) r->connection->sockaddr;
        if (ntohl(sin->sin_addr.s_addr) != INADDR_LOOPBACK) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown: access denied from non-localhost IPv4 address");
            return NGX_HTTP_FORBIDDEN;
        }
    }
#if (NGX_HAVE_INET6)
    else if (r->connection->sockaddr->sa_family == AF_INET6) {
        const struct sockaddr_in6 *sin6 =
            (const struct sockaddr_in6 *) r->connection->sockaddr;
        if (!IN6_IS_ADDR_LOOPBACK(&sin6->sin6_addr)) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown: access denied from non-localhost IPv6 address");
            return NGX_HTTP_FORBIDDEN;
        }
    }
#endif
    else {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: access denied from unknown address family");
        return NGX_HTTP_FORBIDDEN;
    }

    return NGX_OK;
}


/*
 * Validate method and shared-state availability for the metrics handler.
 */
static ngx_int_t
ngx_http_markdown_metrics_validate_request(ngx_http_request_t *r)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_metrics_check_access(r);
    if (rc != NGX_OK) {
        return rc;
    }

    if (!(r->method & (NGX_HTTP_GET|NGX_HTTP_HEAD))) {
        return NGX_HTTP_NOT_ALLOWED;
    }

    if (ngx_http_markdown_metrics == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: shared metrics state unavailable");
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    return NGX_OK;
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
#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
typedef struct {
    u_char             *pos;
    u_char             *end;
    size_t              tail_reserve;
    ngx_atomic_uint_t   omitted_conversions;
    ngx_atomic_uint_t   omitted_entries;
    ngx_atomic_uint_t   omitted_time_ms;
    ngx_uint_t          omitted_nodes;
    ngx_uint_t          entries_written;
    ngx_flag_t          failed;
} ngx_http_markdown_path_detail_render_ctx_t;

static u_char *
ngx_http_markdown_json_walk_path_tree_bounded(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    ngx_http_markdown_path_detail_render_ctx_t *render);

static u_char *
ngx_http_markdown_text_walk_path_tree_bounded(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    ngx_http_markdown_path_detail_render_ctx_t *render);

static u_char *
ngx_http_markdown_json_write_path_details(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_snapshot_t *snapshot);

static u_char *
ngx_http_markdown_text_write_path_details(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_snapshot_t *snapshot);

static size_t ngx_http_markdown_json_path_entry_size(size_t path_len);
static size_t ngx_http_markdown_text_path_entry_size(size_t path_len);
static size_t ngx_http_markdown_json_other_entry_size(void);
static size_t ngx_http_markdown_text_other_entry_size(void);
static size_t ngx_http_markdown_json_tail_reserve(void);
static size_t ngx_http_markdown_text_tail_reserve(void);
#endif


#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
static ngx_atomic_uint_t
ngx_http_markdown_metrics_saturating_add(
    ngx_atomic_uint_t left, ngx_atomic_uint_t right)
{
    if (((ngx_atomic_uint_t) -1) - left < right) {
        return (ngx_atomic_uint_t) -1;
    }

    return left + right;
}

static size_t
ngx_http_markdown_metrics_saturating_size_add(size_t left, size_t right)
{
    if (((size_t) -1) - left < right) {
        return (size_t) -1;
    }

    return left + right;
}
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
    p = ngx_http_markdown_metrics_write_json_conversion(
        p, end, snapshot, conversions_completed,
        conversion_time_avg_ms, input_bytes_avg, output_bytes_avg);
    p = ngx_http_markdown_metrics_write_json_routing(p, end, snapshot);

#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
    p = ngx_http_markdown_json_write_path_details(p, end, snapshot);
    if (p == NULL) {
        return NULL;
    }
#endif /* NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED */

    p = ngx_slprintf(p, end, "\n    ]\n  },\n");
    p = ngx_http_markdown_metrics_write_json_perf(
        p, end, &snapshot->perf);
    return ngx_slprintf(p, end, "}");
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
ngx_http_markdown_metrics_write_text_perf(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_perf_snapshot_t *perf)
{
    return ngx_slprintf(p, end,
        "\n"
        "Performance Metrics:\n"
        "- Backpressure Total: %uA\n"
        "- Backpressure Resume Total: %uA\n"
        "- Pending Output High Watermark (bytes): %uA\n"
        "- Decompression Streaming Total: %uA\n"
        "- Decompression Full-Buffer Total: %uA\n"
        "- Decompression Budget Exceeded Total: %uA\n"
        "- Zero-Copy Output Total: %uA\n"
        "- Copied Output Total: %uA\n",
        perf->backpressure_total,
        perf->backpressure_resume_total,
        perf->pending_output_high_watermark_bytes,
        perf->decompression_streaming_total,
        perf->decompression_fullbuffer_total,
        perf->decompression_budget_exceeded_total,
        perf->zero_copy_output_total,
        perf->copied_output_total);
}


static u_char *
ngx_http_markdown_metrics_write_text_summary(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_snapshot_t *snapshot,
    ngx_atomic_uint_t conversions_completed,
    ngx_atomic_uint_t conversion_time_avg_ms,
    ngx_atomic_uint_t input_bytes_avg,
    ngx_atomic_uint_t output_bytes_avg)
{
    /*
     * Keep the text schema aligned with the snapshot fields and the JSON
     * renderer.  The callers reserve the complete response buffer before
     * entering this formatter, so ngx_slprintf may only advance to `end`.
     */
    return ngx_slprintf(p, end,
        "Markdown Filter Metrics\n"
        "=======================\n"
        "Conversions Attempted: %uA\n"
        "Conversions Succeeded: %uA\n"
        "Conversions Failed: %uA\n"
        "Conversions Bypassed: %uA\n"
        "Conversions Completed: %uA\n\n"
        "Failure Breakdown:\n"
        "- Conversion Errors: %uA\n"
        "- Resource Limit Exceeded: %uA\n"
        "- System Errors: %uA\n\n"
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
        "- Latency > 1000ms: %uA\n\n"
        "Decompression Statistics:\n"
        "- Decompressions Attempted: %uA\n"
        "- Decompressions Succeeded: %uA\n"
        "- Decompressions Failed: %uA\n"
        "- Gzip Decompressions: %uA\n"
        "- Deflate Decompressions: %uA\n"
        "- Brotli Decompressions: %uA\n"
        "- Decompression Budget Exceeded: %uA\n"
        "- Decompression Format Errors: %uA\n"
        "- Decompression Truncated Input: %uA\n"
        "- Decompression I/O Errors: %uA\n"
        "- Replay Buffer Errors: %uA\n\n"
        "Path Routing:\n"
        "- Full-Buffer Path Hits: %uA\n"
        "- Incremental Path Hits: %uA\n"
#ifdef MARKDOWN_STREAMING_ENABLED
        "- Streaming Path Hits: %uA\n\n"
        "Streaming:\n"
        "- Streaming Requests Total: %uA\n"
        "- Streaming Fallback Total: %uA\n"
        "- Streaming Succeeded Total: %uA\n"
        "- Streaming Failed Total: %uA\n"
        "- Streaming Post-Commit Errors: %uA\n"
        "- Streaming Pre-Commit Fail-Open: %uA\n"
        "- Streaming Pre-Commit Reject: %uA\n"
        "- Streaming Budget Exceeded: %uA\n"
        "- Streaming Last TTFB (ms): %uA\n"
        "- Streaming Peak Memory (bytes): %uA\n"
#endif
        "\n",
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
        snapshot->conversion_latency.le_10ms,
        snapshot->conversion_latency.le_100ms,
        snapshot->conversion_latency.le_1000ms,
        snapshot->conversion_latency.gt_1000ms,
        snapshot->decompressions.attempted,
        snapshot->decompressions.succeeded,
        snapshot->decompressions.failed,
        snapshot->decompressions.gzip,
        snapshot->decompressions.deflate,
        snapshot->decompressions.brotli,
        snapshot->decompressions.budget_exceeded_total,
        snapshot->decompressions.format_error_total,
        snapshot->decompressions.truncated_input_total,
        snapshot->decompressions.io_error_total,
        snapshot->results.replay_buffer_errors_total,
        snapshot->path_hits.fullbuffer,
        snapshot->path_hits.incremental
#ifdef MARKDOWN_STREAMING_ENABLED
        ,
        snapshot->path_hits.streaming,
        snapshot->streaming.requests_total,
        snapshot->streaming.fallback_total,
        snapshot->streaming.succeeded_total,
        snapshot->streaming.failed_total,
        snapshot->streaming.postcommit_error_total,
        snapshot->streaming.precommit_failopen_total,
        snapshot->streaming.precommit_reject_total,
        snapshot->streaming.budget_exceeded_total,
        snapshot->streaming.last_ttfb_ms,
        snapshot->streaming.last_peak_memory_bytes
#endif
        );
}

static u_char *
ngx_http_markdown_metrics_write_text_decision(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_snapshot_t *snapshot)
{
    return ngx_slprintf(p, end,
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
        "- Skips (No Accept): %uA\n"
        "- Skips (Conditional): %uA\n"
        "- Skips (Compression Passthrough): %uA\n"
        "- Fail-Open Count: %uA\n"
        "- Delivery Count: %uA\n"
        "- Decision Count: %uA\n"
        "- Estimated Token Savings: %uA\n"
        "- Parse Timeouts Total: %uA\n"
        "- Parse Budget Exceeded Total: %uA\n",
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
        snapshot->skips.no_accept,
        snapshot->skips.conditional,
        snapshot->skips.compression_passthrough,
        snapshot->results.failopen_count,
        snapshot->results.delivery_count,
        snapshot->results.decision_count,
        snapshot->results.estimated_token_savings,
        snapshot->results.parse_interrupts.parse_timeouts_total,
        snapshot->results.parse_interrupts.parse_budget_exceeded_total);
}

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
    p = ngx_http_markdown_metrics_write_text_summary(
        p, end, snapshot, conversions_completed,
        conversion_time_avg_ms, input_bytes_avg, output_bytes_avg);
    p = ngx_http_markdown_metrics_write_text_decision(p, end, snapshot);

    p = ngx_http_markdown_metrics_write_text_perf(p, end, &snapshot->perf);
#if NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED
    p = ngx_http_markdown_text_write_path_details(p, end, snapshot);
    if (p == NULL) {
        return NULL;
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
 * Bounded rendering context for per-path detail output.
 *
 * Shared by the bounded Prometheus renderer to implement graceful
 * degradation when the output buffer cannot accommodate all
 * per-path entries.  Omitted entries are aggregated into an
 * __other__ pseudo-entry so the response always remains
 * syntactically complete and the endpoint returns HTTP 200.
 *
 * The typedef is placed at file scope (before the write functions)
 * so it is visible to both the forward-declaration block and the
 * bounded walk function definitions below.
 */


/*
 * Estimate the maximum encoded size of one JSON per-path entry,
 * including the trailing comma, for a path of the given length.
 * Uses a conservative upper bound that accounts for JSON escaping
 * (each byte may expand to \uXXXX = 6 bytes) and the maximum
 * decimal digit count for ngx_atomic_uint_t counters.
 */
static size_t
ngx_http_markdown_json_path_entry_size(size_t path_len)
{
    size_t  fixed_size;
    size_t  max_escaped;
    size_t  max_digits;

    if (path_len > ((size_t) -1) / 6) {
        return (size_t) -1;
    }

    max_escaped = path_len * 6;
    max_digits = 20 * 3;
    fixed_size = sizeof("\n      {\"path\": \"\", \"conversions\": , "
                        "\"entries\": , \"conversion_time_sum_ms\": },") - 1;

    if (max_escaped > ((size_t) -1) - fixed_size - max_digits) {
        return (size_t) -1;
    }

    return fixed_size + max_escaped + max_digits;
}


/*
 * Estimate the maximum encoded size of one plain-text per-path line
 * for a path of the given length.
 */
static size_t
ngx_http_markdown_text_path_entry_size(size_t path_len)
{
    size_t  fixed_size;
    size_t  max_escaped;
    size_t  max_digits;

    if (path_len > ((size_t) -1) / 6) {
        return (size_t) -1;
    }

    max_escaped = path_len * 6;
    max_digits = 20 * 3;
    fixed_size = sizeof("- Path[]: conversions= entries= time_ms=\n") - 1;

    if (max_escaped > ((size_t) -1) - fixed_size - max_digits) {
        return (size_t) -1;
    }

    return fixed_size + max_escaped + max_digits;
}

static ngx_int_t
ngx_http_markdown_path_len_to_size(
    const ngx_http_markdown_path_metric_node_t *node,
    size_t *path_len)
{
    if (node == NULL || path_len == NULL
        || node->path_len > NGX_MAX_SIZE_T_VALUE)
    {
        return NGX_ERROR;
    }

    *path_len = (size_t) node->path_len;
    return NGX_OK;
}


/*
 * Estimate the maximum encoded size of the __other__ JSON entry.
 */
static size_t
ngx_http_markdown_json_other_entry_size(void)
{
    return sizeof("\n      {\"path\":\"__other__\","
                  "\"conversions\":,"
                  "\"conversion_time_sum_ms\":,"
                  "\"entries\":}") - 1
        + 3 * 20;
}


/*
 * Estimate the maximum encoded size of the __other__ plain-text line.
 */
static size_t
ngx_http_markdown_text_other_entry_size(void)
{
    return sizeof("- Path[__other__]: conversions= entries= time_ms=\n") - 1
        + 3 * 20;
}


static size_t
ngx_http_markdown_json_perf_section_size(void)
{
    return NGX_HTTP_MARKDOWN_JSON_PERF_MAX_SIZE;
}

static size_t
ngx_http_markdown_json_closing_brace_size(void)
{
    return sizeof("}\n") - 1;
}

/*
 * Estimate the tail reserve needed for JSON: closing the paths array,
 * the per_path object, the perf section, and the outer closing brace.
 */
static size_t
ngx_http_markdown_json_tail_reserve(void)
{
    return sizeof("\n    ]\n  },\n") - 1
           + ngx_http_markdown_json_other_entry_size()
           + ngx_http_markdown_json_perf_section_size()
           + ngx_http_markdown_json_closing_brace_size();
}

/*
 * Estimate the tail reserve needed for plain-text: the __other__ line
 * plus exact perf section and trailing newline.
 */
static size_t
ngx_http_markdown_text_tail_reserve(void)
{
    return ngx_http_markdown_text_other_entry_size()
           + sizeof("\nPerformance Metrics:\n"
                    "- Backpressure Total: \n"
                    "- Backpressure Resume Total: \n"
                    "- Pending Output High Watermark (bytes): \n"
                    "- Decompression Streaming Total: \n"
                    "- Decompression Full-Buffer Total: \n"
                    "- Decompression Budget Exceeded Total: \n"
                    "- Zero-Copy Output Total: \n"
                    "- Copied Output Total: \n") - 1
           + 8 * 20;
}


static u_char *
ngx_http_markdown_json_walk_path_tree_bounded(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    ngx_http_markdown_path_detail_render_ctx_t *render)
{
    const ngx_http_markdown_path_metric_node_t  *pnode;
    size_t                                       path_len;
    size_t                                       needed;
    size_t                                       remaining;

    /*
     * In-order traversal preserves deterministic path output.  Every node
     * either fits before the reserved tail or is folded into __other__;
     * `render->failed` records malformed lengths for the caller.
     */
    if (node == sentinel || render->pos >= render->end) {
        return render->pos;
    }

    render->pos = ngx_http_markdown_json_walk_path_tree_bounded(
            node->left, sentinel, render);

    if (render->failed) {
        return render->pos;
    }

    remaining = (size_t) (render->end - render->pos);
    if (remaining > render->tail_reserve) {
        pnode = (const ngx_http_markdown_path_metric_node_t *) node;
        if (ngx_http_markdown_path_len_to_size(pnode, &path_len)
            != NGX_OK)
        {
            render->failed = 1;
            return render->pos;
        }
        needed = ngx_http_markdown_json_path_entry_size(path_len);

        if (needed <= remaining - render->tail_reserve) {
            u_char  *entry_start = render->pos;

            if (render->entries_written > 0) {
                render->pos = ngx_slprintf(render->pos, render->end, ",");
            }

            render->pos = ngx_slprintf(render->pos, render->end,
                "\n      {\"path\": \"");
            render->pos = ngx_http_markdown_escape_json_string(
                render->pos, render->end,
                pnode->path, pnode->path_len);
            render->pos = ngx_slprintf(render->pos, render->end,
                "\", \"conversions\": %uA, "
                "\"entries\": %uA, "
                "\"conversion_time_sum_ms\": %uA}",
                pnode->conversions,
                pnode->entries,
                pnode->conversion_time_sum_ms);

            if (render->pos < render->end) {
                render->entries_written++;
            } else {
                render->pos = entry_start;
                render->omitted_conversions =
                    ngx_http_markdown_metrics_saturating_add(
                        render->omitted_conversions,
                        pnode->conversions);
                render->omitted_entries =
                    ngx_http_markdown_metrics_saturating_add(
                        render->omitted_entries, pnode->entries);
                render->omitted_time_ms =
                    ngx_http_markdown_metrics_saturating_add(
                        render->omitted_time_ms,
                        pnode->conversion_time_sum_ms);
                render->omitted_nodes =
                    ngx_http_markdown_metrics_saturating_size_add(
                        render->omitted_nodes, 1);
            }
        } else {
            render->omitted_conversions =
                ngx_http_markdown_metrics_saturating_add(
                    render->omitted_conversions,
                    pnode->conversions);
            render->omitted_entries =
                ngx_http_markdown_metrics_saturating_add(
                    render->omitted_entries, pnode->entries);
            render->omitted_time_ms =
                ngx_http_markdown_metrics_saturating_add(
                    render->omitted_time_ms,
                    pnode->conversion_time_sum_ms);
            render->omitted_nodes =
                ngx_http_markdown_metrics_saturating_size_add(
                    render->omitted_nodes, 1);
        }
    } else {
        const ngx_http_markdown_path_metric_node_t  *pnode2;
        pnode2 = (const ngx_http_markdown_path_metric_node_t *) node;
        render->omitted_conversions = ngx_http_markdown_metrics_saturating_add(
            render->omitted_conversions, pnode2->conversions);
        render->omitted_entries = ngx_http_markdown_metrics_saturating_add(
            render->omitted_entries, pnode2->entries);
        render->omitted_time_ms = ngx_http_markdown_metrics_saturating_add(
            render->omitted_time_ms,
            pnode2->conversion_time_sum_ms);
        render->omitted_nodes = ngx_http_markdown_metrics_saturating_size_add(
            render->omitted_nodes, 1);
    }

    if (render->failed) {
        return render->pos;
    }

    render->pos = ngx_http_markdown_json_walk_path_tree_bounded(
            node->right, sentinel, render);

    return render->pos;
}


/*
 * Render per-path metrics in sorted order into the bounded text response.
 *
 * The in-order traversal preserves the tree's path ordering.  Each entry is
 * emitted only when it fits before the reserved summary tail; otherwise its
 * counters are accumulated in the omitted totals.  A failed write or a
 * recursive child failure stops the walk without advancing the output.
 *
 * Parameters:
 *   node      Current red-black tree node.
 *   sentinel  Tree sentinel that terminates the traversal.
 *   render    Bounded output state and omitted-entry accumulators.
 *
 * Returns:
 *   The current output position, or the unchanged position after failure.
 */
static u_char *
ngx_http_markdown_text_walk_path_tree_bounded(
    ngx_rbtree_node_t *node,
    ngx_rbtree_node_t *sentinel,
    ngx_http_markdown_path_detail_render_ctx_t *render)
{
    const ngx_http_markdown_path_metric_node_t  *pnode;
    size_t                                       path_len;
    size_t                                       needed;
    size_t                                       remaining;

    if (node == sentinel || render->pos >= render->end) {
        return render->pos;
    }

    /* Visit the left subtree first so text paths remain sorted. */
    render->pos = ngx_http_markdown_text_walk_path_tree_bounded(
            node->left, sentinel, render);

    if (render->failed) {
        return render->pos;
    }

    /* Keep the mandatory summary tail available for the final response. */
    remaining = (size_t) (render->end - render->pos);
    if (remaining > render->tail_reserve) {
        pnode = (const ngx_http_markdown_path_metric_node_t *) node;
        if (ngx_http_markdown_path_len_to_size(pnode, &path_len)
            != NGX_OK)
        {
            render->failed = 1;
            return render->pos;
        }
        needed = ngx_http_markdown_text_path_entry_size(path_len);

        /* Emit complete entries only; otherwise conserve their counters. */
        if (needed <= remaining - render->tail_reserve) {
            u_char  *entry_start = render->pos;

            render->pos = ngx_slprintf(render->pos, render->end,
                "- Path[");
            render->pos = ngx_http_markdown_escape_json_string(
                render->pos, render->end,
                pnode->path, pnode->path_len);
            render->pos = ngx_slprintf(render->pos, render->end,
                "]: conversions=%uA entries=%uA "
                "time_ms=%uA\n",
                pnode->conversions,
                pnode->entries,
                pnode->conversion_time_sum_ms);

            if (render->pos >= render->end) {
                render->pos = entry_start;
                render->omitted_conversions =
                    ngx_http_markdown_metrics_saturating_add(
                        render->omitted_conversions,
                        pnode->conversions);
                render->omitted_entries =
                    ngx_http_markdown_metrics_saturating_add(
                        render->omitted_entries, pnode->entries);
                render->omitted_time_ms =
                    ngx_http_markdown_metrics_saturating_add(
                        render->omitted_time_ms,
                        pnode->conversion_time_sum_ms);
                render->omitted_nodes =
                    ngx_http_markdown_metrics_saturating_size_add(
                        render->omitted_nodes, 1);
            } else {
                render->entries_written++;
            }
        } else {
            render->omitted_conversions =
                ngx_http_markdown_metrics_saturating_add(
                    render->omitted_conversions,
                    pnode->conversions);
            render->omitted_entries =
                ngx_http_markdown_metrics_saturating_add(
                    render->omitted_entries, pnode->entries);
            render->omitted_time_ms =
                ngx_http_markdown_metrics_saturating_add(
                    render->omitted_time_ms,
                    pnode->conversion_time_sum_ms);
            render->omitted_nodes =
                ngx_http_markdown_metrics_saturating_size_add(
                    render->omitted_nodes, 1);
        }
    } else {
        const ngx_http_markdown_path_metric_node_t  *pnode2;
        pnode2 = (const ngx_http_markdown_path_metric_node_t *) node;
        render->omitted_conversions = ngx_http_markdown_metrics_saturating_add(
            render->omitted_conversions, pnode2->conversions);
        render->omitted_entries = ngx_http_markdown_metrics_saturating_add(
            render->omitted_entries, pnode2->entries);
        render->omitted_time_ms = ngx_http_markdown_metrics_saturating_add(
            render->omitted_time_ms,
            pnode2->conversion_time_sum_ms);
        render->omitted_nodes = ngx_http_markdown_metrics_saturating_size_add(
            render->omitted_nodes, 1);
    }

    /* A failed child leaves no safe output position for the right subtree. */
    if (render->failed) {
        return render->pos;
    }

    render->pos = ngx_http_markdown_text_walk_path_tree_bounded(
            node->right, sentinel, render);

    return render->pos;
}


static u_char *
ngx_http_markdown_json_write_path_details(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_snapshot_t *snapshot)
{
    ngx_shm_zone_t                                *zone;
    ngx_slab_pool_t                               *shpool;
    ngx_http_markdown_metrics_t                   *live_metrics;
    ngx_http_markdown_path_detail_render_ctx_t    render;
    ngx_atomic_uint_t                              other_conversions;
    ngx_atomic_uint_t                              other_entries;
    ngx_atomic_uint_t                              other_time_ms;

    render.pos = p;
    render.end = end;
    render.tail_reserve = ngx_http_markdown_json_tail_reserve();
    render.omitted_conversions = 0;
    render.omitted_entries = 0;
    render.omitted_time_ms = 0;
    render.omitted_nodes = 0;
    render.entries_written = 0;
    render.failed = 0;

    if (snapshot->per_path.path_entries > 0
        && ngx_http_markdown_metrics_shm_zone != NULL
        && ngx_http_markdown_metrics_shm_zone->data != NULL)
    {
        zone = ngx_http_markdown_metrics_shm_zone;
        live_metrics = (ngx_http_markdown_metrics_t *) zone->data;
        shpool = (ngx_slab_pool_t *) zone->shm.addr;

        ngx_shmtx_lock(&shpool->mutex);
        p = ngx_http_markdown_json_walk_path_tree_bounded(
                live_metrics->per_path.path_tree.root,
                &live_metrics->per_path.sentinel, &render);
        ngx_shmtx_unlock(&shpool->mutex);
    }

    if (render.failed) {
        return NULL;
    }

    if ((snapshot->per_path.unretained_conversions == 0
         && render.omitted_nodes == 0) || p >= end)
    {
        return p;
    }

    other_conversions = ngx_http_markdown_metrics_saturating_add(
        snapshot->per_path.unretained_conversions,
        render.omitted_conversions);
    other_time_ms = ngx_http_markdown_metrics_saturating_add(
        snapshot->per_path.unretained_conversion_time_sum_ms,
        render.omitted_time_ms);
    other_entries = ngx_http_markdown_metrics_saturating_add(
        snapshot->per_path.unretained_conversions,
        render.omitted_entries);

    if (render.entries_written > 0) {
        p = ngx_slprintf(p, end, ",");
    }

    return ngx_slprintf(p, end,
        "\n"
        "      {\"path\":\"__other__\","
        "\"conversions\":%uA,"
        "\"conversion_time_sum_ms\":%uA,"
        "\"entries\":%uA}",
        other_conversions, other_time_ms, other_entries);
}


static u_char *
ngx_http_markdown_text_write_path_details(
    u_char *p,
    u_char *end,
    const ngx_http_markdown_metrics_snapshot_t *snapshot)
{
    ngx_shm_zone_t                                *zone;
    ngx_slab_pool_t                               *shpool;
    ngx_http_markdown_metrics_t                   *live_metrics;
    ngx_http_markdown_path_detail_render_ctx_t    render;
    ngx_atomic_uint_t                              other_conversions;
    ngx_atomic_uint_t                              other_entries;
    ngx_atomic_uint_t                              other_time_ms;

    render.pos = p;
    render.end = end;
    render.tail_reserve = ngx_http_markdown_text_tail_reserve();
    render.omitted_conversions = 0;
    render.omitted_entries = 0;
    render.omitted_time_ms = 0;
    render.omitted_nodes = 0;
    render.entries_written = 0;
    render.failed = 0;

    if (snapshot->per_path.path_entries > 0
        && ngx_http_markdown_metrics_shm_zone != NULL
        && ngx_http_markdown_metrics_shm_zone->data != NULL)
    {
        zone = ngx_http_markdown_metrics_shm_zone;
        live_metrics = (ngx_http_markdown_metrics_t *) zone->data;
        shpool = (ngx_slab_pool_t *) zone->shm.addr;

        p = ngx_slprintf(p, end, "\nPer-Path Details:\n");
        render.pos = p;
        ngx_shmtx_lock(&shpool->mutex);
        p = ngx_http_markdown_text_walk_path_tree_bounded(
                live_metrics->per_path.path_tree.root,
                &live_metrics->per_path.sentinel, &render);
        ngx_shmtx_unlock(&shpool->mutex);
    } else if (snapshot->per_path.unretained_conversions > 0) {
        p = ngx_slprintf(p, end, "\nPer-Path Details:\n");
        render.pos = p;
    } else {
        return p;
    }

    if (render.failed) {
        return NULL;
    }

    if ((snapshot->per_path.unretained_conversions == 0
         && render.omitted_nodes == 0) || p >= end)
    {
        return p;
    }

    other_conversions = ngx_http_markdown_metrics_saturating_add(
        snapshot->per_path.unretained_conversions,
        render.omitted_conversions);
    other_time_ms = ngx_http_markdown_metrics_saturating_add(
        snapshot->per_path.unretained_conversion_time_sum_ms,
        render.omitted_time_ms);
    other_entries = ngx_http_markdown_metrics_saturating_add(
        snapshot->per_path.unretained_conversions,
        render.omitted_entries);

    return ngx_slprintf(p, end,
        "- Path[__other__]: conversions=%uA entries=%uA "
        "time_ms=%uA\n",
        other_conversions, other_entries, other_time_ms);
}
#endif /* NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED */


/*
 * Render the metrics response body and set the matching Content-Type header.
 *
 * Returns the end pointer for the rendered body on success.
 * The public handler passes the frozen Prometheus format. The JSON and
 * human-readable branches remain only for bounded internal renderer tests;
 * they are not reachable through the 0.9.2 endpoint.
 *
 * NULL is returned only when the aggregate section itself does not fit
 * (indicating the buffer is far too small for any useful output).
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
        p = ngx_http_markdown_metrics_write_json(
                p, b->end, snapshot,
                derived->conversions_completed,
                derived->conversion_time_avg_ms,
                derived->input_bytes_avg,
                derived->output_bytes_avg);
        if (p == NULL || p >= b->end) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown: JSON output "
                "truncated, buffer too small");
            return NULL;
        }
        ngx_str_set(&r->headers_out.content_type,
                     "application/json");
        return p;

    case NGX_HTTP_MARKDOWN_METRICS_OUTPUT_PROMETHEUS:
        {
            ngx_http_markdown_metrics_v1_snapshot_t  v1;

            ngx_http_markdown_metrics_to_v1(snapshot, &v1);
            p = ngx_http_markdown_metrics_v1_render(p, b->end, &v1);
        }
        if (p == NULL) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown: Prometheus output "
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
        if (p == NULL || p >= b->end) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown: plain-text output "
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
 * - Serializes the resulting metrics into an in-memory Prometheus text
 *   buffer and sends the data as the response body.
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

    /* Select the frozen response format before discarding any request body. */
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
            "markdown: failed to allocate "
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
