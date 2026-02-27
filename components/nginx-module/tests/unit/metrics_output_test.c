/*
 * Test: metrics_output
 * Description: metrics output formatting
 */

#include "test_common.h"

typedef struct {
    unsigned long conversions_attempted;
    unsigned long conversions_succeeded;
    unsigned long conversions_failed;
    unsigned long decompressions_attempted;
    unsigned long decompressions_failed;
    unsigned long decompressions_gzip;
    unsigned long decompressions_deflate;
    unsigned long decompressions_brotli;
} metrics_t;

static void
format_metrics_text(const metrics_t *m, char *out, size_t out_len)
{
    snprintf(out, out_len,
             "markdown_conversions_attempted_total %lu\n"
             "markdown_conversions_succeeded_total %lu\n"
             "markdown_conversions_failed_total %lu\n"
             "markdown_decompressions_attempted_total %lu\n"
             "markdown_decompressions_failed_total %lu\n"
             "markdown_decompressions_by_type{type=\"gzip\"} %lu\n"
             "markdown_decompressions_by_type{type=\"deflate\"} %lu\n"
             "markdown_decompressions_by_type{type=\"brotli\"} %lu\n",
             m->conversions_attempted,
             m->conversions_succeeded,
             m->conversions_failed,
             m->decompressions_attempted,
             m->decompressions_failed,
             m->decompressions_gzip,
             m->decompressions_deflate,
             m->decompressions_brotli);
}

static void
format_metrics_json(const metrics_t *m, char *out, size_t out_len)
{
    snprintf(out, out_len,
             "{\"conversions_attempted\":%lu,\"conversions_succeeded\":%lu,\"conversions_failed\":%lu,"
             "\"decompressions_attempted\":%lu,\"decompressions_failed\":%lu,"
             "\"decompressions_by_type\":{\"gzip\":%lu,\"deflate\":%lu,\"brotli\":%lu}}",
             m->conversions_attempted,
             m->conversions_succeeded,
             m->conversions_failed,
             m->decompressions_attempted,
             m->decompressions_failed,
             m->decompressions_gzip,
             m->decompressions_deflate,
             m->decompressions_brotli);
}

static metrics_t
sample(void)
{
    metrics_t m;
    m.conversions_attempted = 100;
    m.conversions_succeeded = 80;
    m.conversions_failed = 20;
    m.decompressions_attempted = 30;
    m.decompressions_failed = 3;
    m.decompressions_gzip = 18;
    m.decompressions_deflate = 9;
    m.decompressions_brotli = 3;
    return m;
}

static void
test_text_format(void)
{
    char out[1024];
    metrics_t m = sample();
    TEST_SUBSECTION("Prometheus text format output");
    format_metrics_text(&m, out, sizeof(out));
    TEST_ASSERT(strstr(out, "markdown_conversions_attempted_total 100") != NULL, "text output should include conversions_attempted");
    TEST_ASSERT(strstr(out, "markdown_decompressions_by_type{type=\"gzip\"} 18") != NULL, "text output should include gzip counter");
    TEST_ASSERT(strstr(out, "markdown_decompressions_by_type{type=\"brotli\"} 3") != NULL, "text output should include brotli counter");
    TEST_PASS("Text format is correct");
}

static void
test_json_format(void)
{
    char out[1024];
    metrics_t m = sample();
    TEST_SUBSECTION("JSON format output");
    format_metrics_json(&m, out, sizeof(out));
    TEST_ASSERT(strstr(out, "\"conversions_attempted\":100") != NULL, "JSON should include conversions_attempted");
    TEST_ASSERT(strstr(out, "\"decompressions_by_type\":{") != NULL, "JSON should include nested type object");
    TEST_ASSERT(strstr(out, "\"deflate\":9") != NULL, "JSON should include deflate counter");
    TEST_PASS("JSON format is correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_output Tests\n");
    printf("========================================\n");

    test_text_format();
    test_json_format();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
