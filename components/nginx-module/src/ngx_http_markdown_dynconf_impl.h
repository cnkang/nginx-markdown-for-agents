/*
 * NGINX Markdown Filter Module - Dynamic Configuration Hot-Reload
 *
 * Enables runtime modification of module configuration without
 * NGINX restart.  Uses a periodic timer event to poll the
 * configuration file for mtime changes, then signals a reload.
 *
 * Architecture:
 *   - Dedicated file watcher per worker process
 *   - Coarse-grained polling (1s interval via ngx_event_t timer)
 *   - On mtime change, the timer handler sets a reload-pending
 *     flag; the next request that enters the filter will pick
 *     up the new configuration.
 *   - Grace-period drain for in-flight requests is handled by
 *     the caller (filter module) by retaining the old config
 *     until the active request count drops to zero.
 *
 * Requirements: v0.6.0 P2-10
 */

#ifndef NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H
#define NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H


/*
 * Dynamic config state constants.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_OFF      0
#define NGX_HTTP_MARKDOWN_DYNCONF_ON       1

/*
 * Dynamic config watch interval in milliseconds.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_WATCH_MS  1000

/*
 * Forward declaration for timer handler.
 */
static void ngx_http_markdown_dynconf_timer_handler(ngx_event_t *ev);

/*
 * Dynamic configuration file watcher.
 *
 * Holds the file path, last modification time, and a
 * periodic timer event that polls for changes.
 */
typedef struct {
    ngx_str_t     path;
    time_t        last_mtime;
    ngx_event_t  *timer;
    ngx_uint_t    active;
    ngx_uint_t    reload_pending;
} ngx_http_markdown_dynconf_watcher_t;


/*
 * Check if the watched configuration file has changed.
 *
 * Compares the current file modification time against the
 * stored last_mtime.  If different, updates last_mtime
 * and returns 1.
 *
 * Parameters:
 *   watcher - Dynamic config watcher
 *   log     - NGINX log
 *
 * Returns:
 *   1 if file changed, 0 otherwise
 */
static ngx_int_t
ngx_http_markdown_dynconf_check(ngx_http_markdown_dynconf_watcher_t *watcher,
                                const ngx_log_t *log)
{
    ngx_file_info_t  fi;

    if (watcher == NULL || !watcher->active) {
        return 0;
    }

    {
        u_char  path_buf[NGX_MAX_PATH + 1];

        if (watcher->path.len > NGX_MAX_PATH) {
            ngx_log_error(NGX_LOG_WARN, (ngx_log_t *) log, 0,
                          "markdown dynconf: path too long (%uz bytes)",
                          watcher->path.len);
            return 0;
        }

        ngx_memcpy(path_buf, watcher->path.data, watcher->path.len);
        path_buf[watcher->path.len] = '\0';

        if (ngx_file_info(path_buf, &fi) == NGX_FILE_ERROR) {
            ngx_log_error(NGX_LOG_WARN, (ngx_log_t *) log, 0,
                          "markdown dynconf: stat(\"%V\") failed",
                          &watcher->path);
            return 0;
        }
    }

    if (ngx_file_mtime(fi) != watcher->last_mtime) {
        watcher->last_mtime = ngx_file_mtime(fi);
        return 1;
    }

    return 0;
}


/*
 * Timer handler for the dynamic config watcher.
 *
 * Called periodically by the NGINX event loop.  Checks the
 * watched file for changes.  If a change is detected, sets
 * reload_pending so the filter module can react on the next
 * request.  Re-arms the timer for the next check.
 *
 * Parameters:
 *   ev - Timer event (ev->data points to the watcher)
 */
static void
ngx_http_markdown_dynconf_timer_handler(ngx_event_t *ev)
{
    ngx_http_markdown_dynconf_watcher_t  *watcher;

    watcher = ev->data;

    if (watcher == NULL || !watcher->active) {
        return;
    }

    if (ngx_http_markdown_dynconf_check(watcher, ev->log)) {
        watcher->reload_pending = 1;

        ngx_log_error(NGX_LOG_INFO, ev->log, 0,
                      "markdown dynconf: change detected on \"%V\", "
                      "reload pending",
                      &watcher->path);
    }

    /* Re-arm the timer for the next poll cycle. */
    if (watcher->timer != NULL && !watcher->timer->timer_set) {
        ngx_add_timer(watcher->timer, NGX_HTTP_MARKDOWN_DYNCONF_WATCH_MS);
    }
}


/*
 * Initialize the dynamic config watcher.
 *
 * Allocates the watcher and timer event from the cycle pool,
 * copies the path to pool-owned storage, performs an initial
 * stat to record the baseline mtime, and adds the periodic
 * timer to the event loop.
 *
 * Parameters:
 *   watcher - Pre-allocated watcher struct (caller provides storage)
 *   cycle   - NGINX cycle structure
 *   path    - Path to the configuration file
 *   log     - NGINX log
 *
 * Returns:
 *   NGX_OK on success
 *   NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_dynconf_start(ngx_http_markdown_dynconf_watcher_t *watcher,
                                ngx_cycle_t *cycle,
                                const ngx_str_t *path,
                                const ngx_log_t *log)
{
    ngx_file_info_t  fi;
    u_char           path_buf[NGX_MAX_PATH + 1];

    if (watcher == NULL || path == NULL || path->len == 0) {
        return NGX_OK;
    }

    if (path->len > NGX_MAX_PATH) {
        ngx_log_error(NGX_LOG_ERR, (ngx_log_t *) log, 0,
                      "markdown dynconf: path too long (%uz bytes)",
                      path->len);
        return NGX_ERROR;
    }

    /* Copy path to pool-owned NUL-terminated storage. */
    watcher->path.data = ngx_pnalloc(cycle->pool, path->len + 1);
    if (watcher->path.data == NULL) {
        return NGX_ERROR;
    }
    ngx_memcpy(watcher->path.data, path->data, path->len);
    watcher->path.data[path->len] = '\0';
    watcher->path.len = path->len;

    /* Stat the file to record baseline mtime. */
    ngx_memcpy(path_buf, path->data, path->len);
    path_buf[path->len] = '\0';

    if (ngx_file_info(path_buf, &fi) == NGX_FILE_ERROR) {
        ngx_log_error(NGX_LOG_WARN, (ngx_log_t *) log, 0,
                      "markdown dynconf: initial stat(\"%V\") failed, "
                      "will retry on timer",
                      path);
        watcher->last_mtime = 0;
    } else {
        watcher->last_mtime = ngx_file_mtime(fi);
    }

    /* Allocate the timer event from the cycle pool. */
    watcher->timer = ngx_pcalloc(cycle->pool, sizeof(ngx_event_t));
    if (watcher->timer == NULL) {
        return NGX_ERROR;
    }

    watcher->timer->handler = ngx_http_markdown_dynconf_timer_handler;
    watcher->timer->data = watcher;
    watcher->timer->log = (ngx_log_t *) log;

    watcher->active = 1;
    watcher->reload_pending = 0;

    ngx_add_timer(watcher->timer, NGX_HTTP_MARKDOWN_DYNCONF_WATCH_MS);

    ngx_log_error(NGX_LOG_INFO, (ngx_log_t *) log, 0,
                  "markdown dynconf: watching \"%V\" "
                  "(interval=%dms)",
                  &watcher->path, NGX_HTTP_MARKDOWN_DYNCONF_WATCH_MS);

    return NGX_OK;
}


/*
 * Stop the dynamic config watcher.
 *
 * Cancels the periodic timer and marks the watcher inactive.
 * Does not free the watcher itself (pool-owned).
 *
 * Parameters:
 *   watcher - Dynamic config watcher
 *   log     - NGINX log
 */
static void
ngx_http_markdown_dynconf_stop(ngx_http_markdown_dynconf_watcher_t *watcher,
                               const ngx_log_t *log)
{
    if (watcher == NULL || !watcher->active) {
        return;
    }

    if (watcher->timer != NULL && watcher->timer->timer_set) {
        ngx_del_timer(watcher->timer);
    }

    watcher->active = 0;
    watcher->reload_pending = 0;

    ngx_log_error(NGX_LOG_INFO, (ngx_log_t *) log, 0,
                  "markdown dynconf: stopped watching \"%V\"",
                  &watcher->path);
}


#endif /* NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H */
