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
#include "ngx_http_markdown_stream_postcommit.h"
#include "ngx_http_markdown_stream_commit.h"

/* Forward declarations */
static ngx_http_markdown_otel_span_t *ngx_http_markdown_otel_span_start(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf);
static void ngx_http_markdown_otel_set_str_attr(
    ngx_http_markdown_otel_span_t *span, const u_char *key, size_t key_len,
    const u_char *value, size_t val_len);
static void ngx_http_markdown_otel_set_int_attr(
    ngx_http_markdown_otel_span_t *span, const u_char *key, size_t key_len,
    int64_t value);
static void ngx_http_markdown_otel_span_end(ngx_http_markdown_otel_span_t *span);
static void ngx_http_markdown_otel_span_export(
    ngx_http_markdown_otel_span_t *span, ngx_log_t *log,
    ngx_http_request_t *r);

/*
 * Streaming body filter main entry point.
 *
 * Implements the streaming state machine lifecycle:
 * Idle -> PreCommit -> PostCommit -> Finalized.
 * Handles backpressure recovery, fallback to full-buffer,
 * and fail-open passthrough.
 *
 * r  - current HTTP request
 * in - incoming chain of upstream buffers (NULL on resume)
 *
 * Returns:
 *   NGX_OK      on success
 *   NGX_AGAIN   on downstream backpressure
 *   NGX_ERROR   on unrecoverable failure
 */
static ngx_int_t
ngx_http_markdown_streaming_body_filter(
    ngx_http_request_t *r, ngx_chain_t *in);

/*
 * Process a single upstream buffer through the streaming pipeline.
 *
 * Decompresses (if needed), enforces cumulative size limits,
 * saves to prebuffer in Pre-Commit state, feeds data to the
 * Rust streaming FFI, and dispatches output or errors.
 *
 * r    - current HTTP request
 * ctx  - per-request module context
 * conf - location configuration
 * buf  - upstream buffer to process
 *
 * Returns:
 *   NGX_OK       on success
 *   NGX_AGAIN    on downstream backpressure
 *   NGX_DECLINED on fail-open or fallback
 *   NGX_ERROR    on failure
 */
static ngx_int_t
ngx_http_markdown_streaming_process_chunk(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_buf_t *buf);

/*
 * Send converted Markdown output downstream.
 *
 * Copies data from the Rust-allocated buffer into pool memory,
 * constructs an output chain, and passes it to the next body
 * filter. Records TTFB on the first successful non-empty send.
 * On NGX_AGAIN, saves the chain as pending output for later
 * resume.
 *
 * r        - current HTTP request
 * ctx      - per-request module context
 * data     - Markdown output bytes (NULL for empty terminal)
 * len      - length of data in bytes
 * last_buf - 1 if this is the final output chunk
 *
 * Returns:
 *   NGX_OK    on successful downstream delivery
 *   NGX_AGAIN on downstream backpressure
 *   NGX_ERROR on allocation or filter failure
 */
static ngx_int_t
ngx_http_markdown_streaming_send_output(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const u_char *data, size_t len,
    ngx_flag_t last_buf);

/*
 * Finalize the streaming conversion on last_buf.
 *
 * Flushes any remaining decompression tail data, calls
 * markdown_streaming_finalize() to obtain the final Markdown
 * output and result metadata (ETag, token estimate), then
 * sends the terminal chunk downstream. Records success or
 * failure metrics based on commit state.
 *
 * r    - current HTTP request
 * ctx  - per-request module context
 * conf - location configuration
 *
 * Returns:
 *   NGX_OK       on success
 *   NGX_AGAIN    if pending output defers finalization
 *   NGX_DECLINED on pre-commit fail-open
 *   NGX_ERROR    on failure
 */
static ngx_int_t
ngx_http_markdown_streaming_finalize_request(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);

/*
 * Pre-Commit fallback from streaming to full-buffer path.
 *
 * Aborts the Rust streaming handle, transfers prebuffered
 * decompressed data to the main buffer, resets conversion
 * state flags, and corrects path-hit metrics. Called when
 * the Rust FFI returns ERROR_STREAMING_FALLBACK.
 *
 * r    - current HTTP request
 * ctx  - per-request module context
 * conf - location configuration
 *
 * Returns:
 *   NGX_DECLINED to signal the caller to re-enter
 *                the full-buffer body filter path
 *   NGX_ERROR    on buffer initialization failure
 */
static ngx_int_t
ngx_http_markdown_streaming_fallback_to_fullbuffer(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);

/*
 * Post-Commit error handler for streaming conversion.
 *
 * After headers or Markdown bytes have been sent downstream, the
 * response cannot revert to HTML or return 502. The handler delegates
 * to the streaming fallback state machine:
 *   - on_error=pass   -> Rust safe_finish, fallback to abort on failure
 *   - on_error=reject -> protocol-safe abort
 *
 * NGX_AGAIN from safe_finish/abort is a legitimate pending state:
 * pending output is preserved and drained by resume_pending().
 *
 * No post-commit path may emit HTML or change the HTTP status.
 *
 * r          - current HTTP request
 * ctx        - per-request module context
 * conf       - location configuration
 * error_code - FFI error code from the Rust streaming engine
 *
 * Returns:
 *   NGX_OK    on successful safe-finish or terminal send
 *   NGX_AGAIN on downstream backpressure (pending output preserved)
 *   NGX_ERROR on send failure
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_postcommit_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    uint32_t error_code);

/*
 * Pre-Commit error handler: apply streaming_on_error policy.
 *
 * Single entry point for all pre-commit streaming failures.
 * Routes to fallback (ERROR_STREAMING_FALLBACK), fail-open
 * (pass original HTML), or fail-closed (reject) based on
 * the error code and the markdown_streaming_on_error directive.
 *
 * r          - current HTTP request
 * ctx        - per-request module context
 * conf       - location configuration
 * error_code - FFI error code from the Rust streaming engine
 *
 * Returns:
 *   NGX_DECLINED on fallback or fail-open
 *   NGX_ERROR    on fail-closed (reject)
 */
static ngx_int_t
ngx_http_markdown_streaming_precommit_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    uint32_t error_code);

/*
 * Handle downstream backpressure during streaming output.
 *
 * Sets the NGX_HTTP_MARKDOWN_BUFFERED flag on the request
 * to signal that this filter has unsent data. The pending
 * chain is preserved in ctx for later resume.
 *
 * r   - current HTTP request
 * ctx - per-request module context
 *
 * Returns:
 *   NGX_AGAIN always (signals caller to pause processing)
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_backpressure(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx);

/*
 * Resume sending pending output after backpressure clears.
 *
 * Drains the chain retained by the downstream filter by calling
 * ngx_http_next_body_filter with NULL.  pending_output remains a
 * request-lifetime anchor and state latch; it is never resubmitted.
 * On success, records deferred TTFB and terminal metrics if
 * applicable, then sends any deferred last_buf. Clears the
 * NGX_HTTP_MARKDOWN_BUFFERED flag when the pending chain
 * drains.
 *
 * r    - current HTTP request
 * ctx  - per-request module context
 * conf - location configuration
 *
 * Returns:
 *   NGX_OK    when all pending output is drained
 *   NGX_AGAIN if backpressure persists
 *   NGX_ERROR on downstream failure
 */
static ngx_int_t
ngx_http_markdown_streaming_resume_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);

/*
 * Record post-commit failure metrics for streaming conversion.
 *
 * Increments postcommit_error_total and failed_total counters
 * (guarded by a one-shot latch to prevent double-counting),
 * then logs the streaming fail-postcommit reason code.
 *
 * r    - current HTTP request
 * ctx  - per-request module context
 * conf - location configuration
 */
static void
ngx_http_markdown_streaming_record_postcommit_failure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);

/*
 * Send deferred terminal last_buf after backpressure drain.
 *
 * Called when finalize() encountered NGX_AGAIN while trying
 * to send the terminal empty last_buf. Retries the send and
 * records success/failure metrics based on the actual result.
 *
 * r    - current HTTP request
 * ctx  - per-request module context
 * conf - location configuration
 *
 * Returns:
 *   NGX_OK    on successful terminal send
 *   NGX_AGAIN on persistent backpressure
 *   NGX_ERROR on send failure
 */
static ngx_int_t
ngx_http_markdown_streaming_send_deferred_lastbuf(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);

/*
 * Pool cleanup handler for streaming resources.
 *
 * Releases the Rust streaming handle and frees any pending
 * output chain buffers that hold Rust-allocated memory.
 * Registered via ngx_pool_cleanup_add() to ensure cleanup
 * even on abnormal request termination (client abort, timeout).
 *
 * data - pointer to the per-request ngx_http_markdown_ctx_t
 */
static void
ngx_http_markdown_streaming_cleanup(void *data);

/*
 * Engine selector: determine the processing path for a request.
 *
 * Evaluates the markdown_streaming_engine enum and applies the
 * selection rules (engine mode, HEAD request,
 * 304 status, conditional_requests policy, content-type
 * exclusions, and auto-mode content-length threshold).
 *
 * r    - current HTTP request
 * conf - location configuration
 *
 * Returns:
 *   NGX_HTTP_MARKDOWN_PATH_STREAMING   for streaming path
 *   NGX_HTTP_MARKDOWN_PATH_FULLBUFFER  for full-buffer path
 */
static ngx_http_markdown_path_selection_t
ngx_http_markdown_select_processing_path(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff);

/*
 * Update response headers at the streaming commit boundary.
 *
 * Sets Content-Type to text/markdown; charset=utf-8, adds
 * Vary: Accept, clears Content-Length (streaming uses chunked
 * transfer), removes Content-Encoding if decompressing, and
 * removes any stale upstream ETag.
 *
 * r    - current HTTP request
 * ctx  - per-request module context
 * conf - location configuration
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on header manipulation failure
 */
static ngx_int_t
ngx_http_markdown_streaming_update_headers(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);

/*
 * Lazily initialize the Rust streaming converter handle.
 *
 * On first invocation (handle == NULL and eligible), calls
 * the streaming init function. On init failure with
 * NGX_DECLINED (fail-open), forwards deferred headers and
 * passes the body chain downstream unchanged.
 *
 * r    - current HTTP request
 * ctx  - per-request module context
 * conf - location configuration
 * in   - incoming chain (forwarded on fail-open)
 *
 * Returns:
 *   NGX_OK    when handle is ready or already initialized
 *   NGX_ERROR on unrecoverable init failure
 *   result of ngx_http_next_body_filter on fail-open
 */
static ngx_int_t
ngx_http_markdown_streaming_ensure_handle(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_chain_t *in);

/*
 * Re-enter the full-buffer body filter after streaming fallback.
 *
 * The current chain node was already consumed into the prebuffer
 * by the streaming path, so re-entry starts at cl->next. If this
 * was the terminal node with no successor, synthesizes an empty
 * terminal chain to preserve end-of-stream signaling.
 *
 * r        - current HTTP request
 * cl       - chain link at which fallback occurred
 * last_buf - 1 if the fallback chain carried last_buf
 *
 * Returns:
 *   result of ngx_http_markdown_body_filter on the
 *   remaining (or synthesized terminal) chain
 */
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

    if (ctx->otel_span != NULL) {
        ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
            (const u_char *) "error_code", 10,
            (int64_t) -1);
        ngx_http_markdown_otel_span_end(ctx->otel_span);
        ngx_http_markdown_otel_span_export(ctx->otel_span,
            ctx->request->connection->log, ctx->request);
        ctx->otel_span = NULL;
    }

    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    /*
     * Pending output chains that hold Rust-allocated buffers
     * would leak if not freed. Walk the chain and release
     * each Rust buffer.
     *
     * Ownership discriminator: buffers created in send_output use
     * ngx_palloc (b->memory=1, b->temporary=0) and are freed with
     * the request pool.  Only buffers with b->temporary=1 are
     * Rust-owned and require markdown_streaming_output_free().
     * The ngx_calloc_buf zeroing guarantees temporary=0 for
     * pool-copied buffers.
     */
    {
        for (ngx_chain_t *cl = ctx->streaming.pending_output;
             cl != NULL; cl = cl->next)
        {
            if (cl->buf != NULL && cl->buf->temporary) {
                size_t len = ngx_http_markdown_buf_len_safe(cl->buf);
                if (len > 0) {
                    markdown_streaming_output_free(cl->buf->pos, len);
                }
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
    const ngx_http_markdown_conf_t *conf)
{
    ngx_str_t   *types;

    if (conf->routing.stream_types == NULL) {
        return 0;
    }

    types = conf->routing.stream_types->elts;
    if (r->headers_out.content_type.data == NULL) {
        return 0;
    }

    for (ngx_uint_t i = 0; i < conf->routing.stream_types->nelts; i++) {
        if (r->headers_out.content_type.len
                >= types[i].len
            && ngx_strncasecmp(
                   r->headers_out.content_type.data,
                   types[i].data,
                   types[i].len) == 0
            && (r->headers_out.content_type.len == types[i].len
                || r->headers_out.content_type.data[types[i].len] == ';'
                || r->headers_out.content_type.data[types[i].len] == ' '
                || r->headers_out.content_type.data[types[i].len] == '/'))
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
    const ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf)
{
    /* r is used only inside ngx_log_debug0 which compiles to nothing
     * in non-debug builds; suppress the unused-parameter warning. */
    (void) r;

    if (conf->policy.conditional_requests
        == NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE)
    {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: streaming allowed: "
            "conditional_requests "
            "if_modified_since_only");
    } else if (conf->policy.conditional_requests
               == NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED)
    {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: streaming allowed: "
            "conditional_requests disabled");
    }
}


/*
 * Engine selector: determine the processing path for a request.
 *
 * Evaluates the markdown_streaming_engine enum once in the header
 * filter phase and caches the result.
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
 * 9. engine == auto + CL >= markdown_stream_threshold -> PATH_STREAMING
 * 10. engine == auto + chunked -> PATH_STREAMING
 * 11. engine == auto + CL < markdown_stream_threshold -> PATH_FULLBUFFER
 *
 * Default (no markdown_streaming_engine directive): auto mode.
 */
static ngx_http_markdown_path_selection_t
ngx_http_markdown_select_processing_path(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    ngx_uint_t   engine_mode;

    if (conf == NULL) {
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_CONFIG_DISABLED);
    }

    engine_mode = conf->stream.engine;

    if (engine_mode == NGX_HTTP_MARKDOWN_STREAM_ENGINE_OFF) {
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_CONFIG_DISABLED);
    }

    /* Rule 3: HEAD request */
    if (r->method == NGX_HTTP_HEAD) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: streaming skip: "
            "HEAD request");
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_CANDIDATE);
    }

    /* Rule 4: 304 Not Modified */
    if (r->headers_out.status == NGX_HTTP_NOT_MODIFIED) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: streaming skip: "
            "304 response");
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_CANDIDATE);
    }

    /* Rule 5: conditional_requests full_support */
    if (conf->policy.conditional_requests
        == NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT)
    {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: streaming skip: "
            "conditional_requests full_support "
            "requires full ETag before headers");
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_CANDIDATE);
    }

    /* Rule 5b: log streaming-allowed decision */
    ngx_http_markdown_log_conditional_streaming(r, conf);

    /* Rule 6: text/event-stream */
    if (r->headers_out.content_type.len >= 17
        && r->headers_out.content_type.data != NULL
        && ngx_strncasecmp(
               r->headers_out.content_type.data,
               (u_char *) "text/event-stream", /* NOSONAR: ngx_strncasecmp API takes non-const u_char* */
               17) == 0)
    {
        ngx_http_markdown_log_decision(r, conf, eff,
            ngx_http_markdown_reason_streaming_skip_unsupported());
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_EXCLUDED_CONTENT_TYPE);
    }

    /* Rule 7: stream_types exclusion list (legacy conf->routing.stream_types)
     * and v0.8.0 conf->stream.excluded_types + built-in hard exclusions */
    if (ngx_http_markdown_is_excluded_stream_type(
            r, conf)
        || ngx_http_markdown_stream_type_excluded(
               &r->headers_out.content_type, conf))
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.selection.excluded_content_type_total);
        ngx_http_markdown_log_decision(r, conf, eff,
            ngx_http_markdown_reason_streaming_skip_unsupported());
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_EXCLUDED_CONTENT_TYPE);
    }

    /* Rule 8: engine == on */
    if (engine_mode
        == NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON)
    {
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_STREAMING,
            NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE);
    }

    /* Rules 9-11: engine == auto */
    if (r->headers_out.content_length_n >= 0
        && (size_t) r->headers_out.content_length_n
           < conf->stream.threshold)
    {
        /* CL < markdown_stream_threshold: use full-buffer */
        ngx_http_markdown_log_decision(r, conf, eff,
            ngx_http_markdown_reason_eligible_fullbuffer_auto());
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_BELOW_THRESHOLD);
    }

    /* auto + CL >= markdown_stream_threshold or chunked (no CL) */
    ngx_http_markdown_log_decision(r, conf, eff,
        ngx_http_markdown_reason_eligible_streaming_auto());
    return ngx_http_markdown_path_selection(
        NGX_HTTP_MARKDOWN_PATH_STREAMING,
        NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE);
}


/*
 * Update response headers at the commit boundary.
 *
 * Delegates to the authoritative ngx_http_markdown_stream_commit_headers()
 * which implements the two-phase atomic commit design (Rule 39).
 * This wrapper preserves the existing call signature in streaming_impl
 * while ensuring a single header mutation code path.
 *
 * Note: stream_commit_headers() also sets headers_committed and
 * transitions state to COMMITTED.  The caller (streaming_commit)
 * must NOT duplicate these assignments.
 */
static ngx_int_t
ngx_http_markdown_streaming_update_headers(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    return ngx_http_markdown_stream_commit_headers(r, ctx, conf);
}


/*
 * Free Rust-owned buffers in a pending chain.
 *
 * Iterates chain links, releasing any temporary buffer data
 * via markdown_streaming_output_free() and NULLing pointers
 * to prevent use-after-free.
 */
static void
ngx_http_markdown_streaming_free_pending_chain(ngx_chain_t *chain)
{
    for (ngx_chain_t *cl = chain; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL || !cl->buf->temporary) {
            continue;
        }

        {
            size_t  buf_len;

            buf_len = ngx_http_markdown_buf_len_safe(cl->buf);
            if (buf_len > 0) {
                markdown_streaming_output_free(cl->buf->pos, buf_len);
            }
        }

        cl->buf->pos = NULL;
        cl->buf->last = NULL;
    }
}


/*
 * Record TTFB (time-to-first-byte) on first successful output.
 *
 * One-shot latch: only fires once, only when downstream confirmed
 * delivery (NGX_OK or NGX_DONE).
 */
static void
ngx_http_markdown_streaming_record_ttfb(
    ngx_http_markdown_ctx_t *ctx)
{
    const ngx_time_t  *tp;
    ngx_msec_t         now_ms;
    ngx_msec_t         elapsed_ms;

    if (ctx->streaming.ttfb.recorded
        || ctx->streaming.ttfb.feed_start_ms == 0
        || ngx_http_markdown_metrics == NULL)
    {
        return;
    }

    tp = ngx_timeofday();
    now_ms = (ngx_msec_t) (tp->sec * 1000 + tp->msec);
    elapsed_ms = (now_ms >= ctx->streaming.ttfb.feed_start_ms)
        ? (now_ms - ctx->streaming.ttfb.feed_start_ms) : 0;

    /*
     * Gauge store: latest-value-wins semantics.
     *
     * Direct assignment to ngx_atomic_t is not formally
     * atomic per C11, but ngx_atomic_t is intptr_t-sized
     * and naturally aligned, making the store word-atomic
     * in practice on all NGINX platforms.
     */
    ngx_http_markdown_metrics->streaming.last_ttfb_ms =
        (ngx_atomic_t) elapsed_ms;
    ctx->streaming.ttfb.recorded = 1;
}


/*
 * Save output chain as pending on downstream backpressure (NGX_AGAIN).
 *
 * Guards against unexpected re-entry by freeing any existing pending
 * chain before saving the new one.  Sets the buffered flag so NGINX
 * event machinery knows to retry the downstream write.
 *
 * Returns:
 *   NGX_AGAIN  - pending saved successfully
 *   NGX_ERROR  - re-entry detected (old chain freed, request aborted)
 */
static ngx_int_t
ngx_http_markdown_streaming_save_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *out,
    const u_char *data, size_t len)
{
    if (ctx->streaming.pending_output != NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: streaming pending output "
                      "re-entry detected, refusing to overwrite "
                      "existing pending chain");

        ngx_http_markdown_streaming_free_pending_chain(
            ctx->streaming.pending_output);
        ctx->streaming.pending_output = NULL;

        return NGX_ERROR;
    }

    ctx->streaming.pending_output = out;
    ctx->streaming.pending_has_data =
        (data != NULL && len > 0) ? 1 : 0;
    ctx->streaming.pending_output_bytes = len;
    r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;

    return NGX_AGAIN;
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
    const u_char *data, size_t len,
    ngx_flag_t last_buf)
{
    ngx_buf_t    *b;
    ngx_chain_t  *out;
    ngx_int_t     rc;
    ngx_flag_t    terminal_last_buf;

    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        return NGX_ERROR;
    }

    if (data != NULL && len > 0) {
        /*
         * Copy Rust-allocated data into pool memory.
         *
         * Ownership model: the caller retains ownership of
         * `data` and must call markdown_streaming_output_free()
         * (or markdown_result_free() for finalize-path buffers)
         * after this function returns, regardless of the return
         * code.  This function does NOT free `data`.
         *
         * Trade-off: this introduces a transient double-buffer
         * window (Rust buffer + pool copy) up to `len` bytes
         * for this chunk.  The caller frees the Rust buffer
         * immediately after this call, closing the window.
         */
        b->pos = ngx_palloc(r->pool, len);
        if (b->pos == NULL) {
            /*
             * Pool allocation failed.  The caller still owns
             * `data` and must free it on seeing NGX_ERROR.
             */
            return NGX_ERROR;
        }
        ngx_memcpy(b->pos, data, len);
        b->last = b->pos + len;
        b->memory = 1;
    }

    b->flush = last_buf ? 0 : 1;
    b->last_buf = (last_buf && r == r->main) ? 1 : 0;
    b->last_in_chain = last_buf ? 1 : 0;

    /*
     * Latch: prevent duplicate terminal signals.  Once the main
     * request's last_buf has been sent downstream, further calls
     * with last_buf=1 are silently deduplicated.
     */
    terminal_last_buf = b->last_buf;
    if (terminal_last_buf && ctx->streaming.main_terminal_sent) {
        b->last_buf = 0;
        terminal_last_buf = 0;
    }

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = NULL;

    rc = ngx_http_next_body_filter(r, out);

    if (terminal_last_buf && (rc == NGX_OK || rc == NGX_DONE)) {
        ctx->streaming.main_terminal_sent = 1;
    }

    /*
     * Record TTFB on first successful non-empty output send.
     * NGX_AGAIN means backpressure — bytes are not yet sent.
     * TTFB will be recorded later in resume_pending() when
     * the pending chain drains successfully.
     */
    if (rc == NGX_OK || rc == NGX_DONE) {
        ctx->streaming.flushes_sent++;

        if (data != NULL && len > 0) {
            ngx_http_markdown_streaming_record_ttfb(ctx);
            NGX_HTTP_MARKDOWN_METRIC_ADD(
                streaming.selection.output_bytes_total,
                (ngx_atomic_int_t) len);
        }
    }

    if (rc == NGX_AGAIN) {
        rc = ngx_http_markdown_streaming_save_pending(
            r, ctx, out, data, len);
    }

    return rc;
}


/*
 * Handle backpressure: save pending output and set buffered flag.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_backpressure(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx)
{
    (void) ctx;

    r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: backpressure detected, "
        "pausing output");

    return NGX_AGAIN;
}

/*
 * Record a post-commit streaming failure in metrics and decision log.
 *
 * Increments postcommit_error_total and failed_total once (idempotent via
 * the failure_recorded flag), then logs the postcommit failure reason code.
 *
 * Parameters:
 *   r     - HTTP request
 *   ctx   - per-request module context
 *   conf  - module configuration
 */
static void
ngx_http_markdown_streaming_record_postcommit_failure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    if (!ctx->streaming.completion.failure_recorded) {
        /*
         * Preserve caller-set specific post-commit reason (e.g.,
         * POSTCOMMIT_BUDGET_EXCEEDED, POSTCOMMIT_IO_ERROR) if already
         * assigned by the error classifier.  Fall back to the generic
         * POSTCOMMIT_PARSE_ERROR only when no post-commit reason was
         * set upstream (reason still reflects the initial engine choice).
         */
        if (ctx->streaming.reason
            < NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_HTML_ERROR)
        {
            ctx->streaming.reason =
                NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_PARSE_ERROR;
        }
        NGX_HTTP_MARKDOWN_METRIC_INC(streaming.postcommit_error_total);
        NGX_HTTP_MARKDOWN_METRIC_INC(streaming.failed_total);

        /*
         * Increment global conversions_failed so the aggregate
         * attempted >= succeeded + failed invariant holds.
         * The resource-limit sub-classification is handled by
         * handle_postcommit_error() before this call.
         */
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);

        /*
         * Post-commit failures always take the abort path
         * (protocol-safe disconnect).  The safe_finish counter
         * is incremented separately if the graceful closure
         * path itself fails.
         */
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.streaming_failure_postcommit_abort);

        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: streaming post-commit failure: "
            "engine=streaming phase=postcommit "
            "committed=1 fallback_available=0 "
            "reason=%s content_type=%V "
            "content_length_known=%d chunked=%d "
            "error_policy=%s",
            ngx_http_markdown_stream_reason_str(
                ctx->streaming.reason),
            &r->headers_out.content_type,
            (r->headers_out.content_length_n >= 0) ? 1 : 0,
            (r->headers_out.content_length_n < 0) ? 1 : 0,
             (conf->stream.on_error
              == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
                ? "reject" : "pass");

        ctx->streaming.completion.failure_recorded = 1;
    }

    ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
        ngx_http_markdown_reason_streaming_fail_postcommit());
}

/*
 * Send the deferred last_buf marker for a streaming response.
 *
 * Called when a streaming conversion completes successfully but the final
 * empty buffer with last_buf=1 was deferred (e.g., due to pending output
 * from a previous send).  Handles backpressure (NGX_AGAIN) by setting
 * the pending_terminal_metrics latch so resume_pending() records success
 * metrics after the drain completes.
 *
 * Parameters:
 *   r     - HTTP request
 *   ctx   - per-request module context
 *   conf  - module configuration
 *
 * Returns:
 *   NGX_OK, NGX_DONE, NGX_AGAIN, or NGX_ERROR.
 */
static ngx_int_t
ngx_http_markdown_streaming_send_deferred_lastbuf(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    ctx->streaming.completion.finalize_pending_lastbuf = 0;
    rc = ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);

    if (rc == NGX_AGAIN) {
        /*
         * Deferred last_buf send hit backpressure. Set the
         * same metrics latch used by finalize() so that
         * resume_pending() will record success metrics after
         * the drain succeeds.
         */
        ctx->streaming.completion.pending_terminal_metrics = 1;
        return ngx_http_markdown_streaming_handle_backpressure(
            r, ctx);
    }

    /*
     * Deferred last_buf send completed. Record success or
     * failure metrics based on the actual result to maintain
     * consistent observability semantics across all post-commit
     * send paths.
     */
    if (rc == NGX_OK || rc == NGX_DONE) {
        NGX_HTTP_MARKDOWN_METRIC_INC(streaming.succeeded_total);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_succeeded);
        NGX_HTTP_MARKDOWN_METRIC_INC(results.delivery_count);

        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_streaming_convert());

        ngx_http_markdown_record_per_path_metrics(r, conf, 0);

        if (ctx->otel_span != NULL) {
            ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
                (const u_char *) "input_bytes", 11,
                (int64_t) ctx->streaming.total_input_bytes);
            ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
                (const u_char *) "output_bytes", 12,
                (int64_t) ctx->streaming.total_output_bytes);
            ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
                (const u_char *) "error_code", 10,
                (int64_t) 0);
            ngx_http_markdown_otel_span_end(ctx->otel_span);
            ngx_http_markdown_otel_span_export(ctx->otel_span,
                r->connection->log, r);
            ctx->otel_span = NULL;
        }
    } else {
        /*
         * Deferred last_buf send failed with a definitive
         * error. Record failure metrics to match the policy
         * used in resume_pending() and finalize().
         */
        ngx_http_markdown_streaming_record_postcommit_failure(
            r, ctx, conf);
    }

    return rc;
}


/*
 * Resume sending pending output after backpressure clears.
 */
static ngx_int_t
ngx_http_markdown_streaming_resume_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    const ngx_chain_t  *out;
    ngx_int_t     rc;
    ngx_flag_t    pending_last_buf;

    out = ctx->streaming.pending_output;
    if (out == NULL) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;

        /*
         * If finalize deferred the terminal last_buf due to
         * backpressure, send it now that the pending output
         * has been drained.
         */
        if (ctx->streaming.completion.finalize_pending_lastbuf) {
            return ngx_http_markdown_streaming_send_deferred_lastbuf(
                r, ctx, conf);
        }

        return NGX_OK;
    }

    /*
     * Capture last_buf before calling the downstream filter: once
     * the chain is consumed the buf metadata may be modified by
     * downstream filters (defensive, per Rule 2/38).
     */
    pending_last_buf = (out->buf != NULL && out->buf->last_buf
                        && r == r->main) ? 1 : 0;
    /*
     * The downstream filter retained the original chain when it returned
     * NGX_AGAIN.  Resume that owned state with NULL; resubmitting out would
     * duplicate its unsent tail (Rule 1).
     */
    rc = ngx_http_next_body_filter(r, NULL);

    if (rc == NGX_AGAIN) {
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
        return NGX_AGAIN;
    }

    ctx->streaming.pending_output = NULL;
    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;

    /*
     * Pending output drained successfully.  If TTFB was not
     * yet recorded (send_output returned NGX_AGAIN on the
     * first non-empty output), record it now — but only when:
     *   1. The retry actually succeeded (NGX_OK or NGX_DONE)
     *   2. The drained chain contained non-empty data
     *      (recorded via pending_has_data flag in send_output)
     *
     * Using the explicit flag avoids undefined pointer comparison
     * (out->buf->last > out->buf->pos) when pos/last may be NULL.
     */
    if (!ctx->streaming.ttfb.recorded
        && ctx->streaming.ttfb.feed_start_ms > 0
        && ngx_http_markdown_metrics != NULL
        && (rc == NGX_OK || rc == NGX_DONE)
        && ctx->streaming.pending_has_data)
    {
        const ngx_time_t  *tp_ttfb;
        ngx_msec_t   now_ms;
        ngx_msec_t   elapsed_ms;

        tp_ttfb = ngx_timeofday();
        now_ms = (ngx_msec_t) (tp_ttfb->sec * 1000
            + tp_ttfb->msec);
        elapsed_ms = (now_ms >= ctx->streaming.ttfb.feed_start_ms)
            ? (now_ms - ctx->streaming.ttfb.feed_start_ms) : 0;

        /* Gauge store: see send_output TTFB comment for rationale. */
        ngx_http_markdown_metrics->streaming.last_ttfb_ms =
            (ngx_atomic_t) elapsed_ms;
        ctx->streaming.ttfb.recorded = 1;
    }

    /*
     * Clear pending_has_data after TTFB sampling has consumed
     * the latch, so future re-entry does not observe a stale
     * flag from a prior send_output NGX_AGAIN.
     */
    ctx->streaming.pending_has_data = 0;

    /*
     * Account for deferred output bytes that were saved on
     * NGX_AGAIN in send_output and now confirmed delivered.
     */
    if ((rc == NGX_OK || rc == NGX_DONE)
        && ctx->streaming.pending_output_bytes > 0)
    {
        NGX_HTTP_MARKDOWN_METRIC_ADD(
            streaming.selection.output_bytes_total,
            (ngx_atomic_int_t) ctx->streaming.pending_output_bytes);
    }
    ctx->streaming.pending_output_bytes = 0;

    /*
     * If the drained pending chain carried a last_buf (closing
     * bytes from safe_finish), mark main_terminal_sent now that
     * delivery is confirmed.  This complements the fix in
     * send_closing()/send_terminal() which defers the latch
     * on NGX_AGAIN.
     */
    if ((rc == NGX_OK || rc == NGX_DONE) && pending_last_buf) {
        ctx->streaming.main_terminal_sent = 1;
    }

    /*
     * Pending output drained. Check if resume failed before
     * proceeding to deferred lastbuf, to avoid the failure
     * branch being short-circuited.
     */
    if (rc != NGX_OK && rc != NGX_DONE) {
        /*
         * Resume failed after draining pending output.
         * Clear any pending terminal metrics latch and failopen
         * delivery latch to avoid stale state on re-entry, then
         * record failure metrics.
         */
        ctx->streaming.completion.pending_terminal_metrics = 0;
        ctx->streaming.pending_failopen_delivery = 0;
        ngx_http_markdown_streaming_record_postcommit_failure(
            r, ctx, conf);

        return rc;
    }

    /*
     * Pending output drained successfully. If this was a
     * fail-open delivery that was deferred by backpressure,
     * increment the delivery counter now (Rule 38).
     */
    if (ctx->streaming.pending_failopen_delivery) {
        NGX_HTTP_MARKDOWN_METRIC_INC(results.failopen_count);
        ctx->streaming.pending_failopen_delivery = 0;
    }

    /*
     * Pending output drained successfully. If the terminal
     * last_buf send was deferred due to backpressure during
     * finalize(), record the success metrics now that the
     * drain has confirmed delivery.
     */
    if (ctx->streaming.completion.pending_terminal_metrics) {
        NGX_HTTP_MARKDOWN_METRIC_INC(streaming.succeeded_total);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_succeeded);
        NGX_HTTP_MARKDOWN_METRIC_INC(results.delivery_count);

        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_streaming_convert());

        ngx_http_markdown_record_per_path_metrics(r, conf, 0);

        if (ctx->otel_span != NULL) {
            ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
                (const u_char *) "input_bytes", 11,
                (int64_t) ctx->streaming.total_input_bytes);
            ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
                (const u_char *) "output_bytes", 12,
                (int64_t) ctx->streaming.total_output_bytes);
            ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
                (const u_char *) "error_code", 10,
                (int64_t) 0);
            ngx_http_markdown_otel_span_end(ctx->otel_span);
            ngx_http_markdown_otel_span_export(ctx->otel_span,
                r->connection->log, r);
            ctx->otel_span = NULL;
        }

        ctx->streaming.completion.pending_terminal_metrics = 0;
    }

    /*
     * Pending output drained successfully. If finalize deferred
     * the terminal last_buf, send it now.
     */
    if (ctx->streaming.completion.finalize_pending_lastbuf) {
        return ngx_http_markdown_streaming_send_deferred_lastbuf(
            r, ctx, conf);
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
    const ngx_http_markdown_conf_t *conf)
{
    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
        "markdown: Pre-Commit fallback "
        "to full-buffer path");

    /* Release the streaming handle */
    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    if (ctx->otel_span != NULL) {
        ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
            (const u_char *) "fallback", 8,
            (const u_char *) "fullbuffer", 10);
        ngx_http_markdown_otel_span_end(ctx->otel_span);
        ngx_http_markdown_otel_span_export(ctx->otel_span,
            r->connection->log, r);
        ctx->otel_span = NULL;
    }

    /* Switch to full-buffer path */
    ctx->processing_path =
        NGX_HTTP_MARKDOWN_PATH_FULLBUFFER;

    /*
     * Clear conversion_attempted so the full-buffer body
     * filter path will actually perform the conversion on
     * the prebuffered data. Without this reset the main
     * body filter sees conversion_attempted == 1 and
     * short-circuits to ngx_http_next_body_filter,
     * forwarding unconverted HTML.
     */
    ctx->conversion.attempted = 0;

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
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.streaming_fallback_precommit_pass);

    ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
        ngx_http_markdown_reason_streaming_fallback());

    ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
        "markdown: streaming fallback: "
        "engine=full_buffer phase=precommit "
        "committed=0 fallback_available=1 "
        "reason=precommit_html_error "
        "content_type=%V "
        "content_length_known=%d "
        "error_policy=pass",
        &r->headers_out.content_type,
        (r->headers_out.content_length_n >= 0) ? 1 : 0);

    /*
     * Transfer prebuffer data to the main buffer for
     * full-buffer conversion. The prebuffer contains
     * already-decompressed upstream data.
     */
    if (ctx->streaming.prebuffer_initialized
        && ctx->streaming.prebuffer.size > 0)
    {
        ngx_int_t  rc;
        size_t     body_limit;

        if (!ctx->buffer_initialized) {
            body_limit = ngx_http_markdown_effective_body_buffer_limit(
                ctx->effective_conf, conf);
            rc = ngx_http_markdown_buffer_init(
                &ctx->buffer, body_limit, r->pool);
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
 * After headers or Markdown bytes have been sent, the response
 * cannot revert to HTML or send an HTTP error status.  Attempts
 * Rust safe_finish first; falls back to abort + empty last_buf
 * only if safe_finish fails.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_postcommit_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    uint32_t error_code)
{
    ngx_int_t  rc;

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
        "markdown: Post-Commit error, "
        "code=%ui, attempting safe_finish",
        (ngx_uint_t) error_code);

    /* Track budget exceeded as auxiliary classification */
    if (!ctx->streaming.completion.failure_recorded
        && (error_code == ERROR_MEMORY_LIMIT
        || error_code == ERROR_BUDGET_EXCEEDED
        || error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED
        || error_code == ERROR_PARSE_TIMEOUT
        || error_code == ERROR_PARSE_BUDGET_EXCEEDED)
    )
    {
        ctx->streaming.reason =
            NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_BUDGET_EXCEEDED;
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.budget_exceeded_total);
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_resource_limit);
        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_streaming_budget_exceeded());
    } else if (!ctx->streaming.completion.failure_recorded) {
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);
    }

    ngx_http_markdown_streaming_record_postcommit_failure(
        r, ctx, conf);

    /*
     * Debug log: bytes already sent, error type, chunks
     * processed. Always emitted regardless of
     * streaming_on_error (post-commit is unconditional).
     */
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: post-commit error, "
        "bytes_sent=%uz, error_code=%ui, chunks=%ui",
        ctx->streaming.total_output_bytes,
        (ngx_uint_t) error_code,
        ctx->streaming.chunks_processed);

    /*
     * Attempt safe_finish first: ask the Rust converter to
     * emit closing markers for open Markdown structures.
     * If safe_finish fails, fall through to abort.
     *
     * NGX_AGAIN is a legitimate pending state: closing bytes
     * are saved to pending_output and will be drained by
     * resume_pending(). Do NOT fall through to abort/terminal.
     */
    rc = ngx_http_markdown_stream_postcommit_safe_finish(r, ctx);
    if (rc == NGX_OK) {
        return NGX_OK;
    }

    if (rc == NGX_AGAIN) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: post-commit safe_finish "
            "returned NGX_AGAIN, closing bytes pending");
        return NGX_AGAIN;
    }

    /* Safe-finish failed: abort the Rust handle and send terminal */
    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    return ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);
}

static ngx_http_markdown_stream_reason_e
ngx_http_markdown_streaming_precommit_reason(
    const ngx_http_markdown_ctx_t *ctx, uint32_t error_code)
{
    if (ctx->streaming.reason
        >= NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_HTML_ERROR
        && ctx->streaming.reason
           < NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_PARSE_ERROR)
    {
        return ctx->streaming.reason;
    }

    if (error_code == ERROR_PARSE_TIMEOUT) {
        return NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_TIMEOUT;
    }

    if (error_code == ERROR_MEMORY_LIMIT
        || error_code == ERROR_BUDGET_EXCEEDED
        || error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED
        || error_code == ERROR_PARSE_BUDGET_EXCEEDED)
    {
        return NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_BUDGET;
    }

    return NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_HTML_ERROR;
}

/*
 * Pre-Commit error handler: apply error_policy for streaming.
 *
 * Single entry point for all pre-commit streaming failures.
 * Routes based on error_code and the markdown_error_policy directive.
 *
 *   error_code == ERROR_STREAMING_FALLBACK:
 *     Capability fallback to full-buffer path, regardless of
 *     error_policy setting.
 *
 *   error_code == 0 (or any non-FALLBACK value):
 *     error_policy == pass      -> fail-open (original HTML)
 *     error_policy == fail_closed -> fail-closed (error)
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
    const ngx_http_markdown_conf_t *conf,
    uint32_t error_code)
{
    ngx_http_markdown_stream_reason_e  mapped_reason;

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

    mapped_reason = ngx_http_markdown_streaming_precommit_reason(
        ctx, error_code);

    /*
     * Track budget exceeded as auxiliary classification.
     * Covers both Rust FFI budget exceeded (ERROR_BUDGET_EXCEEDED = 6,
     * from markdown_streaming_feed/finalize) and C-side size-limit
     * overflow (ERROR_MEMORY_LIMIT = 4, from cumulative input checks),
     * as well as v0.7.0 resource-limit codes:
     *   ERROR_DECOMPRESSION_BUDGET_EXCEEDED (9),
     *   ERROR_PARSE_TIMEOUT (10),
     *   ERROR_PARSE_BUDGET_EXCEEDED (11).
     * The terminal state is determined by streaming_on_error
     * policy below.
     */
    if (error_code == ERROR_MEMORY_LIMIT
        || error_code == ERROR_BUDGET_EXCEEDED
        || error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED
        || error_code == ERROR_PARSE_TIMEOUT
        || error_code == ERROR_PARSE_BUDGET_EXCEEDED)
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.budget_exceeded_total);
        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_streaming_budget_exceeded());
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: budget exceeded "
            "(auxiliary classification, code=%ui)",
            (ngx_uint_t) error_code);
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.failed_total);

    /*
     * Increment global conversions_failed to maintain consistency
     * with the full-buffer path.  The streaming path increments
     * conversions_attempted at init; every terminal failure must
     * have a matching conversions_failed increment so that
     * attempted >= succeeded + failed holds.
     *
     * For resource-limit errors, also increment
     * failures_resource_limit for the global failure-reason
     * breakdown (streaming security enforcement eligibility audit §3.1).
     */
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);

    if (error_code == ERROR_MEMORY_LIMIT
        || error_code == ERROR_BUDGET_EXCEEDED
        || error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED
        || error_code == ERROR_PARSE_TIMEOUT
        || error_code == ERROR_PARSE_BUDGET_EXCEEDED)
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_resource_limit);
    } else {
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);
    }

    if (conf->stream.on_error
        == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
    {
        /* Fail-closed: record reject metrics and reason */
        ctx->streaming.reason = mapped_reason;
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.precommit_reject_total);
        NGX_HTTP_MARKDOWN_METRIC_INC(
            streaming.streaming_fallback_precommit_reject);
        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_streaming_precommit_reject());
        ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
            "markdown: streaming fallback: "
            "engine=rejected phase=precommit "
            "committed=0 fallback_available=0 "
            "reason=%s content_type=%V "
            "content_length_known=%d "
            "error_policy=fail_closed",
            ngx_http_markdown_stream_reason_str(
                ctx->streaming.reason),
            &r->headers_out.content_type,
            (r->headers_out.content_length_n >= 0) ? 1 : 0);
        return NGX_ERROR;
    }

    /* Fail-open: pass original content */
    ctx->streaming.reason = mapped_reason;
    ctx->eligible = 0;
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.precommit_failopen_total);
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.streaming_fallback_precommit_pass);
    ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
        ngx_http_markdown_reason_streaming_precommit_failopen());
    ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
        "markdown: streaming fallback: "
        "engine=passthrough phase=precommit "
        "committed=0 fallback_available=1 "
        "reason=%s content_type=%V "
        "content_length_known=%d "
        "error_policy=pass",
        ngx_http_markdown_stream_reason_str(
            ctx->streaming.reason),
        &r->headers_out.content_type,
        (r->headers_out.content_length_n >= 0) ? 1 : 0);
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
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_streaming_update_headers(
        r, ctx, conf);
    if (rc != NGX_OK) {
        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, ERROR_STREAMING_FALLBACK);
    }

    rc = ngx_http_next_header_filter(r);
    if (rc == NGX_ERROR || rc > NGX_OK) {
        return rc;
    }

    ctx->streaming.commit_state =
        NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx->headers_forwarded = 1;

    /*
     * stream_sm.state and headers_committed were already set by
     * stream_commit_headers().  Verify they are consistent with
     * the runtime commit_state transition.
     */
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: commit "
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
    const ngx_http_markdown_conf_t *conf,
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
            "markdown: feed error "
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

        if (ctx->streaming.total_output_bytes_overflowed) {
            /* latch is sticky: skip all further additions */
        } else if (out_len > (size_t) -1
                            - ctx->streaming.total_output_bytes)
        {
            ctx->streaming.total_output_bytes = (size_t) -1;
            ctx->streaming.total_output_bytes_overflowed = 1;
        } else {
            ctx->streaming.total_output_bytes += out_len;
        }

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

static uint32_t
ngx_http_markdown_streaming_map_feed_decomp_error(ngx_int_t rc)
{
    if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total);
        return ERROR_DECOMPRESSION_BUDGET_EXCEEDED;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.format_error_total);
        return ERROR_DECOMPRESSION_FORMAT_ERROR;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.truncated_input_total);
        return ERROR_DECOMPRESSION_TRUNCATED_INPUT;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.io_error_total);
    return ERROR_DECOMPRESSION_IO_ERROR;
}

static uint32_t
ngx_http_markdown_streaming_map_finalize_decomp_error(
    const ngx_http_markdown_ctx_t *ctx,
    ngx_int_t rc)
{
    if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total);
        return ERROR_DECOMPRESSION_BUDGET_EXCEEDED;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.format_error_total);
        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return ERROR_POST_COMMIT;
        }
        return ERROR_DECOMPRESSION_FORMAT_ERROR;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.truncated_input_total);
        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return ERROR_POST_COMMIT;
        }
        return ERROR_DECOMPRESSION_TRUNCATED_INPUT;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.io_error_total);
    if (ctx->streaming.commit_state
        == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
    {
        return ERROR_POST_COMMIT;
    }
    return ERROR_DECOMPRESSION_IO_ERROR;
}

static ngx_int_t
ngx_http_markdown_streaming_track_feed_budget(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const u_char *feed_data,
    size_t feed_len)
{
    ngx_int_t  rc;

    if (feed_len > (size_t) -1
                   - ctx->streaming.total_input_bytes)
    {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown: input size "
            "overflow detected");

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return
                ngx_http_markdown_streaming_handle_postcommit_error(
                    r, ctx, conf, ERROR_MEMORY_LIMIT);
        }

        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, ERROR_MEMORY_LIMIT);
    }

    ctx->streaming.total_input_bytes += feed_len;
    {
        size_t  body_limit;

        body_limit = ngx_http_markdown_effective_body_buffer_limit(
            ctx->effective_conf, conf);

        if (body_limit > 0
            && ctx->streaming.total_input_bytes > body_limit)
        {
            ngx_log_error(NGX_LOG_WARN,
                r->connection->log, 0,
                "markdown: size limit "
                "exceeded, total=%uz, max=%uz",
                ctx->streaming.total_input_bytes,
                body_limit);

            if (ctx->streaming.commit_state
                == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
            {
                return
                    ngx_http_markdown_streaming_handle_postcommit_error(
                        r, ctx, conf, ERROR_MEMORY_LIMIT);
            }

            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, ERROR_MEMORY_LIMIT);
        }
    }

    /*
     * Parser budget enforcement (cumulative input size limit).
     *
     * Use cumulative input bytes as a proxy for parser memory
     * pressure — matching the full-buffer path which rejects when
     * input_size > parser_memory_budget (html5ever does not expose
     * internal memory tracking).
     *
     * parser_budget == 0 means unlimited (no enforcement).
     */
    if (conf->decompress.parser_budget > 0
        && ctx->streaming.total_input_bytes
           > conf->decompress.parser_budget)
    {
        ngx_log_error(NGX_LOG_WARN,
            r->connection->log, 0,
            "markdown: parser budget "
            "exceeded, total=%uz, budget=%uz",
            ctx->streaming.total_input_bytes,
            conf->decompress.parser_budget);

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return
                ngx_http_markdown_streaming_handle_postcommit_error(
                    r, ctx, conf,
                    ERROR_PARSE_BUDGET_EXCEEDED);
        }

        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, ERROR_PARSE_BUDGET_EXCEEDED);
    }

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
                "markdown: prebuffer "
                "limit exceeded");

            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, ERROR_BUDGET_EXCEEDED);
        }
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
    const ngx_http_markdown_conf_t *conf,
    const ngx_buf_t *buf)
{
    const u_char  *feed_data;
    size_t     feed_len;
    u_char    *out_data;
    size_t     out_len;
    uint32_t   rc_ffi;
    ngx_int_t  rc;

    if (buf == NULL) {
        return NGX_OK;
    }

    feed_len = ngx_http_markdown_buf_len_safe(buf);
    if (feed_len == 0) {
        return NGX_OK;
    }
    feed_data = buf->pos;

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
            uint32_t  decomp_error_code;

            decomp_error_code =
                ngx_http_markdown_streaming_map_feed_decomp_error(rc);

            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown: "
                "decompression failed (rc=%i)",
                rc);

            if (ctx->streaming.commit_state
                == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
            {
                return
                    ngx_http_markdown_streaming_handle_postcommit_error(
                        r, ctx, conf, decomp_error_code);
            }

            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, decomp_error_code);
        }

        if (decomp_data == NULL || decomp_len == 0) {
            return NGX_OK;
        }

        feed_data = decomp_data;
        feed_len = decomp_len;
    }

    rc = ngx_http_markdown_streaming_track_feed_budget(
        r, ctx, conf, feed_data, feed_len);
    if (rc != NGX_OK) {
        return rc;
    }

    /* Step 4: Feed to Rust streaming engine */
    out_data = NULL;
    out_len = 0;

    /*
     * Record feed_start_ms on the first non-empty feed call
     * (one-shot latch).  This ensures TTFB measures actual
     * processing time, not idle time between handle creation
     * and the first upstream chunk.
     */
    if (ctx->streaming.ttfb.feed_start_ms == 0) {
        const ngx_time_t  *tp_feed;

        tp_feed = ngx_timeofday();
        ctx->streaming.ttfb.feed_start_ms =
            (ngx_msec_t) (tp_feed->sec * 1000
                + tp_feed->msec);
    }

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
    const ngx_http_markdown_conf_t *conf)
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
        uint32_t  finish_error_code;

        finish_error_code =
            ngx_http_markdown_streaming_map_finalize_decomp_error(
                ctx, rc);

        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown: decomp_finish "
            "failed in finalize (rc=%i)",
            rc);

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return
                ngx_http_markdown_streaming_handle_postcommit_error(
                    r, ctx, conf,
                    finish_error_code);
        }

        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, finish_error_code);
    }

    if (decomp_data != NULL && decomp_len > 0) {
        u_char    *out_data;
        size_t     out_len;
        uint32_t   feed_rc;
        ngx_int_t  feed_result;

        out_data = NULL;
        out_len = 0;

        /*
         * Budget check: tail decompression bytes count toward
         * total_input_bytes the same as normal feed data.
         * Without this, a crafted compressed stream could hide
         * excess data in the decompression tail and bypass the
         * max_size limit (Property 2: Budget Monotonicity).
         */
        rc = ngx_http_markdown_streaming_track_feed_budget(
            r, ctx, conf, decomp_data, decomp_len);
        if (rc != NGX_OK) {
            return rc;
        }

        /*
         * Record feed_start_ms if this is the first feed
         * (EOF-only decompressor path where process_chunk
         * was never called with non-empty data).
         */
        if (ctx->streaming.ttfb.feed_start_ms == 0) {
            const ngx_time_t  *tp_feed;

            tp_feed = ngx_timeofday();
            ctx->streaming.ttfb.feed_start_ms =
                (ngx_msec_t) (tp_feed->sec * 1000
                    + tp_feed->msec);
        }

        feed_rc = markdown_streaming_feed(
            ctx->streaming.handle,
            decomp_data, decomp_len,
            &out_data, &out_len);

        ctx->streaming.chunks_processed++;

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

static ngx_int_t
ngx_http_markdown_streaming_handle_finalize_ffi_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    struct MarkdownResult *result,
    uint32_t rc_ffi)
{
    ngx_log_error(NGX_LOG_ERR,
        r->connection->log, 0,
        "markdown: finalize error "
        "code=%ui", (ngx_uint_t) rc_ffi);

    if (ctx->otel_span != NULL) {
        ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
            (const u_char *) "input_bytes", 11,
            (int64_t) ctx->streaming.total_input_bytes);
        ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
            (const u_char *) "output_bytes", 12,
            (int64_t) ctx->streaming.total_output_bytes);
        ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
            (const u_char *) "error_code", 10,
            (int64_t) rc_ffi);
        ngx_http_markdown_otel_span_end(ctx->otel_span);
        ngx_http_markdown_otel_span_export(ctx->otel_span,
            r->connection->log, r);
        ctx->otel_span = NULL;
    }

    markdown_result_free(result);

    if (ctx->streaming.commit_state
        == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
    {
        return
            ngx_http_markdown_streaming_handle_postcommit_error(
                r, ctx, conf, rc_ffi);
    }

    return ngx_http_markdown_streaming_precommit_error(
        r, ctx, conf, rc_ffi);
}

static ngx_int_t
ngx_http_markdown_streaming_finalize_send_markdown(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    struct MarkdownResult *result,
    ngx_int_t *final_send_rc)
{
    ngx_int_t  rc;

    if (result->markdown == NULL || result->markdown_len == 0) {
        *final_send_rc = NGX_OK;
        return NGX_OK;
    }

    if (ctx->streaming.total_output_bytes_overflowed) {
        /* latch is sticky: skip all further additions */
    } else if (result->markdown_len > (size_t) -1
                - ctx->streaming.total_output_bytes)
    {
        ctx->streaming.total_output_bytes = (size_t) -1;
        ctx->streaming.total_output_bytes_overflowed = 1;
    } else {
        ctx->streaming.total_output_bytes += result->markdown_len;
    }

    if (ctx->streaming.commit_state
        == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE)
    {
        rc = ngx_http_markdown_streaming_commit(
            r, ctx, conf);
        if (rc != NGX_OK && rc != NGX_AGAIN) {
            markdown_result_free(result);
            return rc;
        }
    }

    rc = ngx_http_markdown_streaming_send_output(
        r, ctx, result->markdown,
        result->markdown_len, /* last_buf */ 0);
    if (rc != NGX_OK
        && rc != NGX_DONE
        && rc != NGX_AGAIN)
    {
        markdown_result_free(result);
        ngx_http_markdown_streaming_record_postcommit_failure(
            r, ctx, conf);
        return rc;
    }

    *final_send_rc = rc;
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
    const ngx_http_markdown_conf_t *conf)
{
    struct MarkdownResult  result;
    uint32_t               rc_ffi;
    ngx_int_t              rc;
    ngx_int_t              final_send_rc;

    markdown_result_init(&result);

    if (ctx->streaming.handle == NULL) {
        return ngx_http_markdown_streaming_send_output(
            r, ctx, NULL, 0, /* last_buf */ 1);
    }

    ctx->streaming.completion.finalize_after_pending = 0;

    /* Finish decompression and feed tail data if any */
    rc = ngx_http_markdown_streaming_finalize_decomp(
        r, ctx, conf);
    if (rc == NGX_AGAIN) {
        ctx->streaming.completion.finalize_after_pending = 1;
        return NGX_AGAIN;
    }
    if (rc != NGX_OK) {
        return rc;
    }

    /* Finalize the streaming converter */
    markdown_result_init(&result);

    rc_ffi = markdown_streaming_finalize(
        ctx->streaming.handle, &result);

    /* Handle is consumed by finalize regardless of result */
    ctx->streaming.handle = NULL;

    if (rc_ffi != ERROR_SUCCESS) {
        return ngx_http_markdown_streaming_handle_finalize_ffi_error(
            r, ctx, conf, &result, rc_ffi);
    }

    rc = ngx_http_markdown_streaming_finalize_send_markdown(
        r, ctx, conf, &result, &final_send_rc);
    if (rc != NGX_OK) {
        return rc;
    }

    /* Log ETag if available (debug observability) */
    if (result.etag != NULL && result.etag_len > 0) {
        ngx_log_debug3(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: finalize ETag "
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
            "markdown: etag=%*s "
            "uri=%V "
            "out_bytes=%uz tokens=%ui",
            result.etag_len, result.etag,
            &r->uri,
            ctx->streaming.total_output_bytes,
            (ngx_uint_t) result.token_estimate);
    }

    /*
     * Capture peak_memory_estimate BEFORE freeing the result.
     *
     * The Rust FFI populates this field in MarkdownResult, but
     * markdown_result_free() may zero or invalidate the struct.
     * Save it to a local variable first to ensure metric stability.
     */
    size_t  peak_memory_bytes = result.peak_memory_estimate;

    markdown_result_free(&result);

    /* Log streaming conversion statistics */
    ngx_log_debug4(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: completed "
        "chunks=%ui flushes=%ui "
        "in_bytes=%uz out_bytes=%uz",
        ctx->streaming.chunks_processed,
        ctx->streaming.flushes_sent,
        ctx->streaming.total_input_bytes,
        ctx->streaming.total_output_bytes);

    /*
     * Record peak memory estimate from Rust streaming stats.
     * This is a gauge metric (per-request sample) updated on each
     * successful streaming conversion.
     *
     * Always update unconditionally so the gauge reflects the
     * most recent request, even if peak_memory_bytes is 0 (for
     * example empty response or extremely small input).
     *
     * Gauge store: latest-value-wins semantics.  Direct assignment
     * to ngx_atomic_t is not formally atomic per C11 §7.1.4¶1,
     * but ngx_atomic_t is intptr_t-sized and naturally aligned on
     * all NGINX platforms (x86_64, ARM64, x86), making the store
     * word-atomic in practice.  A torn read would produce a stale
     * byte count — acceptable for a diagnostic gauge.
     */
    if (ngx_http_markdown_metrics != NULL) {
        ngx_http_markdown_metrics->streaming.last_peak_memory_bytes =
            (ngx_atomic_t) peak_memory_bytes;
    }

    /*
     * Send final empty last_buf to terminate the response.
     *
     * If the final markdown send returned NGX_AGAIN (backpressure),
     * defer the terminal last_buf — sending it now would overwrite
     * the pending markdown chain in send_output, causing data loss.
     * Return NGX_AGAIN so the resume mechanism drains the pending
     * output first; the caller will re-enter finalize on the next
     * write event.
     *
     * IMPORTANT: Success metrics (succeeded_total, reason code)
     * are NOT recorded here when final_send_rc == NGX_AGAIN.
     * They are recorded only after the deferred last_buf is
     * confirmed sent with NGX_OK/NGX_DONE in
     * ngx_http_markdown_streaming_send_deferred_lastbuf(),
     * to avoid observability drift when NGX_AGAIN is followed
     * by a downstream send failure.
     *
     * When final_send_rc is NGX_OK or NGX_DONE, the send
     * completed immediately so we record metrics here.
     */
    if (final_send_rc == NGX_AGAIN) {
        ctx->streaming.completion.finalize_pending_lastbuf = 1;
        return ngx_http_markdown_streaming_handle_backpressure(
            r, ctx);
    }

    /*
     * Send the terminal last_buf and record success/failure
     * metrics based on the actual send result.
     *
     * This ensures consistent observability semantics:
     * - NGX_OK/NGX_DONE: record success metrics immediately
     * - NGX_AGAIN: defer metrics to resume_pending() via
     *   pending_terminal_metrics latch
     * - Other errors: record failure metrics immediately
     */
    rc = ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);

    if (rc == NGX_OK || rc == NGX_DONE) {
        NGX_HTTP_MARKDOWN_METRIC_INC(streaming.succeeded_total);
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_succeeded);
        NGX_HTTP_MARKDOWN_METRIC_INC(results.delivery_count);

        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_streaming_convert());

        ngx_http_markdown_record_per_path_metrics(r, conf, 0);

        if (ctx->otel_span != NULL) {
            ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
                (const u_char *) "input_bytes", 11,
                (int64_t) ctx->streaming.total_input_bytes);
            ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
                (const u_char *) "output_bytes", 12,
                (int64_t) ctx->streaming.total_output_bytes);
            ngx_http_markdown_otel_set_int_attr(ctx->otel_span,
                (const u_char *) "error_code", 10,
                (int64_t) 0);
            ngx_http_markdown_otel_span_end(ctx->otel_span);
            ngx_http_markdown_otel_span_export(ctx->otel_span,
                r->connection->log, r);
            ctx->otel_span = NULL;
        }
    } else if (rc == NGX_AGAIN) {
        /*
         * Terminal last_buf send hit backpressure. Set a latch
         * so that resume_pending() knows to record metrics
         * after the drain succeeds.
         */
        ctx->streaming.completion.pending_terminal_metrics = 1;
    } else {
        /*
         * Terminal last_buf send failed with a definitive
         * error (not backpressure). Record failure metrics
         * to match the post-commit error policy used in
         * resume_pending() and send_deferred_lastbuf().
         */
        ngx_http_markdown_streaming_record_postcommit_failure(
            r, ctx, conf);
    }

    return rc;
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
    const ngx_http_markdown_conf_t *conf)
{
    struct MarkdownOptions  options;
    ngx_pool_cleanup_t     *cln;
    uint32_t                init_rc;
    ngx_int_t               rc;

    rc = ngx_http_markdown_prepare_conversion_options(
        r, conf, ctx->effective_conf, &options);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown: failed to "
            "prepare conversion options");
        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, 0);
    }

    init_rc = markdown_streaming_new_with_code(
        &options, &ctx->streaming.handle);
    if (init_rc != ERROR_SUCCESS
        || ctx->streaming.handle == NULL)
    {
        ngx_log_error(NGX_LOG_ERR,
            r->connection->log, 0,
            "markdown: failed to "
            "create streaming handle rc=%ui",
            (ngx_uint_t) init_rc);
        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, init_rc);
    }

    /*
     * Do NOT record feed_start_ms here — it would include
     * idle time between handle creation and the first feed.
     * Instead, feed_start_ms is set on the first non-empty
     * markdown_streaming_feed() call via a one-shot guard
     * in process_chunk.  Initialize to 0 so the guard fires.
     */
    ctx->streaming.ttfb.feed_start_ms = 0;
    ctx->streaming.completion.failure_recorded = 0;

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
                conf->decompress.max_size);
        if (ctx->streaming.decompressor == NULL) {
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown: failed to "
                "create decompressor");
            markdown_streaming_abort(
                ctx->streaming.handle);
            ctx->streaming.handle = NULL;
            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, 0);
        }
    }

    /* Initialize prebuffer for fallback.
     * Use conf->stream.precommit_buffer (not streaming budget):
     * the two budgets have different semantics — precommit_buffer
     * controls pre-commit buffering and fail-open replay, while
     * budget controls the Rust streaming converter memory. */
    ctx->streaming.prebuffer_limit =
        conf->stream.precommit_buffer;
    if (ctx->streaming.prebuffer_limit > 0) {
        rc = ngx_http_markdown_buffer_init(
            &ctx->streaming.prebuffer,
            ctx->streaming.prebuffer_limit,
            r->pool);
        if (rc == NGX_OK) {
            ctx->streaming.prebuffer_initialized = 1;
        } else {
            /*
             * Prebuffer initialization failed due to pool exhaustion.
             * Without a working prebuffer, the streaming
             * fallback-to-fullbuffer path cannot recover already-processed
             * prefix data, so continuing streaming would silently lose data
             * on fallback.  Treat this as a pre-commit error: fail-open
             * (pass) or reject per the configured streaming_on_error policy.
             */
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown: prebuffer init "
                "failed (pool exhaustion), "
                "cannot guarantee fallback data integrity");
            markdown_streaming_abort(
                ctx->streaming.handle);
            ctx->streaming.handle = NULL;
            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, ERROR_MEMORY_LIMIT);
        }
    } else {
        ctx->streaming.prebuffer_initialized = 0;
    }

    ctx->streaming.commit_state =
        NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;

    /*
     * Initialize fail-open replay buffer.  This buffer stores a copy
     * of original upstream bytes consumed during Pre-Commit so that
     * fail-open can reconstruct the full output chain from
     * module-owned memory, rather than relying on upstream ngx_buf_t*
     * pointer stability across filter chain invocations.
     */
    ctx->streaming.failopen_replay_initialized = 0;
    if (ctx->streaming.prebuffer_limit > 0) {
        rc = ngx_http_markdown_buffer_init(
            &ctx->streaming.failopen_replay_buf,
            ctx->streaming.prebuffer_limit,
            r->pool);
        if (rc != NGX_OK) {
            /*
             * Replay buffer initialization failed due to pool exhaustion.
             * Without a working replay buffer, fail-open cannot reconstruct
             * the original upstream prefix data on pre-commit error, so
             * continuing streaming would silently lose data.  Treat this
             * identically to prebuffer init failure: abort the handle and
             * apply the configured streaming_on_error policy.
             */
            NGX_HTTP_MARKDOWN_METRIC_INC(
                results.replay_buffer_errors_total);
            ngx_log_error(NGX_LOG_ERR,
                r->connection->log, 0,
                "markdown: replay buffer init "
                "failed (pool exhaustion), "
                "cannot guarantee fail-open data integrity");
            markdown_streaming_abort(
                ctx->streaming.handle);
            ctx->streaming.handle = NULL;
            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, ERROR_MEMORY_LIMIT);
        }

        ctx->streaming.failopen_replay_initialized = 1;
    }

    ctx->conversion.attempted = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(
        conversions_attempted);
    NGX_HTTP_MARKDOWN_METRIC_INC(
        streaming.requests_total);

    ctx->otel_span = NULL;
    if (conf->ops.otel_enabled) {
        ctx->otel_span = ngx_http_markdown_otel_span_start(r, conf);
        if (ctx->otel_span != NULL) {
            /*
             * Map flavor to OTel attribute string inline to avoid
             * cross-impl-header dependency.  Keep in sync with
             * ngx_http_markdown_otel_flavor_name() in
             * ngx_http_markdown_conversion_impl.h.
             */
            static ngx_str_t  s_gfm = ngx_string("gfm");
            static ngx_str_t  s_mdx = ngx_string("mdx");
            static ngx_str_t  s_org = ngx_string("org-mode");
            static ngx_str_t  s_cm  = ngx_string("commonmark");
            const ngx_str_t  *fn;

            switch (conf->flavor) {
            case NGX_HTTP_MARKDOWN_FLAVOR_GFM:    fn = &s_gfm; break;
            case NGX_HTTP_MARKDOWN_FLAVOR_MDX:    fn = &s_mdx; break;
            case NGX_HTTP_MARKDOWN_FLAVOR_ORG_MODE: fn = &s_org; break;
            default:                              fn = &s_cm;  break;
            }

            ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
                (const u_char *) "flavor", 6,
                fn->data, fn->len);
            ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
                (const u_char *) "engine", 6,
                (const u_char *) "streaming", 9);
            if (r->uri.len > 0) {
                ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
                    (const u_char *) "uri", 3,
                    r->uri.data, r->uri.len);
            }
        }
    }

    /* Sync streaming fallback state machine: handle initialized → PRE_COMMIT */
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: handle created, "
        "entering Pre-Commit phase");

    return NGX_OK;
}


/*
 * Forward original upstream bytes after a Pre-Commit streaming fail-open.
 *
 * The replay buffer contains a copy of all original upstream bytes consumed
 * during Pre-Commit.  On fail-open, we build an output chain from the
 * replay buffer data (module-owned memory) plus the current unconsumed
 * input chain, then forward it downstream.
 *
 * This approach avoids depending on upstream ngx_buf_t* pointer stability
 * across filter chain invocations, which is fragile in complex filter
 * chains, temporary buffer, compression, or subrequest scenarios.
 *
 * On NGX_AGAIN from the downstream filter, the output chain is saved as
 * ctx->streaming.pending_output and the request buffered flag is set,
 * consistent with send_output()'s backpressure contract (Rule 1).
 * resume_pending() will re-submit the chain when downstream is writable.
 *
 * Returns:
 *   NGX_OK/NGX_AGAIN/NGX_DONE - status from the downstream body filter
 *   NGX_ERROR                  - allocation or header-forwarding failure
 */


/*
 * Clone chain link structures into request pool memory.
 *
 * Each link is newly allocated; the buf pointer is copied (shared)
 * so the caller must ensure the underlying ngx_buf_t and its data
 * remain valid for the request lifetime.  The chain topology
 * (->next pointers) is replicated.
 *
 * For fail-open pending chains saved across NGX_AGAIN, this is
 * safer than holding the original chain links (which belong to the
 * body filter's transient input), but still shares the underlying
 * ngx_buf_t.  In the NGINX filter chain, the buf data is typically
 * stable within a request (pool-allocated by upstream or copy
 * filter), making shared bufs safe for pending chains.  If a future
 * filter chain configuration introduces transient buf data that is
 * invalidated between body_filter invocations, upgrade this to
 * clone_chain_deep() which also copies buf data into request pool.
 *
 * Returns the head of the cloned chain, or NULL on allocation failure.
 */
static ngx_chain_t *
ngx_http_markdown_streaming_clone_chain_links(
    ngx_http_request_t *r,
    ngx_chain_t *in)
{
    ngx_chain_t  *head = NULL;
    ngx_chain_t  **tail = &head;
    ngx_chain_t  *cl;

    for (; in != NULL; in = in->next) {
        cl = ngx_alloc_chain_link(r->pool);
        if (cl == NULL) {
            return NULL;
        }
        cl->buf = in->buf;
        cl->next = NULL;
        *tail = cl;
        tail = &cl->next;
    }

    return head;
}


/*
 * Send a fail-open output chain downstream with backpressure and
 * delivery-metric semantics matching send_output()'s contract.
 *
 * On NGX_AGAIN: saves pending_output, sets buffered flag (Rule 1),
 * and sets pending_failopen_delivery latch so resume_pending can
 * increment failopen_count after successful drain (Rule 38).
 * On NGX_OK or NGX_DONE: increments failopen_count if !ctx->eligible
 * (Rule 38: delivery counter after downstream success).
 *
 * Returns the downstream filter return code.
 */
static ngx_int_t
ngx_http_markdown_streaming_send_failopen_chain(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *out)
{
    ngx_int_t  rc;

    rc = ngx_http_next_body_filter(r, out);

    if (rc == NGX_AGAIN) {
        if (ctx->streaming.pending_output != NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: fail-open pending output "
                "re-entry detected, refusing to overwrite "
                "existing pending chain (Rule 1)");

            ngx_http_markdown_streaming_free_pending_chain(
                ctx->streaming.pending_output);
            ctx->streaming.pending_output = NULL;

            return NGX_ERROR;
        }

        ctx->streaming.pending_output = out;
        ctx->streaming.pending_has_data = 1;
        ctx->streaming.pending_failopen_delivery = (!ctx->eligible) ? 1 : 0;
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
        return NGX_AGAIN;
    }

    if ((rc == NGX_OK || rc == NGX_DONE) && !ctx->eligible) {
        NGX_HTTP_MARKDOWN_METRIC_INC(results.failopen_count);
    }

    return rc;
}


static ngx_int_t
ngx_http_markdown_streaming_failopen_passthrough(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *in)
{
    ngx_chain_t  *head;
    ngx_chain_t  **tail;
    ngx_chain_t  *cl;
    ngx_buf_t    *b;

    if (!ctx->headers_forwarded
        && ngx_http_markdown_forward_headers(r, ctx) != NGX_OK)
    {
        return NGX_ERROR;
    }

    if (!ctx->streaming.failopen_replay_initialized
        || ctx->streaming.failopen_replay_buf.size == 0)
    {
        ngx_chain_t  *cloned;

        cloned = ngx_http_markdown_streaming_clone_chain_links(r, in);
        if (cloned == NULL && in != NULL) {
            return NGX_ERROR;
        }
        return ngx_http_markdown_streaming_send_failopen_chain(r, ctx, cloned);
    }

    /*
     * Build a prefix chain link from the replay buffer data.
     * The replay buffer is module-owned and remains valid for
     * the request lifetime (pool-allocated).
     */
    head = NULL;
    tail = &head;

    cl = ngx_alloc_chain_link(r->pool);
    if (cl == NULL) {
        return NGX_ERROR;
    }

    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        return NGX_ERROR;
    }

    b->pos = ctx->streaming.failopen_replay_buf.data;
    b->last = b->pos + ctx->streaming.failopen_replay_buf.size;
    b->memory = 1;
    b->last_buf = 0;

    cl->buf = b;
    cl->next = NULL;
    *tail = cl;
    tail = &cl->next;

    {
        ngx_chain_t  *cloned;

        cloned = ngx_http_markdown_streaming_clone_chain_links(r, in);
        if (cloned == NULL && in != NULL) {
            return NGX_ERROR;
        }
        *tail = cloned;
    }

    return ngx_http_markdown_streaming_send_failopen_chain(r, ctx, head);
}


/*
 * Handle the result of process_chunk within the body filter loop.
 *
 * Dispatches fallback, fail-open, and error paths. Returns the value the body
 * filter should return, or NGX_OK to continue processing the next buffer in the
 * chain. NGX_AGAIN is preserved as backpressure and must not be treated as
 * success.
 *
 * Side effects:
 *   Sets ctx->failopen_completed to 1 if fail-open passthrough has already
 *   forwarded the original response downstream (including any terminal
 *   buffer), so the caller must not enter finalize_request and must stop
 *   processing remaining chain links.
 *
 * Returns:
 *   NGX_OK       - continue processing next buffer (or stop if
 *                  ctx->failopen_completed is set)
 *   NGX_AGAIN    - backpressure, return immediately
 *   NGX_DONE     - streaming fell back; caller should re-enter full-buffer path
 *   NGX_ERROR    - fatal error
 *   other        - return value to propagate
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_chunk_result(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *in,
    ngx_int_t rc)
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
        rc = ngx_http_markdown_streaming_failopen_passthrough(
            r, ctx, in);
        if (rc == NGX_DONE) {
            rc = NGX_OK;
        }
        if (rc == NGX_OK || rc == NGX_AGAIN) {
            ctx->failopen_completed = 1;
        }
        return rc;
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
    const ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    if (!r->connection->error) {
        return 0;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: client abort "
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
    const ngx_http_markdown_conf_t *conf,
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
         * chain downstream.  Route through the shared
         * fail-open send path so results.failopen_count
         * is incremented consistently.
         */
        if (!ctx->headers_forwarded) {
            rc = ngx_http_markdown_forward_headers(
                r, ctx);
            if (rc != NGX_OK) {
                return NGX_ERROR;
            }
        }

        return ngx_http_markdown_streaming_send_failopen_chain(
            r, ctx, in);
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


/*
 * Resume pending output when the body filter is re-entered with NULL input.
 *
 * NULL input is used by NGINX filter re-entry to give the module a chance to
 * flush data saved after downstream NGX_AGAIN.  If finalization was deferred
 * behind pending output, finalize only after that chain has drained.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_null_input(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_streaming_resume_pending(
        r, ctx, conf);
    if (rc != NGX_OK) {
        return rc;
    }

    if (ctx->streaming.completion.finalize_after_pending) {
        ctx->streaming.completion.finalize_after_pending = 0;
        return ngx_http_markdown_streaming_finalize_request(
            r, ctx, conf);
    }

    return NGX_OK;
}

static ngx_int_t
ngx_http_markdown_streaming_append_replay_chunk(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_chain_t *cl)
{
    ngx_int_t  rc;
    size_t     chunk_len;

    if (ctx->streaming.commit_state
        != NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE
        || !ctx->streaming.failopen_replay_initialized)
    {
        return NGX_OK;
    }

    chunk_len = ngx_http_markdown_buf_len_safe(cl->buf);
    if (chunk_len == 0) {
        return NGX_OK;
    }

    rc = ngx_http_markdown_buffer_append(
        &ctx->streaming.failopen_replay_buf,
        cl->buf->pos, chunk_len);
    if (rc == NGX_OK) {
        return NGX_OK;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(results.replay_buffer_errors_total);
    ngx_log_error(NGX_LOG_ERR,
        r->connection->log, 0,
        "markdown: replay buffer "
        "limit exceeded, aborting streaming "
        "to preserve fail-open data integrity");

    rc = ngx_http_markdown_streaming_precommit_error(
        r, ctx, conf, ERROR_BUDGET_EXCEEDED);
    if (rc == NGX_DECLINED && !ctx->eligible) {
        rc = ngx_http_markdown_streaming_failopen_passthrough(
            r, ctx, cl);
        if (rc == NGX_OK || rc == NGX_AGAIN) {
            ctx->failopen_completed = 1;
        }
    }

    return rc;
}

/*
 * Process every buffer in an input chain through the streaming converter.
 *
 * Tracks terminal buffers, preserves Pre-Commit buffer positions for fail-open
 * replay, and reports the chain link that triggered fallback so callers can
 * re-enter the full-buffer path at the correct point.  NGX_AGAIN is propagated
 * immediately to honor downstream backpressure.
 *
 * Output parameters:
 *   last_buf         - set to 1 if a terminal buffer was observed (unless
 *                      failopen_completed is set, in which case the terminal
 *                      buffer has already been forwarded downstream)
 *   fallback_cl      - set to the chain link that triggered fallback
 *
 * Side effects:
 *   Sets ctx->failopen_completed to 1 if fail-open passthrough has already
 *   forwarded the original response downstream.
 */
static ngx_int_t
ngx_http_markdown_streaming_process_chain(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_chain_t *in,
    ngx_flag_t *last_buf,
    ngx_chain_t **fallback_cl)
{
    ngx_int_t     rc;

    *last_buf = 0;
    *fallback_cl = NULL;

    for (ngx_chain_t *cl = in; cl != NULL; cl = cl->next) {
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
            r, ctx, in, rc);

        if (rc != NGX_OK) {
            if (rc == NGX_DONE) {
                *fallback_cl = cl;
            }
            return rc;
        }

        if (ctx->failopen_completed) {
            return NGX_OK;
        }

        rc = ngx_http_markdown_streaming_append_replay_chunk(
            r, ctx, conf, cl);
        if (rc != NGX_OK) {
            return rc;
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
    const ngx_http_markdown_conf_t  *conf;
    ngx_chain_t               *fallback_cl;
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

    rc = ngx_http_markdown_streaming_process_chain(
        r, ctx, conf, in, &last_buf, &fallback_cl);
    if (rc == NGX_DONE) {
        return ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
            r, fallback_cl, last_buf);
    }
    if (rc != NGX_OK) {
        return rc;
    }

    /*
     * If fail-open passthrough already forwarded the original
     * response (including any terminal buffer), skip finalize
     * to avoid sending a duplicate empty last_buf.
     * Uses ctx->failopen_completed (request-lifetime flag)
     * rather than the local variable, so re-entries also skip.
     */
    if (ctx->failopen_completed) {
        return NGX_OK;
    }

    /* Handle last_buf: finalize */
    if (last_buf) {
        rc = ngx_http_markdown_streaming_finalize_request(
            r, ctx, conf);

        if (rc == NGX_DECLINED && !ctx->eligible) {
            /*
             * Mark failopen_completed on the context before
             * forwarding, so any re-entry of body_filter
             * (e.g. backpressure resume) skips duplicate
             * finalize or passthrough.
             */
            ctx->failopen_completed = 1;
            return ngx_http_markdown_streaming_failopen_passthrough(
                r, ctx, in);
        }

        return rc;
    }

    return NGX_OK;
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_STREAMING_IMPL_H */
