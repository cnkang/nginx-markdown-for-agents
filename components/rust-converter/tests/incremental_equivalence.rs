//! Incremental / full-path equivalence tests.
//!
//! **Validates: Requirements 15.5, 17.5**
//!
//! Property 5 from the design document states:
//!
//! > *For all* valid HTML inputs and identical `ConversionOptions`,
//! > `IncrementalConverter` (single `feed_chunk` of the full data +
//! > `finalize`) SHALL produce byte-identical output to
//! > `MarkdownConverter::convert()`.
//!
//! This file contains:
//! - A proptest property that generates random HTML documents and asserts
//!   byte-equivalence between the two conversion paths.
//! - Deterministic edge-case tests (empty input, single element, large
//!   nested structure, special characters).

#![cfg(feature = "incremental")]

use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter};
use nginx_markdown_converter::incremental::IncrementalConverter;
use nginx_markdown_converter::parser::parse_html;
use proptest::prelude::*;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Convert HTML bytes via the full (batch) path.
fn convert_full(html: &[u8]) -> String {
    let dom = parse_html(html).expect("full-path: parse_html failed");
    MarkdownConverter::with_options(ConversionOptions::default())
        .convert(&dom)
        .expect("full-path: convert failed")
}

/// Convert HTML bytes via the incremental path (single feed_chunk + finalize).
fn convert_incremental(html: &[u8]) -> String {
    let mut conv = IncrementalConverter::new(ConversionOptions::default());
    conv.feed_chunk(html).expect("incremental: feed_chunk failed");
    conv.finalize().expect("incremental: finalize failed")
}

// ---------------------------------------------------------------------------
// Proptest strategies
// ---------------------------------------------------------------------------

/// Generate a random inline text string (ASCII letters, digits, spaces).
fn arb_text() -> impl Strategy<Value = String> {
    "[A-Za-z0-9 ]{0,40}"
}

/// Generate a random href URL.
fn arb_href() -> impl Strategy<Value = String> {
    prop::sample::select(vec![
        "https://example.com".to_string(),
        "https://example.com/page".to_string(),
        "/relative/path".to_string(),
        "#anchor".to_string(),
    ])
}

/// Generate a single random HTML element string.
fn arb_element() -> impl Strategy<Value = String> {
    let heading = (1u8..=6u8, arb_text()).prop_map(|(level, text)| {
        format!("<h{level}>{text}</h{level}>")
    });

    let paragraph = arb_text().prop_map(|t| format!("<p>{t}</p>"));

    let link = (arb_text(), arb_href()).prop_map(|(text, href)| {
        format!("<a href=\"{href}\">{text}</a>")
    });

    let unordered_list = prop::collection::vec(arb_text(), 1..=4).prop_map(|items| {
        let lis: String = items.iter().map(|i| format!("<li>{i}</li>")).collect();
        format!("<ul>{lis}</ul>")
    });

    let ordered_list = prop::collection::vec(arb_text(), 1..=4).prop_map(|items| {
        let lis: String = items.iter().map(|i| format!("<li>{i}</li>")).collect();
        format!("<ol>{lis}</ol>")
    });

    let code_inline = arb_text().prop_map(|t| format!("<code>{t}</code>"));

    let code_block = arb_text().prop_map(|t| format!("<pre><code>{t}</code></pre>"));

    let table = (arb_text(), arb_text(), arb_text(), arb_text()).prop_map(
        |(h1, h2, c1, c2)| {
            format!(
                "<table><thead><tr><th>{h1}</th><th>{h2}</th></tr></thead>\
                 <tbody><tr><td>{c1}</td><td>{c2}</td></tr></tbody></table>"
            )
        },
    );

    let bold = arb_text().prop_map(|t| format!("<strong>{t}</strong>"));
    let italic = arb_text().prop_map(|t| format!("<em>{t}</em>"));
    let blockquote = arb_text().prop_map(|t| format!("<blockquote><p>{t}</p></blockquote>"));
    let hr = Just("<hr>".to_string());
    let br = arb_text().prop_map(|t| format!("<p>{t}<br>more</p>"));
    let img = Just("<img src=\"https://example.com/img.png\" alt=\"image\">".to_string());

    prop_oneof![
        heading,
        paragraph,
        link,
        unordered_list,
        ordered_list,
        code_inline,
        code_block,
        table,
        bold,
        italic,
        blockquote,
        hr,
        br,
        img,
    ]
}

/// Generate a complete random HTML document composed of 1-8 random elements.
fn arb_html_document() -> impl Strategy<Value = String> {
    prop::collection::vec(arb_element(), 1..=8).prop_map(|elements| {
        let body: String = elements.join("\n");
        format!(
            "<!DOCTYPE html><html><head><title>Test</title></head><body>{body}</body></html>"
        )
    })
}

// ---------------------------------------------------------------------------
// Property test
// ---------------------------------------------------------------------------

proptest! {
    #![proptest_config(ProptestConfig::with_cases(256))]

    /// **Property 5: Incremental path / full path output equivalence**
    ///
    /// **Validates: Requirements 15.5, 17.5**
    ///
    /// For any randomly generated HTML document, converting via the full
    /// path (`parse_html` + `MarkdownConverter::convert`) must produce
    /// byte-identical output to converting via the incremental path
    /// (`IncrementalConverter::new` + single `feed_chunk` + `finalize`).
    #[test]
    fn prop_incremental_full_path_equivalence(html in arb_html_document()) {
        let html_bytes = html.as_bytes();
        let full_output = convert_full(html_bytes);
        let incr_output = convert_incremental(html_bytes);

        prop_assert_eq!(
            full_output,
            incr_output,
            "Incremental and full-path outputs must be byte-identical.\n\
             Input HTML ({} bytes):\n{}\n",
            html_bytes.len(),
            html,
        );
    }
}

// ---------------------------------------------------------------------------
// Deterministic edge-case tests
// ---------------------------------------------------------------------------

#[test]
fn test_equivalence_empty_input() {
    // Both paths should reject empty input with an error.
    let full_result = parse_html(b"");
    assert!(full_result.is_err(), "full path should reject empty input");
    let full_err = full_result.err().unwrap();

    let mut conv = IncrementalConverter::new(ConversionOptions::default());
    conv.feed_chunk(b"").unwrap(); // empty feed is a no-op
    let incr_err = conv.finalize().unwrap_err();

    assert_eq!(
        full_err.code(),
        incr_err.code(),
        "Empty input must produce the same error code from both paths"
    );
}

#[test]
fn test_equivalence_single_heading() {
    let html = b"<h1>Hello World</h1>";
    let full = convert_full(html);
    let incr = convert_incremental(html);
    assert_eq!(full, incr, "Single heading must produce identical output");
}

#[test]
fn test_equivalence_single_paragraph() {
    let html = b"<p>A simple paragraph.</p>";
    let full = convert_full(html);
    let incr = convert_incremental(html);
    assert_eq!(full, incr, "Single paragraph must produce identical output");
}

#[test]
fn test_equivalence_large_nested_structure() {
    // Build a deeply nested HTML document with many elements.
    let mut body = String::new();
    for i in 0..50 {
        body.push_str(&format!(
            "<div><h2>Section {i}</h2><p>Paragraph {i} content.</p>\
             <ul><li>Item A{i}</li><li>Item B{i}</li></ul></div>"
        ));
    }
    let html = format!(
        "<!DOCTYPE html><html><head><title>Large</title></head><body>{body}</body></html>"
    );
    let html_bytes = html.as_bytes();

    let full = convert_full(html_bytes);
    let incr = convert_incremental(html_bytes);
    assert_eq!(
        full, incr,
        "Large nested structure must produce identical output"
    );
}

#[test]
fn test_equivalence_special_characters() {
    let html = b"<p>&lt;tag&gt; &amp; &quot;quotes&quot; &#39;apos&#39;</p>";
    let full = convert_full(html);
    let incr = convert_incremental(html);
    assert_eq!(
        full, incr,
        "HTML with special characters must produce identical output"
    );
}

#[test]
fn test_equivalence_unicode_content() {
    let html = "<p>中文内容 日本語 한국어 emoji: 🚀✨</p>".as_bytes();
    let full = convert_full(html);
    let incr = convert_incremental(html);
    assert_eq!(
        full, incr,
        "Unicode content must produce identical output"
    );
}

#[test]
fn test_equivalence_table() {
    let html = b"<table><thead><tr><th>Name</th><th>Value</th></tr></thead>\
                 <tbody><tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></tbody></table>";
    let full = convert_full(html);
    let incr = convert_incremental(html);
    assert_eq!(full, incr, "Table must produce identical output");
}

#[test]
fn test_equivalence_code_block() {
    let html = b"<pre><code>fn main() {\n    println!(\"hello\");\n}</code></pre>";
    let full = convert_full(html);
    let incr = convert_incremental(html);
    assert_eq!(full, incr, "Code block must produce identical output");
}

#[test]
fn test_equivalence_mixed_inline() {
    let html = b"<p>Normal <strong>bold</strong> and <em>italic</em> and <code>code</code></p>";
    let full = convert_full(html);
    let incr = convert_incremental(html);
    assert_eq!(
        full, incr,
        "Mixed inline elements must produce identical output"
    );
}
