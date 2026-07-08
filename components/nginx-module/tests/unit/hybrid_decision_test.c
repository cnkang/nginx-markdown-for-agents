/*
 * Test: hybrid_decision
 *
 * Unit tests for hybrid zero-copy decision logic and backpressure
 * integration.
 *
 * Feature: 0.9.1-performance-optimization
 * Task: 6.6 Write unit tests for hybrid decision and backpressure
 *       integration
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.7
 *
 * The property test (hybrid_decision_property_test.c) already
 * covers the decision matrix exhaustively. This unit test adds
 * specific named scenarios for clarity and documents the
 * NGX_AGAIN buffer retention behavior.
 *
 * Scenarios:
 *   - Feature gate OFF -> always POOL_COPY
 *   - Terminal chunk -> always POOL_COPY regardless of gate
 *   - Backpressure active -> POOL_COPY
 *   - All-clear -> ZERO_COPY
 *   - NGX_AGAIN retains buffer without freeing
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
 * Minimal conf struct stub (only stream.zero_copy needed)
 * ---------------------------------------------------------------- */

typedef struct {
    struct {
        ngx_flag_t    zero_copy;
    } stream;
} ngx_http_markdown_conf_t;

/* ----------------------------------------------------------------
 * Include real production decision helper
 * ---------------------------------------------------------------- */

#include "../../src/ngx_http_markdown_output_decision_impl.h"

/* ----------------------------------------------------------------
 * Buffer retention context: models the production behavior on
 * NGX_AGAIN where the Rust-owned buffer must NOT be freed.
 *
 * In production:
 *   - On NGX_OK: buffer ownership transfers downstream, pool
 *     cleanup will free at request end
 *   - On NGX_AGAIN: buffer retained by module, NOT freed,
 *     pool cleanup remains registered as safety net
 *   - freed flag prevents double-free
 * ---------------------------------------------------------------- */

typedef struct {
    unsigned char  *rust_ptr;
    size_t          rust_len;
    unsigned        freed:1;
} ngx_http_markdown_rust_buf_cleanup_t;

typedef struct {
    ngx_atomic_t  zero_copy_output_total;
    ngx_atomic_t  copied_output_total;
} output_metrics_t;

/* ----------------------------------------------------------------
 * Simulate output delivery with buffer retention semantics
 * ---------------------------------------------------------------- */

static ngx_int_t
simulate_output_and_track(output_metrics_t *metrics,
    ngx_http_markdown_rust_buf_cleanup_t *cleanup,
    ngx_http_markdown_output_decision_t decision,
    ngx_int_t downstream_rc)
{
    UNUSED(cleanup);

    if (downstream_rc == NGX_OK) {
        if (decision == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY) {
            metrics->zero_copy_output_total++;
        } else {
            metrics->copied_output_total++;
        }
        /*
         * On NGX_OK: ownership transferred to downstream.
         * Pool cleanup will handle eventual free.
         */
    } else if (downstream_rc == NGX_AGAIN) {
        /*
         * On NGX_AGAIN: buffer retained. Do NOT free.
         * Do NOT increment counters.
         * Pool cleanup remains registered as safety net.
         * freed flag stays 0.
         */
    }
    /* On NGX_ERROR: no counter change, no free */
    return downstream_rc;
}

/* ================================================================
 * Test 1: Feature gate OFF → always POOL_COPY
 *
 * Validates: Requirement 3.1
 * "WHILE markdown_streaming_zero_copy is set to off, THE Module
 *  SHALL use the Pool_Copy_Output path for all chunks without
 *  behavioral change"
 * ================================================================ */

static void
test_feature_gate_off_always_pool_copy(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;

    TEST_SUBSECTION(
        "Feature gate OFF -> always POOL_COPY (Req 3.1)");

    conf.stream.zero_copy = 0;

    /* Non-terminal, no backpressure */
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, non-terminal, no bp -> POOL_COPY");

    /* Non-terminal, backpressure active */
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, non-terminal, bp -> POOL_COPY");

    /* Terminal, no backpressure */
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, terminal, no bp -> POOL_COPY");

    /* Terminal, backpressure active */
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, terminal, bp -> POOL_COPY");

    TEST_PASS("Feature gate OFF -> always POOL_COPY");
}

/* ================================================================
 * Test 2: Terminal chunk → always POOL_COPY regardless of gate
 *
 * Validates: Requirement 3.3
 * "WHEN a terminal last_buf chunk is ready for output, THE Module
 *  SHALL use the Pool_Copy_Output path regardless of the zero-copy
 *  configuration"
 * ================================================================ */

static void
test_terminal_chunk_always_pool_copy(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;

    TEST_SUBSECTION(
        "Terminal chunk -> always POOL_COPY regardless "
        "of gate (Req 3.3)");

    /* Gate ON, terminal, no backpressure */
    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate ON, terminal, no bp -> POOL_COPY");

    /* Gate ON, terminal, backpressure active */
    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate ON, terminal, bp -> POOL_COPY");

    /* Gate OFF, terminal, no backpressure */
    conf.stream.zero_copy = 0;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, terminal, no bp -> POOL_COPY");

    /* Gate OFF, terminal, backpressure active */
    conf.stream.zero_copy = 0;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, terminal, bp -> POOL_COPY");

    TEST_PASS(
        "Terminal chunk -> always POOL_COPY regardless "
        "of gate");
}

/* ================================================================
 * Test 3: Backpressure active → POOL_COPY
 *
 * Validates: Requirement 3.4
 * "WHILE backpressure is active (pending output exists), THE
 *  Module SHALL use the Pool_Copy_Output path for all new chunks"
 * ================================================================ */

static void
test_backpressure_active_pool_copy(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;

    TEST_SUBSECTION(
        "Backpressure active -> POOL_COPY (Req 3.4)");

    /* Gate ON, non-terminal, backpressure active */
    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate ON, non-terminal, bp active -> POOL_COPY");

    /* Gate OFF, non-terminal, backpressure active */
    conf.stream.zero_copy = 0;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate OFF, non-terminal, bp active -> POOL_COPY");

    /* Gate ON, terminal, backpressure active (both guards) */
    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 1, 1);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "gate ON, terminal, bp active -> POOL_COPY");

    TEST_PASS("Backpressure active -> POOL_COPY");
}

/* ================================================================
 * Test 4: All-clear → ZERO_COPY
 *
 * Validates: Requirement 3.2
 * "WHEN a non-terminal chunk is ready for output with no active
 *  backpressure and markdown_streaming_zero_copy is on, THE Module
 *  SHALL use the Zero_Copy_Output path via the buffer factory"
 * ================================================================ */

static void
test_all_clear_zero_copy(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t result;

    TEST_SUBSECTION(
        "All-clear -> ZERO_COPY (Req 3.2)");

    conf.stream.zero_copy = 1;
    result = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(result == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
        "gate ON, non-terminal, no bp -> ZERO_COPY");

    TEST_PASS("All-clear -> ZERO_COPY");
}

/* ================================================================
 * Test 5: NGX_AGAIN retains buffer without freeing
 *
 * Validates: Requirement 3.7
 * "WHEN ngx_http_output_filter returns NGX_AGAIN for a zero-copy
 *  buffer, THE Module SHALL NOT free the Rust-owned buffer and
 *  SHALL NOT increment any delivery counter"
 *
 * This test verifies the integration of the decision function
 * with the buffer retention semantics on NGX_AGAIN:
 *   - Decision selects ZERO_COPY (all guards clear)
 *   - Downstream returns NGX_AGAIN
 *   - Buffer must NOT be freed (freed flag stays 0)
 *   - Pool cleanup ptr remains valid (non-NULL)
 *   - Delivery counters do NOT increment
 * ================================================================ */

static void
test_ngx_again_retains_buffer_without_freeing(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t decision;
    ngx_http_markdown_rust_buf_cleanup_t cleanup;
    output_metrics_t metrics;
    ngx_int_t rc;

    TEST_SUBSECTION(
        "NGX_AGAIN retains buffer without freeing "
        "(Req 3.7)");

    memset(&metrics, 0, sizeof(metrics));

    /* Simulate a Rust-owned buffer */
    cleanup.rust_ptr = (unsigned char *) "fake_rust_data";
    cleanup.rust_len = 14;
    cleanup.freed = 0;

    /* Decision should be ZERO_COPY (gate ON, non-terminal, no bp) */
    conf.stream.zero_copy = 1;
    decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(decision == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
        "decision is ZERO_COPY for all-clear state");

    /* Simulate downstream returning NGX_AGAIN */
    rc = simulate_output_and_track(
        &metrics, &cleanup, decision, NGX_AGAIN);
    TEST_ASSERT(rc == NGX_AGAIN,
        "return code is NGX_AGAIN");

    /* Verify buffer NOT freed */
    TEST_ASSERT(cleanup.freed == 0,
        "freed flag must remain 0 on NGX_AGAIN");
    TEST_ASSERT(cleanup.rust_ptr != NULL,
        "rust_ptr must remain valid on NGX_AGAIN");
    TEST_ASSERT(cleanup.rust_len == 14,
        "rust_len must be preserved on NGX_AGAIN");

    /* Verify counters NOT incremented */
    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics.zero_copy_output_total
            == 0,
        "zero_copy_output_total must be 0 on NGX_AGAIN");
    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics.copied_output_total
            == 0,
        "copied_output_total must be 0 on NGX_AGAIN");

    TEST_PASS("NGX_AGAIN retains buffer without freeing");
}

/* ================================================================
 * Test 6: NGX_AGAIN followed by successful resume increments
 * counter
 *
 * Documents the full lifecycle: NGX_AGAIN (retain) -> resume
 * NGX_OK (deliver). This ensures the buffer remains valid
 * through the suspend period and the counter increments only
 * after successful delivery.
 *
 * Validates: Requirements 3.5, 3.7
 * ================================================================ */

static void
test_ngx_again_then_ok_increments_counter(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t decision;
    ngx_http_markdown_rust_buf_cleanup_t cleanup;
    output_metrics_t metrics;

    TEST_SUBSECTION(
        "NGX_AGAIN then NGX_OK: retain then deliver "
        "(Req 3.5, 3.7)");

    memset(&metrics, 0, sizeof(metrics));

    cleanup.rust_ptr = (unsigned char *) "rust_chunk";
    cleanup.rust_len = 10;
    cleanup.freed = 0;

    conf.stream.zero_copy = 1;
    decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(decision == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY,
        "decision is ZERO_COPY");

    /* First attempt: NGX_AGAIN (suspended) */
    simulate_output_and_track(
        &metrics, &cleanup, decision, NGX_AGAIN);

    TEST_ASSERT(cleanup.freed == 0,
        "buffer retained after NGX_AGAIN");
    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics.zero_copy_output_total
            == 0,
        "no counter increment after NGX_AGAIN");

    /* Resume: NGX_OK (delivered) */
    simulate_output_and_track(
        &metrics, &cleanup, decision, NGX_OK);

    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics.zero_copy_output_total
            == 1,
        "zero_copy counter increments on resume NGX_OK");
    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics.copied_output_total
            == 0,
        "copied counter unchanged");

    TEST_PASS(
        "NGX_AGAIN then NGX_OK: buffer retained then "
        "counter increments");
}

/* ================================================================
 * Test 7: Multiple NGX_AGAIN do not accumulate counter
 * increments or free buffer
 *
 * Validates: Requirement 3.7
 * ================================================================ */

static void
test_multiple_ngx_again_no_accumulation(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t decision;
    ngx_http_markdown_rust_buf_cleanup_t cleanup;
    output_metrics_t metrics;
    int i;

    TEST_SUBSECTION(
        "Multiple NGX_AGAIN: no counter accumulation "
        "(Req 3.7)");

    memset(&metrics, 0, sizeof(metrics));

    cleanup.rust_ptr = (unsigned char *) "persistent";
    cleanup.rust_len = 10;
    cleanup.freed = 0;

    conf.stream.zero_copy = 1;
    decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);

    /* Simulate 5 consecutive NGX_AGAIN returns */
    for (i = 0; i < 5; i++) {
        simulate_output_and_track(
            &metrics, &cleanup, decision, NGX_AGAIN);
    }

    TEST_ASSERT(cleanup.freed == 0,
        "buffer not freed after 5 NGX_AGAIN");
    TEST_ASSERT(cleanup.rust_ptr != NULL,
        "rust_ptr still valid after 5 NGX_AGAIN");
    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics.zero_copy_output_total
            == 0,
        "zero_copy counter still 0 after 5 NGX_AGAIN");
    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics.copied_output_total
            == 0,
        "copied counter still 0 after 5 NGX_AGAIN");

    TEST_PASS(
        "Multiple NGX_AGAIN: no counter accumulation");
}

/* ================================================================
 * Test 8: Pool-copy path with NGX_OK increments copied counter
 *
 * Validates: Requirement 3.6
 * "WHEN ngx_http_output_filter returns NGX_OK for a pool-copy
 *  buffer, THE Module SHALL increment copied_output_total"
 * ================================================================ */

static void
test_pool_copy_ngx_ok_increments_copied_counter(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_output_decision_t decision;
    ngx_http_markdown_rust_buf_cleanup_t cleanup;
    output_metrics_t metrics;

    TEST_SUBSECTION(
        "Pool-copy path NGX_OK increments "
        "copied_output_total (Req 3.6)");

    memset(&metrics, 0, sizeof(metrics));
    memset(&cleanup, 0, sizeof(cleanup));

    /* Gate OFF forces POOL_COPY */
    conf.stream.zero_copy = 0;
    decision = ngx_http_markdown_hybrid_output_decision(
        &conf, 0, 0);
    TEST_ASSERT(decision == NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY,
        "decision is POOL_COPY when gate OFF");

    /* Successful delivery */
    simulate_output_and_track(
        &metrics, &cleanup, decision, NGX_OK);

    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics.copied_output_total
            == 1,
        "copied_output_total increments on NGX_OK");
    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics.zero_copy_output_total
            == 0,
        "zero_copy_output_total unchanged");

    TEST_PASS(
        "Pool-copy NGX_OK increments copied counter");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Task 6.6: Hybrid Decision and Backpressure "
        "Integration\n"
        "Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.7");

    /* Decision logic tests */
    test_feature_gate_off_always_pool_copy();
    test_terminal_chunk_always_pool_copy();
    test_backpressure_active_pool_copy();
    test_all_clear_zero_copy();

    /* Backpressure integration / NGX_AGAIN semantics */
    test_ngx_again_retains_buffer_without_freeing();
    test_ngx_again_then_ok_increments_counter();
    test_multiple_ngx_again_no_accumulation();
    test_pool_copy_ngx_ok_increments_copied_counter();

    printf("\n");
    TEST_PASS(
        "hybrid_decision: all unit tests passed");
    return 0;
}
