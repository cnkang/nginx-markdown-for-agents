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

#ifndef NGX_HTTP_PRECONDITION_FAILED
#define NGX_HTTP_PRECONDITION_FAILED 412
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
#define ngx_memzero(dst, n)        memset((dst), 0, (n))
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

typedef void (*ngx_pool_cleanup_pt)(void *data);
typedef struct ngx_pool_cleanup_s ngx_pool_cleanup_t;

struct ngx_pool_cleanup_s {
    ngx_pool_cleanup_pt  handler;
    void                *data;
    ngx_pool_cleanup_t  *next;
};

struct ngx_pool_s {
    ngx_pool_cleanup_t  *cleanup;
};

static struct ngx_pool_s g_test_pool;

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
    ngx_buf_t *header_in;
    ngx_connection_t *connection;
    ngx_http_request_t *parent;
    struct {
        ngx_list_t headers;
        ngx_table_elt_t *accept;
        ngx_table_elt_t *cookie;
        ngx_table_elt_t *authorization;
        ngx_table_elt_t *if_none_match;
        ngx_table_elt_t *if_modified_since;
        ngx_table_elt_t *if_match;
        ngx_table_elt_t *if_unmodified_since;
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
        ngx_str_t *override_charset;
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
    ngx_flag_t header_only;
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

ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    ngx_pool_cleanup_t  *cleanup;

    UNUSED(size);
    if (pool == NULL) {
        return NULL;
    }

    cleanup = ngx_pcalloc(pool, sizeof(*cleanup));
    if (cleanup == NULL) {
        return NULL;
    }

    cleanup->next = pool->cleanup;
    pool->cleanup = cleanup;
    return cleanup;
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

time_t
ngx_parse_http_time(u_char *value, size_t len)
{
    static const u_char before[] = "Tue, 20 Oct 2015 07:28:00 GMT";
    static const u_char current[] = "Wed, 21 Oct 2015 07:28:00 GMT";
    static const u_char after[] = "Thu, 22 Oct 2015 07:28:00 GMT";

    if (value == NULL) {
        return (time_t) -1;
    }

    if (len == sizeof(before) - 1
        && memcmp(value, before, len) == 0)
    {
        return (time_t) 1;
    }

    if (len == sizeof(current) - 1
        && memcmp(value, current, len) == 0)
    {
        return (time_t) 2;
    }

    if (len == sizeof(after) - 1
        && memcmp(value, after, len) == 0)
    {
        return (time_t) 3;
    }

    return (time_t) -1;
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
static int g_convert_calls;
static uint8_t *g_convert_etag;
static uintptr_t g_convert_etag_len;
static const uint8_t *g_decide_if_none_match;
static uintptr_t g_decide_if_none_match_len;
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
    g_convert_calls++;
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
    g_decide_if_none_match = input == NULL
                             ? NULL : input->if_none_match;
    g_decide_if_none_match_len = input == NULL
                                 ? 0 : input->if_none_match_len;
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
    g_convert_calls = 0;

    ngx_http_request_t *r = (ngx_http_request_t *)
        ngx_pcalloc(NULL, sizeof(ngx_http_request_t));
    if (r == NULL) return NULL;
    g_test_pool.cleanup = NULL;
    r->pool = &g_test_pool;
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

/* A 412 for a failed If-Match/If-Unmodified-Since precondition must
 * describe the transformed Markdown representation: status 412, no body
 * headers, no source-HTML trailers, and Vary: Accept retained. */
static void
test_send_412_success_clears_body_headers(void)
{
    ngx_http_request_t *r;

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    r->headers_out.trailers = *create_header_list();
    add_header(&r->headers_out.headers, "Content-Length", "123");
    add_header(&r->headers_out.headers, "Accept-Ranges", "bytes");
    add_header(&r->headers_out.headers, "Trailer", "X-Checksum");
    r->headers_out.content_length_n = 123;
    r->allow_ranges = 1;

    TEST_ASSERT(ngx_http_markdown_send_412(r) == NGX_DONE,
                "send_412 succeeds");
    TEST_ASSERT(r->headers_out.status == NGX_HTTP_PRECONDITION_FAILED,
                "412 status is set");
    TEST_ASSERT(r->header_only == 1,
                "412 request is marked header-only (no response body)");
    TEST_ASSERT(r->headers_out.content_length_n == 0,
                "412 response is framed with an empty body");
    TEST_ASSERT(r->allow_ranges == 0,
                "range state cleared on 412");
    TEST_ASSERT(count_vary_headers(r) == 1,
                "Vary: Accept retained on 412");

    TEST_PASS("412 clears body headers and retains Vary: Accept");
}

/* A failed header operation during 412 preparation must restore the exact
 * upstream representation (same failure contract as the 304 path). */
static void
test_send_412_failure_restores_headers(void)
{
    ngx_http_request_t *r;
    ngx_table_elt_t    *original_etag;
    ngx_uint_t          original_header_count;

    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    r->headers_out.trailers = *create_header_list();
    original_etag = fill_response_headers(r);
    original_header_count = r->headers_out.headers.part.nelts;

    TEST_ASSERT(ngx_http_markdown_send_412(r) == NGX_ERROR,
                "Vary allocation failure returns NGX_ERROR");
    TEST_ASSERT(r->headers_out.status == 200,
                "412 failure restores status");
    TEST_ASSERT(r->headers_out.content_length_n == 123,
                "412 failure restores Content-Length");
    TEST_ASSERT(r->allow_ranges == 1,
                "412 failure restores range state");
    TEST_ASSERT(original_etag->hash == 1,
                "412 failure restores ETag");
    TEST_ASSERT(r->headers_out.headers.part.nelts == original_header_count,
                "412 failure restores header list length");

    TEST_PASS("412 failure restores the upstream representation");
}

static void
test_send_412_send_header_again(void)
{
    ngx_http_request_t  *r;

    g_pool_offset = 0;
    g_send_header_rc = NGX_AGAIN;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    TEST_ASSERT(ngx_http_markdown_send_412(r) == NGX_AGAIN,
                "send_412 preserves downstream NGX_AGAIN");
    TEST_ASSERT(r->headers_out.status == NGX_HTTP_PRECONDITION_FAILED
                && r->header_only == 1,
                "412 representation remains prepared for resume");
    TEST_PASS("412 header backpressure");
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
    ngx_table_elt_t *upstream_etag;
    ngx_table_elt_t *upstream_trailer;
    ngx_uint_t original_header_count;

    g_pool_offset = 0;
    g_send_header_rc = NGX_ERROR;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    r->headers_out.trailers = *create_header_list();
    r->headers_out.status = 200;
    r->headers_out.status_line.data = (u_char *) "OK";
    r->headers_out.status_line.len = 2;
    r->headers_out.content_type.data = (u_char *) "text/html";
    r->headers_out.content_type.len = sizeof("text/html") - 1;
    r->headers_out.content_type_len = sizeof("text/html") - 1;
    r->headers_out.content_length_n = 123;
    r->allow_ranges = 1;
    upstream_etag = add_header(&r->headers_out.headers,
                               "ETag", "\"upstream\"");
    r->headers_out.etag = upstream_etag;
    upstream_trailer = add_header(&r->headers_out.trailers,
                                  "Digest", "sha-256=upstream");
    original_header_count = r->headers_out.headers.part.nelts;

    struct MarkdownResult result;
    memset(&result, 0, sizeof(result));

    ngx_int_t rc = ngx_http_markdown_send_304(r, &result);
    TEST_ASSERT(rc == NGX_ERROR, "send_304 returns NGX_ERROR on header fail");
    assert_304_failure_restored(r, upstream_etag, upstream_trailer,
                                original_header_count);
    TEST_PASS("send_304 header failure");
}

static void
test_send_304_send_header_again(void)
{
    g_pool_offset = 0;
    g_send_header_rc = NGX_AGAIN;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    struct MarkdownResult result;
    memset(&result, 0, sizeof(result));

    ngx_int_t rc = ngx_http_markdown_send_304(r, &result);
    TEST_ASSERT(rc == NGX_AGAIN,
                "send_304 must preserve downstream NGX_AGAIN");
    TEST_PASS("send_304 header backpressure");
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

static void
test_capture_restore_conditional_headers(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *inm;
    ngx_table_elt_t *ims;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    inm = add_header(&r->headers_in.headers,
                     "if-none-match", "\"source\"");
    ims = add_header(&r->headers_in.headers,
                     "If-Modified-Since", "Wed, 21 Oct 2015 07:28:00 GMT");
    r->headers_in.if_none_match = inm;
    r->headers_in.if_modified_since = ims;
    memset(&ctx, 0, sizeof(ctx));

    TEST_ASSERT(ngx_http_markdown_has_conditional_request(r),
        "active conditional validator is detected");
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                == NGX_OK,
        "conditional validators are captured");
    TEST_ASSERT(ctx.conditional.captured && ctx.conditional.suppressed,
        "capture records suppressed state");
    TEST_ASSERT(inm->hash == 0 && ims->hash == 0,
        "captured validators are hidden from upstream processing");
    TEST_ASSERT(inm->value.len == 0 && ims->value.len == 0,
        "captured validator values are emptied for upstream forwarders");
    TEST_ASSERT(r->headers_in.if_none_match == NULL
                && r->headers_in.if_modified_since == NULL,
        "typed validator pointers are hidden");

    ngx_http_markdown_restore_conditional_request(r, &ctx);
    TEST_ASSERT(!ctx.conditional.suppressed,
        "restore clears suppressed state");
    TEST_ASSERT(inm->hash != 0 && ims->hash != 0,
        "captured validators regain their original hash state");
    TEST_ASSERT(inm->value.len == sizeof("\"source\"") - 1
                && ims->value.len
                   == sizeof("Wed, 21 Oct 2015 07:28:00 GMT") - 1,
        "captured validator values regain their original lengths");
    TEST_ASSERT(r->headers_in.if_none_match == inm
                && r->headers_in.if_modified_since == ims,
        "typed validator pointers are restored");
    TEST_PASS("capture and restore conditional headers");
}

static void
test_adopt_orphan_conditional_headers(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conditional_ownership_t ownership;
    ngx_table_elt_t *inm;
    ngx_table_elt_t *ims;
    ngx_int_t rc;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    inm = add_header(&r->headers_in.headers,
                     "if-none-match", "\"source\"");
    ims = add_header(&r->headers_in.headers,
                     "If-Modified-Since", "Wed, 21 Oct 2015 07:28:00 GMT");
    r->headers_in.if_none_match = inm;
    r->headers_in.if_modified_since = ims;
    memset(&ctx, 0, sizeof(ctx));

    /* First capture suppresses both validators (hash=0), as PREACCESS does
     * before an internal redirect. */
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                == NGX_OK,
        "conditional validators are captured");
    TEST_ASSERT(inm->hash == 0 && ims->hash == 0,
        "captured validators are hidden");

    /* Internal redirect clears r->ctx (module context array) but leaves the
     * suppressed request-header entries behind.  The orphan-adopt helper
     * restores their visibility so the next PREACCESS pass can re-capture. */
    memset(&ctx, 0, sizeof(ctx));  /* simulates the lost context */
    memset(&ownership, 0, sizeof(ownership));
    rc = ngx_http_markdown_adopt_orphan_conditional_headers(
        r, NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT, &ownership);
    TEST_ASSERT(rc == NGX_OK && ownership.entry_count == 2
                && ownership.adopter
                   == NGX_HTTP_MARKDOWN_CONDITIONAL_ADOPTER_PREACCESS
                && ownership.phase
                   == NGX_HTTP_MARKDOWN_CONDITIONAL_PHASE_PREACCESS,
                "orphan adoption records its owner and phase");
    TEST_ASSERT(inm->hash != 0 && ims->hash != 0,
        "orphaned validators regain visibility after internal redirect");
    TEST_ASSERT(inm->value.len == sizeof("\"source\"") - 1
                && ims->value.len
                   == sizeof("Wed, 21 Oct 2015 07:28:00 GMT") - 1,
        "orphaned validator values remain intact");
    TEST_ASSERT(r->headers_in.if_none_match == inm
                && r->headers_in.if_modified_since == ims,
        "orphan adoption rebuilds typed validator pointers");

    /* A fresh capture on the re-adopted headers must succeed. */
    TEST_ASSERT(ngx_http_markdown_has_conditional_request(r),
        "re-adopted conditional validator is detected");
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                == NGX_OK,
        "re-capture after orphan adoption succeeds");
    TEST_ASSERT(inm->hash == 0 && ims->hash == 0,
        "re-captured validators are hidden again");

    TEST_PASS("adopt orphan conditional headers after internal redirect");
}

static void
test_adopt_orphan_restores_repeated_validators(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *inm1;
    ngx_table_elt_t *inm2;
    ngx_table_elt_t *ims;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    /* Repeated If-None-Match fields: capture records a state entry for
     * each one, so orphan adoption must restore every entry, not just
     * the first match. */
    inm1 = add_header(&r->headers_in.headers,
                      "if-none-match", "\"first\"");
    inm2 = add_header(&r->headers_in.headers,
                      "if-none-match", "\"second\"");
    ims = add_header(&r->headers_in.headers,
                     "If-Modified-Since", "Wed, 21 Oct 2015 07:28:00 GMT");
    r->headers_in.if_none_match = inm1;
    r->headers_in.if_modified_since = ims;
    memset(&ctx, 0, sizeof(ctx));

    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                == NGX_OK,
        "repeated validators are captured");
    TEST_ASSERT(inm1->hash == 0 && inm2->hash == 0 && ims->hash == 0,
        "all repeated validator entries are suppressed");
    TEST_ASSERT(inm1->value.len == 0 && inm2->value.len == 0,
        "all repeated validator values are emptied");

    /* Internal redirect loses the context; adoption must restore every
     * suppressed entry and rebuild the typed pointer from the first one. */
    memset(&ctx, 0, sizeof(ctx));
    TEST_ASSERT(ngx_http_markdown_adopt_orphan_conditional_headers(
                    r, NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT,
                    NULL) == NGX_OK,
                "repeated orphan validators are adopted");
    TEST_ASSERT(inm1->hash != 0 && inm2->hash != 0 && ims->hash != 0,
        "every repeated validator regains visibility");
    TEST_ASSERT(inm1->value.len == sizeof("\"first\"") - 1
                && inm2->value.len == sizeof("\"second\"") - 1,
        "every repeated validator value length is rebuilt");
    TEST_ASSERT(r->headers_in.if_none_match == inm1,
        "typed pointer names the first restored entry");

    /* A fresh capture re-owns all entries again. */
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                == NGX_OK,
        "re-capture after adoption owns repeated entries");
    TEST_ASSERT(inm1->hash == 0 && inm2->hash == 0,
        "re-capture suppresses repeated entries again");

    TEST_PASS("orphan adoption restores repeated validators");
}

static void
test_adopt_orphan_uses_saved_length_for_non_nul_value(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *valid;
    ngx_table_elt_t *non_nul_header;
    u_char *non_nul;
    const size_t original_len = sizeof(
        "Wed, 21 Oct 2015 07:28:00 GMT") - 1;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    valid = add_header(&r->headers_in.headers,
                       "If-None-Match", "\"valid\"");
    non_nul_header = add_header(&r->headers_in.headers,
                                "If-Modified-Since", "date");
    r->headers_in.if_none_match = valid;
    r->headers_in.if_modified_since = non_nul_header;

    /* Install the non-NUL backing storage before capture.  Capture clears
     * value.len, reproducing the internal-redirect state that previously
     * made adoption scan past the request-header allocation. */
    non_nul = ngx_palloc(r->pool, original_len);
    if (non_nul == NULL) { TEST_FAIL("alloc non_nul failed"); return; }
    memset(non_nul, 'A', original_len);
    non_nul_header->value.data = non_nul;
    non_nul_header->value.len = original_len;
    memset(&ctx, 0, sizeof(ctx));

    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                    == NGX_OK,
        "both validators are captured and hidden");
    TEST_ASSERT(valid->hash == 0 && non_nul_header->hash == 0,
                "both entries are suppressed before redirect");

    memset(&ctx, 0, sizeof(ctx));
    TEST_ASSERT(ngx_http_markdown_adopt_orphan_conditional_headers(
                    r, 8192, NULL) == NGX_OK,
                "orphan adoption uses the saved length without a NUL scan");
    TEST_ASSERT(valid->hash != 0 && non_nul_header->hash != 0,
                "both saved validator entries are restored");
    TEST_ASSERT(non_nul_header->value.len == original_len,
                "the saved original length is restored");
    TEST_ASSERT(r->headers_in.if_modified_since == non_nul_header,
                "typed pointer is rebuilt for the non-NUL value");

    TEST_PASS("orphan adoption uses saved length for non-NUL value");
}

static void
test_adopt_orphan_rejects_saved_length_over_limit(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *header;
    u_char *large_value;
    const size_t scan_limit = 8192;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    header = add_header(&r->headers_in.headers, "If-None-Match", "");
    if (header == NULL) { TEST_FAIL("header alloc failed"); return; }
    large_value = ngx_palloc(r->pool, scan_limit + 1);
    if (large_value == NULL) {
        TEST_FAIL("large value allocation failed");
        return;
    }
    memset(large_value, 'A', scan_limit + 1);
    large_value[scan_limit] = '\0';
    header->value.data = large_value;
    header->value.len = scan_limit + 1;
    r->headers_in.if_none_match = header;
    memset(&ctx, 0, sizeof(ctx));

    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                    == NGX_OK,
                "oversized validator is captured before adoption");
    TEST_ASSERT(ngx_http_markdown_adopt_orphan_conditional_headers(
                    r, scan_limit, NULL) == NGX_ERROR,
                "saved value length beyond the bound is rejected");
    TEST_ASSERT(header->hash == 0 && header->value.len == 0,
                "oversized validator remains suppressed after rejection");
    TEST_ASSERT(r->headers_in.if_none_match == NULL,
                "typed pointer is not rebuilt after rejection");
    TEST_PASS("orphan adoption rejects saved length over limit");
}

static void
test_adopt_orphan_with_empty_headers(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_ctx_t ctx;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    memset(&ctx, 0, sizeof(ctx));
    TEST_ASSERT(ngx_http_markdown_adopt_orphan_conditional_headers(
                    r, NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT,
                    NULL) == NGX_OK,
                "empty headers are accepted");
    TEST_ASSERT(r->headers_in.if_none_match == NULL
                && r->headers_in.if_modified_since == NULL,
                "empty headers leave typed pointers NULL");
    TEST_PASS("orphan adoption with empty headers");
}

static void
test_adopt_orphan_skips_invalid_len(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *h;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    h = add_header(&r->headers_in.headers,
                   "If-None-Match", "\"valid\"");
    r->headers_in.if_none_match = h;
    memset(&ctx, 0, sizeof(ctx));
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                == NGX_OK,
                "capture succeeds");
    h->value.len = 5;
    h->value.data = (u_char *) "\"valid\"";
    memset(&ctx, 0, sizeof(ctx));
    TEST_ASSERT(ngx_http_markdown_adopt_orphan_conditional_headers(
                    r, NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT,
                    NULL) == NGX_OK,
                "invalidated value length is skipped");
    TEST_ASSERT(h->hash == 0,
                "entry with non-zero len after hash clear is not adopted");
    TEST_ASSERT(r->headers_in.if_none_match == NULL,
                "typed pointer not rebuilt for invalid len entry");
    TEST_PASS("orphan adoption skips entry with non-zero len");
}

static void
test_adopt_orphan_skips_null_data(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *h;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    h = add_header(&r->headers_in.headers,
                   "If-Modified-Since", "Wed, 21 Oct 2015 07:28:00 GMT");
    r->headers_in.if_modified_since = h;
    memset(&ctx, 0, sizeof(ctx));
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                == NGX_OK,
                "capture succeeds");
    h->value.data = NULL;
    h->value.len = 0;
    memset(&ctx, 0, sizeof(ctx));
    TEST_ASSERT(ngx_http_markdown_adopt_orphan_conditional_headers(
                    r, NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT,
                    NULL) == NGX_OK,
                "invalidated NULL value is skipped");
    TEST_ASSERT(h->hash == 0,
                "entry with NULL data remains hidden");
    TEST_ASSERT(r->headers_in.if_modified_since == NULL,
                "typed pointer not rebuilt for NULL data");
    TEST_PASS("orphan adoption skips entry with NULL data");
}

static void
test_capture_conditional_headers_excludes_range(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *ims;
    ngx_table_elt_t *range;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    ims = add_header(&r->headers_in.headers,
                     "If-Modified-Since", "Wed, 21 Oct 2015 07:28:00 GMT");
    range = add_header(&r->headers_in.headers, "Range", "bytes=0-10");
    r->headers_in.if_modified_since = ims;
    memset(&ctx, 0, sizeof(ctx));

    TEST_ASSERT(!ngx_http_markdown_has_conditional_request(r),
        "range request is not a validator-capture candidate");
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                == NGX_DECLINED,
        "range request is declined by capture");
    TEST_ASSERT(ims->hash != 0 && range->hash != 0,
        "range request headers remain active");
    TEST_PASS("range conditional request remains source-scoped");
}

static void
test_subrequest_capture_does_not_mutate_shared_validators(void)
{
    ngx_http_request_t *parent;
    ngx_http_request_t *subrequest;
    ngx_http_markdown_ctx_t parent_ctx;
    ngx_http_markdown_ctx_t subrequest_ctx;
    ngx_table_elt_t *validator;

    g_pool_offset = 0;
    parent = make_req();
    subrequest = make_req();
    if (parent == NULL || subrequest == NULL) {
        TEST_FAIL("alloc failed");
        return;
    }

    validator = add_header(&parent->headers_in.headers,
                           "If-None-Match", "\"parent-etag\"");
    parent->headers_in.if_none_match = validator;
    subrequest->headers_in.headers = parent->headers_in.headers;
    subrequest->headers_in.if_none_match = validator;
    subrequest->parent = parent;
    memset(&parent_ctx, 0, sizeof(parent_ctx));
    memset(&subrequest_ctx, 0, sizeof(subrequest_ctx));

    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(
                    parent, &parent_ctx) == NGX_OK,
                "parent must capture the shared validator");
    TEST_ASSERT(validator->hash == 0 && validator->value.len == 0,
                "parent capture must suppress the shared validator");
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(
                    subrequest, &subrequest_ctx) == NGX_DECLINED,
                "subrequest capture must decline shared validators");
    TEST_ASSERT(!subrequest_ctx.conditional.captured
                && !subrequest_ctx.conditional.suppressed
                && validator->hash == 0 && validator->value.len == 0,
                "subrequest capture must not alter parent suppression");

    ngx_http_markdown_restore_conditional_request(parent, &parent_ctx);
    TEST_ASSERT(validator->hash != 0
                && validator->value.len == sizeof("\"parent-etag\"") - 1,
                "parent restore must remain responsible for shared headers");
    TEST_PASS("subrequest capture preserves shared parent validators");
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
test_handle_inm_repeated_fields_match_304(void)
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
    ngx_table_elt_t *first = add_header(&r->headers_in.headers,
                                        "If-None-Match", "\"different\"");
    ngx_table_elt_t *second = add_header(&r->headers_in.headers,
                                         "If-None-Match", "\"abc123\"");
    r->headers_in.if_none_match = first;

    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    ngx_http_markdown_ctx_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 1;
    ctx.buffer.size = 100;
    ctx.buffer.data = (u_char *) "test data";

    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                == NGX_OK,
        "repeated If-None-Match fields are captured");

    struct MarkdownConverterHandle converter;
    struct MarkdownResult *result = NULL;
    ngx_int_t rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_HTTP_NOT_MODIFIED,
        "a matching later If-None-Match field returns 304");
    TEST_ASSERT(result != NULL, "Result is set for repeated validators");
    TEST_ASSERT(g_decide_if_none_match_len
                == sizeof("\"different\", \"abc123\"") - 1,
        "all If-None-Match fields reach the decision input");
    TEST_ASSERT(memcmp(g_decide_if_none_match,
                       "\"different\", \"abc123\"",
                       g_decide_if_none_match_len) == 0,
        "If-None-Match fields are comma-combined in order");
    TEST_ASSERT(first->hash == 0 && second->hash == 0,
        "all repeated validators remain suppressed during conversion");
    TEST_PASS("repeated If-None-Match fields match 304");
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

static void
prepare_full_conditional_test(ngx_http_markdown_conf_t *conf,
    ngx_http_markdown_ctx_t *ctx)
{
    memset(conf, 0, sizeof(*conf));
    conf->policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    conf->policy.generate_etag = 1;

    memset(ctx, 0, sizeof(*ctx));
    ctx->buffer_initialized = 1;
    ctx->buffer.size = 100;
    ctx->buffer.data = (u_char *) "test data";
}

static ngx_table_elt_t *
add_last_modified_header(ngx_http_request_t *r, const char *value)
{
    ngx_table_elt_t  *header;

    header = add_header(&r->headers_out.headers, "Last-Modified", value);
    if (header != NULL) {
        r->headers_out.last_modified = header;
    }

    return header;
}

static void
test_handle_if_match_mismatch_returns_412(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_match;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;
    static uint8_t             etag_data[] = "\"abc123\"";

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    g_convert_etag = etag_data;
    g_convert_etag_len = sizeof(etag_data) - 1;
    g_cond_result_code = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_match = add_header(&r->headers_in.headers,
                          "If-Match", "\"different\"");
    r->headers_in.if_match = if_match;
    prepare_full_conditional_test(&conf, &ctx);
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx) == NGX_OK,
        "If-Match is captured before the generated ETag is evaluated");
    TEST_ASSERT(r->headers_in.if_match == NULL,
        "captured If-Match is suppressed from the upstream path");
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_HTTP_PRECONDITION_FAILED,
        "If-Match mismatch returns 412");
    TEST_ASSERT(result == NULL,
        "failed If-Match does not publish a conversion result");
    TEST_ASSERT(g_convert_calls == 1,
        "If-Match comparison uses the generated entity ETag");
    TEST_PASS("If-Match mismatch returns PRECONDITION_FAILED");
}

static void
test_handle_if_match_match_preserves_304(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_match;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;
    static uint8_t             etag_data[] = "\"abc123\"";

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    g_convert_etag = etag_data;
    g_convert_etag_len = sizeof(etag_data) - 1;
    g_cond_result_code = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_match = add_header(&r->headers_in.headers,
                          "If-Match", "\"abc123\"");
    r->headers_in.if_match = if_match;
    add_header(&r->headers_in.headers,
               "If-None-Match", "\"abc123\"");
    prepare_full_conditional_test(&conf, &ctx);
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_HTTP_NOT_MODIFIED,
        "matching If-Match preserves the matching If-None-Match 304");
    TEST_ASSERT(result != NULL, "matching conditional request publishes result");
    TEST_PASS("If-Match match preserves 304");
}

static void
test_handle_if_match_wildcard_passes(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_match;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;
    static uint8_t             etag_data[] = "\"generated\"";

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    g_convert_etag = etag_data;
    g_convert_etag_len = sizeof(etag_data) - 1;
    g_cond_result_code = 1;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_match = add_header(&r->headers_in.headers, "If-Match", "*");
    r->headers_in.if_match = if_match;
    prepare_full_conditional_test(&conf, &ctx);
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "If-Match wildcard passes for any generated entity ETag");
    TEST_ASSERT(result != NULL, "wildcard If-Match keeps the conversion result");
    TEST_ASSERT(g_convert_calls == 1,
        "wildcard If-Match still obtains the generated representation");
    TEST_PASS("If-Match wildcard passes");
}

static void
test_handle_if_match_without_etag_comparison_fails_closed(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_match;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    g_convert_calls = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_match = add_header(&r->headers_in.headers,
                          "If-Match", "\"different\"");
    r->headers_in.if_match = if_match;
    prepare_full_conditional_test(&conf, &ctx);
    conf.policy.generate_etag = 0;
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_HTTP_PRECONDITION_FAILED,
        "non-wildcard If-Match fails closed without an entity ETag");
    TEST_ASSERT(result == NULL,
        "failed no-comparison If-Match publishes no result");
    TEST_ASSERT(g_convert_calls == 0,
        "no-comparison If-Match does not convert the source");

    r = make_req();
    if (r == NULL) { TEST_FAIL("wildcard request allocation failed"); return; }
    if_match = add_header(&r->headers_in.headers, "If-Match", "*");
    r->headers_in.if_match = if_match;
    prepare_full_conditional_test(&conf, &ctx);
    conf.policy.generate_etag = 0;
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, NULL, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "wildcard If-Match passes when entity comparison is unavailable");
    TEST_PASS("If-Match without ETag comparison fails closed");
}

static void
test_handle_oversized_inm_still_evaluates_if_match(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_none_match;
    ngx_table_elt_t          *if_match;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;
    u_char                    *oversized;
    const size_t               oversized_len =
        NGX_HTTP_MARKDOWN_IF_NONE_MATCH_MAX + 1;
    static uint8_t             etag_data[] = "\"abc123\"";

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    g_convert_etag = etag_data;
    g_convert_etag_len = sizeof(etag_data) - 1;
    g_cond_result_code = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_none_match = add_header(&r->headers_in.headers,
                               "If-None-Match", "ignored");
    if_match = add_header(&r->headers_in.headers,
                          "If-Match", "\"different\"");
    if (if_none_match == NULL || if_match == NULL) {
        TEST_FAIL("header allocation failed");
        return;
    }
    oversized = ngx_palloc(r->pool, oversized_len);
    if (oversized == NULL) {
        TEST_FAIL("oversized validator allocation failed");
        return;
    }
    memset(oversized, 'A', oversized_len);
    if_none_match->value.data = oversized;
    if_none_match->value.len = oversized_len;
    r->headers_in.if_none_match = if_none_match;
    r->headers_in.if_match = if_match;
    prepare_full_conditional_test(&conf, &ctx);
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_HTTP_PRECONDITION_FAILED,
        "oversized If-None-Match does not mask a failing If-Match");
    TEST_ASSERT(result == NULL,
        "failing If-Match after ignored oversized INM publishes no result");
    TEST_ASSERT(g_convert_calls == 1,
        "If-Match is evaluated after oversized If-None-Match is ignored");
    TEST_PASS("oversized If-None-Match still evaluates If-Match");
}

static void
test_handle_if_match_ignores_contradicting_if_unmodified_since(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_match;
    ngx_table_elt_t          *if_unmodified_since;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;
    static uint8_t             etag_data[] = "\"abc123\"";

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    g_convert_etag = etag_data;
    g_convert_etag_len = sizeof(etag_data) - 1;
    g_cond_result_code = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_match = add_header(&r->headers_in.headers, "If-Match", "\"abc123\"");
    r->headers_in.if_match = if_match;
    if_unmodified_since = add_header(&r->headers_in.headers,
        "If-Unmodified-Since", "Tue, 20 Oct 2015 07:28:00 GMT");
    r->headers_in.if_unmodified_since = if_unmodified_since;
    add_last_modified_header(r, "Wed, 21 Oct 2015 07:28:00 GMT");
    prepare_full_conditional_test(&conf, &ctx);
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    /* RFC 7232 section 3.3: If-Match takes precedence over
     * If-Unmodified-Since, which must be ignored when both are present.
     * A contradicting IUS must not turn the matched If-Match into 412. */
    TEST_ASSERT(rc == NGX_DECLINED,
        "satisfied If-Match ignores a contradicting If-Unmodified-Since");
    TEST_ASSERT(result != NULL,
        "If-Match satisfied by the generated representation publishes a result");
    TEST_ASSERT(g_convert_calls == 1,
        "If-Match match still obtains the generated representation");
    TEST_PASS("If-Match ignores contradicting If-Unmodified-Since");
}

static void
test_handle_if_unmodified_since_ignores_source_last_modified(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_unmodified_since;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    g_convert_calls = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_unmodified_since = add_header(&r->headers_in.headers,
        "If-Unmodified-Since", "Tue, 20 Oct 2015 07:28:00 GMT");
    r->headers_in.if_unmodified_since = if_unmodified_since;
    add_last_modified_header(r, "Wed, 21 Oct 2015 07:28:00 GMT");
    prepare_full_conditional_test(&conf, &ctx);
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "transformed Markdown ignores source Last-Modified for IUS");
    TEST_ASSERT(result == NULL,
        "date-only source validator does not publish a conversion result");
    TEST_ASSERT(g_convert_calls == 0,
        "source-scoped IUS does not trigger a conversion");
    TEST_PASS("transformed Markdown ignores source Last-Modified for IUS");
}

static void
test_handle_if_unmodified_since_after_last_modified_proceeds(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_unmodified_since;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_unmodified_since = add_header(&r->headers_in.headers,
        "If-Unmodified-Since", "Thu, 22 Oct 2015 07:28:00 GMT");
    r->headers_in.if_unmodified_since = if_unmodified_since;
    add_last_modified_header(r, "Wed, 21 Oct 2015 07:28:00 GMT");
    prepare_full_conditional_test(&conf, &ctx);
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "If-Unmodified-Since after Last-Modified proceeds normally");
    TEST_ASSERT(result == NULL,
        "date-only conditional request does not need an ETag conversion");
    TEST_PASS("If-Unmodified-Since after Last-Modified proceeds");
}

static void
test_handle_if_unmodified_since_without_last_modified_is_satisfied(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_unmodified_since;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_unmodified_since = add_header(&r->headers_in.headers,
        "If-Unmodified-Since", "Tue, 20 Oct 2015 07:28:00 GMT");
    r->headers_in.if_unmodified_since = if_unmodified_since;
    prepare_full_conditional_test(&conf, &ctx);
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "If-Unmodified-Since is satisfied without Last-Modified");
    TEST_ASSERT(result == NULL,
        "missing Last-Modified does not allocate a conversion result");
    TEST_ASSERT(g_convert_calls == 0,
        "date precondition without Last-Modified does not require ETag generation");
    TEST_PASS("If-Unmodified-Since without Last-Modified is satisfied");
}

static void
test_handle_preconditions_disabled(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_match;
    ngx_table_elt_t          *if_unmodified_since;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_match = add_header(&r->headers_in.headers,
                          "If-Match", "\"different\"");
    if_unmodified_since = add_header(&r->headers_in.headers,
        "If-Unmodified-Since", "Tue, 20 Oct 2015 07:28:00 GMT");
    r->headers_in.if_match = if_match;
    r->headers_in.if_unmodified_since = if_unmodified_since;
    add_last_modified_header(r, "Wed, 21 Oct 2015 07:28:00 GMT");
    prepare_full_conditional_test(&conf, &ctx);
    conf.policy.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_DECLINED,
        "disabled conditional mode declines If-Match and If-Unmodified-Since");
    TEST_ASSERT(result == NULL,
        "disabled conditional mode does not allocate a conversion result");
    TEST_ASSERT(g_convert_calls == 0,
        "disabled conditional mode does not generate an entity ETag");
    TEST_PASS("disabled mode declines all conditional preconditions");
}

static void
test_handle_if_match_failure_precedes_if_none_match(void)
{
    ngx_http_request_t       *r;
    ngx_http_markdown_conf_t  conf;
    ngx_http_markdown_ctx_t   ctx;
    ngx_table_elt_t          *if_match;
    struct MarkdownConverterHandle  converter;
    struct MarkdownResult    *result;
    ngx_int_t                  rc;
    static uint8_t             etag_data[] = "\"abc123\"";

    g_pool_offset = 0;
    g_prepare_options_rc = NGX_OK;
    g_convert_error_code = 0;
    g_convert_etag = etag_data;
    g_convert_etag_len = sizeof(etag_data) - 1;
    g_cond_result_code = 0;

    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    if_match = add_header(&r->headers_in.headers,
                          "If-Match", "\"different\"");
    r->headers_in.if_match = if_match;
    add_header(&r->headers_in.headers,
               "If-None-Match", "\"abc123\"");
    prepare_full_conditional_test(&conf, &ctx);
    result = NULL;

    rc = ngx_http_markdown_handle_if_none_match(
        r, &conf, &ctx, &converter, &result);
    TEST_ASSERT(rc == NGX_HTTP_PRECONDITION_FAILED,
        "If-Match failure takes precedence over matching If-None-Match");
    TEST_ASSERT(result == NULL,
        "precedence failure does not publish the converted result");
    TEST_PASS("If-Match failure wins over If-None-Match 304");
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

/* Verify every public conditional-request policy maps to its FFI value. */
static void
test_conditional_cache_validation_modes(void)
{
    TEST_ASSERT(ngx_http_markdown_conditional_cache_validation(
                    NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED) == 0,
                "disabled policy maps to no cache validation");
    TEST_ASSERT(ngx_http_markdown_conditional_cache_validation(
                    NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT) == 2,
                "full policy maps to entity validation");
    TEST_ASSERT(ngx_http_markdown_conditional_cache_validation(
                    NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE) == 1,
                "IMS policy maps to date validation");
    TEST_ASSERT(ngx_http_markdown_conditional_cache_validation(99) == 1,
                "unknown policy uses the IMS fallback");
    TEST_PASS("conditional cache validation policy mapping");
}

/* Ensure invalidated headers are ignored and HTAB is a valid OWS separator. */
static void
test_has_no_transform_ignores_invalidated_and_accepts_htab(void)
{
    ngx_http_request_t *r;
    ngx_table_elt_t *invalidated;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    invalidated = add_header(&r->headers_out.headers, "Cache-Control",
                             "no-transform");
    add_header(&r->headers_out.headers, "Cache-Control",
               "no-transform\t, max-age=0");
    if (invalidated == NULL) { TEST_FAIL("header alloc failed"); return; }
    invalidated->hash = 0;

    TEST_ASSERT(ngx_http_markdown_has_no_transform(r) == 1,
                "active Cache-Control uses HTAB as a valid separator");
    TEST_PASS("no-transform skips invalidated headers and accepts HTAB");
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

static void
test_collect_inm_request_list_branches(void)
{
    ngx_http_request_t *r;
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_ctx_t ctx;
    ngx_table_elt_t *inactive;
    ngx_table_elt_t *empty;
    struct MarkdownConverterHandle converter;
    struct MarkdownResult *result;

    g_pool_offset = 0;
    g_cond_result_code = 1;
    g_convert_error_code = 0;
    g_convert_etag = NULL;
    g_convert_etag_len = 0;

    r = make_req();
    if (r == NULL) {
        TEST_FAIL("request allocation failed");
        return;
    }

    inactive = add_header(&r->headers_in.headers, "X-Irrelevant", "skip");
    inactive->hash = 0;
    add_header(&r->headers_in.headers, "If-None-Match", "\"first\"");
    empty = add_header(&r->headers_in.headers, "If-None-Match", "");
    empty->value.data = NULL;
    add_header(&r->headers_in.headers, "X-Other", "skip");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;
    memset(&ctx, 0, sizeof(ctx));
    ctx.buffer_initialized = 1;
    ctx.buffer.size = 1;
    ctx.buffer.data = (u_char *) "x";
    result = NULL;

    TEST_ASSERT(ngx_http_markdown_handle_if_none_match(
                    r, &conf, &ctx, &converter, &result) == NGX_DECLINED,
                "request-list If-None-Match mismatch is declined");
    TEST_ASSERT(g_decide_if_none_match_len == sizeof("\"first\", ") - 1,
                "request-list values include the empty-field separator");
    TEST_ASSERT(memcmp(g_decide_if_none_match, "\"first\", ",
                       g_decide_if_none_match_len) == 0,
                "request-list values preserve order and skip other headers");
    TEST_PASS("request If-None-Match list branches exercised");
}

static void
test_collect_inm_request_list_error_guards(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_if_none_match_measurement_t measurement;
    ngx_str_t out;
    u_char copied[8];

    memset(&r, 0, sizeof(r));
    r.headers_in.headers.part.elts = NULL;
    r.headers_in.headers.part.nelts = 1;
    memset(&measurement, 0, sizeof(measurement));

    TEST_ASSERT(ngx_http_markdown_measure_request_if_none_match(
                    &r, &measurement) == NGX_ERROR,
                "request measurement rejects a malformed list part");
    TEST_ASSERT(ngx_http_markdown_copy_request_if_none_match(&r, copied)
                    == copied,
                "request copy returns its input on a malformed list part");

    out.data = (u_char *) "stale";
    out.len = 5;
    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    &r, NULL, &out) == NGX_ERROR,
                "request collection propagates a malformed list part");
    TEST_ASSERT(out.data == NULL && out.len == 0,
                "failed request collection clears its output");
    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    NULL, NULL, &out) == NGX_ERROR,
                "request collection rejects a NULL request");
    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    &r, NULL, NULL) == NGX_ERROR,
                "request collection rejects a NULL output");
    TEST_PASS("request If-None-Match list error guards exercised");
}

static void
test_collect_inm_captured_fallback_paths(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conditional_header_state_t state;
    ngx_table_elt_t fallback;
    ngx_str_t out;

    memset(&r, 0, sizeof(r));
    memset(&ctx, 0, sizeof(ctx));
    memset(&state, 0, sizeof(state));
    memset(&fallback, 0, sizeof(fallback));

    fallback.key.data = (u_char *) "If-Modified-Since";
    fallback.key.len = sizeof("If-Modified-Since") - 1;
    fallback.value.data = (u_char *) "date";
    fallback.value.len = 4;
    state.header = &fallback;
    state.original_value_len = 4;
    ctx.conditional.captured = 1;
    ctx.conditional.header_states = &state;
    ctx.conditional.if_none_match = &fallback;

    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    &r, &ctx, &out) == NGX_OK,
                "captured typed fallback is collected");
    TEST_ASSERT(out.data == fallback.value.data && out.len == 4,
                "captured value length comes from its saved state");

    fallback.value.data = NULL;
    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    &r, &ctx, &out) == NGX_ERROR,
                "captured fallback rejects a missing non-empty value");

    fallback.value.data = (u_char *) "date";
    state.original_value_len = NGX_HTTP_MARKDOWN_IF_NONE_MATCH_MAX + 1;
    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    &r, &ctx, &out) == NGX_ERROR,
                "captured fallback rejects an oversized value");

    ctx.conditional.if_none_match = NULL;
    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    &r, &ctx, &out) == NGX_DECLINED,
                "captured collection declines without a typed fallback");

    fallback.key.data = (u_char *) "If-None-Match";
    fallback.key.len = sizeof("If-None-Match") - 1;
    fallback.value.data = NULL;
    fallback.value.len = 4;
    state.original_value_len = 4;
    ctx.conditional.if_none_match = &fallback;
    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    &r, &ctx, &out) == NGX_ERROR,
                "captured measurement errors reach the fallback helper");
    TEST_PASS("captured If-None-Match fallback paths exercised");
}

static void
test_collect_inm_captured_copy_and_alloc_failure(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conditional_header_state_t first_state;
    ngx_http_markdown_conditional_header_state_t second_state;
    ngx_http_markdown_if_none_match_measurement_t measurement;
    ngx_table_elt_t first;
    ngx_table_elt_t second;
    ngx_str_t out;

    g_pool_offset = 0;
    g_pool_allocations = 0;
    g_pool_fail_at = (size_t) -1;
    memset(&r, 0, sizeof(r));
    memset(&ctx, 0, sizeof(ctx));
    memset(&first_state, 0, sizeof(first_state));
    memset(&second_state, 0, sizeof(second_state));
    memset(&first, 0, sizeof(first));
    memset(&second, 0, sizeof(second));

    first.key.data = (u_char *) "If-None-Match";
    first.key.len = sizeof("If-None-Match") - 1;
    first.value.data = (u_char *) "one";
    first.value.len = 3;
    second.key.data = (u_char *) "If-None-Match";
    second.key.len = sizeof("If-None-Match") - 1;
    second.value.data = NULL;
    second.value.len = 0;
    first_state.header = &first;
    first_state.original_value_len = 3;
    first_state.next = &second_state;
    second_state.header = &second;
    second_state.original_value_len = 0;
    ctx.conditional.captured = 1;
    ctx.conditional.header_states = &first_state;
    ctx.conditional.if_none_match = &first;

    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    &r, &ctx, &out) == NGX_OK,
                "captured values are copied into a combined buffer");
    TEST_ASSERT(out.len == sizeof("one, ") - 1
                && memcmp(out.data, "one, ", out.len) == 0,
                "captured copy preserves separators and empty values");

    first.value.data = NULL;
    memset(&measurement, 0, sizeof(measurement));
    TEST_ASSERT(ngx_http_markdown_measure_captured_if_none_match(
                    &ctx, &measurement) == NGX_ERROR,
                "captured measurement rejects a missing non-empty value");

    first.value.data = (u_char *) "one";
    g_pool_fail_at = g_pool_allocations;
    TEST_ASSERT(ngx_http_markdown_collect_if_none_match_value(
                    &r, &ctx, &out) == NGX_ERROR,
                "captured collection propagates pool allocation failure");
    TEST_PASS("captured If-None-Match copy and allocation paths exercised");
}

static void
test_capture_conditional_state_paths(void)
{
    ngx_http_request_t *r;
    ngx_http_request_t *failed_request;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_ctx_t failed_ctx;

    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(NULL, NULL)
                    == NGX_ERROR,
                "capture rejects NULL request and context");

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) {
        TEST_FAIL("request allocation failed");
        return;
    }
    add_header(&r->headers_in.headers, "If-None-Match", "\"one\"");
    add_header(&r->headers_in.headers, "If-Modified-Since", "date");
    memset(&ctx, 0, sizeof(ctx));

    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                    == NGX_OK,
                "initial conditional capture succeeds");
    ngx_http_markdown_restore_conditional_request(r, &ctx);
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(r, &ctx)
                    == NGX_OK,
                "repeated conditional capture reuses its state chain");

    g_pool_offset = 0;
    failed_request = make_req();
    if (failed_request == NULL) {
        TEST_FAIL("failed-request allocation failed");
        return;
    }
    ngx_table_elt_t *failed_header = add_header(
        &failed_request->headers_in.headers, "If-None-Match", "\"one\"");
    memset(&failed_ctx, 0, sizeof(failed_ctx));
    g_pool_fail_at = g_pool_allocations;
    TEST_ASSERT(ngx_http_markdown_capture_conditional_request(
                    failed_request, &failed_ctx) == NGX_ERROR,
                "conditional capture propagates state allocation failure");
    TEST_ASSERT(failed_header->hash != 0,
                "failed capture leaves the request header unsuppressed");
    TEST_PASS("conditional capture state-chain paths exercised");
}

static void
test_conditional_helper_guards(void)
{
    ngx_http_markdown_if_none_match_measurement_t measurement;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conditional_header_state_t state;
    ngx_table_elt_t header;
    ngx_str_t key;
    struct FFIConditionalDecision decision;

    memset(&key, 0, sizeof(key));
    TEST_ASSERT(ngx_http_markdown_is_captured_conditional_name(NULL) == 0,
                "captured-name helper rejects NULL key");
    TEST_ASSERT(ngx_http_markdown_is_captured_conditional_name(&key) == 0,
                "captured-name helper rejects NULL key data");

    memset(&ctx, 0, sizeof(ctx));
    memset(&state, 0, sizeof(state));
    memset(&header, 0, sizeof(header));
    header.value.len = 99;
    TEST_ASSERT(ngx_http_markdown_header_has_cache_directive(
                    &header, (const u_char *) "private", 7) == 0,
                "cache directive helper rejects NULL value data");
    header.value.data = (u_char *) "private";
    header.value.len = 0;
    TEST_ASSERT(ngx_http_markdown_header_has_cache_directive(
                    &header, (const u_char *) "private", 7) == 0,
                "cache directive helper rejects empty values");
    header.value.data = NULL;
    header.value.len = 99;
    state.header = &header;
    state.original_value_len = 7;
    ctx.conditional.captured = 1;
    ctx.conditional.header_states = &state;
    TEST_ASSERT(ngx_http_markdown_conditional_value_len(&ctx, &header) == 7,
                "captured value length uses the saved original length");
    TEST_ASSERT(ngx_http_markdown_conditional_value_len(NULL, &header) == 99,
                "uncaptured value length uses the live header length");

    memset(&measurement, 0, sizeof(measurement));
    measurement.match_count = 1;
    measurement.total_len = (size_t) -1;
    TEST_ASSERT(ngx_http_markdown_add_if_none_match_length(
                    &measurement, 0) == NGX_ERROR,
                "If-None-Match separator overflow is rejected");

    memset(&measurement, 0, sizeof(measurement));
    measurement.total_len = NGX_HTTP_MARKDOWN_IF_NONE_MATCH_MAX;
    TEST_ASSERT(ngx_http_markdown_add_if_none_match_length(
                    &measurement, 1) == NGX_ERROR,
                "If-None-Match value cap overflow is rejected");

    TEST_ASSERT(ngx_http_markdown_has_conditional_request(NULL) == 0,
                "conditional-request helper rejects NULL request");

    memset(&decision, 0, sizeof(decision));
    decision.outcome = 0;
    TEST_ASSERT(ngx_http_markdown_conditional_early_outcome(&decision)
                    == NGX_HTTP_NOT_MODIFIED,
                "early outcome maps match to 304");
    decision.outcome = 2;
    TEST_ASSERT(ngx_http_markdown_conditional_early_outcome(&decision)
                    == NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT,
                "early outcome maps bypass to the module sentinel");
    decision.outcome = 1;
    TEST_ASSERT(ngx_http_markdown_conditional_early_outcome(&decision)
                    == NGX_DECLINED,
                "early outcome maps pending decisions to declined");
    TEST_PASS("conditional helper guards and early outcomes exercised");
}

static void
test_304_snapshot_multiple_headers(void)
{
    ngx_http_request_t *r;
    ngx_list_part_t second_part;
    ngx_table_elt_t second_entries[1];
    ngx_http_markdown_304_list_snapshot_t snapshot;
    ngx_table_elt_t *first;
    ngx_table_elt_t *second;

    g_pool_offset = 0;
    r = make_req();
    if (r == NULL) {
        TEST_FAIL("request allocation failed");
        return;
    }
    first = add_header(&r->headers_out.headers, "ETag", "\"one\"");
    second = add_header(&r->headers_out.headers, "Vary", "Accept");
    memset(&second_part, 0, sizeof(second_part));
    memset(second_entries, 0, sizeof(second_entries));
    second_entries[0].hash = 1;
    second_entries[0].key.data = (u_char *) "Cache-Control";
    second_entries[0].key.len = sizeof("Cache-Control") - 1;
    second_entries[0].value.data = (u_char *) "public";
    second_entries[0].value.len = sizeof("public") - 1;
    second_part.elts = second_entries;
    second_part.nelts = 1;
    r->headers_out.headers.part.next = &second_part;
    r->headers_out.headers.last = &second_part;

    memset(&snapshot, 0, sizeof(snapshot));
    TEST_ASSERT(ngx_http_markdown_304_snapshot_list(
                    r->pool, &r->headers_out.headers, &snapshot) == NGX_OK,
                "304 snapshot saves headers across multiple list parts");
    TEST_ASSERT(snapshot.entry_count == 3
                && snapshot.original_last == &second_part,
                "304 snapshot records every header and the list tail");

    first->hash = 0;
    first->value.data = (u_char *) "changed";
    second->hash = 0;
    second->value.data = (u_char *) "changed";
    second_entries[0].hash = 0;
    second_entries[0].value.data = (u_char *) "changed";
    r->headers_out.headers.last = &r->headers_out.headers.part;
    second_part.next = &r->headers_out.headers.part;

    ngx_http_markdown_304_restore_list(&r->headers_out.headers, &snapshot);
    TEST_ASSERT(r->headers_out.headers.last == &second_part
                && second_part.next == NULL,
                "304 restore reinstates the original list tail linkage");
    TEST_ASSERT(first->hash == 1 && first->value.len == sizeof("\"one\"") - 1,
                "304 restore reinstates the first header");
    TEST_ASSERT(second->hash == 1 && second->value.len == sizeof("Accept") - 1,
                "304 restore reinstates the second header");
    TEST_ASSERT(second_entries[0].hash == 1
                && second_entries[0].value.len == sizeof("public") - 1,
                "304 restore reinstates headers in the next list part");
    TEST_PASS("304 multi-part snapshot and restore exercised");
}

static void
test_304_snapshot_error_guards(void)
{
    ngx_http_markdown_304_list_snapshot_t snapshot;
    ngx_http_markdown_304_list_snapshot_t restore_snapshot;
    ngx_http_markdown_304_snapshot_entry_t saved_entry;
    ngx_list_t list;
    ngx_list_part_t part;
    ngx_table_elt_t entry;

    memset(&snapshot, 0, sizeof(snapshot));
    memset(&restore_snapshot, 0, sizeof(restore_snapshot));
    memset(&saved_entry, 0, sizeof(saved_entry));
    memset(&list, 0, sizeof(list));
    memset(&part, 0, sizeof(part));
    memset(&entry, 0, sizeof(entry));
    list.part = part;

    TEST_ASSERT(ngx_http_markdown_304_snapshot_list(
                    NULL, NULL, &snapshot) == NGX_ERROR,
                "304 snapshot rejects a NULL list");
    TEST_ASSERT(ngx_http_markdown_304_snapshot_list(
                    NULL, &list, NULL) == NGX_ERROR,
                "304 snapshot rejects a NULL snapshot");
    TEST_ASSERT(ngx_http_markdown_304_snapshot_list(
                    NULL, &list, &snapshot) == NGX_OK,
                "304 snapshot accepts an empty list");
    ngx_http_markdown_304_restore_list(&list, &snapshot);
    ngx_http_markdown_304_restore_list(NULL, &snapshot);
    ngx_http_markdown_304_restore_list(&list, NULL);

    list.part.nelts = NGX_HTTP_MARKDOWN_304_SNAPSHOT_MAX_ENTRIES + 1;
    TEST_ASSERT(ngx_http_markdown_304_snapshot_list(
                    NULL, &list, &snapshot) == NGX_ERROR,
                "304 snapshot rejects too many entries");

    g_pool_offset = 0;
    g_pool_allocations = 0;
    g_pool_fail_at = (size_t) -1;
    list.part.nelts = 1;
    list.part.elts = NULL;
    TEST_ASSERT(ngx_http_markdown_304_snapshot_list(
                    NULL, &list, &snapshot) == NGX_ERROR,
                "304 snapshot rejects NULL entry storage");

    list.part.elts = &entry;
    g_pool_fail_at = g_pool_allocations;
    TEST_ASSERT(ngx_http_markdown_304_snapshot_list(
                    NULL, &list, &snapshot) == NGX_ERROR,
                "304 snapshot propagates entry allocation failure");

    restore_snapshot.entry_count = 1;
    restore_snapshot.entries = &saved_entry;
    g_pool_fail_at = (size_t) -1;
    list.part.elts = NULL;
    list.part.nelts = 1;
    ngx_http_markdown_304_restore_list(&list, &restore_snapshot);
    list.part.elts = &entry;
    list.part.nelts = 2;
    ngx_http_markdown_304_restore_list(&list, &restore_snapshot);
    TEST_PASS("304 snapshot and restore error guards exercised");
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
    test_send_412_success_clears_body_headers();
    test_send_412_failure_restores_headers();
    test_send_412_send_header_again();
    test_send_304_replaces_existing_etag();
    test_auth_ignores_invalidated_authorization();
    test_send_304_null_result();
    test_send_304_empty_etag();
    test_send_304_send_header_fails();
    test_send_304_send_header_again();
    test_send_304_etag_failure_restores_headers();
    test_send_304_etag_value_failure_restores_headers();
    test_send_304_vary_failure_restores_headers();
    test_send_304_auth_cache_control_failure_restores_headers();

    test_find_header_null_name();
    test_find_header_found();
    test_find_header_not_found();
    test_find_header_hash_zero_skipped();
    test_capture_restore_conditional_headers();
    test_adopt_orphan_conditional_headers();
    test_adopt_orphan_restores_repeated_validators();
    test_adopt_orphan_uses_saved_length_for_non_nul_value();
    test_adopt_orphan_rejects_saved_length_over_limit();
    test_adopt_orphan_with_empty_headers();
    test_adopt_orphan_skips_invalid_len();
    test_adopt_orphan_skips_null_data();
    test_capture_conditional_headers_excludes_range();
    test_subrequest_capture_does_not_mutate_shared_validators();

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
    test_handle_inm_repeated_fields_match_304();
    test_collect_inm_request_list_branches();
    test_collect_inm_request_list_error_guards();
    test_collect_inm_captured_fallback_paths();
    test_collect_inm_captured_copy_and_alloc_failure();
    test_capture_conditional_state_paths();
    test_conditional_helper_guards();
    test_handle_inm_etag_mismatch();
    test_handle_inm_with_ims_header();
    test_handle_if_match_mismatch_returns_412();
    test_handle_if_match_match_preserves_304();
    test_handle_if_match_wildcard_passes();
    test_handle_if_match_without_etag_comparison_fails_closed();
    test_handle_oversized_inm_still_evaluates_if_match();
    test_handle_if_match_ignores_contradicting_if_unmodified_since();
    test_handle_if_unmodified_since_ignores_source_last_modified();
    test_handle_if_unmodified_since_after_last_modified_proceeds();
    test_handle_if_unmodified_since_without_last_modified_is_satisfied();
    test_handle_preconditions_disabled();
    test_handle_if_match_failure_precedes_if_none_match();

    test_handle_bypass_range_request();
    test_handle_bypass_no_transform();
    test_has_no_transform_in_comma_separated_list();
    test_has_no_transform_absent();
    test_has_no_transform_no_cache_control();
    test_conditional_cache_validation_modes();
    test_has_no_transform_ignores_invalidated_and_accepts_htab();
    test_304_snapshot_multiple_headers();
    test_304_snapshot_error_guards();

    test_handle_ims_only_scalar_time_not_consulted();
    test_handle_ims_only_no_last_modified_at_all();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
