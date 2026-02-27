/*
 * Test: fast_path
 * Description: fast path optimization
 */

#include "test_common.h"

typedef enum {
    COMP_NONE = 0,
    COMP_GZIP = 1
} compression_t;

typedef struct {
    compression_t compression_type;
    int decompression_needed;
    int decompression_done;
} ctx_t;

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

static int
should_run_decompression(const ctx_t *ctx)
{
    return ctx->decompression_needed && !ctx->decompression_done;
}

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
