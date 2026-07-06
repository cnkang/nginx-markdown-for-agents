/*
 * Test: error_classification
 *
 * Includes the actual production source file so coverage instruments
 * the real code paths.  Validates FFI error code classification into
 * semantic categories: CONVERSION, RESOURCE_LIMIT, SYSTEM, plus
 * category string mapping and unknown/boundary handling.
 *
 * This file was rewritten from a local re-implementation to test
 * production code (Rule 14: side-effect tests drive outcome through
 * production branching, not manual mutation).
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
#if defined(MARKDOWN_STREAMING_ENABLED)
    case ERROR_STREAMING_FALLBACK:
#endif
        return 0; /* ConversionError */

    case ERROR_TIMEOUT:
    case ERROR_PARSE_TIMEOUT:
        return 1; /* Timeout */

    case ERROR_MEMORY_LIMIT:
    case ERROR_DECOMPRESSION_BUDGET_EXCEEDED:
    case ERROR_PARSE_BUDGET_EXCEEDED:
#if defined(MARKDOWN_STREAMING_ENABLED)
    case ERROR_BUDGET_EXCEEDED:
#endif
        return 2; /* MemoryBudgetExceeded */

    case ERROR_DECOMPRESSION_FORMAT_ERROR:
    case ERROR_DECOMPRESSION_TRUNCATED_INPUT:
    case ERROR_DECOMPRESSION_IO_ERROR:
        return 4; /* DecompressionError */

#if defined(MARKDOWN_STREAMING_ENABLED)
    case ERROR_POST_COMMIT:
        return 9; /* StreamingMidFlightError */
#endif

    default:
        return 3; /* FfiPanic/system fallback for unknown test inputs */
    }
}


static void
test_conversion_errors(void)
{
    TEST_SUBSECTION("Conversion error classification");
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
        ngx_http_markdown_classify_error(ERROR_DECOMPRESSION_FORMAT_ERROR) == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        "ERROR_DECOMPRESSION_FORMAT_ERROR (12) -> CONVERSION");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_DECOMPRESSION_TRUNCATED_INPUT) == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        "ERROR_DECOMPRESSION_TRUNCATED_INPUT (13) -> CONVERSION");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_DECOMPRESSION_IO_ERROR) == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        "ERROR_DECOMPRESSION_IO_ERROR (14) -> CONVERSION");
    TEST_PASS("Conversion classification works");
}

static void
test_resource_limit_errors(void)
{
    TEST_SUBSECTION("Resource limit error classification");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_TIMEOUT) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_TIMEOUT -> RESOURCE_LIMIT");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_MEMORY_LIMIT) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_MEMORY_LIMIT -> RESOURCE_LIMIT");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_DECOMPRESSION_BUDGET_EXCEEDED) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_DECOMPRESSION_BUDGET_EXCEEDED (9) -> RESOURCE_LIMIT");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_PARSE_TIMEOUT) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_PARSE_TIMEOUT (10) -> RESOURCE_LIMIT");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_PARSE_BUDGET_EXCEEDED) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_PARSE_BUDGET_EXCEEDED (11) -> RESOURCE_LIMIT");
#if defined(MARKDOWN_STREAMING_ENABLED)
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_BUDGET_EXCEEDED) == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        "ERROR_BUDGET_EXCEEDED -> RESOURCE_LIMIT");
#endif
    TEST_PASS("Resource limit classification works");
}

static void
test_system_errors_and_strings(void)
{
    const ngx_str_t *s;

    TEST_SUBSECTION("System fallback and category string");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_INTERNAL) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "ERROR_INTERNAL -> SYSTEM");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(12345U) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "Unknown error code -> SYSTEM");
#if defined(MARKDOWN_STREAMING_ENABLED)
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_STREAMING_FALLBACK) == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        "ERROR_STREAMING_FALLBACK -> CONVERSION");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_POST_COMMIT) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "ERROR_POST_COMMIT -> SYSTEM");
#endif

    s = ngx_http_markdown_error_category_string(NGX_HTTP_MARKDOWN_ERROR_CONVERSION);
    TEST_ASSERT(s != NULL && s->len > 0, "CONVERSION string non-empty");

    s = ngx_http_markdown_error_category_string(NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT);
    TEST_ASSERT(s != NULL && s->len > 0, "RESOURCE_LIMIT string non-empty");

    s = ngx_http_markdown_error_category_string(NGX_HTTP_MARKDOWN_ERROR_SYSTEM);
    TEST_ASSERT(s != NULL && s->len > 0, "SYSTEM string non-empty");

    TEST_PASS("System fallback and strings are correct");
}

static void
test_error_success_mapping(void)
{
    TEST_SUBSECTION("ERROR_SUCCESS (code 0) maps to system default");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(ERROR_SUCCESS) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "ERROR_SUCCESS (0) -> SYSTEM (default)");
    TEST_PASS("ERROR_SUCCESS mapping correct");
}

static void
test_unknown_category_string(void)
{
    const ngx_str_t *s;

    TEST_SUBSECTION("Unknown category value returns \"unknown\"");
    s = ngx_http_markdown_error_category_string((ngx_http_markdown_error_category_t) 99);
    TEST_ASSERT(s != NULL && s->len > 0, "Out-of-range category -> unknown string non-empty");
    s = ngx_http_markdown_error_category_string((ngx_http_markdown_error_category_t) 255);
    TEST_ASSERT(s != NULL && s->len > 0, "High out-of-range category -> unknown string non-empty");
    TEST_PASS("Unknown category string correct");
}

static void
test_error_code_classification_completeness(void)
{
    uint32_t codes[] = {
        ERROR_SUCCESS, ERROR_PARSE, ERROR_ENCODING,
        ERROR_TIMEOUT, ERROR_MEMORY_LIMIT, ERROR_INVALID_INPUT,
        ERROR_DECOMPRESSION_BUDGET_EXCEEDED,
        ERROR_PARSE_TIMEOUT,
        ERROR_PARSE_BUDGET_EXCEEDED,
        ERROR_DECOMPRESSION_FORMAT_ERROR,
        ERROR_DECOMPRESSION_TRUNCATED_INPUT,
        ERROR_DECOMPRESSION_IO_ERROR,
        ERROR_INTERNAL
    };
    ngx_http_markdown_error_category_t expected[] = {
        NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        NGX_HTTP_MARKDOWN_ERROR_SYSTEM
    };

    TEST_SUBSECTION("Error code classification completeness");
    for (size_t i = 0; i < ARRAY_SIZE(codes); i++) {
        TEST_ASSERT(
            ngx_http_markdown_classify_error(codes[i]) == expected[i],
            "Each defined error code maps to correct category");
    }

    TEST_ASSERT(
        ngx_http_markdown_classify_error(42U) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "Unknown code 42 -> SYSTEM");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(100U) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "Unknown code 100 -> SYSTEM");
    TEST_ASSERT(
        ngx_http_markdown_classify_error(UINT32_MAX) == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
        "UINT32_MAX -> SYSTEM");

    TEST_PASS("Error code classification completeness verified");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("error_classification Tests (production code)\n");
    printf("========================================\n");

    test_conversion_errors();
    test_resource_limit_errors();
    test_system_errors_and_strings();
    test_error_success_mapping();
    test_unknown_category_string();
    test_error_code_classification_completeness();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
