/*
 * Test: failure_strategies
 * Description: failure strategy handling
 */

#include "test_common.h"

#define ON_ERROR_PASS 0
#define ON_ERROR_REJECT 1

typedef struct {
    int status;
    int used_fail_open;
    int used_fail_closed;
} failure_result_t;

static failure_result_t
handle_conversion_failure(int on_error, int upstream_status)
{
    failure_result_t out;
    out.status = upstream_status;
    out.used_fail_open = 0;
    out.used_fail_closed = 0;

    if (on_error == ON_ERROR_REJECT) {
        out.status = 502;
        out.used_fail_closed = 1;
    } else {
        out.status = upstream_status;
        out.used_fail_open = 1;
    }

    return out;
}

static void
test_fail_open_strategy(void)
{
    failure_result_t r;
    TEST_SUBSECTION("Fail-open strategy returns original response");
    r = handle_conversion_failure(ON_ERROR_PASS, 200);
    TEST_ASSERT(r.status == 200, "Fail-open should keep original status");
    TEST_ASSERT(r.used_fail_open == 1, "Fail-open flag should be set");
    TEST_ASSERT(r.used_fail_closed == 0, "Fail-closed flag should be clear");
    TEST_PASS("Fail-open behavior is correct");
}

static void
test_fail_closed_strategy(void)
{
    failure_result_t r;
    TEST_SUBSECTION("Fail-closed strategy returns 502");
    r = handle_conversion_failure(ON_ERROR_REJECT, 200);
    TEST_ASSERT(r.status == 502, "Fail-closed should return 502");
    TEST_ASSERT(r.used_fail_closed == 1, "Fail-closed flag should be set");
    TEST_ASSERT(r.used_fail_open == 0, "Fail-open flag should be clear");
    TEST_PASS("Fail-closed behavior is correct");
}

static void
test_unknown_mode_defaults_to_fail_open(void)
{
    failure_result_t r;
    TEST_SUBSECTION("Unknown mode defaults to fail-open");
    r = handle_conversion_failure(99, 418);
    TEST_ASSERT(r.status == 418, "Unknown mode should preserve upstream status");
    TEST_ASSERT(r.used_fail_open == 1, "Unknown mode should use fail-open");
    TEST_PASS("Unknown mode fallback is correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("failure_strategies Tests\n");
    printf("========================================\n");

    test_fail_open_strategy();
    test_fail_closed_strategy();
    test_unknown_mode_defaults_to_fail_open();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
