/*
 * Test: metrics_format_select
 * Description: Content negotiation for metrics output format
 *
 * Uses a standalone test pattern (no NGINX dependencies).
 * Tests all 8 rows of the content negotiation state machine
 * from the design document.
 */

#include "test_common.h"

/* ------------------------------------------------------------------ */
/* Constants mirroring the NGINX module                                */
/* ------------------------------------------------------------------ */

#define METRICS_FORMAT_AUTO        0
#define METRICS_FORMAT_PROMETHEUS  1

#define OUTPUT_TEXT        0
#define OUTPUT_JSON        1
#define OUTPUT_PROMETHEUS  2

/* ------------------------------------------------------------------ */
/* Standalone format selection function                                */
/* ------------------------------------------------------------------ */

/*
 * Check if the Accept header value contains a substring
 * (case-insensitive).
 */
static int
accept_contains(const char *accept, const char *needle)
{
    size_t  accept_len;
    size_t  needle_len;
    size_t  i, j;

    if (accept == NULL || needle == NULL) {
        return 0;
    }

    accept_len = strlen(accept);
    needle_len = strlen(needle);

    if (needle_len == 0 || accept_len < needle_len) {
        return 0;
    }

    for (i = 0; i + needle_len <= accept_len; i++) {
        for (j = 0; j < needle_len; j++) {
            char a = accept[i + j];
            char n = needle[j];

            /* lowercase for case-insensitive compare */
            if (a >= 'A' && a <= 'Z') {
                a = (char)(a + ('a' - 'A'));
            }
            if (n >= 'A' && n <= 'Z') {
                n = (char)(n + ('a' - 'A'));
            }
            if (a != n) {
                break;
            }
        }
        if (j == needle_len) {
            return 1;
        }
    }

    return 0;
}

/*
 * Select metrics output format based on Accept header and
 * metrics_format configuration.
 *
 * Mirrors ngx_http_markdown_metrics_select_format() logic.
 *
 * State machine:
 *   auto       + Accept: application/json     -> JSON
 *   auto       + Accept: text/plain           -> TEXT
 *   auto       + (any other / none)           -> TEXT
 *   prometheus + Accept: application/json     -> JSON
 *   prometheus + Accept: text/plain           -> PROMETHEUS
 *   prometheus + Accept: text/plain; v=0.0.4  -> PROMETHEUS
 *   prometheus + Accept: openmetrics-text     -> PROMETHEUS
 *   prometheus + (any other / none)           -> PROMETHEUS
 */
static int
select_format(int metrics_format, const char *accept)
{
    if (accept_contains(accept, "application/json")) {
        return OUTPUT_JSON;
    }

    if (metrics_format == METRICS_FORMAT_PROMETHEUS) {
        return OUTPUT_PROMETHEUS;
    }

    return OUTPUT_TEXT;
}

/* ------------------------------------------------------------------ */
/* Test: auto + Accept: application/json -> JSON                       */
/* ------------------------------------------------------------------ */

static void
test_auto_json(void)
{
    int result;

    TEST_SUBSECTION("auto + Accept: application/json -> JSON");

    result = select_format(METRICS_FORMAT_AUTO,
                           "application/json");
    TEST_ASSERT(result == OUTPUT_JSON,
                "auto + application/json should select JSON");
    TEST_PASS("auto + application/json -> JSON");
}

/* ------------------------------------------------------------------ */
/* Test: auto + Accept: text/plain -> plain text                       */
/* ------------------------------------------------------------------ */

static void
test_auto_text_plain(void)
{
    int result;

    TEST_SUBSECTION("auto + Accept: text/plain -> TEXT");

    result = select_format(METRICS_FORMAT_AUTO,
                           "text/plain");
    TEST_ASSERT(result == OUTPUT_TEXT,
                "auto + text/plain should select TEXT");
    TEST_PASS("auto + text/plain -> TEXT");
}

/* ------------------------------------------------------------------ */
/* Test: auto + (any other / none) -> plain text                       */
/* ------------------------------------------------------------------ */

static void
test_auto_none(void)
{
    int result;

    TEST_SUBSECTION("auto + no Accept -> TEXT");

    result = select_format(METRICS_FORMAT_AUTO, NULL);
    TEST_ASSERT(result == OUTPUT_TEXT,
                "auto + no Accept should select TEXT");

    result = select_format(METRICS_FORMAT_AUTO, "*/*");
    TEST_ASSERT(result == OUTPUT_TEXT,
                "auto + */* should select TEXT");

    result = select_format(METRICS_FORMAT_AUTO, "text/html");
    TEST_ASSERT(result == OUTPUT_TEXT,
                "auto + text/html should select TEXT");

    TEST_PASS("auto + (any other / none) -> TEXT");
}

/* ------------------------------------------------------------------ */
/* Test: prometheus + Accept: application/json -> JSON                  */
/* ------------------------------------------------------------------ */

static void
test_prometheus_json(void)
{
    int result;

    TEST_SUBSECTION(
        "prometheus + Accept: application/json -> JSON");

    result = select_format(METRICS_FORMAT_PROMETHEUS,
                           "application/json");
    TEST_ASSERT(result == OUTPUT_JSON,
                "prometheus + application/json "
                "should select JSON");
    TEST_PASS("prometheus + application/json -> JSON");
}

/* ------------------------------------------------------------------ */
/* Test: prometheus + Accept: text/plain -> Prometheus                  */
/* ------------------------------------------------------------------ */

static void
test_prometheus_text_plain(void)
{
    int result;

    TEST_SUBSECTION(
        "prometheus + Accept: text/plain -> PROMETHEUS");

    result = select_format(METRICS_FORMAT_PROMETHEUS,
                           "text/plain");
    TEST_ASSERT(result == OUTPUT_PROMETHEUS,
                "prometheus + text/plain "
                "should select PROMETHEUS");
    TEST_PASS("prometheus + text/plain -> PROMETHEUS");
}

/* ------------------------------------------------------------------ */
/* Test: prometheus + Accept: text/plain; version=0.0.4 -> Prometheus   */
/* ------------------------------------------------------------------ */

static void
test_prometheus_text_plain_versioned(void)
{
    int result;

    TEST_SUBSECTION(
        "prometheus + Accept: text/plain; "
        "version=0.0.4 -> PROMETHEUS");

    result = select_format(METRICS_FORMAT_PROMETHEUS,
                           "text/plain; version=0.0.4");
    TEST_ASSERT(result == OUTPUT_PROMETHEUS,
                "prometheus + text/plain; version=0.0.4 "
                "should select PROMETHEUS");
    TEST_PASS(
        "prometheus + text/plain; version=0.0.4 "
        "-> PROMETHEUS");
}

/* ------------------------------------------------------------------ */
/* Test: prometheus + Accept: application/openmetrics-text -> Prometheus */
/* ------------------------------------------------------------------ */

static void
test_prometheus_openmetrics(void)
{
    int result;

    TEST_SUBSECTION(
        "prometheus + Accept: "
        "application/openmetrics-text -> PROMETHEUS");

    result = select_format(METRICS_FORMAT_PROMETHEUS,
        "application/openmetrics-text");
    TEST_ASSERT(result == OUTPUT_PROMETHEUS,
                "prometheus + openmetrics-text "
                "should select PROMETHEUS");
    TEST_PASS(
        "prometheus + openmetrics-text -> PROMETHEUS");
}

/* ------------------------------------------------------------------ */
/* Test: prometheus + (any other / none) -> Prometheus                  */
/* ------------------------------------------------------------------ */

static void
test_prometheus_none(void)
{
    int result;

    TEST_SUBSECTION(
        "prometheus + no Accept -> PROMETHEUS");

    result = select_format(METRICS_FORMAT_PROMETHEUS, NULL);
    TEST_ASSERT(result == OUTPUT_PROMETHEUS,
                "prometheus + no Accept "
                "should select PROMETHEUS");

    result = select_format(METRICS_FORMAT_PROMETHEUS, "*/*");
    TEST_ASSERT(result == OUTPUT_PROMETHEUS,
                "prometheus + */* "
                "should select PROMETHEUS");

    result = select_format(METRICS_FORMAT_PROMETHEUS,
                           "text/html");
    TEST_ASSERT(result == OUTPUT_PROMETHEUS,
                "prometheus + text/html "
                "should select PROMETHEUS");

    TEST_PASS("prometheus + (any other / none) "
              "-> PROMETHEUS");
}

/* ------------------------------------------------------------------ */
/* main                                                                */
/* ------------------------------------------------------------------ */

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_format_select Tests\n");
    printf("========================================\n");

    test_auto_json();
    test_auto_text_plain();
    test_auto_none();
    test_prometheus_json();
    test_prometheus_text_plain();
    test_prometheus_text_plain_versioned();
    test_prometheus_openmetrics();
    test_prometheus_none();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
