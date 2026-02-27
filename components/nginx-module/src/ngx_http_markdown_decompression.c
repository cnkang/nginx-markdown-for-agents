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
    
    /* Handle missing or empty Content-Encoding header (Requirement 1.5) */
    if (h == NULL || h->value.len == 0) {
        return NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
    }
    
    /* Check for gzip compression (case-insensitive, Requirement 1.2) */
    if (ngx_strcasecmp(h->value.data, (u_char *) "gzip") == 0) {
        return NGX_HTTP_MARKDOWN_COMPRESSION_GZIP;
    }
    
    /* Check for deflate compression (case-insensitive, Requirement 1.3) */
    if (ngx_strcasecmp(h->value.data, (u_char *) "deflate") == 0) {
        return NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE;
    }
    
    /* Check for brotli compression (case-insensitive, Requirement 1.4) */
    if (ngx_strcasecmp(h->value.data, (u_char *) "br") == 0) {
        return NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI;
    }
    
    /* Unknown or unsupported compression format (Requirement 1.6) */
    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown filter: decompression unsupported, compression=%V, "
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
ngx_http_markdown_chain_size(ngx_chain_t *in)
{
    size_t        size;
    size_t        len;
    ngx_chain_t  *cl;
    
    size = 0;
    
    for (cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf != NULL) {
            len = cl->buf->last - cl->buf->pos;
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
ngx_http_markdown_chain_to_buffer(ngx_chain_t *in, u_char *dest, size_t size)
{
    size_t        copied;
    size_t        len;
    ngx_chain_t  *cl;
    
    copied = 0;
    
    for (cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL) {
            continue;
        }
        
        len = cl->buf->last - cl->buf->pos;
        
        if (copied > size || len > size - copied) {
            return NGX_ERROR;
        }
        
        ngx_memcpy(dest + copied, cl->buf->pos, len);
        copied += len;
    }
    
    return NGX_OK;
}

static ngx_int_t
ngx_http_markdown_calc_output_size(ngx_http_request_t *r, size_t input_size,
                                   size_t max_size, size_t *output_size)
{
    size_t estimated;

    if (max_size == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: invalid max_size=0 for decompression");
        return NGX_ERROR;
    }

    if (input_size > ((size_t) -1) / 10) {
        estimated = max_size;
    } else {
        estimated = input_size * 10;
    }

    if (estimated > max_size) {
        estimated = max_size;
    }

    /*
     * zlib/brotli decoder output counters use unsigned int/size_t combinations.
     * Clamp to UINT_MAX to avoid truncation when assigning `avail_out`.
     */
    if (estimated > (size_t) UINT_MAX) {
        estimated = (size_t) UINT_MAX;
    }

    if (estimated == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: computed decompression buffer size is zero");
        return NGX_ERROR;
    }

    *output_size = estimated;
    return NGX_OK;
}

/*
 * Decompress gzip/deflate compressed data using zlib
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
 * 5. Performs decompression using inflate() with Z_FINISH flag
 * 6. Checks for Z_STREAM_END return value
 * 7. Creates output chain with decompressed data
 * 8. Cleans up with inflateEnd()
 *
 * Parameters:
 *   r    - nginx request structure
 *   type - compression type (GZIP or DEFLATE)
 *   in   - input chain with compressed data
 *   out  - output chain with decompressed data (output parameter)
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on failure
 *
 * Requirements: 2.1, 2.2, 2.3, 9.1, 9.2, 13.1, 13.5, 14.1, 14.2
 */
ngx_int_t
ngx_http_markdown_decompress_gzip(ngx_http_request_t *r,
                                   ngx_http_markdown_compression_type_e type,
                                   ngx_chain_t *in,
                                   ngx_chain_t **out)
{
    z_stream                     stream;
    ngx_buf_t                   *b;
    ngx_chain_t                 *cl;
    u_char                      *input_data;
    size_t                       input_size;
    u_char                      *output_data;
    size_t                       output_size;
    int                          rc;
    int                          window_bits;
    ngx_http_markdown_conf_t    *conf;
    
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    
    /* Log that we're using zlib for decompression (Requirement 13.5) */
    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: using zlib for gzip/deflate decompression, type=%d",
                  type);
    
    /* Collect all input data into a single buffer */
    input_size = ngx_http_markdown_chain_size(in);
    
    /* Validate input size (Requirement 6.5) */
    if (input_size == 0 || input_size == (size_t) -1) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: decompression failed, "
                     "invalid input size, category=conversion");
        return NGX_ERROR;
    }
    
    input_data = ngx_pnalloc(r->pool, input_size);
    if (input_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to allocate input buffer, "
                     "size=%uz, category=system",
                     input_size);
        return NGX_ERROR;
    }
    
    rc = ngx_http_markdown_chain_to_buffer(in, input_data, input_size);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to collect input data, "
                     "category=system");
        return NGX_ERROR;
    }
    
    /* Initialize zlib stream */
    ngx_memzero(&stream, sizeof(z_stream));
    stream.next_in = input_data;
    stream.avail_in = input_size;
    
    /* Set windowBits based on compression type (Requirement 2.1, 2.2) */
    if (type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP) {
        /* MAX_WBITS + 16 for gzip format */
        window_bits = MAX_WBITS + 16;
    } else {
        /* MAX_WBITS for deflate format */
        window_bits = MAX_WBITS;
    }
    
    rc = inflateInit2(&stream, window_bits);
    if (rc != Z_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: decompression failed, "
                     "inflateInit2 error: %d, category=conversion", rc);
        return NGX_ERROR;
    }
    
    /* Estimate output size (typically input_size * 10) with overflow protection. */
    if (ngx_http_markdown_calc_output_size(r, input_size, conf->max_size, &output_size)
        != NGX_OK)
    {
        inflateEnd(&stream);
        return NGX_ERROR;
    }
    
    /* Allocate output buffer using nginx memory pool (Requirement 9.1, 14.2) */
    output_data = ngx_pnalloc(r->pool, output_size);
    if (output_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to allocate decompression buffer, "
                     "size=%uz, category=system",
                     output_size);
        inflateEnd(&stream);
        return NGX_ERROR;
    }
    
    stream.next_out = output_data;
    stream.avail_out = output_size;
    
    /* Perform decompression using Z_FINISH flag (Requirement 2.3) */
    rc = inflate(&stream, Z_FINISH);
    
    /* Check for Z_STREAM_END (Requirement 6.5) */
    if (rc != Z_STREAM_END) {
        /* Check if failure was due to insufficient output buffer space */
        if (rc == Z_BUF_ERROR && stream.total_out >= conf->max_size) {
            /* Decompressed size would exceed limit (Requirement 9.3, 9.4) */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: decompressed size exceeds limit (%uz), "
                         "category=resource_limit",
                         conf->max_size);
            inflateEnd(&stream);
            return NGX_ERROR;
        }
        
        /* Other decompression error */
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: decompression failed, "
                     "inflate error: %d, category=conversion", rc);
        inflateEnd(&stream);
        return NGX_ERROR;
    }
    
    /* Check if decompressed size exceeds limit (Requirement 9.3, 9.4) */
    if (stream.total_out > conf->max_size) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: decompressed size (%uz) exceeds limit (%uz), "
                     "category=resource_limit",
                     stream.total_out, conf->max_size);
        inflateEnd(&stream);
        return NGX_ERROR;
    }
    
    /* Create output chain with decompressed data (Requirement 2.3) */
    b = ngx_create_temp_buf(r->pool, stream.total_out);
    if (b == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to create output buffer, "
                     "category=system");
        inflateEnd(&stream);
        return NGX_ERROR;
    }
    
    ngx_memcpy(b->pos, output_data, stream.total_out);
    b->last = b->pos + stream.total_out;
    b->last_buf = 1;
    
    cl = ngx_alloc_chain_link(r->pool);
    if (cl == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to allocate chain link, "
                     "category=system");
        inflateEnd(&stream);
        return NGX_ERROR;
    }
    
    cl->buf = b;
    cl->next = NULL;
    *out = cl;
    
    /* Clean up with inflateEnd() */
    inflateEnd(&stream);
    
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: decompression succeeded, "
                  "compressed=%uz bytes, decompressed=%uz bytes, ratio=%.1f",
                  input_size, stream.total_out,
                  (float)stream.total_out / input_size);
    
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
 *   NGX_OK       - Decompression succeeded
 *   NGX_ERROR    - Decompression failed (invalid data, size limit, etc.)
 *   NGX_DECLINED - Brotli support not compiled in (triggers fallback)
 *
 * Requirements: 3.1, 3.2, 3.3, 14.1
 */
ngx_int_t
ngx_http_markdown_decompress_brotli(ngx_http_request_t *r,
                                     ngx_chain_t *in,
                                     ngx_chain_t **out)
{
#ifdef NGX_HTTP_BROTLI
    /* Brotli support is compiled in */
    BrotliDecoderState          *decoder;
    BrotliDecoderResult          result;
    ngx_buf_t                   *b;
    ngx_chain_t                 *cl;
    u_char                      *input_data;
    size_t                       input_size;
    u_char                      *output_data;
    size_t                       output_size;
    size_t                       available_in;
    size_t                       available_out;
    const uint8_t               *next_in;
    uint8_t                     *next_out;
    size_t                       total_out;
    ngx_http_markdown_conf_t    *conf;
    
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    
    /* Log that we're using brotli library for decompression (Requirement 3.2) */
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: using brotli library for decompression");
    
    /* Collect all input data into a single buffer */
    input_size = ngx_http_markdown_chain_size(in);
    
    /* Validate input size */
    if (input_size == 0 || input_size == (size_t) -1) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: decompression failed, "
                     "invalid input size, category=conversion");
        return NGX_ERROR;
    }
    
    input_data = ngx_pnalloc(r->pool, input_size);
    if (input_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to allocate input buffer, "
                     "size=%uz, category=system",
                     input_size);
        return NGX_ERROR;
    }
    
    if (ngx_http_markdown_chain_to_buffer(in, input_data, input_size) != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to collect input data, "
                     "category=system");
        return NGX_ERROR;
    }
    
    /* Create brotli decoder instance (Requirement 3.2) */
    decoder = BrotliDecoderCreateInstance(NULL, NULL, NULL);
    if (decoder == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to create brotli decoder, "
                     "category=system");
        return NGX_ERROR;
    }
    
    /* Estimate output size (typically input_size * 10) with overflow protection. */
    if (ngx_http_markdown_calc_output_size(r, input_size, conf->max_size, &output_size)
        != NGX_OK)
    {
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    
    /* Allocate output buffer using nginx memory pool (Requirement 3.2, 14.1) */
    output_data = ngx_pnalloc(r->pool, output_size);
    if (output_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to allocate decompression buffer, "
                     "size=%uz, category=system",
                     output_size);
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    
    /* Set up decompression parameters */
    available_in = input_size;
    next_in = input_data;
    available_out = output_size;
    next_out = output_data;
    total_out = 0;
    
    /* Perform decompression (Requirement 3.2) */
    result = BrotliDecoderDecompressStream(
        decoder,
        &available_in,
        &next_in,
        &available_out,
        &next_out,
        &total_out
    );
    
    /* Check for errors (Requirement 3.2) */
    if (result == BROTLI_DECODER_RESULT_ERROR) {
        BrotliDecoderErrorCode error_code = BrotliDecoderGetErrorCode(decoder);
        const char *error_str = BrotliDecoderErrorString(error_code);
        
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: brotli decompression failed, "
                     "error: %s, category=conversion",
                     error_str);
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    
    /* Check if decompression is complete */
    if (result != BROTLI_DECODER_RESULT_SUCCESS) {
        /* Check if failure was due to insufficient output buffer space */
        if (result == BROTLI_DECODER_RESULT_NEEDS_MORE_OUTPUT) {
            /* Calculate how much was decompressed so far */
            total_out = output_size - available_out;
            
            /* Decompressed size would exceed limit (Requirement 9.3, 9.4) */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: decompressed size exceeds limit (%uz), "
                         "category=resource_limit",
                         conf->max_size);
            BrotliDecoderDestroyInstance(decoder);
            return NGX_ERROR;
        }
        
        /* Other decompression error */
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: brotli decompression incomplete, "
                     "result=%d, category=conversion",
                     result);
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    
    /* Calculate actual decompressed size */
    total_out = output_size - available_out;
    
    /* Check if decompressed size exceeds limit */
    if (total_out > conf->max_size) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: decompressed size (%uz) exceeds limit (%uz), "
                     "category=resource_limit",
                     total_out, conf->max_size);
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    
    /* Create output chain with decompressed data (Requirement 3.2) */
    b = ngx_create_temp_buf(r->pool, total_out);
    if (b == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to create output buffer, "
                     "category=system");
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    
    ngx_memcpy(b->pos, output_data, total_out);
    b->last = b->pos + total_out;
    b->last_buf = 1;
    
    cl = ngx_alloc_chain_link(r->pool);
    if (cl == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to allocate chain link, "
                     "category=system");
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    
    cl->buf = b;
    cl->next = NULL;
    *out = cl;
    
    /* Clean up decoder instance (Requirement 3.2) */
    BrotliDecoderDestroyInstance(decoder);
    
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: brotli decompression succeeded, "
                  "compressed=%uz bytes, decompressed=%uz bytes, ratio=%.1f",
                  input_size, total_out,
                  (float)total_out / input_size);
    
    return NGX_OK;
    
#else
    /* Brotli support not compiled in (Requirement 3.3) */
    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown filter: brotli not supported, "
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
                              ngx_chain_t *in,
                              ngx_chain_t **out)
{
    ngx_int_t  rc;
    
    /* Route to appropriate decompression function based on type */
    switch (type) {
        case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
            /* Use zlib for gzip/deflate decompression (Requirement 2.3) */
            rc = ngx_http_markdown_decompress_gzip(r, type, in, out);
            return rc;
            
        case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
            /* Use brotli library for brotli decompression (Requirement 3.4) */
            rc = ngx_http_markdown_decompress_brotli(r, in, out);
            
            /* Handle NGX_DECLINED from brotli function (when brotli not available) */
            if (rc == NGX_DECLINED) {
                ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                             "markdown filter: brotli compression detected but "
                             "brotli module not available, returning original content");
            }
            
            return rc;
            
        case NGX_HTTP_MARKDOWN_COMPRESSION_NONE:
            /* No compression, should not reach here */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: decompress called with COMPRESSION_NONE, "
                         "category=system");
            return NGX_ERROR;
            
        case NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN:
            /* Unknown/unsupported compression format */
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown filter: unsupported compression format, "
                         "returning original content");
            return NGX_DECLINED;
            
        default:
            /* Invalid compression type */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: invalid compression type: %d, "
                         "category=system",
                         type);
            return NGX_ERROR;
    }
}
