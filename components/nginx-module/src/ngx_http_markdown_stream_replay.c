/*
 * Streaming Fallback State Machine — Replay Buffer Implementation
 *
 * Tracks bytes buffered during pre-commit streaming to enable HTML
 * passthrough fallback.  If replay buffer overflows before header
 * commit, the decision engine is notified via the replay_available
 * flag and forces a decision (commit, full-buffer, or reject).
 *
 * Rule 43: backing store uses ngx_alloc/ngx_free exclusively.
 * Rule 38: init/append failure semantics → precommit_error.
 */

#include "ngx_http_markdown_stream_replay.h"


/* Function prototypes */

static void
ngx_http_markdown_stream_replay_cleanup(void *data);

static size_t
ngx_http_markdown_stream_replay_grow(size_t current_capacity,
                                      size_t required,
                                      size_t max_capacity);


/*
 * Initialize the replay buffer for pre-commit fallback.
 *
 * Registers a pool cleanup so the ngx_alloc'd backing store is
 * automatically freed when the request ends, regardless of which
 * code path terminates the request.
 *
 * Returns:
 *   NGX_OK    - Buffer initialized, ready for append
 *   NGX_ERROR - Parameter validation or allocation failure
 */
ngx_int_t
ngx_http_markdown_stream_replay_init(ngx_http_markdown_ctx_t *ctx,
                                      ngx_pool_t *pool,
                                      size_t capacity)
{
    ngx_pool_cleanup_t  *cln;

    if (ctx == NULL || pool == NULL) {
        return NGX_ERROR;
    }

    /* Zero capacity means replay is intentionally disabled */
    if (capacity == 0) {
        ctx->stream_sm.replay_initialized = 0;
        return NGX_OK;
    }

    /* Register pool cleanup before allocation to guarantee cleanup */
    cln = ngx_pool_cleanup_add(pool, 0);
    if (cln == NULL) {
        return NGX_ERROR;
    }

    cln->handler = ngx_http_markdown_stream_replay_cleanup;
    cln->data = &ctx->stream_sm.replay_buf;

    /* Initialize buffer fields */
    ctx->stream_sm.replay_buf.data = NULL;
    ctx->stream_sm.replay_buf.size = 0;
    ctx->stream_sm.replay_buf.capacity = 0;
    ctx->stream_sm.replay_buf.max_size = capacity;
    ctx->stream_sm.replay_buf.pool = pool;

    ctx->stream_sm.replay_capacity = capacity;
    ctx->stream_sm.replay_initialized = 1;

    return NGX_OK;
}


/*
 * Compute new capacity for geometric growth of the replay buffer.
 *
 * Doubles from current_capacity until it can hold required bytes,
 * capped at max_capacity.  Returns the computed new capacity.
 */
static size_t
ngx_http_markdown_stream_replay_grow(size_t current_capacity,
                                      size_t required,
                                      size_t max_capacity)
{
    size_t  new_capacity;

    if (current_capacity == 0) {
        new_capacity = (required > 4096) ? required : 4096;
    } else {
        new_capacity = current_capacity;
        while (new_capacity < required) {
            if (new_capacity > max_capacity / 2) {
                new_capacity = max_capacity;
                break;
            }
            new_capacity *= 2;
        }
    }

    if (new_capacity > max_capacity) {
        new_capacity = max_capacity;
    }

    return new_capacity;
}


/*
 * Append bytes to the replay buffer.
 *
 * Checks capacity before copying.  If adding len bytes would exceed
 * the configured capacity, returns NGX_DECLINED without modifying
 * the buffer.  The caller (body filter) then raises
 * EVENT_REPLAY_OVERFLOW to the decision engine.
 *
 * The backing store grows lazily via ngx_alloc on first append and
 * doubles geometrically (capped at replay_capacity) to amortize
 * reallocation cost.
 *
 * Returns:
 *   NGX_OK       - Data appended
 *   NGX_DECLINED - Would overflow (no data written)
 *   NGX_ERROR    - Bad parameters or not initialized
 */
ngx_int_t
ngx_http_markdown_stream_replay_append(ngx_http_markdown_ctx_t *ctx,
                                        const u_char *data,
                                        size_t len)
{
    ngx_http_markdown_buffer_t  *buf;
    u_char                      *new_data;
    size_t                       new_capacity;
    size_t                       required;

    if (ctx == NULL) {
        return NGX_ERROR;
    }

    if (!ctx->stream_sm.replay_initialized) {
        return NGX_ERROR;
    }

    /* Zero-length append is a no-op success */
    if (len == 0) {
        return NGX_OK;
    }

    if (data == NULL) {
        return NGX_ERROR;
    }

    buf = &ctx->stream_sm.replay_buf;

    /*
     * Overflow check: if current size + len exceeds capacity, signal
     * the caller without modifying the buffer.  Use overflow-safe
     * comparison (Rule 32).
     */
    if (buf->size > ctx->stream_sm.replay_capacity
        || len > (ctx->stream_sm.replay_capacity - buf->size))
    {
        return NGX_DECLINED;
    }

    required = buf->size + len;

    /* Ensure backing store has room */
    if (required > buf->capacity) {
        new_capacity = ngx_http_markdown_stream_replay_grow(
            buf->capacity, required, ctx->stream_sm.replay_capacity);

        if (new_capacity < required) {
            return NGX_DECLINED;
        }

        new_data = ngx_alloc(new_capacity,
                             buf->pool != NULL ? buf->pool->log : NULL);
        if (new_data == NULL) {
            return NGX_ERROR;
        }

        if (buf->data != NULL) {
            if (buf->size > 0) {
                ngx_memcpy(new_data, buf->data, buf->size);
            }
            ngx_free(buf->data);
        }

        buf->data = new_data;
        buf->capacity = new_capacity;
    }

    /* Copy data into buffer */
    ngx_memcpy(buf->data + buf->size, data, len);
    buf->size += len;

    return NGX_OK;
}


/*
 * Check whether the replay buffer is available for HTML fallback.
 *
 * Returns 1 when the buffer is initialized and has not overflowed
 * its configured capacity.  This value feeds directly into the
 * decision engine context's replay_available field.
 */
ngx_flag_t
ngx_http_markdown_stream_replay_available(const ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL) {
        return 0;
    }

    if (!ctx->stream_sm.replay_initialized) {
        return 0;
    }

    if (ctx->stream_sm.replay_buf.size > ctx->stream_sm.replay_capacity) {
        return 0;
    }

    return 1;
}


/*
 * Build an output chain from the replay buffer for HTML passthrough.
 *
 * Allocates an ngx_chain_t and ngx_buf_t from the request pool,
 * then points the buffer at the replay data.  The buffer is marked
 * as memory-resident (not file-backed) and last_buf=1 so downstream
 * filters know this is the complete replayed response body.
 *
 * Returns:
 *   Non-NULL chain link on success
 *   NULL if allocation fails or replay buffer is empty
 */
ngx_chain_t *
ngx_http_markdown_stream_replay_chain(ngx_http_markdown_ctx_t *ctx,
                                       ngx_pool_t *pool)
{
    ngx_chain_t  *cl;
    ngx_buf_t    *b;

    if (ctx == NULL || pool == NULL) {
        return NULL;
    }

    if (!ctx->stream_sm.replay_initialized) {
        return NULL;
    }

    if (ctx->stream_sm.replay_buf.data == NULL
        || ctx->stream_sm.replay_buf.size == 0)
    {
        return NULL;
    }

    cl = ngx_alloc_chain_link(pool);
    if (cl == NULL) {
        return NULL;
    }

    b = ngx_calloc_buf(pool);
    if (b == NULL) {
        ngx_free_chain(pool, cl);
        return NULL;
    }

    b->pos = ctx->stream_sm.replay_buf.data;
    b->last = ctx->stream_sm.replay_buf.data + ctx->stream_sm.replay_buf.size;
    b->memory = 1;
    b->last_buf = 1;

    cl->buf = b;
    cl->next = NULL;

    return cl;
}


/*
 * Pool cleanup handler for the replay buffer backing store.
 *
 * Frees the ngx_alloc'd data and resets fields to prevent
 * use-after-free.
 */
static void
ngx_http_markdown_stream_replay_cleanup(void *data)
{
    ngx_http_markdown_buffer_t  *buf;

    buf = data;
    if (buf == NULL) {
        return;
    }

    if (buf->data != NULL) {
        ngx_free(buf->data);
        buf->data = NULL;
    }

    buf->size = 0;
    buf->capacity = 0;
}
