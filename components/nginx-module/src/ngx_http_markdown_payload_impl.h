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

static void
ngx_http_markdown_reclassify_fail_open_path(ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL
        || ctx->processing_path != NGX_HTTP_MARKDOWN_PATH_INCREMENTAL)
    {
        return;
    }

    if (ngx_http_markdown_metrics != NULL
        && ngx_http_markdown_metrics->incremental_path_hits > 0)
    {
        NGX_HTTP_MARKDOWN_METRIC_ADD(incremental_path_hits, -1);
    }
    NGX_HTTP_MARKDOWN_METRIC_INC(fullbuffer_path_hits);
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
    ngx_http_markdown_conf_t *conf,
    const char *debug_message)
{
    if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        return NGX_ERROR;
    }

    return ngx_http_markdown_fail_open_buffered_response(r, ctx, debug_message);
}

/* Wrap the buffered compressed payload into a chain for the decompressor. */
static ngx_int_t
ngx_http_markdown_prepare_compressed_chain(ngx_http_request_t *r,
                                           ngx_http_markdown_ctx_t *ctx,
                                           ngx_http_markdown_conf_t *conf,
                                           ngx_chain_t **compressed_chain)
{
    ngx_buf_t *compressed_buf;

    compressed_buf = ngx_create_temp_buf(r->pool, ctx->buffer.size);
    if (compressed_buf == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to create compressed buffer, "
                     "category=system");
        return ngx_http_markdown_reject_or_fail_open_buffered_response(
            r, ctx, conf,
            "markdown filter: fail-open strategy - returning original content");
    }

    if (ctx->buffer.size > 0) {
        if (ctx->buffer.data == NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: buffered payload pointer is NULL with non-zero size, "
                         "size=%uz, category=system",
                         ctx->buffer.size);
            return ngx_http_markdown_reject_or_fail_open_buffered_response(
                r, ctx, conf, NULL);
        }

        ngx_memcpy(compressed_buf->pos, ctx->buffer.data, ctx->buffer.size);
    }
    compressed_buf->last = compressed_buf->pos + ctx->buffer.size;
    compressed_buf->last_buf = 1;

    *compressed_chain = ngx_alloc_chain_link(r->pool);
    if (*compressed_chain == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to allocate chain link, "
                     "category=system");
        return ngx_http_markdown_reject_or_fail_open_buffered_response(
            r, ctx, conf, NULL);
    }

    (*compressed_chain)->buf = compressed_buf;
    (*compressed_chain)->next = NULL;
    return NGX_OK;
}

/* Replace the module buffer contents with the decompressed payload. */
static ngx_int_t
ngx_http_markdown_apply_decompressed_payload(ngx_http_request_t *r,
                                             ngx_http_markdown_ctx_t *ctx,
                                             ngx_http_markdown_conf_t *conf,
                                             ngx_chain_t *decompressed_chain)
{
    const u_char *decompressed_data;
    u_char       *target_data;

    if (decompressed_chain == NULL || decompressed_chain->buf == NULL) {
        const ngx_str_t *compression_name;

        compression_name = ngx_http_markdown_compression_name(
            ctx->compression_type);

        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: decompression returned NULL chain, "
                     "compression=%V, category=system",
                     compression_name);

        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.failed);
        return ngx_http_markdown_reject_or_fail_open_buffered_response(
            r, ctx, conf, NULL);
    }

    ctx->decompressed_size = decompressed_chain->buf->last - decompressed_chain->buf->pos;
    decompressed_data = decompressed_chain->buf->pos;
    target_data = ctx->buffer.data;

    if (ctx->decompressed_size > ctx->buffer.capacity) {
        u_char *new_data = ngx_alloc(ctx->decompressed_size, r->connection->log);
        if (new_data == NULL) {
            const ngx_str_t *compression_name;

            compression_name = ngx_http_markdown_compression_name(
                ctx->compression_type);

            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to allocate decompressed buffer, "
                         "compression=%V, size=%uz, category=system",
                         compression_name, ctx->decompressed_size);

            NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.failed);
            return ngx_http_markdown_reject_or_fail_open_buffered_response(
                r, ctx, conf, NULL);
        }

        target_data = new_data;
    }

    if (ctx->decompressed_size > 0 && target_data != decompressed_data) {
        ngx_memcpy(target_data, decompressed_data, ctx->decompressed_size);
    }

    if (target_data != ctx->buffer.data) {
        if (ctx->buffer.data != NULL) {
            ngx_free(ctx->buffer.data);
        }

        ctx->buffer.data = target_data;
        ctx->buffer.capacity = ctx->decompressed_size;
    }

    ctx->buffer.size = ctx->decompressed_size;
    ctx->decompression_done = 1;
    return NGX_OK;
}

/* Log decompression metrics and strip Content-Encoding after success. */
static void
ngx_http_markdown_record_decompression_success(ngx_http_request_t *r,
                                               ngx_http_markdown_ctx_t *ctx)
{
    float ratio;

    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.succeeded);
    switch (ctx->compression_type) {
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

    ratio = (ctx->compressed_size != 0)
        ? (float) ctx->decompressed_size / (float) ctx->compressed_size
        : 0.0f;

    ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
                 "markdown filter: decompression succeeded, "
                 "compression=%d, compressed=%uz bytes, decompressed=%uz bytes, ratio=%.1fx",
                 ctx->compression_type, ctx->compressed_size, ctx->decompressed_size,
                 ratio);

    ngx_http_markdown_remove_content_encoding(r);
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: removed Content-Encoding header after decompression");
}


/* Handle buffer initialization failure with configured error strategy. */
static ngx_int_t
ngx_http_markdown_handle_buffer_init_failure(ngx_http_request_t *r,
                                             ngx_http_markdown_ctx_t *ctx,
                                             ngx_http_markdown_conf_t *conf,
                                             ngx_chain_t *in)
{
    ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                 "markdown filter: failed to initialize buffer, category=system");

    if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        return NGX_ERROR;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: fail-open strategy - returning original HTML");
    ngx_http_markdown_reclassify_fail_open_path(ctx);
    ctx->eligible = 0;
    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
        return NGX_ERROR;
    }

    return ngx_http_next_body_filter(r, in);
}

/* Handle buffer append failure (size limit exceeded) with error strategy. */
static ngx_int_t
ngx_http_markdown_handle_buffer_append_failure(ngx_http_request_t *r,
                                               ngx_http_markdown_ctx_t *ctx,
                                               ngx_http_markdown_conf_t *conf,
                                               ngx_chain_t *cl,
                                               size_t chunk_size)
{
    size_t attempted_size;

    attempted_size = (((size_t) -1) - ctx->buffer.size < chunk_size)
        ? (size_t) -1
        : ctx->buffer.size + chunk_size;

    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown filter: response size exceeds limit, "
                 "size=%uz bytes, max=%uz bytes, category=resource_limit",
                 attempted_size, conf->max_size);

    if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        return NGX_ERROR;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: fail-open strategy - returning original HTML");
    ctx->eligible = 0;
    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
        return NGX_ERROR;
    }

    return ngx_http_markdown_fail_open_with_buffered_prefix(r, ctx, cl);
}

/* Append one chain link's buffer data into the module accumulation buffer. */
static ngx_int_t
ngx_http_markdown_append_buffered_chunk(ngx_http_request_t *r,
                                        ngx_http_markdown_ctx_t *ctx,
                                        ngx_http_markdown_conf_t *conf,
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
                                           ngx_http_markdown_conf_t *conf)
{
    ngx_chain_t  *cl;
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
                             "markdown filter: failed to pre-reserve %uz bytes buffer capacity",
                             reserve_hint);
            }
        }
    }

    last_buf = 0;
    for (cl = in; cl != NULL; cl = cl->next) {
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

/* Decompress the buffered payload if compression was detected. */
static ngx_int_t
ngx_http_markdown_body_filter_decompress_if_needed(ngx_http_request_t *r,
                                                   ngx_http_markdown_ctx_t *ctx,
                                                   ngx_http_markdown_conf_t *conf)
{
    if (ctx->decompression_needed && !ctx->decompression_done) {
        ngx_chain_t  *compressed_chain;
        ngx_chain_t  *decompressed_chain;
        ngx_int_t     decompress_rc;
        ngx_int_t     rc;

        ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: starting decompression, type=%d, size=%uz bytes",
                      ctx->compression_type, ctx->buffer.size);

        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.attempted);
        ctx->compressed_size = ctx->buffer.size;
        rc = ngx_http_markdown_prepare_compressed_chain(r, ctx, conf, &compressed_chain);
        if (rc != NGX_OK) {
            return rc;
        }

        decompress_rc = ngx_http_markdown_decompress(r, ctx->compression_type,
                                                      compressed_chain,
                                                      &decompressed_chain);

        if (decompress_rc == NGX_DECLINED) {
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown filter: decompression not supported, "
                          "returning original content (fail-open)");

            return ngx_http_markdown_fail_open_buffered_response(r, ctx, NULL);
        }

        if (decompress_rc != NGX_OK) {
            const ngx_str_t *compression_name;

            compression_name = ngx_http_markdown_compression_name(
                ctx->compression_type);

            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: decompression failed, compression=%V, "
                         "error=\"decompression error\", category=conversion",
                         compression_name);

            NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.failed);
            return ngx_http_markdown_reject_or_fail_open_buffered_response(
                r, ctx, conf,
                "markdown filter: fail-open strategy - returning original content");
        }

        rc = ngx_http_markdown_apply_decompressed_payload(r, ctx, conf, decompressed_chain);
        if (rc != NGX_OK) {
            return rc;
        }

        ngx_http_markdown_record_decompression_success(r, ctx);
    }

    return NGX_OK;
}

/* Forward deferred response headers to the next filter in the chain. */
static ngx_int_t
ngx_http_markdown_forward_headers(ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t rc;

    if (ctx == NULL || ctx->headers_forwarded) {
        return NGX_OK;
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
