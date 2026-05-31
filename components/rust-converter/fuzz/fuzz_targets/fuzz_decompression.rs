#![no_main]

//! Fuzz target for the production bounded decompression API.
//!
//! Drives [`nginx_markdown_converter::decompress::decompress_bounded`] directly
//! across all supported formats (gzip, deflate, brotli) and a range of budgets,
//! exercising the real budget-enforcement, format-dispatch, and error
//! classification code that ships in v0.7.0 — rather than re-implementing a
//! decompression loop against a third-party crate.
//!
//! Invariants checked:
//! - No panics or UB regardless of input.
//! - On success, the decompressed output never exceeds the configured budget.
//! - Every error maps to a known FFI error category code (101–104).

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::decompress::{DecompError, Format, decompress_bounded};

/// Derive a `Format` from a seed byte, covering all valid codes plus the
/// invalid-code rejection path of `Format::from_u8`.
fn pick_format(seed: u8) -> Option<Format> {
    /* Map most of the seed space onto valid formats while still exercising
     * the `None` (invalid code) branch for out-of-range values. */
    Format::from_u8(seed % 4)
}

/// Derive a decompression budget from a seed byte.
///
/// Spans tiny budgets (to exercise the `BudgetExceeded` path aggressively)
/// up to a 1 MiB cap (to exercise successful decompression of small inputs).
fn pick_budget(seed: u8) -> usize {
    match seed % 4 {
        0 => 0,
        1 => 64,
        2 => 4096,
        _ => 1024 * 1024,
    }
}

fuzz_target!(|data: &[u8]| {
    /* Reserve the first two bytes as format/budget selectors; the remainder
     * is the compressed payload. */
    if data.len() < 2 {
        return;
    }
    let format = match pick_format(data[0]) {
        Some(f) => f,
        None => return,
    };
    let budget = pick_budget(data[1]);
    let payload = &data[2..];

    match decompress_bounded(payload, format, budget) {
        Ok(result) => {
            /* Budget invariant: bounded decompression must never emit more
             * than the configured budget. */
            assert!(
                result.output.len() <= budget,
                "decompressed output {} exceeded budget {}",
                result.output.len(),
                budget
            );
        }
        Err(err) => {
            /* Every error must classify into a known FFI category code so the
             * C caller can act on it. */
            let category = err.error_category();
            assert!(
                (101..=104).contains(&category),
                "unexpected error category {} for {:?}",
                category,
                err
            );
            /* Spot-check the variant/category mapping is internally
             * consistent. */
            let expected = match err {
                DecompError::BudgetExceeded => 101,
                DecompError::FormatError(_) => 102,
                DecompError::TruncatedInput(_) => 103,
                DecompError::IoError(_) => 104,
            };
            assert_eq!(category, expected, "category/variant mismatch");
        }
    }
});
