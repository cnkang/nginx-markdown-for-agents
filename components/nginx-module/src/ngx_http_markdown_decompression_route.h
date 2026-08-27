#ifndef NGX_HTTP_MARKDOWN_DECOMPRESSION_ROUTE_H
#define NGX_HTTP_MARKDOWN_DECOMPRESSION_ROUTE_H

#include "markdown_converter.h"

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
