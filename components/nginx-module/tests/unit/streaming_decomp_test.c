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
    UNUSED(pool);
    if (g_palloc_fail_once) {
        g_palloc_fail_once = 0;
        return NULL;
    }
    if (g_palloc_return_static_once) {
        g_palloc_return_static_once = 0;
        return g_static_pool_buf;
    }
    return malloc(size);
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
    return malloc(size);
}

/*
 * ngx_free - Stub for the NGINX deallocator.
 *
 * DIVERGENCE RISK: Production ngx_free may differ from libc free on
 * platforms with custom allocators; this stub simply calls free().
 * The semantic contract mirrored is: "releases memory allocated by
 * ngx_alloc or ngx_palloc stubs."
 *
 * Parameters:
 *   p - pointer to memory to free
 */
void
ngx_free(void *p)
{
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
#define inflate test_inflate
#define inflateInit2 test_inflateInit2
#define inflateEnd test_inflateEnd

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

/*
 * test_pool_reset - Zero-initialise a test pool and clear all failure-injection
 * flags and inflate mock state.  Call at the start of every test case to
 * ensure a clean slate.
 *
 * Parameters:
 *   tp - test pool to reset
 *
 * Side effects: zeroes *tp, clears all g_*_fail_once flags,
 *               resets inflate mode to real.
 */
static void
test_pool_reset(test_pool_t *tp)
{
    memset(tp, 0, sizeof(*tp));
    g_palloc_fail_once = 0;
    g_palloc_return_static_once = 0;
    g_pcalloc_fail_once = 0;
    g_alloc_fail_once = 0;
    g_cleanup_add_fail_once = 0;
    g_inflate_init_fail_once = 0;
    test_reset_inflate_mode();
}

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
compress_payload(const u_char *in, size_t in_len,
    ngx_http_markdown_compression_type_e type,
    u_char **out, size_t *out_len)
{
    z_stream  s;
    int       rc;
    int       window_bits;
    size_t    cap;
    u_char   *in_copy;

    if (in == NULL || in_len == 0 || out == NULL || out_len == NULL) {
        return NGX_ERROR;
    }

    memset(&s, 0, sizeof(s));

    if (type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP) {
        window_bits = MAX_WBITS + 16;
    } else if (type == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE) {
        window_bits = -MAX_WBITS;
    } else {
        return NGX_ERROR;
    }

    rc = deflateInit2(&s, Z_DEFAULT_COMPRESSION, Z_DEFLATED,
                      window_bits, 8, Z_DEFAULT_STRATEGY);
    if (rc != Z_OK) {
        return NGX_ERROR;
    }

    in_copy = malloc(in_len);
    if (in_copy == NULL) {
        deflateEnd(&s);
        return NGX_ERROR;
    }
    memcpy(in_copy, in, in_len);

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
 *   - inflateInit2 failure for deflate returns NULL
 *   - cleanup with NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN type clears
 *     initialized (default switch arm)
 */
static void
test_create_failure_paths_and_cleanup_default(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    ngx_http_markdown_streaming_decomp_t  local;

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
    TEST_ASSERT(decomp == NULL,
        "deflate create should fail when inflateInit2 errors");

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
        free(out);
        free(decomp);
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
 * test_truncated_finish_errors - Verify that finish returns NGX_ERROR when
 * the compressed stream was truncated (incomplete gzip trailer).
 *
 * Branches covered:
 *   - feed of a truncated payload succeeds and emits partial output
 *   - finish on an incomplete stream returns NGX_ERROR
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
        TEST_ASSERT(rc == NGX_ERROR,
            "finish should fail for an incomplete gzip stream");
    }

    free(compressed);
    free(out);
    free(decomp);
    TEST_PASS("finish error branch covered");
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
        &heap_buf, &buf, &buf_size, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "expand_buf should fail on size overflow");
    TEST_ASSERT(heap_buf == NULL,
        "expand_buf overflow should clear heap pointer");

    heap_buf = malloc(8);
    TEST_ASSERT(heap_buf != NULL, "heap allocation should succeed");
    memcpy(heap_buf, "abcdefg", 8);
    buf = heap_buf;
    buf_size = 8;
    g_alloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_decomp_expand_buf(
        &heap_buf, &buf, &buf_size, &test_log);
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
        heap_buf, &buf, &buf_size, 8, &tp.pool);
    /*
     * finalize_buf() takes ownership of heap_buf and frees it on
     * both success and error paths.
     */
    heap_buf = NULL;
    TEST_ASSERT(rc == NGX_ERROR,
        "finalize_buf should fail when pool allocation fails");

    TEST_PASS("create/helper/limit branches covered");
}

/*
 * test_feed_guard_and_overflow_branches - Verify feed's early-exit guard
 * clauses and size-overflow checks.
 *
 * Branches covered:
 *   - NULL decompressor returns NGX_ERROR
 *   - finished decompressor returns NGX_OK with empty output (no-op)
 *   - exhausted budget returns NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED
 *   - input length exceeding uInt range returns NGX_ERROR
 *   - total_decompressed overflow check returns NGX_ERROR
 */
static void
test_feed_guard_and_overflow_branches(void)
{
    test_pool_t                           tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    ngx_http_markdown_streaming_decomp_t  fake;
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
    decomp->max_decompressed_size = 16;
    decomp->total_decompressed = 16;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "exhausted budget should fail early");

    decomp->max_decompressed_size = 0;
    decomp->total_decompressed = 0;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", ((size_t) UINT_MAX) + 1,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when input length exceeds uInt");

    memset(&fake, 0, sizeof(fake));
    fake.initialized = 1;
    fake.type = NGX_HTTP_MARKDOWN_COMPRESSION_GZIP;
    fake.total_decompressed = (size_t) -1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        &fake, (const u_char *) "x", 1, &out, &out_len, &tp.pool, &test_log);
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
 *   - ngx_palloc failure for the output buffer returns NGX_ERROR
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
    g_palloc_fail_once = 1;
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
 *   - saturated initial size (near SIZE_MAX) returns NGX_ERROR
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

    g_palloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, (const u_char *) "x", 1, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when initial output buffer allocation fails");

    huge_in_len = ((size_t) -1 / 4) + 1;
    g_palloc_fail_once = 1;
    one_byte = 'x';
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, &one_byte, huge_in_len, &out, &out_len,
        &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail safely on saturated initial size");

    g_palloc_return_static_once = 1;
    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, &one_byte, ((size_t) UINT_MAX / 2) + 1,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when output buffer size exceeds zlib uInt");

    free(decomp);
    TEST_PASS("feed empty/large-size branches covered");
}

/*
 * test_feed_mocked_inflate_paths - Verify feed's inflate-loop error,
 * budget, and expansion paths using the controlled inflate mock.
 *
 * Branches covered:
 *   - expand_buf failure during inflate loop returns NGX_ERROR
 *   - inflate error after heap expansion returns NGX_ERROR
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
    TEST_ASSERT(rc == NGX_ERROR,
        "feed should fail when inflate errors after heap expansion");
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
    free(decomp);

    TEST_PASS("feed mocked inflate branches covered");
}

/*
 * test_finish_zlib_paths_and_helpers - Verify the finish_zlib helper's
 * error, budget, overflow, expansion, and finalize-failure paths, plus
 * the finish_free_heap helper.
 *
 * Branches covered:
 *   - finish_free_heap frees and clears a non-NULL heap pointer
 *   - inflate error during Z_FINISH returns NGX_ERROR
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
    ngx_http_markdown_streaming_decomp_finish_free_heap(&heap_buf);
    TEST_ASSERT(heap_buf == NULL,
        "finish helper should free and clear non-NULL heap pointer");

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
    TEST_ASSERT(rc == NGX_ERROR,
        "finish_zlib should fail on inflate error");
    free(buf);
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
    free(buf);
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
    free(buf);
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
    /*
     * finish_zlib() owns the expanded heap buffer and finalize_buf()
     * frees it on this error path, so buf is dangling here by design.
     */
    buf = NULL;
    free(decomp);

    TEST_PASS("finish_zlib helper/error/budget/expand branches covered");
}

/*
 * test_finish_wrapper_success_and_overflow_paths - Verify the finish
 * wrapper's success path and total_decompressed overflow saturation.
 *
 * Branches covered:
 *   - finish succeeds for a mocked continue-then-end inflate flow
 *   - finish marks the decompressor as finished
 *   - finish publishes tail output on success
 *   - finish accumulates produced bytes into total_decompressed
 *   - finish saturates total_decompressed to (size_t)-1 on overflow
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
    free(out);
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
    TEST_ASSERT(rc == NGX_OK,
        "finish should still succeed when total would overflow");
    TEST_ASSERT(decomp->total_decompressed == (size_t) -1,
        "finish should saturate total_decompressed on overflow");
    free(out);
    free(decomp);

    TEST_PASS("finish wrapper success/overflow branches covered");
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
    test_budget_and_invalid_type_branches();
    test_truncated_finish_errors();
    test_create_helper_and_limit_branches();
    test_feed_guard_and_overflow_branches();
    test_finish_guard_and_alloc_branches();
    test_feed_empty_and_large_size_paths();
    test_feed_mocked_inflate_paths();
    test_finish_zlib_paths_and_helpers();
    test_finish_wrapper_success_and_overflow_paths();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}

#endif /* MARKDOWN_STREAMING_ENABLED */
