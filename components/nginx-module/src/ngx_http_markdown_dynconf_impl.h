/*
 * NGINX Markdown Filter Module - Dynamic Configuration Hot-Reload
 *
 * Enables runtime modification of module configuration without
 * NGINX restart.  Uses a periodic timer event to poll the
 * configuration file for mtime changes, then reloads into a
 * staging snapshot.  Only if the entire file parses successfully
 * is the active snapshot replaced, guaranteeing atomicity.
 *
 * Architecture (v0.6.1 two-phase model):
 *   - Dedicated file watcher per worker process
 *   - Coarse-grained polling (1s interval via ngx_event_t timer)
 *   - On mtime change, the timer handler reads and parses the
 *     entire file into a staging snapshot.  If every line parses
 *     and applies successfully, the staging snapshot atomically
 *     replaces the active snapshot.  On any parse error the
 *     staging is discarded and the active snapshot is preserved.
 *   - The request path NEVER performs file I/O.  It reads only
 *     the active snapshot.  The header_filter binds the active
 *     snapshot pointer into the request context so that the
 *     entire request lifecycle uses a consistent configuration
 *     even if a concurrent reload swaps the active snapshot.
 *   - Grace-period drain for in-flight requests is handled by
 *     request-bound snapshot pointers: each request holds a
 *     pointer to the snapshot that was active when it entered
 *     the header filter.
 *
 * Requirements: v0.6.0 P2-10, v0.6.1 P0-1, P0-2, P1-2
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
 * Reload result codes for observability and logging.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED      0
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE     1
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE  2
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR      3

/*
 * Forward declaration for timer handler.
 */
static void ngx_http_markdown_dynconf_timer_handler(ngx_event_t *ev);

/*
 * Dynamic configuration snapshot.
 *
 * Contains only the fields that dynconf can modify at runtime.
 * This is the unit of atomicity: a reload either replaces the
 * entire snapshot or leaves it unchanged.
 */
typedef struct ngx_http_markdown_dynconf_snapshot_s {
    ngx_flag_t   enabled;
    ngx_uint_t   enabled_source;
    ngx_http_complex_value_t *enabled_complex;
    ngx_flag_t   prune_noise;
    ngx_uint_t   log_verbosity;
#ifdef MARKDOWN_STREAMING_ENABLED
    size_t       streaming_budget;
#endif
    size_t       memory_budget;
    ngx_uint_t   valid;
} ngx_http_markdown_dynconf_snapshot_t;

/*
 * Dynamic configuration file watcher and runtime.
 *
 * Holds the file path, last modification time, a periodic
 * timer event that polls for changes, and the two-phase
 * snapshot state (active + staging).
 */
typedef struct {
    ngx_str_t     path;
    time_t        last_mtime;
    ngx_event_t  *timer;
    ngx_uint_t    active;

    ngx_http_markdown_dynconf_snapshot_t  active_snapshot;
    ngx_http_markdown_dynconf_snapshot_t  staging_snapshot;
    ngx_uint_t    version;
    ngx_http_markdown_conf_t             *conf;
} ngx_http_markdown_dynconf_watcher_t;

static ngx_int_t ngx_http_markdown_dynconf_reload(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_http_markdown_conf_t *conf,
    ngx_log_t *log);


/**
 * Initialize a dynamic-configuration snapshot from the provided live module configuration.
 *
 * Copies runtime-modifiable fields from `conf` into `snapshot` and marks the snapshot as valid.
 *
 * @param snapshot Snapshot to populate; must be non-NULL.
 * @param conf Source module configuration to copy from; must be non-NULL.
 */
static void
ngx_http_markdown_dynconf_snapshot_from_conf(
    ngx_http_markdown_dynconf_snapshot_t *snapshot,
    const ngx_http_markdown_conf_t *conf)
{
    if (snapshot == NULL || conf == NULL) {
        return;
    }

    snapshot->enabled = conf->enabled;
    snapshot->enabled_source = conf->enabled_source;
    snapshot->enabled_complex = conf->enabled_complex;
    snapshot->prune_noise = conf->prune_noise;
    snapshot->log_verbosity = conf->log_verbosity;
#ifdef MARKDOWN_STREAMING_ENABLED
    snapshot->streaming_budget = conf->streaming_budget;
#endif
    snapshot->memory_budget = conf->memory_budget;
    snapshot->valid = 1;
}


/**
 * Apply a dynamic configuration snapshot to the live module configuration.
 *
 * Copies runtime-modifiable fields from `snapshot` into `conf`, making the
 * snapshot the active running state. No action is taken if `conf` or
 * `snapshot` is NULL or if `snapshot->valid` is false.
 *
 * @param conf Target configuration to update with snapshot values.
 * @param snapshot Source snapshot containing runtime-modifiable settings.
 */
static void
ngx_http_markdown_dynconf_apply_snapshot(
    ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_dynconf_snapshot_t *snapshot)
{
    if (conf == NULL || snapshot == NULL || !snapshot->valid) {
        return;
    }

    conf->enabled = snapshot->enabled;
    conf->enabled_source = snapshot->enabled_source;
    conf->enabled_complex = snapshot->enabled_complex;
    conf->prune_noise = snapshot->prune_noise;
    conf->log_verbosity = snapshot->log_verbosity;
#ifdef MARKDOWN_STREAMING_ENABLED
    conf->streaming_budget = snapshot->streaming_budget;
#endif
    conf->memory_budget = snapshot->memory_budget;
}


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
                                ngx_log_t *log)
{
    ngx_file_info_t  fi;

    if (watcher == NULL || !watcher->active) {
        return 0;
    }

    {
        u_char  path_buf[NGX_MAX_PATH + 1];

        if (watcher->path.len > NGX_MAX_PATH) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: path too long (%uz bytes)",
                          watcher->path.len);
            return 0;
        }

        ngx_memcpy(path_buf, watcher->path.data, watcher->path.len);
        path_buf[watcher->path.len] = '\0';

        if (ngx_file_info(path_buf, &fi) == NGX_FILE_ERROR) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: stat(\"%V\") failed",
                          &watcher->path);
            return 0;
        }
    }

    if (ngx_file_mtime(&fi) != watcher->last_mtime) {
        watcher->last_mtime = ngx_file_mtime(&fi);
        return 1;
    }

    return 0;
}


/**
 * Poll the dynamic-configuration watcher for file changes, log detected changes,
 * and re-arm the watch timer.
 *
 * This timer callback reads the watcher from ev->data, skips processing if the
 * watcher is NULL or not active, calls the change-detection routine and logs an
 * informational message if a change is observed, and re-arms the watch timer
 * for the next polling interval.
 *
 * @param ev Timer event whose `data` field points to the watcher; may be NULL.
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
        ngx_log_error(NGX_LOG_INFO, ev->log, 0,
                      "markdown dynconf: change detected on \"%V\", "
                      "performing two-phase reload",
                      &watcher->path);

        if (watcher->conf != NULL) {
            ngx_http_markdown_dynconf_reload(watcher, watcher->conf,
                                             ev->log);
        }
    }

    /* Re-arm the timer for the next poll cycle. */
    if (watcher->timer != NULL && !watcher->timer->timer_set) {
        ngx_add_timer(watcher->timer, NGX_HTTP_MARKDOWN_DYNCONF_WATCH_MS);
    }
}


/**
 * Start and initialize a dynamic configuration file watcher and seed its active snapshot.
 *
 * Allocates timer storage from the provided cycle pool, copies the watched file path
 * into pool-owned memory, records an initial file modification time (if stat succeeds),
 * initializes the active snapshot from the given configuration, and arms the periodic
 * watch timer.
 *
 * @param watcher Pre-allocated watcher structure to initialize (caller-owned storage).
 * @param cycle NGINX cycle used for pool allocations and timer registration.
 * @param path Path to the dynamic configuration file to watch.
 * @param conf Current module location configuration used to initialize the active snapshot.
 * @param log NGINX log for reporting warnings and informational messages.
 *
 * @return NGX_OK on success, NGX_ERROR on failure.
 */
static ngx_int_t
ngx_http_markdown_dynconf_start(ngx_http_markdown_dynconf_watcher_t *watcher,
                                ngx_cycle_t *cycle,
                                const ngx_str_t *path,
                                const ngx_http_markdown_conf_t *conf,
                                ngx_log_t *log)
{
    ngx_file_info_t  fi;
    u_char           path_buf[NGX_MAX_PATH + 1];

    /* Scope guard: dynconf supports only a single global watcher.
     * If the watcher is already active (started by a previous
     * location block), reject this attempt to prevent ambiguous
     * multi-location configurations.  The operator must use the
     * same dynconf_path at the http/server level or ensure only
     * one location enables dynconf. */
    if (watcher != NULL && watcher->active) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: watcher already active; "
                      "dynconf supports only a single global instance. "
                      "Ignoring markdown_dynamic_config_path \"%V\"",
                      path);
        return NGX_OK;
    }

    if (watcher == NULL || path == NULL || path->len == 0) {
        return NGX_OK;
    }

    if (path->len > NGX_MAX_PATH) {
        ngx_log_error(NGX_LOG_ERR, log, 0,
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
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: initial stat(\"%V\") failed, "
                      "will retry on timer",
                      path);
        watcher->last_mtime = 0;
    } else {
        watcher->last_mtime = ngx_file_mtime(&fi);
    }

    /* Allocate the timer event from the cycle pool. */
    watcher->timer = ngx_pcalloc(cycle->pool, sizeof(ngx_event_t));
    if (watcher->timer == NULL) {
        return NGX_ERROR;
    }

    watcher->timer->handler = ngx_http_markdown_dynconf_timer_handler;
    watcher->timer->data = watcher;
    watcher->timer->log = log;

    watcher->active = 1;
    watcher->version = 0;
    watcher->conf = (ngx_http_markdown_conf_t *) conf;

    /* Initialize active snapshot from current configuration. */
    ngx_http_markdown_dynconf_snapshot_from_conf(&watcher->active_snapshot,
                                                  conf);

    ngx_add_timer(watcher->timer, NGX_HTTP_MARKDOWN_DYNCONF_WATCH_MS);

    ngx_log_error(NGX_LOG_INFO, log, 0,
                  "markdown dynconf: watching \"%V\" "
                  "(interval=%dms)",
                  &watcher->path, NGX_HTTP_MARKDOWN_DYNCONF_WATCH_MS);

    return NGX_OK;
}


/**
 * Stops the dynamic configuration watcher.
 *
 * Cancels the watcher's periodic timer if set, marks the watcher as inactive,
 * and logs the stop event. The watcher object is not freed and remains
 * pool-owned.
 *
 * @param watcher Dynamic configuration watcher to stop; no action is taken if
 *                NULL or not active.
 * @param log     NGINX log used for informational messages.
 */
static void
ngx_http_markdown_dynconf_stop(ngx_http_markdown_dynconf_watcher_t *watcher,
                               ngx_log_t *log)
{
    if (watcher == NULL || !watcher->active) {
        return;
    }

    if (watcher->timer != NULL && watcher->timer->timer_set) {
        ngx_del_timer(watcher->timer);
    }

    watcher->active = 0;

    ngx_log_error(NGX_LOG_INFO, log, 0,
                  "markdown dynconf: stopped watching \"%V\"",
                  &watcher->path);
}


/*
 * Maximum line length in the dynamic config file.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE  1024

/*
 * Supported dynamic config keys and their enum values.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER          1
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE     2
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY   3
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_STREAMING_BUDGET 4
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET   5

/*
 * Match a config key name against known keys.
 *
 * Compares the key text between p and eq against the set of
 * recognized dynamic configuration keys.
 *
 * Parameters:
 *   p   - start of key text
 *   eq  - end of key text (one past last char)
 *   key - [out] matched key enum value
 *
 * Returns:
 *   NGX_OK on match, NGX_DECLINED if unrecognized
 */
static ngx_int_t
ngx_http_markdown_dynconf_match_key(u_char *p, const u_char *eq,
                                    ngx_uint_t *key)
{
    static u_char  markdown_filter_key[] = "markdown_filter";
    static u_char  prune_noise_key[] = "prune_noise";
    static u_char  log_verbosity_key[] = "log_verbosity";
    static u_char  streaming_budget_key[] = "streaming_budget";
    static u_char  memory_budget_key[] = "memory_budget";
    size_t  len;

    len = eq - p;

    if (len == 15 && ngx_strncasecmp(p, markdown_filter_key, 15) == 0) {
        *key = NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER;
    } else if (len == 11 && ngx_strncasecmp(p, prune_noise_key, 11) == 0) {
        *key = NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE;
    } else if (len == 13 && ngx_strncasecmp(p, log_verbosity_key, 13) == 0) {
        *key = NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY;
    } else if (len == 16 && ngx_strncasecmp(p, streaming_budget_key, 16) == 0) {
        *key = NGX_HTTP_MARKDOWN_DYNCONF_KEY_STREAMING_BUDGET;
    } else if (len == 13 && ngx_strncasecmp(p, memory_budget_key, 13) == 0) {
        *key = NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET;
    } else {
        return NGX_DECLINED;
    }

    return NGX_OK;
}


/*
 * Parse a key=value line from the dynamic config file.
 *
 * Lines starting with '#' are comments. Blank lines are skipped.
 * Supported keys:
 *   markdown_filter on|off
 *   prune_noise on|off
 *   log_verbosity error|warn|info|debug
 *   streaming_budget <size_with_unit>
 *   memory_budget <size_with_unit>
 *
 * Parameters:
 *   line     - line text (not NUL-terminated)
 *   line_len - line length
 *   key      - [out] parsed key enum
 *   value    - [out] parsed value string (points into line)
 *   value_len - [out] parsed value length
 *
 * Returns:
 *   NGX_OK on successful parse, NGX_DECLINED if comment/blank,
 *   NGX_ERROR on parse error
 */
static ngx_int_t
ngx_http_markdown_dynconf_parse_line(u_char *line, size_t line_len,
                                     ngx_uint_t *key,
                                     u_char **value, size_t *value_len)
{
    u_char        *p;
    const u_char  *last;
    u_char        *eq;

    p = line;
    last = line + line_len;

    /* Skip leading whitespace. */
    while (p < last && (*p == ' ' || *p == '\t')) {
        p++;
    }

    /* Blank line or comment. */
    if (p >= last || *p == '#') {
        return NGX_DECLINED;
    }

    /* Find '=' separator. */
    eq = p;
    while (eq < last && *eq != '=' && *eq != ' ' && *eq != '\t') {
        eq++;
    }

    if (eq >= last) {
        return NGX_ERROR;
    }

    /* Match key. */
    if (ngx_http_markdown_dynconf_match_key(p, eq, key) != NGX_OK) {
        return NGX_DECLINED;
    }

    /* Skip to '=' sign. */
    p = eq;
    while (p < last && *p != '=') {
        p++;
    }

    if (p >= last) {
        return NGX_ERROR;
    }
    p++; /* skip '=' */

    /* Skip whitespace after '='. */
    while (p < last && (*p == ' ' || *p == '\t')) {
        p++;
    }

    /* Trim trailing whitespace from value. */
    *value = p;
    *value_len = last - p;

    while (*value_len > 0
           && ((*value)[*value_len - 1] == ' '
               || (*value)[*value_len - 1] == '\t'
               || (*value)[*value_len - 1] == '\r'))
    {
        (*value_len)--;
    }

    if (*value_len == 0) {
        return NGX_ERROR;
    }

    return NGX_OK;
}


/*
 * Apply a single parsed key=value pair to a staging snapshot.
 *
 * Only runtime-safe fields are modified.  Fields that require
 * structural changes (content_types, stream_types, etc.) are
 * not supported via dynamic config and must be changed via
 * nginx -s reload.
 *
 * Parameters:
 *   snapshot  - Staging snapshot to update
 *   key       - Parsed key enum
 *   value     - Value string
 *   value_len - Value length
 *   log       - NGINX log
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on invalid value,
 *   NGX_DECLINED for unrecognized key
 */
static ngx_int_t
ngx_http_markdown_dynconf_apply(ngx_http_markdown_dynconf_snapshot_t *snapshot,
                                ngx_uint_t key,
                                u_char *value, size_t value_len,
                                ngx_log_t *log)
{
    /* Canonical string constants for on/off and log-level matching.
     * Length-bounded ngx_strncasecmp avoids NUL-termination dependency. */
    static u_char  on_value[] = "on";
    static u_char  off_value[] = "off";
    static u_char  error_value[] = "error";
    static u_char  warn_value[] = "warn";
    static u_char  info_value[] = "info";
    static u_char  debug_value[] = "debug";

    switch (key) {

    /* Toggle the markdown filter on or off for the current location.
     * Overrides the entire enabled/enabled_source/enabled_complex
     * triple so that a dynconf on/off always acts as a static
     * directive, regardless of whether the original nginx config
     * used a complex value ($variable).  Without this, a prior
     * enabled_source==COMPLEX + enabled_complex!=NULL would cause
     * ngx_http_markdown_is_enabled() to re-evaluate the variable
     * and ignore the dynconf change entirely. */
    case NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER:
        if (value_len == 2 && ngx_strncasecmp(value, on_value, 2) == 0) {
            snapshot->enabled = 1;
            snapshot->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
            snapshot->enabled_complex = NULL;
        } else if (value_len == 3 && ngx_strncasecmp(value, off_value, 3) == 0) {
            snapshot->enabled = 0;
            snapshot->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
            snapshot->enabled_complex = NULL;
        } else {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: invalid markdown_filter value \"%*s\"",
                          (int) value_len, value);
            return NGX_ERROR;
        }
        break;

    /* Toggle noise pruning (boilerplate removal) on or off. */
    case NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE:
        if (value_len == 2 && ngx_strncasecmp(value, on_value, 2) == 0) {
            snapshot->prune_noise = 1;
        } else if (value_len == 3 && ngx_strncasecmp(value, off_value, 3) == 0) {
            snapshot->prune_noise = 0;
        } else {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: invalid prune_noise value \"%*s\"",
                          (int) value_len, value);
            return NGX_ERROR;
        }
        break;

    /* Set the decision-log verbosity: error, warn, info, or debug.
     * Maps the string to the module-local verbosity enum
     * (NGX_HTTP_MARKDOWN_LOG_*), NOT to NGINX's NGX_LOG_* constants.
     * The bridge function ngx_http_markdown_log_verbosity_to_ngx_level()
     * converts to NGX_LOG_* at the actual ngx_log_error() call site. */
    case NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY:
        if (value_len == 5 && ngx_strncasecmp(value, error_value, 5) == 0) {
            snapshot->log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
        } else if (value_len == 4 && ngx_strncasecmp(value, warn_value, 4) == 0) {
            snapshot->log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
        } else if (value_len == 4 && ngx_strncasecmp(value, info_value, 4) == 0) {
            snapshot->log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
        } else if (value_len == 5 && ngx_strncasecmp(value, debug_value, 5) == 0) {
            snapshot->log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
        } else {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: invalid log_verbosity value \"%*s\"",
                          (int) value_len, value);
            return NGX_ERROR;
        }
        break;

    /* Set the streaming working-set budget (size value, e.g. "64k").
     * Only available when compiled with MARKDOWN_STREAMING_ENABLED. */
    case NGX_HTTP_MARKDOWN_DYNCONF_KEY_STREAMING_BUDGET:
#ifdef MARKDOWN_STREAMING_ENABLED
        {
            ngx_str_t   val;
            ssize_t     parsed;

            val.data = value;
            val.len = value_len;
            parsed = ngx_parse_size(&val);
            if (parsed == NGX_ERROR) {
                ngx_log_error(NGX_LOG_WARN, log, 0,
                              "markdown dynconf: invalid streaming_budget value \"%*s\"",
                              (int) value_len, value);
                return NGX_ERROR;
            }
            snapshot->streaming_budget = (size_t) parsed;
        }
#else
        /* Streaming not compiled in; reject with a diagnostic. */
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: streaming_budget not supported "
                      "(streaming not compiled)");
#endif
        break;

    /* Set the total memory budget for full-buffer conversion (size value). */
    case NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET:
        {
            ngx_str_t   val;
            ssize_t     parsed;

            val.data = value;
            val.len = value_len;
            parsed = ngx_parse_size(&val);
            if (parsed == NGX_ERROR) {
                ngx_log_error(NGX_LOG_WARN, log, 0,
                              "markdown dynconf: invalid memory_budget value \"%*s\"",
                              (int) value_len, value);
                return NGX_ERROR;
            }
            snapshot->memory_budget = (size_t) parsed;
        }
        break;

    /* Unrecognized key — caller decides how to handle. */
    default:
        return NGX_DECLINED;
    }

    return NGX_OK;
}


/*
 * Compute the length of a config line, stripping a trailing \r.
 *
 * Parameters:
 *   buf        - buffer containing the line
 *   line_start - start offset of the line in buf
 *   line_end   - offset of the newline (or end) in buf
 *
 * Returns:
 *   Line length with trailing \r stripped
 */
static size_t
ngx_http_markdown_dynconf_line_len(const u_char *buf, size_t line_start,
                                   size_t line_end)
{
    size_t  line_len;

    line_len = line_end - line_start;
    if (line_len > 0 && buf[line_start + line_len - 1] == '\r') {
        line_len--;
    }

    return line_len;
}


/**
 * Attempt to parse and apply a single dynamic config line to a staging snapshot.
 *
 * Parses the provided line and, if it yields a recognized key/value pair, applies it to the staging snapshot and increments `*applied` when the apply succeeds.
 * @param snapshot Staging snapshot to update.
 * @param line Pointer to the line buffer (may not be NUL-terminated).
 * @param len Length of the line in bytes.
 * @param log NGINX log for reporting parse/apply warnings.
 * @param applied In/out pointer to a counter of successfully applied entries; incremented when a key is applied.
 * @returns `NGX_OK` if the line was skipped or applied successfully, `NGX_ERROR` if parsing or applying failed (caller should abort the reload).
 */
static ngx_int_t
ngx_http_markdown_dynconf_try_line(ngx_http_markdown_dynconf_snapshot_t *snapshot,
                                   u_char *line, size_t len,
                                   ngx_log_t *log,
                                   ngx_uint_t *applied)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   parse_rc;
    ngx_int_t   apply_rc;

    parse_rc = ngx_http_markdown_dynconf_parse_line(line, len,
                                                     &key, &value, &value_len);
    if (parse_rc == NGX_DECLINED) {
        return NGX_OK;
    }

    if (parse_rc != NGX_OK) {
        return NGX_ERROR;
    }

    apply_rc = ngx_http_markdown_dynconf_apply(snapshot, key, value,
                                                value_len, log);
    if (apply_rc == NGX_OK) {
        (*applied)++;
        return NGX_OK;
    }

    if (apply_rc == NGX_DECLINED) {
        return NGX_OK;
    }

    return NGX_ERROR;
}


/**
 * Perform a two-phase reload of dynamic configuration from the watcher's file.
 *
 * Reads the entire file into a staging snapshot, parses and applies every line
 * into that staging snapshot, and on complete success atomically replaces the
 * watcher's active snapshot and applies it to the provided live configuration.
 *
 * @param watcher Dynamic config watcher containing the file path and snapshots.
 * @param conf Current module location configuration to update when commit succeeds.
 * @param log Logger used for warnings and informational messages.
 *
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED      if one or more settings were applied and committed
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE    if the file contained no effective keys to apply
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE if any line failed to parse or a line was too long
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR     on file open/read failures or invalid inputs
 */
static ngx_int_t
ngx_http_markdown_dynconf_reload(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_http_markdown_conf_t *conf,
    ngx_log_t *log)
{
    u_char       path_buf[NGX_MAX_PATH + 1];
    ngx_fd_t     fd;
    u_char       buf[NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE];
    ssize_t      n;
    size_t       line_start;
    size_t       pos;
    ngx_uint_t   applied;
    ngx_int_t    line_rc;

    if (watcher == NULL || conf == NULL || log == NULL) {
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    if (watcher->path.len > NGX_MAX_PATH) {
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    ngx_memcpy(path_buf, watcher->path.data, watcher->path.len);
    path_buf[watcher->path.len] = '\0';

    fd = ngx_open_file(path_buf, NGX_FILE_RDONLY, NGX_FILE_OPEN, 0);
    if (fd == NGX_INVALID_FILE) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: failed to open \"%V\" for reload",
                      &watcher->path);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    /* Initialize staging snapshot from current active snapshot.
     * This ensures that keys not present in the dynconf file
     * retain their current values (incremental override). */
    watcher->staging_snapshot = watcher->active_snapshot;

    applied = 0;
    line_start = 0;
    pos = 0;

    for ( ;; ) {
        n = ngx_read_fd(fd, buf + pos, sizeof(buf) - pos);
        if (n == 0) {
            break;
        }

        if (n == -1) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: read error on \"%V\"",
                          &watcher->path);
            ngx_close_file(fd);
            return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
        }

        pos += (size_t) n;

        /* Process complete lines. */
        for ( ;; ) {
            size_t  i;

            /* Find newline. */
            i = line_start;
            while (i < pos && buf[i] != '\n') {
                i++;
            }

            if (i >= pos) {
                /* No complete line; shift remaining data. */
                ngx_memmove(buf, buf + line_start, pos - line_start);
                pos -= line_start;
                line_start = 0;
                break;
            }

            line_rc = ngx_http_markdown_dynconf_try_line(
                &watcher->staging_snapshot, buf + line_start,
                ngx_http_markdown_dynconf_line_len(buf, line_start, i),
                log, &applied);

            if (line_rc != NGX_OK) {
                ngx_log_error(NGX_LOG_WARN, log, 0,
                              "markdown dynconf: parse error in \"%V\", "
                              "discarding staging; active config unchanged",
                              &watcher->path);
                ngx_close_file(fd);
                return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
            }

            line_start = i + 1;
        }

        if (pos >= sizeof(buf)) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: line too long in \"%V\", "
                          "discarding staging; active config unchanged",
                          &watcher->path);
            ngx_close_file(fd);
            return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        }
    }

    ngx_close_file(fd);

    /*
     * Process final line if the file does not end with a newline.
     * When EOF is reached with unprocessed data between line_start
     * and pos, that data constitutes the final line.
     */
    if (line_start < pos) {
        line_rc = ngx_http_markdown_dynconf_try_line(
            &watcher->staging_snapshot, buf + line_start,
            ngx_http_markdown_dynconf_line_len(buf, line_start, pos),
            log, &applied);

        if (line_rc != NGX_OK) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: parse error on final line "
                          "in \"%V\", discarding staging",
                          &watcher->path);
            return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        }
    }

    if (applied > 0) {
        /* All lines parsed successfully: commit staging to active. */
        watcher->active_snapshot = watcher->staging_snapshot;
        watcher->version++;

        /* Apply the new active snapshot to the live conf. */
        ngx_http_markdown_dynconf_apply_snapshot(conf,
                                                  &watcher->active_snapshot);

        ngx_log_error(NGX_LOG_INFO, log, 0,
                      "markdown dynconf: applied %ui settings from \"%V\" "
                      "(version=%ui)",
                      applied, &watcher->path, watcher->version);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED;
    }

    return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE;
}


#endif /* NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H */
