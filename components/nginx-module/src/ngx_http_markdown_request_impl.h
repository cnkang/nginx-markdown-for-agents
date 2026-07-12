#ifndef NGX_HTTP_MARKDOWN_REQUEST_IMPL_H
#define NGX_HTTP_MARKDOWN_REQUEST_IMPL_H

/*
 * Request-path orchestration helpers.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * Kept in a dedicated implementation include so the main module file can
 * stay focused on module wiring while header/body filter state transitions
 * evolve separately from payload buffering, decompression, and output shaping.
 */

#include "ngx_http_markdown_payload_impl.h"
#include "ngx_http_markdown_conversion_impl.h"
#include "ngx_http_markdown_exports.h"
#include "ngx_http_markdown_diagnostics.h"

/*
 * Forward declarations for streaming functions defined in
 * ngx_http_markdown_streaming_impl.h (included after this header).
 * Required so call sites in this header see proper prototypes.
 * Also declared here (instead of the main .c file) so the .c file can
 * keep all #include directives contiguous at the top (SonarCloud c:S954).
 */
#ifdef MARKDOWN_STREAMING_ENABLED
static ngx_http_markdown_path_selection_t
ngx_http_markdown_select_processing_path(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff);
static ngx_int_t
ngx_http_markdown_streaming_body_filter(
    ngx_http_request_t *r, ngx_chain_t *in);
static void ngx_http_markdown_streaming_sync_buffered(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx);
static void ngx_http_markdown_streaming_abandon_input(ngx_chain_t *in);
static ngx_int_t ngx_http_markdown_streaming_pending_input_enqueue_remainder(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, ngx_chain_t *cl);
static ngx_int_t ngx_http_markdown_streaming_handle_postcommit_error(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, uint32_t error_code);
static ngx_int_t ngx_http_markdown_streaming_precommit_error(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, uint32_t error_code);
static ngx_int_t ngx_http_markdown_streaming_failopen_passthrough(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx, ngx_chain_t *in);

/*
 * Handle new non-NULL input arriving while streaming pending_output
 * is non-NULL (downstream backpressure active).
 *
 * TERMINAL disposition: abandon the input and return NGX_AGAIN.
 * Otherwise: enqueue the remainder; on budget exhaustion, route through
 * post-commit or pre-commit error handling (which may fail-open).
 *
 * Returns NGX_AGAIN when the input was enqueued/abandoned (caller should
 * return NGX_AGAIN), or a final error/fail-open rc the caller propagates.
 *
 * Shared by ngx_http_markdown_body_filter (request_impl.h) and
 * ngx_http_markdown_streaming_body_filter (streaming_impl.h) so both
 * entry points stay below SonarCloud c:S3776/c:S134 thresholds.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_new_input_with_pending(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, ngx_chain_t *in)
{
    ngx_int_t  rc;

    if (ctx->streaming.input_disposition
        == NGX_HTTP_MD_INPUT_TERMINAL)
    {
        ngx_http_markdown_streaming_abandon_input(in);
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_AGAIN;
    }

    rc = ngx_http_markdown_streaming_pending_input_enqueue_remainder(
        r, ctx, conf, in);
    if (rc == NGX_OK) {
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_AGAIN;
    }

    if (ctx->streaming.commit_state
        == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
    {
        rc = ngx_http_markdown_streaming_handle_postcommit_error(
            r, ctx, conf, ERROR_BUDGET_EXCEEDED);
        ngx_http_markdown_streaming_abandon_input(in);
        return rc;
    }

    rc = ngx_http_markdown_streaming_precommit_error(
        r, ctx, conf, ERROR_BUDGET_EXCEEDED);
    if (rc == NGX_DECLINED && !ctx->eligible) {
        return ngx_http_markdown_streaming_failopen_passthrough(
            r, ctx, in);
    }
    return rc;
}
#endif

/* Forward declarations for helpers defined in this file */
static void ngx_http_markdown_bind_request_snapshot(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_dynconf_snapshot_t *snap_copy,
    const ngx_http_markdown_effective_conf_t *early_eff,
    const ngx_http_markdown_conf_t *conf);
static ngx_int_t ngx_http_markdown_handle_ctx_alloc_failure(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff);
static void ngx_http_markdown_init_ctx(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx, ngx_flag_t filter_enabled);
static void ngx_http_markdown_log_failure_decision(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);
static ngx_int_t ngx_http_markdown_handle_unsupported_compression(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);
static void ngx_http_markdown_log_decision_with_category(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code, const ngx_str_t *error_category);
static void ngx_http_markdown_log_decision(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code);
static void ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_conf_t *conf);
static ngx_http_output_header_filter_pt ngx_http_next_header_filter;
static ngx_http_output_body_filter_pt ngx_http_next_body_filter;
const ngx_str_t *ngx_http_markdown_reason_failed_closed(void);
const ngx_str_t *ngx_http_markdown_reason_failed_open(void);
const ngx_str_t *ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, ngx_log_t *log);
const ngx_str_t *ngx_http_markdown_reason_converted(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_skip_compressed(void);
const ngx_str_t *ngx_http_markdown_reason_bypass_no_transform(void);
const ngx_str_t *ngx_http_markdown_reason_overload(void);
const ngx_str_t *ngx_http_markdown_reason_invalid_dynconf(void);
const ngx_str_t *ngx_http_markdown_reason_degraded_snapshot(void);
const ngx_str_t *ngx_http_markdown_reason_header_plan_apply_err(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_mid_flight_err(void);
const ngx_str_t *ngx_http_markdown_eligibility_string(
    ngx_http_markdown_eligibility_t eligibility);

/*
 * Log a failure decision with the appropriate reason code and optional
 * error category from the request context.
 *
 * Delegates to ngx_http_markdown_emit_failure_decision() defined in
 * payload_impl.h.
 *
 * Parameters:
 *   r    - NGINX request structure
 *   ctx  - per-request module context (for error category)
 *   conf - module location configuration (for on_error policy)
 */
static void
ngx_http_markdown_log_failure_decision(ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_http_markdown_emit_failure_decision(r, ctx, conf);
}


/*
 * Bind the function-level snapshot copy and effective conf view into
 * the request-pool-allocated context.  This eliminates the race window
 * where the global active_snapshot could be swapped by a concurrent
 * timer reload between the initial header-phase read and the ctx bind.
 *
 * After this call:
 *   - When conf->advanced.dynconf_enabled is true: ctx->dynconf_snapshot holds
 *     a pool-owned copy of snap_copy, and ctx->effective_conf holds a
 *     pool-owned copy of early_eff (derived from the snapshot).
 *   - When conf->advanced.dynconf_enabled is false: ctx->dynconf_snapshot is
 *     NULL (no snapshot bound — this location uses static/inherited
 *     config only), and ctx->effective_conf holds a pool-owned copy
 *     of early_eff (derived from live conf, since header_filter
 *     passed NULL snapshot to build_effective_conf for non-dynconf
 *     locations).
 *
 * If pool allocation fails, the corresponding pointer remains NULL
 * and effective_conf helpers fall back to live conf values (degraded
 * mode).
 *
 * Parameters:
 *   r         - NGINX request structure (for pool and logging)
 *   ctx       - per-request context (already initialised)
 *   snap_copy - function-level snapshot captured once at header_filter entry
 *   early_eff - function-level effective view derived from snap_copy or live conf
 *   conf      - module location configuration (for dynconf_enabled check)
 */
static void
ngx_http_markdown_bind_request_snapshot(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_dynconf_snapshot_t *snap_copy,
    const ngx_http_markdown_effective_conf_t *early_eff,
    const ngx_http_markdown_conf_t *conf)
{
    /*
     * Only bind the dynconf snapshot when this location has
     * dynconf enabled.  Non-dynconf locations must never hold
     * a reference to the global snapshot — their configuration
     * comes exclusively from static/inherited conf values.
     * ctx->dynconf_snapshot remains NULL for non-dynconf
     * locations, which is the correct state.
     */
    if (conf->advanced.dynconf_enabled) {
        if (snap_copy == NULL) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                          "markdown: dynconf_enabled is true but "
                          "snap_copy is NULL; skipping dynconf snapshot binding, "
                          "request will use live conf values");
        } else {
            ctx->dynconf_snapshot =
                ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_dynconf_snapshot_t));
            if (ctx->dynconf_snapshot != NULL) {
                *ctx->dynconf_snapshot = *snap_copy;
            } else {
                ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                              "markdown: failed to allocate dynconf snapshot "
                              "from request pool; request will use live conf values");
            }
        }
    }

    ctx->effective_conf =
        ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_effective_conf_t));
    if (ctx->effective_conf != NULL) {
        *ctx->effective_conf = *early_eff;
    } else {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown: failed to allocate effective conf "
                      "from request pool; request will use live conf values");
    }
}


/*
 * Handle unsupported compression format detected during header phase.
 *
 * Marks the request as ineligible, records error metrics, emits a
 * decision log entry, and applies the configured error strategy.
 *
 * Parameters:
 *   r    - NGINX request structure
 *   ctx  - per-request module context
 *   conf - module location configuration
 *
 * Returns:
 *   NGX_HTTP_BAD_GATEWAY on fail-closed
 *   Result of ngx_http_next_header_filter on fail-open
 */
static ngx_int_t
ngx_http_markdown_handle_unsupported_compression(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ctx->eligible = 0;
    ctx->error.last_category =
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION;
    ctx->error.has_category = 1;

    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);

    if (conf->on_error
        == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
    {
        ngx_log_error(NGX_LOG_WARN,
            r->connection->log, 0,
            "markdown: unsupported "
            "compression format, "
            "rejecting (fail-closed)");
        ngx_http_markdown_log_failure_decision(
            r, ctx, conf);
        return (ngx_int_t) conf->error_status;
    }

    ngx_http_markdown_metric_inc_failopen(conf);

    ngx_log_error(NGX_LOG_WARN,
        r->connection->log, 0,
        "markdown: unsupported "
        "compression format, "
        "returning original content "
        "(fail-open)");
    ngx_http_markdown_log_failure_decision(
        r, ctx, conf);
    ctx->headers_forwarded = 1;
    return ngx_http_next_header_filter(r);
}


/*
 * Handle context allocation failure in header filter.
 *
 * Records metrics, emits decision log with the effective conf view
 * (or NULL if unavailable), and applies the configured error strategy
 * (fail-closed returns 500, fail-open passes through).
 *
 * Parameters:
 *   r    - NGINX request structure
 *   conf - module location configuration
 *   eff  - effective configuration view (may be NULL if allocation
 *          failed before early_eff was built)
 *
 * Returns:
 *   NGX_HTTP_INTERNAL_SERVER_ERROR on fail-closed
 *   Result of ngx_http_next_header_filter on fail-open
 */
static ngx_int_t
ngx_http_markdown_handle_ctx_alloc_failure(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                 "markdown: failed to allocate "
                 "context, category=system");

    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);

    if (conf->on_error
        == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
    {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown: context allocation "
            "failed, rejecting (fail-closed)");
            ngx_http_markdown_log_decision_with_category(
            r, conf, eff,
            ngx_http_markdown_reason_failed_closed(),
            ngx_http_markdown_reason_from_error_category(
                NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
                r->connection->log));
        return (ngx_int_t) conf->error_status;
    }

    ngx_http_markdown_metric_inc_failopen(conf);

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                 "markdown: context allocation "
                 "failed, returning original content "
                 "(fail-open)");
    ngx_http_markdown_log_decision_with_category(
        r, conf, eff,
        ngx_http_markdown_reason_failed_open(),
        ngx_http_markdown_reason_from_error_category(
            NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
            r->connection->log));
    return ngx_http_next_header_filter(r);
}


/*
 * Initialize per-request context fields.
 *
 * Parameters:
 *   r    - NGINX request structure
 *   ctx  - freshly allocated context (zeroed by ngx_pcalloc)
 *   filter_enabled - cached header-phase filter decision
 */
static void
ngx_http_markdown_init_ctx(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_flag_t filter_enabled)
{
    ctx->request = r;
    ctx->filter_enabled = filter_enabled;
    ctx->eligible = 1;
    ctx->buffer_initialized = 0;
    ctx->headers_forwarded = 0;
    ctx->last_modified.source_last_modified_time =
        r->headers_out.last_modified_time;
    ctx->last_modified.has_last_modified_time =
        (r->headers_out.last_modified_time != (time_t) -1);
    ctx->conversion.attempted = 0;
    ctx->conversion.succeeded = 0;
    ctx->conversion.bypass_counted = 0;
    ctx->processing_path =
        NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    ctx->error.last_category =
        NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    ctx->error.has_category = 0;

    /*
     * Initialize decompression state.
     * For uncompressed content, decompression_needed
     * remains 0, ensuring zero overhead in the body
     * filter.
     */
    ctx->decompression.type =
        NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
    ctx->decompression.needed = 0;
    ctx->decompression.done = 0;
    ctx->decompression.compressed_size = 0;
    ctx->decompression.decompressed_size = 0;

#ifdef MARKDOWN_STREAMING_ENABLED
    ctx->streaming.completion.failure_recorded = 0;
#endif
}


static void
ngx_http_markdown_log_accept_skip(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    ngx_uint_t accept_reason)
{
    ngx_http_markdown_decision_path_t  dp;

    dp.conditional_result = NGX_HTTP_MARKDOWN_COND_SKIPPED;
    dp.conversion_status = NGX_HTTP_MARKDOWN_CONV_SKIPPED;
    dp.duration_ms = 0;

    switch (accept_reason) {

    case NEGOTIATE_REASON_NO_ACCEPT:
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.no_accept);
        ngx_http_markdown_log_decision(r, conf, eff,
            ngx_http_markdown_reason_skip_no_accept());
        dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_NONE;
        dp.reason_code = "skipped_no_accept";
        break;

    case NEGOTIATE_REASON_EXPLICIT_REJECT:
        ngx_http_markdown_log_decision(r, conf, eff,
            ngx_http_markdown_reason_skip_accept_reject());
        dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_REJECT;
        dp.reason_code = "skipped_accept_reject";
        break;

    default:
        ngx_http_markdown_log_decision(r, conf, eff,
            ngx_http_markdown_reason_skip_accept());
        dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_SKIP;
        dp.reason_code = "skipped_accept";
        break;
    }

    ngx_http_markdown_log_decision_path(r, conf, eff, &dp);
}

#ifdef MARKDOWN_STREAMING_ENABLED
/*
 * Log a streaming path selection decision at debug level.  Keep this as a
 * macro because ngx_log_debugN may compile away its arguments in non-debug
 * builds; a function wrapper then looks like unused parameters to analyzers.
 */
#define ngx_http_markdown_log_streaming_decision(r, conf, ctx, engine)       \
    do {                                                                    \
        ngx_log_debug6(NGX_LOG_DEBUG_HTTP,                                  \
            (r)->connection->log, 0,                                        \
            "markdown: streaming decision: "                                \
            "engine=%s phase=header_filter "                               \
            "committed=0 fallback_available=1 "                            \
            "reason=%s content_type=%V "                                   \
            "content_length_known=%d chunked=%d "                          \
            "error_policy=%s",                                             \
            (engine),                                                       \
            ngx_http_markdown_stream_reason_str((ctx)->streaming.reason),   \
            &(r)->headers_out.content_type,                                 \
            ((r)->headers_out.content_length_n >= 0) ? 1 : 0,               \
            ((r)->headers_out.content_length_n < 0) ? 1 : 0,                \
            ((conf)->stream.on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS)    \
                ? "pass" : "reject");                                      \
    } while (0)
#endif /* MARKDOWN_STREAMING_ENABLED */

static ngx_flag_t
ngx_http_markdown_header_precheck(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *early_eff,
    ngx_flag_t filter_enabled, ngx_int_t *rc)
{
    ngx_http_markdown_eligibility_t  eligibility;
    ngx_uint_t                       accept_reason;

    if (!filter_enabled) {
        NGX_HTTP_MARKDOWN_METRIC_INC(requests_entered);
        ngx_http_markdown_metric_inc_skip(
            NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_decision(r, conf, early_eff,
            ngx_http_markdown_reason_from_eligibility(
                NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG,
                r->connection->log));
        *rc = ngx_http_next_header_filter(r);
        return 1;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(requests_entered);

    eligibility = ngx_http_markdown_check_eligibility(
        r, conf, filter_enabled, early_eff);
    if (eligibility != NGX_HTTP_MARKDOWN_ELIGIBLE) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP,
                      r->connection->log, 0,
                      "markdown: response not eligible: %V",
                      ngx_http_markdown_eligibility_string(
                          eligibility));
        ngx_http_markdown_metric_inc_skip(eligibility);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_decision(r, conf, early_eff,
            ngx_http_markdown_reason_from_eligibility(
                eligibility, r->connection->log));
        *rc = ngx_http_next_header_filter(r);
        return 1;
    }

    if (conf->policy.auth_policy == NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY
        && ngx_http_markdown_is_authenticated(r, conf))
    {
        ngx_http_markdown_metric_inc_skip(
            NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_decision(r, conf, early_eff,
            ngx_http_markdown_reason_from_eligibility(
                NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH,
                r->connection->log));
        *rc = ngx_http_next_header_filter(r);
        return 1;
    }

    if (ngx_http_markdown_has_no_transform(r)) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: Cache-Control: no-transform present, "
                      "bypassing conversion");
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_decision(r, conf, early_eff,
            ngx_http_markdown_reason_bypass_no_transform());
        *rc = ngx_http_next_header_filter(r);
        return 1;
    }

    if (!ngx_http_markdown_should_convert(r, conf, &accept_reason)) {
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.accept);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_accept_skip(r, conf, early_eff,
            accept_reason);
        *rc = ngx_http_next_header_filter(r);
        return 1;
    }

    return 0;
}


/*
 * Per-worker inflight guard (spec 52).
 *
 * After eligibility passes and before Rust conversion begins,
 * try to increment the inflight counter.  If the worker is at
 * capacity (current >= max_inflight), apply the configured
 * error policy (pass/status/fail_closed from spec 51).
 *
 * The cleanup handler registered by try_increment guarantees
 * decrement on every exit path (normal, abort, timeout, error)
 * via r->pool destruction.
 *
 * Returns NGX_OK on success, or a non-OK value that the caller
 * should return directly from the header filter.
 */
static ngx_int_t
ngx_http_markdown_check_inflight(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  inflight_rc;

    inflight_rc = ngx_http_markdown_inflight_try_increment(
        r, conf);

    if (inflight_rc == NGX_DECLINED) {
        /* Overloaded — apply error policy */
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);

        if (conf->on_error
            == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
        {
            ngx_log_error(NGX_LOG_WARN,
                r->connection->log, 0,
                "markdown: inflight overload, "
                "rejecting (fail-closed)");
            ngx_http_markdown_log_decision(
                r, conf, ctx->effective_conf,
                ngx_http_markdown_reason_overload());
            return conf->error_status;
        }

        /* fail-open: pass through original response */
        ngx_http_markdown_metric_inc_failopen(conf);
        ctx->eligible = 0;
        ctx->headers_forwarded = 1;

        ngx_log_error(NGX_LOG_WARN,
            r->connection->log, 0,
            "markdown: inflight overload, "
            "returning original content "
            "(fail-open)");
        ngx_http_markdown_log_decision(
            r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_overload());
        return ngx_http_next_header_filter(r);
    }

    if (inflight_rc == NGX_ERROR) {
        /* Cleanup alloc failed — treat as system error */
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);

        if (conf->on_error
            == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
        {
            ngx_http_markdown_log_decision(
                r, conf, ctx->effective_conf,
                ngx_http_markdown_reason_failed_closed());
            return (ngx_int_t) conf->error_status;
        }

        ngx_http_markdown_metric_inc_failopen(conf);
        ctx->eligible = 0;
        ctx->headers_forwarded = 1;
        ngx_http_markdown_log_decision(
            r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_failed_open());
        return ngx_http_next_header_filter(r);
    }

    /* NGX_OK: inflight incremented, cleanup registered */
    return NGX_OK;
}


#ifdef MARKDOWN_STREAMING_ENABLED
static ngx_flag_t
ngx_http_markdown_route_streaming_compression(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    if (!ctx->decompression.needed
        || ctx->processing_path != NGX_HTTP_MARKDOWN_PATH_STREAMING)
    {
        return 0;
    }

    if (ctx->decompression.type
        == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE)
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            perf.decompression_streaming_total);
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
            "markdown: streaming decompression selected for deflate");
        return 0;
    }

    ctx->processing_path = NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    ctx->streaming.reason = NGX_HTTP_MARKDOWN_STREAM_REASON_COMPRESSED;
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.engine_choice.full_buffer);

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
        "markdown: streaming decompression not available for encoding %d, "
        "routing to full-buffer", ctx->decompression.type);
    ngx_http_markdown_log_streaming_decision(
        r, conf, ctx, "full_buffer");
    ngx_http_markdown_log_decision(
        r, conf, ctx->effective_conf,
        ngx_http_markdown_reason_streaming_skip_compressed());

    return 1;
}
#endif


/**
 * Determine whether the response should be converted and, if eligible,
 * initialize a per-request Markdown conversion context for body buffering.
 *
 * When conversion is eligible this function allocates and installs a
 * ngx_http_markdown_ctx_t on the request, detects/initializes decompression
 * state (honoring the auto_decompress configuration), selects a processing
 * path (full-buffer or incremental) based on configuration and headers,
 * records path-hit metrics for eligible requests, requests in-memory buffering
 * from upstream, and defers downstream header emission until the body phase.
 * If an unsupported compression format is detected the function marks the
 * request for fail-open (original content returned) and does not enable
 * decompression.
 *
 * @param r The current HTTP request.
 * @return NGX_OK when the header-phase processing is handled and downstream
 *         header emission is deferred to the body filter; otherwise returns
 *         the result of passing the request to the next header filter.
 */
static ngx_int_t
ngx_http_markdown_header_filter(ngx_http_request_t *r)
{
    ngx_http_markdown_ctx_t         *ctx;
    const ngx_http_markdown_conf_t  *conf;
    ngx_flag_t                       filter_enabled;
    ngx_int_t                        precheck_rc;
    ngx_http_markdown_dynconf_snapshot_t  snap_copy;
    ngx_http_markdown_effective_conf_t    early_eff;

    /* Dynamic config: no file I/O in request path.
     *
     * The timer handler performs two-phase reload (read + parse
     * into staging, then atomic swap of active snapshot) entirely
     * in the event loop, never on the request path.  The
     * header_filter copies the active snapshot into request-pool
     * memory and builds an effective_conf view from that copy.
     * A concurrent reload may swap the global active_snapshot,
     * but this request continues using its own copy and derived
     * effective view, guaranteeing request-level consistency.
     *
     * [Rule 34 / E03.2 audit] Bind-once semantic verified:
     *   - active_snapshot read exactly once (snap_copy below)
     *   - build_effective_conf called once from snap_copy
     *   - ctx->effective_conf bound via bind_request_snapshot
     *   - body_filter, streaming, conversion paths all read
     *     from ctx->effective_conf — never re-read global
     */

    /* Get module configuration */
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    if (conf == NULL) {
        /* Module not configured, pass through */
        return ngx_http_next_header_filter(r);
    }

    /*
     * Build a request-local effective configuration view early, before
     * the enabled check, so that is_enabled() and all subsequent
     * header-phase decision logs read from the snapshot rather than
     * live conf.  This stack-allocated view is later copied into the
     * request-pool-allocated ctx->effective_conf.
     *
     * snap_copy and early_eff are function-lifetime variables so they
     * remain valid through ctx binding below.  The snapshot copy on
     * the stack guarantees that the enabled decision is consistent
     * with all subsequent body/conversion/logging reads, even if a
     * concurrent timer reload swaps the global active_snapshot between
     * the early read and the ctx bind — we copy snap_copy into ctx,
     * never re-read the global active_snapshot.
     */
    ngx_memzero(&snap_copy, sizeof(snap_copy));

    /*
     * Copy the global snapshot exactly once.  NGINX runs timer reloads
     * and request header filters on the worker event loop, not
     * concurrently on separate threads, so a plain struct copy is the
     * correct lifecycle primitive here.  Do not use atomic builtins on
     * the aggregate snapshot: coverage builds promote clang's
     * large/misaligned atomic-struct warning to a compile error.
     */
    snap_copy = ngx_http_markdown_dynconf_watcher.active_snapshot;

    /*
     * Build effective conf from the global snapshot ONLY when
     * dynconf_enabled is true for this location.  When a location
     * has markdown_dynamic_config off, it must not consume values
     * from the global dynconf snapshot — doing so would leak
     * runtime changes from other locations into this location's
     * static/inherited configuration.  Passing NULL causes
     * build_effective_conf to fall back to the live conf, which
     * is the correct source for non-dynconf locations.
     */
    ngx_memzero(&early_eff, sizeof(early_eff));
    ngx_http_markdown_build_effective_conf(
        &early_eff,
        conf->advanced.dynconf_enabled ? &snap_copy : NULL,
        conf);

    /*
     * Resolve markdown_filter once in header phase and cache the result in
     * request context. Body phase must reuse this decision to avoid
     * header/body inconsistencies for dynamic variables.
     */
    filter_enabled = ngx_http_markdown_is_enabled(r, conf, &early_eff);
    if (ngx_http_markdown_header_precheck(
            r, conf, &early_eff, filter_enabled, &precheck_rc))
    {
        return precheck_rc;
    }

    /* Create request context for buffering */
    ctx = ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_ctx_t));
    if (ctx == NULL) {
        return ngx_http_markdown_handle_ctx_alloc_failure(
            r, conf, &early_eff);
    }

    /* Initialize context */
    ngx_http_markdown_init_ctx(r, ctx, filter_enabled);

    /*
     * Bind request-lifetime snapshot and effective_conf from the
     * function-level snap_copy and early_eff.  This eliminates the
     * race window where the global active_snapshot could be swapped
     * by a concurrent timer reload between the early read (above)
     * and a second read here.  We never re-read the global
     * active_snapshot in this function after the initial copy.
     *
     * Degraded mode: if pool allocation fails, the pointer remains
     * NULL and effective_conf helpers fall back to live conf values.
     * In this state, a concurrent dynconf reload may cause
     * mid-request drift.  This is a low-probability degraded mode
     * under extreme memory pressure — the request still completes
     * but without snapshot consistency guarantees.  The fallback
     * is logged at NGX_LOG_WARN.
     */
    ngx_http_markdown_bind_request_snapshot(
        r, ctx, &snap_copy, &early_eff, conf);

    /* Set context for this request */
    r->ctx[ngx_http_markdown_filter_module.ctx_index] = ctx;

    /*
     * Detect Content-Encoding ALWAYS, before streaming candidate
     * evaluation (compression detection before streaming candidate evaluation).
     *
     * Compressed responses MUST NOT enter the streaming parser
     * directly.  Detection runs unconditionally so that:
     *
     *  - auto_decompress ON + known format: decompress via
     *    full-buffer or streaming decompression path.
     *  - auto_decompress ON + unknown format: passthrough or
     *    reject per on_error policy.
     *  - auto_decompress OFF + any encoding present: passthrough
     *    (compressed data must not enter parser).
     *
     * Covers: compression detection, auto_decompress off passthrough,
     * unknown format handling, and compressed content exclusion from streaming
     */
    ctx->decompression.type = ngx_http_markdown_detect_compression(r);

    if (ctx->decompression.type != NGX_HTTP_MARKDOWN_COMPRESSION_NONE) {
        if (!conf->decompress.auto_decompress) {
            /*
             * auto_decompress is OFF but Content-Encoding is present.
             * Cannot safely parse compressed content — passthrough.
             * (auto_decompress disabled: compressed content must not enter parser)
             */
            ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
                         "markdown: Content-Encoding present "
                         "(type=%d) but auto_decompress is off, "
                         "passing through original content",
                         ctx->decompression.type);
            ctx->eligible = 0;
            ctx->headers_forwarded = 1;
            NGX_HTTP_MARKDOWN_METRIC_INC(
                skips.compression_passthrough);
            ngx_http_markdown_log_decision(r, conf,
                ctx->effective_conf,
                ngx_http_markdown_reason_streaming_skip_compressed());
            return ngx_http_next_header_filter(r);

        } else if (ctx->decompression.type
                   == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN)
        {
            /*
             * Unsupported compression format detected
             *
             * This is an expected degradation scenario, not a
             * failure.  We gracefully degrade by returning the
             * original content.
             *
             * Note: The warning log has already been emitted by
             * ngx_http_markdown_detect_compression() with the
             * format name.
             *
              * Covers: unsupported compression format handling
             */
            return ngx_http_markdown_handle_unsupported_compression(
                r, ctx, conf);

        } else {
            /* Supported compression format - set flag for decompression */
            ctx->decompression.needed = 1;

            ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown: decompression detected "
                          "compression type: %d",
                          ctx->decompression.type);
        }
    }

    /*
     * Threshold router: select processing path.
     *
     * HEAD requests and 304 responses always use the
     * full-buffer path regardless of the configured
     * threshold.  When the threshold is off (== 0), all
     * requests also use the full-buffer path.
     *
     * When Content-Length is known and >= threshold, select
     * the incremental path.  When Content-Length is absent,
     * the decision is deferred to the body filter (buffer
     * first, then decide once buffered size is known).
     *
     * Fail-open replay (returning buffered original HTML
     * after conversion failure) always uses the full-buffer
     * path; that is enforced at body-filter time since the
     * replay decision happens after conversion attempt.
     *
     * Covers: threshold routing, HEAD/304 bypass, path selection,
     * unknown-length deferral, fail-open replay enforcement
     */

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * Engine selector: evaluate markdown_streaming_engine
     * once and cache the result. If streaming is selected,
     * skip the threshold router entirely.
     */
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.selection.candidate_total);

    ngx_http_markdown_path_selection_t selection =
        ngx_http_markdown_select_processing_path(
            r, conf, ctx->effective_conf);
    ctx->processing_path = selection.path;
    ctx->streaming.reason = selection.reason;

    /*
     * Streaming decompression routing (Req 4.1, 4.2, 4.5–4.7, 4.9):
     *
     * When compression is detected AND streaming was selected,
     * decide whether to route through streaming decompression or
     * force the full-buffer path.
     *
     * Streaming decompression is selected iff ALL FOUR conditions:
     *   (1) auto_decompress on  (already verified above — otherwise
     *       the request was passthrough'd before reaching here)
     *   (2) streaming engine selected (ctx->processing_path ==
     *       PATH_STREAMING at this point)
     *   (3) cache_validation NOT full (select_processing_path
     *       already forces full-buffer for full_support, so if
     *       we reach here streaming was selected → not full)
     *   (4) encoding supported by the 0.9.1 streaming decompression
     *       contract:
     *       - deflate (zlib-wrapped per RFC 9110, or raw deflate):
     *         supported via deferred header sniffing
     *       - gzip/brotli: deferred, route to full-buffer
     *
     * ngx_http_markdown_streaming_decomp_impl.h retains deferred
     * gzip/brotli streaming implementation branches for future
     * enablement, but this routing gate makes them unreachable for
     * the 0.9.1 supported streaming path.
     *
     * This check runs after select_processing_path() so that
     * compression routing is enforced regardless of engine mode
     * (on, auto, or variable-evaluated).
     */
    if (ngx_http_markdown_route_streaming_compression(r, ctx, conf)) {
        goto path_selected;
    }

    if (ctx->processing_path
        == NGX_HTTP_MARKDOWN_PATH_STREAMING)
    {
        ctx->streaming.reason =
            NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE;

        /* Sync streaming fallback state machine: header selected streaming → STREAMING_CANDIDATE */
        ctx->stream_sm.state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;

        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.engine_choice.streaming);
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.selection.true_streaming_selected_total);

        ngx_http_markdown_log_streaming_decision(
            r, conf, ctx, "streaming");

        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_engine_streaming());

        goto path_selected;
    }

    /*
     * select_processing_path() returned FULLBUFFER without
     * compression override — record engine choice here.
     */
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.engine_choice.full_buffer);

    ngx_http_markdown_log_streaming_decision(
        r, conf, ctx, "full_buffer");
#endif /* MARKDOWN_STREAMING_ENABLED */

#ifdef MARKDOWN_INCREMENTAL_ENABLED
    if (conf->routing.large_body_threshold > 0
        && r->method != NGX_HTTP_HEAD
        && r->headers_out.status != NGX_HTTP_NOT_MODIFIED
        && r->headers_out.content_length_n >= 0
        && (size_t) r->headers_out.content_length_n
           >= conf->routing.large_body_threshold)
    {
        ctx->processing_path =
            NGX_HTTP_MARKDOWN_PATH_INCREMENTAL;
    }
    /* else: unknown Content-Length — path deferred to body filter */
#else
    if (conf->routing.large_body_threshold > 0
        && r->method != NGX_HTTP_HEAD
        && r->headers_out.status != NGX_HTTP_NOT_MODIFIED)
    {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: markdown_large_body_threshold is set, "
                     "but incremental support was not compiled in; using "
                     "full-buffer path");
    }
#endif

    /* Record path hit metric (only for eligible requests) */
#ifdef MARKDOWN_STREAMING_ENABLED
path_selected:
    ;
#endif

    ngx_int_t  inflight_rc;

    inflight_rc = ngx_http_markdown_check_inflight(r, ctx, conf);
    if (inflight_rc != NGX_OK) {
        return inflight_rc;
    }

    if (ctx->eligible) {
#ifdef MARKDOWN_STREAMING_ENABLED
        if (ctx->processing_path
            == NGX_HTTP_MARKDOWN_PATH_STREAMING)
        {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                path_hits.streaming);
        } else
#endif
        if (ctx->processing_path
            == NGX_HTTP_MARKDOWN_PATH_INCREMENTAL)
        {
            NGX_HTTP_MARKDOWN_METRIC_INC(path_hits.incremental);
        } else {
            NGX_HTTP_MARKDOWN_METRIC_INC(path_hits.fullbuffer);
        }
    }

    /*
     * Request in-memory buffers from upstream filters/modules.
     *
     * Static file responses may otherwise arrive as file-backed buffers
     * (sendfile path), where `buf->pos..last` is empty and the payload is
     * only described by file offsets. This filter buffers and converts the
     * response body in userspace, so it requires in-memory data.
     */
    r->filter_need_in_memory = 1;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: response eligible for conversion, "
                  "context initialized");

    /*
     * Defer downstream header emission until the body filter completes
     * conversion (or decides to fail-open). This allows the module to set
     * accurate Content-Type / Content-Length / ETag headers based on the
     * converted Markdown output.
     */
    return NGX_OK;
}

/**
 * Determine conditional-request outcome, perform Markdown conversion if needed, and emit the conversion result for a buffered response.
 *
 * @param r The active nginx request.
 * @param ctx Per-request Markdown module context.
 * @param conf Module configuration for the current request.
 * @returns `NGX_OK` on success, `NGX_AGAIN` if downstream requires further processing, or another `ngx_int_t` code returned by underlying helpers to indicate a filter-chain decision or error.
 */
static ngx_int_t
ngx_http_markdown_body_filter_convert_and_output(ngx_http_request_t *r,
                                                 ngx_http_markdown_ctx_t *ctx,
                                                 const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t             rc;
    ngx_msec_t            elapsed_ms;
    ngx_flag_t            has_result;
    struct MarkdownResult result;
    markdown_result_init(&result);

    /*
     * Deferred path selection for chunked/unknown-length
     * responses.  If Content-Length was absent in the
     * header phase, the threshold decision was deferred
     * until the full body is buffered.
     *
     * Covers: deferred path selection for chunked/unknown-length responses
     */
#ifdef MARKDOWN_INCREMENTAL_ENABLED
    if (conf->routing.large_body_threshold > 0
        && ctx->processing_path
            == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER
        && r->method != NGX_HTTP_HEAD
        && r->headers_out.status != NGX_HTTP_NOT_MODIFIED
        && ctx->buffer.size >= conf->routing.large_body_threshold)
    {
        ctx->processing_path =
            NGX_HTTP_MARKDOWN_PATH_INCREMENTAL;

        /*
         * Correct the path hit counters: header filter
         * already incremented path_hits.fullbuffer, so
         * undo that and count incremental instead.
         *
         * Guard against underflow: only decrement if the
         * counter is positive.  In theory the header filter
         * always increments first, but a metrics zone reset
         * (e.g. worker restart) could leave the counter at
         * zero.
         */
        NGX_HTTP_MARKDOWN_METRIC_SAFE_DEC(path_hits.fullbuffer);
        NGX_HTTP_MARKDOWN_METRIC_INC(
            path_hits.incremental);
    }
#endif

    /*
     * conversion_attempted and conversions_attempted metric are
     * already set by the body filter before decompression.
     */
    elapsed_ms = 0;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: buffered complete response, size: %uz bytes",
                  ctx->buffer.size);

    rc = ngx_http_markdown_resolve_conditional_result(
        r, ctx, conf, &result, &elapsed_ms, &has_result);
    if (rc == NGX_HTTP_NOT_MODIFIED) {
        /* 304 Not Modified — skip conversion, client has current */
        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_skip_conditional());

        {
            ngx_http_markdown_decision_path_t  dp;

            dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_CONVERT;
            dp.conditional_result =
                NGX_HTTP_MARKDOWN_COND_NOT_MODIFIED;
            dp.conversion_status =
                NGX_HTTP_MARKDOWN_CONV_SKIPPED;
            dp.reason_code = "skipped_conditional";
            NGX_HTTP_MARKDOWN_METRIC_INC(skips.conditional);
            dp.duration_ms = elapsed_ms;
            ngx_http_markdown_log_decision_path(
                r, conf, ctx->effective_conf, &dp);
        }

        return NGX_OK;
    }
    if (rc != NGX_OK) {
        /* Conditional processing failed — log failure outcome */
        ngx_http_markdown_log_failure_decision(r, ctx, conf);

        {
            ngx_http_markdown_decision_path_t  dp;

            dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_CONVERT;
            dp.conditional_result =
                NGX_HTTP_MARKDOWN_COND_PROCEED;
            dp.conversion_status =
                NGX_HTTP_MARKDOWN_CONV_FAILED;
            dp.reason_code = (conf->on_error
                == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
                ? "failed_closed"
                : "failed_open";
            dp.duration_ms = elapsed_ms;
            ngx_http_markdown_log_decision_path(
                r, conf, ctx->effective_conf, &dp);
        }

        return rc;
    }

    if (!has_result) {
        rc = ngx_http_markdown_execute_conversion(r, ctx, conf, &result, &elapsed_ms);
        if (rc != NGX_OK) {
            /* Conversion failed — log failure outcome */
            ngx_http_markdown_log_failure_decision(r, ctx, conf);

            {
                ngx_http_markdown_decision_path_t  dp;

                dp.accept_result =
                    NGX_HTTP_MARKDOWN_ACCEPT_CONVERT;
                dp.conditional_result =
                    NGX_HTTP_MARKDOWN_COND_PROCEED;
                dp.conversion_status =
                    NGX_HTTP_MARKDOWN_CONV_FAILED;
                dp.reason_code = (conf->on_error
                    == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
                    ? "failed_closed"
                    : "failed_open";
                dp.duration_ms = elapsed_ms;
                ngx_http_markdown_log_decision_path(
                    r, conf, ctx->effective_conf, &dp);
            }

            return rc;
        }
    }

    rc = ngx_http_markdown_send_conversion_output(
        r, ctx, conf, &result, elapsed_ms);
    if (rc == NGX_OK || rc == NGX_AGAIN) {
        /*
         * Some downstream filters return NGX_AGAIN after successfully
         * accepting the converted body chain. That is still a converted
         * request, so record the decision before propagating the status.
         */
        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_converted());

        {
            ngx_http_markdown_decision_path_t  dp;

            dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_CONVERT;
            dp.conditional_result =
                NGX_HTTP_MARKDOWN_COND_PROCEED;
            dp.conversion_status =
                NGX_HTTP_MARKDOWN_CONV_SUCCESS;
            dp.reason_code = "converted";
            dp.duration_ms = elapsed_ms;
            ngx_http_markdown_log_decision_path(
                r, conf, ctx->effective_conf, &dp);
        }

    } else {
        /*
         * Output emission failed after conversion succeeded.
         * Record the terminal failure decision so the request
         * does not rely solely on the earlier success recording
         * in ngx_http_markdown_execute_conversion().
         */
        ngx_http_markdown_log_failure_decision(r, ctx, conf);

        {
            ngx_http_markdown_decision_path_t  dp;

            dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_CONVERT;
            dp.conditional_result =
                NGX_HTTP_MARKDOWN_COND_PROCEED;
            dp.conversion_status =
                NGX_HTTP_MARKDOWN_CONV_FAILED;
            dp.reason_code = (conf->on_error
                == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
                ? "failed_closed"
                : "failed_open";
            dp.duration_ms = elapsed_ms;
            ngx_http_markdown_log_decision_path(
                r, conf, ctx->effective_conf, &dp);
        }

    }

    return rc;
}

/*
 * Body filter
 *
 * Called for each chunk of the response body.
 * Buffers the response and performs conversion when complete.
 * 
 * Body filter hook
 * - Accumulates response chunks in buffer
 * - Detects when all chunks are buffered (last_buf flag)
 * - Calls Rust conversion engine via FFI
 * - Updates response headers on success
 * - Sends converted Markdown response
 * - Handles errors with configured strategy
 *
 * Covers: body accumulation, conversion execution, header updates,
 * Markdown output, error strategy application
 * 
 * @param r   The request structure
 * @param in  The input chain containing response body chunks
 * @return    NGX_OK on success, NGX_ERROR on error
 */
static ngx_int_t
ngx_http_markdown_body_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    ngx_http_markdown_ctx_t   *ctx;
    const ngx_http_markdown_conf_t  *conf;
    ngx_int_t                  rc;

    /* Get module configuration */
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    if (conf == NULL) {
        /* Module disabled, pass through */
        return ngx_http_next_body_filter(r, in);
    }

    /*
     * Get request context created by header filter.
     *
     * IMPORTANT: Do not re-evaluate markdown_filter here. Dynamic expressions
     * can resolve differently between phases; body filter must follow the
     * cached header-phase decision.
     */
    ctx = ngx_http_get_module_ctx(r, ngx_http_markdown_filter_module);
    if (ctx == NULL) {
        /* No context means header filter didn't set up conversion */
        /* Pass through unchanged */
        return ngx_http_next_body_filter(r, in);
    }

    if (!ctx->filter_enabled) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        return ngx_http_next_body_filter(r, in);
    }

    /*
     * Rule 1 / Rule 38: resume full-buffer pending chain.
     * If the full-buffer path previously returned NGX_AGAIN from
     * send_conversion_output, the pending output must be drained
     * before accepting new input.  This is triggered by NGINX
     * re-invoking the body filter (typically with in == NULL)
     * after the downstream filter becomes writable again.
     */
    if (ctx->fullbuffer.pending_has_data) {
        return ngx_http_markdown_body_filter_resume_pending(r, ctx);
    }

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * A fail-open send can mark the request ineligible while downstream owns
     * a pending streaming chain after NGX_AGAIN.  Drain that chain before the
     * generic ineligible passthrough clears our buffered bit.
     *
     * When new non-NULL input arrives while pending output exists, enqueue
     * it to pending_input instead of rejecting it.  Rejecting (returning
     * NGX_AGAIN without retaining the chain) would strand the input in
     * u->busy_bufs — the same lost-continuation bug as process_chain.
     * The enqueue copies chain links (sharing ngx_buf_t) so NGINX keeps
     * the busy buffers alive until we feed them to Rust after the
     * pending output drains.
     */
    if (ctx->processing_path == NGX_HTTP_MARKDOWN_PATH_STREAMING
        && ctx->streaming.pending_output != NULL)
    {
        if (in != NULL) {
            return ngx_http_markdown_streaming_handle_new_input_with_pending(
                r, ctx, conf, in);
        }
        return ngx_http_markdown_streaming_body_filter(r, NULL);
    }
#endif

    /* If not eligible for conversion, pass through */
    if (!ctx->eligible) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        if (!ctx->conversion.bypass_counted && !ctx->error.has_category) {
            /*
             * Track bypassed request once even if the body
             * arrives in chunks.  Do not count requests that
             * already recorded a failure (has_error_category)
             * — those are accounted for by conversions_failed.
             */
            NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
            ctx->conversion.bypass_counted = 1;
        }
        if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
            return NGX_ERROR;
        }
        return ngx_http_next_body_filter(r, in);
    }

#ifdef MARKDOWN_STREAMING_ENABLED
    /* Streaming path: delegate to streaming body filter */
    if (ctx->processing_path
        == NGX_HTTP_MARKDOWN_PATH_STREAMING)
    {
        return ngx_http_markdown_streaming_body_filter(
            r, in);
    }
#endif

    /* If conversion already completed, do not pass original input through. */
    if (ctx->conversion.attempted) {
        if (!ctx->fullbuffer.pending_has_data) {
            r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        }
        return NGX_OK;
    }

    rc = ngx_http_markdown_body_filter_buffer_input(r, in, ctx, conf);
    if (rc == NGX_AGAIN) {
        return NGX_OK;
    }
    if (rc != NGX_OK) {
        return rc;
    }

    /*
     * Mark conversion as attempted before decompression so that any
     * failure path that increments conversions_failed is always
     * preceded by a conversions_attempted increment.  This keeps
     * the two counters consistent (attempted >= failed).
     */
    ctx->conversion.attempted = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);

    rc = ngx_http_markdown_body_filter_decompress_if_needed(r, ctx, conf);
    if (rc != NGX_OK) {
        return rc;
    }

    return ngx_http_markdown_body_filter_convert_and_output(r, ctx, conf);
}

#endif /* NGX_HTTP_MARKDOWN_REQUEST_IMPL_H */
