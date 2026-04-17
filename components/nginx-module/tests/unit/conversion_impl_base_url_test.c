/*
 * Test: conversion_impl_base_url
 * Description: direct coverage for base_url helper paths and overflow guards
 */

#include "../include/test_common.h"
#include <ctype.h>
#include <time.h>

#include "../../src/ngx_http_markdown_filter_module.h"
#include "../../src/markdown_converter.h"

typedef struct ngx_list_part_s ngx_list_part_t;
typedef struct ngx_table_elt_s ngx_table_elt_t;
typedef struct ngx_http_headers_in_s ngx_http_headers_in_t;
typedef struct ngx_http_headers_out_s ngx_http_headers_out_t;
typedef struct ngx_http_core_srv_conf_s ngx_http_core_srv_conf_t;
typedef struct ngx_connection_s ngx_connection_t;
typedef struct ngx_log_s ngx_log_t;
typedef struct ngx_pool_s ngx_pool_t;
typedef ngx_uint_t ngx_atomic_uint_t;
typedef struct ngx_time_s ngx_time_t;

struct ngx_list_part_s {
    void           *elts;
    ngx_uint_t      nelts;
    ngx_list_part_t *next;
};

struct ngx_table_elt_s {
    ngx_str_t key;
    ngx_str_t value;
    ngx_uint_t hash;
};

typedef struct {
    ngx_list_part_t part;
} ngx_list_t;

struct ngx_log_s {
    int dummy;
};

struct ngx_connection_s {
    ngx_log_t *log;
};

struct ngx_pool_s {
    int dummy;
};

struct ngx_buf_s {
    u_char *pos;
    u_char *last;
    u_char *end;
    unsigned memory;
    unsigned last_buf;
    unsigned last_in_chain;
    void *tag;
};

struct ngx_chain_s {
    ngx_buf_t *buf;
    struct ngx_chain_s *next;
};

struct ngx_time_s {
    time_t sec;
    ngx_msec_t msec;
};

struct ngx_http_headers_in_s {
    ngx_list_t headers;
    ngx_str_t  server;
};

struct ngx_http_headers_out_s {
    ngx_str_t content_type;
    ngx_msec_t last_modified_time;
};

struct ngx_http_core_srv_conf_s {
    ngx_str_t server_name;
};

struct ngx_http_request_s {
    ngx_connection_t *connection;
    ngx_pool_t       *pool;
    ngx_uint_t        method;
    ngx_str_t         schema;
    ngx_http_headers_in_t headers_in;
    ngx_http_headers_out_t headers_out;
    ngx_str_t         uri;
    ngx_uint_t        buffered;
    struct ngx_http_request_s *main;
    void             *loc_conf;
    void             *srv_conf;
};

#ifndef ngx_memzero
#define ngx_memzero(buf, n) memset((buf), 0, (n))
#endif
#ifndef ngx_memcpy
#define ngx_memcpy memcpy
#endif
#ifndef NGX_OK
#define NGX_OK 0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR (-1)
#endif
#ifndef NGX_DECLINED
#define NGX_DECLINED (-5)
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 2
#endif
#ifndef NGX_HTTP_NOT_MODIFIED
#define NGX_HTTP_NOT_MODIFIED 304
#endif
#ifndef NGX_HTTP_MARKDOWN_BUFFERED
#define NGX_HTTP_MARKDOWN_BUFFERED 0x00000001
#endif
#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif
#ifndef NGX_HTTP_MARKDOWN_METRIC_ADD
#define NGX_HTTP_MARKDOWN_METRIC_ADD(name, value) ((void) 0)
#endif
#ifndef NGX_HTTP_MARKDOWN_METRIC_INC
#define NGX_HTTP_MARKDOWN_METRIC_INC(name) ((void) 0)
#endif
#ifndef ngx_log_debug2
#define ngx_log_debug2(level, log, err, fmt, arg1, arg2) \
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); UNUSED(arg1); UNUSED(arg2)
#endif
#ifndef ngx_log_debug3
#define ngx_log_debug3(level, log, err, fmt, arg1, arg2, arg3) \
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); UNUSED(arg1); UNUSED(arg2); UNUSED(arg3)
#endif
#ifndef ngx_log_debug0
#define ngx_log_debug0(level, log, err, fmt) UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt)
#endif
#ifndef ngx_log_debug1
#define ngx_log_debug1(level, log, err, fmt, arg) \
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); UNUSED(arg)
#endif
#ifndef ngx_http_get_module_loc_conf
#define ngx_http_get_module_loc_conf(r, module) \
    ((ngx_http_markdown_conf_t *) ((r)->loc_conf))
#endif
#ifndef ngx_http_get_module_srv_conf
#define ngx_http_get_module_srv_conf(r, module) \
    ((ngx_http_core_srv_conf_t *) ((r)->srv_conf))
#endif
#ifndef ngx_tolower
#define ngx_tolower(c) ((u_char) tolower((unsigned char) (c)))
#endif

static ngx_inline u_char *
ngx_cpymem(u_char *dst, const void *src, size_t n)
{
    return (u_char *) memcpy(dst, src, n) + n;
}

static ngx_inline ngx_int_t
ngx_pfree(ngx_pool_t *pool, void *p)
{
    (void) pool;
    free(p);
    return NGX_OK;
}

static ngx_inline void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return malloc(size);
}

static ngx_inline void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    void *p;

    (void) pool;
    p = calloc(1, size);
    return p;
}

static ngx_inline ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    (void) pool;
    return calloc(1, sizeof(ngx_chain_t));
}

static ngx_inline ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        u_char c1;
        u_char c2;

        c1 = (u_char) tolower((unsigned char) s1[i]);
        c2 = (u_char) tolower((unsigned char) s2[i]);
        if (c1 != c2) {
            return (ngx_int_t) c1 - (ngx_int_t) c2;
        }
    }

    return 0;
}

#ifndef ngx_timeofday
static ngx_inline const ngx_time_t *
ngx_timeofday_stub(void)
{
    static ngx_time_t now;

    now.sec = time(NULL);
    now.msec = 0;

    return &now;
}
#define ngx_timeofday() ngx_timeofday_stub()
#endif

static ngx_int_t ngx_http_markdown_forward_headers(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);

u_char ngx_http_markdown_empty_string[] = "";
struct MarkdownConverterHandle *ngx_http_markdown_converter = NULL;
ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;
ngx_int_t (*ngx_http_next_body_filter)(ngx_http_request_t *r, ngx_chain_t *in) = NULL;

#include "../../src/ngx_http_markdown_conversion_impl.h" /* NOSONAR: must follow stub definitions */

static ngx_connection_t g_connection = { 0 };
static ngx_log_t g_log = { 0 };
static ngx_pool_t g_pool = { 0 };

static void
set_str(ngx_str_t *dst, const char *src)
{
    dst->data = (u_char *) (uintptr_t) src;
    dst->len = strlen(src);
}

static void
init_request(ngx_http_request_t *r)
{
    memset(r, 0, sizeof(*r));
    r->connection = &g_connection;
    r->connection->log = &g_log;
    r->pool = &g_pool;
    r->main = r;
}

static void
set_single_header_list(ngx_http_request_t *r, ngx_table_elt_t *headers,
    ngx_uint_t count)
{
    r->headers_in.headers.part.elts = headers;
    r->headers_in.headers.part.nelts = count;
    r->headers_in.headers.part.next = NULL;
}

static void
assert_str_eq(const ngx_str_t *actual, const char *expected, const char *msg)
{
    size_t expected_len;

    expected_len = strlen(expected);
    TEST_ASSERT(actual->len == expected_len, msg);
    TEST_ASSERT(memcmp(actual->data, expected, expected_len) == 0, msg);
}

static void
test_forwarded_headers_priority(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_table_elt_t headers[2];
    ngx_str_t scheme;
    ngx_str_t host;
    ngx_str_t base_url;

    TEST_SUBSECTION("X-Forwarded headers win when trusted");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    conf.ops.trust_forwarded_headers = 1;

    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "origin.example.com");
    set_str(&r.uri, "/articles/page.html");

    set_str(&headers[0].key, "X-Forwarded-Proto");
    set_str(&headers[0].value, "https");
    headers[0].hash = 1;
    set_str(&headers[1].key, "X-Forwarded-Host");
    set_str(&headers[1].value, "proxy.example.com");
    headers[1].hash = 1;
    set_single_header_list(&r, headers, ARRAY_SIZE(headers));
    r.loc_conf = &conf;

    TEST_ASSERT(ngx_http_markdown_select_base_url_parts(&r, &scheme, &host) == NGX_OK,
                "trusted forwarded headers should select base_url parts");
    assert_str_eq(&scheme, "https", "forwarded proto should be selected");
    assert_str_eq(&host, "proxy.example.com", "forwarded host should be selected");

    TEST_ASSERT(ngx_http_markdown_construct_base_url(&r, r.pool, &base_url) == NGX_OK,
                "construct_base_url should succeed with forwarded headers");
    assert_str_eq(&base_url, "https://proxy.example.com/articles/page.html",
                  "base_url should be built from forwarded headers");
    free(base_url.data);

    TEST_PASS("Trusted forwarded-header path is correct");
}

static void
test_direct_request_path(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_str_t scheme;
    ngx_str_t host;
    ngx_str_t base_url;

    TEST_SUBSECTION("Direct request schema and server are used when forwarding is off");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));

    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "origin.example.com");
    set_str(&r.uri, "/docs/index.html");
    r.loc_conf = &conf;

    TEST_ASSERT(ngx_http_markdown_select_base_url_parts(&r, &scheme, &host) == NGX_OK,
                "direct request path should select schema/server");
    assert_str_eq(&scheme, "http", "request schema should be selected");
    assert_str_eq(&host, "origin.example.com", "request server should be selected");

    TEST_ASSERT(ngx_http_markdown_construct_base_url(&r, r.pool, &base_url) == NGX_OK,
                "construct_base_url should succeed for direct requests");
    assert_str_eq(&base_url, "http://origin.example.com/docs/index.html",
                  "base_url should be built from the request");
    free(base_url.data);

    TEST_PASS("Direct-request base_url path is correct");
}

static void
test_server_name_fallback_path(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_http_core_srv_conf_t cscf;
    ngx_str_t scheme;
    ngx_str_t host;
    ngx_str_t base_url;

    TEST_SUBSECTION("Server-name fallback is used when request values are empty");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    memset(&cscf, 0, sizeof(cscf));

    set_str(&r.schema, "");
    set_str(&r.headers_in.server, "");
    set_str(&r.uri, "/fallback/page.html");
    set_str(&cscf.server_name, "fallback.example.org");

    r.loc_conf = &conf;
    r.srv_conf = &cscf;

    TEST_ASSERT(ngx_http_markdown_select_base_url_parts(&r, &scheme, &host) == NGX_OK,
                "server_name fallback should produce base_url parts");
    assert_str_eq(&scheme, "http", "fallback scheme should default to http");
    assert_str_eq(&host, "fallback.example.org", "server_name should be selected");

    TEST_ASSERT(ngx_http_markdown_construct_base_url(&r, r.pool, &base_url) == NGX_OK,
                "construct_base_url should succeed with server_name fallback");
    assert_str_eq(&base_url, "http://fallback.example.org/fallback/page.html",
                  "base_url should be built from server_name fallback");
    free(base_url.data);

    TEST_PASS("Server-name fallback path is correct");
}

static void
test_missing_base_url_inputs_fail(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_str_t scheme;
    ngx_str_t host;
    ngx_str_t base_url;
    ngx_http_core_srv_conf_t cscf;

    TEST_SUBSECTION("Missing scheme, server, and server_name fails cleanly");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    memset(&cscf, 0, sizeof(cscf));

    set_str(&r.schema, "");
    set_str(&r.headers_in.server, "");
    set_str(&r.uri, "/no-base-url");
    set_str(&cscf.server_name, "");
    r.loc_conf = &conf;
    r.srv_conf = &cscf;

    TEST_ASSERT(ngx_http_markdown_select_base_url_parts(&r, &scheme, &host) == NGX_ERROR,
                "base_url part selection should fail without scheme/host");
    TEST_ASSERT(ngx_http_markdown_construct_base_url(&r, r.pool, &base_url) == NGX_ERROR,
                "construct_base_url should fail without any host source");
    TEST_ASSERT(base_url.data == NULL, "failed construction should not allocate output");

    TEST_PASS("Missing base_url inputs fail as expected");
}

static void
test_base_url_add_len_overflow_guard(void)
{
    ngx_http_request_t r;
    size_t total;

    TEST_SUBSECTION("Overflow guard rejects wrapped base_url length");

    init_request(&r);

    total = SIZE_MAX - 2;
    TEST_ASSERT(ngx_http_markdown_base_url_add_len(&r, &total, 3, "scheme") == NGX_ERROR,
                "overflow guard should reject wrapped addition");
    TEST_ASSERT(total == SIZE_MAX - 2, "failed addition must not change the accumulator");

    TEST_PASS("Overflow guard is correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("conversion_impl_base_url Tests\n");
    printf("========================================\n");

    test_forwarded_headers_priority();
    test_direct_request_path();
    test_server_name_fallback_path();
    test_missing_base_url_inputs_fail();
    test_base_url_add_len_overflow_guard();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
