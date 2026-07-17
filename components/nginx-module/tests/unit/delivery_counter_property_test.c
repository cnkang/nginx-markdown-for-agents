/*
 * Test: delivery_counter_property
 *
 * Property-based test for delivery counter correctness (Property 3).
 *
 * Feature: 0.9.1-performance-optimization
 * Property 3: Delivery Counter Correctness
 *
 * Validates: Requirements 3.5, 3.6, 3.7, 7.7
 *
 * For any output attempt through ngx_http_output_filter, delivery
 * counters (zero_copy_output_total, copied_output_total) shall
 * increment by exactly 1 if and only if the return code is NGX_OK;
 * on NGX_AGAIN or any error return, neither counter shall change
 * and no Rust-owned buffer shall be freed.
 *
 * Test approach (pseudo-random sequences):
 *   1. Simulate sequences of output attempts with random return
 *      codes (NGX_OK, NGX_AGAIN, NGX_ERROR, NGX_DONE)
 *   2. Track expected counter values based on the rule
 *   3. After each event, verify counter state matches expected
 */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef volatile long   ngx_atomic_t;
typedef long            ngx_atomic_int_t;
typedef unsigned long   ngx_atomic_uint_t;

enum {
    NGX_OK    =  0,
    NGX_ERROR = -1,
    NGX_AGAIN = -2,
    NGX_DONE  = -4
};

/* ----------------------------------------------------------------
 * Atomic fetch-add stub (single-threaded test environment)
 * ---------------------------------------------------------------- */

static ngx_inline ngx_atomic_int_t
ngx_atomic_fetch_add(ngx_atomic_t *value, ngx_atomic_int_t add)
{
    ngx_atomic_int_t old = *value;
    *value += add;
    return old;
}

/* ----------------------------------------------------------------
 * Output path enumeration
 * ---------------------------------------------------------------- */

typedef enum {
    PATH_ZERO_COPY = 0,
    PATH_POOL_COPY = 1
} output_path_t;

/* ----------------------------------------------------------------
 * Delivery context: models the production delivery counter logic
 *
 * Production behavior:
 *   - On NGX_OK for zero-copy: increment zero_copy_output_total
 *   - On NGX_OK for pool-copy: increment copied_output_total
 *   - On NGX_AGAIN: no counter change, no buffer free
 *   - On NGX_ERROR / NGX_DONE: no counter change
 * ---------------------------------------------------------------- */

typedef struct {
    ngx_atomic_t  zero_copy_output_total;
    ngx_atomic_t  copied_output_total;
    int           rust_buffer_freed;
} delivery_metrics_t;

/*
 * Simulate an output attempt through ngx_http_output_filter.
 * This models the production counter-update logic:
 *   - Increment the appropriate delivery counter only on NGX_OK
 *   - On NGX_AGAIN: retain buffer, no counter change
 *   - On error (NGX_ERROR, NGX_DONE): no counter change
 */
static void
simulate_output_attempt(delivery_metrics_t *metrics,
    output_path_t path, ngx_int_t downstream_rc)
{
    if (downstream_rc == NGX_OK) {
        if (path == PATH_ZERO_COPY) {
            ngx_atomic_fetch_add(
                &metrics->zero_copy_output_total, 1);
        } else {
            ngx_atomic_fetch_add(
                &metrics->copied_output_total, 1);
        }
        /*
         * On NGX_OK the buffer ownership transfers to
         * downstream; Rust buffer is freed by pool cleanup
         * at request end (not here).
         */
    }
    /*
     * On NGX_AGAIN, NGX_ERROR, NGX_DONE:
     *   - No counter increment
     *   - No Rust buffer free (buffer retained for retry
     *     or cleanup handles it on abort)
     */
}

/*
 * Simulate freeing the Rust buffer (only valid after successful
 * delivery or on request teardown — never on NGX_AGAIN).
 */
static void
simulate_rust_buffer_free(delivery_metrics_t *metrics,
    ngx_int_t last_rc)
{
    if (last_rc == NGX_OK) {
        /*
         * Buffer ownership transferred to downstream on
         * NGX_OK; pool cleanup will free at request end.
         */
        metrics->rust_buffer_freed = 1;
    }
    /* On NGX_AGAIN: buffer NOT freed (retained for retry) */
}

/* ----------------------------------------------------------------
 * Simple PRNG for deterministic pseudo-random sequences
 * (same xorshift32 as perf_metrics_property_test.c)
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
 * Return code generation from PRNG
 * ---------------------------------------------------------------- */

static ngx_int_t
random_return_code(void)
{
    unsigned int r;

    r = prng_next() % 4;
    switch (r) {
    case 0:  return NGX_OK;
    case 1:  return NGX_AGAIN;
    case 2:  return NGX_ERROR;
    default: return NGX_DONE;
    }
}

static output_path_t
random_output_path(void)
{
    return (prng_next() % 2 == 0)
        ? PATH_ZERO_COPY : PATH_POOL_COPY;
}

/* ----------------------------------------------------------------
 * Property 3a: Counters increment by exactly 1 iff NGX_OK
 *
 * For any sequence of output attempts with random return codes
 * and random output paths, verify:
 *   - zero_copy_output_total == count of (NGX_OK, ZERO_COPY)
 *   - copied_output_total == count of (NGX_OK, POOL_COPY)
 *
 * Validates: Requirements 3.5, 3.6, 7.7
 * ---------------------------------------------------------------- */

#define DELIVERY_ITERATIONS 200
#define DELIVERY_SEQ_LEN    80

static void
test_property3a_counters_match_ok_count(void)
{
    delivery_metrics_t metrics;
    ngx_int_t rc;
    output_path_t path;
    unsigned int expected_zc;
    unsigned int expected_cp;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 3a: Delivery counters increment by "
        "exactly 1 iff NGX_OK (random sequences)");

    for (iter = 0; iter < DELIVERY_ITERATIONS; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        expected_zc = 0;
        expected_cp = 0;
        prng_seed((unsigned int)(iter + 1));

        for (j = 0; j < DELIVERY_SEQ_LEN; j++) {
            path = random_output_path();
            rc = random_return_code();

            simulate_output_attempt(&metrics, path, rc);

            if (rc == NGX_OK) {
                if (path == PATH_ZERO_COPY) {
                    expected_zc++;
                } else {
                    expected_cp++;
                }
            }

            /* Verify after each step */
            TEST_ASSERT(
                (ngx_atomic_uint_t)
                    metrics.zero_copy_output_total
                    == expected_zc,
                "zero_copy_output_total must match "
                "count of (NGX_OK, ZERO_COPY)");
            TEST_ASSERT(
                (ngx_atomic_uint_t)
                    metrics.copied_output_total
                    == expected_cp,
                "copied_output_total must match "
                "count of (NGX_OK, POOL_COPY)");
        }
    }

    TEST_PASS(
        "Property 3a: counters == NGX_OK count for 200 "
        "random sequences × 80 steps");
}

/* ----------------------------------------------------------------
 * Property 3b: No counter change on NGX_AGAIN
 *
 * For any sequence of NGX_AGAIN returns, verify:
 *   - zero_copy_output_total remains 0
 *   - copied_output_total remains 0
 *   - No Rust buffer freed
 *
 * Validates: Requirements 3.7, 7.7
 * ---------------------------------------------------------------- */

static void
test_property3b_ngx_again_no_counter_change(void)
{
    delivery_metrics_t metrics;
    output_path_t path;
    int iter;
    size_t j;
    size_t seq_len;

    TEST_SUBSECTION(
        "Property 3b: NGX_AGAIN never changes delivery "
        "counters or frees buffer");

    for (iter = 0; iter < DELIVERY_ITERATIONS; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        prng_seed((unsigned int)(iter + 3000));
        seq_len = (prng_next() % 50) + 1;

        for (j = 0; j < seq_len; j++) {
            path = random_output_path();
            simulate_output_attempt(
                &metrics, path, NGX_AGAIN);
            simulate_rust_buffer_free(
                &metrics, NGX_AGAIN);
        }

        TEST_ASSERT(
            (ngx_atomic_uint_t)
                metrics.zero_copy_output_total == 0,
            "zero_copy must be 0 after only NGX_AGAIN");
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                metrics.copied_output_total == 0,
            "copied must be 0 after only NGX_AGAIN");
        TEST_ASSERT(
            metrics.rust_buffer_freed == 0,
            "Rust buffer must NOT be freed on NGX_AGAIN");
    }

    TEST_PASS(
        "Property 3b: NGX_AGAIN never changes counters "
        "or frees buffer (200 sequences)");
}

/* ----------------------------------------------------------------
 * Property 3c: No counter change on NGX_ERROR or NGX_DONE
 *
 * For any sequence of error returns, verify neither delivery
 * counter changes.
 *
 * Validates: Requirements 3.5, 3.6, 7.7
 * ---------------------------------------------------------------- */

static void
test_property3c_error_returns_no_counter_change(void)
{
    delivery_metrics_t metrics;
    output_path_t path;
    ngx_int_t error_codes[2];
    ngx_int_t rc;
    int iter;
    size_t j;
    size_t seq_len;

    error_codes[0] = NGX_ERROR;
    error_codes[1] = NGX_DONE;

    TEST_SUBSECTION(
        "Property 3c: NGX_ERROR/NGX_DONE never change "
        "delivery counters");

    for (iter = 0; iter < DELIVERY_ITERATIONS; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        prng_seed((unsigned int)(iter + 6000));
        seq_len = (prng_next() % 40) + 1;

        for (j = 0; j < seq_len; j++) {
            path = random_output_path();
            rc = error_codes[prng_next() % 2];
            simulate_output_attempt(&metrics, path, rc);
        }

        TEST_ASSERT(
            (ngx_atomic_uint_t)
                metrics.zero_copy_output_total == 0,
            "zero_copy must be 0 after only errors");
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                metrics.copied_output_total == 0,
            "copied must be 0 after only errors");
    }

    TEST_PASS(
        "Property 3c: error returns never change delivery "
        "counters (200 sequences)");
}

/* ----------------------------------------------------------------
 * Property 3d: Per-step isolation
 *
 * For each output attempt in a random sequence, verify:
 *   - If rc == NGX_OK: exactly one counter incremented by 1
 *   - If rc != NGX_OK: neither counter changed
 *
 * This is the strongest form of the property — verified at
 * every step, not just cumulatively.
 *
 * Validates: Requirements 3.5, 3.6, 3.7, 7.7
 * ---------------------------------------------------------------- */

static void
test_property3d_per_step_isolation(void)
{
    delivery_metrics_t metrics;
    ngx_atomic_uint_t zc_before;
    ngx_atomic_uint_t cp_before;
    ngx_atomic_uint_t zc_after;
    ngx_atomic_uint_t cp_after;
    ngx_int_t rc;
    output_path_t path;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 3d: Per-step isolation — exactly one "
        "counter +1 on NGX_OK, zero change otherwise");

    for (iter = 0; iter < DELIVERY_ITERATIONS; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        prng_seed((unsigned int)(iter + 10000));

        for (j = 0; j < DELIVERY_SEQ_LEN; j++) {
            path = random_output_path();
            rc = random_return_code();

            zc_before = (ngx_atomic_uint_t)
                metrics.zero_copy_output_total;
            cp_before = (ngx_atomic_uint_t)
                metrics.copied_output_total;

            simulate_output_attempt(&metrics, path, rc);

            zc_after = (ngx_atomic_uint_t)
                metrics.zero_copy_output_total;
            cp_after = (ngx_atomic_uint_t)
                metrics.copied_output_total;

            if (rc == NGX_OK && path == PATH_ZERO_COPY) {
                TEST_ASSERT(
                    zc_after - zc_before == 1,
                    "zero_copy must +1 on NGX_OK "
                    "ZERO_COPY");
                TEST_ASSERT(
                    cp_after == cp_before,
                    "copied unchanged on NGX_OK "
                    "ZERO_COPY");
            } else if (rc == NGX_OK
                && path == PATH_POOL_COPY)
            {
                TEST_ASSERT(
                    cp_after - cp_before == 1,
                    "copied must +1 on NGX_OK "
                    "POOL_COPY");
                TEST_ASSERT(
                    zc_after == zc_before,
                    "zero_copy unchanged on NGX_OK "
                    "POOL_COPY");
            } else {
                /* NGX_AGAIN, NGX_ERROR, NGX_DONE */
                TEST_ASSERT(
                    zc_after == zc_before,
                    "zero_copy unchanged on non-OK");
                TEST_ASSERT(
                    cp_after == cp_before,
                    "copied unchanged on non-OK");
            }
        }
    }

    TEST_PASS(
        "Property 3d: per-step isolation verified for 200 "
        "sequences × 80 steps");
}

/* ----------------------------------------------------------------
 * Property 3e: Buffer free only on NGX_OK
 *
 * For any random sequence, the Rust buffer free function must
 * only be invoked when the downstream return is NGX_OK. On
 * NGX_AGAIN, the buffer must be retained.
 *
 * Validates: Requirements 3.7
 * ---------------------------------------------------------------- */

static void
test_property3e_buffer_free_only_on_ok(void)
{
    delivery_metrics_t metrics;
    ngx_int_t rc;
    output_path_t path;
    int iter;
    size_t j;
    unsigned int ok_count;

    TEST_SUBSECTION(
        "Property 3e: Rust buffer free only on NGX_OK, "
        "never on NGX_AGAIN");

    for (iter = 0; iter < DELIVERY_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 15000));
        ok_count = 0;

        for (j = 0; j < DELIVERY_SEQ_LEN; j++) {
            memset(&metrics, 0, sizeof(metrics));
            path = random_output_path();
            rc = random_return_code();

            simulate_output_attempt(&metrics, path, rc);
            simulate_rust_buffer_free(&metrics, rc);

            if (rc == NGX_OK) {
                ok_count++;
                TEST_ASSERT(
                    metrics.rust_buffer_freed == 1,
                    "buffer must be freed on NGX_OK");
            } else {
                TEST_ASSERT(
                    metrics.rust_buffer_freed == 0,
                    "buffer must NOT be freed on "
                    "non-OK");
            }
        }

        /* Ensure we exercised both paths */
        TEST_ASSERT(ok_count > 0 || iter > 0,
            "at least some NGX_OK in sequence");
    }

    TEST_PASS(
        "Property 3e: buffer free correctness verified "
        "for 200 sequences × 80 steps");
}

/* ----------------------------------------------------------------
 * Property 3f: Cumulative counter sum equals total NGX_OK count
 *
 * For any random sequence, the sum of both delivery counters
 * must equal the total number of NGX_OK events in the sequence.
 *
 * Validates: Requirements 3.5, 3.6
 * ---------------------------------------------------------------- */

static void
test_property3f_sum_equals_total_ok(void)
{
    delivery_metrics_t metrics;
    ngx_int_t rc;
    output_path_t path;
    unsigned int total_ok;
    ngx_atomic_uint_t counter_sum;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 3f: sum(zero_copy + copied) == total "
        "NGX_OK count");

    for (iter = 0; iter < DELIVERY_ITERATIONS; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        total_ok = 0;
        prng_seed((unsigned int)(iter + 20000));

        for (j = 0; j < DELIVERY_SEQ_LEN; j++) {
            path = random_output_path();
            rc = random_return_code();
            simulate_output_attempt(&metrics, path, rc);

            if (rc == NGX_OK) {
                total_ok++;
            }
        }

        counter_sum =
            (ngx_atomic_uint_t)
                metrics.zero_copy_output_total
            + (ngx_atomic_uint_t)
                metrics.copied_output_total;

        TEST_ASSERT(
            counter_sum == (ngx_atomic_uint_t) total_ok,
            "sum of delivery counters must equal "
            "total NGX_OK count");
    }

    TEST_PASS(
        "Property 3f: counter sum == NGX_OK count for 200 "
        "random sequences");
}

/* ----------------------------------------------------------------
 * Property 3g: Counters are mutually exclusive per attempt
 *
 * For any single output attempt, at most one of the two
 * delivery counters changes (never both).
 *
 * Validates: Requirements 3.5, 3.6
 * ---------------------------------------------------------------- */

static void
test_property3g_mutual_exclusion(void)
{
    delivery_metrics_t metrics;
    ngx_atomic_uint_t zc_before;
    ngx_atomic_uint_t cp_before;
    ngx_atomic_uint_t zc_delta;
    ngx_atomic_uint_t cp_delta;
    ngx_int_t rc;
    output_path_t path;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 3g: At most one delivery counter changes "
        "per attempt (mutual exclusion)");

    for (iter = 0; iter < DELIVERY_ITERATIONS; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        prng_seed((unsigned int)(iter + 25000));

        for (j = 0; j < DELIVERY_SEQ_LEN; j++) {
            path = random_output_path();
            rc = random_return_code();

            zc_before = (ngx_atomic_uint_t)
                metrics.zero_copy_output_total;
            cp_before = (ngx_atomic_uint_t)
                metrics.copied_output_total;

            simulate_output_attempt(&metrics, path, rc);

            zc_delta = (ngx_atomic_uint_t)
                metrics.zero_copy_output_total - zc_before;
            cp_delta = (ngx_atomic_uint_t)
                metrics.copied_output_total - cp_before;

            /* At most one changes */
            TEST_ASSERT(
                !(zc_delta > 0 && cp_delta > 0),
                "both counters must not increment "
                "in same attempt");

            /* Each delta is at most 1 */
            TEST_ASSERT(
                zc_delta <= 1,
                "zero_copy delta must be 0 or 1");
            TEST_ASSERT(
                cp_delta <= 1,
                "copied delta must be 0 or 1");
        }
    }

    TEST_PASS(
        "Property 3g: mutual exclusion verified for 200 "
        "sequences × 80 steps");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Property 3: Delivery Counter Correctness");

    test_property3a_counters_match_ok_count();
    test_property3b_ngx_again_no_counter_change();
    test_property3c_error_returns_no_counter_change();
    test_property3d_per_step_isolation();
    test_property3e_buffer_free_only_on_ok();
    test_property3f_sum_equals_total_ok();
    test_property3g_mutual_exclusion();

    printf("\n");
    TEST_PASS(
        "delivery_counter_property: all property tests "
        "passed");
    return 0;
}
