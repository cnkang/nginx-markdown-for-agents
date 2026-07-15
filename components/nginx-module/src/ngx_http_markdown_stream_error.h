/*
 * Streaming Fallback State Machine — Error Handler Integration
 *
 * Wires the unified markdown_error_policy configuration directive into the
 * streaming state machine.  This is the main entry point called by
 * the body filter when a streaming error occurs.  It uses the
 * decision engine to determine the correct action, then executes
 * that action using the replay, commit, and post-commit modules.
 *
 * The unified markdown_error_policy controls every pre-commit conversion
 * failure and selects the post-commit recovery strategy:
 *   pass          - replay original HTML before commit; after commit attempt
 *                   safe_finish and abort if safe_finish fails
 *   fail_closed   - finalize with 502 before commit; abort after commit
 *   status 429/503 - finalize with the configured status before commit;
 *                   abort after commit
 *
 * Post-commit handling never replays HTML or changes the HTTP status because
 * response headers are already on the wire.
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
 * Returns the result of the selected replay, request-finalization, safe-finish,
 * or abort operation. NGX_AGAIN preserves pending downstream output for
 * backpressure resume; NGX_ERROR reports invalid input or an unrecoverable
 * downstream/finalization failure.
 */
ngx_int_t
ngx_http_markdown_stream_on_error(ngx_http_request_t *r,
                                   ngx_http_markdown_ctx_t *ctx,
                                   const ngx_http_markdown_conf_t *conf);


#endif /* NGX_HTTP_MARKDOWN_STREAM_ERROR_H_INCLUDED_ */
