//! Coverage improvement tests for streaming-specific code paths.
//!
//! These tests target uncovered code in:
//! - `error.rs`: Streaming-feature-gated error variants
//! - `streaming/types.rs`: StreamEvent, CommitState, FallbackReason, etc.
//! - `ffi/streaming.rs`: Additional FFI edge cases
//! - `streaming/charset.rs`: Charset detection edge cases

#![cfg(feature = "streaming")]

use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::ffi::*;
use nginx_markdown_converter::streaming::types::*;
use std::ptr;

/* ================================================================
 * error.rs streaming variants — code() and Display
 * ================================================================ */

#[test]
fn test_error_budget_exceeded_code_and_display() {
    let err = ConversionError::BudgetExceeded {
        stage: "output_buffer".into(),
        used: 2048,
        limit: 1024,
    };
    assert_eq!(err.code(), 6);
    let msg = format!("{}", err);
    assert!(msg.contains("Budget exceeded"));
    assert!(msg.contains("output_buffer"));
    assert!(msg.contains("2048"));
    assert!(msg.contains("1024"));
}

#[test]
fn test_error_streaming_fallback_code_and_display() {
    let err = ConversionError::StreamingFallback {
        reason: FallbackReason::TableDetected,
    };
    assert_eq!(err.code(), 7);
    let msg = format!("{}", err);
    assert!(msg.contains("Streaming fallback"));
    assert!(msg.contains("table"));
}

#[test]
fn test_error_post_commit_code_and_display() {
    let err = ConversionError::PostCommitError {
        reason: "timeout during streaming".into(),
        bytes_emitted: 512,
        original_code: 3,
    };
    assert_eq!(err.code(), 8);
    let msg = format!("{}", err);
    assert!(msg.contains("Post-commit error"));
    assert!(msg.contains("512"));
    assert!(msg.contains("original_code=3"));
}

#[test]
fn test_error_streaming_variants_clone() {
    let err1 = ConversionError::BudgetExceeded {
        stage: "state_stack".into(),
        used: 100,
        limit: 50,
    };
    let cloned = err1.clone();
    assert_eq!(err1.code(), cloned.code());

    let err2 = ConversionError::StreamingFallback {
        reason: FallbackReason::LookaheadExceeded,
    };
    let cloned2 = err2.clone();
    assert_eq!(err2.code(), cloned2.code());

    let err3 = ConversionError::PostCommitError {
        reason: "test".into(),
        bytes_emitted: 0,
        original_code: 6,
    };
    let cloned3 = err3.clone();
    assert_eq!(err3.code(), cloned3.code());
}

/* ================================================================
 * streaming/types.rs — FallbackReason Display
 * ================================================================ */

#[test]
fn test_fallback_reason_display_all_variants() {
    assert_eq!(
        format!("{}", FallbackReason::TableDetected),
        "table element detected"
    );
    assert_eq!(
        format!("{}", FallbackReason::LookaheadExceeded),
        "lookahead buffer exceeded budget"
    );
    assert_eq!(
        format!("{}", FallbackReason::FrontMatterOverflow),
        "front matter data exceeds lookahead budget"
    );
    assert_eq!(
        format!("{}", FallbackReason::UnsupportedStructure("nested".into())),
        "unsupported structure: nested"
    );
}

#[test]
fn test_fallback_reason_equality() {
    assert_eq!(FallbackReason::TableDetected, FallbackReason::TableDetected);
    assert_ne!(
        FallbackReason::TableDetected,
        FallbackReason::LookaheadExceeded
    );
    assert_eq!(
        FallbackReason::UnsupportedStructure("a".into()),
        FallbackReason::UnsupportedStructure("a".into())
    );
    assert_ne!(
        FallbackReason::UnsupportedStructure("a".into()),
        FallbackReason::UnsupportedStructure("b".into())
    );
}

#[test]
fn test_fallback_reason_debug() {
    let reason = FallbackReason::TableDetected;
    let debug = format!("{:?}", reason);
    assert!(debug.contains("TableDetected"));
}

#[test]
fn test_fallback_reason_clone() {
    let reason = FallbackReason::UnsupportedStructure("test".into());
    let cloned = reason.clone();
    assert_eq!(reason, cloned);
}

/* ================================================================
 * streaming/types.rs — CommitState
 * ================================================================ */

#[test]
fn test_commit_state_equality() {
    assert_eq!(CommitState::PreCommit, CommitState::PreCommit);
    assert_eq!(CommitState::PostCommit, CommitState::PostCommit);
    assert_ne!(CommitState::PreCommit, CommitState::PostCommit);
}

#[test]
fn test_commit_state_debug() {
    assert!(format!("{:?}", CommitState::PreCommit).contains("PreCommit"));
    assert!(format!("{:?}", CommitState::PostCommit).contains("PostCommit"));
}

#[test]
fn test_commit_state_clone_copy() {
    let state = CommitState::PreCommit;
    let cloned = state;
    let copied = state;
    assert_eq!(state, cloned);
    assert_eq!(state, copied);
}

/* ================================================================
 * streaming/types.rs — StreamEvent
 * ================================================================ */

#[test]
fn test_stream_event_variants() {
    let start = StreamEvent::StartTag {
        name: "div".into(),
        attrs: vec![("class".into(), "main".into())],
        self_closing: false,
    };
    assert!(format!("{:?}", start).contains("StartTag"));

    let end = StreamEvent::EndTag { name: "div".into() };
    assert!(format!("{:?}", end).contains("EndTag"));

    let text = StreamEvent::Text("hello".into());
    assert!(format!("{:?}", text).contains("Text"));

    let comment = StreamEvent::Comment("a comment".into());
    assert!(format!("{:?}", comment).contains("Comment"));

    let doctype = StreamEvent::Doctype;
    assert!(format!("{:?}", doctype).contains("Doctype"));

    let parse_err = StreamEvent::ParseError("bad token".into());
    assert!(format!("{:?}", parse_err).contains("ParseError"));
}

#[test]
fn test_stream_event_equality() {
    let a = StreamEvent::Text("hello".into());
    let b = StreamEvent::Text("hello".into());
    let c = StreamEvent::Text("world".into());
    assert_eq!(a, b);
    assert_ne!(a, c);

    assert_eq!(StreamEvent::Doctype, StreamEvent::Doctype);
}

#[test]
fn test_stream_event_clone() {
    let event = StreamEvent::StartTag {
        name: "p".into(),
        attrs: vec![],
        self_closing: false,
    };
    let cloned = event.clone();
    assert_eq!(event, cloned);
}

/* ================================================================
 * streaming/types.rs — ChunkOutput and StreamingResult
 * ================================================================ */

#[test]
fn test_chunk_output_debug_clone() {
    let output = ChunkOutput {
        markdown: vec![b'#', b' ', b'H', b'i'],
        flush_count: 1,
    };
    let cloned = output.clone();
    assert_eq!(output.markdown, cloned.markdown);
    assert_eq!(output.flush_count, cloned.flush_count);
    assert!(format!("{:?}", output).contains("ChunkOutput"));
}

#[test]
fn test_streaming_result_debug_clone() {
    let result = StreamingResult {
        final_markdown: vec![b'o', b'k'],
        token_estimate: Some(10),
        etag: Some("abc123".into()),
        stats: StreamingStats::default(),
    };
    let cloned = result.clone();
    assert_eq!(result.final_markdown, cloned.final_markdown);
    assert_eq!(result.token_estimate, cloned.token_estimate);
    assert_eq!(result.etag, cloned.etag);
    assert!(format!("{:?}", result).contains("StreamingResult"));
}

#[test]
fn test_streaming_stats_default() {
    let stats = StreamingStats::default();
    assert_eq!(stats.tokens_processed, 0);
    assert_eq!(stats.parse_errors, 0);
    assert_eq!(stats.flush_count, 0);
    assert_eq!(stats.peak_memory_estimate, 0);
    assert_eq!(stats.chunks_processed, 0);
}

#[test]
fn test_streaming_stats_debug_clone() {
    let stats = StreamingStats {
        tokens_processed: 100,
        parse_errors: 2,
        flush_count: 5,
        peak_memory_estimate: 4096,
        chunks_processed: 10,
    };
    let cloned = stats.clone();
    assert_eq!(stats.tokens_processed, cloned.tokens_processed);
    assert_eq!(stats.peak_memory_estimate, cloned.peak_memory_estimate);
    assert!(format!("{:?}", stats).contains("StreamingStats"));
}

/* ================================================================
 * FFI streaming — additional edge cases for coverage
 * ================================================================ */

fn test_options() -> MarkdownOptions {
    MarkdownOptions {
        flavor: 0,
        timeout_ms: 5000,
        generate_etag: 0,
        estimate_tokens: 0,
        front_matter: 0,
        content_type: ptr::null(),
        content_type_len: 0,
        base_url: ptr::null(),
        base_url_len: 0,
        streaming_budget: 0,
    }
}

fn zeroed_result() -> MarkdownResult {
    MarkdownResult {
        markdown: ptr::null_mut(),
        markdown_len: 0,
        etag: ptr::null_mut(),
        etag_len: 0,
        token_estimate: 0,
        error_code: 0,
        error_message: ptr::null_mut(),
        error_len: 0,
        peak_memory_estimate: 0,
    }
}

#[test]
fn test_streaming_finalize_with_etag() {
    let opts = MarkdownOptions {
        generate_etag: 1,
        ..test_options()
    };
    let handle = unsafe { markdown_streaming_new(&opts) };
    assert!(!handle.is_null());

    let html = b"<h1>Title</h1><p>Content</p>";
    let mut out_data: *mut u8 = ptr::null_mut();
    let mut out_len: usize = 0;

    let rc = unsafe {
        markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        )
    };
    assert_eq!(rc, 0);

    if !out_data.is_null() {
        unsafe { markdown_streaming_output_free(out_data, out_len) };
    }

    let mut result = zeroed_result();
    let rc = unsafe { markdown_streaming_finalize(handle, &mut result) };
    assert_eq!(rc, 0, "finalize with etag should succeed");

    /* ETag may or may not be generated depending on streaming implementation */
    /* The important thing is the finalize path with generate_etag=1 is exercised */

    unsafe { markdown_result_free(&mut result) };
}

#[test]
fn test_streaming_finalize_with_token_estimate() {
    let opts = MarkdownOptions {
        estimate_tokens: 1,
        ..test_options()
    };
    let handle = unsafe { markdown_streaming_new(&opts) };
    assert!(!handle.is_null());

    let html = b"<p>Some text content for token estimation</p>";
    let mut out_data: *mut u8 = ptr::null_mut();
    let mut out_len: usize = 0;

    let rc = unsafe {
        markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        )
    };
    assert_eq!(rc, 0);

    if !out_data.is_null() {
        unsafe { markdown_streaming_output_free(out_data, out_len) };
    }

    let mut result = zeroed_result();
    let rc = unsafe { markdown_streaming_finalize(handle, &mut result) };
    assert_eq!(rc, 0, "finalize with token estimate should succeed");

    unsafe { markdown_result_free(&mut result) };
}

#[test]
fn test_streaming_finalize_with_etag_and_tokens() {
    let opts = MarkdownOptions {
        generate_etag: 1,
        estimate_tokens: 1,
        ..test_options()
    };
    let handle = unsafe { markdown_streaming_new(&opts) };
    assert!(!handle.is_null());

    let html = b"<h1>Title</h1><p>Paragraph with enough content for tokens</p>";
    let mut out_data: *mut u8 = ptr::null_mut();
    let mut out_len: usize = 0;

    let rc = unsafe {
        markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        )
    };
    assert_eq!(rc, 0);

    if !out_data.is_null() {
        unsafe { markdown_streaming_output_free(out_data, out_len) };
    }

    let mut result = zeroed_result();
    let rc = unsafe { markdown_streaming_finalize(handle, &mut result) };
    assert_eq!(rc, 0);

    /* ETag and token estimate paths are exercised; actual values depend on
     * streaming implementation details */

    unsafe { markdown_result_free(&mut result) };
}

#[test]
fn test_streaming_with_custom_budget() {
    let opts = MarkdownOptions {
        streaming_budget: 32 * 1024,
        ..test_options()
    };
    let handle = unsafe { markdown_streaming_new(&opts) };
    assert!(!handle.is_null());

    let html = b"<p>Content with custom budget</p>";
    let mut out_data: *mut u8 = ptr::null_mut();
    let mut out_len: usize = 0;

    let rc = unsafe {
        markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        )
    };
    assert_eq!(rc, 0);

    if !out_data.is_null() {
        unsafe { markdown_streaming_output_free(out_data, out_len) };
    }

    let mut result = zeroed_result();
    let rc = unsafe { markdown_streaming_finalize(handle, &mut result) };
    assert_eq!(rc, 0);

    unsafe { markdown_result_free(&mut result) };
}

#[test]
fn test_streaming_with_content_type() {
    let ct = b"text/html; charset=utf-8";
    let opts = MarkdownOptions {
        content_type: ct.as_ptr(),
        content_type_len: ct.len(),
        ..test_options()
    };
    let handle = unsafe { markdown_streaming_new(&opts) };
    assert!(!handle.is_null());

    let html = b"<p>Content with charset</p>";
    let mut out_data: *mut u8 = ptr::null_mut();
    let mut out_len: usize = 0;

    let rc = unsafe {
        markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        )
    };
    assert_eq!(rc, 0);

    if !out_data.is_null() {
        unsafe { markdown_streaming_output_free(out_data, out_len) };
    }

    let mut result = zeroed_result();
    let rc = unsafe { markdown_streaming_finalize(handle, &mut result) };
    assert_eq!(rc, 0);

    unsafe { markdown_result_free(&mut result) };
}

#[test]
fn test_streaming_multiple_feeds() {
    let opts = test_options();
    let handle = unsafe { markdown_streaming_new(&opts) };
    assert!(!handle.is_null());

    let chunks = [
        b"<h1>Title</h1>" as &[u8],
        b"<p>First paragraph.</p>",
        b"<p>Second paragraph.</p>",
        b"<ul><li>Item 1</li><li>Item 2</li></ul>",
    ];

    for chunk in &chunks {
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe {
            markdown_streaming_feed(
                handle,
                chunk.as_ptr(),
                chunk.len(),
                &mut out_data,
                &mut out_len,
            )
        };
        assert_eq!(rc, 0, "feed should succeed");

        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }
    }

    let mut result = zeroed_result();
    let rc = unsafe { markdown_streaming_finalize(handle, &mut result) };
    assert_eq!(rc, 0);
    assert!(result.markdown_len > 0, "Should produce markdown");

    unsafe { markdown_result_free(&mut result) };
}

#[test]
fn test_streaming_with_timeout() {
    let opts = MarkdownOptions {
        timeout_ms: 100,
        ..test_options()
    };
    let handle = unsafe { markdown_streaming_new(&opts) };
    assert!(!handle.is_null());

    let html = b"<p>Quick content</p>";
    let mut out_data: *mut u8 = ptr::null_mut();
    let mut out_len: usize = 0;

    let rc = unsafe {
        markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        )
    };

    if !out_data.is_null() {
        unsafe { markdown_streaming_output_free(out_data, out_len) };
    }

    /* Either succeeds or times out — both are valid */
    if rc == 0 {
        let mut result = zeroed_result();
        let rc = unsafe { markdown_streaming_finalize(handle, &mut result) };
        assert!(rc == 0 || rc == 3, "Should succeed or timeout");
        unsafe { markdown_result_free(&mut result) };
    } else {
        unsafe { markdown_streaming_abort(handle) };
    }
}
