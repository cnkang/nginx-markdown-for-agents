/*
 * Test: fast_path
 *
 * Validates the decompression state initialization logic: when
 * auto_decompress is enabled and a compressed encoding is detected,
 * the context flags decompression_needed; otherwise all flags remain
 * cleared for the fast (non-decompression) path.
 */

#include "test_common.h"

/*
 * Compression type enumeration.
 */
typedef enum {
    COMP_NONE = 0,
    COMP_GZIP = 1
} compression_t;

/*
 * Decompression state context.
 */
typedef struct {
    compression_t compression_type;  /* Detected compression type. */
    int decompression_needed;        /* 1 when decompression is required. */
    int decompression_done;          /* 1 after decompression has run. */
} ctx_t;

/*
 * Initialize decompression state based on auto_decompress setting
 * and detected compression type.
 *
 * Parameters:
 *   ctx            - context to initialize
 *   auto_decompress - whether auto-decompress is enabled
 *   detected       - detected compression type
 */
static void
init_decompression_state(ctx_t *ctx, int auto_decompress, compression_t detected)
{
    ctx->compression_type = COMP_NONE;
    ctx->decompression_needed = 0;
    ctx->decompression_done = 0;

    if (auto_decompress) {
        ctx->compression_type = detected;
        if (detected != COMP_NONE) {
            ctx->decompression_needed = 1;
        }
    }
}

/*
 * Check whether decompression should run based on needed and done flags.
 *
 * DIVERGENCE RISK: this helper mirrors the production decompression
 * gating decision. Keep it synchronized with runtime logic changes.
 *
 * Parameters:
 *   ctx - decompression state context
 *
 * Returns:
 *   1 if decompression is needed but not yet done, 0 otherwise.
 */
static int
should_run_decompression(const ctx_t *ctx)
{
    return ctx->decompression_needed && !ctx->decompression_done;
}

/*
 * Verify uncompressed content takes the fast path: decompression_needed
 * is 0, should_run_decompression returns 0.
 *
 * Expected: no decompression needed for uncompressed content.
 */
static void
test_uncompressed_fast_path(void)
{
    ctx_t ctx;
    TEST_SUBSECTION("Uncompressed content fast path");

    init_decompression_state(&ctx, 1, COMP_NONE);
    TEST_ASSERT(ctx.decompression_needed == 0, "Uncompressed content must not set decompression_needed");
    TEST_ASSERT(should_run_decompression(&ctx) == 0, "Fast path must skip decompression");
    TEST_PASS("Uncompressed fast path works");
}

/*
 * Verify compressed content takes the slow path: decompression_needed
 * is 1, should_run_decompression returns 1.
 *
 * Expected: decompression required for compressed content.
 */
static void
test_compressed_slow_path(void)
{
    ctx_t ctx;
    TEST_SUBSECTION("Compressed content slow path");

    init_decompression_state(&ctx, 1, COMP_GZIP);
    TEST_ASSERT(ctx.decompression_needed == 1, "Compressed content must set decompression_needed");
    TEST_ASSERT(should_run_decompression(&ctx) == 1, "Slow path must run decompression");
    TEST_PASS("Compressed path works");
}

/*
 * Verify already-decompressed content skips decompression: even though
 * decompression_needed is 1, decompression_done=1 prevents re-running.
 *
 * Expected: should_run_decompression returns 0.
 */
static void
test_already_decompressed(void)
{
    ctx_t ctx;
    TEST_SUBSECTION("Already decompressed content");

    init_decompression_state(&ctx, 1, COMP_GZIP);
    ctx.decompression_done = 1;
    TEST_ASSERT(should_run_decompression(&ctx) == 0, "Must not decompress twice");
    TEST_PASS("Already decompressed case works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("fast_path Tests\n");
    printf("========================================\n");

    test_uncompressed_fast_path();
    test_compressed_slow_path();
    test_already_decompressed();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
