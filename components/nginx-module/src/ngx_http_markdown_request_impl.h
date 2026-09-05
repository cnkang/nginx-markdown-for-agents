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

#include "ngx_http_markdown_decompression_route.h"
#include "ngx_http_markdown_payload_impl.h"
#include "ngx_http_markdown_conversion_impl.h"
#include "ngx_http_markdown_exports.h"
#include "ngx_http_markdown_diagnostics.h"
#include "ngx_http_markdown_durable_bypass.h"

static void
ngx_http_markdown_publish_durable_bypass(
    ngx_http_request_t *r,
    ngx_http_markdown_durable_bypass_kind_t kind)
{
    if (r == NULL) {
        return;
    }

    switch (kind) {
    case NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER:
        r->ctx[ngx_http_markdown_filter_module.ctx_index] =
            (void *) &ngx_http_markdown_failopen_header_marker;
        break;

    case NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY:
        r->ctx[ngx_http_markdown_filter_module.ctx_index] =
            (void *) &ngx_http_markdown_failopen_body_marker;
        break;

    case NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_COMPLETED:
        r->ctx[ngx_http_markdown_filter_module.ctx_index] =
            (void *) &ngx_http_markdown_failopen_completed_marker;
        break;

    case NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_HEADER:
        r->ctx[ngx_http_markdown_filter_module.ctx_index] =
            (void *) &ngx_http_markdown_subrequest_header_marker;
        break;

    case NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_BODY:
        r->ctx[ngx_http_markdown_filter_module.ctx_index] =
            (void *) &ngx_http_markdown_subrequest_body_marker;
        break;

    case NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_COMPLETED:
        r->ctx[ngx_http_markdown_filter_module.ctx_index] =
            (void *) &ngx_http_markdown_subrequest_completed_marker;
        break;

    default:
        break;
    }
}

static void
ngx_http_markdown_settle_durable_bypass_delivery(
    ngx_http_request_t *r,
    ngx_http_markdown_durable_bypass_kind_t kind,
    ngx_int_t rc)
{
    if (kind != NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER
        && kind != NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY
        && kind != NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_HEADER
        && kind != NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_BODY)
    {
        return;
    }

    if (rc == NGX_AGAIN || rc == NGX_ERROR) {
        ngx_http_markdown_publish_durable_bypass(
            r, kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER
                    || kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY
                ? NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY
                : NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_BODY);
        return;
    }

    if (rc == NGX_OK || rc == NGX_DONE) {
        if (kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER
            || kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY)
        {
            NGX_HTTP_MARKDOWN_METRIC_INC(results.failopen_count);
        }
        ngx_http_markdown_publish_durable_bypass(
            r, kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER
                    || kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY
                ? NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_COMPLETED
                : NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_COMPLETED);
    }
}

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
    const ngx_http_markdown_conf_t *conf, ngx_chain_t *cl,
    uint32_t *out_error_code);
static ngx_int_t ngx_http_markdown_streaming_handle_postcommit_error(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, uint32_t error_code);
static ngx_int_t ngx_http_markdown_streaming_precommit_error(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, uint32_t error_code);
static ngx_int_t ngx_http_markdown_streaming_handle_header_snapshot_failure(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);
static ngx_int_t ngx_http_markdown_streaming_failopen_passthrough(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx, ngx_chain_t *in);

/*
 * Forward declaration for the shared new-input-with-pending helper.
 * Defined in ngx_http_markdown_streaming_impl.h (included after this
 * header). Used by ngx_http_markdown_body_filter here and by
 * ngx_http_markdown_streaming_body_filter in streaming_impl.h so both
 * entry points stay below SonarCloud c:S3776/c:S134 thresholds.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_new_input_with_pending(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, ngx_chain_t *in);
#endif

/* Forward declarations for helpers defined in this file */
static ngx_int_t ngx_http_markdown_handle_ctx_alloc_failure(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff);
static ngx_int_t ngx_http_markdown_register_fullbuffer_cleanup(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx);
static void ngx_http_markdown_record_path_hit(
    const ngx_http_markdown_ctx_t *ctx);
static ngx_int_t ngx_http_markdown_handle_encoding_collection_failure(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff);
static void ngx_http_markdown_init_ctx(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx, ngx_flag_t filter_enabled);
static void ngx_http_markdown_establish_preaccess_bypass(
    ngx_http_markdown_ctx_t *ctx, ngx_flag_t filter_enabled,
    const ngx_http_markdown_effective_conf_t *eff, ngx_flag_t failopen);
static ngx_int_t ngx_http_markdown_handle_preaccess_failure(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff);
static ngx_int_t ngx_http_markdown_preaccess_capture(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    ngx_flag_t filter_enabled);
static ngx_int_t ngx_http_markdown_handle_vary_accept_failure(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    ngx_int_t vary_rc, ngx_int_t *rc);
static void ngx_http_markdown_request_cleanup(void *data);
static ngx_int_t ngx_http_markdown_forward_prechecked_headers(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);
static ngx_int_t ngx_http_markdown_handle_durable_bypass(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);
static ngx_int_t ngx_http_markdown_create_header_context(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    ngx_flag_t filter_enabled, ngx_http_markdown_ctx_t **ctx_out);
static void ngx_http_markdown_log_failure_decision(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);
static void ngx_http_markdown_log_decision_with_category(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code, const ngx_str_t *error_category);
static void ngx_http_markdown_log_decision(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code);
static void ngx_http_markdown_log_terminal_decision_path(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_decision_path_t *path);
#ifdef MARKDOWN_STREAMING_ENABLED
static void ngx_http_markdown_log_streaming_terminal_decision(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const char *conversion_status,
    const char *reason_code,
    const char *stage);
#endif
static void ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf);
/*
 * Settle a deferred buffered fail-open delivery counter after the
 * recovery pass-through confirms downstream delivery (Rule 38/23).
 * A buffered fail-open send that hit NGX_AGAIN set
 * fullbuffer.failopen_delivery_pending; this helper publishes the
 * delivery count exactly once, guarded by failopen_completed.
 */
static ngx_inline void
ngx_http_markdown_settle_buffered_failopen_delivery(
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_int_t rc)
{
    if ((rc == NGX_OK || rc == NGX_DONE)
        && ctx->fullbuffer.failopen_delivery_pending
        && !ctx->failopen_completed)
    {
        ngx_http_markdown_metric_inc_failopen(
            ctx->effective_conf, conf);
        ctx->failopen_completed = 1;
        ctx->fullbuffer.failopen_delivery_pending = 0;
    }
}
static ngx_http_output_header_filter_pt ngx_http_next_header_filter;
static ngx_http_output_body_filter_pt ngx_http_next_body_filter;

static ngx_int_t
ngx_http_markdown_next_header_filter_with_auth(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf)
{
    ngx_flag_t  auth_cache_control_required;
    ngx_int_t   rc;

    auth_cache_control_required = 0;
    rc = ngx_http_markdown_auth_cache_control_required(
        r, conf, &auth_cache_control_required);
    if (rc != NGX_OK) {
        return rc;
    }

    if (auth_cache_control_required) {
        rc = ngx_http_markdown_modify_cache_control_for_auth(r);
        if (rc != NGX_OK) {
            return rc;
        }
    }

    return ngx_http_next_header_filter(r);
}


#ifdef MARKDOWN_STREAMING_ENABLED
/*
 * A streaming header rollback failure leaves the response representation
 * uncertain.  It must bypass the configured fail-open policy and terminate
 * through NGINX's finalizer, just like the full-buffer and HEAD paths.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_header_snapshot_failure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                  "markdown: stream header snapshot rollback failed; "
                  "fail-open disabled, category=system");
    ngx_http_markdown_record_system_failure(ctx);
    ngx_http_markdown_log_decision_with_category(
        r, conf, ctx->effective_conf,
        ngx_http_markdown_reason_header_plan_apply_err(),
        ngx_http_markdown_reason_from_error_category(
            NGX_HTTP_MARKDOWN_ERROR_SYSTEM, r->connection->log));

    ngx_http_markdown_inflight_release(ctx);

    return ngx_http_filter_finalize_request(
        r, &ngx_http_markdown_filter_module,
        (ngx_int_t) ngx_http_markdown_effective_error_status(
            ctx->effective_conf, conf));
}
#endif

#define NGX_HTTP_MARKDOWN_NEXT_HEADER_FILTER_WITH_AUTH_DEFINED 1

const ngx_str_t *ngx_http_markdown_reason_failed_closed(void);
const ngx_str_t *ngx_http_markdown_reason_failed_open(void);
const ngx_str_t *ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, ngx_log_t *log);
const ngx_str_t *ngx_http_markdown_reason_converted(void);
const ngx_str_t *ngx_http_markdown_reason_bypass_no_transform(void);
const ngx_str_t *ngx_http_markdown_reason_encoding_header_invalid(void);
const ngx_str_t *ngx_http_markdown_reason_decompression_format_error(void);
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
 * If the dynconf snapshot allocation fails, only the snapshot pointer
 * remains NULL.  The effective view has already been copied by value into
 * `eff_storage`, so effective_conf helpers continue using the header-time
 * values and do not drift to live configuration during this request.
 *
 * Parameters:
 *   r         - NGINX request structure (for pool and logging)
 *   ctx       - per-request context (already initialised)
 *   snap_copy - function-level snapshot captured once at header_filter entry
 *   early_eff - function-level effective view derived from snap_copy or live conf
 *   conf      - module location configuration (for dynconf_enabled check)
 */
static void
ngx_http_markdown_bind_request_context_snapshot(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_dynconf_snapshot_t *snap_copy,
    const ngx_http_markdown_effective_conf_t *early_eff,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_http_markdown_bind_request_snapshot(
        r, conf, snap_copy, early_eff,
        &ctx->effective_conf_storage,
        &ctx->dynconf_snapshot, &ctx->effective_conf);
}


/*
 * Handle context allocation failure in header filter.
 *
 * Records metrics, emits decision log with the effective conf view
 * (or NULL if unavailable), and applies the configured error strategy
 * (fail-closed returns the configured status, fail-open passes through).
 *
 * Parameters:
 *   r    - NGINX request structure
 *   conf - module location configuration
 *   eff  - effective configuration view (may be NULL if allocation
 *          failed before early_eff was built)
 *
 * Returns:
 *   Result of ngx_http_filter_finalize_request on fail-closed
 *   Result of ngx_http_next_header_filter on fail-open
 */
static ngx_int_t
ngx_http_markdown_handle_ctx_alloc_failure(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    ngx_int_t  rc;

    ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                 "markdown: failed to allocate "
                 "context, category=system");

    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);

    if (ngx_http_markdown_effective_error_policy(eff, conf)
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
                NGX_HTTP_MARKDOWN_ERROR_SYSTEM, r->connection->log));
        return ngx_http_filter_finalize_request(
            r, &ngx_http_markdown_filter_module,
            (ngx_int_t) ngx_http_markdown_effective_error_status(eff, conf));
    }

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                 "markdown: context allocation "
                 "failed, returning original content "
                 "(fail-open)");
    ngx_http_markdown_log_decision_with_category(
        r, conf, eff,
        ngx_http_markdown_reason_failed_open(),
        ngx_http_markdown_reason_from_error_category(
            NGX_HTTP_MARKDOWN_ERROR_SYSTEM, r->connection->log));
    ngx_http_markdown_publish_durable_bypass(
        r, NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER);
    rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
    ngx_http_markdown_settle_durable_bypass_delivery(
        r, NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER, rc);
    return rc;
}


/* Mark a header-phase rejection before entering NGINX error handling. */
static void
ngx_http_markdown_mark_header_reject(ngx_http_markdown_ctx_t *ctx)
{
    ctx->eligible = 0;
    ctx->error.header_reject = 1;
}


/**
 * Handles failure to collect repeated Content-Encoding fields according to the
 * configured error policy.
 *
 * @param r Request being processed.
 * @param ctx Request-specific Markdown filter context.
 * @param conf Static Markdown filter configuration.
 * @param eff Effective configuration for the request.
 * @note The combined value is request-pool allocated. Allocation failure must
 *       not be treated as an absent field, because that could continue with
 *       incomplete Content-Encoding metadata.
 * @returns The filter-finalize result for fail-closed handling, or the
 *          downstream header-filter result for fail-open handling.
 */
static ngx_int_t
ngx_http_markdown_handle_encoding_collection_failure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    ngx_int_t  rc;

    /*
     * Mark the request ineligible and record the error before any
     * policy dispatch.  Without this the body filter would convert
     * the still-compressed body under the intact Content-Encoding
     * header and would double-count conversions_attempted (Rule 38).
     */
    ctx->eligible = 0;
    ctx->error.last_category = NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    ctx->error.has_category = 1;

    ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                  "markdown: failed to collect Content-Encoding header, "
                  "category=system");

    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);

    if (ngx_http_markdown_effective_error_policy(eff, conf)
        == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        ngx_http_markdown_mark_header_reject(ctx);
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: Content-Encoding header collection failed, "
                      "rejecting (fail-closed)");
        ngx_http_markdown_log_decision_with_category(
            r, conf, eff,
            ngx_http_markdown_reason_failed_closed(),
            ngx_http_markdown_reason_from_error_category(
                NGX_HTTP_MARKDOWN_ERROR_SYSTEM, r->connection->log));
        return ngx_http_filter_finalize_request(
            r, &ngx_http_markdown_filter_module,
            (ngx_int_t) ngx_http_markdown_effective_error_status(eff, conf));
    }

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                  "markdown: Content-Encoding header collection failed, "
                  "returning original content (fail-open)");
    ngx_http_markdown_log_decision_with_category(
        r, conf, eff,
        ngx_http_markdown_reason_failed_open(),
        ngx_http_markdown_reason_from_error_category(
            NGX_HTTP_MARKDOWN_ERROR_SYSTEM, r->connection->log));
    ngx_http_markdown_restore_conditional_request(r, ctx);
    rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
    /*
     * Canonical NGINX model: header-chain NGX_AGAIN means the write filter
     * queued the header block — the headers are accepted.  Publish the
     * delivery latch so the retry path forwards the buffered original body
     * without re-entering the header chain (Rule 47 delivery latches still
     * follow confirmed success for terminal markers; header acceptance is
     * owned by the write filter once NGX_AGAIN is returned).
     */
    if (rc == NGX_AGAIN) {
        ctx->headers_forwarded = 1;
        ctx->fullbuffer.failopen_delivery_pending = 1;
        return rc;
    }
    if (rc == NGX_OK || rc == NGX_DONE) {
        ctx->headers_forwarded = 1;
        ngx_http_markdown_metric_inc_failopen(eff, conf);
    }
    return rc;
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
    ctx->lifecycle.header_filter_initialized = 1;
    ctx->lifecycle.bypass = 0;
    ctx->lifecycle.last_modified.source_last_modified_time =
        r->headers_out.last_modified_time;
    ctx->lifecycle.last_modified.has_last_modified_time =
        (r->headers_out.last_modified_time != (time_t) -1);
    ctx->conversion.attempted = 0;
    ctx->conversion.succeeded = 0;
    ctx->conversion.delivery_recorded = 0;
    ctx->conversion.bypass_counted = 0;
    ctx->conversion.input_bytes = 0;
    ctx->conversion.output_bytes = 0;
    ctx->processing_path =
        NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    ctx->error.last_category =
        NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    ctx->error.has_category = 0;
    ctx->error.terminal_decision_recorded = 0;
    ctx->error.header_reject = 0;

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
    dp.stage = "eligibility";
    dp.error_category = NULL;
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

    case NEGOTIATE_REASON_INTERNAL_ERROR:
    default:
        ngx_http_markdown_log_decision(r, conf, eff,
            ngx_http_markdown_reason_skip_accept());
        dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_SKIP;
        dp.reason_code = "skipped_accept";
        break;
    }

    ngx_http_markdown_log_terminal_decision_path(
        r, conf, eff, NULL, &dp);
}


static void
ngx_http_markdown_log_terminal_decision_path(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_decision_path_t *path)
{
    if (ctx != NULL) {
        if (ctx->error.terminal_decision_recorded) {
            return;
        }
        ctx->error.terminal_decision_recorded = 1;
    }

    ngx_http_markdown_log_decision_path(r, conf, eff, path);
}


static void
ngx_http_markdown_log_header_terminal_decision(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const char *reason_code)
{
    ngx_http_markdown_decision_path_t  path;

    path.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_SKIP;
    path.conditional_result = NGX_HTTP_MARKDOWN_COND_SKIPPED;
    path.conversion_status = NGX_HTTP_MARKDOWN_CONV_SKIPPED;
    path.reason_code = reason_code;
    path.stage = "eligibility";
    path.error_category = NULL;
    path.duration_ms = 0;
    ngx_http_markdown_log_terminal_decision_path(
        r, conf, eff, NULL, &path);
}

#ifdef MARKDOWN_STREAMING_ENABLED
static void
ngx_http_markdown_log_streaming_terminal_decision(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const char *conversion_status,
    const char *reason_code,
    const char *stage)
{
    ngx_http_markdown_decision_path_t  path;

    if (ctx == NULL) {
        return;
    }

    path.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_CONVERT;
    path.conditional_result = NGX_HTTP_MARKDOWN_COND_PROCEED;
    path.conversion_status = conversion_status;
    path.reason_code = reason_code;
    path.stage = stage;
    path.error_category = ctx->error.has_category
        ? (const char *) ngx_http_markdown_error_category_string(
              ctx->error.last_category)->data : NULL;
    path.duration_ms = 0;
    ngx_http_markdown_log_terminal_decision_path(
        r, conf, ctx->effective_conf, ctx, &path);
}


/*
 * Log a streaming path selection decision at debug level.  Keep this as a
 * macro because ngx_log_debugN may compile away its arguments in non-debug
 * builds; a function wrapper then looks like unused parameters to analyzers.
 */
#define ngx_http_markdown_log_streaming_decision(r, conf, ctx, engine)       \
    do {                                                                    \
        ngx_log_debug6(NGX_LOG_DEBUG_HTTP,                                  \
            (r)->connection->log, 0,                                        \
            "markdown: outcome=- stage=eligibility "                       \
            "reason=- event=streaming_path_selection "                    \
            "engine=%s committed=0 fallback_available=1 "                 \
            "selection_reason=%s content_type=%V "                         \
            "content_length_known=%d chunked=%d "                          \
            "error_policy=%s",                                             \
            (engine),                                                       \
            ngx_http_markdown_stream_reason_str((ctx)->streaming.reason),   \
            &(r)->headers_out.content_type,                                 \
            ((r)->headers_out.content_length_n >= 0) ? 1 : 0,               \
            ((r)->headers_out.content_length_n < 0) ? 1 : 0,                \
            ((conf)->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS)           \
                ? "pass" : "reject");                                      \
    } while (0)
#endif /* MARKDOWN_STREAMING_ENABLED */


static void
ngx_http_markdown_establish_preaccess_bypass(
    ngx_http_markdown_ctx_t *ctx, ngx_flag_t filter_enabled,
    const ngx_http_markdown_effective_conf_t *eff, ngx_flag_t failopen)
{
    if (ctx == NULL) {
        return;
    }

    if (eff != NULL) {
        ctx->effective_conf_storage = *eff;
        ctx->effective_conf = &ctx->effective_conf_storage;
    }
    ctx->filter_enabled = filter_enabled;
    ctx->eligible = 0;
    if (failopen) {
        ctx->error.last_category = NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
        ctx->error.has_category = 1;
    }
    ctx->lifecycle.bypass = 1;
    ctx->fullbuffer.failopen_delivery_pending = failopen;
}

static ngx_int_t
ngx_http_markdown_handle_preaccess_failure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    const ngx_str_t  *error_category;

    error_category = ngx_http_markdown_reason_from_error_category(
        NGX_HTTP_MARKDOWN_ERROR_SYSTEM, r->connection->log);
    if (ctx != NULL) {
        ctx->eligible = 0;
        ctx->error.last_category = NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
        ctx->error.has_category = 1;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);

    if (ngx_http_markdown_effective_error_policy(eff, conf)
        == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
    {
        ngx_http_markdown_log_decision_with_category(
            r, conf, eff,
            ngx_http_markdown_reason_failed_closed(), error_category);
        return (ngx_int_t) ngx_http_markdown_effective_error_status(
            eff, conf);
    }

    ngx_http_markdown_log_decision_with_category(
        r, conf, eff,
        ngx_http_markdown_reason_failed_open(), error_category);
    return NGX_DECLINED;
}

/*
 * Decide whether this pass may adopt orphaned validators and, for a main
 * request without a context, perform the bounded atomic adoption.
 */
static ngx_int_t
ngx_http_markdown_prepare_preaccess_adoption(
    ngx_http_request_t *r,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_ctx_t *ctx,
    ngx_flag_t *adopt_orphans,
    ngx_http_markdown_conditional_ownership_t *ownership)
{
    size_t  scan_limit;

    *adopt_orphans = r->parent == NULL && ctx == NULL;
    ngx_memzero(ownership, sizeof(*ownership));
    if (!*adopt_orphans) {
        return NGX_OK;
    }

    scan_limit = eff != NULL ? eff->streaming_buffer : 0;
    if (scan_limit == NGX_CONF_UNSET_SIZE || scan_limit == 0) {
        scan_limit = NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT;
    }

    /*
     * Internal redirects clear the module context but leave suppressed
     * request headers behind.  Only the main request may re-adopt them:
     * subrequests share the parent's headers_in and must not un-suppress the
     * parent's validators mid-flight.
     */
    return ngx_http_markdown_adopt_orphan_conditional_headers(
        r, scan_limit, ownership);
}

/* Snapshot dynamic configuration before any preaccess state is adopted. */
static void
ngx_http_markdown_prepare_preaccess_effective_conf(
    const ngx_http_markdown_conf_t *conf,
    ngx_http_markdown_effective_conf_t *eff)
{
    ngx_http_markdown_dynconf_snapshot_t  snap_copy;

    snap_copy = ngx_http_markdown_dynconf_watcher.active_snapshot;
    ngx_memzero(eff, sizeof(*eff));
    ngx_http_markdown_build_effective_conf(
        eff,
        conf->advanced.dynconf_enabled == 1 ? &snap_copy : NULL,
        conf);
}

/* Evaluate preaccess eligibility against the already captured snapshot. */
static ngx_flag_t
ngx_http_markdown_prepare_preaccess_eligibility(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    ngx_flag_t *filter_enabled)
{
    ngx_uint_t  accept_reason;

    *filter_enabled = ngx_http_markdown_is_enabled(r, conf, eff);
    accept_reason = 0;
    if (!*filter_enabled) {
        return 0;
    }

    return ngx_http_markdown_should_convert(
        r, conf, &accept_reason);
}

/*
 * Route an orphan-adoption failure through the configured error policy after
 * restoring any live context-owned validators.
 */
static ngx_int_t
ngx_http_markdown_handle_preaccess_adoption_failure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    ngx_int_t  failure_rc;

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                  "markdown: conditional validator adoption "
                  "exceeded the configured request buffer limit");
    /* Restore captured validators before exposing a uniform pass-through
     * state to the error handlers or durable bypass. */
    ngx_http_markdown_restore_conditional_request(r, ctx);
    failure_rc = ngx_http_markdown_handle_preaccess_failure(
        r, ctx, conf, eff);
    if (failure_rc != NGX_DECLINED) {
        return failure_rc;
    }

    ngx_http_markdown_publish_durable_bypass(
        r, NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER);
    return NGX_DECLINED;
}

/*
 * Allocate and register the preaccess context.  On allocation failure,
 * return the configured reject status or publish the fail-open bypass.
 */
static ngx_int_t
ngx_http_markdown_create_preaccess_context(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t **ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    ngx_int_t  failure_rc;

    if (*ctx != NULL) {
        return NGX_OK;
    }

    *ctx = ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_ctx_t));
    if (*ctx != NULL
        && ngx_http_markdown_register_fullbuffer_cleanup(r, *ctx) == NGX_OK)
    {
        return NGX_OK;
    }

    ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                  "markdown: conditional validator context "
                  "allocation failed");
    failure_rc = ngx_http_markdown_handle_preaccess_failure(
        r, *ctx, conf, eff);
    if (failure_rc != NGX_DECLINED) {
        return failure_rc;
    }

    ngx_http_markdown_publish_durable_bypass(
        r, NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER);
    return NGX_DECLINED;
}

/*
 * Capture client validators before proxy, cache, or content handlers can
 * satisfy them against the upstream representation.  The handler only
 * prepares state; the header filter remains responsible for response
 * eligibility and representation selection.
 */
static ngx_int_t
ngx_http_markdown_preaccess_handler(ngx_http_request_t *r)
{
    const ngx_http_markdown_conf_t  *conf;
    ngx_http_markdown_ctx_t         *ctx;
    ngx_http_markdown_effective_conf_t    eff;
    ngx_flag_t                       filter_enabled;
    ngx_flag_t                       adopt_orphans;
    ngx_http_markdown_conditional_ownership_t ownership;
    ngx_int_t                         adoption_rc;
    ngx_int_t                         context_rc;

    ctx = ngx_http_get_module_ctx(r, ngx_http_markdown_filter_module);
    if (ngx_http_markdown_durable_bypass_kind(ctx)
        != NGX_HTTP_MARKDOWN_DURABLE_BYPASS_NONE)
    {
        return NGX_DECLINED;
    }
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    if (conf == NULL) {
        ngx_http_markdown_restore_conditional_request(r, ctx);
        return NGX_DECLINED;
    }

    if (ctx != NULL && ctx->lifecycle.header_filter_initialized) {
        return NGX_DECLINED;
    }

    ngx_http_markdown_prepare_preaccess_effective_conf(conf, &eff);
    adoption_rc = ngx_http_markdown_prepare_preaccess_adoption(
        r, &eff, ctx, &adopt_orphans, &ownership);

    if ((r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD)) == 0) {
        ngx_http_markdown_restore_conditional_request(r, ctx);
        return NGX_DECLINED;
    }

    if (!ngx_http_markdown_prepare_preaccess_eligibility(
            r, conf, &eff, &filter_enabled))
    {
        ngx_http_markdown_restore_conditional_request(r, ctx);
        return NGX_DECLINED;
    }

    if (adoption_rc != NGX_OK) {
        return ngx_http_markdown_handle_preaccess_adoption_failure(
            r, ctx, conf, &eff);
    }

    if (ctx == NULL && !ngx_http_markdown_has_conditional_request(r)) {
        /* A subrequest with no visible conditional validator is a plain
         * representation fetch (for example an SSI fragment include).  It
         * must be allowed through the normal conversion path: bypassing it
         * here would silently return the unconverted source fragment.
         * Only subrequests that carry a conditional validator need the
         * durable bypass, and they are handled after the capture attempt
         * below. */
        return NGX_DECLINED;
    }

    context_rc = ngx_http_markdown_create_preaccess_context(
        r, &ctx, conf, &eff);
    if (context_rc != NGX_OK) {
        return context_rc;
    }

    if (adopt_orphans && ownership.entry_count != 0) {
        ctx->conditional.ownership = ownership;
    }

    return ngx_http_markdown_preaccess_capture(
        r, ctx, conf, &eff, filter_enabled);
}

/* Complete the preaccess validator capture and route the request. */
static ngx_int_t
ngx_http_markdown_preaccess_capture(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    ngx_flag_t filter_enabled)
{
    ngx_int_t  capture_rc;
    ngx_int_t  failure_rc;

    capture_rc = ngx_http_markdown_capture_conditional_request(r, ctx);
    if (capture_rc == NGX_ERROR) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                      "markdown: conditional validator capture failed");
        failure_rc = ngx_http_markdown_handle_preaccess_failure(
            r, ctx, conf, eff);
        if (failure_rc != NGX_DECLINED) {
            return failure_rc;
        }
        /* Establish a durable request-scoped bypass before the header filter
         * can initialize the context and overwrite the eligibility decision.
         * The cached filter decision lets the body filter reach its existing
         * ineligible pass-through path without preparing conversion state. */
        ngx_http_markdown_establish_preaccess_bypass(
            ctx, filter_enabled, eff, 1);
        r->ctx[ngx_http_markdown_filter_module.ctx_index] = ctx;
        return NGX_DECLINED;
    }
    if (capture_rc != NGX_OK) {
        /*
         * A subrequest shares headers_in with its parent.  When it carries
         * a conditional validator it must not use the parent's validators
         * to select a converted representation, so make that bypass
         * decision durable through the header and body filters.  A
         * subrequest without a conditional validator (for example an SSI
         * fragment include) keeps the context and continues into the
         * normal conversion path.
         */
        if (r->parent != NULL
            && ngx_http_markdown_has_conditional_request(r))
        {
            ngx_http_markdown_establish_preaccess_bypass(
                ctx, filter_enabled, eff, 0);
        }
        r->ctx[ngx_http_markdown_filter_module.ctx_index] = ctx;
        return NGX_DECLINED;
    }

    r->ctx[ngx_http_markdown_filter_module.ctx_index] = ctx;
    return NGX_DECLINED;
}

/* Handle a Vary: Accept insertion failure during accept negotiation. */
static ngx_int_t
ngx_http_markdown_handle_vary_accept_failure(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    ngx_int_t vary_rc, ngx_int_t *rc)
{
    if (ctx != NULL) {
        ctx->eligible = 0;
        ngx_http_markdown_restore_conditional_request(r, ctx);
    }
    *rc = vary_rc;
    return 1;
}

/* Forward headers after a precheck has selected source passthrough. */
static ngx_int_t
ngx_http_markdown_forward_prechecked_headers(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    if (ctx != NULL) {
        ctx->eligible = 0;
        ngx_http_markdown_restore_conditional_request(r, ctx);
    }

    rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
    if (ctx != NULL
        && (rc == NGX_AGAIN || rc == NGX_OK || rc == NGX_DONE))
    {
        ctx->headers_forwarded = 1;
        ngx_http_markdown_settle_buffered_failopen_delivery(ctx, conf, rc);
    }
    return rc;
}

static ngx_flag_t
ngx_http_markdown_header_precheck(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *early_eff,
    ngx_flag_t filter_enabled, ngx_http_markdown_ctx_t *ctx,
    ngx_int_t *rc)
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
        ngx_http_markdown_log_header_terminal_decision(
            r, conf, early_eff, "disabled");
        *rc = ngx_http_markdown_forward_prechecked_headers(r, ctx, conf);
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
        ngx_http_markdown_log_header_terminal_decision(
            r, conf, early_eff, "not_eligible");
        *rc = ngx_http_markdown_forward_prechecked_headers(r, ctx, conf);
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
        ngx_http_markdown_log_header_terminal_decision(
            r, conf, early_eff, "not_eligible");
        *rc = ngx_http_markdown_forward_prechecked_headers(r, ctx, conf);
        return 1;
    }

    if (ngx_http_markdown_has_no_transform(r)) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: Cache-Control: no-transform present, "
                      "bypassing conversion");
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.no_transform);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_decision(r, conf, early_eff,
            ngx_http_markdown_reason_bypass_no_transform());
        ngx_http_markdown_log_header_terminal_decision(
            r, conf, early_eff, "bypass_no_transform");
        *rc = ngx_http_markdown_forward_prechecked_headers(r, ctx, conf);
        return 1;
    }

    if (!ngx_http_markdown_should_convert(r, conf, &accept_reason)) {
        if (ngx_http_markdown_accept_result_varies(accept_reason)) {
            ngx_int_t  vary_rc;

            vary_rc = ngx_http_markdown_add_vary_accept(r);
            if (vary_rc != NGX_OK) {
                return ngx_http_markdown_handle_vary_accept_failure(
                    r, ctx, vary_rc, rc);
            }
        }
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.accept);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_accept_skip(r, conf, early_eff,
            accept_reason);
        *rc = ngx_http_markdown_forward_prechecked_headers(r, ctx, conf);
        return 1;
    }

    return 0;
}


/**
 * Reserves worker capacity for request conversion.
 *
 * Applies the configured error policy when capacity is unavailable or
 * reservation setup fails.
 *
 * @param r Request being processed.
 * @param ctx Request-specific Markdown processing state.
 * @param conf Markdown filter configuration.
 * @return NGX_OK when capacity is reserved; otherwise, the response status
 *         or filter result to return from the header filter.
 */
static ngx_int_t
ngx_http_markdown_check_inflight(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  inflight_rc;
    ngx_int_t  rc;

    inflight_rc = ngx_http_markdown_inflight_try_increment(
        r, conf, ctx);

    if (inflight_rc == NGX_DECLINED) {
        /* Overloaded — apply error policy */
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);

        if (ngx_http_markdown_effective_error_policy(
                ctx->effective_conf, conf)
            == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
        {
            ngx_http_markdown_mark_header_reject(ctx);
            ngx_log_error(NGX_LOG_WARN,
                r->connection->log, 0,
                "markdown: inflight overload, "
                "rejecting (fail-closed)");
            ngx_http_markdown_log_decision(
                r, conf, ctx->effective_conf,
                ngx_http_markdown_reason_overload());
            return ngx_http_filter_finalize_request(
                r, &ngx_http_markdown_filter_module,
                (ngx_int_t) ngx_http_markdown_effective_error_status(
                    ctx->effective_conf, conf));
        }

        /* fail-open: pass through original response */
        ctx->eligible = 0;

        ngx_log_error(NGX_LOG_WARN,
            r->connection->log, 0,
            "markdown: inflight overload, "
            "returning original content "
            "(fail-open)");
        ngx_http_markdown_log_decision(
            r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_overload());
        ngx_http_markdown_restore_conditional_request(r, ctx);
        rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
        /*
         * Canonical NGINX model: header-chain NGX_AGAIN means the write
         * filter queued the header block — the headers are accepted.
         * Publish the delivery latch so the pass-through body path never
         * re-enters the header chain (intermediate filters are not
         * idempotent).
         */
        /* Rule 38/23: failopen_count is a delivery counter, not a decision counter. */
        if (rc == NGX_AGAIN) {
            ctx->headers_forwarded = 1;
            ctx->fullbuffer.failopen_delivery_pending = 1;
            return rc;
        }
        if (rc == NGX_OK || rc == NGX_DONE) {
            ctx->headers_forwarded = 1;
            ngx_http_markdown_metric_inc_failopen(
                ctx->effective_conf, conf);
        }
        return rc;
    }

    if (inflight_rc == NGX_ERROR) {
        /* Cleanup alloc failed — treat as system error */
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);

        if (ngx_http_markdown_effective_error_policy(
                ctx->effective_conf, conf)
            == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
        {
            ngx_http_markdown_mark_header_reject(ctx);
            ngx_http_markdown_log_decision(
                r, conf, ctx->effective_conf,
                ngx_http_markdown_reason_failed_closed());
            return ngx_http_filter_finalize_request(
                r, &ngx_http_markdown_filter_module,
                (ngx_int_t) ngx_http_markdown_effective_error_status(
                    ctx->effective_conf, conf));
        }

        ctx->eligible = 0;
        ngx_http_markdown_log_decision(
            r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_failed_open());
        ngx_http_markdown_restore_conditional_request(r, ctx);
        rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
        /*
         * Canonical NGINX model: header-chain NGX_AGAIN means the write
         * filter queued the header block — the headers are accepted.
         * Publish the delivery latch so the pass-through body path never
         * re-enters the header chain (intermediate filters are not
         * idempotent).
         */
        /* Rule 38/23: failopen_count is a delivery counter, not a decision counter. */
        if (rc == NGX_AGAIN) {
            ctx->headers_forwarded = 1;
            ctx->fullbuffer.failopen_delivery_pending = 1;
            return rc;
        }
        if (rc == NGX_OK || rc == NGX_DONE) {
            ctx->headers_forwarded = 1;
            ngx_http_markdown_metric_inc_failopen(
                ctx->effective_conf, conf);
        }
        return rc;
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

    /* Multi-layer chains (2 or 3) decode via bounded full-buffer only
     * (Requirement 12.6); streaming decompression is single-layer only. */
    if (ctx->decompression.layer_count > 1) {
        ctx->processing_path = NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
        ctx->streaming.reason = NGX_HTTP_MARKDOWN_STREAM_REASON_COMPRESSED;
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.engine_choice.full_buffer);

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
            "markdown: multi-layer encoding chain (%ui layers) "
            "routed to full-buffer decode", ctx->decompression.layer_count);
        ngx_http_markdown_log_streaming_decision(
            r, conf, ctx, "full_buffer");
        ngx_http_markdown_log_event(
            r, conf, ctx->effective_conf, "eligibility",
            "compressed_passthrough");

        return 1;
    }

    if (ngx_http_markdown_decompression_is_streamable(
            (unsigned) ctx->decompression.type))
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            perf.decompression_streaming_total);
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
            "markdown: streaming decompression selected for encoding %d",
            ctx->decompression.type);
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
    ngx_http_markdown_log_event(
        r, conf, ctx->effective_conf, "eligibility",
        "compressed_passthrough");

    return 1;
}
#endif


/*
 * Bundle of the four string fields describing a buffered conversion's
 * terminal decision, used so the log helper stays below the parameter
 * limit.  All members are pre-formatted constant string pointers.
 */
typedef struct {
    const char   *conditional_result;
    const char   *conversion_status;
    const char   *reason_code;
    const char   *stage;
} ngx_http_markdown_buffered_decision_t;


static void
ngx_http_markdown_log_buffered_decision_path(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_buffered_decision_t *decision,
    ngx_msec_t duration_ms)
{
    ngx_http_markdown_decision_path_t  dp;

    dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_CONVERT;
    dp.conditional_result = decision->conditional_result;
    dp.conversion_status = decision->conversion_status;
    dp.reason_code = decision->reason_code;
    dp.stage = decision->stage;
    dp.error_category = ctx->error.has_category
        ? (const char *) ngx_http_markdown_error_category_string(
              ctx->error.last_category)->data : NULL;
    dp.duration_ms = duration_ms;
    ngx_http_markdown_log_terminal_decision_path(
        r, conf, ctx->effective_conf, ctx, &dp);
}

/**
 * Handles a malformed Content-Encoding chain according to the configured
 * error policy.
 *
 * @param r Request being processed.
 * @param ctx Per-request module context.
 * @param conf Module location configuration.
 * @param reason Decision reason to log, or the canonical
 *               encoding-header-invalid reason when NULL.
 * @returns The filter-finalize result for fail-closed handling; otherwise, the
 *          downstream header-filter result.
 */
static ngx_int_t
ngx_http_markdown_handle_encoding_header_invalid(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_str_t *reason)
{
    ngx_int_t  rc;

    ctx->eligible = 0;
    ctx->error.last_category = NGX_HTTP_MARKDOWN_ERROR_CONVERSION;
    ctx->error.has_category = 1;

    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);

    if (reason == NULL) {
        reason = ngx_http_markdown_reason_encoding_header_invalid();
    }
    ngx_http_markdown_log_decision(r, conf, ctx->effective_conf, reason);

    if (ngx_http_markdown_effective_error_policy(
            ctx->effective_conf, conf)
        == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        ngx_http_markdown_mark_header_reject(ctx);
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown: malformed Content-Encoding "
                      "chain, rejecting with status %ui",
                      ngx_http_markdown_effective_error_status(
                          ctx->effective_conf, conf));
        return ngx_http_filter_finalize_request(
            r, &ngx_http_markdown_filter_module,
            (ngx_int_t) ngx_http_markdown_effective_error_status(
                ctx->effective_conf, conf));
    }

    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                  "markdown: malformed Content-Encoding "
                  "chain, returning original encoded content");
    ngx_http_markdown_restore_conditional_request(r, ctx);
    rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
    /*
     * Canonical NGINX model: header-chain NGX_AGAIN means the write
     * filter queued the header block — the headers are accepted.
     * Publish the delivery latch so the pass-through body path never
     * re-enters the header chain (intermediate filters are not
     * idempotent).
     */
    /* Rule 38/23: failopen_count is a delivery counter, not a decision counter. */
    if (rc == NGX_AGAIN) {
        ctx->headers_forwarded = 1;
        ctx->fullbuffer.failopen_delivery_pending = 1;
        return rc;
    }
    if (rc == NGX_OK || rc == NGX_DONE) {
        ctx->headers_forwarded = 1;
        ngx_http_markdown_metric_inc_failopen(
            ctx->effective_conf, conf);
    }
    return rc;
}

/**
 * Processes the response's Content-Encoding chain before selecting a conversion path.
 *
 * Valid chains with supported compression layers enable decompression; identity-only
 * chains continue without decompression. Malformed or unsupported chains, collection
 * failures, and disabled automatic decompression follow the configured policy.
 *
 * @param rc Receives the header-filter result when processing terminates.
 * @return Nonzero when the caller must stop processing and return `*rc`; zero when
 *         normal path selection may continue.
 */
static ngx_flag_t
ngx_http_markdown_handle_header_compression(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_int_t *rc)
{
    ngx_str_t  combined;

    if (ctx->decompression.type == NGX_HTTP_MARKDOWN_COMPRESSION_NONE
        && r->headers_out.content_encoding == NULL)
    {
        return 0;
    }

    /* Parse the complete chain grammar during precommit, before any
     * decoding or error-policy dispatch (Requirement 12.7). */
    {
        ngx_int_t  collect_rc;
        u_char  classification;

        collect_rc = ngx_http_markdown_collect_content_encoding(r, &combined);
        if (collect_rc == NGX_ERROR) {
            *rc = ngx_http_markdown_handle_encoding_collection_failure(
                r, ctx, conf, ctx->effective_conf);
            return 1;
        }
        if (collect_rc != NGX_OK) {
            goto encoding_policy;
        }

        classification = ngx_http_markdown_parse_encoding_chain_ffi(
            r, ctx, &combined);

        if (classification == ENCODING_CHAIN_MALFORMED) {
            *rc = ngx_http_markdown_handle_encoding_header_invalid(
                r, ctx, conf, ngx_http_markdown_reason_encoding_header_invalid());
            return 1;
        }

        if (classification == ENCODING_CHAIN_UNKNOWN_TOKEN
            || classification == ENCODING_CHAIN_DEPTH_EXCEEDED)
        {
            *rc = ngx_http_markdown_handle_encoding_header_invalid(
                r, ctx, conf,
                ngx_http_markdown_reason_decompression_format_error());
            return 1;
        }

        if (classification != ENCODING_CHAIN_VALID) {
            /* FFI contract failure: route like MALFORMED
             * (decision governed by on_error policy). */
            *rc = ngx_http_markdown_handle_encoding_header_invalid(
                r, ctx, conf, ngx_http_markdown_reason_encoding_header_invalid());
            return 1;
        }

        /* A valid chain with only identity layers performs no decoder work:
         * conversion proceeds without decompression. */
        if (ctx->decompression.layer_count == 0) {
            ctx->decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
            return 0;
        }
    }

encoding_policy:
    if (!conf->decompress.auto_decompress) {
        ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
                     "markdown: Content-Encoding present "
                     "(type=%d) but auto_decompress is off, "
                     "passing through original content",
                     ctx->decompression.type);
        ctx->eligible = 0;
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.compression_passthrough);
        ngx_http_markdown_log_event(
            r, conf, ctx->effective_conf, "eligibility",
            "compressed_passthrough");
        ngx_http_markdown_restore_conditional_request(r, ctx);
        *rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
        /*
         * Canonical NGINX model: header-chain NGX_AGAIN means the write
         * filter queued the header block — the headers are accepted.
         * Publish the delivery latch so the pass-through body path never
         * re-enters the header chain (intermediate filters are not
         * idempotent).
         */
        if (*rc == NGX_AGAIN) {
            ctx->headers_forwarded = 1;
            ctx->fullbuffer.failopen_delivery_pending = 1;
            return 1;
        }
        if (*rc == NGX_OK || *rc == NGX_DONE) {
            ctx->headers_forwarded = 1;
            ngx_http_markdown_metric_inc_failopen(
                ctx->effective_conf, conf);
        }
        return 1;
    }

    ctx->decompression.needed = 1;
    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: decompression detected "
                  "compression type: %d, layers: %ui",
                  ctx->decompression.type,
                  ctx->decompression.layer_count);
    return 0;
}

/* Select streaming or full-buffer processing after compression. */
static void
ngx_http_markdown_select_header_path(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
#ifdef MARKDOWN_STREAMING_ENABLED
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.selection.candidate_total);

    ngx_http_markdown_path_selection_t selection =
        ngx_http_markdown_select_processing_path(
            r, conf, ctx->effective_conf);
    ctx->processing_path = selection.path;
    ctx->streaming.reason = selection.reason;

    if (ngx_http_markdown_route_streaming_compression(r, ctx, conf)) {
        goto path_selected;
    }

    if (ctx->processing_path == NGX_HTTP_MARKDOWN_PATH_STREAMING) {
        ctx->streaming.reason = NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE;
        ctx->stream_sm.state = NGX_HTTP_MD_STATE_STREAMING_CANDIDATE;
        NGX_HTTP_MARKDOWN_METRIC_INC(streaming.engine_choice.streaming);
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.selection.true_streaming_selected_total);
        ngx_http_markdown_log_streaming_decision(
            r, conf, ctx, "streaming");
        ngx_http_markdown_log_event(
            r, conf, ctx->effective_conf, "eligibility",
            "engine_streaming");
        goto path_selected;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.engine_choice.full_buffer);
    ngx_http_markdown_log_streaming_decision(
        r, conf, ctx, "full_buffer");
#endif

#ifdef MARKDOWN_STREAMING_ENABLED
path_selected:
    ;
#endif
}

static void
ngx_http_markdown_request_cleanup(void *data)
{
    ngx_http_markdown_ctx_t  *ctx;

    ctx = data;
    if (ctx == NULL) {
        return;
    }

    ngx_http_markdown_fullbuffer_cleanup(data);
    ctx->fullbuffer.failopen_delivery_pending = 0;
}

static ngx_int_t
ngx_http_markdown_register_fullbuffer_cleanup(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_pool_cleanup_t  *cleanup;

    cleanup = ngx_pool_cleanup_add(r->pool, 0);
    if (cleanup == NULL) {
        return NGX_ERROR;
    }

    cleanup->handler = ngx_http_markdown_request_cleanup;
    cleanup->data = ctx;
    return NGX_OK;
}

static void
ngx_http_markdown_record_path_hit(const ngx_http_markdown_ctx_t *ctx)
{
    if (!ctx->eligible) {
        return;
    }

#ifdef MARKDOWN_STREAMING_ENABLED
    if (ctx->processing_path == NGX_HTTP_MARKDOWN_PATH_STREAMING) {
        NGX_HTTP_MARKDOWN_METRIC_INC(path_hits.streaming);
        return;
    }
#endif
    NGX_HTTP_MARKDOWN_METRIC_INC(path_hits.fullbuffer);
}

/**
 * Determine whether the response should be converted and, if eligible,
 * initialize a per-request Markdown conversion context for body buffering.
 *
 * When conversion is eligible this function allocates and installs a
 * ngx_http_markdown_ctx_t on the request, detects/initializes decompression
 * state (honoring the auto_decompress configuration), selects a processing
 * path (full-buffer or streaming) based on configuration and headers,
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
/*
 * Handle header-filter re-entry.  NGINX core does not re-enter the
 * header chain once header_sent is set, but a defensive short-circuit
 * keeps a hypothetical re-entry from forwarding headers twice.  The
 * request context created by the first pass is reused (a second context
 * would duplicate cleanup hooks and reset phase latches).
 *
 * Returns:
 *   NGX_OK           - re-entry with headers already forwarded (no-op)
 *   NGX_DECLINED     - first pass or re-entry before forwarding; the caller
 *                      must preserve the existing context and decide whether
 *                      header emission is ready
 */
static ngx_int_t
ngx_http_markdown_header_filter_handle_reentry(const ngx_http_request_t *r)
{
    const ngx_http_markdown_ctx_t  *ctx;

    ctx = ngx_http_get_module_ctx(r, ngx_http_markdown_filter_module);
    if (ctx == NULL) {
        return NGX_DECLINED;
    }
    if (!ctx->lifecycle.header_filter_initialized) {
        return NGX_DECLINED;
    }
    if (ctx->headers_forwarded) {
        return NGX_OK;
    }
    return NGX_DECLINED;
}


/*
 * Resume a deferred header-filter re-entry only after body processing has
 * prepared the final representation.  A re-entry before conversion is ready
 * is an invariant violation: forwarding there would expose upstream headers
 * before the module has applied its representation changes.
 */
static ngx_int_t
ngx_http_markdown_resume_header_filter_reentry(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    if (ctx->error.header_reject) {
        if (ctx->headers_forwarded) {
            return NGX_OK;
        }

        rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
        if (rc == NGX_AGAIN || rc == NGX_OK || rc == NGX_DONE) {
            ctx->headers_forwarded = 1;
        }
        return rc;
    }

    if (!ctx->conversion.attempted) {
        ngx_log_error(NGX_LOG_ALERT, r->connection->log, 0,
                      "markdown: header-filter re-entry before "
                      "conversion state was ready");
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
    if (rc == NGX_AGAIN || rc == NGX_OK || rc == NGX_DONE) {
        ctx->headers_forwarded = 1;
    }

    return rc;
}


static ngx_int_t
ngx_http_markdown_handle_durable_bypass(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_http_markdown_durable_bypass_kind_t  kind;
    ngx_int_t                                rc;

    kind = ngx_http_markdown_durable_bypass_kind(ctx);
    if (kind != NGX_HTTP_MARKDOWN_DURABLE_BYPASS_NONE) {
        if (kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY
            || kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_COMPLETED
            || kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_BODY
            || kind == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_COMPLETED)
        {
            return NGX_OK;
        }

        rc = ngx_http_markdown_next_header_filter_with_auth(r, conf);
        ngx_http_markdown_settle_durable_bypass_delivery(r, kind, rc);
        return rc;
    }

    if (ctx == NULL || !ctx->lifecycle.bypass) {
        return NGX_DECLINED;
    }

    if (ctx->headers_forwarded) {
        return NGX_OK;
    }

    /* Preaccess already made and logged the fail-open decision.  Forward
     * the source representation without initializing conversion state. */
    return ngx_http_markdown_forward_prechecked_headers(r, ctx, conf);
}

/* Allocate and initialize the request context used by the header filter. */
static ngx_int_t
ngx_http_markdown_create_header_context(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    ngx_flag_t filter_enabled,
    ngx_http_markdown_ctx_t **ctx_out)
{
    ngx_http_markdown_ctx_t  *ctx;

    *ctx_out = NULL;
    ctx = ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_ctx_t));
    if (ctx == NULL) {
        return ngx_http_markdown_handle_ctx_alloc_failure(r, conf, eff);
    }

    if (ngx_http_markdown_register_fullbuffer_cleanup(r, ctx) != NGX_OK) {
        return ngx_http_markdown_handle_ctx_alloc_failure(r, conf, eff);
    }

    ngx_http_markdown_init_ctx(r, ctx, filter_enabled);
    *ctx_out = ctx;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_header_filter(ngx_http_request_t *r)
{
    ngx_http_markdown_ctx_t         *ctx;
    const ngx_http_markdown_conf_t  *conf;
    ngx_flag_t                       filter_enabled;
    ngx_int_t                        precheck_rc;
    ngx_http_markdown_dynconf_snapshot_t  snap_copy;
    ngx_http_markdown_effective_conf_t    early_eff;

    /* Get module configuration */
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    ctx = ngx_http_get_module_ctx(r, ngx_http_markdown_filter_module);

    if (conf == NULL) {
        ngx_http_markdown_restore_conditional_request(r, ctx);
        /* Module not configured, pass through */
        return ngx_http_markdown_next_header_filter_with_auth(r, NULL);
    }

    precheck_rc = ngx_http_markdown_handle_durable_bypass(r, ctx, conf);
    if (precheck_rc != NGX_DECLINED) {
        return precheck_rc;
    }

    /* Header-filter re-entry must reuse the request context created by the
     * first pass; allocating a second context would duplicate cleanup hooks
     * and reset the request's phase latches. */
    precheck_rc = ngx_http_markdown_header_filter_handle_reentry(r);
    if (precheck_rc != NGX_DECLINED) {
        return precheck_rc;
    }
    if (ctx != NULL && ctx->lifecycle.header_filter_initialized) {
        return ngx_http_markdown_resume_header_filter_reentry(r, ctx, conf);
    }
    /* Copy the snapshot once; dynconf-disabled locations use static conf. */
    snap_copy = ngx_http_markdown_dynconf_watcher.active_snapshot;
    ngx_memzero(&early_eff, sizeof(early_eff));
    ngx_http_markdown_build_effective_conf(
        &early_eff,
        conf->advanced.dynconf_enabled == 1 ? &snap_copy : NULL,
        conf);

    /*
     * Resolve markdown_filter once in header phase and cache the result in
     * request context. Body phase must reuse this decision to avoid
     * header/body inconsistencies for dynamic variables.
     */
    filter_enabled = ngx_http_markdown_is_enabled(r, conf, &early_eff);
    if (ctx != NULL && !ctx->lifecycle.header_filter_initialized) {
        ngx_http_markdown_init_ctx(r, ctx, filter_enabled);
    }

    if (ngx_http_markdown_header_precheck(
            r, conf, &early_eff, filter_enabled, ctx, &precheck_rc))
    {
        return precheck_rc;
    }

    if (ctx == NULL) {
        precheck_rc = ngx_http_markdown_create_header_context(
            r, conf, &early_eff, filter_enabled, &ctx);
        if (ctx == NULL) {
            return precheck_rc;
        }
    }

    /* Bind the same request-local snapshot used for the header decision. */
    ngx_http_markdown_bind_request_context_snapshot(
        r, ctx, &snap_copy, &early_eff, conf);

    /* Set context for this request */
    r->ctx[ngx_http_markdown_filter_module.ctx_index] = ctx;

    /* Collect and parse the complete Content-Encoding chain before path selection. */
    if (ngx_http_markdown_handle_header_compression(
            r, ctx, conf, &precheck_rc))
    {
        return precheck_rc;
    }

    ngx_http_markdown_select_header_path(r, ctx, conf);

    ngx_int_t  inflight_rc;

    inflight_rc = ngx_http_markdown_check_inflight(r, ctx, conf);
    if (inflight_rc != NGX_OK) {
        return inflight_rc;
    }

    ngx_http_markdown_record_path_hit(ctx);

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
     * conversion_attempted and conversions_attempted metric are
     * already set by the body filter before decompression.
     */
    elapsed_ms = 0;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: buffered complete response, size: %uz bytes",
                  ctx->buffer.size);

    rc = ngx_http_markdown_resolve_conditional_result(
        r, ctx, conf, &result, &elapsed_ms, &has_result);
    if (rc == NGX_DONE) {
        /* send_304 emitted the terminal headers; do not continue the filter
         * chain after the terminal return. */
        return NGX_DONE;
    }
    if (rc == NGX_AGAIN) {
        /* The 304 header chain owns the pending send; resume it from the
         * body-filter entry point without treating backpressure as failure. */
        return NGX_AGAIN;
    }
    if (rc != NGX_OK) {
        /* Conditional processing failed — log failure outcome */
        ngx_http_markdown_log_failure_decision(r, ctx, conf);
        ngx_http_markdown_log_buffered_decision_path(
            r, ctx, conf,
            &((const ngx_http_markdown_buffered_decision_t){
                NGX_HTTP_MARKDOWN_COND_PROCEED,
                NGX_HTTP_MARKDOWN_CONV_FAILED,
                ngx_http_markdown_effective_error_policy(
                    ctx->effective_conf, conf)
                    == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT
                    ? "failed_closed" : "failed_open",
                "conversion"}),
            elapsed_ms);

        /* subrequest: conversion terminated with an error — release the slot. */
        ngx_http_markdown_inflight_release(ctx);
        return rc;
    }

    if (!has_result) {
        rc = ngx_http_markdown_execute_conversion(r, ctx, conf, &result, &elapsed_ms);
        if (rc != NGX_OK) {
            /* Conversion failed — log failure outcome */
            ngx_http_markdown_log_failure_decision(r, ctx, conf);
            ngx_http_markdown_log_buffered_decision_path(
                r, ctx, conf,
                &((const ngx_http_markdown_buffered_decision_t){
                    NGX_HTTP_MARKDOWN_COND_PROCEED,
                    NGX_HTTP_MARKDOWN_CONV_FAILED,
                    ngx_http_markdown_effective_error_policy(
                        ctx->effective_conf, conf)
                        == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT
                        ? "failed_closed" : "failed_open",
                    "conversion"}),
                elapsed_ms);

            /* subrequest: conversion terminated with an error — release the slot. */
            ngx_http_markdown_inflight_release(ctx);
            return rc;
        }
    }

    /* subrequest: conversion work has finished (conditional result reused or
     * fresh conversion succeeded).  Release the inflight slot before
     * emitting output; the slot guards conversions in progress, not
     * downstream delivery.  For subrequests this frees the slot before
     * the shared parent pool is destroyed. */
    ngx_http_markdown_inflight_release(ctx);

    rc = ngx_http_markdown_send_conversion_output(
        r, ctx, conf, &result, elapsed_ms);
    if (rc == NGX_OK || rc == NGX_DONE) {
        /*
         * Record the converted terminal outcome only after the downstream
         * body filter accepts the complete chain. NGX_AGAIN remains pending
         * and is finalized by body-filter resume.
         */
        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_converted());
        ngx_http_markdown_log_buffered_decision_path(
            r, ctx, conf,
            &((const ngx_http_markdown_buffered_decision_t){
                NGX_HTTP_MARKDOWN_COND_PROCEED,
                NGX_HTTP_MARKDOWN_CONV_SUCCESS,
                "converted", "conversion"}),
            elapsed_ms);

    } else if (rc != NGX_AGAIN) {
        /*
         * Output emission failed after conversion succeeded.
         * Record the terminal failure decision so the request
         * does not rely solely on the earlier success recording
         * in ngx_http_markdown_execute_conversion().
         */
        ngx_http_markdown_record_buffered_delivery_failure(ctx);
        ngx_http_markdown_log_failure_decision(r, ctx, conf);
        ngx_http_markdown_log_buffered_decision_path(
            r, ctx, conf,
            &((const ngx_http_markdown_buffered_decision_t){
                NGX_HTTP_MARKDOWN_COND_PROCEED,
                NGX_HTTP_MARKDOWN_CONV_FAILED,
                ngx_http_markdown_effective_error_policy(
                    ctx->effective_conf, conf)
                    == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT
                    ? "failed_closed" : "failed_open",
                "delivery"}),
            elapsed_ms);

    }

    return rc;
}

/*
 * Handle HEAD request representation rewriting.
 * Returns NGX_OK on success, NGX_ERROR on failure, NGX_DECLINED to continue.
 */
static ngx_int_t
ngx_http_markdown_body_filter_handle_head(ngx_http_request_t *r,
                                           ngx_http_markdown_ctx_t *ctx,
                                           const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t rc;

    if (r->method == NGX_HTTP_HEAD && ctx->eligible) {
        rc = ngx_http_markdown_head_representation_headers(r);
        if (rc != NGX_OK) {
            if (rc == NGX_HTTP_MARKDOWN_HEADER_SNAPSHOT_RESTORE_FAILED) {
                ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                              "markdown: HEAD header snapshot rollback "
                              "failed; fail-open disabled, category=system");
                ngx_http_markdown_record_system_failure(ctx);
                ngx_http_markdown_log_decision_with_category(
                    r, conf, ctx->effective_conf,
                    ngx_http_markdown_reason_header_plan_apply_err(),
                    ngx_http_markdown_reason_from_error_category(
                        NGX_HTTP_MARKDOWN_ERROR_SYSTEM, r->connection->log));
                ngx_http_markdown_inflight_release(ctx);
                return ngx_http_filter_finalize_request(
                    r, &ngx_http_markdown_filter_module,
                    (ngx_int_t) ngx_http_markdown_effective_error_status(
                        ctx->effective_conf, conf));
            }
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                          "markdown: HEAD representation header "
                          "rewrite failed");
            return rc;
        }
        /* Release the inflight slot: the HEAD request performs no
         * conversion, so holding the slot for the whole header-only
         * request wastes max_inflight capacity. */
        ngx_http_markdown_inflight_release(ctx);
        /* Do not record a conversion bypass/failure: the HEAD request
         * is not a conversion attempt — it is a representation-header
         * only response.  Mark ineligible so the passthrough below
         * forwards the (rewritten) headers with an empty body. */
        ctx->eligible = 0;
        ctx->conversion.bypass_counted = 1;
        /* Validator restoration needs no caller-side surgery here:
         * head_representation_headers() already reset BOTH outgoing
         * Last-Modified mirrors (typed pointer plus numeric field), so
         * the shared forwarding helper recognizes the rewritten state
         * and skips restoring the preserved source mtime. */
    }
    return NGX_DECLINED;
}


/*
 * Pass-through path for non-eligible requests.
 * Handles header forwarding, body pass-through, and fail-open delivery settlement.
 */
static ngx_int_t
ngx_http_markdown_body_filter_pass_through(ngx_http_request_t *r, ngx_chain_t *in,
                                            ngx_http_markdown_ctx_t *ctx,
                                            const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t rc;

    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
    if (!ctx->conversion.bypass_counted && !ctx->error.has_category) {
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ctx->conversion.bypass_counted = 1;
    }
    rc = ngx_http_markdown_forward_headers(r, ctx);
    if (rc != NGX_OK && rc != NGX_AGAIN) {
        return rc;
    }
    rc = ngx_http_next_body_filter(r, in);
    ngx_http_markdown_settle_buffered_failopen_delivery(ctx, conf, rc);
    return rc;
}

static ngx_int_t
ngx_http_markdown_resume_pending_304(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    if (!ctx->conversion.attempted
        || !ctx->headers_forwarded
        || !ctx->conditional.captured
        || !ctx->conditional.suppressed
        || r->headers_out.status != NGX_HTTP_NOT_MODIFIED)
    {
        return NGX_DECLINED;
    }

    if (ctx->conditional.resume_completed) {
        /* The pending 304 header send already completed; later body-filter
         * entries must not re-enter the header send. */
        return NGX_DONE;
    }

    rc = ngx_http_send_header(r);
    if (rc == NGX_AGAIN) {
        return NGX_AGAIN;
    }
    if (rc == NGX_OK || rc == NGX_DONE) {
        ctx->conditional.resume_completed = 1;
        return NGX_DONE;
    }

    if (!ctx->error.has_category) {
        ngx_http_markdown_record_system_failure(ctx);
    }
    return rc;
}


/*
 * Main body filter logic after HEAD handling.
 * Returns NGX_OK, NGX_AGAIN, NGX_ERROR, or NGX_DECLINED to continue processing.
 */
static ngx_int_t
ngx_http_markdown_body_filter_main(ngx_http_request_t *r, ngx_chain_t *in,
                                    ngx_http_markdown_ctx_t *ctx,
                                    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t rc;

    rc = ngx_http_markdown_resume_pending_304(r, ctx);
    if (rc != NGX_DECLINED) {
        return rc;
    }

    /* Rule 1 / Rule 38: resume full-buffer pending chain. */
    if (ctx->fullbuffer.pending_has_data) {
        return ngx_http_markdown_body_filter_resume_pending(r, ctx);
    }

#ifdef MARKDOWN_STREAMING_ENABLED
    /* Drain pending streaming output after fail-open. */
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
        return ngx_http_markdown_body_filter_pass_through(r, in, ctx, conf);
    }

#ifdef MARKDOWN_STREAMING_ENABLED
    /* Streaming path: delegate to streaming body filter */
    if (ctx->processing_path == NGX_HTTP_MARKDOWN_PATH_STREAMING) {
        return ngx_http_markdown_streaming_body_filter(r, in);
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

    ctx->conversion.attempted = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);

    rc = ngx_http_markdown_body_filter_decompress_if_needed(r, ctx, conf);
    if (rc != NGX_OK) {
        return rc;
    }

    return ngx_http_markdown_body_filter_convert_and_output(r, ctx, conf);
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
    ngx_http_markdown_durable_bypass_kind_t  bypass_kind;
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

    bypass_kind = ngx_http_markdown_durable_bypass_kind(ctx);
    if (bypass_kind != NGX_HTTP_MARKDOWN_DURABLE_BYPASS_NONE) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        rc = ngx_http_next_body_filter(r, in);
        if (bypass_kind
                == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY
            || bypass_kind
                == NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_BODY)
        {
            ngx_http_markdown_settle_durable_bypass_delivery(
                r, bypass_kind, rc);
        }
        return rc;
    }

    if (ctx->error.header_reject) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        return ngx_http_next_body_filter(r, in);
    }

    if (!ctx->filter_enabled) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        return ngx_http_next_body_filter(r, in);
    }

    /* Handle HEAD request representation rewriting */
    rc = ngx_http_markdown_body_filter_handle_head(r, ctx, conf);
    if (rc != NGX_DECLINED) {
        return rc;
    }

    /* Delegate to main body filter logic */
    return ngx_http_markdown_body_filter_main(r, in, ctx, conf);
}

#endif /* NGX_HTTP_MARKDOWN_REQUEST_IMPL_H */
