//! Integration tests for ETag generation with Markdown conversion
//!
//! These tests verify that the ETag generator works correctly with the
//! Markdown converter to produce consistent ETags for identical HTML input.

use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::etag_generator::ETagGenerator;
use nginx_markdown_converter::parser::parse_html;

#[test]
fn test_etag_consistency_with_conversion() {
    let html = b"<html><body><h1>Hello World</h1><p>Test content</p></body></html>";

    // Convert HTML to Markdown twice
    let dom1 = parse_html(html).expect("Failed to parse HTML");
    let converter1 = MarkdownConverter::new();
    let markdown1 = converter1.convert(&dom1).expect("Failed to convert");

    let dom2 = parse_html(html).expect("Failed to parse HTML");
    let converter2 = MarkdownConverter::new();
    let markdown2 = converter2.convert(&dom2).expect("Failed to convert");

    // Verify Markdown is identical
    assert_eq!(
        markdown1, markdown2,
        "Markdown output should be deterministic"
    );

    // Generate ETags
    let generator = ETagGenerator::new();
    let etag1 = generator.generate(markdown1.as_bytes());
    let etag2 = generator.generate(markdown2.as_bytes());

    // Verify ETags are identical
    assert_eq!(
        etag1, etag2,
        "ETags should be consistent for identical Markdown"
    );
}

#[test]
fn test_etag_differentiation_with_different_html() {
    let html1 = b"<html><body><h1>Hello World</h1></body></html>";
    let html2 = b"<html><body><h1>Goodbye World</h1></body></html>";

    // Convert both HTML documents
    let dom1 = parse_html(html1).expect("Failed to parse HTML 1");
    let converter = MarkdownConverter::new();
    let markdown1 = converter.convert(&dom1).expect("Failed to convert 1");

    let dom2 = parse_html(html2).expect("Failed to parse HTML 2");
    let markdown2 = converter.convert(&dom2).expect("Failed to convert 2");

    // Verify Markdown is different
    assert_ne!(
        markdown1, markdown2,
        "Different HTML should produce different Markdown"
    );

    // Generate ETags
    let generator = ETagGenerator::new();
    let etag1 = generator.generate(markdown1.as_bytes());
    let etag2 = generator.generate(markdown2.as_bytes());

    // Verify ETags are different
    assert_ne!(
        etag1, etag2,
        "Different Markdown should produce different ETags"
    );
}

#[test]
fn test_etag_format_compliance() {
    let html = b"<html><body><p>Test</p></body></html>";

    let dom = parse_html(html).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    let generator = ETagGenerator::new();
    let etag = generator.generate(markdown.as_bytes());

    // Verify HTTP specification compliance
    assert!(etag.starts_with('"'), "ETag should start with quote");
    assert!(etag.ends_with('"'), "ETag should end with quote");
    assert_eq!(etag.len(), 34, "ETag should be 32 hex chars + 2 quotes");

    // Verify hex content
    let hex_part = &etag[1..etag.len() - 1];
    assert!(
        hex_part.chars().all(|c| c.is_ascii_hexdigit()),
        "ETag should contain only hex characters"
    );
}

#[test]
fn test_etag_with_complex_html() {
    let html = br#"
        <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Main Heading</h1>
            <p>Paragraph with <strong>bold</strong> and <em>italic</em> text.</p>
            <ul>
                <li>Item 1</li>
                <li>Item 2</li>
            </ul>
            <pre><code>code block</code></pre>
            <a href="https://example.com">Link</a>
        </body>
        </html>
    "#;

    let dom = parse_html(html).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    let generator = ETagGenerator::new();
    let etag = generator.generate(markdown.as_bytes());

    // Verify ETag is generated correctly for complex content
    assert!(etag.starts_with('"'));
    assert!(etag.ends_with('"'));
    assert_eq!(etag.len(), 34);

    // Verify consistency
    let etag2 = generator.generate(markdown.as_bytes());
    assert_eq!(etag, etag2);
}

#[test]
fn test_etag_with_unicode_html() {
    let html = "<html><body><p>Hello 世界 🌍</p></body></html>".as_bytes();

    let dom = parse_html(html).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    let generator = ETagGenerator::new();
    let etag = generator.generate(markdown.as_bytes());

    // Verify ETag handles Unicode correctly
    assert!(etag.starts_with('"'));
    assert!(etag.ends_with('"'));
    assert_eq!(etag.len(), 34);

    // Verify consistency with Unicode
    let etag2 = generator.generate(markdown.as_bytes());
    assert_eq!(etag, etag2);
}
