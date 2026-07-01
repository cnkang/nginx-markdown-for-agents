/*
 * Test: conversion_impl_base_url
 */

#include "../include/test_common.h"
#include <ctype.h>
#include <limits.h>
#include <time.h>
#include <sys/socket.h>

#ifndef MARKDOWN_STREAMING_ENABLED
#define MARKDOWN_STREAMING_ENABLED 1
#endif

#include "../../src/ngx_http_markdown_filter_module.h"

/*
 * Stub effective-conf helpers required by conversion_impl.h.
 * These return the live conf value (eff is NULL in these tests).
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
    return (eff != NULL) ? eff->streaming_budget
                         : conf->stream.budget;
}

static size_t
ngx_http_markdown_effective_memory_budget(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return (eff != NULL) ? eff->memory_budget : conf->advanced.memory_budget;
}

/*
 * Local definition of MarkdownOptions matching the Rust FFI ABI layout.
 * This avoids depending on the cbindgen-generated markdown_converter.h
 * which only exists after building the Rust library (not available in
 * the C-only unit test CI job).
 */
struct MarkdownOptions {
    uint32_t       flavor;
    uint32_t       timeout_ms;
    uint8_t        generate_etag;
    uint8_t        estimate_tokens;
    uint8_t        front_matter;
    const uint8_t *content_type;
    uintptr_t      content_type_len;
    const uint8_t *base_url;
    uintptr_t      base_url_len;
    uint64_t       streaming_budget;
    uint32_t       prune_noise;
    const uint8_t *prune_selectors;
    uintptr_t      prune_selector_len;
    const uint8_t *prune_protection_selectors;
    uintptr_t      prune_protection_selector_len;
    uint64_t       memory_budget;
    uint8_t        llm_provider;
    uint8_t        chars_per_token_fixed;
    uint32_t       parse_timeout_ms;
    uint64_t       parser_memory_budget;
    uint32_t       flush_threshold;
};

struct MarkdownResult {
    uint8_t   *markdown;
    uintptr_t  markdown_len;
    uint8_t   *etag;
    uintptr_t  etag_len;
    uint32_t   token_estimate;
    uint32_t   error_code;
    uint8_t   *error_message;
    uintptr_t  error_len;
    uintptr_t  peak_memory_estimate;
};

struct MarkdownConverterHandle;

/*
 * spec 47: local base-URL FFI ABI mirror + capturing stub.
 *
 * The C unit-test build does not link the Rust library, so the trusted-proxy
 * decision (markdown_decide_base_url) is stubbed here.  The stub captures the
 * marshaled FFIBaseUrlInput so tests can assert the thin wrapper marshaled
 * every request/config field faithfully, and writes a test-controlled
 * authority into the caller buffer.  The decision logic itself is covered by
 * the Rust unit tests in forwarded.rs and the FFI tests in ffi/exports.rs.
 */
#ifndef DECIDE_BASE_URL_OK
#define DECIDE_BASE_URL_OK 0
#endif
#ifndef DECIDE_BASE_URL_INVALID
#define DECIDE_BASE_URL_INVALID 1
#endif

struct FFIBaseUrlInput {
    const uint8_t                       *source_ip;
    uintptr_t                            source_ip_len;
    const struct MarkdownTrustedProxies *trusted;
    const uint8_t                       *forwarded;
    uintptr_t                            forwarded_len;
    const uint8_t                       *x_forwarded_proto;
    uintptr_t                            x_forwarded_proto_len;
    const uint8_t                       *x_forwarded_host;
    uintptr_t                            x_forwarded_host_len;
    const uint8_t                       *host;
    uintptr_t                            host_len;
    uint8_t                              is_unix_socket;
    uint8_t                              trusted_configured;
};
typedef struct FFIBaseUrlInput FFIBaseUrlInput;

struct FFIBaseUrlDecision {
    uintptr_t base_url_len;
    uint8_t   reason;
    uint8_t   source;
};
typedef struct FFIBaseUrlDecision FFIBaseUrlDecision;

/* Stub control + capture state for markdown_decide_base_url. */
static struct FFIBaseUrlInput g_captured_base_url_input;
static ngx_uint_t             g_decide_base_url_calls;
static const char            *g_stub_authority = "https://stub.example.com";
static uint8_t                g_stub_decide_rc = DECIDE_BASE_URL_OK;
static uint8_t                g_stub_decide_reason;
static uint8_t                g_stub_decide_source;

static uint8_t
markdown_decide_base_url(const struct FFIBaseUrlInput *input,
    uint8_t *out_buf, uintptr_t out_buf_cap,
    struct FFIBaseUrlDecision *out) /* SONAR_NOTE: must match FFI signature */
{
    size_t  len;

    g_decide_base_url_calls++;

    if (input == NULL || out == NULL || out_buf == NULL || out_buf_cap == 0) {
        return DECIDE_BASE_URL_INVALID;
    }

    g_captured_base_url_input = *input;

    if (g_stub_decide_rc != DECIDE_BASE_URL_OK) {
        return g_stub_decide_rc;
    }

    len = strlen(g_stub_authority);
    if (len > out_buf_cap) {
        return DECIDE_BASE_URL_INVALID;
    }
    memcpy(out_buf, g_stub_authority, len);
    out->base_url_len = len;
    out->reason = g_stub_decide_reason;
    out->source = g_stub_decide_source;
    return DECIDE_BASE_URL_OK;
}

/*
 * Test-controlled stub state.  Each global allows tests to inject
 * specific return codes or trigger one-shot allocation failures
 * without modifying the stub function bodies.
 */
static ngx_int_t g_forward_headers_rc = 0;
static ngx_int_t g_update_headers_rc = 0;
static ngx_int_t g_failopen_rc = 0;
static ngx_uint_t g_failopen_call_count = 0;
static ngx_int_t g_next_body_filter_rc = 0;
static ngx_chain_t *g_next_body_filter_last_input = NULL;
static ngx_uint_t g_next_body_filter_call_count = 0;
static ngx_uint_t g_markdown_result_free_calls = 0;
static ngx_uint_t g_log_decision_calls = 0;
static ngx_uint_t g_reason_streaming_shadow_calls = 0;
static ngx_uint_t g_streaming_new_with_code_calls = 0;
static ngx_uint_t g_streaming_feed_calls = 0;
static ngx_uint_t g_streaming_finish_calls = 0;
static ngx_uint_t g_streaming_free_calls = 0;
static ngx_uint_t g_abort_calls = 0;
static ngx_uint_t g_output_free_calls = 0;
static ngx_uint_t g_pnalloc_fail_once = 0;
static ngx_uint_t g_pcalloc_fail_once = 0;
static ngx_uint_t g_alloc_chain_fail_once = 0;
static uint32_t g_streaming_new_with_code_rc = 0;
static uint32_t g_streaming_feed_rc = 0;
static uint32_t g_streaming_finalize_rc = 0;
static uint32_t g_streaming_new_with_code_null_handle = 0;

/* FFI stub constants and functions used by conversion_impl.h */
#define ERROR_SUCCESS 0
#ifndef ERROR_PARSE
#define ERROR_PARSE 1
#endif
#ifndef ERROR_ENCODING
#define ERROR_ENCODING 2
#endif
#ifndef ERROR_TIMEOUT
#define ERROR_TIMEOUT 3
#endif
#ifndef ERROR_MEMORY_LIMIT
#define ERROR_MEMORY_LIMIT 4
#endif
#ifndef ERROR_INVALID_INPUT
#define ERROR_INVALID_INPUT 5
#endif
#ifndef ERROR_INTERNAL
#define ERROR_INTERNAL 99
#endif
#ifndef ERROR_DECOMPRESSION_BUDGET_EXCEEDED
#define ERROR_DECOMPRESSION_BUDGET_EXCEEDED 9
#endif
#ifndef ERROR_PARSE_TIMEOUT
#define ERROR_PARSE_TIMEOUT 10
#endif
#ifndef ERROR_PARSE_BUDGET_EXCEEDED
#define ERROR_PARSE_BUDGET_EXCEEDED 11
#endif
#ifndef ERROR_DECOMPRESSION_FORMAT_ERROR
#define ERROR_DECOMPRESSION_FORMAT_ERROR 12
#endif
#ifndef ERROR_DECOMPRESSION_TRUNCATED_INPUT
#define ERROR_DECOMPRESSION_TRUNCATED_INPUT 13
#endif
#ifndef ERROR_DECOMPRESSION_IO_ERROR
#define ERROR_DECOMPRESSION_IO_ERROR 14
#endif

static void
markdown_convert(struct MarkdownConverterHandle *handle, /* SONAR_NOTE: must match FFI signature */
    const uint8_t *html, uintptr_t html_len,
    const struct MarkdownOptions *options,
    struct MarkdownResult *result)
{
    UNUSED(handle);
    UNUSED(html);
    UNUSED(html_len);
    UNUSED(options);
    memset(result, 0, sizeof(*result));
}

/*
 * FFI lifecycle stub for markdown_result_free.  Clears all public ABI
 * fields (pointer, length, and numeric/error fields) to prevent
 * stale-state regressions, and increments g_markdown_result_free_calls
 * so tests can verify the expected number of free invocations.
 *
 * Per AGENTS.md rule 15: partial clears create false confidence and
 * can mask stale-state regressions, so every field is zeroed.
 */
static void
markdown_result_free(struct MarkdownResult *result) /* SONAR_NOTE: must match FFI signature */
{
    g_markdown_result_free_calls++;
    if (result != NULL) {
        result->markdown = NULL;
        result->etag = NULL;
        result->error_message = NULL;
        result->markdown_len = 0;
        result->etag_len = 0;
        result->token_estimate = 0;
        result->error_code = 0;
        result->error_len = 0;
        result->peak_memory_estimate = 0;
    }
}

/*
 * FFI lifecycle stub for markdown_options_init.  Mirrors the Rust
 * implementation: zeroes all fields, then sets non-zero defaults
 * (timeout_ms=5000, generate_etag=1).  The production code must
 * call this instead of ngx_memzero to honour the FFI contract.
 */
static void
markdown_options_init(struct MarkdownOptions *result)
{
    if (result == NULL) {
        return;
    }
    memset(result, 0, sizeof(*result));
    result->timeout_ms = 5000;
    result->generate_etag = 1;
}

/*
 * FFI lifecycle stub for markdown_result_init.  Zeroes all fields
 * to guarantee a clean baseline before FFI calls populate the struct.
 */
static void
markdown_result_init(struct MarkdownResult *result)
{
    if (result == NULL) {
        return;
    }
    memset(result, 0, sizeof(*result));
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_shadow(void)
{
    static ngx_str_t  reason = {
        sizeof("STREAMING_SHADOW") - 1,
        (u_char *) "STREAMING_SHADOW"
    };

    g_reason_streaming_shadow_calls++;
    return &reason;
}

void
ngx_http_markdown_log_decision(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(eff);
    UNUSED(reason_code);
    g_log_decision_calls++;
}

void
markdown_streaming_abort(struct StreamingConverterHandle *handle)
{
    UNUSED(handle);
    g_abort_calls++;
}

void
markdown_streaming_output_free(uint8_t *data, uintptr_t len)
{
    UNUSED(data);
    UNUSED(len);
    g_output_free_calls++;
}

uint32_t
markdown_streaming_new_with_code(const struct MarkdownOptions *options,
    struct StreamingConverterHandle **out_handle)
{
    UNUSED(options);
    g_streaming_new_with_code_calls++;
    if (out_handle != NULL && !g_streaming_new_with_code_null_handle) {
        *out_handle = (struct StreamingConverterHandle *) (uintptr_t) 0x1;
    }
    return g_streaming_new_with_code_rc;
}

uint32_t
markdown_streaming_feed(struct StreamingConverterHandle *handle,
    const uint8_t *html, uintptr_t html_len,
    uint8_t **out_data, uintptr_t *out_len)
{
    UNUSED(handle);
    UNUSED(html);
    UNUSED(html_len);
    UNUSED(out_data);
    UNUSED(out_len);
    g_streaming_feed_calls++;
    return g_streaming_feed_rc;
}

uint32_t
markdown_streaming_finalize(struct StreamingConverterHandle *handle,
    struct MarkdownResult *result)
{
    UNUSED(handle);
    UNUSED(result);
    g_streaming_finish_calls++;
    return g_streaming_finalize_rc;
}

void
markdown_streaming_free(struct StreamingConverterHandle *handle)
{
    UNUSED(handle);
    g_streaming_free_calls++;
}

typedef struct ngx_list_part_s ngx_list_part_t;
typedef struct ngx_table_elt_s ngx_table_elt_t;
typedef struct ngx_http_headers_in_s ngx_http_headers_in_t;
typedef struct ngx_http_headers_out_s ngx_http_headers_out_t;
typedef struct ngx_http_core_srv_conf_s ngx_http_core_srv_conf_t;
typedef struct ngx_connection_s ngx_connection_t;
typedef struct ngx_log_s ngx_log_t;
typedef struct ngx_pool_s ngx_pool_t;
typedef ngx_uint_t ngx_atomic_uint_t;
typedef struct ngx_time_s ngx_time_t;

struct ngx_list_part_s {
    void           *elts;
    ngx_uint_t      nelts;
    ngx_list_part_t *next;
};

struct ngx_table_elt_s {
    ngx_str_t key;
    ngx_str_t value;
    ngx_uint_t hash;
};

typedef struct {
    ngx_list_part_t part;
} ngx_list_t;

struct ngx_log_s {
    int dummy;
};

struct ngx_connection_s {
    ngx_log_t       *log;
    struct sockaddr *sockaddr;
    ngx_str_t        addr_text;
};

struct ngx_pool_s {
    int dummy;
};

/* struct ngx_buf_s provided by nginx_stubs/ngx_core.h */

struct ngx_chain_s {
    ngx_buf_t *buf;
    struct ngx_chain_s *next;
};

struct ngx_time_s {
    time_t sec;
    ngx_msec_t msec;
};

struct ngx_http_headers_in_s {
    ngx_list_t headers;
    ngx_str_t  server;
};

struct ngx_http_headers_out_s {
    ngx_str_t content_type;
    ngx_msec_t last_modified_time;
};

struct ngx_http_core_srv_conf_s {
    ngx_str_t server_name;
};

struct ngx_http_request_s {
    ngx_connection_t *connection;
    ngx_pool_t       *pool;
    ngx_uint_t        method;
    ngx_str_t         schema;
    ngx_http_headers_in_t headers_in;
    ngx_http_headers_out_t headers_out;
    ngx_str_t         uri;
    ngx_uint_t        buffered;
    struct ngx_http_request_s *main;
    void             *main_conf;
    void             *loc_conf;
    void             *srv_conf;
};

#ifndef ngx_memzero
#define ngx_memzero(buf, n) memset((buf), 0, (n))
#endif
#ifndef ngx_memcpy
#define ngx_memcpy memcpy
#endif
#ifndef NGX_OK
#define NGX_OK 0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR (-1)
#endif
#ifndef NGX_DONE
#define NGX_DONE (-4)
#endif
#ifndef NGX_AGAIN
#define NGX_AGAIN (-2)
#endif
#ifndef NGX_DECLINED
#define NGX_DECLINED (-5)
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 4
#endif
#ifndef NGX_HTTP_NOT_MODIFIED
#define NGX_HTTP_NOT_MODIFIED 304
#endif
#ifndef NGX_HTTP_MARKDOWN_BUFFERED
#define NGX_HTTP_MARKDOWN_BUFFERED 0x08
#endif
#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif
#ifndef NGX_LOG_CRIT
#define NGX_LOG_CRIT 1
#endif
static volatile int g_metric_inc_sink;
static volatile int g_metric_add_sink;
#ifndef NGX_HTTP_MARKDOWN_METRIC_ADD
#define NGX_HTTP_MARKDOWN_METRIC_ADD(name, value)                                     \
    do {                                                                              \
        g_metric_add_sink = 1;                                                       \
        UNUSED(value);                                                                \
    } while (0)
#endif
#ifndef NGX_HTTP_MARKDOWN_METRIC_INC
#define NGX_HTTP_MARKDOWN_METRIC_INC(name) (g_metric_inc_sink = 1)
#endif
#ifndef ngx_log_debug2
#define ngx_log_debug2(level, log, err, fmt, arg1, arg2) \
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); UNUSED(arg1); UNUSED(arg2)
#endif
#ifndef ngx_log_debug3
#define ngx_log_debug3(level, log, err, fmt, arg1, arg2, arg3) \
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); UNUSED(arg1); UNUSED(arg2); UNUSED(arg3)
#endif
#ifndef ngx_log_debug0
#define ngx_log_debug0(level, log, err, fmt) UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt)
#endif
#ifndef ngx_log_debug1
#define ngx_log_debug1(level, log, err, fmt, arg) \
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); UNUSED(arg)
#endif
#ifndef ngx_http_get_module_loc_conf
#define ngx_http_get_module_loc_conf(r, module) \
    ((ngx_http_markdown_conf_t *) ((r)->loc_conf))
#endif
#ifndef ngx_http_get_module_srv_conf
#define ngx_http_get_module_srv_conf(r, module) \
    ((ngx_http_core_srv_conf_t *) ((r)->srv_conf))
#endif
#ifndef ngx_http_get_module_main_conf
#define ngx_http_get_module_main_conf(r, module) \
    ((ngx_http_markdown_main_conf_t *) ((r)->main_conf))
#endif
#ifndef ngx_tolower
#define ngx_tolower(c) ((u_char) tolower((unsigned char) (c)))
#endif

static ngx_inline u_char *
ngx_cpymem(u_char *dst, const void *src, size_t n)
{
    return (u_char *) memcpy(dst, src, n) + n;
}

static ngx_inline ngx_int_t
ngx_pfree(ngx_pool_t *pool, void *p)
{
    (void) pool;
    free(p);
    return NGX_OK;
}

/*
 * Pool allocator stub delegating to malloc(3).  When g_pnalloc_fail_once
 * is set, returns NULL once and clears the flag, simulating allocation
 * failure.
 */
static ngx_inline void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    if (g_pnalloc_fail_once) {
        g_pnalloc_fail_once = 0;
        return NULL;
    }
    return malloc(size);
}

/*
 * Pool allocator stub delegating to calloc(3) with zero-initialization.
 * When g_pcalloc_fail_once is set, returns NULL once and clears the flag,
 * simulating allocation failure.
 */
static ngx_inline void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    void *p;

    (void) pool;
    if (g_pcalloc_fail_once) {
        g_pcalloc_fail_once = 0;
        return NULL;
    }
    p = calloc(1, size);
    return p;
}

/*
 * Chain link allocator stub.  When g_alloc_chain_fail_once is set,
 * returns NULL once and clears the flag, simulating allocation failure.
 */
static ngx_inline ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    (void) pool;
    if (g_alloc_chain_fail_once) {
        g_alloc_chain_fail_once = 0;
        return NULL;
    }
    return calloc(1, sizeof(ngx_chain_t));
}

static ngx_inline ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        u_char c1;
        u_char c2;

        c1 = (u_char) tolower((unsigned char) s1[i]);
        c2 = (u_char) tolower((unsigned char) s2[i]);
        if (c1 != c2) {
            return (ngx_int_t) c1 - (ngx_int_t) c2;
        }
    }

    return 0;
}

#ifndef ngx_timeofday
static ngx_inline const ngx_time_t *
ngx_timeofday_stub(void)
{
    static ngx_time_t now;

    now.sec = time(NULL);
    now.msec = 0;

    return &now;
}
#define ngx_timeofday() ngx_timeofday_stub()
#endif

/*
 * Stub definitions for external symbols referenced by conversion_impl.h
 * but not exercised by the base_url / prepare_options tests.
 * These must be defined before the #include of conversion_impl.h
 * because the impl header contains static forward declarations that
 * the linker resolves (GCC on Linux does not strip unused statics).
 */

u_char ngx_http_markdown_empty_string[] = "";
struct MarkdownConverterHandle *ngx_http_markdown_converter = NULL;
ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;
ngx_int_t (*ngx_http_next_body_filter)(ngx_http_request_t *r, ngx_chain_t *in) = NULL;

#ifndef NGX_CONF_UNSET_SIZE
#define NGX_CONF_UNSET_SIZE ((size_t) -1)
#endif

typedef struct {
    int dummy;
} ngx_shmtx_t;

struct ngx_slab_pool_s {
    ngx_shmtx_t   mutex;
};
typedef struct ngx_slab_pool_s ngx_slab_pool_t;

struct ngx_shm_zone_s {
    void          *data;
    struct {
        void      *addr;
    } shm;
};

ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;

#ifndef ngx_atomic_fetch_add
#define ngx_atomic_fetch_add(p, v)                                           \
    ({                                                                        \
        *(p) += (v);                                                          \
        *(p);                                                                 \
    })
#endif

static ngx_inline void
ngx_shmtx_lock(ngx_shmtx_t *mtx)
{
    UNUSED(mtx);
}

static ngx_inline void
ngx_shmtx_unlock(ngx_shmtx_t *mtx)
{
    UNUSED(mtx);
}

static ngx_inline ngx_uint_t
ngx_hash_key(u_char *data, size_t len)
{
    ngx_uint_t  hash;
    hash = 0;
    for (size_t i = 0; i < len; i++) {
        hash = hash * 31 + data[i];
    }
    return hash;
}

static ngx_inline void *
ngx_slab_alloc_locked(ngx_slab_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

static ngx_inline void
ngx_slab_free_locked(ngx_slab_pool_t *pool, void *p)
{
    UNUSED(pool);
    free(p);
}

static ngx_inline void
ngx_rbtree_insert(ngx_rbtree_t *tree, ngx_rbtree_node_t *node)
{
    UNUSED(tree);
    UNUSED(node);
}

/*
 * Forward-headers stub returning the test-controlled g_forward_headers_rc,
 * allowing tests to simulate header forwarding failures.
 */
static ngx_int_t
ngx_http_markdown_forward_headers(
    ngx_http_request_t *r,     /* SONAR_NOTE c:S995 — must match production signature */
    ngx_http_markdown_ctx_t *ctx)  /* SONAR_NOTE c:S995 — must match production signature */
{
    UNUSED(r);
    UNUSED(ctx);
    return g_forward_headers_rc;
}

__attribute__((unused))
static void
ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(conf);
}

/*
 * Fail-open stub.  Increments g_failopen_call_count and returns
 * g_failopen_rc, allowing tests to verify invocation count and control
 * return behavior.
 */
static ngx_int_t
ngx_http_markdown_reject_or_fail_open_buffered_response(
    ngx_http_request_t *r,     /* SONAR_NOTE c:S995 — must match impl forward decl */
    ngx_http_markdown_ctx_t *ctx,  /* SONAR_NOTE c:S995 — must match impl forward decl */
    const ngx_http_markdown_conf_t *conf, const char *debug_message)
{
    UNUSED(r);
    UNUSED(ctx);
    UNUSED(conf);
    UNUSED(debug_message);
    g_failopen_call_count++;
    return g_failopen_rc;
}

/*
 * Classify FFI error codes into semantic categories.  PARSE/ENCODING/
 * INVALID_INPUT map to CONVERSION, TIMEOUT/MEMORY_LIMIT map to
 * RESOURCE_LIMIT, all others map to SYSTEM.  Mirrors the production
 * classification contract.
 */
ngx_http_markdown_error_category_t
ngx_http_markdown_classify_error(uint32_t error_code)
{
    switch (error_code) {
        case ERROR_PARSE:
        case ERROR_ENCODING:
        case ERROR_INVALID_INPUT:
            return NGX_HTTP_MARKDOWN_ERROR_CONVERSION;
        case ERROR_TIMEOUT:
        case ERROR_MEMORY_LIMIT:
            return NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT;
        default:
            return NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    }
}

/*
 * Return a human-readable string for each error category.  Covers
 * CONVERSION, RESOURCE_LIMIT, and SYSTEM categories.
 */
const ngx_str_t *
ngx_http_markdown_error_category_string(
    ngx_http_markdown_error_category_t category)
{
    static u_char conversion_str_data[] = "conversion_error";
    static u_char resource_str_data[] = "memory_budget_exceeded";
    static u_char system_str_data[] = "ffi_panic";
    static ngx_str_t conversion_str = {
        sizeof("conversion_error") - 1, conversion_str_data
    };
    static ngx_str_t resource_str = {
        sizeof("memory_budget_exceeded") - 1, resource_str_data
    };
    static ngx_str_t system_str = {
        sizeof("ffi_panic") - 1, system_str_data
    };
    if (category == NGX_HTTP_MARKDOWN_ERROR_CONVERSION) {
        return &conversion_str;
    }
    if (category == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT) {
        return &resource_str;
    }
    return &system_str;
}

/*
 * Header-update stub returning the test-controlled g_update_headers_rc,
 * allowing tests to simulate header update failures.
 */
ngx_int_t
ngx_http_markdown_update_headers(
    ngx_http_request_t *r,     /* SONAR_NOTE c:S995 — must match module header decl */
    const struct MarkdownResult *result,
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(r);
    UNUSED(result);
    UNUSED(conf);
    return g_update_headers_rc;
}

ngx_int_t
ngx_http_markdown_handle_if_none_match(
    ngx_http_request_t *r,     /* SONAR_NOTE c:S995 — must match module header decl */
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_ctx_t *ctx,
    struct MarkdownConverterHandle *converter,  /* SONAR_NOTE c:S995 — must match module header decl */
    struct MarkdownResult **result)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(ctx);
    UNUSED(converter);
    UNUSED(result);
    return NGX_DECLINED;
}

ngx_int_t
ngx_http_markdown_send_304(
    ngx_http_request_t *r,     /* SONAR_NOTE c:S995 — must match module header decl */
    const struct MarkdownResult *result)
{
    UNUSED(r);
    UNUSED(result);
    return NGX_OK;
}

static ngx_inline ngx_http_markdown_otel_span_t *
ngx_http_markdown_otel_span_start(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(r);
    UNUSED(conf);
    return NULL;
}

static ngx_inline void
ngx_http_markdown_otel_set_str_attr(ngx_http_markdown_otel_span_t *span,
    const u_char *key, size_t key_len,
    const u_char *val, size_t val_len)
{
    UNUSED(span);
    UNUSED(key);
    UNUSED(key_len);
    UNUSED(val);
    UNUSED(val_len);
}

static ngx_inline void
ngx_http_markdown_otel_set_int_attr(ngx_http_markdown_otel_span_t *span,
    const u_char *key, size_t key_len,
    int64_t val)
{
    UNUSED(span);
    UNUSED(key);
    UNUSED(key_len);
    UNUSED(val);
}

static ngx_inline void
ngx_http_markdown_otel_span_end(ngx_http_markdown_otel_span_t *span)
{
    UNUSED(span);
}

static ngx_inline void
ngx_http_markdown_otel_span_export(ngx_http_markdown_otel_span_t *span,
    ngx_log_t *log, ngx_http_request_t *r)
{
    UNUSED(span);
    UNUSED(log);
    UNUSED(r);
}

#include "../../src/ngx_http_markdown_conversion_impl.h" /* SONAR_NOTE: must follow stub definitions */

static ngx_connection_t g_connection = { 0 };
static ngx_log_t g_log = { 0 };
static ngx_pool_t g_pool = { 0 };

static void
set_str(ngx_str_t *dst, const char *src)
{
    dst->data = (u_char *) (uintptr_t) src;
    dst->len = strlen(src);
}

static void
init_request(ngx_http_request_t *r)
{
    memset(r, 0, sizeof(*r));
    r->connection = &g_connection;
    r->connection->log = &g_log;
    r->pool = &g_pool;
    r->main = r;
}

static void
set_single_header_list(ngx_http_request_t *r, ngx_table_elt_t *headers,
    ngx_uint_t count)
{
    r->headers_in.headers.part.elts = headers;
    r->headers_in.headers.part.nelts = count;
    r->headers_in.headers.part.next = NULL;
}

static void
assert_str_eq(const ngx_str_t *actual, const char *expected, const char *msg)
{
    size_t expected_len;

    expected_len = strlen(expected);
    TEST_ASSERT(actual->len == expected_len, msg);
    TEST_ASSERT(memcmp(actual->data, expected, expected_len) == 0, msg);
}

/*
 * Body filter chain stub returning the test-controlled
 * g_next_body_filter_rc.
 */
static ngx_int_t
test_next_body_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    UNUSED(r);
    g_next_body_filter_last_input = in;
    g_next_body_filter_call_count++;
    return g_next_body_filter_rc;
}

/*
 * Reset all stub state to deterministic defaults for each test section.
 *
 * Inputs: none.
 * Output: none.
 * Side effects:
 * - restores default return values for forwarding/update/fail-open stubs
 * - clears one-shot allocation failure injectors and call counters
 * - reinstalls ngx_http_next_body_filter to the local test stub
 */
static void
reset_stub_state(void)
{
    g_forward_headers_rc = NGX_OK;
    g_update_headers_rc = NGX_OK;
    g_failopen_rc = NGX_OK;
    g_failopen_call_count = 0;
    g_next_body_filter_rc = NGX_OK;
    g_next_body_filter_last_input = NULL;
    g_next_body_filter_call_count = 0;
    g_markdown_result_free_calls = 0;
    g_log_decision_calls = 0;
    g_reason_streaming_shadow_calls = 0;
    g_streaming_new_with_code_calls = 0;
    g_streaming_feed_calls = 0;
    g_streaming_finish_calls = 0;
    g_streaming_free_calls = 0;
    g_abort_calls = 0;
    g_output_free_calls = 0;
    g_pnalloc_fail_once = 0;
    g_pcalloc_fail_once = 0;
    g_alloc_chain_fail_once = 0;
    g_streaming_new_with_code_rc = ERROR_SUCCESS;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_new_with_code_null_handle = 0;
    ngx_http_next_body_filter = test_next_body_filter;
}

/*
 * Reset the base-URL decision stub to deterministic defaults.
 */
static void
reset_base_url_stub(void)
{
    memset(&g_captured_base_url_input, 0, sizeof(g_captured_base_url_input));
    g_decide_base_url_calls = 0;
    g_stub_authority = "https://stub.example.com";
    g_stub_decide_rc = DECIDE_BASE_URL_OK;
    g_stub_decide_reason = 0;
    g_stub_decide_source = 0;
}

/*
 * Test: the thin wrapper marshals every request/config field into the FFI
 * input and appends the request URI to the FFI-produced authority.
 *
 * Validates spec 47 Requirement 10.4 (C is a thin wrapper that only
 * marshals) and Requirement 4.2 (C-side responsibilities).
 */
static void
test_base_url_marshals_request_fields(void)
{
    ngx_http_request_t            r;
    ngx_http_markdown_conf_t      conf;
    ngx_http_markdown_main_conf_t main_conf;
    ngx_table_elt_t               headers[3];
    ngx_str_t                     base_url;
    struct MarkdownTrustedProxies *handle;

    TEST_SUBSECTION("base_url thin wrapper marshals request/config fields");

    reset_base_url_stub();
    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    memset(&main_conf, 0, sizeof(main_conf));

    /* Non-NULL sentinel handle: the wrapper must pass it through verbatim. */
    handle = (struct MarkdownTrustedProxies *) (uintptr_t) 0x1234;
    main_conf.trusted_proxies = handle;
    main_conf.trusted_proxies_configured = 1;

    set_str(&r.connection->addr_text, "10.1.2.3");
    set_str(&r.uri, "/articles/page.html");
    set_str(&r.headers_in.server, "origin.example.com");

    set_str(&headers[0].key, "Forwarded");
    set_str(&headers[0].value, "host=fwd.example.com;proto=https");
    headers[0].hash = 1;
    set_str(&headers[1].key, "X-Forwarded-Proto");
    set_str(&headers[1].value, "https");
    headers[1].hash = 1;
    set_str(&headers[2].key, "X-Forwarded-Host");
    set_str(&headers[2].value, "xfwd.example.com");
    headers[2].hash = 1;
    set_single_header_list(&r, headers, ARRAY_SIZE(headers));

    r.loc_conf = &conf;
    r.main_conf = (void *) &main_conf;

    TEST_ASSERT(ngx_http_markdown_construct_base_url(&r, r.pool, &base_url)
                    == NGX_OK,
                "construct_base_url should succeed via the FFI decision");
    TEST_ASSERT(g_decide_base_url_calls == 1,
                "wrapper should call markdown_decide_base_url exactly once");

    /* Authority from the stub + the request URI. */
    assert_str_eq(&base_url, "https://stub.example.com/articles/page.html",
                  "base_url should be authority + request URI");
    free(base_url.data);

    /* Marshaling assertions: every field reached the FFI input. */
    TEST_ASSERT(g_captured_base_url_input.source_ip_len == 8
        && memcmp(g_captured_base_url_input.source_ip, "10.1.2.3", 8) == 0,
        "source IP must be marshaled from connection addr_text");
    TEST_ASSERT(g_captured_base_url_input.trusted == handle,
        "trusted-proxy handle must be passed through from main conf");
    TEST_ASSERT(g_captured_base_url_input.trusted_configured == 1,
        "trusted_configured must reflect main conf");
    TEST_ASSERT(g_captured_base_url_input.is_unix_socket == 0,
        "is_unix_socket must be 0 for a non-unix peer");
    TEST_ASSERT(g_captured_base_url_input.forwarded_len
            == sizeof("host=fwd.example.com;proto=https") - 1,
        "Forwarded header must be marshaled");
    TEST_ASSERT(g_captured_base_url_input.x_forwarded_proto_len == 5,
        "X-Forwarded-Proto must be marshaled");
    TEST_ASSERT(g_captured_base_url_input.x_forwarded_host_len
            == sizeof("xfwd.example.com") - 1,
        "X-Forwarded-Host must be marshaled");
    TEST_ASSERT(g_captured_base_url_input.host_len
            == sizeof("origin.example.com") - 1,
        "Host must be marshaled from headers_in.server");

    TEST_PASS("base_url wrapper marshals all request/config fields");
}

/*
 * Test: a Unix-domain socket peer is marshaled as is_unix_socket = 1.
 *
 * Validates spec 47 Requirement 2.1 (Unix socket handling).
 */
static void
test_base_url_unix_socket_flag(void)
{
    ngx_http_request_t            r;
    ngx_http_markdown_conf_t      conf;
    ngx_http_markdown_main_conf_t main_conf;
    struct sockaddr               unix_sockaddr;
    ngx_str_t                     base_url;

    TEST_SUBSECTION("base_url wrapper flags Unix-socket peers");

    reset_base_url_stub();
    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    memset(&main_conf, 0, sizeof(main_conf));
    memset(&unix_sockaddr, 0, sizeof(unix_sockaddr));
    unix_sockaddr.sa_family = AF_UNIX;

    r.connection->sockaddr = &unix_sockaddr;
    set_str(&r.connection->addr_text, "unix:");
    set_str(&r.uri, "/x");
    r.loc_conf = &conf;
    r.main_conf = (void *) &main_conf;

    TEST_ASSERT(ngx_http_markdown_construct_base_url(&r, r.pool, &base_url)
                    == NGX_OK,
                "construct_base_url should succeed for a unix peer");
    free(base_url.data);

    TEST_ASSERT(g_captured_base_url_input.is_unix_socket == 1,
        "AF_UNIX peer must set is_unix_socket");

    /* Restore the shared connection for later tests. */
    r.connection->sockaddr = NULL;

    TEST_PASS("Unix-socket peers are flagged for the decision");
}

/*
 * Test: when markdown_trusted_proxies is absent, trusted_configured is 0.
 *
 * Validates spec 47 Requirement 1.2 / reason-code selection input.
 */
static void
test_base_url_not_configured_marshaled(void)
{
    ngx_http_request_t            r;
    ngx_http_markdown_conf_t      conf;
    ngx_http_markdown_main_conf_t main_conf;
    ngx_str_t                     base_url;

    TEST_SUBSECTION("base_url wrapper marshals unconfigured trust state");

    reset_base_url_stub();
    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    memset(&main_conf, 0, sizeof(main_conf));
    /* trusted_proxies_configured left 0; trusted_proxies left NULL. */

    set_str(&r.connection->addr_text, "203.0.113.7");
    set_str(&r.uri, "/p");
    r.loc_conf = &conf;
    r.main_conf = (void *) &main_conf;

    TEST_ASSERT(ngx_http_markdown_construct_base_url(&r, r.pool, &base_url)
                    == NGX_OK,
                "construct_base_url should succeed when trust is unset");
    free(base_url.data);

    TEST_ASSERT(g_captured_base_url_input.trusted_configured == 0,
        "trusted_configured must be 0 when directive is absent");
    TEST_ASSERT(g_captured_base_url_input.trusted == NULL,
        "trusted handle must be NULL when directive is absent");

    TEST_PASS("Unconfigured trust state is marshaled faithfully");
}

/*
 * Test: an FFI decision failure propagates as NGX_ERROR without allocating.
 */
static void
test_base_url_decision_failure_propagates(void)
{
    ngx_http_request_t            r;
    ngx_http_markdown_conf_t      conf;
    ngx_http_markdown_main_conf_t main_conf;
    ngx_str_t                     base_url;

    TEST_SUBSECTION("base_url wrapper propagates FFI decision failure");

    reset_base_url_stub();
    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    memset(&main_conf, 0, sizeof(main_conf));
    g_stub_decide_rc = DECIDE_BASE_URL_INVALID;

    set_str(&r.connection->addr_text, "10.0.0.1");
    set_str(&r.uri, "/p");
    r.loc_conf = &conf;
    r.main_conf = (void *) &main_conf;

    TEST_ASSERT(ngx_http_markdown_construct_base_url(&r, r.pool, &base_url)
                    == NGX_ERROR,
                "construct_base_url should fail when the FFI rejects inputs");
    TEST_ASSERT(base_url.data == NULL,
                "failed construction should not allocate output");

    /* Restore the stub so subsequent tests using construct_base_url pass. */
    g_stub_decide_rc = DECIDE_BASE_URL_OK;

    TEST_PASS("FFI decision failure propagates cleanly");
}

static void
test_base_url_add_len_overflow_guard(void)
{
    ngx_http_request_t r;
    size_t total;

    TEST_SUBSECTION("Overflow guard rejects wrapped base_url length");

    init_request(&r);

    total = SIZE_MAX - 2;
    TEST_ASSERT(ngx_http_markdown_base_url_add_len(&r, &total, 3, "scheme") == NGX_ERROR,
                "overflow guard should reject wrapped addition");
    TEST_ASSERT(total == SIZE_MAX - 2, "failed addition must not change the accumulator");

    TEST_PASS("Overflow guard is correct");
}


/*
 * Test: prepare_conversion_options populates all fields correctly.
 */
static void
test_prepare_conversion_options_basic(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: basic field population");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));

    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "example.com");
    set_str(&r.uri, "/page.html");
    set_str(&r.headers_out.content_type, "text/html; charset=utf-8");
    r.loc_conf = &conf;

    conf.flavor = 0;  /* CommonMark */
    conf.timeout = 5000;
    conf.policy.generate_etag = 1;
    conf.token_estimate = 1;
    conf.front_matter = 1;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, NULL, &options) == NGX_OK,
                "prepare_conversion_options should succeed");
    TEST_ASSERT(options.flavor == 0, "flavor should be CommonMark (0)");
    TEST_ASSERT(options.timeout_ms == 5000, "timeout should be 5000");
    TEST_ASSERT(options.generate_etag == 1, "generate_etag should be 1");
    TEST_ASSERT(options.estimate_tokens == 1, "estimate_tokens should be 1");
    TEST_ASSERT(options.front_matter == 1, "front_matter should be 1");
    TEST_ASSERT(options.content_type != NULL, "content_type should be set");
    TEST_ASSERT(options.content_type_len > 0, "content_type_len should be > 0");
    TEST_ASSERT(options.base_url != NULL, "base_url should be constructed");
    TEST_ASSERT(options.base_url_len > 0, "base_url_len should be > 0");

    /* Clean up allocated base_url */
    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options basic fields correct");
}


/*
 * Test: prepare_conversion_options with GFM flavor.
 */
static void
test_prepare_conversion_options_gfm(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: GFM flavor");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));

    set_str(&r.schema, "https");
    set_str(&r.headers_in.server, "gfm.example.com");
    set_str(&r.uri, "/doc.html");
    set_str(&r.headers_out.content_type, "text/html");
    r.loc_conf = &conf;

    conf.flavor = 1;  /* GFM */
    conf.timeout = 3000;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, NULL, &options) == NGX_OK,
                "prepare_conversion_options should succeed for GFM");
    TEST_ASSERT(options.flavor == 1, "flavor should be GFM (1)");
    TEST_ASSERT(options.timeout_ms == 3000, "timeout should be 3000");

    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options GFM flavor correct");
}


/*
 * Test: prepare_conversion_options without content_type.
 */
static void
test_prepare_conversion_options_no_content_type(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: no content_type");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));

    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "example.com");
    set_str(&r.uri, "/page.html");
    r.headers_out.content_type.len = 0;
    r.headers_out.content_type.data = NULL;
    r.loc_conf = &conf;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, NULL, &options) == NGX_OK,
                "prepare_conversion_options should succeed without content_type");
    TEST_ASSERT(options.content_type == NULL, "content_type should be NULL");
    TEST_ASSERT(options.content_type_len == 0, "content_type_len should be 0");

    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options no content_type correct");
}


/*
 * Test: prepare_conversion_options with failed base_url construction.
 */
static void
test_prepare_conversion_options_no_base_url(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_http_core_srv_conf_t cscf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: no base_url");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    memset(&cscf, 0, sizeof(cscf));

    /* Empty schema/server/server_name → base_url construction fails */
    set_str(&r.schema, "");
    set_str(&r.headers_in.server, "");
    set_str(&r.uri, "/page.html");
    set_str(&cscf.server_name, "");
    r.loc_conf = &conf;
    r.srv_conf = &cscf;

    /* Drive the trusted-proxy decision to fail so base_url stays unset. */
    g_stub_decide_rc = DECIDE_BASE_URL_INVALID;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, NULL, &options) == NGX_OK,
                "prepare_conversion_options should succeed even without base_url");
    TEST_ASSERT(options.base_url == NULL, "base_url should be NULL on failure");
    TEST_ASSERT(options.base_url_len == 0, "base_url_len should be 0 on failure");

    g_stub_decide_rc = DECIDE_BASE_URL_OK;

    TEST_PASS("prepare_conversion_options no base_url correct");
}


/*
 * Test: prepare_conversion_options with flavor exceeding uint32 max (clamping).
 */
static void
test_prepare_conversion_options_flavor_clamp(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: flavor overflow clamping");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "example.com");
    set_str(&r.uri, "/page.html");
    set_str(&r.headers_out.content_type, "text/html");
    r.loc_conf = &conf;

    conf.flavor = (ngx_uint_t) UINT32_MAX + 1;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, NULL, &options) == NGX_OK,
                "prepare_conversion_options should succeed with clamped flavor");
    TEST_ASSERT(options.flavor == UINT32_MAX, "flavor should be clamped to UINT32_MAX");

    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options flavor clamping correct");
}


/*
 * Test: prepare_conversion_options with timeout exceeding uint32 max (clamping).
 */
static void
test_prepare_conversion_options_timeout_clamp(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: timeout overflow clamping");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "example.com");
    set_str(&r.uri, "/page.html");
    set_str(&r.headers_out.content_type, "text/html");
    r.loc_conf = &conf;

    conf.timeout = (ngx_msec_t) UINT32_MAX + 1;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, NULL, &options) == NGX_OK,
                "prepare_conversion_options should succeed with clamped timeout");
    TEST_ASSERT(options.timeout_ms == UINT32_MAX, "timeout should be clamped to UINT32_MAX");

    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options timeout clamping correct");
}


/*
 * Test: prepare_conversion_options with prune_selectors configured.
 */
static void
test_prepare_conversion_options_prune_selectors(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;
    ngx_str_t selectors;

    TEST_SUBSECTION("prepare_conversion_options: prune_selectors set");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "example.com");
    set_str(&r.uri, "/page.html");
    set_str(&r.headers_out.content_type, "text/html");
    r.loc_conf = &conf;

    set_str(&selectors, "nav,footer,aside");
    conf.advanced.prune_selectors = &selectors;
    conf.advanced.prune_noise = 1;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, NULL, &options) == NGX_OK,
                "prepare_conversion_options should succeed with prune_selectors");
    TEST_ASSERT(options.prune_selectors != NULL, "prune_selectors should be set");
    TEST_ASSERT(options.prune_selector_len > 0, "prune_selector_len should be > 0");

    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options prune_selectors correct");
}


/*
 * Test: prepare_conversion_options with prune_protection_selectors configured.
 */
static void
test_prepare_conversion_options_prune_protection_selectors(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    struct MarkdownOptions options;
    ngx_str_t protection;

    TEST_SUBSECTION("prepare_conversion_options: prune_protection_selectors set");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    set_str(&r.schema, "http");
    set_str(&r.headers_in.server, "example.com");
    set_str(&r.uri, "/page.html");
    set_str(&r.headers_out.content_type, "text/html");
    r.loc_conf = &conf;

    set_str(&protection, "main,article");
    conf.advanced.prune_protection_selectors = &protection;
    conf.advanced.prune_noise = 1;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, NULL, &options) == NGX_OK,
                "prepare_conversion_options should succeed with prune_protection_selectors");
    TEST_ASSERT(options.prune_protection_selectors != NULL,
                "prune_protection_selectors should be set");
    TEST_ASSERT(options.prune_protection_selector_len > 0,
                "prune_protection_selector_len should be > 0");

    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

    TEST_PASS("prepare_conversion_options prune_protection_selectors correct");
}


/*
 * Test: prepare_conversion_options with empty server but valid schema (line 193).
 *       Also test the else branch (empty schema, line 194-196) in same test.
 */
static void
test_prepare_conversion_options_schema_server_fallback(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_http_core_srv_conf_t cscf;
    struct MarkdownOptions options;

    TEST_SUBSECTION("prepare_conversion_options: schema present, server empty, cscf fallback");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    memset(&cscf, 0, sizeof(cscf));

    set_str(&r.schema, "https");
    set_str(&r.headers_in.server, "");
    set_str(&r.uri, "/page.html");
    set_str(&cscf.server_name, "via-cscf.example.com");
    r.loc_conf = &conf;
    r.srv_conf = &cscf;

    conf.flavor = 0;
    conf.timeout = 5000;

    TEST_ASSERT(ngx_http_markdown_prepare_conversion_options(&r, &conf, NULL, &options) == NGX_OK,
                "prepare_conversion_options should succeed with cscf fallback");
    TEST_ASSERT(options.base_url != NULL, "base_url should be constructed");
    TEST_ASSERT(options.base_url_len > 0, "base_url_len should be > 0");

    if (options.base_url != NULL) {
        free((void *)(uintptr_t) options.base_url);
    }

TEST_PASS("prepare_conversion_options schema+server fallback correct");
}


/*
 * Verify shadow compare exits early when prepare_conversion_options
 * rejects invalid shadow-mode options.
 */
static void
test_shadow_compare_prepare_options_failure(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    struct MarkdownResult     fb_result;

    TEST_SUBSECTION("shadow compare: prepare options failure");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&fb_result, 0, sizeof(fb_result));

    ctx.buffer.data = (u_char *) "shadow body";
    ctx.buffer.size = sizeof("shadow body") - 1;
    ctx.effective_conf = NULL;

    conf.advanced.llm_provider = (ngx_uint_t) UINT8_MAX + 1;
    conf.advanced.chars_per_token_fixed = 1;

    ngx_http_markdown_shadow_compare(&r, &ctx, &conf, &fb_result, 7);

    TEST_ASSERT(g_reason_streaming_shadow_calls == 0,
                "shadow compare must not resolve the shadow reason after option failure");
    TEST_ASSERT(g_log_decision_calls == 0,
                "shadow compare must not log a shadow decision after option failure");
    TEST_ASSERT(g_streaming_new_with_code_calls == 0,
                "shadow compare must not initialize streaming after option failure");
    TEST_ASSERT(g_streaming_feed_calls == 0,
                "shadow compare must not feed streaming after option failure");
    TEST_ASSERT(g_streaming_finish_calls == 0,
                "shadow compare must not finish streaming after option failure");
    TEST_ASSERT(g_streaming_free_calls == 0,
                "shadow compare must not free streaming after option failure");

    TEST_PASS("shadow compare aborts on prepare options failure");
}


/*
 * Test: find_request_header_value with multi-part header list.
 */
static void
test_find_request_header_multi_part(void)
{
    ngx_http_request_t r;
    ngx_http_markdown_conf_t conf;
    ngx_table_elt_t headers_part1[1];
    ngx_table_elt_t headers_part2[1];
    ngx_list_part_t part2;
    const ngx_str_t *result;

    TEST_SUBSECTION("find_request_header_value: multi-part list");

    init_request(&r);
    memset(&conf, 0, sizeof(conf));
    r.loc_conf = &conf;

    /* Part 1: one header */
    set_str(&headers_part1[0].key, "X-First");
    set_str(&headers_part1[0].value, "value1");
    headers_part1[0].hash = 1;

    /* Part 2: one header */
    set_str(&headers_part2[0].key, "X-Forwarded-Proto");
    set_str(&headers_part2[0].value, "https");
    headers_part2[0].hash = 1;

    part2.elts = headers_part2;
    part2.nelts = 1;
    part2.next = NULL;

    r.headers_in.headers.part.elts = headers_part1;
    r.headers_in.headers.part.nelts = 1;
    r.headers_in.headers.part.next = &part2;

    conf.ops.trust_forwarded_headers = 1;

    /* Search for X-Forwarded-Proto — should find it in part 2 */
    result = ngx_http_markdown_find_request_header_value(
        &r,
        (const u_char *) "X-Forwarded-Proto",
        sizeof("X-Forwarded-Proto") - 1);
    TEST_ASSERT(result != NULL, "should find header in second part");
    TEST_ASSERT(result->len == 5, "value length should be 5");
    TEST_ASSERT(memcmp(result->data, "https", 5) == 0, "value should be https");

    /* Search for non-existent header */
    result = ngx_http_markdown_find_request_header_value(
        &r,
        (const u_char *) "X-Nonexistent",
        sizeof("X-Nonexistent") - 1);
    TEST_ASSERT(result == NULL, "non-existent header should return NULL");

    /* Search with empty header list */
    r.headers_in.headers.part.nelts = 0;
    r.headers_in.headers.part.next = NULL;
    result = ngx_http_markdown_find_request_header_value(
        &r,
        (const u_char *) "X-Forwarded-Proto",
        sizeof("X-Forwarded-Proto") - 1);
    TEST_ASSERT(result == NULL, "empty list should return NULL");

    TEST_PASS("find_request_header_value multi-part correct");
}

/*
 * Verify validate_conversion_result invariant checks: NULL markdown with
 * non-zero length triggers fail-open; NULL error_message with non-zero
 * length triggers fail-open; NULL etag with non-zero length triggers
 * fail-open; valid result passes without invoking fail-open.
 */
static void
test_validate_conversion_result_paths(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    struct MarkdownResult     result;
    ngx_int_t                 rc;

    TEST_SUBSECTION("validate_conversion_result invariants");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    memset(&result, 0, sizeof(result));
    result.markdown = NULL;
    result.markdown_len = 1;
    g_failopen_rc = NGX_DECLINED;
    rc = ngx_http_markdown_validate_conversion_result(&r, &ctx, &conf, &result);
    TEST_ASSERT(rc == NGX_DECLINED, "invalid markdown invariants should fail-open");
    TEST_ASSERT(g_failopen_call_count == 1,
                "invalid markdown invariants should invoke fail-open stub");
    TEST_ASSERT(g_markdown_result_free_calls == 1,
                "invalid result should be freed");

    reset_stub_state();
    memset(&result, 0, sizeof(result));
    result.error_message = NULL;
    result.error_len = 3;
    rc = ngx_http_markdown_validate_conversion_result(&r, &ctx, &conf, &result);
    TEST_ASSERT(rc == NGX_OK, "stub fail-open default should propagate as NGX_OK");
    TEST_ASSERT(g_failopen_call_count == 1,
                "error message invariant failure should invoke fail-open stub");

    reset_stub_state();
    memset(&result, 0, sizeof(result));
    result.etag = NULL;
    result.etag_len = 2;
    rc = ngx_http_markdown_validate_conversion_result(&r, &ctx, &conf, &result);
    TEST_ASSERT(rc == NGX_OK, "etag invariant failure should route through fail-open");
    TEST_ASSERT(g_failopen_call_count == 1,
                "etag invariant failure should invoke fail-open stub");

    reset_stub_state();
    memset(&result, 0, sizeof(result));
    rc = ngx_http_markdown_validate_conversion_result(&r, &ctx, &conf, &result);
    TEST_ASSERT(rc == NGX_OK, "valid invariants should pass");
    TEST_ASSERT(g_failopen_call_count == 0,
                "valid invariants should not invoke fail-open stub");

    TEST_PASS("validate_conversion_result branches covered");
}

/*
 * Verify handle_conversion_failure error category classification:
 * PARSE errors map to CONVERSION category; TIMEOUT errors map to
 * RESOURCE_LIMIT category; INTERNAL errors map to SYSTEM category.
 * Each path propagates the fail-open return code.
 */
static void
test_handle_conversion_failure_paths(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    struct MarkdownResult     result;
    ngx_int_t                 rc;
    u_char                    msg[] = "oops";

    TEST_SUBSECTION("handle_conversion_failure categories");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.error_code = ERROR_PARSE;
    result.error_message = msg;
    result.error_len = sizeof(msg) - 1;
    g_failopen_rc = NGX_DONE;
    rc = ngx_http_markdown_handle_conversion_failure(
        &r, &ctx, &conf, &result, 12);
    TEST_ASSERT(rc == NGX_DONE, "conversion category should propagate fail-open return");
    TEST_ASSERT(ctx.error.has_category == 1, "error category should be recorded");
    TEST_ASSERT(ctx.error.last_category == NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
                "conversion category expected");

    reset_stub_state();
    memset(&ctx, 0, sizeof(ctx));
    memset(&result, 0, sizeof(result));
    result.error_code = ERROR_TIMEOUT;
    /*
     * Exercise oversized length handling without risking reads past a tiny
     * stack buffer: NULL message means no payload should be dereferenced.
     */
    result.error_message = NULL;
    result.error_len = (size_t) INT_MAX + 11U;
    rc = ngx_http_markdown_handle_conversion_failure(
        &r, &ctx, &conf, &result, 99);
    TEST_ASSERT(rc == NGX_OK, "resource category should use default fail-open");
    TEST_ASSERT(ctx.error.last_category == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
                "resource category expected");

    reset_stub_state();
    memset(&ctx, 0, sizeof(ctx));
    memset(&result, 0, sizeof(result));
    result.error_code = ERROR_INTERNAL;
    result.error_message = NULL;
    result.error_len = 0;
    rc = ngx_http_markdown_handle_conversion_failure(
        &r, &ctx, &conf, &result, 1);
    TEST_ASSERT(rc == NGX_OK, "system category should route through fail-open");
    TEST_ASSERT(ctx.error.last_category == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
                "system category expected");

    TEST_PASS("handle_conversion_failure branches covered");
}

/*
 * Verify handle_converter_not_initialized follows the configured
 * fail-open strategy and records the system error category.
 */
static void
test_converter_not_initialized_path(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    struct MarkdownResult     result;
    ngx_msec_t                elapsed_ms;
    ngx_flag_t                has_result;
    ngx_int_t                 rc;

    TEST_SUBSECTION("converter_not_initialized path");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    g_failopen_rc = NGX_DECLINED;

    rc = ngx_http_markdown_handle_converter_not_initialized(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED,
                "converter_not_initialized should follow fail-open strategy");
    TEST_ASSERT(ctx.error.has_category == 1,
                "system failure should set error category flag");

    elapsed_ms = 0;
    rc = ngx_http_markdown_execute_conversion(&r, &ctx, &conf, &result, &elapsed_ms);
    TEST_ASSERT(rc == NGX_DECLINED,
                "execute_conversion should follow fail-open when converter is NULL");

    has_result = 0;
    elapsed_ms = 0;
    rc = ngx_http_markdown_resolve_conditional_result(
        &r, &ctx, &conf, &result, &elapsed_ms, &has_result);
    TEST_ASSERT(rc == NGX_OK,
                "conditional resolver should continue when no conditional match");
    TEST_ASSERT(has_result == 0,
                "conditional resolver should not set has_result on DECLINED path");

    TEST_PASS("converter_not_initialized covered");
}

/*
 * Verify send_conversion_output branch coverage: HEAD method output,
 * body output, header update failure, forward header failure, buffer
 * allocation failure (pcalloc), body allocation failure (pnalloc),
 * chain allocation failure.
 */
static void
test_send_conversion_output_paths(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    struct MarkdownResult     result;
    ngx_int_t                 rc;
    u_char                    out[] = "markdown";

    TEST_SUBSECTION("send_conversion_output branches");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    r.method = NGX_HTTP_HEAD;
    result.markdown = out;
    result.markdown_len = sizeof(out) - 1;
    rc = ngx_http_markdown_send_conversion_output(
        &r, &ctx, &conf, &result, 1);
    TEST_ASSERT(rc == NGX_OK, "HEAD output path should succeed");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    r.method = 0;
    result.markdown = out;
    result.markdown_len = sizeof(out) - 1;
    rc = ngx_http_markdown_send_conversion_output(
        &r, &ctx, &conf, &result, 1);
    TEST_ASSERT(rc == NGX_OK, "body output path should succeed");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown = out;
    result.markdown_len = sizeof(out) - 1;
    g_update_headers_rc = NGX_ERROR;
    rc = ngx_http_markdown_send_conversion_output(
        &r, &ctx, &conf, &result, 1);
    TEST_ASSERT(rc == NGX_ERROR, "header update failure should return NGX_ERROR");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown = out;
    result.markdown_len = sizeof(out) - 1;
    g_forward_headers_rc = NGX_ERROR;
    rc = ngx_http_markdown_send_conversion_output(
        &r, &ctx, &conf, &result, 1);
    TEST_ASSERT(rc == NGX_ERROR, "forward header failure should propagate");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown = out;
    result.markdown_len = sizeof(out) - 1;
    g_pcalloc_fail_once = 1;
    rc = ngx_http_markdown_send_conversion_output(
        &r, &ctx, &conf, &result, 1);
    TEST_ASSERT(rc == NGX_ERROR, "buffer allocation failure should return NGX_ERROR");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown = out;
    result.markdown_len = sizeof(out) - 1;
    g_pnalloc_fail_once = 1;
    rc = ngx_http_markdown_send_conversion_output(
        &r, &ctx, &conf, &result, 1);
    TEST_ASSERT(rc == NGX_ERROR, "body allocation failure should return NGX_ERROR");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown = out;
    result.markdown_len = sizeof(out) - 1;
    g_alloc_chain_fail_once = 1;
    rc = ngx_http_markdown_send_conversion_output(
        &r, &ctx, &conf, &result, 1);
    TEST_ASSERT(rc == NGX_ERROR, "chain allocation failure should return NGX_ERROR");

    TEST_PASS("send_conversion_output branches covered");
}

/*
 * Regression: the NGINX copy filter owns its unsent chain after returning
 * NGX_AGAIN.  Resume must call the downstream filter with NULL so it drains
 * that retained state instead of receiving the original chain a second time.
 */
static void
test_fullbuffer_resume_does_not_resubmit_pending_chain(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    struct MarkdownResult     result;
    ngx_chain_t              *original_chain;
    ngx_int_t                 rc;
    u_char                    out[] = "markdown";

    TEST_SUBSECTION("full-buffer resume drains downstream-owned state");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown = out;
    result.markdown_len = sizeof(out) - 1;

    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_send_conversion_output(
        &r, &ctx, &conf, &result, 1);
    original_chain = ctx.fullbuffer.pending_output;

    TEST_ASSERT(rc == NGX_AGAIN,
                "initial full-buffer send should preserve NGX_AGAIN");
    TEST_ASSERT(original_chain != NULL,
                "initial full-buffer send should retain request state");
    TEST_ASSERT(g_next_body_filter_last_input == original_chain,
                "initial send should submit the converted body chain");
    TEST_ASSERT(ctx.fullbuffer.pending_has_data == 1,
                "initial NGX_AGAIN should set the pending latch");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
                "initial NGX_AGAIN should set the module buffered flag");

    g_next_body_filter_rc = NGX_OK;
    g_metric_inc_sink = 0;
    rc = ngx_http_markdown_body_filter_resume_pending(&r, &ctx);

    TEST_ASSERT(rc == NGX_OK,
                "full-buffer resume should return downstream success");
    TEST_ASSERT(g_next_body_filter_call_count == 2,
                "resume should make exactly one additional downstream call");
    TEST_ASSERT(g_next_body_filter_last_input == NULL,
                "resume must flush downstream state without resubmitting chain");
    TEST_ASSERT(ctx.fullbuffer.pending_output == NULL,
                "successful resume should clear the retained chain anchor");
    TEST_ASSERT(ctx.fullbuffer.pending_has_data == 0,
                "successful resume should clear the pending latch");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) == 0,
                "successful resume should clear the module buffered flag");
    TEST_ASSERT(g_metric_inc_sink == 1,
                "successful resume should record full-buffer delivery");

    TEST_PASS("Full-buffer resume does not duplicate downstream pending data");
}

/* Verify persistent backpressure keeps state and terminal failure clears it. */
static void
test_fullbuffer_resume_pending_lifecycle(void)
{
    ngx_http_request_t        r;
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    struct MarkdownResult     result;
    ngx_chain_t              *original_chain;
    ngx_int_t                 rc;
    u_char                    out[] = "markdown";

    TEST_SUBSECTION("full-buffer resume pending lifecycle");

    reset_stub_state();
    init_request(&r);
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));
    result.markdown = out;
    result.markdown_len = sizeof(out) - 1;

    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_send_conversion_output(
        &r, &ctx, &conf, &result, 1);
    original_chain = ctx.fullbuffer.pending_output;
    TEST_ASSERT(rc == NGX_AGAIN,
                "initial send should establish pending state");

    r.buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
    rc = ngx_http_markdown_body_filter_resume_pending(&r, &ctx);
    TEST_ASSERT(rc == NGX_AGAIN,
                "persistent downstream backpressure should remain pending");
    TEST_ASSERT(g_next_body_filter_last_input == NULL,
                "persistent resume should drain with NULL input");
    TEST_ASSERT(ctx.fullbuffer.pending_output == original_chain,
                "persistent backpressure should retain the chain anchor");
    TEST_ASSERT(ctx.fullbuffer.pending_has_data == 1,
                "persistent backpressure should retain the pending latch");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
                "persistent backpressure should reassert the buffered flag");

    g_next_body_filter_rc = NGX_ERROR;
    g_metric_inc_sink = 0;
    rc = ngx_http_markdown_body_filter_resume_pending(&r, &ctx);
    TEST_ASSERT(rc == NGX_ERROR,
                "terminal downstream failure should propagate");
    TEST_ASSERT(g_next_body_filter_last_input == NULL,
                "failure resume should still use NULL input");
    TEST_ASSERT(ctx.fullbuffer.pending_output == NULL,
                "terminal failure should clear the chain anchor");
    TEST_ASSERT(ctx.fullbuffer.pending_has_data == 0,
                "terminal failure should clear the pending latch");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) == 0,
                "terminal failure should clear the buffered flag");
    TEST_ASSERT(g_metric_inc_sink == 0,
                "terminal failure should not record delivery");

    TEST_PASS("Full-buffer resume pending lifecycle is symmetric");
}

/*
 * Verify miscellaneous conversion helper branches:
 * record_conversion_latency, record_system_failure, and
 * record_token_savings_if_enabled with various token_estimate
 * configurations.
 */
static void
test_misc_conversion_helpers(void)
{
    ngx_http_markdown_ctx_t   ctx;
    ngx_http_markdown_conf_t  conf;
    struct MarkdownResult     result;

    TEST_SUBSECTION("misc conversion helper branches");

    reset_stub_state();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&result, 0, sizeof(result));

    ngx_http_markdown_record_conversion_latency(1);
    ngx_http_markdown_record_conversion_latency(50);
    ngx_http_markdown_record_conversion_latency(500);
    ngx_http_markdown_record_conversion_latency(5000);

    ngx_http_markdown_record_system_failure(&ctx);
    TEST_ASSERT(ctx.error.has_category == 1,
                "record_system_failure should set context flag");

    ctx.buffer.size = 400;
    conf.token_estimate = 1;
    result.token_estimate = 50;
    ngx_http_markdown_record_token_savings_if_enabled(
        &ctx, &conf, &result);

    result.token_estimate = 200;
    ngx_http_markdown_record_token_savings_if_enabled(
        &ctx, &conf, &result);

    conf.token_estimate = 0;
    ngx_http_markdown_record_token_savings_if_enabled(
        &ctx, &conf, &result);

    TEST_PASS("misc conversion helpers covered");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("conversion_impl_base_url Tests\n");
    printf("========================================\n");

    test_base_url_marshals_request_fields();
    test_base_url_unix_socket_flag();
    test_base_url_not_configured_marshaled();
    test_base_url_decision_failure_propagates();
    test_base_url_add_len_overflow_guard();
    test_prepare_conversion_options_basic();
    test_prepare_conversion_options_gfm();
    test_prepare_conversion_options_no_content_type();
    test_prepare_conversion_options_no_base_url();
    test_prepare_conversion_options_flavor_clamp();
    test_prepare_conversion_options_timeout_clamp();
    test_prepare_conversion_options_prune_selectors();
    test_prepare_conversion_options_prune_protection_selectors();
    test_prepare_conversion_options_schema_server_fallback();
    test_shadow_compare_prepare_options_failure();
    test_find_request_header_multi_part();
    test_validate_conversion_result_paths();
    test_handle_conversion_failure_paths();
    test_converter_not_initialized_path();
    test_send_conversion_output_paths();
    test_fullbuffer_resume_does_not_resubmit_pending_chain();
    test_fullbuffer_resume_pending_lifecycle();
    test_misc_conversion_helpers();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
