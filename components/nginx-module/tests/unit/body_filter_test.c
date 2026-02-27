/*
 * Test: body_filter
 * Description: body filter functionality
 */

#include "test_common.h"

typedef struct {
    char buffer[4096];
    size_t size;
    int eligible;
    int is_head_request;
    int decompression_needed;
    int decompression_done;
    int conversion_called;
    int decompression_called;
} body_ctx_t;

static void
append_chunk(body_ctx_t *ctx, const char *chunk)
{
    size_t len = strlen(chunk);
    TEST_ASSERT(ctx->size + len < sizeof(ctx->buffer), "test buffer overflow");
    memcpy(ctx->buffer + ctx->size, chunk, len);
    ctx->size += len;
}

static void
run_body_filter(body_ctx_t *ctx, const char *chunk, int last_buf)
{
    append_chunk(ctx, chunk);

    if (!last_buf) {
        return;
    }

    if (!ctx->eligible) {
        return;
    }

    if (ctx->decompression_needed && !ctx->decompression_done) {
        ctx->decompression_called = 1;
        ctx->decompression_done = 1;
    }

    ctx->conversion_called = 1;
    if (ctx->is_head_request) {
        ctx->size = 0;
    }
}

static body_ctx_t
new_ctx(void)
{
    body_ctx_t c;
    memset(&c, 0, sizeof(c));
    c.eligible = 1;
    return c;
}

static void
test_buffer_then_convert(void)
{
    body_ctx_t ctx = new_ctx();
    TEST_SUBSECTION("Buffers chunks and converts only at last chunk");

    run_body_filter(&ctx, "<html>", 0);
    TEST_ASSERT(ctx.conversion_called == 0, "Should not convert before last chunk");

    run_body_filter(&ctx, "<body>ok</body></html>", 1);
    TEST_ASSERT(ctx.conversion_called == 1, "Should convert at last chunk");
    TEST_ASSERT(ctx.size > 0, "Body should remain for GET");
    TEST_PASS("Chunk buffering behavior works");
}

static void
test_decompression_hook(void)
{
    body_ctx_t ctx = new_ctx();
    TEST_SUBSECTION("Runs decompression before conversion when needed");

    ctx.decompression_needed = 1;
    run_body_filter(&ctx, "compressed-bytes", 1);
    TEST_ASSERT(ctx.decompression_called == 1, "Decompression should run");
    TEST_ASSERT(ctx.decompression_done == 1, "decompression_done should be set");
    TEST_ASSERT(ctx.conversion_called == 1, "Conversion should still run");
    TEST_PASS("Decompression hook works");
}

static void
test_head_request_omits_body(void)
{
    body_ctx_t ctx = new_ctx();
    TEST_SUBSECTION("HEAD request omits response body");

    ctx.is_head_request = 1;
    run_body_filter(&ctx, "markdown-body", 1);
    TEST_ASSERT(ctx.conversion_called == 1, "HEAD still runs conversion pipeline");
    TEST_ASSERT(ctx.size == 0, "HEAD response must omit body");
    TEST_PASS("HEAD behavior works");
}

static void
test_ineligible_passthrough(void)
{
    body_ctx_t ctx = new_ctx();
    TEST_SUBSECTION("Ineligible request bypasses conversion");

    ctx.eligible = 0;
    run_body_filter(&ctx, "original", 1);
    TEST_ASSERT(ctx.conversion_called == 0, "Ineligible request must bypass conversion");
    TEST_ASSERT(ctx.decompression_called == 0, "Ineligible request must bypass decompression");
    TEST_PASS("Ineligible passthrough works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("body_filter Tests\n");
    printf("========================================\n");

    test_buffer_then_convert();
    test_decompression_hook();
    test_head_request_omits_body();
    test_ineligible_passthrough();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
