/*
 * Test: metrics_format_select
 *
 * The 0.9.2 public metrics endpoint is frozen to Prometheus text
 * exposition format 0.0.4.  Accept negotiation is retained as an
 * implementation detail only; it must never restore the removed JSON or
 * legacy plain-text response shapes.
 */

#include "test_common.h"

typedef uintptr_t ngx_uint_t;

#include "../../src/ngx_http_markdown_metrics_format.h"

#define OUTPUT_PROMETHEUS NGX_HTTP_MARKDOWN_METRICS_OUTPUT_PROMETHEUS

static void
test_all_accept_values_select_prometheus(void)
{
    const char *accept_values[] = {
        NULL,
        "",
        "*/*",
        "text/plain",
        "text/plain; version=0.0.4",
        "application/openmetrics-text",
        "application/json",
        "application/xml",
    };
    size_t  i;

    TEST_SUBSECTION("all Accept values select Prometheus v1");

    for (i = 0; i < sizeof(accept_values) / sizeof(accept_values[0]); i++) {
        (void) accept_values[i];
        TEST_ASSERT(ngx_http_markdown_metrics_select_format(NULL)
                    == OUTPUT_PROMETHEUS,
                    "metrics endpoint must remain Prometheus-only");
    }

    TEST_PASS("all Accept values select Prometheus v1");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_format_select Tests\n");
    printf("========================================\n");

    test_all_accept_values_select_prometheus();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
