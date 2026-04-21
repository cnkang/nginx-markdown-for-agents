//! Tests for streaming converter failure and error-handling paths.
//!
//! Validates that the streaming converter gracefully handles adverse conditions
//! including memory budget exhaustion (returning `BudgetExceeded`), timeout
//! expiration (returning `Timeout` or `PostCommitError`), and malformed HTML
//! input (no panics). Also uses proptest to fuzz the converter with random
//! byte sequences, ensuring robustness against arbitrary input without
//! panicking or producing invalid state.

#![cfg(feature = "streaming")]

#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::time::Duration;

use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::streaming::{MemoryBudget, StreamingConverter};
use proptest::prelude::*;

fn make_converter_with_budget(budget: MemoryBudget) -> StreamingConverter {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), budget);
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    conv
}

#[test]
fn budget_exceeded_returns_error() {
    let budget = MemoryBudget {
        total: 512,
        state_stack: 128,
        output_buffer: 128,
        charset_sniff: 64,
        lookahead: 64,
    };

    let html = format!(
        "<!DOCTYPE html><html><body><p>{}</p></body></html>",
        "x".repeat(16 * 1024)
    );

    let mut conv = make_converter_with_budget(budget);
    let err = conv.feed_chunk(html.as_bytes()).unwrap_err();
    assert!(matches!(err, ConversionError::BudgetExceeded { .. }));
}

#[test]
fn timeout_returns_timeout_error() {
    let budget = MemoryBudget::default();
    let mut conv = make_converter_with_budget(budget);
    conv.set_timeout(Duration::from_nanos(1));

    std::thread::sleep(Duration::from_millis(1));

    let html = vec![b'a'; 128 * 1024];
    let err = conv.feed_chunk(&html).unwrap_err();
    assert!(
        matches!(
            err,
            ConversionError::Timeout | ConversionError::PostCommitError { .. }
        ),
        "expected Timeout or PostCommitError, got {err:?}"
    );
}

#[test]
fn malformed_html_no_panic_cases() {
    let malformed_cases: [&[u8]; 6] = [
        b"<html><body><div><p>unclosed",
        b"<html><body><p><div>wrong nesting</p></div>",
        b"<html><body><a href='broken\" attr'>x</a></body></html>",
        b"<html><body><img src=\"x\" alt=\"y\" <p>broken",
        b"<html><body>\xFF\xFE\xFA random bytes",
        b"<html><body><script><div>not closed",
    ];

    for case in malformed_cases {
        let result = catch_unwind(AssertUnwindSafe(|| {
            let mut conv = make_converter_with_budget(MemoryBudget::default());
            let _ = conv.feed_chunk(case);
            let _ = conv.finalize();
        }));
        assert!(result.is_ok(), "converter panicked on malformed input");
    }
}

#[test]
fn precommit_streaming_fallback_has_no_markdown_leak() {
    let mut conv = make_converter_with_budget(MemoryBudget::default());

    let prefix = b"<!DOCTYPE html><html><body>";
    let prefix_out = conv
        .feed_chunk(prefix)
        .expect("prefix should not fail before table");
    assert!(
        prefix_out.markdown.is_empty(),
        "pre-commit prefix should not emit markdown"
    );

    let table = b"<table><tr><td>cell</td></tr></table>";
    let err = conv.feed_chunk(table).unwrap_err();

    assert!(
        matches!(err, ConversionError::StreamingFallback { .. }),
        "expected pre-commit StreamingFallback, got {err:?}"
    );
}

#[test]
fn oversize_input_small_streaming_budget_errors() {
    let tiny_budget = MemoryBudget {
        total: 1024,
        state_stack: 256,
        output_buffer: 256,
        charset_sniff: 128,
        lookahead: 256,
    };

    let mut conv = make_converter_with_budget(tiny_budget);
    let html = format!(
        "<!DOCTYPE html><html><body>{}</body></html>",
        "<p>oversize</p>".repeat(200)
    );

    let err = conv.feed_chunk(html.as_bytes()).unwrap_err();
    assert!(
        matches!(
            err,
            ConversionError::BudgetExceeded { .. }
                | ConversionError::MemoryLimit(_)
                | ConversionError::StreamingFallback { .. }
        ),
        "expected budget-related rejection, got {err:?}"
    );
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    #[test]
    fn prop_malformed_no_panic(data in prop::collection::vec(any::<u8>(), 0..8192)) {
        let result = catch_unwind(AssertUnwindSafe(|| {
            let mut conv = make_converter_with_budget(MemoryBudget::default());
            let _ = conv.feed_chunk(&data);
            let _ = conv.finalize();
        }));

        prop_assert!(result.is_ok(), "streaming converter panicked");
    }
}
