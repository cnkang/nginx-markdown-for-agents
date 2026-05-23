/*
 * NGINX Markdown Filter Module - Rust FFI Reason Code Accessor
 *
 * This file provides C-side wrappers around the Rust-defined reason code
 * FFI functions.  The Rust enum in decision/reason_code.rs is the SINGLE
 * SOURCE OF TRUTH for all reason codes.  C code should use these accessors
 * rather than defining independent reason code constants.
 *
 * Migration note (v0.7.0):
 *   The existing ngx_http_markdown_reason.c file defines C-side string
 *   literals for legacy eligibility-based reason codes.  Those are retained
 *   for backward compatibility during the transition period.  New code
 *   should use the FFI accessors in this file to obtain reason code strings
 *   from the Rust enum.  Once the full decision engine migration is complete,
 *   the legacy C-side constants in ngx_http_markdown_reason.c will be
 *   removed entirely.
 *
 * Requirements: REQ-0700-RUST-006
 */

#include <ngx_config.h>
#include <ngx_core.h>

#include "markdown_converter.h"


/*
 * Get the reason code string from the Rust enum via FFI.
 *
 * Calls the Rust-side markdown_reason_code_str() function and wraps
 * the result in an ngx_str_t for NGINX-idiomatic use.
 *
 * The returned ngx_str_t points to static Rust memory that is valid
 * for the lifetime of the process.  The caller must NOT free or modify
 * the data pointer.
 *
 * Parameters:
 *   code    - numeric reason code discriminant (0..REASON_CODE_COUNT-1)
 *   out_str - output ngx_str_t to populate
 *
 * Returns:
 *   NGX_OK if the code is valid and out_str was populated
 *   NGX_DECLINED if the code is invalid (out_str is zeroed)
 */
ngx_int_t
ngx_http_markdown_get_reason_code_str(uint32_t code, ngx_str_t *out_str)
{
    uintptr_t       len;

    if (out_str == NULL) {
        return NGX_ERROR;
    }

    len = 0;
    out_str->data = (u_char *) markdown_reason_code_str(code, &len);

    if (out_str->data == NULL) {
        out_str->len = 0;
        return NGX_DECLINED;
    }

    out_str->len = len;

    return NGX_OK;
}


/*
 * Get the Prometheus metric key from the Rust enum via FFI.
 *
 * Calls the Rust-side markdown_reason_code_metric_key() function and
 * wraps the result in an ngx_str_t for NGINX-idiomatic use.
 *
 * The returned ngx_str_t points to static Rust memory that is valid
 * for the lifetime of the process.  The caller must NOT free or modify
 * the data pointer.
 *
 * Parameters:
 *   code    - numeric reason code discriminant (0..REASON_CODE_COUNT-1)
 *   out_str - output ngx_str_t to populate
 *
 * Returns:
 *   NGX_OK if the code is valid and out_str was populated
 *   NGX_DECLINED if the code is invalid (out_str is zeroed)
 */
ngx_int_t
ngx_http_markdown_get_reason_code_metric_key(uint32_t code,
    ngx_str_t *out_str)
{
    uintptr_t       len;

    if (out_str == NULL) {
        return NGX_ERROR;
    }

    len = 0;
    out_str->data = (u_char *) markdown_reason_code_metric_key(code, &len);

    if (out_str->data == NULL) {
        out_str->len = 0;
        return NGX_DECLINED;
    }

    out_str->len = len;

    return NGX_OK;
}


/*
 * Get the total number of defined reason codes from the Rust enum.
 *
 * This allows C code to verify it handles all variants without
 * maintaining a separate count constant.
 *
 * Returns:
 *   The total number of reason code variants defined in Rust.
 */
uint32_t
ngx_http_markdown_reason_code_total_count(void)
{
    return markdown_reason_code_count();
}
