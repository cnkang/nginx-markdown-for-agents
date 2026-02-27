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
    }
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
    };

    // Test NULL HTML pointer
    ffi_markdown_convert(converter, ptr::null(), 0, &options, &mut result);
    assert_ne!(result.error_code, 0, "Should return error for NULL HTML");
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
    };

    // Perform multiple conversions
    for i in 0..5 {
        let html = format!("<h1>Test {}</h1><p>Content {}</p>", i, i);
        let html_bytes = html.as_bytes();

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

// ============================================================================
// Additional Tests for Task 9.3: FFI Null Pointer Handling
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

    // Trigger error with NULL HTML pointer
    ffi_markdown_convert(converter, ptr::null(), 0, &options, &mut result);

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
    // Note: Zero-length HTML with valid pointer should succeed
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

    ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

    // Empty HTML may succeed or fail depending on parser behavior
    // The important thing is it doesn't crash
    if result.error_code == 0 {
        // Success case - verify result is valid
        assert!(
            !result.markdown.is_null() || result.markdown_len == 0,
            "If successful, markdown should be valid"
        );
    } else {
        // Error case - verify error message is set
        assert!(
            !result.error_message.is_null(),
            "Error message should be set on error"
        );
    }

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
