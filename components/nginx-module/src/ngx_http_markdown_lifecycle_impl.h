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

/* Validate the full Rust/C ABI 4-tuple handshake, then reset state. */
static ngx_int_t
ngx_http_markdown_preconfiguration(ngx_conf_t *cf)
{
    uint32_t  actual_abi;
    uint64_t  actual_header_hash;
    uint64_t  actual_symbol_hash;
    uint64_t  actual_layout_fp;
    ngx_int_t mismatch;

    mismatch = 0;

    /* 1. Numeric ABI version */
    actual_abi = markdown_abi_version();
    if (!ngx_http_markdown_ffi_abi_matches(actual_abi)) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "markdown: ABI handshake FAILED — numeric version "
                           "mismatch (expected=%ui, actual=%ui)",
                           (ngx_uint_t) MARKDOWN_ABI_VERSION,
                           (ngx_uint_t) actual_abi);
        mismatch = 1;
    }

    /* 2. Generated-header identity hash */
    actual_header_hash = markdown_abi_header_hash();
    if (actual_header_hash != MARKDOWN_HEADER_HASH) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "markdown: ABI handshake FAILED — header hash "
                           "mismatch (expected=0x%016xL, actual=0x%016xL)",
                           MARKDOWN_HEADER_HASH,
                           actual_header_hash);
        mismatch = 1;
    }

    /* 3. Exported-symbol-set hash */
    actual_symbol_hash = markdown_abi_symbol_set_hash();
    if (actual_symbol_hash != MARKDOWN_SYMBOL_SET_HASH) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "markdown: ABI handshake FAILED — symbol set hash "
                           "mismatch (expected=0x%016xL, actual=0x%016xL)",
                           MARKDOWN_SYMBOL_SET_HASH,
                           actual_symbol_hash);
        mismatch = 1;
    }

    /* 4. ABI struct layout fingerprint */
    actual_layout_fp = markdown_abi_layout_fingerprint();
    if (actual_layout_fp != MARKDOWN_LAYOUT_FINGERPRINT) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "markdown: ABI handshake FAILED — layout "
                           "fingerprint mismatch "
                           "(expected=0x%016xL, actual=0x%016xL)",
                           MARKDOWN_LAYOUT_FINGERPRINT,
                           actual_layout_fp);
        mismatch = 1;
    }

    if (mismatch) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "markdown: Rust/C ABI handshake failed; rebuild "
                           "the module and bundled Rust converter together");
        return NGX_ERROR;
    }

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
 * shared metrics zone, and optionally start the dynamic configuration watcher.
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
    const ngx_http_conf_ctx_t              *http_ctx;
    const ngx_http_markdown_main_conf_t    *mcf;
    ngx_http_markdown_conf_t               *dynconf_conf;

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
        mcf = (const ngx_http_markdown_main_conf_t *)
            http_ctx->main_conf[
                ngx_http_markdown_filter_module.ctx_index];
        dynconf_conf = ngx_http_markdown_dynconf_owner(mcf);

        if (dynconf_conf != NULL
            && dynconf_conf->advanced.dynconf_enabled
            && dynconf_conf->advanced.dynconf_path.len > 0)
        {
            ngx_http_markdown_dynconf_watcher.validation_index =
                mcf->loc_validation_index;

            if (ngx_http_markdown_dynconf_start(
                    &ngx_http_markdown_dynconf_watcher,
                    cycle, &dynconf_conf->advanced.dynconf_path,
                    dynconf_conf, cycle->log) != NGX_OK)
            {
                ngx_log_error(NGX_LOG_WARN, cycle->log, 0,
                              "markdown: failed to start watcher");
                /* Non-fatal: worker continues without hot-reload. */
            }
        }

        /*
         * Per-path metrics removed from production in 0.9.2
         * (unbounded cardinality risk).
         */
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
        if (ngx_http_markdown_metrics != NULL) {
            ngx_http_markdown_metrics->per_path.cardinality_limit =
                NGX_HTTP_MARKDOWN_PER_PATH_CARDINALITY_DEFAULT;
        }
#endif
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
    } else {
        markdown_converter_free(ngx_http_markdown_converter);
        ngx_http_markdown_converter = NULL;

        ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                      "markdown: converter cleaned up in worker process (pid: %P)",
                      ngx_pid);
    }

    ngx_http_markdown_metrics = NULL;
}

#endif /* NGX_HTTP_MARKDOWN_LIFECYCLE_IMPL_H */
