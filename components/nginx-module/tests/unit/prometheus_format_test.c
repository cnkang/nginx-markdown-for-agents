/*
 * Test: prometheus_format
 * Description: Prometheus text exposition format renderer
 *
 * Uses a standalone test pattern (no NGINX dependencies).
 * Mirrors the renderer logic using snprintf() and unsigned long.
 */

#include "test_common.h"
#include "test_metrics_snapshot.h"

/* Re-export the shared type under the local name used throughout */
typedef test_metrics_snapshot_t  snapshot_t;

/* ------------------------------------------------------------------ */
/* Local Prometheus renderer mirroring the NGINX implementation        */
/* ------------------------------------------------------------------ */

static int
format_prometheus(const snapshot_t *s, char *buf, size_t buf_len)
{
    int n;
    int total = 0;
    char *p = buf;
    size_t rem = buf_len;

#define PROM_WRITE(...)                                     \
    do {                                                    \
        n = snprintf(p, rem, __VA_ARGS__);                  \
        if (n < 0) return -1;                               \
        if ((size_t)n >= rem) { rem = 0; p = buf + buf_len; } \
        else { p += n; rem -= (size_t)n; }                  \
        total += n;                                         \
    } while (0)

    /* requests_total */
    PROM_WRITE(
        "# HELP nginx_markdown_requests_total "
        "Total requests entering the module "
        "decision chain.\n"
        "# TYPE nginx_markdown_requests_total counter\n"
        "nginx_markdown_requests_total %lu\n"
        "\n",
        s->requests_entered);

    /* conversions_total */
    PROM_WRITE(
        "# HELP nginx_markdown_conversions_total "
        "Successful HTML-to-Markdown conversions.\n"
        "# TYPE nginx_markdown_conversions_total counter\n"
        "nginx_markdown_conversions_total %lu\n"
        "\n",
        s->conversions_succeeded);

    /* passthrough_total (derived: skips + fail-open) */
    PROM_WRITE(
        "# HELP nginx_markdown_passthrough_total "
        "Requests not converted "
        "(skipped or failed-open).\n"
        "# TYPE nginx_markdown_passthrough_total "
        "counter\n"
        "nginx_markdown_passthrough_total %lu\n"
        "\n",
        s->conversions_bypassed + s->failopen_count);

    /* skips_total{reason=...} */
    PROM_WRITE(
        "# HELP nginx_markdown_skips_total "
        "Requests skipped by reason.\n"
        "# TYPE nginx_markdown_skips_total counter\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_METHOD\"} %lu\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_STATUS\"} %lu\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_CONTENT_TYPE\"} %lu\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_SIZE\"} %lu\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_STREAMING\"} %lu\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_AUTH\"} %lu\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_RANGE\"} %lu\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_ACCEPT\"} %lu\n"
        "nginx_markdown_skips_total"
        "{reason=\"SKIP_CONFIG\"} %lu\n"
        "\n",
        s->skips.method,
        s->skips.status,
        s->skips.content_type,
        s->skips.size,
        s->skips.streaming,
        s->skips.auth,
        s->skips.range,
        s->skips.accept,
        s->skips.config);

    /* failures_total{stage=...} */
    PROM_WRITE(
        "# HELP nginx_markdown_failures_total "
        "Conversion failures by stage.\n"
        "# TYPE nginx_markdown_failures_total counter\n"
        "nginx_markdown_failures_total"
        "{stage=\"FAIL_CONVERSION\"} %lu\n"
        "nginx_markdown_failures_total"
        "{stage=\"FAIL_RESOURCE_LIMIT\"} %lu\n"
        "nginx_markdown_failures_total"
        "{stage=\"FAIL_SYSTEM\"} %lu\n"
        "\n",
        s->failures_conversion,
        s->failures_resource_limit,
        s->failures_system);

    /* failopen_total */
    PROM_WRITE(
        "# HELP nginx_markdown_failopen_total "
        "Conversions failed with original HTML served "
        "(fail-open).\n"
        "# TYPE nginx_markdown_failopen_total counter\n"
        "nginx_markdown_failopen_total %lu\n"
        "\n",
        s->failopen_count);

    /* large_response_path_total */
    PROM_WRITE(
        "# HELP nginx_markdown_large_response_path_total "
        "Requests routed to incremental "
        "processing path.\n"
        "# TYPE "
        "nginx_markdown_large_response_path_total counter\n"
        "nginx_markdown_large_response_path_total %lu\n"
        "\n",
        s->path_hits.incremental);

    /* input_bytes_total */
    PROM_WRITE(
        "# HELP nginx_markdown_input_bytes_total "
        "Cumulative HTML input bytes from "
        "successful conversions.\n"
        "# TYPE nginx_markdown_input_bytes_total counter\n"
        "nginx_markdown_input_bytes_total %lu\n"
        "\n",
        s->input_bytes);

    /* output_bytes_total */
    PROM_WRITE(
        "# HELP nginx_markdown_output_bytes_total "
        "Cumulative Markdown output bytes from "
        "successful conversions.\n"
        "# TYPE nginx_markdown_output_bytes_total counter\n"
        "nginx_markdown_output_bytes_total %lu\n"
        "\n",
        s->output_bytes);

    /* estimated_token_savings_total */
    PROM_WRITE(
        "# HELP "
        "nginx_markdown_estimated_token_savings_total "
        "Estimated cumulative token savings "
        "(requires markdown_token_estimate on).\n"
        "# TYPE "
        "nginx_markdown_estimated_token_savings_total "
        "counter\n"
        "nginx_markdown_estimated_token_savings_total"
        " %lu\n"
        "\n",
        s->estimated_token_savings);

    /* decompressions_total{format=...} */
    PROM_WRITE(
        "# HELP nginx_markdown_decompressions_total "
        "Decompression operations by format.\n"
        "# TYPE nginx_markdown_decompressions_total "
        "counter\n"
        "nginx_markdown_decompressions_total"
        "{format=\"gzip\"} %lu\n"
        "nginx_markdown_decompressions_total"
        "{format=\"deflate\"} %lu\n"
        "nginx_markdown_decompressions_total"
        "{format=\"brotli\"} %lu\n"
        "\n",
        s->decompressions.gzip,
        s->decompressions.deflate,
        s->decompressions.brotli);

    /* decompression_failures_total */
    PROM_WRITE(
        "# HELP "
        "nginx_markdown_decompression_failures_total "
        "Failed decompression attempts.\n"
        "# TYPE "
        "nginx_markdown_decompression_failures_total "
        "counter\n"
        "nginx_markdown_decompression_failures_total"
        " %lu\n"
        "\n",
        s->decompressions.failed);

    /* conversion_duration_seconds{le=...} (cumulative) */
    PROM_WRITE(
        "# HELP "
        "nginx_markdown_conversion_duration_seconds "
        "Cumulative conversion count per latency bucket "
        "(not a native Prometheus histogram; "
        "no _sum/_count).\n"
        "# TYPE "
        "nginx_markdown_conversion_duration_seconds gauge\n"
        "nginx_markdown_conversion_duration_seconds"
        "{le=\"0.01\"} %lu\n"
        "nginx_markdown_conversion_duration_seconds"
        "{le=\"0.1\"} %lu\n"
        "nginx_markdown_conversion_duration_seconds"
        "{le=\"1.0\"} %lu\n"
        "nginx_markdown_conversion_duration_seconds"
        "{le=\"+Inf\"} %lu\n",
        s->conversion_latency.le_10ms,
        s->conversion_latency.le_10ms
            + s->conversion_latency.le_100ms,
        s->conversion_latency.le_10ms
            + s->conversion_latency.le_100ms
            + s->conversion_latency.le_1000ms,
        s->conversion_latency.le_10ms
            + s->conversion_latency.le_100ms
            + s->conversion_latency.le_1000ms
            + s->conversion_latency.gt_1000ms);

#undef PROM_WRITE

    return total;
}

/* ------------------------------------------------------------------ */
/* Metric family names (13 families)                                   */
/* ------------------------------------------------------------------ */

static const char *metric_families[] = {
    "nginx_markdown_requests_total",
    "nginx_markdown_conversions_total",
    "nginx_markdown_passthrough_total",
    "nginx_markdown_skips_total",
    "nginx_markdown_failures_total",
    "nginx_markdown_failopen_total",
    "nginx_markdown_large_response_path_total",
    "nginx_markdown_input_bytes_total",
    "nginx_markdown_output_bytes_total",
    "nginx_markdown_estimated_token_savings_total",
    "nginx_markdown_decompressions_total",
    "nginx_markdown_decompression_failures_total",
    "nginx_markdown_conversion_duration_seconds"
};

#define NUM_FAMILIES 13

/* ------------------------------------------------------------------ */
/* Reason, stage, and format label value sets                          */
/* ------------------------------------------------------------------ */

static const char *reason_values[] = {
    "SKIP_METHOD", "SKIP_STATUS", "SKIP_CONTENT_TYPE",
    "SKIP_SIZE", "SKIP_STREAMING", "SKIP_AUTH",
    "SKIP_RANGE", "SKIP_ACCEPT", "SKIP_CONFIG"
};
#define NUM_REASONS 9

static const char *stage_values[] = {
    "FAIL_CONVERSION", "FAIL_RESOURCE_LIMIT",
    "FAIL_SYSTEM"
};
#define NUM_STAGES 3

static const char *format_values[] = {
    "gzip", "deflate", "brotli"
};
#define NUM_FORMATS 3

/* ------------------------------------------------------------------ */
/* Helper: check if a string contains a substring                      */
/* ------------------------------------------------------------------ */

static int
contains(const char *haystack, const char *needle)
{
    return strstr(haystack, needle) != NULL;
}

/* ------------------------------------------------------------------ */
/* Test: zeroed snapshot produces valid output with zero values         */
/* ------------------------------------------------------------------ */

static void
test_zeroed_snapshot(void)
{
    char buf[8192];
    snapshot_t s;

    TEST_SUBSECTION("Zeroed snapshot produces valid "
                    "Prometheus format");

    memset(&s, 0, sizeof(s));
    format_prometheus(&s, buf, sizeof(buf));

    /* Every metric value line should have " 0" */
    TEST_ASSERT(
        contains(buf, "nginx_markdown_requests_total 0"),
        "requests_total should be 0");
    TEST_ASSERT(
        contains(buf, "nginx_markdown_conversions_total 0"),
        "conversions_total should be 0");
    TEST_ASSERT(
        contains(buf, "nginx_markdown_passthrough_total 0"),
        "passthrough_total should be 0");
    TEST_ASSERT(
        contains(buf, "nginx_markdown_failopen_total 0"),
        "failopen_total should be 0");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_estimated_token_savings_total 0"),
        "estimated_token_savings_total should be 0");

    TEST_PASS("Zeroed snapshot produces valid output");
}

/* ------------------------------------------------------------------ */
/* Test: known-value snapshot matches expected text                     */
/* ------------------------------------------------------------------ */

static void
test_known_values(void)
{
    char buf[8192];
    snapshot_t s;

    TEST_SUBSECTION("Known-value snapshot matches expected "
                    "Prometheus text");

    memset(&s, 0, sizeof(s));
    s.requests_entered = 100;
    s.conversions_succeeded = 80;
    s.conversions_bypassed = 15;
    s.skips.method = 3;
    s.skips.status = 2;
    s.skips.content_type = 5;
    s.skips.accept = 1;
    s.failures_conversion = 4;
    s.failures_resource_limit = 1;
    s.failopen_count = 3;
    s.path_hits.incremental = 7;
    s.input_bytes = 500000;
    s.output_bytes = 250000;
    s.estimated_token_savings = 12000;
    s.decompressions.gzip = 20;
    s.decompressions.deflate = 5;
    s.decompressions.brotli = 3;
    s.decompressions.failed = 1;
    s.conversion_latency.le_10ms = 40;
    s.conversion_latency.le_100ms = 30;
    s.conversion_latency.le_1000ms = 8;
    s.conversion_latency.gt_1000ms = 2;

    format_prometheus(&s, buf, sizeof(buf));

    TEST_ASSERT(
        contains(buf, "nginx_markdown_requests_total 100"),
        "requests_total should be 100");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_conversions_total 80"),
        "conversions_total should be 80");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_passthrough_total 18"),
        "passthrough_total should be 15+3=18");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_skips_total"
            "{reason=\"SKIP_METHOD\"} 3"),
        "skips method should be 3");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_failures_total"
            "{stage=\"FAIL_CONVERSION\"} 4"),
        "failures conversion should be 4");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_failopen_total 3"),
        "failopen_total should be 3");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_input_bytes_total 500000"),
        "input_bytes_total should be 500000");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_decompressions_total"
            "{format=\"gzip\"} 20"),
        "decompressions gzip should be 20");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_conversion_duration_seconds"
            "{le=\"0.01\"} 40"),
        "latency le 0.01 should be 40");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_conversion_duration_seconds"
            "{le=\"+Inf\"} 80"),
        "latency le +Inf should be 40+30+8+2=80");

    TEST_PASS("Known-value snapshot matches expected text");
}

/* ------------------------------------------------------------------ */
/* Test: every metric family has HELP and TYPE lines                    */
/* ------------------------------------------------------------------ */

static void
test_help_and_type_lines(void)
{
    char buf[8192];
    char help_prefix[128];
    char type_prefix[128];
    snapshot_t s;

    TEST_SUBSECTION("Every metric family has HELP and "
                    "TYPE lines");

    memset(&s, 0, sizeof(s));
    format_prometheus(&s, buf, sizeof(buf));

    for (size_t i = 0; i < NUM_FAMILIES; i++) {
        snprintf(help_prefix, sizeof(help_prefix),
                 "# HELP %s ", metric_families[i]);
        snprintf(type_prefix, sizeof(type_prefix),
                 "# TYPE %s ", metric_families[i]);

        TEST_ASSERT(contains(buf, help_prefix),
                    "Missing HELP line for metric family");
        TEST_ASSERT(contains(buf, type_prefix),
                    "Missing TYPE line for metric family");
    }

    TEST_PASS("All 13 metric families have HELP and "
              "TYPE lines");
}

/* ------------------------------------------------------------------ */
/* Test: counter metrics end with _total                               */
/* ------------------------------------------------------------------ */

static void
test_counter_suffix(void)
{
    char buf[8192];
    char type_line[128];
    snapshot_t s;

    TEST_SUBSECTION("Counter metrics end with _total");

    memset(&s, 0, sizeof(s));
    format_prometheus(&s, buf, sizeof(buf));

    for (size_t i = 0; i < NUM_FAMILIES; i++) {
        snprintf(type_line, sizeof(type_line),
                 "# TYPE %s counter", metric_families[i]);
        if (contains(buf, type_line)) {
            /*
             * Safe: metric_families[] entries are
             * compile-time string literals.
             */
            size_t name_len = strlen(metric_families[i]);
            TEST_ASSERT(
                name_len >= 6
                && strcmp(metric_families[i] + name_len - 6,
                          "_total") == 0,
                "Counter metric must end with _total");
        }
    }

    TEST_PASS("All counter metrics end with _total");
}

/* ------------------------------------------------------------------ */
/* Test: label values match defined sets                                */
/* ------------------------------------------------------------------ */

static void
test_label_values(void)
{
    char buf[8192];
    char label_str[128];
    snapshot_t s;

    TEST_SUBSECTION("Label values match defined sets");

    memset(&s, 0, sizeof(s));
    format_prometheus(&s, buf, sizeof(buf));

    /* Check all reason labels present */
    for (size_t i = 0; i < NUM_REASONS; i++) {
        snprintf(label_str, sizeof(label_str),
                 "reason=\"%s\"", reason_values[i]);
        TEST_ASSERT(contains(buf, label_str),
                    "Missing reason label value");
    }

    /* Check all stage labels present */
    for (size_t i = 0; i < NUM_STAGES; i++) {
        snprintf(label_str, sizeof(label_str),
                 "stage=\"%s\"", stage_values[i]);
        TEST_ASSERT(contains(buf, label_str),
                    "Missing stage label value");
    }

    /* Check all format labels present */
    for (size_t i = 0; i < NUM_FORMATS; i++) {
        snprintf(label_str, sizeof(label_str),
                 "format=\"%s\"", format_values[i]);
        TEST_ASSERT(contains(buf, label_str),
                    "Missing format label value");
    }

    TEST_PASS("All label values match defined sets");
}

/* ------------------------------------------------------------------ */
/* Test: estimated_token_savings_total HELP contains "estimate"        */
/* ------------------------------------------------------------------ */

static void
test_token_savings_help_text(void)
{
    char buf[8192];
    const char *help_start;
    char *help_end;
    snapshot_t s;

    TEST_SUBSECTION("estimated_token_savings_total HELP "
                    "contains estimate");

    memset(&s, 0, sizeof(s));
    format_prometheus(&s, buf, sizeof(buf));

    help_start = strstr(buf,
        "# HELP "
        "nginx_markdown_estimated_token_savings_total");
    TEST_ASSERT(help_start != NULL,
                "HELP line for estimated_token_savings_total "
                "must exist");

    /*
     * Find end of this HELP line.  Derive the mutable pointer
     * from buf so the temporary null-termination below is safe.
     */
    help_end = buf + (help_start - buf);
    help_end = strchr(help_end, '\n');
    TEST_ASSERT(help_end != NULL,
                "HELP line must end with newline");

    /* Temporarily null-terminate the line for search */
    *help_end = '\0';
    TEST_ASSERT(
        strstr(help_start, "stimate") != NULL,
        "HELP text must contain the word estimate");
    *help_end = '\n';

    TEST_PASS("HELP text contains estimate");
}

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */

int
main(void)
{
    printf("\n========================================\n");
    printf("prometheus_format Tests\n");
    printf("========================================\n");

    test_zeroed_snapshot();
    test_known_values();
    test_help_and_type_lines();
    test_counter_suffix();
    test_label_values();
    test_token_savings_help_text();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
