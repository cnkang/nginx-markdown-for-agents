/*
 * Test: inflight
 *
 * Validates the per-worker inflight counter (spec 52):
 *   - Counter starts at 0
 *   - Increment succeeds when below limit
 *   - Increment rejects when at limit (returns NGX_DECLINED)
 *   - Cleanup handler decrements correctly
 *   - Cleanup handler is idempotent (double-call safe)
 *   - High watermark updates correctly
 *   - Overload counter increments on rejection
 *   - Counter does not go negative (no underflow)
 *   - Sequential increment/decrement returns to zero
 */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef intptr_t        ngx_flag_t;
typedef volatile long   ngx_atomic_t;
typedef long            ngx_atomic_int_t;
typedef unsigned long   ngx_atomic_uint_t;

#define NGX_OK        0
#define NGX_ERROR    -1
#define NGX_DECLINED -5

#define NGX_LOG_DEBUG_HTTP  0x100
#define NGX_LOG_WARN        5
#define NGX_LOG_ERR         4

/* Atomic operations (single-threaded test — no real contention) */
static ngx_inline ngx_atomic_int_t
ngx_atomic_fetch_add(ngx_atomic_t *value, ngx_atomic_int_t add)
{
    ngx_atomic_int_t  old = *value;
    *value += add;
    return old;
}

static ngx_inline ngx_atomic_uint_t
ngx_atomic_cmp_set(ngx_atomic_uint_t *lock, ngx_atomic_uint_t old,
    ngx_atomic_uint_t set)
{
    if (*(volatile ngx_atomic_uint_t *) lock == old) {
        *lock = set;
        return 1;
    }
    return 0;
}

/* Minimal pool cleanup chain */
typedef struct ngx_pool_cleanup_s  ngx_pool_cleanup_t;
typedef void (*ngx_pool_cleanup_pt)(void *data);

struct ngx_pool_cleanup_s {
    ngx_pool_cleanup_pt   handler;
    void                 *data;
    ngx_pool_cleanup_t   *next;
};

/* Minimal pool */
typedef struct {
    ngx_pool_cleanup_t  *cleanup;
    /* Simulated cleanup data storage (we allocate inline) */
    unsigned char         cleanup_buf[256];
    size_t                cleanup_buf_used;
} ngx_pool_t;

/* Minimal connection and log */
typedef struct {
    int  dummy;
} ngx_log_t;

typedef struct {
    ngx_log_t  *log;
} ngx_connection_t;

/* Minimal request */
typedef struct {
    ngx_pool_t        *pool;
    ngx_connection_t  *connection;
} ngx_http_request_t;

/* Minimal conf struct — only needs max_inflight */
typedef struct {
    ngx_uint_t   max_inflight;
} ngx_http_markdown_conf_t;

/* Pool cleanup add stub */
static ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *p, size_t size)
{
    ngx_pool_cleanup_t  *cln;
    void                *data;
    size_t               needed;

    needed = sizeof(ngx_pool_cleanup_t) + size;
    if (p->cleanup_buf_used + needed > sizeof(p->cleanup_buf)) {
        return NULL;
    }

    cln = (ngx_pool_cleanup_t *) (p->cleanup_buf + p->cleanup_buf_used);
    p->cleanup_buf_used += sizeof(ngx_pool_cleanup_t);

    data = p->cleanup_buf + p->cleanup_buf_used;
    p->cleanup_buf_used += size;

    cln->handler = NULL;
    cln->data = data;
    cln->next = p->cleanup;
    p->cleanup = cln;

    return cln;
}

/* Simulate pool destroy (run all cleanup handlers) */
static void
test_pool_destroy(ngx_pool_t *p)
{
    ngx_pool_cleanup_t  *cln;

    for (cln = p->cleanup; cln != NULL; cln = cln->next) {
        if (cln->handler != NULL) {
            cln->handler(cln->data);
        }
    }
    p->cleanup = NULL;
}

/* Logging stubs — no-op for tests */
#define ngx_log_error(level, log, err, ...) (void)0
#define ngx_log_debug2(mask, log, err, ...) (void)0

/* Include the implementation under test */
#include "../../src/ngx_http_markdown_inflight_impl.h"

/* ----------------------------------------------------------------
 * Test helpers
 * ---------------------------------------------------------------- */

static ngx_log_t         g_log;
static ngx_connection_t  g_conn;
static ngx_pool_t        g_pool;

static void
setup_request(ngx_http_request_t *r)
{
    memset(&g_pool, 0, sizeof(g_pool));
    g_conn.log = &g_log;
    r->pool = &g_pool;
    r->connection = &g_conn;
}

/* ----------------------------------------------------------------
 * Tests
 * ---------------------------------------------------------------- */

static void
test_counter_starts_at_zero(void)
{
    ngx_http_markdown_inflight_reset();

    TEST_ASSERT(ngx_http_markdown_inflight_current() == 0,
        "counter should start at 0");
    TEST_ASSERT(ngx_http_markdown_inflight_high_watermark() == 0,
        "high_watermark should start at 0");
    TEST_ASSERT(ngx_http_markdown_inflight_overload_total() == 0,
        "overload_total should start at 0");

    TEST_PASS("counter starts at zero");
}

static void
test_increment_below_limit(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_conf_t conf;
    ngx_int_t                rc;

    ngx_http_markdown_inflight_reset();
    setup_request(&r);

    conf.max_inflight = 64;
    rc = ngx_http_markdown_inflight_try_increment(&r, &conf);

    TEST_ASSERT(rc == NGX_OK, "increment should succeed below limit");
    TEST_ASSERT(ngx_http_markdown_inflight_current() == 1,
        "current should be 1 after increment");
    TEST_ASSERT(ngx_http_markdown_inflight_high_watermark() == 1,
        "high_watermark should be 1");

    TEST_PASS("increment succeeds below limit");
}

static void
test_increment_rejects_at_limit(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_conf_t conf;
    ngx_int_t                rc;

    ngx_http_markdown_inflight_reset();
    setup_request(&r);

    conf.max_inflight = 2;

    /* Fill to limit */
    rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
    TEST_ASSERT(rc == NGX_OK, "first increment should succeed");

    /* Reset pool for second request */
    memset(&g_pool, 0, sizeof(g_pool));

    rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
    TEST_ASSERT(rc == NGX_OK, "second increment should succeed");

    /* Now at limit — next should be rejected */
    memset(&g_pool, 0, sizeof(g_pool));

    rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
    TEST_ASSERT(rc == NGX_DECLINED, "third increment should be declined");
    TEST_ASSERT(ngx_http_markdown_inflight_current() == 2,
        "current should remain at 2");
    TEST_ASSERT(ngx_http_markdown_inflight_overload_total() == 1,
        "overload_total should be 1");

    TEST_PASS("increment rejects at limit");
}

static void
test_cleanup_handler_decrements(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_conf_t conf;
    ngx_int_t                rc;

    ngx_http_markdown_inflight_reset();
    setup_request(&r);

    conf.max_inflight = 64;

    rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
    TEST_ASSERT(rc == NGX_OK, "increment should succeed");
    TEST_ASSERT(ngx_http_markdown_inflight_current() == 1,
        "current should be 1");

    /* Simulate pool destroy (triggers cleanup handler) */
    test_pool_destroy(&g_pool);

    TEST_ASSERT(ngx_http_markdown_inflight_current() == 0,
        "current should be 0 after cleanup");

    TEST_PASS("cleanup handler decrements correctly");
}

static void
test_cleanup_handler_idempotent(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_conf_t conf;
    ngx_int_t                rc;
    ngx_pool_cleanup_t      *cln;

    ngx_http_markdown_inflight_reset();
    setup_request(&r);

    conf.max_inflight = 64;

    rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
    TEST_ASSERT(rc == NGX_OK, "increment should succeed");

    /* Call cleanup handler multiple times manually */
    cln = g_pool.cleanup;
    TEST_ASSERT(cln != NULL, "cleanup should be registered");
    TEST_ASSERT(cln->handler != NULL, "cleanup handler should be set");

    cln->handler(cln->data);
    TEST_ASSERT(ngx_http_markdown_inflight_current() == 0,
        "current should be 0 after first call");

    /* Second call should be a no-op */
    cln->handler(cln->data);
    TEST_ASSERT(ngx_http_markdown_inflight_current() == 0,
        "current should remain 0 after second call (idempotent)");

    TEST_PASS("cleanup handler is idempotent");
}

static void
test_high_watermark_updates(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_conf_t conf;
    ngx_int_t                rc;
    int                      i;

    ngx_http_markdown_inflight_reset();
    conf.max_inflight = 10;

    /* Increment 5 times */
    for (i = 0; i < 5; i++) {
        setup_request(&r);
        rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
        TEST_ASSERT(rc == NGX_OK, "increment should succeed");
    }

    TEST_ASSERT(ngx_http_markdown_inflight_high_watermark() == 5,
        "high_watermark should be 5");

    /* Decrement 3 (simulate pool destroy without re-registering) */
    ngx_http_markdown_g_inflight.current = 2;

    /* Increment 2 more (total = 4, below previous watermark) */
    setup_request(&r);
    rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
    TEST_ASSERT(rc == NGX_OK, "increment should succeed");

    setup_request(&r);
    rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
    TEST_ASSERT(rc == NGX_OK, "increment should succeed");

    /* Watermark should still be 5 (not reduced) */
    TEST_ASSERT(ngx_http_markdown_inflight_high_watermark() == 5,
        "high_watermark should remain at 5 (never decreases)");

    /* Push past previous watermark */
    for (i = 0; i < 3; i++) {
        setup_request(&r);
        rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
        TEST_ASSERT(rc == NGX_OK, "increment should succeed");
    }

    TEST_ASSERT(ngx_http_markdown_inflight_high_watermark() == 7,
        "high_watermark should update to 7");

    TEST_PASS("high watermark updates correctly");
}

static void
test_overload_counter_increments(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_conf_t conf;
    ngx_int_t                rc;
    int                      i;

    ngx_http_markdown_inflight_reset();
    conf.max_inflight = 1;

    /* Fill to limit */
    setup_request(&r);
    rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
    TEST_ASSERT(rc == NGX_OK, "first increment should succeed");

    /* Multiple rejections */
    for (i = 0; i < 3; i++) {
        setup_request(&r);
        rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
        TEST_ASSERT(rc == NGX_DECLINED, "should be declined");
    }

    TEST_ASSERT(ngx_http_markdown_inflight_overload_total() == 3,
        "overload_total should be 3");

    TEST_PASS("overload counter increments on rejection");
}

static void
test_no_underflow(void)
{
    ngx_http_markdown_inflight_cleanup_t  cd;

    ngx_http_markdown_inflight_reset();

    /* Manually invoke cleanup when counter is already 0 */
    cd.counter = &ngx_http_markdown_g_inflight;
    cd.decremented = 0;

    ngx_http_markdown_inflight_cleanup_handler(&cd);

    /* Counter should remain at 0 (the > 0 guard prevents underflow) */
    TEST_ASSERT(ngx_http_markdown_inflight_current() == 0,
        "counter should not go negative");

    TEST_PASS("counter does not underflow");
}

static void
test_sequential_increment_decrement_returns_zero(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_conf_t conf;
    ngx_int_t                rc;
    int                      i;

    ngx_http_markdown_inflight_reset();
    conf.max_inflight = 64;

    /* Simulate N requests, each incrementing and then cleaning up */
    for (i = 0; i < 20; i++) {
        setup_request(&r);
        rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
        TEST_ASSERT(rc == NGX_OK, "increment should succeed");
        test_pool_destroy(&g_pool);
    }

    TEST_ASSERT(ngx_http_markdown_inflight_current() == 0,
        "counter should be 0 after all requests complete");

    TEST_PASS("sequential increment/decrement returns to zero");
}

static void
test_concurrent_inflight_all_cleanup(void)
{
    /*
     * Simulate multiple concurrent in-flight requests, then clean
     * them all up. Counter should return to zero.
     */
    ngx_http_request_t       r;
    ngx_http_markdown_conf_t conf;
    ngx_int_t                rc;
    ngx_pool_t               pools[8];
    ngx_connection_t         conn;
    ngx_log_t                log;
    int                      i;

    ngx_http_markdown_inflight_reset();
    conf.max_inflight = 10;

    conn.log = &log;
    r.connection = &conn;

    /* Increment 8 times (each with separate pool) */
    for (i = 0; i < 8; i++) {
        memset(&pools[i], 0, sizeof(ngx_pool_t));
        r.pool = &pools[i];
        rc = ngx_http_markdown_inflight_try_increment(&r, &conf);
        TEST_ASSERT(rc == NGX_OK, "increment should succeed");
    }

    TEST_ASSERT(ngx_http_markdown_inflight_current() == 8,
        "current should be 8");

    /* Destroy all pools (simulating all requests finishing) */
    for (i = 0; i < 8; i++) {
        test_pool_destroy(&pools[i]);
    }

    TEST_ASSERT(ngx_http_markdown_inflight_current() == 0,
        "current should be 0 after all cleanups");
    TEST_ASSERT(ngx_http_markdown_inflight_high_watermark() == 8,
        "high_watermark should be 8");

    TEST_PASS("concurrent inflight all cleanup returns to zero");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION("Per-worker Inflight Counter (spec 52)");

    test_counter_starts_at_zero();
    test_increment_below_limit();
    test_increment_rejects_at_limit();
    test_cleanup_handler_decrements();
    test_cleanup_handler_idempotent();
    test_high_watermark_updates();
    test_overload_counter_increments();
    test_no_underflow();
    test_sequential_increment_decrement_returns_zero();
    test_concurrent_inflight_all_cleanup();

    printf("\n");
    TEST_PASS("inflight: all tests passed");
    return 0;
}
