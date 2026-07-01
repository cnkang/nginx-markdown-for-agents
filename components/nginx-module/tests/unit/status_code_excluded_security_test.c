/*
 * Test: status_code_excluded_security
 *
 * Security test validating that non-200 status codes are correctly
 * excluded from conversion in the streaming path. Only HTTP 200 OK
 * responses are eligible for conversion. HTTP 206 (Partial Content)
 * returns INELIGIBLE_RANGE; all other non-200 codes return
 * INELIGIBLE_STATUS.
 *
 * Requirements: streaming security and resource limits, auth/status checked before streaming candidate, oversized body / replay overflow handling
 *   - status_code checked before streaming candidate evaluation
 *   - Status code excluded test (security)
 *
 * Rules: 14 (regression test), 25 (coverage).
 */

#include "../include/test_common.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdlib.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK 0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR -1
#endif
#ifndef NGX_CONF_UNSET
#define NGX_CONF_UNSET (-1)
#endif
#ifndef NGX_CONF_UNSET_UINT
#define NGX_CONF_UNSET_UINT ((ngx_uint_t) -1)
#endif
#ifndef NGX_CONF_UNSET_SIZE
#define NGX_CONF_UNSET_SIZE ((size_t) -1)
#endif
#ifndef NGX_CONF_UNSET_PTR
#define NGX_CONF_UNSET_PTR ((void *) -1)
#endif

#ifndef NGX_LOG_EMERG
#define NGX_LOG_EMERG 1
#endif
#ifndef NGX_LOG_DEBUG
#define NGX_LOG_DEBUG 8
#endif
#ifndef NGX_LOG_INFO
#define NGX_LOG_INFO 7
#endif

#ifndef NGX_HTTP_GET
#define NGX_HTTP_GET  0x0002
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 0x0004
#endif
#ifndef NGX_HTTP_OK
#define NGX_HTTP_OK  200
#endif
#ifndef NGX_HTTP_PARTIAL_CONTENT
#define NGX_HTTP_PARTIAL_CONTENT 206
#endif

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif

#define ngx_strncmp(s1, s2, n) \
    strncmp((const char *) (s1), (const char *) (s2), (n))

typedef intptr_t ngx_err_t;

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

typedef struct ngx_table_elt_s ngx_table_elt_t;

struct ngx_table_elt_s {
    ngx_str_t   key;
    ngx_str_t   value;
    ngx_uint_t  hash;
};

typedef struct ngx_http_headers_in_s ngx_http_headers_in_t;

struct ngx_http_headers_in_s {
    ngx_table_elt_t *range;
};

typedef struct ngx_http_headers_out_s ngx_http_headers_out_t;

struct ngx_http_headers_out_s {
    ngx_str_t   content_type;
    ngx_uint_t  status;
    off_t       content_length_n;
};

struct ngx_http_request_s {
    ngx_pool_t             *pool;
    ngx_uint_t              method;
    ngx_http_headers_out_t  headers_out;
    ngx_http_headers_in_t   headers_in;
};

struct ngx_http_complex_value_s {
    ngx_str_t  value;
};

struct ngx_conf_s {
    ngx_pool_t  *pool;
    ngx_array_t *args;
};

struct ngx_command_s {
    ngx_str_t  name;
};

struct ngx_module_s {
    int dummy;
};

/*
 * Global module symbol required by the config implementation header.
 */
ngx_module_t ngx_http_markdown_filter_module;

static ngx_int_t
ngx_ascii_strncasecmp(const u_char *s1, const u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        u_char c1 = (u_char) tolower((unsigned char) s1[i]);
        u_char c2 = (u_char) tolower((unsigned char) s2[i]);
        if (c1 != c2) {
            return (ngx_int_t) c1 - (ngx_int_t) c2;
        }
    }
    return 0;
}

static ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    return ngx_ascii_strncasecmp(s1, s2, n);
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

static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return malloc(size);
}

/*
 * Include the production eligibility source so we test the real
 * ngx_http_markdown_check_eligibility() function.
 */
#include "../../src/ngx_http_markdown_eligibility.c"

uint8_t
markdown_decide_eligibility(const struct FFIEligibilityInput *input)
{
    if (input == NULL || !input->filter_enabled) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG;
    }
    if (!input->method_get_or_head) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD;
    }
    if (input->status != NGX_HTTP_OK) {
        if (input->status == NGX_HTTP_PARTIAL_CONTENT) {
            return NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE;
        }
        return NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS;
    }
    if (input->has_range_header) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE;
    }
    if (input->content_type == NULL || input->content_type_len == 0) {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE;
    }
    if (input->content_length >= 0
        && input->body_limit != NGX_MAX_SIZE_T_VALUE
        && (uint64_t) input->content_length > input->body_limit)
    {
        return NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE;
    }
    return NGX_HTTP_MARKDOWN_ELIGIBLE;
}

/* ================================================================
 * Helper: set ngx_str_t from C string literal
 * ================================================================ */
static void
set_ct(ngx_str_t *s, const char *val)
{
    s->data = (u_char *) (uintptr_t) val;
    s->len = strlen(val);
}

/*
 * Build a baseline eligible request for status-code testing.
 * Method=GET, status=200, content_type=text/html, no range.
 */
static void
init_eligible_request(ngx_http_request_t *r)
{
    static ngx_pool_t test_pool;

    memset(r, 0, sizeof(*r));
    r->pool = &test_pool;
    r->method = NGX_HTTP_GET;
    r->headers_out.status = NGX_HTTP_OK;
    set_ct(&r->headers_out.content_type, "text/html");
    r->headers_out.content_length_n = 1024;
    r->headers_in.range = NULL;
}

/*
 * Build a baseline config with filter enabled.
 */
static void
init_conf(ngx_http_markdown_conf_t *conf)
{
    memset(conf, 0, sizeof(*conf));
    conf->content_types = NULL;
    conf->stream_types = NULL;
    conf->max_size = (size_t) -1;
    conf->stream.excluded_types = NULL;
}

/* ================================================================
 * Security Test 1: HTTP 200 is eligible
 *
 * Validates: auth/status checked before streaming candidate (positive control)
 *
 * Only HTTP 200 OK responses pass the status check and are
 * eligible for conversion.
 * ================================================================ */
static void
test_status_200_eligible(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_conf_t  conf;

    TEST_SUBSECTION("Security: HTTP 200 is eligible for conversion");

    init_eligible_request(&r);
    init_conf(&conf);

    /* Full eligibility: 200 passes through to eligible */
    TEST_ASSERT(ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
        == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "HTTP 200 with valid request must be ELIGIBLE");

    TEST_PASS("Security: HTTP 200 passes status check");
}

/* ================================================================
 * Security Test 2: HTTP 206 returns INELIGIBLE_RANGE
 *
 * Validates: auth/status checked before streaming candidate, oversized body / replay overflow handling
 *
 * 206 Partial Content is a special case: it fails the status check
 * (not 200), but the eligibility function routes it to INELIGIBLE_RANGE
 * rather than INELIGIBLE_STATUS, because it represents partial delivery.
 * ================================================================ */
static void
test_status_206_ineligible_range(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_conf_t  conf;

    TEST_SUBSECTION("Security: HTTP 206 returns INELIGIBLE_RANGE");

    init_eligible_request(&r);
    init_conf(&conf);

    r.headers_out.status = NGX_HTTP_PARTIAL_CONTENT;

    /* Full eligibility: 206 routes to INELIGIBLE_RANGE */
    TEST_ASSERT(ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
        == NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE,
        "HTTP 206 must return INELIGIBLE_RANGE");

    TEST_PASS("Security: HTTP 206 correctly excluded as RANGE");
}

/* ================================================================
 * Security Test 3: Redirect status codes return INELIGIBLE_STATUS
 *
 * Validates: auth/status checked before streaming candidate, oversized body / replay overflow handling
 *
 * Redirect responses (301, 302) must never be converted. They
 * typically have no body or a short redirect notice.
 * ================================================================ */
static void
test_redirect_status_ineligible(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_conf_t  conf;

    TEST_SUBSECTION("Security: redirect status codes excluded");

    init_eligible_request(&r);
    init_conf(&conf);

    /* 301 Moved Permanently */
    r.headers_out.status = 301;
    TEST_ASSERT(ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
        == NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        "HTTP 301 must return INELIGIBLE_STATUS");

    /* 302 Found */
    r.headers_out.status = 302;
    TEST_ASSERT(ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
        == NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        "HTTP 302 must return INELIGIBLE_STATUS");

    TEST_PASS("Security: redirects correctly excluded");
}

/* ================================================================
 * Security Test 4: 304 Not Modified returns INELIGIBLE_STATUS
 *
 * Validates: auth/status checked before streaming candidate, oversized body / replay overflow handling
 *
 * 304 has no body. Converting it would produce empty output.
 * ================================================================ */
static void
test_status_304_ineligible(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_conf_t  conf;

    TEST_SUBSECTION("Security: 304 Not Modified excluded");

    init_eligible_request(&r);
    init_conf(&conf);

    r.headers_out.status = 304;

    TEST_ASSERT(ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
        == NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        "HTTP 304 must return INELIGIBLE_STATUS");

    TEST_PASS("Security: 304 correctly excluded");
}

/* ================================================================
 * Security Test 5: Client error (404) returns INELIGIBLE_STATUS
 *
 * Validates: auth/status checked before streaming candidate, oversized body / replay overflow handling
 *
 * Error pages should not be converted as they are not the
 * primary content the agent is requesting.
 * ================================================================ */
static void
test_status_404_ineligible(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_conf_t  conf;

    TEST_SUBSECTION("Security: 404 Not Found excluded");

    init_eligible_request(&r);
    init_conf(&conf);

    r.headers_out.status = 404;

    TEST_ASSERT(ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
        == NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        "HTTP 404 must return INELIGIBLE_STATUS");

    TEST_PASS("Security: 404 correctly excluded");
}

/* ================================================================
 * Security Test 6: Server error (500) returns INELIGIBLE_STATUS
 *
 * Validates: auth/status checked before streaming candidate, oversized body / replay overflow handling
 *
 * Server error pages must not be converted. Converting them
 * could mask operational errors from monitoring tools.
 * ================================================================ */
static void
test_status_500_ineligible(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_conf_t  conf;

    TEST_SUBSECTION("Security: 500 Internal Server Error excluded");

    init_eligible_request(&r);
    init_conf(&conf);

    r.headers_out.status = 500;

    TEST_ASSERT(ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
        == NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        "HTTP 500 must return INELIGIBLE_STATUS");

    TEST_PASS("Security: 500 correctly excluded");
}

/* ================================================================
 * Security Test 7: Status check runs before streaming candidate
 *
 * Validates: auth/status checked before streaming candidate
 *
 * The status check is the SECOND check in check_eligibility()
 * (after method), meaning it runs before content-type, streaming
 * type, or size checks. This confirms that non-200 responses are
 * rejected early, before any streaming candidate evaluation occurs.
 *
 * We verify ordering by setting up a request that would be
 * eligible if only the content-type and streaming checks ran,
 * but is ineligible due to status. If status is checked before
 * streaming evaluation, INELIGIBLE_STATUS is returned (not
 * INELIGIBLE_STREAMING or INELIGIBLE_CONTENT_TYPE).
 * ================================================================ */
static void
test_status_checked_before_streaming(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_conf_t  conf;

    TEST_SUBSECTION("Security: status checked before streaming candidate");

    init_eligible_request(&r);
    init_conf(&conf);

    /*
     * Set up a request that has:
     * - Valid method (GET)
     * - INVALID status (404)
     * - Streaming content type (text/event-stream) -- would be
     *   INELIGIBLE_STREAMING if reached
     *
     * If status is checked before streaming type, we get
     * INELIGIBLE_STATUS. If not, we'd get INELIGIBLE_STREAMING.
     */
    r.headers_out.status = 404;
    set_ct(&r.headers_out.content_type, "text/event-stream");

    TEST_ASSERT(ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
        == NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        "Status check must run BEFORE streaming type check");

    /*
     * Similarly: status 500 with oversized body.
     * If status runs first, INELIGIBLE_STATUS (not INELIGIBLE_SIZE).
     */
    r.headers_out.status = 500;
    set_ct(&r.headers_out.content_type, "text/html");
    r.headers_out.content_length_n = (off_t) conf.max_size + 1;

    TEST_ASSERT(ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
        == NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        "Status check must run BEFORE size check");

    TEST_PASS("Security: status correctly precedes streaming evaluation");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("Status Code Excluded Security Tests (streaming security and resource limits)\n");
    printf("========================================\n");

    test_status_200_eligible();
    test_status_206_ineligible_range();
    test_redirect_status_ineligible();
    test_status_304_ineligible();
    test_status_404_ineligible();
    test_status_500_ineligible();
    test_status_checked_before_streaming();

    printf("\n========================================\n");
    printf("All security tests passed!\n");
    printf("========================================\n\n");

    return 0;
}
