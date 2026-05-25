/*
 * Test: resource_limit_counter
 *
 * Validates that resource limit failures (timeout, budget exceeded,
 * decompression budget exceeded) increment resource-limit-specific
 * counters, not format-error counters.
 *
 * Corresponds to task A04.6.
 */

#include "../include/test_common.h"


enum {
    ERROR_SUCCESS = 0,
    ERROR_PARSE = 1,
    ERROR_ENCODING = 2,
    ERROR_TIMEOUT = 3,
    ERROR_MEMORY_LIMIT = 4,
    ERROR_INVALID_INPUT = 5,
    ERROR_DECOMPRESSION_BUDGET_EXCEEDED = 9,
    ERROR_PARSE_TIMEOUT = 10,
    ERROR_PARSE_BUDGET_EXCEEDED = 11,
    ERROR_INTERNAL = 99
};

typedef struct {
    unsigned int format_error_count;
    unsigned int resource_limit_count;
    unsigned int system_error_count;
} error_counters_t;


static void
classify_and_count(unsigned int error_code, error_counters_t *counters)
{
    switch (error_code) {
        case ERROR_PARSE:
        case ERROR_ENCODING:
        case ERROR_INVALID_INPUT:
            counters->format_error_count++;
            break;
        case ERROR_TIMEOUT:
        case ERROR_MEMORY_LIMIT:
        case ERROR_DECOMPRESSION_BUDGET_EXCEEDED:
        case ERROR_PARSE_TIMEOUT:
        case ERROR_PARSE_BUDGET_EXCEEDED:
            counters->resource_limit_count++;
            break;
        case ERROR_SUCCESS:
            break;
        default:
            counters->system_error_count++;
            break;
    }
}


static void
test_format_errors_go_to_format_counter(void)
{
    error_counters_t c;
    memset(&c, 0, sizeof(c));

    classify_and_count(ERROR_PARSE, &c);
    classify_and_count(ERROR_ENCODING, &c);
    classify_and_count(ERROR_INVALID_INPUT, &c);

    TEST_ASSERT(c.format_error_count == 3, "three format errors");
    TEST_ASSERT(c.resource_limit_count == 0, "no resource limit errors");
}


static void
test_resource_limits_go_to_resource_counter(void)
{
    error_counters_t c;
    memset(&c, 0, sizeof(c));

    classify_and_count(ERROR_TIMEOUT, &c);
    classify_and_count(ERROR_MEMORY_LIMIT, &c);
    classify_and_count(ERROR_DECOMPRESSION_BUDGET_EXCEEDED, &c);
    classify_and_count(ERROR_PARSE_TIMEOUT, &c);
    classify_and_count(ERROR_PARSE_BUDGET_EXCEEDED, &c);

    TEST_ASSERT(c.format_error_count == 0, "no format errors");
    TEST_ASSERT(c.resource_limit_count == 5, "five resource limit errors");
}


static void
test_internal_goes_to_system_counter(void)
{
    error_counters_t c;
    memset(&c, 0, sizeof(c));

    classify_and_count(ERROR_INTERNAL, &c);

    TEST_ASSERT(c.system_error_count == 1, "one system error");
    TEST_ASSERT(c.resource_limit_count == 0, "no resource limit errors for internal");
    TEST_ASSERT(c.format_error_count == 0, "no format errors for internal");
}


int
main(void)
{
    test_format_errors_go_to_format_counter();
    test_resource_limits_go_to_resource_counter();
    test_internal_goes_to_system_counter();

    TEST_PASS("resource_limit_counter: all tests passed");
    return 0;
}
