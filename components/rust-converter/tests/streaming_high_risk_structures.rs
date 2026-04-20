//! Tests for streaming conversion of HTML fixtures containing high-risk structures.
//!
//! Validates that the streaming converter correctly handles HTML constructs
//! known to be challenging for streaming processing, such as deeply nested
//! elements, large tables, complex blockquote structures, and mixed content
//! models. Each fixture in the streaming corpus with high-risk metadata is
//! converted via both single-chunk and chunked streaming, then compared
//! against full-buffer output. Fixtures marked as expecting fallback are
//! verified to return `StreamingFallback` errors.

#![cfg(feature = "streaming")]

#[path = "known_differences.rs"]
mod known_differences;
#[path = "support/streaming_compare_support.rs"]
mod streaming_compare_support;
#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use known_differences::KnownDifferences;
use nginx_markdown_converter::error::ConversionError;
use streaming_compare_support::compare_or_known;
use streaming_test_support::{
    convert_full_buffer, convert_streaming_chunked, convert_streaming_single,
    default_streaming_budget, default_streaming_options, known_differences_path, read_fixture,
    read_fixture_meta,
};

fn fixture_path(name: &str) -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join(format!("../../tests/corpus/streaming/{name}.html"))
}

fn assert_fixture(name: &str) {
    let path = fixture_path(name);
    let fixture_name = format!("streaming/{name}.html");
    let html = read_fixture(&path);
    let meta = read_fixture_meta(&path);
    let known = KnownDifferences::from_file(&known_differences_path())
        .unwrap_or_else(|err| panic!("load known differences: {err}"));

    let full = convert_full_buffer(
        &html,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
    )
    .unwrap_or_else(|err| panic!("full-buffer conversion failed for {fixture_name}: {err}"));

    let single = convert_streaming_single(
        &html,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        default_streaming_budget(),
        None,
    );

    if meta.expected_fallback {
        let err = single.unwrap_err();
        assert!(
            matches!(err, ConversionError::StreamingFallback { .. }),
            "{fixture_name}: expected StreamingFallback, got {err:?}"
        );
        return;
    }

    assert!(
        !meta.high_risk_structures.is_empty(),
        "{fixture_name}: high_risk_structures metadata should not be empty"
    );

    let single =
        single.unwrap_or_else(|err| panic!("{fixture_name}: single streaming failed: {err}"));
    compare_or_known(&fixture_name, &full, &single.markdown, &known)
        .unwrap_or_else(|err| panic!("{err}"));

    let first = html.len().max(1) / 2;
    let second = html.len().max(1).saturating_sub(first);
    let chunks = vec![first, second];
    let chunked = convert_streaming_chunked(
        &html,
        &chunks,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        default_streaming_budget(),
        None,
    )
    .unwrap_or_else(|err| panic!("{fixture_name}: chunked streaming failed: {err}"));

    compare_or_known(&fixture_name, &full, &chunked.markdown, &known)
        .unwrap_or_else(|err| panic!("{err}"));
}

#[test]
fn high_risk_tables_regression() {
    assert_fixture("high-risk-tables");
}

#[test]
fn nested_lists_regression() {
    assert_fixture("nested-lists-deep");
}

#[test]
fn code_blocks_regression() {
    assert_fixture("code-blocks-mixed");
}

#[test]
fn inline_spacing_regression() {
    assert_fixture("inline-spacing");
}

#[test]
fn normalization_regression() {
    assert_fixture("normalization-crlf");
}

#[test]
fn front_matter_regression() {
    assert_fixture("front-matter-metadata");
}

#[test]
fn blockquote_regression() {
    assert_fixture("blockquote-nested");
}
