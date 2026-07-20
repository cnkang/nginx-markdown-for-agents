/*
 * Test: delivery_counter_semantics
 *
 * Validates that the delivery counter is incremented only after
 * NGX_OK from downstream, never on NGX_AGAIN or NGX_ERROR.
 * Also validates that the decision counter increments regardless
 * of downstream return code.
 *
 * Scope: this unit test covers NGX_OK, NGX_AGAIN, and NGX_ERROR only.
 * Production `ngx_http_markdown_streaming_delivery_ok()` also treats
 * NGX_DONE as a successful delivery; the NGX_DONE delivery path is
 * covered by the streaming body_filter test suite, not here.
 *
 * Corresponds to task A01.12.
 *
 * Rules: 38 (delivery after downstream OK), 8 (delivery counters
 * after success), 23 (delivery != decision counters).
 */

#include "../include/test_common.h"


enum {
    NGX_OK    =  0,
    NGX_ERROR = -1,
    NGX_AGAIN = -2
};

typedef struct {
    unsigned int delivery_count;
    unsigned int decision_count;
    unsigned int again_count;
    unsigned int error_count;
    unsigned int pending;
} delivery_ctx_t;


/*
 * Models the production behavior where:
 *   - decision_count always increments (decision was made)
 *   - delivery_count increments only on NGX_OK from downstream
 *   - pending flag tracks NGX_AGAIN state for resume
 */
static void
record_decision_and_delivery(int downstream_rc, delivery_ctx_t *ctx)
{
    ctx->decision_count++;

    if (downstream_rc == NGX_OK) {
        ctx->delivery_count++;
        ctx->pending = 0;
    } else if (downstream_rc == NGX_AGAIN) {
        ctx->again_count++;
        ctx->pending = 1;
    } else {
        ctx->error_count++;
    }
}


/*
 * Test 1: NGX_OK increments both delivery and decision counters.
 */
static void
test_ngx_ok_increments_delivery(void)
{
    delivery_ctx_t ctx;

    memset(&ctx, 0, sizeof(ctx));

    record_decision_and_delivery(NGX_OK, &ctx);
    TEST_ASSERT(ctx.delivery_count == 1,
        "NGX_OK increments delivery_count");
    TEST_ASSERT(ctx.decision_count == 1,
        "NGX_OK also increments decision_count");
    TEST_ASSERT(ctx.again_count == 0,
        "no NGX_AGAIN recorded");
    TEST_ASSERT(ctx.error_count == 0,
        "no errors recorded");

    TEST_PASS("NGX_OK increments delivery_count");
}


/*
 * Test 2: NGX_AGAIN does NOT increment delivery_count.
 */
static void
test_ngx_again_does_not_increment_delivery(void)
{
    delivery_ctx_t ctx;

    memset(&ctx, 0, sizeof(ctx));

    record_decision_and_delivery(NGX_AGAIN, &ctx);
    TEST_ASSERT(ctx.delivery_count == 0,
        "NGX_AGAIN does NOT increment delivery_count");
    TEST_ASSERT(ctx.decision_count == 1,
        "NGX_AGAIN still increments decision_count");
    TEST_ASSERT(ctx.again_count == 1,
        "NGX_AGAIN increments again_count");
    TEST_ASSERT(ctx.pending == 1,
        "NGX_AGAIN sets pending flag");

    record_decision_and_delivery(NGX_AGAIN, &ctx);
    TEST_ASSERT(ctx.delivery_count == 0,
        "second NGX_AGAIN still no delivery increment");
    TEST_ASSERT(ctx.decision_count == 2,
        "second NGX_AGAIN increments decision_count");
    TEST_ASSERT(ctx.again_count == 2,
        "second NGX_AGAIN increments again_count");

    TEST_PASS("NGX_AGAIN does NOT increment delivery_count");
}


/*
 * Test 3: NGX_ERROR does NOT increment delivery_count.
 */
static void
test_ngx_error_does_not_increment_delivery(void)
{
    delivery_ctx_t ctx;

    memset(&ctx, 0, sizeof(ctx));

    record_decision_and_delivery(NGX_ERROR, &ctx);
    TEST_ASSERT(ctx.delivery_count == 0,
        "NGX_ERROR does NOT increment delivery_count");
    TEST_ASSERT(ctx.decision_count == 1,
        "NGX_ERROR still increments decision_count");
    TEST_ASSERT(ctx.error_count == 1,
        "NGX_ERROR increments error_count");

    TEST_PASS("NGX_ERROR does NOT increment delivery_count");
}


/*
 * Test 4: Decision counter increments regardless of downstream rc.
 */
static void
test_decision_counter_always_increments(void)
{
    delivery_ctx_t ctx;

    memset(&ctx, 0, sizeof(ctx));

    record_decision_and_delivery(NGX_OK, &ctx);
    TEST_ASSERT(ctx.decision_count == 1,
        "decision_count is 1 after NGX_OK");

    record_decision_and_delivery(NGX_AGAIN, &ctx);
    TEST_ASSERT(ctx.decision_count == 2,
        "decision_count is 2 after NGX_AGAIN");

    record_decision_and_delivery(NGX_ERROR, &ctx);
    TEST_ASSERT(ctx.decision_count == 3,
        "decision_count is 3 after NGX_ERROR");

    record_decision_and_delivery(NGX_OK, &ctx);
    TEST_ASSERT(ctx.decision_count == 4,
        "decision_count is 4 after second NGX_OK");

    /* delivery_count only incremented for the two NGX_OK calls */
    TEST_ASSERT(ctx.delivery_count == 2,
        "delivery_count is 2 (only NGX_OK calls)");

    /* difference represents pending/failed deliveries */
    TEST_ASSERT(ctx.decision_count - ctx.delivery_count == 2,
        "decision - delivery == 2 (pending + error)");

    TEST_PASS("decision counter increments regardless of rc");
}


/*
 * Test 5: After NGX_AGAIN followed by successful resume (NGX_OK),
 * delivery_count increments.
 */
static void
test_again_then_ok_increments_delivery(void)
{
    delivery_ctx_t ctx;

    memset(&ctx, 0, sizeof(ctx));

    /* First call: downstream suspends */
    record_decision_and_delivery(NGX_AGAIN, &ctx);
    TEST_ASSERT(ctx.delivery_count == 0,
        "no delivery after NGX_AGAIN");
    TEST_ASSERT(ctx.decision_count == 1,
        "decision recorded for NGX_AGAIN");
    TEST_ASSERT(ctx.pending == 1,
        "pending flag set after NGX_AGAIN");

    /* Resume: downstream accepts */
    record_decision_and_delivery(NGX_OK, &ctx);
    TEST_ASSERT(ctx.delivery_count == 1,
        "delivery increments on resume NGX_OK");
    TEST_ASSERT(ctx.decision_count == 2,
        "decision increments on resume");
    TEST_ASSERT(ctx.pending == 0,
        "pending flag cleared after NGX_OK");

    TEST_PASS("NGX_AGAIN then NGX_OK increments delivery");
}


int
main(void)
{
    TEST_SECTION("delivery_counter_semantics");

    test_ngx_ok_increments_delivery();
    test_ngx_again_does_not_increment_delivery();
    test_ngx_error_does_not_increment_delivery();
    test_decision_counter_always_increments();
    test_again_then_ok_increments_delivery();

    TEST_PASS("delivery_counter_semantics: all tests passed");
    return 0;
}
