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
 * Safe narrowing from size_t to zlib's uInt.
 *
 * Returns 1 if the value exceeds UINT_MAX (overflow), 0 otherwise.
 * On success, *out is set to the narrowed value.
 */
static ngx_inline int
ngx_http_markdown_streaming_decomp_size_to_uint(
    size_t val, uInt *out)
{
    if (val > (size_t) UINT_MAX) {
        return 1;
    }
    *out = (uInt) val;
    return 0;
}

/*
 * Module-local sentinel: decompressed size budget exceeded.
 *
 * Returned by decomp_feed/decomp_finish when the cumulative
 * decompressed output exceeds max_decompressed_size. The caller
 * maps this to ERROR_BUDGET_EXCEEDED for proper metrics/reason-code
 * classification, distinguishing it from ERROR_INTERNAL.
 *
 * Value chosen to avoid collision with NGX_OK (0), NGX_ERROR (-1),
 * NGX_AGAIN (-2), NGX_DECLINED (-5), NGX_DONE (-4).
 */
#define NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED  -100

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

#ifdef NGX_HTTP_BROTLI
    /* Brotli I/O cursors (mirrors zlib's next_in/avail_in/next_out/avail_out) */
    const u_char                         *brotli_next_in;
    size_t                                brotli_avail_in;
    u_char                               *brotli_next_out;
    size_t                                brotli_avail_out;
#endif

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
 * Check whether the decompressed output exceeds the size limit.
 *
 * Uses subtraction form to avoid size_t addition overflow:
 * instead of (total + produced > max) which can wrap around,
 * checks (produced > max - total) after verifying total <= max.
 *
 * Returns 1 if the limit is exceeded, 0 otherwise.
 * The caller is responsible for logging when the limit is hit.
 */
static ngx_inline int
ngx_http_markdown_streaming_decomp_check_limit(
    const ngx_http_markdown_streaming_decomp_t *decomp,
    size_t produced)
{
    if (decomp->max_decompressed_size > 0) {
        if (decomp->total_decompressed
            > decomp->max_decompressed_size)
        {
            /* Already exceeded (should not happen, defensive) */
            return 1;
        }
        if (produced > decomp->max_decompressed_size
                       - decomp->total_decompressed)
        {
            return 1;
        }
    }
    return 0;
}


/*
 * Expand a heap-backed working buffer to double its size.
 *
 * On success, *heap_buf_ptr is updated to the new allocation
 * (the old one is freed), and the new buffer contains a copy
 * of the first buf_size bytes.
 *
 * Returns:
 *   NGX_OK    - success
 *   NGX_ERROR - allocation failure (old buffer freed)
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_expand_buf(
    u_char **heap_buf_ptr,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    ngx_log_t *log)
{
    u_char  *new_buf;
    size_t   old_size;
    size_t   new_size;

    old_size = *buf_size_ptr;

    /* Guard against size_t overflow: old_size * 2 */
    if (old_size > (size_t) -1 / 2) {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown streaming decomp: "
            "expand_buf size overflow, old_size=%uz",
            old_size);
        if (*heap_buf_ptr != NULL) {
            ngx_free(*heap_buf_ptr);
            *heap_buf_ptr = NULL;
        }
        return NGX_ERROR;
    }

    new_size = old_size * 2;

    new_buf = ngx_alloc(new_size, log);
    if (new_buf == NULL) {
        if (*heap_buf_ptr != NULL) {
            ngx_free(*heap_buf_ptr);
            *heap_buf_ptr = NULL;
        }
        return NGX_ERROR;
    }

    ngx_memcpy(new_buf, *buf_ptr, old_size);

    if (*heap_buf_ptr != NULL) {
        ngx_free(*heap_buf_ptr);
    }

    *heap_buf_ptr = new_buf;
    *buf_ptr = new_buf;
    *buf_size_ptr = new_size;
    return NGX_OK;
}


/*
 * Copy heap working buffer back to pool memory and free it.
 *
 * Returns:
 *   NGX_OK    - success (*buf_ptr updated to pool allocation)
 *   NGX_ERROR - pool allocation failure (heap buffer freed)
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_finalize_buf(
    u_char *heap_buf,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t produced,
    ngx_pool_t *pool)
{
    u_char  *pool_buf;

    pool_buf = ngx_palloc(pool, produced);
    if (pool_buf == NULL) {
        ngx_free(heap_buf);
        return NGX_ERROR;
    }

    ngx_memcpy(pool_buf, heap_buf, produced);
    ngx_free(heap_buf);

    *buf_ptr = pool_buf;
    *buf_size_ptr = produced;
    return NGX_OK;
}


/*
 * Step return codes:
 *   1  - done (Z_STREAM_END or avail_in == 0)
 *   0  - continue iterating
 *  -1  - error (heap_buf freed if needed)
 *  -2  - budget exceeded (heap_buf freed if needed)
 */
static int
ngx_http_markdown_streaming_decomp_inflate_step(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t *out_produced,
    u_char **heap_buf_ptr,
    int *using_heap_ptr,
    ngx_log_t *log)
{
    int  zrc;

    zrc = inflate(&decomp->state.zlib, Z_SYNC_FLUSH);

    /*
     * Z_BUF_ERROR is non-fatal here: it can mean "need more output space"
     * while still holding valid intermediate state. Treat it like a
     * continue signal and let the caller grow the buffer when needed.
     */
    if (zrc != Z_OK && zrc != Z_STREAM_END
        && zrc != Z_BUF_ERROR)
    {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown streaming decomp: "
            "inflate error %d", zrc);
        if (*heap_buf_ptr != NULL) {
            ngx_free(*heap_buf_ptr);
        }
        return -1;
    }

    *out_produced = *buf_size_ptr
                    - decomp->state.zlib.avail_out;

    if (ngx_http_markdown_streaming_decomp_check_limit(
            decomp, *out_produced))
    {
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown streaming decomp: "
            "decompressed size %uz exceeds limit %uz",
            decomp->total_decompressed + *out_produced,
            decomp->max_decompressed_size);
        if (*heap_buf_ptr != NULL) {
            ngx_free(*heap_buf_ptr);
        }
        return -2;  /* budget exceeded */
    }

    if (zrc == Z_STREAM_END) {
        decomp->finished = 1;
        return 1;
    }

    if (decomp->state.zlib.avail_in == 0) {
        return 1;
    }

    if (decomp->state.zlib.avail_out == 0) {
        if (ngx_http_markdown_streaming_decomp_expand_buf(
                heap_buf_ptr, buf_ptr, buf_size_ptr, log)
            != NGX_OK)
        {
            return -1;
        }

        *using_heap_ptr = 1;

        decomp->state.zlib.next_out =
            *buf_ptr + (*buf_size_ptr / 2);
        {
            uInt  half_out;

            if (ngx_http_markdown_streaming_decomp_size_to_uint(
                    *buf_size_ptr / 2, &half_out))
            {
                ngx_log_error(NGX_LOG_ERR, log, 0,
                    "markdown streaming decomp: "
                    "expanded buffer half %uz exceeds "
                    "zlib uInt max",
                    *buf_size_ptr / 2);
                if (*heap_buf_ptr != NULL) {
                    ngx_free(*heap_buf_ptr);
                }
                return -1;
            }
            decomp->state.zlib.avail_out = half_out;
        }
    }

    return 0;
}


/*
 * Inflate loop for zlib (gzip/deflate) decompression.
 *
 * Calls inflate() in a loop, expanding the output buffer
 * as needed until all input is consumed or Z_STREAM_END.
 *
 * Returns:
 *   NGX_OK    - success (produced written to *out_produced)
 *   NGX_ERROR - inflate error or size limit exceeded
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_inflate_loop(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t *out_produced,
    ngx_pool_t *pool,
    ngx_log_t *log)
{
    u_char  *heap_buf;
    int      using_heap;
    int      step_rc;

    heap_buf = NULL;
    using_heap = 0;

    for ( ;; ) {
        step_rc =
            ngx_http_markdown_streaming_decomp_inflate_step(
                decomp, buf_ptr, buf_size_ptr,
                out_produced, &heap_buf,
                &using_heap, log);

        if (step_rc == -2) {
            return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
        }

        if (step_rc < 0) {
            return NGX_ERROR;
        }

        if (step_rc > 0) {
            break;
        }
    }

    if (using_heap) {
        if (ngx_http_markdown_streaming_decomp_finalize_buf(
                heap_buf, buf_ptr, buf_size_ptr,
                *out_produced, pool)
            != NGX_OK)
        {
            return NGX_ERROR;
        }
    }

    return NGX_OK;
}


#ifdef NGX_HTTP_BROTLI
/*
 * Brotli decompression loop.
 *
 * Calls BrotliDecoderDecompressStream in a loop, expanding
 * the output buffer as needed.
 *
 * Returns:
 *   NGX_OK    - success (produced written to *out_produced)
 *   NGX_ERROR - decode error or size limit exceeded
 */
/*
 * Core brotli iteration: single call to BrotliDecoderDecompressStream
 * with error/limit/expand handling.
 *
 * Returns:
 *   1  - done (SUCCESS or avail_in == 0)
 *   0  - continue iterating
 *  -1  - error (heap_buf freed if needed)
 *  -2  - budget exceeded (heap_buf freed if needed)
 *  -1  - error (heap_buf freed if needed)
 */
static int
ngx_http_markdown_streaming_decomp_brotli_step(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t *out_produced,
    u_char **heap_buf_ptr,
    int *using_heap_ptr,
    ngx_log_t *log)
{
    BrotliDecoderResult  brc;
    size_t               avail_in;
    const uint8_t       *next_in;
    size_t               avail_out;
    uint8_t             *next_out;

    avail_in = decomp->brotli_avail_in;
    next_in = decomp->brotli_next_in;
    avail_out = decomp->brotli_avail_out;
    next_out = decomp->brotli_next_out;

    brc = BrotliDecoderDecompressStream(
        decomp->state.brotli,
        &avail_in, &next_in,
        &avail_out, &next_out, NULL);

    decomp->brotli_avail_in = avail_in;
    decomp->brotli_next_in = next_in;
    decomp->brotli_avail_out = avail_out;
    decomp->brotli_next_out = next_out;

    if (brc == BROTLI_DECODER_RESULT_ERROR) {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown streaming decomp: "
            "brotli decode error");
        if (*heap_buf_ptr != NULL) {
            ngx_free(*heap_buf_ptr);
        }
        return -1;
    }

    *out_produced = *buf_size_ptr - decomp->brotli_avail_out;

    if (ngx_http_markdown_streaming_decomp_check_limit(
            decomp, *out_produced))
    {
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown streaming decomp: "
            "decompressed size %uz exceeds limit %uz",
            decomp->total_decompressed + *out_produced,
            decomp->max_decompressed_size);
        if (*heap_buf_ptr != NULL) {
            ngx_free(*heap_buf_ptr);
        }
        return -2;  /* budget exceeded */
    }

    if (brc == BROTLI_DECODER_RESULT_SUCCESS) {
        decomp->finished = 1;
        return 1;
    }

    if (brc == BROTLI_DECODER_RESULT_NEEDS_MORE_OUTPUT) {
        size_t  old_size;

        old_size = *buf_size_ptr;

        if (ngx_http_markdown_streaming_decomp_expand_buf(
                heap_buf_ptr, buf_ptr, buf_size_ptr, log)
            != NGX_OK)
        {
            return -1;
        }

        *using_heap_ptr = 1;
        /*
         * Expansion doubles the buffer. Resume writing at the old end so
         * already-produced bytes remain intact at [0, old_size).
         */
        decomp->brotli_next_out = *buf_ptr + old_size;
        decomp->brotli_avail_out = *buf_size_ptr - old_size;
        return 0;
    }

    if (decomp->brotli_avail_in == 0) {
        return 1;
    }

    return 0;
}


static ngx_int_t
ngx_http_markdown_streaming_decomp_brotli_loop(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t *out_produced,
    ngx_pool_t *pool,
    ngx_log_t *log)
{
    u_char              *heap_buf;
    int                  using_heap;
    int                  step_rc;

    decomp->brotli_avail_out = *buf_size_ptr;
    decomp->brotli_next_out = *buf_ptr;
    heap_buf = NULL;
    using_heap = 0;

    for ( ;; ) {
        step_rc =
            ngx_http_markdown_streaming_decomp_brotli_step(
                decomp,
                buf_ptr, buf_size_ptr,
                out_produced, &heap_buf,
                &using_heap, log);

        if (step_rc == -2) {
            return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
        }

        if (step_rc < 0) {
            return NGX_ERROR;
        }

        if (step_rc > 0) {
            break;
        }
    }

    if (using_heap) {
        if (ngx_http_markdown_streaming_decomp_finalize_buf(
                heap_buf, buf_ptr, buf_size_ptr,
                *out_produced, pool)
            != NGX_OK)
        {
            return NGX_ERROR;
        }
    }

    return NGX_OK;
}
#endif


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
    if (in_len > (size_t) -1 / 4) {
        buf_size = (size_t) -1;  /* saturate to max */
    } else {
        buf_size = in_len * 4;
    }
    if (buf_size < 4096) {
        buf_size = 4096;
    }

    /* Clamp to remaining size budget */
    if (decomp->max_decompressed_size > 0) {
        size_t remaining;

        if (decomp->total_decompressed
            >= decomp->max_decompressed_size)
        {
            /* Budget already exhausted */
            return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
        }

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
        ngx_int_t  inflate_rc;

        decomp->state.zlib.next_in = in_data;
        if (ngx_http_markdown_streaming_decomp_size_to_uint(
                in_len, &decomp->state.zlib.avail_in))
        {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown streaming decomp: "
                "input length %uz exceeds zlib uInt max",
                in_len);
            return NGX_ERROR;
        }
        decomp->state.zlib.next_out = buf;
        if (ngx_http_markdown_streaming_decomp_size_to_uint(
                buf_size, &decomp->state.zlib.avail_out))
        {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown streaming decomp: "
                "buffer size %uz exceeds zlib uInt max",
                buf_size);
            return NGX_ERROR;
        }

        inflate_rc =
            ngx_http_markdown_streaming_decomp_inflate_loop(
                decomp, &buf, &buf_size, &produced,
                pool, log);
        if (inflate_rc != NGX_OK) {
            return inflate_rc;
        }

        break;
    }

#ifdef NGX_HTTP_BROTLI
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
    {
        ngx_int_t  brotli_rc;

        decomp->brotli_next_in = in_data;
        decomp->brotli_avail_in = in_len;

        brotli_rc =
            ngx_http_markdown_streaming_decomp_brotli_loop(
                decomp,
                &buf, &buf_size, &produced,
                pool, log);
        if (brotli_rc != NGX_OK) {
            return brotli_rc;
        }

        break;
    }
#endif

    default:
        return NGX_DECLINED;
    }

    /* Check size limit and protect against integer overflow. */
    if (decomp->total_decompressed > NGX_MAX_SIZE_T_VALUE - produced) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown streaming decomp: "
            "decompressed size overflow, total=%uz produced=%uz",
            decomp->total_decompressed,
            produced);
        return NGX_ERROR;
    }

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
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    *out_data = buf;
    *out_len = produced;
    return NGX_OK;
}


static void
ngx_http_markdown_streaming_decomp_finish_free_heap(
    u_char **heap_buf_ptr)
{
    if (*heap_buf_ptr != NULL) {
        ngx_free(*heap_buf_ptr);
        *heap_buf_ptr = NULL;
    }
}


static ngx_int_t
ngx_http_markdown_streaming_decomp_finish_zlib(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t *produced_ptr,
    ngx_pool_t *pool,
    ngx_log_t *log)
{
    u_char      *heap_buf;
    ngx_flag_t   using_heap;
    int          zrc;

    heap_buf = NULL;
    using_heap = 0;

    decomp->state.zlib.next_in = Z_NULL;
    decomp->state.zlib.avail_in = 0;
    decomp->state.zlib.next_out = *buf_ptr;
    if (ngx_http_markdown_streaming_decomp_size_to_uint(
            *buf_size_ptr, &decomp->state.zlib.avail_out))
    {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown streaming decomp: "
            "finish buffer %uz exceeds zlib uInt max",
            *buf_size_ptr);
        return NGX_ERROR;
    }

    for ( ;; ) {
        zrc = inflate(&decomp->state.zlib, Z_FINISH);

        if (zrc != Z_STREAM_END && zrc != Z_OK
            && zrc != Z_BUF_ERROR)
        {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown streaming decomp: "
                "finish inflate error %d", zrc);
            ngx_http_markdown_streaming_decomp_finish_free_heap(
                &heap_buf);
            return NGX_ERROR;
        }

        *produced_ptr = *buf_size_ptr
                        - decomp->state.zlib.avail_out;

        if (ngx_http_markdown_streaming_decomp_check_limit(
                decomp, *produced_ptr))
        {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                "markdown streaming decomp: "
                "decompressed size %uz exceeds limit %uz",
                decomp->total_decompressed + *produced_ptr,
                decomp->max_decompressed_size);
            ngx_http_markdown_streaming_decomp_finish_free_heap(
                &heap_buf);
            return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
        }

        if (zrc == Z_STREAM_END) {
            decomp->finished = 1;
            break;
        }

        if (zrc == Z_BUF_ERROR
            && decomp->state.zlib.avail_out != 0)
        {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown streaming decomp: "
                "finish inflate incomplete stream");
            ngx_http_markdown_streaming_decomp_finish_free_heap(
                &heap_buf);
            return NGX_ERROR;
        }

        if (decomp->state.zlib.avail_out != 0) {
            continue;
        }

        {
            size_t  old_size;

            old_size = *buf_size_ptr;
            /*
             * expand_buf() frees any previous heap buffer if expansion fails,
             * so we can safely return directly on NGX_ERROR here.
             */
            if (ngx_http_markdown_streaming_decomp_expand_buf(
                    &heap_buf, buf_ptr, buf_size_ptr, log)
                != NGX_OK)
            {
                return NGX_ERROR;
            }

            using_heap = 1;
            decomp->state.zlib.next_out = *buf_ptr + old_size;
            {
                uInt  expand_out;

                if (ngx_http_markdown_streaming_decomp_size_to_uint(
                        *buf_size_ptr - old_size, &expand_out))
                {
                    ngx_log_error(NGX_LOG_ERR, log, 0,
                        "markdown streaming decomp: "
                        "finish expand %uz exceeds "
                        "zlib uInt max",
                        *buf_size_ptr - old_size);
                    ngx_http_markdown_streaming_decomp_finish_free_heap(
                        &heap_buf);
                    return NGX_ERROR;
                }
                decomp->state.zlib.avail_out = expand_out;
            }
        }
    }

    if (!using_heap) {
        return NGX_OK;
    }

    if (ngx_http_markdown_streaming_decomp_finalize_buf(
            heap_buf, buf_ptr, buf_size_ptr,
            *produced_ptr, pool)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

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
        ngx_int_t  finish_rc;

        finish_rc =
            ngx_http_markdown_streaming_decomp_finish_zlib(
                decomp, &buf, &buf_size,
                &produced, pool, log);
        if (finish_rc != NGX_OK) {
            return finish_rc;
        }
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

    if (produced > (size_t) -1 - decomp->total_decompressed) {
        decomp->total_decompressed = (size_t) -1;
    } else {
        decomp->total_decompressed += produced;
    }
    *out_data = buf;
    *out_len = produced;
    return NGX_OK;
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_STREAMING_DECOMP_IMPL_H */
