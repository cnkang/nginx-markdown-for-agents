//! Tests for parsing heavy inputs (deep nesting, many nodes).
//!
//! Validates: REQ-0700-CORRECTNESS-006 (解析阶段超时和解析器预算)
//!
//! This test exercises the parser with heavy inputs and verifies:
//! - Deep nesting (100+ levels of nested divs) doesn't crash
//! - Many nodes (10000+ elements) completes successfully
//! - The parser budget mechanism works correctly for heavy inputs

use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::parser::parse_html;

/// [A06.9] Deep nesting (100+ levels) doesn't crash the parser.
///
/// Generates HTML with 200 levels of nested `<div>` elements and verifies
/// the parser handles it without panicking or stack overflow.
#[test]
fn test_parser_deep_nesting_no_crash() {
    let depth = 200;
    let mut html = String::with_capacity(depth * 12 + 100);
    html.push_str("<html><body>");
    for _ in 0..depth {
        html.push_str("<div>");
    }
    html.push_str("deeply nested content");
    for _ in 0..depth {
        html.push_str("</div>");
    }
    html.push_str("</body></html>");

    let result = parse_html(html.as_bytes());
    assert!(
        result.is_ok(),
        "Parser should handle {depth} levels of nesting without crashing"
    );

    // Also verify conversion succeeds
    let dom = result.unwrap();
    let converter = MarkdownConverter::new();
    let md = converter.convert(&dom);
    assert!(
        md.is_ok(),
        "Conversion of deeply nested HTML should succeed"
    );
    let markdown = md.unwrap();
    assert!(
        markdown.contains("deeply nested content"),
        "Converted output should contain the nested content"
    );
}

/// [A06.9] Very deep nesting (500+ levels) doesn't crash.
///
/// Tests an extreme nesting depth to verify no stack overflow occurs.
#[test]
fn test_parser_very_deep_nesting_500_levels() {
    let depth = 500;
    let mut html = String::with_capacity(depth * 12 + 100);
    html.push_str("<html><body>");
    for _ in 0..depth {
        html.push_str("<div>");
    }
    html.push_str("content at depth 500");
    for _ in 0..depth {
        html.push_str("</div>");
    }
    html.push_str("</body></html>");

    let result = parse_html(html.as_bytes());
    assert!(
        result.is_ok(),
        "Parser should handle {depth} levels of nesting"
    );
}

/// [A06.9] Many nodes (10000+ elements) completes without timeout.
///
/// Generates HTML with 10000 sibling `<p>` elements and verifies
/// parsing and conversion complete successfully.
#[test]
fn test_parser_many_nodes_10000_elements() {
    let node_count = 10_000;
    let mut html = String::with_capacity(node_count * 30 + 100);
    html.push_str("<html><body>");
    for i in 0..node_count {
        html.push_str(&format!("<p>Paragraph {i}</p>\n"));
    }
    html.push_str("</body></html>");

    let result = parse_html(html.as_bytes());
    assert!(
        result.is_ok(),
        "Parser should handle {node_count} elements"
    );

    let dom = result.unwrap();
    let converter = MarkdownConverter::new();
    let md = converter.convert(&dom);
    assert!(
        md.is_ok(),
        "Conversion of {node_count} elements should succeed"
    );
    let markdown = md.unwrap();
    // Verify first and last paragraphs are present
    assert!(
        markdown.contains("Paragraph 0"),
        "Output should contain first paragraph"
    );
    assert!(
        markdown.contains(&format!("Paragraph {}", node_count - 1)),
        "Output should contain last paragraph"
    );
}

/// [A06.9] Many nodes with mixed structure (tables, lists, headings).
///
/// Tests a complex document with diverse element types to verify
/// the parser handles structural variety at scale.
#[test]
fn test_parser_many_nodes_mixed_structure() {
    let mut html = String::with_capacity(200_000);
    html.push_str("<html><body>");

    // Add headings
    for i in 0..100 {
        html.push_str(&format!("<h2>Section {i}</h2>\n"));
        // Add a list under each heading
        html.push_str("<ul>\n");
        for j in 0..10 {
            html.push_str(&format!("<li>Item {i}.{j}</li>\n"));
        }
        html.push_str("</ul>\n");
    }

    // Add a large table
    html.push_str("<table><thead><tr><th>Col1</th><th>Col2</th><th>Col3</th></tr></thead><tbody>\n");
    for i in 0..500 {
        html.push_str(&format!(
            "<tr><td>Row {i}</td><td>Data</td><td>Value</td></tr>\n"
        ));
    }
    html.push_str("</tbody></table>\n");

    html.push_str("</body></html>");

    let result = parse_html(html.as_bytes());
    assert!(
        result.is_ok(),
        "Parser should handle complex mixed-structure document"
    );

    let dom = result.unwrap();
    let converter = MarkdownConverter::new();
    let md = converter.convert(&dom);
    assert!(
        md.is_ok(),
        "Conversion of mixed-structure document should succeed"
    );
}

/// [A06.9] ParseBudgetExceeded error code maps correctly.
///
/// Verifies the error type and FFI code for parse budget exceeded.
#[test]
fn test_parse_budget_exceeded_error_code() {
    use nginx_markdown_converter::ffi::ERROR_PARSE_BUDGET_EXCEEDED;

    let err = ConversionError::ParseBudgetExceeded {
        used: 128_000_000,
        limit: 64_000_000,
    };
    assert_eq!(err.code(), ERROR_PARSE_BUDGET_EXCEEDED);
    assert_eq!(err.code(), 11);
}

/// [A06.9] ParseTimeout error code maps correctly.
///
/// Verifies the error type and FFI code for parse timeout.
#[test]
fn test_parse_timeout_error_code() {
    use nginx_markdown_converter::ffi::ERROR_PARSE_TIMEOUT;

    let err = ConversionError::ParseTimeout;
    assert_eq!(err.code(), ERROR_PARSE_TIMEOUT);
    assert_eq!(err.code(), 10);
}

/// [A06.9] Deep nesting with inline elements doesn't crash.
///
/// Tests deeply nested inline elements (spans, strongs, ems) which
/// exercise different parser code paths than block elements.
#[test]
fn test_parser_deep_inline_nesting() {
    let depth = 150;
    let mut html = String::with_capacity(depth * 20 + 100);
    html.push_str("<html><body><p>");
    for _ in 0..depth {
        html.push_str("<span><strong><em>");
    }
    html.push_str("deeply nested inline");
    for _ in 0..depth {
        html.push_str("</em></strong></span>");
    }
    html.push_str("</p></body></html>");

    let result = parse_html(html.as_bytes());
    assert!(
        result.is_ok(),
        "Parser should handle deeply nested inline elements"
    );
}

/// [A06.9] Large document (>1MB) parses without issue.
///
/// Generates a document exceeding 1MB to verify the parser handles
/// large inputs within reasonable time.
#[test]
fn test_parser_large_document_over_1mb() {
    let target_size = 1_100_000; // ~1.1 MB
    let mut html = String::with_capacity(target_size + 1000);
    html.push_str("<html><body>");

    let paragraph = "<p>This is a paragraph with enough text to contribute meaningfully to the document size. It contains various words and phrases that make it realistic.</p>\n";
    while html.len() < target_size {
        html.push_str(paragraph);
    }

    html.push_str("</body></html>");

    assert!(
        html.len() > 1_000_000,
        "Document should be >1MB, got {} bytes",
        html.len()
    );

    let result = parse_html(html.as_bytes());
    assert!(
        result.is_ok(),
        "Parser should handle documents >1MB"
    );
}
