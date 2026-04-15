/*
 * Test: streaming_prometheus_metric_family
 * Description: verify streaming Prometheus outcome family separation
 */

#include "../include/test_common.h"

typedef struct {
    unsigned long succeeded_total;
    unsigned long failed_total;
    unsigned long fallback_total;
    unsigned long precommit_failopen_total;
    unsigned long precommit_reject_total;
    unsigned long postcommit_error_total;
} streaming_snapshot_t;

static int
contains(const char *haystack, const char *needle)
{
    return strstr(haystack, needle) != NULL;
}

static int
render_streaming_prometheus(
    const streaming_snapshot_t *s,
    char *buf,
    size_t buf_len)
{
    int n;

    n = snprintf(buf, buf_len,
        "# HELP nginx_markdown_streaming_total "
        "Streaming conversion outcomes.\n"
        "# TYPE nginx_markdown_streaming_total counter\n"
        "nginx_markdown_streaming_total"
        "{result=\"success\"} %lu\n"
        "nginx_markdown_streaming_total"
        "{result=\"failed\"} %lu\n"
        "nginx_markdown_streaming_total"
        "{result=\"fallback\"} %lu\n"
        "\n"
        "# HELP nginx_markdown_streaming_failures_total "
        "Detailed streaming failures by stage.\n"
        "# TYPE nginx_markdown_streaming_failures_total counter\n"
        "nginx_markdown_streaming_failures_total"
        "{stage=\"precommit_failopen\"} %lu\n"
        "nginx_markdown_streaming_failures_total"
        "{stage=\"precommit_reject\"} %lu\n"
        "nginx_markdown_streaming_failures_total"
        "{stage=\"postcommit_error\"} %lu\n"
        "\n",
        s->succeeded_total,
        s->failed_total,
        s->fallback_total,
        s->precommit_failopen_total,
        s->precommit_reject_total,
        s->postcommit_error_total);

    TEST_ASSERT(n >= 0, "snprintf should succeed");
    TEST_ASSERT((size_t) n < buf_len, "output should fit in buffer");
    return n;
}

static void
test_outcome_family_is_mutually_exclusive(void)
{
    char                 buf[2048];
    streaming_snapshot_t s;

    TEST_SUBSECTION(
        "streaming_total excludes postcommit_error label");

    memset(&s, 0, sizeof(s));
    s.succeeded_total = 10;
    s.failed_total = 3;
    s.fallback_total = 2;
    s.precommit_failopen_total = 1;
    s.precommit_reject_total = 1;
    s.postcommit_error_total = 1;

    render_streaming_prometheus(&s, buf, sizeof(buf));

    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_streaming_total"
            "{result=\"success\"} 10"),
        "streaming_total success label should be present");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_streaming_total"
            "{result=\"failed\"} 3"),
        "streaming_total failed label should be present");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_streaming_total"
            "{result=\"fallback\"} 2"),
        "streaming_total fallback label should be present");
    TEST_ASSERT(
        !contains(buf,
            "nginx_markdown_streaming_total"
            "{result=\"postcommit_error\"}"),
        "postcommit_error must not be exported under streaming_total");

    TEST_PASS("streaming_total family is mutually exclusive");
}

static void
test_postcommit_error_is_in_failure_breakdown_family(void)
{
    char                 buf[2048];
    streaming_snapshot_t s;

    TEST_SUBSECTION(
        "postcommit_error appears in streaming_failures_total");

    memset(&s, 0, sizeof(s));
    s.precommit_failopen_total = 4;
    s.precommit_reject_total = 5;
    s.postcommit_error_total = 6;

    render_streaming_prometheus(&s, buf, sizeof(buf));

    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_streaming_failures_total"
            "{stage=\"precommit_failopen\"} 4"),
        "precommit_failopen stage label should be present");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_streaming_failures_total"
            "{stage=\"precommit_reject\"} 5"),
        "precommit_reject stage label should be present");
    TEST_ASSERT(
        contains(buf,
            "nginx_markdown_streaming_failures_total"
            "{stage=\"postcommit_error\"} 6"),
        "postcommit_error stage label should be present");

    TEST_PASS("streaming_failures_total contains postcommit_error stage");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_prometheus_metric_family Tests\n");
    printf("========================================\n");

    test_outcome_family_is_mutually_exclusive();
    test_postcommit_error_is_in_failure_breakdown_family();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
