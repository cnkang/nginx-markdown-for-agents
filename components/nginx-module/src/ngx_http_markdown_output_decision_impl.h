#ifndef _NGX_HTTP_MARKDOWN_OUTPUT_DECISION_IMPL_H_INCLUDED_
#define _NGX_HTTP_MARKDOWN_OUTPUT_DECISION_IMPL_H_INCLUDED_

/*
 * Hybrid output path decision result.
 *
 * Determines whether a streaming chunk is delivered via the
 * zero-copy buffer factory (Rust memory referenced directly)
 * or the existing pool-copy path (data copied into pool).
 */
typedef enum {
    NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY  = 0,
    NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY  = 1
} ngx_http_markdown_output_decision_t;


/*
 * Hybrid output decision function.
 *
 * Evaluates three guards to determine the output path for a
 * streaming chunk.  Zero-copy is selected only when ALL guards
 * are clear; any single guard active forces pool-copy.
 *
 * Decision matrix:
 *   Feature OFF    -> POOL_COPY (Req 3.1)
 *   Terminal chunk -> POOL_COPY (Req 3.3)
 *   Backpressure  -> POOL_COPY (Req 3.4)
 *   All clear     -> ZERO_COPY (Req 3.2)
 *
 * conf               - location configuration (for zero_copy flag)
 * chunk_is_terminal  - 1 if this is a last_buf chunk
 * backpressure_active - 1 if pending output exists
 *
 * Returns:
 *   NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY or
 *   NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY
 */
static inline ngx_http_markdown_output_decision_t
ngx_http_markdown_hybrid_output_decision(
    const ngx_http_markdown_conf_t *conf,
    ngx_flag_t chunk_is_terminal,
    ngx_flag_t backpressure_active)
{
    /* Feature gate OFF -> pool-copy (Req 3.1) */
    if (conf->stream.zero_copy != 1) {
        return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
    }

    /* Terminal chunk -> pool-copy (Req 3.3) */
    if (chunk_is_terminal) {
        return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
    }

    /* Backpressure active -> pool-copy (Req 3.4) */
    if (backpressure_active) {
        return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
    }

    /* All guards clear -> zero-copy (Req 3.2) */
    return NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY;
}

#endif /* _NGX_HTTP_MARKDOWN_OUTPUT_DECISION_IMPL_H_INCLUDED_ */
