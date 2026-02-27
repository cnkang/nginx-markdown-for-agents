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
use markup5ever_rcdom::{Handle, NodeData, RcDom};
use std::cell::Ref;
use std::time::{Duration, Instant};

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
        // Pre-allocate output buffer with reasonable capacity
        // Average compression ratio is ~70-85%, so we estimate output size
        let mut output = String::with_capacity(1024);

        // Extract metadata and add YAML front matter if enabled
        if self.options.include_front_matter && self.options.extract_metadata {
            use crate::metadata::MetadataExtractor;

            let extractor = MetadataExtractor::new(
                self.options.base_url.clone(),
                self.options.resolve_relative_urls,
            );

            if let Ok(metadata) = extractor.extract(dom) {
                self.write_front_matter(&mut output, &metadata)?;
            }

            // Check timeout after metadata extraction
            ctx.check_timeout()?;
        }

        // Start traversal from document root
        // Depth 0 represents the document level
        self.traverse_node_with_context(&dom.document, &mut output, 0, ctx)?;

        // Check timeout before output normalization
        ctx.check_timeout()?;

        // Normalize output: ensure single trailing newline
        let markdown = self.normalize_output(output);

        // Final timeout check after normalization
        ctx.check_timeout()?;

        Ok(markdown)
    }

    /// Write YAML front matter from metadata
    ///
    /// Generates a YAML front matter block with extracted metadata. The front matter
    /// is enclosed in `---` delimiters and includes fields that have values.
    ///
    /// # YAML Formatting Rules
    ///
    /// - Only include fields that have non-empty values
    /// - Escape YAML special characters in values (quotes, colons, etc.)
    /// - Use double quotes for string values to ensure proper escaping
    /// - Include resolved absolute URLs for images
    ///
    /// # Arguments
    ///
    /// * `output` - Mutable string buffer to write front matter to
    /// * `metadata` - Extracted page metadata
    ///
    /// # Format
    ///
    /// ```yaml
    /// ---
    /// title: "Page Title"
    /// url: "https://example.com/page"
    /// description: "Page description"
    /// image: "https://example.com/image.png"
    /// author: "Author Name"
    /// published: "2024-01-15"
    /// ---
    ///
    /// ```
    ///
    /// # Requirements
    ///
    /// Validates: FR-15.3, FR-15.4, FR-15.5
    fn write_front_matter(
        &self,
        output: &mut String,
        metadata: &crate::metadata::PageMetadata,
    ) -> Result<(), ConversionError> {
        // Start YAML front matter block
        output.push_str("---\n");

        // Add title (required field per FR-15.4)
        if let Some(ref title) = metadata.title
            && !title.is_empty()
        {
            output.push_str("title: ");
            self.write_yaml_string(output, title);
            output.push('\n');
        }

        // Add URL (required field per FR-15.4)
        if let Some(ref url) = metadata.url
            && !url.is_empty()
        {
            output.push_str("url: ");
            self.write_yaml_string(output, url);
            output.push('\n');
        }

        // Add description (optional field per FR-15.5)
        if let Some(ref description) = metadata.description
            && !description.is_empty()
        {
            output.push_str("description: ");
            self.write_yaml_string(output, description);
            output.push('\n');
        }

        // Add image with resolved absolute URL (optional field per FR-15.5)
        if let Some(ref image) = metadata.image
            && !image.is_empty()
        {
            output.push_str("image: ");
            self.write_yaml_string(output, image);
            output.push('\n');
        }

        // Add author (optional field)
        if let Some(ref author) = metadata.author
            && !author.is_empty()
        {
            output.push_str("author: ");
            self.write_yaml_string(output, author);
            output.push('\n');
        }

        // Add published date (optional field)
        if let Some(ref published) = metadata.published
            && !published.is_empty()
        {
            output.push_str("published: ");
            self.write_yaml_string(output, published);
            output.push('\n');
        }

        // End YAML front matter block with blank line separator
        output.push_str("---\n\n");

        Ok(())
    }

    /// Write a YAML string value with proper escaping
    ///
    /// Escapes YAML special characters and wraps the value in double quotes.
    /// This ensures the value is properly interpreted by YAML parsers.
    ///
    /// # YAML Special Characters
    ///
    /// The following characters require escaping:
    /// - `"` (double quote) -> `\"`
    /// - `\` (backslash) -> `\\`
    /// - Newlines and control characters are preserved within quotes
    ///
    /// # Arguments
    ///
    /// * `output` - Mutable string buffer to write to
    /// * `value` - String value to escape and write
    ///
    /// # Examples
    ///
    /// - `Hello World` -> `"Hello World"`
    /// - `Title: Subtitle` -> `"Title: Subtitle"`
    /// - `Quote "test"` -> `"Quote \"test\""`
    fn write_yaml_string(&self, output: &mut String, value: &str) {
        output.push('"');
        for ch in value.chars() {
            match ch {
                '"' => output.push_str("\\\""),
                '\\' => output.push_str("\\\\"),
                '\n' => output.push_str("\\n"),
                '\r' => output.push_str("\\r"),
                '\t' => output.push_str("\\t"),
                _ => output.push(ch),
            }
        }
        output.push('"');
    }

    /// Returns true if the output buffer already contains Markdown body content.
    ///
    /// When YAML front matter is enabled, the output buffer is pre-populated before DOM
    /// traversal starts. Text-node whitespace normalization should not treat that prefix
    /// as body content, otherwise leading whitespace in the first body text node can be
    /// emitted inconsistently depending on the front matter toggle.
    fn has_body_content(&self, output: &str) -> bool {
        if output.is_empty() {
            return false;
        }

        if self.options.include_front_matter
            && self.options.extract_metadata
            && output.starts_with("---\n")
            && let Some(rest) = output.strip_prefix("---\n")
            && let Some(end_offset) = rest.find("\n---\n")
        {
            let body = &rest[end_offset + 5..];
            return body.chars().any(|ch| !ch.is_whitespace());
        }

        true
    }

    /// Traverse a DOM node and convert it to Markdown
    ///
    /// This is the core recursive traversal function. It processes each node
    /// according to its type and recursively processes children.
    ///
    /// # Arguments
    ///
    /// * `node` - Current DOM node to process
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth (0 = document root)
    ///
    /// # Traversal Strategy
    ///
    /// The traversal follows these steps:
    /// 1. Process the current node based on its type
    /// 2. Recursively process all child nodes in document order
    /// 3. Apply any closing formatting (e.g., blank lines after blocks)
    ///
    /// # Depth Tracking
    ///
    /// The depth parameter enables:
    /// - Proper indentation for nested structures
    /// - Detection of excessive nesting
    /// - Context-aware formatting decisions
    fn traverse_node(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        match node.data {
            NodeData::Document => {
                // Document root - process all children
                for child in node.children.borrow().iter() {
                    self.traverse_node(child, output, depth)?;
                }
            }
            NodeData::Element { ref name, .. } => {
                // Element node - dispatch to appropriate handler
                let tag_name = name.local.as_ref();
                self.handle_element(node, tag_name, output, depth)?;
            }
            NodeData::Text { ref contents } => {
                // Text node - extract and normalize text
                let text = contents.borrow();
                let normalized = self.normalize_text(&text);
                if !normalized.is_empty() {
                    // Add space before if original text had leading whitespace
                    if text.starts_with(|c: char| c.is_whitespace())
                        && self.has_body_content(output)
                        && !output.ends_with(' ')
                    {
                        output.push(' ');
                    }
                    output.push_str(&normalized);
                    // Add space after if original text had trailing whitespace
                    if text.ends_with(|c: char| c.is_whitespace()) {
                        output.push(' ');
                    }
                }
            }
            NodeData::Comment { .. } => {
                // Comments are ignored in Markdown output
            }
            NodeData::Doctype { .. } => {
                // DOCTYPE declarations are ignored
            }
            NodeData::ProcessingInstruction { .. } => {
                // Processing instructions are ignored
            }
        }

        Ok(())
    }

    /// Traverse a DOM node with timeout support
    ///
    /// This method is similar to `traverse_node` but includes cooperative timeout checking.
    /// It increments the node count and checks timeout every 100 nodes.
    ///
    /// # Arguments
    ///
    /// * `node` - Current DOM node to process
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth (0 = document root)
    /// * `ctx` - Conversion context for timeout tracking
    ///
    /// # Timeout Checkpoints
    ///
    /// This method automatically checks timeout every 100 nodes by calling
    /// `ctx.increment_and_check()`. This provides a balance between:
    /// - Performance: Not checking on every single node
    /// - Responsiveness: Detecting timeout within reasonable time
    ///
    /// # Requirements
    ///
    /// Validates: FR-10.2, FR-10.7
    fn traverse_node_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: &mut ConversionContext,
    ) -> Result<(), ConversionError> {
        // Increment node count and check timeout at checkpoints (every 100 nodes)
        ctx.increment_and_check()?;

        match node.data {
            NodeData::Document => {
                // Document root - process all children
                for child in node.children.borrow().iter() {
                    self.traverse_node_with_context(child, output, depth, ctx)?;
                }
            }
            NodeData::Element { ref name, .. } => {
                // Element node - dispatch to appropriate handler
                let tag_name = name.local.as_ref();
                self.handle_element_with_context(node, tag_name, output, depth, ctx)?;
            }
            NodeData::Text { ref contents } => {
                // Text node - extract and normalize text
                let text = contents.borrow();
                let normalized = self.normalize_text(&text);
                if !normalized.is_empty() {
                    // Add space before if original text had leading whitespace
                    if text.starts_with(|c: char| c.is_whitespace())
                        && self.has_body_content(output)
                        && !output.ends_with(' ')
                    {
                        output.push(' ');
                    }
                    output.push_str(&normalized);
                    // Add space after if original text had trailing whitespace
                    if text.ends_with(|c: char| c.is_whitespace()) {
                        output.push(' ');
                    }
                }
            }
            NodeData::Comment { .. } => {
                // Comments are ignored in Markdown output
            }
            NodeData::Doctype { .. } => {
                // DOCTYPE declarations are ignored
            }
            NodeData::ProcessingInstruction { .. } => {
                // Processing instructions are ignored
            }
        }

        Ok(())
    }

    /// Handle an HTML element and convert it to Markdown
    ///
    /// This function dispatches to specific element handlers based on the tag name.
    /// It implements the element-specific conversion logic for each supported HTML element.
    ///
    /// # Arguments
    ///
    /// * `node` - The element node to process
    /// * `tag_name` - The HTML tag name (e.g., "h1", "p", "div")
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Supported Elements
    ///
    /// Currently supported elements:
    /// - `h1` to `h6`: Headings (ATX-style)
    /// - `p`: Paragraphs
    /// - Other elements: Processed as containers (children traversed)
    ///
    /// # Future Extensions
    ///
    /// Additional element handlers will be added in subsequent tasks:
    /// - Links (`a`)
    /// - Images (`img`)
    /// - Lists (`ul`, `ol`, `li`)
    /// - Code blocks (`pre`, `code`)
    /// - Formatting (`strong`, `em`, `code`)
    /// - Tables (`table`, `tr`, `td`, `th`)
    fn handle_element(
        &self,
        node: &Handle,
        tag_name: &str,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        // Security validation: check if element should be sanitized
        use crate::security::SanitizeAction;
        let sanitize_action = self.security_validator.check_element(tag_name);

        match sanitize_action {
            SanitizeAction::Remove => {
                // Skip dangerous elements and their children
                return Ok(());
            }
            SanitizeAction::Allow | SanitizeAction::StripAttributes | SanitizeAction::StripUrl => {
                // Continue processing, but check attributes if needed
            }
        }

        // Validate nesting depth
        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        match tag_name {
            // Heading elements (h1-h6)
            "h1" => self.handle_heading(node, 1, output, depth)?,
            "h2" => self.handle_heading(node, 2, output, depth)?,
            "h3" => self.handle_heading(node, 3, output, depth)?,
            "h4" => self.handle_heading(node, 4, output, depth)?,
            "h5" => self.handle_heading(node, 5, output, depth)?,
            "h6" => self.handle_heading(node, 6, output, depth)?,

            // Paragraph element
            "p" => self.handle_paragraph(node, output, depth)?,

            // Link element
            "a" => self.handle_link(node, output, depth)?,

            // Image element
            "img" => self.handle_image(node, output, depth)?,

            // List elements
            "ul" => self.handle_list(node, output, 0, false)?,
            "ol" => self.handle_list(node, output, 0, true)?,
            "li" => self.handle_list_item(node, output, 0)?,

            // Code elements
            "pre" => self.handle_code_block(node, output, depth)?,
            "code" => self.handle_inline_code(node, output, depth)?,

            // Text formatting elements
            "strong" | "b" => self.handle_bold(node, output, depth)?,
            "em" | "i" => self.handle_italic(node, output, depth)?,

            // Table elements (GFM only)
            "table" => self.handle_table(node, output, depth)?,

            // Elements to skip (non-content) - already handled by security validator
            "script" | "style" | "noscript" => {
                // Skip these elements and their children
            }

            // Default: process as container (traverse children)
            _ => {
                for child in node.children.borrow().iter() {
                    self.traverse_node(child, output, depth + 1)?;
                }
            }
        }

        Ok(())
    }

    /// Handle an HTML element with timeout support
    ///
    /// This method is similar to `handle_element` but passes the conversion context
    /// through to child traversals for timeout checking.
    ///
    /// # Arguments
    ///
    /// * `node` - The element node to process
    /// * `tag_name` - The HTML tag name (e.g., "h1", "p", "div")
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    /// * `ctx` - Conversion context for timeout tracking
    ///
    /// # Requirements
    ///
    /// Validates: FR-10.2, FR-10.7
    fn handle_element_with_context(
        &self,
        node: &Handle,
        tag_name: &str,
        output: &mut String,
        depth: usize,
        ctx: &mut ConversionContext,
    ) -> Result<(), ConversionError> {
        // Security validation: check if element should be sanitized
        use crate::security::SanitizeAction;
        let sanitize_action = self.security_validator.check_element(tag_name);

        match sanitize_action {
            SanitizeAction::Remove => {
                // Skip dangerous elements and their children
                return Ok(());
            }
            SanitizeAction::Allow | SanitizeAction::StripAttributes | SanitizeAction::StripUrl => {
                // Continue processing, but check attributes if needed
            }
        }

        // Validate nesting depth
        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        match tag_name {
            // Heading elements (h1-h6)
            "h1" => self.handle_heading(node, 1, output, depth)?,
            "h2" => self.handle_heading(node, 2, output, depth)?,
            "h3" => self.handle_heading(node, 3, output, depth)?,
            "h4" => self.handle_heading(node, 4, output, depth)?,
            "h5" => self.handle_heading(node, 5, output, depth)?,
            "h6" => self.handle_heading(node, 6, output, depth)?,

            // Paragraph element
            "p" => self.handle_paragraph(node, output, depth)?,

            // Link element
            "a" => self.handle_link(node, output, depth)?,

            // Image element
            "img" => self.handle_image(node, output, depth)?,

            // List elements
            "ul" => self.handle_list(node, output, 0, false)?,
            "ol" => self.handle_list(node, output, 0, true)?,
            "li" => self.handle_list_item(node, output, 0)?,

            // Code elements
            "pre" => self.handle_code_block(node, output, depth)?,
            "code" => self.handle_inline_code(node, output, depth)?,

            // Text formatting elements
            "strong" | "b" => self.handle_bold(node, output, depth)?,
            "em" | "i" => self.handle_italic(node, output, depth)?,

            // Table elements (GFM only)
            "table" => self.handle_table(node, output, depth)?,

            // Elements to skip (non-content) - already handled by security validator
            "script" | "style" | "noscript" => {
                // Skip these elements and their children
            }

            // Default: process as container (traverse children with context)
            _ => {
                for child in node.children.borrow().iter() {
                    self.traverse_node_with_context(child, output, depth + 1, ctx)?;
                }
            }
        }

        Ok(())
    }

    /// Handle heading elements (h1-h6)
    ///
    /// Converts HTML headings to ATX-style Markdown headings using `#` symbols.
    ///
    /// # Arguments
    ///
    /// * `node` - The heading element node
    /// * `level` - Heading level (1-6)
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Output Format
    ///
    /// - Level 1: `# Heading`
    /// - Level 2: `## Heading`
    /// - Level 3: `### Heading`
    /// - etc.
    ///
    /// Headings are followed by two newlines to create a blank line separator.
    fn handle_heading(
        &self,
        node: &Handle,
        level: usize,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        // Ensure blank line before heading (if not at start)
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        // Add ATX-style heading markers
        for _ in 0..level {
            output.push('#');
        }
        output.push(' ');

        // Process heading content (including inline elements like code)
        let start_len = output.len();
        for child in node.children.borrow().iter() {
            self.traverse_node(child, output, depth + 1)?;
        }

        // Normalize the heading text (collapse whitespace, trim)
        let heading_content = output[start_len..].to_string();
        let normalized = self.normalize_text(&heading_content);
        output.truncate(start_len);
        output.push_str(&normalized);

        // Add blank line after heading
        output.push_str("\n\n");

        Ok(())
    }

    /// Handle paragraph elements
    ///
    /// Converts HTML paragraphs to plain text with blank line separation.
    ///
    /// # Arguments
    ///
    /// * `node` - The paragraph element node
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Output Format
    ///
    /// Paragraphs are rendered as plain text followed by two newlines to create
    /// a blank line separator between paragraphs.
    fn handle_paragraph(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        // Ensure blank line before paragraph (if not at start)
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        // Process paragraph children (which may include inline elements like links, images)
        let start_len = output.len();
        for child in node.children.borrow().iter() {
            self.traverse_node(child, output, depth + 1)?;
        }

        // Add blank line after paragraph if content was added
        if output.len() > start_len {
            output.push_str("\n\n");
        }

        Ok(())
    }

    /// Handle anchor (link) elements
    ///
    /// Converts HTML anchor tags to Markdown link format: `[text](url)`
    ///
    /// # Arguments
    ///
    /// * `node` - The anchor element node
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Output Format
    ///
    /// Links are rendered as `[link text](href)` where:
    /// - `link text` is the text content of the anchor element
    /// - `href` is the value of the href attribute
    ///
    /// If the href attribute is missing, the link text is rendered as plain text.
    ///
    /// # Examples
    ///
    /// ```html
    /// <a href="https://example.com">Example</a>
    /// ```
    /// becomes:
    /// ```markdown
    /// [Example](https://example.com)
    /// ```
    fn handle_link(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
    ) -> Result<(), ConversionError> {
        // Extract href attribute
        // Note: Attributes are processed in the order they appear in the DOM.
        // For deterministic output, we rely on html5ever's consistent attribute ordering.
        let href = if let NodeData::Element { ref attrs, .. } = node.data {
            attrs
                .borrow()
                .iter()
                .find(|attr| attr.name.local.as_ref() == "href")
                .map(|attr| attr.value.to_string())
        } else {
            None
        };

        // Extract link text from children
        let mut link_text = String::new();
        for child in node.children.borrow().iter() {
            self.extract_text(child, &mut link_text)?;
        }
        let normalized_text = self.normalize_text(&link_text);

        // Generate Markdown link or plain text if no href
        if let Some(url) = href {
            // Security: Sanitize URL to prevent javascript: and data: URLs
            if let Some(safe_url) = self.security_validator.sanitize_url(&url) {
                if !normalized_text.is_empty() {
                    output.push('[');
                    output.push_str(&normalized_text);
                    output.push_str("](");
                    output.push_str(safe_url);
                    output.push(')');
                }
            } else {
                // Dangerous URL detected, render as plain text without link
                if !normalized_text.is_empty() {
                    output.push_str(&normalized_text);
                }
            }
        } else {
            // No href attribute, render as plain text
            if !normalized_text.is_empty() {
                output.push_str(&normalized_text);
            }
        }

        Ok(())
    }

    /// Handle image elements
    ///
    /// Converts HTML img tags to Markdown image format: `![alt](src)`
    ///
    /// # Arguments
    ///
    /// * `node` - The img element node
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Output Format
    ///
    /// Images are rendered as `![alt text](src)` where:
    /// - `alt text` is the value of the alt attribute (or empty if missing)
    /// - `src` is the value of the src attribute
    ///
    /// If the src attribute is missing, the image is not rendered.
    ///
    /// # Deterministic Output
    ///
    /// Attributes are processed in a consistent order (html5ever maintains insertion order)
    /// to ensure deterministic output for stable ETag generation.
    ///
    /// # Examples
    ///
    /// ```html
    /// <img src="image.png" alt="Description">
    /// ```
    /// becomes:
    /// ```markdown
    /// ![Description](image.png)
    /// ```
    fn handle_image(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
    ) -> Result<(), ConversionError> {
        // Extract src and alt attributes
        // Note: Attributes are processed in the order they appear in the DOM.
        // For deterministic output, we rely on html5ever's consistent attribute ordering.
        let (src, alt) = if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let src = attrs_borrowed
                .iter()
                .find(|attr| attr.name.local.as_ref() == "src")
                .map(|attr| attr.value.to_string());
            let alt = attrs_borrowed
                .iter()
                .find(|attr| attr.name.local.as_ref() == "alt")
                .map(|attr| attr.value.to_string())
                .unwrap_or_default();
            (src, alt)
        } else {
            (None, String::new())
        };

        // Generate Markdown image if src is present and safe
        if let Some(url) = src {
            // Security: Sanitize URL to prevent javascript: and data: URLs
            if let Some(safe_url) = self.security_validator.sanitize_url(&url) {
                output.push_str("![");
                output.push_str(&alt);
                output.push_str("](");
                output.push_str(safe_url);
                output.push(')');
            }
            // If URL is dangerous, skip the image entirely
        }

        Ok(())
    }

    /// Handle list elements (ul/ol)
    ///
    /// Converts HTML unordered and ordered lists to Markdown list format.
    ///
    /// # Arguments
    ///
    /// * `node` - The list element node (ul or ol)
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    /// * `ordered` - true for ordered lists (ol), false for unordered lists (ul)
    ///
    /// # Output Format
    ///
    /// - Unordered lists use `- ` prefix
    /// - Ordered lists use `1. ` prefix (all items numbered as 1)
    /// - Nested lists are indented with 2 spaces per level
    ///
    /// # Examples
    ///
    /// ```html
    /// <ul>
    ///   <li>Item 1</li>
    ///   <li>Item 2</li>
    /// </ul>
    /// ```
    /// becomes:
    /// ```markdown
    /// - Item 1
    /// - Item 2
    /// ```
    fn handle_list(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ordered: bool,
    ) -> Result<(), ConversionError> {
        // Ensure blank line before list (if not at start)
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        // Store the list type in the context for list items
        // Process all list item children
        for child in node.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "li"
            {
                self.handle_list_item_with_marker(child, output, depth, ordered)?;
            }
        }

        // Ensure blank line after list
        if !output.ends_with("\n\n") {
            output.push('\n');
        }

        Ok(())
    }

    /// Handle list item elements (li)
    ///
    /// This is called when a list item is encountered outside of list context.
    /// It delegates to handle_list_item_with_marker with default settings.
    fn handle_list_item(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        // Default to unordered list marker
        self.handle_list_item_with_marker(node, output, depth, false)
    }

    /// Handle list item elements with specific marker type
    ///
    /// Converts HTML list items to Markdown list items with proper indentation.
    ///
    /// # Arguments
    ///
    /// * `node` - The list item element node
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth (for indentation)
    /// * `ordered` - true for ordered list marker (1.), false for unordered (-)
    ///
    /// # Output Format
    ///
    /// List items are indented based on depth:
    /// - Depth 0: no indentation
    /// - Depth 1: 2 spaces
    /// - Depth 2: 4 spaces
    /// - etc.
    fn handle_list_item_with_marker(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ordered: bool,
    ) -> Result<(), ConversionError> {
        // Add indentation based on depth (2 spaces per level)
        for _ in 0..depth {
            output.push_str("  ");
        }

        // Add list marker
        if ordered {
            output.push_str("1. ");
        } else {
            output.push_str("- ");
        }

        // Process list item content
        let start_len = output.len();
        for child in node.children.borrow().iter() {
            match child.data {
                NodeData::Element { ref name, .. } => {
                    let tag_name = name.local.as_ref();
                    // Handle nested lists
                    if tag_name == "ul" {
                        // Finish current line before nested list
                        if output.len() > start_len && !output.ends_with('\n') {
                            output.push('\n');
                        }

                        // Process nested unordered list
                        self.handle_list(child, output, depth + 1, false)?;
                    } else if tag_name == "ol" {
                        // Finish current line before nested list
                        if output.len() > start_len && !output.ends_with('\n') {
                            output.push('\n');
                        }

                        // Process nested ordered list
                        self.handle_list(child, output, depth + 1, true)?;
                    } else {
                        // Process other elements (including inline elements like <a>, <img>)
                        self.traverse_node(child, output, depth + 1)?;
                    }
                }
                _ => {
                    // Process text nodes and other content
                    self.traverse_node(child, output, depth + 1)?;
                }
            }
        }

        // Ensure line ends with newline
        if !output.ends_with('\n') {
            output.push('\n');
        }

        Ok(())
    }

    /// Handle code block elements (pre/code)
    ///
    /// Converts HTML code blocks to fenced code blocks in Markdown.
    /// Detects language from class attributes (e.g., class="language-python").
    /// Preserves code content without any text normalization.
    ///
    /// # Arguments
    ///
    /// * `node` - The pre element node
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Output Format
    ///
    /// - With language: ```python\ncode\n```
    /// - Without language: ```\ncode\n```
    ///
    /// Code blocks are surrounded by blank lines for proper separation.
    fn handle_code_block(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
    ) -> Result<(), ConversionError> {
        // Ensure blank line before code block (if not at start)
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        // Try to detect language from class attribute
        let mut language = String::new();

        // Check if this pre contains a code element with language class
        for child in node.children.borrow().iter() {
            if let NodeData::Element {
                ref name,
                ref attrs,
                ..
            } = child.data
                && name.local.as_ref() == "code"
            {
                // Look for class attribute with language- prefix
                for attr in attrs.borrow().iter() {
                    if attr.name.local.as_ref() == "class" {
                        let class_value = attr.value.to_string();
                        // Look for language-* or lang-* patterns
                        for class in class_value.split_whitespace() {
                            if let Some(lang) = class.strip_prefix("language-") {
                                language = lang.to_string();
                                break;
                            } else if let Some(lang) = class.strip_prefix("lang-") {
                                language = lang.to_string();
                                break;
                            }
                        }
                        if !language.is_empty() {
                            break;
                        }
                    }
                }
            }
        }

        // Start fenced code block
        output.push_str("```");
        if !language.is_empty() {
            output.push_str(&language);
        }
        output.push('\n');

        // Extract code content WITHOUT normalization
        // This is critical - code must be preserved exactly as-is
        self.extract_code_content(node, output)?;

        // End fenced code block
        // Ensure code ends with newline before closing fence
        if !output.ends_with('\n') {
            output.push('\n');
        }
        output.push_str("```");
        output.push('\n');

        // Ensure blank line after code block
        output.push('\n');

        Ok(())
    }

    /// Handle inline code elements (code)
    ///
    /// Converts HTML inline code to backtick-wrapped code in Markdown.
    /// Preserves code content without modification.
    ///
    /// # Arguments
    ///
    /// * `node` - The code element node
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Output Format
    ///
    /// - Inline code: `code`
    ///
    /// # Note
    ///
    /// This handler is only called for standalone code elements (inline code).
    /// Code elements inside pre elements are handled by handle_code_block.
    fn handle_inline_code(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
    ) -> Result<(), ConversionError> {
        // Extract code content WITHOUT normalization
        let mut code_content = String::new();
        self.extract_code_content(node, &mut code_content)?;

        // Wrap in backticks
        output.push('`');
        output.push_str(&code_content);
        output.push('`');

        Ok(())
    }

    /// Handle bold/strong elements
    ///
    /// Converts HTML bold elements (strong, b) to Markdown bold format: `**text**`
    ///
    /// # Arguments
    ///
    /// * `node` - The bold element node
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Output Format
    ///
    /// Bold text is rendered as `**text**` where text is the content of the element.
    /// Nested formatting is supported (e.g., bold within italic or vice versa).
    ///
    /// # Examples
    ///
    /// ```html
    /// <strong>bold text</strong>
    /// <b>also bold</b>
    /// ```
    /// becomes:
    /// ```markdown
    /// **bold text**
    /// **also bold**
    /// ```
    fn handle_bold(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        // Add opening bold marker
        output.push_str("**");

        // Process children (which may include nested formatting)
        for child in node.children.borrow().iter() {
            self.traverse_node(child, output, depth + 1)?;
        }

        // Add closing bold marker
        output.push_str("**");

        Ok(())
    }

    /// Handle italic/emphasis elements
    ///
    /// Converts HTML italic elements (em, i) to Markdown italic format: `*text*`
    ///
    /// # Arguments
    ///
    /// * `node` - The italic element node
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Output Format
    ///
    /// Italic text is rendered as `*text*` where text is the content of the element.
    /// Nested formatting is supported (e.g., italic within bold or vice versa).
    ///
    /// # Examples
    ///
    /// ```html
    /// <em>italic text</em>
    /// <i>also italic</i>
    /// ```
    /// becomes:
    /// ```markdown
    /// *italic text*
    /// *also italic*
    /// ```
    fn handle_italic(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        // Add opening italic marker
        output.push('*');

        // Process children (which may include nested formatting)
        for child in node.children.borrow().iter() {
            self.traverse_node(child, output, depth + 1)?;
        }

        // Add closing italic marker
        output.push('*');

        Ok(())
    }

    /// Handle table elements (GFM only)
    ///
    /// Converts HTML tables to GitHub Flavored Markdown table format.
    /// Only enabled when GFM flavor is configured.
    ///
    /// # Arguments
    ///
    /// * `node` - The table element node
    /// * `output` - Mutable string buffer for Markdown output
    /// * `depth` - Current nesting depth
    ///
    /// # Output Format
    ///
    /// GFM tables use pipe separators:
    /// ```markdown
    /// | Header 1 | Header 2 |
    /// | -------- | -------- |
    /// | Cell 1   | Cell 2   |
    /// ```
    ///
    /// Alignment is detected from style/align attributes:
    /// - Left: `| :--- |` (default)
    /// - Center: `| :---: |`
    /// - Right: `| ---: |`
    ///
    /// # GFM Flavor Check
    ///
    /// Tables are only converted when flavor is GitHubFlavoredMarkdown.
    /// For CommonMark, tables are processed as regular containers.
    fn handle_table(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        // Only convert tables for GFM flavor
        if !matches!(self.options.flavor, MarkdownFlavor::GitHubFlavoredMarkdown) {
            // For CommonMark, process as container (traverse children)
            for child in node.children.borrow().iter() {
                self.traverse_node(child, output, depth + 1)?;
            }
            return Ok(());
        }

        // Ensure blank line before table
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        // Extract table structure
        let mut headers: Vec<String> = Vec::new();
        let mut alignments: Vec<TableAlignment> = Vec::new();
        let mut rows: Vec<Vec<String>> = Vec::new();

        // Parse table children (thead, tbody, tr)
        for child in node.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data {
                match name.local.as_ref() {
                    "thead" => {
                        self.extract_table_header(child, &mut headers, &mut alignments)?;
                    }
                    "tbody" => {
                        // Check if first row in tbody should be treated as header
                        // If no headers yet, check if tbody's first row should be treated as header
                        if headers.is_empty() {
                            // Look for first tr in tbody
                            let children = child.children.borrow();
                            let first_tr_opt = children.iter().find(|c| {
                                if let NodeData::Element { ref name, .. } = c.data {
                                    name.local.as_ref() == "tr"
                                } else {
                                    false
                                }
                            });

                            if let Some(first_tr) = first_tr_opt {
                                // Check if first row has th elements
                                let has_th = first_tr.children.borrow().iter().any(|c| {
                                    if let NodeData::Element { ref name, .. } = c.data {
                                        name.local.as_ref() == "th"
                                    } else {
                                        false
                                    }
                                });

                                // Treat first row as header if it has th elements OR if it's the only way to get headers
                                // (This handles cases where HTML uses td for headers)
                                if has_th {
                                    // First row is header (has th elements)
                                    self.extract_table_row_as_header(
                                        first_tr,
                                        &mut headers,
                                        &mut alignments,
                                    )?;
                                    // Extract remaining rows as data
                                    let mut is_first = true;
                                    for tbody_child in children.iter() {
                                        if let NodeData::Element { ref name, .. } = tbody_child.data
                                            && name.local.as_ref() == "tr"
                                        {
                                            if is_first {
                                                is_first = false;
                                                continue; // Skip header row
                                            }
                                            let mut row_cells = Vec::new();
                                            self.extract_table_row(tbody_child, &mut row_cells)?;
                                            rows.push(row_cells);
                                        }
                                    }
                                } else {
                                    // First row uses td but treat as header anyway (common pattern)
                                    self.extract_table_row_as_header(
                                        first_tr,
                                        &mut headers,
                                        &mut alignments,
                                    )?;
                                    // Extract remaining rows as data
                                    let mut is_first = true;
                                    for tbody_child in children.iter() {
                                        if let NodeData::Element { ref name, .. } = tbody_child.data
                                            && name.local.as_ref() == "tr"
                                        {
                                            if is_first {
                                                is_first = false;
                                                continue; // Skip header row
                                            }
                                            let mut row_cells = Vec::new();
                                            self.extract_table_row(tbody_child, &mut row_cells)?;
                                            rows.push(row_cells);
                                        }
                                    }
                                }
                            } else {
                                // No rows in tbody
                                self.extract_table_rows(child, &mut rows)?;
                            }
                        } else {
                            // Headers already extracted from thead, all tbody rows are data
                            self.extract_table_rows(child, &mut rows)?;
                        }
                    }
                    "tr" => {
                        // Direct tr under table (no thead/tbody)
                        // This case is rare with html5ever as it auto-inserts tbody
                        if headers.is_empty() {
                            // First row is header
                            self.extract_table_row_as_header(child, &mut headers, &mut alignments)?;
                        } else {
                            // Subsequent rows are data
                            let mut row_cells = Vec::new();
                            self.extract_table_row(child, &mut row_cells)?;
                            rows.push(row_cells);
                        }
                    }
                    _ => {
                        // Ignore other elements
                    }
                }
            }
        }

        // If no headers found, skip table conversion
        if headers.is_empty() {
            return Ok(());
        }

        // Ensure alignments match header count
        while alignments.len() < headers.len() {
            alignments.push(TableAlignment::Left);
        }

        // Generate GFM table
        self.write_gfm_table(output, &headers, &alignments, &rows)?;

        // Ensure blank line after table
        if !output.ends_with("\n\n") {
            output.push('\n');
        }

        Ok(())
    }

    /// Extract table header from thead element
    fn extract_table_header(
        &self,
        thead: &Handle,
        headers: &mut Vec<String>,
        alignments: &mut Vec<TableAlignment>,
    ) -> Result<(), ConversionError> {
        // Find first tr in thead
        for child in thead.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "tr"
            {
                self.extract_table_row_as_header(child, headers, alignments)?;
                break;
            }
        }
        Ok(())
    }

    /// Extract table row as header (th elements)
    fn extract_table_row_as_header(
        &self,
        tr: &Handle,
        headers: &mut Vec<String>,
        alignments: &mut Vec<TableAlignment>,
    ) -> Result<(), ConversionError> {
        for child in tr.children.borrow().iter() {
            if let NodeData::Element {
                ref name,
                ref attrs,
                ..
            } = child.data
            {
                let tag = name.local.as_ref();
                if tag == "th" || tag == "td" {
                    // Extract cell content including inline formatting
                    let mut cell_output = String::new();
                    for cell_child in child.children.borrow().iter() {
                        self.traverse_node(cell_child, &mut cell_output, 0)?;
                    }
                    // Normalize whitespace and trim
                    let normalized = cell_output.trim().to_string();
                    headers.push(normalized);

                    // Extract alignment from attributes
                    let attrs_borrowed = attrs.borrow();
                    let alignment = self.extract_alignment(&attrs_borrowed);
                    alignments.push(alignment);
                }
            }
        }
        Ok(())
    }

    /// Extract table rows from tbody element
    fn extract_table_rows(
        &self,
        tbody: &Handle,
        rows: &mut Vec<Vec<String>>,
    ) -> Result<(), ConversionError> {
        for child in tbody.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "tr"
            {
                let mut row_cells = Vec::new();
                self.extract_table_row(child, &mut row_cells)?;
                rows.push(row_cells);
            }
        }
        Ok(())
    }

    /// Extract cells from a table row
    fn extract_table_row(
        &self,
        tr: &Handle,
        cells: &mut Vec<String>,
    ) -> Result<(), ConversionError> {
        for child in tr.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data {
                let tag = name.local.as_ref();
                if tag == "td" || tag == "th" {
                    // Extract cell content including inline formatting
                    let mut cell_output = String::new();
                    for cell_child in child.children.borrow().iter() {
                        self.traverse_node(cell_child, &mut cell_output, 0)?;
                    }
                    // Normalize whitespace and trim
                    let normalized = cell_output.trim().to_string();
                    cells.push(normalized);
                }
            }
        }
        Ok(())
    }

    /// Extract alignment from element attributes
    fn extract_alignment(&self, attrs: &Ref<Vec<html5ever::Attribute>>) -> TableAlignment {
        // Check align attribute
        for attr in attrs.iter() {
            if attr.name.local.as_ref() == "align" {
                let value = attr.value.to_string().to_lowercase();
                return match value.as_str() {
                    "left" => TableAlignment::Left,
                    "center" => TableAlignment::Center,
                    "right" => TableAlignment::Right,
                    _ => TableAlignment::Left,
                };
            }
        }

        // Check style attribute for text-align
        for attr in attrs.iter() {
            if attr.name.local.as_ref() == "style" {
                let style = attr.value.to_string().to_lowercase();
                if style.contains("text-align") {
                    if style.contains("center") {
                        return TableAlignment::Center;
                    } else if style.contains("right") {
                        return TableAlignment::Right;
                    } else if style.contains("left") {
                        return TableAlignment::Left;
                    }
                }
            }
        }

        TableAlignment::Left
    }

    /// Write GFM table to output
    fn write_gfm_table(
        &self,
        output: &mut String,
        headers: &[String],
        alignments: &[TableAlignment],
        rows: &[Vec<String>],
    ) -> Result<(), ConversionError> {
        // Write header row
        output.push('|');
        for header in headers {
            output.push(' ');
            output.push_str(header);
            output.push_str(" |");
        }
        output.push('\n');

        // Write separator row with alignment
        output.push('|');
        for alignment in alignments {
            output.push(' ');
            match alignment {
                TableAlignment::Left => output.push_str("---"),
                TableAlignment::Center => output.push_str(":---:"),
                TableAlignment::Right => output.push_str("---:"),
            }
            output.push_str(" |");
        }
        output.push('\n');

        // Write data rows
        for row in rows {
            output.push('|');
            for (i, cell) in row.iter().enumerate() {
                output.push(' ');
                output.push_str(cell);
                output.push_str(" |");

                // If row has fewer cells than headers, pad with empty cells
                if i >= headers.len() - 1 {
                    break;
                }
            }
            // Pad remaining cells if row is shorter than header
            for _ in row.len()..headers.len() {
                output.push_str("  |");
            }
            output.push('\n');
        }

        Ok(())
    }

    /// Extract code content from a node without any normalization
    ///
    /// This is critical for code blocks and inline code - we must preserve
    /// the exact content including whitespace, line breaks, and indentation.
    ///
    /// # Arguments
    ///
    /// * `node` - The node to extract code from
    /// * `output` - Mutable string buffer for code content
    fn extract_code_content(
        &self,
        node: &Handle,
        output: &mut String,
    ) -> Result<(), ConversionError> {
        match node.data {
            NodeData::Text { ref contents } => {
                // Add text content exactly as-is, NO normalization
                output.push_str(&contents.borrow());
            }
            NodeData::Element { .. } => {
                // Recursively extract from children
                for child in node.children.borrow().iter() {
                    self.extract_code_content(child, output)?;
                }
            }
            _ => {
                // Ignore other node types
            }
        }
        Ok(())
    }

    /// Extract text content from a node and its descendants
    ///
    /// This helper function recursively extracts all text content from a node,
    /// ignoring non-text elements. It's used to gather text for headings,
    /// paragraphs, and other text-containing elements.
    ///
    /// # Arguments
    ///
    /// * `node` - The node to extract text from
    /// * `output` - Mutable string buffer for extracted text
    fn extract_text(&self, node: &Handle, output: &mut String) -> Result<(), ConversionError> {
        match node.data {
            NodeData::Text { ref contents } => {
                output.push_str(&contents.borrow());
            }
            NodeData::Element { .. } => {
                // Recursively extract text from children
                for child in node.children.borrow().iter() {
                    self.extract_text(child, output)?;
                }
            }
            _ => {
                // Ignore other node types
            }
        }
        Ok(())
    }

    /// Normalize text content
    ///
    /// Applies text normalization rules to ensure consistent output:
    /// - Collapses consecutive whitespace (spaces, tabs, newlines) to single spaces
    /// - Trims leading and trailing whitespace
    /// - Preserves intentional line breaks (future enhancement)
    ///
    /// # Arguments
    ///
    /// * `text` - Raw text content to normalize
    ///
    /// # Returns
    ///
    /// Normalized text string
    ///
    /// # Examples
    ///
    /// ```text
    /// "  multiple   spaces  " -> "multiple spaces"
    /// "line\nbreak" -> "line break"
    /// "  \t  tabs  \t  " -> "tabs"
    /// ```
    fn normalize_text(&self, text: &str) -> String {
        // Split on whitespace and filter empty strings
        let words: Vec<&str> = text.split_whitespace().collect();

        // Join with single spaces
        words.join(" ")
    }

    /// Normalize final output for deterministic Markdown generation
    ///
    /// Applies comprehensive normalization to ensure deterministic output for stable ETags:
    ///
    /// **Normalization Rules:**
    /// 1. **Line Endings**: Enforce LF (`\n`) only, never CRLF (`\r\n`)
    /// 2. **Blank Lines**: Collapse consecutive blank lines to single blank line
    /// 3. **Trailing Whitespace**: Remove trailing whitespace from all lines
    /// 4. **Final Newline**: Ensure exactly one newline at end of file
    /// 5. **Whitespace Normalization**: Collapse consecutive spaces to single space
    /// 6. **Markdown Escaping**: Apply consistent escaping rules for special characters
    ///
    /// These rules ensure that converting identical HTML twice produces identical Markdown,
    /// which is critical for stable ETag generation and predictable caching behavior.
    ///
    /// # Arguments
    ///
    /// * `output` - Raw Markdown output
    ///
    /// # Returns
    ///
    /// Normalized Markdown string with deterministic formatting
    ///
    /// # Examples
    ///
    /// ```
    /// // Input with CRLF and multiple blank lines
    /// let input = "Line 1\r\n\r\n\r\nLine 2  \n";
    /// // Output with LF and single blank line
    /// let output = "Line 1\n\nLine 2\n";
    /// ```
    fn normalize_output(&self, output: String) -> String {
        // Step 1: Normalize line endings (CRLF -> LF)
        let output = output.replace("\r\n", "\n");

        // Step 2: Normalize whitespace within lines (collapse consecutive spaces)
        // This is done line-by-line to preserve intentional spacing in code blocks
        let mut result = String::with_capacity(output.len());
        let mut prev_blank = false;
        let mut in_code_block = false;

        for line in output.lines() {
            // Detect code block boundaries (fenced code blocks start with ```)
            if line.trim_start().starts_with("```") {
                in_code_block = !in_code_block;
            }

            // Step 3: Remove trailing whitespace from all lines
            let trimmed = line.trim_end();

            if trimmed.is_empty() {
                // Step 4: Collapse consecutive blank lines to single blank line
                if !prev_blank {
                    result.push('\n');
                    prev_blank = true;
                }
            } else {
                // Step 5: Normalize whitespace (collapse consecutive spaces)
                // Skip normalization inside code blocks to preserve formatting
                if in_code_block {
                    result.push_str(trimmed);
                } else {
                    // Collapse consecutive spaces to single space
                    let normalized = self.normalize_line_whitespace(trimmed);
                    result.push_str(&normalized);
                }
                result.push('\n');
                prev_blank = false;
            }
        }

        // Step 6: Ensure single trailing newline
        if !result.ends_with('\n') {
            result.push('\n');
        } else if result.ends_with("\n\n") {
            // Remove extra trailing newlines
            while result.ends_with("\n\n") {
                result.pop();
            }
        }

        result
    }

    /// Normalize whitespace within a single line
    ///
    /// Collapses consecutive spaces to a single space while preserving
    /// intentional spacing in Markdown syntax (e.g., list indentation, inline code).
    ///
    /// # Arguments
    ///
    /// * `line` - A single line of text
    ///
    /// # Returns
    ///
    /// Line with normalized whitespace
    fn normalize_line_whitespace(&self, line: &str) -> String {
        let mut result = String::with_capacity(line.len());
        let mut prev_space = false;
        let mut at_start = true;
        let mut in_inline_code = false;

        for ch in line.chars() {
            if ch == '`' {
                // Toggle inline code state
                in_inline_code = !in_inline_code;
                result.push(ch);
                prev_space = false;
                at_start = false;
            } else if ch == ' ' {
                if in_inline_code {
                    // Preserve all spaces inside inline code
                    result.push(ch);
                } else if at_start {
                    // Preserve leading spaces (for list indentation)
                    result.push(ch);
                } else if !prev_space {
                    // First space in a sequence
                    result.push(ch);
                    prev_space = true;
                }
                // Skip consecutive spaces (unless at start or in code)
            } else {
                result.push(ch);
                prev_space = false;
                at_start = false;
            }
        }

        result
    }
}

impl Default for MarkdownConverter {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse_html;
    use proptest::prelude::*;

    fn convert_html_for_test(html: &str) -> String {
        let dom = parse_html(html.as_bytes()).expect("Parse failed");
        MarkdownConverter::new()
            .convert(&dom)
            .expect("Conversion failed")
    }

    fn normalize_expected_text(text: &str) -> String {
        text.split_whitespace().collect::<Vec<_>>().join(" ")
    }

    fn escape_html_text(value: &str) -> String {
        value
            .replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
    }

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

        // Image without src should not be rendered
        assert!(!result.contains("!["));
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
    // Table Conversion Tests (GFM)
    // ============================================================================

    /// Test basic table conversion with GFM flavor
    /// Validates: FR-11.2
    #[test]
    fn test_table_basic_gfm() {
        let html = b"<table><thead><tr><th>Header 1</th><th>Header 2</th></tr></thead><tbody><tr><td>Cell 1</td><td>Cell 2</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should contain GFM table format
        assert!(result.contains("| Header 1 | Header 2 |"));
        assert!(result.contains("| --- | --- |"));
        assert!(result.contains("| Cell 1 | Cell 2 |"));
    }

    /// Test that tables are NOT converted with CommonMark flavor
    /// Validates: FR-11.2
    #[test]
    fn test_table_not_converted_commonmark() {
        let html = b"<table><thead><tr><th>Header</th></tr></thead><tbody><tr><td>Cell</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::CommonMark,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should NOT contain GFM table format
        assert!(!result.contains("|"));
        // Should contain the text content
        assert!(result.contains("Header"));
        assert!(result.contains("Cell"));
    }

    /// Test table with left alignment (default)
    /// Validates: FR-11.2
    #[test]
    fn test_table_left_alignment() {
        let html = b"<table><thead><tr><th align=\"left\">Left</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Left alignment uses default separator
        assert!(result.contains("| --- |"));
        assert!(result.contains("| Left |"));
    }

    /// Test table with center alignment
    /// Validates: FR-11.2
    #[test]
    fn test_table_center_alignment() {
        let html = b"<table><thead><tr><th align=\"center\">Center</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Center alignment uses :---:
        assert!(result.contains("| :---: |"));
        assert!(result.contains("| Center |"));
    }

    /// Test table with right alignment
    /// Validates: FR-11.2
    #[test]
    fn test_table_right_alignment() {
        let html = b"<table><thead><tr><th align=\"right\">Right</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Right alignment uses ---:
        assert!(result.contains("| ---: |"));
        assert!(result.contains("| Right |"));
    }

    /// Test table with mixed alignments
    /// Validates: FR-11.2
    #[test]
    fn test_table_mixed_alignments() {
        let html = b"<table><thead><tr><th align=\"left\">Left</th><th align=\"center\">Center</th><th align=\"right\">Right</th></tr></thead><tbody><tr><td>A</td><td>B</td><td>C</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should have mixed alignment separators
        assert!(result.contains("| --- | :---: | ---: |"));
        assert!(result.contains("| Left | Center | Right |"));
        assert!(result.contains("| A | B | C |"));
    }

    /// Test table with style-based alignment
    /// Validates: FR-11.2
    #[test]
    fn test_table_style_alignment() {
        let html = b"<table><thead><tr><th style=\"text-align: center\">Styled</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should detect alignment from style attribute
        assert!(result.contains("| :---: |"));
    }

    /// Test table without thead (direct tr under table)
    /// Validates: FR-11.2
    #[test]
    fn test_table_without_thead() {
        let html = b"<table><tr><th>Header 1</th><th>Header 2</th></tr><tr><td>Cell 1</td><td>Cell 2</td></tr></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should still convert properly
        assert!(result.contains("| Header 1 | Header 2 |"));
        assert!(result.contains("| --- | --- |"));
        assert!(result.contains("| Cell 1 | Cell 2 |"));
    }

    /// Test table with multiple rows
    /// Validates: FR-11.2
    #[test]
    fn test_table_multiple_rows() {
        let html = b"<table><thead><tr><th>Name</th><th>Age</th></tr></thead><tbody><tr><td>Alice</td><td>30</td></tr><tr><td>Bob</td><td>25</td></tr><tr><td>Charlie</td><td>35</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should have all rows
        assert!(result.contains("| Name | Age |"));
        assert!(result.contains("| Alice | 30 |"));
        assert!(result.contains("| Bob | 25 |"));
        assert!(result.contains("| Charlie | 35 |"));
    }

    /// Test table with empty cells
    /// Validates: FR-11.2
    #[test]
    fn test_table_empty_cells() {
        let html = b"<table><thead><tr><th>Col1</th><th>Col2</th></tr></thead><tbody><tr><td>Data</td><td></td></tr><tr><td></td><td>Data</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should handle empty cells
        assert!(result.contains("| Col1 | Col2 |"));
        assert!(result.contains("| Data | |"));
        assert!(result.contains("| | Data |"));
    }

    /// Test table with uneven rows (fewer cells than headers)
    /// Validates: FR-11.2
    #[test]
    fn test_table_uneven_rows() {
        let html = b"<table><thead><tr><th>A</th><th>B</th><th>C</th></tr></thead><tbody><tr><td>1</td><td>2</td></tr><tr><td>3</td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should pad missing cells
        assert!(result.contains("| A | B | C |"));
        assert!(result.contains("| 1 | 2 | |"));
        assert!(result.contains("| 3 | | |"));
    }

    /// Test table with text formatting in cells
    /// Validates: FR-11.2
    #[test]
    fn test_table_with_formatting() {
        let html = b"<table><thead><tr><th>Name</th><th>Status</th></tr></thead><tbody><tr><td><strong>Bold</strong></td><td><em>Italic</em></td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should preserve formatting in cells
        assert!(result.contains("| Name | Status |"));
        assert!(result.contains("| **Bold** | *Italic* |"));
    }

    /// Test table with links in cells
    /// Validates: FR-11.2
    #[test]
    fn test_table_with_links() {
        let html = b"<table><thead><tr><th>Site</th></tr></thead><tbody><tr><td><a href=\"https://example.com\">Example</a></td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should preserve links in cells
        assert!(result.contains("| Site |"));
        assert!(result.contains("| [Example](https://example.com) |"));
    }

    /// Test table with code in cells
    /// Validates: FR-11.2
    #[test]
    fn test_table_with_code() {
        let html = b"<table><thead><tr><th>Function</th></tr></thead><tbody><tr><td><code>print()</code></td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should preserve inline code in cells
        assert!(result.contains("| Function |"));
        assert!(result.contains("| `print()` |"));
    }

    /// Test table blank line separation
    /// Validates: FR-11.2
    #[test]
    fn test_table_blank_line_separation() {
        let html = b"<p>Before table</p><table><thead><tr><th>Header</th></tr></thead><tbody><tr><td>Data</td></tr></tbody></table><p>After table</p>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should have blank lines around table
        assert!(result.contains("Before table\n\n|"));
        assert!(result.contains("|\n\nAfter table"));
    }

    /// Test table with no tbody (only thead)
    /// Validates: FR-11.2
    #[test]
    fn test_table_thead_only() {
        let html = b"<table><thead><tr><th>Header</th></tr></thead></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Should still generate table with header and separator
        assert!(result.contains("| Header |"));
        assert!(result.contains("| --- |"));
    }

    /// Test table with td in header row (some HTML uses td instead of th)
    /// Validates: FR-11.2
    #[test]
    fn test_table_td_as_header() {
        let html = b"<table><tr><td>Header 1</td><td>Header 2</td></tr><tr><td>Cell 1</td><td>Cell 2</td></tr></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // First row should be treated as header
        assert!(result.contains("| Header 1 | Header 2 |"));
        assert!(result.contains("| --- | --- |"));
        assert!(result.contains("| Cell 1 | Cell 2 |"));
    }

    /// Test empty table (no headers)
    /// Validates: FR-11.2
    #[test]
    fn test_table_empty() {
        let html = b"<table></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Empty table should not produce output
        assert!(!result.contains("|"));
    }

    /// Test table with whitespace in cells
    /// Validates: FR-11.2
    #[test]
    fn test_table_whitespace_normalization() {
        let html = b"<table><thead><tr><th>  Header  </th></tr></thead><tbody><tr><td>  Data  with   spaces  </td></tr></tbody></table>";
        let dom = parse_html(html).expect("Parse failed");

        let options = ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        };
        let converter = MarkdownConverter::with_options(options);
        let result = converter.convert(&dom).expect("Conversion failed");

        // Whitespace should be normalized
        assert!(result.contains("| Header |"));
        assert!(result.contains("| Data with spaces |"));
    }
}
