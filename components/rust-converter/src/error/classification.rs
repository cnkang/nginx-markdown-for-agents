//! Unified error classification and behavior decision (spec 51).
//!
//! This module defines the error policy layer that determines how each
//! error class is handled at runtime. It is the **single source of truth**
//! for error classification, pre-commit/post-commit semantics, and the
//! mapping from error class to behavior.
//!
//! # Design
//!
//! - **ErrorClass**: 10 distinct error categories covering all failure modes.
//! - **ErrorPolicy**: The operator-configured handling strategy
//!   (`pass` / `status <code>` / `fail_closed`).
//! - **ErrorBehavior**: The concrete runtime action taken for a given error.
//! - **decide_error_behavior**: Pure function mapping (class, policy) → behavior.
//!
//! # Pre-commit vs Post-commit
//!
//! - **Pre-commit errors** occur before headers are sent to the client.
//!   The module can safely fall back to the original response or return
//!   an error status code.
//! - **Post-commit errors** occur after headers have been sent. The module
//!   cannot rewrite the status line; it must terminate the connection.
//!   Post-commit errors **always** result in `TerminateConnection` regardless
//!   of the configured policy.
//!
//! # Stability Contract (1.0)
//!
//! - Error class enum: frozen at 0.9.0, additive-only after 1.0.
//! - Error class → reason code mapping: stable, additive-only after 1.0.
//! - Post-commit forced TerminateConnection: safety invariant, never relaxed.

use crate::decision::reason_code::ReasonCode;

/// Number of error class variants.
pub const ERROR_CLASS_COUNT: usize = 10;

/// Error classification — categorizes all possible conversion failures.
///
/// Each variant maps to exactly one reason code and has a fixed
/// pre-commit/post-commit designation.
///
/// # Repr
///
/// Uses `#[repr(u8)]` for FFI compatibility and compact storage.
/// Discriminant values are stable and must not be reordered.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorClass {
    /// HTML-to-Markdown conversion failed (parse error, encoding, etc.).
    ConversionError = 0,
    /// Conversion exceeded the configured timeout.
    Timeout = 1,
    /// Memory budget exceeded during conversion.
    MemoryBudgetExceeded = 2,
    /// Rust FFI panic caught by catch_unwind.
    FfiPanic = 3,
    /// Decompression of upstream response failed.
    DecompressionError = 4,
    /// Worker inflight limit exceeded (detected by spec 52).
    Overload = 5,
    /// Dynamic configuration is invalid.
    InvalidDynconf = 6,
    /// Running with a degraded (last-known-good) snapshot.
    DegradedSnapshot = 7,
    /// HeaderPlan apply failed after headers were committed.
    HeaderPlanApplyError = 8,
    /// Streaming conversion failed mid-flight (body partially sent).
    StreamingMidFlightError = 9,
}

/// All error class variants for exhaustive iteration.
///
/// cbindgen:ignore
pub const ALL_ERROR_CLASSES: [ErrorClass; ERROR_CLASS_COUNT] = [
    ErrorClass::ConversionError,
    ErrorClass::Timeout,
    ErrorClass::MemoryBudgetExceeded,
    ErrorClass::FfiPanic,
    ErrorClass::DecompressionError,
    ErrorClass::Overload,
    ErrorClass::InvalidDynconf,
    ErrorClass::DegradedSnapshot,
    ErrorClass::HeaderPlanApplyError,
    ErrorClass::StreamingMidFlightError,
];

impl ErrorClass {
    /// Whether this error occurs after headers have been committed.
    ///
    /// Post-commit errors force `TerminateConnection` regardless of the
    /// configured policy because the status line cannot be rewritten.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::error::classification::ErrorClass;
    ///
    /// assert!(!ErrorClass::ConversionError.is_post_commit());
    /// assert!(ErrorClass::HeaderPlanApplyError.is_post_commit());
    /// assert!(ErrorClass::StreamingMidFlightError.is_post_commit());
    /// ```
    pub fn is_post_commit(self) -> bool {
        matches!(
            self,
            ErrorClass::HeaderPlanApplyError | ErrorClass::StreamingMidFlightError
        )
    }

    /// Return the lowercase snake_case string representation.
    ///
    /// Used in diagnostics JSON output and structured logging.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::error::classification::ErrorClass;
    ///
    /// assert_eq!(ErrorClass::ConversionError.as_str(), "conversion_error");
    /// assert_eq!(ErrorClass::StreamingMidFlightError.as_str(), "streaming_mid_flight_error");
    /// ```
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorClass::ConversionError => "conversion_error",
            ErrorClass::Timeout => "timeout",
            ErrorClass::MemoryBudgetExceeded => "memory_budget_exceeded",
            ErrorClass::FfiPanic => "ffi_panic",
            ErrorClass::DecompressionError => "decompression_error",
            ErrorClass::Overload => "overload",
            ErrorClass::InvalidDynconf => "invalid_dynconf",
            ErrorClass::DegradedSnapshot => "degraded_snapshot",
            ErrorClass::HeaderPlanApplyError => "header_plan_apply_error",
            ErrorClass::StreamingMidFlightError => "streaming_mid_flight_error",
        }
    }

    /// Construct from numeric discriminant (FFI boundary).
    ///
    /// Returns `None` if the value is not a valid error class.
    pub fn from_discriminant(value: u8) -> Option<Self> {
        match value {
            0 => Some(ErrorClass::ConversionError),
            1 => Some(ErrorClass::Timeout),
            2 => Some(ErrorClass::MemoryBudgetExceeded),
            3 => Some(ErrorClass::FfiPanic),
            4 => Some(ErrorClass::DecompressionError),
            5 => Some(ErrorClass::Overload),
            6 => Some(ErrorClass::InvalidDynconf),
            7 => Some(ErrorClass::DegradedSnapshot),
            8 => Some(ErrorClass::HeaderPlanApplyError),
            9 => Some(ErrorClass::StreamingMidFlightError),
            _ => None,
        }
    }
}

/// Operator-configured error handling policy.
///
/// Set via the `markdown_error_policy` NGINX directive.
/// Applies uniformly to all pre-commit errors; post-commit errors
/// ignore this policy and always terminate the connection.
///
/// # Variants
///
/// - `Pass`: Deliver the original upstream response (fail-open).
/// - `Status(u16)`: Return the specified HTTP status code (429/503; 502 reserved for fail_closed).
/// - `FailClosed`: Return 502 Bad Gateway, never leak original content.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorPolicy {
    /// Deliver original upstream response on error (fail-open, default).
    Pass,
    /// Return the specified HTTP status code on error.
    /// Allowed values: 429, 503 (502 reserved for fail_closed; C parser rejects it).
    Status(u16),
    /// Return 502 Bad Gateway; never leak original content.
    FailClosed,
}

impl ErrorPolicy {
    /// Return the string representation for diagnostics/config display.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::error::classification::ErrorPolicy;
    ///
    /// assert_eq!(ErrorPolicy::Pass.as_str(), "pass");
    /// assert_eq!(ErrorPolicy::Status(503).as_str(), "status");
    /// assert_eq!(ErrorPolicy::FailClosed.as_str(), "fail_closed");
    /// ```
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorPolicy::Pass => "pass",
            ErrorPolicy::Status(_) => "status",
            ErrorPolicy::FailClosed => "fail_closed",
        }
    }
}

/// Concrete runtime error handling behavior.
///
/// This is the output of [`decide_error_behavior`] — the action the
/// C error handler must execute.
///
/// # Variants
///
/// - `PassThrough`: Forward the original upstream response unmodified.
/// - `ReturnStatus(u16)`: Send the specified HTTP status code to the client.
/// - `TerminateConnection`: Close/abort the connection (post-commit only).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorBehavior {
    /// Forward the original upstream response (pre-commit only).
    PassThrough,
    /// Return the specified HTTP status code (pre-commit only).
    ReturnStatus(u16),
    /// Terminate output / close connection (post-commit forced).
    /// Headers already sent; cannot rewrite status line.
    TerminateConnection,
}

impl ErrorBehavior {
    /// Return the string representation for diagnostics output.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::error::classification::ErrorBehavior;
    ///
    /// assert_eq!(ErrorBehavior::PassThrough.as_str(), "pass_through");
    /// assert_eq!(ErrorBehavior::ReturnStatus(502).as_str(), "return_status");
    /// assert_eq!(ErrorBehavior::TerminateConnection.as_str(), "terminate_connection");
    /// ```
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorBehavior::PassThrough => "pass_through",
            ErrorBehavior::ReturnStatus(_) => "return_status",
            ErrorBehavior::TerminateConnection => "terminate_connection",
        }
    }

    /// Whether this behavior was forced (ignoring configured policy).
    ///
    /// Returns `true` only for `TerminateConnection` triggered by a
    /// post-commit error class.
    pub fn is_forced(self) -> bool {
        matches!(self, ErrorBehavior::TerminateConnection)
    }
}

/// Decide the error handling behavior for a given error class and policy.
///
/// This is the core decision function for the unified error policy (spec 51).
///
/// # Rules
///
/// 1. **Post-commit errors** (`HeaderPlanApplyError`, `StreamingMidFlightError`)
///    **always** return `TerminateConnection`, regardless of the configured
///    policy. Headers have been sent; the status line cannot be rewritten.
///
/// 2. **Pre-commit errors** respect the operator-configured policy:
///    - `Pass` → `PassThrough` (deliver original upstream response)
///    - `Status(n)` → `ReturnStatus(n)`
///    - `FailClosed` → `ReturnStatus(502)`
///
/// # Examples
///
/// ```
/// use nginx_markdown_converter::error::classification::*;
///
/// // Pre-commit error with pass policy
/// let behavior = decide_error_behavior(ErrorClass::ConversionError, ErrorPolicy::Pass);
/// assert_eq!(behavior, ErrorBehavior::PassThrough);
///
/// // Pre-commit error with status policy
/// let behavior = decide_error_behavior(ErrorClass::Timeout, ErrorPolicy::Status(503));
/// assert_eq!(behavior, ErrorBehavior::ReturnStatus(503));
///
/// // Post-commit error ignores policy
/// let behavior = decide_error_behavior(
///     ErrorClass::StreamingMidFlightError,
///     ErrorPolicy::Pass,
/// );
/// assert_eq!(behavior, ErrorBehavior::TerminateConnection);
/// ```
pub fn decide_error_behavior(class: ErrorClass, policy: ErrorPolicy) -> ErrorBehavior {
    /* Post-commit errors force TerminateConnection.
     * Headers already sent — cannot rewrite status line or fall back. */
    if class.is_post_commit() {
        return ErrorBehavior::TerminateConnection;
    }

    /* Pre-commit errors respect operator configuration. */
    match policy {
        ErrorPolicy::Pass => ErrorBehavior::PassThrough,
        ErrorPolicy::Status(code) => ErrorBehavior::ReturnStatus(code),
        ErrorPolicy::FailClosed => ErrorBehavior::ReturnStatus(502),
    }
}

/// Map a raw FFI error code to its `ErrorClass`.
///
/// Error codes are defined in `markdown_converter.h` (ERROR_PARSE=1,
/// ERROR_ENCODING=2, etc.). This function is the single source of truth for
/// the raw-error-code → error-class classification, replacing the
/// independent C switch that previously lived in
/// `ngx_http_markdown_classify_error()`.
///
/// # Arguments
///
/// * `error_code` — The numeric FFI error code from `MarkdownResult.error_code`.
///
/// # Returns
///
/// The [`ErrorClass`] corresponding to the given error code. Unknown codes
/// map to [`ErrorClass::FfiPanic`] (the system/catchall category).
///
/// # Examples
///
/// ```
/// use nginx_markdown_converter::error::classification::{ErrorClass, classify_error_code};
///
/// assert_eq!(classify_error_code(1), ErrorClass::ConversionError);  // ERROR_PARSE
/// assert_eq!(classify_error_code(3), ErrorClass::Timeout);          // ERROR_TIMEOUT
/// assert_eq!(classify_error_code(4), ErrorClass::MemoryBudgetExceeded); // ERROR_MEMORY_LIMIT
/// assert_eq!(classify_error_code(99), ErrorClass::FfiPanic);        // ERROR_INTERNAL
/// assert_eq!(classify_error_code(255), ErrorClass::FfiPanic);       // unknown
/// ```
pub fn classify_error_code(error_code: u32) -> ErrorClass {
    match error_code {
        /* Conversion errors: parse, encoding, invalid input,
         * decompression format/truncated/IO errors */
        1 | 2 | 5 | 12 | 13 | 14 => ErrorClass::ConversionError,

        /* Timeout errors: conversion timeout, parse timeout */
        3 | 10 => ErrorClass::Timeout,

        /* Memory/budget exceeded: memory_limit, budget_exceeded,
         * decompression_budget, parse_budget */
        4 | 6 | 9 | 11 => ErrorClass::MemoryBudgetExceeded,

        /* Streaming fallback: engine downgrade (pre-commit).
         * The streaming engine gave up and the module falls back to
         * full-buffer.  Classified as ConversionError because from the
         * streaming converter's perspective the conversion failed; the
         * module then retries via full-buffer.  This is a pre-commit
         * error: the response can be restarted.
         *
         * Historical note: prior to 0.9.0 this mapped to ERROR_SYSTEM
         * in C.  Reclassified as ConversionError because it represents
         * a converter-level failure mode, not a system-level fault. */
        7 => ErrorClass::ConversionError,

        /* Post-commit: streaming mid-flight error */
        8 => ErrorClass::StreamingMidFlightError,

        /* Internal/unknown → system/panic */
        99 => ErrorClass::FfiPanic,
        _ => ErrorClass::FfiPanic,
    }
}

/// Map an error class to its corresponding reason code.
///
/// The mapping is stable (frozen at 0.9.0, additive-only after 1.0).
/// Each error class maps to exactly one reason code for metrics and
/// diagnostics.  These mappings are the **single source of truth**,
/// aligned with the C-side `ngx_http_markdown_reason.c` which sources
/// reason code strings from the same Rust ReasonCode enum via FFI.
///
/// # Examples
///
/// ```
/// use nginx_markdown_converter::error::classification::{ErrorClass, error_to_reason_code};
/// use nginx_markdown_converter::decision::reason_code::ReasonCode;
///
/// assert_eq!(error_to_reason_code(ErrorClass::ConversionError), ReasonCode::ConversionError);
/// assert_eq!(error_to_reason_code(ErrorClass::Timeout), ReasonCode::Timeout);
/// ```
pub fn error_to_reason_code(class: ErrorClass) -> ReasonCode {
    match class {
        ErrorClass::ConversionError => ReasonCode::ConversionError,
        ErrorClass::Timeout => ReasonCode::Timeout,
        ErrorClass::MemoryBudgetExceeded => ReasonCode::MemoryBudgetExceeded,
        ErrorClass::FfiPanic => ReasonCode::FfiPanic,
        ErrorClass::DecompressionError => ReasonCode::DecompressionError,
        ErrorClass::Overload => ReasonCode::Overload,
        ErrorClass::InvalidDynconf => ReasonCode::InvalidDynconf,
        ErrorClass::DegradedSnapshot => ReasonCode::DegradedSnapshot,
        ErrorClass::HeaderPlanApplyError => ReasonCode::HeaderPlanApplyError,
        ErrorClass::StreamingMidFlightError => ReasonCode::StreamingMidFlightError,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /* ====================================================================
     * Task 2.8: 10 error class variants tests
     * ==================================================================== */

    #[test]
    fn test_error_class_count() {
        assert_eq!(ALL_ERROR_CLASSES.len(), ERROR_CLASS_COUNT);
    }

    #[test]
    fn test_error_class_discriminants_unique() {
        let mut seen = std::collections::HashSet::new();
        for class in &ALL_ERROR_CLASSES {
            let d = *class as u8;
            assert!(
                seen.insert(d),
                "Duplicate discriminant {} for {:?}",
                d,
                class
            );
        }
    }

    #[test]
    fn test_error_class_strings_unique() {
        let mut seen = std::collections::HashSet::new();
        for class in &ALL_ERROR_CLASSES {
            let s = class.as_str();
            assert!(seen.insert(s), "Duplicate string '{}' for {:?}", s, class);
        }
    }

    #[test]
    fn test_error_class_from_discriminant_roundtrip() {
        for class in &ALL_ERROR_CLASSES {
            let d = *class as u8;
            let recovered = ErrorClass::from_discriminant(d);
            assert_eq!(
                recovered,
                Some(*class),
                "Round-trip failed for {:?} (discriminant {})",
                class,
                d
            );
        }
    }

    #[test]
    fn test_error_class_from_discriminant_invalid() {
        assert_eq!(ErrorClass::from_discriminant(10), None);
        assert_eq!(ErrorClass::from_discriminant(255), None);
    }

    #[test]
    fn test_error_class_strings_are_snake_case() {
        for class in &ALL_ERROR_CLASSES {
            let s = class.as_str();
            assert!(!s.is_empty());
            for ch in s.chars() {
                assert!(
                    ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '_',
                    "String '{}' for {:?} contains invalid char '{}'",
                    s,
                    class,
                    ch
                );
            }
        }
    }

    /* ====================================================================
     * Task 2.3: is_post_commit tests
     * ==================================================================== */

    #[test]
    fn test_pre_commit_classes() {
        let pre_commit = [
            ErrorClass::ConversionError,
            ErrorClass::Timeout,
            ErrorClass::MemoryBudgetExceeded,
            ErrorClass::FfiPanic,
            ErrorClass::DecompressionError,
            ErrorClass::Overload,
            ErrorClass::InvalidDynconf,
            ErrorClass::DegradedSnapshot,
        ];
        for class in &pre_commit {
            assert!(!class.is_post_commit(), "{:?} should be pre-commit", class);
        }
    }

    #[test]
    fn test_post_commit_classes() {
        assert!(ErrorClass::HeaderPlanApplyError.is_post_commit());
        assert!(ErrorClass::StreamingMidFlightError.is_post_commit());
    }

    /* ====================================================================
     * Task 2.9: Pre-commit error behavior tests (pass / status / fail_closed)
     * ==================================================================== */

    #[test]
    fn test_precommit_pass_behavior() {
        let pre_commit_classes = [
            ErrorClass::ConversionError,
            ErrorClass::Timeout,
            ErrorClass::MemoryBudgetExceeded,
            ErrorClass::FfiPanic,
            ErrorClass::DecompressionError,
            ErrorClass::Overload,
            ErrorClass::InvalidDynconf,
            ErrorClass::DegradedSnapshot,
        ];
        for class in &pre_commit_classes {
            let behavior = decide_error_behavior(*class, ErrorPolicy::Pass);
            assert_eq!(
                behavior,
                ErrorBehavior::PassThrough,
                "{:?} with Pass should yield PassThrough",
                class
            );
        }
    }

    #[test]
    fn test_precommit_status_behavior() {
        let pre_commit_classes = [
            ErrorClass::ConversionError,
            ErrorClass::Timeout,
            ErrorClass::MemoryBudgetExceeded,
            ErrorClass::FfiPanic,
            ErrorClass::DecompressionError,
            ErrorClass::Overload,
            ErrorClass::InvalidDynconf,
            ErrorClass::DegradedSnapshot,
        ];
        for code in [429u16, 502, 503] {
            for class in &pre_commit_classes {
                let behavior = decide_error_behavior(*class, ErrorPolicy::Status(code));
                assert_eq!(
                    behavior,
                    ErrorBehavior::ReturnStatus(code),
                    "{:?} with Status({}) should yield ReturnStatus({})",
                    class,
                    code,
                    code
                );
            }
        }
    }

    #[test]
    fn test_precommit_fail_closed_behavior() {
        let pre_commit_classes = [
            ErrorClass::ConversionError,
            ErrorClass::Timeout,
            ErrorClass::MemoryBudgetExceeded,
            ErrorClass::FfiPanic,
            ErrorClass::DecompressionError,
            ErrorClass::Overload,
            ErrorClass::InvalidDynconf,
            ErrorClass::DegradedSnapshot,
        ];
        for class in &pre_commit_classes {
            let behavior = decide_error_behavior(*class, ErrorPolicy::FailClosed);
            assert_eq!(
                behavior,
                ErrorBehavior::ReturnStatus(502),
                "{:?} with FailClosed should yield ReturnStatus(502)",
                class
            );
        }
    }

    /* ====================================================================
     * Task 2.10: Post-commit forced TerminateConnection tests
     * ==================================================================== */

    #[test]
    fn test_postcommit_forces_terminate_regardless_of_policy() {
        let post_commit_classes = [
            ErrorClass::HeaderPlanApplyError,
            ErrorClass::StreamingMidFlightError,
        ];
        let policies = [
            ErrorPolicy::Pass,
            ErrorPolicy::Status(429),
            ErrorPolicy::Status(502),
            ErrorPolicy::Status(503),
            ErrorPolicy::FailClosed,
        ];
        for class in &post_commit_classes {
            for policy in &policies {
                let behavior = decide_error_behavior(*class, *policy);
                assert_eq!(
                    behavior,
                    ErrorBehavior::TerminateConnection,
                    "{:?} with {:?} must force TerminateConnection",
                    class,
                    policy
                );
            }
        }
    }

    /* ====================================================================
     * Task 2.11: error_to_reason_code mapping tests
     * ==================================================================== */

    #[test]
    fn test_error_to_reason_code_all_classes_mapped() {
        for class in &ALL_ERROR_CLASSES {
            /* Should not panic — every class has a mapping. */
            let _code = error_to_reason_code(*class);
        }
    }

    #[test]
    fn test_error_to_reason_code_specific_mappings() {
        assert_eq!(
            error_to_reason_code(ErrorClass::ConversionError),
            ReasonCode::ConversionError
        );
        assert_eq!(
            error_to_reason_code(ErrorClass::Timeout),
            ReasonCode::Timeout
        );
        assert_eq!(
            error_to_reason_code(ErrorClass::MemoryBudgetExceeded),
            ReasonCode::MemoryBudgetExceeded
        );
        assert_eq!(
            error_to_reason_code(ErrorClass::FfiPanic),
            ReasonCode::FfiPanic
        );
        assert_eq!(
            error_to_reason_code(ErrorClass::DecompressionError),
            ReasonCode::DecompressionError
        );
        assert_eq!(
            error_to_reason_code(ErrorClass::Overload),
            ReasonCode::Overload
        );
        assert_eq!(
            error_to_reason_code(ErrorClass::InvalidDynconf),
            ReasonCode::InvalidDynconf
        );
        assert_eq!(
            error_to_reason_code(ErrorClass::DegradedSnapshot),
            ReasonCode::DegradedSnapshot
        );
        assert_eq!(
            error_to_reason_code(ErrorClass::HeaderPlanApplyError),
            ReasonCode::HeaderPlanApplyError
        );
        assert_eq!(
            error_to_reason_code(ErrorClass::StreamingMidFlightError),
            ReasonCode::StreamingMidFlightError
        );
    }

    /* ====================================================================
     * ErrorBehavior helper tests
     * ==================================================================== */

    #[test]
    fn test_behavior_is_forced() {
        assert!(!ErrorBehavior::PassThrough.is_forced());
        assert!(!ErrorBehavior::ReturnStatus(502).is_forced());
        assert!(ErrorBehavior::TerminateConnection.is_forced());
    }

    #[test]
    fn test_behavior_as_str() {
        assert_eq!(ErrorBehavior::PassThrough.as_str(), "pass_through");
        assert_eq!(ErrorBehavior::ReturnStatus(503).as_str(), "return_status");
        assert_eq!(
            ErrorBehavior::TerminateConnection.as_str(),
            "terminate_connection"
        );
    }

    #[test]
    fn test_policy_as_str() {
        assert_eq!(ErrorPolicy::Pass.as_str(), "pass");
        assert_eq!(ErrorPolicy::Status(429).as_str(), "status");
        assert_eq!(ErrorPolicy::FailClosed.as_str(), "fail_closed");
    }

    /* ====================================================================
     * Task 5.5: classify_error_code tests
     * ==================================================================== */

    #[test]
    fn test_classify_error_code_conversion_errors() {
        /* ERROR_PARSE=1, ERROR_ENCODING=2, ERROR_INVALID_INPUT=5,
         * ERROR_DECOMPRESSION_FORMAT_ERROR=12,
         * ERROR_DECOMPRESSION_TRUNCATED_INPUT=13,
         * ERROR_DECOMPRESSION_IO_ERROR=14,
         * ERROR_STREAMING_FALLBACK=7 */
        for code in [1, 2, 5, 7, 12, 13, 14] {
            assert_eq!(
                classify_error_code(code),
                ErrorClass::ConversionError,
                "Error code {} should classify as ConversionError",
                code,
            );
        }
    }

    #[test]
    fn test_classify_error_code_timeout_errors() {
        /* ERROR_TIMEOUT=3, ERROR_PARSE_TIMEOUT=10 */
        for code in [3, 10] {
            assert_eq!(
                classify_error_code(code),
                ErrorClass::Timeout,
                "Error code {} should classify as Timeout",
                code,
            );
        }
    }

    #[test]
    fn test_classify_error_code_memory_budget_errors() {
        /* ERROR_MEMORY_LIMIT=4, ERROR_BUDGET_EXCEEDED=6,
         * ERROR_DECOMPRESSION_BUDGET_EXCEEDED=9,
         * ERROR_PARSE_BUDGET_EXCEEDED=11 */
        for code in [4, 6, 9, 11] {
            assert_eq!(
                classify_error_code(code),
                ErrorClass::MemoryBudgetExceeded,
                "Error code {} should classify as MemoryBudgetExceeded",
                code,
            );
        }
    }

    #[test]
    fn test_classify_error_code_post_commit() {
        /* ERROR_POST_COMMIT=8 */
        assert_eq!(classify_error_code(8), ErrorClass::StreamingMidFlightError);
    }

    #[test]
    fn test_classify_error_code_internal_and_unknown() {
        /* ERROR_INTERNAL=99, unknown codes */
        assert_eq!(classify_error_code(99), ErrorClass::FfiPanic);
        assert_eq!(classify_error_code(0), ErrorClass::FfiPanic);
        assert_eq!(classify_error_code(100), ErrorClass::FfiPanic);
        assert_eq!(classify_error_code(255), ErrorClass::FfiPanic);
    }
}
