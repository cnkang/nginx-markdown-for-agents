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
//! ```ignore
//! use nginx_markdown_converter::converter::ConversionOptions;
//! use nginx_markdown_converter::incremental::IncrementalConverter;
//!
//! let mut conv = IncrementalConverter::new(ConversionOptions::default());
//! conv.feed_chunk(b"<h1>Hello</h1>").unwrap();
//! conv.feed_chunk(b"<p>world</p>").unwrap();
//! let markdown = conv.finalize().unwrap();
//! assert!(markdown.contains("# Hello"));
//! ```

use std::time::Duration;

use crate::converter::{ConversionContext, ConversionOptions, MarkdownConverter};
use crate::error::ConversionError;
use crate::parser::parse_html_with_charset;

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
    content_type: Option<String>,
    timeout: Duration,
}

/// Default maximum buffer size for the incremental converter (64 MiB).
///
/// This is a safety limit to prevent unbounded memory growth.  The NGINX
/// module's `max_size` directive typically enforces a tighter limit
/// upstream, but this constant protects the Rust layer independently.
const INCREMENTAL_MAX_BUFFER_SIZE: usize = 64 * 1024 * 1024;

impl IncrementalConverter {
    /// Maximum accumulated buffer size in bytes (64 MiB).
    pub const MAX_BUFFER_SIZE: usize = 64 * 1024 * 1024;

    /// Creates a new IncrementalConverter configured with the provided conversion options.
    ///
    /// The converter is initialized with an empty input buffer, the default maximum buffer size,
    /// no content type set, and a zero timeout.
    ///
    /// # Returns
    ///
    /// An `IncrementalConverter` configured with the provided options.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use nginx_markdown_converter::converter::ConversionOptions;
    /// use nginx_markdown_converter::incremental::IncrementalConverter;
    /// let opts = ConversionOptions::default();
    /// let _conv = IncrementalConverter::new(opts);
    /// ```ignore
    pub fn new(options: ConversionOptions) -> Self {
        Self {
            options,
            buffer: Vec::new(),
            max_buffer_size: INCREMENTAL_MAX_BUFFER_SIZE,
            content_type: None,
            timeout: Duration::ZERO,
        }
    }

    /// Constructs an IncrementalConverter with a specified maximum buffer size.
    ///
    /// If `max_buffer_size` is 0, the default `MAX_BUFFER_SIZE` is used.
    /// Values above the hard 64 MiB ceiling are intentionally clamped back to
    /// [`MAX_BUFFER_SIZE`](Self::MAX_BUFFER_SIZE) until a true streaming parser
    /// replaces the current DOM-based implementation.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use nginx_markdown_converter::converter::ConversionOptions;
    /// use nginx_markdown_converter::incremental::IncrementalConverter;
    /// let options = ConversionOptions::default();
    /// let conv = IncrementalConverter::with_max_buffer_size(options, 10 * 1024 * 1024);
    /// ```
    pub fn with_max_buffer_size(options: ConversionOptions, max_buffer_size: usize) -> Self {
        let effective = if max_buffer_size == 0 {
            INCREMENTAL_MAX_BUFFER_SIZE
        } else {
            max_buffer_size.min(INCREMENTAL_MAX_BUFFER_SIZE)
        };
        Self {
            options,
            buffer: Vec::new(),
            max_buffer_size: effective,
            content_type: None,
            timeout: Duration::ZERO,
        }
    }

    /// Set the `Content-Type` header value used for charset detection during conversion.
    ///
    /// When a content type is provided (for example, `text/html; charset=iso-8859-1`),
    /// the parser will honor the charset and transcode the input to UTF-8 before conversion,
    /// matching the behavior of the full-buffer conversion path. Passing `None` clears any
    /// previously set content type.
    ///
    /// # Parameters
    ///
    /// - `content_type`: Optional MIME `Content-Type` header value used to influence charset detection.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use nginx_markdown_converter::incremental::IncrementalConverter;
    /// let mut conv = IncrementalConverter::new(Default::default());
    /// conv.set_content_type(Some("text/html; charset=iso-8859-1".to_string()));
    /// // feed chunks and finalize...
    /// ```
    pub fn set_content_type(&mut self, content_type: Option<String>) {
        self.content_type = content_type;
    }

    /// Configure the cooperative timeout used during conversion finalization.
    /// A zero `Duration` disables timeout checking (the default).
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use std::time::Duration;
    /// use nginx_markdown_converter::incremental::IncrementalConverter;
    ///
    /// let mut conv = IncrementalConverter::new(Default::default());
    /// conv.set_timeout(Duration::from_secs(2));
    /// ```
    pub fn set_timeout(&mut self, timeout: Duration) {
        self.timeout = timeout;
    }

    /// Appends a slice of input bytes to the converter's internal buffer.
    ///
    /// Chunks are appended in order and concatenated during `finalize`. An empty
    /// slice is accepted as a no-op.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::MemoryLimit` if appending `data` would make the
    /// internal buffer exceed `MAX_BUFFER_SIZE`.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use nginx_markdown_converter::incremental::IncrementalConverter;
    /// let mut conv = IncrementalConverter::new(Default::default());
    /// conv.feed_chunk(b"<h1>Hello</h1>").unwrap();
    /// let md = conv.finalize().unwrap();
    /// assert!(md.contains("Hello"));
    /// ```
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

    /// Finalizes the converter and produces Markdown from the accumulated HTML.
    ///
    /// Consumes the `IncrementalConverter`, parses the buffered bytes as a complete HTML document
    /// (honoring `content_type` for charset detection when present), and converts the parsed DOM
    /// to Markdown using the options supplied at construction.
    ///
    /// # Errors
    ///
    /// Returns a [`ConversionError`] if the buffer is empty, if HTML parsing fails, or if conversion
    /// is unsuccessful.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use nginx_markdown_converter::converter::ConversionOptions;
    /// use nginx_markdown_converter::incremental::IncrementalConverter;
    /// let mut conv = IncrementalConverter::new(ConversionOptions::default());
    /// conv.feed_chunk(b"<h1>Hello</h1><p>world</p>").unwrap();
    /// let md = conv.finalize().unwrap();
    /// assert!(md.contains("Hello"));
    /// ```
    pub fn finalize(self) -> Result<String, ConversionError> {
        if self.buffer.is_empty() {
            return Err(ConversionError::InvalidInput(
                "HTML input is empty".to_string(),
            ));
        }

        let ct_ref = self.content_type.as_deref();

        let mut ctx = ConversionContext::new(self.timeout);
        ctx.set_input_size_hint(self.buffer.len());

        let dom = parse_html_with_charset(&self.buffer, ct_ref)?;
        ctx.check_timeout()?;

        let converter = MarkdownConverter::with_options(self.options);
        converter.convert_with_context(&dom, &mut ctx)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse_html;
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
    fn test_finalize_empty_returns_invalid_input_error() {
        let conv = IncrementalConverter::new(ConversionOptions::default());
        let err = conv.finalize().unwrap_err();
        assert!(
            matches!(err, ConversionError::InvalidInput(_)),
            "Expected InvalidInput error for empty buffer, got: {err:?}"
        );
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
        let limit = 1024;
        let mut conv =
            IncrementalConverter::with_max_buffer_size(ConversionOptions::default(), limit);
        let oversized = vec![b'a'; limit + 1];
        let err = conv.feed_chunk(&oversized).unwrap_err();
        assert_eq!(err.code(), 4, "Expected MemoryLimit error code");
    }

    #[test]
    fn test_feed_chunk_rejects_cumulative_overflow() {
        let limit = 1024;
        let mut conv =
            IncrementalConverter::with_max_buffer_size(ConversionOptions::default(), limit);
        let big = vec![b'b'; limit];
        conv.feed_chunk(&big).unwrap();
        let err = conv.feed_chunk(b"x").unwrap_err();
        assert_eq!(err.code(), 4, "Expected MemoryLimit error code");
    }
}
