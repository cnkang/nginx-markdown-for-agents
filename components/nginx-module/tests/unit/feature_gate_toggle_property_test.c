/*
 /* * Test: feature_gate_toggle_property
  *
  * Property-based tests for dynamic feature gate toggle behavior
  * (Property 13) and Brotli compile-time feature gate toggle
  * (Property 1 subset).
  *
  * Feature: 0.9.1-performance-optimization
  * Property 13: Dynamic Feature Gate Toggle
  * Property 1 (subset): Brotli Feature Gate Toggle — verifies that
  *   Brotli streaming decompression is enabled/disabled at compile
  *   time based on NGX_HTTP_BROTLI, with full-buffer fallback when
  *   disabled.
  *
  * Validates: Requirements 10.4, 10.5
  *
  * Verifies:
  *   1. HUP reload with zero_copy OFF causes all subsequent chunks
  *      to use pool-copy path (hybrid_decision always returns
  *      POOL_COPY when gate is OFF).
  *   2. Profile switch from streaming_first to balanced/strict_cache
  *      disables streaming decompression routing without restart.
  *   3. Brotli compile-time gate: with NGX_HTTP_BROTLI defined,
  *      Brotli routes to STREAMING; without it, Brotli always routes
  *      to FULLBUFFER.
  *
  * The test models directive changes and verifies that after toggle,
  * the production decision functions produce the correct results
  * across many random input sequences.
  */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef intptr_t        ngx_flag_t;

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
 * Performance profile enum (mirrors production)
 * ---------------------------------------------------------------- */

typedef enum {
    NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE  = 0,
    NGX_HTTP_MARKDOWN_PROFILE_BALANCED      = 1,
    NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST = 2
} ngx_http_markdown_profile_e;

/* ----------------------------------------------------------------
 * Minimal conf struct stub for the feature gate tests
 * ---------------------------------------------------------------- */

typedef struct {
    struct {
        ngx_flag_t    zero_copy;
    } stream;
    ngx_flag_t        auto_decompress;
    ngx_http_markdown_profile_e  profile;
} ngx_http_markdown_conf_t;

/* ----------------------------------------------------------------
 * Include real production decision helper
 * ---------------------------------------------------------------- */

#include "../../src/ngx_http_markdown_output_decision_impl.h"

/* ----------------------------------------------------------------
 * Production function: streaming decompression routing (inlined)
 *
 * Streaming decompression is enabled only when:
 *   1. Profile is streaming_first (auto_decompress derived)
 *   2. Streaming engine is selected
 *   3. cache_validation is NOT full
 *   4. Encoding is supported (gzip or deflate in 0.9.1)
 *
 * Profile switch from streaming_first to balanced/strict_cache
 * disables streaming decompression (Req 10.5).
 * ---------------------------------------------------------------- */

/*
 * Determine if streaming decompression is eligible based on profile.
 * streaming_first with auto_decompress ON is the only combination
 * that enables streaming decompression routing.
 */
static ngx_flag_t
streaming_decompress_enabled(const ngx_http_markdown_conf_t *conf)
{
    if (conf->profile != NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST) {
        return 0;
    }
    if (conf->auto_decompress != 1) {
        return 0;
    }
    return 1;
}

static ngx_http_markdown_decomp_route_t
ngx_http_markdown_decomp_routing_decision(
    const ngx_http_markdown_conf_t *conf,
    ngx_flag_t streaming_engine_selected,
    ngx_http_markdown_cache_validation_e cache_validation,
    ngx_http_markdown_compression_type_e encoding)
{
    /* Unknown or no encoding -> bypass */
    if (encoding == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN
        || encoding == NGX_HTTP_MARKDOWN_COMPRESSION_NONE) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS;
    }

    /* Condition 1: streaming decompression must be enabled by profile */
    if (!streaming_decompress_enabled(conf)) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /* Condition 2: streaming engine must be selected */
    if (!streaming_engine_selected) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /* Condition 3: cache_validation must NOT be full */
    if (cache_validation
        == NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /* Condition 4: encoding supported (gzip, deflate, or brotli when compiled) */
    if (encoding != NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE
        && encoding != NGX_HTTP_MARKDOWN_COMPRESSION_GZIP
#ifdef NGX_HTTP_BROTLI
        && encoding != NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
#endif
        ) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /* All conditions met -> streaming decompression */
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
 * Property 13a: HUP reload with zero_copy OFF causes all
 * subsequent chunks to use pool-copy.
 *
 * Model: start with zero_copy ON, simulate HUP by toggling to OFF,
 * then verify ALL subsequent hybrid_decision calls return POOL_COPY
 * regardless of terminal/backpressure state.
 *
 * Validates: Requirements 10.4
 * ---------------------------------------------------------------- */

#define TOGGLE_ITERATIONS    300
#define CHUNKS_PER_SEQUENCE   50

static void
test_property13a_hup_zero_copy_off_forces_pool_copy(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;
    ngx_flag_t terminal, backpressure;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 13a: HUP reload with zero_copy OFF "
        "forces pool-copy for all subsequent chunks");

    for (iter = 0; iter < TOGGLE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 42));

        /*
         * Phase 1: conf has zero_copy ON.
         * Some chunks may get ZERO_COPY (when non-terminal, no bp).
         */
        conf.stream.zero_copy = 1;
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;

        for (j = 0; j < CHUNKS_PER_SEQUENCE / 2; j++) {
            terminal = (ngx_flag_t)(prng_next() % 2);
            backpressure = (ngx_flag_t)(prng_next() % 2);
            result = ngx_http_markdown_hybrid_output_decision(
                &conf, terminal, backpressure);

            /* When ON: ZERO_COPY only if non-terminal + no bp */
            if (!terminal && !backpressure) {
                TEST_ASSERT(
                    result
                        == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
                    "Phase 1: all-clear -> ZERO_COPY");
            } else {
                TEST_ASSERT(
                    result
                        == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
                    "Phase 1: guard active -> POOL_COPY");
            }
        }

        /*
         * Phase 2: Simulate HUP reload - set zero_copy OFF.
         * ALL subsequent chunks MUST use POOL_COPY regardless of
         * terminal or backpressure state.
         */
        conf.stream.zero_copy = 0;

        for (j = 0; j < CHUNKS_PER_SEQUENCE / 2; j++) {
            terminal = (ngx_flag_t)(prng_next() % 2);
            backpressure = (ngx_flag_t)(prng_next() % 2);
            result = ngx_http_markdown_hybrid_output_decision(
                &conf, terminal, backpressure);
            TEST_ASSERT(
                result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
                "Phase 2 (post-HUP OFF): must be POOL_COPY");
        }
    }

    TEST_PASS(
        "Property 13a: zero_copy OFF after HUP -> all "
        "chunks use pool-copy (300 sequences × 25 chunks)");
}

/*
 * Verify that the toggle is immediate: no residual ZERO_COPY
 * decisions after the gate change. Even the very first chunk
 * after toggle must be POOL_COPY.
 */
static void
test_property13a_immediate_toggle_effect(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;
    int iter;

    TEST_SUBSECTION(
        "Property 13a: Toggle effect is immediate "
        "(first chunk after OFF is POOL_COPY)");

    for (iter = 0; iter < TOGGLE_ITERATIONS; iter++) {
        /* Start with ON, send one eligible chunk -> ZERO_COPY */
        conf.stream.zero_copy = 1;
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
        result = ngx_http_markdown_hybrid_output_decision(
            &conf, 0, 0);
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
            "before toggle: eligible -> ZERO_COPY");

        /* Toggle OFF (HUP reload) */
        conf.stream.zero_copy = 0;

        /* Very first chunk after toggle: must be POOL_COPY */
        result = ngx_http_markdown_hybrid_output_decision(
            &conf, 0, 0);
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
            "first chunk after toggle OFF -> POOL_COPY");
    }

    TEST_PASS(
        "Property 13a: immediate toggle effect verified "
        "(300 iterations)");
}

/*
 * Verify re-enable: after toggling OFF and back ON, ZERO_COPY
 * decisions resume for eligible chunks.
 */
static void
test_property13a_reenable_restores_zero_copy(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 13a: Re-enable zero_copy restores "
        "ZERO_COPY for eligible chunks");

    for (iter = 0; iter < TOGGLE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 9999));

        conf.stream.zero_copy = 1;
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;

        /* Phase 1: ON - eligible chunks get ZERO_COPY */
        result = ngx_http_markdown_hybrid_output_decision(
            &conf, 0, 0);
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
            "Phase 1: ON -> ZERO_COPY");

        /* Phase 2: OFF - all POOL_COPY */
        conf.stream.zero_copy = 0;
        for (j = 0; j < 5; j++) {
            result = ngx_http_markdown_hybrid_output_decision(
                &conf,
                (ngx_flag_t)(prng_next() % 2),
                (ngx_flag_t)(prng_next() % 2));
            TEST_ASSERT(
                result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
                "Phase 2: OFF -> POOL_COPY");
        }

        /* Phase 3: ON again - eligible chunks get ZERO_COPY */
        conf.stream.zero_copy = 1;
        result = ngx_http_markdown_hybrid_output_decision(
            &conf, 0, 0);
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
            "Phase 3: re-enabled -> ZERO_COPY");
    }

    TEST_PASS(
        "Property 13a: re-enable restores ZERO_COPY "
        "(300 iterations)");
}

/* ----------------------------------------------------------------
 * Property 13b: Profile switch from streaming_first to
 * balanced/strict_cache disables streaming decompression without
 * restart.
 *
 * Model: start with streaming_first profile (streaming decompress
 * enabled), simulate profile switch to balanced or strict_cache,
 * then verify ALL subsequent routing decisions return FULLBUFFER
 * for compressed responses that would previously use streaming.
 *
 * Validates: Requirements 10.5
 * ---------------------------------------------------------------- */

static void
test_property13b_profile_switch_disables_streaming_decompress(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_decomp_route_t route;
    ngx_http_markdown_compression_type_e encoding;
    ngx_http_markdown_cache_validation_e cache_vals[] = {
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_ETAG
    };
    ngx_http_markdown_profile_e target_profiles[] = {
        NGX_HTTP_MARKDOWN_PROFILE_BALANCED,
        NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE
    };
    int iter;
    size_t j, pi, ci;

    TEST_SUBSECTION(
        "Property 13b: Profile switch from streaming_first "
        "disables streaming decompression");

    for (iter = 0; iter < TOGGLE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 7777));

        /*
         * Phase 1: streaming_first + auto_decompress ON.
         * Raw deflate with streaming engine -> STREAMING route.
         */
        conf.stream.zero_copy = 1;
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;

        route = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
        TEST_ASSERT(
            route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "Phase 1: streaming_first -> STREAMING route");

        /*
         * Phase 2: Switch profile to balanced or strict_cache.
         * ALL subsequent deflate requests must route to FULLBUFFER.
         */
        pi = (size_t)(prng_next() % 2);
        conf.profile = target_profiles[pi];

        for (j = 0; j < CHUNKS_PER_SEQUENCE; j++) {
            ci = (size_t)(prng_next() % 2);
            encoding = NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE;

            route = ngx_http_markdown_decomp_routing_decision(
                &conf, 1, cache_vals[ci], encoding);
            TEST_ASSERT(
                route != NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
                "Phase 2 (post-switch): deflate must NOT "
                "route to STREAMING");
            TEST_ASSERT(
                route
                    == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
                "Phase 2 (post-switch): deflate routes to "
                "FULLBUFFER");
        }
    }

    TEST_PASS(
        "Property 13b: profile switch disables streaming "
        "decompression (300 sequences × 50 chunks)");
}

/*
 * Verify that the profile switch effect is immediate: the very
 * first request after switch uses FULLBUFFER.
 */
static void
test_property13b_immediate_profile_switch_effect(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_decomp_route_t route;
    int iter;
    size_t pi;
    ngx_http_markdown_profile_e target_profiles[] = {
        NGX_HTTP_MARKDOWN_PROFILE_BALANCED,
        NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE
    };

    TEST_SUBSECTION(
        "Property 13b: Profile switch effect is immediate "
        "(first request after switch)");

    for (iter = 0; iter < TOGGLE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 5555));

        /* streaming_first: eligible request -> STREAMING */
        conf.stream.zero_copy = 1;
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
        route = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
        TEST_ASSERT(
            route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "before switch: STREAMING");

        /* Switch to balanced or strict_cache */
        pi = (size_t)(prng_next() % 2);
        conf.profile = target_profiles[pi];

        /* Very first request after switch -> FULLBUFFER */
        route = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
        TEST_ASSERT(
            route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
            "first request after switch -> FULLBUFFER");
    }

    TEST_PASS(
        "Property 13b: immediate profile switch effect "
        "verified (300 iterations)");
}

/*
 * Verify that switching back to streaming_first re-enables
 * streaming decompression.
 */
static void
test_property13b_reswitch_restores_streaming(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_decomp_route_t route;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 13b: Re-switch to streaming_first "
        "restores streaming decompression");

    for (iter = 0; iter < TOGGLE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 1111));

        /* Phase 1: streaming_first -> STREAMING */
        conf.stream.zero_copy = 1;
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
        route = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
        TEST_ASSERT(
            route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "Phase 1: streaming -> STREAMING");

        /* Phase 2: balanced -> FULLBUFFER */
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
        for (j = 0; j < 5; j++) {
            route = ngx_http_markdown_decomp_routing_decision(
                &conf, 1,
                NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
                NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
            TEST_ASSERT(
                route
                    == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
                "Phase 2: balanced -> FULLBUFFER");
        }

        /* Phase 3: back to streaming_first -> STREAMING */
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
        route = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
        TEST_ASSERT(
            route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "Phase 3: back to streaming_first -> STREAMING");
    }

    TEST_PASS(
        "Property 13b: re-switch to streaming_first "
        "restores streaming (300 iterations)");
}

/* ----------------------------------------------------------------
 * Combined property: random toggle sequences.
 *
 * Simulate arbitrary sequences of HUP reloads toggling zero_copy
 * and profile switches, verifying that at every point the decision
 * functions respect the current configuration state.
 * ---------------------------------------------------------------- */

#define COMBINED_ITERATIONS  200
#define COMBINED_OPS          40

typedef enum {
    TOGGLE_OP_ZERO_COPY_ON   = 0,
    TOGGLE_OP_ZERO_COPY_OFF  = 1,
    TOGGLE_OP_PROFILE_STREAMING = 2,
    TOGGLE_OP_PROFILE_BALANCED  = 3,
    TOGGLE_OP_PROFILE_STRICT    = 4,
    TOGGLE_OP_CHECK_HYBRID   = 5,
    TOGGLE_OP_CHECK_DECOMP   = 6,
    TOGGLE_OP_COUNT          = 7
} toggle_op_t;

static void
test_property13_combined_random_toggle_sequences(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t h_result;
    ngx_http_markdown_decomp_route_t d_result;
    ngx_flag_t terminal, backpressure, streaming_sel;
    ngx_http_markdown_cache_validation_e cv;
    toggle_op_t op;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 13: Combined random toggle sequences "
        "(200 seeds × 40 ops)");

    for (iter = 0; iter < COMBINED_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 31337));

        /* Start with defaults */
        conf.stream.zero_copy = 0;
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;

        for (j = 0; j < COMBINED_OPS; j++) {
            op = (toggle_op_t)(prng_next() % TOGGLE_OP_COUNT);

            switch (op) {
            case TOGGLE_OP_ZERO_COPY_ON:
                conf.stream.zero_copy = 1;
                break;

            case TOGGLE_OP_ZERO_COPY_OFF:
                conf.stream.zero_copy = 0;
                break;

            case TOGGLE_OP_PROFILE_STREAMING:
                conf.profile =
                    NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
                break;

            case TOGGLE_OP_PROFILE_BALANCED:
                conf.profile =
                    NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
                break;

            case TOGGLE_OP_PROFILE_STRICT:
                conf.profile =
                    NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE;
                break;

            case TOGGLE_OP_CHECK_HYBRID:
                terminal = (ngx_flag_t)(prng_next() % 2);
                backpressure = (ngx_flag_t)(prng_next() % 2);
                h_result =
                    ngx_http_markdown_hybrid_output_decision(
                        &conf, terminal, backpressure);

                /*
                 * Key property (Req 10.4):
                 * When zero_copy is OFF, result MUST be
                 * POOL_COPY regardless of other flags.
                 */
                if (conf.stream.zero_copy != 1) {
                    TEST_ASSERT(
                        h_result
                            == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
                        "zero_copy OFF -> POOL_COPY");
                }
                break;

            case TOGGLE_OP_CHECK_DECOMP:
                streaming_sel = (ngx_flag_t)(prng_next() % 2);
                cv = (ngx_http_markdown_cache_validation_e)
                    (prng_next() % 3);
                d_result =
                    ngx_http_markdown_decomp_routing_decision(
                        &conf, streaming_sel, cv,
                        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);

                /*
                 * Key property (Req 10.5):
                 * When profile is NOT streaming_first,
                 * result MUST NOT be STREAMING.
                 */
                if (conf.profile
                    != NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST)
                {
                    TEST_ASSERT(
                        d_result
                            != NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
                        "non-streaming_first profile -> "
                        "no STREAMING route");
                }
                break;

            default:
                break;
            }
        }
    }

    TEST_PASS(
        "Property 13: combined random toggles verified "
        "(200 × 40 = 8000 operations)");
}

/* ----------------------------------------------------------------
 * Edge case: verify that auto_decompress OFF also prevents
 * streaming decompression regardless of profile.
 * ---------------------------------------------------------------- */

static void
test_property13b_auto_decompress_off_blocks_streaming(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_decomp_route_t route;
    int iter;

    TEST_SUBSECTION(
        "Property 13b: auto_decompress OFF blocks "
        "streaming decompression even with streaming_first");

    for (iter = 0; iter < TOGGLE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 2222));

        conf.stream.zero_copy = 1;
        conf.auto_decompress = 0;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;

        route = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
        TEST_ASSERT(
            route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
            "auto_decompress OFF -> FULLBUFFER even with "
            "streaming_first");
    }

    TEST_PASS(
        "Property 13b: auto_decompress OFF blocks "
        "streaming (300 iterations)");
}

/* ----------------------------------------------------------------
 * Edge case: verify that gzip is routed to streaming when every
 * profile/gate condition is satisfied.
 * ---------------------------------------------------------------- */

static void
test_property13b_gzip_streams_when_enabled(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_decomp_route_t route;
    int iter;

    TEST_SUBSECTION(
        "Property 13b: gzip uses streaming decompression "
        "when all gates are enabled");

    for (iter = 0; iter < TOGGLE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 8888));

        /* Best-case for streaming: streaming_first + all enabled */
        conf.stream.zero_copy = 1;
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;

        route = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
        TEST_ASSERT(
            route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "gzip -> STREAMING when all gates are enabled");
    }

    TEST_PASS(
        "Property 13b: gzip routes to streaming when enabled "
        "(300 iterations)");
}

/* ----------------------------------------------------------------
 * No-restart property: verify that configuration changes take
 * effect through the same conf struct pointer without requiring
 * reinitialization (models HUP reload behavior where NGINX
 * re-reads conf but reuses the worker process).
 * ---------------------------------------------------------------- */

static void
test_property13_no_restart_required(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t h_result;
    ngx_http_markdown_decomp_route_t d_result;

    TEST_SUBSECTION(
        "Property 13: Config changes effective without "
        "restart (same struct, no reinitialization)");

    /* Initialize once, then mutate repeatedly */
    memset(&conf, 0, sizeof(conf));
    conf.auto_decompress = 1;

    /* zero_copy ON -> eligible -> ZERO_COPY */
    conf.stream.zero_copy = 1;
    conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
    h_result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(
        h_result == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
        "initial ON -> ZERO_COPY");

    /* Toggle zero_copy OFF (HUP) */
    conf.stream.zero_copy = 0;
    h_result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(
        h_result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "after HUP OFF -> POOL_COPY");

    /*
     * Decompression routing is independent of zero_copy gate.
     * streaming_first + auto_decompress ON still routes to
     * STREAMING even when zero_copy is OFF.
     */
    d_result = ngx_http_markdown_decomp_routing_decision(
        &conf, 1,
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        d_result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "streaming_first + zero_copy OFF -> STREAMING "
        "(decomp independent of zero_copy)");

    /* Switch profile to balanced (HUP) */
    conf.profile = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
    d_result = ngx_http_markdown_decomp_routing_decision(
        &conf, 1,
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        d_result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "balanced profile -> FULLBUFFER");

    /* Re-enable everything */
    conf.stream.zero_copy = 1;
    conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
    h_result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(
        h_result == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
        "re-enable -> ZERO_COPY");
    d_result = ngx_http_markdown_decomp_routing_decision(
        &conf, 1,
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        d_result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "re-enable -> STREAMING");

    TEST_PASS(
        "Property 13: no-restart config change verified");
}

/* ----------------------------------------------------------------
 * Property 1 (compile-time subset): Brotli Feature Gate Toggle
 *
 * With NGX_HTTP_BROTLI defined: eligible Brotli routes to STREAMING.
 * With NGX_HTTP_BROTLI NOT defined: Brotli always routes to
 * FULLBUFFER (existing bounded full-buffer path).
 *
 * Brotli build-gate behavior is symmetric when enabled or disabled
 * ---------------------------------------------------------------- */

#define BROTLI_GATE_ITERATIONS  150

/*
 * When NGX_HTTP_BROTLI is defined:
 *   Brotli + all conditions met → STREAMING
 *   Brotli + any condition unmet → FULLBUFFER (or BYPASS)
 *
 * When NGX_HTTP_BROTLI is NOT defined:
 *   Brotli always → FULLBUFFER regardless of other conditions
 */
static void
test_property1_brotli_gate_eligible(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_decomp_route_t route;
    int iter;

    TEST_SUBSECTION(
        "Property 1 (compile-time subset): Brotli with all "
        "conditions met");

    for (iter = 0; iter < BROTLI_GATE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 60001));

        /* All conditions satisfied for streaming eligibility */
        conf.stream.zero_copy = (ngx_flag_t)(prng_next() % 2);
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;

        route = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);

#ifdef NGX_HTTP_BROTLI
        TEST_ASSERT(
            route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "NGX_HTTP_BROTLI defined: eligible Brotli -> "
            "STREAMING");
#else
        TEST_ASSERT(
            route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
            "NGX_HTTP_BROTLI undefined: Brotli always -> "
            "FULLBUFFER");
#endif
    }

#ifdef NGX_HTTP_BROTLI
    TEST_PASS(
        "Property 1: NGX_HTTP_BROTLI defined -> eligible "
        "Brotli reaches STREAMING (150 iterations)");
#else
    TEST_PASS(
        "Property 1: NGX_HTTP_BROTLI undefined -> Brotli "
        "always FULLBUFFER (150 iterations)");
#endif
}

/*
 * When NGX_HTTP_BROTLI is NOT defined, Brotli must always route
 * to FULLBUFFER even if every other condition is satisfied.
 * When NGX_HTTP_BROTLI IS defined, Brotli with any single
 * disqualifying condition routes to FULLBUFFER.
 */
static void
test_property1_brotli_gate_ineligible(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_decomp_route_t route;
    int iter;
    unsigned int scenario;

    TEST_SUBSECTION(
        "Property 1 (compile-time subset): Brotli with "
        "one condition unmet");

    for (iter = 0; iter < BROTLI_GATE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 70001));

        /*
         * Randomly select which single condition to violate:
         * 0 = auto_decompress OFF
         * 1 = streaming engine not selected
         * 2 = cache_validation FULL
         * 3 = profile not streaming_first
         */
        scenario = prng_next() % 4;

        conf.stream.zero_copy = 1;
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;

        switch (scenario) {
        case 0:
            conf.auto_decompress = 0;
            route = ngx_http_markdown_decomp_routing_decision(
                &conf, 1,
                NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
                NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);
            /* auto_decompress OFF -> FULLBUFFER for all codecs */
            TEST_ASSERT(
                route
                    == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
                "Brotli + auto_decompress OFF -> FULLBUFFER");
            break;

        case 1:
            route = ngx_http_markdown_decomp_routing_decision(
                &conf, 0,
                NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
                NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);
            TEST_ASSERT(
                route
                    == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
                "Brotli + streaming not selected -> "
                "FULLBUFFER");
            break;

        case 2:
            route = ngx_http_markdown_decomp_routing_decision(
                &conf, 1,
                NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
                NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);
            TEST_ASSERT(
                route
                    == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
                "Brotli + cache_validation FULL -> "
                "FULLBUFFER");
            break;

        case 3:
            conf.profile = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
            route = ngx_http_markdown_decomp_routing_decision(
                &conf, 1,
                NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
                NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);
            TEST_ASSERT(
                route
                    == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
                "Brotli + non-streaming_first profile -> "
                "FULLBUFFER");
            break;

        default:
            break;
        }
    }

    TEST_PASS(
        "Property 1: Brotli with one condition unmet -> "
        "FULLBUFFER (150 iterations)");
}

/*
 * Verify that without NGX_HTTP_BROTLI, NO combination of
 * runtime conditions can produce STREAMING for Brotli.
 * With NGX_HTTP_BROTLI, gzip/deflate remain unaffected by
 * the Brotli gate.
 */
static void
test_property1_brotli_gate_exhaustive_random(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_decomp_route_t route;
    ngx_flag_t auto_dec, streaming_sel;
    ngx_http_markdown_cache_validation_e cv;
    ngx_http_markdown_profile_e prof;
    int iter;

    TEST_SUBSECTION(
        "Property 1 (compile-time subset): Exhaustive "
        "random Brotli gate verification");

    for (iter = 0; iter < BROTLI_GATE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 80001));

        /* Random configuration */
        auto_dec = (ngx_flag_t)(prng_next() % 2);
        streaming_sel = (ngx_flag_t)(prng_next() % 2);
        cv = (ngx_http_markdown_cache_validation_e)
            (prng_next() % 3);
        prof = (ngx_http_markdown_profile_e)
            (prng_next() % 3);

        conf.stream.zero_copy = (ngx_flag_t)(prng_next() % 2);
        conf.auto_decompress = auto_dec;
        conf.profile = prof;

        route = ngx_http_markdown_decomp_routing_decision(
            &conf, streaming_sel, cv,
            NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);

#ifdef NGX_HTTP_BROTLI
        /*
         * With NGX_HTTP_BROTLI: STREAMING iff all four
         * conditions hold.
         */
        if (auto_dec == 1
            && prof == NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST
            && streaming_sel == 1
            && cv != NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL)
        {
            TEST_ASSERT(
                route
                    == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
                "NGX_HTTP_BROTLI + all conditions -> "
                "STREAMING");
        } else if (auto_dec == 0
            || prof
               != NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST)
        {
            TEST_ASSERT(
                route
                    == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
                "NGX_HTTP_BROTLI + condition unmet -> "
                "FULLBUFFER");
        } else {
            TEST_ASSERT(
                route
                    != NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
                "NGX_HTTP_BROTLI + partial conditions -> "
                "not STREAMING");
        }
#else
        /*
         * Without NGX_HTTP_BROTLI: Brotli NEVER reaches
         * STREAMING regardless of runtime conditions.
         */
        TEST_ASSERT(
            route != NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "no NGX_HTTP_BROTLI: Brotli must never reach "
            "STREAMING");
        if (auto_dec == 0
            || prof
               != NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST)
        {
            TEST_ASSERT(
                route
                    == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
                "no NGX_HTTP_BROTLI: Brotli -> FULLBUFFER");
        }
#endif
    }

#ifdef NGX_HTTP_BROTLI
    TEST_PASS(
        "Property 1: NGX_HTTP_BROTLI defined -> Brotli "
        "routing respects all four conditions "
        "(150 iterations)");
#else
    TEST_PASS(
        "Property 1: NGX_HTTP_BROTLI undefined -> Brotli "
        "never reaches STREAMING (150 iterations)");
#endif
}

/*
 * Verify that the feature gate does NOT affect gzip/deflate
 * routing — those codecs reach STREAMING regardless of the
 * NGX_HTTP_BROTLI compile-time state.
 */
static void
test_property1_brotli_gate_does_not_affect_other_codecs(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_decomp_route_t route_gzip;
    ngx_http_markdown_decomp_route_t route_deflate;
    int iter;

    TEST_SUBSECTION(
        "Property 1 (compile-time subset): Brotli gate "
        "does not affect gzip/deflate routing");

    for (iter = 0; iter < BROTLI_GATE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 90001));

        /* All conditions met for streaming */
        conf.stream.zero_copy = (ngx_flag_t)(prng_next() % 2);
        conf.auto_decompress = 1;
        conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;

        route_gzip = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
        TEST_ASSERT(
            route_gzip
                == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "gzip -> STREAMING regardless of Brotli gate");

        route_deflate = ngx_http_markdown_decomp_routing_decision(
            &conf, 1,
            NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
        TEST_ASSERT(
            route_deflate
                == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "deflate -> STREAMING regardless of Brotli gate");
    }

    TEST_PASS(
        "Property 1: Brotli gate does not affect "
        "gzip/deflate routing (150 iterations)");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Property 13: Dynamic Feature Gate Toggle\n"
        "Validates: Requirements 10.4, 10.5");

    /* Property 13a: HUP reload zero_copy OFF -> pool-copy */
    test_property13a_hup_zero_copy_off_forces_pool_copy();
    test_property13a_immediate_toggle_effect();
    test_property13a_reenable_restores_zero_copy();

    /* Property 13b: Profile switch disables streaming decompress */
    test_property13b_profile_switch_disables_streaming_decompress();
    test_property13b_immediate_profile_switch_effect();
    test_property13b_reswitch_restores_streaming();

    /* Combined random toggle sequences */
    test_property13_combined_random_toggle_sequences();

    /* Edge cases */
    test_property13b_auto_decompress_off_blocks_streaming();
    test_property13b_gzip_streams_when_enabled();

    /* No-restart property */
    test_property13_no_restart_required();

    /*
     * Property 1 (compile-time subset): Brotli feature gate toggle
     * Brotli build-gate behavior is symmetric when enabled or disabled
     */
    TEST_SECTION(
        "Feature: Brotli streaming decompression\n"
        "Property 1 (compile-time subset): "
        "Feature Gate Toggle\n"
        "Brotli build-gate behavior is symmetric when enabled or disabled");

    test_property1_brotli_gate_eligible();
    test_property1_brotli_gate_ineligible();
    test_property1_brotli_gate_exhaustive_random();
    test_property1_brotli_gate_does_not_affect_other_codecs();

    printf("\n");
    TEST_PASS(
        "feature_gate_toggle_property: all property tests "
        "passed");
    return 0;
}
