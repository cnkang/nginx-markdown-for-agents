/*
 * Test: eligibility_impl
 *
 * Includes the actual production source so coverage instruments the real
 * decision paths that gate all conversion.
 *
 * As of the Rust-first decision core, the eligibility *decision* lives
 * in the Rust core (markdown_decide_eligibility); the C function
 * ngx_http_markdown_check_eligibility is a thin wrapper that marshals
 * request/config fields into an FFIEligibilityInput, calls the FFI, and maps
 * the returned u8 back to ngx_http_markdown_eligibility_t.
 *
 * Because the C unit-test build does not link the Rust library, this test
 * stubs markdown_decide_eligibility with a controlled return value. The C
 * tests verify request/config marshalling and the returned discriminant
 * mapping; eligibility semantics are tested by the Rust decision-core tests.
 *
 * Validates: FR-02.1 (method), FR-02.2 (status), FR-02.3 (content-type),
 *            FR-02.8 (streaming), FR-07.2 (range), FR-10.1 (size),
 *            the FFI boundary and thin-wrapper requirements.
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
static ngx_uint_t          test_free_calls;

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

static void
test_ngx_free(void *data)
{
    if (data != NULL) {
        test_free_calls++;
    }
    free(data);
}

#define ngx_free test_ngx_free
#define ngx_memcpy memcpy

#include "../../src/ngx_http_markdown_buffer.c"


static ngx_pool_t g_pool;

/*
 * FFI capture stub for markdown_decide_eligibility.
 *
 * The Rust decision engine owns eligibility semantics. This C test exercises
 * only the request-to-FFI boundary and the returned discriminant mapping.
 */
static struct FFIEligibilityInput  g_last_input;
static int                         g_have_last_input;
static uint8_t                     g_forced_code;
static int                         g_call_count;

uint8_t
markdown_decide_eligibility(const struct FFIEligibilityInput *input)
{
    g_call_count++;

    if (input != NULL) {
        g_last_input = *input;
        g_have_last_input = 1;
    }

    if (input == NULL) {
        return 8;
    }

    return g_forced_code;
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
    g_forced_code = 0;
    memset(&g_last_input, 0, sizeof(g_last_input));
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
    g_forced_code = NGX_HTTP_MARKDOWN_ELIGIBLE;

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
    g_forced_code = NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD;
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
        g_forced_code = code;
        TEST_ASSERT(
            ngx_http_markdown_check_eligibility(&r, &conf, 1, NULL)
                == expect[code],
            "FFI u8 maps to matching eligibility enum");
    }

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
    test_free_calls = 0;

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

static void
test_buffer_release_frees_backing_store(void)
{
    ngx_http_markdown_buffer_t  buffer;
    ngx_log_t                   log;
    ngx_pool_t                  pool;

    TEST_SUBSECTION("buffer_release: frees backing store (subrequest)");

    memset(&buffer, 0, sizeof(buffer));
    memset(&log, 0, sizeof(log));
    memset(&pool, 0, sizeof(pool));
    pool.log = &log;
    test_alloc_calls = 0;
    test_free_calls = 0;

    TEST_ASSERT(ngx_http_markdown_buffer_init(&buffer, 0, &pool) == NGX_OK,
                "init should succeed");
    TEST_ASSERT(ngx_http_markdown_buffer_append(
                    &buffer, (const u_char *) "hello", 5) == NGX_OK,
                "append should succeed");
    TEST_ASSERT(buffer.data != NULL,
                "backing store should be allocated after append");

    /* Active release at conversion terminal (pool NOT destroyed). */
    ngx_http_markdown_buffer_release(&buffer);

    TEST_ASSERT(buffer.data == NULL,
                "release should free the backing store");
    TEST_ASSERT(buffer.size == 0 && buffer.capacity == 0,
                "release should reset size/capacity");
    TEST_ASSERT(test_free_calls == 1,
                "release should free the backing store exactly once");

    /* Idempotent: second release is a no-op. */
    ngx_http_markdown_buffer_release(&buffer);
    TEST_ASSERT(buffer.data == NULL,
                "second release should be a no-op");

    /* Pool cleanup after active release must not double-free. */
    test_cleanup.handler(test_cleanup.data);
    TEST_ASSERT(test_free_calls == 1,
                "pool cleanup after release must not access or free storage");
    TEST_PASS("buffer_release frees backing store and is idempotent");
}

static void
test_buffer_release_null_safe(void)
{
    TEST_SUBSECTION("buffer_release: NULL is a no-op");
    ngx_http_markdown_buffer_release(NULL);
    TEST_PASS("buffer_release(NULL) is a no-op");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("eligibility_impl Tests (production code)\n");
    printf("========================================\n");

    test_method_get_head_equivalent();
    test_marshalling_fidelity();
    test_u8_to_enum_mapping();
    test_null_inputs_fail_open();
    test_eligibility_string_all_values();
    test_stream_type_excluded();
    test_zero_limit_buffer_initializes_lazily();
    test_buffer_release_frees_backing_store();
    test_buffer_release_null_safe();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
