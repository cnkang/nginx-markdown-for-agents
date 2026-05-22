#![no_main]

//! Fuzz target: streaming_chunks — chunk-boundary invariants with enriched input model.
//!
//! # Current Implementation Mode
//!
//! This target exercises the **true streaming pipeline** (`StreamingConverter`)
//! which processes HTML incrementally through charset detection → tokenization →
//! sanitization → state machine → emission. It is NOT a buffer-before-conversion
//! abstraction; each `feed_chunk` call drives real incremental processing.
//!
//! ## Extension Plan
//!
//! - Future: add mode bits for charset variation (non-UTF-8 feeds)
//! - Future: exercise `set_content_type` with fuzz-derived charset strings
//! - Future: inject cooperative timeout deadlines to test timeout paths
//!
//! # Input Model
//!
//! ```text
//! byte 0        — mode flags (bit 0: EOF flag, bit 1: reserved, bit 2-3: split strategy)
//! byte 1        — chunk count seed length (mod 32, capped at data.len() - 2)
//! bytes 2..2+N  — split point seed bytes (N = chunk_seed_len)
//! bytes 2+N..   — HTML body
//! ```
//!
//! The `derive_chunks()` function deterministically maps these bytes into a
//! chunk-size sequence using the shared `split_sizes_from_seed` helper. This
//! produces a mix of tiny (1-byte), medium, and larger chunks that exercise
//! UTF-8 tail buffering, tokenizer boundaries, and sanitizer state transitions.
//!
//! # Invariants
//!
//! 1. **No panic**: arbitrary chunk boundaries never trigger panic/unwrap/expect.
//! 2. **Stability**: zero-length chunks, consecutive empty chunks, 1-byte chunks,
//!    and large numbers of small chunks (>1000) all complete without crash.
//! 3. **No memory safety violations**: no double-free, use-after-free, or
//!    state-machine overflow (verified by ASan/MSan under cargo-fuzz).
//! 4. **Consistency (Confluence)**: single-pass full-input conversion vs chunked
//!    conversion must agree — both succeed or both fail; when both succeed,
//!    output matches after ASCII whitespace normalization.
//!
//! # Failure Definition
//!
//! - **panic** = logic defect (P1 issue)
//! - **consistency violation** = logic defect (P1 issue)
//! - **sanitizer report** (ASan/UBSan/MSan) = security defect (security advisory)
//! - **timeout** (>60s) = performance defect (P2 issue)

use libfuzzer_sys::fuzz_target;
mod streaming_utils;

use streaming_utils::{convert, normalize_ascii_whitespace, split_sizes_from_seed};

/// Derive chunk split sequence and HTML body from fuzz input bytes.
///
/// # Input layout
///
/// - `data[0]`: mode flags (bit 0 = EOF flag, currently unused by convert helper)
/// - `data[1]`: seed length indicator (mod 32, capped)
/// - `data[2..2+seed_len]`: split-point seed bytes
/// - `data[2+seed_len..]`: HTML body
///
/// # Returns
///
/// `(chunk_sizes, html_body)` — deterministic split sequence and the HTML slice.
fn derive_chunks(data: &[u8]) -> (Vec<usize>, &[u8]) {
    if data.len() < 3 {
        // Too short to parse structure; treat entire input as single-chunk HTML.
        return (vec![data.len().max(1)], data);
    }

    let _mode = data[0]; // Reserved for future mode bits (EOF flag, split strategy)
    let chunk_seed_len = (usize::from(data[1]) % 32).min(data.len() - 2);
    let seed_bytes = &data[2..2 + chunk_seed_len];
    let html = &data[2 + chunk_seed_len..];

    let splits = split_sizes_from_seed(seed_bytes, html.len());
    (splits, html)
}

fuzz_target!(|data: &[u8]| {
    let (split_sizes, html) = derive_chunks(data);

    // Guard: skip empty HTML to avoid trivial no-op comparisons.
    if html.is_empty() {
        return;
    }

    // Single-pass conversion (one chunk = full input).
    let single_res = convert(html, &[html.len()]);

    // Chunked conversion using fuzz-derived split sizes.
    let chunked_res = convert(html, &split_sizes);

    // Consistency assertion: success/error parity.
    assert_eq!(
        single_res.is_err(),
        chunked_res.is_err(),
        "streaming_chunks: chunk split changed success/error parity: \
         split_sizes={split_sizes:?}, html_len={}",
        html.len()
    );

    // When both succeed, output must match after whitespace normalization.
    if let (Ok(single), Ok(chunked)) = (single_res, chunked_res) {
        if single != chunked {
            assert_eq!(
                normalize_ascii_whitespace(&single),
                normalize_ascii_whitespace(&chunked),
                "streaming_chunks: chunk split changed output beyond whitespace drift, \
                 split_sizes={split_sizes:?}, html_len={}",
                html.len()
            );
        }
    }
});
