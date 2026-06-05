/*
 * Streaming Fallback State Machine — Header Commit Sequence
 *
 * Implements atomic header mutation for the streaming state machine.
 * All header decisions are completed before any outgoing headers are
 * mutated.  If any step fails before header send, no partial Markdown
 * response is committed.
 *
 * Design Property 3 (Requirement 6): Header decisions MUST be
 * completed before outgoing headers are mutated.  Header mutation is
 * applied in a single final step after the decision is known.
 *
 * Rule 39: multi-step header atomicity — abort on first failure,
 * no partial apply.
 */

#ifndef NGX_HTTP_MARKDOWN_STREAM_COMMIT_H_INCLUDED_
#define NGX_HTTP_MARKDOWN_STREAM_COMMIT_H_INCLUDED_

#include "ngx_http_markdown_filter_module.h"


/*
 * Execute the streaming header commit sequence.
 *
 * Evaluates eligibility, makes header decisions, and applies all
 * mutations in a single atomic step.  If any step fails, no partial
 * commit occurs and the function returns NGX_ERROR.
 *
 * On success, transitions stream_sm.state to COMMITTED and sets
 * stream_sm.headers_committed = 1.
 *
 * Parameters:
 *   r   - current HTTP request
 *   ctx - request context with stream_sm sub-struct
 *
 * Returns:
 *   NGX_OK    - Headers committed, streaming proceeds
 *   NGX_ERROR - Commit failed, caller should fallback
 */
ngx_int_t
ngx_http_markdown_stream_commit_headers(ngx_http_request_t *r,
                                         ngx_http_markdown_ctx_t *ctx);


#endif /* NGX_HTTP_MARKDOWN_STREAM_COMMIT_H_INCLUDED_ */
