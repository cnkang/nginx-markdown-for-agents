/*
 * Test: error_classification
 *
 * Validates FFI error code classification into semantic categories:
 * CONVERSION (parse/encoding/invalid_input), RESOURCE_LIMIT (timeout/
 * memory_limit), SYSTEM (internal/unknown).  Also tests category string
 * mapping and ERROR_SUCCESS handling.
 */

#include "../include/test_common.h"
#include <limits.h>

/*
 * FFI error code constants matching the Rust converter ABI.
 */
enum {
    ERROR_SUCCESS = 0,
    ERROR_PARSE = 1,
    ERROR_ENCODING = 2,
    ERROR_TIMEOUT = 3,
    ERROR_MEMORY_LIMIT = 4,
    ERROR_INVALID_INPUT = 5,
    ERROR_INTERNAL = 99
};

/*
 * Error category enumeration for classification.
 */
typedef enum {
    CAT_CONVERSION = 0,
    CAT_RESOURCE_LIMIT,
    CAT_SYSTEM
} error_category_t;

/*
 * Classify an FFI error code into a semantic category.
 *
 * Parameters:
 *   error_code - FFI error code from the Rust converter
 *
 * Returns:
 *   CAT_CONVERSION for parse/encoding/input errors,
 *   CAT_RESOURCE_LIMIT for timeout/memory errors,
 *   CAT_SYSTEM for internal and unknown codes.
 */
static error_category_t
classify_error(unsigned int error_code)
{
    switch (error_code) {
        case ERROR_PARSE:
        case ERROR_ENCODING:
        case ERROR_INVALID_INPUT:
            return CAT_CONVERSION;
        case ERROR_TIMEOUT:
        case ERROR_MEMORY_LIMIT:
            return CAT_RESOURCE_LIMIT;
        case ERROR_INTERNAL:
        default:
            return CAT_SYSTEM;
    }
}

/*
 * Return a human-readable string for an error category.
 *
 * Parameters:
 *   category - error category enum value
 *
 * Returns:
 *   "conversion", "resource_limit", "system", or "unknown".
 */
static const char *
category_string(error_category_t category)
{
    switch (category) {
        case CAT_CONVERSION: return "conversion";
        case CAT_RESOURCE_LIMIT: return "resource_limit";
        case CAT_SYSTEM: return "system";
        default: return "unknown";
    }
}

/*
 * Verify conversion error classification: ERROR_PARSE, ERROR_ENCODING,
 * and ERROR_INVALID_INPUT all map to CAT_CONVERSION.
 *
 * Expected: all three codes return CAT_CONVERSION.
 */
static void
test_conversion_errors(void)
{
    TEST_SUBSECTION("Conversion error classification");
    TEST_ASSERT(classify_error(ERROR_PARSE) == CAT_CONVERSION, "ERROR_PARSE should be conversion");
    TEST_ASSERT(classify_error(ERROR_ENCODING) == CAT_CONVERSION, "ERROR_ENCODING should be conversion");
    TEST_ASSERT(classify_error(ERROR_INVALID_INPUT) == CAT_CONVERSION, "ERROR_INVALID_INPUT should be conversion");
    TEST_PASS("Conversion classification works");
}

/*
 * Verify resource limit error classification: ERROR_TIMEOUT and
 * ERROR_MEMORY_LIMIT both map to CAT_RESOURCE_LIMIT.
 *
 * Expected: both codes return CAT_RESOURCE_LIMIT.
 */
static void
test_resource_limit_errors(void)
{
    TEST_SUBSECTION("Resource limit error classification");
    TEST_ASSERT(classify_error(ERROR_TIMEOUT) == CAT_RESOURCE_LIMIT, "ERROR_TIMEOUT should be resource_limit");
    TEST_ASSERT(classify_error(ERROR_MEMORY_LIMIT) == CAT_RESOURCE_LIMIT, "ERROR_MEMORY_LIMIT should be resource_limit");
    TEST_PASS("Resource limit classification works");
}

/*
 * Verify system error fallback: ERROR_INTERNAL and unknown codes map
 * to CAT_SYSTEM.  Also verify category_string returns correct strings
 * for all three categories.
 *
 * Expected: fallback to CAT_SYSTEM for unknown codes; correct strings.
 */
static void
test_system_errors_and_strings(void)
{
    TEST_SUBSECTION("System fallback and category string");
    TEST_ASSERT(classify_error(ERROR_INTERNAL) == CAT_SYSTEM, "ERROR_INTERNAL should be system");
    TEST_ASSERT(classify_error(12345U) == CAT_SYSTEM, "Unknown error code should map to system");
    TEST_ASSERT(STR_EQ(category_string(CAT_CONVERSION), "conversion"), "Category string conversion");
    TEST_ASSERT(STR_EQ(category_string(CAT_RESOURCE_LIMIT), "resource_limit"), "Category string resource_limit");
    TEST_ASSERT(STR_EQ(category_string(CAT_SYSTEM), "system"), "Category string system");
    TEST_PASS("System fallback and strings are correct");
}

/* Task 5.1: ERROR_SUCCESS mapping and unknown category string */

/*
 * Verify ERROR_SUCCESS (code 0) maps to CAT_SYSTEM via the default branch.
 *
 * Expected: ERROR_SUCCESS returns CAT_SYSTEM.
 */
static void
test_error_success_mapping(void)
{
    TEST_SUBSECTION("ERROR_SUCCESS (code 0) maps to system default");
    TEST_ASSERT(classify_error(ERROR_SUCCESS) == CAT_SYSTEM,
                "ERROR_SUCCESS (0) should map to CAT_SYSTEM (default)");
    TEST_PASS("ERROR_SUCCESS mapping correct");
}

/*
 * Verify unknown category enum values return "unknown" from category_string.
 *
 * Expected: out-of-range category values return "unknown".
 */
static void
test_unknown_category_string(void)
{
    TEST_SUBSECTION("Unknown category value returns \"unknown\"");
    TEST_ASSERT(STR_EQ(category_string((error_category_t) 99), "unknown"),
                "Out-of-range category should return \"unknown\"");
    TEST_ASSERT(STR_EQ(category_string((error_category_t) 255), "unknown"),
                "High out-of-range category should return \"unknown\"");
    TEST_PASS("Unknown category string correct");
}

/* Feature: improve-test-coverage, Property 1: Error code classification completeness */

/*
 * Verify error code classification completeness: every defined error
 * code maps to the correct category, and unknown codes default to
 * CAT_SYSTEM.  Includes boundary value testing (UINT_MAX).
 *
 * Expected: all defined codes match expected categories; unknown codes
 * including UINT_MAX map to CAT_SYSTEM.
 */
static void
test_error_code_classification_completeness(void)
{
    unsigned int codes[] = {
        ERROR_SUCCESS, ERROR_PARSE, ERROR_ENCODING,
        ERROR_TIMEOUT, ERROR_MEMORY_LIMIT, ERROR_INVALID_INPUT,
        ERROR_INTERNAL
    };
    error_category_t expected[] = {
        CAT_SYSTEM, CAT_CONVERSION, CAT_CONVERSION,
        CAT_RESOURCE_LIMIT, CAT_RESOURCE_LIMIT, CAT_CONVERSION,
        CAT_SYSTEM
    };

    TEST_SUBSECTION("Property 1: Error code classification completeness");
    for (size_t i = 0; i < ARRAY_SIZE(codes); i++) {
        TEST_ASSERT(classify_error(codes[i]) == expected[i],
                    "Each defined error code maps to correct category");
    }

    /* Unknown codes default to CAT_SYSTEM */
    TEST_ASSERT(classify_error(42U) == CAT_SYSTEM, "Unknown code 42 -> CAT_SYSTEM");
    TEST_ASSERT(classify_error(100U) == CAT_SYSTEM, "Unknown code 100 -> CAT_SYSTEM");
    TEST_ASSERT(classify_error(200U) == CAT_SYSTEM, "Unknown code 200 -> CAT_SYSTEM");

    /* Boundary value: maximum unsigned int must also fall through to default */
    TEST_ASSERT(classify_error((unsigned int) UINT_MAX) == CAT_SYSTEM,
                "UINT_MAX -> CAT_SYSTEM (upper bound of unknown codes)");

    TEST_PASS("Error code classification completeness verified");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("error_classification Tests\n");
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
