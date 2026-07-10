/*
 * NGINX Markdown Filter Module - Diagnostics Accessor Implementations
 *
 * Provides accessor functions that bridge the diagnostics compilation
 * unit with the module-internal state (metrics pointer, dynconf watcher).
 *
 * This header MUST be included only from the main translation unit
 * (ngx_http_markdown_filter_module.c) after the module_state_impl.h
 * and dynconf_impl.h headers have been included.
 *
 * Requirement: REQ-0700-OPERABILITY-001
 */

#ifndef NGX_HTTP_MARKDOWN_DIAGNOSTICS_ACCESSORS_IMPL_H
#define NGX_HTTP_MARKDOWN_DIAGNOSTICS_ACCESSORS_IMPL_H

#include "ngx_http_markdown_diagnostics.h"


/*
 * Collect key metrics counters for the diagnostics endpoint.
 *
 * Reads the global ngx_http_markdown_metrics pointer (SHM zone)
 * and copies the relevant counters into the output struct.
 * If the metrics pointer is NULL (zone not initialized), all
 * fields are zeroed.
 */
void
ngx_http_markdown_diagnostics_collect_metrics(
    ngx_http_markdown_diag_metrics_t *out)
{
    if (out == NULL) {
        return;
    }

    ngx_memzero(out, sizeof(ngx_http_markdown_diag_metrics_t));

    if (ngx_http_markdown_metrics == NULL) {
        return;
    }

    out->conversions_total =
        ngx_http_markdown_metrics->conversions_succeeded;
    out->delivery_total =
        ngx_http_markdown_metrics->results.delivery_count;
    out->requests_total =
        ngx_http_markdown_metrics->requests_entered;
    out->failopen_total =
        ngx_http_markdown_metrics->results.failopen_count;
    out->overload_total =
        (ngx_atomic_uint_t) ngx_http_markdown_inflight_overload_total();
    out->backpressure_total =
        ngx_http_markdown_metrics->perf.backpressure_total;

#ifdef MARKDOWN_STREAMING_ENABLED
    out->streaming_requests_total =
        ngx_http_markdown_metrics->streaming.requests_total;
    out->streaming_succeeded_total =
        ngx_http_markdown_metrics->streaming.succeeded_total;
    out->streaming_failed_total =
        ngx_http_markdown_metrics->streaming.failed_total;
    out->streaming_fallback_total =
        ngx_http_markdown_metrics->streaming.fallback_total;
    out->streaming_candidate_total =
        ngx_http_markdown_metrics->streaming.selection.candidate_total;
    out->streaming_output_bytes_total =
        ngx_http_markdown_metrics->streaming.selection.output_bytes_total;
    out->engine_choice_streaming =
        ngx_http_markdown_metrics->streaming.engine_choice.streaming;
    out->engine_choice_full_buffer =
        ngx_http_markdown_metrics->streaming.engine_choice.full_buffer;
#endif
}


/*
 * Get the current dynconf watcher state for the diagnostics endpoint.
 *
 * Reads the global ngx_http_markdown_dynconf_watcher and copies
 * the relevant state fields into the output struct.  If the
 * watcher is not active, all fields are zeroed.
 */
void
ngx_http_markdown_diagnostics_get_dynconf_state(
    ngx_http_markdown_diag_dynconf_t *out)
{
    if (out == NULL) {
        return;
    }

    ngx_memzero(out, sizeof(ngx_http_markdown_diag_dynconf_t));

    if (!ngx_http_markdown_dynconf_watcher.active) {
        return;
    }

    out->active_mtime = ngx_http_markdown_dynconf_watcher.applied_mtime;
    out->config_version = ngx_http_markdown_dynconf_watcher.version;
    out->last_known_good_mtime = ngx_http_markdown_dynconf_watcher.lkg_mtime;
    out->lkg_valid = ngx_http_markdown_dynconf_watcher.lkg_valid ? 1 : 0;
}


/*
 * Trigger a manual rollback to the last-known-good configuration.
 *
 * Called from the diagnostics endpoint when an operator requests
 * a rollback via the diagnostics API.  Delegates to the dynconf
 * rollback function which restores the LKG snapshot.
 *
 * Parameters:
 *   log - NGINX log for recording the rollback event
 *
 * Returns:
 *   NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_OK on success
 *   NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_NO_LKG if no LKG available
 *   NGX_HTTP_MARKDOWN_DYNCONF_ROLLBACK_APPLY_ERR on error
 */
ngx_int_t
ngx_http_markdown_diagnostics_trigger_rollback(ngx_log_t *log)
{
    return ngx_http_markdown_dynconf_rollback(
        &ngx_http_markdown_dynconf_watcher, log);
}


#endif /* NGX_HTTP_MARKDOWN_DIAGNOSTICS_ACCESSORS_IMPL_H */
