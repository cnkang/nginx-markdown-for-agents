//! Security validation tests
//!
//! This test suite validates that the converter properly handles malicious HTML input
//! and prevents XSS, XXE, and SSRF attacks.
//!
//! # Requirements
//!
//! Validates: NFR-03.1, NFR-03.2, NFR-03.3, NFR-03.4

use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;

/// Test that script tags are completely removed from output
///
/// **Validates: Requirements NFR-03.4 (XSS Prevention)**
#[test]
fn test_xss_script_tag_removal() {
    let html = r#"<html><body>
        <p>Before dangerous element</p>
        <script>alert('xss')</script>
        <p>After dangerous element</p>
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // Script tag and its content should be completely removed
    assert!(!markdown.contains("<script"));
    assert!(!markdown.contains("</script"));
    assert!(!markdown.contains("alert"));
    assert!(!markdown.contains("xss"));

    // Normal content should be preserved
    assert!(markdown.contains("Before dangerous element"));
    assert!(markdown.contains("After dangerous element"));
}

/// Test that inline script tags are removed
///
/// **Validates: Requirements NFR-03.4 (XSS Prevention)**
#[test]
fn test_xss_inline_script_removal() {
    let html = r#"<p>Text <script>malicious()</script> more text</p>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    assert!(!markdown.contains("script"));
    assert!(!markdown.contains("malicious"));
    assert!(markdown.contains("Text"));
    assert!(markdown.contains("more text"));
}

/// Test that event handler attributes are removed
///
/// **Validates: Requirements NFR-03.4 (XSS Prevention)**
#[test]
fn test_xss_event_handler_removal() {
    let html = r#"<html><body>
        <p onclick="alert('xss')">Click me</p>
        <div onload="malicious()">Content</div>
        <a href="test.html" onmouseover="attack()">Link</a>
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // Event handlers should not appear in output
    assert!(!markdown.contains("onclick"));
    assert!(!markdown.contains("onload"));
    assert!(!markdown.contains("onmouseover"));
    assert!(!markdown.contains("alert"));
    assert!(!markdown.contains("malicious"));
    assert!(!markdown.contains("attack"));

    // Content should be preserved
    assert!(markdown.contains("Click me"));
    assert!(markdown.contains("Content"));
    assert!(markdown.contains("Link"));
}

/// Test that javascript: URLs are blocked in links
///
/// **Validates: Requirements NFR-03.4 (XSS Prevention)**
#[test]
fn test_xss_javascript_url_in_link() {
    let html = r#"<a href="javascript:alert('xss')">Click</a>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // javascript: URL should be blocked, link should be plain text
    assert!(!markdown.contains("javascript:"));
    assert!(!markdown.contains("alert"));
    assert!(markdown.contains("Click"));
    // Should not be a markdown link
    assert!(!markdown.contains("[Click]"));
}

/// Test that javascript: URLs are case-insensitive blocked
///
/// **Validates: Requirements NFR-03.4 (XSS Prevention)**
#[test]
fn test_xss_javascript_url_case_insensitive() {
    let test_cases = vec![
        r#"<a href="javascript:alert('xss')">Test1</a>"#,
        r#"<a href="JavaScript:alert('xss')">Test2</a>"#,
        r#"<a href="JAVASCRIPT:alert('xss')">Test3</a>"#,
        r#"<a href="JaVaScRiPt:alert('xss')">Test4</a>"#,
    ];

    for html in test_cases {
        let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
        let converter = MarkdownConverter::new();
        let markdown = converter.convert(&dom).expect("Failed to convert");

        assert!(!markdown.to_lowercase().contains("javascript:"));
        assert!(!markdown.contains("alert"));
    }
}

/// Test that data: URLs are blocked in links
///
/// **Validates: Requirements NFR-03.4 (XSS Prevention)**
#[test]
fn test_xss_data_url_in_link() {
    let html = r#"<a href="data:text/html,<script>alert('xss')</script>">Click</a>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // data: URL should be blocked
    assert!(!markdown.contains("data:"));
    assert!(!markdown.contains("script"));
    assert!(markdown.contains("Click"));
}

/// Test that javascript: URLs are blocked in images
///
/// **Validates: Requirements NFR-03.4 (XSS Prevention)**
#[test]
fn test_xss_javascript_url_in_image() {
    let html = r#"<img src="javascript:alert('xss')" alt="Image">"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // javascript: URL should be blocked, image should not appear
    assert!(!markdown.contains("javascript:"));
    assert!(!markdown.contains("alert"));
    assert!(!markdown.contains("![Image]"));
}

/// Test that data: URLs are blocked in images
///
/// **Validates: Requirements NFR-03.4 (XSS Prevention)**
#[test]
fn test_xss_data_url_in_image() {
    let html = r#"<img src="data:image/svg+xml,<svg onload='alert(1)'>" alt="SVG">"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // data: URL should be blocked
    assert!(!markdown.contains("data:"));
    assert!(!markdown.contains("onload"));
    assert!(!markdown.contains("![SVG]"));
}

/// Test that safe URLs are preserved
///
/// **Validates: Requirements NFR-03.4 (URL Sanitization)**
#[test]
fn test_safe_urls_preserved() {
    let html = r##"<html><body>
        <a href="https://example.com">HTTPS Link</a>
        <a href="http://example.com">HTTP Link</a>
        <a href="/relative/path">Relative Link</a>
        <a href="../parent">Parent Link</a>
        <a href="#anchor">Anchor Link</a>
        <img src="https://example.com/image.png" alt="Image">
    </body></html>"##;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // All safe URLs should be preserved
    assert!(markdown.contains("https://example.com"));
    assert!(markdown.contains("http://example.com"));
    assert!(markdown.contains("/relative/path"));
    assert!(markdown.contains("../parent"));
    assert!(markdown.contains("#anchor"));
    assert!(markdown.contains("https://example.com/image.png"));
}

/// Test that iframe elements are removed
///
/// **Validates: Requirements NFR-03.4 (SSRF Prevention)**
#[test]
fn test_ssrf_iframe_removal() {
    let html = r#"<html><body>
        <p>Before dangerous element</p>
        <iframe src="https://evil.com/malicious"></iframe>
        <p>After dangerous element</p>
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // iframe should be completely removed
    assert!(!markdown.contains("<iframe"));
    assert!(!markdown.contains("</iframe"));
    assert!(!markdown.contains("evil.com"));
    assert!(!markdown.contains("malicious"));

    // Normal content should be preserved
    assert!(markdown.contains("Before dangerous element"));
    assert!(markdown.contains("After dangerous element"));
}

/// Test that object elements are removed
///
/// **Validates: Requirements NFR-03.4 (SSRF Prevention)**
#[test]
fn test_ssrf_object_removal() {
    let html = r#"<html><body>
        <p>Content</p>
        <object data="https://evil.com/malicious.swf"></object>
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    assert!(!markdown.contains("object"));
    assert!(!markdown.contains("evil.com"));
    assert!(markdown.contains("Content"));
}

/// Test that embed elements are removed
///
/// **Validates: Requirements NFR-03.4 (SSRF Prevention)**
#[test]
fn test_ssrf_embed_removal() {
    let html = r#"<html><body>
        <p>Content</p>
        <embed src="https://evil.com/malicious.swf">
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    assert!(!markdown.contains("embed"));
    assert!(!markdown.contains("evil.com"));
    assert!(markdown.contains("Content"));
}

/// Test that file: URLs are blocked (SSRF prevention)
///
/// **Validates: Requirements NFR-03.4 (SSRF Prevention)**
#[test]
fn test_ssrf_file_url_blocked() {
    let html = r#"<a href="file:///etc/passwd">Local File</a>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    assert!(!markdown.contains("file:"));
    assert!(!markdown.contains("/etc/passwd"));
    assert!(markdown.contains("Local File"));
}

/// Test XXE prevention through DOCTYPE handling
///
/// html5ever does not process XML external entities by design (HTML5 spec).
/// This test documents that XXE attacks are prevented.
///
/// **Validates: Requirements NFR-03.4 (XXE Prevention)**
#[test]
fn test_xxe_prevention_doctype() {
    let html = r#"<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <html><body><p>&xxe;</p></body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // Entity reference should not be resolved
    // html5ever treats this as text content, not an entity
    assert!(!markdown.contains("/etc/passwd"));

    // The entity reference might appear as text or be ignored
    // Either way, it should not resolve to file contents
}

/// Test XXE prevention with external entity in DOCTYPE
///
/// **Validates: Requirements NFR-03.4 (XXE Prevention)**
#[test]
fn test_xxe_prevention_external_entity() {
    let html = r#"<!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://evil.com/malicious.dtd">]>
    <html><body><p>&xxe;</p></body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // External entity should not be fetched or resolved
    assert!(!markdown.contains("evil.com"));
    assert!(!markdown.contains("malicious"));
}

/// Test XXE prevention with parameter entities
///
/// Parameter entities are another XXE attack vector that should be prevented.
///
/// **Validates: Requirements NFR-03.4 (XXE Prevention)**
#[test]
fn test_xxe_prevention_parameter_entity() {
    let html = r#"<!DOCTYPE foo [
        <!ENTITY % xxe SYSTEM "file:///etc/passwd">
        <!ENTITY % dtd SYSTEM "http://evil.com/evil.dtd">
        %dtd;
    ]>
    <html><body><p>Content</p></body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // Parameter entities should not be resolved
    assert!(!markdown.contains("/etc/passwd"));
    assert!(!markdown.contains("evil.com"));
    assert!(!markdown.contains("evil.dtd"));
}

/// Test XXE prevention with internal entities
///
/// Internal entities defined in DOCTYPE should not be processed.
///
/// **Validates: Requirements NFR-03.4 (XXE Prevention)**
#[test]
fn test_xxe_prevention_internal_entity() {
    let html = r#"<!DOCTYPE foo [
        <!ENTITY internal "This is internal entity content">
    ]>
    <html><body><p>&internal;</p></body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // Internal entity should not be expanded
    // html5ever treats entity references as text, not as entity expansions
    // The entity reference might appear as text or be ignored
    assert!(
        !markdown.contains("This is internal entity content") || markdown.contains("&internal;")
    );
}

/// Test XXE prevention with nested entities
///
/// Nested entity definitions should not be processed.
///
/// **Validates: Requirements NFR-03.4 (XXE Prevention)**
#[test]
fn test_xxe_prevention_nested_entities() {
    let html = r#"<!DOCTYPE foo [
        <!ENTITY outer SYSTEM "file:///etc/passwd">
        <!ENTITY inner "&outer;">
    ]>
    <html><body><p>&inner;</p></body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // Nested entities should not be resolved
    assert!(!markdown.contains("/etc/passwd"));
}

/// Test DOCTYPE handling without entity declarations
///
/// Standard DOCTYPE declarations should be handled gracefully.
///
/// **Validates: Requirements NFR-03.4 (XXE Prevention)**
#[test]
fn test_doctype_standard_html5() {
    let html = r#"<!DOCTYPE html>
    <html><body><h1>Title</h1><p>Content</p></body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // Standard DOCTYPE should not cause issues
    assert!(markdown.contains("# Title"));
    assert!(markdown.contains("Content"));
}

/// Test DOCTYPE with PUBLIC identifier
///
/// DOCTYPE with PUBLIC identifier should be handled safely.
///
/// **Validates: Requirements NFR-03.4 (XXE Prevention)**
#[test]
fn test_doctype_public_identifier() {
    let html = r#"<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" 
        "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
    <html><body><p>Content</p></body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // PUBLIC DOCTYPE should not fetch external DTD
    assert!(!markdown.contains("w3.org"));
    assert!(markdown.contains("Content"));
}

/// Test that style tags are removed (CSS injection prevention)
///
/// **Validates: Requirements NFR-03.4 (Code Injection Prevention)**
#[test]
fn test_style_tag_removal() {
    let html = r#"<html><head>
        <style>body { background: url('javascript:alert(1)'); }</style>
    </head><body>
        <p>Content</p>
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    assert!(!markdown.contains("style"));
    assert!(!markdown.contains("background"));
    assert!(!markdown.contains("javascript:"));
    assert!(markdown.contains("Content"));
}

/// Test that link tags are removed (external stylesheet prevention)
///
/// **Validates: Requirements NFR-03.4 (SSRF Prevention)**
#[test]
fn test_link_tag_removal() {
    let html = r#"<html><head>
        <link rel="stylesheet" href="https://evil.com/malicious.css">
    </head><body>
        <p>Content</p>
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // link tag should be removed (it's in DANGEROUS_ELEMENTS)
    assert!(!markdown.contains("evil.com"));
    assert!(markdown.contains("Content"));
}

/// Test that base tags are removed (URL manipulation prevention)
///
/// **Validates: Requirements NFR-03.4 (Security)**
#[test]
fn test_base_tag_removal() {
    let html = r#"<html><head>
        <base href="https://evil.com/">
    </head><body>
        <p>Content</p>
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    assert!(!markdown.contains("base"));
    assert!(!markdown.contains("evil.com"));
    assert!(markdown.contains("Content"));
}

/// Test deeply nested HTML (stack overflow prevention)
///
/// **Validates: Requirements NFR-03.1 (Resource Protection)**
#[test]
fn test_deeply_nested_html() {
    // Create deeply nested HTML (but within limits)
    let mut html = String::from("<html><body>");
    for _ in 0..100 {
        html.push_str("<div>");
    }
    html.push_str("<p>Deep content</p>");
    for _ in 0..100 {
        html.push_str("</div>");
    }
    html.push_str("</body></html>");

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let result = converter.convert(&dom);

    // Should succeed for reasonable nesting depth
    assert!(result.is_ok());
    let markdown = result.unwrap();
    assert!(markdown.contains("Deep content"));
}

/// Test multiple XSS vectors in one document
///
/// **Validates: Requirements NFR-03.4 (Comprehensive XSS Prevention)**
#[test]
fn test_multiple_xss_vectors() {
    let html = r#"<html><body>
        <script>alert('xss1')</script>
        <p onclick="alert('xss2')">Click</p>
        <a href="javascript:alert('xss3')">Link</a>
        <img src="javascript:alert('xss4')" alt="Image">
        <iframe src="javascript:alert('xss5')"></iframe>
        <object data="javascript:alert('xss6')"></object>
        <embed src="javascript:alert('xss7')">
        <p>Safe content</p>
    </body></html>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // All XSS vectors should be blocked
    assert!(!markdown.contains("script"));
    assert!(!markdown.contains("onclick"));
    assert!(!markdown.contains("javascript:"));
    assert!(!markdown.contains("iframe"));
    assert!(!markdown.contains("object"));
    assert!(!markdown.contains("embed"));
    assert!(!markdown.contains("alert"));
    assert!(!markdown.contains("xss"));

    // Safe content should be preserved
    assert!(markdown.contains("Safe content"));
    assert!(markdown.contains("Click"));
}

/// Test that vbscript: URLs are blocked
///
/// **Validates: Requirements NFR-03.4 (XSS Prevention)**
#[test]
fn test_vbscript_url_blocked() {
    let html = r#"<a href="vbscript:msgbox('xss')">Click</a>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    assert!(!markdown.contains("vbscript:"));
    assert!(!markdown.contains("msgbox"));
    assert!(markdown.contains("Click"));
}

/// Test that about: URLs are blocked
///
/// **Validates: Requirements NFR-03.4 (Security)**
#[test]
fn test_about_url_blocked() {
    let html = r#"<a href="about:blank">About</a>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    assert!(!markdown.contains("about:"));
    assert!(markdown.contains("About"));
}

/// Test GFM mode with security (tables should work, but be sanitized)
///
/// **Validates: Requirements NFR-03.4 (Security in GFM mode)**
#[test]
fn test_gfm_security() {
    let html = r#"<table>
        <tr><th onclick="alert('xss')">Header</th></tr>
        <tr><td><a href="javascript:alert('xss')">Link</a></td></tr>
    </table>"#;

    let dom = parse_html(html.as_bytes()).expect("Failed to parse HTML");
    let converter = MarkdownConverter::new();
    let markdown = converter.convert(&dom).expect("Failed to convert");

    // Table should be converted (GFM)
    assert!(markdown.contains("Header"));

    // But security should still apply
    assert!(!markdown.contains("onclick"));
    assert!(!markdown.contains("javascript:"));
    assert!(!markdown.contains("alert"));
}
