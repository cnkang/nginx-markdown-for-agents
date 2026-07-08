#ifndef NGX_HTTP_MARKDOWN_ZEROCOPY_BUF_H
#define NGX_HTTP_MARKDOWN_ZEROCOPY_BUF_H

/*
 * Zero-copy buffer factory for streaming output.
 *
 * Provides a pool cleanup handler and buffer factory that allow
 * ngx_buf_t to reference Rust-owned memory directly, freed via
 * the existing markdown_streaming_output_free() FFI function on
 * pool destruction.
 *
 * WARNING: This header is an implementation detail of the streaming
 * body filter path.  It must NOT be included from any other .c file
 * or used as a standalone compilation unit.
 */

#ifdef MARKDOWN_STREAMING_ENABLED

/*
 * Cleanup context for a zero-copy buffer referencing Rust memory.
 *
 * Tracks the Rust-owned pointer, its length, and a flag to
 * prevent double-free when the buffer is explicitly freed
 * before pool destruction.
 */
typedef struct {
    u_char     *rust_ptr;
    size_t      rust_len;
    unsigned    freed:1;
} ngx_http_markdown_rust_buf_cleanup_t;


/*
 * Pool cleanup handler for Rust-owned zero-copy buffers.
 *
 * Checks the freed flag to prevent double-free, then calls
 * markdown_streaming_output_free() to deallocate the Rust buffer.
 * After deallocation, sets freed=1 and rust_ptr=NULL to mark the
 * cleanup as consumed.
 *
 * data - pointer to ngx_http_markdown_rust_buf_cleanup_t
 */
static void
ngx_http_markdown_rust_buf_cleanup(void *data)
{
    ngx_http_markdown_rust_buf_cleanup_t  *ctx = data;

    if (ctx == NULL || ctx->freed || ctx->rust_ptr == NULL) {
        return;
    }

    markdown_streaming_output_free(ctx->rust_ptr, ctx->rust_len);
    ctx->freed = 1;
    ctx->rust_ptr = NULL;
}


/*
 * Create a zero-copy ngx_buf_t referencing Rust-owned memory.
 *
 * Allocates an ngx_buf_t from the pool, registers a pool cleanup
 * handler that will call markdown_streaming_output_free() on pool
 * destruction, and returns the buffer with pos/last pointing
 * directly into Rust memory.
 *
 * On cleanup allocation failure, calls markdown_streaming_output_free
 * immediately to prevent a Rust memory leak and returns NULL.
 *
 * pool     - request pool for buffer and cleanup allocation
 * rust_ptr - pointer to Rust-allocated output bytes
 * rust_len - length of the Rust-allocated buffer
 *
 * Returns:
 *   non-NULL ngx_buf_t on success (memory=1, temporary=0)
 *   NULL on allocation failure (Rust buffer already freed)
 */
static ngx_buf_t *
ngx_http_markdown_rust_buf_create(ngx_pool_t *pool,
    u_char *rust_ptr, size_t rust_len)
{
    ngx_buf_t                             *b;
    ngx_pool_cleanup_t                    *cln;
    ngx_http_markdown_rust_buf_cleanup_t  *ctx;

    /*
     * Register pool cleanup BEFORE allocating the buffer.
     * This ensures the Rust memory is always freed even if
     * subsequent allocations fail.
     */
    cln = ngx_pool_cleanup_add(pool,
        sizeof(ngx_http_markdown_rust_buf_cleanup_t));
    if (cln == NULL) {
        /*
         * Cleanup allocation failed.  Free the Rust buffer
         * immediately to prevent a leak — the pool cannot
         * guarantee deallocation without a cleanup handler.
         */
        markdown_streaming_output_free(rust_ptr, rust_len);
        return NULL;
    }

    ctx = cln->data;
    ctx->rust_ptr = rust_ptr;
    ctx->rust_len = rust_len;
    ctx->freed = 0;
    cln->handler = ngx_http_markdown_rust_buf_cleanup;

    b = ngx_calloc_buf(pool);
    if (b == NULL) {
        /*
         * Buffer allocation failed.  The pool cleanup handler
         * is already registered and will free the Rust memory
         * on pool destruction — no explicit free needed here.
         */
        return NULL;
    }

    b->pos = rust_ptr;
    b->last = rust_ptr + rust_len;
    b->memory = 1;
    b->temporary = 0;

    return b;
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_ZEROCOPY_BUF_H */
