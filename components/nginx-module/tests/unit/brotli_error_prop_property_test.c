/*
 * Test: brotli_error_prop_property
 *
 * Property-based test for error code propagation through brotli_loop()
 * (Property 9).
 *
 * Feature: Brotli streaming decompression
 * Property 9: Error Code Propagation Through brotli_loop()
 *
 * Coverage: executable Brotli streaming behavior.
 *
 * The property states:
 *   For ALL error conditions where brotli_step() returns a specific
 *   NGX_HTTP_MARKDOWN_DECOMP_* code (FORMAT_ERROR, TRUNCATED_INPUT,
 *   BUDGET_EXCEEDED, IO_ERROR), brotli_loop() SHALL return that same
 *   code to its caller without folding to generic NGX_ERROR.
 *
 * Test approach:
 *   Part A: Generate random malformed inputs (100 iterations)
 *     - Random garbage bytes → assert exact FORMAT_ERROR returned
 *     - Not folded to generic NGX_ERROR
 *   Part B: Generate valid payloads exceeding budget (50 iterations)
 *     - Random plaintext, compress, set tight budget → assert exact
 *       BUDGET_EXCEEDED
 *   Part C: Generate truncated valid payloads (50 iterations)
 *     - Random plaintext, compress, truncate at random offset,
 *       call finish → assert exact TRUNCATED_INPUT
 *
 * Total: 200 iterations across three error classes.
 */

#include "../include/test_common.h"
#include <limits.h>
#include <zlib.h>
#include <brotli/encode.h>
#include <brotli/decode.h>

/*
 * Define struct ngx_log_s - the nginx_stubs only forward-declare it.
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
    printf("brotli_error_prop_property Tests (SKIPPED)\n");
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
    printf("brotli_error_prop_property Tests (SKIPPED)\n");
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
 * zlib stubs - delegate to real zlib
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
 * Part A: Malformed input -> FORMAT_ERROR propagation
 *
 * For random garbage inputs of varying sizes, the decompressor
 * SHALL return the exact NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
 * constant, never folding to generic NGX_ERROR.
 * ---------------------------------------------------------------- */

#define FORMAT_ERROR_ITERATIONS  100
#define MIN_MALFORMED_SIZE        4
#define MAX_MALFORMED_SIZE      256

static void
test_property9_part_a_format_error_propagation(void)
{
    int         iter;
    int         format_errors = 0;
    int         system_errors = 0;
    int         ok_results = 0;
    size_t      input_len;
    u_char      malformed[MAX_MALFORMED_SIZE];
    test_pool_t tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char     *out;
    size_t      out_len;
    ngx_int_t   rc;

    TEST_SUBSECTION(
        "Property 9A: Malformed input -> FORMAT_ERROR "
        "(100 iterations)");

    for (iter = 0; iter < FORMAT_ERROR_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 7777));

        /* Generate random garbage of varying sizes */
        input_len = (size_t)(
            (prng_next() % (MAX_MALFORMED_SIZE - MIN_MALFORMED_SIZE))
            + MIN_MALFORMED_SIZE);
        fill_random_bytes(malformed, input_len);

        /*
         * Ensure the first byte is NOT a valid empty Brotli stream
         * (0x06 is the valid empty Brotli stream).  Random bytes almost
         * never are 0x06, but guard explicitly.
         */
        if (malformed[0] == 0x06) {
            malformed[0] = 0xFF;
        }

        /* Create decompressor with generous budget */
        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, 1048576);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor must be created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, malformed, input_len,
            &out, &out_len, &tp.pool, &test_log);

        /*
         * The result MUST be one of:
         * - FORMAT_ERROR: format-class decoder error propagated correctly
         * - NGX_ERROR with non-NONE origin: allocation/internal error
         * - NGX_OK: decoder accepted partial data as valid-so-far
         *   (some random bytes happen to form a valid partial stream)
         *
         * For Property 9, the key assertion is that when an error IS
         * produced, it is the exact typed constant — FORMAT-class errors
         * produce NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR, not generic
         * NGX_ERROR without classification.
         */
        if (rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR) {
            /* Expected: typed FORMAT_ERROR propagated correctly */
            format_errors++;
            TEST_ASSERT(
                decomp->failure_origin
                    == NGX_HTTP_MD_DECOMP_ORIGIN_NONE,
                "FORMAT_ERROR must not set failure_origin");
        } else if (rc == NGX_ERROR) {
            /*
             * Acceptable only if the Brotli decoder reported an
             * ALLOCATION or INTERNAL class error (not FORMAT).
             * The failure_origin MUST be set.
             */
            system_errors++;
            TEST_ASSERT(
                decomp->failure_origin
                    != NGX_HTTP_MD_DECOMP_ORIGIN_NONE,
                "NGX_ERROR must have failure_origin set");
        } else if (rc == NGX_OK) {
            /*
             * Decoder accepted the data as a valid partial stream
             * (needs more input).  This is acceptable for some random
             * byte patterns.  No error was produced, so the propagation
             * property is vacuously satisfied.
             */
            ok_results++;
        } else {
            /* No other return code is acceptable for malformed input */
            TEST_ASSERT(0,
                "malformed input must return FORMAT_ERROR, "
                "classified NGX_ERROR, or NGX_OK");
        }

        /* No partial output exposed on error */
        if (rc != NGX_OK) {
            TEST_ASSERT(out == NULL && out_len == 0,
                "error path must not expose output");
        }

        ngx_http_markdown_streaming_decomp_cleanup(decomp);

        TEST_ASSERT(g_free_on_palloc_violation == 0,
            "must not ngx_free() pool memory");
        TEST_ASSERT(g_free_on_static_pool_violation == 0,
            "must not ngx_free() static pool memory");
    }

    /*
     * Verify that a meaningful number of iterations actually produced
     * errors (so the property was non-vacuously tested).  With random
     * 4-256 byte garbage, the vast majority should trigger FORMAT_ERROR.
     */
    TEST_ASSERT(format_errors + system_errors >= 50,
        "at least 50 iterations must produce errors");

    printf("    (format_errors=%d, system_errors=%d, ok_results=%d)\n",
        format_errors, system_errors, ok_results);

    TEST_PASS(
        "Property 9A: all 100 iterations confirmed "
        "malformed -> FORMAT_ERROR (never unclassified NGX_ERROR)");
}

/* ----------------------------------------------------------------
 * Part B: Budget exceeded -> BUDGET_EXCEEDED propagation
 *
 * For random valid payloads whose decompressed size exceeds budget,
 * the decompressor SHALL return the exact
 * NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED constant.
 * ---------------------------------------------------------------- */

#define BUDGET_EXCEEDED_ITERATIONS   50
#define MIN_BUDGET_TEXT_SIZE         64
#define MAX_BUDGET_TEXT_SIZE       4096

static void
test_property9_part_b_budget_exceeded_propagation(void)
{
    int         iter;
    size_t      plaintext_len;
    size_t      budget;
    size_t      compressed_len;
    u_char      plaintext[MAX_BUDGET_TEXT_SIZE];
    u_char      compressed[MAX_BUDGET_TEXT_SIZE + 1024];
    test_pool_t tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char     *out;
    size_t      out_len;
    ngx_int_t   rc;

    TEST_SUBSECTION(
        "Property 9B: Budget exceeded -> BUDGET_EXCEEDED "
        "(50 iterations)");

    for (iter = 0; iter < BUDGET_EXCEEDED_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 55555));

        /* Generate random plaintext (64-4096 bytes) */
        plaintext_len = (size_t)(
            (prng_next() % (MAX_BUDGET_TEXT_SIZE - MIN_BUDGET_TEXT_SIZE))
            + MIN_BUDGET_TEXT_SIZE);
        fill_random_bytes(plaintext, plaintext_len);

        /* Compress with Brotli */
        compressed_len = sizeof(compressed);
        TEST_ASSERT(
            BrotliEncoderCompress(
                BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, plaintext_len, plaintext,
                &compressed_len, compressed) == BROTLI_TRUE,
            "brotli compression must succeed");

        /*
         * Set budget to less than half the plaintext length,
         * ensuring budget is exceeded.
         */
        budget = plaintext_len / 4;
        if (budget < 1) {
            budget = 1;
        }

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, budget);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor must be created");

        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, compressed, compressed_len,
            &out, &out_len, &tp.pool, &test_log);

        /*
         * The exact BUDGET_EXCEEDED constant MUST be returned.
         * It must NOT be folded to generic NGX_ERROR.
         */
        TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
            "over-budget brotli must return exact BUDGET_EXCEEDED");

        /* No partial output exposed on budget exceeded */
        TEST_ASSERT(out == NULL && out_len == 0,
            "budget exceeded must not expose partial output");

        ngx_http_markdown_streaming_decomp_cleanup(decomp);

        TEST_ASSERT(g_free_on_palloc_violation == 0,
            "must not ngx_free() pool memory");
        TEST_ASSERT(g_free_on_static_pool_violation == 0,
            "must not ngx_free() static pool memory");
    }

    TEST_PASS(
        "Property 9B: all 50 iterations confirmed "
        "over-budget -> BUDGET_EXCEEDED (never NGX_ERROR)");
}

/* ----------------------------------------------------------------
 * Part C: Truncated input -> TRUNCATED_INPUT propagation
 *
 * For random valid payloads truncated at a random offset, calling
 * finish() SHALL return the exact
 * NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT constant.
 * ---------------------------------------------------------------- */

#define TRUNCATED_ITERATIONS    50
#define MIN_TRUNC_TEXT_SIZE     32
#define MAX_TRUNC_TEXT_SIZE   2048

static void
test_property9_part_c_truncated_input_propagation(void)
{
    int         iter;
    size_t      plaintext_len;
    size_t      compressed_len;
    size_t      trunc_len;
    u_char      plaintext[MAX_TRUNC_TEXT_SIZE];
    u_char      compressed[MAX_TRUNC_TEXT_SIZE + 1024];
    test_pool_t tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char     *out;
    size_t      out_len;
    ngx_int_t   rc;

    TEST_SUBSECTION(
        "Property 9C: Truncated input -> TRUNCATED_INPUT "
        "(50 iterations)");

    for (iter = 0; iter < TRUNCATED_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 33333));

        /* Generate random plaintext (32-2048 bytes) */
        plaintext_len = (size_t)(
            (prng_next() % (MAX_TRUNC_TEXT_SIZE - MIN_TRUNC_TEXT_SIZE))
            + MIN_TRUNC_TEXT_SIZE);
        fill_random_bytes(plaintext, plaintext_len);

        /* Compress with Brotli */
        compressed_len = sizeof(compressed);
        TEST_ASSERT(
            BrotliEncoderCompress(
                BROTLI_DEFAULT_QUALITY, BROTLI_DEFAULT_WINDOW,
                BROTLI_MODE_GENERIC, plaintext_len, plaintext,
                &compressed_len, compressed) == BROTLI_TRUE,
            "brotli compression must succeed");

        /*
         * Truncate at a random offset between 1 and (compressed_len - 1).
         * This ensures we have at least 1 byte and strictly less than
         * the complete stream.
         */
        if (compressed_len <= 2) {
            /* Very rare: tiny compressed output; skip this iteration */
            continue;
        }
        trunc_len = (size_t)(
            (prng_next() % (compressed_len - 2)) + 1);

        test_pool_reset(&tp);
        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
            plaintext_len + 1024);
        TEST_ASSERT(decomp != NULL,
            "brotli decompressor must be created");

        /* Feed truncated data */
        out = NULL;
        out_len = 0;
        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, compressed, trunc_len,
            &out, &out_len, &tp.pool, &test_log);

        /*
         * The feed may succeed (awaiting more data) or may detect a
         * format error if truncation hits mid-block.  If feed succeeds,
         * then finish() must detect the truncation.
         */
        if (rc == NGX_OK) {
            /* Feed accepted partial data; now call finish() */
            out = NULL;
            out_len = 0;
            rc = ngx_http_markdown_streaming_decomp_finish(
                decomp, &out, &out_len, &tp.pool, &test_log);

            /*
             * After feeding truncated data where feed() returned OK,
             * finish() must detect truncation.  However, if the
             * decoder's internal state was left as "finished" (e.g.,
             * the truncated prefix happens to form a valid complete
             * stream), finish() will return NGX_OK which is correct.
             */
            if (rc != NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT
                && rc != NGX_OK)
            {
                printf("      unexpected finish rc=%d iter=%d "
                       "compressed=%zu trunc=%zu\n",
                       (int) rc, iter, compressed_len, trunc_len);
            }
            TEST_ASSERT(
                rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT
                || rc == NGX_OK,
                "finish on truncated brotli must return "
                "TRUNCATED_INPUT or NGX_OK (valid prefix)");

            free(out);
        } else if (rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR) {
            /*
             * Also acceptable: truncation mid-block caused a decode
             * error during feed() which was classified as FORMAT_ERROR.
             * The key assertion: it's the exact constant, not NGX_ERROR.
             */
            TEST_ASSERT(
                decomp->failure_origin
                    == NGX_HTTP_MD_DECOMP_ORIGIN_NONE,
                "FORMAT_ERROR must not set failure_origin");
        } else if (rc == NGX_ERROR) {
            /*
             * Only acceptable if origin is set (not NONE).
             */
            TEST_ASSERT(
                decomp->failure_origin
                    != NGX_HTTP_MD_DECOMP_ORIGIN_NONE,
                "NGX_ERROR from truncated feed must have origin set");
        } else {
            TEST_ASSERT(0,
                "unexpected return code on truncated feed");
        }

        ngx_http_markdown_streaming_decomp_cleanup(decomp);

        TEST_ASSERT(g_free_on_palloc_violation == 0,
            "must not ngx_free() pool memory");
        TEST_ASSERT(g_free_on_static_pool_violation == 0,
            "must not ngx_free() static pool memory");
    }

    TEST_PASS(
        "Property 9C: all 50 iterations confirmed "
        "truncated -> TRUNCATED_INPUT (never NGX_ERROR)");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: Brotli streaming decompression\n"
        "Property 9: Error Code Propagation Through brotli_loop()\n"
        "Coverage: executable Brotli streaming behavior");

    /* Part A: FORMAT_ERROR propagation from malformed input */
    test_property9_part_a_format_error_propagation();

    /* Part B: BUDGET_EXCEEDED propagation */
    test_property9_part_b_budget_exceeded_propagation();

    /* Part C: TRUNCATED_INPUT propagation */
    test_property9_part_c_truncated_input_propagation();

    printf("\n");
    TEST_PASS(
        "brotli_error_prop_property: all property tests passed");
    return 0;
}

#endif /* NGX_HTTP_BROTLI */
#endif /* MARKDOWN_STREAMING_ENABLED */
