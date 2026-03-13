/*
 * Module lifecycle helpers for filter registration and worker setup/teardown.
 *
 * Kept in a dedicated implementation include so the main module file can focus
 * on request-path orchestration.
 */

static ngx_int_t
ngx_http_markdown_filter_init(ngx_conf_t *cf)
{
    ngx_http_next_header_filter = ngx_http_top_header_filter;
    ngx_http_top_header_filter = ngx_http_markdown_header_filter;

    ngx_http_next_body_filter = ngx_http_top_body_filter;
    ngx_http_top_body_filter = ngx_http_markdown_body_filter;

    return NGX_OK;
}

static ngx_int_t
ngx_http_markdown_init_worker(ngx_cycle_t *cycle)
{
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

    return NGX_OK;
}

static void
ngx_http_markdown_exit_worker(ngx_cycle_t *cycle)
{
    if (ngx_http_markdown_converter == NULL) {
        ngx_log_error(NGX_LOG_DEBUG, cycle->log, 0,
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
