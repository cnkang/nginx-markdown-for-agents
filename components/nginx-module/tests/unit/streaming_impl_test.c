/*
 * Test: streaming_impl
 *
 * Unit tests for the NGINX markdown filter module's streaming implementation
 * helpers (ngx_http_markdown_streaming_impl.h).  Provides direct branch
 * coverage for path selection, header updates, output sending, backpressure,
 * commit/finalize lifecycle, fail-open passthrough, and cleanup paths.
 *
 * When MARKDOWN_STREAMING_ENABLED is not defined, the test binary compiles
 * to a skip stub that reports the missing feature and exits successfully.
 *
 * Otherwise, the file reimplements a minimal subset of NGINX runtime types
 * and API stubs so the streaming implementation header can be included and
 * exercised without linking against the full NGINX core library.
 */

#include "../include/test_common.h"
#include <ctype.h>
#include <limits.h>
#include <time.h>

#include "../../src/ngx_http_markdown_filter_module.h"
#include <markdown_converter.h>

#ifndef MARKDOWN_STREAMING_ENABLED

/*
 * Skip stub entry point.  When streaming is disabled at compile time,
 * print a skip banner and return 0 so the test harness records success
 * without exercising any streaming code paths.
 */
int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_impl Tests (SKIPPED)\n");
    printf("MARKDOWN_STREAMING_ENABLED not defined\n");
    printf("========================================\n\n");
    return 0;
}

#else  /* MARKDOWN_STREAMING_ENABLED — main test body follows */

/*
 * DIVERGENCE RISK:
 * This unit reimplements a minimal subset of NGINX runtime structs
 * (ngx_connection_t, ngx_buf_t, ngx_chain_t, ngx_http_request_t,
 * ngx_http_headers_in_t, ngx_http_headers_out_t, ngx_pool_t) so
 * ngx_http_markdown_streaming_impl.h can run without linking nginx.
 *
 * Contract we preserve from production:
 * - connection->log/read/error and request->buffered/method/header_only
 * - buffer flags used by streaming send paths (last_buf/flush/memory)
 * - header fields read or mutated by streaming logic
 * - pool cleanup chaining behavior used by teardown tests
 *
 * Fields not used by the streaming helper paths are intentionally omitted.
 * If production structs or streaming invariants change, update these stubs
 * and extend tests in the same change set.
 */
typedef struct ngx_list_part_s ngx_list_part_t;
typedef struct ngx_table_elt_s ngx_table_elt_t;
typedef struct ngx_http_headers_in_s ngx_http_headers_in_t;
typedef struct ngx_http_headers_out_s ngx_http_headers_out_t;
typedef struct ngx_http_core_srv_conf_s ngx_http_core_srv_conf_t;
typedef struct ngx_connection_s ngx_connection_t;
typedef struct ngx_log_s ngx_log_t;
typedef struct ngx_pool_s ngx_pool_t;
typedef struct ngx_pool_cleanup_s ngx_pool_cleanup_t;
typedef struct ngx_time_s ngx_time_t;
typedef struct ngx_event_s ngx_event_t;

/*
 * Minimal ngx_event_s stub.  Only the pending_eof bit-field is retained
 * because the streaming abort-check path reads it via connection->read.
 * Divergence risk: if production adds event fields used by streaming,
 * this stub must be extended.
 */
struct ngx_event_s {
    unsigned pending_eof:1;
};

/*
 * Minimal ngx_list_part_s stub.  Retains elts/nelts/next for header list
 * traversal.  Divergence risk: if streaming inspects other list-part
 * fields, this stub must be extended.
 */
struct ngx_list_part_s {
    void            *elts;
    ngx_uint_t       nelts;
    ngx_list_part_t *next;
};

/*
 * Minimal ngx_list_t wrapper.  Only the first part is needed for header
 * iteration in streaming paths.
 */
typedef struct {
    ngx_list_part_t part;
} ngx_list_t;

/*
 * Minimal ngx_table_elt_s stub.  Retains key/value/hash fields used by
 * header comparison logic in streaming paths.
 */
struct ngx_table_elt_s {
    ngx_str_t key;
    ngx_str_t value;
    ngx_uint_t hash;
};

/* Sentinel log struct; no fields are read by streaming code under test. */
struct ngx_log_s {
    int dummy;
};

/*
 * Minimal ngx_connection_s stub.  Retains log/read/error fields used by
 * the streaming abort-check and logging paths.
 * Divergence risk: if streaming reads additional connection fields
 * (e.g. fd, sent, buffered), this stub must be extended.
 */
struct ngx_connection_s {
    ngx_log_t   *log;
    ngx_event_t *read;
    unsigned     error:1;
};

/*
 * Minimal ngx_pool_cleanup_s stub.  Retains handler/data/next for pool
 * cleanup chaining used by streaming teardown tests.
 * Divergence risk: if production adds cleanup fields inspected by
 * streaming, this stub must be extended.
 */
struct ngx_pool_cleanup_s {
    void               (*handler)(void *data);
    void                *data;
    ngx_pool_cleanup_t  *next;
};

/* Minimal pool stub; only the cleanups list head is needed for teardown. */
struct ngx_pool_s {
    ngx_pool_cleanup_t *cleanups;
};

/*
 * Minimal ngx_buf_s stub.  Retains pos/last/start/end pointers and the
 * temporary/memory/last_buf/last_in_chain/flush/sync bit-fields used by
 * the streaming send and cleanup paths.
 * Divergence risk: if streaming inspects additional buffer flags
 * (e.g. recycled, file, in_file), this stub must be extended.
 */
struct ngx_buf_s {
    u_char   *pos;
    u_char   *last;
    u_char   *start;
    u_char   *end;
    unsigned  temporary:1;
    unsigned  memory:1;
    unsigned  last_buf:1;
    unsigned  last_in_chain:1;
    unsigned  flush:1;
    unsigned  sync:1;
};

/* Minimal chain node; buf/next are used throughout streaming send paths. */
struct ngx_chain_s {
    ngx_buf_t   *buf;
    ngx_chain_t *next;
};

/* Minimal array struct for stream_types exclusion list in path selection. */
struct ngx_array_s {
    void      *elts;
    ngx_uint_t nelts;
};

/*
 * Minimal request headers-in stub.  Retains headers list and server
 * string used by streaming conditional-request checks.
 */
struct ngx_http_headers_in_s {
    ngx_list_t headers;
    ngx_str_t  server;
};

/*
 * Minimal response headers-out stub.  Retains content_type, content_length_n,
 * status, content_type_len, and charset fields read or mutated by the
 * streaming header-update path.
 */
struct ngx_http_headers_out_s {
    ngx_str_t   content_type;
    off_t       content_length_n;
    ngx_uint_t  status;
    size_t      content_type_len;
    ngx_str_t   charset;
};

/* Minimal server config stub; only server_name is retained for logging. */
struct ngx_http_core_srv_conf_s {
    ngx_str_t server_name;
};

/*
 * Minimal ngx_http_request_s stub.  Retains connection/pool/method/
 * header_only/buffered/headers_in/headers_out/uri/loc_conf/ctx/main fields
 * that are read or mutated by streaming helper paths.
 * Divergence risk: if production streaming reads additional request fields
 * (e.g. count, subrequests, postponed), this stub must be extended.
 */
struct ngx_http_request_s {
    ngx_connection_t      *connection;
    ngx_pool_t            *pool;
    ngx_uint_t             method;
    ngx_uint_t             header_only;
    ngx_uint_t             buffered;
    ngx_http_headers_in_t  headers_in;
    ngx_http_headers_out_t headers_out;
    ngx_str_t              uri;
    void                  *loc_conf;
    void                  *ctx;
    ngx_http_request_t    *main;
};

/* Sentinel module struct; not inspected by streaming code under test. */
struct ngx_module_s {
    int dummy;
};

/* Sentinel conf struct; not inspected by streaming code under test. */
struct ngx_conf_s {
    int dummy;
};

/* Sentinel complex-value struct; only the pointer existence is tested. */
struct ngx_http_complex_value_s {
    int dummy;
};

/* Time struct used by ngx_timeofday() stub for deterministic timing. */
struct ngx_time_s {
    time_t    sec;
    ngx_msec_t msec;
};

/*
 * NGINX return-code and constant macros.  Guarded with #ifndef so the
 * values from test_common.h or production headers take precedence when
 * available.  These define the minimal set needed by streaming_impl.h.
 */
#ifndef NGX_OK
#define NGX_OK 0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR (-1)
#endif
#ifndef NGX_AGAIN
#define NGX_AGAIN (-2)
#endif
#ifndef NGX_DONE
#define NGX_DONE (-4)
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
#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif
#ifndef NGX_LOG_ERR
#define NGX_LOG_ERR 1
#endif
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN 2
#endif
#ifndef NGX_LOG_INFO
#define NGX_LOG_INFO 3
#endif
/* Streaming-specific and utility macros for constants and memory ops. */
#ifndef NGX_HTTP_MARKDOWN_BUFFERED
#define NGX_HTTP_MARKDOWN_BUFFERED 0x08
#endif
#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE SIZE_MAX
#endif

#ifndef ngx_memzero
#define ngx_memzero(buf, n) memset((buf), 0, (n))
#endif
#ifndef ngx_memcpy
#define ngx_memcpy memcpy
#endif

/*
 * Global stub control variables.  Each g_* variable configures the
 * corresponding stub's return code or behaviour so tests can inject
 * success, failure, or scripted sequences without modifying stub code.
 *
 * Return-code globals: set before a test to make the stub return that
 * code on the next call.
 *
 * Counter globals: incremented by stubs on each invocation; tests read
 * them to verify call counts.
 *
 * Fail-once globals: when non-zero, the stub returns NULL/failure once
 * and then auto-clears, simulating a transient allocation failure.
 */
static ngx_int_t g_next_body_filter_rc = NGX_OK;
static ngx_int_t g_next_header_filter_rc = NGX_OK;
static ngx_int_t g_complex_value_rc = NGX_OK;
static ngx_int_t g_add_vary_rc = NGX_OK;
static ngx_int_t g_set_etag_rc = NGX_OK;
static ngx_int_t g_buffer_init_rc = NGX_OK;
static ngx_int_t g_buffer_append_rc = NGX_OK;
static ngx_int_t g_forward_headers_rc = NGX_OK;
static ngx_int_t g_body_filter_rc = NGX_OK;
static ngx_int_t g_prepare_options_rc = NGX_OK;
static uint32_t g_new_with_code_rc = ERROR_SUCCESS;
static ngx_flag_t g_new_with_code_null_handle = 0;
static ngx_uint_t g_remove_content_encoding_calls = 0;
static ngx_uint_t g_abort_calls = 0;
static ngx_uint_t g_output_free_calls = 0;
static ngx_uint_t g_log_decision_calls = 0;
static ngx_uint_t g_palloc_fail_once = 0;
static ngx_uint_t g_pcalloc_fail_once = 0;
static ngx_uint_t g_alloc_chain_fail_once = 0;
static ngx_uint_t g_calloc_buf_fail_once = 0;
static ngx_uint_t g_pool_cleanup_fail_once = 0;
static ngx_str_t g_complex_value = { 2, (u_char *) "on" };
static ngx_time_t g_now = { 100, 0 };
static ngx_http_markdown_ctx_t *g_ctx = NULL;
static ngx_int_t g_next_body_filter_seq[8];
static ngx_uint_t g_next_body_filter_seq_len = 0;
static ngx_uint_t g_next_body_filter_seq_idx = 0;
static uint32_t g_streaming_feed_rc = ERROR_SUCCESS;
static u_char *g_streaming_feed_out_data = NULL;
static uintptr_t g_streaming_feed_out_len = 0;
static uint32_t g_streaming_finalize_rc = ERROR_SUCCESS;
static struct MarkdownResult g_streaming_finalize_result;

/*
 * Production globals that must be defined for the streaming impl header to
 * link.  ngx_http_markdown_content_type provides the module's content-type
 * literal; ngx_http_markdown_metrics is the optional metrics collector
 * (NULL by default, tests bind it to stack-local storage); the filter
 * module struct is a sentinel.
 */
u_char ngx_http_markdown_content_type[] =
    NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL;
ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;
ngx_module_t ngx_http_markdown_filter_module = { 0 };

/*
 * Metric macros.  NGX_HTTP_MARKDOWN_METRIC_ADD conditionally adds a value
 * to a metrics struct field when the global metrics pointer is non-NULL.
 * NGX_HTTP_MARKDOWN_METRIC_INC is a convenience wrapper that adds 1.
 * These mirror the production macros so streaming code under test can
 * update metrics without modification.
 */
#define NGX_HTTP_MARKDOWN_METRIC_ADD(field, value)                                  \
    do {                                                                             \
        if (ngx_http_markdown_metrics != NULL) {                                     \
            ngx_http_markdown_metrics->field += (value);                             \
        }                                                                            \
    } while (0)

#define NGX_HTTP_MARKDOWN_METRIC_INC(field)                                          \
    NGX_HTTP_MARKDOWN_METRIC_ADD(field, 1)

/*
 * Logging macros (no-op stubs).  Production NGINX logging is replaced with
 * macros that suppress all output and silence compiler warnings about
 * unused parameters.  This avoids console noise during test runs while
 * keeping the streaming code's log calls compilable.
 */
#ifdef ngx_log_error
#undef ngx_log_error
#endif
#define ngx_log_error(level, log, err, fmt, ...)                                    \
    do {                                                                             \
        UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);                        \
    } while (0)

#ifdef ngx_log_debug0
#undef ngx_log_debug0
#endif
#define ngx_log_debug0(level, log, err, fmt)                                        \
    do {                                                                             \
        UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);                        \
    } while (0)

#ifdef ngx_log_debug1
#undef ngx_log_debug1
#endif
#define ngx_log_debug1(level, log, err, fmt, arg1)                                  \
    do {                                                                             \
        UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt); UNUSED(arg1);          \
    } while (0)

#ifdef ngx_log_debug3
#undef ngx_log_debug3
#endif
#define ngx_log_debug3(level, log, err, fmt, arg1, arg2, arg3)                      \
    do {                                                                             \
        UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);                        \
        UNUSED(arg1); UNUSED(arg2); UNUSED(arg3);                                    \
    } while (0)

#ifdef ngx_log_debug4
#undef ngx_log_debug4
#endif
#define ngx_log_debug4(level, log, err, fmt, arg1, arg2, arg3, arg4)                \
    do {                                                                             \
        UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);                        \
        UNUSED(arg1); UNUSED(arg2); UNUSED(arg3); UNUSED(arg4);                      \
    } while (0)

/*
 * Stub for ngx_http_next_body_filter.  Returns the next value from the
 * scripted sequence (g_next_body_filter_seq) if available, otherwise
 * returns g_next_body_filter_rc.  Ignores request and chain arguments.
 * Side effect: consumes one slot from the sequence on each call.
 */
static ngx_int_t
ngx_http_next_body_filter_stub(ngx_http_request_t *r, ngx_chain_t *in)
{
    UNUSED(r);
    UNUSED(in);
    if (g_next_body_filter_seq_idx < g_next_body_filter_seq_len) {
        return g_next_body_filter_seq[g_next_body_filter_seq_idx++];
    }
    return g_next_body_filter_rc;
}

/*
 * Stub for ngx_http_next_header_filter.  Always returns
 * g_next_header_filter_rc.  Ignores the request argument.
 */
static ngx_int_t
ngx_http_next_header_filter_stub(ngx_http_request_t *r)
{
    UNUSED(r);
    return g_next_header_filter_rc;
}

/*
 * DIVERGENCE RISK:
 * These stubs model the behavior of nginx core filter hooks
 * (ngx_http_next_body_filter / ngx_http_next_header_filter) for deterministic
 * unit control. Contract:
 * - return codes are forwarded exactly from configured globals
 * - body path can replay scripted responses via
 *   g_next_body_filter_seq/g_next_body_filter_seq_len in-order
 * - no hidden side effects beyond consuming one scripted slot
 *
 * If production filter chaining semantics or expected return-code handling
 * change, update this stub contract and the dependent tests together.
 */
ngx_int_t (*ngx_http_next_body_filter)(ngx_http_request_t *r, ngx_chain_t *in) =
    ngx_http_next_body_filter_stub;
ngx_int_t (*ngx_http_next_header_filter)(ngx_http_request_t *r) =
    ngx_http_next_header_filter_stub;

/*
 * Stub for ngx_palloc.  Delegates to malloc(3).  If g_palloc_fail_once is
 * set, returns NULL once and clears the flag, simulating a transient
 * allocation failure.
 * Divergence risk: production ngx_palloc uses the pool allocator; this
 * stub uses the C heap, so objects allocated here must be freed with
 * free(3), not pool-based cleanup.
 */
void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    if (g_palloc_fail_once) {
        g_palloc_fail_once = 0;
        return NULL;
    }
    return malloc(size);
}

/*
 * Stub for ngx_pcalloc.  Same as ngx_palloc but zero-initialises the
 * allocation via calloc(3).  Honours g_pcalloc_fail_once for one-shot
 * failure injection.
 */
void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    void *p;
    UNUSED(pool);
    if (g_pcalloc_fail_once) {
        g_pcalloc_fail_once = 0;
        return NULL;
    }
    p = calloc(1, size);
    return p;
}

/*
 * Stub for ngx_pnalloc.  Delegates directly to ngx_palloc; production
 * would use a separate non-padded path, but the distinction is irrelevant
 * for unit tests.
 */
void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    return ngx_palloc(pool, size);
}

/*
 * Stub for ngx_alloc.  Direct malloc(3) wrapper ignoring the log argument.
 * Used by streaming code for off-pool allocations.
 */
void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    UNUSED(log);
    return malloc(size);
}

/* Stub for ngx_free.  Direct free(3) wrapper. */
void
ngx_free(void *p)
{
    free(p);
}

/*
 * Stub for ngx_calloc_buf.  Allocates a zero-initialised ngx_buf_t via
 * calloc(3).  Honours g_calloc_buf_fail_once for one-shot failure
 * injection.
 * Divergence risk: production allocates from the pool; this stub uses
 * the C heap, so callers must free(3) the result.
 */
ngx_buf_t *
ngx_calloc_buf(ngx_pool_t *pool)
{
    UNUSED(pool);
    if (g_calloc_buf_fail_once) {
        g_calloc_buf_fail_once = 0;
        return NULL;
    }
    return calloc(1, sizeof(ngx_buf_t));
}

/*
 * Stub for ngx_alloc_chain_link.  Allocates a zero-initialised
 * ngx_chain_t via calloc(3).  Honours g_alloc_chain_fail_once for
 * one-shot failure injection.
 * Divergence risk: same as ngx_calloc_buf — C heap vs pool.
 */
ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    UNUSED(pool);
    if (g_alloc_chain_fail_once) {
        g_alloc_chain_fail_once = 0;
        return NULL;
    }
    return calloc(1, sizeof(ngx_chain_t));
}

/*
 * Stub for ngx_pool_cleanup_add.  Allocates a cleanup node, prepends it
 * to pool->cleanups, and returns it.  Returns NULL if pool is NULL or
 * on one-shot failure (g_pool_cleanup_fail_once).
 * Divergence risk: production also allocates a data buffer of the
 * requested size; this stub ignores the size parameter because no test
 * needs cleanup data.
 */
ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    ngx_pool_cleanup_t *cln;

    UNUSED(size);
    if (pool == NULL) {
        return NULL;
    }
    if (g_pool_cleanup_fail_once) {
        g_pool_cleanup_fail_once = 0;
        return NULL;
    }

    cln = calloc(1, sizeof(ngx_pool_cleanup_t));
    if (cln == NULL) {
        return NULL;
    }

    cln->next = pool->cleanups;
    pool->cleanups = cln;
    return cln;
}

/*
 * Stub for ngx_http_clear_content_length.  Sets
 * r->headers_out.content_length_n to -1, mirroring production behaviour.
 */
void
ngx_http_clear_content_length(ngx_http_request_t *r)
{
    r->headers_out.content_length_n = -1;
}

/*
 * Stub for ngx_http_complex_value.  Returns g_complex_value_rc; on
 * NGX_OK, copies g_complex_value into *val.
 * Divergence risk: production evaluates the complex value script against
 * the request; this stub returns a fixed string controlled by the test.
 */
ngx_int_t
ngx_http_complex_value(ngx_http_request_t *r,
    ngx_http_complex_value_t *cv, ngx_str_t *val)
{
    UNUSED(r);
    UNUSED(cv);
    if (g_complex_value_rc != NGX_OK) {
        return g_complex_value_rc;
    }
    *val = g_complex_value;
    return NGX_OK;
}

/*
 * Stub for ngx_strncasecmp.  Case-insensitive comparison of the first n
 * bytes, mirroring the production NGINX function.
 */
ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        int c1 = tolower((unsigned char) s1[i]);
        int c2 = tolower((unsigned char) s2[i]);
        if (c1 != c2) {
            return c1 - c2;
        }
    }
    return 0;
}

/*
 * Stub for ngx_timeofday.  Returns a pointer to the deterministic
 * g_now global so tests can control perceived time without touching
 * the system clock.
 */
ngx_time_t *
ngx_timeofday(void)
{
    return &g_now;
}

/*
 * Stub for ngx_http_get_module_ctx.  Returns r->ctx, mirroring the
 * production macro that retrieves per-module context.
 */
void *
ngx_http_get_module_ctx(ngx_http_request_t *r, ngx_module_t module)
{
    UNUSED(module);
    return r->ctx;
}

/*
 * Stub for ngx_http_get_module_loc_conf.  Returns r->loc_conf cast to
 * the conf type, mirroring the production macro.
 */
ngx_http_markdown_conf_t *
ngx_http_get_module_loc_conf(ngx_http_request_t *r, ngx_module_t module)
{
    UNUSED(module);
    return (ngx_http_markdown_conf_t *) r->loc_conf;
}

/*
 * Stub for ngx_http_set_ctx.  Sets r->ctx to the provided pointer,
 * mirroring the production macro.
 */
void
ngx_http_set_ctx(ngx_http_request_t *r, void *c, ngx_module_t module)
{
    UNUSED(module);
    r->ctx = c;
}

/*
 * Stub for markdown_streaming_abort.  Increments g_abort_calls so tests
 * can verify the abort was invoked.  Ignores the handle argument.
 */
void
markdown_streaming_abort(struct StreamingConverterHandle *handle)
{
    UNUSED(handle);
    g_abort_calls++;
}

/*
 * Stub for markdown_streaming_output_free.  Increments
 * g_output_free_calls so tests can verify the free was invoked.
 * Ignores data and len arguments.
 */
void
markdown_streaming_output_free(uint8_t *data, uintptr_t len)
{
    UNUSED(data);
    UNUSED(len);
    g_output_free_calls++;
}

/*
 * Stub for markdown_streaming_feed.  Returns g_streaming_feed_rc and
 * writes g_streaming_feed_out_data / g_streaming_feed_out_len into the
 * output parameters if they are non-NULL.  Ignores handle, html_chunk,
 * and chunk_len arguments.
 * Divergence risk: production performs actual streaming conversion; this
 * stub returns fixed test-controlled values.
 */
uint32_t
markdown_streaming_feed(struct StreamingConverterHandle *handle,
    const uint8_t *html_chunk, uintptr_t chunk_len,
    uint8_t **out_data, uintptr_t *out_len)
{
    UNUSED(handle);
    UNUSED(html_chunk);
    UNUSED(chunk_len);
    if (out_data != NULL) {
        *out_data = g_streaming_feed_out_data;
    }
    if (out_len != NULL) {
        *out_len = g_streaming_feed_out_len;
    }
    return g_streaming_feed_rc;
}

/*
 * Stub for markdown_streaming_finalize.  Returns g_streaming_finalize_rc
 * and copies g_streaming_finalize_result into *result if non-NULL.
 * Divergence risk: production finalizes the streaming converter; this
 * stub returns fixed test-controlled values.
 */
uint32_t
markdown_streaming_finalize(struct StreamingConverterHandle *handle,
    struct MarkdownResult *result)
{
    UNUSED(handle);
    if (result != NULL) {
        *result = g_streaming_finalize_result;
    }
    return g_streaming_finalize_rc;
}

/*
 * Stub for markdown_streaming_new_with_code.  Returns g_new_with_code_rc
 * and sets *out_handle to a sentinel pointer (0x1) unless
 * g_new_with_code_null_handle is set, which simulates a success return
 * with a NULL handle.
 * Divergence risk: production creates a real converter; this stub
 * returns a fixed sentinel.
 */
uint32_t
markdown_streaming_new_with_code(const struct MarkdownOptions *options,
    struct StreamingConverterHandle **out_handle)
{
    UNUSED(options);
    if (out_handle != NULL && !g_new_with_code_null_handle) {
        *out_handle = (struct StreamingConverterHandle *) (uintptr_t) 0x1;
    }
    return g_new_with_code_rc;
}

/*
 * Stub for markdown_result_free.  Zeroes the result struct to simulate
 * cleanup, mirroring production behaviour of releasing result resources.
 */
void
markdown_result_free(struct MarkdownResult *result)
{
    if (result != NULL) {
        ngx_memzero(result, sizeof(*result));
    }
}

/*
 * Stub for ngx_http_markdown_add_vary_accept.  Returns g_add_vary_rc.
 * Ignores the request argument.
 */
ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    UNUSED(r);
    return g_add_vary_rc;
}

/*
 * Stub for ngx_http_markdown_remove_content_encoding.  Increments
 * g_remove_content_encoding_calls so tests can verify invocation.
 * Ignores the request argument.
 */
void
ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r)
{
    UNUSED(r);
    g_remove_content_encoding_calls++;
}

/*
 * Stub for ngx_http_markdown_set_etag.  Returns g_set_etag_rc.
 * Ignores all arguments.
 */
ngx_int_t
ngx_http_markdown_set_etag(ngx_http_request_t *r,
    const u_char *etag, size_t etag_len)
{
    UNUSED(r);
    UNUSED(etag);
    UNUSED(etag_len);
    return g_set_etag_rc;
}

/*
 * Stub for ngx_http_markdown_forward_headers.  Returns g_forward_headers_rc.
 * Ignores request and context arguments.
 */
ngx_int_t
ngx_http_markdown_forward_headers(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    UNUSED(r);
    UNUSED(ctx);
    return g_forward_headers_rc;
}

/*
 * Stub for ngx_http_markdown_body_filter.  Returns g_body_filter_rc.
 * Ignores request and chain arguments.  Used by the reentry-after-fallback
 * tests.
 */
ngx_int_t
ngx_http_markdown_body_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    UNUSED(r);
    UNUSED(in);
    return g_body_filter_rc;
}

/*
 * Stub for ngx_http_markdown_buffer_init.  Allocates a data buffer via
 * malloc(3) and initialises the buffer struct.  Returns g_buffer_init_rc
 * if it is not NGX_OK; returns NGX_ERROR on malloc failure.
 * Divergence risk: production uses pool allocation; this stub uses the
 * C heap.
 */
ngx_int_t
ngx_http_markdown_buffer_init(ngx_http_markdown_buffer_t *buf, size_t max_size,
    ngx_pool_t *pool)
{
    UNUSED(pool);
    if (g_buffer_init_rc != NGX_OK) {
        return g_buffer_init_rc;
    }
    buf->data = malloc(max_size ? max_size : 64);
    if (buf->data == NULL) {
        return NGX_ERROR;
    }
    buf->size = 0;
    buf->capacity = max_size ? max_size : 64;
    buf->max_size = max_size;
    return NGX_OK;
}

/*
 * Stub for ngx_http_markdown_buffer_append.  Copies data into the buffer
 * if capacity permits.  Returns g_buffer_append_rc if not NGX_OK; returns
 * NGX_ERROR if buf->data is NULL or capacity is insufficient.
 * Divergence risk: production uses pool-based reallocation; this stub
 * performs a single memcpy and sets size = len.
 */
ngx_int_t
ngx_http_markdown_buffer_append(ngx_http_markdown_buffer_t *buf,
    const u_char *data, size_t len)
{
    if (g_buffer_append_rc != NGX_OK) {
        return g_buffer_append_rc;
    }
    if (buf->data == NULL || buf->capacity < len) {
        return NGX_ERROR;
    }
    if (len > 0) {
        ngx_memcpy(buf->data, data, len);
    }
    buf->size = len;
    return NGX_OK;
}

/*
 * Stub for ngx_http_markdown_prepare_conversion_options.  Returns
 * g_prepare_options_rc.  Ignores all arguments.
 */
ngx_int_t
ngx_http_markdown_prepare_conversion_options(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, struct MarkdownOptions *options)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(options);
    return g_prepare_options_rc;
}

/*
 * Reason-code accessor stubs.  Each returns a static ngx_str_t containing
 * the corresponding decision-reason literal.  These mirror the production
 * accessors used by ngx_http_markdown_log_decision.
 */
const ngx_str_t *
ngx_http_markdown_reason_streaming_convert(void)
{
    static ngx_str_t s = { sizeof("STREAMING_CONVERT") - 1,
        (u_char *) "STREAMING_CONVERT" };
    return &s;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_fail_postcommit(void)
{
    static ngx_str_t s = { sizeof("STREAMING_FAIL_POSTCOMMIT") - 1,
        (u_char *) "STREAMING_FAIL_POSTCOMMIT" };
    return &s;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_fallback(void)
{
    static ngx_str_t s = { sizeof("STREAMING_FALLBACK") - 1,
        (u_char *) "STREAMING_FALLBACK" };
    return &s;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_skip_unsupported(void)
{
    static ngx_str_t s = { sizeof("STREAMING_SKIP_UNSUPPORTED") - 1,
        (u_char *) "STREAMING_SKIP_UNSUPPORTED" };
    return &s;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_budget_exceeded(void)
{
    static ngx_str_t s = { sizeof("STREAMING_BUDGET_EXCEEDED") - 1,
        (u_char *) "STREAMING_BUDGET_EXCEEDED" };
    return &s;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_precommit_failopen(void)
{
    static ngx_str_t s = { sizeof("STREAMING_PRECOMMIT_FAILOPEN") - 1,
        (u_char *) "STREAMING_PRECOMMIT_FAILOPEN" };
    return &s;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_precommit_reject(void)
{
    static ngx_str_t s = { sizeof("STREAMING_PRECOMMIT_REJECT") - 1,
        (u_char *) "STREAMING_PRECOMMIT_REJECT" };
    return &s;
}

/*
 * Stub for ngx_http_markdown_log_decision.  Increments
 * g_log_decision_calls so tests can verify invocation.  Ignores all
 * arguments.
 */
void
ngx_http_markdown_log_decision(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, const ngx_str_t *reason_code)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(reason_code);
    g_log_decision_calls++;
}

/*
 * Include the streaming implementation header after all stubs are defined.
 * This pulls in the inline/static helper functions that are the actual
 * subjects under test, compiled against the stub NGINX runtime above.
 */
#include "../../src/ngx_http_markdown_streaming_impl.h"

/*
 * Reset all global stub control variables to their default (success)
 * state.  Must be called at the start of every test function to ensure
 * a clean slate with no residual state from a prior test.
 * Side effect: sets ngx_http_markdown_metrics to NULL to avoid dangling
 * pointers to stack-local metrics structs from previous tests.
 */
static void
reset_globals(void)
{
    g_next_body_filter_rc = NGX_OK;
    g_next_header_filter_rc = NGX_OK;
    g_complex_value_rc = NGX_OK;
    g_add_vary_rc = NGX_OK;
    g_set_etag_rc = NGX_OK;
    g_buffer_init_rc = NGX_OK;
    g_buffer_append_rc = NGX_OK;
    g_forward_headers_rc = NGX_OK;
    g_body_filter_rc = NGX_OK;
    g_prepare_options_rc = NGX_OK;
    g_new_with_code_rc = ERROR_SUCCESS;
    g_new_with_code_null_handle = 0;
    g_remove_content_encoding_calls = 0;
    g_abort_calls = 0;
    g_output_free_calls = 0;
    g_log_decision_calls = 0;
    g_palloc_fail_once = 0;
    g_pcalloc_fail_once = 0;
    g_alloc_chain_fail_once = 0;
    g_calloc_buf_fail_once = 0;
    g_pool_cleanup_fail_once = 0;
    g_complex_value = (ngx_str_t) { 2, (u_char *) "on" };
    g_now.sec = 100;
    g_now.msec = 0;
    g_ctx = NULL;
    g_next_body_filter_seq_len = 0;
    g_next_body_filter_seq_idx = 0;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    ngx_memzero(&g_streaming_finalize_result,
        sizeof(g_streaming_finalize_result));
    /*
     * Tests frequently bind ngx_http_markdown_metrics to stack-local
     * storage; always clear it here so later tests cannot read a stale
     * out-of-scope pointer.
     */
    ngx_http_markdown_metrics = NULL;
}

/*
 * Program a sequence of return codes for ngx_http_next_body_filter_stub.
 * Copies up to 8 entries from seq into g_next_body_filter_seq and resets
 * the sequence index to 0.  Subsequent body-filter calls will return
 * values from the sequence in order; once exhausted, the stub falls back
 * to g_next_body_filter_rc.
 *
 * Parameters:
 *   seq   - array of return codes to replay
 *   count - number of entries in seq (clamped to array capacity)
 */
static void
set_next_body_filter_sequence(const ngx_int_t *seq, ngx_uint_t count)
{
    ngx_uint_t i;

    if (count > (sizeof(g_next_body_filter_seq)
                 / sizeof(g_next_body_filter_seq[0])))
    {
        count = (sizeof(g_next_body_filter_seq)
                 / sizeof(g_next_body_filter_seq[0]));
    }

    for (i = 0; i < count; i++) {
        g_next_body_filter_seq[i] = seq[i];
    }
    g_next_body_filter_seq_len = count;
    g_next_body_filter_seq_idx = 0;
}

/*
 * Initialise a request, context, config, pool, connection, log, and
 * read-event struct to a consistent baseline state for streaming tests.
 * Zeroes all structs, then wires the cross-pointers:
 *   conn->log = log, conn->read = read_event,
 *   r->connection = conn, r->pool = pool, r->main = r,
 *   r->loc_conf = conf, r->ctx = ctx,
 *   ctx->request = r, ctx->processing_path = STREAMING.
 *
 * Parameters:
 *   r          - request to initialise
 *   ctx        - module context to initialise
 *   conf       - location config to initialise
 *   pool       - pool to initialise
 *   conn       - connection to initialise
 *   log        - log to initialise
 *   read_event - read event to initialise
 */
static void
init_request_ctx_conf(ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf, ngx_pool_t *pool, ngx_connection_t *conn,
    ngx_log_t *log, ngx_event_t *read_event)
{
    ngx_memzero(r, sizeof(*r));
    ngx_memzero(ctx, sizeof(*ctx));
    ngx_memzero(conf, sizeof(*conf));
    ngx_memzero(pool, sizeof(*pool));
    ngx_memzero(conn, sizeof(*conn));
    ngx_memzero(log, sizeof(*log));
    ngx_memzero(read_event, sizeof(*read_event));

    conn->log = log;
    conn->read = read_event;
    r->connection = conn;
    r->pool = pool;
    r->main = r;
    r->loc_conf = conf;
    r->ctx = ctx;

    ctx->request = r;
    ctx->processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
}

/*
 * Test streaming_cleanup paths.  Verifies that:
 * - cleanup with NULL context is safe (no-op)
 * - cleanup aborts the streaming handle and frees temporary pending
 *   buffers, then clears both the handle and pending_output pointers
 * Covers: ngx_http_markdown_streaming_cleanup
 */
static void
test_cleanup_paths(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_chain_t             cl;
    ngx_buf_t               b;

    TEST_SUBSECTION("cleanup aborts handle and frees pending buffers");
    reset_globals();
    ngx_http_markdown_streaming_cleanup(NULL);
    ngx_memzero(&ctx, sizeof(ctx));
    ngx_memzero(&cl, sizeof(cl));
    ngx_memzero(&b, sizeof(b));

    b.pos = (u_char *) "abc";
    b.last = b.pos + 3;
    b.temporary = 1;
    cl.buf = &b;
    cl.next = NULL;

    ctx.streaming.handle = (struct StreamingConverterHandle *) (uintptr_t) 0x1;
    ctx.streaming.pending_output = &cl;
    ngx_http_markdown_streaming_cleanup(&ctx);

    TEST_ASSERT(ctx.streaming.handle == NULL, "cleanup should clear handle");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "cleanup should clear pending chain");
    TEST_ASSERT(g_abort_calls == 1, "cleanup should abort streaming handle once");
    TEST_ASSERT(g_output_free_calls == 1,
        "cleanup should free temporary pending buffer");
    TEST_PASS("cleanup paths covered");
}

/*
 * Test select_processing_path routing logic.  Verifies that:
 * - engine=off routes to full-buffer
 * - HEAD method routes to full-buffer
 * - 304 status routes to full-buffer
 * - full_support conditional mode routes to full-buffer
 * - SSE content-type routes to full-buffer
 * - excluded stream_types prefix routes to full-buffer
 * - engine=on with no exclusions routes to streaming
 * - non-matching stream_types keeps streaming enabled
 * - NULL content-type data does not trigger exclusions
 * - if_modified_since_only conditional mode keeps streaming
 * - auto with small content-length routes to full-buffer
 * - auto without content-length routes to streaming
 * - complex-value failure routes to full-buffer
 * - invalid engine value routes to full-buffer
 * Covers: ngx_http_markdown_select_processing_path
 */
static void
test_select_processing_path(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_str_t               excluded[1];
    ngx_array_t             arr;
    ngx_uint_t              path;

    TEST_SUBSECTION("select_processing_path engine and skip branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);

    conf.streaming_engine = (ngx_http_complex_value_t *) (uintptr_t) 0x1;
    conf.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;
    conf.large_body_threshold = 1024;
    r.headers_out.content_type = (ngx_str_t) { 9, (u_char *) "text/html" };
    r.headers_out.content_length_n = 2048;

    g_complex_value = (ngx_str_t) { 3, (u_char *) "off" };
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "engine=off should route full-buffer");

    g_complex_value = (ngx_str_t) { 2, (u_char *) "on" };
    r.method = NGX_HTTP_HEAD;
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "HEAD should route full-buffer");
    r.method = 0;

    r.headers_out.status = NGX_HTTP_NOT_MODIFIED;
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "304 should route full-buffer");
    r.headers_out.status = 200;

    conf.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "full_support should route full-buffer");
    conf.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;

    r.headers_out.content_type = (ngx_str_t) { 17, (u_char *) "text/event-stream" };
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "SSE should route full-buffer");

    r.headers_out.content_type = (ngx_str_t) { 9, (u_char *) "text/html" };
    excluded[0] = (ngx_str_t) { 4, (u_char *) "text" };
    arr.elts = excluded;
    arr.nelts = 1;
    conf.stream_types = &arr;
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "excluded stream_types should route full-buffer");

    conf.stream_types = NULL;
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "engine=on should route streaming");

    excluded[0] = (ngx_str_t) { 11, (u_char *) "application" };
    arr.elts = excluded;
    arr.nelts = 1;
    conf.stream_types = &arr;
    r.headers_out.content_type = (ngx_str_t) { 9, (u_char *) "text/html" };
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "non-matching stream_types should keep streaming enabled");

    r.headers_out.content_type = (ngx_str_t) { 0, NULL };
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "NULL content-type data should not trigger exclusions");

    conf.stream_types = NULL;
    r.headers_out.content_type = (ngx_str_t) { 9, (u_char *) "text/html" };
    conf.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "if_modified_since_only should keep streaming path");
    conf.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;

    g_complex_value = (ngx_str_t) { 4, (u_char *) "auto" };
    r.headers_out.content_length_n = 10;
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "auto with small content-length should route full-buffer");

    r.headers_out.content_length_n = -1;
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "auto without content-length should route streaming");

    g_complex_value_rc = NGX_ERROR;
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "complex-value failure should route full-buffer");
    g_complex_value_rc = NGX_OK;

    g_complex_value = (ngx_str_t) { 3, (u_char *) "bad" };
    path = ngx_http_markdown_select_processing_path(&r, &conf);
    TEST_ASSERT(path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "invalid engine value should route full-buffer");

    TEST_PASS("path-selection branches covered");
}

/*
 * Test streaming_update_headers success and failure branches.  Verifies:
 * - successful header update sets markdown content-type, clears
 *   content-length, and removes content-encoding when decompression needed
 * - vary-accept add failure returns NGX_ERROR
 * - etag clear failure returns NGX_ERROR
 * Covers: ngx_http_markdown_streaming_update_headers
 */
static void
test_update_headers_paths(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_int_t               rc;

    TEST_SUBSECTION("update_headers success and failure branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);

    ctx.decompression.needed = 1;
    rc = ngx_http_markdown_streaming_update_headers(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "update_headers should succeed");
    TEST_ASSERT(r.headers_out.content_type.len == NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN,
        "content-type should be markdown");
    TEST_ASSERT(r.headers_out.content_length_n == -1,
        "content-length should be cleared");
    TEST_ASSERT(g_remove_content_encoding_calls == 1,
        "decompression path should remove content-encoding");

    g_add_vary_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_update_headers(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR, "vary add failure should return error");
    g_add_vary_rc = NGX_OK;

    g_set_etag_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_update_headers(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR, "etag clear failure should return error");

    TEST_PASS("update_headers branches covered");
}

/*
 * Test send_output and resume_pending backpressure branches.  Verifies:
 * - successful send increments flush counter and records TTFB on first send
 * - NGX_AGAIN from downstream stores pending output and sets buffered flag
 * - resume_pending drains pending chain and clears buffered flag
 * - resume with terminal metrics latch does not fail
 * - resume downstream error propagates and records post-commit failure
 * - deferred last_buf is sent on resume
 * Covers: ngx_http_markdown_streaming_send_output,
 *         ngx_http_markdown_streaming_resume_pending
 */
static void
test_send_output_and_resume_paths(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_http_markdown_metrics_t metrics;
    ngx_buf_t               b_ok;
    ngx_chain_t             c_ok;
    ngx_buf_t               b_err;
    ngx_chain_t             c_err;
    u_char                  data[] = "hello";
    ngx_int_t               rc;

    TEST_SUBSECTION("send_output and resume_pending backpressure branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    ctx.streaming.feed_start_ms = 10;
    g_now.sec = 11;
    g_now.msec = 0;

    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_send_output(
        &r, &ctx, data, sizeof(data) - 1, 0);
    TEST_ASSERT(rc == NGX_OK, "send_output should succeed");
    TEST_ASSERT(ctx.streaming.flushes_sent == 1,
        "flush counter should increment on success");
    TEST_ASSERT(ctx.streaming.ttfb_recorded == 1,
        "successful first send should record TTFB");

    ctx.streaming.feed_start_ms = 20;
    ctx.streaming.ttfb_recorded = 0;
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_send_output(
        &r, &ctx, data, sizeof(data) - 1, 0);
    TEST_ASSERT(rc == NGX_AGAIN, "send_output should propagate NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "NGX_AGAIN should store pending output");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "NGX_AGAIN should set buffered flag");

    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "resume_pending should drain pending chain");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "resume should clear pending chain");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) == 0,
        "resume should clear buffered flag");

    ngx_memzero(&b_ok, sizeof(b_ok));
    ngx_memzero(&c_ok, sizeof(c_ok));
    c_ok.buf = &b_ok;
    c_ok.next = NULL;
    ctx.streaming.pending_terminal_metrics = 1;
    ctx.streaming.pending_output = &c_ok;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "terminal metric latch should not fail resume");
    TEST_ASSERT(ctx.streaming.pending_terminal_metrics == 0,
        "resume should clear pending terminal metrics");

    ngx_memzero(&b_err, sizeof(b_err));
    ngx_memzero(&c_err, sizeof(c_err));
    c_err.buf = &b_err;
    c_err.next = NULL;
    ctx.streaming.pending_output = &c_err;
    g_next_body_filter_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR, "resume should propagate downstream error");
    TEST_ASSERT(ctx.streaming.failure_recorded == 1,
        "resume error should record post-commit failure");

    ctx.streaming.finalize_pending_lastbuf = 1;
    ctx.streaming.pending_output = NULL;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "deferred last_buf should be sent on resume");

    TEST_PASS("send_output/resume_pending branches covered");
}

/*
 * Test send_output allocation errors and deferred-lastbuf branches.  Verifies:
 * - buffer allocation failure returns NGX_ERROR
 * - payload copy allocation failure returns NGX_ERROR
 * - chain-link allocation failure returns NGX_ERROR
 * - backpressure helper returns NGX_AGAIN and sets buffered flag
 * - deferred last_buf propagates backpressure and latches terminal metrics
 * - deferred last_buf propagates hard downstream errors and records failure
 * Covers: ngx_http_markdown_streaming_send_output,
 *         ngx_http_markdown_streaming_handle_backpressure,
 *         ngx_http_markdown_streaming_send_deferred_lastbuf
 */
static void
test_send_output_error_and_deferred_paths(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_http_markdown_metrics_t metrics;
    u_char                  data[] = "hello";
    ngx_int_t               rc;

    TEST_SUBSECTION("send_output allocation errors and deferred-lastbuf branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    g_calloc_buf_fail_once = 1;
    rc = ngx_http_markdown_streaming_send_output(
        &r, &ctx, data, sizeof(data) - 1, 0);
    TEST_ASSERT(rc == NGX_ERROR,
        "send_output should fail when buffer allocation fails");

    g_palloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_send_output(
        &r, &ctx, data, sizeof(data) - 1, 0);
    TEST_ASSERT(rc == NGX_ERROR,
        "send_output should fail when payload copy allocation fails");

    g_alloc_chain_fail_once = 1;
    rc = ngx_http_markdown_streaming_send_output(
        &r, &ctx, NULL, 0, 0);
    TEST_ASSERT(rc == NGX_ERROR,
        "send_output should fail when chain-link allocation fails");

    r.buffered = 0;
    rc = ngx_http_markdown_streaming_handle_backpressure(&r, &ctx);
    TEST_ASSERT(rc == NGX_AGAIN,
        "backpressure helper should return NGX_AGAIN");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "backpressure helper should set buffered flag");

    ctx.streaming.failure_recorded = 0;
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_send_deferred_lastbuf(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_AGAIN,
        "deferred last_buf should propagate backpressure");
    TEST_ASSERT(ctx.streaming.pending_terminal_metrics == 1,
        "deferred last_buf backpressure should latch terminal metrics");

    ctx.streaming.pending_terminal_metrics = 0;
    ctx.streaming.failure_recorded = 0;
    g_next_body_filter_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_send_deferred_lastbuf(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "deferred last_buf should propagate hard downstream errors");
    TEST_ASSERT(ctx.streaming.failure_recorded == 1,
        "deferred last_buf hard error should record postcommit failure");

    TEST_PASS("send_output error/deferred branches covered");
}

/*
 * Test fallback_to_fullbuffer success and error branches.  Verifies:
 * - fallback returns NGX_DECLINED, switches to full-buffer path,
 *   clears conversion_attempted, initialises main buffer, and marks
 *   decompression done
 * - buffer_init failure propagates NGX_ERROR
 * Covers: ngx_http_markdown_streaming_fallback_to_fullbuffer
 */
static void
test_fallback_to_fullbuffer_paths(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_http_markdown_metrics_t metrics;
    u_char                  prebuf[] = "xyz";
    ngx_int_t               rc;

    TEST_SUBSECTION("fallback_to_fullbuffer success and error branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    conf.max_size = 64;
    ctx.conversion_attempted = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *) (uintptr_t) 0x2;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf;
    ctx.streaming.prebuffer.size = sizeof(prebuf);
    ngx_http_markdown_metrics->path_hits.streaming = 2;

    rc = ngx_http_markdown_streaming_fallback_to_fullbuffer(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED, "fallback should return NGX_DECLINED");
    TEST_ASSERT(ctx.processing_path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "fallback should switch to full-buffer path");
    TEST_ASSERT(ctx.conversion_attempted == 0,
        "fallback should clear conversion_attempted");
    TEST_ASSERT(ctx.buffer_initialized == 1,
        "fallback should initialize main buffer");
    TEST_ASSERT(ctx.decompression.done == 1,
        "fallback should mark decompression done");

    ctx.buffer_initialized = 0;
    g_buffer_init_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_fallback_to_fullbuffer(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "buffer_init failure should propagate NGX_ERROR");

    TEST_PASS("fallback branches covered");
}

/*
 * Test postcommit and precommit error policy branches.  Verifies:
 * - postcommit error sends terminal chunk and records failure once;
 *   memory-limit errors are classified as budget-exceeded
 * - precommit reject policy returns NGX_ERROR (fail-closed)
 * - precommit pass policy returns NGX_DECLINED (fail-open) and marks
 *   request ineligible
 * - streaming fallback error routes through fallback path
 * Covers: ngx_http_markdown_streaming_handle_postcommit_error,
 *         ngx_http_markdown_streaming_precommit_error
 */
static void
test_postcommit_and_precommit_error_paths(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_http_markdown_metrics_t metrics;
    ngx_int_t               rc;

    TEST_SUBSECTION("postcommit/precommit error policy branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    ctx.streaming.handle = (struct StreamingConverterHandle *) (uintptr_t) 0x3;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_postcommit_error(
        &r, &ctx, &conf, ERROR_MEMORY_LIMIT);
    TEST_ASSERT(rc == NGX_OK, "postcommit error should send terminal chunk");
    TEST_ASSERT(ctx.streaming.failure_recorded == 1,
        "postcommit error should record failure once");
    TEST_ASSERT(metrics.streaming.budget_exceeded_total == 1,
        "memory-limit postcommit should classify budget exceeded");

    ctx.streaming.failure_recorded = 0;
    ctx.streaming.handle = (struct StreamingConverterHandle *) (uintptr_t) 0x4;
    conf.streaming_on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT;
    rc = ngx_http_markdown_streaming_precommit_error(
        &r, &ctx, &conf, ERROR_INTERNAL);
    TEST_ASSERT(rc == NGX_ERROR, "precommit reject policy should fail-closed");
    TEST_ASSERT(metrics.streaming.precommit_reject_total == 1,
        "precommit reject metric should increment");

    conf.streaming_on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS;
    ctx.eligible = 1;
    rc = ngx_http_markdown_streaming_precommit_error(
        &r, &ctx, &conf, ERROR_MEMORY_LIMIT);
    TEST_ASSERT(rc == NGX_DECLINED, "precommit pass policy should fail-open");
    TEST_ASSERT(ctx.eligible == 0, "precommit fail-open should mark ineligible");
    TEST_ASSERT(metrics.streaming.precommit_failopen_total == 1,
        "precommit fail-open metric should increment");

    rc = ngx_http_markdown_streaming_precommit_error(
        &r, &ctx, &conf, ERROR_STREAMING_FALLBACK);
    TEST_ASSERT(rc == NGX_DECLINED,
        "streaming fallback error should route through fallback");

    TEST_PASS("postcommit/precommit branches covered");
}

/*
 * Test abort, passthrough, and reentry helper branches.  Verifies:
 * - no connection error continues (returns 0)
 * - connection error aborts request and clears streaming handle
 * - passthrough forwards headers then body on first call
 * - passthrough fails when header forwarding fails
 * - passthrough calls next body filter when headers already forwarded
 * - reentry passes cl->next to full-buffer body filter
 * - reentry fails on terminal buffer or chain allocation failure
 * - reentry synthesises terminal buffer when needed
 * Covers: ngx_http_markdown_streaming_check_client_abort,
 *         ngx_http_markdown_streaming_passthrough,
 *         ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback
 */
static void
test_abort_passthrough_and_reentry_helpers(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_chain_t             in;
    ngx_buf_t               in_buf;
    ngx_chain_t             cur;
    ngx_int_t               rc;

    TEST_SUBSECTION("abort, passthrough, and reentry helpers");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&in_buf, sizeof(in_buf));
    ngx_memzero(&cur, sizeof(cur));
    in.buf = &in_buf;
    cur.next = &in;

    rc = ngx_http_markdown_streaming_check_client_abort(&r, &ctx);
    TEST_ASSERT(rc == 0, "no connection error should continue");

    r.connection->error = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *) (uintptr_t) 0x5;
    rc = ngx_http_markdown_streaming_check_client_abort(&r, &ctx);
    TEST_ASSERT(rc == NGX_ERROR, "connection error should abort request");
    TEST_ASSERT(ctx.streaming.handle == NULL,
        "client abort should clear streaming handle");
    r.connection->error = 0;

    g_next_body_filter_rc = NGX_OK;
    ctx.headers_forwarded = 0;
    g_forward_headers_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_passthrough(&r, &ctx, &in);
    TEST_ASSERT(rc == NGX_OK, "passthrough should forward headers then body");

    ctx.headers_forwarded = 0;
    g_forward_headers_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_passthrough(&r, &ctx, &in);
    TEST_ASSERT(rc == NGX_ERROR,
        "passthrough should fail when forwarding headers fails");
    g_forward_headers_rc = NGX_OK;

    ctx.headers_forwarded = 1;
    g_next_body_filter_rc = NGX_DONE;
    rc = ngx_http_markdown_streaming_passthrough(&r, &ctx, &in);
    TEST_ASSERT(rc == NGX_DONE,
        "passthrough should call next body filter when headers already forwarded");

    g_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
        &r, &cur, 0);
    TEST_ASSERT(rc == NGX_OK,
        "reentry should pass cl->next to full-buffer body filter");

    cur.next = NULL;
    g_calloc_buf_fail_once = 1;
    rc = ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
        &r, &cur, 1);
    TEST_ASSERT(rc == NGX_ERROR,
        "reentry should fail when terminal buffer allocation fails");

    g_alloc_chain_fail_once = 1;
    rc = ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
        &r, &cur, 1);
    TEST_ASSERT(rc == NGX_ERROR,
        "reentry should fail when terminal chain allocation fails");

    g_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
        &r, &cur, 1);
    TEST_ASSERT(rc == NGX_OK,
        "reentry should synthesize terminal buffer when needed");

    TEST_PASS("abort/passthrough/reentry helpers covered");
}

/*
 * Test null-input handling, failopen tracking, and body_filter entry
 * branches.  Verifies:
 * - count_chain_bufs counts only non-NULL buffers
 * - post-commit failopen tracking short-circuits
 * - tracking allocation failure returns NGX_ERROR
 * - tracking grows storage and copies prior consumed slots
 * - null-input handler propagates resume backpressure
 * - null-input handler finalizes when pending drain completes
 * - process_chain skips NULL buffers safely
 * - body_filter passthrough when ctx or conf is missing
 * - body_filter stops on client abort
 * - ineligible ctx passthroughs body filter
 * - body_filter surfaces failopen tracking allocation errors
 * Covers: ngx_http_markdown_streaming_count_chain_bufs,
 *         ngx_http_markdown_streaming_prepare_failopen_tracking,
 *         ngx_http_markdown_streaming_handle_null_input,
 *         ngx_http_markdown_streaming_process_chain,
 *         ngx_http_markdown_streaming_body_filter
 */
static void
test_null_input_tracking_and_body_filter_entry(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_chain_t             c1;
    ngx_buf_t               b1;
    ngx_chain_t             c2;
    ngx_buf_t               b2;
    ngx_chain_t             pending;
    ngx_buf_t               pending_buf;
    ngx_int_t               rc;
    ngx_flag_t              last_buf = 0;
    ngx_chain_t            *fallback_cl = NULL;

    TEST_SUBSECTION("null-input, failopen tracking, and body_filter entry");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    conf.streaming_engine = (ngx_http_complex_value_t *) (uintptr_t) 0x1;

    ngx_memzero(&c1, sizeof(c1));
    ngx_memzero(&b1, sizeof(b1));
    ngx_memzero(&c2, sizeof(c2));
    ngx_memzero(&b2, sizeof(b2));
    ngx_memzero(&pending, sizeof(pending));
    ngx_memzero(&pending_buf, sizeof(pending_buf));

    c1.buf = &b1;
    c1.next = &c2;
    c2.buf = &b2;
    c2.next = NULL;
    b1.pos = (u_char *) "a";
    b1.last = b1.pos + 1;
    b2.pos = (u_char *) "b";
    b2.last = b2.pos + 1;

    rc = ngx_http_markdown_streaming_count_chain_bufs(&c1);
    TEST_ASSERT(rc == 2, "count_chain_bufs should count only non-NULL buffers");

    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    rc = ngx_http_markdown_streaming_prepare_failopen_tracking(
        &r, &ctx, &c1);
    TEST_ASSERT(rc == NGX_OK,
        "post-commit tracking should short-circuit");

    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.failopen_consumed_count = 0;
    ctx.streaming.failopen_consumed_capacity = 0;
    g_palloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_prepare_failopen_tracking(
        &r, &ctx, &c1);
    TEST_ASSERT(rc == NGX_ERROR,
        "tracking allocation failure should return NGX_ERROR");

    {
        ngx_buf_t *old_bufs[1];
        u_char    *old_pos[1];

        old_bufs[0] = &b1;
        old_pos[0] = b1.pos;
        ctx.streaming.failopen_consumed_bufs = old_bufs;
        ctx.streaming.failopen_consumed_pos = old_pos;
        ctx.streaming.failopen_consumed_count = 1;
        ctx.streaming.failopen_consumed_capacity = 1;

        rc = ngx_http_markdown_streaming_prepare_failopen_tracking(
            &r, &ctx, &c1);
        TEST_ASSERT(rc == NGX_OK,
            "tracking should grow storage and copy prior consumed slots");
    }

    pending.buf = &pending_buf;
    pending.next = NULL;
    ctx.streaming.pending_output = &pending;
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_handle_null_input(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_AGAIN,
        "null-input handler should propagate resume backpressure");

    ctx.streaming.pending_output = NULL;
    ctx.streaming.finalize_after_pending = 1;
    ctx.streaming.handle = NULL;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_null_input(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "null-input handler should finalize when pending drain completes");

    last_buf = 0;
    fallback_cl = NULL;
    c1.buf = NULL;
    c1.next = NULL;
    rc = ngx_http_markdown_streaming_process_chain(
        &r, &ctx, &conf, &c1, &last_buf, 0, &fallback_cl);
    TEST_ASSERT(rc == NGX_OK,
        "process_chain should skip NULL buffers safely");

    r.ctx = NULL;
    rc = ngx_http_markdown_streaming_body_filter(&r, &c1);
    TEST_ASSERT(rc == g_next_body_filter_rc,
        "body_filter should passthrough when ctx is missing");
    r.ctx = &ctx;

    r.loc_conf = NULL;
    rc = ngx_http_markdown_streaming_body_filter(&r, &c1);
    TEST_ASSERT(rc == g_next_body_filter_rc,
        "body_filter should passthrough when conf is missing");
    r.loc_conf = &conf;

    r.connection->error = 1;
    rc = ngx_http_markdown_streaming_body_filter(&r, &c1);
    TEST_ASSERT(rc == NGX_ERROR,
        "body_filter should stop on client abort");
    r.connection->error = 0;

    ctx.eligible = 0;
    rc = ngx_http_markdown_streaming_body_filter(&r, &c1);
    TEST_ASSERT(rc == g_next_body_filter_rc,
        "ineligible ctx should passthrough body filter");
    ctx.eligible = 1;

    ctx.streaming.handle = (struct StreamingConverterHandle *) (uintptr_t) 0x6;
    ctx.streaming.failopen_consumed_count = 0;
    ctx.streaming.failopen_consumed_capacity = 0;
    c1.buf = &b1;
    b1.pos = (u_char *) "x";
    b1.last = b1.pos + 1;
    b1.last_buf = 0;
    c1.next = NULL;
    g_palloc_fail_once = 1;
    rc = ngx_http_markdown_streaming_body_filter(&r, &c1);
    TEST_ASSERT(rc == NGX_ERROR,
        "body_filter should surface failopen tracking allocation errors");

    TEST_PASS("null-input/tracking/body-filter entry branches covered");
}

/*
 * Test init_handle and chunk_result helper branches.  Verifies:
 * - init_handle succeeds on happy path, sets handle, enters pre-commit
 *   state, and marks conversion attempted
 * - prepare-options failure routes through precommit_error
 * - handle-create failure routes through precommit_error
 * - NULL handle on success code still fails
 * - cleanup registration failure returns NGX_ERROR
 * - decompressor init failure routes through precommit_error
 * - chunk_result propagates NGX_AGAIN and NGX_OK
 * - chunk_result returns NGX_DONE after fallback switch
 * - chunk_result fail-open surfaces header-forward errors
 * - chunk_result propagates non-special errors
 * Covers: ngx_http_markdown_streaming_init_handle,
 *         ngx_http_markdown_streaming_handle_chunk_result
 */
static void
test_init_handle_and_chunk_result_helpers(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_http_markdown_metrics_t metrics;
    ngx_chain_t             in;
    ngx_buf_t               in_buf;
    ngx_int_t               rc;

    TEST_SUBSECTION("init_handle and chunk_result helper branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    conf.streaming_budget = 256;
    conf.streaming_on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "init_handle should succeed on happy path");
    TEST_ASSERT(ctx.streaming.handle != NULL,
        "init_handle should set streaming handle");
    TEST_ASSERT(ctx.streaming.commit_state == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE,
        "init_handle should enter pre-commit state");
    TEST_ASSERT(ctx.conversion_attempted == 1,
        "init_handle should mark conversion attempted");

    ctx.streaming.handle = NULL;
    g_prepare_options_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED,
        "prepare-options failure should route through precommit_error");
    g_prepare_options_rc = NGX_OK;

    ctx.streaming.handle = NULL;
    g_new_with_code_rc = ERROR_INTERNAL;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED,
        "handle-create failure should route through precommit_error");
    g_new_with_code_rc = ERROR_SUCCESS;

    ctx.streaming.handle = NULL;
    g_new_with_code_null_handle = 1;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED,
        "NULL handle on success code should still fail");
    g_new_with_code_null_handle = 0;

    ctx.streaming.handle = NULL;
    g_pool_cleanup_fail_once = 1;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "cleanup registration failure should return NGX_ERROR");

    ctx.streaming.handle = NULL;
    ctx.decompression.needed = 1;
    ctx.decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED,
        "decompressor init failure should route through precommit_error");

    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&in_buf, sizeof(in_buf));
    in.buf = &in_buf;
    in.next = NULL;

    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_AGAIN, 0);
    TEST_ASSERT(rc == NGX_AGAIN,
        "chunk_result should propagate NGX_AGAIN");

    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_OK, 0);
    TEST_ASSERT(rc == NGX_OK, "chunk_result should keep NGX_OK");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_ERROR, 0);
    TEST_ASSERT(rc == NGX_DONE,
        "chunk_result should return NGX_DONE after fallback switch");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 0;
    g_forward_headers_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_ERROR, 0);
    TEST_ASSERT(rc == NGX_ERROR,
        "chunk_result fail-open should surface header-forward errors");

    ctx.eligible = 1;
    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_ERROR, 0);
    TEST_ASSERT(rc == NGX_ERROR,
        "chunk_result should propagate non-special errors");

    TEST_PASS("init_handle/chunk_result branches covered");
}

/*
 * Test commit, feed-result, and finalize core branches.  Verifies:
 * - commit fails when header update or next header filter fails
 * - commit propagates positive next header filter rc
 * - commit succeeds on happy path, switches to post-commit, marks
 *   headers forwarded
 * - fallback feed result switches to full-buffer
 * - post-commit feed errors terminate with downstream rc and record failure
 * - pre-commit feed errors honor pass policy and mark ineligible
 * - output byte counter saturates on overflow
 * - successful feed surfaces backpressure (NGX_AGAIN)
 * - empty successful feed output is a no-op
 * - finalize emits terminal buffer when handle is null
 * - pre-commit finalize errors honor pass policy
 * - post-commit finalize errors terminate downstream
 * - successful finalize increments success metrics and stores peak memory
 * - finalize defers terminal last_buf on NGX_AGAIN and latches it
 * - terminal last_buf backpressure latches pending terminal metrics
 * - terminal last_buf hard errors propagate and record failure
 * Covers: ngx_http_markdown_streaming_commit,
 *         ngx_http_markdown_streaming_handle_feed_result,
 *         ngx_http_markdown_streaming_finalize_request
 */
static void
test_commit_feed_and_finalize_core_paths(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_http_markdown_metrics_t metrics;
    u_char                  prebuf_data[16];
    u_char                  out_data[] = "xyz";
    ngx_int_t               rc;

    TEST_SUBSECTION("commit/feed-result/finalize core branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    conf.max_size = 64;
    conf.streaming_on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer.size = 3;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x11;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;

    g_add_vary_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_commit(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "commit should fail when header update fails");
    g_add_vary_rc = NGX_OK;

    g_next_header_filter_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_commit(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "commit should propagate next header filter errors");

    g_next_header_filter_rc = 1;
    rc = ngx_http_markdown_streaming_commit(&r, &ctx, &conf);
    TEST_ASSERT(rc == 1,
        "commit should propagate positive next header filter rc");
    g_next_header_filter_rc = NGX_OK;

    rc = ngx_http_markdown_streaming_commit(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "commit should succeed on happy path");
    TEST_ASSERT(ctx.streaming.commit_state
        == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST,
        "commit should switch state to post-commit");
    TEST_ASSERT(ctx.headers_forwarded == 1,
        "commit should mark headers as forwarded");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x12;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.prebuffer.size = 3;
    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_STREAMING_FALLBACK, out_data, 3);
    TEST_ASSERT(rc == NGX_DECLINED,
        "fallback feed result should switch to full-buffer");
    TEST_ASSERT(ctx.processing_path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "fallback feed result should update processing path");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x13;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    g_next_body_filter_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_INTERNAL, NULL, 0);
    TEST_ASSERT(rc == NGX_ERROR,
        "post-commit feed errors should terminate with downstream rc");
    TEST_ASSERT(ctx.streaming.failure_recorded == 1,
        "post-commit feed errors should record failure once");

    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.failure_recorded = 0;
    ctx.eligible = 1;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_INTERNAL, NULL, 0);
    TEST_ASSERT(rc == NGX_DECLINED,
        "pre-commit feed errors should honor pass policy");
    TEST_ASSERT(ctx.eligible == 0,
        "pre-commit pass policy should mark request ineligible");

    ctx.eligible = 1;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.total_output_bytes = NGX_MAX_SIZE_T_VALUE;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_SUCCESS, out_data, 1);
    TEST_ASSERT(rc == NGX_OK,
        "successful feed result should send output");
    TEST_ASSERT(ctx.streaming.total_output_bytes == NGX_MAX_SIZE_T_VALUE,
        "output byte counter should saturate on overflow");

    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x14;
    ctx.streaming.total_output_bytes = 0;
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_SUCCESS, out_data, 2);
    TEST_ASSERT(rc == NGX_AGAIN,
        "successful feed should surface backpressure");

    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_SUCCESS, out_data, 0);
    TEST_ASSERT(rc == NGX_OK,
        "empty successful feed output should no-op");

    ctx.streaming.handle = NULL;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "finalize should emit terminal buffer when handle is null");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x15;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    g_streaming_finalize_rc = ERROR_INTERNAL;
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED,
        "pre-commit finalize errors should honor pass policy");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x16;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    g_streaming_finalize_rc = ERROR_INTERNAL;
    g_next_body_filter_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "post-commit finalize errors should terminate downstream");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x17;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = out_data;
    g_streaming_finalize_result.markdown_len = 3;
    g_streaming_finalize_result.etag = (uint8_t *) "etag";
    g_streaming_finalize_result.etag_len = 4;
    g_streaming_finalize_result.token_estimate = 9;
    g_streaming_finalize_result.peak_memory_estimate = 128;
    {
        ngx_int_t seq[] = { NGX_OK, NGX_OK };
        set_next_body_filter_sequence(seq, 2);
    }
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "finalize should succeed when final body and terminal buffer send");
    TEST_ASSERT(metrics.streaming.succeeded_total >= 1,
        "successful finalize should increment success metrics");
    TEST_ASSERT(metrics.streaming.last_peak_memory_bytes == 128,
        "finalize should store peak memory gauge from result");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x18;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.finalize_pending_lastbuf = 0;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = out_data;
    g_streaming_finalize_result.markdown_len = 3;
    {
        ngx_int_t seq[] = { NGX_AGAIN };
        set_next_body_filter_sequence(seq, 1);
    }
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_AGAIN,
        "finalize should defer terminal last_buf when markdown send is NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.finalize_pending_lastbuf == 1,
        "finalize should latch deferred terminal last_buf");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x19;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.pending_terminal_metrics = 0;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = out_data;
    g_streaming_finalize_result.markdown_len = 3;
    {
        ngx_int_t seq[] = { NGX_OK, NGX_AGAIN };
        set_next_body_filter_sequence(seq, 2);
    }
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_AGAIN,
        "terminal last_buf backpressure should propagate NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_terminal_metrics == 1,
        "terminal backpressure should latch pending terminal metrics");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x1A;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.pending_terminal_metrics = 0;
    ctx.streaming.failure_recorded = 0;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = out_data;
    g_streaming_finalize_result.markdown_len = 3;
    {
        ngx_int_t seq[] = { NGX_OK, NGX_ERROR };
        set_next_body_filter_sequence(seq, 2);
    }
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "terminal last_buf hard errors should propagate");
    TEST_ASSERT(ctx.streaming.failure_recorded == 1,
        "terminal last_buf hard errors should record failure");

    TEST_PASS("commit/feed-result/finalize core branches covered");
}

/*
 * Test process-chain, failopen passthrough, and body_filter deep branches.
 * Verifies:
 * - failopen passthrough passes current chain for first invocation
 * - failopen passthrough fails on prefix chain allocation errors
 * - failopen passthrough rebuilds prefix chain on reentry
 * - process_chain fails when failopen tracking capacity is exhausted
 * - process_chain returns NGX_DONE when fallback switches path
 * - body_filter reenters full-buffer path after fallback
 * - body_filter fail-open passthrough when finalize declines
 * Covers: ngx_http_markdown_streaming_failopen_passthrough,
 *         ngx_http_markdown_streaming_process_chain,
 *         ngx_http_markdown_streaming_body_filter
 */
static void
test_process_chain_and_body_filter_deep_paths(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_chain_t             in;
    ngx_buf_t               in_buf;
    ngx_chain_t            *fallback_cl = NULL;
    ngx_flag_t              last_buf = 0;
    ngx_int_t               rc;
    ngx_buf_t              *saved_bufs[2] = { NULL, NULL };
    u_char                 *saved_pos[2] = { NULL, NULL };
    u_char                  prebuf_data[16];
    u_char                  chunk_data[] = "chunk";

    TEST_SUBSECTION("process-chain/failopen/body-filter deep branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    conf.max_size = 0;
    conf.streaming_on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS;

    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&in_buf, sizeof(in_buf));
    in.buf = &in_buf;
    in.next = NULL;
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    in_buf.last_buf = 1;

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x21;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);

    saved_bufs[0] = &in_buf;
    saved_pos[0] = in_buf.pos;
    ctx.streaming.failopen_consumed_bufs = saved_bufs;
    ctx.streaming.failopen_consumed_pos = saved_pos;
    ctx.streaming.failopen_consumed_count = 1;

    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in, 0);
    TEST_ASSERT(rc == NGX_OK,
        "failopen passthrough should pass current chain for first invocation");

    g_alloc_chain_fail_once = 1;
    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in, 1);
    TEST_ASSERT(rc == NGX_ERROR,
        "failopen passthrough should fail on prefix chain allocation errors");

    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in, 1);
    TEST_ASSERT(rc == NGX_OK,
        "failopen passthrough should rebuild prefix chain on reentry");

    ctx.streaming.failopen_consumed_count = 0;
    ctx.streaming.failopen_consumed_capacity = 0;
    conf.streaming_on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    rc = ngx_http_markdown_streaming_process_chain(
        &r, &ctx, &conf, &in, &last_buf, 0, &fallback_cl);
    TEST_ASSERT(rc == NGX_ERROR,
        "process_chain should fail when failopen tracking capacity is exhausted");

    conf.streaming_on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS;
    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x22;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer.size = 3;
    ctx.streaming.failopen_consumed_count = 0;
    ctx.streaming.failopen_consumed_capacity = 2;
    saved_bufs[0] = &in_buf;
    saved_pos[0] = in_buf.pos;
    ctx.streaming.failopen_consumed_bufs = saved_bufs;
    ctx.streaming.failopen_consumed_pos = saved_pos;
    g_streaming_feed_rc = ERROR_STREAMING_FALLBACK;
    rc = ngx_http_markdown_streaming_process_chain(
        &r, &ctx, &conf, &in, &last_buf, 0, &fallback_cl);
    TEST_ASSERT(rc == NGX_DONE,
        "process_chain should return NGX_DONE when fallback switches path");
    TEST_ASSERT(fallback_cl == &in,
        "process_chain should return fallback location");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 1;
    ctx.headers_forwarded = 0;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x23;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer.size = 3;
    ctx.streaming.failopen_consumed_count = 0;
    ctx.streaming.failopen_consumed_capacity = 2;
    ctx.streaming.failopen_consumed_bufs = saved_bufs;
    ctx.streaming.failopen_consumed_pos = saved_pos;
    g_streaming_feed_rc = ERROR_STREAMING_FALLBACK;
    g_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_body_filter(&r, &in);
    TEST_ASSERT(rc == NGX_OK,
        "body_filter should reenter full-buffer path after fallback");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x24;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer.size = 1;
    ctx.streaming.failopen_consumed_count = 0;
    ctx.streaming.failopen_consumed_capacity = 2;
    ctx.streaming.failopen_consumed_bufs = saved_bufs;
    ctx.streaming.failopen_consumed_pos = saved_pos;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    g_streaming_finalize_rc = ERROR_INTERNAL;
    g_next_body_filter_rc = NGX_OK;
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    in_buf.last_buf = 1;
    rc = ngx_http_markdown_streaming_body_filter(&r, &in);
    TEST_ASSERT(rc == NGX_OK,
        "body_filter should fail-open passthrough when finalize declines");

    TEST_PASS("process-chain/failopen/body-filter deep branches covered");
}

/*
 * Test streaming helper gap branches — edge cases not covered by the
 * focused tests above.  Verifies:
 * - selector uses full-buffer when conf is NULL
 * - resume_pending sends deferred last_buf branch
 * - resume_pending sends deferred last_buf after pending drain
 * - fallback fails when prebuffer append fails
 * - feed errors with output fail-open in pre-commit pass mode and free
 *   the output buffer
 * - feed success propagates commit failure and still frees feed output
 * - finalize propagates markdown send errors, preserves saturated output
 *   counter, and records failure
 * - finalize pre-commit surfaces commit errors
 * - process_chunk short-circuits on NULL buffer, empty buffers, and
 *   invalid pointer ordering
 * - ensure_handle fails when fail-open header forwarding fails
 * - ensure_handle passthrough when init declines
 * - ensure_handle propagates hard init failures
 * - ensure_handle returns NGX_OK when init succeeds
 * - body_filter NULL input uses null-input helper success path
 * - body_filter returns non-OK ensure_handle result
 * - body_filter returns NGX_OK for non-terminal successful chunks
 * - body_filter returns finalize rc on terminal buffer
 * - finalize handles empty markdown result path
 * - null-input helper returns NGX_OK when nothing is pending
 * Covers: ngx_http_markdown_select_processing_path,
 *         ngx_http_markdown_streaming_resume_pending,
 *         ngx_http_markdown_streaming_fallback_to_fullbuffer,
 *         ngx_http_markdown_streaming_handle_feed_result,
 *         ngx_http_markdown_streaming_finalize_request,
 *         ngx_http_markdown_streaming_process_chunk,
 *         ngx_http_markdown_streaming_ensure_handle,
 *         ngx_http_markdown_streaming_body_filter,
 *         ngx_http_markdown_streaming_handle_null_input
 */
static void
test_streaming_gap_branches(void)
{
    ngx_http_request_t      r;
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t              pool;
    ngx_connection_t        conn;
    ngx_log_t               log;
    ngx_event_t             read_event;
    ngx_chain_t             in;
    ngx_buf_t               in_buf;
    ngx_int_t               rc;
    u_char                  prebuf_data[16];
    u_char                  mainbuf_data[16];
    u_char                  out_data[] = "zz";
    ngx_buf_t               pending_buf;
    ngx_chain_t             pending_cl;
    ngx_uint_t              selected_path;
    ngx_uint_t              free_before;

    TEST_SUBSECTION("streaming helper gap branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);

    conf.streaming_on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS;
    conf.max_size = 64;

    selected_path = ngx_http_markdown_select_processing_path(&r, NULL);
    TEST_ASSERT(selected_path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "selector should use full-buffer when conf is NULL");

    ctx.streaming.finalize_pending_lastbuf = 1;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "resume_pending should send deferred last_buf branch");

    ngx_memzero(&pending_buf, sizeof(pending_buf));
    ngx_memzero(&pending_cl, sizeof(pending_cl));
    pending_buf.pos = out_data;
    pending_buf.last = out_data + 2;
    pending_cl.buf = &pending_buf;
    pending_cl.next = NULL;
    ctx.streaming.pending_output = &pending_cl;
    ctx.streaming.finalize_pending_lastbuf = 1;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "resume_pending should send deferred last_buf after pending drain");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x31;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer.size = 2;
    ctx.buffer_initialized = 1;
    ctx.buffer.data = mainbuf_data;
    ctx.buffer.capacity = sizeof(mainbuf_data);
    ctx.buffer.max_size = sizeof(mainbuf_data);
    g_buffer_append_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_fallback_to_fullbuffer(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "fallback should fail when prebuffer append fails");
    g_buffer_append_rc = NGX_OK;

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.eligible = 1;
    free_before = g_output_free_calls;
    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_INTERNAL, out_data, 2);
    TEST_ASSERT(rc == NGX_DECLINED,
        "feed errors with output should fail-open in pre-commit pass mode");
    TEST_ASSERT(g_output_free_calls == free_before + 1,
        "feed error branch should free provided output buffer");

    ctx.eligible = 1;
    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x32;
    g_add_vary_rc = NGX_ERROR;
    free_before = g_output_free_calls;
    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_SUCCESS, out_data, 2);
    TEST_ASSERT(rc == NGX_ERROR,
        "feed success should propagate commit failure");
    TEST_ASSERT(g_output_free_calls == free_before + 1,
        "commit failure should still free feed output");
    g_add_vary_rc = NGX_OK;

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x33;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.total_output_bytes = NGX_MAX_SIZE_T_VALUE;
    ctx.streaming.failure_recorded = 0;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = out_data;
    g_streaming_finalize_result.markdown_len = 2;
    {
        ngx_int_t seq[] = { NGX_ERROR };
        set_next_body_filter_sequence(seq, 1);
    }
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "finalize should propagate markdown send errors");
    TEST_ASSERT(ctx.streaming.total_output_bytes == NGX_MAX_SIZE_T_VALUE,
        "finalize markdown send should preserve saturated output counter");
    TEST_ASSERT(ctx.streaming.failure_recorded == 1,
        "finalize markdown send error should record failure");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x34;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = out_data;
    g_streaming_finalize_result.markdown_len = 2;
    g_add_vary_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "finalize pre-commit should surface commit errors");
    g_add_vary_rc = NGX_OK;

    rc = ngx_http_markdown_streaming_process_chunk(
        &r, &ctx, &conf, NULL);
    TEST_ASSERT(rc == NGX_OK,
        "process_chunk should short-circuit on NULL buffer");

    ngx_memzero(&in_buf, sizeof(in_buf));
    in_buf.pos = out_data;
    in_buf.last = out_data;
    rc = ngx_http_markdown_streaming_process_chunk(
        &r, &ctx, &conf, &in_buf);
    TEST_ASSERT(rc == NGX_OK,
        "process_chunk should short-circuit on empty buffers");

    in_buf.pos = out_data + 1;
    in_buf.last = out_data;
    rc = ngx_http_markdown_streaming_process_chunk(
        &r, &ctx, &conf, &in_buf);
    TEST_ASSERT(rc == NGX_OK,
        "process_chunk should ignore invalid pointer ordering");
    in_buf.pos = out_data;
    in_buf.last = out_data + 2;

    ngx_memzero(&in, sizeof(in));
    in.buf = &in_buf;
    in.next = NULL;

    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    ctx.headers_forwarded = 0;
    g_prepare_options_rc = NGX_ERROR;
    g_forward_headers_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_ensure_handle(&r, &ctx, &conf, &in);
    TEST_ASSERT(rc == NGX_ERROR,
        "ensure_handle should fail when fail-open header forwarding fails");

    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    ctx.headers_forwarded = 0;
    g_prepare_options_rc = NGX_ERROR;
    g_forward_headers_rc = NGX_OK;
    g_next_body_filter_rc = NGX_DONE;
    rc = ngx_http_markdown_streaming_ensure_handle(&r, &ctx, &conf, &in);
    TEST_ASSERT(rc == NGX_DONE,
        "ensure_handle should passthrough when init declines");

    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    g_prepare_options_rc = NGX_OK;
    g_new_with_code_rc = ERROR_SUCCESS;
    g_new_with_code_null_handle = 0;
    g_pool_cleanup_fail_once = 1;
    rc = ngx_http_markdown_streaming_ensure_handle(&r, &ctx, &conf, &in);
    TEST_ASSERT(rc == NGX_ERROR,
        "ensure_handle should propagate hard init failures");

    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    g_prepare_options_rc = NGX_OK;
    g_new_with_code_rc = ERROR_SUCCESS;
    g_new_with_code_null_handle = 0;
    rc = ngx_http_markdown_streaming_ensure_handle(&r, &ctx, &conf, &in);
    TEST_ASSERT(rc == NGX_OK,
        "ensure_handle should return NGX_OK when init succeeds");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x35;
    ctx.streaming.pending_output = NULL;
    ctx.streaming.finalize_after_pending = 0;
    rc = ngx_http_markdown_streaming_body_filter(&r, NULL);
    TEST_ASSERT(rc == NGX_OK,
        "body_filter NULL input should use null-input helper success path");

    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    ctx.headers_forwarded = 0;
    g_prepare_options_rc = NGX_ERROR;
    g_forward_headers_rc = NGX_OK;
    g_next_body_filter_rc = NGX_DONE;
    rc = ngx_http_markdown_streaming_body_filter(&r, &in);
    TEST_ASSERT(rc == NGX_DONE,
        "body_filter should return non-OK ensure_handle result");

    ctx.eligible = 1;
    ctx.headers_forwarded = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x36;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.failopen_consumed_count = 0;
    ctx.streaming.failopen_consumed_capacity = 2;
    in_buf.last_buf = 0;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    rc = ngx_http_markdown_streaming_body_filter(&r, &in);
    TEST_ASSERT(rc == NGX_OK,
        "body_filter should return NGX_OK for non-terminal successful chunks");

    ctx.eligible = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x37;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.failopen_consumed_count = 0;
    ctx.streaming.failopen_consumed_capacity = 2;
    in_buf.last_buf = 1;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = NULL;
    g_streaming_finalize_result.markdown_len = 0;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_body_filter(&r, &in);
    TEST_ASSERT(rc == NGX_OK,
        "body_filter should return finalize rc on terminal buffer");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x38;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.total_output_bytes = 0;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = NULL;
    g_streaming_finalize_result.markdown_len = 0;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "finalize should handle empty markdown result path");

    rc = ngx_http_markdown_streaming_handle_null_input(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "null-input helper should return NGX_OK when nothing is pending");

    TEST_PASS("streaming helper gap branches covered");
}

/*
 * Test entry point.  Runs all streaming_impl unit test functions in
 * sequence.  Prints a banner before and after the test run.  Returns 0
 * on success; individual test assertions abort via TEST_ASSERT on failure.
 */
int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_impl Tests\n");
    printf("========================================\n");

    test_cleanup_paths();
    test_select_processing_path();
    test_update_headers_paths();
    test_send_output_and_resume_paths();
    test_send_output_error_and_deferred_paths();
    test_fallback_to_fullbuffer_paths();
    test_postcommit_and_precommit_error_paths();
    test_abort_passthrough_and_reentry_helpers();
    test_null_input_tracking_and_body_filter_entry();
    test_init_handle_and_chunk_result_helpers();
    test_commit_feed_and_finalize_core_paths();
    test_process_chain_and_body_filter_deep_paths();
    test_streaming_gap_branches();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}

#endif  /* MARKDOWN_STREAMING_ENABLED */
