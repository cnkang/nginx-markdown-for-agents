/*
 * Test: prometheus_per_path
 */

#include "../include/test_common.h"
#include <stdarg.h>
#include <string.h>

typedef unsigned char u_char;

typedef struct {
    size_t     len;
    u_char    *data;
} ngx_str_t;

typedef intptr_t  ngx_int_t;
typedef uintptr_t ngx_uint_t;
typedef long      ngx_atomic_t;
typedef unsigned long ngx_atomic_uint_t;

#define NGX_OK        0
#define NGX_ERROR    -1
#define NGX_DECLINED -5

#define ngx_string(str) { sizeof(str) - 1, (u_char *) str }

typedef struct {
    ngx_atomic_uint_t backpressure_total;
    ngx_atomic_uint_t backpressure_resume_total;
    ngx_atomic_uint_t pending_output_high_watermark_bytes;
    ngx_atomic_uint_t decompression_streaming_total;
    ngx_atomic_uint_t decompression_fullbuffer_total;
    ngx_atomic_uint_t decompression_budget_exceeded_total;
    ngx_atomic_uint_t zero_copy_output_total;
    ngx_atomic_uint_t copied_output_total;
    struct {
        ngx_atomic_uint_t current;
        ngx_atomic_uint_t high_watermark;
        ngx_atomic_uint_t overload_total;
    } inflight;
} ngx_http_markdown_metrics_perf_snapshot_t;

typedef struct { /* SONAR_NOTE: mirrors production snapshot */
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
        ngx_atomic_t  path_entries;
        ngx_atomic_t  path_conversions;
        ngx_atomic_t  path_conversion_time_sum_ms;
        ngx_atomic_t  overflow_count;
    } per_path;
    ngx_http_markdown_metrics_perf_snapshot_t perf;
} ngx_http_markdown_metrics_snapshot_t;

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
} ngx_http_markdown_metrics_t;

static u_char *
ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...) /* SONAR_NOTE */
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
ngx_snprintf(u_char *buf, size_t size, const char *fmt, ...) /* SONAR_NOTE */
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

#include "../../src/ngx_http_markdown_prometheus_impl.h" /* SONAR_NOTE */

static int
contains(const char *haystack, const char *needle)
{
    return strstr(haystack, needle) != NULL;
}

static void
init_path_render(
    ngx_http_markdown_prometheus_path_render_ctx_t *render,
    u_char *start,
    u_char *end,
    size_t tail_reserve)
{
    memset(render, 0, sizeof(*render));
    render->pos = start;
    render->end = end;
    render->tail_reserve = tail_reserve;
}

static void
test_escape_prometheus_label_basic(void)
{
    u_char buf[256];
    u_char *p;
    const u_char input[] = "hello world";

    TEST_SUBSECTION("escape_prometheus_label: basic passthrough");

    p = ngx_http_markdown_escape_prometheus_label(
        buf, buf + sizeof(buf), input, sizeof(input) - 1);
    TEST_ASSERT(p > buf, "should produce output");
    *p = '\0';
    TEST_ASSERT(strcmp((char *) buf, "hello world") == 0,
                "plain text should pass through unchanged");

    TEST_PASS("basic passthrough correct");
}

static void
test_escape_prometheus_label_backslash(void)
{
    u_char buf[256];
    u_char *p;
    const u_char input[] = "path\\with\\backslash";

    TEST_SUBSECTION("escape_prometheus_label: backslash escaping");

    p = ngx_http_markdown_escape_prometheus_label(
        buf, buf + sizeof(buf), input, sizeof(input) - 1);
    *p = '\0';
    TEST_ASSERT(strcmp((char *) buf, "path\\\\with\\\\backslash") == 0,
                "backslash should be escaped to double-backslash");

    TEST_PASS("backslash escaping correct");
}

static void
test_escape_prometheus_label_double_quote(void)
{
    u_char buf[256];
    u_char *p;
    const u_char input[] = "val\"ue";

    TEST_SUBSECTION("escape_prometheus_label: double-quote escaping");

    p = ngx_http_markdown_escape_prometheus_label(
        buf, buf + sizeof(buf), input, sizeof(input) - 1);
    *p = '\0';
    TEST_ASSERT(strcmp((char *) buf, "val\\\"ue") == 0,
                "double-quote should be escaped");

    TEST_PASS("double-quote escaping correct");
}

static void
test_escape_prometheus_label_newline(void)
{
    u_char buf[256];
    u_char *p;
    const u_char input[] = "line1\nline2";

    TEST_SUBSECTION("escape_prometheus_label: newline escaping");

    p = ngx_http_markdown_escape_prometheus_label(
        buf, buf + sizeof(buf), input, sizeof(input) - 1);
    *p = '\0';
    TEST_ASSERT(strcmp((char *) buf, "line1\\nline2") == 0,
                "newline should be escaped to \\n");

    TEST_PASS("newline escaping correct");
}

static void
test_escape_prometheus_label_cr_and_tab(void)
{
    u_char buf[256];
    u_char *p;
    const u_char input[] = "a\tb\rc";

    TEST_SUBSECTION("escape_prometheus_label: CR and tab escaping");

    p = ngx_http_markdown_escape_prometheus_label(
        buf, buf + sizeof(buf), input, sizeof(input) - 1);
    *p = '\0';
    TEST_ASSERT(strcmp((char *) buf, "a\\tb\\rc") == 0,
                "tab and CR should be escaped");

    TEST_PASS("CR and tab escaping correct");
}

static void
test_escape_prometheus_label_control_char(void)
{
    u_char buf[256];
    u_char *p;
    const u_char input[] = { 0x01, 0x00 };

    TEST_SUBSECTION("escape_prometheus_label: control character \\uXXXX");

    p = ngx_http_markdown_escape_prometheus_label(
        buf, buf + sizeof(buf), input, 1);
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "\\u0001"),
                "control char should be escaped as \\u0001");

    TEST_PASS("control character escaping correct");
}

static void
test_escape_prometheus_label_buffer_overflow(void)
{
    u_char buf[3];
    u_char *p;
    const u_char input[] = "abc";

    TEST_SUBSECTION("escape_prometheus_label: buffer overflow clamping");

    p = ngx_http_markdown_escape_prometheus_label(
        buf, buf + sizeof(buf), input, sizeof(input) - 1);
    TEST_ASSERT(p <= buf + sizeof(buf),
                "output should be clamped to buffer end");

    TEST_PASS("buffer overflow clamping correct");
}

static void
test_escape_prom_two_char_buffer_overflow(void)
{
    u_char buf[1];
    u_char *p;

    TEST_SUBSECTION("escape_prom_two_char: insufficient buffer");

    p = ngx_http_markdown_escape_prom_two_char(buf, buf + sizeof(buf), 'n');
    TEST_ASSERT(p == buf + sizeof(buf),
                "should return last when buffer too small for 2 chars");

    TEST_PASS("two_char overflow correct");
}

static void
test_prometheus_walk_path_tree_single_node(void)
{
    u_char buf[4096];
    u_char *p;
    ngx_rbtree_node_t sentinel;
    ngx_http_markdown_path_metric_node_t node;
    ngx_http_markdown_prometheus_path_render_ctx_t render;
    u_char path[] = "/api/docs";

    TEST_SUBSECTION("walk_path_tree: single node");

    memset(&sentinel, 0, sizeof(sentinel));
    memset(&node, 0, sizeof(node));
    node.path = path;
    node.path_len = sizeof(path) - 1;
    node.conversions = 42;
    node.conversion_time_sum_ms = 1500;
    node.rbnode.left = &sentinel;
    node.rbnode.right = &sentinel;

    init_path_render(&render, buf, buf + sizeof(buf), 0);
    ngx_http_markdown_prometheus_walk_path_tree(
        &node.rbnode, &sentinel, &render);
    p = render.pos;

    TEST_ASSERT(p > buf, "should produce output");
    TEST_ASSERT(render.omitted_nodes == 0,
                "fitting path should not be omitted");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "nginx_markdown_path_conversions_total"),
                "should contain conversions metric");
    TEST_ASSERT(contains((char *) buf, "/api/docs"),
                "should contain path label value");
    TEST_ASSERT(contains((char *) buf, "42"),
                "should contain conversion count");
    TEST_ASSERT(contains((char *) buf,
                "nginx_markdown_path_conversion_time_ms_total"),
                "should contain time metric");

    TEST_PASS("single node walk correct");
}

static void
test_prometheus_walk_path_tree_sentinel(void)
{
    u_char buf[256];
    u_char *p;
    ngx_rbtree_node_t sentinel;
    ngx_http_markdown_prometheus_path_render_ctx_t render;

    TEST_SUBSECTION("walk_path_tree: sentinel node terminates");

    memset(&sentinel, 0, sizeof(sentinel));
    init_path_render(&render, buf, buf + sizeof(buf), 0);
    ngx_http_markdown_prometheus_walk_path_tree(
        &sentinel, &sentinel, &render);
    p = render.pos;
    TEST_ASSERT(p == buf, "sentinel node should produce no output");

    TEST_PASS("sentinel termination correct");
}

static void
test_prometheus_walk_path_tree_buffer_full(void)
{
    u_char buf[4];
    u_char *p;
    ngx_rbtree_node_t sentinel;
    ngx_http_markdown_path_metric_node_t node;
    ngx_http_markdown_prometheus_path_render_ctx_t render;
    u_char path[] = "/x";

    TEST_SUBSECTION("walk_path_tree: buffer full returns p");

    memset(&sentinel, 0, sizeof(sentinel));
    memset(&node, 0, sizeof(node));
    node.path = path;
    node.path_len = sizeof(path) - 1;
    node.conversions = 1;
    node.rbnode.left = &sentinel;
    node.rbnode.right = &sentinel;

    memset(buf, 'X', sizeof(buf));
    init_path_render(&render, buf, buf + sizeof(buf), 0);
    ngx_http_markdown_prometheus_walk_path_tree(
        &node.rbnode, &sentinel, &render);
    p = render.pos;
    TEST_ASSERT(p <= buf + sizeof(buf),
                "should not write past buffer end");
    TEST_ASSERT(p == buf,
                "insufficient budget must not write a partial path pair");
    TEST_ASSERT(render.omitted_nodes == 1
                && render.omitted_conversions == 1,
                "insufficient budget should aggregate the omitted path");
    TEST_ASSERT(buf[0] == 'X',
                "insufficient budget should leave output bytes untouched");

    TEST_PASS("buffer full handling correct");
}

static void
test_prometheus_path_pair_exact_budget(void)
{
    u_char buf[512];
    char maximum[64];
    u_char path[] = { '/', '\\', '"', '\n', 0x01 };
    size_t needed;
    ngx_atomic_uint_t maximum_counter;
    ngx_http_markdown_prometheus_path_render_ctx_t render;
    ngx_int_t rc;

    TEST_SUBSECTION("path pair: exact escaped-byte budget");

    maximum_counter = (ngx_atomic_uint_t) -1;
    TEST_ASSERT(ngx_http_markdown_prometheus_path_pair_size(
                    path, sizeof(path), maximum_counter, maximum_counter,
                    &needed)
                == NGX_OK,
                "escaped pair size should be computable");
    TEST_ASSERT(needed < sizeof(buf),
                "test pair should fit the backing buffer");

    init_path_render(&render, buf, buf + needed, 0);
    rc = ngx_http_markdown_prometheus_write_path_pair(
        &render, path, sizeof(path), maximum_counter, maximum_counter,
        needed);

    TEST_ASSERT(rc == NGX_OK, "exact-fit pair should be emitted");
    TEST_ASSERT(render.pos == buf + needed,
                "exact-fit pair should consume its calculated size");
    TEST_ASSERT(render.failed == 0,
                "exact-fit pair should not flag rendering failure");

    buf[needed] = '\0';
    TEST_ASSERT(contains((char *) buf, "\\\\"),
                "backslash expansion should be budgeted");
    TEST_ASSERT(contains((char *) buf, "\\\""),
                "quote expansion should be budgeted");
    TEST_ASSERT(contains((char *) buf, "\\n"),
                "newline expansion should be budgeted");
    TEST_ASSERT(contains((char *) buf, "\\u0001"),
                "control-character expansion should be budgeted");

    snprintf(maximum, sizeof(maximum), "%lu",
             (unsigned long) maximum_counter);
    TEST_ASSERT(contains((char *) buf, maximum),
                "maximum-width counters should render without truncation");

    memset(buf, 'X', sizeof(buf));
    init_path_render(&render, buf, buf + needed - 1, 0);
    rc = ngx_http_markdown_prometheus_write_path_pair(
        &render, path, sizeof(path), maximum_counter, maximum_counter,
        needed);

    TEST_ASSERT(rc == NGX_DECLINED,
                "one-byte-short pair should be omitted");
    TEST_ASSERT(render.pos == buf,
                "one-byte-short pair must not be partially emitted");
    TEST_ASSERT(buf[0] == 'X',
                "one-byte-short pair should leave the buffer untouched");

    TEST_PASS("exact and one-byte-short pair budgets correct");
}

static void
test_prometheus_omitted_counter_saturation(void)
{
    u_char buf[1];
    ngx_atomic_uint_t maximum;
    ngx_http_markdown_prometheus_path_render_ctx_t render;

    TEST_SUBSECTION("path pair: omitted counter saturation");

    maximum = (ngx_atomic_uint_t) -1;
    init_path_render(&render, buf, buf + sizeof(buf), 0);
    render.omitted_conversions = maximum - 2;
    render.omitted_time_ms = maximum - 3;

    ngx_http_markdown_prometheus_accumulate_omitted_path(
        &render, 10, 20);

    TEST_ASSERT(render.omitted_conversions == maximum,
                "omitted conversions should saturate");
    TEST_ASSERT(render.omitted_time_ms == maximum,
                "omitted time should saturate");
    TEST_ASSERT(render.omitted_nodes == 1,
                "omitted path count should still advance");

    TEST_PASS("omitted counter saturation correct");
}

static void
test_per_path_walk_with_overflow(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;

    TEST_SUBSECTION("per-path walk with __other__ overflow");

    memset(&s, 0, sizeof(s));
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));

    s.per_path.path_entries = 1;
    s.per_path.overflow_count = 5;

    memset(&live.per_path.sentinel, 0, sizeof(live.per_path.sentinel));
    live.per_path.sentinel.left = &live.per_path.sentinel;
    live.per_path.sentinel.right = &live.per_path.sentinel;
    live.per_path.path_tree.root = &live.per_path.sentinel;

    g_shm_zone.data = &live;
    g_shm_zone.shm.addr = &shpool;
    ngx_http_markdown_metrics_shm_zone = &g_shm_zone;

    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + sizeof(buf), &s);

    TEST_ASSERT(p != NULL, "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "__other__"),
                "should emit __other__ pseudo-path for overflow");
    TEST_ASSERT(contains((char *) buf,
                "nginx_markdown_path_conversions_total"),
                "should contain per-path conversions metric family");

    TEST_PASS("__other__ overflow path correct");
}

static void
test_per_path_response_budget_uses_other(void)
{
    u_char buf[16384];
    u_char path[256];
    u_char *p;
    size_t base_size;
    size_t capacity;
    size_t header_size;
    size_t node_size;
    size_t tail_reserve;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node;

    TEST_SUBSECTION("per-path response budget folds omitted pairs");

    memset(&s, 0, sizeof(s));
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    memset(&node, 0, sizeof(node));
    memset(path, 0x01, sizeof(path));

    /*
     * Measure the fixed aggregate output with the same-width aggregate
     * values, then provide exactly the detailed header plus reserved tail.
     */
    s.per_path.path_entries = 0;
    s.per_path.overflow_count = 5;
    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + sizeof(buf), &s);
    TEST_ASSERT(p != NULL, "aggregate-only renderer should succeed");
    base_size = (size_t) (p - buf);

    TEST_ASSERT(ngx_http_markdown_prometheus_path_tail_reserve(
                    &tail_reserve)
                == NGX_OK,
                "tail reserve should be computable");
    header_size = sizeof(NGX_HTTP_MARKDOWN_PROM_PATH_HEADER) - 1;
    capacity = base_size + header_size + tail_reserve;
    TEST_ASSERT(capacity < sizeof(buf),
                "bounded integration response should fit test storage");

    node.path = path;
    node.path_len = sizeof(path);
    node.conversions = 42;
    node.conversion_time_sum_ms = 1500;
    node.rbnode.left = &live.per_path.sentinel;
    node.rbnode.right = &live.per_path.sentinel;
    live.per_path.path_tree.root = &node.rbnode;

    TEST_ASSERT(ngx_http_markdown_prometheus_path_pair_size(
                    path, sizeof(path), 42, 1500, &node_size)
                == NGX_OK,
                "large escaped path size should be computable");
    TEST_ASSERT(node_size > tail_reserve,
                "test path should exceed the reserved summary tail");

    g_shm_zone.data = &live;
    g_shm_zone.shm.addr = &shpool;
    ngx_http_markdown_metrics_shm_zone = &g_shm_zone;
    s.per_path.path_entries = 1;

    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + capacity, &s);

    TEST_ASSERT(p != NULL,
                "path detail exhaustion should preserve aggregate scrape");
    TEST_ASSERT(p < buf + capacity,
                "summary output should remain inside the response budget");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf,
                "nginx_markdown_path_conversions_total"
                "{path=\"__other__\"} 47\n"),
                "cardinality and response omissions should be combined");
    TEST_ASSERT(contains((char *) buf,
                "nginx_markdown_path_conversion_time_ms_total"
                "{path=\"__other__\"} 1500\n"),
                "omitted conversion time should be retained");
    TEST_ASSERT(!contains((char *) buf, "\\u0001"),
                "oversized detailed path should not be partially emitted");

    TEST_PASS("response-budget omission summary correct");
}

static void
test_per_path_walk_no_shm_zone(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_shm_zone_t *saved_zone;

    TEST_SUBSECTION("per-path walk with NULL shm_zone");

    memset(&s, 0, sizeof(s));
    s.per_path.path_entries = 3;

    saved_zone = ngx_http_markdown_metrics_shm_zone;
    ngx_http_markdown_metrics_shm_zone = NULL;

    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + sizeof(buf), &s);

    ngx_http_markdown_metrics_shm_zone = saved_zone;

    TEST_ASSERT(p != NULL, "renderer should succeed even without SHM zone");
    *p = '\0';
    TEST_ASSERT(!contains((char *) buf, "nginx_markdown_path_conversions_total{path="),
                "should not emit per-path series without SHM zone");

    TEST_PASS("NULL shm_zone skip correct");
}

static void
test_per_path_walk_no_path_entries(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;

    TEST_SUBSECTION("per-path walk with zero path_entries");

    memset(&s, 0, sizeof(s));
    s.per_path.path_entries = 0;
    s.per_path.overflow_count = 0;

    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + sizeof(buf), &s);

    TEST_ASSERT(p != NULL, "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(!contains((char *) buf, "nginx_markdown_path_conversions_total{path="),
                "should not emit per-path series when path_entries is zero");

    TEST_PASS("zero path_entries skip correct");
}

static void
test_per_path_walk_with_real_nodes(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;
    ngx_http_markdown_metrics_t live;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_path_metric_node_t node1;
    ngx_http_markdown_path_metric_node_t node2;
    u_char path1[] = "/api/v1";
    u_char path2[] = "/docs";

    TEST_SUBSECTION("per-path walk with real RB-tree nodes");

    memset(&s, 0, sizeof(s));
    memset(&live, 0, sizeof(live));
    memset(&shpool, 0, sizeof(shpool));
    memset(&node1, 0, sizeof(node1));
    memset(&node2, 0, sizeof(node2));

    s.per_path.path_entries = 2;
    s.per_path.overflow_count = 0;

    live.per_path.sentinel.left = &live.per_path.sentinel;
    live.per_path.sentinel.right = &live.per_path.sentinel;
    live.per_path.sentinel.parent = &live.per_path.sentinel;

    node1.path = path1;
    node1.path_len = sizeof(path1) - 1;
    node1.conversions = 100;
    node1.conversion_time_sum_ms = 5000;
    node1.rbnode.key = 1;
    node1.rbnode.left = &live.per_path.sentinel;
    node1.rbnode.right = &node2.rbnode;
    node1.rbnode.parent = &live.per_path.sentinel;

    node2.path = path2;
    node2.path_len = sizeof(path2) - 1;
    node2.conversions = 50;
    node2.conversion_time_sum_ms = 2000;
    node2.rbnode.key = 2;
    node2.rbnode.left = &live.per_path.sentinel;
    node2.rbnode.right = &live.per_path.sentinel;
    node2.rbnode.parent = &node1.rbnode;

    live.per_path.path_tree.root = &node1.rbnode;

    g_shm_zone.data = &live;
    g_shm_zone.shm.addr = &shpool;
    ngx_http_markdown_metrics_shm_zone = &g_shm_zone;

    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + sizeof(buf), &s);

    TEST_ASSERT(p != NULL, "renderer should succeed");
    *p = '\0';
    TEST_ASSERT(contains((char *) buf, "/api/v1"),
                "should contain first path");
    TEST_ASSERT(contains((char *) buf, "/docs"),
                "should contain second path");
    TEST_ASSERT(contains((char *) buf,
                "nginx_markdown_path_conversions_total{path=\""),
                "should contain per-path conversions series");

    TEST_PASS("real RB-tree walk correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("prometheus_per_path Tests\n");
    printf("========================================\n");

    test_escape_prometheus_label_basic();
    test_escape_prometheus_label_backslash();
    test_escape_prometheus_label_double_quote();
    test_escape_prometheus_label_newline();
    test_escape_prometheus_label_cr_and_tab();
    test_escape_prometheus_label_control_char();
    test_escape_prometheus_label_buffer_overflow();
    test_escape_prom_two_char_buffer_overflow();
    test_prometheus_walk_path_tree_single_node();
    test_prometheus_walk_path_tree_sentinel();
    test_prometheus_walk_path_tree_buffer_full();
    test_prometheus_path_pair_exact_budget();
    test_prometheus_omitted_counter_saturation();
    test_per_path_walk_with_overflow();
    test_per_path_response_budget_uses_other();
    test_per_path_walk_no_shm_zone();
    test_per_path_walk_no_path_entries();
    test_per_path_walk_with_real_nodes();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
