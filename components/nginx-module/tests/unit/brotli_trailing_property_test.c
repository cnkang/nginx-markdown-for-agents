/*
 * Test: brotli_trailing_property
 *
 * Property-based test for Brotli trailing data rejection (Property 2).
 *
 * Feature: Brotli streaming decompression
 * Property 2: Trailing Data Rejection
 *
 * Coverage: executable Brotli streaming behavior
 *
 * The property states:
 *   For ALL valid Brotli streams followed by ANY non-empty trailing
 *   bytes, the decompressor returns FORMAT_ERROR.
 *
 * Test approach:
 *   1. Generate random plaintext data (1-4096 bytes)
 *   2. Compress it with the Brotli encoder
 *   3. Append random trailing bytes (1-128 bytes)
 *   4. Feed the concatenated buffer to the streaming decompressor
 *   5. Assert: result is NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
 *   6. Assert: no output is exposed on error
 *   7. Minimum 100 iterations
 */

#include "../include/test_common.h"
#include <limits.h>
#include <zlib.h>
#include <brotli/encode.h>
#include <brotli/decode.h>

/*
 * Define struct ngx_log_s — the nginx_stubs only forward-declare it.
 * Needed because we instantiate a ngx_log_t value (test_log).
 */
struct ngx_log_s {
    int unused;
};

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
    printf("brotli_trailing_property Tests (SKIPPED)\n");
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
    printf("brotli_trailing_property Tests (SKIPPED)\n");
    printf("NGX_HTTP_BROTLI not defined\n");
    printf("========================================\n\n");
    return 0;
}

#else /* NGX_HTTP_BROTLI */

/* ----------------------------------------------------------------
 * Minimal NGINX pool/allocation stubs for standalone compilation
 *
 * These match the interface expected by the production streaming
 * decompression implementation (ngx_http_markdown_streaming_decomp_impl.h).
 * ---------------------------------------------------------------- */

#define ngx_memcpy memcpy
#define NGX_MAX_SIZE_T_VALUE SIZE_MAX

static ngx_log_t test_log;

/* Allocation failure injection flags */
static ngx_uint_t g_palloc_fail_once = 0;
static ngx_uint_t g_pcalloc_fail_once = 0;
static ngx_uint_t g_alloc_fail_once = 0;
static ngx_uint_t g_cleanup_add_fail_once = 0;
static ngx_uint_t g_inflate_init_fail_once = 0;
static ngx_uint_t g_inflate_reset_fail_once = 0;
static ngx_uint_t g_palloc_return_static_once = 0;
static ngx_uint_t g_alloc_return_static_once = 0;
static ngx_uint_t g_free_on_palloc_violation = 0;
static ngx_uint_t g_free_on_static_pool_violation = 0;

static size_t g_palloc_bytes = 0;

#define G_PALLOC_PTR_MAX  256
static void    *g_palloc_ptrs[G_PALLOC_PTR_MAX];
static size_t   g_palloc_ptr_count = 0;

static u_char g_static_pool_buf[8192];

/* ----------------------------------------------------------------
 * ngx_palloc stub
 * ---------------------------------------------------------------- */

static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    void *p;

    UNUSED(pool);
    if (g_palloc_fail_once) {
        g_palloc_fail_once = 0;
        return NULL;
    }
    if (g_palloc_return_static_once) {
        g_palloc_return_static_once = 0;
        return g_static_pool_buf;
    }
    p = malloc(size);
    if (p != NULL) {
        g_palloc_bytes += size;
        if (g_palloc_ptr_count < G_PALLOC_PTR_MAX) {
            g_palloc_ptrs[g_palloc_ptr_count++] = p;
        }
    }
    return p;
}

/* ----------------------------------------------------------------
 * ngx_pnalloc stub (same as ngx_palloc for tests)
 * ---------------------------------------------------------------- */

static void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    return ngx_palloc(pool, size);
}

/* ----------------------------------------------------------------
 * ngx_pcalloc stub
 * ---------------------------------------------------------------- */

static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    void *p;

    UNUSED(pool);
    if (g_pcalloc_fail_once) {
        g_pcalloc_fail_once = 0;
        return NULL;
    }
    p = calloc(1, size);
    if (p != NULL) {
        g_palloc_bytes += size;
        if (g_palloc_ptr_count < G_PALLOC_PTR_MAX) {
            g_palloc_ptrs[g_palloc_ptr_count++] = p;
        }
    }
    return p;
}

/* ----------------------------------------------------------------
 * ngx_alloc stub
 * ---------------------------------------------------------------- */

static void *
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

/* ----------------------------------------------------------------
 * ngx_free stub
 * ---------------------------------------------------------------- */

static void
ngx_free(void *ptr)
{
    size_t i;

    if (ptr == NULL) {
        return;
    }
    if (ptr == g_static_pool_buf) {
        g_free_on_static_pool_violation = 1;
        return;
    }
    for (i = 0; i < g_palloc_ptr_count; i++) {
        if (g_palloc_ptrs[i] == ptr) {
            g_free_on_palloc_violation = 1;
            return;
        }
    }
    free(ptr);
}

/* ----------------------------------------------------------------
 * ngx_pfree stub (no-op for pool memory)
 * ---------------------------------------------------------------- */

static ngx_int_t
ngx_pfree(ngx_pool_t *pool, void *ptr)
{
    UNUSED(pool);
    UNUSED(ptr);
    return NGX_OK;
}

/* ----------------------------------------------------------------
 * ngx_pool_cleanup_add stub
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

static ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    ngx_pool_cleanup_t *c;

    UNUSED(size);
    if (g_cleanup_add_fail_once) {
        g_cleanup_add_fail_once = 0;
        return NULL;
    }
    c = (ngx_pool_cleanup_t *) calloc(1, sizeof(ngx_pool_cleanup_t));
    if (c == NULL) {
        return NULL;
    }
    c->next = pool->cleanups;
    pool->cleanups = c;
    return c;
}

/* ----------------------------------------------------------------
 * zlib stubs — delegate to real zlib
 * ---------------------------------------------------------------- */

static int (*g_real_inflate_fn)(z_streamp, int) = inflate;
static int (*g_real_inflate_end_fn)(z_streamp) = inflateEnd;
static int (*g_real_inflate_reset_fn)(z_streamp) = inflateReset;

static int
test_inflateInit2(z_streamp strm, int window_bits)
{
    if (g_inflate_init_fail_once) {
        g_inflate_init_fail_once = 0;
        return Z_MEM_ERROR;
    }
    return inflateInit2_(strm, window_bits, ZLIB_VERSION,
                         (int) sizeof(z_stream));
}

static int
test_inflateEnd(z_streamp strm)
{
    return g_real_inflate_end_fn(strm);
}

static int
test_inflate(z_streamp strm, int flush)
{
    return g_real_inflate_fn(strm, flush);
}

static int
test_inflateReset(z_streamp strm)
{
    if (g_inflate_reset_fail_once) {
        g_inflate_reset_fail_once = 0;
        return Z_STREAM_ERROR;
    }
    return g_real_inflate_reset_fn(strm);
}

/*
 * Redirect zlib inflate/inflateInit2/inflateEnd to the test stubs so that
 * the production implementation (included next) calls our versions.
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

/* Include the production streaming decompression implementation */
#include "../src/ngx_http_markdown_streaming_decomp_impl.h"

/* ----------------------------------------------------------------
 * Test pool infrastructure
 * ---------------------------------------------------------------- */

typedef struct {
    ngx_pool_t            pool;
} test_pool_t;

static void
test_pool_free_tracked_allocations(void)
{
    size_t i;

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

/* Fill buffer with random bytes */
static void
fill_random_bytes(u_char *buf, size_t len)
{
    size_t i;

    for (i = 0; i < len; i++) {
        buf[i] = (u_char)(prng_next() & 0xFF);
    }
}

/* ----------------------------------------------------------------
 * Property 2: Trailing Data Rejection (same-feed)
 *
 * For ALL valid Brotli streams followed by ANY non-empty trailing
 * bytes in the SAME feed call, the decompressor returns FORMAT_ERROR.
 *
 * Coverage: executable Brotli streaming behavior
 * ---------------------------------------------------------------- */

#define TRAILING_DATA_ITERATIONS  150
#define MIN_PLAINTEXT_SIZE         1
#define MAX_PLAINTEXT_SIZE         4096
#define MIN_TRAILING_SIZE          1
#define MAX_TRAILING_SIZE          128

static void
test_property2_trailing_data_rejection(void)
{
    int     iter;
    size_t  plaintext_len;
    size_t  trailing_len;
    size_t  compressed_len;
    size_t  total_len;
    u_char  plaintext[MAX_PLAINTEXT_SIZE];
    u_char  compressed[MAX_PLAINTEXT_SIZE + 1024];
    u_char  with_trailing[MAX_PLAINTEXT_SIZE + 1024 + MAX_TRAILING_SIZE];
    test_pool_t tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char *out;
    size_t  out_len;
    ngx_int_t rc;

    TEST_SUBSECTION(
        "Property 2: Trailing data rejection — same feed "
        "(150 iterations)");

    for (iter = 0; iter < TRAILING_DATA_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 42));

        /* Generate random plaintext (1-4096 bytes) */
        plaintext_len = (size_t)(
            (prng_next() % (MAX_PLAINTEXT_SIZE - MIN_PLAINTEXT_SIZE))
            + MIN_PLAINTEXT_SIZE);
        fill_random_bytes(plaintext, plaintext_len);

        /* Compress with Brotli encoder */
        compressed_len = sizeof(compressed);
        TEST_ASSERT(
            BrotliEncoderCompress(
                BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, plaintext_len, plaintext,
                &compressed_len, compressed) == BROTLI_TRUE,
            "brotli compression must succeed");

        /* Generate random trailing bytes (1-128 bytes) */
        trailing_len = (size_t)(
            (prng_next() % (MAX_TRAILING_SIZE - MIN_TRAILING_SIZE))
            + MIN_TRAILING_SIZE);

        /* Build payload: valid compressed + trailing garbage */
        total_len = compressed_len + trailing_len;
        memcpy(with_trailing, compressed, compressed_len);
        fill_random_bytes(with_trailing + compressed_len, trailing_len);

        /* Create decompressor with no budget limit */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor must be created");

        /* Feed the concatenated buffer (valid + trailing) */
        out = (u_char *) 0x1;
        out_len = 1;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, with_trailing, total_len,
            &out, &out_len, &tp.pool, &test_log);

        /* Assert: FORMAT_ERROR returned */
        TEST_ASSERT(
            rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
            "trailing data must return FORMAT_ERROR");

        /* Assert: no output exposed on error */
        TEST_ASSERT(out == NULL && out_len == 0,
            "trailing-data error must not expose output");

        /* Cleanup */
        ngx_http_markdown_streaming_decomp_cleanup(decomp);

        TEST_ASSERT(g_free_on_palloc_violation == 0,
            "must not ngx_free() pool memory");
        TEST_ASSERT(g_free_on_static_pool_violation == 0,
            "must not ngx_free() static pool memory");
    }

    TEST_PASS(
        "Property 2: all 150 iterations confirmed "
        "same-feed trailing data -> FORMAT_ERROR");
}

/* ----------------------------------------------------------------
 * Property 2b: Next-feed trailing data rejection
 *
 * Valid Brotli stream completes in first feed, then ANY non-empty
 * feed returns FORMAT_ERROR without invoking the decoder.
 *
 * Coverage: executable Brotli streaming behavior
 * ---------------------------------------------------------------- */

#define NEXT_FEED_TRAILING_ITERATIONS  100

static void
test_property2b_next_feed_trailing_rejection(void)
{
    int     iter;
    size_t  plaintext_len;
    size_t  trailing_len;
    size_t  compressed_len;
    u_char  plaintext[MAX_PLAINTEXT_SIZE];
    u_char  compressed[MAX_PLAINTEXT_SIZE + 1024];
    u_char  trailing[MAX_TRAILING_SIZE];
    test_pool_t tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char *out;
    size_t  out_len;
    ngx_int_t rc;

    TEST_SUBSECTION(
        "Property 2b: Next-feed trailing data rejection "
        "(100 iterations)");

    for (iter = 0; iter < NEXT_FEED_TRAILING_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 9999));

        /* Generate random plaintext */
        plaintext_len = (size_t)(
            (prng_next() % (MAX_PLAINTEXT_SIZE - MIN_PLAINTEXT_SIZE))
            + MIN_PLAINTEXT_SIZE);
        fill_random_bytes(plaintext, plaintext_len);

        /* Compress with Brotli encoder */
        compressed_len = sizeof(compressed);
        TEST_ASSERT(
            BrotliEncoderCompress(
                BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, plaintext_len, plaintext,
                &compressed_len, compressed) == BROTLI_TRUE,
            "brotli compression must succeed");

        /* Create decompressor with no budget limit */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor must be created");

        /* First feed: valid complete Brotli stream */
        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, compressed, compressed_len,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK,
            "valid brotli feed must succeed");
        TEST_ASSERT(decomp->finished == 1,
            "complete brotli stream must set finished");

        /* Generate random trailing bytes for second feed */
        trailing_len = (size_t)(
            (prng_next() % (MAX_TRAILING_SIZE - MIN_TRAILING_SIZE))
            + MIN_TRAILING_SIZE);
        fill_random_bytes(trailing, trailing_len);

        /* Second feed: non-empty trailing data */
        out = (u_char *) 0x1;
        out_len = 1;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, trailing, trailing_len,
            &out, &out_len, &tp.pool, &test_log);

        /* Assert: FORMAT_ERROR returned */
        TEST_ASSERT(
            rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
            "next-feed trailing data must return FORMAT_ERROR");

        /* Assert: no output exposed on error */
        TEST_ASSERT(out == NULL && out_len == 0,
            "trailing-data error must not expose output");

        /* Cleanup */
        ngx_http_markdown_streaming_decomp_cleanup(decomp);

        TEST_ASSERT(g_free_on_palloc_violation == 0,
            "must not ngx_free() pool memory");
        TEST_ASSERT(g_free_on_static_pool_violation == 0,
            "must not ngx_free() static pool memory");
    }

    TEST_PASS(
        "Property 2b: all 100 iterations confirmed "
        "next-feed trailing -> FORMAT_ERROR");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: Brotli streaming decompression\n"
        "Property 2: Trailing Data Rejection\n"
        "Coverage: executable Brotli streaming behavior");

    /* Same-feed trailing data rejection */
    test_property2_trailing_data_rejection();

    /* Next-feed trailing data rejection */
    test_property2b_next_feed_trailing_rejection();

    printf("\n");
    TEST_PASS(
        "brotli_trailing_property: all property tests passed");
    return 0;
}

#endif /* NGX_HTTP_BROTLI */
#endif /* MARKDOWN_STREAMING_ENABLED */
