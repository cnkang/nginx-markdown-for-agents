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
    
    /* Handle missing or empty Content-Encoding header (Requirement 1.5) */
    if (h == NULL || h->value.len == 0) {
        return NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
    }
    
    /* Check for gzip compression (case-insensitive, Requirement 1.2) */
    if (h->value.len == sizeof("gzip") - 1
        && ngx_strncasecmp(h->value.data,
                            ngx_http_markdown_encoding_gzip,
                            sizeof("gzip") - 1) == 0)
    {
        return NGX_HTTP_MARKDOWN_COMPRESSION_GZIP;
    }
    
    /* Check for deflate compression (case-insensitive, Requirement 1.3) */
    if (h->value.len == sizeof("deflate") - 1
        && ngx_strncasecmp(h->value.data,
                            ngx_http_markdown_encoding_deflate,
                            sizeof("deflate") - 1) == 0)
    {
        return NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE;
    }
    
    /* Check for brotli compression (case-insensitive, Requirement 1.4) */
    if (h->value.len == sizeof("br") - 1
        && ngx_strncasecmp(h->value.data,
                            ngx_http_markdown_encoding_br,
                            sizeof("br") - 1) == 0)
    {
        return NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI;
    }
    
    /* Unknown or unsupported compression format (Requirement 1.6) */
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
        
        len = cl->buf->last - cl->buf->pos;
        
        if (copied > size || len > size - copied) {
            return NGX_ERROR;
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
                                   const ngx_chain_t *in,
                                   ngx_chain_t **out)
{
    z_stream                           stream;
    ngx_buf_t                         *b;
    ngx_chain_t                       *cl;
    u_char                            *input_data;
    size_t                             input_size;
    u_char                            *output_data;
    size_t                             output_size;
    ngx_int_t                          chain_rc;
    int                                zrc;
    int                                window_bits;
    const ngx_http_markdown_conf_t    *conf;
    
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    
    /* Log that we're using zlib for decompression (Requirement 13.5) */
    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: using zlib for gzip/deflate decompression, type=%d",
                  type);
    
    /* Collect all input data into a single buffer */
    input_size = ngx_http_markdown_chain_size(in);
    
    /* Validate input size (Requirement 6.5) */
    if (input_size == 0 || input_size == (size_t) -1) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompression failed, "
                     "invalid input size, category=conversion");
        return NGX_ERROR;
    }
    
    input_data = ngx_pnalloc(r->pool, input_size);
    if (input_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate input buffer, "
                     "size=%uz, category=system",
                     input_size);
        return NGX_ERROR;
    }
    
    chain_rc = ngx_http_markdown_chain_to_buffer(in, input_data, input_size);
    if (chain_rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to collect input data, "
                     "category=system");
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
    
    /* Set windowBits based on compression type (Requirement 2.1, 2.2) */
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
    
    /* Allocate output buffer using nginx memory pool (Requirement 9.1, 14.2) */
    output_data = ngx_pnalloc(r->pool, output_size);
    if (output_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate decompression buffer, "
                     "size=%uz, category=system",
                     output_size);
        inflateEnd(&stream);
        return NGX_ERROR;
    }
    
    stream.next_out = output_data;
    stream.avail_out = (uInt) output_size;
    
    /*
     * Perform decompression using incremental inflate with Z_NO_FLUSH.
     * If the output buffer is exhausted before all input is consumed,
     * reallocate up to decompress.max_size and continue. This avoids
     * misclassifying high-compression-ratio payloads that exceed the
     * 10x heuristic estimate but are still within budget.
     */
    for ( ;; ) {
        zrc = inflate(&stream, Z_NO_FLUSH);

        if (zrc == Z_STREAM_END) {
            break;
        }

        if (zrc == Z_OK) {
            /*
             * Z_OK with avail_in > 0 and avail_out == 0: output buffer
             * full, need more space. Reallocate if within budget.
             */
            if (stream.avail_out == 0 && stream.avail_in > 0) {
                size_t   new_size;
                size_t   used;
                u_char  *new_data;

                used = stream.total_out;
                if (used >= conf->decompress.max_size) {
                    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                                 "markdown: decompressed size exceeds "
                                 "decompression budget (%uz), "
                                 "category=resource_limit",
                                 conf->decompress.max_size);
                    inflateEnd(&stream);
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
                    inflateEnd(&stream);
                    return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
                }

                new_data = ngx_pnalloc(r->pool, new_size);
                if (new_data == NULL) {
                    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                                 "markdown: failed to reallocate decompression "
                                 "buffer, size=%uz, category=system",
                                 new_size);
                    inflateEnd(&stream);
                    return NGX_ERROR;
                }

                ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                              "markdown: decompression buffer realloc "
                              "used=%uz new_size=%uz (old buffer in pool)",
                              used, new_size);

                ngx_memcpy(new_data, output_data, used);
                output_data = new_data;
                output_size = new_size;
                stream.next_out = output_data + used;
                stream.avail_out = (uInt) (new_size - used);
                continue;
            }

            /*
             * Z_OK with avail_in == 0: all input consumed but stream
             * not ended. This is truncated input.
             */
            if (stream.avail_in == 0) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: decompression failed, "
                             "truncated input (Z_OK with no remaining "
                             "input), category=conversion");
                inflateEnd(&stream);
                return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
            }

            /* Z_OK with remaining input and remaining output: unexpected */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: decompression stalled, "
                         "inflate returned Z_OK with avail_in=%d avail_out=%d, "
                         "category=conversion",
                         stream.avail_in, stream.avail_out);
            inflateEnd(&stream);
            return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
        }

        if (zrc == Z_BUF_ERROR) {
            /*
             * Z_BUF_ERROR: no progress possible. If we have consumed
             * all input, this is truncated input. Otherwise, try
             * expanding the output buffer.
             */
            if (stream.avail_in == 0) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: decompression failed, "
                             "truncated input (Z_BUF_ERROR with no "
                             "remaining input), category=conversion");
                inflateEnd(&stream);
                return NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT;
            }

            if (stream.avail_out == 0) {
                /*
                 * Output buffer full with remaining input.
                 * Attempt reallocation (same logic as Z_OK path above).
                 */
                size_t   new_size;
                size_t   used;
                u_char  *new_data;

                used = stream.total_out;
                if (used >= conf->decompress.max_size) {
                    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                                 "markdown: decompressed size exceeds "
                                 "decompression budget (%uz), "
                                 "category=resource_limit",
                                 conf->decompress.max_size);
                    inflateEnd(&stream);
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
                    inflateEnd(&stream);
                    return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
                }

                new_data = ngx_pnalloc(r->pool, new_size);
                if (new_data == NULL) {
                    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                                 "markdown: failed to reallocate decompression "
                                 "buffer, size=%uz, category=system",
                                 new_size);
                    inflateEnd(&stream);
                    return NGX_ERROR;
                }

                ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                              "markdown: decompression buffer realloc "
                              "used=%uz new_size=%uz (old buffer in pool)",
                              used, new_size);

                ngx_memcpy(new_data, output_data, used);
                output_data = new_data;
                output_size = new_size;
                stream.next_out = output_data + used;
                stream.avail_out = (uInt) (new_size - used);
                continue;
            }

            /* Z_BUF_ERROR with both avail_in > 0 and avail_out > 0:
             * should not happen; treat as format error. */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: decompression failed, "
                         "Z_BUF_ERROR with avail_in=%d avail_out=%d, "
                         "category=conversion",
                         stream.avail_in, stream.avail_out);
            inflateEnd(&stream);
            return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
        }

        /* All other zlib errors */
        if (zrc == Z_DATA_ERROR) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown: decompression failed, "
                         "inflate format error (Z_DATA_ERROR), "
                         "category=conversion");
            inflateEnd(&stream);
            return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
        }

        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompression failed, "
                     "inflate error: %d, category=conversion", zrc);
        inflateEnd(&stream);
        return NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR;
    }
    
    /* Check if decompressed size exceeds decompression budget (Requirement 9.3, 9.4) */
    if (stream.total_out > conf->decompress.max_size) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size (%uz) exceeds decompression budget (%uz), "
                     "category=resource_limit",
                     stream.total_out, conf->decompress.max_size);
        inflateEnd(&stream);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }
    
    /* Create output chain wrapping the decompressed data directly
     * (avoids a second allocation + memcpy). */
    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to create output buffer, "
                     "category=system");
        inflateEnd(&stream);
        return NGX_ERROR;
    }
    
    b->pos = output_data;
    b->last = output_data + stream.total_out;
    b->start = output_data;
    b->end = output_data + output_size;
    b->temporary = 1;
    b->last_buf = 1;
    
    cl = ngx_alloc_chain_link(r->pool);
    if (cl == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate chain link, "
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
                  "markdown: decompression succeeded, "
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
                                    const ngx_chain_t *in,
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
    const ngx_http_markdown_conf_t    *conf;
    
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    
    /* Log that we're using brotli library for decompression (Requirement 3.2) */
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: using brotli library for decompression");
    
    /* Collect all input data into a single buffer */
    input_size = ngx_http_markdown_chain_size(in);
    
    /* Validate input size */
    if (input_size == 0 || input_size == (size_t) -1) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompression failed, "
                     "invalid input size, category=conversion");
        return NGX_ERROR;
    }
    
    input_data = ngx_pnalloc(r->pool, input_size);
    if (input_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate input buffer, "
                     "size=%uz, category=system",
                     input_size);
        return NGX_ERROR;
    }
    
    if (ngx_http_markdown_chain_to_buffer(in, input_data, input_size) != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to collect input data, "
                     "category=system");
        return NGX_ERROR;
    }
    
    /* Create brotli decoder instance (Requirement 3.2) */
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
    
    /* Allocate output buffer using nginx memory pool (Requirement 3.2, 14.1) */
    output_data = ngx_pnalloc(r->pool, output_size);
    if (output_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate decompression buffer, "
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
            size_t   used;
            size_t   new_size;
            u_char  *new_data;

            used = output_size - available_out;

            if (used >= conf->decompress.max_size) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: decompressed size exceeds "
                             "decompression budget (%uz), "
                             "category=resource_limit",
                             conf->decompress.max_size);
                BrotliDecoderDestroyInstance(decoder);
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
                BrotliDecoderDestroyInstance(decoder);
                return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
            }

            new_data = ngx_pnalloc(r->pool, new_size);
            if (new_data == NULL) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: failed to reallocate decompression "
                             "buffer, size=%uz, category=system",
                             new_size);
                BrotliDecoderDestroyInstance(decoder);
                return NGX_ERROR;
            }

            ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown: brotli decompression buffer realloc "
                          "used=%uz new_size=%uz (old buffer in pool)",
                          used, new_size);

            ngx_memcpy(new_data, output_data, used);
            output_data = new_data;
            output_size = new_size;
            available_out = new_size - used;
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
    
    /* Create output chain wrapping the decompressed data directly
     * (avoids a second allocation + memcpy). */
    b = ngx_calloc_buf(r->pool);
    if (b == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to create output buffer, "
                     "category=system");
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    
    b->pos = output_data;
    b->last = output_data + total_out;
    b->start = output_data;
    b->end = output_data + output_size;
    b->temporary = 1;
    b->last_buf = 1;
    
    cl = ngx_alloc_chain_link(r->pool);
    if (cl == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate chain link, "
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
                  "markdown: brotli decompression succeeded, "
                  "compressed=%uz bytes, decompressed=%uz bytes, ratio=%.1f",
                  input_size, total_out,
                  (float)total_out / input_size);
    
    return NGX_OK;
    
#else
    (void) in;
    (void) out;

    /* Brotli support not compiled in (Requirement 3.3) */
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
            /* Use zlib for gzip/deflate decompression (Requirement 2.3) */
            rc = ngx_http_markdown_decompress_gzip(r, type, in, out);

            if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: gzip/deflate decompressed "
                             "size exceeds budget, category=resource_limit");
            }

            return rc;
            
        case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
            /* Use brotli library for brotli decompression (Requirement 3.4) */
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
