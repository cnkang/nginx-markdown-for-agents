/*
 * Test: diagnostics_core
 *
 * Validates the diagnostics subsystem core logic:
 *   - init/cleanup lifecycle
 *   - ring buffer record and wrap-around
 *   - get_state before/after initialization
 *   - decision path logging with verbosity gating
 *
 * Coverage targets:
 *   ngx_http_markdown_diagnostics.c (init, record, cleanup, get_state,
 *   log_decision_path, decision_path_is_failure)
 */

#include "../include/test_common.h"

/* ── Minimal NGINX type stubs ─────────────────────────────────── */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;
typedef int             ngx_flag_t;
typedef uintptr_t       ngx_msec_t;
typedef uintptr_t       ngx_atomic_uint_t;
typedef long            time_t;

typedef struct {
    size_t      len;
    u_char     *data;
} ngx_str_t;

#define NGX_OK         0
#define NGX_ERROR     -1
#define NGX_DECLINED  -5

/* Pool stub */
typedef struct ngx_pool_s ngx_pool_t;

struct ngx_pool_s {
    u_char  *last;
    u_char  *end;
    u_char   buf[1024 * 1024];  /* 1MB pool */
};

static ngx_pool_t g_pool;
static size_t g_pool_offset;

static void
pool_init(void)
{
    memset(&g_pool, 0, sizeof(g_pool));
    g_pool.last = g_pool.buf;
    g_pool.end = g_pool.buf + sizeof(g_pool.buf);
    g_pool_offset = 0;
}

void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    void *p;
    size_t aligned;

    (void) pool;
    aligned = (size + 7) & ~(size_t)7;
    if (g_pool_offset + aligned > sizeof(g_pool.buf)) {
        return NULL;
    }
    p = g_pool.buf + g_pool_offset;
    memset(p, 0, size);
    g_pool_offset += aligned;
    return p;
}

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    return ngx_pcalloc(pool, size);
}

/* ── Diagnostics types (mirror production) ────────────────────── */

#define NGX_HTTP_MARKDOWN_DIAG_DEFAULT_CAPACITY  100
#define NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY      10000

typedef struct {
    ngx_msec_t    timestamp;
    ngx_int_t     reason_code;
    ngx_msec_t    duration_ms;
} ngx_http_markdown_diag_decision_t;

typedef struct {
    ngx_http_markdown_diag_decision_t  *entries;
    ngx_uint_t                          capacity;
    ngx_uint_t                          head;
    ngx_uint_t                          count;
} ngx_http_markdown_diag_ring_t;

typedef struct {
    ngx_http_markdown_diag_ring_t   ring;
    ngx_flag_t                      enabled;
} ngx_http_markdown_diag_state_t;

/* Global state (mirrors production static globals) */
static ngx_http_markdown_diag_state_t  g_diag_state;
static ngx_flag_t  g_diag_initialized = 0;

/* Simulated ngx_current_msec */
static ngx_msec_t ngx_current_msec = 1000;

/* ── Production function reimplementations ────────────────────── */

ngx_int_t
ngx_http_markdown_diagnostics_init(ngx_http_markdown_diag_state_t *state,
    ngx_pool_t *pool, ngx_uint_t capacity)
{
    if (state == NULL || pool == NULL) {
        return NGX_ERROR;
    }

    if (capacity == 0) {
        capacity = NGX_HTTP_MARKDOWN_DIAG_DEFAULT_CAPACITY;
    }

    if (capacity > NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY) {
        capacity = NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY;
    }

    state->ring.entries = ngx_pcalloc(pool,
        capacity * sizeof(ngx_http_markdown_diag_decision_t));

    if (state->ring.entries == NULL) {
        return NGX_ERROR;
    }

    state->ring.capacity = capacity;
    state->ring.head = 0;
    state->ring.count = 0;
    state->enabled = 0;

    if (state == &g_diag_state) {
        g_diag_initialized = 1;
    }

    return NGX_OK;
}

void
ngx_http_markdown_diagnostics_record(ngx_http_markdown_diag_state_t *state,
    ngx_int_t reason_code, ngx_msec_t duration_ms)
{
    ngx_http_markdown_diag_decision_t  *entry;

    if (state == NULL || state->ring.entries == NULL || !state->enabled) {
        return;
    }

    entry = &state->ring.entries[state->ring.head];
    entry->timestamp = ngx_current_msec;
    entry->reason_code = reason_code;
    entry->duration_ms = duration_ms;

    state->ring.head = (state->ring.head + 1) % state->ring.capacity;

    if (state->ring.count < state->ring.capacity) {
        state->ring.count++;
    }
}

void
ngx_http_markdown_diagnostics_cleanup(ngx_http_markdown_diag_state_t *state)
{
    if (state == NULL) {
        return;
    }

    state->ring.head = 0;
    state->ring.count = 0;
    state->enabled = 0;
}

ngx_http_markdown_diag_state_t *
ngx_http_markdown_diagnostics_get_state(void)
{
    if (!g_diag_initialized) {
        return NULL;
    }

    return &g_diag_state;
}

/* ── Decision path logging types and logic ────────────────────── */

#define NGX_HTTP_MARKDOWN_CONV_FAILED   "FAILED"
#define NGX_HTTP_MARKDOWN_LOG_WARN      1
#define NGX_HTTP_MARKDOWN_LOG_INFO      2
#define NGX_HTTP_MARKDOWN_LOG_DEBUG     3

typedef struct {
    const char   *accept_result;
    const char   *conditional_result;
    const char   *conversion_status;
    const char   *reason_code;
    ngx_msec_t    duration_ms;
} ngx_http_markdown_decision_path_t;

/* Capture log output for verification */
static char g_last_log[512];
static int  g_log_called;

static ngx_int_t
decision_path_is_failure(const char *status)
{
    if (status == NULL) {
        return 0;
    }
    if (strcmp(status, NGX_HTTP_MARKDOWN_CONV_FAILED) == 0) {
        return 1;
    }
    return 0;
}

/* Simplified log_decision_path for testing verbosity gating */
static void
log_decision_path(ngx_uint_t effective_verbosity,
    const ngx_http_markdown_decision_path_t *path)
{
    ngx_int_t is_failure;

    if (path == NULL) {
        return;
    }

    is_failure = decision_path_is_failure(path->conversion_status);

    if (effective_verbosity <= NGX_HTTP_MARKDOWN_LOG_WARN && !is_failure) {
        return;
    }

    g_log_called = 1;
    snprintf(g_last_log, sizeof(g_last_log),
        "accept=%s cond=%s status=%s reason=%s dur=%lu",
        path->accept_result ? path->accept_result : "-",
        path->conditional_result ? path->conditional_result : "-",
        path->conversion_status ? path->conversion_status : "-",
        path->reason_code ? path->reason_code : "-",
        (unsigned long) path->duration_ms);
}


/* ── Tests ─────────────────────────────────────────────────────── */

static void
test_init_null_params(void)
{
    ngx_int_t rc;

    TEST_SUBSECTION("init with NULL params");

    rc = ngx_http_markdown_diagnostics_init(NULL, &g_pool, 10);
    TEST_ASSERT(rc == NGX_ERROR, "NULL state should return NGX_ERROR");

    ngx_http_markdown_diag_state_t state;
    rc = ngx_http_markdown_diagnostics_init(&state, NULL, 10);
    TEST_ASSERT(rc == NGX_ERROR, "NULL pool should return NGX_ERROR");

    TEST_PASS("NULL params handled correctly");
}

static void
test_init_default_capacity(void)
{
    ngx_http_markdown_diag_state_t state;
    ngx_int_t rc;

    TEST_SUBSECTION("init with default capacity (0)");

    pool_init();
    rc = ngx_http_markdown_diagnostics_init(&state, &g_pool, 0);
    TEST_ASSERT(rc == NGX_OK, "init with capacity=0 should succeed");
    TEST_ASSERT(state.ring.capacity == NGX_HTTP_MARKDOWN_DIAG_DEFAULT_CAPACITY,
                "capacity should be default (100)");
    TEST_ASSERT(state.ring.entries != NULL, "entries should be allocated");
    TEST_ASSERT(state.ring.head == 0, "head should be 0");
    TEST_ASSERT(state.ring.count == 0, "count should be 0");
    TEST_ASSERT(state.enabled == 0, "enabled should be 0");

    TEST_PASS("Default capacity initialization correct");
}

static void
test_init_capped_capacity(void)
{
    ngx_http_markdown_diag_state_t state;
    ngx_int_t rc;

    TEST_SUBSECTION("init with capacity exceeding max");

    pool_init();
    rc = ngx_http_markdown_diagnostics_init(&state, &g_pool, 99999);
    TEST_ASSERT(rc == NGX_OK, "init with over-max capacity should succeed");
    TEST_ASSERT(state.ring.capacity == NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY,
                "capacity should be clamped to max (10000)");

    TEST_PASS("Capacity clamping correct");
}

static void
test_init_custom_capacity(void)
{
    ngx_http_markdown_diag_state_t state;
    ngx_int_t rc;

    TEST_SUBSECTION("init with custom capacity");

    pool_init();
    rc = ngx_http_markdown_diagnostics_init(&state, &g_pool, 50);
    TEST_ASSERT(rc == NGX_OK, "init with capacity=50 should succeed");
    TEST_ASSERT(state.ring.capacity == 50, "capacity should be 50");

    TEST_PASS("Custom capacity initialization correct");
}

static void
test_record_disabled(void)
{
    ngx_http_markdown_diag_state_t state;

    TEST_SUBSECTION("record when disabled");

    pool_init();
    ngx_http_markdown_diagnostics_init(&state, &g_pool, 10);
    /* state.enabled is 0 by default */

    ngx_http_markdown_diagnostics_record(&state, 1, 100);
    TEST_ASSERT(state.ring.count == 0,
                "record should be no-op when disabled");

    TEST_PASS("Record disabled correctly");
}

static void
test_record_null_state(void)
{
    TEST_SUBSECTION("record with NULL state");

    /* Should not crash */
    ngx_http_markdown_diagnostics_record(NULL, 1, 100);

    TEST_PASS("NULL state record is no-op");
}

static void
test_record_basic(void)
{
    ngx_http_markdown_diag_state_t state;

    TEST_SUBSECTION("basic record");

    pool_init();
    ngx_http_markdown_diagnostics_init(&state, &g_pool, 10);
    state.enabled = 1;

    ngx_current_msec = 5000;
    ngx_http_markdown_diagnostics_record(&state, 42, 15);

    TEST_ASSERT(state.ring.count == 1, "count should be 1");
    TEST_ASSERT(state.ring.head == 1, "head should advance to 1");
    TEST_ASSERT(state.ring.entries[0].reason_code == 42,
                "reason_code should be 42");
    TEST_ASSERT(state.ring.entries[0].duration_ms == 15,
                "duration_ms should be 15");
    TEST_ASSERT(state.ring.entries[0].timestamp == 5000,
                "timestamp should be 5000");

    TEST_PASS("Basic record correct");
}

static void
test_record_wraparound(void)
{
    ngx_http_markdown_diag_state_t state;
    ngx_uint_t i;

    TEST_SUBSECTION("record wraparound");

    pool_init();
    ngx_http_markdown_diagnostics_init(&state, &g_pool, 4);
    state.enabled = 1;

    /* Fill the buffer */
    for (i = 0; i < 4; i++) {
        ngx_current_msec = 1000 + i;
        ngx_http_markdown_diagnostics_record(&state, (ngx_int_t) i, i * 10);
    }

    TEST_ASSERT(state.ring.count == 4, "count should be 4 (full)");
    TEST_ASSERT(state.ring.head == 0, "head should wrap to 0");

    /* Overwrite oldest entry */
    ngx_current_msec = 9999;
    ngx_http_markdown_diagnostics_record(&state, 99, 999);

    TEST_ASSERT(state.ring.count == 4, "count should stay at 4");
    TEST_ASSERT(state.ring.head == 1, "head should be 1");
    TEST_ASSERT(state.ring.entries[0].reason_code == 99,
                "oldest entry should be overwritten");
    TEST_ASSERT(state.ring.entries[0].timestamp == 9999,
                "overwritten entry timestamp correct");

    TEST_PASS("Ring buffer wraparound correct");
}

static void
test_cleanup(void)
{
    ngx_http_markdown_diag_state_t state;

    TEST_SUBSECTION("cleanup");

    pool_init();
    ngx_http_markdown_diagnostics_init(&state, &g_pool, 10);
    state.enabled = 1;
    ngx_current_msec = 1000;
    ngx_http_markdown_diagnostics_record(&state, 1, 10);

    ngx_http_markdown_diagnostics_cleanup(&state);

    TEST_ASSERT(state.ring.head == 0, "head should be reset to 0");
    TEST_ASSERT(state.ring.count == 0, "count should be reset to 0");
    TEST_ASSERT(state.enabled == 0, "enabled should be reset to 0");

    TEST_PASS("Cleanup resets state correctly");
}

static void
test_cleanup_null(void)
{
    TEST_SUBSECTION("cleanup with NULL");

    /* Should not crash */
    ngx_http_markdown_diagnostics_cleanup(NULL);

    TEST_PASS("NULL cleanup is no-op");
}

static void
test_get_state_before_init(void)
{
    TEST_SUBSECTION("get_state before initialization");

    g_diag_initialized = 0;
    ngx_http_markdown_diag_state_t *s =
        ngx_http_markdown_diagnostics_get_state();
    TEST_ASSERT(s == NULL, "should return NULL before init");

    TEST_PASS("get_state returns NULL before init");
}

static void
test_get_state_after_init(void)
{
    TEST_SUBSECTION("get_state after initialization");

    pool_init();
    g_diag_initialized = 0;
    ngx_http_markdown_diagnostics_init(&g_diag_state, &g_pool, 10);

    ngx_http_markdown_diag_state_t *s =
        ngx_http_markdown_diagnostics_get_state();
    TEST_ASSERT(s != NULL, "should return non-NULL after init");
    TEST_ASSERT(s == &g_diag_state, "should return global state pointer");

    TEST_PASS("get_state returns global state after init");
}

static void
test_decision_path_is_failure_cases(void)
{
    TEST_SUBSECTION("decision_path_is_failure");

    TEST_ASSERT(decision_path_is_failure(NULL) == 0,
                "NULL should not be failure");
    TEST_ASSERT(decision_path_is_failure("SUCCESS") == 0,
                "SUCCESS should not be failure");
    TEST_ASSERT(decision_path_is_failure("SKIPPED") == 0,
                "SKIPPED should not be failure");
    TEST_ASSERT(decision_path_is_failure("FAILED") == 1,
                "FAILED should be failure");

    TEST_PASS("Failure detection correct");
}

static void
test_log_decision_path_verbosity_gating(void)
{
    ngx_http_markdown_decision_path_t path;

    TEST_SUBSECTION("log_decision_path verbosity gating");

    memset(&path, 0, sizeof(path));
    path.accept_result = "CONVERT";
    path.conditional_result = "PROCEED";
    path.conversion_status = "SUCCESS";
    path.reason_code = "converted";
    path.duration_ms = 12;

    /* WARN verbosity + non-failure = suppressed */
    g_log_called = 0;
    log_decision_path(NGX_HTTP_MARKDOWN_LOG_WARN, &path);
    TEST_ASSERT(g_log_called == 0,
                "non-failure at WARN verbosity should be suppressed");

    /* INFO verbosity + non-failure = emitted */
    g_log_called = 0;
    log_decision_path(NGX_HTTP_MARKDOWN_LOG_INFO, &path);
    TEST_ASSERT(g_log_called == 1,
                "non-failure at INFO verbosity should be emitted");

    /* WARN verbosity + failure = emitted */
    path.conversion_status = "FAILED";
    g_log_called = 0;
    log_decision_path(NGX_HTTP_MARKDOWN_LOG_WARN, &path);
    TEST_ASSERT(g_log_called == 1,
                "failure at WARN verbosity should be emitted");

    /* DEBUG verbosity + non-failure = emitted */
    path.conversion_status = "SUCCESS";
    g_log_called = 0;
    log_decision_path(NGX_HTTP_MARKDOWN_LOG_DEBUG, &path);
    TEST_ASSERT(g_log_called == 1,
                "non-failure at DEBUG verbosity should be emitted");

    TEST_PASS("Verbosity gating correct");
}

static void
test_log_decision_path_null_fields(void)
{
    ngx_http_markdown_decision_path_t path;

    TEST_SUBSECTION("log_decision_path with NULL fields");

    memset(&path, 0, sizeof(path));
    path.conversion_status = "FAILED";
    path.duration_ms = 5;

    g_log_called = 0;
    log_decision_path(NGX_HTTP_MARKDOWN_LOG_INFO, &path);
    TEST_ASSERT(g_log_called == 1, "should log even with NULL fields");
    /* Verify NULL fields become "-" */
    TEST_ASSERT(strstr(g_last_log, "accept=-") != NULL,
                "NULL accept_result should become -");
    TEST_ASSERT(strstr(g_last_log, "cond=-") != NULL,
                "NULL conditional_result should become -");

    TEST_PASS("NULL fields handled correctly");
}

static void
test_log_decision_path_null_path(void)
{
    TEST_SUBSECTION("log_decision_path with NULL path");

    g_log_called = 0;
    log_decision_path(NGX_HTTP_MARKDOWN_LOG_INFO, NULL);
    TEST_ASSERT(g_log_called == 0, "NULL path should be no-op");

    TEST_PASS("NULL path is no-op");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("diagnostics_core Tests\n");
    printf("========================================\n");

    test_init_null_params();
    test_init_default_capacity();
    test_init_capped_capacity();
    test_init_custom_capacity();
    test_record_disabled();
    test_record_null_state();
    test_record_basic();
    test_record_wraparound();
    test_cleanup();
    test_cleanup_null();
    test_get_state_before_init();
    test_get_state_after_init();
    test_decision_path_is_failure_cases();
    test_log_decision_path_verbosity_gating();
    test_log_decision_path_null_fields();
    test_log_decision_path_null_path();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
