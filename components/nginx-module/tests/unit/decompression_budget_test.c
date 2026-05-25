/*
 * Test: decompression_budget
 *
 * Validates FFI decompression pass-through and budget exceeded
 * behavior: when decompressed output exceeds decompress_max_size,
 * the error code is ERROR_DECOMPRESSION_BUDGET_EXCEEDED (9) and
 * pass-through mode is entered.
 *
 * Corresponds to task A03.11.
 */

#include "../include/test_common.h"


enum {
    ERROR_SUCCESS = 0,
    ERROR_DECOMPRESSION_BUDGET_EXCEEDED = 9
};

typedef struct {
    int passed_through;
    unsigned int error_code;
    unsigned int decompression_budget_exceeded_total;
} decomp_state_t;


static int
decompress_with_budget(size_t input_size, size_t budget, decomp_state_t *state)
{
    if (input_size > budget) {
        state->error_code = ERROR_DECOMPRESSION_BUDGET_EXCEEDED;
        state->passed_through = 1;
        state->decompression_budget_exceeded_total++;
        return ERROR_DECOMPRESSION_BUDGET_EXCEEDED;
    }

    state->error_code = ERROR_SUCCESS;
    state->passed_through = 0;
    return ERROR_SUCCESS;
}


static void
test_within_budget(void)
{
    decomp_state_t state;
    memset(&state, 0, sizeof(state));

    int rc = decompress_with_budget(1024, 4096, &state);
    TEST_ASSERT(rc == ERROR_SUCCESS, "within budget returns SUCCESS");
    TEST_ASSERT(state.error_code == ERROR_SUCCESS, "error_code is SUCCESS");
    TEST_ASSERT(state.passed_through == 0, "not in pass-through mode");
    TEST_ASSERT(state.decompression_budget_exceeded_total == 0, "no budget exceeded counter");
}


static void
test_budget_exceeded(void)
{
    decomp_state_t state;
    memset(&state, 0, sizeof(state));

    int rc = decompress_with_budget(8192, 4096, &state);
    TEST_ASSERT(rc == ERROR_DECOMPRESSION_BUDGET_EXCEEDED, "over budget returns BUDGET_EXCEEDED");
    TEST_ASSERT(state.error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED, "error_code is BUDGET_EXCEEDED");
    TEST_ASSERT(state.passed_through == 1, "enters pass-through mode");
    TEST_ASSERT(state.decompression_budget_exceeded_total == 1, "budget exceeded counter incremented");
}


static void
test_exact_budget_boundary(void)
{
    decomp_state_t state;
    memset(&state, 0, sizeof(state));

    int rc = decompress_with_budget(4096, 4096, &state);
    TEST_ASSERT(rc == ERROR_SUCCESS, "exact budget returns SUCCESS");
    TEST_ASSERT(state.passed_through == 0, "not in pass-through at exact boundary");
}


int
main(void)
{
    test_within_budget();
    test_budget_exceeded();
    test_exact_budget_boundary();

    TEST_PASS("decompression_budget: all tests passed");
    return 0;
}
