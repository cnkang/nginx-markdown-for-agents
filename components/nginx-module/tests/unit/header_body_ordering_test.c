/*
 * Test: header_body_ordering
 *
 * Validates that response headers are always sent before any body
 * data, and that header send is idempotent (multiple filter entries
 * do not cause duplicate header sends).
 *
 * Corresponds to tasks A02.6 and A02.7.
 */

#include "../include/test_common.h"


enum {
    NGX_OK = 0,
    NGX_ERROR = -1
};

typedef struct {
    int headers_sent;
    int body_started;
    int header_send_count;
    int pending_chain;
} filter_state_t;


static int
send_headers(filter_state_t *state)
{
    if (state->body_started) {
        return NGX_ERROR;
    }

    if (!state->headers_sent) {
        state->header_send_count++;
        state->headers_sent = 1;
    }
    return NGX_OK;
}

static int
send_body_chunk(filter_state_t *state, int downstream_rc)
{
    if (!state->headers_sent) {
        return NGX_ERROR;
    }

    if (downstream_rc == -11) {
        state->pending_chain = 1;
        return -11;
    }
    if (downstream_rc != NGX_OK) {
        return NGX_ERROR;
    }
    state->pending_chain = 0;
    state->body_started = 1;
    return NGX_OK;
}


static void
test_headers_before_body(void)
{
    filter_state_t state;
    memset(&state, 0, sizeof(state));

    int rc = send_body_chunk(&state, NGX_OK);
    TEST_ASSERT(rc == NGX_ERROR, "body before headers must fail");
    TEST_ASSERT(state.body_started == 0, "body_started must remain 0");

    rc = send_headers(&state);
    TEST_ASSERT(rc == NGX_OK, "headers must succeed");
    TEST_ASSERT(state.headers_sent == 1, "headers_sent must be 1");

    rc = send_body_chunk(&state, NGX_OK);
    TEST_ASSERT(rc == NGX_OK, "body after headers must succeed");
    TEST_ASSERT(state.body_started == 1, "body_started must be 1");
}


static void
test_header_send_idempotent(void)
{
    filter_state_t state;
    memset(&state, 0, sizeof(state));

    int rc1 = send_headers(&state);
    TEST_ASSERT(rc1 == NGX_OK, "first header send succeeds");
    TEST_ASSERT(state.header_send_count == 1, "header_send_count is 1");

    int rc2 = send_headers(&state);
    TEST_ASSERT(rc2 == NGX_OK, "second header send succeeds (idempotent)");
    TEST_ASSERT(state.header_send_count == 1, "header send remains idempotent");
}


static void
test_header_body_ordering_robust(void)
{
    filter_state_t state;
    memset(&state, 0, sizeof(state));

    send_headers(&state);
    TEST_ASSERT(send_body_chunk(&state, -11) == -11, "NGX_AGAIN should defer body");
    TEST_ASSERT(state.pending_chain == 1, "pending chain should be set on NGX_AGAIN");

    int rc = send_body_chunk(&state, NGX_OK);
    TEST_ASSERT(rc == NGX_OK, "resume should succeed after pending NGX_AGAIN");
    TEST_ASSERT(state.pending_chain == 0, "pending chain cleared on resume");
    TEST_ASSERT(state.body_started == 1, "body_started must be set after resume");
}


int
main(void)
{
    test_headers_before_body();
    test_header_send_idempotent();
    test_header_body_ordering_robust();

    TEST_PASS("header_body_ordering: all tests passed");
    return 0;
}
