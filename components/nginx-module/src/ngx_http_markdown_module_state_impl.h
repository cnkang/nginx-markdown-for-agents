/*
 * Internal module-state declarations shared by implementation includes.
 *
 * This header is intentionally included only from the main translation unit so
 * helper implementation headers can rely on a stable set of shared globals,
 * filter-chain pointers, metric macros, and forward declarations while keeping
 * all #include directives at the top of the .c file.
 */

#ifndef NGX_HTTP_MARKDOWN_MODULE_STATE_IMPL_H
#define NGX_HTTP_MARKDOWN_MODULE_STATE_IMPL_H

/*
 * Request-level buffered flag for this module while it is accumulating the
 * full response body before conversion.
 *
 * Low bits 0x01/0x02/0x04 are used by core modules (SSI/SUB/COPY). 0x08 is
 * available for request-level buffering (image filter uses 0x08 on
 * connection->buffered, not r->buffered).
 */
#define NGX_HTTP_MARKDOWN_BUFFERED  0x08
/* Bound eager reservation to avoid huge one-shot allocations on giant responses. */
#define NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT (16 * 1024 * 1024)

/* Global converter instance for this worker process */
static struct MarkdownConverterHandle *ngx_http_markdown_converter = NULL;

/* Global pointer to shared metrics state, resolved during worker init. */
static ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;
static ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;
static ngx_str_t ngx_http_markdown_metrics_shm_name = ngx_string("nginx_markdown_metrics");
static u_char ngx_http_markdown_empty_string[] = "";

#define NGX_HTTP_MARKDOWN_METRIC_ADD(field, value)                                  \
    do {                                                                            \
        if (ngx_http_markdown_metrics != NULL) {                                    \
            ngx_atomic_fetch_add(&ngx_http_markdown_metrics->field,                 \
                (ngx_atomic_uint_t) (value));                                       \
        }                                                                           \
    } while (0)

#define NGX_HTTP_MARKDOWN_METRIC_INC(field)                                         \
    NGX_HTTP_MARKDOWN_METRIC_ADD(field, 1)

/* Forward declarations */
static ngx_int_t ngx_http_markdown_filter_init(ngx_conf_t *cf);
static ngx_int_t ngx_http_markdown_init_worker(ngx_cycle_t *cycle);
static void ngx_http_markdown_exit_worker(ngx_cycle_t *cycle);

/* Filter entrypoint declarations */
static ngx_int_t ngx_http_markdown_header_filter(ngx_http_request_t *r);
static ngx_int_t ngx_http_markdown_body_filter(ngx_http_request_t *r, ngx_chain_t *in);

/* Metrics endpoint handler */
static ngx_int_t ngx_http_markdown_metrics_handler(ngx_http_request_t *r);
#if !(NGX_HTTP_HEADERS)
static ngx_table_elt_t *ngx_http_markdown_find_request_header(ngx_http_request_t *r,
    const ngx_str_t *name);
#endif

/* Next filter pointers for filter chain */
static ngx_http_output_header_filter_pt ngx_http_next_header_filter;
static ngx_http_output_body_filter_pt ngx_http_next_body_filter;

#endif
