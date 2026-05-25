/*
 * Test: parse_timeout
 *
 * Validates that:
 *   A06.7 — timeout triggers PARSE_TIMEOUT reason code and metric increment
 *   A06.8 — budget exceeded triggers PARSE_BUDGET_EXCEEDED reason code
 *           and metric increment
 *
 * Models the parse interrupt decision logic as a state machine:
 *
 *   PARSING -> [timeout elapsed] -> PARSE_TIMEOUT + metric++
 *   PARSING -> [budget exceeded] -> PARSE_BUDGET_EXCEEDED + metric++
 *   PARSING -> [success] -> DONE
 *
 * Corresponds to tasks A06.7 and A06.8.
 *
 * Rules: 7 (explicit skip-reason mapping; reason-code tests aligned),
 *        23 (complete metric lifecycle).
 */

#include "../include/test_common.h"


/* Error codes matching production definitions */
enum {
    ERROR_SUCCESS = 0,
    ERROR_PARSE_TIMEOUT = 10,
    ERROR_PARSE_BUDGET_EXCEEDED = 11
};

/* Reason codes matching production definitions */
enum {
    REASON_NONE = 0,
    REASON_PARSE_TIMEOUT = 10,
    REASON_PARSE_BUDGET_EXCEEDED = 11
};

/* Parse state machine phases */
typedef enum {
    PARSE_PHASE_IDLE = 0,
    PARSE_PHASE_ACTIVE,
    PARSE_PHASE_COMPLETED,
    PARSE_PHASE_TIMED_OUT,
    PARSE_PHASE_BUDGET_EXCEEDED
} parse_phase_t;

typedef struct {
    parse_phase_t   phase;
    unsigned int    error_code;
    unsigned int    reason_code;
    unsigned int    parse_timeouts_total;
    unsigned int    parse_budget_exceeded_total;
    size_t          bytes_parsed;
    size_t          budget;
    unsigned int    timeout_ms;
    unsigned int    elapsed_ms;
} parse_ctx_t;


/*
 * Initialize parse context with timeout and budget.
 */
static void
parse_ctx_init(parse_ctx_t *ctx, unsigned int timeout_ms, size_t budget)
{
    memset(ctx, 0, sizeof(*ctx));
    ctx->timeout_ms = timeout_ms;
    ctx->budget = budget;
    ctx->phase = PARSE_PHASE_IDLE;
}


/*
 * Simulate a parse step.  Checks timeout and budget conditions.
 *
 * Parameters:
 *   ctx          — parse context
 *   chunk_size   — bytes to parse in this step
 *   elapsed_ms   — simulated elapsed time after this step
 *
 * Returns:
 *   ERROR_SUCCESS if parse step completed within limits
 *   ERROR_PARSE_TIMEOUT if elapsed exceeds timeout
 *   ERROR_PARSE_BUDGET_EXCEEDED if bytes exceed budget
 */
static int
parse_step(parse_ctx_t *ctx, size_t chunk_size, unsigned int elapsed_ms)
{
    if (ctx->phase == PARSE_PHASE_TIMED_OUT ||
        ctx->phase == PARSE_PHASE_BUDGET_EXCEEDED) {
        /* Already terminated; no further parsing */
        return ctx->error_code;
    }

    ctx->phase = PARSE_PHASE_ACTIVE;
    ctx->elapsed_ms = elapsed_ms;

    /* Check timeout first (timeout takes priority) */
    if (ctx->elapsed_ms >= ctx->timeout_ms) {
        ctx->phase = PARSE_PHASE_TIMED_OUT;
        ctx->error_code = ERROR_PARSE_TIMEOUT;
        ctx->reason_code = REASON_PARSE_TIMEOUT;
        ctx->parse_timeouts_total++;
        return ERROR_PARSE_TIMEOUT;
    }

    /* Check budget */
    if (ctx->bytes_parsed + chunk_size > ctx->budget) {
        ctx->phase = PARSE_PHASE_BUDGET_EXCEEDED;
        ctx->error_code = ERROR_PARSE_BUDGET_EXCEEDED;
        ctx->reason_code = REASON_PARSE_BUDGET_EXCEEDED;
        ctx->parse_budget_exceeded_total++;
        return ERROR_PARSE_BUDGET_EXCEEDED;
    }

    /* Within limits: accept parse step */
    ctx->bytes_parsed += chunk_size;
    ctx->error_code = ERROR_SUCCESS;
    return ERROR_SUCCESS;
}


/*
 * Mark parse as completed (all input consumed within limits).
 */
static void
parse_complete(parse_ctx_t *ctx)
{
    if (ctx->phase == PARSE_PHASE_ACTIVE) {
        ctx->phase = PARSE_PHASE_COMPLETED;
    }
}


/* ── A06.7: Timeout triggers PARSE_TIMEOUT ────────────────────── */

static void
test_timeout_triggers_parse_timeout(void)
{
    parse_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Timeout triggers PARSE_TIMEOUT reason code and metric");

    parse_ctx_init(&ctx, 30000, 64 * 1024 * 1024);  /* 30s, 64MB */

    /* First step: within time */
    rc = parse_step(&ctx, 1024, 5000);
    TEST_ASSERT(rc == ERROR_SUCCESS, "first step within timeout");
    TEST_ASSERT(ctx.phase == PARSE_PHASE_ACTIVE, "phase is ACTIVE");

    /* Second step: exceeds timeout */
    rc = parse_step(&ctx, 1024, 31000);
    TEST_ASSERT(rc == ERROR_PARSE_TIMEOUT,
        "timeout returns ERROR_PARSE_TIMEOUT");
    TEST_ASSERT(ctx.error_code == ERROR_PARSE_TIMEOUT,
        "error_code is PARSE_TIMEOUT (10)");
    TEST_ASSERT(ctx.reason_code == REASON_PARSE_TIMEOUT,
        "reason_code is PARSE_TIMEOUT (10)");
    TEST_ASSERT(ctx.parse_timeouts_total == 1,
        "parse_timeouts_total incremented to 1");
    TEST_ASSERT(ctx.parse_budget_exceeded_total == 0,
        "parse_budget_exceeded_total remains 0");
    TEST_ASSERT(ctx.phase == PARSE_PHASE_TIMED_OUT,
        "phase is TIMED_OUT");

    TEST_PASS("timeout triggers PARSE_TIMEOUT correctly");
}


static void
test_timeout_at_exact_boundary(void)
{
    parse_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Timeout at exact boundary");

    parse_ctx_init(&ctx, 1000, 1024 * 1024);

    /* Elapsed exactly equals timeout */
    rc = parse_step(&ctx, 100, 1000);
    TEST_ASSERT(rc == ERROR_PARSE_TIMEOUT,
        "exact timeout boundary triggers PARSE_TIMEOUT");
    TEST_ASSERT(ctx.parse_timeouts_total == 1,
        "metric incremented at exact boundary");

    TEST_PASS("timeout at exact boundary handled");
}


static void
test_timeout_prevents_further_parsing(void)
{
    parse_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Timeout prevents further parsing");

    parse_ctx_init(&ctx, 100, 1024 * 1024);

    rc = parse_step(&ctx, 100, 200);
    TEST_ASSERT(rc == ERROR_PARSE_TIMEOUT, "timeout triggered");

    /* Subsequent calls return same error without incrementing metric */
    rc = parse_step(&ctx, 50, 50);
    TEST_ASSERT(rc == ERROR_PARSE_TIMEOUT,
        "subsequent call returns PARSE_TIMEOUT");
    TEST_ASSERT(ctx.parse_timeouts_total == 1,
        "metric not double-incremented");

    TEST_PASS("timeout prevents further parsing");
}


/* ── A06.8: Budget exceeded triggers PARSE_BUDGET_EXCEEDED ────── */

static void
test_budget_exceeded_triggers_parse_budget(void)
{
    parse_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Budget exceeded triggers PARSE_BUDGET_EXCEEDED");

    parse_ctx_init(&ctx, 30000, 4096);  /* 30s, 4KB budget */

    /* First step: within budget */
    rc = parse_step(&ctx, 2048, 100);
    TEST_ASSERT(rc == ERROR_SUCCESS, "first step within budget");
    TEST_ASSERT(ctx.bytes_parsed == 2048, "2048 bytes parsed");

    /* Second step: exceeds budget (2048 + 4096 > 4096) */
    rc = parse_step(&ctx, 4096, 200);
    TEST_ASSERT(rc == ERROR_PARSE_BUDGET_EXCEEDED,
        "over-budget returns ERROR_PARSE_BUDGET_EXCEEDED");
    TEST_ASSERT(ctx.error_code == ERROR_PARSE_BUDGET_EXCEEDED,
        "error_code is PARSE_BUDGET_EXCEEDED (11)");
    TEST_ASSERT(ctx.reason_code == REASON_PARSE_BUDGET_EXCEEDED,
        "reason_code is PARSE_BUDGET_EXCEEDED (11)");
    TEST_ASSERT(ctx.parse_budget_exceeded_total == 1,
        "parse_budget_exceeded_total incremented to 1");
    TEST_ASSERT(ctx.parse_timeouts_total == 0,
        "parse_timeouts_total remains 0");
    TEST_ASSERT(ctx.phase == PARSE_PHASE_BUDGET_EXCEEDED,
        "phase is BUDGET_EXCEEDED");

    TEST_PASS("budget exceeded triggers PARSE_BUDGET_EXCEEDED correctly");
}


static void
test_budget_exceeded_prevents_further_parsing(void)
{
    parse_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Budget exceeded prevents further parsing");

    parse_ctx_init(&ctx, 30000, 100);

    rc = parse_step(&ctx, 200, 10);
    TEST_ASSERT(rc == ERROR_PARSE_BUDGET_EXCEEDED, "budget exceeded");

    rc = parse_step(&ctx, 10, 20);
    TEST_ASSERT(rc == ERROR_PARSE_BUDGET_EXCEEDED,
        "subsequent call returns same error");
    TEST_ASSERT(ctx.parse_budget_exceeded_total == 1,
        "metric not double-incremented");

    TEST_PASS("budget exceeded prevents further parsing");
}


/* ── Test: counters are independent ───────────────────────────── */

static void
test_counters_independent(void)
{
    parse_ctx_t ctx1;
    parse_ctx_t ctx2;

    TEST_SUBSECTION("Timeout and budget counters are independent");

    /* Timeout scenario */
    parse_ctx_init(&ctx1, 100, 1024 * 1024);
    parse_step(&ctx1, 50, 200);
    TEST_ASSERT(ctx1.parse_timeouts_total == 1, "timeout counted");
    TEST_ASSERT(ctx1.parse_budget_exceeded_total == 0,
        "budget not counted in timeout scenario");

    /* Budget scenario */
    parse_ctx_init(&ctx2, 30000, 100);
    parse_step(&ctx2, 200, 10);
    TEST_ASSERT(ctx2.parse_budget_exceeded_total == 1, "budget counted");
    TEST_ASSERT(ctx2.parse_timeouts_total == 0,
        "timeout not counted in budget scenario");

    TEST_PASS("counters are independent");
}


/* ── Test: successful parse does not increment any error counter ─ */

static void
test_success_no_error_counters(void)
{
    parse_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Successful parse increments no error counters");

    parse_ctx_init(&ctx, 30000, 4096);

    rc = parse_step(&ctx, 1024, 100);
    TEST_ASSERT(rc == ERROR_SUCCESS, "step 1 OK");

    rc = parse_step(&ctx, 1024, 200);
    TEST_ASSERT(rc == ERROR_SUCCESS, "step 2 OK");

    parse_complete(&ctx);

    TEST_ASSERT(ctx.parse_timeouts_total == 0, "no timeouts");
    TEST_ASSERT(ctx.parse_budget_exceeded_total == 0, "no budget exceeded");
    TEST_ASSERT(ctx.phase == PARSE_PHASE_COMPLETED, "phase is COMPLETED");
    TEST_ASSERT(ctx.bytes_parsed == 2048, "2048 bytes parsed total");

    TEST_PASS("successful parse increments no error counters");
}


int
main(void)
{
    TEST_SECTION("parse_timeout");

    test_timeout_triggers_parse_timeout();
    test_timeout_at_exact_boundary();
    test_timeout_prevents_further_parsing();
    test_budget_exceeded_triggers_parse_budget();
    test_budget_exceeded_prevents_further_parsing();
    test_counters_independent();
    test_success_no_error_counters();

    TEST_PASS("parse_timeout: all tests passed");
    return 0;
}
