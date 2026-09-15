//! Differential coverage for GitHub Flavored Markdown constructs.
//!
//! Only the full-buffer engine rendered these constructs, so the same document
//! could gain GFM markers under one processing path and lose them under the
//! other.  These cases run the SAME HTML through both engines with the SAME
//! GFM options and require the same markers.

#![cfg(feature = "streaming")]

#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use nginx_markdown_converter::converter::{ConversionOptions, MarkdownFlavor};
use streaming_test_support::{
    convert_full_buffer, convert_streaming_chunked, convert_streaming_single,
    default_streaming_budget, default_streaming_options,
};

fn gfm_options() -> ConversionOptions {
    let mut options = default_streaming_options();
    options.flavor = MarkdownFlavor::GitHubFlavoredMarkdown;
    options
}

fn commonmark_options() -> ConversionOptions {
    default_streaming_options()
}

const STRIKETHROUGH: &[u8] = b"<html><body>\
<p>Keep <del>removed</del> and <s>also</s> and <strike>third</strike> here.</p>\
</body></html>";

fn engines(body: &[u8], options: ConversionOptions) -> (String, String, String) {
    let full = convert_full_buffer(body, Some("text/html"), options.clone())
        .expect("full-buffer conversion");

    let single = convert_streaming_single(
        body,
        Some("text/html"),
        options.clone(),
        default_streaming_budget(),
        None,
    )
    .expect("single-chunk streaming conversion");

    let split = body.len() / 2;
    let chunked = convert_streaming_chunked(
        body,
        &[split, body.len() - split],
        Some("text/html"),
        options,
        default_streaming_budget(),
        None,
    )
    .expect("chunked streaming conversion");

    (full, single.markdown, chunked.markdown)
}

#[test]
fn gfm_strikethrough_markers_match_across_engines() {
    let (full, single, chunked) = engines(STRIKETHROUGH, gfm_options());

    for (label, markdown) in [
        ("full-buffer", &full),
        ("streaming", &single),
        ("chunked", &chunked),
    ] {
        assert!(markdown.contains("~~removed~~"), "{label}: {markdown}");
        assert!(markdown.contains("~~also~~"), "{label}: {markdown}");
        assert!(markdown.contains("~~third~~"), "{label}: {markdown}");
    }

    assert_eq!(
        single, chunked,
        "chunk boundaries must not change the output"
    );
    assert_eq!(
        single.split_whitespace().collect::<Vec<_>>(),
        full.split_whitespace().collect::<Vec<_>>(),
        "the two engines must agree on the Markdown text"
    );
}

#[test]
fn commonmark_omits_strikethrough_markers_in_both_engines() {
    let (full, single, chunked) = engines(STRIKETHROUGH, commonmark_options());

    for (label, markdown) in [
        ("full-buffer", &full),
        ("streaming", &single),
        ("chunked", &chunked),
    ] {
        assert!(markdown.contains("removed"), "{label}: {markdown}");
        assert!(
            !markdown.contains("~~"),
            "{label} emitted GFM markers under CommonMark: {markdown}"
        );
    }
}

const TASK_LIST: &[u8] = b"<html><body>\
<ul><li><input type=\"checkbox\" checked> done</li>\
<li><input type=\"checkbox\"> todo</li>\
<li><input type=\"text\" value=\"ignored\"> plain</li></ul>\
</body></html>";

#[test]
fn gfm_task_list_markers_match_across_engines() {
    let (full, single, chunked) = engines(TASK_LIST, gfm_options());

    for (label, markdown) in [
        ("full-buffer", &full),
        ("streaming", &single),
        ("chunked", &chunked),
    ] {
        assert!(markdown.contains("[x] "), "{label}: {markdown}");
        assert!(markdown.contains("[ ] "), "{label}: {markdown}");
    }

    assert_eq!(
        single, chunked,
        "chunk boundaries must not change the output"
    );
}

#[test]
fn commonmark_omits_task_list_markers_in_both_engines() {
    let (full, single, chunked) = engines(TASK_LIST, commonmark_options());

    for (label, markdown) in [
        ("full-buffer", &full),
        ("streaming", &single),
        ("chunked", &chunked),
    ] {
        assert!(
            !markdown.contains("[x] ") && !markdown.contains("[ ] "),
            "{label} emitted GFM task markers under CommonMark: {markdown}"
        );
        assert!(markdown.contains("done"), "{label}: {markdown}");
    }
}
