//! Reason code registry documentation sync test.
//!
//! Verifies that the reason code registry's string representations
//! are consistent and complete.

use std::collections::HashSet;

use nginx_markdown_converter::decision::reason_code::{ALL, REASON_CODE_COUNT, ReasonCode};

/// All reason codes have a non-empty as_str() value
#[test]
fn test_all_reason_codes_have_string() {
    for rc in &ALL {
        assert!(!rc.as_str().is_empty(), "{:?} has empty string", rc);
    }
}

/// All reason codes have a non-empty metric_key() value
#[test]
fn test_all_reason_codes_have_metric_key() {
    for rc in &ALL {
        assert!(!rc.metric_key().is_empty(), "{:?} has empty metric_key", rc);
    }
}

/// The C metric-key accessor must agree with the Rust registry for valid codes.
#[test]
fn test_ffi_metric_keys_match_registry() {
    for rc in &ALL {
        let mut length = 0;
        let pointer = unsafe {
            nginx_markdown_converter::decision::reason_code::markdown_reason_code_metric_key(
                rc.discriminant(),
                &mut length,
            )
        };
        assert!(!pointer.is_null(), "FFI key missing for {:?}", rc);
        let bytes = unsafe { std::slice::from_raw_parts(pointer, length) };
        assert_eq!(
            std::str::from_utf8(bytes).expect("FFI metric key must be UTF-8"),
            rc.metric_key(),
            "FFI metric key mismatch for {:?}",
            rc
        );
    }
}

/// All reason codes have a non-empty log_callsite() value
#[test]
fn test_all_reason_codes_have_log_callsite() {
    for rc in &ALL {
        assert!(
            !rc.log_callsite().is_empty(),
            "{:?} has empty log_callsite",
            rc
        );
    }
}

/// Each reason code must identify a unique decision-log callsite.
#[test]
fn test_log_callsites_are_unique() {
    let mut seen = HashSet::new();
    for rc in &ALL {
        assert!(
            seen.insert(rc.log_callsite()),
            "duplicate log_callsite for {:?}: {}",
            rc,
            rc.log_callsite()
        );
    }
}

/// Verify total count matches ALL array
#[test]
fn test_reason_code_count_consistency() {
    assert_eq!(ALL.len(), REASON_CODE_COUNT);
    assert_eq!(ALL.len(), 27);
}

/// All reason code strings are valid as normalized label values
#[test]
fn test_all_reason_codes_are_valid_label_values() {
    use nginx_markdown_converter::metrics::normalize_label_value;
    for rc in &ALL {
        let s = rc.as_str();
        let normalized = normalize_label_value(s);
        assert_eq!(
            s, normalized,
            "{:?} as_str() '{}' is not already normalized (got '{}')",
            rc, s, normalized
        );
    }
}

/// Document the 0.8.x → 0.9.0 reason code string migration
#[test]
fn test_legacy_to_new_mapping() {
    // These are the OLD 0.8.x strings mapped to NEW 0.9.0 strings
    let mappings = &[
        ("CONVERTED", "converted", 0u32),
        ("SKIPPED_ACCEPT", "skipped_accept", 1),
        ("SKIPPED_NO_ACCEPT", "skipped_no_accept", 2),
        ("SKIPPED_CONDITIONAL", "skipped_conditional", 3),
        ("FAILED_DECOMPRESSION", "decompression_error", 4),
        (
            "DECOMPRESSION_BUDGET_EXCEEDED",
            "decompression_budget_exceeded",
            5,
        ),
        (
            "DECOMPRESSION_FORMAT_ERROR",
            "decompression_format_error",
            6,
        ),
        (
            "DECOMPRESSION_TRUNCATED_INPUT",
            "decompression_truncated_input",
            7,
        ),
        ("DECOMPRESSION_IO_ERROR", "decompression_io_error", 8),
        ("PARSE_TIMEOUT", "timeout", 9),
        ("PARSE_BUDGET_EXCEEDED", "budget_exceeded", 10),
        ("REPLAY_BUFFER_ERROR", "replay_error", 11),
        ("SKIPPED_ACCEPT_REJECT", "skipped_accept_reject", 12),
        ("FFI_CALL_ERROR", "ffi_panic", 13),
        ("NOT_ELIGIBLE", "not_eligible", 14),
        ("DISABLED", "disabled", 15),
        ("FAILED_OPEN", "failed_open", 16),
        ("FAILED_CLOSED", "failed_closed", 17),
    ];

    for (old_name, new_name, disc) in mappings {
        let rc = ReasonCode::from_discriminant(*disc)
            .unwrap_or_else(|| panic!("discriminant {} should be valid", disc));
        assert_eq!(
            rc.as_str(),
            *new_name,
            "Discriminant {} (old: '{}') should map to '{}' but got '{}'",
            disc,
            old_name,
            new_name,
            rc.as_str()
        );
    }
}
