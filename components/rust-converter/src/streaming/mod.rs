//! Bounded-memory streaming HTML-to-Markdown conversion engine (Engine B).
//!
//! This module provides [`StreamingConverter`], an event-driven streaming
//! converter that processes HTML input in chunks without building a full DOM
//! tree. It is feature-gated behind the `streaming` Cargo feature.
//!
//! # Architecture
//!
//! The streaming pipeline consists of:
//! - Charset detection and transcoding ([`charset::CharsetState`])
//! - html5ever TokenSink-based tokenization ([`tokenizer::StreamingTokenizer`])
//! - Streaming security sanitization ([`sanitizer::StreamingSanitizer`])
//! - Structural state machine for document context tracking
//!   ([`state_machine::StructuralStateMachine`])
//! - Incremental Markdown emission with flush points
//!   ([`emitter::IncrementalEmitter`])
//!
//! # Usage
//!
//! ```no_run
//! use nginx_markdown_converter::converter::ConversionOptions;
//! use nginx_markdown_converter::streaming::{StreamingConverter, MemoryBudget};
//!
//! let mut conv = StreamingConverter::new(
//!     ConversionOptions::default(),
//!     MemoryBudget::default(),
//! );
//! conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
//!
//! let _output = conv.feed_chunk(b"<h1>Hello</h1><p>World</p>").unwrap();
//! let _result = conv.finalize().unwrap();
//! ```
//!
//! # Feature Gate
//!
//! This module is only compiled when the `streaming` Cargo feature is enabled.
//! When disabled, the crate's public API and ABI remain identical to the
//! pre-streaming baseline.

// ════════════════════════════════════════════════════════════════════
// Capability Matrix (0.5.0)
//
// Status values (aligned with sub-spec #12):
//   streaming-supported         — fully supported in the streaming path
//   full-buffer-only            — only available via the full-buffer path
//   pre-commit-fallback-only    — streaming path falls back to full-buffer
//                                 during the pre-commit phase
//
// ┌─────────────────────────────────────┬──────────────────────────────┐
// │ Capability                          │ Status                       │
// ├─────────────────────────────────────┼──────────────────────────────┤
// │ Headings (h1–h6)                    │ streaming-supported          │
// │ Paragraphs                          │ streaming-supported          │
// │ Ordered lists (incl. nesting)       │ streaming-supported          │
// │ Unordered lists (incl. nesting)     │ streaming-supported          │
// │ Links                               │ streaming-supported          │
// │ Images                              │ streaming-supported          │
// │ Code blocks (incl. language id)     │ streaming-supported          │
// │ Inline code                         │ streaming-supported          │
// │ Blockquotes (incl. nesting)         │ streaming-supported          │
// │ Bold / Italic                       │ streaming-supported          │
// │ HTML entity decoding                │ streaming-supported          │
// │ Embedded content URL extraction     │ streaming-supported          │
// │ Media element URL extraction        │ streaming-supported          │
// │ Form content preservation           │ streaming-supported          │
// │ Security sanitization               │ streaming-supported          │
// │ Charset detection / transcoding     │ streaming-supported          │
// │ Token estimate                      │ streaming-supported          │
// │ ETag (internal hash)                │ streaming-supported          │
// │ Front matter (within lookahead)     │ streaming-supported          │
// │ Tables (incl. alignment)            │ pre-commit-fallback-only     │
// │ Front matter (beyond lookahead)     │ pre-commit-fallback-only     │
// │ Noise region pruning                │ pre-commit-fallback-only     │
// │ ETag (response header)              │ full-buffer-only             │
// │ Fast-path evaluation                │ P1 (optional, deferred)      │
// └─────────────────────────────────────┴──────────────────────────────┘
//
// Known differences from full-buffer path:
// - html5ever TokenSink operates at the token level without tree builder
//   error recovery. Malformed HTML may produce different output compared
//   to the full-buffer path which uses the tree builder. These differences
//   are quantified by sub-spec #16 (streaming-parity-diff-testing).
// ════════════════════════════════════════════════════════════════════

pub mod budget;
pub mod charset;
pub mod converter;
pub mod emitter;
pub mod sanitizer;
pub mod state_machine;
pub mod tokenizer;
pub mod types;

// Re-export primary public types
pub use budget::MemoryBudget;
pub use converter::StreamingConverter;
pub use types::{ChunkOutput, FallbackReason, StreamingResult, StreamingStats};
