//! FFI (Foreign Function Interface) layer for C integration
//!
//! This module provides C-compatible data structures and functions for
//! integrating the Rust conversion engine with the NGINX C module.
//!
//! # FFI Boundary Contract
//!
//! ## CRITICAL: String Representation
//!
//! **All strings use UTF-8 bytes + length representation (NOT NUL-terminated C strings)**
//!
//! Exception: header plan entries in `FFIHeaderEntry` use NUL-terminated
//! key/value buffers (with `key_len`/`value_len` excluding the NUL), per the
//! `FFIHeaderEntry` contract. All other FFI strings are length-prefixed.
//!
//! This is a non-standard but intentional design choice that provides several benefits:
//! 1. **Binary Safety**: Supports embedded NUL bytes in content
//! 2. **Performance**: Avoids strlen() overhead in C code
//! 3. **Explicit Length**: Provides clear boundaries for memory operations
//! 4. **UTF-8 Correctness**: Length represents byte count, not character count
//!
//! ## Memory Management
//!
//! **Ownership Model:**
//! - Rust allocates all output memory using `Box<[u8]>`
//! - C receives raw pointers but does NOT own the memory
//! - C must call `markdown_result_free()` exactly once to deallocate
//! - After calling free, all pointers become invalid
//! - No shared ownership across the FFI boundary
//!
//! ## Error Handling Contract
//!
//! **Success Case:**
//! - `error_code = 0`
//! - `error_message = NULL`
//! - `error_len = 0`
//! - Output fields (markdown, etag) contain valid data
//!
//! **Error Case:**
//! - `error_code != 0` (see error code constants below)
//! - `error_message` points to UTF-8 error description
//! - `error_len` contains byte length of error message
//! - All output fields (markdown, etag) are NULL
//!
//! **Panic Safety:**
//! - FFI exports that invoke fallible or third-party logic guard against
//!   panics crossing the C boundary. `markdown_convert` and the streaming
//!   converter convert caught panics into error codes and
//!   messages; `markdown_decompress_bounded` (which runs attacker-controlled
//!   bytes through flate2/brotli) and conditional-decision exports wrap their
//!   fallible core in `catch_unwind` and fall back to a safe result on panic.
//! - Only explicitly verified constant/static lookups, such as
//!   `markdown_abi_version` and the `markdown_reason_code_*` accessors, avoid
//!   `catch_unwind`. Initialization helpers only write deterministic values.
//! - In all cases, C code will never observe Rust unwinding.
//!
//! ## Pointer Validation
//!
//! All FFI functions validate pointers before dereferencing:
//! - NULL pointers are rejected with appropriate error codes
//! - Invalid pointers may cause undefined behavior (C caller responsibility)
//! - All error paths ensure consistent error state
//!
//! ## Thread Safety
//!
//! - `MarkdownConverterHandle` is NOT thread-safe
//! - Each NGINX worker should have its own converter instance
//! - Concurrent calls to `markdown_convert()` on the same handle are unsafe
//! - Multiple converter instances can be used concurrently

pub(crate) mod abi;
mod convert;
mod exports;
pub(crate) mod memory;
mod options;

#[cfg(feature = "streaming")]
pub(crate) use options::clamp_chars_per_token;

#[cfg(feature = "streaming")]
mod streaming;

pub use abi::{
    DECOMP_CATEGORY_BUDGET_EXCEEDED, DECOMP_CATEGORY_FORMAT_ERROR, DECOMP_CATEGORY_INVALID_ARGS,
    DECOMP_CATEGORY_IO_ERROR, DECOMP_CATEGORY_RATIO_EXCEEDED, DECOMP_CATEGORY_TRUNCATED_INPUT,
    ENCODING_CHAIN_DEPTH_EXCEEDED, ENCODING_CHAIN_INVALID_ARGS, ENCODING_CHAIN_MALFORMED,
    ENCODING_CHAIN_UNKNOWN_TOKEN, ENCODING_CHAIN_VALID, ERROR_DECOMPRESSION_BUDGET_EXCEEDED,
    ERROR_ENCODING, ERROR_INTERNAL, ERROR_INVALID_INPUT, ERROR_MEMORY_LIMIT, ERROR_PARSE,
    ERROR_PARSE_BUDGET_EXCEEDED, ERROR_PARSE_TIMEOUT, ERROR_SUCCESS, ERROR_TIMEOUT,
    FFIAcceptResult, FFIBaseUrlDecision, FFIBaseUrlInput, FFIChainDecodeResult,
    FFIConditionalDecision, FFIConditionalInput, FFIDecompResult, FFIEligibilityInput,
    FFIEncodingChainResult, FFIErrorClass, FFIHeaderEntry, FFIHeaderPlan, FFIHeaderPlanHandle,
    FFIStr, MARKDOWN_ABI_VERSION, MARKDOWN_FORMAT_BROTLI, MARKDOWN_FORMAT_DEFLATE,
    MARKDOWN_FORMAT_GZIP, MARKDOWN_HEADER_HASH, MARKDOWN_LAYOUT_FINGERPRINT,
    MARKDOWN_SYMBOL_SET_HASH, MarkdownConverterHandle, MarkdownOptions, MarkdownResult,
    MarkdownTrustedProxies, NEGOTIATE_REASON_CONVERT, NEGOTIATE_REASON_EXPLICIT_REJECT,
    NEGOTIATE_REASON_INTERNAL_ERROR, NEGOTIATE_REASON_LOWER_Q, NEGOTIATE_REASON_MALFORMED,
    NEGOTIATE_REASON_NO_ACCEPT, NEGOTIATE_WILDCARD_ALLOW, NEGOTIATE_WILDCARD_STRICT,
};

#[cfg(feature = "streaming")]
pub use abi::{
    ERROR_BUDGET_EXCEEDED, ERROR_POST_COMMIT, ERROR_STREAMING_FALLBACK, POST_COMMIT_ABORT,
    POST_COMMIT_SAFE_FINISH,
};
pub use exports::{
    markdown_abi_header_hash, markdown_abi_layout_fingerprint, markdown_abi_symbol_set_hash,
    markdown_abi_version, markdown_base_url_input_init, markdown_build_header_plan,
    markdown_chain_decode_free, markdown_chain_decode_result_init, markdown_classify_error_code,
    markdown_convert, markdown_converter_free, markdown_converter_new, markdown_decide_base_url,
    markdown_decide_conditional, markdown_decide_eligibility, markdown_decode_encoding_chain,
    markdown_decomp_result_init, markdown_decompress_bounded, markdown_decompress_free,
    markdown_header_plan_free, markdown_header_plan_init, markdown_negotiate_accept,
    markdown_options_init, markdown_parse_encoding_chain, markdown_result_free,
    markdown_result_init, markdown_trusted_proxies_free, markdown_trusted_proxies_new,
    markdown_trusted_proxies_push,
};

#[cfg(feature = "streaming")]
pub use streaming::{
    StreamingConverterHandle, markdown_streaming_abort, markdown_streaming_feed,
    markdown_streaming_finalize, markdown_streaming_new_with_code, markdown_streaming_output_free,
    markdown_streaming_safe_finish,
};
