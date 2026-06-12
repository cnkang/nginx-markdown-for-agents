/*
 * Streaming Fallback State Machine — Replay Buffer Interface
 *
 * Provides helpers for tracking bytes buffered during pre-commit
 * streaming.  The replay buffer enables HTML passthrough fallback by
 * retaining original upstream bytes until headers are committed.
 *
 * Rule 43: backing store uses ngx_alloc/ngx_free, never pool-allocate.
 * Rule 38: init/append failure → precommit_error semantics.
 */

#ifndef NGX_HTTP_MARKDOWN_STREAM_REPLAY_H_INCLUDED_
#define NGX_HTTP_MARKDOWN_STREAM_REPLAY_H_INCLUDED_

#include "ngx_http_markdown_filter_module.h"


/*
 * Initialize replay buffer from config precommit_buffer size.
 *
 * Allocates the backing store via ngx_alloc and registers a pool
 * cleanup handler so it is freed automatically when the request ends.
 *
 * Parameters:
 *   ctx      - Request context with stream_sm sub-struct
 *   pool     - Request pool (for cleanup registration and logging)
 *   capacity - Maximum replay buffer size in bytes
 *
 * Returns:
 *   NGX_OK    - Buffer initialized successfully
 *   NGX_ERROR - Allocation or cleanup registration failed
 */
ngx_int_t
ngx_http_markdown_stream_replay_init(ngx_http_markdown_ctx_t *ctx,
                                      ngx_pool_t *pool,
                                      size_t capacity);


/*
 * Append bytes to replay buffer, tracking usage against capacity.
 *
 * Parameters:
 *   ctx  - Request context with initialized stream_sm replay buffer
 *   data - Bytes to append
 *   len  - Number of bytes
 *
 * Returns:
 *   NGX_OK       - Data appended successfully
 *   NGX_DECLINED - Would overflow capacity (caller decides action)
 *   NGX_ERROR    - Invalid parameters or buffer not initialized
 */
ngx_int_t
ngx_http_markdown_stream_replay_append(ngx_http_markdown_ctx_t *ctx,
                                        const u_char *data,
                                        size_t len);


/*
 * Check whether the replay buffer is still available for fallback.
 *
 * Returns 1 (true) if:
 *   - replay_initialized is set
 *   - replay_buf.size <= replay_capacity (no overflow)
 *
 * Returns 0 (false) if:
 *   - Not initialized
 *   - Buffer has overflowed capacity
 *
 * Integrates with the decision engine's replay_available context field.
 */
ngx_flag_t
ngx_http_markdown_stream_replay_available(const ngx_http_markdown_ctx_t *ctx);


/*
 * Build an output chain from replay buffer contents for HTML passthrough.
 *
 * Creates an ngx_chain_t with a single buffer pointing to the replay
 * data.  Used when the decision engine returns PASS_HTML action during
 * pre-commit fallback.
 *
 * Parameters:
 *   ctx  - Request context with replay buffer data
 *   pool - Request pool for chain/buffer allocation
 *
 * Returns:
 *   Non-NULL chain on success
 *   NULL on allocation failure or empty replay buffer
 */
ngx_chain_t *
ngx_http_markdown_stream_replay_chain(
    const ngx_http_markdown_ctx_t *ctx, ngx_pool_t *pool);


#endif /* NGX_HTTP_MARKDOWN_STREAM_REPLAY_H_INCLUDED_ */
