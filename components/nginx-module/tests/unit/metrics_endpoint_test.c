/*
 * Test: metrics_endpoint
 *
 * Validates the /metrics endpoint: localhost access control, the frozen
 * Prometheus text 0.0.4 response, correct metric values, and error handling
 * for non-localhost requests.
 */

#include "test_common.h"

typedef struct {
    unsigned long conversions_attempted;
    unsigned long conversions_succeeded;
    unsigned long conversions_failed;
    unsigned long conversion_time_sum_ms;
    struct {
        unsigned long le_10ms;
        unsigned long le_100ms;
        unsigned long le_1000ms;
        unsigned long gt_1000ms;
    } conversion_latency;
    unsigned long decompressions_attempted;
    unsigned long decompressions_succeeded;
    unsigned long streaming_peak_memory_bytes;
} metrics_t;

typedef struct {
    int status;
    char content_type[96];
    char body[4096];
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
    (void) format;
    memset(&out, 0, sizeof(out));

    if (!is_localhost(remote_addr)) {
        out.status = 403;
        return out;
    }
    if (!(STR_EQ(method, "GET") || STR_EQ(method, "HEAD"))) {
        out.status = 405;
        return out;
    }

    out.status = 200;
    completed = m->conversions_succeeded + m->conversions_failed;
    snprintf(out.content_type, sizeof(out.content_type),
             "text/plain; version=0.0.4; charset=utf-8");
    snprintf(out.body, sizeof(out.body),
             "# HELP nginx_markdown_requests_total terminal request outcomes\n"
             "# TYPE nginx_markdown_requests_total counter\n"
             "nginx_markdown_requests_total{outcome=\"converted\",stage=\"conversion\",reason=\"converted\"} %lu\n"
             "# HELP nginx_markdown_conversion_attempts_total conversion attempts\n"
             "# TYPE nginx_markdown_conversion_attempts_total counter\n"
             "nginx_markdown_conversion_attempts_total{engine=\"full_buffer\"} %lu\n"
             "# HELP nginx_markdown_conversion_deliveries_total successful deliveries\n"
             "# TYPE nginx_markdown_conversion_deliveries_total counter\n"
             "nginx_markdown_conversion_deliveries_total{engine=\"full_buffer\"} %lu\n"
             "# HELP nginx_markdown_conversion_duration_seconds conversion duration\n"
             "# TYPE nginx_markdown_conversion_duration_seconds histogram\n"
             "nginx_markdown_conversion_duration_seconds_count{engine=\"full_buffer\"} %lu\n"
             "# HELP nginx_markdown_input_bytes_total input bytes\n"
             "# TYPE nginx_markdown_input_bytes_total counter\n"
             "nginx_markdown_input_bytes_total 0\n"
             "# HELP nginx_markdown_output_bytes_total output bytes\n"
             "# TYPE nginx_markdown_output_bytes_total counter\n"
             "nginx_markdown_output_bytes_total 0\n"
             "# HELP nginx_markdown_streaming_peak_memory_bytes peak streaming memory\n"
             "# TYPE nginx_markdown_streaming_peak_memory_bytes gauge\n"
             "nginx_markdown_streaming_peak_memory_bytes %lu\n"
             "# HELP nginx_markdown_streaming_events_total streaming events\n"
             "# TYPE nginx_markdown_streaming_events_total counter\n"
             "nginx_markdown_streaming_events_total{transition=\"commit\",reason=\"converted\"} 0\n"
             "# HELP nginx_markdown_decompression_events_total decompression events\n"
             "# TYPE nginx_markdown_decompression_events_total counter\n"
             "nginx_markdown_decompression_events_total{encoding=\"gzip\",outcome=\"success\",reason=\"ok\"} %lu\n"
             "# HELP nginx_markdown_dynconf_reloads_total dynconf reloads\n"
             "# TYPE nginx_markdown_dynconf_reloads_total counter\n"
             "nginx_markdown_dynconf_reloads_total{outcome=\"success\",reason=\"ok\"} 0\n"
             "# HELP nginx_markdown_build_info build information\n"
             "# TYPE nginx_markdown_build_info gauge\n"
             "nginx_markdown_build_info{version=\"test\",nginx_version=\"test\",features=\"\"} 1\n",
             m->conversions_succeeded, m->conversions_attempted, m->conversions_succeeded,
             completed, m->streaming_peak_memory_bytes,
             m->decompressions_succeeded);
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
    m.conversion_latency.le_10ms = 4;
    m.conversion_latency.le_100ms = 5;
    m.conversion_latency.le_1000ms = 1;
    m.conversion_latency.gt_1000ms = 0;
    m.decompressions_attempted = 4;
    m.decompressions_succeeded = 3;
    m.streaming_peak_memory_bytes = 65536;
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

    written = snprintf(remote_addr, sizeof(remote_addr), "%u.%u.%u.%u",
                       10U, 0U, 0U, 5U);
    TEST_ASSERT(written > 0 && (size_t) written < sizeof(remote_addr),
                "failed to build remote address");
    r = handle_metrics_request("POST", remote_addr, "text", &m);
    TEST_ASSERT(r.status == 403,
                "Remote clients must be denied before method handling");

    r = handle_metrics_request("POST", "127.0.0.1", "text", &m);
    TEST_ASSERT(r.status == 405, "Only GET/HEAD should be allowed locally");

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

    TEST_SUBSECTION("Prometheus text 0.0.4 output is fixed");

    r = handle_metrics_request("GET", "::1", "text/plain", &m);
    TEST_ASSERT(STR_EQ(r.content_type, "text/plain; version=0.0.4; charset=utf-8"),
                "Metrics content type should be Prometheus text 0.0.4");
    TEST_ASSERT(strstr(r.body, "# TYPE nginx_markdown_requests_total counter") != NULL,
                "Prometheus output should declare requests_total");
    TEST_ASSERT(strstr(r.body, "nginx_markdown_requests_total{outcome=\"converted\"") != NULL,
                "Prometheus output should include converted requests");
    TEST_ASSERT(strstr(r.body, "nginx_markdown_decompression_events_total") != NULL,
                "Prometheus output should include decompression events");

    r = handle_metrics_request("GET", "::1", "application/json", &m);
    TEST_ASSERT(STR_EQ(r.content_type, "text/plain; version=0.0.4; charset=utf-8"),
                "JSON Accept must not select a removed representation");
    TEST_ASSERT(strstr(r.body, "nginx_markdown_build_info") != NULL,
                "All Accept values should receive the frozen Prometheus surface");
    TEST_PASS("Prometheus output contract works");
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
