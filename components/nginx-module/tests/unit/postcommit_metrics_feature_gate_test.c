/*
 * Regression: feature-disabled builds omit the streaming metrics subtree.
 * The production postcommit helper implementation must therefore compile
 * without requiring that subtree when MARKDOWN_STREAMING_ENABLED is absent.
 */

#include "../include/test_common.h"

typedef unsigned long  ngx_atomic_t;

typedef struct {
    struct {
        ngx_atomic_t  backpressure_total;
        ngx_atomic_t  pending_output_high_watermark_bytes;
        ngx_atomic_t  copied_output_total;
    } perf;
} ngx_http_markdown_metrics_t;

static ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;

#define NGX_HTTP_MARKDOWN_METRIC_ADD(field, value)                    \
    do {                                                               \
        if (ngx_http_markdown_metrics != NULL) {                       \
            ngx_http_markdown_metrics->field += (value);              \
        }                                                              \
    } while (0)

#define NGX_HTTP_MARKDOWN_METRIC_INC(field)                            \
    NGX_HTTP_MARKDOWN_METRIC_ADD(field, 1)

#define NGX_HTTP_MARKDOWN_METRIC_WATERMARK(field, value)               \
    do {                                                               \
        if (ngx_http_markdown_metrics != NULL                          \
            && (value) > ngx_http_markdown_metrics->field)            \
        {                                                              \
            ngx_http_markdown_metrics->field = (value);                \
        }                                                              \
    } while (0)

#include "../../src/ngx_http_markdown_postcommit_metrics_impl.h"

int
main(void)
{
    UNUSED(ngx_http_markdown_metrics);
    TEST_PASS("feature-disabled postcommit metrics implementation compiles");
    return 0;
}
