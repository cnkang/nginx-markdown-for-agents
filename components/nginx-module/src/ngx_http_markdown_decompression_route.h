#ifndef NGX_HTTP_MARKDOWN_DECOMPRESSION_ROUTE_H
#define NGX_HTTP_MARKDOWN_DECOMPRESSION_ROUTE_H

#include "markdown_converter.h"

/*
 * Rust full-buffer decompression keeps the original request payload until
 * the caller commits the result.  The Rust-owned result and the C-owned
 * delivery buffer therefore coexist with that payload.  A defensive chain
 * linearization may add one more input allocation; callers account for it
 * separately through input_copy_size.
 */
#ifndef NGX_HTTP_MARKDOWN_NO_RUST_DECOMPRESS
static int
ngx_http_markdown_decompression_peak_within_budget(
    size_t source_size, size_t input_copy_size, size_t output_size,
    unsigned output_buffers, size_t memory_budget)
{
    size_t  peak;

    if (memory_budget == 0 || memory_budget == (size_t) -1) {
        return 1;
    }

    if (output_buffers != 0
        && output_size > ((size_t) -1) / output_buffers)
    {
        return 0;
    }
    peak = output_size * output_buffers;

    if (input_copy_size > ((size_t) -1) - peak) {
        return 0;
    }
    peak += input_copy_size;

    if (source_size > ((size_t) -1) - peak) {
        return 0;
    }
    peak += source_size;

    return peak <= memory_budget;
}
#endif /* NGX_HTTP_MARKDOWN_NO_RUST_DECOMPRESS */

/*
 * The C compression enum reserves NONE=0. The remaining values are the
 * generated Rust FFI format constants plus that offset. Keep this predicate
 * independent of request-path headers so standalone routing tests exercise
 * the same production capability decision.
 */
#ifdef MARKDOWN_STREAMING_ENABLED

static int
ngx_http_markdown_decompression_is_streamable(unsigned compression_type)
{
    if (compression_type == MARKDOWN_FORMAT_GZIP + 1
        || compression_type == MARKDOWN_FORMAT_DEFLATE + 1) {
        return 1;
    }

#ifdef NGX_HTTP_BROTLI
    if (compression_type == MARKDOWN_FORMAT_BROTLI + 1) {
        return 1;
    }
#endif

    return 0;
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_DECOMPRESSION_ROUTE_H */
