#ifndef NGX_HTTP_MARKDOWN_PAYLOAD_IMPL_H
#define NGX_HTTP_MARKDOWN_PAYLOAD_IMPL_H

/*
 * Payload-path helpers.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * Kept separate from the request entry/exit state machine so buffering,
 * decompression coordination, header forwarding, and fail-open replay can
 * evolve without expanding the header/body filter flow itself.
 */

static ngx_int_t ngx_http_markdown_forward_headers(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);
static ngx_int_t ngx_http_markdown_send_buffered_original_response(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx);
static ngx_int_t ngx_http_markdown_fail_open_with_buffered_prefix(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx, ngx_chain_t *remaining);
static void ngx_http_markdown_reclassify_fail_open_path(
    ngx_http_markdown_ctx_t *ctx);
static ngx_int_t ngx_http_markdown_handle_decompression_alloc_error(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_http_markdown_error_category_t category,
    const char *debug_message);
static ngx_int_t ngx_http_markdown_handle_decompression_conversion_error(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const char *debug_message);
static void ngx_http_markdown_emit_failure_decision(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);
static void ngx_http_markdown_record_system_failure(
    ngx_http_markdown_ctx_t *ctx);
const ngx_str_t *ngx_http_markdown_reason_failed_closed(void);
const ngx_str_t *ngx_http_markdown_reason_failed_open(void);
const ngx_str_t *ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, ngx_log_t *log);
ngx_int_t ngx_http_markdown_decompress(ngx_http_request_t *r,
    ngx_http_markdown_compression_type_e type, const ngx_chain_t *in,
    ngx_chain_t **out);
static ngx_int_t ngx_http_markdown_decompress_via_rust(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_chain_t *compressed_chain,
    ngx_chain_t **decompressed_chain);
#ifndef NGX_HTTP_MARKDOWN_NO_RUST_DECOMPRESS
static ngx_int_t ngx_http_markdown_linearize_chain(
    ngx_http_request_t *r, const ngx_chain_t *chain,
    u_char **out_buf, size_t *out_size);
#endif
static void ngx_http_markdown_log_decision_with_category(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code, const ngx_str_t *error_category);
static void ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_conf_t *conf);
static const ngx_str_t *ngx_http_markdown_compression_name(
    ngx_http_markdown_compression_type_e compression_type);
static ngx_http_output_header_filter_pt ngx_http_next_header_filter;
static ngx_http_output_body_filter_pt ngx_http_next_body_filter;

/*
 * Reclassify an incremental-path attempt as full-buffer fail-open.
 *
 * Incremental routing increments the path-hit metric before the final
 * conversion outcome is known.  If that path later fails open and serves the
 * original buffered body, operators should see the request counted with the
 * full-buffer/fail-open behavior that actually reached the client.  The
 * decrement guard avoids underflow after counter resets or partial tests.
 */
static void
ngx_http_markdown_reclassify_fail_open_path(ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL
        || ctx->processing_path != NGX_HTTP_MARKDOWN_PATH_INCREMENTAL)
    {
        return;
    }

    if (ngx_http_markdown_metrics != NULL
        && ngx_http_markdown_metrics->path_hits.incremental > 0)
    {
        NGX_HTTP_MARKDOWN_METRIC_ADD(path_hits.incremental, -1);
    }
    NGX_HTTP_MARKDOWN_METRIC_INC(path_hits.fullbuffer);
    ctx->processing_path = NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
}

/* Return original HTML when conversion fails or is ineligible (fail-open). */
static ngx_int_t
ngx_http_markdown_fail_open_buffered_response(ngx_http_request_t *r,
                                              ngx_http_markdown_ctx_t *ctx,
                                              const char *debug_message)
{
    if (debug_message != NULL) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0, debug_message);
    }

    ngx_http_markdown_reclassify_fail_open_path(ctx);
    ctx->eligible = 0;

    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
        return NGX_ERROR;
    }

    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;

    return ngx_http_markdown_send_buffered_original_response(r, ctx);
}

/* Apply error strategy: reject (return error) or fail-open (return original). */
static ngx_int_t
ngx_http_markdown_reject_or_fail_open_buffered_response(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const char *debug_message)
{
    ngx_int_t  rc;

    if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        /*
         * Use ngx_http_filter_finalize_request to send the configured
         * error status (429/503/502).  In the body filter, returning a
         * positive HTTP status code directly is unreliable — NGINX's
         * upstream body output path only special-cases NGX_ERROR.
         * ngx_http_filter_finalize_request sets r->headers_out.status
         * and routes through the finalizer to generate the correct
         * error response.
         */
        return ngx_http_filter_finalize_request(r,
            (ngx_int_t) conf->error_status);
    }

    rc = ngx_http_markdown_fail_open_buffered_response(
        r, ctx, debug_message);
    if (rc == NGX_OK || rc == NGX_DONE) {
        /*
         * Record fail-open outcome only after the original response
         * has been successfully sent downstream.  Do not count
         * NGX_AGAIN (backpressure) or NGX_ERROR as fail-open
         * completions — the response has not reached the client.
         */
        NGX_HTTP_MARKDOWN_METRIC_INC(results.failopen_count);
    }
    if (rc != NGX_OK && rc != NGX_DONE) {
        return rc;
    }

    /*
     * Fail-open already sent the original response downstream.
     * Return NGX_DONE so callers stop further decompression/
     * conversion work for this request.
     */
    return NGX_DONE;
}

/*
 * Handle a decompression-phase allocation or system error.
 *
 * Centralizes the repeated pattern of:
 *   1. recording error category in context
 *   2. incrementing decompressions.failed + conversions_failed + category metric
 *   3. emitting a decision log entry with the failure reason
 *   4. applying the configured error strategy (reject or fail-open)
 *
 * Parameters:
 *   r              - NGINX request structure
 *   ctx            - per-request module context
 *   conf           - module location configuration
 *   category       - error classification for metrics
 *   debug_message  - optional debug log message (NULL to skip)
 *
 * Returns:
 *   Result of reject_or_fail_open_buffered_response
 */
static ngx_int_t
ngx_http_markdown_handle_decompression_alloc_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_http_markdown_error_category_t category,
    const char *debug_message)
{
    const ngx_str_t *reason;

    ctx->error.last_category = category;
    ctx->error.has_category = 1;

    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);

    switch (category) {

    case NGX_HTTP_MARKDOWN_ERROR_CONVERSION:
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);
        break;

    case NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT:
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_resource_limit);
        break;

    case NGX_HTTP_MARKDOWN_ERROR_SYSTEM:
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);
        break;
    }

    reason = (conf->on_error
              == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
        ? ngx_http_markdown_reason_failed_closed()
        : ngx_http_markdown_reason_failed_open();

    ngx_http_markdown_log_decision_with_category(
        r, conf, ctx->effective_conf, reason,
        ngx_http_markdown_reason_from_error_category(
            category, r->connection->log));

    return ngx_http_markdown_reject_or_fail_open_buffered_response(
        r, ctx, conf, debug_message);
}


/*
 * Wrap the buffered compressed payload into a chain for the decompressor.
 *
 * After body-filter accumulation, ctx->buffer.data is always a single
 * contiguous ngx_alloc-backed allocation (Rule 43 invariant).  When
 * this invariant holds we reference the buffer directly without a
 * linearize copy, reducing one full memcpy from the decompression
 * hot path.
 */
static ngx_int_t
ngx_http_markdown_prepare_compressed_chain(ngx_http_request_t *r,
                                           ngx_http_markdown_ctx_t *ctx,
                                           const ngx_http_markdown_conf_t *conf,
                                           ngx_chain_t **compressed_chain)
{
    ngx_buf_t *compressed_buf;

    if (ctx->buffer.size > 0 && ctx->buffer.data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: buffered payload "
                     "pointer is NULL with non-zero "
                     "size, size=%uz, category=system",
                     ctx->buffer.size);
        return ngx_http_markdown_handle_decompression_alloc_error(
            r, ctx, conf,
            NGX_HTTP_MARKDOWN_ERROR_SYSTEM, NULL);
    }

    /*
     * Contiguity fast path: ctx->buffer.data is a single ngx_alloc
     * allocation after body-filter accumulation.  Reference it
     * directly without copying to avoid the linearize memcpy.
     * The buffer remains valid through the decompression call
     * because apply_decompressed_payload runs only after success.
     */
    compressed_buf = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (compressed_buf == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to create "
                     "compressed buffer, category=system");
        return ngx_http_markdown_handle_decompression_alloc_error(
            r, ctx, conf, NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
            "markdown: fail-open strategy "
            "- returning original content");
    }

    compressed_buf->pos = ctx->buffer.data;
    compressed_buf->last = ctx->buffer.data + ctx->buffer.size;
    compressed_buf->memory = 1;
    compressed_buf->last_buf = 1;

    *compressed_chain = ngx_alloc_chain_link(r->pool);
    if (*compressed_chain == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate "
                     "chain link, category=system");
        return ngx_http_markdown_handle_decompression_alloc_error(
            r, ctx, conf,
            NGX_HTTP_MARKDOWN_ERROR_SYSTEM, NULL);
    }

    (*compressed_chain)->buf = compressed_buf;
    (*compressed_chain)->next = NULL;
    return NGX_OK;
}

/* Replace the module buffer contents with the decompressed payload. */
static ngx_int_t
ngx_http_markdown_apply_decompressed_payload(ngx_http_request_t *r,
                                             ngx_http_markdown_ctx_t *ctx,
                                             const ngx_http_markdown_conf_t *conf,
                                             const ngx_chain_t *decompressed_chain)
{
    u_char  *decompressed_data;
    size_t   decompressed_size;

    if (decompressed_chain == NULL || decompressed_chain->buf == NULL) {
        const ngx_str_t *compression_name;

        compression_name = ngx_http_markdown_compression_name(
            ctx->decompression.type);

        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompression "
                     "returned NULL chain, "
                     "compression=%V, category=system",
                     compression_name);

        return ngx_http_markdown_handle_decompression_alloc_error(
            r, ctx, conf,
            NGX_HTTP_MARKDOWN_ERROR_SYSTEM, NULL);
    }

    /*
     * Compute decompressed size defensively.  When both pos and last
     * are NULL the buffer represents a valid zero-length payload (e.g.
     * decompressing an empty compressed stream).  Pointer subtraction
     * on two NULL pointers is undefined behaviour in C, so we handle
     * this case explicitly.
     */
    if (decompressed_chain->buf->pos == NULL
        && decompressed_chain->buf->last == NULL)
    {
        ctx->decompression.decompressed_size = 0;
        ctx->buffer.size = 0;
        ctx->decompression.done = 1;
        return NGX_OK;
    }

    if (decompressed_chain->buf->pos == NULL
        || decompressed_chain->buf->last == NULL
        || (uintptr_t) decompressed_chain->buf->last
           < (uintptr_t) decompressed_chain->buf->pos)
    {
        const ngx_str_t *compression_name;

        compression_name = ngx_http_markdown_compression_name(
            ctx->decompression.type);

        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompression "
                     "returned invalid buffer "
                     "pointers, compression=%V, "
                     "category=system",
                     compression_name);

        /*
         * The buffer pos may point to ngx_alloc'd memory from
         * decompress_via_rust.  Free it to prevent a leak.
         */
        if (decompressed_chain->buf->pos != NULL) {
            ngx_free(decompressed_chain->buf->pos);
        }

        return ngx_http_markdown_handle_decompression_alloc_error(
            r, ctx, conf,
            NGX_HTTP_MARKDOWN_ERROR_SYSTEM, NULL);
    }

    decompressed_size =
        decompressed_chain->buf->last - decompressed_chain->buf->pos;
    decompressed_data = decompressed_chain->buf->pos;

    /*
     * Phase 1: Budget check — verify the decompressor output does
     * not exceed markdown_decompress_max_size before swapping.
     * On failure, free the decompressor output and trigger
     * fail-open with the original compressed ctx->buffer.data
     * intact (Requirement 5.4).
     */
    if (decompressed_size > conf->decompress.max_size) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: decompressed output "
                     "exceeds budget, size=%uz, "
                     "max=%uz, category=resource_limit",
                     decompressed_size,
                     conf->decompress.max_size);

        ngx_free(decompressed_data);

        return ngx_http_markdown_handle_decompression_alloc_error(
            r, ctx, conf,
            NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
            "markdown: fail-open strategy "
            "- decompressed output exceeds budget");
    }

    /*
     * Phase 2: Direct buffer swap — the decompressor output is
     * already in an ngx_alloc buffer (Rule 43).  Free the old
     * compressed buffer and swap in the decompressed pointer
     * directly without an intermediate memcpy.
     *
     * Invariant: on success, ctx->buffer.data points to the new
     * decompressed buffer; on failure (handled above), the
     * original compressed buffer remains intact for fail-open.
     */
    ctx->decompression.decompressed_size = decompressed_size;

    if (ctx->buffer.data != NULL) {
        ngx_free(ctx->buffer.data);
    }

    ctx->buffer.data = decompressed_data;
    ctx->buffer.size = decompressed_size;
    ctx->buffer.capacity = decompressed_size;
    ctx->decompression.done = 1;

    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: direct buffer swap "
                  "completed, old compressed buffer "
                  "freed, new size=%uz, capacity=%uz",
                  ctx->buffer.size, ctx->buffer.capacity);

    return NGX_OK;
}

/* Log decompression metrics and strip Content-Encoding after success. */
static void
ngx_http_markdown_record_decompression_success(ngx_http_request_t *r,
                                               const ngx_http_markdown_ctx_t *ctx)
{
    float ratio;

    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.succeeded);
    switch (ctx->decompression.type) {
        case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
            NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.gzip);
            break;
        case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
            NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.deflate);
            break;
        case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
            NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.brotli);
            break;
        default:
            break;
    }

    ratio = (ctx->decompression.compressed_size != 0)
        ? (float) ctx->decompression.decompressed_size / (float) ctx->decompression.compressed_size
        : 0.0f;

    ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
                 "markdown: decompression succeeded, "
                 "compression=%d, compressed=%uz bytes, decompressed=%uz bytes, ratio=%.1fx",
                 ctx->decompression.type, ctx->decompression.compressed_size,
                 ctx->decompression.decompressed_size,
                 ratio);

    ngx_http_markdown_remove_content_encoding(r);
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: removed Content-Encoding header after decompression");
}


/*
 * Emit a decision log entry for a failure with the appropriate
 * reason code (ELIGIBLE_FAILED_OPEN or ELIGIBLE_FAILED_CLOSED)
 * based on the configured error strategy.
 *
 * If the context has an error category set, it is included in the
 * log entry as a sub-classification.
 *
 * Parameters:
 *   r    - NGINX request structure
 *   ctx  - per-request module context (for error category)
 *   conf - module location configuration (for on_error policy)
 */
static void
ngx_http_markdown_emit_failure_decision(ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    const ngx_str_t  *reason;
    const ngx_str_t  *fail_category;

    reason = (conf->on_error
              == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
        ? ngx_http_markdown_reason_failed_closed()
        : ngx_http_markdown_reason_failed_open();

    fail_category = ctx->error.has_category
        ? ngx_http_markdown_reason_from_error_category(
              ctx->error.last_category,
              r->connection->log)
        : NULL;

    ngx_http_markdown_log_decision_with_category(
        r, conf, ctx->effective_conf, reason, fail_category);
}


/* Handle buffer initialization failure with configured error strategy. */
static ngx_int_t
ngx_http_markdown_handle_buffer_init_failure(ngx_http_request_t *r,
                                             ngx_http_markdown_ctx_t *ctx,
                                             const ngx_http_markdown_conf_t *conf,
                                             ngx_chain_t *in)
{
    ngx_int_t  rc;

    ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                 "markdown: failed to initialize "
                 "buffer, category=system");

    ngx_http_markdown_record_system_failure(ctx);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);

    ngx_http_markdown_emit_failure_decision(r, ctx, conf);

    if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        return NGX_ERROR;
    }

    ngx_http_markdown_metric_inc_failopen(conf);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: fail-open strategy - returning original HTML");
    ngx_http_markdown_reclassify_fail_open_path(ctx);
    ctx->eligible = 0;
    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
        return NGX_ERROR;
    }

    rc = ngx_http_next_body_filter(r, in);

    /*
     * Return the downstream result directly (typically NGX_OK).
     *
     * Returning NGX_DONE here would signal NGINX that request
     * processing is complete, which prevents subsequent body
     * chunks from reaching the filter chain and truncates the
     * response.  Since ctx->eligible is already cleared, any
     * later body filter invocations will take the pass-through
     * path and forward chunks unchanged.
     */
    return rc;
}

/* Handle buffer append failure (size limit exceeded) with error strategy. */
static ngx_int_t
ngx_http_markdown_handle_buffer_append_failure(ngx_http_request_t *r,
                                               ngx_http_markdown_ctx_t *ctx,
                                               const ngx_http_markdown_conf_t *conf,
                                               ngx_chain_t *cl,
                                               size_t chunk_size)
{
    ngx_int_t  rc;
    size_t     attempted_size;
    size_t     body_limit;

    attempted_size = (((size_t) -1) - ctx->buffer.size < chunk_size)
        ? (size_t) -1
        : ctx->buffer.size + chunk_size;

    body_limit = ngx_http_markdown_effective_body_buffer_limit(
        ctx->effective_conf, conf);

    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown: response size exceeds limit, "
                 "size=%uz bytes, max=%uz bytes, category=resource_limit",
                 attempted_size, body_limit);

    ctx->error.last_category = NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT;
    ctx->error.has_category = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);
    NGX_HTTP_MARKDOWN_METRIC_INC(failures_resource_limit);

    ngx_http_markdown_emit_failure_decision(r, ctx, conf);

    if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        return NGX_ERROR;
    }

    ngx_http_markdown_metric_inc_failopen(conf);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: fail-open strategy - returning original HTML");
    ctx->eligible = 0;
    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_fail_open_with_buffered_prefix(r, ctx, cl);

    /*
     * Preserve the downstream result.  The oversized chain may not be the
     * terminal upstream chain when the response is chunked.  Returning
     * NGX_DONE after a successful replay would stop later body-filter calls
     * and truncate the original response.  With eligible cleared, subsequent
     * chunks take the ordinary pass-through path.
     */
    return rc;
}

/*
 * Append one chain link's in-memory bytes into the accumulation buffer.
 *
 * Preconditions: `cl` is a live NGINX chain link and any non-NULL buffer has
 * valid `pos <= last` memory pointers.  On success the buffer position is
 * advanced to `last` because the bytes are now owned by the request-pool
 * accumulation buffer.  On append failure the fail-open path receives the
 * original chain link before its position is advanced.
 */
static ngx_int_t
ngx_http_markdown_append_buffered_chunk(ngx_http_request_t *r,
                                        ngx_http_markdown_ctx_t *ctx,
                                        const ngx_http_markdown_conf_t *conf,
                                        ngx_chain_t *cl)
{
    ngx_int_t rc;
    size_t    chunk_size;

    if (cl->buf == NULL) {
        return NGX_OK;
    }

    chunk_size = ngx_http_markdown_buf_len_safe(cl->buf);
    if (chunk_size == 0) {
        return NGX_OK;
    }

    rc = ngx_http_markdown_buffer_append(&ctx->buffer, cl->buf->pos, chunk_size);
    if (rc != NGX_OK) {
        return ngx_http_markdown_handle_buffer_append_failure(
            r, ctx, conf, cl, chunk_size);
    }

    cl->buf->pos = cl->buf->last;
    return NGX_OK;
}

/*
 * Buffer incoming body chunks until the last_buf flag is seen.
 *
 * Returns NGX_AGAIN while buffering, NGX_OK when complete.
 */
static ngx_int_t
ngx_http_markdown_body_filter_buffer_input(ngx_http_request_t *r,
                                           ngx_chain_t *in,
                                           ngx_http_markdown_ctx_t *ctx,
                                           const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t     rc;
    ngx_flag_t    last_buf;
    size_t        reserve_hint;
    off_t         content_length_n;
    size_t        body_limit;

    body_limit = ngx_http_markdown_effective_body_buffer_limit(
        ctx->effective_conf, conf);

    if (!ctx->buffer_initialized) {
        rc = ngx_http_markdown_buffer_init(&ctx->buffer, body_limit, r->pool);
        if (rc != NGX_OK) {
            return ngx_http_markdown_handle_buffer_init_failure(r, ctx, conf, in);
        }
        ctx->buffer_initialized = 1;

        content_length_n = r->headers_out.content_length_n;
        if (content_length_n > 0 && content_length_n <= (off_t) body_limit) {
            reserve_hint = (size_t) content_length_n;
            if (reserve_hint > NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT) {
                reserve_hint = NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT;
            }

            if (ngx_http_markdown_buffer_reserve(&ctx->buffer, reserve_hint) != NGX_OK) {
                ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                             "markdown: failed to pre-reserve %uz bytes buffer capacity",
                             reserve_hint);
            }
        }
    }

    last_buf = 0;
    for (ngx_chain_t *cl = in; cl != NULL; cl = cl->next) {
        rc = ngx_http_markdown_append_buffered_chunk(r, ctx, conf, cl);
        if (rc != NGX_OK) {
            return rc;
        }

        if (cl->buf != NULL
            && (cl->buf->last_buf || (r != r->main && cl->buf->last_in_chain)))
        {
            last_buf = 1;
            break;
        }
    }

    if (!last_buf) {
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
        return NGX_AGAIN;
    }

    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
    return NGX_OK;
}

/*
 * Handle a decompression-phase conversion error (DECLINED or ERROR).
 *
 * Centralizes the repeated pattern for decompression failures that
 * are classified as conversion errors (unsupported format, decode
 * failure).
 *
 * Parameters:
 *   r              - NGINX request structure
 *   ctx            - per-request module context
 *   conf           - module location configuration
 *   debug_message  - fail-open debug log message
 *
 * Returns:
 *   Result of reject_or_fail_open_buffered_response
 */
static ngx_int_t
ngx_http_markdown_handle_decompression_conversion_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const char *debug_message)
{
    return ngx_http_markdown_handle_decompression_alloc_error(
        r, ctx, conf,
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION,
        debug_message);
}

/*
 * Linearize a buffer chain into a single contiguous allocation.
 *
 * Validates each buffer's pos/last pointers (non-NULL, well-ordered via
 * uintptr_t comparison) and accumulates total size with overflow checking.
 * On success, allocates a pool buffer and copies all chain data into it.
 *
 * Parameters:
 *   r        - NGINX request (pool allocation and logging)
 *   chain    - input chain to linearize
 *   out_buf  - output: pointer to the allocated contiguous buffer
 *   out_size - output: total byte count
 *
 * Returns:
 *   NGX_OK                                  - success
 *   NGX_ERROR                               - invalid pointers or alloc failure
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED - size overflow
 *   NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT - empty input
 */
#ifndef NGX_HTTP_MARKDOWN_NO_RUST_DECOMPRESS
static ngx_int_t
ngx_http_markdown_linearize_chain(ngx_http_request_t *r,
                                  const ngx_chain_t *chain,
                                  u_char **out_buf, size_t *out_size)
{
    const ngx_chain_t  *src;
    size_t              total;
    u_char             *buf;
    u_char             *dst;

    total = 0;
    for (src = chain; src != NULL; src = src->next) {
        if (src->buf == NULL) {
            continue;
        }

        if (src->buf->pos == NULL || src->buf->last == NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: linearize chain "
                         "invalid buffer pointers, "
                         "category=system");
            return NGX_ERROR;
        }

        {
            size_t  len;

            len = ngx_http_markdown_buf_len_safe(src->buf);
            if (len == 0) {
                continue;
            }
            if (len > ((size_t) -1) - total) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: linearize chain "
                             "input size overflow, "
                             "category=resource");
                return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
            }
            total += len;
        }
    }

    if (total == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: linearize chain "
                     "called with empty input");
        return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
    }

    buf = ngx_palloc(r->pool, total);
    if (buf == NULL) {
        return NGX_ERROR;
    }

    dst = buf;
    for (src = chain; src != NULL; src = src->next) {
        if (src->buf != NULL) {
            size_t  len;

            len = ngx_http_markdown_buf_len_safe(src->buf);
            if (len > 0) {
                ngx_memcpy(dst, src->buf->pos, len);
                dst += len;
            }
        }
    }

    *out_buf = buf;
    *out_size = total;
    return NGX_OK;
}
#endif /* !NGX_HTTP_MARKDOWN_NO_RUST_DECOMPRESS */


/*
 * Decompress via Rust FFI bounded decompressor.
 *
 * Linearizes the compressed chain into a contiguous buffer, maps the
 * NGINX compression type enum to the Rust format code, and calls
 * markdown_decompress_bounded with the configured budget.
 *
 * On success, wraps the Rust-owned output in an ngx_chain_t, copies
 * it to pool memory, and frees the Rust allocation.
 *
 * Falls back to the C decompressor when the Rust library is not
 * linked (NGX_HTTP_MARKDOWN_NO_RUST_DECOMPRESS defined).
 *
 * Parameters:
 *   r                - NGINX request
 *   ctx              - module context
 *   conf             - module location config
 *   compressed_chain - input chain (may have multiple buffers)
 *   decompressed_chain - output chain (single buffer on success)
 *
 * Returns:
 *   NGX_OK                                  - success
 *   NGX_DECLINED                            - unsupported format
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED
 *   NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
 *   NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT
 *   NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR
 *   NGX_ERROR                               - system error
 */
static ngx_int_t
ngx_http_markdown_decompress_via_rust(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_chain_t *compressed_chain,
    ngx_chain_t **decompressed_chain)
{
#ifdef NGX_HTTP_MARKDOWN_NO_RUST_DECOMPRESS
    /* Fallback: use the C decompressor when Rust is unavailable. */
    return ngx_http_markdown_decompress(
        r, ctx->decompression.type,
        compressed_chain, decompressed_chain);
#else
    FFIDecompResult        result;
    uint32_t               ffi_rc;
    uint8_t                format;
    size_t                 input_size;
    u_char                *input_buf;
    u_char                *output_buf;
    ngx_buf_t             *b;
    ngx_chain_t           *cl;
    ngx_int_t              rc_linear;

    /*
     * Map NGINX compression type to Rust format code:
     *   GZIP=1 -> 0, DEFLATE=2 -> 1, BROTLI=3 -> 2
     */
    switch (ctx->decompression.type) {
    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        format = 0;
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        format = 1;
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        format = 2;
        break;
    default:
        return NGX_DECLINED;
    }

    /*
     * Contiguity fast path: when the chain has a single buffer with
     * valid pos/last pointers, use it directly without linearizing.
     * This is the common case after body-filter accumulation where
     * ctx->buffer.data is a single ngx_alloc allocation (Rule 43).
     */
    if (compressed_chain->next == NULL
        && compressed_chain->buf != NULL
        && compressed_chain->buf->pos != NULL
        && compressed_chain->buf->last != NULL
        && (uintptr_t) compressed_chain->buf->last
           >= (uintptr_t) compressed_chain->buf->pos)
    {
        input_buf = compressed_chain->buf->pos;
        input_size = compressed_chain->buf->last
                     - compressed_chain->buf->pos;

        if (input_size == 0) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: contiguous chain "
                         "has zero-length buffer");
            return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
        }

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP,
                      r->connection->log, 0,
                      "markdown: skipping linearize "
                      "copy, buffer contiguous, "
                      "size=%uz", input_size);
    } else {
        /*
         * Multi-buffer chain: linearize into a contiguous allocation.
         * This path is defensive and handles chains with multiple
         * buffers, though after body-filter accumulation the chain
         * is always single-buffer.
         */
        rc_linear = ngx_http_markdown_linearize_chain(
            r, compressed_chain, &input_buf, &input_size);
        if (rc_linear != NGX_OK) {
            return rc_linear;
        }
    }

    /* Initialize the result struct before the FFI call. */
    markdown_decomp_result_init(&result);

    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: calling rust "
                  "decompress, format=%d, input=%uz, "
                  "budget=%uz",
                  (int) format, input_size,
                  conf->decompress.max_size);

    ffi_rc = markdown_decompress_bounded(
        (const uint8_t *) input_buf,
        (uintptr_t) input_size,
        format,
        (uintptr_t) conf->decompress.max_size,
        &result);

    if (ffi_rc != 0) {
        /*
         * Map Rust DECOMP_CATEGORY_* error codes to NGINX decomp error codes:
         *   101 = DECOMP_CATEGORY_BUDGET_EXCEEDED
         *   102 = DECOMP_CATEGORY_FORMAT_ERROR
         *   103 = DECOMP_CATEGORY_TRUNCATED_INPUT
         *   104 = DECOMP_CATEGORY_IO_ERROR
         *   105 = DECOMP_CATEGORY_INVALID_ARGS
         */
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: rust decompress "
                     "failed, rc=%ud, error_category=%ud",
                     ffi_rc, result.error_category);

        switch (ffi_rc) {
        case 101:
            return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
        case 102:
            return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
        case 103:
            return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
        case 104:
            return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
        case 105:
            return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
        default:
            return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
        }
    }

    /*
     * Success: copy the Rust-owned output to pool memory, then
     * free the Rust allocation.
     *
     * A zero-length output is valid (e.g. decompressing an empty
     * compressed payload).  Only treat it as an error when the
     * pointer is NULL but the length claims non-zero bytes.
     */
    if (result.output == NULL && result.output_len > 0) {
        size_t  saved_len;

        saved_len = (size_t) result.output_len;
        markdown_decompress_free(&result);
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: rust decompress "
                     "returned NULL output with "
                     "non-zero length=%uz",
                     saved_len);
        return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
    }

    if (result.output_len == 0) {
        /*
         * Valid empty decompression result.  Build a zero-length
         * buffer chain so downstream sees an empty body rather
         * than an error.
         */
        markdown_decompress_free(&result);

        b = ngx_calloc_buf(r->pool);
        if (b == NULL) {
            return NGX_ERROR;
        }

        b->pos = NULL;
        b->last = NULL;
        b->memory = 1;
        b->last_buf = 1;

        cl = ngx_alloc_chain_link(r->pool);
        if (cl == NULL) {
            return NGX_ERROR;
        }

        cl->buf = b;
        cl->next = NULL;
        *decompressed_chain = cl;

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: rust decompress "
                      "succeeded with empty output, "
                      "input=%uz", input_size);
        return NGX_OK;
    }

    /*
     * Allocate the output buffer with ngx_alloc (Rule 43) so that
     * apply_decompressed_payload can perform a direct pointer swap
     * into ctx->buffer.data without an additional memcpy.  The
     * caller (apply_decompressed_payload) takes ownership of this
     * allocation and is responsible for ngx_free on all paths.
     */
    output_buf = ngx_alloc((size_t) result.output_len,
                          r->connection->log);
    if (output_buf == NULL) {
        markdown_decompress_free(&result);
        return NGX_ERROR;
    }

    ngx_memcpy(output_buf, result.output, (size_t) result.output_len);

    {
        size_t  output_len;

        /* Save length before free resets the struct. */
        output_len = (size_t) result.output_len;

        /* Free the Rust-owned buffer before building the chain. */
        markdown_decompress_free(&result);

        /* Wrap the ngx_alloc'd output in an ngx_buf_t / chain. */
        b = ngx_calloc_buf(r->pool);
        if (b == NULL) {
            ngx_free(output_buf);
            return NGX_ERROR;
        }

        b->pos = output_buf;
        b->last = output_buf + output_len;
        b->memory = 1;
        b->last_buf = 1;

        cl = ngx_alloc_chain_link(r->pool);
        if (cl == NULL) {
            ngx_free(output_buf);
            return NGX_ERROR;
        }

        cl->buf = b;
        cl->next = NULL;
        *decompressed_chain = cl;

        ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: rust decompress "
                      "succeeded, input=%uz, output=%uz",
                      input_size, output_len);
    }

    return NGX_OK;
#endif /* NGX_HTTP_MARKDOWN_NO_RUST_DECOMPRESS */
}

/*
 * Decompress the buffered payload if compression was detected.
 *
 * This is the top-level decompression entry point for the buffered body
 * filter path.  It orchestrates three phases:
 *   1. Prepare a contiguous compressed chain from ctx->buffer.
 *   2. Invoke the Rust FFI bounded decompressor (or C fallback).
 *   3. Apply the decompressed output back into ctx->buffer.
 *
 * On every failure class the function follows a fail-open strategy:
 * it logs the error, increments the appropriate metrics, and returns
 * the original (still-compressed) content to the downstream filter
 * chain rather than rejecting the request.  This is intentional --
 * the caller (body filter) must not block or abort the request when
 * decompression is unavailable or fails; the content is forwarded
 * as-is so the client can still receive a response.
 *
 * Parameters:
 *   r    - NGINX request (used for pool allocation and logging)
 *   ctx  - module context holding the buffered payload and
 *          decompression state
 *   conf - location configuration (decompression budget, on_error
 *          policy, compression settings)
 *
 * Returns:
 *   NGX_OK   - decompression succeeded or was not needed; caller
 *              should continue normal processing
 *   NGX_ERROR - unrecoverable system error (buffer/chain allocation
 *               failure); the error handler has already logged and
 *               incremented failure metrics
 */
static inline ngx_int_t
ngx_http_markdown_handle_decompress_result(ngx_http_request_t *r,
                                           ngx_http_markdown_ctx_t *ctx,
                                           const ngx_http_markdown_conf_t *conf,
                                           ngx_int_t decompress_rc)
{
    /*
     * NGX_DECLINED means the compression type is not supported
     * by the linked decompressor library.  Treat as a conversion
     * error (fail-open: return original content).
     */
    if (decompress_rc == NGX_DECLINED) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
                      r->connection->log, 0,
                      "markdown: decompression "
                      "not supported");
        return ngx_http_markdown_handle_decompression_conversion_error(
            r, ctx, conf,
            "markdown: returning original "
            "content after unsupported decompression");
    }

    /*
     * Budget exceeded: the decompressed output would surpass the
     * configured decompress_max_size limit.  This is a resource-
     * limit category error, not a conversion error, because the
     * input is structurally valid but the output is too large.
     */
    if (decompress_rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total);
        NGX_HTTP_MARKDOWN_METRIC_INC(
            perf.decompression_budget_exceeded_total);
        return ngx_http_markdown_handle_decompression_alloc_error(
            r, ctx, conf,
            NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
            "markdown: fail-open strategy "
            "- returning original content after "
            "decompression budget exceeded");
    }

    if (decompress_rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.format_error_total);
        return ngx_http_markdown_handle_decompression_conversion_error(
            r, ctx, conf,
            "markdown: fail-open strategy "
            "- returning original content after "
            "decompression format error");
    }

    if (decompress_rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            decompressions.truncated_input_total);
        return ngx_http_markdown_handle_decompression_conversion_error(
            r, ctx, conf,
            "markdown: fail-open strategy "
            "- returning original content after "
            "truncated compressed input");
    }

    if (decompress_rc == NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.io_error_total);
        return ngx_http_markdown_handle_decompression_conversion_error(
            r, ctx, conf,
            "markdown: fail-open strategy "
            "- returning original content after "
            "decompression I/O error");
    }

    /*
     * NGX_ERROR indicates a system-level failure (invalid buffer
     * pointers, allocation failure) rather than a data-format
     * conversion error.  Route through the system-error handler
     * so metrics reflect the correct category.
     */
    if (decompress_rc == NGX_ERROR) {
        return ngx_http_markdown_handle_decompression_alloc_error(
            r, ctx, conf,
            NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
            "markdown: fail-open strategy "
            "- returning original content after "
            "decompression system error");
    }

    /*
     * Catch-all for any unexpected error code not explicitly
     * classified above.  Logs the compression type for
     * post-mortem debugging and falls back to fail-open.
     */
    if (decompress_rc != NGX_OK) {
        const ngx_str_t *compression_name;

        compression_name =
            ngx_http_markdown_compression_name(
                ctx->decompression.type);
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.io_error_total);
        ngx_log_error(NGX_LOG_ERR,
                     r->connection->log, 0,
                     "markdown: decompression "
                     "failed, compression=%V, "
                     "error=\"decompression error\", "
                     "category=conversion",
                     compression_name);
        return ngx_http_markdown_handle_decompression_conversion_error(
            r, ctx, conf,
            "markdown: fail-open strategy "
            "- returning original content");
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_body_filter_decompress_if_needed(ngx_http_request_t *r,
                                                   ngx_http_markdown_ctx_t *ctx,
                                                   const ngx_http_markdown_conf_t *conf)
{
    ngx_chain_t  *compressed_chain = NULL;
    ngx_chain_t  *decompressed_chain = NULL;
    ngx_int_t     decompress_rc;
    ngx_int_t     rc;

    /*
     * Skip decompression entirely when no compressed content was
     * detected (needed == 0) or when decompression already ran
     * (done == 1).  The latter guards against double invocation
     * if the body filter is re-entered.
     */
    if (!ctx->decompression.needed || ctx->decompression.done) {
        return NGX_OK;
    }

    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: starting decompression, "
                  "type=%d, size=%uz bytes",
                  ctx->decompression.type, ctx->buffer.size);

    /*
     * Contiguity assertion: after body-filter accumulation,
     * ctx->buffer.data is always a single contiguous ngx_alloc-
     * backed allocation (Rule 43 invariant).  Log at debug level
     * to verify this invariant holds at runtime.
     */
    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: buffer contiguity "
                  "verified, data=%p, size=%uz "
                  "(single ngx_alloc allocation)",
                  ctx->buffer.data, ctx->buffer.size);

    /*
     * Record the attempt metric and snapshot the compressed size
     * before the decompressor consumes the buffer, because the
     * decompressor may reallocate or replace ctx->buffer.data.
     * Increment decompression_fullbuffer_total at entry to the
     * full-buffer decompression path (Req 4.6).
     */
    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(perf.decompression_fullbuffer_total);
    ctx->decompression.compressed_size = ctx->buffer.size;

    /*
     * Phase 1: wrap ctx->buffer into a single-element chain that
     * the Rust decompressor can consume.  The buffer is already
     * contiguous (Rule 43), so the helper references it directly
     * without a linearize copy.
     */
    rc = ngx_http_markdown_prepare_compressed_chain(
        r, ctx, conf, &compressed_chain);
    if (rc != NGX_OK) {
        return rc;
    }

    /*
     * Phase 2: invoke the bounded Rust FFI decompressor.
     * The decompressor returns domain-specific error codes that
     * classify the failure mode rather than mapping everything
     * to NGX_ERROR, which allows us to pick the correct metric
     * and error category for each failure path.
     */
    decompress_rc = ngx_http_markdown_decompress_via_rust(
        r, ctx, conf, compressed_chain, &decompressed_chain);

    /*
     * Phase 3: result handling and success apply
     */
    rc = ngx_http_markdown_handle_decompress_result(
        r, ctx, conf, decompress_rc);
    if (rc != NGX_OK) {
        return rc;
    }

    rc = ngx_http_markdown_apply_decompressed_payload(
        r, ctx, conf, decompressed_chain);
    if (rc != NGX_OK) {
        return rc;
    }

    ngx_http_markdown_record_decompression_success(r, ctx);
    return NGX_OK;
}

/*
 * Forward deferred response headers to the next filter in the chain.
 *
 * Header forwarding is explicit and idempotent because the module may choose
 * conversion, fail-open, decompression fallback, or streaming fallback after
 * the header filter has deferred output.  The helper restores source
 * Last-Modified metadata when needed, calls the next header filter before any
 * body bytes are sent, and marks `headers_forwarded` only after the downstream
 * filter accepts the headers.
 */
static ngx_int_t
ngx_http_markdown_forward_headers(ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t rc;

    if (ctx == NULL || ctx->headers_forwarded) {
        return NGX_OK;
    }

    if (ctx->last_modified.has_last_modified_time) {
        r->headers_out.last_modified_time = ctx->last_modified.source_last_modified_time;
    }

    rc = ngx_http_next_header_filter(r);
    if (rc == NGX_ERROR || rc > NGX_OK) {
        return rc;
    }

    ctx->headers_forwarded = 1;
    return rc;
}

/*
 * Send the fully buffered original HTML response (fail-open path).
 *
 * This is used when conversion fails after the module has already buffered and
 * consumed the entire upstream body. At that point, forwarding the current
 * input chain would lose data; we must emit the buffered original body.
 */
static ngx_int_t
ngx_http_markdown_send_buffered_original_response(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_buf_t    *b;
    ngx_chain_t  *out;

    if (ctx == NULL) {
        return NGX_ERROR;
    }

    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        return NGX_ERROR;
    }

    if (r->method == NGX_HTTP_HEAD || ctx->buffer.size == 0) {
        b->pos = NULL;
        b->last = NULL;
        b->memory = 0;
    } else {
        b->pos = ctx->buffer.data;
        b->last = ctx->buffer.data + ctx->buffer.size;
        b->memory = 1;
    }

    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = NULL;

    return ngx_http_next_body_filter(r, out);
}

/*
 * Fail-open while buffering is in progress by replaying the already-buffered
 * prefix and then forwarding the unconsumed upstream chain.
 *
 * This preserves correctness when a size limit is exceeded after some chunks
 * were already copied into the module buffer and marked consumed.
 */
static ngx_int_t
ngx_http_markdown_fail_open_with_buffered_prefix(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx, ngx_chain_t *remaining)
{
    ngx_buf_t    *b;
    ngx_chain_t  *out;

    if (ctx == NULL || ctx->buffer.size == 0) {
        return ngx_http_next_body_filter(r, remaining);
    }

    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        return NGX_ERROR;
    }

    if (r->method == NGX_HTTP_HEAD) {
        b->pos = NULL;
        b->last = NULL;
        b->memory = 0;
    } else {
        b->pos = ctx->buffer.data;
        b->last = ctx->buffer.data + ctx->buffer.size;
        b->memory = 1;
    }
    b->last_buf = 0;
    b->last_in_chain = 0;

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = remaining;

    return ngx_http_next_body_filter(r, out);
}

#endif /* NGX_HTTP_MARKDOWN_PAYLOAD_IMPL_H */
