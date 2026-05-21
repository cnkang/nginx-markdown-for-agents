//! Reason code enum — single source of truth for all decision reason codes.
//!
//! This module defines the canonical [`ReasonCode`] enum that represents
//! every possible outcome of the module's conversion decision chain. It is
//! the **single source of truth** for reason codes across the entire system:
//! C code accesses these values through FFI, and all metrics, logging, and
//! documentation derive from this enum.
//!
//! # FFI Boundary
//!
//! The enum uses `#[repr(C)]` so it can be passed directly across the
//! Rust↔C FFI boundary without marshalling. Each variant has a stable
//! numeric discriminant that must not change once assigned.
//!
//! # Adding New Reason Codes
//!
//! When adding a new reason code:
//! 1. Add the variant to [`ReasonCode`] with the next available discriminant
//! 2. Add the string representation in [`ReasonCode::as_str`]
//! 3. Add the metric key in [`ReasonCode::metric_key`]
//! 4. Add the log_decision() callsite in [`ReasonCode::log_callsite`]
//! 5. Add the variant to the `ALL` constant array
//! 6. Update the closure test to include the new variant
//! 7. Ensure a `log_decision()` callsite exists for the new code (Rule 7)

/// Total number of reason code variants.
///
/// This constant is used by the closure test to verify that all variants
/// are accounted for in the `ALL` array. Update this when adding variants.
pub const REASON_CODE_COUNT: usize = 18;

/// Comprehensive reason code enum — single source of truth.
///
/// Every conversion decision path produces exactly one `ReasonCode`.
/// The numeric discriminants are stable and must not be reordered.
///
/// # Repr
///
/// Uses `#[repr(C)]` for direct FFI passthrough without marshalling.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ReasonCode {
    /// Conversion completed successfully.
    ///
    /// **log_decision() callsite**: body_filter: after successful conversion
    /// and downstream NGX_OK.
    Converted = 0,

    /// Skipped: Accept header prefers text/html over text/markdown.
    ///
    /// **log_decision() callsite**: header_filter: Accept negotiation
    /// determined text/html preferred.
    SkippedAccept = 1,

    /// Skipped: no Accept header present in the request.
    ///
    /// **log_decision() callsite**: header_filter: no Accept header present.
    SkippedNoAccept = 2,

    /// Skipped: conditional request matched (304 Not Modified).
    ///
    /// **log_decision() callsite**: header_filter: conditional request
    /// matched (304).
    SkippedConditional = 3,

    /// Failed: decompression error (generic).
    ///
    /// **log_decision() callsite**: body_filter: decompression failed
    /// (generic).
    FailedDecompression = 4,

    /// Failed: decompression output exceeded budget limit.
    ///
    /// **log_decision() callsite**: body_filter: decompression output
    /// exceeded budget.
    DecompressionBudgetExceeded = 5,

    /// Failed: decompression input has invalid format.
    ///
    /// **log_decision() callsite**: body_filter: invalid compression format.
    DecompressionFormatError = 6,

    /// Failed: decompression input was truncated.
    ///
    /// **log_decision() callsite**: body_filter: truncated compressed input.
    DecompressionTruncatedInput = 7,

    /// Failed: decompression I/O error.
    ///
    /// **log_decision() callsite**: body_filter: decompression I/O error.
    DecompressionIoError = 8,

    /// Failed: HTML parsing exceeded the configured timeout.
    ///
    /// **log_decision() callsite**: body_filter: parse timeout exceeded.
    ParseTimeout = 9,

    /// Failed: parser memory allocation exceeded budget.
    ///
    /// **log_decision() callsite**: body_filter: parser memory budget
    /// exceeded.
    ParseBudgetExceeded = 10,

    /// Failed: replay buffer init or append error.
    ///
    /// **log_decision() callsite**: body_filter: replay buffer init/append
    /// failure.
    ReplayBufferError = 11,

    /// Skipped: Accept header explicitly rejects text/markdown (q=0).
    ///
    /// **log_decision() callsite**: header_filter: Accept explicitly rejects
    /// text/markdown (q=0).
    SkippedAcceptReject = 12,

    /// Failed: FFI call error (Rust function returned unexpected error).
    ///
    /// **log_decision() callsite**: body_filter: FFI function returned
    /// unexpected error.
    FfiCallError = 13,

    /// Skipped: response not eligible for conversion (method, status, etc.).
    ///
    /// **log_decision() callsite**: header_filter: response not eligible
    /// (method/status/content-type).
    NotEligible = 14,

    /// Skipped: module disabled for this location/request.
    ///
    /// **log_decision() callsite**: header_filter: module disabled for this
    /// location.
    Disabled = 15,

    /// Failed: fail-open path triggered, original content passed through.
    ///
    /// **log_decision() callsite**: body_filter: fail-open path triggered.
    FailedOpen = 16,

    /// Failed: fail-closed path triggered, request rejected.
    ///
    /// **log_decision() callsite**: body_filter: fail-closed path triggered.
    FailedClosed = 17,
}

/// Array of all reason code variants for exhaustive iteration.
///
/// This array must contain every variant of [`ReasonCode`] exactly once.
/// The closure test verifies this invariant.
///
/// cbindgen:ignore
pub const ALL: [ReasonCode; REASON_CODE_COUNT] = [
    ReasonCode::Converted,
    ReasonCode::SkippedAccept,
    ReasonCode::SkippedNoAccept,
    ReasonCode::SkippedConditional,
    ReasonCode::FailedDecompression,
    ReasonCode::DecompressionBudgetExceeded,
    ReasonCode::DecompressionFormatError,
    ReasonCode::DecompressionTruncatedInput,
    ReasonCode::DecompressionIoError,
    ReasonCode::ParseTimeout,
    ReasonCode::ParseBudgetExceeded,
    ReasonCode::ReplayBufferError,
    ReasonCode::SkippedAcceptReject,
    ReasonCode::FfiCallError,
    ReasonCode::NotEligible,
    ReasonCode::Disabled,
    ReasonCode::FailedOpen,
    ReasonCode::FailedClosed,
];

impl ReasonCode {
    /// Return the uppercase snake_case string representation.
    ///
    /// This string is used in structured logs, diagnostics endpoints,
    /// and as the label value in Prometheus metrics.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::decision::reason_code::ReasonCode;
    ///
    /// assert_eq!(ReasonCode::Converted.as_str(), "CONVERTED");
    /// assert_eq!(ReasonCode::ParseTimeout.as_str(), "PARSE_TIMEOUT");
    /// ```
    pub fn as_str(self) -> &'static str {
        match self {
            ReasonCode::Converted => "CONVERTED",
            ReasonCode::SkippedAccept => "SKIPPED_ACCEPT",
            ReasonCode::SkippedNoAccept => "SKIPPED_NO_ACCEPT",
            ReasonCode::SkippedConditional => "SKIPPED_CONDITIONAL",
            ReasonCode::FailedDecompression => "FAILED_DECOMPRESSION",
            ReasonCode::DecompressionBudgetExceeded => "DECOMPRESSION_BUDGET_EXCEEDED",
            ReasonCode::DecompressionFormatError => "DECOMPRESSION_FORMAT_ERROR",
            ReasonCode::DecompressionTruncatedInput => "DECOMPRESSION_TRUNCATED_INPUT",
            ReasonCode::DecompressionIoError => "DECOMPRESSION_IO_ERROR",
            ReasonCode::ParseTimeout => "PARSE_TIMEOUT",
            ReasonCode::ParseBudgetExceeded => "PARSE_BUDGET_EXCEEDED",
            ReasonCode::ReplayBufferError => "REPLAY_BUFFER_ERROR",
            ReasonCode::SkippedAcceptReject => "SKIPPED_ACCEPT_REJECT",
            ReasonCode::FfiCallError => "FFI_CALL_ERROR",
            ReasonCode::NotEligible => "NOT_ELIGIBLE",
            ReasonCode::Disabled => "DISABLED",
            ReasonCode::FailedOpen => "FAILED_OPEN",
            ReasonCode::FailedClosed => "FAILED_CLOSED",
        }
    }

    /// Return the Prometheus metric key name for this reason code.
    ///
    /// The metric key is used as the counter name suffix in Prometheus
    /// exposition format. Each reason code maps to a specific metric
    /// that tracks how often that decision path is taken.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::decision::reason_code::ReasonCode;
    ///
    /// assert_eq!(
    ///     ReasonCode::Converted.metric_key(),
    ///     "markdown_conversions_total"
    /// );
    /// assert_eq!(
    ///     ReasonCode::DecompressionBudgetExceeded.metric_key(),
    ///     "markdown_decompression_budget_exceeded_total"
    /// );
    /// ```
    pub fn metric_key(self) -> &'static str {
        match self {
            ReasonCode::Converted => "markdown_conversions_total",
            ReasonCode::SkippedAccept => "markdown_skipped_accept_total",
            ReasonCode::SkippedNoAccept => "markdown_skipped_no_accept_total",
            ReasonCode::SkippedConditional => "markdown_skipped_conditional_total",
            ReasonCode::FailedDecompression => "markdown_failed_decompression_total",
            ReasonCode::DecompressionBudgetExceeded => {
                "markdown_decompression_budget_exceeded_total"
            }
            ReasonCode::DecompressionFormatError => "markdown_decompression_format_error_total",
            ReasonCode::DecompressionTruncatedInput => {
                "markdown_decompression_truncated_input_total"
            }
            ReasonCode::DecompressionIoError => "markdown_decompression_io_error_total",
            ReasonCode::ParseTimeout => "markdown_parse_timeouts_total",
            ReasonCode::ParseBudgetExceeded => "markdown_parse_budget_exceeded_total",
            ReasonCode::ReplayBufferError => "markdown_replay_buffer_errors_total",
            ReasonCode::SkippedAcceptReject => "markdown_skipped_accept_reject_total",
            ReasonCode::FfiCallError => "markdown_ffi_call_errors_total",
            ReasonCode::NotEligible => "markdown_skipped_not_eligible_total",
            ReasonCode::Disabled => "markdown_skipped_disabled_total",
            ReasonCode::FailedOpen => "markdown_failed_open_total",
            ReasonCode::FailedClosed => "markdown_failed_closed_total",
        }
    }

    /// Return the expected `log_decision()` callsite description for this
    /// reason code.
    ///
    /// Per AGENTS.md Rule 7, every reason code must have a corresponding
    /// `log_decision()` callsite where it is emitted. This method documents
    /// WHERE in the NGINX filter pipeline each reason code is produced.
    ///
    /// The callsite string indicates the filter phase (header_filter or
    /// body_filter) and the specific condition that triggered the decision.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::decision::reason_code::ReasonCode;
    ///
    /// assert_eq!(
    ///     ReasonCode::Converted.log_callsite(),
    ///     "body_filter: after successful conversion and downstream NGX_OK"
    /// );
    /// assert_eq!(
    ///     ReasonCode::SkippedAccept.log_callsite(),
    ///     "header_filter: Accept negotiation determined text/html preferred"
    /// );
    /// ```
    pub fn log_callsite(self) -> &'static str {
        match self {
            ReasonCode::Converted => {
                "body_filter: after successful conversion and downstream NGX_OK"
            }
            ReasonCode::SkippedAccept => {
                "header_filter: Accept negotiation determined text/html preferred"
            }
            ReasonCode::SkippedNoAccept => "header_filter: no Accept header present",
            ReasonCode::SkippedConditional => "header_filter: conditional request matched (304)",
            ReasonCode::FailedDecompression => "body_filter: decompression failed (generic)",
            ReasonCode::DecompressionBudgetExceeded => {
                "body_filter: decompression output exceeded budget"
            }
            ReasonCode::DecompressionFormatError => "body_filter: invalid compression format",
            ReasonCode::DecompressionTruncatedInput => "body_filter: truncated compressed input",
            ReasonCode::DecompressionIoError => "body_filter: decompression I/O error",
            ReasonCode::ParseTimeout => "body_filter: parse timeout exceeded",
            ReasonCode::ParseBudgetExceeded => "body_filter: parser memory budget exceeded",
            ReasonCode::ReplayBufferError => "body_filter: replay buffer init/append failure",
            ReasonCode::SkippedAcceptReject => {
                "header_filter: Accept explicitly rejects text/markdown (q=0)"
            }
            ReasonCode::FfiCallError => "body_filter: FFI function returned unexpected error",
            ReasonCode::NotEligible => {
                "header_filter: response not eligible (method/status/content-type)"
            }
            ReasonCode::Disabled => "header_filter: module disabled for this location",
            ReasonCode::FailedOpen => "body_filter: fail-open path triggered",
            ReasonCode::FailedClosed => "body_filter: fail-closed path triggered",
        }
    }

    /// Return the numeric discriminant value for FFI transport.
    ///
    /// This is equivalent to casting `self as u32` but provides a
    /// named accessor for clarity at call sites.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::decision::reason_code::ReasonCode;
    ///
    /// assert_eq!(ReasonCode::Converted.discriminant(), 0);
    /// assert_eq!(ReasonCode::ParseTimeout.discriminant(), 9);
    /// ```
    pub fn discriminant(self) -> u32 {
        self as u32
    }

    /// Attempt to construct a `ReasonCode` from its numeric discriminant.
    ///
    /// Returns `None` if the value does not correspond to a known variant.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::decision::reason_code::ReasonCode;
    ///
    /// assert_eq!(ReasonCode::from_discriminant(0), Some(ReasonCode::Converted));
    /// assert_eq!(ReasonCode::from_discriminant(9), Some(ReasonCode::ParseTimeout));
    /// assert_eq!(ReasonCode::from_discriminant(255), None);
    /// ```
    pub fn from_discriminant(value: u32) -> Option<Self> {
        match value {
            0 => Some(ReasonCode::Converted),
            1 => Some(ReasonCode::SkippedAccept),
            2 => Some(ReasonCode::SkippedNoAccept),
            3 => Some(ReasonCode::SkippedConditional),
            4 => Some(ReasonCode::FailedDecompression),
            5 => Some(ReasonCode::DecompressionBudgetExceeded),
            6 => Some(ReasonCode::DecompressionFormatError),
            7 => Some(ReasonCode::DecompressionTruncatedInput),
            8 => Some(ReasonCode::DecompressionIoError),
            9 => Some(ReasonCode::ParseTimeout),
            10 => Some(ReasonCode::ParseBudgetExceeded),
            11 => Some(ReasonCode::ReplayBufferError),
            12 => Some(ReasonCode::SkippedAcceptReject),
            13 => Some(ReasonCode::FfiCallError),
            14 => Some(ReasonCode::NotEligible),
            15 => Some(ReasonCode::Disabled),
            16 => Some(ReasonCode::FailedOpen),
            17 => Some(ReasonCode::FailedClosed),
            _ => None,
        }
    }
}

/// Get the string representation of a reason code by its numeric value.
///
/// Returns a pointer to a static string and writes the length to `out_len`.
/// Returns NULL if the discriminant is invalid.
///
/// # Safety
///
/// The caller must ensure that `out_len` either is NULL or points to
/// writable storage for a `usize`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_reason_code_str(code: u32, out_len: *mut usize) -> *const u8 {
    match ReasonCode::from_discriminant(code) {
        Some(rc) => {
            let s = rc.as_str();
            if !out_len.is_null() {
                unsafe { *out_len = s.len() };
            }
            s.as_ptr()
        }
        None => {
            if !out_len.is_null() {
                unsafe { *out_len = 0 };
            }
            std::ptr::null()
        }
    }
}

/// Get the Prometheus metric key for a reason code by its numeric value.
///
/// Returns a pointer to a static string and writes the length to `out_len`.
/// Returns NULL if the discriminant is invalid.
///
/// # Safety
///
/// The caller must ensure that `out_len` either is NULL or points to
/// writable storage for a `usize`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_reason_code_metric_key(
    code: u32,
    out_len: *mut usize,
) -> *const u8 {
    match ReasonCode::from_discriminant(code) {
        Some(rc) => {
            let s = rc.metric_key();
            if !out_len.is_null() {
                unsafe { *out_len = s.len() };
            }
            s.as_ptr()
        }
        None => {
            if !out_len.is_null() {
                unsafe { *out_len = 0 };
            }
            std::ptr::null()
        }
    }
}

/// Return the total number of defined reason codes.
///
/// C callers can use this to verify they handle all variants.
#[unsafe(no_mangle)]
pub extern "C" fn markdown_reason_code_count() -> u32 {
    REASON_CODE_COUNT as u32
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    /// Verify that ALL array length matches REASON_CODE_COUNT.
    #[test]
    fn test_all_array_length_matches_count() {
        assert_eq!(
            ALL.len(),
            REASON_CODE_COUNT,
            "ALL array length ({}) must equal REASON_CODE_COUNT ({})",
            ALL.len(),
            REASON_CODE_COUNT
        );
    }

    /// Verify that every variant in ALL has a unique discriminant.
    #[test]
    fn test_discriminants_unique() {
        let mut seen = HashSet::new();
        for rc in &ALL {
            let d = rc.discriminant();
            assert!(seen.insert(d), "Duplicate discriminant {} for {:?}", d, rc);
        }
    }

    /// Verify that every variant in ALL has a unique string representation.
    #[test]
    fn test_strings_unique() {
        let mut seen = HashSet::new();
        for rc in &ALL {
            let s = rc.as_str();
            assert!(seen.insert(s), "Duplicate string '{}' for {:?}", s, rc);
        }
    }

    /// Verify that every variant in ALL has a unique metric key.
    #[test]
    fn test_metric_keys_unique() {
        let mut seen = HashSet::new();
        for rc in &ALL {
            let k = rc.metric_key();
            assert!(seen.insert(k), "Duplicate metric key '{}' for {:?}", k, rc);
        }
    }

    /// Verify that all string representations are uppercase snake_case.
    #[test]
    fn test_strings_are_uppercase_snake_case() {
        for rc in &ALL {
            let s = rc.as_str();
            assert!(!s.is_empty(), "{:?} has empty string", rc);
            for ch in s.chars() {
                assert!(
                    ch.is_ascii_uppercase() || ch.is_ascii_digit() || ch == '_',
                    "String '{}' for {:?} contains invalid char '{}'",
                    s,
                    rc,
                    ch
                );
            }
            assert!(
                s.chars().next().unwrap().is_ascii_uppercase(),
                "String '{}' must start with uppercase letter",
                s
            );
        }
    }

    /// Verify that all metric keys follow Prometheus naming conventions.
    #[test]
    fn test_metric_keys_prometheus_format() {
        for rc in &ALL {
            let k = rc.metric_key();
            assert!(!k.is_empty(), "{:?} has empty metric key", rc);
            assert!(
                k.starts_with("markdown_"),
                "Metric key '{}' must start with 'markdown_'",
                k
            );
            assert!(
                k.ends_with("_total"),
                "Metric key '{}' must end with '_total' (counter)",
                k
            );
            for ch in k.chars() {
                assert!(
                    ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '_',
                    "Metric key '{}' contains invalid char '{}'",
                    k,
                    ch
                );
            }
        }
    }

    /// Verify round-trip: discriminant → from_discriminant → same variant.
    #[test]
    fn test_from_discriminant_roundtrip() {
        for rc in &ALL {
            let d = rc.discriminant();
            let recovered = ReasonCode::from_discriminant(d);
            assert_eq!(
                recovered,
                Some(*rc),
                "Round-trip failed for {:?} (discriminant {})",
                rc,
                d
            );
        }
    }

    /// Verify that from_discriminant returns None for invalid values.
    #[test]
    fn test_from_discriminant_invalid() {
        assert_eq!(ReasonCode::from_discriminant(255), None);
        assert_eq!(ReasonCode::from_discriminant(100), None);
        assert_eq!(ReasonCode::from_discriminant(u32::MAX), None);
    }

    /// Closure test: verify that the discriminant range is contiguous
    /// from 0 to REASON_CODE_COUNT-1 with no gaps.
    #[test]
    fn test_discriminant_range_contiguous() {
        let mut discriminants: Vec<u32> = ALL.iter().map(|rc| rc.discriminant()).collect();
        discriminants.sort();
        for (i, d) in discriminants.iter().enumerate() {
            assert_eq!(
                *d, i as u32,
                "Expected discriminant {} at index {}, got {}",
                i, i, d
            );
        }
    }

    /// Closure test: verify that ALL contains every variant by checking
    /// that from_discriminant succeeds for all values in [0, REASON_CODE_COUNT).
    #[test]
    fn test_closure_all_discriminants_covered() {
        for i in 0..REASON_CODE_COUNT as u32 {
            assert!(
                ReasonCode::from_discriminant(i).is_some(),
                "Discriminant {} has no corresponding variant in from_discriminant",
                i
            );
        }
        // Next value should be None (no gap-free extension beyond count)
        assert_eq!(
            ReasonCode::from_discriminant(REASON_CODE_COUNT as u32),
            None,
            "Discriminant {} should not exist (REASON_CODE_COUNT boundary)",
            REASON_CODE_COUNT
        );
    }

    /// FFI function test: markdown_reason_code_str returns correct data.
    #[test]
    fn test_ffi_reason_code_str() {
        for rc in &ALL {
            let mut len: usize = 0;
            let ptr = unsafe { markdown_reason_code_str(rc.discriminant(), &mut len) };
            assert!(!ptr.is_null(), "NULL returned for {:?}", rc);
            assert_eq!(len, rc.as_str().len());
            let slice = unsafe { std::slice::from_raw_parts(ptr, len) };
            let s = std::str::from_utf8(slice).unwrap();
            assert_eq!(s, rc.as_str());
        }
    }

    /// FFI function test: markdown_reason_code_str returns NULL for invalid.
    #[test]
    fn test_ffi_reason_code_str_invalid() {
        let mut len: usize = 99;
        let ptr = unsafe { markdown_reason_code_str(255, &mut len) };
        assert!(ptr.is_null());
        assert_eq!(len, 0);
    }

    /// FFI function test: markdown_reason_code_metric_key returns correct data.
    #[test]
    fn test_ffi_reason_code_metric_key() {
        for rc in &ALL {
            let mut len: usize = 0;
            let ptr = unsafe { markdown_reason_code_metric_key(rc.discriminant(), &mut len) };
            assert!(!ptr.is_null(), "NULL returned for {:?}", rc);
            assert_eq!(len, rc.metric_key().len());
            let slice = unsafe { std::slice::from_raw_parts(ptr, len) };
            let s = std::str::from_utf8(slice).unwrap();
            assert_eq!(s, rc.metric_key());
        }
    }

    /// FFI function test: markdown_reason_code_count returns correct value.
    #[test]
    fn test_ffi_reason_code_count() {
        assert_eq!(markdown_reason_code_count(), REASON_CODE_COUNT as u32);
    }

    /// Verify the enum size is suitable for FFI (repr(C) u32-sized discriminant).
    #[test]
    fn test_enum_size_for_ffi() {
        assert_eq!(
            std::mem::size_of::<ReasonCode>(),
            4,
            "ReasonCode should be 4 bytes (u32 discriminant) for FFI"
        );
        assert_eq!(
            std::mem::align_of::<ReasonCode>(),
            4,
            "ReasonCode should have 4-byte alignment for FFI"
        );
    }

    /// Verify that every variant has a non-empty log_callsite() description.
    ///
    /// Per AGENTS.md Rule 7, every reason code must have a corresponding
    /// log_decision() callsite. This test ensures no variant returns an
    /// empty string from log_callsite().
    #[test]
    fn test_log_callsite_non_empty() {
        for rc in &ALL {
            let callsite = rc.log_callsite();
            assert!(
                !callsite.is_empty(),
                "{:?} has empty log_callsite() description",
                rc
            );
        }
    }

    /// Verify that log_callsite() descriptions indicate a valid filter phase.
    ///
    /// Each callsite must start with either "header_filter:" or "body_filter:"
    /// to indicate where in the NGINX pipeline the log_decision() call occurs.
    #[test]
    fn test_log_callsite_has_valid_phase() {
        for rc in &ALL {
            let callsite = rc.log_callsite();
            assert!(
                callsite.starts_with("header_filter:") || callsite.starts_with("body_filter:"),
                "{:?} log_callsite '{}' must start with 'header_filter:' or 'body_filter:'",
                rc,
                callsite
            );
        }
    }

    /// Verify that all log_callsite() descriptions are unique.
    #[test]
    fn test_log_callsite_unique() {
        let mut seen = HashSet::new();
        for rc in &ALL {
            let callsite = rc.log_callsite();
            assert!(
                seen.insert(callsite),
                "Duplicate log_callsite '{}' for {:?}",
                callsite,
                rc
            );
        }
    }
}
