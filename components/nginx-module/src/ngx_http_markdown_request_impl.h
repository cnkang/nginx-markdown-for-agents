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

/* Forward declarations for helpers defined in this file */
static ngx_int_t ngx_http_markdown_handle_ctx_alloc_failure(
    ngx_http_request_t *r, ngx_http_markdown_conf_t *conf);
static void ngx_http_markdown_init_ctx(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx, ngx_http_markdown_conf_t *conf,
    ngx_flag_t filter_enabled);
static void ngx_http_markdown_log_failure_decision(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf);
static ngx_int_t ngx_http_markdown_handle_unsupported_compression(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf);

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
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf)
{
    ngx_http_markdown_emit_failure_decision(r, ctx, conf);
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
    ngx_http_markdown_conf_t *conf)
{
    ctx->eligible = 0;
    ctx->last_error_category =
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION;
    ctx->has_error_category = 1;

    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);

    if (conf->on_error
        == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
    {
        ngx_log_error(NGX_LOG_WARN,
            r->connection->log, 0,
            "markdown filter: unsupported "
            "compression format, "
            "rejecting (fail-closed)");
        ngx_http_markdown_log_failure_decision(
            r, ctx, conf);
        return NGX_HTTP_BAD_GATEWAY;
    }

    ngx_log_error(NGX_LOG_WARN,
        r->connection->log, 0,
        "markdown filter: unsupported "
        "compression format, "
        "returning original content "
        "(fail-open)");
    ngx_http_markdown_log_failure_decision(
        r, ctx, conf);
    return ngx_http_next_header_filter(r);
}


/*
 * Handle context allocation failure in header filter.
 *
 * Records metrics, emits decision log, and applies the configured
 * error strategy (fail-closed returns 500, fail-open passes through).
 *
 * Parameters:
 *   r    - NGINX request structure
 *   conf - module location configuration
 *
 * Returns:
 *   NGX_HTTP_INTERNAL_SERVER_ERROR on fail-closed
 *   Result of ngx_http_next_header_filter on fail-open
 */
static ngx_int_t
ngx_http_markdown_handle_ctx_alloc_failure(ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf)
{
    ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                 "markdown filter: failed to allocate "
                 "context, category=system");

    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);

    if (conf->on_error
        == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
    {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown filter: context allocation "
            "failed, rejecting (fail-closed)");
        ngx_http_markdown_log_decision_with_category(
            r, conf,
            ngx_http_markdown_reason_failed_closed(),
            ngx_http_markdown_reason_from_error_category(
                NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
                r->connection->log));
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                 "markdown filter: context allocation "
                 "failed, returning original content "
                 "(fail-open)");
    ngx_http_markdown_log_decision_with_category(
        r, conf,
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
 *   conf - module location configuration
 *   filter_enabled - cached header-phase filter decision
 */
static void
ngx_http_markdown_init_ctx(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    ngx_flag_t filter_enabled)
{
    ctx->request = r;
    ctx->filter_enabled = filter_enabled;
    ctx->eligible = 1;
    ctx->buffer_initialized = 0;
    ctx->headers_forwarded = 0;
    ctx->source_last_modified_time =
        r->headers_out.last_modified_time;
    ctx->has_last_modified_time =
        (r->headers_out.last_modified_time != (time_t) -1);
    ctx->conversion_attempted = 0;
    ctx->conversion_succeeded = 0;
    ctx->bypass_counted = 0;
    ctx->processing_path =
        NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    ctx->last_error_category =
        NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    ctx->has_error_category = 0;

    /*
     * Initialize decompression state.
     * For uncompressed content, decompression_needed
     * remains 0, ensuring zero overhead in the body
     * filter.
     */
    ctx->compression_type =
        NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
    ctx->decompression_needed = 0;
    ctx->decompression_done = 0;
    ctx->compressed_size = 0;
    ctx->decompressed_size = 0;
}


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
    ngx_http_markdown_conf_t        *conf;
    ngx_http_markdown_eligibility_t  eligibility;
    ngx_flag_t                       filter_enabled;
    ngx_int_t                        should_convert;

    /* Get module configuration */
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    if (conf == NULL) {
        /* Module not configured, pass through */
        return ngx_http_next_header_filter(r);
    }

    /*
     * Resolve markdown_filter once in header phase and cache the result in
     * request context. Body phase must reuse this decision to avoid
     * header/body inconsistencies for dynamic variables.
     */
    filter_enabled = ngx_http_markdown_is_enabled(r, conf);
    if (!filter_enabled) {
        /* Module disabled, pass through */
        ngx_http_markdown_log_decision(r, conf,
            ngx_http_markdown_reason_from_eligibility(
                NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG,
                r->connection->log));
        return ngx_http_next_header_filter(r);
    }

    /*
     * Check eligibility before Accept negotiation.
     *
     * The decision chain order (Requirement 2.1) is:
     * scope -> method -> status -> range -> streaming ->
     * content-type -> size -> auth -> Accept.
     * Accept must be last before conversion attempt.
     */
    eligibility = ngx_http_markdown_check_eligibility(
        r, conf, filter_enabled);
    if (eligibility != NGX_HTTP_MARKDOWN_ELIGIBLE) {
        /* Not eligible, pass through */
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP,
                      r->connection->log, 0,
                      "markdown filter: response not eligible: %V",
                      ngx_http_markdown_eligibility_string(
                          eligibility));
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_decision(r, conf,
            ngx_http_markdown_reason_from_eligibility(
                eligibility, r->connection->log));
        return ngx_http_next_header_filter(r);
    }

    /*
     * Auth policy check happens after the core eligibility checks and before
     * Accept negotiation, matching the documented decision chain order.
     */
    if (conf->auth_policy == NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY
        && ngx_http_markdown_is_authenticated(r, conf))
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_decision(r, conf,
            ngx_http_markdown_reason_from_eligibility(
                NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH,
                r->connection->log));
        return ngx_http_next_header_filter(r);
    }

    /* Check if client wants Markdown (Accept header) */
    should_convert = ngx_http_markdown_should_convert(r, conf);
    if (!should_convert) {
        /* Client doesn't want Markdown, pass through */
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
        ngx_http_markdown_log_decision(r, conf,
            ngx_http_markdown_reason_skip_accept());
        return ngx_http_next_header_filter(r);
    }

    /* Create request context for buffering */
    ctx = ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_ctx_t));
    if (ctx == NULL) {
        return ngx_http_markdown_handle_ctx_alloc_failure(
            r, conf);
    }

    /* Initialize context */
    ngx_http_markdown_init_ctx(r, ctx, conf, filter_enabled);

    /* Set context for this request */
    ngx_http_set_ctx(r, ctx, ngx_http_markdown_filter_module);

    /*
     * Detect compression type if auto_decompress is enabled (Task 2.1, 4.2)
     * 
     * Fast path: If compression_type == NONE, decompression_needed stays 0
     * Slow path: If compression detected, decompression_needed is set to 1
     * Special case: If compression_type == UNKNOWN, trigger fail-open immediately
     * 
     * Requirements: 1.1, 1.6, 4.2, 8.1, 10.3, 11.1, 11.5
     */
    if (conf->auto_decompress) {
        ctx->compression_type = ngx_http_markdown_detect_compression(r);
        
        if (ctx->compression_type == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN) {
            /*
             * Unsupported compression format detected (Task 4.2)
             *
             * This is an expected degradation scenario, not a
             * failure.  We gracefully degrade by returning the
             * original content.
             *
             * Note: The warning log has already been emitted by
             * ngx_http_markdown_detect_compression() with the
             * format name.
             *
             * Requirements: 1.6, 11.5
             */
            return ngx_http_markdown_handle_unsupported_compression(
                r, ctx, conf);

        } else if (ctx->compression_type != NGX_HTTP_MARKDOWN_COMPRESSION_NONE) {
            /* Supported compression format - set flag for decompression */
            ctx->decompression_needed = 1;
            
            ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown filter: decompression detected compression type: %d",
                          ctx->compression_type);
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
     * Requirements: 16.1, 16.3, 16.4, 16.6, 16.7
     */
    if (conf->large_body_threshold > 0
        && r->method != NGX_HTTP_HEAD
        && r->headers_out.status != NGX_HTTP_NOT_MODIFIED)
    {
#ifdef MARKDOWN_INCREMENTAL_ENABLED
        if (r->headers_out.content_length_n >= 0
            && (size_t) r->headers_out.content_length_n
                >= conf->large_body_threshold)
        {
            ctx->processing_path =
                NGX_HTTP_MARKDOWN_PATH_INCREMENTAL;
        }
        /* else: no CL — deferred to body filter */
#else
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: markdown_large_body_threshold is set, "
                     "but incremental support was not compiled in; using "
                     "full-buffer path");
#endif
    }

    /* Record path hit metric (only for eligible requests) */
    if (ctx->eligible) {
        if (ctx->processing_path
            == NGX_HTTP_MARKDOWN_PATH_INCREMENTAL)
        {
            NGX_HTTP_MARKDOWN_METRIC_INC(incremental_path_hits);
        } else {
            NGX_HTTP_MARKDOWN_METRIC_INC(fullbuffer_path_hits);
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
                  "markdown filter: response eligible for conversion, "
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
 * Coordinate conditional-request resolution, optional Markdown conversion, and emission of the conversion result for a buffered response.
 *
 * This function performs any deferred processing-path selection (switching to incremental processing when the buffered body reaches the configured threshold), resolves conditional-request outcomes, invokes the conversion engine if no conditional result is available, and sends the final conversion output. It may update the per-request context (e.g., processing_path) and module metrics as part of its processing.
 *
 * @param r The active nginx request.
 * @param ctx Per-request Markdown module context.
 * @param conf Module configuration for the current request.
 * @returns `NGX_OK` on success, or another `ngx_int_t` code returned by underlying helpers to indicate a filter-chain decision or an error. */
static ngx_int_t
ngx_http_markdown_body_filter_convert_and_output(ngx_http_request_t *r,
                                                 ngx_http_markdown_ctx_t *ctx,
                                                 ngx_http_markdown_conf_t *conf)
{
    ngx_int_t             rc;
    ngx_msec_t            elapsed_ms;
    ngx_flag_t            has_result;
    struct MarkdownResult result;

    /*
     * Deferred path selection for chunked/unknown-length
     * responses.  If Content-Length was absent in the
     * header phase, the threshold decision was deferred
     * until the full body is buffered.
     *
     * Requirements: 16.7
     */
#ifdef MARKDOWN_INCREMENTAL_ENABLED
    if (conf->large_body_threshold > 0
        && ctx->processing_path
            == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER
        && r->method != NGX_HTTP_HEAD
        && r->headers_out.status != NGX_HTTP_NOT_MODIFIED
        && ctx->buffer.size >= conf->large_body_threshold)
    {
        ctx->processing_path =
            NGX_HTTP_MARKDOWN_PATH_INCREMENTAL;

        /*
         * Correct the path hit counters: header filter
         * already incremented fullbuffer_path_hits, so
         * undo that and count incremental instead.
         *
         * Guard against underflow: only decrement if the
         * counter is positive.  In theory the header filter
         * always increments first, but a metrics zone reset
         * (e.g. worker restart) could leave the counter at
         * zero.
         */
        if (ngx_http_markdown_metrics != NULL
            && ngx_http_markdown_metrics->fullbuffer_path_hits > 0)
        {
            NGX_HTTP_MARKDOWN_METRIC_ADD(
                fullbuffer_path_hits, -1);
        }
        NGX_HTTP_MARKDOWN_METRIC_INC(
            incremental_path_hits);
    }
#endif

    /*
     * conversion_attempted and conversions_attempted metric are
     * already set by the body filter before decompression.
     */
    elapsed_ms = 0;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: buffered complete response, size: %uz bytes",
                  ctx->buffer.size);

    rc = ngx_http_markdown_resolve_conditional_result(
        r, ctx, conf, &result, &elapsed_ms, &has_result);
    if (rc == NGX_HTTP_NOT_MODIFIED) {
        /* 304 Not Modified — conversion matched, log as converted */
        ngx_http_markdown_log_decision(r, conf,
            ngx_http_markdown_reason_converted());
        return NGX_OK;
    }
    if (rc != NGX_OK) {
        /* Conditional processing failed — log failure outcome */
        ngx_http_markdown_log_failure_decision(r, ctx, conf);
        return rc;
    }

    if (!has_result) {
        rc = ngx_http_markdown_execute_conversion(r, ctx, conf, &result, &elapsed_ms);
        if (rc != NGX_OK) {
            /* Conversion failed — log failure outcome */
            ngx_http_markdown_log_failure_decision(r, ctx, conf);
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
        ngx_http_markdown_log_decision(r, conf,
            ngx_http_markdown_reason_converted());
    }

    return rc;
}

/*
 * Body filter
 *
 * Called for each chunk of the response body.
 * Buffers the response and performs conversion when complete.
 * 
 * This implements Task 14.7: Body filter hook
 * - Accumulates response chunks in buffer
 * - Detects when all chunks are buffered (last_buf flag)
 * - Calls Rust conversion engine via FFI
 * - Updates response headers on success
 * - Sends converted Markdown response
 * - Handles errors with configured strategy
 *
 * Requirements: FR-02.4, FR-04.1, FR-09.1, FR-09.2, FR-10.1, FR-10.3
 * 
 * @param r   The request structure
 * @param in  The input chain containing response body chunks
 * @return    NGX_OK on success, NGX_ERROR on error
 */
static ngx_int_t
ngx_http_markdown_body_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    ngx_http_markdown_ctx_t   *ctx;
    ngx_http_markdown_conf_t  *conf;
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

    /* If not eligible for conversion, pass through */
    if (!ctx->eligible) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        if (!ctx->bypass_counted && !ctx->has_error_category) {
            /*
             * Track bypassed request once even if the body
             * arrives in chunks.  Do not count requests that
             * already recorded a failure (has_error_category)
             * — those are accounted for by conversions_failed.
             */
            NGX_HTTP_MARKDOWN_METRIC_INC(conversions_bypassed);
            ctx->bypass_counted = 1;
        }
        if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
            return NGX_ERROR;
        }
        return ngx_http_next_body_filter(r, in);
    }

    /* If conversion already attempted, pass through */
    if (ctx->conversion_attempted) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        return ngx_http_next_body_filter(r, in);
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
    ctx->conversion_attempted = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);

    rc = ngx_http_markdown_body_filter_decompress_if_needed(r, ctx, conf);
    if (rc != NGX_OK) {
        return rc;
    }

    return ngx_http_markdown_body_filter_convert_and_output(r, ctx, conf);
}

#endif /* NGX_HTTP_MARKDOWN_REQUEST_IMPL_H */
