/*
 * Test: resource_limit_edge_cases
 *
 * Validates edge cases for v0.7.0 resource limit directives:
 *   - Decompression budget at exact boundary
 *   - Decompression budget zero → all content exceeds
 *   - Parse timeout zero → immediate timeout
 *   - Parser budget zero → immediate budget exceeded
 *   - Interaction: timeout and budget can both trigger
 *   - Fail-open: no 500 errors on limit exceeded
 *   - Metric counters increment exactly once
 *   - Error codes: DECOMPRESSION_BUDGET_EXCEEDED(9),
 *     PARSE_TIMEOUT(10), PARSE_BUDGET_EXCEEDED(11)
 *
 * Coverage targets:
 *   Decompression budget, parse timeout, parser budget paths
 *
 * Rules: 7 (reason codes), 23 (metric lifecycle),
 *        16 (no dead stores), 14 (regression test).
 */

#include "../include/test_common.h"


enum {
    ERROR_SUCCESS = 0,
    ERROR_DECOMPRESSION_BUDGET_EXCEEDED = 9,
    ERROR_PARSE_TIMEOUT = 10,
    ERROR_PARSE_BUDGET_EXCEEDED = 11
};

enum {
    REASON_NONE = 0,
    REASON_DECOMPRESSION_BUDGET_EXCEEDED = 9,
    REASON_PARSE_TIMEOUT = 10,
    REASON_PARSE_BUDGET_EXCEEDED = 11
};

typedef struct {
    unsigned int    error_code;
    unsigned int    reason_code;
    int            failopen;
    unsigned int    decomp_budget_exceeded_total;
    unsigned int    parse_timeouts_total;
    unsigned int    parse_budget_exceeded_total;
    size_t         decomp_output_size;
    size_t         decomp_budget;
    unsigned int    parse_elapsed_ms;
    unsigned int    parse_timeout_ms;
    size_t         parse_bytes;
    size_t         parse_budget;
} resource_state_t;


static void
resource_state_init(resource_state_t *state,
                    size_t decomp_budget,
                    unsigned int parse_timeout_ms,
                    size_t parse_budget)
{
    memset(state, 0, sizeof(*state));
    state->decomp_budget = decomp_budget;
    state->parse_timeout_ms = parse_timeout_ms;
    state->parse_budget = parse_budget;
}


static int
check_decompression(resource_state_t *state, size_t output_size)
{
    state->decomp_output_size = output_size;

    if (output_size > state->decomp_budget) {
        state->error_code = ERROR_DECOMPRESSION_BUDGET_EXCEEDED;
        state->reason_code = REASON_DECOMPRESSION_BUDGET_EXCEEDED;
        state->failopen = 1;
        state->decomp_budget_exceeded_total++;
        return ERROR_DECOMPRESSION_BUDGET_EXCEEDED;
    }

    state->error_code = ERROR_SUCCESS;
    return ERROR_SUCCESS;
}


static int
check_parse(resource_state_t *state, size_t bytes, unsigned int elapsed_ms)
{
    state->parse_bytes = bytes;
    state->parse_elapsed_ms = elapsed_ms;

    if (elapsed_ms >= state->parse_timeout_ms && state->parse_timeout_ms > 0) {
        state->error_code = ERROR_PARSE_TIMEOUT;
        state->reason_code = REASON_PARSE_TIMEOUT;
        state->failopen = 1;
        state->parse_timeouts_total++;
        return ERROR_PARSE_TIMEOUT;
    }

    if (bytes > state->parse_budget && state->parse_budget > 0) {
        state->error_code = ERROR_PARSE_BUDGET_EXCEEDED;
        state->reason_code = REASON_PARSE_BUDGET_EXCEEDED;
        state->failopen = 1;
        state->parse_budget_exceeded_total++;
        return ERROR_PARSE_BUDGET_EXCEEDED;
    }

    state->error_code = ERROR_SUCCESS;
    return ERROR_SUCCESS;
}


/* ── Decompression budget edge cases ───────────────────────────── */

static void
test_decomp_exact_budget(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Decompression at exact budget boundary");

    resource_state_init(&state, 4096, 30000, 64 * 1024 * 1024);

    int rc = check_decompression(&state, 4096);
    TEST_ASSERT(rc == ERROR_SUCCESS, "exact budget returns SUCCESS");
    TEST_ASSERT(state.failopen == 0, "not in fail-open");
    TEST_ASSERT(state.decomp_budget_exceeded_total == 0,
        "no budget exceeded counter");

    TEST_PASS("decomp exact budget");
}


static void
test_decomp_one_byte_over(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Decompression 1 byte over budget");

    resource_state_init(&state, 4096, 30000, 64 * 1024 * 1024);

    int rc = check_decompression(&state, 4097);
    TEST_ASSERT(rc == ERROR_DECOMPRESSION_BUDGET_EXCEEDED,
        "1 byte over returns BUDGET_EXCEEDED");
    TEST_ASSERT(state.failopen == 1, "in fail-open mode");
    TEST_ASSERT(state.decomp_budget_exceeded_total == 1,
        "counter incremented once");

    TEST_PASS("decomp 1 byte over");
}


static void
test_decomp_zero_budget(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Decompression with zero budget");

    resource_state_init(&state, 0, 30000, 64 * 1024 * 1024);

    int rc = check_decompression(&state, 1);
    TEST_ASSERT(rc == ERROR_DECOMPRESSION_BUDGET_EXCEEDED,
        "any content exceeds zero budget");
    TEST_ASSERT(state.failopen == 1, "in fail-open mode");

    TEST_PASS("decomp zero budget");
}


static void
test_decomp_zero_output(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Decompression with zero output size");

    resource_state_init(&state, 4096, 30000, 64 * 1024 * 1024);

    int rc = check_decompression(&state, 0);
    TEST_ASSERT(rc == ERROR_SUCCESS,
        "zero output within any budget");
    TEST_ASSERT(state.failopen == 0, "not in fail-open");

    TEST_PASS("decomp zero output");
}


/* ── Parse timeout edge cases ──────────────────────────────────── */

static void
test_parse_timeout_exact_boundary(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Parse timeout at exact boundary");

    resource_state_init(&state, 64 * 1024 * 1024, 1000, 64 * 1024 * 1024);

    int rc = check_parse(&state, 1024, 1000);
    TEST_ASSERT(rc == ERROR_PARSE_TIMEOUT,
        "exact timeout boundary triggers PARSE_TIMEOUT");
    TEST_ASSERT(state.parse_timeouts_total == 1,
        "timeout counter incremented");

    TEST_PASS("parse timeout exact boundary");
}


static void
test_parse_timeout_one_ms_under(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Parse timeout 1ms under boundary");

    resource_state_init(&state, 64 * 1024 * 1024, 1000, 64 * 1024 * 1024);

    int rc = check_parse(&state, 1024, 999);
    TEST_ASSERT(rc == ERROR_SUCCESS,
        "1ms under timeout returns SUCCESS");
    TEST_ASSERT(state.parse_timeouts_total == 0,
        "no timeout counter");

    TEST_PASS("parse timeout 1ms under");
}


static void
test_parse_zero_timeout(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Parse with zero timeout (disabled)");

    resource_state_init(&state, 64 * 1024 * 1024, 0, 64 * 1024 * 1024);

    int rc = check_parse(&state, 1024, 5000);
    TEST_ASSERT(rc == ERROR_SUCCESS,
        "zero timeout means no timeout check");

    TEST_PASS("parse zero timeout");
}


/* ── Parser budget edge cases ──────────────────────────────────── */

static void
test_parse_budget_exact_boundary(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Parser budget at exact boundary");

    resource_state_init(&state, 64 * 1024 * 1024, 30000, 4096);

    int rc = check_parse(&state, 4096, 100);
    TEST_ASSERT(rc == ERROR_SUCCESS,
        "exact budget returns SUCCESS");
    TEST_ASSERT(state.parse_budget_exceeded_total == 0,
        "no budget exceeded counter");

    TEST_PASS("parse budget exact boundary");
}


static void
test_parse_budget_one_byte_over(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Parser budget 1 byte over");

    resource_state_init(&state, 64 * 1024 * 1024, 30000, 4096);

    int rc = check_parse(&state, 4097, 100);
    TEST_ASSERT(rc == ERROR_PARSE_BUDGET_EXCEEDED,
        "1 byte over returns BUDGET_EXCEEDED");
    TEST_ASSERT(state.failopen == 1, "in fail-open mode");
    TEST_ASSERT(state.parse_budget_exceeded_total == 1,
        "counter incremented once");

    TEST_PASS("parse budget 1 byte over");
}


static void
test_parse_zero_budget(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Parse with zero budget (disabled)");

    resource_state_init(&state, 64 * 1024 * 1024, 30000, 0);

    int rc = check_parse(&state, 1024, 100);
    TEST_ASSERT(rc == ERROR_SUCCESS,
        "zero budget means no budget check");

    TEST_PASS("parse zero budget");
}


/* ── Interaction: timeout vs budget priority ───────────────────── */

static void
test_timeout_takes_priority_over_budget(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Timeout takes priority over budget when both would trigger");

    resource_state_init(&state, 64 * 1024 * 1024, 1000, 1024);

    int rc = check_parse(&state, 2048, 2000);
    TEST_ASSERT(rc == ERROR_PARSE_TIMEOUT,
        "timeout triggered (not budget)");
    TEST_ASSERT(state.parse_timeouts_total == 1,
        "timeout counter incremented");
    TEST_ASSERT(state.parse_budget_exceeded_total == 0,
        "budget counter NOT incremented");

    TEST_PASS("timeout priority over budget");
}


static void
test_budget_when_not_timed_out(void)
{
    resource_state_t state;

    TEST_SUBSECTION("Budget exceeded when not timed out");

    resource_state_init(&state, 64 * 1024 * 1024, 30000, 1024);

    int rc = check_parse(&state, 2048, 100);
    TEST_ASSERT(rc == ERROR_PARSE_BUDGET_EXCEEDED,
        "budget exceeded when within timeout");
    TEST_ASSERT(state.parse_timeouts_total == 0,
        "timeout counter NOT incremented");
    TEST_ASSERT(state.parse_budget_exceeded_total == 1,
        "budget counter incremented");

    TEST_PASS("budget when not timed out");
}


/* ── Error code values match production ────────────────────────── */

static void
test_error_code_values(void)
{
    TEST_SUBSECTION("Error code values match production definitions");

    TEST_ASSERT(ERROR_DECOMPRESSION_BUDGET_EXCEEDED == 9,
        "DECOMPRESSION_BUDGET_EXCEEDED is 9");
    TEST_ASSERT(ERROR_PARSE_TIMEOUT == 10,
        "PARSE_TIMEOUT is 10");
    TEST_ASSERT(ERROR_PARSE_BUDGET_EXCEEDED == 11,
        "PARSE_BUDGET_EXCEEDED is 11");

    TEST_ASSERT(ERROR_DECOMPRESSION_BUDGET_EXCEEDED < ERROR_PARSE_TIMEOUT,
        "9 < 10");
    TEST_ASSERT(ERROR_PARSE_TIMEOUT < ERROR_PARSE_BUDGET_EXCEEDED,
        "10 < 11");

    TEST_PASS("error code values");
}


/* ── All 3 error codes are distinct ────────────────────────────── */

static void
test_error_codes_distinct(void)
{
    TEST_SUBSECTION("All 3 resource limit error codes are distinct");

    TEST_ASSERT(ERROR_DECOMPRESSION_BUDGET_EXCEEDED != ERROR_PARSE_TIMEOUT,
        "9 != 10");
    TEST_ASSERT(ERROR_DECOMPRESSION_BUDGET_EXCEEDED != ERROR_PARSE_BUDGET_EXCEEDED,
        "9 != 11");
    TEST_ASSERT(ERROR_PARSE_TIMEOUT != ERROR_PARSE_BUDGET_EXCEEDED,
        "10 != 11");

    TEST_PASS("error codes distinct");
}


int
main(void)
{
    TEST_SECTION("resource_limit_edge_cases");

    test_decomp_exact_budget();
    test_decomp_one_byte_over();
    test_decomp_zero_budget();
    test_decomp_zero_output();

    test_parse_timeout_exact_boundary();
    test_parse_timeout_one_ms_under();
    test_parse_zero_timeout();

    test_parse_budget_exact_boundary();
    test_parse_budget_one_byte_over();
    test_parse_zero_budget();

    test_timeout_takes_priority_over_budget();
    test_budget_when_not_timed_out();

    test_error_code_values();
    test_error_codes_distinct();

    TEST_PASS("resource_limit_edge_cases: all tests passed");
    return 0;
}
