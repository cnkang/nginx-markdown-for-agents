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

/* Reset per-configuration-cycle state before nginx parses directives. */
static ngx_int_t
ngx_http_markdown_preconfiguration(ngx_conf_t *cf)
{
    (void) cf;

    ngx_http_markdown_diagnostics_reset_recording_request();

    return NGX_OK;
}


/* Wire this module into the header and body filter chains. */
static ngx_int_t
ngx_http_markdown_filter_init(ngx_conf_t *cf)
{
    (void) cf;

    ngx_http_next_header_filter = ngx_http_top_header_filter;
    ngx_http_top_header_filter = ngx_http_markdown_header_filter;

    ngx_http_next_body_filter = ngx_http_top_body_filter;
    ngx_http_top_body_filter = ngx_http_markdown_body_filter;

    return NGX_OK;
}

/**
 * Initialize per-worker markdown resources: allocate a converter, attach the
 * shared metrics zone, optionally start the dynamic configuration watcher, and
 * apply any configured per-path metrics cardinality.
 *
 * If the metrics shared-memory zone is unavailable or the converter cannot be
 * created, initialization fails.
 *
 * @param cycle Pointer to the nginx cycle (used for logging and to obtain the HTTP configuration).
 * @return NGX_OK on successful initialization;
 *         NGX_ERROR if the metrics shared-memory zone is missing or the converter creation fails.
 *         Note: failure to start the dynamic configuration watcher is logged as a warning but is non-fatal.
 */
static ngx_int_t
ngx_http_markdown_init_worker(ngx_cycle_t *cycle)
{
    const ngx_http_conf_ctx_t       *http_ctx;
    ngx_http_markdown_conf_t  *lcf;

    if (ngx_http_markdown_metrics_shm_zone == NULL
        || ngx_http_markdown_metrics_shm_zone->data == NULL)
    {
        ngx_log_error(NGX_LOG_CRIT, cycle->log, 0,
                      "markdown: metrics shared-memory zone unavailable");
        return NGX_ERROR;
    }

    ngx_http_markdown_metrics = ngx_http_markdown_metrics_shm_zone->data;

    ngx_http_markdown_converter = markdown_converter_new();
    if (ngx_http_markdown_converter == NULL) {
        ngx_log_error(NGX_LOG_CRIT, cycle->log, 0,
                      "markdown: failed to initialize converter in worker process, "
                      "category=system");
        return NGX_ERROR;
    }

    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown: converter initialized in worker process (pid: %P)",
                  ngx_pid);

    /*
     * Initialize the per-worker diagnostics recent-decisions ring.  This is
     * a no-op unless a location enabled markdown_diagnostics.  Allocation
     * failure is non-fatal: the worker continues without the ring (the
     * diagnostics endpoint will simply report an empty recent_decisions
     * array) rather than refusing to start.
     */
    if (ngx_http_markdown_diagnostics_init_worker(cycle) != NGX_OK) {
        ngx_log_error(NGX_LOG_WARN, cycle->log, 0,
                      "markdown: diagnostics ring init failed; "
                      "recent_decisions will be empty");
        /* Non-fatal: worker continues without decision recording. */
    }

#ifdef NGX_HTTP_BROTLI
    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown: decompression support: gzip=yes, deflate=yes, brotli=yes");
#else
    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown: decompression support: gzip=yes, deflate=yes, brotli=no");
#endif

    /* Start dynamic config watcher if configured. */
    http_ctx = (const ngx_http_conf_ctx_t *)
        ngx_get_conf(cycle->conf_ctx, ngx_http_module);
    if (http_ctx != NULL) {
        lcf = (ngx_http_markdown_conf_t *)
            http_ctx->loc_conf[ngx_http_markdown_filter_module.ctx_index];
        if (lcf != NULL && lcf->advanced.dynconf_enabled
            && lcf->advanced.dynconf_path.len > 0
            && ngx_http_markdown_dynconf_start(
                   &ngx_http_markdown_dynconf_watcher,
                   cycle, &lcf->advanced.dynconf_path, lcf, cycle->log)
               != NGX_OK)
        {
            ngx_log_error(NGX_LOG_WARN, cycle->log, 0,
                          "markdown: failed to start watcher");
            /* Non-fatal: worker continues without hot-reload. */
        }

        /*
         * Wire per-path cardinality limit from the main configuration
         * into the shared metrics struct.  This is a global (http-level)
         * setting because the SHM metrics struct is process-wide.
         */
        if (ngx_http_markdown_metrics != NULL) {
            const ngx_http_markdown_main_conf_t  *mcf;

            mcf = (const ngx_http_markdown_main_conf_t *)
                http_ctx->main_conf[
                    ngx_http_markdown_filter_module.ctx_index];

            if (mcf != NULL)
            {
                ngx_http_markdown_metrics->per_path.cardinality_limit =
                    mcf->metrics_per_path_cardinality;
            }
        }
    }

    return NGX_OK;
}

/*
 * Release per-worker resources on graceful shutdown.
 *
 * Stops the dynamic config watcher, frees the Rust converter handle,
 * and clears global pointers.  Safe to call when the converter was
 * never initialized (early-exit on NULL).
 *
 * Parameters:
 *   cycle - NGINX cycle (used for logging)
 */
static void
ngx_http_markdown_exit_worker(ngx_cycle_t *cycle)
{
    ngx_http_markdown_dynconf_stop(&ngx_http_markdown_dynconf_watcher,
                                   cycle->log);

    if (ngx_http_markdown_converter == NULL) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, cycle->log, 0,
                       "markdown: no converter to clean up in worker process");
        return;
    }

    markdown_converter_free(ngx_http_markdown_converter);
    ngx_http_markdown_converter = NULL;
    ngx_http_markdown_metrics = NULL;

    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown: converter cleaned up in worker process (pid: %P)",
                  ngx_pid);
}

#endif /* NGX_HTTP_MARKDOWN_LIFECYCLE_IMPL_H */
