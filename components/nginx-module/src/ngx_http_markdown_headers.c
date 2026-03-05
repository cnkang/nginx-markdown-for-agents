/*
 * NGINX Markdown Filter Module - Header Management
 *
 * Production entry point that wires NGINX types and APIs to the
 * shared header-update implementation.
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

#include "ngx_http_markdown_headers_impl.h"
