/*
 * NGINX Markdown Filter Module - Dynamic Configuration Hot-Reload
 *
 * Enables runtime modification of module configuration without
 * NGINX restart.  Uses a periodic timer event to poll the
 * configuration file for mtime changes, then reloads into a
 * staging snapshot.  Only if the entire file parses successfully
 * is the active snapshot replaced, guaranteeing atomicity.
 *
 * Architecture (v0.6.2 effective-conf model):
 *   - Dedicated file watcher per worker process
 *   - Coarse-grained polling (1s interval via ngx_event_t timer)
 *   - On mtime change, the timer handler reads and parses the
 *     entire file into a staging snapshot.  If every line parses
 *     and applies successfully, the staging snapshot atomically
 *     replaces the active snapshot.  On any parse error the
 *     staging is discarded and the active snapshot is preserved.
 *   - The request path NEVER performs file I/O.  The header_filter
 *     copies the active snapshot into request-pool memory and
 *     builds an effective_conf view from that copy, so that the
 *     entire request lifecycle uses a consistent configuration
 *     even if a concurrent reload swaps the global active_snapshot.
 *   - Grace-period drain for in-flight requests is handled by
 *     request-owned snapshot copies: each request holds its own
 *     copy of the snapshot that was active when it entered the
 *     header filter.
 *
 * Requirements: v0.6.0 P2-10, v0.6.1 P0-1, P0-2, P1-2
 */

#ifndef NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H
#define NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H

#include <stdlib.h>


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
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK    4
#define NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL  5

/*
 * Rollback result codes.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK          0
#define NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_NO_LKG      1
#define NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_APPLY_ERR   2

/*
 * Maximum number of validation errors collected during dry-run.
 * Prevents unbounded allocation; additional errors beyond this
 * cap are counted but not stored.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_MAX_ERRORS  32

/*
 * Maximum length for field name in a validation error entry.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_ERR_FIELD_MAX  64

/*
 * Maximum length for error reason in a validation error entry.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_ERR_REASON_MAX  128

/*
 * Single validation error entry captured during dry-run parsing.
 *
 * Each entry records the line number where the error occurred,
 * the field/key name that caused the error, and a human-readable
 * reason string.  Field and reason are stored as fixed-size
 * buffers to avoid heap allocation in the request path.
 */
typedef struct {
    ngx_uint_t   line;
    u_char       field[NGX_HTTP_MARKDOWN_DYNCONF_ERR_FIELD_MAX];
    size_t       field_len;
    u_char       reason[NGX_HTTP_MARKDOWN_DYNCONF_ERR_REASON_MAX];
    size_t       reason_len;
} ngx_http_markdown_dynconf_validation_error_t;

/*
 * Validation result from a dry-run reload attempt.
 *
 * Collects up to NGX_HTTP_MARKDOWN_DYNCONF_MAX_ERRORS detailed
 * error entries.  If more errors are found, total_errors tracks
 * the true count while only the first MAX_ERRORS entries are
 * stored.  The diagnostics endpoint can read this struct to
 * report validation failures to operators.
 */
typedef struct {
    ngx_http_markdown_dynconf_validation_error_t
        errors[NGX_HTTP_MARKDOWN_DYNCONF_MAX_ERRORS];
    ngx_uint_t   count;         /* stored entries (<=MAX_ERRORS) */
    ngx_uint_t   total_errors;  /* total errors found (may exceed count) */
    ngx_uint_t   valid;         /* 1 if last dry-run passed, 0 if failed */
} ngx_http_markdown_dynconf_validation_result_t;

/*
 * Forward declaration for timer handler.
 */
static void ngx_http_markdown_dynconf_timer_handler(ngx_event_t *ev);

/*
 * Dynconf snapshot — a point-in-time copy of all runtime-modifiable
 * configuration fields.  Captured once per request at header_filter
 * time and stored in ctx->dynconf_snapshot.  The effective_conf view
 * is derived from this snapshot (or live conf as fallback).
 *
 * The snapshot guarantees that a request sees a consistent set of
 * values even if a concurrent timer reload swaps the global
 * active_snapshot mid-request.
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
 * Effective configuration view for per-request consistency.
 *
 * Contains only the fields that dynconf can modify at runtime.
 * Built once at header_filter time from the dynconf snapshot (if valid)
 * or from the live static conf.  Request-lifetime code reads mutable
 * fields through this struct to guarantee mid-request consistency even
 * when a concurrent timer reload swaps the global active_snapshot.
 *
 * Struct definition is in ngx_http_markdown_filter_module.h so that
 * all translation units (including test binaries) can access fields
 * without depending on this impl header's NGINX API requirements.
 */

/*
 * Dynamic configuration file watcher and runtime.
 *
 * Holds the file path, last modification time, a periodic
 * timer event that polls for changes, and the two-phase
 * snapshot state (active + staging).
 *
 * last_mtime tracks the most recently observed file mtime
 * (updated on stat, even if the subsequent reload fails).
 * applied_mtime tracks the mtime of the last successfully
 * applied reload.  When last_mtime != applied_mtime, a
 * reload attempt is needed (either for the first time or
 * as a retry after a previous failure).  This separation
 * ensures that a failed reload does not prevent the timer
 * from retrying on the next poll cycle.
 *
 * last_known_good holds the previous active snapshot that was
 * replaced by the most recent successful reload.  When
 * lkg_valid is set, the operator can trigger a rollback to
 * restore the last-known-good configuration (see E04.2).
 * The LKG is NOT updated on validation failure — only a
 * successful reload promotes the current active to LKG.
 */
typedef struct {
    ngx_str_t     path;
    time_t        last_mtime;
    time_t        applied_mtime;
    ngx_event_t  *timer;
    ngx_uint_t    active;

    ngx_http_markdown_dynconf_snapshot_t  active_snapshot;
    ngx_http_markdown_dynconf_snapshot_t  staging_snapshot;
    ngx_http_markdown_dynconf_snapshot_t  last_known_good;
    ngx_uint_t    lkg_valid;
    ngx_uint_t    version;
    ngx_http_markdown_conf_t             *conf;

    /* Last dry-run validation result; populated when dry-run mode
     * is active and a reload attempt occurs.  Available for the
     * diagnostics endpoint to report validation failures. */
    ngx_http_markdown_dynconf_validation_result_t  last_validation;
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
    snapshot->prune_noise = conf->advanced.prune_noise;
    snapshot->log_verbosity = conf->policy.log_verbosity;
#ifdef MARKDOWN_STREAMING_ENABLED
    snapshot->streaming_budget = conf->streaming.budget;
#endif
    snapshot->memory_budget = conf->advanced.memory_budget;
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
    conf->advanced.prune_noise = snapshot->prune_noise;
    conf->policy.log_verbosity = snapshot->log_verbosity;
#ifdef MARKDOWN_STREAMING_ENABLED
    conf->streaming.budget = snapshot->streaming_budget;
#endif
    conf->advanced.memory_budget = snapshot->memory_budget;
}


/**
 * Build the effective configuration view from a dynconf snapshot and live conf.
 *
 * If the snapshot is non-NULL and valid, its values take precedence.
 * Otherwise, the corresponding fields from the live conf are used.
 * The resulting view is stored in the caller-provided struct (typically
 * allocated from the request pool and hung off ctx->effective_conf).
 *
 * @param eff  Target effective config view to populate; must be non-NULL.
 * @param snap Dynconf snapshot bound to this request; may be NULL.
 * @param conf Live module configuration for fallback; must be non-NULL.
 */
static void
ngx_http_markdown_build_effective_conf(
    ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_dynconf_snapshot_t *snap,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff == NULL || conf == NULL) {
        return;
    }

    if (snap != NULL && snap->valid) {
        eff->enabled        = snap->enabled;
        eff->enabled_source = snap->enabled_source;
        eff->prune_noise    = snap->prune_noise;
        eff->log_verbosity  = snap->log_verbosity;
        eff->memory_budget  = snap->memory_budget;
#ifdef MARKDOWN_STREAMING_ENABLED
        eff->streaming_budget = snap->streaming_budget;
#endif
    } else {
        eff->enabled        = conf->enabled;
        eff->enabled_source = conf->enabled_source;
        eff->prune_noise    = conf->advanced.prune_noise;
        eff->log_verbosity  = conf->policy.log_verbosity;
        eff->memory_budget  = conf->advanced.memory_budget;
#ifdef MARKDOWN_STREAMING_ENABLED
        eff->streaming_budget = conf->streaming.budget;
#endif
    }
}


/**
 * Read effective log_verbosity for a request.
 *
 * Prefers the effective_conf view bound to ctx; falls back to live conf
 * if the view is unavailable.
 */
static ngx_uint_t
ngx_http_markdown_effective_log_verbosity(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->log_verbosity;
    }
    return conf->policy.log_verbosity;
}


/**
 * Read effective prune_noise for a request.
 */
static ngx_flag_t
ngx_http_markdown_effective_prune_noise(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->prune_noise;
    }
    return conf->advanced.prune_noise;
}


/**
 * Read effective memory_budget for a request.
 */
static size_t
ngx_http_markdown_effective_memory_budget(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->memory_budget;
    }
    return conf->advanced.memory_budget;
}


#ifdef MARKDOWN_STREAMING_ENABLED
/**
 * Read effective streaming_budget for a request.
 */
static size_t
ngx_http_markdown_effective_streaming_budget(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->streaming_budget;
    }
    return conf->streaming.budget;
}
#endif


/**
 * Read effective enabled flag for a request.
 */
static ngx_flag_t
ngx_http_markdown_effective_enabled(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->enabled;
    }
    return conf->enabled;
}


/**
 * Read effective enabled_source for a request.
 */
static ngx_uint_t
ngx_http_markdown_effective_enabled_source(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->enabled_source;
    }
    return conf->enabled_source;
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
 * After a file change is detected (dynconf_check returns 1), a two-phase
 * reload is attempted.  If the reload succeeds (RELOAD_APPLIED or
 * RELOAD_NO_CHANGE), applied_mtime is updated to last_mtime, confirming
 * the reload.  If the reload fails (INVALID_FILE or IO_ERROR),
 * applied_mtime is NOT updated, so the next timer cycle will see
 * last_mtime != applied_mtime and retry the reload — matching the
 * failure-retry contract described in
 * docs/harness/risk-packs/dynamic-config-hot-reload.md.
 *
 * Additionally, even when no new file change is detected (dynconf_check
 * returns 0), if last_mtime != applied_mtime (indicating a previous
 * failed reload), a retry is attempted.  This covers the case where
 * the file content was invalid but the mtime has not changed since.
 *
 * @param ev Timer event whose `data` field points to the watcher; may be NULL.
 */
static void
ngx_http_markdown_dynconf_timer_handler(ngx_event_t *ev)
{
    ngx_http_markdown_dynconf_watcher_t  *watcher;
    ngx_int_t                            reload_rc;

    watcher = ev->data;

    if (watcher == NULL || !watcher->active) {
        return;
    }

    reload_rc = NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE;

    if (ngx_http_markdown_dynconf_check(watcher, ev->log)) {
        ngx_log_error(NGX_LOG_INFO, ev->log, 0,
                      "markdown dynconf: change detected on \"%V\", "
                      "performing two-phase reload",
                      &watcher->path);

        if (watcher->conf != NULL) {
            reload_rc = ngx_http_markdown_dynconf_reload(watcher,
                                                          watcher->conf,
                                                          ev->log);
        }
    } else if (watcher->last_mtime != watcher->applied_mtime) {
        /*
         * No new mtime change detected, but a previous reload failed
         * (last_mtime advanced but applied_mtime did not).  Retry the
         * reload so that transient errors (e.g. briefly invalid file
         * content) are eventually resolved without operator intervention.
         */
        ngx_log_error(NGX_LOG_INFO, ev->log, 0,
                      "markdown dynconf: retrying failed reload on \"%V\" "
                      "(last_mtime=%T, applied_mtime=%T)",
                      &watcher->path,
                      watcher->last_mtime, watcher->applied_mtime);

        if (watcher->conf != NULL) {
            reload_rc = ngx_http_markdown_dynconf_reload(watcher,
                                                          watcher->conf,
                                                          ev->log);
        }
    }

    /*
     * Update applied_mtime only after a successful reload.
     * RELOAD_APPLIED: new settings committed.
     * RELOAD_NO_CHANGE: file parsed successfully but contained no
     *   effective keys — still a successful parse, so confirm.
     * RELOAD_DRY_RUN_OK: validation passed but settings were NOT
     *   applied (dry-run mode); do NOT update applied_mtime so
     *   the next timer cycle will re-validate if the file changes.
     * RELOAD_DRY_RUN_FAIL: validation found errors; update
     *   applied_mtime to suppress repeated re-validation of the
     *   same file content (errors are stored in last_validation).
     * INVALID_FILE / IO_ERROR: reload failed; applied_mtime stays
     *   at its previous value so the next timer cycle will retry.
     */
    if (reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED
        || reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE)
    {
        watcher->applied_mtime = watcher->last_mtime;
    } else if (reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK
               || reload_rc
                  == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL)
    {
        /*
         * Dry-run mode (pass or fail): update applied_mtime to
         * suppress repeated re-validation of the same file content
         * on every timer cycle.  For DRY_RUN_FAIL, the errors are
         * stored in watcher->last_validation and have already been
         * logged; re-validating the same content would produce
         * identical results.
         */
        watcher->applied_mtime = watcher->last_mtime;
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
 * If the dynconf file already exists at startup, its contents are parsed and applied
 * immediately so that runtime overrides persist across NGINX restart/reload.  If the
 * initial parse fails, the watcher still starts (using static conf as the baseline)
 * but applied_mtime is set to 0 so the timer will retry the reload on the next cycle.
 *
 * @param watcher Pre-allocated watcher structure to initialize (caller-owned storage).
 * @param cycle NGINX cycle used for pool allocations and timer registration.
 * @param path Path to the dynamic configuration file to watch.
 * @param conf Current module location configuration (mutable; dynconf applies snapshot writes via reload).
 * @param log NGINX log for reporting warnings and informational messages.
 *
 * @return NGX_OK on success, NGX_ERROR on failure.
 */
static ngx_int_t
ngx_http_markdown_dynconf_start(ngx_http_markdown_dynconf_watcher_t *watcher,
                                ngx_cycle_t *cycle,
                                const ngx_str_t *path,
                                ngx_http_markdown_conf_t *conf,
                                ngx_log_t *log)
{
    ngx_file_info_t  fi;
    u_char           path_buf[NGX_MAX_PATH + 1];
    ngx_int_t        initial_rc;

    /* Scope guard: dynconf supports only a single global watcher.
     * If the watcher is already active (started by a previous
     * location block), reject this attempt to prevent ambiguous
     * multi-location configurations.
     *
     * Primary enforcement is at config-parse time via
     * ngx_http_markdown_set_dynconf_path(), which returns
     * NGX_CONF_ERROR on duplicate.  This runtime check is a
     * defensive fallback in case a code path bypasses the
     * config handler. */
    if (watcher != NULL && watcher->active) {
        ngx_log_error(NGX_LOG_ERR, log, 0,
                      "markdown dynconf: watcher already active; "
                      "dynconf supports only a single global instance. "
                      "Rejecting duplicate markdown_dynamic_config_path \"%V\"",
                      path);
        return NGX_ERROR;
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
        watcher->applied_mtime = 0;
    } else {
        watcher->last_mtime = ngx_file_mtime(&fi);
        watcher->applied_mtime = watcher->last_mtime;
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
    watcher->lkg_valid = 0;
    watcher->conf = conf;

    /* Initialize active snapshot from current configuration. */
    ngx_http_markdown_dynconf_snapshot_from_conf(&watcher->active_snapshot,
                                                  conf);

    /*
     * Apply the dynconf file immediately at startup so that runtime
     * overrides persist across NGINX restart/reload.  If the file
     * exists and parses successfully, the active snapshot and live
     * conf are updated before any request arrives.  If the initial
     * parse fails, the watcher still starts with the static conf
     * baseline but applied_mtime is set to 0 so the timer will
     * retry on the next poll cycle.
     */
    if (watcher->last_mtime != 0) {
        initial_rc = ngx_http_markdown_dynconf_reload(watcher, conf, log);
        if (initial_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED
            || initial_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE)
        {
            watcher->applied_mtime = watcher->last_mtime;
            ngx_log_error(NGX_LOG_INFO, log, 0,
                          "markdown dynconf: applied existing file on startup "
                          "(rc=%i, version=%ui)",
                          initial_rc, watcher->version);
        } else if (initial_rc
                   == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK)
        {
            watcher->applied_mtime = watcher->last_mtime;
            ngx_log_error(NGX_LOG_INFO, log, 0,
                          "markdown dynconf: dry-run validation passed "
                          "on startup (rc=%i, not applied)",
                          initial_rc);
        } else if (initial_rc
                   == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL)
        {
            watcher->applied_mtime = watcher->last_mtime;
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: dry-run validation failed "
                          "on startup (rc=%i, %ui errors found)",
                          initial_rc,
                          watcher->last_validation.total_errors);
        } else {
            watcher->applied_mtime = 0;
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown dynconf: initial reload of \"%V\" failed "
                          "(rc=%i); starting from static conf, will retry on timer",
                          &watcher->path, initial_rc);
        }
    }

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
 *   NGX_OK on match, NGX_ERROR if unrecognized
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
        return NGX_ERROR;
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
 *   NGX_ERROR on parse error or unrecognized key
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
        return NGX_ERROR;
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
 * Fallback definition for NGX_MAX_SIZE_T_VALUE when building
 * outside the NGINX compilation environment (e.g. unit tests).
 * NGINX core defines this in ngx_config.h.
 */
#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE  ((size_t) -1)
#endif

/**
 * Safely parse a size value string and validate for assignment to a size_t field.
 *
 * Performs the complete "parse + validate + safe-cast" sequence to prevent
 * CWE-190 integer overflow when converting ssize_t (signed) results from
 * ngx_parse_size() to size_t (unsigned) snapshot fields.
 *
 * Validation checks:
 *   1. ngx_parse_size() must succeed (not return NGX_ERROR)
 *   2. Result must be non-negative (no negative ssize_t values)
 *   3. Result must not exceed max_size_t (caller-specified upper bound;
 *      pass SIZE_MAX to allow any non-negative value)
 *
 * On failure, logs a diagnostic with the key name, raw input, and rejection
 * reason, and does NOT modify the snapshot field.
 *
 * @param value      Value string (not NUL-terminated)
 * @param value_len  Length of value string
 * @param key_name   Human-readable key name for error messages
 * @param max_size_t Maximum allowed value (e.g. NGX_MAX_SIZE_T_VALUE or SIZE_MAX)
 * @param log        NGINX log for error messages
 * @param[out] out   Output size_t value; only written on NGX_OK return
 *
 * @returns NGX_OK on success (out populated), NGX_ERROR on any validation failure
 */
static ngx_int_t
ngx_http_markdown_dynconf_parse_size_safe(const u_char *value, size_t value_len,
                                           const char *key_name,
                                           size_t max_size_t,
                                           ngx_log_t *log,
                                           size_t *out)
{
    ngx_str_t   val;
    u_char     *scratch;
    ssize_t     parsed;

    scratch = malloc(value_len);
    if (scratch == NULL) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: invalid %s value \"%*s\" "
                      "(allocation failure)", key_name,
                      (int) value_len, value);
        return NGX_ERROR;
    }
    ngx_memcpy(scratch, value, value_len);
    val.data = scratch;
    val.len = value_len;

    parsed = ngx_parse_size(&val);
    free(scratch);

    if (parsed == NGX_ERROR) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: invalid %s value \"%*s\" "
                      "(parse error)", key_name,
                      (int) value_len, value);
        return NGX_ERROR;
    }

    if (parsed < 0) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: invalid %s value \"%*s\" "
                      "(negative result: %z)", key_name,
                      (int) value_len, value, parsed);
        return NGX_ERROR;
    }

    if ((size_t) parsed > max_size_t) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: invalid %s value \"%*s\" "
                      "(exceeds maximum: %z > %z)", key_name,
                      (int) value_len, value,
                      (size_t) parsed, max_size_t);
        return NGX_ERROR;
    }

    *out = (size_t) parsed;  /* guarded by parsed >= 0 check above */
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
 *   NGX_OK on success, NGX_ERROR on invalid value or unrecognized key
 *   (atomic reload rejection)
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
            size_t  budget;

            if (ngx_http_markdown_dynconf_parse_size_safe(
                    value, value_len, "streaming_budget",
                    NGX_MAX_SIZE_T_VALUE, log, &budget) != NGX_OK)
            {
                return NGX_ERROR;
            }
            snapshot->streaming_budget = budget;
        }
#else
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: streaming_budget not supported "
                      "(streaming not compiled)");
        return NGX_ERROR;
#endif
        break;

    /* Set the total memory budget for full-buffer conversion (size value). */
    case NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET:
        {
            size_t  budget;

            if (ngx_http_markdown_dynconf_parse_size_safe(
                    value, value_len, "memory_budget",
                    NGX_MAX_SIZE_T_VALUE, log, &budget) != NGX_OK)
            {
                return NGX_ERROR;
            }
            snapshot->memory_budget = budget;
        }
        break;

    /* Unrecognized key — should not reach here because match_key
     * rejects unknown keys before apply is called.  Return error
     * as a defensive fallback. */
    default:
        return NGX_ERROR;
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

    return NGX_ERROR;
}


/*
 * Process complete lines from the read buffer.
 *
 * Scans for newline-terminated lines in buf[line_start..pos), calls
 * try_line for each, and updates line_start.  Returns NGX_OK if all
 * lines were processed, NGX_ERROR on a parse failure (caller should
 * abort the reload).  When no complete line remains, shifts
 * unprocessed data to the front of buf and updates pos/line_start.
 *
 * @param snapshot   Staging snapshot to apply lines to.
 * @param buf        Read buffer.
 * @param pos        [in/out] Current end of data in buf.
 * @param line_start [in/out] Start of next unprocessed line.
 * @param log        Logger.
 * @param applied    [in/out] Count of successfully applied entries.
 * @returns NGX_OK on success, NGX_ERROR on parse failure.
 */
static ngx_int_t
ngx_http_markdown_dynconf_process_buffer(
    ngx_http_markdown_dynconf_snapshot_t *snapshot,
    u_char *buf, size_t *pos, size_t *line_start,
    ngx_log_t *log, ngx_uint_t *applied)
{
    for ( ;; ) {
        size_t  i;

        /* Find newline. */
        i = *line_start;
        while (i < *pos && buf[i] != '\n') {
            i++;
        }

        if (i >= *pos) {
            /* No complete line; shift remaining data. */
            /* Defensive: line_start must not exceed pos (invariant from
             * the loop above where i starts at *line_start and advances
             * to *pos), but guard explicitly for static analysis. */
            if (*line_start > *pos) {
                return NGX_ERROR;
            }
            ngx_memmove(buf, buf + *line_start, *pos - *line_start);
            *pos -= *line_start;
            *line_start = 0;
            return NGX_OK;
        }

        if (ngx_http_markdown_dynconf_try_line(
                snapshot, buf + *line_start,
                ngx_http_markdown_dynconf_line_len(buf, *line_start, i),
                log, applied) != NGX_OK)
        {
            return NGX_ERROR;
        }

        *line_start = i + 1;
    }
}

/*
 * Read one chunk from fd into buf at *pos with bounds checks.
 *
 * Returns NGX_OK on successful read attempt (including EOF/error status in
 * *n), or a reload status code on hard failure.
 */
static ngx_int_t
ngx_http_markdown_dynconf_read_chunk(
    ngx_fd_t fd, u_char *buf, size_t *pos, size_t buf_cap,
    const ngx_str_t *path, ngx_log_t *log, ssize_t *n)
{
    u_char  read_buf[NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE];
    size_t  avail;

    if (*pos >= buf_cap) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: buffer position overflow in \"%V\"",
                      (ngx_str_t *) path);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    avail = buf_cap - *pos;

    *n = ngx_read_fd(fd, read_buf,
                     avail < sizeof(read_buf) ? avail : sizeof(read_buf));

    if (*n <= 0) {
        return NGX_OK;
    }

    if ((size_t) *n > avail) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: read overflow in \"%V\"",
                      (ngx_str_t *) path);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    {
        size_t  len;

        len = (size_t) *n;
        ngx_memcpy(buf + *pos, read_buf, len);
        *pos += len;
    }

    return NGX_OK;
}

/*
 * Process complete lines currently present in buf and enforce line-length cap.
 */
static ngx_int_t
ngx_http_markdown_dynconf_process_chunk(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    u_char *buf, size_t *pos, size_t *line_start,
    ngx_log_t *log, ngx_uint_t *applied)
{
    if (ngx_http_markdown_dynconf_process_buffer(
            &watcher->staging_snapshot, buf, pos, line_start,
            log, applied) != NGX_OK)
    {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: parse error in \"%V\", "
                      "discarding staging; active config unchanged",
                      &watcher->path);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    if (*pos >= NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf: line too long in \"%V\", "
                      "discarding staging; active config unchanged",
                      &watcher->path);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    return NGX_OK;
}


/**
 * Record a validation error into the validation result struct.
 *
 * Appends an error entry with line number, field name, and reason
 * to the result.  If the result has reached its capacity
 * (NGX_HTTP_MARKDOWN_DYNCONF_MAX_ERRORS), only total_errors is
 * incremented without storing the entry.
 *
 * @param result  Validation result to append to; must be non-NULL.
 * @param line    1-based line number where the error occurred.
 * @param field   Field/key name (may be NULL for structural errors).
 * @param flen    Length of field name.
 * @param reason  Human-readable error reason.
 * @param rlen    Length of reason string.
 */
static void
ngx_http_markdown_dynconf_record_error(
    ngx_http_markdown_dynconf_validation_result_t *result,
    ngx_uint_t line,
    const u_char *field, size_t flen,
    const u_char *reason, size_t rlen)
{
    ngx_http_markdown_dynconf_validation_error_t  *entry;

    if (result == NULL) {
        return;
    }

    result->total_errors++;

    if (result->count >= NGX_HTTP_MARKDOWN_DYNCONF_MAX_ERRORS) {
        return;
    }

    entry = &result->errors[result->count];
    entry->line = line;

    /* Copy field name, truncating if necessary. */
    if (field != NULL && flen > 0) {
        if (flen > NGX_HTTP_MARKDOWN_DYNCONF_ERR_FIELD_MAX - 1) {
            flen = NGX_HTTP_MARKDOWN_DYNCONF_ERR_FIELD_MAX - 1;
        }
        ngx_memcpy(entry->field, field, flen);
        entry->field[flen] = '\0';
        entry->field_len = flen;
    } else {
        entry->field[0] = '\0';
        entry->field_len = 0;
    }

    /* Copy reason, truncating if necessary. */
    if (reason != NULL && rlen > 0) {
        if (rlen > NGX_HTTP_MARKDOWN_DYNCONF_ERR_REASON_MAX - 1) {
            rlen = NGX_HTTP_MARKDOWN_DYNCONF_ERR_REASON_MAX - 1;
        }
        ngx_memcpy(entry->reason, reason, rlen);
        entry->reason[rlen] = '\0';
        entry->reason_len = rlen;
    } else {
        entry->reason[0] = '\0';
        entry->reason_len = 0;
    }

    result->count++;
}


/**
 * Log all collected validation errors at WARN level.
 *
 * Iterates through the validation result and emits one log line
 * per error with line number, field name, and reason.  If errors
 * were capped, logs an additional summary line.
 *
 * @param result  Validation result containing collected errors.
 * @param path    Path to the dynconf file (for log context).
 * @param log     NGINX log for output.
 */
static void
ngx_http_markdown_dynconf_log_validation_errors(
    const ngx_http_markdown_dynconf_validation_result_t *result,
    const ngx_str_t *path,
    ngx_log_t *log)
{
    ngx_uint_t  i;
    const ngx_http_markdown_dynconf_validation_error_t  *entry;

    if (result == NULL || result->total_errors == 0) {
        return;
    }

    for (i = 0; i < result->count; i++) {
        entry = &result->errors[i];
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf dry-run: error in \"%V\" "
                      "line %ui, field \"%*s\", reason: %*s",
                      path,
                      entry->line,
                      entry->field_len, entry->field,
                      entry->reason_len, entry->reason);
    }

    if (result->total_errors > result->count) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf dry-run: %ui total errors in "
                      "\"%V\" (%ui shown, %ui truncated)",
                      result->total_errors, path,
                      result->count,
                      result->total_errors - result->count);
    }

    ngx_log_error(NGX_LOG_WARN, log, 0,
                  "markdown dynconf dry-run: validation failed for "
                  "\"%V\" with %ui error(s)",
                  path, result->total_errors);
}


/**
 * Attempt to parse a single line in dry-run mode, collecting errors
 * instead of aborting on failure.
 *
 * Unlike ngx_http_markdown_dynconf_try_line which returns NGX_ERROR
 * on parse/apply failure (causing the reload to abort), this function
 * records the error in the validation result and returns NGX_OK so
 * that parsing continues to find all errors.
 *
 * @param snapshot  Staging snapshot to apply valid lines to.
 * @param line      Pointer to the line buffer.
 * @param len       Length of the line in bytes.
 * @param line_num  1-based line number for error reporting.
 * @param log       NGINX log for diagnostics.
 * @param applied   In/out counter of successfully applied entries.
 * @param result    Validation result to collect errors into.
 *
 * @returns NGX_OK always (errors are collected, not fatal).
 */
static ngx_int_t
ngx_http_markdown_dynconf_try_line_dryrun(
    ngx_http_markdown_dynconf_snapshot_t *snapshot,
    u_char *line, size_t len,
    ngx_uint_t line_num,
    ngx_log_t *log,
    ngx_uint_t *applied,
    ngx_http_markdown_dynconf_validation_result_t *result)
{
    ngx_uint_t  key;
    u_char     *value;
    size_t      value_len;
    ngx_int_t   parse_rc;
    ngx_int_t   apply_rc;
    u_char     *p;
    const u_char *last;
    u_char     *eq;
    size_t      key_len;

    parse_rc = ngx_http_markdown_dynconf_parse_line(line, len,
                                                     &key, &value,
                                                     &value_len);
    if (parse_rc == NGX_DECLINED) {
        /* Comment or blank line — skip. */
        return NGX_OK;
    }

    if (parse_rc != NGX_OK) {
        /*
         * Parse error: extract the field name from the line for
         * error reporting.  The field is the text before '=' or
         * whitespace.
         */
        p = line;
        last = line + len;

        /* Skip leading whitespace. */
        while (p < last && (*p == ' ' || *p == '\t')) {
            p++;
        }

        eq = p;
        while (eq < last && *eq != '=' && *eq != ' ' && *eq != '\t') {
            eq++;
        }

        key_len = eq - p;

        /* Determine reason based on whether key was recognized. */
        if (key_len > 0) {
            ngx_uint_t  tmp_key;

            if (ngx_http_markdown_dynconf_match_key(p, eq, &tmp_key)
                != NGX_OK)
            {
                ngx_http_markdown_dynconf_record_error(
                    result, line_num, p, key_len,
                    (const u_char *) "unknown key",
                    sizeof("unknown key") - 1);
            } else {
                ngx_http_markdown_dynconf_record_error(
                    result, line_num, p, key_len,
                    (const u_char *) "invalid syntax",
                    sizeof("invalid syntax") - 1);
            }
        } else {
            ngx_http_markdown_dynconf_record_error(
                result, line_num,
                (const u_char *) "(empty)", sizeof("(empty)") - 1,
                (const u_char *) "invalid syntax",
                sizeof("invalid syntax") - 1);
        }

        return NGX_OK;
    }

    /* Key parsed successfully; try to apply the value. */
    apply_rc = ngx_http_markdown_dynconf_apply(snapshot, key, value,
                                                value_len, log);
    if (apply_rc == NGX_OK) {
        (*applied)++;
        return NGX_OK;
    }

    /*
     * Apply failed: the value is invalid for this key.
     * Extract the key name for the error entry.
     */
    {
        static u_char  key_names[][20] = {
            "markdown_filter",
            "prune_noise",
            "log_verbosity",
            "streaming_budget",
            "memory_budget"
        };
        static size_t  key_name_lens[] = { 15, 11, 13, 16, 13 };

        const u_char  *field_name;
        size_t         field_name_len;

        if (key >= 1 && key <= 5) {
            field_name = key_names[key - 1];
            field_name_len = key_name_lens[key - 1];
        } else {
            field_name = (const u_char *) "(unknown)";
            field_name_len = sizeof("(unknown)") - 1;
        }

        ngx_http_markdown_dynconf_record_error(
            result, line_num, field_name, field_name_len,
            (const u_char *) "invalid value",
            sizeof("invalid value") - 1);
    }

    return NGX_OK;
}


/**
 * Process complete lines from the read buffer in dry-run mode.
 *
 * Similar to ngx_http_markdown_dynconf_process_buffer but uses
 * the dry-run line handler that collects errors instead of aborting.
 * Tracks line numbers for error reporting.
 *
 * @param snapshot    Staging snapshot to apply valid lines to.
 * @param buf         Read buffer.
 * @param pos         [in/out] Current end of data in buf.
 * @param line_start  [in/out] Start of next unprocessed line.
 * @param line_num    [in/out] Current 1-based line number.
 * @param log         Logger.
 * @param applied     [in/out] Count of successfully applied entries.
 * @param result      Validation result to collect errors into.
 *
 * @returns NGX_OK always (errors are collected, not fatal).
 */
static ngx_int_t
ngx_http_markdown_dynconf_process_buffer_dryrun(
    ngx_http_markdown_dynconf_snapshot_t *snapshot,
    u_char *buf, size_t *pos, size_t *line_start,
    ngx_uint_t *line_num,
    ngx_log_t *log, ngx_uint_t *applied,
    ngx_http_markdown_dynconf_validation_result_t *result)
{
    for ( ;; ) {
        size_t  i;

        /* Find newline. */
        i = *line_start;
        while (i < *pos && buf[i] != '\n') {
            i++;
        }

        if (i >= *pos) {
            /* No complete line; shift remaining data. */
            if (*line_start > *pos) {
                return NGX_ERROR;
            }
            ngx_memmove(buf, buf + *line_start, *pos - *line_start);
            *pos -= *line_start;
            *line_start = 0;
            return NGX_OK;
        }

        ngx_http_markdown_dynconf_try_line_dryrun(
            snapshot, buf + *line_start,
            ngx_http_markdown_dynconf_line_len(buf, *line_start, i),
            *line_num, log, applied, result);

        (*line_num)++;
        *line_start = i + 1;
    }
}


/**
 * Perform a two-phase reload of dynamic configuration from the watcher's file.
 *
 * Reads the entire file into a staging snapshot, parses and applies every line
 * into that staging snapshot, and on complete success atomically replaces the
 * watcher's active snapshot and applies it to the provided live configuration.
 *
 * When conf->advanced.dynconf_dry_run is enabled (on), the reload performs
 * full validation (syntax and semantic checks) but does NOT replace the
 * active_snapshot, does NOT update applied_mtime, does NOT update
 * last_known_good.  In dry-run mode, ALL errors are collected (up to
 * NGX_HTTP_MARKDOWN_DYNCONF_MAX_ERRORS) with line numbers, field names,
 * and reasons, then logged at WARN level.  Returns DRY_RUN_OK on success
 * or DRY_RUN_FAIL when errors are found.
 *
 * @param watcher Dynamic config watcher containing the file path and snapshots.
 * @param conf Current module location configuration to update when commit succeeds.
 * @param log Logger used for warnings and informational messages.
 *
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED      if one or more settings were applied and committed
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE    if the file contained no effective keys to apply
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE if any line failed to parse or a line was too long
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR     on file open/read failures or invalid inputs
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK   if validation passed
 *          but dry-run mode prevented application
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL if dry-run validation
 *          found errors (details in watcher->last_validation)
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
    ngx_uint_t   dry_run;
    ngx_uint_t   line_num;

    if (watcher == NULL || conf == NULL || log == NULL) {
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    if (watcher->path.len > NGX_MAX_PATH) {
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    dry_run = (conf->advanced.dynconf_dry_run == 1) ? 1 : 0;

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

    /*
     * In dry-run mode, reset the validation result to collect
     * errors from this reload attempt.
     */
    if (dry_run) {
        ngx_memzero(&watcher->last_validation,
                    sizeof(ngx_http_markdown_dynconf_validation_result_t));
    }

    applied = 0;
    line_start = 0;
    pos = 0;
    line_num = 1;

    if (dry_run) {
        /*
         * Dry-run path: parse all lines, collecting errors instead
         * of aborting at the first failure.  This provides operators
         * with a complete list of issues to fix.
         */
        for ( ;; ) {
            line_rc = ngx_http_markdown_dynconf_read_chunk(
                fd, buf, &pos, sizeof(buf), &watcher->path, log, &n);
            if (line_rc != NGX_OK) {
                ngx_close_file(fd);
                return line_rc;
            }

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

            /* Check line length cap before processing. */
            if (pos >= NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE) {
                ngx_http_markdown_dynconf_record_error(
                    &watcher->last_validation, line_num,
                    (const u_char *) "(line)", sizeof("(line)") - 1,
                    (const u_char *) "line too long",
                    sizeof("line too long") - 1);
                ngx_close_file(fd);
                goto dryrun_finish;
            }

            ngx_http_markdown_dynconf_process_buffer_dryrun(
                &watcher->staging_snapshot, buf, &pos, &line_start,
                &line_num, log, &applied, &watcher->last_validation);
        }

        ngx_close_file(fd);

        /* Process final line if file does not end with newline. */
        if (line_start < pos) {
            ngx_http_markdown_dynconf_try_line_dryrun(
                &watcher->staging_snapshot, buf + line_start,
                ngx_http_markdown_dynconf_line_len(buf, line_start, pos),
                line_num, log, &applied, &watcher->last_validation);
        }

dryrun_finish:
        if (watcher->last_validation.total_errors > 0) {
            watcher->last_validation.valid = 0;
            ngx_http_markdown_dynconf_log_validation_errors(
                &watcher->last_validation, &watcher->path, log);
            return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL;
        }

        /* Dry-run passed: mark validation as successful. */
        watcher->last_validation.valid = 1;

        if (applied > 0) {
            ngx_log_error(NGX_LOG_INFO, log, 0,
                          "markdown dynconf: dry-run validation passed "
                          "for \"%V\" (%ui settings validated, "
                          "not applied)",
                          &watcher->path, applied);
        } else {
            ngx_log_error(NGX_LOG_INFO, log, 0,
                          "markdown dynconf: dry-run validation passed "
                          "for \"%V\" (0 effective keys, not applied)",
                          &watcher->path);
        }

        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK;
    }

    /*
     * Normal (non-dry-run) path: abort on first error.
     */
    for ( ;; ) {
        line_rc = ngx_http_markdown_dynconf_read_chunk(
            fd, buf, &pos, sizeof(buf), &watcher->path, log, &n);
        if (line_rc != NGX_OK) {
            ngx_close_file(fd);
            return line_rc;
        }

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

        line_rc = ngx_http_markdown_dynconf_process_chunk(
            watcher, buf, &pos, &line_start, log, &applied);
        if (line_rc != NGX_OK) {
            ngx_close_file(fd);
            return line_rc;
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
        /* All lines parsed successfully: commit staging to active.
         * Preserve the current active snapshot as last-known-good
         * before overwriting it with the new configuration.  This
         * enables rollback to the previous working state (E04). */
        watcher->last_known_good = watcher->active_snapshot;
        watcher->lkg_valid = 1;

        watcher->active_snapshot = watcher->staging_snapshot;
        watcher->version++;

        /* Apply the new active snapshot to the live conf. */
        ngx_http_markdown_dynconf_apply_snapshot(conf,
                                                  &watcher->active_snapshot);

        ngx_log_error(NGX_LOG_INFO, log, 0,
                      "markdown dynconf: applied %ui settings from \"%V\" "
                      "(version=%ui, lkg preserved)",
                      applied, &watcher->path, watcher->version);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED;
    }

    return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE;
}


/**
 * Trigger a manual rollback of the active configuration to the
 * last-known-good (LKG) snapshot.
 *
 * This function implements the manual rollback path described in
 * design.md §13.3:
 *   active_snapshot (N+1) ← last_known_good (N)
 *   record rollback event to log and metrics
 *
 * The rollback is only possible when lkg_valid is set (i.e. at
 * least one successful reload has occurred since the watcher was
 * started).  After rollback, the version counter is incremented
 * to reflect the configuration change, and the LKG snapshot is
 * preserved (not cleared) so that repeated rollback calls are
 * idempotent.
 *
 * Callable from:
 *   - The diagnostics endpoint (manual rollback API)
 *   - A signal handler or timer callback (future: automatic
 *     rollback on anomaly detection)
 *
 * @param watcher Dynamic config watcher holding the snapshots.
 * @param log     NGINX log for recording the rollback event.
 *
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK
 *          if the rollback succeeded
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_NO_LKG
 *          if no last-known-good is available (lkg_valid == 0)
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_APPLY_ERR
 *          if the watcher or conf pointer is invalid
 */
static ngx_int_t
ngx_http_markdown_dynconf_rollback(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_log_t *log)
{
    if (watcher == NULL || watcher->conf == NULL) {
        if (log != NULL) {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                          "markdown dynconf rollback: "
                          "invalid watcher or conf pointer");
        }
        return NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_APPLY_ERR;
    }

    if (!watcher->lkg_valid) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown dynconf rollback: "
                      "no last-known-good available "
                      "(no successful reload has occurred)");
        return NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_NO_LKG;
    }

    /*
     * Restore the last-known-good snapshot as the active
     * configuration.  The LKG itself is NOT cleared — it
     * remains valid so that repeated rollback calls are
     * idempotent (rolling back to the same LKG again is
     * a no-op in effect but still logged).
     */
    watcher->active_snapshot = watcher->last_known_good;
    watcher->version++;

    /* Apply the restored snapshot to the live conf. */
    ngx_http_markdown_dynconf_apply_snapshot(watcher->conf,
                                              &watcher->active_snapshot);

    ngx_log_error(NGX_LOG_WARN, log, 0,
                  "markdown dynconf rollback: "
                  "restored last-known-good configuration "
                  "(version=%ui)",
                  watcher->version);

    return NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK;
}


#endif /* NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H */
