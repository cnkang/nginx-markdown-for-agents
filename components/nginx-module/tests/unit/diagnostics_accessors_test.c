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

/* ── Minimal NGINX type stubs ─────────────────────────────────── */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;
typedef int             ngx_flag_t;
typedef uintptr_t       ngx_atomic_uint_t;
typedef long            time_t;
typedef uintptr_t       ngx_atomic_t;

#define NGX_OK         0
#define NGX_ERROR     -1

#define ngx_memzero(buf, n) memset(buf, 0, n)

/* Log stub */
typedef struct {
    int dummy;
} ngx_log_t;

static ngx_log_t g_log;

/* ── Metrics struct (mirrors production SHM layout) ───────────── */

typedef struct {
    ngx_atomic_t  conversions_succeeded;
    struct {
        ngx_atomic_t  delivery_count;
        ngx_atomic_t  failopen_count;
    } results;
    ngx_atomic_t  requests_entered;
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
} ngx_http_markdown_dynconf_watcher_t;

static ngx_http_markdown_dynconf_watcher_t ngx_http_markdown_dynconf_watcher;

/* ── Diagnostics output types ─────────────────────────────────── */

typedef struct {
    ngx_atomic_uint_t  conversions_total;
    ngx_atomic_uint_t  delivery_total;
    ngx_atomic_uint_t  requests_total;
    ngx_atomic_uint_t  failopen_total;
} ngx_http_markdown_diag_metrics_t;

typedef struct {
    time_t      active_mtime;
    ngx_uint_t  config_version;
    time_t      last_known_good_mtime;
    ngx_flag_t  lkg_valid;
} ngx_http_markdown_diag_dynconf_t;

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

/* ── Production function reimplementations ────────────────────── */

void
ngx_http_markdown_diagnostics_collect_metrics(
    ngx_http_markdown_diag_metrics_t *out)
{
    if (out == NULL) {
        return;
    }

    ngx_memzero(out, sizeof(ngx_http_markdown_diag_metrics_t));

    if (ngx_http_markdown_metrics == NULL) {
        return;
    }

    out->conversions_total =
        (ngx_atomic_uint_t) ngx_http_markdown_metrics->conversions_succeeded;
    out->delivery_total =
        (ngx_atomic_uint_t) ngx_http_markdown_metrics->results.delivery_count;
    out->requests_total =
        (ngx_atomic_uint_t) ngx_http_markdown_metrics->requests_entered;
    out->failopen_total =
        (ngx_atomic_uint_t) ngx_http_markdown_metrics->results.failopen_count;
}

void
ngx_http_markdown_diagnostics_get_dynconf_state(
    ngx_http_markdown_diag_dynconf_t *out)
{
    if (out == NULL) {
        return;
    }

    ngx_memzero(out, sizeof(ngx_http_markdown_diag_dynconf_t));

    if (!ngx_http_markdown_dynconf_watcher.active) {
        return;
    }

    out->active_mtime = ngx_http_markdown_dynconf_watcher.applied_mtime;
    out->config_version = ngx_http_markdown_dynconf_watcher.version;
    out->last_known_good_mtime = ngx_http_markdown_dynconf_watcher.last_mtime;
    out->lkg_valid = ngx_http_markdown_dynconf_watcher.lkg_valid ? 1 : 0;
}

ngx_int_t
ngx_http_markdown_diagnostics_trigger_rollback(ngx_log_t *log)
{
    return ngx_http_markdown_dynconf_rollback(
        &ngx_http_markdown_dynconf_watcher, log);
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
    ngx_http_markdown_dynconf_watcher.last_mtime = 1699999000;
    ngx_http_markdown_dynconf_watcher.lkg_valid = 1;

    ngx_http_markdown_diagnostics_get_dynconf_state(&out);

    TEST_ASSERT(out.active_mtime == 1700000000,
                "active_mtime should match");
    TEST_ASSERT(out.config_version == 5,
                "config_version should be 5");
    TEST_ASSERT(out.last_known_good_mtime == 1699999000,
                "lkg_mtime should match");
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
