/*
 * Test: conversion_impl_base_url
 * Description: direct coverage for base_url helper paths and overflow guards
 */

#include "../include/test_common.h"
#include <ctype.h>
#include <time.h>

#include "../../src/ngx_http_markdown_filter_module.h"

/*
 * Local definition of MarkdownOptions matching the Rust FFI ABI layout.
 * This avoids depending on the cbindgen-generated markdown_converter.h
 * which only exists after building the Rust library (not available in
 * the C-only unit test CI job).
 */
struct MarkdownOptions {
    uint32_t       flavor;
    uint32_t       timeout_ms;
    uint8_t        generate_etag;
    uint8_t        estimate_tokens;
    uint8_t        front_matter;
    const uint8_t *content_type;
    uintptr_t      content_type_len;
    const uint8_t *base_url;
    uintptr_t      base_url_len;
    uint64_t       streaming_budget;
};

struct MarkdownResult {
    uint8_t   *markdown;
    uintptr_t  markdown_len;
    uint8_t   *etag;
    uintptr_t  etag_len;
    uint32_t   token_estimate;
    uint32_t   error_code;
    uint8_t   *error_message;
    uintptr_t  error_len;
    uintptr_t  peak_memory_estimate;
};

struct MarkdownConverterHandle;

/* FFI stub constants and functions used by conversion_impl.h */
#define ERROR_SUCCESS 0

static void
markdown_convert(struct MarkdownConverterHandle *handle, /* NOSONAR: must match FFI signature */
    const uint8_t *html, uintptr_t html_len,
    const struct MarkdownOptions *options,
    struct MarkdownResult *result)
{
    UNUSED(handle);
    UNUSED(html);
    UNUSED(html_len);
    UNUSED(options);
    memset(result, 0, sizeof(*result));
}

static void
markdown_result_free(struct MarkdownResult *result) /* NOSONAR: must match FFI signature */
{
    UNUSED(result);
}

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

/*
 * Stub definitions for external symbols referenced by conversion_impl.h
 * but not exercised by the base_url / prepare_options tests.
 * These must be defined before the #include of conversion_impl.h
 * because the impl header contains static forward declarations that
 * the linker resolves (GCC on Linux does not strip unused statics).
 */

u_char ngx_http_markdown_empty_string[] = "";
struct MarkdownConverterHandle *ngx_http_markdown_converter = NULL;
ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;
ngx_int_t (*ngx_http_next_body_filter)(ngx_http_request_t *r, ngx_chain_t *in) = NULL;

static ngx_int_t
ngx_http_markdown_forward_headers(
    ngx_http_request_t *r,     /* NOSONAR c:S995 — must match production signature */
    ngx_http_markdown_ctx_t *ctx)  /* NOSONAR c:S995 — must match production signature */
{
    UNUSED(r);
    UNUSED(ctx);
    return NGX_OK;
}

static void
ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(conf);
}

static ngx_int_t
ngx_http_markdown_reject_or_fail_open_buffered_response(
    ngx_http_request_t *r,     /* NOSONAR c:S995 — must match impl forward decl */
    ngx_http_markdown_ctx_t *ctx,  /* NOSONAR c:S995 — must match impl forward decl */
    const ngx_http_markdown_conf_t *conf, const char *debug_message)
{
    UNUSED(r);
    UNUSED(ctx);
    UNUSED(conf);
    UNUSED(debug_message);
    return NGX_OK;
}

ngx_http_markdown_error_category_t
ngx_http_markdown_classify_error(uint32_t error_code)
{
    UNUSED(error_code);
    return NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
}

const ngx_str_t *
ngx_http_markdown_error_category_string(
    ngx_http_markdown_error_category_t category)
{
    static u_char system_str_data[] = "FAIL_SYSTEM";
    static ngx_str_t system_str = {
        sizeof("FAIL_SYSTEM") - 1, system_str_data
    };
    UNUSED(category);
    return &system_str;
}

ngx_int_t
ngx_http_markdown_update_headers(
    ngx_http_request_t *r,     /* NOSONAR c:S995 — must match module header decl */
    const struct MarkdownResult *result,
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(r);
    UNUSED(result);
    UNUSED(conf);
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_handle_if_none_match(
    ngx_http_request_t *r,     /* NOSONAR c:S995 — must match module header decl */
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_ctx_t *ctx,
    struct MarkdownConverterHandle *converter,  /* NOSONAR c:S995 — must match module header decl */
    struct MarkdownResult **result)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(ctx);
    UNUSED(converter);
    UNUSED(result);
    return NGX_DECLINED;
}

ngx_int_t
ngx_http_markdown_send_304(
    ngx_http_request_t *r,     /* NOSONAR c:S995 — must match module header decl */
    const struct MarkdownResult *result)
{
    UNUSED(r);
    UNUSED(result);
    return NGX_OK;
}

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


/*
 * Test: prepare_conversion_options populates all fields correctly.
 */
static void
test_prepare_conversion_options_basic(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: basic field population");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));

    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "example.com");
    set_str(&r.uri, "/page.html");
    set_str(&r.headers_out.content_type, "text/html; charset=utf-8");
    r.loc_conf = &conf;

    conf.flavor = 0;  /* CommonMark */
    conf.timeout = 5000;
    conf.generate_etag = 1;
    conf.token_estimate = 1;
    conf.front_matter = 1;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, &options) == NGX_OK,
                "prepare_conversion_options should succeed");
    TEST_ASSERT(options.flavor == 0, "flavor should be CommonMark (0)");
    TEST_ASSERT(options.timeout_ms == 5000, "timeout should be 5000");
    TEST_ASSERT(options.generate_etag == 1, "generate_etag should be 1");
    TEST_ASSERT(options.estimate_tokens == 1, "estimate_tokens should be 1");
    TEST_ASSERT(options.front_matter == 1, "front_matter should be 1");
    TEST_ASSERT(options.content_type != NULL, "content_type should be set");
    TEST_ASSERT(options.content_type_len > 0, "content_type_len should be > 0");
    TEST_ASSERT(options.base_url != NULL, "base_url should be constructed");
    TEST_ASSERT(options.base_url_len > 0, "base_url_len should be > 0");

    /* Clean up allocated base_url */
    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options basic fields correct");
}


/*
 * Test: prepare_conversion_options with GFM flavor.
 */
static void
test_prepare_conversion_options_gfm(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: GFM flavor");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));

    set_str(&r.schema, "https");
    set_str(&r.headers_in.server, "gfm.example.com");
    set_str(&r.uri, "/doc.html");
    set_str(&r.headers_out.content_type, "text/html");
    r.loc_conf = &conf;

    conf.flavor = 1;  /* GFM */
    conf.timeout = 3000;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, &options) == NGX_OK,
                "prepare_conversion_options should succeed for GFM");
    TEST_ASSERT(options.flavor == 1, "flavor should be GFM (1)");
    TEST_ASSERT(options.timeout_ms == 3000, "timeout should be 3000");

    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options GFM flavor correct");
}


/*
 * Test: prepare_conversion_options without content_type.
 */
static void
test_prepare_conversion_options_no_content_type(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: no content_type");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));

    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "example.com");
    set_str(&r.uri, "/page.html");
    r.headers_out.content_type.len = 0;
    r.headers_out.content_type.data = NULL;
    r.loc_conf = &conf;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, &options) == NGX_OK,
                "prepare_conversion_options should succeed without content_type");
    TEST_ASSERT(options.content_type == NULL, "content_type should be NULL");
    TEST_ASSERT(options.content_type_len == 0, "content_type_len should be 0");

    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options no content_type correct");
}


/*
 * Test: prepare_conversion_options with failed base_url construction.
 */
static void
test_prepare_conversion_options_no_base_url(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_http_core_srv_conf_t cscf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: no base_url");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    memset(&cscf, 0, sizeof(cscf));

    /* Empty schema/server/server_name → base_url construction fails */
    set_str(&r.schema, "");
    set_str(&r.headers_in.server, "");
    set_str(&r.uri, "/page.html");
    set_str(&cscf.server_name, "");
    r.loc_conf = &conf;
    r.srv_conf = &cscf;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, &options) == NGX_OK,
                "prepare_conversion_options should succeed even without base_url");
    TEST_ASSERT(options.base_url == NULL, "base_url should be NULL on failure");
    TEST_ASSERT(options.base_url_len == 0, "base_url_len should be 0 on failure");

    TEST_PASS("prepare_conversion_options no base_url correct");
}


/*
 * Test: scheme_is_http_family validates http and https.
 */
static void
test_scheme_is_http_family(void)
{
    ngx_str_t http_scheme;
    ngx_str_t https_scheme;
    ngx_str_t ftp_scheme;
    ngx_str_t empty_scheme;

    TEST_SUBSECTION("scheme_is_http_family validation");

    set_str(&http_scheme, "http");
    set_str(&https_scheme, "https");
    set_str(&ftp_scheme, "ftp");
    empty_scheme.len = 0;
    empty_scheme.data = NULL;

    TEST_ASSERT(ngx_http_markdown_scheme_is_http_family(&http_scheme) == 1,
                "http should be http family");
    TEST_ASSERT(ngx_http_markdown_scheme_is_http_family(&https_scheme) == 1,
                "https should be http family");
    TEST_ASSERT(ngx_http_markdown_scheme_is_http_family(&ftp_scheme) == 0,
                "ftp should not be http family");
    TEST_ASSERT(ngx_http_markdown_scheme_is_http_family(&empty_scheme) == 0,
                "empty should not be http family");

    TEST_PASS("scheme_is_http_family validation correct");
}


/*
 * Test: find_request_header_value with multi-part header list.
 */
static void
test_find_request_header_multi_part(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_table_elt_t headers_part1[1];
    ngx_table_elt_t headers_part2[1];
    ngx_list_part_t part2;
    const ngx_str_t *result;

    TEST_SUBSECTION("find_request_header_value: multi-part list");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    r.loc_conf = &conf;

    /* Part 1: one header */
    set_str(&headers_part1[0].key, "X-First");
    set_str(&headers_part1[0].value, "value1");
    headers_part1[0].hash = 1;

    /* Part 2: one header */
    set_str(&headers_part2[0].key, "X-Forwarded-Proto");
    set_str(&headers_part2[0].value, "https");
    headers_part2[0].hash = 1;

    part2.elts = headers_part2;
    part2.nelts = 1;
    part2.next = NULL;

    r.headers_in.headers.part.elts = headers_part1;
    r.headers_in.headers.part.nelts = 1;
    r.headers_in.headers.part.next = &part2;

    conf.ops.trust_forwarded_headers = 1;

    /* Search for X-Forwarded-Proto — should find it in part 2 */
    result = ngx_http_markdown_find_request_header_value(
        &r,
        (const u_char *) "X-Forwarded-Proto",
        sizeof("X-Forwarded-Proto") - 1);
    TEST_ASSERT(result != NULL, "should find header in second part");
    TEST_ASSERT(result->len == 5, "value length should be 5");
    TEST_ASSERT(memcmp(result->data, "https", 5) == 0, "value should be https");

    /* Search for non-existent header */
    result = ngx_http_markdown_find_request_header_value(
        &r,
        (const u_char *) "X-Nonexistent",
        sizeof("X-Nonexistent") - 1);
    TEST_ASSERT(result == NULL, "non-existent header should return NULL");

    /* Search with empty header list */
    r.headers_in.headers.part.nelts = 0;
    r.headers_in.headers.part.next = NULL;
    result = ngx_http_markdown_find_request_header_value(
        &r,
        (const u_char *) "X-Forwarded-Proto",
        sizeof("X-Forwarded-Proto") - 1);
    TEST_ASSERT(result == NULL, "empty list should return NULL");

    TEST_PASS("find_request_header_value multi-part correct");
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
    test_prepare_conversion_options_basic();
    test_prepare_conversion_options_gfm();
    test_prepare_conversion_options_no_content_type();
    test_prepare_conversion_options_no_base_url();
    test_scheme_is_http_family();
    test_find_request_header_multi_part();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
