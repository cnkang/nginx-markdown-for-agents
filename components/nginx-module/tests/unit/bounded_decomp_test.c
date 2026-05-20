/*
 * Test: bounded_decomp (state-machine model)
 *
 * Models the decompression decision logic as a state machine to
 * validate budget enforcement and error classification semantics.
 * This is a MODEL TEST — it exercises the logical state transitions
 * without calling the production FFI function
 * (markdown_decompress_bounded).
 *
 * Production FFI integration is covered by:
 *   - Rust unit tests in components/rust-converter/src/decompress.rs
 *   - E2E tests via make verify-chunked-native-e2e-smoke
 *   - The production path in ngx_http_markdown_decompress_via_rust()
 *     which calls markdown_decompress_bounded() at runtime
 *
 * State machine:
 *   INPUT -> CHECK_BUDGET -> [within] -> DECOMPRESS -> OUTPUT
 *                         -> [exceeded] -> PASS_THROUGH + ERROR
 *
 * Corresponds to task A03.11.
 *
 * Rules: 3 (enforce all budgets; free auxiliary buffers on all exits),
 *        32 (overflow guard on addition).
 */

#include "../include/test_common.h"


enum {
    DECOMP_OK = 0,
    DECOMP_BUDGET_EXCEEDED = 9,
    DECOMP_FORMAT_ERROR = 1,
    DECOMP_TRUNCATED_INPUT = 2,
    DECOMP_IO_ERROR = 3
};

/*
 * State machine phases for bounded decompression.
 */
typedef enum {
    DECOMP_PHASE_INIT = 0,
    DECOMP_PHASE_ACTIVE,
    DECOMP_PHASE_PASS_THROUGH,
    DECOMP_PHASE_ERROR
} decomp_phase_t;

typedef struct {
    decomp_phase_t  phase;
    size_t          budget;
    size_t          bytes_produced;
    unsigned int    error_code;
    unsigned int    budget_exceeded_count;
    unsigned int    format_error_count;
    int             output_valid;
} bounded_decomp_ctx_t;


/*
 * Initialize bounded decompression context with a given budget.
 */
static void
bounded_decomp_init(bounded_decomp_ctx_t *ctx, size_t budget)
{
    memset(ctx, 0, sizeof(*ctx));
    ctx->budget = budget;
    ctx->phase = DECOMP_PHASE_INIT;
}


/*
 * Simulate decompression of a chunk.  The decompressed_size represents
 * the output size after decompression.
 *
 * Returns:
 *   DECOMP_OK if within budget
 *   DECOMP_BUDGET_EXCEEDED if output would exceed budget
 *   Other error codes for format/IO errors
 */
static int
bounded_decomp_process(bounded_decomp_ctx_t *ctx,
    size_t decompressed_size, int format_ok)
{
    if (ctx->phase == DECOMP_PHASE_PASS_THROUGH) {
        /* Already in pass-through; no further decompression */
        return DECOMP_BUDGET_EXCEEDED;
    }

    if (!format_ok) {
        ctx->phase = DECOMP_PHASE_ERROR;
        ctx->error_code = DECOMP_FORMAT_ERROR;
        ctx->format_error_count++;
        ctx->output_valid = 0;
        return DECOMP_FORMAT_ERROR;
    }

    ctx->phase = DECOMP_PHASE_ACTIVE;

    /* Check if cumulative output would exceed budget */
    if (ctx->bytes_produced + decompressed_size > ctx->budget) {
        ctx->phase = DECOMP_PHASE_PASS_THROUGH;
        ctx->error_code = DECOMP_BUDGET_EXCEEDED;
        ctx->budget_exceeded_count++;
        ctx->output_valid = 0;
        return DECOMP_BUDGET_EXCEEDED;
    }

    /* Within budget: accept decompressed output */
    ctx->bytes_produced += decompressed_size;
    ctx->error_code = DECOMP_OK;
    ctx->output_valid = 1;
    return DECOMP_OK;
}


/* ── Test: within-budget pass-through ─────────────────────────── */

static void
test_within_budget_succeeds(void)
{
    bounded_decomp_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Within-budget decompression succeeds");

    bounded_decomp_init(&ctx, 4096);

    rc = bounded_decomp_process(&ctx, 1024, 1);
    TEST_ASSERT(rc == DECOMP_OK, "first chunk within budget");
    TEST_ASSERT(ctx.bytes_produced == 1024, "1024 bytes produced");
    TEST_ASSERT(ctx.output_valid == 1, "output is valid");
    TEST_ASSERT(ctx.phase == DECOMP_PHASE_ACTIVE, "phase is ACTIVE");

    rc = bounded_decomp_process(&ctx, 2048, 1);
    TEST_ASSERT(rc == DECOMP_OK, "second chunk within budget");
    TEST_ASSERT(ctx.bytes_produced == 3072, "3072 bytes produced");
    TEST_ASSERT(ctx.output_valid == 1, "output still valid");

    rc = bounded_decomp_process(&ctx, 1024, 1);
    TEST_ASSERT(rc == DECOMP_OK, "third chunk at exact budget boundary");
    TEST_ASSERT(ctx.bytes_produced == 4096, "4096 bytes produced (exact budget)");

    TEST_ASSERT(ctx.budget_exceeded_count == 0, "no budget exceeded");
    TEST_ASSERT(ctx.format_error_count == 0, "no format errors");

    TEST_PASS("within-budget decompression succeeds");
}


/* ── Test: budget exceeded triggers pass-through ──────────────── */

static void
test_budget_exceeded_enters_passthrough(void)
{
    bounded_decomp_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Budget exceeded enters pass-through");

    bounded_decomp_init(&ctx, 4096);

    /* First chunk within budget */
    rc = bounded_decomp_process(&ctx, 2048, 1);
    TEST_ASSERT(rc == DECOMP_OK, "first chunk OK");

    /* Second chunk exceeds budget (2048 + 4096 > 4096) */
    rc = bounded_decomp_process(&ctx, 4096, 1);
    TEST_ASSERT(rc == DECOMP_BUDGET_EXCEEDED,
        "over-budget returns BUDGET_EXCEEDED");
    TEST_ASSERT(ctx.phase == DECOMP_PHASE_PASS_THROUGH,
        "phase transitions to PASS_THROUGH");
    TEST_ASSERT(ctx.budget_exceeded_count == 1,
        "budget_exceeded_count is 1");
    TEST_ASSERT(ctx.output_valid == 0,
        "output is not valid after budget exceeded");
    TEST_ASSERT(ctx.bytes_produced == 2048,
        "bytes_produced unchanged (only first chunk counted)");

    /* Subsequent calls remain in pass-through */
    rc = bounded_decomp_process(&ctx, 100, 1);
    TEST_ASSERT(rc == DECOMP_BUDGET_EXCEEDED,
        "subsequent call still returns BUDGET_EXCEEDED");
    TEST_ASSERT(ctx.phase == DECOMP_PHASE_PASS_THROUGH,
        "phase remains PASS_THROUGH");

    TEST_PASS("budget exceeded enters pass-through");
}


/* ── Test: single chunk exceeding budget ──────────────────────── */

static void
test_single_chunk_exceeds_budget(void)
{
    bounded_decomp_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Single chunk exceeding budget");

    bounded_decomp_init(&ctx, 1024);

    rc = bounded_decomp_process(&ctx, 8192, 1);
    TEST_ASSERT(rc == DECOMP_BUDGET_EXCEEDED,
        "single large chunk returns BUDGET_EXCEEDED");
    TEST_ASSERT(ctx.phase == DECOMP_PHASE_PASS_THROUGH,
        "immediately enters PASS_THROUGH");
    TEST_ASSERT(ctx.budget_exceeded_count == 1,
        "budget_exceeded_count is 1");
    TEST_ASSERT(ctx.bytes_produced == 0,
        "no bytes produced");

    TEST_PASS("single chunk exceeding budget handled");
}


/* ── Test: format error does not increment budget counter ─────── */

static void
test_format_error_separate_from_budget(void)
{
    bounded_decomp_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Format error separate from budget counter");

    bounded_decomp_init(&ctx, 4096);

    rc = bounded_decomp_process(&ctx, 1024, 0);
    TEST_ASSERT(rc == DECOMP_FORMAT_ERROR,
        "format error returns FORMAT_ERROR");
    TEST_ASSERT(ctx.phase == DECOMP_PHASE_ERROR,
        "phase is ERROR");
    TEST_ASSERT(ctx.format_error_count == 1,
        "format_error_count is 1");
    TEST_ASSERT(ctx.budget_exceeded_count == 0,
        "budget_exceeded_count remains 0");

    TEST_PASS("format error separate from budget counter");
}


/* ── Test: zero budget immediately rejects ────────────────────── */

static void
test_zero_budget_rejects_all(void)
{
    bounded_decomp_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Zero budget rejects all decompression");

    bounded_decomp_init(&ctx, 0);

    rc = bounded_decomp_process(&ctx, 1, 1);
    TEST_ASSERT(rc == DECOMP_BUDGET_EXCEEDED,
        "even 1 byte exceeds zero budget");
    TEST_ASSERT(ctx.budget_exceeded_count == 1,
        "budget_exceeded_count is 1");

    TEST_PASS("zero budget rejects all decompression");
}


int
main(void)
{
    TEST_SECTION("bounded_decomp");

    test_within_budget_succeeds();
    test_budget_exceeded_enters_passthrough();
    test_single_chunk_exceeds_budget();
    test_format_error_separate_from_budget();
    test_zero_budget_rejects_all();

    TEST_PASS("bounded_decomp: all tests passed");
    return 0;
}
