#ifndef NGX_HTTP_MARKDOWN_DURABLE_BYPASS_H
#define NGX_HTTP_MARKDOWN_DURABLE_BYPASS_H

typedef enum {
    NGX_HTTP_MARKDOWN_DURABLE_BYPASS_NONE = 0,
    NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER,
    NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY,
    NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_COMPLETED,
    NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_HEADER,
    NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_BODY,
    NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_COMPLETED
} ngx_http_markdown_durable_bypass_kind_t;

typedef struct {
    ngx_http_markdown_durable_bypass_kind_t kind;
} ngx_http_markdown_durable_bypass_marker_t;

/*
 * These marker addresses occupy the request context slot when a request
 * context cannot be allocated.  State transitions replace the address, so
 * no mutable state is shared between requests.  The objects are never
 * written through: the context slot is a `void *`, so the markers stay
 * non-const to avoid casts that drop a qualifier; only their addresses
 * are ever compared.
 */
static ngx_http_markdown_durable_bypass_marker_t
    ngx_http_markdown_failopen_header_marker = {
        NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER
    };
static ngx_http_markdown_durable_bypass_marker_t
    ngx_http_markdown_failopen_body_marker = {
        NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY
    };
static ngx_http_markdown_durable_bypass_marker_t
    ngx_http_markdown_failopen_completed_marker = {
        NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_COMPLETED
    };
static ngx_http_markdown_durable_bypass_marker_t
    ngx_http_markdown_subrequest_header_marker = {
        NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_HEADER
    };
static ngx_http_markdown_durable_bypass_marker_t
    ngx_http_markdown_subrequest_body_marker = {
        NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_BODY
    };
static ngx_http_markdown_durable_bypass_marker_t
    ngx_http_markdown_subrequest_completed_marker = {
        NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_COMPLETED
    };

static ngx_http_markdown_durable_bypass_kind_t
ngx_http_markdown_durable_bypass_kind(const void *value)
{
    if (value == (const void *) &ngx_http_markdown_failopen_header_marker) {
        return NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_HEADER;
    }
    if (value == (const void *) &ngx_http_markdown_failopen_body_marker) {
        return NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_BODY;
    }
    if (value == (const void *) &ngx_http_markdown_failopen_completed_marker) {
        return NGX_HTTP_MARKDOWN_DURABLE_BYPASS_FAILOPEN_COMPLETED;
    }
    if (value == (const void *) &ngx_http_markdown_subrequest_header_marker) {
        return NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_HEADER;
    }
    if (value == (const void *) &ngx_http_markdown_subrequest_body_marker) {
        return NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_BODY;
    }
    if (value
        == (const void *) &ngx_http_markdown_subrequest_completed_marker)
    {
        return NGX_HTTP_MARKDOWN_DURABLE_BYPASS_SUBREQUEST_COMPLETED;
    }

    return NGX_HTTP_MARKDOWN_DURABLE_BYPASS_NONE;
}

#endif /* NGX_HTTP_MARKDOWN_DURABLE_BYPASS_H */
