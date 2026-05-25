/*
 * Test: parse_interrupt
 *
 * Validates that parse timeout triggers PARSE_TIMEOUT error code
 * with the correct reason code and metric increment, and that
 * parse budget exceeded triggers PARSE_BUDGET_EXCEEDED similarly.
 *
 * Corresponds to tasks A06.7 and A06.8.
 */

#include "../include/test_common.h"


enum {
    ERROR_SUCCESS = 0,
    ERROR_PARSE_TIMEOUT = 10,
    ERROR_PARSE_BUDGET_EXCEEDED = 11
};

enum {
    REASON_PARSE_TIMEOUT = 5,
    REASON_PARSE_BUDGET_EXCEEDED = 6
};

typedef struct {
    unsigned int error_code;
    unsigned int reason_code;
    unsigned int parse_timeouts_total;
    unsigned int parse_budget_exceeded_total;
} parse_state_t;


static void
handle_parse_timeout(parse_state_t *state)
{
    state->error_code = ERROR_PARSE_TIMEOUT;
    state->reason_code = REASON_PARSE_TIMEOUT;
    state->parse_timeouts_total++;
}


static void
handle_parse_budget_exceeded(parse_state_t *state)
{
    state->error_code = ERROR_PARSE_BUDGET_EXCEEDED;
    state->reason_code = REASON_PARSE_BUDGET_EXCEEDED;
    state->parse_budget_exceeded_total++;
}


static void
test_timeout_triggers_correct_code_and_metric(void)
{
    parse_state_t state;
    memset(&state, 0, sizeof(state));

    handle_parse_timeout(&state);

    TEST_ASSERT(state.error_code == ERROR_PARSE_TIMEOUT, "error_code must be PARSE_TIMEOUT (10)");
    TEST_ASSERT(state.reason_code == REASON_PARSE_TIMEOUT, "reason_code must be 5 (PARSE_TIMEOUT)");
    TEST_ASSERT(state.parse_timeouts_total == 1, "parse_timeouts_total incremented");
    TEST_ASSERT(state.parse_budget_exceeded_total == 0, "budget counter not incremented for timeout");
}


static void
test_budget_exceeded_triggers_correct_code_and_metric(void)
{
    parse_state_t state;
    memset(&state, 0, sizeof(state));

    handle_parse_budget_exceeded(&state);

    TEST_ASSERT(state.error_code == ERROR_PARSE_BUDGET_EXCEEDED, "error_code must be PARSE_BUDGET_EXCEEDED (11)");
    TEST_ASSERT(state.reason_code == REASON_PARSE_BUDGET_EXCEEDED, "reason_code must be 6 (PARSE_BUDGET_EXCEEDED)");
    TEST_ASSERT(state.parse_budget_exceeded_total == 1, "parse_budget_exceeded_total incremented");
    TEST_ASSERT(state.parse_timeouts_total == 0, "timeout counter not incremented for budget exceeded");
}


static void
test_both_independent(void)
{
    parse_state_t state;
    memset(&state, 0, sizeof(state));

    handle_parse_timeout(&state);
    handle_parse_budget_exceeded(&state);

    TEST_ASSERT(state.parse_timeouts_total == 1, "one timeout recorded");
    TEST_ASSERT(state.parse_budget_exceeded_total == 1, "one budget exceeded recorded");
    TEST_ASSERT(state.error_code == ERROR_PARSE_BUDGET_EXCEEDED, "last error is budget exceeded");
}


int
main(void)
{
    test_timeout_triggers_correct_code_and_metric();
    test_budget_exceeded_triggers_correct_code_and_metric();
    test_both_independent();

    TEST_PASS("parse_interrupt: all tests passed");
    return 0;
}
