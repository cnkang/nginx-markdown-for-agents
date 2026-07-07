/*
 * Test: hybrid_decision_property
 *
 * Property-based tests for the hybrid output decision matrix
 * (Property 2).
 *
 * Feature: 0.9.1-performance-optimization
 * Property 2: Hybrid Decision Matrix Correctness
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4
 *
 * The decision function ngx_http_markdown_hybrid_output_decision
 * returns ZERO_COPY only when ALL three guards are clear:
 *   - Feature gate ON  (conf->stream.zero_copy == 1)
 *   - Non-terminal chunk (chunk_is_terminal == 0)
 *   - No backpressure   (backpressure_active == 0)
 *
 * Any single guard active forces POOL_COPY.
 *
 * This test exhaustively verifies all 8 combinations and also
 * uses pseudo-random sequences to confirm the property holds
 * across many input combinations.
 */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef intptr_t        ngx_flag_t;

/* ----------------------------------------------------------------
 * Output decision enum (mirrors production)
 * ---------------------------------------------------------------- */

typedef enum {
    NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY  = 0,
    NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY  = 1
} ngx_http_markdown_output_decision_t;

/* ----------------------------------------------------------------
 * Minimal conf struct stub (only stream.zero_copy needed)
 * ---------------------------------------------------------------- */

typedef struct {
    struct {
        ngx_flag_t    zero_copy;
    } stream;
} ngx_http_markdown_conf_t;

/* ----------------------------------------------------------------
 * Production function under test (inlined from streaming_impl.h)
 *
 * This replicates the exact production logic:
 *   Feature OFF    -> POOL_COPY (Req 3.1)
 *   Terminal chunk -> POOL_COPY (Req 3.3)
 *   Backpressure  -> POOL_COPY (Req 3.4)
 *   All clear     -> ZERO_COPY (Req 3.2)
 * ---------------------------------------------------------------- */

static ngx_http_markdown_output_decision_t
ngx_http_markdown_hybrid_output_decision(
    const ngx_http_markdown_conf_t *conf,
    ngx_flag_t chunk_is_terminal,
    ngx_flag_t backpressure_active)
{
    /* Feature gate OFF -> pool-copy (Req 3.1) */
    if (conf->stream.zero_copy != 1) {
        return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
    }

    /* Terminal chunk -> pool-copy (Req 3.3) */
    if (chunk_is_terminal) {
        return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
    }

    /* Backpressure active -> pool-copy (Req 3.4) */
    if (backpressure_active) {
        return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
    }

    /* All guards clear -> zero-copy (Req 3.2) */
    return NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY;
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
 * Property 2: Hybrid Decision Matrix Correctness
 *
 * The core property:
 *   ZERO_COPY iff (gate==ON AND terminal==0 AND backpressure==0)
 *   POOL_COPY otherwise
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4
 * ---------------------------------------------------------------- */

/*
 * Reference oracle: computes the expected decision from inputs.
 * This is the specification encoded as code.
 */
static ngx_http_markdown_output_decision_t
expected_decision(ngx_flag_t gate_on, ngx_flag_t terminal,
    ngx_flag_t backpressure)
{
    if (gate_on && !terminal && !backpressure) {
        return NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY;
    }
    return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
}

/*
 * Exhaustive test of all 8 combinations in the decision matrix.
 */
static void
test_property2_exhaustive_decision_matrix(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;
    ngx_http_markdown_output_decision_t expect;

    ngx_flag_t gate_values[2]  = { 0, 1 };
    ngx_flag_t term_values[2]  = { 0, 1 };
    ngx_flag_t bp_values[2]    = { 0, 1 };

    int gi, ti, bi;

    TEST_SUBSECTION(
        "Property 2: Exhaustive 8-combination decision matrix");

    for (gi = 0; gi < 2; gi++) {
        for (ti = 0; ti < 2; ti++) {
            for (bi = 0; bi < 2; bi++) {
                conf.stream.zero_copy = gate_values[gi];
                result = ngx_http_markdown_hybrid_output_decision(
                    &conf, term_values[ti], bp_values[bi]);
                expect = expected_decision(
                    gate_values[gi], term_values[ti],
                    bp_values[bi]);
                TEST_ASSERT(result == expect,
                    "decision must match oracle for "
                    "combination");
            }
        }
    }

    TEST_PASS(
        "Property 2: all 8 combinations match oracle");
}

/*
 * Explicit named cases for clarity and traceability.
 */
static void
test_property2_named_cases(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;

    TEST_SUBSECTION(
        "Property 2: Named cases from decision table");

    /* Case 1: gate=OFF, terminal=NO, backpressure=NO -> POOL_COPY */
    conf.stream.zero_copy = 0;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, no terminal, no bp -> POOL_COPY (Req 3.1)");

    /* Case 2: gate=OFF, terminal=NO, backpressure=YES -> POOL_COPY */
    conf.stream.zero_copy = 0;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, no terminal, bp active -> POOL_COPY");

    /* Case 3: gate=OFF, terminal=YES, backpressure=NO -> POOL_COPY */
    conf.stream.zero_copy = 0;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, terminal, no bp -> POOL_COPY");

    /* Case 4: gate=OFF, terminal=YES, backpressure=YES -> POOL_COPY */
    conf.stream.zero_copy = 0;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, terminal, bp active -> POOL_COPY");

    /* Case 5: gate=ON, terminal=NO, backpressure=NO -> ZERO_COPY */
    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
        "gate ON, no terminal, no bp -> ZERO_COPY (Req 3.2)");

    /* Case 6: gate=ON, terminal=NO, backpressure=YES -> POOL_COPY */
    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate ON, no terminal, bp active -> POOL_COPY "
        "(Req 3.4)");

    /* Case 7: gate=ON, terminal=YES, backpressure=NO -> POOL_COPY */
    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate ON, terminal, no bp -> POOL_COPY (Req 3.3)");

    /* Case 8: gate=ON, terminal=YES, backpressure=YES -> POOL_COPY */
    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate ON, terminal, bp active -> POOL_COPY");

    TEST_PASS(
        "Property 2: all 8 named cases pass");
}

/* ----------------------------------------------------------------
 * Random sequence property test
 *
 * Generate many random (gate, terminal, backpressure) tuples and
 * verify the decision function matches the oracle for each.
 * ---------------------------------------------------------------- */

#define RANDOM_ITERATIONS  500
#define RANDOM_SEQ_LEN      50

static void
test_property2_random_sequences(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;
    ngx_http_markdown_output_decision_t expect;
    ngx_flag_t gate, terminal, backpressure;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 2: Random input sequences "
        "(500 seeds × 50 inputs)");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 42));

        for (j = 0; j < RANDOM_SEQ_LEN; j++) {
            gate = (ngx_flag_t)(prng_next() % 2);
            terminal = (ngx_flag_t)(prng_next() % 2);
            backpressure = (ngx_flag_t)(prng_next() % 2);

            conf.stream.zero_copy = gate;
            result = ngx_http_markdown_hybrid_output_decision(
                &conf, terminal, backpressure);
            expect = expected_decision(
                gate, terminal, backpressure);

            TEST_ASSERT(result == expect,
                "random input must match oracle");
        }
    }

    TEST_PASS(
        "Property 2: oracle match verified for 25000 "
        "random inputs");
}

/* ----------------------------------------------------------------
 * Boundary / edge-case property tests
 * ---------------------------------------------------------------- */

/*
 * Verify that non-boolean truthy values for terminal and
 * backpressure are still treated as active guards.
 * The production code uses `if (chunk_is_terminal)` which
 * treats any non-zero value as true.
 */
static void
test_property2_nonboolean_truthy_values(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;
    ngx_flag_t truthy_values[] = { 1, 2, 42, -1, 100 };
    size_t i;

    TEST_SUBSECTION(
        "Property 2: Non-boolean truthy values treated "
        "as guard-active");

    conf.stream.zero_copy = 1;

    /* Non-zero terminal values -> POOL_COPY */
    for (i = 0; i < ARRAY_SIZE(truthy_values); i++) {
        result = ngx_http_markdown_hybrid_output_decision(
            &conf, truthy_values[i], 0);
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
            "truthy terminal value -> POOL_COPY");
    }

    /* Non-zero backpressure values -> POOL_COPY */
    for (i = 0; i < ARRAY_SIZE(truthy_values); i++) {
        result = ngx_http_markdown_hybrid_output_decision(
            &conf, 0, truthy_values[i]);
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
            "truthy backpressure value -> POOL_COPY");
    }

    TEST_PASS(
        "Property 2: non-boolean truthy values correctly "
        "force POOL_COPY");
}

/*
 * Verify that the feature gate checks for equality to 1,
 * not just non-zero. Values like 2, -1, 42 should all
 * result in POOL_COPY (gate not enabled).
 */
static void
test_property2_gate_value_strict_equality(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;
    ngx_flag_t non_one_values[] = { 0, 2, -1, 42, 100 };
    size_t i;

    TEST_SUBSECTION(
        "Property 2: Gate requires exact value 1 for "
        "ZERO_COPY eligibility");

    for (i = 0; i < ARRAY_SIZE(non_one_values); i++) {
        conf.stream.zero_copy = non_one_values[i];
        result = ngx_http_markdown_hybrid_output_decision(
            &conf, 0, 0);
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
            "gate != 1 -> POOL_COPY even with all else "
            "clear");
    }

    /* Only exactly 1 enables zero-copy */
    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
        "gate == 1 with all clear -> ZERO_COPY");

    TEST_PASS(
        "Property 2: strict gate==1 check verified");
}

/*
 * Property: ZERO_COPY implies all three conditions are met.
 * Contrapositive: if any guard is active, result is POOL_COPY.
 *
 * Verify with random inputs that whenever the result is
 * ZERO_COPY, all three guards are indeed clear.
 */
static void
test_property2_zero_copy_implies_all_clear(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;
    ngx_flag_t gate, terminal, backpressure;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 2: ZERO_COPY implies all guards clear "
        "(contrapositive check)");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 7777));

        for (j = 0; j < RANDOM_SEQ_LEN; j++) {
            gate = (ngx_flag_t)(prng_next() % 2);
            terminal = (ngx_flag_t)(prng_next() % 2);
            backpressure = (ngx_flag_t)(prng_next() % 2);

            conf.stream.zero_copy = gate;
            result = ngx_http_markdown_hybrid_output_decision(
                &conf, terminal, backpressure);

            if (result == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY) {
                TEST_ASSERT(gate == 1,
                    "ZERO_COPY implies gate ON");
                TEST_ASSERT(terminal == 0,
                    "ZERO_COPY implies non-terminal");
                TEST_ASSERT(backpressure == 0,
                    "ZERO_COPY implies no backpressure");
            }
        }
    }

    TEST_PASS(
        "Property 2: ZERO_COPY -> all guards clear "
        "(25000 inputs)");
}

/*
 * Property: POOL_COPY implies at least one guard is active.
 * Verify with random inputs.
 */
static void
test_property2_pool_copy_implies_guard_active(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;
    ngx_flag_t gate, terminal, backpressure;
    int any_guard_active;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 2: POOL_COPY implies at least one "
        "guard active");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 3333));

        for (j = 0; j < RANDOM_SEQ_LEN; j++) {
            gate = (ngx_flag_t)(prng_next() % 2);
            terminal = (ngx_flag_t)(prng_next() % 2);
            backpressure = (ngx_flag_t)(prng_next() % 2);

            conf.stream.zero_copy = gate;
            result = ngx_http_markdown_hybrid_output_decision(
                &conf, terminal, backpressure);

            if (result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY) {
                any_guard_active =
                    (gate != 1) || terminal || backpressure;
                TEST_ASSERT(any_guard_active,
                    "POOL_COPY implies at least one "
                    "guard active");
            }
        }
    }

    TEST_PASS(
        "Property 2: POOL_COPY -> guard active "
        "(25000 inputs)");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Property 2: Hybrid Decision Matrix Correctness\n"
        "Validates: Requirements 3.1, 3.2, 3.3, 3.4");

    /* Exhaustive 8-combination tests */
    test_property2_exhaustive_decision_matrix();
    test_property2_named_cases();

    /* Random sequence property tests */
    test_property2_random_sequences();

    /* Edge case / boundary tests */
    test_property2_nonboolean_truthy_values();
    test_property2_gate_value_strict_equality();

    /* Contrapositive / logical property tests */
    test_property2_zero_copy_implies_all_clear();
    test_property2_pool_copy_implies_guard_active();

    printf("\n");
    TEST_PASS(
        "hybrid_decision_property: all property tests "
        "passed");
    return 0;
}
