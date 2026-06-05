//! Bounded memory and streaming incrementality tests.
//!
//! **Task 6.1**: Verify that peak memory does NOT grow linearly with input size.
//! Feed a large input (1 MB) through the streaming converter in small chunks and
//! assert that `peak_memory_estimate` stays bounded (within budget, not O(input_size)).
//!
//! **Task 6.2**: Verify true streaming — output appears before all input is consumed.
//! Feed HTML in multiple chunks and assert that non-empty output is produced after
//! the first few chunks, before the final chunk/finalize.
//!
//! **Validates: Requirements 5.1, 5.2, 6.1, 6.2**

#![cfg(feature = "streaming")]

#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use nginx_markdown_converter::streaming::{MemoryBudget, StreamingConverter};
use streaming_test_support::{
    convert_streaming_chunked, default_streaming_budget, default_streaming_options,
};

// ════════════════════════════════════════════════════════════════════
// Task 6.1: Large input bounded memory
// Validates: Requirements 5.1, 5.2
// ════════════════════════════════════════════════════════════════════

/// Generate HTML of approximately `target_bytes` using repeated paragraphs.
fn make_html_of_size(target_bytes: usize) -> Vec<u8> {
    let mut html = String::with_capacity(target_bytes + 256);
    html.push_str("<!DOCTYPE html><html><body>\n");
    let paragraph = "<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. \
                     Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>\n";
    while html.len() < target_bytes {
        html.push_str(paragraph);
    }
    html.push_str("</body></html>\n");
    html.into_bytes()
}

/// Verify that peak memory does NOT grow linearly with input size.
///
/// Strategy: feed two inputs of vastly different sizes (64 KB vs 1 MB) through
/// the streaming converter in 4 KB chunks. If memory were O(n), peak memory
/// would grow ~16x with ~16x input growth. We assert that peak memory growth
/// is less than 25% of input growth — proving bounded behavior.
///
/// **Validates: Requirements 5.1, 5.2**
#[test]
fn large_input_bounded_memory() {
    let small_size = 64 * 1024; // 64 KB
    let large_size = 1024 * 1024; // 1 MB

    // Use a generous budget so we don't hit BudgetExceeded
    let budget = MemoryBudget {
        total: 16 * 1024 * 1024, // 16 MiB
        state_stack: 512 * 1024,
        output_buffer: 4 * 1024 * 1024,
        charset_sniff: 1024,
        lookahead: 512 * 1024,
    };

    let chunk_size = 4 * 1024; // 4 KB chunks

    // Run with small input
    let small_html = make_html_of_size(small_size);
    let small_chunks: Vec<usize> =
        std::iter::repeat_n(chunk_size, small_html.len().div_ceil(chunk_size)).collect();
    let small_run = convert_streaming_chunked(
        &small_html,
        &small_chunks,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        budget.clone(),
        None,
    )
    .expect("small input streaming conversion should succeed");

    // Run with large input
    let large_html = make_html_of_size(large_size);
    let large_chunks: Vec<usize> =
        std::iter::repeat_n(chunk_size, large_html.len().div_ceil(chunk_size)).collect();
    let large_run = convert_streaming_chunked(
        &large_html,
        &large_chunks,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        budget.clone(),
        None,
    )
    .expect("large input streaming conversion should succeed");

    // Both should have non-zero peak memory
    assert!(
        small_run.stats.peak_memory_estimate > 0,
        "small input peak memory should be non-zero"
    );
    assert!(
        large_run.stats.peak_memory_estimate > 0,
        "large input peak memory should be non-zero"
    );

    // Peak memory should stay within budget
    assert!(
        large_run.stats.peak_memory_estimate <= budget.total,
        "peak memory {} exceeds budget {} for 1 MB input",
        large_run.stats.peak_memory_estimate,
        budget.total
    );

    // The key assertion: peak memory must NOT grow linearly.
    // Input grows ~16x (64KB -> 1MB), so if memory is O(n) peak would grow ~16x.
    // We assert peak growth is less than 25% of input growth — proving bounded behavior.
    let input_growth = large_html.len() as f64 / small_html.len() as f64;
    let peak_growth = large_run.stats.peak_memory_estimate as f64
        / small_run.stats.peak_memory_estimate.max(1) as f64;

    assert!(
        peak_growth < input_growth * 0.25,
        "Peak memory growth ({:.2}x) is too close to linear input growth ({:.2}x).\n\
         Small input: {} bytes, peak memory: {} bytes\n\
         Large input: {} bytes, peak memory: {} bytes\n\
         This suggests O(input_size) memory usage, violating bounded memory requirement.",
        peak_growth,
        input_growth,
        small_html.len(),
        small_run.stats.peak_memory_estimate,
        large_html.len(),
        large_run.stats.peak_memory_estimate,
    );
}

/// Additional check: peak memory stays bounded even with the default budget.
///
/// Feed 1 MB of HTML in small chunks using the default (2 MiB) budget.
/// Verify peak_memory_estimate stays within budget.total.
///
/// **Validates: Requirements 5.1, 5.2**
#[test]
fn large_input_within_default_budget() {
    let html = make_html_of_size(1024 * 1024); // 1 MB
    let budget = default_streaming_budget();
    let chunk_size = 4 * 1024;
    let chunks: Vec<usize> =
        std::iter::repeat_n(chunk_size, html.len().div_ceil(chunk_size)).collect();

    let run = convert_streaming_chunked(
        &html,
        &chunks,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        budget.clone(),
        None,
    )
    .expect("1 MB input should convert within default budget");

    assert!(
        run.stats.peak_memory_estimate <= budget.total,
        "peak memory {} exceeds default budget {} for 1 MB input",
        run.stats.peak_memory_estimate,
        budget.total
    );

    // Verify multiple chunks were processed (not buffered-then-convert)
    assert!(
        run.stats.chunks_processed > 10,
        "Expected many chunks to be processed (got {}), indicating streaming behavior",
        run.stats.chunks_processed
    );
}

// ════════════════════════════════════════════════════════════════════
// Task 6.2: Output before input complete
// Validates: Requirements 6.1, 6.2
// ════════════════════════════════════════════════════════════════════

/// Verify that non-empty output is produced before all input is consumed.
///
/// Strategy: Feed a substantial HTML document (many paragraphs) in small
/// chunks. Track which chunk first produces non-empty output. Assert that
/// output appears well before the final chunk, distinguishing true streaming
/// from buffered-then-convert.
///
/// **Validates: Requirements 6.1, 6.2**
#[test]
fn output_before_input_complete() {
    // Generate HTML with many paragraphs — enough to guarantee early output
    let mut html = String::new();
    html.push_str("<!DOCTYPE html><html><body>\n");
    for i in 0..200 {
        html.push_str(&format!(
            "<p>Paragraph number {} with enough text to trigger flushing.</p>\n",
            i
        ));
    }
    html.push_str("</body></html>\n");
    let html_bytes = html.as_bytes();

    // Feed in small chunks (1 KB each)
    let chunk_size = 1024;
    let total_chunks = html_bytes.len().div_ceil(chunk_size);

    let budget = MemoryBudget {
        total: 16 * 1024 * 1024,
        state_stack: 512 * 1024,
        output_buffer: 4 * 1024 * 1024,
        charset_sniff: 1024,
        lookahead: 512 * 1024,
    };

    let mut conv = StreamingConverter::new(default_streaming_options(), budget);
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

    let mut first_output_chunk: Option<usize> = None;
    let mut total_output_before_final = 0usize;
    let mut cursor = 0usize;

    for chunk_idx in 0..total_chunks {
        let end = (cursor + chunk_size).min(html_bytes.len());
        if cursor >= html_bytes.len() {
            break;
        }

        let output = conv
            .feed_chunk(&html_bytes[cursor..end])
            .expect("feed_chunk should succeed");

        if !output.markdown.is_empty() {
            if first_output_chunk.is_none() {
                first_output_chunk = Some(chunk_idx);
            }
            total_output_before_final += output.markdown.len();
        }

        cursor = end;
    }

    let result = conv.finalize().expect("finalize should succeed");

    // Assert output appeared before all input was consumed
    let first_chunk = first_output_chunk.expect(
        "Expected non-empty output from at least one feed_chunk call, \
         but no output was produced until finalize. This indicates \
         buffered-then-convert behavior, not true streaming.",
    );

    // Output should appear well before the final chunk — in the first half
    assert!(
        first_chunk < total_chunks / 2,
        "First output at chunk {}/{} — output should appear in the first half \
         of chunks for true streaming behavior (not near the end).",
        first_chunk,
        total_chunks
    );

    // Verify substantial output was produced before finalize
    let total_output = total_output_before_final + result.final_markdown.len();
    let pre_finalize_ratio = total_output_before_final as f64 / total_output.max(1) as f64;

    assert!(
        pre_finalize_ratio > 0.5,
        "Only {:.1}% of output was produced before finalize. \
         True streaming should produce most output incrementally. \
         Pre-finalize output: {} bytes, total: {} bytes",
        pre_finalize_ratio * 100.0,
        total_output_before_final,
        total_output
    );
}

/// Verify output incrementality with the test support harness (first_non_empty_at).
///
/// Uses the convert_streaming_chunked helper which tracks timing of first
/// non-empty output. For a sufficiently large input fed in many chunks,
/// first_non_empty_at should be Some (not None), proving output appeared
/// during chunk feeding rather than only at finalize time.
///
/// **Validates: Requirements 6.1, 6.2**
#[test]
fn first_output_during_feed_not_finalize() {
    let mut html = String::new();
    html.push_str("<!DOCTYPE html><html><body>\n");
    for i in 0..100 {
        html.push_str(&format!(
            "<p>Content block {} for streaming incrementality test.</p>\n",
            i
        ));
    }
    html.push_str("</body></html>\n");
    let html_bytes = html.as_bytes();

    let chunk_size = 512; // Small chunks to create many feed calls
    let chunks: Vec<usize> =
        std::iter::repeat_n(chunk_size, html_bytes.len().div_ceil(chunk_size)).collect();

    let budget = MemoryBudget {
        total: 16 * 1024 * 1024,
        state_stack: 512 * 1024,
        output_buffer: 4 * 1024 * 1024,
        charset_sniff: 1024,
        lookahead: 512 * 1024,
    };

    let run = convert_streaming_chunked(
        html_bytes,
        &chunks,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        budget,
        None,
    )
    .expect("streaming conversion should succeed");

    // first_non_empty_at should be populated (output during feed, not just finalize)
    assert!(
        run.first_non_empty_at.is_some(),
        "first_non_empty_at is None — no output was produced during feed_chunk calls. \
         This indicates the converter buffers everything until finalize, \
         which is not true streaming behavior."
    );

    // Multiple chunks should have been processed
    assert!(
        run.stats.chunks_processed > 5,
        "Expected multiple chunks to be processed (got {})",
        run.stats.chunks_processed
    );

    // Markdown output should be non-empty
    assert!(
        !run.markdown.is_empty(),
        "Streaming conversion produced no output at all"
    );
}
