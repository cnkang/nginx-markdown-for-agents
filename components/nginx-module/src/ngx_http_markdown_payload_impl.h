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
    ngx_http_markdown_error_category_t category, const ngx_log_t *log);
ngx_int_t ngx_http_markdown_decompress(ngx_http_request_t *r,
    ngx_http_markdown_compression_type_e type, const ngx_chain_t *in,
    ngx_chain_t **out);
static ngx_int_t ngx_http_markdown_decompress_via_rust(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_chain_t *compressed_chain,
    ngx_chain_t **decompressed_chain);
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
    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;

    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
        return NGX_ERROR;
    }

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
        return NGX_ERROR;
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

    ctx->last_error_category = category;
    ctx->has_error_category = 1;

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


/* Wrap the buffered compressed payload into a chain for the decompressor. */
static ngx_int_t
ngx_http_markdown_prepare_compressed_chain(ngx_http_request_t *r,
                                           ngx_http_markdown_ctx_t *ctx,
                                           const ngx_http_markdown_conf_t *conf,
                                           ngx_chain_t **compressed_chain)
{
    ngx_buf_t *compressed_buf;

    compressed_buf = ngx_create_temp_buf(r->pool, ctx->buffer.size);
    if (compressed_buf == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to create "
                     "compressed buffer, category=system");
        return ngx_http_markdown_handle_decompression_alloc_error(
            r, ctx, conf, NGX_HTTP_MARKDOWN_ERROR_SYSTEM,
            "markdown: fail-open strategy "
            "- returning original content");
    }

    if (ctx->buffer.size > 0) {
        if (ctx->buffer.data == NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: buffered payload "
                         "pointer is NULL with non-zero "
                         "size, size=%uz, category=system",
                         ctx->buffer.size);
            return ngx_http_markdown_handle_decompression_alloc_error(
                r, ctx, conf,
                NGX_HTTP_MARKDOWN_ERROR_SYSTEM, NULL);
        }

        ngx_memcpy(compressed_buf->pos, ctx->buffer.data, ctx->buffer.size);
    }
    compressed_buf->last = compressed_buf->pos + ctx->buffer.size;
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
    const u_char *decompressed_data;
    u_char       *target_data;

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

    ctx->decompression.decompressed_size = decompressed_chain->buf->last - decompressed_chain->buf->pos;
    decompressed_data = decompressed_chain->buf->pos;
    target_data = ctx->buffer.data;

    if (ctx->decompression.decompressed_size > ctx->buffer.capacity) {
        u_char *new_data = ngx_alloc(ctx->decompression.decompressed_size, r->connection->log);
        if (new_data == NULL) {
            const ngx_str_t *compression_name;

            compression_name = ngx_http_markdown_compression_name(
                ctx->decompression.type);

            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: failed to "
                         "allocate decompressed buffer, "
                         "compression=%V, size=%uz, "
                         "category=system",
                         compression_name,
                         ctx->decompression.decompressed_size);

            return ngx_http_markdown_handle_decompression_alloc_error(
                r, ctx, conf,
                NGX_HTTP_MARKDOWN_ERROR_SYSTEM, NULL);
        }

        target_data = new_data;
    }

    if (ctx->decompression.decompressed_size > 0 && target_data != decompressed_data) {
        ngx_memcpy(target_data, decompressed_data, ctx->decompression.decompressed_size);
    }

    if (target_data != ctx->buffer.data) {
        if (ctx->buffer.data != NULL) {
            ngx_free(ctx->buffer.data);
        }

        ctx->buffer.data = target_data;
        ctx->buffer.capacity = ctx->decompression.decompressed_size;
    }

    ctx->buffer.size = ctx->decompression.decompressed_size;
    ctx->decompression.done = 1;
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

    fail_category = ctx->has_error_category
        ? ngx_http_markdown_reason_from_error_category(
              ctx->last_error_category,
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

    attempted_size = (((size_t) -1) - ctx->buffer.size < chunk_size)
        ? (size_t) -1
        : ctx->buffer.size + chunk_size;

    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown: response size exceeds limit, "
                 "size=%uz bytes, max=%uz bytes, category=resource_limit",
                 attempted_size, conf->max_size);

    ctx->last_error_category = NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT;
    ctx->has_error_category = 1;
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
    if (rc != NGX_OK) {
        return rc;
    }

    return NGX_DONE;
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

    chunk_size = cl->buf->last - cl->buf->pos;
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

    if (!ctx->buffer_initialized) {
        rc = ngx_http_markdown_buffer_init(&ctx->buffer, conf->max_size, r->pool);
        if (rc != NGX_OK) {
            return ngx_http_markdown_handle_buffer_init_failure(r, ctx, conf, in);
        }
        ctx->buffer_initialized = 1;

        content_length_n = r->headers_out.content_length_n;
        if (content_length_n > 0 && content_length_n <= (off_t) conf->max_size) {
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
    ngx_http_markdown_ctx_t *ctx,
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
    u_char                *pool_copy;
    ngx_buf_t             *b;
    ngx_chain_t           *cl;
    const ngx_chain_t     *src;

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
     * Linearize the chain into a contiguous buffer.  The chain from
     * prepare_compressed_chain is typically a single buffer, but we
     * handle multi-buffer chains defensively.
     */
    input_size = 0;
    for (src = compressed_chain; src != NULL; src = src->next) {
        if (src->buf != NULL) {
            input_size += (size_t) (src->buf->last - src->buf->pos);
        }
    }

    if (input_size == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: rust decompress "
                     "called with empty input");
        return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
    }

    input_buf = ngx_palloc(r->pool, input_size);
    if (input_buf == NULL) {
        return NGX_ERROR;
    }

    {
        u_char  *dst;

        dst = input_buf;
        for (src = compressed_chain; src != NULL; src = src->next) {
            if (src->buf != NULL) {
                size_t  len;

                len = (size_t) (src->buf->last - src->buf->pos);
                ngx_memcpy(dst, src->buf->pos, len);
                dst += len;
            }
        }
    }

    /* Initialize the result struct before the FFI call. */
    markdown_decomp_result_init(&result);

    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: calling rust "
                  "decompress, format=%d, input=%uz, "
                  "budget=%uz",
                  (int) format, input_size,
                  conf->decompress_max_size);

    ffi_rc = markdown_decompress_bounded(
        (const uint8_t *) input_buf,
        (uintptr_t) input_size,
        format,
        (uintptr_t) conf->decompress_max_size,
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
     */
    if (result.output == NULL || result.output_len == 0) {
        markdown_decompress_free(&result);
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: rust decompress "
                     "returned empty output");
        return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
    }

    pool_copy = ngx_palloc(r->pool, (size_t) result.output_len);
    if (pool_copy == NULL) {
        markdown_decompress_free(&result);
        return NGX_ERROR;
    }

    ngx_memcpy(pool_copy, result.output, (size_t) result.output_len);

    {
        size_t  output_len;

        /* Save length before free resets the struct. */
        output_len = (size_t) result.output_len;

        /* Free the Rust-owned buffer before building the chain. */
        markdown_decompress_free(&result);

        /* Wrap the pool-copied output in an ngx_buf_t / ngx_chain_t. */
        b = ngx_calloc_buf(r->pool);
        if (b == NULL) {
            return NGX_ERROR;
        }

        b->pos = pool_copy;
        b->last = pool_copy + output_len;
        b->memory = 1;
        b->last_buf = 1;

        cl = ngx_alloc_chain_link(r->pool);
        if (cl == NULL) {
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

/* Decompress the buffered payload if compression was detected. */
static ngx_int_t
ngx_http_markdown_body_filter_decompress_if_needed(ngx_http_request_t *r,
                                                   ngx_http_markdown_ctx_t *ctx,
                                                   const ngx_http_markdown_conf_t *conf)
{
    ngx_chain_t  *compressed_chain;
    ngx_chain_t  *decompressed_chain;
    ngx_int_t     decompress_rc;
    ngx_int_t     rc;

    if (!ctx->decompression.needed || ctx->decompression.done) {
        return NGX_OK;
    }

    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: starting decompression, "
                  "type=%d, size=%uz bytes",
                  ctx->decompression.type, ctx->buffer.size);

    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.attempted);
    ctx->decompression.compressed_size = ctx->buffer.size;

    rc = ngx_http_markdown_prepare_compressed_chain(
        r, ctx, conf, &compressed_chain);
    if (rc != NGX_OK) {
        return rc;
    }

    decompress_rc = ngx_http_markdown_decompress_via_rust(
        r, ctx, conf, compressed_chain, &decompressed_chain);

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

    if (decompress_rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total);
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
