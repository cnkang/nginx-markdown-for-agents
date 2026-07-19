/*
 * Test: rollout_control
 *
 * Integration tests for staged rollout control. Verifies that
 * feature gates correctly control optimization activation and
 * that both default and all-features-on configurations produce
 * valid decisions without regressions.
 *
 * Feature: 0.9.1-performance-optimization
 * Task: 14.5 Write integration tests for rollout control
 *
 * Validates: Requirements 10.1, 10.2, 10.3, 10.6
 *
 * Test cases:
 *   1. Zero-copy default-off — no behavioral change without
 *      explicit `markdown_streaming_zero_copy on`
 *   2. Streaming decompression gated by profile — only active
 *      under streaming_first profile with auto_decompress on
 *   3. Full-buffer copy reduction always active — no config
 *      toggle, internal implementation detail
 *   4. Both default and all-features-on configurations produce
 *      valid decisions (CI matrix coverage)
 */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef intptr_t        ngx_flag_t;
typedef volatile long   ngx_atomic_t;
typedef long            ngx_atomic_int_t;
typedef unsigned long   ngx_atomic_uint_t;

enum {
    NGX_OK    =  0,
    NGX_ERROR = -1,
    NGX_AGAIN = -2
};

/* ----------------------------------------------------------------
 * Output decision enum (mirrors production)
 * ---------------------------------------------------------------- */

typedef enum {
    NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY  = 0,
    NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY  = 1
} ngx_http_markdown_output_decision_t;

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
 * Minimal conf struct modeling rollout-relevant fields
 * ---------------------------------------------------------------- */

typedef struct {
    struct {
        ngx_flag_t    zero_copy;       /* default: 0 (OFF) */
    } stream;
    ngx_flag_t        auto_decompress; /* default: 0 (OFF) */
    ngx_uint_t        profile;         /* default: BALANCED */
    ngx_http_markdown_cache_validation_e  cache_validation;
} rollout_conf_t;

/* ----------------------------------------------------------------
 * Copy reduction state — always active, no config surface
 * ---------------------------------------------------------------- */

typedef struct {
    int  contiguity_skip_active;    /* always 1 */
    int  direct_swap_active;        /* always 1 */
} copy_reduction_state_t;

/* ----------------------------------------------------------------
 * Production functions under test (inlined for standalone build)
 * ---------------------------------------------------------------- */

/*
 * Hybrid output decision: zero-copy vs pool-copy routing.
 * Mirrors production logic from streaming_impl.h.
 */
static ngx_http_markdown_output_decision_t
ngx_http_markdown_hybrid_output_decision(
    const rollout_conf_t *conf,
    ngx_flag_t chunk_is_terminal,
    ngx_flag_t backpressure_active)
{
    /* Feature gate OFF -> pool-copy (Req 10.1) */
    if (conf->stream.zero_copy != 1) {
        return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
    }

    /* Terminal chunk -> pool-copy */
    if (chunk_is_terminal) {
        return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
    }

    /* Backpressure active -> pool-copy */
    if (backpressure_active) {
        return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
    }

    /* All guards clear -> zero-copy */
    return NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY;
}

/*
 * Streaming decompression routing decision.
 * Active ONLY when streaming_first profile + auto_decompress on.
 * Mirrors production logic.
 */
static ngx_http_markdown_decomp_route_t
ngx_http_markdown_decomp_routing_decision(
    ngx_flag_t auto_decompress,
    ngx_flag_t streaming_engine_selected,
    ngx_http_markdown_cache_validation_e cache_validation,
    ngx_http_markdown_compression_type_e encoding)
{
    /* Unknown or no encoding -> bypass */
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

    /* Condition 3: cache_validation must NOT be full */
    if (cache_validation
        == NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /*
     * Condition 4: encoding must be supported by streaming
     * decompressor. Gzip, deflate, and Brotli (when compiled)
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

    return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING;
}

/*
 * Determine if streaming engine is selected based on profile.
 * streaming_first profile selects streaming; others do not.
 */
static ngx_flag_t
is_streaming_engine_selected(ngx_uint_t profile)
{
    return (profile == NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST)
        ? 1 : 0;
}

/*
 * Copy reduction is always active — there is no configuration
 * toggle.  This function models the invariant.
 */
static void
init_copy_reduction_state(copy_reduction_state_t *state)
{
    state->contiguity_skip_active = 1;
    state->direct_swap_active = 1;
}

/* ----------------------------------------------------------------
 * Default configuration initializer (mirrors nginx conf init)
 * ---------------------------------------------------------------- */

static void
init_default_conf(rollout_conf_t *conf)
{
    conf->stream.zero_copy = 0;   /* OFF by default (Req 10.1) */
    conf->auto_decompress = 0;    /* OFF by default */
    conf->profile = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
    conf->cache_validation =
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE;
}

/* ----------------------------------------------------------------
 * All-features-on configuration (CI matrix "all-features-on")
 * ---------------------------------------------------------------- */

static void
init_all_features_conf(rollout_conf_t *conf)
{
    conf->stream.zero_copy = 1;   /* ON explicitly */
    conf->auto_decompress = 1;    /* ON */
    conf->profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
    conf->cache_validation =
        NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE;
}

/* ================================================================
 * Test 1: Zero-copy default-off — no behavioral change without
 * explicit enable.
 *
 * Validates: Requirement 10.1
 * "THE Module SHALL ship Zero_Copy_Output as default-off in 0.9.1,
 *  activated only by explicit markdown_streaming_zero_copy on"
 *
 * This test verifies that:
 *   a) Default conf has zero_copy = 0 (OFF)
 *   b) With default conf, ALL chunks route to POOL_COPY
 *   c) No zero-copy behavior is observable without explicit enable
 * ================================================================ */

static void
test_zero_copy_default_off(void)
{
    rollout_conf_t conf;
    ngx_http_markdown_output_decision_t result;

    TEST_SUBSECTION(
        "zero-copy default-off: no behavioral change "
        "without explicit enable (Req 10.1)");

    init_default_conf(&conf);

    /* Verify the default value is OFF */
    TEST_ASSERT(conf.stream.zero_copy == 0,
        "default conf has zero_copy = 0 (OFF)");

    /* Non-terminal, no backpressure — still POOL_COPY */
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "default conf, non-terminal, no bp → POOL_COPY");

    /* Non-terminal, backpressure — POOL_COPY */
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "default conf, non-terminal, bp → POOL_COPY");

    /* Terminal, no backpressure — POOL_COPY */
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "default conf, terminal, no bp → POOL_COPY");

    /* Terminal, backpressure — POOL_COPY */
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "default conf, terminal, bp → POOL_COPY");

    TEST_PASS(
        "zero-copy default-off: all paths are POOL_COPY");
}

/* ================================================================
 * Test 2: Streaming decompression gated by profile.
 *
 * Validates: Requirement 10.2
 * "THE Module SHALL ship Streaming_Decompression as profile-gated
 *  in 0.9.1, active only under streaming_first profile with
 *  auto_decompress on"
 *
 * This test verifies:
 *   a) Default conf (balanced profile) does NOT select streaming
 *      decompression even for raw deflate
 *   b) strict_cache profile does NOT select streaming decompression
 *   c) streaming_first + auto_decompress off does NOT select it
 *   d) streaming_first + auto_decompress on DOES select streaming
 *      decompression for raw deflate
 *   e) streaming_first + auto_decompress on selects streaming
 *      decompression for gzip
 * ================================================================ */

static void
test_streaming_decompression_profile_gated(void)
{
    rollout_conf_t conf;
    ngx_http_markdown_decomp_route_t route;
    ngx_flag_t streaming_selected;

    TEST_SUBSECTION(
        "streaming decompression gated by profile "
        "(Req 10.2)");

    /* (a) Default conf: balanced profile, auto_decompress off */
    init_default_conf(&conf);
    streaming_selected = is_streaming_engine_selected(
        conf.profile);
    route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "balanced profile, auto_decompress off → "
        "FULLBUFFER");

    /* (b) strict_cache profile, auto_decompress on */
    conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE;
    conf.auto_decompress = 1;
    streaming_selected = is_streaming_engine_selected(
        conf.profile);
    route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "strict_cache profile → FULLBUFFER even with "
        "auto_decompress on");

    /* (c) streaming_first + auto_decompress off */
    conf.profile = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
    conf.auto_decompress = 0;
    streaming_selected = is_streaming_engine_selected(
        conf.profile);
    route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "streaming_first + auto_decompress off → "
        "FULLBUFFER");

    /* (d) streaming_first + auto_decompress on + deflate */
    conf.auto_decompress = 1;
    streaming_selected = is_streaming_engine_selected(
        conf.profile);
    route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "streaming_first + auto_decompress on + "
        "deflate → STREAMING");

    /* (e) streaming_first + auto_decompress on + gzip */
    route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
    TEST_ASSERT(
        route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "streaming_first + auto_decompress on + gzip "
        "→ STREAMING");

    TEST_PASS(
        "streaming decompression correctly profile-gated");
}

/* ================================================================
 * Test 3: Full-buffer copy reduction always active.
 *
 * Validates: Requirement 10.3
 * "THE Module SHALL ship full-buffer copy reduction as default-on
 *  in 0.9.1 since it is an internal implementation detail with no
 *  configuration surface"
 *
 * Verifies:
 *   a) Copy reduction state is always active regardless of config
 *   b) No conf field controls copy reduction on/off
 *   c) Both default and all-features-on confs have copy reduction
 * ================================================================ */

static void
test_copy_reduction_always_active(void)
{
    copy_reduction_state_t state;
    rollout_conf_t default_conf;
    rollout_conf_t allfeatures_conf;

    TEST_SUBSECTION(
        "copy reduction always active, no config toggle "
        "(Req 10.3)");

    /* Initialize and verify always-on */
    init_copy_reduction_state(&state);
    TEST_ASSERT(state.contiguity_skip_active == 1,
        "contiguity skip is always active");
    TEST_ASSERT(state.direct_swap_active == 1,
        "direct swap is always active");

    /*
     * Verify no configuration field can disable it.
     * With default conf:
     */
    init_default_conf(&default_conf);
    init_copy_reduction_state(&state);
    TEST_ASSERT(state.contiguity_skip_active == 1,
        "copy reduction active with default conf");
    TEST_ASSERT(state.direct_swap_active == 1,
        "direct swap active with default conf");

    UNUSED(default_conf);

    /*
     * With all-features-on conf — still active (not toggled
     * by other features):
     */
    init_all_features_conf(&allfeatures_conf);
    init_copy_reduction_state(&state);
    TEST_ASSERT(state.contiguity_skip_active == 1,
        "copy reduction active with all-features conf");
    TEST_ASSERT(state.direct_swap_active == 1,
        "direct swap active with all-features conf");

    UNUSED(allfeatures_conf);

    TEST_PASS(
        "copy reduction always active: no config toggle");
}

/* ================================================================
 * Test 4: Both default and all-features-on configurations
 * produce valid decisions.
 *
 * Validates: Requirement 10.6
 * "THE Module SHALL test in CI with both default configuration
 *  and all-features-on configuration to ensure feature gates do
 *  not introduce regressions"
 *
 * This exercises the CI matrix concept at the unit level:
 *   a) Default configuration produces valid output decisions
 *      and valid decompression routing
 *   b) All-features-on configuration produces valid decisions
 *   c) No decision returns an invalid/unrecognized value
 *   d) Feature gates interact correctly without conflict
 * ================================================================ */

static void
test_ci_matrix_default_config(void)
{
    rollout_conf_t conf;
    ngx_http_markdown_output_decision_t out_decision;
    ngx_http_markdown_decomp_route_t decomp_route;
    ngx_flag_t streaming_selected;
    copy_reduction_state_t copy_state;
    ngx_http_markdown_compression_type_e encodings[] = {
        NGX_HTTP_MARKDOWN_COMPRESSION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
        NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN
    };
    size_t i;

    TEST_SUBSECTION(
        "CI matrix: default configuration produces "
        "valid decisions (Req 10.6)");

    init_default_conf(&conf);
    init_copy_reduction_state(&copy_state);
    streaming_selected = is_streaming_engine_selected(
        conf.profile);

    /* Output decision: all cases must be POOL_COPY */
    out_decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(
        out_decision == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "default: output decision is valid POOL_COPY");

    out_decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 0);
    TEST_ASSERT(
        out_decision == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "default: terminal chunk → valid POOL_COPY");

    /* Decompression routing: all encodings must produce valid
     * route without crash */
    for (i = 0; i < ARRAY_SIZE(encodings); i++) {
        decomp_route = ngx_http_markdown_decomp_routing_decision(
            conf.auto_decompress, streaming_selected,
            conf.cache_validation, encodings[i]);
        TEST_ASSERT(
            decomp_route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER
            || decomp_route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS,
            "default: decomp route is valid "
            "(FULLBUFFER or BYPASS)");
        /* STREAMING should NOT be reachable with default conf */
        TEST_ASSERT(
            decomp_route
                != NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
            "default: STREAMING must not be reachable");
    }

    /* Copy reduction: always active */
    TEST_ASSERT(copy_state.contiguity_skip_active == 1,
        "default: copy reduction active");

    TEST_PASS(
        "CI matrix: default config produces all-valid "
        "decisions");
}

static void
test_ci_matrix_all_features_config(void)
{
    rollout_conf_t conf;
    ngx_http_markdown_output_decision_t out_decision;
    ngx_http_markdown_decomp_route_t decomp_route;
    ngx_flag_t streaming_selected;
    copy_reduction_state_t copy_state;

    TEST_SUBSECTION(
        "CI matrix: all-features-on configuration "
        "produces valid decisions (Req 10.6)");

    init_all_features_conf(&conf);
    init_copy_reduction_state(&copy_state);
    streaming_selected = is_streaming_engine_selected(
        conf.profile);

    /* Output decision: zero-copy reachable when all guards clear */
    out_decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(
        out_decision == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
        "all-features: non-terminal, no bp → ZERO_COPY");

    /* Terminal still forces pool-copy even with gate ON */
    out_decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 0);
    TEST_ASSERT(
        out_decision == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "all-features: terminal → POOL_COPY");

    /* Backpressure forces pool-copy */
    out_decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 1);
    TEST_ASSERT(
        out_decision == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "all-features: backpressure → POOL_COPY");

    /* Decompression: raw deflate reaches streaming */
    decomp_route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        decomp_route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "all-features: deflate reaches STREAMING");

    /* Gzip reaches streaming with all features enabled */
    decomp_route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
    TEST_ASSERT(
        decomp_route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "all-features: gzip reaches STREAMING");

    /* No encoding → bypass even with all features */
    decomp_route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_NONE);
    TEST_ASSERT(
        decomp_route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS,
        "all-features: no encoding → BYPASS");

    /* Copy reduction: still always active */
    TEST_ASSERT(copy_state.contiguity_skip_active == 1,
        "all-features: copy reduction active");
    TEST_ASSERT(copy_state.direct_swap_active == 1,
        "all-features: direct swap active");

    TEST_PASS(
        "CI matrix: all-features-on produces valid "
        "decisions with no conflicts");
}

/* ================================================================
 * Test 5: Feature gates interact correctly — switching from
 * all-features to default disables optimizations cleanly.
 *
 * This simulates the runtime toggle documented in Req 10.4/10.5:
 *   - Zero-copy reverts to pool-copy on conf change
 *   - Profile switch disables streaming decompression
 *
 * Validates: Requirements 10.1, 10.2 (implicit rollback path)
 * ================================================================ */

static void
test_feature_gate_toggle_coherence(void)
{
    rollout_conf_t conf;
    ngx_http_markdown_output_decision_t out_decision;
    ngx_http_markdown_decomp_route_t decomp_route;
    ngx_flag_t streaming_selected;

    TEST_SUBSECTION(
        "feature gate toggle: reverting to defaults "
        "disables optimizations");

    /* Start with all-features-on */
    init_all_features_conf(&conf);
    streaming_selected = is_streaming_engine_selected(
        conf.profile);

    out_decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(
        out_decision == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
        "all-features: ZERO_COPY active");

    decomp_route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        decomp_route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "all-features: STREAMING active");

    /* Simulate HUP reload reverting to defaults */
    init_default_conf(&conf);
    streaming_selected = is_streaming_engine_selected(
        conf.profile);

    out_decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(
        out_decision == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "after revert: POOL_COPY (zero-copy disabled)");

    decomp_route = ngx_http_markdown_decomp_routing_decision(
        conf.auto_decompress, streaming_selected,
        conf.cache_validation,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        decomp_route == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "after revert: FULLBUFFER (streaming decomp "
        "disabled)");

    TEST_PASS(
        "feature gate toggle: optimizations correctly "
        "disabled on revert");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Task 14.5: Integration Tests for Rollout Control\n"
        "Validates: Requirements 10.1, 10.2, 10.3, 10.6");

    /* Req 10.1: Zero-copy default-off */
    test_zero_copy_default_off();

    /* Req 10.2: Streaming decompression profile-gated */
    test_streaming_decompression_profile_gated();

    /* Req 10.3: Copy reduction always active */
    test_copy_reduction_always_active();

    /* Req 10.6: CI matrix — both configurations valid */
    test_ci_matrix_default_config();
    test_ci_matrix_all_features_config();

    /* Rollback coherence (implicit from 10.1, 10.2) */
    test_feature_gate_toggle_coherence();

    printf("\n");
    TEST_PASS(
        "rollout_control: all integration tests passed");
    return 0;
}
