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


/* Type of the Rust FFI accessor that returns a static string + length.
 * Both markdown_reason_code_str() and markdown_reason_code_metric_key()
 * share this signature, so a single wrapper suffices. */
typedef const u_char *(*reason_str_accessor_t)(uint32_t code, uintptr_t *len);

/*
 * Internal helper: call a Rust-side static-string accessor and wrap its
 * result in an ngx_str_t.
 *
 * The returned ngx_str_t points to static Rust memory valid for the
 * lifetime of the process.  The caller must NOT free or modify the
 * data pointer.
 *
 * Parameters:
 *   code    - numeric reason code discriminant (0..REASON_CODE_COUNT-1)
 *   accessor - Rust FFI function returning the static string
 *   out_str - output ngx_str_t to populate
 *
 * Returns:
 *   NGX_OK if the code is valid and out_str was populated
 *   NGX_DECLINED if the code is invalid (out_str is zeroed)
 *   NGX_ERROR if out_str is NULL
 */
static ngx_int_t
ngx_http_markdown_fill_str_from_rust(uint32_t code,
    reason_str_accessor_t accessor, ngx_str_t *out_str)
{
    uintptr_t           len;
    const u_char       *p;

    if (out_str == NULL) {
        return NGX_ERROR;
    }

    len = 0;
    p = accessor(code, &len);
    out_str->data = (u_char *) p; /* NOSONAR: ngx_str_t.data is u_char* per NGINX API */

    if (out_str->data == NULL) {
        out_str->len = 0;
        return NGX_DECLINED;
    }

    out_str->len = len;

    return NGX_OK;
}


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
    return ngx_http_markdown_fill_str_from_rust(code,
                                                markdown_reason_code_str,
                                                out_str);
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
    return ngx_http_markdown_fill_str_from_rust(
        code, markdown_reason_code_metric_key, out_str);
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
