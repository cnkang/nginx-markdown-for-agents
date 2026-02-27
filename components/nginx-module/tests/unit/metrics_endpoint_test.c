/*
 * Test: metrics_endpoint
 * Description: metrics endpoint
 */

#include "test_common.h"

typedef struct {
    unsigned long conversions_attempted;
    unsigned long conversions_succeeded;
    unsigned long conversions_failed;
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
    if (STR_EQ(format, "json")) {
        snprintf(out.body, sizeof(out.body),
                 "{\"conversions_attempted\":%lu,\"conversions_succeeded\":%lu,\"conversions_failed\":%lu,"
                 "\"decompressions_attempted\":%lu,\"decompressions_succeeded\":%lu}",
                 m->conversions_attempted, m->conversions_succeeded, m->conversions_failed,
                 m->decompressions_attempted, m->decompressions_succeeded);
    } else {
        snprintf(out.body, sizeof(out.body),
                 "markdown_conversions_attempted_total %lu\n"
                 "markdown_conversions_succeeded_total %lu\n"
                 "markdown_conversions_failed_total %lu\n"
                 "markdown_decompressions_attempted_total %lu\n"
                 "markdown_decompressions_succeeded_total %lu\n",
                 m->conversions_attempted, m->conversions_succeeded, m->conversions_failed,
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
    m.decompressions_attempted = 4;
    m.decompressions_succeeded = 3;
    return m;
}

static void
test_access_restrictions(void)
{
    endpoint_response_t r;
    metrics_t m = sample_metrics();

    TEST_SUBSECTION("Method and localhost restrictions");

    r = handle_metrics_request("POST", "127.0.0.1", "text", &m);
    TEST_ASSERT(r.status == 405, "Only GET/HEAD should be allowed");

    r = handle_metrics_request("GET", "10.0.0.5", "text", &m);
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
    TEST_ASSERT(strstr(r.body, "markdown_conversions_attempted_total 10") != NULL,
                "Plain text output should include counters");

    r = handle_metrics_request("GET", "::1", "json", &m);
    TEST_ASSERT(strstr(r.body, "\"conversions_attempted\":10") != NULL,
                "JSON output should include counters");
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
