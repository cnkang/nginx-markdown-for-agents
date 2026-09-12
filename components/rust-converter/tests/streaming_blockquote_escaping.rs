//! Escaping of literal block markers inside blockquotes (streaming engine).
//!
//! The emitter writes the `> ` prefix itself.  Feeding those characters
//! through the ordinary text escape state cleared the line-prefix flag, so a
//! literal `- `, `# `, or `1. ` that followed became a list item or a heading
//! in the generated Markdown while the full-buffer engine escaped it.  These
//! cases pin the escaped output and the agreement with the full-buffer engine.

#![cfg(feature = "streaming")]

#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use streaming_test_support::{
    convert_full_buffer, convert_streaming_single, default_streaming_budget,
    default_streaming_options,
};

/// (HTML body inside a blockquote, the escape the streaming engine must emit)
const CASES: &[(&str, &str)] = &[
    ("- literal dash text", "> \\- literal dash text"),
    ("# literal hash", "> \\# literal hash"),
    ("1. literal ordered", "> 1\\. literal ordered"),
    ("* literal star", "> \\* literal star"),
    ("+ literal plus", "> \\+ literal plus"),
    ("= literal equals", "> \\= literal equals"),
];

#[test]
fn literal_block_markers_inside_a_blockquote_stay_text() {
    for (body, expected) in CASES {
        let html = format!("<html><body><blockquote><p>{body}</p></blockquote></body></html>");

        let streamed = convert_streaming_single(
            html.as_bytes(),
            Some("text/html"),
            default_streaming_options(),
            default_streaming_budget(),
            None,
        )
        .expect("streaming conversion");

        assert!(
            streamed.markdown.contains(expected),
            "streaming output must escape the literal marker: expected {expected:?}\n--- output ---\n{}",
            streamed.markdown
        );

        // The full-buffer engine escapes the same characters, so the escaped
        // text must agree between the two engines.
        let full = convert_full_buffer(
            html.as_bytes(),
            Some("text/html"),
            default_streaming_options(),
        )
        .expect("full-buffer conversion");

        let text = expected.trim_start_matches("> ");
        assert!(
            full.contains(text),
            "full-buffer output must escape the same marker: expected {text:?}\n--- output ---\n{full}"
        );
    }
}

#[test]
fn nested_blockquotes_still_escape_literal_markers() {
    let html = "<html><body><blockquote><blockquote><p>- literal</p></blockquote></blockquote></body></html>";
    let streamed = convert_streaming_single(
        html.as_bytes(),
        Some("text/html"),
        default_streaming_options(),
        default_streaming_budget(),
        None,
    )
    .expect("streaming conversion");

    assert!(
        streamed.markdown.contains("\\- literal"),
        "nested blockquote must still escape the literal marker\n--- output ---\n{}",
        streamed.markdown
    );
}
