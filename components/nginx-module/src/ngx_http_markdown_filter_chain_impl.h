/*
 * NGINX Markdown Filter Module - Filter Chain Delegation
 *
 * This implementation stays in the main translation unit so it can use the
 * body filter pointer captured when the module is inserted into the chain.
 */

#ifndef NGX_HTTP_MARKDOWN_FILTER_CHAIN_IMPL_H
#define NGX_HTTP_MARKDOWN_FILTER_CHAIN_IMPL_H

ngx_int_t
ngx_http_markdown_next_body_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    return ngx_http_next_body_filter(r, in);
}

#endif /* NGX_HTTP_MARKDOWN_FILTER_CHAIN_IMPL_H */
