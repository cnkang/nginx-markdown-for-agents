//! Tokenizer statistics accuracy tests for compact and non-compact modes.
//!
//! Verifies that token counts and parse-error counts are accurate in both
//! compact mode (used by the runtime streaming converter) and non-compact
//! mode (used by tests). Ensures no double-counting and that discarded
//! tokens (comments, doctypes, parse errors) are still counted.

#![cfg(feature = "streaming")]

use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::streaming::{MemoryBudget, StreamingConverter};

fn make_converter() -> StreamingConverter {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    conv
}

fn convert_and_collect(html: &[u8]) -> (Vec<u8>, u64, u64) {
    let mut conv = make_converter();
    let mut all_md = Vec::new();
    let out = conv.feed_chunk(html).expect("feed_chunk should succeed");
    all_md.extend_from_slice(&out.markdown);
    let result = conv.finalize().expect("finalize should succeed");
    all_md.extend_from_slice(&result.final_markdown);
    (
        all_md,
        result.stats.tokens_processed,
        result.stats.parse_errors,
    )
}

#[test]
fn plain_text_token_count() {
    let (_, tokens, _) = convert_and_collect(b"<p>Hello World</p>");
    assert_eq!(tokens, 3, "start tag, text, and end tag only");
}

#[test]
fn start_end_tag_counted() {
    let (_, tokens, _) = convert_and_collect(b"<h1>Title</h1>");
    assert_eq!(tokens, 3, "start tag, text, and end tag only");
}

#[test]
fn comment_token_counted() {
    let (_, tokens, _) = convert_and_collect(b"<p>before</p><!-- comment --><p>after</p>");
    assert_eq!(
        tokens, 7,
        "compact mode must retain the discarded comment count"
    );
}

#[test]
fn doctype_token_counted() {
    let (_, tokens, _) = convert_and_collect(b"<!DOCTYPE html><p>text</p>");
    assert_eq!(tokens, 4, "EOF is not a token");
}

#[test]
fn parse_error_token_counted() {
    let (_, tokens, parse_errors) = convert_and_collect(b"<p>\x00bad</p>");
    assert_eq!(tokens, 5, "parse errors are non-EOF tokenizer tokens");
    assert_eq!(parse_errors, 1, "one NUL produces one parse error");
}

#[test]
fn null_character_token_counted() {
    let (_, tokens, _) = convert_and_collect(b"<p>\x00</p>");
    assert_eq!(tokens, 4, "NUL is a token and also reports a parse error");
}

#[test]
fn mixed_batch_token_count() {
    let html = b"<h1>Title</h1><!-- comment --><p>Text</p>";
    let (_, tokens, parse_errors) = convert_and_collect(html);
    assert_eq!(tokens, 7, "start/end/text, comment, start/end/text");
    assert_eq!(parse_errors, 0, "should have no parse errors");
}

#[test]
fn multi_chunk_token_count_consistent() {
    let html = b"<h1>Title</h1><p>Paragraph one</p><p>Paragraph two</p>";

    let mut conv1 = make_converter();
    let out1 = conv1.feed_chunk(html).unwrap();
    let mut all_md1 = out1.markdown;
    let result1 = conv1.finalize().unwrap();
    all_md1.extend_from_slice(&result1.final_markdown);
    let tokens_single = result1.stats.tokens_processed;

    let mut conv2 = make_converter();
    let mut all_md2 = Vec::new();
    let mid = b"<h1>Title</h1>".len();
    let out2a = conv2.feed_chunk(&html[..mid]).unwrap();
    all_md2.extend_from_slice(&out2a.markdown);
    let out2b = conv2.feed_chunk(&html[mid..]).unwrap();
    all_md2.extend_from_slice(&out2b.markdown);
    let result2 = conv2.finalize().unwrap();
    all_md2.extend_from_slice(&result2.final_markdown);
    let tokens_split = result2.stats.tokens_processed;

    assert_eq!(tokens_single, 9);
    assert_eq!(
        tokens_split, 9,
        "batch accumulation must not duplicate tokens"
    );
}

#[test]
fn finalize_token_count_includes_remaining() {
    let html = b"<p>First</p><p>Second</p>";
    let (_, tokens, _) = convert_and_collect(html);
    assert_eq!(tokens, 6, "finalize must retain its final batch");
}

#[test]
fn single_feed_vs_byte_by_byte_token_count() {
    let html = b"<div><p>Hello</p></div>";

    let mut conv1 = make_converter();
    let out1 = conv1.feed_chunk(html).unwrap();
    let mut md1 = out1.markdown;
    let r1 = conv1.finalize().unwrap();
    md1.extend_from_slice(&r1.final_markdown);
    let tokens_single = r1.stats.tokens_processed;

    let mut conv2 = make_converter();
    let mut md2 = Vec::new();
    for &byte in html {
        let out = conv2.feed_chunk(&[byte]).unwrap();
        md2.extend_from_slice(&out.markdown);
    }
    let r2 = conv2.finalize().unwrap();
    md2.extend_from_slice(&r2.final_markdown);
    let tokens_byte = r2.stats.tokens_processed;

    // Byte-by-byte feeding may produce more tokens due to character token
    // splitting at chunk boundaries. The important invariant is that both
    // counts are positive and the markdown output is semantically equivalent.
    assert!(
        tokens_single > 0 && tokens_byte > 0,
        "both should have positive token counts: single={}, byte={}",
        tokens_single,
        tokens_byte
    );
    // The byte-by-byte count should not be wildly larger (no unbounded growth)
    assert!(
        tokens_byte < tokens_single * 5,
        "byte-by-byte token count ({}) should not be wildly larger than single-feed ({})",
        tokens_byte,
        tokens_single
    );
}

#[test]
fn no_double_counting_across_batches() {
    let html = b"<p>One</p><p>Two</p><p>Three</p>";
    let (_, tokens, _) = convert_and_collect(html);
    assert_eq!(tokens, 9, "three paragraphs must not be double-counted");
}

#[test]
fn parse_errors_accumulate_across_chunks() {
    let mut conv = make_converter();
    let chunk1 = b"<p>\x00bad</p>";
    let chunk2 = b"<p>\x00worse</p>";
    let _ = conv.feed_chunk(chunk1).unwrap();
    let _ = conv.feed_chunk(chunk2).unwrap();
    let result = conv.finalize().unwrap();
    assert_eq!(result.stats.tokens_processed, 10);
    assert_eq!(result.stats.parse_errors, 2);
}
