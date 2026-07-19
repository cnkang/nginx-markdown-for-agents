/*
 * Test: postcommit_metrics_accounting
 *
 * Binds focused metric accounting regressions to the production helper
 * implementation.  Keep this minimal contract synchronized with the
 * requirements documented by ngx_http_markdown_postcommit_metrics_impl.h.
 */

#include "../include/test_common.h"

#define MARKDOWN_STREAMING_ENABLED 1

typedef unsigned long  ngx_atomic_t;

typedef struct {
    struct {
        struct {
            ngx_atomic_t  output_bytes_total;
        } selection;
        ngx_atomic_t  streaming_failure_postcommit_abort;
    } streaming;
    struct {
        ngx_atomic_t  backpressure_total;
        ngx_atomic_t  pending_output_high_watermark_bytes;
        ngx_atomic_t  copied_output_total;
    } perf;
} ngx_http_markdown_metrics_t;

static ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;

#define NGX_HTTP_MARKDOWN_METRIC_ADD(field, value)                    \
    do {                                                               \
        if (ngx_http_markdown_metrics != NULL) {                       \
            ngx_http_markdown_metrics->field += (value);              \
        }                                                              \
    } while (0)

#define NGX_HTTP_MARKDOWN_METRIC_INC(field)                            \
    NGX_HTTP_MARKDOWN_METRIC_ADD(field, 1)

#define NGX_HTTP_MARKDOWN_METRIC_WATERMARK(field, value)               \
    do {                                                               \
        if (ngx_http_markdown_metrics != NULL                          \
            && (value) > ngx_http_markdown_metrics->field)            \
        {                                                              \
            ngx_http_markdown_metrics->field = (value);                \
        }                                                              \
    } while (0)

#include "../../src/ngx_http_markdown_postcommit_metrics_impl.h"

static void
test_pending_tracks_events_and_maximum(void)
{
    ngx_http_markdown_metrics_t  metrics;

    memset(&metrics, 0, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    ngx_http_markdown_metrics_record_postcommit_pending(4096);
    TEST_ASSERT(metrics.perf.backpressure_total == 1,
        "pending(4096) must increment backpressure_total");
    TEST_ASSERT(metrics.perf.pending_output_high_watermark_bytes == 4096,
        "pending(4096) must set the watermark to 4096");

    ngx_http_markdown_metrics_record_postcommit_pending(1024);
    TEST_ASSERT(metrics.perf.backpressure_total == 2,
        "pending(1024) must increment backpressure_total again");
    TEST_ASSERT(metrics.perf.pending_output_high_watermark_bytes == 4096,
        "pending(1024) must preserve the 4096 watermark");

    ngx_http_markdown_metrics_record_postcommit_pending(8192);
    TEST_ASSERT(metrics.perf.backpressure_total == 3,
        "pending(8192) must increment backpressure_total a third time");
    TEST_ASSERT(metrics.perf.pending_output_high_watermark_bytes == 8192,
        "pending(8192) must raise the watermark to 8192");
}

static void
test_zero_pending_counts_without_watermark(void)
{
    ngx_http_markdown_metrics_t  metrics;

    memset(&metrics, 0, sizeof(metrics));
    metrics.perf.pending_output_high_watermark_bytes = 77;
    ngx_http_markdown_metrics = &metrics;

    ngx_http_markdown_metrics_record_postcommit_pending(0);
    TEST_ASSERT(metrics.perf.backpressure_total == 1,
        "pending(0) must increment backpressure_total");
    TEST_ASSERT(metrics.perf.pending_output_high_watermark_bytes == 77,
        "pending(0) must not change the watermark");
}

static void
test_copied_delivery_accumulates_bytes_and_events(void)
{
    ngx_http_markdown_metrics_t  metrics;

    memset(&metrics, 0, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    ngx_http_markdown_metrics_record_postcommit_copied_delivery(4);
    TEST_ASSERT(metrics.streaming.selection.output_bytes_total == 4,
        "copied(4) must add four output bytes");
    TEST_ASSERT(metrics.perf.copied_output_total == 1,
        "copied(4) must increment copied_output_total");

    ngx_http_markdown_metrics_record_postcommit_copied_delivery(7);
    TEST_ASSERT(metrics.streaming.selection.output_bytes_total == 11,
        "copied(7) must accumulate output bytes to eleven");
    TEST_ASSERT(metrics.perf.copied_output_total == 2,
        "copied(7) must increment copied_output_total again");
}

static void
test_zero_copied_delivery_is_noop(void)
{
    ngx_http_markdown_metrics_t  metrics;

    memset(&metrics, 0, sizeof(metrics));
    metrics.streaming.selection.output_bytes_total = 13;
    metrics.perf.copied_output_total = 17;
    ngx_http_markdown_metrics = &metrics;

    ngx_http_markdown_metrics_record_postcommit_copied_delivery(0);
    TEST_ASSERT(metrics.streaming.selection.output_bytes_total == 13,
        "copied(0) must not change output bytes");
    TEST_ASSERT(metrics.perf.copied_output_total == 17,
        "copied(0) must not change copied_output_total");
}

static void
test_abort_metric_increments_once(void)
{
    ngx_http_markdown_metrics_t  metrics;

    memset(&metrics, 0, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    ngx_http_markdown_metrics_record_postcommit_abort();
    TEST_ASSERT(metrics.streaming.streaming_failure_postcommit_abort == 1,
        "abort() must increment streaming_failure_postcommit_abort");

    ngx_http_markdown_metrics_record_postcommit_abort();
    TEST_ASSERT(metrics.streaming.streaming_failure_postcommit_abort == 2,
        "abort() called again must increment again (caller guards one-shot)");
}

static void
test_null_metrics_pointer_is_noop(void)
{
    ngx_http_markdown_metrics = NULL;

    ngx_http_markdown_metrics_record_postcommit_pending(4096);
    ngx_http_markdown_metrics_record_postcommit_copied_delivery(7);
    ngx_http_markdown_metrics_record_postcommit_abort();

    TEST_PASS("NULL metrics pointer is a no-op");
}

int
main(void)
{
    TEST_SECTION("Postcommit production metric helper tests");

    test_pending_tracks_events_and_maximum();
    test_zero_pending_counts_without_watermark();
    test_copied_delivery_accumulates_bytes_and_events();
    test_zero_copied_delivery_is_noop();
    test_abort_metric_increments_once();
    test_null_metrics_pointer_is_noop();

    TEST_PASS("postcommit production metric helper accounting");
    return 0;
}
