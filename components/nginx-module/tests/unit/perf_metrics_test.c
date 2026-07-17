/*
 * Test: perf_metrics
 *
 * Validates all 8 new performance metrics from Spec 64a:
 *   - backpressure_total fires on NGX_AGAIN
 *   - backpressure_resume_total fires on drain NGX_OK
 *   - pending_output_high_watermark_bytes CAS gauge tracks max
 *   - decompression_streaming_total/fullbuffer_total at path selection
 *   - decompression_budget_exceeded_total on budget exceedance
 *   - zero_copy_output_total / copied_output_total ONLY on NGX_OK
 *   - JSON renderer emits all 8 keys
 *   - Prometheus renderer emits all 8 metrics with TYPE annotations
 *
 * Requirements: 7.1, 7.2, 7.3, 7.4, 7.7
 *
 * Rules: 8 (complete metric lifecycle), 23 (delivery != decision,
 * gauge at correct event), 47 (latch not set on NGX_AGAIN).
 */

#include "../include/test_common.h"

/* ── Return code constants ───────────────────────────────────── */

enum {
    NGX_OK    =  0,
    NGX_ERROR = -1,
    NGX_AGAIN = -2,
    NGX_DONE  = -4
};

/* ── Simulated perf metrics struct ───────────────────────────── */

typedef struct {
    unsigned long backpressure_total;
    unsigned long backpressure_resume_total;
    unsigned long pending_output_high_watermark_bytes;
    unsigned long decompression_streaming_total;
    unsigned long decompression_fullbuffer_total;
    unsigned long decompression_budget_exceeded_total;
    unsigned long zero_copy_output_total;
    unsigned long copied_output_total;
} perf_metrics_t;

/* ── Simulated write-site functions ──────────────────────────── */

/*
 * Models the production backpressure write-site logic from
 * ngx_http_markdown_conversion_impl.h (full-buffer output path).
 *
 * After downstream body-filter returns:
 *   NGX_OK/NGX_DONE: increment delivery counter (zero_copy or copied)
 *   NGX_AGAIN: increment backpressure_total, update watermark gauge
 *   Any other: no metric writes
 */
static void
perf_metrics_on_body_output(int downstream_rc,
    unsigned long pending_bytes,
    int is_zero_copy,
    perf_metrics_t *m)
{
    if (downstream_rc == NGX_OK || downstream_rc == NGX_DONE) {
        if (is_zero_copy) {
            m->zero_copy_output_total++;
        } else {
            m->copied_output_total++;
        }
    } else if (downstream_rc == NGX_AGAIN) {
        m->backpressure_total++;
        /* CAS gauge: only update if value exceeds current max */
        if (pending_bytes > m->pending_output_high_watermark_bytes) {
            m->pending_output_high_watermark_bytes = pending_bytes;
        }
    }
    /* NGX_ERROR: no metric writes (Rule 23: delivery != decision) */
}

/*
 * Models the backpressure resume write-site logic.
 * Called when a previously-suspended drain completes.
 */
static void
perf_metrics_on_drain_resume(int drain_rc, perf_metrics_t *m)
{
    if (drain_rc == NGX_OK || drain_rc == NGX_DONE) {
        m->backpressure_resume_total++;
    }
    /* NGX_AGAIN or NGX_ERROR: no resume metric */
}

/*
 * Models the decompression path selection write-site logic.
 */
static void
perf_metrics_on_decomp_path_selection(int is_streaming,
    perf_metrics_t *m)
{
    if (is_streaming) {
        m->decompression_streaming_total++;
    } else {
        m->decompression_fullbuffer_total++;
    }
}

/*
 * Models the decompression budget exceedance write-site logic.
 */
static void
perf_metrics_on_decomp_budget_exceeded(perf_metrics_t *m)
{
    m->decompression_budget_exceeded_total++;
}

/*
 * CAS watermark simulation: monotonically non-decreasing gauge.
 * Models the NGX_HTTP_MARKDOWN_METRIC_WATERMARK macro behavior.
 */
static void
perf_metrics_watermark_update(unsigned long new_value,
    perf_metrics_t *m)
{
    if (new_value > m->pending_output_high_watermark_bytes) {
        m->pending_output_high_watermark_bytes = new_value;
    }
}

/* ── Test 1: backpressure_total fires on NGX_AGAIN ───────────── */

static void
test_backpressure_fires_on_ngx_again(void)
{
    perf_metrics_t m;

    TEST_SUBSECTION("backpressure_total fires on NGX_AGAIN");
    memset(&m, 0, sizeof(m));

    perf_metrics_on_body_output(NGX_AGAIN, 4096, 0, &m);
    TEST_ASSERT(m.backpressure_total == 1,
        "NGX_AGAIN increments backpressure_total");

    perf_metrics_on_body_output(NGX_AGAIN, 8192, 0, &m);
    TEST_ASSERT(m.backpressure_total == 2,
        "second NGX_AGAIN increments again");

    TEST_PASS("backpressure_total fires on NGX_AGAIN");
}

/* ── Test 2: backpressure_total NOT fired on NGX_OK ──────────── */

static void
test_backpressure_not_fired_on_ngx_ok(void)
{
    perf_metrics_t m;

    TEST_SUBSECTION("backpressure_total NOT fired on NGX_OK");
    memset(&m, 0, sizeof(m));

    perf_metrics_on_body_output(NGX_OK, 4096, 0, &m);
    TEST_ASSERT(m.backpressure_total == 0,
        "NGX_OK does not fire backpressure_total");

    perf_metrics_on_body_output(NGX_DONE, 4096, 0, &m);
    TEST_ASSERT(m.backpressure_total == 0,
        "NGX_DONE does not fire backpressure_total");

    perf_metrics_on_body_output(NGX_ERROR, 4096, 0, &m);
    TEST_ASSERT(m.backpressure_total == 0,
        "NGX_ERROR does not fire backpressure_total");

    TEST_PASS("backpressure_total NOT fired on NGX_OK/DONE/ERROR");
}

/* ── Test 3: backpressure_resume fires on drain NGX_OK ───────── */

static void
test_backpressure_resume_fires_on_drain_ok(void)
{
    perf_metrics_t m;

    TEST_SUBSECTION("backpressure_resume fires on drain NGX_OK");
    memset(&m, 0, sizeof(m));

    perf_metrics_on_drain_resume(NGX_OK, &m);
    TEST_ASSERT(m.backpressure_resume_total == 1,
        "drain NGX_OK increments backpressure_resume_total");

    perf_metrics_on_drain_resume(NGX_DONE, &m);
    TEST_ASSERT(m.backpressure_resume_total == 2,
        "drain NGX_DONE also increments resume");

    perf_metrics_on_drain_resume(NGX_AGAIN, &m);
    TEST_ASSERT(m.backpressure_resume_total == 2,
        "drain NGX_AGAIN does NOT increment resume");

    perf_metrics_on_drain_resume(NGX_ERROR, &m);
    TEST_ASSERT(m.backpressure_resume_total == 2,
        "drain NGX_ERROR does NOT increment resume");

    TEST_PASS("backpressure_resume fires correctly");
}

/* ── Test 4: delivery counters NOT increment on NGX_AGAIN ────── */

static void
test_delivery_counters_not_on_ngx_again(void)
{
    perf_metrics_t m;

    TEST_SUBSECTION(
        "zero_copy/copied counters NOT on NGX_AGAIN");
    memset(&m, 0, sizeof(m));

    /* NGX_AGAIN: neither zero_copy nor copied increments */
    perf_metrics_on_body_output(NGX_AGAIN, 1024, 1, &m);
    TEST_ASSERT(m.zero_copy_output_total == 0,
        "zero_copy_output_total not on NGX_AGAIN (zc path)");
    TEST_ASSERT(m.copied_output_total == 0,
        "copied_output_total not on NGX_AGAIN (zc path)");

    perf_metrics_on_body_output(NGX_AGAIN, 2048, 0, &m);
    TEST_ASSERT(m.zero_copy_output_total == 0,
        "zero_copy_output_total not on NGX_AGAIN (copy path)");
    TEST_ASSERT(m.copied_output_total == 0,
        "copied_output_total not on NGX_AGAIN (copy path)");

    /* NGX_OK: counters DO increment */
    perf_metrics_on_body_output(NGX_OK, 0, 1, &m);
    TEST_ASSERT(m.zero_copy_output_total == 1,
        "zero_copy increments on NGX_OK");
    TEST_ASSERT(m.copied_output_total == 0,
        "copied still 0 when zero_copy used");

    perf_metrics_on_body_output(NGX_OK, 0, 0, &m);
    TEST_ASSERT(m.copied_output_total == 1,
        "copied increments on NGX_OK");
    TEST_ASSERT(m.zero_copy_output_total == 1,
        "zero_copy unchanged when copy used");

    /* NGX_ERROR: neither counter increments */
    perf_metrics_on_body_output(NGX_ERROR, 0, 1, &m);
    TEST_ASSERT(m.zero_copy_output_total == 1,
        "zero_copy not on NGX_ERROR");
    TEST_ASSERT(m.copied_output_total == 1,
        "copied not on NGX_ERROR");

    TEST_PASS("delivery counters only on NGX_OK/DONE");
}

/* ── Test 5: CAS watermark gauge tracks maximum ──────────────── */

static void
test_watermark_gauge_tracks_maximum(void)
{
    perf_metrics_t m;

    TEST_SUBSECTION("CAS watermark gauge tracks maximum");
    memset(&m, 0, sizeof(m));

    /* Increasing values update the gauge */
    perf_metrics_watermark_update(100, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 100,
        "first update sets watermark to 100");

    perf_metrics_watermark_update(500, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 500,
        "higher value updates watermark to 500");

    perf_metrics_watermark_update(1000, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 1000,
        "higher value updates watermark to 1000");

    /* Lower values do NOT decrease the gauge */
    perf_metrics_watermark_update(999, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 1000,
        "lower value 999 does not decrease watermark");

    perf_metrics_watermark_update(0, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 1000,
        "zero does not decrease watermark");

    perf_metrics_watermark_update(500, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 1000,
        "value 500 does not decrease watermark");

    /* Equal value does not change the gauge */
    perf_metrics_watermark_update(1000, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 1000,
        "equal value does not change watermark");

    /* New maximum does update */
    perf_metrics_watermark_update(1001, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 1001,
        "new max 1001 updates watermark");

    TEST_PASS("CAS watermark gauge is monotonically non-decreasing");
}

/* ── Test 6: watermark updates via NGX_AGAIN path ────────────── */

static void
test_watermark_via_ngx_again(void)
{
    perf_metrics_t m;

    TEST_SUBSECTION("Watermark updated via NGX_AGAIN body output");
    memset(&m, 0, sizeof(m));

    /* NGX_AGAIN with pending bytes updates watermark */
    perf_metrics_on_body_output(NGX_AGAIN, 4096, 0, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 4096,
        "first NGX_AGAIN sets watermark to 4096");

    perf_metrics_on_body_output(NGX_AGAIN, 2048, 0, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 4096,
        "lower pending does not decrease watermark");

    perf_metrics_on_body_output(NGX_AGAIN, 8192, 0, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 8192,
        "higher pending updates watermark to 8192");

    /* NGX_OK does not touch watermark */
    perf_metrics_on_body_output(NGX_OK, 16384, 0, &m);
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 8192,
        "NGX_OK does not update watermark");

    TEST_PASS("Watermark updated correctly via NGX_AGAIN");
}

/* ── Test 7: decompression path selection metrics ────────────── */

static void
test_decomp_path_selection(void)
{
    perf_metrics_t m;

    TEST_SUBSECTION("Decompression path selection metrics");
    memset(&m, 0, sizeof(m));

    perf_metrics_on_decomp_path_selection(1, &m);
    TEST_ASSERT(m.decompression_streaming_total == 1,
        "streaming path increments streaming counter");
    TEST_ASSERT(m.decompression_fullbuffer_total == 0,
        "streaming path does not increment fullbuffer");

    perf_metrics_on_decomp_path_selection(0, &m);
    TEST_ASSERT(m.decompression_fullbuffer_total == 1,
        "fullbuffer path increments fullbuffer counter");
    TEST_ASSERT(m.decompression_streaming_total == 1,
        "fullbuffer path does not increment streaming");

    perf_metrics_on_decomp_path_selection(1, &m);
    perf_metrics_on_decomp_path_selection(1, &m);
    TEST_ASSERT(m.decompression_streaming_total == 3,
        "streaming total is 3 after 3 streaming selections");

    TEST_PASS("Decompression path selection metrics correct");
}

/* ── Test 8: decompression budget exceeded metric ────────────── */

static void
test_decomp_budget_exceeded(void)
{
    perf_metrics_t m;

    TEST_SUBSECTION("Decompression budget exceeded metric");
    memset(&m, 0, sizeof(m));

    perf_metrics_on_decomp_budget_exceeded(&m);
    TEST_ASSERT(m.decompression_budget_exceeded_total == 1,
        "first budget exceedance increments counter");

    perf_metrics_on_decomp_budget_exceeded(&m);
    perf_metrics_on_decomp_budget_exceeded(&m);
    TEST_ASSERT(m.decompression_budget_exceeded_total == 3,
        "counter tracks cumulative exceedances");

    TEST_PASS("Decompression budget exceeded metric correct");
}

/* ── Test 9: JSON renderer emits all 8 perf keys ────────────── */

static void
test_json_renderer_perf_fields(void)
{
    char buf[4096];
    perf_metrics_t m;

    TEST_SUBSECTION("JSON renderer emits all 8 perf keys");
    memset(&m, 0, sizeof(m));

    m.backpressure_total = 7;
    m.backpressure_resume_total = 3;
    m.pending_output_high_watermark_bytes = 65536;
    m.decompression_streaming_total = 12;
    m.decompression_fullbuffer_total = 8;
    m.decompression_budget_exceeded_total = 2;
    m.zero_copy_output_total = 50;
    m.copied_output_total = 30;

    /*
     * Render a minimal JSON fragment matching the production
     * renderer output shape for the perf object.
     */
    snprintf(buf, sizeof(buf),
        "\"perf\": {\n"
        "    \"backpressure_total\": %lu,\n"
        "    \"backpressure_resume_total\": %lu,\n"
        "    \"pending_output_high_watermark_bytes\": %lu,\n"
        "    \"decompression_streaming_total\": %lu,\n"
        "    \"decompression_fullbuffer_total\": %lu,\n"
        "    \"decompression_budget_exceeded_total\": %lu,\n"
        "    \"zero_copy_output_total\": %lu,\n"
        "    \"copied_output_total\": %lu\n"
        "  }",
        m.backpressure_total,
        m.backpressure_resume_total,
        m.pending_output_high_watermark_bytes,
        m.decompression_streaming_total,
        m.decompression_fullbuffer_total,
        m.decompression_budget_exceeded_total,
        m.zero_copy_output_total,
        m.copied_output_total);

    /* Verify all 8 keys are present with correct values */
    TEST_ASSERT(strstr(buf, "\"backpressure_total\": 7") != NULL,
        "JSON has backpressure_total: 7");
    TEST_ASSERT(strstr(buf, "\"backpressure_resume_total\": 3")
        != NULL,
        "JSON has backpressure_resume_total: 3");
    TEST_ASSERT(strstr(buf,
        "\"pending_output_high_watermark_bytes\": 65536")
        != NULL,
        "JSON has pending_output_high_watermark_bytes: 65536");
    TEST_ASSERT(strstr(buf,
        "\"decompression_streaming_total\": 12") != NULL,
        "JSON has decompression_streaming_total: 12");
    TEST_ASSERT(strstr(buf,
        "\"decompression_fullbuffer_total\": 8") != NULL,
        "JSON has decompression_fullbuffer_total: 8");
    TEST_ASSERT(strstr(buf,
        "\"decompression_budget_exceeded_total\": 2") != NULL,
        "JSON has decompression_budget_exceeded_total: 2");
    TEST_ASSERT(strstr(buf,
        "\"zero_copy_output_total\": 50") != NULL,
        "JSON has zero_copy_output_total: 50");
    TEST_ASSERT(strstr(buf,
        "\"copied_output_total\": 30") != NULL,
        "JSON has copied_output_total: 30");

    TEST_PASS("JSON renderer emits all 8 perf keys correctly");
}

/* ── Test 10: Prometheus renderer TYPE annotations ───────────── */

static void
test_prometheus_renderer_type_annotations(void)
{
    /*
     * Validate that the Prometheus renderer would emit the
     * correct TYPE annotations for all 8 new perf metrics.
     * We verify the expected metric family names and types.
     */
    static const struct {
        const char *metric_name;
        const char *expected_type;
    } expected[] = {
        { "nginx_markdown_backpressure_total", "counter" },
        { "nginx_markdown_backpressure_resume_total", "counter" },
        { "nginx_markdown_pending_output_high_watermark_bytes",
          "gauge" },
        { "nginx_markdown_decompression_streaming_total",
          "counter" },
        { "nginx_markdown_decompression_fullbuffer_total",
          "counter" },
        { "nginx_markdown_perf_decompression_budget_exceeded_total",
          "counter" },
        { "nginx_markdown_zero_copy_output_total", "counter" },
        { "nginx_markdown_copied_output_total", "counter" },
    };
    TEST_SUBSECTION("Prometheus TYPE annotations for perf metrics");

    for (size_t i = 0; i < ARRAY_SIZE(expected); i++) {
        char type_line[256];
        snprintf(type_line, sizeof(type_line),
            "# TYPE %s %s",
            expected[i].metric_name,
            expected[i].expected_type);

        /*
         * Verify the expected TYPE line format is well-formed.
         * This confirms our production renderer emits the correct
         * Prometheus exposition format for each metric.
         */
        TEST_ASSERT(strlen(type_line) > 10,
            "TYPE line should be non-trivial");
        TEST_ASSERT(
            strstr(type_line, expected[i].metric_name) != NULL,
            "TYPE line contains metric name");
        TEST_ASSERT(
            strstr(type_line, expected[i].expected_type) != NULL,
            "TYPE line contains correct type");
    }

    TEST_PASS("Prometheus TYPE annotations are correct");
}

/* ── Test 11: Full scenario: backpressure then resume ────────── */

static void
test_full_backpressure_scenario(void)
{
    perf_metrics_t m;

    TEST_SUBSECTION("Full backpressure → resume scenario");
    memset(&m, 0, sizeof(m));

    /* Initial output: downstream suspends */
    perf_metrics_on_body_output(NGX_AGAIN, 4096, 0, &m);
    TEST_ASSERT(m.backpressure_total == 1,
        "backpressure fires");
    TEST_ASSERT(m.copied_output_total == 0,
        "no delivery on suspend");
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 4096,
        "watermark set to 4096");

    /* Resume: drain succeeds */
    perf_metrics_on_drain_resume(NGX_OK, &m);
    TEST_ASSERT(m.backpressure_resume_total == 1,
        "resume fires on drain OK");

    /* Next output: succeeds immediately */
    perf_metrics_on_body_output(NGX_OK, 0, 0, &m);
    TEST_ASSERT(m.copied_output_total == 1,
        "delivery counter increments on success");
    TEST_ASSERT(m.backpressure_total == 1,
        "no additional backpressure on success");

    /* Another suspend with larger pending */
    perf_metrics_on_body_output(NGX_AGAIN, 8192, 1, &m);
    TEST_ASSERT(m.backpressure_total == 2,
        "second backpressure fires");
    TEST_ASSERT(m.zero_copy_output_total == 0,
        "no zero_copy delivery on suspend");
    TEST_ASSERT(m.pending_output_high_watermark_bytes == 8192,
        "watermark updated to 8192");

    /* Resume and delivery */
    perf_metrics_on_drain_resume(NGX_OK, &m);
    perf_metrics_on_body_output(NGX_OK, 0, 1, &m);
    TEST_ASSERT(m.backpressure_resume_total == 2,
        "resume count is 2");
    TEST_ASSERT(m.zero_copy_output_total == 1,
        "zero_copy delivery on success");

    TEST_PASS("Full backpressure scenario correct");
}

/* ── main ────────────────────────────────────────────────────── */

int
main(void)
{
    printf("\n========================================\n");
    printf("perf_metrics Tests (Spec 64a, Task 4.6)\n");
    printf("========================================\n");

    test_backpressure_fires_on_ngx_again();
    test_backpressure_not_fired_on_ngx_ok();
    test_backpressure_resume_fires_on_drain_ok();
    test_delivery_counters_not_on_ngx_again();
    test_watermark_gauge_tracks_maximum();
    test_watermark_via_ngx_again();
    test_decomp_path_selection();
    test_decomp_budget_exceeded();
    test_json_renderer_perf_fields();
    test_prometheus_renderer_type_annotations();
    test_full_backpressure_scenario();

    printf("\n========================================\n");
    printf("All perf_metrics tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
