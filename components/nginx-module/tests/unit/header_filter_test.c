/*
 * Test: header_filter
 * Description: header filter functionality
 *
 * Validates NGINX header filter chain registration (markdown filter
 * inserts itself at the top and calls the next filter) and compression
 * detection flags in the header filter context.
 */

#include "test_common.h"

/*
 * Header filter function type.
 */
typedef int (*header_filter_pt)(void *req);

/*
 * Global filter chain pointers simulating NGINX's filter chain.
 */
static header_filter_pt ngx_http_top_header_filter;
static header_filter_pt ngx_http_next_header_filter;

/*
 * Flags tracking filter invocations.
 */
static int dummy_next_filter_called;
static int markdown_filter_called;

/*
 * Dummy next filter that increments invocation counter.
 */
static int
next_filter(void *req)
{
    UNUSED(req);
    dummy_next_filter_called++;
    return 0;
}

/*
 * Simulated markdown header filter: increments counter and chains
 * to the next filter in the chain.
 */
static int
markdown_header_filter(void *req)
{
    UNUSED(req);
    markdown_filter_called++;
    if (ngx_http_next_header_filter != NULL) {
        return ngx_http_next_header_filter(req);
    }
    return 0;
}

/*
 * Simulated filter module init: inserts markdown filter at the top
 * of the header filter chain, saving the previous top filter as next.
 */
static int
markdown_filter_init(void)
{
    ngx_http_next_header_filter = ngx_http_top_header_filter;
    ngx_http_top_header_filter = markdown_header_filter;
    return 0;
}

/*
 * Header filter context with compression detection state.
 */
typedef struct {
    int auto_decompress;
    int compression_type;
    int decompression_needed;
} ctx_t;

/*
 * Detect compression from header filter context.
 * Sets decompression_needed if auto_decompress is enabled and a
 * supported compression type (1=gzip, 2=deflate, 3=brotli) is detected.
 *
 * Parameters:
 *   ctx - header filter context with compression state
 */
static void
header_filter_detect(ctx_t *ctx)
{
    if (!ctx->auto_decompress) {
        ctx->decompression_needed = 0;
        return;
    }
    if (ctx->compression_type == 1 || ctx->compression_type == 2 || ctx->compression_type == 3) {
        ctx->decompression_needed = 1;
    } else {
        ctx->decompression_needed = 0;
    }
}

/*
 * Verify header filter chain registration: markdown filter replaces
 * the top filter and still chains to the previous filter.
 *
 * Expected: top filter is markdown, both markdown and next filters called.
 */
static void
test_filter_chain_registration(void)
{
    TEST_SUBSECTION("Header filter chain registration");

    ngx_http_top_header_filter = next_filter;
    ngx_http_next_header_filter = NULL;
    dummy_next_filter_called = 0;
    markdown_filter_called = 0;

    TEST_ASSERT(markdown_filter_init() == 0, "init should succeed");
    TEST_ASSERT(ngx_http_top_header_filter == markdown_header_filter,
                "top filter should be replaced by markdown filter");

    ngx_http_top_header_filter(NULL);
    TEST_ASSERT(markdown_filter_called == 1, "markdown filter should be called");
    TEST_ASSERT(dummy_next_filter_called == 1, "next filter should still be called");
    TEST_PASS("Filter chain registration works");
}

/*
 * Verify compression detection flags: supported compression sets
 * decompression_needed, no compression clears it, auto_decompress=off
 * disables detection entirely.
 *
 * Expected: correct flag state for each scenario.
 */
static void
test_compression_detection_flags(void)
{
    ctx_t ctx;
    TEST_SUBSECTION("Header filter compression detection flags");

    ctx.auto_decompress = 1;
    ctx.compression_type = 1; /* gzip */
    ctx.decompression_needed = 0;
    header_filter_detect(&ctx);
    TEST_ASSERT(ctx.decompression_needed == 1, "Supported compression should set decompression_needed");

    ctx.compression_type = 0; /* none */
    ctx.decompression_needed = 1;
    header_filter_detect(&ctx);
    TEST_ASSERT(ctx.decompression_needed == 0, "No compression should clear decompression_needed");

    ctx.auto_decompress = 0;
    ctx.compression_type = 1;
    ctx.decompression_needed = 1;
    header_filter_detect(&ctx);
    TEST_ASSERT(ctx.decompression_needed == 0, "auto_decompress=off should disable decompression");
    TEST_PASS("Compression detection flags work");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("header_filter Tests\n");
    printf("========================================\n");

    test_filter_chain_registration();
    test_compression_detection_flags();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
