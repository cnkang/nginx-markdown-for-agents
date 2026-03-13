/*
 * Test: metrics_output
 * Description: metrics output formatting
 */

#include "test_common.h"

typedef struct {
    unsigned long conversions_attempted;
    unsigned long conversions_succeeded;
    unsigned long conversions_failed;
    unsigned long conversions_bypassed;
    unsigned long failures_conversion;
    unsigned long failures_resource_limit;
    unsigned long failures_system;
    unsigned long conversion_time_sum_ms;
    unsigned long input_bytes;
    unsigned long output_bytes;
    unsigned long conversion_latency_le_10ms;
    unsigned long conversion_latency_le_100ms;
    unsigned long conversion_latency_le_1000ms;
    unsigned long conversion_latency_gt_1000ms;
    unsigned long decompressions_attempted;
    unsigned long decompressions_succeeded;
    unsigned long decompressions_failed;
    unsigned long decompressions_gzip;
    unsigned long decompressions_deflate;
    unsigned long decompressions_brotli;
} metrics_t;

static void
format_metrics_text(const metrics_t *m, char *out, size_t out_len)
{
    unsigned long completed = m->conversions_succeeded + m->conversions_failed;
    unsigned long avg_ms = completed == 0 ? 0 : m->conversion_time_sum_ms / completed;
    unsigned long avg_input = m->conversions_succeeded == 0 ? 0 : m->input_bytes / m->conversions_succeeded;
    unsigned long avg_output = m->conversions_succeeded == 0 ? 0 : m->output_bytes / m->conversions_succeeded;

    snprintf(out, out_len,
             "Markdown Filter Metrics\n"
             "=======================\n"
             "Conversions Attempted: %lu\n"
             "Conversions Succeeded: %lu\n"
             "Conversions Failed: %lu\n"
             "Conversions Bypassed: %lu\n"
             "Conversions Completed: %lu\n"
             "\n"
             "Failure Breakdown:\n"
             "- Conversion Errors: %lu\n"
             "- Resource Limit Exceeded: %lu\n"
             "- System Errors: %lu\n"
             "\n"
             "Performance:\n"
             "- Total Conversion Time: %lu ms\n"
             "- Average Conversion Time: %lu ms\n"
             "- Total Input Bytes: %lu\n"
             "- Average Input Bytes: %lu\n"
             "- Total Output Bytes: %lu\n"
             "- Average Output Bytes: %lu\n"
             "- Latency <= 10ms: %lu\n"
             "- Latency <= 100ms: %lu\n"
             "- Latency <= 1000ms: %lu\n"
             "- Latency > 1000ms: %lu\n"
             "\n"
             "Decompression Statistics:\n"
             "- Decompressions Attempted: %lu\n"
             "- Decompressions Succeeded: %lu\n"
             "- Decompressions Failed: %lu\n"
             "- Gzip Decompressions: %lu\n"
             "- Deflate Decompressions: %lu\n"
             "- Brotli Decompressions: %lu\n",
             m->conversions_attempted,
             m->conversions_succeeded,
             m->conversions_failed,
             m->conversions_bypassed,
             completed,
             m->failures_conversion,
             m->failures_resource_limit,
             m->failures_system,
             m->conversion_time_sum_ms,
             avg_ms,
             m->input_bytes,
             avg_input,
             m->output_bytes,
             avg_output,
             m->conversion_latency_le_10ms,
             m->conversion_latency_le_100ms,
             m->conversion_latency_le_1000ms,
             m->conversion_latency_gt_1000ms,
             m->decompressions_attempted,
             m->decompressions_succeeded,
             m->decompressions_failed,
             m->decompressions_gzip,
             m->decompressions_deflate,
             m->decompressions_brotli);
}

static void
format_metrics_json(const metrics_t *m, char *out, size_t out_len)
{
    unsigned long completed = m->conversions_succeeded + m->conversions_failed;
    unsigned long avg_ms = completed == 0 ? 0 : m->conversion_time_sum_ms / completed;
    unsigned long avg_input = m->conversions_succeeded == 0 ? 0 : m->input_bytes / m->conversions_succeeded;
    unsigned long avg_output = m->conversions_succeeded == 0 ? 0 : m->output_bytes / m->conversions_succeeded;

    snprintf(out, out_len,
             "{\"conversions_attempted\":%lu,\"conversions_succeeded\":%lu,\"conversions_failed\":%lu,"
             "\"conversions_bypassed\":%lu,\"conversion_completed\":%lu,"
             "\"failures_conversion\":%lu,\"failures_resource_limit\":%lu,\"failures_system\":%lu,"
             "\"conversion_time_sum_ms\":%lu,\"conversion_time_avg_ms\":%lu,"
             "\"input_bytes\":%lu,\"input_bytes_avg\":%lu,"
             "\"output_bytes\":%lu,\"output_bytes_avg\":%lu,"
             "\"conversion_latency_buckets\":{\"le_10ms\":%lu,\"le_100ms\":%lu,\"le_1000ms\":%lu,\"gt_1000ms\":%lu},"
             "\"decompressions_attempted\":%lu,\"decompressions_succeeded\":%lu,\"decompressions_failed\":%lu,"
             "\"decompressions_gzip\":%lu,\"decompressions_deflate\":%lu,\"decompressions_brotli\":%lu}",
             m->conversions_attempted,
             m->conversions_succeeded,
             m->conversions_failed,
             m->conversions_bypassed,
             completed,
             m->failures_conversion,
             m->failures_resource_limit,
             m->failures_system,
             m->conversion_time_sum_ms,
             avg_ms,
             m->input_bytes,
             avg_input,
             m->output_bytes,
             avg_output,
             m->conversion_latency_le_10ms,
             m->conversion_latency_le_100ms,
             m->conversion_latency_le_1000ms,
             m->conversion_latency_gt_1000ms,
             m->decompressions_attempted,
             m->decompressions_succeeded,
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
    m.conversions_bypassed = 7;
    m.failures_conversion = 11;
    m.failures_resource_limit = 6;
    m.failures_system = 3;
    m.conversion_time_sum_ms = 5500;
    m.input_bytes = 80000;
    m.output_bytes = 32000;
    m.conversion_latency_le_10ms = 40;
    m.conversion_latency_le_100ms = 45;
    m.conversion_latency_le_1000ms = 10;
    m.conversion_latency_gt_1000ms = 5;
    m.decompressions_attempted = 30;
    m.decompressions_succeeded = 27;
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
    TEST_SUBSECTION("Human-readable text format output");
    format_metrics_text(&m, out, sizeof(out));
    TEST_ASSERT(strstr(out, "Conversions Attempted: 100") != NULL, "text output should include conversions_attempted");
    TEST_ASSERT(strstr(out, "Conversions Completed: 100") != NULL, "text output should include completed count");
    TEST_ASSERT(strstr(out, "Average Conversion Time: 55 ms") != NULL, "text output should include average conversion time");
    TEST_ASSERT(strstr(out, "Latency <= 100ms: 45") != NULL, "text output should include latency buckets");
    TEST_ASSERT(strstr(out, "Gzip Decompressions: 18") != NULL, "text output should include gzip counter");
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
    TEST_ASSERT(strstr(out, "\"conversion_completed\":100") != NULL, "JSON should include completed count");
    TEST_ASSERT(strstr(out, "\"conversion_latency_buckets\":{") != NULL, "JSON should include latency buckets");
    TEST_ASSERT(strstr(out, "\"decompressions_deflate\":9") != NULL, "JSON should include deflate counter");
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
