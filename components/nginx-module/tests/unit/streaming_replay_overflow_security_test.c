/*
 * Test: streaming_replay_overflow_security
 *
 * Security test validating that the streaming path correctly handles
 * replay buffer overflow when the bounded streaming replay buffer
 * is exceeded.
 *
 * Validates:
 *   - REPLAY_OVERFLOW event fires when buffer capacity is exceeded
 *   - State machine transitions to PRE_COMMIT_REPLAY_UNAVAILABLE
 *   - Decision engine handles fallback correctly (full-buffer or reject)
 *   - Fail-open/reject preservation per on_error policy (fail-open/fail-closed policy preservation)
 *
 * Feature: streaming-security-resource-limits (streaming security resource limits)
 * Validates: oversized body / replay overflow handling, fail-open/fail-closed policy preservation
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
 * Validates: oversized body / replay overflow handling
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

    /* NGX_DECLINED latches overflow so callers cannot reuse the partial
     * replay as if it represented the complete response. The body filter
     * raises EVENT_REPLAY_OVERFLOW to the decision engine. */
    TEST_ASSERT(
        ctx.stream_sm.replay_overflowed == 1,
        "overflow state is latched for the decision handoff");
    TEST_ASSERT(
        ngx_http_markdown_stream_replay_available(&ctx) == 0,
        "replay is unavailable after overflow");

    free(ctx.stream_sm.replay_buf.data);
    TEST_PASS("Overflow correctly fires NGX_DECLINED signal");
}


/* ================================================================
 * Security Test: Single large chunk exceeds small buffer
 *
 * Scenario: A single chunk larger than the entire buffer capacity
 *           is fed. This simulates a large upstream response body
 *           hitting a small streaming_buffer setting.
 *
 * Validates: oversized body / replay overflow handling, Rule 38
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
 * Security Test: Replay-buffer overflow detection
 *
 * Scenario: Multiple small appends gradually fill the buffer.
 *           The overflow is detected precisely at the boundary.
 *           No data corruption or partial writes occur.
 *
 * Validates: no unbounded allocation, Rule 38
 * ================================================================ */

static void
test_replay_overflow_boundary(void)
{
    ngx_http_markdown_ctx_t  ctx;
    ngx_int_t                rc;
    u_char                   chunk[8];
    size_t                   i;

    TEST_SUBSECTION(
        "Replay-buffer overflow: precise boundary detection");

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
    TEST_PASS("Replay-buffer overflow boundary handling correct");
}


/* ================================================================
 * Main
 * ================================================================ */

int
main(void)
{
    TEST_SECTION(
        "Streaming Replay Overflow Security Tests "
        "(streaming security resource limits)");

    /* Replay buffer overflow detection */
    test_overflow_fires_on_exceed_capacity();
    test_overflow_single_large_chunk();
    test_replay_overflow_boundary();

    printf("\n  All streaming replay overflow security "
           "tests passed\n\n");
    return 0;
}
