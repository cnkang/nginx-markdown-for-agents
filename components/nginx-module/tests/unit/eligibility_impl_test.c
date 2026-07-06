/*
 * Test: eligibility_impl
 *
 * Includes the actual production source so coverage instruments the real
 * decision paths that gate all conversion.
 *
 * As of spec 46 (Rust-first decision core), the eligibility *decision* lives
 * in the Rust core (markdown_decide_eligibility); the C function
 * ngx_http_markdown_check_eligibility is a thin wrapper that marshals
 * request/config fields into an FFIEligibilityInput, calls the FFI, and maps
 * the returned u8 back to ngx_http_markdown_eligibility_t.
 *
 * Because the C unit-test build does not link the Rust library, this test
 * stubs markdown_decide_eligibility with a self-contained reference port of
 * the legacy C decision (the "parity oracle").  Driving the production
 * wrapper across the decision matrix and asserting the result matches the
 * legacy oracle proves two things:
 *   1. the wrapper marshals every request/config field faithfully (otherwise
 *      the oracle, which reads only the marshalled struct, would diverge);
 *   2. the wrapper maps the returned u8 back to the correct enum.
 * The Rust side's agreement with the same matrix is covered independently by
 * the Rust unit tests in decision/eligibility.rs and the FFI tests in
 * ffi/exports.rs.
 *
 * Validates: FR-02.1 (method), FR-02.2 (status), FR-02.3 (content-type),
 *            FR-02.8 (streaming), FR-07.2 (range), FR-10.1 (size),
 *            spec 46 Req 3 (parity) and Req 4 (thin wrapper).
 */

#include "../include/test_common.h"
#include <ctype.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

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
#ifndef NGX_CONF_UNSET_PTR
#define NGX_CONF_UNSET_PTR ((void *) -1)
#endif

typedef struct ngx_pool_s ngx_pool_t;
typedef struct ngx_table_elt_s ngx_table_elt_t;
typedef struct ngx_http_headers_in_s ngx_http_headers_in_t;
typedef struct ngx_http_headers_out_s ngx_http_headers_out_t;

struct ngx_log_s { int dummy; };
struct ngx_pool_s { ngx_log_t *log; };

struct ngx_table_elt_s {
    ngx_str_t key;
    ngx_str_t value;
    ngx_uint_t hash;
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
    ngx_pool_t               *pool;
    ngx_http_headers_out_t    headers_out;
    ngx_http_headers_in_t     headers_in;
};

static ngx_int_t
ngx_strncasecmp(const u_char *s1, const u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        u_char c1 = (u_char) tolower((unsigned char) s1[i]);
        u_char c2 = (u_char) tolower((unsigned char) s2[i]);
        if (c1 != c2) return (ngx_int_t) c1 - (ngx_int_t) c2;
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

/*
 * Bump allocator backing ngx_palloc for the marshalling step inside the
 * eligibility wrapper.  The wrapper copies the configured content-type /
 * stream-type arrays into pool-allocated FFIStr arrays; the test only needs a
 * scratch region whose lifetime spans the call.  Reset g_palloc_offset before
 * tests that exercise the marshalling path.
 */
static u_char g_palloc_buf[64 * 1024];
static size_t g_palloc_offset;

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    void *p;

    UNUSED(pool);

    if (g_palloc_offset + size > sizeof(g_palloc_buf)) {
        return NULL;
    }
    p = g_palloc_buf + g_palloc_offset;
    g_palloc_offset += size;
    return p;
}

#include "../../src/ngx_http_markdown_eligibility.c"

typedef struct ngx_pool_cleanup_s {
    void                         (*handler)(void *data);
    void                          *data;
    struct ngx_pool_cleanup_s     *next;
} ngx_pool_cleanup_t;

static ngx_pool_cleanup_t  test_cleanup;
static ngx_uint_t          test_alloc_calls;

ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    UNUSED(size);
    memset(&test_cleanup, 0, sizeof(test_cleanup));
    return &test_cleanup;
}

void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    UNUSED(log);
    test_alloc_calls++;
    return malloc(size);
}

#define ngx_free free
#define ngx_memcpy memcpy

#include "../../src/ngx_http_markdown_buffer.c"


static ngx_pool_t g_pool;

/*
 * Parity oracle + FFI capture for markdown_decide_eligibility.
 *
 * reference_decide() is a self-contained port of the legacy C decision order
 * (filter -> method -> status/206 -> range -> streaming -> content-type ->
 * size), operating only on the marshalled FFIEligibilityInput.  The returned
 * u8 codes match the ngx_http_markdown_eligibility_t discriminants.
 */
static struct FFIEligibilityInput  g_last_input;
static int                         g_have_last_input;
static int                         g_force_mode;
static uint8_t                     g_forced_code;
static int                         g_call_count;

/* Case-insensitive ASCII compare over the first n bytes. */
static int
ref_strncasecmp(const uint8_t *a, const uint8_t *b, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        unsigned char ca = (unsigned char) a[i];
        unsigned char cb = (unsigned char) b[i];
        unsigned char la = (ca >= 'A' && ca <= 'Z') ? (ca | 0x20) : ca;
        unsigned char lb = (cb >= 'A' && cb <= 'Z') ? (cb | 0x20) : cb;
        if (la != lb) {
            return (int) la - (int) lb;
        }
    }
    return 0;
}

/* Case-insensitive prefix match with type-token boundary (NUL/';'/SP/HTAB). */
static int
ref_match_prefix(const uint8_t *ct, size_t ct_len,
                 const uint8_t *needle, size_t needle_len)
{
    if (ct_len < needle_len) {
        return 0;
    }
    if (ref_strncasecmp(ct, needle, needle_len) != 0) {
        return 0;
    }
    if (ct_len == needle_len) {
        return 1;
    }
    {
        uint8_t b = ct[needle_len];
        return (b == ';' || b == ' ' || b == '\t');
    }
}

static int
ref_is_streaming(const struct FFIEligibilityInput *in)
{
    static const uint8_t  text_event_stream[] = "text/event-stream";

    if (in->content_type_len == 0) {
        return 0;
    }
    if (ref_match_prefix(in->content_type, in->content_type_len,
                         text_event_stream, sizeof(text_event_stream) - 1))
    {
        return 1;
    }
    for (uintptr_t i = 0; i < in->stream_types_count; i++) {
        if (ref_match_prefix(in->content_type, in->content_type_len,
                             in->stream_types[i].data,
                             in->stream_types[i].len))
        {
            return 1;
        }
    }
    return 0;
}

static int
ref_content_type_allowed(const struct FFIEligibilityInput *in)
{
    static const uint8_t  text_html[] = "text/html";

    if (in->content_type_len == 0) {
        return 0;
    }
    if (in->content_types_count == 0) {
        return ref_match_prefix(in->content_type, in->content_type_len,
                                text_html, sizeof(text_html) - 1);
    }
    for (uintptr_t i = 0; i < in->content_types_count; i++) {
        if (ref_match_prefix(in->content_type, in->content_type_len,
                             in->content_types[i].data,
                             in->content_types[i].len))
        {
            return 1;
        }
    }
    return 0;
}

static uint8_t
reference_decide(const struct FFIEligibilityInput *in)
{
    if (!in->filter_enabled) {
        return 8; /* INELIGIBLE_CONFIG */
    }
    if (!in->method_get_or_head) {
        return 1; /* INELIGIBLE_METHOD */
    }
    if (in->status != 200) {
        return (in->status == 206) ? 7 : 2; /* RANGE : STATUS */
    }
    if (in->has_range_header) {
        return 7; /* INELIGIBLE_RANGE */
    }
    if (ref_is_streaming(in)) {
        return 5; /* INELIGIBLE_STREAMING */
    }
    if (!ref_content_type_allowed(in)) {
        return 3; /* INELIGIBLE_CONTENT_TYPE */
    }
    if (in->content_length >= 0 && in->body_limit != 0
        && (uint64_t) in->content_length > (uint64_t) in->body_limit)
    {
        return 4; /* INELIGIBLE_SIZE */
    }

    return 0; /* ELIGIBLE */
}

uint8_t
markdown_decide_eligibility(const struct FFIEligibilityInput *input)
{
    g_call_count++;

    if (input != NULL) {
        g_last_input = *input;
        g_have_last_input = 1;
    }

    if (g_force_mode) {
        return g_forced_code;
    }

    if (input == NULL) {
        /* Mirror the Rust NULL contract (Rule 46): safe skip. */
        return 8; /* INELIGIBLE_CONFIG */
    }

    return reference_decide(input);
}

static void
set_str(ngx_str_t *s, const char *val)
{
    s->data = (u_char *) (uintptr_t) val;
    s->len = strlen(val);
}

static void
init_conf(ngx_http_markdown_conf_t *conf)
{
    memset(conf, 0, sizeof(*conf));
    conf->routing.content_types = NULL;
    conf->routing.stream_types = NULL;
    conf->max_size = (size_t) -1;
}

/* Build a baseline eligible request: GET / 200 / text/html / 1024 bytes. */
static void
init_base_request(ngx_http_request_t *r)
{
    memset(r, 0, sizeof(*r));
    r->pool = &g_pool;
    r->method = NGX_HTTP_GET;
    r->headers_out.status = NGX_HTTP_OK;
    r->headers_out.content_length_n = 1024;
    set_str(&r->headers_out.content_type, "text/html");
}

static void
reset_ffi_capture(void)
{
    g_palloc_offset = 0;
    g_have_last_input = 0;
    g_call_count = 0;
    g_force_mode = 0;
    g_forced_code = 0;
    memset(&g_last_input, 0, sizeof(g_last_input));
}


/*
 * Parity: Content-Type matching through the production wrapper with the
 * default (unconfigured) text/html allowlist.  Mirrors the legacy
 * check_content_type matrix but now exercises the thin wrapper + oracle.
 */
static void
test_parity_content_type_default(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("parity: default text/html content-type matching");

    init_conf(&conf);
    conf.max_size = 10 * 1024 * 1024;

    init_base_request(&r);
    reset_ffi_capture();
    set_str(&r.headers_out.content_type, "text/html");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "text/html should match default -> ELIGIBLE");

    set_str(&r.headers_out.content_type, "text/html;charset=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "text/html;charset=utf-8 should match (';' boundary)");

    set_str(&r.headers_out.content_type, "text/html encoding=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "text/html with space separator should match");

    set_str(&r.headers_out.content_type, "text/html\t;charset=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "text/html with HTAB separator should match");

    set_str(&r.headers_out.content_type, "text/htmlx");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE,
        "text/htmlx must NOT match (no boundary char)");

    set_str(&r.headers_out.content_type, "application/json");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE,
        "application/json must NOT match default");

    r.headers_out.content_type.len = 0;
    r.headers_out.content_type.data = NULL;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE,
        "empty content-type must NOT match");

    TEST_PASS("Default text/html content-type parity correct");
}


/*
 * Parity: configured markdown_content_types allowlist replaces the default,
 * exercised through the wrapper (verifies the marshalled FFIStr array).
 */
static void
test_parity_content_type_custom_allowlist(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_array_t ct_array;
    ngx_str_t ct_entries[2];

    TEST_SUBSECTION("parity: custom content-type allowlist");

    init_conf(&conf);
    conf.max_size = 10 * 1024 * 1024;

    set_str(&ct_entries[0], "text/html");
    set_str(&ct_entries[1], "application/xhtml+xml");
    ct_array.elts = ct_entries;
    ct_array.nelts = 2;
    ct_array.size = sizeof(ngx_str_t);
    ct_array.nalloc = 2;
    ct_array.pool = &g_pool;
    conf.routing.content_types = &ct_array;

    init_base_request(&r);
    reset_ffi_capture();

    set_str(&r.headers_out.content_type, "text/html");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "text/html matches custom allowlist");

    set_str(&r.headers_out.content_type, "application/xhtml+xml");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "application/xhtml+xml matches custom allowlist");

    set_str(&r.headers_out.content_type,
            "application/xhtml+xml\t;charset=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "custom allowlist should accept HTAB separator");

    set_str(&r.headers_out.content_type, "application/json");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE,
        "application/json not in allowlist");

    /* Confirm the configured allowlist was marshalled across the FFI. */
    TEST_ASSERT(g_have_last_input && g_last_input.content_types_count == 2,
                "content_types_count marshalled as 2");

    TEST_PASS("Custom content-type allowlist parity correct");
}


/*
 * Parity: streaming detection (built-in SSE + configured stream_types)
 * through the wrapper.  Streaming must win over the content-type check.
 */
static void
test_parity_streaming_detection(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_array_t st_array;
    ngx_str_t st_entries[1];

    TEST_SUBSECTION("parity: streaming detection");

    init_conf(&conf);
    conf.max_size = 10 * 1024 * 1024;

    init_base_request(&r);
    reset_ffi_capture();

    set_str(&r.headers_out.content_type, "text/event-stream");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING,
        "text/event-stream -> INELIGIBLE_STREAMING");

    set_str(&r.headers_out.content_type, "text/event-stream;charset=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING,
        "text/event-stream with charset -> INELIGIBLE_STREAMING");

    /* Configured stream_types exclusion. */
    set_str(&st_entries[0], "application/x-ndjson");
    st_array.elts = st_entries;
    st_array.nelts = 1;
    st_array.size = sizeof(ngx_str_t);
    st_array.nalloc = 1;
    st_array.pool = &g_pool;
    conf.routing.stream_types = &st_array;

    reset_ffi_capture();
    set_str(&r.headers_out.content_type, "application/x-ndjson\t;charset=utf-8");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING,
        "configured stream type -> INELIGIBLE_STREAMING");
    TEST_ASSERT(g_have_last_input && g_last_input.stream_types_count == 1,
                "stream_types_count marshalled as 1");

    set_str(&r.headers_out.content_type, "text/html");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "text/html not in stream_types -> ELIGIBLE");

    TEST_PASS("Streaming detection parity correct");
}


/*
 * Parity: the full decision chain in legacy order, through the wrapper.
 */
static void
test_check_eligibility_full_chain(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_table_elt_t range_hdr;

    TEST_SUBSECTION("parity: full decision chain");

    init_conf(&conf);
    conf.max_size = 10 * 1024 * 1024;
    memset(&range_hdr, 0, sizeof(range_hdr));

    init_base_request(&r);
    reset_ffi_capture();

    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "valid GET/200/text-html should be ELIGIBLE");

    r.method = NGX_HTTP_HEAD;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "HEAD method should be ELIGIBLE");

    r.method = 2;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD,
        "POST method -> INELIGIBLE_METHOD");

    r.method = NGX_HTTP_GET;
    r.headers_out.status = 404;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        "404 status -> INELIGIBLE_STATUS");

    r.headers_out.status = NGX_HTTP_PARTIAL_CONTENT;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE,
        "206 status -> INELIGIBLE_RANGE (not INELIGIBLE_STATUS)");

    r.headers_out.status = NGX_HTTP_OK;
    r.headers_in.range = &range_hdr;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE,
        "Range header present -> INELIGIBLE_RANGE");

    r.headers_in.range = NULL;
    set_str(&r.headers_out.content_type, "text/event-stream");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING,
        "SSE content type -> INELIGIBLE_STREAMING");

    set_str(&r.headers_out.content_type, "application/json");
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE,
        "application/json -> INELIGIBLE_CONTENT_TYPE");

    set_str(&r.headers_out.content_type, "text/html");
    r.headers_out.content_length_n = 100 * 1024 * 1024;
    conf.max_size = 10 * 1024 * 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE,
        "oversized response -> INELIGIBLE_SIZE");

    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 0, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG,
        "filter_enabled=0 -> INELIGIBLE_CONFIG");

    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, NULL, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG,
        "NULL conf -> INELIGIBLE_CONFIG");

    TEST_PASS("Full eligibility decision chain parity correct");
}


/*
 * Parity: size boundary cases through the wrapper, including the
 * memory_budget precedence resolved by the effective-conf helper.
 */
static void
test_parity_size_boundary(void)
{
    ngx_http_request_t                  r;
    ngx_http_markdown_conf_t            conf;
    ngx_http_markdown_effective_conf_t  eff;

    TEST_SUBSECTION("parity: size limit boundary");

    init_conf(&conf);
    init_base_request(&r);
    reset_ffi_capture();

    conf.max_size = 1024;

    r.headers_out.content_length_n = 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "exactly max_size should be ELIGIBLE");

    r.headers_out.content_length_n = 1025;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE,
        "max_size+1 -> INELIGIBLE_SIZE");

    r.headers_out.content_length_n = -1;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "missing Content-Length (-1) should be ELIGIBLE");

    /* max_size=0 means unlimited. */
    conf.max_size = 0;
    r.headers_out.content_length_n = 100 * 1024 * 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "max_size=0 (unlimited) + large CL should be ELIGIBLE");

    /* eff->memory_budget stricter than conf.max_size governs. */
    memset(&eff, 0, sizeof(eff));
    conf.max_size = 10 * 1024 * 1024;
    eff.memory_budget = 512;
    r.headers_out.content_length_n = 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, &eff)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE,
        "eff.memory_budget < CL -> INELIGIBLE_SIZE");
    TEST_ASSERT(g_have_last_input && g_last_input.body_limit == 512,
                "body_limit resolved to memory_budget (512)");

    /* eff->memory_budget unlimited sentinel falls back to max_size. */
    eff.memory_budget = (size_t) -1;
    r.headers_out.content_length_n = 1024;
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, &eff)
            == NGX_HTTP_MARKDOWN_ELIGIBLE,
        "eff.memory_budget=unlimited + CL within max_size -> ELIGIBLE");

    TEST_PASS("Size limit boundary parity correct");
}


/*
 * Thin-wrapper: GET and HEAD must produce the same decision and the same
 * marshalled method flag; a non-GET/HEAD method marshals 0 and is rejected.
 */
static void
test_method_get_head_equivalent(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_eligibility_t e_get;
    ngx_http_markdown_eligibility_t e_head;

    TEST_SUBSECTION("thin wrapper: GET and HEAD decide identically");

    init_conf(&conf);
    conf.max_size = 10 * 1024 * 1024;

    init_base_request(&r);
    reset_ffi_capture();

    r.method = NGX_HTTP_GET;
    e_get = ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL);
    TEST_ASSERT(g_have_last_input && g_last_input.method_get_or_head == 1,
                "GET marshals method_get_or_head=1");

    r.method = NGX_HTTP_HEAD;
    e_head = ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL);
    TEST_ASSERT(g_last_input.method_get_or_head == 1,
                "HEAD marshals method_get_or_head=1");

    TEST_ASSERT(e_get == NGX_HTTP_MARKDOWN_ELIGIBLE && e_get == e_head,
                "GET and HEAD yield the same ELIGIBLE decision");

    r.method = 2; /* neither GET nor HEAD */
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD,
        "non-GET/HEAD -> INELIGIBLE_METHOD");
    TEST_ASSERT(g_last_input.method_get_or_head == 0,
                "non-GET/HEAD marshals method_get_or_head=0");

    TEST_PASS("GET/HEAD wrapper equivalence correct");
}


/*
 * Thin-wrapper: every request/config field is marshalled into the
 * FFIEligibilityInput exactly once per call.
 */
static void
test_marshalling_fidelity(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_table_elt_t range_hdr;

    TEST_SUBSECTION("thin wrapper: input marshalling fidelity");

    init_conf(&conf);
    conf.max_size = 4096;
    memset(&range_hdr, 0, sizeof(range_hdr));

    init_base_request(&r);
    r.headers_out.status = NGX_HTTP_OK;
    r.headers_out.content_length_n = 2048;
    r.headers_in.range = &range_hdr;
    set_str(&r.headers_out.content_type, "text/html");

    reset_ffi_capture();
    (void) ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL);

    TEST_ASSERT(g_call_count == 1, "FFI called exactly once");
    TEST_ASSERT(g_last_input.filter_enabled == 1, "filter_enabled marshalled");
    TEST_ASSERT(g_last_input.status == 200, "status marshalled");
    TEST_ASSERT(g_last_input.has_range_header == 1,
                "has_range_header marshalled from headers_in.range");
    TEST_ASSERT(g_last_input.content_type_len == 9,
                "content_type_len marshalled");
    TEST_ASSERT(g_last_input.content_length == 2048,
                "content_length marshalled");
    TEST_ASSERT(g_last_input.body_limit == 4096,
                "body_limit resolved from conf.max_size");
    TEST_ASSERT(g_last_input.content_types_count == 0,
                "no allowlist -> content_types_count 0");
    TEST_ASSERT(g_last_input.stream_types_count == 0,
                "no stream types -> stream_types_count 0");

    TEST_PASS("Marshalling fidelity correct");
}


/*
 * Thin-wrapper: each u8 returned by the FFI maps to the matching enum.
 */
static void
test_u8_to_enum_mapping(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    static const ngx_http_markdown_eligibility_t expect[9] = {
        NGX_HTTP_MARKDOWN_ELIGIBLE,
        NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD,
        NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
        NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE,
        NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE,
        NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING,
        NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH,
        NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE,
        NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG
    };

    TEST_SUBSECTION("thin wrapper: u8 return -> enum mapping");

    init_conf(&conf);
    conf.max_size = 10 * 1024 * 1024;
    init_base_request(&r);

    for (uint8_t code = 0; code < 9; code++) {
        reset_ffi_capture();
        g_force_mode = 1;
        g_forced_code = code;
        TEST_ASSERT(
            ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
                == expect[code],
            "FFI u8 maps to matching eligibility enum");
    }
    g_force_mode = 0;

    TEST_PASS("u8-to-enum mapping correct");
}


/*
 * Thin-wrapper: NULL request or configuration fails open (skip) without
 * invoking the FFI.
 */
static void
test_null_inputs_fail_open(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("thin wrapper: NULL inputs fail open");

    init_conf(&conf);
    init_base_request(&r);

    reset_ffi_capture();
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(NULL, &conf, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG,
        "NULL request -> INELIGIBLE_CONFIG");
    TEST_ASSERT(g_call_count == 0, "NULL request must not call the FFI");

    reset_ffi_capture();
    TEST_ASSERT(
        ngx_http_markdown_check_eligibility(&r, NULL, 1, NULL)
            == NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG,
        "NULL conf -> INELIGIBLE_CONFIG");
    TEST_ASSERT(g_call_count == 0, "NULL conf must not call the FFI");

    TEST_PASS("NULL-input fail-open correct");
}


static void
test_eligibility_string_all_values(void)
{
    const ngx_str_t *s;

    TEST_SUBSECTION("eligibility_string: all enum values");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_ELIGIBLE);
    TEST_ASSERT(s != NULL && s->len > 0, "ELIGIBLE string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_METHOD string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_STATUS string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_CONTENT_TYPE string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_SIZE string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_STREAMING string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_AUTH string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_RANGE string non-empty");

    s = ngx_http_markdown_eligibility_string(NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG);
    TEST_ASSERT(s != NULL && s->len > 0, "INELIGIBLE_CONFIG string non-empty");

    s = ngx_http_markdown_eligibility_string((ngx_http_markdown_eligibility_t) 99);
    TEST_ASSERT(s != NULL && s->len > 0, "Unknown eligibility -> 'unknown' string");

    TEST_PASS("All eligibility string values verified");
}


/*
 * stream_type_excluded remains a C-side helper (used by the streaming
 * engine selector, not the eligibility decision); verify it still works.
 */
static void
test_stream_type_excluded(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_str_t ct;

    TEST_SUBSECTION("stream_type_excluded: built-in hard exclusions");

    init_conf(&conf);

    set_str(&ct, "text/event-stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
                "text/event-stream is excluded");

    set_str(&ct, "application/x-ndjson; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
                "application/x-ndjson is excluded (params ignored)");

    set_str(&ct, "application/stream+json");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
                "application/stream+json is excluded");

    set_str(&ct, "text/html");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
                "text/html is not excluded");

    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(NULL, &conf) == 0,
                "NULL content type is not excluded");

    TEST_PASS("stream_type_excluded correct");
}


static void
test_zero_limit_buffer_initializes_lazily(void)
{
    ngx_http_markdown_buffer_t  buffer;
    ngx_log_t                   log;
    ngx_pool_t                  pool;

    TEST_SUBSECTION("buffer_init: zero limit remains lazy");

    memset(&buffer, 0, sizeof(buffer));
    memset(&log, 0, sizeof(log));
    memset(&pool, 0, sizeof(pool));
    pool.log = &log;
    test_alloc_calls = 0;

    TEST_ASSERT(ngx_http_markdown_buffer_init(&buffer, 0, &pool) == NGX_OK,
                "zero limit should initialize as unlimited");
    TEST_ASSERT(test_alloc_calls == 0,
                "unlimited initialization should not allocate eagerly");
    TEST_ASSERT(ngx_http_markdown_buffer_append(
                    &buffer, (const u_char *) "test", 4) == NGX_OK,
                "unlimited buffer should accept a bounded append");
    TEST_ASSERT(test_alloc_calls == 1,
                "first append should allocate only on demand");

    test_cleanup.handler(test_cleanup.data);
    TEST_PASS("Zero limit initializes lazily and appends on demand");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("eligibility_impl Tests (production code)\n");
    printf("========================================\n");

    test_parity_content_type_default();
    test_parity_content_type_custom_allowlist();
    test_parity_streaming_detection();
    test_check_eligibility_full_chain();
    test_parity_size_boundary();
    test_method_get_head_equivalent();
    test_marshalling_fidelity();
    test_u8_to_enum_mapping();
    test_null_inputs_fail_open();
    test_eligibility_string_all_values();
    test_stream_type_excluded();
    test_zero_limit_buffer_initializes_lazily();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
