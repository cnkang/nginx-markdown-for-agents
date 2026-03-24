/*
 * Test: header_compile
 * Description: compile-time smoke test for typedef ordering
 *
 * This test provides a compile-time smoke check for a critical
 * typedef/struct dependency chain used by the module header.
 *
 * The test mirrors the critical type dependency chain from
 * ngx_http_markdown_filter_module.h:
 *
 *   ngx_http_markdown_error_category_t  (enum)
 *     must be defined before
 *   ngx_http_markdown_ctx_t             (struct)
 *     which contains a last_error_category field
 *
 * IMPORTANT: This unit test does NOT compile the real module header.
 * It only compiles a minimal mirror of the dependency chain. This
 * catches regressions when the mirror is kept in sync with the real
 * header, but it will not automatically detect changes unless the
 * mirror is updated accordingly.
 *
 * Limitation: This test cannot include the real module header
 * because the unit test infrastructure does not provide NGINX
 * stub headers (ngx_config.h, ngx_core.h, ngx_http.h).  It
 * mirrors the type dependency chain instead.  For full header
 * inclusion testing, use the integration test suite which
 * compiles against real NGINX headers.
 */

#include "test_common.h"

/*
 * Mirror of ngx_http_markdown_compression_type_e from the
 * module header.  Must be defined before ctx_t.
 */
typedef enum {
    TEST_COMPRESSION_NONE = 0,
    TEST_COMPRESSION_GZIP,
    TEST_COMPRESSION_DEFLATE,
    TEST_COMPRESSION_BROTLI,
    TEST_COMPRESSION_UNKNOWN
} test_compression_type_e;

/*
 * Mirror of ngx_http_markdown_error_category_t from the
 * module header.  Must be defined before ctx_t.
 */
typedef enum {
    TEST_ERROR_CONVERSION = 0,
    TEST_ERROR_RESOURCE_LIMIT,
    TEST_ERROR_SYSTEM
} test_error_category_t;

/*
 * Mirror of ngx_http_markdown_eligibility_t from the module
 * header.  Must be defined before function prototypes that
 * reference it.
 */
typedef enum {
    TEST_ELIGIBLE = 0,
    TEST_INELIGIBLE_METHOD,
    TEST_INELIGIBLE_STATUS,
    TEST_INELIGIBLE_CONTENT_TYPE,
    TEST_INELIGIBLE_SIZE,
    TEST_INELIGIBLE_STREAMING,
    TEST_INELIGIBLE_AUTH,
    TEST_INELIGIBLE_RANGE,
    TEST_INELIGIBLE_CONFIG
} test_eligibility_t;

/*
 * Minimal mirror of ngx_http_markdown_ctx_t that exercises
 * the critical type dependency: the struct must be able to
 * use test_error_category_t and test_compression_type_e as
 * field types.  If these enums were defined after this
 * struct, compilation would fail.
 */
typedef struct {
    int                      eligible;
    int                      conversion_attempted;
    int                      conversion_succeeded;
    unsigned int             processing_path;
    test_compression_type_e  compression_type;
    int                      decompression_needed;
    int                      decompression_done;
    size_t                   compressed_size;
    size_t                   decompressed_size;
    test_error_category_t    last_error_category;
    int                      has_error_category;
} test_ctx_t;


/* Function prototype using eligibility enum */
static const char *test_eligibility_string(test_eligibility_t e);


static const char *
test_eligibility_string(test_eligibility_t e)
{
    switch (e) {
    case TEST_ELIGIBLE:
        return "ELIGIBLE";
    case TEST_INELIGIBLE_METHOD:
        return "SKIP_METHOD";
    case TEST_INELIGIBLE_STATUS:
        return "SKIP_STATUS";
    case TEST_INELIGIBLE_CONTENT_TYPE:
        return "SKIP_CONTENT_TYPE";
    case TEST_INELIGIBLE_SIZE:
        return "SKIP_SIZE";
    case TEST_INELIGIBLE_STREAMING:
        return "SKIP_STREAMING";
    case TEST_INELIGIBLE_AUTH:
        return "SKIP_AUTH";
    case TEST_INELIGIBLE_RANGE:
        return "SKIP_RANGE";
    case TEST_INELIGIBLE_CONFIG:
        return "SKIP_CONFIG";
    default:
        return "UNKNOWN";
    }
}


static void
test_ctx_uses_error_category(void)
{
    test_ctx_t ctx;

    TEST_SUBSECTION("ctx_t uses error_category_t field");

    memset(&ctx, 0, sizeof(ctx));
    ctx.last_error_category = TEST_ERROR_CONVERSION;
    ctx.has_error_category = 1;

    TEST_ASSERT(ctx.last_error_category == TEST_ERROR_CONVERSION,
                "error category field stores CONVERSION");

    ctx.last_error_category = TEST_ERROR_RESOURCE_LIMIT;
    TEST_ASSERT(ctx.last_error_category
                == TEST_ERROR_RESOURCE_LIMIT,
                "error category field stores RESOURCE_LIMIT");

    ctx.last_error_category = TEST_ERROR_SYSTEM;
    TEST_ASSERT(ctx.last_error_category == TEST_ERROR_SYSTEM,
                "error category field stores SYSTEM");

    TEST_PASS("ctx_t error_category field compiles and works");
}


static void
test_ctx_uses_compression_type(void)
{
    test_ctx_t ctx;

    TEST_SUBSECTION("ctx_t uses compression_type_e field");

    memset(&ctx, 0, sizeof(ctx));
    ctx.compression_type = TEST_COMPRESSION_GZIP;

    TEST_ASSERT(ctx.compression_type == TEST_COMPRESSION_GZIP,
                "compression type field stores GZIP");

    ctx.compression_type = TEST_COMPRESSION_UNKNOWN;
    TEST_ASSERT(ctx.compression_type
                == TEST_COMPRESSION_UNKNOWN,
                "compression type field stores UNKNOWN");

    TEST_PASS("ctx_t compression_type field compiles and works");
}


static void
test_eligibility_enum_before_prototypes(void)
{
    const char *s;

    TEST_SUBSECTION("eligibility_t usable in function prototypes");

    s = test_eligibility_string(TEST_ELIGIBLE);
    TEST_ASSERT(strcmp(s, "ELIGIBLE") == 0,
                "ELIGIBLE maps to correct string");

    s = test_eligibility_string(TEST_INELIGIBLE_CONFIG);
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
