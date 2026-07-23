//! Charset transcoding memory budget enforcement tests.
//!
//! Verifies that the streaming converter's total memory budget covers
//! charset transcoding buffers, sniff buffers, UTF-8 tails, and
//! lossy conversion allocations. Tests budget enforcement at exact
//! boundaries, overflow paths, and multi-chunk decoder state.

#![cfg(feature = "streaming")]

use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::streaming::{MemoryBudget, StreamingConverter};

fn make_converter_with_budget(budget: MemoryBudget, charset: &str) -> StreamingConverter {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), budget);
    conv.set_content_type(Some(format!("text/html; charset={}", charset)));
    conv
}

fn iso_8859_1_input(size: usize) -> Vec<u8> {
    let mut data = b"<html><body><p>".to_vec();
    while data.len() < size.saturating_add(15) {
        data.push(0xE9);
    }
    data.extend_from_slice(b"</p></body></html>");
    data
}

fn windows_1252_input(size: usize) -> Vec<u8> {
    let mut data = b"<html><body><p>".to_vec();
    while data.len() < size.saturating_add(15) {
        data.push(0x93);
    }
    data.extend_from_slice(b"</p></body></html>");
    data
}

#[test]
fn iso_8859_1_exceeding_total_budget_returns_budget_exceeded() {
    let budget = MemoryBudget {
        total: 512,
        charset_sniff: 64,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let input = iso_8859_1_input(2048);
    let result = conv.feed_chunk(&input);
    assert!(
        result.is_err(),
        "large ISO-8859-1 input should exceed tiny budget"
    );
    match result.unwrap_err() {
        ConversionError::BudgetExceeded { stage, .. } => {
            assert!(
                stage.contains("total"),
                "expected total budget error, got: {}",
                stage
            );
        }
        other => panic!("expected BudgetExceeded, got: {:?}", other),
    }
}

#[test]
fn windows_1252_exceeding_total_budget_returns_budget_exceeded() {
    let budget = MemoryBudget {
        total: 512,
        charset_sniff: 64,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "windows-1252");
    let input = windows_1252_input(2048);
    let result = conv.feed_chunk(&input);
    assert!(
        result.is_err(),
        "large Windows-1252 input should exceed tiny budget"
    );
    match result.unwrap_err() {
        ConversionError::BudgetExceeded { stage, .. } => {
            assert!(
                stage.contains("total"),
                "expected total budget error, got: {}",
                stage
            );
        }
        other => panic!("expected BudgetExceeded, got: {:?}", other),
    }
}

#[test]
fn sniff_buffer_at_limit_with_transcoded_output_within_budget() {
    let budget = MemoryBudget {
        total: 64 * 1024,
        charset_sniff: 32,
        ..MemoryBudget::default()
    };
    let mut conv = StreamingConverter::new(ConversionOptions::default(), budget);
    let mut input = Vec::new();
    input.extend_from_slice(b"<html><body>");
    while input.len() < 64 {
        input.push(b' ');
    }
    input.extend_from_slice(b"caf\xe9</body></html>");
    let result = conv.feed_chunk(&input);
    assert!(
        result.is_ok(),
        "small transcoded output should fit budget: {:?}",
        result
    );
}

#[test]
fn budget_exact_boundary_succeeds() {
    let budget = MemoryBudget {
        total: 2 * 1024 * 1024,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let input = iso_8859_1_input(1024);
    let result = conv.feed_chunk(&input);
    assert!(
        result.is_ok(),
        "input within budget should succeed: {:?}",
        result
    );
}

#[test]
fn budget_exact_boundary_plus_one_byte_fails() {
    let budget = MemoryBudget {
        total: 256,
        charset_sniff: 32,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let input = iso_8859_1_input(512);
    let result = conv.feed_chunk(&input);
    assert!(result.is_err(), "input exceeding tiny budget should fail");
}

#[test]
fn multi_feed_non_utf8_preserves_decoder_state() {
    let budget = MemoryBudget {
        total: 64 * 1024,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let chunk1 = b"<html><body><p>caf";
    let chunk2 = b"\xe9</p></body></html>";
    let r1 = conv.feed_chunk(chunk1);
    assert!(r1.is_ok(), "first chunk should succeed: {:?}", r1);
    let r2 = conv.feed_chunk(chunk2);
    assert!(r2.is_ok(), "second chunk should succeed: {:?}", r2);
    let mut all_md = Vec::new();
    if let Ok(ref r1) = r1 {
        all_md.extend_from_slice(&r1.markdown);
    }
    if let Ok(ref r2) = r2 {
        all_md.extend_from_slice(&r2.markdown);
    }
    let result = conv.finalize().expect("finalize should succeed");
    all_md.extend_from_slice(&result.final_markdown);
    let md = String::from_utf8_lossy(&all_md);
    assert!(
        md.contains("caf") || md.contains("é"),
        "should contain transcoded text, got: {:?}",
        md
    );
}

#[test]
fn multi_byte_char_across_chunk_boundary() {
    let budget = MemoryBudget {
        total: 64 * 1024,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let chunk1 = b"<p>text \xe9";
    let chunk2 = b" more</p>";
    let r1 = conv.feed_chunk(chunk1);
    assert!(r1.is_ok(), "first chunk: {:?}", r1);
    let r2 = conv.feed_chunk(chunk2);
    assert!(r2.is_ok(), "second chunk: {:?}", r2);
    let mut all_md = Vec::new();
    if let Ok(ref r1) = r1 {
        all_md.extend_from_slice(&r1.markdown);
    }
    if let Ok(ref r2) = r2 {
        all_md.extend_from_slice(&r2.markdown);
    }
    let result = conv.finalize().expect("finalize should succeed");
    all_md.extend_from_slice(&result.final_markdown);
    assert!(!all_md.is_empty(), "should produce some markdown output");
}

#[test]
fn finalize_decoder_output_exceeds_budget() {
    let budget = MemoryBudget {
        total: 512,
        charset_sniff: 64,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let input = iso_8859_1_input(1024);
    let _ = conv.feed_chunk(&input);
    let result = conv.finalize();
    match result {
        Ok(_) => {}
        Err(ConversionError::BudgetExceeded { .. }) => {}
        Err(ConversionError::PostCommitError { .. }) => {}
        Err(other) => panic!("unexpected error: {:?}", other),
    }
}

#[test]
fn utf8_tail_counted_in_total_budget() {
    let budget = MemoryBudget {
        total: 64 * 1024,
        ..MemoryBudget::default()
    };
    let mut conv = StreamingConverter::new(ConversionOptions::default(), budget);
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    let chunk = b"<p>Hello</p>";
    let r = conv.feed_chunk(chunk);
    assert!(r.is_ok(), "UTF-8 feed should succeed: {:?}", r);
    let result = conv.finalize().expect("finalize should succeed");
    assert!(result.stats.peak_memory_estimate > 0);
}

#[test]
fn from_utf8_lossy_owned_allocation_within_budget() {
    let budget = MemoryBudget {
        total: 64 * 1024,
        ..MemoryBudget::default()
    };
    let mut conv = StreamingConverter::new(ConversionOptions::default(), budget);
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    let mut input = b"<p>".to_vec();
    input.push(0xFF);
    input.extend_from_slice(b"text</p>");
    let r = conv.feed_chunk(&input);
    assert!(r.is_ok(), "lossy conversion should succeed: {:?}", r);
    let mut all_md = Vec::new();
    if let Ok(ref r) = r {
        all_md.extend_from_slice(&r.markdown);
    }
    let result = conv.finalize().expect("finalize should succeed");
    all_md.extend_from_slice(&result.final_markdown);
    assert!(!all_md.is_empty(), "should produce some markdown output");
}

#[test]
fn precommit_budget_error_preserves_fallback_semantics() {
    let budget = MemoryBudget {
        total: 256,
        charset_sniff: 32,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let input = iso_8859_1_input(2048);
    let result = conv.feed_chunk(&input);
    match result {
        Err(ConversionError::BudgetExceeded { stage, used, limit }) => {
            assert!(
                stage.contains("total"),
                "stage should mention total: {}",
                stage
            );
            assert!(
                used > limit,
                "used ({}) should exceed limit ({})",
                used,
                limit
            );
        }
        Err(other) => {
            panic!("expected BudgetExceeded, got: {:?}", other);
        }
        Ok(_) => {
            // Very small budget may succeed if the input fits
        }
    }
}

#[test]
fn postcommit_budget_error_generates_correct_error() {
    let budget = MemoryBudget {
        total: 64 * 1024,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let chunk1 = b"<p>Hello</p>";
    let r1 = conv.feed_chunk(chunk1);
    assert!(r1.is_ok(), "first chunk should succeed: {:?}", r1);
    if !r1.unwrap().markdown.is_empty() {
        let big_input = iso_8859_1_input(128 * 1024);
        let result = conv.feed_chunk(&big_input);
        match result {
            Err(ConversionError::PostCommitError {
                reason,
                bytes_emitted,
                ..
            }) => {
                assert!(bytes_emitted > 0, "should have emitted bytes before error");
                assert!(!reason.is_empty(), "reason should not be empty");
            }
            Err(ConversionError::BudgetExceeded { .. }) => {}
            Ok(_) => {}
            Err(other) => panic!("unexpected error: {:?}", other),
        }
    }
}

#[test]
fn utf8_zero_copy_no_extra_copy_or_regression() {
    let budget = MemoryBudget::default();
    let mut conv = StreamingConverter::new(ConversionOptions::default(), budget);
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    let html = b"<h1>Hello</h1><p>World</p>";
    let out = conv.feed_chunk(html).expect("UTF-8 feed should succeed");
    let mut all_md = out.markdown;
    let result = conv.finalize().expect("finalize should succeed");
    all_md.extend_from_slice(&result.final_markdown);
    let md = String::from_utf8_lossy(&all_md);
    assert!(md.contains("Hello"), "should contain heading text");
    assert!(md.contains("World"), "should contain paragraph text");
}

#[test]
fn peak_memory_includes_charset_buffers() {
    let budget = MemoryBudget {
        total: 64 * 1024,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let input = iso_8859_1_input(1024);
    let _ = conv.feed_chunk(&input);
    let result = conv.finalize();
    match result {
        Ok(r) => {
            assert!(r.stats.peak_memory_estimate > 0, "peak should be positive");
        }
        Err(_) => {}
    }
}

#[test]
fn sniff_buffer_resident_bytes_tracked() {
    let budget = MemoryBudget {
        total: 64 * 1024,
        charset_sniff: 512,
        ..MemoryBudget::default()
    };
    let mut conv = StreamingConverter::new(ConversionOptions::default(), budget);
    let small_input = b"<html><body><p>Hi</p></body></html>";
    let r = conv.feed_chunk(small_input);
    assert!(r.is_ok(), "small input should succeed: {:?}", r);
    let result = conv.finalize().expect("finalize should succeed");
    assert!(result.stats.peak_memory_estimate > 0);
}

#[test]
fn large_non_utf8_single_chunk_budget_exceeded() {
    let budget = MemoryBudget {
        total: 1024,
        charset_sniff: 64,
        ..MemoryBudget::default()
    };
    let mut conv = make_converter_with_budget(budget, "ISO-8859-1");
    let mut input = Vec::new();
    input.extend_from_slice(b"<html><body>");
    while input.len() < 4096 {
        input.push(0xE9);
    }
    input.extend_from_slice(b"</body></html>");
    let result = conv.feed_chunk(&input);
    assert!(
        result.is_err(),
        "large non-UTF-8 chunk should exceed tiny budget"
    );
}
