//! FFI memory management helpers for result struct lifecycle.
//!
//! This module provides internal helpers for managing the memory of
//! [`MarkdownResult`] fields across the FFI boundary. All pointer fields in
//! All pointer fields in the result struct are owned by Rust and transferred
//! to C via `as_mut_ptr` + `mem::forget` (see Rule 53); they must be freed
//! by calling `markdown_result_free()` from C.
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

use super::abi::{ConversionOutput, MarkdownResult, ERROR_SUCCESS};

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
    let mut error_bytes = error_message.into_bytes().into_boxed_slice();
    result.error_code = error_code;
    result.error_len = error_bytes.len();
    let p = error_bytes.as_mut_ptr();
    std::mem::forget(error_bytes);
    result.error_message = p;
}

/// Populate a result struct with successful conversion output buffers.
pub(crate) fn set_success_result(result: &mut MarkdownResult, output: ConversionOutput) {
    let mut markdown_buf = output.markdown;
    result.markdown_len = markdown_buf.len();
    let p = markdown_buf.as_mut_ptr();
    std::mem::forget(markdown_buf);
    result.markdown = p;
    result.token_estimate = output.token_estimate;
    result.error_code = ERROR_SUCCESS;
    result.error_message = ptr::null_mut();
    result.error_len = 0;

    if let Some(etag_bytes) = output.etag {
        let mut etag_buf = etag_bytes;
        result.etag_len = etag_buf.len();
        let p = etag_buf.as_mut_ptr();
        std::mem::forget(etag_buf);
        result.etag = p;
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
