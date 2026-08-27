/*
 * Test: delivery_decision_failopen
 *
 * Validates that delivery_total != decision_total counters in fail-open
 * scenarios.  In fail-open mode, a decision is made (decision_count++)
 * but delivery may not succeed (delivery_count only increments on
 * downstream NGX_OK).
 *
 * State machine:
 *   REQUEST -> DECISION (decision_count++) -> [fail-open path]
 *     -> downstream NGX_OK  -> delivery_count++
 *     -> downstream NGX_AGAIN -> delivery_count unchanged (pending)
 *     -> downstream NGX_ERROR -> delivery_count unchanged (failed)
 *
 * Corresponds to the fail-open delivery decision task.
 *
 * Rules: 23 (delivery != decision counters), 38 (delivery after
 * downstream OK), 8 (delivery counters after success).
 */

#include "../include/test_common.h"


enum {
    NGX_OK    =  0,
    NGX_ERROR = -1,
    NGX_AGAIN = -2,
    NGX_DONE  = -4
};

typedef enum {
    MODE_NORMAL = 0,
    MODE_FAILOPEN
} filter_mode_t;

typedef struct {
    filter_mode_t   mode;
    unsigned int    decision_count;
    unsigned int    delivery_count;
    unsigned int    failopen_count;
    unsigned int    failopen_completed;
    unsigned int    pending;
} failopen_ctx_t;


/*
 * Simulate a fail-open decision.  The module decides to pass through
 * the original response (fail-open) because conversion failed.
 *
 * decision_count always increments.
 * failopen_count increments when entering fail-open mode.
 * delivery_count only increments when downstream accepts (NGX_OK).
 */
static int
failopen_deliver(failopen_ctx_t *ctx, int downstream_rc)
{
    /* A decision was made regardless of outcome */
    ctx->decision_count++;

    if (ctx->mode == MODE_NORMAL) {
        ctx->mode = MODE_FAILOPEN;
        ctx->failopen_count++;
    }

    if (downstream_rc == NGX_OK) {
        ctx->delivery_count++;
        ctx->pending = 0;
        if (!ctx->failopen_completed) {
            ctx->failopen_completed = 1;
        }
        return NGX_OK;
    }

    if (downstream_rc == NGX_AGAIN) {
        ctx->pending = 1;
        return NGX_AGAIN;
    }

    /* NGX_ERROR */
    return NGX_ERROR;
}


/* ── Test: fail-open with immediate success ───────────────────── */

static void
test_failopen_immediate_success(void)
{
    failopen_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Fail-open with immediate downstream success");

    memset(&ctx, 0, sizeof(ctx));

    rc = failopen_deliver(&ctx, NGX_OK);
    TEST_ASSERT(rc == NGX_OK, "downstream accepts");
    TEST_ASSERT(ctx.decision_count == 1, "decision_count is 1");
    TEST_ASSERT(ctx.delivery_count == 1, "delivery_count is 1");
    TEST_ASSERT(ctx.failopen_count == 1, "failopen_count is 1");
    TEST_ASSERT(ctx.failopen_completed == 1, "failopen_completed set");

    /* delivery == decision in this simple case */
    TEST_ASSERT(ctx.delivery_count == ctx.decision_count,
        "delivery equals decision for immediate success");

    TEST_PASS("fail-open immediate success");
}


/* ── Test: fail-open with NGX_AGAIN (delivery != decision) ────── */

static void
test_failopen_again_diverges_counters(void)
{
    failopen_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Fail-open NGX_AGAIN diverges delivery from decision");

    memset(&ctx, 0, sizeof(ctx));

    /* First attempt: downstream suspends */
    rc = failopen_deliver(&ctx, NGX_AGAIN);
    TEST_ASSERT(rc == NGX_AGAIN, "downstream suspends");
    TEST_ASSERT(ctx.decision_count == 1, "decision_count is 1");
    TEST_ASSERT(ctx.delivery_count == 0,
        "delivery_count is 0 (not delivered yet)");
    TEST_ASSERT(ctx.pending == 1, "pending flag set");

    /* Key assertion: delivery != decision */
    TEST_ASSERT(ctx.delivery_count != ctx.decision_count,
        "delivery != decision after NGX_AGAIN");

    /* Resume: downstream accepts */
    rc = failopen_deliver(&ctx, NGX_OK);
    TEST_ASSERT(rc == NGX_OK, "resume succeeds");
    TEST_ASSERT(ctx.decision_count == 2, "decision_count is 2");
    TEST_ASSERT(ctx.delivery_count == 1, "delivery_count is 1");
    TEST_ASSERT(ctx.pending == 0, "pending cleared");

    /* Still diverged: 2 decisions, 1 delivery */
    TEST_ASSERT(ctx.decision_count - ctx.delivery_count == 1,
        "decision - delivery == 1 (one pending was resolved)");

    TEST_PASS("fail-open NGX_AGAIN diverges counters");
}


/* ── Test: fail-open with NGX_ERROR (delivery never increments) ─ */

static void
test_failopen_error_no_delivery(void)
{
    failopen_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Fail-open NGX_ERROR: delivery never increments");

    memset(&ctx, 0, sizeof(ctx));

    rc = failopen_deliver(&ctx, NGX_ERROR);
    TEST_ASSERT(rc == NGX_ERROR, "downstream errors");
    TEST_ASSERT(ctx.decision_count == 1, "decision_count is 1");
    TEST_ASSERT(ctx.delivery_count == 0,
        "delivery_count is 0 (error, not delivered)");
    TEST_ASSERT(ctx.failopen_count == 1, "failopen_count is 1");
    TEST_ASSERT(ctx.failopen_completed == 0,
        "failopen_completed not set (delivery failed)");

    TEST_PASS("fail-open error: no delivery increment");
}


/* ── Test: multiple fail-open attempts accumulate divergence ───── */

static void
test_multiple_failopen_divergence(void)
{
    failopen_ctx_t ctx;

    TEST_SUBSECTION("Multiple fail-open attempts accumulate divergence");

    memset(&ctx, 0, sizeof(ctx));

    /* Three decisions, only one delivery */
    failopen_deliver(&ctx, NGX_AGAIN);   /* decision 1, no delivery */
    failopen_deliver(&ctx, NGX_ERROR);   /* decision 2, no delivery */
    failopen_deliver(&ctx, NGX_OK);      /* decision 3, delivery 1 */

    TEST_ASSERT(ctx.decision_count == 3, "3 decisions made");
    TEST_ASSERT(ctx.delivery_count == 1, "only 1 delivery");
    TEST_ASSERT(ctx.decision_count - ctx.delivery_count == 2,
        "divergence is 2");

    TEST_PASS("multiple fail-open attempts accumulate divergence");
}


/* ── Test: failopen_completed prevents duplicate finalization ──── */

static void
test_failopen_completed_idempotent(void)
{
    failopen_ctx_t ctx;

    TEST_SUBSECTION("failopen_completed is idempotent");

    memset(&ctx, 0, sizeof(ctx));

    failopen_deliver(&ctx, NGX_OK);
    TEST_ASSERT(ctx.failopen_completed == 1, "completed after first OK");

    failopen_deliver(&ctx, NGX_OK);
    TEST_ASSERT(ctx.failopen_completed == 1,
        "completed remains 1 (not incremented to 2)");
    TEST_ASSERT(ctx.delivery_count == 2, "delivery_count is 2");

    TEST_PASS("failopen_completed is idempotent");
}


/* ── Test: buffered fail-open NGX_AGAIN defers counting to recovery ──
 *
 * Mirrors the Rule 38 delivery-latch contract added for the buffered
 * (full-buffer) fail-open paths: when the initial send hits downstream
 * backpressure (NGX_AGAIN), failopen_count is NOT incremented at the
 * decision point; the recovery pass-through increments it only after
 * the downstream filter confirms delivery.  This is the buffered
 * counterpart of the streaming pending_failopen_delivery latch.
 */

typedef struct {
    unsigned int  failopen_count;
    unsigned int  latch;
    unsigned int  completed;
} buffered_failopen_ctx_t;

static void
buffered_failopen_send(buffered_failopen_ctx_t *ctx, int downstream_rc)
{
    if (downstream_rc == NGX_AGAIN) {
        ctx->latch = 1;
        return;
    }
    if ((downstream_rc == NGX_OK || downstream_rc == NGX_DONE)
        && ctx->latch && !ctx->completed)
    {
        ctx->failopen_count++;
        ctx->completed = 1;
        ctx->latch = 0;
    }
}

static void
test_buffered_failopen_again_defers_count(void)
{
    buffered_failopen_ctx_t ctx;

    TEST_SUBSECTION("Buffered fail-open NGX_AGAIN defers failopen_count");

    memset(&ctx, 0, sizeof(ctx));

    /* Initial send hits backpressure: latch set, counter untouched. */
    buffered_failopen_send(&ctx, NGX_AGAIN);
    TEST_ASSERT(ctx.latch == 1, "latch set on NGX_AGAIN");
    TEST_ASSERT(ctx.failopen_count == 0,
        "failopen_count NOT incremented on NGX_AGAIN (Rule 38)");

    /* Recovery pass-through confirms delivery: count published once. */
    buffered_failopen_send(&ctx, NGX_OK);
    TEST_ASSERT(ctx.failopen_count == 1,
        "failopen_count incremented after recovery delivery");
    TEST_ASSERT(ctx.completed == 1, "completed set");
    TEST_ASSERT(ctx.latch == 0, "latch cleared");

    /* Idempotent: later OK does not double count. */
    buffered_failopen_send(&ctx, NGX_OK);
    TEST_ASSERT(ctx.failopen_count == 1,
        "no double count on repeated recovery OK");

    TEST_PASS("buffered fail-open NGX_AGAIN defers count");
}

static void
test_buffered_failopen_error_never_counts(void)
{
    buffered_failopen_ctx_t ctx;

    TEST_SUBSECTION("Buffered fail-open error never counts");

    memset(&ctx, 0, sizeof(ctx));

    buffered_failopen_send(&ctx, NGX_AGAIN);
    buffered_failopen_send(&ctx, NGX_ERROR);
    TEST_ASSERT(ctx.failopen_count == 0,
        "failopen_count stays 0 when recovery errors");
    TEST_ASSERT(ctx.latch == 1,
        "latch stays set when recovery errors (no delivery)");

    TEST_PASS("buffered fail-open error never counts");
}


int
main(void)
{
    TEST_SECTION("delivery_decision_failopen");

    test_failopen_immediate_success();
    test_failopen_again_diverges_counters();
    test_failopen_error_no_delivery();
    test_multiple_failopen_divergence();
    test_failopen_completed_idempotent();
    test_buffered_failopen_again_defers_count();
    test_buffered_failopen_error_never_counts();

    TEST_PASS("delivery_decision_failopen: all tests passed");
    return 0;
}
