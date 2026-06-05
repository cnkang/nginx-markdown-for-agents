/*
 * Streaming Fallback State Machine — Error Handler Integration
 *
 * Wires the markdown_on_error configuration directive into the
 * streaming state machine.  This is the main entry point called by
 * the body filter when a streaming error occurs.  It uses the
 * decision engine to determine the correct action, then executes
 * that action using the replay, commit, and post-commit modules.
 *
 * Task 6.1: Pre-commit + pass = replay HTML
 * Task 6.2: Pre-commit + reject = 502
 * Task 6.3: Post-commit + pass = safe_finish/abort
 * Task 6.4: Post-commit + reject = safe_finish/abort
 */

#ifndef NGX_HTTP_MARKDOWN_STREAM_ERROR_H_INCLUDED_
#define NGX_HTTP_MARKDOWN_STREAM_ERROR_H_INCLUDED_

#include "ngx_http_markdown_filter_module.h"


/*
 * Handle a streaming error using the state machine and on_error policy.
 *
 * This function is the bridge between the body filter error path and
 * the streaming state machine.  It:
 *   1. Populates the decision context from request state
 *   2. Calls the decision engine with the appropriate error event
 *   3. Executes the resulting action
 *
 * Parameters:
 *   r      - current HTTP request
 *   ctx    - request context with stream_sm sub-struct
 *   conf   - module configuration (for on_error policy)
 *
 * Returns:
 *   NGX_OK             - Error handled (fallback or finish executed)
 *   NGX_HTTP_BAD_GATEWAY - 502 reject (task 6.2)
 *   NGX_ERROR          - Unrecoverable error
 */
ngx_int_t
ngx_http_markdown_stream_on_error(ngx_http_request_t *r,
                                   ngx_http_markdown_ctx_t *ctx,
                                   const ngx_http_markdown_conf_t *conf);


#endif /* NGX_HTTP_MARKDOWN_STREAM_ERROR_H_INCLUDED_ */
