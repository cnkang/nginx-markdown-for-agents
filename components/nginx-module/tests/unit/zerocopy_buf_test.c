/*
 * Test: zerocopy_buf
 *
 * Validates the zero-copy buffer factory and pool cleanup handler
 * defined in ngx_http_markdown_zerocopy_buf.h.
 *
 * Tests cover:
 *   1. NULL pointer input to cleanup handler (no-op)
 *   2. Double-call prevention via freed flag
 *   3. Buffer factory success (valid ngx_buf_t with Rust memory)
 *   4. Buffer factory cleanup alloc failure (immediate free, NULL return)
 *
 * Feature: 0.9.1-performance-optimization
 * Validates: Requirements 2.1, 2.2, 2.4, 2.5, 2.6
 *
 * Rules: 15 (FFI cross-boundary), 46 (NULL/empty guards)
 */

/*
 * Block the real markdown_converter.h from being pulled in via
 * -I../../rust-converter/include.  We provide our own stub for
 * markdown_streaming_output_free below.
 */
#define NGINX_MARKDOWN_CONVERTER_H

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef intptr_t        ngx_flag_t;

typedef unsigned char   u_char;

/* ----------------------------------------------------------------
 * ngx_buf_t stub (matches production layout for pos/last/memory/temporary)
 * ---------------------------------------------------------------- */

typedef struct ngx_buf_s ngx_buf_t;

struct ngx_buf_s {
    u_char     *pos;
    u_char     *last;
    u_char     *start;
    u_char     *end;
    unsigned    temporary:1;
    unsigned    memory:1;
    unsigned    last_buf:1;
    unsigned    last_in_chain:1;
    unsigned    flush:1;
    unsigned    sync:1;
};

/* ----------------------------------------------------------------
 * Pool cleanup structure stub
 * ---------------------------------------------------------------- */

typedef struct ngx_pool_cleanup_s ngx_pool_cleanup_t;
typedef struct ngx_pool_s         ngx_pool_t;

struct ngx_pool_cleanup_s {
    void                    (*handler)(void *data);
    void                     *data;
    ngx_pool_cleanup_t       *next;
};

struct ngx_pool_s {
    ngx_pool_cleanup_t       *cleanups;
};

/* ----------------------------------------------------------------
 * Test control flags
 * ---------------------------------------------------------------- */

/*
 * g_cleanup_add_fail_once - When non-zero, the next call to
 * ngx_pool_cleanup_add returns NULL (simulating alloc failure)
 * and the flag is cleared.
 */
static ngx_uint_t g_cleanup_add_fail_once = 0;

/*
 * g_calloc_buf_fail_once - When non-zero, the next call to
 * ngx_calloc_buf returns NULL (simulating buf alloc failure)
 * and the flag is cleared.
 */
static ngx_uint_t g_calloc_buf_fail_once = 0;

/*
 * g_free_call_count - Tracks how many times markdown_streaming_output_free
 * was called.  Reset before each test.
 */
static unsigned int g_free_call_count = 0;

/*
 * g_free_last_ptr - Records the last pointer passed to
 * markdown_streaming_output_free.
 */
static u_char *g_free_last_ptr = NULL;

/*
 * g_free_last_len - Records the last length passed to
 * markdown_streaming_output_free.
 */
static size_t g_free_last_len = 0;

/* ----------------------------------------------------------------
 * Stub: markdown_streaming_output_free
 *
 * Mock that records calls for verification.  Production function
 * deallocates a Rust Box<[u8]>; we only track invocations.
 *
 * Signature matches the cbindgen-generated header:
 *   void markdown_streaming_output_free(uint8_t *data, uintptr_t len);
 * ---------------------------------------------------------------- */

void
markdown_streaming_output_free(uint8_t *data, uintptr_t len)
{
    g_free_call_count++;
    g_free_last_ptr = (u_char *) data;
    g_free_last_len = (size_t) len;
}

/* ----------------------------------------------------------------
 * Stub: ngx_pool_cleanup_add
 *
 * Allocates a cleanup node with data buffer via calloc.  Supports
 * g_cleanup_add_fail_once for failure injection.
 *
 * Production allocates from the pool; this stub uses calloc for
 * individually freeable test allocations.
 * ---------------------------------------------------------------- */

ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    ngx_pool_cleanup_t  *cln;

    if (pool == NULL) {
        return NULL;
    }
    if (g_cleanup_add_fail_once) {
        g_cleanup_add_fail_once = 0;
        return NULL;
    }

    cln = calloc(1, sizeof(ngx_pool_cleanup_t) + size);
    if (cln == NULL) {
        return NULL;
    }

    /* data points to the memory immediately after the cleanup struct */
    if (size > 0) {
        cln->data = (void *)((u_char *) cln + sizeof(ngx_pool_cleanup_t));
        memset(cln->data, 0, size);
    } else {
        cln->data = NULL;
    }

    cln->handler = NULL;
    cln->next = pool->cleanups;
    pool->cleanups = cln;
    return cln;
}

/* ----------------------------------------------------------------
 * Stub: ngx_calloc_buf
 *
 * Allocates a zero-initialised ngx_buf_t via calloc.  Supports
 * g_calloc_buf_fail_once for failure injection.
 * ---------------------------------------------------------------- */

ngx_buf_t *
ngx_calloc_buf(ngx_pool_t *pool)
{
    UNUSED(pool);
    if (g_calloc_buf_fail_once) {
        g_calloc_buf_fail_once = 0;
        return NULL;
    }
    return calloc(1, sizeof(ngx_buf_t));
}

/* ----------------------------------------------------------------
 * Helper: test_pool_run_cleanups
 *
 * Simulates pool destruction by invoking all registered cleanup
 * handlers in order (LIFO), then frees the cleanup entries.
 * ---------------------------------------------------------------- */

static void
test_pool_run_cleanups(ngx_pool_t *pool)
{
    ngx_pool_cleanup_t  *cln;
    ngx_pool_cleanup_t  *next;

    cln = pool->cleanups;
    while (cln != NULL) {
        next = cln->next;
        if (cln->handler != NULL) {
            cln->handler(cln->data);
        }
        free(cln);
        cln = next;
    }
    pool->cleanups = NULL;
}

/* ----------------------------------------------------------------
 * Enable MARKDOWN_STREAMING_ENABLED to include the header under test
 * ---------------------------------------------------------------- */

#define MARKDOWN_STREAMING_ENABLED

#include "../../src/ngx_http_markdown_zerocopy_buf.h"

/* ================================================================
 * Test 1: NULL pointer input to cleanup handler is no-op
 *
 * Validates: Requirement 2.1, 2.2
 * When rust_ptr is NULL, the cleanup handler returns immediately
 * without calling markdown_streaming_output_free.
 * ================================================================ */

static void
test_cleanup_handler_null_ptr(void)
{
    ngx_http_markdown_rust_buf_cleanup_t  ctx;

    g_free_call_count = 0;
    g_free_last_ptr = NULL;
    g_free_last_len = 0;

    memset(&ctx, 0, sizeof(ctx));
    ctx.rust_ptr = NULL;
    ctx.rust_len = 128;
    ctx.freed = 0;

    /* Invoke cleanup handler directly */
    ngx_http_markdown_rust_buf_cleanup(&ctx);

    TEST_ASSERT(g_free_call_count == 0,
        "NULL rust_ptr: free not called");
    TEST_ASSERT(ctx.freed == 0,
        "NULL rust_ptr: freed flag unchanged");

    TEST_PASS("cleanup handler with NULL rust_ptr is no-op");
}

/* ================================================================
 * Test 2: Double-call prevention via freed flag
 *
 * Validates: Requirement 2.4, 2.5
 * If freed=1, the cleanup handler does nothing (prevents double-free).
 * ================================================================ */

static void
test_cleanup_handler_double_free_prevention(void)
{
    ngx_http_markdown_rust_buf_cleanup_t  ctx;
    u_char                                dummy_data[64];

    g_free_call_count = 0;
    g_free_last_ptr = NULL;
    g_free_last_len = 0;

    memset(&ctx, 0, sizeof(ctx));
    ctx.rust_ptr = dummy_data;
    ctx.rust_len = sizeof(dummy_data);
    ctx.freed = 0;

    /* First call: should invoke free */
    ngx_http_markdown_rust_buf_cleanup(&ctx);

    TEST_ASSERT(g_free_call_count == 1,
        "first call: free invoked once");
    TEST_ASSERT(g_free_last_ptr == dummy_data,
        "first call: correct pointer passed to free");
    TEST_ASSERT(g_free_last_len == sizeof(dummy_data),
        "first call: correct length passed to free");
    TEST_ASSERT(ctx.freed == 1,
        "first call: freed flag set to 1");
    TEST_ASSERT(ctx.rust_ptr == NULL,
        "first call: rust_ptr set to NULL");

    /* Second call: should be no-op (double-free prevention) */
    ngx_http_markdown_rust_buf_cleanup(&ctx);

    TEST_ASSERT(g_free_call_count == 1,
        "second call: free NOT called again (double-free prevention)");

    TEST_PASS("cleanup handler prevents double-free via freed flag");
}

/* ================================================================
 * Test 3: Buffer factory success path
 *
 * Validates: Requirement 2.4, 2.5, 2.6
 * Buffer factory returns a valid ngx_buf_t with pos/last pointing
 * to the Rust memory, memory=1, temporary=0, and cleanup registered.
 * ================================================================ */

static void
test_buffer_factory_success(void)
{
    ngx_pool_t   pool;
    ngx_buf_t   *b;
    u_char       rust_data[256];
    size_t       rust_len;

    rust_len = sizeof(rust_data);

    g_free_call_count = 0;
    g_cleanup_add_fail_once = 0;
    g_calloc_buf_fail_once = 0;
    memset(&pool, 0, sizeof(pool));
    pool.cleanups = NULL;

    /* Fill with known pattern */
    memset(rust_data, 0xAB, rust_len);

    b = ngx_http_markdown_rust_buf_create(&pool, rust_data, rust_len);

    TEST_ASSERT(b != NULL,
        "buffer factory returns non-NULL buffer");
    TEST_ASSERT(b->pos == rust_data,
        "buf->pos points to Rust memory");
    TEST_ASSERT(b->last == rust_data + rust_len,
        "buf->last points to end of Rust memory");
    TEST_ASSERT(b->memory == 1,
        "buf->memory is set to 1");
    TEST_ASSERT(b->temporary == 0,
        "buf->temporary is set to 0");
    TEST_ASSERT(g_free_call_count == 0,
        "no free called during successful creation");

    /* Verify cleanup handler is registered */
    TEST_ASSERT(pool.cleanups != NULL,
        "cleanup registered on pool");
    TEST_ASSERT(pool.cleanups->handler != NULL,
        "cleanup handler function is set");

    /* Run pool cleanup (simulating pool destruction) */
    test_pool_run_cleanups(&pool);

    TEST_ASSERT(g_free_call_count == 1,
        "pool cleanup called free exactly once");
    TEST_ASSERT(g_free_last_ptr == rust_data,
        "pool cleanup passed correct pointer");
    TEST_ASSERT(g_free_last_len == rust_len,
        "pool cleanup passed correct length");

    /* Free the buffer (was allocated with calloc in the stub) */
    free(b);

    TEST_PASS("buffer factory success: valid buf with pool cleanup");
}

/* ================================================================
 * Test 4: Buffer factory cleanup alloc failure
 *
 * Validates: Requirement 2.6
 * When ngx_pool_cleanup_add fails, the buffer factory calls
 * markdown_streaming_output_free immediately and returns NULL.
 * ================================================================ */

static void
test_buffer_factory_cleanup_alloc_failure(void)
{
    ngx_pool_t   pool;
    ngx_buf_t   *b;
    u_char       rust_data[128];
    size_t       rust_len;

    rust_len = sizeof(rust_data);

    g_free_call_count = 0;
    g_free_last_ptr = NULL;
    g_free_last_len = 0;
    g_calloc_buf_fail_once = 0;
    memset(&pool, 0, sizeof(pool));
    pool.cleanups = NULL;

    /* Inject cleanup allocation failure */
    g_cleanup_add_fail_once = 1;

    b = ngx_http_markdown_rust_buf_create(&pool, rust_data, rust_len);

    TEST_ASSERT(b == NULL,
        "cleanup alloc failure: buffer factory returns NULL");
    TEST_ASSERT(g_free_call_count == 1,
        "cleanup alloc failure: free called immediately");
    TEST_ASSERT(g_free_last_ptr == rust_data,
        "cleanup alloc failure: correct pointer freed");
    TEST_ASSERT(g_free_last_len == rust_len,
        "cleanup alloc failure: correct length passed");

    /* No cleanups should be registered */
    TEST_ASSERT(pool.cleanups == NULL,
        "cleanup alloc failure: no cleanup on pool");

    TEST_PASS("buffer factory cleanup alloc failure: immediate free, NULL return");
}

/* ================================================================
 * Test 5: Pool cleanup on request pool destruction
 *
 * Validates: Requirement 2.4, 2.5
 * After a successful buffer factory call, destroying the pool
 * triggers the cleanup handler which calls
 * markdown_streaming_output_free.
 * ================================================================ */

static void
test_pool_cleanup_on_pool_destruction(void)
{
    ngx_pool_t   pool;
    ngx_buf_t   *b;
    u_char       rust_data[64];
    size_t       rust_len;

    rust_len = sizeof(rust_data);

    g_free_call_count = 0;
    g_free_last_ptr = NULL;
    g_free_last_len = 0;
    g_cleanup_add_fail_once = 0;
    g_calloc_buf_fail_once = 0;
    memset(&pool, 0, sizeof(pool));
    pool.cleanups = NULL;

    b = ngx_http_markdown_rust_buf_create(&pool, rust_data, rust_len);
    TEST_ASSERT(b != NULL,
        "buffer created successfully for pool cleanup test");

    /* No free should have been called yet */
    TEST_ASSERT(g_free_call_count == 0,
        "no free before pool destruction");

    /* Simulate pool destruction */
    test_pool_run_cleanups(&pool);

    TEST_ASSERT(g_free_call_count == 1,
        "pool destruction: free called exactly once");
    TEST_ASSERT(g_free_last_ptr == rust_data,
        "pool destruction: correct pointer passed");
    TEST_ASSERT(g_free_last_len == rust_len,
        "pool destruction: correct length passed");

    /* Free the buffer allocation */
    free(b);

    TEST_PASS("pool cleanup on destruction calls markdown_streaming_output_free");
}

/* ================================================================
 * Test 6: Buffer factory buf alloc failure (cleanup already registered)
 *
 * Validates: Requirement 2.5, 2.6
 * When ngx_calloc_buf fails AFTER cleanup is registered, the buffer
 * factory returns NULL.  The Rust memory will be freed by the
 * already-registered pool cleanup handler on pool destroy.
 * ================================================================ */

static void
test_buffer_factory_buf_alloc_failure(void)
{
    ngx_pool_t   pool;
    ngx_buf_t   *b;
    u_char       rust_data[128];
    size_t       rust_len;

    rust_len = sizeof(rust_data);

    g_free_call_count = 0;
    g_free_last_ptr = NULL;
    g_free_last_len = 0;
    g_cleanup_add_fail_once = 0;
    memset(&pool, 0, sizeof(pool));
    pool.cleanups = NULL;

    /* Inject buffer allocation failure */
    g_calloc_buf_fail_once = 1;

    b = ngx_http_markdown_rust_buf_create(&pool, rust_data, rust_len);

    TEST_ASSERT(b == NULL,
        "buf alloc failure: buffer factory returns NULL");
    TEST_ASSERT(g_free_call_count == 0,
        "buf alloc failure: free NOT called (cleanup registered)");

    /* Cleanup should still be registered on the pool */
    TEST_ASSERT(pool.cleanups != NULL,
        "buf alloc failure: cleanup is registered");
    TEST_ASSERT(pool.cleanups->handler != NULL,
        "buf alloc failure: cleanup handler is set");

    /* Pool destruction should free the Rust memory */
    test_pool_run_cleanups(&pool);

    TEST_ASSERT(g_free_call_count == 1,
        "buf alloc failure: pool cleanup frees Rust memory");
    TEST_ASSERT(g_free_last_ptr == rust_data,
        "buf alloc failure: correct pointer freed by cleanup");
    TEST_ASSERT(g_free_last_len == rust_len,
        "buf alloc failure: correct length freed by cleanup");

    TEST_PASS("buf alloc failure: cleanup registered, pool frees Rust memory");
}


int
main(void)
{
    TEST_SECTION("zerocopy_buf tests (pool cleanup + buffer factory)");

    test_cleanup_handler_null_ptr();
    test_cleanup_handler_double_free_prevention();
    test_buffer_factory_success();
    test_buffer_factory_cleanup_alloc_failure();
    test_pool_cleanup_on_pool_destruction();
    test_buffer_factory_buf_alloc_failure();

    printf("\nAll zerocopy_buf tests passed.\n");
    return 0;
}
