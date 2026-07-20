/*
 * Test: decomp_routing_property
 *
 * Property-based tests for streaming decompression routing
 * correctness (Property 4).
 *
 * Feature: 0.9.1-performance-optimization
 * Property 4: Streaming Decompression Routing Correctness
 *
 * Validates: Requirements 4.1, 4.2
 *
 * The routing function selects STREAMING decompression iff ALL
 * four conditions hold simultaneously:
 *   1. auto_decompress is ON
 *   2. Streaming engine is selected (not forced to full-buffer)
 *   3. cache_validation is NOT full
 *   4. Encoding is supported by streaming decompressor:
 *      - deflate    -> SUPPORTED (zlib-wrapped or raw)
 *      - gzip       -> SUPPORTED (gzip-wrapped inflate)
 *      - brotli     -> SUPPORTED (when NGX_HTTP_BROTLI defined)
 *      - unknown    -> BYPASS (no decompression at all)
 *
 * If ANY condition is false -> full-buffer decompression for a known coding.
 * If ALL four hold -> streaming decompression.
 * If encoding is unknown -> bypass (no decompression).
 */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef int             ngx_flag_t;

/* ----------------------------------------------------------------
 * Compression type enum (mirrors production)
 * ---------------------------------------------------------------- */

typedef enum {
    NGX_HTTP_MARKDOWN_COMPRESSION_NONE    = 0,
    NGX_HTTP_MARKDOWN_COMPRESSION_GZIP    = 1,
    NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE = 2,
    NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI  = 3,
    NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN = 4
} ngx_http_markdown_compression_type_e;

/* ----------------------------------------------------------------
 * Cache validation mode enum
 * ---------------------------------------------------------------- */

typedef enum {
    NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE    = 0,
    NGX_HTTP_MARKDOWN_CACHE_VALIDATION_ETAG    = 1,
    NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL    = 2
} ngx_http_markdown_cache_validation_e;

/* ----------------------------------------------------------------
 * Decompression routing decision enum
 * ---------------------------------------------------------------- */

typedef enum {
    NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING    = 0,
    NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER   = 1,
    NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS       = 2
} ngx_http_markdown_decomp_route_t;

/* ----------------------------------------------------------------
 * Production function under test (inlined routing logic)
 *
 * This replicates the exact production routing decision:
 *   - Unknown encoding -> BYPASS (no decompression)
 *   - auto_decompress OFF -> FULLBUFFER (Req 4.1)
 *   - Streaming engine not selected -> FULLBUFFER
 *   - cache_validation == full -> FULLBUFFER (Req 4.2)
 *   - Encoding not supported by streaming (brotli without NGX_HTTP_BROTLI) -> FULLBUFFER
 *   - All four conditions met -> STREAMING
 * ---------------------------------------------------------------- */

static ngx_http_markdown_decomp_route_t
ngx_http_markdown_decomp_routing_decision(
    ngx_flag_t auto_decompress,
    ngx_flag_t streaming_engine_selected,
    ngx_http_markdown_cache_validation_e cache_validation,
    ngx_http_markdown_compression_type_e encoding)
{
    /* Unknown encoding -> bypass (no decompression at all) */
    if (encoding == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN
        || encoding == NGX_HTTP_MARKDOWN_COMPRESSION_NONE) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS;
    }

    /* Condition 1: auto_decompress must be ON */
    if (auto_decompress != 1) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /* Condition 2: streaming engine must be selected */
    if (!streaming_engine_selected) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /* Condition 3: cache_validation must NOT be full (Req 4.2) */
    if (cache_validation == NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /*
     * Condition 4: encoding must be supported by streaming
     * decompressor.  Gzip, deflate, and Brotli (when compiled)
     * are supported in 0.9.1.
     */
    if (encoding != NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE
        && encoding != NGX_HTTP_MARKDOWN_COMPRESSION_GZIP
#ifdef NGX_HTTP_BROTLI
        && encoding != NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
#endif
        ) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /* All four conditions hold -> streaming decompression */
    return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING;
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

/* ----------------------------------------------------------------
 * Oracle function: computes expected routing from inputs.
 *
 * This is the specification encoded as code:
 *   - BYPASS if encoding is NONE or UNKNOWN
 *   - STREAMING iff all four conditions hold
 *   - FULLBUFFER otherwise
 * ---------------------------------------------------------------- */

static ngx_http_markdown_decomp_route_t
expected_routing(
    ngx_flag_t auto_decompress,
    ngx_flag_t streaming_engine_selected,
    ngx_http_markdown_cache_validation_e cache_validation,
    ngx_http_markdown_compression_type_e encoding)
{
    /* Bypass: no decompression needed */
    if (encoding == NGX_HTTP_MARKDOWN_COMPRESSION_NONE
        || encoding == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS;
    }

    /*
     * Streaming iff ALL four conditions:
     *   1. auto_decompress ON
     *   2. streaming engine selected
     *   3. cache_validation != full
     *   4. encoding supported (gzip, deflate, or brotli when compiled)
     */
    if (auto_decompress == 1
        && streaming_engine_selected
        && cache_validation != NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL
        && (encoding == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE
            || encoding == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP
#ifdef NGX_HTTP_BROTLI
            || encoding == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
#endif
            )) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING;
    }

    /* Otherwise full-buffer */
    return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
}

/* ----------------------------------------------------------------
 * Helper: routing name for diagnostics
 * ---------------------------------------------------------------- */

static const char *
route_name(ngx_http_markdown_decomp_route_t r)
{
    switch (r) {
    case NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING:
        return "STREAMING";
    case NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER:
        return "FULLBUFFER";
    case NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS:
        return "BYPASS";
    default:
        return "UNKNOWN";
    }
}

/* ----------------------------------------------------------------
 * Property 4: Exhaustive test of all combinations
 *
 * Input space:
 *   auto_decompress: {0, 1}                      = 2
 *   streaming_engine_selected: {0, 1}            = 2
 *   cache_validation: {NONE, ETAG, FULL}         = 3
 *   encoding: {NONE, GZIP, DEFLATE, BROTLI, UNKNOWN} = 5
 *
 * Total combinations: 2 * 2 * 3 * 5 = 60
 * ---------------------------------------------------------------- */

static void
test_property4_exhaustive_routing_matrix(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_decomp_route_t expect;

    ngx_flag_t auto_decomp_values[] = { 0, 1 };
    ngx_flag_t stream_eng_values[]  = { 0, 1 };
    ngx_http_markdown_cache_validation_e cv_values[] = {
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_ETAG,
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL
    };
    ngx_http_markdown_compression_type_e enc_values[] = {
        NGX_HTTP_MARKDOWN_COMPRESSION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
        NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN
    };

    size_t ai, si, ci, ei;
    int    count;

    TEST_SUBSECTION(
        "Property 4: Exhaustive 60-combination "
        "routing matrix");

    count = 0;
    for (ai = 0; ai < ARRAY_SIZE(auto_decomp_values); ai++) {
        for (si = 0; si < ARRAY_SIZE(stream_eng_values); si++) {
            for (ci = 0; ci < ARRAY_SIZE(cv_values); ci++) {
                for (ei = 0; ei < ARRAY_SIZE(enc_values); ei++) {
                    result =
                        ngx_http_markdown_decomp_routing_decision(
                            auto_decomp_values[ai],
                            stream_eng_values[si],
                            cv_values[ci],
                            enc_values[ei]);
                    expect = expected_routing(
                        auto_decomp_values[ai],
                        stream_eng_values[si],
                        cv_values[ci],
                        enc_values[ei]);
                    TEST_ASSERT(result == expect,
                        "routing must match oracle");
                    count++;
                }
            }
        }
    }

    TEST_ASSERT(count == 60,
        "must test exactly 60 combinations");
    TEST_PASS(
        "Property 4: all 60 exhaustive combinations "
        "match oracle");
}

/* ----------------------------------------------------------------
 * Named cases for traceability
 * ---------------------------------------------------------------- */

static void
test_property4_named_cases(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "Property 4: Named cases from requirements");

    /* Req 4.1: All conditions met with deflate -> STREAMING */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "all conditions met (deflate) -> STREAMING "
        "(Req 4.1)");

    /* Req 4.2: cache_validation=full -> FULLBUFFER */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "cache_validation=full -> FULLBUFFER (Req 4.2)");

    /* auto_decompress OFF -> FULLBUFFER */
    result = ngx_http_markdown_decomp_routing_decision(
        0, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "auto_decompress OFF -> FULLBUFFER");

    /* Streaming engine not selected -> FULLBUFFER */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 0, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "streaming engine not selected -> FULLBUFFER");

    /* Gzip supported in streaming -> STREAMING */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "gzip encoding -> STREAMING");

    /* Brotli routing depends on compile-time flag */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);
#ifdef NGX_HTTP_BROTLI
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "brotli encoding -> STREAMING (NGX_HTTP_BROTLI defined)");
#else
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "brotli encoding -> FULLBUFFER (NGX_HTTP_BROTLI not defined)");
#endif

    /* Unknown encoding -> BYPASS */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS,
        "unknown encoding -> BYPASS");

    /* No encoding -> BYPASS */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_NONE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS,
        "no encoding -> BYPASS");

    /* cache_validation=ETAG with deflate -> STREAMING */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_ETAG,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "cache_validation=ETAG (not full) -> STREAMING");

    TEST_PASS(
        "Property 4: all named cases pass");
}

/* ----------------------------------------------------------------
 * Random sequence property test
 *
 * Generate many random input tuples and verify the routing
 * function matches the oracle for each.
 * ---------------------------------------------------------------- */

#define RANDOM_ITERATIONS  500
#define RANDOM_SEQ_LEN      50

static void
test_property4_random_sequences(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_decomp_route_t expect;
    ngx_flag_t auto_decomp;
    ngx_flag_t stream_eng;
    ngx_http_markdown_cache_validation_e cv;
    ngx_http_markdown_compression_type_e enc;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 4: Random input sequences "
        "(500 seeds x 50 inputs)");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 42));

        for (j = 0; j < RANDOM_SEQ_LEN; j++) {
            auto_decomp = (ngx_flag_t)(prng_next() % 2);
            stream_eng = (ngx_flag_t)(prng_next() % 2);
            cv = (ngx_http_markdown_cache_validation_e)
                (prng_next() % 3);
            enc = (ngx_http_markdown_compression_type_e)
                (prng_next() % 5);

            result =
                ngx_http_markdown_decomp_routing_decision(
                    auto_decomp, stream_eng, cv, enc);
            expect = expected_routing(
                auto_decomp, stream_eng, cv, enc);

            TEST_ASSERT(result == expect,
                "random input must match oracle");
        }
    }

    TEST_PASS(
        "Property 4: oracle match verified for 25000 "
        "random inputs");
}

/* ----------------------------------------------------------------
 * Contrapositive: STREAMING implies all four conditions
 * ---------------------------------------------------------------- */

static void
test_property4_streaming_implies_all_conditions(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_flag_t auto_decomp;
    ngx_flag_t stream_eng;
    ngx_http_markdown_cache_validation_e cv;
    ngx_http_markdown_compression_type_e enc;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 4: STREAMING implies all four "
        "conditions met (contrapositive)");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 9999));

        for (j = 0; j < RANDOM_SEQ_LEN; j++) {
            auto_decomp = (ngx_flag_t)(prng_next() % 2);
            stream_eng = (ngx_flag_t)(prng_next() % 2);
            cv = (ngx_http_markdown_cache_validation_e)
                (prng_next() % 3);
            enc = (ngx_http_markdown_compression_type_e)
                (prng_next() % 5);

            result =
                ngx_http_markdown_decomp_routing_decision(
                    auto_decomp, stream_eng, cv, enc);

            if (result
                == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING) {
                TEST_ASSERT(auto_decomp == 1,
                    "STREAMING implies "
                    "auto_decompress ON");
                TEST_ASSERT(stream_eng == 1,
                    "STREAMING implies "
                    "streaming engine selected");
                TEST_ASSERT(cv
                    != NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
                    "STREAMING implies "
                    "cache_validation != full");
                TEST_ASSERT((enc
                    == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE)
                    || (enc
                    == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP)
#ifdef NGX_HTTP_BROTLI
                    || (enc
                    == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI)
#endif
                    ,
                    "STREAMING implies "
                    "supported encoding");
            }
        }
    }

    TEST_PASS(
        "Property 4: STREAMING -> all conditions "
        "(25000 inputs)");
}

/* ----------------------------------------------------------------
 * Contrapositive: FULLBUFFER implies at least one condition
 * is NOT met (excludes BYPASS cases)
 * ---------------------------------------------------------------- */

static void
test_property4_fullbuffer_implies_condition_violated(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_flag_t auto_decomp;
    ngx_flag_t stream_eng;
    ngx_http_markdown_cache_validation_e cv;
    ngx_http_markdown_compression_type_e enc;
    int any_violated;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 4: FULLBUFFER implies at least one "
        "condition violated");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 5555));

        for (j = 0; j < RANDOM_SEQ_LEN; j++) {
            auto_decomp = (ngx_flag_t)(prng_next() % 2);
            stream_eng = (ngx_flag_t)(prng_next() % 2);
            cv = (ngx_http_markdown_cache_validation_e)
                (prng_next() % 3);
            enc = (ngx_http_markdown_compression_type_e)
                (prng_next() % 5);

            result =
                ngx_http_markdown_decomp_routing_decision(
                    auto_decomp, stream_eng, cv, enc);

            if (result
                == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER) {
                /*
                 * FULLBUFFER means at least one of:
                 *  - auto_decompress != 1
                 *  - streaming_engine not selected
                 *  - cache_validation == FULL
                 *  - encoding is neither DEFLATE nor GZIP
                 *    (but must be a real encoding,
                 *     not NONE/UNKNOWN which is BYPASS)
                 */
                any_violated =
                    (auto_decomp != 1)
                    || (!stream_eng)
                    || (cv
                       == NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL)
                    || ((enc
                        != NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE)
                       && (enc
                        != NGX_HTTP_MARKDOWN_COMPRESSION_GZIP));
                TEST_ASSERT(any_violated,
                    "FULLBUFFER implies at least one "
                    "condition violated");
            }
        }
    }

    TEST_PASS(
        "Property 4: FULLBUFFER -> condition violated "
        "(25000 inputs)");
}

/* ----------------------------------------------------------------
 * BYPASS only occurs for NONE or UNKNOWN encoding
 * ---------------------------------------------------------------- */

static void
test_property4_bypass_implies_no_decompression_needed(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_flag_t auto_decomp;
    ngx_flag_t stream_eng;
    ngx_http_markdown_cache_validation_e cv;
    ngx_http_markdown_compression_type_e enc;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 4: BYPASS implies encoding is "
        "NONE or UNKNOWN");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 1111));

        for (j = 0; j < RANDOM_SEQ_LEN; j++) {
            auto_decomp = (ngx_flag_t)(prng_next() % 2);
            stream_eng = (ngx_flag_t)(prng_next() % 2);
            cv = (ngx_http_markdown_cache_validation_e)
                (prng_next() % 3);
            enc = (ngx_http_markdown_compression_type_e)
                (prng_next() % 5);

            result =
                ngx_http_markdown_decomp_routing_decision(
                    auto_decomp, stream_eng, cv, enc);

            if (result
                == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS) {
                TEST_ASSERT(
                    enc == NGX_HTTP_MARKDOWN_COMPRESSION_NONE
                    || enc
                       == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN,
                    "BYPASS implies NONE or UNKNOWN "
                    "encoding");
            }
        }
    }

    TEST_PASS(
        "Property 4: BYPASS -> NONE/UNKNOWN encoding "
        "(25000 inputs)");
}

/* ----------------------------------------------------------------
 * cache_validation=full always forces FULLBUFFER for
 * decompressible encodings (Req 4.2 direct test)
 * ---------------------------------------------------------------- */

static void
test_property4_full_cache_always_fullbuffer(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_compression_type_e enc_decomp[] = {
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
    };
    size_t i;

    TEST_SUBSECTION(
        "Property 4: cache_validation=full always "
        "forces FULLBUFFER (Req 4.2)");

    for (i = 0; i < ARRAY_SIZE(enc_decomp); i++) {
        /* Even with all other conditions ideal */
        result = ngx_http_markdown_decomp_routing_decision(
            1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
            enc_decomp[i]);
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
            "cache_validation=full -> FULLBUFFER "
            "regardless of encoding");
    }

    TEST_PASS(
        "Property 4: full cache validation always "
        "forces FULLBUFFER");
}

/* ----------------------------------------------------------------
 * Only supported encodings (deflate, gzip, and Brotli when
 * NGX_HTTP_BROTLI is defined) can reach STREAMING in 0.9.1
 * ---------------------------------------------------------------- */

static void
test_property4_only_supported_encodings_reach_streaming(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_compression_type_e enc;
    int iter;
    size_t j;
    ngx_flag_t auto_decomp;
    ngx_flag_t stream_eng;
    ngx_http_markdown_cache_validation_e cv;

    TEST_SUBSECTION(
        "Property 4: only supported encodings can reach "
        "STREAMING");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 8888));

        for (j = 0; j < RANDOM_SEQ_LEN; j++) {
            auto_decomp = (ngx_flag_t)(prng_next() % 2);
            stream_eng = (ngx_flag_t)(prng_next() % 2);
            cv = (ngx_http_markdown_cache_validation_e)
                (prng_next() % 3);
            enc = (ngx_http_markdown_compression_type_e)
                (prng_next() % 5);

            result =
                ngx_http_markdown_decomp_routing_decision(
                    auto_decomp, stream_eng, cv, enc);

            if (result
                == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING) {
                TEST_ASSERT(
                    (enc
                     == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE)
                    || (enc
                        == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP)
#ifdef NGX_HTTP_BROTLI
                    || (enc
                        == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI)
#endif
                    ,
                    "STREAMING only reachable with "
                    "supported encodings");
            }
        }
    }

    TEST_PASS(
        "Property 4: only supported encodings reach "
        "STREAMING (25000 inputs)");
}

/* ================================================================
 * Routing decision correctness for Brotli
 *
 * Feature: Brotli streaming decompression
 * Property 1: Routing Decision Correctness
 *
 * Brotli routing follows all runtime eligibility conditions
 *
 * Oracle:
 *   STREAMING iff all four conditions hold AND codec is
 *   gzip/deflate/brotli(+compiled);
 *   FULLBUFFER when any condition violated for a known codec;
 *   BYPASS for NONE/UNKNOWN encoding.
 *
 * Minimum 200 iterations.
 * ================================================================ */

#define PROPERTY1_ITERATIONS  250
#define PROPERTY1_SEQ_LEN      50

/* ----------------------------------------------------------------
 * Property 1: Random routing correctness (oracle match)
 *
 * Generates random (auto_decompress, streaming_engine,
 * cache_validation, compression_type) tuples and verifies
 * the routing function matches the oracle.
 * ---------------------------------------------------------------- */

static void
test_property1_random_routing_correctness(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_decomp_route_t expect;
    ngx_flag_t auto_decomp;
    ngx_flag_t stream_eng;
    ngx_http_markdown_cache_validation_e cv;
    ngx_http_markdown_compression_type_e enc;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 1: Random routing correctness "
        "(250 seeds x 50 inputs)");

    for (iter = 0; iter < PROPERTY1_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 77777));

        for (j = 0; j < PROPERTY1_SEQ_LEN; j++) {
            auto_decomp = (ngx_flag_t)(prng_next() % 2);
            stream_eng = (ngx_flag_t)(prng_next() % 2);
            cv = (ngx_http_markdown_cache_validation_e)
                (prng_next() % 3);
            enc = (ngx_http_markdown_compression_type_e)
                (prng_next() % 5);

            result =
                ngx_http_markdown_decomp_routing_decision(
                    auto_decomp, stream_eng, cv, enc);
            expect = expected_routing(
                auto_decomp, stream_eng, cv, enc);

            TEST_ASSERT(result == expect,
                "Property 1: routing must match "
                "oracle");
        }
    }

    TEST_PASS(
        "Property 1: oracle match verified for 12500 "
        "random inputs");
}

/* ----------------------------------------------------------------
 * Property 1: Brotli-specific streaming eligibility
 *
 * Brotli + all conditions + NGX_HTTP_BROTLI ->
 *          STREAMING
 * ---------------------------------------------------------------- */

static void
test_property1_brotli_streaming_eligible(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_cache_validation_e cv_values[] = {
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_ETAG
    };
    size_t i;

    TEST_SUBSECTION(
        "Property 1: Brotli streaming eligibility "
        "");

    for (i = 0; i < ARRAY_SIZE(cv_values); i++) {
        result = ngx_http_markdown_decomp_routing_decision(
            1, 1, cv_values[i],
            NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);

#ifdef NGX_HTTP_BROTLI
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "Brotli + all conditions "
            "+ NGX_HTTP_BROTLI -> STREAMING");
#else
        TEST_ASSERT(
            result
            == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
            "Brotli + all conditions "
            "- NGX_HTTP_BROTLI -> FULLBUFFER");
#endif
    }

    TEST_PASS(
        "Property 1: Brotli streaming eligibility "
        "verified");
}

/* ----------------------------------------------------------------
 * Property 1: auto_decompress OFF -> not STREAMING
 *
 * ---------------------------------------------------------------- */

static void
test_property1_auto_decompress_off(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_compression_type_e enc_decomp[] = {
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
    };
    size_t i;

    TEST_SUBSECTION(
        "Property 1: auto_decompress OFF -> "
        "FULLBUFFER");

    for (i = 0; i < ARRAY_SIZE(enc_decomp); i++) {
        result = ngx_http_markdown_decomp_routing_decision(
            0, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            enc_decomp[i]);
        TEST_ASSERT(
            result
            == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
            "auto_decompress OFF -> "
            "FULLBUFFER for all codecs");
    }

    TEST_PASS(
        "Property 1: auto_decompress OFF forces "
        "FULLBUFFER");
}

/* ----------------------------------------------------------------
 * Property 1: streaming engine not selected -> FULLBUFFER
 *
 * ---------------------------------------------------------------- */

static void
test_property1_streaming_not_selected(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_compression_type_e enc_decomp[] = {
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
    };
    size_t i;

    TEST_SUBSECTION(
        "Property 1: streaming engine not selected "
        "-> FULLBUFFER");

    for (i = 0; i < ARRAY_SIZE(enc_decomp); i++) {
        result = ngx_http_markdown_decomp_routing_decision(
            1, 0, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            enc_decomp[i]);
        TEST_ASSERT(
            result
            == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
            "streaming not selected -> "
            "FULLBUFFER for all codecs");
    }

    TEST_PASS(
        "Property 1: streaming not selected forces "
        "FULLBUFFER");
}

/* ----------------------------------------------------------------
 * Property 1: cache_validation=full -> FULLBUFFER
 *
 * ---------------------------------------------------------------- */

static void
test_property1_cache_validation_full(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_compression_type_e enc_decomp[] = {
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
    };
    size_t i;

    TEST_SUBSECTION(
        "Property 1: cache_validation=full -> "
        "FULLBUFFER");

    for (i = 0; i < ARRAY_SIZE(enc_decomp); i++) {
        result = ngx_http_markdown_decomp_routing_decision(
            1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
            enc_decomp[i]);
        TEST_ASSERT(
            result
            == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
            "cache_validation=full -> "
            "FULLBUFFER for all codecs");
    }

    TEST_PASS(
        "Property 1: cache_validation=full forces "
        "FULLBUFFER");
}

/* ----------------------------------------------------------------
 * Property 1: Contrapositive — STREAMING implies Brotli
 * is included in supported set when NGX_HTTP_BROTLI is
 * defined
 * ---------------------------------------------------------------- */

static void
test_property1_streaming_includes_brotli(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_flag_t auto_decomp;
    ngx_flag_t stream_eng;
    ngx_http_markdown_cache_validation_e cv;
    ngx_http_markdown_compression_type_e enc;
    int iter;
    size_t j;
    int brotli_streaming_seen;

    TEST_SUBSECTION(
        "Property 1: STREAMING reachable with Brotli "
        "");

    brotli_streaming_seen = 0;

    for (iter = 0; iter < PROPERTY1_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 33333));

        for (j = 0; j < PROPERTY1_SEQ_LEN; j++) {
            auto_decomp = (ngx_flag_t)(prng_next() % 2);
            stream_eng = (ngx_flag_t)(prng_next() % 2);
            cv = (ngx_http_markdown_cache_validation_e)
                (prng_next() % 3);
            enc = (ngx_http_markdown_compression_type_e)
                (prng_next() % 5);

            result =
                ngx_http_markdown_decomp_routing_decision(
                    auto_decomp, stream_eng, cv, enc);

            if (result
                == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING
                && enc
                   == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI) {
                brotli_streaming_seen = 1;
            }
        }
    }

#ifdef NGX_HTTP_BROTLI
    TEST_ASSERT(brotli_streaming_seen,
        "Brotli must reach STREAMING "
        "when NGX_HTTP_BROTLI defined");
#else
    TEST_ASSERT(!brotli_streaming_seen,
        "Brotli must NOT reach STREAMING "
        "when NGX_HTTP_BROTLI not defined");
#endif

    TEST_PASS(
        "Property 1: Brotli streaming reachability "
        "matches compile-time guard");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    UNUSED(route_name);

    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Property 4: Streaming Decompression Routing "
        "Correctness\n"
        "Validates: Requirements 4.1, 4.2");

    /* Exhaustive tests */
    test_property4_exhaustive_routing_matrix();
    test_property4_named_cases();

    /* Random sequence property tests */
    test_property4_random_sequences();

    /* Contrapositive / logical property tests */
    test_property4_streaming_implies_all_conditions();
    test_property4_fullbuffer_implies_condition_violated();
    test_property4_bypass_implies_no_decompression_needed();

    /* Requirement-specific property tests */
    test_property4_full_cache_always_fullbuffer();
    test_property4_only_supported_encodings_reach_streaming();

    /* ============================================================
     * Property 1: Routing Decision Correctness
     *
     *
     * Brotli routing follows all runtime eligibility conditions
     * ============================================================ */
    TEST_SECTION(
        "Feature: Brotli streaming decompression\n"
        "Property 1: Routing Decision Correctness\n"
        "Brotli routing follows all runtime eligibility conditions");

    test_property1_random_routing_correctness();
    test_property1_brotli_streaming_eligible();
    test_property1_auto_decompress_off();
    test_property1_streaming_not_selected();
    test_property1_cache_validation_full();
    test_property1_streaming_includes_brotli();

    printf("\n");
    TEST_PASS(
        "decomp_routing_property: all property tests "
        "passed");
    return 0;
}
