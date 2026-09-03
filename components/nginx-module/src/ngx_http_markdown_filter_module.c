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
#include "ngx_http_markdown_ffi_layout_check.h"
#include "ngx_http_markdown_diagnostics.h"
#include "ngx_http_markdown_dynconf_impl.h"
#include "ngx_http_markdown_module_state_impl.h"
#include "ngx_http_markdown_postcommit_metrics_impl.h"
#include "ngx_http_markdown_filter_chain_impl.h"
#include "ngx_http_markdown_config_impl.h"
#include "ngx_http_markdown_lifecycle_impl.h"
#include "ngx_http_markdown_decision_log_impl.h"
#include "ngx_http_markdown_inflight_impl.h"
#include "ngx_http_markdown_request_impl.h"
#include "ngx_http_markdown_metrics_v1_renderer.h"
#include "ngx_http_markdown_metrics_impl.h"
#include "ngx_http_markdown_diagnostics_accessors_impl.h"

#ifdef MARKDOWN_STREAMING_ENABLED
#include "ngx_http_markdown_streaming_impl.h"
#endif

ngx_http_markdown_inflight_t  ngx_http_markdown_g_inflight;

void
ngx_http_markdown_release_inflight_for_request(const ngx_http_request_t *r)
{
    ngx_http_markdown_ctx_t  *ctx;

    if (r == NULL) {
        return;
    }

    ctx = ngx_http_get_module_ctx(r, ngx_http_markdown_filter_module);
    if (ctx == NULL
        || ngx_http_markdown_durable_bypass_kind(ctx)
           != NGX_HTTP_MARKDOWN_DURABLE_BYPASS_NONE)
    {
        return;
    }

    ngx_http_markdown_inflight_release(ctx);
}

/*
 * Module context
 *
 * Defines callbacks for configuration creation and merging.
 */
static ngx_http_module_t ngx_http_markdown_filter_module_ctx = {
    ngx_http_markdown_preconfiguration,     /* preconfiguration */
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
 * Keep the body hook as a separate module entry so NGINX can place it after
 * copy_filter while placing the representation-selecting header hook before
 * not_modified.  Both entries share this translation unit and the primary
 * module remains the owner of configuration and worker lifecycle state.
 */
static ngx_http_module_t ngx_http_markdown_body_filter_module_ctx = {
    NULL,                                   /* preconfiguration */
    ngx_http_markdown_body_filter_init,     /* postconfiguration */
    NULL,                                   /* create main configuration */
    NULL,                                   /* init main configuration */
    NULL,                                   /* create server configuration */
    NULL,                                   /* merge server configuration */
    NULL,                                   /* create location configuration */
    NULL                                    /* merge location configuration */
};

ngx_module_t ngx_http_markdown_body_filter_module = {
    NGX_MODULE_V1,
    &ngx_http_markdown_body_filter_module_ctx, /* module context */
    NULL,                                      /* module directives */
    NGX_HTTP_MODULE,                           /* module type */
    NULL,                                      /* init master */
    NULL,                                      /* init module */
    NULL,                                      /* init process */
    NULL,                                      /* init thread */
    NULL,                                      /* exit thread */
    NULL,                                      /* exit process */
    NULL,                                      /* exit master */
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
 * Find a request header by name using the generic linked-list storage.
 *
 * This fallback is used when NGINX is compiled without typed header
 * convenience pointers (e.g., r->headers_in.accept).  It traverses
 * the full ngx_list_part_t chain to locate the first matching header
 * by case-insensitive name comparison.
 *
 * Parameters:
 *   r    - HTTP request whose incoming headers are searched
 *   name - header name to find (need not be NUL-terminated)
 *
 * Returns:
 *   pointer to the matching ngx_table_elt_t, or NULL if not found
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
            if (headers[i].hash == 0) {
                continue;
            }
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
