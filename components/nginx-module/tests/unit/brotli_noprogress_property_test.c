/*
 * Test: brotli_noprogress_property
 *
 * Property-based test for no-progress guard — no false positives (Property 11).
 *
 * Feature: Brotli streaming decompression
 * Property 11: No-Progress Guard — No False Positives
 *
 * Coverage: executable Brotli streaming behavior.
 *
 * The property states:
 *   For ALL valid Brotli-compressed streams fed through the streaming
 *   decompressor in ANY chunk pattern, the no-progress guard SHALL
 *   never fire (i.e., every BrotliDecoderDecompressStream call on
 *   valid data exhibits at least one progress indicator).
 *
 * Test approach:
 *   1. Generate random plaintext of various sizes (1 byte to 32 KiB)
 *   2. Compress with BrotliEncoderCompress
 *   3. Feed through the module's streaming decompressor in random
 *      non-empty chunks (1 byte to full payload)
 *   4. Assert: feed always returns NGX_OK (never FORMAT_ERROR from
 *      the no-progress guard)
 *   5. Assert: concatenated output matches original plaintext
 *   6. Minimum 150 iterations with varying seeds
 */

#include "../include/test_common.h"
#include <limits.h>
#include <zlib.h>
#include <brotli/encode.h>
#include <brotli/decode.h>

/*
 * Define struct ngx_log_s needed for test_log instantiation.
 */
struct ngx_log_s {
    int unused;
};

#include <ngx_http_markdown_filter_module.h>

#ifndef MARKDOWN_STREAMING_ENABLED

int
main(void)
{
    printf("\n========================================\n");
    printf("brotli_noprogress_property Tests (SKIPPED)\n");
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
    printf("brotli_noprogress_property Tests (SKIPPED)\n");
    printf("NGX_HTTP_BROTLI not defined\n");
    printf("========================================\n\n");
    return 0;
}

#else /* NGX_HTTP_BROTLI */

/* ----------------------------------------------------------------
 * Minimal NGINX pool/allocation stubs for standalone compilation
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

static void * __attribute__((unused))
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    return ngx_palloc(pool, size);
}

/* ----------------------------------------------------------------
 * ngx_pcalloc stub
 *
 * Pool-lifetime allocations (struct, cleanup) — NOT tracked for
 * mid-loop free. Only ngx_palloc (per-chunk output copies) is tracked.
 * ---------------------------------------------------------------- */

static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    if (g_pcalloc_fail_once) {
        g_pcalloc_fail_once = 0;
        return NULL;
    }
    return calloc(1, size);
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

static ngx_int_t __attribute__((unused))
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
 * Redirect zlib inflate/inflateInit2/inflateEnd to the test stubs so
 * the production implementation calls our versions.
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
 * Property 11: No-Progress Guard — No False Positives
 *
 * For ALL valid Brotli-compressed streams fed through the streaming
 * decompressor in arbitrary non-empty chunks, the no-progress guard
 * SHALL never fire.
 *
 * Strategy:
 *   Generate random plaintext (1 byte to 32 KiB), compress with
 *   Brotli, feed through ngx_http_markdown_streaming_decomp_feed()
 *   in random non-empty chunks (1 byte to remaining payload).
 *   Assert every feed returns NGX_OK.  After all feeds, call finish
 *   and verify NGX_OK.  Concatenated output must match original.
 *
 * Coverage: executable Brotli streaming behavior
 * ---------------------------------------------------------------- */

#define PROPERTY11_ITERATIONS     150
#define PROPERTY11_MAX_TEXT_SIZE   (32 * 1024)
#define PROPERTY11_MAX_COMPRESSED  (PROPERTY11_MAX_TEXT_SIZE + 4096)

static void
test_property11_no_progress_no_false_positives(void)
{
    int          iter;
    size_t       text_len;
    u_char      *plaintext;
    u_char      *compressed;
    size_t       compressed_cap;
    size_t       compressed_len;
    u_char      *result_buf;
    size_t       result_len;
    test_pool_t  tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    int          pass_count;

    TEST_SUBSECTION(
        "Property 11: No-Progress Guard — No False Positives "
        "(150 iterations, 1B-32KiB, random chunks)");

    pass_count = 0;

    for (iter = 0; iter < PROPERTY11_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 101));

        /* Random plaintext size: 1 to 32 KiB */
        text_len = (size_t)((prng_next() % PROPERTY11_MAX_TEXT_SIZE) + 1);

        plaintext = (u_char *) malloc(text_len);
        TEST_ASSERT(plaintext != NULL,
            "plaintext allocation must succeed");
        fill_random_bytes(plaintext, text_len);

        /* Compress with Brotli */
        compressed_cap = BrotliEncoderMaxCompressedSize(text_len);
        if (compressed_cap == 0) {
            compressed_cap = text_len + 1024;
        }
        compressed = (u_char *) malloc(compressed_cap);
        TEST_ASSERT(compressed != NULL,
            "compressed buffer allocation must succeed");

        compressed_len = compressed_cap;
        TEST_ASSERT(
            BrotliEncoderCompress(
                BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, text_len, plaintext,
                &compressed_len, compressed)
            == BROTLI_TRUE,
            "BrotliEncoderCompress must succeed");

        /* Allocate result buffer for concatenated output */
        result_buf = (u_char *) malloc(text_len + 256);
        TEST_ASSERT(result_buf != NULL,
            "result buffer allocation must succeed");
        result_len = 0;

        /* Create module decompressor with no budget limit */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor must be created");

        /* Feed compressed data in random non-empty chunks.
         * Use a different seed for chunk splitting. */
        {
            size_t   offset = 0;
            size_t   chunk_size;

            prng_seed((unsigned int)(iter * 6277 + 4013));

            while (offset < compressed_len) {
                u_char    *out;
                size_t     out_len;
                ngx_int_t  rc;
                size_t     remaining;

                remaining = compressed_len - offset;

                /* Random chunk: 1 byte to remaining */
                chunk_size = (size_t)(
                    (prng_next() % remaining) + 1);

                out = NULL;
                out_len = 0;
                rc = ngx_http_markdown_streaming_decomp_feed(
                    decomp,
                    compressed + offset, chunk_size,
                    &out, &out_len, &tp.pool, &test_log);

                /*
                 * The critical assertion: feed must return NGX_OK
                 * for valid Brotli data. If FORMAT_ERROR is returned,
                 * the no-progress guard fired as a false positive.
                 */
                TEST_ASSERT(rc == NGX_OK,
                    "feed on valid Brotli must return NGX_OK "
                    "(no-progress guard must not false-positive)");

                /* Accumulate output */
                if (out_len > 0) {
                    TEST_ASSERT(
                        result_len + out_len <= text_len + 256,
                        "output must not exceed expected size");
                    memcpy(result_buf + result_len, out, out_len);
                    result_len += out_len;
                }

                /* Free pool allocations for this chunk */
                test_pool_free_tracked_allocations();
                offset += chunk_size;
            }
        }

        /* Finish must also succeed */
        {
            u_char    *finish_out;
            size_t     finish_len;
            ngx_int_t  rc;

            finish_out = NULL;
            finish_len = 0;
            rc = ngx_http_markdown_streaming_decomp_finish(
                decomp, &finish_out, &finish_len,
                &tp.pool, &test_log);
            TEST_ASSERT(rc == NGX_OK,
                "finish on valid Brotli must return NGX_OK");

            if (finish_len > 0) {
                memcpy(result_buf + result_len,
                    finish_out, finish_len);
                result_len += finish_len;
            }
        }

        /* Verify output matches original plaintext */
        TEST_ASSERT(result_len == text_len,
            "decompressed output length must match original");
        TEST_ASSERT(MEM_EQ(result_buf, plaintext, text_len),
            "decompressed output must be byte-identical "
            "to original plaintext");

        /* Cleanup */
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
        free(plaintext);
        free(compressed);
        free(result_buf);
        test_pool_free_tracked_allocations();

        TEST_ASSERT(g_free_on_palloc_violation == 0,
            "must not ngx_free() pool memory");
        TEST_ASSERT(g_free_on_static_pool_violation == 0,
            "must not ngx_free() static pool memory");

        pass_count++;
    }

    TEST_ASSERT(pass_count == PROPERTY11_ITERATIONS,
        "all iterations must pass");
    TEST_PASS(
        "Property 11: no-progress guard no false positives "
        "verified (150 iterations, 1B-32KiB, random chunks)");
}

/* ----------------------------------------------------------------
 * Property 11b: Single-byte chunk pattern — worst case
 *
 * Feed valid Brotli streams one byte at a time through the module
 * decompressor.  This is the worst-case fragmentation pattern and
 * exercises NEEDS_MORE_INPUT returns most aggressively.
 *
 * Coverage: executable Brotli streaming behavior
 * ---------------------------------------------------------------- */

#define PROPERTY11B_ITERATIONS  50
#define PROPERTY11B_MAX_TEXT    4096

static void
test_property11b_single_byte_no_false_positives(void)
{
    int          iter;
    size_t       text_len;
    u_char      *plaintext;
    u_char      *compressed;
    size_t       compressed_cap;
    size_t       compressed_len;
    u_char      *result_buf;
    size_t       result_len;
    test_pool_t  tp;
    ngx_http_markdown_streaming_decomp_t *decomp;

    TEST_SUBSECTION(
        "Property 11b: Single-byte chunks — no false positives "
        "(50 iterations, 1B-4KiB)");

    for (iter = 0; iter < PROPERTY11B_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 5557));

        /* Smaller sizes for byte-at-a-time to keep runtime bounded */
        text_len = (size_t)((prng_next() % PROPERTY11B_MAX_TEXT) + 1);

        plaintext = (u_char *) malloc(text_len);
        TEST_ASSERT(plaintext != NULL,
            "plaintext allocation must succeed");
        fill_random_bytes(plaintext, text_len);

        compressed_cap = BrotliEncoderMaxCompressedSize(text_len);
        if (compressed_cap == 0) {
            compressed_cap = text_len + 1024;
        }
        compressed = (u_char *) malloc(compressed_cap);
        TEST_ASSERT(compressed != NULL,
            "compressed buffer allocation must succeed");

        compressed_len = compressed_cap;
        TEST_ASSERT(
            BrotliEncoderCompress(
                BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, text_len, plaintext,
                &compressed_len, compressed)
            == BROTLI_TRUE,
            "BrotliEncoderCompress must succeed");

        result_buf = (u_char *) malloc(text_len + 256);
        TEST_ASSERT(result_buf != NULL,
            "result buffer allocation must succeed");
        result_len = 0;

        /* Create module decompressor with no budget limit */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor must be created");

        /* Feed one byte at a time */
        {
            size_t i;

            for (i = 0; i < compressed_len; i++) {
                u_char    *out;
                size_t     out_len;
                ngx_int_t  rc;

                out = NULL;
                out_len = 0;
                rc = ngx_http_markdown_streaming_decomp_feed(
                    decomp,
                    compressed + i, 1,
                    &out, &out_len, &tp.pool, &test_log);

                TEST_ASSERT(rc == NGX_OK,
                    "single-byte feed on valid Brotli must "
                    "return NGX_OK (no false-positive guard)");

                if (out_len > 0) {
                    TEST_ASSERT(
                        result_len + out_len <= text_len + 256,
                        "output must not exceed expected size");
                    memcpy(result_buf + result_len, out, out_len);
                    result_len += out_len;
                }

                test_pool_free_tracked_allocations();
            }
        }

        /* Finish */
        {
            u_char    *finish_out;
            size_t     finish_len;
            ngx_int_t  rc;

            finish_out = NULL;
            finish_len = 0;
            rc = ngx_http_markdown_streaming_decomp_finish(
                decomp, &finish_out, &finish_len,
                &tp.pool, &test_log);
            TEST_ASSERT(rc == NGX_OK,
                "finish on valid Brotli must return NGX_OK");

            if (finish_len > 0) {
                memcpy(result_buf + result_len,
                    finish_out, finish_len);
                result_len += finish_len;
            }
        }

        /* Verify output */
        TEST_ASSERT(result_len == text_len,
            "single-byte decompressed length must match");
        TEST_ASSERT(MEM_EQ(result_buf, plaintext, text_len),
            "single-byte decompressed output must match "
            "original");

        /* Cleanup */
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
        free(plaintext);
        free(compressed);
        free(result_buf);
        test_pool_free_tracked_allocations();

        TEST_ASSERT(g_free_on_palloc_violation == 0,
            "must not ngx_free() pool memory");
        TEST_ASSERT(g_free_on_static_pool_violation == 0,
            "must not ngx_free() static pool memory");
    }

    TEST_PASS(
        "Property 11b: single-byte chunks no false positives "
        "verified (50 iterations)");
}

/* ----------------------------------------------------------------
 * Property 11c: Large payload with varied compression quality
 *
 * Use higher compression quality settings to exercise different
 * internal decoder states and window sizes with random chunks.
 *
 * Coverage: executable Brotli streaming behavior
 * ---------------------------------------------------------------- */

#define PROPERTY11C_ITERATIONS  30
#define PROPERTY11C_MAX_TEXT    (32 * 1024)

static void
test_property11c_varied_quality_no_false_positives(void)
{
    int          iter;
    size_t       text_len;
    u_char      *plaintext;
    u_char      *compressed;
    size_t       compressed_cap;
    size_t       compressed_len;
    u_char      *result_buf;
    size_t       result_len;
    test_pool_t  tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    int          quality;

    TEST_SUBSECTION(
        "Property 11c: Varied compression quality "
        "(30 iterations, quality 1-11, random chunks)");

    for (iter = 0; iter < PROPERTY11C_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 7331));

        text_len = (size_t)(
            (prng_next() % PROPERTY11C_MAX_TEXT) + 1);

        /* Vary quality from 1 (fastest) to 11 (best compression) */
        quality = (int)((prng_next() % 11) + 1);

        plaintext = (u_char *) malloc(text_len);
        TEST_ASSERT(plaintext != NULL,
            "plaintext allocation must succeed");
        fill_random_bytes(plaintext, text_len);

        compressed_cap = BrotliEncoderMaxCompressedSize(text_len);
        if (compressed_cap == 0) {
            compressed_cap = text_len + 1024;
        }
        compressed = (u_char *) malloc(compressed_cap);
        TEST_ASSERT(compressed != NULL,
            "compressed buffer allocation must succeed");

        compressed_len = compressed_cap;
        TEST_ASSERT(
            BrotliEncoderCompress(
                quality, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, text_len, plaintext,
                &compressed_len, compressed)
            == BROTLI_TRUE,
            "BrotliEncoderCompress must succeed");

        result_buf = (u_char *) malloc(text_len + 256);
        TEST_ASSERT(result_buf != NULL,
            "result buffer allocation must succeed");
        result_len = 0;

        /* Create module decompressor with no budget limit */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 0);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor must be created");

        /* Feed in random chunks */
        {
            size_t   offset = 0;
            size_t   chunk_size;

            prng_seed((unsigned int)(iter * 3119 + 997));

            while (offset < compressed_len) {
                u_char    *out;
                size_t     out_len;
                ngx_int_t  rc;
                size_t     remaining;

                remaining = compressed_len - offset;
                chunk_size = (size_t)(
                    (prng_next() % remaining) + 1);

                out = NULL;
                out_len = 0;
                rc = ngx_http_markdown_streaming_decomp_feed(
                    decomp,
                    compressed + offset, chunk_size,
                    &out, &out_len, &tp.pool, &test_log);

                TEST_ASSERT(rc == NGX_OK,
                    "varied-quality feed on valid Brotli must "
                    "return NGX_OK");

                if (out_len > 0) {
                    memcpy(result_buf + result_len, out, out_len);
                    result_len += out_len;
                }

                test_pool_free_tracked_allocations();
                offset += chunk_size;
            }
        }

        /* Finish */
        {
            u_char    *finish_out;
            size_t     finish_len;
            ngx_int_t  rc;

            finish_out = NULL;
            finish_len = 0;
            rc = ngx_http_markdown_streaming_decomp_finish(
                decomp, &finish_out, &finish_len,
                &tp.pool, &test_log);
            TEST_ASSERT(rc == NGX_OK,
                "finish must return NGX_OK");

            if (finish_len > 0) {
                memcpy(result_buf + result_len,
                    finish_out, finish_len);
                result_len += finish_len;
            }
        }

        /* Verify output matches original */
        TEST_ASSERT(result_len == text_len,
            "varied-quality output length must match");
        TEST_ASSERT(MEM_EQ(result_buf, plaintext, text_len),
            "varied-quality output must match original");

        /* Cleanup */
        ngx_http_markdown_streaming_decomp_cleanup(decomp);
        free(decomp);
        free(plaintext);
        free(compressed);
        free(result_buf);
        test_pool_free_tracked_allocations();
    }

    TEST_PASS(
        "Property 11c: varied quality no false positives "
        "verified (30 iterations)");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    printf("\n========================================\n");
    printf("brotli_noprogress_property Tests\n");
    printf("Property 11: No-Progress Guard — "
           "No False Positives\n");
    printf("Feature: Brotli streaming decompression\n");
    printf("========================================\n");

    TEST_SECTION(
        "Feature: Brotli streaming decompression\n"
        "Property 11: No-Progress Guard — No False Positives\n"
        "Coverage: executable Brotli streaming behavior.");

    /* Core property: random sizes, random chunk splits */
    test_property11_no_progress_no_false_positives();

    /* Worst case: single-byte chunk feeds */
    test_property11b_single_byte_no_false_positives();

    /* Varied quality levels */
    test_property11c_varied_quality_no_false_positives();

    printf("\n========================================\n");
    printf("All Property 11 tests PASSED\n");
    printf("========================================\n\n");
    return 0;
}

#endif /* NGX_HTTP_BROTLI */
#endif /* MARKDOWN_STREAMING_ENABLED */
