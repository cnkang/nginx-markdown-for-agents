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
#include "../../src/ngx_http_markdown_diagnostics.h"

/* The conversion-output tests exercise the production finalizer call. */
struct ngx_module_s {
    int unused;
};
ngx_module_t ngx_http_markdown_filter_module;

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
    return (eff != NULL) ? eff->memory_budget : conf->limits.conversion_memory;
}

/*
 * Capturing stub for the base-URL FFI entry point.
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

/* Stub control + capture state for markdown_decide_base_url. */
static FFIBaseUrlInput g_captured_base_url_input;
static ngx_uint_t             g_decide_base_url_calls;
static const char            *g_stub_authority = "https://stub.example.com";
static uint8_t                g_stub_decide_rc = DECIDE_BASE_URL_OK;
static uint8_t                g_stub_decide_reason;
static uint8_t                g_stub_decide_source;

uint8_t
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
static ngx_int_t g_forward_transformed_headers_rc = 0;
static ngx_int_t g_update_headers_rc = 0;
static ngx_int_t g_failopen_rc = 0;
static ngx_uint_t g_failopen_call_count = 0;
static ngx_int_t g_bypass_failopen_rc = 0;
static ngx_uint_t g_bypass_failopen_call_count = 0;
static ngx_int_t g_conditional_return_rc = NGX_DECLINED;
static ngx_int_t g_send_304_rc = NGX_OK;
static ngx_uint_t g_release_inflight_call_count = 0;
static ngx_int_t g_next_body_filter_rc = 0;
static ngx_chain_t *g_next_body_filter_last_input = NULL;
static ngx_uint_t g_next_body_filter_call_count = 0;
static ngx_uint_t g_markdown_result_free_calls = 0;
static ngx_uint_t g_log_decision_calls = 0;
static const ngx_str_t *g_last_decision_reason = NULL;
static const ngx_str_t *g_last_decision_category = NULL;
static ngx_uint_t g_last_failure_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
static ngx_uint_t g_last_failure_status = 0;
static ngx_uint_t g_streaming_new_with_code_calls = 0;
static ngx_uint_t g_streaming_feed_calls = 0;
static ngx_uint_t g_streaming_finish_calls = 0;
static ngx_uint_t g_abort_calls = 0;
static ngx_uint_t g_output_free_calls = 0;
static ngx_uint_t g_pnalloc_fail_once = 0;
static ngx_uint_t g_pcalloc_fail_once = 0;
static ngx_uint_t g_alloc_chain_fail_once = 0;
static uint32_t g_streaming_new_with_code_rc = 0;
static uint32_t g_streaming_feed_rc = 0;
static uint32_t g_streaming_finalize_rc = 0;
static uint32_t g_streaming_new_with_code_null_handle = 0;

/* Decompression failure helpers are owned by module_state_impl.h in the
 * production translation unit.  Keep this direct conversion-header test
 * independent of that implementation-only include. */
static void
ngx_http_markdown_record_decompression_failure_budget(
    ngx_http_markdown_compression_type_e type)
{
    UNUSED(type);
}

static void
ngx_http_markdown_record_decompression_failure_format(
    ngx_http_markdown_compression_type_e type)
{
    UNUSED(type);
}

static void
ngx_http_markdown_record_decompression_failure_truncated(
    ngx_http_markdown_compression_type_e type)
{
    UNUSED(type);
}

static void
ngx_http_markdown_record_decompression_failure_io(
    ngx_http_markdown_compression_type_e type)
{
    UNUSED(type);
}

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

void
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
void
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
 * (timeout_ms=5000, generate_etag=0).  The production code must
 * call this instead of ngx_memzero to honour the FFI contract.
 */
void
markdown_options_init(struct MarkdownOptions *result)
{
    if (result == NULL) {
        return;
    }
    memset(result, 0, sizeof(*result));
    result->timeout_ms = 5000;
    result->generate_etag = 0;
}

/*
 * FFI lifecycle stub for markdown_result_init.  Zeroes all fields
 * to guarantee a clean baseline before FFI calls populate the struct.
 */
void
markdown_result_init(struct MarkdownResult *result)
{
    if (result == NULL) {
        return;
    }
    memset(result, 0, sizeof(*result));
}

/* FFI lifecycle stub for the borrowed base-URL input snapshot. */
void
markdown_base_url_input_init(struct FFIBaseUrlInput *result)
{
    if (result == NULL) {
        return;
    }
    memset(result, 0, sizeof(*result));
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
ngx_http_markdown_log_decision_path(
    ngx_http_request_t *r,
    const void *conf,
    const void *eff,
    const ngx_http_markdown_decision_path_t *path)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(eff);
    UNUSED(path);
    g_log_decision_calls++;
}

const ngx_str_t *
ngx_http_markdown_reason_header_plan_apply_err(void)
{
    static ngx_str_t reason = {
        sizeof("header_plan_apply_error") - 1,
        (u_char *) "header_plan_apply_error"
    };

    return &reason;
}

const ngx_str_t *
ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, ngx_log_t *log)
{
    static ngx_str_t conversion = {
        sizeof("conversion_error") - 1,
        (u_char *) "conversion_error"
    };
    static ngx_str_t resource = {
        sizeof("memory_budget_exceeded") - 1,
        (u_char *) "memory_budget_exceeded"
    };
    static ngx_str_t system = {
        sizeof("ffi_panic") - 1,
        (u_char *) "ffi_panic"
    };

    UNUSED(log);
    if (category == NGX_HTTP_MARKDOWN_ERROR_CONVERSION) {
        return &conversion;
    }
    if (category == NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT) {
        return &resource;
    }
    return &system;
}

static void
ngx_http_markdown_log_decision_with_category(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code,
    const ngx_str_t *error_category)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(eff);
    g_log_decision_calls++;
    g_last_decision_reason = reason_code;
    g_last_decision_category = error_category;
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

typedef struct ngx_list_part_s ngx_list_part_t;
typedef struct ngx_table_elt_s ngx_table_elt_t;
typedef struct ngx_http_headers_in_s ngx_http_headers_in_t;
typedef struct ngx_http_headers_out_s ngx_http_headers_out_t;
typedef struct ngx_http_core_srv_conf_s ngx_http_core_srv_conf_t;
typedef struct ngx_connection_s ngx_connection_t;
typedef struct ngx_log_s ngx_log_t;
typedef struct ngx_pool_s ngx_pool_t;
typedef struct ngx_http_variable_value_s ngx_http_variable_value_t;
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

struct ngx_http_variable_value_s {
    unsigned         len:28;
    unsigned         valid:1;
    unsigned         no_cacheable:1;
    unsigned         not_found:1;
    unsigned         escape:1;
    u_char           *data;
};

struct ngx_pool_s {
    ngx_log_t  *log;
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
    time_t last_modified_time;
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

static ngx_str_t g_realip_remote_addr;

#ifndef NGX_CONF_UNSET_SIZE
#define NGX_CONF_UNSET_SIZE ((size_t) -1)
#endif

ngx_uint_t
ngx_hash_key_lc(u_char *data, size_t len)
{
    UNUSED(data);
    UNUSED(len);
    return 0;
}

ngx_http_variable_value_t *
ngx_http_get_variable(ngx_http_request_t *r, ngx_str_t *name,
    ngx_uint_t key)
{
    static ngx_http_variable_value_t not_found = {
        0, 0, 0, 1, 0, NULL
    };
    static ngx_http_variable_value_t realip_remote_addr;

    UNUSED(r);
    UNUSED(key);

    if (name == NULL
        || name->len != sizeof("realip_remote_addr") - 1
        || memcmp(name->data, "realip_remote_addr", name->len) != 0
        || g_realip_remote_addr.len == 0)
    {
        return &not_found;
    }

    realip_remote_addr.len = g_realip_remote_addr.len;
    realip_remote_addr.valid = 1;
    realip_remote_addr.no_cacheable = 0;
    realip_remote_addr.not_found = 0;
    realip_remote_addr.escape = 0;
    realip_remote_addr.data = g_realip_remote_addr.data;
    return &realip_remote_addr;
}

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
#ifndef NGX_HTTP_MARKDOWN_METRIC_WATERMARK
#define NGX_HTTP_MARKDOWN_METRIC_WATERMARK(field, value)                              \
    do {                                                                              \
        g_metric_add_sink = 1;                                                       \
        UNUSED(value);                                                                \
    } while (0)
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
 * subrequest: buffer.c symbols.  conversion_impl.h calls
 * ngx_http_markdown_buffer_release() at the conversion terminal; the
 * implementation lives in buffer.c and needs these pool/alloc stubs
 * (same pattern as eligibility_impl_test.c).
 */
typedef struct ngx_pool_cleanup_s {
    void                         (*handler)(void *data);
    void                          *data;
    struct ngx_pool_cleanup_s     *next;
} ngx_pool_cleanup_t;

static ngx_pool_cleanup_t  test_cleanup;

ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    (void) size;
    memset(&test_cleanup, 0, sizeof(test_cleanup));
    return &test_cleanup;
}

void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    (void) log;
    return malloc(size);
}

#define ngx_free free
#define ngx_memcpy memcpy

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

/* The converted-representation path has its own forwarding seam so tests
 * cannot accidentally verify source-representation Last-Modified behavior. */
static ngx_int_t
ngx_http_markdown_forward_transformed_headers(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(r);
    UNUSED(ctx);
    return g_forward_transformed_headers_rc;
}

__attribute__((unused))
static void
ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(eff);
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
    UNUSED(debug_message);
    g_failopen_call_count++;
    if (conf != NULL) {
        g_last_failure_policy = conf->on_error;
        g_last_failure_status = conf->error_status;
    } else {
        /* Safe fallbacks matching the stub-state initializers. */
        g_last_failure_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
        g_last_failure_status = 0;
    }
    return g_failopen_rc;
}

/*
 * Direct fail-open stub (bypass path).  Increments g_bypass_failopen_call_count
 * and returns g_bypass_failopen_rc, allowing tests to verify that the bypass
 * path calls fail_open directly, NOT reject_or_fail_open.
 */
static ngx_int_t
ngx_http_markdown_fail_open_buffered_response(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const char *debug_message)
{
    UNUSED(r);
    UNUSED(ctx);
    UNUSED(debug_message);
    g_bypass_failopen_call_count++;
    return g_bypass_failopen_rc;
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
    return g_conditional_return_rc;
}

void
ngx_http_markdown_release_inflight_for_request(const ngx_http_request_t *r)
{
    UNUSED(r);
    g_release_inflight_call_count++;
}

ngx_int_t
ngx_http_markdown_send_304(
    ngx_http_request_t *r,     /* SONAR_NOTE c:S995 — must match module header decl */
    const struct MarkdownResult *result)
{
    UNUSED(r);
    UNUSED(result);
    return g_send_304_rc;
}

ngx_int_t
ngx_http_markdown_send_412(ngx_http_request_t *r)
{
    UNUSED(r);
    return g_send_304_rc;
}

/*
 * subrequest: conversion_impl.h calls ngx_http_markdown_buffer_release() at the
 * conversion terminal.  The implementation lives in buffer.c; include it
 * directly (same pattern as eligibility_impl_test.c) so the symbol
 * resolves at link time.  Must follow the stub definitions above.
 */
#include "../../src/ngx_http_markdown_buffer.c"

#include "../../src/ngx_http_markdown_conversion_impl.h" /* SONAR_NOTE: must follow stub definitions */
#ifndef NGINX_VERSION
#define NGINX_VERSION "test"
#endif
#define NGX_HTTP_MARKDOWN_METRICS_CORE_ONLY
static ngx_atomic_uint_t
ngx_http_markdown_inflight_current(void)
{
    return 0;
}
#include "../../src/ngx_http_markdown_metrics_impl.h"
#undef NGX_HTTP_MARKDOWN_METRICS_CORE_ONLY

static ngx_connection_t g_connection = { 0 };
static ngx_log_t g_log = { 0 };

/* ── Test: production delivery recording ─────────────────────── */

static void
test_success_records_delivery_once(void)
{
    ngx_http_markdown_ctx_t  ctx;

    TEST_SUBSECTION("production record_buffered_delivery_success");

    memset(&ctx, 0, sizeof(ctx));
    ctx.conversion.succeeded = 1;
    ctx.conversion.input_bytes = 100;
    ctx.conversion.output_bytes = 50;

    g_metric_inc_sink = 0;
    g_metric_add_sink = 0;

    ngx_http_markdown_record_buffered_delivery_success(&ctx);

    TEST_ASSERT(ctx.conversion.delivery_recorded == 1,
        "delivery recorded after success");
    TEST_ASSERT(g_metric_inc_sink == 1,
        "conversions_succeeded metric incremented");

    /* Idempotent: second call must not re-record. */
    g_metric_inc_sink = 0;
    ngx_http_markdown_record_buffered_delivery_success(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 1,
        "delivery_recorded stays set");
    TEST_ASSERT(g_metric_inc_sink == 0,
        "no duplicate metric increment on repeat call");

    TEST_PASS("success records delivery exactly once");
}

static void
test_failure_records_terminal_failure(void)
{
    ngx_http_markdown_ctx_t  ctx;

    TEST_SUBSECTION("production record_buffered_delivery_failure");

    memset(&ctx, 0, sizeof(ctx));
    ctx.conversion.succeeded = 1;
    ctx.error.has_category = 0;

    ngx_http_markdown_record_buffered_delivery_failure(&ctx);

    /* Failure path records system error metrics but does not set delivery_recorded
     * (only success path sets delivery_recorded). The function is idempotent. */
    TEST_ASSERT(ctx.conversion.delivery_recorded == 0,
        "delivery_recorded not set for terminal failure");
    TEST_ASSERT(ctx.error.has_category == 1,
        "error category recorded for terminal failure");

    /* Already-recorded: no-op. */
    ngx_http_markdown_record_buffered_delivery_failure(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 0,
        "repeat failure call is a no-op");
    TEST_ASSERT(ctx.error.has_category == 1,
        "error category remains set");

    TEST_PASS("failure records terminal delivery once");
}

static void
test_not_succeeded_never_records(void)
{
    ngx_http_markdown_ctx_t  ctx;

    TEST_SUBSECTION("conversion not succeeded -> no delivery record");

    memset(&ctx, 0, sizeof(ctx));
    ctx.conversion.succeeded = 0;

    ngx_http_markdown_record_buffered_delivery_success(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 0,
        "no delivery record when conversion did not succeed");

    ngx_http_markdown_record_buffered_delivery_failure(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 0,
        "no delivery record on failure when conversion did not succeed");

    TEST_PASS("not-succeeded conversions never record delivery");
}

static void
test_null_ctx_is_safe(void)
{
    TEST_SUBSECTION("NULL ctx is a safe no-op");

    ngx_http_markdown_record_buffered_delivery_success(NULL);
    ngx_http_markdown_record_buffered_delivery_failure(NULL);

    TEST_PASS("NULL ctx handled safely");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("failopen_delivery_after_downstream (production logic) Tests\n");
    printf("========================================\n");

    test_success_records_delivery_once();
    test_failure_records_terminal_failure();
    test_not_succeeded_never_records();
    test_null_ctx_is_safe();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
