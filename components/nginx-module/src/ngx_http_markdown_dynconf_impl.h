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
 * Requirements: P2-10, P0-1, P0-2, P1-2
 */

#ifndef NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H
#define NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H

#include <stdlib.h>
#include <sys/types.h>

/* NGINX defines this in ngx_config.h; keep standalone unit builds portable. */
#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE  ((size_t) -1)
#endif

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
 * Backstop full content-digest check frequency in timer ticks.
 *
 * Every BACKSTOP_TICKS timer firings (~30 seconds at 1-second interval),
 * the watcher reads the file and computes a content digest even if metadata
 * has not changed.  This prevents same-inode or same-mtime-tick atomic
 * replaces from being permanently missed.
 *
 * This is a FIXED internal constant — NOT operator-configurable in 1.0.
 * No directive or dynconf key exists for it.
 *
 * Requirements: 3.3, 3.12
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_BACKSTOP_TICKS  30

/*
 * SHA-256 hex digest length (64 hex chars + NUL terminator).
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN  72

/* Rust returns a 64-byte hex digest; diagnostics exposes the typed prefix. */
#define NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_PREFIX  "sha256:"
#define NGX_HTTP_MARKDOWN_DYNCONF_MAX_FILE_SIZE  (1024 * 1024)

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
 * Dynconf dry-run validation (0.9.2).
 *
 * The dynconf-mutable field set (filter, prune_noise, log_verbosity,
 * error_policy, streaming_buffer) does not include any fields that
 * require cross-field conflict detection at runtime.  All structural
 * conflict rules (streaming+conditional, max_inflight==0) involve
 * static-only configuration and are checked at nginx -t time.
 */

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
    ngx_uint_t   error_policy;
    ngx_uint_t   error_status;
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
 * lkg_valid is set, diagnostics can report the preserved state and
 * a failed file reload leaves the active snapshot unchanged.
 * The LKG is NOT updated on validation failure — only a
 * successful reload promotes the current active to LKG.
 */
typedef struct {
    ngx_str_t     path;
    time_t        last_mtime;
    time_t        applied_mtime;
    ngx_event_t  *timer;
    ngx_uint_t    active;

    /*
     * Two-tier polling file identity fields (Requirements: 3.3, 3.12).
     *
     * Fast path (every tick): stat-only check of device ID, inode, size,
     * and highest-available mtime precision.  No file read under steady
     * state.
     */
    dev_t         file_dev;       /* device ID of last stat */
    ino_t         file_ino;       /* inode number of last stat */
    off_t         file_size;      /* file size in bytes of last stat */
    time_t        file_mtime_sec; /* mtime seconds of last stat */
    long          file_mtime_nsec;/* mtime nanoseconds (0 if unavailable) */

    /*
     * Tick counter for backstop scheduling.
     * Incremented on each timer firing; reset to 0 after backstop.
     */
    ngx_uint_t    tick_counter;

    /*
     * Worker-local monotonically increasing generation counter.
     * Starts at 1, incremented on each successful reload within
     * this worker.  Cross-worker convergence is via matching
     * active_digest values, not generation counter equality.
     * (Requirement 3.7)
     */
    ngx_uint_t    generation;

    /*
     * Content digests (Requirements: 3.7, 3.16).
     *
     * source_digest: SHA-256 over raw file bytes.
     * active_digest: SHA-256 over canonical UTF-8 JSON representation
     *   of the typed dynconf overlay (from Rust FFI).
     * lkg_digest: active_digest of the last-known-good configuration.
     *
     * Stored as 64 hex chars + NUL terminator.
     * Empty string means "not yet computed".
     */
    u_char        source_digest[NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    u_char        active_digest[NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    u_char        lkg_digest[NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];

    ngx_http_markdown_dynconf_snapshot_t  active_snapshot;
    ngx_http_markdown_dynconf_snapshot_t  staging_snapshot;
    ngx_http_markdown_dynconf_snapshot_t  last_known_good;
    ngx_uint_t    lkg_valid;
    /* File mtime of the configuration captured as last_known_good.
     * Distinct from last_mtime (most recently observed file mtime) and
     * applied_mtime (mtime of the currently active config): this is the
     * mtime of the previous active config preserved for diagnostics.  Set
     * whenever last_known_good is captured. */
    time_t        lkg_mtime;
    ngx_uint_t    version;
    ngx_http_markdown_conf_t             *conf;

    /* Last dry-run validation result; populated when dry-run mode
     * is active and a reload attempt occurs.  Available for the
     * diagnostics endpoint to report validation failures. */
    ngx_http_markdown_dynconf_validation_result_t  last_validation;

    /* Bounded lifecycle metadata consumed by diagnostics and metrics. */
    ngx_uint_t    last_result;
    time_t        last_success;
    u_char        last_error[513];
    size_t        last_error_len;
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
    snapshot->error_policy = conf->on_error;
    snapshot->error_status = conf->error_status;
#ifdef MARKDOWN_STREAMING_ENABLED
    snapshot->streaming_budget = conf->stream.budget;
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
    conf->on_error = snapshot->error_policy;
    conf->error_status = snapshot->error_status;
#ifdef MARKDOWN_STREAMING_ENABLED
        conf->stream.budget = snapshot->streaming_budget;
#endif
    conf->advanced.memory_budget = snapshot->memory_budget;
}


/**
 * Build the effective configuration view from a dynconf snapshot and live conf.
 *
 * Implements the five-tier precedence model (0.9.2):
 *   1. Request variable evaluation (applied after this function, at is_enabled)
 *   2. Server/location explicit static (block bit SET → use conf value)
 *   3. Dynconf runtime override (block bit NOT set → use snapshot value)
 *   4. Inherited http baseline (conf value when block bit not set and no snap)
 *   5. Built-in default (conf value when nothing configured)
 *
 * For each dynconf-mutable field, checks the location's block mask:
 *   - If the block bit IS set: use the static conf value (tier 2)
 *   - If the block bit is NOT set and snapshot is valid: use snapshot (tier 3)
 *   - Otherwise: use the conf value (tiers 4-5)
 *
 * Records per-field provenance for diagnostics.
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
    ngx_uint_t  mask;
    ngx_flag_t  snap_valid;

    if (eff == NULL || conf == NULL) {
        return;
    }

    mask = conf->advanced.dynconf_block_mask;
    snap_valid = (snap != NULL && snap->valid);

    /* Copy block mask into effective_conf for diagnostics */
    eff->block_mask = mask;

    /*
     * Field: filter (enabled / enabled_source)
     *
     * If block bit is set, use static conf value (tier 2).
     * If block bit is clear and snapshot valid, use dynconf (tier 3).
     * Otherwise, use conf value (tier 4/5).
     *
     * Special case: if enabled_source is COMPLEX (variable), the
     * provenance is request_variable (tier 1, evaluated at request
     * time in ngx_http_markdown_is_enabled after this function).
     */
    if (conf->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_COMPLEX) {
        /* Tier 1: request variable always wins */
        eff->enabled = conf->enabled;
        eff->enabled_source = conf->enabled_source;
        eff->filter_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_REQUEST_VARIABLE;
    } else if (mask & NGX_HTTP_MARKDOWN_BLOCK_FILTER) {
        eff->enabled = conf->enabled;
        eff->enabled_source = conf->enabled_source;
        eff->filter_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    } else if (snap_valid) {
        eff->enabled = snap->enabled;
        eff->enabled_source = snap->enabled_source;
        eff->filter_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
    } else {
        eff->enabled = conf->enabled;
        eff->enabled_source = conf->enabled_source;
        eff->filter_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    }

    /*
     * Field: prune_noise
     */
    if (mask & NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE) {
        eff->prune_noise = conf->advanced.prune_noise;
        eff->prune_noise_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    } else if (snap_valid) {
        eff->prune_noise = snap->prune_noise;
        eff->prune_noise_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
    } else {
        eff->prune_noise = conf->advanced.prune_noise;
        eff->prune_noise_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    }

    /*
     * Field: log_verbosity
     */
    if (mask & NGX_HTTP_MARKDOWN_BLOCK_LOG_VERBOSITY) {
        eff->log_verbosity = conf->policy.log_verbosity;
        eff->log_verbosity_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    } else if (snap_valid) {
        eff->log_verbosity = snap->log_verbosity;
        eff->log_verbosity_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
    } else {
        eff->log_verbosity = conf->policy.log_verbosity;
        eff->log_verbosity_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    }

    /*
     * Field: error_policy
     */
    if (mask & NGX_HTTP_MARKDOWN_BLOCK_ERROR_POLICY) {
        eff->error_policy = conf->on_error;
        eff->error_status = conf->error_status;
        eff->error_policy_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    } else if (snap_valid) {
        eff->error_policy = snap->error_policy;
        eff->error_status = snap->error_status;
        eff->error_policy_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
    } else {
        eff->error_policy = conf->on_error;
        eff->error_status = conf->error_status;
        eff->error_policy_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    }

    /*
     * Field: memory_budget (legacy bridge from conversion_memory).
     *
     * memory_budget is NOT part of the 0.9.2 dynconf-mutable field
     * set (which is filter, prune_noise, log_verbosity, error_policy,
     * streaming_buffer).  However, it was historically overlaid by
     * dynconf and tests validate this behavior.  We preserve the
     * legacy overlay behavior without block mask gating.
     */
    if (snap_valid) {
        eff->memory_budget = snap->memory_budget;
    } else {
        eff->memory_budget = conf->advanced.memory_budget;
    }

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * Field: streaming_buffer
     */
    if (mask & NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER) {
        eff->streaming_budget = conf->stream.budget;
        eff->streaming_buffer = conf->stream.budget;
        eff->streaming_buffer_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    } else if (snap_valid) {
        eff->streaming_budget = snap->streaming_budget;
        eff->streaming_buffer = snap->streaming_budget;
        eff->streaming_buffer_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
    } else {
        eff->streaming_budget = conf->stream.budget;
        eff->streaming_buffer = conf->stream.budget;
        eff->streaming_buffer_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    }
#else
    eff->streaming_buffer = conf->stream.budget;
#endif
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
    return conf->stream.budget;
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
 * Two-tier polling: fast-path stat-only metadata check.
 *
 * Compares current file identity (device, inode, size, mtime with
 * nanosecond precision where available) against stored values.
 * If ANY metadata field differs, returns 1 (metadata changed).
 * Does NOT read the file — no I/O beyond stat().
 *
 * Per-event-loop-iteration stat calls are prohibited: this function
 * is called at most once per timer tick (1-second interval).
 *
 * Parameters:
 *   watcher - Dynamic config watcher
 *   log     - NGINX log
 *
 * Returns:
 *   1 if file metadata changed, 0 otherwise (including stat failure)
 *
 * Requirements: 3.3, 3.12
 */
static ngx_int_t
ngx_http_markdown_dynconf_check(ngx_http_markdown_dynconf_watcher_t *watcher,
                                ngx_log_t *log)
{
    ngx_file_info_t  fi;
    dev_t            cur_dev;
    ino_t            cur_ino;
    off_t            cur_size;
    time_t           cur_mtime_sec;
    long             cur_mtime_nsec;

    if (watcher == NULL || !watcher->active) {
        return 0;
    }

    {
        u_char  path_buf[NGX_MAX_PATH + 1];

        if (watcher->path.len > NGX_MAX_PATH) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: path too long (%uz bytes)",
                          watcher->path.len);
            return 0;
        }

        ngx_memcpy(path_buf, watcher->path.data, watcher->path.len);
        path_buf[watcher->path.len] = '\0';

        if (ngx_file_info(path_buf, &fi) == NGX_FILE_ERROR) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: stat(\"%V\") failed",
                          &watcher->path);
            return 0;
        }
    }

    /* Extract file identity fields from stat result. */
    cur_dev = fi.st_dev;
    cur_ino = fi.st_ino;
    cur_size = (off_t) fi.st_size;
    cur_mtime_sec = ngx_file_mtime(&fi);

    /*
     * Extract nanosecond mtime precision where available.
     * macOS uses st_mtimespec; Linux uses st_mtim.
     * Falls back to 0 (second-only precision) on other platforms
     * or test stubs.
     */
#if defined(__APPLE__)
    cur_mtime_nsec = fi.st_mtimespec.tv_nsec;
#elif defined(__linux__) && defined(_POSIX_C_SOURCE) && _POSIX_C_SOURCE >= 200809L
    cur_mtime_nsec = fi.st_mtim.tv_nsec;
#elif defined(__linux__) && defined(_GNU_SOURCE)
    cur_mtime_nsec = fi.st_mtim.tv_nsec;
#else
    cur_mtime_nsec = 0;
#endif

    /* Compare all metadata fields.  Any change triggers reload. */
    if (cur_dev != watcher->file_dev
        || cur_ino != watcher->file_ino
        || cur_size != watcher->file_size
        || cur_mtime_sec != watcher->file_mtime_sec
        || cur_mtime_nsec != watcher->file_mtime_nsec)
    {
        /* Update stored identity to current values. */
        watcher->file_dev = cur_dev;
        watcher->file_ino = cur_ino;
        watcher->file_size = cur_size;
        watcher->file_mtime_sec = cur_mtime_sec;
        watcher->file_mtime_nsec = cur_mtime_nsec;

        /* Also update last_mtime for backward compatibility. */
        watcher->last_mtime = cur_mtime_sec;

        return 1;
    }

    return 0;
}


/**
 * Two-tier dynconf polling timer handler (Requirements: 3.3, 3.6, 3.12).
 *
 * Implements the bounded timer interval (1 second per worker) with:
 *
 *   Fast path (every tick): stat-only check of device ID, inode, size,
 *   and highest-available mtime precision (nanosecond where available).
 *   No file read on every tick under steady state.
 *
 *   Full content-digest backstop (every 30 ticks, ~30 seconds): read
 *   file and compute content digest even without metadata changes —
 *   prevents same-inode or same-mtime-tick atomic replaces from being
 *   permanently missed.
 *
 * On metadata change (fast path) OR content-digest change (backstop):
 * trigger reload if content differs from active snapshot.
 *
 * File I/O (read + hash) SHALL NOT occur on every timer tick under
 * steady state.  Per-event-loop-iteration stat calls are prohibited.
 *
 * Generation counter: worker-local monotonically increasing, starts at 1,
 * incremented on each successful reload within that worker.
 *
 * WHEN source_digest changes but active_digest does not (formatting-only
 * change): reload the snapshot (update source_digest and generation),
 * because file content has changed.  This is NOT a validated_noop —
 * generation always increments on any successful reload.
 *
 * @param ev Timer event whose `data` field points to the watcher.
 */
static void
ngx_http_markdown_dynconf_timer_handler(ngx_event_t *ev)
{
    ngx_http_markdown_dynconf_watcher_t  *watcher;
    ngx_int_t                            reload_rc;
    ngx_uint_t                            reload_attempted;
    ngx_uint_t                            metadata_changed;
    ngx_uint_t                            backstop_tick;

    watcher = ev->data;

    if (watcher == NULL || !watcher->active) {
        return;
    }

    reload_rc = NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE;
    reload_attempted = 0;

    /* Increment tick counter for backstop scheduling. */
    watcher->tick_counter++;

    /* Determine if this is a backstop tick. */
    backstop_tick = (watcher->tick_counter
                     >= NGX_HTTP_MARKDOWN_DYNCONF_BACKSTOP_TICKS) ? 1 : 0;

    /* Fast path: stat-only metadata check (every tick). */
    metadata_changed = ngx_http_markdown_dynconf_check(watcher, ev->log);

    if (metadata_changed) {
        /*
         * Metadata changed: read file, parse, and reload.
         * Reset backstop counter since we're reading the file now.
         */
        watcher->tick_counter = 0;

        ngx_log_error(NGX_LOG_INFO, ev->log, 0,
                      "markdown: metadata change detected on \"%V\", "
                      "performing two-phase reload",
                      &watcher->path);

        if (watcher->conf != NULL) {
            reload_attempted = 1;
            reload_rc = ngx_http_markdown_dynconf_reload(watcher,
                                                          watcher->conf,
                                                          ev->log);
        }
    } else if (backstop_tick) {
        /*
         * Full content-digest backstop: read file and compute digest
         * even without metadata changes.  This prevents same-inode or
         * same-mtime-tick atomic replaces from being permanently missed.
         *
         * Reset tick counter after backstop regardless of outcome.
         */
        watcher->tick_counter = 0;

        if (watcher->conf != NULL) {
            reload_attempted = 1;
            reload_rc = ngx_http_markdown_dynconf_reload(watcher,
                                                          watcher->conf,
                                                          ev->log);

            if (reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE) {
                /* Backstop check: no content change — steady state. */
                (void) 0;
            } else if (reload_rc
                       == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED)
            {
                ngx_log_error(NGX_LOG_INFO, ev->log, 0,
                              "markdown: backstop detected content "
                              "change on \"%V\" (atomic replace?)",
                              &watcher->path);
            }
        }
    } else if (watcher->last_mtime != watcher->applied_mtime) {
        /*
         * No new metadata change and not a backstop tick, but a
         * previous reload failed (last_mtime advanced but
         * applied_mtime did not).  Retry the reload so transient
         * errors are eventually resolved.
         */
        ngx_log_error(NGX_LOG_INFO, ev->log, 0,
                      "markdown: retrying failed reload on \"%V\" "
                      "(last_mtime=%T, applied_mtime=%T)",
                      &watcher->path,
                      watcher->last_mtime, watcher->applied_mtime);

        if (watcher->conf != NULL) {
            reload_attempted = 1;
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
     * RELOAD_DRY_RUN_OK: validation succeeded without applying settings;
     *   record applied_mtime so unchanged content is not re-validated on
     *   every timer cycle.  A later mtime change starts a new validation.
     * RELOAD_DRY_RUN_FAIL: validation failed; record applied_mtime after
     *   storing the failure so the same invalid content is not retried.
     * INVALID_FILE / IO_ERROR: reload failed; applied_mtime stays
     *   at its previous value so the next timer cycle will retry.
     */
    if (reload_attempted
        && (reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED
            || reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE))
    {
        watcher->applied_mtime = watcher->last_mtime;
    } else if (reload_attempted
               && (reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK
                   || reload_rc
                      == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL))
    {
        /*
         * Dry-run mode (pass or fail): update applied_mtime to
         * suppress repeated re-validation of the same file content
         * on every timer cycle.
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
                      "markdown: watcher already active; "
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
                      "markdown: path too long (%uz bytes)",
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
                      "markdown: initial stat(\"%V\") failed, "
                      "will retry on timer",
                      path);
        watcher->last_mtime = 0;
        watcher->applied_mtime = 0;
        watcher->file_dev = 0;
        watcher->file_ino = 0;
        watcher->file_size = 0;
        watcher->file_mtime_sec = 0;
        watcher->file_mtime_nsec = 0;
    } else {
        watcher->last_mtime = ngx_file_mtime(&fi);
        watcher->applied_mtime = watcher->last_mtime;

        /* Capture full file identity for two-tier fast path. */
        watcher->file_dev = fi.st_dev;
        watcher->file_ino = fi.st_ino;
        watcher->file_size = (off_t) fi.st_size;
        watcher->file_mtime_sec = ngx_file_mtime(&fi);
#if defined(__APPLE__)
        watcher->file_mtime_nsec = fi.st_mtimespec.tv_nsec;
#elif defined(__linux__) && defined(_GNU_SOURCE)
        watcher->file_mtime_nsec = fi.st_mtim.tv_nsec;
#else
        watcher->file_mtime_nsec = 0;
#endif
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

    /* Initialize two-tier polling state. */
    watcher->tick_counter = 0;
    watcher->generation = 0;  /* Will become 1 on first successful reload. */
    watcher->source_digest[0] = '\0';
    watcher->active_digest[0] = '\0';
    watcher->lkg_digest[0] = '\0';
    watcher->last_result = NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE;
    watcher->last_success = 0;
    watcher->last_error_len = 0;
    watcher->last_error[0] = '\0';

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
                          "markdown: applied existing file on startup "
                          "(rc=%i, version=%ui)",
                          initial_rc, watcher->version);
        } else if (initial_rc
                   == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK)
        {
            watcher->applied_mtime = watcher->last_mtime;
            ngx_log_error(NGX_LOG_INFO, log, 0,
                          "markdown: dry-run validation passed "
                          "on startup (rc=%i, not applied)",
                          initial_rc);
        } else if (initial_rc
                   == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL)
        {
            watcher->applied_mtime = watcher->last_mtime;
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: dry-run validation failed "
                          "on startup (rc=%i, %ui errors found)",
                          initial_rc,
                          watcher->last_validation.total_errors);
        } else {
            watcher->applied_mtime = 0;
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: initial reload of \"%V\" failed "
                          "(rc=%i); starting from static conf, will retry on timer",
                          &watcher->path, initial_rc);
        }
    }

    ngx_add_timer(watcher->timer, NGX_HTTP_MARKDOWN_DYNCONF_WATCH_MS);

    ngx_log_error(NGX_LOG_INFO, log, 0,
                  "markdown: watching \"%V\" "
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
                  "markdown: stopped watching \"%V\"",
                  &watcher->path);
}


/* The line-oriented parser remains only for its legacy unit-test contract.
 * Production reloads use the bounded Rust JSON/FFI parser below. */
#if defined(NGX_HTTP_MARKDOWN_DYNCONF_LEGACY_TEST)

/*
 * Maximum line length in the dynamic config file.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE  1024

/*
 * Supported dynamic config keys and their enum values.
 * These are used as a dispatch table index in apply() and
 * as identifiers in validation error entries.  Values start
 * at 1 (0 is unused/invalid).
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_FILTER          1
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE     2
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_LOG_VERBOSITY   3
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_STREAMING_BUDGET 4
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_MEMORY_BUDGET   5
#define NGX_HTTP_MARKDOWN_DYNCONF_KEY_SCHEMA_VERSION  6

/*
 * Required schema version string for 0.9.0.
 * Missing or mismatched schema_version causes atomic file rejection.
 */
#define NGX_HTTP_MARKDOWN_DYNCONF_SCHEMA_VERSION_09  "0.9"

/*
 * File-scope flag tracking whether schema_version was seen during
 * the current reload parse.  Reset at the start of each reload
 * attempt, set by apply when schema_version is parsed.  Checked
 * post-parse to enforce the mandatory-field requirement.
 *
 * Safe because NGINX workers are single-threaded and the dynconf
 * timer handler is non-reentrant within a single worker.
 */
static ngx_uint_t ngx_http_markdown_dynconf_schema_version_seen;

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
    static u_char  schema_version_key[] = "schema_version";
    size_t  len;

    len = eq - p;

    /* Length-first comparison avoids string comparisons on mismatched
     * lengths.  All key names are ASCII; ngx_strncasecmp is safe
     * without NUL-termination because len is bounded by (eq - p). */
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
    } else if (len == 14 && ngx_strncasecmp(p, schema_version_key, 14) == 0) {
        *key = NGX_HTTP_MARKDOWN_DYNCONF_KEY_SCHEMA_VERSION;
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
 *   schema_version <version_string>   (mandatory, must be "0.9")
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

    if (value_len == 0) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown: dynconf %s value is empty",
                      key_name);
        return NGX_ERROR;
    }

    if (value_len > NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown: dynconf %s value too long "
                      "(%uz > %uz limit)", key_name,
                      value_len,
                      (size_t) NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE);
        return NGX_ERROR;
    }

    scratch = malloc(value_len);
    if (scratch == NULL) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown: invalid %s value \"%*s\" "
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
                      "markdown: invalid %s value \"%*s\" "
                      "(parse error)", key_name,
                      (int) value_len, value);
        return NGX_ERROR;
    }

    if (parsed < 0) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown: invalid %s value \"%*s\" "
                      "(negative result: %z)", key_name,
                      (int) value_len, value, parsed);
        return NGX_ERROR;
    }

    if ((size_t) parsed > max_size_t) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown: invalid %s value \"%*s\" "
                      "(exceeds maximum: %z > %z)", key_name,
                      (int) value_len, value,
                      (size_t) parsed, max_size_t);
        return NGX_ERROR;
    }

    *out = (size_t) parsed;  /* guarded by parsed >= 0 check above */
    return NGX_OK;
}

static ngx_int_t
ngx_http_markdown_dynconf_parse_on_off(u_char *value, size_t value_len,
                                       ngx_flag_t *out)
{
    static u_char  on_value[] = "on";
    static u_char  off_value[] = "off";

    if (value_len == 2 && ngx_strncasecmp(value, on_value, 2) == 0) {
        *out = 1;
        return NGX_OK;
    }

    if (value_len == 3 && ngx_strncasecmp(value, off_value, 3) == 0) {
        *out = 0;
        return NGX_OK;
    }

    return NGX_ERROR;
}

static ngx_int_t
ngx_http_markdown_dynconf_parse_verbosity(u_char *value, size_t value_len,
                                          ngx_uint_t *out)
{
    static u_char  error_value[] = "error";
    static u_char  warn_value[] = "warn";
    static u_char  info_value[] = "info";
    static u_char  debug_value[] = "debug";

    if (value_len == 5 && ngx_strncasecmp(value, error_value, 5) == 0) {
        *out = NGX_HTTP_MARKDOWN_LOG_ERROR;
        return NGX_OK;
    }

    if (value_len == 4 && ngx_strncasecmp(value, warn_value, 4) == 0) {
        *out = NGX_HTTP_MARKDOWN_LOG_WARN;
        return NGX_OK;
    }

    if (value_len == 4 && ngx_strncasecmp(value, info_value, 4) == 0) {
        *out = NGX_HTTP_MARKDOWN_LOG_INFO;
        return NGX_OK;
    }

    if (value_len == 5 && ngx_strncasecmp(value, debug_value, 5) == 0) {
        *out = NGX_HTTP_MARKDOWN_LOG_DEBUG;
        return NGX_OK;
    }

    return NGX_ERROR;
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
        if (ngx_http_markdown_dynconf_parse_on_off(
                value, value_len, &snapshot->enabled)
            != NGX_OK)
        {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: invalid markdown_filter value \"%*s\"",
                          (int) value_len, value);
            return NGX_ERROR;
        }
        snapshot->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
        snapshot->enabled_complex = NULL;
        break;

    /* Toggle noise pruning (boilerplate removal) on or off. */
    case NGX_HTTP_MARKDOWN_DYNCONF_KEY_PRUNE_NOISE:
        if (ngx_http_markdown_dynconf_parse_on_off(
                value, value_len, &snapshot->prune_noise)
            != NGX_OK)
        {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: invalid prune_noise value \"%*s\"",
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
        if (ngx_http_markdown_dynconf_parse_verbosity(
                value, value_len, &snapshot->log_verbosity)
            != NGX_OK)
        {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: invalid log_verbosity value \"%*s\"",
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
                      "markdown: streaming_budget not supported "
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

    /* Validate the schema_version field (mandatory, must be "0.9").
     * This key does not modify the snapshot — it is purely a
     * compatibility gate.  If the value is not "0.9", the entire
     * file is rejected.  The file-scope schema_version_seen flag
     * is set so post-parse validation can detect a missing field. */
    case NGX_HTTP_MARKDOWN_DYNCONF_KEY_SCHEMA_VERSION:
        {
            static u_char  expected[] = NGX_HTTP_MARKDOWN_DYNCONF_SCHEMA_VERSION_09;
            size_t         expected_len = sizeof(NGX_HTTP_MARKDOWN_DYNCONF_SCHEMA_VERSION_09) - 1;

            if (value_len != expected_len
                || ngx_strncasecmp(value, expected, expected_len) != 0)
            {
                ngx_log_error(NGX_LOG_WARN, log, 0,
                              "markdown: unsupported schema_version "
                              "\"%*s\" (expected \""
                              NGX_HTTP_MARKDOWN_DYNCONF_SCHEMA_VERSION_09
                              "\")",
                              (int) value_len, value);
                return NGX_ERROR;
            }
            ngx_http_markdown_dynconf_schema_version_seen = 1;
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
                      "markdown: buffer position overflow in \"%V\"",
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
                      "markdown: read overflow in \"%V\"",
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
                      "markdown: parse error in \"%V\", "
                      "discarding staging; active config unchanged",
                      &watcher->path);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    if (*pos >= NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown: line too long in \"%V\", "
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
    const ngx_http_markdown_dynconf_validation_error_t  *entry;

    if (result == NULL || result->total_errors == 0) {
        return;
    }

    for (ngx_uint_t i = 0; i < result->count; i++) {
        entry = &result->errors[i];
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown: error in \"%V\" "
                      "line %ui, field \"%*s\", reason: %*s",
                      path,
                      entry->line,
                      entry->field_len, entry->field,
                      entry->reason_len, entry->reason);
    }

    if (result->total_errors > result->count) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown: %ui total errors in "
                      "\"%V\" (%ui shown, %ui truncated)",
                      result->total_errors, path,
                      result->count,
                      result->total_errors - result->count);
    }

    ngx_log_error(NGX_LOG_WARN, log, 0,
                  "markdown: validation failed for "
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
    const u_char *eq;
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
     *
     * key_names is indexed by (key - 1) where key is the
     * NGX_HTTP_MARKDOWN_DYNCONF_KEY_* enum (1..6).
     */
    {
        static u_char  key_names[][20] = {
            "markdown_filter",
            "prune_noise",
            "log_verbosity",
            "streaming_budget",
            "memory_budget",
            "schema_version"
        };
        static size_t  key_name_lens[] = { 15, 11, 13, 16, 13, 14 };

        const u_char  *field_name;
        size_t         field_name_len;

        if (key >= 1 && key <= 6) {
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


/*
 * Buffer context for dry-run line processing.
 *
 * Bundles the mutable scan state (buffer pointer, current position,
 * line start offset, line counter, and applied-key counter) into a
 * single struct so that ngx_http_markdown_dynconf_process_buffer_dryrun
 * can pass them to try_line_dryrun without an excessive parameter list.
 *
 * All pointers are borrowed from the caller's stack frame; this struct
 * does not own any memory.
 */
typedef struct {
    u_char       *buf;        /* read buffer (caller-owned) */
    size_t       *pos;        /* [in/out] current end of valid data in buf */
    size_t       *line_start; /* [in/out] byte offset of next unprocessed line */
    ngx_uint_t   *line_num;   /* [in/out] 1-based line counter for error reporting */
    ngx_uint_t   *applied;    /* [in/out] count of successfully applied entries */
} ngx_http_markdown_dynconf_buf_ctx_t;

/**
 * Process complete lines from the read buffer in dry-run mode.
 *
 * Similar to ngx_http_markdown_dynconf_process_buffer but uses
 * the dry-run line handler that collects errors instead of aborting.
 * Tracks line numbers for error reporting.
 *
 * @param snapshot    Staging snapshot to apply valid lines to.
 * @param bctx       Buffer context bundling buf/pos/line_start/line_num/applied.
 * @param log        Logger.
 * @param result     Validation result to collect errors into.
 *
 * @returns NGX_OK always (errors are collected, not fatal).
 */
static ngx_int_t
ngx_http_markdown_dynconf_process_buffer_dryrun(
    ngx_http_markdown_dynconf_snapshot_t *snapshot,
    ngx_http_markdown_dynconf_buf_ctx_t *bctx,
    ngx_log_t *log,
    ngx_http_markdown_dynconf_validation_result_t *result)
{
    for ( ;; ) {
        size_t  i;

        /* Find newline. */
        i = *bctx->line_start;
        while (i < *bctx->pos && bctx->buf[i] != '\n') {
            i++;
        }

        if (i >= *bctx->pos) {
            /* No complete line; shift remaining data. */
            if (*bctx->line_start > *bctx->pos) {
                return NGX_ERROR;
            }
            ngx_memmove(bctx->buf, bctx->buf + *bctx->line_start,
                        *bctx->pos - *bctx->line_start);
            *bctx->pos -= *bctx->line_start;
            *bctx->line_start = 0;
            return NGX_OK;
        }

        ngx_http_markdown_dynconf_try_line_dryrun(
            snapshot, bctx->buf + *bctx->line_start,
            ngx_http_markdown_dynconf_line_len(bctx->buf, *bctx->line_start, i),
            *bctx->line_num, log, bctx->applied, result);

        (*bctx->line_num)++;
        *bctx->line_start = i + 1;
    }
}


/**
 * Dry-run helper for ngx_http_markdown_dynconf_reload.
 *
 * Parses all lines collecting errors instead of aborting at the first
 * failure, providing operators with a complete list of issues to fix.
 *
 * @param watcher Dynamic config watcher containing the file path and snapshots.
 * @param fd     Open file descriptor for the dynconf file.
 * @param buf    Read buffer (NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE bytes).
 * @param log    Logger for warnings and informational messages.
 *
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK   if validation passed
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL if validation found errors
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR     on read error
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE on read_chunk failure
 */
static ngx_int_t
ngx_http_markdown_dynconf_reload_dryrun(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_fd_t fd,
    u_char *buf,
    ngx_log_t *log)
{
    ssize_t    n;
    size_t     line_start;
    size_t     pos;
    ngx_uint_t applied;
    ngx_int_t  line_rc;
    ngx_uint_t line_num;

    /* Initialize parse state: applied counts valid key=value pairs,
       pos tracks the current read position, line_start marks the
       beginning of the current line within buf, line_num is 1-based. */
    applied = 0;
    line_start = 0;
    pos = 0;
    line_num = 1;

    /* Read and process the file in chunks until EOF or error. */
    for ( ;; ) {
        /* Fill buf from fd; read_chunk updates pos with the new end. */
        line_rc = ngx_http_markdown_dynconf_read_chunk(
            fd, buf, &pos, NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE,
            &watcher->path, log, &n);
        if (line_rc != NGX_OK) {
            /* Propagate read_chunk failure (INVALID_FILE or similar). */
            ngx_close_file(fd);
            return line_rc;
        }

        if (n == 0) {
            /* EOF reached: exit the read loop to process the tail. */
            break;
        }

        if (n == -1) {
            /* I/O error on read: log and abort. */
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: read error on \"%V\"",
                          &watcher->path);
            ngx_close_file(fd);
            return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
        }

        /* Line-length overflow: the buffer filled before a newline
           was found, meaning the line exceeds MAX_LINE.  Record the
           error, then report final validation status and return. */
        if (pos >= NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE) {
            ngx_http_markdown_dynconf_record_error(
                &watcher->last_validation, line_num,
                (const u_char *) "(line)", sizeof("(line)") - 1,
                (const u_char *) "line too long",
                sizeof("line too long") - 1);
            ngx_close_file(fd);

            watcher->last_validation.valid = 0;
            ngx_http_markdown_dynconf_log_validation_errors(
                &watcher->last_validation, &watcher->path, log);
            return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL;
        }

        /* Process complete lines within the chunk: parse each
           key=value pair in dry-run mode, recording errors rather
           than aborting on the first failure. */
        {
            ngx_http_markdown_dynconf_buf_ctx_t  bctx;
            bctx.buf = buf;
            bctx.pos = &pos;
            bctx.line_start = &line_start;
            bctx.line_num = &line_num;
            bctx.applied = &applied;
            ngx_http_markdown_dynconf_process_buffer_dryrun(
                &watcher->staging_snapshot, &bctx, log,
                &watcher->last_validation);
        }
    }

    /* Close the file descriptor now that all reads are complete. */
    ngx_close_file(fd);

    /* Handle a trailing line without a terminating newline: process
       the remaining bytes from line_start to pos as the last line. */
    if (line_start < pos) {
        ngx_http_markdown_dynconf_try_line_dryrun(
            &watcher->staging_snapshot, buf + line_start,
            ngx_http_markdown_dynconf_line_len(buf, line_start, pos),
            line_num, log, &applied, &watcher->last_validation);
    }

    /* Final validation: if any errors were accumulated across all
       lines, mark invalid and report; otherwise mark valid. */
    if (watcher->last_validation.total_errors > 0) {
        watcher->last_validation.valid = 0;
        ngx_http_markdown_dynconf_log_validation_errors(
            &watcher->last_validation, &watcher->path, log);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL;
    }

    /* schema_version is mandatory (spec 45/53).  Record missing
     * schema_version as a validation error in dry-run mode. */
    if (!ngx_http_markdown_dynconf_schema_version_seen) {
        ngx_http_markdown_dynconf_record_error(
            &watcher->last_validation, 0,
            (const u_char *) "schema_version",
            sizeof("schema_version") - 1,
            (const u_char *) "required field missing; expected "
            NGX_HTTP_MARKDOWN_DYNCONF_SCHEMA_VERSION_09,
            sizeof("required field missing; expected "
                   NGX_HTTP_MARKDOWN_DYNCONF_SCHEMA_VERSION_09) - 1);
        watcher->last_validation.valid = 0;
        ngx_http_markdown_dynconf_log_validation_errors(
            &watcher->last_validation, &watcher->path, log);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL;
    }

    watcher->last_validation.valid = 1;

    if (applied > 0) {
        ngx_log_error(NGX_LOG_INFO, log, 0,
                      "markdown: dry-run validation passed "
                      "for \"%V\" (%ui settings validated, "
                      "not applied)",
                      &watcher->path, applied);
    } else {
        ngx_log_error(NGX_LOG_INFO, log, 0,
                      "markdown: dry-run validation passed "
                      "for \"%V\" (0 effective keys, not applied)",
                      &watcher->path);
    }

    return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK;
}


/**
 * Normal (non-dry-run) helper for ngx_http_markdown_dynconf_reload.
 *
 * Parses all lines, aborting on first error.  On success, commits
 * the staging snapshot to active and applies it to the live config.
 *
 * @param watcher Dynamic config watcher containing the file path and snapshots.
 * @param conf   Current module location configuration to update on commit.
 * @param fd     Open file descriptor for the dynconf file.
 * @param buf    Read buffer (NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE bytes).
 * @param log    Logger for warnings and informational messages.
 *
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED      if settings were applied
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE    if no effective keys found
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE on parse error
 * @returns NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR     on read error
 */
static ngx_int_t
ngx_http_markdown_dynconf_reload_normal(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_http_markdown_conf_t *conf,
    ngx_fd_t fd,
    u_char *buf,
    ngx_log_t *log)
{
    ssize_t    n;
    size_t     line_start;
    size_t     pos;
    ngx_uint_t applied;
    ngx_int_t  line_rc;

    applied = 0;
    line_start = 0;
    pos = 0;

    for ( ;; ) {
        line_rc = ngx_http_markdown_dynconf_read_chunk(
            fd, buf, &pos, NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE,
            &watcher->path, log, &n);
        if (line_rc != NGX_OK) {
            ngx_close_file(fd);
            return line_rc;
        }

        if (n == 0) {
            break;
        }

        if (n == -1) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: read error on \"%V\"",
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

    if (line_start < pos) {
        line_rc = ngx_http_markdown_dynconf_try_line(
            &watcher->staging_snapshot, buf + line_start,
            ngx_http_markdown_dynconf_line_len(buf, line_start, pos),
            log, &applied);

        if (line_rc != NGX_OK) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: parse error on final line "
                          "in \"%V\", discarding staging",
                          &watcher->path);
            return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        }
    }

    /* schema_version is mandatory (spec 45/53).  If missing, the
     * entire file is rejected to prevent loading config files
     * intended for a different module version. */
    if (!ngx_http_markdown_dynconf_schema_version_seen) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
                      "markdown: missing required \"schema_version\" "
                      "in \"%V\"; expected schema_version = "
                      NGX_HTTP_MARKDOWN_DYNCONF_SCHEMA_VERSION_09,
                      &watcher->path);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    if (applied > 0) {
        watcher->last_known_good = watcher->active_snapshot;
        watcher->lkg_valid = 1;
        /* The config being preserved as LKG is the one currently active,
         * whose file mtime is applied_mtime (the caller overwrites
         * applied_mtime with last_mtime only after this returns). */
        watcher->lkg_mtime = watcher->applied_mtime;

        /*
         * Preserve the current active_digest as the LKG digest
         * before overwriting active_digest with the new value.
         * lkg_digest always refers to the active_digest of the
         * last-known-good configuration (canonical form), not
         * the source_digest. (Requirement 3.16)
         */
        if (watcher->active_digest[0] != '\0') {
            ngx_memcpy(watcher->lkg_digest, watcher->active_digest,
                       NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN);
        }

        /*
         * SHALLOW COPY: active_snapshot = staging_snapshot is a C struct
         * assignment (bitwise copy).  NGINX worker event-loop ordering
         * makes a plain assignment the correct lifecycle primitive here;
         * do not use atomic builtins on this aggregate snapshot because
         * coverage builds treat large/misaligned atomic struct access as
         * a compile error.  The only pointer field
         * (enabled_complex) points into the cycle-level ngx_conf_t
         * which outlives both snapshots.  If a new pointer field is
         * added to ngx_http_markdown_dynconf_snapshot_t that references
         * staging-local memory, this assignment must be reviewed for
         * use-after-free.
         *
         * Compile-time guard: if a field is added, sizeof changes and
         * this assertion fires, forcing a review of shallow-copy safety.
         */
        watcher->active_snapshot = watcher->staging_snapshot;
#ifdef MARKDOWN_STREAMING_ENABLED
        _Static_assert(
            sizeof(ngx_http_markdown_dynconf_snapshot_t)
                == 10 * sizeof(void *),
            "dynconf_snapshot_t layout changed, review shallow copy");
#else
        _Static_assert(
            sizeof(ngx_http_markdown_dynconf_snapshot_t)
                == 9 * sizeof(void *),
            "dynconf_snapshot_t layout changed, review shallow copy");
#endif

        /*
         * Increment generation counter on every successful reload.
         * WHEN source_digest changes but active_digest does not
         * (formatting-only change): generation still increments
         * because the file content has changed.  This is NOT a
         * validated_noop. (Requirement 3.16)
         *
         * Generation starts at 0 (no reload yet) and becomes 1
         * on first successful reload.
         */
        watcher->generation++;
        watcher->version++;

        ngx_http_markdown_dynconf_apply_snapshot(conf,
                                                  &watcher->active_snapshot);

        ngx_log_error(NGX_LOG_INFO, log, 0,
                      "markdown: applied %ui settings from \"%V\" "
                      "(version=%ui, generation=%ui, lkg preserved)",
                      applied, &watcher->path, watcher->version,
                      watcher->generation);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED;
    }

    return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE;
}


static ngx_int_t
ngx_http_markdown_dynconf_reload(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_http_markdown_conf_t *conf,
    ngx_log_t *log)
{
    u_char     path_buf[NGX_MAX_PATH + 1];
    ngx_fd_t   fd;
    u_char     buf[NGX_HTTP_MARKDOWN_DYNCONF_MAX_LINE];
    ngx_uint_t dry_run;

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
                      "markdown: failed to open \"%V\" for reload",
                      &watcher->path);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    watcher->staging_snapshot = watcher->active_snapshot;

    /* Reset per-reload schema_version tracking (spec 45/53). */
    ngx_http_markdown_dynconf_schema_version_seen = 0;

    if (dry_run) {
        ngx_memzero(&watcher->last_validation,
                    sizeof(ngx_http_markdown_dynconf_validation_result_t));
        return ngx_http_markdown_dynconf_reload_dryrun(watcher, fd, buf, log);
    }

    return ngx_http_markdown_dynconf_reload_normal(watcher, conf, fd, buf, log);
}

#else /* NGX_HTTP_MARKDOWN_DYNCONF_LEGACY_TEST */

static ngx_int_t
ngx_http_markdown_dynconf_read_file(
    ngx_http_markdown_dynconf_watcher_t *watcher, ngx_log_t *log,
    u_char **data, size_t *file_size)
{
    u_char             path_buf[NGX_MAX_PATH + 1];
    ngx_fd_t           fd;
    ngx_file_info_t    file_info;
    size_t             offset;
    size_t             bytes_read;
    ssize_t            nread;

    if (watcher == NULL || log == NULL || data == NULL || file_size == NULL
        || watcher->path.data == NULL || watcher->path.len > NGX_MAX_PATH)
    {
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    ngx_memcpy(path_buf, watcher->path.data, watcher->path.len);
    path_buf[watcher->path.len] = '\0';

    if (ngx_file_info(path_buf, &file_info) == NGX_FILE_ERROR) {
        ngx_http_markdown_record_dynconf_reload(DYNCONF_ERR_INTERNAL);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }
    if (file_info.st_size < 0
        || (uint64_t) file_info.st_size > NGX_HTTP_MARKDOWN_DYNCONF_MAX_FILE_SIZE
        || (uint64_t) file_info.st_size > NGX_MAX_SIZE_T_VALUE)
    {
        ngx_http_markdown_record_dynconf_reload(DYNCONF_ERR_TOO_LARGE);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    *file_size = (size_t) file_info.st_size;
    *data = ngx_alloc(*file_size == 0 ? 1 : *file_size, log);
    if (*data == NULL) {
        ngx_http_markdown_record_dynconf_reload(DYNCONF_ERR_INTERNAL);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    fd = ngx_open_file(path_buf, NGX_FILE_RDONLY, NGX_FILE_OPEN, 0);
    if (fd == NGX_INVALID_FILE) {
        ngx_free(*data);
        *data = NULL;
        ngx_http_markdown_record_dynconf_reload(DYNCONF_ERR_INTERNAL);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    offset = 0;
    while (offset < *file_size) {
        nread = ngx_read_fd(fd, *data + offset, *file_size - offset);
        if (nread < 0 || nread == 0
            || (uint64_t) nread > (uint64_t) (*file_size - offset))
        {
            ngx_close_file(fd);
            ngx_free(*data);
            *data = NULL;
            ngx_http_markdown_record_dynconf_reload(DYNCONF_ERR_INTERNAL);
            return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
        }
        bytes_read = (size_t) nread; /* CWE-190:guarded */
        offset += bytes_read;
    }
    ngx_close_file(fd);
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_dynconf_copy_digest(
    u_char *dst, size_t dst_size, const uint8_t *src, size_t src_len)
{
    static const u_char prefix[] = NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_PREFIX;
    size_t prefix_len = sizeof(prefix) - 1;

    if (dst == NULL || src == NULL || src_len != 64
        || dst_size < prefix_len + src_len + 1)
    {
        return NGX_ERROR;
    }

    ngx_memcpy(dst, prefix, prefix_len);
    ngx_memcpy(dst + prefix_len, src, src_len);
    dst[prefix_len + src_len] = '\0';
    return NGX_OK;
}


static void
ngx_http_markdown_dynconf_record_error(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    const FFIDynconfResult *result)
{
    size_t length;

    if (watcher == NULL || result == NULL) {
        return;
    }

    watcher->last_error_len = 0;
    watcher->last_error[0] = '\0';
    if (result->error_message == NULL || result->error_message_len == 0) {
        return;
    }

    length = result->error_message_len;
    if (length > sizeof(watcher->last_error) - 1) {
        length = sizeof(watcher->last_error) - 1;
    }
    ngx_memcpy(watcher->last_error, result->error_message, length);
    watcher->last_error[length] = '\0';
    watcher->last_error_len = length;
}


static ngx_int_t
ngx_http_markdown_dynconf_apply_ffi_result(
    ngx_http_markdown_dynconf_snapshot_t *snapshot,
    const FFIDynconfResult *result)
{
    if (snapshot == NULL || result == NULL || result->error_code != DYNCONF_OK) {
        return NGX_ERROR;
    }

    if (result->filter != DYNCONF_NOT_SET_U8) {
        snapshot->enabled = result->filter == DYNCONF_FILTER_ON;
        snapshot->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
        snapshot->enabled_complex = NULL;
    }
    if (result->prune_noise != DYNCONF_NOT_SET_U8) {
        snapshot->prune_noise = result->prune_noise == DYNCONF_PRUNE_NOISE_ON;
    }
    if (result->log_verbosity != DYNCONF_NOT_SET_U8) {
        snapshot->log_verbosity = result->log_verbosity;
    }
    if (result->error_policy != DYNCONF_NOT_SET_U8) {
        switch (result->error_policy) {
        case DYNCONF_POLICY_PASS:
            snapshot->error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
            snapshot->error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;
            break;
        case DYNCONF_POLICY_FAIL_CLOSED:
            snapshot->error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
            snapshot->error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;
            break;
        case DYNCONF_POLICY_STATUS_429:
            snapshot->error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
            snapshot->error_status = NGX_HTTP_TOO_MANY_REQUESTS;
            break;
        case DYNCONF_POLICY_STATUS_503:
            snapshot->error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
            snapshot->error_status = NGX_HTTP_SERVICE_UNAVAILABLE;
            break;
        default:
            return NGX_ERROR;
        }
    }
#ifdef MARKDOWN_STREAMING_ENABLED
    if (result->streaming_buffer != DYNCONF_NOT_SET_U64) {
        if (result->streaming_buffer > NGX_MAX_SIZE_T_VALUE) {
            return NGX_ERROR;
        }
        snapshot->streaming_budget = (size_t) result->streaming_buffer;
    }
#else
    if (result->streaming_buffer != DYNCONF_NOT_SET_U64) {
        return NGX_ERROR;
    }
#endif
    snapshot->valid = 1;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_dynconf_reload(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_http_markdown_conf_t *conf,
    ngx_log_t *log)
{
    u_char             *data;
    size_t              file_size;
    ngx_int_t            rc;
    u_char               next_source_digest[
        NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    u_char               next_active_digest[
        NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    FFIDynconfResult    result;

    if (watcher == NULL || conf == NULL || log == NULL)
    {
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    rc = ngx_http_markdown_dynconf_read_file(watcher, log, &data, &file_size);
    if (rc != NGX_OK) {
        watcher->last_result = rc;
        return rc;
    }

    markdown_dynconf_result_init(&result);
    markdown_dynconf_parse(data, file_size, &result);
    ngx_free(data);

    if (result.error_code != DYNCONF_OK) {
        ngx_http_markdown_record_dynconf_reload(result.error_code);
        ngx_http_markdown_dynconf_record_error(watcher, &result);
        watcher->last_result = conf->advanced.dynconf_dry_run
            ? NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL
            : NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        rc = conf->advanced.dynconf_dry_run
            ? NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL
            : NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        markdown_dynconf_result_free(&result);
        return rc;
    }

    watcher->staging_snapshot = watcher->active_snapshot;
    if (ngx_http_markdown_dynconf_apply_ffi_result(
            &watcher->staging_snapshot, &result) != NGX_OK)
    {
        ngx_http_markdown_record_dynconf_reload(DYNCONF_ERR_INVALID_TYPE);
        watcher->last_result = NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        markdown_dynconf_result_free(&result);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    if (ngx_http_markdown_dynconf_copy_digest(
            next_source_digest, sizeof(next_source_digest),
            result.source_digest, result.source_digest_len) != NGX_OK
        || ngx_http_markdown_dynconf_copy_digest(
            next_active_digest, sizeof(next_active_digest),
            result.active_digest, result.active_digest_len) != NGX_OK)
    {
        ngx_http_markdown_record_dynconf_reload(DYNCONF_ERR_INTERNAL);
        watcher->last_result = NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        markdown_dynconf_result_free(&result);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    if (conf->advanced.dynconf_dry_run) {
        ngx_http_markdown_record_dynconf_reload(DYNCONF_OK);
        watcher->last_result = NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK;
        watcher->last_error_len = 0;
        watcher->last_error[0] = '\0';
        markdown_dynconf_result_free(&result);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK;
    }

    if (watcher->generation > 0) {
        watcher->last_known_good = watcher->active_snapshot;
        watcher->lkg_valid = 1;
        ngx_memcpy(watcher->lkg_digest, watcher->active_digest,
                   sizeof(watcher->lkg_digest));
        watcher->lkg_mtime = watcher->applied_mtime;
    }
    ngx_memcpy(watcher->source_digest, next_source_digest,
               sizeof(watcher->source_digest));
    ngx_memcpy(watcher->active_digest, next_active_digest,
               sizeof(watcher->active_digest));
    watcher->active_snapshot = watcher->staging_snapshot;
    ngx_http_markdown_dynconf_apply_snapshot(conf, &watcher->active_snapshot);
    watcher->generation++;
    watcher->version++;
    watcher->applied_mtime = watcher->last_mtime;
    watcher->last_success = ngx_time();
    watcher->last_result = NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED;
    ngx_http_markdown_record_dynconf_reload(DYNCONF_OK);
    watcher->last_error_len = 0;
    watcher->last_error[0] = '\0';
    if (!watcher->lkg_valid) {
        ngx_memcpy(watcher->lkg_digest, watcher->active_digest,
                   sizeof(watcher->lkg_digest));
    }
    markdown_dynconf_result_free(&result);
    return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED;
}

#endif /* NGX_HTTP_MARKDOWN_DYNCONF_LEGACY_TEST */


#endif /* NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H */
