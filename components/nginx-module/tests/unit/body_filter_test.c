/*
 * Test: body_filter
 *
 * Validates the body filter's chunk buffering, decompression hook,
 * HEAD request handling, and ineligible-request passthrough behavior.
 *
 * This test reimplements the body filter decision logic in standard C
 * (no NGINX dependencies) to verify the ordering contract: chunks are
 * buffered until last_buf, decompression runs before conversion when
 * needed, HEAD requests omit the response body, and ineligible
 * requests bypass conversion entirely.
 *
 * DIVERGENCE RISK: this reimplementation may drift from production
 * body filter logic in ordering, edge-case handling, or new feature
 * gates.  Synchronize with production when body filter semantics
 * change.  Runtime E2E tests remain the source of truth.
 */

#include "test_common.h"

/*
 * Simulated body filter context tracking buffered data and state flags.
 *
 * Fields:
 *   buffer              - accumulated response body chunks
 *   size                - current buffer occupancy in bytes
 *   eligible            - whether the request qualifies for conversion
 *   is_head_request     - whether the original request was HEAD
 *   decompression_needed - whether the upstream response is compressed
 *   decompression_done  - whether decompression has already run
 *   conversion_called   - whether the conversion step was invoked
 *   decompression_called - whether the decompression step was invoked
 */
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

/*
 * Append a chunk to the body context buffer.
 *
 * Parameters:
 *   ctx   - body filter context to append to
 *   chunk - NUL-terminated string to append
 *
 * Aborts via TEST_ASSERT if the buffer would overflow.
 */
static void
append_chunk(body_ctx_t *ctx, const char *chunk)
{
    size_t remaining;
    size_t len;

    remaining = sizeof(ctx->buffer) - ctx->size;
    len = test_cstrnlen(chunk, remaining);
    TEST_ASSERT(ctx->size + len < sizeof(ctx->buffer), "test buffer overflow");
    memcpy(ctx->buffer + ctx->size, chunk, len);
    ctx->size += len;
}

/*
 * Simulate a single body filter invocation.
 *
 * Accumulates the chunk, then on last_buf: triggers decompression
 * if needed but not yet done, marks conversion as called, and for
 * HEAD requests clears the body buffer.
 *
 * Parameters:
 *   ctx      - body filter context
 *   chunk    - response body chunk to process
 *   last_buf - 1 if this is the final buffer in the chain, 0 otherwise
 */
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

/*
 * Create a fresh body context with eligible=1 and all other fields zeroed.
 *
 * Returns:
 *   Zero-initialized body_ctx_t with conversion eligibility enabled.
 */
static body_ctx_t
new_ctx(void)
{
    body_ctx_t c;
    memset(&c, 0, sizeof(c));
    c.eligible = 1;
    return c;
}

/*
 * Verify that chunks are buffered without triggering conversion until
 * the last buffer arrives.  This ensures the full response body is
 * available before the conversion pipeline runs.
 *
 * Expected: conversion_called is 0 after intermediate chunks, 1 after last_buf.
 */
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

/*
 * Verify that decompression runs before conversion when the upstream
 * response is compressed.  The body filter must decompress first,
 * then pass the decompressed content to the conversion pipeline.
 *
 * Expected: both decompression_called and conversion_called are 1.
 */
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

/*
 * Verify that HEAD requests still run the conversion pipeline
 * (to update Content-Type, ETag, etc.) but produce an empty body.
 *
 * Expected: conversion_called is 1, body size is 0.
 */
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

/*
 * Verify that ineligible requests (wrong method, content-type, etc.)
 * bypass both decompression and conversion.  The body passes through
 * unchanged to the next filter in the chain.
 *
 * Expected: conversion_called and decompression_called are both 0.
 */
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
