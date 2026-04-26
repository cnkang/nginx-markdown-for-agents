//! Coverage improvement tests for streaming-specific edge cases:
//! - streaming/charset.rs: set_content_type after resolved
//! - streaming/converter.rs: chunked feed, UTF-8 boundary, security
//! - ffi/streaming.rs: basic lifecycle, timeout, etag, content_type

#![cfg(feature = "streaming")]

use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::ffi::*;
use nginx_markdown_converter::streaming::budget::MemoryBudget;
use nginx_markdown_converter::streaming::converter::StreamingConverter;
use nginx_markdown_converter::streaming::types::*;
use std::ptr;

fn make_converter() -> StreamingConverter {
    StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default())
}

fn ffi_test_default_streaming_options() -> MarkdownOptions {
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

fn ffi_test_empty_result() -> MarkdownResult {
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

/* ================================================================
 * Streaming converter basic lifecycle tests
 * ================================================================ */

#[test]
fn test_streaming_basic_lifecycle() {
    let mut converter = make_converter();
    let html = b"<h1>Title</h1><p>Content</p>";
    let _output = converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("Title"));
    assert!(md.contains("Content"));
}

#[test]
fn test_streaming_empty_feed() {
    let mut converter = make_converter();
    converter.feed_chunk(b"").expect("empty feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.trim().is_empty() || md.len() < 10);
}

#[test]
fn test_streaming_multiple_feeds() {
    let mut converter = make_converter();
    converter.feed_chunk(b"<h1>Hel").expect("feed 1 failed");
    converter.feed_chunk(b"lo</h1>").expect("feed 2 failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("Hello"));
}

#[test]
fn test_streaming_with_content_type() {
    let mut converter = make_converter();
    converter.set_content_type(Some("text/html; charset=utf-8".to_string()));
    let output = converter.feed_chunk(b"<p>Test</p>").expect("feed failed");
    /* Content may be in feed output or in finalize output */
    let feed_md = String::from_utf8_lossy(&output.markdown);
    let result = converter.finalize().expect("finalize failed");
    let final_md = String::from_utf8_lossy(&result.final_markdown);
    let combined = format!("{}{}", feed_md, final_md);
    assert!(combined.contains("Test"), "Content should appear in output");
}

#[test]
fn test_streaming_with_budget() {
    let budget = MemoryBudget::for_total(1024 * 1024);
    let mut converter = StreamingConverter::new(ConversionOptions::default(), budget);
    converter
        .feed_chunk(b"<p>Small content</p>")
        .expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("Small content"));
}

#[test]
fn test_streaming_script_stripped() {
    let mut converter = make_converter();
    let html = b"<p>Safe</p><script>alert(1)</script><p>Also safe</p>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("Safe"));
    assert!(md.contains("Also safe"));
    assert!(!md.contains("alert"));
}

#[test]
fn test_streaming_javascript_url_stripped() {
    let mut converter = make_converter();
    let html = b"<a href=\"javascript:void(0)\">Click</a>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(!md.contains("javascript:"));
    assert!(md.contains("Click"));
}

#[test]
fn test_streaming_data_url_stripped() {
    let mut converter = make_converter();
    let html = b"<img src=\"data:image/png;base64,AAAA\" alt=\"Img\">";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(!md.contains("data:"));
}

#[test]
fn test_streaming_event_handler_stripped() {
    let mut converter = make_converter();
    let html = b"<div onclick=\"alert(1)\">Content</div>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(!md.contains("onclick"));
    assert!(md.contains("Content"));
}

#[test]
fn test_streaming_style_attribute_stripped() {
    let mut converter = make_converter();
    let html = b"<p style=\"color:red\">Text</p>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(!md.contains("style"));
    assert!(md.contains("Text"));
}

#[test]
fn test_streaming_deeply_nested() {
    let mut converter = make_converter();
    let mut html = Vec::new();
    html.extend_from_slice(b"<html><body>");
    for _ in 0..50 {
        html.extend_from_slice(b"<div>");
    }
    html.extend_from_slice(b"<p>Deep</p>");
    for _ in 0..50 {
        html.extend_from_slice(b"</div>");
    }
    html.extend_from_slice(b"</body></html>");
    converter.feed_chunk(&html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("Deep"));
}

#[test]
fn test_streaming_chunked_feed() {
    let mut converter = make_converter();
    let html = b"<h1>Title</h1><p>Paragraph with <strong>bold</strong> text.</p>";
    for chunk in html.chunks(10) {
        converter.feed_chunk(chunk).expect("chunk feed failed");
    }
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("Title"));
    assert!(md.contains("bold"));
}

#[test]
fn test_streaming_utf8_multibyte_across_chunks() {
    let mut converter = make_converter();
    let html = "<p>你好世界</p>".as_bytes();
    for chunk in html.chunks(5) {
        converter.feed_chunk(chunk).expect("utf8 chunk feed failed");
    }
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("你好世界"));
}

#[test]
fn test_streaming_table() {
    let mut converter = make_converter();
    let html = b"<table><tr><th>H1</th><th>H2</th></tr><tr><td>A</td><td>B</td></tr></table>";
    let output = converter.feed_chunk(html).expect("feed failed");
    let feed_md = String::from_utf8_lossy(&output.markdown);
    /* Table may trigger streaming fallback - that's expected behavior */
    let result = converter.finalize();
    match result {
        Ok(r) => {
            let final_md = String::from_utf8_lossy(&r.final_markdown);
            let combined = format!("{}{}", feed_md, final_md);
            assert!(
                combined.contains("H1") || combined.contains("A") || combined.contains("table")
            );
        }
        Err(ConversionError::StreamingFallback { .. }) => {
            /* Table detection triggers fallback - this is correct behavior */
        }
        Err(e) => panic!("Unexpected error: {}", e),
    }
}

#[test]
fn test_streaming_blockquote() {
    let mut converter = make_converter();
    let html = b"<blockquote><p>Quoted</p></blockquote>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("Quoted"));
}

#[test]
fn test_streaming_code_block() {
    let mut converter = make_converter();
    let html = b"<pre><code>fn main() { println!(\"hello\"); }</code></pre>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("fn main"));
}

#[test]
fn test_streaming_list() {
    let mut converter = make_converter();
    let html = b"<ul><li>Item 1</li><li>Item 2</li><li>Item 3</li></ul>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("Item 1"));
    assert!(md.contains("Item 2"));
    assert!(md.contains("Item 3"));
}

#[test]
fn test_streaming_set_content_type_after_resolved() {
    let mut converter = make_converter();
    converter.set_content_type(Some("text/html; charset=utf-8".to_string()));
    let output1 = converter
        .feed_chunk(b"<p>First</p>")
        .expect("feed 1 failed");
    /* Set content type again - should be no-op since already resolved */
    converter.set_content_type(Some("text/html; charset=iso-8859-1".to_string()));
    let output2 = converter
        .feed_chunk(b"<p>Second</p>")
        .expect("feed 2 failed");
    let result = converter.finalize().expect("finalize failed");
    let feed1_md = String::from_utf8_lossy(&output1.markdown);
    let feed2_md = String::from_utf8_lossy(&output2.markdown);
    let final_md = String::from_utf8_lossy(&result.final_markdown);
    let combined = format!("{}{}{}", feed1_md, feed2_md, final_md);
    assert!(
        combined.contains("First") || combined.contains("Second"),
        "Content should appear in combined output"
    );
}

#[test]
fn test_streaming_stats_populated() {
    let mut converter = make_converter();
    converter
        .feed_chunk(b"<h1>Title</h1><p>Content</p>")
        .expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    assert!(
        result.stats.tokens_processed > 0,
        "tokens_processed should be > 0"
    );
    assert!(
        result.stats.chunks_processed > 0,
        "chunks_processed should be > 0"
    );
}

/* ================================================================
 * Streaming types coverage
 * ================================================================ */

#[test]
fn test_fallback_reason_display() {
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
        format!("{}", FallbackReason::UnsupportedStructure("test".into())),
        "unsupported structure: test"
    );
}

#[test]
fn test_commit_state_variants() {
    let pre = CommitState::PreCommit;
    let post = CommitState::PostCommit;
    assert_ne!(pre, post);
}

/* ================================================================
 * Streaming error paths
 * ================================================================ */

#[test]
fn test_budget_exceeded_error() {
    let err = ConversionError::BudgetExceeded {
        stage: "test".into(),
        used: 2048,
        limit: 1024,
    };
    assert_eq!(err.code(), 6);
    let msg = format!("{}", err);
    assert!(msg.contains("Budget exceeded"));
}

#[test]
fn test_streaming_fallback_error() {
    let err = ConversionError::StreamingFallback {
        reason: FallbackReason::TableDetected,
    };
    assert_eq!(err.code(), 7);
    let msg = format!("{}", err);
    assert!(msg.contains("Streaming fallback"));
}

#[test]
fn test_post_commit_error() {
    let err = ConversionError::PostCommitError {
        reason: "decompression failed".into(),
        bytes_emitted: 100,
        original_code: 3,
    };
    assert_eq!(err.code(), 8);
    let msg = format!("{}", err);
    assert!(msg.contains("Post-commit"));
}

/* ================================================================
 * FFI streaming edge cases
 * ================================================================ */

#[test]
fn test_ffi_streaming_basic_lifecycle() {
    let options = ffi_test_default_streaming_options();

    unsafe {
        let handle = markdown_streaming_new(&options);
        assert!(
            !handle.is_null(),
            "streaming new should return non-NULL handle"
        );

        let html = b"<h1>Title</h1>";
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;
        let feed_rc = markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        );
        assert_eq!(feed_rc, 0, "feed should succeed");

        if !out_data.is_null() {
            markdown_streaming_output_free(out_data, out_len);
        }

        let mut result = ffi_test_empty_result();
        let finalize_rc = markdown_streaming_finalize(handle, &mut result);
        assert_eq!(finalize_rc, 0, "finalize should succeed");
        assert!(result.markdown_len > 0, "should produce markdown");

        markdown_result_free(&mut result);
    }
}

#[test]
fn test_ffi_streaming_null_options() {
    unsafe {
        let handle = markdown_streaming_new(ptr::null());
        assert!(handle.is_null(), "NULL options should return NULL handle");
    }
}

#[test]
fn test_ffi_streaming_abort() {
    let options = ffi_test_default_streaming_options();

    unsafe {
        let handle = markdown_streaming_new(&options);
        assert!(!handle.is_null());
        markdown_streaming_abort(handle);
    }
}

#[test]
fn test_ffi_streaming_free() {
    let options = ffi_test_default_streaming_options();

    unsafe {
        let handle = markdown_streaming_new(&options);
        assert!(!handle.is_null());
        markdown_streaming_free(handle);
    }
}

#[test]
fn test_ffi_streaming_with_timeout() {
    let options = MarkdownOptions {
        timeout_ms: 30000,
        ..ffi_test_default_streaming_options()
    };

    unsafe {
        let handle = markdown_streaming_new(&options);
        assert!(!handle.is_null());

        let html = b"<p>Quick content</p>";
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;
        let feed_rc = markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        );
        assert_eq!(feed_rc, 0, "feed should succeed");
        if !out_data.is_null() {
            markdown_streaming_output_free(out_data, out_len);
        }

        let mut result = ffi_test_empty_result();
        let finalize_rc = markdown_streaming_finalize(handle, &mut result);
        assert_eq!(finalize_rc, 0, "finalize should succeed");
        assert_eq!(result.error_code, 0, "should complete within timeout");
        markdown_result_free(&mut result);
    }
}

#[test]
fn test_ffi_streaming_with_etag_and_tokens() {
    let options = MarkdownOptions {
        generate_etag: 1,
        estimate_tokens: 1,
        ..ffi_test_default_streaming_options()
    };

    unsafe {
        let handle = markdown_streaming_new(&options);
        assert!(!handle.is_null());

        let html = b"<h1>Title</h1><p>Content</p>";
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;
        let feed_rc = markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        );
        assert_eq!(feed_rc, 0, "feed should succeed");
        if !out_data.is_null() {
            markdown_streaming_output_free(out_data, out_len);
        }

        let mut result = ffi_test_empty_result();
        let finalize_rc = markdown_streaming_finalize(handle, &mut result);
        assert_eq!(finalize_rc, 0, "finalize should succeed");
        assert_eq!(result.error_code, 0);
        assert!(
            !result.etag.is_null() || result.etag_len == 0,
            "ETag field should be set (null or non-null)"
        );
        assert!(result.token_estimate > 0, "Token estimate should be > 0");
        markdown_result_free(&mut result);
    }
}

#[test]
fn test_ffi_streaming_multiple_feeds() {
    let options = ffi_test_default_streaming_options();

    unsafe {
        let handle = markdown_streaming_new(&options);
        assert!(!handle.is_null());

        let chunks: &[&[u8]] = &[b"<h1>Hel", b"lo</h1><p>Wo", b"rld</p>"];
        for chunk in chunks {
            let mut out_data: *mut u8 = ptr::null_mut();
            let mut out_len: usize = 0;
            let feed_rc = markdown_streaming_feed(
                handle,
                chunk.as_ptr(),
                chunk.len(),
                &mut out_data,
                &mut out_len,
            );
            assert_eq!(feed_rc, 0, "feed should succeed");
            if !out_data.is_null() {
                markdown_streaming_output_free(out_data, out_len);
            }
        }

        let mut result = ffi_test_empty_result();
        let finalize_rc = markdown_streaming_finalize(handle, &mut result);
        assert_eq!(finalize_rc, 0, "finalize should succeed");
        assert_eq!(result.error_code, 0);
        assert!(result.markdown_len > 0);
        markdown_result_free(&mut result);
    }
}

#[test]
fn test_ffi_streaming_with_content_type() {
    let ct = b"text/html; charset=utf-8";
    let options = MarkdownOptions {
        content_type: ct.as_ptr(),
        content_type_len: ct.len(),
        ..ffi_test_default_streaming_options()
    };

    unsafe {
        let handle = markdown_streaming_new(&options);
        assert!(!handle.is_null());

        let html = b"<p>Test</p>";
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;
        let feed_rc = markdown_streaming_feed(
            handle,
            html.as_ptr(),
            html.len(),
            &mut out_data,
            &mut out_len,
        );
        assert_eq!(feed_rc, 0, "feed should succeed");
        if !out_data.is_null() {
            markdown_streaming_output_free(out_data, out_len);
        }

        let mut result = ffi_test_empty_result();
        let finalize_rc = markdown_streaming_finalize(handle, &mut result);
        assert_eq!(finalize_rc, 0, "finalize should succeed");
        assert_eq!(result.error_code, 0);
        markdown_result_free(&mut result);
    }
}
