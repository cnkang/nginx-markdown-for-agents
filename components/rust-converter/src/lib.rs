//! NGINX Markdown Converter - Rust FFI Library
//!
//! This library provides a memory-safe HTML to Markdown conversion engine
//! designed for integration with NGINX via FFI (Foreign Function Interface).
//!
//! # Architecture
//!
//! The library is structured into several modules:
//! - `ffi`: C-compatible FFI interface for NGINX integration
//! - `parser`: HTML5 parsing using html5ever
//! - `converter`: Markdown generation from DOM tree
//! - `charset`: Character encoding detection and handling
//! - `metadata`: Page metadata extraction
//! - `token_estimator`: Token count estimation for LLMs
//! - `etag_generator`: ETag generation using BLAKE3
//! - `security`: Input validation and sanitization
//!
//! # Safety
//!
//! All FFI functions are marked `unsafe` and include comprehensive safety
//! documentation. Memory allocated by Rust must be freed by Rust via the
//! provided cleanup functions.

// Module declarations
pub mod charset;
pub mod converter;
pub mod error;
pub mod etag_generator;
pub mod ffi;
pub mod metadata;
pub mod parser;
pub mod security;
pub mod token_estimator;

// Re-export main types for convenience
pub use converter::MarkdownConverter;
pub use error::ConversionError;
pub use ffi::{MarkdownOptions, MarkdownResult};
pub use parser::parse_html;
