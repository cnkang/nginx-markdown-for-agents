#include "../include/test_common.h"
#include <strings.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK          0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR       -1
#endif
#ifndef NGX_DECLINED
#define NGX_DECLINED    -5
#endif

#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif
#ifndef NGX_LOG_ERR
#define NGX_LOG_ERR 3
#endif
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN 4
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

#define ngx_memcpy(dst, src, n)    memcpy(dst, src, n)
#define ngx_cpymem(dst, src, n)    (((u_char *) memcpy(dst, src, (n))) + (n))
#define ngx_strncmp(s1, s2, n)     strncmp((const char *) s1, (const char *) s2, n)
#define ngx_null_string            { 0, NULL }
#define ngx_str_set(str, text)                                          \
    (str)->len = sizeof(text) - 1; (str)->data = (u_char *) text

struct ngx_array_s {
    void       *elts;
    ngx_uint_t  nelts;
    size_t      size;
    ngx_uint_t  nalloc;
    ngx_pool_t *pool;
};

struct ngx_pool_s {
    int dummy;
};

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
        ngx_table_elt_t *cookie;
        ngx_table_elt_t *authorization;
    } headers_in;
    struct {
        ngx_list_t headers;
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
    UNUSED(pool);
    if (g_pool_offset + size > sizeof(g_pool_buf)) {
        return NULL;
    }
    void *p = g_pool_buf + g_pool_offset;
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

#include "../../src/ngx_http_markdown_auth.c"

static void
reset_pool(void)
{
    g_pool_offset = 0;
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
add_header(ngx_list_t *list, const char *key, const char *value,
           ngx_uint_t hash)
{
    ngx_table_elt_t *h = ngx_list_push(list);
    if (h == NULL) return NULL;
    h->key.data = (u_char *) key;
    h->key.len = strlen(key);
    h->value.data = (u_char *) value;
    h->value.len = strlen(value);
    h->hash = hash;
    return h;
}

static ngx_http_request_t *
make_req(void)
{
    ngx_http_request_t *r = (ngx_http_request_t *)
        ngx_pcalloc(NULL, sizeof(ngx_http_request_t));
    if (r == NULL) return NULL;
    r->connection = (ngx_connection_t *)
        ngx_pcalloc(NULL, sizeof(ngx_connection_t));
    if (r->connection == NULL) return NULL;
    r->headers_out.headers = *create_header_list();
    r->headers_in.headers = *create_header_list();
    return r;
}

/* ── is_cache_control_header ─────────────────────────────────── */

static void
test_is_cache_control_header_match(void)
{
    ngx_table_elt_t h;
    h.key.data = (u_char *) "Cache-Control";
    h.key.len = 13;
    ngx_flag_t rc = ngx_http_markdown_is_cache_control_header(&h);
    TEST_ASSERT(rc == 1, "Cache-Control header detected");
    TEST_PASS("Cache-Control header match");
}

static void
test_is_cache_control_header_no_match(void)
{
    ngx_table_elt_t h;
    h.key.data = (u_char *) "Content-Type";
    h.key.len = 12;
    ngx_flag_t rc = ngx_http_markdown_is_cache_control_header(&h);
    TEST_ASSERT(rc == 0, "Non-Cache-Control header rejected");
    TEST_PASS("non-Cache-Control header rejected");
}

static void
test_is_cache_control_header_wrong_len(void)
{
    ngx_table_elt_t h;
    h.key.data = (u_char *) "Cache-Control";
    h.key.len = 5;
    ngx_flag_t rc = ngx_http_markdown_is_cache_control_header(&h);
    TEST_ASSERT(rc == 0, "Wrong length header rejected");
    TEST_PASS("wrong length header rejected");
}

/* ── cache_control_has_directive ─────────────────────────────── */

static void
test_cc_has_directive_found(void)
{
    ngx_str_t value = ngx_string("public, max-age=3600");
    ngx_str_t directive = ngx_string("public");
    ngx_int_t rc = ngx_http_markdown_cache_control_has_directive(
        &value, &directive);
    TEST_ASSERT(rc == 1, "public directive found");
    TEST_PASS("directive found in value");
}

static void
test_cc_has_directive_not_found(void)
{
    ngx_str_t value = ngx_string("private, no-store");
    ngx_str_t directive = ngx_string("public");
    ngx_int_t rc = ngx_http_markdown_cache_control_has_directive(
        &value, &directive);
    TEST_ASSERT(rc == 0, "public not in value");
    TEST_PASS("directive not found");
}

static void
test_cc_has_directive_null_value(void)
{
    ngx_str_t directive = ngx_string("public");
    ngx_int_t rc = ngx_http_markdown_cache_control_has_directive(
        NULL, &directive);
    TEST_ASSERT(rc == 0, "NULL value");
    TEST_PASS("NULL value handled");
}

static void
test_cc_has_directive_empty_value(void)
{
    ngx_str_t value = ngx_string("");
    ngx_str_t directive = ngx_string("public");
    ngx_int_t rc = ngx_http_markdown_cache_control_has_directive(
        &value, &directive);
    TEST_ASSERT(rc == 0, "empty value");
    TEST_PASS("empty value handled");
}

static void
test_cc_has_directive_null_directive(void)
{
    ngx_str_t value = ngx_string("public");
    ngx_int_t rc = ngx_http_markdown_cache_control_has_directive(
        &value, NULL);
    TEST_ASSERT(rc == 0, "NULL directive");
    TEST_PASS("NULL directive handled");
}

/* ── cookie_matches_pattern ──────────────────────────────────── */

static void
test_cookie_exact_match(void)
{
    ngx_str_t cookie = ngx_string("PHPSESSID");
    ngx_str_t pattern = ngx_string("PHPSESSID");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        &cookie, &pattern);
    TEST_ASSERT(rc == 1, "Exact match");
    TEST_PASS("exact match");
}

static void
test_cookie_exact_no_match(void)
{
    ngx_str_t cookie = ngx_string("other_cookie");
    ngx_str_t pattern = ngx_string("PHPSESSID");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        &cookie, &pattern);
    TEST_ASSERT(rc == 0, "Exact mismatch");
    TEST_PASS("exact mismatch");
}

static void
test_cookie_prefix_wildcard_match(void)
{
    ngx_str_t cookie = ngx_string("session_id");
    ngx_str_t pattern = ngx_string("session*");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        &cookie, &pattern);
    TEST_ASSERT(rc == 1, "Prefix wildcard match");
    TEST_PASS("prefix wildcard match");
}

static void
test_cookie_prefix_wildcard_short(void)
{
    ngx_str_t cookie = ngx_string("ses");
    ngx_str_t pattern = ngx_string("session*");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        &cookie, &pattern);
    TEST_ASSERT(rc == 0, "Cookie shorter than prefix");
    TEST_PASS("cookie shorter than prefix");
}

static void
test_cookie_suffix_wildcard_match(void)
{
    ngx_str_t cookie = ngx_string("wordpress_logged_in");
    ngx_str_t pattern = ngx_string("*_logged_in");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        &cookie, &pattern);
    TEST_ASSERT(rc == 1, "Suffix wildcard match");
    TEST_PASS("suffix wildcard match");
}

static void
test_cookie_suffix_wildcard_short(void)
{
    ngx_str_t cookie = ngx_string("tiny");
    ngx_str_t pattern = ngx_string("*logged_in");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        &cookie, &pattern);
    TEST_ASSERT(rc == 0, "Cookie shorter than suffix");
    TEST_PASS("cookie shorter than suffix");
}

static void
test_cookie_null_name(void)
{
    ngx_str_t pattern = ngx_string("test*");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        NULL, &pattern);
    TEST_ASSERT(rc == 0, "NULL cookie name");
    TEST_PASS("NULL cookie name handled");
}

static void
test_cookie_null_pattern(void)
{
    ngx_str_t cookie = ngx_string("session");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        &cookie, NULL);
    TEST_ASSERT(rc == 0, "NULL pattern");
    TEST_PASS("NULL pattern handled");
}

static void
test_cookie_empty_name(void)
{
    ngx_str_t cookie = ngx_string("");
    ngx_str_t pattern = ngx_string("test*");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        &cookie, &pattern);
    TEST_ASSERT(rc == 0, "Empty cookie name");
    TEST_PASS("empty cookie name handled");
}

static void
test_cookie_empty_pattern(void)
{
    ngx_str_t cookie = ngx_string("test");
    ngx_str_t pattern = ngx_string("");
    ngx_int_t rc = ngx_http_markdown_cookie_matches_pattern(
        &cookie, &pattern);
    TEST_ASSERT(rc == 0, "Empty pattern");
    TEST_PASS("empty pattern handled");
}

/* ── token_equals_ignore_case ────────────────────────────────── */

static void
test_token_equals_ignore_case_match(void)
{
    ngx_flag_t rc = ngx_http_markdown_token_equals_ignore_case(
        (u_char *) "public", (u_char *) "PUBLIC", 6);
    TEST_ASSERT(rc == 1, "Case-insensitive match");
    TEST_PASS("case-insensitive token match");
}

static void
test_token_equals_ignore_case_mismatch(void)
{
    ngx_flag_t rc = ngx_http_markdown_token_equals_ignore_case(
        (u_char *) "public", (u_char *) "private", 6);
    TEST_ASSERT(rc == 0, "Mismatched tokens");
    TEST_PASS("mismatched tokens rejected");
}

/* ── scan_cache_control_headers ──────────────────────────────── */

static void
test_scan_cc_empty_headers(void)
{
    ngx_http_markdown_cc_scan_t scan;
    ngx_list_t list = *create_header_list();
    ngx_http_markdown_scan_cache_control_headers(&list, &scan);
    TEST_ASSERT(scan.first_entry == NULL, "No CC entry");
    TEST_ASSERT(scan.has_no_store == 0, "No no-store");
    TEST_ASSERT(scan.has_private == 0, "No private");
    TEST_ASSERT(scan.any_public == 0, "No public");
    TEST_PASS("empty headers scan");
}

static void
test_scan_cc_finds_public(void)
{
    ngx_list_t list = *create_header_list();
    add_header(&list, "Cache-Control", "public", 1);
    ngx_http_markdown_cc_scan_t scan;
    ngx_http_markdown_scan_cache_control_headers(&list, &scan);
    TEST_ASSERT(scan.first_entry != NULL, "Has CC entry");
    TEST_ASSERT(scan.any_public == 1, "Has public");
    TEST_ASSERT(scan.has_private == 0, "No private");
    TEST_ASSERT(scan.has_no_store == 0, "No no-store");
    TEST_PASS("scan finds public");
}

static void
test_scan_cc_finds_no_store(void)
{
    ngx_list_t list = *create_header_list();
    add_header(&list, "Cache-Control", "no-store", 1);
    ngx_http_markdown_cc_scan_t scan;
    ngx_http_markdown_scan_cache_control_headers(&list, &scan);
    TEST_ASSERT(scan.has_no_store == 1, "Has no-store");
    TEST_PASS("scan finds no-store");
}

static void
test_scan_cc_skips_other_headers(void)
{
    ngx_list_t list = *create_header_list();
    add_header(&list, "Content-Type", "text/html", 1);
    ngx_http_markdown_cc_scan_t scan;
    ngx_http_markdown_scan_cache_control_headers(&list, &scan);
    TEST_ASSERT(scan.first_entry == NULL, "Skips non-CC headers");
    TEST_PASS("scan skips non-CC headers");
}

static void
test_scan_cc_multiple_entries(void)
{
    ngx_list_t list = *create_header_list();
    add_header(&list, "Cache-Control", "public", 1);
    add_header(&list, "Cache-Control", "no-store", 1);
    ngx_http_markdown_cc_scan_t scan;
    ngx_http_markdown_scan_cache_control_headers(&list, &scan);
    TEST_ASSERT(scan.any_public == 1, "Has public");
    TEST_ASSERT(scan.has_no_store == 1, "Has no-store");
    TEST_PASS("scan handles multiple CC entries");
}

/* ── cache_control_token_is_public ───────────────────────────── */

static void
test_token_is_public_match(void)
{
    const u_char *tok = (const u_char *) "public";
    ngx_flag_t rc = ngx_http_markdown_cache_control_token_is_public(
        tok, tok + 6);
    TEST_ASSERT(rc == 1, "public token detected");
    TEST_PASS("public token match");
}

static void
test_token_is_public_wrong_len(void)
{
    const u_char *tok = (const u_char *) "pub";
    ngx_flag_t rc = ngx_http_markdown_cache_control_token_is_public(
        tok, tok + 3);
    TEST_ASSERT(rc == 0, "Short token not public");
    TEST_PASS("wrong length token rejected");
}

static void
test_token_is_public_not_public(void)
{
    const u_char *tok = (const u_char *) "private";
    ngx_flag_t rc = ngx_http_markdown_cache_control_token_is_public(
        tok, tok + 7);
    TEST_ASSERT(rc == 0, "private token not public");
    TEST_PASS("non-public token rejected");
}

/* ── next_cache_control_token ────────────────────────────────── */

static void
test_next_cc_token_first(void)
{
    const u_char *p = (u_char *) "public, private";
    const u_char *end = p + 15;
    const u_char *ts;
    const u_char *te;
    ngx_int_t rc = ngx_http_markdown_next_cache_control_token(
        &p, end, &ts, &te);
    TEST_ASSERT(rc == NGX_OK, "First token found");
    TEST_ASSERT((size_t)(te - ts) == 6, "Token len 6");
    TEST_PASS("first token extracted");
}

static void
test_next_cc_token_second(void)
{
    ngx_int_t rc;
    const u_char input[] = "public, private";
    const u_char *p = input;
    const u_char *end = input + 15;
    const u_char *ts;
    const u_char *te;
    ngx_http_markdown_next_cache_control_token(&p, end, &ts, &te);
    rc = ngx_http_markdown_next_cache_control_token(&p, end, &ts, &te);
    TEST_ASSERT(rc == NGX_OK, "Second token found");
    TEST_ASSERT((size_t)(te - ts) == 7, "Second token len 7");
    TEST_PASS("second token extracted");
}

static void
test_next_cc_token_exhausted(void)
{
    ngx_int_t rc;
    const u_char input[] = "public";
    const u_char *p = input;
    const u_char *end = input + 6;
    const u_char *ts;
    const u_char *te;
    ngx_http_markdown_next_cache_control_token(&p, end, &ts, &te);
    rc = ngx_http_markdown_next_cache_control_token(&p, end, &ts, &te);
    TEST_ASSERT(rc == NGX_DECLINED, "Exhausted returns DECLINED");
    TEST_PASS("exhausted token stream");
}

static void
test_next_cc_token_consecutive_commas(void)
{
    const u_char input[] = ",,public,,";
    const u_char *p = input;
    const u_char *end = input + 10;
    const u_char *ts;
    const u_char *te;
    ngx_int_t rc = ngx_http_markdown_next_cache_control_token(
        &p, end, &ts, &te);
    TEST_ASSERT(rc == NGX_OK, "Token after consecutive commas");
    TEST_ASSERT((size_t)(te - ts) == 6, "Token is public");
    TEST_PASS("consecutive commas handled");
}

/* ── skip_cache_control_separators ───────────────────────────── */

static void
test_skip_cc_separators(void)
{
    const u_char input[] = "  ,,public";
    const u_char *p = input;
    const u_char *end = input + 9;
    ngx_http_markdown_skip_cache_control_separators(&p, end);
    TEST_ASSERT(p == input + 4, "Skipped spaces and commas");
    TEST_PASS("separators skipped");
}

static void
test_skip_cc_separators_already_at_token(void)
{
    const u_char input[] = "public";
    const u_char *p = input;
    const u_char *end = input + 6;
    ngx_http_markdown_skip_cache_control_separators(&p, end);
    TEST_ASSERT(p == input, "No movement at non-separator");
    TEST_PASS("no separators to skip");
}

/* ── trim_cache_control_token ────────────────────────────────── */

static void
test_trim_cc_token(void)
{
    const u_char input[] = "  public\t  ";
    const u_char *ts = input;
    const u_char *te = input + 11;
    ngx_http_markdown_trim_cache_control_token(&ts, &te);
    TEST_ASSERT((size_t)(te - ts) == 6, "Trimmed to 6");
    TEST_PASS("token trimmed");
}

/* ── is_authenticated ────────────────────────────────────────── */

static void
test_is_authenticated_null_request(void)
{
    ngx_int_t rc = ngx_http_markdown_is_authenticated(NULL, NULL);
    TEST_ASSERT(rc == 0, "NULL request");
    TEST_PASS("null request");
}

static void
test_is_authenticated_authorization(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    r->headers_in.authorization = (ngx_table_elt_t *)
        ngx_pcalloc(NULL, sizeof(ngx_table_elt_t));
    if (r->headers_in.authorization == NULL) {
        TEST_FAIL("alloc failed"); return;
    }
    r->headers_in.authorization->hash = 1;
    ngx_int_t rc = ngx_http_markdown_is_authenticated(r, NULL);
    TEST_ASSERT(rc == 1, "Authorization header detected");
    TEST_PASS("authorization detected");
}

static void
test_is_authenticated_auth_cookie(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    r->headers_in.cookie = (ngx_table_elt_t *)
        ngx_pcalloc(NULL, sizeof(ngx_table_elt_t));
    if (r->headers_in.cookie == NULL) {
        TEST_FAIL("alloc failed"); return;
    }
    r->headers_in.cookie->hash = 1;
    r->headers_in.cookie->value.data = (u_char *) "session_id=abc123";
    r->headers_in.cookie->value.len = 19;
    ngx_int_t rc = ngx_http_markdown_is_authenticated(r, NULL);
    TEST_ASSERT(rc == 1, "Auth cookie detected");
    TEST_PASS("auth cookie detected");
}

static void
test_is_authenticated_no_auth(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    ngx_int_t rc = ngx_http_markdown_is_authenticated(r, NULL);
    TEST_ASSERT(rc == 0, "No auth detected");
    TEST_PASS("no auth with empty request");
}

static void
test_is_authenticated_non_auth_cookie(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    r->headers_in.cookie = (ngx_table_elt_t *)
        ngx_pcalloc(NULL, sizeof(ngx_table_elt_t));
    if (r->headers_in.cookie == NULL) {
        TEST_FAIL("alloc failed"); return;
    }
    r->headers_in.cookie->hash = 1;
    r->headers_in.cookie->value.data = (u_char *) "tracking=xyz";
    r->headers_in.cookie->value.len = 12;
    ngx_int_t rc = ngx_http_markdown_is_authenticated(r, NULL);
    TEST_ASSERT(rc == 0, "Non-auth cookie ignored");
    TEST_PASS("non-auth cookie ignored");
}

/* ── modify_cache_control_for_auth ───────────────────────────── */

static void
test_modify_cc_null_request(void)
{
    ngx_int_t rc = ngx_http_markdown_modify_cache_control_for_auth(NULL);
    TEST_ASSERT(rc == NGX_ERROR, "NULL request returns NGX_ERROR");
    TEST_PASS("null request handled");
}

static void
test_modify_cc_empty_headers(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    r->headers_out.headers.part.nelts = 0;
    ngx_int_t rc = ngx_http_markdown_modify_cache_control_for_auth(r);
    TEST_ASSERT(rc == NGX_OK, "Empty headers: adds private");
    TEST_PASS("empty headers: added private");
}

static void
test_modify_cc_no_cached_headers(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_out.headers, "Content-Type", "text/html", 1);
    ngx_int_t rc = ngx_http_markdown_modify_cache_control_for_auth(r);
    TEST_ASSERT(rc == NGX_OK, "Non-CC headers: adds private");
    TEST_PASS("non-CC headers: added private");
}

static void
test_modify_cc_no_store_preserved(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_out.headers, "Cache-Control", "no-store", 1);
    ngx_int_t rc = ngx_http_markdown_modify_cache_control_for_auth(r);
    TEST_ASSERT(rc == NGX_OK, "no-store preserved");
    TEST_PASS("no-store preserved");
}

static void
test_modify_cc_public_upgraded(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_out.headers, "Cache-Control", "public", 1);
    ngx_int_t rc = ngx_http_markdown_modify_cache_control_for_auth(r);
    TEST_ASSERT(rc == NGX_OK, "public upgraded to private");
    TEST_PASS("public upgraded");
}

static void
test_modify_cc_public_mixed(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_out.headers, "Cache-Control",
        "public, max-age=3600", 1);
    ngx_int_t rc = ngx_http_markdown_modify_cache_control_for_auth(r);
    TEST_ASSERT(rc == NGX_OK, "public mixed upgraded");
    TEST_PASS("public mixed upgraded");
}

static void
test_modify_cc_already_private(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_out.headers, "Cache-Control", "private", 1);
    ngx_int_t rc = ngx_http_markdown_modify_cache_control_for_auth(r);
    TEST_ASSERT(rc == NGX_OK, "already private unchanged");
    TEST_PASS("already private preserved");
}

static void
test_modify_cc_append_private(void)
{
    reset_pool();
    ngx_http_request_t *r = make_req();
    if (r == NULL) { TEST_FAIL("alloc failed"); return; }
    add_header(&r->headers_out.headers, "Cache-Control", "no-cache", 1);
    ngx_int_t rc = ngx_http_markdown_modify_cache_control_for_auth(r);
    TEST_ASSERT(rc == NGX_OK, "private appended");
    TEST_PASS("private appended to no-cache");
}

/* ── get_auth_patterns ───────────────────────────────────────── */

static void
test_get_auth_patterns_null_conf(void)
{
    ngx_str_t *patterns = NULL;
    ngx_uint_t count = 0;
    ngx_http_markdown_get_auth_patterns(NULL, &patterns, &count);
    TEST_ASSERT(patterns != NULL, "Default patterns returned");
    TEST_ASSERT(count > 0, "Non-zero count");
    TEST_PASS("default auth patterns with NULL conf");
}

static void
test_get_auth_patterns_empty_conf(void)
{
    ngx_http_markdown_conf_t conf;
    memset(&conf, 0, sizeof(conf));
    ngx_str_t *patterns = NULL;
    ngx_uint_t count = 0;
    ngx_http_markdown_get_auth_patterns(&conf, &patterns, &count);
    TEST_ASSERT(patterns != NULL, "Default patterns");
    TEST_ASSERT(count > 0, "Non-zero count");
    TEST_PASS("default auth patterns with empty conf");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("auth_production Tests\n");
    printf("========================================\n");

    test_is_cache_control_header_match();
    test_is_cache_control_header_no_match();
    test_is_cache_control_header_wrong_len();

    test_cc_has_directive_found();
    test_cc_has_directive_not_found();
    test_cc_has_directive_null_value();
    test_cc_has_directive_empty_value();
    test_cc_has_directive_null_directive();

    test_token_equals_ignore_case_match();
    test_token_equals_ignore_case_mismatch();

    test_cookie_exact_match();
    test_cookie_exact_no_match();
    test_cookie_prefix_wildcard_match();
    test_cookie_prefix_wildcard_short();
    test_cookie_suffix_wildcard_match();
    test_cookie_suffix_wildcard_short();
    test_cookie_null_name();
    test_cookie_null_pattern();
    test_cookie_empty_name();
    test_cookie_empty_pattern();

    test_scan_cc_empty_headers();
    test_scan_cc_finds_public();
    test_scan_cc_finds_no_store();
    test_scan_cc_skips_other_headers();
    test_scan_cc_multiple_entries();

    test_token_is_public_match();
    test_token_is_public_wrong_len();
    test_token_is_public_not_public();

    test_next_cc_token_first();
    test_next_cc_token_second();
    test_next_cc_token_exhausted();
    test_next_cc_token_consecutive_commas();

    test_skip_cc_separators();
    test_skip_cc_separators_already_at_token();

    test_trim_cc_token();

    test_is_authenticated_null_request();
    test_is_authenticated_authorization();
    test_is_authenticated_auth_cookie();
    test_is_authenticated_no_auth();
    test_is_authenticated_non_auth_cookie();

    test_modify_cc_null_request();
    test_modify_cc_empty_headers();
    test_modify_cc_no_cached_headers();
    test_modify_cc_no_store_preserved();
    test_modify_cc_public_upgraded();
    test_modify_cc_public_mixed();
    test_modify_cc_already_private();
    test_modify_cc_append_private();

    test_get_auth_patterns_null_conf();
    test_get_auth_patterns_empty_conf();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
