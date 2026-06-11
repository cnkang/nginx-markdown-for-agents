/*
 * Test: eligibility_impl
 *
 * Includes the actual production source so coverage instruments
 * the real decision paths that gate all conversion.
 *
 * Validates: FR-02.1 (method), FR-02.2 (status), FR-02.3 (content-type),
 *            FR-02.8 (streaming), FR-07.2 (range), FR-10.1 (size).
 */

#include "../include/test_common.h"
#include <ctype.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_HTTP_GET
#define NGX_HTTP_GET  0
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 1
#endif
#ifndef NGX_HTTP_OK
#define NGX_HTTP_OK  200
#endif
#ifndef NGX_HTTP_PARTIAL_CONTENT
#define NGX_HTTP_PARTIAL_CONTENT 206
#endif
#ifndef NGX_CONF_UNSET_PTR
#define NGX_CONF_UNSET_PTR ((void *) -1)
#endif

typedef struct ngx_pool_s ngx_pool_t;
typedef struct ngx_table_elt_s ngx_table_elt_t;
typedef struct ngx_http_headers_in_s ngx_http_headers_in_t;
typedef struct ngx_http_headers_out_s ngx_http_headers_out_t;

struct ngx_pool_s { int dummy; };

struct ngx_table_elt_s {
    ngx_str_t key;
    ngx_str_t value;
    ngx_uint_t hash;
};

struct ngx_http_headers_in_s {
    ngx_table_elt_t *range;
};

struct ngx_http_headers_out_s {
    ngx_str_t   content_type;
    ngx_uint_t  status;
    off_t       content_length_n;
};

struct ngx_array_s {
    void       *elts;
    ngx_uint_t  nelts;
    size_t      size;
    ngx_uint_t  nalloc;
    ngx_pool_t *pool;
};

struct ngx_http_request_s {
    ngx_uint_t                method;
    ngx_http_headers_out_t    headers_out;
    ngx_http_headers_in_t     headers_in;
};

static ngx_int_t
ngx_strncasecmp(const u_char *s1, const u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        u_char c1 = (u_char) tolower((unsigned char) s1[i]);
        u_char c2 = (u_char) tolower((unsigned char) s2[i]);
        if (c1 != c2) return (ngx_int_t) c1 - (ngx_int_t) c2;
    }
    return 0;
}

static u_char *
ngx_strlchr(u_char *p, u_char *last, u_char c)
{
    while (p < last) {
        if (*p == c) {
            return p;
        }
        p++;
    }

    return NULL;
}

#include "../../src/ngx_http_markdown_eligibility.c"


static ngx_pool_t g_pool;

static void
set_str(ngx_str_t *s, const char *val)
{
    s->data = (u_char *) (uintptr_t) val;
    s->len = strlen(val);
}

static void
init_conf(ngx_http_markdown_conf_t *conf)
{
    memset(conf, 0, sizeof(*conf));
    conf->content_types = NULL;
    conf->stream_types = NULL;
    conf->max_size = (size_t) -1;
}


static void
test_check_content_type_default(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("check_content_type: default text/html matching");

    memset(&r, 0, sizeof(r));
    init_conf(&conf);

    set_str(&r.headers_out.content_type, "text/html");
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 1,
        "text/html should match default");

    set_str(&r.headers_out.content_type, "text/html;charset=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 1,
        "text/html;charset=utf-8 should match (boundary char ';')");

    set_str(&r.headers_out.content_type, "text/html encoding=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 1,
        "text/html with space separator should match");

    set_str(&r.headers_out.content_type, "text/htmlx");
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 0,
        "text/htmlx must NOT match (no boundary char)");

    set_str(&r.headers_out.content_type, "application/json");
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 0,
        "application/json must NOT match default");

    r.headers_out.content_type.len = 0;
    r.headers_out.content_type.data = NULL;
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 0,
        "empty content-type must NOT match");

    TEST_PASS("Default text/html content-type matching correct");
}


static void
test_check_content_type_custom_allowlist(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_array_t ct_array;
    ngx_str_t ct_entries[2];

    TEST_SUBSECTION("check_content_type: custom allowlist");

    memset(&r, 0, sizeof(r));
    init_conf(&conf);

    set_str(&ct_entries[0], "text/html");
    set_str(&ct_entries[1], "application/xhtml+xml");
    ct_array.elts = ct_entries;
    ct_array.nelts = 2;
    ct_array.size = sizeof(ngx_str_t);
    ct_array.nalloc = 2;
    ct_array.pool = &g_pool;
    conf.content_types = &ct_array;

    set_str(&r.headers_out.content_type, "text/html");
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 1,
        "text/html matches custom allowlist");

    set_str(&r.headers_out.content_type, "application/xhtml+xml");
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 1,
        "application/xhtml+xml matches custom allowlist");

    set_str(&r.headers_out.content_type, "application/json");
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 0,
        "application/json not in allowlist");

    TEST_PASS("Custom content-type allowlist matching correct");
}


static void
test_is_streaming_detection(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("is_streaming: SSE and configured stream types");

    memset(&r, 0, sizeof(r));
    init_conf(&conf);

    set_str(&r.headers_out.content_type, "text/event-stream");
    TEST_ASSERT(
        ngx_http_markdown_is_streaming(&r, &conf) == 1,
        "text/event-stream should be detected as streaming");

    set_str(&r.headers_out.content_type, "text/event-stream;charset=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_is_streaming(&r, &conf) == 1,
        "text/event-stream with charset should be detected");

    set_str(&r.headers_out.content_type, "text/html");
    TEST_ASSERT(
        ngx_http_markdown_is_streaming(&r, &conf) == 0,
        "text/html is not streaming");

    r.headers_out.content_type.len = 0;
    TEST_ASSERT(
        ngx_http_markdown_is_streaming(&r, &conf) == 0,
        "empty content-type is not streaming");

    TEST_PASS("Streaming detection correct");
}


static void
test_is_streaming_configured_types(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_array_t st_array;
    ngx_str_t st_entries[1];

    TEST_SUBSECTION("is_streaming: configured stream_types exclusion");

    memset(&r, 0, sizeof(r));
    init_conf(&conf);

    set_str(&st_entries[0], "application/x-ndjson");
    st_array.elts = st_entries;
    st_array.nelts = 1;
    st_array.size = sizeof(ngx_str_t);
    st_array.nalloc = 1;
    st_array.pool = &g_pool;
    conf.stream_types = &st_array;

    set_str(&r.headers_out.content_type, "application/x-ndjson");
    TEST_ASSERT(
        ngx_http_markdown_is_streaming(&r, &conf) == 1,
        "configured stream type should be detected");

    set_str(&r.headers_out.content_type, "text/html");
    TEST_ASSERT(
        ngx_http_markdown_is_streaming(&r, &conf) == 0,
        "text/html not in stream_types");

    TEST_PASS("Configured stream_types exclusion correct");
}


static void
test_check_eligibility_full_chain(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_table_elt_t range_hdr;

    TEST_SUBSECTION("check_eligibility: full decision chain");

    memset(&r, 0, sizeof(r));
    init_conf(&conf);
    memset(&range_hdr, 0, sizeof(range_hdr));

    r.method = NGX_HTTP_GET;
    r.headers_out.status = NGX_HTTP_OK;
    set_str(&r.headers_out.content_type, "text/html");
    r.headers_out.content_length_n = 1024;
    conf.max_size = 10 * 1024 * 1024;
    r.headers_in.range = NULL;

    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL) == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "valid GET/200/text-html should be ELIGIBLE");

    r.method = NGX_HTTP_HEAD;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL) == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "HEAD method should be ELIGIBLE");

    r.method = 2;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL) == NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD,
        "POST method -> INELIGIBLE_METHOD");

    r.method = NGX_HTTP_GET;
    r.headers_out.status = 404;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL) == NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        "404 status -> INELIGIBLE_STATUS");

    r.headers_out.status = NGX_HTTP_PARTIAL_CONTENT;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL) == NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE,
        "206 status -> INELIGIBLE_RANGE (not INELIGIBLE_STATUS)");

    r.headers_out.status = NGX_HTTP_OK;
    r.headers_in.range = &range_hdr;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL) == NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE,
        "Range header present -> INELIGIBLE_RANGE");

    r.headers_in.range = NULL;
    set_str(&r.headers_out.content_type, "text/event-stream");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL) == NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING,
        "SSE content type -> INELIGIBLE_STREAMING");

    set_str(&r.headers_out.content_type, "application/json");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL) == NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE,
        "application/json -> INELIGIBLE_CONTENT_TYPE");

    set_str(&r.headers_out.content_type, "text/html");
    r.headers_out.content_length_n = 100 * 1024 * 1024;
    conf.max_size = 10 * 1024 * 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL) == NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE,
        "oversized response -> INELIGIBLE_SIZE");

    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 0, NULL) == NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG,
        "filter_enabled=0 -> INELIGIBLE_CONFIG");

    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, NULL, 1, NULL) == NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG,
        "NULL conf -> INELIGIBLE_CONFIG");

    TEST_PASS("Full eligibility decision chain correct");
}


static void
test_check_size_limit_boundary(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("check_size_limit: boundary cases");

    memset(&r, 0, sizeof(r));
    init_conf(&conf);

    conf.max_size = 1024;

    r.headers_out.content_length_n = 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_size_limit(&r, &conf, NULL) == 1,
        "exactly max_size should pass");

    r.headers_out.content_length_n = 1025;
    TEST_ASSERT(
        ngx_http_markdown_check_size_limit(&r, &conf, NULL) == 0,
        "max_size+1 should fail");

    r.headers_out.content_length_n = -1;
    TEST_ASSERT(
        ngx_http_markdown_check_size_limit(&r, &conf, NULL) == 1,
        "missing Content-Length (-1) should pass");

    /* max_size=0 means unlimited — any content length should pass */
    conf.max_size = 0;
    r.headers_out.content_length_n = 1;
    TEST_ASSERT(
        ngx_http_markdown_check_size_limit(&r, &conf, NULL) == 1,
        "max_size=0 (unlimited) + CL=1 should pass");

    r.headers_out.content_length_n = 100 * 1024 * 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_size_limit(&r, &conf, NULL) == 1,
        "max_size=0 (unlimited) + large CL should pass");

    TEST_PASS("Size limit boundary cases correct");
}


static void
test_check_content_type_custom_boundary_char_space(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_array_t ct_array;
    ngx_str_t ct_entries[1];

    TEST_SUBSECTION("check_content_type: custom allowlist with space boundary");

    memset(&r, 0, sizeof(r));
    init_conf(&conf);

    set_str(&ct_entries[0], "text/html");
    ct_array.elts = ct_entries;
    ct_array.nelts = 1;
    ct_array.size = sizeof(ngx_str_t);
    ct_array.nalloc = 1;
    ct_array.pool = &g_pool;
    conf.content_types = &ct_array;

    set_str(&r.headers_out.content_type, "text/html charset=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_check_content_type(&r, &conf) == 1,
        "custom allowlist: text/html with space separator should match");

    TEST_PASS("Custom allowlist boundary char space correct");
}


static void
test_eligibility_string_all_values(void)
{
    const ngx_str_t *s;

    TEST_SUBSECTION("eligibility_string: all enum values");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_ELIGIBLE);
    TEST_ASSERT(s != NULL && s->len > 0, "ELIGIBLE string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_METHOD string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_STATUS string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_CONTENT_TYPE string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_SIZE string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_STREAMING string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_AUTH string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_RANGE string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_CONFIG string non-empty");

    s = ngx_http_markdown_eligibility_string((ngx_http_markdown_eligibility_t) 99);
    TEST_ASSERT(s != NULL && s->len > 0, "Unknown eligibility -> 'unknown' string");

    TEST_PASS("All eligibility string values verified");
}


static void
test_check_size_limit_with_eff(void)
{
    ngx_http_request_t                  r;
    ngx_http_markdown_conf_t            conf;
    ngx_http_markdown_effective_conf_t  eff;

    TEST_SUBSECTION("check_size_limit: non-NULL eff memory_budget path");

    memset(&r, 0, sizeof(r));
    memset(&eff, 0, sizeof(eff));
    init_conf(&conf);

    r.method = NGX_HTTP_GET;
    r.headers_out.status = NGX_HTTP_OK;
    set_str(&r.headers_out.content_type, "text/html");

    /* eff->memory_budget stricter than conf.max_size -> INELIGIBLE_SIZE */
    conf.max_size = 10 * 1024 * 1024;
    eff.memory_budget = 512;
    r.headers_out.content_length_n = 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, &eff)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE,
        "eff.memory_budget < max_size and CL > budget -> INELIGIBLE_SIZE");

    /* eff->memory_budget = unlimited sentinel -> max_size governs */
    eff.memory_budget = (size_t) -1;
    r.headers_out.content_length_n = 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, &eff)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "eff.memory_budget=unlimited + CL within max_size -> ELIGIBLE");

    /* eff->memory_budget = 0 (unlimited) -> max_size governs */
    eff.memory_budget = 0;
    r.headers_out.content_length_n = 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, &eff)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "eff.memory_budget=0 + CL within max_size -> ELIGIBLE");

    TEST_PASS("Size limit with non-NULL eff correct");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("eligibility_impl Tests (production code)\n");
    printf("========================================\n");

    test_check_content_type_default();
    test_check_content_type_custom_allowlist();
    test_is_streaming_detection();
    test_is_streaming_configured_types();
    test_check_eligibility_full_chain();
    test_check_size_limit_boundary();
    test_check_size_limit_with_eff();
    test_check_content_type_custom_boundary_char_space();
    test_eligibility_string_all_values();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
