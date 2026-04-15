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
#include "ngx_http_markdown_module_state_impl.h"
#include "ngx_http_markdown_config_impl.h"
#include "ngx_http_markdown_lifecycle_impl.h"
#include "ngx_http_markdown_decision_log_impl.h"
#include "ngx_http_markdown_request_impl.h"
#include "ngx_http_markdown_metrics_impl.h"
#include "ngx_http_markdown_prometheus_impl.h"

#ifdef MARKDOWN_STREAMING_ENABLED
#include "ngx_http_markdown_streaming_impl.h"
#endif

/*
 * Module context
 *
 * Defines callbacks for configuration creation and merging.
 */
static ngx_http_module_t ngx_http_markdown_filter_module_ctx = {
    NULL,                                   /* preconfiguration */
    ngx_http_markdown_filter_init,          /* postconfiguration */
    ngx_http_markdown_create_main_conf,     /* create main configuration */
    ngx_http_markdown_init_main_conf,       /* init main configuration */
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

/*
 * Find a request header by name in the generic headers list.
 *
 * This fallback is used for builds where some convenience pointers in
 * ngx_http_headers_in_t (for example `accept`) are not compiled in.
 */
#if !(NGX_HTTP_HEADERS)
/*
 * Fallback header lookup for builds without typed header shortcuts in
 * `ngx_http_headers_in_t`.
 */
static ngx_table_elt_t *
ngx_http_markdown_find_request_header(ngx_http_request_t *r,
    const ngx_str_t *name)
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
                && ngx_strncasecmp(headers[i].key.data,
                                   name->data, name->len) == 0)
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
