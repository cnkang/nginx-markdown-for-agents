#![no_main]

use std::ptr;

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::ffi::{
    MarkdownConverterHandle, MarkdownOptions, MarkdownResult, markdown_convert,
    markdown_converter_free, markdown_converter_new, markdown_result_free,
};

/// Derive a deterministic option bit from the input data.
///
/// Uses a hash-like combination of all input bytes so that even very short
/// inputs produce varied option combinations instead of falling back to
/// defaults.  This significantly improves option-space coverage compared
/// to indexing individual bytes.
fn option_bits(data: &[u8]) -> u8 {
    data.iter().fold(0u8, |acc, &b| acc.wrapping_add(b) ^ b.rotate_left(3))
}

fuzz_target!(|data: &[u8]| {
    let handle: *mut MarkdownConverterHandle = markdown_converter_new();
    if handle.is_null() {
        return;
    }

    let bits = option_bits(data);

    let content_type = b"text/html; charset=UTF-8";
    let base_url = b"https://example.com/fuzz";
    let options = MarkdownOptions {
        flavor: u32::from(bits & 1),
        timeout_ms: u32::from((bits >> 1) & 0x0F) + 1,
        generate_etag: (bits >> 5) & 1,
        estimate_tokens: (bits >> 6) & 1,
        front_matter: (bits >> 7) & 1,
        content_type: if bits & 0x04 == 0 {
            content_type.as_ptr()
        } else {
            ptr::null()
        },
        content_type_len: if bits & 0x04 == 0 {
            content_type.len()
        } else {
            0
        },
        base_url: if bits & 0x08 == 0 {
            base_url.as_ptr()
        } else {
            ptr::null()
        },
        base_url_len: if bits & 0x08 == 0 {
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
