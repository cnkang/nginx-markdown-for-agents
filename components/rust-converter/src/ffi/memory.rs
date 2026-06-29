//! FFI memory management helpers for result struct lifecycle.
//!
//! This module provides internal helpers for managing the memory of
//! [`MarkdownResult`] fields across the FFI boundary.
//!
//! All heap-allocated buffers owned by Rust that are transferred to C use
//! the `as_mut_ptr` + `mem::forget` pattern (see Rule 53).  The thin data
//! pointer is passed to C together with the buffer length; C must keep
//! these paired and return them to `free_buffer` (or the dedicated FFI free
//! function) for correct deallocation.
//!
//! # Functions
//!
//! | Function | Purpose |
//! |----------|---------|
//! | `leak_boxed_slice_to_raw` | Leak a `Box<[u8]>` into `(*mut u8, usize)` for C ABI export |
//! | `reset_result` | Zero-initialize all fields of a `MarkdownResult` |
//! | `set_error_result` | Populate error code and message into a result |
//! | `set_success_result` | Populate markdown/etag/token fields into a result |
//! | `free_buffer` | Release one heap-allocated buffer back to Rust |
//!
//! # Safety
//!
//! - Buffers must be freed via `free_buffer` (or the dedicated FFI free
//!   function) using the exact `(pointer, len)` pair returned by
//!   `leak_boxed_slice_to_raw`.  Using a different deallocation strategy
//!   (e.g. libc `free`, `ngx_pfree`) is undefined behavior.
//! - Each `(pointer, len)` pair must be freed exactly once.
//! - After `free_buffer` is called, the pointer is set to NULL and the
//!   length to 0, preventing double-free.

use std::ptr;

use super::abi::{ConversionOutput, ERROR_SUCCESS, MarkdownResult};

/// Leak a [`Box<[u8]>`] into a `(thin pointer, length)` pair for C ABI
/// export, avoiding the `Box::into_raw(Box<[u8]>) as *mut u8` fat-pointer
/// truncation pattern (Rule 53).
///
/// The returned `(*mut u8, usize)` pair must be kept together and eventually
/// freed by reconstructing the `Box<[u8]>` via [`ptr::slice_from_raw_parts_mut`]
/// + [`Box::from_raw`] (see [`free_buffer`]).
pub(crate) fn leak_boxed_slice_to_raw(mut buf: Box<[u8]>) -> (*mut u8, usize) {
    let len = buf.len();
    if len == 0 {
        return (ptr::null_mut(), 0);
    }
    let ptr = buf.as_mut_ptr();
    std::mem::forget(buf);
    (ptr, len)
}

/// Reset an output struct to a clean success-initialized state.
pub(crate) fn reset_result(result: &mut MarkdownResult) {
    result.markdown = ptr::null_mut();
    result.markdown_len = 0;
    result.etag = ptr::null_mut();
    result.etag_len = 0;
    result.token_estimate = 0;
    result.error_code = ERROR_SUCCESS;
    result.error_message = ptr::null_mut();
    result.error_len = 0;
    result.peak_memory_estimate = 0;
}

/// Populate a result struct with an owned error payload.
pub(crate) fn set_error_result(
    result: &mut MarkdownResult,
    error_code: u32,
    error_message: String,
) {
    let (ptr, len) = leak_boxed_slice_to_raw(error_message.into_bytes().into_boxed_slice());
    result.error_code = error_code;
    result.error_len = len;
    result.error_message = ptr;
}

/// Populate a result struct with successful conversion output buffers.
pub(crate) fn set_success_result(result: &mut MarkdownResult, output: ConversionOutput) {
    let (md_ptr, md_len) = leak_boxed_slice_to_raw(output.markdown);
    result.markdown_len = md_len;
    result.markdown = md_ptr;
    result.token_estimate = output.token_estimate;
    result.error_code = ERROR_SUCCESS;
    result.error_message = ptr::null_mut();
    result.error_len = 0;

    if let Some(etag_bytes) = output.etag {
        let (etag_ptr, etag_len) = leak_boxed_slice_to_raw(etag_bytes);
        result.etag_len = etag_len;
        result.etag = etag_ptr;
    } else {
        result.etag = ptr::null_mut();
        result.etag_len = 0;
    }
}

/// Free one heap buffer previously exported through the C ABI.
pub(crate) fn free_buffer(ptr_field: &mut *mut u8, len_field: &mut usize) {
    if (*ptr_field).is_null() {
        return;
    }

    let raw = ptr::slice_from_raw_parts_mut(*ptr_field, *len_field);
    // SAFETY: `raw` is a reconstructed fat-pointer from the thin pointer that
    // was originally obtained via `leak_boxed_slice_to_raw` / `as_mut_ptr`
    // + `mem::forget`, and `len_field` is the original buffer length.
    let _ = unsafe { Box::from_raw(raw) };
    *ptr_field = ptr::null_mut();
    *len_field = 0;
}

#[cfg(test)]
mod tests {
    use super::leak_boxed_slice_to_raw;

    #[test]
    fn leak_boxed_slice_to_raw_returns_null_for_empty_slice() {
        let (ptr, len) = leak_boxed_slice_to_raw(Vec::new().into_boxed_slice());

        assert!(ptr.is_null());
        assert_eq!(len, 0);
    }
}
