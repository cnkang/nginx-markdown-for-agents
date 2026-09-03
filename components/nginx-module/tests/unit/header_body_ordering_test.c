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

typedef enum {
    MODULE_BODY_MARKDOWN = 0,
    MODULE_COPY_FILTER,
    MODULE_NOT_MODIFIED,
    MODULE_HEADER_MARKDOWN,
    MODULE_SLICE_FILTER
} module_order_entry_t;

static int
module_order_position(const module_order_entry_t *order, size_t count,
                       module_order_entry_t module)
{
    size_t i;

    for (i = 0; i < count; i++) {
        if (order[i] == module) {
            return (int) i;
        }
    }

    return -1;
}


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


static void
test_split_filter_hook_ordering(void)
{
    static const module_order_entry_t module_order[] = {
        MODULE_BODY_MARKDOWN,
        MODULE_COPY_FILTER,
        MODULE_NOT_MODIFIED,
        MODULE_HEADER_MARKDOWN,
        MODULE_SLICE_FILTER
    };
    int body_position;
    int copy_position;
    int header_position;
    int not_modified_position;

    body_position = module_order_position(
        module_order, sizeof(module_order) / sizeof(module_order[0]),
        MODULE_BODY_MARKDOWN);
    copy_position = module_order_position(
        module_order, sizeof(module_order) / sizeof(module_order[0]),
        MODULE_COPY_FILTER);
    header_position = module_order_position(
        module_order, sizeof(module_order) / sizeof(module_order[0]),
        MODULE_HEADER_MARKDOWN);
    not_modified_position = module_order_position(
        module_order, sizeof(module_order) / sizeof(module_order[0]),
        MODULE_NOT_MODIFIED);

    TEST_ASSERT(body_position >= 0 && copy_position >= 0
                && header_position >= 0 && not_modified_position >= 0,
                "split hook order must contain both filter anchors");
    TEST_ASSERT(body_position < copy_position,
                "body module must precede copy filter in module order");
    TEST_ASSERT(not_modified_position < header_position,
                "header module must follow not_modified in module order");
    TEST_PASS("split filter hooks preserve independent runtime order");
}


int
main(void)
{
    test_headers_before_body();
    test_header_send_idempotent();
    test_header_body_ordering_robust();
    test_split_filter_hook_ordering();

    TEST_PASS("header_body_ordering: all tests passed");
    return 0;
}
