/*
 * Test: stream_commit
 *
 * Validates the header commit sequence for the streaming fallback
 * state machine (streaming fallback state machine, header commit).
 *
 * Tests successful commit, double commit, wrong-state commit,
 * NULL parameters, and header mutation correctness.
 */

#include "../include/test_common.h"

/* Pull in base NGINX types from stubs */
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif

#define ngx_log_debug0(level, log, err, fmt) \
    do { (void)(level); (void)(log); (void)(err); (void)(fmt); } while (0)

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

typedef struct ngx_table_elt_s {
    ngx_uint_t    hash;
    ngx_str_t     key;
    ngx_str_t     value;
} ngx_table_elt_t;

typedef struct {
    ngx_str_t          content_type;
    size_t             content_type_len;
    u_char            *content_type_lowcase;
    ngx_str_t          charset;
    ngx_uint_t         status;
    off_t              content_length_n;
    ngx_table_elt_t   *content_length;
} ngx_http_headers_out_t;

struct ngx_http_request_s {
    ngx_connection_impl_t      *connection;
    ngx_pool_t                 *pool;
    ngx_http_headers_out_t      headers_out;
    struct ngx_http_request_s  *main;
};

/* Include the module header for types */
#include "../../src/ngx_http_markdown_filter_module.h"

/* Include the commit header */
#include "../../src/ngx_http_markdown_stream_commit.h"

/*
 * Provide the content type literal that the commit function needs.
 */
u_char ngx_http_markdown_content_type[] =
    NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL;

/* Test infrastructure */
static ngx_log_t             test_log;
static struct ngx_pool_s     test_pool;
static ngx_connection_impl_t test_connection;
static ngx_http_request_t    test_request;
static ngx_table_elt_t       test_content_length_elt;

static int test_vary_accept_called;
static int test_vary_accept_rc;
static int test_set_etag_called;
static int test_set_etag_rc;

/* Mock: ngx_http_markdown_add_vary_accept */
ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    UNUSED(r);
    test_vary_accept_called = 1;
    return test_vary_accept_rc;
}

/* Mock: ngx_http_markdown_set_etag */
ngx_int_t
ngx_http_markdown_set_etag(ngx_http_request_t *r,
                           const u_char *etag_value, size_t etag_len)
{
    UNUSED(r); UNUSED(etag_value); UNUSED(etag_len);
    test_set_etag_called = 1;
    return test_set_etag_rc;
}

/* Mock: ngx_http_markdown_remove_content_encoding */
static int test_remove_content_encoding_called;

void
ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r)
{
    UNUSED(r);
    test_remove_content_encoding_called = 1;
}

/* Mock: ngx_http_markdown_is_authenticated */
ngx_int_t
ngx_http_markdown_is_authenticated(ngx_http_request_t *r,
                                   const ngx_http_markdown_conf_t *conf)
{
    UNUSED(r); UNUSED(conf);
    return 0;
}

/* Mock: ngx_http_markdown_modify_cache_control_for_auth */
ngx_int_t
ngx_http_markdown_modify_cache_control_for_auth(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

/* Stub: ngx_log_error_core */
void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log,
                   ngx_err_t err, const char *fmt, ...)
{
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);
}

/* Include the commit source after mocks */
#include "../../src/ngx_http_markdown_stream_commit.c"


static void test_setup(void)
{
    memset(&test_log, 0, sizeof(test_log));
    memset(&test_pool, 0, sizeof(test_pool));
    memset(&test_connection, 0, sizeof(test_connection));
    memset(&test_request, 0, sizeof(test_request));
    memset(&test_content_length_elt, 0, sizeof(test_content_length_elt));

    test_pool.log = &test_log;
    test_connection.log = &test_log;
    test_request.connection = &test_connection;
    test_request.pool = &test_pool;
    test_request.main = &test_request;
    test_request.headers_out.content_length_n = 12345;
    test_content_length_elt.hash = 1;
    test_request.headers_out.content_length = &test_content_length_elt;

    test_vary_accept_called = 0;
    test_vary_accept_rc = NGX_OK;
    test_set_etag_called = 0;
    test_set_etag_rc = NGX_OK;
}

/* --- Successful commit --- */

static void test_commit_success(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_OK, "commit returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 1,
                "headers_committed flag set");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_COMMITTED,
                "state = COMMITTED");
    TEST_ASSERT(test_vary_accept_called == 1,
                "Vary: Accept was set");
    TEST_ASSERT(test_set_etag_called == 1,
                "ETag was handled");
    TEST_PASS("Successful commit (transitions state, sets flag)");
}

/* --- Double commit --- */

static void test_double_commit(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "double commit returns NGX_ERROR");
    TEST_PASS("Double commit returns NGX_ERROR");
}

/* --- Commit from wrong state --- */

static void test_commit_wrong_state(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_NOT_ELIGIBLE;
    ctx.stream_sm.headers_committed = 0;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR,
                "commit from NOT_ELIGIBLE returns NGX_ERROR");

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PASSTHROUGH;
    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR,
                "commit from PASSTHROUGH returns NGX_ERROR");
    TEST_PASS("Commit from wrong state returns NGX_ERROR");
}

/* --- NULL parameters --- */

static void test_commit_null_params(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;

    rc = ngx_http_markdown_stream_commit_headers(NULL, &ctx, NULL);
    TEST_ASSERT(rc == NGX_ERROR, "NULL request returns NGX_ERROR");

    rc = ngx_http_markdown_stream_commit_headers(&test_request, NULL, NULL);
    TEST_ASSERT(rc == NGX_ERROR, "NULL ctx returns NGX_ERROR");

    TEST_PASS("NULL parameters return NGX_ERROR");
}

/* --- Content-Type set correctly --- */

static void test_content_type_set(void)
{
    ngx_http_markdown_ctx_t ctx;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(test_request.headers_out.content_type.len
                == NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN,
                "Content-Type length correct");
    TEST_ASSERT(test_request.headers_out.content_type_len
                == NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN,
                "content_type_len correct");
    TEST_ASSERT(memcmp(test_request.headers_out.content_type.data,
                       "text/markdown; charset=utf-8",
                       NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN) == 0,
                "Content-Type value correct");
    TEST_PASS("Content-Type set correctly after commit");
}

/* --- Content-Length cleared --- */

static void test_content_length_cleared(void)
{
    ngx_http_markdown_ctx_t ctx;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(test_request.headers_out.content_length_n == -1,
                "content_length_n = -1");
    TEST_ASSERT(test_request.headers_out.content_length == NULL,
                "content_length pointer = NULL");
    TEST_ASSERT(test_content_length_elt.hash == 0,
                "content_length elt hash = 0 (invalidated)");
    TEST_PASS("Content-Length cleared after commit");
}

/* --- Commit from PRE_COMMIT_REPLAY_UNAVAILABLE --- */

static void test_commit_from_replay_unavailable(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state =
        NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE;
    ctx.stream_sm.headers_committed = 0;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_OK, "commit from replay-unavailable ok");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_COMMITTED,
                "state = COMMITTED");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 1,
                "committed flag set");
    TEST_PASS("Commit from PRE_COMMIT_REPLAY_UNAVAILABLE");
}

/* --- Vary header failure aborts commit --- */

static void test_commit_vary_failure_aborts(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    /* Make vary accept fail */
    test_vary_accept_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR,
                "commit fails when vary fails");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 0,
                "headers_committed stays 0 on failure");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PRE_COMMIT,
                "state stays PRE_COMMIT on failure");
    TEST_ASSERT(test_set_etag_called == 0,
                "ETag mutation NOT executed after vary failure");
    TEST_ASSERT(test_request.headers_out.content_length_n == 12345,
                "Content-Length value preserved after vary failure");
    TEST_ASSERT(test_request.headers_out.content_length == &test_content_length_elt,
                "Content-Length pointer preserved after vary failure");
    TEST_ASSERT(test_content_length_elt.hash == 1,
                "Content-Length header still valid after vary failure");
    TEST_PASS("Commit atomicity: vary failure aborts before ETag");
}

/* --- ETag removal failure aborts commit --- */

static void test_commit_etag_failure_aborts(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    /* Make etag removal fail */
    test_set_etag_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR,
                "commit fails when etag fails");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 0,
                "headers_committed stays 0 on failure");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PRE_COMMIT,
                "state stays PRE_COMMIT on failure");
    TEST_ASSERT(test_request.headers_out.content_length_n == 12345,
                "Content-Length value preserved after etag failure");
    TEST_ASSERT(test_request.headers_out.content_length == &test_content_length_elt,
                "Content-Length pointer preserved after etag failure");
    TEST_ASSERT(test_content_length_elt.hash == 1,
                "Content-Length header still valid after etag failure");
    TEST_PASS("Commit atomicity: etag failure aborts commit");
}

/* --- Content-Length absent (no header entry) --- */

static void test_commit_content_length_absent(void)
{
    ngx_http_markdown_ctx_t ctx;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    /* Remove the content_length header entry */
    test_request.headers_out.content_length = NULL;
    test_request.headers_out.content_length_n = -1;

    ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(test_request.headers_out.content_length_n == -1,
                "content_length_n stays -1");
    TEST_ASSERT(test_request.headers_out.content_length == NULL,
                "content_length stays NULL");
    TEST_PASS("Commit handles absent Content-Length header");
}


/* --- Decompression.needed controls Content-Encoding removal --- */

static void test_content_encoding_removed_when_decompression_needed(void)
{
    ngx_http_markdown_ctx_t ctx;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.decompression.needed = 1;

    test_remove_content_encoding_called = 0;

    ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(test_remove_content_encoding_called == 1,
                "Content-Encoding removed when decompression needed");
    TEST_PASS("Content-Encoding removed when decompression needed");
}

static void test_content_encoding_preserved_when_no_decompression(void)
{
    ngx_http_markdown_ctx_t ctx;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.decompression.needed = 0;

    test_remove_content_encoding_called = 0;

    ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(test_remove_content_encoding_called == 0,
                "Content-Encoding NOT removed when no decompression");
    TEST_PASS("Content-Encoding preserved when no decompression");
}


/* --- Vary failure does not expose partial mutations --- */

static void test_commit_vary_failure_no_content_type_leak(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    test_vary_accept_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "commit fails on vary error");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 0,
                "headers_committed stays 0");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PRE_COMMIT,
                "state stays PRE_COMMIT");

    TEST_PASS("Vary failure does not leak content-type mutation");
}


int main(void)
{
    TEST_SECTION("Stream Header Commit (streaming fallback state machine, header commit)");

    test_commit_success();
    test_double_commit();
    test_commit_wrong_state();
    test_commit_null_params();
    test_content_type_set();
    test_content_length_cleared();
    test_commit_from_replay_unavailable();
    test_commit_vary_failure_aborts();
    test_commit_etag_failure_aborts();
    test_commit_content_length_absent();
    test_content_encoding_removed_when_decompression_needed();
    test_content_encoding_preserved_when_no_decompression();
    test_commit_vary_failure_no_content_type_leak();

    printf("\n  All stream commit tests passed\n\n");
    return 0;
}
