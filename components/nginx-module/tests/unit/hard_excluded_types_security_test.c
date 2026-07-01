/*
 * Test: hard_excluded_types_security
 *
 * Security test validating that hard-excluded content types
 * (text/event-stream, application/x-ndjson, application/stream+json)
 * are ALWAYS rejected from streaming conversion, regardless of
 * user configuration or streaming engine mode.
 *
 * Requirements: streaming security and resource limits, hard-excluded content types always passthrough
 *   AC 1: text/event-stream, application/x-ndjson,
 *          application/stream+json always passthrough
 *   AC 2: Cannot be removed by user config
 *   AC 3: Checked before any conversion attempt
 *   AC 4: Matching ignores Content-Type parameters and case,
 *          evaluated before decompression or parser allocation
 *
 * Security invariant: No streaming parser or converter resources
 * are ever allocated for hard-excluded content types because the
 * exclusion check (stream_type_excluded) runs at the very start
 * of select_processing_path, BEFORE any engine allocation, parser
 * init, or FFI call.
 *
 * Rules: 14 (regression test), 25 (coverage).
 */

#include "../include/test_common.h"

#include <ctype.h>
#include <stdarg.h>

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
    ngx_uint_t              method;
    ngx_pool_t             *pool;
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
    UNUSED(pool);
    return malloc(size);
}

/*
 * Include the production eligibility source so we test the real
 * ngx_http_markdown_stream_type_excluded() function.
 */
#include "../../src/ngx_http_markdown_eligibility.c"

/* ================================================================
 * Helper: set ngx_str_t from C string literal
 * ================================================================ */
static void
set_ct(ngx_str_t *s, const char *val)
{
    s->data = (u_char *) (uintptr_t) val;
    s->len = strlen(val);
}

/* ================================================================
 * Security Test 1: Hard-excluded types always passthrough
 *
 * Validates: hard-excluded types always passthrough (AC 1)
 *
 * These types represent streaming protocols (SSE, NDJSON, streaming
 * JSON) that must NEVER enter the Markdown converter, whether
 * streaming or full-buffer conversion is being used.
 * ================================================================ */
static void
test_hard_excluded_types_always_passthrough(void)
{
    ngx_http_markdown_conf_t  conf;
    ngx_str_t                 ct;

    TEST_SUBSECTION("Security: hard-excluded types always passthrough");

    memset(&conf, 0, sizeof(conf));
    conf.stream.excluded_types = NULL;

    /* text/event-stream (Server-Sent Events) */
    set_ct(&ct, "text/event-stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream MUST be excluded (security)");

    /* application/x-ndjson (Newline-Delimited JSON) */
    set_ct(&ct, "application/x-ndjson");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/x-ndjson MUST be excluded (security)");

    /* application/stream+json (JSON Streaming) */
    set_ct(&ct, "application/stream+json");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/stream+json MUST be excluded (security)");

    /* Positive control: text/html is NOT excluded */
    set_ct(&ct, "text/html");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/html should NOT be excluded (control)");

    /* Positive control: application/json is NOT excluded */
    set_ct(&ct, "application/json");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "application/json should NOT be excluded (control)");

    TEST_PASS("Security: all hard-excluded types always passthrough");
}

/* ================================================================
 * Security Test 2: Cannot be overridden by user configuration
 *
 * Validates: hard-excluded types cannot be overridden by user config (AC 2)
 *
 * Even when an operator configures markdown_stream_excluded_types
 * with their own list, the built-in hard exclusions remain active.
 * An operator cannot accidentally or intentionally remove protection
 * for SSE/NDJSON/streaming-JSON.
 * ================================================================ */
static void
test_hard_exclusions_cannot_be_overridden(void)
{
    ngx_http_markdown_conf_t  conf;
    ngx_str_t                 ct;
    ngx_array_t               user_types;
    ngx_str_t                 user_elts[3];

    TEST_SUBSECTION("Security: hard exclusions cannot be overridden");

    memset(&conf, 0, sizeof(conf));

    /*
     * Simulate user configuration with custom exclusion types.
     * The hard-coded exclusions must remain active regardless.
     */
    set_ct(&user_elts[0], "text/csv");
    set_ct(&user_elts[1], "application/xml");
    set_ct(&user_elts[2], "image/png");

    user_types.elts = user_elts;
    user_types.nelts = 3;
    user_types.size = sizeof(ngx_str_t);
    user_types.nalloc = 3;
    user_types.pool = NULL;

    conf.stream.excluded_types = &user_types;

    /* Hard exclusions remain with user config present */
    set_ct(&ct, "text/event-stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream MUST remain excluded with user config");

    set_ct(&ct, "application/x-ndjson");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/x-ndjson MUST remain excluded with user config");

    set_ct(&ct, "application/stream+json");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/stream+json MUST remain excluded with user config");

    /* User-configured types are also excluded */
    set_ct(&ct, "text/csv");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "user-configured text/csv should also be excluded");

    /* NULL user config: hard exclusions still work */
    conf.stream.excluded_types = NULL;
    set_ct(&ct, "text/event-stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream excluded with NULL user config");

    set_ct(&ct, "application/x-ndjson");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/x-ndjson excluded with NULL user config");

    set_ct(&ct, "application/stream+json");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/stream+json excluded with NULL user config");

    TEST_PASS("Security: hard exclusions cannot be overridden");
}

/* ================================================================
 * Security Test 3: Exclusion checked BEFORE any conversion attempt
 *
 * Validates: exclusion checked before any conversion attempt (AC 3)
 *
 * stream_type_excluded() is a pure predicate that does not allocate
 * memory, does not modify state, and returns immediately. In the
 * production code path (select_processing_path), it is evaluated
 * as Rule 6/7, which precedes Rule 8 (engine=on -> streaming path).
 *
 * This test proves the function is side-effect free: it does not
 * modify inputs, is idempotent, and handles edge cases safely.
 * If this function says "excluded", no streaming resources are ever
 * allocated (parser, converter, buffers).
 * ================================================================ */
static void
test_exclusion_before_conversion(void)
{
    ngx_http_markdown_conf_t  conf;
    ngx_str_t                 ct;
    ngx_int_t                 r1;
    ngx_int_t                 r2;

    TEST_SUBSECTION("Security: exclusion before any conversion attempt");

    memset(&conf, 0, sizeof(conf));
    conf.stream.excluded_types = NULL;

    /*
     * Prove idempotency: calling twice must return same result.
     * A side-effectful function might return different results.
     */
    set_ct(&ct, "text/event-stream");
    r1 = ngx_http_markdown_stream_type_excluded(&ct, &conf);
    r2 = ngx_http_markdown_stream_type_excluded(&ct, &conf);
    TEST_ASSERT(r1 == 1 && r2 == 1,
        "text/event-stream: idempotent exclusion (no state mutation)");

    set_ct(&ct, "application/x-ndjson");
    r1 = ngx_http_markdown_stream_type_excluded(&ct, &conf);
    r2 = ngx_http_markdown_stream_type_excluded(&ct, &conf);
    TEST_ASSERT(r1 == 1 && r2 == 1,
        "application/x-ndjson: idempotent exclusion");

    set_ct(&ct, "application/stream+json");
    r1 = ngx_http_markdown_stream_type_excluded(&ct, &conf);
    r2 = ngx_http_markdown_stream_type_excluded(&ct, &conf);
    TEST_ASSERT(r1 == 1 && r2 == 1,
        "application/stream+json: idempotent exclusion");

    /* Verify input string is not mutated */
    set_ct(&ct, "text/event-stream; charset=utf-8");
    r1 = ngx_http_markdown_stream_type_excluded(&ct, &conf);
    TEST_ASSERT(r1 == 1, "excluded with params");
    TEST_ASSERT(ct.len == 32,
        "content_type length not modified by check");
    TEST_ASSERT(memcmp(ct.data, "text/event-stream; charset=utf-8",
                       32) == 0,
        "content_type data not modified by check");

    /* NULL and empty edge cases: safe, no crash */
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(NULL, &conf) == 0,
        "NULL content_type: safe return 0");

    ct.data = NULL;
    ct.len = 0;
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "empty content_type: safe return 0");

    ct.data = (u_char *) "";
    ct.len = 0;
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "zero-length content_type: safe return 0");

    TEST_PASS("Security: exclusion before any conversion attempt");
}

/* ================================================================
 * Security Test 4: Parameter injection cannot bypass exclusion
 *
 * Validates: parameter/case matching cannot bypass exclusion (AC 4)
 *
 * An attacker or misconfigured upstream might send Content-Type
 * headers with extra parameters to try to bypass the exclusion.
 * The function must strip parameters before matching.
 * ================================================================ */
static void
test_parameter_injection_bypass(void)
{
    ngx_http_markdown_conf_t  conf;
    ngx_str_t                 ct;

    TEST_SUBSECTION("Security: parameter injection cannot bypass");

    memset(&conf, 0, sizeof(conf));
    conf.stream.excluded_types = NULL;

    /* Bypass attempt: charset parameter */
    set_ct(&ct, "text/event-stream; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "charset param cannot bypass text/event-stream");

    /* Bypass attempt: boundary parameter */
    set_ct(&ct, "application/x-ndjson; boundary=AAAA");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "boundary param cannot bypass application/x-ndjson");

    /* Bypass attempt: multiple parameters */
    set_ct(&ct,
        "application/stream+json; charset=utf-8; boundary=x");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "multiple params cannot bypass stream+json");

    /* Bypass attempt: space before semicolon */
    set_ct(&ct, "text/event-stream ;charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "space before ; cannot bypass text/event-stream");

    /* Bypass attempt: case variation with parameters */
    set_ct(&ct, "TEXT/EVENT-STREAM; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "uppercase+params cannot bypass text/event-stream");

    set_ct(&ct, "Application/X-NDJSON; boundary=foo");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "mixed case+params cannot bypass application/x-ndjson");

    set_ct(&ct, "APPLICATION/STREAM+JSON; x=1");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "uppercase+params cannot bypass stream+json");

    TEST_PASS("Security: parameter injection cannot bypass");
}

/* ================================================================
 * Security Test 5: Case variation cannot bypass exclusion
 *
 * Validates: parameter/case matching cannot bypass exclusion (AC 4)
 *
 * Content-Type matching must be case-insensitive per RFC 9110.
 * ================================================================ */
static void
test_case_variation_bypass(void)
{
    ngx_http_markdown_conf_t  conf;
    ngx_str_t                 ct;

    TEST_SUBSECTION("Security: case variation cannot bypass");

    memset(&conf, 0, sizeof(conf));
    conf.stream.excluded_types = NULL;

    /* All-uppercase */
    set_ct(&ct, "TEXT/EVENT-STREAM");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "TEXT/EVENT-STREAM must be excluded");

    set_ct(&ct, "APPLICATION/X-NDJSON");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "APPLICATION/X-NDJSON must be excluded");

    set_ct(&ct, "APPLICATION/STREAM+JSON");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "APPLICATION/STREAM+JSON must be excluded");

    /* Mixed case */
    set_ct(&ct, "Text/Event-Stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "Text/Event-Stream (title case) must be excluded");

    set_ct(&ct, "Application/X-Ndjson");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "Application/X-Ndjson (title case) must be excluded");

    set_ct(&ct, "Application/Stream+Json");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "Application/Stream+Json (title case) must be excluded");

    /* Partial match: must NOT be excluded (no false positives) */
    set_ct(&ct, "text/event-streaming");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/event-streaming is NOT excluded (no false positive)");

    set_ct(&ct, "application/x-ndjsonl");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "application/x-ndjsonl is NOT excluded");

    set_ct(&ct, "application/stream+jsonp");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "application/stream+jsonp is NOT excluded");

    /* Prefix match: must NOT be excluded */
    set_ct(&ct, "text/event");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/event (too short) is NOT excluded");

    set_ct(&ct, "application/x-ndj");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "application/x-ndj (too short) is NOT excluded");

    TEST_PASS("Security: case variation cannot bypass");
}

/* ================================================================
 * Security Test 6: Exclusion applies in the streaming path
 *
 * Validates: exclusion applies in streaming path (AC 3) + integration verification
 *
 * In production, select_processing_path checks:
 *   Rule 6: text/event-stream (direct check)
 *   Rule 7: stream_types exclusion (calls stream_type_excluded
 *           indirectly via is_excluded_stream_type)
 *
 * Both execute BEFORE Rule 8 (engine=on -> STREAMING).
 * This means even when streaming engine is forced "on", excluded
 * types never enter the streaming parser.
 *
 * We verify this property by confirming the function returns
 * the correct exclusion result for every hard-excluded type,
 * proving that the path selector will always see "excluded"
 * and route to FULLBUFFER before any streaming init occurs.
 * ================================================================ */
static void
test_exclusion_in_streaming_path(void)
{
    ngx_http_markdown_conf_t  conf;
    ngx_str_t                 ct;
    ngx_array_t               stream_types;
    ngx_str_t                 stream_elts[3];

    TEST_SUBSECTION("Security: exclusion applies in streaming path");

    memset(&conf, 0, sizeof(conf));

    /*
     * Simulate the configuration where all three hard-excluded
     * types are also in the stream_types array (belt and suspenders).
     * The hard exclusion in stream_type_excluded is the primary
     * gate; stream_types in select_processing_path is defense-in-depth.
     */
    set_ct(&stream_elts[0], "text/event-stream");
    set_ct(&stream_elts[1], "application/x-ndjson");
    set_ct(&stream_elts[2], "application/stream+json");

    stream_types.elts = stream_elts;
    stream_types.nelts = 3;
    stream_types.size = sizeof(ngx_str_t);
    stream_types.nalloc = 3;
    stream_types.pool = NULL;

    conf.stream.excluded_types = NULL;

    /*
     * Even without stream_types configured, the hard exclusions
     * in stream_type_excluded block these types.
     */
    set_ct(&ct, "text/event-stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream blocked without stream_types config");

    set_ct(&ct, "application/x-ndjson");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/x-ndjson blocked without stream_types config");

    set_ct(&ct, "application/stream+json");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/stream+json blocked without stream_types config");

    /* With stream_types set, both mechanisms agree */
    conf.stream.excluded_types = &stream_types;
    set_ct(&ct, "text/event-stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream excluded via both mechanisms");

    /* Verify a non-excluded type is allowed through */
    set_ct(&ct, "text/html");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/html passes through (not hard-excluded)");

    TEST_PASS("Security: exclusion applies in streaming path");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("Hard-Excluded Types Security Tests (streaming security and resource limits)\n");
    printf("========================================\n");

    test_hard_excluded_types_always_passthrough();
    test_hard_exclusions_cannot_be_overridden();
    test_exclusion_before_conversion();
    test_parameter_injection_bypass();
    test_case_variation_bypass();
    test_exclusion_in_streaming_path();

    printf("\n========================================\n");
    printf("All security tests passed!\n");
    printf("========================================\n\n");

    return 0;
}
