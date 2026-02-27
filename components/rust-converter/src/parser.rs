//! HTML5 parser using html5ever
//!
//! This module provides HTML parsing functionality that handles malformed
//! markup gracefully according to the HTML5 specification.
//!
//! # Overview
//!
//! The parser uses Mozilla's html5ever library, which implements the WHATWG HTML5
//! parsing algorithm. This ensures that even malformed HTML is parsed consistently
//! and predictably, following the same rules as modern web browsers.
//!
//! # Features
//!
//! - **HTML5 Compliance**: Follows the WHATWG HTML5 parsing specification
//! - **Malformed Markup Handling**: Gracefully handles invalid HTML
//! - **Charset Handling**: Detects charset and transcodes supported encodings to UTF-8
//! - **Error Recovery**: Attempts to parse even broken HTML documents
//!
//! # Examples
//!
//! ```rust
//! use nginx_markdown_converter::parser::parse_html;
//!
//! // Parse well-formed HTML
//! let html = b"<html><body><h1>Hello</h1></body></html>";
//! let dom = parse_html(html).expect("Failed to parse HTML");
//!
//! // Parse malformed HTML (missing closing tags)
//! let malformed = b"<html><body><h1>Hello";
//! let dom = parse_html(malformed).expect("Parser handles malformed HTML");
//!
//! // Parse HTML with UTF-8 content
//! let utf8_html = b"<html><body><p>\xE2\x9C\x93 Unicode</p></body></html>";
//! let dom = parse_html(utf8_html).expect("UTF-8 content parsed");
//! ```
//!
//! # Configuration
//!
//! The parser uses default html5ever configuration:
//! - **Scripting**: Disabled (scripts are not executed)
//! - **Error Handling**: Errors are collected but parsing continues
//! - **Tree Builder**: Uses RcDom for reference-counted DOM nodes
//!
//! # Performance Considerations
//!
//! - The parser allocates memory for the DOM tree proportional to document size
//! - Large documents should be size-limited before parsing (enforced by caller)
//! - Parsing is single-threaded and synchronous

use html5ever::parse_document;
use html5ever::tendril::TendrilSink;
use markup5ever_rcdom::RcDom;
use std::borrow::Cow;

use crate::charset::detect_charset;
use crate::error::ConversionError;

/// Parse HTML bytes into a DOM tree with charset detection
///
/// This function implements the charset detection cascade specified in
/// Requirements FR-05.1, FR-05.2, and FR-05.3:
///
/// 1. Check Content-Type header charset parameter (FR-05.1)
/// 2. Check HTML meta charset tags (FR-05.2)
/// 3. Default to UTF-8 (FR-05.3)
///
/// # Arguments
///
/// * `html` - A byte slice containing HTML content
/// * `content_type` - Optional Content-Type header value (e.g., "text/html; charset=UTF-8")
///
/// # Returns
///
/// Returns `Ok(RcDom)` containing the parsed DOM tree on success.
/// Returns `Err(ConversionError)` if parsing fails or encoding is invalid.
///
/// # Errors
///
/// This function returns an error in the following cases:
///
/// - `ConversionError::EncodingError`: The input is invalid for the detected charset,
///   or the detected charset is unsupported
/// - `ConversionError::ParseError`: HTML parsing failed (rare, as html5ever is very permissive)
/// - `ConversionError::InvalidInput`: Input is empty or null
///
/// # Examples
///
/// ```rust
/// use nginx_markdown_converter::parser::parse_html_with_charset;
///
/// // Parse with Content-Type charset
/// let html = b"<html><body><h1>Hello</h1></body></html>";
/// let dom = parse_html_with_charset(html, Some("text/html; charset=UTF-8"))
///     .expect("Failed to parse HTML");
///
/// // Parse with HTML meta charset
/// let html = b"<html><head><meta charset=\"UTF-8\"></head><body>Content</body></html>";
/// let dom = parse_html_with_charset(html, None)
///     .expect("Failed to parse HTML");
///
/// // Parse with default UTF-8
/// let html = b"<html><body>No charset specified</body></html>";
/// let dom = parse_html_with_charset(html, None)
///     .expect("Failed to parse HTML");
/// ```
///
/// # Charset Detection Cascade
///
/// The function follows a three-level cascade:
///
/// 1. **Content-Type Header** (Priority 1): If `content_type` parameter contains
///    a charset parameter, use that charset for parsing.
///
/// 2. **HTML Meta Tags** (Priority 2): If Content-Type has no charset, scan the
///    HTML for `<meta charset>` or `<meta http-equiv="Content-Type">` tags.
///
/// 3. **Default to UTF-8** (Priority 3): If both fail, assume UTF-8 encoding.
///
/// # Performance Notes
///
/// - Charset detection scans only the first 1024 bytes of HTML
/// - Input is decoded/transcoded to UTF-8 before parsing
/// - Parsing time is roughly linear with document size
pub fn parse_html_with_charset(
    html: &[u8],
    content_type: Option<&str>,
) -> Result<RcDom, ConversionError> {
    // Validate input is not empty
    if html.is_empty() {
        return Err(ConversionError::InvalidInput(
            "HTML input is empty".to_string(),
        ));
    }

    // Detect charset using the three-level cascade
    let detected_charset = detect_charset(content_type, html);

    // Decode to UTF-8 before html5ever parsing. html5ever's `from_utf8()` expects UTF-8 bytes,
    // so non-UTF-8 inputs must be transcoded according to the detected charset.
    let utf8_str = decode_html_to_utf8(html, &detected_charset)?;

    // Parse the HTML document using html5ever directly from a UTF-8 string
    // sink to avoid `std::io::Read`/Cursor overhead in the hot path.
    let dom = parse_document(RcDom::default(), Default::default())
        .one(utf8_str.as_ref());

    Ok(dom)
}

fn decode_html_to_utf8<'a>(
    html: &'a [u8],
    detected_charset: &str,
) -> Result<Cow<'a, str>, ConversionError> {
    if detected_charset.eq_ignore_ascii_case("UTF-8") {
        return std::str::from_utf8(html).map(Cow::Borrowed).map_err(|e| {
            ConversionError::EncodingError(format!(
                "Invalid UTF-8 at byte position {}: {} (detected charset: {})",
                e.valid_up_to(),
                e,
                detected_charset
            ))
        });
    }

    let encoding =
        encoding_rs::Encoding::for_label(detected_charset.as_bytes()).ok_or_else(|| {
            ConversionError::EncodingError(format!(
                "Unsupported charset '{}' for HTML parsing",
                detected_charset
            ))
        })?;

    encoding
        .decode_without_bom_handling_and_without_replacement(html)
        .ok_or_else(|| {
            ConversionError::EncodingError(format!(
                "Invalid byte sequence for charset '{}'",
                detected_charset
            ))
        })
}

/// Parse HTML bytes into a DOM tree
///
/// This is a convenience function that calls `parse_html_with_charset` with
/// no Content-Type header, relying on HTML meta tags or defaulting to UTF-8.
///
/// # Arguments
///
/// * `html` - A byte slice containing HTML content. Must be valid for the detected/default charset.
///
/// # Returns
///
/// Returns `Ok(RcDom)` containing the parsed DOM tree on success.
/// Returns `Err(ConversionError)` if parsing fails or encoding is invalid.
///
/// # Examples
///
/// ```rust
/// use nginx_markdown_converter::parser::parse_html;
///
/// // Parse a simple HTML document
/// let html = b"<html><body><h1>Hello</h1></body></html>";
/// let dom = parse_html(html).expect("Failed to parse");
/// ```
///
/// # See Also
///
/// - `parse_html_with_charset`: For parsing with Content-Type header support
pub fn parse_html(html: &[u8]) -> Result<RcDom, ConversionError> {
    parse_html_with_charset(html, None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::converter::MarkdownConverter;
    use proptest::prelude::*;

    #[test]
    fn test_parse_simple_html() {
        let html = b"<html><body><h1>Hello</h1></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse simple HTML");
    }

    #[test]
    fn test_parse_malformed_html() {
        // Missing closing tags
        let html = b"<html><body><h1>Hello";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should handle malformed HTML gracefully");
    }

    #[test]
    fn test_parse_empty_input() {
        let html = b"";
        let result = parse_html(html);
        assert!(result.is_err(), "Should reject empty input");
        match result {
            Err(ConversionError::InvalidInput(_)) => (),
            _ => panic!("Expected InvalidInput error"),
        }
    }

    #[test]
    fn test_parse_invalid_utf8() {
        // Invalid UTF-8 sequence
        let html = b"\xFF\xFE<html><body>Invalid</body></html>";
        let result = parse_html(html);
        assert!(result.is_err(), "Should reject invalid UTF-8");
        match result {
            Err(ConversionError::EncodingError(_)) => (),
            _ => panic!("Expected EncodingError"),
        }
    }

    #[test]
    fn test_parse_utf8_content() {
        // Valid UTF-8 with Unicode characters
        let html = b"<html><body><p>\xE2\x9C\x93 Check mark</p></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse UTF-8 content");
    }

    #[test]
    fn test_parse_html_entities() {
        let html = b"<html><body><p>&lt;tag&gt; &amp; &quot;quotes&quot;</p></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse HTML entities");
    }

    #[test]
    fn test_parse_nested_elements() {
        let html =
            b"<html><body><div><p><strong>Bold <em>italic</em></strong></p></div></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse nested elements");
    }

    #[test]
    fn test_parse_misnested_tags() {
        // Misnested tags: <b><i>text</b></i>
        let html = b"<html><body><b><i>text</b></i></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should handle misnested tags");
    }

    #[test]
    fn test_parse_doctype() {
        let html = b"<!DOCTYPE html><html><head><title>Test</title></head><body></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse DOCTYPE");
    }

    #[test]
    fn test_parse_fragment() {
        // HTML fragment without DOCTYPE or html/body tags
        let html = b"<div><p>Content</p></div>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse HTML fragment");
    }

    #[test]
    fn test_parse_with_comments() {
        let html = b"<html><!-- Comment --><body><p>Text</p></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse HTML with comments");
    }

    #[test]
    fn test_parse_with_charset_content_type() {
        // Test parsing with Content-Type charset parameter
        let html = b"<html><body><h1>Hello</h1></body></html>";
        let result = parse_html_with_charset(html, Some("text/html; charset=UTF-8"));
        assert!(result.is_ok(), "Should parse with Content-Type charset");
    }

    #[test]
    fn test_parse_with_charset_html_meta() {
        // Test parsing with HTML meta charset tag
        let html = b"<html><head><meta charset=\"UTF-8\"></head><body><h1>Hello</h1></body></html>";
        let result = parse_html_with_charset(html, None);
        assert!(result.is_ok(), "Should parse with HTML meta charset");
    }

    #[test]
    fn test_parse_with_charset_default_utf8() {
        // Test parsing with default UTF-8 (no charset specified)
        let html = b"<html><body><h1>Hello</h1></body></html>";
        let result = parse_html_with_charset(html, None);
        assert!(result.is_ok(), "Should parse with default UTF-8");
    }

    #[test]
    fn test_parse_with_charset_priority() {
        // Test that Content-Type takes priority over HTML meta tag
        let html =
            b"<html><head><meta charset=\"ISO-8859-1\"></head><body><h1>Hello</h1></body></html>";
        let result = parse_html_with_charset(html, Some("text/html; charset=UTF-8"));
        assert!(
            result.is_ok(),
            "Should parse with Content-Type taking priority"
        );
    }

    #[test]
    fn test_parse_with_iso_8859_1_content_type_transcodes_to_utf8() {
        // "Café" encoded as ISO-8859-1 (0xE9 is invalid UTF-8)
        let html = b"<html><body><p>Caf\xE9</p></body></html>";
        let dom = parse_html_with_charset(html, Some("text/html; charset=ISO-8859-1"))
            .expect("Should transcode ISO-8859-1 input");

        let markdown = MarkdownConverter::new()
            .convert(&dom)
            .expect("Converted Markdown should be produced");
        assert!(
            markdown.contains("Café"),
            "Expected transcoded content in Markdown, got: {markdown:?}"
        );
    }

    #[test]
    fn test_parse_with_windows_1252_content_type_transcodes_to_utf8() {
        // "€" is 0x80 in windows-1252 and invalid UTF-8
        let html = b"<html><body><p>Price \x80 10</p></body></html>";
        let dom = parse_html_with_charset(html, Some("text/html; charset=windows-1252"))
            .expect("Should transcode windows-1252 input");

        let markdown = MarkdownConverter::new()
            .convert(&dom)
            .expect("Converted Markdown should be produced");
        assert!(
            markdown.contains("€"),
            "Expected euro sign after windows-1252 decode, got: {markdown:?}"
        );
    }

    #[test]
    fn test_parse_with_non_utf8_charset_from_meta_transcodes_to_utf8() {
        // HTML meta tag is ASCII; body contains ISO-8859-1 encoded "é".
        let html =
            b"<html><head><meta charset=\"ISO-8859-1\"></head><body><p>Caf\xE9</p></body></html>";
        let dom =
            parse_html_with_charset(html, None).expect("Should use meta charset and transcode");

        let markdown = MarkdownConverter::new()
            .convert(&dom)
            .expect("Converted Markdown should be produced");
        assert!(
            markdown.contains("Café"),
            "Expected transcoded content from meta charset, got: {markdown:?}"
        );
    }

    #[test]
    fn test_parse_with_unknown_charset_returns_encoding_error() {
        let html = b"<html><body><p>Hello</p></body></html>";
        let result = parse_html_with_charset(html, Some("text/html; charset=x-unknown-test"));

        match result {
            Err(ConversionError::EncodingError(message)) => {
                assert!(message.contains("Unsupported charset"));
            }
            Ok(_) => panic!("Expected EncodingError for unknown charset, got Ok(_)"),
            Err(err) => panic!("Expected EncodingError for unknown charset, got: {err}"),
        }
    }

    #[test]
    fn test_parse_with_scripts() {
        let html = b"<html><body><script>alert('test');</script><p>Content</p></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse HTML with scripts");
    }

    #[test]
    fn test_parse_with_styles() {
        let html = b"<html><head><style>body { color: red; }</style></head><body></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse HTML with styles");
    }

    #[test]
    fn test_parse_chinese_characters() {
        // Chinese characters: 世界 (world)
        let html = b"<html><body><p>\xE4\xB8\x96\xE7\x95\x8C</p></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse Chinese characters");
    }

    #[test]
    fn test_parse_emoji() {
        // Emoji: 😀 (grinning face)
        let html = b"<html><body><p>\xF0\x9F\x98\x80</p></body></html>";
        let result = parse_html(html);
        assert!(result.is_ok(), "Should parse emoji");
    }

    // ============================================================================
    // Property-Based Tests
    // ============================================================================

    // Property 9: Malformed HTML Handling
    // Validates: Requirements FR-03.7, NFR-01.1, NFR-01.4
    //
    // This property test verifies that the HTML parser handles malformed markup
    // gracefully without crashing. The parser should either:
    // 1. Successfully parse the HTML (even if malformed) and return a DOM tree
    // 2. Return a proper error (EncodingError or InvalidInput)
    // 3. Never panic or crash
    //
    // Test Strategy:
    // - Generate various malformed HTML patterns
    // - Verify parser doesn't panic (caught by proptest framework)
    // - Verify result is either Ok or a proper error
    // - Test unclosed tags, invalid nesting, broken UTF-8, etc.
    proptest! {
        #[test]
        fn prop_malformed_html_no_crash(
            // Generate various malformed HTML patterns
            tag in prop::sample::select(vec!["div", "p", "span", "h1", "ul", "li", "table", "tr", "td"]),
            content in "[a-zA-Z0-9 ]{0,100}",
            close_tag in prop::bool::ANY,
            add_invalid_nesting in prop::bool::ANY,
        ) {
            // Build malformed HTML with various patterns
            let mut html = String::new();

            // Pattern 1: Unclosed tags
            html.push_str(&format!("<{}>", tag));
            html.push_str(&content);
            if close_tag {
                html.push_str(&format!("</{}>", tag));
            }
            // If close_tag is false, tag is left unclosed (malformed)

            // Pattern 2: Invalid nesting (optional)
            if add_invalid_nesting {
                html.push_str("<p><div>Invalid nesting</div></p>");
            }

            // Test the parser
            let result = parse_html(html.as_bytes());

            // Property: Parser must not panic and must return a valid result
            // Either Ok (parsed successfully despite malformation) or Err (proper error)
            match result {
                Ok(_dom) => {
                    // Success: html5ever handled the malformed HTML gracefully
                    // This is the expected behavior per HTML5 spec
                },
                Err(ConversionError::EncodingError(_)) => {
                    // Acceptable: encoding error detected
                },
                Err(ConversionError::InvalidInput(_)) => {
                    // Acceptable: invalid input detected
                },
                Err(e) => {
                    // Unexpected error type for malformed HTML
                    panic!("Unexpected error type for malformed HTML: {:?}", e);
                }
            }
        }

        #[test]
        fn prop_unclosed_tags_handled(
            tag in prop::sample::select(vec!["div", "p", "span", "h1", "h2", "h3", "ul", "ol", "li"]),
            content in "[a-zA-Z0-9 ]{1,50}",
        ) {
            // Generate HTML with unclosed tags
            let html = format!("<html><body><{0}>{1}", tag, content);

            // Parser should handle unclosed tags gracefully
            let result = parse_html(html.as_bytes());

            // Property: Unclosed tags should be parsed successfully
            // html5ever automatically closes tags per HTML5 spec
            prop_assert!(result.is_ok(), "Parser should handle unclosed tags: {}", html);
        }

        #[test]
        fn prop_misnested_tags_handled(
            outer_tag in prop::sample::select(vec!["b", "i", "strong", "em"]),
            inner_tag in prop::sample::select(vec!["b", "i", "strong", "em"]),
            content in "[a-zA-Z0-9 ]{1,30}",
        ) {
            // Generate misnested tags: <outer><inner>content</outer></inner>
            let html = format!(
                "<html><body><{0}><{1}>{2}</{0}></{1}></body></html>",
                outer_tag, inner_tag, content
            );

            // Parser should handle misnested tags gracefully
            let result = parse_html(html.as_bytes());

            // Property: Misnested tags should be parsed successfully
            // html5ever corrects nesting per HTML5 spec
            prop_assert!(result.is_ok(), "Parser should handle misnested tags: {}", html);
        }

        #[test]
        fn prop_invalid_nesting_handled(
            content in "[a-zA-Z0-9 ]{1,30}",
        ) {
            // Generate invalid nesting patterns (block inside inline, etc.)
            let patterns = vec![
                format!("<p><div>{}</div></p>", content),
                format!("<span><p>{}</p></span>", content),
                format!("<a><div>{}</div></a>", content),
            ];

            for html in patterns {
                // Parser should handle invalid nesting gracefully
                let result = parse_html(html.as_bytes());

                // Property: Invalid nesting should be parsed successfully
                // html5ever restructures per HTML5 spec
                prop_assert!(result.is_ok(), "Parser should handle invalid nesting: {}", html);
            }
        }

        #[test]
        fn prop_broken_attributes_handled(
            tag in prop::sample::select(vec!["div", "p", "a", "img"]),
            attr_name in "[a-z]{1,10}",
            attr_value in "[a-zA-Z0-9]{0,20}",
            broken in prop::bool::ANY,
        ) {
            // Generate HTML with broken attributes
            let html = if broken {
                // Missing closing quote
                format!("<{} {}=\"{}>Content</{}>", tag, attr_name, attr_value, tag)
            } else {
                // Missing value
                format!("<{} {}>Content</{}>", tag, attr_name, tag)
            };

            // Parser should handle broken attributes gracefully
            let result = parse_html(html.as_bytes());

            // Property: Broken attributes should be parsed successfully
            prop_assert!(result.is_ok(), "Parser should handle broken attributes: {}", html);
        }

        #[test]
        fn prop_empty_and_whitespace_handled(
            whitespace in prop::sample::select(vec!["", " ", "  ", "\n", "\t", "\r\n"]),
        ) {
            // Test empty and whitespace-only inputs
            if whitespace.is_empty() {
                // Empty input should return InvalidInput error
                let result = parse_html(whitespace.as_bytes());
                prop_assert!(result.is_err(), "Empty input should return error");
                if let Err(e) = result {
                    prop_assert!(matches!(e, ConversionError::InvalidInput(_)));
                }
            } else {
                // Whitespace-only input should be parsed (html5ever is permissive)
                let result = parse_html(whitespace.as_bytes());
                prop_assert!(result.is_ok(), "Whitespace should be parsed: {:?}", whitespace);
            }
        }

        #[test]
        fn prop_deeply_nested_handled(
            depth in 1usize..20usize,
            content in "[a-zA-Z]{1,10}",
        ) {
            // Generate deeply nested HTML
            let mut html = String::from("<html><body>");
            for _ in 0..depth {
                html.push_str("<div>");
            }
            html.push_str(&content);
            for _ in 0..depth {
                html.push_str("</div>");
            }
            html.push_str("</body></html>");

            // Parser should handle deep nesting
            let result = parse_html(html.as_bytes());

            // Property: Deep nesting should be parsed successfully
            prop_assert!(result.is_ok(), "Parser should handle deep nesting (depth={})", depth);
        }

        #[test]
        fn prop_special_characters_in_content(
            special_chars in prop::sample::select(vec![
                "<>&\"'", "<<<>>>", "&&&", "\"\"\"", "'''",
                "\0\0\0", // Null bytes
            ]),
        ) {
            // Generate HTML with special characters in content
            let html = format!("<html><body><p>{}</p></body></html>", special_chars);

            // Parser should handle special characters
            let result = parse_html(html.as_bytes());

            // Property: Special characters should be parsed
            // Note: Some may cause encoding errors (e.g., null bytes)
            match result {
                Ok(_) => {}, // Successfully parsed
                Err(ConversionError::EncodingError(_)) => {}, // Acceptable for invalid UTF-8
                Err(e) => {
                    panic!("Unexpected error for special chars: {:?}", e);
                }
            }
        }
    }
}
