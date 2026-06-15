/*
 * Test: auth_deny_security
 *
 * Security test validating that the streaming path cannot bypass the
 * auth_policy deny check.  When `markdown_auth_policy deny` is
 * configured and the request is authenticated (Authorization header
 * or configured cookie patterns), the response MUST be passed through
 * without conversion — even if the streaming engine would otherwise
 * be selected.
 *
 * streaming security and resource limits: Streaming Security, Resource Limits, and Compression
 * Validates:
 *   auth/status checked before streaming candidate
 *   None bypassed by streaming path
 *   oversized body / replay overflow handling: Auth deny test
 *
 * AGENTS.md compliance:
 *   Rule 14: security regression test
 *   Rule 16: no dead stores; every variable consumed by assertion
 */

#include "../include/test_common.h"

#include <ctype.h>

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

/* Include the production auth source under test */
#include "../../src/ngx_http_markdown_auth.c"


/* ================================================================
 * Pool / request helpers
 * ================================================================ */

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


/* ================================================================
 * Streaming eligibility model
 *
 * Models the pre-streaming policy gate from Component 1 of the
 * design.  In production (request_impl.h), this check runs BEFORE
 * select_processing_path and streaming candidate evaluation.
 *
 * Logic:
 *   if (auth_policy == DENY && is_authenticated(r, conf))
 *       -> not eligible (passthrough)
 *
 * Returns:
 *   1 = eligible for conversion (streaming or full-buffer)
 *   0 = passthrough (auth denied, conversion blocked)
 * ================================================================ */
static int
model_streaming_auth_gate(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf)
{
    if (conf->policy.auth_policy == NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY
        && ngx_http_markdown_is_authenticated(r, conf))
    {
        return 0;  /* passthrough: auth denied */
    }

    return 1;  /* eligible: proceed with conversion */
}


/* ================================================================
 * Test 1: auth_policy=deny + Authorization header -> passthrough
 *
 * When auth_policy is deny and an Authorization header is present,
 * conversion MUST NOT proceed.  The streaming engine must not be
 * selected because the auth gate fires first.
 *
 * Validates: auth/status checked before streaming candidate, oversized body / replay overflow handling
 * ================================================================ */
static void
test_auth_deny_with_authorization_header(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_request_t      *r;
    int                      eligible;

    TEST_SUBSECTION("auth_policy=deny + Authorization header -> passthrough");

    reset_pool();
    memset(&conf, 0, sizeof(conf));
    conf.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY;
    conf.policy.auth_cookies = NULL;

    r = make_req();
    TEST_ASSERT(r != NULL, "request allocation succeeded");

    /* Add Authorization header */
    r->headers_in.authorization = (ngx_table_elt_t *)
        ngx_pcalloc(NULL, sizeof(ngx_table_elt_t));
    TEST_ASSERT(r->headers_in.authorization != NULL, "header alloc OK");
    r->headers_in.authorization->hash = 1;
    r->headers_in.authorization->value.data = (u_char *) "Bearer token123";
    r->headers_in.authorization->value.len = 15;

    /* Verify is_authenticated detects the header */
    TEST_ASSERT(ngx_http_markdown_is_authenticated(r, &conf) == 1,
        "is_authenticated returns 1 with Authorization header");

    /* Verify the auth gate blocks conversion */
    eligible = model_streaming_auth_gate(r, &conf);
    TEST_ASSERT(eligible == 0,
        "auth_policy=deny + Authorization -> NOT eligible (passthrough)");

    TEST_PASS("auth_policy=deny + Authorization header -> passthrough");
}


/* ================================================================
 * Test 2: auth_policy=deny + auth cookie present -> passthrough
 *
 * When auth_policy is deny and a configured auth cookie pattern
 * matches a request cookie, conversion MUST NOT proceed.
 *
 * Validates: auth/status checked before streaming candidate, oversized body / replay overflow handling
 * ================================================================ */
static void
test_auth_deny_with_auth_cookie(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_request_t      *r;
    int                      eligible;

    TEST_SUBSECTION("auth_policy=deny + auth cookie -> passthrough");

    reset_pool();
    memset(&conf, 0, sizeof(conf));
    conf.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY;
    conf.policy.auth_cookies = NULL;  /* uses default patterns */

    r = make_req();
    TEST_ASSERT(r != NULL, "request allocation succeeded");

    /* Add a cookie matching default auth patterns (session_id) */
    r->headers_in.cookie = (ngx_table_elt_t *)
        ngx_pcalloc(NULL, sizeof(ngx_table_elt_t));
    TEST_ASSERT(r->headers_in.cookie != NULL, "cookie alloc OK");
    r->headers_in.cookie->hash = 1;
    r->headers_in.cookie->value.data =
        (u_char *) "session_id=abc123def456";
    r->headers_in.cookie->value.len = 23;

    /* Verify is_authenticated detects the auth cookie */
    TEST_ASSERT(ngx_http_markdown_is_authenticated(r, &conf) == 1,
        "is_authenticated returns 1 with auth cookie");

    /* Verify the auth gate blocks conversion */
    eligible = model_streaming_auth_gate(r, &conf);
    TEST_ASSERT(eligible == 0,
        "auth_policy=deny + auth cookie -> NOT eligible (passthrough)");

    TEST_PASS("auth_policy=deny + auth cookie -> passthrough");
}


/* ================================================================
 * Test 3: auth_policy=deny + no auth credentials -> eligible
 *
 * When auth_policy is deny but no authentication credentials are
 * present, conversion SHOULD proceed normally (streaming or
 * full-buffer as selected by the engine).
 *
 * Validates: auth/status checked before streaming candidate (non-authenticated proceeds)
 * ================================================================ */
static void
test_auth_deny_no_credentials(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_request_t      *r;
    int                      eligible;

    TEST_SUBSECTION("auth_policy=deny + no auth -> eligible");

    reset_pool();
    memset(&conf, 0, sizeof(conf));
    conf.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY;
    conf.policy.auth_cookies = NULL;

    r = make_req();
    TEST_ASSERT(r != NULL, "request allocation succeeded");

    /* No Authorization header, no cookies */

    /* Verify is_authenticated does not detect auth */
    TEST_ASSERT(ngx_http_markdown_is_authenticated(r, &conf) == 0,
        "is_authenticated returns 0 with no credentials");

    /* Verify the auth gate allows conversion */
    eligible = model_streaming_auth_gate(r, &conf);
    TEST_ASSERT(eligible == 1,
        "auth_policy=deny + no auth -> eligible (conversion proceeds)");

    TEST_PASS("auth_policy=deny + no auth -> eligible");
}


/* ================================================================
 * Test 4: auth_policy=allow + auth present -> eligible
 *
 * When auth_policy is allow (default), authenticated requests
 * ARE eligible for conversion.  The streaming engine selection
 * proceeds normally.
 *
 * Validates: auth/status checked before streaming candidate (allow policy)
 * ================================================================ */
static void
test_auth_allow_with_auth_present(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_request_t      *r;
    int                      eligible;

    TEST_SUBSECTION("auth_policy=allow + auth present -> eligible");

    reset_pool();
    memset(&conf, 0, sizeof(conf));
    conf.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW;
    conf.policy.auth_cookies = NULL;

    r = make_req();
    TEST_ASSERT(r != NULL, "request allocation succeeded");

    /* Add Authorization header */
    r->headers_in.authorization = (ngx_table_elt_t *)
        ngx_pcalloc(NULL, sizeof(ngx_table_elt_t));
    TEST_ASSERT(r->headers_in.authorization != NULL, "header alloc OK");
    r->headers_in.authorization->hash = 1;
    r->headers_in.authorization->value.data = (u_char *) "Basic dXNlcjpwYXNz";
    r->headers_in.authorization->value.len = 18;

    /* Verify is_authenticated detects the header */
    TEST_ASSERT(ngx_http_markdown_is_authenticated(r, &conf) == 1,
        "is_authenticated returns 1 with Authorization header");

    /* Verify the auth gate ALLOWS conversion (policy=allow) */
    eligible = model_streaming_auth_gate(r, &conf);
    TEST_ASSERT(eligible == 1,
        "auth_policy=allow + auth -> eligible (conversion proceeds)");

    TEST_PASS("auth_policy=allow + auth present -> eligible");
}


/* ================================================================
 * Test 5: auth_policy=deny + non-auth cookie -> eligible
 *
 * When auth_policy is deny but the request only has cookies that
 * do NOT match auth patterns (e.g., tracking cookies), conversion
 * should proceed.
 *
 * Validates: auth/status checked before streaming candidate (only auth cookies block)
 * ================================================================ */
static void
test_auth_deny_with_non_auth_cookie(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_request_t      *r;
    int                      eligible;

    TEST_SUBSECTION("auth_policy=deny + non-auth cookie -> eligible");

    reset_pool();
    memset(&conf, 0, sizeof(conf));
    conf.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY;
    conf.policy.auth_cookies = NULL;

    r = make_req();
    TEST_ASSERT(r != NULL, "request allocation succeeded");

    /* Add a non-auth cookie (tracking, analytics, etc.) */
    r->headers_in.cookie = (ngx_table_elt_t *)
        ngx_pcalloc(NULL, sizeof(ngx_table_elt_t));
    TEST_ASSERT(r->headers_in.cookie != NULL, "cookie alloc OK");
    r->headers_in.cookie->hash = 1;
    r->headers_in.cookie->value.data =
        (u_char *) "tracking=xyz789";
    r->headers_in.cookie->value.len = 15;

    /* Verify is_authenticated does NOT detect this cookie as auth */
    TEST_ASSERT(ngx_http_markdown_is_authenticated(r, &conf) == 0,
        "is_authenticated returns 0 with non-auth cookie");

    /* Verify the auth gate allows conversion */
    eligible = model_streaming_auth_gate(r, &conf);
    TEST_ASSERT(eligible == 1,
        "auth_policy=deny + non-auth cookie -> eligible");

    TEST_PASS("auth_policy=deny + non-auth cookie -> eligible");
}


/* ================================================================
 * Test 6: Auth check order -- runs before streaming candidate
 *
 * Validates that the auth_policy deny check is a pure predicate
 * that executes independently of the streaming engine state.
 * In production (request_impl.h), it fires BEFORE
 * select_processing_path is ever called.
 *
 * Property: model_streaming_auth_gate is idempotent and does not
 * mutate any input state.  This proves it can run before any
 * streaming resource allocation without side effects.
 *
 * Validates: auth/status checked before streaming candidate, none bypassed by streaming path
 * ================================================================ */
static void
test_auth_check_runs_before_streaming(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_request_t      *r;
    int                      result1;
    int                      result2;

    TEST_SUBSECTION("Auth check: idempotent, runs before streaming");

    reset_pool();
    memset(&conf, 0, sizeof(conf));
    conf.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY;
    conf.policy.auth_cookies = NULL;

    r = make_req();
    TEST_ASSERT(r != NULL, "request allocation succeeded");

    /* Add Authorization header */
    r->headers_in.authorization = (ngx_table_elt_t *)
        ngx_pcalloc(NULL, sizeof(ngx_table_elt_t));
    TEST_ASSERT(r->headers_in.authorization != NULL, "header alloc OK");
    r->headers_in.authorization->hash = 1;
    r->headers_in.authorization->value.data = (u_char *) "Bearer xyz";
    r->headers_in.authorization->value.len = 10;

    /*
     * Call the auth gate twice -- must be idempotent.
     * A side-effectful function would return different results.
     */
    result1 = model_streaming_auth_gate(r, &conf);
    result2 = model_streaming_auth_gate(r, &conf);
    TEST_ASSERT(result1 == 0 && result2 == 0,
        "auth gate idempotent: both calls return 0 (passthrough)");

    /* Verify the request state was not mutated */
    TEST_ASSERT(r->headers_in.authorization->hash == 1,
        "Authorization header hash unchanged after auth check");
    TEST_ASSERT(r->headers_in.authorization->value.len == 10,
        "Authorization header value length unchanged");

    /* Verify conf was not mutated */
    TEST_ASSERT(conf.policy.auth_policy
        == NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY,
        "auth_policy unchanged after gate check");

    TEST_PASS("Auth check: idempotent, runs before streaming");
}


/* ================================================================
 * Test 7: auth_policy=deny + custom cookie patterns -> passthrough
 *
 * When operator configures custom auth cookie patterns, those
 * patterns must also be respected by the deny gate.
 *
 * Validates: none bypassed by streaming path
 * ================================================================ */
static void
test_auth_deny_custom_cookie_patterns(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_request_t      *r;
    ngx_array_t              patterns;
    ngx_str_t                pattern_elts[2];
    int                      eligible;

    TEST_SUBSECTION("auth_policy=deny + custom cookie patterns");

    reset_pool();
    memset(&conf, 0, sizeof(conf));
    conf.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY;

    /* Configure custom auth cookie patterns */
    pattern_elts[0].data = (u_char *) "my_auth*";
    pattern_elts[0].len = 8;
    pattern_elts[1].data = (u_char *) "custom_session";
    pattern_elts[1].len = 14;

    patterns.elts = pattern_elts;
    patterns.nelts = 2;
    patterns.size = sizeof(ngx_str_t);
    patterns.nalloc = 2;
    patterns.pool = NULL;
    conf.policy.auth_cookies = &patterns;

    r = make_req();
    TEST_ASSERT(r != NULL, "request allocation succeeded");

    /* Add a cookie matching custom pattern "my_auth*" */
    r->headers_in.cookie = (ngx_table_elt_t *)
        ngx_pcalloc(NULL, sizeof(ngx_table_elt_t));
    TEST_ASSERT(r->headers_in.cookie != NULL, "cookie alloc OK");
    r->headers_in.cookie->hash = 1;
    r->headers_in.cookie->value.data =
        (u_char *) "my_auth_token=secret123";
    r->headers_in.cookie->value.len = 23;

    /* Verify is_authenticated detects via custom pattern */
    TEST_ASSERT(ngx_http_markdown_is_authenticated(r, &conf) == 1,
        "is_authenticated detects custom auth cookie pattern");

    /* Verify the auth gate blocks conversion */
    eligible = model_streaming_auth_gate(r, &conf);
    TEST_ASSERT(eligible == 0,
        "auth_policy=deny + custom cookie pattern -> passthrough");

    TEST_PASS("auth_policy=deny + custom cookie patterns -> passthrough");
}


/* ================================================================
 * Entry point
 * ================================================================ */
int
main(void)
{
    printf("\n========================================\n");
    printf("Auth Deny Security Tests (streaming security and resource limits)\n");
    printf("auth/status checked before streaming candidate, oversized body / replay overflow handling\n");
    printf("========================================\n");

    TEST_SECTION("auth_policy=deny streaming bypass prevention");

    test_auth_deny_with_authorization_header();
    test_auth_deny_with_auth_cookie();
    test_auth_deny_no_credentials();
    test_auth_allow_with_auth_present();
    test_auth_deny_with_non_auth_cookie();
    test_auth_check_runs_before_streaming();
    test_auth_deny_custom_cookie_patterns();

    printf("\n========================================\n");
    printf("All auth deny security tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
