/*
 * Test: metrics_endpoint
 * Description: metrics endpoint
 */

#include "test_common.h"

typedef struct {
    unsigned long conversions_attempted;
    unsigned long conversions_succeeded;
    unsigned long conversions_failed;
    unsigned long conversion_time_sum_ms;
    unsigned long conversion_latency_le_10ms;
    unsigned long conversion_latency_le_100ms;
    unsigned long conversion_latency_le_1000ms;
    unsigned long conversion_latency_gt_1000ms;
    unsigned long decompressions_attempted;
    unsigned long decompressions_succeeded;
} metrics_t;

typedef struct {
    int status;
    char body[1024];
} endpoint_response_t;

static int
is_localhost(const char *addr)
{
    return STR_EQ(addr, "127.0.0.1") || STR_EQ(addr, "::1") || STR_EQ(addr, "localhost");
}

static endpoint_response_t
handle_metrics_request(const char *method, const char *remote_addr, const char *format, const metrics_t *m)
{
    endpoint_response_t out;
    unsigned long completed;
    unsigned long avg_ms;
    memset(&out, 0, sizeof(out));

    if (!(STR_EQ(method, "GET") || STR_EQ(method, "HEAD"))) {
        out.status = 405;
        return out;
    }
    if (!is_localhost(remote_addr)) {
        out.status = 403;
        return out;
    }

    out.status = 200;
    completed = m->conversions_succeeded + m->conversions_failed;
    avg_ms = completed == 0 ? 0 : m->conversion_time_sum_ms / completed;
    if (STR_EQ(format, "json")) {
        snprintf(out.body, sizeof(out.body),
                 "{\"conversions_attempted\":%lu,\"conversions_succeeded\":%lu,\"conversions_failed\":%lu,"
                 "\"conversion_completed\":%lu,\"conversion_time_avg_ms\":%lu,"
                 "\"conversion_latency_buckets\":{\"le_10ms\":%lu,\"le_100ms\":%lu,\"le_1000ms\":%lu,\"gt_1000ms\":%lu},"
                 "\"decompressions_attempted\":%lu,\"decompressions_succeeded\":%lu}",
                 m->conversions_attempted, m->conversions_succeeded, m->conversions_failed,
                 completed, avg_ms,
                 m->conversion_latency_le_10ms, m->conversion_latency_le_100ms,
                 m->conversion_latency_le_1000ms, m->conversion_latency_gt_1000ms,
                 m->decompressions_attempted, m->decompressions_succeeded);
    } else {
        snprintf(out.body, sizeof(out.body),
                 "Markdown Filter Metrics\n"
                 "=======================\n"
                 "Conversions Attempted: %lu\n"
                 "Conversions Succeeded: %lu\n"
                 "Conversions Failed: %lu\n"
                 "Conversions Completed: %lu\n"
                 "Average Conversion Time: %lu ms\n"
                 "Latency <= 10ms: %lu\n"
                 "Latency <= 100ms: %lu\n"
                 "Latency <= 1000ms: %lu\n"
                 "Latency > 1000ms: %lu\n"
                 "Decompressions Attempted: %lu\n"
                 "Decompressions Succeeded: %lu\n",
                 m->conversions_attempted, m->conversions_succeeded, m->conversions_failed,
                 completed, avg_ms,
                 m->conversion_latency_le_10ms, m->conversion_latency_le_100ms,
                 m->conversion_latency_le_1000ms, m->conversion_latency_gt_1000ms,
                 m->decompressions_attempted, m->decompressions_succeeded);
    }
    return out;
}

static metrics_t
sample_metrics(void)
{
    metrics_t m;
    m.conversions_attempted = 10;
    m.conversions_succeeded = 8;
    m.conversions_failed = 2;
    m.conversion_time_sum_ms = 100;
    m.conversion_latency_le_10ms = 4;
    m.conversion_latency_le_100ms = 5;
    m.conversion_latency_le_1000ms = 1;
    m.conversion_latency_gt_1000ms = 0;
    m.decompressions_attempted = 4;
    m.decompressions_succeeded = 3;
    return m;
}

static void
test_access_restrictions(void)
{
    endpoint_response_t r;
    metrics_t m = sample_metrics();
    char remote_addr[16];
    int written;

    TEST_SUBSECTION("Method and localhost restrictions");

    r = handle_metrics_request("POST", "127.0.0.1", "text", &m);
    TEST_ASSERT(r.status == 405, "Only GET/HEAD should be allowed");

    written = snprintf(remote_addr, sizeof(remote_addr), "%u.%u.%u.%u",
                       10U, 0U, 0U, 5U);
    TEST_ASSERT(written > 0 && (size_t) written < sizeof(remote_addr),
                "failed to build remote address");
    r = handle_metrics_request("GET", remote_addr, "text", &m);
    TEST_ASSERT(r.status == 403, "Non-localhost access should be forbidden");

    r = handle_metrics_request("GET", "127.0.0.1", "text", &m);
    TEST_ASSERT(r.status == 200, "Localhost GET should be allowed");
    TEST_PASS("Access restrictions work");
}

static void
test_output_formats(void)
{
    endpoint_response_t r;
    metrics_t m = sample_metrics();

    TEST_SUBSECTION("Plain text and JSON output formats");

    r = handle_metrics_request("GET", "::1", "text", &m);
    TEST_ASSERT(strstr(r.body, "Conversions Attempted: 10") != NULL,
                "Plain text output should include counters");
    TEST_ASSERT(strstr(r.body, "Average Conversion Time: 10 ms") != NULL,
                "Plain text output should include averages");

    r = handle_metrics_request("GET", "::1", "json", &m);
    TEST_ASSERT(strstr(r.body, "\"conversions_attempted\":10") != NULL,
                "JSON output should include counters");
    TEST_ASSERT(strstr(r.body, "\"conversion_latency_buckets\":{") != NULL,
                "JSON output should include latency buckets");
    TEST_ASSERT(strstr(r.body, "\"decompressions_succeeded\":3") != NULL,
                "JSON output should include decompression counters");
    TEST_PASS("Output formats work");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_endpoint Tests\n");
    printf("========================================\n");

    test_access_restrictions();
    test_output_formats();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
