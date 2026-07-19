/*
 * Test: brotli_budget_property
 *
 * Property-based test for Brotli cumulative budget enforcement (Property 7).
 *
 * Feature: Brotli streaming decompression
 * Property 7: Cumulative Budget Enforcement
 *
 * Coverage: executable Brotli streaming behavior
 *
 * For any Brotli-compressed payload whose decompressed size exceeds the
 * configured max_decompressed_size, the streaming decompressor SHALL:
 *   - Return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED when cumulative
 *     output first exceeds the limit.
 *   - NOT deliver any partial output from the exceeding feed call.
 *   - Maintain total_decompressed as monotonically non-decreasing
 *     across all feed calls.
 *
 * Test approach:
 *   - Generate random plaintext payloads of varying sizes (32-4096 bytes)
 *   - Compress each with BrotliEncoderCompress
 *   - Set budget strictly less than decompressed size
 *   - Feed through the streaming decompressor (single-chunk and
 *     multi-chunk variants)
 *   - Assert: BUDGET_EXCEEDED returned, no partial output on the
 *     exceeding call, total_decompressed monotonically non-decreasing
 *   - Minimum 100 iterations
 */

#include "../include/test_common.h"
#include <limits.h>
#include <zlib.h>
#include <brotli/encode.h>

#define NGX_OK 0
#define NGX_ERROR -1
#define NGX_AGAIN -2
#define NGX_DECLINED -5
#define NGX_DONE -4

#include <ngx_http_markdown_filter_module.h>

#ifndef MARKDOWN_STREAMING_ENABLED

int
main(void)
{
    printf("\n========================================\n");
    printf("brotli_budget_property Tests (SKIPPED)\n");
    printf("MARKDOWN_STREAMING_ENABLED not defined\n");
    printf("========================================\n\n");
    return 0;
}

#else /* MARKDOWN_STREAMING_ENABLED */

#ifndef NGX_HTTP_BROTLI

int
main(void)
{
    printf("\n========================================\n");
    printf("brotli_budget_property Tests (SKIPPED)\n");
    printf("NGX_HTTP_BROTLI not defined\n");
    printf("========================================\n\n");
    return 0;
}

#else /* NGX_HTTP_BROTLI */

/* ----------------------------------------------------------------
 * Minimal NGINX pool stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef struct test_pool_cleanup_s test_pool_cleanup_t;
typedef test_pool_cleanup_t ngx_pool_cleanup_t;

struct test_pool_cleanup_s {
    void                 (*handler)(void *data);
    void                  *data;
    ngx_pool_cleanup_t    *next;
};

struct ngx_pool_s {
    ngx_pool_cleanup_t    *cleanups;
};

struct ngx_log_s {
    int                    unused;
};

#define ngx_memcpy memcpy
#define NGX_MAX_SIZE_T_VALUE SIZE_MAX

static ngx_log_t test_log;

/* ----------------------------------------------------------------
 * Pool allocation tracking (same pattern as streaming_decomp_test)
 * ---------------------------------------------------------------- */

#define G_PALLOC_PTR_MAX  256
static void    *g_palloc_ptrs[G_PALLOC_PTR_MAX];
static size_t   g_palloc_ptr_count = 0;
static ngx_uint_t g_palloc_fail_once = 0;
static ngx_uint_t g_pcalloc_fail_once = 0;
static ngx_uint_t g_alloc_fail_once = 0;
static ngx_uint_t g_cleanup_add_fail_once = 0;
static ngx_uint_t g_palloc_return_static_once = 0;
static ngx_uint_t g_alloc_return_static_once = 0;
static ngx_uint_t g_free_on_palloc_violation = 0;
static ngx_uint_t g_free_on_static_pool_violation = 0;
static size_t g_palloc_bytes = 0;
static u_char g_static_pool_buf[8192];

/* inflate stubs: not exercised by Brotli-only tests but required by
 * the production impl header for deflate/gzip paths. */
static ngx_uint_t g_inflate_init_fail_once = 0;
static ngx_uint_t g_inflate_reset_fail_once = 0;

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
            g_free_on_palloc_violation = 1;
            return;
        }
    }
    free(p);
}

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

/* ----------------------------------------------------------------
 * zlib inflate stubs: redirect through real functions so
 * gzip/deflate paths in the included production header compile.
 *
 * Save function pointers to the real implementations BEFORE redefining
 * the macros so the pointer initialisers resolve to the real symbols.
 * ---------------------------------------------------------------- */

static int (*g_real_inflate_fn)(z_streamp, int) = inflate;
static int (*g_real_inflate_end_fn)(z_streamp) = inflateEnd;
static int (*g_real_inflate_reset_fn)(z_streamp) = inflateReset;

/* Forward-declare the test stubs so the macros can reference them */
int test_inflateInit2(z_streamp strm, int window_bits);
int test_inflateEnd(z_streamp strm);
int test_inflateReset(z_streamp strm);
int test_inflate(z_streamp strm, int flush);

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

/* Include the production streaming decompression implementation */
#include "../src/ngx_http_markdown_streaming_decomp_impl.h"

/* Now define the stub functions (after the include so they are not
 * subject to the macros at their definition site) */
#undef inflate
#undef inflateInit2
#undef inflateEnd
#undef inflateReset

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

int
test_inflate(z_streamp strm, int flush)
{
    return g_real_inflate_fn(strm, flush);
}

/* ----------------------------------------------------------------
 * Test pool helper
 * ---------------------------------------------------------------- */

typedef struct {
    ngx_pool_t  pool;
} test_pool_t;

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
}

/* ----------------------------------------------------------------
 * Simple PRNG for deterministic pseudo-random sequences
 * ---------------------------------------------------------------- */

static unsigned int g_prng_state = 12345;

static unsigned int
prng_next(void)
{
    /* xorshift32 */
    g_prng_state ^= g_prng_state << 13;
    g_prng_state ^= g_prng_state >> 17;
    g_prng_state ^= g_prng_state << 5;
    return g_prng_state;
}

static void
prng_seed(unsigned int seed)
{
    g_prng_state = seed ? seed : 1;
}

/* Fill buffer with pseudo-random bytes */
static void
fill_random_bytes(u_char *buf, size_t len)
{
    size_t i;

    for (i = 0; i < len; i++) {
        buf[i] = (u_char)(prng_next() & 0xFF);
    }
}

/* ----------------------------------------------------------------
 * Property 7: Cumulative Budget Enforcement
 *
 * Coverage: executable Brotli streaming behavior
 *
 * Strategy:
 *   For each iteration, generate a random plaintext payload (32-4096 bytes),
 *   compress with Brotli, and set budget < decompressed size. Feed the
 *   compressed data through the decompressor and verify:
 *     (a) BUDGET_EXCEEDED is eventually returned
 *     (b) No partial output from the exceeding feed call
 *     (c) total_decompressed is monotonically non-decreasing
 *
 *   Sub-strategy 1 (single-chunk): Feed entire compressed blob at once
 *     with budget = decompressed_size - random_offset (1..half_size).
 *
 *   Sub-strategy 2 (multi-chunk): Split compressed bytes at random
 *     boundaries, feed chunk-by-chunk, verify monotonicity across calls
 *     and BUDGET_EXCEEDED on the exceeding chunk.
 * ---------------------------------------------------------------- */

#define PROPERTY_ITERATIONS 100
#define MIN_PAYLOAD_SIZE    32
#define MAX_PAYLOAD_SIZE    4096
#define MAX_COMPRESSED_SIZE 8192

static void
test_property7_single_chunk(void)
{
    int          iter;
    u_char       plaintext[MAX_PAYLOAD_SIZE];
    uint8_t      compressed[MAX_COMPRESSED_SIZE];
    size_t       plaintext_len;
    size_t       compressed_len;
    size_t       budget;
    size_t       budget_reduction;
    test_pool_t  tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char      *out;
    size_t       out_len;
    ngx_int_t    rc;

    TEST_SUBSECTION(
        "Property 7 (single-chunk): Cumulative Budget Enforcement "
        "(100 iterations)");

    for (iter = 0; iter < PROPERTY_ITERATIONS; iter++) {
        /* Generate random payload size in [MIN_PAYLOAD_SIZE, MAX_PAYLOAD_SIZE] */
        plaintext_len = MIN_PAYLOAD_SIZE
            + (prng_next() % (MAX_PAYLOAD_SIZE - MIN_PAYLOAD_SIZE + 1));

        /* Fill with random bytes */
        fill_random_bytes(plaintext, plaintext_len);

        /* Compress with Brotli */
        compressed_len = sizeof(compressed);
        TEST_ASSERT(
            BrotliEncoderCompress(
                BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, plaintext_len, plaintext,
                &compressed_len, compressed)
            == BROTLI_TRUE,
            "brotli compression should succeed");

        /* Set budget strictly less than decompressed size.
         * Reduce by 1..half_size to get varied budget values. */
        budget_reduction = 1 + (prng_next() % (plaintext_len / 2));
        budget = plaintext_len - budget_reduction;

        /* Create decompressor with tight budget */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, budget);
        TEST_ASSERT(decomp != NULL,
            "decompressor creation should succeed");

        /* Feed entire compressed payload at once */
        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, compressed, compressed_len,
            &out, &out_len, &tp.pool, &test_log);

        /* Must return BUDGET_EXCEEDED */
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
            "single-chunk: must return BUDGET_EXCEEDED when "
            "decompressed size > budget");

        /* No partial output from the exceeding feed call */
        TEST_ASSERT(out == NULL && out_len == 0,
            "single-chunk: no partial output on budget exceed");

        /* total_decompressed must be 0 (no output delivered) */
        TEST_ASSERT(decomp->total_decompressed == 0,
            "single-chunk: total_decompressed stays 0 on single "
            "exceeding call");

        /* Cleanup */
        test_pool_free_tracked_allocations();
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "no pool-free violations in single-chunk property");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "no static-pool violations in single-chunk property");
    TEST_PASS(
        "Property 7 (single-chunk): Budget enforcement verified "
        "(100 iterations)");
}

static void
test_property7_multi_chunk(void)
{
    int          iter;
    u_char       plaintext[MAX_PAYLOAD_SIZE];
    uint8_t      compressed[MAX_COMPRESSED_SIZE];
    size_t       plaintext_len;
    size_t       compressed_len;
    size_t       budget;
    size_t       budget_reduction;
    test_pool_t  tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char      *out;
    size_t       out_len;
    ngx_int_t    rc;
    size_t       prev_total;
    size_t       offset;
    size_t       chunk_size;
    int          got_budget_exceeded;

    TEST_SUBSECTION(
        "Property 7 (multi-chunk): Cumulative Budget Enforcement "
        "(100 iterations)");

    for (iter = 0; iter < PROPERTY_ITERATIONS; iter++) {
        /* Generate random payload size in [MIN_PAYLOAD_SIZE, MAX_PAYLOAD_SIZE] */
        plaintext_len = MIN_PAYLOAD_SIZE
            + (prng_next() % (MAX_PAYLOAD_SIZE - MIN_PAYLOAD_SIZE + 1));

        /* Fill with random bytes */
        fill_random_bytes(plaintext, plaintext_len);

        /* Compress with Brotli */
        compressed_len = sizeof(compressed);
        TEST_ASSERT(
            BrotliEncoderCompress(
                BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, plaintext_len, plaintext,
                &compressed_len, compressed)
            == BROTLI_TRUE,
            "brotli compression should succeed");

        /* Set budget strictly less than decompressed size.
         * Reduce by 1..half_size for varied budgets. */
        budget_reduction = 1 + (prng_next() % (plaintext_len / 2));
        budget = plaintext_len - budget_reduction;

        /* Create decompressor with tight budget */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, budget);
        TEST_ASSERT(decomp != NULL,
            "decompressor creation should succeed");

        /* Feed compressed data in random-sized chunks */
        offset = 0;
        prev_total = 0;
        got_budget_exceeded = 0;

        while (offset < compressed_len) {
            /* Random chunk size: 1..min(64, remaining) */
            size_t remaining = compressed_len - offset;
            chunk_size = 1 + (prng_next() % (remaining < 64 ? remaining : 64));
            if (chunk_size > remaining) {
                chunk_size = remaining;
            }

            out = NULL;
            out_len = 0;
            rc = ngx_http_markdown_streaming_decomp_feed(
                decomp, compressed + offset, chunk_size,
                &out, &out_len, &tp.pool, &test_log);

            if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
                /* Budget exceeded: verify no partial output */
                TEST_ASSERT(out == NULL && out_len == 0,
                    "multi-chunk: no partial output on budget exceed");
                got_budget_exceeded = 1;
                break;
            }

            TEST_ASSERT(rc == NGX_OK,
                "multi-chunk: non-exceeding feed should return NGX_OK");

            /* Verify monotonically non-decreasing total_decompressed */
            TEST_ASSERT(decomp->total_decompressed >= prev_total,
                "multi-chunk: total_decompressed must be "
                "monotonically non-decreasing");
            prev_total = decomp->total_decompressed;

            /* Free pool allocations for next iteration */
            test_pool_free_tracked_allocations();
            offset += chunk_size;
        }

        /* Must have hit BUDGET_EXCEEDED at some point because
         * budget < decompressed size */
        TEST_ASSERT(got_budget_exceeded == 1,
            "multi-chunk: must eventually hit BUDGET_EXCEEDED");

        /* Final total_decompressed check: must be <= budget
         * (it was last observed before the exceeding call) */
        TEST_ASSERT(decomp->total_decompressed <= budget,
            "multi-chunk: total_decompressed never exceeds budget");

        /* Cleanup */
        test_pool_free_tracked_allocations();
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
    }

    TEST_ASSERT(g_free_on_palloc_violation == 0,
        "no pool-free violations in multi-chunk property");
    TEST_ASSERT(g_free_on_static_pool_violation == 0,
        "no static-pool violations in multi-chunk property");
    TEST_PASS(
        "Property 7 (multi-chunk): Budget enforcement verified "
        "(100 iterations)");
}

/* ----------------------------------------------------------------
 * main
 * ---------------------------------------------------------------- */

int
main(void)
{
    printf("\n========================================\n");
    printf("brotli_budget_property Tests\n");
    printf("Property 7: Cumulative Budget Enforcement\n");
    printf("Feature: Brotli streaming decompression\n");
    printf("========================================\n");

    prng_seed(42);

    test_property7_single_chunk();
    test_property7_multi_chunk();

    printf("\n========================================\n");
    printf("All Property 7 tests PASSED\n");
    printf("========================================\n\n");
    return 0;
}

#endif /* NGX_HTTP_BROTLI */
#endif /* MARKDOWN_STREAMING_ENABLED */
