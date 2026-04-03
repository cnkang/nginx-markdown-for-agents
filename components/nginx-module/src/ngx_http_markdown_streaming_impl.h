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
        static const u_char  str_off[]  = "off";
        static const u_char  str_on[]   = "on";
        static const u_char  str_auto[] = "auto";

        if (val.len == 3
            && ngx_strncasecmp(val.data, (u_char *) str_off,
                               3) == 0)
        {
            return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
        }

        if (val.len == 2
            && ngx_strncasecmp(val.data, (u_char *) str_on,
                               2) == 0)
        {
            engine_mode =
                NGX_HTTP_MARKDOWN_STREAMING_ENGINE_ON;

        } else if (val.len == 4
                   && ngx_strncasecmp(val.data,
                                      (u_char *) str_auto,
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
            "conditional_requests full_support");
        return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
    }

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
    if (conf->stream_types != NULL) {
        ngx_str_t   *types;
        ngx_uint_t   i;

        types = conf->stream_types->elts;
        for (i = 0; i < conf->stream_types->nelts; i++)
        {
            if (r->headers_out.content_type.len
                    >= types[i].len
                && ngx_strncasecmp(
                       r->headers_out.content_type.data,
                       types[i].data,
                       types[i].len) == 0)
            {
                return NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;
            }
        }
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
        ctx->streaming.pending_output = out;
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
        return NGX_OK;
    }

    ctx->streaming.pending_output = NULL;
    rc = ngx_http_next_body_filter(r, out);

    if (rc == NGX_AGAIN) {
        ctx->streaming.pending_output = out;
        return NGX_AGAIN;
    }

    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
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

    /* Record decision log */
    ngx_http_markdown_log_decision(r, conf,
        &ngx_http_markdown_reason_streaming_fail_postcommit);

    /* Send empty last_buf to terminate the response */
    return ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);
}


/*
 * Pre-Commit error handler: abort handle and apply on_error policy.
 *
 * Returns:
 *   NGX_ERROR    if on_error == reject
 *   NGX_DECLINED if on_error == pass (fail-open, sets eligible=0)
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
    ngx_flag_t inc_failopen)
{
    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.failed_total);

    if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        return NGX_ERROR;
    }

    /* Fail-open: pass original content */
    ctx->eligible = 0;
    if (inc_failopen) {
        ngx_http_markdown_metric_inc_failopen(conf);
    }
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
            r, ctx, conf, /* inc_failopen */ 1);
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
                r, ctx, conf, /* inc_failopen */ 0);
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
            r, ctx, conf, /* inc_failopen */ 0);
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
            r, ctx, conf, /* inc_failopen */ 0);
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
                r, ctx, conf, /* inc_failopen */ 0);
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

    if (ctx->streaming.handle == NULL) {
        /* Handle already consumed */
        return ngx_http_markdown_streaming_send_output(
            r, ctx, NULL, 0, /* last_buf */ 1);
    }

    /* Finish decompression if needed */
    if (ctx->decompression.needed
        && ctx->streaming.decompressor != NULL)
    {
        u_char  *decomp_data;
        size_t   decomp_len;

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
                r, ctx, conf, /* inc_failopen */ 1);
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

        /* Pre-Commit finalize error */
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.failed_total);
        return NGX_ERROR;
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
    }

    /* Log ETag if available */
    if (result.etag != NULL && result.etag_len > 0) {
        ngx_log_debug2(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown streaming: finalize ETag "
            "len=%uz, tokens=%ui",
            result.etag_len,
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

    ngx_http_markdown_log_decision(r, conf,
        &ngx_http_markdown_reason_streaming_convert);

    /* Send final empty last_buf */
    return ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);
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
    ngx_chain_t               *cl;
    ngx_int_t                  rc;
    ngx_flag_t                 last_buf;

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
    if (r->connection->error) {
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

    /* Resume pending output (backpressure recovery) */
    if (in == NULL) {
        return ngx_http_markdown_streaming_resume_pending(
            r, ctx);
    }

    /* Initialize streaming handle on first call */
    if (ctx->streaming.handle == NULL
        && ctx->eligible)
    {
        struct MarkdownOptions  options;
        ngx_pool_cleanup_t     *cln;

        ngx_memzero(&options,
                    sizeof(struct MarkdownOptions));

        rc = ngx_http_markdown_prepare_conversion_options(
            r, conf, &options);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown streaming: failed to "
                "prepare conversion options");
            ctx->eligible = 0;
            return ngx_http_next_body_filter(r, in);
        }

        ctx->streaming.handle =
            markdown_streaming_new(&options);
        if (ctx->streaming.handle == NULL) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown streaming: failed to "
                "create streaming handle");

            NGX_HTTP_MARKDOWN_METRIC_INC(
                streaming.failed_total);

            if (conf->on_error
                == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
            {
                return NGX_ERROR;
            }

            /* Fail-open: pass through */
            ctx->eligible = 0;
            ngx_http_markdown_metric_inc_failopen(conf);
            return ngx_http_next_body_filter(r, in);
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
                ctx->eligible = 0;
                return ngx_http_next_body_filter(
                    r, in);
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

        ctx->conversion_attempted = 1;
        NGX_HTTP_MARKDOWN_METRIC_INC(
            conversions_attempted);
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.requests_total);

        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown streaming: handle created, "
            "entering Pre-Commit phase");
    }

    if (!ctx->eligible || ctx->streaming.handle == NULL) {
        /* Streaming was aborted or not eligible */
        if (!ctx->headers_forwarded) {
            rc = ngx_http_markdown_forward_headers(
                r, ctx);
            if (rc != NGX_OK) {
                return NGX_ERROR;
            }
        }
        return ngx_http_next_body_filter(r, in);
    }

    /* Process each chunk in the input chain */
    last_buf = 0;
    for (cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL) {
            continue;
        }

        if (cl->buf->last_buf
            || (r != r->main && cl->buf->last_in_chain))
        {
            last_buf = 1;
        }

        rc = ngx_http_markdown_streaming_process_chunk(
            r, ctx, conf, cl->buf);

        if (rc == NGX_AGAIN) {
            /* Backpressure */
            return NGX_AGAIN;
        }

        if (rc != NGX_OK) {
            /*
             * If fallback occurred, the processing path
             * was switched. Let the caller re-enter the
             * body filter with the remaining chain.
             */
            if (ctx->processing_path
                != NGX_HTTP_MARKDOWN_PATH_STREAMING)
            {
                return NGX_OK;
            }

            if (!ctx->eligible) {
                /* Fail-open: forward remaining */
                if (!ctx->headers_forwarded) {
                    ngx_http_markdown_forward_headers(
                        r, ctx);
                }
                return ngx_http_next_body_filter(
                    r, in);
            }

            return rc;
        }

        /* Mark buffer as consumed */
        cl->buf->pos = cl->buf->last;
    }

    /* Handle last_buf: finalize */
    if (last_buf) {
        return
            ngx_http_markdown_streaming_finalize_request(
                r, ctx, conf);
    }

    return NGX_OK;
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_STREAMING_IMPL_H */
