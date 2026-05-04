/*
 * Test: error_impl
 * Description: direct coverage for production error classification code.
 *
 * Includes the actual production source file so coverage instruments
 * the real code paths.
 *
 * Validates: Rule 7 (reason-code classification completeness),
 *            Rule 15 (FFI error code coverage across language boundary).
 */

#include "../include/test_common.h"
#include <limits.h>

#include "../../src/ngx_http_markdown_filter_module.h"

#include "../src/ngx_http_markdown_error.c"


static void
test_classify_all_defined_codes(void)
{
    TEST_SUBSECTION("classify_error: all FFI-defined error codes");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_PARSE) == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        "ERROR_PARSE -> CONVERSION");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_ENCODING) == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        "ERROR_ENCODING -> CONVERSION");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_INVALID_INPUT) == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        "ERROR_INVALID_INPUT -> CONVERSION");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_TIMEOUT) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_TIMEOUT -> RESOURCE_LIMIT");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_MEMORY_LIMIT) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_MEMORY_LIMIT -> RESOURCE_LIMIT");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_INTERNAL) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "ERROR_INTERNAL -> SYSTEM");

    TEST_PASS("All base error codes classified correctly");
}


static void
test_classify_streaming_specific_codes(void)
{
    TEST_SUBSECTION("classify_error: streaming-specific FFI codes (Rule 7/15)");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_BUDGET_EXCEEDED) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_BUDGET_EXCEEDED (6) -> RESOURCE_LIMIT (budget is a resource limit)");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_STREAMING_FALLBACK) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "ERROR_STREAMING_FALLBACK (7) -> SYSTEM (engine downgrade)");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_POST_COMMIT) == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        "ERROR_POST_COMMIT (8) -> CONVERSION (post-commit is a conversion failure)");

    TEST_PASS("Streaming-specific error codes classified correctly");
}


static void
test_classify_unknown_and_boundary(void)
{
    TEST_SUBSECTION("classify_error: unknown and boundary values");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_SUCCESS) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "ERROR_SUCCESS (0) falls to default -> SYSTEM");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(42) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "Unknown code 42 -> SYSTEM (default)");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(UINT32_MAX) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "UINT32_MAX -> SYSTEM (default)");

    TEST_PASS("Unknown/boundary codes -> SYSTEM (default)");
}


static void
test_category_string_all_values(void)
{
    const ngx_str_t *s;

    TEST_SUBSECTION("error_category_string: all category values");

    s = ngx_http_markdown_error_category_string(NGX_HTTP_MARKDOWN_ERROR_CONVERSION);
    TEST_ASSERT(s != NULL && s->len > 0, "CONVERSION string non-empty");
    TEST_ASSERT(s->len == strlen("conversion"), "CONVERSION string length");

    s = ngx_http_markdown_error_category_string(NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT);
    TEST_ASSERT(s != NULL && s->len > 0, "RESOURCE_LIMIT string non-empty");
    TEST_ASSERT(s->len == strlen("resource_limit"), "RESOURCE_LIMIT string length");

    s = ngx_http_markdown_error_category_string(NGX_HTTP_MARKDOWN_ERROR_SYSTEM);
    TEST_ASSERT(s != NULL && s->len > 0, "SYSTEM string non-empty");
    TEST_ASSERT(s->len == strlen("system"), "SYSTEM string length");

    s = ngx_http_markdown_error_category_string((ngx_http_markdown_error_category_t) 99);
    TEST_ASSERT(s != NULL && s->len > 0, "Unknown category -> 'unknown' string non-empty");
    TEST_ASSERT(s->len == strlen("unknown"), "Unknown category string length");

    TEST_PASS("All category strings verified");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("error_impl Tests (production code)\n");
    printf("========================================\n");

    test_classify_all_defined_codes();
    test_classify_streaming_specific_codes();
    test_classify_unknown_and_boundary();
    test_category_string_all_values();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
