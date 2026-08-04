#ifndef _NGX_HTTP_MARKDOWN_OUTPUT_DECISION_IMPL_H_INCLUDED_
#define _NGX_HTTP_MARKDOWN_OUTPUT_DECISION_IMPL_H_INCLUDED_

/*
 * Hybrid output path decision result.
 *
 * Determines whether a streaming chunk is delivered via the
 * zero-copy buffer factory (Rust memory referenced directly)
 * or the existing pool-copy path (data copied into pool).
 *
 * Zero-copy was removed in 0.9.2 (directive deleted); always pool-copy.
 */
typedef enum {
    NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY  = 0,
    NGX_HTTP_MARKDOWN_OUTPUT_ZERO_COPY  = 1
} ngx_http_markdown_output_decision_t;


/*
 * Hybrid output decision function.
 *
 * Zero-copy removed in 0.9.2: always returns POOL_COPY.
 *
 * conf               - location configuration (unused)
 * chunk_is_terminal  - whether this is the last chunk (unused)
 * backpressure_active - whether downstream backpressure is active (unused)
 *
 * Returns:
 *   NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY
 */
static inline ngx_http_markdown_output_decision_t
ngx_http_markdown_hybrid_output_decision(
    const ngx_http_markdown_conf_t *conf,
    ngx_flag_t chunk_is_terminal,
    ngx_flag_t backpressure_active)
{
    (void) conf;
    (void) chunk_is_terminal;
    (void) backpressure_active;

    return NGX_HTTP_MARKDOWN_OUTPUT_POOL_COPY;
}

#endif /* _NGX_HTTP_MARKDOWN_OUTPUT_DECISION_IMPL_H_INCLUDED_ */
