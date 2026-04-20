//! YAML front matter generation for Markdown output.
//!
//! When both `include_front_matter` and `extract_metadata` are enabled in
//! [`ConversionOptions`], this module prepends a YAML front matter block
//! to the Markdown output. The front matter is enclosed in `---` delimiters
//! and contains metadata extracted from the HTML document (title, description,
//! canonical URL, Open Graph tags, etc.).
//!
//! # Example Output
//!
//! ```yaml
//! ---
//! title: "Example Page"
//! description: "A sample document"
//! url: "https://example.com/page"
//! og_image: "https://example.com/image.png"
//! ---
//! ```
//!
//! # YAML Safety
//!
//! String values are double-quoted and escaped to prevent YAML injection.
//! Only fields with non-empty values are included. The front matter block
//! is always terminated with a trailing `---` followed by a blank line to
//! ensure clean separation from the Markdown body.

use super::*;

impl MarkdownConverter {
    /// Optionally prepend YAML front matter extracted from DOM metadata.
    pub(super) fn maybe_write_front_matter_from_dom(
        &self,
        dom: &markup5ever_rcdom::RcDom,
        output: &mut String,
    ) -> Result<(), ConversionError> {
        if !(self.options.include_front_matter && self.options.extract_metadata) {
            return Ok(());
        }

        let extractor = crate::metadata::MetadataExtractor::new(
            self.options.base_url.clone(),
            self.options.resolve_relative_urls,
        );

        if let Ok(metadata) = extractor.extract(dom) {
            self.write_front_matter(output, &metadata)?;
        }

        Ok(())
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
    pub(super) fn write_front_matter(
        &self,
        output: &mut String,
        metadata: &crate::metadata::PageMetadata,
    ) -> Result<(), ConversionError> {
        output.push_str("---\n");

        if let Some(ref title) = metadata.title
            && !title.is_empty()
        {
            output.push_str("title: ");
            self.write_yaml_string(output, title);
            output.push('\n');
        }

        if let Some(ref url) = metadata.url
            && !url.is_empty()
        {
            output.push_str("url: ");
            self.write_yaml_string(output, url);
            output.push('\n');
        }

        if let Some(ref description) = metadata.description
            && !description.is_empty()
        {
            output.push_str("description: ");
            self.write_yaml_string(output, description);
            output.push('\n');
        }

        if let Some(ref image) = metadata.image
            && !image.is_empty()
        {
            output.push_str("image: ");
            self.write_yaml_string(output, image);
            output.push('\n');
        }

        if let Some(ref author) = metadata.author
            && !author.is_empty()
        {
            output.push_str("author: ");
            self.write_yaml_string(output, author);
            output.push('\n');
        }

        if let Some(ref published) = metadata.published
            && !published.is_empty()
        {
            output.push_str("published: ");
            self.write_yaml_string(output, published);
            output.push('\n');
        }

        output.push_str("---\n\n");

        Ok(())
    }

    /// Write a YAML string value with proper escaping
    ///
    /// Escapes YAML special characters and wraps the value in double quotes.
    /// This ensures the value is properly interpreted by YAML parsers.
    pub(super) fn write_yaml_string(&self, output: &mut String, value: &str) {
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
    pub(super) fn has_body_content(&self, output: &str) -> bool {
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
}
