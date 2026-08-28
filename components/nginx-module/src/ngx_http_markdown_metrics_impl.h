#ifndef NGX_HTTP_MARKDOWN_METRICS_IMPL_H
#define NGX_HTTP_MARKDOWN_METRICS_IMPL_H

#include "ngx_http_markdown_metrics_v1_renderer.h"

#include <stdint.h>

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
 * The latency histogram, decompression counters, path-hit counters, and skip
 * counters are grouped into anonymous sub-structs to keep the top-level field
 * count within SonarCloud's 20-field limit.
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

/*
 * Auxiliary counters needed by the v1 renderer after the shared metrics
 * values have been copied into a request-local snapshot.
 */
typedef struct {
    struct {
        ngx_atomic_uint_t current;
    } inflight;
    ngx_atomic_uint_t backpressure_resume_total;
    ngx_atomic_uint_t backpressure_resume_failure_total;
} ngx_http_markdown_metrics_performance_snapshot_t;

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

    /* Path hit metrics, grouped to keep the snapshot compact. */
    struct {
        ngx_atomic_uint_t fullbuffer;
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
        ngx_atomic_uint_t no_transform;
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

        /* Parse interrupt metrics */
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
        ngx_atomic_uint_t last_ttfb_ms;
        ngx_atomic_uint_t last_peak_memory_bytes;

        /* Fallback/failure counters */
        ngx_atomic_uint_t streaming_fallback_precommit_pass;
        ngx_atomic_uint_t streaming_fallback_precommit_reject;
        ngx_atomic_uint_t streaming_failure_postcommit_abort;
        ngx_atomic_uint_t streaming_failure_postcommit_safe_finish;
        ngx_atomic_uint_t terminal_aborted_total;

        /* Engine choice counters */
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

    /* Auxiliary counters consumed by the current v1 renderer. */
    ngx_http_markdown_metrics_performance_snapshot_t perf;
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

/* C99 declaration visibility for standalone static analysis of this impl header. */
#ifndef ngx_memzero
void ngx_memzero(void *buf, size_t n);
#endif
u_char *ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...);
ngx_int_t ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *out);

#ifndef ngx_str_set
#define ngx_str_set(str, text)                                                    \
    do {                                                                          \
        (str)->len = sizeof(text) - 1;                                            \
        (str)->data = (u_char *) text;                                            \
    } while (0)
#endif

/*
 * Response buffer size for the metrics endpoint.
 *
 * Estimated current Prometheus output:
 *   ~3.8 KiB (most verbose due to HELP/TYPE lines)
 */
#define NGX_HTTP_MARKDOWN_METRICS_BUF_SIZE  131072

static u_char *
ngx_http_markdown_metrics_render_response_body(
    ngx_http_request_t *r,
    ngx_buf_t *b,
    const ngx_http_markdown_metrics_snapshot_t *snapshot);
static ngx_int_t
ngx_http_markdown_metrics_send_response(
    ngx_http_request_t *r,
    ngx_buf_t *b,
    u_char *response_end);

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
    snapshot->skips.no_transform = metrics->skips.no_transform;
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
}

static void
ngx_http_markdown_collect_performance_snapshot(
    ngx_http_markdown_metrics_snapshot_t *snapshot,
    const ngx_http_markdown_metrics_t *metrics)
{
    snapshot->perf.inflight.current =
        (ngx_atomic_uint_t) ngx_http_markdown_inflight_current();
    snapshot->perf.backpressure_resume_total =
        metrics->perf.backpressure_resume_total;
    snapshot->perf.backpressure_resume_failure_total =
        metrics->perf.backpressure_resume_failure_total;
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
 * Translate the collected storage snapshot into the v1 metric contract.
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
        snapshot->skips.no_transform;
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
    v1->attempts.full_buffer = snapshot->path_hits.fullbuffer;
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
    /* Count only failures observed while resuming pending output. */
    v1->streaming_events.resume_failure =
        snapshot->perf.backpressure_resume_failure_total;
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
    v1->build_info.version = (const u_char *) NGX_HTTP_MARKDOWN_PRODUCT_VERSION;
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

#if (NGX_HAVE_UNIX_DOMAIN)
    if (r->connection->sockaddr->sa_family == AF_UNIX) {
        /* UNIX-domain peers connect through a local socket path and
         * cannot originate from a remote host, so the loopback-only
         * boundary is inherently satisfied. */
        return NGX_OK;
    }
#endif

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

/* Render the frozen Prometheus v1 response and set its Content-Type header. */
static u_char *
ngx_http_markdown_metrics_render_response_body(
    ngx_http_request_t *r,
    ngx_buf_t *b,
    const ngx_http_markdown_metrics_snapshot_t *snapshot)
{
    ngx_http_markdown_metrics_v1_snapshot_t  v1;
    u_char                                  *p;

    ngx_http_markdown_metrics_to_v1(snapshot, &v1);
    p = ngx_http_markdown_metrics_v1_render(b->pos, b->end, &v1);
    if (p == NULL) {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown: Prometheus output "
            "truncated, buffer too small");
        return NULL;
    }

    ngx_str_set(&r->headers_out.content_type,
                "text/plain; version=0.0.4; charset=utf-8");
    return p;
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
 * Send the metrics endpoint's 405 response and Allow header, mirroring the
 * diagnostics endpoint's transactional pattern: allocate the body buffer
 * first, then the Allow header, so an allocation failure never leaves a live
 * Allow header on a 500.  NGINX does not add the Allow header for 405
 * responses from content handlers; RFC 9110 Section 15.5.6 requires it.
 */
static ngx_int_t
ngx_http_markdown_metrics_method_not_allowed(ngx_http_request_t *r)
{
    static u_char body[] = "Method Not Allowed. Use GET or HEAD.\n";
    ngx_table_elt_t  *allow_hdr;
    ngx_buf_t    *b;
    ngx_chain_t   out;
    ngx_int_t     rc;

    rc = ngx_http_discard_request_body(r);
    if (rc != NGX_OK) {
        return rc;
    }

    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    allow_hdr = ngx_list_push(&r->headers_out.headers);
    if (allow_hdr == NULL) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }
    allow_hdr->hash = 1;
    ngx_str_set(&allow_hdr->key, "Allow");
    ngx_str_set(&allow_hdr->value, "GET, HEAD");

    b->pos = body;
    b->last = body + sizeof(body) - 1;
    b->memory = 1;
    b->last_in_chain = 1;
    b->last_buf = (r == r->main) ? 1 : 0;

    r->headers_out.status = NGX_HTTP_NOT_ALLOWED;
    r->headers_out.content_type_len = sizeof("text/plain") - 1;
    ngx_str_set(&r->headers_out.content_type, "text/plain");
    r->headers_out.content_type_lowcase = NULL;
    r->headers_out.content_type_hash = 0;
    r->headers_out.content_length_n = b->last - b->pos;

    rc = ngx_http_send_header(r);
    if (rc == NGX_ERROR || rc > NGX_OK) {
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
 *   NGX_HTTP_NOT_ALLOWED and an Allow: GET, HEAD header per RFC 9110
 *   Section 15.5.6.
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
    ngx_http_markdown_metrics_snapshot_t  snapshot;

    rc = ngx_http_markdown_metrics_validate_request(r);
    if (rc != NGX_OK) {
        if (rc == NGX_HTTP_NOT_ALLOWED) {
            return ngx_http_markdown_metrics_method_not_allowed(r);
        }
        return rc;
    }

    rc = ngx_http_discard_request_body(r);
    if (rc != NGX_OK) {
        return rc;
    }

    /* Take one best-effort snapshot before rendering the response. */
    ngx_http_markdown_collect_metrics_snapshot(&snapshot);

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
        r, b, &snapshot);
    if (response_end == NULL) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    return ngx_http_markdown_metrics_send_response(
        r, b, response_end);
}

#endif /* NGX_HTTP_MARKDOWN_METRICS_IMPL_H */
