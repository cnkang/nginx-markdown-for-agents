/*
 * Streaming Fallback State Machine — Post-commit Error Handler
 *
 * Implements the post-commit error handling paths for the streaming
 * state machine (Component 5 of streaming fallback state machine design).
 *
 * Critical safety property — post-commit irreversibility:
 *   After headers or Markdown bytes are sent (COMMITTED state),
 *   post-commit errors MUST NOT:
 *     - Revert to HTML
 *     - Append HTML
 *     - Mix Markdown/HTML
 *     - Send conflicting Content-Type
 *     - Return 502
 *
 *   The ONLY valid post-commit error responses are:
 *     - Safe-finish: graceful Markdown closure via Rust converter
 *     - Abort: protocol-safe disconnect
 *
 * Design constraint — Rust ownership boundary:
 *   C must NOT synthesize Markdown closure for Rust-owned
 *   parser/emitter state.  Safe-finish delegates to the Rust
 *   converter finish-mode API.
 */

#ifndef NGX_HTTP_MARKDOWN_STREAM_POSTCOMMIT_H_INCLUDED_
#define NGX_HTTP_MARKDOWN_STREAM_POSTCOMMIT_H_INCLUDED_

#include "ngx_http_markdown_filter_module.h"


/*
 * Request Rust finish-mode API to close known Markdown structures.
 *
 * Sends a "finish" signal to the Rust streaming converter so it emits
 * closing markers for any open lists, blockquotes, code blocks, etc.
 * The output is valid Markdown (just truncated).
 *
 * If Rust cannot guarantee safe-finish (e.g., converter handle is NULL
 * or in an error state), returns NGX_ERROR and the caller follows
 * the abort path instead.
 *
 * C must NOT synthesize Markdown closure for Rust-owned parser/emitter
 * state (Design document Component 5 constraint).
 *
 * Parameters:
 *   r   - current HTTP request
 *   ctx - request context with stream_sm sub-struct
 *
 * Returns:
 *   NGX_OK    - Safe-finish completed, response closed gracefully
 *   NGX_ERROR - Cannot safe-finish; caller should abort
 */
ngx_int_t
ngx_http_markdown_stream_postcommit_safe_finish(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);


/*
 * Protocol-safe abort.
 *
 * Terminates the streaming response chain safely:
 *   1. Transition to POST_COMMIT_ABORT state
 *   2. Send a last_buf=1 terminal chain to close the HTTP response
 *   3. Do NOT send any additional content bytes
 *
 * The downstream client receives a truncated but valid HTTP response
 * (Content-Length was removed at commit time, so truncation is valid
 * for chunked transfer encoding).
 *
 * Parameters:
 *   r   - current HTTP request
 *   ctx - request context with stream_sm sub-struct
 *
 * Returns:
 *   NGX_OK    - Abort completed, response terminated
 *   NGX_ERROR - Terminal chain send failed
 */
ngx_int_t
ngx_http_markdown_stream_postcommit_abort(
    ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);


/*
 * Guard function to verify no HTML is being sent post-commit.
 *
 * Called before any output in the body filter path when in committed
 * state.  Returns NGX_OK if the guard passes (no HTML detected),
 * NGX_ERROR if HTML content would be sent (triggers abort).
 *
 * This is the enforcement point for the post-commit irreversibility
 * invariant.
 *
 * Parameters:
 *   r     - current HTTP request
 *   ctx   - request context with stream_sm sub-struct
 *   chain - output chain to inspect
 *
 * Returns:
 *   NGX_OK    - Guard passes, output is safe
 *   NGX_ERROR - HTML detected, caller must abort
 */
ngx_int_t
ngx_http_markdown_stream_postcommit_guard(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    ngx_chain_t *chain);


/*
 * Log a post-commit event with structured fields.
 *
 * Emits a log entry with fields:
 *   phase=postcommit, action=(safe_finish|abort),
 *   committed=1, reason=<reason_code>
 *
 * Parameters:
 *   r      - current HTTP request
 *   ctx    - request context with stream_sm sub-struct
 *   action - action string ("safe_finish" or "abort")
 *   reason - reason code enum value
 */
void
ngx_http_markdown_stream_postcommit_log(
    ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    const char *action,
    ngx_http_markdown_reason_code_e reason);


#endif /* NGX_HTTP_MARKDOWN_STREAM_POSTCOMMIT_H_INCLUDED_ */
