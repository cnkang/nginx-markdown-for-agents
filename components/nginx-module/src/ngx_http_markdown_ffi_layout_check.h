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
 *
 * Platform assumption: LP64 (sizeof(void*) == 8, sizeof(size_t) == 8).
 * These assertions are valid on both Linux (gcc) and macOS (clang)
 * 64-bit targets.
 */

#include <stddef.h>
#include <limits.h>
#include "markdown_converter.h"

/* Return non-zero only when the linked Rust archive matches this header. */
static inline int
ngx_http_markdown_ffi_abi_matches(uint32_t actual)
{
    return actual == MARKDOWN_ABI_VERSION;
}

/* Return non-zero only when the full 4-tuple ABI handshake passes. */
static inline int
ngx_http_markdown_ffi_abi_tuple_matches(uint32_t abi, uint64_t hdr,
                                        uint64_t sym, uint64_t layout)
{
    return abi == MARKDOWN_ABI_VERSION
        && hdr == MARKDOWN_HEADER_HASH
        && sym == MARKDOWN_SYMBOL_SET_HASH
        && layout == MARKDOWN_LAYOUT_FINGERPRINT;
}

/*
 * Guard: these layout checks are only valid on LP64 platforms
 * (64-bit pointers, 64-bit size_t). Fail explicitly on other
 * data models (ILP32, LLP64, etc.) to prevent silent misuse.
 */
#if ULONG_MAX != 18446744073709551615UL
#error "FFI layout checks require LP64 targets (64-bit unsigned long)"
#endif
_Static_assert(sizeof(void *) == 8,
    "FFI layout checks require 64-bit pointers (LP64)");
_Static_assert(sizeof(size_t) == 8,
    "FFI layout checks require 64-bit size_t (LP64)");

/* ----------------------------------------------------------------
 * MarkdownOptions layout (128 bytes on LP64).
 *
 * Fields:
 *   flavor                         : u32           offset   0
 *   timeout_ms                     : u32           offset   4
 *   generate_etag                  : u8            offset   8
 *   estimate_tokens                : u8            offset   9
 *   front_matter                   : u8            offset  10
 *   (padding)                                      offset  11..15
 *   content_type                   : *const u8     offset  16
 *   content_type_len               : usize         offset  24
 *   base_url                       : *const u8     offset  32
 *   base_url_len                   : usize         offset  40
 *   streaming_budget               : u64           offset  48
 *   prune_noise                    : u32           offset  56
 *   (padding)                                      offset  60..63
 *   prune_selectors                : *const u8     offset  64
 *   prune_selector_len             : usize         offset  72
 *   prune_protection_selectors     : *const u8     offset  80
 *   prune_protection_selector_len  : usize         offset  88
 *   memory_budget                  : u64           offset  96
 *   parse_timeout_ms               : u32           offset 104
 *   (padding)                                      offset 108..111
 *   parser_memory_budget           : u64           offset 112
 *   flush_threshold                 : u32           offset 120
 *   (padding)                                      offset 124..127
 * Total: 128 bytes, align 8
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(MarkdownOptions) == 128,
    "MarkdownOptions size must match Rust (128 bytes on 64-bit)");
_Static_assert(offsetof(MarkdownOptions, flavor) == 0,
    "MarkdownOptions.flavor offset must be 0");
_Static_assert(offsetof(MarkdownOptions, timeout_ms) == 4,
    "MarkdownOptions.timeout_ms offset must be 4");
_Static_assert(offsetof(MarkdownOptions, generate_etag) == 8,
    "MarkdownOptions.generate_etag offset must be 8");
_Static_assert(offsetof(MarkdownOptions, estimate_tokens) == 9,
    "MarkdownOptions.estimate_tokens offset must be 9");
_Static_assert(offsetof(MarkdownOptions, front_matter) == 10,
    "MarkdownOptions.front_matter offset must be 10");
_Static_assert(offsetof(MarkdownOptions, content_type) == 16,
    "MarkdownOptions.content_type offset must be 16");
_Static_assert(offsetof(MarkdownOptions, content_type_len) == 24,
    "MarkdownOptions.content_type_len offset must be 24");
_Static_assert(offsetof(MarkdownOptions, base_url) == 32,
    "MarkdownOptions.base_url offset must be 32");
_Static_assert(offsetof(MarkdownOptions, base_url_len) == 40,
    "MarkdownOptions.base_url_len offset must be 40");
_Static_assert(offsetof(MarkdownOptions, streaming_budget) == 48,
    "MarkdownOptions.streaming_budget offset must be 48");
_Static_assert(offsetof(MarkdownOptions, prune_noise) == 56,
    "MarkdownOptions.prune_noise offset must be 56");
_Static_assert(offsetof(MarkdownOptions, prune_selectors) == 64,
    "MarkdownOptions.prune_selectors offset must be 64");
_Static_assert(offsetof(MarkdownOptions, prune_selector_len) == 72,
    "MarkdownOptions.prune_selector_len offset must be 72");
_Static_assert(offsetof(MarkdownOptions, prune_protection_selectors) == 80,
    "MarkdownOptions.prune_protection_selectors offset must be 80");
_Static_assert(offsetof(MarkdownOptions, prune_protection_selector_len) == 88,
    "MarkdownOptions.prune_protection_selector_len offset must be 88");
_Static_assert(offsetof(MarkdownOptions, memory_budget) == 96,
    "MarkdownOptions.memory_budget offset must be 96");
_Static_assert(offsetof(MarkdownOptions, parse_timeout_ms) == 104,
    "MarkdownOptions.parse_timeout_ms offset must be 104");
_Static_assert(offsetof(MarkdownOptions, parser_memory_budget) == 112,
    "MarkdownOptions.parser_memory_budget offset must be 112");
_Static_assert(offsetof(MarkdownOptions, flush_threshold) == 120,
    "MarkdownOptions.flush_threshold offset must be 120");

/* ----------------------------------------------------------------
 * MarkdownResult layout (64 bytes on LP64).
 *
 * Fields:
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
 * ---------------------------------------------------------------- */
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

/* ----------------------------------------------------------------
 * FFIAcceptResult layout (2 bytes).
 *   should_convert : u8   offset 0
 *   reason         : u8   offset 1
 * Total: 2 bytes, align 1
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIAcceptResult) == 2,
    "FFIAcceptResult size must match Rust (2 bytes)");
_Static_assert(offsetof(FFIAcceptResult, should_convert) == 0,
    "FFIAcceptResult.should_convert offset must be 0");
_Static_assert(offsetof(FFIAcceptResult, reason) == 1,
    "FFIAcceptResult.reason offset must be 1");

/* ----------------------------------------------------------------
 * FFIHeaderEntry layout (40 bytes on LP64).
 *   op_type   : u8            offset  0
 *   (padding)                  offset  1..7
 *   key       : *const u8     offset  8
 *   key_len   : usize         offset 16
 *   value     : *const u8     offset 24
 *   value_len : usize         offset 32
 * Total: 40 bytes, align 8
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIHeaderEntry) == 40,
    "FFIHeaderEntry size must match Rust (40 bytes on 64-bit)");
_Static_assert(offsetof(FFIHeaderEntry, op_type) == 0,
    "FFIHeaderEntry.op_type offset must be 0");
_Static_assert(offsetof(FFIHeaderEntry, key) == 8,
    "FFIHeaderEntry.key offset must be 8");
_Static_assert(offsetof(FFIHeaderEntry, key_len) == 16,
    "FFIHeaderEntry.key_len offset must be 16");
_Static_assert(offsetof(FFIHeaderEntry, value) == 24,
    "FFIHeaderEntry.value offset must be 24");
_Static_assert(offsetof(FFIHeaderEntry, value_len) == 32,
    "FFIHeaderEntry.value_len offset must be 32");

/* ----------------------------------------------------------------
 * FFIHeaderPlan layout (24 bytes on LP64).
 *   handle  : *mut FFIHeaderPlanHandle  offset  0
 *   entries : *const FFIHeaderEntry     offset  8
 *   count   : usize                     offset 16
 * Total: 24 bytes, align 8
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIHeaderPlan) == 24,
    "FFIHeaderPlan size must match Rust (24 bytes on 64-bit)");
_Static_assert(offsetof(FFIHeaderPlan, handle) == 0,
    "FFIHeaderPlan.handle offset must be 0");
_Static_assert(offsetof(FFIHeaderPlan, entries) == 8,
    "FFIHeaderPlan.entries offset must be 8");
_Static_assert(offsetof(FFIHeaderPlan, count) == 16,
    "FFIHeaderPlan.count offset must be 16");

/* ----------------------------------------------------------------
 * Error code distinctness (matches abi.rs test_error_codes_distinct).
 * Only the unconditional codes are checked here; streaming-gated
 * codes are verified at runtime by the Rust test suite.
 * ---------------------------------------------------------------- */
_Static_assert(ERROR_SUCCESS != ERROR_PARSE,
    "error codes must be distinct");
_Static_assert(ERROR_PARSE != ERROR_ENCODING,
    "error codes must be distinct");
_Static_assert(ERROR_ENCODING != ERROR_TIMEOUT,
    "error codes must be distinct");
_Static_assert(ERROR_TIMEOUT != ERROR_MEMORY_LIMIT,
    "error codes must be distinct");
_Static_assert(ERROR_MEMORY_LIMIT != ERROR_INVALID_INPUT,
    "error codes must be distinct");
_Static_assert(ERROR_INVALID_INPUT != ERROR_DECOMPRESSION_BUDGET_EXCEEDED,
    "error codes must be distinct");
_Static_assert(ERROR_DECOMPRESSION_BUDGET_EXCEEDED != ERROR_PARSE_TIMEOUT,
    "error codes must be distinct");
_Static_assert(ERROR_PARSE_TIMEOUT != ERROR_PARSE_BUDGET_EXCEEDED,
    "error codes must be distinct");
_Static_assert(ERROR_PARSE_BUDGET_EXCEEDED != ERROR_DECOMPRESSION_FORMAT_ERROR,
    "error codes must be distinct");
_Static_assert(ERROR_DECOMPRESSION_FORMAT_ERROR != ERROR_DECOMPRESSION_TRUNCATED_INPUT,
    "error codes must be distinct");
_Static_assert(ERROR_DECOMPRESSION_TRUNCATED_INPUT != ERROR_DECOMPRESSION_IO_ERROR,
    "error codes must be distinct");
_Static_assert(ERROR_DECOMPRESSION_IO_ERROR != ERROR_INTERNAL,
    "error codes must be distinct");

/* ----------------------------------------------------------------
 * Negotiation reason code distinctness.
 * ---------------------------------------------------------------- */
_Static_assert(NEGOTIATE_REASON_CONVERT != NEGOTIATE_REASON_NO_ACCEPT,
    "negotiate reason codes must be distinct");
_Static_assert(NEGOTIATE_REASON_NO_ACCEPT != NEGOTIATE_REASON_LOWER_Q,
    "negotiate reason codes must be distinct");
_Static_assert(NEGOTIATE_REASON_LOWER_Q != NEGOTIATE_REASON_EXPLICIT_REJECT,
    "negotiate reason codes must be distinct");
_Static_assert(NEGOTIATE_REASON_EXPLICIT_REJECT != NEGOTIATE_REASON_MALFORMED,
    "negotiate reason codes must be distinct");
_Static_assert(NEGOTIATE_REASON_MALFORMED != NEGOTIATE_REASON_INTERNAL_ERROR,
    "negotiate reason codes must be distinct");

/* ----------------------------------------------------------------
 * FFIDecompResult layout (24 bytes on LP64).
 *   output        : *mut u8    offset  0, size 8
 *   output_len    : usize      offset  8, size 8
 *   error_category : u32       offset 16, size 4
 *   (padding)                  offset 20..23
 * Total: 24 bytes, align 8
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIDecompResult) == 24,
    "FFIDecompResult size must match Rust (24 bytes on 64-bit)");
_Static_assert(offsetof(FFIDecompResult, output) == 0,
    "FFIDecompResult.output offset must be 0");
_Static_assert(offsetof(FFIDecompResult, output_len) == 8,
    "FFIDecompResult.output_len offset must be 8");
_Static_assert(offsetof(FFIDecompResult, error_category) == 16,
    "FFIDecompResult.error_category offset must be 16");

/* ----------------------------------------------------------------
 * FFIDynconfResult layout (72 bytes on LP64).
 *   error_code         : u32        offset  0, size 4
 *   error_message      : *const u8  offset  8, size 8
 *   error_message_len  : usize      offset 16, size 8
 *   source_digest      : *const u8  offset 24, size 8
 *   source_digest_len  : usize      offset 32, size 8
 *   active_digest      : *const u8  offset 40, size 8
 *   active_digest_len  : usize      offset 48, size 8
 *   filter             : u8         offset 56, size 1
 *   prune_noise        : u8         offset 57, size 1
 *   log_verbosity      : u8         offset 58, size 1
 *   error_policy       : u8         offset 59, size 1
 *   streaming_buffer   : u64        offset 64, size 8
 * Total: 72 bytes, align 8
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIDynconfResult) == 72,
    "FFIDynconfResult size must match Rust (72 bytes on 64-bit)");
_Static_assert(offsetof(FFIDynconfResult, error_code) == 0,
    "FFIDynconfResult.error_code offset must be 0");
_Static_assert(offsetof(FFIDynconfResult, error_message) == 8,
    "FFIDynconfResult.error_message offset must be 8");
_Static_assert(offsetof(FFIDynconfResult, error_message_len) == 16,
    "FFIDynconfResult.error_message_len offset must be 16");
_Static_assert(offsetof(FFIDynconfResult, source_digest) == 24,
    "FFIDynconfResult.source_digest offset must be 24");
_Static_assert(offsetof(FFIDynconfResult, source_digest_len) == 32,
    "FFIDynconfResult.source_digest_len offset must be 32");
_Static_assert(offsetof(FFIDynconfResult, active_digest) == 40,
    "FFIDynconfResult.active_digest offset must be 40");
_Static_assert(offsetof(FFIDynconfResult, active_digest_len) == 48,
    "FFIDynconfResult.active_digest_len offset must be 48");
_Static_assert(offsetof(FFIDynconfResult, filter) == 56,
    "FFIDynconfResult.filter offset must be 56");
_Static_assert(offsetof(FFIDynconfResult, prune_noise) == 57,
    "FFIDynconfResult.prune_noise offset must be 57");
_Static_assert(offsetof(FFIDynconfResult, log_verbosity) == 58,
    "FFIDynconfResult.log_verbosity offset must be 58");
_Static_assert(offsetof(FFIDynconfResult, error_policy) == 59,
    "FFIDynconfResult.error_policy offset must be 59");
_Static_assert(offsetof(FFIDynconfResult, streaming_buffer) == 64,
    "FFIDynconfResult.streaming_buffer offset must be 64");

/* ----------------------------------------------------------------
 * FFIEncodingChainResult layout (12 bytes on LP64).
 *   classification  : u8       offset  0, size 1
 *   (padding)                 offset  1..3
 *   layer_count     : u32      offset  4, size 4
 *   layers          : [u8;3]   offset  8, size 3
 *   identity_present: u8       offset 11, size 1
 * Total: 12 bytes, align 4
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIEncodingChainResult) == 12,
    "FFIEncodingChainResult size must match Rust (12 bytes on 64-bit)");
_Static_assert(offsetof(FFIEncodingChainResult, classification) == 0,
    "FFIEncodingChainResult.classification offset must be 0");
_Static_assert(offsetof(FFIEncodingChainResult, layer_count) == 4,
    "FFIEncodingChainResult.layer_count offset must be 4");
_Static_assert(offsetof(FFIEncodingChainResult, layers) == 8,
    "FFIEncodingChainResult.layers offset must be 8");
_Static_assert(offsetof(FFIEncodingChainResult, identity_present) == 11,
    "FFIEncodingChainResult.identity_present offset must be 11");

/* ----------------------------------------------------------------
 * FFIChainDecodeResult layout (24 bytes on LP64).
 *   output        : *mut u8  offset  0, size 8
 *   output_len    : usize    offset  8, size 8
 *   error_category: u32      offset 16, size 4
 *   (padding)                offset 20..23
 * Total: 24 bytes, align 8
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIChainDecodeResult) == 24,
    "FFIChainDecodeResult size must match Rust (24 bytes on 64-bit)");
_Static_assert(offsetof(FFIChainDecodeResult, output) == 0,
    "FFIChainDecodeResult.output offset must be 0");
_Static_assert(offsetof(FFIChainDecodeResult, output_len) == 8,
    "FFIChainDecodeResult.output_len offset must be 8");
_Static_assert(offsetof(FFIChainDecodeResult, error_category) == 16,
    "FFIChainDecodeResult.error_category offset must be 16");

/* ----------------------------------------------------------------
 * FFIBaseUrlInput layout (144 bytes on LP64) for the current ABI.
 *   source_ip             : *const u8                     offset   0
 *   source_ip_len         : usize                         offset   8
 *   trusted               : *const MarkdownTrustedProxies offset  16
 *   forwarded             : *const u8                     offset  24
 *   forwarded_len         : usize                         offset  32
 *   x_forwarded_for       : *const u8                     offset  40
 *   x_forwarded_for_len   : usize                         offset  48
 *   x_forwarded_proto     : *const u8                     offset  56
 *   x_forwarded_proto_len : usize                         offset  64
 *   x_forwarded_host      : *const u8                     offset  72
 *   x_forwarded_host_len  : usize                         offset  80
 *   x_forwarded_port      : *const u8                     offset  88
 *   x_forwarded_port_len  : usize                         offset  96
 *   host                  : *const u8                     offset 104
 *   host_len              : usize                         offset 112
 *   is_unix_socket        : u8                            offset 120
 *   trusted_configured    : u8                            offset 121
 *   (padding)                                             offset 122..127
 *   direct_scheme         : *const u8                     offset 128
 *   direct_scheme_len     : usize                         offset 136
 * Total: 144 bytes, align 8
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIBaseUrlInput) == 144,
    "FFIBaseUrlInput size must match Rust (144 bytes on 64-bit)");
_Static_assert(offsetof(FFIBaseUrlInput, source_ip) == 0,
    "FFIBaseUrlInput.source_ip offset must be 0");
_Static_assert(offsetof(FFIBaseUrlInput, source_ip_len) == 8,
    "FFIBaseUrlInput.source_ip_len offset must be 8");
_Static_assert(offsetof(FFIBaseUrlInput, trusted) == 16,
    "FFIBaseUrlInput.trusted offset must be 16");
_Static_assert(offsetof(FFIBaseUrlInput, forwarded) == 24,
    "FFIBaseUrlInput.forwarded offset must be 24");
_Static_assert(offsetof(FFIBaseUrlInput, forwarded_len) == 32,
    "FFIBaseUrlInput.forwarded_len offset must be 32");
_Static_assert(offsetof(FFIBaseUrlInput, x_forwarded_for) == 40,
    "FFIBaseUrlInput.x_forwarded_for offset must be 40");
_Static_assert(offsetof(FFIBaseUrlInput, x_forwarded_for_len) == 48,
    "FFIBaseUrlInput.x_forwarded_for_len offset must be 48");
_Static_assert(offsetof(FFIBaseUrlInput, x_forwarded_proto) == 56,
    "FFIBaseUrlInput.x_forwarded_proto offset must be 56");
_Static_assert(offsetof(FFIBaseUrlInput, x_forwarded_proto_len) == 64,
    "FFIBaseUrlInput.x_forwarded_proto_len offset must be 64");
_Static_assert(offsetof(FFIBaseUrlInput, x_forwarded_host) == 72,
    "FFIBaseUrlInput.x_forwarded_host offset must be 72");
_Static_assert(offsetof(FFIBaseUrlInput, x_forwarded_host_len) == 80,
    "FFIBaseUrlInput.x_forwarded_host_len offset must be 80");
_Static_assert(offsetof(FFIBaseUrlInput, x_forwarded_port) == 88,
    "FFIBaseUrlInput.x_forwarded_port offset must be 88");
_Static_assert(offsetof(FFIBaseUrlInput, x_forwarded_port_len) == 96,
    "FFIBaseUrlInput.x_forwarded_port_len offset must be 96");
_Static_assert(offsetof(FFIBaseUrlInput, host) == 104,
    "FFIBaseUrlInput.host offset must be 104");
_Static_assert(offsetof(FFIBaseUrlInput, host_len) == 112,
    "FFIBaseUrlInput.host_len offset must be 112");
_Static_assert(offsetof(FFIBaseUrlInput, is_unix_socket) == 120,
    "FFIBaseUrlInput.is_unix_socket offset must be 120");
_Static_assert(offsetof(FFIBaseUrlInput, trusted_configured) == 121,
    "FFIBaseUrlInput.trusted_configured offset must be 121");
_Static_assert(offsetof(FFIBaseUrlInput, direct_scheme) == 128,
    "FFIBaseUrlInput.direct_scheme offset must be 128");
_Static_assert(offsetof(FFIBaseUrlInput, direct_scheme_len) == 136,
    "FFIBaseUrlInput.direct_scheme_len offset must be 136");

/* ----------------------------------------------------------------
 * FFIBaseUrlDecision layout (16 bytes on LP64) - spec 47.
 *   base_url_len : usize  offset 0
 *   reason       : u8     offset 8
 *   source       : u8     offset 9
 *   (padding)            offset 10..15
 * Total: 16 bytes, align 8
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIBaseUrlDecision) == 16,
    "FFIBaseUrlDecision size must match Rust (16 bytes on 64-bit)");
_Static_assert(offsetof(FFIBaseUrlDecision, base_url_len) == 0,
    "FFIBaseUrlDecision.base_url_len offset must be 0");
_Static_assert(offsetof(FFIBaseUrlDecision, reason) == 8,
    "FFIBaseUrlDecision.reason offset must be 8");
_Static_assert(offsetof(FFIBaseUrlDecision, source) == 9,
    "FFIBaseUrlDecision.source offset must be 9");

/* ----------------------------------------------------------------
 * FFIStr layout (16 bytes on LP64) - shared borrowed string.
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIStr) == 16,
    "FFIStr size must match Rust (16 bytes on 64-bit)");
_Static_assert(offsetof(FFIStr, data) == 0,
    "FFIStr.data offset must be 0");
_Static_assert(offsetof(FFIStr, len) == 8,
    "FFIStr.len offset must be 8");

/* ----------------------------------------------------------------
 * FFIEligibilityInput layout (72 bytes on LP64) - spec 49.
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIEligibilityInput) == 72,
    "FFIEligibilityInput size must match Rust (72 bytes on 64-bit)");
_Static_assert(offsetof(FFIEligibilityInput, filter_enabled) == 0,
    "FFIEligibilityInput.filter_enabled offset must be 0");
_Static_assert(offsetof(FFIEligibilityInput, method_get_or_head) == 1,
    "FFIEligibilityInput.method_get_or_head offset must be 1");
_Static_assert(offsetof(FFIEligibilityInput, has_range_header) == 2,
    "FFIEligibilityInput.has_range_header offset must be 2");
_Static_assert(offsetof(FFIEligibilityInput, status) == 4,
    "FFIEligibilityInput.status offset must be 4");
_Static_assert(offsetof(FFIEligibilityInput, content_type) == 8,
    "FFIEligibilityInput.content_type offset must be 8");
_Static_assert(offsetof(FFIEligibilityInput, content_type_len) == 16,
    "FFIEligibilityInput.content_type_len offset must be 16");
_Static_assert(offsetof(FFIEligibilityInput, content_types) == 24,
    "FFIEligibilityInput.content_types offset must be 24");
_Static_assert(offsetof(FFIEligibilityInput, content_types_count) == 32,
    "FFIEligibilityInput.content_types_count offset must be 32");
_Static_assert(offsetof(FFIEligibilityInput, stream_types) == 40,
    "FFIEligibilityInput.stream_types offset must be 40");
_Static_assert(offsetof(FFIEligibilityInput, stream_types_count) == 48,
    "FFIEligibilityInput.stream_types_count offset must be 48");
_Static_assert(offsetof(FFIEligibilityInput, content_length) == 56,
    "FFIEligibilityInput.content_length offset must be 56");
_Static_assert(offsetof(FFIEligibilityInput, body_limit) == 64,
    "FFIEligibilityInput.body_limit offset must be 64");

/* ----------------------------------------------------------------
 * FFIConditionalInput layout (72 bytes on LP64) - spec 49.
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIConditionalInput) == 72,
    "FFIConditionalInput size must match Rust (72 bytes on 64-bit)");
_Static_assert(offsetof(FFIConditionalInput, cache_validation) == 0,
    "FFIConditionalInput.cache_validation offset must be 0");
_Static_assert(offsetof(FFIConditionalInput, has_range) == 1,
    "FFIConditionalInput.has_range offset must be 1");
_Static_assert(offsetof(FFIConditionalInput, no_transform) == 2,
    "FFIConditionalInput.no_transform offset must be 2");
_Static_assert(offsetof(FFIConditionalInput, if_none_match) == 8,
    "FFIConditionalInput.if_none_match offset must be 8");
_Static_assert(offsetof(FFIConditionalInput, if_none_match_len) == 16,
    "FFIConditionalInput.if_none_match_len offset must be 16");
_Static_assert(offsetof(FFIConditionalInput, entity_etag) == 24,
    "FFIConditionalInput.entity_etag offset must be 24");
_Static_assert(offsetof(FFIConditionalInput, entity_etag_len) == 32,
    "FFIConditionalInput.entity_etag_len offset must be 32");
_Static_assert(offsetof(FFIConditionalInput, if_modified_since) == 40,
    "FFIConditionalInput.if_modified_since offset must be 40");
_Static_assert(offsetof(FFIConditionalInput, if_modified_since_len) == 48,
    "FFIConditionalInput.if_modified_since_len offset must be 48");
_Static_assert(offsetof(FFIConditionalInput, last_modified) == 56,
    "FFIConditionalInput.last_modified offset must be 56");
_Static_assert(offsetof(FFIConditionalInput, last_modified_len) == 64,
    "FFIConditionalInput.last_modified_len offset must be 64");

/* ----------------------------------------------------------------
 * FFIConditionalDecision layout (3 bytes) - spec 49.
 * ---------------------------------------------------------------- */
_Static_assert(sizeof(FFIConditionalDecision) == 3,
    "FFIConditionalDecision size must match Rust (3 bytes)");
_Static_assert(offsetof(FFIConditionalDecision, outcome) == 0,
    "FFIConditionalDecision.outcome offset must be 0");
_Static_assert(offsetof(FFIConditionalDecision, reason) == 1,
    "FFIConditionalDecision.reason offset must be 1");
_Static_assert(offsetof(FFIConditionalDecision, evaluated_header) == 2,
    "FFIConditionalDecision.evaluated_header offset must be 2");

#endif /* NGX_HTTP_MARKDOWN_FFI_LAYOUT_CHECK_H */
