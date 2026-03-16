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

/*
 * Header filter
 *
 * Called when response headers are being sent.
 * Checks if conversion should be attempted based on:
 * - Accept header (text/markdown)
 * - Configuration (enabled/disabled)
 * - Request method (GET/HEAD)
 * - Response status and content type
 * 
 * If conversion is eligible, sets up conversion context.
 * Otherwise, passes through to next filter.
 *
 * This is part of Task 14.4 setup - creates context for body buffering.
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
        return ngx_http_next_header_filter(r);
    }

    /* Check if client wants Markdown (Accept header) */
    should_convert = ngx_http_markdown_should_convert(r, conf);
    if (!should_convert) {
        /* Client doesn't want Markdown, pass through */
        return ngx_http_next_header_filter(r);
    }

    /* Check if response is eligible for conversion */
    eligibility = ngx_http_markdown_check_eligibility(r, conf, filter_enabled);
    if (eligibility != NGX_HTTP_MARKDOWN_ELIGIBLE) {
        /* Not eligible, pass through */
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: response not eligible: %V",
                      ngx_http_markdown_eligibility_string(eligibility));
        return ngx_http_next_header_filter(r);
    }

    /* Create request context for buffering */
    ctx = ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_ctx_t));
    if (ctx == NULL) {
        /*
         * Context allocation failed - critical system error.
         * This means we're out of memory.
         * 
         * Log level: NGX_LOG_CRIT (critical system failure)
         * Requirements: FR-09.5, FR-09.6, FR-09.7
         */
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to allocate context, category=system");
        return ngx_http_next_header_filter(r);
    }

    /* Initialize context */
    ctx->request = r;
    ctx->filter_enabled = filter_enabled;
    ctx->eligible = 1;
    ctx->buffer_initialized = 0;
    ctx->headers_forwarded = 0;
    ctx->conversion_attempted = 0;
    ctx->conversion_succeeded = 0;
    ctx->bypass_counted = 0;
    ctx->processing_path = NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    
    /* Initialize decompression state (Task 2.4 - Fast path initialization)
     * 
     * For uncompressed content, decompression_needed remains 0, ensuring
     * zero overhead in the body filter (no decompression functions called).
     * 
     * Requirements: 4.2, 10.3
     */
    ctx->compression_type = NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
    ctx->decompression_needed = 0;
    ctx->decompression_done = 0;
    ctx->compressed_size = 0;
    ctx->decompressed_size = 0;

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
             * This is an expected degradation scenario, not a failure.
             * We gracefully degrade by returning the original content.
             * 
             * Note: The warning log has already been emitted by
             * ngx_http_markdown_detect_compression() with the format name.
             * 
             * Requirements: 1.6, 11.5
             */
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown filter: unsupported compression format detected, "
                         "returning original content (fail-open)");
            
            /* Set eligible = 0 to trigger fail-open (return original content) */
            ctx->eligible = 0;
            
            /* Don't set decompression_needed - we're not attempting decompression */
            /* Don't increment failure counter - this is expected degradation */
            
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
        if (r->headers_out.content_length_n >= 0
            && (size_t) r->headers_out.content_length_n
                >= conf->large_body_threshold)
        {
            ctx->processing_path =
                NGX_HTTP_MARKDOWN_PATH_INCREMENTAL;
        }
        /* else: no CL — deferred to body filter */
    }

    /* Record path hit metric */
    if (ctx->processing_path
        == NGX_HTTP_MARKDOWN_PATH_INCREMENTAL)
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(incremental_path_hits);
    } else {
        NGX_HTTP_MARKDOWN_METRIC_INC(fullbuffer_path_hits);
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

/* Orchestrate conditional-request check, conversion, and output emission. */
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

    ctx->conversion_attempted = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    elapsed_ms = 0;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: buffered complete response, size: %uz bytes",
                  ctx->buffer.size);

    rc = ngx_http_markdown_resolve_conditional_result(
        r, ctx, conf, &result, &elapsed_ms, &has_result);
    if (rc == NGX_DONE) {
        return NGX_OK;
    }
    if (rc != NGX_OK) {
        return rc;
    }

    if (!has_result) {
        rc = ngx_http_markdown_execute_conversion(r, ctx, conf, &result, &elapsed_ms);
        if (rc != NGX_OK) {
            return rc;
        }
    }

    return ngx_http_markdown_send_conversion_output(r, ctx, conf, &result, elapsed_ms);
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
        if (!ctx->bypass_counted) {
            /* Track bypassed request once even if the body arrives in chunks. */
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

    rc = ngx_http_markdown_body_filter_decompress_if_needed(r, ctx, conf);
    if (rc != NGX_OK) {
        return rc;
    }

    return ngx_http_markdown_body_filter_convert_and_output(r, ctx, conf);
}
