import sys

file_path = 'components/nginx-module/tests/unit/streaming_impl_test.c'
with open(file_path, 'r') as f:
    content = f.read()

test_code = r'''
/*
 * Test: fail-open multi-link pending output terminal loss.
 *
 * Regression for a bug where resume_pending() only checks the head of 
 * the pending_output chain for last_buf. In fail-open paths, the 
 * pending chain often starts with a replay prefix (non-terminal) 
 * and ends with the original input (terminal).
 *
 * Lifecycle:
 *   1. Fail-open active, replay buffer present.
 *   2. Input chain is multi-link with terminal tail.
 *   3. Downstream returns NGX_AGAIN on first submission.
 *   4. NULL resume returns NGX_OK.
 *   5. Verify main_terminal_sent is latched.
 */
static void
test_failopen_multilink_pending_terminal_loss(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              in, in_tail;
    ngx_buf_t                in_buf, in_tail_buf;
    ngx_int_t                rc;
    u_char                   in_data[] = "data";
    u_char                   in_tail_data[] = "tail";
    ngx_http_markdown_metrics_t metrics;

    TEST_SUBSECTION(
        "fail-open multi-link pending output terminal latch");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;
    conf.stream.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    /* 1. Setup fail-open state with replay buffer */
    ctx.eligible = 0;
    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.data = (u_char *) "replay";
    ctx.streaming.failopen_replay_buf.size = 6;

    /* 2. Construct multi-link input chain with terminal tail */
    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&in_buf, sizeof(in_buf));
    in.buf = &in_buf;
    in_buf.pos = in_data;
    in_buf.last = in_data + sizeof(in_data) - 1;
    in_buf.last_buf = 0;

    ngx_memzero(&in_tail, sizeof(in_tail));
    ngx_memzero(&in_tail_buf, sizeof(in_tail_buf));
    in_tail.buf = &in_tail_buf;
    in_tail_buf.pos = in_tail_data;
    in_tail_buf.last = in_tail_data + sizeof(in_tail_data) - 1;
    in_tail_buf.last_buf = 1;
    in.next = &in_tail;

    /* 3. Downstream returns NGX_AGAIN on first submission */
    g_next_body_filter_rc = NGX_AGAIN;
    g_next_body_filter_calls = 0;

    rc = ngx_http_markdown_streaming_failopen_passthrough(&r, &ctx, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "failopen_passthrough must return NGX_AGAIN on downstream backpressure");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "pending_output must be retained");
    TEST_ASSERT(g_next_body_filter_calls == 1,
        "downstream must be called once");

    /* 4. NULL resume returns NGX_OK */
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "resume_pending must return NGX_OK on successful drain");
    TEST_ASSERT(g_next_body_filter_calls == 2,
        "downstream must be called exactly twice (initial + NULL resume)");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "pending_output must clear after drain");

    /* 5. Verify main_terminal_sent is latched (This is the core of the bug) */
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 1,
        "main_terminal_sent must be latched after draining terminal multi-link chain");

    TEST_PASS("fail-open multi-link pending terminal latch covered");
}
'''

# Find a safe place to insert the test function.
# We should insert it AFTER the helper functions (reset_globals, etc.)
# and BEFORE the main() function.
# A good marker is the first 'static void test_' that appears in the #else block.

else_idx = content.find('#else  /* MARKDOWN_STREAMING_ENABLED — main test body follows */')
if else_idx == -1:
    print("Error: #else not found", file=sys.stderr)
    sys.exit(1)

# Find the first test function starting with 'static void test_'
first_test_idx = content.find('static void test_', else_idx)

if first_test_idx == -1:
    # Fallback: just find main() and put it before that.
    main_search = ['int\nmain(void)', 'int main(void)']
    main_idx = -1
    for s in main_search:
        main_idx = content.find(s, else_idx)
        if main_idx != -1:
            break
    insert_pos = main_idx - 10 if main_idx != -1 else else_idx + 60
else:
    insert_pos = first_test_idx

new_content = content[:insert_pos] + test_code + '\n' + content[insert_pos:]

# Find main() inside the #else block.
main_search = ['int\nmain(void)', 'int main(void)']
main_idx = -1
for s in main_search:
    main_idx = new_content.find(s, else_idx)
    if main_idx != -1:
        break

if main_idx == -1:
    print("Error: main not found", file=sys.stderr)
    sys.exit(1)

# Find the last test call in main()
last_call = "test_failopen_delivery_abort_does_not_double_count_conversion_failure();"
call_idx = new_content.rfind(last_call)
if call_idx == -1:
    print("Error: last call not found", file=sys.stderr)
    sys.exit(1)

insert_pos_main = call_idx + len(last_call) + 1
final_content = new_content[:insert_pos_main] + '    test_failopen_multilink_pending_terminal_loss();\n' + new_content[insert_pos_main:]

with open(file_path, 'w') as f:
    f.write(final_content)
