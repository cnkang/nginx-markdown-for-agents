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

unsafe fn new_streaming_handle_for_test(
    options: *const MarkdownOptions,
) -> *mut StreamingConverterHandle {
    let mut handle = ptr::null_mut();
    let _ = unsafe { markdown_streaming_new_with_code(options, &mut handle) };
    handle
}

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
        prune_noise: 1,
        prune_selectors: ptr::null(),
        prune_selector_len: 0,
        prune_protection_selectors: ptr::null(),
        prune_protection_selector_len: 0,
        memory_budget: 0,
        llm_provider: 0,
        chars_per_token_fixed: 0,
        parse_timeout_ms: 0,
        parser_memory_budget: 0,
        flush_threshold: 0,
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
    assert!(md.trim().is_empty());
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

/// Verifies that the streaming path recognizes the `lang-` prefix for code
/// fence languages, matching the full-buffer path (parity requirement).
#[test]
fn test_streaming_code_block_lang_prefix_parity() {
    let mut converter = make_converter();
    let html = b"<pre><code class=\"lang-python\">print(\"hello\")</code></pre>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(
        md.contains("python"),
        "streaming should honor lang- prefix, got: {md}"
    );
}

/// Verifies that an invalid language followed by a valid one is handled
/// correctly in the streaming path, matching the full-buffer path.
#[test]
fn test_streaming_code_block_invalid_then_valid_language_parity() {
    let mut converter = make_converter();
    let html = b"<pre><code class=\"language-bad/token language-rust\">fn main() {}</code></pre>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("rust"), "streaming should pick rust, got: {md}");
    assert!(!md.contains("bad/token"));
}

/// Regression: omitted `</p>` before a block-level start tag (e.g. `<div>`)
/// must produce a separator between the paragraph text and the following
/// block, not glue them together. The streaming sanitizer mirrors implied
/// end-tag closures into the StructuralStateMachine so the paragraph context
/// is exited before the next block opens.
#[test]
fn test_streaming_omitted_p_closure_separates_blocks() {
    let mut converter = make_converter();
    let html = b"<html><body><p>intro<div>block</div></body></html>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(
        !md.contains("introblock"),
        "paragraph text must not glue to following block, got: {md}"
    );
    assert!(md.contains("intro"), "missing intro: {md}");
    assert!(md.contains("block"), "missing block: {md}");
}

/// Implied paragraph closure must unwind nested inline contexts from the
/// inside out so their Markdown delimiters do not cross the paragraph break.
#[test]
fn test_streaming_implied_p_closure_unwinds_inline_contexts_first() {
    let mut converter = make_converter();
    let html = b"<html><body><p><strong>intro<div>block</div></body></html>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);

    assert!(
        md.contains("**intro**\n"),
        "bold delimiter must close before the paragraph break, got: {md}"
    );
}

/// Regression: implied `</p>` closure before a skipped `<form>` must still
/// propagate to the state machine. The sanitizer produces `implied_closures`
/// even when the current tag is returned as `Skip` (e.g. `<form>` is both a
/// PARAGRAPH_CLOSING_START_TAG and a FORM_ELEMENT). Prior to the fix, the
/// `Skip` branch returned before consuming implied closures, leaving the
/// state machine's paragraph context open and gluing "intro" to form content.
#[test]
fn test_streaming_implied_closure_before_skip_propagates() {
    let mut converter = make_converter();
    let html = b"<html><body><p>intro<form>inside</form></body></html>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("intro"), "missing intro: {md}");
    assert!(
        !md.contains("introinside"),
        "skipped form must not glue to preceding paragraph, got: {md}"
    );
}

/// Regression: second paragraph after skipped form must not merge with the first.
#[test]
fn test_streaming_implied_closure_before_skip_multiple_paragraphs() {
    let mut converter = make_converter();
    let html = b"<html><body><p>first<form>skip</form><p>second</body></html>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("first"), "missing first: {md}");
    assert!(md.contains("second"), "missing second: {md}");
    assert!(
        !md.contains("firstsecond"),
        "paragraphs must not merge through skipped form, got: {md}"
    );
}

/// Regression: omitted `</li>` between consecutive list items must not
/// merge item content. The sanitizer's implied closure for `<li>` is
/// propagated to the StructuralStateMachine.
#[test]
fn test_streaming_omitted_li_closure_separates_items() {
    let mut converter = make_converter();
    let html = b"<html><body><ul><li>first<li>second</ul></body></html>";
    converter.feed_chunk(html).expect("feed failed");
    let result = converter.finalize().expect("finalize failed");
    let md = String::from_utf8_lossy(&result.final_markdown);
    assert!(md.contains("first"), "missing first: {md}");
    assert!(md.contains("second"), "missing second: {md}");
    assert!(
        !md.contains("firstsecond"),
        "list items must not glue together, got: {md}"
    );
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
    assert!(combined.contains("First"), "First chunk should use UTF-8");
    assert!(
        combined.contains("Second"),
        "Second chunk should still use UTF-8"
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
        let handle = new_streaming_handle_for_test(&options);
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
        let handle = new_streaming_handle_for_test(ptr::null());
        assert!(handle.is_null(), "NULL options should return NULL handle");
    }
}

#[test]
fn test_ffi_streaming_abort() {
    let options = ffi_test_default_streaming_options();

    unsafe {
        let handle = new_streaming_handle_for_test(&options);
        assert!(!handle.is_null());
        markdown_streaming_abort(handle);
    }
}

#[test]
fn test_ffi_streaming_with_timeout() {
    let options = MarkdownOptions {
        timeout_ms: 30000,
        ..ffi_test_default_streaming_options()
    };

    unsafe {
        let handle = new_streaming_handle_for_test(&options);
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
        let handle = new_streaming_handle_for_test(&options);
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
            !result.etag.is_null() && result.etag_len > 0,
            "ETag field should be non-null with non-zero length"
        );
        assert!(result.token_estimate > 0, "Token estimate should be > 0");
        markdown_result_free(&mut result);
    }
}

#[test]
fn test_ffi_streaming_multiple_feeds() {
    let options = ffi_test_default_streaming_options();

    unsafe {
        let handle = new_streaming_handle_for_test(&options);
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
        let handle = new_streaming_handle_for_test(&options);
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
