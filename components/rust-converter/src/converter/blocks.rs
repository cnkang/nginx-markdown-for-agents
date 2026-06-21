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

use super::*;

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
                let first_trimmed = line.trim_start();
                // If the content already starts with a list marker (from a
                // nested list that was converted earlier), emit a blank line
                // after our marker and then the content with continuation
                // indentation to avoid producing a malformed double-marker.
                if first_trimmed.starts_with("- ")
                    || first_trimmed.starts_with("* ")
                    || first_trimmed.starts_with("1. ")
                {
                    output.push('\n');
                    if !line.is_empty() {
                        let already_indented = (!base_indent.is_empty()
                            && line.starts_with(&base_indent))
                            || line.starts_with(' ')
                            || line.starts_with('\t');
                        if !already_indented {
                            output.push_str(&continuation_indent);
                        }
                        output.push_str(line);
                        output.push('\n');
                    }
                    continue;
                }
            } else if !line.is_empty() {
                // Indent continuation lines unless they already carry
                // indentation (from pre-formatted or nested content).
                let already_indented = (!base_indent.is_empty() && line.starts_with(&base_indent))
                    || line.starts_with(' ')
                    || line.starts_with('\t');
                if !already_indented {
                    output.push_str(&continuation_indent);
                }
            }

            output.push_str(line);
            output.push('\n');
        }
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
        ctx: Option<&mut ConversionContext>,
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
        let mut ctx = ctx;
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
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        let start_len = output.len();
        let mut ctx = ctx;
        for child in node.children.borrow().iter() {
            self.traverse_node_optional(child, output, depth + 1, ctx.as_deref_mut())?;
        }

        if output.len() > start_len {
            output.push_str("\n\n");
        }

        Ok(())
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
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        let mut ctx = ctx;
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

        Ok(())
    }

    /// Handle list item elements (li) with optional timeout context.
    pub(super) fn handle_list_item_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        self.handle_list_item_with_marker(node, output, depth, false, ctx)
    }

    /// Handle list item elements with specific marker type.
    pub(super) fn handle_list_item_with_marker(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ordered: bool,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut item_output = String::new();
        let mut ctx = ctx;
        for child in node.children.borrow().iter() {
            match child.data {
                NodeData::Element { ref name, .. } => {
                    let tag_name = name.local.as_ref();
                    if tag_name == "ul" {
                        if !item_output.is_empty() && !item_output.ends_with('\n') {
                            item_output.push('\n');
                        }
                        self.handle_list_with_context(
                            child,
                            &mut item_output,
                            depth + 1,
                            false,
                            ctx.as_deref_mut(),
                        )?;
                    } else if tag_name == "ol" {
                        if !item_output.is_empty() && !item_output.ends_with('\n') {
                            item_output.push('\n');
                        }
                        self.handle_list_with_context(
                            child,
                            &mut item_output,
                            depth + 1,
                            true,
                            ctx.as_deref_mut(),
                        )?;
                    } else {
                        self.traverse_node_optional(
                            child,
                            &mut item_output,
                            depth + 1,
                            ctx.as_deref_mut(),
                        )?;
                    }
                }
                _ => {
                    self.traverse_node_optional(
                        child,
                        &mut item_output,
                        depth + 1,
                        ctx.as_deref_mut(),
                    )?;
                }
            }
        }

        self.format_list_item_lines(output, &item_output, depth, ordered);

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

        let mut language = String::new();
        for child in node.children.borrow().iter() {
            if let NodeData::Element {
                ref name,
                ref attrs,
                ..
            } = child.data
                && name.local.as_ref() == "code"
            {
                for attr in attrs.borrow().iter() {
                    if attr.name.local.as_ref() == "class" {
                        let class_value = attr.value.to_string();
                        for class in class_value.split_whitespace() {
                            if let Some(lang) = class.strip_prefix("language-") {
                                language = Self::safe_code_language(lang).unwrap_or_default();
                                break;
                            } else if let Some(lang) = class.strip_prefix("lang-") {
                                language = Self::safe_code_language(lang).unwrap_or_default();
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
