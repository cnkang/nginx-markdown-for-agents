//! Differential coverage for URL resolution across processing paths.
//!
//! The corpus parity harness never installed a base URL, so a relative
//! reference could resolve in the full-buffer engine and stay untouched in the
//! streaming engine without any test noticing. These cases run the SAME HTML
//! through both engines with the SAME production-like options and compare the
//! emitted link/image destinations — the document text itself legitimately
//! differs by block spacing, but a destination must never depend on the
//! processing path.

#![cfg(feature = "streaming")]

#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use nginx_markdown_converter::converter::ConversionOptions;
use streaming_test_support::{
    convert_full_buffer, convert_streaming_chunked, convert_streaming_single,
    default_streaming_budget, default_streaming_options,
};

const BASE: &str = "https://example.com/docs/page.html";

const BODY: &str = concat!(
    "<html><head><title>t</title></head><body>",
    "<p><a href=\"../api\">up</a> ",
    "<a href=\"#section\">fragment</a> ",
    "<a href=\"?print=1\">query</a> ",
    "<a href=\"icons/logo.svg\">relative</a> ",
    "<a href=\"/hero.png\">root</a> ",
    "<a href=\"//cdn.example.com/a.js\">protocol relative</a> ",
    "<a href=\"https://other.example/x\">absolute</a> ",
    "<a href=\"mailto:user@example.com\">mail</a></p>",
    "<p><img src=\"/hero.png\" alt=\"hero\">",
    "<img src=\"icons/logo.svg\" alt=\"logo\"></p>",
    "</body></html>",
);

/// The destinations every engine must emit for [`BODY`], in document order.
const EXPECTED: &[&str] = &[
    "https://example.com/api",
    "https://example.com/docs/page.html#section",
    "https://example.com/docs/page.html?print=1",
    "https://example.com/docs/icons/logo.svg",
    "https://example.com/hero.png",
    "//cdn.example.com/a.js",
    "https://other.example/x",
    "mailto:user@example.com",
    "https://example.com/hero.png",
    "https://example.com/docs/icons/logo.svg",
];

/// Collect the destinations of every `[text](destination)` and `![alt](source)`.
fn destinations(markdown: &str) -> Vec<String> {
    let mut found = Vec::new();
    let mut rest = markdown;

    while let Some(start) = rest.find("](") {
        let after = &rest[start + 2..];
        if let Some(end) = after.find(')') {
            found.push(after[..end].to_string());
            rest = &after[end + 1..];
        } else {
            break;
        }
    }

    found
}

fn options_with_base() -> ConversionOptions {
    let mut options = default_streaming_options();
    options.base_url = Some(BASE.to_string());
    options.resolve_relative_urls = true;
    options
}

#[test]
fn body_urls_resolve_identically_in_both_engines() {
    let options = options_with_base();

    let full = convert_full_buffer(BODY.as_bytes(), Some("text/html"), options.clone())
        .expect("full-buffer conversion");

    let single = convert_streaming_single(
        BODY.as_bytes(),
        Some("text/html"),
        options.clone(),
        default_streaming_budget(),
        None,
    )
    .expect("single-chunk streaming conversion");

    let split = BODY.len() / 2;
    let chunked = convert_streaming_chunked(
        BODY.as_bytes(),
        &[split, BODY.len() - split],
        Some("text/html"),
        options,
        default_streaming_budget(),
        None,
    )
    .expect("chunked streaming conversion");

    // Both engines must emit exactly the resolved destinations, in order: a
    // missing resolution fails here instead of hiding behind an
    // equal-but-unresolved pair of outputs.
    for (label, markdown) in [
        ("full-buffer", full.as_str()),
        ("streaming", single.markdown.as_str()),
        ("streaming-chunked", chunked.markdown.as_str()),
    ] {
        assert_eq!(
            destinations(markdown),
            EXPECTED,
            "{label} emitted unexpected link/image destinations\n--- output ---\n{markdown}"
        );
    }
}

#[test]
fn relative_references_stay_untouched_without_a_base() {
    // Without a configured base the engines must still agree, and must not
    // invent an absolute URL from nothing.
    let options = default_streaming_options();

    let full = convert_full_buffer(BODY.as_bytes(), Some("text/html"), options.clone())
        .expect("full-buffer conversion");
    let single = convert_streaming_single(
        BODY.as_bytes(),
        Some("text/html"),
        options,
        default_streaming_budget(),
        None,
    )
    .expect("single-chunk streaming conversion");

    let expected = vec![
        "../api",
        "#section",
        "?print=1",
        "icons/logo.svg",
        "/hero.png",
        "//cdn.example.com/a.js",
        "https://other.example/x",
        "mailto:user@example.com",
        "/hero.png",
        "icons/logo.svg",
    ];

    assert_eq!(destinations(&full), expected);
    assert_eq!(destinations(&single.markdown), expected);
}
