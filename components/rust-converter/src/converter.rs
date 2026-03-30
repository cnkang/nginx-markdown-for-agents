//! Markdown converter - transforms DOM tree to Markdown
//!
//! This module provides the core conversion logic for transforming HTML DOM trees
//! into clean, semantic Markdown output. The converter is designed to be extensible,
//! supporting multiple Markdown flavors and configurable conversion strategies.
//!
//! # Conversion Strategy
//!
//! The converter uses a depth-first traversal of the DOM tree, processing each node
//! according to its type and applying appropriate Markdown formatting rules. The
//! conversion process follows these principles:
//!
//! 1. **Semantic Preservation**: Maintain the semantic structure of the HTML document
//!    (headings, paragraphs, lists, etc.) in the Markdown output
//! 2. **Content Extraction**: Focus on extracting meaningful content while removing
//!    or simplifying non-content elements (scripts, styles, navigation)
//! 3. **Deterministic Output**: Produce consistent, normalized Markdown for identical
//!    HTML input to enable reliable caching and ETag generation
//! 4. **Graceful Degradation**: Handle malformed HTML and edge cases without failing
//!
//! # DOM Traversal
//!
//! The converter traverses the DOM tree in document order (depth-first, left-to-right),
//! maintaining a depth counter to track nesting levels. This enables:
//!
//! - Proper indentation for nested structures (lists, blockquotes)
//! - Detection of deeply nested content that may need simplification
//! - Context-aware formatting decisions based on parent elements
//!
//! # Element Handlers
//!
//! Each HTML element type has a dedicated handler that determines how it should be
//! converted to Markdown. The current implementation supports:
//!
//! - **Headings (h1-h6)**: Converted to ATX-style headings (`#` to `######`)
//! - **Paragraphs (p)**: Converted to plain text with blank line separation
//! - **Text nodes**: Extracted and normalized (whitespace collapsed)
//!
//! Additional element handlers (links, images, lists, code, etc.) will be added in
//! subsequent tasks to provide comprehensive HTML to Markdown conversion.
//!
//! # Examples
//!
//! ## Basic Heading Conversion
//!
//! Input HTML:
//! ```html
//! <h1>Main Title</h1>
//! <h2>Subtitle</h2>
//! <p>Some content here.</p>
//! ```
//!
//! Output Markdown:
//! ```markdown
//! # Main Title
//!
//! ## Subtitle
//!
//! Some content here.
//! ```
//!
//! ## Nested Structure
//!
//! Input HTML:
//! ```html
//! <div>
//!   <h1>Title</h1>
//!   <p>First paragraph.</p>
//!   <p>Second paragraph.</p>
//! </div>
//! ```
//!
//! Output Markdown:
//! ```markdown
//! # Title
//!
//! First paragraph.
//!
//! Second paragraph.
//! ```
//!
//! ## Text Normalization
//!
//! Input HTML:
//! ```html
//! <p>Text   with    multiple    spaces</p>
//! <p>Text
//! with
//! newlines</p>
//! ```
//!
//! Output Markdown:
//! ```markdown
//! Text with multiple spaces
//!
//! Text with newlines
//! ```
//!
//! # Performance Considerations
//!
//! - String concatenation uses a pre-allocated buffer to minimize allocations
//! - DOM traversal is single-pass with no backtracking
//! - Text normalization is performed inline during traversal
//! - Memory usage is proportional to output size, not input DOM size

use crate::error::ConversionError;
use html5ever::Attribute;
use markup5ever_rcdom::{Handle, NodeData, RcDom};
use std::cell::Ref;
use std::time::{Duration, Instant};

mod blocks;
pub(crate) mod fast_path;
mod front_matter;
mod inline;
pub(crate) mod large_response;
mod normalize;
pub(crate) mod pruning;
mod tables;
mod traversal;

/// Threshold in bytes above which the fused normalizer is used instead of
/// the two-pass `normalize_output`. This avoids a second full-size allocation
/// for large documents.
///
/// This value is aligned with the default `markdown_large_body_threshold`
/// NGINX directive (256 KB). If the directive default changes, this constant
/// should be updated to match.
const LARGE_BODY_THRESHOLD: usize = 256 * 1024; // 256 KB

/// Markdown flavor selection
#[derive(Debug, Clone, Copy)]
pub enum MarkdownFlavor {
    /// CommonMark baseline
    CommonMark,
    /// GitHub Flavored Markdown
    GitHubFlavoredMarkdown,
}

/// Table column alignment (GFM)
#[derive(Debug, Clone, Copy)]
enum TableAlignment {
    Left,
    Center,
    Right,
}

/// Conversion options
#[derive(Debug, Clone)]
pub struct ConversionOptions {
    /// Markdown flavor to generate
    pub flavor: MarkdownFlavor,
    /// Include YAML front matter
    pub include_front_matter: bool,
    /// Extract metadata
    pub extract_metadata: bool,
    /// Simplify navigation elements
    pub simplify_navigation: bool,
    /// Preserve tables (GFM only)
    pub preserve_tables: bool,
    /// Base URL for resolving relative URLs (scheme://host/path)
    pub base_url: Option<String>,
    /// Resolve relative URLs to absolute URLs
    pub resolve_relative_urls: bool,
}

impl Default for ConversionOptions {
    /// Return conservative conversion defaults used by the top-level converter.
    fn default() -> Self {
        Self {
            flavor: MarkdownFlavor::CommonMark,
            include_front_matter: false,
            extract_metadata: false,
            simplify_navigation: true,
            preserve_tables: true,
            base_url: None,
            resolve_relative_urls: true,
        }
    }
}

/// Conversion context for tracking timeout and node count
///
/// This struct implements a cooperative timeout mechanism that checks elapsed time
/// at regular intervals during conversion. The timeout is cooperative (not preemptive),
/// meaning conversion must reach a checkpoint to detect timeout.
///
/// # Timeout Strategy
///
/// The cooperative timeout mechanism provides resource protection without thread spawning:
///
/// - **No thread spawning**: Avoids thread explosion under high concurrency
/// - **NGINX-friendly**: Compatible with event-driven worker model
/// - **Predictable**: No background threads consuming CPU after timeout
/// - **Simple**: Easier to reason about and test
///
/// # Trade-offs
///
/// - **Not preemptive**: Conversion must reach a checkpoint to detect timeout
/// - **Worst-case delay**: Timeout detection delayed until next checkpoint
/// - **Mitigation**: Checkpoints at frequent intervals (every 100 nodes, every parse step)
///
/// # Checkpoints
///
/// Timeout is checked at these key points:
/// 1. After HTML parsing
/// 2. Every 100 DOM nodes during traversal
/// 3. After Markdown generation
///
/// # Example
///
/// ```rust
/// use std::time::Duration;
/// use nginx_markdown_converter::converter::ConversionContext;
///
/// // Create context with 5 second timeout
/// let ctx = ConversionContext::new(Duration::from_secs(5));
///
/// // Check timeout during processing
/// if let Err(e) = ctx.check_timeout() {
///     println!("Timeout exceeded: {}", e);
/// }
/// ```
#[derive(Debug)]
pub struct ConversionContext {
    /// Start time of conversion
    start_time: Instant,
    /// Timeout duration (0 means no timeout)
    timeout: Duration,
    /// Number of nodes processed (for checkpoint frequency)
    node_count: u32,
    /// Whether the document qualified for the fast path.
    ///
    /// When `true`, the traversal can skip branches that are unreachable for
    /// fast-path documents (e.g., form control extraction, embedded content
    /// handling, table/media element processing). This avoids per-node
    /// method calls and attribute inspection for code paths that the
    /// qualification scan has already proven unreachable.
    pub(crate) is_fast_path: bool,
}

impl ConversionContext {
    /// Create a new conversion context with the specified timeout
    ///
    /// # Arguments
    ///
    /// * `timeout` - Maximum duration for conversion (Duration::ZERO means no timeout)
    ///
    /// # Example
    ///
    /// ```rust
    /// use std::time::Duration;
    /// use nginx_markdown_converter::converter::ConversionContext;
    ///
    /// // 5 second timeout
    /// let ctx = ConversionContext::new(Duration::from_secs(5));
    ///
    /// // No timeout
    /// let ctx_unlimited = ConversionContext::new(Duration::ZERO);
    /// ```
    pub fn new(timeout: Duration) -> Self {
        Self {
            start_time: Instant::now(),
            timeout,
            node_count: 0,
            is_fast_path: false,
        }
    }

    /// Check if timeout has been exceeded
    ///
    /// This method should be called at regular checkpoints during conversion.
    /// Returns `Ok(())` if within timeout, or `Err(ConversionError::Timeout)` if exceeded.
    ///
    /// # Returns
    ///
    /// - `Ok(())` - Conversion is within timeout limit
    /// - `Err(ConversionError::Timeout)` - Timeout has been exceeded
    ///
    /// # Example
    ///
    /// ```rust
    /// use std::time::Duration;
    /// use nginx_markdown_converter::converter::ConversionContext;
    ///
    /// let ctx = ConversionContext::new(Duration::from_millis(100));
    /// std::thread::sleep(Duration::from_millis(150));
    ///
    /// assert!(ctx.check_timeout().is_err());
    /// ```
    pub fn check_timeout(&self) -> Result<(), ConversionError> {
        // If timeout is zero, no timeout is enforced
        if self.timeout.is_zero() {
            return Ok(());
        }

        let elapsed = self.start_time.elapsed();
        if elapsed > self.timeout {
            return Err(ConversionError::Timeout);
        }

        Ok(())
    }

    /// Increment node count and check timeout if at checkpoint
    ///
    /// This method should be called for each DOM node processed. It automatically
    /// checks timeout every 100 nodes to balance performance and responsiveness.
    ///
    /// # Returns
    ///
    /// - `Ok(())` - Conversion is within timeout limit
    /// - `Err(ConversionError::Timeout)` - Timeout has been exceeded
    ///
    /// # Example
    ///
    /// ```rust
    /// use std::time::Duration;
    /// use nginx_markdown_converter::converter::ConversionContext;
    ///
    /// let mut ctx = ConversionContext::new(Duration::from_secs(5));
    ///
    /// // Process nodes
    /// for _ in 0..1000 {
    ///     ctx.increment_and_check()?;
    /// }
    /// # Ok::<(), nginx_markdown_converter::error::ConversionError>(())
    /// ```
    pub fn increment_and_check(&mut self) -> Result<(), ConversionError> {
        self.node_count += 1;

        // Check timeout every 100 nodes (checkpoint frequency)
        if self.node_count.is_multiple_of(100) {
            self.check_timeout()?;
        }

        Ok(())
    }

    /// Get elapsed time since conversion started
    ///
    /// # Returns
    ///
    /// Duration representing elapsed time
    pub fn elapsed(&self) -> Duration {
        self.start_time.elapsed()
    }

    /// Get number of nodes processed
    ///
    /// # Returns
    ///
    /// Number of DOM nodes processed so far
    pub fn node_count(&self) -> u32 {
        self.node_count
    }
}

/// Main Markdown converter
///
/// The `MarkdownConverter` is responsible for transforming HTML DOM trees into
/// clean Markdown output. It maintains conversion state and configuration, and
/// provides the main entry point for conversion operations.
///
/// # Design
///
/// The converter is designed to be:
/// - **Stateless**: Each conversion is independent, allowing concurrent use
/// - **Configurable**: Supports multiple Markdown flavors and conversion options
/// - **Extensible**: Element handlers can be easily added or modified
/// - **Deterministic**: Produces consistent output for identical input
///
/// # Usage
///
/// ```rust
/// use nginx_markdown_converter::converter::{MarkdownConverter, ConversionOptions, MarkdownFlavor};
/// use nginx_markdown_converter::parser::parse_html;
///
/// // Create converter with default options (CommonMark)
/// let converter = MarkdownConverter::new();
///
/// // Or with custom options
/// let options = ConversionOptions {
///     flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
///     ..Default::default()
/// };
/// let converter = MarkdownConverter::with_options(options);
///
/// // Parse HTML and convert
/// let html = b"<h1>Title</h1><p>Content</p>";
/// let dom = parse_html(html).expect("Failed to parse");
/// let markdown = converter.convert(&dom).expect("Failed to convert");
/// ```
pub struct MarkdownConverter {
    #[allow(dead_code)] // Will be used in future tasks for flavor-specific logic
    options: ConversionOptions,
    security_validator: crate::security::SecurityValidator,
}

impl MarkdownConverter {
    /// Create a new converter with default options
    ///
    /// The default configuration uses CommonMark flavor with standard settings:
    /// - No YAML front matter
    /// - No metadata extraction
    /// - Navigation simplification enabled
    /// - Table preservation enabled (for GFM)
    pub fn new() -> Self {
        Self {
            options: ConversionOptions::default(),
            security_validator: crate::security::SecurityValidator::new(),
        }
    }

    /// Create a new converter with custom options
    ///
    /// # Arguments
    ///
    /// * `options` - Conversion options specifying flavor and behavior
    ///
    /// # Examples
    ///
    /// ```rust
    /// use nginx_markdown_converter::converter::{MarkdownConverter, ConversionOptions, MarkdownFlavor};
    ///
    /// let options = ConversionOptions {
    ///     flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
    ///     include_front_matter: true,
    ///     extract_metadata: true,
    ///     ..Default::default()
    /// };
    /// let converter = MarkdownConverter::with_options(options);
    /// ```
    pub fn with_options(options: ConversionOptions) -> Self {
        Self {
            options,
            security_validator: crate::security::SecurityValidator::new(),
        }
    }

    /// Convert DOM tree to Markdown
    ///
    /// This is the main entry point for conversion. It traverses the DOM tree
    /// and generates Markdown output according to the configured options.
    ///
    /// # Arguments
    ///
    /// * `dom` - Parsed DOM tree from html5ever
    ///
    /// # Returns
    ///
    /// Returns `Ok(String)` containing the Markdown output on success.
    /// Returns `Err(ConversionError)` if conversion fails.
    ///
    /// # Errors
    ///
    /// This function may return errors in the following cases:
    /// - Internal conversion errors (should be rare)
    /// - Memory allocation failures (extreme cases)
    ///
    /// # Examples
    ///
    /// ```rust
    /// use nginx_markdown_converter::converter::MarkdownConverter;
    /// use nginx_markdown_converter::parser::parse_html;
    ///
    /// let html = b"<h1>Hello World</h1><p>This is a test.</p>";
    /// let dom = parse_html(html).expect("Parse failed");
    /// let converter = MarkdownConverter::new();
    /// let markdown = converter.convert(&dom).expect("Conversion failed");
    /// assert!(markdown.contains("# Hello World"));
    /// ```
    pub fn convert(&self, dom: &RcDom) -> Result<String, ConversionError> {
        // Create a context with no timeout for backward compatibility
        let mut ctx = ConversionContext::new(std::time::Duration::ZERO);
        self.convert_with_context(dom, &mut ctx)
    }

    /// Convert DOM tree to Markdown with timeout support
    ///
    /// This method provides cooperative timeout support for conversion operations.
    /// The timeout is checked at regular intervals during traversal (every 100 nodes).
    ///
    /// # Arguments
    ///
    /// * `dom` - Parsed DOM tree from html5ever
    /// * `ctx` - Conversion context for timeout tracking
    ///
    /// # Returns
    ///
    /// Returns `Ok(String)` containing the Markdown output on success.
    /// Returns `Err(ConversionError::Timeout)` if timeout is exceeded.
    /// Returns `Err(ConversionError)` for other conversion failures.
    ///
    /// # Timeout Strategy
    ///
    /// The timeout mechanism is cooperative (not preemptive):
    /// - Timeout is checked every 100 DOM nodes during traversal
    /// - Timeout is checked after metadata extraction
    /// - Timeout is checked after output normalization
    /// - No thread spawning or background processing
    ///
    /// # Examples
    ///
    /// ```rust
    /// use nginx_markdown_converter::converter::{MarkdownConverter, ConversionContext};
    /// use nginx_markdown_converter::parser::parse_html;
    /// use std::time::Duration;
    ///
    /// let html = b"<h1>Hello World</h1><p>This is a test.</p>";
    /// let dom = parse_html(html).expect("Parse failed");
    /// let converter = MarkdownConverter::new();
    ///
    /// // Convert with 5 second timeout
    /// let mut ctx = ConversionContext::new(Duration::from_secs(5));
    /// let markdown = converter.convert_with_context(&dom, &mut ctx)
    ///     .expect("Conversion failed");
    /// assert!(markdown.contains("# Hello World"));
    /// ```
    ///
    /// # Requirements
    ///
    /// Validates: FR-10.2, FR-10.7
    pub fn convert_with_context(
        &self,
        dom: &RcDom,
        ctx: &mut ConversionContext,
    ) -> Result<String, ConversionError> {
        // Fast path qualification: check if the document is structurally simple
        // enough for optimized traversal. When the document qualifies, the
        // traversal skips unreachable branches (form controls, embedded content,
        // table/media handling) reducing per-node overhead.
        ctx.is_fast_path =
            fast_path::qualifies(dom) == fast_path::FastPathResult::Qualifies;

        // Pre-allocate output buffer with reasonable capacity
        // Average compression ratio is ~70-85%, so we estimate output size
        let mut output = String::with_capacity(1024);

        // Extract metadata and add YAML front matter if enabled
        if self.options.include_front_matter && self.options.extract_metadata {
            self.maybe_write_front_matter_from_dom(dom, &mut output)?;
            // Check timeout after metadata extraction
            ctx.check_timeout()?;
        }

        // Start traversal from document root
        // Depth 0 represents the document level
        self.traverse_node_with_context(&dom.document, &mut output, 0, ctx)?;

        // Check timeout before output normalization
        ctx.check_timeout()?;

        // Normalize output: use fused normalizer for large documents to avoid
        // a second full-size allocation, otherwise use the standard two-pass path.
        let markdown = if output.len() > LARGE_BODY_THRESHOLD {
            let capacity = large_response::estimate_output_capacity(output.len());
            let mut normalizer = large_response::FusedNormalizer::new(capacity);
            let output = output.replace("\r\n", "\n");
            for line in output.lines() {
                normalizer.push_line(line);
            }
            normalizer.finalize()
        } else {
            self.normalize_output(output)
        };

        // Final timeout check after normalization
        ctx.check_timeout()?;

        Ok(markdown)
    }
}

impl Default for MarkdownConverter {
    /// Construct a converter with default `ConversionOptions`.
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse_html;
    use proptest::prelude::*;

    /// Parse HTML and convert it with default converter settings.
    fn convert_html_for_test(html: &str) -> String {
        let dom = parse_html(html.as_bytes()).expect("Parse failed");
        MarkdownConverter::new()
            .convert(&dom)
            .expect("Conversion failed")
    }

    /// Normalize whitespace for robust text-only expectations.
    fn normalize_expected_text(text: &str) -> String {
        text.split_whitespace().collect::<Vec<_>>().join(" ")
    }

    /// Escape characters that would otherwise be interpreted as HTML markup.
    fn escape_html_text(value: &str) -> String {
        value
            .replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
    }

    /// Encode one character using a deterministic mix of named/decimal/hex entities.
    fn encode_entity_char(ch: char, selector: u8) -> String {
        match ch {
            '&' => match selector % 3 {
                0 => "&amp;".to_string(),
                1 => "&#38;".to_string(),
                _ => "&#x26;".to_string(),
            },
            '<' => match selector % 3 {
                0 => "&lt;".to_string(),
                1 => "&#60;".to_string(),
                _ => "&#x3C;".to_string(),
            },
            '>' => match selector % 3 {
                0 => "&gt;".to_string(),
                1 => "&#62;".to_string(),
                _ => "&#x3E;".to_string(),
            },
            '"' => match selector % 3 {
                0 => "&quot;".to_string(),
                1 => "&#34;".to_string(),
                _ => "&#x22;".to_string(),
            },
            '\'' => match selector % 2 {
                0 => "&#39;".to_string(),
                _ => "&#x27;".to_string(),
            },
            'A' => match selector % 3 {
                0 => "A".to_string(),
                1 => "&#65;".to_string(),
                _ => "&#x41;".to_string(),
            },
            '€' => match selector % 2 {
                0 => "&#8364;".to_string(),
                _ => "&#x20AC;".to_string(),
            },
            '中' => match selector % 2 {
                0 => "&#20013;".to_string(),
                _ => "&#x4E2D;".to_string(),
            },
            _ => ch.to_string(),
        }
    }

    #[test]
    fn test_heading_conversion() {
        let html = b"<h1>Title</h1><h2>Subtitle</h2>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("# Title"));
        assert!(result.contains("## Subtitle"));
    }

    #[test]
    fn test_paragraph_conversion() {
        let html = b"<p>First paragraph.</p><p>Second paragraph.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("First paragraph."));
        assert!(result.contains("Second paragraph."));
    }

    #[test]
    fn test_text_normalization() {
        let html = b"<p>Text   with    multiple    spaces</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Text with multiple spaces"));
        assert!(!result.contains("   "));
    }

    #[test]
    fn test_script_removal() {
        let html = b"<p>Content</p><script>alert('xss')</script><p>More</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Content"));
        assert!(result.contains("More"));
        assert!(!result.contains("alert"));
        assert!(!result.contains("xss"));
    }

    /// Test that style tags and their content are completely removed
    /// Validates: FR-03.3, NFR-03.4
    #[test]
    fn test_style_removal() {
        let html = b"<p>Before</p><style>body { color: red; }</style><p>After</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Before"));
        assert!(result.contains("After"));
        assert!(!result.contains("body"));
        assert!(!result.contains("color"));
        assert!(!result.contains("red"));
        assert!(!result.contains("style"));
    }

    /// Test that noscript tags and their content are completely removed
    /// Validates: FR-03.3, NFR-03.4
    #[test]
    fn test_noscript_removal() {
        let html = b"<p>Content</p><noscript>Please enable JavaScript</noscript><p>More</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Content"));
        assert!(result.contains("More"));
        assert!(!result.contains("noscript"));
        assert!(!result.contains("JavaScript"));
        assert!(!result.contains("enable"));
    }

    /// Test removal of multiple non-content elements in one document
    /// Validates: FR-03.3, NFR-03.4
    #[test]
    fn test_multiple_non_content_removal() {
        let html = b"<h1>Title</h1><script>var x = 1;</script><p>Paragraph</p><style>.class{}</style><noscript>No JS</noscript><p>End</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Content should be present
        assert!(result.contains("# Title"));
        assert!(result.contains("Paragraph"));
        assert!(result.contains("End"));

        // Non-content should be removed
        assert!(!result.contains("var x"));
        assert!(!result.contains("script"));
        assert!(!result.contains(".class"));
        assert!(!result.contains("style"));
        assert!(!result.contains("No JS"));
        assert!(!result.contains("noscript"));
    }

    /// Test that nested non-content elements are removed
    /// Validates: FR-03.3, NFR-03.4
    #[test]
    fn test_nested_non_content_removal() {
        let html = b"<div><p>Before</p><div><script>nested();</script></div><p>After</p></div>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Before"));
        assert!(result.contains("After"));
        assert!(!result.contains("nested"));
        assert!(!result.contains("script"));
    }

    /// Test script with attributes is removed
    /// Validates: FR-03.3, NFR-03.4
    #[test]
    fn test_script_with_attributes_removal() {
        let html = b"<p>Text</p><script type=\"text/javascript\" src=\"file.js\">code();</script><p>More</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Text"));
        assert!(result.contains("More"));
        assert!(!result.contains("javascript"));
        assert!(!result.contains("file.js"));
        assert!(!result.contains("code"));
        assert!(!result.contains("script"));
    }

    /// Test style in head section is removed
    /// Validates: FR-03.3, NFR-03.4
    #[test]
    fn test_style_in_head_removal() {
        let html = b"<html><head><style>h1 { font-size: 2em; }</style></head><body><h1>Title</h1></body></html>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("# Title"));
        assert!(!result.contains("font-size"));
        assert!(!result.contains("2em"));
        assert!(!result.contains("style"));
    }

    /// Test inline script event handlers are in script tags (removed)
    /// Note: Inline event handlers in attributes are a separate concern
    /// Validates: FR-03.3, NFR-03.4
    #[test]
    fn test_inline_script_removal() {
        let html =
            b"<p>Click</p><script>document.addEventListener('click', handler);</script><p>Done</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Click"));
        assert!(result.contains("Done"));
        assert!(!result.contains("addEventListener"));
        assert!(!result.contains("handler"));
        assert!(!result.contains("document"));
    }

    /// Test that content around non-content elements is preserved correctly
    /// Validates: FR-03.3
    #[test]
    fn test_content_preservation_around_non_content() {
        let html = b"<p>First paragraph.</p><script>removed();</script><p>Second paragraph.</p><style>removed{}</style><p>Third paragraph.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // All paragraphs should be present
        assert!(result.contains("First paragraph"));
        assert!(result.contains("Second paragraph"));
        assert!(result.contains("Third paragraph"));

        // Non-content should be gone
        assert!(!result.contains("removed"));
        assert!(!result.contains("script"));
        assert!(!result.contains("style"));

        // Check structure is maintained (paragraphs separated by blank lines)
        let lines: Vec<&str> = result.lines().collect();
        assert!(lines.len() >= 5); // At least 3 paragraphs + 2 blank lines
    }

    /// Test that iframe fallback text is preserved and src is extracted as a link.
    #[test]
    fn test_iframe_fallback_and_src_extraction() {
        let html = br#"<div>
            <p>Before</p>
            <iframe src="https://example.com/embed" title="Demo video">
                <p>Your browser does not support iframes. Visit the content directly.</p>
            </iframe>
            <p>After</p>
        </div>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // src should be extracted as a Markdown link with title as label
        assert!(
            result.contains("[Demo video](https://example.com/embed)"),
            "iframe src should be extracted as a Markdown link with title: {}",
            result
        );
        // Fallback text should be preserved
        assert!(
            result.contains("Your browser does not support iframes"),
            "Fallback text should be preserved: {}",
            result
        );
        // No raw HTML
        assert!(
            !result.contains("<iframe"),
            "iframe tag must not leak into Markdown"
        );
        // Surrounding content preserved
        assert!(result.contains("Before"), "Content before iframe preserved");
        assert!(result.contains("After"), "Content after iframe preserved");
    }

    /// Test that object fallback content is preserved and data URL extracted.
    #[test]
    fn test_object_fallback_and_data_extraction() {
        let html = br#"<div>
            <object data="https://example.com/doc.pdf" title="User Guide">
                <p>Cannot display PDF. Download it here.</p>
            </object>
        </div>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // data attr should be extracted as a Markdown link
        assert!(
            result.contains("[User Guide](https://example.com/doc.pdf)"),
            "object data should be extracted as a Markdown link: {}",
            result
        );
        // Fallback text preserved
        assert!(
            result.contains("Cannot display PDF"),
            "Fallback text should be preserved: {}",
            result
        );
        assert!(!result.contains("<object"), "object tag must not leak");
    }

    /// Test that iframe/object with dangerous URLs have the URL suppressed.
    #[test]
    fn test_embedded_content_dangerous_url_suppressed() {
        let html = br#"<iframe src="javascript:alert(1)"><p>Fallback</p></iframe>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // javascript: URL must not appear
        assert!(
            !result.contains("javascript:"),
            "Dangerous URL must be suppressed"
        );
        // Fallback text still preserved
        assert!(
            result.contains("Fallback"),
            "Fallback text should still be preserved"
        );
    }

    /// Test iframe without src or title — just fallback text.
    #[test]
    fn test_iframe_no_src_fallback_only() {
        let html = br#"<iframe><p>Embedded content unavailable</p></iframe>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("Embedded content unavailable"),
            "Fallback text should be preserved even without src"
        );
        assert!(!result.contains("<iframe"), "No raw HTML");
    }

    /// Test that embed src is extracted as a Markdown link.
    #[test]
    fn test_embed_src_extraction() {
        let html =
            br#"<div><p>Before</p><embed src="https://example.com/doc.pdf"><p>After</p></div>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("[https://example.com/doc.pdf](https://example.com/doc.pdf)"),
            "embed src should be extracted as link: {}",
            result
        );
        assert!(!result.contains("<embed"), "No raw HTML");
        assert!(result.contains("Before"));
        assert!(result.contains("After"));
    }

    /// Test video element: src and poster extracted, fallback text preserved.
    #[test]
    fn test_video_url_extraction() {
        let html = br#"<video src="https://example.com/video.mp4" poster="https://example.com/thumb.jpg" title="Demo video">
            <p>Your browser does not support video.</p>
        </video>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("[Demo video](https://example.com/video.mp4)"),
            "video src should be extracted with title as label: {}",
            result
        );
        assert!(
            result.contains("![](https://example.com/thumb.jpg)"),
            "poster should be extracted as image: {}",
            result
        );
        assert!(
            result.contains("Your browser does not support video"),
            "Fallback text should be preserved: {}",
            result
        );
        assert!(!result.contains("<video"), "No raw HTML");
    }

    /// Test audio element: src extracted, fallback text preserved.
    #[test]
    fn test_audio_url_extraction() {
        let html = br#"<audio src="https://example.com/podcast.mp3" title="Episode 1">
            <p>Your browser does not support audio.</p>
        </audio>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("[Episode 1](https://example.com/podcast.mp3)"),
            "audio src should be extracted with title: {}",
            result
        );
        assert!(
            result.contains("Your browser does not support audio"),
            "Fallback text preserved: {}",
            result
        );
    }

    /// Test source element inside video: src extracted with type as label.
    #[test]
    fn test_source_url_extraction() {
        let html = br#"<video>
            <source src="https://example.com/video.webm" type="video/webm">
            <source src="https://example.com/video.mp4" type="video/mp4">
            <p>Fallback text</p>
        </video>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("[video/webm](https://example.com/video.webm)"),
            "source src with type label: {}",
            result
        );
        assert!(
            result.contains("[video/mp4](https://example.com/video.mp4)"),
            "source src with type label: {}",
            result
        );
        assert!(
            result.contains("Fallback text"),
            "Fallback preserved: {}",
            result
        );
    }

    /// Test track element: src extracted with label text.
    #[test]
    fn test_track_url_extraction() {
        let html = r#"<video src="video.mp4">
            <track src="subs_en.vtt" kind="subtitles" label="English">
            <track src="subs_zh.vtt" kind="subtitles" label="中文">
        </video>"#;
        let dom = parse_html(html.as_bytes()).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("[English](subs_en.vtt)"),
            "track label should be link text: {}",
            result
        );
        assert!(
            result.contains("[中文](subs_zh.vtt)"),
            "track label with CJK: {}",
            result
        );
    }

    /// Test area element: href extracted with alt text.
    #[test]
    fn test_area_link_extraction() {
        let html = br#"<map name="infographic">
            <area href="https://example.com/page1" alt="Section 1" title="First section">
            <area href="https://example.com/page2" alt="Section 2">
            <area href="https://example.com/page3" title="Third section">
        </map>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("[Section 1](https://example.com/page1)"),
            "area alt should be link text: {}",
            result
        );
        assert!(
            result.contains("[Section 2](https://example.com/page2)"),
            "area alt as link text: {}",
            result
        );
        assert!(
            result.contains("[Third section](https://example.com/page3)"),
            "area title as fallback label: {}",
            result
        );
    }

    /// Test that form elements are stripped but their text content is preserved.
    /// AI agents benefit from seeing labels, button text, and option lists.
    /// Validates: I-02 security fix — no raw HTML form tags in output.
    #[test]
    fn test_form_content_extraction() {
        let html = br#"<form action="/search">
            <label>Search query</label>
            <input type="text" placeholder="Enter keywords">
            <select><option>Option A</option><option>Option B</option></select>
            <textarea>Default text</textarea>
            <button>Submit</button>
        </form>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Text content from form elements should be preserved
        assert!(
            result.contains("Search query"),
            "Label text should be preserved"
        );
        assert!(
            result.contains("Enter keywords"),
            "Input placeholder should be extracted"
        );
        assert!(
            result.contains("Option A"),
            "Option text should be preserved"
        );
        assert!(
            result.contains("Option B"),
            "Option text should be preserved"
        );
        assert!(
            result.contains("Default text"),
            "Textarea content should be preserved"
        );
        assert!(result.contains("Submit"), "Button text should be preserved");

        // Raw HTML tags must not appear in output
        assert!(
            !result.contains("<form"),
            "Form tag must not leak into Markdown"
        );
        assert!(
            !result.contains("<input"),
            "Input tag must not leak into Markdown"
        );
        assert!(
            !result.contains("<select"),
            "Select tag must not leak into Markdown"
        );
        assert!(
            !result.contains("<button"),
            "Button tag must not leak into Markdown"
        );
        assert!(
            !result.contains("<label"),
            "Label tag must not leak into Markdown"
        );
        assert!(!result.contains("action="), "Form attributes must not leak");
    }

    /// Test that hidden inputs are suppressed but submit/reset values are kept.
    #[test]
    fn test_input_type_handling() {
        let html = br#"<div>
            <input type="hidden" name="csrf" value="token123">
            <input type="submit" value="Send">
            <input type="reset" value="Clear">
            <input type="text" aria-label="Username field" placeholder="user" value="john">
        </div>"#;
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Hidden input content should not appear
        assert!(
            !result.contains("token123"),
            "Hidden input value must be suppressed"
        );

        // Submit/reset button text should appear
        assert!(result.contains("Send"), "Submit value should be preserved");
        assert!(result.contains("Clear"), "Reset value should be preserved");

        // aria-label takes priority over placeholder and value
        assert!(
            result.contains("Username field"),
            "aria-label should be preferred"
        );
    }

    #[test]
    fn test_nested_structure() {
        let html = b"<div><h1>Title</h1><p>Content</p></div>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("# Title"));
        assert!(result.contains("Content"));
    }

    #[test]
    fn test_all_heading_levels() {
        let html = b"<h1>H1</h1><h2>H2</h2><h3>H3</h3><h4>H4</h4><h5>H5</h5><h6>H6</h6>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("# H1"));
        assert!(result.contains("## H2"));
        assert!(result.contains("### H3"));
        assert!(result.contains("#### H4"));
        assert!(result.contains("##### H5"));
        assert!(result.contains("###### H6"));
    }

    #[test]
    fn test_empty_paragraph() {
        let html = b"<p></p><p>Content</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Content"));
        // Empty paragraphs should not add extra blank lines
    }

    #[test]
    fn test_whitespace_only_paragraph() {
        let html = b"<p>   </p><p>Content</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Content"));
        // Whitespace-only paragraphs should be ignored
    }

    #[test]
    fn test_output_normalization() {
        let html = b"<p>Para1</p><p>Para2</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should have single blank lines between paragraphs
        assert!(!result.contains("\n\n\n"));
        // Should end with single newline
        assert!(result.ends_with('\n'));
        // The last paragraph adds \n\n, but normalize_output ensures single trailing newline
        let lines: Vec<&str> = result.lines().collect();
        assert!(lines.len() >= 2); // At least two paragraphs
    }

    // ============================================================================
    // Deterministic Output Normalization Tests
    // ============================================================================

    /// Test that CRLF line endings are normalized to LF
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_crlf_to_lf() {
        let converter = MarkdownConverter::new();
        let input = "Line 1\r\nLine 2\r\nLine 3\r\n".to_string();
        let result = converter.normalize_output(input);

        // Should not contain any CRLF
        assert!(!result.contains("\r\n"));
        // Should contain LF
        assert!(result.contains("Line 1\n"));
        assert!(result.contains("Line 2\n"));
        assert!(result.contains("Line 3\n"));
    }

    /// Test that consecutive blank lines are collapsed to single blank line
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_consecutive_blank_lines() {
        let converter = MarkdownConverter::new();
        let input = "Para 1\n\n\n\nPara 2\n\n\nPara 3\n".to_string();
        let result = converter.normalize_output(input);

        // Should not contain triple newlines
        assert!(!result.contains("\n\n\n"));
        // Should have single blank lines between paragraphs
        assert!(result.contains("Para 1\n\nPara 2"));
        assert!(result.contains("Para 2\n\nPara 3"));
    }

    /// Test that trailing whitespace is removed from all lines
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_trailing_whitespace() {
        let converter = MarkdownConverter::new();
        let input = "Line 1   \nLine 2\t\t\nLine 3 \n".to_string();
        let result = converter.normalize_output(input);

        // No line should end with spaces or tabs (except the final newline)
        for line in result.lines() {
            assert!(!line.ends_with(' '));
            assert!(!line.ends_with('\t'));
        }
        assert_eq!(result, "Line 1\nLine 2\nLine 3\n");
    }

    /// Test that output ends with exactly one newline
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_single_final_newline() {
        let converter = MarkdownConverter::new();

        // Test with no trailing newline
        let input1 = "Content".to_string();
        let result1 = converter.normalize_output(input1);
        assert!(result1.ends_with('\n'));
        assert!(!result1.ends_with("\n\n"));

        // Test with multiple trailing newlines
        let input2 = "Content\n\n\n".to_string();
        let result2 = converter.normalize_output(input2);
        assert!(result2.ends_with('\n'));
        assert!(!result2.ends_with("\n\n"));

        // Test with single trailing newline (should be preserved)
        let input3 = "Content\n".to_string();
        let result3 = converter.normalize_output(input3);
        assert_eq!(result3, "Content\n");
    }

    /// Test that consecutive spaces are collapsed to single space (outside code blocks)
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_consecutive_spaces() {
        let converter = MarkdownConverter::new();
        let input = "Word1    Word2  Word3\nLine2   has    spaces\n".to_string();
        let result = converter.normalize_output(input);

        // Should collapse consecutive spaces to single space
        assert!(result.contains("Word1 Word2 Word3"));
        assert!(result.contains("Line2 has spaces"));
        assert!(!result.contains("  ")); // No double spaces
    }

    /// Test that whitespace normalization preserves inline code spacing
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_preserves_inline_code_spaces() {
        let converter = MarkdownConverter::new();
        let input = "Text with `  code  ` and more  text\n".to_string();
        let result = converter.normalize_output(input);

        // Should preserve spaces inside inline code
        assert!(result.contains("`  code  `"));
        // Should normalize spaces outside inline code
        assert!(result.contains("and more text"));
    }

    /// Test that whitespace normalization preserves code block formatting
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_preserves_code_blocks() {
        let converter = MarkdownConverter::new();
        let input = "```rust\nfn  test()  {\n    let  x  =  5;\n}\n```\n".to_string();
        let result = converter.normalize_output(input);

        // Code block content should preserve spacing
        assert!(result.contains("fn  test()  {"));
        assert!(result.contains("let  x  =  5;"));
    }

    /// Test that list indentation is preserved (2 spaces for nested lists)
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_preserves_list_indentation() {
        let converter = MarkdownConverter::new();
        let input = "- Item 1\n  - Nested 1\n  - Nested 2\n- Item 2\n".to_string();
        let result = converter.normalize_output(input);

        // Should preserve leading spaces for list indentation
        assert!(result.contains("  - Nested 1"));
        assert!(result.contains("  - Nested 2"));
    }

    /// Test deterministic output: identical HTML produces identical Markdown
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_deterministic_output_identical_html() {
        let html = b"<h1>Title</h1><p>Paragraph with <strong>bold</strong> text.</p><ul><li>Item 1</li><li>Item 2</li></ul>";

        // Convert the same HTML twice
        let dom1 = parse_html(html).expect("Parse failed");
        let converter1 = MarkdownConverter::new();
        let result1 = converter1.convert(&dom1).expect("Conversion failed");

        let dom2 = parse_html(html).expect("Parse failed");
        let converter2 = MarkdownConverter::new();
        let result2 = converter2.convert(&dom2).expect("Conversion failed");

        // Results should be byte-for-byte identical
        assert_eq!(result1, result2);
    }

    /// Test deterministic output with various HTML inputs
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_deterministic_output_complex_html() {
        let html = b"<html><body><h1>Title</h1><p>Text with <a href='url'>link</a> and <img src='img.png' alt='image'/>.</p><pre><code>code block</code></pre></body></html>";

        // Convert multiple times
        let mut results = Vec::new();
        for _ in 0..5 {
            let dom = parse_html(html).expect("Parse failed");
            let converter = MarkdownConverter::new();
            let result = converter.convert(&dom).expect("Conversion failed");
            results.push(result);
        }

        // All results should be identical
        for i in 1..results.len() {
            assert_eq!(
                results[0], results[i],
                "Conversion {} differs from first",
                i
            );
        }
    }

    /// Test that Markdown escaping is applied consistently
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_consistent_markdown_escaping() {
        // Test with special Markdown characters in text
        let html = b"<p>Text with * asterisk and _ underscore and [brackets]</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should preserve special characters (they're in plain text context)
        assert!(result.contains("*"));
        assert!(result.contains("_"));
        assert!(result.contains("["));
        assert!(result.contains("]"));
    }

    /// Test normalization with mixed line endings
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_mixed_line_endings() {
        let converter = MarkdownConverter::new();
        let input = "Line 1\r\nLine 2\nLine 3\r\nLine 4\n".to_string();
        let result = converter.normalize_output(input);

        // All line endings should be LF
        assert!(!result.contains("\r"));
        assert_eq!(result, "Line 1\nLine 2\nLine 3\nLine 4\n");
    }

    /// Test normalization with empty input
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_empty_input() {
        let converter = MarkdownConverter::new();
        let input = "".to_string();
        let result = converter.normalize_output(input);

        // Empty input should produce single newline
        assert_eq!(result, "\n");
    }

    /// Test normalization with only whitespace
    /// Validates: Design - Deterministic Markdown Output Constraints
    #[test]
    fn test_normalize_whitespace_only() {
        let converter = MarkdownConverter::new();
        let input = "   \n\t\n  \n".to_string();
        let result = converter.normalize_output(input);

        // Should collapse to single newline
        assert_eq!(result, "\n");
    }

    // ============================================================================
    // Property-Based Tests
    // ============================================================================

    // Property 5: Structural Preservation
    // **Validates: Requirements FR-03.2**
    //
    // This property test verifies that the Markdown converter preserves semantic
    // structure from HTML. When HTML contains semantic elements (headings, paragraphs,
    // links, images, lists, code blocks, tables), the converted Markdown output
    // should contain representations of all these elements.
    //
    // Test Strategy:
    // - Generate HTML with various semantic elements
    // - Convert to Markdown
    // - Verify that Markdown contains representations of each element type
    // - Test that structure is preserved (not just content)
    //
    // Note: This test focuses on elements currently implemented (headings, paragraphs).
    // As more element handlers are added (links, images, lists, code, tables),
    // this test should be expanded to cover those elements.
    proptest! {
        #[test]
        fn prop_structural_preservation_headings(
            h1_text in "[a-zA-Z0-9]{1,50}",
            h2_text in "[a-zA-Z0-9]{1,50}",
            h3_text in "[a-zA-Z0-9]{1,50}",
        ) {
            // Generate HTML with multiple heading levels
            let html = format!(
                "<html><body><h1>{}</h1><h2>{}</h2><h3>{}</h3></body></html>",
                h1_text, h2_text, h3_text
            );

            // Convert to Markdown
            let dom = parse_html(html.as_bytes()).expect("Parse failed");
            let converter = MarkdownConverter::new();
            let markdown = converter.convert(&dom).expect("Conversion failed");

            // Property: Markdown should contain heading markers for each level
            // Note: Text is normalized (whitespace collapsed), so we normalize expected text too
            let h1_normalized = h1_text.split_whitespace().collect::<Vec<_>>().join(" ");
            let h2_normalized = h2_text.split_whitespace().collect::<Vec<_>>().join(" ");
            let h3_normalized = h3_text.split_whitespace().collect::<Vec<_>>().join(" ");

            prop_assert!(
                markdown.contains(&format!("# {}", h1_normalized)),
                "Markdown should contain h1 heading: expected '# {}', got:\n{}",
                h1_normalized, markdown
            );
            prop_assert!(
                markdown.contains(&format!("## {}", h2_normalized)),
                "Markdown should contain h2 heading: expected '## {}', got:\n{}",
                h2_normalized, markdown
            );
            prop_assert!(
                markdown.contains(&format!("### {}", h3_normalized)),
                "Markdown should contain h3 heading: expected '### {}', got:\n{}",
                h3_normalized, markdown
            );
        }

        #[test]
        fn prop_structural_preservation_paragraphs(
            para1 in "[a-zA-Z0-9]{1,50}",
            para2 in "[a-zA-Z0-9]{1,50}",
            para3 in "[a-zA-Z0-9]{1,50}",
        ) {
            // Generate HTML with multiple paragraphs
            let html = format!(
                "<html><body><p>{}</p><p>{}</p><p>{}</p></body></html>",
                para1, para2, para3
            );

            // Convert to Markdown
            let dom = parse_html(html.as_bytes()).expect("Parse failed");
            let converter = MarkdownConverter::new();
            let markdown = converter.convert(&dom).expect("Conversion failed");

            // Property: Markdown should contain all paragraph content
            // Text is normalized (whitespace collapsed)
            let para1_normalized = para1.split_whitespace().collect::<Vec<_>>().join(" ");
            let para2_normalized = para2.split_whitespace().collect::<Vec<_>>().join(" ");
            let para3_normalized = para3.split_whitespace().collect::<Vec<_>>().join(" ");

            prop_assert!(
                markdown.contains(&para1_normalized),
                "Markdown should contain first paragraph: expected '{}', got:\n{}",
                para1_normalized, markdown
            );
            prop_assert!(
                markdown.contains(&para2_normalized),
                "Markdown should contain second paragraph: expected '{}', got:\n{}",
                para2_normalized, markdown
            );
            prop_assert!(
                markdown.contains(&para3_normalized),
                "Markdown should contain third paragraph: expected '{}', got:\n{}",
                para3_normalized, markdown
            );
        }

        #[test]
        fn prop_structural_preservation_mixed_elements(
            heading in "[a-zA-Z0-9]{1,30}",
            para1 in "[a-zA-Z0-9]{1,40}",
            para2 in "[a-zA-Z0-9]{1,40}",
            heading_level in 1usize..=6usize,
        ) {
            // Generate HTML with mixed semantic elements
            let heading_tag = format!("h{}", heading_level);
            let html = format!(
                "<html><body><{0}>{1}</{0}><p>{2}</p><p>{3}</p></body></html>",
                heading_tag, heading, para1, para2
            );

            // Convert to Markdown
            let dom = parse_html(html.as_bytes()).expect("Parse failed");
            let converter = MarkdownConverter::new();
            let markdown = converter.convert(&dom).expect("Conversion failed");

            // Property: Markdown should preserve structure
            // 1. Heading should be present with correct level
            let heading_marker = "#".repeat(heading_level);
            let heading_normalized = heading.split_whitespace().collect::<Vec<_>>().join(" ");
            prop_assert!(
                markdown.contains(&format!("{} {}", heading_marker, heading_normalized)),
                "Markdown should contain heading: expected '{} {}', got:\n{}",
                heading_marker, heading_normalized, markdown
            );

            // 2. Paragraphs should be present
            let para1_normalized = para1.split_whitespace().collect::<Vec<_>>().join(" ");
            let para2_normalized = para2.split_whitespace().collect::<Vec<_>>().join(" ");
            prop_assert!(
                markdown.contains(&para1_normalized),
                "Markdown should contain first paragraph"
            );
            prop_assert!(
                markdown.contains(&para2_normalized),
                "Markdown should contain second paragraph"
            );

            // 3. Structure should be preserved (heading before paragraphs)
            // Only check order if both heading and first paragraph have content
            if !heading_normalized.is_empty() && !para1_normalized.is_empty() {
                let heading_pos = markdown.find(&format!("{} {}", heading_marker, heading_normalized));
                let para1_pos = markdown.find(&para1_normalized);
                if let (Some(h_pos), Some(p_pos)) = (heading_pos, para1_pos) {
                    prop_assert!(
                        h_pos < p_pos,
                        "Heading should appear before paragraph in output"
                    );
                }
            }
        }

        #[test]
        fn prop_structural_preservation_nested_structure(
            heading in "[a-zA-Z0-9]{1,30}",
            content in "[a-zA-Z0-9]{1,40}",
            nesting_depth in 1usize..5usize,
        ) {
            // Generate HTML with nested div structure
            let mut html = String::from("<html><body>");
            for _ in 0..nesting_depth {
                html.push_str("<div>");
            }
            html.push_str(&format!("<h2>{}</h2><p>{}</p>", heading, content));
            for _ in 0..nesting_depth {
                html.push_str("</div>");
            }
            html.push_str("</body></html>");

            // Convert to Markdown
            let dom = parse_html(html.as_bytes()).expect("Parse failed");
            let converter = MarkdownConverter::new();
            let markdown = converter.convert(&dom).expect("Conversion failed");

            // Property: Semantic structure should be preserved regardless of nesting
            let heading_normalized = heading.split_whitespace().collect::<Vec<_>>().join(" ");
            let content_normalized = content.split_whitespace().collect::<Vec<_>>().join(" ");

            prop_assert!(
                markdown.contains(&format!("## {}", heading_normalized)),
                "Markdown should contain heading despite nesting"
            );
            prop_assert!(
                markdown.contains(&content_normalized),
                "Markdown should contain content despite nesting"
            );
        }

        #[test]
        fn prop_structural_preservation_all_heading_levels(
            h1 in "[a-zA-Z]{1,20}",
            h2 in "[a-zA-Z]{1,20}",
            h3 in "[a-zA-Z]{1,20}",
            h4 in "[a-zA-Z]{1,20}",
            h5 in "[a-zA-Z]{1,20}",
            h6 in "[a-zA-Z]{1,20}",
        ) {
            // Generate HTML with all six heading levels
            let html = format!(
                "<html><body><h1>{}</h1><h2>{}</h2><h3>{}</h3><h4>{}</h4><h5>{}</h5><h6>{}</h6></body></html>",
                h1, h2, h3, h4, h5, h6
            );

            // Convert to Markdown
            let dom = parse_html(html.as_bytes()).expect("Parse failed");
            let converter = MarkdownConverter::new();
            let markdown = converter.convert(&dom).expect("Conversion failed");

            // Property: All heading levels should be preserved with correct markers
            prop_assert!(markdown.contains(&format!("# {}", h1)), "h1 should be preserved");
            prop_assert!(markdown.contains(&format!("## {}", h2)), "h2 should be preserved");
            prop_assert!(markdown.contains(&format!("### {}", h3)), "h3 should be preserved");
            prop_assert!(markdown.contains(&format!("#### {}", h4)), "h4 should be preserved");
            prop_assert!(markdown.contains(&format!("##### {}", h5)), "h5 should be preserved");
            prop_assert!(markdown.contains(&format!("###### {}", h6)), "h6 should be preserved");
        }

        #[test]
        fn prop_structural_preservation_empty_elements(
            heading in "[a-zA-Z0-9]{1,30}",
            content in "[a-zA-Z0-9]{1,30}",
        ) {
            // Generate HTML with some empty elements
            let html = format!(
                "<html><body><h1>{}</h1><p></p><p>{}</p><div></div></body></html>",
                heading, content
            );

            // Convert to Markdown
            let dom = parse_html(html.as_bytes()).expect("Parse failed");
            let converter = MarkdownConverter::new();
            let markdown = converter.convert(&dom).expect("Conversion failed");

            // Property: Non-empty elements should be preserved, empty ones may be omitted
            let heading_normalized = heading.split_whitespace().collect::<Vec<_>>().join(" ");
            let content_normalized = content.split_whitespace().collect::<Vec<_>>().join(" ");

            prop_assert!(
                markdown.contains(&format!("# {}", heading_normalized)),
                "Non-empty heading should be preserved"
            );
            prop_assert!(
                markdown.contains(&content_normalized),
                "Non-empty paragraph should be preserved"
            );
        }
    }

    // Property 6: Non-Content Removal
    // Validates: FR-03.3
    //
    // Ensures script/style/noscript payloads do not leak into Markdown output while
    // surrounding visible content remains present.
    proptest! {
        #[test]
        fn prop_non_content_elements_are_removed(
            before in "[a-m0-9 ]{1,24}",
            after in "[a-m0-9 ]{1,24}",
            script_id in "[A-Z0-9]{4,12}",
            style_id in "[A-Z0-9]{4,12}",
            noscript_id in "[A-Z0-9]{4,12}",
        ) {
            let script_sentinel = format!("SCRIPT_SENTINEL_{}", script_id);
            let style_sentinel = format!("STYLE_SENTINEL_{}", style_id);
            let noscript_sentinel = format!("NOSCRIPT_SENTINEL_{}", noscript_id);

            let html = format!(
                concat!(
                    "<html><head><style>body::before{{content:'{style}'}}</style></head><body>",
                    "<p>{before}</p>",
                    "<script>console.log('{script}');</script>",
                    "<noscript>{noscript}</noscript>",
                    "<p>{after}</p>",
                    "</body></html>"
                ),
                style = style_sentinel,
                before = escape_html_text(&before),
                script = script_sentinel,
                noscript = noscript_sentinel,
                after = escape_html_text(&after),
            );

            let markdown = convert_html_for_test(&html);

            prop_assert!(
                markdown.contains(&normalize_expected_text(&before)),
                "Visible content before hidden elements should be preserved. Markdown:\n{}",
                markdown
            );
            prop_assert!(
                markdown.contains(&normalize_expected_text(&after)),
                "Visible content after hidden elements should be preserved. Markdown:\n{}",
                markdown
            );
            prop_assert!(!markdown.contains(&script_sentinel), "Script content leaked into Markdown");
            prop_assert!(!markdown.contains(&style_sentinel), "Style content leaked into Markdown");
            prop_assert!(!markdown.contains(&noscript_sentinel), "Noscript content leaked into Markdown");
        }
    }

    // Property 7: HTML Entity Decoding
    // Validates: FR-03.4
    proptest! {
        #[test]
        fn prop_html_entities_decode_to_expected_text(
            symbols in prop::collection::vec((0usize..8usize, any::<u8>()), 1..40),
        ) {
            let alphabet = ['&', '<', '>', '"', '\'', 'A', '€', '中'];

            let mut encoded = String::new();
            let mut expected = String::new();

            for (idx, selector) in symbols {
                let ch = alphabet[idx];
                encoded.push_str(&encode_entity_char(ch, selector));
                expected.push(ch);
            }

            let html = format!("<p>{}</p>", encoded);
            let markdown = convert_html_for_test(&html);

            prop_assert!(
                markdown.contains(&expected),
                "Decoded Markdown should contain expected text.\nExpected: {:?}\nActual: {:?}",
                expected,
                markdown
            );
        }
    }

    // Property 8: Unicode Preservation
    // Validates: FR-03.5, FR-05.4
    proptest! {
        #[test]
        fn prop_unicode_text_is_preserved_in_markdown(
            chars in prop::collection::vec(
                prop::sample::select(vec!['中', '文', '🙂', '🚀', 'é', 'ß', 'Ω', 'Ж', 'ا', 'ह', '한', '🌍', 'A', 'z', '0']),
                1..48
            ),
        ) {
            let text: String = chars.into_iter().collect();
            let html = format!("<p>{}</p>", text);
            let markdown = convert_html_for_test(&html);

            prop_assert!(
                markdown.contains(&text),
                "Unicode text should be preserved.\nInput: {:?}\nMarkdown: {:?}",
                text,
                markdown
            );
        }
    }

    // Property: Deterministic Output Consistency
    // Validates: Deterministic output normalization / stable ETags
    proptest! {
        #[test]
        fn prop_deterministic_output_identical_html_is_byte_identical(
            heading in "[A-Za-z0-9 ]{1,24}",
            paragraph in "[A-Za-z0-9 ]{1,40}",
            link_text in "[A-Za-z0-9 ]{1,20}",
            path in "[a-z0-9/-]{1,20}",
            item1 in "[A-Za-z0-9 ]{1,18}",
            item2 in "[A-Za-z0-9 ]{1,18}",
        ) {
            let html = format!(
                concat!(
                    "<html><body>",
                    "<h2>{heading}</h2>",
                    "<p>{paragraph} <a href=\"/{path}\">{link_text}</a></p>",
                    "<ul><li>{item1}</li><li>{item2}</li></ul>",
                    "</body></html>"
                ),
                heading = escape_html_text(&heading),
                paragraph = escape_html_text(&paragraph),
                path = path,
                link_text = escape_html_text(&link_text),
                item1 = escape_html_text(&item1),
                item2 = escape_html_text(&item2),
            );

            let markdown_a = convert_html_for_test(&html);
            let markdown_b = convert_html_for_test(&html);

            prop_assert_eq!(&markdown_a, &markdown_b, "Identical HTML must produce identical Markdown");
            prop_assert!(!markdown_a.contains('\r'), "Normalized Markdown should use LF line endings only");
            prop_assert!(markdown_a.ends_with('\n'), "Normalized Markdown should end with a single trailing newline");
        }
    }

    // Tests for link handling
    #[test]
    fn test_link_conversion() {
        let html = b"<p>Visit <a href=\"https://example.com\">Example</a> for more.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("[Example](https://example.com)"));
        assert!(result.contains("Visit"));
        assert!(result.contains("for more."));
    }

    #[test]
    fn test_link_without_href() {
        let html = b"<p>This is <a>not a link</a> text.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("not a link"));
        assert!(!result.contains("["));
        assert!(!result.contains("]"));
    }

    #[test]
    fn test_link_with_empty_text() {
        let html = b"<p>Link: <a href=\"https://example.com\"></a></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Empty link text should not produce a link
        assert!(!result.contains("[](https://example.com)"));
    }

    #[test]
    fn test_multiple_links() {
        let html = b"<p><a href=\"/page1\">Page 1</a> and <a href=\"/page2\">Page 2</a></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("[Page 1](/page1)"));
        assert!(result.contains("[Page 2](/page2)"));
        assert!(result.contains("and"));
    }

    // Tests for image handling
    #[test]
    fn test_image_conversion() {
        let html = b"<p>Image: <img src=\"image.png\" alt=\"Description\"></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("![Description](image.png)"));
        assert!(result.contains("Image:"));
    }

    #[test]
    fn test_image_without_alt() {
        let html = b"<p><img src=\"photo.jpg\"></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("![](photo.jpg)"));
    }

    #[test]
    fn test_image_without_src() {
        let html = b"<p>Text <img alt=\"No source\"> more text</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Image without src: alt text is preserved for AI agents
        assert!(
            !result.contains("!["),
            "No Markdown image syntax without URL"
        );
        assert!(result.contains("No source"), "Alt text should be preserved");
        assert!(result.contains("Text"));
        assert!(result.contains("more text"));
    }

    #[test]
    fn test_multiple_images() {
        let html = b"<p><img src=\"a.png\" alt=\"A\"> <img src=\"b.png\" alt=\"B\"></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("![A](a.png)"));
        assert!(result.contains("![B](b.png)"));
    }

    #[test]
    fn test_image_with_title() {
        let html =
            b"<img src=\"photo.jpg\" alt=\"Sunset\" title=\"A beautiful sunset over the ocean\">";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("![Sunset](photo.jpg \"A beautiful sunset over the ocean\")"),
            "Title should be included in Markdown image syntax: {}",
            result
        );
    }

    #[test]
    fn test_image_with_quoted_title() {
        let html = b"<img src=\"photo.jpg\" alt=\"Sunset\" title='A \"quoted\" title'>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("![Sunset](photo.jpg \"A \\\"quoted\\\" title\")"),
            "Quotes inside image titles should be escaped: {}",
            result
        );
    }

    #[test]
    fn test_image_dangerous_url_preserves_alt() {
        let html = b"<p><img src=\"javascript:alert(1)\" alt=\"Important diagram\"></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            !result.contains("javascript:"),
            "Dangerous URL must be suppressed"
        );
        assert!(
            result.contains("Important diagram"),
            "Alt text should be preserved even when URL is blocked: {}",
            result
        );
    }

    // Tests for unordered list handling
    #[test]
    fn test_unordered_list_conversion() {
        let html = b"<ul><li>Item 1</li><li>Item 2</li><li>Item 3</li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- Item 1"));
        assert!(result.contains("- Item 2"));
        assert!(result.contains("- Item 3"));
    }

    #[test]
    fn test_top_level_list_inside_container_keeps_top_level_indentation() {
        let html = b"<div><ul><li>Item 1</li><li>Item 2</li></ul></div>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.starts_with("- Item 1\n- Item 2"));
        assert!(!result.contains("  - Item 1"));
    }

    #[test]
    fn test_top_level_ordered_list_inside_container_keeps_top_level_indentation() {
        let html = b"<section><ol><li>First</li><li>Second</li></ol></section>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.starts_with("1. First\n1. Second"));
        assert!(!result.contains("  1. First"));
    }

    #[test]
    fn test_ordered_list_conversion() {
        let html = b"<ol><li>First</li><li>Second</li><li>Third</li></ol>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("1. First"));
        assert!(result.contains("1. Second"));
        assert!(result.contains("1. Third"));
    }

    #[test]
    fn test_nested_unordered_list() {
        let html =
            b"<ul><li>Item 1<ul><li>Nested 1</li><li>Nested 2</li></ul></li><li>Item 2</li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- Item 1"));
        assert!(result.contains("  - Nested 1"));
        assert!(result.contains("  - Nested 2"));
        assert!(result.contains("- Item 2"));
    }

    #[test]
    fn test_nested_ordered_list() {
        let html = b"<ol><li>First<ol><li>Sub 1</li><li>Sub 2</li></ol></li><li>Second</li></ol>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("1. First"));
        assert!(result.contains("  1. Sub 1"));
        assert!(result.contains("  1. Sub 2"));
        assert!(result.contains("1. Second"));
    }

    #[test]
    fn test_mixed_nested_lists() {
        let html = b"<ul><li>Unordered<ol><li>Ordered nested</li></ol></li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- Unordered"));
        assert!(result.contains("  1. Ordered nested"));
    }

    #[test]
    fn test_list_with_empty_items() {
        let html = b"<ul><li>Item 1</li><li></li><li>Item 3</li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- Item 1"));
        assert!(result.contains("- Item 3"));
        // Empty list items should still have markers
        let lines: Vec<&str> = result.lines().collect();
        let dash_count = lines
            .iter()
            .filter(|line| line.trim().starts_with('-'))
            .count();
        assert_eq!(
            dash_count, 3,
            "Should have 3 list items including empty one"
        );
    }

    #[test]
    fn test_deeply_nested_list() {
        let html = b"<ul><li>L1<ul><li>L2<ul><li>L3</li></ul></li></ul></li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- L1"));
        assert!(result.contains("  - L2"));
        assert!(result.contains("    - L3"));
    }

    // Tests for combined elements
    #[test]
    fn test_link_in_list() {
        let html = b"<ul><li><a href=\"/page\">Link</a></li><li>Plain text</li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- [Link](/page)"));
        assert!(result.contains("- Plain text"));
    }

    #[test]
    fn test_image_in_list() {
        let html = b"<ul><li><img src=\"icon.png\" alt=\"Icon\"> Item</li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- ![Icon](icon.png) Item"));
    }

    #[test]
    fn test_list_in_paragraph_context() {
        let html = b"<p>Before list</p><ul><li>Item</li></ul><p>After list</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Before list"));
        assert!(result.contains("- Item"));
        assert!(result.contains("After list"));

        // Check for proper blank line separation
        let lines: Vec<&str> = result.lines().collect();
        assert!(lines.len() >= 5, "Should have proper line separation");
    }

    #[test]
    fn test_complex_document_structure() {
        let html = b"<h1>Title</h1><p>Intro with <a href=\"/link\">link</a>.</p><ul><li>Item 1</li><li>Item 2</li></ul><p><img src=\"img.png\" alt=\"Image\"></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("# Title"));
        assert!(result.contains("[link](/link)"));
        assert!(result.contains("- Item 1"));
        assert!(result.contains("- Item 2"));
        assert!(result.contains("![Image](img.png)"));
    }

    // Tests for code block handling
    #[test]
    fn test_code_block_basic() {
        let html = b"<pre><code>function hello() {\n  return 'world';\n}</code></pre>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("```"));
        assert!(result.contains("function hello() {"));
        assert!(result.contains("  return 'world';"));
        assert!(result.contains("}"));
    }

    #[test]
    fn test_code_block_with_language() {
        let html =
            b"<pre><code class=\"language-python\">def hello():\n    return 'world'</code></pre>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("```python"));
        assert!(result.contains("def hello():"));
        assert!(result.contains("    return 'world'"));
    }

    #[test]
    fn test_code_block_with_lang_prefix() {
        let html = b"<pre><code class=\"lang-javascript\">const x = 42;</code></pre>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("```javascript"));
        assert!(result.contains("const x = 42;"));
    }

    #[test]
    fn test_code_block_preserves_whitespace() {
        let html = b"<pre><code>  indented\n    more indented\n  back</code></pre>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Whitespace must be preserved exactly
        assert!(result.contains("  indented"));
        assert!(result.contains("    more indented"));
        assert!(result.contains("  back"));
    }

    #[test]
    fn test_code_block_preserves_empty_lines() {
        let html = b"<pre><code>line1\n\nline3</code></pre>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Empty lines in code must be preserved
        let lines: Vec<&str> = result.lines().collect();
        let code_start = lines.iter().position(|&l| l == "```").unwrap();
        let code_end = lines.iter().rposition(|&l| l == "```").unwrap();
        let code_lines = &lines[code_start + 1..code_end];

        assert_eq!(code_lines.len(), 3);
        assert_eq!(code_lines[0], "line1");
        assert_eq!(code_lines[1], "");
        assert_eq!(code_lines[2], "line3");
    }

    #[test]
    fn test_code_block_without_code_tag() {
        let html = b"<pre>plain text in pre</pre>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("```"));
        assert!(result.contains("plain text in pre"));
    }

    #[test]
    fn test_inline_code_basic() {
        let html = b"<p>Use the <code>print()</code> function.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("`print()`"));
        assert!(result.contains("Use the"));
        assert!(result.contains("function."));
    }

    #[test]
    fn test_inline_code_preserves_content() {
        let html = b"<p>The variable <code>  x  </code> has spaces.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Inline code should preserve spaces
        assert!(result.contains("`  x  `"));
    }

    #[test]
    fn test_multiple_inline_code() {
        let html = b"<p>Compare <code>foo</code> and <code>bar</code>.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("`foo`"));
        assert!(result.contains("`bar`"));
        assert!(result.contains("Compare"));
        assert!(result.contains("and"));
    }

    #[test]
    fn test_code_in_heading() {
        let html = b"<h2>Using <code>async</code> functions</h2>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        eprintln!("Result: {:?}", result);
        assert!(result.contains("## Using"));
        assert!(result.contains("`async`"));
        assert!(result.contains("functions"));
    }

    #[test]
    fn test_code_in_list() {
        let html =
            b"<ul><li>Use <code>git commit</code></li><li>Then <code>git push</code></li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- Use `git commit`"));
        assert!(result.contains("- Then `git push`"));
    }

    #[test]
    fn test_mixed_code_and_text() {
        let html = b"<p>Before <code>code1</code> middle <code>code2</code> after</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("Before `code1` middle `code2` after"));
    }

    #[test]
    fn test_code_block_with_special_characters() {
        let html = b"<pre><code>if (x < 5 && y > 3) {\n  return true;\n}</code></pre>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Special characters should be preserved in code blocks
        assert!(result.contains("if (x < 5 && y > 3) {"));
        assert!(result.contains("  return true;"));
    }

    #[test]
    fn test_inline_code_with_special_characters() {
        let html = b"<p>Use <code>x < 5 && y > 3</code> for comparison.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Special characters should be preserved in inline code
        assert!(result.contains("`x < 5 && y > 3`"));
    }

    #[test]
    fn test_code_block_blank_line_separation() {
        let html =
            b"<p>Paragraph before</p><pre><code>code here</code></pre><p>Paragraph after</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Code blocks should be separated by blank lines
        assert!(result.contains("Paragraph before\n\n```"));
        assert!(result.contains("```\n\nParagraph after"));
    }

    #[test]
    fn test_empty_code_block() {
        let html = b"<pre><code></code></pre>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Empty code block should still render
        assert!(result.contains("```"));
    }

    #[test]
    fn test_empty_inline_code() {
        let html = b"<p>Text <code></code> more text</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Empty inline code should render as empty backticks
        assert!(result.contains("``"));
    }

    // Tests for bold formatting
    #[test]
    fn test_bold_with_strong() {
        let html = b"<p>This is <strong>bold text</strong> here.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("**bold text**"));
        assert!(result.contains("This is"));
        assert!(result.contains("here."));
    }

    #[test]
    fn test_bold_with_b() {
        let html = b"<p>This is <b>bold text</b> here.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("**bold text**"));
    }

    #[test]
    fn test_multiple_bold() {
        let html = b"<p><strong>First</strong> and <b>second</b> bold.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("**First**"));
        assert!(result.contains("**second**"));
        assert!(result.contains("and"));
    }

    #[test]
    fn test_bold_in_heading() {
        let html = b"<h2>Title with <strong>bold</strong> word</h2>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("## Title with **bold** word"));
    }

    #[test]
    fn test_bold_in_list() {
        let html = b"<ul><li>Item with <strong>bold</strong></li><li>Plain item</li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- Item with **bold**"));
        assert!(result.contains("- Plain item"));
    }

    #[test]
    fn test_empty_bold() {
        let html = b"<p>Text <strong></strong> more text</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Empty bold should render as empty markers
        assert!(result.contains("****"));
    }

    // Tests for italic formatting
    #[test]
    fn test_italic_with_em() {
        let html = b"<p>This is <em>italic text</em> here.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("*italic text*"));
        assert!(result.contains("This is"));
        assert!(result.contains("here."));
    }

    #[test]
    fn test_italic_with_i() {
        let html = b"<p>This is <i>italic text</i> here.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("*italic text*"));
    }

    #[test]
    fn test_multiple_italic() {
        let html = b"<p><em>First</em> and <i>second</i> italic.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("*First*"));
        assert!(result.contains("*second*"));
        assert!(result.contains("and"));
    }

    #[test]
    fn test_italic_in_heading() {
        let html = b"<h2>Title with <em>italic</em> word</h2>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("## Title with *italic* word"));
    }

    #[test]
    fn test_italic_in_list() {
        let html = b"<ul><li>Item with <em>italic</em></li><li>Plain item</li></ul>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("- Item with *italic*"));
        assert!(result.contains("- Plain item"));
    }

    #[test]
    fn test_empty_italic() {
        let html = b"<p>Text <em></em> more text</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Empty italic should render as empty markers
        assert!(result.contains("**"));
    }

    // Tests for nested formatting
    #[test]
    fn test_bold_inside_italic() {
        let html = b"<p><em>italic with <strong>bold</strong> inside</em></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("*italic with **bold** inside*"));
    }

    #[test]
    fn test_italic_inside_bold() {
        let html = b"<p><strong>bold with <em>italic</em> inside</strong></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("**bold with *italic* inside**"));
    }

    #[test]
    fn test_bold_and_italic_same_level() {
        let html = b"<p>Text with <strong>bold</strong> and <em>italic</em> formatting.</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("**bold**"));
        assert!(result.contains("*italic*"));
        assert!(result.contains("and"));
    }

    #[test]
    fn test_bold_italic_combination() {
        let html = b"<p><strong><em>bold and italic</em></strong></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should produce ***bold and italic***
        assert!(result.contains("***bold and italic***"));
    }

    #[test]
    fn test_italic_bold_combination() {
        let html = b"<p><em><strong>italic and bold</strong></em></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should produce *italic and bold* (order matters)
        assert!(result.contains("***italic and bold***"));
    }

    #[test]
    fn test_formatting_with_code() {
        let html = b"<p><strong>Bold with <code>code</code> inside</strong></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("**Bold with `code` inside**"));
    }

    #[test]
    fn test_formatting_in_link() {
        let html = b"<p><a href=\"/page\"><strong>Bold link</strong></a></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Link text extraction extracts plain text (formatting is lost in link text)
        // This is expected behavior - Markdown links contain plain text
        assert!(result.contains("[Bold link](/page)"));
    }

    #[test]
    fn test_complex_nested_formatting() {
        let html = b"<p>Normal <strong>bold <em>bold-italic</em> bold</strong> normal</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("**bold *bold-italic* bold**"));
        assert!(result.contains("Normal"));
        assert!(result.contains("normal"));
    }

    #[test]
    fn test_deeply_nested_formatting() {
        let html = b"<p><strong><em><strong>triple nested</strong></em></strong></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should handle deep nesting correctly
        assert!(result.contains("***"));
        assert!(result.contains("triple nested"));
    }

    #[test]
    fn test_formatting_with_whitespace() {
        let html = b"<p>Text <strong> bold with spaces </strong> more text</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Whitespace handling: leading/trailing spaces in text nodes are preserved
        // This results in spaces around the bold markers
        assert!(result.contains("** bold with spaces **"));
    }

    #[test]
    fn test_adjacent_formatting() {
        let html = b"<p><strong>bold</strong><em>italic</em></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(result.contains("**bold**"));
        assert!(result.contains("*italic*"));
    }

    #[test]
    fn test_formatting_across_multiple_lines() {
        let html = b"<p><strong>This is\nbold text\nacross lines</strong></p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Newlines should be normalized to spaces
        assert!(result.contains("**This is bold text across lines**"));
    }

    // Comprehensive formatting demonstration test
    #[test]
    fn test_comprehensive_formatting_demo() {
        let html = br#"
<h1>Text Formatting Examples</h1>

<h2>Bold Text</h2>
<p>This paragraph has <strong>bold text</strong> and <b>more bold</b>.</p>

<h2>Italic Text</h2>
<p>This paragraph has <em>italic text</em> and <i>more italic</i>.</p>

<h2>Combined Formatting</h2>
<p>You can have <strong>bold</strong> and <em>italic</em> in the same paragraph.</p>
<p>You can also have <strong><em>bold and italic together</em></strong>.</p>

<h2>Nested Formatting</h2>
<p>This is <strong>bold with <em>italic inside</em> it</strong>.</p>
<p>This is <em>italic with <strong>bold inside</strong> it</em>.</p>

<h2>Formatting in Lists</h2>
<ul>
    <li><strong>Bold</strong> list item</li>
    <li><em>Italic</em> list item</li>
    <li>Normal with <strong>bold</strong> and <em>italic</em> words</li>
</ul>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        println!("\n=== Comprehensive Formatting Demo ===");
        println!("{}", result);
        println!("=== End Demo ===\n");

        // Verify all formatting is present
        assert!(result.contains("# Text Formatting Examples"));
        assert!(result.contains("## Bold Text"));
        assert!(result.contains("**bold text**"));
        assert!(result.contains("**more bold**"));
        assert!(result.contains("## Italic Text"));
        assert!(result.contains("*italic text*"));
        assert!(result.contains("*more italic*"));
        assert!(result.contains("## Combined Formatting"));
        assert!(result.contains("***bold and italic together***"));
        assert!(result.contains("## Nested Formatting"));
        assert!(result.contains("**bold with *italic inside* it**"));
        assert!(result.contains("*italic with **bold inside** it*"));
        assert!(result.contains("## Formatting in Lists"));
        assert!(result.contains("- **Bold** list item"));
        assert!(result.contains("- *Italic* list item"));
        assert!(result.contains("- Normal with **bold** and *italic* words"));
    }

    // HTML Entity Decoding Tests
    // These tests verify that html5ever automatically decodes HTML entities
    // during parsing, so the converter receives decoded text in the DOM.

    #[test]
    fn test_common_named_entities() {
        let html = br#"
<html><body>
<p>&amp; &lt; &gt; &quot; &#39;</p>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // html5ever decodes entities automatically
        assert!(
            result.contains("& < > \" '"),
            "Common named entities should be decoded"
        );
    }

    #[test]
    fn test_decimal_numeric_entities() {
        let html = br#"
<html><body>
<p>&#65; &#66; &#67;</p>
<p>&#48; &#49; &#50;</p>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Decimal entities should be decoded to their characters
        assert!(
            result.contains("A B C"),
            "Decimal entities for letters should be decoded"
        );
        assert!(
            result.contains("0 1 2"),
            "Decimal entities for digits should be decoded"
        );
    }

    #[test]
    fn test_hexadecimal_numeric_entities() {
        let html = br#"
<html><body>
<p>&#x41; &#x42; &#x43;</p>
<p>&#x30; &#x31; &#x32;</p>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Hexadecimal entities should be decoded to their characters
        assert!(
            result.contains("A B C"),
            "Hex entities for letters should be decoded"
        );
        assert!(
            result.contains("0 1 2"),
            "Hex entities for digits should be decoded"
        );
    }

    #[test]
    fn test_nbsp_entity() {
        let html = br#"
<html><body>
<p>word&nbsp;word</p>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // &nbsp; (non-breaking space) should be decoded to a space character
        // Note: The actual character is U+00A0, but it may be normalized to a regular space
        assert!(result.contains("word"), "Text should be present");
    }

    #[test]
    fn test_entities_in_headings() {
        let html = br#"
<html><body>
<h1>&lt;Title&gt; &amp; Subtitle</h1>
<h2>Section &quot;One&quot;</h2>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("# <Title> & Subtitle"),
            "Entities in h1 should be decoded"
        );
        assert!(
            result.contains("## Section \"One\""),
            "Entities in h2 should be decoded"
        );
    }

    #[test]
    fn test_entities_in_links() {
        let html = br#"
<html><body>
<p><a href="http://example.com?a=1&amp;b=2">Link &lt;text&gt;</a></p>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Entities in link text should be decoded
        assert!(
            result.contains("Link <text>"),
            "Entities in link text should be decoded"
        );
        // Entities in href should also be decoded by html5ever
        assert!(
            result.contains("a=1&b=2"),
            "Entities in href should be decoded"
        );
    }

    #[test]
    fn test_entities_in_code() {
        let html = br#"
<html><body>
<p>Inline code: <code>&lt;tag&gt; &amp; text</code></p>
<pre><code>&lt;html&gt;
&lt;body&gt;
&lt;/body&gt;
&lt;/html&gt;</code></pre>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Entities in code should be decoded (html5ever decodes them)
        assert!(
            result.contains("`<tag> & text`"),
            "Entities in inline code should be decoded"
        );
        assert!(
            result.contains("<html>"),
            "Entities in code block should be decoded"
        );
        assert!(
            result.contains("<body>"),
            "Entities in code block should be decoded"
        );
    }

    #[test]
    fn test_mixed_entities() {
        let html = br#"
<html><body>
<p>Named: &amp; &lt; &gt; Decimal: &#65; &#66; Hex: &#x43; &#x44;</p>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // All entity types should be decoded
        assert!(
            result.contains("Named: & < >"),
            "Named entities should be decoded"
        );
        assert!(
            result.contains("Decimal: A B"),
            "Decimal entities should be decoded"
        );
        assert!(
            result.contains("Hex: C D"),
            "Hex entities should be decoded"
        );
    }

    #[test]
    fn test_entities_in_lists() {
        let html = br#"
<html><body>
<ul>
<li>&lt;item&gt; one</li>
<li>item &amp; two</li>
<li>item &quot;three&quot;</li>
</ul>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert!(
            result.contains("- <item> one"),
            "Entities in list items should be decoded"
        );
        assert!(
            result.contains("- item & two"),
            "Entities in list items should be decoded"
        );
        assert!(
            result.contains("- item \"three\""),
            "Entities in list items should be decoded"
        );
    }

    #[test]
    fn test_double_encoded_entities() {
        let html = br#"
<html><body>
<p>&amp;lt; &amp;gt; &amp;amp;</p>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Double-encoded entities should be decoded once by html5ever
        // &amp;lt; becomes &lt; (not <)
        assert!(
            result.contains("&lt; &gt; &amp;"),
            "Double-encoded entities should be decoded once"
        );
    }

    #[test]
    fn test_unicode_entities() {
        let html = br#"
<html><body>
<p>&#8364; &#8217; &#8220; &#8221;</p>
<p>&#x20AC; &#x2019; &#x201C; &#x201D;</p>
</body></html>
"#;

        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        // Unicode entities should be decoded to their characters
        // € (euro), ' (right single quote), " (left double quote), " (right double quote)
        assert!(result.contains("€"), "Euro symbol should be decoded");
        assert!(
            result.contains("\u{2019}"),
            "Right single quote should be decoded"
        );
        assert!(
            result.contains("\u{201C}"),
            "Left double quote should be decoded"
        );
        assert!(
            result.contains("\u{201D}"),
            "Right double quote should be decoded"
        );
    }

    // ============================================================================
    // Pruning Integration Tests
    // ============================================================================

    // NOTE: Testing `<nav>` pruning when `prune_noise_regions` is enabled is
    // skipped here because the feature flag is off by default. When enabled,
    // `<nav><a href="/">Home</a></nav>` should produce empty output.

    /// Test that content around pruned elements (script) is preserved.
    /// Validates: FR-10.3 — pruning must not discard surrounding content.
    #[test]
    fn test_pruning_preserves_content_around_pruned_elements() {
        let html = b"<p>Before</p><script>alert('x')</script><p>After</p>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert_eq!(result, "Before\n\nAfter\n");
    }

    /// Test that nested prunable elements produce no output.
    /// Validates: FR-10.3 — early pruning skips entire subtrees.
    #[test]
    fn test_pruning_nested_prunable_elements() {
        let html = b"<script><style>body{}</style></script>";
        let dom = parse_html(html).expect("Parse failed");
        let converter = MarkdownConverter::new();
        let result = converter.convert(&dom).expect("Conversion failed");

        assert_eq!(result, "\n");
    }
}
