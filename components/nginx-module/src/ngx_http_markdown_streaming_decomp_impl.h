#ifndef NGX_HTTP_MARKDOWN_STREAMING_DECOMP_IMPL_H
#define NGX_HTTP_MARKDOWN_STREAMING_DECOMP_IMPL_H

/*
 * Streaming decompression implementation.
 *
 * WARNING: This header is an implementation detail of the main translation
 * unit (ngx_http_markdown_filter_module.c). It must NOT be included from
 * any other .c file or used as a standalone compilation unit.
 *
 * Provides incremental (chunk-at-a-time) decompression for gzip, deflate,
 * and optionally brotli compressed upstream responses in the streaming
 * conversion path.
 */

#ifdef MARKDOWN_STREAMING_ENABLED

#include <zlib.h>

#ifdef NGX_HTTP_BROTLI
#include <brotli/decode.h>
#endif

/*
 * Streaming decompressor state.
 *
 * Maintains per-request decompression context for incremental
 * processing. Released via pool cleanup handler.
 */
typedef struct ngx_http_markdown_streaming_decomp_s {
    ngx_http_markdown_compression_type_e  type;
    size_t                                max_decompressed_size;
    size_t                                total_decompressed;

    union {
        z_stream                          zlib;
#ifdef NGX_HTTP_BROTLI
        BrotliDecoderState               *brotli;
#endif
    } state;

    ngx_flag_t                            initialized;
    ngx_flag_t                            finished;
} ngx_http_markdown_streaming_decomp_t;

/* Forward declarations */
static void
ngx_http_markdown_streaming_decomp_cleanup(void *data);

/*
 * Pool cleanup handler for streaming decompressor.
 *
 * Releases zlib or brotli state when the request pool
 * is destroyed.
 */
static void
ngx_http_markdown_streaming_decomp_cleanup(void *data)
{
    ngx_http_markdown_streaming_decomp_t *decomp = data;

    if (decomp == NULL || !decomp->initialized) {
        return;
    }

    switch (decomp->type) {

    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        inflateEnd(&decomp->state.zlib);
        break;

#ifdef NGX_HTTP_BROTLI
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        if (decomp->state.brotli != NULL) {
            BrotliDecoderDestroyInstance(
                decomp->state.brotli);
            decomp->state.brotli = NULL;
        }
        break;
#endif

    default:
        break;
    }

    decomp->initialized = 0;
}


/*
 * Create a streaming decompressor for the given compression type.
 *
 * Returns NULL on allocation failure or unsupported type.
 * Registers a pool cleanup handler for automatic teardown.
 */
static ngx_http_markdown_streaming_decomp_t *
ngx_http_markdown_streaming_decomp_create(
    ngx_pool_t *pool,
    ngx_http_markdown_compression_type_e type,
    size_t max_decompressed_size)
{
    ngx_http_markdown_streaming_decomp_t  *decomp;
    ngx_pool_cleanup_t                    *cln;
    int                                    zrc;
    int                                    window_bits;

    if (pool == NULL) {
        return NULL;
    }

    decomp = ngx_pcalloc(pool,
        sizeof(ngx_http_markdown_streaming_decomp_t));
    if (decomp == NULL) {
        return NULL;
    }

    decomp->type = type;
    decomp->max_decompressed_size = max_decompressed_size;
    decomp->total_decompressed = 0;
    decomp->initialized = 0;
    decomp->finished = 0;

    switch (type) {

    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        /* gzip: MAX_WBITS + 16 for automatic gzip header detection */
        window_bits = MAX_WBITS + 16;
        decomp->state.zlib.zalloc = Z_NULL;
        decomp->state.zlib.zfree = Z_NULL;
        decomp->state.zlib.opaque = Z_NULL;
        decomp->state.zlib.avail_in = 0;
        decomp->state.zlib.next_in = Z_NULL;

        zrc = inflateInit2(&decomp->state.zlib,
                           window_bits);
        if (zrc != Z_OK) {
            return NULL;
        }
        decomp->initialized = 1;
        break;

    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        /* deflate: -MAX_WBITS for raw deflate */
        window_bits = -MAX_WBITS;
        decomp->state.zlib.zalloc = Z_NULL;
        decomp->state.zlib.zfree = Z_NULL;
        decomp->state.zlib.opaque = Z_NULL;
        decomp->state.zlib.avail_in = 0;
        decomp->state.zlib.next_in = Z_NULL;

        zrc = inflateInit2(&decomp->state.zlib,
                           window_bits);
        if (zrc != Z_OK) {
            return NULL;
        }
        decomp->initialized = 1;
        break;

#ifdef NGX_HTTP_BROTLI
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        decomp->state.brotli =
            BrotliDecoderCreateInstance(NULL, NULL, NULL);
        if (decomp->state.brotli == NULL) {
            return NULL;
        }
        decomp->initialized = 1;
        break;
#endif

    default:
        return NULL;
    }

    /* Register pool cleanup */
    cln = ngx_pool_cleanup_add(pool, 0);
    if (cln == NULL) {
        ngx_http_markdown_streaming_decomp_cleanup(
            decomp);
        return NULL;
    }

    cln->handler =
        ngx_http_markdown_streaming_decomp_cleanup;
    cln->data = decomp;

    return decomp;
}


/*
 * Feed a compressed chunk to the streaming decompressor.
 *
 * Decompresses incrementally and writes output to a pool-
 * allocated buffer. Enforces the max decompressed size limit.
 *
 * Returns:
 *   NGX_OK       - success (out_data/out_len populated)
 *   NGX_ERROR    - decompression error or size limit exceeded
 *   NGX_DECLINED - unsupported format
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_feed(
    ngx_http_markdown_streaming_decomp_t *decomp,
    const u_char *in_data,
    size_t in_len,
    u_char **out_data,
    size_t *out_len,
    ngx_pool_t *pool,
    ngx_log_t *log)
{
    u_char  *buf;
    size_t   buf_size;
    size_t   produced;

    if (decomp == NULL || !decomp->initialized
        || out_data == NULL || out_len == NULL)
    {
        return NGX_ERROR;
    }

    if (decomp->finished) {
        *out_data = NULL;
        *out_len = 0;
        return NGX_OK;
    }

    /* Empty input is a no-op */
    if (in_data == NULL || in_len == 0) {
        *out_data = NULL;
        *out_len = 0;
        return NGX_OK;
    }

    /* Estimate output buffer: 4x input or 4KB minimum */
    buf_size = in_len * 4;
    if (buf_size < 4096) {
        buf_size = 4096;
    }

    /* Clamp to remaining size budget */
    if (decomp->max_decompressed_size > 0) {
        size_t remaining;

        remaining = decomp->max_decompressed_size
                    - decomp->total_decompressed;
        if (buf_size > remaining) {
            buf_size = remaining + 1;
        }
    }

    buf = ngx_palloc(pool, buf_size);
    if (buf == NULL) {
        return NGX_ERROR;
    }

    produced = 0;

    switch (decomp->type) {

    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
    {
        int      zrc;
        u_char  *new_buf;
        size_t   new_size;

        decomp->state.zlib.next_in = (Bytef *) in_data;
        decomp->state.zlib.avail_in = (uInt) in_len;
        decomp->state.zlib.next_out = (Bytef *) buf;
        decomp->state.zlib.avail_out = (uInt) buf_size;

        for ( ;; ) {
            zrc = inflate(&decomp->state.zlib,
                          Z_SYNC_FLUSH);

            if (zrc != Z_OK && zrc != Z_STREAM_END
                && zrc != Z_BUF_ERROR)
            {
                ngx_log_error(NGX_LOG_ERR, log, 0,
                    "markdown streaming decomp: "
                    "inflate error %d", zrc);
                return NGX_ERROR;
            }

            produced = buf_size
                       - decomp->state.zlib.avail_out;

            /* Check size limit after each iteration */
            if (decomp->max_decompressed_size > 0
                && (decomp->total_decompressed + produced)
                   > decomp->max_decompressed_size)
            {
                ngx_log_error(NGX_LOG_WARN, log, 0,
                    "markdown streaming decomp: "
                    "decompressed size %uz exceeds "
                    "limit %uz",
                    decomp->total_decompressed + produced,
                    decomp->max_decompressed_size);
                return NGX_ERROR;
            }

            if (zrc == Z_STREAM_END) {
                decomp->finished = 1;
                break;
            }

            if (decomp->state.zlib.avail_in == 0) {
                break;
            }

            /* Output buffer full, input remaining:
             * expand buffer to 2x and continue */
            if (decomp->state.zlib.avail_out == 0) {
                new_size = buf_size * 2;

                new_buf = ngx_palloc(pool, new_size);
                if (new_buf == NULL) {
                    return NGX_ERROR;
                }

                ngx_memcpy(new_buf, buf, buf_size);

                decomp->state.zlib.next_out =
                    (Bytef *) (new_buf + buf_size);
                decomp->state.zlib.avail_out =
                    (uInt) (new_size - buf_size);

                buf = new_buf;
                buf_size = new_size;
            }
        }

        break;
    }

#ifdef NGX_HTTP_BROTLI
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
    {
        BrotliDecoderResult  brc;
        u_char              *new_buf;
        size_t               new_size;
        size_t               avail_in;
        size_t               avail_out;
        const uint8_t       *next_in;
        uint8_t             *next_out;

        avail_in = in_len;
        next_in = (const uint8_t *) in_data;
        avail_out = buf_size;
        next_out = (uint8_t *) buf;

        for ( ;; ) {
            brc = BrotliDecoderDecompressStream(
                decomp->state.brotli,
                &avail_in, &next_in,
                &avail_out, &next_out, NULL);

            if (brc == BROTLI_DECODER_RESULT_ERROR) {
                ngx_log_error(NGX_LOG_ERR, log, 0,
                    "markdown streaming decomp: "
                    "brotli decode error");
                return NGX_ERROR;
            }

            produced = buf_size - avail_out;

            /* Check size limit after each iteration */
            if (decomp->max_decompressed_size > 0
                && (decomp->total_decompressed + produced)
                   > decomp->max_decompressed_size)
            {
                ngx_log_error(NGX_LOG_WARN, log, 0,
                    "markdown streaming decomp: "
                    "decompressed size %uz exceeds "
                    "limit %uz",
                    decomp->total_decompressed + produced,
                    decomp->max_decompressed_size);
                return NGX_ERROR;
            }

            if (brc == BROTLI_DECODER_RESULT_SUCCESS) {
                decomp->finished = 1;
                break;
            }

            /* Output buffer full: expand before checking
             * avail_in — Brotli may have internal output
             * pending even when all input is consumed */
            if (brc
                == BROTLI_DECODER_RESULT_NEEDS_MORE_OUTPUT)
            {
                new_size = buf_size * 2;

                new_buf = ngx_palloc(pool, new_size);
                if (new_buf == NULL) {
                    return NGX_ERROR;
                }

                ngx_memcpy(new_buf, buf, buf_size);

                next_out =
                    (uint8_t *) (new_buf + buf_size);
                avail_out = new_size - buf_size;

                buf = new_buf;
                buf_size = new_size;
                continue;
            }

            if (avail_in == 0) {
                break;
            }
        }

        break;
    }
#endif

    default:
        return NGX_DECLINED;
    }

    /* Check size limit */
    decomp->total_decompressed += produced;
    if (decomp->max_decompressed_size > 0
        && decomp->total_decompressed
           > decomp->max_decompressed_size)
    {
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown streaming decomp: "
            "decompressed size %uz exceeds limit %uz",
            decomp->total_decompressed,
            decomp->max_decompressed_size);
        return NGX_ERROR;
    }

    *out_data = buf;
    *out_len = produced;
    return NGX_OK;
}


/*
 * Finish decompression (handle last_buf).
 *
 * For zlib, flushes remaining data with Z_FINISH.
 * For brotli, checks stream completeness.
 *
 * Returns:
 *   NGX_OK    - success
 *   NGX_ERROR - decompression error
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_finish(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **out_data,
    size_t *out_len,
    ngx_pool_t *pool,
    ngx_log_t *log)
{
    u_char  *buf;
    size_t   buf_size;
    size_t   produced;

    if (decomp == NULL || !decomp->initialized
        || out_data == NULL || out_len == NULL)
    {
        return NGX_ERROR;
    }

    *out_data = NULL;
    *out_len = 0;

    if (decomp->finished) {
        return NGX_OK;
    }

    buf_size = 4096;
    buf = ngx_palloc(pool, buf_size);
    if (buf == NULL) {
        return NGX_ERROR;
    }

    produced = 0;

    switch (decomp->type) {

    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
    {
        int  zrc;

        decomp->state.zlib.next_in = Z_NULL;
        decomp->state.zlib.avail_in = 0;
        decomp->state.zlib.next_out = (Bytef *) buf;
        decomp->state.zlib.avail_out = (uInt) buf_size;

        zrc = inflate(&decomp->state.zlib, Z_FINISH);

        if (zrc != Z_STREAM_END && zrc != Z_OK
            && zrc != Z_BUF_ERROR)
        {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown streaming decomp: "
                "finish inflate error %d", zrc);
            return NGX_ERROR;
        }

        produced = buf_size
                   - decomp->state.zlib.avail_out;
        decomp->finished = 1;
        break;
    }

#ifdef NGX_HTTP_BROTLI
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        if (!BrotliDecoderIsFinished(
                decomp->state.brotli))
        {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                "markdown streaming decomp: "
                "brotli stream not finished");
        }
        decomp->finished = 1;
        break;
#endif

    default:
        return NGX_ERROR;
    }

    decomp->total_decompressed += produced;
    *out_data = buf;
    *out_len = produced;
    return NGX_OK;
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_STREAMING_DECOMP_IMPL_H */
