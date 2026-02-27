/*
 * Test: error_classification
 * Description: error classification
 */

#include "test_common.h"

enum {
    ERROR_SUCCESS = 0,
    ERROR_PARSE = 1,
    ERROR_ENCODING = 2,
    ERROR_TIMEOUT = 3,
    ERROR_MEMORY_LIMIT = 4,
    ERROR_INVALID_INPUT = 5,
    ERROR_INTERNAL = 99
};

typedef enum {
    CAT_CONVERSION = 0,
    CAT_RESOURCE_LIMIT,
    CAT_SYSTEM
} error_category_t;

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

static void
test_conversion_errors(void)
{
    TEST_SUBSECTION("Conversion error classification");
    TEST_ASSERT(classify_error(ERROR_PARSE) == CAT_CONVERSION, "ERROR_PARSE should be conversion");
    TEST_ASSERT(classify_error(ERROR_ENCODING) == CAT_CONVERSION, "ERROR_ENCODING should be conversion");
    TEST_ASSERT(classify_error(ERROR_INVALID_INPUT) == CAT_CONVERSION, "ERROR_INVALID_INPUT should be conversion");
    TEST_PASS("Conversion classification works");
}

static void
test_resource_limit_errors(void)
{
    TEST_SUBSECTION("Resource limit error classification");
    TEST_ASSERT(classify_error(ERROR_TIMEOUT) == CAT_RESOURCE_LIMIT, "ERROR_TIMEOUT should be resource_limit");
    TEST_ASSERT(classify_error(ERROR_MEMORY_LIMIT) == CAT_RESOURCE_LIMIT, "ERROR_MEMORY_LIMIT should be resource_limit");
    TEST_PASS("Resource limit classification works");
}

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

int
main(void)
{
    printf("\n========================================\n");
    printf("error_classification Tests\n");
    printf("========================================\n");

    test_conversion_errors();
    test_resource_limit_errors();
    test_system_errors_and_strings();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
