/*
 * Test: header_filter
 * Description: header filter functionality
 */

#include "test_common.h"

typedef int (*header_filter_pt)(void *req);

static header_filter_pt ngx_http_top_header_filter;
static header_filter_pt ngx_http_next_header_filter;

static int dummy_next_filter_called;
static int markdown_filter_called;

static int
next_filter(void *req)
{
    UNUSED(req);
    dummy_next_filter_called++;
    return 0;
}

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

static int
markdown_filter_init(void)
{
    ngx_http_next_header_filter = ngx_http_top_header_filter;
    ngx_http_top_header_filter = markdown_header_filter;
    return 0;
}

typedef struct {
    int auto_decompress;
    int compression_type;
    int decompression_needed;
} ctx_t;

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
