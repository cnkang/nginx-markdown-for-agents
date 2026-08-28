#include "../include/test_common.h"
#include <strings.h>

#define MARKDOWN_STREAMING_ENABLED 1
#include "../../src/ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

/* Provide the content type literal that send_304 needs (the production
 * symbol lives in headers_impl.h, which is not compiled into this test
 * binary). */
u_char ngx_http_markdown_content_type[] =
    NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL;

/* Define opaque handle for test stubs */
struct MarkdownConverterHandle { int dummy; };

#ifndef NGX_OK
#define NGX_OK          0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR       -1
#endif
#ifndef NGX_AGAIN
#define NGX_AGAIN       -2
#endif
#ifndef NGX_DECLINED
#define NGX_DECLINED    -5
#endif
#ifndef NGX_DONE
#define NGX_DONE        -4
#endif

#ifndef NGX_HTTP_NOT_MODIFIED
#define NGX_HTTP_NOT_MODIFIED 304
#endif

#ifndef NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT
#define NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT (-104)
#endif

#ifndef NGX_LOG_ERR
#define NGX_LOG_ERR 3
#endif
#ifndef NGX_LOG_CRIT
#define NGX_LOG_CRIT 2
#endif
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN 4
#endif
#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif

#ifdef ngx_log_debug0
#undef ngx_log_debug0
#endif
#define ngx_log_debug0(level, log, err, fmt)                    \
    do { (void)(level); (void)(log); (void)(err); } while (0)

#ifdef ngx_log_debug1
#undef ngx_log_debug1
#endif
#define ngx_log_debug1(level, log, err, fmt, arg1)              \
    do { (void)(level); (void)(log); (void)(err);               \
         (void)(arg1); } while (0)

#ifdef ngx_log_debug2
#undef ngx_log_debug2
#endif
#define ngx_log_debug2(level, log, err, fmt, arg1, arg2)        \
    do { (void)(level); (void)(log); (void)(err);               \
         (void)(arg1); (void)(arg2); } while (0)

#ifdef ngx_log_debug3
#undef ngx_log_debug3
#endif
#define ngx_log_debug3(level, log, err, fmt, arg1, arg2, arg3)  \
    do { (void)(level); (void)(log); (void)(err);               \
         (void)(arg1); (void)(arg2); (void)(arg3); } while (0)

#ifdef ngx_log_error
#undef ngx_log_error
#endif
#define ngx_log_error(level, log, err, fmt, ...)                \
    do { (void)(level); (void)(log); (void)(err); } while (0)

#define ngx_memcpy(dst, src, n)    memcpy(dst, src, n)
#define ngx_cpymem(dst, src, n)    (((u_char *) memcpy(dst, src, (n))) + (n))
#define ngx_strncmp(s1, s2, n)     strncmp((const char *) (s1), \
                                            (const char *) (s2), (n))
#define ngx_null_string            { 0, NULL }
#define ngx_pfree(pool, p)         do { (void)(pool); (void)(p); } while (0)
#define ngx_str_set(str, text)                                          \
    (str)->len = sizeof(text) - 1; (str)->data = (u_char *) text

#ifndef ERROR_SUCCESS
#define ERROR_SUCCESS 0
#endif

struct ngx_pool_s {
    int dummy;
};

struct ngx_array_s {
    void       *elts;
    ngx_uint_t  nelts;
    size_t      size;
    ngx_uint_t  nalloc;
    ngx_pool_t *pool;
};

/* struct ngx_buf_s provided by nginx_stubs/ngx_core.h */

typedef struct ngx_table_elt_s {
    ngx_str_t key;
    ngx_str_t value;
    ngx_uint_t hash;
    struct ngx_table_elt_s *next;
} ngx_table_elt_t;

typedef struct ngx_list_part_s {
    void *elts;
    ngx_uint_t nelts;
    struct ngx_list_part_s *next;
} ngx_list_part_t;

typedef struct {
    ngx_list_part_t part;
    ngx_list_part_t *last;
    size_t size;
    ngx_uint_t nalloc;
    void *pool;
} ngx_list_t;

struct ngx_connection_s {
    ngx_log_t *log;
};

typedef struct ngx_connection_s ngx_connection_t;

struct ngx_http_request_s {
    ngx_pool_t *pool;
    ngx_connection_t *connection;
    struct {
        ngx_list_t headers;
        ngx_table_elt_t *accept;
        ngx_table_elt_t *cookie;
        ngx_table_elt_t *authorization;
    } headers_in;
    struct {
        ngx_uint_t status;
        ngx_str_t  status_line;
        ngx_list_t headers;
        ngx_list_t trailers;
        ngx_str_t  content_type;
        u_char    *content_type_lowcase;
        ngx_uint_t content_type_hash;
        ngx_str_t  charset;
        size_t     content_type_len;
        ngx_table_elt_t *content_length;
        ngx_table_elt_t *content_encoding;
        ngx_table_elt_t *etag;
        ngx_table_elt_t *accept_ranges;
        ngx_table_elt_t *last_modified;
        off_t      content_length_n;
        time_t     last_modified_time;
    } headers_out;
    ngx_flag_t allow_ranges;
};

struct ngx_module_s {
    int dummy;
};

ngx_module_t ngx_http_markdown_filter_module;
ngx_module_t ngx_http_core_module;

static u_char g_pool_buf[1024 * 64];
static size_t g_pool_offset;
static size_t g_pool_allocations;
static size_t g_pool_fail_at;

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    void *p;
    UNUSED(pool);
    if (g_pool_allocations == g_pool_fail_at) {
        g_pool_allocations++;
        return NULL;
    }
    g_pool_allocations++;
    if (g_pool_offset + size > sizeof(g_pool_buf)) {
        return NULL;
    }
    p = g_pool_buf + g_pool_offset;
    g_pool_offset += size;
    return p;
}

void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    void *p = ngx_palloc(pool, size);
    if (p != NULL) {
        memset(p, 0, size);
    }
    return p;
}

void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    return ngx_palloc(pool, size);
}

ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    if (s1 == NULL || s2 == NULL) {
        return (s1 == s2) ? 0 : (s1 == NULL) ? -1 : 1;
    }
    return (ngx_int_t) strncasecmp((const char *)s1, (const char *)s2, n);
}

/*
 * Minimal ngx_http_time stub: formats a time_t as a fixed RFC 1123
 * string.  The real NGINX function uses gmtime + snprintf; this stub
 * produces a constant date string for testing.  The exact value does
 * not matter for unit tests — only that a non-empty string is produced
 * when last_modified_time is valid.
 */
u_char *
ngx_http_time(u_char *buf, time_t t)
{
    static const u_char  fmt[] = "Wed, 21 Oct 2015 07:28:00 GMT";

    if (buf == NULL || t == (time_t) -1) {
        return buf;
    }

    memcpy(buf, fmt, sizeof(fmt) - 1);
    return buf + (sizeof(fmt) - 1);
}

ngx_table_elt_t *
ngx_list_push(ngx_list_t *list)
{
    ngx_list_part_t *part = &list->part;
    ngx_table_elt_t *elts = (ngx_table_elt_t *) part->elts;

    if (part->nelts < list->nalloc) {
        ngx_table_elt_t *h = &elts[part->nelts];
        part->nelts++;
        memset(h, 0, sizeof(*h));
        return h;
    }
    return NULL;
}

static int g_send_header_rc;
static int g_finalize_call_count;
static int g_prepare_options_rc;
static int g_cond_result_code;
static int g_convert_error_code;
static uint8_t *g_convert_etag;
static uintptr_t g_convert_etag_len;
static uintptr_t g_decide_last_modified_len;
static uintptr_t g_decide_if_modified_since_len;

ngx_int_t
ngx_http_markdown_set_etag(ngx_http_request_t *r, const u_char *etag,
    size_t etag_len)
{
    ngx_list_part_t *part;
    ngx_table_elt_t *headers;

    for (part = &r->headers_out.headers.part; part != NULL; part = part->next) {
        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash != 0 && headers[i].key.len == 4
                && strncasecmp((const char *) headers[i].key.data,
                               "ETag", 4)
                   == 0)
            {
                headers[i].hash = 0;
            }
        }
    }

    if (etag == NULL || etag_len == 0) {
        r->headers_out.etag = NULL;
        return NGX_OK;
    }

    ngx_table_elt_t *h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }
    h->hash = 1;
    h->key.data = (u_char *) "ETag";
    h->key.len = 4;
    h->value.data = ngx_pnalloc(r->pool, etag_len);
    if (h->value.data == NULL) {
        return NGX_ERROR;
    }
    ngx_memcpy(h->value.data, etag, etag_len);
    h->value.len = etag_len;
    r->headers_out.etag = h;
    return NGX_OK;
}

ngx_int_t
ngx_http_send_header(ngx_http_request_t *r)
{
    UNUSED(r);
    return g_send_header_rc;
}

void
ngx_http_finalize_request(ngx_http_request_t *r, ngx_int_t rc)
{
    UNUSED(r);
    UNUSED(rc);
    g_finalize_call_count++;
}

void
ngx_http_clear_content_length(ngx_http_request_t *r)
{
    r->headers_out.content_length = NULL;
}

ngx_int_t
ngx_http_markdown_prepare_conversion_options(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    struct MarkdownOptions *options)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(eff);
    if (options != NULL) {
        memset(options, 0, sizeof(*options));
    }
    return g_prepare_options_rc;
}

void
markdown_convert(struct MarkdownConverterHandle *handle,
    const uint8_t *input, uintptr_t input_len,
    const struct MarkdownOptions *options,
    struct MarkdownResult *result)
{
    UNUSED(handle); UNUSED(input); UNUSED(input_len); UNUSED(options);
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
        result->error_code = g_convert_error_code;
        result->etag = g_convert_etag;
        result->etag_len = g_convert_etag_len;
    }
}

void
markdown_decide_conditional(const struct FFIConditionalInput *input,
    struct FFIConditionalDecision *out)
{
    g_decide_last_modified_len = input == NULL
                                 ? 0 : input->last_modified_len;
    g_decide_if_modified_since_len = input == NULL
                                     ? 0 : input->if_modified_since_len;

    if (out == NULL) {
        return;
    }

    memset(out, 0, sizeof(*out));
    out->outcome = 1;
    out->reason = 0;
    out->evaluated_header = 0;

    if (input == NULL || input->cache_validation == 0) {
        return;
    }

    if (input->has_range) {
        out->outcome = 2;
        out->reason = 3;
        return;
    }

    if (input->no_transform) {
        out->outcome = 2;
        out->reason = 4;
        return;
    }

    if (input->cache_validation == 1) {
        if (input->if_modified_since_len > 0 && input->last_modified_len > 0) {
            out->outcome = (uint8_t) g_cond_result_code;
            out->reason = 2;
            out->evaluated_header = 2;
        }
        return;
    }

    if (input->if_none_match_len > 0) {
        out->outcome = (uint8_t) g_cond_result_code;
        out->reason = 1;
        out->evaluated_header = 1;
        return;
    }

    if (input->if_modified_since_len > 0 && input->last_modified_len > 0) {
        out->outcome = (uint8_t) g_cond_result_code;
        out->reason = 2;
        out->evaluated_header = 2;
    }
}

void
markdown_result_free(struct MarkdownResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_converter_free(struct MarkdownConverterHandle *handle)
{
    UNUSED(handle);
}

void
markdown_result_init(struct MarkdownResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_decompress_free(struct FFIDecompResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_decomp_result_init(struct FFIDecompResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

uint32_t
markdown_decompress_bounded(const uint8_t *input,
    uintptr_t input_len, uint8_t format,
    uintptr_t budget, uint64_t ratio, struct FFIDecompResult *result)
{
    UNUSED(input); UNUSED(input_len); UNUSED(format);
    UNUSED(budget); UNUSED(ratio); UNUSED(result);
    return 0;
}

const uint8_t *
markdown_reason_code_str(uint32_t code, uintptr_t *out_len)
{
    UNUSED(code);
    if (out_len != NULL) { *out_len = 0; }
    return NULL;
}

const uint8_t *
markdown_reason_code_metric_key(uint32_t code, uintptr_t *out_len)
{
    UNUSED(code);
    if (out_len != NULL) { *out_len = 0; }
    return NULL;
}

uint32_t
markdown_reason_code_count(void)
{
    return 0;
}

#ifndef ngx_http_get_module_loc_conf
static ngx_http_markdown_conf_t *g_conditional_conf;
#define ngx_http_get_module_loc_conf(request, module) (g_conditional_conf)
#else
static ngx_http_markdown_conf_t *g_conditional_conf;
#endif

#include "../../src/ngx_http_markdown_conditional.c"
#include "../../src/ngx_http_markdown_auth.c"

/* Stub for ngx_http_markdown_clear_trailers: the production implementation
 * lives in ngx_http_markdown_headers_impl.h (not compiled into this test
 * binary).  Mirror the production semantics: mark every trailer entry
 * hash=0 so output filters suppress the trailer block. */
void
ngx_http_markdown_set_representation_content_type(ngx_http_request_t *r)
{
    /* Test-local mirror of the production helper: invalidate all
     * Content-Type list entries, then point the dedicated field at the
     * shared Markdown media type and set its charset and mirrors.  Mirrors the
     * semantics asserted by headers_test.c against the real helper. */
    ngx_list_part_t  *part;
    ngx_table_elt_t  *headers;

    for (part = &r->headers_out.headers.part; part != NULL;
         part = part->next)
    {
        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash != 0 && headers[i].key.len == 12
                && strncasecmp((const char *) headers[i].key.data,
                               "Content-Type", 12)
                   == 0)
            {
                headers[i].hash = 0;
            }
        }
    }

    r->headers_out.content_type.data =
        (u_char *) NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL;
    r->headers_out.content_type.len =
        sizeof(NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL) - 1;
    r->headers_out.content_type_len =
        sizeof(NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL) - 1;
    r->headers_out.charset.len = NGX_HTTP_MARKDOWN_CHARSET_LEN;
    r->headers_out.charset.data =
        (u_char *) NGX_HTTP_MARKDOWN_CHARSET_LITERAL;
    r->headers_out.content_type_lowcase = NULL;
    r->headers_out.content_type_hash = 0;
}

void
ngx_http_markdown_clear_trailers(ngx_http_request_t *r)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *elts;

    part = &r->headers_out.trailers.part;

    while (part != NULL) {
        elts = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            elts[i].hash = 0;
        }
        part = part->next;
    }
}

/* Test-local mirror of the production Vary helper (headers_impl.h): find an
 * existing Vary header, add "Accept" when absent, append the Accept token to
 * an existing value, and treat an existing Accept token as a no-op.  The
 * dedup behavior is the contract under test for the 304 path; keeping it
 * beside the other mirrors keeps the conditional suite self-contained. */
static u_char test_hdr_vary[] = "Vary";
static u_char test_hdr_accept[] = "Accept";
static u_char test_vary_suffix[] = ", Accept";

static ngx_table_elt_t *
test_find_header_ci(ngx_http_request_t *r, const u_char *name, size_t name_len)
{
    for (ngx_list_part_t *part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash != 0
                && headers[i].key.len == name_len
                && strncasecmp((const char *) headers[i].key.data,
                               (const char *) name, name_len) == 0)
            {
                return &headers[i];
            }
        }
    }
    return NULL;
}

ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    ngx_table_elt_t *vary = test_find_header_ci(
        r, test_hdr_vary, sizeof(test_hdr_vary) - 1);

    if (vary == NULL) {
        ngx_table_elt_t *h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }
        h->hash = 1;
        h->key.data = test_hdr_vary;
        h->key.len = sizeof(test_hdr_vary) - 1;
        h->value.data = test_hdr_accept;
        h->value.len = sizeof(test_hdr_accept) - 1;
        return NGX_OK;
    }

    /* Already carries the Accept token: dedup no-op. */
    if (vary->value.len == sizeof(test_hdr_accept) - 1
        && strncasecmp((const char *) vary->value.data,
                       (const char *) test_hdr_accept,
                       sizeof(test_hdr_accept) - 1) == 0)
    {
        return NGX_OK;
    }

    if (vary->value.len
        > ((size_t) -1) - (sizeof(test_vary_suffix) - 1))
    {
        return NGX_ERROR;
    }

    size_t  len = vary->value.len + sizeof(test_vary_suffix) - 1;
    u_char *p = ngx_pnalloc(r->pool, len);
    if (p == NULL) {
        return NGX_ERROR;
    }

    u_char *tail = ngx_cpymem(p, vary->value.data, vary->value.len);
    tail = ngx_cpymem(tail, test_vary_suffix, sizeof(test_vary_suffix) - 1);
    (void) tail;

    vary->value.data = p;
    vary->value.len = len;
    return NGX_OK;
}

static ngx_list_t *
create_header_list(void)
{
    ngx_list_t *list;
    ngx_table_elt_t *elts;

    list = (ngx_list_t *) ngx_palloc(NULL, sizeof(ngx_list_t));
    if (list == NULL) return NULL;
    memset(list, 0, sizeof(*list));

    elts = (ngx_table_elt_t *) ngx_palloc(NULL,
        sizeof(ngx_table_elt_t) * 16);
    if (elts == NULL) return NULL;
    memset(elts, 0, sizeof(ngx_table_elt_t) * 16);

    list->part.elts = elts;
    list->part.nelts = 0;
    list->part.next = NULL;
    list->size = sizeof(ngx_table_elt_t);
    list->nalloc = 16;
    return list;
}

static ngx_table_elt_t *
add_header(ngx_list_t *list, const char *key, const char *value)
{
    ngx_table_elt_t *h = ngx_list_push(list);
    if (h == NULL) return NULL;
    h->key.data = (u_char *) key;
    h->key.len = strlen(key);
    h->value.data = (u_char *) value;
    h->value.len = strlen(value);
    h->hash = 1;
    return h;
}

static ngx_http_request_t *
make_req(void)
{
    g_pool_fail_at = (size_t) -1;
    g_pool_allocations = 0;
    g_finalize_call_count = 0;

    ngx_http_request_t *r = (ngx_http_request_t *)
        ngx_pcalloc(NULL, sizeof(ngx_http_request_t));
    if (r == NULL) return NULL;
    r->pool = NULL;
    r->connection = (ngx_connection_t *)
        ngx_pcalloc(NULL, sizeof(ngx_connection_t));
    if (r->connection == NULL) return NULL;
    r->headers_in.headers = *create_header_list();
    r->headers_out.headers = *create_header_list();
    g_conditional_conf = NULL;
    return r;
}

static ngx_table_elt_t *
fill_response_headers(ngx_http_request_t *r)
{
    ngx_table_elt_t *original_etag;

    original_etag = add_header(&r->headers_out.headers,
                               "ETag", "\"upstream\"");
    r->headers_out.etag = original_etag;
    r->headers_out.status = 200;
    r->headers_out.status_line.data = (u_char *) "OK";
    r->headers_out.status_line.len = 2;
    r->headers_out.content_type.data = (u_char *) "text/html";
    r->headers_out.content_type.len = sizeof("text/html") - 1;
    r->headers_out.content_type_len = sizeof("text/html") - 1;
    r->headers_out.content_length_n = 123;
    r->allow_ranges = 1;

    while (r->headers_out.headers.part.nelts
           < r->headers_out.headers.nalloc)
    {
        add_header(&r->headers_out.headers, "X-Filler", "value");
    }

    return original_etag;
}

/* Count live Vary headers in the response header list. */
static ngx_uint_t
count_vary_headers(ngx_http_request_t *r)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *headers;
    ngx_uint_t        count = 0;

    for (part = &r->headers_out.headers.part; part != NULL;
         part = part->next)
    {
        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash != 0 && headers[i].key.len == 4
                && strncasecmp((const char *) headers[i].key.data,
                               "Vary", 4)
                   == 0)
            {
                count++;
            }
        }
    }
    return count;
}

/* A 304 for an upstream response that already carries Vary: Accept must
 * reuse that header (the shared dedup helper) rather than pushing a second
 * Vary entry — HTTP combiners would fold the duplicate, but a doubled Vary
 * leaks the module's mutation and complicates downstream caches. */
static void
test_send_304_does_not_duplicate_upstream_vary(void)
{
    ngx_http_request_t *r;
    ngx_table_elt_t    *upstream_vary;
    struct MarkdownResult result;

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    r->headers_out.trailers = *create_header_list();
    upstream_vary = add_header(&r->headers_out.headers, "Vary", "Accept");
    memset(&result, 0, sizeof(result));

    TEST_ASSERT(ngx_http_markdown_send_304(r, &result) == NGX_DONE,
                "send_304 succeeds with an upstream Vary header");
    TEST_ASSERT(count_vary_headers(r) == 1,
                "no second Vary header is added for the 304");
    TEST_ASSERT(upstream_vary->hash != 0,
                "the upstream Vary header stays in place");

    TEST_PASS("304 with an upstream Vary: Accept does not add a second Vary");
}

/* A 304 for an upstream response carrying a different Vary token must
 * append Accept to the existing header, not emit a second Vary entry. */
static void
test_send_304_appends_accept_to_upstream_vary(void)
{
    ngx_http_request_t *r;
    ngx_table_elt_t    *upstream_vary;
    struct MarkdownResult result;

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    r->headers_out.trailers = *create_header_list();
    upstream_vary = add_header(&r->headers_out.headers,
                               "Vary", "Accept-Encoding");
    memset(&result, 0, sizeof(result));

    TEST_ASSERT(ngx_http_markdown_send_304(r, &result) == NGX_DONE,
                "send_304 succeeds with a foreign Vary token");
    TEST_ASSERT(count_vary_headers(r) == 1,
                "no second Vary header is added");
    TEST_ASSERT(upstream_vary->value.len > sizeof("Accept-Encoding") - 1,
                "Accept appended to the upstream Vary value");

    TEST_PASS("304 appends Accept to an existing foreign Vary header");
}

static void
assert_304_failure_restored(ngx_http_request_t *r,
    ngx_table_elt_t *original_etag, ngx_table_elt_t *original_trailer,
    ngx_uint_t original_header_count)
{
    TEST_ASSERT(r->headers_out.status == 200,
                "304 failure restores status");
    TEST_ASSERT(r->headers_out.status_line.len == 2
                && memcmp(r->headers_out.status_line.data, "OK", 2) == 0,
                "304 failure restores status line");
    TEST_ASSERT(r->headers_out.content_type.len == sizeof("text/html") - 1
                && memcmp(r->headers_out.content_type.data,
                          "text/html", sizeof("text/html") - 1) == 0,
                "304 failure restores Content-Type");
    TEST_ASSERT(r->headers_out.content_length_n == 123,
                "304 failure restores Content-Length value");
    TEST_ASSERT(r->allow_ranges == 1,
                "304 failure restores range state");
    TEST_ASSERT(r->headers_out.etag == original_etag
                && original_etag->hash == 1,
                "304 failure restores typed and list ETag");
    TEST_ASSERT(r->headers_out.headers.part.nelts == original_header_count,
                "304 failure restores header list length");
    TEST_ASSERT(original_trailer == NULL || original_trailer->hash == 1,
                "304 failure restores trailer entries");
}

static void
test_send_304_etag_failure_restores_headers(void)
{
    ngx_http_request_t       *r;
    ngx_table_elt_t          *original_etag;
    ngx_table_elt_t          *original_trailer;
    ngx_uint_t                original_header_count;
    struct MarkdownResult     result;
    static uint8_t             etag_data[] = "\"markdown\"";

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    r->headers_out.trailers = *create_header_list();
    original_etag = fill_response_headers(r);
    original_trailer = add_header(&r->headers_out.trailers,
                                  "Digest", "sha-256=upstream");
    original_header_count = r->headers_out.headers.part.nelts;

    memset(&result, 0, sizeof(result));
    result.etag = etag_data;
    result.etag_len = sizeof(etag_data) - 1;

    TEST_ASSERT(ngx_http_markdown_send_304(r, &result) == NGX_ERROR,
                "ETag allocation failure returns NGX_ERROR");
    assert_304_failure_restored(r, original_etag, original_trailer,
                                original_header_count);
    TEST_PASS("304 ETag failure rolls back all representation headers");
}

static void
test_send_304_etag_value_failure_restores_headers(void)
{
    ngx_http_request_t       *r;
    ngx_table_elt_t          *original_etag;
    ngx_uint_t                original_header_count;
    struct MarkdownResult     result;
    static uint8_t             etag_data[] = "\"markdown\"";

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    r->headers_out.status = 200;
    r->headers_out.status_line.data = (u_char *) "OK";
    r->headers_out.status_line.len = 2;
    r->headers_out.content_type.data = (u_char *) "text/html";
    r->headers_out.content_type.len = sizeof("text/html") - 1;
    r->headers_out.content_type_len = sizeof("text/html") - 1;
    r->headers_out.content_length_n = 123;
    r->allow_ranges = 1;
    original_etag = add_header(&r->headers_out.headers,
                               "ETag", "\"upstream\"");
    r->headers_out.etag = original_etag;
    original_header_count = r->headers_out.headers.part.nelts;

    /* Snapshot allocation succeeds; the ETag value copy must fail. */
    g_pool_fail_at = g_pool_allocations + 1;
    memset(&result, 0, sizeof(result));
    result.etag = etag_data;
    result.etag_len = sizeof(etag_data) - 1;

    TEST_ASSERT(ngx_http_markdown_send_304(r, &result) == NGX_ERROR,
                "ETag value allocation failure returns NGX_ERROR");
    assert_304_failure_restored(r, original_etag, NULL,
                                original_header_count);
    TEST_PASS("304 ETag value failure rolls back all representation headers");
}

static void
test_send_304_vary_failure_restores_headers(void)
{
    ngx_http_request_t       *r;
    ngx_table_elt_t          *original_etag;
    ngx_table_elt_t          *original_trailer;
    ngx_uint_t                original_header_count;
    struct MarkdownResult     result;

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    r->headers_out.trailers = *create_header_list();
    original_etag = fill_response_headers(r);
    original_trailer = add_header(&r->headers_out.trailers,
                                  "Digest", "sha-256=upstream");
    original_header_count = r->headers_out.headers.part.nelts;

    memset(&result, 0, sizeof(result));

    TEST_ASSERT(ngx_http_markdown_send_304(r, &result) == NGX_ERROR,
                "Vary allocation failure returns NGX_ERROR");
    assert_304_failure_restored(r, original_etag, original_trailer,
                                original_header_count);
    TEST_PASS("304 Vary failure rolls back all representation headers");
}

static void
test_send_304_auth_cache_control_failure_restores_headers(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_table_elt_t          *authorization;
    ngx_table_elt_t          *cache_control;
    struct MarkdownResult     result;

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    memset(&conf, 0, sizeof(conf));
    g_conditional_conf = &conf;
    authorization = add_header(&r->headers_in.headers,
                               "Authorization", "Bearer token");
    r->headers_in.authorization = authorization;
    cache_control = add_header(&r->headers_out.headers,
                               "Cache-Control", "public");

    /* The first post-setup allocation is the snapshot; fail the auth rewrite
     * allocation so the snapshot has to restore the pre-304 response. */
    g_pool_fail_at = g_pool_allocations + 1;
    memset(&result, 0, sizeof(result));

    TEST_ASSERT(ngx_http_markdown_send_304(r, &result) == NGX_ERROR,
                "auth Cache-Control allocation failure returns NGX_ERROR");
    TEST_ASSERT(r->headers_out.status == 0,
                "auth Cache-Control failure restores status");
    TEST_ASSERT(cache_control->hash == 1
                && cache_control->value.len == sizeof("public") - 1
                && memcmp(cache_control->value.data, "public",
                          sizeof("public") - 1) == 0,
                "auth Cache-Control failure restores original value");
    TEST_PASS("304 auth Cache-Control failure rolls back header mutation");
}

/* ── send_304 tests ──────────────────────────────────────────── */

static void
test_send_304_with_etag(void)
{
    ngx_table_elt_t *content_encoding;
    ngx_table_elt_t *trailer;

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    content_encoding = add_header(&r->headers_out.headers,
                                  "Content-Encoding", "gzip");
    r->headers_out.content_encoding = content_encoding;
    r->headers_out.trailers = *create_header_list();
    trailer = add_header(&r->headers_out.trailers,
                         "Digest", "sha-256=upstream");

    static uint8_t etag_data[] = "\"abc123\"";
    struct MarkdownResult result;
    memset(&result, 0, sizeof(result));
    result.etag = etag_data;
    result.etag_len = 8;

    ngx_int_t rc = ngx_http_markdown_send_304(r, &result);
    TEST_ASSERT(rc == NGX_DONE, "send_304 returns NGX_DONE");
    TEST_ASSERT(g_finalize_call_count == 0,
                "send_304 leaves finalization to the body-filter caller");
    TEST_ASSERT(r->headers_out.status == NGX_HTTP_NOT_MODIFIED,
        "Status is 304");
    TEST_ASSERT(r->headers_out.content_encoding == NULL
                && content_encoding->hash == 0,
                "304 clears Content-Encoding");
    TEST_ASSERT(trailer->hash == 0,
                "304 clears actual trailer fields");
    TEST_PASS("send_304 with ETag");
}

static void
test_send_304_replaces_existing_etag(void)
{
    ngx_table_elt_t *upstream_etag;
    ngx_uint_t active_etags;

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    upstream_etag = add_header(&r->headers_out.headers,
                               "ETag", "\"upstream\"");
    r->headers_out.etag = upstream_etag;

    static uint8_t etag_data[] = "\"markdown\"";
    struct MarkdownResult result;
    memset(&result, 0, sizeof(result));
    result.etag = etag_data;
    result.etag_len = sizeof(etag_data) - 1;

    ngx_int_t rc = ngx_http_markdown_send_304(r, &result);
    TEST_ASSERT(rc == NGX_DONE, "send_304 returns NGX_DONE");
    TEST_ASSERT(upstream_etag->hash == 0,
                "Existing upstream ETag should be invalidated");
    TEST_ASSERT(r->headers_out.etag != upstream_etag,
                "Typed ETag pointer should reference Markdown ETag");

    active_etags = 0;
    ngx_table_elt_t *headers = r->headers_out.headers.part.elts;
    for (ngx_uint_t i = 0; i < r->headers_out.headers.part.nelts; i++) {
        if (headers[i].hash != 0 && headers[i].key.len == 4
            && strncasecmp((const char *) headers[i].key.data,
                           "ETag", 4)
               == 0)
        {
            active_etags++;
        }
    }
    TEST_ASSERT(active_etags == 1,
                "304 response should contain exactly one active ETag");
    TEST_PASS("send_304 replaces existing ETag");
}

static void
test_auth_ignores_invalidated_authorization(void)
{
    ngx_http_request_t *r;
    ngx_table_elt_t *authorization;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    authorization = add_header(&r->headers_in.headers,
                               "Authorization", "Bearer stale");
    r->headers_in.authorization = authorization;
    authorization->hash = 0;

    TEST_ASSERT(ngx_http_markdown_is_authenticated(r, NULL) == 0,
                "Invalidated Authorization should not authenticate request");

    authorization->hash = 1;
    TEST_ASSERT(ngx_http_markdown_is_authenticated(r, NULL) == 1,
                "Active Authorization should authenticate request");
    TEST_PASS("invalidated Authorization is ignored");
}

static void
test_send_304_null_result(void)
{
    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    ngx_int_t rc = ngx_http_markdown_send_304(r, NULL);
    TEST_ASSERT(rc == NGX_DONE, "send_304 returns NGX_DONE with NULL result");
    TEST_PASS("send_304 with NULL result");
}

static void
test_send_304_empty_etag(void)
{
    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    struct MarkdownResult result;
    memset(&result, 0, sizeof(result));
    result.etag = NULL;
    result.etag_len = 0;

    ngx_int_t rc = ngx_http_markdown_send_304(r, &result);
    TEST_ASSERT(rc == NGX_DONE, "send_304 returns NGX_DONE with empty etag");
    TEST_PASS("send_304 with empty etag");
}

static void
test_send_304_send_header_fails(void)
{
    g_pool_offset = 0;
    g_send_header_rc = NGX_ERROR;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    struct MarkdownResult result;
    memset(&result, 0, sizeof(result));

    ngx_int_t rc = ngx_http_markdown_send_304(r, &result);
    TEST_ASSERT(rc == NGX_ERROR, "send_304 returns NGX_ERROR on header fail");
    TEST_PASS("send_304 header failure");
}

/* ── find_request_header tests ───────────────────────────────── */

static void
test_find_header_null_name(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        r, NULL, 0);
    TEST_ASSERT(h == NULL, "NULL name returns NULL");
    TEST_PASS("NULL name handled");
}

static void
test_find_header_found(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"abc\"");
    add_header(&r->headers_in.headers, "Accept", "text/html");

    static u_char inm_name[] = "If-None-Match";
    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        r, inm_name, sizeof(inm_name) - 1);
    TEST_ASSERT(h != NULL, "If-None-Match found");
    TEST_PASS("header found");
}

static void
test_find_header_not_found(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "Accept", "text/html");

    static u_char inm_name[] = "If-None-Match";
    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        r, inm_name, sizeof(inm_name) - 1);
    TEST_ASSERT(h == NULL, "If-None-Match not found");
    TEST_PASS("header not found");
}

static void
test_find_header_hash_zero_skipped(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    ngx_table_elt_t *h1 = add_header(&r->headers_in.headers,
        "If-None-Match", "\"invalid\"");
    h1->hash = 0;
    add_header(&r->headers_in.headers, "If-None-Match", "\"valid\"");

    static u_char inm_name[] = "If-None-Match";
    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        r, inm_name, sizeof(inm_name) - 1);
    TEST_ASSERT(h != NULL, "Skipped hash==0, found valid");
    TEST_ASSERT(MEM_EQ(h->value.data, "\"valid\"", 7),
        "Correct value found");
    TEST_PASS("hash==0 skipped");
}

/* ── handle_if_none_match tests ──────────────────────────────── */

static void
test_handle_inm_disabled(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_DECLINED, "Disabled returns NGX_DECLINED");
    TEST_PASS("conditional disabled");
}

static void
test_handle_inm_if_modified_since_only(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "IF_MODIFIED_SINCE only returns NGX_DECLINED");
    TEST_PASS("if_modified_since_only mode");
}

static void
test_handle_ims_only_cannot_synthesize_not_modified(void)
{
    g_pool_offset = 0;
    g_prepare_options_rc = NGX_ERROR;
    g_cond_result_code = 0;

    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"ignored\"");
    add_header(&r->headers_in.headers, "If-Modified-Since",
        "Wed, 21 Oct 2015 07:28:00 GMT");
    add_header(&r->headers_out.headers, "Last-Modified",
        "Wed, 21 Oct 2015 07:28:00 GMT");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    conf.policy.generate_etag = 0;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "IMS-only request falls through to conversion instead of a "
        "source-mtime 304");
    TEST_ASSERT(result == NULL, "suppressed conditional path does not allocate result");
    TEST_ASSERT(g_decide_if_modified_since_len == 0,
        "decision input carries no If-Modified-Since value");
    TEST_ASSERT(g_decide_last_modified_len == 0,
        "decision input carries no source Last-Modified value");
    TEST_PASS("ims_only validators withheld from converted-representation decision");
}

static void
test_handle_inm_no_inm_header(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_DECLINED, "No INM header returns NGX_DECLINED");
    TEST_PASS("no If-None-Match header");
}

static void
test_handle_inm_etag_disabled(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"abc\"");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 0;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_DECLINED, "ETag disabled returns NGX_DECLINED");
    TEST_PASS("etag generation disabled");
}

static void
test_handle_inm_buffer_not_initialized(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"abc\"");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 0;

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_ERROR, "Uninitialized buffer returns NGX_ERROR");
    TEST_PASS("buffer not initialized");
}

static void
test_handle_inm_empty_buffer(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"abc\"");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 1;
    ctx.buffer.size = 0;

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_ERROR, "Empty buffer returns NGX_ERROR");
    TEST_PASS("empty buffer");
}

static void
test_handle_inm_null_converter(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"abc\"");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 1;
    ctx.buffer.size = 100;
    ctx.buffer.data = (u_char *) "test data";

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_ERROR, "NULL converter returns NGX_ERROR");
    TEST_PASS("NULL converter");
}

static void
test_handle_inm_prepare_options_fails(void)
{
    g_pool_offset = 0;
    g_prepare_options_rc = NGX_ERROR;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"abc\"");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 1;
    ctx.buffer.size = 100;
    ctx.buffer.data = (u_char *) "test data";

    struct MarkdownConverterHandle converter;
    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_ERROR, "Prepare options fails returns NGX_ERROR");
    TEST_PASS("prepare_conversion_options fails");
}

static void
test_handle_inm_conversion_error(void)
{
    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 1;
    g_convert_etag = NULL;
    g_convert_etag_len = 0;

    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"abc\"");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 1;
    ctx.buffer.size = 100;
    ctx.buffer.data = (u_char *) "test data";

    struct MarkdownConverterHandle converter;
    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_ERROR, "Conversion error returns NGX_ERROR");
    TEST_PASS("conversion error");
}

static void
test_handle_inm_etag_match_304(void)
{
    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    static uint8_t etag_data[] = "\"abc123\"";
    g_convert_etag = etag_data;
    g_convert_etag_len = 8;
    g_cond_result_code = 0;

    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"abc123\"");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 1;
    ctx.buffer.size = 100;
    ctx.buffer.data = (u_char *) "test data";

    struct MarkdownConverterHandle converter;
    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_HTTP_NOT_MODIFIED, "ETag match returns 304");
    TEST_ASSERT(result != NULL, "Result is set");
    TEST_PASS("ETag match returns 304");
}

static void
test_handle_inm_etag_mismatch(void)
{
    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    static uint8_t etag_data[] = "\"abc123\"";
    g_convert_etag = etag_data;
    g_convert_etag_len = 8;
    g_cond_result_code = 1;

    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"different\"");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 1;
    ctx.buffer.size = 100;
    ctx.buffer.data = (u_char *) "test data";

    struct MarkdownConverterHandle converter;
    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_DECLINED, "ETag mismatch returns NGX_DECLINED");
    TEST_ASSERT(result != NULL, "Result is set");
    TEST_PASS("ETag mismatch returns DECLINED");
}

static void
test_handle_inm_with_ims_header(void)
{
    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    static uint8_t etag_data[] = "\"abc123\"";
    g_convert_etag = etag_data;
    g_convert_etag_len = 8;
    g_cond_result_code = 0;

    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-None-Match", "\"abc123\"");
    add_header(&r->headers_in.headers, "If-Modified-Since",
        "Wed, 21 Oct 2015 07:28:00 GMT");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 1;
    ctx.buffer.size = 100;
    ctx.buffer.data = (u_char *) "test data";

    struct MarkdownConverterHandle converter;
    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_HTTP_NOT_MODIFIED, "With IMS header returns 304");
    TEST_PASS("with If-Modified-Since header");
}

/* ── Bypass outcome tests ────────────────────────────────────── */

/*
 * Regression test: Range header with conditional headers present
 * must return Bypass, not proceed to conversion.
 */
static void
test_handle_bypass_range_request(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    add_header(&r->headers_in.headers, "If-Modified-Since",
        "Wed, 21 Oct 2015 07:28:00 GMT");
    add_header(&r->headers_in.headers, "Range", "bytes=0-1023");
    add_header(&r->headers_out.headers, "Last-Modified",
        "Wed, 21 Oct 2015 07:28:00 GMT");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT,
        "Range request returns Bypass result code");
    TEST_ASSERT(result == NULL,
        "Bypass does not allocate conversion result");
    TEST_PASS("Range bypass returns BYPASS, not DECLINED");
}

/*
 * Regression test: Cache-Control: no-transform with conditional
 * headers present must return Bypass, not proceed to conversion.
 */
static void
test_handle_bypass_no_transform(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    add_header(&r->headers_in.headers, "If-Modified-Since",
        "Wed, 21 Oct 2015 07:28:00 GMT");
    add_header(&r->headers_out.headers, "Cache-Control", "no-transform");
    add_header(&r->headers_out.headers, "Last-Modified",
        "Wed, 21 Oct 2015 07:28:00 GMT");

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT,
        "no-transform response returns Bypass result code");
    TEST_ASSERT(result == NULL,
        "Bypass does not allocate conversion result");
    TEST_PASS("no-transform bypass returns BYPASS, not DECLINED");
}

/*
 * Regression test: has_no_transform detects no-transform in a
 * comma-separated Cache-Control value.
 */
static void
test_has_no_transform_in_comma_separated_list(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    add_header(&r->headers_out.headers, "Cache-Control",
        "public, max-age=3600, no-transform");

    ngx_flag_t result = ngx_http_markdown_has_no_transform(r);
    TEST_ASSERT(result == 1,
        "no-transform detected in comma-separated list");
    TEST_PASS("has_no_transform finds directive in list");
}

/*
 * Regression test: has_no_transform returns 0 when no-transform
 * is absent.
 */
static void
test_has_no_transform_absent(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    add_header(&r->headers_out.headers, "Cache-Control",
        "public, max-age=3600");

    ngx_flag_t result = ngx_http_markdown_has_no_transform(r);
    TEST_ASSERT(result == 0, "no-transform absent returns 0");
    TEST_PASS("has_no_transform absent");
}

/*
 * Regression test: has_no_transform returns 0 when no Cache-Control
 * header at all.
 */
static void
test_has_no_transform_no_cache_control(void)
{
    g_pool_offset = 0;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    ngx_flag_t result = ngx_http_markdown_has_no_transform(r);
    TEST_ASSERT(result == 0, "No Cache-Control returns 0");
    TEST_PASS("has_no_transform no header");
}

/* ── last_modified_time fallback tests ──────────────────────── */

/*
 * Regression test: an IMS-only request with only the scalar
 * r->headers_out.last_modified_time set must not be treated as having a
 * Last-Modified header. The conditional handler must decline without
 * allocating a result or passing a date to the Rust decision layer.
 */
static void
test_handle_ims_only_scalar_time_not_consulted(void)
{
    g_pool_offset = 0;
    g_cond_result_code = 0;  /* NotModified would be returned if consulted */

    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-Modified-Since",
        "Wed, 21 Oct 2015 07:28:00 GMT");
    /* No Last-Modified list header — only the dedicated field. */
    r->headers_out.last_modified_time = 1445412480;

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    conf.policy.generate_etag = 0;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "scalar last_modified_time fallback cannot drive a 304 for a "
        "converted response");
    TEST_ASSERT(result == NULL,
        "suppressed fallback path does not allocate result");
    TEST_ASSERT(g_decide_last_modified_len == 0,
        "fallback date never reaches the decision input");
    TEST_ASSERT(g_decide_if_modified_since_len == 0,
        "request IMS value never reaches the decision input");
    TEST_PASS("ims_only scalar last_modified_time withheld from decision");
}

/*
 * Regression test: IMS-only with last_modified_time == -1 (unset)
 * and no Last-Modified list header must NOT produce a 304.
 */
static void
test_handle_ims_only_no_last_modified_at_all(void)
{
    g_pool_offset = 0;
    g_cond_result_code = 0;

    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_in.headers, "If-Modified-Since",
        "Wed, 21 Oct 2015 07:28:00 GMT");
    /* No Last-Modified list header and last_modified_time == -1. */
    r->headers_out.last_modified_time = (time_t) -1;

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    conf.policy.generate_etag = 0;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));

    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "IMS-only with no Last-Modified at all returns DECLINED");
    TEST_PASS("ims_only no last_modified falls through");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("conditional_production Tests\n");
    printf("========================================\n");

    test_send_304_with_etag();
    test_send_304_does_not_duplicate_upstream_vary();
    test_send_304_appends_accept_to_upstream_vary();
    test_send_304_replaces_existing_etag();
    test_auth_ignores_invalidated_authorization();
    test_send_304_null_result();
    test_send_304_empty_etag();
    test_send_304_send_header_fails();
    test_send_304_etag_failure_restores_headers();
    test_send_304_etag_value_failure_restores_headers();
    test_send_304_vary_failure_restores_headers();
    test_send_304_auth_cache_control_failure_restores_headers();

    test_find_header_null_name();
    test_find_header_found();
    test_find_header_not_found();
    test_find_header_hash_zero_skipped();

    test_handle_inm_disabled();
    test_handle_inm_if_modified_since_only();
    test_handle_ims_only_cannot_synthesize_not_modified();
    test_handle_inm_no_inm_header();
    test_handle_inm_etag_disabled();
    test_handle_inm_buffer_not_initialized();
    test_handle_inm_empty_buffer();
    test_handle_inm_null_converter();
    test_handle_inm_prepare_options_fails();
    test_handle_inm_conversion_error();
    test_handle_inm_etag_match_304();
    test_handle_inm_etag_mismatch();
    test_handle_inm_with_ims_header();

    test_handle_bypass_range_request();
    test_handle_bypass_no_transform();
    test_has_no_transform_in_comma_separated_list();
    test_has_no_transform_absent();
    test_has_no_transform_no_cache_control();

    test_handle_ims_only_scalar_time_not_consulted();
    test_handle_ims_only_no_last_modified_at_all();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
