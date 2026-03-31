/*
 * Test: header_compile
 * Description: compile-time smoke test for typedef ordering
 *
 * This test provides a compile-time smoke check for a critical
 * typedef/struct dependency chain used by the module header.
 *
 * It compiles the real ngx_http_markdown_filter_module.h header
 * using lightweight NGINX stub headers to verify the compile-time
 * correctness of structs and enums.
 */

#include "test_common.h"
#include <ngx_http_markdown_filter_module.h>


/* Function prototype using eligibility enum */
static const char *test_eligibility_string(ngx_http_markdown_eligibility_t e);


static const char *
test_eligibility_string(ngx_http_markdown_eligibility_t e)
{
    switch (e) {
    case NGX_HTTP_MARKDOWN_ELIGIBLE:
        return "ELIGIBLE";
    case NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD:
        return "SKIP_METHOD";
    case NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS:
        return "SKIP_STATUS";
    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE:
        return "SKIP_CONTENT_TYPE";
    case NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE:
        return "SKIP_SIZE";
    case NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING:
        return "SKIP_STREAMING";
    case NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH:
        return "SKIP_AUTH";
    case NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE:
        return "SKIP_RANGE";
    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG:
        return "SKIP_CONFIG";
    default:
        return "UNKNOWN";
    }
}


static void
test_ctx_uses_error_category(void)
{
    ngx_http_markdown_ctx_t ctx;

    TEST_SUBSECTION("ctx_t uses error_category_t field");

    memset(&ctx, 0, sizeof(ctx));
    ctx.last_error_category = NGX_HTTP_MARKDOWN_ERROR_CONVERSION;
    ctx.has_error_category = 1;

    TEST_ASSERT(ctx.last_error_category == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
                "error category field stores CONVERSION");

    ctx.last_error_category = NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT;
    TEST_ASSERT(ctx.last_error_category
                == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
                "error category field stores RESOURCE_LIMIT");

    ctx.last_error_category = NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    TEST_ASSERT(ctx.last_error_category == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
                "error category field stores SYSTEM");

    TEST_PASS("ctx_t error_category field compiles and works");
}


static void
test_ctx_uses_compression_type(void)
{
    ngx_http_markdown_ctx_t ctx;

    TEST_SUBSECTION("ctx_t uses decompression.type field");

    memset(&ctx, 0, sizeof(ctx));
    ctx.decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_GZIP;

    TEST_ASSERT(ctx.decompression.type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
                "compression type field stores GZIP");

    ctx.decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN;
    TEST_ASSERT(ctx.decompression.type
                == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN,
                "compression type field stores UNKNOWN");

    TEST_PASS("ctx_t decompression.type field compiles and works");
}


static void
test_eligibility_enum_before_prototypes(void)
{
    const char *s;

    TEST_SUBSECTION("eligibility_t usable in function prototypes");

    s = test_eligibility_string(NGX_HTTP_MARKDOWN_ELIGIBLE);
    TEST_ASSERT(strcmp(s, "ELIGIBLE") == 0,
                "ELIGIBLE maps to correct string");

    s = test_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG);
    TEST_ASSERT(strcmp(s, "SKIP_CONFIG") == 0,
                "INELIGIBLE_CONFIG maps to correct string");

    TEST_PASS("eligibility_t compiles before function prototypes");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("header_compile Tests (typedef ordering)\n");
    printf("========================================\n");

    test_ctx_uses_error_category();
    test_ctx_uses_compression_type();
    test_eligibility_enum_before_prototypes();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
