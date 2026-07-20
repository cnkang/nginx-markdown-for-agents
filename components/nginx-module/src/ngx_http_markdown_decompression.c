/*
 * NGINX Markdown Filter Module - Decompression Functions
 *
 * This file implements automatic decompression of upstream compressed content
 * (gzip, deflate, brotli) to enable HTML-to-Markdown conversion.
 *
 * Architecture: Uses nginx's standard dependencies (zlib, brotli) directly
 * for decompression, providing a fully automatic "technical fallback" solution
 * when upstream servers force compression.
 */

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>
#include <limits.h>
#include <zlib.h>

/* Conditionally include brotli header if support is compiled in */
#ifdef NGX_HTTP_BROTLI
#include <brotli/decode.h>
#endif

#include "ngx_http_markdown_filter_module.h"

static u_char ngx_http_markdown_encoding_gzip[] = "gzip";
static u_char ngx_http_markdown_encoding_deflate[] = "deflate";
static u_char ngx_http_markdown_encoding_br[] = "br";

/*
 * Detect compression type from Content-Encoding header
 *
 * This function examines the Content-Encoding response header and returns
 * the appropriate compression type enum value. The detection is case-insensitive
 * and handles empty/null values gracefully.
 *
 * Parameters:
 *   r - nginx request structure
 *
 * Returns:
 *   NGX_HTTP_MARKDOWN_COMPRESSION_NONE     - No Content-Encoding header or empty value
 *   NGX_HTTP_MARKDOWN_COMPRESSION_GZIP     - gzip compression detected
 *   NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE  - deflate compression detected
 *   NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI   - brotli compression detected
 *   NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN  - Unknown/unsupported compression format
 *
 * Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6
 */
ngx_http_markdown_compression_type_e
ngx_http_markdown_detect_compression(ngx_http_request_t *r)
{
    ngx_table_elt_t  *h;
    
    /* Get Content-Encoding header from response headers */
    h = r->headers_out.content_encoding;
    
    /* Handle missing or empty Content-Encoding header (empty or missing Content-Encoding) */
    if (h == NULL || h->value.len == 0) {
        return NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
    }
    
    /* Check for gzip compression (case-insensitive, gzip compression detection) */
    if (h->value.len == sizeof("gzip") - 1
        && ngx_strncasecmp(h->value.data,
                            ngx_http_markdown_encoding_gzip,
                            sizeof("gzip") - 1) == 0)
    {
        return NGX_HTTP_MARKDOWN_COMPRESSION_GZIP;
    }
    
    /* Check for deflate compression (case-insensitive, deflate compression detection) */
    if (h->value.len == sizeof("deflate") - 1
        && ngx_strncasecmp(h->value.data,
                            ngx_http_markdown_encoding_deflate,
                            sizeof("deflate") - 1) == 0)
    {
        return NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE;
    }
    
    /* Check for brotli compression (case-insensitive, brotli compression detection) */
    if (h->value.len == sizeof("br") - 1
        && ngx_strncasecmp(h->value.data,
                            ngx_http_markdown_encoding_br,
                            sizeof("br") - 1) == 0)
    {
        return NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI;
    }
    
    /* Unknown or unsupported compression format (unknown or unsupported compression format) */
    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown: decompression unsupported, compression=%V, "
                 "returning original content",
                 &h->value);
    
    return NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN;
}

/*
 * Helper function: Calculate total size of chain buffers
 *
 * Iterates through a chain of buffers and sums their sizes.
 *
 * Parameters:
 *   in - Input chain
 *
 * Returns:
 *   Total size in bytes
 */
static size_t
ngx_http_markdown_chain_size(const ngx_chain_t *in)
{
    size_t  size;
    size_t  len;
    
    size = 0;
    
    for (const ngx_chain_t *cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf != NULL) {
            len = ngx_http_markdown_buf_len_safe(cl->buf);
            if (len > ((size_t) -1) - size) {
                return (size_t) -1;
            }
            size += len;
        }
    }
    
    return size;
}

/*
 * Helper function: Copy chain data to a single buffer
 *
 * Collects all data from a chain of buffers into a single contiguous buffer.
 *
 * Parameters:
 *   in     - Input chain
 *   dest   - Destination buffer (must be pre-allocated)
 *   size   - Size of destination buffer
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_chain_to_buffer(const ngx_chain_t *in, u_char *dest,
                                  size_t size)
{
    size_t  copied;
    size_t  len;
    
    copied = 0;
    
    for (const ngx_chain_t *cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL) {
            continue;
        }
        
        len = ngx_http_markdown_buf_len_safe(cl->buf);
        
        if (copied > size || len > size - copied) {
            return NGX_ERROR;
        }

        if (len == 0) {
            continue;
        }
        
        ngx_memcpy(dest + copied, cl->buf->pos, len);
        copied += len;
    }
    
    return NGX_OK;
}

/*
 * Estimate a safe decompression output buffer size.
 *
 * Strategy:
 *   - Start with a heuristic expansion factor (input * 10)
 *   - Cap at configured decompress_max_size (independent from max_size)
 *   - Clamp to UINT_MAX for decoder APIs that use unsigned-int counters
 *
 * This keeps allocation bounded while still allowing common compressed
 * HTML payloads to inflate in a single pass.
 */
static ngx_int_t
ngx_http_markdown_calc_output_size(ngx_http_request_t *r, size_t input_size,
                                   size_t decompress_max_size, size_t *output_size)
{
    size_t estimated;

    if (decompress_max_size == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: invalid decompress_max_size=0 for decompression");
        return NGX_ERROR;
    }

    /* Guard multiplication overflow before applying 10x heuristic. */
    if (input_size > ((size_t) -1) / 10) {
        estimated = decompress_max_size;
    } else {
        estimated = input_size * 10;
    }

    if (estimated > decompress_max_size) {
        estimated = decompress_max_size;
    }

    /*
     * zlib/brotli decoder output counters use unsigned int/size_t combinations.
     * Clamp to UINT_MAX to avoid truncation when assigning `avail_out`.
     */
    if (estimated > (size_t) UINT_MAX) {
        estimated = (size_t) UINT_MAX;
    }

    /* Warn when the estimated decompression buffer is unusually large. */
    if (estimated > 50 * 1024 * 1024) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: large decompression buffer estimated=%uz "
                     "from input_size=%uz (ratio=%uz:1), capped by decompress_max_size=%uz",
                     estimated, input_size,
                     (input_size > 0) ? estimated / input_size : 0,
                     decompress_max_size);
    }

    if (estimated == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: computed decompression buffer size is zero");
        return NGX_ERROR;
    }

    *output_size = estimated;
    return NGX_OK;
}


/*
 * Grow the decompression output buffer up to decompress.max_size.
 *
 * Codec-agnostic buffer growth: computes a new size (double current used,
 * minimum +4096, capped at budget and UINT_MAX), allocates from the pool,
 * and copies existing data. The caller is responsible for updating any
 * codec-specific pointers (z_stream, brotli next_out, etc.) after this
 * function returns.
 *
 * Parameters:
 *   r           - nginx request structure (for pool allocation and logging)
 *   conf        - module configuration (provides decompress.max_size)
 *   output_data - pointer to current output buffer pointer (updated on success)
 *   output_size - pointer to current output buffer size (updated on success)
 *   used        - number of bytes already written to the buffer
 *
 * Returns:
 *   NGX_OK on successful reallocation
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED if budget would be exceeded
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_grow_output_buffer(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    u_char **output_data, size_t *output_size, size_t used)
{
    size_t   new_size;
    u_char  *new_data;

    if (used >= conf->decompress.max_size) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size exceeds "
                     "decompression budget (%uz), "
                     "category=resource_limit",
                     conf->decompress.max_size);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    if (used > ((size_t) -1) / 2) {
        new_size = conf->decompress.max_size;
    } else {
        new_size = used * 2;
    }
    if (new_size < used + 4096) {
        new_size = used + 4096;
    }
    if (new_size > conf->decompress.max_size) {
        new_size = conf->decompress.max_size;
    }
    if (new_size > (size_t) UINT_MAX) {
        new_size = (size_t) UINT_MAX;
    }
    if (new_size <= used) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size exceeds "
                     "decompression budget (%uz), "
                     "category=resource_limit",
                     conf->decompress.max_size);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    /* Mark the old pool buffer as reusable via ngx_pfree so subsequent
     * pnalloc calls can reclaim the space.  The memory is not returned to
     * the OS until the request pool is destroyed; this is intentional —
     * the output buffer is pool-owned and follows pool lifetime. */
    new_data = ngx_pnalloc(r->pool, new_size);
    if (new_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to reallocate decompression "
                     "buffer (pnalloc), size=%uz, category=system",
                     new_size);
        return NGX_ERROR;
    }
    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                "markdown: decompression buffer realloc "
                "used=%uz new_size=%uz (old buffer marked reusable)",
                used, new_size);
    ngx_memcpy(new_data, *output_data, used);
    ngx_pfree(r->pool, *output_data);
    *output_data = new_data;
    *output_size = new_size;

    return NGX_OK;
}


/*
 * Grow the decompression output buffer up to decompress.max_size (zlib).
 *
 * Thin wrapper around ngx_http_markdown_grow_output_buffer that also
 * updates the zlib stream's next_out and avail_out pointers.
 *
 * Parameters:
 *   r           - nginx request structure (for pool allocation and logging)
 *   conf        - module configuration (provides decompress.max_size)
 *   output_data - pointer to current output buffer pointer (updated on success)
 *   output_size - pointer to current output buffer size (updated on success)
 *   stream      - zlib stream (next_out and avail_out updated on success)
 *   completed_out - output bytes from completed gzip members
 *
 * Returns:
 *   NGX_OK on successful reallocation
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED if budget would be exceeded
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_grow_decomp_buffer(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    u_char **output_data, size_t *output_size,
    z_stream *stream, size_t completed_out)
{
    size_t     used;
    ngx_int_t  rc;

    if (completed_out > conf->decompress.max_size
        || stream->total_out
           > (uLong) (conf->decompress.max_size - completed_out))
    {
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    used = completed_out + (size_t) stream->total_out;
    rc = ngx_http_markdown_grow_output_buffer(r, conf, output_data,
                                             output_size, used);
    if (rc != NGX_OK) {
        return rc;
    }

    stream->next_out = *output_data + used;
    stream->avail_out = (uInt) (*output_size - used);

    return NGX_OK;
}


typedef struct {
    ngx_http_request_t                    *request;
    const ngx_http_markdown_conf_t        *conf;
    z_stream                              *stream;
    u_char                               **output_data;
    size_t                                *output_size;
    ngx_http_markdown_compression_type_e   type;
    size_t                                 completed_out;
    u_char                                *overflow_probe;
} ngx_http_markdown_inflate_ctx_t;


/*
 * Handle a zlib "no progress" condition (avail_out or avail_in exhausted).
 *
 * Called from the inflate loop when inflate() returns Z_BUF_ERROR
 * without reaching Z_STREAM_END, or when inflate() returns Z_OK but
 * avail_out is 0. Checks output buffer exhaustion first (grow), then
 * input exhaustion (truncated), then reports an unexpected stall.
 *
 * Parameters:
 *   ctx           - inflate state and request-owned output buffer
 *   stall_code    - error code to return on unexpected stall
 *   context_label - label for log messages ("Z_OK" or "Z_BUF_ERROR")
 *
 * Returns:
 *   NGX_AGAIN if buffer was grown (caller should continue the loop)
 *   NGX_OK is never returned
 *   Any other value is a terminal error code for the caller to return
 */
static ngx_int_t
ngx_http_markdown_handle_inflate_stall(
    ngx_http_markdown_inflate_ctx_t *ctx, ngx_int_t stall_code,
    const char *context_label)
{
    ngx_int_t  grow_rc;

    if (ctx->stream->avail_out == 0) {
        grow_rc = ngx_http_markdown_grow_decomp_buffer(
            ctx->request, ctx->conf, ctx->output_data, ctx->output_size,
            ctx->stream, ctx->completed_out);
        if (grow_rc != NGX_OK) {
            return grow_rc;
        }
        return NGX_AGAIN;
    }

    if (ctx->stream->avail_in == 0) {
        ngx_log_error(NGX_LOG_ERR, ctx->request->connection->log, 0,
                     "markdown: decompression failed, "
                     "truncated input (%s with no remaining "
                     "input), category=conversion",
                     context_label);
        return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
    }

    ngx_log_error(NGX_LOG_ERR, ctx->request->connection->log, 0,
                 "markdown: decompression failed, "
                 "%s with avail_in=%d avail_out=%d, "
                 "category=conversion",
                 context_label, ctx->stream->avail_in,
                 ctx->stream->avail_out);
    return stall_code;
}

static ngx_int_t
ngx_http_markdown_reset_gzip_member(ngx_http_request_t *r,
    z_stream *stream, u_char *output_data, size_t output_size,
    size_t completed_out, u_char *overflow_probe)
{
    Bytef  *next_in;
    size_t  remaining_out;
    uInt    avail_in;
    int     zrc;

    next_in = stream->next_in;
    avail_in = stream->avail_in;
    zrc = inflateReset(stream);
    if (zrc != Z_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompression failed, "
                     "inflateReset error: %d, category=conversion", zrc);
        return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
    }

    stream->next_in = next_in;
    stream->avail_in = avail_in;

    if (completed_out < output_size) {
        remaining_out = output_size - completed_out;
        if (remaining_out > (size_t) UINT_MAX) {
            return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
        }
        stream->next_out = output_data + completed_out;
        stream->avail_out = (uInt) remaining_out;
    } else {
        stream->next_out = overflow_probe;
        stream->avail_out = 1;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_complete_inflate_member(
    ngx_http_markdown_inflate_ctx_t *ctx, size_t *total_out)
{
    ngx_int_t  rc;

    if (ctx->completed_out > ctx->conf->decompress.max_size
        || ctx->stream->total_out
           > (uLong) (ctx->conf->decompress.max_size
                      - ctx->completed_out))
    {
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    ctx->completed_out += (size_t) ctx->stream->total_out;

    /*
     * Deflate (zlib-wrapped or raw) does not support concatenated members.
     * A complete deflate stream must consume every byte of the compressed
     * payload; any remaining avail_in after Z_STREAM_END is trailing data
     * that does not belong to the stream.  Silently accepting it would let
     * an illegal Content-Encoding: deflate response be truncated and treated
     * as a successful conversion.  Gzip is exempt because it supports
     * concatenated members.
     */
    if (ctx->type != NGX_HTTP_MARKDOWN_COMPRESSION_GZIP) {
        if (ctx->stream->avail_in > 0) {
            ngx_log_error(NGX_LOG_ERR, ctx->request->connection->log, 0,
                         "markdown: decompression failed, "
                         "deflate stream ended with %d trailing bytes "
                         "(avail_in > 0 after Z_STREAM_END), "
                         "category=conversion",
                         ctx->stream->avail_in);
            return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
        }
        *total_out = ctx->completed_out;
        return NGX_OK;
    }

    /* Gzip: a complete member with no remaining input finishes the response. */
    if (ctx->stream->avail_in == 0) {
        *total_out = ctx->completed_out;
        return NGX_OK;
    }

    if (ctx->completed_out == *ctx->output_size
        && ctx->completed_out < ctx->conf->decompress.max_size)
    {
        rc = ngx_http_markdown_grow_output_buffer(
            ctx->request, ctx->conf, ctx->output_data, ctx->output_size,
            ctx->completed_out);
        if (rc != NGX_OK) {
            return rc;
        }
    }

    rc = ngx_http_markdown_reset_gzip_member(
        ctx->request, ctx->stream, *ctx->output_data, *ctx->output_size,
        ctx->completed_out, ctx->overflow_probe);
    if (rc != NGX_OK) {
        return rc;
    }

    return NGX_AGAIN;
}


static ngx_int_t
ngx_http_markdown_handle_inflate_result(
    ngx_http_markdown_inflate_ctx_t *ctx, int zrc, size_t *total_out)
{
    if (zrc == Z_STREAM_END) {
        return ngx_http_markdown_complete_inflate_member(
            ctx, total_out);
    }

    if (zrc == Z_OK && ctx->stream->avail_out > 0) {
        return NGX_AGAIN;
    }

    if (zrc == Z_OK) {
        return ngx_http_markdown_handle_inflate_stall(
            ctx, NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR, "Z_OK");
    }

    if (zrc == Z_BUF_ERROR) {
        return ngx_http_markdown_handle_inflate_stall(
            ctx, NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR, "Z_BUF_ERROR");
    }

    if (zrc == Z_DATA_ERROR) {
        ngx_log_error(NGX_LOG_ERR, ctx->request->connection->log, 0,
                     "markdown: decompression failed, "
                     "inflate format error (Z_DATA_ERROR), "
                     "category=conversion");
        return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
    }

    ngx_log_error(NGX_LOG_ERR, ctx->request->connection->log, 0,
                 "markdown: decompression failed, "
                 "inflate error: %d, category=conversion", zrc);
    return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
}


/*
 * Run the zlib inflate loop until Z_STREAM_END or error.
 *
 * Handles buffer growth on avail_out exhaustion (Z_OK with avail_out == 0
 * or Z_BUF_ERROR) by calling ngx_http_markdown_grow_decomp_buffer.
 * Prioritizes output buffer growth over truncated-input classification
 * to avoid misdiagnosing streams where zlib needs one more call after
 * consuming all input bytes.
 *
 * Parameters:
 *   r           - nginx request structure
 *   conf        - module configuration (provides decompress.max_size)
 *   stream      - initialized zlib stream (modified in place)
 *   output_data - pointer to output buffer pointer (may be reallocated)
 *   output_size - pointer to output buffer size (updated on realloc)
 *   type        - content coding; gzip permits concatenated members
 *   total_out   - response-wide decompressed byte count on success
 *
 * Returns:
 *   NGX_OK on Z_STREAM_END (success)
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED if budget exceeded
 *   NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT if input is incomplete
 *   NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR on Z_DATA_ERROR or unexpected state
 *   NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR on other zlib errors
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_inflate_loop(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, z_stream *stream,
    u_char **output_data, size_t *output_size,
    ngx_http_markdown_compression_type_e type, size_t *total_out)
{
    int                              zrc;
    ngx_int_t                        rc;
    ngx_http_markdown_inflate_ctx_t  ctx;

    ctx.request = r;
    ctx.conf = conf;
    ctx.stream = stream;
    ctx.output_data = output_data;
    ctx.output_size = output_size;
    ctx.type = type;
    ctx.completed_out = 0;

    /*
     * Pool-allocate the overflow probe byte so its address is heap-resident.
     * Storing a stack address into stream->next_out (non-local memory) would
     * trigger CodeQL "local variable address stored in non-local memory".
     */
    ctx.overflow_probe = ngx_pnalloc(r->pool, 1);
    if (ctx.overflow_probe == NULL) {
        return NGX_ERROR;
    }
    *ctx.overflow_probe = 0;

    for ( ;; ) {
        zrc = inflate(stream, Z_NO_FLUSH);
        rc = ngx_http_markdown_handle_inflate_result(
            &ctx, zrc, total_out);
        if (rc != NGX_AGAIN) {
            return rc;
        }
    }
}


/*
 * Decompress gzip/deflate compressed data using zlib
 *
 * Despite the "gzip" in the function name, this function handles both
 * gzip (Content-Encoding: gzip) and deflate (Content-Encoding: deflate)
 * formats via the `type` parameter.  The name is a historical artifact
 * from when only gzip was supported.
 *
 * This function implements automatic decompression of gzip and deflate
 * compressed content using nginx's standard zlib dependency. It provides
 * a fully automatic "technical fallback" solution when upstream servers
 * force compression.
 *
 * The function:
 * 1. Collects all input data from the chain into a single buffer
 * 2. Initializes zlib stream with appropriate windowBits
 *    - MAX_WBITS + 16 for gzip format
 *    - MAX_WBITS for deflate format
 * 3. Estimates output size (typically input_size * 10)
 * 4. Allocates output buffer using nginx memory pool
 * 5. Performs incremental decompression using inflate(..., Z_NO_FLUSH)
 *    via ngx_http_markdown_inflate_loop()
 * 6. Grows the output buffer up to decompress.max_size when avail_out
 *    is exhausted
 * 7. Returns classified decompression errors for budget, truncation,
 *    format, and I/O failures
 * 8. Creates output chain with decompressed data
 * 9. Cleans up with inflateEnd()
 *
 * Parameters:
 *   r    - nginx request structure
 *   type - compression type (GZIP or DEFLATE)
 *   in   - input chain with compressed data
 *   out  - output chain with decompressed data (output parameter)
 *
 * Returns:
 *   NGX_OK on success
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED if decompressed size exceeds budget
 *   NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT if input stream is incomplete
 *   NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR on invalid compressed data
 *   NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR on unexpected zlib errors
 *   NGX_ERROR on system failures (allocation, initialization)
 *
 * Requirements: 2.1, 2.2, 2.3, 9.1, 9.2, 13.1, 13.5, 14.1, 14.2
 */


/*
 * Cleanup callback signature for ngx_http_markdown_decomp_alloc_output.
 *
 * Called when the output allocation fails, so the caller can release the
 * backend-specific decoder state (inflateEnd for zlib, destroy-instance
 * for brotli) before the helper logs and returns NGX_ERROR.
 */
typedef void (*ngx_http_markdown_decomp_cleanup_t)(void *decoder_state);


/*
 * Allocate the decompression output buffer from r->pool, logging and
 * cleaning up decoder state on failure.
 *
 * Shared by the zlib (gzip/deflate) and brotli decompression paths so the
 * two backends cannot drift apart on the alloc-failure error path. The
 * caller provides a backend-specific cleanup callback invoked only on
 * failure (the caller retains ownership of decoder_state on success).
 *
 * Parameters:
 *   r             - nginx request (logging + pool)
 *   output_size   - capacity to allocate
 *   cleanup       - decoder cleanup callback (NULL if no cleanup needed)
 *   decoder_state - opaque pointer forwarded to cleanup (e.g. z_stream*
 *                   or BrotliDecoderState*)
 *
 * Returns:
 *   non-NULL u_char* on success, NULL on failure (error logged + cleanup
 *   invoked).
 */
static u_char *
ngx_http_markdown_decomp_alloc_output(ngx_http_request_t *r,
    size_t output_size, ngx_http_markdown_decomp_cleanup_t cleanup,
    void *decoder_state)
{
    u_char  *output_data;

    output_data = ngx_pnalloc(r->pool, output_size);
    if (output_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate decompression buffer, "
                     "size=%uz, category=system",
                     output_size);
        if (cleanup != NULL) {
            cleanup(decoder_state);
        }
        return NULL;
    }

    return output_data;
}


/* zlib cleanup adapter: forwards to inflateEnd via the z_stream pointer. */
static void
ngx_http_markdown_decomp_zlib_cleanup(void *decoder_state)
{
    inflateEnd((z_stream *) decoder_state);
}


#ifdef NGX_HTTP_BROTLI
/* brotli cleanup adapter: forwards to BrotliDecoderDestroyInstance. */
static void
ngx_http_markdown_decomp_brotli_cleanup(void *decoder_state)
{
    BrotliDecoderDestroyInstance((BrotliDecoderState *) decoder_state);
}
#endif


/*
 * Validate decompression input size and collect the input chain into a
 * single contiguous buffer allocated from the request pool.
 *
 * Shared by the zlib (gzip/deflate) and brotli decompression paths so the
 * two functions cannot drift apart on size validation or buffer setup.
 *
 * Parameters:
 *   r           - nginx request
 *   in          - input chain with compressed bytes
 *   input_data  - on success, points to the pool-allocated buffer holding
 *                 a copy of the input chain contents
 *   input_size  - on success, the total size of the collected input
 *
 * Returns:
 *   NGX_OK on success (input_data/input_size populated)
 *   NGX_ERROR if the input chain is empty, oversized, or cannot be copied
 */
static ngx_int_t
ngx_http_markdown_decomp_collect_input(ngx_http_request_t *r,
    const ngx_chain_t *in, u_char **input_data, size_t *input_size)
{
    size_t   sz;

    sz = ngx_http_markdown_chain_size(in);

    if (sz == 0 || sz == (size_t) -1) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompression failed, "
                     "invalid input size, category=conversion");
        return NGX_ERROR;
    }

    *input_data = ngx_pnalloc(r->pool, sz);
    if (*input_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate input buffer, "
                     "size=%uz, category=system",
                     sz);
        return NGX_ERROR;
    }

    if (ngx_http_markdown_chain_to_buffer(in, *input_data, sz) != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to collect input data, "
                     "category=system");
        return NGX_ERROR;
    }

    *input_size = sz;
    return NGX_OK;
}


/*
 * Build the output chain wrapping a pool-allocated decompressed buffer.
 *
 * Shared by the zlib (gzip/deflate) and brotli decompression paths so the
 * buffer setup + chain link construction cannot drift apart.
 *
 * Parameters:
 *   r            - nginx request
 *   output_data  - start of the decompressed output buffer
 *   output_size  - total capacity of output_data (b->end = output_data + output_size)
 *   used         - actual decompressed length (b->last = output_data + used)
 *   last_buf     - 1 to set b->last_buf (main request terminal), 0 otherwise
 *   out          - on success, set to a newly allocated chain link wrapping the buffer
 *
 * Returns:
 *   NGX_OK on success (*out populated)
 *   NGX_ERROR on allocation failure (buf or chain link)
 */
static ngx_int_t
ngx_http_markdown_decomp_build_output_chain(ngx_http_request_t *r,
    u_char *output_data, size_t output_size, size_t used, u_char last_buf,
    ngx_chain_t **out)
{
    ngx_buf_t    *b;
    ngx_chain_t  *cl;

    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to create output buffer, "
                     "category=system");
        return NGX_ERROR;
    }

    b->pos = output_data;
    b->last = output_data + used;
    b->start = output_data;
    b->end = output_data + output_size;
    b->temporary = 1;
    b->last_buf = last_buf;

    cl = ngx_alloc_chain_link(r->pool);
    if (cl == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate chain link, "
                     "category=system");
        return NGX_ERROR;
    }

    cl->buf = b;
    cl->next = NULL;
    *out = cl;

    return NGX_OK;
}


ngx_int_t
ngx_http_markdown_decompress_gzip(ngx_http_request_t *r,
                                   ngx_http_markdown_compression_type_e type,
                                   const ngx_chain_t *in,
                                   ngx_chain_t **out)
{
    z_stream                           stream;
    u_char                            *input_data;
    size_t                             input_size;
    u_char                            *output_data;
    size_t                             output_size;
    size_t                             total_decompressed;
    ngx_int_t                          loop_rc;
    int                                zrc;
    int                                window_bits;
    const ngx_http_markdown_conf_t    *conf;
    
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    
    /* Log that we're using zlib for decompression (zlib decompression path) */
    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: using zlib for gzip/deflate decompression, type=%d",
                  type);
    
    /* Collect all input data into a single buffer and validate its size
     * (shared with the brotli path via ngx_http_markdown_decomp_collect_input
     * so the two decompression backends cannot drift apart on size checks). */
    if (ngx_http_markdown_decomp_collect_input(r, in, &input_data,
                                               &input_size) != NGX_OK)
    {
        return NGX_ERROR;
    }

    if (input_size > (size_t) UINT_MAX) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: compressed input too large for zlib "
                     "decoder counters, size=%uz", input_size);
        return NGX_ERROR;
    }
    
    /* Initialize zlib stream */
    ngx_memzero(&stream, sizeof(z_stream));
    stream.next_in = input_data;
    stream.avail_in = (uInt) input_size;
    
    /* Set windowBits based on compression type (windowBits selection based on compression type) */
    if (type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP) {
        /* MAX_WBITS + 16 for gzip format */
        window_bits = MAX_WBITS + 16;
    } else {
        /* MAX_WBITS for deflate format */
        window_bits = MAX_WBITS;
    }
    
    zrc = inflateInit2(&stream, window_bits);
    if (zrc != Z_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompression failed, "
                     "inflateInit2 error: %d, category=conversion", zrc);
        return NGX_ERROR;
    }
    
    /* Estimate output size with independent decompression budget. */
    if (ngx_http_markdown_calc_output_size(r, input_size, conf->decompress.max_size, &output_size)
        != NGX_OK)
    {
        inflateEnd(&stream);
        return NGX_ERROR;
    }
    
    /* Allocate output buffer using nginx memory pool (output buffer allocation from nginx pool) */
    output_data = ngx_http_markdown_decomp_alloc_output(r, output_size,
        ngx_http_markdown_decomp_zlib_cleanup, &stream);
    if (output_data == NULL) {
        return NGX_ERROR;
    }
    
    stream.next_out = output_data;
    stream.avail_out = (uInt) output_size;
    
    /* Run the inflate loop (extracted for complexity reduction). */
    loop_rc = ngx_http_markdown_inflate_loop(r, conf, &stream,
                                             &output_data, &output_size,
                                             type, &total_decompressed);
    if (loop_rc != NGX_OK) {
        /*
         * Fallback: if deflate decompression fails with FORMAT_ERROR,
         * retry with raw deflate (-MAX_WBITS).  Some legacy servers
         * (Microsoft IIS 5/6, older Java servlets) send raw deflate
         * (RFC 1951 only) under Content-Encoding: deflate instead of
         * zlib-wrapped (RFC 1950).  For gzip, no fallback is attempted.
         */
        if (loop_rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
            && type == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE)
        {
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                           "markdown: deflate zlib-wrapped failed, "
                           "retrying with raw deflate (-MAX_WBITS)");

            inflateEnd(&stream);

            ngx_memzero(&stream, sizeof(z_stream));
            stream.next_in = input_data;
            stream.avail_in = (uInt) input_size;

            zrc = inflateInit2(&stream, -MAX_WBITS);
            if (zrc != Z_OK) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: raw deflate inflateInit2 "
                             "error: %d, category=conversion", zrc);
                return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
            }

            stream.next_out = output_data;
            stream.avail_out = (uInt) output_size;

            loop_rc = ngx_http_markdown_inflate_loop(r, conf, &stream,
                                                     &output_data,
                                                     &output_size, type,
                                                     &total_decompressed);
            if (loop_rc != NGX_OK) {
                inflateEnd(&stream);
                return loop_rc;
            }
            /* fallthrough to success path below */
        } else {
            inflateEnd(&stream);
            return loop_rc;
        }
    }
    
    /* Check if decompressed size exceeds decompression budget (decompressed size budget enforcement) */
    if (total_decompressed > conf->decompress.max_size) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size (%uz) exceeds decompression budget (%uz), "
                     "category=resource_limit",
                     total_decompressed, conf->decompress.max_size);
        inflateEnd(&stream);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }
    
    /* total_decompressed was saved before inflateEnd releases the stream. */
    inflateEnd(&stream);
    
    /* Build the output chain wrapping the decompressed data directly
     * (avoids a second allocation + memcpy). Shared with the brotli path
     * via ngx_http_markdown_decomp_build_output_chain so the two backends
     * cannot drift apart on buffer/chain setup. */
    if (ngx_http_markdown_decomp_build_output_chain(r, output_data, output_size,
                                                    total_decompressed, 1,
                                                    out) != NGX_OK)
    {
        return NGX_ERROR;
    }
    
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: decompression succeeded, "
                  "compressed=%uz bytes, decompressed=%uz bytes, ratio=%.1f",
                  input_size, total_decompressed,
                  input_size > 0
                      ? (float)total_decompressed / input_size : 0.0f);

    /* Suppress -Wunused-but-set-variable when NGX_DEBUG is not enabled */
    (void) total_decompressed;
    
    return NGX_OK;
}

/*
 * Decompress brotli compressed data using brotli library
 *
 * This function implements automatic decompression of brotli compressed
 * content using the brotli library (if available at compile time).
 * It provides a fully automatic "technical fallback" solution when
 * upstream servers force brotli compression.
 *
 * The function:
 * 1. Checks if brotli support is compiled in (#ifdef NGX_HTTP_BROTLI)
 * 2. If not compiled, logs warning and returns NGX_DECLINED
 * 3. If compiled:
 *    - Collects all input data from the chain into a single buffer
 *    - Creates brotli decoder instance
 *    - Estimates output size (typically input_size * 10)
 *    - Allocates output buffer using nginx memory pool
 *    - Performs decompression
 *    - Checks for errors and size limits
 *    - Creates output chain with decompressed data
 *    - Destroys decoder instance
 *
 * Parameters:
 *   r   - nginx request structure
 *   in  - input chain with compressed data
 *   out - output chain with decompressed data (output parameter)
 *
 * Returns:
 *   NGX_OK                           - Decompression succeeded
 *   NGX_ERROR                        - Allocation failure
 *   NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR - Brotli decompression failed (invalid data)
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED - Decompressed size exceeds budget
 *   NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT - Truncated input stream detected
 *   NGX_DECLINED                     - Brotli support not compiled in (triggers fallback)
 *
 * Requirements: 3.1, 3.2, 3.3, 14.1
 */
ngx_int_t
ngx_http_markdown_decompress_brotli(ngx_http_request_t *r,
                                    const ngx_chain_t *in,
                                    ngx_chain_t **out)
{
#ifdef NGX_HTTP_BROTLI
    /* Brotli support is compiled in */
    BrotliDecoderState          *decoder;
    BrotliDecoderResult          result;
    u_char                      *input_data;
    size_t                       input_size;
    u_char                      *output_data;
    size_t                       output_size;
    size_t                       available_in;
    size_t                       available_out;
    const uint8_t               *next_in;
    uint8_t                     *next_out;
    size_t                       total_out;
    const ngx_http_markdown_conf_t    *conf;
    
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    
    /* Log that we're using brotli library for decompression (brotli decompression path) */
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: using brotli library for decompression");
    
    /* Collect all input data into a single buffer and validate its size
     * (shared with the zlib path via ngx_http_markdown_decomp_collect_input
     * so the two decompression backends cannot drift apart on size checks). */
    if (ngx_http_markdown_decomp_collect_input(r, in, &input_data,
                                               &input_size) != NGX_OK)
    {
        return NGX_ERROR;
    }
    
    /* Create brotli decoder instance (brotli decoder instance creation) */
    decoder = BrotliDecoderCreateInstance(NULL, NULL, NULL);
    if (decoder == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to create brotli decoder, "
                     "category=system");
        return NGX_ERROR;
    }
    
    /* Estimate output size with independent decompression budget. */
    if (ngx_http_markdown_calc_output_size(r, input_size, conf->decompress.max_size, &output_size)
        != NGX_OK)
    {
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    
    /* Allocate output buffer using nginx memory pool (brotli output buffer allocation from nginx pool) */
    output_data = ngx_http_markdown_decomp_alloc_output(r, output_size,
        ngx_http_markdown_decomp_brotli_cleanup, decoder);
    if (output_data == NULL) {
        return NGX_ERROR;
    }
    
    /* Set up decompression parameters */
    available_in = input_size;
    next_in = input_data;
    available_out = output_size;
    next_out = output_data;
    total_out = 0;
    
    /*
     * Perform decompression in a loop. If the decoder signals
     * NEEDS_MORE_OUTPUT, reallocate the output buffer (up to
     * decompress.max_size) and continue, rather than immediately
     * classifying as budget_exceeded. This avoids misclassifying
     * high-compression-ratio payloads that exceed the 10x heuristic
     * estimate but are still within budget.
     */
    for ( ;; ) {
        result = BrotliDecoderDecompressStream(
            decoder,
            &available_in,
            &next_in,
            &available_out,
            &next_out,
            &total_out
        );

        if (result == BROTLI_DECODER_RESULT_SUCCESS) {
            break;
        }

        if (result == BROTLI_DECODER_RESULT_ERROR) {
            BrotliDecoderErrorCode error_code = BrotliDecoderGetErrorCode(decoder);
            const char *error_str = BrotliDecoderErrorString(error_code);

            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: brotli decompression failed, "
                         "error: %s, category=conversion",
                         error_str);
            BrotliDecoderDestroyInstance(decoder);
            return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
        }

        if (result == BROTLI_DECODER_RESULT_NEEDS_MORE_OUTPUT) {
            size_t     used;
            ngx_int_t  grow_rc;

            used = output_size - available_out;
            grow_rc = ngx_http_markdown_grow_output_buffer(
                r, conf, &output_data, &output_size, used);
            if (grow_rc != NGX_OK) {
                BrotliDecoderDestroyInstance(decoder);
                return grow_rc;
            }
            available_out = output_size - used;
            next_out = output_data + used;
            continue;
        }

        if (result == BROTLI_DECODER_RESULT_NEEDS_MORE_INPUT) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: brotli decompression "
                         "incomplete, truncated input, "
                         "category=conversion");
            BrotliDecoderDestroyInstance(decoder);
            return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
        }

        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: brotli decompression "
                     "incomplete, result=%d, category=conversion",
                     result);
        BrotliDecoderDestroyInstance(decoder);
        return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
    }
    
    /* Calculate actual decompressed size */
    total_out = output_size - available_out;
    
    /* Check if decompressed size exceeds decompression budget */
    if (total_out > conf->decompress.max_size) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size (%uz) exceeds decompression budget (%uz), "
                     "category=resource_limit",
                     total_out, conf->decompress.max_size);
        BrotliDecoderDestroyInstance(decoder);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }
    
    /* Clean up decoder instance (brotli decoder cleanup) */
    BrotliDecoderDestroyInstance(decoder);
    
    /* Build the output chain wrapping the decompressed data directly
     * (avoids a second allocation + memcpy). Shared with the zlib path
     * via ngx_http_markdown_decomp_build_output_chain so the two backends
     * cannot drift apart on buffer/chain setup. */
    if (ngx_http_markdown_decomp_build_output_chain(r, output_data, output_size,
                                                    total_out, 1,
                                                    out) != NGX_OK)
    {
        return NGX_ERROR;
    }
    
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: brotli decompression succeeded, "
                  "compressed=%uz bytes, decompressed=%uz bytes, ratio=%.1f",
                  input_size, total_out,
                  input_size > 0
                      ? (float)total_out / input_size : 0.0f);
    
    return NGX_OK;
    
#else
    (void) in;
    (void) out;

    /* Brotli support not compiled in (brotli support not compiled in) */
    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown: brotli not supported, "
                 "brotli module not compiled in");
    
    return NGX_DECLINED;
#endif
}

/*
 * Unified decompression entry function
 *
 * This function serves as the main entry point for decompression operations.
 * It routes to the appropriate decompression function based on the compression
 * type and handles special cases like NGX_DECLINED from brotli (when brotli
 * support is not available).
 *
 * The function acts as a dispatcher and should be simple and straightforward.
 * It's called from the body filter after compression type detection.
 *
 * Parameters:
 *   r    - nginx request structure
 *   type - detected compression type (from ngx_http_markdown_detect_compression)
 *   in   - input chain with compressed data
 *   out  - output chain with decompressed data (output parameter)
 *
 * Returns:
 *   NGX_OK       - Decompression succeeded
 *   NGX_ERROR    - Decompression failed (invalid data, size limit, etc.)
 *   NGX_DECLINED - Unsupported format or brotli not available (triggers fallback)
 *
 * Requirements: 2.3, 3.4, 14.1
 */
ngx_int_t
ngx_http_markdown_decompress(ngx_http_request_t *r,
                              ngx_http_markdown_compression_type_e type,
                              const ngx_chain_t *in,
                              ngx_chain_t **out)
{
    ngx_int_t  rc;
    
    /* Route to appropriate decompression function based on type */
    switch (type) {
        case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
            /* Use zlib for gzip/deflate decompression (zlib for gzip/deflate decompression) */
            rc = ngx_http_markdown_decompress_gzip(r, type, in, out);

            if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: gzip/deflate decompressed "
                             "size exceeds budget, category=resource_limit");
            }

            return rc;
            
        case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
            /* Use brotli library for brotli decompression (brotli library for brotli decompression) */
            rc = ngx_http_markdown_decompress_brotli(r, in, out);
            
            /* Handle NGX_DECLINED from brotli function (when brotli not available) */
            if (rc == NGX_DECLINED) {
                ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                             "markdown: brotli compression detected but "
                             "brotli module not available, returning original content");
            }

            if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: brotli decompressed "
                             "size exceeds budget, category=resource_limit");
            }
            
            return rc;
            
        case NGX_HTTP_MARKDOWN_COMPRESSION_NONE:
            /* No compression, should not reach here */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: decompress called with COMPRESSION_NONE, "
                         "category=system");
            return NGX_ERROR;
            
        case NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN:
            /* Unknown/unsupported compression format */
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown: unsupported compression format, "
                         "returning original content");
            return NGX_DECLINED;
            
        default:
            /* Invalid compression type */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: invalid compression type: %d, "
                         "category=system",
                         type);
            return NGX_ERROR;
    }
}
