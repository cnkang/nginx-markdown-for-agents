/*
 * NGINX Markdown Filter Module - Implementation
 *
 * This module provides HTML to Markdown conversion for AI agents via
 * HTTP content negotiation (Accept: text/markdown).
 * The main translation unit now mostly owns shared globals and module wiring,
 * while configuration/bootstrap, request-path orchestration, worker lifecycle,
 * and metrics endpoint helpers live in dedicated implementation includes.
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

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

extern ngx_module_t ngx_http_markdown_filter_module;

#include "ngx_http_markdown_config_impl.h"

/*
 * Module context
 *
 * Defines callbacks for configuration creation and merging.
 */
static ngx_http_module_t ngx_http_markdown_filter_module_ctx = {
    NULL,                                   /* preconfiguration */
    ngx_http_markdown_filter_init,          /* postconfiguration */
    ngx_http_markdown_create_main_conf,     /* create main configuration */
    NULL,                                   /* init main configuration */
    NULL,                                   /* create server configuration */
    NULL,                                   /* merge server configuration */
    ngx_http_markdown_create_conf,          /* create location configuration */
    ngx_http_markdown_merge_conf            /* merge location configuration */
};

/*
 * Module definition
 */
ngx_module_t ngx_http_markdown_filter_module = {
    NGX_MODULE_V1,
    &ngx_http_markdown_filter_module_ctx,   /* module context */
    ngx_http_markdown_filter_commands,      /* module directives */
    NGX_HTTP_MODULE,                        /* module type */
    NULL,                                   /* init master */
    NULL,                                   /* init module */
    ngx_http_markdown_init_worker,          /* init process */
    NULL,                                   /* init thread */
    NULL,                                   /* exit thread */
    ngx_http_markdown_exit_worker,          /* exit process */
    NULL,                                   /* exit master */
    NGX_MODULE_V1_PADDING
};

#include "ngx_http_markdown_lifecycle_impl.h"

#include "ngx_http_markdown_request_impl.h"

#include "ngx_http_markdown_metrics_impl.h"

/*
 * Find a request header by name in the generic headers list.
 *
 * This fallback is used for builds where some convenience pointers in
 * ngx_http_headers_in_t (for example `accept`) are not compiled in.
 */
#if !(NGX_HTTP_HEADERS)
static ngx_table_elt_t *
ngx_http_markdown_find_request_header(ngx_http_request_t *r, const ngx_str_t *name)
{
    ngx_list_part_t *part;
    ngx_table_elt_t *headers;
    ngx_uint_t       i;

    if (r == NULL || name == NULL || name->len == 0) {
        return NULL;
    }

    part = &r->headers_in.headers.part;
    headers = part->elts;

    for ( ;; ) {
        for (i = 0; i < part->nelts; i++) {
            if (headers[i].key.len == name->len
                && ngx_strncasecmp(headers[i].key.data, name->data, name->len) == 0)
            {
                return &headers[i];
            }
        }

        if (part->next == NULL) {
            break;
        }

        part = part->next;
        headers = part->elts;
    }

    return NULL;
}
#endif
