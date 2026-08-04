//! FFI interface for the dynconf parser.
//!
//! Exposes the dynconf parser to C via a single entry point that accepts
//! raw bytes and returns a structured result with both digests, parsed
//! values, and error information.
//!
//! # Safety
//!
//! All FFI functions in this module accept raw pointers from C and must
//! validate them before dereferencing. The result struct is owned by Rust
//! and must be freed via `markdown_dynconf_result_free`.

use std::panic;
use std::ptr;

use super::{parse_dynconf, DynconfResult};
use super::schema::{FilterValue, PruneNoiseValue, LogVerbosity, ErrorPolicy};

/// FFI result code: success.
pub const DYNCONF_OK: u32 = 0;
/// FFI result code: document too large.
pub const DYNCONF_ERR_TOO_LARGE: u32 = 1;
/// FFI result code: invalid JSON.
pub const DYNCONF_ERR_INVALID_JSON: u32 = 2;
/// FFI result code: token budget exceeded.
pub const DYNCONF_ERR_TOKEN_BUDGET: u32 = 3;
/// FFI result code: nesting depth exceeded.
pub const DYNCONF_ERR_NESTING_DEPTH: u32 = 4;
/// FFI result code: duplicate key.
pub const DYNCONF_ERR_DUPLICATE_KEY: u32 = 5;
/// FFI result code: missing schema_version.
pub const DYNCONF_ERR_MISSING_SCHEMA_VERSION: u32 = 6;
/// FFI result code: invalid schema_version.
pub const DYNCONF_ERR_INVALID_SCHEMA_VERSION: u32 = 7;
/// FFI result code: unknown key.
pub const DYNCONF_ERR_UNKNOWN_KEY: u32 = 8;
/// FFI result code: invalid type for a value.
pub const DYNCONF_ERR_INVALID_TYPE: u32 = 9;
/// FFI result code: value out of range.
pub const DYNCONF_ERR_VALUE_OUT_OF_RANGE: u32 = 10;
/// FFI result code: invalid UTF-8.
pub const DYNCONF_ERR_INVALID_UTF8: u32 = 11;
/// FFI result code: internal panic.
pub const DYNCONF_ERR_INTERNAL: u32 = 255;

/// Sentinel value for "not present" in optional u8 fields.
pub const DYNCONF_NOT_SET_U8: u8 = 255;

/// Sentinel value for "not present" in optional u64 fields.
pub const DYNCONF_NOT_SET_U64: u64 = u64::MAX;

/// FFI filter value: on.
pub const DYNCONF_FILTER_ON: u8 = 0;
/// FFI filter value: off.
pub const DYNCONF_FILTER_OFF: u8 = 1;

/// FFI prune_noise value: on.
pub const DYNCONF_PRUNE_NOISE_ON: u8 = 0;
/// FFI prune_noise value: off.
pub const DYNCONF_PRUNE_NOISE_OFF: u8 = 1;

/// FFI log_verbosity value: error.
pub const DYNCONF_LOG_ERROR: u8 = 0;
/// FFI log_verbosity value: warn.
pub const DYNCONF_LOG_WARN: u8 = 1;
/// FFI log_verbosity value: info.
pub const DYNCONF_LOG_INFO: u8 = 2;
/// FFI log_verbosity value: debug.
pub const DYNCONF_LOG_DEBUG: u8 = 3;

/// FFI error_policy value: pass.
pub const DYNCONF_POLICY_PASS: u8 = 0;
/// FFI error_policy value: fail_closed.
pub const DYNCONF_POLICY_FAIL_CLOSED: u8 = 1;
/// FFI error_policy value: status 429.
pub const DYNCONF_POLICY_STATUS_429: u8 = 2;
/// FFI error_policy value: status 503.
pub const DYNCONF_POLICY_STATUS_503: u8 = 3;

/// C-compatible result struct for dynconf parsing.
///
/// All string fields (source_digest, active_digest, error_message) are
/// UTF-8 byte pointers with explicit lengths. They are owned by Rust and
/// must be freed via `markdown_dynconf_result_free`.
#[repr(C)]
pub struct FFIDynconfResult {
    /// Result code (0 = success, non-zero = error).
    pub error_code: u32,
    /// Error message bytes (NULL on success).
    pub error_message: *const u8,
    /// Error message byte length (0 on success).
    pub error_message_len: usize,

    /// Source digest (SHA-256 hex, 64 bytes). NULL on error.
    pub source_digest: *const u8,
    /// Source digest length (64 on success, 0 on error).
    pub source_digest_len: usize,

    /// Active digest (SHA-256 hex, 64 bytes). NULL on error.
    pub active_digest: *const u8,
    /// Active digest length (64 on success, 0 on error).
    pub active_digest_len: usize,

    /// Filter value (DYNCONF_FILTER_ON/OFF or DYNCONF_NOT_SET_U8).
    pub filter: u8,
    /// Prune noise value (DYNCONF_PRUNE_NOISE_ON/OFF or DYNCONF_NOT_SET_U8).
    pub prune_noise: u8,
    /// Log verbosity value (DYNCONF_LOG_* or DYNCONF_NOT_SET_U8).
    pub log_verbosity: u8,
    /// Error policy value (DYNCONF_POLICY_* or DYNCONF_NOT_SET_U8).
    pub error_policy: u8,
    /// Streaming buffer value in bytes (DYNCONF_NOT_SET_U64 if absent).
    pub streaming_buffer: u64,
}

/// Initialize an FFIDynconfResult to safe defaults.
///
/// # Safety
///
/// `result` must point to a valid, writable `FFIDynconfResult`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_dynconf_result_init(result: *mut FFIDynconfResult) {
    if result.is_null() {
        return;
    }
    unsafe {
        ptr::write(
            result,
            FFIDynconfResult {
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
            },
        );
    }
}

/// Parse and validate a dynconf JSON document.
///
/// This is the primary FFI entry point for dynconf parsing. The C module
/// reads the file, enforces the 1 MiB size cap, and passes the raw bytes
/// to this function. Rust performs all JSON parsing, validation, and
/// digest computation.
///
/// # Safety
///
/// - `data` must point to `data_len` bytes of readable memory, or be NULL
///   if `data_len` is 0.
/// - `result` must point to a valid, writable `FFIDynconfResult` that has
///   been initialized via `markdown_dynconf_result_init`.
/// - After a successful call, `result` contains heap-allocated strings that
///   must be freed via `markdown_dynconf_result_free`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_dynconf_parse(
    data: *const u8,
    data_len: usize,
    result: *mut FFIDynconfResult,
) {
    if result.is_null() {
        return;
    }

    // Initialize to safe defaults before any work
    unsafe { markdown_dynconf_result_init(result); }

    let outcome = panic::catch_unwind(|| {
        // Validate input
        if data.is_null() && data_len > 0 {
            return Err(make_error(
                DYNCONF_ERR_INVALID_JSON,
                "NULL data pointer with non-zero length".to_string(),
            ));
        }

        let raw_bytes = if data_len == 0 {
            &[]
        } else {
            unsafe { std::slice::from_raw_parts(data, data_len) }
        };

        match parse_dynconf(raw_bytes) {
            Ok(dynconf_result) => Ok(dynconf_result),
            Err(e) => Err(make_error(map_error_kind(&e.kind), e.message)),
        }
    });

    match outcome {
        Ok(Ok(dynconf_result)) => {
            unsafe { write_success(result, &dynconf_result); }
        }
        Ok(Err((code, msg))) => {
            unsafe { write_error(result, code, &msg); }
        }
        Err(_panic) => {
            unsafe {
                write_error(
                    result,
                    DYNCONF_ERR_INTERNAL,
                    "internal panic during dynconf parsing",
                );
            }
        }
    }
}

/// Free heap memory owned by an FFIDynconfResult.
///
/// After calling this function, all pointer fields in `result` become invalid.
/// It is safe to call this on a result that was initialized but never
/// populated (e.g., after a NULL pointer early-return).
///
/// # Safety
///
/// - `result` must point to a valid `FFIDynconfResult` that was populated
///   by `markdown_dynconf_parse` or initialized via `markdown_dynconf_result_init`.
/// - Must be called exactly once per successful `markdown_dynconf_parse` call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_dynconf_result_free(result: *mut FFIDynconfResult) {
    if result.is_null() {
        return;
    }

    let _ = panic::catch_unwind(|| {
        let r = unsafe { &*result };

        // Free error message
        if !r.error_message.is_null() && r.error_message_len > 0 {
            let _ = unsafe {
                Box::from_raw(ptr::slice_from_raw_parts_mut(
                    r.error_message as *mut u8,
                    r.error_message_len,
                ))
            };
        }

        // Free source digest
        if !r.source_digest.is_null() && r.source_digest_len > 0 {
            let _ = unsafe {
                Box::from_raw(ptr::slice_from_raw_parts_mut(
                    r.source_digest as *mut u8,
                    r.source_digest_len,
                ))
            };
        }

        // Free active digest
        if !r.active_digest.is_null() && r.active_digest_len > 0 {
            let _ = unsafe {
                Box::from_raw(ptr::slice_from_raw_parts_mut(
                    r.active_digest as *mut u8,
                    r.active_digest_len,
                ))
            };
        }

        // Clear all pointers
        let r_mut = unsafe { &mut *result };
        r_mut.error_message = ptr::null();
        r_mut.error_message_len = 0;
        r_mut.source_digest = ptr::null();
        r_mut.source_digest_len = 0;
        r_mut.active_digest = ptr::null();
        r_mut.active_digest_len = 0;
    });
}

fn make_error(code: u32, message: String) -> (u32, String) {
    (code, message)
}

fn map_error_kind(kind: &super::parser::DynconfParseErrorKind) -> u32 {
    use super::parser::DynconfParseErrorKind;
    match kind {
        DynconfParseErrorKind::DocumentTooLarge => DYNCONF_ERR_TOO_LARGE,
        DynconfParseErrorKind::InvalidJson => DYNCONF_ERR_INVALID_JSON,
        DynconfParseErrorKind::TokenBudgetExceeded => DYNCONF_ERR_TOKEN_BUDGET,
        DynconfParseErrorKind::NestingDepthExceeded => DYNCONF_ERR_NESTING_DEPTH,
        DynconfParseErrorKind::DuplicateKey => DYNCONF_ERR_DUPLICATE_KEY,
        DynconfParseErrorKind::MissingSchemaVersion => DYNCONF_ERR_MISSING_SCHEMA_VERSION,
        DynconfParseErrorKind::InvalidSchemaVersion => DYNCONF_ERR_INVALID_SCHEMA_VERSION,
        DynconfParseErrorKind::UnknownKey => DYNCONF_ERR_UNKNOWN_KEY,
        DynconfParseErrorKind::InvalidType => DYNCONF_ERR_INVALID_TYPE,
        DynconfParseErrorKind::ValueOutOfRange => DYNCONF_ERR_VALUE_OUT_OF_RANGE,
        DynconfParseErrorKind::InvalidUtf8 => DYNCONF_ERR_INVALID_UTF8,
    }
}

unsafe fn write_success(result: *mut FFIDynconfResult, dynconf: &DynconfResult) {
    let r = unsafe { &mut *result };

    // Allocate and write source digest
    let source_bytes = dynconf.source_digest.as_bytes().to_vec().into_boxed_slice();
    r.source_digest_len = source_bytes.len();
    r.source_digest = Box::into_raw(source_bytes) as *const u8;

    // Allocate and write active digest
    let active_bytes = dynconf.active_digest.as_bytes().to_vec().into_boxed_slice();
    r.active_digest_len = active_bytes.len();
    r.active_digest = Box::into_raw(active_bytes) as *const u8;

    // Write typed values
    r.filter = match dynconf.filter {
        Some(FilterValue::On) => DYNCONF_FILTER_ON,
        Some(FilterValue::Off) => DYNCONF_FILTER_OFF,
        None => DYNCONF_NOT_SET_U8,
    };

    r.prune_noise = match dynconf.prune_noise {
        Some(PruneNoiseValue::On) => DYNCONF_PRUNE_NOISE_ON,
        Some(PruneNoiseValue::Off) => DYNCONF_PRUNE_NOISE_OFF,
        None => DYNCONF_NOT_SET_U8,
    };

    r.log_verbosity = match dynconf.log_verbosity {
        Some(LogVerbosity::Error) => DYNCONF_LOG_ERROR,
        Some(LogVerbosity::Warn) => DYNCONF_LOG_WARN,
        Some(LogVerbosity::Info) => DYNCONF_LOG_INFO,
        Some(LogVerbosity::Debug) => DYNCONF_LOG_DEBUG,
        None => DYNCONF_NOT_SET_U8,
    };

    r.error_policy = match dynconf.error_policy {
        Some(ErrorPolicy::Pass) => DYNCONF_POLICY_PASS,
        Some(ErrorPolicy::FailClosed) => DYNCONF_POLICY_FAIL_CLOSED,
        Some(ErrorPolicy::Status429) => DYNCONF_POLICY_STATUS_429,
        Some(ErrorPolicy::Status503) => DYNCONF_POLICY_STATUS_503,
        None => DYNCONF_NOT_SET_U8,
    };

    r.streaming_buffer = dynconf.streaming_buffer.unwrap_or(DYNCONF_NOT_SET_U64);

    // Clear error fields
    r.error_code = DYNCONF_OK;
    r.error_message = ptr::null();
    r.error_message_len = 0;
}

unsafe fn write_error(result: *mut FFIDynconfResult, code: u32, message: &str) {
    let r = unsafe { &mut *result };

    r.error_code = code;

    // Truncate message to 512 bytes (bounded error messages per spec)
    let truncated = if message.len() > 512 {
        &message[..message.floor_char_boundary(512)]
    } else {
        message
    };

    let msg_bytes = truncated.as_bytes().to_vec().into_boxed_slice();
    r.error_message_len = msg_bytes.len();
    r.error_message = Box::into_raw(msg_bytes) as *const u8;

    // Clear success fields
    r.source_digest = ptr::null();
    r.source_digest_len = 0;
    r.active_digest = ptr::null();
    r.active_digest_len = 0;
    r.filter = DYNCONF_NOT_SET_U8;
    r.prune_noise = DYNCONF_NOT_SET_U8;
    r.log_verbosity = DYNCONF_NOT_SET_U8;
    r.error_policy = DYNCONF_NOT_SET_U8;
    r.streaming_buffer = DYNCONF_NOT_SET_U64;
}
