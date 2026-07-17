#ifndef _NGX_HTTP_H_INCLUDED_
#define _NGX_HTTP_H_INCLUDED_

#include "ngx_core.h"

/*
 * Stub for ngx_http_filter_finalize_request.
 * In production NGINX builds this is an inline function in ngx_http.h
 * that finalizes the request through the content handler chain.
 * For unit tests, we cannot access r->headers_out (the struct is only
 * forward-declared in the stub), so we simply return NGX_ERROR.  Tests
 * that exercise reject paths verify the configured error_status through
 * the conf struct, not through the finalizer return value.
 */
static ngx_inline ngx_int_t
ngx_http_filter_finalize_request(ngx_http_request_t *r, ngx_module_t *module,
    ngx_int_t rc)
{
    (void) r;
    (void) module;
    (void) rc;
    return NGX_ERROR;
}

#endif
