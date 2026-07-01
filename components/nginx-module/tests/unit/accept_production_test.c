#include "../include/test_common.h"
#include <strings.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

#ifndef NGX_OK
#define NGX_OK          0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR       -1
#endif
#ifndef NGX_AGAIN
#define NGX_AGAIN       -2
#endif
#ifndef NGX_DECLINED
#define NGX_DECLINED    -5
#endif
#ifndef NGX_DONE
#define NGX_DONE        -4
#endif

#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif

/*
 * No-op stubs for NGINX debug logging macros.
 *
 * The production source included below calls ngx_log_debug{0,1,2} which
 * normally expand to runtime logging.  In unit tests we suppress all output
 * without evaluating arguments — this avoids null-pointer dereferences when
 * test scaffolding leaves r->connection NULL (Sonar S2259).
 * The numeric suffix indicates the number of format-string values (NGINX
 * convention); macro names must match the production API exactly.
 */

#ifdef ngx_log_debug0
#undef ngx_log_debug0
#endif
#define ngx_log_debug0(severity, logger, errcode, fmt)  ((void)0)

#ifdef ngx_log_debug1
#undef ngx_log_debug1
#endif
#define ngx_log_debug1(severity, logger, errcode, fmt, val1)  ((void)0)

#ifdef ngx_log_debug2
#undef ngx_log_debug2
#endif
#define ngx_log_debug2(severity, logger, errcode, fmt, val1, val2)  ((void)0)

struct ngx_pool_s {
    int dummy;
};

typedef struct {
    ngx_str_t key;
    ngx_str_t value;
    ngx_uint_t hash;
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
        ngx_table_elt_t *accept;
    } headers_in;
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
    void *p;
    UNUSED(pool);
    if (g_pool_offset + size > sizeof(g_pool_buf)) {
        return NULL;
    }
    p = g_pool_buf + g_pool_offset;
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

static int g_ffi_should_convert;
static int g_ffi_reason;

void
markdown_negotiate_accept(const uint8_t *accept_header,
                          uintptr_t accept_header_len,
                          uint8_t on_wildcard,
                          struct FFIAcceptResult *result)
{
    UNUSED(accept_header);
    UNUSED(accept_header_len);
    UNUSED(on_wildcard);
    result->should_convert = (uint8_t) g_ffi_should_convert;
    result->reason = (uint8_t) g_ffi_reason;
}

void
markdown_result_free(struct MarkdownResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_converter_free(struct MarkdownConverterHandle *handle)
{
    UNUSED(handle);
}

void
markdown_options_init(struct MarkdownOptions *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
        result->timeout_ms = 5000;
        result->generate_etag = 0;
    }
}

void
markdown_header_plan_init(struct FFIHeaderPlan *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_build_header_plan(const uint8_t *content_type,
                           uintptr_t content_type_len,
                           uint8_t has_etag,
                           struct FFIHeaderPlan *result)
{
    UNUSED(content_type); UNUSED(content_type_len);
    UNUSED(has_etag); UNUSED(result);
}

void
markdown_accept_result_init(struct FFIAcceptResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_conditional_result_init(struct FFIConditionalResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_decision_result_init(struct FFIDecisionResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_decompress_free(struct FFIDecompResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_decomp_result_init(struct FFIDecompResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

uint32_t
markdown_decompress_bounded(const uint8_t *input,
                            uintptr_t input_len,
                            uint8_t format,
                            uintptr_t budget,
                            struct FFIDecompResult *result)
{
    UNUSED(input); UNUSED(input_len); UNUSED(format);
    UNUSED(budget); UNUSED(result);
    return 0;
}

const uint8_t *
markdown_reason_code_str(uint32_t code, uintptr_t *out_len)
{
    UNUSED(code);
    if (out_len != NULL) {
        *out_len = 0;
    }
    return NULL;
}

const uint8_t *
markdown_reason_code_metric_key(uint32_t code, uintptr_t *out_len)
{
    UNUSED(code);
    if (out_len != NULL) {
        *out_len = 0;
    }
    return NULL;
}

uint32_t
markdown_reason_code_count(void)
{
    return 0;
}

void
ngx_http_clear_content_length(ngx_http_request_t *r)
{
    UNUSED(r);
}

#include "../../src/ngx_http_markdown_accept.c"

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
add_header(ngx_list_t *list, const char *key, const char *value)
{
    ngx_table_elt_t *h = ngx_list_push(list);
    if (h == NULL) return NULL;
    h->key.data = (u_char *) key;
    h->key.len = strlen(key);
    h->value.data = (u_char *) value;
    h->value.len = strlen(value);
    h->hash = 1;
    return h;
}

static void
test_find_request_header_null(void)
{
    const ngx_table_elt_t *h;
    h = ngx_http_markdown_find_request_header(
        NULL, (u_char *) "Accept", 6);
    TEST_ASSERT(h == NULL, "NULL request returns NULL");
    TEST_PASS("null request handled");
}

static void
test_find_request_header_empty_headers(void)
{
    ngx_http_request_t r;
    const ngx_list_t *list;

    memset(&r, 0, sizeof(r));
    g_pool_offset = 0;
    list = create_header_list();
    if (list == NULL) { TEST_FAIL("create_header_list failed"); return; }
    r.headers_in.headers = *list;
    r.headers_in.headers.part.nelts = 0;

    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        &r, (u_char *) "Accept", 6);
    TEST_ASSERT(h == NULL, "Empty headers returns NULL");
    TEST_PASS("empty headers handled");
}

static void
test_find_request_header_found(void)
{
    ngx_http_request_t r;

    memset(&r, 0, sizeof(r));
    g_pool_offset = 0;
    r.headers_in.headers = *create_header_list();
    add_header(&r.headers_in.headers, "Host", "example.com");
    add_header(&r.headers_in.headers, "Accept", "text/html");

    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        &r, (u_char *) "Accept", 6);
    TEST_ASSERT(h != NULL, "Accept header found");
    TEST_ASSERT(h->value.len == 9, "Accept value length check");
    TEST_PASS("header found by name");
}

static void
test_find_request_header_case_insensitive(void)
{
    ngx_http_request_t r;

    memset(&r, 0, sizeof(r));
    g_pool_offset = 0;
    r.headers_in.headers = *create_header_list();
    add_header(&r.headers_in.headers, "accept", "text/markdown");

    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        &r, (u_char *) "Accept", 6);
    TEST_ASSERT(h != NULL, "Case-insensitive match works");
    TEST_PASS("case-insensitive header match");
}

static void
test_find_request_header_not_found(void)
{
    ngx_http_request_t r;

    memset(&r, 0, sizeof(r));
    g_pool_offset = 0;
    r.headers_in.headers = *create_header_list();
    add_header(&r.headers_in.headers, "Host", "example.com");
    add_header(&r.headers_in.headers, "Content-Type", "text/html");

    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        &r, (u_char *) "Accept", 6);
    TEST_ASSERT(h == NULL, "Non-existent header returns NULL");
    TEST_PASS("non-existent header returns NULL");
}

static void
test_find_request_header_hash_zero_skipped(void)
{
    ngx_http_request_t r;

    memset(&r, 0, sizeof(r));
    g_pool_offset = 0;
    r.headers_in.headers = *create_header_list();

    ngx_table_elt_t *h1 = add_header(&r.headers_in.headers,
        "Accept", "text/markdown");
    h1->hash = 0;

    add_header(&r.headers_in.headers, "Accept", "text/html");

    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        &r, (u_char *) "Accept", 6);
    TEST_ASSERT(h != NULL, "Hash-zero entry skipped, next found");
    TEST_ASSERT(h->value.len == 9, "Found second Accept with valid hash");
    TEST_PASS("hash==0 entry skipped");
}

static void
test_find_request_header_chain_traversal(void)
{
    ngx_http_request_t r;
    ngx_list_part_t part2;
    ngx_table_elt_t part2_elts[1];

    memset(&r, 0, sizeof(r));
    memset(&part2, 0, sizeof(part2));
    memset(part2_elts, 0, sizeof(part2_elts));

    g_pool_offset = 0;
    r.headers_in.headers = *create_header_list();
    r.headers_in.headers.part.next = &part2;
    part2.elts = part2_elts;
    part2.nelts = 1;
    part2.next = NULL;
    part2_elts[0].key.data = (u_char *) "Accept";
    part2_elts[0].key.len = 6;
    part2_elts[0].value.data = (u_char *) "text/markdown";
    part2_elts[0].value.len = 13;
    part2_elts[0].hash = 1;

    add_header(&r.headers_in.headers, "Host", "example.com");

    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        &r, (u_char *) "Accept", 6);
    TEST_ASSERT(h != NULL, "Header found in second list part");
    TEST_ASSERT(MEM_EQ(h->value.data, "text/markdown", 13),
        "Correct value found in second part");
    TEST_PASS("chain traversal works");
}

static void
test_find_request_header_null_name(void)
{
    ngx_http_request_t r;
    memset(&r, 0, sizeof(r));

    const ngx_table_elt_t *h = ngx_http_markdown_find_request_header(
        &r, NULL, 0);
    TEST_ASSERT(h == NULL, "NULL name returns NULL");
    TEST_PASS("NULL name handled");
}

static void
test_get_accept_header_null_request(void)
{
    const ngx_table_elt_t *h = ngx_http_markdown_get_accept_header(NULL);
    TEST_ASSERT(h == NULL, "NULL request returns NULL");
    TEST_PASS("NULL request handled");
}

static void
test_get_accept_header_fallback(void)
{
    ngx_http_request_t r;

    memset(&r, 0, sizeof(r));
    g_pool_offset = 0;
    r.headers_in.accept = NULL;
    r.headers_in.headers = *create_header_list();
    add_header(&r.headers_in.headers, "Accept", "text/html");

    const ngx_table_elt_t *h = ngx_http_markdown_get_accept_header(&r);
    TEST_ASSERT(h != NULL, "Fallback to find_request_header works");
    TEST_PASS("fallback search works");
}

static void
test_should_convert_null_conf(void)
{
    ngx_uint_t reason = 99;
    ngx_int_t rc = ngx_http_markdown_should_convert(NULL, NULL, &reason);
    TEST_ASSERT(rc == 0, "NULL conf returns 0");
    TEST_ASSERT(reason == NEGOTIATE_REASON_NO_ACCEPT,
        "reason = NO_ACCEPT");
    TEST_PASS("NULL conf handled");
}

static void
test_should_convert_null_conf_null_reason(void)
{
    ngx_int_t rc = ngx_http_markdown_should_convert(NULL, NULL, NULL);
    TEST_ASSERT(rc == 0, "NULL conf + NULL reason does not crash");
    TEST_PASS("NULL conf + NULL reason handled");
}

static void
test_should_convert_no_accept_header(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_uint_t reason = 99;

    memset(&r, 0, sizeof(r));
    memset(&conf, 0, sizeof(conf));
    g_pool_offset = 0;
    r.headers_in.headers = *create_header_list();

    g_ffi_should_convert = 0;
    g_ffi_reason = 0;

    ngx_int_t rc = ngx_http_markdown_should_convert(
        &r, &conf, &reason);
    TEST_ASSERT(rc == 0, "Missing Accept returns 0");
    TEST_ASSERT(reason == NEGOTIATE_REASON_NO_ACCEPT,
        "reason = NO_ACCEPT");
    TEST_PASS("no Accept header handled");
}

static void
test_should_convert_ffi_convert(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_uint_t reason;

    memset(&r, 0, sizeof(r));
    memset(&conf, 0, sizeof(conf));
    g_pool_offset = 0;
    r.connection = (ngx_connection_t *) ngx_palloc(NULL,
        sizeof(ngx_connection_t));
    if (r.connection == NULL) {
        TEST_FAIL("pool alloc failed"); return;
    }
    memset(r.connection, 0, sizeof(ngx_connection_t));
    r.headers_in.headers = *create_header_list();
    add_header(&r.headers_in.headers, "Accept", "text/markdown");

    g_ffi_should_convert = 1;
    g_ffi_reason = NEGOTIATE_REASON_CONVERT;

    ngx_int_t rc = ngx_http_markdown_should_convert(
        &r, &conf, &reason);
    TEST_ASSERT(rc == 1, "should_convert returns 1");
    TEST_ASSERT(reason == NEGOTIATE_REASON_CONVERT,
        "reason = CONVERT");
    TEST_PASS("FFI convert works");
}

static void
test_should_convert_ffi_skip(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_uint_t reason = 99;

    memset(&r, 0, sizeof(r));
    memset(&conf, 0, sizeof(conf));
    g_pool_offset = 0;
    r.connection = (ngx_connection_t *) ngx_palloc(NULL,
        sizeof(ngx_connection_t));
    if (r.connection == NULL) {
        TEST_FAIL("pool alloc failed"); return;
    }
    memset(r.connection, 0, sizeof(ngx_connection_t));
    r.headers_in.headers = *create_header_list();
    add_header(&r.headers_in.headers, "Accept", "text/html");

    g_ffi_should_convert = 0;
    g_ffi_reason = NEGOTIATE_REASON_LOWER_Q;

    ngx_int_t rc = ngx_http_markdown_should_convert(
        &r, &conf, &reason);
    TEST_ASSERT(rc == 0, "should_convert returns 0");
    TEST_ASSERT(reason == NEGOTIATE_REASON_LOWER_Q,
        "reason = LOWER_Q");
    TEST_PASS("FFI skip works");
}

static void
test_should_convert_out_reason_null(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;

    memset(&r, 0, sizeof(r));
    memset(&conf, 0, sizeof(conf));
    r.connection = (ngx_connection_t *) ngx_palloc(NULL,
        sizeof(ngx_connection_t));
    memset(r.connection, 0, sizeof(ngx_connection_t));
    g_pool_offset = 0;
    r.headers_in.headers = *create_header_list();
    add_header(&r.headers_in.headers, "Accept", "text/html");

    g_ffi_should_convert = 0;
    g_ffi_reason = NEGOTIATE_REASON_NO_ACCEPT;

    ngx_int_t rc = ngx_http_markdown_should_convert(
        &r, &conf, NULL);
    TEST_ASSERT(rc == 0, "NULL out_reason does not crash");
    TEST_PASS("NULL out_reason handled");
}

/*
 * Verify that the markdown_options_init stub exposes the same defaults
 * as the Rust implementation (timeout_ms=5000, generate_etag=0).
 * This test ensures accept/production tests fail if the default FFI
 * contract ever drifts from the Rust implementation.
 */
static void
test_markdown_options_init_defaults(void)
{
    struct MarkdownOptions options;

    /* Start with garbage to ensure init actually sets values. */
    memset(&options, 0xFF, sizeof(options));

    markdown_options_init(&options);

    TEST_ASSERT(options.timeout_ms == 5000,
        "markdown_options_init sets timeout_ms=5000");
    TEST_ASSERT(options.generate_etag == 0,
        "markdown_options_init sets generate_etag=0");
    TEST_PASS("markdown_options_init production defaults verified");
}

/*
 * Verify that markdown_options_init handles NULL safely.
 */
static void
test_markdown_options_init_null(void)
{
    markdown_options_init(NULL);
    TEST_PASS("markdown_options_init(NULL) does not crash");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("accept_production Tests\n");
    printf("========================================\n");

    test_find_request_header_null();
    test_find_request_header_empty_headers();
    test_find_request_header_found();
    test_find_request_header_case_insensitive();
    test_find_request_header_not_found();
    test_find_request_header_hash_zero_skipped();
    test_find_request_header_chain_traversal();
    test_find_request_header_null_name();
    test_get_accept_header_null_request();
    test_get_accept_header_fallback();
    test_should_convert_null_conf();
    test_should_convert_null_conf_null_reason();
    test_should_convert_no_accept_header();
    test_should_convert_ffi_convert();
    test_should_convert_ffi_skip();
    test_should_convert_out_reason_null();
    test_markdown_options_init_defaults();
    test_markdown_options_init_null();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
