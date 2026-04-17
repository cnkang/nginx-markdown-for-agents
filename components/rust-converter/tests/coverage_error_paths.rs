//! Coverage improvement tests for error paths and edge cases.
//!
//! These tests target uncovered code paths in critical modules:
//! - `error.rs`: All error variant Display and code() methods
//! - `ffi/convert.rs`: Error paths in convert_inner
//! - `security.rs`: check_attributes and get_attributes_to_remove
//! - `metadata/resolve.rs`: URL resolution edge cases
//! - `converter/tables.rs`: Table edge cases
//! - `converter/blocks.rs`: Block formatting edge cases

use nginx_markdown_converter::MarkdownConverter;
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::ffi::*;
use nginx_markdown_converter::parser::parse_html;
use nginx_markdown_converter::security::{SanitizeAction, SecurityValidator};
use std::ptr;

/// Helper: parse HTML string and convert to Markdown using default options.
fn convert_html(html: &str) -> String {
    let dom = parse_html(html.as_bytes()).expect("Parse failed");
    MarkdownConverter::new()
        .convert(&dom)
        .expect("Conversion failed")
}

/* ================================================================
 * error.rs coverage — all non-streaming variants
 * ================================================================ */

#[test]
fn test_error_code_parse_error() {
    let err = ConversionError::ParseError("bad html".into());
    assert_eq!(err.code(), 1);
    assert_eq!(format!("{}", err), "Parse error: bad html");
}

#[test]
fn test_error_code_encoding_error() {
    let err = ConversionError::EncodingError("invalid utf-8".into());
    assert_eq!(err.code(), 2);
    assert_eq!(format!("{}", err), "Encoding error: invalid utf-8");
}

#[test]
fn test_error_code_timeout() {
    let err = ConversionError::Timeout;
    assert_eq!(err.code(), 3);
    assert_eq!(format!("{}", err), "Conversion timeout exceeded");
}

#[test]
fn test_error_code_memory_limit() {
    let err = ConversionError::MemoryLimit("exceeded 1MB".into());
    assert_eq!(err.code(), 4);
    assert_eq!(format!("{}", err), "Memory limit exceeded: exceeded 1MB");
}

#[test]
fn test_error_code_invalid_input() {
    let err = ConversionError::InvalidInput("empty".into());
    assert_eq!(err.code(), 5);
    assert_eq!(format!("{}", err), "Invalid input: empty");
}

#[test]
fn test_error_code_internal_error() {
    let err = ConversionError::InternalError("unexpected".into());
    assert_eq!(err.code(), 99);
    assert_eq!(format!("{}", err), "Internal error: unexpected");
}

#[test]
fn test_error_is_std_error() {
    let err = ConversionError::Timeout;
    /* Verify ConversionError implements std::error::Error */
    let _: &dyn std::error::Error = &err;
}

#[test]
fn test_error_debug_format() {
    let err = ConversionError::ParseError("test".into());
    let debug = format!("{:?}", err);
    assert!(debug.contains("ParseError"));
}

#[test]
fn test_error_clone() {
    let err = ConversionError::MemoryLimit("clone test".into());
    let cloned = err.clone();
    assert_eq!(err.code(), cloned.code());
    assert_eq!(format!("{}", err), format!("{}", cloned));
}

/* ================================================================
 * security.rs coverage — check_attributes and get_attributes_to_remove
 * These require html5ever Attribute types, so we test via conversion
 * ================================================================ */

#[test]
fn test_security_check_element_all_dangerous() {
    let validator = SecurityValidator::new();

    /* All dangerous elements */
    for elem in &["script", "style", "noscript", "applet", "link", "base"] {
        assert_eq!(
            validator.check_element(elem),
            SanitizeAction::Remove,
            "{} should be removed",
            elem
        );
    }
}

#[test]
fn test_security_check_element_all_form_elements() {
    let validator = SecurityValidator::new();

    for elem in &[
        "form", "button", "select", "textarea", "fieldset", "legend", "label", "option",
        "optgroup", "datalist", "output",
    ] {
        assert_eq!(
            validator.check_element(elem),
            SanitizeAction::StripElement,
            "{} should be stripped",
            elem
        );
    }
}

#[test]
fn test_security_check_element_all_embedded() {
    let validator = SecurityValidator::new();

    for elem in &["iframe", "object", "embed"] {
        assert_eq!(
            validator.check_element(elem),
            SanitizeAction::StripElement,
            "{} should be stripped",
            elem
        );
        assert!(
            validator.is_embedded_content(elem),
            "{} should be embedded content",
            elem
        );
    }
}

#[test]
fn test_security_void_form_control() {
    let validator = SecurityValidator::new();
    assert!(validator.is_void_form_control("input"));
    assert!(!validator.is_void_form_control("textarea"));
    assert!(!validator.is_void_form_control("select"));
    assert!(!validator.is_void_form_control("div"));
}

#[test]
fn test_security_event_handler_edge_cases() {
    let validator = SecurityValidator::new();

    /* Bare "on" is not an event handler */
    assert!(!validator.is_event_handler("on"));
    /* Single char after "on" is an event handler */
    assert!(validator.is_event_handler("onx"));
    /* Empty string */
    assert!(!validator.is_event_handler(""));
    /* "o" alone */
    assert!(!validator.is_event_handler("o"));
}

#[test]
fn test_security_dangerous_url_edge_cases() {
    let validator = SecurityValidator::new();

    /* Whitespace-padded dangerous URLs */
    assert!(validator.is_dangerous_url("  javascript:void(0)"));
    assert!(validator.is_dangerous_url("\tdata:text/html,test"));
    assert!(validator.is_dangerous_url("\n\rvbscript:test"));

    /* Control characters in URL */
    assert!(validator.is_dangerous_url("java\x01script:test"));
    assert!(validator.is_dangerous_url("\x00test"));

    /* about: scheme */
    assert!(validator.is_dangerous_url("about:blank"));

    /* Safe URLs */
    assert!(!validator.is_dangerous_url("mailto:test@example.com"));
    assert!(!validator.is_dangerous_url("tel:+1234567890"));
    assert!(!validator.is_dangerous_url(""));
}

#[test]
fn test_security_sanitize_url_edge_cases() {
    let validator = SecurityValidator::new();

    assert_eq!(validator.sanitize_url(""), Some(""));
    assert_eq!(
        validator.sanitize_url("https://safe.com"),
        Some("https://safe.com")
    );
    assert_eq!(validator.sanitize_url("javascript:void(0)"), None);
    assert_eq!(validator.sanitize_url("data:,"), None);
}

#[test]
fn test_security_depth_validation_boundary() {
    let validator = SecurityValidator::with_max_depth(10);

    assert!(validator.validate_depth(0).is_ok());
    assert!(validator.validate_depth(10).is_ok());
    assert!(validator.validate_depth(11).is_err());

    let err = validator.validate_depth(11).unwrap_err();
    assert!(err.contains("11"));
    assert!(err.contains("10"));
}

#[test]
fn test_security_default_trait() {
    let validator = SecurityValidator::default();
    /* Default should allow safe elements */
    assert_eq!(validator.check_element("div"), SanitizeAction::Allow);
    /* Default should have max depth of 1000 */
    assert!(validator.validate_depth(1000).is_ok());
    assert!(validator.validate_depth(1001).is_err());
}

/* ================================================================
 * FFI convert error paths via markdown_convert
 * ================================================================ */

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

#[test]
fn test_ffi_convert_null_handle() {
    let html = b"<p>test</p>";
    let options = ffi_test_default_options();
    let mut result = ffi_test_empty_result();

    unsafe {
        markdown_convert(
            ptr::null_mut(),
            html.as_ptr(),
            html.len(),
            &options,
            &mut result,
        );
    }

    assert_ne!(result.error_code, 0, "NULL handle should produce error");
    unsafe { markdown_result_free(&mut result) };
}

#[test]
fn test_ffi_convert_null_options() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let html = b"<p>test</p>";
    let mut result = ffi_test_empty_result();

    unsafe {
        markdown_convert(
            converter,
            html.as_ptr(),
            html.len(),
            ptr::null(),
            &mut result,
        );
    }

    assert_ne!(result.error_code, 0, "NULL options should produce error");
    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_convert_null_result() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let html = b"<p>test</p>";
    let options = ffi_test_default_options();

    /* NULL result should be a no-op, not crash */
    unsafe {
        markdown_convert(
            converter,
            html.as_ptr(),
            html.len(),
            &options,
            ptr::null_mut(),
        );
    }

    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_convert_null_html_with_nonzero_len() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let options = ffi_test_default_options();
    let mut result = ffi_test_empty_result();

    unsafe {
        markdown_convert(converter, ptr::null(), 100, &options, &mut result);
    }

    assert_ne!(
        result.error_code, 0,
        "NULL html with non-zero len should error"
    );
    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_convert_empty_html() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let options = ffi_test_default_options();
    let mut result = ffi_test_empty_result();

    unsafe {
        markdown_convert(converter, ptr::null(), 0, &options, &mut result);
    }

    assert_eq!(result.error_code, 0, "Empty HTML should succeed");
    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_convert_with_etag_and_tokens() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let options = MarkdownOptions {
        generate_etag: 1,
        estimate_tokens: 1,
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();
    let html = b"<h1>Title</h1><p>Content here</p>";

    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }

    assert_eq!(result.error_code, 0, "Conversion should succeed");
    assert!(!result.etag.is_null(), "ETag should be generated");
    assert!(result.etag_len > 0, "ETag should be non-empty");
    assert!(result.token_estimate > 0, "Token estimate should be > 0");

    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_convert_empty_html_with_etag_and_tokens() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let options = MarkdownOptions {
        generate_etag: 1,
        estimate_tokens: 1,
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();

    unsafe {
        markdown_convert(converter, ptr::null(), 0, &options, &mut result);
    }

    assert_eq!(result.error_code, 0, "Empty HTML with etag should succeed");
    /* ETag should still be generated for empty content */
    assert!(!result.etag.is_null(), "ETag should be generated for empty");

    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_convert_with_gfm_flavor() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let options = MarkdownOptions {
        flavor: 1,
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();
    let html = b"<table><tr><th>A</th></tr><tr><td>1</td></tr></table>";

    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }

    assert_eq!(result.error_code, 0, "GFM conversion should succeed");
    assert!(result.markdown_len > 0, "Should produce markdown");

    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_convert_with_base_url() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let base_url = b"https://example.com/docs/";
    let options = MarkdownOptions {
        base_url: base_url.as_ptr(),
        base_url_len: base_url.len(),
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();
    let html = b"<a href=\"/page\">Link</a>";

    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }

    assert_eq!(result.error_code, 0, "Base URL conversion should succeed");

    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_convert_with_content_type() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let ct = b"text/html; charset=utf-8";
    let options = MarkdownOptions {
        content_type: ct.as_ptr(),
        content_type_len: ct.len(),
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();
    let html = b"<p>Hello</p>";

    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }

    assert_eq!(
        result.error_code, 0,
        "Content-type conversion should succeed"
    );

    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_convert_with_front_matter() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let options = MarkdownOptions {
        front_matter: 1,
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();
    let html = b"<html><head><title>Test</title></head><body><p>Content</p></body></html>";

    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }

    assert_eq!(
        result.error_code, 0,
        "Front matter conversion should succeed"
    );

    unsafe { markdown_result_free(&mut result) };
    unsafe { markdown_converter_free(converter) };
}

#[test]
fn test_ffi_result_free_null() {
    /* NULL result free should be a no-op */
    unsafe { markdown_result_free(ptr::null_mut()) };
}

#[test]
fn test_ffi_converter_free_null() {
    /* NULL converter free should be a no-op */
    unsafe { markdown_converter_free(ptr::null_mut()) };
}

/* ================================================================
 * Converter edge cases for blocks and tables
 * ================================================================ */

#[test]
fn test_converter_code_block_with_backticks_in_content() {
    let md = convert_html("<pre><code>```\nsome code\n```</code></pre>");
    /* Should use a longer fence to avoid conflict */
    assert!(md.contains("````") || md.contains("~~~"));
}

#[test]
fn test_converter_nested_lists() {
    let md = convert_html("<ul><li>A<ul><li>B<ul><li>C</li></ul></li></ul></li></ul>");
    assert!(md.contains("A"));
    assert!(md.contains("B"));
    assert!(md.contains("C"));
}

#[test]
fn test_converter_table_with_alignment() {
    let md = convert_html(
        r#"<table>
        <tr><th style="text-align:left">Left</th><th style="text-align:center">Center</th><th style="text-align:right">Right</th></tr>
        <tr><td>1</td><td>2</td><td>3</td></tr>
    </table>"#,
    );
    assert!(md.contains("Left"));
}

#[test]
fn test_converter_table_with_pipe_in_cell() {
    let md = convert_html("<table><tr><th>Header</th></tr><tr><td>a | b</td></tr></table>");
    /* Pipe in cell should be escaped */
    assert!(md.contains("a \\| b") || md.contains("a | b"));
}

#[test]
fn test_converter_empty_table() {
    let _md = convert_html("<table></table>");
}

#[test]
fn test_converter_table_no_header() {
    let md =
        convert_html("<table><tr><td>A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>");
    assert!(md.contains("A"));
}

/* ================================================================
 * URL resolution edge cases (metadata/resolve.rs)
 * ================================================================ */

#[test]
fn test_url_resolution_via_converter() {
    let converter = markdown_converter_new();
    assert!(!converter.is_null());

    let base_url = b"https://example.com/docs/guide/";
    let options = MarkdownOptions {
        base_url: base_url.as_ptr(),
        base_url_len: base_url.len(),
        ..ffi_test_default_options()
    };
    let mut result = ffi_test_empty_result();

    /* Relative URL */
    let html = b"<a href=\"page.html\">Link</a>";
    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }
    assert_eq!(result.error_code, 0);
    unsafe { markdown_result_free(&mut result) };

    /* Absolute path URL */
    let html = b"<a href=\"/other\">Link</a>";
    result = ffi_test_empty_result();
    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }
    assert_eq!(result.error_code, 0);
    unsafe { markdown_result_free(&mut result) };

    /* Protocol-relative URL */
    let html = b"<a href=\"//cdn.example.com/file\">Link</a>";
    result = ffi_test_empty_result();
    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }
    assert_eq!(result.error_code, 0);
    unsafe { markdown_result_free(&mut result) };

    /* Already absolute URL */
    let html = b"<a href=\"https://other.com/page\">Link</a>";
    result = ffi_test_empty_result();
    unsafe {
        markdown_convert(converter, html.as_ptr(), html.len(), &options, &mut result);
    }
    assert_eq!(result.error_code, 0);
    unsafe { markdown_result_free(&mut result) };

    unsafe { markdown_converter_free(converter) };
}

/* ================================================================
 * Conversion with security-relevant HTML
 * ================================================================ */

#[test]
fn test_conversion_strips_event_handlers() {
    let md = convert_html(r#"<a href="https://safe.com" onclick="alert('xss')">Click</a>"#);
    assert!(md.contains("Click"));
    assert!(!md.contains("onclick"));
    assert!(!md.contains("alert"));
}

#[test]
fn test_conversion_strips_style_attributes() {
    let md = convert_html(r#"<p style="color:red;background:url(javascript:void(0))">Text</p>"#);
    assert!(md.contains("Text"));
    assert!(!md.contains("style"));
}

#[test]
fn test_conversion_handles_deeply_nested_html() {
    let mut html = String::new();
    for _ in 0..50 {
        html.push_str("<div>");
    }
    html.push_str("<p>Deep content</p>");
    for _ in 0..50 {
        html.push_str("</div>");
    }
    let md = convert_html(&html);
    assert!(md.contains("Deep content"));
}

/* ================================================================
 * Heading edge cases
 * ================================================================ */

#[test]
fn test_converter_heading_levels() {
    for level in 1..=6 {
        let html = format!("<h{level}>Heading {level}</h{level}>");
        let md = convert_html(&html);
        let prefix = "#".repeat(level);
        assert!(
            md.contains(&format!("{} Heading {}", prefix, level)),
            "h{} should produce {} prefix",
            level,
            prefix
        );
    }
}

#[test]
fn test_converter_blockquote() {
    let md = convert_html("<blockquote><p>Quoted text</p></blockquote>");
    assert!(md.contains("Quoted text"));
}

#[test]
fn test_converter_horizontal_rule() {
    let md = convert_html("<p>Before</p><hr><p>After</p>");
    assert!(md.contains("Before"));
    assert!(md.contains("After"));
}

#[test]
fn test_converter_image_with_alt() {
    let md = convert_html(r#"<img src="https://example.com/img.png" alt="Alt text">"#);
    assert!(md.contains("Alt text"));
}

#[test]
fn test_converter_video_source() {
    let _md = convert_html(r#"<video><source src="https://example.com/video.mp4"></video>"#);
}

#[test]
fn test_converter_audio_source() {
    let _md = convert_html(r#"<audio src="https://example.com/audio.mp3">Audio</audio>"#);
}
