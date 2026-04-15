#ifndef NGX_HTTP_MARKDOWN_EXPORTS_H
#define NGX_HTTP_MARKDOWN_EXPORTS_H

/*
 * Shared exported helper prototypes.
 *
 * This header is included by both the public module header and implementation
 * headers to keep one canonical declaration source and avoid signature drift.
 * It expects ngx_http_markdown_conf_t and ngx_http_request_t to be visible.
 */

ngx_int_t ngx_http_markdown_is_authenticated(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf);
ngx_int_t ngx_http_markdown_modify_cache_control_for_auth(
    ngx_http_request_t *r);

#endif /* NGX_HTTP_MARKDOWN_EXPORTS_H */
