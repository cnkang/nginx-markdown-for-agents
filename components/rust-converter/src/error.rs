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
}

impl ConversionError {
    /// Map a `ConversionError` variant to its numeric FFI error code.
    ///
    /// The mapping is:
    /// - `ParseError(_)` -> 1
    /// - `EncodingError(_)` -> 2
    /// - `Timeout` -> 3
    /// - `MemoryLimit(_)` -> 4
    /// - `InvalidInput(_)` -> 5
    /// - `InternalError(_)` -> 99
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
            ConversionError::InternalError(_) => 99,
        }
    }
}

impl fmt::Display for ConversionError {
    /// Formats a human-readable message for the error suitable for logs and FFI boundaries.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::error::ConversionError;
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
        }
    }
}

impl std::error::Error for ConversionError {}
