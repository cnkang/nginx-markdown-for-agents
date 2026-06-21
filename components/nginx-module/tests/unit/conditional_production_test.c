#include "../include/test_common.h"
#include <strings.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

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

#ifndef NGX_LOG_ERR
#define NGX_LOG_ERR 3
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
        ngx_table_elt_t *etag;
        off_t      content_length_n;
    } headers_out;
};

struct ngx_module_s {
    int dummy;
};

ngx_module_t ngx_http_markdown_filter_module;
ngx_module_t ngx_http_core_module;

static u_char g_pool_buf[1024 * 64];
static size_t g_pool_offset;

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    void *p;
    UNUSED(pool);
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
static int g_prepare_options_rc;
static int g_cond_result_code;
static int g_convert_error_code;
static uint8_t *g_convert_etag;
static uintptr_t g_convert_etag_len;

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
}

void
ngx_http_clear_content_length(ngx_http_request_t *r)
{
    UNUSED(r);
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
markdown_check_conditional(const uint8_t *if_none_match,
    uintptr_t if_none_match_len,
    const uint8_t *etag, uintptr_t etag_len,
    const uint8_t *if_modified_since,
    uintptr_t if_modified_since_len,
    const uint8_t *vary_digest, uintptr_t vary_digest_len,
    struct FFIConditionalResult *result)
{
    UNUSED(if_none_match); UNUSED(if_none_match_len);
    UNUSED(etag); UNUSED(etag_len);
    UNUSED(if_modified_since); UNUSED(if_modified_since_len);
    UNUSED(vary_digest); UNUSED(vary_digest_len);
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
        result->result_code = (uint8_t) g_cond_result_code;
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
markdown_accept_result_init(struct FFIAcceptResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_conditional_result_init(struct FFIConditionalResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_decision_result_init(struct FFIDecisionResult *result)
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
    uintptr_t budget, struct FFIDecompResult *result)
{
    UNUSED(input); UNUSED(input_len); UNUSED(format);
    UNUSED(budget); UNUSED(result);
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

#include "../../src/ngx_http_markdown_conditional.c"
#include "../../src/ngx_http_markdown_auth.c"

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
    ngx_http_request_t *r = (ngx_http_request_t *)
        ngx_pcalloc(NULL, sizeof(ngx_http_request_t));
    if (r == NULL) return NULL;
    r->pool = NULL;
    r->connection = (ngx_connection_t *)
        ngx_pcalloc(NULL, sizeof(ngx_connection_t));
    if (r->connection == NULL) return NULL;
    r->headers_in.headers = *create_header_list();
    r->headers_out.headers = *create_header_list();
    return r;
}

/* ── send_304 tests ──────────────────────────────────────────── */

static void
test_send_304_with_etag(void)
{
    g_pool_offset = 0;
    g_send_header_rc = NGX_OK;
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }

    static uint8_t etag_data[] = "\"abc123\"";
    struct MarkdownResult result;
    memset(&result, 0, sizeof(result));
    result.etag = etag_data;
    result.etag_len = 8;

    ngx_int_t rc = ngx_http_markdown_send_304(r, &result);
    TEST_ASSERT(rc == NGX_DONE, "send_304 returns NGX_DONE");
    TEST_ASSERT(r->headers_out.status == NGX_HTTP_NOT_MODIFIED,
        "Status is 304");
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

int
main(void)
{
    printf("\n========================================\n");
    printf("conditional_production Tests\n");
    printf("========================================\n");

    test_send_304_with_etag();
    test_send_304_replaces_existing_etag();
    test_auth_ignores_invalidated_authorization();
    test_send_304_null_result();
    test_send_304_empty_etag();
    test_send_304_send_header_fails();

    test_find_header_null_name();
    test_find_header_found();
    test_find_header_not_found();
    test_find_header_hash_zero_skipped();

    test_handle_inm_disabled();
    test_handle_inm_if_modified_since_only();
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

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
