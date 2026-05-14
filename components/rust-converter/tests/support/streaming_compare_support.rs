//! Shared comparison support for streaming parity tests.
//!
//! Provides the `compare_or_known` function that checks full-buffer vs streaming
//! output equality, falling back to the known-differences registry when a mismatch
//! is observed. This avoids false positives for pre-approved divergences.

#![cfg(feature = "streaming")]
#![allow(dead_code)]

use crate::known_differences::{KnownDifferences, OutputDifference};
use crate::streaming_test_support::normalize_whitespace_tokens;

/// Compare full-buffer and streaming outputs, returning `Ok(())` if they match
/// or if the mismatch is explained by a known difference entry. Returns `Err`
/// with a diagnostic message if an unexpected divergence is detected.
pub fn compare_or_known(
    fixture_name: &str,
    full: &str,
    streaming: &str,
    known: &KnownDifferences,
) -> Result<(), String> {
    if full == streaming {
        return Ok(());
    }

    let mut diff = format!(
        "full_len={} streaming_len={} first_mismatch={:?}",
        full.len(),
        streaming.len(),
        full.chars()
            .zip(streaming.chars())
            .position(|(a, b)| a != b),
    );
    if normalize_whitespace_tokens(full) == normalize_whitespace_tokens(streaming) {
        diff = format!("whitespace-only-parity-drift\n{diff}");
    }

    let output = OutputDifference {
        full_buffer: full,
        streaming,
        diff: &diff,
    };

    if known.matches(fixture_name, &output).is_some() {
        return Ok(());
    }

    Err(format!(
        "{fixture_name}: differential mismatch\n{diff}\n--- full ---\n{full}\n--- streaming ---\n{streaming}"
    ))
}
