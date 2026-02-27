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
    MemoryLimit,
    /// Invalid input data
    InvalidInput(String),
    /// Internal error
    InternalError(String),
}

impl ConversionError {
    /// Get numeric error code for FFI
    pub fn code(&self) -> u32 {
        match self {
            ConversionError::ParseError(_) => 1,
            ConversionError::EncodingError(_) => 2,
            ConversionError::Timeout => 3,
            ConversionError::MemoryLimit => 4,
            ConversionError::InvalidInput(_) => 5,
            ConversionError::InternalError(_) => 99,
        }
    }
}

impl fmt::Display for ConversionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConversionError::ParseError(msg) => write!(f, "Parse error: {}", msg),
            ConversionError::EncodingError(msg) => write!(f, "Encoding error: {}", msg),
            ConversionError::Timeout => write!(f, "Conversion timeout exceeded"),
            ConversionError::MemoryLimit => write!(f, "Memory limit exceeded"),
            ConversionError::InvalidInput(msg) => write!(f, "Invalid input: {}", msg),
            ConversionError::InternalError(msg) => write!(f, "Internal error: {}", msg),
        }
    }
}

impl std::error::Error for ConversionError {}
