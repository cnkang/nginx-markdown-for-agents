/*
 * Test: diagnostics_accessors
 *
 * Validates the diagnostics accessor functions that bridge the
 * diagnostics compilation unit with module-internal state:
 *   - collect_metrics (reads SHM metrics zone)
 *   - get_dynconf_state (reads dynconf watcher)
 *   - trigger_rollback (delegates to dynconf rollback)
 *
 * Coverage targets:
 *   ngx_http_markdown_diagnostics_accessors_impl.h
 */

#include "../include/test_common.h"
#include <ngx_config.h>
#include <ngx_core.h>

struct ngx_log_s {
    int dummy;
};

#define NGX_OK         0
#define NGX_ERROR     -1

#define ngx_memzero(buf, n) memset(buf, 0, n)

static ngx_log_t g_log;

/* ── Metrics struct (mirrors production SHM layout) ───────────── */

typedef struct {
    ngx_atomic_t  conversions_succeeded;
    struct {
        ngx_atomic_t  delivery_count;
        ngx_atomic_t  failopen_count;
    } results;
    ngx_atomic_t  requests_entered;
#ifdef MARKDOWN_STREAMING_ENABLED
    struct {
        ngx_atomic_t  requests_total;
        ngx_atomic_t  fallback_total;
        ngx_atomic_t  succeeded_total;
        ngx_atomic_t  failed_total;
        ngx_atomic_t  postcommit_error_total;
        ngx_atomic_t  precommit_failopen_total;
        ngx_atomic_t  precommit_reject_total;
        ngx_atomic_t  budget_exceeded_total;
        ngx_atomic_t  shadow_total;
        ngx_atomic_t  shadow_diff_total;
        ngx_atomic_t  last_ttfb_ms;
        ngx_atomic_t  last_peak_memory_bytes;
        ngx_atomic_t  streaming_fallback_precommit_pass;
        ngx_atomic_t  streaming_fallback_precommit_reject;
        ngx_atomic_t  streaming_failure_postcommit_abort;
        ngx_atomic_t  streaming_failure_postcommit_safe_finish;
        struct {
            ngx_atomic_t  streaming;
            ngx_atomic_t  full_buffer;
            ngx_atomic_t  passthrough;
            ngx_atomic_t  not_eligible;
        } engine_choice;
        struct {
            ngx_atomic_t  candidate_total;
            ngx_atomic_t  true_streaming_selected_total;
            ngx_atomic_t  output_bytes_total;
            ngx_atomic_t  excluded_content_type_total;
        } selection;
    } streaming;
#endif
    struct {
        ngx_atomic_t  backpressure_total;
        ngx_atomic_t  backpressure_resume_total;
        ngx_atomic_t  pending_output_high_watermark_bytes;
        ngx_atomic_t  decompression_streaming_total;
        ngx_atomic_t  decompression_fullbuffer_total;
        ngx_atomic_t  decompression_budget_exceeded_total;
        ngx_atomic_t  zero_copy_output_total;
        ngx_atomic_t  copied_output_total;
    } perf;
} ngx_http_markdown_metrics_t;

/* Global metrics pointer (mirrors production) */
static ngx_http_markdown_metrics_t  g_metrics_data;
static ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;

/* ── Dynconf watcher struct (mirrors production) ──────────────── */

typedef struct {
    ngx_flag_t  active;
    time_t      applied_mtime;
    ngx_uint_t  version;
    time_t      last_mtime;
    ngx_flag_t  lkg_valid;
    time_t      lkg_mtime;
} ngx_http_markdown_dynconf_watcher_t;

static ngx_http_markdown_dynconf_watcher_t ngx_http_markdown_dynconf_watcher;

ngx_int_t ngx_http_markdown_dynconf_rollback(
    ngx_http_markdown_dynconf_watcher_t *watcher, ngx_log_t *log);

/* ── Inflight overload stub ────────────────────────────────────── */

static ngx_atomic_int_t g_inflight_overload_total;

static ngx_inline ngx_atomic_int_t
ngx_http_markdown_inflight_overload_total(void)
{
    return g_inflight_overload_total;
}

/* ── Production function headers and implementation ───────────── */

#include "ngx_http_markdown_diagnostics_accessors_impl.h"

/* ── Rollback return codes ────────────────────────────────────── */

#define NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK         0
#define NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_NO_LKG    -1
#define NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_APPLY_ERR -2

static int g_rollback_called;
static ngx_int_t g_rollback_result;

ngx_int_t
ngx_http_markdown_dynconf_rollback(
    ngx_http_markdown_dynconf_watcher_t *watcher, ngx_log_t *log)
{
    (void) watcher;
    (void) log;
    g_rollback_called = 1;
    return g_rollback_result;
}

/* ── Tests ─────────────────────────────────────────────────────── */

static void
test_collect_metrics_null_output(void)
{
    TEST_SUBSECTION("collect_metrics with NULL output");

    /* Should not crash */
    ngx_http_markdown_diagnostics_collect_metrics(NULL);

    TEST_PASS("NULL output is no-op");
}

static void
test_collect_metrics_null_zone(void)
{
    ngx_http_markdown_diag_metrics_t out;

    TEST_SUBSECTION("collect_metrics with NULL metrics zone");

    ngx_http_markdown_metrics = NULL;
    memset(&out, 0xFF, sizeof(out));

    ngx_http_markdown_diagnostics_collect_metrics(&out);

    TEST_ASSERT(out.conversions_total == 0, "conversions should be 0");
    TEST_ASSERT(out.delivery_total == 0, "delivery should be 0");
    TEST_ASSERT(out.requests_total == 0, "requests should be 0");
    TEST_ASSERT(out.failopen_total == 0, "failopen should be 0");

    TEST_PASS("NULL zone zeroes all fields");
}

static void
test_collect_metrics_with_data(void)
{
    ngx_http_markdown_diag_metrics_t out;

    TEST_SUBSECTION("collect_metrics with populated zone");

    memset(&g_metrics_data, 0, sizeof(g_metrics_data));
    g_metrics_data.conversions_succeeded = 42;
    g_metrics_data.results.delivery_count = 100;
    g_metrics_data.requests_entered = 200;
    g_metrics_data.results.failopen_count = 3;
    ngx_http_markdown_metrics = &g_metrics_data;

    ngx_http_markdown_diagnostics_collect_metrics(&out);

    TEST_ASSERT(out.conversions_total == 42, "conversions should be 42");
    TEST_ASSERT(out.delivery_total == 100, "delivery should be 100");
    TEST_ASSERT(out.requests_total == 200, "requests should be 200");
    TEST_ASSERT(out.failopen_total == 3, "failopen should be 3");

    TEST_PASS("Metrics collected correctly");
}

#ifdef MARKDOWN_STREAMING_ENABLED
static void
test_collect_metrics_streaming(void)
{
    ngx_http_markdown_diag_metrics_t out;

    TEST_SUBSECTION("collect_metrics with streaming counters");

    memset(&g_metrics_data, 0, sizeof(g_metrics_data));
    g_metrics_data.conversions_succeeded = 10;
    g_metrics_data.results.delivery_count = 50;
    g_metrics_data.requests_entered = 100;
    g_metrics_data.results.failopen_count = 1;
    g_metrics_data.streaming.requests_total = 30;
    g_metrics_data.streaming.succeeded_total = 25;
    g_metrics_data.streaming.failed_total = 5;
    g_metrics_data.streaming.fallback_total = 2;
    g_metrics_data.streaming.selection.candidate_total = 35;
    g_metrics_data.streaming.selection.output_bytes_total = 1024000;
    g_metrics_data.streaming.engine_choice.streaming = 20;
    g_metrics_data.streaming.engine_choice.full_buffer = 10;
    ngx_http_markdown_metrics = &g_metrics_data;

    ngx_http_markdown_diagnostics_collect_metrics(&out);

    TEST_ASSERT(out.conversions_total == 10, "conversions should be 10");
    TEST_ASSERT(out.streaming_requests_total == 30,
                "streaming_requests should be 30");
    TEST_ASSERT(out.streaming_succeeded_total == 25,
                "streaming_succeeded should be 25");
    TEST_ASSERT(out.streaming_failed_total == 5,
                "streaming_failed should be 5");
    TEST_ASSERT(out.streaming_fallback_total == 2,
                "streaming_fallback should be 2");
    TEST_ASSERT(out.streaming_candidate_total == 35,
                "streaming_candidate should be 35");
    TEST_ASSERT(out.streaming_output_bytes_total == 1024000,
                "streaming_output_bytes should be 1024000");
    TEST_ASSERT(out.engine_choice_streaming == 20,
                "engine_choice_streaming should be 20");
    TEST_ASSERT(out.engine_choice_full_buffer == 10,
                "engine_choice_full_buffer should be 10");

    TEST_PASS("Streaming metrics collected correctly");
}
#endif

static void
test_get_dynconf_state_null_output(void)
{
    TEST_SUBSECTION("get_dynconf_state with NULL output");

    /* Should not crash */
    ngx_http_markdown_diagnostics_get_dynconf_state(NULL);

    TEST_PASS("NULL output is no-op");
}

static void
test_get_dynconf_state_inactive(void)
{
    ngx_http_markdown_diag_dynconf_t out;

    TEST_SUBSECTION("get_dynconf_state when inactive");

    memset(&ngx_http_markdown_dynconf_watcher, 0,
           sizeof(ngx_http_markdown_dynconf_watcher));
    ngx_http_markdown_dynconf_watcher.active = 0;
    memset(&out, 0xFF, sizeof(out));

    ngx_http_markdown_diagnostics_get_dynconf_state(&out);

    TEST_ASSERT(out.active_mtime == 0, "active_mtime should be 0");
    TEST_ASSERT(out.config_version == 0, "config_version should be 0");
    TEST_ASSERT(out.last_known_good_mtime == 0, "lkg_mtime should be 0");
    TEST_ASSERT(out.lkg_valid == 0, "lkg_valid should be 0");

    TEST_PASS("Inactive watcher zeroes all fields");
}

static void
test_get_dynconf_state_active(void)
{
    ngx_http_markdown_diag_dynconf_t out;

    TEST_SUBSECTION("get_dynconf_state when active");

    memset(&ngx_http_markdown_dynconf_watcher, 0,
           sizeof(ngx_http_markdown_dynconf_watcher));
    ngx_http_markdown_dynconf_watcher.active = 1;
    ngx_http_markdown_dynconf_watcher.applied_mtime = 1700000000;
    ngx_http_markdown_dynconf_watcher.version = 5;
    /*
     * Regression (CMOD-4): last_mtime is the most recently *observed* file
     * mtime (updated even on a rejected reload); lkg_mtime is the mtime of
     * the previous successfully-applied config.  They are deliberately
     * different here so the test fails if the accessor reads last_mtime
     * instead of lkg_mtime.
     */
    ngx_http_markdown_dynconf_watcher.last_mtime = 1699999000;
    ngx_http_markdown_dynconf_watcher.lkg_mtime = 1699998000;
    ngx_http_markdown_dynconf_watcher.lkg_valid = 1;

    ngx_http_markdown_diagnostics_get_dynconf_state(&out);

    TEST_ASSERT(out.active_mtime == 1700000000,
                "active_mtime should match");
    TEST_ASSERT(out.config_version == 5,
                "config_version should be 5");
    TEST_ASSERT(out.last_known_good_mtime == 1699998000,
                "lkg_mtime should reflect the LKG config mtime, "
                "not last_mtime");
    TEST_ASSERT(out.lkg_valid == 1, "lkg_valid should be 1");

    TEST_PASS("Active watcher state collected correctly");
}

static void
test_trigger_rollback_ok(void)
{
    ngx_int_t rc;

    TEST_SUBSECTION("trigger_rollback success");

    g_rollback_called = 0;
    g_rollback_result = NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK;

    rc = ngx_http_markdown_diagnostics_trigger_rollback(&g_log);

    TEST_ASSERT(g_rollback_called == 1, "rollback should be called");
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK,
                "should return OK");

    TEST_PASS("Rollback success correct");
}

static void
test_trigger_rollback_no_lkg(void)
{
    ngx_int_t rc;

    TEST_SUBSECTION("trigger_rollback no LKG");

    g_rollback_called = 0;
    g_rollback_result = NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_NO_LKG;

    rc = ngx_http_markdown_diagnostics_trigger_rollback(&g_log);

    TEST_ASSERT(g_rollback_called == 1, "rollback should be called");
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_NO_LKG,
                "should return NO_LKG");

    TEST_PASS("Rollback no-LKG correct");
}

static void
test_trigger_rollback_apply_err(void)
{
    ngx_int_t rc;

    TEST_SUBSECTION("trigger_rollback apply error");

    g_rollback_called = 0;
    g_rollback_result = NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_APPLY_ERR;

    rc = ngx_http_markdown_diagnostics_trigger_rollback(&g_log);

    TEST_ASSERT(g_rollback_called == 1, "rollback should be called");
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_APPLY_ERR,
                "should return APPLY_ERR");

    TEST_PASS("Rollback apply-error correct");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("diagnostics_accessors Tests\n");
    printf("========================================\n");

    test_collect_metrics_null_output();
    test_collect_metrics_null_zone();
    test_collect_metrics_with_data();
#ifdef MARKDOWN_STREAMING_ENABLED
    test_collect_metrics_streaming();
#endif
    test_get_dynconf_state_null_output();
    test_get_dynconf_state_inactive();
    test_get_dynconf_state_active();
    test_trigger_rollback_ok();
    test_trigger_rollback_no_lkg();
    test_trigger_rollback_apply_err();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
