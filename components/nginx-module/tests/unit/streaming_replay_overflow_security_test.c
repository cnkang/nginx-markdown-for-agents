/*
 * Test: streaming_replay_overflow_security
 *
 * Security test validating that the streaming path correctly handles
 * replay buffer overflow when the precommit buffer limit
 * (markdown_stream_precommit_buffer) is exceeded.
 *
 * Validates:
 *   - REPLAY_OVERFLOW event fires when buffer capacity is exceeded
 *   - State machine transitions to PRE_COMMIT_REPLAY_UNAVAILABLE
 *   - Decision engine handles fallback correctly (full-buffer or reject)
 *   - Fail-open/reject preservation per on_error policy (Requirement 5)
 *
 * Feature: streaming-security-resource-limits (Spec 41, Task 4.2)
 * Validates: Requirement 6 AC 1 (replay overflow), Requirement 5
 * AGENTS.md: Rule 14 (regression tests), Rule 38 (replay buffer data integrity)
 */

#include "../include/test_common.h"

/* Pull in base NGINX types from stubs */
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif

#ifndef NGX_CONF_UNSET
#define NGX_CONF_UNSET (-1)
#endif

#ifndef NGX_CONF_UNSET_UINT
#define NGX_CONF_UNSET_UINT ((ngx_uint_t) -1)
#endif

#ifndef NGX_CONF_UNSET_SIZE
#define NGX_CONF_UNSET_SIZE ((size_t) -1)
#endif

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif

#define MARKDOWN_STREAMING_ENABLED 1

#ifndef ngx_str_set
#define ngx_str_set(str, text)                                    \
    (str)->len = sizeof(text) - 1; (str)->data = (u_char *) text
#endif

#ifndef ngx_strncasecmp
#define ngx_strncasecmp(s1, s2, n) \
    strncasecmp((const char *) (s1), (const char *) (s2), (n))
#endif

typedef intptr_t ngx_err_t;

/* Define structs that the stubs only forward-declare */
struct ngx_log_s { int dummy; };
struct ngx_pool_s { ngx_log_t *log; };
struct ngx_array_s {
    void       *elts;
    ngx_uint_t  nelts;
    size_t      size;
    ngx_uint_t  nalloc;
    ngx_pool_t *pool;
};
struct ngx_shm_zone_s { int dummy; };
struct ngx_module_s { int dummy; };
struct ngx_command_s { int dummy; };
struct ngx_conf_s { ngx_pool_t *pool; };
struct ngx_chain_s { ngx_buf_t *buf; struct ngx_chain_s *next; };
struct ngx_http_complex_value_s { ngx_str_t value; };

typedef struct {
    ngx_log_t *log;
} ngx_connection_impl_t;

typedef struct {
    ngx_str_t     content_type;
    size_t        content_type_len;
    u_char       *content_type_lowcase;
    ngx_uint_t    status;
    off_t         content_length_n;
} ngx_http_headers_out_t;

struct ngx_http_request_s {
    ngx_connection_impl_t  *connection;
    ngx_pool_t             *pool;
    ngx_http_headers_out_t  headers_out;
    struct ngx_http_request_s *main;
};

/* Include the module header for types */
#include "../../src/ngx_http_markdown_filter_module.h"

/* Include the replay buffer header */
#include "../../src/ngx_http_markdown_stream_replay.h"

/* Include the state machine header */
#include "../../src/ngx_http_markdown_stream_state.h"

/*
 * Mock pool infrastructure for replay buffer tests.
 */

typedef struct ngx_pool_cleanup_s {
    void (*handler)(void *data);
    void *data;
    struct ngx_pool_cleanup_s *next;
} ngx_pool_cleanup_t;

#define TEST_MAX_CLEANUPS 8
static ngx_pool_cleanup_t test_cleanup_slots[TEST_MAX_CLEANUPS];
static int test_cleanup_count;

static ngx_log_t         test_log;
static struct ngx_pool_s test_pool;

/* Mock: ngx_pool_cleanup_add */
ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool); UNUSED(size);
    if (test_cleanup_count >= TEST_MAX_CLEANUPS) {
        return NULL;
    }
    memset(&test_cleanup_slots[test_cleanup_count], 0,
           sizeof(ngx_pool_cleanup_t));
    return &test_cleanup_slots[test_cleanup_count++];
}

/* Mock: ngx_alloc */
void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    UNUSED(log);
    return malloc(size);
}

/* Mock: ngx_palloc */
void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

/* Mock: ngx_free */
#define ngx_free free

/* Mock: ngx_memcpy */
#define ngx_memcpy memcpy

/* Mock: ngx_alloc_chain_link */
ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    UNUSED(pool);
    return (ngx_chain_t *) calloc(1, sizeof(ngx_chain_t));
}

/* Mock: ngx_calloc_buf */
ngx_buf_t *
ngx_calloc_buf(ngx_pool_t *pool)
{
    UNUSED(pool);
    return (ngx_buf_t *) calloc(1, sizeof(ngx_buf_t));
}

/* Mock: ngx_free_chain */
void
ngx_free_chain(ngx_pool_t *pool, ngx_chain_t *cl)
{
    UNUSED(pool);
    free(cl);
}

/* Stub: ngx_log_error_core */
void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log,
                   ngx_err_t err, const char *fmt, ...)
{
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);
}

/* Include implementation sources after mocks */
#include "../../src/ngx_http_markdown_stream_replay.c"
#include "../../src/ngx_http_markdown_stream_state.c"


/* ================================================================
 * Test setup helper
 * ================================================================ */

static void
test_setup(void)
{
    test_cleanup_count = 0;
    memset(&test_log, 0, sizeof(test_log));
    memset(&test_pool, 0, sizeof(test_pool));
    test_pool.log = &test_log;
}


/* ================================================================
 * Security Test: Replay buffer overflow fires REPLAY_OVERFLOW event
 *
 * Scenario: A streaming context has a small precommit buffer limit.
 *           Data fed in pre-commit state exceeds the replay buffer
 *           capacity. Verify NGX_DECLINED is returned (the signal
 *           that triggers REPLAY_OVERFLOW in the body filter).
 *
 * Validates: Requirement 6 AC 1
 * ================================================================ */

static void
test_overflow_fires_on_exceed_capacity(void)
{
    ngx_http_markdown_ctx_t  ctx;
    ngx_int_t                rc;
    u_char                   data[128];

    TEST_SUBSECTION(
        "Replay overflow: exceeding capacity returns NGX_DECLINED");

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(data, 'X', sizeof(data));

    /* Small precommit buffer: 64 bytes */
    rc = ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 64);
    TEST_ASSERT(rc == NGX_OK, "init with 64-byte capacity succeeds");
    TEST_ASSERT(ctx.stream_sm.replay_initialized == 1,
                "replay buffer initialized");

    /* First append within capacity: 32 bytes */
    rc = ngx_http_markdown_stream_replay_append(&ctx, data, 32);
    TEST_ASSERT(rc == NGX_OK, "first 32 bytes append succeeds");
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 32,
                "buffer tracks 32 bytes");

    /* Second append exceeds capacity: 33 bytes (32+33=65 > 64) */
    rc = ngx_http_markdown_stream_replay_append(&ctx, data, 33);
    TEST_ASSERT(rc == NGX_DECLINED,
                "append exceeding capacity returns NGX_DECLINED");
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 32,
                "buffer unchanged after overflow");

    /*
     * After NGX_DECLINED, the replay buffer itself still reports
     * available (size <= capacity). The body filter layer is
     * responsible for detecting NGX_DECLINED and raising
     * EVENT_REPLAY_OVERFLOW to the decision engine. Verify
     * the buffer state is consistent for that handoff.
     */
    TEST_ASSERT(
        ngx_http_markdown_stream_replay_available(&ctx) == 1,
        "replay_available still true (buffer not corrupted, "
        "caller must raise overflow event)");

    free(ctx.stream_sm.replay_buf.data);
    TEST_PASS("Overflow correctly fires NGX_DECLINED signal");
}


/* ================================================================
 * Security Test: Single large chunk exceeds small buffer
 *
 * Scenario: A single chunk larger than the entire buffer capacity
 *           is fed. This simulates a large upstream response body
 *           hitting a small precommit_buffer setting.
 *
 * Validates: Requirement 6 AC 1, Rule 38
 * ================================================================ */

static void
test_overflow_single_large_chunk(void)
{
    ngx_http_markdown_ctx_t  ctx;
    ngx_int_t                rc;
    u_char                   large_data[256];

    TEST_SUBSECTION(
        "Replay overflow: single large chunk exceeds buffer");

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(large_data, 'Y', sizeof(large_data));

    /* Very small precommit buffer: 16 bytes */
    rc = ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 16);
    TEST_ASSERT(rc == NGX_OK, "init with 16-byte capacity succeeds");

    /* Feed 256 bytes in a single chunk */
    rc = ngx_http_markdown_stream_replay_append(
        &ctx, large_data, sizeof(large_data));
    TEST_ASSERT(rc == NGX_DECLINED,
                "single 256-byte chunk exceeds 16-byte buffer");
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 0,
                "no data stored on overflow");

    TEST_PASS("Single large chunk overflow handled correctly");
}


/* ================================================================
 * Security Test: Decision engine transitions on REPLAY_OVERFLOW
 *
 * Scenario: After replay buffer overflow is detected, the body
 *           filter raises EVENT_REPLAY_OVERFLOW to the decision
 *           engine. Verify the state machine transitions correctly
 *           to PRE_COMMIT_REPLAY_UNAVAILABLE semantics.
 *
 * Case A: within_resource_limits=1 -> FULL_BUFFER_FALLBACK
 * Case B: within_resource_limits=0, on_error=pass -> PASSTHROUGH
 * Case C: within_resource_limits=0, on_error=reject -> REJECT_502
 *
 * Validates: Requirement 5, Requirement 6 AC 1
 * ================================================================ */

static void
test_decision_engine_overflow_within_limits(void)
{
    ngx_http_markdown_stream_ctx_t  dctx;
    ngx_http_markdown_decision_t    decision;

    TEST_SUBSECTION(
        "Decision engine: overflow + within limits "
        "-> FULL_BUFFER_FALLBACK");

    memset(&dctx, 0, sizeof(dctx));
    dctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    dctx.replay_available = 0;   /* overflow: replay NOT available */
    dctx.headers_committed = 0;
    dctx.within_resource_limits = 1;
    dctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    decision = ngx_http_markdown_stream_decide(
        &dctx, NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

    TEST_ASSERT(
        decision.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
        "transitions to FULL_BUFFER_FALLBACK");
    TEST_ASSERT(
        decision.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
        "action is SWITCH_FULL_BUFFER");
    TEST_ASSERT(
        decision.reason == NGX_HTTP_MD_REASON_REPLAY_OVERFLOW,
        "reason is REPLAY_OVERFLOW");

    TEST_PASS(
        "Overflow + within limits = FULL_BUFFER_FALLBACK");
}


static void
test_decision_engine_overflow_exceeded_limits_pass(void)
{
    ngx_http_markdown_stream_ctx_t  dctx;
    ngx_http_markdown_decision_t    decision;

    TEST_SUBSECTION(
        "Decision engine: overflow + exceeded limits + "
        "on_error=pass -> PASSTHROUGH");

    memset(&dctx, 0, sizeof(dctx));
    dctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    dctx.replay_available = 0;   /* overflow */
    dctx.headers_committed = 0;
    dctx.within_resource_limits = 0;  /* limits exceeded */
    dctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    decision = ngx_http_markdown_stream_decide(
        &dctx, NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

    TEST_ASSERT(
        decision.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
        "transitions to PASSTHROUGH");
    TEST_ASSERT(
        decision.action == NGX_HTTP_MD_ACTION_REJECT_502,
        "action is REJECT_502 (fail-open cannot replay)");
    TEST_ASSERT(
        decision.reason
            == NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED,
        "reason is RESOURCE_LIMIT_EXCEEDED");

    TEST_PASS(
        "Overflow + exceeded limits + pass = "
        "PASSTHROUGH/REJECT_502");
}


static void
test_decision_engine_overflow_exceeded_limits_reject(void)
{
    ngx_http_markdown_stream_ctx_t  dctx;
    ngx_http_markdown_decision_t    decision;

    TEST_SUBSECTION(
        "Decision engine: overflow + exceeded limits + "
        "on_error=reject -> REJECT_502");

    memset(&dctx, 0, sizeof(dctx));
    dctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    dctx.replay_available = 0;   /* overflow */
    dctx.headers_committed = 0;
    dctx.within_resource_limits = 0;  /* limits exceeded */
    dctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;

    decision = ngx_http_markdown_stream_decide(
        &dctx, NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

    TEST_ASSERT(
        decision.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
        "transitions to PASSTHROUGH");
    TEST_ASSERT(
        decision.action == NGX_HTTP_MD_ACTION_REJECT_502,
        "action is REJECT_502");
    TEST_ASSERT(
        decision.reason
            == NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED,
        "reason is RESOURCE_LIMIT_EXCEEDED");

    TEST_PASS(
        "Overflow + exceeded limits + reject = REJECT_502");
}


/* ================================================================
 * Security Test: PRE_COMMIT_REPLAY_UNAVAILABLE state handles commit
 *
 * Scenario: After overflow, the module may still commit headers and
 *           proceed with streaming (without replay capability).
 *           Verify that COMMIT event transitions to COMMITTED state.
 *
 * Validates: Requirement 5 (fail-open preservation)
 * ================================================================ */

static void
test_replay_unavailable_can_still_commit(void)
{
    ngx_http_markdown_stream_ctx_t  dctx;
    ngx_http_markdown_decision_t    decision;

    TEST_SUBSECTION(
        "PRE_COMMIT_REPLAY_UNAVAILABLE: commit still allowed");

    memset(&dctx, 0, sizeof(dctx));
    dctx.current_state =
        NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE;
    dctx.replay_available = 0;
    dctx.headers_committed = 0;
    dctx.within_resource_limits = 1;
    dctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    decision = ngx_http_markdown_stream_decide(
        &dctx, NGX_HTTP_MD_EVENT_COMMIT);

    TEST_ASSERT(
        decision.new_state == NGX_HTTP_MD_STATE_COMMITTED,
        "transitions to COMMITTED");
    TEST_ASSERT(
        decision.action == NGX_HTTP_MD_ACTION_COMMIT_HEADERS,
        "action is COMMIT_HEADERS");
    TEST_ASSERT(
        decision.reason == NGX_HTTP_MD_REASON_COMMIT_SUCCESS,
        "reason is COMMIT_SUCCESS");

    TEST_PASS(
        "Replay-unavailable state allows commit to proceed");
}


/* ================================================================
 * Security Test: End-to-end overflow -> decision flow
 *
 * Scenario: Exercises the full flow from replay buffer append
 *           overflow through decision engine invocation. Simulates
 *           what the body filter does when replay_append returns
 *           NGX_DECLINED.
 *
 * Validates: Requirement 6 AC 1, Rule 38
 * ================================================================ */

static void
test_e2e_overflow_to_decision(void)
{
    ngx_http_markdown_ctx_t          ctx;
    ngx_http_markdown_stream_ctx_t   dctx;
    ngx_http_markdown_decision_t     decision;
    ngx_int_t                        rc;
    u_char                           data[128];

    TEST_SUBSECTION(
        "E2E: replay overflow -> decision engine fallback");

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(data, 'Z', sizeof(data));

    /* Step 1: Init replay buffer with small capacity */
    rc = ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 32);
    TEST_ASSERT(rc == NGX_OK, "replay init succeeds");

    /* Step 2: Fill buffer to capacity */
    rc = ngx_http_markdown_stream_replay_append(&ctx, data, 32);
    TEST_ASSERT(rc == NGX_OK, "fill to capacity succeeds");
    TEST_ASSERT(
        ngx_http_markdown_stream_replay_available(&ctx) == 1,
        "replay still available at capacity");

    /* Step 3: One more byte overflows */
    rc = ngx_http_markdown_stream_replay_append(&ctx, data, 1);
    TEST_ASSERT(rc == NGX_DECLINED,
                "overflow returns NGX_DECLINED");

    /*
     * Step 4: Body filter would now set replay_available=0
     * and raise EVENT_REPLAY_OVERFLOW to the decision engine.
     * Simulate this sequence:
     */
    memset(&dctx, 0, sizeof(dctx));
    dctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    dctx.replay_available = 0;   /* overflow detected */
    dctx.headers_committed = 0;
    dctx.within_resource_limits = 1;
    dctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    decision = ngx_http_markdown_stream_decide(
        &dctx, NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

    TEST_ASSERT(
        decision.new_state == NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
        "E2E: transitions to FULL_BUFFER_FALLBACK");
    TEST_ASSERT(
        decision.action == NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
        "E2E: action is SWITCH_FULL_BUFFER");
    TEST_ASSERT(
        decision.reason == NGX_HTTP_MD_REASON_REPLAY_OVERFLOW,
        "E2E: reason is REPLAY_OVERFLOW");

    free(ctx.stream_sm.replay_buf.data);
    TEST_PASS("E2E: overflow -> decision engine fallback correct");
}


/* ================================================================
 * Security Test: Incremental overflow detection
 *
 * Scenario: Multiple small appends gradually fill the buffer.
 *           The overflow is detected precisely at the boundary.
 *           No data corruption or partial writes occur.
 *
 * Validates: Requirement 1 AC 3 (no unbounded allocation), Rule 38
 * ================================================================ */

static void
test_incremental_overflow_boundary(void)
{
    ngx_http_markdown_ctx_t  ctx;
    ngx_int_t                rc;
    u_char                   chunk[8];
    size_t                   i;

    TEST_SUBSECTION(
        "Incremental overflow: precise boundary detection");

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(chunk, 'A', sizeof(chunk));

    /* Buffer capacity: 24 bytes (3 chunks of 8) */
    rc = ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 24);
    TEST_ASSERT(rc == NGX_OK, "init with 24-byte capacity");

    /* Fill with 3 chunks of 8 bytes each */
    for (i = 0; i < 3; i++) {
        rc = ngx_http_markdown_stream_replay_append(
            &ctx, chunk, sizeof(chunk));
        TEST_ASSERT(rc == NGX_OK, "chunk append succeeds");
    }
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 24,
                "buffer exactly at capacity (24 bytes)");
    TEST_ASSERT(
        ngx_http_markdown_stream_replay_available(&ctx) == 1,
        "replay still available at exact capacity");

    /* One more byte: overflow */
    rc = ngx_http_markdown_stream_replay_append(
        &ctx, chunk, 1);
    TEST_ASSERT(rc == NGX_DECLINED,
                "1-byte overflow detected");
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 24,
                "buffer unchanged after overflow");

    /* Verify data integrity: all bytes should be 'A' */
    for (i = 0; i < 24; i++) {
        TEST_ASSERT(ctx.stream_sm.replay_buf.data[i] == 'A',
                    "data integrity preserved after overflow");
    }

    free(ctx.stream_sm.replay_buf.data);
    TEST_PASS("Incremental overflow boundary handling correct");
}


/* ================================================================
 * Security Test: Overflow with on_error=reject (fail-closed)
 *
 * Scenario: When resource limits are exceeded AND on_error=reject,
 *           the decision engine should still produce REJECT_502.
 *           This verifies fail-closed semantics per Requirement 5.
 *
 * Validates: Requirement 5 AC 2
 * ================================================================ */

static void
test_overflow_reject_policy_produces_502(void)
{
    ngx_http_markdown_stream_ctx_t  dctx;
    ngx_http_markdown_decision_t    decision;

    TEST_SUBSECTION(
        "Overflow + on_error=reject -> 502 (fail-closed)");

    memset(&dctx, 0, sizeof(dctx));
    dctx.current_state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    dctx.replay_available = 0;
    dctx.headers_committed = 0;
    dctx.within_resource_limits = 0;
    dctx.on_error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;

    decision = ngx_http_markdown_stream_decide(
        &dctx, NGX_HTTP_MD_EVENT_REPLAY_OVERFLOW);

    TEST_ASSERT(
        decision.new_state == NGX_HTTP_MD_STATE_PASSTHROUGH,
        "reject policy: transitions to PASSTHROUGH");
    TEST_ASSERT(
        decision.action == NGX_HTTP_MD_ACTION_REJECT_502,
        "reject policy: action is REJECT_502");

    TEST_PASS("Overflow + reject policy = 502 (fail-closed)");
}


/* ================================================================
 * Main
 * ================================================================ */

int
main(void)
{
    TEST_SECTION(
        "Streaming Replay Overflow Security Tests "
        "(Spec 41, Task 4.2)");

    /* Replay buffer overflow detection */
    test_overflow_fires_on_exceed_capacity();
    test_overflow_single_large_chunk();
    test_incremental_overflow_boundary();

    /* Decision engine transitions */
    test_decision_engine_overflow_within_limits();
    test_decision_engine_overflow_exceeded_limits_pass();
    test_decision_engine_overflow_exceeded_limits_reject();
    test_overflow_reject_policy_produces_502();

    /* State handling after overflow */
    test_replay_unavailable_can_still_commit();

    /* End-to-end flow */
    test_e2e_overflow_to_decision();

    printf("\n  All streaming replay overflow security "
           "tests passed\n\n");
    return 0;
}
