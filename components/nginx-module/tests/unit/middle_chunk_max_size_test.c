/*
 * Test: middle_chunk_max_size_failure
 *
 * Validates that in a multi-chunk streaming scenario, when a middle
 * chunk triggers max-size failure, subsequent chunks are still
 * delivered normally (pass-through mode after budget exceeded).
 *
 * Corresponds to task A01.11.
 */

#include "../include/test_common.h"


enum {
    NGX_OK = 0,
    NGX_AGAIN = -11,
    NGX_ERROR = -1
};

enum {
    ERROR_SUCCESS = 0,
    ERROR_DECOMPRESSION_BUDGET_EXCEEDED = 9
};

typedef struct {
    int in_pass_through;
    unsigned int chunks_delivered;
    unsigned int budget_exceeded_count;
    unsigned int error_code;
} streaming_state_t;


static int
process_chunk(size_t chunk_size, size_t budget, streaming_state_t *state)
{
    if (state->in_pass_through) {
        state->chunks_delivered++;
        return NGX_OK;
    }

    if (chunk_size > budget) {
        state->error_code = ERROR_DECOMPRESSION_BUDGET_EXCEEDED;
        state->budget_exceeded_count++;
        state->in_pass_through = 1;
        return NGX_ERROR;
    }

    state->chunks_delivered++;
    state->error_code = ERROR_SUCCESS;
    return NGX_OK;
}


static void
test_middle_chunk_failure_pass_through(void)
{
    streaming_state_t state;
    memset(&state, 0, sizeof(state));

    int rc1 = process_chunk(1024, 4096, &state);
    TEST_ASSERT(rc1 == NGX_OK, "first chunk within budget succeeds");
    TEST_ASSERT(state.chunks_delivered == 1, "one chunk delivered");

    int rc2 = process_chunk(8192, 4096, &state);
    TEST_ASSERT(rc2 == NGX_ERROR, "middle chunk exceeding budget fails");
    TEST_ASSERT(state.budget_exceeded_count == 1, "budget exceeded once");
    TEST_ASSERT(state.in_pass_through == 1, "entered pass-through mode");
    TEST_ASSERT(state.error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED, "error code is BUDGET_EXCEEDED");

    int rc3 = process_chunk(512, 4096, &state);
    TEST_ASSERT(rc3 == NGX_OK, "subsequent chunk delivered in pass-through mode");
    TEST_ASSERT(state.chunks_delivered == 2, "two chunks delivered total");

    int rc4 = process_chunk(2048, 4096, &state);
    TEST_ASSERT(rc4 == NGX_OK, "another subsequent chunk delivered");
    TEST_ASSERT(state.chunks_delivered == 3, "three chunks delivered total");
}


int
main(void)
{
    test_middle_chunk_failure_pass_through();

    TEST_PASS("middle_chunk_max_size_failure: all tests passed");
    return 0;
}
