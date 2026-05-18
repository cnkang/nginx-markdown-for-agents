#ifndef NGX_HTTP_MARKDOWN_FFI_LAYOUT_CHECK_H
#define NGX_HTTP_MARKDOWN_FFI_LAYOUT_CHECK_H

/*
 * Compile-time layout validation for FFI structs shared between
 * the NGINX C module and the Rust conversion library.
 *
 * These _Static_assert checks ensure that C and Rust agree on
 * struct sizes and field offsets.  If any assertion fails, the
 * build breaks immediately, preventing silent ABI drift.
 *
 * The expected values are derived from the Rust layout tests in
 * components/rust-converter/src/ffi/abi.rs and must be updated
 * in the same change set whenever the corresponding #[repr(C)]
 * struct is modified.
 */

#include <stddef.h>
#include "markdown_converter.h"

/*
 * MarkdownResult layout (matches abi.rs test_markdown_result_layout).
 *
 * Fields on 64-bit (LP64):
 *   markdown          : *mut u8       offset  0, size 8
 *   markdown_len      : usize         offset  8, size 8
 *   etag              : *mut u8       offset 16, size 8
 *   etag_len          : usize         offset 24, size 8
 *   token_estimate    : u32           offset 32, size 4
 *   error_code        : u32           offset 36, size 4
 *   error_message     : *mut u8       offset 40, size 8
 *   error_len         : usize         offset 48, size 8
 *   peak_memory_est   : usize         offset 56, size 8
 * Total: 64 bytes, align 8
 */
_Static_assert(sizeof(MarkdownResult) == 64,
    "MarkdownResult size must match Rust (64 bytes on 64-bit)");
_Static_assert(offsetof(MarkdownResult, markdown) == 0,
    "MarkdownResult.markdown offset must be 0");
_Static_assert(offsetof(MarkdownResult, markdown_len) == 8,
    "MarkdownResult.markdown_len offset must be 8");
_Static_assert(offsetof(MarkdownResult, etag) == 16,
    "MarkdownResult.etag offset must be 16");
_Static_assert(offsetof(MarkdownResult, etag_len) == 24,
    "MarkdownResult.etag_len offset must be 24");
_Static_assert(offsetof(MarkdownResult, token_estimate) == 32,
    "MarkdownResult.token_estimate offset must be 32");
_Static_assert(offsetof(MarkdownResult, error_code) == 36,
    "MarkdownResult.error_code offset must be 36");
_Static_assert(offsetof(MarkdownResult, error_message) == 40,
    "MarkdownResult.error_message offset must be 40");
_Static_assert(offsetof(MarkdownResult, error_len) == 48,
    "MarkdownResult.error_len offset must be 48");
_Static_assert(offsetof(MarkdownResult, peak_memory_estimate) == 56,
    "MarkdownResult.peak_memory_estimate offset must be 56");

/*
 * FFIAcceptResult layout (matches abi.rs test_ffi_accept_result_layout).
 *   should_convert : u8   offset 0
 *   reason         : u8   offset 1
 * Total: 2 bytes, align 1
 */
_Static_assert(sizeof(FFIAcceptResult) == 2,
    "FFIAcceptResult size must match Rust (2 bytes)");

/*
 * FFIConditionalResult layout.
 *   result_code    : u8   offset 0
 *   matched_etag_len: u32  offset 4  (3 bytes padding after result_code)
 * Total: 8 bytes, align 4
 */
_Static_assert(sizeof(FFIConditionalResult) == 8,
    "FFIConditionalResult size must match Rust (8 bytes)");

/*
 * FFIDecisionResult layout.
 *   decision   : u8  offset 0
 *   reason_code: u8  offset 1
 * Total: 2 bytes, align 1
 */
_Static_assert(sizeof(FFIDecisionResult) == 2,
    "FFIDecisionResult size must match Rust (2 bytes)");

/*
 * Error codes must be distinct (matches abi.rs test_error_codes_distinct).
 * Only the unconditional codes are checked here; streaming-gated codes
 * are verified at runtime by the Rust test suite.
 */
_Static_assert(ERROR_SUCCESS != ERROR_PARSE, "error codes must be distinct");
_Static_assert(ERROR_PARSE != ERROR_ENCODING, "error codes must be distinct");
_Static_assert(ERROR_ENCODING != ERROR_TIMEOUT, "error codes must be distinct");
_Static_assert(ERROR_TIMEOUT != ERROR_MEMORY_LIMIT, "error codes must be distinct");
_Static_assert(ERROR_MEMORY_LIMIT != ERROR_INVALID_INPUT, "error codes must be distinct");
_Static_assert(ERROR_INVALID_INPUT != ERROR_DECOMPRESSION_BUDGET_EXCEEDED,
    "error codes must be distinct");
_Static_assert(ERROR_DECOMPRESSION_BUDGET_EXCEEDED != ERROR_PARSE_TIMEOUT,
    "error codes must be distinct");
_Static_assert(ERROR_PARSE_TIMEOUT != ERROR_PARSE_BUDGET_EXCEEDED,
    "error codes must be distinct");
_Static_assert(ERROR_PARSE_BUDGET_EXCEEDED != ERROR_INTERNAL,
    "error codes must be distinct");

/*
 * Negotiation reason codes must be distinct.
 */
_Static_assert(NEGOTIATE_REASON_CONVERT != NEGOTIATE_REASON_NO_ACCEPT,
    "negotiate reason codes must be distinct");
_Static_assert(NEGOTIATE_REASON_NO_ACCEPT != NEGOTIATE_REASON_LOWER_Q,
    "negotiate reason codes must be distinct");
_Static_assert(NEGOTIATE_REASON_LOWER_Q != NEGOTIATE_REASON_EXPLICIT_REJECT,
    "negotiate reason codes must be distinct");
_Static_assert(NEGOTIATE_REASON_EXPLICIT_REJECT != NEGOTIATE_REASON_MALFORMED,
    "negotiate reason codes must be distinct");

#endif /* NGX_HTTP_MARKDOWN_FFI_LAYOUT_CHECK_H */
