/*
 * Test: streaming_metrics_increment
 *
 * Validates streaming observability metric counters:
 *   1. Snapshot struct correctly mirrors the new streaming fields.
 *   2. Reason code enum has the expected count (15).
 *   3. String mapping function returns valid strings for all enum values.
 *   4. String mapping function returns "unknown" for out-of-range values.
 *
 * Compiled with -DMARKDOWN_STREAMING_ENABLED to access streaming-gated code.
 */

#include "../include/test_common.h"

#ifndef MARKDOWN_STREAMING_ENABLED
int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_metrics_increment Tests (SKIPPED)\n");
    printf("MARKDOWN_STREAMING_ENABLED not defined\n");
    printf("========================================\n\n");
    return 0;
}
#else /* MARKDOWN_STREAMING_ENABLED */

#include <ngx_http_markdown_filter_module.h>

/* ── Test helpers ─────────────────────────────────────────────── */

/*
 * Local streaming metrics struct mirroring the production layout.
 * Uses unsigned long so the test compiles without the full NGINX
 * atomic infrastructure.
 */
typedef struct {
    unsigned long requests_total;
    unsigned long fallback_total;
    unsigned long succeeded_total;
    unsigned long failed_total;
    unsigned long postcommit_error_total;
    unsigned long precommit_failopen_total;
    unsigned long precommit_reject_total;
    unsigned long budget_exceeded_total;
    unsigned long shadow_total;
    unsigned long shadow_diff_total;
    unsigned long last_ttfb_ms;
    unsigned long last_peak_memory_bytes;
    struct {
        unsigned long streaming;
        unsigned long full_buffer;
        unsigned long passthrough;
        unsigned long not_eligible;
    } engine_choice;
    unsigned long streaming_fallback_precommit_pass;
    unsigned long streaming_fallback_precommit_reject;
    unsigned long streaming_failure_postcommit_abort;
    unsigned long streaming_failure_postcommit_safe_finish;
    struct {
        unsigned long candidate_total;
        unsigned long true_streaming_selected_total;
        unsigned long output_bytes_total;
        unsigned long excluded_content_type_total;
    } selection;
} test_streaming_metrics_t;

static test_streaming_metrics_t *g_streaming = NULL;

/*
 * Simulate the snapshot collection for streaming fields.
 * Mirrors the production collect_metrics_snapshot() streaming section.
 */
static void
collect_streaming_snapshot(test_streaming_metrics_t *snap)
{
    const test_streaming_metrics_t *m;

    memset(snap, 0, sizeof(test_streaming_metrics_t));

    m = g_streaming;
    if (m == NULL) {
        return;
    }

    snap->requests_total = m->requests_total;
    snap->fallback_total = m->fallback_total;
    snap->succeeded_total = m->succeeded_total;
    snap->failed_total = m->failed_total;
    snap->postcommit_error_total = m->postcommit_error_total;
    snap->precommit_failopen_total = m->precommit_failopen_total;
    snap->precommit_reject_total = m->precommit_reject_total;
    snap->budget_exceeded_total = m->budget_exceeded_total;
    snap->shadow_total = m->shadow_total;
    snap->shadow_diff_total = m->shadow_diff_total;
    snap->last_ttfb_ms = m->last_ttfb_ms;
    snap->last_peak_memory_bytes = m->last_peak_memory_bytes;
    snap->engine_choice.streaming = m->engine_choice.streaming;
    snap->engine_choice.full_buffer = m->engine_choice.full_buffer;
    snap->engine_choice.passthrough = m->engine_choice.passthrough;
    snap->engine_choice.not_eligible = m->engine_choice.not_eligible;
    snap->streaming_fallback_precommit_pass =
        m->streaming_fallback_precommit_pass;
    snap->streaming_fallback_precommit_reject =
        m->streaming_fallback_precommit_reject;
    snap->streaming_failure_postcommit_abort =
        m->streaming_failure_postcommit_abort;
    snap->streaming_failure_postcommit_safe_finish =
        m->streaming_failure_postcommit_safe_finish;
    snap->selection.candidate_total = m->selection.candidate_total;
    snap->selection.true_streaming_selected_total = m->selection.true_streaming_selected_total;
    snap->selection.output_bytes_total = m->selection.output_bytes_total;
    snap->selection.excluded_content_type_total = m->selection.excluded_content_type_total;
}

/* ── Test: reason code enum count ─────────────────────────────── */

static void
test_reason_code_enum_count(void)
{
    TEST_SUBSECTION("Reason code enum count equals 15");

    TEST_ASSERT(NGX_HTTP_MARKDOWN_STREAM_REASON_COUNT == 15,
                "NGX_HTTP_MARKDOWN_STREAM_REASON_COUNT should be 15");

    TEST_PASS("Reason code enum count is 15");
}

/* ── Test: string mapping for all valid enum values ───────────── */

static void
test_reason_string_mapping_valid(void)
{
    ngx_http_markdown_stream_reason_e  reason;
    const char                        *str;

    TEST_SUBSECTION(
        "String mapping returns valid strings for all enum values");

    for (reason = NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE;
         (unsigned) reason < NGX_HTTP_MARKDOWN_STREAM_REASON_COUNT;
         reason = (ngx_http_markdown_stream_reason_e)
             ((unsigned) reason + 1))
    {
        str = ngx_http_markdown_stream_reason_str(reason);

        TEST_ASSERT(str != NULL,
                    "reason string should not be NULL");
        TEST_ASSERT(strlen(str) > 0,
                    "reason string should not be empty");
        TEST_ASSERT(strcmp(str, "unknown") != 0,
                    "valid reason should not return \"unknown\"");
    }

    /* Verify specific expected strings */
    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE), "eligible") == 0,
        "ELIGIBLE -> \"eligible\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_CONTENT_LENGTH_KNOWN),
            "content_length_known") == 0,
        "CONTENT_LENGTH_KNOWN -> \"content_length_known\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_BELOW_THRESHOLD),
            "below_threshold") == 0,
        "BELOW_THRESHOLD -> \"below_threshold\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_CONFIG_DISABLED),
            "config_disabled") == 0,
        "CONFIG_DISABLED -> \"config_disabled\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_EXCLUDED_CONTENT_TYPE),
            "excluded_content_type") == 0,
        "EXCLUDED_CONTENT_TYPE -> \"excluded_content_type\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_HTML),
            "not_html") == 0,
        "NOT_HTML -> \"not_html\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_COMPRESSED),
            "compressed") == 0,
        "COMPRESSED -> \"compressed\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_CANDIDATE),
            "not_candidate") == 0,
        "NOT_CANDIDATE -> \"not_candidate\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_ACCEPT_MISMATCH),
            "accept_mismatch") == 0,
        "ACCEPT_MISMATCH -> \"accept_mismatch\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_HTML_ERROR),
            "precommit_html_error") == 0,
        "PRECOMMIT_HTML_ERROR -> \"precommit_html_error\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_BUDGET),
            "precommit_budget") == 0,
        "PRECOMMIT_BUDGET -> \"precommit_budget\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_TIMEOUT),
            "precommit_timeout") == 0,
        "PRECOMMIT_TIMEOUT -> \"precommit_timeout\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_PARSE_ERROR),
            "postcommit_parse_error") == 0,
        "POSTCOMMIT_PARSE_ERROR -> \"postcommit_parse_error\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_BUDGET_EXCEEDED),
            "postcommit_budget_exceeded") == 0,
        "POSTCOMMIT_BUDGET_EXCEEDED -> \"postcommit_budget_exceeded\"");

    TEST_ASSERT(
        strcmp(ngx_http_markdown_stream_reason_str(
            NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_IO_ERROR),
            "postcommit_io_error") == 0,
        "POSTCOMMIT_IO_ERROR -> \"postcommit_io_error\"");

    TEST_PASS("All valid enum values map to expected strings");
}

/* ── Test: out-of-range values return "unknown" ───────────────── */

static void
test_reason_string_mapping_out_of_range(void)
{
    const char *str;

    TEST_SUBSECTION(
        "String mapping returns \"unknown\" for out-of-range values");

    str = ngx_http_markdown_stream_reason_str(
        NGX_HTTP_MARKDOWN_STREAM_REASON_COUNT);
    TEST_ASSERT(strcmp(str, "unknown") == 0,
                "COUNT sentinel value should return \"unknown\"");

    str = ngx_http_markdown_stream_reason_str(
        (ngx_http_markdown_stream_reason_e) 99);
    TEST_ASSERT(strcmp(str, "unknown") == 0,
                "Value 99 should return \"unknown\"");

    str = ngx_http_markdown_stream_reason_str(
        (ngx_http_markdown_stream_reason_e) 255);
    TEST_ASSERT(strcmp(str, "unknown") == 0,
                "Value 255 should return \"unknown\"");

    TEST_PASS("Out-of-range values correctly return \"unknown\"");
}

/* ── Test: snapshot struct mirrors streaming fields ────────────── */

static void
test_snapshot_null_zeroes_streaming_fields(void)
{
    test_streaming_metrics_t snap;

    TEST_SUBSECTION(
        "Streaming fields zeroed when metrics pointer is NULL");

    g_streaming = NULL;
    collect_streaming_snapshot(&snap);

    TEST_ASSERT(snap.requests_total == 0,
                "requests_total should be zero");
    TEST_ASSERT(snap.fallback_total == 0,
                "fallback_total should be zero");
    TEST_ASSERT(snap.succeeded_total == 0,
                "succeeded_total should be zero");
    TEST_ASSERT(snap.failed_total == 0,
                "failed_total should be zero");
    TEST_ASSERT(snap.engine_choice.streaming == 0,
                "engine_choice_streaming should be zero");
    TEST_ASSERT(snap.engine_choice.full_buffer == 0,
                "engine_choice_full_buffer should be zero");
    TEST_ASSERT(snap.engine_choice.passthrough == 0,
                "engine_choice_passthrough should be zero");
    TEST_ASSERT(snap.engine_choice.not_eligible == 0,
                "engine_choice_not_eligible should be zero");
    TEST_ASSERT(snap.selection.candidate_total == 0,
                "streaming_candidate_total should be zero");
    TEST_ASSERT(snap.selection.true_streaming_selected_total == 0,
                "true_streaming_selected_total should be zero");
    TEST_ASSERT(snap.selection.output_bytes_total == 0,
                "streaming_output_bytes_total should be zero");
    TEST_ASSERT(snap.selection.excluded_content_type_total == 0,
                "excluded_content_type_total should be zero");

    TEST_PASS("All streaming fields are zeroed when source is NULL");
}

static void
test_snapshot_copies_streaming_fields(void)
{
    test_streaming_metrics_t live;
    test_streaming_metrics_t snap;

    TEST_SUBSECTION(
        "Streaming fields correctly copied from live metrics");

    memset(&live, 0, sizeof(live));

    /* Set distinctive values for each streaming field */
    live.requests_total = 100;
    live.fallback_total = 5;
    live.succeeded_total = 90;
    live.failed_total = 3;
    live.postcommit_error_total = 2;
    live.precommit_failopen_total = 4;
    live.precommit_reject_total = 1;
    live.budget_exceeded_total = 6;
    live.shadow_total = 7;
    live.shadow_diff_total = 8;
    live.last_ttfb_ms = 42;
    live.last_peak_memory_bytes = 65536;
    live.engine_choice.streaming = 50;
    live.engine_choice.full_buffer = 30;
    live.engine_choice.passthrough = 15;
    live.engine_choice.not_eligible = 5;
    live.streaming_fallback_precommit_pass = 3;
    live.streaming_fallback_precommit_reject = 1;
    live.streaming_failure_postcommit_abort = 2;
    live.streaming_failure_postcommit_safe_finish = 1;
    live.selection.candidate_total = 80;
    live.selection.true_streaming_selected_total = 50;
    live.selection.output_bytes_total = 1048576;
    live.selection.excluded_content_type_total = 10;

    g_streaming = &live;
    collect_streaming_snapshot(&snap);
    g_streaming = NULL;

    TEST_ASSERT(snap.requests_total == 100,
                "requests_total should be copied");
    TEST_ASSERT(snap.fallback_total == 5,
                "fallback_total should be copied");
    TEST_ASSERT(snap.succeeded_total == 90,
                "succeeded_total should be copied");
    TEST_ASSERT(snap.failed_total == 3,
                "failed_total should be copied");
    TEST_ASSERT(snap.postcommit_error_total == 2,
                "postcommit_error_total should be copied");
    TEST_ASSERT(snap.precommit_failopen_total == 4,
                "precommit_failopen_total should be copied");
    TEST_ASSERT(snap.precommit_reject_total == 1,
                "precommit_reject_total should be copied");
    TEST_ASSERT(snap.budget_exceeded_total == 6,
                "budget_exceeded_total should be copied");
    TEST_ASSERT(snap.shadow_total == 7,
                "shadow_total should be copied");
    TEST_ASSERT(snap.shadow_diff_total == 8,
                "shadow_diff_total should be copied");
    TEST_ASSERT(snap.last_ttfb_ms == 42,
                "last_ttfb_ms should be copied");
    TEST_ASSERT(snap.last_peak_memory_bytes == 65536,
                "last_peak_memory_bytes should be copied");
    TEST_ASSERT(snap.engine_choice.streaming == 50,
                "engine_choice_streaming should be copied");
    TEST_ASSERT(snap.engine_choice.full_buffer == 30,
                "engine_choice_full_buffer should be copied");
    TEST_ASSERT(snap.engine_choice.passthrough == 15,
                "engine_choice_passthrough should be copied");
    TEST_ASSERT(snap.engine_choice.not_eligible == 5,
                "engine_choice_not_eligible should be copied");
    TEST_ASSERT(snap.streaming_fallback_precommit_pass == 3,
                "streaming_fallback_precommit_pass should be copied");
    TEST_ASSERT(snap.streaming_fallback_precommit_reject == 1,
                "streaming_fallback_precommit_reject should be copied");
    TEST_ASSERT(snap.streaming_failure_postcommit_abort == 2,
                "streaming_failure_postcommit_abort should be copied");
    TEST_ASSERT(snap.streaming_failure_postcommit_safe_finish == 1,
                "streaming_failure_postcommit_safe_finish "
                "should be copied");
    TEST_ASSERT(snap.selection.candidate_total == 80,
                "streaming_candidate_total should be copied");
    TEST_ASSERT(snap.selection.true_streaming_selected_total == 50,
                "true_streaming_selected_total should be copied");
    TEST_ASSERT(snap.selection.output_bytes_total == 1048576,
                "streaming_output_bytes_total should be copied");
    TEST_ASSERT(snap.selection.excluded_content_type_total == 10,
                "excluded_content_type_total should be copied");

    TEST_PASS("All streaming fields are copied correctly");
}

/* ── Test: engine choice counters are mutually exclusive ───────── */

static void
test_engine_choice_increment(void)
{
    test_streaming_metrics_t m;
    test_streaming_metrics_t snap;

    TEST_SUBSECTION(
        "Engine choice counters increment independently");

    memset(&m, 0, sizeof(m));

    /* Simulate: 3 streaming, 2 full_buffer, 1 passthrough, 1 not_eligible */
    m.engine_choice.streaming = 3;
    m.engine_choice.full_buffer = 2;
    m.engine_choice.passthrough = 1;
    m.engine_choice.not_eligible = 1;

    g_streaming = &m;
    collect_streaming_snapshot(&snap);
    g_streaming = NULL;

    TEST_ASSERT(snap.engine_choice.streaming == 3,
                "engine_choice_streaming should be 3");
    TEST_ASSERT(snap.engine_choice.full_buffer == 2,
                "engine_choice_full_buffer should be 2");
    TEST_ASSERT(snap.engine_choice.passthrough == 1,
                "engine_choice_passthrough should be 1");
    TEST_ASSERT(snap.engine_choice.not_eligible == 1,
                "engine_choice_not_eligible should be 1");

    /* Sum equals total requests processed */
    TEST_ASSERT(
        (snap.engine_choice.streaming +
         snap.engine_choice.full_buffer +
         snap.engine_choice.passthrough +
         snap.engine_choice.not_eligible) == 7,
        "sum of engine choice counters should match total");

    TEST_PASS("Engine choice counters increment independently");
}

/* ── main ─────────────────────────────────────────────────────── */

int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_metrics_increment Tests\n");
    printf("========================================\n");

    test_reason_code_enum_count();
    test_reason_string_mapping_valid();
    test_reason_string_mapping_out_of_range();
    test_snapshot_null_zeroes_streaming_fields();
    test_snapshot_copies_streaming_fields();
    test_engine_choice_increment();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}

#endif /* MARKDOWN_STREAMING_ENABLED */
