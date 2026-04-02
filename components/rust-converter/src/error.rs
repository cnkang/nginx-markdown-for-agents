//! Error types for conversion operations

use std::fmt;

/// Errors that can occur during HTML to Markdown conversion
#[derive(Debug)]
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
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::error::ConversionError;
    ///
    /// let err = ConversionError::Timeout;
    /// assert_eq!(err.code(), 3);
    /// ```
    pub fn code(&self) -> u32 {
        match self {
            ConversionError::ParseError(_) => 1,
            ConversionError::EncodingError(_) => 2,
            ConversionError::Timeout => 3,
            ConversionError::MemoryLimit(_) => 4,
            ConversionError::InvalidInput(_) => 5,
            #[cfg(feature = "streaming")]
            ConversionError::BudgetExceeded { .. } => 6,
            #[cfg(feature = "streaming")]
            ConversionError::StreamingFallback { .. } => 7,
            #[cfg(feature = "streaming")]
            ConversionError::PostCommitError { .. } => 8,
            ConversionError::InternalError(_) => 99,
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
            } => {
                write!(
                    f,
                    "Post-commit error after {} bytes emitted: {}",
                    bytes_emitted, reason
                )
            }
        }
    }
}

impl std::error::Error for ConversionError {}
