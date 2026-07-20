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
//!   panics crossing the C boundary. `markdown_convert` and the streaming /
//!   incremental converters convert caught panics into error codes and
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
mod memory;
mod options;

#[cfg(feature = "streaming")]
pub(crate) use options::clamp_chars_per_token;

#[cfg(feature = "incremental")]
mod incremental;

#[cfg(feature = "streaming")]
mod streaming;

pub use abi::{
    ERROR_DECOMPRESSION_BUDGET_EXCEEDED, ERROR_ENCODING, ERROR_INTERNAL, ERROR_INVALID_INPUT,
    ERROR_MEMORY_LIMIT, ERROR_PARSE, ERROR_PARSE_BUDGET_EXCEEDED, ERROR_PARSE_TIMEOUT,
    ERROR_SUCCESS, ERROR_TIMEOUT, FFI_CONFIG_NOT_SET_U8, FFI_CONFIG_NOT_SET_U32,
    FFI_CONFIG_NOT_SET_U64, FFI_PROFILE_BALANCED, FFI_PROFILE_NONE, FFI_PROFILE_STREAMING_FIRST,
    FFI_PROFILE_STRICT_CACHE, FFIAcceptResult, FFIBaseUrlDecision, FFIBaseUrlInput, FFIConflict,
    FFIConflictLevel, FFIConflictList, FFIDecompResult, FFIEffectiveConfig, FFIErrorClass,
    FFIExplicitConfig, FFIProfile, MARKDOWN_ABI_VERSION, MarkdownConverterHandle, MarkdownOptions,
    MarkdownResult, MarkdownTrustedProxies, NEGOTIATE_REASON_CONVERT,
    NEGOTIATE_REASON_EXPLICIT_REJECT, NEGOTIATE_REASON_LOWER_Q, NEGOTIATE_REASON_MALFORMED,
    NEGOTIATE_REASON_NO_ACCEPT, NEGOTIATE_WILDCARD_ALLOW, NEGOTIATE_WILDCARD_STRICT,
};

#[cfg(feature = "streaming")]
pub use abi::{
    ERROR_BUDGET_EXCEEDED, ERROR_POST_COMMIT, ERROR_STREAMING_FALLBACK, POST_COMMIT_ABORT,
    POST_COMMIT_SAFE_FINISH,
};
pub use exports::{
    markdown_abi_version, markdown_classify_error_code, markdown_convert, markdown_converter_free,
    markdown_converter_new, markdown_decide_base_url, markdown_decomp_result_init,
    markdown_decompress_bounded, markdown_decompress_free, markdown_detect_conflicts,
    markdown_free_conflicts, markdown_negotiate_accept, markdown_result_free,
    markdown_trusted_proxies_free, markdown_trusted_proxies_new, markdown_trusted_proxies_push,
    markdown_options_init, markdown_result_init, markdown_header_plan_init, markdown_decide_eligibility,
    markdown_decide_conditional, markdown_build_header_plan, markdown_header_plan_free,
};

#[cfg(feature = "incremental")]
pub use incremental::{
    IncrementalConverterHandle, markdown_incremental_feed, markdown_incremental_finalize,
    markdown_incremental_free, markdown_incremental_new_with_code,
};

#[cfg(feature = "streaming")]
pub use streaming::{
    StreamingConverterHandle, StreamingOptions, markdown_streaming_abort, markdown_streaming_feed,
    markdown_streaming_finalize, markdown_streaming_new_with_code, markdown_streaming_output_free,
    markdown_streaming_safe_finish,
};
