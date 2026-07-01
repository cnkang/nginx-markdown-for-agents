//! Reason code registry documentation sync test.
//!
//! Verifies that the reason code registry's string representations
//! are consistent and complete.

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

/// Verify total count matches ALL array
#[test]
fn test_reason_code_count_consistency() {
    assert_eq!(ALL.len(), REASON_CODE_COUNT);
    assert_eq!(ALL.len(), 25);
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
