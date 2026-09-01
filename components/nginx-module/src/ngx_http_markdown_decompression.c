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
#include <stdint.h>
#include <zlib.h>

#include "markdown_converter.h"
#include "ngx_http_markdown_filter_module.h"

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif

/* Conditionally include brotli header if support is compiled in */
#ifdef NGX_HTTP_BROTLI
#include <brotli/decode.h>

typedef struct {
    ngx_atomic_uint_t  *counter;
    size_t              limit;
    ngx_log_t          *log;
} ngx_http_markdown_full_brotli_alloc_ctx_t;

typedef struct {
    ngx_atomic_uint_t  *counter;
    size_t              reserved_size;
} ngx_http_markdown_full_brotli_allocation_t;


static ngx_int_t
ngx_http_markdown_full_brotli_reserve(
    ngx_atomic_uint_t *counter, size_t limit, size_t size)
{
    ngx_atomic_uint_t  current;
    ngx_atomic_uint_t  next;

    if (counter == NULL || limit == 0) {
        return NGX_ERROR;
    }

    current = *counter;
    for ( ;; ) {
        if (current > limit || size > limit - current)
        {
            return NGX_ERROR;
        }

        next = current + (ngx_atomic_uint_t) size;
        if (ngx_atomic_cmp_set(counter, current, next)) {
            return NGX_OK;
        }
        current = *counter;
    }
}


static void *
ngx_http_markdown_full_brotli_alloc(void *opaque, size_t size)
{
    ngx_http_markdown_full_brotli_alloc_ctx_t  *ctx;
    ngx_http_markdown_full_brotli_allocation_t  *allocation;
    size_t                                       total;

    ctx = opaque;
    if (ctx == NULL || ctx->log == NULL || size == 0
        || size > (size_t) -1
            - sizeof(ngx_http_markdown_full_brotli_allocation_t))
    {
        return NULL;
    }

    if (size > (size_t) -1
        - sizeof(ngx_http_markdown_full_brotli_allocation_t))
    {
        return NULL;
    }
    total = sizeof(ngx_http_markdown_full_brotli_allocation_t) + size;
    if (ngx_http_markdown_full_brotli_reserve(
            ctx->counter, ctx->limit, total) != NGX_OK)
    {
        return NULL;
    }

    allocation = ngx_alloc(total, ctx->log);
    if (allocation == NULL) {
        /*
         * Roll back the reserved budget.  NGINX workers are single-threaded
         * event loops (one worker per process), so the atomic counter has no
         * real contention here — the CAS/atomic discipline is retained for
         * Rule 42 volatile/atomic consistency and metrics-snapshot safety,
         * not for cross-thread synchronization.
        */
        (void) ngx_atomic_fetch_add(
            ctx->counter, -((ngx_atomic_int_t) total));
        return NULL;
    }

    allocation->counter = ctx->counter;
    allocation->reserved_size = total;
    return allocation + 1;
}


static void
ngx_http_markdown_full_brotli_free(void *opaque, void *address)
{
    ngx_http_markdown_full_brotli_allocation_t  *allocation;
    ngx_atomic_uint_t                           *counter;
    size_t                                       reserved_size;

    (void) opaque;
    if (address == NULL) {
        return;
    }

    allocation =
        ((ngx_http_markdown_full_brotli_allocation_t *) address) - 1;
    counter = allocation->counter;
    reserved_size = allocation->reserved_size;
    ngx_free(allocation);
    (void) ngx_atomic_fetch_add(
        counter, -((ngx_atomic_int_t) reserved_size));
}
#endif

static u_char ngx_http_markdown_encoding_gzip[] = "gzip";
static u_char ngx_http_markdown_encoding_deflate[] = "deflate";
static u_char ngx_http_markdown_encoding_br[] = "br";
static u_char ngx_http_markdown_content_encoding_header[] =
    "Content-Encoding";

static ngx_flag_t ngx_http_markdown_is_content_encoding_header(
    const ngx_table_elt_t *header);
static ngx_int_t ngx_http_markdown_measure_content_encoding(
    ngx_http_request_t *r, const ngx_str_t **single_value,
    ngx_uint_t *match_count, size_t *total_len);
static ngx_int_t ngx_http_markdown_add_content_encoding_length(
    size_t value_len, ngx_uint_t match_count, size_t *total_len);
static ngx_int_t ngx_http_markdown_copy_content_encoding(
    ngx_http_request_t *r, u_char *data, size_t *written_out);


static ngx_flag_t
ngx_http_markdown_is_content_encoding_header(const ngx_table_elt_t *header)
{
    return header != NULL && header->hash != 0
           && header->key.data != NULL
           && header->key.len == sizeof("Content-Encoding") - 1
           && ngx_strncasecmp(header->key.data,
                              ngx_http_markdown_content_encoding_header,
                              sizeof("Content-Encoding") - 1) == 0;
}


static ngx_int_t
ngx_http_markdown_add_content_encoding_length(
    size_t value_len, ngx_uint_t match_count, size_t *total_len)
{
    if (match_count != 0) {
        if (*total_len > ((size_t) -1) - 2) {
            return NGX_ERROR;
        }
        *total_len += 2;
    }
    if (value_len > ((size_t) -1) - *total_len) {
        return NGX_ERROR;
    }
    *total_len += value_len;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_measure_content_encoding(
    ngx_http_request_t *r, const ngx_str_t **single_value,
    ngx_uint_t *match_count, size_t *total_len)
{
    if (r == NULL || single_value == NULL || match_count == NULL
        || total_len == NULL)
    {
        return NGX_ERROR;
    }

    *single_value = NULL;
    *match_count = 0;
    *total_len = 0;

    for (ngx_list_part_t *part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        const ngx_table_elt_t *headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return NGX_ERROR;
        }
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (!ngx_http_markdown_is_content_encoding_header(&headers[i])) {
                continue;
            }
            if (headers[i].value.len > 0 && headers[i].value.data == NULL) {
                return NGX_ERROR;
            }

            if (*match_count == 0) {
                *single_value = &headers[i].value;
            }
            if (ngx_http_markdown_add_content_encoding_length(
                    headers[i].value.len, *match_count, total_len)
                != NGX_OK)
            {
                return NGX_ERROR;
            }
            (*match_count)++;
        }
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_copy_content_encoding(ngx_http_request_t *r, u_char *data,
                                        size_t *written_out)
{
    ngx_flag_t        first;
    size_t             written;

    if (r == NULL || data == NULL || written_out == NULL) {
        return NGX_ERROR;
    }

    first = 1;
    written = 0;
    *written_out = 0;
    for (ngx_list_part_t *part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        const ngx_table_elt_t *headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return NGX_ERROR;
        }
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (!ngx_http_markdown_is_content_encoding_header(&headers[i])) {
                continue;
            }
            if (!first) {
                data[written++] = ',';
                data[written++] = ' ';
            }
            if (headers[i].value.len > 0) {
                ngx_memcpy(data + written, headers[i].value.data,
                           headers[i].value.len);
            }
            written += headers[i].value.len;
            first = 0;
        }
    }

    *written_out = written;
    return NGX_OK;
}

/*
 * Collect all Content-Encoding response header fields in received order.
 *
 * The values are concatenated with a comma+space separator, matching the
 * RFC 9110 field-line combination semantics, and returned as a pool-owned
 * string. A single field line remains a zero-copy view into NGINX response
 * header storage.
 *
 * No separate per-header cap is applied: the total is bounded by the
 * upstream response-header buffering accepted by the active upstream module
 * (for example, proxy_buffer_size) and the module's accepted header limits.
 *
 * Returns NGX_OK with `out` populated, NGX_DECLINED when no Content-Encoding
 * field exists, or NGX_ERROR for allocation failure.
 */
ngx_int_t
ngx_http_markdown_collect_content_encoding(ngx_http_request_t *r,
                                           ngx_str_t *out)
{
    ngx_uint_t        match_count;
    size_t             total_len;
    size_t             written;
    u_char            *data;
    const ngx_str_t  *single_value;

    if (r == NULL || r->pool == NULL || out == NULL) {
        return NGX_ERROR;
    }

    out->data = NULL;
    out->len = 0;

    if (ngx_http_markdown_measure_content_encoding(
            r, &single_value, &match_count, &total_len) != NGX_OK)
    {
        return NGX_ERROR;
    }

    if (match_count == 0) {
        return NGX_DECLINED;
    }
    if (match_count == 1) {
        *out = *single_value;
        return NGX_OK;
    }

    data = ngx_pnalloc(r->pool, total_len);
    if (data == NULL) {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_copy_content_encoding(r, data, &written) != NGX_OK
        || written != total_len)
    {
        out->data = NULL;
        out->len = 0;
        return NGX_ERROR;
    }

    out->data = data;
    out->len = total_len;
    return NGX_OK;
}

/*
 * Parse the Content-Encoding chain grammar and populate the request
 * context decompression state.
 *
 * Calls the Rust FFI chain parser on the concatenated header value. The
 * classification outcome decides precommit routing:
 *
 *   VALID            - ctx->decompression.layer_count and layers[] hold the
 *                      non-identity decoder list in application order
 *   MALFORMED        - canonical ENCODING_HEADER_INVALID (stage=decompression,
 *                      error_origin=format); no decoder is started
 *   UNKNOWN_TOKEN    - unsupported token; the caller routes it through the
 *                      configured error policy without starting a decoder
 *   DEPTH_EXCEEDED   - unsupported chain depth; the caller routes it through
 *                      the configured error policy without starting a decoder
 *
 * Returns the ENCODING_CHAIN_* classification (including
 * ENCODING_CHAIN_INVALID_ARGS for NULL/empty argument violations and
 * capacity failures) on error.
 */
/*
 * FFI encoding-chain layer codes.  These mirror the Rust `Format` enum
 * (Gzip = 0, Deflate = 1, Br = 2) in decompress.rs; the C switch below must
 * use these names so a Rust renumbering cannot silently misroute layers.
 */
#define NGX_HTTP_MARKDOWN_FFI_LAYER_GZIP    0
#define NGX_HTTP_MARKDOWN_FFI_LAYER_DEFLATE 1
#define NGX_HTTP_MARKDOWN_FFI_LAYER_BROTLI  2

u_char
ngx_http_markdown_parse_encoding_chain_ffi(const ngx_http_request_t *r,
                                           ngx_http_markdown_ctx_t *ctx,
                                           const ngx_str_t *combined)
{
    FFIEncodingChainResult   result;
    u_char                   classification;
    ngx_uint_t               layer_capacity;

    (void) r;
    ngx_memzero(&result, sizeof(result));

    if (ctx == NULL || combined == NULL
        || (combined->len > 0 && combined->data == NULL))
    {
        if (ctx != NULL) {
            ctx->decompression.chain_parsed = 1;
            ctx->decompression.layer_count = 0;
            ctx->decompression.needed = 0;
            ctx->decompression.done = 0;
            ctx->decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
            ngx_memzero(ctx->decompression.layers,
                        sizeof(ctx->decompression.layers));
        }
        return ENCODING_CHAIN_INVALID_ARGS;
    }

    classification = markdown_parse_encoding_chain(
        (const uint8_t *) combined->data, combined->len, &result);

    ctx->decompression.chain_parsed = 1;

    if (classification != ENCODING_CHAIN_VALID) {
        ctx->decompression.layer_count = 0;
        ctx->decompression.needed = 0;
        ctx->decompression.done = 0;
        ctx->decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
        ngx_memzero(ctx->decompression.layers,
                    sizeof(ctx->decompression.layers));
        return classification;
    }

    layer_capacity = sizeof(ctx->decompression.layers)
                     / sizeof(ctx->decompression.layers[0]);
    if (result.layer_count > layer_capacity
        || result.layer_count > MAX_DECODER_DEPTH) {
        ctx->decompression.layer_count = 0;
        ctx->decompression.needed = 0;
        ctx->decompression.done = 0;
        ctx->decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
        ngx_memzero(ctx->decompression.layers,
                    sizeof(ctx->decompression.layers));
        return ENCODING_CHAIN_INVALID_ARGS;
    }

    ctx->decompression.layer_count = result.layer_count;
    ngx_memzero(ctx->decompression.layers,
                sizeof(ctx->decompression.layers));
    if (result.layer_count > 0) {
        ngx_memcpy(ctx->decompression.layers, result.layers,
                   result.layer_count
                   * sizeof(ctx->decompression.layers[0]));
    }

    if (result.layer_count == 0) {
        ctx->decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
        return ENCODING_CHAIN_VALID;
    }

    /* Map the first layer to the module routing type enum. The layer codes are
     * Rust `Format` enum values (Gzip=0, Deflate=1, Br=2); keep them in
     * named constants so a renumbering cannot silently misroute. */
    switch (result.layers[0]) {
    case NGX_HTTP_MARKDOWN_FFI_LAYER_GZIP:
        ctx->decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_GZIP;
        break;
    case NGX_HTTP_MARKDOWN_FFI_LAYER_DEFLATE:
        ctx->decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE;
        break;
    case NGX_HTTP_MARKDOWN_FFI_LAYER_BROTLI:
        ctx->decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI;
        break;
    default:
        ctx->decompression.type = NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN;
        break;
    }

    return classification;
}

/**
 * Determines the compression type declared by the response's Content-Encoding
 * header.
 *
 * @param r Request whose response headers are inspected.
 * @return NGX_HTTP_MARKDOWN_COMPRESSION_NONE when the header is absent or
 *         empty; NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
 *         NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE, or
 *         NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI for a recognized single
 *         coding; NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN for an unsupported
 *         single coding or a comma-separated chain that must be classified by
 *         the chain parser.
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

    /*
     * Content-Encoding is a chain grammar, not a single token.  The
     * precommit path parses comma-separated values with the Rust chain
     * validator; do not emit the single-token "unsupported" warning before
     * that parser has classified a valid multi-layer response.
     *
     * Intentional (comma-value): a comma-containing value is reported UNKNOWN
     * here so the single-token classifier does not double-report what the
     * chain parser owns; the precommit chain validator then classifies the
     * real layers (valid multi-layer chains still reach decompression).
     */
    if (ngx_strlchr(h->value.data, h->value.data + h->value.len, ',') != NULL) {
        return NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN;
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

/**
 * Calculates the total size of data in a buffer chain.
 *
 * @param in Input buffer chain.
 * @return Total size in bytes, or SIZE_MAX if the total overflows size_t.
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

/**
 * Copies the data in a buffer chain into a contiguous destination buffer.
 *
 * @param in   Input buffer chain.
 * @param dest Pre-allocated destination buffer.
 * @param size Size of the destination buffer.
 * @return NGX_OK on success, or NGX_ERROR if the destination is too small.
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


/* Return the finite output ceiling implied by a compressed-input ratio. */
static size_t
ngx_http_markdown_decomp_ratio_limit(size_t input_size, ngx_uint_t ratio)
{
    if (input_size == 0 || ratio == 0) {
        return NGX_MAX_SIZE_T_VALUE;
    }

    /*
     * Compare in uintmax_t before multiplying. This keeps the overflow
     * check valid even if ngx_uint_t is wider than size_t, without a
     * same-width comparison that static analysis can prove redundant.
     */
    if ((uintmax_t) input_size
        > (uintmax_t) NGX_MAX_SIZE_T_VALUE / (uintmax_t) ratio)
    {
        return NGX_MAX_SIZE_T_VALUE;
    }

    return input_size * (size_t) ratio;
}


/*
 * Grow the decompression output buffer up to decompress.max_size.
 *
 * Codec-agnostic buffer growth: computes a new size (double current used,
 * minimum +4096, capped at budget and UINT_MAX), allocates an independently
 * freeable replacement, and copies existing data. The caller is responsible
 * for updating any codec-specific pointers (z_stream, brotli next_out, etc.)
 * after this function returns.
 *
 * Parameters:
 *   r           - nginx request structure (for logging)
 *   output_data - pointer to current output buffer pointer (updated on success)
 *   output_size - pointer to current output buffer size (updated on success)
 *   used        - number of bytes already written to the buffer
 *   output_limit - effective absolute/ratio output ceiling
 *
 * Returns:
 *   NGX_OK on successful reallocation
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED if budget would be exceeded
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_grow_output_buffer_limited(
    ngx_http_request_t *r,
    u_char **output_data, size_t *output_size, size_t used,
    size_t output_limit)
{
    size_t   new_size;
    u_char  *new_data;

    if (used >= output_limit) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size exceeds "
                     "decompression budget (%uz), "
                     "category=resource_limit", output_limit);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    if (used > ((size_t) -1) / 2) {
        new_size = output_limit;
    } else {
        new_size = used * 2;
    }
    if (used <= ((size_t) -1) - 4096
        && new_size < used + 4096)
    {
        new_size = used + 4096;
    }
    if (new_size > output_limit) {
        new_size = output_limit;
    }
    if (new_size > (size_t) UINT_MAX) {
        new_size = (size_t) UINT_MAX;
    }
    if (new_size <= used) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size exceeds "
                     "decompression budget (%uz), "
                     "category=resource_limit",
                     output_limit);
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    /*
     * The returned output is adopted by ctx->buffer.data, whose resizable
     * backing-store contract is ngx_alloc/ngx_free.  Keep the same allocator
     * family across every growth so ownership remains transferable.
     */
    new_data = ngx_alloc(new_size, r->connection->log);
    if (new_data == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to reallocate decompression "
                     "buffer, size=%uz, category=system",
                     new_size);
        return NGX_ERROR;
    }
    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                "markdown: decompression buffer realloc "
                "used=%uz new_size=%uz",
                used, new_size);
    ngx_memcpy(new_data, *output_data, used);
    ngx_free(*output_data);
    *output_data = new_data;
    *output_size = new_size;

    return NGX_OK;
}


/*
 * Grow the decompression output buffer up to decompress.max_size (zlib).
 *
 * Thin wrapper around ngx_http_markdown_grow_output_buffer_limited that also
 * updates the zlib stream's next_out and avail_out pointers.
 *
 * Parameters:
 *   r           - nginx request structure (for pool allocation and logging)
 *   output_data - pointer to current output buffer pointer (updated on success)
 *   output_size - pointer to current output buffer size (updated on success)
 *   stream      - zlib stream (next_out and avail_out updated on success)
 *   completed_out - output bytes from completed gzip members
 *   output_limit - effective absolute/ratio output ceiling
 *
 * Returns:
 *   NGX_OK on successful reallocation
 *   NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED if budget would be exceeded
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_grow_decomp_buffer(ngx_http_request_t *r,
    u_char **output_data, size_t *output_size,
    z_stream *stream, size_t completed_out, size_t output_limit)
{
    size_t     used;
    ngx_int_t  rc;

    if (stream->total_out > NGX_MAX_SIZE_T_VALUE
        || completed_out > output_limit
        || stream->total_out
           > (uLong) (output_limit - completed_out))
    {
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    used = completed_out + (size_t) stream->total_out;
    rc = ngx_http_markdown_grow_output_buffer_limited(
        r, output_data, output_size, used, output_limit);
    if (rc != NGX_OK) {
        return rc;
    }

    stream->next_out = *output_data + used;
    stream->avail_out = (uInt) (*output_size - used);

    return NGX_OK;
}


typedef struct {
    ngx_http_request_t                    *request;
    z_stream                              *stream;
    u_char                               **output_data;
    size_t                                *output_size;
    ngx_http_markdown_compression_type_e   type;
    size_t                                 completed_out;
    size_t                                 output_limit;
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
            ctx->request, ctx->output_data, ctx->output_size,
            ctx->stream, ctx->completed_out, ctx->output_limit);
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

    stream->total_in = 0;
    stream->total_out = 0;
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

    if (ctx->stream->total_out > NGX_MAX_SIZE_T_VALUE
        || ctx->completed_out > ctx->output_limit
        || ctx->stream->total_out
           > (uLong) (ctx->output_limit
                      - ctx->completed_out))
    {
        return NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED;
    }

    /* CWE-190:guarded */
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
        && ctx->completed_out < ctx->output_limit)
    {
        rc = ngx_http_markdown_grow_output_buffer_limited(
            ctx->request, ctx->output_data, ctx->output_size,
            ctx->completed_out, ctx->output_limit);
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
 *   stream      - initialized zlib stream (modified in place)
 *   output_data - pointer to output buffer pointer (may be reallocated)
 *   output_size - pointer to output buffer size (updated on realloc)
 *   type        - content coding; gzip permits concatenated members
 *   output_limit - effective absolute/ratio output ceiling
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
    z_stream *stream,
    u_char **output_data, size_t *output_size,
    ngx_http_markdown_compression_type_e type, size_t output_limit,
    size_t *total_out)
{
    int                              zrc;
    ngx_int_t                        rc;
    ngx_http_markdown_inflate_ctx_t  ctx;

    ctx.request = r;
    ctx.stream = stream;
    ctx.output_data = output_data;
    ctx.output_size = output_size;
    ctx.type = type;
    ctx.completed_out = 0;
    ctx.output_limit = output_limit;

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
 * 4. Allocates transferable output using ngx_alloc
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
 * Allocate an independently freeable decompression output buffer, logging
 * and cleaning up decoder state on failure.
 *
 * Shared by the zlib (gzip/deflate) and brotli decompression paths so the
 * two backends cannot drift apart on the alloc-failure error path. The
 * caller provides a backend-specific cleanup callback invoked only on
 * failure (the caller retains ownership of decoder_state on success).
 *
 * Parameters:
 *   r             - nginx request (logging)
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

    output_data = ngx_alloc(output_size, r->connection->log);
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


static ngx_int_t
ngx_http_markdown_decomp_brotli_fail(BrotliDecoderState *decoder,
    u_char *output_data, ngx_int_t rc)
{
    BrotliDecoderDestroyInstance(decoder);
    ngx_free(output_data);
    return rc;
}


static ngx_int_t
ngx_http_markdown_full_brotli_prepare_alloc_ctx(
    ngx_http_request_t *r,
    ngx_http_markdown_full_brotli_alloc_ctx_t *alloc_ctx)
{
    ngx_http_markdown_main_conf_t  *main_conf;

    if (r == NULL || r->connection == NULL || r->connection->log == NULL
        || alloc_ctx == NULL)
    {
        return NGX_ERROR;
    }

    main_conf = ngx_http_get_module_main_conf(
        r, ngx_http_markdown_filter_module);
    if (main_conf == NULL) {
        return NGX_ERROR;
    }

    alloc_ctx->counter = &main_conf->brotli_workspace_bytes;
    alloc_ctx->limit = ngx_http_markdown_brotli_workspace_limit(
        main_conf->brotli_workspace_limit);
    alloc_ctx->log = r->connection->log;

    return NGX_OK;
}


typedef struct {
    u_char  *data;
    size_t   size;
    size_t   total_out;
} ngx_http_markdown_decomp_output_t;


static ngx_int_t
ngx_http_markdown_decomp_brotli_stream(
    ngx_http_request_t *r, BrotliDecoderState *decoder,
    const u_char *input_data, size_t input_size,
    size_t output_limit, ngx_http_markdown_decomp_output_t *output)
{
    BrotliDecoderResult  result;
    size_t               available_in;
    size_t               available_out;
    size_t               used;
    const uint8_t       *next_in;
    uint8_t             *next_out;
    ngx_int_t             grow_rc;

    output->total_out = 0;
    available_in = input_size;
    next_in = input_data;
    available_out = output->size;
    next_out = output->data;

    for ( ;; ) {
        result = BrotliDecoderDecompressStream(
            decoder, &available_in, &next_in, &available_out,
            &next_out, &output->total_out);

        if (result == BROTLI_DECODER_RESULT_SUCCESS) {
            output->total_out = output->size - available_out;
            if (available_in > 0) {
                /* Trailing compressed bytes after stream completion are
                 * a format violation.  The streaming path classifies
                 * this case as FORMAT_ERROR; keep both paths aligned
                 * (parity rule). */
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                              "markdown: brotli trailing data: %uz bytes "
                              "after stream completion, "
                              "category=conversion",
                              available_in);
                return ngx_http_markdown_decomp_brotli_fail(
                    decoder, output->data,
                    NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR);
            }
            return NGX_OK;
        }

        if (result == BROTLI_DECODER_RESULT_ERROR) {
            BrotliDecoderErrorCode                  error_code;
            ngx_http_markdown_brotli_error_class_e  error_class;
            const char                             *error_str;

            error_code = BrotliDecoderGetErrorCode(decoder);
            error_class = ngx_http_markdown_brotli_error_classify(
                (int) error_code);
            error_str = BrotliDecoderErrorString(error_code);

            /*
             * Keep buffered classification aligned with the streaming
             * decoder.  Only documented invalid-input codes are format
             * errors; allocation and internal decoder failures are system
             * errors and must not be reported as corrupt input.
             */
            if (error_class == NGX_HTTP_MARKDOWN_BROTLI_ERROR_FORMAT) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                              "markdown: brotli decompression failed, "
                              "error: %s, category=conversion",
                              error_str);
                return ngx_http_markdown_decomp_brotli_fail(
                    decoder, output->data,
                    NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR);
            }

            if (error_class == NGX_HTTP_MARKDOWN_BROTLI_ERROR_ALLOCATION) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                              "markdown: brotli decompression failed, "
                              "error: %s, class=allocation, category=system",
                              error_str);
            } else {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                              "markdown: brotli decompression failed, "
                              "error: %s, class=internal, category=system",
                              error_str);
            }

            return ngx_http_markdown_decomp_brotli_fail(
                decoder, output->data, NGX_ERROR);
        }

        if (result == BROTLI_DECODER_RESULT_NEEDS_MORE_OUTPUT) {
            used = output->size - available_out;
            grow_rc = ngx_http_markdown_grow_output_buffer_limited(
                r, &output->data, &output->size, used, output_limit);
            if (grow_rc != NGX_OK) {
                return ngx_http_markdown_decomp_brotli_fail(
                    decoder, output->data, grow_rc);
            }
            available_out = output->size - used;
            next_out = output->data + used;
            continue;
        }

        if (result == BROTLI_DECODER_RESULT_NEEDS_MORE_INPUT) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                          "markdown: brotli decompression incomplete, "
                          "truncated input, category=conversion");
            return ngx_http_markdown_decomp_brotli_fail(
                decoder, output->data,
                NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT);
        }

        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: brotli decompression incomplete, "
                      "result=%d, category=conversion", result);
        return ngx_http_markdown_decomp_brotli_fail(
            decoder, output->data, NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR);
    }
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
 * Build the output chain wrapping an independently allocated decompressed
 * buffer.
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


/*
 * Deflate compatibility fallback: retry a failed zlib-wrapped (RFC 1950)
 * decode as raw RFC 1951 (-MAX_WBITS).  Raw RFC 1951 deflate is part of the
 * public contract as a fallback for servers that provide raw deflate
 * (Microsoft IIS 5/6 and older Java servlets send raw RFC 1951 under
 * Content-Encoding: deflate).  The caller invokes this only after a
 * FORMAT_ERROR with zero output produced, so a partial decode (already
 * committed with zlib framing) is never replayed.  The retry result is
 * propagated to the caller: on failure it returns the raw attempt's own
 * classification (budget, truncation, or format), which is more informative
 * than the original wrapped-mode error; only an inflateInit2 failure (raw
 * mode could not even start) returns the original format error.
 */
static ngx_int_t
ngx_http_markdown_deflate_raw_retry(ngx_http_request_t *r,
                                    z_stream *stream,
                                    u_char *input_data, size_t input_size,
                                    ngx_http_markdown_inflate_ctx_t *ctx,
                                    size_t output_limit)
{
    ngx_int_t  loop_rc;
    int        zrc;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: deflate zlib-wrapped failed, "
                   "retrying with raw deflate (-MAX_WBITS)");

    inflateEnd(stream);

    ngx_memzero(stream, sizeof(z_stream));
    stream->next_in = input_data;
    stream->avail_in = (uInt) input_size;

    zrc = inflateInit2(stream, -MAX_WBITS);
    if (zrc != Z_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: raw deflate inflateInit2 "
                      "error: %d, category=conversion", zrc);
        ngx_free(*ctx->output_data);
        return NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR;
    }

    stream->next_out = *ctx->output_data;
    stream->avail_out = (uInt) *ctx->output_size;

    loop_rc = ngx_http_markdown_inflate_loop(r, stream,
                                             ctx->output_data, ctx->output_size,
                                             NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
                                             output_limit,
                                             &ctx->completed_out);
    if (loop_rc != NGX_OK) {
        /*
         * The retry owns the output buffer on failure: free it here and
         * let the caller propagate the error without touching the
         * (possibly freed) buffer again.  NGX_DONE is not a success code
         * for the full-buffer inflate path, so any non-NGX_OK return
         * takes this branch.
         */
        inflateEnd(stream);
        ngx_free(*ctx->output_data);
        /* Propagate the raw attempt's own classification (budget,
         * truncation, or format) rather than masking it. */
        return loop_rc;
    }
    return loop_rc;
}


/*
 * Finish an unsuccessful wrapped-mode inflate attempt.  A zero-output format
 * error for deflate may be retried as raw RFC 1951; all other failures own and
 * release the current output buffer before returning their classification.
 */
static ngx_int_t
ngx_http_markdown_decomp_handle_inflate_failure(
    ngx_http_markdown_inflate_ctx_t *ctx,
    u_char *input_data, size_t input_size,
    ngx_flag_t ratio_limited, ngx_int_t loop_rc)
{
    ngx_http_markdown_inflate_ctx_t  retry_ctx;

    if (loop_rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR
        && ctx->type == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE
        && ctx->completed_out == 0)
    {
        ngx_memzero(&retry_ctx, sizeof(retry_ctx));
        retry_ctx.output_data = ctx->output_data;
        retry_ctx.output_size = ctx->output_size;

        loop_rc = ngx_http_markdown_deflate_raw_retry(
            ctx->request, ctx->stream, input_data, input_size, &retry_ctx,
            ctx->output_limit);
        if (loop_rc == NGX_OK) {
            ctx->completed_out = retry_ctx.completed_out;
            return NGX_OK;
        }
        if (loop_rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED
            && ratio_limited)
        {
            return NGX_HTTP_MARKDOWN_DECOMP_RATIO_EXCEEDED;
        }
        return loop_rc;
    }

    inflateEnd(ctx->stream);
    ngx_free(*ctx->output_data);
    if (loop_rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED
        && ratio_limited)
    {
        return NGX_HTTP_MARKDOWN_DECOMP_RATIO_EXCEEDED;
    }
    return loop_rc;
}


/**
 * Decompresses gzip or deflate input and builds an NGINX output chain.
 *
 * @param r Request associated with the decompression operation.
 * @param type Compression format of the input.
 * @param in Input buffer chain containing compressed data.
 * @param out Receives the decompressed output buffer chain.
 * @return NGX_OK on success; an error code when decompression, allocation,
 *         validation, or the configured decompression budget prevents success.
 */
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
    size_t                             total_decompressed = 0;
    ngx_int_t                          loop_rc;
    int                                zrc;
    int                                window_bits;
    const ngx_http_markdown_conf_t    *conf;
    ngx_http_markdown_inflate_ctx_t    failure_ctx;
    size_t                             ratio_limit;
    size_t                             output_limit;
    ngx_flag_t                         ratio_limited;

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

    ratio_limit = ngx_http_markdown_decomp_ratio_limit(
        input_size, conf->limits.decompression_ratio);
    output_limit = conf->decompress.max_size;
    ratio_limited = ratio_limit < output_limit;
    if (ratio_limited) {
        output_limit = ratio_limit;
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
    if (ngx_http_markdown_calc_output_size(r, input_size, output_limit,
                                           &output_size)
        != NGX_OK)
    {
        inflateEnd(&stream);
        return NGX_ERROR;
    }

    /* Allocate transferable output using the same ngx_alloc/ngx_free
     * allocator family as ctx->buffer (Rule 43). */
    output_data = ngx_http_markdown_decomp_alloc_output(r, output_size,
        ngx_http_markdown_decomp_zlib_cleanup, &stream);
    if (output_data == NULL) {
        return NGX_ERROR;
    }

    stream.next_out = output_data;
    stream.avail_out = (uInt) output_size;

    /* Run the inflate loop (extracted for complexity reduction). */
    loop_rc = ngx_http_markdown_inflate_loop(r, &stream,
                                             &output_data, &output_size,
                                             type, output_limit,
                                             &total_decompressed);
    if (loop_rc != NGX_OK) {
        ngx_memzero(&failure_ctx, sizeof(failure_ctx));
        failure_ctx.request = r;
        failure_ctx.stream = &stream;
        failure_ctx.output_data = &output_data;
        failure_ctx.output_size = &output_size;
        failure_ctx.type = type;
        failure_ctx.completed_out = total_decompressed;
        failure_ctx.output_limit = output_limit;
        loop_rc = ngx_http_markdown_decomp_handle_inflate_failure(
            &failure_ctx, input_data, input_size, ratio_limited, loop_rc);
        if (loop_rc != NGX_OK) {
            return loop_rc;
        }
        total_decompressed = failure_ctx.completed_out;
    }

    if (ratio_limited && total_decompressed > ratio_limit) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size (%uz) exceeds "
                     "decompression ratio limit (%uz), "
                     "category=resource_limit",
                     total_decompressed, ratio_limit);
        inflateEnd(&stream);
        ngx_free(output_data);
        return NGX_HTTP_MARKDOWN_DECOMP_RATIO_EXCEEDED;
    }

    /* Check if decompressed size exceeds decompression budget (decompressed size budget enforcement) */
    if (total_decompressed > conf->decompress.max_size) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size (%uz) exceeds decompression budget (%uz), "
                     "category=resource_limit",
                     total_decompressed, conf->decompress.max_size);
        inflateEnd(&stream);
        ngx_free(output_data);
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
        ngx_free(output_data);
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

/**
 * Decompresses Brotli-encoded response data.
 *
 * @param r Request associated with the decompression operation.
 * @param in Chain containing the compressed input.
 * @param out Receives the decompressed output chain.
 * @returns NGX_OK on success; NGX_DECLINED when Brotli support is unavailable;
 *          otherwise, an error classification such as allocation failure,
 *          invalid format, truncated input, or exceeded decompression budget.
 */
ngx_int_t
ngx_http_markdown_decompress_brotli(ngx_http_request_t *r,
                                    const ngx_chain_t *in,
                                    ngx_chain_t **out)
{
    if (r == NULL || r->connection == NULL || r->connection->log == NULL) {
        return NGX_ERROR;
    }

#ifdef NGX_HTTP_BROTLI
    /* Brotli support is compiled in */
    BrotliDecoderState          *decoder;
    u_char                      *input_data;
    size_t                       input_size;
    ngx_http_markdown_decomp_output_t  output;
    const ngx_http_markdown_conf_t    *conf;
    ngx_http_markdown_full_brotli_alloc_ctx_t  alloc_ctx;
    ngx_int_t                   rc;
    size_t                      ratio_limit;
    size_t                      output_limit;
    ngx_flag_t                  ratio_limited;

    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    if (conf == NULL) {
        return NGX_ERROR;
    }
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

    ratio_limit = ngx_http_markdown_decomp_ratio_limit(
        input_size, conf->limits.decompression_ratio);
    output_limit = conf->decompress.max_size;
    ratio_limited = ratio_limit < output_limit;
    if (ratio_limited) {
        output_limit = ratio_limit;
    }

    if (ngx_http_markdown_full_brotli_prepare_alloc_ctx(r, &alloc_ctx)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    /* Create brotli decoder instance (brotli decoder instance creation) */
    decoder = BrotliDecoderCreateInstance(
        ngx_http_markdown_full_brotli_alloc,
        ngx_http_markdown_full_brotli_free, &alloc_ctx);
    if (decoder == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to create brotli decoder, "
                     "category=system");
        return NGX_ERROR;
    }
    /* Estimate output size with independent decompression budget. */
    if (ngx_http_markdown_calc_output_size(
            r, input_size, output_limit, &output.size)
        != NGX_OK)
    {
        BrotliDecoderDestroyInstance(decoder);
        return NGX_ERROR;
    }
    /* Allocate transferable output using the same ngx_alloc/ngx_free
     * allocator family as ctx->buffer (Rule 43). */
    output.data = ngx_http_markdown_decomp_alloc_output(r, output.size,
        ngx_http_markdown_decomp_brotli_cleanup, decoder);
    if (output.data == NULL) {
        return NGX_ERROR;
    }
    rc = ngx_http_markdown_decomp_brotli_stream(
        r, decoder, input_data, input_size, output_limit, &output);
    if (rc != NGX_OK) {
        if (rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED
            && ratio_limited)
        {
            return NGX_HTTP_MARKDOWN_DECOMP_RATIO_EXCEEDED;
        }
        return rc;
    }

    if (ratio_limited && output.total_out > ratio_limit) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size (%uz) exceeds "
                     "decompression ratio limit (%uz), "
                     "category=resource_limit",
                     output.total_out, ratio_limit);
        return ngx_http_markdown_decomp_brotli_fail(
            decoder, output.data,
            NGX_HTTP_MARKDOWN_DECOMP_RATIO_EXCEEDED);
    }

    /* Check if decompressed size exceeds decompression budget */
    if (output.total_out > conf->decompress.max_size) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: decompressed size (%uz) exceeds decompression budget (%uz), "
                     "category=resource_limit",
                     output.total_out, conf->decompress.max_size);
        return ngx_http_markdown_decomp_brotli_fail(
            decoder, output.data,
            NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED);
    }

    /* Clean up decoder instance (brotli decoder cleanup) */
    BrotliDecoderDestroyInstance(decoder);

    /* Build the output chain wrapping the decompressed data directly
     * (avoids a second allocation + memcpy). Shared with the zlib path
     * via ngx_http_markdown_decomp_build_output_chain so the two backends
     * cannot drift apart on buffer/chain setup. */
    if (ngx_http_markdown_decomp_build_output_chain(
            r, output.data, output.size, output.total_out, 1, out) != NGX_OK)
    {
        ngx_free(output.data);
        return NGX_ERROR;
    }

    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: brotli decompression succeeded, "
                  "compressed=%uz bytes, decompressed=%uz bytes, ratio=%.1f",
                  input_size, output.total_out,
                  input_size > 0
                      ? (float) output.total_out / input_size : 0.0f);

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

/**
 * Decompresses response data according to the specified compression type.
 *
 * @param r    Nginx request used for request context and logging.
 * @param type Compression type used to select the decompression method.
 * @param in   Compressed input chain.
 * @param out  Output chain for the decompressed data.
 * @returns The decompression status, including NGX_OK on success,
 *          NGX_DECLINED for unsupported compression, or an error status.
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
