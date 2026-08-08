#ifndef NGX_HTTP_MARKDOWN_METRICS_FORMAT_H
#define NGX_HTTP_MARKDOWN_METRICS_FORMAT_H

/*
 * The 0.9.2 metrics endpoint has one public wire format.  Keep this
 * selector in a small production header so tests exercise the same code
 * used by the HTTP handler instead of maintaining a copy of its policy.
 */
#define NGX_HTTP_MARKDOWN_METRICS_OUTPUT_TEXT        0
#define NGX_HTTP_MARKDOWN_METRICS_OUTPUT_JSON        1
#define NGX_HTTP_MARKDOWN_METRICS_OUTPUT_PROMETHEUS  2

struct ngx_http_request_s;

static ngx_uint_t
ngx_http_markdown_metrics_select_format(
    const struct ngx_http_request_s *r)
{
    (void) r;
    return NGX_HTTP_MARKDOWN_METRICS_OUTPUT_PROMETHEUS;
}

#endif /* NGX_HTTP_MARKDOWN_METRICS_FORMAT_H */
