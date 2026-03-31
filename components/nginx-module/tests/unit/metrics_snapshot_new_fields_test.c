/*
 * Test: metrics_snapshot_new_fields
 * Description: snapshot collection of new Prometheus counter fields
 */

#include "test_common.h"
#include "test_metrics_snapshot.h"

/*
 * In the real module the metrics struct and the snapshot struct
 * have the same layout.  Reuse the shared type for both so the
 * test verifies field-by-field copy without duplicating the
 * struct definition.
 */
typedef test_metrics_snapshot_t  metrics_t;
typedef test_metrics_snapshot_t  snapshot_t;

/* Global pointer simulating ngx_http_markdown_metrics */
static metrics_t *g_metrics = NULL;

static void
collect_snapshot(snapshot_t *snap)
{
    const metrics_t *m;

    memset(snap, 0, sizeof(snapshot_t));

    m = g_metrics;
    if (m == NULL) {
        return;
    }

    snap->conversions_attempted = m->conversions_attempted;
    snap->conversions_succeeded = m->conversions_succeeded;
    snap->conversions_failed = m->conversions_failed;
    snap->conversions_bypassed = m->conversions_bypassed;
    snap->failures_conversion = m->failures_conversion;
    snap->failures_resource_limit = m->failures_resource_limit;
    snap->failures_system = m->failures_system;
    snap->conversion_time_sum_ms = m->conversion_time_sum_ms;
    snap->input_bytes = m->input_bytes;
    snap->output_bytes = m->output_bytes;
    snap->conversion_latency.le_10ms = m->conversion_latency.le_10ms;
    snap->conversion_latency.le_100ms = m->conversion_latency.le_100ms;
    snap->conversion_latency.le_1000ms = m->conversion_latency.le_1000ms;
    snap->conversion_latency.gt_1000ms = m->conversion_latency.gt_1000ms;
    snap->decompressions.attempted = m->decompressions.attempted;
    snap->decompressions.succeeded = m->decompressions.succeeded;
    snap->decompressions.failed = m->decompressions.failed;
    snap->decompressions.gzip = m->decompressions.gzip;
    snap->decompressions.deflate = m->decompressions.deflate;
    snap->decompressions.brotli = m->decompressions.brotli;
    snap->path_hits.fullbuffer = m->path_hits.fullbuffer;
    snap->path_hits.incremental = m->path_hits.incremental;
    snap->requests_entered = m->requests_entered;
    snap->skips.config = m->skips.config;
    snap->skips.method = m->skips.method;
    snap->skips.status = m->skips.status;
    snap->skips.content_type = m->skips.content_type;
    snap->skips.size = m->skips.size;
    snap->skips.streaming = m->skips.streaming;
    snap->skips.auth = m->skips.auth;
    snap->skips.range = m->skips.range;
    snap->skips.accept = m->skips.accept;
    snap->failopen_count = m->failopen_count;
    snap->estimated_token_savings = m->estimated_token_savings;
}

static void
test_null_metrics_zeroes_new_fields(void)
{
    snapshot_t snap;

    TEST_SUBSECTION("New fields zeroed when metrics pointer is NULL");

    g_metrics = NULL;
    collect_snapshot(&snap);

    TEST_ASSERT(snap.requests_entered == 0,
                "requests_entered should be zero");
    TEST_ASSERT(snap.skips.config == 0,
                "skips.config should be zero");
    TEST_ASSERT(snap.skips.method == 0,
                "skips.method should be zero");
    TEST_ASSERT(snap.skips.status == 0,
                "skips.status should be zero");
    TEST_ASSERT(snap.skips.content_type == 0,
                "skips.content_type should be zero");
    TEST_ASSERT(snap.skips.size == 0,
                "skips.size should be zero");
    TEST_ASSERT(snap.skips.streaming == 0,
                "skips.streaming should be zero");
    TEST_ASSERT(snap.skips.auth == 0,
                "skips.auth should be zero");
    TEST_ASSERT(snap.skips.range == 0,
                "skips.range should be zero");
    TEST_ASSERT(snap.skips.accept == 0,
                "skips.accept should be zero");
    TEST_ASSERT(snap.failopen_count == 0,
                "failopen_count should be zero");
    TEST_ASSERT(snap.estimated_token_savings == 0,
                "estimated_token_savings should be zero");

    TEST_PASS("All new fields are zeroed when metrics is NULL");
}

static void
test_new_fields_copied_correctly(void)
{
    metrics_t  m;
    snapshot_t snap;

    TEST_SUBSECTION("New fields copied from mock metrics struct");

    memset(&m, 0, sizeof(m));

    /* Set distinctive values for each new field */
    m.requests_entered = 42;
    m.skips.config = 1;
    m.skips.method = 2;
    m.skips.status = 3;
    m.skips.content_type = 4;
    m.skips.size = 5;
    m.skips.streaming = 6;
    m.skips.auth = 7;
    m.skips.range = 8;
    m.skips.accept = 9;
    m.failopen_count = 10;
    m.estimated_token_savings = 15000;

    /* Also set some existing fields to verify no interference */
    m.conversions_attempted = 100;
    m.conversions_succeeded = 80;

    g_metrics = &m;
    collect_snapshot(&snap);
    g_metrics = NULL;

    TEST_ASSERT(snap.requests_entered == 42,
                "requests_entered should be copied");
    TEST_ASSERT(snap.skips.config == 1,
                "skips.config should be copied");
    TEST_ASSERT(snap.skips.method == 2,
                "skips.method should be copied");
    TEST_ASSERT(snap.skips.status == 3,
                "skips.status should be copied");
    TEST_ASSERT(snap.skips.content_type == 4,
                "skips.content_type should be copied");
    TEST_ASSERT(snap.skips.size == 5,
                "skips.size should be copied");
    TEST_ASSERT(snap.skips.streaming == 6,
                "skips.streaming should be copied");
    TEST_ASSERT(snap.skips.auth == 7,
                "skips.auth should be copied");
    TEST_ASSERT(snap.skips.range == 8,
                "skips.range should be copied");
    TEST_ASSERT(snap.skips.accept == 9,
                "skips.accept should be copied");
    TEST_ASSERT(snap.failopen_count == 10,
                "failopen_count should be copied");
    TEST_ASSERT(snap.estimated_token_savings == 15000,
                "estimated_token_savings should be copied");

    /* Verify existing fields still work */
    TEST_ASSERT(snap.conversions_attempted == 100,
                "existing conversions_attempted should be copied");
    TEST_ASSERT(snap.conversions_succeeded == 80,
                "existing conversions_succeeded should be copied");

    TEST_PASS("All new fields are copied correctly");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_snapshot_new_fields Tests\n");
    printf("========================================\n");

    test_null_metrics_zeroes_new_fields();
    test_new_fields_copied_correctly();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
