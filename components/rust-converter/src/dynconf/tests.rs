//! Unit tests for the dynconf parser module.

use super::*;
use super::parser::DynconfParseErrorKind;

#[test]
fn test_minimal_valid_document() {
    let input = br#"{"schema_version": 1}"#;
    let result = parse_dynconf(input).unwrap();
    assert!(result.filter.is_none());
    assert!(result.prune_noise.is_none());
    assert!(result.log_verbosity.is_none());
    assert!(result.error_policy.is_none());
    assert!(result.streaming_buffer.is_none());
    assert_eq!(result.source_digest.len(), 64);
    assert_eq!(result.active_digest.len(), 64);
}

#[test]
fn test_full_valid_document() {
    let input = br#"{
        "schema_version": 1,
        "filter": "on",
        "prune_noise": "off",
        "log_verbosity": "info",
        "error_policy": "pass",
        "streaming_buffer": 2097152
    }"#;
    let result = parse_dynconf(input).unwrap();
    assert_eq!(result.filter, Some(schema::FilterValue::On));
    assert_eq!(result.prune_noise, Some(schema::PruneNoiseValue::Off));
    assert_eq!(result.log_verbosity, Some(schema::LogVerbosity::Info));
    assert_eq!(result.error_policy, Some(schema::ErrorPolicy::Pass));
    assert_eq!(result.streaming_buffer, Some(2_097_152));
}

#[test]
fn test_partial_keys() {
    let input = br#"{"schema_version": 1, "filter": "off", "streaming_buffer": 65536}"#;
    let result = parse_dynconf(input).unwrap();
    assert_eq!(result.filter, Some(schema::FilterValue::Off));
    assert!(result.prune_noise.is_none());
    assert!(result.log_verbosity.is_none());
    assert!(result.error_policy.is_none());
    assert_eq!(result.streaming_buffer, Some(65536));
}

#[test]
fn test_missing_schema_version() {
    let input = br#"{"filter": "on"}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::MissingSchemaVersion);
}

#[test]
fn test_wrong_schema_version() {
    let input = br#"{"schema_version": 2}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidSchemaVersion);
}

#[test]
fn test_schema_version_not_integer() {
    let input = br#"{"schema_version": "1"}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidSchemaVersion);
}

#[test]
fn test_unknown_key_rejected() {
    let input = br#"{"schema_version": 1, "unknown_field": true}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::UnknownKey);
}

#[test]
fn test_duplicate_key_rejected() {
    let input = br#"{"schema_version": 1, "filter": "on", "filter": "off"}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::DuplicateKey);
}

#[test]
fn test_invalid_json() {
    let input = br#"{schema_version: 1}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidJson);
}

#[test]
fn test_not_an_object() {
    let input = br#"[1, 2, 3]"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidJson);
}

#[test]
fn test_invalid_utf8() {
    let input: &[u8] = &[0x7b, 0xff, 0x7d]; // {<invalid>}
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidUtf8);
}

#[test]
fn test_document_too_large() {
    let large = vec![b' '; MAX_DOCUMENT_SIZE + 1];
    let err = parse_dynconf(&large).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::DocumentTooLarge);
}

#[test]
fn test_nesting_depth_exceeded() {
    // Create deeply nested JSON: {{{{{{{{{}}}}}}}}}  (9 levels)
    let mut input = String::new();
    for _ in 0..9 {
        input.push_str(r#"{"a":"#);
    }
    input.push_str(r#""x""#);
    for _ in 0..9 {
        input.push('}');
    }
    let err = parse_dynconf(input.as_bytes()).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::NestingDepthExceeded);
}

#[test]
fn test_streaming_buffer_valid_min() {
    let input = br#"{"schema_version": 1, "streaming_buffer": 65536}"#;
    let result = parse_dynconf(input).unwrap();
    assert_eq!(result.streaming_buffer, Some(65536));
}

#[test]
fn test_streaming_buffer_valid_max() {
    let input = br#"{"schema_version": 1, "streaming_buffer": 1073741824}"#;
    let result = parse_dynconf(input).unwrap();
    assert_eq!(result.streaming_buffer, Some(1_073_741_824));
}

#[test]
fn test_streaming_buffer_below_min() {
    let input = br#"{"schema_version": 1, "streaming_buffer": 65535}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::ValueOutOfRange);
}

#[test]
fn test_streaming_buffer_above_max() {
    let input = br#"{"schema_version": 1, "streaming_buffer": 1073741825}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::ValueOutOfRange);
}

#[test]
fn test_streaming_buffer_string_rejected() {
    let input = br#"{"schema_version": 1, "streaming_buffer": "2m"}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidType);
}

#[test]
fn test_streaming_buffer_float_rejected() {
    let input = br#"{"schema_version": 1, "streaming_buffer": 65536.0}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidType);
}

#[test]
fn test_streaming_buffer_leading_zero_rejected() {
    let input = br#"{"schema_version": 1, "streaming_buffer": 065536}"#;
    // Leading zeros in JSON numbers are already rejected by the parser as invalid JSON
    let err = parse_dynconf(input).unwrap_err();
    // The JSON parser itself rejects "065536" because after a leading 0,
    // only '.', 'e', 'E' or end are valid per RFC 8259
    assert!(
        err.kind == DynconfParseErrorKind::InvalidJson
            || err.kind == DynconfParseErrorKind::ValueOutOfRange
    );
}

#[test]
fn test_streaming_buffer_negative_rejected() {
    let input = br#"{"schema_version": 1, "streaming_buffer": -1}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::ValueOutOfRange);
}

#[test]
fn test_filter_invalid_value() {
    let input = br#"{"schema_version": 1, "filter": "maybe"}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::ValueOutOfRange);
}

#[test]
fn test_filter_wrong_type() {
    let input = br#"{"schema_version": 1, "filter": true}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidType);
}

#[test]
fn test_log_verbosity_all_values() {
    for level in &["error", "warn", "info", "debug"] {
        let input = format!(r#"{{"schema_version": 1, "log_verbosity": "{}"}}"#, level);
        let result = parse_dynconf(input.as_bytes()).unwrap();
        assert!(result.log_verbosity.is_some());
    }
}

#[test]
fn test_error_policy_all_values() {
    for policy in &["pass", "fail_closed", "status 429", "status 503"] {
        let input = format!(
            r#"{{"schema_version": 1, "error_policy": "{}"}}"#,
            policy
        );
        let result = parse_dynconf(input.as_bytes()).unwrap();
        assert!(result.error_policy.is_some());
    }
}

#[test]
fn test_source_digest_changes_with_formatting() {
    let input1 = br#"{"schema_version": 1, "filter": "on"}"#;
    let input2 = br#"{"schema_version":1,"filter":"on"}"#;
    let r1 = parse_dynconf(input1).unwrap();
    let r2 = parse_dynconf(input2).unwrap();
    // Source digest differs (different bytes)
    assert_ne!(r1.source_digest, r2.source_digest);
    // Active digest is the same (same semantic content)
    assert_eq!(r1.active_digest, r2.active_digest);
}

#[test]
fn test_active_digest_differs_when_key_absent_vs_present() {
    let input1 = br#"{"schema_version": 1}"#;
    let input2 = br#"{"schema_version": 1, "filter": "on"}"#;
    let r1 = parse_dynconf(input1).unwrap();
    let r2 = parse_dynconf(input2).unwrap();
    assert_ne!(r1.active_digest, r2.active_digest);
}

#[test]
fn test_key_order_does_not_affect_active_digest() {
    // Even though JSON key order differs, active_digest normalizes
    let input1 = br#"{"schema_version": 1, "filter": "on", "prune_noise": "off"}"#;
    let input2 = br#"{"prune_noise": "off", "schema_version": 1, "filter": "on"}"#;
    let r1 = parse_dynconf(input1).unwrap();
    let r2 = parse_dynconf(input2).unwrap();
    assert_eq!(r1.active_digest, r2.active_digest);
}

#[test]
fn test_static_only_keys_rejected() {
    // conversion_timeout is static-only, not in dynconf
    let input = br#"{"schema_version": 1, "conversion_timeout": "30s"}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::UnknownKey);
}

#[test]
fn test_legacy_names_rejected() {
    // streaming_budget and memory_budget do not exist
    let input = br#"{"schema_version": 1, "streaming_budget": 2097152}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::UnknownKey);

    let input = br#"{"schema_version": 1, "memory_budget": 2097152}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::UnknownKey);
}

#[test]
fn test_empty_object_missing_schema_version() {
    let input = br#"{}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::MissingSchemaVersion);
}

#[test]
fn test_trailing_content_rejected() {
    let input = br#"{"schema_version": 1} extra"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidJson);
}

#[test]
fn test_null_value_rejected_for_filter() {
    let input = br#"{"schema_version": 1, "filter": null}"#;
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidType);
}

#[test]
fn test_token_budget_exceeded() {
    // Create a JSON with many keys (all unknown, but token budget hit first)
    // Actually, let's make a deeply nested array to consume tokens
    let mut input = String::from(r#"{"schema_version": 1, "filter": "#);
    // Nested arrays won't work since additionalProperties: false
    // Just use a value with many tokens - an array won't be accepted as filter value
    // Instead test with a document that has many entries
    input.clear();
    input.push('{');
    input.push_str(r#""schema_version": 1"#);
    // The token budget test relies on the parser consuming tokens for each value
    // For a practical test, we can lower the budget temporarily via the internal API
    // Here we just verify the error type exists
    // Create a moderately complex doc and rely on the parser internals
    input.push('}');
    // This won't exceed 10000 tokens. We need a special test with the internal API.
    // Let's test via parse_json_with_budget directly with a tiny budget
    use super::parser::parse_json_with_budget;
    let doc = br#"{"schema_version": 1, "filter": "on"}"#;
    let err = parse_json_with_budget(doc, 8, 2).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::TokenBudgetExceeded);
}

#[test]
fn test_empty_input() {
    let input = b"";
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidJson);
}

#[test]
fn test_whitespace_only() {
    let input = b"   \n\t  ";
    let err = parse_dynconf(input).unwrap_err();
    assert_eq!(err.kind, DynconfParseErrorKind::InvalidJson);
}
