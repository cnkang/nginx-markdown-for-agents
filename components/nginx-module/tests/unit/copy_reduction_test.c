/*
 * Test: copy_reduction
 *
 * Exercises the production payload linearization path
 * (ngx_http_markdown_decompression_input in ngx_http_markdown_payload_impl.h):
 * a contiguous single-buffer chain must be consumed zero-copy, while a
 * multi-buffer chain must be linearized into one allocation that preserves
 * every byte and the total size.
 *
 * The translation unit compiles the production buffer, decompression, and
 * payload implementation headers directly (same pattern as
 * decompression_production_test.c) against lightweight NGINX API and FFI
 * stubs, so the copy-reduction decision is measured against real code
 * rather than a model copy.
 */

#include "../include/test_common.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <strings.h>
#include <sys/socket.h>
#include <time.h>
#include <zlib.h>

#ifndef MARKDOWN_STREAMING_ENABLED
#define MARKDOWN_STREAMING_ENABLED 1
#endif

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

/*
 * NGX_OK/NGX_ERROR/NGX_AGAIN/NGX_DECLINED come guarded from the shared
 * nginx_stubs/ngx_core.h; only extend the stub surface this TU needs.
 */
#define NGX_LOG_DEBUG       8
#define NGX_LOG_DEBUG_HTTP  NGX_LOG_DEBUG
#define NGX_LOG_INFO        6
#define NGX_LOG_ERR         1
#define NGX_LOG_WARN        2

#define ngx_memzero(buf, n)        memset(buf, 0, n)
#define ngx_memcpy(dst, src, n)    memcpy(dst, src, n)
#define ngx_cpymem(dst, src, n) \
    (((u_char *) memcpy(dst, src, n)) + (n))
#define ngx_strncasecmp(s1, s2, n) \
    strncasecmp((const char *) (s1), (const char *) (s2), (n))

#define ngx_log_debug0(level, log, err, fmt) \
    do { (void) (level); (void) (log); (void) (err); (void) (fmt); } while (0)
#define ngx_log_debug1(level, log, err, fmt, a1) \
    do { (void) (level); (void) (log); (void) (err); (void) (fmt); \
         (void) (a1); } while (0)
#define ngx_log_debug2(level, log, err, fmt, a1, a2) \
    do { (void) (level); (void) (log); (void) (err); (void) (fmt); \
         (void) (a1); (void) (a2); } while (0)
#define ngx_log_debug3(level, log, err, fmt, a1, a2, a3) \
    do { (void) (level); (void) (log); (void) (err); (void) (fmt); \
         (void) (a1); (void) (a2); (void) (a3); } while (0)

typedef struct ngx_pool_cleanup_s ngx_pool_cleanup_t;
struct ngx_pool_cleanup_s {
    ngx_pool_cleanup_t  *next;
    void                *data;
    void              (*handler)(void *data);
};

/*
 * Include the production module header early: it owns the ctx/conf/effective
 * types and every NGX_HTTP_MARKDOWN_* constant the stubs below reference.
 * The stub struct definitions below must stay layout-compatible with what
 * the included production sources access through ngx_http_request_t.
 */
#include "../../src/ngx_http_markdown_filter_module.h"

typedef struct {
    ngx_str_t  key;
    ngx_str_t  value;
    unsigned   hash;
} ngx_table_elt_t;

typedef struct ngx_list_part_s ngx_list_part_t;
struct ngx_list_part_s {
    ngx_table_elt_t  *elts;
    ngx_uint_t        nelts;
    ngx_list_part_t  *next;
};

typedef struct {
    ngx_list_part_t  part;
} ngx_list_t;

typedef struct {
    ngx_table_elt_t  *content_encoding;
    ngx_list_t        headers;
    ngx_str_t         content_type;
    time_t            last_modified_time;
    void             *last_modified;
    off_t             content_length_n;
    ngx_uint_t        status;
} ngx_http_headers_out_t;

struct ngx_log_s {
    int dummy;
};

struct ngx_pool_s {
    ngx_log_t  *log;
};

typedef struct {
    ngx_log_t  *log;
    struct sockaddr *sockaddr;
} ngx_connection_t;

/* struct ngx_buf_s provided by nginx_stubs/ngx_core.h */

struct ngx_chain_s {
    ngx_buf_t    *buf;
    ngx_chain_t  *next;
};

typedef struct {
    ngx_str_t  server;
} ngx_http_headers_in_t;

struct ngx_http_request_s {
    ngx_pool_t               *pool;
    ngx_connection_t         *connection;
    ngx_http_headers_in_t     headers_in;
    ngx_http_headers_out_t    headers_out;
    void                     *loc_conf;
    ngx_uint_t                buffered;
    ngx_uint_t                method;
    struct ngx_http_request_s *main;
};

struct ngx_module_s {
    int dummy;
};

ngx_module_t ngx_http_markdown_filter_module;

/* Pool allocator: plain heap with a test-visible allocation counter. */
static int g_palloc_fail_once;
static size_t g_heap_alloc_count;

void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    void  *p;

    (void) log;
    if (g_palloc_fail_once > 0) {
        g_palloc_fail_once--;
        if (g_palloc_fail_once == 0) {
            return NULL;
        }
    }

    p = malloc(size);
    if (p != NULL) {
        g_heap_alloc_count++;
    }
    return p;
}

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return ngx_alloc(size, NULL);
}

void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return ngx_alloc(size, NULL);
}

void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return calloc(1, size);
}

void
ngx_free(void *p)
{
    free(p);
}

void
ngx_pfree(ngx_pool_t *pool, void *p)
{
    (void) pool;
    free(p);
}

ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    (void) pool;
    return malloc(sizeof(ngx_chain_t));
}

ngx_buf_t *
ngx_calloc_buf(ngx_pool_t *pool)
{
    (void) pool;
    return calloc(1, sizeof(ngx_buf_t));
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

ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    (void) size;
    /* Buffer cleanup handlers are irrelevant to these tests. */
    return NULL;
}

ngx_int_t
ngx_http_get_module_loc_conf_stub_set(ngx_http_request_t *r, void *conf)
{
    r->loc_conf = conf;
    return NGX_OK;
}

void *
ngx_http_get_module_loc_conf(ngx_http_request_t *r, ngx_module_t module)
{
    (void) module;
    return r->loc_conf;
}

void *
ngx_http_get_module_ctx(ngx_http_request_t *r, ngx_module_t module)
{
    (void) r;
    (void) module;
    /* No module context is registered in this TU. */
    return NULL;
}

ngx_int_t
ngx_http_complex_value(ngx_http_request_t *r,
    ngx_http_complex_value_t *val, ngx_str_t *value)
{
    (void) r;
    (void) val;
    (void) value;
    return NGX_OK;
}

/* 2-arg request-header lookup used by decision_log_impl.h. */
static ngx_table_elt_t *
ngx_http_markdown_find_request_header(ngx_http_request_t *r,
    const ngx_str_t *name)
{
    (void) r;
    (void) name;
    return NULL;
}

/*
 * Effective-view helpers: dynconf_impl.h owns the production versions but
 * is too heavy for this TU; mirror the production fallback semantics
 * (prefer eff, fall back to conf).
 */
static ngx_flag_t
ngx_http_markdown_effective_prune_noise(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return (eff != NULL) ? eff->prune_noise : conf->advanced.prune_noise;
}

static size_t
ngx_http_markdown_effective_streaming_budget(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return (eff != NULL) ? eff->streaming_budget : conf->stream.budget;
}

static size_t
ngx_http_markdown_effective_memory_budget(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return (eff != NULL) ? eff->memory_budget : conf->limits.conversion_memory;
}

static ngx_uint_t
ngx_http_markdown_effective_log_verbosity(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return (eff != NULL) ? eff->log_verbosity : conf->policy.log_verbosity;
}

static ngx_flag_t
ngx_http_markdown_effective_enabled(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return (eff != NULL) ? eff->enabled : conf->enabled;
}

static ngx_uint_t
ngx_http_markdown_effective_enabled_source(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return (eff != NULL) ? eff->enabled_source : conf->enabled_source;
}

/*
 * Header helpers declared by filter_module.h; the tests never exercise
 * them, so provide stable no-op implementations.
 */
ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    (void) r;
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r)
{
    (void) r;
    return NGX_OK;
}

void
ngx_http_markdown_restore_conditional_request(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    (void) r;
    (void) ctx;
}

/* Reason-code accessors owned by reason.c in production. */
const ngx_str_t *
ngx_http_markdown_reason_failed_open(void)
{
    static ngx_str_t  reason = ngx_string("failed_open");
    return &reason;
}

const ngx_str_t *
ngx_http_markdown_reason_failed_closed(void)
{
    static ngx_str_t  reason = ngx_string("failed_closed");
    return &reason;
}

const ngx_str_t *
ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, ngx_log_t *log)
{
    (void) log;
    if (category == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT) {
        return ngx_http_markdown_reason_failed_closed();
    }
    return ngx_http_markdown_reason_failed_open();
}

const ngx_str_t *
ngx_http_markdown_reason_header_plan_apply_err(void)
{
    static ngx_str_t  reason = ngx_string("header_plan_apply_error");
    return &reason;
}

/*
 * Compression codec name (owned by config_core_impl.h in production).
 * Payload logs use it only for diagnostics.
 */
static const ngx_str_t *
ngx_http_markdown_compression_name(
    ngx_http_markdown_compression_type_e compression_type)
{
    static ngx_str_t  none = ngx_string("none");
    static ngx_str_t  gzip = ngx_string("gzip");
    static ngx_str_t  deflate = ngx_string("deflate");
    static ngx_str_t  brotli = ngx_string("brotli");
    static ngx_str_t  unknown = ngx_string("unknown");

    switch (compression_type) {
    case NGX_HTTP_MARKDOWN_COMPRESSION_NONE:
        return &none;
    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        return &gzip;
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        return &deflate;
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        return &brotli;
    default:
        return &unknown;
    }
}

/*
 * Metric macros (owned by module_state_impl.h in production): route into a
 * capture sink so the tests could observe increments without the SHM zone.
 */
static volatile int g_metric_inc_sink;

#ifndef NGX_HTTP_MARKDOWN_METRIC_ADD
#define NGX_HTTP_MARKDOWN_METRIC_ADD(field, value) \
    do { \
        (void) &(g_metric_inc_sink); \
        (void) (value); \
    } while (0)
#endif
#ifndef NGX_HTTP_MARKDOWN_METRIC_INC
#define NGX_HTTP_MARKDOWN_METRIC_INC(field) \
    NGX_HTTP_MARKDOWN_METRIC_ADD(field, 1)
#endif
#ifndef NGX_HTTP_MARKDOWN_METRIC_WATERMARK
#define NGX_HTTP_MARKDOWN_METRIC_WATERMARK(field, value) \
    NGX_HTTP_MARKDOWN_METRIC_ADD(field, value)
#endif

/* Decompression counter helpers (owned by module_state_impl.h). */
static void
ngx_http_markdown_record_decompression_success_metrics(
    const ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL) {
        return;
    }
    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.succeeded);
}

static void
ngx_http_markdown_record_decompression_failure_budget(
    ngx_http_markdown_compression_type_e type)
{
    (void) type;
    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total);
}

static void
ngx_http_markdown_record_decompression_failure_format(
    ngx_http_markdown_compression_type_e type)
{
    (void) type;
    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.format_error_total);
}

static void
ngx_http_markdown_record_decompression_failure_truncated(
    ngx_http_markdown_compression_type_e type)
{
    (void) type;
    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.truncated_input_total);
}

static void
ngx_http_markdown_record_decompression_failure_io(
    ngx_http_markdown_compression_type_e type)
{
    (void) type;
    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.io_error_total);
}

/* Fail-open decision counter (owned by request_impl.h in production). */
static void
ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    (void) eff;
    (void) conf;
    NGX_HTTP_MARKDOWN_METRIC_INC(results.failopen_count);
}

/* Decision-log emission (owned by decision_log_impl.h in production). */
static void
ngx_http_markdown_log_decision_with_category(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code, const ngx_str_t *error_category)
{
    (void) r;
    (void) conf;
    (void) eff;
    (void) reason_code;
    (void) error_category;
}

/*
 * Auth-aware header-filter delegation (owned by request_impl.h in
 * production).  The copy-reduction tests never drive the header-forward
 * path, so this stub returns DECLINED without touching the real filter
 * chain.
 */
static ngx_int_t
ngx_http_markdown_next_header_filter_with_auth(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf)
{
    (void) r;
    (void) conf;
    return NGX_DECLINED;
}

/*
 * FFI stubs: the C unit build does not link the Rust library.  These
 * mirror the Rust ABI contracts just enough for the payload path's
 * bounded-decompression wrappers; the copy-reduction tests never reach
 * the decoders themselves.
 */
uint8_t
markdown_parse_encoding_chain(const uint8_t *value, uintptr_t value_len,
    struct FFIEncodingChainResult *result)
{
    (void) value;
    (void) value_len;
    if (result != NULL) {
        ngx_memzero(result, sizeof(*result));
        result->classification = 0;
        result->layer_count = 0;
    }
    return 0;
}

/*
 * Additional stub surface the payload path needs.  The filter typedefs
 * mirror NGINX core (absent from the stub ngx_http.h).
 */
typedef ngx_int_t (*ngx_http_output_header_filter_pt)(ngx_http_request_t *r);
typedef ngx_int_t (*ngx_http_output_body_filter_pt)(ngx_http_request_t *r,
    ngx_chain_t *in);

#ifndef NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT
#define NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT (16 * 1024 * 1024)
#endif
#ifndef NGX_HTTP_GET
#define NGX_HTTP_GET  0x0002
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 0x0004
#endif
#ifndef NGX_LOG_CRIT
#define NGX_LOG_CRIT  3
#endif
#ifndef NGX_CONF_UNSET_UINT
#define NGX_CONF_UNSET_UINT ((ngx_uint_t) -1)
#endif

void
markdown_decomp_result_init(struct FFIDecompResult *result)
{
    if (result != NULL) {
        ngx_memzero(result, sizeof(*result));
    }
}

uint32_t
markdown_decompress_bounded(const uint8_t *input, uintptr_t input_len,
    uint8_t format, uintptr_t budget, uint64_t ratio,
    struct FFIDecompResult *result)
{
    (void) input;
    (void) input_len;
    (void) format;
    (void) budget;
    (void) ratio;
    if (result != NULL) {
        ngx_memzero(result, sizeof(*result));
    }
    /* Unknown error category: callers map it to the I/O failure branch. */
    return 999;
}

void
markdown_decompress_free(struct FFIDecompResult *result)
{
    (void) result;
}

void
markdown_chain_decode_result_init(struct FFIChainDecodeResult *result)
{
    if (result != NULL) {
        ngx_memzero(result, sizeof(*result));
    }
}

uint32_t
markdown_decode_encoding_chain(const uint8_t *input, uintptr_t input_len,
    const uint8_t *layers, uint32_t layer_count, uintptr_t max_output,
    uint64_t ratio, struct FFIChainDecodeResult *result)
{
    (void) input;
    (void) input_len;
    (void) layers;
    (void) layer_count;
    (void) max_output;
    (void) ratio;
    if (result != NULL) {
        ngx_memzero(result, sizeof(*result));
    }
    return 999;
}

void
markdown_chain_decode_free(struct FFIChainDecodeResult *result)
{
    (void) result;
}

/*
 * Include the production module header early: it owns the ctx/conf/effective
 * types and every NGX_HTTP_MARKDOWN_* constant the stubs below reference.
 * The filter_module.h include guard macro is defined afterwards only for
 * sources that re-include it defensively.
 */
#include "../../src/ngx_http_markdown_filter_module.h"

/*
 * Include the production implementations under test.  buffer.c must come
 * first: conversion/payload code resolves its buffer symbols from this
 * single definition, and a second inclusion would redefine every
 * non-static function.
 */
#include "../../src/ngx_http_markdown_buffer.c"
#include "../../src/ngx_http_markdown_decompression.c"
#include "../../src/ngx_http_markdown_decompression_route.h"
#include "../../src/ngx_http_markdown_payload_impl.h"

/* ── Test: contiguous single-buffer skips the linearize copy ────────── */

static void
test_contiguous_single_buffer_skips_copy(void)
{
    ngx_http_request_t   r;
    ngx_connection_t     conn;
    ngx_log_t            log;
    ngx_chain_t          chain;
    ngx_buf_t            buf;
    u_char              *input_buf = NULL;
    size_t               input_size = 0;
    ngx_int_t            rc;
    u_char               data[] = "Hello copy reduction contiguous buffer";

    TEST_SUBSECTION("contiguous single buffer skips linearize copy");

    memset(&r, 0, sizeof(r));
    memset(&conn, 0, sizeof(conn));
    memset(&log, 0, sizeof(log));
    r.connection = &conn;
    r.connection->log = &log;

    memset(&chain, 0, sizeof(chain));
    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + sizeof(data) - 1;
    chain.buf = &buf;
    chain.next = NULL;

    g_heap_alloc_count = 0;

    rc = ngx_http_markdown_decompression_input(
        &r, &chain, &input_buf, &input_size);
    TEST_ASSERT(rc == NGX_OK, "decompression input should return NGX_OK");
    TEST_ASSERT(input_buf == buf.pos,
                "contiguous input must alias the original buffer");
    TEST_ASSERT(input_size == sizeof(data) - 1,
                "input size must match the buffer length");
    TEST_ASSERT(g_heap_alloc_count == 0,
                "contiguous input must not allocate a linearized copy");

    TEST_PASS("contiguous single buffer skips the copy");
}

/* ── Test: multi-buffer chain linearizes into one allocation ────────── */

static void
test_multi_buffer_chain_linearizes(void)
{
    ngx_http_request_t   r;
    ngx_connection_t     conn;
    ngx_log_t            log;
    ngx_chain_t          chain1;
    ngx_chain_t          chain2;
    ngx_chain_t          chain3;
    ngx_buf_t            buf1;
    ngx_buf_t            buf2;
    ngx_buf_t            buf3;
    u_char              *input_buf = NULL;
    size_t               input_size = 0;
    ngx_int_t            rc;
    u_char               part1[] = "Hello ";
    u_char               part2[] = "copy ";
    u_char               part3[] = "reduction!";

    TEST_SUBSECTION("multi-buffer chain linearizes");

    memset(&r, 0, sizeof(r));
    memset(&conn, 0, sizeof(conn));
    memset(&log, 0, sizeof(log));
    r.connection = &conn;
    r.connection->log = &log;

    memset(&chain1, 0, sizeof(chain1));
    memset(&chain2, 0, sizeof(chain2));
    memset(&chain3, 0, sizeof(chain3));
    memset(&buf1, 0, sizeof(buf1));
    memset(&buf2, 0, sizeof(buf2));
    memset(&buf3, 0, sizeof(buf3));
    buf1.pos = part1;
    buf1.last = part1 + sizeof(part1) - 1;
    buf2.pos = part2;
    buf2.last = part2 + sizeof(part2) - 1;
    buf3.pos = part3;
    buf3.last = part3 + sizeof(part3) - 1;
    chain1.buf = &buf1;
    chain1.next = &chain2;
    chain2.buf = &buf2;
    chain2.next = &chain3;
    chain3.buf = &buf3;
    chain3.next = NULL;

    g_heap_alloc_count = 0;

    rc = ngx_http_markdown_decompression_input(
        &r, &chain1, &input_buf, &input_size);
    TEST_ASSERT(rc == NGX_OK, "multi-buffer chain should return NGX_OK");
    TEST_ASSERT(input_size == sizeof(part1) + sizeof(part2) + sizeof(part3) - 3,
                "linearized size must sum every buffer");
    TEST_ASSERT(input_buf != NULL && input_buf != buf1.pos,
                "linearized output must be a fresh allocation");
    TEST_ASSERT(g_heap_alloc_count == 1,
                "linearization allocates exactly one buffer");
    TEST_ASSERT(memcmp(input_buf, "Hello copy reduction!", input_size) == 0,
                "linearized content must preserve every byte");

    free(input_buf);
    TEST_PASS("multi-buffer chain linearizes with correct byte count");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("copy_reduction (production logic) Tests\n");
    printf("========================================\n");

    test_contiguous_single_buffer_skips_copy();
    test_multi_buffer_chain_linearizes();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
