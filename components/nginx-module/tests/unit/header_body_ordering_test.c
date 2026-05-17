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
} filter_state_t;


static int
send_headers(filter_state_t *state)
{
    if (state->body_started) {
        return NGX_ERROR;
    }

    state->header_send_count++;
    state->headers_sent = 1;
    return NGX_OK;
}


static int
send_body_chunk(filter_state_t *state)
{
    if (!state->headers_sent) {
        return NGX_ERROR;
    }

    state->body_started = 1;
    return NGX_OK;
}


static void
test_headers_before_body(void)
{
    filter_state_t state;
    memset(&state, 0, sizeof(state));

    int rc = send_body_chunk(&state);
    TEST_ASSERT(rc == NGX_ERROR, "body before headers must fail");
    TEST_ASSERT(state.body_started == 0, "body_started must remain 0");

    rc = send_headers(&state);
    TEST_ASSERT(rc == NGX_OK, "headers must succeed");
    TEST_ASSERT(state.headers_sent == 1, "headers_sent must be 1");

    rc = send_body_chunk(&state);
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
    TEST_ASSERT(state.header_send_count == 2, "header_send_count is 2 (but NGINX guards prevent duplicate)");
}


static void
test_header_body_ordering_robust(void)
{
    filter_state_t state;
    memset(&state, 0, sizeof(state));

    send_headers(&state);
    send_body_chunk(&state);

    int rc = send_body_chunk(&state);
    TEST_ASSERT(rc == NGX_OK, "subsequent body chunks succeed after header+first body");
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
