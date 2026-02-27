/*
 * Test: headers
 * Description: HTTP header manipulation
 */

#include "test_common.h"
#include <ctype.h>
#include <stdint.h>
#include <sys/types.h>

typedef unsigned char u_char;
typedef intptr_t ngx_int_t;
typedef uintptr_t ngx_uint_t;
typedef uintptr_t ngx_flag_t;
typedef uintptr_t ngx_msec_t;
typedef struct ngx_list_part_s ngx_list_part_t;

#define NGX_OK 0
#define NGX_ERROR -1

typedef struct {
    u_char *data;
    size_t len;
} ngx_str_t;

typedef struct {
    ngx_str_t key;
    ngx_str_t value;
    ngx_uint_t hash;
} ngx_table_elt_t;

struct ngx_list_part_s {
    void *elts;
    ngx_uint_t nelts;
    ngx_list_part_t *next;
};

typedef struct {
    ngx_list_part_t part;
    size_t size;
    ngx_uint_t nalloc;
    void *pool;
} ngx_list_t;

typedef struct { int dummy; } ngx_pool_t;
typedef struct { void *log; } ngx_connection_t;

typedef struct {
    ngx_str_t content_type;
    ngx_str_t charset;
    size_t content_type_len;
    off_t content_length_n;
    ngx_table_elt_t *etag;
    ngx_table_elt_t *content_encoding;
    ngx_table_elt_t *accept_ranges;
    ngx_list_t headers;
} ngx_http_headers_out_t;

typedef struct {
    ngx_pool_t *pool;
    ngx_connection_t *connection;
    ngx_http_headers_out_t headers_out;
    void *main;
} ngx_http_request_t;

typedef struct {
    ngx_flag_t enabled;
    size_t max_size;
    ngx_msec_t timeout;
    ngx_uint_t on_error;
    ngx_uint_t flavor;
    ngx_flag_t token_estimate;
    ngx_flag_t front_matter;
    ngx_flag_t on_wildcard;
    ngx_uint_t auth_policy;
    void *auth_cookies;
    ngx_flag_t generate_etag;
    ngx_uint_t conditional_requests;
    ngx_flag_t buffer_chunked;
    void *stream_types;
} ngx_http_markdown_conf_t;

typedef struct {
    uint8_t *markdown;
    uintptr_t markdown_len;
    uint8_t *etag;
    uintptr_t etag_len;
    uint32_t token_estimate;
    uint32_t error_code;
    uint8_t *error_message;
    uintptr_t error_len;
} MarkdownResult;

/* Exported by ngx_http_markdown_headers_standalone.c */
ngx_int_t ngx_http_markdown_update_headers(ngx_http_request_t *r,
                                           MarkdownResult *result,
                                           ngx_http_markdown_conf_t *conf);

/* Mocks required by ngx_http_markdown_headers_standalone.c */
void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

ngx_table_elt_t *
ngx_list_push(ngx_list_t *list)
{
    ngx_table_elt_t *elts;
    if (list->part.elts == NULL) {
        return NULL;
    }
    if (list->part.nelts >= list->nalloc) {
        return NULL;
    }
    elts = (ngx_table_elt_t *) list->part.elts;
    return &elts[list->part.nelts++];
}

void
ngx_http_clear_content_length(ngx_http_request_t *r)
{
    r->headers_out.content_length_n = -1;
}

void ngx_log_error(int level, void *log, int err, const char *fmt, ...) { UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); }
void ngx_log_debug0(int level, void *log, int err, const char *fmt) { UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); }
void ngx_log_debug1(int level, void *log, int err, const char *fmt, ...) { UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); }

int
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    size_t i;
    for (i = 0; i < n; i++) {
        int c1 = tolower((unsigned char) s1[i]);
        int c2 = tolower((unsigned char) s2[i]);
        if (c1 != c2) {
            return c1 - c2;
        }
        if (s1[i] == '\0' || s2[i] == '\0') {
            break;
        }
    }
    return 0;
}

u_char *
ngx_cpymem(u_char *dst, const void *src, size_t n)
{
    memcpy(dst, src, n);
    return dst + n;
}

u_char *
ngx_sprintf(u_char *buf, const char *fmt, ...)
{
    char fmt_buf[128];
    va_list args;
    int len;
    size_t i;
    size_t j = 0;

    for (i = 0; fmt[i] != '\0' && j + 1 < sizeof(fmt_buf); i++) {
        if (fmt[i] == '%' && fmt[i + 1] == 'u' && fmt[i + 2] == 'i') {
            fmt_buf[j++] = '%';
            fmt_buf[j++] = 'u';
            i += 2;
            continue;
        }
        fmt_buf[j++] = fmt[i];
    }
    fmt_buf[j] = '\0';

    va_start(args, fmt);
    len = vsnprintf((char *) buf, 128, fmt_buf, args);
    va_end(args);

    if (len < 0) {
        len = 0;
    }
    return buf + len;
}

static void
init_headers_list(ngx_list_t *list, ngx_uint_t capacity)
{
    list->size = sizeof(ngx_table_elt_t);
    list->nalloc = capacity;
    list->pool = NULL;
    list->part.elts = calloc(capacity, sizeof(ngx_table_elt_t));
    list->part.nelts = 0;
    list->part.next = NULL;
}

static ngx_table_elt_t *
push_header(ngx_http_request_t *r, const char *key, const char *value)
{
    size_t key_len = strlen(key) + 1;
    size_t val_len = strlen(value) + 1;
    char *key_copy = (char *) malloc(key_len);
    char *val_copy = (char *) malloc(val_len);
    ngx_table_elt_t *h = ngx_list_push(&r->headers_out.headers);
    TEST_ASSERT(h != NULL, "header list push failed");
    TEST_ASSERT(key_copy != NULL, "alloc key failed");
    TEST_ASSERT(val_copy != NULL, "alloc value failed");
    memcpy(key_copy, key, key_len);
    memcpy(val_copy, value, val_len);
    h->hash = 1;
    h->key.data = (u_char *) key_copy;
    h->key.len = strlen(key);
    h->value.data = (u_char *) val_copy;
    h->value.len = strlen(value);
    return h;
}

static ngx_table_elt_t *
find_header(ngx_http_request_t *r, const char *key)
{
    ngx_table_elt_t *elts = (ngx_table_elt_t *) r->headers_out.headers.part.elts;
    ngx_uint_t i;
    for (i = 0; i < r->headers_out.headers.part.nelts; i++) {
        if (elts[i].hash != 0 &&
            elts[i].key.len == strlen(key) &&
            ngx_strncasecmp(elts[i].key.data, (u_char *) key, elts[i].key.len) == 0)
        {
            return &elts[i];
        }
    }
    return NULL;
}

static ngx_uint_t
count_active_headers(ngx_http_request_t *r, const char *key)
{
    ngx_table_elt_t *elts = (ngx_table_elt_t *) r->headers_out.headers.part.elts;
    ngx_uint_t i;
    ngx_uint_t count = 0;

    for (i = 0; i < r->headers_out.headers.part.nelts; i++) {
        if (elts[i].hash != 0 &&
            elts[i].key.len == strlen(key) &&
            ngx_strncasecmp(elts[i].key.data, (u_char *) key, elts[i].key.len) == 0)
        {
            count++;
        }
    }

    return count;
}

static ngx_http_request_t
new_request(void)
{
    ngx_http_request_t r;
    ngx_pool_t *pool = (ngx_pool_t *) calloc(1, sizeof(ngx_pool_t));
    ngx_connection_t *conn = (ngx_connection_t *) calloc(1, sizeof(ngx_connection_t));
    memset(&r, 0, sizeof(r));
    r.pool = pool;
    r.connection = conn;
    init_headers_list(&r.headers_out.headers, 32);
    return r;
}

static void
test_update_headers_full_path(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    ngx_table_elt_t *vary;
    ngx_table_elt_t *token_h;

    TEST_SUBSECTION("Update headers with ETag and token estimation enabled");

    memset(&conf, 0, sizeof(conf));
    conf.generate_etag = 1;
    conf.token_estimate = 1;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 42;
    result.etag = (uint8_t *) "\"etag-1\"";
    result.etag_len = strlen((char *) result.etag);
    result.token_estimate = 123;

    push_header(&r, "Vary", "User-Agent");
    r.headers_out.content_encoding = push_header(&r, "Content-Encoding", "gzip");
    r.headers_out.accept_ranges = push_header(&r, "Accept-Ranges", "bytes");
    r.headers_out.etag = push_header(&r, "ETag", "\"upstream\"");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");

    TEST_ASSERT(STR_EQ((char *) r.headers_out.content_type.data, "text/markdown; charset=utf-8"),
                "Content-Type should be markdown utf-8");
    TEST_ASSERT(r.headers_out.content_length_n == 42, "Content-Length should match markdown length");
    TEST_ASSERT(r.headers_out.content_encoding == NULL, "Content-Encoding pointer should be cleared");
    TEST_ASSERT(r.headers_out.accept_ranges == NULL, "Accept-Ranges pointer should be cleared");
    TEST_ASSERT(r.headers_out.etag != NULL, "ETag should be set when enabled");
    TEST_ASSERT(count_active_headers(&r, "ETag") == 1, "Only one active ETag header should remain");

    vary = find_header(&r, "Vary");
    TEST_ASSERT(vary != NULL, "Vary header should exist");
    TEST_ASSERT(strstr((char *) vary->value.data, "Accept") != NULL, "Vary should include Accept");

    token_h = find_header(&r, "X-Markdown-Tokens");
    TEST_ASSERT(token_h != NULL, "Token header should be present when enabled");
    TEST_ASSERT(strstr((char *) token_h->value.data, "123") != NULL, "Token header value should contain token count");

    TEST_PASS("Full header update path works");
}

static void
test_update_headers_without_optional_fields(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;

    TEST_SUBSECTION("Update headers without ETag/token output");

    memset(&conf, 0, sizeof(conf));
    conf.generate_etag = 0;
    conf.token_estimate = 0;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 7;
    r.headers_out.etag = push_header(&r, "ETag", "\"stale-upstream\"");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed without optional fields");
    TEST_ASSERT(r.headers_out.etag == NULL, "ETag should be cleared when generation disabled");
    TEST_ASSERT(find_header(&r, "ETag") == NULL,
                "No active ETag header should remain when generation disabled");
    TEST_ASSERT(find_header(&r, "X-Markdown-Tokens") == NULL,
                "Token header should not be added when disabled");
    TEST_PASS("Optional field handling works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("headers Tests\n");
    printf("========================================\n");

    test_update_headers_full_path();
    test_update_headers_without_optional_fields();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
