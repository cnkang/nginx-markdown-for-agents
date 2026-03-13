#![no_main]

use std::ptr;

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::ffi::{
    MarkdownConverterHandle, MarkdownOptions, MarkdownResult, markdown_convert,
    markdown_converter_free, markdown_converter_new, markdown_result_free,
};

fuzz_target!(|data: &[u8]| {
    let handle: *mut MarkdownConverterHandle = markdown_converter_new();
    if handle.is_null() {
        return;
    }

    let content_type = b"text/html; charset=UTF-8";
    let base_url = b"https://example.com/fuzz";
    let options = MarkdownOptions {
        flavor: u32::from(data.first().copied().unwrap_or_default() & 1),
        timeout_ms: u32::from(data.get(1).copied().unwrap_or(1)) + 1,
        generate_etag: data.get(2).copied().unwrap_or_default() & 1,
        estimate_tokens: data.get(3).copied().unwrap_or_default() & 1,
        front_matter: data.get(4).copied().unwrap_or_default() & 1,
        content_type: if data.get(5).copied().unwrap_or_default() & 1 == 0 {
            content_type.as_ptr()
        } else {
            ptr::null()
        },
        content_type_len: if data.get(5).copied().unwrap_or_default() & 1 == 0 {
            content_type.len()
        } else {
            0
        },
        base_url: if data.get(6).copied().unwrap_or_default() & 1 == 0 {
            base_url.as_ptr()
        } else {
            ptr::null()
        },
        base_url_len: if data.get(6).copied().unwrap_or_default() & 1 == 0 {
            base_url.len()
        } else {
            0
        },
    };

    let mut result = MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
    };

    unsafe {
        markdown_convert(handle, data.as_ptr(), data.len(), &options, &mut result);
        markdown_result_free(&mut result);
        markdown_converter_free(handle);
    }
});
