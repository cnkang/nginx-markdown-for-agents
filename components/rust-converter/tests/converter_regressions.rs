//! Regression tests for the HTML-to-Markdown converter.
//!
//! Each test in this module addresses a specific bug or edge case that was
//! previously mishandled. They serve as permanent guards against regressions
//! in inline code fencing, whitespace handling, link formatting, and nested
//! list structure.
//!
//! When adding a new regression test, include a comment referencing the
//! original issue or commit that introduced the fix.

use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter, MarkdownFlavor};
use nginx_markdown_converter::parser::parse_html;

fn convert_html(html: &[u8]) -> String {
    convert_html_with_options(html, ConversionOptions::default())
}

fn convert_html_with_options(html: &[u8], options: ConversionOptions) -> String {
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::with_options(options);
    converter.convert(&dom).expect("Conversion failed")
}

#[test]
fn inline_code_should_use_a_fence_longer_than_embedded_backticks() {
    let result = convert_html(b"<p><code>value ``` with ticks</code></p>");

    assert!(result.trim().contains("````value ``` with ticks````"));
}

#[test]
fn whitespace_only_nodes_should_preserve_word_separation() {
    let result = convert_html(b"<p>Hello<span> </span>world</p>");

    assert!(result.contains("Hello world"));
    assert!(!result.contains("Helloworld"));
}

#[test]
fn link_text_extraction_should_skip_removed_children() {
    let result = convert_html(
        b"<p><a href=\"https://example.com\">safe<script>alert(1)</script> text</a></p>",
    );

    assert!(result.contains("[safe text](https://example.com)"));
    assert!(!result.contains("alert"));
}

/// Headings nested inside container elements (`<div>`, `<section>`, `<article>`)
/// must preserve their Markdown level regardless of container nesting depth.
/// Regression guard for Requirement 1.4 (semantic fidelity spec).
#[test]
fn headings_inside_containers_preserve_level() {
    // Heading inside <div>
    let result = convert_html(b"<div><h1>Div Title</h1></div>");
    assert!(
        result.contains("# Div Title"),
        "h1 inside <div> should produce '# ': {result:?}"
    );

    // Heading inside <section>
    let result = convert_html(b"<section><h2>Section Title</h2></section>");
    assert!(
        result.contains("## Section Title"),
        "h2 inside <section> should produce '## ': {result:?}"
    );

    // Heading inside <article>
    let result = convert_html(b"<article><h3>Article Title</h3></article>");
    assert!(
        result.contains("### Article Title"),
        "h3 inside <article> should produce '### ': {result:?}"
    );

    // Deeply nested containers
    let result =
        convert_html(b"<div><section><article><h4>Deep Title</h4></article></section></div>");
    assert!(
        result.contains("#### Deep Title"),
        "h4 inside nested containers should produce '#### ': {result:?}"
    );
}

#[test]
fn nested_lists_should_not_double_indent_pre_rendered_children() {
    let result = convert_html_with_options(
        b"<ul><li>Parent<ul><li>Child</li></ul></li></ul>",
        ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        },
    );

    assert!(result.contains("\n  - Child"));
    assert!(!result.contains("\n    - Child"));
}

#[test]
fn link_url_with_angle_brackets_percent_encodes_both() {
    // Regression: URLs containing literal '<' or '>' must be
    // percent-encoded when wrapped in angle-bracket destinations.
    // Previously only '>' was encoded, leaving '<' to break the
    // angle-bracket destination syntax.
    let result = convert_html(br#"<a href="https://example.com/path?a=1&lt;b=2&gt;c=3">link</a>"#);
    // Both %3C (for <) and %3E (for >) must appear in the output
    assert!(
        result.contains("%3C"),
        "expected %3C for literal '<' in URL, got: {result}"
    );
    assert!(
        result.contains("%3E"),
        "expected %3E for literal '>' in URL, got: {result}"
    );
    // The destination must be wrapped in angle brackets
    assert!(
        result.contains("<https://") || result.contains("<http://"),
        "expected angle-bracket destination wrapping, got: {result}"
    );
}
