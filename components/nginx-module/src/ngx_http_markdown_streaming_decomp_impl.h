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
 *
 * ROUTING NOTE (0.9.1): Deflate and gzip are routed to the streaming
 * decompression path when automatic decompression is enabled and full cache
 * validation is not required.  The deflate path defers inflateInit2 until
 * the first 2 bytes of input arrive, then sniffs the zlib wrapper:
 *   - zlib-wrapped (RFC 1950, RFC 9110-compliant): MAX_WBITS
 *   - raw deflate (RFC 1951): -MAX_WBITS
 * Gzip uses MAX_WBITS + 16 and preserves gzip member boundaries across
 * arbitrary input chunks. Brotli uses its incremental decoder when the
 * optional Brotli build support is enabled.
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
 * NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED is defined in
 * ngx_http_markdown_filter_module.h (shared by buffered and streaming
 * decompression paths).  The streaming decomp implementation relies
 * on the shared definition included via filter_module.h.
 */

/*
 * Failure origin tracking for bare NGX_ERROR returns.
 *
 * When a decompressor returns NGX_ERROR (as opposed to a typed error
 * like FORMAT_ERROR), the outer mapper needs to know whether the
 * failure is an allocation issue or an internal/system error to
 * select the correct global failure category counter.
 */
typedef enum {
    NGX_HTTP_MD_DECOMP_ORIGIN_NONE = 0,
    NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION,
    NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL
} ngx_http_markdown_decomp_failure_origin_e;

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

    /*
     * zlib header sniffing for Content-Encoding: deflate.
     *
     * RFC 9110 defines HTTP "deflate" as the zlib-wrapped format
     * (RFC 1950), but many servers send raw deflate (RFC 1951).
     * Unlike the buffered path, the streaming path cannot retry
     * once chunks are consumed.  We defer inflateInit2 until the
     * first 2 bytes arrive, then sniff the zlib header:
     *
     *   - If the first two bytes look like a valid zlib header
     *     (CMF + FLG), initialize with MAX_WBITS (zlib-wrapped).
     *   - Otherwise, initialize with -MAX_WBITS (raw deflate).
     *
     * The sniff is heuristic but matches the common-cases approach
     * used by zlib's own `uncompress()` and by curl/libcurl.
     *
     * ``zlib_header_pending`` is 1 when we are still collecting the
     * first 2 bytes.  ``pending_header`` accumulates up to 2 bytes.
     * ``pending_header_len`` tracks how many we have so far.
     */
    ngx_flag_t                            zlib_header_pending;
    u_char                                pending_header[2];
    size_t                                pending_header_len;

    /*
     * Set after a gzip member reaches Z_STREAM_END and the inflater has
     * been reset for a possible next member.  A member boundary is valid at
     * upstream EOF, but later compressed input starts a new member and must
     * not be discarded as if the HTTP response had already finished.
     */
    ngx_flag_t                            at_gzip_member_boundary;

    ngx_flag_t                            initialized;
    /* Set only when the complete compressed HTTP response is finalized. */
    ngx_flag_t                            finished;

    /*
     * Per-call failure origin for bare NGX_ERROR returns.
     * Set by the decoder step before returning NGX_ERROR so the
     * outer mapper can distinguish allocation vs internal failures.
     * Reset to NONE at the start of each feed/finish call.
     */
    ngx_http_markdown_decomp_failure_origin_e  failure_origin;
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
 * Initialize zlib inflate state for gzip or raw deflate.
 *
 * Arguments:
 *   decomp      — target decompressor (z_stream zeroed on entry)
 *   window_bits — zlib window size (MAX_WBITS+16 for gzip, -MAX_WBITS for raw deflate)
 *
 * Returns:
 *   NGX_OK    — inflateInit2 succeeded, decomp->initialized set
 *   NGX_ERROR — inflateInit2 failed
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_init_zlib(
    ngx_http_markdown_streaming_decomp_t *decomp,
    int window_bits)
{
    int  zrc;

    decomp->state.zlib.zalloc = Z_NULL;
    decomp->state.zlib.zfree = Z_NULL;
    decomp->state.zlib.opaque = Z_NULL;
    decomp->state.zlib.avail_in = 0;
    decomp->state.zlib.next_in = Z_NULL;

    zrc = inflateInit2(&decomp->state.zlib, window_bits);
    if (zrc != Z_OK) {
        if (zrc == Z_MEM_ERROR) {
            decomp->failure_origin =
                NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        } else {
            decomp->failure_origin =
                NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
        }
        return NGX_ERROR;
    }

    decomp->initialized = 1;
    return NGX_OK;
}


/*
 * Heuristic detection of the zlib wrapper from the first 2 bytes.
 *
 * A valid zlib (RFC 1950) stream starts with CMF followed by FLG.
 * CMF encodes CM (compression method, bits 0-3) and CINFO (bits 4-7).
 * CM must be 8 (deflate).  FLG has FCHECK (bits 0-4), FDICT (bit 5),
 * and FLEVEL (bits 6-7).  (CMF * 256 + FLG) must be divisible by 31.
 *
 * Returns 1 (true) if the two bytes form a valid zlib header, 0
 * otherwise (in which case the data is raw deflate per RFC 1951).
 */
static ngx_inline int
ngx_http_markdown_streaming_decomp_is_zlib_header(u_char cmf, u_char flg)
{
    /* CM must be 8 (deflate) */
    if ((cmf & 0x0f) != 8) {
        return 0;
    }

    /* CINFO is the log2 of the window size minus 8; must be <= 7 */
    if ((cmf >> 4) > 7) {
        return 0;
    }

    /* (CMF * 256 + FLG) must be a multiple of 31 */
    if ((((unsigned) cmf << 8) | flg) % 31 != 0) {
        return 0;
    }

    return 1;
}


/*
 * Resolve the deferred zlib initialization for Content-Encoding: deflate.
 *
 * Called after the first 2 bytes of input are accumulated in
 * decomp->pending_header.  If the bytes look like a valid zlib
 * (RFC 1950) header, initialize with MAX_WBITS (zlib-wrapped).
 * Otherwise, initialize with -MAX_WBITS (raw deflate, RFC 1951).
 *
 * After initialization, the pending header bytes are fed into the
 * inflate stream as the first input chunk via the caller's normal
 * feed path (the caller passes them as the prefix of the first
 * real input).
 *
 * Returns:
 *   NGX_OK    - initialization succeeded; caller should feed
 *               pending_header + in_data together
 *   NGX_ERROR - inflateInit2 failed
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_resolve_deflate_header(
    ngx_http_markdown_streaming_decomp_t *decomp,
    const ngx_log_t *log)
{
    int  window_bits;

    if (ngx_http_markdown_streaming_decomp_is_zlib_header(
            decomp->pending_header[0],
            decomp->pending_header[1]))
    {
        /* zlib-wrapped deflate (RFC 1950 / RFC 9110) */
        window_bits = MAX_WBITS;
        if (log != NULL) {
#ifdef NGX_LOG_DEBUG_HTTP
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, (ngx_log_t *) log, 0,
                "markdown: streaming deflate: zlib-wrapped header detected");
#endif
        }
    } else {
        /* raw deflate (RFC 1951) */
        window_bits = -MAX_WBITS;
        if (log != NULL) {
#ifdef NGX_LOG_DEBUG_HTTP
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, (ngx_log_t *) log, 0,
                "markdown: streaming deflate: raw deflate header detected");
#endif
        }
    }

    if (ngx_http_markdown_streaming_decomp_init_zlib(
            decomp, window_bits)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    decomp->zlib_header_pending = 0;
    return NGX_OK;
}


/*
 * Accumulate header bytes for the deferred deflate initialization.
 *
 * Feeds input bytes into pending_header until 2 bytes are collected,
 * then resolves the initialization.  Sets *consumed to the number of
 * input bytes consumed as header bytes.  The caller must re-feed the
 * pending header + remaining input after this returns NGX_OK.
 *
 * Returns:
 *   NGX_OK    - header resolved or more bytes needed
 *               (if more bytes needed, *consumed = in_len, no more
 *                input to feed this round)
 *   NGX_ERROR - initialization failed after 2 bytes collected
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_feed_header(
    ngx_http_markdown_streaming_decomp_t *decomp,
    const u_char *in_data,
    size_t in_len,
    size_t *consumed,
    const ngx_log_t *log)
{
    size_t  needed;
    size_t  to_copy;

    *consumed = 0;

    needed = 2 - decomp->pending_header_len;
    to_copy = (in_len < needed) ? in_len : needed;

    ngx_memcpy(&decomp->pending_header[decomp->pending_header_len],
               in_data, to_copy);
    decomp->pending_header_len += to_copy;
    *consumed = to_copy;

    if (decomp->pending_header_len < 2) {
        /* Still need more bytes; all input was consumed as header. */
        return NGX_OK;
    }

    /* We have 2 bytes; resolve the initialization. */
    return ngx_http_markdown_streaming_decomp_resolve_deflate_header(
        decomp, log);
}


/*
 * Create a streaming decompressor for the given compression type.
 *
 * Returns NULL on allocation failure or unsupported type.
 * Registers a pool cleanup handler for automatic teardown.
 */
static ngx_http_markdown_streaming_decomp_t *
ngx_http_markdown_streaming_decomp_create_with_origin(
    ngx_pool_t *pool,
    ngx_http_markdown_compression_type_e type,
    size_t max_decompressed_size,
    ngx_http_markdown_decomp_failure_origin_e *origin)
{
    ngx_http_markdown_streaming_decomp_t  *decomp;
    ngx_pool_cleanup_t                    *cln;

    if (origin != NULL) {
        *origin = NGX_HTTP_MD_DECOMP_ORIGIN_NONE;
    }

    if (pool == NULL) {
        if (origin != NULL) {
            *origin = NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
        }
        return NULL;
    }

    decomp = ngx_pcalloc(pool,
        sizeof(ngx_http_markdown_streaming_decomp_t));
    if (decomp == NULL) {
        if (origin != NULL) {
            *origin = NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        }
        return NULL;
    }

    decomp->type = type;
    decomp->max_decompressed_size = max_decompressed_size;
    decomp->total_decompressed = 0;
    decomp->initialized = 0;
    decomp->finished = 0;
    decomp->failure_origin = NGX_HTTP_MD_DECOMP_ORIGIN_NONE;
    decomp->at_gzip_member_boundary = 0;
    decomp->zlib_header_pending = 0;
    decomp->pending_header_len = 0;

    switch (type) {

    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        /*
         * Gzip is unambiguous (magic 0x1f 0x8b), so we initialize
         * eagerly with MAX_WBITS + 16.  Z_STREAM_END completes one gzip
         * member, not necessarily the complete HTTP response; the feed
         * loop resets this inflater when another member follows.
         */
        if (ngx_http_markdown_streaming_decomp_init_zlib(
                decomp, MAX_WBITS + 16)
            != NGX_OK)
        {
            if (origin != NULL) {
                *origin = decomp->failure_origin;
            }
            return NULL;
        }
        break;

    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        /*
         * RFC 9110 defines HTTP "deflate" as the zlib-wrapped format
         * (RFC 1950).  However, many servers send raw deflate
         * (RFC 1951) without the zlib wrapper.  Unlike the buffered
         * path, the streaming path cannot retry once chunks are
         * consumed.  We defer inflateInit2 until the first 2 bytes
         * arrive, then sniff the zlib header to decide:
         *
         *   - zlib-wrapped (valid CMF/FLG): MAX_WBITS
         *   - raw deflate: -MAX_WBITS
         *
         * This matches the behavior of curl/libcurl and zlib's own
         * detection heuristics, covering both the standard-compliant
         * and the common-but-nonstandard server behaviors.
         */
        decomp->zlib_header_pending = 1;
        decomp->pending_header_len = 0;
        break;

#ifdef NGX_HTTP_BROTLI
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        /* One request owns one decoder for the complete Brotli stream. */
        decomp->state.brotli =
            BrotliDecoderCreateInstance(NULL, NULL, NULL);
        if (decomp->state.brotli == NULL) {
            if (origin != NULL) {
                *origin = NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
            }
            return NULL;
        }
        decomp->initialized = 1;
        break;
#endif

    default:
        if (origin != NULL) {
            *origin = NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
        }
        return NULL;
    }

    /* Register pool cleanup */
    cln = ngx_pool_cleanup_add(pool, 0);
    if (cln == NULL) {
        ngx_http_markdown_streaming_decomp_cleanup(
            decomp);
        if (origin != NULL) {
            *origin = NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        }
        return NULL;
    }

    cln->handler =
        ngx_http_markdown_streaming_decomp_cleanup;
    cln->data = decomp;

    return decomp;
}


static __attribute__((unused))
ngx_http_markdown_streaming_decomp_t *
ngx_http_markdown_streaming_decomp_create(
    ngx_pool_t *pool,
    ngx_http_markdown_compression_type_e type,
    size_t max_decompressed_size)
{
    return ngx_http_markdown_streaming_decomp_create_with_origin(
        pool, type, max_decompressed_size, NULL);
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
 * Free a heap-allocated buffer through a pointer-to-pointer,
 * guarding against NULL pointer and setting the pointer to NULL
 * after release to prevent dangling references.
 *
 * Used consistently across inflate, brotli, feed, and finish
 * paths to enforce a single defensive free pattern.
 */
static void
ngx_http_markdown_streaming_decomp_free_heap(u_char **heap_buf_ptr)
{
    if (heap_buf_ptr != NULL && *heap_buf_ptr != NULL) {
        ngx_free(*heap_buf_ptr);
        *heap_buf_ptr = NULL;
    }
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
    size_t max_size,
    ngx_log_t *log)
{
    u_char  *new_buf;
    size_t   old_size;
    size_t   new_size;

    old_size = *buf_size_ptr;

    /* Guard against size_t overflow: old_size * 2 */
    if (old_size > (size_t) -1 / 2) {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown: "
            "expand_buf size overflow, old_size=%uz",
            old_size);
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_ERROR;
    }

    new_size = old_size * 2;
    if (max_size > 0 && new_size > max_size) {
        new_size = max_size;
    }

    new_buf = ngx_alloc(new_size, log);
    if (new_buf == NULL) {
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_ERROR;
    }

    ngx_memcpy(new_buf, *buf_ptr, old_size);

    ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);

    *heap_buf_ptr = new_buf;
    *buf_ptr = new_buf;
    *buf_size_ptr = new_size;
    return NGX_OK;
}


/*
 * Copy heap working buffer back to pool memory and free it.
 *
 * Takes the heap buffer by pointer-to-pointer so the release path
 * stays consistent with free_heap/expand_buf: on every exit the
 * heap allocation is released and *heap_buf_ptr is cleared, which
 * prevents callers from accidentally double-freeing or leaking the
 * old workspace.
 *
 * Returns:
 *   NGX_OK    - success (*buf_ptr updated to pool allocation)
 *   NGX_ERROR - pool allocation failure (heap buffer freed)
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_finalize_buf(
    u_char **heap_buf_ptr,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t produced,
    ngx_pool_t *pool)
{
    u_char  *pool_buf;

    if (produced == 0) {
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        *buf_ptr = NULL;
        *buf_size_ptr = 0;
        return NGX_OK;
    }

    pool_buf = ngx_palloc(pool, produced);
    if (pool_buf == NULL) {
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_ERROR;
    }

    /*
     * free_heap() performs NULL-guarded release; safe to dereference
     * *heap_buf_ptr for the copy because produced > 0 here implies
     * heap_buf_ptr is non-NULL and non-empty.
     */
    ngx_memcpy(pool_buf, *heap_buf_ptr, produced);
    ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);

    *buf_ptr = pool_buf;
    *buf_size_ptr = produced;
    return NGX_OK;
}


/*
 * Reset the gzip inflater after one member reaches Z_STREAM_END while
 * preserving the caller-owned input and output cursors.  inflateReset()
 * retains the MAX_WBITS + 16 attributes selected by inflateInit2().
 * Response-wide decompression accounting lives outside z_stream and is
 * intentionally not reset here.
 *
 * Returns NGX_OK on success.  On failure, releases the current heap
 * workspace and returns NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR because an
 * inflater reset failure is a zlib runtime error, not malformed input.
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_reset_gzip_member(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **heap_buf_ptr,
    ngx_log_t *log)
{
    Bytef  *next_in;
    Bytef  *next_out;
    uInt    avail_in;
    uInt    avail_out;
    int     zrc;

    next_in = decomp->state.zlib.next_in;
    avail_in = decomp->state.zlib.avail_in;
    next_out = decomp->state.zlib.next_out;
    avail_out = decomp->state.zlib.avail_out;

    zrc = inflateReset(&decomp->state.zlib);
    if (zrc != Z_OK) {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown: gzip member inflateReset error %d", zrc);
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
    }

    decomp->state.zlib.next_in = next_in;
    decomp->state.zlib.avail_in = avail_in;
    decomp->state.zlib.next_out = next_out;
    decomp->state.zlib.avail_out = avail_out;
    decomp->at_gzip_member_boundary = 1;

    return NGX_OK;
}


/*
 * Check for trailing bytes after Z_STREAM_END for deflate streams.
 * Deflate (zlib-wrapped or raw) does not support concatenated members;
 * any remaining avail_in after Z_STREAM_END is trailing data that does
 * not belong to the stream.
 *
 * Returns NGX_OK if no trailing bytes, NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
 * if trailing bytes detected.
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_check_deflate_trailing(
    const ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **heap_buf_ptr,
    ngx_log_t *log)
{
    if (decomp->state.zlib.avail_in > 0) {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown: "
            "deflate stream ended with %ud trailing bytes "
            "(avail_in > 0 after Z_STREAM_END)",
            decomp->state.zlib.avail_in);
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
    }

    return NGX_OK;
}


/*
 * Grow the output buffer when avail_out == 0.
 *
 * Returns NGX_OK on success, NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED
 * if the budget is exhausted, or NGX_ERROR on allocation/conversion failure.
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_grow_output_buf(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    u_char **heap_buf_ptr,
    int *using_heap_ptr,
    ngx_log_t *log)
{
    size_t  remaining;
    size_t  old_size;

    remaining = 0;
    if (decomp->max_decompressed_size > 0) {
        remaining = decomp->max_decompressed_size
                    - decomp->total_decompressed;
        if (*buf_size_ptr >= remaining) {
            ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
            return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
        }
    }

    old_size = *buf_size_ptr;

    if (ngx_http_markdown_streaming_decomp_expand_buf(
            heap_buf_ptr, buf_ptr, buf_size_ptr, remaining, log)
        != NGX_OK)
    {
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }

    *using_heap_ptr = 1;

    decomp->state.zlib.next_out = *buf_ptr + old_size;
    {
        uInt  avail_out;

        if (ngx_http_markdown_streaming_decomp_size_to_uint(
                *buf_size_ptr - old_size, &avail_out))
        {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown: "
                "expanded buffer free space %uz exceeds "
                "zlib uInt max",
                *buf_size_ptr - old_size);
            ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
            decomp->failure_origin =
                NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
            return NGX_ERROR;
        }
        decomp->state.zlib.avail_out = avail_out;
    }

    return NGX_OK;
}


/*
 * Step return codes:
 *   1  - done (Z_STREAM_END or avail_in == 0)
 *   0  - continue iterating
 *  <0  - NGX/decompression error code (heap_buf freed if needed)
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
    int      zrc;
    uInt     avail_in_before;
    uInt     avail_out_before;
    uLong    total_out_before;

    avail_in_before = decomp->state.zlib.avail_in;
    avail_out_before = decomp->state.zlib.avail_out;
    total_out_before = decomp->state.zlib.total_out;

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
            "markdown: "
            "inflate error %d", zrc);
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        if (zrc == Z_DATA_ERROR) {
            return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
        }
        return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
    }

    /*
     * No-progress guard: if inflate returned Z_BUF_ERROR but neither
     * input was consumed nor output was produced, continuing the loop
     * would spin forever with the same state.  This can happen with
     * malformed deflate streams that leave the decoder in a state
     * where it cannot make progress but does not report a hard error.
     * Detect the stuck condition and fail immediately.
     */
    if (zrc == Z_BUF_ERROR
        && decomp->state.zlib.avail_in == avail_in_before
        && decomp->state.zlib.avail_out == avail_out_before
        && decomp->state.zlib.total_out == total_out_before)
    {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown: "
            "inflate no-progress (Z_BUF_ERROR with no state change)");
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
    }

    *out_produced = *buf_size_ptr
                    - decomp->state.zlib.avail_out;

    if (ngx_http_markdown_streaming_decomp_check_limit(
            decomp, *out_produced))
    {
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown: "
            "decompressed size %uz exceeds limit %uz",
            decomp->total_decompressed + *out_produced,
            decomp->max_decompressed_size);
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    if (zrc == Z_STREAM_END) {
        if (decomp->type != NGX_HTTP_MARKDOWN_COMPRESSION_GZIP) {
            ngx_int_t  check_rc;

            check_rc = ngx_http_markdown_streaming_decomp_check_deflate_trailing(
                decomp, heap_buf_ptr, log);
            if (check_rc != NGX_OK) {
                return (int) check_rc;
            }
            decomp->finished = 1;
            return 1;
        }

        {
            ngx_int_t  reset_rc;

            reset_rc =
                ngx_http_markdown_streaming_decomp_reset_gzip_member(
                    decomp, heap_buf_ptr, log);
            if (reset_rc != NGX_OK) {
                return (int) reset_rc;
            }
        }

        if (decomp->state.zlib.avail_in == 0) {
            return 1;
        }

        /* Remaining compressed bytes belong to the next gzip member. */
        decomp->at_gzip_member_boundary = 0;
    }

    if (decomp->state.zlib.avail_in == 0) {
        return 1;
    }

    if (decomp->state.zlib.avail_out == 0) {
        ngx_int_t  grow_rc;

        grow_rc = ngx_http_markdown_streaming_decomp_grow_output_buf(
            decomp, buf_ptr, buf_size_ptr, heap_buf_ptr,
            using_heap_ptr, log);
        if (grow_rc != NGX_OK) {
            return (grow_rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED)
                ? (int) grow_rc : -1;
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
 *   NGX_OK - success (produced written to *out_produced)
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED - size limit hit
 *   NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR - malformed compressed data
 *   NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR - unexpected zlib runtime error
 *   NGX_ERROR - allocation or narrowing failure
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

    heap_buf = *buf_ptr;
    using_heap = 1;

    for ( ;; ) {
        step_rc =
            ngx_http_markdown_streaming_decomp_inflate_step(
                decomp, buf_ptr, buf_size_ptr,
                out_produced, &heap_buf,
                &using_heap, log);

        if (step_rc < 0) {
            return (ngx_int_t) step_rc;
        }

        if (step_rc > 0) {
            break;
        }
    }

    if (using_heap
        && ngx_http_markdown_streaming_decomp_finalize_buf(
               &heap_buf, buf_ptr, buf_size_ptr,
               *out_produced, pool)
           != NGX_OK)
    {
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        return NGX_ERROR;
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
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED - size limit hit
 *   NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR - malformed data
 *   NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT - incomplete stream
 *   NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR - I/O error
 *   NGX_ERROR - allocation or internal failure (failure_origin set)
 */

/*
 * Three-way error classification for BrotliDecoderErrorCode.
 *
 * The classification uses integer-range comparisons exclusively to
 * remain compatible with Brotli 1.0.9 (Ubuntu 22.04), which does not
 * expose all named enum constants present in newer releases.
 *
 * Frozen ranges (from the Brotli specification and brotli/decode.h):
 *   FORMAT:     codes -1 through -17
 *   ALLOCATION: codes -21 through -30
 *   INTERNAL:   codes -18 through -20, -31, and any unknown/out-of-range
 */
typedef enum {
    NGX_HTTP_MARKDOWN_BROTLI_ERROR_FORMAT = 0,
    NGX_HTTP_MARKDOWN_BROTLI_ERROR_ALLOCATION,
    NGX_HTTP_MARKDOWN_BROTLI_ERROR_INTERNAL
} ngx_http_markdown_brotli_error_class_e;

static ngx_http_markdown_brotli_error_class_e
ngx_http_markdown_brotli_classify_error(BrotliDecoderErrorCode code)
{
    int  c;

    c = (int) code;

    /* FORMAT: codes -1 through -17 */
    if (c >= -17 && c <= -1) {
        return NGX_HTTP_MARKDOWN_BROTLI_ERROR_FORMAT;
    }

    /* ALLOCATION: codes -21 through -30 */
    if (c >= -30 && c <= -21) {
        return NGX_HTTP_MARKDOWN_BROTLI_ERROR_ALLOCATION;
    }

    /*
     * INTERNAL: codes -18 through -20 (COMPOUND_DICTIONARY,
     * DICTIONARY_NOT_SET, INVALID_ARGUMENTS), -31 (UNREACHABLE),
     * and any unknown/out-of-range value.
     */
    return NGX_HTTP_MARKDOWN_BROTLI_ERROR_INTERNAL;
}


static int
ngx_http_markdown_streaming_decomp_brotli_error(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **heap_buf_ptr,
    ngx_log_t *log,
    const char *stage)
{
    BrotliDecoderErrorCode                  err_code;
    ngx_http_markdown_brotli_error_class_e  err_class;
    const char                             *err_str;

    err_code = BrotliDecoderGetErrorCode(decomp->state.brotli);
    err_class = ngx_http_markdown_brotli_classify_error(err_code);
    err_str = BrotliDecoderErrorString(err_code);

    ngx_log_error(NGX_LOG_ERR, log, 0,
        "markdown: reason=brotli_decode_error "
        "brotli %s decode error: code=%d, %s",
        stage, (int) err_code, err_str);
    ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);

    if (err_class == NGX_HTTP_MARKDOWN_BROTLI_ERROR_FORMAT) {
        return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
    }

    decomp->failure_origin =
        (err_class == NGX_HTTP_MARKDOWN_BROTLI_ERROR_ALLOCATION)
        ? NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION
        : NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
    return NGX_ERROR;
}


/*
 * Brotli exact-budget completion probe (Scenario B).
 *
 * Called from brotli_step() when NEEDS_MORE_OUTPUT arrives with produced
 * == remaining exactly.  Expands the transient workspace by 1 byte and
 * invokes the decoder once more.  Interprets the probe result per the
 * strict exact-budget priority order:
 *
 *  1. ERROR        → preserve classified decoder error
 *  2. SUCCESS+trail→ FORMAT_ERROR / brotli_trailing_data
 *  3. SUCCESS+0    → finished, return produced=remaining (exact budget)
 *  4. SUCCESS+>0   → BUDGET_EXCEEDED (produced beyond budget)
 *  5. produced>0   → BUDGET_EXCEEDED
 *  6. NEEDS_MORE_OUTPUT → BUDGET_EXCEEDED
 *  7. NEEDS_MORE_INPUT+avail_in==0+produced==0 → continue (wait for chunk)
 *
 * Returns the same codes as brotli_step:
 *   1  - done (stream complete or awaiting next chunk)
 *   NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED
 *   NGX_ERROR - allocation/internal
 */
static int
ngx_http_markdown_streaming_decomp_brotli_probe(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t *out_produced,
    u_char **heap_buf_ptr,
    int *using_heap_ptr,
    size_t remaining,
    ngx_log_t *log)
{
    u_char              *new_buf;
    size_t               new_size;
    size_t               old_size;
    BrotliDecoderResult  brc;
    size_t               probe_produced;

    old_size = *buf_size_ptr;

    /* Expand workspace by exactly 1 byte for the probe */
    new_size = old_size + 1;
    new_buf = ngx_alloc(new_size, log);
    if (new_buf == NULL) {
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        decomp->finished = 1;
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }

    ngx_memcpy(new_buf, *buf_ptr, old_size);
    ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);

    *heap_buf_ptr = new_buf;
    *buf_ptr = new_buf;
    *buf_size_ptr = new_size;
    *using_heap_ptr = 1;

    /* Point decoder output at the single probe byte */
    decomp->brotli_next_out = new_buf + old_size;
    decomp->brotli_avail_out = 1;

    brc = BrotliDecoderDecompressStream(
        decomp->state.brotli,
        &decomp->brotli_avail_in,
        &decomp->brotli_next_in,
        &decomp->brotli_avail_out,
        &decomp->brotli_next_out, NULL);

    probe_produced = 1 - decomp->brotli_avail_out;

    /* Priority 1: ERROR → preserve classified error */
    if (brc == BROTLI_DECODER_RESULT_ERROR) {
        return ngx_http_markdown_streaming_decomp_brotli_error(
            decomp, heap_buf_ptr, log, "probe");
    }

    /* Priority 2: SUCCESS */
    if (brc == BROTLI_DECODER_RESULT_SUCCESS) {
        /* 2a: trailing data → FORMAT_ERROR */
        if (decomp->brotli_avail_in > 0) {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown: reason=brotli_trailing_data "
                "brotli trailing data: "
                "%uz bytes after stream completion",
                decomp->brotli_avail_in);
            ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
            return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
        }
        /* 2b: SUCCESS + produced==0 → stream complete */
        if (probe_produced == 0) {
            decomp->finished = 1;
            /* Retain exact-budget bytes (remaining) as output */
            *out_produced = remaining;
            return 1;
        }
        /* 2c: SUCCESS + produced>0 → BUDGET_EXCEEDED */
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    /* Priority 3/5: produced>0 (non-SUCCESS/non-ERROR) → BUDGET_EXCEEDED */
    if (probe_produced > 0) {
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    /* Priority 4: NEEDS_MORE_OUTPUT → BUDGET_EXCEEDED */
    if (brc == BROTLI_DECODER_RESULT_NEEDS_MORE_OUTPUT) {
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    if (brc == BROTLI_DECODER_RESULT_NEEDS_MORE_INPUT
        && decomp->brotli_avail_in == 0)
    {
        *out_produced = remaining;
        return 1;
    }

    ngx_log_error(NGX_LOG_ERR, log, 0,
        "markdown: brotli exact-budget probe returned invalid state "
        "(result=%d, avail_in=%uz)",
        (int) brc, decomp->brotli_avail_in);
    ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
    decomp->failure_origin = NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
    return NGX_ERROR;
}


static ngx_int_t
ngx_http_markdown_streaming_decomp_brotli_check_progress(
    ngx_http_markdown_streaming_decomp_t *decomp,
    BrotliDecoderResult brc,
    size_t previous_input,
    size_t previous_output,
    u_char **heap_buf_ptr,
    ngx_log_t *log)
{
    size_t  consumed;
    size_t  produced;

    consumed = previous_input - decomp->brotli_avail_in;
    produced = previous_output - decomp->brotli_avail_out;
    if (consumed > 0 || produced > 0
        || brc == BROTLI_DECODER_RESULT_SUCCESS
        || brc == BROTLI_DECODER_RESULT_ERROR
        || (brc == BROTLI_DECODER_RESULT_NEEDS_MORE_INPUT
            && decomp->brotli_avail_in == 0))
    {
        return NGX_OK;
    }

    ngx_log_error(NGX_LOG_ERR, log, 0,
        "markdown: reason=brotli_no_progress "
        "brotli decoder consumed no input and produced no output "
        "(result=%d)",
        (int) brc);
    ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
    return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
}


static int
ngx_http_markdown_streaming_decomp_brotli_expand(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t *out_produced,
    u_char **heap_buf_ptr,
    int *using_heap_ptr,
    ngx_log_t *log)
{
    size_t  old_size;
    size_t  remaining;

    old_size = *buf_size_ptr;
    remaining = 0;
    if (decomp->max_decompressed_size > 0) {
        remaining = decomp->max_decompressed_size
                    - decomp->total_decompressed;
        if (old_size >= remaining) {
            if (old_size == remaining && remaining < (size_t) -1) {
                return ngx_http_markdown_streaming_decomp_brotli_probe(
                    decomp, buf_ptr, buf_size_ptr, out_produced,
                    heap_buf_ptr, using_heap_ptr, remaining, log);
            }
            ngx_log_error(NGX_LOG_WARN, log, 0,
                "markdown: reason=brotli_budget_exceeded "
                "brotli output exceeds the decompression budget");
            ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
            return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
        }
    }

    if (ngx_http_markdown_streaming_decomp_expand_buf(
            heap_buf_ptr, buf_ptr, buf_size_ptr, remaining, log)
        != NGX_OK)
    {
        decomp->finished = 1;
        decomp->failure_origin = NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }

    *using_heap_ptr = 1;
    decomp->brotli_next_out = *buf_ptr + old_size;
    decomp->brotli_avail_out = *buf_size_ptr - old_size;
    return 0;
}


/*
 * Core brotli iteration: single call to BrotliDecoderDecompressStream
 * with error/limit/expand handling.
 *
 * Returns:
 *  1  - done (SUCCESS or avail_in == 0)
 *  0  - continue iterating
 *  NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR - format error
 *  NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED - budget exceeded
 *  NGX_ERROR - allocation or internal error (heap_buf freed,
 *              failure_origin set before return)
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
    size_t               prev_avail_in;
    size_t               prev_avail_out;

    avail_in = decomp->brotli_avail_in;
    next_in = decomp->brotli_next_in;
    avail_out = decomp->brotli_avail_out;
    next_out = decomp->brotli_next_out;

    /* Save cursors before decode for no-progress guard */
    prev_avail_in = avail_in;
    prev_avail_out = avail_out;

    brc = BrotliDecoderDecompressStream(
        decomp->state.brotli,
        &avail_in, &next_in,
        &avail_out, &next_out, NULL);

    decomp->brotli_avail_in = avail_in;
    decomp->brotli_next_in = next_in;
    decomp->brotli_avail_out = avail_out;
    decomp->brotli_next_out = next_out;

    if (ngx_http_markdown_streaming_decomp_brotli_check_progress(
            decomp, brc, prev_avail_in, prev_avail_out,
            heap_buf_ptr, log)
        != NGX_OK)
    {
        return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
    }

    if (brc == BROTLI_DECODER_RESULT_ERROR) {
        return ngx_http_markdown_streaming_decomp_brotli_error(
            decomp, heap_buf_ptr, log, "stream");
    }

    *out_produced = *buf_size_ptr - decomp->brotli_avail_out;

    if (ngx_http_markdown_streaming_decomp_check_limit(
            decomp, *out_produced))
    {
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown: reason=brotli_budget_exceeded "
            "decompressed size %uz exceeds limit %uz",
            decomp->total_decompressed + *out_produced,
            decomp->max_decompressed_size);
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    if (brc == BROTLI_DECODER_RESULT_SUCCESS) {
        if (decomp->brotli_avail_in > 0) {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown: reason=brotli_trailing_data "
                "brotli trailing data: "
                "%uz bytes after stream completion",
                decomp->brotli_avail_in);
            ngx_http_markdown_streaming_decomp_free_heap(
                heap_buf_ptr);
            return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
        }
        decomp->finished = 1;
        return 1;
    }

    if (brc == BROTLI_DECODER_RESULT_NEEDS_MORE_OUTPUT) {
        return ngx_http_markdown_streaming_decomp_brotli_expand(
            decomp, buf_ptr, buf_size_ptr, out_produced,
            heap_buf_ptr, using_heap_ptr, log);
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
    heap_buf = *buf_ptr;
    using_heap = 1;

    for ( ;; ) {
        step_rc =
            ngx_http_markdown_streaming_decomp_brotli_step(
                decomp,
                buf_ptr, buf_size_ptr,
                out_produced, &heap_buf,
                &using_heap, log);

        if (step_rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED
            || step_rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
            || step_rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT
            || step_rc == NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR)
        {
            return (ngx_int_t) step_rc;
        }

        if (step_rc < 0) {
            /* Any other negative: system-level failure */
            return NGX_ERROR;
        }

        if (step_rc > 0) {
            break;
        }
    }

    if (using_heap) {
        if (ngx_http_markdown_streaming_decomp_finalize_buf(
                &heap_buf, buf_ptr, buf_size_ptr,
                *out_produced, pool)
            != NGX_OK)
        {
            /*
             * Pool-copy failure after decode completed: the decoder
             * has consumed all input for this call.  Mark as
             * non-retryable and set allocation origin.
             */
            decomp->finished = 1;
            decomp->failure_origin =
                NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
            return NGX_ERROR;
        }
    }

    return NGX_OK;
}
#endif


/*
 * Run the zlib inflate loop after the caller has configured
 * decomp->state.zlib.next_in and avail_in.
 *
 * Sets next_out/avail_out on the zlib stream, validates narrowing
 * from size_t to uInt, then delegates to the inflate loop.
 *
 * Returns:
 *   NGX_OK    - success (produced written to *out_produced)
 *   NGX_ERROR - setup or inflate error
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED - size limit hit
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_feed_zlib(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t *out_produced,
    ngx_pool_t *pool,
    ngx_log_t *log)
{
    decomp->state.zlib.next_out = *buf_ptr;
    if (ngx_http_markdown_streaming_decomp_size_to_uint(
            *buf_size_ptr, &decomp->state.zlib.avail_out))
    {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown: "
            "buffer size %uz exceeds zlib uInt max",
            *buf_size_ptr);
        ngx_http_markdown_streaming_decomp_free_heap(buf_ptr);
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
        return NGX_ERROR;
    }

    return ngx_http_markdown_streaming_decomp_inflate_loop(
        decomp, buf_ptr, buf_size_ptr, out_produced,
        pool, log);
}

typedef struct {
    u_char      **buf_ptr;
    size_t       *buf_size_ptr;
    size_t       *out_produced;
    ngx_pool_t   *pool;
    ngx_log_t    *log;
} ngx_http_markdown_streaming_decomp_feed_ctx_t;

static ngx_int_t
ngx_http_markdown_streaming_decomp_feed_case_zlib(
    ngx_http_markdown_streaming_decomp_t *decomp,
    const u_char *in_data,
    size_t in_len,
    const ngx_http_markdown_streaming_decomp_feed_ctx_t *ctx)
{
    /*
     * zlib's z_stream.next_in type depends on ZLIB_CONST.
     * On builds without ZLIB_CONST it is Bytef *, but inflate()
     * reads from the buffer only.
     */
    decomp->state.zlib.next_in = (Bytef *) in_data; /* NOSONAR: zlib z_stream.next_in requires Bytef* (non-const) without ZLIB_CONST */
    if (ngx_http_markdown_streaming_decomp_size_to_uint(
            in_len, &decomp->state.zlib.avail_in))
    {
        ngx_log_error(NGX_LOG_ERR, ctx->log, 0,
            "markdown: "
            "input length %uz exceeds zlib uInt max",
            in_len);
        ngx_http_markdown_streaming_decomp_free_heap(ctx->buf_ptr);
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
        return NGX_ERROR;
    }

    return ngx_http_markdown_streaming_decomp_feed_zlib(
        decomp, ctx->buf_ptr, ctx->buf_size_ptr,
        ctx->out_produced, ctx->pool, ctx->log);
}

#ifdef NGX_HTTP_BROTLI
static ngx_int_t
ngx_http_markdown_streaming_decomp_feed_case_brotli(
    ngx_http_markdown_streaming_decomp_t *decomp,
    const u_char *in_data,
    size_t in_len,
    const ngx_http_markdown_streaming_decomp_feed_ctx_t *ctx)
{
    decomp->brotli_next_in = in_data;
    decomp->brotli_avail_in = in_len;

    return ngx_http_markdown_streaming_decomp_brotli_loop(
        decomp, ctx->buf_ptr, ctx->buf_size_ptr,
        ctx->out_produced, ctx->pool, ctx->log);
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
static ngx_int_t ngx_http_markdown_streaming_decomp_apply_limits(
    ngx_http_markdown_streaming_decomp_t *decomp,
    size_t produced,
    u_char **buf_ptr,
    ngx_log_t *log);


static ngx_int_t
ngx_http_markdown_streaming_decomp_prepare_input(
    ngx_http_markdown_streaming_decomp_t *decomp,
    const u_char **in_data,
    size_t *in_len,
    u_char **combined_input,
    ngx_flag_t *input_ready,
    ngx_log_t *log)
{
    size_t  header_consumed;
    size_t  combined_len;

    *input_ready = 1;
    if (!decomp->zlib_header_pending) {
        return NGX_OK;
    }

    header_consumed = 0;
    if (ngx_http_markdown_streaming_decomp_feed_header(
            decomp, *in_data, *in_len, &header_consumed, log)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    if (decomp->zlib_header_pending) {
        *input_ready = 0;
        return NGX_OK;
    }

    *in_data += header_consumed;
    *in_len -= header_consumed;
    combined_len = 2 + *in_len;
    *combined_input = ngx_alloc(combined_len, log);
    if (*combined_input == NULL) {
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }

    ngx_memcpy(*combined_input, decomp->pending_header, 2);
    if (*in_len > 0) {
        ngx_memcpy(*combined_input + 2, *in_data, *in_len);
    }
    *in_data = *combined_input;
    *in_len = combined_len;

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_streaming_decomp_workspace_size(
    const ngx_http_markdown_streaming_decomp_t *decomp,
    size_t in_len,
    size_t *buf_size)
{
    size_t  remaining;

    if (in_len > (size_t) -1 / 4) {
        *buf_size = (size_t) -1;
    } else {
        *buf_size = in_len * 4;
    }
    if (*buf_size < 4096) {
        *buf_size = 4096;
    }

    if (decomp->max_decompressed_size == 0) {
        return NGX_OK;
    }
    if (decomp->total_decompressed > decomp->max_decompressed_size) {
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    remaining = decomp->max_decompressed_size
                - decomp->total_decompressed;

    /*
     * Gzip permits concatenated empty members.  Reserve one bounded probe
     * byte when the normal workspace reaches the remaining response budget:
     * an empty member can consume its header/trailer without output, while
     * any produced probe byte is rejected by check_limit() before exposure.
     *
     * Brotli uses the same probe for exact-budget completion detection:
     * a stream at exact budget that hasn't finished needs one probe byte
     * to determine whether the decoder completes without further output
     * (success) or produces additional bytes (budget exceeded).
     */
    if ((decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP
#ifdef NGX_HTTP_BROTLI
         || (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
             && !decomp->finished)
#endif
        )
        && *buf_size >= remaining && remaining < (size_t) -1)
    {
        *buf_size = remaining + 1;
    } else if (remaining == 0) {
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    } else if (*buf_size > remaining) {
        *buf_size = remaining;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_streaming_decomp_finished_input(
    const ngx_http_markdown_streaming_decomp_t *decomp,
    size_t in_len,
    ngx_log_t *log)
{
    if (!decomp->finished) {
        return NGX_DECLINED;
    }
    if (in_len == 0
        || decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP)
    {
        return NGX_OK;
    }

    ngx_log_error(NGX_LOG_ERR, log, 0,
        "markdown: %s trailing data after stream end "
        "(%uz bytes in finished state)",
        (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI)
            ? "brotli" : "deflate",
        in_len);
    return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
}


static ngx_int_t
ngx_http_markdown_streaming_decomp_allocate_workspace(
    ngx_http_markdown_streaming_decomp_t *decomp,
    size_t in_len,
    u_char **buf,
    size_t *buf_size,
    u_char **combined_input,
    ngx_log_t *log)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_streaming_decomp_workspace_size(
        decomp, in_len, buf_size);
    if (rc != NGX_OK) {
        ngx_http_markdown_streaming_decomp_free_heap(combined_input);
        return rc;
    }

    *buf = ngx_alloc(*buf_size, log);
    if (*buf != NULL) {
        return NGX_OK;
    }

    ngx_http_markdown_streaming_decomp_free_heap(combined_input);
    decomp->failure_origin = NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
    return NGX_ERROR;
}


static ngx_int_t
ngx_http_markdown_streaming_decomp_decode_chunk(
    ngx_http_markdown_streaming_decomp_t *decomp,
    const u_char *in_data,
    size_t in_len,
    const ngx_http_markdown_streaming_decomp_feed_ctx_t *feed_ctx,
    u_char **combined_input)
{
    ngx_int_t  rc;

    if (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP
        || decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE)
    {
        rc = ngx_http_markdown_streaming_decomp_feed_case_zlib(
            decomp, in_data, in_len, feed_ctx);
#ifdef NGX_HTTP_BROTLI
    } else if (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI) {
        rc = ngx_http_markdown_streaming_decomp_feed_case_brotli(
            decomp, in_data, in_len, feed_ctx);
#endif
    } else {
        ngx_free(*feed_ctx->buf_ptr);
        *feed_ctx->buf_ptr = NULL;
        rc = NGX_DECLINED;
    }

    ngx_http_markdown_streaming_decomp_free_heap(combined_input);
    return rc;
}


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
    u_char  *combined_input = NULL;
    ngx_flag_t  input_ready;
    ngx_int_t   rc;
    ngx_http_markdown_streaming_decomp_feed_ctx_t  feed_ctx;

    if (decomp == NULL || out_data == NULL || out_len == NULL)
    {
        return NGX_ERROR;
    }

    /*
     * Per-call failure origin lifecycle: reset to NONE so that no
     * stale origin from a prior call can leak into the mapper if
     * this call returns bare NGX_ERROR.
     */
    decomp->failure_origin = NGX_HTTP_MD_DECOMP_ORIGIN_NONE;

    /*
     * Defensive baseline: clear the caller-facing output slots before
     * any work begins so every error path (including the budget/alloc
     * early returns below) is guaranteed to emit empty output and never
     * leak stale caller-supplied values.
     */
    *out_data = NULL;
    *out_len = 0;

    rc = ngx_http_markdown_streaming_decomp_finished_input(
        decomp, in_len, log);
    if (rc != NGX_DECLINED) {
        return rc;
    }

    /* Empty input is a no-op */
    if (in_data == NULL || in_len == 0) {
        return NGX_OK;
    }

    if (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP
        && decomp->at_gzip_member_boundary)
    {
        /* The reset inflater is about to consume a later gzip member. */
        decomp->at_gzip_member_boundary = 0;
    }

    /*
     * Deferred deflate initialization: if we are still sniffing the
     * zlib header, accumulate up to 2 bytes before initializing the
     * inflate stream.  Once initialized, the pending header bytes
     * are prepended to the current (or next) input chunk so inflate
     * sees the complete stream from the start.
     */
    rc = ngx_http_markdown_streaming_decomp_prepare_input(
        decomp, &in_data, &in_len, &combined_input, &input_ready, log);
    if (rc != NGX_OK) {
        return rc;
    }
    if (!input_ready) {
        return NGX_OK;
    }

    if (!decomp->initialized) {
        ngx_http_markdown_streaming_decomp_free_heap(&combined_input);
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_streaming_decomp_allocate_workspace(
        decomp, in_len, &buf, &buf_size, &combined_input, log);
    if (rc != NGX_OK) {
        return rc;
    }

    produced = 0;
    feed_ctx.buf_ptr = &buf;
    feed_ctx.buf_size_ptr = &buf_size;
    feed_ctx.out_produced = &produced;
    feed_ctx.pool = pool;
    feed_ctx.log = log;

    rc = ngx_http_markdown_streaming_decomp_decode_chunk(
        decomp, in_data, in_len, &feed_ctx, &combined_input);
    if (rc != NGX_OK) {
        return rc;
    }

    /*
     * Post-decode size validation: check for overflow and budget limits.
     * Returns NGX_OK on success, or the appropriate error code.
     * On success, total_decompressed is updated.
     *
     * Ownership: the decode loop's finalize_buf() has already transferred
     * the heap workspace to pool memory. apply_limits() validates only
     * and does not free the buffer — pool reclaim handles its lifetime.
     */
    {
        ngx_int_t  limit_rc;

        limit_rc = ngx_http_markdown_streaming_decomp_apply_limits(
            decomp, produced, &buf, log);
        if (limit_rc != NGX_OK) {
            return limit_rc;
        }
    }

    *out_data = buf;
    *out_len = produced;
    return NGX_OK;
}


/*
 * Validate decompressed size against overflow and budget limits after
 * a decode pass. On success, updates decomp->total_decompressed and
 * returns NGX_OK. On failure, returns the appropriate error code
 * (NGX_ERROR or NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) without
 * freeing the buffer.
 *
 * Ownership note: when called from ngx_http_markdown_streaming_decomp_feed,
 * the decode loop has already called finalize_buf() which transferred
 * the heap workspace to pool memory. The buffer at *buf_ptr is
 * therefore pool-allocated and must NOT be freed here — the pool
 * reclaim handles its lifetime.
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_apply_limits(
    ngx_http_markdown_streaming_decomp_t *decomp,
    size_t produced,
    u_char **buf_ptr,
    ngx_log_t *log)
{
    (void) buf_ptr;

    if (decomp->total_decompressed > NGX_MAX_SIZE_T_VALUE - produced) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown: "
            "decompressed size overflow, total=%uz produced=%uz",
            decomp->total_decompressed,
            produced);
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
        return NGX_ERROR;
    }

    decomp->total_decompressed += produced;
    if (decomp->max_decompressed_size > 0
        && decomp->total_decompressed
           > decomp->max_decompressed_size)
    {
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown: "
            "decompressed size %uz exceeds limit %uz",
            decomp->total_decompressed,
            decomp->max_decompressed_size);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_streaming_decomp_finish_zlib_expand(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **heap_buf_ptr,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    ngx_flag_t *using_heap_ptr,
    ngx_log_t *log)
{
    size_t   old_size;
    uInt     expand_out;

    old_size = *buf_size_ptr;
    if (decomp->max_decompressed_size > 0
        && old_size >= decomp->max_decompressed_size
                       - decomp->total_decompressed)
    {
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }
    /*
     * expand_buf() frees any previous heap buffer if expansion fails,
     * so we can safely return directly on NGX_ERROR here.
     */
    if (ngx_http_markdown_streaming_decomp_expand_buf(
            heap_buf_ptr, buf_ptr, buf_size_ptr,
            decomp->max_decompressed_size > 0
            ? decomp->max_decompressed_size
              - decomp->total_decompressed
            : 0,
            log)
        != NGX_OK)
    {
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }

    *using_heap_ptr = 1;
    decomp->state.zlib.next_out = *buf_ptr + old_size;
    if (ngx_http_markdown_streaming_decomp_size_to_uint(
            *buf_size_ptr - old_size, &expand_out))
    {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown: "
            "finish expand %uz exceeds "
            "zlib uInt max",
            *buf_size_ptr - old_size);
        ngx_http_markdown_streaming_decomp_free_heap(heap_buf_ptr);
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
        return NGX_ERROR;
    }
    decomp->state.zlib.avail_out = expand_out;

    return NGX_OK;
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
    ngx_int_t    rc;

    /*
     * Track the caller-supplied buffer (allocated via ngx_alloc in
     * finish()) as the initial heap workspace.  This ensures
     * finalize_buf() always transfers produced bytes to pool memory
     * and releases the heap allocation, regardless of whether
     * expansion occurred.
     */
    heap_buf = *buf_ptr;
    using_heap = 1;

    decomp->state.zlib.next_in = Z_NULL;
    decomp->state.zlib.avail_in = 0;
    decomp->state.zlib.next_out = *buf_ptr;
    if (ngx_http_markdown_streaming_decomp_size_to_uint(
            *buf_size_ptr, &decomp->state.zlib.avail_out))
    {
        ngx_log_error(NGX_LOG_ERR, log, 0,
            "markdown: "
            "finish buffer %uz exceeds zlib uInt max",
            *buf_size_ptr);
        ngx_http_markdown_streaming_decomp_free_heap(&heap_buf);
        *buf_ptr = NULL;
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_INTERNAL;
        return NGX_ERROR;
    }

    for ( ;; ) {
        zrc = inflate(&decomp->state.zlib, Z_FINISH);

        if (zrc != Z_STREAM_END && zrc != Z_OK
            && zrc != Z_BUF_ERROR)
        {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown: "
                "finish inflate error %d", zrc);
            ngx_http_markdown_streaming_decomp_free_heap(
                &heap_buf);
            *buf_ptr = NULL;
            if (zrc == Z_DATA_ERROR) {
                return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
            }
            return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
        }

        *produced_ptr = *buf_size_ptr
                        - decomp->state.zlib.avail_out;

        if (ngx_http_markdown_streaming_decomp_check_limit(
                decomp, *produced_ptr))
        {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                "markdown: "
                "decompressed size %uz exceeds limit %uz",
                decomp->total_decompressed + *produced_ptr,
                decomp->max_decompressed_size);
            ngx_http_markdown_streaming_decomp_free_heap(
                &heap_buf);
            *buf_ptr = NULL;
            return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
        }

        if (zrc == Z_STREAM_END) {
            if (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP) {
                decomp->at_gzip_member_boundary = 1;
            }
            decomp->finished = 1;
            break;
        }

        if (zrc == Z_BUF_ERROR
            && decomp->state.zlib.avail_out != 0)
        {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                "markdown: "
                "finish inflate incomplete stream");
            ngx_http_markdown_streaming_decomp_free_heap(
                &heap_buf);
            *buf_ptr = NULL;
            return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
        }

        if (decomp->state.zlib.avail_out != 0) {
            continue;
        }

        rc = ngx_http_markdown_streaming_decomp_finish_zlib_expand(
                 decomp, &heap_buf, buf_ptr, buf_size_ptr,
                 &using_heap, log);
        if (rc != NGX_OK) {
            *buf_ptr = NULL;
            return rc;
        }
    }

    /*
     * Always finalize: transfer produced bytes to pool memory (or
     * free the heap buffer if nothing was produced).  This guarantees
     * the caller receives either a pool-owned buffer or NULL/0, never
     * a leaked raw heap pointer.
     */
    if (ngx_http_markdown_streaming_decomp_finalize_buf(
            &heap_buf, buf_ptr, buf_size_ptr,
            *produced_ptr, pool)
        != NGX_OK)
    {
        *buf_ptr = NULL;
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }

    return NGX_OK;
}


#ifdef NGX_HTTP_BROTLI
/*
 * Finish brotli decompression.  Brotli produces no tail output; checks
 * stream completeness, marks finished, and releases the heap workspace.
 *
 * Returns NGX_OK on success, NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT on
 * incomplete stream.
 */
static ngx_int_t
ngx_http_markdown_streaming_decomp_finish_brotli(
    ngx_http_markdown_streaming_decomp_t *decomp,
    u_char **buf_ptr,
    size_t *produced_ptr,
    ngx_log_t *log)
{
    if (!BrotliDecoderIsFinished(decomp->state.brotli)) {
        ngx_log_error(NGX_LOG_WARN, log, 0,
            "markdown: reason=brotli_truncated_input "
            "brotli stream truncated "
            "(decoder not finished at EOF)");
        ngx_free(*buf_ptr);
        *buf_ptr = NULL;
        return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
    }

    decomp->finished = 1;
    ngx_free(*buf_ptr);
    *buf_ptr = NULL;
    *produced_ptr = 0;
    return NGX_OK;
}
#endif


/*
 * Finish decompression (handle last_buf).
 *
 * Allocates a transient heap workspace via ngx_alloc, then delegates to
 * the format-specific finish path.  On success the workspace is either
 * transferred to pool memory (via finalize_buf) or freed when no output
 * is produced; the caller always receives a pool-owned buffer or
 * NULL/0, never a raw heap pointer.
 *
 * For zlib, flushes remaining data with Z_FINISH.
 * For brotli, checks stream completeness (no tail output).
 *
 * All error paths free the heap workspace before returning.
 *
 * Returns:
 *   NGX_OK - success (*out_data is pool-owned or NULL)
 *   NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT - incomplete stream at EOF
 *   Other decompression/NGX errors from the format-specific finish path
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

    if (decomp == NULL || out_data == NULL || out_len == NULL)
    {
        return NGX_ERROR;
    }

    /*
     * Per-call failure origin lifecycle: reset to NONE so that no
     * stale origin from a prior call can leak into the mapper if
     * this call returns bare NGX_ERROR.
     */
    decomp->failure_origin = NGX_HTTP_MD_DECOMP_ORIGIN_NONE;

    *out_data = NULL;
    *out_len = 0;

    /*
     * A deflate stream can still be waiting for the second format-sniffing
     * header byte.  Feed-time partial input is valid, but once the complete
     * HTTP response reaches EOF there is no more input to resolve the stream,
     * so the terminal classification is truncated input.
     */
    if (!decomp->initialized) {
        if (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE
            && decomp->zlib_header_pending)
        {
            return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
        }
        return NGX_ERROR;
    }

    if (decomp->finished) {
        return NGX_OK;
    }

    if (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP
        && decomp->at_gzip_member_boundary)
    {
        /* EOF immediately after a complete gzip member is valid. */
        decomp->finished = 1;
        return NGX_OK;
    }

    buf_size = 4096;
    buf = ngx_alloc(buf_size, log);
    if (buf == NULL) {
        decomp->failure_origin =
            NGX_HTTP_MD_DECOMP_ORIGIN_ALLOCATION;
        return NGX_ERROR;
    }

    produced = 0;

    if (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP
        || decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE)
    {
        ngx_int_t  finish_rc;

        finish_rc =
            ngx_http_markdown_streaming_decomp_finish_zlib(
                decomp, &buf, &buf_size,
                &produced, pool, log);
        if (finish_rc != NGX_OK) {
            /*
             * finish_zlib frees the heap buffer on all error paths
             * and clears *buf_ptr, so buf is already NULL here.
             */
            buf = NULL;
            return finish_rc;
        }
    }
#ifdef NGX_HTTP_BROTLI
    else if (decomp->type == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI)
    {
        ngx_int_t  brotli_finish_rc;

        brotli_finish_rc =
            ngx_http_markdown_streaming_decomp_finish_brotli(
                decomp, &buf, &produced, log);
        if (brotli_finish_rc != NGX_OK) {
            buf = NULL;
            return brotli_finish_rc;
        }
    }
#endif
    else {
        ngx_free(buf);
        buf = NULL;
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
