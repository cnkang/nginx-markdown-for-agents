/*
 * Test: metrics_bounded_rendering
 *
 * Validates bounded per-path rendering for JSON and plain-text
 * metrics formats.  Follows the same pattern as
 * prometheus_per_path_test.c: defines minimal NGINX types and
 * includes only the metrics_impl.h header, which contains the
 * bounded walk functions and rendering helpers.
 */

#if defined(__APPLE__)
#define _DARWIN_C_SOURCE
#endif
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L

#include "../include/test_common.h"
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <netinet/in.h>
#include <arpa/inet.h>

typedef unsigned char u_char;

typedef struct {
    size_t     len;
    u_char    *data;
} ngx_str_t;

typedef intptr_t  ngx_int_t;
typedef uintptr_t ngx_uint_t;
typedef long      ngx_atomic_t;
typedef unsigned long ngx_atomic_uint_t;
typedef int ngx_flag_t;

#define NGX_OK        0
#define NGX_ERROR    -1
#define NGX_DECLINED -5
#define NGX_HTTP_OK              200
#define NGX_HTTP_NOT_ALLOWED     405
#define NGX_HTTP_FORBIDDEN       403
#define NGX_HTTP_INTERNAL_SERVER_ERROR  500
#define NGX_LOG_WARN             4
#define NGX_LOG_ERR              3
#define NGX_LOG_CRIT             2
#define NGX_HTTP_GET             0x0002
#define NGX_HTTP_HEAD            0x0004

#define ngx_string(str) { sizeof(str) - 1, (u_char *) str }

#ifndef ngx_str_set
#define ngx_str_set(str, text)                                                    \
    do {                                                                          \
        (str)->len = sizeof(text) - 1;                                            \
        (str)->data = (u_char *) text;                                            \
    } while (0)
#endif


typedef struct ngx_rbtree_node_s  ngx_rbtree_node_t;

struct ngx_rbtree_node_s {
    ngx_rbtree_node_t  *left;
    ngx_rbtree_node_t  *right;
    ngx_rbtree_node_t  *parent;
    u_char              color;
    ngx_uint_t          key;
};

typedef struct {
    ngx_rbtree_node_t  *root;
    ngx_rbtree_node_t   sentinel;
} ngx_rbtree_t;

typedef struct {
    ngx_rbtree_node_t  rbnode;
    ngx_uint_t         path_len;
    u_char            *path;
    ngx_atomic_t       conversions;
    ngx_atomic_t       conversion_time_sum_ms;
    ngx_atomic_t       entries;
} ngx_http_markdown_path_metric_node_t;

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
typedef struct ngx_shm_zone_s ngx_shm_zone_t;

typedef struct {
    ngx_rbtree_t       path_tree;
    ngx_rbtree_node_t  sentinel;
    ngx_atomic_t       path_entries;
    ngx_atomic_t       path_conversions;
    ngx_atomic_t       path_conversion_time_sum_ms;
    ngx_uint_t         cardinality_limit;
    ngx_atomic_t       overflow_count;
    ngx_atomic_t       unretained_conversions;
    ngx_atomic_t       unretained_conversion_time_sum_ms;
} ngx_http_markdown_metrics_per_path_t;

typedef struct {
    ngx_http_markdown_metrics_per_path_t per_path;
    ngx_atomic_t  conversions_attempted;
    ngx_atomic_t  conversions_succeeded;
    ngx_atomic_t  conversions_failed;
    ngx_atomic_t  conversions_bypassed;
    ngx_atomic_t  failures_conversion;
    ngx_atomic_t  failures_resource_limit;
    ngx_atomic_t  failures_system;
    ngx_atomic_t  conversion_time_sum_ms;
    ngx_atomic_t  input_bytes;
    ngx_atomic_t  output_bytes;
    struct {
        ngx_atomic_t  le_10ms;
        ngx_atomic_t  le_100ms;
        ngx_atomic_t  le_1000ms;
        ngx_atomic_t  gt_1000ms;
    } conversion_latency;
    struct {
        struct {
            ngx_atomic_t le_1ms;
            ngx_atomic_t le_5ms;
            ngx_atomic_t le_10ms;
            ngx_atomic_t le_25ms;
            ngx_atomic_t le_50ms;
            ngx_atomic_t le_100ms;
            ngx_atomic_t le_250ms;
            ngx_atomic_t le_500ms;
            ngx_atomic_t le_1000ms;
            ngx_atomic_t le_5000ms;
            ngx_atomic_t sum_ms;
            ngx_atomic_t count;
        } full_buffer;
        struct {
            ngx_atomic_t le_1ms;
            ngx_atomic_t le_5ms;
            ngx_atomic_t le_10ms;
            ngx_atomic_t le_25ms;
            ngx_atomic_t le_50ms;
            ngx_atomic_t le_100ms;
            ngx_atomic_t le_250ms;
            ngx_atomic_t le_500ms;
            ngx_atomic_t le_1000ms;
            ngx_atomic_t le_5000ms;
            ngx_atomic_t sum_ms;
            ngx_atomic_t count;
        } streaming;
    } conversion_latency_v1;
    struct {
        ngx_atomic_t  attempted;
        ngx_atomic_t  succeeded;
        ngx_atomic_t  failed;
        ngx_atomic_t  gzip;
        ngx_atomic_t  deflate;
        ngx_atomic_t  brotli;
        ngx_atomic_t  budget_exceeded_total;
        ngx_atomic_t  format_error_total;
        ngx_atomic_t  truncated_input_total;
        ngx_atomic_t  io_error_total;
        struct {
            ngx_atomic_t budget;
            ngx_atomic_t format;
            ngx_atomic_t truncated;
            ngx_atomic_t io;
        } gzip_failures;
        struct {
            ngx_atomic_t budget;
            ngx_atomic_t format;
            ngx_atomic_t truncated;
            ngx_atomic_t io;
        } deflate_failures;
        struct {
            ngx_atomic_t budget;
            ngx_atomic_t format;
            ngx_atomic_t truncated;
            ngx_atomic_t io;
        } brotli_failures;
    } decompressions;
    struct {
        ngx_atomic_t  fullbuffer;
        ngx_atomic_t  incremental;
        ngx_atomic_t  streaming;
    } path_hits;
    ngx_atomic_t  requests_entered;
    struct {
        ngx_atomic_t  config;
        ngx_atomic_t  method;
        ngx_atomic_t  status;
        ngx_atomic_t  content_type;
        ngx_atomic_t  size;
        ngx_atomic_t  streaming;
        ngx_atomic_t  auth;
        ngx_atomic_t  range;
        ngx_atomic_t  accept;
        ngx_atomic_t  no_accept;
        ngx_atomic_t  conditional;
        ngx_atomic_t  compression_passthrough;
        ngx_atomic_t  no_transform;
    } skips;
    struct {
        ngx_atomic_t  failopen_count;
        ngx_atomic_t  delivery_count;
        ngx_atomic_t  full_buffer_delivery_count;
        ngx_atomic_t  decision_count;
        ngx_atomic_t  estimated_token_savings;
        ngx_atomic_t  replay_buffer_errors_total;
        struct {
            ngx_atomic_t success;
            ngx_atomic_t failure_schema_version;
            ngx_atomic_t failure_unknown_key;
            ngx_atomic_t failure_duplicate_key;
            ngx_atomic_t failure_invalid_type;
            ngx_atomic_t failure_out_of_range;
            ngx_atomic_t failure_size_exceeded;
            ngx_atomic_t failure_parse_error;
            ngx_atomic_t failure_file_error;
        } dynconf_reloads;
        struct {
            ngx_atomic_t  parse_timeouts_total;
            ngx_atomic_t  parse_budget_exceeded_total;
        } parse_interrupts;
    } results;
    struct {
        ngx_atomic_t  requests_total;
        ngx_atomic_t  fallback_total;
        ngx_atomic_t  succeeded_total;
        ngx_atomic_t  commit_total;
        ngx_atomic_t  failed_total;
        ngx_atomic_t  postcommit_error_total;
        ngx_atomic_t  precommit_failopen_total;
        ngx_atomic_t  precommit_reject_total;
        ngx_atomic_t  budget_exceeded_total;
        ngx_atomic_t  shadow_total;
        ngx_atomic_t  shadow_diff_total;
        ngx_atomic_t  last_ttfb_ms;
        ngx_atomic_t  last_peak_memory_bytes;
        ngx_atomic_t  streaming_fallback_precommit_pass;
        ngx_atomic_t  streaming_fallback_precommit_reject;
        ngx_atomic_t  streaming_failure_postcommit_abort;
        ngx_atomic_t  streaming_failure_postcommit_safe_finish;
        ngx_atomic_t  terminal_aborted_total;
        struct {
            ngx_atomic_t  streaming;
            ngx_atomic_t  full_buffer;
            ngx_atomic_t  passthrough;
            ngx_atomic_t  not_eligible;
        } engine_choice;
        struct {
            ngx_atomic_t  candidate_total;
            ngx_atomic_t  true_streaming_selected_total;
            ngx_atomic_t  output_bytes_total;
            ngx_atomic_t  excluded_content_type_total;
        } selection;
    } streaming;
    struct {
        ngx_atomic_t  backpressure_total;
        ngx_atomic_t  backpressure_resume_total;
        ngx_atomic_t  pending_output_high_watermark_bytes;
        ngx_atomic_t  decompression_streaming_total;
        ngx_atomic_t  decompression_fullbuffer_total;
        ngx_atomic_t  decompression_budget_exceeded_total;
        ngx_atomic_t  copied_output_total;
    } perf;
} ngx_http_markdown_metrics_t;

static ngx_http_markdown_metrics_t  g_metrics;
ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = &g_metrics;

static u_char *
ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...)
{
    va_list      args;
    int          n;
    size_t       remaining;
    const char  *rewritten;
    char         local_fmt[4096];
    size_t   fi;
    size_t   oi;

    if (buf >= last) {
        return buf;
    }

    remaining = (size_t)(last - buf);

    fi = 0;
    oi = 0;
    while (fmt[fi] != '\0' && oi < sizeof(local_fmt) - 4) {
        if (fmt[fi] == '%') {
            local_fmt[oi++] = fmt[fi++];
            while (fmt[fi] >= '0' && fmt[fi] <= '9') {
                local_fmt[oi++] = fmt[fi++];
            }
            if (fmt[fi] == 'u' && fmt[fi + 1] == 'A') {
                local_fmt[oi++] = 'l';
                local_fmt[oi++] = 'u';
                fi += 2;
            } else {
                local_fmt[oi++] = fmt[fi++];
            }
        } else {
            local_fmt[oi++] = fmt[fi++];
        }
    }
    local_fmt[oi] = '\0';
    rewritten = local_fmt;

    va_start(args, fmt);
    n = vsnprintf((char *) buf, remaining, rewritten, args);
    va_end(args);

    if (n < 0) {
        return buf;
    }

    if ((size_t) n >= remaining) {
        return last;
    }

    return buf + n;
}

static u_char *
ngx_snprintf(u_char *buf, size_t size, const char *fmt, ...)
{
    va_list  args;
    int      n;
    char     tmp[64];

    va_start(args, fmt);
    n = vsnprintf(tmp, sizeof(tmp), fmt, args);
    va_end(args);

    if (n < 0) {
        return buf;
    }

    if ((size_t) n > size) {
        n = (int) size;
    }

    memcpy(buf, tmp, (size_t) n);

    return buf + n;
}

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

static ngx_shm_zone_t  g_shm_zone;

ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = &g_shm_zone;

#define MARKDOWN_STREAMING_ENABLED 1
#define MARKDOWN_METRICS_PER_PATH_DEBUG 1
#define NGX_HTTP_MARKDOWN_PER_PATH_WALK_ENABLED 1

/*
 * Include the bounded rendering portion of metrics_impl.h.
 * We skip the HTTP handler and response-sending functions by
 * defining stubs for the NGINX request/buffer types they need.
 * The bounded walk functions and rendering helpers only need
 * the types defined above.
 */

typedef struct {
    u_char    *pos;
    u_char    *last;
    u_char    *start;
    u_char    *end;
    unsigned   last_buf;
    unsigned   last_in_chain;
} ngx_buf_t;

typedef struct ngx_chain_s {
    ngx_buf_t         *buf;
    struct ngx_chain_s *next;
} ngx_chain_t;

typedef struct {
    ngx_str_t  value;
} ngx_table_elt_t;

typedef struct {
    struct sockaddr  *sockaddr;
    void             *log;
} ngx_connection_stub_t;

typedef struct ngx_http_request_s {
    ngx_connection_stub_t *connection;
    struct {
        ngx_table_elt_t  *accept;
    } headers_in;
    struct {
        unsigned            status;
        size_t              content_length_n;
        size_t              content_type_len;
        ngx_str_t           content_type;
    } headers_out;
    unsigned            method;
    unsigned            header_only;
    void               *pool;
    struct ngx_http_request_s *main;
} ngx_http_request_t;

static ngx_inline void
ngx_memzero(void *buf, size_t n)
{
    memset(buf, 0, n);
}

static ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    return strncasecmp((const char *) s1, (const char *) s2, n);
}

static ngx_uint_t  g_output_filter_calls;
static ngx_uint_t  g_send_header_calls;

static ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *out)
{
    g_output_filter_calls++;
    UNUSED(r);
    UNUSED(out);
    return NGX_OK;
}

static ngx_inline void
ngx_log_error(int level, void *log, int err, const char *fmt, ...)
{
    UNUSED(level);
    UNUSED(log);
    UNUSED(err);
    UNUSED(fmt);
}

static ngx_inline ngx_atomic_uint_t
ngx_http_markdown_inflight_current(void)
{
    return 0;
}

static ngx_inline ngx_atomic_uint_t
ngx_http_markdown_inflight_high_watermark(void)
{
    return 0;
}

static ngx_inline ngx_atomic_uint_t
ngx_http_markdown_inflight_overload_total(void)
{
    return 0;
}

static ngx_table_elt_t *
ngx_http_markdown_find_request_header(ngx_http_request_t *r, ngx_str_t *name)
{
    UNUSED(r);
    UNUSED(name);
    return NULL;
}

typedef struct {
    int  metrics_format;
    struct {
        int  metrics_format;
    } ops;
} ngx_http_markdown_conf_t;

typedef struct ngx_module_s  ngx_module_t;
struct ngx_module_s { int dummy; };

static ngx_http_markdown_conf_t  g_conf;

static ngx_http_markdown_conf_t *
ngx_http_get_module_loc_conf(ngx_http_request_t *r, ngx_module_t m)
{
    UNUSED(r);
    UNUSED(m);
    return &g_conf;
}

static ngx_module_t  ngx_http_markdown_filter_module = {0};

static ngx_int_t
ngx_http_send_header(ngx_http_request_t *r)
{
    g_send_header_calls++;
    UNUSED(r);
    return NGX_OK;
}

static ngx_int_t
ngx_http_discard_request_body(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

static ngx_buf_t *
ngx_create_temp_buf(void *pool, size_t size)
{
    ngx_buf_t *b;
    UNUSED(pool);
    b = (ngx_buf_t *) calloc(1, sizeof(ngx_buf_t));
    if (b == NULL) return NULL;
    b->start = (u_char *) calloc(1, size);
    if (b->start == NULL) { free(b); return NULL; }
    b->pos = b->start;
    b->last = b->start;
    b->end = b->start + size;
    return b;
}

#define NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS  1
#define NGX_HTTP_HEADERS 1
#define NGINX_VERSION "1.26.3"
#define NGX_HTTP_MARKDOWN_PRODUCT_VERSION "0.9.2"

/* Keep the overflow injection local to this translation unit. */
#define NGX_MAX_SIZE_T_VALUE ((size_t) 4096)

#include "../../src/ngx_http_markdown_metrics_v1_renderer.h"
#include "../../src/ngx_http_markdown_metrics_impl.h"
#include "../../src/ngx_http_markdown_prometheus_impl.h"

static int
contains(const char *haystack, const char *needle)
{
    return strstr(haystack, needle) != NULL;
}

static void
init_tree(ngx_http_markdown_metrics_t *live)
{
    memset(&live->per_path.sentinel, 0, sizeof(live->per_path.sentinel));
    live->per_path.sentinel.left = &live->per_path.sentinel;
    live->per_path.sentinel.right = &live->per_path.sentinel;
    live->per_path.path_tree.root = &live->per_path.sentinel;
}

static void
init_snapshot(ngx_http_markdown_metrics_snapshot_t *s)
{
    ngx_memzero(s, sizeof(*s));
}

static void
setup_shm(ngx_http_markdown_metrics_t *live, ngx_slab_pool_t *shpool)
{
    g_shm_zone.data = live;
    g_shm_zone.shm.addr = shpool;
    ngx_http_markdown_metrics_shm_zone = &g_shm_zone;
}

static void
add_node(ngx_http_markdown_path_metric_node_t *node,
         u_char *path, size_t path_len,
         ngx_atomic_uint_t conversions,
         ngx_atomic_uint_t time_ms,
         ngx_rbtree_node_t *sentinel)
{
    memset(node, 0, sizeof(*node));
    node->path = path;
    node->path_len = path_len;
    node->conversions = conversions;
    node->conversion_time_sum_ms = time_ms;
    node->entries = 1;
    node->rbnode.left = sentinel;
    node->rbnode.right = sentinel;
}

static void
test_malformed_path_len_sets_failed_and_stops_right_walk(void)
{
    u_char                                      buf[4096];
    u_char                                     *p;
    size_t                                       converted_len;
    u_char                                      bad_path[] = "/bad";
    u_char                                      right_path[] = "/right";
    ngx_http_markdown_path_detail_render_ctx_t  render;
    ngx_http_markdown_path_metric_node_t        root;
    ngx_http_markdown_path_metric_node_t        bad;
    ngx_http_markdown_path_metric_node_t        right;
    ngx_rbtree_node_t                           sentinel;

    TEST_SUBSECTION("malformed path length fails and stops right walk");

    memset(&sentinel, 0, sizeof(sentinel));
    add_node(&root, NULL, 0, 1, 1, &sentinel);
    add_node(&bad, bad_path, NGX_MAX_SIZE_T_VALUE + 1, 2, 2, &sentinel);
    add_node(&right, right_path, sizeof(right_path) - 1, 3, 3, &sentinel);
    root.rbnode.left = &bad.rbnode;
    root.rbnode.right = &right.rbnode;

    memset(&render, 0, sizeof(render));
    render.pos = buf;
    render.end = buf + sizeof(buf);
    converted_len = 0;

    TEST_ASSERT(ngx_http_markdown_path_len_to_size(&bad, &converted_len)
                == NGX_ERROR,
                "path length conversion must reject the injected boundary");
    TEST_ASSERT(converted_len == 0,
                "failed conversion must not write a converted length");

    render.omitted_nodes = 0;
    p = ngx_http_markdown_json_walk_path_tree_bounded(
        &root.rbnode, &sentinel, &render);

    TEST_ASSERT(p == buf, "failed walk must not advance the output pointer");
    TEST_ASSERT(render.failed != 0,
                "malformed path length must set render.failed");
    TEST_ASSERT(render.entries_written == 0,
                "failed left subtree must stop before writing the root");
    TEST_ASSERT(render.omitted_nodes == 0,
                "failed left subtree must not aggregate the right subtree");

    TEST_PASS("malformed path failure stops right subtree traversal");
}

static void
test_malformed_path_detail_writers_return_null(void)
{
    u_char                                      json_buf[4096];
    u_char                                      text_buf[4096];
    u_char                                      path[] = "/malformed";
    ngx_http_markdown_metrics_snapshot_t        snapshot;
    ngx_http_markdown_metrics_t                 live;
    ngx_slab_pool_t                              shpool;
    ngx_http_markdown_path_metric_node_t         node;

    TEST_SUBSECTION("malformed path detail writers fail closed");

    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);
    add_node(&node, path, NGX_MAX_SIZE_T_VALUE + 1, 1, 1,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;

    init_snapshot(&snapshot);
    snapshot.per_path.path_entries = 1;

    TEST_ASSERT(ngx_http_markdown_json_write_path_details(
                    json_buf, json_buf + sizeof(json_buf), &snapshot) == NULL,
                "JSON path detail writer must return NULL on malformed path");
    TEST_ASSERT(ngx_http_markdown_text_write_path_details(
                    text_buf, text_buf + sizeof(text_buf), &snapshot) == NULL,
                "text path detail writer must return NULL on malformed path");

    TEST_PASS("JSON and text path detail writers propagate failure");
}

static void
test_malformed_path_outer_renderers_return_null(void)
{
    u_char                                      json_buf[4096];
    u_char                                      text_buf[4096];
    u_char                                      path[] = "/malformed";
    ngx_buf_t                                   json_output;
    ngx_buf_t                                   text_output;
    ngx_http_request_t                          request;
    ngx_connection_stub_t                      connection;
    ngx_http_markdown_metrics_snapshot_t        snapshot;
    ngx_http_markdown_metrics_derived_t         derived;
    ngx_http_markdown_metrics_t                 live;
    ngx_slab_pool_t                              shpool;
    ngx_http_markdown_path_metric_node_t         node;

    TEST_SUBSECTION("malformed path outer renderers fail closed");

    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);
    add_node(&node, path, NGX_MAX_SIZE_T_VALUE + 1, 1, 1,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;

    init_snapshot(&snapshot);
    snapshot.per_path.path_entries = 1;
    memset(&derived, 0, sizeof(derived));
    memset(&request, 0, sizeof(request));
    memset(&connection, 0, sizeof(connection));
    request.connection = &connection;
    memset(&json_output, 0, sizeof(json_output));
    json_output.pos = json_buf;
    json_output.end = json_buf + sizeof(json_buf);
    memset(&text_output, 0, sizeof(text_output));
    text_output.pos = text_buf;
    text_output.end = text_buf + sizeof(text_buf);

    TEST_ASSERT(ngx_http_markdown_metrics_render_response_body(
                    &request, &json_output,
                    NGX_HTTP_MARKDOWN_METRICS_OUTPUT_JSON,
                    &snapshot, &derived) == NULL,
                "JSON outer renderer must propagate path failure");
    TEST_ASSERT(ngx_http_markdown_metrics_render_response_body(
                    &request, &text_output,
                    NGX_HTTP_MARKDOWN_METRICS_OUTPUT_TEXT,
                    &snapshot, &derived) == NULL,
                "text outer renderer must propagate path failure");

    TEST_PASS("JSON and text outer renderers propagate failure");
}

static void
test_metrics_handler_uses_frozen_v1_surface(void)
{
    u_char                                      path[] = "/malformed";
    struct sockaddr_in                          address;
    ngx_connection_stub_t                      connection;
    ngx_http_request_t                          request;
    ngx_http_markdown_metrics_t                 live;
    ngx_http_markdown_path_metric_node_t        node;
    ngx_slab_pool_t                              shpool;
    ngx_int_t                                    rc;

    TEST_SUBSECTION("metrics handler uses frozen v1 surface");

    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);
    add_node(&node, path, NGX_MAX_SIZE_T_VALUE + 1, 1, 1,
             &live.per_path.sentinel);
    live.per_path.path_entries = 1;
    live.per_path.path_tree.root = &node.rbnode;
    ngx_http_markdown_metrics = &live;

    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    memset(&connection, 0, sizeof(connection));
    connection.sockaddr = (struct sockaddr *) &address;
    memset(&request, 0, sizeof(request));
    request.connection = &connection;
    request.method = NGX_HTTP_GET;
    request.main = &request;
    g_send_header_calls = 0;
    g_output_filter_calls = 0;

    rc = ngx_http_markdown_metrics_handler(&request);

    TEST_ASSERT(rc == NGX_OK,
                "metrics handler should render the frozen v1 response");
    TEST_ASSERT(request.headers_out.status == NGX_HTTP_OK,
                "metrics handler should commit HTTP 200 headers");
    TEST_ASSERT(request.headers_out.content_type.len
                == strlen("text/plain; version=0.0.4; charset=utf-8"),
                "metrics handler should use Prometheus content type");
    TEST_ASSERT(g_send_header_calls == 1,
                "metrics handler should send response headers once");
    TEST_ASSERT(g_output_filter_calls == 1,
                "metrics handler should send one complete response body");

    TEST_PASS("metrics handler uses frozen Prometheus v1 surface");
}

static void
test_v1_output_bytes_include_streaming_delivery(void)
{
    ngx_http_markdown_metrics_snapshot_t  snapshot;
    ngx_http_markdown_metrics_v1_snapshot_t  v1;

    TEST_SUBSECTION("v1 output bytes include streaming delivery");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.output_bytes = 100;
    snapshot.streaming.selection.output_bytes_total = 250;

    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);

    TEST_ASSERT(v1.output_bytes == 350,
                "v1 output bytes must include full-buffer and streaming bytes");
    TEST_PASS("v1 output bytes include streaming delivery");
}

static void
test_v1_engine_delivery_and_event_sources(void)
{
    ngx_http_markdown_metrics_snapshot_t  snapshot;
    ngx_http_markdown_metrics_v1_snapshot_t  v1;

    TEST_SUBSECTION("v1 engine delivery and event sources");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.results.delivery_count = 99;
    snapshot.results.full_buffer_delivery_count = 4;
    snapshot.streaming.succeeded_total = 7;
    snapshot.streaming.commit_total = 2;
    snapshot.perf.backpressure_resume_total = 3;

    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);

    TEST_ASSERT(v1.deliveries.full_buffer == 4,
                "full-buffer deliveries must use their direct counter");
    TEST_ASSERT(v1.deliveries.streaming == 7,
                "streaming deliveries must use streaming success counter");
    TEST_ASSERT(v1.streaming_events.commit == 2,
                "commit events must use successful header commits");
    TEST_ASSERT(v1.streaming_events.resume_success == 3,
                "resume successes must use downstream resume counter");
    TEST_PASS("v1 engine delivery and event sources");
}


static void
test_v1_outcome_formula_clamps_underflow(void)
{
    ngx_http_markdown_metrics_snapshot_t  snapshot;
    ngx_http_markdown_metrics_v1_snapshot_t  v1;

    TEST_SUBSECTION("v1 outcome formula underflow boundary");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.results.failopen_count = 4;
    snapshot.streaming.terminal_aborted_total = 3;

    snapshot.conversions_failed = 6;
    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);
    TEST_ASSERT(v1.requests.failed_closed == 0
                && v1.requests.aborted == 3,
        "failed_closed must clamp when failures are below deductions");

    snapshot.conversions_failed = 7;
    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);
    TEST_ASSERT(v1.requests.failed_closed == 0,
        "failed_closed must remain zero at the subtraction boundary");

    snapshot.conversions_failed = 8;
    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);
    TEST_ASSERT(v1.requests.failed_closed == 1,
        "failed_closed must preserve the positive residual");

    snapshot.results.failopen_count = (ngx_atomic_uint_t) -1;
    snapshot.streaming.terminal_aborted_total = 1;
    snapshot.conversions_failed = (ngx_atomic_uint_t) -1;
    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);
    TEST_ASSERT(v1.requests.failed_closed == 0,
        "failed_closed must not wrap when deduction counters overflow");

    TEST_PASS("v1 outcome formula clamps underflow");
}

static void
test_v1_latency_mapping_uses_all_frozen_boundaries(void)
{
    static const ngx_atomic_uint_t expected_full[] =
        { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 };
    static const ngx_atomic_uint_t expected_streaming[] =
        { 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 };
    ngx_http_markdown_metrics_snapshot_t  snapshot;
    ngx_http_markdown_metrics_v1_snapshot_t  v1;
    ngx_uint_t                              i;

    TEST_SUBSECTION("v1 latency mapping uses all frozen boundaries");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.conversion_latency_v1.full_buffer.le_1ms = 1;
    snapshot.conversion_latency_v1.full_buffer.le_5ms = 2;
    snapshot.conversion_latency_v1.full_buffer.le_10ms = 3;
    snapshot.conversion_latency_v1.full_buffer.le_25ms = 4;
    snapshot.conversion_latency_v1.full_buffer.le_50ms = 5;
    snapshot.conversion_latency_v1.full_buffer.le_100ms = 6;
    snapshot.conversion_latency_v1.full_buffer.le_250ms = 7;
    snapshot.conversion_latency_v1.full_buffer.le_500ms = 8;
    snapshot.conversion_latency_v1.full_buffer.le_1000ms = 9;
    snapshot.conversion_latency_v1.full_buffer.le_5000ms = 10;
    snapshot.conversion_latency_v1.full_buffer.count = 60;
    snapshot.conversion_latency_v1.streaming.le_1ms = 11;
    snapshot.conversion_latency_v1.streaming.le_5ms = 12;
    snapshot.conversion_latency_v1.streaming.le_10ms = 13;
    snapshot.conversion_latency_v1.streaming.le_25ms = 14;
    snapshot.conversion_latency_v1.streaming.le_50ms = 15;
    snapshot.conversion_latency_v1.streaming.le_100ms = 16;
    snapshot.conversion_latency_v1.streaming.le_250ms = 17;
    snapshot.conversion_latency_v1.streaming.le_500ms = 18;
    snapshot.conversion_latency_v1.streaming.le_1000ms = 19;
    snapshot.conversion_latency_v1.streaming.le_5000ms = 20;
    snapshot.conversion_latency_v1.streaming.count = 200;

    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);

    for (i = 0; i < 10; i++) {
        TEST_ASSERT(v1.duration_full_buffer.buckets[i] == expected_full[i],
                    "full-buffer finite buckets must preserve source bands");
        TEST_ASSERT(v1.duration_streaming.buckets[i] == expected_streaming[i],
                    "streaming finite buckets must preserve source bands");
    }
    TEST_ASSERT(v1.duration_full_buffer.buckets[9] == 10
                && v1.duration_full_buffer.count == 60,
                "full-buffer values above 5s must remain only in +Inf");
    TEST_ASSERT(v1.duration_streaming.buckets[9] == 20
                && v1.duration_streaming.count == 200,
                "streaming values above 5s must remain only in +Inf");

    TEST_PASS("All ten v1 latency boundaries map to production buckets");
}

static void
test_v1_latency_sum_conversion_is_bounded(void)
{
    ngx_http_markdown_metrics_snapshot_t  snapshot;
    ngx_http_markdown_metrics_v1_snapshot_t v1;
    ngx_atomic_uint_t                      maximum;

    TEST_SUBSECTION("v1 latency sum conversion is bounded");

    maximum = (ngx_atomic_uint_t) -1;
    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.conversion_latency_v1.full_buffer.count = 1;
    snapshot.conversion_latency_v1.full_buffer.sum_ms = maximum / 1000 + 1;

    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);
    TEST_ASSERT(v1.duration_full_buffer.sum_us == maximum,
                "full-buffer millisecond conversion must saturate");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.conversion_latency_v1.streaming.count = 1;
    snapshot.conversion_latency_v1.streaming.sum_ms = 1234;
    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);
    TEST_ASSERT(v1.duration_streaming.sum_us == 1234000,
                "streaming millisecond conversion must preserve precision");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.conversion_time_sum_ms = maximum / 1000 + 1;
    ngx_http_markdown_metrics_to_v1(&snapshot, &v1);
    TEST_ASSERT(v1.duration_full_buffer.sum_us == maximum,
                "aggregate millisecond conversion must saturate");

    TEST_PASS("v1 latency sum conversion is bounded");
}

static void
test_v1_renderer_emits_frozen_families_and_fails_on_truncation(void)
{
    u_char                                      buf[32768];
    u_char                                     *p;
    ngx_http_markdown_metrics_v1_snapshot_t     snapshot;

    TEST_SUBSECTION("v1 renderer emits frozen families and truncates safely");

    memset(&snapshot, 0, sizeof(snapshot));
    snapshot.requests.converted = 3;
    snapshot.attempts.full_buffer = 4;
    snapshot.deliveries.streaming = 5;
    snapshot.duration_full_buffer.buckets[0] = 6;
    snapshot.duration_full_buffer.count = 6;
    snapshot.input_bytes = 7;
    snapshot.output_bytes = 8;
    snapshot.streaming_events.resume_failure = 9;
    snapshot.decompression.gzip_failure_format = 10;
    snapshot.dynconf_reloads.failure_file_error = 11;
    snapshot.build_info.version = (const u_char *) "0.9.2";
    snapshot.build_info.nginx_version_text = (const u_char *) "1.26.3";
    snapshot.build_info.features = (const u_char *) "streaming";

    p = ngx_http_markdown_metrics_v1_render(
        buf, buf + sizeof(buf), &snapshot);
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "v1 renderer should fit the bounded test buffer");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf,
                         "# TYPE nginx_markdown_requests_total counter"),
                "v1 renderer must emit the requests family");
    TEST_ASSERT(contains((char *) buf,
                         "# TYPE nginx_markdown_conversion_duration_seconds histogram"),
                "v1 renderer must emit the histogram family");
    TEST_ASSERT(contains((char *) buf,
                         "{engine=\"full_buffer\",le=\"0.001\"} 6"),
                "v1 renderer must emit finite histogram buckets and labels");
    TEST_ASSERT(contains((char *) buf,
                         "{transition=\"resume_failure\",reason=\"streaming_mid_flight_error\"} 9"),
                "v1 renderer must emit bounded streaming event labels");
    TEST_ASSERT(contains((char *) buf,
                         "# TYPE nginx_markdown_build_info gauge"),
                "v1 renderer must emit build-info family");

    TEST_ASSERT(ngx_http_markdown_metrics_v1_render(
                    buf, buf + 64, &snapshot) == NULL,
                "v1 renderer must return NULL on truncation");

    TEST_PASS("v1 renderer emits frozen families and fails closed on truncation");
}

static void
test_json_single_path_fits(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;
    u_char path[] = "/api/docs";

    TEST_SUBSECTION("JSON bounded: single path fits");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 1;
    add_node(&node, path, sizeof(path) - 1, 42, 1500,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 42, 35, 100, 200);

    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "/api/docs"),
                "should contain path value");
    TEST_ASSERT(contains((char *) buf, "\"conversions\": 42"),
                "should contain conversion count");
    TEST_ASSERT(!contains((char *) buf, "__other__"),
                "single fitting path should not produce __other__");

    TEST_PASS("JSON single path fits correctly");
}

static void
test_json_zero_paths(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;

    TEST_SUBSECTION("JSON bounded: zero paths");

    init_snapshot(&s);

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(!contains((char *) buf, "__other__"),
                "zero paths should not produce __other__");
    TEST_ASSERT(contains((char *) buf, "\"paths\": ["),
                "should contain empty paths array");
    TEST_ASSERT(contains((char *) buf, "\"perf\":"),
                "mandatory perf object must be present");
    TEST_ASSERT(p > buf && p[-1] == '}',
                "JSON output must end with the outer closing brace");

    TEST_PASS("JSON zero paths correct");
}

static void
test_json_overflow_produces_other(void)
{
    u_char buf[16384];
    u_char *p;
    u_char path[] = "/x";
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;

    TEST_SUBSECTION("JSON bounded: overflow_count produces __other__");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 1;
    s.per_path.overflow_count = 5;
    s.per_path.unretained_conversions = 5;
    s.per_path.unretained_conversion_time_sum_ms = 55;

    add_node(&node, path, sizeof(path) - 1, 7, 70,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "__other__"),
                "overflow_count should produce __other__ entry");


    TEST_PASS("JSON overflow __other__ correct");
}


static void
test_json_overflow_without_retained_paths(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_shm_zone_t *saved_zone;

    TEST_SUBSECTION("JSON bounded: empty tree overflow remains visible");

    init_snapshot(&s);
    s.per_path.overflow_count = 5;
    s.per_path.unretained_conversions = 5;
    s.per_path.unretained_conversion_time_sum_ms = 55;
    saved_zone = ngx_http_markdown_metrics_shm_zone;
    ngx_http_markdown_metrics_shm_zone = NULL;

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);

    ngx_http_markdown_metrics_shm_zone = saved_zone;
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "overflow-only JSON renderer should succeed without SHM");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "\"path\":\"__other__\""),
                "overflow-only JSON must contain __other__");
    TEST_ASSERT(contains((char *) buf, "\"conversions\":5"),
                "overflow-only JSON must preserve conversion count");
    TEST_ASSERT(contains((char *) buf, "\"conversion_time_sum_ms\":55"),
                "overflow-only JSON must preserve conversion time");
    TEST_ASSERT(p > buf && p[-1] == '}',
                "overflow-only JSON must keep its closing brace");

    TEST_PASS("JSON empty-tree overflow correct");
}

static void
test_json_oversized_path_omitted(void)
{
    u_char buf[16384];
    u_char path[256];
    u_char *p;
    size_t base_size;
    size_t capacity;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;

    TEST_SUBSECTION("JSON bounded: oversized path omitted into __other__");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);
    memset(path, 0x01, sizeof(path));

    s.per_path.path_entries = 0;
    s.per_path.overflow_count = 0;
    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "aggregate-only renderer should leave room for NUL");
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path), 42, 1500,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size
        + ngx_http_markdown_json_tail_reserve();

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p < buf + capacity,
                "bounded JSON should stay within capacity");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "__other__"),
                "oversized path should produce __other__");
    TEST_ASSERT(!contains((char *) buf, "\\u0001\\u0001"),
                "oversized path should not be partially emitted");

    TEST_PASS("JSON oversized path omission correct");
}

static void
test_json_structural_completeness(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;
    u_char path[] = "/test";

    TEST_SUBSECTION("JSON bounded: structural completeness");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 1;
    add_node(&node, path, sizeof(path) - 1, 5, 50,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 5, 10, 100, 200);

    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "renderer should succeed");
    *p = '\0';

    {
        FILE *parser;
        size_t written;
        int status;

        parser = popen("python3 -c 'import json, sys; json.load(sys.stdin)'",
                       "w");
        TEST_ASSERT(parser != NULL,
                    "strict JSON parser should be available");
        written = fwrite(buf, 1, (size_t) (p - buf), parser);
        TEST_ASSERT(written == (size_t) (p - buf),
                    "strict JSON parser must receive the complete document");
        status = pclose(parser);
        TEST_ASSERT(status == 0,
                    "bounded JSON output must pass strict JSON parsing");
    }

    TEST_PASS("JSON structural completeness correct");
}

static void
test_json_escape_expansion(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;
    u_char path[] = "/api/\"test\"\\path\n";

    TEST_SUBSECTION("JSON bounded: escape expansion in path");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 1;
    add_node(&node, path, sizeof(path) - 1, 1, 10,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 1, 10, 100, 200);

    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "\\\"test\\\""),
                "double-quotes should be escaped in JSON");
    TEST_ASSERT(contains((char *) buf, "\\\\path"),
                "backslash should be escaped in JSON");
    TEST_ASSERT(contains((char *) buf, "\\n"),
                "newline should be escaped in JSON");

    TEST_PASS("JSON escape expansion correct");
}

static void
test_json_other_time_ms_not_zero(void)
{
    u_char buf[16384];
    u_char *p;
    size_t base_size;
    size_t capacity;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;
    u_char path[] = "/some/longer/path/that/might/not/fit";

    TEST_SUBSECTION("JSON bounded: __other__ conversion_time_sum_ms from omitted");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 0;
    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "aggregate-only renderer should leave room for NUL");
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path) - 1, 5, 999,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size + ngx_http_markdown_json_tail_reserve();

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p < buf + capacity,
                "renderer should succeed without exhausting the buffer");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "__other__"),
                "omitted path should produce __other__");
    TEST_ASSERT(contains((char *) buf,
                         "\"conversion_time_sum_ms\":999"),
                "__other__ should contain omitted time_ms");

    TEST_PASS("JSON __other__ time_ms correct");
}

static void
test_json_path_entry_size_positive(void)
{
    TEST_SUBSECTION("json_path_entry_size: positive for all inputs");

    TEST_ASSERT(ngx_http_markdown_json_path_entry_size(0) > 0,
                "zero-length path should have positive entry size");
    TEST_ASSERT(ngx_http_markdown_json_path_entry_size(100) > 0,
                "100-byte path should have positive entry size");
    TEST_ASSERT(ngx_http_markdown_json_path_entry_size(100)
                > ngx_http_markdown_json_path_entry_size(1),
                "longer path should have larger entry size");

    TEST_PASS("json_path_entry_size positive");
}


static void
test_path_entry_size_overflow_is_rejected(void)
{
    size_t  overflow_len;

    TEST_SUBSECTION("path entry size: overflow is rejected");

    overflow_len = ((size_t) -1) / 6 + 1;
    TEST_ASSERT(ngx_http_markdown_json_path_entry_size(overflow_len)
                == (size_t) -1,
                "JSON path entry overflow must be omitted");
    TEST_ASSERT(ngx_http_markdown_text_path_entry_size(overflow_len)
                == (size_t) -1,
                "text path entry overflow must be omitted");

    TEST_PASS("path entry size overflow is rejected");
}

static void
test_text_single_path_fits(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;
    u_char path[] = "/api/docs";

    TEST_SUBSECTION("text bounded: single path fits");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 1;
    add_node(&node, path, sizeof(path) - 1, 42, 1500,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + sizeof(buf), &s, 42, 35, 100, 200);

    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "/api/docs"),
                "should contain path value");
    TEST_ASSERT(contains((char *) buf, "conversions=42"),
                "should contain conversion count");
    TEST_ASSERT(!contains((char *) buf, "__other__"),
                "single fitting path should not produce __other__");

    TEST_PASS("text single path fits correctly");
}

static void
test_text_zero_paths(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;

    TEST_SUBSECTION("text bounded: zero paths");

    init_snapshot(&s);

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(!contains((char *) buf, "__other__"),
                "zero paths should not produce __other__");
    TEST_ASSERT(!contains((char *) buf, "Per-Path Details"),
                "zero paths should not emit per-path section");

    TEST_PASS("text zero paths correct");
}

static void
test_text_overflow_produces_other(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;
    u_char path[] = "/x";

    TEST_SUBSECTION("text bounded: overflow_count produces __other__");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 1;
    s.per_path.overflow_count = 3;
    s.per_path.unretained_conversions = 3;
    s.per_path.unretained_conversion_time_sum_ms = 30;

    add_node(&node, path, sizeof(path) - 1, 7, 70,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + sizeof(buf), &s, 7, 10, 100, 200);

    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "__other__"),
                "overflow_count should produce __other__ line");

    TEST_PASS("text overflow __other__ correct");
}


static void
test_text_overflow_without_retained_paths(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_shm_zone_t *saved_zone;

    TEST_SUBSECTION("text bounded: empty tree overflow remains visible");

    init_snapshot(&s);
    s.per_path.overflow_count = 3;
    s.per_path.unretained_conversions = 3;
    s.per_path.unretained_conversion_time_sum_ms = 30;
    saved_zone = ngx_http_markdown_metrics_shm_zone;
    ngx_http_markdown_metrics_shm_zone = NULL;

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);

    ngx_http_markdown_metrics_shm_zone = saved_zone;
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "overflow-only text renderer should succeed without SHM");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "Per-Path Details"),
                "overflow-only text must have a details section");
    TEST_ASSERT(contains((char *) buf, "Path[__other__]: conversions=3"),
                "overflow-only text must preserve conversion count");
    TEST_ASSERT(contains((char *) buf, "time_ms=30"),
                "overflow-only text must preserve conversion time");

    TEST_PASS("text empty-tree overflow correct");
}

static void
test_text_oversized_path_omitted(void)
{
    u_char buf[16384];
    u_char path[256];
    u_char *p;
    size_t base_size;
    size_t capacity;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;

    TEST_SUBSECTION("text bounded: oversized path omitted into __other__");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);
    memset(path, 0x01, sizeof(path));

    s.per_path.path_entries = 0;
    p = ngx_http_markdown_metrics_write_text(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "aggregate-only renderer should leave room for NUL");
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path), 42, 1500,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size
        + ngx_http_markdown_text_tail_reserve();

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p < buf + capacity,
                "bounded text should stay within capacity");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "__other__"),
                "oversized path should produce __other__");

    TEST_PASS("text oversized path omission correct");
}

static void
test_text_no_shm_zone(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_shm_zone_t *saved_zone;

    TEST_SUBSECTION("text bounded: NULL shm_zone");

    init_snapshot(&s);
    s.per_path.path_entries = 3;

    saved_zone = ngx_http_markdown_metrics_shm_zone;
    ngx_http_markdown_metrics_shm_zone = NULL;

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);

    ngx_http_markdown_metrics_shm_zone = saved_zone;

    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "renderer should succeed even without SHM zone");
    *p = '\0';
    TEST_ASSERT(!contains((char *) buf, "Per-Path Details"),
                "should not emit per-path section without SHM zone");

    TEST_PASS("text NULL shm_zone skip correct");
}

static void
test_text_path_entry_size_positive(void)
{
    TEST_SUBSECTION("text_path_entry_size: positive for all inputs");

    TEST_ASSERT(ngx_http_markdown_text_path_entry_size(0) > 0,
                "zero-length path should have positive entry size");
    TEST_ASSERT(ngx_http_markdown_text_path_entry_size(100) > 0,
                "100-byte path should have positive entry size");

    TEST_PASS("text_path_entry_size positive");
}

static void
test_json_tail_reserve_positive(void)
{
    size_t  expected;

    TEST_SUBSECTION("json_tail_reserve: complete mandatory suffix");

    expected = sizeof("\n    ]\n  },\n") - 1
        + ngx_http_markdown_json_other_entry_size()
        + NGX_HTTP_MARKDOWN_JSON_PERF_MAX_SIZE
        + sizeof("}\n") - 1;

    TEST_ASSERT(ngx_http_markdown_json_tail_reserve() == expected,
                "tail reserve must include paths, __other__, perf, and brace");

    TEST_PASS("json_tail_reserve covers the mandatory suffix");
}

static void
test_text_tail_reserve_positive(void)
{
    TEST_SUBSECTION("text_tail_reserve: positive");

    TEST_ASSERT(ngx_http_markdown_text_tail_reserve() > 0,
                "tail reserve must be positive");

    TEST_PASS("text_tail_reserve positive");
}

static void
test_json_no_partial_path_on_budget_exhaustion(void)
{
    u_char buf[16384];
    u_char *p;
    size_t base_size;
    size_t capacity;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;
    u_char path[] = "/very/long/path/that/will/be/omitted";

    TEST_SUBSECTION("JSON bounded: no partial path on budget exhaustion");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 0;
    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "aggregate-only renderer should leave room for NUL");
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path) - 1, 1, 1,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size + ngx_http_markdown_json_tail_reserve();

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p < buf + capacity,
                "bounded JSON should stay within capacity");
    *p = '\0';

    if (contains((char *) buf, "\"path\":")) {
        TEST_ASSERT(contains((char *) buf, "\"conversions\":"),
                    "if path key appears, entry must be complete");
    }

    TEST_PASS("JSON no partial path on exhaustion");
}

static void
test_text_no_partial_line_on_budget_exhaustion(void)
{
    u_char buf[16384];
    u_char *p;
    size_t base_size;
    size_t capacity;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;
    u_char path[] = "/very/long/path/that/will/be/omitted";

    TEST_SUBSECTION("text bounded: no partial line on budget exhaustion");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 0;
    p = ngx_http_markdown_metrics_write_text(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "aggregate-only renderer should leave room for NUL");
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path) - 1, 1, 1,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size + ngx_http_markdown_text_tail_reserve();

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p < buf + capacity,
                "bounded text should stay within capacity");
    *p = '\0';

    if (contains((char *) buf, "Path[")) {
        TEST_ASSERT(contains((char *) buf, "conversions="),
                    "if Path[ appears, line must be complete");
    }

    TEST_PASS("text no partial line on exhaustion");
}

static void
assert_text_other_values(
    const u_char *buf,
    ngx_atomic_uint_t conversions,
    ngx_atomic_uint_t entries,
    ngx_atomic_uint_t time_ms)
{
    const char          *line;
    unsigned long        actual_conversions;
    unsigned long        actual_entries;
    unsigned long        actual_time_ms;

    line = strstr((const char *) buf, "- Path[__other__]:");
    TEST_ASSERT(line != NULL, "plain text must contain __other__");
    TEST_ASSERT(sscanf(line,
                       "- Path[__other__]: conversions=%lu entries=%lu time_ms=%lu",
                       &actual_conversions, &actual_entries, &actual_time_ms) == 3,
                "plain text __other__ values must parse");
    TEST_ASSERT(actual_conversions == conversions,
                "plain text __other__ conversions must match");
    TEST_ASSERT(actual_entries == entries,
                "plain text __other__ entries must match");
    TEST_ASSERT(actual_time_ms == time_ms,
                "plain text __other__ time must match");
}

static void
assert_json_other_values(
    const u_char *buf,
    size_t len,
    ngx_atomic_uint_t conversions,
    ngx_atomic_uint_t entries,
    ngx_atomic_uint_t time_ms)
{
    char    command[512];
    FILE   *parser;
    int     status;

    snprintf(command, sizeof(command),
             "python3 -c 'import json,sys; o=json.load(sys.stdin); "
             "n=next(x for x in o[\"per_path\"][\"paths\"] "
             "if x[\"path\"] == \"__other__\"); "
             "assert (n[\"conversions\"],n[\"entries\"],"
             "n[\"conversion_time_sum_ms\"]) == (%lu,%lu,%lu)'",
             (unsigned long) conversions, (unsigned long) entries,
             (unsigned long) time_ms);
    parser = popen(command, "w");
    TEST_ASSERT(parser != NULL, "strict JSON parser should be available");
    TEST_ASSERT(fwrite(buf, 1, len, parser) == len,
                "strict JSON parser must receive the complete document");
    status = pclose(parser);
    TEST_ASSERT(status == 0,
                "strict JSON __other__ values must preserve conservation");
}

static void
test_other_entries_match_unretained_conversions(void)
{
    u_char                                   buf[16384];
    u_char                                  *p;
    ngx_http_markdown_metrics_snapshot_t     s;
    ngx_shm_zone_t                          *saved_zone;

    TEST_SUBSECTION("__other__ entries: allocation failure conservation");

    init_snapshot(&s);
    s.per_path.unretained_conversions = 1;
    s.per_path.unretained_conversion_time_sum_ms = 17;
    saved_zone = ngx_http_markdown_metrics_shm_zone;
    ngx_http_markdown_metrics_shm_zone = NULL;

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "allocation-only JSON renderer should succeed");
    assert_json_other_values(buf, (size_t) (p - buf), 1, 1, 17);

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);
    ngx_http_markdown_metrics_shm_zone = saved_zone;
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "allocation-only plain renderer should succeed");
    *p = '\0';
    assert_text_other_values(buf, 1, 1, 17);

    TEST_PASS("allocation failure entries match conversions");
}

static void
test_other_entries_sum_omitted_node_entries(void)
{
    u_char                                   buf[16384];
    u_char                                  *p;
    u_char                                   path[4096];
    size_t                                   base_size;
    size_t                                   capacity;
    ngx_http_markdown_metrics_snapshot_t     s;
    ngx_http_markdown_metrics_t              live;
    ngx_http_markdown_path_metric_node_t     node;
    ngx_slab_pool_t                          shpool;

    TEST_SUBSECTION("__other__ entries: omitted high-frequency path");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);
    memset(path, 0x01, sizeof(path));
    p = ngx_http_markdown_metrics_write_json(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "aggregate-only JSON renderer should succeed");
    base_size = (size_t) (p - buf);
    add_node(&node, path, sizeof(path), 100, 700,
             &live.per_path.sentinel);
    node.entries = 100;
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;
    capacity = base_size + ngx_http_markdown_json_tail_reserve();

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + capacity, &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + capacity,
                "bounded JSON omission should succeed");
    assert_json_other_values(buf, (size_t) (p - buf), 100, 100, 700);

    s.per_path.path_entries = 0;
    p = ngx_http_markdown_metrics_write_text(
        buf, buf + sizeof(buf), &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + sizeof(buf),
                "aggregate-only plain renderer should succeed");
    base_size = (size_t) (p - buf);
    s.per_path.path_entries = 1;
    capacity = base_size + ngx_http_markdown_text_tail_reserve();
    p = ngx_http_markdown_metrics_write_text(
        buf, buf + capacity, &s, 0, 0, 0, 0);
    TEST_ASSERT(p != NULL && p < buf + capacity,
                "bounded plain-text omission should succeed");
    *p = '\0';
    assert_text_other_values(buf, 100, 100, 700);

    TEST_PASS("omitted high-frequency entries are conserved");
}

static void
test_other_entries_saturate_without_wrapping(void)
{
    ngx_atomic_uint_t  maximum;

    TEST_SUBSECTION("__other__ entries: saturating aggregation");

    maximum = (ngx_atomic_uint_t) -1;
    TEST_ASSERT(ngx_http_markdown_metrics_saturating_add(maximum - 1, 2)
                == maximum,
                "unretained and omitted entries must saturate");
    TEST_ASSERT(ngx_http_markdown_metrics_saturating_add(3, 4) == 7,
                "non-saturating entry sums must stay exact");

    TEST_PASS("entry aggregation saturates without wrapping");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_bounded_rendering Tests\n");
    printf("========================================\n");

    test_malformed_path_len_sets_failed_and_stops_right_walk();
    test_malformed_path_detail_writers_return_null();
    test_malformed_path_outer_renderers_return_null();
    test_metrics_handler_uses_frozen_v1_surface();
    test_v1_output_bytes_include_streaming_delivery();
    test_v1_engine_delivery_and_event_sources();
    test_v1_outcome_formula_clamps_underflow();
    test_v1_latency_mapping_uses_all_frozen_boundaries();
    test_v1_latency_sum_conversion_is_bounded();
    test_v1_renderer_emits_frozen_families_and_fails_on_truncation();
    test_json_single_path_fits();
    test_json_zero_paths();
    test_json_overflow_produces_other();
    test_json_overflow_without_retained_paths();
    test_json_oversized_path_omitted();
    test_json_structural_completeness();
    test_json_escape_expansion();
    test_json_other_time_ms_not_zero();
    test_json_path_entry_size_positive();
    test_path_entry_size_overflow_is_rejected();
    test_text_single_path_fits();
    test_text_zero_paths();
    test_text_overflow_produces_other();
    test_text_overflow_without_retained_paths();
    test_text_oversized_path_omitted();
    test_text_no_shm_zone();
    test_text_path_entry_size_positive();
    test_json_tail_reserve_positive();
    test_text_tail_reserve_positive();
    test_json_no_partial_path_on_budget_exhaustion();
    test_text_no_partial_line_on_budget_exhaustion();
    test_other_entries_match_unretained_conversions();
    test_other_entries_sum_omitted_node_entries();
    test_other_entries_saturate_without_wrapping();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
