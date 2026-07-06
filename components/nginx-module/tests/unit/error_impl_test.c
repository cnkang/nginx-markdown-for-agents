/*
 * Test: error_impl
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

uint8_t
markdown_classify_error_code(uint32_t error_code)
{
    switch (error_code) {
    case ERROR_PARSE:
    case ERROR_ENCODING:
    case ERROR_INVALID_INPUT:
    case ERROR_DECOMPRESSION_FORMAT_ERROR:
    case ERROR_DECOMPRESSION_TRUNCATED_INPUT:
    case ERROR_DECOMPRESSION_IO_ERROR:
    case ERROR_STREAMING_FALLBACK:
        return 0; /* ConversionError */

    case ERROR_TIMEOUT:
    case ERROR_PARSE_TIMEOUT:
        return 1; /* Timeout */

    case ERROR_MEMORY_LIMIT:
    case ERROR_BUDGET_EXCEEDED:
    case ERROR_DECOMPRESSION_BUDGET_EXCEEDED:
    case ERROR_PARSE_BUDGET_EXCEEDED:
        return 2; /* MemoryBudgetExceeded */

    case ERROR_POST_COMMIT:
        return 9; /* StreamingMidFlightError */

    default:
        return 3; /* FfiPanic/system fallback for unknown test inputs */
    }
}


/*
 * Verify all base (non-streaming) FFI-defined error codes are
 * classified correctly by the production
 * ngx_http_markdown_classify_error() function.
 *
 * Expected: ERROR_PARSE/ENCODING/INVALID_INPUT -> CONVERSION,
 * ERROR_TIMEOUT/MEMORY_LIMIT -> RESOURCE_LIMIT, ERROR_INTERNAL -> SYSTEM.
 */
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


/*
 * Verify streaming-specific FFI error codes are classified correctly.
 * Tests ERROR_BUDGET_EXCEEDED (6), ERROR_STREAMING_FALLBACK (7),
 * and ERROR_POST_COMMIT (8).
 *
 * Expected: BUDGET_EXCEEDED -> RESOURCE_LIMIT,
 * STREAMING_FALLBACK -> CONVERSION, POST_COMMIT -> SYSTEM.
 */
static void
test_classify_streaming_specific_codes(void)
{
    TEST_SUBSECTION("classify_error: streaming-specific FFI codes (Rule 7/15)");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_BUDGET_EXCEEDED) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_BUDGET_EXCEEDED (6) -> RESOURCE_LIMIT (budget is a resource limit)");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_STREAMING_FALLBACK) == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        "ERROR_STREAMING_FALLBACK (7) -> CONVERSION (engine downgrade)");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_POST_COMMIT) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "ERROR_POST_COMMIT (8) -> SYSTEM (post-commit terminates connection)");

    TEST_PASS("Streaming-specific error codes classified correctly");
}


/*
 * Verify v0.7.0 FFI error codes are classified correctly.
 * Tests ERROR_DECOMPRESSION_BUDGET_EXCEEDED (9), ERROR_PARSE_TIMEOUT (10),
 * and ERROR_PARSE_BUDGET_EXCEEDED (11).
 *
 * Expected: all three map to RESOURCE_LIMIT (budget/timeout resource limits).
 */
static void
test_classify_v070_codes(void)
{
    TEST_SUBSECTION("classify_error: v0.7.0 FFI codes (Rule 7/15)");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_DECOMPRESSION_BUDGET_EXCEEDED) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_DECOMPRESSION_BUDGET_EXCEEDED (9) -> RESOURCE_LIMIT");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_PARSE_TIMEOUT) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_PARSE_TIMEOUT (10) -> RESOURCE_LIMIT");

    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_PARSE_BUDGET_EXCEEDED) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_PARSE_BUDGET_EXCEEDED (11) -> RESOURCE_LIMIT");

    TEST_PASS("v0.7.0 error codes classified correctly");
}


/*
 * Verify unknown and boundary error codes fall through to SYSTEM.
 * Tests ERROR_SUCCESS (0), arbitrary code 42, and UINT32_MAX.
 *
 * Expected: all three map to NGX_HTTP_MARKDOWN_ERROR_SYSTEM.
 */
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


/*
 * Verify error_category_string returns correct strings for all three
 * categories plus unknown.  Checks non-NULL, non-empty, and correct length.
 *
 * Expected: each category maps to its documented string representation.
 */
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
    test_classify_v070_codes();
    test_classify_unknown_and_boundary();
    test_category_string_all_values();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
