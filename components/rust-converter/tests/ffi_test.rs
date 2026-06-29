//! FFI integration tests
//!
//! These tests verify that the FFI functions work correctly and handle
//! memory management properly.

use nginx_markdown_converter::ffi::*;
use proptest::prelude::*;
use std::ptr;
use std::slice;

fn ffi_markdown_convert(
    handle: *mut MarkdownConverterHandle,
    html: *const u8,
    html_len: usize,
    options: *const MarkdownOptions,
    result: *mut MarkdownResult,
) {
    unsafe {
        nginx_markdown_converter::ffi::markdown_convert(handle, html, html_len, options, result)
    }
}

fn ffi_markdown_result_free(result: *mut MarkdownResult) {
    unsafe { nginx_markdown_converter::ffi::markdown_result_free(result) }
}

fn ffi_markdown_converter_free(handle: *mut MarkdownConverterHandle) {
    unsafe { nginx_markdown_converter::ffi::markdown_converter_free(handle) }
}

#[cfg(feature = "incremental")]
fn ffi_markdown_incremental_new(
    options: *const MarkdownOptions,
) -> *mut IncrementalConverterHandle {
    unsafe { nginx_markdown_converter::ffi::markdown_incremental_new(options) }
}

#[cfg(feature = "incremental")]
fn ffi_markdown_incremental_feed(
    handle: *mut IncrementalConverterHandle,
    data: *const u8,
    data_len: usize,
) -> u32 {
    unsafe { nginx_markdown_converter::ffi::markdown_incremental_feed(handle, data, data_len) }
}

#[cfg(feature = "incremental")]
fn ffi_markdown_incremental_finalize(
    handle: *mut IncrementalConverterHandle,
    result: *mut MarkdownResult,
) -> u32 {
    unsafe { nginx_markdown_converter::ffi::markdown_incremental_finalize(handle, result) }
}

#[cfg(feature = "incremental")]
fn ffi_markdown_incremental_free(handle: *mut IncrementalConverterHandle) {
    unsafe { nginx_markdown_converter::ffi::markdown_incremental_free(handle) }
}

fn ffi_test_default_options() -> MarkdownOptions {
    MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
    }
}

fn ffi_test_empty_result() -> MarkdownResult {
    MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    }
}

/// Verify that `markdown_result_free` clears `peak_memory_estimate` to 0.
#[test]
fn test_result_free_clears_peak_memory_estimate() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<h1>Test</h1><p>Content</p>";
    let options = ffi_test_default_options();
    let mut result = ffi_test_empty_result();

    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    assert_eq!(result.error_code, 0, "Conversion should succeed");

    /* peak_memory_estimate is only populated by streaming finalize,
     * so it remains 0 for full-buffer conversions. Verify free clears it. */
    ffi_markdown_result_free(&mut result);

    assert_eq!(
        result.peak_memory_estimate, 0,
        "peak_memory_estimate should be 0 after free"
    );

    ffi_markdown_converter_free(converter);
}

#[test]
fn test_converter_lifecycle() {
    // Create converter
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    // Free converter
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_basic_conversion() {
    // Create converter
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    // Prepare input
    let html = b"<h1>Hello World</h1><p>This is a test.</p>";

    // Prepare options
    let options = MarkdownOptions {
        flavor: 0, // CommonMark
        timeout_ms: 5000,
        generate_etag: 1,
        estimate_tokens: 1,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
    };

    // Perform conversion
    let mut result = MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    // Check result
    assert_eq!(result.error_code, 0, "Conversion should succeed");
    assert!(!result.markdown.is_null(), "Markdown should not be NULL");
    assert!(result.markdown_len > 0, "Markdown length should be > 0");

    // Verify markdown content
    unsafe {
        let markdown_slice = slice::from_raw_parts(result.markdown, result.markdown_len);
        let markdown_str = std::str::from_utf8(markdown_slice).expect("Valid UTF-8");
        assert!(
            markdown_str.contains("# Hello World"),
            "Should contain heading"
        );
        assert!(
            markdown_str.contains("This is a test"),
            "Should contain paragraph"
        );
    }

    // Check ETag was generated
    assert!(!result.etag.is_null(), "ETag should be generated");
    assert!(result.etag_len > 0, "ETag length should be > 0");

    // Check token estimate
    assert!(result.token_estimate > 0, "Token estimate should be > 0");

    // Free result
    ffi_markdown_result_free(&mut result);

    // Verify pointers are NULL after free
    assert!(
        result.markdown.is_null(),
        "Markdown should be NULL after free"
    );
    assert!(result.etag.is_null(), "ETag should be NULL after free");

    // Free converter
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_reuse_result_releases_previous_buffers() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let options = ffi_test_default_options();
    let first_html = b"<h1>First</h1><p>Pass</p>";
    let second_html = b"<h2>Second</h2><p>Pass</p>";

    let mut result = ffi_test_empty_result();

    ffi_markdown_convert(
        converter,
        first_html.as_ptr(),
        first_html.len(),
        &options,
        &mut result,
    );

    assert_eq!(result.error_code, 0, "First conversion should succeed");
    assert!(
        !result.markdown.is_null(),
        "First conversion should allocate markdown"
    );

    let first_markdown_ptr = result.markdown;
    let first_markdown_len = result.markdown_len;

    ffi_markdown_convert(
        converter,
        second_html.as_ptr(),
        second_html.len(),
        &options,
        &mut result,
    );

    assert_eq!(result.error_code, 0, "Second conversion should succeed");
    assert!(
        !result.markdown.is_null(),
        "Second conversion should allocate markdown"
    );
    assert!(
        result.markdown_len > 0,
        "Second conversion should return markdown"
    );
    assert!(
        result.markdown != first_markdown_ptr || result.markdown_len != first_markdown_len,
        "Reused result should be refreshed with the latest output"
    );

    let markdown = unsafe {
        let bytes = slice::from_raw_parts(result.markdown, result.markdown_len);
        std::str::from_utf8(bytes)
            .expect("markdown must be valid utf-8")
            .to_string()
    };
    assert!(
        markdown.contains("## Second"),
        "Second conversion output should be present"
    );

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

#[cfg(feature = "incremental")]
#[test]
fn test_incremental_conversion_matches_full_buffer_output() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<h1>Hello World</h1><p>This is a test.</p>";
    let options = ffi_test_default_options();

    let incremental = ffi_markdown_incremental_new(&options);
    assert!(
        !incremental.is_null(),
        "Incremental converter should be created"
    );

    let feed_rc = ffi_markdown_incremental_feed(incremental, html.as_ptr(), html.len());
    assert_eq!(
        feed_rc, ERROR_SUCCESS,
        "Feeding buffered HTML should succeed"
    );

    let mut incremental_result = ffi_test_empty_result();
    let finalize_rc = ffi_markdown_incremental_finalize(incremental, &mut incremental_result);
    assert_eq!(finalize_rc, ERROR_SUCCESS, "Finalize should succeed");
    assert_eq!(
        incremental_result.error_code, ERROR_SUCCESS,
        "Incremental result should report success"
    );

    let mut full_result = ffi_test_empty_result();
    ffi_markdown_convert(
        converter,
        html.as_ptr(),
        html.len(),
        &options,
        &mut full_result,
    );
    assert_eq!(
        full_result.error_code, ERROR_SUCCESS,
        "Full-buffer conversion should succeed"
    );

    let incremental_markdown = unsafe {
        slice::from_raw_parts(incremental_result.markdown, incremental_result.markdown_len)
    };
    let full_markdown =
        unsafe { slice::from_raw_parts(full_result.markdown, full_result.markdown_len) };

    assert_eq!(
        incremental_markdown, full_markdown,
        "Incremental finalize output should match full-buffer output"
    );

    ffi_markdown_result_free(&mut incremental_result);
    ffi_markdown_result_free(&mut full_result);
    ffi_markdown_converter_free(converter);
}

#[cfg(feature = "incremental")]
#[test]
fn test_incremental_feed_rejects_null_data_with_nonzero_length() {
    let options = ffi_test_default_options();
    let incremental = ffi_markdown_incremental_new(&options);
    assert!(
        !incremental.is_null(),
        "Incremental converter should be created"
    );

    let feed_rc = ffi_markdown_incremental_feed(incremental, ptr::null(), 1);
    assert_eq!(
        feed_rc, ERROR_INVALID_INPUT,
        "NULL chunk pointer with non-zero length should be rejected"
    );

    ffi_markdown_incremental_free(incremental);
}

#[cfg(feature = "incremental")]
#[test]
fn test_incremental_finalize_reports_null_handle() {
    let mut result = ffi_test_empty_result();

    let finalize_rc = ffi_markdown_incremental_finalize(ptr::null_mut(), &mut result);

    assert_eq!(
        finalize_rc, ERROR_INVALID_INPUT,
        "NULL incremental handle should be rejected"
    );
    assert_eq!(
        result.error_code, ERROR_INVALID_INPUT,
        "Result should record the invalid-input error"
    );
    assert!(
        !result.error_message.is_null(),
        "Error message should be populated for NULL handle"
    );

    ffi_markdown_result_free(&mut result);
}

proptest! {
    /// Property 25: Token Estimate Header (FFI token_estimate field source)
    /// Validates: FR-15.1, FR-15.2
    #[test]
    fn prop_token_estimate_matches_markdown_output_formula(
        heading in "[A-Za-z0-9 ]{1,24}",
        paragraph in "[A-Za-z0-9 ]{1,64}",
        estimate_enabled in any::<bool>(),
    ) {
        let converter = markdown_converter_new();
        prop_assert!(!converter.is_null(), "Converter handle should be created");

        let html = format!("<h1>{}</h1><p>{}</p>", heading, paragraph);

        let mut options = ffi_test_default_options();
        options.estimate_tokens = u8::from(estimate_enabled);

        let mut result = ffi_test_empty_result();
        ffi_markdown_convert(
            converter,
            html.as_ptr(),
            html.len(),
            &options,
            &mut result,
        );

        prop_assert_eq!(result.error_code, 0, "Conversion should succeed in property test");
        prop_assert!(!result.markdown.is_null(), "Markdown should be returned on success");

        let markdown = unsafe {
            let markdown_slice = slice::from_raw_parts(result.markdown, result.markdown_len);
            std::str::from_utf8(markdown_slice)
                .expect("FFI markdown must be valid UTF-8")
                .to_string()
        };

        let expected = (markdown.chars().count() as f32 / 4.0).ceil() as u32;
        if estimate_enabled {
            prop_assert_eq!(
                result.token_estimate,
                expected,
                "Enabled token estimation should match character-count/4 heuristic"
            );
        } else {
            prop_assert_eq!(result.token_estimate, 0, "Disabled token estimation should return 0");
        }

        ffi_markdown_result_free(&mut result);
        ffi_markdown_converter_free(converter);
    }
}

#[test]
fn test_null_pointer_handling() {
    // Create converter
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<p>Test</p>";
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    // Test NULL converter handle
    ffi_markdown_convert(
        ptr::null_mut(),
        html.as_ptr(),
        html.len(),
        &options,
        &mut result,
    );
    assert_ne!(result.error_code, 0, "Should return error for NULL handle");
    assert!(!result.error_message.is_null(), "Should have error message");
    ffi_markdown_result_free(&mut result);

    // Reset result
    result = MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    };

    // Test NULL HTML pointer with zero length (allowed, treated as empty input)
    ffi_markdown_convert(converter, ptr::null(), 0, &options, &mut result);
    assert_eq!(
        result.error_code, 0,
        "NULL HTML with zero length should succeed"
    );
    assert_eq!(
        result.markdown_len, 0,
        "Zero-length input should produce empty output"
    );
    ffi_markdown_result_free(&mut result);

    // Reset result
    result = MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    };

    // Test NULL options pointer
    ffi_markdown_convert(
        converter,
        html.as_ptr(),
        html.len(),
        ptr::null(),
        &mut result,
    );
    assert_ne!(result.error_code, 0, "Should return error for NULL options");
    assert!(!result.error_message.is_null(), "Should have error message");
    ffi_markdown_result_free(&mut result);

    // Free converter
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_multiple_conversions() {
    // Create converter
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
    };

    // Perform multiple conversions
    for i in 0..5 {
        let html = format!("<h1>Test {}</h1><p>Content {}</p>", i, i);
        let html_bytes = html.as_bytes();

        let mut result = ffi_test_empty_result();

        ffi_markdown_convert(
            converter,
            html_bytes.as_ptr(),
            html_bytes.len(),
            &options,
            &mut result,
        );

        assert_eq!(result.error_code, 0, "Conversion {} should succeed", i);
        assert!(
            !result.markdown.is_null(),
            "Markdown {} should not be NULL",
            i
        );

        // Free result
        ffi_markdown_result_free(&mut result);
    }

    // Free converter
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_idempotent_free() {
    // Create converter
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<p>Test</p>";
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    assert_eq!(result.error_code, 0, "Conversion should succeed");

    // Free result multiple times (should be safe)
    ffi_markdown_result_free(&mut result);
    ffi_markdown_result_free(&mut result);
    ffi_markdown_result_free(&mut result);

    // Free converter
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_content_type_charset_detection() {
    // Create converter
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<p>Test</p>";
    let content_type = b"text/html; charset=UTF-8";

    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: content_type.as_ptr(),
        content_type_len: content_type.len(),
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    assert_eq!(
        result.error_code, 0,
        "Conversion should succeed with Content-Type"
    );
    assert!(!result.markdown.is_null(), "Markdown should not be NULL");

    // Free result
    ffi_markdown_result_free(&mut result);

    // Free converter
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_gfm_flavor() {
    // Create converter
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<table><tr><th>Header</th></tr><tr><td>Cell</td></tr></table>";

    let options = MarkdownOptions {
        flavor: 1, // GFM
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    assert_eq!(result.error_code, 0, "Conversion should succeed with GFM");
    assert!(!result.markdown.is_null(), "Markdown should not be NULL");

    // Verify table was converted
    unsafe {
        let markdown_slice = slice::from_raw_parts(result.markdown, result.markdown_len);
        let markdown_str = std::str::from_utf8(markdown_slice).expect("Valid UTF-8");
        assert!(
            markdown_str.contains("Header"),
            "Should contain table header"
        );
        assert!(markdown_str.contains("Cell"), "Should contain table cell");
    }

    // Free result
    ffi_markdown_result_free(&mut result);

    // Free converter
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_invalid_flavor_rejected() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let html = b"<p>hello</p>";

    let mut options = ffi_test_default_options();
    options.flavor = 99;

    let mut result = MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    assert_eq!(
        result.error_code, ERROR_INVALID_INPUT,
        "flavor=99 should be rejected with ERROR_INVALID_INPUT"
    );
    assert!(
        result.markdown.is_null(),
        "No markdown output on invalid flavor"
    );

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_chars_per_token_affects_estimate() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let html = b"<p>This is a test paragraph with some content.</p>";

    let mut options_20 = ffi_test_default_options();
    options_20.estimate_tokens = 1;
    options_20.chars_per_token_fixed = 20;

    let mut options_40 = ffi_test_default_options();
    options_40.estimate_tokens = 1;
    options_40.chars_per_token_fixed = 40;

    let mut result_20 = MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    };

    let mut result_40 = MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(
        converter,
        html.as_ptr(),
        html.len(),
        &options_20,
        &mut result_20,
    );
    ffi_markdown_convert(
        converter,
        html.as_ptr(),
        html.len(),
        &options_40,
        &mut result_40,
    );

    assert_eq!(
        result_20.error_code, 0,
        "chars_per_token=2.0 should succeed"
    );
    assert_eq!(
        result_40.error_code, 0,
        "chars_per_token=4.0 should succeed"
    );

    assert!(
        result_20.token_estimate > result_40.token_estimate,
        "chars_per_token_fixed=20 (ratio=2.0) should estimate more tokens than \
         chars_per_token_fixed=40 (ratio=4.0), got {} vs {}",
        result_20.token_estimate,
        result_40.token_estimate,
    );

    ffi_markdown_result_free(&mut result_20);
    ffi_markdown_result_free(&mut result_40);
    ffi_markdown_converter_free(converter);
}

// ============================================================================
// Additional Tests for FFI Null Pointer Handling
// ============================================================================

#[test]
fn test_null_result_pointer() {
    // Test that markdown_convert handles NULL result pointer gracefully
    // This should not crash - the function should return early
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<p>Test</p>";
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
    };

    // Call with NULL result pointer - should not crash
    ffi_markdown_convert(
        converter,
        html.as_ptr(),
        html.len(),
        &options,
        ptr::null_mut(),
    );

    // If we get here, the function handled NULL result gracefully
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_free_null_converter() {
    // Test that markdown_converter_free handles NULL gracefully
    ffi_markdown_converter_free(ptr::null_mut());
    // If we get here, the function handled NULL gracefully
}

#[test]
fn test_free_null_result() {
    // Test that markdown_result_free handles NULL gracefully
    ffi_markdown_result_free(ptr::null_mut());
    // If we get here, the function handled NULL gracefully
}

#[test]
fn test_free_empty_result() {
    // Test freeing a result with all NULL pointers
    let mut result = MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    };

    // Should handle empty result gracefully
    ffi_markdown_result_free(&mut result);

    // Verify all fields remain NULL/zero
    assert!(result.markdown.is_null());
    assert_eq!(result.markdown_len, 0);
    assert!(result.etag.is_null());
    assert_eq!(result.etag_len, 0);
}

#[test]
fn test_memory_cleanup_with_all_fields() {
    // Test that all allocated fields are properly freed
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<h1>Test</h1><p>Content</p>";
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 1,   // Enable ETag
        estimate_tokens: 1, // Enable token estimation
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    assert_eq!(result.error_code, 0, "Conversion should succeed");
    assert!(!result.markdown.is_null(), "Markdown should be allocated");
    assert!(!result.etag.is_null(), "ETag should be allocated");
    assert!(result.token_estimate > 0, "Token estimate should be set");

    // Free should clean up all fields
    ffi_markdown_result_free(&mut result);

    // Verify all pointers are NULL and lengths are 0
    assert!(
        result.markdown.is_null(),
        "Markdown should be NULL after free"
    );
    assert_eq!(result.markdown_len, 0, "Markdown length should be 0");
    assert!(result.etag.is_null(), "ETag should be NULL after free");
    assert_eq!(result.etag_len, 0, "ETag length should be 0");
    assert_eq!(result.token_estimate, 0, "Token estimate should be 0");
    assert_eq!(result.error_code, 0, "Error code should be 0");

    ffi_markdown_converter_free(converter);
}

#[test]
fn test_memory_cleanup_error_case() {
    // Test that error message is properly freed
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let _html = b"<p>Test</p>";
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    // Trigger error with NULL HTML pointer and non-zero length.
    ffi_markdown_convert(converter, ptr::null(), 1, &options, &mut result);

    assert_ne!(result.error_code, 0, "Should have error code");
    assert!(
        !result.error_message.is_null(),
        "Error message should be allocated"
    );
    assert!(result.error_len > 0, "Error message length should be > 0");

    // Free should clean up error message
    ffi_markdown_result_free(&mut result);

    // Verify error message is freed
    assert!(
        result.error_message.is_null(),
        "Error message should be NULL after free"
    );
    assert_eq!(result.error_len, 0, "Error length should be 0");

    ffi_markdown_converter_free(converter);
}

#[test]
fn test_panic_catching_invalid_utf8() {
    // Test that panics during conversion are caught and converted to errors
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    // Create invalid UTF-8 sequence
    let invalid_utf8 = [0xFF, 0xFE, 0xFD];

    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(
        converter,
        invalid_utf8.as_ptr(),
        invalid_utf8.len(),
        &options,
        &mut result,
    );

    // Should return error, not panic
    assert_ne!(
        result.error_code, 0,
        "Should return error for invalid UTF-8"
    );
    assert!(!result.error_message.is_null(), "Should have error message");

    // Verify error message is valid UTF-8
    unsafe {
        let error_slice = slice::from_raw_parts(result.error_message, result.error_len);
        let error_str = std::str::from_utf8(error_slice);
        assert!(error_str.is_ok(), "Error message should be valid UTF-8");
    }

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_zero_length_html() {
    // Test conversion with zero-length HTML
    // Zero-length HTML should succeed and produce empty markdown.
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    // Use a valid pointer to empty slice (not NULL)
    let html = b"";
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    assert_eq!(result.error_code, 0, "Zero-length input should succeed");
    assert_eq!(result.markdown_len, 0, "Markdown output should be empty");
    assert!(
        result.markdown.is_null(),
        "Empty markdown output should use the NULL/0 FFI convention"
    );

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_zero_length_html_with_null_pointer() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
    };

    let mut result = ffi_test_empty_result();
    ffi_markdown_convert(converter, ptr::null(), 0, &options, &mut result);

    assert_eq!(
        result.error_code, 0,
        "NULL pointer with zero length should succeed"
    );
    assert_eq!(result.markdown_len, 0, "Markdown output should be empty");
    assert!(
        result.markdown.is_null(),
        "Empty markdown output should use the NULL/0 FFI convention"
    );

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_null_content_type_with_zero_length() {
    // Test that NULL content_type with zero length is handled correctly
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let _html = b"<p>Test</p>";
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(), // NULL pointer
        content_type_len: 0,       // Zero length
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    ffi_markdown_convert(
        converter,
        _html.as_ptr(),
        _html.len(),
        &options,
        &mut result,
    );

    assert_eq!(
        result.error_code, 0,
        "Should succeed with NULL content_type"
    );
    assert!(!result.markdown.is_null(), "Markdown should be generated");

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

#[test]
fn test_error_state_consistency() {
    // Test that error state is consistent across all fields
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let _html = b"<p>Test</p>";
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 1,
        estimate_tokens: 1,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
        peak_memory_estimate: 0,
    };

    // Trigger error with NULL converter
    ffi_markdown_convert(
        ptr::null_mut(),
        _html.as_ptr(),
        _html.len(),
        &options,
        &mut result,
    );

    // Verify error state consistency
    assert_ne!(result.error_code, 0, "Should have error code");
    assert!(
        result.markdown.is_null(),
        "Markdown should be NULL on error"
    );
    assert_eq!(
        result.markdown_len, 0,
        "Markdown length should be 0 on error"
    );
    assert!(result.etag.is_null(), "ETag should be NULL on error");
    assert_eq!(result.etag_len, 0, "ETag length should be 0 on error");
    assert_eq!(
        result.token_estimate, 0,
        "Token estimate should be 0 on error"
    );
    assert!(
        !result.error_message.is_null(),
        "Error message should be set"
    );
    assert!(result.error_len > 0, "Error length should be > 0");

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

proptest! {
    /// Property 28: Crash Resistance
    /// Validates: NFR-01.1, NFR-01.4
    #[test]
    fn prop_random_bytes_do_not_crash_ffi_conversion(input in proptest::collection::vec(any::<u8>(), 0..128)) {
        let converter = markdown_converter_new();
        prop_assert!(!converter.is_null());

        let options = ffi_test_default_options();
        let mut result = ffi_test_empty_result();

        ffi_markdown_convert(
            converter,
            input.as_ptr(),
            input.len(),
            &options,
            &mut result,
        );

        if result.error_code == 0 {
            prop_assert!(result.error_message.is_null());
            prop_assert!(!result.markdown.is_null() || result.markdown_len == 0);
        } else {
            prop_assert!(result.markdown.is_null());
            prop_assert_eq!(result.markdown_len, 0);
            prop_assert!(result.etag.is_null());
            prop_assert_eq!(result.etag_len, 0);
            prop_assert_eq!(result.token_estimate, 0);
            prop_assert!(!result.error_message.is_null());
            prop_assert!(result.error_len > 0);
        }

        ffi_markdown_result_free(&mut result);
        ffi_markdown_converter_free(converter);
    }

    /// Property 29: Graceful Error Recovery
    /// Validates: NFR-01.2, NFR-01.5
    #[test]
    fn prop_converter_recovers_after_error(
        heading in "[A-Za-z0-9 ]{1,24}",
        paragraph in "[A-Za-z0-9 ]{1,64}",
    ) {
        let converter = markdown_converter_new();
        prop_assert!(!converter.is_null());

        let options = ffi_test_default_options();
        let invalid = [0xFF, 0xFE, 0xFD];

        let mut error_result = ffi_test_empty_result();
        ffi_markdown_convert(
            converter,
            invalid.as_ptr(),
            invalid.len(),
            &options,
            &mut error_result,
        );
        prop_assert_ne!(error_result.error_code, 0, "Invalid UTF-8 should fail");
        prop_assert!(!error_result.error_message.is_null());
        ffi_markdown_result_free(&mut error_result);

        let html = format!("<h1>{}</h1><p>{}</p>", heading, paragraph);
        let mut success_result = ffi_test_empty_result();
        ffi_markdown_convert(
            converter,
            html.as_ptr(),
            html.len(),
            &options,
            &mut success_result,
        );

        prop_assert_eq!(success_result.error_code, 0, "Converter should recover for next request");
        prop_assert!(!success_result.markdown.is_null());
        prop_assert!(success_result.markdown_len > 0);

        let markdown = unsafe {
            let bytes = slice::from_raw_parts(success_result.markdown, success_result.markdown_len);
            std::str::from_utf8(bytes).expect("markdown must be valid utf-8").to_string()
        };
        prop_assert!(markdown.contains('#'));

        ffi_markdown_result_free(&mut success_result);
        ffi_markdown_converter_free(converter);
    }
}

/// Regression (TEST-2): the parser memory budget must be enforced at the
/// conversion entry point, not merely classified at the error-code level.
///
/// Drives `markdown_convert` with a tiny `parser_memory_budget` against an
/// input that exceeds it, asserting the conversion fails with
/// `ERROR_PARSE_BUDGET_EXCEEDED`.
#[test]
fn test_parser_memory_budget_enforced() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<h1>Hello World</h1><p>This input is larger than the budget.</p>";
    let mut options = ffi_test_default_options();
    /* Budget far smaller than the input length forces early rejection. */
    options.parser_memory_budget = 8;

    let mut result = ffi_test_empty_result();
    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    assert_eq!(
        result.error_code, ERROR_PARSE_BUDGET_EXCEEDED,
        "oversized input must be rejected with ERROR_PARSE_BUDGET_EXCEEDED"
    );

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

/// Verify that an input within the parser memory budget is NOT rejected,
/// so the budget check does not over-trigger.
#[test]
fn test_parser_memory_budget_allows_small_input() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let html = b"<p>hi</p>";
    let mut options = ffi_test_default_options();
    /* Budget comfortably larger than the input. */
    options.parser_memory_budget = 4096;

    let mut result = ffi_test_empty_result();
    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    assert_eq!(
        result.error_code, ERROR_SUCCESS,
        "input within budget must convert successfully"
    );

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

/// Regression (TEST-2): a parse that overruns `parse_timeout` must fail with
/// `ERROR_PARSE_TIMEOUT` rather than returning partial output.
///
/// Uses a 10 ms deadline against a 500 KiB document to make timeout
/// detection reliable on CI hardware while avoiding the 1 ms boundary
/// flakiness.  To hedge against unusually fast machines, the timeout
/// assertion is only made when the call genuinely took longer than the
/// deadline; otherwise the conversion legitimately completed in time
/// and must report success.
#[test]
fn test_parse_timeout_enforced_when_overrun() {
    use std::time::{Duration, Instant};

    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Converter should not be NULL");

    let mut large_html = Vec::with_capacity(512 * 1024);
    large_html.extend_from_slice(b"<html><body>");
    for i in 0..8000 {
        large_html.extend_from_slice(
            format!("<p>paragraph number {i} with enough text to ensure parsing cost</p>")
                .as_bytes(),
        );
    }
    large_html.extend_from_slice(b"</body></html>");

    let mut options = ffi_test_default_options();
    options.parse_timeout_ms = 10;
    options.parser_memory_budget = 0;

    let mut result = ffi_test_empty_result();
    let start = Instant::now();
    ffi_markdown_convert(
        converter,
        large_html.as_ptr(),
        large_html.len(),
        &options,
        &mut result,
    );
    let elapsed = start.elapsed();

    if elapsed > Duration::from_millis(10) {
        assert_eq!(
            result.error_code, ERROR_PARSE_TIMEOUT,
            "conversion exceeding parse_timeout must fail with ERROR_PARSE_TIMEOUT"
        );
    } else {
        assert_eq!(
            result.error_code, ERROR_SUCCESS,
            "conversion completed within the deadline must report success"
        );
    }

    ffi_markdown_result_free(&mut result);
    ffi_markdown_converter_free(converter);
}

// ── Decision/negotiation/conditional FFI boundary tests (FFI-3) ──────────
//
// These exercise the unsafe extern "C" wrappers directly — NULL result
// handling, malformed UTF-8, optional_str empty-vs-null, and crafted inputs —
// which the pure-Rust unit tests on the underlying modules do not cover.

#[test]
fn test_negotiate_accept_null_result_is_safe() {
    /* A NULL result pointer must be a no-op, not a crash. */
    let header = b"text/markdown";
    unsafe {
        markdown_negotiate_accept(
            header.as_ptr(),
            header.len(),
            NEGOTIATE_WILDCARD_STRICT,
            std::ptr::null_mut(),
        );
    }
}

#[test]
fn test_negotiate_accept_markdown_preferred() {
    let header = b"text/markdown";
    let mut result = FFIAcceptResult {
        should_convert: 9,
        reason: 9,
    };
    unsafe {
        markdown_negotiate_accept(
            header.as_ptr(),
            header.len(),
            NEGOTIATE_WILDCARD_STRICT,
            &mut result,
        );
    }
    assert_eq!(result.should_convert, 1);
    assert_eq!(result.reason, NEGOTIATE_REASON_CONVERT);
}

#[test]
fn test_negotiate_accept_null_header_is_no_accept() {
    /* NULL header with zero length is the documented empty-Accept case. */
    let mut result = FFIAcceptResult {
        should_convert: 9,
        reason: 9,
    };
    unsafe {
        markdown_negotiate_accept(std::ptr::null(), 0, NEGOTIATE_WILDCARD_STRICT, &mut result);
    }
    assert_eq!(result.should_convert, 0);
    assert_eq!(result.reason, NEGOTIATE_REASON_NO_ACCEPT);
}

#[test]
fn test_negotiate_accept_malformed_utf8() {
    /* Invalid UTF-8 in the Accept header must classify as malformed, not
     * panic or read past the slice. */
    let header = [0xff, 0xfe, 0x80];
    let mut result = FFIAcceptResult {
        should_convert: 9,
        reason: 9,
    };
    unsafe {
        markdown_negotiate_accept(
            header.as_ptr(),
            header.len(),
            NEGOTIATE_WILDCARD_STRICT,
            &mut result,
        );
    }
    assert_eq!(result.should_convert, 0);
    assert_eq!(result.reason, NEGOTIATE_REASON_MALFORMED);
}

#[test]
fn test_check_conditional_null_result_is_safe() {
    let inm = b"\"abc\"";
    let etag = b"\"abc\"";
    unsafe {
        markdown_check_conditional(
            inm.as_ptr(),
            inm.len(),
            etag.as_ptr(),
            etag.len(),
            std::ptr::null(),
            0,
            std::ptr::null(),
            0,
            std::ptr::null_mut(),
        );
    }
}

#[test]
fn test_check_conditional_etag_match_not_modified() {
    let inm = b"\"abc\"";
    let etag = b"\"abc\"";
    let mut result = FFIConditionalResult {
        result_code: 9,
        matched_etag_len: 9,
    };
    unsafe {
        markdown_check_conditional(
            inm.as_ptr(),
            inm.len(),
            etag.as_ptr(),
            etag.len(),
            std::ptr::null(),
            0,
            std::ptr::null(),
            0,
            &mut result,
        );
    }
    assert_eq!(result.result_code, 0, "matching ETag must be NotModified");
}

#[test]
fn test_check_conditional_non_ascii_ims_proceeds() {
    /* Regression (FFI-1) at the boundary: a crafted 29-byte non-ASCII
     * If-Modified-Since must not panic; with no If-None-Match and a
     * Last-Modified present it falls through parse_http_date to Proceed. */
    let ims = "😀aaaaaaaaaaaaaaaaaaaaa GMT".as_bytes();
    let lm = b"Sun, 04 Nov 1994 08:49:37 GMT";
    let mut result = FFIConditionalResult {
        result_code: 9,
        matched_etag_len: 9,
    };
    unsafe {
        markdown_check_conditional(
            std::ptr::null(),
            0,
            std::ptr::null(),
            0,
            ims.as_ptr(),
            ims.len(),
            lm.as_ptr(),
            lm.len(),
            &mut result,
        );
    }
    assert_eq!(result.result_code, 1, "unparseable IMS must Proceed");
}

#[test]
fn test_make_decision_null_result_is_safe() {
    unsafe {
        markdown_make_decision(1, 1, 1, 1, 0, 1, 0, 0, std::ptr::null_mut());
    }
}

#[test]
fn test_make_decision_convert_path() {
    /* enabled, eligible, accept prefers markdown, accept present, not
     * conditional-304, decompression ok, no timeout, no budget overrun. */
    let mut result = FFIDecisionResult {
        decision: 9,
        reason_code: 9,
    };
    unsafe {
        markdown_make_decision(1, 1, 1, 1, 0, 1, 0, 0, &mut result);
    }
    assert_eq!(result.decision, 0, "should convert");
    assert_eq!(result.reason_code, 0, "Converted reason code is 0");
}

#[test]
fn test_make_decision_disabled_skips() {
    let mut result = FFIDecisionResult {
        decision: 9,
        reason_code: 9,
    };
    unsafe {
        /* enabled = 0 → skip with Disabled (canonical discriminant 15). */
        markdown_make_decision(0, 1, 1, 1, 0, 1, 0, 0, &mut result);
    }
    assert_eq!(result.decision, 1, "disabled must skip");
    assert_eq!(result.reason_code, 15, "Disabled discriminant is 15");
}

#[cfg(feature = "streaming")]
#[test]
fn test_shared_option_field_semantics_aligned() {
    use nginx_markdown_converter::ffi::{MarkdownOptions, StreamingOptions};

    let md_opts = MarkdownOptions {
        flavor: 1,
        timeout_ms: 5000,
        generate_etag: 1,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 2 * 1024 * 1024,
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 30000,
        parser_memory_budget: 64 * 1024 * 1024,
        flush_threshold: 16384,
    };

    let st_opts = StreamingOptions {
        flavor: 1,
        timeout_ms: 5000,
        streaming_budget: 2 * 1024 * 1024,
        flush_threshold: 16384,
        generate_etag: 1,
        estimate_tokens: 0,
        front_matter: 0,
        prune_noise: 1,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
    };

    assert_eq!(
        md_opts.flavor, st_opts.flavor,
        "flavor must match across MarkdownOptions and StreamingOptions"
    );
    assert_eq!(
        md_opts.timeout_ms, st_opts.timeout_ms,
        "timeout_ms must match"
    );
    assert_eq!(
        md_opts.streaming_budget, st_opts.streaming_budget,
        "streaming_budget must match"
    );
    assert_eq!(
        md_opts.flush_threshold, st_opts.flush_threshold,
        "flush_threshold must match"
    );
    assert_eq!(
        md_opts.generate_etag, st_opts.generate_etag,
        "generate_etag must match"
    );
    assert_eq!(
        md_opts.estimate_tokens, st_opts.estimate_tokens,
        "estimate_tokens must match"
    );
    assert_eq!(
        md_opts.front_matter, st_opts.front_matter,
        "front_matter must match"
    );
    assert_eq!(
        md_opts.llm_provider, st_opts.llm_provider,
        "llm_provider must match"
    );
    assert_eq!(
        md_opts.chars_per_token_fixed, st_opts.chars_per_token_fixed,
        "chars_per_token_fixed must match"
    );

    let md_prune_on = md_opts.prune_noise != 0;
    let st_prune_on = st_opts.prune_noise != 0;
    assert_eq!(
        md_prune_on, st_prune_on,
        "prune_noise boolean semantics must match (MarkdownOptions u32 vs StreamingOptions u8)"
    );
}
