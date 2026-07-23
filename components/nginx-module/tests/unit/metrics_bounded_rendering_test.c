/*
 * Test: metrics_bounded_rendering
 *
 * Validates bounded per-path rendering for JSON and plain-text
 * metrics formats.  Follows the same pattern as
 * prometheus_per_path_test.c: defines minimal NGINX types and
 * includes only the metrics_impl.h header, which contains the
 * bounded walk functions and rendering helpers.
 */

#include "../include/test_common.h"
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
    } skips;
    struct {
        ngx_atomic_t  failopen_count;
        ngx_atomic_t  delivery_count;
        ngx_atomic_t  decision_count;
        ngx_atomic_t  estimated_token_savings;
        ngx_atomic_t  replay_buffer_errors_total;
        struct {
            ngx_atomic_t  parse_timeouts_total;
            ngx_atomic_t  parse_budget_exceeded_total;
        } parse_interrupts;
    } results;
    struct {
        ngx_atomic_t  requests_total;
        ngx_atomic_t  fallback_total;
        ngx_atomic_t  succeeded_total;
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
        ngx_atomic_t  zero_copy_output_total;
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

static ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *out)
{
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

#include "../../src/ngx_http_markdown_metrics_impl.h"

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

    TEST_PASS("JSON zero paths correct");
}

static void
test_json_overflow_produces_other(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;

    TEST_SUBSECTION("JSON bounded: overflow_count produces __other__");

    init_snapshot(&s);
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    init_tree(&live);
    setup_shm(&live, &shpool);

    s.per_path.path_entries = 1;
    s.per_path.overflow_count = 5;

    {
        ngx_http_markdown_path_metric_node_t node;
        u_char path[] = "/x";
        add_node(&node, path, sizeof(path) - 1, 7, 70,
                 &live.per_path.sentinel);
        live.per_path.path_tree.root = &node.rbnode;
    }

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
    TEST_ASSERT(p != NULL, "aggregate-only renderer should succeed");
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path), 42, 1500,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size
        + ngx_http_markdown_json_tail_reserve();

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p <= buf + capacity,
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
        int brace_depth = 0;
        size_t i;
        for (i = 0; buf[i] != '\0'; i++) {
            if (buf[i] == '{') brace_depth++;
            if (buf[i] == '}') brace_depth--;
        }
        TEST_ASSERT(brace_depth == 0,
                    "JSON braces must be balanced");
        TEST_ASSERT(contains((char *) buf, "]"),
                    "paths array must be closed with ]");
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
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path) - 1, 5, 999,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size + ngx_http_markdown_json_tail_reserve();

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL, "renderer should succeed");
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
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path), 42, 1500,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size
        + ngx_http_markdown_text_tail_reserve();

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p <= buf + capacity,
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
    TEST_SUBSECTION("json_tail_reserve: positive");

    TEST_ASSERT(ngx_http_markdown_json_tail_reserve() > 0,
                "tail reserve must be positive");

    TEST_PASS("json_tail_reserve positive");
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
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path) - 1, 1, 1,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size + ngx_http_markdown_json_tail_reserve();

    p = ngx_http_markdown_metrics_write_json(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p <= buf + capacity,
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
    base_size = (size_t) (p - buf);

    add_node(&node, path, sizeof(path) - 1, 1, 1,
             &live.per_path.sentinel);
    live.per_path.path_tree.root = &node.rbnode;
    s.per_path.path_entries = 1;

    capacity = base_size + ngx_http_markdown_text_tail_reserve();

    p = ngx_http_markdown_metrics_write_text(
        buf, buf + capacity, &s, 0, 0, 0, 0);

    TEST_ASSERT(p != NULL && p <= buf + capacity,
                "bounded text should stay within capacity");
    *p = '\0';

    if (contains((char *) buf, "Path[")) {
        TEST_ASSERT(contains((char *) buf, "conversions="),
                    "if Path[ appears, line must be complete");
    }

    TEST_PASS("text no partial line on exhaustion");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_bounded_rendering Tests\n");
    printf("========================================\n");

    test_json_single_path_fits();
    test_json_zero_paths();
    test_json_overflow_produces_other();
    test_json_oversized_path_omitted();
    test_json_structural_completeness();
    test_json_escape_expansion();
    test_json_other_time_ms_not_zero();
    test_json_path_entry_size_positive();
    test_text_single_path_fits();
    test_text_zero_paths();
    test_text_overflow_produces_other();
    test_text_oversized_path_omitted();
    test_text_no_shm_zone();
    test_text_path_entry_size_positive();
    test_json_tail_reserve_positive();
    test_text_tail_reserve_positive();
    test_json_no_partial_path_on_budget_exhaustion();
    test_text_no_partial_line_on_budget_exhaustion();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
