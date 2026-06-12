/*
 * Test: hard_excluded_params_case
 *
 * Security test validating that hard-excluded content types are correctly
 * matched even with Content-Type parameters (e.g., charset=utf-8) and
 * mixed-case variants.  This ensures that an attacker cannot bypass the
 * streaming exclusion list by appending parameters or altering case.
 *
 * Validates: Spec 41, Requirement 4 AC 4
 *   "Matching ignores Content-Type parameters and case, and is evaluated
 *    before decompression or parser allocation."
 *
 * AGENTS.md: Rule 14 (regression tests), Rule 30 (length-bounded matching)
 */

#include "../include/test_common.h"
#include <ctype.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_CONF_UNSET_PTR
#define NGX_CONF_UNSET_PTR ((void *) -1)
#endif
#ifndef NGX_HTTP_GET
#define NGX_HTTP_GET  0
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 1
#endif
#ifndef NGX_HTTP_OK
#define NGX_HTTP_OK  200
#endif
#ifndef NGX_HTTP_PARTIAL_CONTENT
#define NGX_HTTP_PARTIAL_CONTENT 206
#endif

typedef struct ngx_pool_s ngx_pool_t;
typedef struct ngx_table_elt_s ngx_table_elt_t;
typedef struct ngx_http_headers_in_s ngx_http_headers_in_t;
typedef struct ngx_http_headers_out_s ngx_http_headers_out_t;

struct ngx_pool_s { int dummy; };

struct ngx_table_elt_s {
    ngx_str_t   key;
    ngx_str_t   value;
    ngx_uint_t  hash;
};

struct ngx_http_headers_in_s {
    ngx_table_elt_t *range;
};

struct ngx_http_headers_out_s {
    ngx_str_t   content_type;
    ngx_uint_t  status;
    off_t       content_length_n;
};

struct ngx_array_s {
    void       *elts;
    ngx_uint_t  nelts;
    size_t      size;
    ngx_uint_t  nalloc;
    ngx_pool_t *pool;
};

struct ngx_http_request_s {
    ngx_uint_t                method;
    ngx_http_headers_out_t    headers_out;
    ngx_http_headers_in_t     headers_in;
};

/* ── NGINX API stubs ────────────────────────────────────────────── */

static ngx_int_t
ngx_strncasecmp(const u_char *s1, const u_char *s2, size_t n)
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

/* ── Include production source ──────────────────────────────────── */

#include "../../src/ngx_http_markdown_eligibility.c"

/* ── Test helpers ───────────────────────────────────────────────── */

static void
set_ct(ngx_str_t *ct, const char *val)
{
    ct->data = (u_char *) (uintptr_t) val;
    ct->len = strlen(val);
}

static void
init_conf(ngx_http_markdown_conf_t *conf)
{
    memset(conf, 0, sizeof(*conf));
    conf->content_types = NULL;
    conf->stream_types = NULL;
    conf->stream.excluded_types = NULL;
    conf->max_size = (size_t) -1;
}

/* ══════════════════════════════════════════════════════════════════
 * Security: mixed-case hard exclusions
 *
 * Verifies that case variation cannot bypass the exclusion list.
 * ══════════════════════════════════════════════════════════════════ */
static void
test_mixed_case_exclusions(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_str_t                ct;

    TEST_SUBSECTION("Mixed-case hard exclusions");
    init_conf(&conf);

    /* text/event-stream: mixed case */
    set_ct(&ct, "Text/Event-Stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "Text/Event-Stream (mixed case) must be excluded");

    /* text/event-stream: all uppercase */
    set_ct(&ct, "TEXT/EVENT-STREAM");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "TEXT/EVENT-STREAM (all caps) must be excluded");

    /* application/x-ndjson: mixed case */
    set_ct(&ct, "Application/X-Ndjson");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "Application/X-Ndjson (mixed case) must be excluded");

    /* application/x-ndjson: all uppercase */
    set_ct(&ct, "APPLICATION/X-NDJSON");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "APPLICATION/X-NDJSON (all caps) must be excluded");

    /* application/stream+json: mixed case */
    set_ct(&ct, "Application/Stream+JSON");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "Application/Stream+JSON (mixed case) must be excluded");

    /* application/stream+json: all uppercase */
    set_ct(&ct, "APPLICATION/STREAM+JSON");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "APPLICATION/STREAM+JSON (all caps) must be excluded");

    TEST_PASS("Mixed-case exclusions correctly blocked");
}

/* ══════════════════════════════════════════════════════════════════
 * Security: Content-Type parameters stripped before comparison
 *
 * Verifies that appending parameters (charset, boundary, etc.)
 * cannot bypass the exclusion list.
 * ══════════════════════════════════════════════════════════════════ */
static void
test_parameter_stripping_exclusions(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_str_t                ct;

    TEST_SUBSECTION("Content-Type parameter stripping");
    init_conf(&conf);

    /* text/event-stream with charset parameter */
    set_ct(&ct, "text/event-stream; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream; charset=utf-8 must be excluded");

    /* application/x-ndjson with charset parameter */
    set_ct(&ct, "application/x-ndjson; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/x-ndjson; charset=utf-8 must be excluded");

    /* application/stream+json with boundary parameter */
    set_ct(&ct, "application/stream+json; boundary=something");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/stream+json; boundary=something must be excluded");

    /* Trailing space before semicolon */
    set_ct(&ct, "text/event-stream ;charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream with space before params must be excluded");

    /* Multiple parameters */
    set_ct(&ct, "application/x-ndjson; charset=utf-8; boundary=x");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/x-ndjson; multiple params must be excluded");

    TEST_PASS("Parameter stripping correctly blocks bypass attempts");
}

/* ══════════════════════════════════════════════════════════════════
 * Security: combined case + parameters
 *
 * Verifies that combining case variation AND parameters cannot
 * bypass the exclusion list.
 * ══════════════════════════════════════════════════════════════════ */
static void
test_combined_case_and_params(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_str_t                ct;

    TEST_SUBSECTION("Combined case variation + parameters");
    init_conf(&conf);

    /* Mixed case + charset */
    set_ct(&ct, "Text/Event-Stream; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "Text/Event-Stream; charset=utf-8 must be excluded");

    /* All caps + boundary */
    set_ct(&ct, "APPLICATION/X-NDJSON; boundary=foo");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "APPLICATION/X-NDJSON; boundary=foo must be excluded");

    /* Mixed case stream+json + charset */
    set_ct(&ct, "Application/Stream+Json; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "Application/Stream+Json; charset=utf-8 must be excluded");

    TEST_PASS("Combined case+params correctly blocked");
}

/* ══════════════════════════════════════════════════════════════════
 * Security: partial matches must NOT be excluded (boundary check)
 *
 * Verifies that length-bounded matching prevents false positives
 * on content types that share a prefix with excluded types.
 * Rule 30: length-bounded matching.
 * ══════════════════════════════════════════════════════════════════ */
static void
test_partial_match_not_excluded(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_str_t                ct;

    TEST_SUBSECTION("Partial match boundary checks (must NOT exclude)");
    init_conf(&conf);

    /* text/event-stream-extended shares prefix but is different type */
    set_ct(&ct, "text/event-stream-extended");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/event-stream-extended must NOT be excluded");

    /* application/x-ndjson-custom shares prefix but is different type */
    set_ct(&ct, "application/x-ndjson-custom");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "application/x-ndjson-custom must NOT be excluded");

    /* application/stream+jsonl shares prefix but is different type */
    set_ct(&ct, "application/stream+jsonl");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "application/stream+jsonl must NOT be excluded");

    /* text/event-streaming shares prefix but is different type */
    set_ct(&ct, "text/event-streaming");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/event-streaming must NOT be excluded");

    /* Partial match in uppercase should also NOT match */
    set_ct(&ct, "TEXT/EVENT-STREAM-EXTENDED");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "TEXT/EVENT-STREAM-EXTENDED (caps) must NOT be excluded");

    set_ct(&ct, "APPLICATION/X-NDJSON-CUSTOM");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "APPLICATION/X-NDJSON-CUSTOM (caps) must NOT be excluded");

    TEST_PASS("Partial matches correctly NOT excluded");
}

/* ══════════════════════════════════════════════════════════════════
 * Security: non-excluded types remain eligible
 *
 * Ensures the function does not over-match legitimate content types.
 * ══════════════════════════════════════════════════════════════════ */
static void
test_non_excluded_types(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_str_t                ct;

    TEST_SUBSECTION("Non-excluded types remain eligible");
    init_conf(&conf);

    set_ct(&ct, "text/html");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/html must NOT be excluded");

    set_ct(&ct, "text/html; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/html; charset=utf-8 must NOT be excluded");

    set_ct(&ct, "application/json");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "application/json must NOT be excluded");

    set_ct(&ct, "text/plain");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/plain must NOT be excluded");

    TEST_PASS("Non-excluded types correctly pass through");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("hard_excluded_params_case Security Tests\n");
    printf("  (Spec 41, Req 4 AC 4)\n");
    printf("========================================\n");

    test_mixed_case_exclusions();
    test_parameter_stripping_exclusions();
    test_combined_case_and_params();
    test_partial_match_not_excluded();
    test_non_excluded_types();

    printf("\n========================================\n");
    printf("All security tests passed!\n");
    printf("========================================\n\n");

    return 0;
}
