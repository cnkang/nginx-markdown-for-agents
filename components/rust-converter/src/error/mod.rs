//! Error types for conversion operations.
//!
//! Defines [`ConversionError`] with variants for each failure mode in the
//! HTML-to-Markdown pipeline: parse errors, encoding errors, timeouts,
//! memory/budget limits, invalid input, and streaming-specific conditions
//! (fallback requests and post-commit errors).
//!
//! The [`ConversionError::code`] method maps each variant to a stable numeric
//! FFI error code (1–11, 99) that is shared across the Rust↔C boundary via
//! `markdown_converter.h`.  `markdown_converter.h` additionally defines
//! `ERROR_SUCCESS = 0` and decompression-category codes 101–105 used by the
//! FFI decompression result path (`FFIDecompResult.error_category`); those
//! codes are not produced by `ConversionError::code()` itself.  Adding a
//! new `ConversionError` variant requires updating both this mapping and
//! the C-side classification in `ngx_http_markdown_error.c`.
//!
//! ## Error Classification (spec 51)
//!
//! The [`classification`] submodule defines the unified error policy layer:
//! error classes, policies, and behavior decisions that determine how each
//! error type is handled at runtime (pass-through, return status, or
//! terminate connection).

pub mod classification;

use std::fmt;

/// Errors that can occur during HTML to Markdown conversion
#[derive(Debug, Clone)]
pub enum ConversionError {
    /// HTML parsing failed
    ParseError(String),
    /// Character encoding error
    EncodingError(String),
    /// Conversion timeout exceeded
    Timeout,
    /// Memory limit exceeded
    MemoryLimit(String),
    /// Invalid input data
    InvalidInput(String),
    /// Internal error
    InternalError(String),

    /// Memory budget exceeded during streaming conversion.
    #[cfg(feature = "streaming")]
    BudgetExceeded {
        /// Pipeline stage that exceeded its budget.
        stage: String,
        /// Bytes used when the breach was detected.
        used: usize,
        /// Budget limit in bytes.
        limit: usize,
    },

    /// Streaming engine requests fallback to full-buffer conversion.
    /// Only produced during the pre-commit phase.
    #[cfg(feature = "streaming")]
    StreamingFallback {
        /// Reason for the fallback.
        reason: crate::streaming::types::FallbackReason,
    },

    /// Error after the streaming engine has already emitted partial output.
    /// The caller must handle this according to its stream failure policy.
    #[cfg(feature = "streaming")]
    PostCommitError {
        /// Description of the error.
        reason: String,
        /// Number of Markdown bytes already emitted before the error.
        bytes_emitted: usize,
        /// Error code of the original error before post-commit wrapping.
        /// Preserves the classification (e.g. timeout=3, budget=6) so
        /// downstream consumers can categorise the failure correctly.
        original_code: u32,
    },

    /// Decompression budget exceeded: the decompressed output size
    /// exceeds the configured decompress_max_size limit.
    /// Distinct from BudgetExceeded (streaming working-set) to
    /// separate decompression-layer limits from streaming-layer limits.
    DecompressionBudgetExceeded {
        /// Decompressed bytes when the breach was detected.
        used: usize,
        /// Configured decompression budget in bytes.
        limit: usize,
    },

    /// Parse timeout: the HTML parsing phase exceeded the
    /// configured parse_timeout deadline.
    ParseTimeout,

    /// Parse budget exceeded: the parser memory allocation
    /// exceeded the configured parser_memory_budget.
    ParseBudgetExceeded {
        /// Bytes allocated when the breach was detected.
        used: usize,
        /// Configured parser budget in bytes.
        limit: usize,
    },
}

impl ConversionError {
    /// Map a `ConversionError` to its numeric FFI error code.
    ///
    /// The returned code identifies the error for FFI boundaries:
    /// - `ParseError(_)` -> `1`
    /// - `EncodingError(_)` -> `2`
    /// - `Timeout` -> `3`
    /// - `MemoryLimit(_)` -> `4`
    /// - `InvalidInput(_)` -> `5`
    /// - `InternalError(_)` -> `99`
    ///
    /// When compiled with the `streaming` feature enabled, the following additional mappings apply:
    /// - `BudgetExceeded { .. }` -> `6`
    /// - `StreamingFallback { .. }` -> `7`
    /// - `PostCommitError { .. }` -> `8`
    ///
    /// Decompression-specific errors:
    /// - `DecompressionBudgetExceeded { .. }` -> `9`
    ///
    /// Parse-specific errors:
    /// - `ParseTimeout` -> `10`
    /// - `ParseBudgetExceeded { .. }` -> `11`
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::error::ConversionError;
    ///
    /// let err = ConversionError::Timeout;
    /// assert_eq!(err.code(), 3);
    /// ```
    pub fn code(&self) -> u32 {
        #[cfg(feature = "streaming")]
        use crate::ffi::{ERROR_BUDGET_EXCEEDED, ERROR_POST_COMMIT, ERROR_STREAMING_FALLBACK};
        use crate::ffi::{
            ERROR_DECOMPRESSION_BUDGET_EXCEEDED, ERROR_ENCODING, ERROR_INTERNAL,
            ERROR_INVALID_INPUT, ERROR_MEMORY_LIMIT, ERROR_PARSE, ERROR_PARSE_BUDGET_EXCEEDED,
            ERROR_PARSE_TIMEOUT, ERROR_TIMEOUT,
        };

        match self {
            ConversionError::ParseError(_) => ERROR_PARSE,
            ConversionError::EncodingError(_) => ERROR_ENCODING,
            ConversionError::Timeout => ERROR_TIMEOUT,
            ConversionError::MemoryLimit(_) => ERROR_MEMORY_LIMIT,
            ConversionError::InvalidInput(_) => ERROR_INVALID_INPUT,
            #[cfg(feature = "streaming")]
            ConversionError::BudgetExceeded { .. } => ERROR_BUDGET_EXCEEDED,
            #[cfg(feature = "streaming")]
            ConversionError::StreamingFallback { .. } => ERROR_STREAMING_FALLBACK,
            #[cfg(feature = "streaming")]
            ConversionError::PostCommitError { .. } => ERROR_POST_COMMIT,
            ConversionError::DecompressionBudgetExceeded { .. } => {
                ERROR_DECOMPRESSION_BUDGET_EXCEEDED
            }
            ConversionError::ParseTimeout => ERROR_PARSE_TIMEOUT,
            ConversionError::ParseBudgetExceeded { .. } => ERROR_PARSE_BUDGET_EXCEEDED,
            ConversionError::InternalError(_) => ERROR_INTERNAL,
        }
    }
}

impl fmt::Display for ConversionError {
    /// Format a human-readable message describing the conversion error for logs and FFI boundaries.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::error::ConversionError;
    ///
    /// let err = ConversionError::ParseError("unexpected tag".into());
    /// assert_eq!(format!("{}", err), "Parse error: unexpected tag");
    /// ```
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConversionError::ParseError(msg) => write!(f, "Parse error: {}", msg),
            ConversionError::EncodingError(msg) => write!(f, "Encoding error: {}", msg),
            ConversionError::Timeout => write!(f, "Conversion timeout exceeded"),
            ConversionError::MemoryLimit(msg) => write!(f, "Memory limit exceeded: {}", msg),
            ConversionError::InvalidInput(msg) => write!(f, "Invalid input: {}", msg),
            ConversionError::InternalError(msg) => write!(f, "Internal error: {}", msg),
            #[cfg(feature = "streaming")]
            ConversionError::BudgetExceeded { stage, used, limit } => {
                write!(
                    f,
                    "Budget exceeded in {}: used {} bytes, limit {} bytes",
                    stage, used, limit
                )
            }
            #[cfg(feature = "streaming")]
            ConversionError::StreamingFallback { reason } => {
                write!(f, "Streaming fallback: {}", reason)
            }
            #[cfg(feature = "streaming")]
            ConversionError::PostCommitError {
                reason,
                bytes_emitted,
                original_code,
            } => {
                write!(
                    f,
                    "Post-commit error (original_code={}) after {} bytes emitted: {}",
                    original_code, bytes_emitted, reason
                )
            }
            ConversionError::DecompressionBudgetExceeded { used, limit } => {
                write!(
                    f,
                    "Decompression budget exceeded: used {} bytes, limit {} bytes",
                    used, limit
                )
            }
            ConversionError::ParseTimeout => {
                write!(f, "Parse timeout exceeded")
            }
            ConversionError::ParseBudgetExceeded { used, limit } => {
                write!(
                    f,
                    "Parse budget exceeded: used {} bytes, limit {} bytes",
                    used, limit
                )
            }
        }
    }
}

impl std::error::Error for ConversionError {}
