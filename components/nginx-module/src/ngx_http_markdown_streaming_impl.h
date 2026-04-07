#ifndef NGX_HTTP_MARKDOWN_STREAMING_IMPL_H
#define NGX_HTTP_MARKDOWN_STREAMING_IMPL_H

/*
 * Streaming body filter implementation.
 *
 * WARNING: This header is an implementation detail of the main translation
 * unit (ngx_http_markdown_filter_module.c). It must NOT be included from
 * any other .c file or used as a standalone compilation unit.
 *
 * Implements the streaming conversion path: upstream chunks are
 * incrementally decompressed and fed to the Rust streaming FFI,
 * with Markdown output flushed to downstream as it becomes available.
 */

#ifdef MARKDOWN_STREAMING_ENABLED

#include "ngx_http_markdown_streaming_decomp_impl.h"

/* Streaming reason code string constants */
static ngx_str_t
    ngx_http_markdown_reason_engine_streaming =
        ngx_string("ENGINE_STREAMING");
static ngx_str_t
    ngx_http_markdown_reason_streaming_convert =
        ngx_string("STREAMING_CONVERT");
static ngx_str_t
    ngx_http_markdown_reason_streaming_fallback =
        ngx_string("STREAMING_FALLBACK_PREBUFFER");
static ngx_str_t
    ngx_http_markdown_reason_streaming_fail_postcommit =
        ngx_string("STREAMING_FAIL_POSTCOMMIT");
static ngx_str_t
    ngx_http_markdown_reason_streaming_skip =
        ngx_string("STREAMING_SKIP_UNSUPPORTED");
static ngx_str_t
    ngx_http_markdown_reason_streaming_precommit_failopen =
        ngx_string("STREAMING_PRECOMMIT_FAILOPEN");
static ngx_str_t
    ngx_http_markdown_reason_streaming_precommit_reject =
        ngx_string("STREAMING_PRECOMMIT_REJECT");
static ngx_str_t
    ngx_http_markdown_reason_streaming_budget =
        ngx_string("STREAMING_BUDGET_EXCEEDED");
static ngx_str_t
    ngx_http_markdown_reason_streaming_shadow =
        ngx_string("STREAMING_SHADOW");

/* Forward declarations */
static ngx_int_t
ngx_http_markdown_streaming_body_filter(
    ngx_http_request_t *r, ngx_chain_t *in);
static ngx_int_t
ngx_http_markdown_streaming_process_chunk(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    ngx_buf_t *buf);
static ngx_int_t
ngx_http_markdown_streaming_send_output(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    u_char *data, size_t len,
    ngx_flag_t last_buf);
static ngx_int_t
ngx_http_markdown_streaming_finalize_request(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf);
static ngx_int_t
ngx_http_markdown_streaming_fallback_to_fullbuffer(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf);
static ngx_int_t
ngx_http_markdown_streaming_handle_postcommit_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    uint32_t error_code);
static ngx_int_t
ngx_http_markdown_streaming_precommit_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    uint32_t error_code);
static ngx_int_t
ngx_http_markdown_streaming_handle_backpressure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);
static ngx_int_t
ngx_http_markdown_streaming_resume_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);
static void
ngx_http_markdown_streaming_cleanup(void *data);
static ngx_uint_t
ngx_http_markdown_select_processing_path(
    ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf);
static ngx_int_t
ngx_http_markdown_streaming_update_headers(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf);
static ngx_int_t
ngx_http_markdown_streaming_ensure_handle(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    ngx_chain_t *in);
static ngx_int_t
ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
    ngx_http_request_t *r,
    ngx_chain_t *cl,
    ngx_flag_t last_buf);


/*
 * Pool cleanup handler for streaming resources.
 *
 * Ensures the Rust streaming handle is released even if
 * the request terminates abnormally (client abort, timeout).
 */
static void
ngx_http_markdown_streaming_cleanup(void *data)
{
    ngx_http_markdown_ctx_t *ctx = data;

    if (ctx == NULL) {
        return;
    }

    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    /*
     * Pending output chains that hold Rust-allocated buffers
     * would leak if not freed. Walk the chain and release
     * each Rust buffer.
     */
    {
        ngx_chain_t  *cl;

        for (cl = ctx->streaming.pending_output;
             cl != NULL; cl = cl->next)
        {
            if (cl->buf != NULL
                && cl->buf->pos != NULL
                && cl->buf->temporary)
            {
                markdown_streaming_output_free(
                    cl->buf->pos,
                    cl->buf->last - cl->buf->pos);
                cl->buf->pos = NULL;
                cl->buf->last = NULL;
            }
        }
        ctx->streaming.pending_output = NULL;
    }
}


/*
 * Check whether the content type matches any stream_types
 * exclusion entry.
 *
 * Returns:
 *   1 if the content type matches an exclusion entry
 *   0 otherwise
 */
static ngx_int_t
ngx_http_markdown_is_excluded_stream_type(
    ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf)
{
    ngx_str_t   *types;
    ngx_uint_t   i;

    if (conf->stream_types == NULL) {
        return 0;
    }

    types = conf->stream_types->elts;
    for (i = 0; i < conf->stream_types->nelts; i++) {
        if (r->headers_out.content_type.len
                >= types[i].len
            && ngx_strncasecmp(
                   r->headers_out.content_type.data,
                   types[i].data,
                   types[i].len) == 0)
        {
            return 1;
        }
    }

    return 0;
}


/*
 * Log conditional_requests streaming decision for
 * observability.  Called only for modes that allow
 * streaming (if_modified_since_only and disabled).
 */
static void
ngx_http_markdown_log_conditional_streaming(
    ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf)
{
    if (conf->conditional_requests
        == NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE)
    {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown filter: streaming allowed: "
            "conditional_requests "
            "if_modified_since_only");
    } else if (conf->conditional_requests
               == NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED)
    {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown filter: streaming allowed: "
            "conditional_requests disabled");
    }
}


/*
 * Engine selector: determine the processing path for a request.
 *
 * Evaluates the markdown_streaming_engine complex value once
 * in the header filter phase and caches the result.
 *
 * Evaluation order (per design doc):
 * 1. engine == off -> PATH_FULLBUFFER
 * 2. streaming feature not compiled -> warn + PATH_FULLBUFFER
 * 3. HEAD request -> PATH_FULLBUFFER
 * 4. 304 Not Modified -> PATH_FULLBUFFER
 * 5. conditional_requests full_support -> PATH_FULLBUFFER
 * 6. Content-Type is text/event-stream -> PATH_FULLBUFFER
 * 7. stream_types exclusion match -> PATH_FULLBUFFER
 * 8. engine == on -> PATH_STREAMING
 * 9. engine == auto + CL >= threshold -> PATH_STREAMING
 * 10. engine == auto + no CL -> PATH_STREAMING
 * 11. engine == auto + CL < threshold -> fall through
 */
static ngx_uint_t
ngx_http_markdown_select_processing_path(
    ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf)
{
    ngx_str_t    val;
    ngx_uint_t   engine_mode;

    if (conf == NULL || conf->streaming_engine == NULL) {
        return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    }

    /* Evaluate the complex value */
    if (ngx_http_complex_value(r, conf->streaming_engine,
                               &val) != NGX_OK)
    {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
            "markdown filter: failed to evaluate "
            "markdown_streaming_engine, "
            "falling back to full-buffer");
        return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    }

    /* Parse the evaluated string */
    {
        static u_char  str_off[]  = "off";
        static u_char  str_on[]   = "on";
        static u_char  str_auto[] = "auto";

        if (val.len == 3
            && ngx_strncasecmp(val.data, str_off,
                               3) == 0)
        {
            return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
        }

        if (val.len == 2
            && ngx_strncasecmp(val.data, str_on,
                               2) == 0)
        {
            engine_mode =
                NGX_HTTP_MARKDOWN_STREAMING_ENGINE_ON;

        } else if (val.len == 4
                   && ngx_strncasecmp(val.data,
                                      str_auto,
                                      4) == 0)
        {
            engine_mode =
                NGX_HTTP_MARKDOWN_STREAMING_ENGINE_AUTO;

        } else {
            ngx_log_error(NGX_LOG_WARN,
                r->connection->log, 0,
                "markdown filter: invalid "
                "markdown_streaming_engine value "
                "\"%V\", falling back to off", &val);
            return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
        }
    }

    /* Rule 3: HEAD request */
    if (r->method == NGX_HTTP_HEAD) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown filter: streaming skip: "
            "HEAD request");
        return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    }

    /* Rule 4: 304 Not Modified */
    if (r->headers_out.status == NGX_HTTP_NOT_MODIFIED) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown filter: streaming skip: "
            "304 response");
        return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    }

    /* Rule 5: conditional_requests full_support */
    if (conf->conditional_requests
        == NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT)
    {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown filter: streaming skip: "
            "conditional_requests full_support "
            "requires full ETag before headers");
        return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    }

    /* Rule 5b: log streaming-allowed decision */
    ngx_http_markdown_log_conditional_streaming(r, conf);

    /* Rule 6: text/event-stream */
    if (r->headers_out.content_type.len >= 17
        && ngx_strncasecmp(
               r->headers_out.content_type.data,
               (u_char *) "text/event-stream",
               17) == 0)
    {
        return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    }

    /* Rule 7: stream_types exclusion list */
    if (ngx_http_markdown_is_excluded_stream_type(
            r, conf))
    {
        return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    }

    /* Rule 8: engine == on */
    if (engine_mode
        == NGX_HTTP_MARKDOWN_STREAMING_ENGINE_ON)
    {
        return NGX_HTTP_MARKDOWN_PATH_STREAMING;
    }

    /* Rules 9-11: engine == auto */
    if (r->headers_out.content_length_n >= 0
        && conf->large_body_threshold > 0
        && (size_t) r->headers_out.content_length_n
           < conf->large_body_threshold)
    {
        /* CL < threshold: let threshold router decide */
        return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    }

    /* auto + CL >= threshold or no CL */
    return NGX_HTTP_MARKDOWN_PATH_STREAMING;
}


/*
 * Update response headers at the commit boundary.
 *
 * Sets Content-Type to text/markdown, adds Vary: Accept,
 * removes Content-Length and Content-Encoding.
 */
static ngx_int_t
ngx_http_markdown_streaming_update_headers(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    /* Set Content-Type: text/markdown; charset=utf-8 */
    r->headers_out.content_type.data =
        ngx_http_markdown_content_type;
    r->headers_out.content_type.len =
        sizeof(ngx_http_markdown_content_type) - 1;
    r->headers_out.content_type_len =
        r->headers_out.content_type.len;
    r->headers_out.charset.len = 0;
    r->headers_out.charset.data = NULL;

    /* Add Vary: Accept */
    rc = ngx_http_markdown_add_vary_accept(r);
    if (rc != NGX_OK) {
        return NGX_ERROR;
    }

    /* Remove Content-Length (streaming = chunked) */
    ngx_http_clear_content_length(r);
    r->headers_out.content_length_n = -1;

    /* Remove Content-Encoding if decompressing */
    if (ctx->decompression.needed) {
        ngx_http_markdown_remove_content_encoding(r);
    }

    /*
     * Remove any upstream ETag header.
     *
     * In streaming mode the Markdown ETag is computed
     * incrementally via BLAKE3 and is only available
     * after finalize().  An upstream HTML ETag would be
     * stale and semantically wrong for the transformed
     * Markdown body.  Clear it unconditionally so the
     * response never carries a mismatched ETag.
     */
    rc = ngx_http_markdown_set_etag(r, NULL, 0);
    if (rc != NGX_OK) {
        return NGX_ERROR;
    }

    /* Enable chunked transfer encoding */
    r->chunked = 1;

    return NGX_OK;
}


/*
 * Send Markdown output downstream.
 *
 * Constructs an output chain from the provided data and
 * passes it to the next body filter. Sets flush and
 * last_buf flags as appropriate.
 */
static ngx_int_t
ngx_http_markdown_streaming_send_output(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    u_char *data, size_t len,
    ngx_flag_t last_buf)
{
    ngx_buf_t    *b;
    ngx_chain_t  *out;
    ngx_int_t     rc;

    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        return NGX_ERROR;
    }

    if (data != NULL && len > 0) {
        /*
         * Copy Rust-allocated data into pool memory so we
         * can free the Rust buffer immediately.
         */
        b->pos = ngx_palloc(r->pool, len);
        if (b->pos == NULL) {
            return NGX_ERROR;
        }
        ngx_memcpy(b->pos, data, len);
        b->last = b->pos + len;
        b->memory = 1;
    }

    b->flush = last_buf ? 0 : 1;
    b->last_buf = (last_buf && r == r->main) ? 1 : 0;
    b->last_in_chain = last_buf ? 1 : 0;

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = NULL;

    rc = ngx_http_next_body_filter(r, out);

    if (rc == NGX_OK || rc == NGX_DONE) {
        ctx->streaming.flushes_sent++;
    }

    if (rc == NGX_AGAIN) {
        /*
         * Keep exactly one outstanding pending chain owned by this
         * request context. The retry path (resume_pending) will submit
         * this same chain again once downstream write readiness returns.
         */
        ctx->streaming.pending_output = out;
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
    }

    return rc;
}


/*
 * Handle backpressure: save pending output and set buffered flag.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_backpressure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown streaming: backpressure detected, "
        "pausing output");

    return NGX_AGAIN;
}


static ngx_int_t
ngx_http_markdown_streaming_send_deferred_lastbuf(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    ctx->streaming.finalize_pending_lastbuf = 0;
    rc = ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);

    if (rc == NGX_AGAIN) {
        return ngx_http_markdown_streaming_handle_backpressure(
            r, ctx);
    }

    return rc;
}


/*
 * Resume sending pending output after backpressure clears.
 */
static ngx_int_t
ngx_http_markdown_streaming_resume_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_chain_t  *out;
    ngx_int_t     rc;

    out = ctx->streaming.pending_output;
    if (out == NULL) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;

        /*
         * If finalize deferred the terminal last_buf due to
         * backpressure, send it now that the pending output
         * has been drained.
         */
        if (ctx->streaming.finalize_pending_lastbuf) {
            return ngx_http_markdown_streaming_send_deferred_lastbuf(
                r, ctx);
        }

        return NGX_OK;
    }

    ctx->streaming.pending_output = NULL;
    /*
     * Retry the exact buffered chain first; only after it drains do we
     * allow finalize() to emit a terminal empty last_buf.
     */
    rc = ngx_http_next_body_filter(r, out);

    if (rc == NGX_AGAIN) {
        ctx->streaming.pending_output = out;
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
        return NGX_AGAIN;
    }

    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;

    /*
     * Pending output drained. If finalize deferred the
     * terminal last_buf, send it now.
     */
    if (ctx->streaming.finalize_pending_lastbuf) {
        return ngx_http_markdown_streaming_send_deferred_lastbuf(
            r, ctx);
    }

    return rc;
}


/*
 * Pre-Commit fallback to full-buffer path.
 *
 * Releases the streaming handle and passes already-
 * decompressed data to the full-buffer conversion path.
 */
static ngx_int_t
ngx_http_markdown_streaming_fallback_to_fullbuffer(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf)
{
    ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
        "markdown streaming: Pre-Commit fallback "
        "to full-buffer path");

    /* Release the streaming handle */
    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    /* Switch to full-buffer path */
    ctx->processing_path =
        NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    ctx->streaming.failopen_consumed_count = 0;

    /*
     * Clear conversion_attempted so the full-buffer body
     * filter path will actually perform the conversion on
     * the prebuffered data. Without this reset the main
     * body filter sees conversion_attempted == 1 and
     * short-circuits to ngx_http_next_body_filter,
     * forwarding unconverted HTML.
     */
    ctx->conversion_attempted = 0;

    /* Correct path hit metrics */
    if (ngx_http_markdown_metrics != NULL
        && ngx_http_markdown_metrics->path_hits.streaming
           > 0)
    {
        NGX_HTTP_MARKDOWN_METRIC_ADD(
            path_hits.streaming, -1);
    }
    NGX_HTTP_MARKDOWN_METRIC_INC(path_hits.fullbuffer);
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.fallback_total);

    ngx_http_markdown_log_decision(r, conf,
        &ngx_http_markdown_reason_streaming_fallback);

    /*
     * Transfer prebuffer data to the main buffer for
     * full-buffer conversion. The prebuffer contains
     * already-decompressed upstream data.
     */
    if (ctx->streaming.prebuffer_initialized
        && ctx->streaming.prebuffer.size > 0)
    {
        ngx_int_t  rc;

        if (!ctx->buffer_initialized) {
            rc = ngx_http_markdown_buffer_init(
                &ctx->buffer, conf->max_size, r->pool);
            if (rc != NGX_OK) {
                return NGX_ERROR;
            }
            ctx->buffer_initialized = 1;
        }

        rc = ngx_http_markdown_buffer_append(
            &ctx->buffer,
            ctx->streaming.prebuffer.data,
            ctx->streaming.prebuffer.size);
        if (rc != NGX_OK) {
            return NGX_ERROR;
        }

        /* Mark decompression as done since prebuffer
         * already holds decompressed data */
        ctx->decompression.done = 1;
    }

    return NGX_DECLINED;
}


/*
 * Post-Commit error handler.
 *
 * After headers have been sent, we cannot switch back to HTML
 * or send an HTTP error status. We abort the Rust handle and
 * send an empty last_buf to terminate the response.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_postcommit_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    uint32_t error_code)
{
    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
        "markdown streaming: Post-Commit error, "
        "code=%ui, terminating response",
        (ngx_uint_t) error_code);

    /* Abort the Rust handle */
    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    /* Record metrics */
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.postcommit_error_total);
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.failed_total);

    /* Track budget exceeded as auxiliary classification */
    if (error_code == ERROR_MEMORY_LIMIT) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.budget_exceeded_total);
    }

    /* Record decision log */
    ngx_http_markdown_log_decision(r, conf,
        &ngx_http_markdown_reason_streaming_fail_postcommit);

    /*
     * Debug log: bytes already sent, error type, chunks
     * processed. Always emitted regardless of
     * streaming_on_error (post-commit is unconditional).
     */
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown streaming: post-commit error, "
        "bytes_sent=%uz, error_code=%ui, chunks=%ui",
        ctx->streaming.total_output_bytes,
        (ngx_uint_t) error_code,
        ctx->streaming.chunks_processed);

    /* Send empty last_buf to terminate the response */
    return ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);
}


/*
 * Pre-Commit error handler: apply streaming_on_error policy.
 *
 * Single entry point for all pre-commit streaming failures.
 * Routes based on error_code and the markdown_streaming_on_error
 * directive (not the full-buffer markdown_on_error).
 *
 *   error_code == ERROR_STREAMING_FALLBACK:
 *     Capability fallback to full-buffer path, regardless of
 *     streaming_on_error setting.
 *
 *   error_code == 0 (or any non-FALLBACK value):
 *     streaming_on_error == pass  -> fail-open (original HTML)
 *     streaming_on_error == reject -> fail-closed (error)
 *
 * Every non-FALLBACK call unconditionally records the appropriate
 * reason code and increments the corresponding metrics counter so
 * that all pre-commit failures are observable by operators.
 *
 * Returns:
 *   NGX_DECLINED - fallback to full-buffer or fail-open
 *   NGX_ERROR    - fail-closed (reject)
 *
 * The caller must NOT advance the buffer position when NGX_DECLINED
 * is returned so that the body filter can forward the unconsumed
 * chain via ngx_http_next_body_filter.
 */
static ngx_int_t
ngx_http_markdown_streaming_precommit_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    uint32_t error_code)
{
    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    if (error_code == ERROR_STREAMING_FALLBACK) {
        /*
         * Capability fallback: always fall back to full-buffer
         * regardless of streaming_on_error setting.
         */
        return ngx_http_markdown_streaming_fallback_to_fullbuffer(
            r, ctx, conf);
    }

    /*
     * Track budget exceeded as auxiliary classification.
     * The terminal state is determined by streaming_on_error
     * policy below.
     */
    if (error_code == ERROR_MEMORY_LIMIT) {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.budget_exceeded_total);
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown streaming: budget exceeded "
            "(auxiliary classification)");
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.failed_total);

    if (conf->streaming_on_error
        == NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT)
    {
        /* Fail-closed: record reject metrics and reason */
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.precommit_reject_total);
        ngx_http_markdown_log_decision(r, conf,
            &ngx_http_markdown_reason_streaming_precommit_reject);
        return NGX_ERROR;
    }

    /* Fail-open: pass original content */
    ctx->eligible = 0;
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.precommit_failopen_total);
    NGX_HTTP_MARKDOWN_METRIC_INC(failopen_count);
    ngx_http_markdown_log_decision(r, conf,
        &ngx_http_markdown_reason_streaming_precommit_failopen);
    return NGX_DECLINED;
}


/*
 * Commit boundary: update headers and send them downstream.
 *
 * Called when the first non-empty output is produced in
 * Pre-Commit state. Transitions to Post-Commit on success.
 *
 * Returns:
 *   NGX_OK    on success (commit state updated)
 *   NGX_ERROR on header update or filter failure
 */
static ngx_int_t
ngx_http_markdown_streaming_commit(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_streaming_update_headers(
        r, ctx, conf);
    if (rc != NGX_OK) {
        return NGX_ERROR;
    }

    rc = ngx_http_next_header_filter(r);
    if (rc == NGX_ERROR || rc > NGX_OK) {
        return rc;
    }

    ctx->streaming.commit_state =
        NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx->streaming.failopen_consumed_count = 0;
    ctx->headers_forwarded = 1;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown streaming: commit "
        "boundary reached, headers sent");

    return NGX_OK;
}


/*
 * Handle the result of a streaming feed call.
 *
 * Dispatches to fallback, post-commit error, pre-commit
 * error, or output sending based on the FFI return code
 * and current commit state.
 *
 * Returns:
 *   NGX_OK, NGX_AGAIN, NGX_ERROR, or NGX_DECLINED
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_feed_result(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    uint32_t rc_ffi,
    u_char *out_data,
    size_t out_len)
{
    ngx_int_t  rc;

    if (rc_ffi == ERROR_STREAMING_FALLBACK) {
        if (out_data != NULL) {
            markdown_streaming_output_free(
                out_data, out_len);
        }
        return
            ngx_http_markdown_streaming_fallback_to_fullbuffer(
                r, ctx, conf);
    }

    if (rc_ffi != ERROR_SUCCESS) {
        if (out_data != NULL) {
            markdown_streaming_output_free(
                out_data, out_len);
        }

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return
                ngx_http_markdown_streaming_handle_postcommit_error(
                    r, ctx, conf, rc_ffi);
        }

        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown streaming: feed error "
            "code=%ui in Pre-Commit",
            (ngx_uint_t) rc_ffi);

        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, rc_ffi);
    }

    /* Success: handle output */
    if (out_data != NULL && out_len > 0) {
        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE)
        {
            rc = ngx_http_markdown_streaming_commit(
                r, ctx, conf);
            if (rc != NGX_OK) {
                markdown_streaming_output_free(
                    out_data, out_len);
                return rc;
            }
        }

        ctx->streaming.total_output_bytes += out_len;

        rc = ngx_http_markdown_streaming_send_output(
            r, ctx, out_data, out_len, /* last_buf */ 0);

        markdown_streaming_output_free(
            out_data, out_len);

        if (rc == NGX_AGAIN) {
            return ngx_http_markdown_streaming_handle_backpressure(
                r, ctx);
        }

        return rc;
    }

    /* Empty output: free if non-NULL */
    if (out_data != NULL) {
        markdown_streaming_output_free(
            out_data, out_len);
    }

    return NGX_OK;
}


/*
 * Process a single upstream chunk through the streaming pipeline.
 *
 * Steps:
 * 1. Decompress (if needed)
 * 2. Track cumulative input size (size limit check)
 * 3. Save to prebuffer (Pre-Commit only)
 * 4. Feed to Rust streaming engine
 * 5. Handle output / errors based on commit state
 */
static ngx_int_t
ngx_http_markdown_streaming_process_chunk(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    ngx_buf_t *buf)
{
    u_char    *feed_data;
    size_t     feed_len;
    u_char    *out_data;
    size_t     out_len;
    uint32_t   rc_ffi;
    ngx_int_t  rc;

    if (buf == NULL) {
        return NGX_OK;
    }

    feed_data = buf->pos;
    feed_len = buf->last - buf->pos;

    if (feed_len == 0) {
        return NGX_OK;
    }

    /* Step 1: Decompress if needed */
    if (ctx->decompression.needed
        && ctx->streaming.decompressor != NULL)
    {
        u_char  *decomp_data;
        size_t   decomp_len;

        rc = ngx_http_markdown_streaming_decomp_feed(
            (ngx_http_markdown_streaming_decomp_t *)
                ctx->streaming.decompressor,
            feed_data, feed_len,
            &decomp_data, &decomp_len,
            r->pool, r->connection->log);

        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown streaming: "
                "decompression failed");

            if (ctx->streaming.commit_state
                == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
            {
                return
                    ngx_http_markdown_streaming_handle_postcommit_error(
                        r, ctx, conf, ERROR_INTERNAL);
            }

            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, 0);
        }

        if (decomp_data == NULL || decomp_len == 0) {
            return NGX_OK;
        }

        feed_data = decomp_data;
        feed_len = decomp_len;
    }

    /* Step 2: Track cumulative input size */
    if (feed_len > (size_t) -1
                   - ctx->streaming.total_input_bytes)
    {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown streaming: input size "
            "overflow detected");

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return
                ngx_http_markdown_streaming_handle_postcommit_error(
                    r, ctx, conf, ERROR_MEMORY_LIMIT);
        }

        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, 0);
    }

    ctx->streaming.total_input_bytes += feed_len;
    if (conf->max_size > 0
        && ctx->streaming.total_input_bytes
           > conf->max_size)
    {
        ngx_log_error(NGX_LOG_WARN,
            r->connection->log, 0,
            "markdown streaming: size limit "
            "exceeded, total=%uz, max=%uz",
            ctx->streaming.total_input_bytes,
            conf->max_size);

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return
                ngx_http_markdown_streaming_handle_postcommit_error(
                    r, ctx, conf, ERROR_MEMORY_LIMIT);
        }

        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, 0);
    }

    /* Step 3: Save to prebuffer (Pre-Commit only) */
    if (ctx->streaming.commit_state
        == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE
        && ctx->streaming.prebuffer_initialized)
    {
        rc = ngx_http_markdown_buffer_append(
            &ctx->streaming.prebuffer,
            feed_data, feed_len);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_WARN,
                r->connection->log, 0,
                "markdown streaming: prebuffer "
                "limit exceeded");

            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, 0);
        }
    }

    /* Step 4: Feed to Rust streaming engine */
    out_data = NULL;
    out_len = 0;

    rc_ffi = markdown_streaming_feed(
        ctx->streaming.handle,
        feed_data, feed_len,
        &out_data, &out_len);

    ctx->streaming.chunks_processed++;

    /* Step 5: Handle result */
    return ngx_http_markdown_streaming_handle_feed_result(
        r, ctx, conf, rc_ffi, out_data, out_len);
}


/*
 * Finish decompression and feed any tail data to the
 * streaming engine.
 *
 * Returns:
 *   NGX_OK    - success (or no decompression needed)
 *   other     - error propagated from decomp or feed
 */
static ngx_int_t
ngx_http_markdown_streaming_finalize_decomp(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf)
{
    u_char    *decomp_data;
    size_t     decomp_len;
    ngx_int_t  rc;

    if (!ctx->decompression.needed
        || ctx->streaming.decompressor == NULL)
    {
        return NGX_OK;
    }

    rc = ngx_http_markdown_streaming_decomp_finish(
        (ngx_http_markdown_streaming_decomp_t *)
            ctx->streaming.decompressor,
        &decomp_data, &decomp_len,
        r->pool, r->connection->log);

    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown streaming: decomp_finish "
            "failed in finalize");

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return
                ngx_http_markdown_streaming_handle_postcommit_error(
                    r, ctx, conf,
                    ERROR_POST_COMMIT);
        }

        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, 0);
    }

    if (decomp_data != NULL && decomp_len > 0) {
        u_char    *out_data;
        size_t     out_len;
        uint32_t   feed_rc;
        ngx_int_t  feed_result;

        out_data = NULL;
        out_len = 0;

        feed_rc = markdown_streaming_feed(
            ctx->streaming.handle,
            decomp_data, decomp_len,
            &out_data, &out_len);

        feed_result =
            ngx_http_markdown_streaming_handle_feed_result(
                r, ctx, conf, feed_rc,
                out_data, out_len);

        if (feed_result != NGX_OK) {
            return feed_result;
        }
    }

    return NGX_OK;
}


/*
 * Finalize the streaming conversion on last_buf.
 *
 * Calls markdown_streaming_finalize() to get the final
 * Markdown output and result metadata, then sends the
 * final chunk downstream.
 */
static ngx_int_t
ngx_http_markdown_streaming_finalize_request(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf)
{
    struct MarkdownResult  result;
    uint32_t               rc_ffi;
    ngx_int_t              rc;
    ngx_int_t              final_send_rc;

    if (ctx->streaming.handle == NULL) {
        return ngx_http_markdown_streaming_send_output(
            r, ctx, NULL, 0, /* last_buf */ 1);
    }

    ctx->streaming.finalize_after_pending = 0;

    /* Finish decompression and feed tail data if any */
    rc = ngx_http_markdown_streaming_finalize_decomp(
        r, ctx, conf);
    if (rc == NGX_AGAIN) {
        ctx->streaming.finalize_after_pending = 1;
        return NGX_AGAIN;
    }
    if (rc != NGX_OK) {
        return rc;
    }

    /* Finalize the streaming converter */
    ngx_memzero(&result, sizeof(struct MarkdownResult));

    rc_ffi = markdown_streaming_finalize(
        ctx->streaming.handle, &result);

    /* Handle is consumed by finalize regardless of result */
    ctx->streaming.handle = NULL;

    if (rc_ffi != ERROR_SUCCESS) {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown streaming: finalize error "
            "code=%ui", (ngx_uint_t) rc_ffi);

        markdown_result_free(&result);

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                streaming.postcommit_error_total);
            NGX_HTTP_MARKDOWN_METRIC_INC(
                streaming.failed_total);
            ngx_http_markdown_log_decision(r, conf,
                &ngx_http_markdown_reason_streaming_fail_postcommit);
            return ngx_http_markdown_streaming_send_output(
                r, ctx, NULL, 0, /* last_buf */ 1);
        }

        /* Pre-Commit finalize error follows configured policy */
        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, 0);
    }

    /* Send final Markdown output if any */
    if (result.markdown != NULL
        && result.markdown_len > 0)
    {
        ctx->streaming.total_output_bytes +=
            result.markdown_len;

        /* If still Pre-Commit, send headers first */
        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE)
        {
            rc = ngx_http_markdown_streaming_commit(
                r, ctx, conf);
            if (rc != NGX_OK && rc != NGX_AGAIN) {
                markdown_result_free(&result);
                return rc;
            }
        }

        rc = ngx_http_markdown_streaming_send_output(
            r, ctx,
            result.markdown,
            result.markdown_len,
            /* last_buf */ 0);

        if (rc != NGX_OK && rc != NGX_AGAIN) {
            markdown_result_free(&result);
            return rc;
        }

        final_send_rc = rc;
    } else {
        final_send_rc = NGX_OK;
    }

    /* Log ETag if available (debug observability) */
    if (result.etag != NULL && result.etag_len > 0) {
        ngx_log_debug3(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown streaming: finalize ETag "
            "value=\"%*s\", len=%uz",
            result.etag_len, result.etag,
            result.etag_len);

        /*
         * Record ETag in the decision/metrics log.
         *
         * In streaming mode the ETag cannot appear in
         * response headers (sent at Commit_Boundary),
         * so we log it here for operator observability,
         * debug correlation, and future cache-layer use.
         */
        ngx_log_error(NGX_LOG_INFO,
            r->connection->log, 0,
            "markdown streaming: etag=%*s "
            "uri=%V "
            "out_bytes=%uz tokens=%ui",
            result.etag_len, result.etag,
            &r->uri,
            ctx->streaming.total_output_bytes,
            (ngx_uint_t) result.token_estimate);
    }

    markdown_result_free(&result);

    /* Log streaming conversion statistics */
    ngx_log_debug4(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown streaming: completed "
        "chunks=%ui flushes=%ui "
        "in_bytes=%uz out_bytes=%uz",
        ctx->streaming.chunks_processed,
        ctx->streaming.flushes_sent,
        ctx->streaming.total_input_bytes,
        ctx->streaming.total_output_bytes);

    /* Record success metrics */
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.succeeded_total);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_succeeded);

    /*
     * Update streaming gauge metrics.
     * TTFB is approximated as the time from first feed to
     * finalize completion (stored in milliseconds, converted
     * to microseconds for the gauge).
     */
    if (ctx->streaming.feed_start_ms > 0) {
        ngx_time_t  *tp_now;
        ngx_msec_t   now_ms;
        ngx_msec_t   elapsed_ms;

        tp_now = ngx_timeofday();
        now_ms = (ngx_msec_t) (tp_now->sec * 1000
            + tp_now->msec);
        elapsed_ms = (now_ms >= ctx->streaming.feed_start_ms)
            ? (now_ms - ctx->streaming.feed_start_ms) : 0;

        ngx_http_markdown_metrics->streaming.last_ttfb_us =
            (ngx_atomic_t) (elapsed_ms * 1000);
    }

    ngx_http_markdown_log_decision(r, conf,
        &ngx_http_markdown_reason_streaming_convert);

    /*
     * Send final empty last_buf to terminate the response.
     *
     * If the final markdown send returned NGX_AGAIN (backpressure),
     * defer the terminal last_buf — sending it now would overwrite
     * the pending markdown chain in send_output, causing data loss.
     * Return NGX_AGAIN so the resume mechanism drains the pending
     * output first; the caller will re-enter finalize on the next
     * write event.
     */
    if (final_send_rc == NGX_AGAIN) {
        ctx->streaming.finalize_pending_lastbuf = 1;
        return ngx_http_markdown_streaming_handle_backpressure(
            r, ctx);
    }

    return ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);
}


/*
 * Initialize the streaming handle, decompressor, and prebuffer.
 *
 * Called on the first body filter invocation when the handle
 * is NULL and the request is eligible.
 *
 * Returns:
 *   NGX_OK       - handle created successfully
 *   NGX_ERROR    - fatal error (reject policy)
 *   NGX_DECLINED - non-fatal init failure (eligible cleared)
 */
static ngx_int_t
ngx_http_markdown_streaming_init_handle(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf)
{
    struct MarkdownOptions  options;
    ngx_pool_cleanup_t     *cln;
    ngx_int_t               rc;

    ngx_memzero(&options, sizeof(struct MarkdownOptions));

    rc = ngx_http_markdown_prepare_conversion_options(
        r, conf, &options);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown streaming: failed to "
            "prepare conversion options");
        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, 0);
    }

    ctx->streaming.handle =
        markdown_streaming_new(&options);
    if (ctx->streaming.handle == NULL) {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown streaming: failed to "
            "create streaming handle");
        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, 0);
    }

    /* Record feed start time for TTFB gauge */
    {
        ngx_time_t  *tp;

        tp = ngx_timeofday();
        ctx->streaming.feed_start_ms =
            (ngx_msec_t) (tp->sec * 1000 + tp->msec);
    }

    /* Register cleanup handler */
    cln = ngx_pool_cleanup_add(r->pool, 0);
    if (cln == NULL) {
        markdown_streaming_abort(
            ctx->streaming.handle);
        ctx->streaming.handle = NULL;
        return NGX_ERROR;
    }
    cln->handler =
        ngx_http_markdown_streaming_cleanup;
    cln->data = ctx;

    /* Initialize decompressor if needed */
    if (ctx->decompression.needed) {
        ctx->streaming.decompressor =
            ngx_http_markdown_streaming_decomp_create(
                r->pool,
                ctx->decompression.type,
                conf->max_size);
        if (ctx->streaming.decompressor == NULL) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown streaming: failed to "
                "create decompressor");
            markdown_streaming_abort(
                ctx->streaming.handle);
            ctx->streaming.handle = NULL;
            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, 0);
        }
    }

    /* Initialize prebuffer for fallback */
    ctx->streaming.prebuffer_limit =
        conf->streaming_budget;
    rc = ngx_http_markdown_buffer_init(
        &ctx->streaming.prebuffer,
        ctx->streaming.prebuffer_limit,
        r->pool);
    if (rc == NGX_OK) {
        ctx->streaming.prebuffer_initialized = 1;
    }

    ctx->streaming.commit_state =
        NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx->streaming.failopen_consumed_count = 0;

    ctx->conversion_attempted = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(
        conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.requests_total);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown streaming: handle created, "
        "entering Pre-Commit phase");

    return NGX_OK;
}


/*
 * Handle the result of process_chunk within the body filter loop.
 *
 * Dispatches fallback, fail-open, and error paths. Returns the
 * value the body filter should return, or NGX_OK to continue
 * processing the next buffer in the chain.
 *
 * Returns:
 *   NGX_OK       - continue processing next buffer
 *   NGX_AGAIN    - backpressure, return immediately
 *   NGX_ERROR    - fatal error
 *   other        - return value to propagate
 */
static ngx_int_t
ngx_http_markdown_streaming_failopen_passthrough(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *in,
    ngx_uint_t invocation_start)
{
    ngx_chain_t *head;
    ngx_chain_t **tail;
    ngx_chain_t *cl;
    ngx_uint_t  i;

    /*
     * Fail-open must preserve the original upstream bytes. If earlier chunks in
     * this chain were already marked consumed, restore their positions before
     * forwarding the chain downstream.
     */
    for (i = 0; i < ctx->streaming.failopen_consumed_count; i++) {
        ctx->streaming.failopen_consumed_bufs[i]->pos =
            ctx->streaming.failopen_consumed_pos[i];
    }

    if (!ctx->headers_forwarded) {
        if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
            return NGX_ERROR;
        }
    }

    if (invocation_start == 0) {
        return ngx_http_next_body_filter(r, in);
    }

    /*
     * Previous invocations may already have consumed pre-commit buffers that are
     * not part of the current `in` chain. Rebuild a prefix chain from durable
     * per-request bookkeeping so fail-open can forward the full original body.
     */
    head = NULL;
    tail = &head;
    for (i = 0; i < invocation_start; i++) {
        cl = ngx_alloc_chain_link(r->pool);
        if (cl == NULL) {
            return NGX_ERROR;
        }
        cl->buf = ctx->streaming.failopen_consumed_bufs[i];
        cl->next = NULL;
        *tail = cl;
        tail = &cl->next;
    }
    *tail = in;

    return ngx_http_next_body_filter(r, head);
}


static ngx_int_t
ngx_http_markdown_streaming_handle_chunk_result(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *in,
    ngx_int_t rc,
    ngx_uint_t invocation_start)
{
    if (rc == NGX_AGAIN) {
        return NGX_AGAIN;
    }

    if (rc == NGX_OK) {
        return NGX_OK;
    }

    /*
     * If fallback occurred, the processing path
     * was switched. Let the caller re-enter the
     * body filter with the remaining chain.
     */
    if (ctx->processing_path
        != NGX_HTTP_MARKDOWN_PATH_STREAMING)
    {
        return NGX_DONE;
    }

    if (!ctx->eligible) {
        return ngx_http_markdown_streaming_failopen_passthrough(
            r, ctx, in, invocation_start);
    }

    return rc;
}


/*
 * Check for client abort and clean up streaming state.
 *
 * Returns:
 *   0         - no abort, continue processing
 *   NGX_ERROR - client aborted, handle released
 */
static ngx_inline ngx_int_t
ngx_http_markdown_streaming_check_client_abort(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    if (!r->connection->error) {
        return 0;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown streaming: client abort "
        "detected");

    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(
            ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    return NGX_ERROR;
}


/*
 * Forward headers and pass chain through when streaming
 * is not active (aborted or not eligible).
 */
static ngx_int_t
ngx_http_markdown_streaming_passthrough(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *in)
{
    ngx_int_t  rc;

    if (!ctx->headers_forwarded) {
        rc = ngx_http_markdown_forward_headers(
            r, ctx);
        if (rc != NGX_OK) {
            return NGX_ERROR;
        }
    }
    return ngx_http_next_body_filter(r, in);
}


/*
 * Ensure the streaming handle is initialized.
 *
 * On first body-filter invocation, creates the streaming handle,
 * decompressor, and prebuffer. If init fails with NGX_DECLINED
 * (fail-open), forwards deferred headers and passes the body
 * chain downstream.
 *
 * Returns:
 *   NGX_OK    - handle ready, continue processing
 *   otherwise - status propagated from passthrough/error path
 */
static ngx_int_t
ngx_http_markdown_streaming_ensure_handle(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    ngx_chain_t *in)
{
    ngx_int_t  rc;

    if (ctx->streaming.handle != NULL || !ctx->eligible) {
        return NGX_OK;
    }

    rc = ngx_http_markdown_streaming_init_handle(
        r, ctx, conf);

    if (rc == NGX_ERROR) {
        return NGX_ERROR;
    }

    if (rc == NGX_DECLINED) {
        /*
         * Fail-open: headers were deferred in the header
         * filter, so forward them before passing the body
         * chain downstream.
         */
        if (!ctx->headers_forwarded) {
            rc = ngx_http_markdown_forward_headers(
                r, ctx);
            if (rc != NGX_OK) {
                return NGX_ERROR;
            }
        }

        return ngx_http_next_body_filter(r, in);
    }

    return NGX_OK;
}

/*
 * Re-enter full-buffer body filter after streaming fallback.
 *
 * The current chain node was already consumed into prebuffer by
 * the streaming path, so re-entry starts at cl->next. If this was
 * the terminal node and there is no next link, synthesize an empty
 * terminal chain node to preserve end-of-stream signaling.
 */
static ngx_int_t
ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
    ngx_http_request_t *r,
    ngx_chain_t *cl,
    ngx_flag_t last_buf)
{
    ngx_chain_t  *reentry_in;

    reentry_in = cl->next;
    if (reentry_in == NULL && last_buf) {
        ngx_buf_t    *term_buf;
        ngx_chain_t  *term_cl;

        term_buf = ngx_calloc_buf(r->pool);
        if (term_buf == NULL) {
            return NGX_ERROR;
        }

        term_buf->last_buf = (r == r->main) ? 1 : 0;
        term_buf->last_in_chain = (r != r->main) ? 1 : 0;

        term_cl = ngx_alloc_chain_link(r->pool);
        if (term_cl == NULL) {
            return NGX_ERROR;
        }

        term_cl->buf = term_buf;
        term_cl->next = NULL;
        reentry_in = term_cl;
    }

    return ngx_http_markdown_body_filter(r, reentry_in);
}


static ngx_int_t
ngx_http_markdown_streaming_handle_null_input(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_streaming_resume_pending(
        r, ctx);
    if (rc != NGX_OK) {
        return rc;
    }

    if (ctx->streaming.finalize_after_pending) {
        ctx->streaming.finalize_after_pending = 0;
        return ngx_http_markdown_streaming_finalize_request(
            r, ctx, conf);
    }

    return NGX_OK;
}


static ngx_uint_t
ngx_http_markdown_streaming_count_chain_bufs(
    ngx_chain_t *in)
{
    ngx_chain_t *cl;
    ngx_uint_t   chain_bufs;

    chain_bufs = 0;
    for (cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf != NULL) {
            chain_bufs++;
        }
    }

    return chain_bufs;
}


static ngx_int_t
ngx_http_markdown_streaming_prepare_failopen_tracking(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *in)
{
    ngx_buf_t  **new_bufs;
    u_char     **new_pos;
    ngx_uint_t   chain_bufs;
    ngx_uint_t   required;

    if (ctx->streaming.commit_state
        == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
    {
        return NGX_OK;
    }

    chain_bufs = ngx_http_markdown_streaming_count_chain_bufs(
        in);
    required = ctx->streaming.failopen_consumed_count
               + chain_bufs;

    if (required == 0
        || required <= ctx->streaming.failopen_consumed_capacity)
    {
        return NGX_OK;
    }

    new_bufs = ngx_pnalloc(r->pool,
        required * sizeof(ngx_buf_t *));
    new_pos = ngx_pnalloc(r->pool,
        required * sizeof(u_char *));

    if (new_bufs == NULL || new_pos == NULL)
    {
        return NGX_ERROR;
    }

    if (ctx->streaming.failopen_consumed_count > 0) {
        ngx_memcpy(new_bufs,
            ctx->streaming.failopen_consumed_bufs,
            ctx->streaming.failopen_consumed_count
                * sizeof(ngx_buf_t *));
        ngx_memcpy(new_pos,
            ctx->streaming.failopen_consumed_pos,
            ctx->streaming.failopen_consumed_count
                * sizeof(u_char *));
    }

    ctx->streaming.failopen_consumed_bufs = new_bufs;
    ctx->streaming.failopen_consumed_pos = new_pos;
    ctx->streaming.failopen_consumed_capacity = required;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_streaming_process_chain(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_conf_t *conf,
    ngx_chain_t *in,
    ngx_flag_t *last_buf,
    ngx_uint_t invocation_start,
    ngx_chain_t **fallback_cl)
{
    ngx_chain_t  *cl;
    ngx_int_t     rc;

    *last_buf = 0;
    *fallback_cl = NULL;

    for (cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL) {
            continue;
        }

        if (cl->buf->last_buf
            || (r != r->main && cl->buf->last_in_chain))
        {
            *last_buf = 1;
        }

        rc = ngx_http_markdown_streaming_process_chunk(
            r, ctx, conf, cl->buf);

        rc = ngx_http_markdown_streaming_handle_chunk_result(
            r, ctx, in, rc, invocation_start);

        if (rc != NGX_OK) {
            if (rc == NGX_DONE) {
                *fallback_cl = cl;
            }
            return rc;
        }

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE)
        {
            ngx_uint_t idx;

            idx = ctx->streaming.failopen_consumed_count;
            ctx->streaming.failopen_consumed_bufs[idx] = cl->buf;
            ctx->streaming.failopen_consumed_pos[idx] = cl->buf->pos;
            ctx->streaming.failopen_consumed_count++;
        }

        /* Mark buffer as consumed */
        cl->buf->pos = cl->buf->last;
    }

    return NGX_OK;
}


/*
 * Streaming body filter main entry point.
 *
 * Called when processing_path == PATH_STREAMING.
 * Implements the streaming state machine:
 *   Idle -> PreCommit -> PostCommit -> Finalized
 */
static ngx_int_t
ngx_http_markdown_streaming_body_filter(
    ngx_http_request_t *r, ngx_chain_t *in)
{
    ngx_http_markdown_ctx_t   *ctx;
    ngx_http_markdown_conf_t  *conf;
    ngx_chain_t               *fallback_cl;
    ngx_int_t                  rc;
    ngx_flag_t                 last_buf;
    ngx_uint_t                 invocation_start;

    ctx = ngx_http_get_module_ctx(r,
        ngx_http_markdown_filter_module);
    if (ctx == NULL) {
        return ngx_http_next_body_filter(r, in);
    }

    conf = ngx_http_get_module_loc_conf(r,
        ngx_http_markdown_filter_module);
    if (conf == NULL) {
        return ngx_http_next_body_filter(r, in);
    }

    /* Check for client abort */
    rc = ngx_http_markdown_streaming_check_client_abort(
        r, ctx);
    if (rc != 0) {
        return NGX_ERROR;
    }

    /* Resume pending output (backpressure recovery) */
    if (in == NULL) {
        return ngx_http_markdown_streaming_handle_null_input(
            r, ctx, conf);
    }

    /* Initialize streaming handle on first call */
    rc = ngx_http_markdown_streaming_ensure_handle(
        r, ctx, conf, in);
    if (rc != NGX_OK) {
        return rc;
    }

    if (!ctx->eligible || ctx->streaming.handle == NULL) {
        return ngx_http_markdown_streaming_passthrough(
            r, ctx, in);
    }

    invocation_start = ctx->streaming.failopen_consumed_count;

    rc = ngx_http_markdown_streaming_prepare_failopen_tracking(
        r, ctx, in);
    if (rc != NGX_OK) {
        return rc;
    }

    rc = ngx_http_markdown_streaming_process_chain(
        r, ctx, conf, in, &last_buf, invocation_start,
        &fallback_cl);
    if (rc == NGX_DONE) {
        return ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
            r, fallback_cl, last_buf);
    }
    if (rc != NGX_OK) {
        return rc;
    }

    /* Handle last_buf: finalize */
    if (last_buf) {
        rc = ngx_http_markdown_streaming_finalize_request(
            r, ctx, conf);

        if (rc == NGX_DECLINED && !ctx->eligible) {
            return ngx_http_markdown_streaming_failopen_passthrough(
                r, ctx, in, invocation_start);
        }

        return rc;
    }

    return NGX_OK;
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_STREAMING_IMPL_H */
