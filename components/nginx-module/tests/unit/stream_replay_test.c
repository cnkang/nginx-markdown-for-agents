/*
 * Test: stream_replay
 *
 * Validates the replay buffer tracking for the streaming fallback
 * state machine (streaming fallback state machine, task 7.2).
 *
 * Tests init, append, overflow, available, and chain operations
 * of ngx_http_markdown_stream_replay_*() functions.
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

/* Include the replay source after mocks are defined */
#include "../../src/ngx_http_markdown_stream_replay.c"


static void test_setup(void)
{
    test_cleanup_count = 0;
    memset(&test_log, 0, sizeof(test_log));
    memset(&test_pool, 0, sizeof(test_pool));
    test_pool.log = &test_log;
}

/* --- Init tests --- */

static void test_init_zero_capacity(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));

    rc = ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 0);

    TEST_ASSERT(rc == NGX_OK, "zero capacity returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.replay_initialized == 0,
                "replay_initialized = 0 (disabled)");
    TEST_PASS("Init with zero capacity (disabled)");
}

static void test_init_valid_capacity(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));

    rc = ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 4096);

    TEST_ASSERT(rc == NGX_OK, "valid capacity returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.replay_initialized == 1,
                "replay_initialized = 1");
    TEST_ASSERT(ctx.stream_sm.replay_capacity == 4096,
                "replay_capacity set correctly");
    TEST_PASS("Init with valid capacity");
}

/* --- Append tests --- */

static void test_append_within_capacity(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;
    u_char data[] = "hello world";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 1024);

    rc = ngx_http_markdown_stream_replay_append(&ctx, data, 11);

    TEST_ASSERT(rc == NGX_OK, "append within capacity returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 11,
                "buffer size = 11");
    TEST_ASSERT(memcmp(ctx.stream_sm.replay_buf.data,
                       "hello world", 11) == 0,
                "data matches");
    free(ctx.stream_sm.replay_buf.data);
    TEST_PASS("Append within capacity");
}

static void test_append_overflow(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;
    u_char data[64];

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(data, 'A', sizeof(data));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 32);

    rc = ngx_http_markdown_stream_replay_append(&ctx, data, 64);

    TEST_ASSERT(rc == NGX_DECLINED, "overflow returns NGX_DECLINED");
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 0,
                "buffer unchanged on overflow");
    TEST_PASS("Append that would overflow returns NGX_DECLINED");
}

static void test_append_accumulate_to_capacity(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;
    u_char chunk[16];

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(chunk, 'B', sizeof(chunk));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 32);

    rc = ngx_http_markdown_stream_replay_append(&ctx, chunk, 16);
    TEST_ASSERT(rc == NGX_OK, "first append ok");

    rc = ngx_http_markdown_stream_replay_append(&ctx, chunk, 16);
    TEST_ASSERT(rc == NGX_OK, "second append ok (at capacity)");
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 32,
                "buffer at capacity");

    /* One more byte should overflow */
    rc = ngx_http_markdown_stream_replay_append(&ctx, chunk, 1);
    TEST_ASSERT(rc == NGX_DECLINED, "exceeding capacity fails");

    free(ctx.stream_sm.replay_buf.data);
    TEST_PASS("Multiple appends accumulating to capacity");
}

/* --- Available tests --- */

static void test_available_within_capacity(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_flag_t avail;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 1024);
    ctx.stream_sm.replay_buf.size = 100;

    avail = ngx_http_markdown_stream_replay_available(&ctx);
    TEST_ASSERT(avail == 1, "available when within capacity");
    TEST_PASS("Available returns true when within capacity");
}

static void test_available_overflowed(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_flag_t avail;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 100);
    /* Simulate overflow by manually setting size > capacity */
    ctx.stream_sm.replay_buf.size = 200;

    avail = ngx_http_markdown_stream_replay_available(&ctx);
    TEST_ASSERT(avail == 0, "not available when overflowed");
    TEST_PASS("Available returns false when overflowed");
}

static void test_available_not_initialized(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_flag_t avail;

    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.replay_initialized = 0;

    avail = ngx_http_markdown_stream_replay_available(&ctx);
    TEST_ASSERT(avail == 0, "not available when not initialized");
    TEST_PASS("Available returns false when not initialized");
}

/* --- Chain tests --- */

static void test_chain_builds_correctly(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_chain_t *chain;
    u_char data[] = "test data";

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 1024);
    ngx_http_markdown_stream_replay_append(&ctx, data, 9);

    chain = ngx_http_markdown_stream_replay_chain(&ctx, &test_pool);

    TEST_ASSERT(chain != NULL, "chain not NULL");
    TEST_ASSERT(chain->buf != NULL, "buf not NULL");
    TEST_ASSERT(chain->buf->pos != ctx.stream_sm.replay_buf.data,
                "pos points to pool-owned replay copy");
    TEST_ASSERT(memcmp(chain->buf->pos, ctx.stream_sm.replay_buf.data, 9) == 0,
                "pool-owned replay copy matches data");
    TEST_ASSERT((size_t)(chain->buf->last - chain->buf->pos) == 9,
                "buf length matches");
    TEST_ASSERT(chain->buf->last_buf == 1, "last_buf set");
    TEST_ASSERT(chain->buf->memory == 1, "memory flag set");
    TEST_ASSERT(chain->next == NULL, "no next link");

    free(chain->buf->pos);
    free(chain->buf);
    free(chain);
    free(ctx.stream_sm.replay_buf.data);
    TEST_PASS("Replay chain builds correctly");
}

static void test_chain_returns_null_when_empty(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_chain_t *chain;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 1024);

    chain = ngx_http_markdown_stream_replay_chain(&ctx, &test_pool);

    TEST_ASSERT(chain == NULL, "chain NULL when empty");
    TEST_PASS("Replay chain returns NULL when empty");
}

/* --- NULL parameter tests --- */

static void test_null_parameters(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;
    ngx_flag_t avail;
    ngx_chain_t *chain;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));

    rc = ngx_http_markdown_stream_replay_init(NULL, &test_pool, 1024);
    TEST_ASSERT(rc == NGX_ERROR, "init NULL ctx -> NGX_ERROR");

    rc = ngx_http_markdown_stream_replay_init(&ctx, NULL, 1024);
    TEST_ASSERT(rc == NGX_ERROR, "init NULL pool -> NGX_ERROR");

    rc = ngx_http_markdown_stream_replay_append(NULL,
            (u_char *) "x", 1);
    TEST_ASSERT(rc == NGX_ERROR, "append NULL ctx -> NGX_ERROR");

    rc = ngx_http_markdown_stream_replay_append(&ctx,
            (u_char *) "x", 1);
    TEST_ASSERT(rc == NGX_ERROR,
                "append not initialized -> NGX_ERROR");

    avail = ngx_http_markdown_stream_replay_available(NULL);
    TEST_ASSERT(avail == 0, "available NULL ctx -> 0");

    chain = ngx_http_markdown_stream_replay_chain(NULL, &test_pool);
    TEST_ASSERT(chain == NULL, "chain NULL ctx -> NULL");

    chain = ngx_http_markdown_stream_replay_chain(&ctx, NULL);
    TEST_ASSERT(chain == NULL, "chain NULL pool -> NULL");

    TEST_PASS("NULL parameter handling");
}

/* --- Append with NULL data and non-zero len --- */

static void test_append_null_data_nonzero_len(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 1024);

    rc = ngx_http_markdown_stream_replay_append(&ctx, NULL, 10);

    TEST_ASSERT(rc == NGX_ERROR,
                "NULL data with non-zero len -> NGX_ERROR");
    TEST_PASS("Append NULL data with non-zero len returns NGX_ERROR");
}

/* --- Append with zero length --- */

static void test_append_zero_length(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 1024);

    rc = ngx_http_markdown_stream_replay_append(&ctx,
            (u_char *) "x", 0);

    TEST_ASSERT(rc == NGX_OK, "zero-length append returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 0,
                "buffer size unchanged");
    TEST_PASS("Zero-length append is a no-op");
}

/* --- Chain not initialized --- */

static void test_chain_not_initialized(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_chain_t *chain;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    /* replay_initialized = 0 by default */

    chain = ngx_http_markdown_stream_replay_chain(&ctx, &test_pool);

    TEST_ASSERT(chain == NULL, "chain not initialized -> NULL");
    TEST_PASS("Chain returns NULL when not initialized");
}

/* --- Available with NULL ctx --- */

static void test_available_null_ctx(void)
{
    ngx_flag_t avail;

    avail = ngx_http_markdown_stream_replay_available(NULL);
    TEST_ASSERT(avail == 0, "available NULL ctx -> 0");
    TEST_PASS("Available returns 0 for NULL ctx");
}

/* --- Cleanup function coverage --- */

static void test_cleanup_with_data(void)
{
    ngx_http_markdown_ctx_t ctx;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 1024);

    /* Append some data to allocate backing store */
    u_char data[] = "test data for cleanup";
    ngx_http_markdown_stream_replay_append(&ctx, data, sizeof(data) - 1);

    TEST_ASSERT(ctx.stream_sm.replay_buf.data != NULL,
                "backing store allocated");

    /* Invoke the cleanup handler directly to cover the cleanup path */
    if (test_cleanup_count > 0 && test_cleanup_slots[0].handler != NULL) {
        test_cleanup_slots[0].handler(test_cleanup_slots[0].data);
    }

    TEST_ASSERT(ctx.stream_sm.replay_buf.data == NULL,
                "backing store freed by cleanup");
    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 0,
                "size reset by cleanup");
    TEST_PASS("Cleanup frees backing store");
}

/* --- Cleanup with NULL buf->data --- */

static void test_cleanup_null_buf_data(void)
{
    ngx_http_markdown_ctx_t ctx;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 1024);

    /* Manually set data to NULL but size/capacity non-zero */
    ctx.stream_sm.replay_buf.data = NULL;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_buf.capacity = 200;

    /* Invoke cleanup - should not crash on NULL data */
    if (test_cleanup_count > 0 && test_cleanup_slots[0].handler != NULL) {
        test_cleanup_slots[0].handler(test_cleanup_slots[0].data);
    }

    TEST_ASSERT(ctx.stream_sm.replay_buf.size == 0,
                "size reset even with NULL data");
    TEST_PASS("Cleanup handles NULL buf->data safely");
}

/* --- Init pool cleanup registration failure --- */

static void test_init_cleanup_registration_failure(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));

    /* Exhaust cleanup slots */
    test_cleanup_count = TEST_MAX_CLEANUPS;

    rc = ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 1024);

    TEST_ASSERT(rc == NGX_ERROR,
                "init cleanup registration failure -> NGX_ERROR");
    TEST_PASS("Init fails when cleanup registration fails");
}

/* --- Overflow detection (size > capacity) --- */

static void test_available_size_exceeds_capacity(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_flag_t avail;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ngx_http_markdown_stream_replay_init(&ctx, &test_pool, 100);
    ctx.stream_sm.replay_buf.size = 150;
    ctx.stream_sm.replay_buf.capacity = 100;

    avail = ngx_http_markdown_stream_replay_available(&ctx);

    TEST_ASSERT(avail == 0,
                "not available when size > capacity");
    TEST_PASS("Available returns 0 when size exceeds capacity");
}


int main(void)
{
    TEST_SECTION("Stream Replay Buffer (Spec 37, Task 7.2)");

    test_init_zero_capacity();
    test_init_valid_capacity();
    test_init_cleanup_registration_failure();
    test_append_within_capacity();
    test_append_overflow();
    test_append_accumulate_to_capacity();
    test_append_null_data_nonzero_len();
    test_append_zero_length();
    test_available_within_capacity();
    test_available_overflowed();
    test_available_not_initialized();
    test_available_null_ctx();
    test_available_size_exceeds_capacity();
    test_chain_builds_correctly();
    test_chain_returns_null_when_empty();
    test_chain_not_initialized();
    test_null_parameters();
    test_cleanup_with_data();
    test_cleanup_null_buf_data();

    printf("\n  All stream replay buffer tests passed\n\n");
    return 0;
}
