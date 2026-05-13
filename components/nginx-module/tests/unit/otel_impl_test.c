/*
 * Test: otel_impl
 *
 * Validates OpenTelemetry span JSON rendering: correct trace/span ID
 * formatting, attribute serialization, timestamp handling, and edge
 * cases in the JSON output generator.
 */

#include "../include/test_common.h"

#include <ctype.h>
#include <fcntl.h>
#include <time.h>
#include <unistd.h>

typedef unsigned char u_char;
typedef intptr_t      ngx_int_t;
typedef uintptr_t     ngx_uint_t;
typedef ngx_uint_t    ngx_msec_t;
typedef int           ngx_flag_t;
typedef long long     ngx_msec_int_t;

typedef struct ngx_log_s ngx_log_t;
typedef struct ngx_pool_s ngx_pool_t;
typedef struct ngx_module_s ngx_module_t;
typedef struct ngx_http_request_s ngx_http_request_t;
typedef struct ngx_http_request_body_s ngx_http_request_body_t;
typedef struct ngx_http_markdown_conf_s ngx_http_markdown_conf_t;
typedef struct ngx_connection_s ngx_connection_t;

typedef struct {
    size_t  len;
    u_char *data;
} ngx_str_t;

typedef struct {
    ngx_str_t key;
    ngx_str_t value;
} ngx_table_elt_t;

typedef struct ngx_list_part_s ngx_list_part_t;
struct ngx_list_part_s {
    void            *elts;
    ngx_uint_t       nelts;
    ngx_list_part_t *next;
};

typedef struct {
    ngx_list_part_t part;
} ngx_list_t;

typedef struct {
    ngx_list_t headers;
} ngx_headers_in_t;

typedef struct ngx_buf_s ngx_buf_t;
struct ngx_buf_s {
    u_char *pos;
    u_char *last;
    unsigned last_in_chain:1;
    unsigned last_buf:1;
};

typedef struct ngx_chain_s ngx_chain_t;
struct ngx_chain_s {
    ngx_buf_t   *buf;
    ngx_chain_t *next;
};

struct ngx_http_request_body_s {
    ngx_chain_t *bufs;
};

struct ngx_connection_s {
    ngx_log_t *log;
};

typedef struct {
    time_t      sec;
    ngx_uint_t  msec;
} ngx_time_t;

struct ngx_http_markdown_conf_s {
    struct {
        ngx_flag_t otel_enabled;
        ngx_str_t  otel_endpoint;
    } ops;
};

struct ngx_http_request_s {
    ngx_pool_t             *pool;
    ngx_headers_in_t        headers_in;
    ngx_uint_t              method;
    ngx_str_t               method_name;
    ngx_http_request_body_t *request_body;
    ngx_connection_t       *connection;
};

struct ngx_pool_s { int dummy; };
struct ngx_log_s  { int dummy; };
struct ngx_module_s { int dummy; };

typedef struct {
    void (*handler)(ngx_http_request_t *r, void *data, ngx_int_t rc);
    void *data;
} ngx_http_post_subrequest_t;

ngx_module_t ngx_http_markdown_filter_module;
ngx_msec_t ngx_current_msec = 0;
static ngx_time_t g_fake_time = {0, 0};
static ngx_http_markdown_conf_t g_fake_conf;

#define NGX_OK 0
#define NGX_ERROR -1
#define NGX_DECLINED -5
#define NGX_HTTP_POST 2
#define NGX_HTTP_SUBREQUEST_IN_MEMORY 0
#define NGX_LOG_INFO 3
#define NGX_LOG_WARN 2
#define NGX_LOG_ALERT 1

#define ngx_memcpy(dst, src, n) memcpy((dst), (src), (n))
#define ngx_string(str) { sizeof(str) - 1, (u_char *) (str) }
static void
test_otel_log_ignore(const char *fmt, ...)
{
    UNUSED(fmt);
}

#define ngx_log_error(level, log, err, fmt, ...)                                     \
    do {                                                                              \
        UNUSED(level);                                                                \
        UNUSED(log);                                                                  \
        UNUSED(err);                                                                  \
        if (0) {                                                                      \
            test_otel_log_ignore((fmt), ##__VA_ARGS__);                              \
        }                                                                             \
    } while (0)
#define ngx_random() rand()

static ngx_time_t *
ngx_timeofday(void)
{
    return &g_fake_time;
}

static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

static void
ngx_pfree(ngx_pool_t *pool, void *p)
{
    /*
     * Test stub for ngx_pfree.
     *
     * In nginx production builds, ngx_pfree releases pool-managed memory.
     * This unit harness mocks that behavior for heap allocations created
     * by test stubs: `pool` is intentionally unused and `p` is freed via
     * free(p). This helper returns no value and may release heap memory.
     */
    UNUSED(pool);
    free(p);
}

static ngx_buf_t *
ngx_create_temp_buf(ngx_pool_t *pool, size_t size)
{
    ngx_buf_t *b;

    b = ngx_pcalloc(pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        return NULL;
    }

    b->pos = ngx_palloc(pool, size);
    if (b->pos == NULL) {
        return NULL;
    }

    b->last = b->pos;
    return b;
}

static ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    return ngx_pcalloc(pool, sizeof(ngx_chain_t));
}

static ngx_int_t
ngx_http_subrequest(ngx_http_request_t *r, ngx_str_t *uri,
                    ngx_str_t *args, ngx_http_request_t **psr,
                    ngx_http_post_subrequest_t *post, ngx_uint_t flags)
{
    UNUSED(r);
    UNUSED(uri);
    UNUSED(args);
    UNUSED(psr);
    UNUSED(post);
    UNUSED(flags);
    return NGX_DECLINED;
}

static void *
ngx_http_get_module_loc_conf(ngx_http_request_t *r, ngx_module_t module)
{
    UNUSED(r);
    UNUSED(module);
    return &g_fake_conf;
}

static ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    size_t i;

    for (i = 0; i < n; i++) {
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

/*
 * Minimal ngx_slprintf/ngx_snprintf wrappers for test coverage.
 * Rewrites %L/%M into %lld before forwarding to vsnprintf.
 */
static u_char *
test_vslprintf(u_char *buf, u_char *last, const char *fmt, va_list ap)
{
    char   rewritten[4096];
    size_t fi;
    size_t oi;
    size_t rem;
    int    n;

    if (buf >= last) {
        return buf;
    }

    fi = 0;
    oi = 0;
    while (fmt[fi] != '\0' && oi < sizeof(rewritten) - 4) {
        if (fmt[fi] == '%' && (fmt[fi + 1] == 'L' || fmt[fi + 1] == 'M')) {
            rewritten[oi++] = '%';
            rewritten[oi++] = 'l';
            rewritten[oi++] = 'l';
            rewritten[oi++] = 'd';
            fi += 2;
            continue;
        }
        rewritten[oi++] = fmt[fi++];
    }
    rewritten[oi] = '\0';

    rem = (size_t) (last - buf);
    n = vsnprintf((char *) buf, rem, rewritten, ap);
    if (n < 0) {
        return buf;
    }
    if ((size_t) n >= rem) {
        return last;
    }
    return buf + n;
}

static u_char *
ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...)
{
    va_list ap;
    u_char *p;

    va_start(ap, fmt);
    p = test_vslprintf(buf, last, fmt, ap);
    va_end(ap);
    return p;
}

static u_char *
ngx_snprintf(u_char *buf, size_t max, const char *fmt, ...)
{
    va_list ap;
    u_char *p;

    va_start(ap, fmt);
    p = test_vslprintf(buf, buf + max, fmt, ap);
    va_end(ap);
    return p;
}

#include "../../src/ngx_http_markdown_otel_impl.h"

static void
test_otel_render_json_with_string_attr(void)
{
    ngx_http_markdown_otel_span_t span;
    u_char                        out[2048];
    size_t                        n;
    const char                   *json;
    static const u_char           key[] = "reason_code";
    static const u_char           val[] = "ELIGIBLE_MARKDOWN";
    ngx_str_t                     endpoint = ngx_string("/_otel_export");

    TEST_SUBSECTION("OTel JSON render should handle string attrs safely");

    memset(&span, 0, sizeof(span));
    memcpy(span.trace_id, "4bf92f3577b34da6a3ce929d0e0e4736", 32);
    memcpy(span.span_id, "00f067aa0ba902b7", 16);
    span.trace_id[32] = '\0';
    span.span_id[16] = '\0';
    span.start_ms = 100;
    span.end_ms = 120;
    span.start_epoch_nano = 1000000000LL;
    span.end_epoch_nano = 1200000000LL;
    span.attr_count = 1;
    span.attrs[0].key = key;
    span.attrs[0].key_len = sizeof(key) - 1;
    span.attrs[0].str_value = val;
    span.attrs[0].str_value_len = sizeof(val) - 1;
    span.attrs[0].is_int = 0;

    n = ngx_http_markdown_otel_render_json(&span, out, sizeof(out), &endpoint);
    TEST_ASSERT(n > 0, "JSON render should succeed");
    TEST_ASSERT(n < sizeof(out), "JSON length should fit output buffer");
    out[n] = '\0';

    json = (const char *) out;
    TEST_ASSERT(strstr(json, "\"key\":\"reason_code\"") != NULL,
                "JSON should include reason_code key");
    TEST_ASSERT(strstr(json, "\"stringValue\":\"ELIGIBLE_MARKDOWN\"") != NULL,
                "JSON should include string attribute value");
}

static void
test_otel_parse_traceparent_invalid_version(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_otel_span_t span;
    ngx_table_elt_t hdr;
    ngx_list_part_t part;

    TEST_SUBSECTION("OTel parse_traceparent: invalid version");

    memset(&r, 0, sizeof(r));
    memset(&span, 0, sizeof(span));
    memset(&hdr, 0, sizeof(hdr));
    memset(&part, 0, sizeof(part));

    hdr.key.data = (u_char *) "traceparent";
    hdr.key.len = 11;
    hdr.value.data = (u_char *) "01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
    hdr.value.len = 55;

    part.elts = &hdr;
    part.nelts = 1;
    part.next = NULL;
    r.headers_in.headers.part = part;

    TEST_ASSERT(
        ngx_http_markdown_otel_parse_traceparent(&r, &span) == NGX_DECLINED,
        "version 01 should be declined");

    TEST_PASS("invalid version returns DECLINED");
}

static void
test_otel_parse_traceparent_missing_separator(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_otel_span_t span;
    ngx_table_elt_t hdr;
    ngx_list_part_t part;

    TEST_SUBSECTION("OTel parse_traceparent: missing separator after trace_id");

    memset(&r, 0, sizeof(r));
    memset(&span, 0, sizeof(span));
    memset(&hdr, 0, sizeof(hdr));
    memset(&part, 0, sizeof(part));

    hdr.key.data = (u_char *) "traceparent";
    hdr.key.len = 11;
    hdr.value.data = (u_char *) "00-4bf92f3577b34da6a3ce929d0e0e473600f067aa0ba902b7-01";
    hdr.value.len = 55;

    part.elts = &hdr;
    part.nelts = 1;
    part.next = NULL;
    r.headers_in.headers.part = part;

    TEST_ASSERT(
        ngx_http_markdown_otel_parse_traceparent(&r, &span) == NGX_DECLINED,
        "missing separator after trace_id should be declined");

    TEST_PASS("missing separator returns DECLINED");
}

static void
test_otel_parse_traceparent_missing_flags_separator(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_otel_span_t span;
    ngx_table_elt_t hdr;
    ngx_list_part_t part;

    TEST_SUBSECTION("OTel parse_traceparent: missing separator before flags");

    memset(&r, 0, sizeof(r));
    memset(&span, 0, sizeof(span));
    memset(&hdr, 0, sizeof(hdr));
    memset(&part, 0, sizeof(part));

    hdr.key.data = (u_char *) "traceparent";
    hdr.key.len = 11;
    hdr.value.data = (u_char *) "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b701";
    hdr.value.len = 55;

    part.elts = &hdr;
    part.nelts = 1;
    part.next = NULL;
    r.headers_in.headers.part = part;

    TEST_ASSERT(
        ngx_http_markdown_otel_parse_traceparent(&r, &span) == NGX_DECLINED,
        "missing separator before flags should be declined");

    TEST_PASS("missing flags separator returns DECLINED");
}

static void
test_otel_parse_traceparent_lowercase_flags(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_otel_span_t span;
    ngx_table_elt_t hdr;
    ngx_list_part_t part;

    TEST_SUBSECTION("OTel parse_traceparent: lowercase hex flags");

    memset(&r, 0, sizeof(r));
    memset(&span, 0, sizeof(span));
    memset(&hdr, 0, sizeof(hdr));
    memset(&part, 0, sizeof(part));

    hdr.key.data = (u_char *) "traceparent";
    hdr.key.len = 11;
    hdr.value.data = (u_char *) "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-0f";
    hdr.value.len = 55;

    part.elts = &hdr;
    part.nelts = 1;
    part.next = NULL;
    r.headers_in.headers.part = part;

    TEST_ASSERT(
        ngx_http_markdown_otel_parse_traceparent(&r, &span) == NGX_OK,
        "lowercase hex flags should parse");
    TEST_ASSERT(span.trace_flags == 0x0f,
        "flags should be 0x0f");
    TEST_ASSERT(span.has_parent == 1,
        "has_parent should be set");

    TEST_PASS("lowercase hex flags parsed correctly");
}

static void
test_otel_span_start_disabled(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_otel_span_t *span;

    TEST_SUBSECTION("OTel span_start: disabled returns NULL");

    memset(&r, 0, sizeof(r));
    memset(&conf, 0, sizeof(conf));
    conf.ops.otel_enabled = 0;

    span = ngx_http_markdown_otel_span_start(&r, &conf);
    TEST_ASSERT(span == NULL, "disabled otel should return NULL");

    TEST_PASS("disabled otel returns NULL");
}

static void
test_otel_helper_functions(void)
{
    ngx_http_markdown_otel_span_t span;

    TEST_SUBSECTION("OTel helper functions smoke");

    memset(&span, 0, sizeof(span));

    ngx_http_markdown_otel_set_str_attr(
        &span,
        (const u_char *) "key",
        3,
        (const u_char *) "value",
        5);
    ngx_http_markdown_otel_set_int_attr(
        &span,
        (const u_char *) "code",
        4,
        200);

    ngx_current_msec = 1234;
    g_fake_time.sec = 10;
    g_fake_time.msec = 500;
    ngx_http_markdown_otel_span_end(&span);

    ngx_http_markdown_otel_span_export(NULL, NULL, NULL);

    TEST_ASSERT(span.attr_count == 2, "helper attrs should be appended");
    TEST_ASSERT(span.end_ms == 1234, "span_end should record current msec");
    TEST_ASSERT(span.exported == 0, "span_end should clear exported flag");

    TEST_PASS("OTel helper functions covered");
}

int
main(void)
{
    TEST_SECTION("OTel Impl Regression Tests");
    test_otel_render_json_with_string_attr();
    test_otel_parse_traceparent_invalid_version();
    test_otel_parse_traceparent_missing_separator();
    test_otel_parse_traceparent_missing_flags_separator();
    test_otel_parse_traceparent_lowercase_flags();
    test_otel_span_start_disabled();
    test_otel_helper_functions();
    printf("\nAll otel_impl tests passed!\n");
    return 0;
}
