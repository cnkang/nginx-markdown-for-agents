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

#ifndef NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
#define NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL 1
#endif

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

typedef struct ngx_list_part_s ngx_list_part_t;

struct ngx_list_part_s {
    void            *elts;
    ngx_uint_t       nelts;
    ngx_list_part_t *next;
};

typedef struct {
    ngx_list_part_t  part;
    size_t           size;
    ngx_uint_t       nalloc;
    void            *pool;
} ngx_list_t;

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
    ngx_table_elt_t   *etag;
    ngx_list_t         headers;
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
static int test_is_authenticated;
static int test_auth_cache_control_called;
static int test_auth_cache_control_rc;

/* Forward declarations for stubs defined later */
ngx_table_elt_t *ngx_list_push(ngx_list_t *list);
void *ngx_pnalloc(ngx_pool_t *pool, size_t size);
#ifndef ngx_memcpy
#define ngx_memcpy(dst, src, n)  memcpy((dst), (src), (n))
#endif

/* Helper: find a header by name across all ngx_list_part_t parts.
 * Returns the first matching entry with hash != 0, or NULL.
 */
static ngx_table_elt_t *
test_find_header_in_list(ngx_list_t *list, const char *name, size_t name_len)
{
    ngx_list_part_t *part;
    ngx_table_elt_t *elts;
    ngx_uint_t       i;

    part = &list->part;
    while (part != NULL) {
        elts = (ngx_table_elt_t *) part->elts;
        for (i = 0; i < part->nelts; i++) {
            if (elts[i].hash != 0
                && elts[i].key.len == name_len
                && ngx_strncasecmp(elts[i].key.data,
                                   (const u_char *) name, name_len) == 0)
            {
                return &elts[i];
            }
        }
        part = part->next;
    }
    return NULL;
}

/* Helper: iterate all entries matching a header name across all parts.
 * Calls callback for each matching entry with hash != 0.
 */
typedef void (*test_header_iter_cb)(ngx_table_elt_t *h, void *ctx);

static void
test_iter_header_in_list(ngx_list_t *list, const char *name, size_t name_len,
                         test_header_iter_cb cb, void *cb_ctx)
{
    ngx_list_part_t *part;
    ngx_table_elt_t *elts;
    ngx_uint_t       i;

    part = &list->part;
    while (part != NULL) {
        elts = (ngx_table_elt_t *) part->elts;
        for (i = 0; i < part->nelts; i++) {
            if (elts[i].hash != 0
                && elts[i].key.len == name_len
                && ngx_strncasecmp(elts[i].key.data,
                                   (const u_char *) name, name_len) == 0)
            {
                cb(&elts[i], cb_ctx);
            }
        }
        part = part->next;
    }
}

/* Mock: ngx_http_markdown_add_vary_accept
 *
 * Simulates real behavior: if no Vary header exists, pushes a new
 * "Vary: Accept" entry; if Vary exists without "Accept", appends
 * ", Accept" to the value.  This makes rollback tests meaningful.
 */
ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    ngx_table_elt_t  *vary;
    ngx_table_elt_t  *h;
    u_char           *p;
    size_t            len;
    const char        suffix[] = ", Accept";

    test_vary_accept_called = 1;
    if (test_vary_accept_rc != NGX_OK) {
        return test_vary_accept_rc;
    }

    vary = test_find_header_in_list(&r->headers_out.headers, "Vary", 4);

    if (vary == NULL) {
        h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }
        h->hash = 1;
        h->key.data = (u_char *) "Vary";
        h->key.len = 4;
        h->value.data = (u_char *) "Accept";
        h->value.len = 6;
        return NGX_OK;
    }

    len = vary->value.len + sizeof(suffix) - 1;
    p = ngx_pnalloc(r->pool, len);
    if (p == NULL) {
        return NGX_ERROR;
    }
    ngx_memcpy(p, vary->value.data, vary->value.len);
    ngx_memcpy(p + vary->value.len, suffix, sizeof(suffix) - 1);
    vary->value.data = p;
    vary->value.len = len;
    return NGX_OK;
}

/* Mock: ngx_http_markdown_set_etag
 *
 * Simulates real behavior: when etag is NULL/0, invalidates all
 * existing ETag entries (hash=0) and clears the typed pointer.
 * This makes rollback tests meaningful.
 */

static void
test_etag_invalidate_cb(ngx_table_elt_t *h, void *ctx)
{
    UNUSED(ctx);
    h->hash = 0;
}

ngx_int_t
ngx_http_markdown_set_etag(ngx_http_request_t *r,
                           const u_char *etag_value, size_t etag_len)
{
    test_set_etag_called = 1;
    if (test_set_etag_rc != NGX_OK) {
        return test_set_etag_rc;
    }

    if (etag_value != NULL && etag_len > 0) {
        return NGX_OK;
    }

    test_iter_header_in_list(&r->headers_out.headers,
                             "ETag", 4,
                             test_etag_invalidate_cb, NULL);

    r->headers_out.etag = NULL;
    return NGX_OK;
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
ngx_http_markdown_is_authenticated(const ngx_http_request_t *r,
                                   const ngx_http_markdown_conf_t *conf)
{
    UNUSED(r); UNUSED(conf);
    return test_is_authenticated;
}

/* Mock: ngx_http_markdown_modify_cache_control_for_auth
 *
 * Simulates real behavior: modifies the existing Cache-Control
 * header value (e.g. appends ", private").  This makes rollback
 * tests meaningful.
 */
ngx_int_t
ngx_http_markdown_modify_cache_control_for_auth(ngx_http_request_t *r)
{
    ngx_table_elt_t  *cc;
    u_char           *p;
    size_t            len;
    const char        suffix[] = ", private";

    test_auth_cache_control_called = 1;
    if (test_auth_cache_control_rc != NGX_OK) {
        return test_auth_cache_control_rc;
    }

    cc = test_find_header_in_list(&r->headers_out.headers,
                                  "Cache-Control", 13);

    if (cc == NULL) {
        cc = ngx_list_push(&r->headers_out.headers);
        if (cc == NULL) {
            return NGX_ERROR;
        }
        cc->hash = 1;
        cc->key.data = (u_char *) "Cache-Control";
        cc->key.len = 13;
        cc->value.data = (u_char *) "private";
        cc->value.len = 7;
        return NGX_OK;
    }

    len = cc->value.len + sizeof(suffix) - 1;
    p = ngx_pnalloc(r->pool, len);
    if (p == NULL) {
        return NGX_ERROR;
    }
    ngx_memcpy(p, cc->value.data, cc->value.len);
    ngx_memcpy(p + cc->value.len, suffix, sizeof(suffix) - 1);
    cc->value.data = p;
    cc->value.len = len;
    return NGX_OK;
}

/* Stub: ngx_log_error_core */
void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log,
                   ngx_err_t err, const char *fmt, ...)
{
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);
}

/* Stub: ngx_list_push
 *
 * Supports cross-part push: when part1 is full and part2 is linked
 * (part->next != NULL), pushes into the next part.  This makes
 * multipart rollback tests realistic.
 */
ngx_table_elt_t *
ngx_list_push(ngx_list_t *list)
{
    ngx_list_part_t *part;
    ngx_table_elt_t *elts;

    part = &list->part;
    while (part != NULL) {
        if (part->nelts < list->nalloc) {
            elts = (ngx_table_elt_t *) part->elts;
            return &elts[part->nelts++];
        }
        if (part->next != NULL) {
            part = part->next;
            continue;
        }
        return NULL;
    }
    return NULL;
}

/* Stub: ngx_pnalloc */
void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

/* Include the commit source after mocks */
#include "../../src/ngx_http_markdown_stream_commit.c"


static ngx_table_elt_t test_headers_storage[32];

static void test_setup(void)
{
    memset(&test_log, 0, sizeof(test_log));
    memset(&test_pool, 0, sizeof(test_pool));
    memset(&test_connection, 0, sizeof(test_connection));
    memset(&test_request, 0, sizeof(test_request));
    memset(&test_content_length_elt, 0, sizeof(test_content_length_elt));
    memset(test_headers_storage, 0, sizeof(test_headers_storage));

    test_pool.log = &test_log;
    test_connection.log = &test_log;
    test_request.connection = &test_connection;
    test_request.pool = &test_pool;
    test_request.main = &test_request;
    test_request.headers_out.content_length_n = 12345;
    test_content_length_elt.hash = 1;
    test_request.headers_out.content_length = &test_content_length_elt;

    /* Initialize headers list with capacity 32 */
    test_request.headers_out.headers.part.elts = test_headers_storage;
    test_request.headers_out.headers.part.nelts = 0;
    test_request.headers_out.headers.part.next = NULL;
    test_request.headers_out.headers.size = sizeof(ngx_table_elt_t);
    test_request.headers_out.headers.nalloc = 32;
    test_request.headers_out.headers.pool = NULL;
    test_request.headers_out.etag = NULL;

    test_vary_accept_called = 0;
    test_vary_accept_rc = NGX_OK;
    test_set_etag_called = 0;
    test_set_etag_rc = NGX_OK;
    test_is_authenticated = 0;
    test_auth_cache_control_called = 0;
    test_auth_cache_control_rc = NGX_OK;
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
    TEST_ASSERT(test_request.headers_out.content_type.data == NULL,
                "content_type not modified on vary failure");
    TEST_ASSERT(test_request.headers_out.content_type.len == 0,
                "content_type_len not modified on vary failure");
    TEST_ASSERT(test_request.headers_out.content_length_n == 12345,
                "content_length_n not modified on vary failure");

    TEST_PASS("Vary failure does not leak content-type mutation");
}


/* --- Auth Cache-Control failure aborts commit --- */

static void test_commit_auth_cache_control_failure_aborts(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    test_is_authenticated = 1;
    test_auth_cache_control_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(
        &test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_ERROR,
                "commit fails when auth Cache-Control fails");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 0,
                "headers_committed stays 0 on auth CC failure");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PRE_COMMIT,
                "state stays PRE_COMMIT on auth CC failure");
    TEST_ASSERT(test_request.headers_out.content_type.data == NULL,
                "Content-Type not set on auth CC failure");
    TEST_ASSERT(test_request.headers_out.content_type.len == 0,
                "Content-Type len not set on auth CC failure");
    TEST_ASSERT(test_request.headers_out.content_length_n == 12345,
                "Content-Length value preserved on auth CC failure");
    TEST_ASSERT(test_request.headers_out.content_length
                == &test_content_length_elt,
                "Content-Length pointer preserved on auth CC failure");
    TEST_ASSERT(test_content_length_elt.hash == 1,
                "Content-Length header still valid on auth CC failure");
    TEST_ASSERT(test_auth_cache_control_called == 1,
                "auth Cache-Control was called");
    TEST_ASSERT(test_vary_accept_called == 1,
                "Vary was set before auth CC failure");
    TEST_ASSERT(test_set_etag_called == 1,
                "ETag was removed before auth CC failure");

    TEST_PASS("Auth Cache-Control failure aborts commit (Phase 1 semantics)");
}


/* --- Auth Cache-Control success with authenticated request --- */

static void test_commit_auth_cache_control_success(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    test_is_authenticated = 1;
    test_auth_cache_control_rc = NGX_OK;

    rc = ngx_http_markdown_stream_commit_headers(
        &test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_OK,
                "commit succeeds when auth Cache-Control succeeds");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 1,
                "headers_committed set on auth CC success");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_COMMITTED,
                "state = COMMITTED on auth CC success");
    TEST_ASSERT(test_auth_cache_control_called == 1,
                "auth Cache-Control was called");

    TEST_PASS("Auth Cache-Control success with authenticated request");
}


/* Helper: push a header into the test request headers list */
static ngx_table_elt_t *
test_push_header(const char *key, const char *value)
{
    ngx_table_elt_t *h = ngx_list_push(&test_request.headers_out.headers);
    TEST_ASSERT(h != NULL, "header list push failed");
    h->hash = 1;
    h->key.data = (u_char *) key;
    h->key.len = strlen(key);
    h->value.data = (u_char *) value;
    h->value.len = strlen(value);
    return h;
}

/* Helper: find a header in the test request headers list */
static ngx_table_elt_t *
test_find_header(const char *key)
{
    ngx_table_elt_t *elts = (ngx_table_elt_t *) test_request.headers_out.headers.part.elts;
    size_t key_len = strlen(key);
    for (ngx_uint_t i = 0; i < test_request.headers_out.headers.part.nelts; i++) {
        if (elts[i].hash != 0 &&
            elts[i].key.len == key_len &&
            ngx_strncasecmp(elts[i].key.data, (const u_char *) key, key_len) == 0) {
            return &elts[i];
        }
    }
    return NULL;
}

/* --- Rollback: Vary failure restores pre-existing Vary header --- */

static void test_rollback_vary_failure_restores_vary(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *orig_vary;
    ngx_table_elt_t *found_vary;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    /* Pre-existing Vary: Cookie header */
    orig_vary = test_push_header("Vary", "Cookie");
    TEST_ASSERT(orig_vary != NULL, "pre-existing Vary pushed");

    /* Make vary accept fail */
    test_vary_accept_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "commit fails on vary error");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 0,
                "headers_committed stays 0");

    /* The original Vary header must still be present and unmodified */
    found_vary = test_find_header("Vary");
    TEST_ASSERT(found_vary != NULL, "Vary header still present after rollback");
    TEST_ASSERT(found_vary == orig_vary,
                "Vary header pointer unchanged after rollback");
    TEST_ASSERT(found_vary->hash == 1,
                "Vary header hash restored to 1");
    TEST_ASSERT(found_vary->value.len == 6,
                "Vary header value length restored");
    TEST_ASSERT(memcmp(found_vary->value.data, "Cookie", 6) == 0,
                "Vary header value restored to Cookie");

    TEST_PASS("Rollback: Vary failure restores pre-existing Vary header");
}

/* --- Rollback: ETag failure restores pre-existing ETag header --- */

static void test_rollback_etag_failure_restores_etag(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *orig_etag;
    ngx_table_elt_t *found_etag;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    /* Pre-existing ETag header */
    orig_etag = test_push_header("ETag", "\"abc123\"");
    test_request.headers_out.etag = orig_etag;

    /* Make etag removal fail */
    test_set_etag_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "commit fails on etag error");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 0,
                "headers_committed stays 0");

    /* The original ETag header must still be present and unmodified */
    found_etag = test_find_header("ETag");
    TEST_ASSERT(found_etag != NULL, "ETag header still present after rollback");
    TEST_ASSERT(found_etag == orig_etag,
                "ETag header pointer unchanged after rollback");
    TEST_ASSERT(found_etag->hash == 1,
                "ETag header hash restored to 1");
    TEST_ASSERT(test_request.headers_out.etag == orig_etag,
                "typed ETag pointer restored");

    TEST_PASS("Rollback: ETag failure restores pre-existing ETag header");
}

/* --- Rollback: Cache-Control failure restores pre-existing CC header --- */

static void test_rollback_cc_failure_restores_cc(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_table_elt_t *orig_cc;
    ngx_table_elt_t *found_cc;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    /* Pre-existing Cache-Control: public header */
    orig_cc = test_push_header("Cache-Control", "public");

    test_is_authenticated = 1;
    test_auth_cache_control_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(
        &test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_ERROR, "commit fails on auth CC error");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 0,
                "headers_committed stays 0");

    /* The original Cache-Control header must still be present and unmodified */
    found_cc = test_find_header("Cache-Control");
    TEST_ASSERT(found_cc != NULL, "CC header still present after rollback");
    TEST_ASSERT(found_cc == orig_cc,
                "CC header pointer unchanged after rollback");
    TEST_ASSERT(found_cc->hash == 1,
                "CC header hash restored to 1");
    TEST_ASSERT(found_cc->value.len == 6,
                "CC header value length restored");
    TEST_ASSERT(memcmp(found_cc->value.data, "public", 6) == 0,
                "CC header value restored to public");

    TEST_PASS("Rollback: CC failure restores pre-existing Cache-Control header");
}

/* --- Rollback: ETag failure also rolls back Vary mutation --- */

static void test_rollback_etag_failure_rolls_back_vary(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *orig_vary;
    ngx_table_elt_t *found_vary;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    /* Pre-existing Vary: Cookie header */
    orig_vary = test_push_header("Vary", "Cookie");

    /* Vary succeeds, but ETag fails */
    test_vary_accept_rc = NGX_OK;
    test_set_etag_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "commit fails on etag error");
    TEST_ASSERT(test_vary_accept_called == 1, "Vary was called");
    TEST_ASSERT(test_set_etag_called == 1, "ETag was called");

    /* The original Vary header must be restored despite Vary mutation succeeding */
    found_vary = test_find_header("Vary");
    TEST_ASSERT(found_vary != NULL, "Vary header still present after rollback");
    TEST_ASSERT(found_vary == orig_vary,
                "Vary header pointer unchanged after rollback");
    TEST_ASSERT(found_vary->hash == 1,
                "Vary header hash restored to 1");
    TEST_ASSERT(found_vary->value.len == 6,
                "Vary header value length restored");
    TEST_ASSERT(memcmp(found_vary->value.data, "Cookie", 6) == 0,
                "Vary header value restored to Cookie");

    TEST_PASS("Rollback: ETag failure rolls back prior Vary mutation");
}

/* --- Rollback: typed ETag pointer restored after Vary failure --- */

static void test_rollback_restores_typed_etag_pointer(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *orig_etag;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    /* Pre-existing typed ETag pointer */
    orig_etag = test_push_header("ETag", "\"v1\"");
    test_request.headers_out.etag = orig_etag;

    /* Make vary fail (Phase 1 step 1) */
    test_vary_accept_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "commit fails on vary error");
    TEST_ASSERT(test_request.headers_out.etag == orig_etag,
                "typed ETag pointer restored after rollback");

    TEST_PASS("Rollback: typed ETag pointer restored after Vary failure");
}

/* --- Snapshot overflow fails before any Phase 1 mutation --- */

static void
test_snapshot_overflow_for_header(const char *name, const char *value)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *headers[NGX_HTTP_MARKDOWN_COMMIT_SNAPSHOT_MAX + 1];
    ngx_table_elt_t *original_etag;
    ngx_str_t values[NGX_HTTP_MARKDOWN_COMMIT_SNAPSHOT_MAX + 1];
    ngx_str_t original_content_type;
    ngx_uint_t hashes[NGX_HTTP_MARKDOWN_COMMIT_SNAPSHOT_MAX + 1];
    ngx_uint_t original_nelts;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    test_request.headers_out.content_type.data = (u_char *) "text/html";
    test_request.headers_out.content_type.len = sizeof("text/html") - 1;
    original_content_type = test_request.headers_out.content_type;

    for (ngx_uint_t i = 0;
         i < NGX_HTTP_MARKDOWN_COMMIT_SNAPSHOT_MAX + 1;
         i++)
    {
        headers[i] = test_push_header(name, value);
        values[i] = headers[i]->value;
        hashes[i] = headers[i]->hash;
    }

    if (strcmp(name, "ETag") == 0) {
        test_request.headers_out.etag = headers[0];
    }
    original_etag = test_request.headers_out.etag;
    original_nelts = test_request.headers_out.headers.part.nelts;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR,
                "snapshot overflow returns NGX_ERROR");
    TEST_ASSERT(test_vary_accept_called == 0,
                "Vary mutation not called after snapshot overflow");
    TEST_ASSERT(test_set_etag_called == 0,
                "ETag mutation not called after snapshot overflow");
    TEST_ASSERT(test_auth_cache_control_called == 0,
                "Cache-Control mutation not called after snapshot overflow");
    TEST_ASSERT(ctx.stream_sm.headers_committed == 0,
                "headers_committed remains clear after snapshot overflow");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PRE_COMMIT,
                "state remains PRE_COMMIT after snapshot overflow");
    TEST_ASSERT(test_request.headers_out.content_type.data
                == original_content_type.data,
                "Content-Type pointer remains unchanged");
    TEST_ASSERT(test_request.headers_out.content_type.len
                == original_content_type.len,
                "Content-Type length remains unchanged");
    TEST_ASSERT(test_request.headers_out.content_length_n == 12345,
                "Content-Length remains unchanged after snapshot overflow");
    TEST_ASSERT(test_request.headers_out.content_length
                == &test_content_length_elt,
                "Content-Length pointer remains unchanged");
    TEST_ASSERT(test_request.headers_out.headers.part.nelts == original_nelts,
                "snapshot overflow does not push a new header");
    TEST_ASSERT(test_request.headers_out.etag == original_etag,
                "typed ETag pointer remains unchanged");

    for (ngx_uint_t i = 0;
         i < NGX_HTTP_MARKDOWN_COMMIT_SNAPSHOT_MAX + 1;
         i++)
    {
        TEST_ASSERT(headers[i]->hash == hashes[i],
                    "original header hash remains unchanged");
        TEST_ASSERT(headers[i]->value.data == values[i].data,
                    "original header value pointer remains unchanged");
        TEST_ASSERT(headers[i]->value.len == values[i].len,
                    "original header value length remains unchanged");
    }
}

static void test_snapshot_overflow_fails_before_mutation(void)
{
    test_snapshot_overflow_for_header("Vary", "Cookie");
    test_snapshot_overflow_for_header("ETag", "\"original\"");
    test_snapshot_overflow_for_header("Cache-Control", "public");

    TEST_PASS("All snapshot overflows fail before Phase 1 mutation");
}

/* --- Multipart header list rollback regression tests (Rule 39 / Rule 40) --- */

static ngx_table_elt_t  mp_part1_storage[8];
static ngx_table_elt_t  mp_part2_storage[8];
static ngx_list_part_t  mp_part2;

static void test_setup_multipart(void)
{
    memset(&test_log, 0, sizeof(test_log));
    memset(&test_pool, 0, sizeof(test_pool));
    memset(&test_connection, 0, sizeof(test_connection));
    memset(&test_request, 0, sizeof(test_request));
    memset(&test_content_length_elt, 0, sizeof(test_content_length_elt));
    memset(mp_part1_storage, 0, sizeof(mp_part1_storage));
    memset(mp_part2_storage, 0, sizeof(mp_part2_storage));
    memset(&mp_part2, 0, sizeof(mp_part2));

    test_pool.log = &test_log;
    test_connection.log = &test_log;
    test_request.connection = &test_connection;
    test_request.pool = &test_pool;
    test_request.main = &test_request;
    test_request.headers_out.content_length_n = 12345;
    test_content_length_elt.hash = 1;
    test_request.headers_out.content_length = &test_content_length_elt;

    test_request.headers_out.headers.part.elts = mp_part1_storage;
    test_request.headers_out.headers.part.nelts = 0;
    test_request.headers_out.headers.part.next = NULL;
    test_request.headers_out.headers.size = sizeof(ngx_table_elt_t);
    test_request.headers_out.headers.nalloc = 8;
    test_request.headers_out.headers.pool = NULL;
    test_request.headers_out.etag = NULL;

    mp_part2.elts = mp_part2_storage;
    mp_part2.nelts = 0;
    mp_part2.next = NULL;

    test_vary_accept_called = 0;
    test_vary_accept_rc = NGX_OK;
    test_set_etag_called = 0;
    test_set_etag_rc = NGX_OK;
    test_is_authenticated = 0;
    test_auth_cache_control_called = 0;
    test_auth_cache_control_rc = NGX_OK;
}

static void mp_link_part2(void)
{
    test_request.headers_out.headers.part.next = &mp_part2;
}

static ngx_table_elt_t *
mp_push_part1(const char *key, const char *value)
{
    ngx_uint_t idx = test_request.headers_out.headers.part.nelts;
    TEST_ASSERT(idx < 8, "part1 not full");
    ngx_table_elt_t *h = &mp_part1_storage[idx];
    h->hash = 1;
    h->key.data = (u_char *) key;
    h->key.len = strlen(key);
    h->value.data = (u_char *) value;
    h->value.len = strlen(value);
    test_request.headers_out.headers.part.nelts++;
    return h;
}

static ngx_table_elt_t *
mp_push_part2(const char *key, const char *value)
{
    ngx_uint_t idx = mp_part2.nelts;
    TEST_ASSERT(idx < 8, "part2 not full");
    ngx_table_elt_t *h = &mp_part2_storage[idx];
    h->hash = 1;
    h->key.data = (u_char *) key;
    h->key.len = strlen(key);
    h->value.data = (u_char *) value;
    h->value.len = strlen(value);
    mp_part2.nelts++;
    return h;
}



static void test_multipart_rollback_vary_in_part2(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *vary_in_p2;
    ngx_table_elt_t *unrelated_in_p1;
    ngx_table_elt_t *unrelated_in_p2;
    ngx_str_t orig_vary_value;
    ngx_str_t orig_unrelated_p1_value;
    ngx_str_t orig_unrelated_p2_value;
    ngx_int_t rc;

    test_setup_multipart();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    unrelated_in_p1 = mp_push_part1("X-App", "v1");
    orig_unrelated_p1_value = unrelated_in_p1->value;

    mp_link_part2();

    vary_in_p2 = mp_push_part2("Vary", "Cookie");
    orig_vary_value = vary_in_p2->value;

    unrelated_in_p2 = mp_push_part2("X-Trace", "abc");
    orig_unrelated_p2_value = unrelated_in_p2->value;

    test_vary_accept_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "commit returns NGX_ERROR on Vary failure");
    TEST_ASSERT(vary_in_p2->hash == 1, "Vary hash restored in part2");
    TEST_ASSERT(vary_in_p2->value.data == orig_vary_value.data,
                "Vary value restored in part2");
    TEST_ASSERT(vary_in_p2->value.len == orig_vary_value.len,
                "Vary value len restored in part2");
    TEST_ASSERT(unrelated_in_p1->hash == 1, "unrelated header in part1 untouched");
    TEST_ASSERT(unrelated_in_p1->value.data == orig_unrelated_p1_value.data,
                "unrelated part1 value untouched");
    TEST_ASSERT(unrelated_in_p2->hash == 1, "unrelated header in part2 untouched");
    TEST_ASSERT(unrelated_in_p2->value.data == orig_unrelated_p2_value.data,
                "unrelated part2 value untouched");

    TEST_PASS("Multipart rollback: Vary in part2 restored, unrelated headers untouched");
}

static void test_multipart_rollback_target_in_p2_non_target_in_p2(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *vary_in_p2;
    ngx_table_elt_t *etag_in_p2;
    ngx_str_t orig_vary_value;
    ngx_str_t orig_etag_value;
    ngx_int_t rc;

    test_setup_multipart();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    mp_push_part1("X-App", "v1");
    mp_push_part1("X-Req-Id", "42");

    mp_link_part2();

    vary_in_p2 = mp_push_part2("Vary", "Cookie");
    orig_vary_value = vary_in_p2->value;

    etag_in_p2 = mp_push_part2("ETag", "\"orig\"");
    orig_etag_value = etag_in_p2->value;
    test_request.headers_out.etag = etag_in_p2;

    mp_push_part2("X-Trace", "abc");

    test_vary_accept_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "commit returns NGX_ERROR on Vary failure");
    TEST_ASSERT(vary_in_p2->hash == 1, "Vary hash restored in part2");
    TEST_ASSERT(vary_in_p2->value.data == orig_vary_value.data,
                "Vary value restored in part2");
    TEST_ASSERT(etag_in_p2->hash == 1, "ETag hash preserved (not mutated yet)");
    TEST_ASSERT(etag_in_p2->value.data == orig_etag_value.data,
                "ETag value preserved (not mutated yet)");
    TEST_ASSERT(test_request.headers_out.etag == etag_in_p2,
                "typed ETag pointer preserved");

    TEST_PASS("Multipart rollback: target and non-target headers in part2");
}

static void test_multipart_rollback_etag_failure_restores_vary_in_p1_etag_in_p2(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *vary_in_p1;
    ngx_table_elt_t *etag_in_p2;
    ngx_str_t orig_vary_value;
    ngx_str_t orig_etag_value;
    ngx_int_t rc;

    test_setup_multipart();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    vary_in_p1 = mp_push_part1("Vary", "Cookie");
    orig_vary_value = vary_in_p1->value;

    mp_link_part2();

    etag_in_p2 = mp_push_part2("ETag", "\"orig\"");
    orig_etag_value = etag_in_p2->value;
    test_request.headers_out.etag = etag_in_p2;

    mp_push_part2("X-Trace", "abc");

    test_set_etag_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "commit returns NGX_ERROR on ETag failure");
    TEST_ASSERT(vary_in_p1->hash == 1, "Vary hash restored in part1");
    TEST_ASSERT(vary_in_p1->value.data == orig_vary_value.data,
                "Vary value restored in part1 after cross-header rollback");
    TEST_ASSERT(etag_in_p2->hash == 1, "ETag hash restored in part2");
    TEST_ASSERT(etag_in_p2->value.data == orig_etag_value.data,
                "ETag value restored in part2");
    TEST_ASSERT(test_request.headers_out.etag == etag_in_p2,
                "typed ETag pointer restored after cross-header rollback");

    TEST_PASS("Multipart rollback: ETag failure rolls back Vary in p1 and ETag in p2");
}

static void test_multipart_rollback_new_push_invalidated_across_parts(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *new_vary_in_p2;
    ngx_int_t rc;
    ngx_uint_t i;

    test_setup_multipart();
    memset(&ctx, 0, sizeof(ctx));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    mp_push_part1("X-App", "v1");
    mp_push_part1("X-Req-Id", "42");
    mp_push_part1("X-Color", "blue");
    mp_push_part1("X-Env", "prod");
    mp_push_part1("X-Region", "us");
    mp_push_part1("X-Shard", "3");
    mp_push_part1("X-Pool", "main");
    mp_push_part1("X-Flavor", "dev");

    mp_link_part2();

    mp_push_part2("ETag", "\"orig\"");
    mp_push_part2("X-Trace", "abc");

    test_vary_accept_rc = NGX_OK;
    test_set_etag_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(&test_request, &ctx, NULL);

    TEST_ASSERT(rc == NGX_ERROR, "commit returns NGX_ERROR on ETag failure");

    new_vary_in_p2 = NULL;
    for (i = 0; i < mp_part2.nelts; i++) {
        ngx_table_elt_t *h = &mp_part2_storage[i];
        if (h->key.len == 4
            && ngx_strncasecmp(h->key.data, (const u_char *) "Vary", 4) == 0
            && h->hash == 0)
        {
            new_vary_in_p2 = h;
            break;
        }
    }

    TEST_ASSERT(new_vary_in_p2 != NULL,
                "new Vary push entry exists in part2 after rollback");
    TEST_ASSERT(new_vary_in_p2->hash == 0,
                "new Vary push entry has hash=0 (invalidated by rollback)");
    TEST_ASSERT(new_vary_in_p2->value.len == 6,
                "new Vary push entry value is 'Accept'");
    TEST_ASSERT(memcmp(new_vary_in_p2->value.data, "Accept", 6) == 0,
                "new Vary push entry value data is 'Accept'");

    TEST_PASS("Multipart rollback: new Vary push in part2 invalidated across parts");
}

static void test_multipart_rollback_cc_failure_target_in_p1_etag_in_p2(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_table_elt_t *cc_in_p1;
    ngx_table_elt_t *etag_in_p2;
    ngx_str_t orig_cc_value;
    ngx_str_t orig_etag_value;
    ngx_int_t rc;

    test_setup_multipart();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;

    cc_in_p1 = mp_push_part1("Cache-Control", "public");
    orig_cc_value = cc_in_p1->value;

    mp_link_part2();

    etag_in_p2 = mp_push_part2("ETag", "\"orig\"");
    orig_etag_value = etag_in_p2->value;
    test_request.headers_out.etag = etag_in_p2;

    mp_push_part2("X-Trace", "abc");

    test_is_authenticated = 1;
    test_auth_cache_control_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_commit_headers(
        &test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_ERROR, "commit returns NGX_ERROR on CC failure");
    TEST_ASSERT(cc_in_p1->hash == 1, "Cache-Control hash restored in part1");
    TEST_ASSERT(cc_in_p1->value.data == orig_cc_value.data,
                "Cache-Control value restored in part1");
    TEST_ASSERT(etag_in_p2->hash == 1, "ETag hash restored in part2");
    TEST_ASSERT(etag_in_p2->value.data == orig_etag_value.data,
                "ETag value restored in part2");
    TEST_ASSERT(test_request.headers_out.etag == etag_in_p2,
                "typed ETag pointer restored");

    TEST_PASS("Multipart rollback: CC failure rolls back CC in p1 and ETag in p2");
}

static void test_multipart_orig_nelts_cross_part_count(void)
{
    ngx_http_markdown_commit_snap_t snap;
    ngx_int_t rc;

    test_setup_multipart();
    memset(&snap, 0, sizeof(snap));

    mp_push_part1("X-App", "v1");
    mp_push_part1("Vary", "Cookie");
    mp_push_part1("X-Req-Id", "42");

    mp_link_part2();

    mp_push_part2("ETag", "\"abc\"");
    mp_push_part2("X-Trace", "def");

    rc = ngx_http_markdown_stream_commit_snapshot_header(
        &test_request, (const u_char *) "Vary", 4, &snap.vary);

    TEST_ASSERT(rc == NGX_OK, "snapshot Vary succeeds");
    TEST_ASSERT(snap.vary.count == 1, "one Vary entry snapshotted");
    TEST_ASSERT(snap.vary.orig_nelts == 5,
                "orig_nelts counts all entries across both parts");

    rc = ngx_http_markdown_stream_commit_snapshot_header(
        &test_request, (const u_char *) "ETag", 4, &snap.etag);

    TEST_ASSERT(rc == NGX_OK, "snapshot ETag succeeds");
    TEST_ASSERT(snap.etag.count == 1, "one ETag entry snapshotted");
    TEST_ASSERT(snap.etag.orig_nelts == 5,
                "orig_nelts same for all headers (total list entry count)");

    TEST_PASS("Multipart orig_nelts: cross-part linear count is correct");
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
    test_commit_auth_cache_control_failure_aborts();
    test_commit_auth_cache_control_success();
    test_rollback_vary_failure_restores_vary();
    test_rollback_etag_failure_restores_etag();
    test_rollback_cc_failure_restores_cc();
    test_rollback_etag_failure_rolls_back_vary();
    test_rollback_restores_typed_etag_pointer();
    test_snapshot_overflow_fails_before_mutation();

    TEST_SECTION("Multipart header list rollback (Rule 39 / Rule 40)");
    test_multipart_rollback_vary_in_part2();
    test_multipart_rollback_target_in_p2_non_target_in_p2();
    test_multipart_rollback_etag_failure_restores_vary_in_p1_etag_in_p2();
    test_multipart_rollback_new_push_invalidated_across_parts();
    test_multipart_rollback_cc_failure_target_in_p1_etag_in_p2();
    test_multipart_orig_nelts_cross_part_count();

    printf("\n  All stream commit tests passed\n\n");
    return 0;
}
