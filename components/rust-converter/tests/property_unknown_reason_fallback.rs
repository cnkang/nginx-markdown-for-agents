//! Property-based tests for unknown reason code fallback (Property 12).
//!
//! **Validates: Requirements 6.6**
//!
//! For numeric values not in the registry (gaps, values > max discriminant),
//! verify that `ReasonCode::from_discriminant()` returns `None` and the C
//! accessor `reason_code_str()` returns NULL for invalid values.

use nginx_markdown_converter::decision::reason_code::{REASON_CODE_COUNT, ReasonCode};
use proptest::prelude::*;

/// The maximum valid discriminant in the current registry.
const MAX_VALID_DISCRIMINANT: u32 = (REASON_CODE_COUNT as u32) - 1;

// ─── Strategies ───────────────────────────────────────────────────────────────

/// Generate u32 values that are NOT valid discriminants (>= REASON_CODE_COUNT).
fn invalid_discriminant() -> impl Strategy<Value = u32> {
    (REASON_CODE_COUNT as u32)..=u32::MAX
}

// ─── Property Tests ───────────────────────────────────────────────────────────

proptest! {
    /// Property 12: from_discriminant returns None for all invalid values.
    ///
    /// For any numeric value >= REASON_CODE_COUNT (27), the function must
    /// return None, indicating no valid reason code exists for that
    /// discriminant.
    #[test]
    fn from_discriminant_returns_none_for_invalid_values(
        value in invalid_discriminant()
    ) {
        let result = ReasonCode::from_discriminant(value);
        prop_assert!(
            result.is_none(),
            "from_discriminant({}) should return None but got {:?}",
            value,
            result
        );
    }

    /// Property 12: FFI reason_code_str returns NULL for invalid discriminants.
    ///
    /// The C-callable `markdown_reason_code_str` must return a null pointer
    /// when given a discriminant outside the valid range, indicating the
    /// internal_unknown fallback path.
    #[test]
    fn ffi_reason_code_str_returns_null_for_invalid(
        value in invalid_discriminant()
    ) {
        let mut out_len: usize = 999;
        let ptr = unsafe {
            nginx_markdown_converter::decision::reason_code::markdown_reason_code_str(
                value,
                &mut out_len as *mut usize,
            )
        };
        prop_assert!(
            ptr.is_null(),
            "markdown_reason_code_str({}) should return NULL but got non-null pointer",
            value
        );
        prop_assert_eq!(
            out_len, 0,
            "out_len should be 0 for invalid discriminant {}, got {}",
            value, out_len
        );
    }

    /// Property 12: FFI metric key also returns NULL for invalid discriminants.
    ///
    /// The `markdown_reason_code_metric_key` C accessor must return NULL for
    /// unknown discriminants, consistent with the error handling path.
    #[test]
    fn ffi_metric_key_returns_null_for_invalid(
        value in invalid_discriminant()
    ) {
        let mut out_len: usize = 999;
        let ptr = unsafe {
            nginx_markdown_converter::decision::reason_code::markdown_reason_code_metric_key(
                value,
                &mut out_len as *mut usize,
            )
        };
        prop_assert!(
            ptr.is_null(),
            "markdown_reason_code_metric_key({}) should return NULL but got non-null pointer",
            value
        );
        prop_assert_eq!(
            out_len, 0,
            "out_len should be 0 for invalid discriminant {}, got {}",
            value, out_len
        );
    }

    /// Property 12: All valid discriminants round-trip correctly.
    ///
    /// Complementary check: every value in [0, REASON_CODE_COUNT) must
    /// successfully resolve, proving there are no gaps in the registry.
    #[test]
    fn valid_discriminants_always_resolve(
        value in 0u32..REASON_CODE_COUNT as u32
    ) {
        let result = ReasonCode::from_discriminant(value);
        prop_assert!(
            result.is_some(),
            "from_discriminant({}) should return Some but got None — gap in registry",
            value
        );
    }
}

/// Non-property test: verify boundary value at max_discriminant + 1.
#[test]
fn boundary_just_above_max_is_none() {
    assert_eq!(
        ReasonCode::from_discriminant(MAX_VALID_DISCRIMINANT + 1),
        None,
        "discriminant {} (max+1) must return None",
        MAX_VALID_DISCRIMINANT + 1
    );
}

/// Non-property test: verify the exact boundary — max valid discriminant.
#[test]
fn boundary_at_max_valid_is_some() {
    assert!(
        ReasonCode::from_discriminant(MAX_VALID_DISCRIMINANT).is_some(),
        "discriminant {} (max valid) must return Some",
        MAX_VALID_DISCRIMINANT
    );
}

/// Non-property test: verify u32::MAX returns None (extreme boundary).
#[test]
fn u32_max_returns_none() {
    assert_eq!(
        ReasonCode::from_discriminant(u32::MAX),
        None,
        "u32::MAX must return None"
    );
}
