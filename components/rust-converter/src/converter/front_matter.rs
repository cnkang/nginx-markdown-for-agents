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
//! url: "https://example.com/page"
//! description: "A sample document"
//! image: "https://example.com/image.png"
//! ---
//! ```
//!
//! # YAML Safety
//!
//! String values are double-quoted and escaped to prevent YAML injection.
//! Only fields with non-empty values are included. The front matter block
//! is always terminated with a trailing `---` followed by a blank line to
//! ensure clean separation from the Markdown body.

use super::{BudgetedMarkdownWriter, ConversionContext, ConversionError, MarkdownConverter};

impl MarkdownConverter {
    /// Optionally prepend YAML front matter extracted from DOM metadata.
    pub(super) fn maybe_write_front_matter_from_dom(
        &self,
        dom: &markup5ever_rcdom::RcDom,
        output: &mut String,
        ctx: &mut ConversionContext,
    ) -> Result<(), ConversionError> {
        if !(self.options.include_front_matter && self.options.extract_metadata) {
            return Ok(());
        }

        let extractor = crate::metadata::MetadataExtractor::new(
            self.options.base_url.clone(),
            self.options.resolve_relative_urls,
        );

        let metadata = extractor.extract_with_context(dom, ctx)?;
        let mut writer = ctx.budgeted_writer(output);
        self.write_front_matter(&mut writer, &metadata)?;

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
        writer: &mut BudgetedMarkdownWriter<'_>,
        metadata: &crate::metadata::PageMetadata,
    ) -> Result<(), ConversionError> {
        writer.push_str("---\n")?;

        self.write_optional_yaml_field(writer, "title", metadata.title.as_deref())?;
        self.write_optional_yaml_field(writer, "url", metadata.url.as_deref())?;
        self.write_optional_yaml_field(writer, "description", metadata.description.as_deref())?;
        self.write_optional_yaml_field(writer, "image", metadata.image.as_deref())?;
        self.write_optional_yaml_field(writer, "author", metadata.author.as_deref())?;
        self.write_optional_yaml_field(writer, "published", metadata.published.as_deref())?;

        writer.push_str("---\n\n")?;

        Ok(())
    }

    fn write_optional_yaml_field(
        &self,
        writer: &mut BudgetedMarkdownWriter<'_>,
        key: &str,
        value: Option<&str>,
    ) -> Result<(), ConversionError> {
        let Some(value) = value.filter(|value| !value.is_empty()) else {
            return Ok(());
        };

        writer.push_str(key)?;
        writer.push_str(": ")?;
        self.write_yaml_string(writer, value)?;
        writer.push('\n')?;
        Ok(())
    }

    /// Write a YAML string value with proper escaping
    ///
    /// Escapes YAML special characters and wraps the value in double quotes.
    /// This ensures the value is properly interpreted by YAML parsers.
    pub(super) fn write_yaml_string(
        &self,
        writer: &mut BudgetedMarkdownWriter<'_>,
        value: &str,
    ) -> Result<(), ConversionError> {
        writer.push('"')?;
        for ch in value.chars() {
            match ch {
                '"' => writer.push_str("\\\"")?,
                '\\' => writer.push_str("\\\\")?,
                '\n' => writer.push_str("\\n")?,
                '\r' => writer.push_str("\\r")?,
                '\t' => writer.push_str("\\t")?,
                // Escape remaining C0/C1 control characters (U+0000..U+001F,
                // U+007F..U+009F) as \uXXXX so the generated YAML stays
                // parseable (review LOW-1).
                ch if ch.is_control() => {
                    let code = ch as u32;
                    let mut escaped = *b"\\u0000";
                    for (index, digit) in escaped[2..].iter_mut().enumerate() {
                        let shift = 12 - index * 4;
                        *digit = b"0123456789abcdef"[((code >> shift) & 0xf) as usize];
                    }
                    writer.push_str(std::str::from_utf8(&escaped).expect("ASCII YAML escape"))?;
                }
                _ => writer.push(ch)?,
            }
        }
        writer.push('"')?;
        Ok(())
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
