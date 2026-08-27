//! NGINX Markdown Converter - Rust FFI Library
//!
//! This library provides a memory-safe HTML to Markdown conversion engine
//! designed for integration with NGINX via FFI (Foreign Function Interface).
//!
//! # Architecture
//!
//! The library is structured into several module groups:
//!
//! **Conversion pipeline:**
//! - `parser`: HTML5 parsing using html5ever
//! - `converter`: Markdown generation from DOM tree
//! - `metadata`: Page metadata extraction
//! - `charset`: Character encoding detection and handling
//! - `decompress`: Streaming-safe bounded decompression (gzip, deflate, brotli)
//! - `security`: Input validation and sanitization
//! - `token_estimator`: Token count estimation for LLMs
//! - `etag_generator`: ETag generation using BLAKE3
//!
//! **HTTP decision and negotiation:**
//! - `decision`: Pure decision engine (context → decision + reason code)
//! - `negotiator`: Accept header negotiation (RFC 9110 q-value logic)
//! - `conditional`: Conditional request evaluation (ETag/If-Modified-Since)
//! - `header_plan`: Header mutation planning shared across FFI
//! - `forwarded`: Forwarded/X-Forwarded header parsing
//!
//! **FFI boundary:**
//! - `ffi`: C-compatible FFI interface for NGINX integration
//!
//! **Feature-gated:**
//! - `streaming`: Bounded-memory streaming conversion (enabled by default)
//!
//! # Safety
//!
//! All FFI functions are marked `unsafe` and include comprehensive safety
//! documentation. Memory allocated by Rust must be freed by Rust via the
//! provided cleanup functions.

// Module declarations
pub mod charset;
pub mod conditional;
pub mod converter;
pub mod decision;
pub mod decompress;
pub mod dynconf;
pub mod encoding;
pub mod error;
pub mod etag_generator;
pub mod ffi;
pub mod forwarded;
pub mod header_plan;
pub mod metadata;
pub mod negotiator;
pub mod parser;
pub mod security;
pub mod token_estimator;

// Streaming conversion API (feature-gated, enabled by default)
#[cfg(feature = "streaming")]
pub mod streaming;

// Re-export main types for convenience
pub use converter::MarkdownConverter;
pub use error::ConversionError;
pub use ffi::{MarkdownOptions, MarkdownResult};
pub use parser::parse_html;
