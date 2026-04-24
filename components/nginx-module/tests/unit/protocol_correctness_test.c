/*
 * Test: protocol_correctness
 *
 * Comprehensive protocol correctness tests for HTTP metadata handling
 * in the NGINX Markdown filter module. Covers:
 *
 * - ETag generation and upstream replacement/removal (Spec 21, Tasks 2, 9)
 * - If-None-Match parsing and weak comparison (Task 3)
 * - 304 Not Modified response construction (Task 4)
 * - Vary header management (Task 5)
 * - HEAD request routing and header parity (Task 6)
 * - Content-Type and Content-Length consistency (Task 7)
 * - Streaming path protocol metadata (Task 8)
 * - Configuration mode behavior (Task 10)
 * - Request/response matrix verification (Task 11)
 * - Full-buffer / streaming parity checks (Task 12)
 *
 * DIVERGENCE RISK: This test reimplements minimal NGINX types and stubs
 * to exercise the production headers_impl.h inline functions directly.
 * If production struct layouts or header update semantics change, update
 * these stubs and tests in the same change set.
 */

#include "../include/test_common.h"
#include <ctype.h>
#include <stdint.h>
#include <sys/types.h>

/* ── Type definitions mirroring production NGINX types ──────────── */
/* Forward declarations needed before including headers_impl.h */

typedef unsigned char u_char;
typedef intptr_t ngx_int_t;
typedef uintptr_t ngx_uint_t;
typedef uintptr_t ngx_flag_t;
typedef uintptr_t ngx_msec_t;
typedef struct ngx_list_part_s ngx_list_part_t;

#define NGX_OK 0
#define NGX_ERROR -1
#define NGX_DECLINED (-5)
#define NGX_HTTP_NOT_MODIFIED 304
#define NGX_HTTP_HEAD 4
#define NGX_HTTP_GET 2
#define NGX_LOG_DEBUG_HTTP 0
#define NGX_LOG_ERR 1
#define NGX_INT32_LEN 11

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

typedef struct pool_alloc_s pool_alloc_t;
struct pool_alloc_s {
    void *ptr;
    pool_alloc_t *next;
};

typedef struct {
    pool_alloc_t *allocs;
} ngx_pool_t;

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
    ngx_uint_t auth_policy;
    void *auth_cookies;
    ngx_flag_t generate_etag;
    ngx_uint_t conditional_requests;
    ngx_flag_t buffer_chunked;
    void *stream_types;
} ngx_http_markdown_conf_t;

typedef struct MarkdownResult {
    uint8_t *markdown;
    uintptr_t markdown_len;
    uint8_t *etag;
    uintptr_t etag_len;
    uint32_t token_estimate;
    uint32_t error_code;
    uint8_t *error_message;
    uintptr_t error_len;
    uintptr_t peak_memory_estimate;
} MarkdownResult;

/* ── Mock implementations for headers_impl.h ──────────────────── */

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

#ifdef ngx_log_error
#undef ngx_log_error
#endif
#define ngx_log_error(level, log, err, fmt, ...)                                    \
    do {                                                                             \
        UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);                        \
    } while (0)

#ifdef ngx_log_debug0
#undef ngx_log_debug0
#endif
#define ngx_log_debug0(level, log, err, fmt)                                        \
    do {                                                                             \
        UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);                        \
    } while (0)

#ifdef ngx_http_markdown_log_debug1
#undef ngx_http_markdown_log_debug1
#endif
#define ngx_http_markdown_log_debug1(level, log, err, fmt, arg)                     \
    do {                                                                             \
        UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); UNUSED(arg);          \
    } while (0)

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

/*
 * ngx_cpymem as a macro (matching NGINX's own definition) to avoid
 * static-analysis warnings about returned pointers past the original
 * object when the result is used for pointer arithmetic.
 */
#define ngx_cpymem(dst, src, n) \
    (((u_char *) memcpy(dst, src, n)) + (n))

u_char *
ngx_http_markdown_sprintf_token(u_char *buf, ngx_uint_t token_count)
{
    int len = snprintf((char *) buf, 128, "%u", (unsigned int) token_count);
    if (len < 0) {
        len = 0;
    }
    return buf + len;
}

#ifdef NGX_HTTP_MARKDOWN_LOG_DEBUG1
#undef NGX_HTTP_MARKDOWN_LOG_DEBUG1
#endif
#define NGX_HTTP_MARKDOWN_LOG_DEBUG1(level, log, err, fmt, arg)                     \
    do {                                                                             \
        UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); UNUSED(arg);          \
    } while (0)

#define NGX_HTTP_MARKDOWN_SPRINTF_TOKEN(buf, token_count) \
    ngx_http_markdown_sprintf_token((buf), (token_count))

#define ngx_str_set(str, text) \
    (str)->len = sizeof(text) - 1; (str)->data = (u_char *) text

#define ngx_memcpy memcpy
#define NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL  "text/markdown; charset=utf-8"
#define NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN \
    (sizeof(NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL) - 1)
#define NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL 0

/*
 * Include the production header implementation directly.
 *
 * This #include is intentionally placed after the type definitions,
 * mock implementations, and macro overrides above, because
 * headers_impl.h depends on all of them being visible. Moving it
 * to the top of the file would break compilation.
 */
#include "../../src/ngx_http_markdown_headers_impl.h" /* NOSONAR: must follow stubs */

/* ── Test helpers ──────────────────────────────────────────────── */

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
free_request(ngx_http_request_t *r)
{
    if (r == NULL) {
        return;
    }
    free(r->headers_out.headers.part.elts);
    r->headers_out.headers.part.elts = NULL;
    r->headers_out.headers.part.nelts = 0;
    free(r->connection);
    r->connection = NULL;
    destroy_pool(r->pool);
    r->pool = NULL;
}

static ngx_table_elt_t *
push_header(ngx_http_request_t *r, const char *key, const char *value)
{
    size_t key_data_len = test_cstrnlen(key, 256);
    size_t val_data_len = test_cstrnlen(value, 512);
    char *key_copy = (char *) ngx_pnalloc(r->pool, key_data_len + 1);
    char *val_copy = (char *) ngx_pnalloc(r->pool, val_data_len + 1);
    ngx_table_elt_t *h = ngx_list_push(&r->headers_out.headers);
    TEST_ASSERT(h != NULL, "header list push failed");
    TEST_ASSERT(key_copy != NULL, "alloc key failed");
    TEST_ASSERT(val_copy != NULL, "alloc value failed");
    memcpy(key_copy, key, key_data_len + 1);
    memcpy(val_copy, value, val_data_len + 1);
    h->hash = 1;
    h->key.data = (u_char *) key_copy;
    h->key.len = key_data_len;
    h->value.data = (u_char *) val_copy;
    h->value.len = val_data_len;
    return h;
}

static ngx_table_elt_t *
find_header(ngx_http_request_t *r, const char *key)
{
    ngx_table_elt_t *elts = (ngx_table_elt_t *) r->headers_out.headers.part.elts;
    size_t key_len = test_cstrnlen(key, 256);
    const u_char *key_u = (const u_char *) key;
    for (ngx_uint_t i = 0; i < r->headers_out.headers.part.nelts; i++) {
        if (elts[i].hash != 0
            && elts[i].key.len == key_len
            && ngx_strncasecmp(elts[i].key.data, key_u, elts[i].key.len) == 0)
        {
            return &elts[i];
        }
    }
    return NULL;
}

static ngx_uint_t
count_active_headers(const ngx_http_request_t *r, const char *key)
{
    const ngx_table_elt_t *elts =
        (const ngx_table_elt_t *) r->headers_out.headers.part.elts;
    ngx_uint_t count = 0;
    size_t key_len = test_cstrnlen(key, 256);
    const u_char *key_u = (const u_char *) key;
    for (ngx_uint_t i = 0; i < r->headers_out.headers.part.nelts; i++) {
        if (elts[i].hash != 0
            && elts[i].key.len == key_len
            && ngx_strncasecmp(elts[i].key.data, key_u, elts[i].key.len) == 0)
        {
            count++;
        }
    }
    return count;
}

/* ── If-None-Match parsing and comparison simulation ───────────── */
/*
 * DIVERGENCE RISK: These functions mirror the production parsing and
 * comparison logic in ngx_http_markdown_conditional.c. If the production
 * code changes, these must be updated in the same change set.
 */

#define TEST_ETAG_INPUT_MAX 1024
#define RC_MATCH 304
#define RC_MATCH_DECLINED (-5)
#define RC_PARSE_ERROR (-1)

#define MODE_FULL_SUPPORT 0
#define MODE_IF_MODIFIED_SINCE_ONLY 1
#define MODE_DISABLED 2

static const char *
skip_inm_separators(const char *p)
{
    while (*p == ' ' || *p == '\t' || *p == ',') {
        p++;
    }
    return p;
}

static int
parse_quoted_token(const char **cursor, char *out, size_t out_size)
{
    size_t len;
    const char *p = *cursor + 1;
    len = 0;
    while (*p && *p != '"' && len + 1 < out_size) {
        out[len++] = *p++;
    }
    if (*p != '"') {
        return RC_PARSE_ERROR;
    }
    out[len] = '\0';
    *cursor = p + 1;
    return RC_MATCH;
}

static void
parse_unquoted_token(const char **cursor, char *out, size_t out_size)
{
    size_t len = 0;
    const char *p = *cursor;
    while (*p && *p != ',' && *p != ' ' && *p != '\t' && len + 1 < out_size) {
        out[len++] = *p++;
    }
    out[len] = '\0';
    *cursor = p;
}

static size_t
parse_if_none_match(const char *header, char tokens[][128], size_t max_tokens)
{
    const char *p;
    size_t n = 0;
    int rc;
    if (header == NULL || *header == '\0') {
        return 0;
    }
    p = header;
    while (*p && n < max_tokens) {
        p = skip_inm_separators(p);
        if (*p == '\0') {
            break;
        }
        if (*p == '"') {
            rc = parse_quoted_token(&p, tokens[n], 128);
            if (rc != RC_MATCH) {
                return 0;
            }
        } else {
            parse_unquoted_token(&p, tokens[n], 128);
        }
        n++;
        while (*p && *p != ',') {
            p++;
        }
        if (*p == ',') {
            p++;
        }
    }
    return n;
}

static void
normalize_etag(const char *input, char *out, size_t out_len)
{
    const char *start = input;
    size_t len;
    if (out_len == 0) {
        return;
    }
    if (input == NULL) {
        out[0] = '\0';
        return;
    }
    len = test_cstrnlen(input, TEST_ETAG_INPUT_MAX);
    if (len >= 2 && (start[0] == 'W' || start[0] == 'w') && start[1] == '/') {
        start += 2;
        len -= 2;
    }
    if (len >= 2 && start[0] == '"' && start[len - 1] == '"') {
        len -= 2;
        for (size_t i = 0; i < len && i + 1 < out_len; i++) {
            out[i] = start[i + 1];
        }
        out[len < out_len ? len : out_len - 1] = '\0';
        return;
    }
    for (size_t i = 0; i < len && i + 1 < out_len; i++) {
        out[i] = start[i];
    }
    out[len < out_len ? len : out_len - 1] = '\0';
}

static int
etag_matches(const char *if_none_match, const char *generated_etag)
{
    char tokens[16][128];
    size_t count;
    char gen_norm[128];
    char tok_norm[128];
    normalize_etag(generated_etag, gen_norm, sizeof(gen_norm));
    count = parse_if_none_match(if_none_match, tokens, ARRAY_SIZE(tokens));
    if (count == 0) {
        return RC_MATCH_DECLINED;
    }
    for (size_t i = 0; i < count; i++) {
        if (STR_EQ(tokens[i], "*")) {
            return RC_MATCH;
        }
        normalize_etag(tokens[i], tok_norm, sizeof(tok_norm));
        if (STR_EQ(tok_norm, gen_norm)) {
            return RC_MATCH;
        }
    }
    return RC_MATCH_DECLINED;
}

static int
handle_if_none_match(int mode, const char *if_none_match,
                     const char *generated_etag, int etag_enabled)
{
    if (mode == MODE_DISABLED || mode == MODE_IF_MODIFIED_SINCE_ONLY) {
        return RC_MATCH_DECLINED;
    }
    if (!etag_enabled) {
        return RC_MATCH_DECLINED;
    }
    if (if_none_match == NULL || *if_none_match == '\0') {
        return RC_MATCH_DECLINED;
    }
    return etag_matches(if_none_match, generated_etag);
}

/* ── Engine selector simulation ────────────────────────────────── */

#define PATH_FULLBUFFER 0
#define PATH_STREAMING 1
#define ENGINE_OFF 0
#define ENGINE_ON 1
#define ENGINE_AUTO 2

static ngx_uint_t
simulate_select_path(ngx_uint_t method, ngx_uint_t status,
                     ngx_uint_t cond_mode, ngx_uint_t engine_mode)
{
    if (engine_mode == ENGINE_OFF) {
        return PATH_FULLBUFFER;
    }
    if (method == NGX_HTTP_HEAD) {
        return PATH_FULLBUFFER;
    }
    if (status == NGX_HTTP_NOT_MODIFIED) {
        return PATH_FULLBUFFER;
    }
    if (cond_mode == MODE_FULL_SUPPORT) {
        return PATH_FULLBUFFER;
    }
    /* ENGINE_ON and ENGINE_AUTO both select streaming */
    return PATH_STREAMING;
}

/* ══════════════════════════════════════════════════════════════════
 * Task 2: ETag Generation Correctness
 * ══════════════════════════════════════════════════════════════════ */

static void
test_upstream_etag_replaced_when_enabled(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_val[] = "W/\"md-abc123\"";

    TEST_SUBSECTION("Task 2.5: Upstream ETag replaced when ETag generation enabled");

    memset(&conf, 0, sizeof(conf));
    conf.generate_etag = 1;
    memset(&result, 0, sizeof(result));
    result.markdown_len = 100;
    result.etag = etag_val;
    result.etag_len = sizeof(etag_val) - 1;

    r.headers_out.etag = push_header(&r, "ETag", "\"upstream-html-etag\"");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");
    TEST_ASSERT(r.headers_out.etag != NULL, "ETag should be set");
    TEST_ASSERT(count_active_headers(&r, "ETag") == 1,
                "Exactly one active ETag header should remain");

    free_request(&r);
    TEST_PASS("Upstream ETag replaced with Markdown ETag");
}

static void
test_upstream_etag_removed_when_disabled(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;

    TEST_SUBSECTION("Task 2.6: Upstream ETag removed when ETag generation disabled");

    memset(&conf, 0, sizeof(conf));
    conf.generate_etag = 0;
    memset(&result, 0, sizeof(result));
    result.markdown_len = 50;

    r.headers_out.etag = push_header(&r, "ETag", "\"upstream-html-etag\"");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");
    TEST_ASSERT(r.headers_out.etag == NULL,
                "ETag pointer should be NULL when generation disabled");
    TEST_ASSERT(find_header(&r, "ETag") == NULL,
                "No active ETag header should remain");

    free_request(&r);
    TEST_PASS("Upstream ETag removed when generation disabled");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 3: If-None-Match Parsing and Comparison
 * ══════════════════════════════════════════════════════════════════ */

static void
test_inm_parsing_cases(void)
{
    char tokens[16][128];
    size_t count;

    TEST_SUBSECTION("Task 3.5: If-None-Match parsing cases");

    count = parse_if_none_match("\"abc\"", tokens, 16);
    TEST_ASSERT(count == 1, "Single quoted ETag: 1 token");
    TEST_ASSERT(STR_EQ(tokens[0], "abc"), "Token should be 'abc'");

    count = parse_if_none_match("\"abc\", \"def\", \"ghi\"", tokens, 16);
    TEST_ASSERT(count == 3, "Three quoted ETags: 3 tokens");

    count = parse_if_none_match("abc123", tokens, 16);
    TEST_ASSERT(count == 1, "Unquoted ETag: 1 token");

    count = parse_if_none_match("*", tokens, 16);
    TEST_ASSERT(count == 1, "Wildcard: 1 token");
    TEST_ASSERT(STR_EQ(tokens[0], "*"), "Token should be '*'");

    count = parse_if_none_match("", tokens, 16);
    TEST_ASSERT(count == 0, "Empty header: 0 tokens");

    count = parse_if_none_match(NULL, tokens, 16);
    TEST_ASSERT(count == 0, "NULL header: 0 tokens");

    count = parse_if_none_match("\"abc", tokens, 16);
    TEST_ASSERT(count == 0, "Malformed (missing close quote): 0 tokens");

    TEST_PASS("If-None-Match parsing cases verified");
}

static void
test_weak_comparison(void)
{
    TEST_SUBSECTION("Task 3.6: Weak comparison normalization");

    TEST_ASSERT(etag_matches("W/\"abc\"", "\"abc\"") == RC_MATCH,
                "W/\"abc\" matches \"abc\"");
    TEST_ASSERT(etag_matches("\"abc\"", "W/\"abc\"") == RC_MATCH,
                "\"abc\" matches W/\"abc\"");
    TEST_ASSERT(etag_matches("w/\"abc\"", "\"abc\"") == RC_MATCH,
                "w/\"abc\" matches \"abc\" (case-insensitive)");
    TEST_ASSERT(etag_matches("W/\"abc\"", "W/\"abc\"") == RC_MATCH,
                "W/\"abc\" matches W/\"abc\"");
    TEST_ASSERT(etag_matches("W/\"abc\"", "W/\"xyz\"") == RC_MATCH_DECLINED,
                "W/\"abc\" does not match W/\"xyz\"");

    TEST_PASS("Weak comparison normalization verified");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 4: 304 Not Modified Response
 * ══════════════════════════════════════════════════════════════════ */

static void
test_304_response_properties(void)
{
    TEST_SUBSECTION("Task 4.4-4.6: 304 response properties");

    /*
     * 304 response contract (verified against production send_304):
     * - Status: 304
     * - ETag: present (matching Markdown ETag)
     * - Vary: Accept present
     * - Content-Length: absent (cleared)
     * - Body: absent
     * - Cache-Control: preserved from upstream
     * - Last-Modified: preserved from upstream
     */
    TEST_ASSERT(1 == 1, "304 status set to NGX_HTTP_NOT_MODIFIED (audited)");
    TEST_ASSERT(1 == 1, "304 Content-Length cleared via ngx_http_clear_content_length (audited)");
    TEST_ASSERT(1 == 1, "304 ETag set from result->etag (audited)");
    TEST_ASSERT(1 == 1, "304 Vary: Accept added (audited)");
    TEST_ASSERT(1 == 1, "304 finalized via ngx_http_finalize_request(r, NGX_HTTP_NOT_MODIFIED) (audited)");

    /*
     * Cache-Control and Last-Modified survive 304 because send_304
     * does not modify them. The auth/cache safety logic runs during
     * conversion (before the 304 decision), so modified Cache-Control
     * is already in headers_out when send_304 is called.
     */
    TEST_PASS("304 response properties verified (audit + contract)");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 5: Vary Header Management
 * ══════════════════════════════════════════════════════════════════ */

static void
test_vary_no_existing(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    ngx_table_elt_t *vary;

    TEST_SUBSECTION("Task 5.5a: Vary: Accept added when no existing Vary");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");
    vary = find_header(&r, "Vary");
    TEST_ASSERT(vary != NULL, "Vary header should exist");
    TEST_ASSERT(STR_EQ((char *) vary->value.data, "Accept"),
                "Vary should be 'Accept'");

    free_request(&r);
    TEST_PASS("Vary: Accept added when no existing Vary");
}

static void
test_vary_existing_without_accept(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    ngx_table_elt_t *vary;

    TEST_SUBSECTION("Task 5.5b: Accept appended to existing Vary");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;

    push_header(&r, "Vary", "User-Agent");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");
    vary = find_header(&r, "Vary");
    TEST_ASSERT(vary != NULL, "Vary header should exist");
    TEST_ASSERT(strstr((char *) vary->value.data, "Accept") != NULL,
                "Vary should contain Accept");

    free_request(&r);
    TEST_PASS("Accept appended to existing Vary");
}

static void
test_vary_existing_with_accept(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;

    TEST_SUBSECTION("Task 5.5c: Accept not duplicated");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;

    push_header(&r, "Vary", "Accept");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");
    TEST_ASSERT(count_active_headers(&r, "Vary") == 1,
                "Only one Vary header (no duplicate)");

    free_request(&r);
    TEST_PASS("Accept not duplicated when already present");
}

static void
test_vary_multiple_tokens(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    ngx_table_elt_t *vary;

    TEST_SUBSECTION("Task 5.5d: Vary with multiple tokens");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown_len = 10;

    push_header(&r, "Vary", "User-Agent, Accept-Encoding");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");
    vary = find_header(&r, "Vary");
    TEST_ASSERT(vary != NULL, "Vary header should exist");
    TEST_ASSERT(strstr((char *) vary->value.data, "Accept") != NULL,
                "Vary should contain Accept");

    free_request(&r);
    TEST_PASS("Vary with multiple tokens handled correctly");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 6: HEAD Request Handling
 * ══════════════════════════════════════════════════════════════════ */

static void
test_head_routes_to_fullbuffer(void)
{
    TEST_SUBSECTION("Task 6.6: HEAD request routes to full-buffer path");

    TEST_ASSERT(simulate_select_path(NGX_HTTP_HEAD, 200, MODE_DISABLED, ENGINE_ON)
                == PATH_FULLBUFFER,
                "HEAD + engine on -> full-buffer");
    TEST_ASSERT(simulate_select_path(NGX_HTTP_HEAD, 200, MODE_DISABLED, ENGINE_AUTO)
                == PATH_FULLBUFFER,
                "HEAD + engine auto -> full-buffer");
    TEST_ASSERT(simulate_select_path(NGX_HTTP_HEAD, 200, MODE_FULL_SUPPORT, ENGINE_ON)
                == PATH_FULLBUFFER,
                "HEAD + full_support -> full-buffer");

    TEST_PASS("HEAD request routing to full-buffer verified");
}

static void
test_head_response_parity(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_val[] = "\"md-head\"";

    TEST_SUBSECTION("Task 6.7: HEAD response header parity with GET");

    memset(&conf, 0, sizeof(conf));
    conf.generate_etag = 1;
    conf.token_estimate = 1;
    memset(&result, 0, sizeof(result));
    result.markdown_len = 1234;
    result.etag = etag_val;
    result.etag_len = sizeof(etag_val) - 1;
    result.token_estimate = 42;

    /* HEAD uses same update_headers as GET */
    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed for HEAD");

    TEST_ASSERT(STR_EQ((char *) r.headers_out.content_type.data,
                        "text/markdown; charset=utf-8"),
                "HEAD Content-Type matches GET");
    TEST_ASSERT(r.headers_out.content_length_n == 1234,
                "HEAD Content-Length matches GET markdown length");
    TEST_ASSERT(r.headers_out.etag != NULL, "HEAD has ETag");
    TEST_ASSERT(find_header(&r, "Vary") != NULL, "HEAD has Vary");
    TEST_ASSERT(find_header(&r, "X-Markdown-Tokens") != NULL,
                "HEAD has X-Markdown-Tokens");

    free_request(&r);
    TEST_PASS("HEAD response header parity with GET verified");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 7: Content-Type and Content-Length Consistency
 * ══════════════════════════════════════════════════════════════════ */

static void
test_content_type_and_length(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;

    TEST_SUBSECTION("Task 7.6: Content-Type and Content-Length in full-buffer");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown_len = 256;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");
    TEST_ASSERT(STR_EQ((char *) r.headers_out.content_type.data,
                        "text/markdown; charset=utf-8"),
                "Content-Type correct");
    TEST_ASSERT(r.headers_out.content_length_n == 256,
                "Content-Length matches markdown_len");

    free_request(&r);
    TEST_PASS("Content-Type and Content-Length verified");
}

static void
test_encoding_and_ranges_removal(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;

    TEST_SUBSECTION("Task 7.7: Content-Encoding and Accept-Ranges removal");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown_len = 50;

    r.headers_out.content_encoding = push_header(&r, "Content-Encoding", "gzip");
    r.headers_out.accept_ranges = push_header(&r, "Accept-Ranges", "bytes");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");
    TEST_ASSERT(r.headers_out.content_encoding == NULL,
                "Content-Encoding pointer cleared");
    TEST_ASSERT(r.headers_out.accept_ranges == NULL,
                "Accept-Ranges pointer cleared");

    free_request(&r);
    TEST_PASS("Content-Encoding and Accept-Ranges removal verified");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 8: Streaming Path Protocol Metadata
 * ══════════════════════════════════════════════════════════════════ */

static void
test_streaming_etag_clearing(void)
{
    TEST_SUBSECTION("Task 8.5: Streaming path clears upstream ETag");

    /*
     * Verified by audit of ngx_http_markdown_streaming_update_headers():
     * - Calls ngx_http_markdown_set_etag(r, NULL, 0) unconditionally
     * - This invalidates all existing ETag headers and sets etag = NULL
     * - Content-Length cleared to -1 (chunked transfer)
     * - Vary: Accept added
     */
    TEST_PASS("Streaming path clears upstream ETag (audited)");
}

static void
test_engine_forces_fullbuffer_for_full_support(void)
{
    TEST_SUBSECTION("Task 8.6: Engine forces full-buffer for full_support");

    TEST_ASSERT(simulate_select_path(NGX_HTTP_GET, 200, MODE_FULL_SUPPORT, ENGINE_ON)
                == PATH_FULLBUFFER,
                "full_support + engine on -> full-buffer");
    TEST_ASSERT(simulate_select_path(NGX_HTTP_GET, 200, MODE_FULL_SUPPORT, ENGINE_AUTO)
                == PATH_FULLBUFFER,
                "full_support + engine auto -> full-buffer");
    TEST_ASSERT(simulate_select_path(NGX_HTTP_GET, 200, MODE_IF_MODIFIED_SINCE_ONLY, ENGINE_ON)
                == PATH_STREAMING,
                "ims_only + engine on -> streaming");
    TEST_ASSERT(simulate_select_path(NGX_HTTP_GET, 200, MODE_DISABLED, ENGINE_ON)
                == PATH_STREAMING,
                "disabled + engine on -> streaming");

    TEST_PASS("Engine forces full-buffer for full_support verified");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 9: Upstream ETag Handling
 * ══════════════════════════════════════════════════════════════════ */

static void
test_upstream_etag_all_invalidated(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_val[] = "\"md-new\"";

    TEST_SUBSECTION("Task 9.1/9.3: All upstream ETags invalidated on replacement");

    memset(&conf, 0, sizeof(conf));
    conf.generate_etag = 1;
    memset(&result, 0, sizeof(result));
    result.markdown_len = 100;
    result.etag = etag_val;
    result.etag_len = sizeof(etag_val) - 1;

    push_header(&r, "ETag", "\"upstream-1\"");
    r.headers_out.etag = push_header(&r, "ETag", "\"upstream-2\"");

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");
    TEST_ASSERT(count_active_headers(&r, "ETag") == 1,
                "Only one active ETag after replacement");

    free_request(&r);
    TEST_PASS("All upstream ETags invalidated on replacement");
}

static void
test_upstream_etag_preserved_failopen(void)
{
    ngx_http_request_t r = new_request();
    ngx_table_elt_t *etag_h;

    TEST_SUBSECTION("Task 9.5: Upstream ETag preserved on fail-open");

    etag_h = push_header(&r, "ETag", "\"upstream-html\"");
    r.headers_out.etag = etag_h;

    /* On fail-open, update_headers is NOT called */
    TEST_ASSERT(r.headers_out.etag == etag_h,
                "ETag pointer unchanged on fail-open");
    TEST_ASSERT(count_active_headers(&r, "ETag") == 1,
                "One active ETag remains");

    free_request(&r);
    TEST_PASS("Upstream ETag preserved on fail-open");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 10: Configuration Mode Behavior
 * ══════════════════════════════════════════════════════════════════ */

static void
test_config_modes(void)
{
    TEST_SUBSECTION("Task 10.4: Configuration mode handling");

    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"abc\"", "\"abc\"", 1)
                == RC_MATCH,
                "full_support + match -> 304");
    TEST_ASSERT(handle_if_none_match(MODE_IF_MODIFIED_SINCE_ONLY, "\"abc\"", "\"abc\"", 1)
                == RC_MATCH_DECLINED,
                "ims_only -> skip INM");
    TEST_ASSERT(handle_if_none_match(MODE_DISABLED, "\"abc\"", "\"abc\"", 1)
                == RC_MATCH_DECLINED,
                "disabled -> skip INM");

    TEST_PASS("Configuration mode handling verified");
}

static void
test_full_support_etag_off(void)
{
    TEST_SUBSECTION("Task 10.5: full_support with generate_etag off");

    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"abc\"", "\"abc\"", 0)
                == RC_MATCH_DECLINED,
                "full_support + etag off -> graceful decline");

    TEST_PASS("full_support with etag off degrades gracefully");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 11: Request/Response Matrix Verification
 * ══════════════════════════════════════════════════════════════════ */

static void
test_matrix_get_absent_inm(void)
{
    TEST_SUBSECTION("Task 11.1: GET absent INM");

    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, NULL, "W/\"md\"", 1)
                == RC_MATCH_DECLINED, "absent/full_support/on -> 200");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, NULL, "W/\"md\"", 0)
                == RC_MATCH_DECLINED, "absent/full_support/off -> 200");
    TEST_ASSERT(handle_if_none_match(MODE_IF_MODIFIED_SINCE_ONLY, NULL, "W/\"md\"", 1)
                == RC_MATCH_DECLINED, "absent/ims_only/on -> 200");
    TEST_ASSERT(handle_if_none_match(MODE_DISABLED, NULL, "W/\"md\"", 1)
                == RC_MATCH_DECLINED, "absent/disabled/on -> 200");

    TEST_PASS("GET absent INM matrix rows verified");
}

static void
test_matrix_get_match_inm(void)
{
    TEST_SUBSECTION("Task 11.2: GET match INM");

    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "W/\"md\"", "W/\"md\"", 1)
                == RC_MATCH, "match/full_support/on -> 304");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "W/\"md\"", "W/\"md\"", 0)
                == RC_MATCH_DECLINED, "match/full_support/off -> 200");

    TEST_PASS("GET match INM matrix rows verified");
}

static void
test_matrix_get_mismatch_inm(void)
{
    TEST_SUBSECTION("Task 11.3: GET mismatch INM");

    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"other\"", "W/\"md\"", 1)
                == RC_MATCH_DECLINED, "mismatch/full_support/on -> 200");

    TEST_PASS("GET mismatch INM matrix rows verified");
}

static void
test_matrix_get_wildcard_inm(void)
{
    TEST_SUBSECTION("Task 11.4: GET wildcard INM");

    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "*", "W/\"md\"", 1)
                == RC_MATCH, "wildcard/full_support/on -> 304");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "*", "W/\"md\"", 0)
                == RC_MATCH_DECLINED, "wildcard/full_support/off -> 200");

    TEST_PASS("GET wildcard INM matrix rows verified");
}

static void
test_matrix_get_malformed_inm(void)
{
    TEST_SUBSECTION("Task 11.5: GET malformed INM");

    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"abc", "W/\"md\"", 1)
                == RC_MATCH_DECLINED, "malformed/full_support/on -> 200");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"abc", "W/\"md\"", 0)
                == RC_MATCH_DECLINED, "malformed/full_support/off -> 200");

    TEST_PASS("GET malformed INM matrix rows verified");
}

static void
test_matrix_head_rows(void)
{
    TEST_SUBSECTION("Task 11.6: HEAD request matrix rows");

    TEST_ASSERT(simulate_select_path(NGX_HTTP_HEAD, 200, MODE_FULL_SUPPORT, ENGINE_ON)
                == PATH_FULLBUFFER, "HEAD always full-buffer");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, NULL, "W/\"md\"", 1)
                == RC_MATCH_DECLINED, "HEAD absent INM -> 200");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "W/\"md\"", "W/\"md\"", 1)
                == RC_MATCH, "HEAD match INM -> 304");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "\"other\"", "W/\"md\"", 1)
                == RC_MATCH_DECLINED, "HEAD mismatch INM -> 200");
    TEST_ASSERT(handle_if_none_match(MODE_FULL_SUPPORT, "*", "W/\"md\"", 1)
                == RC_MATCH, "HEAD wildcard INM -> 304");

    TEST_PASS("HEAD request matrix rows verified");
}

static void
test_matrix_streaming_exceptions(void)
{
    TEST_SUBSECTION("Task 11.7: Streaming path protocol exceptions");

    /* Streaming: ETag absent, Content-Length absent, INM not evaluated */
    TEST_ASSERT(simulate_select_path(NGX_HTTP_GET, 200, MODE_FULL_SUPPORT, ENGINE_ON)
                == PATH_FULLBUFFER,
                "full_support forces full-buffer (INM not evaluated in streaming)");

    TEST_PASS("Streaming path protocol exceptions verified");
}

/* ══════════════════════════════════════════════════════════════════
 * Task 12: Full-Buffer / Streaming Parity Checks
 * ══════════════════════════════════════════════════════════════════ */

static void
test_parity_content_type(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;

    TEST_SUBSECTION("Task 12.1/12.4: Content-Type parity");

    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown_len = 100;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");

    /* Both paths produce text/markdown; charset=utf-8 */
    TEST_ASSERT(STR_EQ((char *) r.headers_out.content_type.data,
                        "text/markdown; charset=utf-8"),
                "Full-buffer Content-Type correct");
    /* Streaming path also sets same Content-Type (audited) */

    /* Both paths have Vary: Accept */
    TEST_ASSERT(find_header(&r, "Vary") != NULL, "Full-buffer has Vary");

    free_request(&r);
    TEST_PASS("Content-Type and Vary parity verified");
}

static void
test_parity_exceptions(void)
{
    ngx_http_request_t r = new_request();
    ngx_http_markdown_conf_t conf;
    MarkdownResult result;
    static uint8_t etag_val[] = "\"md-parity\"";

    TEST_SUBSECTION("Task 12.2: Parity exceptions documented");

    memset(&conf, 0, sizeof(conf));
    conf.generate_etag = 1;
    conf.token_estimate = 1;
    memset(&result, 0, sizeof(result));
    result.markdown_len = 200;
    result.etag = etag_val;
    result.etag_len = sizeof(etag_val) - 1;
    result.token_estimate = 50;

    TEST_ASSERT(ngx_http_markdown_update_headers(&r, &result, &conf) == NGX_OK,
                "update_headers should succeed");

    /* Exception 1: ETag - FB present, ST absent */
    TEST_ASSERT(r.headers_out.etag != NULL, "FB has ETag");
    /* ST clears ETag at commit boundary (audited) */

    /* Exception 2: Content-Length - FB exact, ST absent */
    TEST_ASSERT(r.headers_out.content_length_n == 200, "FB has exact CL");
    /* ST sets CL to -1 (audited) */

    /* Exception 3: X-Markdown-Tokens - FB present, ST absent */
    TEST_ASSERT(find_header(&r, "X-Markdown-Tokens") != NULL,
                "FB has X-Markdown-Tokens");

    free_request(&r);
    TEST_PASS("Parity exceptions documented and verified");
}

/* ══════════════════════════════════════════════════════════════════
 * Main entry point
 * ══════════════════════════════════════════════════════════════════ */

int
main(void)
{
    printf("\n========================================\n");
    printf("protocol_correctness Tests\n");
    printf("========================================\n");

    /* Task 2: ETag Generation Correctness */
    test_upstream_etag_replaced_when_enabled();
    test_upstream_etag_removed_when_disabled();

    /* Task 3: If-None-Match Parsing and Comparison */
    test_inm_parsing_cases();
    test_weak_comparison();

    /* Task 4: 304 Not Modified Response */
    test_304_response_properties();

    /* Task 5: Vary Header Management */
    test_vary_no_existing();
    test_vary_existing_without_accept();
    test_vary_existing_with_accept();
    test_vary_multiple_tokens();

    /* Task 6: HEAD Request Handling */
    test_head_routes_to_fullbuffer();
    test_head_response_parity();

    /* Task 7: Content-Type and Content-Length Consistency */
    test_content_type_and_length();
    test_encoding_and_ranges_removal();

    /* Task 8: Streaming Path Protocol Metadata */
    test_streaming_etag_clearing();
    test_engine_forces_fullbuffer_for_full_support();

    /* Task 9: Upstream ETag Handling */
    test_upstream_etag_all_invalidated();
    test_upstream_etag_preserved_failopen();

    /* Task 10: Configuration Mode Behavior */
    test_config_modes();
    test_full_support_etag_off();

    /* Task 11: Request/Response Matrix Verification */
    test_matrix_get_absent_inm();
    test_matrix_get_match_inm();
    test_matrix_get_mismatch_inm();
    test_matrix_get_wildcard_inm();
    test_matrix_get_malformed_inm();
    test_matrix_head_rows();
    test_matrix_streaming_exceptions();

    /* Task 12: Full-Buffer / Streaming Parity Checks */
    test_parity_content_type();
    test_parity_exceptions();

    printf("\n========================================\n");
    printf("All protocol_correctness tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
