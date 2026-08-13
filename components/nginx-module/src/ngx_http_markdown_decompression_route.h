#ifndef NGX_HTTP_MARKDOWN_DECOMPRESSION_ROUTE_H
#define NGX_HTTP_MARKDOWN_DECOMPRESSION_ROUTE_H

/*
 * Compression enum values are part of the module's internal routing ABI:
 * NONE=0, GZIP=1, DEFLATE=2, BROTLI=3.  Keep this predicate independent of
 * the request-path headers so standalone routing tests can exercise the same
 * production capability decision.
 */
#ifdef MARKDOWN_STREAMING_ENABLED

static int
ngx_http_markdown_decompression_is_streamable(unsigned compression_type)
{
    if (compression_type == 1 || compression_type == 2) {
        return 1;
    }

#ifdef NGX_HTTP_BROTLI
    if (compression_type == 3) {
        return 1;
    }
#endif

    return 0;
}

#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_DECOMPRESSION_ROUTE_H */
