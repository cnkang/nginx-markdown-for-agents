#include "../include/test_common.h"

#include <sys/socket.h>
#include <strings.h>

#include "../../src/ngx_http_markdown_filter_module.h"
#include "../../src/ngx_http_markdown_diagnostics.h"
#include "markdown_converter.h"

#ifndef NGX_HTTP_GET
#define NGX_HTTP_GET  0x0002
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 0x0004
#endif
#ifndef NGX_CONF_UNSET_UINT
#define NGX_CONF_UNSET_UINT ((ngx_uint_t) -1)
#endif
#ifndef NGX_CONF_UNSET_SIZE
#define NGX_CONF_UNSET_SIZE ((size_t) -1)
#endif
#ifndef NGX_HTTP_NOT_MODIFIED
#define NGX_HTTP_NOT_MODIFIED 304
#endif

#ifndef NGX_LOG_INFO
#define NGX_LOG_INFO  6
#endif
#ifndef NGX_LOG_CRIT
#define NGX_LOG_CRIT  2
#endif
#ifndef NGX_LOG_ALERT
#define NGX_LOG_ALERT 1
#endif
#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif

#define NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT (16 * 1024 * 1024)

static ngx_http_markdown_metrics_t g_metrics;

#define NGX_HTTP_MARKDOWN_METRIC_INC(name) \
    do { g_metrics.name++; } while (0)
#define NGX_HTTP_MARKDOWN_METRIC_ADD(name, value) \
    do { g_metrics.name += (value); } while (0)
#define NGX_HTTP_MARKDOWN_METRIC_WATERMARK(name, value) \
    do { \
        if (g_metrics.name < (value)) { \
            g_metrics.name = (value); \
        } \
    } while (0)

#ifdef ngx_log_error
#undef ngx_log_error
#endif
#define ngx_log_error(level, log, err, fmt, ...) \
    do { (void) (level); (void) (log); (void) (err); } while (0)

#define ngx_log_debug0(...) ((void) 0)
#define ngx_log_debug1(...) ((void) 0)
#define ngx_log_debug2(...) ((void) 0)
#define ngx_log_debug3(...) ((void) 0)
#define ngx_log_debug4(...) ((void) 0)
#define ngx_log_debug5(...) ((void) 0)
#define ngx_log_debug6(...) ((void) 0)

#define ngx_memzero(dst, n)       memset((dst), 0, (n))
#define ngx_memcpy(dst, src, n)   memcpy((dst), (src), (n))
#define ngx_cpymem(dst, src, n)   (((u_char *) memcpy((dst), (src), (n))) + (n))
#define ngx_strncmp(s1, s2, n)    strncmp((const char *) (s1), \
                                            (const char *) (s2), (n))
#define ngx_null_string            { 0, NULL }
#define ngx_pfree(pool, ptr)       do { (void) (pool); free(ptr); } while (0)

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
    struct sockaddr *sockaddr;
    ngx_str_t addr_text;
};

typedef struct ngx_connection_s ngx_connection_t;

struct ngx_http_request_s {
    ngx_pool_t *pool;
    ngx_connection_t *connection;
    ngx_http_request_t *parent;
    ngx_http_request_t *main;
    void *ctx[1];
    ngx_uint_t method;
    ngx_flag_t buffered;
    ngx_flag_t filter_need_in_memory;
    ngx_int_t sa_family;
    ngx_str_t key;
    ngx_str_t uri;
    ngx_str_t schema;
    struct {
        ngx_list_t headers;
        ngx_str_t server;
        ngx_table_elt_t *accept;
        ngx_table_elt_t *cookie;
        ngx_table_elt_t *authorization;
        ngx_table_elt_t *if_none_match;
        ngx_table_elt_t *if_modified_since;
    } headers_in;
    struct {
        ngx_uint_t status;
        ngx_str_t status_line;
        ngx_list_t headers;
        ngx_list_t trailers;
        ngx_str_t content_type;
        u_char *content_type_lowcase;
        ngx_uint_t content_type_hash;
        ngx_str_t charset;
        size_t content_type_len;
        ngx_table_elt_t *content_length;
        ngx_table_elt_t *content_encoding;
        ngx_table_elt_t *etag;
        ngx_table_elt_t *accept_ranges;
        ngx_table_elt_t *last_modified;
        off_t content_length_n;
        time_t last_modified_time;
    } headers_out;
};

struct ngx_chain_s {
    ngx_buf_t *buf;
    ngx_chain_t *next;
};

typedef ngx_int_t (*ngx_http_output_header_filter_pt)(
    ngx_http_request_t *r);
typedef ngx_int_t (*ngx_http_output_body_filter_pt)(
    ngx_http_request_t *r, ngx_chain_t *in);

struct ngx_module_s {
    ngx_uint_t ctx_index;
};

typedef void (*ngx_pool_cleanup_pt)(void *data);
typedef struct ngx_pool_cleanup_s ngx_pool_cleanup_t;
struct ngx_pool_cleanup_s {
    ngx_pool_cleanup_pt handler;
    void *data;
};

struct ngx_http_complex_value_s {
    ngx_flag_t valid;
    ngx_flag_t not_found;
    u_char *data;
    size_t len;
};

typedef struct {
    ngx_flag_t valid;
    ngx_flag_t not_found;
    u_char *data;
    size_t len;
} ngx_http_variable_value_t;

typedef struct {
    time_t sec;
    ngx_uint_t msec;
} ngx_time_t;

struct ngx_http_markdown_dynconf_snapshot_s {
    ngx_flag_t valid;
};

static ngx_http_markdown_conf_t *g_conf;
static struct {
    ngx_http_markdown_dynconf_snapshot_t active_snapshot;
} ngx_http_markdown_dynconf_watcher;

ngx_module_t ngx_http_markdown_filter_module = { 0 };
ngx_module_t ngx_http_core_module = { 0 };

#define ngx_http_get_module_ctx(request, module) \
    ((ngx_http_markdown_ctx_t *) (request)->ctx[(module).ctx_index])
#define ngx_http_get_module_loc_conf(request, module) (g_conf)
#define ngx_http_get_module_main_conf(request, module) (NULL)

static struct MarkdownConverterHandle *ngx_http_markdown_converter;

static ngx_http_variable_value_t *
ngx_http_get_variable(ngx_http_request_t *r, ngx_str_t *name,
    ngx_uint_t key)
{
    UNUSED(r);
    UNUSED(name);
    UNUSED(key);
    return NULL;
}

ngx_uint_t
ngx_hash_key_lc(u_char *data, size_t len)
{
    UNUSED(data);
    UNUSED(len);
    return 0;
}

static ngx_time_t g_time = { 0, 0 };

const ngx_time_t *
ngx_timeofday(void)
{
    return &g_time;
}

static ngx_inline ngx_flag_t
ngx_http_markdown_effective_prune_noise(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return eff != NULL ? eff->prune_noise : conf->advanced.prune_noise;
}

static ngx_inline size_t
ngx_http_markdown_effective_memory_budget(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return eff != NULL ? eff->memory_budget : conf->limits.conversion_memory;
}

static ngx_str_t g_test_reason = ngx_string("test_reason");

const ngx_str_t *
ngx_http_markdown_reason_failed_open(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_failed_closed(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, ngx_log_t *log)
{
    UNUSED(category);
    UNUSED(log);
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_from_eligibility(
    ngx_http_markdown_eligibility_t eligibility, ngx_log_t *log)
{
    UNUSED(eligibility);
    UNUSED(log);
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_skip_accept(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_skip_no_accept(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_skip_accept_reject(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_skip_conditional(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_bypass_no_transform(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_encoding_header_invalid(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_decompression_format_error(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_overload(void)
{
    return &g_test_reason;
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
    UNUSED(reason_code);
    UNUSED(error_category);
}

static void
ngx_http_markdown_log_decision(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(eff);
    UNUSED(reason_code);
}

static void
ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff == NULL && conf == NULL) {
        return;
    }

    if (ngx_http_markdown_effective_error_policy(eff, conf)
        == NGX_HTTP_MARKDOWN_ON_ERROR_PASS)
    {
        g_metrics.results.failopen_count++;
    }
}

static const ngx_str_t *
ngx_http_markdown_compression_name(
    ngx_http_markdown_compression_type_e compression_type)
{
    UNUSED(compression_type);
    return &g_test_reason;
}

static void
ngx_http_markdown_metric_inc_skip(ngx_http_markdown_eligibility_t eligibility)
{
    UNUSED(eligibility);
}

const ngx_str_t *
ngx_http_markdown_reason_converted(void)
{
    return &g_test_reason;
}

const ngx_str_t *
ngx_http_markdown_reason_header_plan_apply_err(void)
{
    return &g_test_reason;
}

static void
ngx_http_markdown_log_event(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const char *stage,
    const char *event)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(eff);
    UNUSED(stage);
    UNUSED(event);
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
}

void *ngx_pcalloc(ngx_pool_t *pool, size_t size);

ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    return ngx_pcalloc(pool, sizeof(ngx_chain_t));
}

void
ngx_http_markdown_record_decompression_success_metrics(
    const ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(ctx);
}

void
ngx_http_markdown_record_decompression_failure_budget(
    ngx_uint_t compression_type)
{
    UNUSED(compression_type);
}

void
ngx_http_markdown_record_decompression_failure_format(
    ngx_uint_t compression_type)
{
    UNUSED(compression_type);
}

void
ngx_http_markdown_record_decompression_failure_truncated(
    ngx_uint_t compression_type)
{
    UNUSED(compression_type);
}

void
ngx_http_markdown_record_decompression_failure_io(
    ngx_uint_t compression_type)
{
    UNUSED(compression_type);
}

static u_char g_pool_storage[128 * 1024];
static size_t g_pool_offset;
static ngx_int_t g_next_header_rc;
static ngx_int_t g_next_body_rc;
static ngx_uint_t g_next_header_calls;
static ngx_uint_t g_next_body_calls;
static ngx_uint_t g_restore_calls;
static ngx_pool_cleanup_t *g_last_cleanup;

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    void *result;

    UNUSED(pool);
    if (size > sizeof(g_pool_storage) - g_pool_offset) {
        return NULL;
    }

    result = g_pool_storage + g_pool_offset;
    g_pool_offset += size;
    return result;
}

void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    void *result = ngx_palloc(pool, size);

    if (result != NULL) {
        memset(result, 0, size);
    }

    return result;
}

void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    return ngx_palloc(pool, size);
}

void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    UNUSED(log);
    return malloc(size);
}

void
ngx_free(void *ptr)
{
    free(ptr);
}

ngx_buf_t *
ngx_calloc_buf(ngx_pool_t *pool)
{
    return ngx_pcalloc(pool, sizeof(ngx_buf_t));
}

ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    UNUSED(size);
    static ngx_pool_cleanup_t cleanup;
    memset(&cleanup, 0, sizeof(cleanup));
    g_last_cleanup = &cleanup;
    return &cleanup;
}

ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    return (ngx_int_t) strncasecmp((const char *) s1, (const char *) s2, n);
}

ngx_int_t
ngx_http_markdown_is_authenticated(const ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(r);
    UNUSED(conf);
    return 0;
}

ngx_int_t
ngx_http_markdown_modify_cache_control_for_auth(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

ngx_flag_t
ngx_http_markdown_has_no_transform(ngx_http_request_t *r)
{
    UNUSED(r);
    return 0;
}

ngx_flag_t
ngx_http_markdown_is_enabled(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(eff);
    return 1;
}

ngx_int_t
ngx_http_markdown_should_convert(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t *out_reason)
{
    UNUSED(r);
    UNUSED(conf);
    if (out_reason != NULL) {
        *out_reason = 0;
    }
    return 1;
}

ngx_flag_t
ngx_http_markdown_has_conditional_request(ngx_http_request_t *r)
{
    UNUSED(r);
    return 1;
}

ngx_flag_t
ngx_http_markdown_accept_result_varies(ngx_uint_t reason)
{
    UNUSED(reason);
    return 0;
}

ngx_http_markdown_eligibility_t
ngx_http_markdown_check_eligibility(
    const ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    ngx_flag_t filter_enabled,
    const ngx_http_markdown_effective_conf_t *eff)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(filter_enabled);
    UNUSED(eff);
    return NGX_HTTP_MARKDOWN_ELIGIBLE;
}

ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_collect_content_encoding(ngx_http_request_t *r,
    ngx_str_t *out)
{
    UNUSED(r);
    UNUSED(out);
    return NGX_ERROR;
}

u_char
ngx_http_markdown_parse_encoding_chain_ffi(const ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx, const ngx_str_t *combined)
{
    UNUSED(r);
    UNUSED(ctx);
    UNUSED(combined);
    return 0;
}

ngx_int_t
ngx_http_markdown_capture_conditional_request(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(r);
    UNUSED(ctx);
    return NGX_ERROR;
}

void
ngx_http_markdown_restore_conditional_request(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(r);
    UNUSED(ctx);
    g_restore_calls++;
}

void
ngx_http_markdown_adopt_orphan_conditional_headers(ngx_http_request_t *r)
{
    UNUSED(r);
}

void
ngx_http_markdown_build_effective_conf(
    ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_dynconf_snapshot_t *snap,
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(snap);
    if (eff == NULL || conf == NULL) {
        return;
    }
    memset(eff, 0, sizeof(*eff));
    eff->enabled = conf->enabled;
    eff->error_policy = conf->on_error;
    eff->error_status = conf->error_status;
}

void
ngx_http_markdown_bind_request_snapshot(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_dynconf_snapshot_t *snap_copy,
    const ngx_http_markdown_effective_conf_t *early_eff,
    ngx_http_markdown_effective_conf_t *eff_storage,
    ngx_http_markdown_dynconf_snapshot_t **snapshot_slot,
    ngx_http_markdown_effective_conf_t **effective_slot)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(snap_copy);
    UNUSED(early_eff);
    UNUSED(eff_storage);
    UNUSED(snapshot_slot);
    UNUSED(effective_slot);
}

ngx_int_t
ngx_http_markdown_inflight_try_increment(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(ctx);
    return NGX_OK;
}

void
ngx_http_markdown_inflight_release(ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(ctx);
}

void
ngx_http_markdown_pending_output_set(ngx_chain_t **slot, ngx_chain_t *value)
{
    if (slot != NULL) {
        *slot = value;
    }
}

void
ngx_http_markdown_buffer_release(ngx_http_markdown_buffer_t *buffer)
{
    if (buffer != NULL) {
        buffer->data = NULL;
        buffer->size = 0;
        buffer->capacity = 0;
    }
}

ngx_int_t
ngx_http_markdown_buffer_init(ngx_http_markdown_buffer_t *buffer,
    size_t max_size, ngx_pool_t *pool)
{
    UNUSED(pool);
    if (buffer == NULL) {
        return NGX_ERROR;
    }
    memset(buffer, 0, sizeof(*buffer));
    buffer->max_size = max_size;
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_buffer_reserve(ngx_http_markdown_buffer_t *buffer,
    size_t capacity_hint)
{
    UNUSED(buffer);
    UNUSED(capacity_hint);
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_buffer_append(ngx_http_markdown_buffer_t *buffer,
    const u_char *data, size_t len)
{
    UNUSED(buffer);
    UNUSED(data);
    UNUSED(len);
    return NGX_OK;
}

ngx_http_markdown_error_category_t
ngx_http_markdown_classify_error(uint32_t error_code)
{
    UNUSED(error_code);
    return NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
}

const ngx_str_t *
ngx_http_markdown_error_category_string(
    ngx_http_markdown_error_category_t category)
{
    UNUSED(category);
    return &g_test_reason;
}

ngx_int_t
ngx_http_markdown_head_representation_headers(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_update_headers(ngx_http_request_t *r,
    const struct MarkdownResult *result,
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(r);
    UNUSED(result);
    UNUSED(conf);
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_handle_if_none_match(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_ctx_t *ctx,
    struct MarkdownConverterHandle *converter,
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
ngx_http_markdown_send_304(ngx_http_request_t *r,
    const struct MarkdownResult *result)
{
    UNUSED(r);
    UNUSED(result);
    return NGX_OK;
}

void
ngx_http_markdown_release_inflight_for_request(const ngx_http_request_t *r)
{
    UNUSED(r);
}

void
markdown_options_init(struct MarkdownOptions *options)
{
    if (options != NULL) {
        memset(options, 0, sizeof(*options));
    }
}

void
markdown_convert(struct MarkdownConverterHandle *handle,
    const uint8_t *input, uintptr_t input_len,
    const struct MarkdownOptions *options, struct MarkdownResult *result)
{
    UNUSED(handle);
    UNUSED(input);
    UNUSED(input_len);
    UNUSED(options);
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_result_init(struct MarkdownResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_result_free(struct MarkdownResult *result)
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
markdown_decompress_bounded(const uint8_t *input, uintptr_t input_len,
    uint8_t format, uintptr_t budget, uint64_t ratio,
    struct FFIDecompResult *result)
{
    UNUSED(input);
    UNUSED(input_len);
    UNUSED(format);
    UNUSED(budget);
    UNUSED(ratio);
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
    return 0;
}

void
markdown_decompress_free(struct FFIDecompResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

void
markdown_base_url_input_init(struct FFIBaseUrlInput *input)
{
    if (input != NULL) {
        memset(input, 0, sizeof(*input));
    }
}

uint8_t
markdown_decide_base_url(const struct FFIBaseUrlInput *input,
    uint8_t *out_buf, uintptr_t out_buf_cap,
    struct FFIBaseUrlDecision *out)
{
    UNUSED(input);
    UNUSED(out_buf);
    UNUSED(out_buf_cap);
    if (out != NULL) {
        memset(out, 0, sizeof(*out));
    }
    return DECIDE_BASE_URL_INVALID;
}

void
markdown_chain_decode_result_init(struct FFIChainDecodeResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

uint32_t
markdown_decode_encoding_chain(const uint8_t *input, uintptr_t input_len,
    const uint8_t *layers, uint32_t layer_count, uintptr_t max_output,
    uint64_t ratio, struct FFIChainDecodeResult *result)
{
    UNUSED(input);
    UNUSED(input_len);
    UNUSED(layers);
    UNUSED(layer_count);
    UNUSED(max_output);
    UNUSED(ratio);
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
    return 0;
}

void
markdown_chain_decode_free(struct FFIChainDecodeResult *result)
{
    if (result != NULL) {
        memset(result, 0, sizeof(*result));
    }
}

static ngx_int_t
test_next_header_filter(ngx_http_request_t *r)
{
    UNUSED(r);
    g_next_header_calls++;
    return g_next_header_rc;
}

static ngx_int_t
test_next_body_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    UNUSED(r);
    UNUSED(in);
    g_next_body_calls++;
    return g_next_body_rc;
}

#include "../../src/ngx_http_markdown_request_impl.h"

static ngx_http_request_t
make_request(void)
{
    static ngx_pool_t pool;
    static ngx_connection_t connection;
    ngx_http_request_t request;

    memset(&request, 0, sizeof(request));
    memset(&connection, 0, sizeof(connection));
    request.pool = &pool;
    request.connection = &connection;
    request.method = NGX_HTTP_GET;
    return request;
}

static void
reset_test_state(void)
{
    memset(&g_metrics, 0, sizeof(g_metrics));
    g_pool_offset = 0;
    g_next_header_rc = NGX_OK;
    g_next_body_rc = NGX_OK;
    g_next_header_calls = 0;
    g_next_body_calls = 0;
    g_restore_calls = 0;
    g_last_cleanup = NULL;
}

static void
test_preaccess_handler_installs_durable_bypass(void)
{
    ngx_http_request_t request = make_request();
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_ctx_t *ctx;
    ngx_chain_t pending_chain;
    ngx_int_t rc;

    request.main = &request;
    reset_test_state();
    memset(&conf, 0, sizeof(conf));
    conf.enabled = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    conf.error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;
    g_conf = &conf;
    g_pool_offset = 0;
    request.ctx[ngx_http_markdown_filter_module.ctx_index] = NULL;

    rc = ngx_http_markdown_preaccess_handler(&request);
    TEST_ASSERT(rc == NGX_DECLINED,
                "preaccess capture fail-open must continue the request");
    ctx = (ngx_http_markdown_ctx_t *)
        request.ctx[ngx_http_markdown_filter_module.ctx_index];
    TEST_ASSERT(ctx != NULL,
                "preaccess capture failure must install its context");
    TEST_ASSERT(ctx->eligible == 0 && ctx->lifecycle.bypass == 1,
                "preaccess handler must install an ineligible durable bypass");
    TEST_ASSERT(ctx->filter_enabled == 1,
                "body filter must retain the enabled decision");
    TEST_ASSERT(ctx->effective_conf != NULL
                && ctx->effective_conf->error_policy
                    == NGX_HTTP_MARKDOWN_ON_ERROR_PASS,
                "bypass must retain the request effective error policy");
    TEST_ASSERT(ctx->error.has_category
                && ctx->error.last_category
                    == NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
                "capture failure must remain classified as a system error");
    TEST_ASSERT(ctx->lifecycle.header_filter_initialized == 0,
                "bypass must not initialize conversion state");

    g_next_header_rc = NGX_AGAIN;
    g_next_header_calls = 0;
    g_restore_calls = 0;
    ngx_http_next_header_filter = test_next_header_filter;
    rc = ngx_http_markdown_header_filter(&request);
    TEST_ASSERT(rc == NGX_AGAIN,
                "header filter must forward the original response");
    TEST_ASSERT(g_next_header_calls == 1 && g_restore_calls == 1,
                "header filter must forward and restore headers once");
    TEST_ASSERT(ctx->eligible == 0 && ctx->lifecycle.bypass == 1,
                "header filter must preserve durable ineligible state");
    TEST_ASSERT(ctx->lifecycle.header_filter_initialized == 0
                && ctx->buffer_initialized == 0
                && ctx->conversion.attempted == 0
                && ctx->decompression.needed == 0
                && ctx->effective_conf != NULL,
                "header bypass must skip conversion preparation");
    TEST_ASSERT(ctx->headers_forwarded == 1,
                "header backpressure must latch forwarded headers");
    TEST_ASSERT(g_metrics.results.failopen_count == 0
                && ctx->fullbuffer.failopen_delivery_pending == 1,
                "NGX_AGAIN must defer fail-open delivery accounting");

    g_next_header_rc = NGX_ERROR;
    rc = ngx_http_markdown_header_filter(&request);
    TEST_ASSERT(rc == NGX_OK && g_next_header_calls == 1,
                "header re-entry must not forward headers twice");

    g_next_body_rc = NGX_AGAIN;
    g_next_body_calls = 0;
    ngx_http_next_body_filter = test_next_body_filter;
    rc = ngx_http_markdown_body_filter(&request, NULL);
    TEST_ASSERT(rc == NGX_AGAIN && g_next_body_calls == 1,
                "body pass-through must expose downstream backpressure");
    TEST_ASSERT(g_metrics.results.failopen_count == 0
                && ctx->fullbuffer.failopen_delivery_pending == 1,
                "body NGX_AGAIN must keep fail-open delivery pending");

    g_next_body_rc = NGX_OK;
    rc = ngx_http_markdown_body_filter(&request, NULL);
    TEST_ASSERT(rc == NGX_OK && g_next_body_calls == 2,
                "body pass-through must settle after downstream NGX_OK");
    TEST_ASSERT(g_metrics.results.failopen_count == 1
                && ctx->failopen_completed == 1
                && ctx->fullbuffer.failopen_delivery_pending == 0,
                "successful body delivery must account fail-open exactly once");

    g_next_body_rc = NGX_DONE;
    rc = ngx_http_markdown_body_filter(&request, NULL);
    TEST_ASSERT(rc == NGX_DONE && g_next_body_calls == 3,
                "body pass-through must return downstream NGX_DONE");
    TEST_ASSERT(g_metrics.results.failopen_count == 1,
                "a second terminal body delivery must not double count");

    memset(&pending_chain, 0, sizeof(pending_chain));
    ctx->fullbuffer.pending_output = &pending_chain;
    ctx->fullbuffer.pending_has_data = 1;
    ctx->fullbuffer.failopen_delivery_pending = 1;
    TEST_ASSERT(g_last_cleanup != NULL && g_last_cleanup->handler != NULL,
                "bypass must register request cleanup");
    g_last_cleanup->handler(g_last_cleanup->data);
    TEST_ASSERT(ctx->fullbuffer.pending_output == NULL
                && ctx->fullbuffer.pending_has_data == 0
                && ctx->fullbuffer.failopen_delivery_pending == 0,
                "request cleanup must release pending bypass state");
    TEST_PASS("preaccess handler installs durable bypass on capture failure");
}

static void
test_header_filter_bypass_forwards_once(void)
{
    ngx_http_request_t request = make_request();
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    request.main = &request;
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    g_conf = &conf;
    ctx.request = &request;
    ctx.filter_enabled = 1;
    ctx.eligible = 0;
    ctx.lifecycle.bypass = 1;
    request.ctx[ngx_http_markdown_filter_module.ctx_index] = &ctx;
    g_next_header_rc = NGX_AGAIN;
    g_next_header_calls = 0;
    g_restore_calls = 0;
    ngx_http_next_header_filter = test_next_header_filter;

    rc = ngx_http_markdown_header_filter(&request);
    TEST_ASSERT(rc == NGX_AGAIN,
                "bypass must return downstream header backpressure");
    TEST_ASSERT(g_next_header_calls == 1,
                "bypass must forward headers exactly once");
    TEST_ASSERT(g_restore_calls == 1,
                "bypass must restore captured conditional headers");
    TEST_ASSERT(ctx.eligible == 0,
                "header bypass must preserve ineligible state");
    TEST_ASSERT(ctx.lifecycle.bypass == 1,
                "header bypass must preserve durable state");
    TEST_ASSERT(ctx.lifecycle.header_filter_initialized == 0,
                "header bypass must skip init_ctx");
    TEST_ASSERT(ctx.headers_forwarded == 1,
                "NGX_AGAIN must latch forwarded headers for re-entry");

    g_next_header_rc = NGX_ERROR;
    rc = ngx_http_markdown_header_filter(&request);
    TEST_ASSERT(rc == NGX_OK,
                "bypass re-entry must be an idempotent no-op");
    TEST_ASSERT(g_next_header_calls == 1,
                "bypass re-entry must not forward headers twice");
    TEST_ASSERT(ctx.eligible == 0,
                "bypass re-entry must preserve ineligible state");
    TEST_PASS("header filter durable bypass forwards once and survives re-entry");
}

static void
test_preaccess_bypass_terminal_header_outcomes(void)
{
    ngx_http_request_t request;
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_ctx_t *ctx;
    ngx_int_t rc;

    memset(&conf, 0, sizeof(conf));
    conf.enabled = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    conf.error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;
    g_conf = &conf;

    request = make_request();
    request.main = &request;
    reset_test_state();
    rc = ngx_http_markdown_preaccess_handler(&request);
    TEST_ASSERT(rc == NGX_DECLINED,
                "preaccess must prepare the fail-open OK header case");
    ctx = (ngx_http_markdown_ctx_t *)
        request.ctx[ngx_http_markdown_filter_module.ctx_index];
    TEST_ASSERT(ctx != NULL && ctx->fullbuffer.failopen_delivery_pending == 1,
                "OK header case must start with a pending delivery latch");

    g_next_header_rc = NGX_OK;
    ngx_http_next_header_filter = test_next_header_filter;
    rc = ngx_http_markdown_header_filter(&request);
    TEST_ASSERT(rc == NGX_OK && g_next_header_calls == 1,
                "header bypass must return downstream NGX_OK");
    TEST_ASSERT(g_metrics.results.failopen_count == 1
                && ctx->failopen_completed == 1
                && ctx->fullbuffer.failopen_delivery_pending == 0,
                "NGX_OK header delivery must settle fail-open once");

    g_next_body_rc = NGX_ERROR;
    g_next_body_calls = 0;
    ngx_http_next_body_filter = test_next_body_filter;
    rc = ngx_http_markdown_body_filter(&request, NULL);
    TEST_ASSERT(rc == NGX_ERROR && g_next_body_calls == 1,
                "body delivery errors must be returned to the caller");
    TEST_ASSERT(g_metrics.results.failopen_count == 1,
                "body delivery errors must not duplicate fail-open accounting");

    request = make_request();
    request.main = &request;
    reset_test_state();
    rc = ngx_http_markdown_preaccess_handler(&request);
    TEST_ASSERT(rc == NGX_DECLINED,
                "preaccess must prepare the fail-open DONE header case");
    ctx = (ngx_http_markdown_ctx_t *)
        request.ctx[ngx_http_markdown_filter_module.ctx_index];
    TEST_ASSERT(ctx != NULL,
                "DONE header case must install a request context");

    g_next_header_rc = NGX_DONE;
    ngx_http_next_header_filter = test_next_header_filter;
    rc = ngx_http_markdown_header_filter(&request);
    TEST_ASSERT(rc == NGX_DONE,
                "header bypass must return downstream NGX_DONE");
    TEST_ASSERT(g_metrics.results.failopen_count == 1
                && ctx->failopen_completed == 1
                && ctx->fullbuffer.failopen_delivery_pending == 0,
                "NGX_DONE header delivery must settle fail-open once");
    TEST_PASS("preaccess bypass settles NGX_OK and NGX_DONE headers");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("request_bypass_production Tests\n");
    printf("========================================\n");

    g_conf = NULL;
    test_preaccess_handler_installs_durable_bypass();
    test_header_filter_bypass_forwards_once();
    test_preaccess_bypass_terminal_header_outcomes();

    printf("\n========================================\n");
    printf("All request bypass tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
