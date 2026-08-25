/*
 * Test: headers
 *
 * Validates header update logic: Content-Type setting, Vary header
 * management, Content-Length clearing, Content-Encoding removal,
 * ETag clearing, and Accept-Ranges handling.
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
#define NGX_HTTP_MARKDOWN_HEADER_SNAPSHOT_RESTORE_FAILED -107

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
    ngx_list_part_t *last;
    size_t size;
    ngx_uint_t nalloc;
    void *pool;
} ngx_list_t;

typedef struct pool_alloc_s pool_alloc_t;

struct pool_alloc_s {
    void *ptr;
    pool_alloc_t *next;
};

static ngx_uint_t g_fail_list_push_after_expand;
static ngx_uint_t g_list_was_expanded;

typedef struct {
    pool_alloc_t *allocs;
} ngx_pool_t;

typedef struct { void *log; } ngx_connection_t;

typedef struct {
    ngx_str_t content_type;
    ngx_str_t charset;
    size_t content_type_len;
    u_char *content_type_lowcase;
    ngx_uint_t content_type_hash;
    off_t content_length_n;
    time_t last_modified_time;
    ngx_table_elt_t *etag;
    ngx_table_elt_t *last_modified;
    ngx_table_elt_t *content_encoding;
    ngx_table_elt_t *accept_ranges;
    ngx_list_t headers;
    ngx_list_t trailers;
} ngx_http_headers_out_t;

typedef struct {
    ngx_pool_t *pool;
    ngx_connection_t *connection;
    ngx_http_headers_out_t headers_out;
    ngx_flag_t allow_ranges;
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
    struct {
        ngx_uint_t auth_policy;
        void *auth_cookies;
        ngx_flag_t generate_etag;
        ngx_uint_t conditional_requests;
    } policy;
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
                                           const MarkdownResult *result,
                                           const ngx_http_markdown_conf_t *conf);
ngx_int_t ngx_http_markdown_head_representation_headers(ngx_http_request_t *r);
void ngx_http_markdown_clear_trailers(ngx_http_request_t *r);
ngx_int_t ngx_http_markdown_test_header_snapshot_restore_status(void);

/* Mocks required by ngx_http_markdown_headers_standalone.c */
void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    pool_alloc_t *node;
    void *ptr;

    if (pool == NULL) {
        return NULL;
    }

    ptr = malloc(size);
    if (ptr == NULL) {
        return NULL;
    }

    node = (pool_alloc_t *) malloc(sizeof(*node));
    if (node == NULL) {
        free(ptr);
        return NULL;
    }

    node->ptr = ptr;
    node->next = pool->allocs;
    pool->allocs = node;
    return ptr;
}

ngx_table_elt_t *
ngx_list_push(ngx_list_t *list)
{
    ngx_list_part_t *last;
    ngx_list_part_t *part;
    ngx_table_elt_t *elts;

    if (list == NULL || list->part.elts == NULL) {
        return NULL;
    }

    last = (list->last != NULL) ? list->last : &list->part;
    if (last->nelts >= list->nalloc) {
        if (g_fail_list_push_after_expand && g_list_was_expanded) {
            return NULL;
        }

        part = (ngx_list_part_t *) ngx_pnalloc(
            (ngx_pool_t *) list->pool, sizeof(*part));
        if (part == NULL) {
            return NULL;
        }
        part->elts = ngx_pnalloc((ngx_pool_t *) list->pool,
                                 list->nalloc * list->size);
        if (part->elts == NULL) {
            return NULL;
        }
        part->nelts = 0;
        part->next = NULL;
        last->next = part;
        list->last = part;
        last = part;
        g_list_was_expanded = 1;
    }

    elts = (ngx_table_elt_t *) last->elts;
    if (list->last == NULL) {
        list->last = last;
    }
    return &elts[last->nelts++];
}

void
ngx_http_clear_content_length(ngx_http_request_t *r)
{
    r->headers_out.content_length_n = -1;
}

void ngx_log_error(int level, void *log, int err, const char *fmt) { UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); }
void ngx_log_debug0(int level, void *log, int err, const char *fmt) { UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); }
void ngx_http_markdown_log_debug1(int level, void *log, int err, const char *fmt, uintptr_t arg)
{
    UNUSED(level);
    UNUSED(log);
    UNUSED(err);
    UNUSED(fmt);
    UNUSED(arg);
}

int
ngx_strncasecmp(const u_char *s1, const u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        int c1 = tolower(s1[i]);
        int c2 = tolower(s2[i]);
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
ngx_http_markdown_sprintf_token(u_char *buf, ngx_uint_t token_count)
{
    int len;

    len = snprintf((char *) buf, 128, "%u", (unsigned int) token_count);

    if (len < 0) {
        len = 0;
    }
    return buf + len;
}

static void
init_headers_list(ngx_list_t *list, ngx_uint_t capacity, ngx_pool_t *pool)
{
    list->size = sizeof(ngx_table_elt_t);
    list->nalloc = capacity;
    list->pool = pool;
    list->part.elts = calloc(capacity, sizeof(ngx_table_elt_t));
    list->part.nelts = 0;
    list->part.next = NULL;
    list->last = &list->part;
}

static void
destroy_pool(ngx_pool_t *pool)
{
    pool_alloc_t *node;

    if (pool == NULL) {
        return;
    }

    node = pool->allocs;
    while (node != NULL) {
        pool_alloc_t *next = node->next;
        free(node->ptr);
        free(node);
        node = next;
    }

    free(pool);
}

static ngx_table_elt_t *
push_header(ngx_http_request_t *r, const char *key, const char *value)
{
    size_t key_data_len = test_cstrnlen(key, 256);
    size_t val_data_len = test_cstrnlen(value, 512);
    size_t key_len = key_data_len + 1;
    size_t val_len = val_data_len + 1;
    char *key_copy = (char *) ngx_pnalloc(r->pool, key_len);
    char *val_copy = (char *) ngx_pnalloc(r->pool, val_len);
    ngx_table_elt_t *h = ngx_list_push(&r->headers_out.headers);
    TEST_ASSERT(h != NULL, "header list push failed");
    TEST_ASSERT(key_copy != NULL, "alloc key failed");
    TEST_ASSERT(val_copy != NULL, "alloc value failed");
    TEST_ASSERT(key_data_len < 256, "key must be null-terminated within 255 bytes");
    TEST_ASSERT(val_data_len < 512, "value must be null-terminated within 511 bytes");
    memcpy(key_copy, key, key_len);
    memcpy(val_copy, value, val_len);
    h->hash = 1;
    h->key.data = (u_char *) key_copy;
    h->key.len = key_data_len;
    h->value.data = (u_char *) val_copy;
    h->value.len = val_data_len;
    return h;
}

/*
 * Bounded substring search that does not require NUL-terminated input.
 * Returns 1 if `needle` is found within `haystack[0..haystack_len)`, 0 otherwise.
 */
static int
find_substr(const u_char *haystack, size_t haystack_len,
    const char *needle, size_t needle_len)
{
    size_t limit;

    if (needle_len == 0) {
        return 1;
    }

    if (needle_len > haystack_len) {
        return 0;
    }

    limit = haystack_len - needle_len + 1;

    for (size_t i = 0; i < limit; i++) {
        if (memcmp(haystack + i, needle, needle_len) == 0) {
            return 1;
        }
    }

    return 0;
}

static ngx_table_elt_t *
find_header(ngx_http_request_t *r, const char *key)
{
    ngx_table_elt_t *elts = (ngx_table_elt_t *) r->headers_out.headers.part.elts;
    size_t key_len = test_cstrnlen(key, 256);
    const u_char *key_u = (const u_char *) key;

    for (ngx_uint_t i = 0; i < r->headers_out.headers.part.nelts; i++) {
        if (elts[i].hash != 0 &&
            elts[i].key.len == key_len &&
            ngx_strncasecmp(elts[i].key.data, key_u, elts[i].key.len) == 0)
        {
            return &elts[i];
        }
    }
    return NULL;
}

static ngx_uint_t
count_active_headers(const ngx_http_request_t *r, const char *key)
{
    const ngx_table_elt_t *elts = (const ngx_table_elt_t *) r->headers_out.headers.part.elts;
    ngx_uint_t count = 0;
    size_t key_len = test_cstrnlen(key, 256);
    const u_char *key_u = (const u_char *) key;

    for (ngx_uint_t i = 0; i < r->headers_out.headers.part.nelts; i++) {
        if (elts[i].hash != 0 &&
            elts[i].key.len == key_len &&
            ngx_strncasecmp(elts[i].key.data, key_u, elts[i].key.len) == 0)
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
    init_headers_list(&r.headers_out.headers, 32, pool);
    init_headers_list(&r.headers_out.trailers, 4, pool);
    /* The returned request is copied, so never retain a stack address. */
    r.headers_out.headers.last = NULL;
    r.headers_out.trailers.last = NULL;
    return r;
}

static void
free_request(ngx_http_request_t *r)
{
    if (r == NULL) {
        return;
    }

    free(r->headers_out.headers.part.elts);
    r->headers_out.headers.part.elts = NULL;
    r->headers_out.headers.part.nelts = 0;

    free(r->headers_out.trailers.part.elts);
    r->headers_out.trailers.part.elts = NULL;
    r->headers_out.trailers.part.nelts = 0;

    free(r->connection);
    r->connection = NULL;

    destroy_pool(r->pool);
    r->pool = NULL;
}

static void
test_update_headers_full_path(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_value[] = "\"etag-1\"";
    ngx_table_elt_t *vary;
    ngx_table_elt_t *token_h;

    TEST_SUBSECTION("Update headers with ETag and token estimation enabled");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;
    conf.token_estimate = 1;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 42;
    result.etag = etag_value;
    result.etag_len = sizeof(etag_value) - 1;
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
    TEST_ASSERT(find_substr(vary->value.data, vary->value.len, "Accept", 6),
                "Vary should include Accept");

    token_h = find_header(&r, "X-Markdown-Tokens");
    TEST_ASSERT(token_h != NULL, "Token header should be present when enabled");
    TEST_ASSERT(find_substr(token_h->value.data, token_h->value.len, "123", 3),
                "Token header value should contain token count");

    free_request(&r);
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
    conf.policy.generate_etag = 0;
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

    free_request(&r);
    TEST_PASS("Optional field handling works");
}

static void
test_update_headers_null_args(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;

    TEST_SUBSECTION("Update headers with NULL arguments");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));

    TEST_ASSERT(ngx_http_markdown_update_headers(NULL, &result, &conf) == NGX_ERROR,
                "NULL request should fail");
    TEST_ASSERT(ngx_http_markdown_update_headers(&r, NULL, &conf) == NGX_ERROR,
                "NULL result should fail");
    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, NULL) == NGX_ERROR,
                "NULL conf should fail");

    free_request(&r);
    TEST_PASS("NULL argument validation works");
}

static void
test_update_headers_etag_no_existing(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_value[] = "\"abc123\"";
    ngx_table_elt_t *vary;

    TEST_SUBSECTION("Update headers with ETag but no existing Vary header");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;
    conf.token_estimate = 0;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;
    result.etag = etag_value;
    result.etag_len = sizeof(etag_value) - 1;

    /* No existing Vary header - tests the "create new Vary" path */
    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers with new Vary should succeed");

    TEST_ASSERT(r.headers_out.etag != NULL, "ETag should be set");
    vary = find_header(&r, "Vary");
    TEST_ASSERT(vary != NULL, "Vary header should be created");
    TEST_ASSERT(find_substr(vary->value.data, vary->value.len, "Accept", 6),
                "Vary should include Accept");

    free_request(&r);
    TEST_PASS("ETag with new Vary path works");
}

static void
test_update_headers_etag_existing_vary_accept(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_value[] = "\"abc123\"";
    ngx_table_elt_t *vary;

    TEST_SUBSECTION("Update headers with ETag and existing Vary: Accept");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;
    conf.token_estimate = 0;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;
    result.etag = etag_value;
    result.etag_len = sizeof(etag_value) - 1;

    push_header(&r, "Vary", "Accept");
    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers with existing Vary: Accept should succeed");

    TEST_ASSERT(count_active_headers(&r, "Vary") == 1,
                "Existing Vary: Accept should not be duplicated");
    vary = find_header(&r, "Vary");
    TEST_ASSERT(vary != NULL, "Vary header should still exist");
    TEST_ASSERT(vary->value.len == sizeof("Accept") - 1 &&
                memcmp(vary->value.data, "Accept", vary->value.len) == 0,
                "Vary header value should remain unchanged");

    free_request(&r);
    TEST_PASS("Existing Vary: Accept path works");
}

static void
test_update_headers_etag_existing_vary_accept_trailing_ows(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_value[] = "\"abc123\"";
    ngx_table_elt_t *vary;

    TEST_SUBSECTION("Update headers with trailing OWS in Vary: Accept");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;
    conf.token_estimate = 0;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;
    result.etag = etag_value;
    result.etag_len = sizeof(etag_value) - 1;

    push_header(&r, "Vary", "User-Agent, Accept\t");
    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should accept trailing HTAB in Vary");

    TEST_ASSERT(count_active_headers(&r, "Vary") == 1,
                "Vary with trailing HTAB should not be duplicated");
    vary = find_header(&r, "Vary");
    TEST_ASSERT(vary != NULL, "Vary header should still exist");
    TEST_ASSERT(vary->value.len == sizeof("User-Agent, Accept\t") - 1
                && memcmp(vary->value.data, "User-Agent, Accept\t",
                          vary->value.len) == 0,
                "Vary header value should remain unchanged");

    free_request(&r);
    TEST_PASS("Trailing HTAB in Vary is handled");
}

static void
test_update_headers_token_zero(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;

    TEST_SUBSECTION("Update headers with token_estimate enabled but zero count");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 0;
    conf.token_estimate = 1;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 5;
    result.token_estimate = 0;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers with zero tokens should succeed");
    TEST_ASSERT(find_header(&r, "X-Markdown-Tokens") == NULL,
                "Token header should not be added for zero count");

    free_request(&r);
    TEST_PASS("Zero token count handling works");
}

static void
test_update_headers_ignores_invalidated_vary(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_value[] = "\"abc123\"";
    ngx_table_elt_t *invalid_vary;
    ngx_table_elt_t *valid_vary;

    TEST_SUBSECTION("Update headers ignores invalidated Vary entries");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;
    result.etag = etag_value;
    result.etag_len = sizeof(etag_value) - 1;

    invalid_vary = push_header(&r, "Vary", "Accept");
    invalid_vary->hash = 0;
    valid_vary = push_header(&r, "Vary", "User-Agent");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should skip invalidated Vary entries");
    TEST_ASSERT(invalid_vary->hash == 0, "Invalidated Vary should stay inactive");
    TEST_ASSERT(valid_vary->hash != 0, "Valid Vary should stay active");
    TEST_ASSERT(count_active_headers(&r, "Vary") == 1,
                "Only the valid Vary header should remain active");
    TEST_ASSERT(find_substr(valid_vary->value.data, valid_vary->value.len,
                "Accept", 6),
                "Valid Vary should be updated with Accept");

    free_request(&r);
    TEST_PASS("Invalidated Vary entries are ignored");
}

static void
test_update_headers_creates_vary_after_invalidated_only(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_value[] = "\"abc123\"";
    ngx_table_elt_t *invalid_vary;
    ngx_table_elt_t *vary;

    TEST_SUBSECTION("Update headers creates Vary after invalidated-only match");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;
    result.etag = etag_value;
    result.etag_len = sizeof(etag_value) - 1;

    invalid_vary = push_header(&r, "Vary", "Accept");
    invalid_vary->hash = 0;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should create a fresh active Vary header");

    TEST_ASSERT(invalid_vary->hash == 0, "Invalidated Vary should stay inactive");
    TEST_ASSERT(count_active_headers(&r, "Vary") == 1,
                "Exactly one active Vary should be created");
    vary = find_header(&r, "Vary");
    TEST_ASSERT(vary != NULL, "Active Vary header should exist");
    TEST_ASSERT(vary != invalid_vary, "Active Vary should not reuse inactive entry");
    TEST_ASSERT(find_substr(vary->value.data, vary->value.len, "Accept", 6),
                "Created Vary should include Accept");

    free_request(&r);
    TEST_PASS("Invalidated-only Vary path creates a new header");
}

static void
test_update_headers_removes_duplicate_content_encoding(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    ngx_table_elt_t *gzip;
    ngx_table_elt_t *br;

    TEST_SUBSECTION("Update headers removes duplicate Content-Encoding entries");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;

    gzip = push_header(&r, "Content-Encoding", "gzip");
    br = push_header(&r, "Content-Encoding", "br");
    r.headers_out.content_encoding = gzip;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should remove duplicate Content-Encoding");
    TEST_ASSERT(r.headers_out.content_encoding == NULL,
                "Content-Encoding pointer should be cleared");
    TEST_ASSERT(gzip->hash == 0, "First Content-Encoding should be inactive");
    TEST_ASSERT(br->hash == 0, "Second Content-Encoding should be inactive");
    TEST_ASSERT(count_active_headers(&r, "Content-Encoding") == 0,
                "No active Content-Encoding headers should remain");

    free_request(&r);
    TEST_PASS("Duplicate Content-Encoding headers are removed");
}

static void
test_update_headers_skips_invalidated_accept_ranges(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    ngx_table_elt_t *invalid_ranges;
    ngx_table_elt_t *active_ranges;

    TEST_SUBSECTION("Accept-Ranges removal skips invalidated entries");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;

    invalid_ranges = push_header(&r, "Accept-Ranges", "none");
    active_ranges = push_header(&r, "Accept-Ranges", "bytes");
    invalid_ranges->hash = 0;
    r.headers_out.accept_ranges = active_ranges;
    r.allow_ranges = 1;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should remove active Accept-Ranges");
    TEST_ASSERT(invalid_ranges->hash == 0,
                "Invalidated Accept-Ranges should stay inactive");
    TEST_ASSERT(active_ranges->hash == 0,
                "Active Accept-Ranges after invalid entry should be removed");
    TEST_ASSERT(r.headers_out.accept_ranges == NULL,
                "Typed Accept-Ranges pointer should be cleared");
    TEST_ASSERT(r.allow_ranges == 0,
                "Range support should be disabled");

    free_request(&r);
    TEST_PASS("Invalidated Accept-Ranges entries are skipped");
}

static void
test_update_headers_prepare_failure_rolls_back(void)
{
    ngx_http_request_t       r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult           result;
    ngx_table_elt_t           *content_encoding;
    ngx_table_elt_t           *etag;
    ngx_table_elt_t            before[2];
    ngx_uint_t                 original_nelts;

    TEST_SUBSECTION("Header prepare failure restores the original response");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;
    result.etag = (uint8_t *) "\"new-etag\"";
    result.etag_len = sizeof("\"new-etag\"") - 1;

    content_encoding = push_header(&r, "Content-Encoding", "gzip");
    etag = push_header(&r, "ETag", "\"upstream\"");
    r.headers_out.content_encoding = content_encoding;
    r.headers_out.etag = etag;
    r.allow_ranges = 1;
    original_nelts = r.headers_out.headers.part.nelts;
    before[0] = *(ngx_table_elt_t *) r.headers_out.headers.part.elts;
    before[1] = ((ngx_table_elt_t *) r.headers_out.headers.part.elts)[1];

    /* Force the P2 ETag list push to fail after P1 has applied its plan. */
    r.headers_out.headers.nalloc = original_nelts;
    g_fail_list_push_after_expand = 1;
    g_list_was_expanded = 1;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf)
                    == NGX_ERROR,
                "ETag prepare failure should abort header update");
    TEST_ASSERT(r.headers_out.headers.part.nelts == original_nelts,
                "failed prepare must restore header-list cardinality");
    TEST_ASSERT(memcmp(r.headers_out.headers.part.elts, before,
                       sizeof(before)) == 0,
                "failed prepare must restore existing header entries");
    TEST_ASSERT(r.headers_out.content_encoding == content_encoding,
                "failed prepare must restore Content-Encoding pointer");
    TEST_ASSERT(r.headers_out.etag == etag,
                "failed prepare must restore ETag pointer");
    TEST_ASSERT(r.allow_ranges == 1,
                "failed prepare must restore allow_ranges");

    g_fail_list_push_after_expand = 0;
    g_list_was_expanded = 0;
    free_request(&r);
    TEST_PASS("Header prepare failure is atomic");
}

static void
test_update_headers_multipart_failure_restores_chain(void)
{
    ngx_http_request_t       r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult           result;
    ngx_table_elt_t           *content_encoding;
    ngx_table_elt_t           before[2];
    ngx_list_part_t          *original_last;
    ngx_list_part_t          *original_next;
    ngx_uint_t                 total;

    TEST_SUBSECTION("Multipart header rollback restores list links");

    memset(&conf, 0, sizeof(conf));
    conf.policy.generate_etag = 1;
    conf.token_estimate = 1;

    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;
    result.etag = (uint8_t *) "\"new-etag\"";
    result.etag_len = sizeof("\"new-etag\"") - 1;
    result.token_estimate = 1;

    content_encoding = push_header(&r, "Content-Encoding", "gzip");
    push_header(&r, "Content-Length", "10");
    r.headers_out.content_encoding = content_encoding;

    /* Model a full production list part before the snapshot is taken. */
    r.headers_out.headers.nalloc = 2;
    original_last = r.headers_out.headers.last;
    original_next = original_last->next;
    before[0] = ((ngx_table_elt_t *) original_last->elts)[0];
    before[1] = ((ngx_table_elt_t *) original_last->elts)[1];

    /* P2 expands to a new part; P4 then fails on its next push. */
    g_fail_list_push_after_expand = 1;
    g_list_was_expanded = 0;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf)
                    == NGX_ERROR,
                "multipart prepare failure should abort header update");
    TEST_ASSERT(r.headers_out.headers.last == original_last,
                "rollback must restore the original last list part");
    TEST_ASSERT(original_last->next == original_next,
                "rollback must restore the original last part next pointer");
    TEST_ASSERT(original_last->nelts == 2,
                "rollback must restore the original last part cardinality");

    total = 0;
    for (ngx_list_part_t *part = &r.headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        total += part->nelts;
    }
    TEST_ASSERT(total == 2,
                "rollback must detach every newly allocated list part");
    TEST_ASSERT(memcmp(original_last->elts, before, sizeof(before)) == 0,
                "rollback must preserve all original multipart entries");
    TEST_ASSERT(r.headers_out.content_encoding == content_encoding,
                "multipart rollback must restore typed header pointers");

    g_fail_list_push_after_expand = 0;
    g_list_was_expanded = 0;
    free_request(&r);
    TEST_PASS("Multipart header rollback restores list links");
}

static void
test_header_snapshot_restore_failure_is_terminal(void)
{
    TEST_SUBSECTION("Header rollback failure is a distinct terminal result");

    TEST_ASSERT(
        ngx_http_markdown_test_header_snapshot_restore_status()
            == NGX_HTTP_MARKDOWN_HEADER_SNAPSHOT_RESTORE_FAILED,
        "failed header snapshot restore must not collapse into NGX_ERROR");

    TEST_PASS("Header rollback failure is terminal");
}

/* ══════════════════════════════════════════════════════════════════
 * HEAD representation headers
 * ══════════════════════════════════════════════════════════════════ */

static void
push_trailer(ngx_http_request_t *r, const char *name, const char *value);

static void
test_head_representation_headers_strips_html_metadata(void)
{
    ngx_http_request_t r = new_request();
    ngx_table_elt_t   *vary;
    ngx_table_elt_t   *trailer;

    TEST_SUBSECTION("HEAD representation headers describe Markdown");

    /* Upstream HTML representation metadata the HEAD must strip. */
    push_header(&r, "Content-Type", "text/html");
    push_header(&r, "Content-Encoding", "gzip");
    push_header(&r, "Content-Length", "2048");
    push_header(&r, "ETag", "\"html-etag\"");
    push_header(&r, "Last-Modified", "Wed, 01 Jan 2025 00:00:00 GMT");
    push_header(&r, "Accept-Ranges", "bytes");
    push_header(&r, "Content-MD5", "abc123");
    push_header(&r, "Digest", "sha-256=:abc123:");
    push_header(&r, "Content-Digest", "sha-256=:abc123:");
    push_header(&r, "Repr-Digest", "sha-256=:abc123:");
    push_header(&r, "X-Markdown-Tokens", "42");
    push_header(&r, "Trailer", "Content-Digest");
    push_trailer(&r, "Content-Digest", "sha-256=:abc123:");
    r.headers_out.content_type.data = (u_char *) "text/html";
    r.headers_out.content_type.len = sizeof("text/html") - 1;
    r.headers_out.content_type_len = sizeof("text/html") - 1;
    r.headers_out.content_length_n = 2048;
    r.headers_out.last_modified_time = 1234567890;
    r.headers_out.etag = push_header(&r, "ETag", "\"html-etag\"");
    r.headers_out.accept_ranges = (ngx_table_elt_t *) 1;

    TEST_ASSERT(ngx_http_markdown_head_representation_headers(&r) == NGX_OK,
                "HEAD representation headers should succeed");

    TEST_ASSERT(STR_EQ((char *) r.headers_out.content_type.data,
                        "text/markdown; charset=utf-8"),
                "HEAD Content-Type should be Markdown");
    TEST_ASSERT(r.headers_out.content_encoding == NULL,
                "HEAD Content-Encoding should be cleared");
    TEST_ASSERT(r.headers_out.content_length_n == -1,
                "HEAD Content-Length should be removed (not fabricated)");
    TEST_ASSERT(r.headers_out.etag == NULL,
                "HEAD ETag should be removed (not fabricated)");
    TEST_ASSERT(r.headers_out.last_modified_time == (time_t) -1,
                "HEAD Last-Modified should be removed");
    TEST_ASSERT(r.headers_out.accept_ranges == NULL,
                "HEAD Accept-Ranges should be cleared");

    TEST_ASSERT(find_header(&r, "Content-MD5") == NULL,
                "HEAD strips Content-MD5");
    TEST_ASSERT(find_header(&r, "Digest") == NULL,
                "HEAD strips Digest");
    TEST_ASSERT(find_header(&r, "Content-Digest") == NULL,
                "HEAD strips Content-Digest");
    TEST_ASSERT(find_header(&r, "Repr-Digest") == NULL,
                "HEAD strips Repr-Digest");
    TEST_ASSERT(find_header(&r, "X-Markdown-Tokens") == NULL,
                "HEAD strips X-Markdown-Tokens");
    TEST_ASSERT(find_header(&r, "Trailer") == NULL,
                "HEAD strips Trailer declaration");
    TEST_ASSERT(find_header(&r, "ETag") == NULL,
                "HEAD strips ETag entries");
    TEST_ASSERT(find_header(&r, "Content-Length") == NULL,
                "HEAD strips Content-Length entries");
    TEST_ASSERT(find_header(&r, "Last-Modified") == NULL,
                "HEAD strips Last-Modified entries");
    trailer = (ngx_table_elt_t *) r.headers_out.trailers.part.elts;
    TEST_ASSERT(trailer != NULL && trailer[0].hash == 0,
                "HEAD suppresses actual trailer entries");

    vary = find_header(&r, "Vary");
    TEST_ASSERT(vary != NULL, "HEAD has Vary");
    TEST_ASSERT(find_substr(vary->value.data, vary->value.len, "Accept", 6),
                "HEAD Vary includes Accept");

    free_request(&r);
    TEST_PASS("HEAD representation headers verified");
}

static void
test_head_representation_headers_null(void)
{
    TEST_SUBSECTION("HEAD representation headers NULL request");

    TEST_ASSERT(ngx_http_markdown_head_representation_headers(NULL)
                    == NGX_ERROR,
                "NULL request should fail");

    TEST_PASS("NULL request validation works");
}

/*
 * Adversarial duplicate-header regression: upstream may carry
 * Content-Type / Content-Encoding / Last-Modified both as dedicated
 * fields and as multiple header-list entries (including charset
 * variants and multi-part lists).  After the HEAD representation
 * rewrite there must be exactly one effective Content-Type (the
 * Markdown media type), zero Content-Encoding entries, and zero
 * Last-Modified entries — with the typed pointer and time mirror
 * cleared together.
 */
static void
test_head_representation_headers_duplicate_entries(void)
{
    ngx_http_request_t r = new_request();

    TEST_SUBSECTION("HEAD representation headers purge duplicates");

    push_header(&r, "Content-Type", "text/html");
    push_header(&r, "Content-Type", "text/html; charset=iso-8859-1");
    push_header(&r, "Content-Encoding", "gzip");
    push_header(&r, "Content-Encoding", "br");
    push_header(&r, "Last-Modified", "Wed, 01 Jan 2025 00:00:00 GMT");
    push_header(&r, "Last-Modified", "Wed, 01 Jan 2025 01:00:00 GMT");
    r.headers_out.content_type.data = (u_char *) "text/html";
    r.headers_out.content_type.len = sizeof("text/html") - 1;
    r.headers_out.content_type_len = sizeof("text/html") - 1;
    r.headers_out.content_encoding = NULL;
    r.headers_out.last_modified_time = 1234567890;
    r.headers_out.last_modified =
        push_header(&r, "Last-Modified", "Wed, 01 Jan 2025 02:00:00 GMT");

    TEST_ASSERT(ngx_http_markdown_head_representation_headers(&r) == NGX_OK,
                "HEAD representation rewrite should succeed");

    TEST_ASSERT(count_active_headers(&r, "Content-Type") == 0,
                "all stale Content-Type list entries must be invalidated "
                "(a survivor would emit a second Content-Type)");
    TEST_ASSERT(count_active_headers(&r, "Content-Encoding") == 0,
                "all stale Content-Encoding list entries must be invalidated");
    TEST_ASSERT(count_active_headers(&r, "Last-Modified") == 0,
                "duplicate Last-Modified entries must all be invalidated "
                "(stop_after_first would leave later entries alive)");
    TEST_ASSERT(STR_EQ((char *) r.headers_out.content_type.data,
                        "text/markdown; charset=utf-8"),
                "dedicated Content-Type must be the Markdown media type");
    TEST_ASSERT(r.headers_out.content_encoding == NULL,
                "dedicated Content-Encoding pointer must be cleared");
    TEST_ASSERT(r.headers_out.last_modified_time == (time_t) -1,
                "Last-Modified time mirror must be reset");
    TEST_ASSERT(r.headers_out.last_modified == NULL,
                "Last-Modified typed pointer must be cleared together with "
                "the time mirror");

    free_request(&r);
    TEST_PASS("HEAD duplicate representation headers purged");
}

/* ══════════════════════════════════════════════════════════════════
 * Response trailers clearing
 * ══════════════════════════════════════════════════════════════════ */

static void
push_trailer(ngx_http_request_t *r, const char *name, const char *value)
{
    ngx_table_elt_t *h = ngx_list_push(&r->headers_out.trailers);
    TEST_ASSERT(h != NULL, "trailer push should succeed");
    h->hash = 1;
    h->key.data = (u_char *) name;
    h->key.len = strlen(name);
    h->value.data = (u_char *) value;
    h->value.len = strlen(value);
}

static void
test_clear_trailers_suppresses_all_entries(void)
{
    ngx_http_request_t r = new_request();
    ngx_table_elt_t *elts;
    ngx_uint_t i;

    TEST_SUBSECTION("clear_trailers suppresses upstream representation trailers");

    push_trailer(&r, "Content-Digest", "sha-256=:abc123:");
    push_trailer(&r, "Repr-Digest", "sha-256=:abc123:");
    push_trailer(&r, "Digest", "sha-256=:abc123:");
    push_trailer(&r, "X-Markdown-Tokens", "42");

    ngx_http_markdown_clear_trailers(&r);

    elts = (ngx_table_elt_t *) r.headers_out.trailers.part.elts;
    for (i = 0; i < r.headers_out.trailers.part.nelts; i++) {
        TEST_ASSERT(elts[i].hash == 0,
                    "every trailer entry must be invalidated (hash=0)");
    }
    TEST_ASSERT(r.headers_out.trailers.part.nelts == 4,
                "trailer entries remain in the list but are suppressed");

    free_request(&r);
    TEST_PASS("clear_trailers suppresses all trailer entries");
}

static void
test_clear_trailers_empty_list(void)
{
    ngx_http_request_t r = new_request();

    TEST_SUBSECTION("clear_trailers handles an empty trailer list");

    ngx_http_markdown_clear_trailers(&r);

    TEST_ASSERT(r.headers_out.trailers.part.nelts == 0,
                "empty trailer list stays empty");

    free_request(&r);
    TEST_PASS("clear_trailers handles empty list");
}

static void
test_clear_trailers_null_elts_with_entries(void)
{
    ngx_http_request_t r = new_request();

    TEST_SUBSECTION("clear_trailers handles a malformed list part");

    free(r.headers_out.trailers.part.elts);
    r.headers_out.trailers.part.elts = NULL;
    r.headers_out.trailers.part.nelts = 1;

    ngx_http_markdown_clear_trailers(&r);

    TEST_ASSERT(r.headers_out.trailers.part.nelts == 1,
                "malformed trailer part remains untouched after guard");

    free_request(&r);
    TEST_PASS("clear_trailers guards NULL elts with entries");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("headers Tests\n");
    printf("========================================\n");

    test_update_headers_full_path();
    test_update_headers_without_optional_fields();
    test_update_headers_null_args();
    test_update_headers_etag_no_existing();
    test_update_headers_etag_existing_vary_accept();
    test_update_headers_etag_existing_vary_accept_trailing_ows();
    test_update_headers_token_zero();
    test_update_headers_ignores_invalidated_vary();
    test_update_headers_creates_vary_after_invalidated_only();
    test_update_headers_removes_duplicate_content_encoding();
    test_update_headers_skips_invalidated_accept_ranges();
    test_update_headers_prepare_failure_rolls_back();
    test_update_headers_multipart_failure_restores_chain();
    test_header_snapshot_restore_failure_is_terminal();
    test_head_representation_headers_strips_html_metadata();
    test_head_representation_headers_null();
    test_head_representation_headers_duplicate_entries();
    test_clear_trailers_suppresses_all_entries();
    test_clear_trailers_empty_list();
    test_clear_trailers_null_elts_with_entries();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
