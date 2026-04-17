/*
 * Test: prometheus_renderer
 * Description: exercises the real ngx_http_markdown_prometheus_impl.h
 *              renderer to provide unit-test coverage for the production
 *              Prometheus text exposition code path.
 *
 * This test includes the actual impl header with minimal stubs so that
 * gcov/lcov instruments the production renderer function.
 */

#include "../include/test_common.h"

/* ── Minimal NGINX type stubs ─────────────────────────────────────── */

typedef unsigned char u_char;

typedef struct {
    size_t     len;
    u_char    *data;
} ngx_str_t;

typedef intptr_t  ngx_int_t;
typedef uintptr_t ngx_uint_t;
typedef int       ngx_atomic_t;
typedef unsigned long ngx_atomic_uint_t;

#define ngx_string(str) { sizeof(str) - 1, (u_char *) str }

/* ── Metrics snapshot type (mirrors production layout) ────────────── */

/*
 * DIVERGENCE RISK: This struct must remain bitwise/field-order compatible
 * with the canonical ngx_http_markdown_metrics_snapshot_t defined in
 * ngx_http_markdown_filter_module.h (via ngx_http_markdown_metrics_t).
 * Field names, types, and order must match exactly so that the offsets
 * used by ngx_http_markdown_metrics_write_prometheus() are correct.
 * Any change to the production struct requires updating this local copy.
 *
 * NOSONAR c:S1820 — field count mirrors production struct; grouping
 * into sub-structs would break ABI compatibility with the impl header.
 */
typedef struct { /* NOSONAR */
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
    ngx_atomic_t  conversion_latency_le_10ms;
    ngx_atomic_t  conversion_latency_le_100ms;
    ngx_atomic_t  conversion_latency_le_1000ms;
    ngx_atomic_t  conversion_latency_gt_1000ms;
    struct {
        ngx_atomic_t  attempted;
        ngx_atomic_t  succeeded;
        ngx_atomic_t  failed;
        ngx_atomic_t  gzip;
        ngx_atomic_t  deflate;
        ngx_atomic_t  brotli;
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
    } skips;
    ngx_atomic_t  failopen_count;
    ngx_atomic_t  estimated_token_savings;
} ngx_http_markdown_metrics_snapshot_t;

/* ── ngx_slprintf stub ────────────────────────────────────────────── */

/*
 * Minimal ngx_slprintf implementation using vsnprintf.
 *
 * The production ngx_slprintf uses NGINX's own format specifiers
 * (%uA for ngx_atomic_uint_t).  In this test stub we map %uA to
 * %d since ngx_atomic_t is int.
 *
 * NOSONAR c:S923 — variadic signature must match production ngx_slprintf.
 */
static u_char *
ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...) /* NOSONAR */
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

    /*
     * Rewrite NGINX format specifiers to standard printf ones:
     *   %uA  ->  %d   (ngx_atomic_uint_t printed as int)
     *   %03uA -> %03d
     */
    fi = 0;
    oi = 0;
    while (fmt[fi] != '\0' && oi < sizeof(local_fmt) - 4) {
        if (fmt[fi] == '%') {
            /* Copy the '%' */
            local_fmt[oi++] = fmt[fi++];
            /* Copy optional width/flags (digits, '0', '-', etc.) */
            while (fmt[fi] >= '0' && fmt[fi] <= '9') {
                local_fmt[oi++] = fmt[fi++];
            }
            /* Check for 'uA' */
            if (fmt[fi] == 'u' && fmt[fi + 1] == 'A') {
                local_fmt[oi++] = 'd';
                fi += 2;  /* skip 'uA' */
            } else {
                /* Not our special format; copy as-is */
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

/* Enable streaming metrics in the renderer */
#define MARKDOWN_STREAMING_ENABLED 1

/*
 * NOSONAR c:S954 — the impl header must follow type definitions and
 * stubs above; it cannot be moved to the top of the file.
 */
#include "../../src/ngx_http_markdown_prometheus_impl.h" /* NOSONAR */

/* ── Helpers ──────────────────────────────────────────────────────── */

static int
contains(const char *haystack, const char *needle)
{
    return strstr(haystack, needle) != NULL;
}

/* ── Tests ────────────────────────────────────────────────────────── */

/*
 * Test: zeroed snapshot produces valid Prometheus output with all
 * expected metric families and zero values.
 */
static void
test_zeroed_snapshot(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;

    TEST_SUBSECTION("Zeroed snapshot produces valid Prometheus output");

    memset(&s, 0, sizeof(s));
    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + sizeof(buf), &s);

    TEST_ASSERT(p != NULL, "renderer should not return NULL");
    TEST_ASSERT(p > buf, "renderer should produce output");
    TEST_ASSERT(p < buf + sizeof(buf),
        "renderer should not exhaust buffer");

    /* Null-terminate for string operations */
    *p = '\0';

    /* Core metric families */
    TEST_ASSERT(
        contains((char *) buf, "nginx_markdown_requests_total 0"),
        "requests_total should be 0");
    TEST_ASSERT(
        contains((char *) buf, "nginx_markdown_conversions_total 0"),
        "conversions_total should be 0");
    TEST_ASSERT(
        contains((char *) buf, "nginx_markdown_passthrough_total 0"),
        "passthrough_total should be 0");
    TEST_ASSERT(
        contains((char *) buf, "nginx_markdown_failopen_total 0"),
        "failopen_total should be 0");

    /* Streaming families */
    TEST_ASSERT(
        contains((char *) buf, "nginx_markdown_streaming_path_total 0"),
        "streaming_path_total should be 0");
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_total{result=\"success\"} 0"),
        "streaming success should be 0");
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_budget_exceeded_total 0"),
        "streaming budget exceeded should be 0");
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_ttfb_seconds 0.000"),
        "streaming TTFB should be 0.000");
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_peak_memory_bytes 0"),
        "streaming peak memory should be 0");

    TEST_PASS("Zeroed snapshot produces valid Prometheus output");
}


/*
 * Test: known-value snapshot renders correct metric values.
 */
static void
test_known_values(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;

    TEST_SUBSECTION("Known-value snapshot renders correctly");

    memset(&s, 0, sizeof(s));
    s.requests_entered = 200;
    s.conversions_succeeded = 150;
    s.conversions_bypassed = 30;
    s.failopen_count = 10;
    s.failures_conversion = 5;
    s.failures_resource_limit = 3;
    s.failures_system = 2;
    s.input_bytes = 1000000;
    s.output_bytes = 500000;
    s.estimated_token_savings = 25000;
    s.decompressions.gzip = 40;
    s.decompressions.deflate = 10;
    s.decompressions.brotli = 5;
    s.decompressions.failed = 2;
    s.conversion_latency_le_10ms = 80;
    s.conversion_latency_le_100ms = 50;
    s.conversion_latency_le_1000ms = 15;
    s.conversion_latency_gt_1000ms = 5;
    s.path_hits.incremental = 20;
    s.path_hits.streaming = 30;
    s.streaming.succeeded_total = 25;
    s.streaming.failed_total = 3;
    s.streaming.fallback_total = 2;
    s.streaming.precommit_failopen_total = 1;
    s.streaming.precommit_reject_total = 1;
    s.streaming.postcommit_error_total = 1;
    s.streaming.budget_exceeded_total = 2;
    s.streaming.shadow_total = 10;
    s.streaming.shadow_diff_total = 1;
    s.streaming.last_ttfb_ms = 1234;
    s.streaming.last_peak_memory_bytes = 65536;

    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + sizeof(buf), &s);

    TEST_ASSERT(p != NULL, "renderer should not return NULL");
    *p = '\0';

    TEST_ASSERT(
        contains((char *) buf, "nginx_markdown_requests_total 200"),
        "requests_total should be 200");
    TEST_ASSERT(
        contains((char *) buf, "nginx_markdown_conversions_total 150"),
        "conversions_total should be 150");
    /* passthrough = bypassed + failopen = 30 + 10 = 40 */
    TEST_ASSERT(
        contains((char *) buf, "nginx_markdown_passthrough_total 40"),
        "passthrough_total should be 40");
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_total{result=\"success\"} 25"),
        "streaming success should be 25");
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_budget_exceeded_total 2"),
        "streaming budget exceeded should be 2");
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_shadow_total 10"),
        "streaming shadow total should be 10");
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_shadow_diff_total 1"),
        "streaming shadow diff should be 1");
    /* TTFB: 1234ms = 1.234s */
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_ttfb_seconds 1.234"),
        "streaming TTFB should be 1.234");
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_streaming_peak_memory_bytes 65536"),
        "streaming peak memory should be 65536");
    /* Latency cumulative: le=0.01 -> 80, le=+Inf -> 80+50+15+5=150 */
    TEST_ASSERT(
        contains((char *) buf,
            "nginx_markdown_conversion_duration_seconds"
            "{le=\"+Inf\"} 150"),
        "latency le +Inf should be 150");

    TEST_PASS("Known-value snapshot renders correctly");
}


/*
 * Test: HELP and TYPE lines present for all metric families.
 */
static void
test_help_and_type_lines(void)
{
    u_char buf[16384];
    u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;

    static const char *families[] = {
        "nginx_markdown_requests_total",
        "nginx_markdown_conversions_total",
        "nginx_markdown_passthrough_total",
        "nginx_markdown_skips_total",
        "nginx_markdown_failures_total",
        "nginx_markdown_failopen_total",
        "nginx_markdown_large_response_path_total",
        "nginx_markdown_streaming_path_total",
        "nginx_markdown_streaming_total",
        "nginx_markdown_streaming_failures_total",
        "nginx_markdown_streaming_budget_exceeded_total",
        "nginx_markdown_streaming_shadow_total",
        "nginx_markdown_streaming_shadow_diff_total",
        "nginx_markdown_streaming_ttfb_seconds",
        "nginx_markdown_streaming_peak_memory_bytes",
        "nginx_markdown_input_bytes_total",
        "nginx_markdown_output_bytes_total",
        "nginx_markdown_estimated_token_savings_total",
        "nginx_markdown_decompressions_total",
        "nginx_markdown_decompression_failures_total",
        "nginx_markdown_conversion_duration_seconds",
    };

    TEST_SUBSECTION("HELP and TYPE lines for all families");

    memset(&s, 0, sizeof(s));
    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + sizeof(buf), &s);
    TEST_ASSERT(p != NULL, "renderer should succeed");
    *p = '\0';

    for (size_t i = 0; i < ARRAY_SIZE(families); i++) {
        char help_prefix[256];
        char type_prefix[256];

        snprintf(help_prefix, sizeof(help_prefix),
            "# HELP %s ", families[i]);
        snprintf(type_prefix, sizeof(type_prefix),
            "# TYPE %s ", families[i]);

        TEST_ASSERT(contains((char *) buf, help_prefix),
            "Missing HELP line for metric family");
        TEST_ASSERT(contains((char *) buf, type_prefix),
            "Missing TYPE line for metric family");
    }

    TEST_PASS("All metric families have HELP and TYPE lines");
}


/*
 * Test: buffer truncation returns NULL.
 */
static void
test_truncation_detection(void)
{
    u_char buf[64];  /* intentionally tiny */
    const u_char *p;
    ngx_http_markdown_metrics_snapshot_t s;

    TEST_SUBSECTION("Buffer truncation returns NULL");

    memset(&s, 0, sizeof(s));
    p = ngx_http_markdown_metrics_write_prometheus(
        buf, buf + sizeof(buf), &s);

    TEST_ASSERT(p == NULL,
        "truncated output should return NULL");

    TEST_PASS("Truncation detection works");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("prometheus_renderer Tests\n");
    printf("========================================\n");

    test_zeroed_snapshot();
    test_known_values();
    test_help_and_type_lines();
    test_truncation_detection();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
