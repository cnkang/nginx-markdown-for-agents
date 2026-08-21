/*
 * NGINX Markdown Filter Module - Header Management
 *
 * Production entry point that wires NGINX types and APIs to the
 * shared header-update implementation.
 */

#ifndef NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
#define NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL 1
#endif
#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

#include "ngx_http_markdown_headers_impl.h"
