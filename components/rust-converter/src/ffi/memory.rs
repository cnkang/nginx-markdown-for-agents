//! FFI memory management helpers for result struct lifecycle.
//!
//! This module provides internal helpers for managing the memory of
//! [`MarkdownResult`] fields across the FFI boundary. All pointer fields in
//! the result struct are owned by Rust and allocated via `Box::into_raw`;
//! they must be freed by calling `markdown_result_free()` from C.
//!
//! # Functions
//!
//! | Function | Purpose |
//! |----------|---------|
//! | `reset_result` | Zero-initialize all fields of a `MarkdownResult` |
//! | `set_error_result` | Populate error code and message into a result |
//! | `set_success_result` | Populate markdown/etag/token fields into a result |
//! | `free_buffer` | Release one heap-allocated buffer back to Rust |
//!
//! # Safety
//!
//! - `free_buffer` must only be called on pointers that were originally
//!   produced by `Box::into_raw(Box<[u8]>)`. Calling it on any other
//!   pointer is undefined behavior.
//! - After `free_buffer` is called, the pointer is set to NULL and the
//!   length to 0, preventing double-free.

use std::ptr;

use super::abi::{ConversionOutput, ERROR_SUCCESS, MarkdownResult};

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
    let error_bytes = error_message.into_bytes().into_boxed_slice();
    result.error_code = error_code;
    result.error_len = error_bytes.len();
    result.error_message = Box::into_raw(error_bytes) as *mut u8;
}

/// Populate a result struct with successful conversion output buffers.
pub(crate) fn set_success_result(result: &mut MarkdownResult, output: ConversionOutput) {
    result.markdown_len = output.markdown.len();
    result.markdown = Box::into_raw(output.markdown) as *mut u8;
    result.token_estimate = output.token_estimate;
    result.error_code = ERROR_SUCCESS;
    result.error_message = ptr::null_mut();
    result.error_len = 0;

    if let Some(etag_bytes) = output.etag {
        result.etag_len = etag_bytes.len();
        result.etag = Box::into_raw(etag_bytes) as *mut u8;
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
    // SAFETY: `raw` was allocated by `Box<[u8]>` via `Box::into_raw`.
    let _ = unsafe { Box::from_raw(raw) };
    *ptr_field = ptr::null_mut();
    *len_field = 0;
}
