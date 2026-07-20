/*
 * Streaming Fallback State Machine — Header Commit Sequence
 *
 * Implements atomic header mutation for the streaming state machine.
 * All header decisions are completed before any outgoing headers are
 * mutated.  If any step fails before header send, no partial Markdown
 * response is committed.
 *
 * Header commit safety invariant: Header decisions MUST be
 * completed before outgoing headers are mutated.  Header mutation is
 * applied in a single final step after the decision is known.
 *
 * Rule 39: multi-step header atomicity — abort on first failure,
 * no partial apply.
 *
 * Two-phase design:
 *   Phase 1 (fallible): perform operations that can fail (Vary: Accept
 *     allocation, ETag removal, auth Cache-Control modification).
 *     If any step fails, headers are unmodified and fallback can safely
 *     serve the upstream response.
 *   Phase 2 (infallible): perform operations that cannot fail
 *     (Content-Type assignment, Content-Length/Content-Encoding
 *     removal).  These are pointer/integer writes that always succeed.
 *   Only after both phases complete are headers_committed and state
 *     updated.
 *
 * This is the single authoritative streaming header commit path.
 * All streaming code paths (streaming_impl, decision engine) must
 * call this function instead of ad-hoc header mutation.
 */

#ifndef NGX_HTTP_MARKDOWN_STREAM_COMMIT_H_INCLUDED_
#define NGX_HTTP_MARKDOWN_STREAM_COMMIT_H_INCLUDED_

#include "ngx_http_markdown_filter_module.h"


/*
 * Execute the streaming header commit sequence.
 *
 * Applies all header mutations for the streaming Markdown response in
 * a single atomic step:
 *   - Set Content-Type: text/markdown; charset=utf-8
 *   - Add Vary: Accept
 *   - Remove Content-Length (hash=0, pointer clear, content_length_n=-1)
 *   - Remove Content-Encoding when decompression was performed/needed
 *   - Remove upstream HTML ETag
 *   - Apply authenticated content Cache-Control protection (compile-time
 *     gated, same as full-buffer mode)
 *
 * Two-phase commit:
 *   Phase 1: fallible operations (Vary, ETag, auth Cache-Control).
 *     If any fails, NGX_ERROR is returned before headers are sent
 *     downstream.  Phase 1 mutations are performed on a snapshot and
 *     only applied to headers_out on Phase 2 success — no partial
 *     mutations are visible to the fallback path.
 *   Phase 2: infallible mutations (Content-Type, Content-Length,
 *     Content-Encoding).  These always succeed.
 *
 * On success, transitions stream_sm.state to COMMITTED and sets
 * stream_sm.headers_committed = 1.
 *
 * Parameters:
 *   r    - current HTTP request
 *   ctx  - request context with stream_sm sub-struct
 *   conf - location configuration (needed for auth Cache-Control)
 *
 * Returns:
 *   NGX_OK    - Headers committed, streaming proceeds
 *   NGX_ERROR - Commit failed, caller should fallback
 */
ngx_int_t
ngx_http_markdown_stream_commit_headers(ngx_http_request_t *r,
                                         ngx_http_markdown_ctx_t *ctx,
                                         const ngx_http_markdown_conf_t *conf);


#endif /* NGX_HTTP_MARKDOWN_STREAM_COMMIT_H_INCLUDED_ */
