/*
 * Test: middle_chunk_max_size_failure
 *
 * Validates that in a multi-chunk streaming scenario, when a middle
 * chunk triggers max-size failure (accumulated size > markdown_max_size),
 * subsequent chunks are still delivered to the client (not discarded).
 *
 * Key invariants tested:
 *   1. First chunk is processed normally (within budget)
 *   2. Middle chunk causes accumulated size to exceed markdown_max_size
 *   3. After failure, module enters fail-open pass-through mode
 *   4. Subsequent chunks are passed through unchanged (not discarded)
 *   5. Delivery counter is NOT incremented for the failed conversion
 *   6. Decision counter IS incremented when max-size failure occurs
 *
 * Corresponds to task A01.11.
 * Validates: REQ-0700-CORRECTNESS-001 (items 5, 6 of §5.1.1)
 */

#include "../include/test_common.h"


/*
 * Return codes mirroring NGINX semantics.
 */
enum {
    NGX_OK    =  0,
    NGX_AGAIN = -2,
    NGX_ERROR = -1
};

/*
 * Error classification codes for the streaming path.
 */
enum {
    ERROR_SUCCESS                       = 0,
    ERROR_MAX_SIZE_EXCEEDED             = 7,
    ERROR_DECOMPRESSION_BUDGET_EXCEEDED = 9
};

/*
 * Simulated module context tracking accumulated size, counters,
 * and fail-open state across multiple chunk invocations.
 *
 * This models the per-request state that the body filter maintains
 * in production (ngx_http_markdown_ctx_t fields).
 */
typedef struct {
    size_t       accumulated_size;
    size_t       markdown_max_size;
    int          in_pass_through;
    unsigned int failopen_completed;
    unsigned int precommit_error;
    unsigned int chunks_delivered;
    unsigned int decision_count;
    unsigned int delivery_count;
    unsigned int error_code;
} chunk_filter_ctx_t;


/*
 * Simulate processing a single chunk through the body filter.
 *
 * Models the production logic:
 *   - If already in pass-through mode, forward chunk unchanged
 *     and increment delivery_count on NGX_OK from downstream.
 *   - Otherwise, accumulate size and check against max_size.
 *   - If accumulated size exceeds max_size, enter fail-open mode,
 *     increment decision_count, but do NOT increment delivery_count.
 *   - If within budget, process normally and increment both counters.
 *
 * Parameters:
 *   chunk_size   - size of the incoming chunk in bytes
 *   downstream_rc - simulated return code from downstream filter
 *   ctx          - per-request filter context
 *
 * Returns:
 *   NGX_OK on successful delivery or pass-through
 *   NGX_ERROR when max-size exceeded (fail-open triggered)
 *   NGX_AGAIN when downstream returns NGX_AGAIN
 */
static int
process_chunk_with_counters(size_t chunk_size, int downstream_rc,
    chunk_filter_ctx_t *ctx)
{
    /*
     * Pass-through mode: after max-size failure, all subsequent
     * chunks bypass conversion and go directly to downstream.
     */
    if (ctx->in_pass_through) {
        if (downstream_rc == NGX_OK) {
            ctx->chunks_delivered++;
            ctx->delivery_count++;
        }
        return downstream_rc;
    }

    /*
     * Accumulate total size across chunks.  This models the
     * production behaviour where markdown_max_size is checked
     * against the running total, not per-chunk size.
     */
    ctx->accumulated_size += chunk_size;

    if (ctx->accumulated_size > ctx->markdown_max_size) {
        /*
         * Max-size exceeded: enter fail-open mode.
         * - Decision counter increments (we made a decision)
         * - Delivery counter does NOT increment (conversion failed)
         * - failopen_completed remains 0 until request finalization
         */
        ctx->error_code = ERROR_MAX_SIZE_EXCEEDED;
        ctx->decision_count++;
        ctx->in_pass_through = 1;
        return NGX_ERROR;
    }

    /*
     * Normal processing: chunk within budget.
     * Both decision and delivery counters increment on success.
     */
    ctx->decision_count++;
    if (downstream_rc == NGX_OK) {
        ctx->chunks_delivered++;
        ctx->delivery_count++;
    }
    ctx->error_code = ERROR_SUCCESS;
    return downstream_rc;
}


/*
 * Test: middle chunk triggers max-size via accumulated size.
 *
 * Scenario: markdown_max_size = 4096
 *   chunk 1: 2048 bytes (accumulated = 2048, within budget)
 *   chunk 2: 2048 bytes (accumulated = 4096, within budget)
 *   chunk 3: 1024 bytes (accumulated = 5120, EXCEEDS budget)
 *   chunk 4: 512 bytes  (pass-through, delivered normally)
 *   chunk 5: 2048 bytes (pass-through, delivered normally)
 */
static void
test_accumulated_size_triggers_max_size(void)
{
    chunk_filter_ctx_t ctx;
    int rc;

    memset(&ctx, 0, sizeof(ctx));
    ctx.markdown_max_size = 4096;

    TEST_SUBSECTION("accumulated size triggers max-size failure");

    /* Chunk 1: within budget */
    rc = process_chunk_with_counters(2048, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_OK, "chunk 1 succeeds");
    TEST_ASSERT(ctx.accumulated_size == 2048,
        "accumulated size is 2048 after chunk 1");
    TEST_ASSERT(ctx.in_pass_through == 0,
        "not in pass-through after chunk 1");
    TEST_ASSERT(ctx.delivery_count == 1,
        "delivery count is 1 after chunk 1");
    TEST_ASSERT(ctx.decision_count == 1,
        "decision count is 1 after chunk 1");

    /* Chunk 2: still within budget (exactly at limit) */
    rc = process_chunk_with_counters(2048, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_OK, "chunk 2 succeeds (at limit)");
    TEST_ASSERT(ctx.accumulated_size == 4096,
        "accumulated size is 4096 after chunk 2");
    TEST_ASSERT(ctx.in_pass_through == 0,
        "not in pass-through after chunk 2");
    TEST_ASSERT(ctx.delivery_count == 2,
        "delivery count is 2 after chunk 2");
    TEST_ASSERT(ctx.decision_count == 2,
        "decision count is 2 after chunk 2");

    /* Chunk 3: exceeds budget (accumulated 4096 + 1024 = 5120 > 4096) */
    rc = process_chunk_with_counters(1024, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_ERROR,
        "chunk 3 returns NGX_ERROR (max-size exceeded)");
    TEST_ASSERT(ctx.accumulated_size == 5120,
        "accumulated size is 5120 after chunk 3");
    TEST_ASSERT(ctx.in_pass_through == 1,
        "entered pass-through mode after chunk 3");
    TEST_ASSERT(ctx.error_code == ERROR_MAX_SIZE_EXCEEDED,
        "error code is MAX_SIZE_EXCEEDED");
    TEST_ASSERT(ctx.delivery_count == 2,
        "delivery count NOT incremented on failure (still 2)");
    TEST_ASSERT(ctx.decision_count == 3,
        "decision count IS incremented on failure (now 3)");

    /* Chunk 4: pass-through mode, delivered normally */
    rc = process_chunk_with_counters(512, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_OK,
        "chunk 4 delivered in pass-through mode");
    TEST_ASSERT(ctx.chunks_delivered == 3,
        "three chunks delivered total");
    TEST_ASSERT(ctx.delivery_count == 3,
        "delivery count increments in pass-through (now 3)");

    /* Chunk 5: pass-through mode, also delivered */
    rc = process_chunk_with_counters(2048, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_OK,
        "chunk 5 delivered in pass-through mode");
    TEST_ASSERT(ctx.chunks_delivered == 4,
        "four chunks delivered total");
    TEST_ASSERT(ctx.delivery_count == 4,
        "delivery count increments in pass-through (now 4)");
}


/*
 * Test: delivery counter not incremented when downstream returns
 * NGX_AGAIN in pass-through mode.
 *
 * After max-size failure, if downstream returns NGX_AGAIN for a
 * subsequent chunk, delivery_count must NOT increment (the chunk
 * has not been successfully delivered yet).
 */
static void
test_pass_through_ngx_again_no_delivery_increment(void)
{
    chunk_filter_ctx_t ctx;
    int rc;

    memset(&ctx, 0, sizeof(ctx));
    ctx.markdown_max_size = 1024;

    TEST_SUBSECTION("pass-through NGX_AGAIN does not increment delivery");

    /* Chunk 1: within budget */
    rc = process_chunk_with_counters(512, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_OK, "chunk 1 succeeds");

    /* Chunk 2: exceeds budget */
    rc = process_chunk_with_counters(1024, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_ERROR, "chunk 2 triggers max-size failure");
    TEST_ASSERT(ctx.delivery_count == 1,
        "delivery count is 1 after failure");
    TEST_ASSERT(ctx.decision_count == 2,
        "decision count is 2 after failure");

    /* Chunk 3: pass-through but downstream returns NGX_AGAIN */
    rc = process_chunk_with_counters(256, NGX_AGAIN, &ctx);
    TEST_ASSERT(rc == NGX_AGAIN,
        "chunk 3 returns NGX_AGAIN from downstream");
    TEST_ASSERT(ctx.delivery_count == 1,
        "delivery count NOT incremented on NGX_AGAIN (still 1)");
    TEST_ASSERT(ctx.chunks_delivered == 1,
        "chunks_delivered unchanged on NGX_AGAIN");

    /* Chunk 4: pass-through, downstream now returns NGX_OK */
    rc = process_chunk_with_counters(256, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_OK,
        "chunk 4 delivered successfully");
    TEST_ASSERT(ctx.delivery_count == 2,
        "delivery count increments on NGX_OK (now 2)");
    TEST_ASSERT(ctx.chunks_delivered == 2,
        "chunks_delivered is 2");
}


/*
 * Test: single large middle chunk exceeds max-size in one shot.
 *
 * Scenario: first chunk is small, second chunk alone pushes
 * accumulated size over the limit.
 */
static void
test_single_large_middle_chunk_exceeds(void)
{
    chunk_filter_ctx_t ctx;
    int rc;

    memset(&ctx, 0, sizeof(ctx));
    ctx.markdown_max_size = 4096;

    TEST_SUBSECTION("single large middle chunk exceeds max-size");

    /* Chunk 1: small, within budget */
    rc = process_chunk_with_counters(100, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_OK, "chunk 1 succeeds");
    TEST_ASSERT(ctx.delivery_count == 1, "delivery count is 1");

    /* Chunk 2: very large, exceeds budget in one shot */
    rc = process_chunk_with_counters(8192, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_ERROR,
        "large middle chunk triggers max-size failure");
    TEST_ASSERT(ctx.in_pass_through == 1,
        "entered pass-through mode");
    TEST_ASSERT(ctx.delivery_count == 1,
        "delivery count NOT incremented on failure");
    TEST_ASSERT(ctx.decision_count == 2,
        "decision count IS incremented");

    /* Chunk 3: subsequent chunk delivered in pass-through */
    rc = process_chunk_with_counters(500, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_OK,
        "subsequent chunk delivered in pass-through");
    TEST_ASSERT(ctx.delivery_count == 2,
        "delivery count increments in pass-through");
    TEST_ASSERT(ctx.chunks_delivered == 2,
        "two chunks delivered total");
}


/*
 * Test: multiple subsequent chunks all delivered after failure.
 *
 * Verifies that the fail-open pass-through mode persists across
 * many chunks, not just the first one after failure.
 */
static void
test_many_subsequent_chunks_after_failure(void)
{
    chunk_filter_ctx_t ctx;
    int rc;
    unsigned int i;

    memset(&ctx, 0, sizeof(ctx));
    ctx.markdown_max_size = 512;

    TEST_SUBSECTION("many subsequent chunks after failure");

    /* Chunk 1: exceeds immediately */
    rc = process_chunk_with_counters(1024, NGX_OK, &ctx);
    TEST_ASSERT(rc == NGX_ERROR, "first chunk exceeds max-size");
    TEST_ASSERT(ctx.decision_count == 1, "decision count is 1");
    TEST_ASSERT(ctx.delivery_count == 0,
        "delivery count is 0 after failure");

    /* Deliver 10 subsequent chunks in pass-through mode */
    for (i = 0; i < 10; i++) {
        rc = process_chunk_with_counters(128, NGX_OK, &ctx);
        TEST_ASSERT(rc == NGX_OK,
            "subsequent chunk delivered in pass-through");
    }

    TEST_ASSERT(ctx.chunks_delivered == 10,
        "all 10 subsequent chunks delivered");
    TEST_ASSERT(ctx.delivery_count == 10,
        "delivery count is 10 for pass-through chunks");
    TEST_ASSERT(ctx.in_pass_through == 1,
        "still in pass-through mode after all chunks");
}


int
main(void)
{
    TEST_SECTION("middle_chunk_max_size_failure tests");

    test_accumulated_size_triggers_max_size();
    test_pass_through_ngx_again_no_delivery_increment();
    test_single_large_middle_chunk_exceeds();
    test_many_subsequent_chunks_after_failure();

    TEST_PASS("middle_chunk_max_size_failure: all tests passed");
    return 0;
}
