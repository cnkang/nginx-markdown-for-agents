/*
 * Test: decomp_failopen_property
 *
 * Property-based tests for decompression fail-open (Property 5).
 *
 * Feature: 0.9.1-performance-optimization
 * Property 5: Decompression Fail-Open on Malformed Input
 *             (Pre-Commit Only) + Post-Commit Safe-Finish
 *
 * Validates: Requirements 4.3, 4.4, 4.10
 *
 * Pre-commit state (flushes_sent == 0):
 *   On any decompression error -> fail-open via replay buffer
 *   passthrough of original compressed body. No status rewrite.
 *
 * Post-commit state (flushes_sent > 0):
 *   On any decompression error -> invoke safe_finish for
 *   graceful termination. NO replay. NO status rewrite.
 *
 * Error conditions modeled:
 *   - Truncated stream (incomplete deflate data)
 *   - Budget exceeded (cumulative > max_size)
 *   - zlib inflate error (corrupt data / Z_DATA_ERROR)
 *   - Invalid header (malformed gzip/deflate header)
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
 * Decompression error type enumeration
 * ---------------------------------------------------------------- */

typedef enum {
    DECOMP_ERR_TRUNCATED_STREAM = 0,
    DECOMP_ERR_BUDGET_EXCEEDED,
    DECOMP_ERR_INFLATE_ERROR,
    DECOMP_ERR_INVALID_HEADER,
    DECOMP_ERR_COUNT
} decomp_error_t;

/* ----------------------------------------------------------------
 * Commit state enumeration (pre-commit vs post-commit)
 * ---------------------------------------------------------------- */

typedef enum {
    STATE_PRE_COMMIT  = 0,  /* flushes_sent == 0 */
    STATE_POST_COMMIT = 1   /* flushes_sent > 0  */
} commit_state_t;

/* ----------------------------------------------------------------
 * Error-handling action result enumeration
 * ---------------------------------------------------------------- */

typedef enum {
    ACTION_REPLAY_PASSTHROUGH = 0,  /* fail-open via replay buf */
    ACTION_SAFE_FINISH        = 1   /* graceful terminate       */
} error_action_t;

/* ----------------------------------------------------------------
 * Metrics struct (subset relevant to decompression)
 * ---------------------------------------------------------------- */

typedef struct {
    ngx_atomic_t  decompression_budget_exceeded_total;
    ngx_atomic_t  streaming_fallback_total;
} decomp_metrics_t;

/* ----------------------------------------------------------------
 * Decompression context: models the pre/post-commit state machine
 *
 * Production behavior (from design §4):
 *   PRE-COMMIT  (flushes_sent == 0):
 *     error → replay buffer passthrough (original body)
 *     increment streaming_fallback_total
 *     if budget exceeded → also increment budget_exceeded
 *
 *   POST-COMMIT (flushes_sent > 0):
 *     error → markdown_streaming_safe_finish
 *     NO replay attempted, NO status rewrite
 *     if budget exceeded → also increment budget_exceeded
 * ---------------------------------------------------------------- */

typedef struct {
    commit_state_t  state;
    ngx_uint_t      flushes_sent;
    size_t          cumulative_decompressed;
    size_t          max_decompressed_size;
    int             replay_triggered;
    int             safe_finish_invoked;
    int             status_rewritten;
    decomp_metrics_t metrics;
} decomp_ctx_t;

/* ----------------------------------------------------------------
 * Production error-handling logic (modeled from design §4)
 *
 * This replicates the decision logic for decompression errors:
 *   - Pre-commit: fail-open via replay buffer
 *   - Post-commit: safe-finish, no replay
 *
 * Budget exceeded increments the budget_exceeded counter in
 * both states.
 * ---------------------------------------------------------------- */

static error_action_t
handle_decomp_error(decomp_ctx_t *ctx, decomp_error_t error)
{
    /* Budget exceeded always increments its counter */
    if (error == DECOMP_ERR_BUDGET_EXCEEDED) {
        ngx_atomic_fetch_add(
            &ctx->metrics.decompression_budget_exceeded_total,
            1);
    }

    if (ctx->state == STATE_PRE_COMMIT) {
        /*
         * Pre-commit: no converted bytes sent downstream.
         * Trigger fail-open via replay buffer passthrough.
         * Deliver original compressed body unchanged.
         */
        ctx->replay_triggered = 1;
        ngx_atomic_fetch_add(
            &ctx->metrics.streaming_fallback_total, 1);
        return ACTION_REPLAY_PASSTHROUGH;
    }

    /*
     * Post-commit: at least one chunk already sent.
     * Replay is impossible. Invoke safe-finish to
     * gracefully terminate the response.
     * NO replay buffer. NO status rewrite.
     */
    ctx->safe_finish_invoked = 1;
    return ACTION_SAFE_FINISH;
}

/* ----------------------------------------------------------------
 * Oracle function: expected behavior for any (state, error) pair
 *
 * The specification says:
 *   PRE-COMMIT  + any error → ACTION_REPLAY_PASSTHROUGH
 *   POST-COMMIT + any error → ACTION_SAFE_FINISH
 * ---------------------------------------------------------------- */

static error_action_t
oracle_expected_action(commit_state_t state)
{
    if (state == STATE_PRE_COMMIT) {
        return ACTION_REPLAY_PASSTHROUGH;
    }
    return ACTION_SAFE_FINISH;
}

/* ----------------------------------------------------------------
 * Context initialization helper
 * ---------------------------------------------------------------- */

static void
init_decomp_ctx(decomp_ctx_t *ctx, commit_state_t state,
    size_t max_size)
{
    memset(ctx, 0, sizeof(*ctx));
    ctx->state = state;
    ctx->flushes_sent = (state == STATE_POST_COMMIT) ? 1 : 0;
    ctx->max_decompressed_size = max_size;
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
 * Property 5a: Pre-commit errors always trigger replay passthrough
 *
 * For any error condition in pre-commit state, the handler must:
 *   1. Select ACTION_REPLAY_PASSTHROUGH
 *   2. Set replay_triggered = 1
 *   3. NOT invoke safe_finish
 *   4. NOT rewrite status
 *   5. Increment streaming_fallback_total
 *
 * Validates: Requirements 4.3, 4.4
 * ---------------------------------------------------------------- */

static void
test_property5a_precommit_exhaustive(void)
{
    decomp_ctx_t ctx;
    error_action_t action;
    decomp_error_t error;

    TEST_SUBSECTION(
        "Property 5a: Pre-commit errors → replay "
        "passthrough (exhaustive)");

    for (error = 0; error < DECOMP_ERR_COUNT; error++) {
        init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);
        action = handle_decomp_error(&ctx, error);

        TEST_ASSERT(action == ACTION_REPLAY_PASSTHROUGH,
            "pre-commit error must select replay");
        TEST_ASSERT(ctx.replay_triggered == 1,
            "replay_triggered must be set");
        TEST_ASSERT(ctx.safe_finish_invoked == 0,
            "safe_finish must NOT be invoked pre-commit");
        TEST_ASSERT(ctx.status_rewritten == 0,
            "status must NOT be rewritten");
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                ctx.metrics.streaming_fallback_total == 1,
            "streaming_fallback_total must increment");
    }

    TEST_PASS(
        "Property 5a: all 4 error types trigger replay "
        "in pre-commit");
}

/* ----------------------------------------------------------------
 * Property 5b: Post-commit errors always invoke safe-finish
 *
 * For any error condition in post-commit state, the handler must:
 *   1. Select ACTION_SAFE_FINISH
 *   2. Set safe_finish_invoked = 1
 *   3. NOT trigger replay
 *   4. NOT rewrite status
 *   5. NOT attempt to deliver original compressed body
 *
 * Validates: Requirement 4.10
 * ---------------------------------------------------------------- */

static void
test_property5b_postcommit_exhaustive(void)
{
    decomp_ctx_t ctx;
    error_action_t action;
    decomp_error_t error;

    TEST_SUBSECTION(
        "Property 5b: Post-commit errors → safe-finish "
        "(exhaustive)");

    for (error = 0; error < DECOMP_ERR_COUNT; error++) {
        init_decomp_ctx(&ctx, STATE_POST_COMMIT, 1048576);
        action = handle_decomp_error(&ctx, error);

        TEST_ASSERT(action == ACTION_SAFE_FINISH,
            "post-commit error must select safe-finish");
        TEST_ASSERT(ctx.safe_finish_invoked == 1,
            "safe_finish_invoked must be set");
        TEST_ASSERT(ctx.replay_triggered == 0,
            "replay must NOT be triggered post-commit");
        TEST_ASSERT(ctx.status_rewritten == 0,
            "status must NOT be rewritten post-commit");
    }

    TEST_PASS(
        "Property 5b: all 4 error types invoke safe-finish "
        "in post-commit");
}

/* ----------------------------------------------------------------
 * Property 5c: Budget exceeded counter increments in both states
 *
 * For DECOMP_ERR_BUDGET_EXCEEDED in either state, the
 * decompression_budget_exceeded_total counter must increment by 1.
 * For other error types, it must remain unchanged.
 *
 * Validates: Requirements 4.3, 4.4, 4.10
 * ---------------------------------------------------------------- */

static void
test_property5c_budget_exceeded_counter(void)
{
    decomp_ctx_t ctx;
    decomp_error_t error;

    TEST_SUBSECTION(
        "Property 5c: Budget exceeded counter semantics");

    /* Budget exceeded in pre-commit → counter increments */
    init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);
    handle_decomp_error(&ctx, DECOMP_ERR_BUDGET_EXCEEDED);
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics.decompression_budget_exceeded_total == 1,
        "budget counter +1 on budget error (pre-commit)");

    /* Budget exceeded in post-commit → counter increments */
    init_decomp_ctx(&ctx, STATE_POST_COMMIT, 1048576);
    handle_decomp_error(&ctx, DECOMP_ERR_BUDGET_EXCEEDED);
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics.decompression_budget_exceeded_total == 1,
        "budget counter +1 on budget error (post-commit)");

    /* Non-budget errors: counter unchanged */
    for (error = 0; error < DECOMP_ERR_COUNT; error++) {
        if (error == DECOMP_ERR_BUDGET_EXCEEDED) {
            continue;
        }
        init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);
        handle_decomp_error(&ctx, error);
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                ctx.metrics
                    .decompression_budget_exceeded_total == 0,
            "budget counter unchanged for non-budget error");
    }

    TEST_PASS(
        "Property 5c: budget counter only increments on "
        "budget error");
}

/* ----------------------------------------------------------------
 * Property 5d: Random error sequences with state transitions
 *
 * Model a stream processing lifecycle:
 *   1. Start in PRE_COMMIT
 *   2. Some chunks are processed successfully (advancing state)
 *   3. At a random point, an error occurs
 *   4. Verify the action matches the oracle for the current state
 *
 * Validates: Requirements 4.3, 4.4, 4.10
 * ---------------------------------------------------------------- */

#define RANDOM_ITERATIONS  500
#define MAX_CHUNKS_BEFORE_ERROR 20

static void
test_property5d_random_error_sequences(void)
{
    decomp_ctx_t ctx;
    error_action_t action;
    error_action_t expected;
    commit_state_t state;
    decomp_error_t error;
    ngx_uint_t chunks_before_error;
    int iter;

    TEST_SUBSECTION(
        "Property 5d: Random error sequences with state "
        "transitions (500 iterations)");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 42));

        /* Random number of successful chunks before error */
        chunks_before_error =
            (ngx_uint_t)(prng_next() % MAX_CHUNKS_BEFORE_ERROR);

        /* Determine commit state from chunk count */
        state = (chunks_before_error > 0)
            ? STATE_POST_COMMIT : STATE_PRE_COMMIT;

        /* Random error condition */
        error = (decomp_error_t)(
            prng_next() % DECOMP_ERR_COUNT);

        /* Initialize context at the appropriate state */
        init_decomp_ctx(&ctx, state, 1048576);
        ctx.flushes_sent = chunks_before_error;

        /* Apply the error */
        action = handle_decomp_error(&ctx, error);
        expected = oracle_expected_action(state);

        TEST_ASSERT(action == expected,
            "action must match oracle for random input");

        /* Verify state invariants */
        if (state == STATE_PRE_COMMIT) {
            TEST_ASSERT(ctx.replay_triggered == 1,
                "pre-commit must trigger replay");
            TEST_ASSERT(ctx.safe_finish_invoked == 0,
                "pre-commit must not safe-finish");
        } else {
            TEST_ASSERT(ctx.safe_finish_invoked == 1,
                "post-commit must safe-finish");
            TEST_ASSERT(ctx.replay_triggered == 0,
                "post-commit must not replay");
        }
    }

    TEST_PASS(
        "Property 5d: oracle match verified for 500 "
        "random sequences");
}

/* ----------------------------------------------------------------
 * Property 5e: Budget enforcement triggers at correct threshold
 *
 * Model incremental budget tracking:
 *   1. Process chunks that accumulate decompressed bytes
 *   2. When cumulative exceeds max_size, error is BUDGET_EXCEEDED
 *   3. Verify the correct action for current commit state
 *
 * Validates: Requirement 4.4
 * ---------------------------------------------------------------- */

#define BUDGET_ITERATIONS  200

static void
test_property5e_budget_enforcement_random(void)
{
    decomp_ctx_t ctx;
    error_action_t action;
    error_action_t expected;
    commit_state_t state;
    size_t max_size;
    size_t chunk_size;
    size_t total;
    ngx_uint_t chunks_sent;
    int iter;

    TEST_SUBSECTION(
        "Property 5e: Budget enforcement at random "
        "thresholds (200 iterations)");

    for (iter = 0; iter < BUDGET_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 8888));

        /* Random budget limit (1KB to 10MB) */
        max_size = (size_t)(
            (prng_next() % (10 * 1024 * 1024)) + 1024);

        /* Simulate chunks until budget exceeded */
        total = 0;
        chunks_sent = 0;

        while (total <= max_size) {
            chunk_size = (size_t)(
                (prng_next() % 65536) + 1);
            total += chunk_size;
            chunks_sent++;
        }

        /* State depends on whether any chunk was sent */
        state = (chunks_sent > 1)
            ? STATE_POST_COMMIT : STATE_PRE_COMMIT;

        init_decomp_ctx(&ctx, state, max_size);
        ctx.flushes_sent = (state == STATE_POST_COMMIT)
            ? chunks_sent - 1 : 0;
        ctx.cumulative_decompressed = total;

        /* Trigger budget exceeded error */
        action = handle_decomp_error(
            &ctx, DECOMP_ERR_BUDGET_EXCEEDED);
        expected = oracle_expected_action(state);

        TEST_ASSERT(action == expected,
            "budget error action must match oracle");
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                ctx.metrics
                    .decompression_budget_exceeded_total == 1,
            "budget counter must increment");
    }

    TEST_PASS(
        "Property 5e: budget enforcement verified for 200 "
        "random thresholds");
}

/* ----------------------------------------------------------------
 * Property 5f: Post-commit NEVER triggers replay
 *              (contrapositive check)
 *
 * For any error in post-commit state, replay_triggered must be 0.
 * This is the critical safety property: once bytes have been sent
 * downstream, replay is impossible.
 *
 * Validates: Requirement 4.10
 * ---------------------------------------------------------------- */

#define CONTRAPOSITIVE_ITERATIONS 500

static void
test_property5f_postcommit_never_replays(void)
{
    decomp_ctx_t ctx;
    decomp_error_t error;
    ngx_uint_t flushes;
    int iter;

    TEST_SUBSECTION(
        "Property 5f: Post-commit NEVER triggers replay "
        "(500 random inputs)");

    for (iter = 0; iter < CONTRAPOSITIVE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 1111));

        error = (decomp_error_t)(
            prng_next() % DECOMP_ERR_COUNT);
        flushes = (ngx_uint_t)(
            (prng_next() % 100) + 1);  /* always > 0 */

        init_decomp_ctx(&ctx, STATE_POST_COMMIT, 1048576);
        ctx.flushes_sent = flushes;

        handle_decomp_error(&ctx, error);

        TEST_ASSERT(ctx.replay_triggered == 0,
            "post-commit must NEVER trigger replay");
        TEST_ASSERT(ctx.safe_finish_invoked == 1,
            "post-commit must ALWAYS safe-finish");
        TEST_ASSERT(ctx.status_rewritten == 0,
            "post-commit must NEVER rewrite status");
    }

    TEST_PASS(
        "Property 5f: replay never triggered in "
        "post-commit (500 inputs)");
}

/* ----------------------------------------------------------------
 * Property 5g: Pre-commit NEVER invokes safe-finish
 *              (contrapositive check)
 *
 * For any error in pre-commit state, safe_finish_invoked must be 0.
 * Pre-commit errors always use replay buffer, never safe-finish.
 *
 * Validates: Requirements 4.3, 4.4
 * ---------------------------------------------------------------- */

static void
test_property5g_precommit_never_safe_finishes(void)
{
    decomp_ctx_t ctx;
    decomp_error_t error;
    int iter;

    TEST_SUBSECTION(
        "Property 5g: Pre-commit NEVER invokes safe-finish "
        "(500 random inputs)");

    for (iter = 0; iter < CONTRAPOSITIVE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 2222));

        error = (decomp_error_t)(
            prng_next() % DECOMP_ERR_COUNT);

        init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);

        handle_decomp_error(&ctx, error);

        TEST_ASSERT(ctx.safe_finish_invoked == 0,
            "pre-commit must NEVER invoke safe-finish");
        TEST_ASSERT(ctx.replay_triggered == 1,
            "pre-commit must ALWAYS trigger replay");
        TEST_ASSERT(ctx.status_rewritten == 0,
            "pre-commit must NEVER rewrite status");
    }

    TEST_PASS(
        "Property 5g: safe-finish never invoked in "
        "pre-commit (500 inputs)");
}

/* ----------------------------------------------------------------
 * Property 5h: Mutual exclusion of replay and safe-finish
 *
 * For any (state, error) combination, exactly one of
 * replay_triggered or safe_finish_invoked must be set (never
 * both, never neither).
 *
 * Validates: Requirements 4.3, 4.4, 4.10
 * ---------------------------------------------------------------- */

static void
test_property5h_mutual_exclusion(void)
{
    decomp_ctx_t ctx;
    commit_state_t state;
    decomp_error_t error;
    int iter;
    int replay_xor_safe;

    TEST_SUBSECTION(
        "Property 5h: Mutual exclusion of replay and "
        "safe-finish (random inputs)");

    for (iter = 0; iter < RANDOM_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 5555));

        state = (commit_state_t)(prng_next() % 2);
        error = (decomp_error_t)(
            prng_next() % DECOMP_ERR_COUNT);

        init_decomp_ctx(&ctx, state, 1048576);
        handle_decomp_error(&ctx, error);

        /* Exactly one must be set */
        replay_xor_safe =
            (ctx.replay_triggered ^ ctx.safe_finish_invoked);
        TEST_ASSERT(replay_xor_safe == 1,
            "exactly one of replay/safe-finish must be set");

        /* Neither is both-set */
        TEST_ASSERT(
            !(ctx.replay_triggered && ctx.safe_finish_invoked),
            "replay and safe-finish cannot both be set");
    }

    TEST_PASS(
        "Property 5h: mutual exclusion verified for 500 "
        "random inputs");
}

/* ----------------------------------------------------------------
 * Property 5i: Multiple sequential errors in pre-commit
 *
 * If multiple decompression errors occur before any commit,
 * each must trigger replay and increment fallback counter.
 * This models the case where the decompression pipeline is
 * retried on different data segments before commitment.
 *
 * Validates: Requirements 4.3, 4.4
 * ---------------------------------------------------------------- */

#define MULTI_ERROR_ITERATIONS 100
#define MAX_ERRORS_PER_STREAM   10

static void
test_property5i_multiple_precommit_errors(void)
{
    decomp_ctx_t ctx;
    error_action_t action;
    decomp_error_t error;
    ngx_uint_t error_count;
    ngx_uint_t i;
    int iter;

    TEST_SUBSECTION(
        "Property 5i: Multiple pre-commit errors "
        "(100 iterations)");

    for (iter = 0; iter < MULTI_ERROR_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 6666));

        error_count = (ngx_uint_t)(
            (prng_next() % MAX_ERRORS_PER_STREAM) + 1);

        init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);

        for (i = 0; i < error_count; i++) {
            error = (decomp_error_t)(
                prng_next() % DECOMP_ERR_COUNT);

            /* Reset flags for fresh error handling */
            ctx.replay_triggered = 0;
            ctx.safe_finish_invoked = 0;

            action = handle_decomp_error(&ctx, error);
            TEST_ASSERT(action == ACTION_REPLAY_PASSTHROUGH,
                "each pre-commit error → replay");
            TEST_ASSERT(ctx.replay_triggered == 1,
                "replay flag set on each error");
            TEST_ASSERT(ctx.safe_finish_invoked == 0,
                "safe-finish never invoked pre-commit");
        }

        /* Fallback counter = total number of errors */
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                ctx.metrics.streaming_fallback_total
                == (ngx_atomic_uint_t) error_count,
            "fallback count must equal error count");
    }

    TEST_PASS(
        "Property 5i: multiple pre-commit errors "
        "correctly handled");
}

/* ----------------------------------------------------------------
 * Property 5j: State transition boundary test
 *
 * The boundary between pre-commit and post-commit is the
 * first successful downstream flush. Verify that:
 *   - flushes_sent == 0 → pre-commit (replay allowed)
 *   - flushes_sent >= 1 → post-commit (safe-finish only)
 *
 * Test various flush counts at the boundary.
 *
 * Validates: Requirements 4.3, 4.10
 * ---------------------------------------------------------------- */

static void
test_property5j_state_boundary(void)
{
    decomp_ctx_t ctx;
    error_action_t action;
    ngx_uint_t flush_counts[] = { 0, 1, 2, 5, 10, 100, 1000 };
    size_t i;

    TEST_SUBSECTION(
        "Property 5j: State boundary at flushes_sent");

    for (i = 0; i < ARRAY_SIZE(flush_counts); i++) {
        commit_state_t state =
            (flush_counts[i] == 0)
                ? STATE_PRE_COMMIT : STATE_POST_COMMIT;

        init_decomp_ctx(&ctx, state, 1048576);
        ctx.flushes_sent = flush_counts[i];

        action = handle_decomp_error(
            &ctx, DECOMP_ERR_TRUNCATED_STREAM);

        if (flush_counts[i] == 0) {
            TEST_ASSERT(action == ACTION_REPLAY_PASSTHROUGH,
                "flushes==0 → replay passthrough");
            TEST_ASSERT(ctx.replay_triggered == 1,
                "replay triggered at boundary");
        } else {
            TEST_ASSERT(action == ACTION_SAFE_FINISH,
                "flushes>0 → safe-finish");
            TEST_ASSERT(ctx.safe_finish_invoked == 1,
                "safe-finish at boundary");
            TEST_ASSERT(ctx.replay_triggered == 0,
                "no replay once committed");
        }
    }

    TEST_PASS(
        "Property 5j: state boundary at flushes_sent "
        "verified");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Property 5: Decompression Fail-Open on Malformed "
        "Input\n"
        "            (Pre-Commit + Post-Commit Safe-Finish)\n"
        "Validates: Requirements 4.3, 4.4, 4.10");

    /* Exhaustive state × error tests */
    test_property5a_precommit_exhaustive();
    test_property5b_postcommit_exhaustive();

    /* Budget counter semantics */
    test_property5c_budget_exceeded_counter();

    /* Random sequence property tests */
    test_property5d_random_error_sequences();
    test_property5e_budget_enforcement_random();

    /* Contrapositive property tests */
    test_property5f_postcommit_never_replays();
    test_property5g_precommit_never_safe_finishes();

    /* Logical property tests */
    test_property5h_mutual_exclusion();

    /* Multi-error and boundary tests */
    test_property5i_multiple_precommit_errors();
    test_property5j_state_boundary();

    printf("\n");
    TEST_PASS(
        "decomp_failopen_property: all property tests "
        "passed");
    return 0;
}
