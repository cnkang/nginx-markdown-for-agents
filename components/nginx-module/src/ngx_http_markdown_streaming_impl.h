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


typedef struct {
    ngx_flag_t  main_terminal;
    ngx_flag_t  subrequest_terminal;
} ngx_http_markdown_pending_terminal_t;


/* Forward declarations */
static ngx_flag_t ngx_http_markdown_streaming_delivery_ok(ngx_int_t rc);
static void ngx_http_markdown_streaming_record_send_delivery(
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_pending_terminal_t *terminal,
    const u_char *data, size_t len, ngx_flag_t delivered);
static void ngx_http_markdown_streaming_release_pending_header_output(
    ngx_http_markdown_ctx_t *ctx);
static ngx_int_t ngx_http_markdown_streaming_handle_output_loss(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);

static void ngx_http_markdown_streaming_record_postcommit_category(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, uint32_t error_code);
static void ngx_http_markdown_streaming_record_postcommit_category_metrics(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf, uint32_t error_code);
static void ngx_http_markdown_streaming_record_postcommit_aborted(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);
static void ngx_http_markdown_streaming_record_postcommit_success(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf);

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
/*
 * Finalization state-machine invariants.
 *
 * Finalization is split across three helpers so that backpressure
 * (NGX_AGAIN) cannot be mistaken for success:
 *
 *   1. ngx_http_markdown_streaming_finalize_request   (orchestrator)
 *      - Decompresses tail data via finalize_decomp().
 *      - Calls markdown_streaming_finalize() (FFI).
 *      - Sends the final Markdown chunk.
 *      - Records finalization stats from the FFI result.
 *      - Delegates terminal delivery to finish_terminal().
 *
 *   2. ngx_http_markdown_streaming_finish_terminal()
 *      - Sends the empty last_buf chunk.
 *      - Sets pending_terminal_metrics when backpressured
 *        (final_send_rc == NGX_AGAIN) so metrics record only
 *        after the deferred send is confirmed.
 *
 *   3. ngx_http_markdown_streaming_record_finalize_stats()
 *      - Logs the ETag and token estimate.
 *      - Captures peak_memory_bytes before freeing the FFI result.
 *
 * Latch semantics:
 *   - finalize_after_pending == 1  -> decompression tail is deferred;
 *     the caller re-enters finalize_request() after draining.
 *   - finalize_pending_lastbuf == 1 -> terminal last_buf is deferred
 *     because the final Markdown chunk was backpressured.
 *   - pending_terminal_metrics == 1 -> terminal delivery was backpressured;
 *     success/failure metrics must record only after the deferred send.
 *
 * Only one of the three latches may be set at any point during a
 * single finalization pass; they are cleared by their owning helpers
 * at the respective finalization stage.
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
 *   NGX_OK, NGX_DONE, NGX_AGAIN, or NGX_ERROR
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

static ngx_int_t
ngx_http_markdown_streaming_finalize_and_dispatch_fallback(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_flag_t last_buf);


static void
ngx_http_markdown_streaming_release_pending_header_output(
    ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL
        || ctx->streaming.pending_meta.pending_header_output == NULL)
    {
        return;
    }

    markdown_streaming_output_free(
        ctx->streaming.pending_meta.pending_header_output,
        ctx->streaming.pending_meta.pending_header_output_len);
    ctx->streaming.pending_meta.pending_header_output = NULL;
    ctx->streaming.pending_meta.pending_header_output_len = 0;
}


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
     * A feed result can be waiting for a header-only NGX_AGAIN retry.
     * Unlike pending_output, this pointer is still owned by the Rust FFI.
     */
    ngx_http_markdown_streaming_release_pending_header_output(ctx);

    /* Release a deferred finalize result when the header NGX_AGAIN
     * retry never completed on this request. */
    if (ctx->streaming.completion.finalize_pending_result != NULL) {
        markdown_result_free(
            ctx->streaming.completion.finalize_pending_result);
        ctx->streaming.completion.finalize_pending_result = NULL;
    }

    /*
     * Clear the pending_output anchor/state.
     *
     * Ownership is layered explicitly elsewhere and must not be
     * re-derived here from generic ngx_buf_t behavior flags
     * (memory/temporary describe buffer *behavior*, not Rust
     * allocator *provenance*):
     *
     *   - Pool-copy output buffers (send_output) are ngx_palloc'd from
     *     the request pool and reclaimed with it; nothing to free here.
     *   - Fail-open cloned chain links share the ngx_buf_t with an
     *     upstream/module-owned buffer, which may legitimately be
     *     marked temporary=1.  That memory is never Rust-allocated;
     *     calling markdown_streaming_output_free() on it would be an
     *     invalid free.
     *
     * Walking pending_output and branching on cl->buf->temporary cannot
     * distinguish these cases and would risk an invalid free for shared
     * fail-open buffers.  This function only clears our tracking state so
     * stale references are not observed after cleanup runs.
     */
    ngx_http_markdown_pending_output_set(
        &ctx->streaming.pending_output, NULL);

    /*
     * Clear pending input chain.  Links are pool-allocated and will be
     * reclaimed when the request pool is destroyed.  The shared ngx_buf_t
     * pointers are upstream-owned and managed by NGINX.  We only reset
     * our tracking state.
     */
    ngx_http_markdown_streaming_pending_input_clear(ctx);
}

static ngx_int_t
ngx_http_markdown_is_excluded_stream_type(
    ngx_http_request_t *r)
{
    static u_char  sse_type[] = "text/event-stream";
    static u_char  ndjson_type[] = "application/x-ndjson";

    if (r->headers_out.content_type.data == NULL) {
        return 0;
    }

    /* Built-in hard exclusion: text/event-stream */
    if (r->headers_out.content_type.len >= sizeof(sse_type) - 1
        && ngx_strncasecmp(r->headers_out.content_type.data,
                           sse_type, sizeof(sse_type) - 1) == 0
        && (r->headers_out.content_type.len == sizeof(sse_type) - 1
            || r->headers_out.content_type.data[sizeof(sse_type) - 1] == ';'
            || r->headers_out.content_type.data[sizeof(sse_type) - 1] == ' '
            || r->headers_out.content_type.data[sizeof(sse_type) - 1] == '\t'))
    {
        return 1;
    }

    /* Built-in hard exclusion: application/x-ndjson */
    if (r->headers_out.content_type.len >= sizeof(ndjson_type) - 1
        && ngx_strncasecmp(r->headers_out.content_type.data,
                           ndjson_type, sizeof(ndjson_type) - 1) == 0
        && (r->headers_out.content_type.len == sizeof(ndjson_type) - 1
            || r->headers_out.content_type.data[sizeof(ndjson_type) - 1] == ';'
            || r->headers_out.content_type.data[sizeof(ndjson_type) - 1] == ' '
            || r->headers_out.content_type.data[sizeof(ndjson_type) - 1] == '\t'))
    {
        return 1;
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
 * 7. stream_excluded_types exclusion match -> PATH_FULLBUFFER
 * 8. policy == force -> PATH_STREAMING
 * 9. policy == auto + CL >= NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT -> PATH_STREAMING
 * 10. policy == auto + chunked -> PATH_STREAMING
 * 11. policy == auto + CL < NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT -> PATH_FULLBUFFER
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

    /* Rule 7: stream_excluded_types + built-in hard exclusions */
    if (ngx_http_markdown_is_excluded_stream_type(r)
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
           < NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT)
    {
        /* CL < 1 MiB fixed threshold: use full-buffer */
        ngx_http_markdown_log_decision(r, conf, eff,
            ngx_http_markdown_reason_eligible_fullbuffer_auto());
        return ngx_http_markdown_path_selection(
            NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
            NGX_HTTP_MARKDOWN_STREAM_REASON_BELOW_THRESHOLD);
    }

    /* auto + CL >= NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT or chunked (no CL) */
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
 * Note: stream_commit_headers() applies the response-header mutations and
 * eagerly records its committed state. The caller must roll back
 * headers_committed and COMMIT_POST when downstream returns NGX_AGAIN, and
 * publishes headers_forwarded only after the header chain is accepted.
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
 * Sets NGX_HTTP_MARKDOWN_BUFFERED when any output, input, header commit,
 * finalize, or fail-open abort continuation remains pending.  Clears it
 * otherwise.  This is the single authority for the buffered bit on the
 * streaming path — individual helpers must not set/clear it directly.
 */
static void
ngx_http_markdown_streaming_sync_buffered(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx)
{
    if (ctx->streaming.pending_output != NULL
        || ctx->streaming.pending_input.head != NULL
        || ctx->streaming.pending_meta.pending_header_output != NULL
        || ctx->stream_sm.headers_pending
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


static void
ngx_http_markdown_streaming_release_finalize_pending(
    ngx_http_markdown_ctx_t *ctx)
{
    if (ctx->streaming.completion.finalize_pending_result != NULL) {
        markdown_result_free(ctx->streaming.completion.finalize_pending_result);
        ctx->streaming.completion.finalize_pending_result = NULL;
    }
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
    ngx_http_markdown_pending_output_set(
        &ctx->streaming.pending_output, NULL);
    ctx->streaming.pending_meta.has_data = 0;
    ctx->streaming.pending_meta.bytes = 0;
    ctx->streaming.pending_meta.main_terminal = 0;
    ctx->streaming.pending_meta.subrequest_terminal = 0;
    ctx->streaming.pending_meta.pending_abort_terminal = 0;

    ctx->streaming.completion.finalize_after_pending = 0;
    ctx->streaming.completion.finalize_pending_lastbuf = 0;
    ctx->streaming.completion.pending_terminal_metrics = 0;
    ngx_http_markdown_streaming_release_finalize_pending(ctx);
    ctx->streaming.completion.pending_failopen_delivery = 0;
    ctx->streaming.completion.postcommit_error_after_pending = 0;
    ctx->streaming.completion.postcommit_error_code = ERROR_SUCCESS;
    ctx->streaming.completion.safe_finish_error_pending = 0;
    ctx->streaming.completion.safe_finish_error_code = ERROR_SUCCESS;
    ctx->streaming.completion.safe_finish_output_loss = 0;
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

    ngx_http_markdown_pending_output_set(
        &ctx->streaming.pending_output, out);
    ctx->streaming.pending_meta.has_data =
        (data != NULL && len > 0) ? 1 : 0;
    ctx->streaming.pending_meta.bytes = len;
    ctx->streaming.pending_meta.main_terminal = terminal.main_terminal;
    ctx->streaming.pending_meta.subrequest_terminal =
        terminal.subrequest_terminal;
    ctx->streaming.pending_meta.pending_abort_terminal = 0;

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

    /* Keep empty buffers in a valid, zero-length state even when the
     * allocator does not guarantee zeroed memory. */
    b->pos = (u_char *) b;
    b->last = b->pos;

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
            r, ctx, out, data, len, terminal);
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
ngx_http_markdown_streaming_record_postcommit_outcome(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    const ngx_str_t *decision_reason,
    const char *conversion_status,
    const char *terminal_reason)
{
    if (ctx->streaming.completion.failure_recorded) {
        return;
    }

    /*
     * Preserve caller-set specific post-commit reason (for example,
     * POSTCOMMIT_BUDGET_EXCEEDED) and provide a safe fallback for a
     * delivery outcome that did not receive a more specific classifier.
     */
    if (ctx->streaming.reason
        < NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_HTML_ERROR)
    {
        ctx->streaming.reason =
            NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_PARSE_ERROR;
    }
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.postcommit_error_total);
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.failed_total);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
        "markdown: streaming post-commit failure: "
        "engine=streaming phase=postcommit "
        "committed=1 fallback_available=0 "
        "reason=%s content_type=%V "
        "content_length_known=%d chunked=%d "
        "error_policy=%s",
        ngx_http_markdown_stream_reason_str(ctx->streaming.reason),
        &r->headers_out.content_type,
        (r->headers_out.content_length_n >= 0) ? 1 : 0,
        (r->headers_out.content_length_n < 0) ? 1 : 0,
        (ngx_http_markdown_effective_error_policy(
             ctx->effective_conf, conf)
         == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT)
            ? "fail_closed" : "pass");

    ctx->streaming.completion.failure_recorded = 1;

    ngx_http_markdown_log_decision(
        r, conf, ctx->effective_conf, decision_reason);
    ngx_http_markdown_log_streaming_terminal_decision(
        r, ctx, conf, conversion_status, terminal_reason, "postcommit");
}


static void
ngx_http_markdown_streaming_record_postcommit_failure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_http_markdown_streaming_record_postcommit_outcome(
        r, ctx, conf, ngx_http_markdown_reason_failed_closed(),
        "failed_closed", "failed_closed");
}


/*
 * Record a post-commit abort (streaming mid-flight failure after the
 * response was committed).  NOTE on raw-counter semantics: the outcome
 * recorder below increments conversions_failed / streaming.failed_total for
 * these aborts, so raw counters classify aborted-but-delivered requests as
 * failed.  The metrics v1 renderer reclassifies them (metrics_impl.h
 * subtracts terminal_aborted_total from failed_closed and reports
 * requests.aborted separately); consumers of the raw counters must apply
 * the same reclassification.
 */
static void
ngx_http_markdown_streaming_record_postcommit_aborted(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_http_markdown_streaming_record_postcommit_outcome(
        r, ctx, conf, ngx_http_markdown_reason_streaming_fail_postcommit(),
        "aborted", "streaming_mid_flight_error");
}


/* Record the converted outcome after safe-finish terminal delivery. */
static void
ngx_http_markdown_streaming_record_postcommit_success(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    if (ctx->error.terminal_decision_recorded) {
        return;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.succeeded_total);
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_succeeded);
    NGX_HTTP_MARKDOWN_METRIC_INC(results.delivery_count);

    ngx_http_markdown_log_decision(r, conf, ctx->effective_conf,
        ngx_http_markdown_reason_streaming_convert());
    ngx_http_markdown_log_streaming_terminal_decision(
        r, ctx, conf, NGX_HTTP_MARKDOWN_CONV_SUCCESS,
        "converted", "postcommit");
    ngx_http_markdown_record_per_path_metrics(r, conf, 0);
    ctx->error.terminal_decision_recorded = 1;
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
        /*
         * Route through the guarded post-commit recorder so delivery
         * accounting stays single-sourced (the direct-increment duplicate
         * bypassed the terminal_decision_recorded guard and could silently
         * double-count from a future call site).
         */
        ngx_http_markdown_streaming_record_postcommit_success(
            r, ctx, conf);
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
        NGX_HTTP_MARKDOWN_METRIC_INC(perf.copied_output_total);
    }

    ctx->streaming.pending_meta.bytes = 0;
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

    ngx_http_markdown_streaming_record_postcommit_success(r, ctx, conf);
    ctx->streaming.completion.pending_terminal_metrics = 0;
}


/* Metadata snapshot taken before downstream owns and may mutate the chain. */
typedef struct {
    ngx_flag_t  main_terminal;
    ngx_flag_t  subrequest_terminal;
    ngx_flag_t  has_data;
    ngx_flag_t  failopen;
    ngx_flag_t  safe_finish;
    ngx_flag_t  abort_terminal;
    uint32_t    safe_finish_error;
} ngx_http_markdown_streaming_pending_snapshot_t;


/* Resume sending pending output after backpressure clears. */
static ngx_int_t
ngx_http_markdown_streaming_resume_failure(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_int_t downstream_rc,
    ngx_http_markdown_streaming_pending_snapshot_t pending)
{
    ctx->streaming.completion.pending_terminal_metrics = 0;
    ctx->streaming.completion.pending_failopen_delivery = 0;
    ctx->streaming.completion.safe_finish_error_pending = 0;
    ctx->streaming.completion.safe_finish_error_code = ERROR_SUCCESS;

    if (!pending.failopen && pending.has_data) {
        ctx->streaming.classify.last_send_failure_origin =
            NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM;
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return ngx_http_markdown_streaming_handle_output_loss(
            r, ctx, conf);
    }

    if (!pending.abort_terminal && pending.safe_finish) {
        ngx_http_markdown_streaming_record_postcommit_category(
            r, ctx, conf, pending.safe_finish_error);
    }
    /* For a pending abort, category metrics were recorded before the
     * terminal send.  The definitive downstream failure is therefore
     * recorded below without reclassifying the original error. */
    ngx_http_markdown_streaming_record_postcommit_failure(r, ctx, conf);
    ngx_http_markdown_streaming_sync_buffered(r, ctx);
    return downstream_rc;
}


static ngx_int_t
ngx_http_markdown_streaming_resume_success(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_int_t downstream_rc,
    ngx_flag_t pending_safe_finish,
    ngx_flag_t pending_abort_terminal,
    uint32_t pending_error)
{
    if (ctx->streaming.completion.pending_failopen_delivery) {
        NGX_HTTP_MARKDOWN_METRIC_INC(results.failopen_count);
        ctx->streaming.completion.pending_failopen_delivery = 0;
        ctx->failopen_completed = 1;
    }

    if (pending_safe_finish) {
        ctx->streaming.completion.safe_finish_error_pending = 0;
        ctx->streaming.completion.safe_finish_error_code = ERROR_SUCCESS;
        ngx_http_markdown_streaming_record_postcommit_category_metrics(
            r, ctx, conf, pending_error);
        ngx_http_markdown_streaming_record_postcommit_success(
            r, ctx, conf);
    }

    if (pending_abort_terminal
        && !ctx->streaming.completion.terminal_aborted_recorded)
    {
        ngx_http_markdown_metrics_record_terminal_abort();
        ctx->streaming.completion.terminal_aborted_recorded = 1;
        ngx_http_markdown_streaming_record_postcommit_aborted(
            r, ctx, conf);
    }

    ngx_http_markdown_streaming_record_pending_terminal_success(r, ctx, conf);
    if (ctx->streaming.completion.finalize_pending_lastbuf) {
        return ngx_http_markdown_streaming_send_deferred_lastbuf(
            r, ctx, conf);
    }

    ngx_http_markdown_streaming_sync_buffered(r, ctx);
    return downstream_rc;
}


static ngx_int_t
ngx_http_markdown_streaming_resume_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t     rc;
    ngx_http_markdown_streaming_pending_snapshot_t  pending;

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
    pending.main_terminal = ctx->streaming.pending_meta.main_terminal;
    pending.subrequest_terminal =
        ctx->streaming.pending_meta.subrequest_terminal;
    pending.has_data = ctx->streaming.pending_meta.has_data;
    pending.failopen =
        ctx->streaming.completion.pending_failopen_delivery;
    pending.safe_finish =
        ctx->streaming.completion.safe_finish_error_pending;
    pending.safe_finish_error =
        ctx->streaming.completion.safe_finish_error_code;
    pending.abort_terminal =
        ctx->streaming.pending_meta.pending_abort_terminal;

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

    ngx_http_markdown_pending_output_set(
        &ctx->streaming.pending_output, NULL);

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
        && r == r->main && pending.main_terminal)
    {
        ctx->streaming.main_terminal_sent = 1;
    }
    if (ngx_http_markdown_streaming_delivery_ok(rc)
        && r != r->main && pending.subrequest_terminal)
    {
        ctx->streaming.subrequest_terminal_sent = 1;
    }

    /*
     * Clear consumed terminal metadata regardless of delivery result
     * so stale state does not persist into the next pending cycle.
     */
    ctx->streaming.pending_meta.main_terminal = 0;
    ctx->streaming.pending_meta.subrequest_terminal = 0;
    ctx->streaming.pending_meta.pending_abort_terminal = 0;

    /*
     * Pending output drained. Check if resume failed before
     * proceeding to deferred lastbuf, to avoid the failure
     * branch being short-circuited.
     */
    if (!ngx_http_markdown_streaming_delivery_ok(rc)) {
        return ngx_http_markdown_streaming_resume_failure(
            r, ctx, conf, rc, pending);
    }

    return ngx_http_markdown_streaming_resume_success(
        r, ctx, conf, rc, pending.safe_finish,
        pending.abort_terminal, pending.safe_finish_error);
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
 * cannot revert to HTML or send an HTTP error status.
 *
 * Branches (in order):
 * 1. Rust safe_finish succeeds (produces closing bytes or terminal
 *    chain) → immediate downstream send. The request outcome is
 *    converted after delivery. If send returns NGX_AGAIN, save pending
 *    provenance and defer the category/outcome record until resume.
 * 2. Rust safe_finish returns NGX_AGAIN → save original error,
 *    mark safe_finish_failure_pending=1, pending_kind=CLOSING_MARKDOWN.
 *    On resume: success → converted; failure → failed_closed.
 * 3. Rust safe_finish returns error before producing bytes (converter
 *    state prevents finalization) → Protocol_Safe_Abort (terminal chain
 *    send). Record the original category BEFORE abort. If terminal send
 *    returns NGX_AGAIN, preserve pending_abort_terminal; delivery then
 *    records aborted, while a definitive send failure records failed_closed.
 * 4. Rust safe_finish produces no closing bytes, terminal-only chain
 *    send immediately fails → record original category/recorder,
 *    return NGX_ERROR. No abort, no retry.
 * 5. Final fallback → postcommit_abort() (protocol-safe disconnect).
 *    Only reached when safe_finish cannot be attempted or has
 *    already failed in a way that precludes retry.
 *
 * In all branches: category metrics are recorded at most once and exactly
 * one request-level terminal outcome is published.  A successful safe-finish
 * is converted; a delivered protocol-safe abort is aborted; any definitive
 * failure is failed_closed.  The outcome latch prevents duplicate metrics or
 * diagnostics across downstream re-entry.
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_postcommit_error(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    uint32_t error_code)
{
    ngx_int_t   rc;
    ngx_flag_t  terminal_already_sent;

    ctx->streaming.classify.input_disposition = NGX_HTTP_MD_INPUT_TERMINAL;

    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
        "markdown: Post-Commit error, "
        "code=%ui, attempting safe_finish",
        (ngx_uint_t) error_code);

    ctx->streaming.completion.safe_finish_output_loss = 0;
    ctx->streaming.completion.safe_finish_terminal_send_failed = 0;

    rc = ngx_http_markdown_stream_postcommit_safe_finish(r, ctx);
    if (rc == NGX_OK || rc == NGX_DONE) {
        ngx_http_markdown_streaming_record_postcommit_category_metrics(
            r, ctx, conf, error_code);
        ngx_http_markdown_streaming_record_postcommit_success(
            r, ctx, conf);
        return rc;
    }

    if (rc == NGX_AGAIN) {
        ctx->streaming.completion.safe_finish_error_pending = 1;
        ctx->streaming.completion.safe_finish_error_code = error_code;
        return NGX_AGAIN;
    }

    if (ctx->streaming.completion.safe_finish_output_loss) {
        return ngx_http_markdown_streaming_handle_output_loss(r, ctx, conf);
    }

    /*
     * Rust safe-finish succeeded with zero closing bytes, but the
     * terminal send failed definitively.  Do NOT retry via abort —
     * the terminal send failure is the real error.  Record the
     * original failure and propagate the send error (Spec case 8).
     */
    if (ctx->streaming.completion.safe_finish_terminal_send_failed) {
        ngx_http_markdown_streaming_record_postcommit_category(
            r, ctx, conf, error_code);
        return NGX_ERROR;
    }

    /* Rust could not produce closing output. Record only the original
     * category before attempting the protocol-safe terminal abort. The
     * request-level outcome depends on whether that terminal is delivered. */
    ngx_http_markdown_streaming_record_postcommit_category_metrics(
        r, ctx, conf, error_code);
    terminal_already_sent =
        ngx_http_markdown_streaming_terminal_sent_for_request(r, ctx);
    rc = ngx_http_markdown_stream_postcommit_abort(r, ctx);
    if (rc == NGX_OK || rc == NGX_DONE) {
        if (!terminal_already_sent
            && ctx->streaming.completion.terminal_aborted_recorded)
        {
            ngx_http_markdown_streaming_record_postcommit_aborted(
                r, ctx, conf);
        }
        return rc;
    }
    if (rc != NGX_AGAIN) {
        ngx_http_markdown_streaming_record_postcommit_failure(
            r, ctx, conf);
    }
    return rc;
}


static void
ngx_http_markdown_streaming_record_postcommit_category_metrics(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    uint32_t error_code)
{
    /* Track budget exceeded as auxiliary classification. */
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
    } else if (!ctx->streaming.completion.failure_recorded
               && error_code == ERROR_INTERNAL)
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);
    } else if (!ctx->streaming.completion.failure_recorded) {
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);
    }

}


static void
ngx_http_markdown_streaming_record_postcommit_category(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    uint32_t error_code)
{
    ngx_http_markdown_streaming_record_postcommit_category_metrics(
        r, ctx, conf, error_code);
    ngx_http_markdown_streaming_record_postcommit_failure(r, ctx, conf);

    /* Debug log: bytes already sent, error type, chunks processed. */
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: post-commit error, "
        "bytes_sent=%uz, error_code=%ui, chunks=%ui",
        ctx->streaming.output.bytes,
        (ngx_uint_t) error_code,
        ctx->streaming.chunks_processed);
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

    if (error_code == ERROR_MEMORY_LIMIT
        || error_code == ERROR_BUDGET_EXCEEDED
        || error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED
        || error_code == ERROR_PARSE_TIMEOUT
        || error_code == ERROR_PARSE_BUDGET_EXCEEDED)
    {
        ctx->error.last_category =
            NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT;
    } else if (error_code == ERROR_INTERNAL) {
        ctx->error.last_category = NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    } else {
        ctx->error.last_category = NGX_HTTP_MARKDOWN_ERROR_CONVERSION;
    }
    ctx->error.has_category = 1;

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
     * Failure-reason classification (three-way):
     *   - resource-limit errors (MEMORY_LIMIT, BUDGET_EXCEEDED,
     *     DECOMPRESSION_BUDGET_EXCEEDED, PARSE_TIMEOUT,
     *     PARSE_BUDGET_EXCEEDED) -> failures_resource_limit
     *   - ERROR_INTERNAL -> failures_system
     *   - all other errors -> failures_conversion
     */
    NGX_HTTP_MARKDOWN_METRIC_INC(conversions_failed);

    if (error_code == ERROR_MEMORY_LIMIT
        || error_code == ERROR_BUDGET_EXCEEDED
        || error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED
        || error_code == ERROR_PARSE_TIMEOUT
        || error_code == ERROR_PARSE_BUDGET_EXCEEDED)
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_resource_limit);
    } else if (error_code == ERROR_INTERNAL) {
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_system);
    } else {
        NGX_HTTP_MARKDOWN_METRIC_INC(failures_conversion);
    }

    if (ngx_http_markdown_effective_error_policy(
            ctx->effective_conf, conf)
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
        ngx_http_markdown_log_streaming_terminal_decision(
            r, ctx, conf, NGX_HTTP_MARKDOWN_CONV_FAILED,
            "failed_closed", "precommit");
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
            (ngx_int_t) ngx_http_markdown_effective_error_status(
                ctx->effective_conf, conf));
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
    ngx_http_markdown_log_streaming_terminal_decision(
        r, ctx, conf, NGX_HTTP_MARKDOWN_CONV_FAILED,
        "failed_open", "precommit");
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
static ngx_int_t ngx_http_markdown_streaming_resume_header_commit(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);

static ngx_int_t
ngx_http_markdown_streaming_commit(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    /* A prior header-filter NGX_AGAIN already completed header mutation.
     * Resume only the downstream header filter; replaying update_headers
     * would apply the transaction a second time on the feed path. */
    if (ctx->stream_sm.headers_pending) {
        return ngx_http_markdown_streaming_resume_header_commit(r, ctx);
    }

    rc = ngx_http_markdown_streaming_update_headers(
        r, ctx, conf);
    if (rc != NGX_OK) {
        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, ERROR_STREAMING_FALLBACK);
    }

    rc = ngx_http_next_header_filter(r);
    if (rc == NGX_AGAIN) {
        /*
         * Header mutations are complete, but the downstream header chain
         * still owns delivery. Roll back every commit latch and remember the
         * retry so body output cannot run ahead of headers.
         */
        ctx->stream_sm.headers_committed = 0;
        ctx->stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
        ctx->stream_sm.headers_pending = 1;
        return NGX_AGAIN;
    }
    if (!ngx_http_markdown_streaming_delivery_ok(rc)) {
        ctx->stream_sm.headers_committed = 0;
        ctx->stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
        ctx->stream_sm.headers_pending = 0;
        return rc;
    }

    ctx->stream_sm.headers_pending = 0;
    ctx->stream_sm.headers_committed = 1;
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx->streaming.commit_state =
        NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx->headers_forwarded = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.commit_total);

    /*
     * Publish the request-level latches only after the downstream header
     * filter accepts the chain; this keeps the commit state atomic.
     */
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: commit "
        "boundary reached, headers sent");

    return NGX_OK;
}


/*
 * Retry only the downstream header filter after a commit returned NGX_AGAIN.
 * The header mutation phase is not repeated: it already completed before the
 * first downstream call. Publish all commit latches atomically after the
 * retry accepts the headers.
 *
 * NOTE: this re-entry assumes the downstream
 * header filter chain tolerates being invoked a second time. The built-in
 * ngx_http_header_filter guards on r->header_sent and returns NGX_OK, so no
 * duplicate header block reaches the wire; intermediate filters (e.g. gzip)
 * DO re-run their header-filter code on the retry. This matches the module's
 * documented deferral design; the canonical NGINX model (header NGX_AGAIN =
 * accepted, no re-entry) is tracked as a follow-up unification with the
 * full-buffer path.
 */
static ngx_int_t
ngx_http_markdown_streaming_resume_header_commit(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    if (!ctx->stream_sm.headers_pending) {
        return NGX_OK;
    }

    rc = ngx_http_next_header_filter(r);
    if (rc == NGX_AGAIN) {
        return NGX_AGAIN;
    }
    if (!ngx_http_markdown_streaming_delivery_ok(rc)) {
        ctx->stream_sm.headers_pending = 0;
        return rc;
    }

    ctx->stream_sm.headers_pending = 0;
    ctx->stream_sm.headers_committed = 1;
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx->streaming.commit_state =
        NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST;
    ctx->headers_forwarded = 1;
    NGX_HTTP_MARKDOWN_METRIC_INC(streaming.commit_total);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: resumed header commit successfully");

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
ngx_http_markdown_streaming_send_feed_output(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    u_char *out_data,
    size_t out_len)
{
    (void) conf;

    /*
     * The 0.9.2 contract has one streaming output ownership path: copy the
     * Rust result into request-pool memory, then release the result after the
     * downstream call (or after pending delivery records it).
     */
    ngx_int_t rc = ngx_http_markdown_streaming_send_output(
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
 * Note: this path does NOT increment streaming_failure_postcommit_abort.
 * That metric is reserved for protocol-safe abort attempts (via
 * postcommit_abort) that send an empty terminal chain.  Hard aborts
 * reset the connection without a clean HTTP closure and are tracked
 * solely by the failure_reason metrics above.
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
        if (rc == NGX_AGAIN) {
            /*
             * The downstream header chain still owns delivery after a
             * backpressure NGX_AGAIN (headers_pending). Keep the converted
             * bytes in module state until that header-only retry succeeds;
             * the input buffer is consumed and cannot be fed again on NULL
             * re-entry.
             */
            if (ctx->streaming.pending_meta.pending_header_output != NULL) {
                /*
                 * State-machine invariant violation: a second converted
                 * output arrived while the previous one is still waiting for
                 * the header retry.  This must not happen by construction;
                 * when it does it signals a missed NGX_AGAIN handling at a
                 * call site (fix the call site, do not relax this guard).
                 * Fail-open is not an option here: the headers were already
                 * mutated and queued by the write filter, so delivering the
                 * original body would produce a mismatched response.
                 * The error log entry below is the production signal for
                 * this invariant violation.
                 */
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                    "markdown: pending header output re-entry "
                    "(state-machine invariant violation)");
                markdown_streaming_output_free(out_data, out_len);
                return NGX_ERROR;
            }
            ctx->streaming.pending_meta.pending_header_output = out_data;
            ctx->streaming.pending_meta.pending_header_output_len = out_len;
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return NGX_AGAIN;
        }
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


static ngx_int_t
ngx_http_markdown_streaming_resume_pending_header_output(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    u_char  *out_data;
    size_t   out_len;

    if (ctx->streaming.pending_meta.pending_header_output == NULL) {
        return NGX_OK;
    }

    out_data = ctx->streaming.pending_meta.pending_header_output;
    out_len = ctx->streaming.pending_meta.pending_header_output_len;
    ctx->streaming.pending_meta.pending_header_output = NULL;
    ctx->streaming.pending_meta.pending_header_output_len = 0;

    return ngx_http_markdown_streaming_handle_success_output(
        r, ctx, conf, out_data, out_len);
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
ngx_http_markdown_streaming_map_feed_decomp_error(
    ngx_int_t rc,
    const ngx_http_markdown_streaming_decomp_t *decomp,
    ngx_log_t *log)
{
    if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total);
        ngx_http_markdown_record_decompression_failure_budget(
            decomp != NULL ? decomp->type
                           : NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN);
        NGX_HTTP_MARKDOWN_METRIC_INC(
            perf.decompression_budget_exceeded_total);
        return ERROR_DECOMPRESSION_BUDGET_EXCEEDED;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.format_error_total);
        ngx_http_markdown_record_decompression_failure_format(
            decomp != NULL ? decomp->type
                           : NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN);
        return ERROR_DECOMPRESSION_FORMAT_ERROR;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.truncated_input_total);
        ngx_http_markdown_record_decompression_failure_truncated(
            decomp != NULL ? decomp->type
                           : NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN);
        return ERROR_DECOMPRESSION_TRUNCATED_INPUT;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.io_error_total);
        ngx_http_markdown_record_decompression_failure_io(
            decomp != NULL ? decomp->type
                           : NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN);
        return ERROR_DECOMPRESSION_IO_ERROR;
    }

    /*
     * Bare NGX_ERROR: classify based on failure_origin field.
     * The decompressor sets this before each NGX_ERROR return.
     * Do NOT increment decompressions.io_error_total for these.
     */
    if (decomp != NULL
        && decomp->failure_origin
           == NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION)
    {
        return ERROR_MEMORY_LIMIT;
    }

    /*
     * INTERNAL, NONE, or a NULL decompressor all fail closed as INTERNAL.
     * Do not increment decompressions.io_error_total for these origins.
     *
     * Log an invariant violation when origin is NONE or decomp is NULL:
     * a well-behaved decompressor must always set a specific origin
     * before returning bare NGX_ERROR.  Silent fallback would mask
     * future omissions.
     */
    if (log != NULL
        && (decomp == NULL
            || decomp->failure_origin
               == NGX_HTTP_MD_DECOMP_ORIGIN_NONE))
    {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown: invariant violation: "
            "bare decompression NGX_ERROR without failure origin "
            "(decomp=%p, origin=%d)",
            decomp,
            decomp != NULL ? (int) decomp->failure_origin : -1);
    }

    return ERROR_INTERNAL;
}

static uint32_t
ngx_http_markdown_streaming_map_finalize_decomp_error(
    const ngx_http_markdown_ctx_t *ctx,
    ngx_int_t rc,
    const ngx_http_markdown_streaming_decomp_t *decomp,
    ngx_log_t *log)
{
    if (ctx == NULL) {
        return ERROR_INTERNAL;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.budget_exceeded_total);
        ngx_http_markdown_record_decompression_failure_budget(
            ctx->decompression.type);
        NGX_HTTP_MARKDOWN_METRIC_INC(
            perf.decompression_budget_exceeded_total);
        return ERROR_DECOMPRESSION_BUDGET_EXCEEDED;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.format_error_total);
        ngx_http_markdown_record_decompression_failure_format(
            ctx->decompression.type);
        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return ERROR_POST_COMMIT;
        }
        return ERROR_DECOMPRESSION_FORMAT_ERROR;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.truncated_input_total);
        ngx_http_markdown_record_decompression_failure_truncated(
            ctx->decompression.type);
        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return ERROR_POST_COMMIT;
        }
        return ERROR_DECOMPRESSION_TRUNCATED_INPUT;
    }

    if (rc == NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR) {
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.io_error_total);
        ngx_http_markdown_record_decompression_failure_io(
            ctx->decompression.type);
        if (ctx->streaming.commit_state
            == NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST)
        {
            return ERROR_POST_COMMIT;
        }
        return ERROR_DECOMPRESSION_IO_ERROR;
    }

    /*
     * Bare NGX_ERROR: classify based on failure_origin field.
     * Do NOT increment decompressions.io_error_total for these.
     */
    if (decomp != NULL
        && decomp->failure_origin
           == NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION)
    {
        return ERROR_MEMORY_LIMIT;
    }

    /*
     * INTERNAL, NONE, or a NULL decompressor all fail closed as INTERNAL.
     * Do not increment decompressions.io_error_total for these origins.
     *
     * Log an invariant violation when origin is NONE or decomp is NULL:
     * a well-behaved decompressor must always set a specific origin
     * before returning bare NGX_ERROR.  Silent fallback would mask
     * future omissions.
     */
    if (log != NULL
        && (decomp == NULL
            || decomp->failure_origin
               == NGX_HTTP_MD_DECOMP_ORIGIN_NONE))
    {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown: invariant violation: "
            "bare decompression NGX_ERROR without failure origin "
            "(decomp=%p, origin=%d)",
            decomp,
            decomp != NULL ? (int) decomp->failure_origin : -1);
    }

    return ERROR_INTERNAL;
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
                ngx_http_markdown_streaming_map_feed_decomp_error(
                    rc,
                    (const ngx_http_markdown_streaming_decomp_t *)
                        ctx->streaming.decompressor,
                    r->connection->log);

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
        || ctx->decompression.done
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
                ctx, rc,
                (const ngx_http_markdown_streaming_decomp_t *)
                    ctx->streaming.decompressor,
                r->connection->log);

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

    ctx->decompression.done = 1;
    ngx_http_markdown_record_decompression_success_metrics(ctx);

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
        if (rc == NGX_AGAIN) {
            /* Header chain owns delivery (headers_pending).  Defer the
             * finalize output until the header retry succeeds, matching
             * the feed path's pending_header_output handling: body
             * output must not run ahead of headers.  The null-input
             * re-entry path sends the deferred chunk after the retry. */
            if (ctx->streaming.completion.finalize_pending_result != NULL) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                    "markdown: finalize output re-entry while a previous "
                    "finalize output waits for the header retry "
                    "(state-machine invariant violation)");
                markdown_result_free(result);
                return NGX_ERROR;
            }
            ctx->streaming.completion.finalize_pending_result =
                ngx_palloc(r->pool, sizeof(struct MarkdownResult));
            if (ctx->streaming.completion.finalize_pending_result == NULL) {
                markdown_result_free(result);
                return NGX_ERROR;
            }
            *ctx->streaming.completion.finalize_pending_result = *result;
            markdown_result_init(result);
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return NGX_AGAIN;
        }
        if (rc != NGX_OK) {
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

        /*
         * Finalize-produced Markdown failed during C-side construction
         * or downstream delivery.  The Rust converter already produced
         * these bytes (markdown_streaming_finalize consumed the handle),
         * so the converter's state diverges from what was delivered.
         * Route through handle_output_loss(). Do not call the generic
         * failure recorder before branching because the output-loss
         * handler owns its recording.
         */
        return ngx_http_markdown_streaming_handle_output_loss(
            r, ctx, conf);
    }

    *final_send_rc = rc;
    return NGX_OK;
}


/* Record final result metadata and release the FFI-owned result buffers. */
static void
ngx_http_markdown_streaming_record_finalize_stats(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    struct MarkdownResult *result)
{
    size_t  peak_memory_bytes;

    if (result->etag != NULL && result->etag_len > 0) {
        ngx_log_debug3(NGX_LOG_DEBUG_HTTP,
            r->connection->log, 0,
            "markdown: finalize ETag value=\"%*s\", len=%uz",
            result->etag_len, result->etag, result->etag_len);
        ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
            "markdown: etag_len=%uz uri_len=%uz out_bytes=%uz tokens=%ui",
            result->etag_len, r->uri.len,
            ctx->streaming.output.bytes,
            (ngx_uint_t) result->token_estimate);
    }

    peak_memory_bytes = result->peak_memory_estimate;
    markdown_result_free(result);
    ngx_log_debug4(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: completed chunks=%ui flushes=%ui "
        "in_bytes=%uz out_bytes=%uz",
        ctx->streaming.chunks_processed,
        ctx->streaming.flushes_sent,
        ctx->streaming.total_input_bytes,
        ctx->streaming.output.bytes);

    if (ngx_http_markdown_metrics != NULL) {
        ngx_http_markdown_metrics->streaming.last_peak_memory_bytes =
            (ngx_atomic_t) peak_memory_bytes;
    }
}

/* Send the terminal last_buf and record delivery metrics after a real send. */
static ngx_int_t
ngx_http_markdown_streaming_finish_terminal(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_int_t final_send_rc)
{
    ngx_int_t  rc;

    if (final_send_rc == NGX_AGAIN) {
        ctx->streaming.completion.finalize_pending_lastbuf = 1;
        return ngx_http_markdown_streaming_handle_backpressure(r, ctx);
    }

    rc = ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);
    if (rc == NGX_OK || rc == NGX_DONE) {
        ngx_http_markdown_streaming_record_postcommit_success(
            r, ctx, conf);
    } else if (rc == NGX_AGAIN) {
        ctx->streaming.completion.pending_terminal_metrics = 1;
    } else {
        ngx_http_markdown_streaming_record_postcommit_failure(
            r, ctx, conf);
    }
    return rc;
}

/*
 * Finalize the streaming conversion on last_buf.  Decompression and Rust
 * finalization happen before the terminal send; terminal delivery is kept in
 * a separate helper so NGX_AGAIN cannot be mistaken for success.
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
    ngx_int_t              final_send_rc = NGX_OK;

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

    ngx_http_markdown_streaming_record_finalize_stats(r, ctx, &result);
    return ngx_http_markdown_streaming_finish_terminal(
        r, ctx, conf, final_send_rc);
}


/*
 * Initialize the decompressor and both bounded pre-commit buffers after the
 * streaming handle has been created.  Any allocation failure is routed
 * through the pre-commit policy before data can be consumed irreversibly.
 */
static ngx_int_t
ngx_http_markdown_streaming_init_buffers(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t             rc;
    ngx_atomic_uint_t    *brotli_workspace_bytes;
    size_t                brotli_workspace_limit;

    if (ctx->decompression.needed) {
        ngx_http_markdown_decomp_failure_origin_e  create_origin;
        uint32_t                                   create_error;

        brotli_workspace_bytes = NULL;
        brotli_workspace_limit = 0;
#ifdef NGX_HTTP_BROTLI
        {
            ngx_http_markdown_main_conf_t  *main_conf;

            main_conf = ngx_http_get_module_main_conf(
                r, ngx_http_markdown_filter_module);
            if (main_conf != NULL) {
                brotli_workspace_bytes =
                    &main_conf->brotli_workspace_bytes;
                brotli_workspace_limit =
                    (size_t) main_conf->brotli_workspace_limit;
            }
        }
#endif

        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.attempted);
        ctx->streaming.decompressor =
            ngx_http_markdown_streaming_decomp_create_with_origin(
                r->pool, ctx->decompression.type,
                conf->decompress.max_size, brotli_workspace_bytes,
                brotli_workspace_limit, r->connection->log,
                &create_origin);
        if (ctx->streaming.decompressor == NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: failed to create decompressor");
            markdown_streaming_abort(ctx->streaming.handle);
            ctx->streaming.handle = NULL;
            create_error = create_origin
                == NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION
                ? ERROR_MEMORY_LIMIT : ERROR_INTERNAL;
            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, create_error);
        }
    }

    ctx->streaming.prebuffer_limit = ctx->effective_conf != NULL
        ? ctx->effective_conf->streaming_buffer
        : conf->limits.streaming_buffer;
    if (ctx->streaming.prebuffer_limit > 0) {
        rc = ngx_http_markdown_buffer_init(
            &ctx->streaming.prebuffer,
            ctx->streaming.prebuffer_limit, r->pool);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: prebuffer init failed; "
                "cannot guarantee fallback data integrity");
            markdown_streaming_abort(ctx->streaming.handle);
            ctx->streaming.handle = NULL;
            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, ERROR_MEMORY_LIMIT);
        }
        ctx->streaming.prebuffer_initialized = 1;
    } else {
        ctx->streaming.prebuffer_initialized = 0;
    }

    ctx->streaming.commit_state = NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE;
    ctx->streaming.failopen_replay_initialized = 0;
    if (ctx->streaming.prebuffer_limit > 0) {
        rc = ngx_http_markdown_buffer_init(
            &ctx->streaming.failopen_replay_buf,
            ctx->streaming.prebuffer_limit, r->pool);
        if (rc != NGX_OK) {
            NGX_HTTP_MARKDOWN_METRIC_INC(
                results.replay_buffer_errors_total);
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: replay buffer init failed; "
                "cannot guarantee fail-open data integrity");
            markdown_streaming_abort(ctx->streaming.handle);
            ctx->streaming.handle = NULL;
            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, ERROR_MEMORY_LIMIT);
        }
        ctx->streaming.failopen_replay_initialized = 1;
    }
    return NGX_OK;
}

/*
 * Initialize the Rust streaming handle and request-lifetime buffers.
 *
 * Returns NGX_OK on success, NGX_ERROR for a configured reject path, or
 * NGX_DECLINED when a pass policy has already switched the request to
 * fail-open handling.
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
    size_t                  prebuffer_limit;

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

    /*
     * Configuration parsing rejects zero, but keep a runtime guard for
     * dynamic or programmatic configuration paths.  Fail before Rust sees
     * any input so fail-open can still forward the untouched current chain.
     */
    prebuffer_limit = ctx->effective_conf != NULL
        ? ctx->effective_conf->streaming_buffer
        : conf->limits.streaming_buffer;
    if (prebuffer_limit == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: streaming precommit buffer is zero; "
            "refusing to consume input without recovery storage");
        return ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, ERROR_INTERNAL);
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

    rc = ngx_http_markdown_streaming_init_buffers(r, ctx, conf);
    if (rc != NGX_OK) {
        return rc;
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
        ngx_http_markdown_pending_output_set(
            &ctx->streaming.pending_output, out);
        ctx->streaming.pending_meta.has_data = 1;
        ctx->streaming.pending_meta.main_terminal = cap_main_terminal;
        ctx->streaming.pending_meta.subrequest_terminal =
            cap_subrequest_terminal;
        ctx->streaming.pending_meta.pending_abort_terminal = 0;
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
    ngx_int_t     rc;

    ctx->streaming.completion.failopen_active = 1;

    if (!ctx->headers_forwarded) {
        rc = ngx_http_markdown_forward_headers(r, ctx);
        if (rc != NGX_OK && rc != NGX_AGAIN) {
            return rc;
        }
        /* Header-chain NGX_AGAIN = headers queued by the write filter
         * (NGINX core model).  Continue so the fail-open replay body is
         * always delivered; returning early here sends headers only
         * under backpressure. */
    }

    if (!ctx->streaming.failopen_replay_initialized
        || ctx->streaming.failopen_replay_buf.size == 0)
    {
        ngx_chain_t  *cloned;

        cloned = ngx_http_markdown_streaming_clone_chain_links(r, in);
        if (cloned == NULL && in != NULL) {
            /* Uniform error classification (Rule 38): replay-buffer
             * allocation failure is a resource-limit failure, not a bare
             * internal error. */
            const ngx_http_markdown_conf_t  *conf;

            conf = ngx_http_get_module_loc_conf(
                r, ngx_http_markdown_filter_module);
            if (conf == NULL) {
                /* Location configuration unavailable: fail closed.
                 * In practice the request path always has a loc conf
                 * (allocated during configuration parsing); this guard
                 * keeps the resource-limit error path from dereferencing
                 * a NULL policy pointer. */
                return NGX_ERROR;
            }
            return ngx_http_markdown_streaming_precommit_error(
                r, ctx, conf, ERROR_MEMORY_LIMIT);
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
            return rc;
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
 * the streaming path, so re-entry starts at cl->next. A NULL cl
 * means finalization consumed the complete terminal chain. If
 * there is no successor, synthesize an empty terminal chain node
 * to preserve end-of-stream signaling.
 */
static ngx_int_t
ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
    ngx_http_request_t *r,
    ngx_chain_t *cl,
    ngx_flag_t last_buf)
{
    ngx_chain_t  *reentry_in;

    reentry_in = (cl != NULL) ? cl->next : NULL;
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
 * Finalize streaming and complete a capability fallback transition.
 *
 * A pre-commit capability fallback may first surface while Rust consumes
 * end-of-input.  At that point the upstream terminal chain has already been
 * consumed, so returning NGX_DECLINED would leave the restored full-buffer
 * payload without another body-filter callback.  Re-enter full-buffer
 * processing exactly once with a synthesized terminal signal.
 *
 * Returns:
 *   full-buffer body-filter status for a capability fallback
 *   finalize status for all other outcomes
 */
static ngx_int_t
ngx_http_markdown_streaming_finalize_and_dispatch_fallback(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    ngx_flag_t last_buf)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_streaming_finalize_request(r, ctx, conf);

    if (rc == NGX_DECLINED
        && ctx->eligible
        && ctx->processing_path == NGX_HTTP_MARKDOWN_PATH_FULLBUFFER)
    {
        return ngx_http_markdown_streaming_reenter_fullbuffer_after_fallback(
            r, NULL, last_buf);
    }

    return rc;
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
    ngx_http_markdown_log_streaming_terminal_decision(
        r, ctx, conf, NGX_HTTP_MARKDOWN_CONV_FAILED,
        "streaming_mid_flight_error", "postcommit");

    ngx_http_markdown_streaming_sync_buffered(r, ctx);
    return NGX_ERROR;
}


/*
 * Resume a header-only backpressure retry before body output.
 *
 * Finishes the header commit and then resumes pending header output.  Returns
 * NGX_OK when both resumed cleanly, otherwise propagates the delivery rc.
 * Extracted from ngx_http_markdown_streaming_handle_null_input() to bound
 * cognitive complexity (Rule 17).
 */
static ngx_int_t
ngx_http_markdown_streaming_null_input_resume_header(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_streaming_resume_header_commit(r, ctx);
    if (rc == NGX_AGAIN) {
        return rc;
    }
    if (!ngx_http_markdown_streaming_delivery_ok(rc)) {
        ngx_http_markdown_streaming_release_pending_header_output(ctx);
        ngx_http_markdown_streaming_release_finalize_pending(ctx);
        return rc;
    }

    rc = ngx_http_markdown_streaming_resume_pending_header_output(
        r, ctx, conf);
    if (rc == NGX_AGAIN || rc == NGX_ERROR) {
        return rc;
    }
    if (!ngx_http_markdown_streaming_delivery_ok(rc)) {
        ngx_http_markdown_streaming_release_finalize_pending(ctx);
        return rc;
    }

    /* A header NGX_AGAIN may also have deferred the finalize output on
     * the finalize path.  Send it now that the header retry succeeded,
     * then deliver the terminal last_buf.  The output-bytes accounting
     * already ran on the first pass (before the deferral), so this path
     * sends directly instead of re-running finalize_send_markdown. */
    if (ctx->streaming.completion.finalize_pending_result != NULL) {
        struct MarkdownResult  *pending;
        ngx_int_t               final_send_rc = NGX_OK;

        pending = ctx->streaming.completion.finalize_pending_result;
        ctx->streaming.completion.finalize_pending_result = NULL;
        rc = ngx_http_markdown_streaming_send_output(
            r, ctx, pending->markdown, pending->markdown_len,
            /* last_buf */ 0);
        if (rc != NGX_OK && rc != NGX_DONE && rc != NGX_AGAIN) {
            /* The deferred finalize result owns Rust-allocated buffers;
             * release it before the hard-abort path so a definitive
             * delivery error does not leak the finalize output. */
            markdown_result_free(pending);
            return ngx_http_markdown_streaming_handle_output_loss(
                r, ctx, conf);
        }
        final_send_rc = rc;
        ngx_http_markdown_streaming_record_finalize_stats(r, ctx, pending);
        return ngx_http_markdown_streaming_finish_terminal(
            r, ctx, conf, final_send_rc);
    }

    return NGX_OK;
}


/*
 * Terminate a fail-open request once pending input has drained.
 *
 * The caller has verified the fail-open terminal condition (fail-open active,
 * pending input empty, upstream terminal seen) and cleared the latch.  This
 * helper applies the request-type-aware terminal-delivered latch: main
 * requests check main_terminal_sent (last_buf delivered); subrequests check
 * subrequest_terminal_sent (last_in_chain delivered).  If the matching
 * terminal has already been confirmed downstream (e.g. via a backpressured
 * fail-open chain that carried last_in_chain in its tail), return without
 * synthesizing a duplicate terminal.  (Rule 47: the latch is only set after
 * confirmed delivery, never on NGX_AGAIN.)
 *
 * Extracted from ngx_http_markdown_streaming_handle_null_input() to bound
 * cognitive complexity (Rule 17).
 */
static ngx_int_t
ngx_http_markdown_streaming_null_input_failopen_terminal(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    ngx_int_t rc)
{
    if (ngx_http_markdown_streaming_terminal_sent_for_request(r, ctx)) {
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return rc;
    }
    return ngx_http_markdown_streaming_send_output(
        r, ctx, NULL, 0, /* last_buf */ 1);
}


/*
 * Process any enqueued pending input after downstream output has drained.
 *
 * Detaches the pending_input chain and feeds it through process_chain, which
 * re-enqueues any remainder that hits NGX_AGAIN back to pending_input.
 * Extracted from ngx_http_markdown_streaming_handle_null_input() to bound
 * cognitive complexity (Rule 17).
 */
static ngx_int_t
ngx_http_markdown_streaming_handle_null_input_pending(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t    rc;
    ngx_flag_t   last_buf;
    ngx_chain_t *fallback_cl;
    ngx_chain_t *input_chain;

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

    return NGX_OK;
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

    /* Step 1: Finish a header-only backpressure retry before body output. */
    rc = ngx_http_markdown_streaming_null_input_resume_header(r, ctx, conf);
    if (rc != NGX_OK) {
        return rc;
    }

    /* Step 2: Drain pending output (backpressure recovery) */
    rc = ngx_http_markdown_streaming_resume_pending(r, ctx, conf);
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
        return ngx_http_markdown_streaming_null_input_failopen_terminal(
            r, ctx, rc);
    }

    if (ctx->streaming.classify.input_disposition == NGX_HTTP_MD_INPUT_TERMINAL) {
        ctx->streaming.completion.upstream_terminal_seen = 0;
        ngx_http_markdown_streaming_pending_input_clear(ctx);
        ngx_http_markdown_streaming_sync_buffered(r, ctx);
        return rc;
    }

    /* Step 3: Process pending input if any */
    if (!ngx_http_markdown_streaming_pending_input_is_empty(ctx)) {
        rc = ngx_http_markdown_streaming_handle_null_input_pending(
            r, ctx, conf);
        if (rc != NGX_OK) {
            return rc;
        }
        /* rc == NGX_OK: all pending_input consumed */
    }

    /*
     * Step 4: Finalize if upstream terminal was seen and we are
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
        return ngx_http_markdown_streaming_finalize_and_dispatch_fallback(
            r, ctx, conf, 1);
    }

    /*
     * Step 5: Legacy finalize_after_pending path.
     *
     * This is set by finalize_request itself (finalize_decomp
     * NGX_AGAIN), not by process_chain.  When the finalize
     * path's own output hits backpressure, it sets this latch
     * so we re-enter finalize after the pending output drains.
     */
    if (ctx->streaming.completion.finalize_after_pending) {
        ctx->streaming.completion.finalize_after_pending = 0;
        return ngx_http_markdown_streaming_finalize_and_dispatch_fallback(
            r, ctx, conf, 1);
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
    rc = ngx_http_markdown_streaming_finalize_and_dispatch_fallback(
        r, ctx, conf, last_buf);

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

    if (ctx->streaming.pending_meta.pending_header_output != NULL) {
        uint32_t  enqueue_error = ERROR_BUDGET_EXCEEDED;

        rc = ngx_http_markdown_streaming_pending_input_enqueue_remainder(
            r, ctx, conf, in, &enqueue_error);
        if (rc == NGX_OK) {
            ngx_http_markdown_streaming_sync_buffered(r, ctx);
            return NGX_AGAIN;
        }

        ngx_http_markdown_streaming_release_pending_header_output(ctx);
        rc = ngx_http_markdown_streaming_precommit_error(
            r, ctx, conf, enqueue_error);
        if (rc == NGX_DECLINED && !ctx->eligible) {
            if (ctx->stream_sm.headers_pending) {
                /* The header block was already mutated and queued for
                 * the header retry.  Fail-open here would pair
                 * Markdown-contract headers with the original HTML
                 * body, a mismatched response.  Fail closed instead;
                 * the request terminates with an error. */
                return NGX_ERROR;
            }
            return ngx_http_markdown_streaming_failopen_passthrough(
                r, ctx, in);
        }
        return rc;
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

    if (ctx->streaming.pending_output != NULL
        || ctx->streaming.pending_meta.pending_header_output != NULL)
    {
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
