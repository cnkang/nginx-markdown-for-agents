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
#include "ngx_http_markdown_zerocopy_buf.h"
#include "ngx_http_markdown_output_decision_impl.h"


typedef struct {
    ngx_flag_t  main_terminal;
    ngx_flag_t  subrequest_terminal;
} ngx_http_markdown_pending_terminal_t;


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
static void ngx_http_markdown_streaming_start_otel_span(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);
static ngx_flag_t ngx_http_markdown_streaming_delivery_ok(ngx_int_t rc);
static void ngx_http_markdown_streaming_record_send_delivery(
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_pending_terminal_t *terminal,
    const u_char *data, size_t len, ngx_flag_t delivered);
static ngx_int_t ngx_http_markdown_streaming_handle_output_loss(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);

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

static void
ngx_http_markdown_streaming_pending_input_clear(
    ngx_http_markdown_ctx_t *ctx);

static ngx_int_t
ngx_http_markdown_streaming_process_chain(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_chain_t *in,
    ngx_flag_t *last_buf,
    ngx_chain_t **fallback_cl);

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
 * Pre-Commit error handler: apply unified markdown_error_policy.
 *
 * Single entry point for all pre-commit streaming failures.
 * Routes to fallback (ERROR_STREAMING_FALLBACK), fail-open
 * (pass original HTML), or fail-closed (reject) based on
 * the error code and the unified markdown_error_policy directive.
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
 * Streaming policy selector: determine the processing path for a request.
 *
 * Evaluates markdown_streaming and applies the selection rules (policy, HEAD,
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
     * Clear the pending_output anchor/state.
     *
     * Ownership is layered explicitly elsewhere and must not be
     * re-derived here from generic ngx_buf_t behavior flags
     * (memory/temporary describe buffer *behavior*, not Rust
     * allocator *provenance*):
     *
     *   - Zero-copy Rust output buffers (b->memory=1, b->temporary=0,
     *     created by ngx_http_markdown_rust_buf_create_ex) own their
     *     own pool cleanup context (ngx_http_markdown_rust_buf_cleanup_t)
     *     registered at creation time.  That cleanup calls
     *     markdown_streaming_output_free() exactly once, independent
     *     of pending_output's lifetime.
     *   - Pool-copy output buffers (send_output) are ngx_palloc'd from
     *     the request pool and reclaimed with it; nothing to free here.
     *   - Fail-open cloned chain links share the ngx_buf_t with an
     *     upstream/module-owned buffer, which may legitimately be
     *     marked temporary=1.  That memory is never Rust-allocated;
     *     calling markdown_streaming_output_free() on it would be an
     *     invalid free.
     *
     * Walking pending_output and branching on cl->buf->temporary
     * cannot distinguish these cases and previously caused exactly
     * this invalid-free risk for shared fail-open buffers.  Freeing
     * Rust-owned memory is therefore left entirely to the zero-copy
     * buffer's own pool cleanup; this function only clears our
     * tracking state so stale references are not observed after
     * cleanup runs.
     */
    ctx->streaming.pending_output = NULL;

    /*
     * Clear pending input chain.  Links are pool-allocated and will be
     * reclaimed when the request pool is destroyed.  The shared ngx_buf_t
     * pointers are upstream-owned and managed by NGINX.  We only reset
     * our tracking state.
     */
    ngx_http_markdown_streaming_pending_input_clear(ctx);
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
 * Streaming policy selector: determine the processing path for a request.
 *
 * Evaluates markdown_streaming once in the header filter phase and caches
 * the result.
 *
 * Evaluation order (per design doc):
 * 1. policy == off -> PATH_FULLBUFFER
 * 2. streaming feature not compiled -> warn + PATH_FULLBUFFER
 * 3. HEAD request -> PATH_FULLBUFFER
 * 4. 304 Not Modified -> PATH_FULLBUFFER
 * 5. conditional_requests full_support -> PATH_FULLBUFFER
 * 6. Content-Type is text/event-stream -> PATH_FULLBUFFER
 * 7. stream_types exclusion match -> PATH_FULLBUFFER
 * 8. policy == force -> PATH_STREAMING
 * 9. policy == auto + CL >= markdown_stream_threshold -> PATH_STREAMING
 * 10. policy == auto + chunked -> PATH_STREAMING
 * 11. policy == auto + CL < markdown_stream_threshold -> PATH_FULLBUFFER
 *
 * Default (no markdown_streaming directive): auto mode.
 */
static ngx_http_markdown_path_selection_t
ngx_http_markdown_select_processing_path(
    ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    ngx_uint_t   policy;

    if (conf == NULL) {
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_CONFIG_DISABLED);
    }

    policy = conf->stream.policy;

    if (policy == NGX_HTTP_MARKDOWN_STREAMING_OFF) {
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

    /* Rule 8: policy == force */
    if (policy == NGX_HTTP_MARKDOWN_STREAMING_FORCE)
    {
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_STREAMING,
            NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE);
    }

    /* Rules 9-11: policy == auto */
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
 * Synchronize r->buffered with the full streaming pending state.
 *
 * Sets NGX_HTTP_MARKDOWN_BUFFERED when any output, input, finalize, or
 * fail-open abort continuation remains pending.  Clears it otherwise.  This
 * is the single authority for the buffered bit on the streaming path —
 * individual helpers must not set/clear it directly.
 */
static void
ngx_http_markdown_streaming_sync_buffered(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx)
{
    if (ctx->streaming.pending_output != NULL
        || ctx->streaming.pending_input.head != NULL
        || ctx->streaming.completion.finalize_after_pending
        || ctx->streaming.completion.finalize_pending_lastbuf
        || ctx->streaming.completion.failopen_abort_after_pending)
    {
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
    } else {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
    }
}


/*
 * Pending input chain management.
 *
 * Retains unconsumed upstream ngx_chain_t links for re-feed after
 * downstream backpressure clears.  Links are pool-allocated copies
 * that share the original ngx_buf_t — no payload duplication.  NGINX
 * keeps busy buffers alive while pos < last, so shared bufs remain
 * valid until we advance pos after feeding them to Rust.
 */

static void
ngx_http_markdown_streaming_pending_input_clear(
    ngx_http_markdown_ctx_t *ctx)
{
    ctx->streaming.pending_input.head = NULL;
    ctx->streaming.pending_input.tail = NULL;
    ctx->streaming.pending_input.bytes = 0;
    ctx->streaming.pending_input.links = 0;
}

static ngx_flag_t
ngx_http_markdown_streaming_pending_input_is_empty(
    const ngx_http_markdown_ctx_t *ctx)
{
    return (ctx->streaming.pending_input.head == NULL) ? 1 : 0;
}

static void
ngx_http_markdown_streaming_abandon_input(ngx_chain_t *in)
{
    for (; in != NULL; in = in->next) {
        if (in->buf != NULL) {
            in->buf->pos = in->buf->last;
        }
    }
}

static void
ngx_http_markdown_streaming_pending_input_abandon_and_clear(
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_http_markdown_streaming_abandon_input(
        ctx->streaming.pending_input.head);
    ngx_http_markdown_streaming_pending_input_clear(ctx);
}


/*
 * Abandon every module-owned continuation after irrecoverable output loss.
 *
 * pending_output may already be downstream-owned after NGX_AGAIN.  Detach
 * only the module's anchor and metadata: never inspect, mutate, free, or
 * resubmit that chain.  Pending input remains module-owned bookkeeping, so
 * consume its payload positions before clearing the retained links.
 */
static void
ngx_http_markdown_streaming_abandon_pending_after_fatal(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ctx->streaming.pending_output = NULL;
    ctx->streaming.pending_meta.has_data = 0;
    ctx->streaming.pending_meta.bytes = 0;
    ctx->streaming.pending_meta.zero_copy = 0;
    ctx->streaming.pending_meta.main_terminal = 0;
    ctx->streaming.pending_meta.subrequest_terminal = 0;

    ctx->streaming.completion.finalize_after_pending = 0;
    ctx->streaming.completion.finalize_pending_lastbuf = 0;
    ctx->streaming.completion.pending_terminal_metrics = 0;
    ctx->streaming.completion.pending_failopen_delivery = 0;
    ctx->streaming.completion.postcommit_error_after_pending = 0;
    ctx->streaming.completion.postcommit_error_code = ERROR_SUCCESS;
    ctx->streaming.completion.failopen_abort_after_pending = 0;
    ctx->streaming.completion.failopen_abort_error_code = ERROR_SUCCESS;
    ctx->streaming.completion.upstream_terminal_seen = 0;

    ngx_http_markdown_streaming_pending_input_abandon_and_clear(ctx);
    ngx_http_markdown_streaming_sync_buffered(r, ctx);
}

/*
 * Preflight scan of a chain remainder: count non-empty bytes/links and
 * detect any terminal buffer. Detects size_t/ngx_uint_t overflow before
 * accumulation so callers can reject early.
 *
 * Outputs via pointers: added_bytes, added_links, terminal_seen.
 * Returns NGX_OK on success, NGX_ERROR on overflow.
 */
static ngx_int_t
ngx_http_markdown_streaming_preflight_chain_stats(
    const ngx_http_request_t *r,
    ngx_chain_t *cl,
    size_t *added_bytes,
    ngx_uint_t *added_links,
    ngx_flag_t *terminal_seen)
{
    size_t      buf_size;
    ngx_flag_t  term = 0;

    *added_bytes = 0;
    *added_links = 0;

    for (ngx_chain_t *scan = cl; scan != NULL; scan = scan->next) {
        if (scan->buf == NULL) {
            continue;
        }

        if (scan->buf->last_buf
            || (r != r->main && scan->buf->last_in_chain))
        {
            term = 1;
        }

        buf_size = ngx_http_markdown_buf_len_safe(scan->buf);
        if (buf_size == 0) {
            continue;
        }

        if (buf_size > (size_t) -1 - *added_bytes
            || *added_links == (ngx_uint_t) -1)
        {
            return NGX_ERROR;
        }
        *added_bytes += buf_size;
        (*added_links)++;
    }

    *terminal_seen = term;
    return NGX_OK;
}

/*
 * Verify that adding `added_bytes`/`added_links` to the existing
 * pending_input totals stays within numeric limits and the configured
 * body buffer limit.
 *
 * Returns NGX_OK if within budget, NGX_ERROR otherwise.
 */
static ngx_int_t
ngx_http_markdown_streaming_check_pending_budget(
    const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    size_t added_bytes,
    ngx_uint_t added_links)
{
    size_t  limit;

    if (added_bytes > (size_t) -1 - ctx->streaming.pending_input.bytes
        || added_links > (ngx_uint_t) -1
                         - ctx->streaming.pending_input.links)
    {
        return NGX_ERROR;
    }

    limit = ngx_http_markdown_effective_body_buffer_limit(
        ctx->effective_conf, conf);
    if (limit == 0) {
        return NGX_OK;
    }

    if (ctx->streaming.pending_input.bytes > limit
        || added_bytes > limit - ctx->streaming.pending_input.bytes)
    {
        return NGX_ERROR;
    }

    return NGX_OK;
}

/*
 * Enqueue all chain links starting from `cl` (the remainder after
 * a CONSUMED + NGX_AGAIN chunk).  The complete chain is preflighted and
 * built off-queue, then appended atomically so failures cannot expose a
 * partially retained continuation.
 *
 * out_error_code (optional): when non-NULL and the function returns
 * NGX_ERROR, receives the specific failure classification so the caller
 * can route through the correct error path instead of uniformly
 * classifying every failure as ERROR_BUDGET_EXCEEDED.  Current values:
 *   ERROR_BUDGET_EXCEEDED — cumulative input exceeds the configured
 *                          body buffer limit, or preflight detected a
 *                          size_t/ngx_uint_t overflow (P2).
 *   ERROR_MEMORY_LIMIT    — pool allocation failure (ngx_alloc_chain_link).
 */
static ngx_int_t
ngx_http_markdown_streaming_pending_input_enqueue_remainder(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_chain_t *cl,
    uint32_t *out_error_code)
{
    size_t        added_bytes;
    ngx_uint_t    added_links;
    ngx_flag_t    terminal_seen;
    ngx_chain_t  *head;
    ngx_chain_t  *link;
    ngx_chain_t  *tail;

    if (ngx_http_markdown_streaming_preflight_chain_stats(
            r, cl, &added_bytes, &added_links, &terminal_seen)
        != NGX_OK)
    {
        if (out_error_code != NULL) {
            *out_error_code = ERROR_BUDGET_EXCEEDED;
        }
        return NGX_ERROR;
    }

    if (ngx_http_markdown_streaming_check_pending_budget(
            ctx, conf, added_bytes, added_links)
        != NGX_OK)
    {
        if (out_error_code != NULL) {
            *out_error_code = ERROR_BUDGET_EXCEEDED;
        }
        return NGX_ERROR;
    }

    head = NULL;
    tail = NULL;

    for (; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL
            || ngx_http_markdown_buf_len_safe(cl->buf) == 0)
        {
            continue;
        }

        link = ngx_alloc_chain_link(r->pool);
        if (link == NULL) {
            if (out_error_code != NULL) {
                *out_error_code = ERROR_MEMORY_LIMIT;
            }
            return NGX_ERROR;
        }
        link->buf = cl->buf;
        link->next = NULL;

        if (tail != NULL) {
            tail->next = link;
        } else {
            head = link;
        }
        tail = link;
    }

    if (ctx->streaming.pending_input.tail != NULL) {
        ctx->streaming.pending_input.tail->next = head;
    } else {
        ctx->streaming.pending_input.head = head;
    }
    if (tail != NULL) {
        ctx->streaming.pending_input.tail = tail;
    }
    ctx->streaming.pending_input.bytes += added_bytes;
    ctx->streaming.pending_input.links += added_links;
    ctx->streaming.completion.upstream_terminal_seen |= terminal_seen;

    return NGX_OK;
}

/*
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
 * Return the request-type-aware terminal-delivered latch for the current
 * request.
 *
 * Main requests (r == r->main) use main_terminal_sent (last_buf delivered).
 * Subrequests (r != r->main) use subrequest_terminal_sent
 * (last_in_chain delivered).
 *
 * This lets handle_null_input's fail-open EOF branch check the terminal
 * delivery state matching the current request type, preventing a
 * duplicate synthetic terminal after a backpressured subrequest terminal
 * has already been confirmed downstream.
 */
static ngx_inline ngx_flag_t
ngx_http_markdown_streaming_terminal_sent_for_request(
    const ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx)
{
    if (r == r->main) {
        return ctx->streaming.main_terminal_sent;
    }
    return ctx->streaming.subrequest_terminal_sent;
}


/*
 * Scan a chain for terminal metadata, distinguishing main request
 * (last_buf) from subrequest (last_in_chain) semantics.
 *
 * Terminal state is captured BEFORE the first downstream body-filter
 * call so it survives across the ownership boundary (Rule 1/47).
 * resume_pending() consumes the captured metadata instead of
 * re-scanning the downstream-retained chain.
 *
 * Output parameters:
 *   main_terminal       - main request chain carries last_buf
 *   subrequest_terminal - subrequest chain carries last_in_chain
 */
static void
ngx_http_markdown_streaming_capture_chain_terminal(
    ngx_chain_t *out,
    const ngx_http_request_t *r,
    ngx_flag_t *main_terminal,
    ngx_flag_t *subrequest_terminal)
{
    ngx_flag_t  mt = 0;
    ngx_flag_t  st = 0;

    for (ngx_chain_t *cl = out; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL) {
            continue;
        }
        if (r == r->main && cl->buf->last_buf) {
            mt = 1;
        }
        if (r != r->main && cl->buf->last_in_chain) {
            st = 1;
        }
    }

    *main_terminal = mt;
    *subrequest_terminal = st;
}


/*
 * Save output chain as pending on downstream backpressure (NGX_AGAIN).
 *
 * Guards against unexpected re-entry without modifying the existing
 * downstream-owned pending chain.  Sets the buffered flag so NGINX event
 * machinery knows to retry the downstream write.
 *
 * Returns:
 *   NGX_AGAIN  - pending saved successfully
 *   NGX_ERROR  - re-entry detected (old chain remains pending)
 */
static ngx_int_t
ngx_http_markdown_streaming_save_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *out,
    const u_char *data, size_t len,
    ngx_flag_t zero_copy,
    ngx_http_markdown_pending_terminal_t terminal)
{
    if (ctx->streaming.pending_output != NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: streaming pending output "
                      "re-entry detected, refusing to overwrite "
                      "existing pending chain");

        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_ERROR;
    }

    ctx->streaming.pending_output = out;
    ctx->streaming.pending_meta.has_data =
        (data != NULL && len > 0) ? 1 : 0;
    ctx->streaming.pending_meta.bytes = len;
    ctx->streaming.pending_meta.zero_copy = zero_copy;
    ctx->streaming.pending_meta.main_terminal = terminal.main_terminal;
    ctx->streaming.pending_meta.subrequest_terminal =
        terminal.subrequest_terminal;

    /* Backpressure metric: streaming output returned NGX_AGAIN */
    NGX_HTTP_MARKDOWN_METRIC_INC(perf.backpressure_total);

    /* Watermark gauge: CAS loop for pending output high-water */
    if (len > 0) {
        NGX_HTTP_MARKDOWN_METRIC_WATERMARK(
            perf.pending_output_high_watermark_bytes,
            (ngx_atomic_t) len);
    }

    ngx_http_markdown_streaming_sync_buffered(r, ctx);

    return NGX_AGAIN;
}


static void
ngx_http_markdown_streaming_record_send_delivery(
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_pending_terminal_t *terminal,
    const u_char *data, size_t len, ngx_flag_t delivered)
{
    if (!delivered) {
        return;
    }

    if (terminal->main_terminal) {
        ctx->streaming.main_terminal_sent = 1;
    }
    if (terminal->subrequest_terminal) {
        ctx->streaming.subrequest_terminal_sent = 1;
    }

    ctx->streaming.flushes_sent++;

    if (data != NULL && len > 0) {
        ngx_http_markdown_streaming_record_ttfb(ctx);
        NGX_HTTP_MARKDOWN_METRIC_ADD(
            streaming.selection.output_bytes_total,
            (ngx_atomic_int_t) len);
    }
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
    ngx_flag_t    delivered;
    ngx_http_markdown_pending_terminal_t  terminal;

    ctx->streaming.classify.last_send_failure_origin = NGX_HTTP_MD_SEND_ORIGIN_NONE;

    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION;
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
            ctx->streaming.classify.last_send_failure_origin =
                NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION;
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
    if (b->last_buf && ctx->streaming.main_terminal_sent) {
        b->last_buf = 0;
    }

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = NULL;

    /*
     * Capture terminal metadata BEFORE the downstream call (Rule 1/47
     * ownership boundary).  send_output builds a single-link chain, so
     * the terminal state is known from the buffer we just constructed.
     * For the main request, last_buf is the terminal marker; for a
     * subrequest, last_in_chain is the terminal marker.
     */
    terminal.main_terminal = (r == r->main && b->last_buf);
    terminal.subrequest_terminal = (r != r->main && b->last_in_chain);

    rc = ngx_http_next_body_filter(r, out);
    delivered = ngx_http_markdown_streaming_delivery_ok(rc);

    /* Classify downstream failure (non-AGAIN, non-delivery) */
    if (!delivered && rc != NGX_AGAIN) {
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM;
    }

    /*
     * Latch terminal delivery and record TTFB/bytes only after confirmed
     * delivery.  NGX_AGAIN is accounted when resume_pending() drains.
     */
    ngx_http_markdown_streaming_record_send_delivery(
        ctx, &terminal, data, len, delivered);

    if (rc == NGX_AGAIN) {
        rc = ngx_http_markdown_streaming_save_pending(
            r, ctx, out, data, len, 0, terminal);
        if (rc == NGX_ERROR) {
            ctx->streaming.classify.last_send_failure_origin =
                NGX_HTTP_MD_SEND_ORIGIN_INVARIANT;
        }
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
    ngx_http_markdown_streaming_sync_buffered(r, ctx);

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
             (conf->on_error
              == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
                ? "fail_closed" : "pass");

        ctx->streaming.completion.failure_recorded = 1;

        ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
            ngx_http_markdown_reason_streaming_fail_postcommit());
    }
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
                (int64_t) ctx->streaming.output.bytes);
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


static ngx_flag_t
ngx_http_markdown_streaming_delivery_ok(ngx_int_t rc)
{
    return (rc == NGX_OK || rc == NGX_DONE) ? 1 : 0;
}


static void
ngx_http_markdown_streaming_record_pending_ttfb(
    ngx_http_markdown_ctx_t *ctx, ngx_int_t rc)
{
    const ngx_time_t  *tp_ttfb;
    ngx_msec_t        now_ms;
    ngx_msec_t        elapsed_ms;

    if (ctx->streaming.ttfb.recorded
        || ctx->streaming.ttfb.feed_start_ms == 0
        || ngx_http_markdown_metrics == NULL
        || !ngx_http_markdown_streaming_delivery_ok(rc)
        || !ctx->streaming.pending_meta.has_data)
    {
        return;
    }

    tp_ttfb = ngx_timeofday();
    now_ms = (ngx_msec_t) (tp_ttfb->sec * 1000 + tp_ttfb->msec);
    elapsed_ms = (now_ms >= ctx->streaming.ttfb.feed_start_ms)
        ? (now_ms - ctx->streaming.ttfb.feed_start_ms) : 0;

    /* Gauge store: see send_output TTFB comment for rationale. */
    ngx_http_markdown_metrics->streaming.last_ttfb_ms =
        (ngx_atomic_t) elapsed_ms;
    ctx->streaming.ttfb.recorded = 1;
}


static void
ngx_http_markdown_streaming_account_pending_output(
    ngx_http_markdown_ctx_t *ctx, ngx_int_t rc)
{
    if (ngx_http_markdown_streaming_delivery_ok(rc)
        && ctx->streaming.pending_meta.bytes > 0)
    {
        NGX_HTTP_MARKDOWN_METRIC_ADD(
            streaming.selection.output_bytes_total,
            (ngx_atomic_int_t) ctx->streaming.pending_meta.bytes);
        if (ctx->streaming.pending_meta.zero_copy) {
            NGX_HTTP_MARKDOWN_METRIC_INC(perf.zero_copy_output_total);
        } else {
            NGX_HTTP_MARKDOWN_METRIC_INC(perf.copied_output_total);
        }
    }

    ctx->streaming.pending_meta.bytes = 0;
    ctx->streaming.pending_meta.zero_copy = 0;
}


static void
ngx_http_markdown_streaming_record_pending_terminal_success(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    if (!ctx->streaming.completion.pending_terminal_metrics) {
        return;
    }

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
            (int64_t) ctx->streaming.output.bytes);
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
 * Resume sending pending output after backpressure clears.
 */
static ngx_int_t
ngx_http_markdown_streaming_resume_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t     rc;
    ngx_flag_t    pending_main_terminal;
    ngx_flag_t    pending_subrequest_terminal;

    if (ctx->streaming.pending_output == NULL) {
        /*
         * If finalize deferred the terminal last_buf due to
         * backpressure, send it now that the pending output
         * has been drained.
         */
        if (ctx->streaming.completion.finalize_pending_lastbuf) {
            return ngx_http_markdown_streaming_send_deferred_lastbuf(
                r, ctx, conf);
        }

        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_OK;
    }

    /*
     * Read terminal metadata captured BEFORE the first downstream call
     * (Rule 1/47 ownership boundary).  This is the authoritative source
     * for terminal state — NOT a head-only or full-chain re-scan of the
     * downstream-retained chain, which may have been mutated by
     * downstream filters.
     *
     * For main requests: main_terminal indicates last_buf was present
     *   in the pending chain (possibly in a non-head tail link of a
     *   multi-link fail-open chain).
     * For subrequests: subrequest_terminal indicates last_in_chain was
     *   present.  This must NOT latch main_terminal_sent.
     */
    pending_main_terminal = ctx->streaming.pending_meta.main_terminal;
    pending_subrequest_terminal =
        ctx->streaming.pending_meta.subrequest_terminal;

    /*
     * The downstream filter retained the original chain when it returned
     * NGX_AGAIN.  Resume that owned state with NULL; resubmitting out would
     * duplicate its unsent tail (Rule 1).
     */
    rc = ngx_http_next_body_filter(r, NULL);

    if (rc == NGX_AGAIN) {
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_AGAIN;
    }

    ctx->streaming.pending_output = NULL;

    /* Backpressure resume: pending drain completed */
    if (ngx_http_markdown_streaming_delivery_ok(rc)) {
        NGX_HTTP_MARKDOWN_METRIC_INC(perf.backpressure_resume_total);
    }

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
    ngx_http_markdown_streaming_record_pending_ttfb(ctx, rc);

    /*
     * Clear pending_has_data after TTFB sampling has consumed
     * the latch, so future re-entry does not observe a stale
     * flag from a prior send_output NGX_AGAIN.
     */
    ctx->streaming.pending_meta.has_data = 0;

    /*
     * Account for deferred output bytes that were saved on
     * NGX_AGAIN in send_output and now confirmed delivered.
     */
    ngx_http_markdown_streaming_account_pending_output(ctx, rc);

    /*
     * If the drained pending chain carried a main-request last_buf
     * (captured before the first downstream call), mark
     * main_terminal_sent now that delivery is confirmed.  This
     * complements the fix in send_output()/send_failopen_chain()
     * which captures terminal state before crossing the downstream
     * ownership boundary.
     *
     * Rule 47: only latch after confirmed delivery (NGX_OK/NGX_DONE).
     * NGX_AGAIN was handled above and never reaches this point.
     *
     * Main request terminal (last_buf) latches main_terminal_sent.
     * Subrequest terminal (last_in_chain) latches subrequest_terminal_sent.
     * The two latches are request-type-aware and symmetric: each represents
     * "the terminal marker appropriate for THIS request has been confirmed
     * downstream."  handle_null_input's fail-open EOF branch checks the
     * matching latch via terminal_sent_for_request() to avoid synthesizing
     * a duplicate terminal after a backpressured subrequest terminal has
     * already been confirmed downstream.
     */
    if (ngx_http_markdown_streaming_delivery_ok(rc)
        && r == r->main && pending_main_terminal)
    {
        ctx->streaming.main_terminal_sent = 1;
    }
    if (ngx_http_markdown_streaming_delivery_ok(rc)
        && r != r->main && pending_subrequest_terminal)
    {
        ctx->streaming.subrequest_terminal_sent = 1;
    }

    /*
     * Clear consumed terminal metadata regardless of delivery result
     * so stale state does not persist into the next pending cycle.
     */
    ctx->streaming.pending_meta.main_terminal = 0;
    ctx->streaming.pending_meta.subrequest_terminal = 0;

    /*
     * Pending output drained. Check if resume failed before
     * proceeding to deferred lastbuf, to avoid the failure
     * branch being short-circuited.
     */
    if (!ngx_http_markdown_streaming_delivery_ok(rc)) {
        /*
         * Resume failed after draining pending output.
         * Clear any pending terminal metrics latch and failopen
         * delivery latch to avoid stale state on re-entry, then
         * record failure metrics.
         */
        ctx->streaming.completion.pending_terminal_metrics = 0;
        ctx->streaming.completion.pending_failopen_delivery = 0;
        ngx_http_markdown_streaming_record_postcommit_failure(
            r, ctx, conf);

        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return rc;
    }

    /*
     * Pending output drained successfully. If this was a
     * fail-open delivery that was deferred by backpressure,
     * increment the delivery counter now (Rule 38).
     */
    if (ctx->streaming.completion.pending_failopen_delivery) {
        NGX_HTTP_MARKDOWN_METRIC_INC(results.failopen_count);
        ctx->streaming.completion.pending_failopen_delivery = 0;
        ctx->failopen_completed = 1;
    }

    /*
     * Pending output drained successfully. If the terminal
     * last_buf send was deferred due to backpressure during
     * finalize(), record the success metrics now that the
     * drain has confirmed delivery.
     */
    ngx_http_markdown_streaming_record_pending_terminal_success(r, ctx, conf);

    /*
     * Pending output drained successfully. If finalize deferred
     * the terminal last_buf, send it now.
     */
    if (ctx->streaming.completion.finalize_pending_lastbuf) {
        return ngx_http_markdown_streaming_send_deferred_lastbuf(
            r, ctx, conf);
    }

    ngx_http_markdown_streaming_sync_buffered(r, ctx);
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

    ctx->streaming.classify.input_disposition = NGX_HTTP_MD_INPUT_TERMINAL;

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
     * markdown_error_policy (post-commit is unconditional).
     */
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: post-commit error, "
        "bytes_sent=%uz, error_code=%ui, chunks=%ui",
        ctx->streaming.output.bytes,
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

static ngx_int_t
ngx_http_markdown_streaming_defer_postcommit_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    uint32_t error_code,
    ngx_chain_t *in)
{
    ctx->streaming.classify.input_disposition = NGX_HTTP_MD_INPUT_TERMINAL;
    ctx->streaming.completion.postcommit_error_after_pending = 1;
    ctx->streaming.completion.postcommit_error_code = error_code;
    ctx->streaming.completion.upstream_terminal_seen = 0;
    ngx_http_markdown_streaming_pending_input_abandon_and_clear(ctx);
    ngx_http_markdown_streaming_abandon_input(in);
    ngx_http_markdown_streaming_sync_buffered(r, ctx);

    return NGX_AGAIN;
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
         * regardless of markdown_error_policy setting.
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
     * The terminal state is determined by markdown_error_policy
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

    if (conf->on_error
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
        /*
         * Use ngx_http_filter_finalize_request so the configured error
         * status (429/503/502) generates the correct error response.
         * In the body filter, returning a positive status code directly
         * is unreliable; the finalizer sets r->headers_out.status and
         * routes through the proper error response generation path.
         */
        return ngx_http_filter_finalize_request(r,
            &ngx_http_markdown_filter_module,
            (ngx_int_t) conf->error_status);
    }

    /* Fail-open: pass original content */
    ctx->streaming.reason = mapped_reason;
    ctx->eligible = 0;
    /*
     * Mark request-lifetime fail-open mode at the single policy
     * selection point so every fail-open entry (prepare options
     * failure, handle creation failure, decompressor/prebuffer/replay
     * init failure, feed/finalize precommit error, pending-input
     * enqueue failure) uniformly enters the mode.  Future input then
     * bypasses Rust via continue_failopen_input instead of
     * re-entering the converter with a NULL handle.  (P1-1)
     */
    ctx->streaming.completion.failopen_active = 1;
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


static void
ngx_http_markdown_streaming_add_output_bytes(
    ngx_http_markdown_ctx_t *ctx, size_t out_len)
{
    if (ctx->streaming.output.overflowed) {
        return;
    }

    if (out_len > (size_t) -1 - ctx->streaming.output.bytes) {
        ctx->streaming.output.bytes = (size_t) -1;
        ctx->streaming.output.overflowed = 1;
        return;
    }

    ctx->streaming.output.bytes += out_len;
}


static ngx_int_t
ngx_http_markdown_streaming_send_zero_copy_feed_output(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    u_char *out_data,
    size_t out_len)
{
    ngx_buf_t    *zb;
    ngx_chain_t  *zout;
    ngx_int_t     rc;
    ngx_flag_t    owner_transferred;
    ngx_http_markdown_pending_terminal_t  terminal;

    ctx->streaming.classify.last_send_failure_origin = NGX_HTTP_MD_SEND_ORIGIN_NONE;

    /*
     * Zero-copy path: buffer factory creates an ngx_buf_t referencing
     * Rust memory with pool cleanup registered.  On success, the pool
     * cleanup owns the Rust buffer; caller does NOT free it.
     *
     * On failure, check owner_transferred to determine if we can
     * fallback to pool-copy (caller still owns the buffer).
     */
    zb = ngx_http_markdown_rust_buf_create_ex(r->pool, out_data, out_len,
                                              &owner_transferred);
    if (zb == NULL) {
        if (!owner_transferred) {
            /*
             * Factory failed before taking ownership of the Rust
             * buffer.  Fallback to pool-copy: copy data into pool
             * memory, then free the Rust buffer ourselves.
             */
            rc = ngx_http_markdown_streaming_send_output(
                r, ctx, out_data, out_len, /* last_buf */ 0);

            if (ngx_http_markdown_streaming_delivery_ok(rc)) {
                NGX_HTTP_MARKDOWN_METRIC_INC(perf.copied_output_total);
            }

            markdown_streaming_output_free(out_data, out_len);
            return rc;
        }

        /*
         * Factory took ownership but still failed (cleanup alloc
         * succeeded but something else went wrong, or it freed the
         * buffer).  Cannot fallback — the data is gone.
         */
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }

    zb->flush = 1;
    zb->last_buf = 0;
    zb->last_in_chain = 0;

    zout = ngx_alloc_chain_link(r->pool);
    if (zout == NULL) {
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }
    zout->buf = zb;
    zout->next = NULL;

    rc = ngx_http_next_body_filter(r, zout);

    if (ngx_http_markdown_streaming_delivery_ok(rc)) {
        ctx->streaming.flushes_sent++;
        ngx_http_markdown_streaming_record_ttfb(ctx);
        NGX_HTTP_MARKDOWN_METRIC_ADD(
            streaming.selection.output_bytes_total,
            (ngx_atomic_int_t) out_len);
        NGX_HTTP_MARKDOWN_METRIC_INC(perf.zero_copy_output_total);
    }

    /* Classify downstream failure */
    if (!ngx_http_markdown_streaming_delivery_ok(rc)
        && rc != NGX_AGAIN)
    {
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM;
    }

    if (rc == NGX_AGAIN) {
        terminal.main_terminal = 0;
        terminal.subrequest_terminal = 0;
        rc = ngx_http_markdown_streaming_save_pending(
            r, ctx, zout, out_data, out_len, 1, terminal);
        if (rc == NGX_ERROR) {
            ctx->streaming.classify.last_send_failure_origin =
                NGX_HTTP_MD_SEND_ORIGIN_INVARIANT;
        }
    }

    return rc;
}


static ngx_int_t
ngx_http_markdown_streaming_send_feed_output(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    u_char *out_data,
    size_t out_len)
{
    ngx_http_markdown_output_decision_t  decision;
    ngx_flag_t                          bp_active;
    ngx_int_t                           rc;

    bp_active = (ctx->streaming.pending_output != NULL) ? 1 : 0;

    decision = ngx_http_markdown_hybrid_output_decision(
        conf, /* chunk_is_terminal */ 0, bp_active);

    if (decision == NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY) {
        return ngx_http_markdown_streaming_send_zero_copy_feed_output(
            r, ctx, out_data, out_len);
    }

    /*
     * Pool-copy path: existing send_output copies data into pool memory.
     * Caller frees the Rust buffer after return.
     */
    rc = ngx_http_markdown_streaming_send_output(
        r, ctx, out_data, out_len, /* last_buf */ 0);

    if (ngx_http_markdown_streaming_delivery_ok(rc)) {
        NGX_HTTP_MARKDOWN_METRIC_INC(perf.copied_output_total);
    }

    markdown_streaming_output_free(out_data, out_len);
    return rc;
}


/*
 * Handle known output-loss post-commit failure (hard abort).
 *
 * Called when the Rust converter produced output (out_data/out_len)
 * but the C-side construction or downstream delivery failed.  The
 * chunk is irrecoverably lost — the client has received a partial
 * Markdown body.
 *
 * Unlike handle_postcommit_error (which attempts safe-finish for
 * converter/parser errors where no output was lost), this path
 * performs a hard abort:
 *   - Does NOT call safe_finish (would send closing markers for
 *     an incomplete body, masquerading as a valid response)
 *   - Does NOT send a terminal chain or last_buf (would signal
 *     chunked-encoding completion to the client)
 *   - Aborts the Rust streaming handle
 *   - Returns NGX_ERROR so the connection is reset
 *
 * Metrics classification uses last_send_failure_origin:
 *   ALLOCATION → failures_resource_limit + budget_exceeded_total
 *   DOWNSTREAM/INVARIANT → failures_conversion only
 *
 * Returns:
 *   NGX_ERROR unconditionally (hard failure)
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_output_loss(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_uint_t  origin;

    ctx->streaming.classify.input_disposition = NGX_HTTP_MD_INPUT_TERMINAL;
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_POST_COMMIT_ABORT;

    ngx_http_markdown_streaming_abandon_pending_after_fatal(r, ctx);

    origin = ctx->streaming.classify.last_send_failure_origin;

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
        "markdown: post-commit output loss, "
        "origin=%ui, hard abort (no safe-finish)",
        origin);

    /* Classify failure for reason taxonomy and metrics. */
    if (!ctx->streaming.completion.failure_recorded) {
        switch (origin) {
        case NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION:
            ctx->streaming.reason =
                NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_BUDGET_EXCEEDED;
            NGX_HTTP_MARKDOWN_METRIC_INC(
                streaming.budget_exceeded_total);
            NGX_HTTP_MARKDOWN_METRIC_INC(failures_resource_limit);
            ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
                ngx_http_markdown_reason_streaming_budget_exceeded());
            break;

        case NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM:
            ctx->streaming.reason =
                NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_IO_ERROR;
            NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);
            break;

        case NGX_HTTP_MD_SEND_ORIGIN_INVARIANT:
            /*
             * No public internal-failure reason exists.  Preserve the
             * current taxonomy by using the generic post-commit conversion
             * fallback, never the resource-limit reason.
             */
            ctx->streaming.reason =
                NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_PARSE_ERROR;
            NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);
            break;

        default:
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: unexpected output-loss origin=%ui, "
                "classifying as internal invariant failure",
                origin);
            ctx->streaming.reason =
                NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_PARSE_ERROR;
            NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);
            break;
        }
    }

    /* Record postcommit failure exactly once */
    ngx_http_markdown_streaming_record_postcommit_failure(
        r, ctx, conf);

    ngx_log_debug3(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: output-loss hard abort, "
        "bytes_sent=%uz, origin=%ui, chunks=%ui",
        ctx->streaming.output.bytes,
        origin,
        ctx->streaming.chunks_processed);

    /* Hard abort: destroy Rust handle without safe-finish */
    if (ctx->streaming.handle != NULL) {
        markdown_streaming_abort(ctx->streaming.handle);
        ctx->streaming.handle = NULL;
    }

    /*
     * Return NGX_ERROR without sending any terminal chain.
     * The body-filter caller sees NGX_ERROR and the connection
     * will be reset, making the incomplete body visible to the
     * client as a protocol-level error (truncated chunked
     * transfer).
     */
    return NGX_ERROR;
}


static ngx_int_t
ngx_http_markdown_streaming_handle_success_output(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    u_char *out_data,
    size_t out_len)
{
    ngx_int_t  rc;

    if (out_data == NULL || out_len == 0) {
        if (out_data != NULL) {
            markdown_streaming_output_free(out_data, out_len);
        }
        return NGX_OK;
    }

    if (ctx->streaming.commit_state == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE) {
        rc = ngx_http_markdown_streaming_commit(r, ctx, conf);
        if (rc != NGX_OK) {
            markdown_streaming_output_free(out_data, out_len);
            return rc;
        }
    }

    ngx_http_markdown_streaming_add_output_bytes(ctx, out_len);

    rc = ngx_http_markdown_streaming_send_feed_output(
        r, ctx, conf, out_data, out_len);
    if (rc == NGX_AGAIN) {
        return ngx_http_markdown_streaming_handle_backpressure(r, ctx);
    }

    if (!ngx_http_markdown_streaming_delivery_ok(rc)
        && rc != NGX_AGAIN
        && ctx->streaming.commit_state
           == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
    {
        /*
         * C-side output construction or downstream delivery failed
         * after commit.  The Rust converter produced output that was
         * lost before reaching the client — safe-finish MUST NOT be
         * attempted because it would send closing markers pretending
         * the response body is complete while a chunk is missing.
         *
         * Classification by failure origin:
         *   ALLOCATION: pool/buf/chain alloc failure → resource limit
         *   DOWNSTREAM: body filter definitive error → I/O failure
         *   INVARIANT:  save_pending re-entry or state error → internal
         *
         * All paths:
         *   - input_disposition = TERMINAL
         *   - postcommit_error_total, failed_total, conversions_failed
         *     incremented exactly once (idempotent via failure_recorded)
         *   - Rust handle aborted (not safe-finished)
         *   - No terminal chain or closing bytes sent
         *   - Returns NGX_ERROR for protocol-visible disconnect
         */
        return ngx_http_markdown_streaming_handle_output_loss(
            r, ctx, conf);
    }

    return rc;
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

    return ngx_http_markdown_streaming_handle_success_output(
        r, ctx, conf, out_data, out_len);
}

static uint32_t
ngx_http_markdown_streaming_map_feed_decomp_error(ngx_int_t rc)
{
    if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total);
        NGX_HTTP_MARKDOWN_METRIC_INC(
            perf.decompression_budget_exceeded_total);
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
        NGX_HTTP_MARKDOWN_METRIC_INC(
            perf.decompression_budget_exceeded_total);
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

    /*
     * Default input disposition: CONSUMED.  Rust will eat this chunk;
     * on downstream NGX_AGAIN the caller may safely advance buf->pos
     * and enqueue cl->next to pending_input.
     *
     * Overridden to RETAIN by send_failopen_chain when the fail-open
     * clone shares this ngx_buf_t (advancing pos would corrupt the
     * pending fail-open output).
     */
    ctx->streaming.classify.input_disposition = NGX_HTTP_MD_INPUT_CONSUMED;

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
            (int64_t) ctx->streaming.output.bytes);
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

    if (ctx->streaming.output.overflowed) {
        /* latch is sticky: skip all further additions */
    } else if (result->markdown_len > (size_t) -1
                - ctx->streaming.output.bytes)
    {
        ctx->streaming.output.bytes = (size_t) -1;
        ctx->streaming.output.overflowed = 1;
    } else {
        ctx->streaming.output.bytes += result->markdown_len;
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
            "markdown: etag_len=%uz "
            "uri_len=%uz "
            "out_bytes=%uz tokens=%ui",
            result.etag_len,
            r->uri.len,
            ctx->streaming.output.bytes,
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
        ctx->streaming.output.bytes);

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
                (int64_t) ctx->streaming.output.bytes);
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


static void
ngx_http_markdown_streaming_start_otel_span(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    static ngx_str_t  s_gfm = ngx_string("gfm");
    static ngx_str_t  s_cm = ngx_string("commonmark");

    const ngx_str_t  *flavor;

    ctx->otel_span = NULL;
    if (conf->ops.otel_enabled == 0) {
        return;
    }

    ctx->otel_span = ngx_http_markdown_otel_span_start(r, conf);
    if (ctx->otel_span == NULL) {
        return;
    }

    if (conf->flavor == NGX_HTTP_MARKDOWN_FLAVOR_GFM) {
        flavor = &s_gfm;
    } else {
        flavor = &s_cm;
    }

    ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
        (const u_char *) "flavor", 6, flavor->data, flavor->len);
    ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
        (const u_char *) "engine", 6, (const u_char *) "streaming", 9);

    if (r->uri.len > 0) {
        ngx_http_markdown_otel_set_str_attr(ctx->otel_span,
            (const u_char *) "uri_route", 9,
            (const u_char *) "redacted", 8);
    }
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

    /*
     * Record the conversion attempt before any fallible initialization.
     * precommit_error records the matching failure, so delaying this until
     * setup completes would violate attempted >= failed.
     */
    if (!ctx->conversion.attempted) {
        ctx->conversion.attempted = 1;
        NGX_HTTP_MARKDOWN_METRIC_INC(conversions_attempted);
        NGX_HTTP_MARKDOWN_METRIC_INC(streaming.requests_total);
    }

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
        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, ERROR_MEMORY_LIMIT);
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

        /*
         * Note: decompression_streaming_total is incremented at
         * path selection time in the header filter (Req 4.5),
         * not here at decompressor initialization.
         */
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
             * (pass) or reject per the configured markdown_error_policy.
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
             * apply the configured markdown_error_policy.
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

    ngx_http_markdown_streaming_start_otel_span(r, ctx, conf);

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
    ngx_int_t   rc;
    ngx_flag_t  cap_main_terminal;
    ngx_flag_t  cap_subrequest_terminal;

    /*
     * Defense-in-depth: refuse to submit a new output chain while
     * downstream still owns a previously-retained pending_output.  The
     * main entry points (handle_new_input_with_pending, ensure_handle)
     * route around this, but guard here so any future caller cannot
     * violate the backpressure ownership contract (Rule 1) by
     * overwriting or interleaving pending output.  Moving the check
     * before the downstream call also prevents a partial submit when
     * the new chain is the one that would be discarded.  (P1-2)
     */
    if (ctx->streaming.pending_output != NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: fail-open pending output "
            "re-entry detected, refusing to submit new chain "
            "before old pending output drains (Rule 1)");

        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_ERROR;
    }

    /*
     * Capture terminal metadata from the FULL chain BEFORE the
     * downstream call (Rule 1/47 ownership boundary).  The fail-open
     * chain may be multi-link (replay prefix + cloned input + terminal
     * tail).  The head is typically non-terminal (replay prefix), so
     * a head-only scan in resume_pending() would miss the terminal
     * tail.  Capturing here ensures terminal state survives across the
     * downstream ownership boundary.
     */
    ngx_http_markdown_streaming_capture_chain_terminal(
        out, r, &cap_main_terminal, &cap_subrequest_terminal);

    rc = ngx_http_next_body_filter(r, out);

    if (rc == NGX_AGAIN) {
        ctx->streaming.pending_output = out;
        ctx->streaming.pending_meta.has_data = 1;
        ctx->streaming.pending_meta.main_terminal = cap_main_terminal;
        ctx->streaming.pending_meta.subrequest_terminal =
            cap_subrequest_terminal;
        ctx->streaming.completion.pending_failopen_delivery =
            (!ctx->eligible && !ctx->failopen_completed) ? 1 : 0;

        /*
         * Set RETAIN disposition: the fail-open clone shares ngx_buf_t
         * with the original upstream chain.  Advancing the source pos
         * would corrupt the pending fail-open output's shared buffers.
         * process_chain checks this to avoid advancing pos on RETAIN.
         */
        ctx->streaming.classify.input_disposition = NGX_HTTP_MD_INPUT_RETAIN;

        /* Backpressure metric: fail-open output returned NGX_AGAIN */
        NGX_HTTP_MARKDOWN_METRIC_INC(perf.backpressure_total);

        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_AGAIN;
    }

    if ((rc == NGX_OK || rc == NGX_DONE) && !ctx->eligible
        && !ctx->failopen_completed)
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(results.failopen_count);
        ctx->failopen_completed = 1;
    }

    /*
     * Latch terminal-delivered state after confirmed immediate delivery
     * (Rule 47).  Symmetric with resume_pending() and send_output():
     * the fail-open chain's terminal tail may carry last_buf (main
     * request) or last_in_chain (subrequest).  Latching here prevents
     * handle_null_input's fail-open EOF branch from synthesizing a
     * duplicate terminal after a successful immediate fail-open
     * delivery.
     */
    if (rc == NGX_OK || rc == NGX_DONE) {
        if (r == r->main && cap_main_terminal) {
            ctx->streaming.main_terminal_sent = 1;
        }
        if (r != r->main && cap_subrequest_terminal) {
            ctx->streaming.subrequest_terminal_sent = 1;
        }
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

    ctx->streaming.completion.failopen_active = 1;

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
        /*
         * Only set failopen_completed on successful downstream delivery
         * (NGX_OK).  NGX_AGAIN means backpressure — pending output
         * has been saved but not yet delivered; setting the latch
         * here would cause the body filter to skip the resume path
         * and the delivery counter would never increment.  (Rule 47)
         */
        if (rc == NGX_OK) {
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

    if (ctx->streaming.classify.input_disposition == NGX_HTTP_MD_INPUT_TERMINAL) {
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_streaming_init_handle(
        r, ctx, conf);

    if (rc == NGX_ERROR) {
        return NGX_ERROR;
    }

    if (rc == NGX_DECLINED) {
        /*
         * Fail-open: route through the shared failopen_passthrough
         * contract (header forwarding, chain-link cloning, replay
         * prefix composition, and delivery-metric bookkeeping) so
         * init-failure fail-open behaves identically to every other
         * fail-open entry point.
         *
         * Do NOT call send_failopen_chain() directly here: `in` is
         * the body-filter's transient input chain, owned by the
         * caller and not guaranteed to remain stable across filter
         * invocations.  failopen_passthrough() clones the chain
         * links into request-pool memory (sharing only the
         * underlying ngx_buf_t) before handing off to the delivery
         * helper, so pending_output stays valid for resume_pending()
         * and cleanup() even if the caller reuses `in` afterwards.
         *
         * The caller (body_filter) must check ctx->failopen_completed
         * after this call: on immediate downstream success this
         * function returns NGX_OK, but the fail-open body has
         * already been delivered, so the caller must not fall
         * through into a second, generic passthrough of the same
         * input chain.
         */
        rc = ngx_http_markdown_streaming_failopen_passthrough(
            r, ctx, in);

        /*
         * Only latch failopen_completed on confirmed downstream
         * delivery (NGX_OK/NGX_DONE).  NGX_AGAIN means the chain is
         * pending_output-owned by downstream; the caller (body_filter)
         * must still treat this as "handled" and not fall through to
         * a generic passthrough, but the request-lifetime completion
         * latch is set only once delivery is confirmed so resume
         * accounting (Rule 47) stays correct.
         */
        if (rc == NGX_OK || rc == NGX_DONE) {
            ctx->failopen_completed = 1;
        }

        return rc;
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


/* Continue request-lifetime fail-open without re-entering Rust or replaying. */
static ngx_int_t
ngx_http_markdown_streaming_continue_failopen_input(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *input_chain)
{
    ngx_flag_t    last_buf;
    ngx_int_t     rc;

    last_buf = 0;
    for (ngx_chain_t *cl = input_chain; cl != NULL; cl = cl->next) {
        if (cl->buf != NULL
            && (cl->buf->last_buf
                || (r != r->main && cl->buf->last_in_chain)))
        {
            last_buf = 1;
            break;
        }
    }

    rc = ngx_http_markdown_streaming_send_failopen_chain(
        r, ctx, input_chain);
    if (!ngx_http_markdown_streaming_delivery_ok(rc)) {
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return rc;
    }

    ngx_http_markdown_streaming_abandon_input(input_chain);
    if (last_buf) {
        ctx->streaming.completion.upstream_terminal_seen = 0;
        /*
         * send_failopen_chain already latched the request-type-aware
         * terminal-delivered state (main_terminal_sent for main
         * requests, subrequest_terminal_sent for subrequests) after
         * confirmed delivery.  Set the matching latch here for
         * symmetry with the immediate-success path, ensuring all
         * terminal-delivery confirmation paths stay consistent.
         */
        if (r == r->main) {
            ctx->streaming.main_terminal_sent = 1;
        } else {
            ctx->streaming.subrequest_terminal_sent = 1;
        }
    } else if (ctx->streaming.completion.upstream_terminal_seen) {
        ctx->streaming.completion.upstream_terminal_seen = 0;
        return ngx_http_markdown_streaming_send_output(
            r, ctx, NULL, 0, /* last_buf */ 1);
    }

    ngx_http_markdown_streaming_sync_buffered(r, ctx);
    return rc;
}


/*
 * Terminate fail-open after older downstream-owned output has drained.
 *
 * This handles a fail-open *delivery integrity* failure: later input
 * could not be retained (budget exhaustion) while an earlier fail-open
 * output chain was still downstream-owned, so bytes were unavoidably
 * dropped and delivery must abort without a clean terminal.
 *
 * This is deliberately NOT routed through
 * ngx_http_markdown_streaming_record_postcommit_failure() or any
 * conversions_failed/streaming.failed_total/failures_resource_limit/
 * failures_conversion increment.  The conversion failure that selected
 * fail-open in the first place (ngx_http_markdown_streaming_precommit_error)
 * already recorded exactly one conversions_failed/streaming.failed_total
 * increment for this request, classified via failures_resource_limit or
 * failures_conversion.  That accounting is complete and authoritative;
 * this function handles a distinct, later-occurring event (the fail-open
 * delivery itself failing to complete), not a second conversion outcome.
 * Recording it again here would double-count the same request against
 * the attempted == succeeded + failed invariant.
 */
static ngx_int_t
ngx_http_markdown_streaming_abort_failopen_after_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    uint32_t  error_code;

    error_code = ctx->streaming.completion.failopen_abort_error_code;
    ctx->streaming.completion.failopen_abort_after_pending = 0;
    ctx->streaming.completion.failopen_abort_error_code = ERROR_SUCCESS;
    ctx->streaming.completion.upstream_terminal_seen = 0;
    ctx->streaming.classify.input_disposition = NGX_HTTP_MD_INPUT_TERMINAL;
    ngx_http_markdown_streaming_pending_input_abandon_and_clear(ctx);

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
        "markdown: fail-open delivery integrity abort: "
        "retained input could not be preserved while an earlier "
        "fail-open output chain was still downstream-owned "
        "(error_code=%ui); aborting without a clean terminal",
        (ngx_uint_t) error_code);
    (void) error_code;
    ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
        ngx_http_markdown_reason_streaming_fail_postcommit());

    ngx_http_markdown_streaming_sync_buffered(r, ctx);
    return NGX_ERROR;
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
    ngx_int_t      rc;
    ngx_flag_t     last_buf;
    ngx_chain_t   *fallback_cl;
    ngx_chain_t   *input_chain;

    /* Step 1: Drain pending output (backpressure recovery) */
    rc = ngx_http_markdown_streaming_resume_pending(
        r, ctx, conf);
    if (rc == NGX_AGAIN || rc == NGX_ERROR) {
        return rc;
    }
    /*
     * P2 fix: NGX_DONE means delivery succeeded.  Both NGX_OK and
     * NGX_DONE must continue to process pending_input and deferred
     * finalize.  The old code (if rc != NGX_OK return rc) trapped
     * NGX_DONE, skipping deferred finalize.
     */
    if (!ngx_http_markdown_streaming_delivery_ok(rc)) {
        return rc;
    }

    if (ctx->streaming.completion.postcommit_error_after_pending) {
        uint32_t  error_code;

        error_code = ctx->streaming.completion.postcommit_error_code;
        ctx->streaming.completion.postcommit_error_after_pending = 0;
        ctx->streaming.completion.postcommit_error_code = ERROR_SUCCESS;
        return ngx_http_markdown_streaming_handle_postcommit_error(
            r, ctx, conf, error_code);
    }

    /*
     * The input that triggered this latch could not be retained and was
     * abandoned.  After the older downstream-owned output drains, terminate
     * instead of allowing later input to bypass the missing chunk.
     */
    if (ctx->streaming.completion.failopen_abort_after_pending) {
        return ngx_http_markdown_streaming_abort_failopen_after_pending(
            r, ctx, conf);
    }

    if (ctx->streaming.completion.failopen_active
        && ngx_http_markdown_streaming_pending_input_is_empty(ctx)
        && ctx->streaming.completion.upstream_terminal_seen)
    {
        ctx->streaming.completion.upstream_terminal_seen = 0;
        /*
         * Check the request-type-aware terminal-delivered latch.
         * Main requests check main_terminal_sent (last_buf delivered);
         * subrequests check subrequest_terminal_sent (last_in_chain
         * delivered).  If the matching terminal has already been
         * confirmed downstream (e.g. via a backpressured fail-open chain
         * that carried last_in_chain in its tail), return without
         * synthesizing a duplicate terminal.  (Rule 47: the latch is
         * only set after confirmed delivery, never on NGX_AGAIN.)
         */
        if (ngx_http_markdown_streaming_terminal_sent_for_request(r, ctx)) {
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return rc;
        }
        return ngx_http_markdown_streaming_send_output(
            r, ctx, NULL, 0, /* last_buf */ 1);
    }

    if (ctx->streaming.classify.input_disposition == NGX_HTTP_MD_INPUT_TERMINAL) {
        ctx->streaming.completion.upstream_terminal_seen = 0;
        ngx_http_markdown_streaming_pending_input_clear(ctx);
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return rc;
    }

    /* Step 2: Process pending input if any */
    if (!ngx_http_markdown_streaming_pending_input_is_empty(ctx)) {
        /*
         * Detach the pending_input chain and feed it through
         * process_chain.  process_chain will re-enqueue any remainder
         * that hits NGX_AGAIN back to pending_input.
         */
        input_chain = ctx->streaming.pending_input.head;
        ctx->streaming.pending_input.head = NULL;
        ctx->streaming.pending_input.tail = NULL;
        ctx->streaming.pending_input.bytes = 0;
        ctx->streaming.pending_input.links = 0;
        if (ctx->streaming.completion.failopen_active) {
            return ngx_http_markdown_streaming_continue_failopen_input(
                r, ctx, input_chain);
        }
        rc = ngx_http_markdown_streaming_process_chain(
            r, ctx, conf, input_chain, &last_buf, &fallback_cl);

        if (rc == NGX_AGAIN) {
            /* process_chain re-enqueued remainder + set terminal_seen */
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return NGX_AGAIN;
        }
        if (rc == NGX_DONE) {
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
                r, fallback_cl, last_buf);
        }
        if (rc != NGX_OK) {
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return rc;
        }
        /* rc == NGX_OK: all pending_input consumed */
    }

    /*
     * Step 3: Finalize if upstream terminal was seen and we are
     * still eligible for streaming conversion.
     *
     * terminal_seen is set by pending_input enqueue from the
     * original input chain.  It represents the upstream input EOF,
     * not the downstream delivery state.  finalize only when all
     * pending_input has been consumed.
     */
    if (ngx_http_markdown_streaming_pending_input_is_empty(ctx)
        && ctx->streaming.completion.upstream_terminal_seen
        && ctx->eligible)
    {
        ctx->streaming.completion.upstream_terminal_seen = 0;
        return ngx_http_markdown_streaming_finalize_request(
            r, ctx, conf);
    }

    /*
     * Step 4: Legacy finalize_after_pending path.
     *
     * This is set by finalize_request itself (finalize_decomp
     * NGX_AGAIN), not by process_chain.  When the finalize
     * path's own output hits backpressure, it sets this latch
     * so we re-enter finalize after the pending output drains.
     */
    if (ctx->streaming.completion.finalize_after_pending) {
        ctx->streaming.completion.finalize_after_pending = 0;
        return ngx_http_markdown_streaming_finalize_request(
            r, ctx, conf);
    }

    ngx_http_markdown_streaming_sync_buffered(r, ctx);
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
        /* Only set latch on successful delivery, not NGX_AGAIN (Rule 47) */
        if (rc == NGX_OK) {
            ctx->failopen_completed = 1;
        }
    }

    return rc;
}

/*
 * Handle NGX_AGAIN returned from process_chunk for a single chain link.
 *
 * RETAIN disposition: fail-open shared ngx_buf_t — do NOT advance pos.
 * The fail-open clone (pending_output) references the same buf and covers
 * the full input chain, so cl->next must NOT be enqueued separately.
 *
 * CONSUMED disposition: Rust ate this chunk. Advance pos so NGINX can
 * release the busy buffer, then enqueue the remainder (cl->next) to
 * pending_input so it is not stranded in u->busy_bufs (NGINX does not
 * re-submit busy buffers to the body filter).
 *
 * terminal_seen is captured during enqueue (from last_buf/last_in_chain
 * on any remaining link); finalize_after_pending is handled by
 * terminal_seen in handle_null_input, not here.
 *
 * Returns NGX_AGAIN (after sync) or an enqueue error rc.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_consumed_again(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_chain_t *cl)
{
    ngx_int_t  rc;

    if (ctx->streaming.classify.input_disposition
        == NGX_HTTP_MD_INPUT_RETAIN)
    {
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_AGAIN;
    }

    /* CONSUMED: advance pos so NGINX releases the busy buffer. */
    cl->buf->pos = cl->buf->last;

    if (cl->next != NULL) {
        uint32_t  enqueue_error = ERROR_SUCCESS;

        rc = ngx_http_markdown_streaming_pending_input_enqueue_remainder(
            r, ctx, conf, cl->next, &enqueue_error);
        if (rc != NGX_OK) {
            if (ctx->streaming.pending_output != NULL
                && ctx->streaming.commit_state
                   == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
            {
                return ngx_http_markdown_streaming_defer_postcommit_error(
                    r, ctx, enqueue_error, cl->next);
            }
            return rc;
        }
    }

    ngx_http_markdown_streaming_sync_buffered(r, ctx);
    return NGX_AGAIN;
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
            ctx->streaming.completion.upstream_terminal_seen = 1;
        }

        rc = ngx_http_markdown_streaming_process_chunk(
            r, ctx, conf, cl->buf);

        rc = ngx_http_markdown_streaming_handle_chunk_result(
            r, ctx, in, rc);

        if (ctx->streaming.classify.input_disposition
            == NGX_HTTP_MD_INPUT_TERMINAL)
        {
            ngx_http_markdown_streaming_abandon_input(cl);
            ngx_http_markdown_streaming_pending_input_clear(ctx);
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return rc;
        }

        if (rc != NGX_OK) {
            if (rc == NGX_AGAIN) {
                return ngx_http_markdown_streaming_handle_consumed_again(
                    r, ctx, conf, cl);
            }
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
 * Detect terminal last_buf in a chain without enqueuing it.
 *
 * Used by the fail-open abort path when enqueue has already failed
 * and we must still preserve upstream EOF semantics so the request
 * can terminate safely after the pending output drains.
 */
static ngx_flag_t
ngx_http_markdown_streaming_chain_has_terminal(ngx_chain_t *in,
    const ngx_http_request_t *r)
{
    for (ngx_chain_t *cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL) {
            continue;
        }
        if (cl->buf->last_buf
            || (r != r->main && cl->buf->last_in_chain))
        {
            return 1;
        }
    }
    return 0;
}


/*
 * Handle last_buf finalization in the streaming body filter.
 *
 * When an upstream terminal buffer arrives, reset the terminal flag and
 * call finalize_request.  If finalize returns NGX_DECLINED and the
 * request is not eligible for streaming, route through failopen_passthrough.
 *
 * Returns the finalize/passthrough rc, or NGX_OK if no finalization needed.
 */
static ngx_int_t
ngx_http_markdown_streaming_finalize_on_last_buf(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_chain_t *in,
    ngx_flag_t last_buf)
{
    ngx_int_t  rc;

    if (!last_buf) {
        return NGX_OK;
    }

    ctx->streaming.completion.upstream_terminal_seen = 0;
    rc = ngx_http_markdown_streaming_finalize_request(
        r, ctx, conf);

    if (rc == NGX_DECLINED && !ctx->eligible) {
        /*
         * Call failopen_passthrough first; only set the latch
         * after successful downstream delivery.  Setting the
         * latch before the call would cause a backpressure
         * (NGX_AGAIN) re-entry to skip the resume path and
         * lose pending output.  (Rule 47)
         */
        rc = ngx_http_markdown_streaming_failopen_passthrough(
            r, ctx, in);
        if (rc == NGX_OK) {
            ctx->failopen_completed = 1;
        }
    }

    return rc;
}


/*
 * Handle new non-NULL input arriving while streaming pending_output
 * is non-NULL (downstream backpressure active).
 *
 * FAILOPEN_ACTIVE mode: retain future input without re-entering Rust or
 * building a new output chain.  If the retained-input budget is
 * exhausted, latch failopen_abort_after_pending so after the old
 * pending output drains the request aborts without a clean last_buf,
 * instead of hiding missing bytes or submitting a second chain while
 * downstream still owns the first (Rule 1 backpressure contract).
 * TERMINAL disposition: abandon the input and return NGX_AGAIN.
 * Otherwise: enqueue the remainder; on budget exhaustion, route through
 * post-commit or pre-commit error handling (which may fail-open).
 *
 * Returns NGX_AGAIN when the input was enqueued/abandoned (caller should
 * return NGX_AGAIN), or a final error/fail-open rc the caller propagates.
 *
 * Shared by ngx_http_markdown_body_filter (request_impl.h, which forward-
 * declares this helper) and ngx_http_markdown_streaming_body_filter below,
 * so both entry points stay below SonarCloud c:S3776/c:S134 thresholds.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_new_input_with_pending(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, ngx_chain_t *in)
{
    ngx_int_t  rc;

    if (ctx->streaming.classify.input_disposition
        == NGX_HTTP_MD_INPUT_TERMINAL)
    {
        ngx_http_markdown_streaming_abandon_input(in);
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_AGAIN;
    }

    /*
     * P1-2: fail-open mode is a request-lifetime terminal state.
     * Never re-enter precommit_error (which would double-count
     * conversions_failed) or failopen_passthrough (which would
     * build a new replay+input chain and submit it while the old
     * pending_output is still downstream-owned).  Retain future
     * input; on budget exhaustion, latch a safe terminal after
     * drain.  (Rule 1, Rule 47)
     */
    if (ctx->streaming.completion.failopen_active) {
        uint32_t  enqueue_error;

        if (ctx->streaming.completion.failopen_abort_after_pending) {
            if (ngx_http_markdown_streaming_chain_has_terminal(in, r)) {
                ctx->streaming.completion.upstream_terminal_seen = 1;
            }
            ngx_http_markdown_streaming_abandon_input(in);
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return NGX_AGAIN;
        }

        enqueue_error = ERROR_BUDGET_EXCEEDED;
        rc = ngx_http_markdown_streaming_pending_input_enqueue_remainder(
            r, ctx, conf, in, &enqueue_error);
        if (rc == NGX_OK) {
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return NGX_AGAIN;
        }

        if (ngx_http_markdown_streaming_chain_has_terminal(in, r)) {
            ctx->streaming.completion.upstream_terminal_seen = 1;
        }
        ctx->streaming.completion.failopen_abort_after_pending = 1;
        ctx->streaming.completion.failopen_abort_error_code = enqueue_error;
        ctx->streaming.completion.pending_failopen_delivery = 0;
        ngx_http_markdown_streaming_abandon_input(in);
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_AGAIN;
    }

    {
        uint32_t  enqueue_error = ERROR_BUDGET_EXCEEDED;

        rc = ngx_http_markdown_streaming_pending_input_enqueue_remainder(
            r, ctx, conf, in, &enqueue_error);
        if (rc == NGX_OK) {
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return NGX_AGAIN;
        }

        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return ngx_http_markdown_streaming_defer_postcommit_error(
                r, ctx, enqueue_error, in);
        }

        rc = ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, enqueue_error);
        if (rc == NGX_DECLINED && !ctx->eligible) {
            return ngx_http_markdown_streaming_failopen_passthrough(
                r, ctx, in);
        }
        return rc;
    }
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

    if (ctx->streaming.pending_output != NULL) {
        return ngx_http_markdown_streaming_handle_new_input_with_pending(
            r, ctx, conf, in);
    }

    if (ctx->streaming.classify.input_disposition == NGX_HTTP_MD_INPUT_TERMINAL) {
        ngx_http_markdown_streaming_abandon_input(in);
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return NGX_OK;
    }

    /* Initialize streaming handle on first call */
    rc = ngx_http_markdown_streaming_ensure_handle(
        r, ctx, conf, in);
    if (rc != NGX_OK) {
        return rc;
    }

    /*
     * On init failure, ensure_handle() routes through
     * failopen_passthrough() and, on confirmed downstream delivery
     * (NGX_OK/NGX_DONE), latches ctx->failopen_completed before
     * returning NGX_OK here.  The fail-open body (including any
     * terminal buffer) has therefore already been forwarded
     * downstream — falling through to the generic passthrough below
     * would resubmit the same `in` chain a second time.
     */
    if (ctx->failopen_completed) {
        return NGX_OK;
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

    if (ctx->streaming.classify.input_disposition == NGX_HTTP_MD_INPUT_TERMINAL) {
        return NGX_OK;
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
    return ngx_http_markdown_streaming_finalize_on_last_buf(
        r, ctx, conf, in, last_buf);
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_STREAMING_IMPL_H */
