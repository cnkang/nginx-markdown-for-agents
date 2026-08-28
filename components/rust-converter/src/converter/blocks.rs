//! Block-level element handlers for the Markdown converter.
//!
//! This module contains methods on [`MarkdownConverter`] that handle block-level
//! HTML elements during DOM-to-Markdown traversal. Block elements produce
//! structural Markdown constructs (headings, paragraphs, lists, code blocks,
//! blockquotes, horizontal rules) and typically emit surrounding blank lines
//! to maintain proper Markdown paragraph separation.
//!
//! # Element Coverage
//!
//! | HTML Element | Markdown Output | Handler |
//! |-------------|----------------|---------|
//! | `<h1>`–`<h6>` | `#`–`######` | `handle_heading` |
//! | `<p>` | plain text + blank line | `handle_paragraph` |
//! | `<ul>`, `<ol>` | `- ` / `1. ` lists | `handle_list` |
//! | `<li>` | list item with indentation | `format_list_item_lines` |
//! | `<pre>`, `<code>` | fenced code block | `handle_preformatted` |
//! | `<blockquote>` | `> ` prefix | `handle_blockquote` |
//! | `<hr>` | `---` | `handle_horizontal_rule` |
//! | `<div>`, `<section>`, etc. | transparent container | traversal passthrough |
//!
//! # List Formatting
//!
//! Nested and multi-line list items require careful indentation management.
//! [`format_list_item_lines`] handles continuation-line indentation so that
//! wrapped content aligns with the list marker, and nested sub-lists are
//! indented by 2 spaces per depth level (CommonMark convention).
//!
//! # Code Block Fencing
//!
//! [`choose_code_fence`] selects a backtick fence length that is strictly
//! longer than any backtick run in the code payload, preventing premature
//! fence termination. [`longest_backtick_run`] is the helper that scans the
//! payload for the longest contiguous backtick sequence.

use super::{
    Attribute, ConversionContext, ConversionError, Handle, MarkdownConverter, NodeData, Ref,
};

impl MarkdownConverter {
    /// Emit one list item while preserving multi-line/nested-item indentation.
    ///
    /// Writes the list marker (`- ` or `1. `) prefixed by `depth * 2` spaces
    /// of indentation. Continuation lines (lines after the first) are indented
    /// to align with the marker's content column so wrapped text and nested
    /// sub-lists render correctly in CommonMark.
    ///
    /// If the first line of content itself starts with a list marker (e.g. the
    /// child was a nested `<ul>` that was already converted), the marker is
    /// emitted on its own line and the content follows with appropriate
    /// indentation to avoid double-marking.
    fn format_list_item_lines(
        &self,
        output: &mut String,
        content: &str,
        depth: usize,
        ordered: bool,
    ) {
        let base_indent = "  ".repeat(depth);
        let marker = if ordered { "1. " } else { "- " };
        // Continuation indent aligns with content after the marker (e.g. "- " → 2 chars).
        let continuation_indent = format!("{base_indent}{}", " ".repeat(marker.len()));

        let trimmed = content.trim_matches('\n');
        if trimmed.is_empty() {
            output.push_str(&base_indent);
            output.push_str(marker);
            output.push('\n');
            return;
        }

        for (index, line) in trimmed.lines().enumerate() {
            if index == 0 {
                output.push_str(&base_indent);
                output.push_str(marker);
                // If the content already starts with a list marker (from a
                // nested list that was converted earlier), emit a blank line
                // after our marker and then the content with continuation
                // indentation to avoid producing a malformed double-marker.
                if Self::list_line_is_nested(line) {
                    output.push('\n');
                    Self::append_list_item_line(output, line, &base_indent, &continuation_indent);
                    continue;
                }
            } else if !line.is_empty() && !Self::list_line_is_indented(line, &base_indent) {
                // Indent continuation lines unless they already carry
                // indentation (from pre-formatted or nested content).
                output.push_str(&continuation_indent);
            }

            output.push_str(line);
            output.push('\n');
        }
    }

    /// Append one list-item payload line, aligning it with the item's own
    /// indentation.
    ///
    /// A line that already starts with whitespace is not simply dropped: that
    /// whitespace usually comes from a nested list rendered at a deeper depth
    /// inside this item's content.  Re-indent it relative to this item so the
    /// payload stays attached to the item marker (a bare early return silently
    /// discarded the whole first-child nested list).  Empty lines still emit
    /// nothing.
    fn append_list_item_line(
        output: &mut String,
        line: &str,
        base_indent: &str,
        continuation_indent: &str,
    ) {
        if line.is_empty() {
            return;
        }
        if Self::list_line_is_indented(line, base_indent) {
            // Strip exactly the nested render's leading whitespace, then
            // re-anchor the line under this item's continuation indent.
            let stripped = line.trim_start_matches([' ', '\t']);
            if stripped.is_empty() {
                return;
            }
            output.push_str(continuation_indent);
            output.push_str(stripped);
        } else {
            output.push_str(continuation_indent);
            output.push_str(line);
        }
        output.push('\n');
    }

    /// Return the longest contiguous run of backticks in `content`.
    pub(super) fn longest_backtick_run(&self, content: &str) -> usize {
        let mut longest = 0;
        let mut current = 0;

        for ch in content.chars() {
            if ch == '`' {
                current += 1;
                longest = longest.max(current);
            } else {
                current = 0;
            }
        }

        longest
    }

    /// Choose a fenced-code delimiter that cannot collide with the payload.
    ///
    /// Markdown requires the outer fence to be longer than any backtick run
    /// contained inside the code block.
    pub(super) fn choose_code_fence(&self, content: &str) -> String {
        let longest_backticks = self.longest_backtick_run(content);
        if longest_backticks == 0 {
            "```".to_string()
        } else {
            "`".repeat(longest_backticks.max(3) + 1)
        }
    }

    /// Handle heading elements (h1-h6) with optional timeout context.
    pub(super) fn handle_heading_with_context(
        &self,
        node: &Handle,
        level: usize,
        output: &mut String,
        depth: usize,
        mut ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        for _ in 0..level {
            output.push('#');
        }
        output.push(' ');

        let start_len = output.len();
        for child in node.children.borrow().iter() {
            self.traverse_node_optional(child, output, depth + 1, ctx.as_deref_mut())?;
        }

        let heading_content = output[start_len..].to_string();
        let normalized = self.normalize_text(&heading_content);
        output.truncate(start_len);
        output.push_str(&normalized);
        output.push_str("\n\n");

        Ok(())
    }

    /// Handle paragraph elements with optional timeout context.
    pub(super) fn handle_paragraph_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        mut ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        let start_len = output.len();
        for child in node.children.borrow().iter() {
            self.traverse_node_optional(child, output, depth + 1, ctx.as_deref_mut())?;
        }

        if output.len() > start_len {
            output.push_str("\n\n");
        }

        Ok(())
    }

    /// Compute the exact byte length that `format_list_item_lines` will
    /// append for `content`, including the list marker, base indentation,
    /// continuation indentation, and trailing newlines.  Mirrors the
    /// rendering logic so callers can validate the output budget with
    /// checked arithmetic before mutating the output buffer.
    fn list_line_is_indented(line: &str, base_indent: &str) -> bool {
        (!base_indent.is_empty() && line.starts_with(base_indent))
            || line.starts_with(' ')
            || line.starts_with('\t')
    }

    fn list_line_continuation_indent(
        line: &str,
        _base_indent: &str,
        continuation_indent_len: usize,
    ) -> usize {
        // Mirrors append_list_item_line: an indented line is stripped of its
        // leading whitespace and re-anchored under the continuation indent,
        // so the rendered length always includes that indent (never 0 for a
        // non-empty line).
        if line.is_empty() {
            0
        } else {
            continuation_indent_len
        }
    }

    fn list_line_is_nested(line: &str) -> bool {
        let trimmed = line.trim_start();
        trimmed.starts_with("- ") || trimmed.starts_with("* ") || trimmed.starts_with("1. ")
    }

    fn format_list_item_rendered_len(content: &str, depth: usize, ordered: bool) -> usize {
        let base_indent_len = depth * 2;
        let marker_len = if ordered { 3 } else { 2 };
        let continuation_indent_len = base_indent_len + marker_len;
        let base_indent = "  ".repeat(depth);

        let trimmed = content.trim_matches('\n');
        if trimmed.is_empty() {
            return base_indent_len + marker_len + 1;
        }

        let mut total = 0usize;
        for (index, line) in trimmed.lines().enumerate() {
            if index == 0 {
                total += base_indent_len + marker_len;
                if Self::list_line_is_nested(line) {
                    // Blank line after our marker, then the content with
                    // continuation indentation unless already indented.
                    total += 1;
                    if !line.is_empty() {
                        total += Self::list_line_continuation_indent(
                            line,
                            &base_indent,
                            continuation_indent_len,
                        );
                        total += line.len() + 1;
                    }
                    continue;
                }
            } else {
                total += Self::list_line_continuation_indent(
                    line,
                    &base_indent,
                    continuation_indent_len,
                );
            }

            total += line.len() + 1;
        }

        total
    }

    /// Estimates the maximum rendered length of a list item before its content is fully formatted.
    ///
    /// The estimate accounts for the item's depth, marker style, rendered line count, and
    /// per-line formatting overhead. Returns a memory-limit error if the estimate overflows.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let upper_bound = Self::format_list_item_upper_bound(5, 1, 1, false).unwrap();
    /// assert_eq!(upper_bound, 25);
    /// ```
    fn format_list_item_upper_bound(
        content_len: usize,
        newline_count: usize,
        depth: usize,
        ordered: bool,
    ) -> Result<usize, ConversionError> {
        let base_indent_len = depth.checked_mul(2).ok_or_else(|| {
            ConversionError::MemoryLimit("generated Markdown list indentation overflow".to_string())
        })?;
        let marker_len = if ordered { 3 } else { 2 };
        let prefix_len = base_indent_len.checked_add(marker_len).ok_or_else(|| {
            ConversionError::MemoryLimit(
                "generated Markdown list marker length overflow".to_string(),
            )
        })?;
        let line_count = newline_count.checked_add(1).ok_or_else(|| {
            ConversionError::MemoryLimit("generated Markdown list line count overflow".to_string())
        })?;
        let per_line_overhead = prefix_len
            .checked_mul(2)
            .and_then(|value| value.checked_add(2))
            .ok_or_else(|| {
                ConversionError::MemoryLimit(
                    "generated Markdown list overhead overflow".to_string(),
                )
            })?;

        content_len
            .checked_add(line_count.checked_mul(per_line_overhead).ok_or_else(|| {
                ConversionError::MemoryLimit(
                    "generated Markdown list output length overflow".to_string(),
                )
            })?)
            .ok_or_else(|| {
                ConversionError::MemoryLimit(
                    "generated Markdown list output length overflow".to_string(),
                )
            })
    }

    /// Handle list elements (ul/ol) with optional timeout context.
    pub(super) fn handle_list_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ordered: bool,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        let mut ctx = ctx;
        if let Some(context) = ctx.as_deref_mut() {
            context.increment_and_check()?;
        }

        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        for child in node.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "li"
            {
                self.handle_list_item_with_marker(
                    child,
                    output,
                    depth,
                    ordered,
                    ctx.as_deref_mut(),
                )?;
            }
        }

        if !output.ends_with("\n\n") {
            output.push('\n');
        }

        if let Some(context) = ctx {
            context.check_output_budget(output.len())?;
        }

        Ok(())
    }

    /// Renders a list item using an unordered-list marker and the specified nesting depth.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// converter.handle_list_item_with_context(&node, &mut output, 0, None)?;
    /// ```
    ///
    /// # Errors
    ///
    /// Returns a [`ConversionError`] if rendering the list item fails.
    pub(super) fn handle_list_item_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        self.handle_list_item_with_marker(node, output, depth, false, ctx)
    }

    /// Renders a list item's child content, including nested ordered and unordered lists.
    ///
    /// Nested lists are rendered at the next indentation depth, while other children
    /// are traversed at that depth.
    ///
    /// # Examples
    ///
    /// ```rust,ignore
    /// let mut output = String::new();
    /// let mut context = None;
    ///
    /// converter
    ///     .render_list_item_child(&child, &mut output, depth, &mut context)
    ///     .unwrap();
    /// assert!(!output.is_empty());
    /// ```
    fn render_list_item_child(
        &self,
        child: &Handle,
        item_output: &mut String,
        depth: usize,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        match child.data {
            NodeData::Element { ref name, .. } => {
                let tag_name = name.local.as_ref();
                if tag_name == "ul" || tag_name == "ol" {
                    if !item_output.is_empty() && !item_output.ends_with('\n') {
                        item_output.push('\n');
                    }
                    self.handle_list_with_context(
                        child,
                        item_output,
                        depth + 1,
                        tag_name == "ol",
                        ctx.as_deref_mut(),
                    )?;
                } else {
                    self.traverse_node_optional(child, item_output, depth + 1, ctx.as_deref_mut())?;
                }
            }
            _ => {
                self.traverse_node_optional(child, item_output, depth + 1, ctx.as_deref_mut())?;
            }
        }
        Ok(())
    }

    /// Renders a list item using an ordered or unordered Markdown marker.
    ///
    /// Nested list content is rendered at the specified depth, and the conversion
    /// context is used to enforce output limits when provided.
    ///
    /// # Examples
    ///
    /// ```rust,ignore
    /// converter.handle_list_item_with_marker(&node, &mut output, 0, false, None)?;
    /// ```
    pub(super) fn handle_list_item_with_marker(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ordered: bool,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut item_output = String::new();
        let mut newline_count = 0usize;
        let mut ctx = ctx;
        for child in node.children.borrow().iter() {
            let child_output_start = item_output.len();

            self.render_list_item_child(child, &mut item_output, depth, &mut ctx)?;

            let appended_newlines = item_output[child_output_start..]
                .bytes()
                .filter(|byte| *byte == b'\n')
                .count();
            newline_count = newline_count
                .checked_add(appended_newlines)
                .ok_or_else(|| {
                    ConversionError::MemoryLimit(
                        "generated Markdown list newline count overflow".to_string(),
                    )
                })?;

            if let Some(context) = ctx.as_deref_mut() {
                let upper_bound = Self::format_list_item_upper_bound(
                    item_output.len(),
                    newline_count,
                    depth,
                    ordered,
                )?;
                let projected_len = output.len().checked_add(upper_bound).ok_or_else(|| {
                    ConversionError::MemoryLimit(
                        "generated Markdown list output length overflow".to_string(),
                    )
                })?;
                context.check_output_budget(projected_len)?;
            }
        }

        if let Some(context) = ctx.as_deref_mut() {
            let rendered_len = Self::format_list_item_rendered_len(&item_output, depth, ordered);
            let projected_len = output.len().checked_add(rendered_len).ok_or_else(|| {
                ConversionError::MemoryLimit(
                    "generated Markdown list output length overflow".to_string(),
                )
            })?;
            context.check_output_budget(projected_len)?;
        }

        self.format_list_item_lines(output, &item_output, depth, ordered);

        if let Some(context) = ctx {
            context.check_output_budget(output.len())?;
        }

        Ok(())
    }

    /// Handle code block elements (pre/code) with optional timeout context.
    pub(super) fn handle_code_block_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        let language = self.extract_code_language(node);

        let mut code_content = String::new();
        self.extract_code_content(node, &mut code_content, 0, ctx)?;
        let fence = self.choose_code_fence(&code_content);

        output.push_str(&fence);
        if !language.is_empty() {
            output.push_str(&language);
        }
        output.push('\n');

        output.push_str(&code_content);

        if !output.ends_with('\n') {
            output.push('\n');
        }
        output.push_str(&fence);
        output.push('\n');
        output.push('\n');

        Ok(())
    }

    fn extract_code_language(&self, node: &Handle) -> String {
        for child in node.children.borrow().iter() {
            if let NodeData::Element {
                ref name,
                ref attrs,
                ..
            } = child.data
                && name.local.as_ref() == "code"
                && let Some(lang) = self.find_language_from_attrs(&attrs.borrow())
            {
                return lang;
            }
        }
        String::new()
    }

    fn find_language_from_attrs(&self, attrs: &Ref<Vec<Attribute>>) -> Option<String> {
        for attr in attrs.iter() {
            if attr.name.local.as_ref() == "class"
                && let Some(lang) = self.find_language_from_classes(&attr.value)
            {
                return Some(lang);
            }
        }
        None
    }

    fn find_language_from_classes(&self, class_value: &str) -> Option<String> {
        for class in class_value.split_whitespace() {
            let candidate = class
                .strip_prefix("language-")
                .or_else(|| class.strip_prefix("lang-"));
            if let Some(lang) = candidate
                && let Some(valid) = Self::safe_code_language(lang)
            {
                return Some(valid);
            }
        }
        None
    }

    /// Accept common code-language identifiers without allowing characters
    /// that can alter the opening Markdown fence or inject a new line.
    fn safe_code_language(language: &str) -> Option<String> {
        if !language.is_empty()
            && language.bytes().all(|byte| {
                byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'+' | b'.' | b'#')
            })
        {
            return Some(language.to_string());
        }

        None
    }
}

#[cfg(test)]
mod tests {
    use super::MarkdownConverter;
    use crate::error::ConversionError;

    #[test]
    fn list_line_indent_detection_matches_rendering_contract() {
        // An empty base indent never matches by prefix; raw whitespace
        // prefixes still count as pre-indented content.
        assert!(MarkdownConverter::list_line_is_indented("  x", "  "));
        assert!(MarkdownConverter::list_line_is_indented("   x", "  "));
        assert!(!MarkdownConverter::list_line_is_indented("x", ""));
        assert!(MarkdownConverter::list_line_is_indented(" x", ""));
        assert!(MarkdownConverter::list_line_is_indented("\tx", ""));
        assert!(!MarkdownConverter::list_line_is_indented("x", "  "));
    }

    #[test]
    fn list_line_nested_marker_detection_covers_all_markers() {
        assert!(MarkdownConverter::list_line_is_nested("- item"));
        assert!(MarkdownConverter::list_line_is_nested("* item"));
        assert!(MarkdownConverter::list_line_is_nested("1. item"));
        assert!(MarkdownConverter::list_line_is_nested("  - deep"));
        assert!(!MarkdownConverter::list_line_is_nested("-emph-no-space"));
        assert!(!MarkdownConverter::list_line_is_nested("plain text"));
    }

    #[test]
    fn list_item_upper_bound_covers_line_shapes_and_depth() {
        assert_eq!(
            MarkdownConverter::format_list_item_upper_bound(0, 0, 0, false).unwrap(),
            6
        );
        assert_eq!(
            MarkdownConverter::format_list_item_upper_bound(5, 0, 0, false).unwrap(),
            11
        );
        assert_eq!(
            MarkdownConverter::format_list_item_upper_bound(5, 1, 0, false).unwrap(),
            17
        );
        assert_eq!(
            MarkdownConverter::format_list_item_upper_bound(8, 3, 2, true).unwrap(),
            72
        );
    }

    #[test]
    fn list_item_upper_bound_rejects_arithmetic_overflow() {
        assert!(matches!(
            MarkdownConverter::format_list_item_upper_bound(0, usize::MAX, 0, false),
            Err(ConversionError::MemoryLimit(_))
        ));
        assert!(matches!(
            MarkdownConverter::format_list_item_upper_bound(0, 0, usize::MAX, false),
            Err(ConversionError::MemoryLimit(_))
        ));
        assert!(matches!(
            MarkdownConverter::format_list_item_upper_bound(usize::MAX, 0, 0, false),
            Err(ConversionError::MemoryLimit(_))
        ));
    }
}
