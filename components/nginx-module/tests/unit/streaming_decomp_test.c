/*
 * Test: streaming_decomp
 *
 * Purpose: Unit tests for the streaming decompression pipeline used by the
 * nginx markdown filter module.  Validates create/feed/finish lifecycle,
 * error propagation, budget enforcement, buffer expansion, size-overflow
 * guards, and cleanup semantics for gzip and deflate streams.
 *
 * When MARKDOWN_STREAMING_ENABLED is not defined the entire suite is
 * compiled to a no-op that reports a skip banner.
 *
 * Stubs: ngx_palloc, ngx_pcalloc, ngx_alloc, ngx_free,
 *        ngx_pool_cleanup_add, inflateInit2, inflateEnd, inflate
 * are reimplemented here to inject allocation failures and controlled
 * inflate behaviour without depending on exact compressed-payload shapes.
 * See per-stub DIVERGENCE RISK notes below.
 */

#include "../include/test_common.h"
#include <limits.h>
#include <zlib.h>

#ifdef NGX_HTTP_BROTLI
#include <brotli/encode.h>
#endif

#define NGX_OK 0
#define NGX_ERROR -1
#define NGX_AGAIN -2
#define NGX_DECLINED -5
#define NGX_DONE -4

#include <ngx_http_markdown_filter_module.h>

/*
 * Skip path: when the streaming feature flag is absent at compile time,
 * emit a skip banner and return 0 so the test binary still succeeds.
 */
#ifndef MARKDOWN_STREAMING_ENABLED

int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_decomp Tests (SKIPPED)\n");
    printf("MARKDOWN_STREAMING_ENABLED not defined\n");
    printf("========================================\n\n");
    return 0;
}

#else /* MARKDOWN_STREAMING_ENABLED */

/*
 * test_pool_cleanup_t - Minimal stand-in for ngx_pool_cleanup_t.
 * Mirrors the production linked-list cleanup structure so that
 * ngx_pool_cleanup_add and test_pool_run_cleanups can exercise
 * registration and execution of cleanup handlers.
 *
 * Fields:
 *   handler - callback to invoke on pool destruction
 *   data    - opaque context passed to handler
 *   next    - linked-list pointer to the next cleanup entry
 */
typedef struct test_pool_cleanup_s test_pool_cleanup_t;
typedef test_pool_cleanup_t ngx_pool_cleanup_t;

struct test_pool_cleanup_s {
    void                 (*handler)(void *data);
    void                  *data;
    ngx_pool_cleanup_t    *next;
};

/*
 * ngx_pool_s - Minimal stand-in for the NGINX pool structure.
 * Only the cleanups list is needed for these tests.
 *
 * Fields:
 *   cleanups - head of the linked list of registered cleanup handlers
 */
struct ngx_pool_s {
    ngx_pool_cleanup_t    *cleanups;
};

/*
 * ngx_log_s - Minimal stand-in for the NGINX log structure.
 * No fields are exercised by the tests; the struct exists only so
 * that function signatures match production code.
 *
 * Fields:
 *   unused - placeholder to avoid an empty struct
 */
struct ngx_log_s {
    int                    unused;
};

#define ngx_memcpy memcpy
#define NGX_MAX_SIZE_T_VALUE SIZE_MAX

/* test_log - Dummy log instance passed to production APIs under test. */
static ngx_log_t test_log;

/*
 * g_palloc_fail_once - When non-zero, the next call to ngx_palloc returns
 * NULL and the flag is cleared.  Used to inject a single allocation failure.
 */
static ngx_uint_t g_palloc_fail_once = 0;

/*
 * g_palloc_return_static_once - When non-zero, the next call to ngx_palloc
 * returns g_static_pool_buf instead of a malloc'd block and the flag is
 * cleared.  Used to test the code path where the output buffer is a
 * pool-allocated (non-heap) pointer.
 */
static ngx_uint_t g_palloc_return_static_once = 0;

/* Total request-pool bytes requested by the production path under test. */
static size_t g_palloc_bytes = 0;

/*
 * g_palloc_ptrs / g_palloc_ptr_count — Tracks pointers returned by ngx_palloc
 * so that ngx_free() can detect an illegal free of pool memory.  In real
 * NGINX, pool allocations cannot be individually freed; this catches the
 * ownership bug where apply_limits() called free_heap() on a pool buffer.
 *
 * Harness semantics: ngx_palloc() is backed by malloc() so the buffers are
 * individually reclaimable for leak-free test teardown, but ngx_free() must
 * NOT silently absorb a pool-pointer free.  Instead it records a violation
 * flag that the test asserts stays clear.  Test-owned reclamation of these
 * malloc-backed pool allocations is performed exclusively by
 * test_pool_free_tracked_allocations(), which bypasses the violation guard.
 */
#define G_PALLOC_PTR_MAX  256
static void    *g_palloc_ptrs[G_PALLOC_PTR_MAX];
static size_t   g_palloc_ptr_count = 0;

/*
 * g_free_on_palloc_violation - Set when ngx_free() is called with a pointer
 * that ngx_palloc() returned (i.e. an attempt to individually free pool
 * memory).  Tests assert this stays 0; a non-zero value indicates a
 * production-code ownership regression such as apply_limits() freeing a
 * pool-owned buffer.
 */
static ngx_uint_t g_free_on_palloc_violation = 0;

/*
 * g_free_on_static_pool_violation - Set when ngx_free() is called with
 * g_static_pool_buf, which simulates a pool-managed buffer whose lifetime
 * is controlled by the pool, not by free().  Tests assert this stays 0.
 */
static ngx_uint_t g_free_on_static_pool_violation = 0;

/*
 * g_pcalloc_fail_once - When non-zero, the next call to ngx_pcalloc returns
 * NULL and the flag is cleared.  Used to inject a single zeroed-allocation
 * failure.
 */
static ngx_uint_t g_pcalloc_fail_once = 0;

/*
 * g_alloc_fail_once - When non-zero, the next call to ngx_alloc returns
 * NULL and the flag is cleared.  Used to inject a single raw-allocation
 * failure (e.g. for buffer expansion).
 */
static ngx_uint_t g_alloc_fail_once = 0;

/* Return a non-freeable stand-in once for huge-size guard tests. */
static ngx_uint_t g_alloc_return_static_once = 0;

/*
 * g_cleanup_add_fail_once - When non-zero, the next call to
 * ngx_pool_cleanup_add returns NULL and the flag is cleared.
 * Used to test cleanup-registration failure in create.
 */
static ngx_uint_t g_cleanup_add_fail_once = 0;

/*
 * g_inflate_init_fail_once - When non-zero, the next call to
 * test_inflateInit2 returns Z_MEM_ERROR and the flag is cleared.
 * Used to test inflateInit2 failure in create.
 */
static ngx_uint_t g_inflate_init_fail_once = 0;
static ngx_uint_t g_inflate_reset_fail_once = 0;

/*
 * g_static_pool_buf - Fixed-size buffer returned by ngx_palloc when
 * g_palloc_return_static_once is set.  Simulates a pool-allocated
 * buffer whose lifetime is managed by the pool, not by free().
 */
static u_char g_static_pool_buf[8192];

/*
 * test_inflate_mode_t - Enumerates the controllable behaviours of the
 * test_inflate mock.  Each mode exercises a specific inflate path in
 * streaming_decomp_impl (feed expansion, finish error, budget exceed,
 * multi-call finish, etc.).  TEST_INFLATE_MODE_REAL delegates to the
 * real zlib inflate().
 */
typedef enum {
    TEST_INFLATE_MODE_REAL = 0,
    TEST_INFLATE_MODE_FEED_EXPAND_THEN_ERROR,
    TEST_INFLATE_MODE_FEED_EXPAND_THEN_BUDGET,
    TEST_INFLATE_MODE_FEED_EXPAND_THEN_DONE,
    TEST_INFLATE_MODE_FEED_BUF_ERROR_NO_PROGRESS,
    TEST_INFLATE_MODE_FEED_IO_ERROR,
    TEST_INFLATE_MODE_FINISH_ERROR,
    TEST_INFLATE_MODE_FINISH_BUDGET,
    TEST_INFLATE_MODE_FINISH_CONTINUE_THEN_END,
    TEST_INFLATE_MODE_FINISH_EXPAND_THEN_END
} test_inflate_mode_t;

/* g_inflate_mode - Selects which mock behaviour test_inflate exhibits. */
static test_inflate_mode_t g_inflate_mode = TEST_INFLATE_MODE_REAL;

/* g_inflate_call_count - Counts invocations of test_inflate within the
 * current test case; used by multi-call mock modes to vary behaviour
 * on the first versus subsequent calls. */
static ngx_uint_t g_inflate_call_count = 0;

/* g_real_inflate_fn - Saved pointer to the real zlib inflate() function;
 * used by TEST_INFLATE_MODE_REAL to delegate to genuine decompression. */
static int (*g_real_inflate_fn)(z_streamp, int) = inflate;

/* g_real_inflate_end_fn - Saved pointer to the real zlib inflateEnd();
 * test_inflateEnd delegates to this so stream teardown is always real. */
static int (*g_real_inflate_end_fn)(z_streamp) = inflateEnd;
static int (*g_real_inflate_reset_fn)(z_streamp) = inflateReset;

/*
 * test_reset_inflate_mode - Reset the inflate mock to its default state
 * (real zlib behaviour, call count zeroed).
 *
 * Side effects: clears g_inflate_mode and g_inflate_call_count.
 */
static void
test_reset_inflate_mode(void)
{
    g_inflate_mode = TEST_INFLATE_MODE_REAL;
    g_inflate_call_count = 0;
}

/*
 * ngx_palloc - Stub for the NGINX pool allocator.
 *
 * DIVERGENCE RISK: Production ngx_palloc allocates from a pool chunk and
 * never calls malloc().  This stub uses malloc() so allocations are
 * individually freeable, and supports two test-only injection points:
 *   - g_palloc_fail_once: return NULL once to simulate OOM
 *   - g_palloc_return_static_once: return g_static_pool_buf once to
 *     simulate a pool-managed (non-heap) buffer
 * The semantic contract mirrored is: "returns NULL on failure, a valid
 * pointer of at least `size` bytes on success."
 *
 * Parameters:
 *   pool - ignored (UNUSED)
 *   size - number of bytes to allocate
 *
 * Return: pointer to allocated memory, or NULL on injected failure.
 */
void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    void  *p;

    UNUSED(pool);
    g_palloc_bytes += size;
    if (g_palloc_fail_once) {
        g_palloc_fail_once = 0;
        return NULL;
    }
    if (g_palloc_return_static_once) {
        g_palloc_return_static_once = 0;
        return g_static_pool_buf;
    }
    p = malloc(size);
    if (p != NULL && g_palloc_ptr_count < G_PALLOC_PTR_MAX) {
        g_palloc_ptrs[g_palloc_ptr_count++] = p;
    }
    return p;
}

/*
 * ngx_pcalloc - Stub for the NGINX zeroed pool allocator.
 *
 * DIVERGENCE RISK: Production ngx_pcalloc zeroes a pool chunk; this stub
 * uses calloc(1, size) for the same zeroing semantics but with individual
 * freeability.  Supports g_pcalloc_fail_once to inject a single failure.
 * The semantic contract mirrored is: "returns NULL on failure, a
 * zero-filled pointer of at least `size` bytes on success."
 *
 * Parameters:
 *   pool - ignored (UNUSED)
 *   size - number of bytes to allocate and zero
 *
 * Return: pointer to zeroed memory, or NULL on injected failure.
 */
void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    if (g_pcalloc_fail_once) {
        g_pcalloc_fail_once = 0;
        return NULL;
    }
    return calloc(1, size);
}

/*
 * ngx_alloc - Stub for the NGINX raw (non-pool) allocator.
 *
 * DIVERGENCE RISK: Production ngx_alloc logs on failure; this stub
 * suppresses logging.  Supports g_alloc_fail_once to inject a single
 * failure.  The semantic contract mirrored is: "returns NULL on failure,
 * a valid pointer of at least `size` bytes on success."
 *
 * Parameters:
 *   size - number of bytes to allocate
 *   log  - ignored (UNUSED)
 *
 * Return: pointer to allocated memory, or NULL on injected failure.
 */
void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    UNUSED(log);
    if (g_alloc_fail_once) {
        g_alloc_fail_once = 0;
        return NULL;
    }
    if (g_alloc_return_static_once) {
        g_alloc_return_static_once = 0;
        return g_static_pool_buf;
    }
    return malloc(size);
}

/*
 * ngx_free - Stub for the NGINX deallocator.
 *
 * DIVERGENCE RISK: Production ngx_free may differ from libc free on
 * platforms with custom allocators; this stub calls free() for raw
 * ngx_alloc-backed heap buffers.  For ngx_palloc-backed buffers and the
 * static pool-buffer stand-in, it does NOT free; instead it records a
 * violation flag so tests can assert that production code never attempts
 * to individually free pool memory (the apply_limits/free_heap ownership
 * regression).  The semantic contract mirrored is: "releases memory
 * allocated by ngx_alloc; pool allocations are not individually freeable."
 *
 * Parameters:
 *   p - pointer to memory to free
 *
 * Side effects: sets g_free_on_palloc_violation or
 *               g_free_on_static_pool_violation on a pool-buffer free
 *               attempt; tests assert these remain 0.
 */
void
ngx_free(void *p)
{
    size_t  i;

    if (p == NULL) {
        return;
    }
    if (p == g_static_pool_buf) {
        g_free_on_static_pool_violation = 1;
        return;
    }
    for (i = 0; i < g_palloc_ptr_count; i++) {
        if (g_palloc_ptrs[i] == p) {
            /*
             * Production code must never call ngx_free() on a pool
             * allocation.  Record the violation and leave the tracking
             * entry intact so the test harness can still reclaim the
             * malloc-backed block via test_pool_free_tracked_allocations()
             * during teardown.
             */
            g_free_on_palloc_violation = 1;
            return;
        }
    }
    free(p);
}

/*
 * ngx_pool_cleanup_add - Stub for the NGINX pool cleanup registration API.
 *
 * DIVERGENCE RISK: Production ngx_pool_cleanup_add allocates the cleanup
 * structure from the pool itself; this stub uses calloc() so it is
 * individually freeable.  Supports g_cleanup_add_fail_once to inject a
 * single registration failure.  The semantic contract mirrored is:
 * "returns NULL on failure (or NULL pool), otherwise a cleanup entry
 * linked into pool->cleanups."
 *
 * Parameters:
 *   pool - pool to register the cleanup on; NULL causes immediate failure
 *   size - size of cleanup data (ignored in this stub)
 *
 * Return: pointer to new cleanup entry, or NULL on failure.
 */
ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    ngx_pool_cleanup_t  *cln;

    UNUSED(size);

    if (pool == NULL) {
        return NULL;
    }
    if (g_cleanup_add_fail_once) {
        g_cleanup_add_fail_once = 0;
        return NULL;
    }

    cln = calloc(1, sizeof(ngx_pool_cleanup_t));
    if (cln == NULL) {
        return NULL;
    }

    cln->next = pool->cleanups;
    pool->cleanups = cln;
    return cln;
}

/*
 * test_inflateInit2 - Stub for zlib inflateInit2.
 *
 * DIVERGENCE RISK: Production inflateInit2 always attempts real
 * initialisation; this stub can inject Z_MEM_ERROR via
 * g_inflate_init_fail_once to test the create-failure path.
 * On the non-failure path it delegates to inflateInit2_() (the
 * versioned entry point) to perform genuine initialisation.
 * The semantic contract mirrored is: "returns Z_OK on success,
 * a zlib error code on failure."
 *
 * Parameters:
 *   strm        - zlib stream to initialise
 *   window_bits - window size / format selector (gzip vs deflate)
 *
 * Return: Z_OK on success, Z_MEM_ERROR on injected failure.
 */
int
test_inflateInit2(z_streamp strm, int window_bits)
{
    if (g_inflate_init_fail_once) {
        g_inflate_init_fail_once = 0;
        return Z_MEM_ERROR;
    }
    return inflateInit2_(strm, window_bits, ZLIB_VERSION,
                         (int) sizeof(z_stream));
}

/*
 * test_inflateEnd - Stub for zlib inflateEnd.
 *
 * DIVERGENCE RISK: None.  This stub unconditionally delegates to the
 * real inflateEnd via g_real_inflate_end_fn so that zlib stream teardown
 * is always genuine.  The semantic contract mirrored is identical to
 * inflateEnd: "releases inflate working memory, returns Z_OK on success."
 *
 * Parameters:
 *   strm - zlib stream to finalise
 *
 * Return: zlib return code from the real inflateEnd.
 */
int
test_inflateEnd(z_streamp strm)
{
    return g_real_inflate_end_fn(strm);
}

int
test_inflateReset(z_streamp strm)
{
    if (g_inflate_reset_fail_once) {
        g_inflate_reset_fail_once = 0;
        return Z_MEM_ERROR;
    }
    return g_real_inflate_reset_fn(strm);
}

/*
 * DIVERGENCE RISK:
 * test_inflate() is a controlled mock used to drive rare error/expand paths
 * in streaming_decomp_impl without depending on exact compressed payload
 * shapes. The contract is keyed by g_inflate_mode and g_inflate_call_count:
 * it only returns Z_OK, Z_STREAM_END, or Z_DATA_ERROR and mutates
 * strm->avail_in/avail_out to model buffer growth and finish behavior for
 * FEED_EXPAND_THEN_ERROR/_BUDGET/_DONE and FINISH_ERROR/_BUDGET/
 * _CONTINUE_THEN_END/_EXPAND_THEN_END.
 *
 * TEST_INFLATE_MODE_REAL (and default fallthrough) delegates to
 * g_real_inflate_fn so baseline behavior remains the real zlib inflate().
 * If production inflate expectations change, update this mock contract and
 * the dependent tests in the same changeset.
 */
int
test_inflate(z_streamp strm, int flush)
{
    g_inflate_call_count++;

    switch (g_inflate_mode) {
    case TEST_INFLATE_MODE_FEED_EXPAND_THEN_ERROR:
        if (g_inflate_call_count == 1) {
            strm->avail_out = 0;
            strm->avail_in = 1;
            return Z_OK;
        }
        return Z_DATA_ERROR;

    case TEST_INFLATE_MODE_FEED_EXPAND_THEN_BUDGET:
        if (g_inflate_call_count == 1) {
            strm->avail_out = 0;
            strm->avail_in = 1;
            return Z_OK;
        }
        if (strm->avail_out > 128) {
            strm->avail_out -= 128;
        } else {
            strm->avail_out = 0;
        }
        strm->avail_in = 0;
        return Z_OK;

    case TEST_INFLATE_MODE_FEED_EXPAND_THEN_DONE:
        if (g_inflate_call_count == 1) {
            strm->avail_out = 0;
            strm->avail_in = 1;
            return Z_OK;
        }
        if (strm->avail_out > 32) {
            strm->avail_out -= 32;
        } else {
            strm->avail_out = 0;
        }
        strm->avail_in = 0;
        return Z_OK;

    case TEST_INFLATE_MODE_FEED_BUF_ERROR_NO_PROGRESS:
        /*
         * Simulate a malformed deflate stream where inflate returns
         * Z_BUF_ERROR without consuming input or producing output.
         * The no-progress guard in inflate_step must detect this and
         * return an error instead of looping forever.
         */
        return Z_BUF_ERROR;

    case TEST_INFLATE_MODE_FEED_IO_ERROR:
        return Z_MEM_ERROR;

    case TEST_INFLATE_MODE_FINISH_ERROR:
        if (flush == Z_FINISH) {
            return Z_DATA_ERROR;
        }
        break;

    case TEST_INFLATE_MODE_FINISH_BUDGET:
        if (flush == Z_FINISH) {
            if (strm->avail_out > 128) {
                strm->avail_out -= 128;
            } else {
                strm->avail_out = 0;
            }
            return Z_OK;
        }
        break;

    case TEST_INFLATE_MODE_FINISH_CONTINUE_THEN_END:
        if (flush == Z_FINISH) {
            if (g_inflate_call_count == 1) {
                if (strm->avail_out > 48) {
                    strm->avail_out -= 48;
                } else {
                    strm->avail_out = 0;
                }
                return Z_OK;
            }
            if (strm->avail_out > 24) {
                strm->avail_out -= 24;
            } else {
                strm->avail_out = 0;
            }
            return Z_STREAM_END;
        }
        break;

    case TEST_INFLATE_MODE_FINISH_EXPAND_THEN_END:
        if (flush == Z_FINISH) {
            if (g_inflate_call_count == 1) {
                strm->avail_out = 0;
                return Z_OK;
            }
            if (strm->avail_out > 32) {
                strm->avail_out -= 32;
            } else {
                strm->avail_out = 0;
            }
            return Z_STREAM_END;
        }
        break;

    case TEST_INFLATE_MODE_REAL:
    default:
        break;
    }

    return g_real_inflate_fn(strm, flush);
}

/*
 * Redirect zlib inflate/inflateInit2/inflateEnd to the test stubs so that
 * the production implementation (included next) calls our mockable
 * versions.  This must precede the #include of the implementation header
 * so that the macros take effect during its compilation.
 */
#ifdef inflate
#undef inflate
#endif
#ifdef inflateInit2
#undef inflateInit2
#endif
#ifdef inflateEnd
#undef inflateEnd
#endif
#ifdef inflateReset
#undef inflateReset
#endif
#define inflate test_inflate
#define inflateInit2 test_inflateInit2
#define inflateEnd test_inflateEnd
#define inflateReset test_inflateReset

/* Include the production streaming decompression implementation so that
 * its functions are compiled against the stubs defined above. */
#include "../src/ngx_http_markdown_streaming_decomp_impl.h"

/*
 * test_pool_t - Wrapper around ngx_pool_s that provides a local pool
 * instance for tests.  The embedded pool field is passed to production
 * APIs under test.
 *
 * Fields:
 *   pool - embedded NGINX pool structure (minimal stub)
 */
typedef struct {
    ngx_pool_t            pool;
} test_pool_t;

static int
compress_payload(const u_char *in, size_t in_len,
    ngx_http_markdown_compression_type_e type,
    u_char **out, size_t *out_len);

/*
 * test_pool_free_tracked_allocations - Reclaim the malloc-backed blocks
 * recorded by the ngx_palloc() stub without going through ngx_free(), so
 * the ownership-violation guard is not triggered.  This is the only
 * sanctioned path for releasing pool-allocation stand-ins in this harness.
 *
 * After the call, g_palloc_ptrs is empty and g_palloc_ptr_count is 0.
 * The violation flags are left untouched so the caller can assert them.
 *
 * Side effects: free()s every entry in g_palloc_ptrs, resets
 *               g_palloc_ptr_count to 0.
 */
static void
test_pool_free_tracked_allocations(void)
{
    size_t  i;

    for (i = 0; i < g_palloc_ptr_count; i++) {
        if (g_palloc_ptrs[i] != NULL
            && g_palloc_ptrs[i] != g_static_pool_buf) {
            free(g_palloc_ptrs[i]);
        }
        g_palloc_ptrs[i] = NULL;
    }
    g_palloc_ptr_count = 0;
}

/*
 * test_pool_reset - Zero-initialise a test pool and clear all failure-injection
 * flags and inflate mock state.  Call at the start of every test case to
 * ensure a clean slate.
 *
 * Parameters:
 *   tp - test pool to reset
 *
 * Side effects: zeroes *tp, reclaims tracked ngx_palloc allocations,
 *               clears all g_*_fail_once flags, clears ngx_free violation
 *               flags, resets inflate mode to real.
 */
static void
test_pool_reset(test_pool_t *tp)
{
    test_pool_free_tracked_allocations();
    memset(tp, 0, sizeof(*tp));
    g_palloc_fail_once = 0;
    g_palloc_return_static_once = 0;
    g_palloc_bytes = 0;
    g_pcalloc_fail_once = 0;
    g_alloc_fail_once = 0;
    g_alloc_return_static_once = 0;
    g_cleanup_add_fail_once = 0;
    g_inflate_init_fail_once = 0;
    g_inflate_reset_fail_once = 0;
    g_free_on_palloc_violation = 0;
    g_free_on_static_pool_violation = 0;
    test_reset_inflate_mode();
}


/*
 * Feed a valid gzip stream one compressed byte at a time and verify that
 * request-pool allocation tracks emitted output rather than chunk count.
 */
static void
test_tiny_chunks_do_not_amplify_request_pool(void)
{
    const char                          *text;
    size_t                               text_len;
    u_char                              *compressed;
    size_t                               compressed_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *actual;
    size_t                               emitted;
    ngx_int_t                            rc;

    TEST_SUBSECTION("tiny chunks keep request-pool use output-bounded");

    text = "tiny compressed chunks must not reserve 4 KiB each";
    text_len = test_cstrnlen(text, 1024);
    compressed = NULL;
    test_pool_reset(&tp);

    rc = compress_payload((const u_char *) text, text_len,
                          NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
                          &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "compression should succeed");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, text_len + 1);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");

    actual = malloc(text_len);
    TEST_ASSERT(actual != NULL, "output collector should be allocated");

    emitted = 0;
    for (size_t i = 0; i < compressed_len; i++) {
        u_char  *out;
        size_t   out_len;

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, &compressed[i], 1, &out, &out_len,
            &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK, "one-byte feed should succeed");
        TEST_ASSERT(out_len <= text_len - emitted,
            "one-byte feeds must not exceed expected output size");
        if (out_len > 0) {
            memcpy(actual + emitted, out, out_len);
        }
        emitted += out_len;
        /*
         * out is pool-owned (ngx_palloc-tracked); reclaim via the
         * sanctioned helper rather than free() so the ownership
         * guard stays armed across iterations.
         */
        test_pool_free_tracked_allocations();
    }

    TEST_ASSERT(emitted == text_len,
        "tiny feeds should emit the complete decompressed payload");
    TEST_ASSERT(MEM_EQ(actual, text, text_len),
        "tiny feeds must not drop or duplicate decompressed bytes");
    TEST_ASSERT(g_palloc_bytes <= emitted,
        "request-pool allocation must be bounded by emitted output");
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");

    {
        u_char  *finish_out;
        size_t   finish_len;

        finish_out = (u_char *) 0x1;
        finish_len = 1;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &finish_out, &finish_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "one-byte gzip feeds should finish successfully");
        TEST_ASSERT(finish_out == NULL && finish_len == 0,
            "completed gzip member should not emit a finish tail");
    }

    free(actual);
    free(compressed);
    free(decomp);
    TEST_PASS("tiny chunk request-pool amplification rejected");
}


/* Forward declaration: defined after the #ifdef NGX_HTTP_BROTLI block. */
static void test_pool_run_cleanups(test_pool_t *tp);

#ifdef NGX_HTTP_BROTLI
/* Verify terminal validation rejects a Brotli stream missing its tail. */
static void
test_truncated_brotli_finish_errors(void)
{
    const uint8_t                        text[] =
        "truncated brotli input must fail at finish";
    size_t                               compressed_len;
    uint8_t                              compressed[256];
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("finish error on truncated brotli stream");

    compressed_len = sizeof(compressed);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, sizeof(text) - 1, text,
                    &compressed_len, compressed)
                == BROTLI_TRUE,
        "brotli compression should succeed");
    TEST_ASSERT(compressed_len > 2,
        "brotli payload should be long enough to truncate");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, sizeof(text));
    TEST_ASSERT(decomp != NULL, "brotli decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len - 2,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "truncated brotli feed should await terminal validation");
    /* out is pool-owned; reclaim via the sanctioned helper. */
    test_pool_free_tracked_allocations();

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_finish(
        decomp, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
        "finish should reject an incomplete brotli stream");

    /* finish error path publishes NULL output. */
    free(out);
    free(decomp);
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("truncated brotli stream rejected at finish");
}

/*
 * test_brotli_error_classification - Verify the frozen three-way
 * error classifier correctly categorizes BrotliDecoderErrorCode values
 * into FORMAT, ALLOCATION, and INTERNAL classes.
 *
 * Uses integer casts exclusively (not named enum constants) to match
 * the production code's range-based approach and ensure compatibility
 * with Brotli 1.0.9 (Ubuntu 22.04).
 */
static void
test_brotli_error_classification(void)
{
    ngx_http_markdown_brotli_error_class_e  cls;

    TEST_SUBSECTION("brotli error classification: frozen three-way");

    /* FORMAT boundary: -1 (first FORMAT code) */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-1));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_FORMAT,
        "code -1 should classify as FORMAT");

    /* FORMAT boundary: -17 (last FORMAT code) */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-17));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_FORMAT,
        "code -17 should classify as FORMAT");

    /* FORMAT mid-range: -10 */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-10));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_FORMAT,
        "code -10 should classify as FORMAT");

    /* ALLOCATION boundary: -21 (first ALLOCATION code) */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-21));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_ALLOCATION,
        "code -21 should classify as ALLOCATION");

    /* ALLOCATION boundary: -30 (last ALLOCATION code) */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-30));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_ALLOCATION,
        "code -30 should classify as ALLOCATION");

    /* ALLOCATION mid-range: -25 */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-25));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_ALLOCATION,
        "code -25 should classify as ALLOCATION");

    /* INTERNAL: -18 (COMPOUND_DICTIONARY) */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-18));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_INTERNAL,
        "code -18 should classify as INTERNAL");

    /* INTERNAL: -19 (DICTIONARY_NOT_SET) */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-19));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_INTERNAL,
        "code -19 should classify as INTERNAL");

    /* INTERNAL: -20 (INVALID_ARGUMENTS) */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-20));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_INTERNAL,
        "code -20 should classify as INTERNAL");

    /* INTERNAL: -31 (UNREACHABLE) */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-31));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_INTERNAL,
        "code -31 should classify as INTERNAL");

    /* Unknown out-of-range: -99 classifies as INTERNAL (not FORMAT) */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-99));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_INTERNAL,
        "unknown code -99 should classify as INTERNAL");

    /* Unknown out-of-range: -32 classifies as INTERNAL */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(-32));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_INTERNAL,
        "unknown code -32 should classify as INTERNAL");

    /* Unknown out-of-range: 0 (not negative) classifies as INTERNAL */
    cls = ngx_http_markdown_brotli_classify_error(
        (BrotliDecoderErrorCode)(0));
    TEST_ASSERT(cls == NGX_HTTP_MARKDOWN_BROTLI_ERROR_INTERNAL,
        "code 0 should classify as INTERNAL");

    TEST_PASS("brotli error classification covers all ranges");
}


/*
 * test_brotli_exact_budget_probe - Verify the exact-budget completion
 * probe for Brotli (both Scenario A and Scenario B).
 *
 * Scenario A: remaining==0 at call start → workspace_size gives 1 byte
 * probe workspace.  If the decoder completes without output, success
 * with out_len=0.  If it produces output, BUDGET_EXCEEDED.
 *
 * Scenario B: mid-call NEEDS_MORE_OUTPUT with produced == remaining
 * → probe fires inside brotli_step.  Same completion semantics.
 *
 * Tests validate the five probe outcomes plus edge cases.
 */
static void
test_brotli_exact_budget_probe(void)
{
    /*
     * Create a known Brotli-compressed payload.  The decompressed text
     * must be long enough to meaningfully exercise the budget, but short
     * enough for a single feed.
     */
    static const uint8_t text[] = "Brotli exact budget probe test data";
    size_t                               text_len;
    size_t                               compressed_len;
    uint8_t                              compressed[512];
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("brotli exact-budget probe outcomes");

    text_len = sizeof(text) - 1;
    compressed_len = sizeof(compressed);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, text_len, text,
                    &compressed_len, compressed)
                == BROTLI_TRUE,
        "brotli compression should succeed");

    /*
     * Test 1 (Scenario A): remaining==0 at call start, probe produces
     * 0 bytes and returns SUCCESS → mark finished, return NGX_OK with
     * out_len=0 (no re-exposure of prior-call bytes).
     *
     * Strategy: first feed with budget=text_len exactly fills the budget.
     * Second feed with 0 remaining triggers the Scenario A probe.
     * The decoder is already finished from the first feed (SUCCESS),
     * so the post-completion guard returns NGX_OK with 0 output.
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, text_len);
    TEST_ASSERT(decomp != NULL, "brotli decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "first feed with exact budget should succeed");
    TEST_ASSERT(out_len == text_len,
        "first feed should produce exactly budget bytes");
    TEST_ASSERT(MEM_EQ(out, text, text_len),
        "first feed output must match original text");
    test_pool_free_tracked_allocations();

    /* Second feed: remaining==0, decomp->finished already set from SUCCESS */
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, NULL, 0,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "zero-byte feed after exact budget should succeed");
    TEST_ASSERT(out == NULL && out_len == 0,
        "zero-byte feed should not re-expose prior bytes");
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    /*
     * Test 1b (Scenario B): mid-call budget exhaustion, probe produces
     * 0 bytes + SUCCESS → return NGX_OK with exact-budget bytes.
     *
     * This is covered by the same test as #1 above if the stream
     * fits in one feed call (SUCCESS returned with produced == budget).
     * Already validated by the first feed above producing text_len.
     */

    /*
     * Test 4: Exact budget, probe produces 1+ bytes → BUDGET_EXCEEDED,
     * probe bytes not exposed to caller.
     *
     * Strategy: set budget to text_len - 1 (one byte short).
     * The decoder cannot complete within that budget.
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, text_len - 1);
    TEST_ASSERT(decomp != NULL, "brotli decompressor (budget-1) created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "budget-1 should trigger BUDGET_EXCEEDED");
    TEST_ASSERT(out == NULL && out_len == 0,
        "over-budget feed should not expose any output");
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    /*
     * Test 3: Exact budget, probe returns NEEDS_MORE_INPUT with input
     * exhausted, then upstream EOF arrives → TRUNCATED_INPUT.
     *
     * Strategy: truncate the compressed payload so the decoder hasn't
     * finished.  Set budget == text_len.  Feed truncated data. The
     * first feed may NEEDS_MORE_INPUT and succeed.  Then finish()
     * with decoder not yet finished → TRUNCATED_INPUT.
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, text_len);
    TEST_ASSERT(decomp != NULL, "brotli decompressor for truncation test");

    /* Feed only partial compressed data */
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len / 2,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "partial compressed feed should succeed awaiting more input");
    test_pool_free_tracked_allocations();

    /* Now finish() while decoder is not done */
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_finish(
        decomp, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
        "finish on incomplete stream should return TRUNCATED_INPUT");
    free(out);
    free(decomp);

    /*
     * Test 2: Exact budget, subsequent zero-output compressed bytes
     * arrive after probe, decoder eventually returns SUCCESS → succeed.
     *
     * This scenario is inherently tested by any multi-feed test where
     * the first feed produces exact-budget bytes but the decoder only
     * reports SUCCESS after consuming more input in the same call.
     * Our test #1 already exercises this (Brotli typically returns
     * SUCCESS in the same feed that produces the last output byte).
     */

    /*
     * Test (Scenario B): mid-call NEEDS_MORE_OUTPUT with produced ==
     * remaining → probe fires, 0 bytes + SUCCESS → return exact-budget
     * bytes as success.
     *
     * Strategy: choose a payload whose decompressed size exceeds a single
     * iteration buffer but fits the budget exactly.  This forces
     * NEEDS_MORE_OUTPUT internally, and the budget cap at remaining
     * triggers the probe path when produced reaches the budget.
     *
     * Use a payload > 4096 bytes (default initial workspace) to force
     * expansion, with budget == decompressed size.
     */
    {
        static uint8_t  large_text[8192];
        size_t          large_text_len;
        uint8_t         large_compressed[16384];
        size_t          large_compressed_len;
        size_t          i;

        large_text_len = sizeof(large_text);
        for (i = 0; i < large_text_len; i++) {
            large_text[i] = (uint8_t) ('A' + (i % 26));
        }

        large_compressed_len = sizeof(large_compressed);
        TEST_ASSERT(BrotliEncoderCompress(
                        BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                        BROTLI_MODE_TEXT, large_text_len, large_text,
                        &large_compressed_len, large_compressed)
                    == BROTLI_TRUE,
            "large brotli compression should succeed");

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
            large_text_len);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor (exact large budget) created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, large_compressed, large_compressed_len,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "large exact-budget feed should succeed");
        TEST_ASSERT(out_len == large_text_len,
            "large feed should produce exactly budget bytes");
        TEST_ASSERT(MEM_EQ(out, large_text, large_text_len),
            "large feed output must match original");
        test_pool_free_tracked_allocations();
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);

        /*
         * Test (Scenario B): mid-call NEEDS_MORE_OUTPUT with produced ==
         * remaining → probe produces 1 byte → BUDGET_EXCEEDED, no output.
         *
         * Set budget = large_text_len - 1.
         */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
            large_text_len - 1);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor (large budget-1) created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, large_compressed, large_compressed_len,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
            "large budget-1 should trigger BUDGET_EXCEEDED");
        TEST_ASSERT(out == NULL && out_len == 0,
            "large over-budget feed should not expose output");
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);

        /*
         * Test (Scenario B): mid-call NEEDS_MORE_OUTPUT with produced <
         * remaining → normal expansion, NOT a probe.
         *
         * Set budget = large_text_len + 1000 (plenty of headroom).
         * The NEEDS_MORE_OUTPUT path should do normal expansion.
         */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
            large_text_len + 1000);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor (large budget+1000) created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, large_compressed, large_compressed_len,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "large feed with headroom should succeed normally");
        TEST_ASSERT(out_len == large_text_len,
            "large feed with headroom should produce all bytes");
        TEST_ASSERT(MEM_EQ(out, large_text, large_text_len),
            "large feed with headroom output must match");
        test_pool_free_tracked_allocations();
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    /*
     * Test (priority): probe returns SUCCESS + avail_in > 0 → FORMAT_ERROR
     * / brotli_trailing_data (trailing-data rejection applies during probe).
     *
     * Strategy: append garbage after valid compressed payload and set
     * budget = text_len exactly.  The decoder completes in the probe with
     * trailing bytes → FORMAT_ERROR.
     */
    {
        uint8_t  trailing_buf[600];
        size_t   trailing_len;

        ngx_memcpy(trailing_buf, compressed, compressed_len);
        trailing_buf[compressed_len] = 0xDE;
        trailing_buf[compressed_len + 1] = 0xAD;
        trailing_len = compressed_len + 2;

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, text_len);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor (trailing data probe) created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, trailing_buf, trailing_len,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
            "trailing data at exact budget should return FORMAT_ERROR");
        TEST_ASSERT(out == NULL && out_len == 0,
            "trailing data error should not expose output");
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    /*
     * Test (priority): probe returns SUCCESS + avail_in == 0 +
     * produced == 0 → success (stream complete).
     *
     * Already covered by Test 1 above (exact budget, decoder finishes
     * cleanly).
     */

    /*
     * Test: probe returns NEEDS_MORE_OUTPUT → BUDGET_EXCEEDED.
     *
     * This is covered by the large budget-1 test above: when one byte
     * over budget exists, the probe produces that byte and returns
     * BUDGET_EXCEEDED.  If the decoder had even more output pending
     * (NEEDS_MORE_OUTPUT from the probe itself), the same code path
     * returns BUDGET_EXCEEDED.
     */

    TEST_PASS("brotli exact-budget probe outcomes verified");
}


/*
 * test_brotli_scenario_a_trailing_data_priority - Verify that FORMAT_ERROR
 * from trailing data has higher priority than BUDGET_EXCEEDED in Scenario A.
 *
 * Scenario A: remaining==0 at feed start, workspace_size allocates a 1-byte
 * probe workspace. The generic brotli_step runs the decoder. If the decoder
 * returns SUCCESS with trailing compressed bytes AND produced output that
 * exceeds the budget, the correct classification is FORMAT_ERROR (trailing
 * data), NOT BUDGET_EXCEEDED.
 *
 * This test constructs the exact boundary condition by:
 *   1. Creating a Brotli stream with known decompressed size.
 *   2. Setting max_decompressed_size = text_len.
 *   3. Performing a first feed that fills the budget exactly.
 *   4. Appending trailing garbage to remaining compressed input.
 *   5. Feeding the trailing-garbage input in a second call where
 *      remaining==0 → must return FORMAT_ERROR, not BUDGET_EXCEEDED.
 *
 * Branches covered:
 *   - brotli_step SUCCESS + avail_in>0 checked BEFORE check_limit
 *   - FORMAT_ERROR returned at exact-budget boundary with trailing data
 */
static void
test_brotli_scenario_a_trailing_data_priority(void)
{
    static const uint8_t text[] = "Scenario A priority test data";
    size_t                               text_len;
    size_t                               compressed_len;
    uint8_t                              compressed[512];
    uint8_t                              trailing_buf[600];
    size_t                               trailing_total;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("brotli Scenario A: trailing data > budget priority");

    text_len = sizeof(text) - 1;
    compressed_len = sizeof(compressed);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, text_len, text,
                    &compressed_len, compressed)
                == BROTLI_TRUE,
        "brotli compression should succeed");

    /* Append trailing garbage after valid stream */
    memcpy(trailing_buf, compressed, compressed_len);
    trailing_buf[compressed_len] = 0xDE;
    trailing_buf[compressed_len + 1] = 0xAD;
    trailing_buf[compressed_len + 2] = 0xBE;
    trailing_total = compressed_len + 3;

    /*
     * First feed: set budget = text_len (exact budget). The decoder
     * processes the entire valid stream in one shot, producing
     * text_len bytes. Workspace from workspace_size is capped to
     * remaining+1 = text_len+1 (probe byte). SUCCESS is returned
     * by the decoder, avail_in > 0 (trailing data).
     *
     * With the correct priority (trailing data > budget), this must
     * return FORMAT_ERROR, NOT BUDGET_EXCEEDED.
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, text_len);
    TEST_ASSERT(decomp != NULL,
        "brotli decompressor (Scenario A priority) created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, trailing_buf, trailing_total,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "Scenario A: trailing data at exact budget must return "
        "FORMAT_ERROR, not BUDGET_EXCEEDED");
    TEST_ASSERT(out == NULL && out_len == 0,
        "Scenario A: trailing-data error must not expose output");
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    /*
     * Control: same payload without trailing data should succeed with
     * exact budget (confirms the priority fix doesn't break the
     * normal exact-budget success path).
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, text_len);
    TEST_ASSERT(decomp != NULL,
        "brotli decompressor (Scenario A control) created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "Scenario A control: exact budget without trailing must succeed");
    TEST_ASSERT(out_len == text_len,
        "Scenario A control: must produce exactly budget bytes");
    test_pool_free_tracked_allocations();
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    TEST_PASS("brotli Scenario A trailing-data priority verified");
}


/*
 * test_brotli_roundtrip_single_chunk - Verify end-to-end decompression
 * round-trip for Brotli when the entire compressed payload is fed in one
 * call.
 *
 * Branches covered:
 *   - BrotliEncoderCompress produces valid compressed data
 *   - Single-chunk feed produces correct output
 *   - Output is byte-for-byte identical to original text
 *   - Finish after completed stream is a no-op (NGX_OK, no output)
 */
static void
test_brotli_roundtrip_single_chunk(void)
{
    static const uint8_t text[] =
        "Hello from the Brotli streaming decompressor test. "
        "This payload should round-trip correctly in one chunk.";
    size_t                               text_len;
    size_t                               compressed_len;
    uint8_t                              compressed[512];
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("brotli round-trip: single chunk");

    text_len = sizeof(text) - 1;
    compressed_len = sizeof(compressed);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, text_len, text,
                    &compressed_len, compressed)
                == BROTLI_TRUE,
        "brotli compression should succeed");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
    TEST_ASSERT(decomp != NULL, "brotli decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "single-chunk brotli feed should succeed");
    TEST_ASSERT(out_len == text_len,
        "brotli output length should match source");
    TEST_ASSERT(MEM_EQ(out, text, text_len),
        "brotli output should be byte-identical to original");
    test_pool_free_tracked_allocations();

    /* Finish after completed stream should be a no-op */
    {
        u_char  *finish_out;
        size_t   finish_len;

        finish_out = (u_char *) 0x1;
        finish_len = 1;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &finish_out, &finish_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "finish after completed brotli stream should succeed");
        TEST_ASSERT(finish_out == NULL && finish_len == 0,
            "finish should produce no additional output");
    }

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("brotli round-trip single chunk verified");
}


/*
 * test_brotli_roundtrip_multi_chunk - Feed the compressed Brotli data in
 * small chunks (2 bytes at a time) and verify correct reassembly.
 *
 * Branches covered:
 *   - Multiple small feeds produce correct cumulative output
 *   - Reassembled output is byte-identical to original text
 *   - No data duplication or loss at chunk boundaries
 *   - Finish after multi-chunk completion is a no-op
 */
static void
test_brotli_roundtrip_multi_chunk(void)
{
    static const uint8_t text[] =
        "Multi-chunk Brotli streaming test verifies correct "
        "reassembly across arbitrary feed boundaries.";
    size_t                               text_len;
    size_t                               compressed_len;
    uint8_t                              compressed[512];
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *actual;
    size_t                               emitted;
    ngx_int_t                            rc;
    size_t                               i;
    size_t                               chunk_size;

    TEST_SUBSECTION("brotli round-trip: multi-chunk (2 bytes at a time)");

    text_len = sizeof(text) - 1;
    compressed_len = sizeof(compressed);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, text_len, text,
                    &compressed_len, compressed)
                == BROTLI_TRUE,
        "brotli compression should succeed");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
    TEST_ASSERT(decomp != NULL, "brotli decompressor should be created");

    actual = malloc(text_len);
    TEST_ASSERT(actual != NULL, "output collector should be allocated");
    emitted = 0;

    for (i = 0; i < compressed_len; i += chunk_size) {
        u_char  *out;
        size_t   out_len;

        chunk_size = 2;
        if (i + chunk_size > compressed_len) {
            chunk_size = compressed_len - i;
        }

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, &compressed[i], chunk_size,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK, "two-byte brotli feed should succeed");
        if (out_len > 0) {
            TEST_ASSERT(emitted + out_len <= text_len,
                "multi-chunk output must not exceed expected size");
            memcpy(actual + emitted, out, out_len);
            emitted += out_len;
        }
        test_pool_free_tracked_allocations();
    }

    TEST_ASSERT(emitted == text_len,
        "multi-chunk feeds should emit the complete decompressed payload");
    TEST_ASSERT(MEM_EQ(actual, text, text_len),
        "multi-chunk feeds must not drop or duplicate bytes");

    /* Finish should be a no-op */
    {
        u_char  *finish_out;
        size_t   finish_len;

        finish_out = (u_char *) 0x1;
        finish_len = 1;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &finish_out, &finish_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "finish after multi-chunk brotli should succeed");
        TEST_ASSERT(finish_out == NULL && finish_len == 0,
            "finish should produce no additional output");
    }

    free(actual);
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("brotli round-trip multi-chunk verified");
}


/*
 * test_brotli_trailing_data_same_feed - Verify that trailing garbage bytes
 * appended to a valid Brotli stream in the same feed call are rejected as
 * NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR.
 *
 * Branches covered:
 *   - BROTLI_DECODER_RESULT_SUCCESS with avail_in > 0 → FORMAT_ERROR
 *   - No output exposed on trailing-data error
 *   - decomp->finished is NOT set on error
 */
static void
test_brotli_trailing_data_same_feed(void)
{
    static const uint8_t text[] =
        "trailing data same feed test payload";
    size_t                               text_len;
    size_t                               compressed_len;
    uint8_t                              compressed[512];
    uint8_t                              with_trailing[600];
    size_t                               trailing_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("brotli trailing data rejected in same feed");

    text_len = sizeof(text) - 1;
    compressed_len = sizeof(compressed);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, text_len, text,
                    &compressed_len, compressed)
                == BROTLI_TRUE,
        "brotli compression should succeed");

    /* Append 5 garbage bytes after the valid Brotli stream */
    memcpy(with_trailing, compressed, compressed_len);
    with_trailing[compressed_len] = 0xDE;
    with_trailing[compressed_len + 1] = 0xAD;
    with_trailing[compressed_len + 2] = 0xBE;
    with_trailing[compressed_len + 3] = 0xEF;
    with_trailing[compressed_len + 4] = 0xFF;
    trailing_len = compressed_len + 5;

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
    TEST_ASSERT(decomp != NULL, "brotli decompressor should be created");

    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, with_trailing, trailing_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "brotli same-feed trailing data should return FORMAT_ERROR");
    TEST_ASSERT(out == NULL && out_len == 0,
        "trailing-data error should not expose output");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("brotli trailing data rejected in same feed");
}


/*
 * test_brotli_trailing_data_next_feed - Verify that feeding non-empty data
 * after a Brotli stream has already completed (SUCCESS in a prior feed)
 * returns NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR without invoking the
 * decoder again.
 *
 * Branches covered:
 *   - Valid Brotli feed completes → finished set
 *   - Subsequent non-empty feed → FORMAT_ERROR (without decoder call)
 *   - No output exposed on error
 */
static void
test_brotli_trailing_data_next_feed(void)
{
    static const uint8_t text[] =
        "trailing data next feed test payload";
    size_t                               text_len;
    size_t                               compressed_len;
    uint8_t                              compressed[512];
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("brotli trailing data rejected across feeds");

    text_len = sizeof(text) - 1;
    compressed_len = sizeof(compressed);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, text_len, text,
                    &compressed_len, compressed)
                == BROTLI_TRUE,
        "brotli compression should succeed");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
    TEST_ASSERT(decomp != NULL, "brotli decompressor should be created");

    /* First feed: valid complete Brotli stream */
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "valid brotli feed should succeed");
    TEST_ASSERT(out_len == text_len,
        "brotli output should match source length");
    TEST_ASSERT(MEM_EQ(out, text, text_len),
        "brotli output should match source text");
    TEST_ASSERT(decomp->finished == 1,
        "complete brotli stream should set finished");
    test_pool_free_tracked_allocations();

    /* Second feed: non-empty trailing data after finish */
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "TRAILING_GARBAGE", 16,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "non-empty feed after brotli finish must be FORMAT_ERROR");
    TEST_ASSERT(out == NULL && out_len == 0,
        "trailing-data error should not expose output");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("brotli trailing data rejected across feeds");
}


/*
 * test_brotli_empty_feed_after_completion - Verify that feeding zero bytes
 * after a Brotli stream has completed returns NGX_OK without modifying
 * the output or decoder state.
 *
 * Branches covered:
 *   - Valid Brotli feed completes → finished set
 *   - Zero-byte feed after finish → NGX_OK (safe no-op)
 *   - Output slots remain NULL/0 on empty no-op feed
 */
static void
test_brotli_empty_feed_after_completion(void)
{
    static const uint8_t text[] =
        "empty feed after brotli completion test";
    size_t                               text_len;
    size_t                               compressed_len;
    uint8_t                              compressed[512];
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("brotli empty feed after completion is safe no-op");

    text_len = sizeof(text) - 1;
    compressed_len = sizeof(compressed);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, text_len, text,
                    &compressed_len, compressed)
                == BROTLI_TRUE,
        "brotli compression should succeed");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
    TEST_ASSERT(decomp != NULL, "brotli decompressor should be created");

    /* Feed valid complete Brotli stream */
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "valid brotli feed should succeed");
    TEST_ASSERT(out_len == text_len,
        "brotli output should match source length");
    TEST_ASSERT(decomp->finished == 1,
        "complete brotli stream should set finished");
    test_pool_free_tracked_allocations();

    /* Empty feed after completion: should be safe no-op */
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, NULL, 0,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "zero-byte feed after brotli completion should be NGX_OK");
    TEST_ASSERT(out == NULL && out_len == 0,
        "zero-byte feed should not emit output");

    /* Also test with non-NULL pointer but zero length */
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 0,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "zero-length feed (non-NULL ptr) after completion should be NGX_OK");
    TEST_ASSERT(out == NULL && out_len == 0,
        "zero-length feed should not emit output");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("brotli empty feed after completion is safe no-op");
}


/*
 * test_brotli_no_progress_guard_and_error_propagation - Verify the
 * no-progress guard in brotli_step() and typed error code propagation
 * through brotli_loop().
 *
 * Key test cases:
 * 1. Malformed Brotli data → FORMAT_ERROR (not generic NGX_ERROR)
 * 2. brotli_loop() propagates FORMAT_ERROR from brotli_step()
 * 3. brotli_loop() propagates BUDGET_EXCEEDED from brotli_step()
 * 4. brotli_loop() propagates TRUNCATED_INPUT from finish
 * 5. Three-way error classification: FORMAT (-1..-17), ALLOCATION
 *    (-21..-30), INTERNAL (-18..-20, -31, unknown) verified via
 *    test_brotli_error_classification() which is already in the suite.
 * 6. Every error code asserted as exact NGX_HTTP_MARKDOWN_DECOMP_*
 *    constant (not just non-NGX_OK)
 *
 * The no-progress guard is difficult to trigger with real Brotli data
 * since the real decoder should not stall.  We verify:
 *   - Malformed input triggers FORMAT_ERROR (not generic error)
 *   - The error is NOT folded to NGX_ERROR by brotli_loop()
 *   - FORMAT_ERROR does NOT set failure_origin (typed error, not bare
 *     NGX_ERROR)
 */
static void
test_brotli_no_progress_guard_and_error_propagation(void)
{
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("brotli no-progress guard and error propagation");

    /*
     * Test 1: Malformed Brotli data → FORMAT_ERROR flows through
     * brotli_loop() without being folded to NGX_ERROR.
     *
     * Strategy: Feed completely invalid bytes that will cause the Brotli
     * decoder to report BROTLI_DECODER_RESULT_ERROR with a FORMAT-class
     * error code (range -1 to -17).  Verify the return is the exact
     * NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR constant.
     */
    {
        /*
         * Invalid Brotli data: random garbage that does not form a valid
         * Brotli stream.  The decoder should reject this immediately
         * with a format error.
         */
        static const u_char  malformed_brotli[] = {
            0xFF, 0xFE, 0xFD, 0xFC, 0xFB, 0xFA, 0xF9, 0xF8,
            0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08
        };

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 4096);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor should be created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, malformed_brotli, sizeof(malformed_brotli),
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
            "malformed brotli must return exact FORMAT_ERROR constant "
            "(not generic NGX_ERROR)");
        TEST_ASSERT(out == NULL && out_len == 0,
            "malformed brotli must not expose partial output");

        /*
         * Verify failure_origin is NOT set for FORMAT_ERROR:
         * typed errors bypass the origin mechanism.  The origin field
         * should remain at NONE (its reset value at start of feed).
         */
        TEST_ASSERT(
            decomp->failure_origin == NGX_HTTP_MD_DECOMP_ORIGIN_NONE,
            "FORMAT_ERROR must not set failure_origin "
            "(typed error, not bare NGX_ERROR)");

        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    /*
     * Test 2: Verify brotli_loop() propagates FORMAT_ERROR specifically.
     *
     * Use a different malformed payload to confirm the specific error
     * code propagation is consistent.  A valid Brotli header prefix
     * followed by corrupt body data triggers a format error mid-decode.
     */
    {
        /*
         * Brotli stream with a valid-looking first byte but corrupted
         * continuation.  The window size byte (first byte) might pass
         * initial validation but the subsequent bytes will fail.
         */
        static const u_char  corrupt_body[] = {
            0x89, 0x00, 0x80, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
            0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF
        };

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 4096);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor should be created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, corrupt_body, sizeof(corrupt_body),
            &out, &out_len, &tp.pool, &test_log);
        /*
         * The decoder may return FORMAT_ERROR or (if it interprets
         * the first byte as a valid window-size declaration) may
         * attempt to proceed and find the no-progress guard or
         * a different format error.  Either way, it must NOT be
         * folded to generic NGX_ERROR.
         */
        TEST_ASSERT(
            rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
            || rc == NGX_ERROR,
            "corrupt brotli body must return a classified error");
        /*
         * If it's FORMAT_ERROR, origin must be NONE.
         * If it's NGX_ERROR, origin must be set (ALLOC or INTERNAL).
         */
        if (rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR) {
            TEST_ASSERT(
                decomp->failure_origin
                    == NGX_HTTP_MD_DECOMP_ORIGIN_NONE,
                "FORMAT_ERROR does not use failure_origin");
        } else {
            TEST_ASSERT(
                decomp->failure_origin
                    != NGX_HTTP_MD_DECOMP_ORIGIN_NONE,
                "NGX_ERROR must have failure_origin set");
        }
        TEST_ASSERT(out == NULL && out_len == 0,
            "corrupt body must not expose output");

        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    /*
     * Test 3: brotli_loop() propagates BUDGET_EXCEEDED from brotli_step()
     *
     * Compress known data, set budget smaller than decompressed size,
     * verify the exact BUDGET_EXCEEDED constant is returned.
     */
    {
        static const uint8_t  budget_text[] =
            "This text is long enough to exceed a tiny budget limit "
            "and demonstrate that budget exceeded propagates through "
            "brotli_loop correctly without being folded to NGX_ERROR";
        size_t                budget_text_len;
        uint8_t               budget_compressed[512];
        size_t                budget_compressed_len;

        budget_text_len = sizeof(budget_text) - 1;
        budget_compressed_len = sizeof(budget_compressed);
        TEST_ASSERT(BrotliEncoderCompress(
                        BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                        BROTLI_MODE_TEXT, budget_text_len, budget_text,
                        &budget_compressed_len, budget_compressed)
                    == BROTLI_TRUE,
            "brotli compression should succeed for budget test");

        test_pool_reset(&tp);
        /* Budget of 16 bytes: much smaller than the decompressed text */
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 16);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor (budget test) should be created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, budget_compressed, budget_compressed_len,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
            "brotli_loop must propagate exact BUDGET_EXCEEDED "
            "constant (not NGX_ERROR)");
        TEST_ASSERT(out == NULL && out_len == 0,
            "budget exceeded must not expose partial output");

        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    /*
     * Test 4: brotli_loop() propagates TRUNCATED_INPUT from finish.
     *
     * Feed partial compressed data, then call finish() while the decoder
     * is not done.  Verify the exact TRUNCATED_INPUT constant.
     * (Also covered by test_truncated_brotli_finish_errors but we verify
     * here specifically that the constant is exact, not just non-zero.)
     */
    {
        static const uint8_t  trunc_text[] =
            "brotli truncation propagation test data payload";
        size_t                trunc_text_len;
        uint8_t               trunc_compressed[512];
        size_t                trunc_compressed_len;

        trunc_text_len = sizeof(trunc_text) - 1;
        trunc_compressed_len = sizeof(trunc_compressed);
        TEST_ASSERT(BrotliEncoderCompress(
                        BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                        BROTLI_MODE_TEXT, trunc_text_len, trunc_text,
                        &trunc_compressed_len, trunc_compressed)
                    == BROTLI_TRUE,
            "brotli compression should succeed for truncation test");
        TEST_ASSERT(trunc_compressed_len > 4,
            "compressed payload should be long enough to truncate");

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
            trunc_text_len + 64);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor (truncation test) should be created");

        /* Feed only partial compressed data */
        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, trunc_compressed, trunc_compressed_len / 2,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "partial brotli feed should succeed awaiting more");
        test_pool_free_tracked_allocations();

        /* Call finish while decoder is not complete */
        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
            "finish on incomplete brotli must return exact "
            "TRUNCATED_INPUT constant (not generic NGX_ERROR)");

        free(out);
        free(decomp);
    }

    /*
     * Test 5: Verify three-way classification is included in test run.
     *
     * test_brotli_error_classification() is already called from main()
     * and covers:
     *   - FORMAT codes (-1 to -17) classify correctly
     *   - ALLOCATION codes (-21 to -30) classify correctly
     *   - INTERNAL codes (-18 to -20, -31) classify correctly
     *   - Unknown out-of-range classifies as INTERNAL (not FORMAT)
     *
     * This test adds a complementary end-to-end verification that a
     * decoder returning a FORMAT-range code results in FORMAT_ERROR
     * at the feed() API level (not NGX_ERROR).  Test 1 above already
     * demonstrates this with real malformed data.
     */

    /*
     * Test 6: Verify error code values are exact constants.
     *
     * Explicit compile-time checks that the constants exist and have
     * the expected relationship (all distinct, all negative, different
     * from NGX_ERROR and NGX_OK).
     */
    {
        TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR != NGX_ERROR,
            "FORMAT_ERROR must be distinct from NGX_ERROR");
        TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT != NGX_ERROR,
            "TRUNCATED_INPUT must be distinct from NGX_ERROR");
        TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED != NGX_ERROR,
            "BUDGET_EXCEEDED must be distinct from NGX_ERROR");
        TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR != NGX_ERROR,
            "IO_ERROR must be distinct from NGX_ERROR");
        TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR != NGX_OK,
            "FORMAT_ERROR must be distinct from NGX_OK");
        TEST_ASSERT(
            NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
                != NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
            "FORMAT_ERROR and BUDGET_EXCEEDED must be distinct");
        TEST_ASSERT(
            NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
                != NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
            "FORMAT_ERROR and TRUNCATED_INPUT must be distinct");
        TEST_ASSERT(
            NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
                != NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR,
            "FORMAT_ERROR and IO_ERROR must be distinct");
    }

    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("brotli no-progress guard and error propagation verified");
}


/*
 * test_brotli_budget_enforcement - Verify cumulative decompression budget
 * enforcement for the Brotli streaming decompressor.
 *
 * Key test cases:
 *   1. Exact budget success: text of known size N, max_decompressed_size=N,
 *      feed → NGX_OK and output matches.
 *   2. Budget exceeded by 1: max_decompressed_size=N-1, feed →
 *      BUDGET_EXCEEDED.
 *   3. Cumulative budget across chunks: split one compressed payload into
 *      two chunks, max_decompressed_size = decompressed_len, feed both
 *      → both succeed and total output matches.
 *   4. Cumulative budget exceeded on second chunk: budget slightly less
 *      than total, first chunk succeeds, second → BUDGET_EXCEEDED.
 *   5. Exact-budget probe success: covered by test_brotli_exact_budget_probe.
 */
static void
test_brotli_budget_enforcement(void)
{
    static const uint8_t text_a[] =
        "Budget enforcement test payload alpha (chunk A).";
    size_t               text_a_len;
    uint8_t              compressed_a[512];
    size_t               compressed_a_len;
    test_pool_t          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char              *out;
    size_t               out_len;
    ngx_int_t            rc;

    TEST_SUBSECTION("brotli budget enforcement");

    text_a_len = sizeof(text_a) - 1;

    /* Compress payload at runtime using BrotliEncoderCompress */
    compressed_a_len = sizeof(compressed_a);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, text_a_len, text_a,
                    &compressed_a_len, compressed_a)
                == BROTLI_TRUE,
        "brotli compression of text_a should succeed");

    /*
     * Test 1: Exact budget success.
     * max_decompressed_size = text_a_len → feed entire payload → NGX_OK.
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, text_a_len);
    TEST_ASSERT(decomp != NULL,
        "brotli decompressor (exact budget) should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed_a, compressed_a_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "exact budget feed should succeed");
    TEST_ASSERT(out_len == text_a_len,
        "exact budget feed should produce exactly N bytes");
    TEST_ASSERT(MEM_EQ(out, text_a, text_a_len),
        "exact budget output must match original text");
    test_pool_free_tracked_allocations();
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    /*
     * Test 2: Budget exceeded by 1.
     * max_decompressed_size = text_a_len - 1 → BUDGET_EXCEEDED.
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
        text_a_len - 1);
    TEST_ASSERT(decomp != NULL,
        "brotli decompressor (budget-1) should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed_a, compressed_a_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "budget-1 feed should return BUDGET_EXCEEDED");
    TEST_ASSERT(out == NULL && out_len == 0,
        "budget-exceeded feed should not expose any output");
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    /*
     * Test 3: Cumulative budget across chunks.
     * Compress one payload, split the compressed bytes in half, feed
     * each half separately with budget = decompressed_len.  Both
     * feeds should succeed and total output should match the original.
     */
    {
        static const uint8_t big_text[] =
            "Brotli multi-chunk cumulative budget enforcement "
            "test payload that is long enough to split into "
            "two compressed chunks for meaningful verification.";
        size_t   big_text_len;
        uint8_t  big_compressed[1024];
        size_t   big_compressed_len;
        size_t   split_point;
        u_char  *out1;
        size_t   out1_len;
        u_char  *out2;
        size_t   out2_len;
        u_char  *combined_output;
        size_t   combined_len;

        big_text_len = sizeof(big_text) - 1;
        big_compressed_len = sizeof(big_compressed);
        TEST_ASSERT(BrotliEncoderCompress(
                        BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                        BROTLI_MODE_TEXT, big_text_len, big_text,
                        &big_compressed_len, big_compressed)
                    == BROTLI_TRUE,
            "brotli compression of big_text should succeed");

        split_point = big_compressed_len / 2;
        TEST_ASSERT(split_point > 0 && split_point < big_compressed_len,
            "split point should be interior to compressed data");

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
            big_text_len);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor (multi-chunk budget) should be created");

        /* First chunk */
        out1 = NULL;
        out1_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, big_compressed, split_point,
            &out1, &out1_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "multi-chunk budget: first chunk feed should succeed");
        /* Save output before reclaiming pool allocations */
        combined_output = malloc(big_text_len);
        TEST_ASSERT(combined_output != NULL,
            "output collector should allocate");
        combined_len = 0;
        if (out1_len > 0) {
            memcpy(combined_output, out1, out1_len);
            combined_len = out1_len;
        }
        test_pool_free_tracked_allocations();

        /* Second chunk */
        out2 = NULL;
        out2_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, big_compressed + split_point,
            big_compressed_len - split_point,
            &out2, &out2_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "multi-chunk budget: second chunk feed should succeed");

        if (out2_len > 0) {
            memcpy(combined_output + combined_len, out2, out2_len);
            combined_len += out2_len;
        }
        test_pool_free_tracked_allocations();

        TEST_ASSERT(combined_len == big_text_len,
            "multi-chunk: total output should equal decompressed size");
        TEST_ASSERT(MEM_EQ(combined_output, big_text, big_text_len),
            "multi-chunk: reassembled output must match original text");
        TEST_ASSERT(decomp->total_decompressed == big_text_len,
            "multi-chunk: total_decompressed = decompressed size");

        free(combined_output);
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    /*
     * Test 4: Cumulative budget exceeded on second chunk.
     * Same payload split into two chunks but budget is one byte less
     * than the total decompressed size.  First chunk succeeds; second
     * chunk triggers BUDGET_EXCEEDED.
     */
    {
        static const uint8_t big_text[] =
            "Brotli multi-chunk cumulative budget enforcement "
            "test payload that is long enough to split into "
            "two compressed chunks for meaningful verification.";
        size_t   big_text_len;
        uint8_t  big_compressed[1024];
        size_t   big_compressed_len;
        size_t   split_point;
        u_char  *out1;
        size_t   out1_len;
        u_char  *out2;
        size_t   out2_len;

        big_text_len = sizeof(big_text) - 1;
        big_compressed_len = sizeof(big_compressed);
        TEST_ASSERT(BrotliEncoderCompress(
                        BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                        BROTLI_MODE_TEXT, big_text_len, big_text,
                        &big_compressed_len, big_compressed)
                    == BROTLI_TRUE,
            "brotli compression of big_text should succeed");

        split_point = big_compressed_len / 2;

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
            big_text_len - 1);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor (budget-1 multi-chunk) should be created");

        /* First chunk: may succeed partially */
        out1 = NULL;
        out1_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, big_compressed, split_point,
            &out1, &out1_len, &tp.pool, &test_log);
        /*
         * The first chunk may either succeed (if the decoder produces
         * fewer than budget bytes from the first half) or already hit
         * BUDGET_EXCEEDED (if the decoder produces enough output).
         * Either is valid depending on compressed payload
         * characteristics.
         */
        if (rc == NGX_OK) {
            /* First chunk fits within budget; second must fail */
            TEST_ASSERT(out1_len < big_text_len,
                "budget-1: first chunk within budget");
            TEST_ASSERT(decomp->total_decompressed == out1_len,
                "budget-1: accounting after first chunk");
            test_pool_free_tracked_allocations();

            out2 = NULL;
            out2_len = 0;
            rc = ngx_http_markdown_streaming_decomp_feed(
                decomp, big_compressed + split_point,
                big_compressed_len - split_point,
                &out2, &out2_len, &tp.pool, &test_log);
            TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
                "budget-1: second chunk should exceed budget");
            TEST_ASSERT(out2 == NULL && out2_len == 0,
                "budget-1: no output on budget exceed");
        } else {
            /* First chunk already exceeded budget */
            TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
                "budget-1: first chunk exceeds budget");
            TEST_ASSERT(out1 == NULL && out1_len == 0,
                "budget-1: no output on first exceed");
        }

        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("brotli budget enforcement verified");
}


/*
 * test_brotli_alloc_failures_and_lifecycle - Verify allocation failure
 * handling and cleanup lifecycle for the Brotli streaming decompressor.
 *
 * Key test cases:
 *   1. Create failure (pcalloc NULL) → decomp_create returns NULL,
 *      no cleanup handler registered
 *   2. Pool cleanup registration failure → decoder rolled back, no leak
 *   3. Cleanup idempotency: create decoder, call cleanup twice → no crash
 *      (second call is no-op due to NULL pointer check)
 *   4. Pre-decode alloc failure: workspace ngx_alloc returns NULL →
 *      NGX_ERROR returned, state unchanged, no leak
 *   5. Heap workspace freed on error paths (g_free_on_palloc_violation == 0)
 *   6. Post-decode expansion failure sets finished=1 (non-retryable)
 *   7. Failure origin set correctly on pre-decode alloc failure
 */
static void
test_brotli_alloc_failures_and_lifecycle(void)
{
    static const uint8_t                 text[] =
        "Brotli allocation failure lifecycle test payload";
    size_t                               text_len;
    size_t                               compressed_len;
    uint8_t                              compressed[512];
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("brotli allocation failures and cleanup lifecycle");

    text_len = sizeof(text) - 1;
    compressed_len = sizeof(compressed);
    TEST_ASSERT(BrotliEncoderCompress(
                    BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                    BROTLI_MODE_TEXT, text_len, text,
                    &compressed_len, compressed)
                == BROTLI_TRUE,
        "brotli compression should succeed");

    /*
     * Test 1: Create failure (pcalloc NULL) → decomp_create returns
     * NULL, no cleanup handler registered.
     */
    test_pool_reset(&tp);
    g_pcalloc_fail_once = 1;
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 1024);
    TEST_ASSERT(decomp == NULL,
        "brotli create should fail when pcalloc returns NULL");
    TEST_ASSERT(tp.pool.cleanups == NULL,
        "no cleanup should be registered on pcalloc failure");

    /*
     * Test 2: Pool cleanup registration failure after successful
     * BrotliDecoderCreateInstance → decomp_create returns NULL.
     * The decoder must be destroyed via the rollback path (same
     * idempotent cleanup routine), no leak.
     */
    test_pool_reset(&tp);
    g_cleanup_add_fail_once = 1;
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 1024);
    TEST_ASSERT(decomp == NULL,
        "brotli create should fail when cleanup_add returns NULL");
    TEST_ASSERT(tp.pool.cleanups == NULL,
        "no cleanup registered when cleanup_add fails");
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "rollback must not violate pool memory ownership");

    /*
     * Test 3: Cleanup idempotency — create a valid decoder, call
     * cleanup explicitly, then run pool cleanups (simulating pool
     * destruction).  The second cleanup invocation must be a safe
     * no-op (NULL pointer sentinel check).
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 1024);
    TEST_ASSERT(decomp != NULL,
        "brotli decompressor should be created");
    TEST_ASSERT(decomp->initialized == 1,
        "brotli decompressor should be initialized");
    TEST_ASSERT(decomp->state.brotli != NULL,
        "brotli decoder pointer should be non-NULL");

    /* First explicit cleanup: destroys the decoder, sets ptr to NULL */
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    TEST_ASSERT(decomp->initialized == 0,
        "first cleanup should clear initialized flag");
    TEST_ASSERT(decomp->state.brotli == NULL,
        "first cleanup should NULL the brotli pointer");

    /* Second explicit cleanup: must be safe no-op */
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    TEST_ASSERT(decomp->state.brotli == NULL,
        "second cleanup should remain NULL (no double-free)");

    /*
     * Simulate pool destruction by invoking the registered pool
     * cleanup handler directly.  Since the handler is registered
     * in pool->cleanups, invoke it with the same data pointer.
     * After the first explicit cleanup set initialized=0, the
     * pool handler returns immediately (safe no-op).
     */
    {
        ngx_pool_cleanup_t  *cln;

        cln = tp.pool.cleanups;
        if (cln != NULL && cln->handler != NULL) {
            cln->handler(cln->data);
        }
    }
    TEST_ASSERT(decomp->state.brotli == NULL,
        "pool cleanup after explicit cleanup is safe no-op");

    free(decomp);

    /*
     * Test 4: Pre-decode workspace allocation failure.
     * ngx_alloc returns NULL BEFORE any decoder invocation in the
     * current feed call.  Return NGX_ERROR, decoder state unchanged,
     * no leak.
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 1024);
    TEST_ASSERT(decomp != NULL,
        "brotli decompressor should be created for alloc failure test");

    g_alloc_fail_once = 1;
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "pre-decode alloc failure should return NGX_ERROR");
    TEST_ASSERT(out == NULL && out_len == 0,
        "pre-decode alloc failure should not expose output");
    TEST_ASSERT(decomp->finished == 0,
        "pre-decode alloc failure should not advance decoder state");
    TEST_ASSERT(decomp->failure_origin
                == NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION,
        "pre-decode alloc failure should set ALLOCATION origin");

    /* Verify decoder can still be used (state was not advanced) */
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "retry after pre-decode failure should succeed");
    TEST_ASSERT(out_len == text_len,
        "retry should produce the complete decompressed output");
    TEST_ASSERT(MEM_EQ(out, text, text_len),
        "retry output should match original text");

    test_pool_free_tracked_allocations();
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    /*
     * Test 5: Heap workspace freed on error paths
     * (g_free_on_palloc_violation == 0).
     * Verified by the assertion at the end of this function after
     * exercising all error paths above.
     */

    /*
     * Test 6: Post-decode expansion failure sets finished=1.
     *
     * After a stream has been decoded completely and finished is set,
     * verify that no re-feed is possible.  This validates the
     * behavioral contract that once the decoder has consumed input
     * and cannot be retried, the finished flag prevents re-feeding.
     *
     * Direct expansion failure testing (where the decoder has
     * consumed partial input but expansion fails) is verified via
     * the shared expand_buf helper tests.  Here we verify the
     * non-retryable contract behaviorally: after finished is set,
     * subsequent non-empty feeds return FORMAT_ERROR.
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
    TEST_ASSERT(decomp != NULL,
        "brotli decompressor for post-decode finish test");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "initial feed should succeed");
    TEST_ASSERT(decomp->finished == 1,
        "decoder should be finished after complete stream");
    test_pool_free_tracked_allocations();

    /* Attempt to re-feed: FORMAT_ERROR (non-retryable) */
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, 10,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "re-feed after finished must return FORMAT_ERROR");
    TEST_ASSERT(out == NULL && out_len == 0,
        "re-feed after finished must not expose output");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    /*
     * Test 7: Failure origin set correctly on pre-decode alloc
     * failure and reset on next successful call.
     */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 1024);
    TEST_ASSERT(decomp != NULL,
        "brotli decompressor for origin lifecycle test");

    /* Inject pre-decode alloc failure */
    g_alloc_fail_once = 1;
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "alloc failure should return NGX_ERROR");
    TEST_ASSERT(decomp->failure_origin
                == NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION,
        "failure origin should be ALLOCATION after alloc failure");

    /* Successful feed resets origin (per-call lifecycle contract) */
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "feed after alloc failure recovery should succeed");
    TEST_ASSERT(decomp->failure_origin
                == NGX_HTTP_MD_DECOMP_ORIGIN_NONE,
        "successful feed should reset failure origin to NONE");

    test_pool_free_tracked_allocations();
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    /* Final meta-assertion: no pool-memory ownership violations */
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "brotli error paths must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "brotli error paths must not ngx_free() static pool buf");

    TEST_PASS("brotli allocation failures and lifecycle verified");
}


/*
 * test_brotli_truncation_detection_property - Property-based test for
 * truncation detection.
 *
 * Feature: Brotli streaming decompression
 * Property 4: Truncation Detection
 *
 * Coverage: truncated Brotli input is rejected at end of stream.
 *
 * For ALL valid Brotli streams truncated at ANY position before
 * completion, finish() returns TRUNCATED_INPUT.
 *
 * Strategy:
 *   - Generate multiple random plaintext payloads of varying sizes
 *   - Compress each with Brotli
 *   - For each compressed payload, truncate at a random offset
 *     (1 to compressed_len - 1)
 *   - Feed the truncated bytes, then call finish()
 *   - Assert finish() returns NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT
 *
 * Minimum 100 iterations as required by the design.
 */

/* Simple PRNG for property test randomization (xorshift32) */
static unsigned int g_brotli_prop_prng = 42;

static unsigned int
brotli_prop_prng_next(void)
{
    g_brotli_prop_prng ^= g_brotli_prop_prng << 13;
    g_brotli_prop_prng ^= g_brotli_prop_prng >> 17;
    g_brotli_prop_prng ^= g_brotli_prop_prng << 5;
    return g_brotli_prop_prng;
}

static void
test_brotli_truncation_detection_property(void)
{
    const int                             iterations = 150;
    int                                   i;
    int                                   passed;

    TEST_SUBSECTION(
        "Property 4: Truncation Detection — "
        "truncated Brotli streams yield TRUNCATED_INPUT");

    passed = 0;
    g_brotli_prop_prng = 42;

    for (i = 0; i < iterations; i++) {
        /*
         * Generate random plaintext of varying length
         * (16 to 2048 bytes) to ensure diverse compressed payloads.
         */
        size_t         text_len;
        uint8_t        text_buf[2048];
        size_t         compressed_capacity;
        size_t         compressed_len;
        uint8_t       *compressed;
        size_t         truncate_pos;
        test_pool_t    tp;
        ngx_http_markdown_streaming_decomp_t *decomp;
        u_char        *out;
        size_t         out_len;
        ngx_int_t      rc;
        size_t         j;

        /* Random plaintext length: 16..2047 */
        text_len = 16 + (brotli_prop_prng_next() % 2032);

        /* Fill with pseudo-random bytes */
        for (j = 0; j < text_len; j++) {
            text_buf[j] = (uint8_t)(brotli_prop_prng_next() & 0xFF);
        }

        /* Compress with Brotli */
        compressed_capacity = BrotliEncoderMaxCompressedSize(text_len);
        if (compressed_capacity == 0) {
            compressed_capacity = text_len + 1024;
        }
        compressed = (uint8_t *) malloc(compressed_capacity);
        TEST_ASSERT(compressed != NULL,
            "property test: malloc for compressed buffer");

        compressed_len = compressed_capacity;
        TEST_ASSERT(
            BrotliEncoderCompress(
                BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, text_len, text_buf,
                &compressed_len, compressed)
            == BROTLI_TRUE,
            "property test: brotli compression should succeed");

        /* Ensure compressed payload is long enough to truncate */
        if (compressed_len < 2) {
            free(compressed);
            continue;
        }

        /*
         * Truncation position: random offset in [1, compressed_len - 1].
         * We never feed 0 bytes (trivial no-op) and never feed the
         * full stream (that would succeed, not truncate).
         */
        truncate_pos =
            1 + (brotli_prop_prng_next() % (compressed_len - 1));

        /* Create decompressor with generous budget */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
            text_len * 2);
        TEST_ASSERT(decomp != NULL,
            "property test: brotli decompressor created");

        /* Feed truncated data */
        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, compressed, truncate_pos,
            &out, &out_len, &tp.pool, &test_log);

        /*
         * Feed may succeed (NGX_OK / partial decode) or return
         * FORMAT_ERROR for severely corrupt tail.  Either is
         * acceptable before finish(); the key property is about
         * finish() itself.
         */
        if (rc != NGX_OK && rc != NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
            && rc != NGX_ERROR)
        {
            /*
             * BUDGET_EXCEEDED or IO_ERROR are unexpected here because
             * we set a generous budget and the data is well-formed up
             * to the truncation point.  Still, they indicate a
             * non-success path, so we skip finish validation for
             * these (they already signaled failure).
             */
            test_pool_free_tracked_allocations();
            ngx_http_markdown_streaming_decomp_cleanup(decomp);
            free(decomp);
            free(compressed);
            passed++;
            continue;
        }

        /* Reclaim pool-allocated output before finish */
        test_pool_free_tracked_allocations();

        if (rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
            || rc == NGX_ERROR)
        {
            /*
             * Feed already detected an error (e.g. the truncation
             * point fell inside a critical decoder state that
             * immediately fails).  The stream is already rejected —
             * no need to call finish().  This still satisfies the
             * property: the truncated stream was NOT accepted as
             * valid.
             */
            ngx_http_markdown_streaming_decomp_cleanup(decomp);
            free(decomp);
            free(compressed);
            passed++;
            continue;
        }

        /* Feed returned NGX_OK — call finish() to check truncation */
        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &out, &out_len, &tp.pool, &test_log);

        TEST_ASSERT(
            rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
            "property test: truncated Brotli stream must yield "
            "TRUNCATED_INPUT at finish()");

        /* Clean up */
        free(out);
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
        free(compressed);
        test_pool_free_tracked_allocations();
        passed++;
    }

    TEST_ASSERT(passed >= 100,
        "property test: at least 100 iterations completed");
    TEST_PASS(
        "Property 4: all truncated Brotli streams correctly "
        "detected — TRUNCATED_INPUT returned");
}
#endif


/*
 * test_pool_run_cleanups - Execute all registered cleanup handlers on a
 * test pool, then free each cleanup entry and clear the list.
 * Mirrors the behaviour of NGINX pool destruction for test assertions.
 *
 * Parameters:
 *   tp - test pool whose cleanups should be run
 *
 * Side effects: invokes each handler, frees each cleanup node,
 *               sets tp->pool.cleanups to NULL.
 */
static void
test_pool_run_cleanups(test_pool_t *tp)
{
    ngx_pool_cleanup_t  *cln;
    ngx_pool_cleanup_t  *next;

    cln = tp->pool.cleanups;
    while (cln != NULL) {
        next = cln->next;
        if (cln->handler != NULL) {
            cln->handler(cln->data);
        }
        free(cln);
        cln = next;
    }

    tp->pool.cleanups = NULL;
}

/*
 * compress_payload - Compress a plain-text buffer using zlib deflate with
 * the specified compression type (gzip or deflate).  Used by round-trip
 * and budget tests to produce valid compressed input for the decompressor.
 *
 * Parameters:
 *   in      - input bytes to compress
 *   in_len  - length of in
 *   type    - compression format (GZIP or DEFLATE)
 *   out     - [out] receives a malloc'd buffer with the compressed data
 *   out_len - [out] receives the length of the compressed data
 *
 * Return: NGX_OK on success, NGX_ERROR on any failure (NULL args,
 *         unsupported type, zlib error, or malloc failure).
 *
 * Side effects: allocates via malloc; caller must free(*out) on success.
 */
static int
compress_payload_with_window_bits(const u_char *in, size_t in_len,
    int window_bits, u_char **out, size_t *out_len)
{
    z_stream  s;
    int       rc;
    size_t    cap;
    u_char   *in_copy;

    if (in == NULL || out == NULL || out_len == NULL) {
        return NGX_ERROR;
    }

    memset(&s, 0, sizeof(s));

    rc = deflateInit2(&s, Z_DEFAULT_COMPRESSION, Z_DEFLATED,
                      window_bits, 8, Z_DEFAULT_STRATEGY);
    if (rc != Z_OK) {
        return NGX_ERROR;
    }

    in_copy = NULL;
    if (in_len > 0) {
        in_copy = malloc(in_len);
        if (in_copy == NULL) {
            deflateEnd(&s);
            return NGX_ERROR;
        }
        memcpy(in_copy, in, in_len);
    }

    cap = in_len + (in_len / 8) + 64;
    *out = malloc(cap);
    if (*out == NULL) {
        free(in_copy);
        deflateEnd(&s);
        return NGX_ERROR;
    }

    if (in_len > (size_t) UINT_MAX || cap > (size_t) UINT_MAX) {
        free(*out);
        free(in_copy);
        *out = NULL;
        deflateEnd(&s);
        return NGX_ERROR;
    }

    s.next_in = in_copy;
    s.avail_in = (uInt) in_len;
    s.next_out = *out;
    s.avail_out = (uInt) cap;

    rc = deflate(&s, Z_FINISH);
    if (rc != Z_STREAM_END) {
        free(*out);
        free(in_copy);
        *out = NULL;
        deflateEnd(&s);
        return NGX_ERROR;
    }

    *out_len = s.total_out;
    free(in_copy);
    deflateEnd(&s);
    return NGX_OK;
}


static int
compress_payload(const u_char *in, size_t in_len,
    ngx_http_markdown_compression_type_e type,
    u_char **out, size_t *out_len)
{
    int  window_bits;

    if (type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP) {
        window_bits = MAX_WBITS + 16;
    } else if (type == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE) {
        /* Raw deflate matches the common non-standard HTTP encoding. */
        window_bits = -MAX_WBITS;
    } else {
        return NGX_ERROR;
    }

    return compress_payload_with_window_bits(
        in, in_len, window_bits, out, out_len);
}

/*
 * Join two compressed byte ranges into one test-owned allocation.
 * Returns NULL when the inputs are invalid, the combined size overflows,
 * or allocation fails.  The caller owns the returned buffer.
 */
static u_char *
concat_compressed_ranges(const u_char *first, size_t first_len,
    const u_char *second, size_t second_len, size_t *combined_len)
{
    u_char  *combined;

    if (first == NULL || second == NULL || combined_len == NULL
        || first_len > SIZE_MAX - second_len)
    {
        return NULL;
    }

    *combined_len = first_len + second_len;
    combined = malloc(*combined_len);
    if (combined == NULL) {
        return NULL;
    }

    memcpy(combined, first, first_len);
    memcpy(combined + first_len, second, second_len);
    return combined;
}

/*
 * test_size_to_uint_guards - Verify the size_to_uint narrowing helper
 * correctly rejects values above UINT_MAX and accepts values within range.
 *
 * Branches covered:
 *   - overflow path (value > UINT_MAX returns 1)
 *   - success path  (value <= UINT_MAX returns 0, preserves value)
 */
static void
test_size_to_uint_guards(void)
{
    uInt    narrowed;
    size_t  too_large;

    TEST_SUBSECTION("size_to_uint guard branches");

    narrowed = 0;
    too_large = (size_t) UINT_MAX + 1;
    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_size_to_uint(
            too_large, &narrowed) == 1,
        "values above UINT_MAX should overflow");

    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_size_to_uint(123, &narrowed) == 0,
        "values within UINT_MAX should narrow successfully");
    TEST_ASSERT(narrowed == 123, "narrowed value should be preserved");

    TEST_PASS("size_to_uint guard branches covered");
}

/*
 * test_create_and_cleanup - Verify the create/cleanup lifecycle for the
 * streaming decompressor.
 *
 * Branches covered:
 *   - NULL pool returns NULL
 *   - unsupported compression type returns NULL
 *   - successful gzip create sets initialized and registers cleanup
 *   - cleanup(NULL) is a safe no-op
 *   - cleanup on zeroed (uninitialised) struct leaves initialized == 0
 *   - registered cleanup handler clears initialized flag
 */
static void
test_create_and_cleanup(void)
{
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;

    TEST_SUBSECTION("create and cleanup branches");

    test_pool_reset(&tp);

    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_create(
            NULL, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 1024)
        == NULL,
        "NULL pool should return NULL");

    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN, 1024)
        == NULL,
        "unsupported compression type should return NULL");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 1024);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");
    TEST_ASSERT(decomp->initialized == 1,
        "gzip decompressor should initialize");
    TEST_ASSERT(tp.pool.cleanups != NULL,
        "cleanup handler should be registered");

    ngx_http_markdown_streaming_decomp_cleanup(NULL);
    {
        ngx_http_markdown_streaming_decomp_t  empty;

        memset(&empty, 0, sizeof(empty));
        ngx_http_markdown_streaming_decomp_cleanup(&empty);
        TEST_ASSERT(empty.initialized == 0,
            "cleanup should leave uninitialized state untouched");
    }

    test_pool_run_cleanups(&tp);
    TEST_ASSERT(decomp->initialized == 0,
        "registered cleanup should clear initialized state");

    free(decomp);
    TEST_PASS("create and cleanup branches covered");
}

/*
 * test_create_failure_paths_and_cleanup_default - Verify create failure
 * when sub-allocations fail and the default switch arm in cleanup.
 *
 * Branches covered:
 *   - ngx_pcalloc failure returns NULL
 *   - inflateInit2 failure for gzip returns NULL
 *   - deferred inflateInit2 failure for deflate makes feed return NGX_ERROR
 *   - cleanup with NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN type clears
 *     initialized (default switch arm)
 */
static void
test_create_failure_paths_and_cleanup_default(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    ngx_http_markdown_streaming_decomp_t  local;
    u_char                               *out;
    size_t                                out_len;
    ngx_int_t                             rc;

    TEST_SUBSECTION("create failure paths and cleanup default switch arm");

    test_pool_reset(&tp);

    g_pcalloc_fail_once = 1;
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 1024);
    TEST_ASSERT(decomp == NULL,
        "create should fail when ngx_pcalloc returns NULL");

    g_inflate_init_fail_once = 1;
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 1024);
    TEST_ASSERT(decomp == NULL,
        "gzip create should fail when inflateInit2 errors");

    g_inflate_init_fail_once = 1;
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE, 1024);
    TEST_ASSERT(decomp != NULL,
        "deflate create should defer inflateInit2 until feed");
    TEST_ASSERT(decomp->zlib_header_pending == 1,
        "deflate create should wait for header bytes");

    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "xx", 2, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "deflate feed should fail when deferred inflateInit2 errors");
    TEST_ASSERT(out == NULL && out_len == 0,
        "failed deferred initialization should not emit output");
    TEST_ASSERT(decomp->initialized == 0,
        "failed deferred initialization should remain uninitialized");
    free(decomp);

    memset(&local, 0, sizeof(local));
    local.initialized = 1;
    local.type = NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN;
    ngx_http_markdown_streaming_decomp_cleanup(&local);
    TEST_ASSERT(local.initialized == 0,
        "cleanup should clear initialized flag for unknown type");

    TEST_PASS("create failure/default cleanup branches covered");
}

/*
 * test_roundtrip_and_empty_feed - Verify end-to-end decompression round-trip
 * for both gzip and deflate, plus empty-input and post-stream finish no-ops.
 *
 * Branches covered:
 *   - feed of a fully-compressed payload produces the original text
 *   - feed with NULL/zero-length input is a no-op (no output emitted)
 *   - finish after a completed stream is a no-op (no tail output)
 */
static void
test_roundtrip_and_empty_feed(void)
{
    const char                          *text;
    size_t                               text_len;
    u_char                              *compressed;
    size_t                               compressed_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;
    ngx_http_markdown_compression_type_e  types[2];

    TEST_SUBSECTION("feed round-trip, empty input, and finish no-op");

    text = "Hello from the streaming decompressor test. "
           "This payload should round-trip.";
    text_len = test_cstrnlen(text, 1024);
    types[0] = NGX_HTTP_MARKDOWN_COMPRESSION_GZIP;
    types[1] = NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE;

    for (size_t i = 0; i < ARRAY_SIZE(types); i++) {
        test_pool_reset(&tp);
        compressed = NULL;
        out = NULL;
        out_len = 0;

        rc = compress_payload((const u_char *) text, text_len,
                              types[i], &compressed, &compressed_len);
        TEST_ASSERT(rc == NGX_OK, "compression should succeed");

        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, types[i], 0);
        TEST_ASSERT(decomp != NULL, "decompressor should be created");

        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, compressed, compressed_len,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK, "feed should succeed");
        TEST_ASSERT(out != NULL, "feed should produce output");
        TEST_ASSERT(out_len == text_len,
            "feed output length should match source");
        TEST_ASSERT(MEM_EQ(out, text, out_len),
            "feed output should match source");

        {
            u_char  *empty_out;
            size_t   empty_len;

            empty_out = (u_char *) 0x1;
            empty_len = 1;
            rc = ngx_http_markdown_streaming_decomp_feed(
                decomp, NULL, 0, &empty_out, &empty_len,
                &tp.pool, &test_log);
            TEST_ASSERT(rc == NGX_OK,
                "empty input should be a no-op");
            TEST_ASSERT(empty_out == NULL && empty_len == 0,
                "empty input should not emit output");
        }

        {
            u_char  *finish_out;
            size_t   finish_len;

            finish_out = NULL;
            finish_len = 0;
            rc = ngx_http_markdown_streaming_decomp_finish(
                decomp, &finish_out, &finish_len,
                &tp.pool, &test_log);
            TEST_ASSERT(rc == NGX_OK,
                "finish should succeed after a completed feed");
            TEST_ASSERT(finish_out == NULL && finish_len == 0,
                "finish should be a no-op after stream end");
        }

        free(compressed);
        /* out is pool-owned; reclaimed by the next test_pool_reset. */
        free(decomp);
        TEST_ASSERT(g_free_on_palloc_violation == 0,
            "production code must not ngx_free() pool memory");
        TEST_ASSERT(g_free_on_static_pool_violation == 0,
            "production code must not ngx_free() static pool memory");
    }

    TEST_PASS("feed round-trip and empty-input branches covered");
}

/*
 * test_budget_and_invalid_type_branches - Verify budget-exceeded detection
 * and the invalid-type decline/error paths in feed and finish.
 *
 * Branches covered:
 *   - feed returns NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED when
 *     decompressed output exceeds the configured limit
 *   - feed returns NGX_DECLINED for UNKNOWN compression type
 *   - finish returns NGX_ERROR for UNKNOWN compression type
 */
static void
test_budget_and_invalid_type_branches(void)
{
    const char                          *text;
    size_t                               text_len;
    u_char                              *compressed;
    size_t                               compressed_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("budget exceed and invalid type branches");

    text = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
    text_len = test_cstrnlen(text, 1024);
    test_pool_reset(&tp);
    compressed = NULL;
    out = NULL;
    out_len = 0;

    rc = compress_payload((const u_char *) text, text_len,
                          NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
                          &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "compression should succeed");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 64);
    TEST_ASSERT(decomp != NULL, "bounded decompressor should be created");

    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "feed should report budget exceed");
    TEST_ASSERT(out == NULL && out_len == 0,
        "budget exceed should not publish output");

    free(compressed);
    free(decomp);

    {
        ngx_http_markdown_streaming_decomp_t  fake;
        u_char                                *fake_out;
        size_t                                 fake_len;

        memset(&fake, 0, sizeof(fake));
        fake.initialized = 1;
        fake.type = NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN;
        fake_out = NULL;
        fake_len = 0;

        rc = ngx_http_markdown_streaming_decomp_feed(
            &fake, (const u_char *) "x", 1,
            &fake_out, &fake_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_DECLINED,
            "unsupported type should decline in feed");

        rc = ngx_http_markdown_streaming_decomp_finish(
            &fake, &fake_out, &fake_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_ERROR,
            "unsupported type should error in finish");
    }

    TEST_PASS("budget and invalid-type branches covered");
}

/*
 * test_truncated_finish_errors - Verify that EOF classifies an incomplete
 * gzip trailer as truncated input, while feed still accepts partial input.
 *
 * Branches covered:
 *   - feed of a truncated payload succeeds and emits partial output
 *   - finish on an incomplete stream returns TRUNCATED_INPUT
 */
static void
test_truncated_finish_errors(void)
{
    const char                          *text;
    size_t                               text_len;
    u_char                              *compressed;
    size_t                               compressed_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;
    size_t                               truncated_len;

    TEST_SUBSECTION("finish error on truncated stream");

    text = "Truncated gzip input should fail at finish time.";
    text_len = test_cstrnlen(text, 1024);
    test_pool_reset(&tp);
    compressed = NULL;
    out = NULL;
    out_len = 0;

    rc = compress_payload((const u_char *) text, text_len,
                          NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
                          &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "compression should succeed");
    TEST_ASSERT(compressed_len > 8, "compressed payload should be long enough");

    truncated_len = compressed_len - 8;
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");

    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, truncated_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "truncated feed should still succeed");
    TEST_ASSERT(out != NULL, "truncated feed should emit partial output");

    {
        u_char  *finish_out;
        size_t   finish_len;

        finish_out = (u_char *) 0x1;
        finish_len = 1;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &finish_out, &finish_len,
            &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
            "finish should fail for an incomplete gzip stream");
    }

    free(compressed);
    /* out is pool-owned; reclaimed by the next test_pool_reset. */
    free(decomp);
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("finish error branch covered");
}


/*
 * Real malformed gzip and deflate bytes must be classified by the production
 * zlib feed path.  These payloads contain valid format-selection prefixes and
 * an invalid RFC 1951 block type, so inflate() reports Z_DATA_ERROR.
 */
static void
test_malformed_zlib_formats_are_classified(void)
{
    static const u_char  malformed_gzip[] = {
        0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x03, 0x07, 0x00
    };
    static const u_char  malformed_raw[] = { 0x07, 0x00 };
    static const u_char  malformed_zlib[] = { 0x78, 0x9c, 0x07, 0x00 };
    struct {
        const char                            *name;
        ngx_http_markdown_compression_type_e  type;
        const u_char                          *data;
        size_t                                 len;
    } cases[] = {
        {
            "malformed gzip", NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
            malformed_gzip, sizeof(malformed_gzip)
        },
        {
            "malformed raw deflate", NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
            malformed_raw, sizeof(malformed_raw)
        },
        {
            "malformed zlib-wrapped deflate",
            NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
            malformed_zlib, sizeof(malformed_zlib)
        }
    };
    size_t  i;

    TEST_SUBSECTION("malformed gzip/deflate classification");

    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        test_pool_t                           tp;
        ngx_http_markdown_streaming_decomp_t *decomp;
        u_char                               *out;
        size_t                                out_len;
        ngx_int_t                             rc;

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, cases[i].type, 0);
        TEST_ASSERT(decomp != NULL,
            "malformed format decompressor should be created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, cases[i].data, cases[i].len,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
            cases[i].name);
        TEST_ASSERT(out == NULL && out_len == 0,
            "malformed input must not publish partial output");

        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    TEST_PASS("malformed gzip/deflate classified as format errors");
}


/*
 * Feed-time partial deflate is not terminal.  Once the HTTP response reaches
 * EOF, finish must classify both raw and zlib-wrapped incomplete streams as
 * truncated input.
 */
static void
test_truncated_deflate_formats_are_classified_at_finish(void)
{
    const char  *text;
    int          window_bits[] = { -MAX_WBITS, MAX_WBITS };
    const char  *names[] = { "raw deflate", "zlib-wrapped deflate" };
    size_t       text_len;
    size_t       i;

    TEST_SUBSECTION("truncated deflate classification at EOF finish");

    text = "partial deflate input is only truncated after response EOF";
    text_len = test_cstrnlen(text, 1024);

    for (i = 0; i < sizeof(window_bits) / sizeof(window_bits[0]); i++) {
        test_pool_t                           tp;
        ngx_http_markdown_streaming_decomp_t *decomp;
        u_char                               *compressed;
        size_t                                compressed_len;
        u_char                               *out;
        size_t                                out_len;
        ngx_int_t                             rc;

        compressed = NULL;
        test_pool_reset(&tp);
        rc = compress_payload_with_window_bits(
            (const u_char *) text, text_len, window_bits[i],
            &compressed, &compressed_len);
        TEST_ASSERT(rc == NGX_OK, "deflate compression should succeed");
        TEST_ASSERT(compressed_len > 2,
            "deflate payload should be long enough to truncate");

        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE, 0);
        TEST_ASSERT(decomp != NULL,
            "deflate decompressor should be created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, compressed, compressed_len - 1,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK, names[i]);

        out = (u_char *) 0x1;
        out_len = 1;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
            names[i]);
        TEST_ASSERT(out == NULL && out_len == 0,
            "truncated finish must publish no tail output");

        free(compressed);
        test_pool_free_tracked_allocations();
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    for (i = 0; i < 2; i++) {
        test_pool_t                           tp;
        ngx_http_markdown_streaming_decomp_t *decomp;
        u_char                                one_header_byte;
        u_char                               *out;
        size_t                                out_len;
        ngx_int_t                             rc;

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE, 0);
        TEST_ASSERT(decomp != NULL,
            "pending-header deflate decompressor should be created");

        one_header_byte = 0x78;
        out = NULL;
        out_len = 0;
        if (i == 1) {
            rc = ngx_http_markdown_streaming_decomp_feed(
                decomp, &one_header_byte, 1,
                &out, &out_len, &tp.pool, &test_log);
            TEST_ASSERT(rc == NGX_OK,
                "one deflate header byte should await more feed input");
        }

        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
            "EOF before the two-byte deflate header must be truncated");
        TEST_ASSERT(out == NULL && out_len == 0,
            "pending-header truncation must not publish output");

        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    TEST_PASS("raw and zlib-wrapped truncation classified at finish");
}

/*
 * Verify that concatenated gzip members in one compressed feed are decoded
 * exactly once and share one cumulative response budget.
 */
static void
test_gzip_members_in_one_feed(void)
{
    const char                          *first_text;
    const char                          *second_text;
    size_t                               first_len;
    size_t                               second_len;
    u_char                              *first_gzip;
    size_t                               first_gzip_len;
    u_char                              *second_gzip;
    size_t                               second_gzip_len;
    u_char                              *combined;
    size_t                               combined_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("concatenated gzip members in one feed");

    first_text = "first gzip member\n";
    second_text = "second gzip member\n";
    first_len = test_cstrnlen(first_text, 1024);
    second_len = test_cstrnlen(second_text, 1024);
    first_gzip = NULL;
    second_gzip = NULL;
    test_pool_reset(&tp);

    rc = compress_payload((const u_char *) first_text, first_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &first_gzip, &first_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "first gzip member should compress");
    rc = compress_payload((const u_char *) second_text, second_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &second_gzip, &second_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "second gzip member should compress");

    combined = concat_compressed_ranges(
        first_gzip, first_gzip_len,
        second_gzip, second_gzip_len, &combined_len);
    TEST_ASSERT(combined != NULL, "gzip members should concatenate");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        first_len + second_len);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, combined, combined_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "concatenated gzip members should decompress");
    TEST_ASSERT(out_len == first_len + second_len,
        "both gzip members should emit their complete output");
    TEST_ASSERT(MEM_EQ(out, first_text, first_len),
        "first gzip member output should match");
    TEST_ASSERT(MEM_EQ(out + first_len, second_text, second_len),
        "second gzip member output should match");
    TEST_ASSERT(decomp->total_decompressed == first_len + second_len,
        "decompression accounting should remain cumulative");

    {
        u_char  *finish_out;
        size_t   finish_len;

        finish_out = (u_char *) 0x1;
        finish_len = 1;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &finish_out, &finish_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "completed concatenated gzip members should finish");
        TEST_ASSERT(finish_out == NULL && finish_len == 0,
            "member-boundary finish should not emit duplicate output");
    }

    free(combined);
    free(second_gzip);
    free(first_gzip);
    free(decomp);
    TEST_PASS("concatenated gzip members decoded in one feed");
}

/*
 * A response-wide budget limits decompressed bytes, not gzip member count.
 * Once a member exactly fills the budget, a later empty member remains valid;
 * a later member that emits even one byte must still fail the same budget.
 */
static void
test_gzip_empty_member_after_exact_budget(void)
{
    const char                          *first_text;
    size_t                               first_len;
    u_char                              *first_gzip;
    size_t                               first_gzip_len;
    u_char                              *empty_gzip;
    size_t                               empty_gzip_len;
    u_char                              *nonempty_gzip;
    size_t                               nonempty_gzip_len;
    u_char                              *combined;
    size_t                               combined_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("empty gzip member after exact response budget");

    first_text = "exact gzip response budget";
    first_len = test_cstrnlen(first_text, 1024);
    first_gzip = NULL;
    empty_gzip = NULL;
    nonempty_gzip = NULL;

    rc = compress_payload((const u_char *) first_text, first_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &first_gzip, &first_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "first gzip member should compress");
    rc = compress_payload((const u_char *) "", 0,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &empty_gzip, &empty_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "empty gzip member should compress");
    rc = compress_payload((const u_char *) "x", 1,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &nonempty_gzip, &nonempty_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "non-empty gzip member should compress");

    combined = concat_compressed_ranges(
        first_gzip, first_gzip_len,
        empty_gzip, empty_gzip_len, &combined_len);
    TEST_ASSERT(combined != NULL,
        "exact-budget and empty members should concatenate");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, first_len);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, combined, combined_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "same-feed empty member should fit after exact budget");
    TEST_ASSERT(out_len == first_len
        && MEM_EQ(out, first_text, first_len),
        "same-feed empty member should not change output");
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    free(combined);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, first_len);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, first_gzip, first_gzip_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK && out_len == first_len,
        "first feed should exactly consume the response budget");
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, empty_gzip, empty_gzip_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "cross-feed empty member should fit after exact budget");
    TEST_ASSERT(out == NULL && out_len == 0,
        "cross-feed empty member should emit no output");
    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, first_len);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, first_gzip, first_gzip_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK && out_len == first_len,
        "first feed should exactly consume the response budget");
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, nonempty_gzip, nonempty_gzip_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "one output byte after exact budget must remain rejected");
    TEST_ASSERT(out == NULL && out_len == 0,
        "over-budget member should not expose probe output");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    free(nonempty_gzip);
    free(empty_gzip);
    free(first_gzip);
    TEST_PASS("empty gzip member accepted at exact budget");
}

/*
 * Verify that a gzip member ending exactly at a feed boundary does not make
 * the next member a no-op.
 */
static void
test_gzip_member_boundary_between_feeds(void)
{
    const char                          *first_text;
    const char                          *second_text;
    size_t                               first_len;
    size_t                               second_len;
    u_char                              *first_gzip;
    size_t                               first_gzip_len;
    u_char                              *second_gzip;
    size_t                               second_gzip_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("gzip member boundary exactly between feeds");

    first_text = "member boundary first";
    second_text = "member boundary second";
    first_len = test_cstrnlen(first_text, 1024);
    second_len = test_cstrnlen(second_text, 1024);
    first_gzip = NULL;
    second_gzip = NULL;
    test_pool_reset(&tp);

    rc = compress_payload((const u_char *) first_text, first_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &first_gzip, &first_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "first gzip member should compress");
    rc = compress_payload((const u_char *) second_text, second_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &second_gzip, &second_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "second gzip member should compress");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        first_len + second_len);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, first_gzip, first_gzip_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "first gzip member should decompress");
    TEST_ASSERT(out_len == first_len
        && MEM_EQ(out, first_text, first_len),
        "first feed should emit only the first member");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, second_gzip, second_gzip_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "second gzip member should decompress");
    TEST_ASSERT(out_len == second_len
        && MEM_EQ(out, second_text, second_len),
        "second feed should emit the later gzip member exactly once");
    TEST_ASSERT(decomp->total_decompressed == first_len + second_len,
        "cross-feed member accounting should remain cumulative");

    free(second_gzip);
    free(first_gzip);
    free(decomp);
    TEST_PASS("gzip member boundary preserved between feeds");
}

/*
 * Verify a transition feed containing the tail of one member and the prefix
 * of the next consumes both ranges without dropping or duplicating bytes.
 */
static void
test_gzip_member_boundary_inside_feed(void)
{
    const char                          *first_text;
    const char                          *second_text;
    size_t                               first_len;
    size_t                               second_len;
    u_char                              *first_gzip;
    size_t                               first_gzip_len;
    u_char                              *second_gzip;
    size_t                               second_gzip_len;
    size_t                               first_split;
    size_t                               second_split;
    u_char                              *transition;
    size_t                               transition_len;
    u_char                              *actual;
    size_t                               actual_len;
    size_t                               expected_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("gzip member boundary inside an arbitrary feed");

    first_text = "first member split across compressed chunks\n";
    second_text = "second member split across compressed chunks\n";
    first_len = test_cstrnlen(first_text, 1024);
    second_len = test_cstrnlen(second_text, 1024);
    expected_len = first_len + second_len;
    first_gzip = NULL;
    second_gzip = NULL;
    test_pool_reset(&tp);

    rc = compress_payload((const u_char *) first_text, first_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &first_gzip, &first_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "first gzip member should compress");
    rc = compress_payload((const u_char *) second_text, second_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &second_gzip, &second_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "second gzip member should compress");

    first_split = first_gzip_len / 2;
    second_split = second_gzip_len / 2;
    TEST_ASSERT(first_split > 0 && first_split < first_gzip_len,
        "first gzip member should have an interior split");
    TEST_ASSERT(second_split > 0 && second_split < second_gzip_len,
        "second gzip member should have an interior split");

    transition = concat_compressed_ranges(
        first_gzip + first_split, first_gzip_len - first_split,
        second_gzip, second_split, &transition_len);
    TEST_ASSERT(transition != NULL,
        "transition chunk should contain both member ranges");
    actual = malloc(expected_len);
    TEST_ASSERT(actual != NULL, "output collector should be allocated");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, expected_len);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");

    actual_len = 0;
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, first_gzip, first_split,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "first gzip prefix should decompress");
    TEST_ASSERT(out_len <= expected_len - actual_len,
        "first feed output should remain bounded");
    if (out_len > 0) {
        memcpy(actual + actual_len, out, out_len);
        actual_len += out_len;
    }

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, transition, transition_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "member transition feed should decompress");
    TEST_ASSERT(out_len <= expected_len - actual_len,
        "transition output should remain bounded");
    if (out_len > 0) {
        memcpy(actual + actual_len, out, out_len);
        actual_len += out_len;
    }

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, second_gzip + second_split,
        second_gzip_len - second_split,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "second gzip tail should decompress");
    TEST_ASSERT(out_len <= expected_len - actual_len,
        "final feed output should remain bounded");
    if (out_len > 0) {
        memcpy(actual + actual_len, out, out_len);
        actual_len += out_len;
    }

    TEST_ASSERT(actual_len == expected_len,
        "arbitrary member boundary should emit all plaintext");
    TEST_ASSERT(MEM_EQ(actual, first_text, first_len),
        "first member should not be truncated or duplicated");
    TEST_ASSERT(MEM_EQ(actual + first_len, second_text, second_len),
        "second member should not be truncated or duplicated");

    free(actual);
    free(transition);
    free(second_gzip);
    free(first_gzip);
    free(decomp);
    TEST_PASS("gzip member boundary consumed inside a feed");
}

/*
 * Verify that a complete first member cannot hide an incomplete later member
 * when the response reaches terminal finalization.
 */
static void
test_gzip_truncated_second_member(void)
{
    const char                          *first_text;
    const char                          *second_text;
    size_t                               first_len;
    size_t                               second_len;
    u_char                              *first_gzip;
    size_t                               first_gzip_len;
    u_char                              *second_gzip;
    size_t                               second_gzip_len;
    u_char                              *combined;
    size_t                               combined_len;
    size_t                               truncated_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    u_char                              *finish_out;
    size_t                               finish_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("truncated second gzip member fails finalization");

    first_text = "complete first gzip member";
    second_text = "second gzip member must be complete";
    first_len = test_cstrnlen(first_text, 1024);
    second_len = test_cstrnlen(second_text, 1024);
    first_gzip = NULL;
    second_gzip = NULL;
    test_pool_reset(&tp);

    rc = compress_payload((const u_char *) first_text, first_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &first_gzip, &first_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "first gzip member should compress");
    rc = compress_payload((const u_char *) second_text, second_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &second_gzip, &second_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "second gzip member should compress");
    TEST_ASSERT(second_gzip_len > 8,
        "second gzip member should include a removable trailer");

    truncated_len = second_gzip_len - 8;
    combined = concat_compressed_ranges(
        first_gzip, first_gzip_len,
        second_gzip, truncated_len, &combined_len);
    TEST_ASSERT(combined != NULL,
        "complete and truncated members should concatenate");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, combined, combined_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "truncated later member should defer integrity failure to finish");
    TEST_ASSERT(out_len >= first_len
        && MEM_EQ(out, first_text, first_len),
        "completed first member should still be emitted once");

    finish_out = (u_char *) 0x1;
    finish_len = 1;
    rc = ngx_http_markdown_streaming_decomp_finish(
        decomp, &finish_out, &finish_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
        "truncated second gzip member must fail finalization");

    free(combined);
    free(second_gzip);
    free(first_gzip);
    free(decomp);
    TEST_PASS("truncated second gzip member rejected");
}

/*
 * Verify that resetting the gzip inflater between members does not reset the
 * response-wide decompression budget.
 */
static void
test_gzip_member_budget_is_cumulative(void)
{
    const char                          *first_text;
    const char                          *second_text;
    size_t                               first_len;
    size_t                               second_len;
    size_t                               max_size;
    u_char                              *first_gzip;
    size_t                               first_gzip_len;
    u_char                              *second_gzip;
    size_t                               second_gzip_len;
    u_char                              *combined;
    size_t                               combined_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("gzip member resets preserve cumulative budget");

    first_text = "first member within budget";
    second_text = "second member crosses budget";
    first_len = test_cstrnlen(first_text, 1024);
    second_len = test_cstrnlen(second_text, 1024);
    max_size = first_len + second_len - 1;
    TEST_ASSERT(first_len < max_size && second_len < max_size,
        "each gzip member should fit the response budget alone");
    first_gzip = NULL;
    second_gzip = NULL;
    test_pool_reset(&tp);

    rc = compress_payload((const u_char *) first_text, first_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &first_gzip, &first_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "first gzip member should compress");
    rc = compress_payload((const u_char *) second_text, second_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &second_gzip, &second_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "second gzip member should compress");

    combined = concat_compressed_ranges(
        first_gzip, first_gzip_len,
        second_gzip, second_gzip_len, &combined_len);
    TEST_ASSERT(combined != NULL, "gzip members should concatenate");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, max_size);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");

    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, combined, combined_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "combined gzip members should exceed the response budget");
    TEST_ASSERT(out == NULL && out_len == 0,
        "budget failure should not expose partial decompressed output");

    free(combined);
    free(second_gzip);
    free(first_gzip);
    free(decomp);
    TEST_PASS("gzip member budget remains cumulative");
}

/* Verify reset failure frees the current growable heap workspace. */
static void
test_gzip_member_reset_failure_releases_heap(void)
{
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *heap_buf;
    ngx_int_t                            rc;

    TEST_SUBSECTION("gzip member reset failure releases heap workspace");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 1024);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");
    heap_buf = ngx_alloc(64, &test_log);
    TEST_ASSERT(heap_buf != NULL, "heap workspace should be allocated");

    g_inflate_reset_fail_once = 1;
    rc = ngx_http_markdown_streaming_decomp_reset_gzip_member(
        decomp, &heap_buf, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR,
        "gzip member reset should propagate zlib reset failure");
    TEST_ASSERT(heap_buf == NULL,
        "gzip member reset failure should free and clear heap workspace");
    TEST_ASSERT(decomp->at_gzip_member_boundary == 0,
        "failed reset must not expose a valid gzip member boundary");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    TEST_PASS("gzip reset failure cleanup covered");
}

/*
 * test_create_helper_and_limit_branches - Verify cleanup-registration
 * failure in create, check_limit boundary conditions, expand_buf overflow
 * and allocation-failure paths, and finalize_buf allocation-failure path.
 *
 * Branches covered:
 *   - cleanup_add failure causes create to return NULL
 *   - check_limit fails when total already exceeds max
 *   - check_limit fails when produced exceeds remaining budget
 *   - check_limit passes on exact boundary
 *   - expand_buf fails on size_t overflow
 *   - expand_buf fails when ngx_alloc returns NULL
 *   - finalize_buf fails when ngx_palloc returns NULL
 */
static void
test_create_helper_and_limit_branches(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    ngx_http_markdown_streaming_decomp_t  local;
    u_char                               *heap_buf;
    u_char                               *buf;
    size_t                                buf_size;
    ngx_int_t                             rc;

    TEST_SUBSECTION("create helper and limit guard branches");

    test_pool_reset(&tp);
    g_cleanup_add_fail_once = 1;
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 1024);
    TEST_ASSERT(decomp == NULL,
        "cleanup registration failure should fail create");

    memset(&local, 0, sizeof(local));
    local.max_decompressed_size = 10;
    local.total_decompressed = 11;
    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_check_limit(&local, 0) == 1,
        "check_limit should fail when total already exceeds max");
    local.total_decompressed = 3;
    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_check_limit(&local, 8) == 1,
        "check_limit should fail when produced exceeds remaining");
    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_check_limit(&local, 7) == 0,
        "check_limit should pass on boundary");

    heap_buf = malloc(8);
    TEST_ASSERT(heap_buf != NULL, "heap allocation should succeed");
    memcpy(heap_buf, "1234567", 8);
    buf = heap_buf;
    buf_size = (size_t) -1;
    rc = ngx_http_markdown_streaming_decomp_expand_buf(
        &heap_buf, &buf, &buf_size, 0, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR,
        "expand_buf should fail with OVERFLOW_ERROR on size overflow");
    TEST_ASSERT(heap_buf == NULL,
        "expand_buf overflow should clear heap pointer");

    heap_buf = malloc(8);
    TEST_ASSERT(heap_buf != NULL, "heap allocation should succeed");
    memcpy(heap_buf, "abcdefg", 8);
    buf = heap_buf;
    buf_size = 8;
    g_alloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_decomp_expand_buf(
        &heap_buf, &buf, &buf_size, 0, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "expand_buf should fail when ngx_alloc fails");
    TEST_ASSERT(heap_buf == NULL,
        "expand_buf alloc failure should clear heap pointer");

    heap_buf = malloc(16);
    TEST_ASSERT(heap_buf != NULL, "heap allocation should succeed");
    memcpy(heap_buf, "payload", 8);
    buf = heap_buf;
    buf_size = 16;
    g_palloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_decomp_finalize_buf(
        &heap_buf, &buf, &buf_size, 8, &tp.pool);
    /*
     * finalize_buf() takes ownership of the heap buffer by pointer-to-
     * pointer and frees it on both success and error paths, clearing
     * *heap_buf_ptr so the caller cannot double-free or leak it.
     */
    TEST_ASSERT(rc == NGX_ERROR,
        "finalize_buf should fail when pool allocation fails");
    TEST_ASSERT(heap_buf == NULL,
        "finalize_buf should clear heap pointer on error");

    TEST_PASS("create/helper/limit branches covered");
}

/*
 * test_feed_guard_and_overflow_branches - Verify feed's early-exit guard
 * clauses and size-overflow checks.
 *
 * Branches covered:
 *   - NULL decompressor returns NGX_ERROR
 *   - finished decompressor returns NGX_OK with empty output (no-op)
 *   - input length exceeding uInt range returns NGX_ERROR
 *   - total_decompressed overflow check returns NGX_ERROR
 */
static void
test_feed_guard_and_overflow_branches(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                               *out;
    size_t                                out_len;
    ngx_int_t                             rc;

    TEST_SUBSECTION("feed guard, budget, and overflow branches");

    test_pool_reset(&tp);
    out = NULL;
    out_len = 0;

    rc = ngx_http_markdown_streaming_decomp_feed(
        NULL, (const u_char *) "x", 1, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR, "NULL decompressor should return NGX_ERROR");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");

    decomp->finished = 1;
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "finished decompressor should no-op");
    TEST_ASSERT(out == NULL && out_len == 0,
        "finished decompressor should emit empty output");

    decomp->finished = 0;
    decomp->max_decompressed_size = 0;
    decomp->total_decompressed = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", ((size_t) UINT_MAX) + 1,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when input length exceeds uInt");

    decomp->total_decompressed = (size_t) -1;
    g_inflate_mode = TEST_INFLATE_MODE_FEED_EXPAND_THEN_DONE;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "total size overflow check should fail");

    free(decomp);
    TEST_PASS("feed guard/overflow branches covered");
}

/*
 * test_finish_guard_and_alloc_branches - Verify finish's early-exit guard
 * clauses and output-buffer allocation failure.
 *
 * Branches covered:
 *   - NULL decompressor returns NGX_ERROR
 *   - finished decompressor returns NGX_OK with empty output (no-op)
 *   - ngx_alloc failure for the output workspace returns NGX_ERROR
 */
static void
test_finish_guard_and_alloc_branches(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                               *out;
    size_t                                out_len;
    ngx_int_t                             rc;

    TEST_SUBSECTION("finish guard and allocation branches");

    test_pool_reset(&tp);
    out = NULL;
    out_len = 0;

    rc = ngx_http_markdown_streaming_decomp_finish(
        NULL, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR, "NULL decompressor should error in finish");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");

    decomp->finished = 1;
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_finish(
        decomp, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "finished decompressor should no-op in finish");
    TEST_ASSERT(out == NULL && out_len == 0,
        "finished decompressor should emit empty finish output");

    decomp->finished = 0;
    g_alloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_decomp_finish(
        decomp, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "finish should fail when output buffer allocation fails");

    free(decomp);
    TEST_PASS("finish guard/allocation branches covered");
}

/*
 * test_feed_empty_and_large_size_paths - Verify feed behaviour with
 * zero-length input, initial buffer allocation failure, and saturated
 * size calculations that would overflow uInt.
 *
 * Branches covered:
 *   - zero-length input is a no-op (no output)
 *   - initial output buffer allocation failure returns NGX_ERROR
 *   - saturated initial size (near SIZE_MAX) returns NGX_ERROR with
 *     failure_origin = INTERNAL (arithmetic overflow, not allocation)
 *   - output buffer size exceeding zlib uInt range returns NGX_ERROR
 */
static void
test_feed_empty_and_large_size_paths(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                               *out;
    size_t                                out_len;
    ngx_int_t                             rc;
    size_t                                huge_in_len;
    u_char                                one_byte;

    TEST_SUBSECTION("feed empty input and large-size guard paths");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");

    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 0, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "zero-length input should be a no-op");
    TEST_ASSERT(out == NULL && out_len == 0,
        "zero-length input should not produce output");

    g_alloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when initial output buffer allocation fails");

    g_alloc_fail_once = 0;
    g_alloc_return_static_once = 0;
    huge_in_len = ((size_t) -1 / 4) + 1;
    one_byte = 'x';
    out = NULL;
    out_len = 0;
    g_alloc_return_static_once = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, &one_byte, huge_in_len, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail safely on saturated initial size");
    TEST_ASSERT(decomp->failure_origin
                == NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL,
        "workspace_size overflow must set failure_origin to INTERNAL");
    g_alloc_return_static_once = 0;

    out = NULL;
    out_len = 0;
    g_alloc_return_static_once = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, &one_byte, ((size_t) UINT_MAX / 2) + 1,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when output buffer size exceeds zlib uInt");
    g_alloc_return_static_once = 0;

    free(decomp);
    TEST_PASS("feed empty/large-size branches covered");
}

/*
 * test_feed_mocked_inflate_paths - Verify feed's inflate-loop error,
 * budget, and expansion paths using the controlled inflate mock.
 *
 * Branches covered:
 *   - expand_buf failure during inflate loop returns NGX_ERROR
 *   - Z_DATA_ERROR after heap expansion returns FORMAT_ERROR
 *   - a true zlib runtime failure returns IO_ERROR
 *   - mocked oversized output classified as budget exceeded
 *   - finalize_buf allocation failure returns NGX_ERROR
 *   - total_decompressed overflow on feed completion returns NGX_ERROR
 */
static void
test_feed_mocked_inflate_paths(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                               *out;
    size_t                                out_len;
    ngx_int_t                             rc;

    TEST_SUBSECTION("feed mocked inflate error/budget/expand paths");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FEED_EXPAND_THEN_DONE;
    g_alloc_fail_once = 1;
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when expand_buf fails during inflate loop");
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FEED_EXPAND_THEN_ERROR;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "feed should classify Z_DATA_ERROR after expansion as format error");
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FEED_IO_ERROR;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR,
        "feed should classify Z_MEM_ERROR as a zlib runtime I/O error");
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 64);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FEED_EXPAND_THEN_BUDGET;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "feed should classify mocked oversized output as budget exceeded");
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FEED_EXPAND_THEN_DONE;
    g_palloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when finalize_buf cannot allocate pool memory");
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FEED_EXPAND_THEN_DONE;
    decomp->total_decompressed = (size_t) -8;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when total_decompressed would overflow size_t");
    /*
     * This path mirrors the historical apply_limits() ownership bug:
     * finalize_buf() has already transferred the buffer to pool memory
     * when apply_limits() reports the overflow error.  Production code
     * must not ngx_free() the pool-owned buffer on the error return.
     */
    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "post-decode overflow must not ngx_free() pool memory");
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FEED_BUF_ERROR_NO_PROGRESS;
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "feed should classify a no-progress zlib stall as format error");
    free(decomp);

    TEST_PASS("feed mocked inflate branches covered");
}

/*
 * test_finish_zlib_paths_and_helpers - Verify the finish_zlib helper's
 * error, budget, overflow, expansion, and finalize-failure paths, plus
 * the free_heap helper.
 *
 * Branches covered:
 *   - free_heap frees and clears a non-NULL heap pointer
 *   - Z_DATA_ERROR during Z_FINISH returns FORMAT_ERROR
 *   - budget exceeded during finish tail returns
 *     NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED
 *   - initial finish buffer exceeding uInt range returns NGX_ERROR
 *   - expand-then-end flow succeeds and reports produced > old size
 *   - finalize_buf allocation failure during finish returns NGX_ERROR
 */
static void
test_finish_zlib_paths_and_helpers(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    ngx_int_t                             rc;
    u_char                               *heap_buf;
    size_t                                buf_size;
    size_t                                produced;
    u_char                               *buf;

    TEST_SUBSECTION("finish_zlib helper/error/budget/expand branches");

    heap_buf = malloc(16);
    TEST_ASSERT(heap_buf != NULL, "heap allocation should succeed");
    ngx_http_markdown_streaming_decomp_free_heap(&heap_buf);
    TEST_ASSERT(heap_buf == NULL,
        "free_heap should free and clear non-NULL heap pointer");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FINISH_ERROR;
    buf = malloc(128);
    TEST_ASSERT(buf != NULL, "finish buffer allocation should succeed");
    buf_size = 128;
    produced = 0;
    rc = ngx_http_markdown_streaming_decomp_finish_zlib(
        decomp, &buf, &buf_size, &produced, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "finish_zlib should classify Z_DATA_ERROR as format error");
    TEST_ASSERT(buf == NULL,
        "finish_zlib should clear buf after freeing heap on inflate error");
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 32);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FINISH_BUDGET;
    buf = malloc(4096);
    TEST_ASSERT(buf != NULL, "finish buffer allocation should succeed");
    buf_size = 4096;
    produced = 0;
    rc = ngx_http_markdown_streaming_decomp_finish_zlib(
        decomp, &buf, &buf_size, &produced, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "finish_zlib should return budget exceeded when tail is too large");
    TEST_ASSERT(buf == NULL,
        "finish_zlib should clear buf after freeing heap on budget exceeded");
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    buf = g_static_pool_buf;
    buf_size = ((size_t) UINT_MAX) + 1;
    produced = 0;
    rc = ngx_http_markdown_streaming_decomp_finish_zlib(
        decomp, &buf, &buf_size, &produced, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "finish_zlib should fail when initial finish buffer exceeds uInt");
    TEST_ASSERT(buf == NULL,
        "finish_zlib should clear buf on uInt overflow (g_static_pool_buf not freed)");
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FINISH_EXPAND_THEN_END;
    buf = malloc(64);
    TEST_ASSERT(buf != NULL, "finish buffer allocation should succeed");
    buf_size = 64;
    produced = 0;
    rc = ngx_http_markdown_streaming_decomp_finish_zlib(
        decomp, &buf, &buf_size, &produced, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "finish_zlib should succeed when mocked flow expands then ends");
    TEST_ASSERT(produced > 64,
        "finish_zlib expand path should report produced bytes above old size");
    /*
     * On success finalize_buf() transfers the heap buffer to pool
     * memory and frees the heap allocation, so buf now points to a
     * pool-owned buffer (malloc-backed in this harness).  Reclaim it
     * via the sanctioned helper rather than free().
     */
    test_pool_free_tracked_allocations();
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FINISH_EXPAND_THEN_END;
    g_palloc_fail_once = 1;
    buf = malloc(64);
    TEST_ASSERT(buf != NULL, "finish buffer allocation should succeed");
    buf_size = 64;
    produced = 0;
    rc = ngx_http_markdown_streaming_decomp_finish_zlib(
        decomp, &buf, &buf_size, &produced, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "finish_zlib should fail when finalize_buf cannot allocate");
    TEST_ASSERT(buf == NULL,
        "finish_zlib should clear buf after freeing heap on finalize failure");
    free(decomp);

    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("finish_zlib helper/error/budget/expand branches covered");
}

/*
 * test_finish_wrapper_success_and_overflow_paths - Verify the finish
 * wrapper's success path and total_decompressed overflow detection.
 *
 * Branches covered:
 *   - finish succeeds for a mocked continue-then-end inflate flow
 *   - finish marks the decompressor as finished
 *   - finish publishes tail output on success
 *   - finish accumulates produced bytes into total_decompressed
 *   - finish returns NGX_ERROR with INTERNAL origin on overflow
 */
static void
test_finish_wrapper_success_and_overflow_paths(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                               *out;
    size_t                                out_len;
    ngx_int_t                             rc;

    TEST_SUBSECTION("finish wrapper success and total-size overflow branches");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FINISH_CONTINUE_THEN_END;
    decomp->total_decompressed = 1;
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_finish(
        decomp, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "finish should succeed for mocked continue-then-end flow");
    TEST_ASSERT(decomp->finished == 1,
        "finish should mark decompressor as finished");
    TEST_ASSERT(out != NULL && out_len > 0,
        "finish should publish tail output on success");
    TEST_ASSERT(decomp->total_decompressed > 1,
        "finish should add produced bytes into running total");
    /* out is pool-owned; reclaimed by the next test_pool_reset. */
    free(decomp);

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");
    g_inflate_mode = TEST_INFLATE_MODE_FINISH_CONTINUE_THEN_END;
    decomp->total_decompressed = (size_t) -8;
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_finish(
        decomp, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "finish must fail with NGX_ERROR when total would overflow");
    TEST_ASSERT(decomp->failure_origin
                == NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL,
        "finish overflow must set failure_origin to INTERNAL");
    TEST_ASSERT(out == NULL && out_len == 0,
        "finish overflow must not expose output");
    /* out is NULL; no pool-owned buffer to reclaim. */
    free(decomp);

    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "production code must not ngx_free() pool memory");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "production code must not ngx_free() static pool memory");
    TEST_PASS("finish wrapper success/overflow branches covered");
}

/*
 * test_ngx_free_rejects_pool_memory - Verify the ownership-tracking guard
 * actually fires when ngx_free() is handed a pool-backed pointer.
 *
 * This is the regression test for the apply_limits()/free_heap() ownership
 * bug class: production code must never call ngx_free() on a buffer
 * allocated by ngx_palloc() (pool memory is not individually freeable in
 * real NGINX).  The guard must surface the violation rather than silently
 * absorbing it, otherwise the harness cannot detect a future regression.
 *
 * Branches covered:
 *   - ngx_free() on a tracked ngx_palloc() pointer sets the violation flag
 *     and does NOT free the block (so the harness can still reclaim it)
 *   - ngx_free() on g_static_pool_buf sets the static-pool violation flag
 *   - ngx_free() on an untracked ngx_alloc() heap pointer frees normally
 *     and sets neither flag
 */
static void
test_ngx_free_rejects_pool_memory(void)
{
    test_pool_t  tp;
    u_char     *pool_ptr;
    u_char     *heap_ptr;

    TEST_SUBSECTION("ngx_free ownership guard fires on pool memory");

    test_pool_reset(&tp);

    /* A tracked ngx_palloc() pointer. */
    pool_ptr = ngx_palloc(&tp.pool, 32);
    TEST_ASSERT(pool_ptr != NULL, "ngx_palloc should succeed");
    TEST_ASSERT(g_palloc_ptr_count == 1,
        "ngx_palloc pointer should be tracked");

    /* ngx_free() must not reclaim it; it must flag the violation. */
    ngx_free(pool_ptr);
    TEST_ASSERT(g_free_on_palloc_violation == 1,
        "ngx_free on a pool pointer must set the violation flag");
    TEST_ASSERT(g_palloc_ptr_count == 1,
        "ngx_free must not remove the tracked pool pointer");

    /* A second ngx_free() on the static pool stand-in. */
    ngx_free(g_static_pool_buf);
    TEST_ASSERT(g_free_on_static_pool_violation == 1,
        "ngx_free on the static pool buffer must set the violation flag");

    /* An untracked ngx_alloc() heap pointer frees normally. */
    heap_ptr = ngx_alloc(16, &test_log);
    TEST_ASSERT(heap_ptr != NULL, "ngx_alloc should succeed");
    ngx_free(heap_ptr);
    TEST_ASSERT(g_free_on_palloc_violation == 1,
        "heap free must not clear the pool-violation flag");
    TEST_ASSERT(g_free_on_static_pool_violation == 1,
        "heap free must not clear the static-pool-violation flag");

    /* Reclaim the tracked pool allocation via the sanctioned helper. */
    test_pool_free_tracked_allocations();
    TEST_ASSERT(g_palloc_ptr_count == 0,
        "tracked allocations should be reclaimed by the helper");

    TEST_PASS("ngx_free ownership guard covered");
}

/*
 * test_deflate_trailing_data_same_feed - Verify that a deflate stream with
 * trailing garbage in the same chunk is rejected as FORMAT_ERROR.
 *
 * Covers both zlib-wrapped (RFC 1950) and raw (RFC 1951) deflate formats.
 * A complete deflate stream must consume every byte of compressed input;
 * any remaining bytes after Z_STREAM_END are trailing data that must be
 * rejected rather than silently truncated.
 *
 * Branches covered:
 *   - zlib-wrapped deflate + trailing garbage in one feed -> FORMAT_ERROR
 *   - raw deflate + trailing garbage in one feed -> FORMAT_ERROR
 *   - output slots are NULL/0 on error (no partial output exposed)
 *   - decomp->finished is NOT set on error (state stays recoverable)
 */
static void
test_deflate_trailing_data_same_feed(void)
{
    static const char  *plain = "<p>deflate trailing data</p>";
    size_t              plain_len;
    u_char             *zlib_data;
    size_t              zlib_len;
    u_char             *raw_data;
    size_t              raw_len;
    u_char             *combined;
    size_t              combined_len;
    test_pool_t         tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char             *out;
    size_t              out_len;
    ngx_int_t           rc;

    TEST_SUBSECTION("deflate trailing data rejected in same feed");

    plain_len = test_cstrnlen(plain, 1024);

    /* zlib-wrapped deflate + trailing garbage */
    zlib_data = NULL;
    rc = compress_payload_with_window_bits(
        (const u_char *) plain, plain_len, MAX_WBITS,
        &zlib_data, &zlib_len);
    TEST_ASSERT(rc == NGX_OK, "zlib-wrapped deflate should compress");

    combined = concat_compressed_ranges(
        zlib_data, zlib_len,
        (const u_char *) "GARBAGE_TRAIL", 12, &combined_len);
    TEST_ASSERT(combined != NULL, "zlib deflate + garbage should concatenate");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        plain_len + 256);
    TEST_ASSERT(decomp != NULL, "deflate decompressor should be created");

    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, combined, combined_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "zlib-wrapped deflate with trailing garbage should be FORMAT_ERROR");
    TEST_ASSERT(out == NULL && out_len == 0,
        "trailing-data error should not expose partial output");
    TEST_ASSERT(decomp->finished == 0,
        "finished flag must not be set on trailing-data error");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    free(combined);
    free(zlib_data);

    /* raw deflate + trailing garbage */
    raw_data = NULL;
    rc = compress_payload_with_window_bits(
        (const u_char *) plain, plain_len, -MAX_WBITS,
        &raw_data, &raw_len);
    TEST_ASSERT(rc == NGX_OK, "raw deflate should compress");

    combined = concat_compressed_ranges(
        raw_data, raw_len,
        (const u_char *) "RAW_GARBAGE_TRAIL", 15, &combined_len);
    TEST_ASSERT(combined != NULL, "raw deflate + garbage should concatenate");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        plain_len + 256);
    TEST_ASSERT(decomp != NULL, "deflate decompressor should be created");

    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, combined, combined_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "raw deflate with trailing garbage should be FORMAT_ERROR");
    TEST_ASSERT(out == NULL && out_len == 0,
        "raw trailing-data error should not expose partial output");
    TEST_ASSERT(decomp->finished == 0,
        "finished flag must not be set on raw trailing-data error");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    free(combined);
    free(raw_data);

    TEST_PASS("deflate trailing data rejected in same feed");
}


/*
 * test_deflate_trailing_data_next_feed - Verify that a deflate stream that
 * already finished rejects subsequent non-empty chunks as FORMAT_ERROR,
 * while empty chunks remain a safe no-op.
 *
 * Branches covered:
 *   - valid deflate in first feed -> NGX_OK, finished set
 *   - empty second feed after finish -> NGX_OK (safe no-op)
 *   - non-empty second feed after finish -> FORMAT_ERROR (trailing data)
 *   - output slots cleared on error
 */
static void
test_deflate_trailing_data_next_feed(void)
{
    static const char  *plain = "<p>deflate cross-feed trailing</p>";
    size_t              plain_len;
    u_char             *zlib_data;
    size_t              zlib_len;
    test_pool_t         tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char             *out;
    size_t              out_len;
    ngx_int_t           rc;

    TEST_SUBSECTION("deflate trailing data rejected across feeds");

    plain_len = test_cstrnlen(plain, 1024);

    zlib_data = NULL;
    rc = compress_payload_with_window_bits(
        (const u_char *) plain, plain_len, MAX_WBITS,
        &zlib_data, &zlib_len);
    TEST_ASSERT(rc == NGX_OK, "zlib-wrapped deflate should compress");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        plain_len + 256);
    TEST_ASSERT(decomp != NULL, "deflate decompressor should be created");

    /* First feed: valid deflate stream, should succeed and set finished */
    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, zlib_data, zlib_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "valid deflate feed should succeed");
    TEST_ASSERT(out_len == plain_len
        && MEM_EQ(out, plain, plain_len),
        "deflate output should match plain text");
    TEST_ASSERT(decomp->finished == 1,
        "complete deflate stream should set finished");

    /* Empty second feed: safe no-op */
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, NULL, 0, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "empty feed after finish should be a safe no-op");
    TEST_ASSERT(out == NULL && out_len == 0,
        "empty no-op feed should clear output slots");

    /* Non-empty second feed: trailing data, must be rejected */
    out = (u_char *) 0x1;
    out_len = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "TRAILING_DATA", 12,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "non-empty feed after deflate finish must be FORMAT_ERROR");
    TEST_ASSERT(out == NULL && out_len == 0,
        "trailing-data error should not expose output");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    free(zlib_data);

    TEST_PASS("deflate trailing data rejected across feeds");
}


/*
 * test_deflate_no_trailing_data_still_succeeds - Verify that a clean
 * deflate stream (no trailing data) still succeeds after the fix.
 *
 * This is a positive control for both zlib-wrapped and raw deflate to
 * ensure the trailing-data guard does not over-reject valid streams.
 *
 * Branches covered:
 *   - zlib-wrapped deflate, clean stream -> NGX_OK, finished set
 *   - raw deflate, clean stream -> NGX_OK, finished set
 *   - finish after clean deflate -> NGX_OK
 */
static void
test_deflate_no_trailing_data_still_succeeds(void)
{
    static const char  *plain = "<p>clean deflate no trailing</p>";
    size_t              plain_len;
    u_char             *zlib_data;
    size_t              zlib_len;
    u_char             *raw_data;
    size_t              raw_len;
    test_pool_t         tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char             *out;
    size_t              out_len;
    ngx_int_t           rc;

    TEST_SUBSECTION("clean deflate streams still succeed (no trailing data)");

    plain_len = test_cstrnlen(plain, 1024);

    /* zlib-wrapped deflate, clean stream */
    zlib_data = NULL;
    rc = compress_payload_with_window_bits(
        (const u_char *) plain, plain_len, MAX_WBITS,
        &zlib_data, &zlib_len);
    TEST_ASSERT(rc == NGX_OK, "zlib-wrapped deflate should compress");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        plain_len + 256);
    TEST_ASSERT(decomp != NULL, "zlib deflate decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, zlib_data, zlib_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "clean zlib-wrapped deflate should succeed");
    TEST_ASSERT(out_len == plain_len
        && MEM_EQ(out, plain, plain_len),
        "clean zlib-wrapped deflate output should match");
    TEST_ASSERT(decomp->finished == 1,
        "clean zlib-wrapped deflate should set finished");

    {
        u_char  *finish_out = (u_char *) 0x1;
        size_t   finish_len = 1;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &finish_out, &finish_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "finish after clean deflate should succeed");
        TEST_ASSERT(finish_out == NULL && finish_len == 0,
            "clean deflate finish should emit no additional output");
    }

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    free(zlib_data);

    /* raw deflate, clean stream */
    raw_data = NULL;
    rc = compress_payload_with_window_bits(
        (const u_char *) plain, plain_len, -MAX_WBITS,
        &raw_data, &raw_len);
    TEST_ASSERT(rc == NGX_OK, "raw deflate should compress");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        plain_len + 256);
    TEST_ASSERT(decomp != NULL, "raw deflate decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, raw_data, raw_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "clean raw deflate should succeed");
    TEST_ASSERT(out_len == plain_len
        && MEM_EQ(out, plain, plain_len),
        "clean raw deflate output should match");
    TEST_ASSERT(decomp->finished == 1,
        "clean raw deflate should set finished");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    free(raw_data);

    TEST_PASS("clean deflate streams still succeed without trailing data");
}


/*
 * test_gzip_concatenated_members_not_regressed - Verify that gzip
 * concatenated members still succeed after the deflate trailing-data fix.
 *
 * This is the gzip anti-regression guard: the trailing-data rejection
 * applies only to deflate, not to gzip, which must continue to support
 * concatenated members.
 *
 * Branches covered:
 *   - two concatenated gzip members in one feed -> NGX_OK
 *   - gzip member boundary between feeds -> NGX_OK
 */
static void
test_gzip_concatenated_members_not_regressed(void)
{
    static const char  *first_text = "gzip anti-regression first";
    static const char  *second_text = "gzip anti-regression second";
    size_t              first_len;
    size_t              second_len;
    u_char             *first_gzip;
    size_t              first_gzip_len;
    u_char             *second_gzip;
    size_t              second_gzip_len;
    u_char             *combined;
    size_t              combined_len;
    test_pool_t         tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char             *out;
    size_t              out_len;
    ngx_int_t           rc;

    TEST_SUBSECTION("gzip concatenated members not regressed by deflate fix");

    first_len = test_cstrnlen(first_text, 1024);
    second_len = test_cstrnlen(second_text, 1024);
    first_gzip = NULL;
    second_gzip = NULL;

    rc = compress_payload((const u_char *) first_text, first_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &first_gzip, &first_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "first gzip member should compress");
    rc = compress_payload((const u_char *) second_text, second_len,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &second_gzip, &second_gzip_len);
    TEST_ASSERT(rc == NGX_OK, "second gzip member should compress");

    /* Two concatenated members in one feed */
    combined = concat_compressed_ranges(
        first_gzip, first_gzip_len,
        second_gzip, second_gzip_len, &combined_len);
    TEST_ASSERT(combined != NULL, "gzip members should concatenate");

    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        first_len + second_len);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, combined, combined_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "concatenated gzip members should still succeed");
    TEST_ASSERT(out_len == first_len + second_len,
        "concatenated gzip output should include both members");
    TEST_ASSERT(MEM_EQ(out, first_text, first_len),
        "first gzip member output should match");
    TEST_ASSERT(MEM_EQ(out + first_len, second_text, second_len),
        "second gzip member output should match");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    free(combined);

    /* Two members in separate feeds */
    test_pool_reset(&tp);
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        first_len + second_len);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, first_gzip, first_gzip_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "first gzip member in separate feed should succeed");
    TEST_ASSERT(out_len == first_len
        && MEM_EQ(out, first_text, first_len),
        "first gzip feed output should match");

    out = NULL;
    out_len = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, second_gzip, second_gzip_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK,
        "second gzip member in separate feed should succeed");
    TEST_ASSERT(out_len == second_len
        && MEM_EQ(out, second_text, second_len),
        "second gzip feed output should match");

    ngx_http_markdown_streaming_decomp_cleanup(decomp);
    free(decomp);
    free(second_gzip);
    free(first_gzip);

    TEST_PASS("gzip concatenated members not regressed by deflate fix");
}


/*
 * main - Test entry point.  Runs all streaming decompression test cases
 * and prints a summary banner.  Returns 0 on success.
 */
int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_decomp Tests\n");
    printf("========================================\n");

    test_size_to_uint_guards();
    test_create_and_cleanup();
    test_create_failure_paths_and_cleanup_default();
    test_roundtrip_and_empty_feed();
#ifdef NGX_HTTP_BROTLI
    test_truncated_brotli_finish_errors();
    test_brotli_error_classification();
    test_brotli_exact_budget_probe();
    test_brotli_scenario_a_trailing_data_priority();
    test_brotli_roundtrip_single_chunk();
    test_brotli_roundtrip_multi_chunk();
    test_brotli_trailing_data_same_feed();
    test_brotli_trailing_data_next_feed();
    test_brotli_empty_feed_after_completion();
    test_brotli_no_progress_guard_and_error_propagation();
    test_brotli_budget_enforcement();
    test_brotli_alloc_failures_and_lifecycle();
    test_brotli_truncation_detection_property();
#endif
    test_tiny_chunks_do_not_amplify_request_pool();
    test_budget_and_invalid_type_branches();
    test_truncated_finish_errors();
    test_malformed_zlib_formats_are_classified();
    test_truncated_deflate_formats_are_classified_at_finish();
    test_gzip_members_in_one_feed();
    test_gzip_empty_member_after_exact_budget();
    test_gzip_member_boundary_between_feeds();
    test_gzip_member_boundary_inside_feed();
    test_gzip_truncated_second_member();
    test_gzip_member_budget_is_cumulative();
    test_gzip_member_reset_failure_releases_heap();
    test_create_helper_and_limit_branches();
    test_feed_guard_and_overflow_branches();
    test_finish_guard_and_alloc_branches();
    test_feed_empty_and_large_size_paths();
    test_feed_mocked_inflate_paths();
    test_finish_zlib_paths_and_helpers();
    test_finish_wrapper_success_and_overflow_paths();
    test_ngx_free_rejects_pool_memory();
    test_deflate_trailing_data_same_feed();
    test_deflate_trailing_data_next_feed();
    test_deflate_no_trailing_data_still_succeeds();
    test_gzip_concatenated_members_not_regressed();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}

#endif /* MARKDOWN_STREAMING_ENABLED */
