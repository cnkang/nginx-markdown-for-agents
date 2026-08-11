//! FFI integration tests for the dynconf parser entry points.
//!
//! Verifies that `markdown_dynconf_parse`, `markdown_sha256_hex`, and
//! `markdown_dynconf_result_free` behave correctly across the FFI boundary,
//! including NULL-pointer handling, error classification, digest output,
//! and memory safety (no double-free, no leak on error paths).

use nginx_markdown_converter::dynconf::ffi::*;
use std::ptr;

// ─── Helpers ───────────────────────────────────────────────────────────────

/// Call `markdown_dynconf_parse` with the given bytes and return the
/// populated `FFIDynconfResult`.  The caller is responsible for freeing
/// the result via `markdown_dynconf_result_free`.
fn parse_dynconf_ffi(data: &[u8]) -> FFIDynconfResult {
    let mut result = FFIDynconfResult {
        error_code: u32::MAX,
        error_message: ptr::null(),
        error_message_len: 0,
        source_digest: ptr::null(),
        source_digest_len: 0,
        active_digest: ptr::null(),
        active_digest_len: 0,
        filter: 0,
        prune_noise: 0,
        log_verbosity: 0,
        error_policy: 0,
        streaming_buffer: 0,
    };
    unsafe {
        markdown_dynconf_result_init(&mut result);
        markdown_dynconf_parse(data.as_ptr(), data.len(), &mut result);
    }
    result
}

/// Free a result and verify all pointers are cleared afterwards.
fn free_and_verify(result: &mut FFIDynconfResult) {
    unsafe {
        markdown_dynconf_result_free(result);
    }
    assert!(result.error_message.is_null());
    assert_eq!(result.error_message_len, 0);
    assert!(result.source_digest.is_null());
    assert_eq!(result.source_digest_len, 0);
    assert!(result.active_digest.is_null());
    assert_eq!(result.active_digest_len, 0);
}

/// Extract a digest field as an owned `Vec<u8>`.
fn digest_bytes(ptr: *const u8, len: usize) -> Vec<u8> {
    if ptr.is_null() || len == 0 {
        return Vec::new();
    }
    unsafe { std::slice::from_raw_parts(ptr, len).to_vec() }
}

const VALID_DOC: &[u8] = br#"{"schema_version": 1, "filter": "on"}"#;

// ─── markdown_dynconf_parse: success path ──────────────────────────────────

#[test]
fn parse_valid_json_returns_success() {
    let mut result = parse_dynconf_ffi(VALID_DOC);
    assert_eq!(result.error_code, DYNCONF_OK);
    assert!(result.error_message.is_null());
    assert_eq!(result.error_message_len, 0);
    assert_eq!(result.source_digest_len, 64);
    assert_eq!(result.active_digest_len, 64);
    assert!(!result.source_digest.is_null());
    assert!(!result.active_digest.is_null());
    assert_eq!(result.filter, DYNCONF_FILTER_ON);
    free_and_verify(&mut result);
}

#[test]
fn parse_valid_json_digests_are_hex() {
    let mut result = parse_dynconf_ffi(VALID_DOC);
    assert_eq!(result.error_code, DYNCONF_OK);
    let src = digest_bytes(result.source_digest, result.source_digest_len);
    let act = digest_bytes(result.active_digest, result.active_digest_len);
    assert_eq!(src.len(), 64);
    assert_eq!(act.len(), 64);
    // All bytes must be hex characters
    for &b in src.iter().chain(act.iter()) {
        assert!(b.is_ascii_hexdigit(), "non-hex byte in digest: {b:#x}");
    }
    free_and_verify(&mut result);
}

#[test]
fn parse_valid_json_error_policy_is_not_set() {
    let mut result = parse_dynconf_ffi(VALID_DOC);
    assert_eq!(result.error_code, DYNCONF_OK);
    assert_eq!(result.error_policy, DYNCONF_NOT_SET_U8);
    assert_eq!(result.streaming_buffer, DYNCONF_NOT_SET_U64);
    free_and_verify(&mut result);
}

// ─── markdown_dynconf_parse: error paths ───────────────────────────────────

#[test]
fn parse_null_data_nonzero_len_returns_invalid_json() {
    let mut result = FFIDynconfResult {
        error_code: u32::MAX,
        error_message: ptr::null(),
        error_message_len: 0,
        source_digest: ptr::null(),
        source_digest_len: 0,
        active_digest: ptr::null(),
        active_digest_len: 0,
        filter: 0,
        prune_noise: 0,
        log_verbosity: 0,
        error_policy: 0,
        streaming_buffer: 0,
    };
    unsafe {
        markdown_dynconf_result_init(&mut result);
        markdown_dynconf_parse(ptr::null(), 10, &mut result);
    }
    assert_eq!(result.error_code, DYNCONF_ERR_INVALID_JSON);
    assert!(!result.error_message.is_null());
    assert!(result.error_message_len > 0);
    assert!(result.source_digest.is_null());
    assert!(result.active_digest.is_null());
    free_and_verify(&mut result);
}

#[test]
fn parse_empty_data_returns_error() {
    let mut result = parse_dynconf_ffi(&[]);
    assert_ne!(result.error_code, DYNCONF_OK);
    assert!(result.source_digest.is_null());
    assert!(result.active_digest.is_null());
    free_and_verify(&mut result);
}

#[test]
fn parse_invalid_json_returns_error() {
    let mut result = parse_dynconf_ffi(b"not json at all");
    assert_eq!(result.error_code, DYNCONF_ERR_INVALID_JSON);
    assert!(!result.error_message.is_null());
    assert!(result.error_message_len > 0);
    assert!(result.source_digest.is_null());
    assert!(result.active_digest.is_null());
    free_and_verify(&mut result);
}

#[test]
fn parse_missing_schema_version_returns_error() {
    let mut result = parse_dynconf_ffi(br#"{"filter": "on"}"#);
    assert_eq!(result.error_code, DYNCONF_ERR_MISSING_SCHEMA_VERSION);
    free_and_verify(&mut result);
}

#[test]
fn parse_unknown_key_returns_error() {
    let mut result = parse_dynconf_ffi(br#"{"schema_version": 1, "unknown_key": "secret-value"}"#);
    assert_eq!(result.error_code, DYNCONF_ERR_UNKNOWN_KEY);
    let message = digest_bytes(result.error_message, result.error_message_len);
    let message = std::str::from_utf8(&message).unwrap();
    assert!(message.contains("unknown key"));
    assert!(!message.contains("secret-value"));
    assert!(message.len() <= 512);
    free_and_verify(&mut result);
}

#[test]
fn parse_null_result_pointer_is_no_op() {
    // Must not crash
    unsafe {
        markdown_dynconf_parse(VALID_DOC.as_ptr(), VALID_DOC.len(), ptr::null_mut());
    }
}

// ─── markdown_dynconf_result_free: safety ──────────────────────────────────

#[test]
fn free_null_pointer_is_no_op() {
    unsafe {
        markdown_dynconf_result_free(ptr::null_mut());
    }
}

#[test]
fn free_on_init_only_result_is_safe() {
    let mut result = FFIDynconfResult {
        error_code: DYNCONF_ERR_INTERNAL,
        error_message: ptr::null(),
        error_message_len: 0,
        source_digest: ptr::null(),
        source_digest_len: 0,
        active_digest: ptr::null(),
        active_digest_len: 0,
        filter: DYNCONF_NOT_SET_U8,
        prune_noise: DYNCONF_NOT_SET_U8,
        log_verbosity: DYNCONF_NOT_SET_U8,
        error_policy: DYNCONF_NOT_SET_U8,
        streaming_buffer: DYNCONF_NOT_SET_U64,
    };
    free_and_verify(&mut result);
    assert!(result.error_message.is_null());
    assert_eq!(result.error_message_len, 0);
    assert!(result.source_digest.is_null());
    assert_eq!(result.source_digest_len, 0);
    assert!(result.active_digest.is_null());
    assert_eq!(result.active_digest_len, 0);
}

#[test]
fn double_free_after_successful_parse_is_safe() {
    // After free_and_verify, all pointers are NULL.  Calling free again
    // should be a no-op because the function checks for NULL+len==0.
    let mut result = parse_dynconf_ffi(VALID_DOC);
    free_and_verify(&mut result);
    // Second free — must not crash or double-free
    unsafe {
        markdown_dynconf_result_free(&mut result);
    }
    assert!(result.error_message.is_null());
    assert_eq!(result.error_message_len, 0);
    assert!(result.source_digest.is_null());
    assert_eq!(result.source_digest_len, 0);
    assert!(result.active_digest.is_null());
    assert_eq!(result.active_digest_len, 0);
}

#[test]
fn parse_reuses_result_without_leaking_or_retaining_optional_values() {
    let mut result = parse_dynconf_ffi(VALID_DOC);
    assert_eq!(result.error_code, DYNCONF_OK);
    assert_eq!(result.filter, DYNCONF_FILTER_ON);

    unsafe {
        markdown_dynconf_parse(
            br#"{"schema_version": 1, "unknown_key": "secret-value"}"#.as_ptr(),
            br#"{"schema_version": 1, "unknown_key": "secret-value"}"#.len(),
            &mut result,
        );
    }

    assert_eq!(result.error_code, DYNCONF_ERR_UNKNOWN_KEY);
    assert!(result.source_digest.is_null());
    assert!(result.active_digest.is_null());
    assert_eq!(result.filter, DYNCONF_NOT_SET_U8);
    free_and_verify(&mut result);
}

// ─── markdown_sha256_hex ───────────────────────────────────────────────────

#[test]
fn sha256_hex_valid_input_returns_ok_and_64_bytes() {
    let input = b"hello world";
    let mut output = [0u8; 64];
    let rc = unsafe {
        markdown_sha256_hex(
            input.as_ptr(),
            input.len(),
            output.as_mut_ptr(),
            output.len(),
        )
    };
    assert_eq!(rc, DYNCONF_OK);
    // SHA-256 of "hello world" is a known value
    let hex = std::str::from_utf8(&output).unwrap();
    assert_eq!(
        hex,
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    );
}

#[test]
fn sha256_hex_empty_input_returns_ok() {
    let mut output = [0u8; 64];
    let rc = unsafe { markdown_sha256_hex(ptr::null(), 0, output.as_mut_ptr(), output.len()) };
    assert_eq!(rc, DYNCONF_OK);
    // SHA-256 of empty string
    let hex = std::str::from_utf8(&output).unwrap();
    assert_eq!(
        hex,
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
}

#[test]
fn sha256_hex_null_output_returns_invalid_type() {
    let input = b"test";
    let rc = unsafe { markdown_sha256_hex(input.as_ptr(), input.len(), ptr::null_mut(), 64) };
    assert_eq!(rc, DYNCONF_ERR_INVALID_TYPE);
}

#[test]
fn sha256_hex_short_output_returns_invalid_type() {
    let input = b"test";
    let mut output = [0u8; 32];
    let rc = unsafe { markdown_sha256_hex(input.as_ptr(), input.len(), output.as_mut_ptr(), 32) };
    assert_eq!(rc, DYNCONF_ERR_INVALID_TYPE);
}

#[test]
fn sha256_hex_null_data_nonzero_len_returns_invalid_type() {
    let mut output = [0u8; 64];
    let rc = unsafe { markdown_sha256_hex(ptr::null(), 10, output.as_mut_ptr(), output.len()) };
    assert_eq!(rc, DYNCONF_ERR_INVALID_TYPE);
}

// ─── markdown_dynconf_result_init ──────────────────────────────────────────

#[test]
fn result_init_sets_safe_defaults() {
    let mut result = FFIDynconfResult {
        error_code: 0,
        error_message: 42 as *const u8,
        error_message_len: 999,
        source_digest: 42 as *const u8,
        source_digest_len: 999,
        active_digest: 42 as *const u8,
        active_digest_len: 999,
        filter: 0,
        prune_noise: 0,
        log_verbosity: 0,
        error_policy: 0,
        streaming_buffer: 0,
    };
    unsafe {
        markdown_dynconf_result_init(&mut result);
    }
    assert_eq!(result.error_code, DYNCONF_ERR_INTERNAL);
    assert!(result.error_message.is_null());
    assert_eq!(result.error_message_len, 0);
    assert!(result.source_digest.is_null());
    assert_eq!(result.source_digest_len, 0);
    assert!(result.active_digest.is_null());
    assert_eq!(result.active_digest_len, 0);
    assert_eq!(result.filter, DYNCONF_NOT_SET_U8);
    assert_eq!(result.prune_noise, DYNCONF_NOT_SET_U8);
    assert_eq!(result.log_verbosity, DYNCONF_NOT_SET_U8);
    assert_eq!(result.error_policy, DYNCONF_NOT_SET_U8);
    assert_eq!(result.streaming_buffer, DYNCONF_NOT_SET_U64);
}

#[test]
fn result_init_null_pointer_is_no_op() {
    unsafe {
        markdown_dynconf_result_init(ptr::null_mut());
    }
}
