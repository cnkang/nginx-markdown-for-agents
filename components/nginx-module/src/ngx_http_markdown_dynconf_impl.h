/*
 * NGINX Markdown Filter Module - Dynamic Configuration Hot-Reload
 *
 * Enables runtime modification of module configuration without
 * NGINX restart.  Uses a periodic timer event to poll the
 * configuration file for mtime changes, then reloads into a
 * staging snapshot.  Only if the entire file parses successfully
 * is the active snapshot replaced, guaranteeing atomicity.
 *
 * Architecture (0.9.2 JSON schema v1 / LKG / request-snapshot model):
 *   - Dedicated file watcher per worker process
 *   - Coarse-grained polling (1s interval via ngx_event_t timer)
 *   - On mtime change, the timer handler reads and parses the
 *     entire file into a staging snapshot.  If the JSON schema v1
 *     parses and applies successfully, the staging snapshot atomically
 *     replaces the active snapshot (last-known-good, LKG).  On any
 *     parse error the staging is discarded and the active snapshot
 *     is preserved.
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
 */

#ifndef NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H
#define NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H

#include <stdlib.h>
#include <sys/types.h>

#include "ngx_http_markdown_dynconf_precedence.h"

#if defined(__APPLE__)
#define NGX_HTTP_MARKDOWN_STAT_MTIME_NSEC(fi) ((fi).st_mtimespec.tv_nsec)
#elif defined(__linux__) \
    && ((defined(_POSIX_C_SOURCE) && _POSIX_C_SOURCE >= 200809L) \
        || defined(_GNU_SOURCE))
#define NGX_HTTP_MARKDOWN_STAT_MTIME_NSEC(fi) ((fi).st_mtim.tv_nsec)
#else
#define NGX_HTTP_MARKDOWN_STAT_MTIME_NSEC(fi) 0L
#endif

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

/* Streaming buffer bounds shared by static and dynamic configuration. */
#define NGX_HTTP_MARKDOWN_DYNCONF_STREAMING_BUFFER_MIN  65536
#define NGX_HTTP_MARKDOWN_DYNCONF_STREAMING_BUFFER_MAX  1073741824

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

/* Maximum delay between retries after a transient file I/O failure. */
#define NGX_HTTP_MARKDOWN_DYNCONF_MAX_IO_RETRY_TICKS  16

/*
 * SHA-256 hex digest length: 72 = 'sha256:' prefix (7) + 64 hex
 * chars + NUL terminator.
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
    size_t       conversion_memory;
    const ngx_http_markdown_loc_validation_summary_t *validation_summary;
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
 * applied_mtime tracks the mtime that has been accepted by the
 * watcher.  Invalid content advances applied_mtime after its
 * rejection has been recorded, while I/O failures leave it behind
 * and use bounded retry backoff.
 *
 * last_known_good holds the previous active snapshot that was
 * replaced by the most recent successful reload.  When
 * lkg_valid is set, diagnostics can report the preserved state and
 * a failed file reload leaves the active snapshot unchanged.
 * The LKG is NOT updated on validation failure — only a
 * successful reload promotes the current active to LKG.
 */
typedef struct {
    time_t        last_mtime;
    time_t        applied_mtime;
    dev_t         file_dev;
    ino_t         file_ino;
    off_t         file_size;
    time_t        file_mtime_sec;
    long          file_mtime_nsec;
    ngx_uint_t    tick_counter;
    ngx_uint_t    io_retry_delay_ticks;
    ngx_uint_t    io_retry_remaining_ticks;
    u_char        rejected_source_digest[
        NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
} ngx_http_markdown_dynconf_file_state_t;

typedef struct {
    ngx_uint_t    generation;
    u_char        source_digest[NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    u_char        active_digest[NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    u_char        lkg_digest[NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    ngx_uint_t    lkg_valid;
    time_t        lkg_mtime;
} ngx_http_markdown_dynconf_digest_state_t;

typedef struct {
    ngx_uint_t    version;
    ngx_http_markdown_dynconf_validation_result_t  last_validation;
    ngx_uint_t    last_result;
    time_t        last_success;
    u_char        last_error[513];
    size_t        last_error_len;
    u_char        last_rejected_source_digest[
        NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    u_char        last_rejected_error[513];
    size_t        last_rejected_error_len;
    ngx_uint_t    last_masked_fields;
} ngx_http_markdown_dynconf_diagnostic_state_t;

typedef struct {
    ngx_str_t     path;
    ngx_event_t  *timer;
    ngx_uint_t    active;
    ngx_http_markdown_dynconf_file_state_t       file_state;
    ngx_http_markdown_dynconf_digest_state_t     digest_state;
    ngx_http_markdown_dynconf_snapshot_t         active_snapshot;
    ngx_http_markdown_dynconf_snapshot_t         static_snapshot;
    ngx_http_markdown_dynconf_snapshot_t         staging_snapshot;
    ngx_http_markdown_dynconf_snapshot_t         last_known_good;
    ngx_http_markdown_conf_t                    *conf;
    const ngx_http_markdown_loc_validation_summary_t *validation_summary;
    ngx_http_markdown_dynconf_diagnostic_state_t diagnostic_state;
} ngx_http_markdown_dynconf_watcher_t;

static void
ngx_http_markdown_dynconf_reset_io_retry(
    ngx_http_markdown_dynconf_file_state_t *file_state)
{
    if (file_state == NULL) {
        return;
    }

    file_state->io_retry_delay_ticks = 0;
    file_state->io_retry_remaining_ticks = 0;
}

static void
ngx_http_markdown_dynconf_schedule_io_retry(
    ngx_http_markdown_dynconf_file_state_t *file_state)
{
    if (file_state == NULL) {
        return;
    }

    if (file_state->io_retry_delay_ticks == 0) {
        file_state->io_retry_delay_ticks = 1;
    } else if (file_state->io_retry_delay_ticks
               < NGX_HTTP_MARKDOWN_DYNCONF_MAX_IO_RETRY_TICKS)
    {
        file_state->io_retry_delay_ticks *= 2;
        if (file_state->io_retry_delay_ticks
            > NGX_HTTP_MARKDOWN_DYNCONF_MAX_IO_RETRY_TICKS)
        {
            file_state->io_retry_delay_ticks =
                NGX_HTTP_MARKDOWN_DYNCONF_MAX_IO_RETRY_TICKS;
        }
    }

    file_state->io_retry_remaining_ticks =
        file_state->io_retry_delay_ticks;
}

static void ngx_http_markdown_dynconf_snapshot_from_conf(
    ngx_http_markdown_dynconf_snapshot_t *snapshot,
    const ngx_http_markdown_conf_t *conf);

/*
 * Reset the staging snapshot to the immutable static/http baseline.
 *
 * The live module configuration is updated when a dynamic snapshot is
 * published, so it cannot be used as the baseline for a later generation.
 * Tests and narrow callers that do not run the full watcher startup path use
 * the live configuration as a guarded fallback.
 */
static void
ngx_http_markdown_dynconf_snapshot_reset_baseline(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    const ngx_http_markdown_conf_t *conf)
{
    if (watcher == NULL || conf == NULL) {
        return;
    }

    if (watcher->static_snapshot.valid) {
        watcher->staging_snapshot = watcher->static_snapshot;
    } else {
        ngx_http_markdown_dynconf_snapshot_from_conf(
            &watcher->staging_snapshot, conf);
    }
    watcher->staging_snapshot.validation_summary =
        watcher->validation_summary;
}

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
    snapshot->conversion_memory = conf->limits.conversion_memory;
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
    /* conversion_memory is the frozen conversion bound; restore it so
     * effective_memory_budget reads the snapshot-consistent value. */
    conf->limits.conversion_memory = snapshot->conversion_memory;
}


static void
ngx_http_markdown_select_effective_uint(
    ngx_uint_t *value, ngx_uint_t *provenance, ngx_uint_t mask,
    ngx_uint_t block, ngx_flag_t snap_valid, ngx_uint_t static_value,
    ngx_uint_t dynamic_value)
{
    if (!(mask & block) && snap_valid) {
        *value = dynamic_value;
        *provenance = NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
        return;
    }

    *value = static_value;
    *provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
}


static void
ngx_http_markdown_select_effective_flag(
    ngx_flag_t *value, ngx_uint_t *provenance, ngx_uint_t mask,
    ngx_uint_t block, ngx_flag_t snap_valid, ngx_flag_t static_value,
    ngx_flag_t dynamic_value)
{
    if (!(mask & block) && snap_valid) {
        *value = dynamic_value;
        *provenance = NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
        return;
    }

    *value = static_value;
    *provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
}


static void
ngx_http_markdown_select_effective_filter(
    ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_dynconf_snapshot_t *snap,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t mask,
    ngx_flag_t snap_valid)
{
    if (conf->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_COMPLEX) {
        eff->enabled = conf->enabled;
        eff->enabled_source = conf->enabled_source;
        eff->filter_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_REQUEST_VARIABLE;
        return;
    }

    if (!(mask & NGX_HTTP_MARKDOWN_BLOCK_FILTER) && snap_valid) {
        eff->enabled = snap->enabled;
        eff->enabled_source = snap->enabled_source;
        eff->filter_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
        return;
    }

    eff->enabled = conf->enabled;
    eff->enabled_source = conf->enabled_source;
    eff->filter_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
}


static void
ngx_http_markdown_select_effective_error(
    ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_dynconf_snapshot_t *snap,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t mask,
    ngx_flag_t snap_valid)
{
    if (!(mask & NGX_HTTP_MARKDOWN_BLOCK_ERROR_POLICY) && snap_valid) {
        eff->error_policy = snap->error_policy;
        eff->error_status = snap->error_status;
        eff->error_policy_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
        return;
    }

    eff->error_policy = conf->on_error;
    eff->error_status = conf->error_status;
    eff->error_policy_provenance =
        NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
}


#ifdef MARKDOWN_STREAMING_ENABLED
static void
ngx_http_markdown_select_effective_streaming(
    ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_dynconf_snapshot_t *snap,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t mask,
    ngx_flag_t snap_valid)
{
    if (!(mask & NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER) && snap_valid) {
        eff->streaming_budget = snap->streaming_budget;
        eff->streaming_buffer = snap->streaming_budget;
        eff->streaming_buffer_provenance =
            NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF;
        return;
    }

    eff->streaming_budget = conf->stream.budget;
    eff->streaming_buffer = conf->stream.budget;
    eff->streaming_buffer_provenance =
        NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
}
#endif


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

    ngx_http_markdown_select_effective_filter(
        eff, snap, conf, mask, snap_valid);
    ngx_http_markdown_select_effective_flag(
        &eff->prune_noise, &eff->prune_noise_provenance, mask,
        NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE, snap_valid,
        conf->advanced.prune_noise, snap != NULL ? snap->prune_noise : 0);
    ngx_http_markdown_select_effective_uint(
        &eff->log_verbosity, &eff->log_verbosity_provenance, mask,
        NGX_HTTP_MARKDOWN_BLOCK_LOG_VERBOSITY, snap_valid,
        conf->policy.log_verbosity, snap != NULL ? snap->log_verbosity : 0);
    ngx_http_markdown_select_effective_error(
        eff, snap, conf, mask, snap_valid);

    /* memory_budget is static in the dynconf schema; the effective
     * projection carries the frozen conversion_memory limit so request
     * paths read one consistent value. */
    eff->memory_budget = conf->limits.conversion_memory;

#ifdef MARKDOWN_STREAMING_ENABLED
    ngx_http_markdown_select_effective_streaming(
        eff, snap, conf, mask, snap_valid);
#else
    eff->streaming_buffer = conf->stream.budget;
    eff->streaming_buffer_provenance = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
#endif
}


/*
 * Bind the captured snapshot and effective view into request storage.
 * Production request code and conformance tests use this same seam so the
 * allocation and dynconf gating rules cannot silently diverge.
 *
 * The effective view is copied by value into `eff_storage` (owned by the
 * caller, typically the request context).  There is no pool allocation for
 * the effective view.  If the optional request-pool snapshot allocation
 * fails, the captured header-time effective view still binds; only the
 * snapshot pointer is unavailable.  This keeps later phases on the same
 * configuration view (bind-once invariant).
 */
static void
ngx_http_markdown_bind_request_snapshot(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_dynconf_snapshot_t *snap_copy,
    const ngx_http_markdown_effective_conf_t *early_eff,
    ngx_http_markdown_effective_conf_t *eff_storage,
    ngx_http_markdown_dynconf_snapshot_t **snapshot_slot,
    ngx_http_markdown_effective_conf_t **effective_slot)
{
    ngx_log_t  *log;

    if (r == NULL || r->pool == NULL || r->connection == NULL
        || conf == NULL || snapshot_slot == NULL
        || effective_slot == NULL || eff_storage == NULL)
    {
        return;
    }

    log = r->connection->log;

    if (conf->advanced.dynconf_enabled == 1) {
        if (snap_copy == NULL) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: dynconf_enabled is true but "
                          "snap_copy is NULL; skipping dynconf snapshot binding, "
                          "request will use live conf values");
        } else {
            *snapshot_slot = ngx_pcalloc(
                r->pool, sizeof(ngx_http_markdown_dynconf_snapshot_t));
            if (*snapshot_slot != NULL) {
                **snapshot_slot = *snap_copy;
            } else {
                ngx_log_error(NGX_LOG_WARN, log, 0,
                              "markdown: failed to allocate dynconf snapshot "
                              "from request pool; retaining the captured "
                              "header-time effective view");
            }
        }
    }

    if (early_eff == NULL) {
        return;
    }

    /* By-value copy: no allocation, no failure path, bind-once preserved. */
    *eff_storage = *early_eff;
    *effective_slot = eff_storage;
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
 *
 * The full-buffer conversion memory bound is the public
 * `markdown_limits conversion_memory` limit.  `eff->memory_budget` carries
 * that value for dynamic snapshots, and the static fallback reads
 * `conf->limits.conversion_memory` directly.
 */
static size_t
ngx_http_markdown_effective_memory_budget(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (eff != NULL) {
        return eff->memory_budget;
    }
    return conf->limits.conversion_memory;
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
    cur_mtime_nsec = NGX_HTTP_MARKDOWN_STAT_MTIME_NSEC(fi);

    /* Compare all metadata fields.  Any change triggers reload. */
    if (cur_dev != watcher->file_state.file_dev
        || cur_ino != watcher->file_state.file_ino
        || cur_size != watcher->file_state.file_size
        || cur_mtime_sec != watcher->file_state.file_mtime_sec
        || cur_mtime_nsec != watcher->file_state.file_mtime_nsec)
    {
        /* Update stored identity to current values. */
        watcher->file_state.file_dev = cur_dev;
        watcher->file_state.file_ino = cur_ino;
        watcher->file_state.file_size = cur_size;
        watcher->file_state.file_mtime_sec = cur_mtime_sec;
        watcher->file_state.file_mtime_nsec = cur_mtime_nsec;
        watcher->file_state.rejected_source_digest[0] = '\0';

        /* Keep the seconds-resolution mtime mirror synchronized. */
        watcher->file_state.last_mtime = cur_mtime_sec;

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
    watcher->file_state.tick_counter++;

    /* Determine if this is a backstop tick. */
    backstop_tick = (watcher->file_state.tick_counter
                     >= NGX_HTTP_MARKDOWN_DYNCONF_BACKSTOP_TICKS) ? 1 : 0;

    /* Fast path: stat-only metadata check (every tick). */
    metadata_changed = ngx_http_markdown_dynconf_check(watcher, ev->log);

    if (metadata_changed) {
        /*
         * Metadata changed: read file, parse, and reload.
         * Reset backstop counter since we're reading the file now.
         */
        watcher->file_state.tick_counter = 0;

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
        watcher->file_state.tick_counter = 0;

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
    } else if (watcher->file_state.io_retry_remaining_ticks > 0) {
        watcher->file_state.io_retry_remaining_ticks--;
    } else if (watcher->file_state.last_mtime != watcher->file_state.applied_mtime)
    {
        /*
         * No new metadata change and not a backstop tick, but a
         * previous I/O reload failed (last_mtime advanced but
         * applied_mtime did not).  Retry after the bounded backoff.
         */
        ngx_log_error(NGX_LOG_INFO, ev->log, 0,
                      "markdown: retrying failed reload on \"%V\" "
                      "(last_mtime=%T, applied_mtime=%T)",
                      &watcher->path,
                      watcher->file_state.last_mtime, watcher->file_state.applied_mtime);

        if (watcher->conf != NULL) {
            reload_attempted = 1;
            reload_rc = ngx_http_markdown_dynconf_reload(watcher,
                                                          watcher->conf,
                                                          ev->log);
        }
    }

    /*
     * Update applied_mtime after a successful reload or after an invalid
     * candidate has been recorded.
     * RELOAD_APPLIED: new settings committed.
     * RELOAD_NO_CHANGE: file parsed successfully but contained no
     *   effective keys — still a successful parse, so confirm.
     * RELOAD_DRY_RUN_OK: validation succeeded without applying settings;
     *   record applied_mtime so unchanged content is not re-validated on
     *   every timer cycle.  A later mtime change starts a new validation.
     * RELOAD_DRY_RUN_FAIL / INVALID_FILE: validation failed; record
     * applied_mtime after storing the failure so the same invalid content
     * is not retried every timer tick.
     * RELOAD_IO_ERROR: reload failed; applied_mtime stays at its previous
     * value and the next retry is delayed with bounded backoff.
     */
    if (reload_attempted
        && (reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED
            || reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE
            || reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE
            || reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK
            || reload_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL))
    {
        watcher->file_state.applied_mtime = watcher->file_state.last_mtime;
        ngx_http_markdown_dynconf_reset_io_retry(&watcher->file_state);
    } else if (reload_attempted
               && reload_rc
                  == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR)
    {
        ngx_http_markdown_dynconf_schedule_io_retry(&watcher->file_state);
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
 * initial invalid content is recorded and acknowledged so it is not parsed
 * again every timer tick; initial I/O failures leave the mtime pending and
 * retry with bounded backoff.
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
        ngx_memzero(&watcher->file_state, sizeof(watcher->file_state));
    } else {
        watcher->file_state.last_mtime = ngx_file_mtime(&fi);
        watcher->file_state.applied_mtime = watcher->file_state.last_mtime;

        /* Capture full file identity for two-tier fast path. */
        watcher->file_state.file_dev = fi.st_dev;
        watcher->file_state.file_ino = fi.st_ino;
        watcher->file_state.file_size = (off_t) fi.st_size;
        watcher->file_state.file_mtime_sec = ngx_file_mtime(&fi);
        watcher->file_state.file_mtime_nsec =
            NGX_HTTP_MARKDOWN_STAT_MTIME_NSEC(fi);
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
    watcher->diagnostic_state.version = 0;
    watcher->digest_state.lkg_valid = 0;
    watcher->conf = conf;

    /* Initialize two-tier polling state. */
    watcher->file_state.tick_counter = 0;
    ngx_http_markdown_dynconf_reset_io_retry(&watcher->file_state);
    watcher->file_state.rejected_source_digest[0] = '\0';
    watcher->digest_state.generation = 0;  /* Will become 1 on first successful reload. */
    watcher->digest_state.source_digest[0] = '\0';
    watcher->digest_state.active_digest[0] = '\0';
    watcher->digest_state.lkg_digest[0] = '\0';
    watcher->diagnostic_state.last_result = NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE;
    watcher->diagnostic_state.last_success = 0;
    watcher->diagnostic_state.last_error_len = 0;
    watcher->diagnostic_state.last_error[0] = '\0';
    watcher->diagnostic_state.last_rejected_source_digest[0] = '\0';
    watcher->diagnostic_state.last_rejected_error_len = 0;
    watcher->diagnostic_state.last_rejected_error[0] = '\0';

    /* Initialize the immutable baseline and the active snapshot. */
    ngx_http_markdown_dynconf_snapshot_from_conf(&watcher->static_snapshot,
                                                  conf);
    watcher->static_snapshot.validation_summary = watcher->validation_summary;
    watcher->active_snapshot = watcher->static_snapshot;

    /*
     * Apply the dynconf file immediately at startup so that runtime
     * overrides persist across NGINX restart/reload.  If the file
     * exists and parses successfully, the active snapshot and live
     * conf are updated before any request arrives.  If the initial
     * parse rejects the file, the watcher still starts with the static
     * baseline and acknowledges that content.  I/O failures retain a
     * pending mtime and retry with bounded backoff.
     */
    if (watcher->file_state.last_mtime != 0) {
        initial_rc = ngx_http_markdown_dynconf_reload(watcher, conf, log);
        if (initial_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED
            || initial_rc == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE)
        {
            watcher->file_state.applied_mtime = watcher->file_state.last_mtime;
            ngx_log_error(NGX_LOG_INFO, log, 0,
                          "markdown: applied existing file on startup "
                          "(rc=%i, version=%ui)",
                          initial_rc, watcher->diagnostic_state.version);
        } else if (initial_rc
                   == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK)
        {
            watcher->file_state.applied_mtime = watcher->file_state.last_mtime;
            ngx_log_error(NGX_LOG_INFO, log, 0,
                          "markdown: dry-run validation passed "
                          "on startup (rc=%i, not applied)",
                          initial_rc);
        } else if (initial_rc
                   == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL)
        {
            watcher->file_state.applied_mtime = watcher->file_state.last_mtime;
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: dry-run validation failed "
                          "on startup (rc=%i, %ui errors found)",
                          initial_rc,
                          watcher->diagnostic_state.last_validation.total_errors);
        } else if (initial_rc
                   == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE)
        {
            watcher->file_state.applied_mtime = watcher->file_state.last_mtime;
            ngx_http_markdown_dynconf_reset_io_retry(&watcher->file_state);
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: initial reload of \"%V\" rejected "
                          "(rc=%i); unchanged content will not be retried "
                          "on every timer tick",
                          &watcher->path, initial_rc);
        } else {
            watcher->file_state.applied_mtime = 0;
            ngx_http_markdown_dynconf_schedule_io_retry(
                &watcher->file_state);
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: initial reload of \"%V\" failed "
                          "(rc=%i); starting from static conf, will retry "
                          "with bounded backoff",
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


static ngx_int_t
ngx_http_markdown_dynconf_apply_streaming_buffer(
    const ngx_http_markdown_dynconf_snapshot_t *snapshot,
    ngx_http_markdown_dynconf_snapshot_t *candidate,
    const FFIDynconfResult *result)
{
#ifdef MARKDOWN_STREAMING_ENABLED
    ngx_flag_t  has_location_index;

    if (result->streaming_buffer == DYNCONF_NOT_SET_U64) {
        return NGX_OK;
    }

    has_location_index = snapshot->validation_summary != NULL
        && snapshot->validation_summary->min_applicable_set;

    /* The bounded index owns per-location applicability and limits. */
    if (result->streaming_buffer
            < NGX_HTTP_MARKDOWN_DYNCONF_STREAMING_BUFFER_MIN
        || result->streaming_buffer
            > NGX_HTTP_MARKDOWN_DYNCONF_STREAMING_BUFFER_MAX
        || result->streaming_buffer > NGX_MAX_SIZE_T_VALUE
        || (!has_location_index && snapshot->conversion_memory != 0
            && snapshot->conversion_memory
                != NGX_HTTP_MARKDOWN_CONF_UNSET_SIZE
            && result->streaming_buffer > snapshot->conversion_memory))
    {
        return NGX_ERROR;
    }

    candidate->streaming_budget =
        (size_t) result->streaming_buffer; /* CWE-190:guarded */
#else
    (void) snapshot;
    (void) candidate;

    if (result->streaming_buffer != DYNCONF_NOT_SET_U64) {
        return NGX_ERROR;
    }
#endif

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_dynconf_apply_filter(
    ngx_http_markdown_dynconf_snapshot_t *candidate,
    const FFIDynconfResult *result)
{
    if (result->filter == DYNCONF_NOT_SET_U8) {
        return NGX_OK;
    }

    if (result->filter != DYNCONF_FILTER_ON
        && result->filter != DYNCONF_FILTER_OFF)
    {
        return NGX_ERROR;
    }

    candidate->enabled = result->filter == DYNCONF_FILTER_ON;
    candidate->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    candidate->enabled_complex = NULL;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_dynconf_apply_prune_noise(
    ngx_http_markdown_dynconf_snapshot_t *candidate,
    const FFIDynconfResult *result)
{
    if (result->prune_noise == DYNCONF_NOT_SET_U8) {
        return NGX_OK;
    }

    if (result->prune_noise != DYNCONF_PRUNE_NOISE_ON
        && result->prune_noise != DYNCONF_PRUNE_NOISE_OFF)
    {
        return NGX_ERROR;
    }

    candidate->prune_noise = result->prune_noise == DYNCONF_PRUNE_NOISE_ON;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_dynconf_apply_log_verbosity(
    ngx_http_markdown_dynconf_snapshot_t *candidate,
    const FFIDynconfResult *result)
{
    if (result->log_verbosity == DYNCONF_NOT_SET_U8) {
        return NGX_OK;
    }

    if (result->log_verbosity > DYNCONF_LOG_DEBUG) {
        return NGX_ERROR;
    }

    candidate->log_verbosity = result->log_verbosity;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_dynconf_apply_error_policy(
    ngx_http_markdown_dynconf_snapshot_t *candidate,
    const FFIDynconfResult *result)
{
    if (result->error_policy == DYNCONF_NOT_SET_U8) {
        return NGX_OK;
    }

    switch (result->error_policy) {
    case DYNCONF_POLICY_PASS:
        candidate->error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
        candidate->error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;
        break;
    case DYNCONF_POLICY_FAIL_CLOSED:
        candidate->error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
        candidate->error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;
        break;
    case DYNCONF_POLICY_STATUS_429:
        candidate->error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
        candidate->error_status = NGX_HTTP_TOO_MANY_REQUESTS;
        break;
    case DYNCONF_POLICY_STATUS_503:
        candidate->error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
        candidate->error_status = NGX_HTTP_SERVICE_UNAVAILABLE;
        break;
    default:
        return NGX_ERROR;
    }

    return NGX_OK;
}


/*
 * Record the failure code (when requested) and return NGX_ERROR.
 * Keeps the apply chain terse so the caller's Cognitive Complexity
 * stays within the allowed threshold.
 */
static ngx_int_t
ngx_http_markdown_dynconf_apply_reject(ngx_uint_t *failure_code,
    ngx_uint_t code)
{
    if (failure_code != NULL) {
        *failure_code = code;
    }
    return NGX_ERROR;
}


/* Project a validated JSON/FFI result into the staged snapshot.  The bounded
 * Rust parser rejects line-oriented input before this function is reached. */
static ngx_int_t
ngx_http_markdown_dynconf_apply_ffi_result_with_log(
    ngx_http_markdown_dynconf_snapshot_t *snapshot,
    const FFIDynconfResult *result, ngx_log_t *log,
    ngx_uint_t *failure_code)
{
    ngx_http_markdown_dynconf_snapshot_t candidate;

    if (failure_code != NULL) {
        *failure_code = DYNCONF_ERR_INTERNAL;
    }

    if (snapshot == NULL || result == NULL) {
        return NGX_ERROR;
    }

    if (result->error_code != DYNCONF_OK) {
        return ngx_http_markdown_dynconf_apply_reject(
            failure_code, result->error_code);
    }

    if (failure_code != NULL) {
        *failure_code = DYNCONF_ERR_INVALID_TYPE;
    }

    candidate = *snapshot;

    /* Validate before applying any field so a rejected result is atomic. */
    if (ngx_http_markdown_dynconf_apply_streaming_buffer(
            snapshot, &candidate, result) != NGX_OK)
    {
        return ngx_http_markdown_dynconf_apply_reject(
            failure_code, DYNCONF_ERR_VALUE_OUT_OF_RANGE);
    }
    if (ngx_http_markdown_dynconf_apply_filter(&candidate, result) != NGX_OK)
    {
        return ngx_http_markdown_dynconf_apply_reject(
            failure_code, DYNCONF_ERR_INVALID_TYPE);
    }
    if (ngx_http_markdown_dynconf_apply_prune_noise(&candidate, result)
        != NGX_OK)
    {
        return ngx_http_markdown_dynconf_apply_reject(
            failure_code, DYNCONF_ERR_INVALID_TYPE);
    }
    if (ngx_http_markdown_dynconf_apply_log_verbosity(&candidate, result)
        != NGX_OK)
    {
        return ngx_http_markdown_dynconf_apply_reject(
            failure_code, DYNCONF_ERR_INVALID_TYPE);
    }
    if (ngx_http_markdown_dynconf_apply_error_policy(&candidate, result)
        != NGX_OK)
    {
        return ngx_http_markdown_dynconf_apply_reject(
            failure_code, DYNCONF_ERR_INVALID_TYPE);
    }

#ifdef MARKDOWN_STREAMING_ENABLED
    if (result->streaming_buffer != DYNCONF_NOT_SET_U64
        && candidate.validation_summary != NULL
        && ngx_http_markdown_validate_snapshot_against_summary(
               candidate.validation_summary,
               candidate.streaming_budget, log) != NGX_OK)
    {
        return ngx_http_markdown_dynconf_apply_reject(
            failure_code, DYNCONF_ERR_VALUE_OUT_OF_RANGE);
    }
#endif

    candidate.valid = 1;
    *snapshot = candidate;
    return NGX_OK;
}

/* Declared here because the helper is defined after the reload path. */
static void ngx_http_markdown_dynconf_assert_snapshot_layout(void);

static void
ngx_http_markdown_dynconf_record_static_error(
    ngx_http_markdown_dynconf_watcher_t *watcher, ngx_uint_t error_code)
{
    static const u_char file_error[] =
        "dynamic configuration file could not be read";
    static const u_char too_large[] =
        "dynamic configuration file exceeds the size limit";
    static const u_char invalid_json[] =
        "dynamic configuration document is invalid JSON";
    static const u_char token_budget[] =
        "dynamic configuration document exceeds the parser token budget";
    static const u_char nesting_depth[] =
        "dynamic configuration document exceeds the parser nesting limit";
    static const u_char duplicate_key[] =
        "dynamic configuration contains a duplicate key";
    static const u_char missing_schema[] =
        "dynamic configuration is missing schema_version";
    static const u_char invalid_schema[] =
        "dynamic configuration has an invalid schema_version";
    static const u_char unknown_key[] =
        "dynamic configuration contains an unknown key";
    static const u_char invalid_type[] =
        "dynamic configuration contains a value of invalid type";
    static const u_char out_of_range[] =
        "dynamic configuration contains a value out of range";
    static const u_char invalid_utf8[] =
        "dynamic configuration is not valid UTF-8";
    static const u_char internal[] =
        "dynamic configuration parser failed internally";
    const u_char  *message;
    size_t         length;

    if (watcher == NULL) {
        return;
    }

    switch (error_code) {
    case NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO:
        message = file_error;
        break;
    case DYNCONF_ERR_TOO_LARGE:
        message = too_large;
        break;
    case DYNCONF_ERR_INVALID_JSON:
        message = invalid_json;
        break;
    case DYNCONF_ERR_TOKEN_BUDGET:
        message = token_budget;
        break;
    case DYNCONF_ERR_NESTING_DEPTH:
        message = nesting_depth;
        break;
    case DYNCONF_ERR_DUPLICATE_KEY:
        message = duplicate_key;
        break;
    case DYNCONF_ERR_MISSING_SCHEMA_VERSION:
        message = missing_schema;
        break;
    case DYNCONF_ERR_INVALID_SCHEMA_VERSION:
        message = invalid_schema;
        break;
    case DYNCONF_ERR_UNKNOWN_KEY:
        message = unknown_key;
        break;
    case DYNCONF_ERR_INVALID_TYPE:
        message = invalid_type;
        break;
    case DYNCONF_ERR_VALUE_OUT_OF_RANGE:
        message = out_of_range;
        break;
    case DYNCONF_ERR_INVALID_UTF8:
        message = invalid_utf8;
        break;
    default:
        message = internal;
        break;
    }

    length = ngx_strlen(message);
    if (length > sizeof(watcher->diagnostic_state.last_error) - 1) {
        length = sizeof(watcher->diagnostic_state.last_error) - 1;
    }
    ngx_memcpy(watcher->diagnostic_state.last_error, message, length);
    watcher->diagnostic_state.last_error[length] = '\0';
    watcher->diagnostic_state.last_error_len = length;
}

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
        ngx_http_markdown_record_dynconf_reload(
            NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
        ngx_http_markdown_dynconf_record_static_error(
            watcher, NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    ngx_memcpy(path_buf, watcher->path.data, watcher->path.len);
    path_buf[watcher->path.len] = '\0';

    /*
     * O_NONBLOCK keeps open() non-latching for FIFOs and other special
     * files: a blocking O_RDONLY on an empty FIFO would park this worker
     * timer inside open() until a writer appears, freezing the event
     * loop.  The descriptor-level type check below rejects anything that
     * is not a regular file once the fd is already open.
     *
     * Symlinked paths stay permitted (deployment layouts commonly link
     * the watch file); swap races are covered because every subsequent
     * validation runs against the opened descriptor rather than the
     * pathname.
     */
    fd = ngx_open_file(path_buf,
                       NGX_FILE_RDONLY | NGX_FILE_NONBLOCK,
                       NGX_FILE_OPEN, 0);
    if (fd == NGX_INVALID_FILE) {
        ngx_http_markdown_record_dynconf_reload(
            NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
        ngx_http_markdown_dynconf_record_static_error(
            watcher, NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    /* Inspect the opened descriptor so the size check cannot race a path swap. */
    if (ngx_fd_info(fd, &file_info) == NGX_FILE_ERROR) {
        ngx_close_file(fd);
        ngx_http_markdown_record_dynconf_reload(
            NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
        ngx_http_markdown_dynconf_record_static_error(
            watcher, NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }
    /* Descriptor-level type gate: only regular files are valid dynconf
     * sources.  With the non-blocking open above, a swapped-in FIFO or
     * device node reaches here immediately and gets rejected instead of
     * latching the worker. */
    if (!S_ISREG(file_info.st_mode)) {
        ngx_close_file(fd);
        ngx_http_markdown_record_dynconf_reload(NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
        ngx_http_markdown_dynconf_record_static_error(
            watcher, NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }
    if (file_info.st_size < 0
        || (uint64_t) file_info.st_size > NGX_HTTP_MARKDOWN_DYNCONF_MAX_FILE_SIZE
        || (uint64_t) file_info.st_size > NGX_MAX_SIZE_T_VALUE)
    {
        ngx_close_file(fd);
        ngx_http_markdown_record_dynconf_reload(DYNCONF_ERR_TOO_LARGE);
        ngx_http_markdown_dynconf_record_static_error(
            watcher, DYNCONF_ERR_TOO_LARGE);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    *file_size = (size_t) file_info.st_size; /* CWE-190:guarded */
    *data = ngx_alloc(*file_size == 0 ? 1 : *file_size, log);
    if (*data == NULL) {
        ngx_close_file(fd);
        ngx_http_markdown_record_dynconf_reload(
            NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
        ngx_http_markdown_dynconf_record_static_error(
            watcher, NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
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
            ngx_http_markdown_record_dynconf_reload(
                NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
            ngx_http_markdown_dynconf_record_static_error(
                watcher, NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO);
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


static ngx_int_t
ngx_http_markdown_dynconf_compute_source_digest(
    const u_char *data, size_t data_len, u_char *digest)
{
    u_char raw_digest[64];

    if (digest == NULL || (data == NULL && data_len != 0)) {
        return NGX_ERROR;
    }

    if (markdown_sha256_hex(data, data_len, raw_digest, sizeof(raw_digest))
        != DYNCONF_OK)
    {
        return NGX_ERROR;
    }

    return ngx_http_markdown_dynconf_copy_digest(
        digest, NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN,
        raw_digest, sizeof(raw_digest));
}


static void
ngx_http_markdown_dynconf_record_rejected_source_digest(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    const FFIDynconfResult *result)
{
    if (watcher == NULL) {
        return;
    }

    watcher->diagnostic_state.last_rejected_source_digest[0] = '\0';
    watcher->file_state.rejected_source_digest[0] = '\0';
    if (result == NULL || result->source_digest == NULL
        || result->source_digest_len == 0)
    {
        return;
    }

    if (ngx_http_markdown_dynconf_copy_digest(
        watcher->diagnostic_state.last_rejected_source_digest,
        sizeof(watcher->diagnostic_state.last_rejected_source_digest),
        result->source_digest, result->source_digest_len) == NGX_OK)
    {
        ngx_memcpy(watcher->file_state.rejected_source_digest,
                   watcher->diagnostic_state.last_rejected_source_digest,
                   sizeof(watcher->file_state.rejected_source_digest));
    }
}


static void
ngx_http_markdown_dynconf_record_rejected_error(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    const u_char *message, size_t length)
{
    if (watcher == NULL) {
        return;
    }

    if (length > sizeof(watcher->diagnostic_state.last_rejected_error) - 1) {
        length = sizeof(watcher->diagnostic_state.last_rejected_error) - 1;
    }
    if (message != NULL && length > 0) {
        ngx_memcpy(watcher->diagnostic_state.last_rejected_error,
                   message, length);
    }
    watcher->diagnostic_state.last_rejected_error[length] = '\0';
    watcher->diagnostic_state.last_rejected_error_len = length;
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

    watcher->diagnostic_state.last_error_len = 0;
    watcher->diagnostic_state.last_error[0] = '\0';
    if (result->error_message == NULL || result->error_message_len == 0) {
        ngx_http_markdown_dynconf_record_static_error(
            watcher, result->error_code);
        ngx_http_markdown_dynconf_record_rejected_error(
            watcher, watcher->diagnostic_state.last_error,
            watcher->diagnostic_state.last_error_len);
        return;
    }

    length = result->error_message_len;
    if (length > sizeof(watcher->diagnostic_state.last_error) - 1) {
        length = sizeof(watcher->diagnostic_state.last_error) - 1;
    }
    ngx_memcpy(watcher->diagnostic_state.last_error, result->error_message, length);
    watcher->diagnostic_state.last_error[length] = '\0';
    watcher->diagnostic_state.last_error_len = length;
    ngx_http_markdown_dynconf_record_rejected_error(
        watcher, watcher->diagnostic_state.last_error,
        watcher->diagnostic_state.last_error_len);
}

static void
ngx_http_markdown_dynconf_record_candidate_error(
    ngx_http_markdown_dynconf_watcher_t *watcher, ngx_uint_t error_code)
{
    if (watcher == NULL) {
        return;
    }

    ngx_http_markdown_dynconf_record_static_error(watcher, error_code);
    ngx_http_markdown_dynconf_record_rejected_error(
        watcher, watcher->diagnostic_state.last_error,
        watcher->diagnostic_state.last_error_len);
}


static ngx_uint_t
ngx_http_markdown_dynconf_blocked_fields(
    const ngx_http_markdown_dynconf_watcher_t *watcher)
{
    ngx_uint_t mask;

    if (watcher == NULL) {
        return 0;
    }

    mask = watcher->conf != NULL
        ? watcher->conf->advanced.dynconf_block_mask : 0;
    if (watcher->validation_summary != NULL) {
        mask |= watcher->validation_summary->block_mask_union;
    }

    return mask;
}


static ngx_uint_t
ngx_http_markdown_dynconf_masked_fields(
    const ngx_http_markdown_dynconf_watcher_t *watcher,
    const FFIDynconfResult *result)
{
    ngx_uint_t mask;

    if (watcher == NULL || result == NULL) {
        return 0;
    }

    mask = ngx_http_markdown_dynconf_blocked_fields(watcher);
    if (result->filter == DYNCONF_NOT_SET_U8) {
        mask &= ~NGX_HTTP_MARKDOWN_BLOCK_FILTER;
    }
    if (result->prune_noise == DYNCONF_NOT_SET_U8) {
        mask &= ~NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE;
    }
    if (result->log_verbosity == DYNCONF_NOT_SET_U8) {
        mask &= ~NGX_HTTP_MARKDOWN_BLOCK_LOG_VERBOSITY;
    }
    if (result->error_policy == DYNCONF_NOT_SET_U8) {
        mask &= ~NGX_HTTP_MARKDOWN_BLOCK_ERROR_POLICY;
    }
    if (result->streaming_buffer == DYNCONF_NOT_SET_U64) {
        mask &= ~NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER;
    }

    return mask;
}


static void
ngx_http_markdown_dynconf_log_masked_fields(ngx_log_t *log, ngx_uint_t mask)
{
    static const struct {
        ngx_uint_t bit;
        const char *name;
    } fields[] = {
        { NGX_HTTP_MARKDOWN_BLOCK_FILTER, "filter" },
        { NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE, "prune_noise" },
        { NGX_HTTP_MARKDOWN_BLOCK_LOG_VERBOSITY, "log_verbosity" },
        { NGX_HTTP_MARKDOWN_BLOCK_ERROR_POLICY, "error_policy" },
        { NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER, "streaming_buffer" }
    };

    if (log == NULL) {
        return;
    }

    for (ngx_uint_t i = 0; i < sizeof(fields) / sizeof(fields[0]); i++) {
        if ((mask & fields[i].bit) != 0) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: dynconf key \"%s\" is masked by "
                          "explicit static configuration; static "
                          "value remains effective",
                          fields[i].name);
        }
    }
}


static void
ngx_http_markdown_dynconf_parse_candidate(
    u_char *data, size_t file_size, FFIDynconfResult *result)
{
    markdown_dynconf_parse(data, file_size, result);
    ngx_free(data);
}


static ngx_int_t
ngx_http_markdown_dynconf_candidate_error(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    const ngx_http_markdown_conf_t *conf, const FFIDynconfResult *result)
{
    ngx_int_t rc;

    ngx_http_markdown_record_dynconf_reload(result->error_code);
    ngx_http_markdown_dynconf_record_rejected_source_digest(watcher, result);
    ngx_http_markdown_dynconf_record_error(watcher, result);
    rc = conf->advanced.dynconf_dry_run == 1
        ? NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL
        : NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    watcher->diagnostic_state.last_result = rc;
    return rc;
}


static ngx_int_t
ngx_http_markdown_dynconf_stage_candidate(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    const ngx_http_markdown_conf_t *conf, ngx_log_t *log,
    const FFIDynconfResult *result, u_char *source_digest,
    u_char *active_digest)
{
    ngx_uint_t failure_code;
    ngx_uint_t masked_fields;

    /* Omitted fields must resolve against the static/http baseline. */
    ngx_http_markdown_dynconf_snapshot_reset_baseline(watcher, conf);
    failure_code = DYNCONF_ERR_INVALID_TYPE;
    if (ngx_http_markdown_dynconf_apply_ffi_result_with_log(
            &watcher->staging_snapshot, result, log, &failure_code)
        != NGX_OK)
    {
        ngx_http_markdown_record_dynconf_reload(failure_code);
        ngx_http_markdown_dynconf_record_candidate_error(
            watcher, failure_code);
        ngx_http_markdown_dynconf_record_rejected_source_digest(
            watcher, result);
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown: dynamic configuration candidate rejected "
            "(error_code=%ui)", failure_code);
        watcher->diagnostic_state.last_result =
            conf->advanced.dynconf_dry_run == 1
            ? NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL
            : NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        return NGX_ERROR;
    }

    masked_fields = ngx_http_markdown_dynconf_masked_fields(watcher, result);
    watcher->diagnostic_state.last_masked_fields = masked_fields;

    if (ngx_http_markdown_dynconf_copy_digest(
            source_digest, NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN,
            result->source_digest, result->source_digest_len) != NGX_OK
        || ngx_http_markdown_dynconf_copy_digest(
            active_digest, NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN,
            result->active_digest, result->active_digest_len) != NGX_OK)
    {
        ngx_http_markdown_record_dynconf_reload(DYNCONF_ERR_INTERNAL);
        ngx_http_markdown_dynconf_record_candidate_error(
            watcher, DYNCONF_ERR_INTERNAL);
        ngx_http_markdown_dynconf_record_rejected_source_digest(
            watcher, result);
        watcher->diagnostic_state.last_result =
            NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        return NGX_ERROR;
    }

    return NGX_OK;
}


static ngx_flag_t
ngx_http_markdown_dynconf_source_unchanged(
    const ngx_http_markdown_dynconf_watcher_t *watcher,
    const u_char *source_digest)
{
    return watcher->digest_state.generation > 0
        && ngx_memcmp(watcher->digest_state.source_digest, source_digest,
                      sizeof(watcher->digest_state.source_digest)) == 0;
}


static void
ngx_http_markdown_dynconf_clear_last_error(
    ngx_http_markdown_dynconf_watcher_t *watcher)
{
    watcher->diagnostic_state.last_error_len = 0;
    watcher->diagnostic_state.last_error[0] = '\0';
}


static void
ngx_http_markdown_dynconf_publish_candidate(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_http_markdown_conf_t *conf, const u_char *source_digest,
    const u_char *active_digest)
{
    if (watcher->digest_state.generation > 0) {
        watcher->last_known_good = watcher->active_snapshot;
        watcher->digest_state.lkg_valid = 1;
        ngx_memcpy(watcher->digest_state.lkg_digest,
                   watcher->digest_state.active_digest,
                   sizeof(watcher->digest_state.lkg_digest));
        watcher->digest_state.lkg_mtime = watcher->file_state.applied_mtime;
    } else if (!watcher->digest_state.lkg_valid
               && watcher->active_snapshot.valid)
    {
        /* Preserve the static configuration as the bootstrap LKG. */
        watcher->last_known_good = watcher->active_snapshot;
        watcher->digest_state.lkg_valid = 1;
    }
    ngx_memcpy(watcher->digest_state.source_digest, source_digest,
               sizeof(watcher->digest_state.source_digest));
    ngx_memcpy(watcher->digest_state.active_digest, active_digest,
               sizeof(watcher->digest_state.active_digest));
    /*
     * Compile-time guard: if a field is added to the snapshot struct,
     * sizeof changes and this assertion fires, forcing a review of the
     * shallow-copy safety below (active_snapshot = staging_snapshot).
     */
    ngx_http_markdown_dynconf_assert_snapshot_layout();
    watcher->active_snapshot = watcher->staging_snapshot;
    ngx_http_markdown_dynconf_apply_snapshot(conf, &watcher->active_snapshot);
    watcher->digest_state.generation++;
    watcher->diagnostic_state.version++;
    watcher->file_state.applied_mtime = watcher->file_state.last_mtime;
    watcher->file_state.rejected_source_digest[0] = '\0';
    watcher->diagnostic_state.last_success = ngx_time();
    watcher->diagnostic_state.last_result =
        NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED;
    ngx_http_markdown_record_dynconf_reload(DYNCONF_OK);
    ngx_http_markdown_dynconf_clear_last_error(watcher);
}

/* Reload the bounded file, validate its JSON result, and publish it atomically.
 * The staged snapshot and digest copies are request-independent, so all
 * validation completes before active state or last-known-good state changes.
 */
static ngx_int_t
ngx_http_markdown_dynconf_reload(
    ngx_http_markdown_dynconf_watcher_t *watcher,
    ngx_http_markdown_conf_t *conf,
    ngx_log_t *log)
{
    u_char             *data;
    size_t              file_size;
    ngx_int_t            rc;
    ngx_int_t            digest_rc;
    u_char               current_source_digest[
        NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    u_char               next_source_digest[
        NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    u_char               next_active_digest[
        NGX_HTTP_MARKDOWN_DYNCONF_DIGEST_LEN];
    FFIDynconfResult    result;

    if (watcher == NULL || conf == NULL || log == NULL)
    {
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR;
    }

    /* Read and parse into a disposable FFI result before touching snapshots. */
    rc = ngx_http_markdown_dynconf_read_file(watcher, log, &data, &file_size);
    if (rc != NGX_OK) {
        watcher->diagnostic_state.last_result = rc;
        return rc;
    }

    digest_rc = ngx_http_markdown_dynconf_compute_source_digest(
        data, file_size, current_source_digest);
    if (digest_rc == NGX_OK
        && watcher->file_state.rejected_source_digest[0] != '\0'
        && ngx_memcmp(current_source_digest,
                      watcher->file_state.rejected_source_digest,
                      sizeof(current_source_digest)) == 0)
    {
        ngx_free(data);
        watcher->diagnostic_state.last_result =
            NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE;
    }

    markdown_dynconf_result_init(&result);
    ngx_http_markdown_dynconf_parse_candidate(data, file_size, &result);

    if (result.error_code != DYNCONF_OK) {
        rc = ngx_http_markdown_dynconf_candidate_error(
            watcher, conf, &result);
        markdown_dynconf_result_free(&result);
        return rc;
    }

    if (ngx_http_markdown_dynconf_stage_candidate(
            watcher, conf, log, &result,
            next_source_digest, next_active_digest) != NGX_OK)
    {
        markdown_dynconf_result_free(&result);
        return watcher->diagnostic_state.last_result;
    }

    if (conf->advanced.dynconf_dry_run == 1) {
        ngx_http_markdown_record_dynconf_reload(DYNCONF_OK);
        watcher->diagnostic_state.last_result =
            NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK;
        watcher->file_state.rejected_source_digest[0] = '\0';
        ngx_http_markdown_dynconf_clear_last_error(watcher);
        markdown_dynconf_result_free(&result);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_OK;
    }

    if (ngx_http_markdown_dynconf_source_unchanged(
            watcher, next_source_digest))
    {
        watcher->diagnostic_state.last_result =
            NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE;
        ngx_http_markdown_dynconf_clear_last_error(watcher);
        markdown_dynconf_result_free(&result);
        return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_NO_CHANGE;
    }

    ngx_http_markdown_dynconf_log_masked_fields(
        log, watcher->diagnostic_state.last_masked_fields);
    ngx_http_markdown_dynconf_publish_candidate(
        watcher, conf, next_source_digest, next_active_digest);
    markdown_dynconf_result_free(&result);
    return NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_APPLIED;
}

static void
ngx_http_markdown_dynconf_assert_snapshot_layout(void)
{
#ifdef MARKDOWN_STREAMING_ENABLED
    _Static_assert(
        sizeof(ngx_http_markdown_dynconf_snapshot_t)
            == 11 * sizeof(void *),
        "dynconf_snapshot_t layout changed, review shallow copy");
#if (NGX_PTR_SIZE == 8)
    /* Literal-byte offset assertions are LP64-specific; on 32-bit targets
     * the pointer-derived offsets differ and the pointer-scaled size
     * assertion above remains the portable check. */
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            enabled) == 0,
                   "dynconf snapshot enabled offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            enabled_source) == 8,
                   "dynconf snapshot enabled_source offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            enabled_complex) == 16,
                   "dynconf snapshot enabled_complex offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            prune_noise) == 24,
                   "dynconf snapshot prune_noise offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            log_verbosity) == 32,
                   "dynconf snapshot log_verbosity offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            error_policy) == 40,
                   "dynconf snapshot error_policy offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            error_status) == 48,
                   "dynconf snapshot error_status offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            streaming_budget) == 56,
                   "dynconf snapshot streaming_budget offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            conversion_memory) == 64,
                   "dynconf snapshot conversion_memory offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            validation_summary) == 72,
                   "dynconf snapshot validation_summary offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            valid) == 80,
                   "dynconf snapshot valid offset changed");
#endif /* NGX_PTR_SIZE == 8 (LP64 literal offsets) */
#else
    _Static_assert(
        sizeof(ngx_http_markdown_dynconf_snapshot_t)
            == 10 * sizeof(void *),
        "dynconf_snapshot_t layout changed, review shallow copy");
#if (NGX_PTR_SIZE == 8)
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            enabled) == 0,
                   "dynconf snapshot enabled offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            enabled_source) == 8,
                   "dynconf snapshot enabled_source offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            enabled_complex) == 16,
                   "dynconf snapshot enabled_complex offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            prune_noise) == 24,
                   "dynconf snapshot prune_noise offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            log_verbosity) == 32,
                   "dynconf snapshot log_verbosity offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            error_policy) == 40,
                   "dynconf snapshot error_policy offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            error_status) == 48,
                   "dynconf snapshot error_status offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            conversion_memory) == 56,
                   "dynconf snapshot conversion_memory offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            validation_summary) == 64,
                   "dynconf snapshot validation_summary offset changed");
    _Static_assert(offsetof(ngx_http_markdown_dynconf_snapshot_t,
                            valid) == 72,
                   "dynconf snapshot valid offset changed");
#endif /* NGX_PTR_SIZE == 8 (LP64 literal offsets) */
#endif

}




#endif /* NGX_HTTP_MARKDOWN_DYNCONF_IMPL_H */
