/*
 * Internal postcommit metric helper implementations.
 *
 * Production inclusion owner: the main ngx_http_markdown_filter_module.c
 * translation unit, immediately after ngx_http_markdown_module_state_impl.h
 * defines the metric macros used below.  Focused unit tests may include this
 * file only after defining MARKDOWN_STREAMING_ENABLED and providing
 * ngx_http_markdown_metrics_t, the ngx_http_markdown_metrics pointer,
 * ngx_atomic_t, and the METRIC_ADD, METRIC_INC, and METRIC_WATERMARK macros.
 * Feature-disabled builds intentionally receive no definitions because their
 * metrics layout omits streaming fields.
 */

#ifndef NGX_HTTP_MARKDOWN_POSTCOMMIT_METRICS_IMPL_H
#define NGX_HTTP_MARKDOWN_POSTCOMMIT_METRICS_IMPL_H

#ifdef MARKDOWN_STREAMING_ENABLED

/*
 * Record a postcommit output chain becoming pending at the downstream
 * ownership boundary.  A terminal-only chain is still a backpressure event,
 * but contributes no bytes to the pending-output high-watermark.
 */
void
ngx_http_markdown_metrics_record_postcommit_pending(size_t bytes)
{
    NGX_HTTP_MARKDOWN_METRIC_INC(perf.backpressure_total);

    if (bytes > 0) {
        NGX_HTTP_MARKDOWN_METRIC_WATERMARK(
            perf.pending_output_high_watermark_bytes,
            (ngx_atomic_t) bytes);
    }
}

/*
 * Record pool-copied postcommit bytes after confirmed immediate delivery.
 * Deferred delivery uses pending_meta and is accounted by resume_pending().
 */
void
ngx_http_markdown_metrics_record_postcommit_copied_delivery(size_t bytes)
{
    if (bytes == 0) {
        return;
    }

    NGX_HTTP_MARKDOWN_METRIC_ADD(
        streaming.selection.output_bytes_total,
        (ngx_atomic_t) bytes);
    NGX_HTTP_MARKDOWN_METRIC_INC(perf.copied_output_total);
}

/*
 * Record a protocol-safe postcommit abort metric increment.
 *
 * Semantics: this metric counts protocol-safe abort ATTEMPTS via
 * postcommit_abort() — i.e., attempts to send an empty terminal
 * chain to gracefully close a truncated streaming response.
 *
 * NOT counted by this metric:
 *   - Hard aborts via handle_output_loss() (connection-reset with no
 *     protocol-safe terminal; those are counted by the failure_reason
 *     metrics: failures_resource_limit or failures_conversion).
 *
 * The metric fires once per request at the first protocol-safe abort
 * attempt, regardless of whether the terminal chain delivery succeeds,
 * returns NGX_AGAIN (backpressure), or fails.  The one-shot guard is
 * implemented by the caller (ngx_http_markdown_stream_postcommit_abort)
 * via ctx->streaming.completion.postcommit_abort_recorded.
 */
void
ngx_http_markdown_metrics_record_postcommit_abort(void)
{
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.streaming_failure_postcommit_abort);
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_POSTCOMMIT_METRICS_IMPL_H */
