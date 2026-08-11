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

struct ngx_http_request_s {
    unsigned char  unused;
};

static void
test_request_shapes_select_prometheus(void)
{
    struct ngx_http_request_s request;

    TEST_SUBSECTION("all request shapes select Prometheus v1");

    memset(&request, 0, sizeof(request));
    TEST_ASSERT(ngx_http_markdown_metrics_select_format(NULL)
                == OUTPUT_PROMETHEUS,
                "metrics endpoint must remain Prometheus-only");
    TEST_ASSERT(ngx_http_markdown_metrics_select_format(&request)
                == OUTPUT_PROMETHEUS,
                "metrics endpoint must remain Prometheus-only");

    TEST_PASS("all request shapes select Prometheus v1");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_format_select Tests\n");
    printf("========================================\n");

    test_request_shapes_select_prometheus();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
