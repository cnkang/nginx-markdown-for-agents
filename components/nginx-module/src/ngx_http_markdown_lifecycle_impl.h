#ifndef NGX_HTTP_MARKDOWN_LIFECYCLE_IMPL_H
#define NGX_HTTP_MARKDOWN_LIFECYCLE_IMPL_H

/*
 * Module lifecycle helpers for filter registration and worker setup/teardown.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * Kept in a dedicated implementation include so the main module file can focus
 * on request-path orchestration.
 */

/* Wire this module into the header and body filter chains. */
static ngx_int_t
ngx_http_markdown_filter_init(ngx_conf_t *cf) /* NOSONAR: nginx callback signature */
{
    (void) cf;

    ngx_http_next_header_filter = ngx_http_top_header_filter;
    ngx_http_top_header_filter = ngx_http_markdown_header_filter;

    ngx_http_next_body_filter = ngx_http_top_body_filter;
    ngx_http_top_body_filter = ngx_http_markdown_body_filter;

    return NGX_OK;
}

/* Allocate per-worker converter handle and attach shared metrics zone. */
static ngx_int_t
ngx_http_markdown_init_worker(ngx_cycle_t *cycle)
{
    ngx_http_conf_ctx_t       *http_ctx;
    ngx_http_markdown_conf_t  *lcf;

    if (ngx_http_markdown_metrics_shm_zone == NULL
        || ngx_http_markdown_metrics_shm_zone->data == NULL)
    {
        ngx_log_error(NGX_LOG_CRIT, cycle->log, 0,
                      "markdown filter: metrics shared-memory zone unavailable");
        return NGX_ERROR;
    }

    ngx_http_markdown_metrics = ngx_http_markdown_metrics_shm_zone->data;

    ngx_http_markdown_converter = markdown_converter_new();
    if (ngx_http_markdown_converter == NULL) {
        ngx_log_error(NGX_LOG_CRIT, cycle->log, 0,
                      "markdown filter: failed to initialize converter in worker process, "
                      "category=system");
        return NGX_ERROR;
    }

    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown filter: converter initialized in worker process (pid: %P)",
                  ngx_pid);

#ifdef NGX_HTTP_BROTLI
    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown filter: decompression support: gzip=yes, deflate=yes, brotli=yes");
#else
    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown filter: decompression support: gzip=yes, deflate=yes, brotli=no");
#endif

    /* Start dynamic config watcher if configured. */
    http_ctx = (ngx_http_conf_ctx_t *)
        ngx_get_conf(cycle->conf_ctx, ngx_http_module);
    if (http_ctx != NULL) {
        lcf = http_ctx->loc_conf[ngx_http_markdown_filter_module.ctx_index];
        if (lcf != NULL && lcf->dynconf_enabled
            && lcf->dynconf_path.len > 0)
        {
            if (ngx_http_markdown_dynconf_start(
                    &ngx_http_markdown_dynconf_watcher,
                    cycle, &lcf->dynconf_path, cycle->log)
                != NGX_OK)
            {
                ngx_log_error(NGX_LOG_WARN, cycle->log, 0,
                              "markdown dynconf: failed to start watcher");
                /* Non-fatal: worker continues without hot-reload. */
            }
        }

        /*
         * Wire per-path cardinality limit from configuration into
         * the shared metrics struct.  The SHM initializer sets a
         * default; override it with the operator's configured value.
         */
        if (lcf != NULL && lcf->ops.metrics_per_path
            && lcf->ops.metrics_per_path_cardinality > 0
            && ngx_http_markdown_metrics != NULL)
        {
            ngx_http_markdown_metrics->per_path.cardinality_limit =
                lcf->ops.metrics_per_path_cardinality;
        }
    }

    return NGX_OK;
}

/* Release the per-worker converter handle on graceful shutdown. */
static void
ngx_http_markdown_exit_worker(ngx_cycle_t *cycle)
{
    ngx_http_markdown_dynconf_stop(&ngx_http_markdown_dynconf_watcher,
                                   cycle->log);

    if (ngx_http_markdown_converter == NULL) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, cycle->log, 0,
                       "markdown filter: no converter to clean up in worker process");
        return;
    }

    markdown_converter_free(ngx_http_markdown_converter);
    ngx_http_markdown_converter = NULL;
    ngx_http_markdown_metrics = NULL;

    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown filter: converter cleaned up in worker process (pid: %P)",
                  ngx_pid);
}

#endif /* NGX_HTTP_MARKDOWN_LIFECYCLE_IMPL_H */
