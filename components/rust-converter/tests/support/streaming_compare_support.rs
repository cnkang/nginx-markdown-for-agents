#![cfg(feature = "streaming")]
#![allow(dead_code)]

use crate::known_differences::{KnownDifferences, OutputDifference};
use crate::streaming_test_support::normalize_whitespace_tokens;

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
