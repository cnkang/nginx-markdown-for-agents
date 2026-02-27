//! Feature Independence Tests
//!
//! This test module validates that token estimation and YAML front matter
//! features are truly independent and can be toggled separately.
//!
//! **Validates: Requirements FR-15.6, FR-15.7, FR-15.8**
//!
//! The module should support all four combinations:
//! 1. Both enabled
//! 2. Token estimation only
//! 3. Front matter only
//! 4. Both disabled

use nginx_markdown_converter::ffi::{
    ERROR_SUCCESS, MarkdownOptions, MarkdownResult, markdown_converter_new,
};
use proptest::prelude::*;
use std::ptr;
use std::slice;

fn ffi_markdown_convert(
    handle: *mut nginx_markdown_converter::ffi::MarkdownConverterHandle,
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

fn ffi_markdown_converter_free(
    handle: *mut nginx_markdown_converter::ffi::MarkdownConverterHandle,
) {
    unsafe { nginx_markdown_converter::ffi::markdown_converter_free(handle) }
}

/// Helper function to create test HTML
fn create_test_html() -> Vec<u8> {
    r#"<!DOCTYPE html>
<html>
<head>
    <title>Test Page</title>
    <meta name="description" content="Test description">
    <meta property="og:image" content="https://example.com/image.png">
</head>
<body>
    <h1>Main Heading</h1>
    <p>This is a test paragraph with some content.</p>
    <p>Another paragraph here.</p>
</body>
</html>"#
        .as_bytes()
        .to_vec()
}

fn build_feature_test_html(
    title: &str,
    heading: &str,
    paragraph_a: &str,
    paragraph_b: &str,
) -> Vec<u8> {
    format!(
        r#"<!DOCTYPE html>
<html>
<head>
    <title>{title}</title>
    <meta name="description" content="Generated test description">
    <meta property="og:image" content="https://example.com/image.png">
</head>
<body>
    <h1>{heading}</h1>
    <p>{paragraph_a}</p>
    <p>{paragraph_b}</p>
</body>
</html>"#
    )
    .into_bytes()
}

fn empty_result() -> MarkdownResult {
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

fn convert_with_feature_toggles(
    html: &[u8],
    estimate_tokens: bool,
    front_matter: bool,
    base_url: &[u8],
) -> (String, u32) {
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Failed to create converter");

    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: u8::from(estimate_tokens),
        front_matter: u8::from(front_matter),
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: if front_matter {
            base_url.as_ptr()
        } else {
            ptr::null()
        },
        base_url_len: if front_matter { base_url.len() } else { 0 },
    };

    let mut result = empty_result();

    unsafe {
        ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
        assert_eq!(
            result.error_code, ERROR_SUCCESS,
            "Conversion should succeed"
        );

        let markdown = result_markdown_to_string(&result);
        let token_estimate = result.token_estimate;

        ffi_markdown_result_free(&mut result);
        ffi_markdown_converter_free(converter);

        (markdown, token_estimate)
    }
}

fn markdown_body_without_front_matter(markdown: &str) -> &str {
    if !markdown.starts_with("---\n") {
        return markdown;
    }

    let rest = &markdown[4..];
    if let Some(end_offset) = rest.find("\n---\n") {
        let after_front_matter = &rest[end_offset + 5..];
        return after_front_matter.trim_start_matches('\n');
    }

    markdown
}

/// Helper function to convert result markdown to string
unsafe fn result_markdown_to_string(result: &MarkdownResult) -> String {
    unsafe {
        if result.markdown.is_null() || result.markdown_len == 0 {
            return String::new();
        }
        let slice = slice::from_raw_parts(result.markdown, result.markdown_len);
        String::from_utf8_lossy(slice).to_string()
    }
}

/// Test Case 1: Both token estimation and front matter enabled
#[test]
fn test_both_features_enabled() {
    let html = create_test_html();
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Failed to create converter");

    let base_url = "https://example.com/page".as_bytes();
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 1, // Enable token estimation
        front_matter: 1,    // Enable front matter
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: base_url.as_ptr(),
        base_url_len: base_url.len(),
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
        ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

        // Verify conversion succeeded
        assert_eq!(
            result.error_code, ERROR_SUCCESS,
            "Conversion failed with error code: {}",
            result.error_code
        );

        // Verify token estimation is present
        assert!(
            result.token_estimate > 0,
            "Token estimate should be present when enabled"
        );

        // Verify front matter is present
        let markdown = result_markdown_to_string(&result);
        assert!(
            markdown.starts_with("---\n"),
            "Front matter should start with '---'"
        );
        assert!(
            markdown.contains("title:"),
            "Front matter should contain title field"
        );
        assert!(
            markdown.contains("url:"),
            "Front matter should contain url field"
        );

        // Verify content is present after front matter
        assert!(
            markdown.contains("# Main Heading"),
            "Markdown content should be present"
        );

        println!(
            "✓ Both features enabled: token_estimate={}, has_front_matter=true",
            result.token_estimate
        );

        ffi_markdown_result_free(&mut result);
        ffi_markdown_converter_free(converter);
    }
}

/// Test Case 2: Token estimation only (front matter disabled)
#[test]
fn test_token_estimation_only() {
    let html = create_test_html();
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Failed to create converter");

    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 1, // Enable token estimation
        front_matter: 0,    // Disable front matter
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

    unsafe {
        ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

        // Verify conversion succeeded
        assert_eq!(
            result.error_code, ERROR_SUCCESS,
            "Conversion failed with error code: {}",
            result.error_code
        );

        // Verify token estimation is present
        assert!(
            result.token_estimate > 0,
            "Token estimate should be present when enabled"
        );

        // Verify front matter is NOT present
        let markdown = result_markdown_to_string(&result);
        assert!(
            !markdown.starts_with("---\n"),
            "Front matter should NOT be present when disabled"
        );
        assert!(
            !markdown.contains("title:"),
            "Front matter fields should NOT be present when disabled"
        );

        // Verify content is present (without front matter)
        assert!(
            markdown.starts_with("# Main Heading") || markdown.contains("# Main Heading"),
            "Markdown content should be present without front matter"
        );

        println!(
            "✓ Token estimation only: token_estimate={}, has_front_matter=false",
            result.token_estimate
        );

        ffi_markdown_result_free(&mut result);
        ffi_markdown_converter_free(converter);
    }
}

/// Test Case 3: Front matter only (token estimation disabled)
#[test]
fn test_front_matter_only() {
    let html = create_test_html();
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Failed to create converter");

    let base_url = "https://example.com/page".as_bytes();
    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0, // Disable token estimation
        front_matter: 1,    // Enable front matter
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: base_url.as_ptr(),
        base_url_len: base_url.len(),
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
        ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

        // Verify conversion succeeded
        assert_eq!(
            result.error_code, ERROR_SUCCESS,
            "Conversion failed with error code: {}",
            result.error_code
        );

        // Verify token estimation is NOT present
        assert_eq!(
            result.token_estimate, 0,
            "Token estimate should be 0 when disabled"
        );

        // Verify front matter IS present
        let markdown = result_markdown_to_string(&result);
        assert!(
            markdown.starts_with("---\n"),
            "Front matter should start with '---'"
        );
        assert!(
            markdown.contains("title:"),
            "Front matter should contain title field"
        );
        assert!(
            markdown.contains("url:"),
            "Front matter should contain url field"
        );

        // Verify content is present after front matter
        assert!(
            markdown.contains("# Main Heading"),
            "Markdown content should be present"
        );

        println!("✓ Front matter only: token_estimate=0, has_front_matter=true");

        ffi_markdown_result_free(&mut result);
        ffi_markdown_converter_free(converter);
    }
}

/// Test Case 4: Both features disabled
#[test]
fn test_both_features_disabled() {
    let html = create_test_html();
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Failed to create converter");

    let options = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0, // Disable token estimation
        front_matter: 0,    // Disable front matter
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

    unsafe {
        ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

        // Verify conversion succeeded
        assert_eq!(
            result.error_code, ERROR_SUCCESS,
            "Conversion failed with error code: {}",
            result.error_code
        );

        // Verify token estimation is NOT present
        assert_eq!(
            result.token_estimate, 0,
            "Token estimate should be 0 when disabled"
        );

        // Verify front matter is NOT present
        let markdown = result_markdown_to_string(&result);
        assert!(
            !markdown.starts_with("---\n"),
            "Front matter should NOT be present when disabled"
        );
        assert!(
            !markdown.contains("title:"),
            "Front matter fields should NOT be present when disabled"
        );

        // Verify content is present (without front matter)
        assert!(
            markdown.starts_with("# Main Heading") || markdown.contains("# Main Heading"),
            "Markdown content should be present without front matter"
        );

        println!("✓ Both features disabled: token_estimate=0, has_front_matter=false");

        ffi_markdown_result_free(&mut result);
        ffi_markdown_converter_free(converter);
    }
}

/// Test Case 5: Verify independence - enabling one doesn't affect the other
#[test]
fn test_feature_independence_comprehensive() {
    let html = create_test_html();
    let base_url = "https://example.com/page".as_bytes();

    // Test all four combinations and verify they produce different results
    let test_cases = [
        (0, 0, "both_disabled"),
        (1, 0, "token_only"),
        (0, 1, "front_matter_only"),
        (1, 1, "both_enabled"),
    ];

    for (estimate_tokens, front_matter, label) in test_cases.iter() {
        let converter = markdown_converter_new();
        assert!(!converter.is_null(), "Failed to create converter");

        let options = MarkdownOptions {
            flavor: 0,
            timeout_ms: 5000,
            generate_etag: 0,
            estimate_tokens: *estimate_tokens,
            front_matter: *front_matter,
            content_type: ptr::null(),
            content_type_len: 0,
            base_url: if *front_matter == 1 {
                base_url.as_ptr()
            } else {
                ptr::null()
            },
            base_url_len: if *front_matter == 1 {
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
            ffi_markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);

            assert_eq!(
                result.error_code, ERROR_SUCCESS,
                "Conversion failed for {}: error code {}",
                label, result.error_code
            );

            let markdown = result_markdown_to_string(&result);

            // Verify token estimation behavior
            if *estimate_tokens == 1 {
                assert!(
                    result.token_estimate > 0,
                    "{}: Token estimate should be present",
                    label
                );
            } else {
                assert_eq!(
                    result.token_estimate, 0,
                    "{}: Token estimate should be 0",
                    label
                );
            }

            // Verify front matter behavior
            if *front_matter == 1 {
                assert!(
                    markdown.starts_with("---\n"),
                    "{}: Front matter should be present",
                    label
                );
                assert!(
                    markdown.contains("title:"),
                    "{}: Front matter should contain title",
                    label
                );
            } else {
                assert!(
                    !markdown.starts_with("---\n"),
                    "{}: Front matter should NOT be present",
                    label
                );
            }

            // Verify content is always present
            assert!(
                markdown.contains("Main Heading"),
                "{}: Content should always be present",
                label
            );

            println!(
                "✓ {}: token_estimate={}, has_front_matter={}",
                label,
                result.token_estimate,
                markdown.starts_with("---\n")
            );

            ffi_markdown_result_free(&mut result);
            ffi_markdown_converter_free(converter);
        }
    }
}

/// Test Case 6: Verify no hidden dependencies between features
#[test]
fn test_no_hidden_dependencies() {
    let html = create_test_html();
    let converter = markdown_converter_new();
    assert!(!converter.is_null(), "Failed to create converter");

    // Test 1: Enable token estimation, verify it doesn't force front matter
    let options1 = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 1,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
    };

    let mut result1 = MarkdownResult {
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
        ffi_markdown_convert(
            converter,
            html.as_ptr(),
            html.len(),
            &options1,
            &mut result1,
        );

        assert_eq!(result1.error_code, ERROR_SUCCESS);
        assert!(result1.token_estimate > 0, "Token estimation should work");
        let markdown1 = result_markdown_to_string(&result1);
        assert!(
            !markdown1.starts_with("---\n"),
            "Token estimation should not enable front matter"
        );

        ffi_markdown_result_free(&mut result1);
    }

    // Test 2: Enable front matter, verify it doesn't force token estimation
    let base_url = "https://example.com/page".as_bytes();
    let options2 = MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 1,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: base_url.as_ptr(),
        base_url_len: base_url.len(),
    };

    let mut result2 = MarkdownResult {
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
        ffi_markdown_convert(
            converter,
            html.as_ptr(),
            html.len(),
            &options2,
            &mut result2,
        );

        assert_eq!(result2.error_code, ERROR_SUCCESS);
        assert_eq!(
            result2.token_estimate, 0,
            "Front matter should not enable token estimation"
        );
        let markdown2 = result_markdown_to_string(&result2);
        assert!(
            markdown2.starts_with("---\n"),
            "Front matter should work independently"
        );

        ffi_markdown_result_free(&mut result2);
        ffi_markdown_converter_free(converter);
    }

    println!("✓ No hidden dependencies: features are truly independent");
}

proptest! {
    /// Property 29: Feature Toggle Independence
    /// Validates: FR-15.6, FR-15.7, FR-15.8
    #[test]
    fn prop_feature_toggle_independence(
        title in "[A-Za-z0-9 ]{1,24}",
        heading in "[A-Za-z0-9 ]{1,24}",
        paragraph_a in "[A-Za-z0-9 ]{1,80}",
        paragraph_b in "[A-Za-z0-9 ]{1,80}",
    ) {
        let html = build_feature_test_html(&title, &heading, &paragraph_a, &paragraph_b);
        let base_url = b"https://example.com/generated";

        let (markdown_disabled_disabled, tokens_disabled_disabled) =
            convert_with_feature_toggles(&html, false, false, base_url);
        let (markdown_enabled_disabled, tokens_enabled_disabled) =
            convert_with_feature_toggles(&html, true, false, base_url);
        let (markdown_disabled_enabled, tokens_disabled_enabled) =
            convert_with_feature_toggles(&html, false, true, base_url);
        let (markdown_enabled_enabled, tokens_enabled_enabled) =
            convert_with_feature_toggles(&html, true, true, base_url);

        prop_assert_eq!(tokens_disabled_disabled, 0);
        prop_assert_eq!(tokens_disabled_enabled, 0);
        prop_assert!(tokens_enabled_disabled > 0);
        prop_assert!(tokens_enabled_enabled > 0);

        prop_assert_eq!(
            markdown_disabled_disabled.as_str(), markdown_enabled_disabled.as_str(),
            "Token estimation toggle must not change markdown output when front matter is disabled"
        );
        prop_assert_eq!(
            markdown_disabled_enabled.as_str(), markdown_enabled_enabled.as_str(),
            "Token estimation toggle must not change markdown output when front matter is enabled"
        );

        prop_assert!(!markdown_disabled_disabled.starts_with("---\n"));
        prop_assert!(!markdown_enabled_disabled.starts_with("---\n"));
        prop_assert!(markdown_disabled_enabled.starts_with("---\n"));
        prop_assert!(markdown_enabled_enabled.starts_with("---\n"));

        prop_assert_eq!(
            markdown_body_without_front_matter(&markdown_disabled_enabled),
            markdown_disabled_disabled.trim_start_matches('\n'),
            "Front matter toggle should only add/remove front matter wrapper"
        );
        prop_assert_eq!(
            markdown_body_without_front_matter(&markdown_enabled_enabled),
            markdown_enabled_disabled.trim_start_matches('\n'),
            "Front matter toggle should not alter markdown body content"
        );
    }
}
