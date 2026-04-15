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
//! - All FFI functions use `catch_unwind` to prevent panics from crossing the boundary
//! - Panics are converted to error codes and messages
//! - C code will never see Rust unwinding
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

mod abi;
mod convert;
mod exports;
mod memory;
mod options;

#[cfg(feature = "incremental")]
mod incremental;

#[cfg(feature = "streaming")]
mod streaming;

pub use abi::{
    ERROR_ENCODING, ERROR_INTERNAL, ERROR_INVALID_INPUT, ERROR_MEMORY_LIMIT, ERROR_PARSE,
    ERROR_SUCCESS, ERROR_TIMEOUT, MarkdownConverterHandle, MarkdownOptions, MarkdownResult,
};

#[cfg(feature = "streaming")]
pub use abi::{ERROR_BUDGET_EXCEEDED, ERROR_POST_COMMIT, ERROR_STREAMING_FALLBACK};
pub use exports::{
    markdown_convert, markdown_converter_free, markdown_converter_new, markdown_result_free,
};

#[cfg(feature = "incremental")]
pub use incremental::{
    IncrementalConverterHandle, markdown_incremental_feed, markdown_incremental_finalize,
    markdown_incremental_free, markdown_incremental_new,
};

#[cfg(feature = "streaming")]
pub use streaming::{
    StreamingConverterHandle, markdown_streaming_abort, markdown_streaming_feed,
    markdown_streaming_finalize, markdown_streaming_free, markdown_streaming_new,
    markdown_streaming_output_free,
};
