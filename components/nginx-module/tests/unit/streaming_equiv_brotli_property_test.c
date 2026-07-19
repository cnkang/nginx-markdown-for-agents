/*
 * Test: streaming_equiv_brotli_property
 *
 * Property-based test for streaming-vs-full-buffer decompression
 * equivalence (Property 6).
 *
 * Feature: Brotli streaming decompression
 * Property 6: Streaming-vs-Full-Buffer Equivalence
 *
 * Coverage: executable Brotli streaming behavior.
 *
 * The property states:
 *   For ALL valid Brotli streams, the streaming decompressor produces
 *   byte-identical output to decompressing the same data in one shot
 *   (full-buffer BrotliDecoderDecompress).
 *
 * Test approach:
 *   1. Generate random text of various sizes (1 byte to 64 KiB)
 *   2. Compress with BrotliEncoderCompress
 *   3. Decompress via streaming (random non-empty chunk splits)
 *   4. Decompress via full-buffer (BrotliDecoderDecompress)
 *   5. Assert both produce identical output
 *   6. Minimum 100 iterations with varying seeds
 */

#include "../include/test_common.h"
#include <brotli/encode.h>
#include <brotli/decode.h>

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;
typedef int             ngx_flag_t;

#define NGX_OK     0
#define NGX_ERROR -1
#define NGX_AGAIN -2

#define ngx_memcpy memcpy
#define NGX_MAX_SIZE_T_VALUE SIZE_MAX

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

/* Fill buffer with pseudo-random bytes from the current PRNG state */
static void
fill_random(u_char *buf, size_t len)
{
    size_t i;

    for (i = 0; i < len; i++) {
        buf[i] = (u_char)(prng_next() & 0xFF);
    }
}

/* ----------------------------------------------------------------
 * Full-buffer decompression using BrotliDecoderDecompress.
 *
 * Returns 0 on success, -1 on failure.
 * On success, *out and *out_len are set (caller must free *out).
 * ---------------------------------------------------------------- */

static int
decompress_fullbuffer(const u_char *compressed, size_t compressed_len,
    size_t expected_len, u_char **out, size_t *out_len)
{
    size_t  decoded_size;
    u_char *buf;

    /*
     * Allocate a buffer large enough for the expected output.
     * Add a margin for safety.
     */
    decoded_size = expected_len + 256;
    buf = (u_char *) malloc(decoded_size);
    if (buf == NULL) {
        return -1;
    }

    if (BrotliDecoderDecompress(compressed_len, compressed,
            &decoded_size, buf)
        != BROTLI_DECODER_RESULT_SUCCESS)
    {
        free(buf);
        return -1;
    }

    *out = buf;
    *out_len = decoded_size;
    return 0;
}

/* ----------------------------------------------------------------
 * Streaming decompression using BrotliDecoderDecompressStream.
 *
 * Feeds the compressed data in random non-empty chunks determined
 * by the current PRNG state.  Accumulates output into a single
 * buffer.
 *
 * Returns 0 on success, -1 on failure.
 * On success, *out and *out_len are set (caller must free *out).
 * ---------------------------------------------------------------- */

static int
decompress_streaming(const u_char *compressed, size_t compressed_len,
    size_t expected_len, u_char **out, size_t *out_len)
{
    BrotliDecoderState *state;
    u_char             *result_buf;
    size_t              result_cap;
    size_t              result_len;
    const u_char       *next_in;
    size_t              avail_in;
    size_t              offset;
    size_t              chunk_size;
    BrotliDecoderResult brc;
    int                 finished;

    state = BrotliDecoderCreateInstance(NULL, NULL, NULL);
    if (state == NULL) {
        return -1;
    }

    result_cap = expected_len + 256;
    result_buf = (u_char *) malloc(result_cap);
    if (result_buf == NULL) {
        BrotliDecoderDestroyInstance(state);
        return -1;
    }
    result_len = 0;
    finished = 0;
    offset = 0;

    while (offset < compressed_len && !finished) {
        /* Random non-empty chunk size: 1 to remaining */
        size_t remaining = compressed_len - offset;

        chunk_size = (size_t)((prng_next() % remaining) + 1);
        next_in = compressed + offset;
        avail_in = chunk_size;

        while (avail_in > 0) {
            u_char  tmp_out[4096];
            u_char *next_out = tmp_out;
            size_t  avail_out = sizeof(tmp_out);

            brc = BrotliDecoderDecompressStream(state,
                &avail_in, &next_in, &avail_out, &next_out, NULL);

            if (brc == BROTLI_DECODER_RESULT_ERROR) {
                free(result_buf);
                BrotliDecoderDestroyInstance(state);
                return -1;
            }

            /* Copy produced output */
            {
                size_t produced = sizeof(tmp_out) - avail_out;

                if (produced > 0) {
                    if (result_len + produced > result_cap) {
                        result_cap = (result_len + produced) * 2;
                        result_buf = (u_char *) realloc(
                            result_buf, result_cap);
                        if (result_buf == NULL) {
                            BrotliDecoderDestroyInstance(state);
                            return -1;
                        }
                    }
                    memcpy(result_buf + result_len,
                        tmp_out, produced);
                    result_len += produced;
                }
            }

            if (brc == BROTLI_DECODER_RESULT_SUCCESS) {
                finished = 1;
                break;
            }

            if (brc == BROTLI_DECODER_RESULT_NEEDS_MORE_INPUT) {
                break;
            }
            /* NEEDS_MORE_OUTPUT: loop to drain */
        }

        offset += (chunk_size - avail_in);
    }

    /* Drain any remaining output after all input consumed */
    if (!finished) {
        for (;;) {
            u_char  tmp_out[4096];
            u_char *next_out = tmp_out;
            size_t  avail_out = sizeof(tmp_out);
            size_t  zero_in = 0;
            const u_char *null_in = NULL;

            brc = BrotliDecoderDecompressStream(state,
                &zero_in, &null_in, &avail_out, &next_out, NULL);

            {
                size_t produced = sizeof(tmp_out) - avail_out;

                if (produced > 0) {
                    if (result_len + produced > result_cap) {
                        result_cap = (result_len + produced) * 2;
                        result_buf = (u_char *) realloc(
                            result_buf, result_cap);
                        if (result_buf == NULL) {
                            BrotliDecoderDestroyInstance(state);
                            return -1;
                        }
                    }
                    memcpy(result_buf + result_len,
                        tmp_out, produced);
                    result_len += produced;
                }
            }

            if (brc == BROTLI_DECODER_RESULT_SUCCESS) {
                finished = 1;
                break;
            }
            if (brc == BROTLI_DECODER_RESULT_ERROR
                || brc == BROTLI_DECODER_RESULT_NEEDS_MORE_INPUT)
            {
                break;
            }
        }
    }

    if (!finished) {
        free(result_buf);
        BrotliDecoderDestroyInstance(state);
        return -1;
    }

    BrotliDecoderDestroyInstance(state);
    *out = result_buf;
    *out_len = result_len;
    return 0;
}

/* ----------------------------------------------------------------
 * Property 6: Streaming-vs-Full-Buffer Equivalence
 *
 * For each iteration:
 *   1. Generate random plaintext (1 byte to 64 KiB)
 *   2. Compress with BrotliEncoderCompress
 *   3. Decompress streaming (random chunk splits)
 *   4. Decompress full-buffer (one-shot)
 *   5. Assert byte-identical results
 *
 * Coverage: executable Brotli streaming behavior
 * ---------------------------------------------------------------- */

#define PROPERTY6_ITERATIONS     200
#define PROPERTY6_MAX_TEXT_SIZE  (64 * 1024)

static void
test_property6_streaming_vs_fullbuffer_equivalence(void)
{
    int       iter;
    size_t    text_len;
    u_char   *plaintext;
    u_char   *compressed;
    size_t    compressed_cap;
    size_t    compressed_len;
    u_char   *stream_out;
    size_t    stream_out_len;
    u_char   *full_out;
    size_t    full_out_len;
    int       rc_stream;
    int       rc_full;
    int       pass_count;

    TEST_SUBSECTION(
        "Property 6: Streaming-vs-Full-Buffer Equivalence "
        "(200 iterations, 1B-64KiB)");

    pass_count = 0;

    for (iter = 0; iter < PROPERTY6_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 42));

        /* Generate random plaintext size: 1 to 64KiB */
        text_len = (size_t)((prng_next() % PROPERTY6_MAX_TEXT_SIZE) + 1);

        plaintext = (u_char *) malloc(text_len);
        TEST_ASSERT(plaintext != NULL,
            "plaintext allocation must succeed");
        fill_random(plaintext, text_len);

        /*
         * Compress.  BrotliEncoderMaxCompressedSize gives an upper
         * bound on the compressed output size.
         */
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

        /*
         * Decompress via streaming (random chunk splits).
         * Use a different seed for chunk randomization so the
         * chunk pattern varies per iteration.
         */
        prng_seed((unsigned int)(iter * 7919 + 1013));
        rc_stream = decompress_streaming(compressed, compressed_len,
            text_len, &stream_out, &stream_out_len);
        TEST_ASSERT(rc_stream == 0,
            "streaming decompression must succeed");

        /* Decompress via full-buffer (one-shot) */
        rc_full = decompress_fullbuffer(compressed, compressed_len,
            text_len, &full_out, &full_out_len);
        TEST_ASSERT(rc_full == 0,
            "full-buffer decompression must succeed");

        /* Assert length equality */
        TEST_ASSERT(stream_out_len == full_out_len,
            "streaming and full-buffer output lengths "
            "must be identical");

        /* Assert both match original plaintext length */
        TEST_ASSERT(stream_out_len == text_len,
            "decompressed output must equal original length");

        /* Assert byte-identical output */
        TEST_ASSERT(
            MEM_EQ(stream_out, full_out, stream_out_len),
            "streaming output must be byte-identical "
            "to full-buffer output");

        /* Also verify both match original plaintext */
        TEST_ASSERT(
            MEM_EQ(stream_out, plaintext, text_len),
            "decompressed output must match original "
            "plaintext");

        free(plaintext);
        free(compressed);
        free(stream_out);
        free(full_out);
        pass_count++;
    }

    TEST_ASSERT(pass_count == PROPERTY6_ITERATIONS,
        "all iterations must pass");
    TEST_PASS(
        "Property 6: streaming-vs-full-buffer equivalence "
        "verified (200 iterations, 1B-64KiB)");
}

/* ----------------------------------------------------------------
 * Property 6b: Single-byte chunk streaming equivalence
 *
 * Feed compressed data one byte at a time (worst-case fragmentation)
 * and verify output matches full-buffer decompression.
 * ---------------------------------------------------------------- */

#define PROPERTY6B_ITERATIONS  50

static int
decompress_streaming_single_byte(const u_char *compressed,
    size_t compressed_len, size_t expected_len,
    u_char **out, size_t *out_len)
{
    BrotliDecoderState *state;
    u_char             *result_buf;
    size_t              result_cap;
    size_t              result_len;
    size_t              i;
    BrotliDecoderResult brc;

    state = BrotliDecoderCreateInstance(NULL, NULL, NULL);
    if (state == NULL) {
        return -1;
    }

    result_cap = expected_len + 256;
    result_buf = (u_char *) malloc(result_cap);
    if (result_buf == NULL) {
        BrotliDecoderDestroyInstance(state);
        return -1;
    }
    result_len = 0;

    for (i = 0; i < compressed_len; i++) {
        const u_char *next_in = &compressed[i];
        size_t avail_in = 1;

        while (avail_in > 0 || BrotliDecoderHasMoreOutput(state)) {
            u_char  tmp_out[4096];
            u_char *next_out = tmp_out;
            size_t  avail_out = sizeof(tmp_out);

            brc = BrotliDecoderDecompressStream(state,
                &avail_in, &next_in, &avail_out, &next_out, NULL);

            if (brc == BROTLI_DECODER_RESULT_ERROR) {
                free(result_buf);
                BrotliDecoderDestroyInstance(state);
                return -1;
            }

            {
                size_t produced = sizeof(tmp_out) - avail_out;

                if (produced > 0) {
                    if (result_len + produced > result_cap) {
                        result_cap = (result_len + produced) * 2;
                        result_buf = (u_char *) realloc(
                            result_buf, result_cap);
                        if (result_buf == NULL) {
                            BrotliDecoderDestroyInstance(state);
                            return -1;
                        }
                    }
                    memcpy(result_buf + result_len,
                        tmp_out, produced);
                    result_len += produced;
                }
            }

            if (brc == BROTLI_DECODER_RESULT_SUCCESS
                || brc == BROTLI_DECODER_RESULT_NEEDS_MORE_INPUT)
            {
                break;
            }
        }

        if (BrotliDecoderIsFinished(state)) {
            break;
        }
    }

    if (!BrotliDecoderIsFinished(state)) {
        free(result_buf);
        BrotliDecoderDestroyInstance(state);
        return -1;
    }

    BrotliDecoderDestroyInstance(state);
    *out = result_buf;
    *out_len = result_len;
    return 0;
}

static void
test_property6b_single_byte_streaming_equivalence(void)
{
    int       iter;
    size_t    text_len;
    u_char   *plaintext;
    u_char   *compressed;
    size_t    compressed_cap;
    size_t    compressed_len;
    u_char   *stream_out;
    size_t    stream_out_len;
    u_char   *full_out;
    size_t    full_out_len;
    int       rc_stream;
    int       rc_full;

    TEST_SUBSECTION(
        "Property 6b: Single-byte streaming equivalence "
        "(50 iterations, 1B-4KiB)");

    for (iter = 0; iter < PROPERTY6B_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 777));

        /* Smaller sizes for byte-at-a-time (keep runtime bounded) */
        text_len = (size_t)((prng_next() % 4096) + 1);

        plaintext = (u_char *) malloc(text_len);
        TEST_ASSERT(plaintext != NULL,
            "plaintext allocation must succeed");
        fill_random(plaintext, text_len);

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

        /* Streaming: single-byte chunks */
        rc_stream = decompress_streaming_single_byte(
            compressed, compressed_len,
            text_len, &stream_out, &stream_out_len);
        TEST_ASSERT(rc_stream == 0,
            "single-byte streaming decompression must succeed");

        /* Full-buffer */
        rc_full = decompress_fullbuffer(compressed, compressed_len,
            text_len, &full_out, &full_out_len);
        TEST_ASSERT(rc_full == 0,
            "full-buffer decompression must succeed");

        /* Assert equivalence */
        TEST_ASSERT(stream_out_len == full_out_len,
            "single-byte streaming and full-buffer lengths "
            "must match");
        TEST_ASSERT(
            MEM_EQ(stream_out, full_out, stream_out_len),
            "single-byte streaming output must be "
            "byte-identical to full-buffer");
        TEST_ASSERT(
            MEM_EQ(stream_out, plaintext, text_len),
            "output must match original plaintext");

        free(plaintext);
        free(compressed);
        free(stream_out);
        free(full_out);
    }

    TEST_PASS(
        "Property 6b: single-byte streaming equivalence "
        "verified (50 iterations)");
}

/* ----------------------------------------------------------------
 * Property 6c: Chunk boundary independence
 *
 * For the same compressed payload, different random chunk split
 * patterns must all produce byte-identical output.
 * ---------------------------------------------------------------- */

#define PROPERTY6C_ITERATIONS    50
#define PROPERTY6C_SPLIT_RUNS     5

static void
test_property6c_chunk_boundary_independence(void)
{
    int       iter;
    size_t    text_len;
    u_char   *plaintext;
    u_char   *compressed;
    size_t    compressed_cap;
    size_t    compressed_len;
    u_char   *reference_out;
    size_t    reference_len;
    u_char   *variant_out;
    size_t    variant_len;
    int       rc;
    int       split;

    TEST_SUBSECTION(
        "Property 6c: Chunk boundary independence "
        "(50 payloads × 5 split patterns)");

    for (iter = 0; iter < PROPERTY6C_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 3333));

        text_len = (size_t)((prng_next() % (32 * 1024)) + 1);

        plaintext = (u_char *) malloc(text_len);
        TEST_ASSERT(plaintext != NULL,
            "plaintext allocation must succeed");
        fill_random(plaintext, text_len);

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

        /* Reference: full-buffer decompression */
        rc = decompress_fullbuffer(compressed, compressed_len,
            text_len, &reference_out, &reference_len);
        TEST_ASSERT(rc == 0,
            "reference full-buffer decompression must succeed");

        /* Multiple streaming runs with different chunk splits */
        for (split = 0; split < PROPERTY6C_SPLIT_RUNS; split++) {
            prng_seed(
                (unsigned int)(iter * 1000 + split * 137 + 9001));

            rc = decompress_streaming(compressed, compressed_len,
                text_len, &variant_out, &variant_len);
            TEST_ASSERT(rc == 0,
                "variant streaming decompression must succeed");
            TEST_ASSERT(variant_len == reference_len,
                "variant length must match reference");
            TEST_ASSERT(
                MEM_EQ(variant_out, reference_out, reference_len),
                "variant output must be byte-identical "
                "to reference");
            free(variant_out);
        }

        free(plaintext);
        free(compressed);
        free(reference_out);
    }

    TEST_PASS(
        "Property 6c: chunk boundary independence verified "
        "(50 × 5 splits)");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    UNUSED(g_prng_state);

    TEST_SECTION(
        "Feature: Brotli streaming decompression\n"
        "Property 6: Streaming-vs-Full-Buffer Equivalence\n"
        "Coverage: executable Brotli streaming behavior.");

    /* Core property: random sizes, random chunk splits */
    test_property6_streaming_vs_fullbuffer_equivalence();

    /* Worst-case: single-byte chunk feeds */
    test_property6b_single_byte_streaming_equivalence();

    /* Independence: same payload, different splits */
    test_property6c_chunk_boundary_independence();

    printf("\n");
    TEST_PASS(
        "streaming_equiv_brotli_property: all property "
        "tests passed");
    return 0;
}
