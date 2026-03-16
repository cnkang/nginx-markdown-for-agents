//! Feature-gated incremental processing API.
//!
//! This module provides [`IncrementalConverter`], a stateful converter that
//! accepts HTML input in chunks via [`feed_chunk`](IncrementalConverter::feed_chunk)
//! and produces the final Markdown output on
//! [`finalize`](IncrementalConverter::finalize).
//!
//! # Feature gate
//!
//! This module is only compiled when the `incremental` Cargo feature is
//! enabled.  When the feature is disabled the crate's public API and ABI
//! remain identical to the pre-incremental baseline.
//!
//! # Example
//!
//! ```rust
//! use nginx_markdown_converter::converter::ConversionOptions;
//! use nginx_markdown_converter::incremental::IncrementalConverter;
//!
//! let mut conv = IncrementalConverter::new(ConversionOptions::default());
//! conv.feed_chunk(b"<h1>Hello</h1>").unwrap();
//! conv.feed_chunk(b"<p>world</p>").unwrap();
//! let markdown = conv.finalize().unwrap();
//! assert!(markdown.contains("# Hello"));
//! ```

use crate::converter::{ConversionOptions, MarkdownConverter};
use crate::error::ConversionError;
use crate::parser::parse_html;

/// Stateful incremental converter that accepts input in chunks.
///
/// Chunks fed via [`feed_chunk`](Self::feed_chunk) are buffered internally.
/// Calling [`finalize`](Self::finalize) concatenates the buffered data,
/// parses the complete HTML document, and runs the standard conversion
/// pipeline to produce Markdown output.
///
/// This is a first-iteration prototype: the full document is still parsed
/// and converted in one pass during `finalize`.  Future iterations may
/// introduce true streaming conversion.
///
/// # Buffer size limit
///
/// The converter enforces a maximum accumulated buffer size of
/// [`MAX_BUFFER_SIZE`](Self::MAX_BUFFER_SIZE) (64 MiB).  Feeding data
/// beyond this limit returns a [`ConversionError`].  The NGINX module's
/// `max_size` directive provides an additional upstream guard, but this
/// limit protects the Rust layer independently.
pub struct IncrementalConverter {
    options: ConversionOptions,
    buffer: Vec<u8>,
    max_buffer_size: usize,
}

/// Default maximum buffer size for the incremental converter (64 MiB).
///
/// This is a safety limit to prevent unbounded memory growth.  The NGINX
/// module's `max_size` directive typically enforces a tighter limit
/// upstream, but this constant protects the Rust layer independently.
const INCREMENTAL_MAX_BUFFER_SIZE: usize = 64 * 1024 * 1024;

impl IncrementalConverter {
    /// Maximum accumulated buffer size in bytes (64 MiB).
    pub const MAX_BUFFER_SIZE: usize = INCREMENTAL_MAX_BUFFER_SIZE;

    /// Creates a new incremental converter with the given options.
    pub fn new(options: ConversionOptions) -> Self {
        Self {
            options,
            buffer: Vec::new(),
            max_buffer_size: INCREMENTAL_MAX_BUFFER_SIZE,
        }
    }

    /// Creates a new incremental converter with a custom buffer size limit.
    ///
    /// If `max_buffer_size` is 0, the default limit
    /// ([`MAX_BUFFER_SIZE`](Self::MAX_BUFFER_SIZE)) is used.
    pub fn with_max_buffer_size(options: ConversionOptions, max_buffer_size: usize) -> Self {
        let effective = if max_buffer_size == 0 {
            INCREMENTAL_MAX_BUFFER_SIZE
        } else {
            max_buffer_size
        };
        Self {
            options,
            buffer: Vec::new(),
            max_buffer_size: effective,
        }
    }

    /// Feeds a chunk of input data into the converter buffer.
    ///
    /// Chunks are appended in order and concatenated during
    /// [`finalize`](Self::finalize).  An empty chunk is accepted as a
    /// no-op.
    ///
    /// # Errors
    ///
    /// Returns [`ConversionError`] if the accumulated buffer size would
    /// exceed [`MAX_BUFFER_SIZE`](Self::MAX_BUFFER_SIZE).
    pub fn feed_chunk(&mut self, data: &[u8]) -> Result<(), ConversionError> {
        let new_len = self.buffer.len().saturating_add(data.len());
        if new_len > self.max_buffer_size {
            return Err(ConversionError::MemoryLimit(format!(
                "incremental buffer would exceed limit: {} + {} > {} bytes",
                self.buffer.len(),
                data.len(),
                self.max_buffer_size,
            )));
        }
        self.buffer.extend_from_slice(data);
        Ok(())
    }

    /// Finalizes the conversion and returns the Markdown output.
    ///
    /// Consumes `self`, parses the accumulated buffer as a complete HTML
    /// document, and converts it to Markdown using the options provided at
    /// construction time.
    ///
    /// # Errors
    ///
    /// Returns [`ConversionError`] if parsing or conversion fails.
    pub fn finalize(self) -> Result<String, ConversionError> {
        if self.buffer.is_empty() {
            return Ok(String::new());
        }

        let dom = parse_html(&self.buffer)?;
        let converter = MarkdownConverter::with_options(self.options);
        converter.convert(&dom)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_new_creates_empty_buffer() {
        let conv = IncrementalConverter::new(ConversionOptions::default());
        assert!(conv.buffer.is_empty());
    }

    #[test]
    fn test_feed_chunk_accumulates_data() {
        let mut conv = IncrementalConverter::new(ConversionOptions::default());
        conv.feed_chunk(b"<h1>").unwrap();
        conv.feed_chunk(b"Hello</h1>").unwrap();
        assert_eq!(conv.buffer, b"<h1>Hello</h1>");
    }

    #[test]
    fn test_feed_empty_chunk_is_noop() {
        let mut conv = IncrementalConverter::new(ConversionOptions::default());
        conv.feed_chunk(b"<p>hi</p>").unwrap();
        let len_before = conv.buffer.len();
        conv.feed_chunk(b"").unwrap();
        assert_eq!(conv.buffer.len(), len_before);
    }

    #[test]
    fn test_finalize_empty_returns_empty_string() {
        let conv = IncrementalConverter::new(ConversionOptions::default());
        let result = conv.finalize().unwrap();
        assert_eq!(result, "");
    }

    #[test]
    fn test_finalize_converts_html_to_markdown() {
        let mut conv = IncrementalConverter::new(ConversionOptions::default());
        conv.feed_chunk(b"<h1>Title</h1><p>Body text</p>").unwrap();
        let md = conv.finalize().unwrap();
        assert!(md.contains("# Title"), "Expected heading in output: {md}");
        assert!(md.contains("Body text"), "Expected body in output: {md}");
    }

    #[test]
    fn test_finalize_with_multiple_chunks() {
        let mut conv = IncrementalConverter::new(ConversionOptions::default());
        conv.feed_chunk(b"<html><body>").unwrap();
        conv.feed_chunk(b"<h1>Hello</h1>").unwrap();
        conv.feed_chunk(b"<p>World</p>").unwrap();
        conv.feed_chunk(b"</body></html>").unwrap();
        let md = conv.finalize().unwrap();
        assert!(md.contains("# Hello"), "Expected heading: {md}");
        assert!(md.contains("World"), "Expected paragraph: {md}");
    }

    #[test]
    fn test_equivalence_with_direct_conversion() {
        let html = b"<h1>Test</h1><p>Paragraph</p>";
        let options = ConversionOptions::default();

        // Incremental path
        let mut conv = IncrementalConverter::new(options.clone());
        conv.feed_chunk(html).unwrap();
        let incremental_result = conv.finalize().unwrap();

        // Direct path
        let options2 = ConversionOptions::default();
        let dom = parse_html(html).unwrap();
        let converter = MarkdownConverter::with_options(options2);
        let direct_result = converter.convert(&dom).unwrap();

        assert_eq!(incremental_result, direct_result);
    }

    #[test]
    fn test_feed_chunk_rejects_exceeding_max_buffer() {
        let mut conv = IncrementalConverter::new(ConversionOptions::default());
        // Feed a chunk that alone exceeds the limit.
        let oversized = vec![b'a'; IncrementalConverter::MAX_BUFFER_SIZE + 1];
        let err = conv.feed_chunk(&oversized).unwrap_err();
        assert_eq!(err.code(), 4, "Expected MemoryLimit error code");
    }

    #[test]
    fn test_feed_chunk_rejects_cumulative_overflow() {
        let mut conv = IncrementalConverter::new(ConversionOptions::default());
        // Feed a chunk just under the limit, then a small one that tips over.
        let big = vec![b'b'; IncrementalConverter::MAX_BUFFER_SIZE];
        conv.feed_chunk(&big).unwrap();
        let err = conv.feed_chunk(b"x").unwrap_err();
        assert_eq!(err.code(), 4, "Expected MemoryLimit error code");
    }
}
