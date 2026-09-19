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

/// Tables are the documented streaming divergence point.  The streaming engine
/// does **not** render them inline: it signals `StreamingFallback
/// { reason: TableDetected }` before committing output, and the caller
/// re-converts the request through the full-buffer engine (the C layer's
/// `fallback_to_fullbuffer` path).  The parity contract is therefore not
/// "both engines emit a pipe table" — it is "streaming refuses to commit and
/// the full-buffer engine that receives the same bytes does emit one".
const TABLE: &[u8] = b"<html><body>\
<table><thead><tr><th>Name</th><th>Value</th></tr></thead>\
<tbody><tr><td>alpha</td><td>1</td></tr>\
<tr><td>beta</td><td>2</td></tr></tbody></table>\
</body></html>";

/// Shared assertion: the fallback engine's output is a GFM pipe table whose
/// cells and delimiter row both survive.
fn assert_gfm_pipe_table(label: &str, markdown: &str) {
    assert!(
        markdown.contains("Name") && markdown.contains("Value"),
        "{label} must render the header cells: {markdown}"
    );
    assert!(
        markdown.contains("alpha") && markdown.contains("beta"),
        "{label} must render the body cells: {markdown}"
    );
    assert!(
        markdown.contains('|'),
        "{label} must emit pipe-table separators: {markdown}"
    );
    assert!(
        markdown.lines().any(|line| {
            let trimmed = line.trim();
            trimmed.starts_with('|')
                && trimmed.contains("---")
                && trimmed
                    .chars()
                    .all(|ch| matches!(ch, '|' | '-' | ':' | ' '))
        }),
        "{label} must emit a GFM delimiter row: {markdown}"
    );
}

#[test]
fn gfm_table_streaming_signals_fallback_and_full_buffer_renders_it() {
    /* The full-buffer engine is where a table is meant to be rendered. */
    let full = convert_full_buffer(TABLE, Some("text/html"), gfm_options())
        .expect("full-buffer conversion of a table must succeed");
    assert_gfm_pipe_table("full-buffer", &full);

    /* The streaming engine must refuse to commit rather than emit a
     * half-rendered table. */
    for chunking in ["single", "chunked"] {
        let outcome = if chunking == "single" {
            convert_streaming_single(
                TABLE,
                Some("text/html"),
                gfm_options(),
                default_streaming_budget(),
                None,
            )
        } else {
            let split = TABLE.len() / 2;
            convert_streaming_chunked(
                TABLE,
                &[split, TABLE.len() - split],
                Some("text/html"),
                gfm_options(),
                default_streaming_budget(),
                None,
            )
        };

        match outcome {
            Err(nginx_markdown_converter::error::ConversionError::StreamingFallback { reason }) => {
                assert!(
                    matches!(
                        reason,
                        nginx_markdown_converter::streaming::types::FallbackReason::TableDetected
                    ),
                    "{chunking}: table fallback must name the reason, got {reason:?}"
                );
            }
            Ok(result) => panic!(
                "{chunking}: streaming must signal fallback for a table instead of \
                 committing output; got markdown {:?}",
                result.markdown
            ),
            Err(other) => {
                panic!("{chunking}: expected StreamingFallback(TableDetected), got {other:?}")
            }
        }
    }
}

/// The fallback the streaming engine signals must be actionable: feeding the
/// same bytes to the full-buffer engine (what the C layer does on
/// `ERROR_STREAMING_FALLBACK`) yields the table the streaming path declined to
/// emit, so the client never loses the content.
#[test]
fn gfm_table_fallback_path_preserves_content() {
    let full = convert_full_buffer(TABLE, Some("text/html"), gfm_options())
        .expect("fallback engine must convert the table");

    assert!(
        full.contains("alpha") && full.contains("beta"),
        "the fallback output must keep every cell: {full}"
    );
    assert!(
        !full.is_empty() && full.contains('|'),
        "the fallback output must be a pipe table: {full}"
    );
}

#[test]
fn commonmark_table_fallback_matches_gfm_fallback_path() {
    let gfm = convert_full_buffer(TABLE, Some("text/html"), gfm_options())
        .expect("gfm full-buffer conversion");
    let commonmark = convert_full_buffer(TABLE, Some("text/html"), commonmark_options())
        .expect("commonmark full-buffer conversion");

    for (label, markdown) in [("gfm", &gfm), ("commonmark", &commonmark)] {
        assert!(
            markdown.contains("alpha") && markdown.contains("beta"),
            "{label} must not drop the cell text: {markdown}"
        );
    }
}

/// Autolinks: a bare absolute URL in text becomes a Markdown autolink in GFM.
/// Both engines must agree on whether the link is wrapped, since a divergence
/// here changes the rendered destination for the same input document.
const AUTOLINK: &[u8] = b"<html><body>\
<p>See https://example.com/docs for details.</p>\
<p>Also http://example.org/page?q=1 here.</p>\
</body></html>";

#[test]
fn gfm_autolinks_match_across_engines() {
    let (full, single, chunked) = engines(AUTOLINK, gfm_options());

    for (label, markdown) in [
        ("full-buffer", &full),
        ("streaming", &single),
        ("chunked", &chunked),
    ] {
        assert!(
            markdown.contains("https://example.com/docs"),
            "{label} must preserve the bare URL text: {markdown}"
        );
        assert!(
            markdown.contains("http://example.org/page?q=1"),
            "{label} must preserve the second bare URL: {markdown}"
        );
        /* Both engines must treat the bare URL the same way: either both
         * autolink it or neither does. */
        let autolinked = markdown.contains("<https://example.com/docs>");
        let plain = markdown.contains("See https://example.com/docs for");
        assert!(
            autolinked != plain,
            "{label} must be exactly one of autolinked or plain: {markdown}"
        );
    }

    assert_eq!(
        single, chunked,
        "chunk boundaries must not change the autolink output"
    );
    assert_eq!(
        full.matches("https://example.com/docs").count(),
        1,
        "the URL must be emitted exactly once by the full-buffer engine: {full}"
    );
}

#[test]
fn autolink_handling_agrees_between_engines() {
    let (full, single, _chunked) = engines(AUTOLINK, gfm_options());

    assert_eq!(
        full.contains("<https://example.com/docs>"),
        single.contains("<https://example.com/docs>"),
        "the two engines must agree on autolink wrapping \
         (full={full:?} streaming={single:?})"
    );
}
