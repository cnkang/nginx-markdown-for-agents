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
//! Code content is measured while it is still in the DOM, allowing the
//! handler to select a safe fence and stream the content directly into the
//! budgeted output writer without a second payload allocation.

use super::traversal::{
    append_char_with_context, append_repeated_char_with_context, append_str_with_context,
    with_reserved_working_set,
};
use super::{ConversionContext, ConversionError, Handle, MarkdownConverter, NodeData};
use html5ever::Attribute;

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
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let base_indent_len = depth.checked_mul(2).ok_or_else(|| {
            ConversionError::MemoryLimit("list indentation working-set overflow".into())
        })?;
        let marker_len: usize = if ordered { 3 } else { 2 };
        let temp_bytes = base_indent_len
            .checked_mul(2)
            .and_then(|value| {
                marker_len
                    .checked_mul(2)
                    .and_then(|mark| value.checked_add(mark))
            })
            .ok_or_else(|| {
                ConversionError::MemoryLimit("list indentation working-set overflow".into())
            })?;

        with_reserved_working_set(output, ctx, temp_bytes, |output, ctx| {
            let base_indent = "  ".repeat(depth);
            let marker = if ordered { "1. " } else { "- " };
            // Continuation indent aligns with content after the marker (e.g. "- " → 2 chars).
            let continuation_indent = format!("{base_indent}{}", " ".repeat(marker.len()));

            Self::write_list_item_lines(
                output,
                ctx,
                content,
                &base_indent,
                marker,
                &continuation_indent,
            )
        })
    }

    /// Write every payload line of a list item under its marker.
    fn write_list_item_lines(
        output: &mut String,
        ctx: &mut Option<&mut ConversionContext>,
        content: &str,
        base_indent: &str,
        marker: &str,
        continuation_indent: &str,
    ) -> Result<(), ConversionError> {
        let trimmed = content.trim_matches('\n');
        if trimmed.is_empty() {
            append_str_with_context(output, base_indent, ctx)?;
            append_str_with_context(output, marker, ctx)?;
            append_char_with_context(output, '\n', ctx)?;
            return Ok(());
        }

        for (index, line) in trimmed.lines().enumerate() {
            if index == 0 {
                if Self::write_list_item_first_line(
                    output,
                    ctx,
                    line,
                    base_indent,
                    marker,
                    continuation_indent,
                )? {
                    continue;
                }
            } else if !line.is_empty() && !Self::list_line_is_indented(line, base_indent) {
                // Indent continuation lines unless they already carry
                // indentation (from pre-formatted or nested content).
                append_str_with_context(output, continuation_indent, ctx)?;
            }

            append_str_with_context(output, line, ctx)?;
            append_char_with_context(output, '\n', ctx)?;
        }
        Ok(())
    }

    /// Write the marker and first payload line of a list item.
    ///
    /// Returns `true` when the line was fully emitted (nested-list case,
    /// where the content follows on its own line); `false` when the caller
    /// must emit the line itself after the marker.
    fn write_list_item_first_line(
        output: &mut String,
        ctx: &mut Option<&mut ConversionContext>,
        line: &str,
        base_indent: &str,
        marker: &str,
        continuation_indent: &str,
    ) -> Result<bool, ConversionError> {
        append_str_with_context(output, base_indent, ctx)?;
        append_str_with_context(output, marker, ctx)?;
        // If the content already starts with a list marker (from a
        // nested list that was converted earlier), emit a blank line
        // after our marker and then the content with continuation
        // indentation to avoid producing a malformed double-marker.
        if Self::list_line_is_nested(line) {
            append_char_with_context(output, '\n', ctx)?;
            Self::append_list_item_line(output, line, base_indent, continuation_indent, ctx)?;
            return Ok(true);
        }
        Ok(false)
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
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        if line.is_empty() {
            return Ok(());
        }
        if Self::list_line_is_indented(line, base_indent) {
            // Strip exactly the nested render's leading whitespace, then
            // re-anchor the line under this item's continuation indent.
            let stripped = line.trim_start_matches([' ', '\t']);
            if stripped.is_empty() {
                return Ok(());
            }
            append_str_with_context(output, continuation_indent, ctx)?;
            append_str_with_context(output, stripped, ctx)?;
        } else {
            append_str_with_context(output, continuation_indent, ctx)?;
            append_str_with_context(output, line, ctx)?;
        }
        append_char_with_context(output, '\n', ctx)
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
                append_char_with_context(output, '\n', &mut ctx)?;
            } else {
                append_str_with_context(output, "\n\n", &mut ctx)?;
            }
        }

        for _ in 0..level {
            append_char_with_context(output, '#', &mut ctx)?;
        }
        append_char_with_context(output, ' ', &mut ctx)?;

        let start_len = output.len();
        for child in node.children.borrow().iter() {
            self.traverse_node_optional(child, output, depth + 1, ctx.as_deref_mut())?;
        }

        let heading_len = output.len().saturating_sub(start_len);
        let temp_bytes = heading_len.checked_mul(2).ok_or_else(|| {
            ConversionError::MemoryLimit("heading working-set size overflow".into())
        })?;
        with_reserved_working_set(output, &mut ctx, temp_bytes, |output, ctx| {
            let heading_content = output[start_len..].to_string();
            let normalized = self.normalize_text(&heading_content);
            output.truncate(start_len);
            append_str_with_context(output, &normalized, ctx)?;
            append_str_with_context(output, "\n\n", ctx)
        })
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
                append_char_with_context(output, '\n', &mut ctx)?;
            } else {
                append_str_with_context(output, "\n\n", &mut ctx)?;
            }
        }

        let start_len = output.len();
        for child in node.children.borrow().iter() {
            self.traverse_node_optional(child, output, depth + 1, ctx.as_deref_mut())?;
        }

        if output.len() > start_len {
            append_str_with_context(output, "\n\n", &mut ctx)?;
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

    fn list_line_continuation_indent(line: &str, continuation_indent_len: usize) -> usize {
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
        let base_indent_len = depth.saturating_mul(2);
        let marker_len = if ordered { 3 } else { 2 };
        let continuation_indent_len = base_indent_len.saturating_add(marker_len);

        let trimmed = content.trim_matches('\n');
        if trimmed.is_empty() {
            return base_indent_len.saturating_add(marker_len).saturating_add(1);
        }

        let mut total = 0usize;
        for (index, line) in trimmed.lines().enumerate() {
            if index == 0 {
                total = total.saturating_add(base_indent_len.saturating_add(marker_len));
                if Self::list_line_is_nested(line) {
                    // Blank line after our marker, then the content with
                    // continuation indentation unless already indented.
                    total = total.saturating_add(1);
                    if !line.is_empty() {
                        total = total
                            .saturating_add(Self::list_line_continuation_indent(
                                line,
                                continuation_indent_len,
                            ))
                            .saturating_add(line.len())
                            .saturating_add(1);
                    }
                    continue;
                }
            } else {
                total = total.saturating_add(Self::list_line_continuation_indent(
                    line,
                    continuation_indent_len,
                ));
            }

            total = total.saturating_add(line.len()).saturating_add(1);
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
                append_char_with_context(output, '\n', &mut ctx)?;
            } else {
                append_str_with_context(output, "\n\n", &mut ctx)?;
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
            append_char_with_context(output, '\n', &mut ctx)?;
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
                        append_char_with_context(item_output, '\n', ctx)?;
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

    fn render_list_item_content(
        &self,
        node: &Handle,
        output: &str,
        depth: usize,
        ordered: bool,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(String, usize), ConversionError> {
        let mut item_output = String::new();
        let mut newline_count = 0usize;
        for child in node.children.borrow().iter() {
            let child_output_start = item_output.len();
            self.render_list_item_child(child, &mut item_output, depth, ctx)?;

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
        Ok((item_output, newline_count))
    }

    fn format_list_item_with_context(
        &self,
        output: &mut String,
        item_output: String,
        depth: usize,
        ordered: bool,
        ctx: &mut Option<&mut ConversionContext>,
        output_charge_released: &mut bool,
    ) -> Result<(), ConversionError> {
        let Some(context) = ctx.as_deref_mut() else {
            let result = self.format_list_item_lines(output, &item_output, depth, ordered, ctx);
            drop(item_output);
            return result;
        };

        let rendered_len = Self::format_list_item_rendered_len(&item_output, depth, ordered);
        let projected_len = output.len().checked_add(rendered_len).ok_or_else(|| {
            ConversionError::MemoryLimit(
                "generated Markdown list output length overflow".to_string(),
            )
        })?;
        context.check_output_budget(projected_len)?;

        let item_capacity = item_output.capacity();
        let output_capacity = output.capacity();
        context.reserve_working_set(item_capacity)?;
        context.release_working_set(output_capacity);
        *output_charge_released = true;

        let format_result = self.format_list_item_lines(output, &item_output, depth, ordered, ctx);
        drop(item_output);
        if let Some(context) = ctx.as_deref_mut() {
            context.release_working_set(item_capacity);
        }
        format_result
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
        let mut ctx = ctx;
        let output_capacity = output.capacity();
        let mut output_charge_active = false;
        if let Some(context) = ctx.as_deref_mut() {
            context.reserve_working_set(output_capacity)?;
            output_charge_active = true;
        }

        let mut output_charge_released = false;
        let result = (|| {
            let (item_output, _) =
                self.render_list_item_content(node, output, depth, ordered, &mut ctx)?;
            self.format_list_item_with_context(
                output,
                item_output,
                depth,
                ordered,
                &mut ctx,
                &mut output_charge_released,
            )?;

            if let Some(context) = ctx.as_deref_mut() {
                context.check_output_budget(output.len())?;
            }
            Ok(())
        })();

        if output_charge_active
            && !output_charge_released
            && let Some(context) = ctx
        {
            context.release_working_set(output_capacity);
        }
        result
    }

    /// Handle code block elements (pre/code) with optional timeout context.
    pub(super) fn handle_code_block_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                append_char_with_context(output, '\n', &mut ctx)?;
            } else {
                append_str_with_context(output, "\n\n", &mut ctx)?;
            }
        }

        // Measure the code payload before any output allocation.  The payload
        // is then streamed directly into the final budgeted writer, avoiding
        // an uncharged `String` that used to hold the entire code block.
        let stats = self.measure_code_content(node, 0, &mut ctx)?;
        let fence_len = if stats.max_backtick_run == 0 {
            3
        } else {
            stats
                .max_backtick_run
                .max(3)
                .checked_add(1)
                .ok_or_else(|| ConversionError::MemoryLimit("code fence length overflow".into()))?
        };

        append_repeated_char_with_context(output, '`', fence_len, &mut ctx)?;
        self.append_code_language(node, output, &mut ctx)?;
        append_char_with_context(output, '\n', &mut ctx)?;

        self.extract_code_content(node, output, 0, ctx.as_deref_mut())?;

        if !output.ends_with('\n') {
            append_char_with_context(output, '\n', &mut ctx)?;
        }
        append_repeated_char_with_context(output, '`', fence_len, &mut ctx)?;
        append_str_with_context(output, "\n\n", &mut ctx)
    }

    /// Extract a safe `language-*` / `lang-*` class value from the code
    /// element, appending it to the output and returning `true` on the
    /// first match (first-wins, mirroring the prior inline loop).
    fn append_safe_language_class(
        &self,
        attrs: &std::cell::RefCell<Vec<Attribute>>,
        output: &mut String,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<bool, ConversionError> {
        let attrs_borrowed = attrs.borrow();
        for attr in attrs_borrowed.iter() {
            if attr.name.local.as_ref() != "class" {
                continue;
            }
            for class in attr.value.split_whitespace() {
                let candidate = class
                    .strip_prefix("language-")
                    .or_else(|| class.strip_prefix("lang-"));
                if let Some(language) = candidate
                    && Self::is_safe_code_language(language)
                {
                    append_str_with_context(output, language, ctx)?;
                    return Ok(true);
                }
            }
        }
        Ok(false)
    }

    fn append_code_language(
        &self,
        node: &Handle,
        output: &mut String,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        for child in node.children.borrow().iter() {
            if let NodeData::Element {
                ref name,
                ref attrs,
                ..
            } = child.data
                && name.local.as_ref() == "code"
                && self.append_safe_language_class(attrs, output, ctx)?
            {
                break;
            }
        }
        Ok(())
    }

    /// Accept common code-language identifiers without allowing characters
    /// that can alter the opening Markdown fence or inject a new line.
    fn is_safe_code_language(language: &str) -> bool {
        if !language.is_empty()
            && language.bytes().all(|byte| {
                byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'+' | b'.' | b'#')
            })
        {
            return true;
        }

        false
    }
}

#[cfg(test)]
mod tests {
    use super::super::{ConversionContext, ConversionOptions, MarkdownFlavor};
    use super::MarkdownConverter;
    use crate::error::ConversionError;
    use crate::parser::parse_html;
    use std::time::Duration;

    fn convert_with_budget(
        converter: &MarkdownConverter,
        html: &str,
        budget: usize,
    ) -> (Result<String, ConversionError>, ConversionContext) {
        let dom = parse_html(html.as_bytes()).expect("test HTML should parse");
        let mut context = ConversionContext::with_output_budget(Duration::ZERO, budget);
        let result = converter.convert_with_context(&dom, &mut context);
        (result, context)
    }

    fn assert_memory_limit(result: Result<String, ConversionError>) {
        assert!(matches!(result, Err(ConversionError::MemoryLimit(_))));
    }

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

    #[test]
    fn large_code_block_is_rejected_before_payload_allocation() {
        let html = format!("<pre>{}</pre>", "x".repeat(16 * 1024));
        let (result, context) = convert_with_budget(&MarkdownConverter::new(), &html, 1024);

        assert_memory_limit(result);
        assert_eq!(context.working_set_bytes, 0);
    }

    #[test]
    fn large_inline_code_is_rejected_before_payload_allocation() {
        let html = format!("<p><code>{}</code></p>", "x".repeat(16 * 1024));
        let (result, context) = convert_with_budget(&MarkdownConverter::new(), &html, 1024);

        assert_memory_limit(result);
        assert_eq!(context.working_set_bytes, 0);
    }

    fn assert_large_prescan_times_out(html: String) {
        let dom = parse_html(html.as_bytes()).expect("test HTML should parse");
        let converter = MarkdownConverter::new();
        let mut context = ConversionContext::new(Duration::from_nanos(1));
        std::thread::sleep(Duration::from_millis(1));

        assert!(matches!(
            converter.convert_with_context(&dom, &mut context),
            Err(ConversionError::Timeout)
        ));
    }

    #[test]
    fn large_link_text_prescan_checks_timeout() {
        let body = "x".repeat(32 * 1024);
        assert_large_prescan_times_out(format!(
            "<p><a href=\"https://example.test\">{body}</a></p>"
        ));
    }

    #[test]
    fn large_inline_code_prescan_checks_timeout() {
        let body = "x".repeat(32 * 1024);
        assert_large_prescan_times_out(format!("<p><code>{body}</code></p>"));
    }

    #[test]
    fn large_fenced_code_prescan_checks_timeout() {
        let body = "x".repeat(32 * 1024);
        assert_large_prescan_times_out(format!("<pre><code>{body}</code></pre>"));
    }

    #[test]
    fn large_table_is_rejected_before_cell_growth() {
        let mut html = String::from("<table><thead><tr><th>head</th><th>head</th></tr></thead>");
        for _ in 0..24 {
            html.push_str("<tr><td>");
            html.push_str(&"cell ".repeat(32));
            html.push_str("</td><td>");
            html.push_str(&"value ".repeat(32));
            html.push_str("</td></tr>");
        }
        html.push_str("</table>");

        let converter = MarkdownConverter::with_options(ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..ConversionOptions::default()
        });
        let (result, context) = convert_with_budget(&converter, &html, 4096);

        assert_memory_limit(result);
        assert_eq!(context.working_set_bytes, 0);
    }

    #[test]
    fn long_text_line_is_rejected_before_normalization_allocation() {
        let html = format!("<p>{}</p>", "long-word ".repeat(4096));
        let (result, context) = convert_with_budget(&MarkdownConverter::new(), &html, 1024);

        assert_memory_limit(result);
        assert_eq!(context.working_set_bytes, 0);
    }

    #[test]
    fn large_link_and_media_are_rejected_before_resolution_allocation() {
        let long_path = "x".repeat(16 * 1024);
        let link_html = format!("<a href=\"https://example.test/{long_path}\">link</a>");
        let (link_result, link_context) =
            convert_with_budget(&MarkdownConverter::new(), &link_html, 1024);
        assert_memory_limit(link_result);
        assert_eq!(link_context.working_set_bytes, 0);

        let image_html = format!("<img src=\"https://example.test/{long_path}\" alt=\"image\">");
        let (image_result, image_context) =
            convert_with_budget(&MarkdownConverter::new(), &image_html, 1024);
        assert_memory_limit(image_result);
        assert_eq!(image_context.working_set_bytes, 0);
    }

    #[test]
    fn failed_reservation_is_released_before_context_reuse() {
        let large_html = format!("<pre>{}</pre>", "x".repeat(16 * 1024));
        let small_html = "<p>reused context</p>";
        let dom_large = parse_html(large_html.as_bytes()).expect("test HTML should parse");
        let dom_small = parse_html(small_html.as_bytes()).expect("test HTML should parse");
        let converter = MarkdownConverter::new();
        let mut context = ConversionContext::with_output_budget(Duration::ZERO, 2048);

        assert_memory_limit(converter.convert_with_context(&dom_large, &mut context));
        assert_eq!(context.working_set_bytes, 0);

        let markdown = converter
            .convert_with_context(&dom_small, &mut context)
            .expect("small conversion should reuse released working set");
        assert_eq!(markdown, "reused context\n");
        assert_eq!(context.working_set_bytes, 0);
    }

    #[test]
    fn list_item_rendered_len_never_panics_on_extreme_inputs() {
        // Extreme depth and content must saturate instead of overflowing
        // in debug builds, mirroring the checked upper-bound path.
        let rendered_len =
            MarkdownConverter::format_list_item_rendered_len(&"x".repeat(4096), usize::MAX, true);
        assert!(rendered_len >= usize::MAX - 8);

        // Deep nesting with realistic content stays exact and finite.
        assert_eq!(
            MarkdownConverter::format_list_item_rendered_len("line1\nline2", 3, false),
            28
        );

        // A realistic large content with realistic depth must remain far
        // below any overflow boundary while exercising the saturating adds.
        let large_len = MarkdownConverter::format_list_item_rendered_len(
            &"x".repeat(16 * 1024 * 1024),
            1000,
            true,
        );
        assert!(
            large_len > 16 * 1024 * 1024,
            "large content must be counted"
        );
    }

    #[test]
    fn deeply_nested_list_with_long_content_converts_without_panic() {
        let depth = 40;
        let mut html = String::from("<ul>");
        for _ in 0..depth {
            html.push_str("<li>outer<ul>");
        }
        html.push_str("<li>");
        html.push_str(&"detail ".repeat(512));
        html.push_str("</li>");
        for _ in 0..depth {
            html.push_str("</li></ul>");
        }
        html.push_str("</ul>");

        let (result, _context) =
            convert_with_budget(&MarkdownConverter::new(), &html, 16 * 1024 * 1024);
        let markdown = result.expect("deep nested list must convert without overflow");
        assert!(markdown.contains("- detail"), "got: {markdown}");
    }
}
