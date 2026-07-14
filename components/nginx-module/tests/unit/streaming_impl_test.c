/*
 * Test: streaming_impl
 *
 * Validates the streaming conversion pipeline: chunk feeding, output
 * accumulation, budget enforcement, error handling, and finalization
 * semantics for the streaming engine path.
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
/* struct ngx_buf_s provided by nginx_stubs/ngx_core.h */

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
static ngx_uint_t g_next_body_filter_calls = 0;
static ngx_chain_t *g_next_body_filter_last_in = NULL;

/*
 * Per-call history recorder for downstream body-filter invocations.
 * Captures whether in==NULL, whether any link carries last_buf, and
 * whether any link carries last_in_chain, for each call.  This lets
 * regression tests prove exact downstream invocation sequences:
 *   call 1 = multi-link chain with terminal tail
 *   call 2 = NULL resume
 *   call 3 = MUST NOT EXIST
 */
#define G_BODY_FILTER_HIST_MAX 16
typedef struct {
    unsigned    is_null:1;
    unsigned    any_last_buf:1;
    unsigned    any_last_in_chain:1;
} body_filter_hist_entry_t;
static body_filter_hist_entry_t g_body_filter_hist[G_BODY_FILTER_HIST_MAX];
static ngx_uint_t g_body_filter_hist_len = 0;
static ngx_int_t g_next_header_filter_rc = NGX_OK;
static ngx_int_t g_complex_value_rc = NGX_OK;
static ngx_int_t g_add_vary_rc = NGX_OK;
static ngx_int_t g_set_etag_rc = NGX_OK;
static ngx_int_t g_buffer_init_rc = NGX_OK;
static ngx_uint_t g_buffer_init_fail_after = 0;
static ngx_uint_t g_buffer_init_call_count = 0;
static ngx_int_t g_buffer_append_rc = NGX_OK;
static ngx_uint_t g_buffer_append_fail_after = 0;
static ngx_uint_t g_buffer_append_call_count = 0;
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
static ngx_uint_t g_alloc_chain_fail_after = 0;
static ngx_uint_t g_alloc_chain_call_count = 0;
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
static ngx_uint_t g_streaming_feed_calls = 0;
static uint32_t g_streaming_safe_finish_rc = POST_COMMIT_SAFE_FINISH;
static u_char *g_streaming_safe_finish_data = NULL;
static uintptr_t g_streaming_safe_finish_len = 0;
static uint32_t g_streaming_finalize_rc = ERROR_SUCCESS;
static struct MarkdownResult g_streaming_finalize_result;
static ngx_uint_t g_info_log_count = 0;
static const char *g_last_info_log_fmt = NULL;
static ngx_uint_t g_otel_span_start_calls = 0;
static ngx_uint_t g_otel_str_attr_count = 0;
static ngx_uint_t g_otel_uri_attr_seen = 0;
static ngx_uint_t g_otel_uri_route_seen = 0;
static ngx_uint_t g_otel_full_uri_seen = 0;

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

#define NGX_HTTP_MARKDOWN_METRIC_WATERMARK(field, value)                             \
    do {                                                                             \
        if (ngx_http_markdown_metrics != NULL) {                                     \
            ngx_atomic_t _wm_new = (ngx_atomic_t) (value);                          \
            if (_wm_new > ngx_http_markdown_metrics->field) {                        \
                ngx_http_markdown_metrics->field = _wm_new;                          \
            }                                                                        \
        }                                                                            \
    } while (0)

#include "../../src/ngx_http_markdown_postcommit_metrics_impl.h"

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
        if ((level) == NGX_LOG_INFO) {                                               \
            g_info_log_count++;                                                      \
            g_last_info_log_fmt = (fmt);                                             \
        }                                                                            \
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
    unsigned  is_null = (in == NULL) ? 1 : 0;
    unsigned  any_lb = 0;
    unsigned  any_lic = 0;

    UNUSED(r);
    g_next_body_filter_calls++;
    g_next_body_filter_last_in = in;

    for (ngx_chain_t *cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL) {
            continue;
        }
        if (cl->buf->last_buf) {
            any_lb = 1;
        }
        if (cl->buf->last_in_chain) {
            any_lic = 1;
        }
    }

    if (g_body_filter_hist_len < G_BODY_FILTER_HIST_MAX) {
        g_body_filter_hist[g_body_filter_hist_len].is_null = is_null;
        g_body_filter_hist[g_body_filter_hist_len].any_last_buf = any_lb;
        g_body_filter_hist[g_body_filter_hist_len].any_last_in_chain = any_lic;
        g_body_filter_hist_len++;
    }

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
 * Local helper: count non-NULL buffers in an input chain.
 * Removed from production streaming_impl.h after the replay buffer
 * refactor (prepare_failopen_tracking no longer needs it).  Kept
 * here for test coverage of the counting logic itself.
 */
static ngx_uint_t
test_count_chain_bufs(ngx_chain_t *in)
{
    ngx_chain_t *cl;
    ngx_uint_t   count;

    count = 0;
    for (cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf != NULL) {
            count++;
        }
    }

    return count;
}

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
    g_alloc_chain_call_count++;
    if (g_alloc_chain_fail_after > 0
        && g_alloc_chain_call_count == g_alloc_chain_fail_after)
    {
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
    g_streaming_feed_calls++;
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
 * Stub for markdown_streaming_safe_finish.  Returns a test-controlled
 * result and closing-byte buffer so the production postcommit sender can
 * be exercised together with the shared streaming resume lifecycle.
 */
uint32_t
markdown_streaming_safe_finish(struct StreamingConverterHandle *handle,
    uint8_t **out_data, uintptr_t *out_len)
{
    UNUSED(handle);
    if (out_data != NULL) {
        *out_data = g_streaming_safe_finish_data;
    }
    if (out_len != NULL) {
        *out_len = g_streaming_safe_finish_len;
    }
    return g_streaming_safe_finish_rc;
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
 * Stub for markdown_result_init.  Zeroes all fields to guarantee a
 * clean baseline before FFI calls populate the struct.
 */
void
markdown_result_init(struct MarkdownResult *result)
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
    g_buffer_init_call_count++;
    if (g_buffer_init_rc != NGX_OK) {
        return g_buffer_init_rc;
    }
    if (g_buffer_init_fail_after > 0
        && g_buffer_init_call_count > g_buffer_init_fail_after)
    {
        return NGX_ERROR;
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
    g_buffer_append_call_count++;
    if (g_buffer_append_rc != NGX_OK) {
        return g_buffer_append_rc;
    }
    if (g_buffer_append_fail_after > 0
        && g_buffer_append_call_count > g_buffer_append_fail_after)
    {
        return NGX_ERROR;
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
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    struct MarkdownOptions *options)
{
    UNUSED(r);
    UNUSED(conf);
    UNUSED(eff);
    UNUSED(options);
    return g_prepare_options_rc;
}

/*
 * Stub for ngx_http_markdown_stream_type_excluded.
 * The v0.8.0 excluded_types check is tested in
 * hard_excluded_types_security_test and streaming_config_contract_test.
 * Here we return 0 (not excluded) to let the path selection proceed.
 */
ngx_int_t
ngx_http_markdown_stream_type_excluded(const ngx_str_t *content_type,
    const ngx_http_markdown_conf_t *conf)
{
    UNUSED(content_type);
    UNUSED(conf);
    return 0;
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
ngx_http_markdown_reason_streaming_skip_compressed(void)
{
    static ngx_str_t s = { sizeof("STREAMING_SKIP_COMPRESSED") - 1,
        (u_char *) "STREAMING_SKIP_COMPRESSED" };
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

const ngx_str_t *
ngx_http_markdown_reason_eligible_streaming_auto(void)
{
    static ngx_str_t s = { sizeof("ELIGIBLE_STREAMING_AUTO") - 1,
        (u_char *) "ELIGIBLE_STREAMING_AUTO" };
    return &s;
}

const ngx_str_t *
ngx_http_markdown_reason_eligible_fullbuffer_auto(void)
{
    static ngx_str_t s = { sizeof("ELIGIBLE_FULLBUFFER_AUTO") - 1,
        (u_char *) "ELIGIBLE_FULLBUFFER_AUTO" };
    return &s;
}

/*
 * Stub for ngx_http_markdown_log_decision.  Increments
 * g_log_decision_calls so tests can verify invocation.  Ignores all
 * arguments.
 */
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
#define ngx_atomic_fetch_add(p, v)  (*(p) += (v), *(p))
#endif

static ngx_inline ngx_http_markdown_otel_span_t *
ngx_http_markdown_otel_span_start(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf)
{
    static ngx_http_markdown_otel_span_t span;

    (void) r;
    if (conf == NULL || conf->ops.otel_enabled == 0) {
        return NULL;
    }

    ngx_memzero(&span, sizeof(span));
    g_otel_span_start_calls++;
    return &span;
}

static ngx_inline void
ngx_http_markdown_otel_set_str_attr(ngx_http_markdown_otel_span_t *span,
    const u_char *key, size_t key_len,
    const u_char *val, size_t val_len)
{
    (void) span;

    g_otel_str_attr_count++;
    if (key_len == 3 && ngx_memcmp(key, "uri", 3) == 0) {
        g_otel_uri_attr_seen = 1;
    }
    if (key_len == 9 && ngx_memcmp(key, "uri_route", 9) == 0) {
        g_otel_uri_route_seen = 1;
    }
    if (val_len == sizeof("/private/customer/12345?token=secret") - 1
        && ngx_memcmp(val, "/private/customer/12345?token=secret",
                      val_len) == 0)
    {
        g_otel_full_uri_seen = 1;
    }
}

static ngx_inline void
ngx_http_markdown_otel_set_int_attr(ngx_http_markdown_otel_span_t *span,
    const u_char *key, size_t key_len,
    int64_t val)
{
    (void) span;
    (void) key;
    (void) key_len;
    (void) val;
}

static ngx_inline void
ngx_http_markdown_otel_span_end(ngx_http_markdown_otel_span_t *span)
{
    (void) span;
}

static ngx_inline void
ngx_http_markdown_otel_span_export(ngx_http_markdown_otel_span_t *span,
    ngx_log_t *log, ngx_http_request_t *r)
{
    (void) span;
    (void) log;
    (void) r;
}

static ngx_inline void
ngx_http_markdown_record_per_path_metrics(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    ngx_msec_t elapsed_ms)
{
    (void) r;
    (void) conf;
    (void) elapsed_ms;
}

static ngx_int_t g_stream_commit_headers_rc = NGX_OK;
static int g_stream_commit_headers_called;

/* Stub: ngx_http_markdown_stream_commit_headers */
ngx_int_t
ngx_http_markdown_stream_commit_headers(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    (void) r; (void) ctx; (void) conf;
    g_stream_commit_headers_called++;
    return g_stream_commit_headers_rc;
}

/*
 * Include the streaming implementation header after all stubs are defined.
 * This pulls in the inline/static helper functions that are the actual
 * subjects under test, compiled against the stub NGINX runtime above.
 */
#include "../../src/ngx_http_markdown_streaming_impl.h"

/*
 * Pull in the production postcommit sender so metric regressions exercise
 * safe_finish -> send_chain -> pending ownership -> shared NULL resume.
 */
static void
ngx_http_markdown_test_postcommit_log(ngx_uint_t level, ngx_log_t *log,
    ngx_int_t err, const char *fmt, ...)
{
    UNUSED(level);
    UNUSED(log);
    UNUSED(err);
    UNUSED(fmt);
}

#undef ngx_log_error
#define ngx_log_error(...) \
    ngx_http_markdown_test_postcommit_log(__VA_ARGS__)

#include "../../src/ngx_http_markdown_filter_chain_impl.h"
#include "../../src/ngx_http_markdown_stream_postcommit.c"

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
    g_next_body_filter_calls = 0;
    g_next_body_filter_last_in = NULL;
    g_body_filter_hist_len = 0;
    ngx_memzero(g_body_filter_hist, sizeof(g_body_filter_hist));
    g_next_header_filter_rc = NGX_OK;
    g_complex_value_rc = NGX_OK;
    g_add_vary_rc = NGX_OK;
    g_set_etag_rc = NGX_OK;
    g_stream_commit_headers_rc = NGX_OK;
    g_stream_commit_headers_called = 0;
    g_buffer_init_rc = NGX_OK;
    g_buffer_init_fail_after = 0;
    g_buffer_init_call_count = 0;
    g_buffer_append_rc = NGX_OK;
    g_buffer_append_fail_after = 0;
    g_buffer_append_call_count = 0;
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
    g_alloc_chain_fail_after = 0;
    g_alloc_chain_call_count = 0;
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
    g_streaming_feed_calls = 0;
    g_streaming_safe_finish_rc = POST_COMMIT_SAFE_FINISH;
    g_streaming_safe_finish_data = NULL;
    g_streaming_safe_finish_len = 0;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    ngx_memzero(&g_streaming_finalize_result,
        sizeof(g_streaming_finalize_result));
    g_info_log_count = 0;
    g_last_info_log_fmt = NULL;
    g_otel_span_start_calls = 0;
    g_otel_str_attr_count = 0;
    g_otel_uri_attr_seen = 0;
    g_otel_uri_route_seen = 0;
    g_otel_full_uri_seen = 0;
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
    conf->stream.precommit_buffer = 256 * 1024;
}

/*
 * Test streaming_cleanup paths.  Verifies that:
 * - cleanup with NULL context is safe (no-op)
 * - cleanup aborts the streaming handle and clears the pending_output
 *   anchor without inferring Rust allocator ownership from ngx_buf_t
 *   flags (b->temporary describes buffer behavior, not allocator
 *   provenance — see test_cleanup_does_not_free_shared_temporary_buffer
 *   below for the memory-safety regression this guards against)
 * Covers: ngx_http_markdown_streaming_cleanup
 */
static void
test_cleanup_paths(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_chain_t             cl;
    ngx_buf_t               b;

    TEST_SUBSECTION("cleanup aborts handle and clears pending anchor");
    reset_globals();
    ngx_http_markdown_streaming_cleanup(NULL);
    ngx_memzero(&ctx, sizeof(ctx));
    ngx_memzero(&cl, sizeof(cl));
    ngx_memzero(&b, sizeof(b));

    b.pos = (u_char *) "abc";
    b.last = b.pos + 3;
    cl.buf = &b;
    cl.next = NULL;

    ctx.streaming.handle = (struct StreamingConverterHandle *) (uintptr_t) 0x1;
    ctx.streaming.pending_output = &cl;
    ngx_http_markdown_streaming_cleanup(&ctx);

    TEST_ASSERT(ctx.streaming.handle == NULL, "cleanup should clear handle");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "cleanup should clear pending chain");
    TEST_ASSERT(g_abort_calls == 1, "cleanup should abort streaming handle once");
    TEST_ASSERT(g_output_free_calls == 0,
        "cleanup must not free pending buffers based on ngx_buf_t flags; "
        "Rust-owned zero-copy buffers are freed exactly once by their own "
        "pool cleanup context (ngx_http_markdown_rust_buf_cleanup), not by "
        "streaming_cleanup walking pending_output");
    TEST_ASSERT(b.pos != NULL && b.last != NULL,
        "cleanup must not clear/free buffer fields it does not own");
    TEST_PASS("cleanup paths covered");
}

/*
 * P1 memory-safety regression: streaming_cleanup() must NOT infer Rust
 * allocator ownership from ngx_buf_t->temporary.
 *
 * Production reachable scenario: a fail-open cloned chain link
 * (ngx_http_markdown_streaming_clone_chain_links) shares the ngx_buf_t
 * with an upstream-owned buffer.  Upstream/proxy buffers are commonly
 * marked temporary=1 (writable buffer owned by the module that created
 * it — this is an NGINX buffer *behavior* flag, not a Rust allocator
 * provenance tag).  If that shared buffer is still referenced by
 * pending_output when the client aborts/times out and the request pool
 * is destroyed, streaming_cleanup() must not call
 * markdown_streaming_output_free() on it: the pointer was never
 * allocated by the Rust streaming allocator, so freeing it is an
 * invalid free (allocator corruption / crash risk).
 *
 * Covers: ngx_http_markdown_streaming_cleanup
 */
static void
test_cleanup_does_not_free_shared_temporary_buffer(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_chain_t             cl;
    ngx_buf_t               upstream_buf;
    u_char                  upstream_data[] = "upstream-owned-html";
    u_char                 *saved_pos;
    u_char                 *saved_last;

    TEST_SUBSECTION(
        "cleanup must not free shared upstream temporary=1 buffer");
    reset_globals();
    ngx_memzero(&ctx, sizeof(ctx));
    ngx_memzero(&cl, sizeof(cl));
    ngx_memzero(&upstream_buf, sizeof(upstream_buf));

    /*
     * Simulate an upstream-owned buffer (as would be shared by a
     * fail-open cloned chain link) that happens to be marked
     * temporary=1.  This is a buffer *behavior* flag set by whatever
     * module created the buffer — it says nothing about whether the
     * Rust streaming allocator produced this pointer.
     */
    upstream_buf.pos = upstream_data;
    upstream_buf.last = upstream_data + sizeof(upstream_data) - 1;
    upstream_buf.temporary = 1;
    saved_pos = upstream_buf.pos;
    saved_last = upstream_buf.last;

    cl.buf = &upstream_buf;
    cl.next = NULL;

    ctx.streaming.handle = NULL;
    ctx.streaming.pending_output = &cl;

    ngx_http_markdown_streaming_cleanup(&ctx);

    TEST_ASSERT(g_output_free_calls == 0,
        "cleanup must never call markdown_streaming_output_free on a "
        "buffer it does not know to be Rust-allocated, regardless of "
        "the temporary flag");
    TEST_ASSERT(upstream_buf.pos == saved_pos
                && upstream_buf.last == saved_last,
        "cleanup must not mutate a buffer it does not own");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "cleanup must still clear the pending_output anchor/state");

    TEST_PASS("cleanup shared-temporary-buffer ownership safety covered");
}

/*
 * Test select_processing_path routing logic.  Verifies that:
 * - policy=off routes to full-buffer
 * - HEAD method routes to full-buffer
 * - 304 status routes to full-buffer
 * - full_support conditional mode routes to full-buffer
 * - SSE content-type routes to full-buffer
 * - excluded stream_types prefix routes to full-buffer
 * - policy=force with no exclusions routes to streaming
 * - non-matching stream_types keeps streaming enabled
 * - NULL content-type data does not trigger exclusions
 * - if_modified_since_only conditional mode keeps streaming
 * - auto with small content-length routes to full-buffer
 * - auto without content-length routes to streaming
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
    ngx_http_markdown_path_selection_t selection;

    TEST_SUBSECTION("select_processing_path policy and skip branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);

    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;
    conf.stream.threshold = 1024;
    conf.policy.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;
    conf.routing.large_body_threshold = 1024;
    r.headers_out.content_type = (ngx_str_t) { 9, (u_char *) "text/html" };
    r.headers_out.content_length_n = 2048;

    /* policy=off should route full-buffer */
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_OFF;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "policy=off should route full-buffer");
    TEST_ASSERT(selection.reason == NGX_HTTP_MARKDOWN_STREAM_REASON_CONFIG_DISABLED,
        "policy=off should preserve config_disabled reason");

    /* policy=auto, HEAD request */
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;
    r.method = NGX_HTTP_HEAD;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "HEAD should route full-buffer");
    TEST_ASSERT(selection.reason == NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_CANDIDATE,
        "HEAD should preserve not_candidate reason");
    r.method = 0;

    r.headers_out.status = NGX_HTTP_NOT_MODIFIED;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "304 should route full-buffer");
    r.headers_out.status = 200;

    conf.policy.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "full_support should route full-buffer");
    conf.policy.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;

    r.headers_out.content_type = (ngx_str_t) { 17, (u_char *) "text/event-stream" };
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "SSE should route full-buffer");
    TEST_ASSERT(selection.reason == NGX_HTTP_MARKDOWN_STREAM_REASON_EXCLUDED_CONTENT_TYPE,
        "SSE should preserve excluded_content_type reason");

    r.headers_out.content_type = (ngx_str_t) { 9, (u_char *) "text/html" };
    excluded[0] = (ngx_str_t) { 4, (u_char *) "text" };
    arr.elts = excluded;
    arr.nelts = 1;
    conf.routing.stream_types = &arr;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "excluded stream_types should route full-buffer");

    conf.routing.stream_types = NULL;
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_FORCE;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "policy=force should route streaming");
    TEST_ASSERT(selection.reason == NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE,
        "policy=force should preserve eligible reason");

    /* Switch back to auto for remaining tests */
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;

    excluded[0] = (ngx_str_t) { 11, (u_char *) "application" };
    arr.elts = excluded;
    arr.nelts = 1;
    conf.routing.stream_types = &arr;
    r.headers_out.content_type = (ngx_str_t) { 9, (u_char *) "text/html" };
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "non-matching stream_types should keep streaming enabled");

    r.headers_out.content_type = (ngx_str_t) { 0, NULL };
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "NULL content-type data should not trigger exclusions");

    conf.routing.stream_types = NULL;
    r.headers_out.content_type = (ngx_str_t) { 9, (u_char *) "text/html" };
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "if_modified_since_only should keep streaming path");
    conf.policy.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;

    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;
    r.headers_out.content_length_n = 10;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "auto with small content-length should route full-buffer");
    TEST_ASSERT(selection.reason == NGX_HTTP_MARKDOWN_STREAM_REASON_BELOW_THRESHOLD,
        "auto with small content-length should preserve below_threshold reason");

    r.headers_out.content_length_n = -1;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "auto without content-length should route streaming");

    r.headers_out.content_length_n = 2048;
    conf.stream.threshold = 1024;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "auto with large CL should route streaming");

    r.headers_out.content_length_n = -1;
    selection = ngx_http_markdown_select_processing_path(&r, &conf, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_STREAMING,
        "auto without CL should route streaming");

    TEST_PASS("path-selection branches covered");
}

/*
 * Test streaming_update_headers success and failure branches.  Verifies:
 * - update_headers delegates to stream_commit_headers on success
 * - update_headers returns NGX_ERROR when stream_commit_headers fails
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
    g_stream_commit_headers_called = 0;
    rc = ngx_http_markdown_streaming_update_headers(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "update_headers should succeed");
    TEST_ASSERT(g_stream_commit_headers_called == 1,
        "update_headers delegates to stream_commit_headers");

    g_stream_commit_headers_rc = NGX_ERROR;
    g_stream_commit_headers_called = 0;
    rc = ngx_http_markdown_streaming_update_headers(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "stream_commit_headers failure propagated");

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
    ngx_chain_t            *pending_anchor;
    u_char                  data[] = "hello";
    ngx_int_t               rc;

    TEST_SUBSECTION("send_output and resume_pending backpressure branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    ctx.streaming.ttfb.feed_start_ms = 10;
    g_now.sec = 11;
    g_now.msec = 0;

    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_send_output(
        &r, &ctx, data, sizeof(data) - 1, 0);
    TEST_ASSERT(rc == NGX_OK, "send_output should succeed");
    TEST_ASSERT(ctx.streaming.flushes_sent == 1,
        "flush counter should increment on success");
    TEST_ASSERT(ctx.streaming.ttfb.recorded == 1,
        "successful first send should record TTFB");

    ctx.streaming.ttfb.feed_start_ms = 20;
    ctx.streaming.ttfb.recorded = 0;
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_send_output(
        &r, &ctx, data, sizeof(data) - 1, 0);
    TEST_ASSERT(rc == NGX_AGAIN, "send_output should propagate NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "NGX_AGAIN should store pending output");
    pending_anchor = ctx.streaming.pending_output;
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "NGX_AGAIN should set buffered flag");

    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_AGAIN,
        "resume_pending should preserve persistent backpressure");
    TEST_ASSERT(g_next_body_filter_last_in == NULL,
        "resume_pending must drain downstream-owned state with NULL");
    TEST_ASSERT(ctx.streaming.pending_output == pending_anchor,
        "persistent backpressure must preserve the pending anchor");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "persistent backpressure must preserve the buffered flag");

    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "resume_pending should drain pending chain");
    TEST_ASSERT(g_next_body_filter_last_in == NULL,
        "successful resume must not resubmit the original chain");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "resume should clear pending chain");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 0,
        "resume should clear pending_has_data on successful drain");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) == 0,
        "resume should clear buffered flag");

    ngx_memzero(&b_ok, sizeof(b_ok));
    ngx_memzero(&c_ok, sizeof(c_ok));
    c_ok.buf = &b_ok;
    c_ok.next = NULL;
    ctx.streaming.completion.pending_terminal_metrics = 1;
    ctx.streaming.pending_output = &c_ok;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "terminal metric latch should not fail resume");
    TEST_ASSERT(ctx.streaming.completion.pending_terminal_metrics == 0,
        "resume should clear pending terminal metrics");

    ngx_memzero(&b_err, sizeof(b_err));
    ngx_memzero(&c_err, sizeof(c_err));
    c_err.buf = &b_err;
    c_err.next = NULL;
    ctx.streaming.pending_output = &c_err;
    g_next_body_filter_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR, "resume should propagate downstream error");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 0,
        "resume error should clear pending_has_data");
    TEST_ASSERT(ctx.streaming.completion.failure_recorded == 1,
        "resume error should record post-commit failure");

    ctx.streaming.completion.finalize_pending_lastbuf = 1;
    ctx.streaming.pending_output = NULL;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "deferred last_buf should be sent on resume");

    ctx.streaming.main_terminal_sent = 0;
    ctx.streaming.subrequest_terminal_sent = 0;
    rc = ngx_http_markdown_streaming_send_output(
        &r, &ctx, NULL, 0, /* last_buf */ 1);
    TEST_ASSERT(rc == NGX_OK,
        "main-request terminal output should succeed");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 1,
        "main-request terminal should latch main_terminal_sent");
    TEST_ASSERT(ctx.streaming.subrequest_terminal_sent == 0,
        "main-request terminal must not latch subrequest terminal state");

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
        &r, &ctx, NULL, 0, 1);
    TEST_ASSERT(rc == NGX_ERROR,
        "send_output should fail when chain-link allocation fails");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 0,
        "chain-link allocation failure must not latch terminal sent");

    r.buffered = 0;
    ctx.streaming.pending_output = (ngx_chain_t *) (uintptr_t) 0x1;
    rc = ngx_http_markdown_streaming_handle_backpressure(&r, &ctx);
    TEST_ASSERT(rc == NGX_AGAIN,
        "backpressure helper should return NGX_AGAIN");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "backpressure helper should set buffered flag");
    ctx.streaming.pending_output = NULL;

    ctx.streaming.completion.failure_recorded = 0;
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_send_deferred_lastbuf(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_AGAIN,
        "deferred last_buf should propagate backpressure");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 0,
        "deferred last_buf backpressure must not latch terminal sent "
        "until pending drain confirms delivery");
    TEST_ASSERT(ctx.streaming.completion.pending_terminal_metrics == 1,
        "deferred last_buf backpressure should latch terminal metrics");

    ctx.streaming.completion.pending_terminal_metrics = 0;
    ctx.streaming.completion.failure_recorded = 0;
    ctx.streaming.pending_output = NULL;
    ctx.streaming.main_terminal_sent = 0;
    g_next_body_filter_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_send_deferred_lastbuf(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "deferred last_buf should propagate hard downstream errors");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 0,
        "deferred last_buf hard error must not latch terminal sent");
    TEST_ASSERT(ctx.streaming.completion.failure_recorded == 1,
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
    ctx.conversion.attempted = 1;
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
    TEST_ASSERT(ctx.conversion.attempted == 0,
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
 * - precommit reject policy finalizes with NGX_ERROR (error_status set)
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
    TEST_ASSERT(ctx.streaming.completion.failure_recorded == 1,
        "postcommit error should record failure once");
    TEST_ASSERT(metrics.streaming.budget_exceeded_total == 1,
        "memory-limit postcommit should classify budget exceeded");
    TEST_ASSERT(g_log_decision_calls == 2,
        "postcommit budget failure should log classification and terminal reason");

    ngx_http_markdown_streaming_record_postcommit_failure(
        &r, &ctx, &conf);
    TEST_ASSERT(metrics.streaming.postcommit_error_total == 1,
        "repeated postcommit recording must not increment metrics");
    TEST_ASSERT(g_log_decision_calls == 2,
        "repeated postcommit recording must not duplicate terminal reason");

    ctx.streaming.completion.failure_recorded = 0;
    ctx.streaming.handle = (struct StreamingConverterHandle *) (uintptr_t) 0x4;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    conf.error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;
    rc = ngx_http_markdown_streaming_precommit_error(
        &r, &ctx, &conf, ERROR_INTERNAL);
    TEST_ASSERT(rc == NGX_ERROR,
        "precommit reject policy finalizes with NGX_ERROR (error_status set)");
    TEST_ASSERT(metrics.streaming.precommit_reject_total == 1,
        "precommit reject metric should increment");

    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
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
 * Covers: test_count_chain_bufs (local helper mirroring removed production code),
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
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;

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

    rc = test_count_chain_bufs(&c1);
    TEST_ASSERT(rc == 2, "count_chain_bufs should count only non-NULL buffers");

    pending.buf = &pending_buf;
    pending.next = NULL;
    ctx.streaming.pending_output = &pending;
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_handle_null_input(
        &r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_AGAIN,
        "null-input handler should propagate resume backpressure");

    ctx.streaming.pending_output = NULL;
    ctx.streaming.completion.finalize_after_pending = 1;
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
        &r, &ctx, &conf, &c1, &last_buf, &fallback_cl);
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
    ctx.streaming.failopen_replay_initialized = 0;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    c1.buf = &b1;
    b1.pos = (u_char *) "x";
    b1.last = b1.pos + 1;
    b1.last_buf = 0;
    c1.next = NULL;
    rc = ngx_http_markdown_streaming_body_filter(&r, &c1);
    TEST_ASSERT(rc == g_next_body_filter_rc,
        "Post-Commit passthrough with no replay data should forward chain");

    TEST_PASS("null-input/tracking/body-filter entry branches covered");
}

/*
 * Test init_handle and chunk_result helper branches.  Verifies:
 * - init_handle succeeds on happy path, sets handle, enters pre-commit
 *   state, and marks conversion attempted
 * - prepare-options failure routes through precommit_error
 * - handle-create failure routes through precommit_error
 * - NULL handle on success code still fails
 * - cleanup registration failure routes through precommit_error
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

    conf.stream.budget = 256;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK, "init_handle should succeed on happy path");
    TEST_ASSERT(ctx.streaming.handle != NULL,
        "init_handle should set streaming handle");
    TEST_ASSERT(ctx.streaming.commit_state == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE,
        "init_handle should enter pre-commit state");
    TEST_ASSERT(ctx.conversion.attempted == 1,
        "init_handle should mark conversion attempted");

    ctx.streaming.handle = NULL;
    ctx.streaming.prebuffer_initialized = 0;
    ctx.streaming.failopen_replay_initialized = 0;
    ctx.conversion.attempted = 0;
    conf.ops.otel_enabled = 1;
    r.uri.data = (u_char *) "/private/customer/12345?token=secret";
    r.uri.len = sizeof("/private/customer/12345?token=secret") - 1;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "init_handle should succeed when OTel is enabled");
    TEST_ASSERT(g_otel_span_start_calls == 1,
        "OTel enabled should start a span");
    TEST_ASSERT(g_otel_uri_route_seen == 1,
        "OTel enabled should emit the redacted URI route label");
    TEST_ASSERT(g_otel_uri_attr_seen == 0,
        "OTel enabled should not emit a full URI attribute");
    TEST_ASSERT(g_otel_full_uri_seen == 0,
        "OTel enabled should not copy the full request URI");
    conf.ops.otel_enabled = 0;

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
    TEST_ASSERT(rc == NGX_DECLINED,
        "cleanup registration failure should honor PASS policy");

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
        &r, &ctx, &in, NGX_AGAIN);
    TEST_ASSERT(rc == NGX_AGAIN,
        "chunk_result should propagate NGX_AGAIN");

    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_OK);
    TEST_ASSERT(rc == NGX_OK, "chunk_result should keep NGX_OK");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_ERROR);
    TEST_ASSERT(rc == NGX_DONE,
        "chunk_result should return NGX_DONE after fallback switch");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 0;
    g_forward_headers_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_ERROR);
    TEST_ASSERT(rc == NGX_ERROR,
        "chunk_result fail-open should surface header-forward errors");

    ctx.eligible = 1;
    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_ERROR);
    TEST_ASSERT(rc == NGX_ERROR,
        "chunk_result should propagate non-special errors");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 0;
    ctx.headers_forwarded = 1;
    ctx.failopen_completed = 0;
    g_forward_headers_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_chunk_result(
        &r, &ctx, &in, NGX_ERROR);
    TEST_ASSERT(ctx.failopen_completed == 1,
        "chunk_result should set ctx.failopen_completed on successful fail-open");
    ctx.eligible = 1;
    ctx.headers_forwarded = 0;

    /*
     * Replay buffer init failure (second buffer_init call in
     * init_handle after prebuffer succeeds) must route through
     * precommit_error, not silently continue streaming.
     */
    ctx.streaming.handle = NULL;
    ctx.streaming.prebuffer_initialized = 0;
    ctx.streaming.failopen_replay_initialized = 0;
    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.conversion.attempted = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    conf.error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;
    g_buffer_init_fail_after = 1;
    g_buffer_init_call_count = 0;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "replay buffer init failure with reject policy finalizes with NGX_ERROR");
    TEST_ASSERT(ctx.streaming.handle == NULL,
        "replay buffer init failure should abort handle");
    g_buffer_init_fail_after = 0;
    g_buffer_init_call_count = 0;

    ctx.streaming.handle = NULL;
    ctx.streaming.prebuffer_initialized = 0;
    ctx.streaming.failopen_replay_initialized = 0;
    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.conversion.attempted = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    g_buffer_init_fail_after = 1;
    g_buffer_init_call_count = 0;
    rc = ngx_http_markdown_streaming_init_handle(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED,
        "replay buffer init failure with pass policy should route fail-open (NGX_DECLINED)");
    TEST_ASSERT(ctx.eligible == 0,
        "replay buffer init failure with pass policy should set eligible=0");
    g_buffer_init_fail_after = 0;
    g_buffer_init_call_count = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

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
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer.size = 3;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x11;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;

    g_stream_commit_headers_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_commit(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED,
        "header update failure triggers full-buffer fallback");
    g_stream_commit_headers_rc = NGX_OK;

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
    TEST_ASSERT(ctx.streaming.completion.failure_recorded == 1,
        "post-commit feed errors should record failure once");

    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.completion.failure_recorded = 0;
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
    ctx.streaming.output.bytes = NGX_MAX_SIZE_T_VALUE;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_SUCCESS, out_data, 1);
    TEST_ASSERT(rc == NGX_OK,
        "successful feed result should send output");
    TEST_ASSERT(ctx.streaming.output.bytes == NGX_MAX_SIZE_T_VALUE,
        "output byte counter should saturate on overflow");

    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x14;
    ctx.streaming.output.bytes = 0;
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
    ctx.streaming.pending_output = NULL;
    g_streaming_finalize_rc = ERROR_INTERNAL;
    g_next_body_filter_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "post-commit finalize errors should terminate downstream");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x17;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.pending_output = NULL;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = out_data;
    g_streaming_finalize_result.markdown_len = 3;
    g_streaming_finalize_result.etag = (uint8_t *) "secret-etag-value";
    g_streaming_finalize_result.etag_len =
        sizeof("secret-etag-value") - 1;
    g_streaming_finalize_result.token_estimate = 9;
    g_streaming_finalize_result.peak_memory_estimate = 128;
    r.uri.data = (u_char *) "/private/customer/12345?token=secret";
    r.uri.len = sizeof("/private/customer/12345?token=secret") - 1;
    g_info_log_count = 0;
    g_last_info_log_fmt = NULL;
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
    TEST_ASSERT(g_info_log_count == 1,
        "finalize should emit one info-level ETag summary");
    TEST_ASSERT(g_last_info_log_fmt != NULL,
        "finalize info log format should be captured");
    TEST_ASSERT(strstr(g_last_info_log_fmt, "etag=%*s") == NULL,
        "finalize info log must not format the full ETag");
    TEST_ASSERT(strstr(g_last_info_log_fmt, "uri=%V") == NULL,
        "finalize info log must not format the full URI");
    TEST_ASSERT(strstr(g_last_info_log_fmt, "etag_len=%uz") != NULL,
        "finalize info log should retain ETag length observability");
    TEST_ASSERT(strstr(g_last_info_log_fmt, "uri_len=%uz") != NULL,
        "finalize info log should retain URI length observability");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x18;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.completion.finalize_pending_lastbuf = 0;
    ctx.streaming.pending_output = NULL;
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
    TEST_ASSERT(ctx.streaming.completion.finalize_pending_lastbuf == 1,
        "finalize should latch deferred terminal last_buf");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x19;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.completion.pending_terminal_metrics = 0;
    ctx.streaming.pending_output = NULL;
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
    TEST_ASSERT(ctx.streaming.completion.pending_terminal_metrics == 1,
        "terminal backpressure should latch pending terminal metrics");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x1A;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.completion.pending_terminal_metrics = 0;
    ctx.streaming.completion.failure_recorded = 0;
    ctx.streaming.pending_output = NULL;
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
    TEST_ASSERT(ctx.streaming.completion.failure_recorded == 1,
        "terminal last_buf hard errors should record failure");

    TEST_PASS("commit/feed-result/finalize core branches covered");
}

/*
 * Test process-chain, failopen passthrough, and body_filter deep branches.
 * Verifies:
 * - failopen passthrough passes current chain for first invocation
 * - failopen passthrough fails on prefix chain allocation errors
 * - failopen passthrough rebuilds prefix chain on reentry
 * - process_chain fails when reject policy applies
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
    u_char                  prebuf_data[16];
    u_char                  chunk_data[] = "chunk";

    TEST_SUBSECTION("process-chain/failopen/body-filter deep branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    conf.max_size = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

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

    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.data = prebuf_data;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.streaming.failopen_replay_buf.capacity = sizeof(prebuf_data);
    ctx.streaming.failopen_replay_buf.max_size = sizeof(prebuf_data);
    ngx_memcpy(prebuf_data, "abc", 3);

    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in);
    TEST_ASSERT(rc == NGX_OK,
        "failopen passthrough should rebuild prefix chain from replay buffer");

    g_alloc_chain_fail_once = 1;
    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in);
    TEST_ASSERT(rc == NGX_ERROR,
        "failopen passthrough should fail on prefix chain allocation errors");

    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in);
    TEST_ASSERT(rc == NGX_OK,
        "failopen passthrough should rebuild prefix chain on reentry");

    ctx.streaming.failopen_replay_initialized = 0;
    ctx.eligible = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    rc = ngx_http_markdown_streaming_process_chain(
        &r, &ctx, &conf, &in, &last_buf, &fallback_cl);
    TEST_ASSERT(rc == NGX_OK,
        "process_chain should pass through when already in fail-open "
        "path regardless of reject policy (reject only applies at "
        "precommit_error decision point)");
    ctx.eligible = 1;

    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x22;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer.size = 3;
    ctx.streaming.failopen_replay_initialized = 0;
    g_streaming_feed_rc = ERROR_STREAMING_FALLBACK;
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    rc = ngx_http_markdown_streaming_process_chain(
        &r, &ctx, &conf, &in, &last_buf, &fallback_cl);
    TEST_ASSERT(rc == NGX_DONE,
        "process_chain should return NGX_DONE when fallback switches path");
    TEST_ASSERT(fallback_cl == &in,
        "process_chain should return fallback location");

    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 1;
    ctx.failopen_completed = 0;
    ctx.headers_forwarded = 0;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x27;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.data = prebuf_data;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.streaming.failopen_replay_buf.capacity = sizeof(prebuf_data);
    ctx.streaming.failopen_replay_buf.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer.size = 3;
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    in_buf.last_buf = 1;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    g_next_body_filter_rc = NGX_OK;
    g_next_body_filter_calls = 0;
    g_buffer_append_fail_after = 1;
    g_buffer_append_call_count = 0;
    rc = ngx_http_markdown_streaming_body_filter(&r, &in);
    TEST_ASSERT(rc == NGX_OK,
        "body_filter should reenter full-buffer path after fallback");
    g_buffer_append_fail_after = 0;
    g_buffer_append_call_count = 0;

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
    ctx.streaming.failopen_replay_initialized = 0;
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

    /*
     * Replay buffer append failure during Pre-Commit must trigger
     * precommit_error immediately rather than continuing streaming
     * with an incomplete replay buffer.
     */
    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x25;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.data = prebuf_data;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.streaming.failopen_replay_buf.capacity = sizeof(prebuf_data);
    ctx.streaming.failopen_replay_buf.max_size = sizeof(prebuf_data);
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    in_buf.last_buf = 0;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    conf.error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;
    g_buffer_append_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_process_chain(
        &r, &ctx, &conf, &in, &last_buf, &fallback_cl);
    TEST_ASSERT(rc == NGX_ERROR,
        "replay append failure with reject policy finalizes with NGX_ERROR");
    g_buffer_append_rc = NGX_OK;

    /*
     * Replay append failure with pass policy: precommit_error sets
     * eligible=0 and returns NGX_DECLINED, then failopen_passthrough
     * must be called to forward the original chain downstream.
     * The result should be whatever ngx_http_next_body_filter returns
     * (NGX_OK by default).
     */
    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x26;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.data = prebuf_data;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.streaming.failopen_replay_buf.capacity = sizeof(prebuf_data);
    ctx.streaming.failopen_replay_buf.max_size = sizeof(prebuf_data);
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    in_buf.last_buf = 0;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    g_next_body_filter_rc = NGX_OK;
    g_buffer_append_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_process_chain(
        &r, &ctx, &conf, &in, &last_buf, &fallback_cl);
    TEST_ASSERT(rc == NGX_OK,
        "replay append failure with pass policy should fail-open "
        "passthrough (forward chain downstream)");
    TEST_ASSERT(ctx.eligible == 0,
        "replay append failure with pass policy should set eligible=0");
    TEST_ASSERT(ctx.failopen_completed == 1,
        "replay append failure with pass policy should set "
        "ctx.failopen_completed=1");
    g_buffer_append_rc = NGX_OK;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    /*
     * Once markdown_streaming_feed consumes an upstream buffer, downstream
     * NGX_AGAIN applies only to the generated output.  The input position must
     * advance so NGINX can release its busy buffer and continue upstream I/O.
     */
    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x28;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.prebuffer_initialized = 0;
    ctx.streaming.failopen_replay_initialized = 0;
    ctx.streaming.completion.finalize_after_pending = 0;
    ctx.failopen_completed = 0;
    conf.stream.zero_copy = 0;
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    in_buf.last_buf = 0;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = chunk_data;
    g_streaming_feed_out_len = 5;
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_process_chain(
        &r, &ctx, &conf, &in, &last_buf, &fallback_cl);
    TEST_ASSERT(rc == NGX_AGAIN,
        "process_chain should propagate output backpressure");
    TEST_ASSERT(in_buf.pos == in_buf.last,
        "consumed upstream input must advance when output returns NGX_AGAIN");
    ctx.streaming.pending_output = NULL;
    ctx.streaming.pending_meta.has_data = 0;

    /*
     * Precise test: prebuffer append succeeds, replay append fails,
     * buffer has last_buf=1.  Use g_buffer_append_fail_after=1
     * so only the first append (prebuffer) succeeds and the second
     * (replay) fails.  body_filter should fail-open passthrough
     * and NOT enter finalize_request (which would send a duplicate
     * empty last_buf since handle is NULL).
     *
     * Verify by counting next_body_filter calls: should be exactly
     * 1 (the fail-open passthrough), not 2 (passthrough + finalize
     * empty terminal).
     */
    ctx.processing_path = NGX_HTTP_MARKDOWN_PATH_STREAMING;
    ctx.eligible = 1;
    ctx.failopen_completed = 0;
    ctx.headers_forwarded = 0;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x27;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.data = prebuf_data;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.streaming.failopen_replay_buf.capacity = sizeof(prebuf_data);
    ctx.streaming.failopen_replay_buf.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.streaming.prebuffer.size = 3;
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    in_buf.last_buf = 1;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    g_next_body_filter_rc = NGX_OK;
    g_next_body_filter_calls = 0;
    g_buffer_append_fail_after = 1;
    g_buffer_append_call_count = 0;
    rc = ngx_http_markdown_streaming_body_filter(&r, &in);
    TEST_ASSERT(rc == NGX_OK,
        "terminal replay append failure with pass policy should "
        "fail-open passthrough via body_filter");
    TEST_ASSERT(ctx.eligible == 0,
        "terminal replay append failure should set eligible=0");
    TEST_ASSERT(g_next_body_filter_calls == 1,
        "terminal replay append failure should call next_body_filter "
        "exactly once (fail-open passthrough only, no finalize empty "
        "last_buf)");
    g_buffer_append_fail_after = 0;
    g_buffer_append_call_count = 0;
    g_buffer_append_rc = NGX_OK;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

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
    ngx_http_markdown_path_selection_t selection;
    ngx_uint_t              free_before;

    TEST_SUBSECTION("streaming helper gap branches");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);

    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    conf.max_size = 64;

    selection = ngx_http_markdown_select_processing_path(&r, NULL, NULL);
    TEST_ASSERT(selection.path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
        "selector should use full-buffer when conf is NULL");
    TEST_ASSERT(selection.reason == NGX_HTTP_MARKDOWN_STREAM_REASON_CONFIG_DISABLED,
        "selector should report config_disabled when conf is NULL");

    ctx.streaming.completion.finalize_pending_lastbuf = 1;
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
    ctx.streaming.completion.finalize_pending_lastbuf = 1;
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
    g_stream_commit_headers_rc = NGX_ERROR;
    free_before = g_output_free_calls;
    rc = ngx_http_markdown_streaming_handle_feed_result(
        &r, &ctx, &conf, ERROR_SUCCESS, out_data, 2);
    TEST_ASSERT(rc == NGX_DECLINED,
        "feed success triggers full-buffer fallback on commit failure");
    TEST_ASSERT(g_output_free_calls == free_before + 1,
        "commit failure should still free feed output");
    g_stream_commit_headers_rc = NGX_OK;

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x33;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.output.bytes = NGX_MAX_SIZE_T_VALUE;
    ctx.streaming.completion.failure_recorded = 0;
    ctx.streaming.pending_output = NULL;
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
    TEST_ASSERT(ctx.streaming.output.bytes == NGX_MAX_SIZE_T_VALUE,
        "finalize markdown send should preserve saturated output counter");
    TEST_ASSERT(ctx.streaming.completion.failure_recorded == 1,
        "finalize markdown send error should record failure");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x34;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    g_streaming_finalize_rc = ERROR_SUCCESS;
    g_streaming_finalize_result.markdown = out_data;
    g_streaming_finalize_result.markdown_len = 2;
    g_stream_commit_headers_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_finalize_request(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DECLINED,
        "finalize pre-commit triggers fallback on commit errors");
    g_stream_commit_headers_rc = NGX_OK;

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
    TEST_ASSERT(rc == NGX_DONE,
        "cleanup allocation failure should follow PASS policy");

    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    g_prepare_options_rc = NGX_OK;
    g_new_with_code_rc = ERROR_SUCCESS;
    g_new_with_code_null_handle = 0;
    rc = ngx_http_markdown_streaming_ensure_handle(&r, &ctx, &conf, &in);
    TEST_ASSERT(rc == NGX_OK,
        "ensure_handle should return NGX_OK when init succeeds");

    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&in, sizeof(in));
    in.buf = &in_buf;
    in.next = NULL;
    ctx.eligible = 1;
    conf.stream.precommit_buffer = 0;
    g_prepare_options_rc = NGX_OK;
    g_new_with_code_rc = ERROR_SUCCESS;
    g_new_with_code_null_handle = 0;
    rc = ngx_http_markdown_streaming_ensure_handle(&r, &ctx, &conf, &in);
    TEST_ASSERT(rc == NGX_OK,
        "ensure_handle should accept precommit_buffer 0");
    TEST_ASSERT(ctx.streaming.prebuffer_initialized == 0,
        "precommit_buffer 0 should leave prebuffer disabled");
    TEST_ASSERT(ctx.streaming.failopen_replay_initialized == 0,
        "precommit_buffer 0 should leave replay buffer disabled");
    TEST_ASSERT(g_buffer_init_call_count == 0,
        "precommit_buffer 0 should not call buffer init");

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x35;
    ctx.streaming.pending_output = NULL;
    ctx.streaming.completion.finalize_after_pending = 0;
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
    ctx.streaming.failopen_replay_initialized = 0;
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
    ctx.streaming.failopen_replay_initialized = 0;
    in_buf.last_buf = 1;
    ctx.streaming.pending_output = NULL;
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
    ctx.streaming.output.bytes = 0;
    ctx.streaming.pending_output = NULL;
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
 * Verifies failopen_passthrough NGX_AGAIN backpressure contract:
 * - On NGX_AGAIN from downstream, pending_output is set, buffered flag is set
 * - failopen_count is NOT incremented on NGX_AGAIN
 * - On subsequent NGX_OK (resume), failopen_count is incremented
 * - Works for both replay-buffer and no-replay-buffer paths
 *
 * Covers: Rule 1 (pending chain preserved on NGX_AGAIN),
 *         Rule 38 (failopen_count after downstream OK)
 */
static void
test_failopen_passthrough_again_pending(void)
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
    u_char                  chunk_data[] = "chunk";
    ngx_http_markdown_metrics_t metrics;

    TEST_SUBSECTION("failopen_passthrough NGX_AGAIN pending/resume");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;
    conf.max_size = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&in_buf, sizeof(in_buf));
    in.buf = &in_buf;
    in.next = NULL;
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    in_buf.last_buf = 0;

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x39;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.eligible = 0;

    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.data = prebuf_data;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.streaming.failopen_replay_buf.capacity = sizeof(prebuf_data);
    ctx.streaming.failopen_replay_buf.max_size = sizeof(prebuf_data);
    ngx_memcpy(prebuf_data, "abc", 3);

    if (ngx_http_markdown_metrics != NULL) {
        ngx_http_markdown_metrics->results.failopen_count = 0;
    }

    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "replay: failopen_passthrough should return NGX_AGAIN on backpressure");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "replay: pending_output must be set on NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 1,
        "replay: pending_has_data must be 1 on NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.completion.pending_failopen_delivery == 1,
        "replay: pending_failopen_delivery latch must be set on NGX_AGAIN");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "replay: request buffered flag must be set on NGX_AGAIN");
    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(ngx_http_markdown_metrics->results.failopen_count == 0,
            "replay: failopen_count must NOT increment on NGX_AGAIN");
    }

    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "replay: resume_pending should return NGX_OK on downstream success");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "replay: pending_output must be NULL after successful resume");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 0,
        "replay: pending_has_data must be 0 after successful resume");
    TEST_ASSERT(ctx.streaming.completion.pending_failopen_delivery == 0,
        "replay: pending_failopen_delivery latch must be cleared after resume");
    TEST_ASSERT((r.buffered & NGX_HTTP_MARKDOWN_BUFFERED) == 0,
        "replay: buffered flag must be cleared after successful resume");
    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(ngx_http_markdown_metrics->results.failopen_count == 1,
            "replay: failopen_count must increment after resume succeeds");
    }

    if (ngx_http_markdown_metrics != NULL) {
        ngx_http_markdown_metrics->results.failopen_count = 0;
    }
    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.failopen_completed = 0;

    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "replay: second NGX_AGAIN setup");

    g_next_body_filter_rc = NGX_DONE;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_DONE,
        "replay: resume_pending should return NGX_DONE on downstream NGX_DONE");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 0,
        "replay: pending_has_data must be 0 after NGX_DONE resume");
    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(ngx_http_markdown_metrics->results.failopen_count == 1,
            "replay: failopen_count must increment on NGX_DONE resume");
    }

    if (ngx_http_markdown_metrics != NULL) {
        ngx_http_markdown_metrics->results.failopen_count = 0;
    }
    ctx.streaming.failopen_replay_initialized = 0;
    ctx.streaming.failopen_replay_buf.size = 0;
    ctx.failopen_completed = 0;

    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "no-replay: failopen_passthrough should return NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "no-replay: pending_output must be set on NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.completion.pending_failopen_delivery == 1,
        "no-replay: pending_failopen_delivery latch must be set on NGX_AGAIN");
    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(ngx_http_markdown_metrics->results.failopen_count == 0,
            "no-replay: failopen_count must NOT increment on NGX_AGAIN");
    }

    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "no-replay: resume_pending should return NGX_OK on success");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 0,
        "no-replay: pending_has_data must be 0 after successful resume");
    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(ngx_http_markdown_metrics->results.failopen_count == 1,
            "no-replay: failopen_count must increment after resume succeeds");
    }

    if (ngx_http_markdown_metrics != NULL) {
        ngx_http_markdown_metrics->results.failopen_count = 0;
    }
    ctx.streaming.failopen_replay_initialized = 0;
    ctx.streaming.failopen_replay_buf.size = 0;
    ctx.failopen_completed = 0;

    g_next_body_filter_rc = NGX_DONE;
    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in);
    TEST_ASSERT(rc == NGX_DONE,
        "no-replay: failopen_passthrough should return NGX_DONE on downstream NGX_DONE");
    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(ngx_http_markdown_metrics->results.failopen_count == 1,
            "no-replay: failopen_count must increment on NGX_DONE (delivery success)");
    }

    if (ngx_http_markdown_metrics != NULL) {
        ngx_http_markdown_metrics->results.failopen_count = 0;
    }
    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.failopen_completed = 0;

    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_failopen_passthrough(
        &r, &ctx, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "resume-failure: setup NGX_AGAIN");

    g_next_body_filter_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "resume-failure: resume_pending should return NGX_ERROR on downstream error");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 0,
        "resume-failure: pending_has_data must be 0 after resume failure");
    TEST_ASSERT(ctx.streaming.completion.pending_failopen_delivery == 0,
        "resume-failure: pending_failopen_delivery latch must be cleared on failure");
    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(ngx_http_markdown_metrics->results.failopen_count == 0,
            "resume-failure: failopen_count must NOT increment on resume failure");
    }

    TEST_PASS("failopen_passthrough NGX_AGAIN pending/resume");
}

/*
 * Regression: multi-link fail-open pending chain with terminal tail.
 *
 * Drives the real fail-open composition path: replay prefix (non-terminal)
 * + cloned input chain (containing the terminal buffer).  Downstream
 * returns NGX_AGAIN, so the full multi-link chain is retained as
 * pending_output.  NULL resume then drains it.
 *
 * Asserts:
 *   - Initial fail-open submission reaches downstream exactly once
 *     (call 1 = multi-link chain with terminal tail, in != NULL)
 *   - NULL resume reaches downstream exactly once
 *     (call 2 = in == NULL)
 *   - Total downstream calls == 2 (no third empty terminal send)
 *   - main_terminal_sent == 1 (terminal was in the tail, not the head)
 *   - pending_output == NULL after drain
 *   - failopen_count increments only after successful drain
 *
 * Covers: Rule 1 (NULL resume), Rule 38 (failopen_count after OK),
 *         Rule 47 (terminal-sent latch only after confirmed delivery)
 */
static void
test_failopen_multilink_pending_terminal_tail(void)
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
    u_char                  chunk_data[] = "chunk";
    ngx_http_markdown_metrics_t metrics;

    TEST_SUBSECTION("fail-open multi-link pending chain with terminal tail");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;
    conf.max_size = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    /*
     * Build a fail-open scenario with replay buffer data.
     * The input chain carries the terminal buffer (last_buf=1).
     * failopen_passthrough composes: replay prefix (last_buf=0) ->
     * cloned input (last_buf=1).  This is a multi-link chain where
     * the head is non-terminal and the tail is terminal.
     */
    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&in_buf, sizeof(in_buf));
    in.buf = &in_buf;
    in.next = NULL;
    in_buf.pos = chunk_data;
    in_buf.last = chunk_data + 5;
    in_buf.last_buf = 1;        /* terminal in the input (tail of chain) */
    in_buf.last_in_chain = 0;

    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x39;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.prebuffer_initialized = 1;
    ctx.streaming.prebuffer.data = prebuf_data;
    ctx.streaming.prebuffer.capacity = sizeof(prebuf_data);
    ctx.streaming.prebuffer.max_size = sizeof(prebuf_data);
    ctx.eligible = 0;

    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.data = prebuf_data;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.streaming.failopen_replay_buf.capacity = sizeof(prebuf_data);
    ctx.streaming.failopen_replay_buf.max_size = sizeof(prebuf_data);
    ngx_memcpy(prebuf_data, "abc", 3);

    ctx.headers_forwarded = 1;  /* skip header forwarding */

    if (ngx_http_markdown_metrics != NULL) {
        ngx_http_markdown_metrics->results.failopen_count = 0;
    }

    /*
     * Step 1: failopen_passthrough composes replay prefix + cloned input
     *         and submits to downstream.  Downstream returns NGX_AGAIN.
     *         The full multi-link chain is retained as pending_output.
     */
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_failopen_passthrough(&r, &ctx, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "multi-link: failopen_passthrough should return NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "multi-link: pending_output must be set on NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.completion.pending_failopen_delivery == 1,
        "multi-link: pending_failopen_delivery latch must be set");

    /*
     * Call 1 must be the multi-link chain (in != NULL) with a terminal
     * tail (any_last_buf == 1).  The head is the replay prefix
     * (last_buf=0), so head-only detection would miss the terminal.
     */
    TEST_ASSERT(g_body_filter_hist_len >= 1,
        "multi-link: at least one downstream call expected");
    TEST_ASSERT(g_body_filter_hist[0].is_null == 0,
        "multi-link: call 1 must have non-NULL input (the composed chain)");
    TEST_ASSERT(g_body_filter_hist[0].any_last_buf == 1,
        "multi-link: call 1 chain must carry last_buf in a link (terminal tail)");

    /*
     * pending_meta must have captured main_terminal=1 BEFORE the
     * downstream call, even though the chain head is non-terminal.
     */
    TEST_ASSERT(ctx.streaming.pending_meta.main_terminal == 1,
        "multi-link: pending_meta.main_terminal must be captured before ownership crossing");

    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(ngx_http_markdown_metrics->results.failopen_count == 0,
            "multi-link: failopen_count must NOT increment on NGX_AGAIN");
    }

    /*
     * Step 2: NULL resume drains the downstream-retained chain.
     *         Downstream returns NGX_OK.
     */
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "multi-link: resume_pending should return NGX_OK on downstream success");

    /*
     * Call 2 must be the NULL resume (in == NULL).
     */
    TEST_ASSERT(g_body_filter_hist_len >= 2,
        "multi-link: two downstream calls expected");
    TEST_ASSERT(g_body_filter_hist[1].is_null == 1,
        "multi-link: call 2 must be NULL resume");

    /*
     * CRITICAL: exactly 2 downstream calls — no third empty terminal send.
     */
    TEST_ASSERT(g_next_body_filter_calls == 2,
        "multi-link: exactly 2 downstream calls (no duplicate empty terminal)");

    /*
     * main_terminal_sent must be latched only after confirmed delivery.
     */
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 1,
        "multi-link: main_terminal_sent must be 1 after confirmed drain");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "multi-link: pending_output must be NULL after successful drain");
    TEST_ASSERT(ctx.streaming.pending_meta.main_terminal == 0,
        "multi-link: pending_meta.main_terminal must be cleared after drain");
    TEST_ASSERT(ctx.streaming.completion.pending_failopen_delivery == 0,
        "multi-link: pending_failopen_delivery must be cleared after drain");

    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(ngx_http_markdown_metrics->results.failopen_count == 1,
            "multi-link: failopen_count must increment after successful drain");
    }

    TEST_PASS("fail-open multi-link pending chain with terminal tail");
}

/*
 * Regression: subrequest terminal (last_in_chain) must not latch
 * main_terminal_sent.
 *
 * For a subrequest (r != r->main), the terminal marker is last_in_chain,
 * not last_buf.  The pending chain may carry last_in_chain=1 in a
 * non-head tail link.  resume_pending must detect this via captured
 * metadata but must NOT latch main_terminal_sent (which is main-request
 * lifecycle only).
 */
static void
test_subrequest_pending_terminal_last_in_chain(void)
{
    ngx_http_request_t       r;
    ngx_http_request_t       main_r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              in_head;
    ngx_chain_t              in_tail;
    ngx_buf_t                head_buf;
    ngx_buf_t                tail_buf;
    u_char                   head_data[] = "data";
    ngx_int_t                rc;

    TEST_SUBSECTION("subrequest pending terminal last_in_chain");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    /*
     * Make this a subrequest: r->main points to a different request.
     */
    ngx_memzero(&main_r, sizeof(main_r));
    r.main = &main_r;

    conf.max_size = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    ctx.headers_forwarded = 1;
    ctx.eligible = 0;

    /*
     * Build a 2-link chain: head (non-terminal, has data) + tail
     * (last_in_chain=1, last_buf=0).  This models a subrequest
     * terminal marker in the tail.
     */
    ngx_memzero(&head_buf, sizeof(head_buf));
    ngx_memzero(&tail_buf, sizeof(tail_buf));
    head_buf.pos = head_data;
    head_buf.last = head_data + 4;
    head_buf.last_buf = 0;
    head_buf.last_in_chain = 0;
    tail_buf.last_buf = 0;
    tail_buf.last_in_chain = 1;   /* subrequest terminal */

    in_head.buf = &head_buf;
    in_head.next = &in_tail;
    in_tail.buf = &tail_buf;
    in_tail.next = NULL;

    /*
     * Use the no-replay failopen path (replay not initialized) so
     * clone_chain_links produces a 2-link clone with shared bufs.
     */
    ctx.streaming.failopen_replay_initialized = 0;
    ctx.streaming.failopen_replay_buf.size = 0;

    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_streaming_failopen_passthrough(&r, &ctx, &in_head);
    TEST_ASSERT(rc == NGX_AGAIN,
        "subrequest: failopen_passthrough should return NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "subrequest: pending_output must be set on NGX_AGAIN");

    /*
     * The pending chain head is non-terminal; the tail carries
     * last_in_chain=1.  pending_meta must capture subrequest_terminal.
     */
    TEST_ASSERT(ctx.streaming.pending_meta.subrequest_terminal == 1,
        "subrequest: pending_meta.subrequest_terminal must be captured");
    TEST_ASSERT(ctx.streaming.pending_meta.main_terminal == 0,
        "subrequest: pending_meta.main_terminal must be 0 (no last_buf)");

    /*
     * NULL resume drains the chain.
     */
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_resume_pending(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "subrequest: resume_pending should return NGX_OK");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "subrequest: pending_output must be NULL after drain");

    /*
     * CRITICAL: main_terminal_sent must NOT be latched for a subrequest
     * terminal.  last_in_chain is subrequest EOF, not main request EOF.
     */
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 0,
        "subrequest: main_terminal_sent must remain 0 for subrequest terminal");
    TEST_ASSERT(ctx.streaming.subrequest_terminal_sent == 1,
        "subrequest: confirmed resume must latch subrequest terminal state");
    TEST_ASSERT(ctx.streaming.pending_meta.subrequest_terminal == 0,
        "subrequest: pending_meta.subrequest_terminal must be cleared after drain");

    /*
     * Exactly 2 downstream calls: the fail-open submission + NULL resume.
     */
    TEST_ASSERT(g_next_body_filter_calls == 2,
        "subrequest: exactly 2 downstream calls");

    TEST_PASS("subrequest pending terminal last_in_chain");
}

/*
 * Exercise the production input-lifecycle helpers added for generic
 * downstream backpressure.  These assertions call the real static helpers
 * from ngx_http_markdown_streaming_impl.h rather than simulating their state.
 */
static void
test_pending_input_production_lifecycle(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              current;
    ngx_chain_t              future;
    ngx_chain_t              future_tail;
    ngx_chain_t              empty_terminal;
    ngx_chain_t              pending_output;
    ngx_buf_t                current_buf;
    ngx_buf_t                future_buf;
    ngx_buf_t                future_tail_buf;
    ngx_buf_t                empty_terminal_buf;
    ngx_buf_t                pending_buf;
    ngx_chain_t             *fallback_cl;
    ngx_flag_t               last_buf;
    ngx_int_t                rc;
    u_char                   current_data[] = "terminal";
    u_char                   future_data[] = "future";

    TEST_SUBSECTION("production pending-input lifecycle");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);

    ngx_memzero(&current, sizeof(current));
    ngx_memzero(&future, sizeof(future));
    ngx_memzero(&future_tail, sizeof(future_tail));
    ngx_memzero(&empty_terminal, sizeof(empty_terminal));
    ngx_memzero(&pending_output, sizeof(pending_output));
    ngx_memzero(&current_buf, sizeof(current_buf));
    ngx_memzero(&future_buf, sizeof(future_buf));
    ngx_memzero(&future_tail_buf, sizeof(future_tail_buf));
    ngx_memzero(&empty_terminal_buf, sizeof(empty_terminal_buf));
    ngx_memzero(&pending_buf, sizeof(pending_buf));

    current.buf = &current_buf;
    current_buf.pos = current_data;
    current_buf.last = current_data + sizeof(current_data) - 1;
    current_buf.last_buf = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x41;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    g_streaming_feed_rc = ERROR_SUCCESS;
    g_streaming_feed_out_data = current_data;
    g_streaming_feed_out_len = sizeof(current_data) - 1;
    g_next_body_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_streaming_process_chain(
        &r, &ctx, &conf, &current, &last_buf, &fallback_cl);
    TEST_ASSERT(rc == NGX_AGAIN,
        "terminal current link should propagate output backpressure");
    TEST_ASSERT(current_buf.pos == current_buf.last,
        "terminal current link should be consumed exactly once");
    TEST_ASSERT(ctx.streaming.completion.upstream_terminal_seen == 1,
        "current terminal must latch EOF without a queued remainder");

    ngx_http_markdown_streaming_pending_input_clear(&ctx);
    ctx.streaming.completion.upstream_terminal_seen = 0;
    empty_terminal.buf = &empty_terminal_buf;
    empty_terminal_buf.last_buf = 1;
    rc = ngx_http_markdown_streaming_pending_input_enqueue_remainder(
        &r, &ctx, &conf, &empty_terminal, NULL);
    TEST_ASSERT(rc == NGX_OK,
        "zero-length terminal remainder should enqueue successfully");
    TEST_ASSERT(ctx.streaming.pending_input.head == NULL,
        "zero-length terminal remainder should not allocate a queue link");
    TEST_ASSERT(ctx.streaming.completion.upstream_terminal_seen == 1,
        "zero-length terminal remainder must retain upstream EOF");

    ctx.streaming.pending_input.bytes = 3;
    ctx.streaming.pending_input.links = 1;
    conf.max_size = 4;
    future.buf = &future_buf;
    future_buf.pos = future_data;
    future_buf.last = future_data + sizeof(future_data) - 1;
    {
        uint32_t  enqueue_error = ERROR_SUCCESS;
        rc = ngx_http_markdown_streaming_pending_input_enqueue_remainder(
            &r, &ctx, &conf, &future, &enqueue_error);
        TEST_ASSERT(rc == NGX_ERROR,
            "retained input beyond the effective body limit must fail");
        TEST_ASSERT(enqueue_error == ERROR_BUDGET_EXCEEDED,
            "P2: budget rejection must classify as ERROR_BUDGET_EXCEEDED");
    }
    TEST_ASSERT(ctx.streaming.pending_input.head == NULL
                && ctx.streaming.pending_input.bytes == 3
                && ctx.streaming.pending_input.links == 1,
        "budget rejection must leave the pending queue unchanged");

    ngx_http_markdown_streaming_pending_input_clear(&ctx);
    conf.max_size = 0;
    future.next = &future_tail;
    future_tail.buf = &future_tail_buf;
    future_tail_buf.pos = future_data;
    future_tail_buf.last = future_data + 1;
    g_alloc_chain_fail_after = 2;
    g_alloc_chain_call_count = 0;
    {
        uint32_t  enqueue_error = ERROR_SUCCESS;
        rc = ngx_http_markdown_streaming_pending_input_enqueue_remainder(
            &r, &ctx, &conf, &future, &enqueue_error);
        TEST_ASSERT(rc == NGX_ERROR,
            "a later chain-link allocation failure must reject the batch");
        TEST_ASSERT(enqueue_error == ERROR_MEMORY_LIMIT,
            "P2: allocation failure must classify as ERROR_MEMORY_LIMIT, "
            "not ERROR_BUDGET_EXCEEDED");
    }
    TEST_ASSERT(ctx.streaming.pending_input.head == NULL
                && ctx.streaming.pending_input.tail == NULL
                && ctx.streaming.pending_input.bytes == 0
                && ctx.streaming.pending_input.links == 0,
        "partial allocation failure must not publish a partial queue");
    g_alloc_chain_fail_after = 0;
    future.next = NULL;

    ngx_http_markdown_streaming_pending_input_clear(&ctx);
    conf.max_size = 0;
    ctx.streaming.pending_output = &pending_output;
    ctx.streaming.pending_meta.has_data = 0;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x42;
    future_buf.pos = future_data;
    future_buf.last = future_data + sizeof(future_data) - 1;
    rc = ngx_http_markdown_streaming_body_filter(&r, &future);
    TEST_ASSERT(rc == NGX_AGAIN,
        "an empty terminal pending output must still defer future input");
    TEST_ASSERT(ctx.streaming.pending_input.head != NULL
                && future_buf.pos != future_buf.last,
        "future input must be retained without feeding while output is pending");

    ctx.eligible = 0;
    ctx.streaming.handle = NULL;
    ctx.streaming.input_disposition = NGX_HTTP_MD_INPUT_RETAIN;
    ctx.streaming.completion.pending_failopen_delivery = 1;
    ctx.streaming.completion.failopen_active = 1;
    g_streaming_feed_calls = 0;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_null_input(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "future input should continue directly after fail-open drain");
    TEST_ASSERT(g_streaming_feed_calls == 0,
        "fail-open continuation must never re-enter the Rust converter");
    TEST_ASSERT(future_buf.pos == future_buf.last,
        "fail-open continuation should consume future input exactly once");

    ctx.streaming.pending_output = NULL;
    ngx_http_markdown_streaming_pending_input_clear(&ctx);
    ctx.eligible = 1;
    ctx.streaming.completion.failopen_active = 0;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx.streaming.pending_output = &pending_output;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x43;
    conf.max_size = 1;
    future_buf.pos = future_data;
    future_buf.last = future_data + sizeof(future_data) - 1;
    g_next_body_filter_calls = 0;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_new_input_with_pending(
        &r, &ctx, &conf, &future);
    TEST_ASSERT(rc == NGX_AGAIN,
        "post-commit enqueue failure must wait behind pending output");
    TEST_ASSERT(g_next_body_filter_calls == 0,
        "post-commit safe finish must not overtake pending output");
    rc = ngx_http_markdown_streaming_handle_null_input(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "deferred post-commit error should run after pending output drains");
    TEST_ASSERT(g_next_body_filter_calls == 2,
        "deferred post-commit error should drain old output before terminal send");

    ctx.streaming.pending_output = NULL;
    ngx_http_markdown_streaming_pending_input_clear(&ctx);
    ctx.streaming.input_disposition = NGX_HTTP_MD_INPUT_TERMINAL;
    ctx.streaming.handle = NULL;
    ctx.eligible = 1;
    future_buf.pos = future_data;
    future_buf.last = future_data + sizeof(future_data) - 1;
    g_streaming_feed_calls = 0;
    rc = ngx_http_markdown_streaming_body_filter(&r, &future);
    TEST_ASSERT(rc == NGX_OK,
        "terminal disposition should abandon future input before ensure_handle");
    TEST_ASSERT(future_buf.pos == future_buf.last
                && g_streaming_feed_calls == 0,
        "terminal disposition must neither recreate nor feed a Rust handle");

    ctx.streaming.input_disposition = NGX_HTTP_MD_INPUT_CONSUMED;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x44;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    rc = ngx_http_markdown_streaming_handle_postcommit_error(
        &r, &ctx, &conf, ERROR_POST_COMMIT);
    TEST_ASSERT(ctx.streaming.input_disposition
                == NGX_HTTP_MD_INPUT_TERMINAL,
        "post-commit termination must select TERMINAL input disposition");
    ctx.streaming.completion.upstream_terminal_seen = 1;
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_null_input(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "terminal pending output should complete after downstream drain");
    TEST_ASSERT(ctx.streaming.completion.upstream_terminal_seen == 0,
        "terminal drain must not re-enter upstream EOF finalization");

    TEST_PASS("production pending-input lifecycle covered");
}

/*
 * Test: P1-1 — ensure_handle() init-failure fail-open must set
 * failopen_active so future input bypasses Rust instead of
 * re-entering the converter with a NULL handle.
 *
 * Covers:
 *   - init_handle returns NGX_DECLINED (precommit_error with pass)
 *   - ensure_handle sends fail-open chain, downstream NGX_AGAIN
 *   - failopen_active must be latched at the policy selection point
 *   - future input must not re-enter markdown_streaming_feed
 */
static void
test_failopen_init_failure_latches_mode(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              in;
    ngx_chain_t              future;
    ngx_buf_t                in_buf;
    ngx_buf_t                future_buf;
    ngx_int_t                rc;
    u_char                   in_data[] = "initial";
    u_char                   future_data[] = "future";
    ngx_http_markdown_metrics_t metrics;

    TEST_SUBSECTION("P1-1: init-failure fail-open latches failopen_active");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&future, sizeof(future));
    ngx_memzero(&in_buf, sizeof(in_buf));
    ngx_memzero(&future_buf, sizeof(future_buf));
    in.buf = &in_buf;
    in_buf.pos = in_data;
    in_buf.last = in_data + sizeof(in_data) - 1;
    in.next = NULL;

    /*
     * Force init_handle to decline: prepare_options failure routes
     * through precommit_error with pass policy, which sets eligible=0
     * and (after the P1-1 fix) failopen_active=1.
     */
    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    ctx.headers_forwarded = 0;
    g_prepare_options_rc = NGX_ERROR;
    g_forward_headers_rc = NGX_OK;
    g_next_body_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_streaming_ensure_handle(&r, &ctx, &conf, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "ensure_handle init-decline + downstream NGX_AGAIN returns NGX_AGAIN");
    TEST_ASSERT(ctx.eligible == 0,
        "precommit_error pass policy must clear eligible");
    TEST_ASSERT(ctx.streaming.completion.failopen_active == 1,
        "P1-1: init-failure fail-open must latch failopen_active at "
        "the policy selection point so future input bypasses Rust");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "downstream NGX_AGAIN must retain pending_output");
    TEST_ASSERT(ctx.streaming.handle == NULL,
        "failed handle must remain NULL");
    TEST_ASSERT(metrics.conversions_attempted == 1,
        "init failure must record exactly one conversion attempt");
    TEST_ASSERT(metrics.conversions_failed == 1,
        "init failure must record exactly one conversion failure");
    TEST_ASSERT(metrics.streaming.failed_total == 1,
        "init failure must record exactly one streaming failure");
    TEST_ASSERT(metrics.results.failopen_count == 0,
        "fail-open delivery must not count before pending output drains");

    /*
     * Future input arrives while pending_output is downstream-owned.
     * handle_new_input_with_pending must route through the
     * failopen_active early branch, not precommit_error (which would
     * double-count conversions_failed) or failopen_passthrough (which
     * would build a new replay+input chain while the old output is
     * still owned downstream).
     */
    future.buf = &future_buf;
    future_buf.pos = future_data;
    future_buf.last = future_data + sizeof(future_data) - 1;
    future.next = NULL;
    g_streaming_feed_calls = 0;
    g_next_body_filter_calls = 0;

    rc = ngx_http_markdown_streaming_handle_new_input_with_pending(
        &r, &ctx, &conf, &future);
    TEST_ASSERT(rc == NGX_AGAIN,
        "failopen_active future input must return NGX_AGAIN");
    TEST_ASSERT(g_streaming_feed_calls == 0,
        "P1-1: failopen_active future input must never re-enter the "
        "Rust converter (NULL handle would be rejected)");
    TEST_ASSERT(g_next_body_filter_calls == 0,
        "P1-2: failopen_active future input must not submit a new "
        "chain while pending_output is still downstream-owned");

    /*
     * After pending_output drains (NULL resume), the failopen_active
     * branch in handle_null_input must send the retained future input
     * via continue_failopen_input (passthrough, no Rust), then send
     * terminal last_buf if upstream_terminal_seen.
     */
    g_next_body_filter_rc = NGX_OK;
    g_streaming_feed_calls = 0;
    rc = ngx_http_markdown_streaming_handle_null_input(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_OK,
        "failopen drain + future passthrough should complete");
    TEST_ASSERT(g_streaming_feed_calls == 0,
        "fail-open continuation must never re-enter the Rust converter");
    TEST_ASSERT(future_buf.pos == future_buf.last,
        "fail-open continuation should consume future input exactly once");
    TEST_ASSERT(metrics.conversions_attempted == 1
                && metrics.conversions_failed == 1
                && metrics.streaming.failed_total == 1,
        "fail-open continuation must not duplicate failure metrics");
    TEST_ASSERT(metrics.results.failopen_count == 1,
        "fail-open delivery must count after pending output drains");

    TEST_PASS("P1-1 init-failure failopen_active latch covered");
}

/*
 * Test: P1-2 — failopen_active + pending_output + future-input enqueue
 * failure must not submit a new chain while the old output is still
 * downstream-owned, must not re-enter precommit_error (double-counting
 * conversions_failed), and must latch a protocol-visible abort after drain.
 *
 * Covers:
 *   - failopen_active already latched
 *   - pending_output downstream-owned (NGX_AGAIN retained)
 *   - future input exceeds retained-input budget (enqueue fails)
 *   - send_failopen_chain pending_output guard fires before downstream
 *   - failopen_abort_after_pending latched
 *   - after drain, known data loss aborts without a terminal last_buf
 */
static void
test_failopen_active_enqueue_failure_aborts_safely(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              pending_output;
    ngx_chain_t              retained;
    ngx_chain_t              future;
    ngx_chain_t              later;
    ngx_buf_t                pending_buf;
    ngx_buf_t                retained_buf;
    ngx_buf_t                future_buf;
    ngx_buf_t                later_buf;
    ngx_int_t                rc;
    u_char                   future_data[] = "future-overflow";
    u_char                   retained_data[] = "old";
    u_char                   later_data[] = "later";
    u_char                  *pending_pos;
    u_char                  *pending_last;
    ngx_uint_t               free_calls;
    ngx_http_markdown_pending_terminal_t  terminal;

    TEST_SUBSECTION("P1-2: failopen_active enqueue failure aborts safely");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);

    ngx_memzero(&pending_output, sizeof(pending_output));
    ngx_memzero(&pending_buf, sizeof(pending_buf));
    ngx_memzero(&retained, sizeof(retained));
    ngx_memzero(&retained_buf, sizeof(retained_buf));
    ngx_memzero(&future, sizeof(future));
    ngx_memzero(&future_buf, sizeof(future_buf));
    ngx_memzero(&later, sizeof(later));
    ngx_memzero(&later_buf, sizeof(later_buf));
    ngx_memzero(&terminal, sizeof(terminal));

    /*
     * Simulate the state after fail-open was selected and the first
     * output chain was retained by downstream (NGX_AGAIN):
     *   - eligible=0, failopen_active=1
     *   - pending_output downstream-owned
     *   - pending_input already holding one entry (bytes=3, links=1)
     */
    ctx.eligible = 0;
    ctx.streaming.handle = NULL;
    ctx.streaming.completion.failopen_active = 1;
    ctx.streaming.pending_output = &pending_output;
    ctx.streaming.pending_meta.has_data = 1;
    pending_output.buf = &pending_buf;
    pending_buf.pos = (u_char *) "old";
    pending_buf.last = pending_buf.pos + 3;
    pending_buf.temporary = 1;
    pending_pos = pending_buf.pos;
    pending_last = pending_buf.last;
    retained.buf = &retained_buf;
    retained_buf.pos = retained_data;
    retained_buf.last = retained_data + sizeof(retained_data) - 1;
    ctx.streaming.pending_input.head = &retained;
    ctx.streaming.pending_input.tail = &retained;
    ctx.streaming.pending_input.bytes = 3;
    ctx.streaming.pending_input.links = 1;
    ctx.streaming.input_disposition = NGX_HTTP_MD_INPUT_RETAIN;

    /*
     * Future input that exceeds the retained-input budget: conf.max_size
     * is set so pending_input.bytes(3) + added_bytes > limit.
     */
    conf.max_size = 4;
    future.buf = &future_buf;
    future_buf.pos = future_data;
    future_buf.last = future_data + sizeof(future_data) - 1;
    future.next = NULL;

    g_streaming_feed_calls = 0;
    g_next_body_filter_calls = 0;
    g_next_body_filter_rc = NGX_OK;

    rc = ngx_http_markdown_streaming_handle_new_input_with_pending(
        &r, &ctx, &conf, &future);
    TEST_ASSERT(rc == NGX_AGAIN,
        "P1-2: failopen_active enqueue failure must return NGX_AGAIN");
    TEST_ASSERT(g_streaming_feed_calls == 0,
        "P1-2: must never re-enter the Rust converter");
    TEST_ASSERT(g_next_body_filter_calls == 0,
        "P1-2: must not submit a new chain while pending_output is "
        "downstream-owned (Rule 1 backpressure ownership contract)");
    TEST_ASSERT(ctx.streaming.completion.failopen_abort_after_pending == 1,
        "P1-2: enqueue failure in failopen_active must latch "
        "failopen_abort_after_pending for visible abort after drain");
    TEST_ASSERT(ctx.streaming.pending_output == &pending_output,
        "P1-2: old pending_output must remain intact (not freed/overwritten)");

    /*
     * Defense-in-depth: send_failopen_chain must refuse to submit when
     * pending_output is already set, BEFORE calling the downstream filter.
     */
    rc = ngx_http_markdown_streaming_send_failopen_chain(&r, &ctx, &future);
    TEST_ASSERT(rc == NGX_ERROR,
        "P1-2: send_failopen_chain must refuse to submit a new chain "
        "before old pending_output drains (defense-in-depth)");
    TEST_ASSERT(g_next_body_filter_calls == 0,
        "P1-2: send_failopen_chain guard must fire before downstream call");
    TEST_ASSERT(ctx.streaming.pending_output == &pending_output,
        "P1-2: send_failopen_chain guard must preserve the old anchor");
    TEST_ASSERT(pending_buf.pos == pending_pos
                && pending_buf.last == pending_last,
        "P1-2: send_failopen_chain guard must not mutate shared buffers");
    TEST_ASSERT(g_output_free_calls == 0,
        "P1-2: guard must not free downstream-owned pending buffers");

    free_calls = g_output_free_calls;
    rc = ngx_http_markdown_streaming_save_pending(
        &r, &ctx, &future, future_buf.pos,
        ngx_http_markdown_buf_len_safe(&future_buf), 0, terminal);
    TEST_ASSERT(rc == NGX_ERROR,
        "P1-2: save_pending must reject re-entry");
    TEST_ASSERT(ctx.streaming.pending_output == &pending_output,
        "P1-2: save_pending guard must preserve the old anchor");
    TEST_ASSERT(pending_buf.pos == pending_pos
                && pending_buf.last == pending_last,
        "P1-2: save_pending guard must not mutate shared buffers");
    TEST_ASSERT(g_output_free_calls == free_calls,
        "P1-2: save_pending guard must not free downstream-owned buffers");

    /*
     * A real NULL resume must drain the old pending output, consume the
     * abort latch without requiring upstream EOF, consume retained shared
     * buffers, and return a protocol-visible failure without a clean terminal.
     */
    pending_buf.temporary = 0;
    g_next_body_filter_rc = NGX_OK;
    g_next_body_filter_calls = 0;
    rc = ngx_http_markdown_streaming_handle_null_input(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "P1-2: known data loss must abort after draining old output");
    TEST_ASSERT(g_next_body_filter_calls == 1,
        "P1-2: abort must not send a clean terminal after data loss");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "P1-2: old pending anchor must clear only after downstream drain");
    TEST_ASSERT(ctx.streaming.completion.failopen_abort_after_pending == 0,
        "P1-2: abort latch must clear after failure continuation");
    TEST_ASSERT(retained_buf.pos == retained_buf.last,
        "P1-2: discarded retained input must consume shared buffers");
    TEST_ASSERT(ctx.streaming.input_disposition == NGX_HTTP_MD_INPUT_TERMINAL,
        "P1-2: abort continuation must reject all later input");

    later.buf = &later_buf;
    later_buf.pos = later_data;
    later_buf.last = later_data + sizeof(later_data) - 1;
    later.next = NULL;
    rc = ngx_http_markdown_streaming_body_filter(&r, &later);
    TEST_ASSERT(rc == NGX_OK,
        "P1-2: later input after abort must be safely abandoned");
    TEST_ASSERT(later_buf.pos == later_buf.last,
        "P1-2: later input after abort must be consumed without delivery");
    TEST_ASSERT(g_next_body_filter_calls == 1,
        "P1-2: later input after abort must not reach downstream");

    TEST_PASS("P1-2 failopen_active enqueue-failure safe abort covered");
}

/*
 * Test: init-failure fail-open with immediate downstream success must
 * submit the original input chain exactly once.
 *
 * Regression for a control-flow bug in ensure_handle(): on init failure,
 * ensure_handle called send_failopen_chain() directly.  When downstream
 * returned NGX_OK immediately, ensure_handle returned NGX_OK to the body
 * filter (masking the fact that the fail-open body was already
 * delivered).  body_filter then saw !eligible/handle==NULL and entered
 * the generic passthrough helper, which called ngx_http_next_body_filter
 * a second time with the SAME input chain — a duplicate submission of
 * the same html body downstream.
 *
 * This test drives the real top-level entry point
 * (ngx_http_markdown_streaming_body_filter) exactly like production:
 * prepare_options failure -> precommit_error (PASS policy) -> fail-open
 * chain delivery -> immediate NGX_OK from downstream.
 *
 * Covers: ngx_http_markdown_streaming_ensure_handle,
 *         ngx_http_markdown_streaming_body_filter,
 *         ngx_http_markdown_streaming_send_failopen_chain
 */
static void
test_init_failure_immediate_success_submits_input_once(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              in;
    ngx_buf_t                in_buf;
    ngx_int_t                rc;
    u_char                   in_data[] = "original-html-body";
    ngx_http_markdown_metrics_t metrics;

    TEST_SUBSECTION(
        "init-failure fail-open + immediate NGX_OK submits input once");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&in_buf, sizeof(in_buf));
    in.buf = &in_buf;
    in.next = NULL;
    in_buf.pos = in_data;
    in_buf.last = in_data + sizeof(in_data) - 1;
    in_buf.last_buf = 1;

    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    ctx.headers_forwarded = 0;
    g_prepare_options_rc = NGX_ERROR;
    g_forward_headers_rc = NGX_OK;
    g_next_body_filter_rc = NGX_OK;
    g_next_body_filter_calls = 0;
    g_streaming_feed_calls = 0;

    rc = ngx_http_markdown_streaming_body_filter(&r, &in);

    TEST_ASSERT(rc == NGX_OK,
        "init-failure + immediate downstream success must return NGX_OK");
    TEST_ASSERT(g_next_body_filter_calls == 1,
        "init-failure fail-open must submit the original input chain "
        "exactly once downstream, not once via ensure_handle's "
        "send_failopen_chain and again via generic passthrough");
    TEST_ASSERT(metrics.results.failopen_count == 1,
        "fail-open delivery counter must record exactly one delivery");
    TEST_ASSERT(ctx.streaming.completion.failopen_active == 1,
        "init-failure fail-open must latch failopen_active");
    TEST_ASSERT(ctx.failopen_completed == 1,
        "successful immediate fail-open delivery must set "
        "failopen_completed so re-entries do not resubmit");
    TEST_ASSERT(g_streaming_feed_calls == 0,
        "fail-open must never re-enter the Rust converter");
    TEST_ASSERT(metrics.conversions_attempted == 1
                && metrics.conversions_failed == 1,
        "init failure must record exactly one attempt/failure pair");

    TEST_PASS(
        "init-failure immediate-success single-submission covered");
}

/*
 * Test: init-failure fail-open with downstream NGX_AGAIN must retain a
 * pool-owned CLONE of the input chain links as pending_output, not the
 * original body-filter input chain links.
 *
 * The body-filter `in` chain-link nodes are transient: they belong to
 * the caller (e.g. NGINX core, or an upstream filter) and may be
 * reused/mutated once this filter invocation returns.  Only the
 * underlying ngx_buf_t is safe to share; the ngx_chain_t link nodes
 * themselves must be cloned into request-pool memory so pending_output
 * remains valid across body-filter invocations (resume_pending() and
 * cleanup() dereference pending_output long after `in` may have been
 * repurposed by the caller).
 *
 * Regression for a bug where ensure_handle()'s init-failure branch
 * called send_failopen_chain(r, ctx, in) directly with the raw,
 * transient `in` chain — bypassing the clone_chain_links() step that
 * the rest of the fail-open path (failopen_passthrough) already uses.
 *
 * Covers: ngx_http_markdown_streaming_ensure_handle,
 *         ngx_http_markdown_streaming_send_failopen_chain,
 *         ngx_http_markdown_streaming_clone_chain_links
 */
static void
test_init_failure_again_retains_cloned_chain_links(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              in;
    ngx_buf_t                in_buf;
    ngx_buf_t                different_buf;
    ngx_chain_t              different_chain;
    ngx_int_t                rc;
    u_char                   in_data[] = "original-html-body";
    u_char                   different_data[] = "reused-by-caller";
    ngx_chain_t              *cloned;

    TEST_SUBSECTION(
        "init-failure fail-open + NGX_AGAIN retains cloned chain links");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&in_buf, sizeof(in_buf));
    in.buf = &in_buf;
    in.next = NULL;
    in_buf.pos = in_data;
    in_buf.last = in_data + sizeof(in_data) - 1;

    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    ctx.headers_forwarded = 0;
    g_prepare_options_rc = NGX_ERROR;
    g_forward_headers_rc = NGX_OK;
    g_next_body_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_streaming_ensure_handle(&r, &ctx, &conf, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "init-failure + downstream NGX_AGAIN must return NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "downstream NGX_AGAIN must retain pending_output");

    cloned = ctx.streaming.pending_output;
    TEST_ASSERT(cloned != &in,
        "pending_output must be a pool-owned clone of the chain link, "
        "not the caller's transient input chain-link node");
    TEST_ASSERT(cloned->buf == &in_buf,
        "the cloned chain link must share the underlying ngx_buf_t "
        "(buffer data ownership stays with the caller/pool)");

    /*
     * Simulate the caller reusing/repurposing the original transient
     * chain-link node after this body-filter invocation returns, as
     * production callers are free to do once the filter call returns.
     */
    ngx_memzero(&different_buf, sizeof(different_buf));
    ngx_memzero(&different_chain, sizeof(different_chain));
    different_buf.pos = different_data;
    different_buf.last = different_data + sizeof(different_data) - 1;
    in.buf = &different_buf;
    in.next = &different_chain;

    /*
     * pending_output must be unaffected: it is an independent clone,
     * not a pointer into the caller's (now-mutated) chain-link node.
     */
    TEST_ASSERT(ctx.streaming.pending_output->buf == &in_buf,
        "pending_output must remain valid after the caller mutates "
        "the original transient chain-link node");
    TEST_ASSERT(ctx.streaming.pending_output->buf != &different_buf,
        "pending_output must not observe the caller's chain-link reuse");

    TEST_PASS(
        "init-failure NGX_AGAIN cloned-chain-link retention covered");
}

/*
 * Test: fail-open delivery integrity abort must not be double-counted
 * as a second conversion failure.
 *
 * Drives the full real production lifecycle from an authoritative
 * pre-commit conversion failure through to a fail-open delivery abort:
 *
 *   1. prepare_options failure -> precommit_error() with PASS policy.
 *      This is the ONE authoritative conversion-failure recording point:
 *      conversions_attempted, conversions_failed, streaming.failed_total,
 *      and the failures_resource_limit/failures_conversion breakdown are
 *      each incremented exactly once here.  eligible=0, failopen_active=1.
 *   2. ensure_handle() delivers the fail-open chain; downstream returns
 *      NGX_AGAIN, so pending_output is retained (delivery not yet
 *      confirmed -> failopen_count must not increment yet).
 *   3. Future input arrives while pending_output is still
 *      downstream-owned.  The retained-input budget (conf.max_size) is
 *      exhausted, so handle_new_input_with_pending's failopen_active
 *      branch cannot enqueue it and latches
 *      failopen_abort_after_pending instead of dropping bytes silently.
 *   4. A NULL resume drains the old pending_output (confirming fail-open
 *      delivery), then abort_failopen_after_pending() terminates the
 *      request because later bytes could not be preserved.
 *
 * This is a distinct, later-occurring event from the conversion failure
 * recorded in step 1 — it must NOT add a second conversions_failed /
 * streaming.failed_total / failures_resource_limit / failures_conversion
 * increment, which would break the attempted == succeeded + failed
 * invariant for this single request.
 *
 * Covers: ngx_http_markdown_streaming_precommit_error,
 *         ngx_http_markdown_streaming_ensure_handle,
 *         ngx_http_markdown_streaming_handle_new_input_with_pending,
 *         ngx_http_markdown_streaming_handle_null_input,
 *         ngx_http_markdown_streaming_abort_failopen_after_pending
 */
static void
test_failopen_delivery_abort_does_not_double_count_conversion_failure(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              in;
    ngx_chain_t              future;
    ngx_buf_t                in_buf;
    ngx_buf_t                future_buf;
    ngx_int_t                rc;
    u_char                   in_data[] = "initial-body";
    u_char                   future_data[] = "future-overflow-bytes";
    ngx_http_markdown_metrics_t metrics;
    ngx_uint_t                failure_breakdown_total;

    TEST_SUBSECTION(
        "fail-open delivery abort must not double-count conversion failure");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    ngx_memzero(&in, sizeof(in));
    ngx_memzero(&future, sizeof(future));
    ngx_memzero(&in_buf, sizeof(in_buf));
    ngx_memzero(&future_buf, sizeof(future_buf));
    in.buf = &in_buf;
    in_buf.pos = in_data;
    in_buf.last = in_data + sizeof(in_data) - 1;
    in.next = NULL;

    /* Step 1: real pre-commit conversion failure (prepare_options). */
    ctx.eligible = 1;
    ctx.streaming.handle = NULL;
    ctx.headers_forwarded = 0;
    g_prepare_options_rc = NGX_ERROR;
    g_forward_headers_rc = NGX_OK;
    g_next_body_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_streaming_ensure_handle(&r, &ctx, &conf, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "ensure_handle init-decline + downstream NGX_AGAIN returns NGX_AGAIN");
    TEST_ASSERT(ctx.eligible == 0,
        "precommit_error pass policy must clear eligible");
    TEST_ASSERT(ctx.streaming.completion.failopen_active == 1,
        "init failure must latch failopen_active");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "downstream NGX_AGAIN must retain pending_output "
        "(delivery not yet confirmed)");
    TEST_ASSERT(metrics.conversions_attempted == 1,
        "step 1 must record exactly one conversion attempt");
    TEST_ASSERT(metrics.conversions_failed == 1,
        "step 1 must record exactly one conversion failure "
        "(the single authoritative recording point)");
    TEST_ASSERT(metrics.streaming.failed_total == 1,
        "step 1 must record exactly one streaming failure");
    TEST_ASSERT(metrics.results.failopen_count == 0,
        "fail-open delivery must not count before pending output drains");

    failure_breakdown_total = (ngx_uint_t)
        (metrics.failures_resource_limit + metrics.failures_conversion);
    TEST_ASSERT(failure_breakdown_total == 1,
        "the failure-reason breakdown must record this conversion "
        "failure exactly once");

    /*
     * Step 2/3: future input arrives while pending_output is still
     * downstream-owned.  Exhaust the retained-input budget so the
     * enqueue fails and the failopen_active branch must latch a safe
     * abort instead of silently dropping bytes or re-entering
     * precommit_error (which would double-count).
     */
    conf.max_size = 1;
    future.buf = &future_buf;
    future_buf.pos = future_data;
    future_buf.last = future_data + sizeof(future_data) - 1;
    future.next = NULL;

    rc = ngx_http_markdown_streaming_handle_new_input_with_pending(
        &r, &ctx, &conf, &future);
    TEST_ASSERT(rc == NGX_AGAIN,
        "enqueue failure under failopen_active must return NGX_AGAIN "
        "(old pending_output still owns downstream)");
    TEST_ASSERT(ctx.streaming.completion.failopen_abort_after_pending == 1,
        "budget exhaustion must latch failopen_abort_after_pending, "
        "not re-enter precommit_error");
    TEST_ASSERT(metrics.conversions_failed == 1,
        "enqueue failure latch must NOT increment conversions_failed "
        "a second time");
    TEST_ASSERT(metrics.streaming.failed_total == 1,
        "enqueue failure latch must NOT increment streaming.failed_total "
        "a second time");

    /*
     * Step 4: NULL resume drains the old pending_output (confirming
     * fail-open delivery), then aborts because later bytes could not
     * be preserved.  This must return NGX_ERROR without adding a
     * second conversion-failure recording.
     */
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_handle_null_input(&r, &ctx, &conf);
    TEST_ASSERT(rc == NGX_ERROR,
        "known data loss must abort after draining old pending output");
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "pending_output must clear after the drain");
    TEST_ASSERT(ctx.streaming.completion.failopen_abort_after_pending == 0,
        "abort latch must clear after the abort completes");
    TEST_ASSERT(ctx.streaming.input_disposition == NGX_HTTP_MD_INPUT_TERMINAL,
        "abort must reject all further input for this request");

    TEST_ASSERT(metrics.conversions_attempted == 1,
        "the exact-once invariant: exactly one attempt for this request");
    TEST_ASSERT(metrics.conversions_failed == 1,
        "the exact-once invariant: fail-open delivery abort must NOT "
        "be recorded as a second conversion failure");
    TEST_ASSERT(metrics.streaming.failed_total == 1,
        "the exact-once invariant: streaming.failed_total must stay "
        "at exactly one for this request");
    TEST_ASSERT(
        (ngx_uint_t) (metrics.failures_resource_limit
                      + metrics.failures_conversion)
            == failure_breakdown_total,
        "the exact-once invariant: the failure-reason breakdown must "
        "not gain a second entry from the fail-open delivery abort");
    TEST_ASSERT(metrics.results.failopen_count == 0,
        "aborted fail-open delivery must not count as a completed "
        "fail-open delivery");

    TEST_PASS(
        "fail-open delivery abort exact-once metrics invariant covered");
}

/*
 * Regression: subrequest terminal (last_in_chain) delivered via a
 * backpressured fail-open multi-link chain must NOT be duplicated after
 * the pending chain drains.
 *
 * Production lifecycle under test:
 *
 *   1. streaming_body_filter(&r, &in)
 *        - subrequest (r != r->main)
 *        - in: data buffer with last_in_chain=1 and last_buf=0
 *        - process_chain detects last_in_chain on the input buffer and
 *          sets upstream_terminal_seen=1
 *        - process_chunk feeds the head buffer to Rust; Rust returns
 *          ERROR_BUDGET_EXCEEDED -> precommit_error(PASS) ->
 *          eligible=0, failopen_active=1
 *        - handle_chunk_result sees !eligible -> failopen_passthrough
 *          composes: replay-prefix (last_buf=0) + cloned input (tail
 *          carries last_in_chain=1) -> multi-link chain
 *        - send_failopen_chain submits the multi-link chain downstream
 *          -> NGX_AGAIN -> pending_output retained, pending_meta
 *          captures subrequest_terminal=1
 *
 *   2. streaming_body_filter(&r, NULL)
 *        - handle_null_input -> resume_pending
 *        - ngx_http_next_body_filter(r, NULL) -> NGX_OK
 *        - original terminal delivery confirmed downstream
 *        - pending_output cleared, pending_meta cleared,
 *          pending_failopen_delivery consumed (failopen_count=1,
 *          failopen_completed=1)
 *        - upstream_terminal_seen is still 1 (set by process_chain,
 *          not cleared by resume_pending)
 *
 *   3. (BUG, pre-fix) handle_null_input fail-open EOF branch:
 *        - failopen_active && pending_input empty && upstream_terminal_seen
 *        - checks main_terminal_sent (== 0 for subrequest)
 *        - sends a synthetic empty last_in_chain=1 downstream
 *          -> DUPLICATE SUBREQUEST TERMINAL (call 3)
 *
 * After the fix:
 *   - resume_pending latches subrequest_terminal_sent after confirmed
 *     delivery of a subrequest terminal
 *   - handle_null_input's fail-open EOF branch checks the request-type-
 *     aware terminal-delivered state (subrequest_terminal_sent for
 *     subrequests), so it returns without synthesizing a duplicate
 *   - exactly 2 downstream calls: call 1 (multi-link, NGX_AGAIN) and
 *     call 2 (NULL resume, NGX_OK); call 3 MUST NOT EXIST
 *
 * Covers:
 *   - ngx_http_markdown_streaming_body_filter (production entry point)
 *   - ngx_http_markdown_streaming_process_chain (sets upstream_terminal_seen)
 *   - ngx_http_markdown_streaming_process_chunk (Rust feed failure)
 *   - ngx_http_markdown_streaming_precommit_error (fail-open selection)
 *   - ngx_http_markdown_streaming_failopen_passthrough (replay prefix +
 *     clone)
 *   - ngx_http_markdown_streaming_send_failopen_chain (capture + NGX_AGAIN)
 *   - ngx_http_markdown_streaming_handle_null_input (resume + EOF branch)
 *   - ngx_http_markdown_streaming_resume_pending (terminal latch)
 *   - ngx_http_markdown_streaming_send_output (synthetic terminal)
 */
static void
test_subrequest_failopen_pending_terminal_resumes_once(void)
{
    ngx_http_request_t       r;
    ngx_http_request_t       main_r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              in;
    ngx_buf_t                in_buf;
    u_char                   in_data[] = "chunk-data";
    u_char                   prebuf_data[16];
    ngx_int_t                rc;
    ngx_http_markdown_metrics_t metrics;

    TEST_SUBSECTION(
        "subrequest fail-open pending terminal resumes once (no duplicate)");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    /*
     * Make this a subrequest: r->main points to a distinct request so
     * the terminal marker is last_in_chain, not last_buf.
     */
    ngx_memzero(&main_r, sizeof(main_r));
    r.main = &main_r;

    /*
     * Configuration: fail-open policy, no body-size limit (so
     * track_feed_budget does not reject the head chunk before Rust
     * sees it), parser budget unlimited.
     */
    conf.max_size = 0;
    conf.advanced.memory_budget = 0;
    conf.decompress.parser_budget = 0;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    /*
     * Streaming state: handle initialized, Pre-Commit phase, replay
     * buffer pre-populated so failopen_passthrough builds a multi-link
     * (replay-prefix + cloned-input) chain.
     */
    ctx.eligible = 1;
    ctx.headers_forwarded = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *)
        (uintptr_t) 0x71;
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx.streaming.prebuffer_initialized = 0;

    ctx.streaming.failopen_replay_initialized = 1;
    ctx.streaming.failopen_replay_buf.data = prebuf_data;
    ctx.streaming.failopen_replay_buf.size = 3;
    ctx.streaming.failopen_replay_buf.capacity = sizeof(prebuf_data);
    ctx.streaming.failopen_replay_buf.max_size = sizeof(prebuf_data);
    ctx.streaming.failopen_replay_buf.pool = &pool;
    ngx_memcpy(prebuf_data, "abc", 3);

    /*
     * Build a single-link input chain carrying both data and the
     * subrequest terminal marker.  process_chain detects last_in_chain
     * BEFORE calling process_chunk, so upstream_terminal_seen is set
     * authoritatively by the production terminal-detection helper.
     * process_chunk then feeds the data to Rust, which fails with
     * ERROR_BUDGET_EXCEEDED, triggering the real precommit_error(PASS)
     * path that selects fail-open.
     */
    ngx_memzero(&in_buf, sizeof(in_buf));
    in_buf.pos = in_data;
    in_buf.last = in_data + (sizeof(in_data) - 1);
    in_buf.last_buf = 0;
    in_buf.last_in_chain = 1;  /* subrequest terminal */

    in.buf = &in_buf;
    in.next = NULL;

    /*
     * Inject a real Pre-Commit failure: markdown_streaming_feed returns
     * ERROR_BUDGET_EXCEEDED, so process_chunk -> handle_feed_result ->
     * precommit_error(PASS) clears eligible and latches failopen_active.
     * Downstream returns NGX_AGAIN so the composed fail-open multi-link
     * chain is retained as pending_output.
     */
    g_streaming_feed_rc = ERROR_BUDGET_EXCEEDED;
    g_streaming_feed_out_data = NULL;
    g_streaming_feed_out_len = 0;
    g_next_body_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_streaming_body_filter(&r, &in);
    TEST_ASSERT(rc == NGX_AGAIN,
        "subrequest-resumes-once: body_filter must return NGX_AGAIN when "
        "downstream backpressures the fail-open chain");
    TEST_ASSERT(ctx.streaming.completion.failopen_active == 1,
        "subrequest-resumes-once: failopen_active must be latched by "
        "precommit_error");
    TEST_ASSERT(ctx.eligible == 0,
        "subrequest-resumes-once: eligible must be cleared by "
        "precommit_error PASS policy");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "subrequest-resumes-once: pending_output must be retained on "
        "NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.completion.upstream_terminal_seen == 1,
        "subrequest-resumes-once: upstream_terminal_seen must be set by "
        "process_chain (authoritative terminal detection from production "
        "helper)");
    TEST_ASSERT(ctx.streaming.pending_meta.subrequest_terminal == 1,
        "subrequest-resumes-once: pending_meta.subrequest_terminal must "
        "be captured before downstream ownership crossing");
    TEST_ASSERT(ctx.streaming.pending_meta.main_terminal == 0,
        "subrequest-resumes-once: pending_meta.main_terminal must be 0 "
        "(subrequest uses last_in_chain, not last_buf)");
    TEST_ASSERT(ctx.streaming.subrequest_terminal_sent == 0,
        "subrequest-resumes-once: NGX_AGAIN must not confirm terminal "
        "delivery");

    /*
     * Call 1 must be the multi-link fail-open chain (in != NULL) with
     * a terminal tail carrying last_in_chain=1 and last_buf=0.
     */
    TEST_ASSERT(g_body_filter_hist_len >= 1,
        "subrequest-resumes-once: at least one downstream call expected");
    TEST_ASSERT(g_body_filter_hist[0].is_null == 0,
        "subrequest-resumes-once: call 1 must have non-NULL input "
        "(composed fail-open chain)");
    TEST_ASSERT(g_body_filter_hist[0].any_last_buf == 0,
        "subrequest-resumes-once: call 1 must NOT carry last_buf "
        "(subrequest terminal is last_in_chain only)");
    TEST_ASSERT(g_body_filter_hist[0].any_last_in_chain == 1,
        "subrequest-resumes-once: call 1 chain must carry last_in_chain "
        "in the tail link (subrequest terminal)");

    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(metrics.results.failopen_count == 0,
            "subrequest-resumes-once: failopen_count must NOT increment "
            "on NGX_AGAIN (delivery not yet confirmed)");
    }

    /*
     * Step 2: real NULL re-entry through the production entry point.
     * Downstream returns NGX_OK, draining the retained multi-link chain
     * and confirming the original subrequest terminal delivery.
     */
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_body_filter(&r, NULL);
    TEST_ASSERT(rc == NGX_OK,
        "subrequest-resumes-once: NULL resume must return NGX_OK after "
        "downstream confirms delivery");

    /*
     * Call 2 must be the NULL resume (in == NULL) that drains the
     * downstream-retained chain.
     */
    TEST_ASSERT(g_body_filter_hist_len >= 2,
        "subrequest-resumes-once: two downstream calls expected");
    TEST_ASSERT(g_body_filter_hist[1].is_null == 1,
        "subrequest-resumes-once: call 2 must be NULL resume");

    /*
     * CRITICAL: exactly 2 downstream calls — the original terminal
     * delivery (call 1, NGX_AGAIN retained) + the NULL resume
     * confirming it (call 2, NGX_OK).  A third call with a synthetic
     * empty last_in_chain=1 would be a DUPLICATE SUBREQUEST TERMINAL.
     */
    TEST_ASSERT(g_body_filter_hist_len == 2,
        "subrequest-resumes-once: downstream call history must contain "
        "exactly 2 entries — a third would be a duplicate subrequest "
        "terminal");
    TEST_ASSERT(g_next_body_filter_calls == 2,
        "subrequest-resumes-once: exactly 2 downstream calls "
        "(no duplicate subrequest terminal after pending drain)");

    /*
     * pending_output and pending metadata must be cleared after the
     * successful drain.
     */
    TEST_ASSERT(ctx.streaming.pending_output == NULL,
        "subrequest-resumes-once: pending_output must be NULL after "
        "successful drain");
    TEST_ASSERT(ctx.streaming.pending_meta.subrequest_terminal == 0,
        "subrequest-resumes-once: pending_meta.subrequest_terminal "
        "must be cleared after drain");
    TEST_ASSERT(ctx.streaming.pending_meta.main_terminal == 0,
        "subrequest-resumes-once: pending_meta.main_terminal must be "
        "cleared after drain");
    TEST_ASSERT(ctx.streaming.completion.pending_failopen_delivery == 0,
        "subrequest-resumes-once: pending_failopen_delivery must be "
        "cleared after drain");

    /*
     * The subrequest terminal delivery is now confirmed downstream.
     * The request-type-aware terminal-delivered latch must reflect
     * this so the fail-open EOF branch does not synthesize a duplicate.
     */
    TEST_ASSERT(ctx.streaming.completion.upstream_terminal_seen == 0,
        "subrequest-resumes-once: upstream_terminal_seen must be "
        "cleared by the fail-open EOF branch after terminal delivery "
        "is confirmed");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 0,
        "subrequest-resumes-once: main_terminal_sent must remain 0 "
        "for a subrequest (last_in_chain is not main-request EOF)");
    TEST_ASSERT(ctx.streaming.subrequest_terminal_sent == 1,
        "subrequest-resumes-once: confirmed NULL resume must latch "
        "subrequest terminal delivery");

    if (ngx_http_markdown_metrics != NULL) {
        TEST_ASSERT(metrics.results.failopen_count == 1,
            "subrequest-resumes-once: failopen_count must be 1 after "
            "successful fail-open delivery drain");
    }

    TEST_PASS(
        "subrequest fail-open pending terminal resumes once "
        "(no duplicate subrequest terminal)");
}

/*
 * Rule 1 regression: an existing pending chain is already owned by the
 * downstream filter.  Postcommit must reject a second chain before calling
 * downstream and must preserve every field describing the old pending chain.
 */
static void
test_postcommit_existing_pending_rejects_second_submission(void)
{
    ngx_http_request_t       r;
    ngx_http_markdown_ctx_t  ctx;
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    ngx_connection_t         conn;
    ngx_log_t                log;
    ngx_event_t              read_event;
    ngx_chain_t              old_pending;
    ngx_buf_t                old_buf;
    ngx_uint_t               buffered_before;
    ngx_int_t                rc;

    TEST_SUBSECTION(
        "postcommit rejects a second chain while output is pending");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&old_pending, sizeof(old_pending));
    ngx_memzero(&old_buf, sizeof(old_buf));

    old_pending.buf = &old_buf;
    ctx.streaming.pending_output = &old_pending;
    ctx.streaming.pending_meta.has_data = 1;
    ctx.streaming.pending_meta.bytes = 37;
    ctx.streaming.pending_meta.zero_copy = 1;
    ctx.streaming.pending_meta.main_terminal = 0;
    ctx.streaming.pending_meta.subrequest_terminal = 1;
    r.buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
    buffered_before = r.buffered;

    rc = ngx_http_markdown_stream_postcommit_send_terminal(&r, &ctx);

    TEST_ASSERT(g_next_body_filter_calls == 0,
        "postcommit pending guard: existing pending output must prevent "
        "a second downstream submission");
    TEST_ASSERT(rc == NGX_ERROR,
        "postcommit pending guard: rejected submission must return error");
    TEST_ASSERT(ctx.streaming.pending_output == &old_pending,
        "postcommit pending guard: old pending anchor must remain unchanged");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 1
        && ctx.streaming.pending_meta.bytes == 37
        && ctx.streaming.pending_meta.zero_copy == 1
        && ctx.streaming.pending_meta.main_terminal == 0
        && ctx.streaming.pending_meta.subrequest_terminal == 1,
        "postcommit pending guard: old pending metadata must remain unchanged");
    TEST_ASSERT(r.buffered == buffered_before,
        "postcommit pending guard: buffered state must remain unchanged");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 0,
        "postcommit pending guard: rejected terminal must not latch delivery");

    TEST_PASS("postcommit rejects a second chain while output is pending");
}

/*
 * Regression: the postcommit sender is a pending-output producer and must
 * create exactly one backpressure event for each pending ownership cycle.
 * Persistent NULL retries do not create additional cycles; only a confirmed
 * drain increments the resume counter.
 */
static void
test_postcommit_pending_backpressure_metrics_are_symmetric(void)
{
    ngx_http_request_t          r;
    ngx_http_markdown_ctx_t     ctx;
    ngx_http_markdown_conf_t    conf;
    ngx_pool_t                  pool;
    ngx_connection_t            conn;
    ngx_log_t                   log;
    ngx_event_t                 read_event;
    ngx_http_markdown_metrics_t metrics;
    ngx_int_t                   rc;
    u_char                      closing[] = "\n```";

    TEST_SUBSECTION("postcommit pending backpressure metrics are symmetric");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.streaming.handle =
        (struct StreamingConverterHandle *) (uintptr_t) 0x91;
    g_streaming_safe_finish_data = closing;
    g_streaming_safe_finish_len = sizeof(closing) - 1;
    g_next_body_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(&r, &ctx);
    TEST_ASSERT(rc == NGX_AGAIN,
        "postcommit pending metrics: producer must return NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "postcommit pending metrics: producer must retain pending output");
    TEST_ASSERT(metrics.perf.backpressure_total == 1,
        "postcommit pending metrics: pending ownership must increment "
        "backpressure_total once");
    TEST_ASSERT(metrics.perf.backpressure_resume_total == 0,
        "postcommit pending metrics: resume must remain zero before drain");
    TEST_ASSERT(
        metrics.perf.pending_output_high_watermark_bytes
            >= (ngx_atomic_t) (sizeof(closing) - 1),
        "postcommit pending metrics: closing bytes must update watermark");

    rc = ngx_http_markdown_streaming_body_filter(&r, NULL);
    TEST_ASSERT(rc == NGX_AGAIN,
        "postcommit pending metrics: persistent NULL retry may stay pending");
    TEST_ASSERT(metrics.perf.backpressure_total == 1,
        "postcommit pending metrics: NULL retry must not create a new cycle");
    TEST_ASSERT(metrics.perf.backpressure_resume_total == 0,
        "postcommit pending metrics: persistent retry is not a resume");

    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_body_filter(&r, NULL);
    TEST_ASSERT(rc == NGX_OK,
        "postcommit pending metrics: successful NULL resume must return OK");
    TEST_ASSERT(metrics.perf.backpressure_total == 1,
        "postcommit pending metrics: first cycle must have one event");
    TEST_ASSERT(metrics.perf.backpressure_resume_total == 1,
        "postcommit pending metrics: first cycle must have one resume");

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.streaming.main_terminal_sent = 0;
    ctx.streaming.handle =
        (struct StreamingConverterHandle *) (uintptr_t) 0x92;
    g_next_body_filter_rc = NGX_AGAIN;
    rc = ngx_http_markdown_stream_postcommit_safe_finish(&r, &ctx);
    TEST_ASSERT(rc == NGX_AGAIN,
        "postcommit pending metrics: second cycle must retain output");
    TEST_ASSERT(metrics.perf.backpressure_total == 2,
        "postcommit pending metrics: second cycle must increment once");

    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_body_filter(&r, NULL);
    TEST_ASSERT(rc == NGX_OK,
        "postcommit pending metrics: second cycle must resume successfully");
    TEST_ASSERT(metrics.perf.backpressure_total == 2,
        "postcommit pending metrics: two cycles must have two events");
    TEST_ASSERT(metrics.perf.backpressure_resume_total == 2,
        "postcommit pending metrics: two cycles must have two resumes");

    TEST_PASS("postcommit pending backpressure metrics are symmetric");
}

/*
 * Regression: the same pool-copied safe-finish bytes must have identical
 * delivered-byte and copied-output classification whether downstream accepts
 * them immediately or after one pending-output resume.
 */
static void
test_postcommit_copied_output_accounting_matches_after_resume(void)
{
    ngx_http_request_t          r;
    ngx_http_markdown_ctx_t     ctx;
    ngx_http_markdown_conf_t    conf;
    ngx_pool_t                  pool;
    ngx_connection_t            conn;
    ngx_log_t                   log;
    ngx_event_t                 read_event;
    ngx_http_markdown_metrics_t metrics;
    ngx_atomic_t                immediate_bytes;
    ngx_atomic_t                immediate_copied;
    ngx_atomic_t                resumed_bytes;
    ngx_atomic_t                resumed_copied;
    ngx_int_t                   rc;
    u_char                      closing[] = "\n```";

    TEST_SUBSECTION("postcommit immediate and resumed copied output parity");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.streaming.handle =
        (struct StreamingConverterHandle *) (uintptr_t) 0x93;
    g_streaming_safe_finish_data = closing;
    g_streaming_safe_finish_len = sizeof(closing) - 1;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(&r, &ctx);
    TEST_ASSERT(rc == NGX_OK,
        "postcommit copied parity: immediate delivery must succeed");
    immediate_bytes = metrics.streaming.selection.output_bytes_total;
    immediate_copied = metrics.perf.copied_output_total;

    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.streaming.handle =
        (struct StreamingConverterHandle *) (uintptr_t) 0x94;
    g_streaming_safe_finish_data = closing;
    g_streaming_safe_finish_len = sizeof(closing) - 1;
    g_next_body_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(&r, &ctx);
    TEST_ASSERT(rc == NGX_AGAIN,
        "postcommit copied parity: deferred delivery must retain output");
    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_body_filter(&r, NULL);
    TEST_ASSERT(rc == NGX_OK,
        "postcommit copied parity: deferred delivery must resume");
    resumed_bytes = metrics.streaming.selection.output_bytes_total;
    resumed_copied = metrics.perf.copied_output_total;

    TEST_ASSERT(immediate_bytes == resumed_bytes,
        "postcommit copied parity: immediate/resumed byte deltas must match");
    TEST_ASSERT(immediate_copied == resumed_copied,
        "postcommit copied parity: immediate/resumed copied deltas must match");
    TEST_ASSERT(immediate_bytes == (ngx_atomic_t) (sizeof(closing) - 1),
        "postcommit copied parity: delivered bytes must equal closing length");
    TEST_ASSERT(immediate_copied == 1,
        "postcommit copied parity: one pool-copy chain must be counted");

    TEST_PASS("postcommit immediate and resumed copied output parity");
}

/*
 * Regression: terminal-only postcommit backpressure is a backpressure event,
 * but it carries no data and therefore must not update byte, watermark, or
 * copied-output metrics.  Its terminal latch is confirmed only after resume.
 */
static void
test_postcommit_terminal_only_backpressure_metrics(void)
{
    ngx_http_request_t          r;
    ngx_http_markdown_ctx_t     ctx;
    ngx_http_markdown_conf_t    conf;
    ngx_pool_t                  pool;
    ngx_connection_t            conn;
    ngx_log_t                   log;
    ngx_event_t                 read_event;
    ngx_http_markdown_metrics_t metrics;
    ngx_int_t                   rc;

    TEST_SUBSECTION("postcommit terminal-only backpressure metrics");
    reset_globals();
    init_request_ctx_conf(&r, &ctx, &conf, &pool, &conn, &log, &read_event);
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;
    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.streaming.handle =
        (struct StreamingConverterHandle *) (uintptr_t) 0x95;
    g_next_body_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(&r, &ctx);
    TEST_ASSERT(rc == NGX_AGAIN,
        "postcommit terminal metrics: terminal send must retain output");
    TEST_ASSERT(metrics.perf.backpressure_total == 1,
        "postcommit terminal metrics: terminal pending cycle must count");
    TEST_ASSERT(metrics.perf.pending_output_high_watermark_bytes == 0,
        "postcommit terminal metrics: zero bytes must not change watermark");
    TEST_ASSERT(metrics.streaming.selection.output_bytes_total == 0,
        "postcommit terminal metrics: terminal carries no output bytes");
    TEST_ASSERT(metrics.perf.copied_output_total == 0,
        "postcommit terminal metrics: terminal is not copied data output");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 0,
        "postcommit terminal metrics: NGX_AGAIN must not latch terminal");

    g_next_body_filter_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_body_filter(&r, NULL);
    TEST_ASSERT(rc == NGX_OK,
        "postcommit terminal metrics: terminal resume must succeed");
    TEST_ASSERT(metrics.perf.backpressure_resume_total == 1,
        "postcommit terminal metrics: confirmed drain must count resume");
    TEST_ASSERT(metrics.streaming.selection.output_bytes_total == 0,
        "postcommit terminal metrics: resume must not add output bytes");
    TEST_ASSERT(metrics.perf.copied_output_total == 0,
        "postcommit terminal metrics: resume must not add copied output");
    TEST_ASSERT(ctx.streaming.main_terminal_sent == 1,
        "postcommit terminal metrics: resume must latch main terminal");
    TEST_ASSERT(g_next_body_filter_calls == 2,
        "postcommit terminal metrics: send plus NULL resume only");

    rc = ngx_http_markdown_streaming_body_filter(&r, NULL);
    TEST_ASSERT(rc == NGX_OK,
        "postcommit terminal metrics: repeated NULL entry remains stable");
    TEST_ASSERT(g_next_body_filter_calls == 2,
        "postcommit terminal metrics: no duplicate terminal submission");

    TEST_PASS("postcommit terminal-only backpressure metrics");
}


/*
 * The streaming decompressor returns dedicated sentinels; the production
 * feed/finalize mappers must increment exactly one matching shared metric.
 * Post-commit finalization still reports ERROR_POST_COMMIT to the state
 * machine, without losing the decompression taxonomy in metrics.
 */
static void
test_streaming_decompression_error_metric_mapping(void)
{
    ngx_http_markdown_ctx_t     ctx;
    ngx_http_markdown_metrics_t metrics;
    uint32_t                    error_code;

    TEST_SUBSECTION("streaming decompression error metric mapping");
    reset_globals();
    ngx_memzero(&ctx, sizeof(ctx));
    ngx_memzero(&metrics, sizeof(metrics));
    ngx_http_markdown_metrics = &metrics;

    error_code = ngx_http_markdown_streaming_map_feed_decomp_error(
        NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR);
    TEST_ASSERT(error_code == ERROR_DECOMPRESSION_FORMAT_ERROR,
        "feed format sentinel should map to the format error code");
    TEST_ASSERT(metrics.decompressions.format_error_total == 1,
        "feed format sentinel should increment format exactly once");
    TEST_ASSERT(metrics.decompressions.truncated_input_total == 0
                && metrics.decompressions.io_error_total == 0,
        "feed format sentinel must not increment truncated or I/O metrics");

    ngx_memzero(&metrics, sizeof(metrics));
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    error_code = ngx_http_markdown_streaming_map_finalize_decomp_error(
        &ctx, NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT);
    TEST_ASSERT(error_code == ERROR_DECOMPRESSION_TRUNCATED_INPUT,
        "precommit truncated sentinel should retain its error code");
    TEST_ASSERT(metrics.decompressions.truncated_input_total == 1,
        "truncated sentinel should increment truncated exactly once");
    TEST_ASSERT(metrics.decompressions.format_error_total == 0
                && metrics.decompressions.io_error_total == 0,
        "truncated sentinel must not increment format or I/O metrics");

    ngx_memzero(&metrics, sizeof(metrics));
    error_code = ngx_http_markdown_streaming_map_feed_decomp_error(
        NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR);
    TEST_ASSERT(error_code == ERROR_DECOMPRESSION_IO_ERROR,
        "feed I/O sentinel should map to the I/O error code");
    TEST_ASSERT(metrics.decompressions.io_error_total == 1,
        "I/O sentinel should increment I/O exactly once");
    TEST_ASSERT(metrics.decompressions.format_error_total == 0
                && metrics.decompressions.truncated_input_total == 0,
        "I/O sentinel must not increment format or truncated metrics");

    ngx_memzero(&metrics, sizeof(metrics));
    ctx.streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    error_code = ngx_http_markdown_streaming_map_finalize_decomp_error(
        &ctx, NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT);
    TEST_ASSERT(error_code == ERROR_POST_COMMIT,
        "postcommit truncation should remain terminal postcommit failure");
    TEST_ASSERT(metrics.decompressions.truncated_input_total == 1,
        "postcommit truncation should preserve the metric classification");
    TEST_ASSERT(metrics.decompressions.format_error_total == 0
                && metrics.decompressions.io_error_total == 0,
        "postcommit truncation must not increment other error metrics");

    ngx_http_markdown_metrics = NULL;
    TEST_PASS("streaming decompression metric taxonomy remains exact");
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
    test_cleanup_does_not_free_shared_temporary_buffer();
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
    test_failopen_passthrough_again_pending();
    test_failopen_multilink_pending_terminal_tail();
    test_subrequest_pending_terminal_last_in_chain();
    test_pending_input_production_lifecycle();
    test_failopen_init_failure_latches_mode();
    test_failopen_active_enqueue_failure_aborts_safely();
    test_init_failure_immediate_success_submits_input_once();
    test_init_failure_again_retains_cloned_chain_links();
    test_failopen_delivery_abort_does_not_double_count_conversion_failure();
    test_subrequest_failopen_pending_terminal_resumes_once();
    test_postcommit_existing_pending_rejects_second_submission();
    test_postcommit_pending_backpressure_metrics_are_symmetric();
    test_postcommit_copied_output_accounting_matches_after_resume();
    test_postcommit_terminal_only_backpressure_metrics();
    test_streaming_decompression_error_metric_mapping();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}

#endif  /* MARKDOWN_STREAMING_ENABLED */
