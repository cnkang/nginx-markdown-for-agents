use std::ptr;

use super::abi::{ConversionOutput, ERROR_SUCCESS, MarkdownResult};

pub(crate) fn reset_result(result: &mut MarkdownResult) {
    result.markdown = ptr::null_mut();
    result.markdown_len = 0;
    result.etag = ptr::null_mut();
    result.etag_len = 0;
    result.token_estimate = 0;
    result.error_code = ERROR_SUCCESS;
    result.error_message = ptr::null_mut();
    result.error_len = 0;
}

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
