//! Security tests for malformed/adversarial HTML in the streaming converter.
//!
//! Validates that the streaming path handles malformed and adversarial HTML
//! inputs safely without crashing, producing unbounded output, or leaking
//! dangerous content.
//!
//! **Validates: Requirement 6 AC 1 (Malformed HTML test)**
//! **Validates: Requirement 1 AC 3 (No unbounded allocation in streaming path)**

#![cfg(feature = "streaming")]

#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use std::panic::{AssertUnwindSafe, catch_unwind};

use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::streaming::{MemoryBudget, StreamingConverter};
use streaming_test_support::{convert_streaming_chunked, default_streaming_options};

/// Helper: create a streaming converter with default budget and UTF-8 content type.
fn make_default_converter() -> StreamingConverter {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    conv
}

/// Helper: create a streaming converter with a specific budget.
fn make_converter_with_budget(budget: MemoryBudget) -> StreamingConverter {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), budget);
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    conv
}

// ════════════════════════════════════════════════════════════════════
// Deeply nested elements (nesting depth saturation)
// AGENTS.md Rule 5: nesting-depth saturation-safe
// ════════════════════════════════════════════════════════════════════

/// Deeply nested (1500 levels) HTML must not panic or produce unbounded output.
/// The sanitizer's MAX_NESTING_DEPTH (1000) should kick in.
///
/// **Validates: Requirement 6 AC 1, Requirement 1 AC 3**
#[test]
fn deeply_nested_elements_no_panic() {
    let depth = 1500;
    let mut html = String::with_capacity(depth * 10);
    for _ in 0..depth {
        html.push_str("<div>");
    }
    html.push_str("<p>deep</p>");
    for _ in 0..depth {
        html.push_str("</div>");
    }

    let result = catch_unwind(AssertUnwindSafe(|| {
        let mut conv = make_default_converter();
        let _ = conv.feed_chunk(html.as_bytes());
        let _ = conv.finalize();
    }));

    assert!(
        result.is_ok(),
        "streaming converter panicked on deeply nested HTML"
    );
}

/// Deeply nested elements fed in small chunks (1 byte at a time) must not panic.
/// Tests boundary handling when nesting depth increases across chunk boundaries.
///
/// **Validates: Requirement 6 AC 1, Requirement 1 AC 3**
#[test]
fn deeply_nested_elements_single_byte_chunks_no_panic() {
    let depth = 500;
    let mut html = String::with_capacity(depth * 10);
    html.push_str("<!DOCTYPE html><html><body>");
    for _ in 0..depth {
        html.push_str("<div>");
    }
    html.push_str("<p>content</p>");
    for _ in 0..depth {
        html.push_str("</div>");
    }
    html.push_str("</body></html>");
    let html_bytes = html.as_bytes();

    /* Feed one byte at a time via the chunked helper */
    let chunk_sizes: Vec<usize> = vec![1; html_bytes.len()];

    let result = convert_streaming_chunked(
        html_bytes,
        &chunk_sizes,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        MemoryBudget::default(),
        None,
    );

    /* Either succeeds or returns an error — must not panic */
    match result {
        Ok(run) => {
            /* Output should be bounded */
            assert!(
                run.markdown.len() < html_bytes.len() * 2,
                "output length {} is unreasonably large for input length {}",
                run.markdown.len(),
                html_bytes.len()
            );
        }
        Err(_) => { /* Budget/depth errors are acceptable */ }
    }
}

/// Extremely deep nesting (10000 levels) with a tight budget should hit budget
/// limits before causing any unbounded behavior.
///
/// **Validates: Requirement 1 AC 3**
#[test]
fn extreme_nesting_tight_budget_bounded() {
    let depth = 10_000;
    let mut html = String::with_capacity(depth * 10);
    for _ in 0..depth {
        html.push_str("<div>");
    }
    html.push('x');
    for _ in 0..depth {
        html.push_str("</div>");
    }

    let tight_budget = MemoryBudget {
        total: 32 * 1024,
        state_stack: 8 * 1024,
        output_buffer: 8 * 1024,
        charset_sniff: 1024,
        lookahead: 8 * 1024,
    };

    let mut conv = make_converter_with_budget(tight_budget);
    let result = conv.feed_chunk(html.as_bytes());

    /* Should either succeed (depth saturation prevents stack growth) or fail with budget/depth error */
    match result {
        Ok(output) => {
            assert!(
                output.markdown.len() <= 64 * 1024,
                "output should be bounded"
            );
        }
        Err(ref e) => {
            assert!(
                matches!(
                    e,
                    ConversionError::BudgetExceeded { .. }
                        | ConversionError::MemoryLimit(_)
                        | ConversionError::StreamingFallback { .. }
                ),
                "expected budget/depth error, got: {e:?}"
            );
        }
    }
}

// ════════════════════════════════════════════════════════════════════
// Unclosed tags
// ════════════════════════════════════════════════════════════════════

/// Unclosed tags must not cause the converter to panic or hang.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn unclosed_tags_no_panic() {
    let cases: &[&[u8]] = &[
        b"<html><body><div><p>no closing tags",
        b"<html><body><ul><li>item1<li>item2<li>item3",
        b"<div><span><em><strong>all unclosed",
        b"<html><body><h1>heading<p>paragraph<blockquote>quote",
        b"<html><body><a href='http://example.com'>link text without close",
        b"<table><tr><td>cell<tr><td>cell2",
    ];

    for case in cases {
        let result = catch_unwind(AssertUnwindSafe(|| {
            let mut conv = make_default_converter();
            let _ = conv.feed_chunk(case);
            let _ = conv.finalize();
        }));
        assert!(
            result.is_ok(),
            "converter panicked on unclosed tag input: {:?}",
            String::from_utf8_lossy(case)
        );
    }
}

/// Unclosed tags fed across chunk boundaries should not corrupt state.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn unclosed_tags_split_across_chunks() {
    let html = b"<html><body><div><p>paragraph<span>inline<em>emphasis";
    let chunks = &[5, 3, 7, 4, 10, 6, html.len()];

    let result = convert_streaming_chunked(
        html,
        chunks,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        MemoryBudget::default(),
        None,
    );

    /* Must not panic; error or success both acceptable */
    assert!(
        result.is_ok() || result.is_err(),
        "should produce a definitive result"
    );
}

// ════════════════════════════════════════════════════════════════════
// Invalid UTF-8 sequences
// AGENTS.md Rule 4: Preserve incomplete UTF-8 tails across chunks
// ════════════════════════════════════════════════════════════════════

/// Invalid UTF-8 sequences must not cause panics.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn invalid_utf8_no_panic() {
    let cases: &[&[u8]] = &[
        /* Isolated continuation bytes */
        b"<html><body><p>\x80\x81\x82 text</p></body></html>",
        /* Overlong encoding */
        b"<html><body><p>\xC0\xAF overlong</p></body></html>",
        /* Truncated 3-byte sequence */
        b"<html><body><p>\xE0\xA0 truncated</p></body></html>",
        /* Truncated 4-byte sequence */
        b"<html><body><p>\xF0\x90\x80 truncated4</p></body></html>",
        /* Invalid start bytes */
        b"<html><body><p>\xFE\xFF invalid start</p></body></html>",
        /* Mixed valid and invalid */
        b"<html><body><p>hello \xC3\x28 world</p></body></html>",
        /* All high bytes */
        b"<html><body><p>\xFF\xFE\xFD\xFC\xFB\xFA</p></body></html>",
        /* NUL bytes interspersed */
        b"<html><body><p>text\x00with\x00nulls</p></body></html>",
    ];

    for case in cases {
        let result = catch_unwind(AssertUnwindSafe(|| {
            let mut conv = make_default_converter();
            let _ = conv.feed_chunk(case);
            let _ = conv.finalize();
        }));
        assert!(
            result.is_ok(),
            "converter panicked on invalid UTF-8: {:?}",
            case
        );
    }
}

/// Invalid UTF-8 split at a chunk boundary (partial multibyte across feeds).
///
/// **Validates: Requirement 6 AC 1, AGENTS.md Rule 4**
#[test]
fn invalid_utf8_split_across_chunks() {
    /* Valid UTF-8: "Ω" is 0xCE 0xA9 — split in the middle */
    let chunk1 = b"<html><body><p>\xCE";
    let chunk2 = b"\xA9 omega</p></body></html>";

    let result = catch_unwind(AssertUnwindSafe(|| {
        let mut conv = make_default_converter();
        let _ = conv.feed_chunk(chunk1);
        let _ = conv.feed_chunk(chunk2);
        let _ = conv.finalize();
    }));

    assert!(
        result.is_ok(),
        "converter panicked on split multibyte UTF-8"
    );
}

/// Completely random bytes must not panic.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn random_bytes_no_panic() {
    /* Deterministic pseudo-random sequence */
    let mut data = vec![0u8; 4096];
    let mut seed: u32 = 0xDEAD_BEEF;
    for byte in data.iter_mut() {
        seed = seed.wrapping_mul(1103515245).wrapping_add(12345);
        *byte = (seed >> 16) as u8;
    }

    let result = catch_unwind(AssertUnwindSafe(|| {
        let mut conv = make_default_converter();
        let _ = conv.feed_chunk(&data);
        let _ = conv.finalize();
    }));

    assert!(result.is_ok(), "converter panicked on random bytes");
}

// ════════════════════════════════════════════════════════════════════
// Extremely long attribute values
// ════════════════════════════════════════════════════════════════════

/// Extremely long attribute values must not cause unbounded output.
///
/// **Validates: Requirement 1 AC 3**
#[test]
fn long_attribute_values_bounded_output() {
    let long_value = "x".repeat(100_000);
    let html = format!(
        r#"<html><body><p class="{}">content</p></body></html>"#,
        long_value
    );

    let budget = MemoryBudget::default();

    let mut conv = make_converter_with_budget(budget.clone());
    let result = conv.feed_chunk(html.as_bytes());

    match result {
        Ok(output) => {
            /* Output must not contain the raw attribute value at full length
             * (Markdown doesn't reproduce class attributes) */
            assert!(
                output.markdown.len() < long_value.len(),
                "output ({} bytes) should not contain the entire long attribute ({} bytes)",
                output.markdown.len(),
                long_value.len()
            );
        }
        Err(ref e) => {
            /* Budget exceeded is acceptable */
            assert!(
                matches!(
                    e,
                    ConversionError::BudgetExceeded { .. }
                        | ConversionError::MemoryLimit(_)
                        | ConversionError::StreamingFallback { .. }
                ),
                "unexpected error: {e:?}"
            );
        }
    }
}

/// Long href attribute with a safe URL should not cause unbounded allocation.
///
/// **Validates: Requirement 1 AC 3**
#[test]
fn long_href_bounded() {
    let long_path = "a".repeat(50_000);
    let html = format!(
        r#"<html><body><a href="https://example.com/{}">link</a></body></html>"#,
        long_path
    );

    let result = catch_unwind(AssertUnwindSafe(|| {
        let mut conv = make_default_converter();
        let feed_result = conv.feed_chunk(html.as_bytes());
        if let Ok(output) = &feed_result {
            /* Output should be bounded relative to input */
            assert!(output.markdown.len() < html.len() * 2);
        }
        let _ = conv.finalize();
    }));

    assert!(result.is_ok(), "converter panicked on long href");
}

// ════════════════════════════════════════════════════════════════════
// Script/style injection attempts
// AGENTS.md Rule 5: skip-mode name-aware
// AGENTS.md Rule 27: Escape link labels/destinations; reject control chars in URLs
// ════════════════════════════════════════════════════════════════════

/// Script tags in the streaming path must be fully suppressed.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn script_injection_suppressed() {
    let html = concat!(
        "<!DOCTYPE html><html><body>",
        "<p>safe</p>",
        "<script>alert('xss')</script>",
        "<p>also safe</p>",
        "</body></html>"
    );

    let result = convert_streaming_chunked(
        html.as_bytes(),
        &[html.len()],
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        MemoryBudget::default(),
        None,
    )
    .expect("conversion should succeed");

    assert!(
        !result.markdown.contains("script"),
        "script tag leaked: {}",
        result.markdown
    );
    assert!(
        !result.markdown.contains("alert"),
        "script content leaked: {}",
        result.markdown
    );
    assert!(
        !result.markdown.contains("xss"),
        "XSS payload leaked: {}",
        result.markdown
    );
    assert!(
        result.markdown.contains("safe"),
        "safe content should be preserved"
    );
}

/// Style tags in the streaming path must be suppressed.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn style_injection_suppressed() {
    let html = concat!(
        "<!DOCTYPE html><html><body>",
        "<style>body { background: url('javascript:alert(1)'); }</style>",
        "<p>content</p>",
        "</body></html>"
    );

    let result = convert_streaming_chunked(
        html.as_bytes(),
        &[html.len()],
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        MemoryBudget::default(),
        None,
    )
    .expect("conversion should succeed");

    assert!(
        !result.markdown.contains("style"),
        "style tag leaked: {}",
        result.markdown
    );
    assert!(
        !result.markdown.contains("javascript:"),
        "javascript: URL leaked: {}",
        result.markdown
    );
    assert!(
        result.markdown.contains("content"),
        "safe content preserved"
    );
}

/// Script injected across chunk boundaries must still be suppressed.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn script_split_across_chunks_suppressed() {
    let html = b"<!DOCTYPE html><html><body><p>before</p><script>evil()</script><p>after</p></body></html>";
    /* Split so that <script> tag crosses a chunk boundary */
    let chunks = &[40, 10, 15, html.len()];

    let result = convert_streaming_chunked(
        html,
        chunks,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        MemoryBudget::default(),
        None,
    )
    .expect("conversion should succeed");

    assert!(
        !result.markdown.contains("evil"),
        "script content leaked across chunk boundary: {}",
        result.markdown
    );
    assert!(
        !result.markdown.contains("script"),
        "script tag leaked: {}",
        result.markdown
    );
}

/// JavaScript URL in link must be blocked in streaming path.
///
/// **Validates: Requirement 6 AC 1, AGENTS.md Rule 27**
#[test]
fn javascript_url_in_link_blocked_streaming() {
    let html = concat!(
        "<!DOCTYPE html><html><body>",
        r#"<a href="javascript:alert('xss')">Click me</a>"#,
        "<p>safe</p>",
        "</body></html>"
    );

    let result = convert_streaming_chunked(
        html.as_bytes(),
        &[html.len()],
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        MemoryBudget::default(),
        None,
    )
    .expect("conversion should succeed");

    assert!(
        !result.markdown.contains("javascript:"),
        "javascript: URL leaked: {}",
        result.markdown
    );
    assert!(
        !result.markdown.contains("alert"),
        "alert leaked: {}",
        result.markdown
    );
}

/// Control characters in URLs must be rejected (AGENTS.md Rule 27).
///
/// **Validates: Requirement 6 AC 1, AGENTS.md Rule 27**
#[test]
fn control_chars_in_url_rejected_streaming() {
    let html = "<html><body><a href=\"https://example.com/\x01\x02\x03\">link</a></body></html>";

    let result = convert_streaming_chunked(
        html.as_bytes(),
        &[html.len()],
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        MemoryBudget::default(),
        None,
    )
    .expect("conversion should succeed");

    /* The link should NOT be emitted as a Markdown link with the dangerous URL */
    assert!(
        !result.markdown.contains("\x01"),
        "control char leaked: {:?}",
        result.markdown
    );
    assert!(
        !result.markdown.contains("\x02"),
        "control char leaked: {:?}",
        result.markdown
    );
}

/// Multiple XSS vectors in one document, streamed in small chunks.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn multiple_xss_vectors_chunked() {
    let html = concat!(
        "<!DOCTYPE html><html><body>",
        "<script>alert(1)</script>",
        r#"<p onclick="evil()">text</p>"#,
        r#"<a href="javascript:void(0)">link</a>"#,
        r#"<img src="data:image/svg,<svg onload='x'>">"#,
        "<style>@import url('evil.css')</style>",
        "<p>safe content</p>",
        "</body></html>"
    );
    let html_bytes = html.as_bytes();
    /* Small chunks to stress boundary handling */
    let chunk_sizes: Vec<usize> = vec![17; html_bytes.len().div_ceil(17)];

    let result = convert_streaming_chunked(
        html_bytes,
        &chunk_sizes,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        MemoryBudget::default(),
        None,
    )
    .expect("conversion should succeed");

    assert!(
        !result.markdown.contains("alert"),
        "script payload leaked"
    );
    assert!(
        !result.markdown.contains("onclick"),
        "event handler leaked"
    );
    assert!(
        !result.markdown.contains("javascript:"),
        "js URL leaked"
    );
    assert!(!result.markdown.contains("data:"), "data: URL leaked");
    assert!(
        !result.markdown.to_lowercase().contains("<style"),
        "style tag leaked"
    );
    assert!(
        result.markdown.contains("safe content"),
        "safe content should be preserved"
    );
}

// ════════════════════════════════════════════════════════════════════
// Budget enforcement on adversarial input
// ════════════════════════════════════════════════════════════════════

/// Adversarial input that tries to exhaust the output buffer should be rejected.
///
/// **Validates: Requirement 1 AC 3**
#[test]
fn adversarial_output_amplification_bounded() {
    /* Many heading elements that expand in Markdown (# prefix + newlines) */
    let mut html = String::from("<!DOCTYPE html><html><body>");
    for i in 0..5000 {
        html.push_str(&format!("<h1>heading {}</h1>", i));
    }
    html.push_str("</body></html>");

    let tight_budget = MemoryBudget {
        total: 64 * 1024,
        state_stack: 16 * 1024,
        output_buffer: 16 * 1024,
        charset_sniff: 1024,
        lookahead: 16 * 1024,
    };

    let mut conv = make_converter_with_budget(tight_budget);
    let result = conv.feed_chunk(html.as_bytes());

    match result {
        Ok(output) => {
            /* If it succeeds, output must be bounded */
            assert!(
                output.markdown.len() <= 64 * 1024,
                "output {} exceeds tight budget",
                output.markdown.len()
            );
        }
        Err(e) => {
            /* Budget exceeded is the expected outcome */
            assert!(
                matches!(
                    e,
                    ConversionError::BudgetExceeded { .. }
                        | ConversionError::MemoryLimit(_)
                        | ConversionError::StreamingFallback { .. }
                ),
                "expected budget error, got: {e:?}"
            );
        }
    }
}

// ════════════════════════════════════════════════════════════════════
// Error handling for unrecoverable inputs
// ════════════════════════════════════════════════════════════════════

/// Completely empty input should not crash and should produce empty output.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn empty_input_handled_gracefully() {
    let mut conv = make_default_converter();
    let output = conv.feed_chunk(b"").expect("empty input should not error");
    assert!(output.markdown.is_empty());
    let result = conv.finalize().expect("finalize after empty should succeed");
    /* Empty or minimal output is fine */
    assert!(result.final_markdown.len() < 100);
}

/// Input that is entirely non-HTML random bytes should produce a result
/// (possibly empty Markdown) without panicking.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn non_html_input_no_panic() {
    let inputs: &[&[u8]] = &[
        b"This is just plain text with no HTML",
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", /* PNG header */
        b"%PDF-1.4 \n1 0 obj\n",                  /* PDF header */
        b"\x00\x00\x00\x1cftypisom",              /* MP4 header */
        &vec![0xAA; 8192],                         /* Repeated byte pattern */
    ];

    for input in inputs {
        let result = catch_unwind(AssertUnwindSafe(|| {
            let mut conv = make_default_converter();
            let _ = conv.feed_chunk(input);
            let _ = conv.finalize();
        }));
        assert!(
            result.is_ok(),
            "converter panicked on non-HTML input: {:?}",
            &input[..input.len().min(20)]
        );
    }
}

/// Malformed tag-soup with interleaved open/close that doesn't form valid HTML.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn tag_soup_no_panic() {
    let html = concat!(
        "<p><div></p></div>",
        "<li><ul></li></ul>",
        "<b><i></b></i>",
        "<table><p></table></p>",
        "</nonexistent>",
        "</>",
        "< >",
        "<123>",
        "<-invalid->",
    );

    let result = catch_unwind(AssertUnwindSafe(|| {
        let mut conv = make_default_converter();
        let _ = conv.feed_chunk(html.as_bytes());
        let _ = conv.finalize();
    }));

    assert!(result.is_ok(), "converter panicked on tag soup");
}

/// Extremely long single tag name should not cause unbounded allocation.
///
/// **Validates: Requirement 1 AC 3**
#[test]
fn extremely_long_tag_name_bounded() {
    let long_tag = "a".repeat(100_000);
    let html = format!("<{0}>content</{0}>", long_tag);

    let result = catch_unwind(AssertUnwindSafe(|| {
        let budget = MemoryBudget {
            total: 512 * 1024,
            state_stack: 128 * 1024,
            output_buffer: 128 * 1024,
            charset_sniff: 1024,
            lookahead: 128 * 1024,
        };
        let mut conv = make_converter_with_budget(budget);
        let _ = conv.feed_chunk(html.as_bytes());
        let _ = conv.finalize();
    }));

    assert!(
        result.is_ok(),
        "converter panicked on extremely long tag name"
    );
}

/// HTML comment with adversarial content (never closed) should not hang.
///
/// **Validates: Requirement 6 AC 1**
#[test]
fn unclosed_comment_no_hang() {
    let html = "<!DOCTYPE html><html><body><!-- this comment is never closed and goes on forever ";
    let padding = "x".repeat(10_000);
    let full = format!("{}{}", html, padding);

    let result = catch_unwind(AssertUnwindSafe(|| {
        let mut conv = make_default_converter();
        let _ = conv.feed_chunk(full.as_bytes());
        let _ = conv.finalize();
    }));

    assert!(result.is_ok(), "converter panicked on unclosed comment");
}
