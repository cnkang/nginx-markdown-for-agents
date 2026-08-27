//! Reason code registry documentation sync test.
//!
//! Verifies that the reason code registry's string representations
//! are consistent and complete.

use std::collections::HashSet;

use nginx_markdown_converter::decision::reason_code::{ALL, REASON_CODE_COUNT};

fn normalize_label_value(value: &str) -> String {
    let replaced: String = value
        .chars()
        .map(|c| match c {
            '-' | ' ' => '_',
            _ => c,
        })
        .collect();
    let lowered = replaced.to_lowercase();
    let filtered: String = lowered
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == '_')
        .collect();
    let mut collapsed = String::with_capacity(filtered.len());
    let mut previous_underscore = false;
    for character in filtered.chars() {
        if character == '_' {
            if !previous_underscore {
                collapsed.push(character);
            }
            previous_underscore = true;
        } else {
            collapsed.push(character);
            previous_underscore = false;
        }
    }
    collapsed.trim_matches('_').to_string()
}

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
