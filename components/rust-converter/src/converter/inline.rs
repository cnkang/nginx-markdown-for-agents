//! Inline element handlers for the Markdown converter.
//!
//! This module contains methods on [`MarkdownConverter`] that handle inline
//! (phrasing-content) HTML elements during DOM-to-Markdown traversal. Inline
//! elements produce Markdown inline constructs (links, images, emphasis, code
//! spans) and do not emit surrounding blank lines.
//!
//! # Element Coverage
//!
//! | HTML Element | Markdown Output | Handler |
//! |-------------|----------------|---------|
//! | `<a>` | `[text](url)` | `handle_link` |
//! | `<img>` | `![alt](src)` | `handle_image` |
//! | `<strong>`, `<b>` | `**text**` | `handle_strong` |
//! | `<em>`, `<i>` | `*text*` | `handle_emphasis` |
//! | `<code>` | `` `text` `` | `handle_inline_code` |
//! | `<br>` | hard line break | `handle_line_break` |
//! | `<sub>`, `<sup>` | Unicode subscript/superscript | fallback text |
//!
//! # Security
//!
//! Link and image URL extraction passes through [`SecurityValidator::sanitize_url`]
//! to suppress dangerous URL schemes (`javascript:`, `data:`, `vbscript:`).
//! When a URL is rejected, the link text is still emitted but the URL is
//! omitted, preventing XSS while preserving content accessibility for AI agents.
//!
use super::traversal::{
    append_char_with_context, append_escaped_text_with_context, append_image_with_context,
    append_link_destination, append_link_label, append_repeated_char_with_context,
    append_str_with_context, with_reserved_working_set,
};
use super::{ConversionContext, ConversionError, Handle, MarkdownConverter, NodeData};

/// Statistics for code content collected without materializing a second
/// `String`.  The backtick run is carried across text-node boundaries so
/// the selected fence remains identical to the concatenated legacy value.
#[derive(Debug, Default)]
pub(super) struct CodeContentStats {
    pub(super) max_backtick_run: usize,
    current_backtick_run: usize,
    pub(super) first_byte: Option<u8>,
    pub(super) last_byte: Option<u8>,
    pub(super) len: usize,
}

const MEASUREMENT_CHECKPOINT_BYTES: usize = 1024;

fn check_measurement_node(ctx: &mut Option<&mut ConversionContext>) -> Result<(), ConversionError> {
    if let Some(context) = ctx.as_deref_mut() {
        context.increment_and_check()?;
    }
    Ok(())
}

fn check_measurement_bytes(
    ctx: &mut Option<&mut ConversionContext>,
    bytes: &[u8],
) -> Result<(), ConversionError> {
    if let Some(context) = ctx.as_deref_mut() {
        for _ in bytes.chunks(MEASUREMENT_CHECKPOINT_BYTES) {
            context.check_timeout()?;
        }
    }
    Ok(())
}

fn check_measurement_checkpoint(
    ctx: &mut Option<&mut ConversionContext>,
    byte_index: usize,
) -> Result<(), ConversionError> {
    if byte_index.is_multiple_of(MEASUREMENT_CHECKPOINT_BYTES)
        && let Some(context) = ctx.as_deref_mut()
    {
        context.check_timeout()?;
    }
    Ok(())
}

impl MarkdownConverter {
    /// Handle anchor (link) elements with optional timeout context.
    pub(super) fn handle_link_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        self.handle_link(node, output, depth, ctx)
    }

    /// Handle anchor (link) elements.
    pub(super) fn handle_link(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        let link_text_capacity = self.text_content_len(node, depth, &mut ctx)?;

        with_reserved_working_set(output, &mut ctx, link_text_capacity, |output, ctx| {
            let mut link_text = String::with_capacity(link_text_capacity);
            for child in node.children.borrow().iter() {
                self.extract_text(child, &mut link_text, depth + 1, ctx.as_deref_mut())?;
            }

            let normalized_capacity = link_text.len();
            with_reserved_working_set(output, ctx, normalized_capacity, |output, ctx| {
                let normalized_text = self.normalize_text(&link_text);
                if normalized_text.is_empty() {
                    return Ok(());
                }

                if let NodeData::Element { ref attrs, .. } = node.data {
                    let attrs_borrowed = attrs.borrow();
                    let href = attrs_borrowed
                        .iter()
                        .find(|attr| attr.name.local.as_ref() == "href")
                        .map(|attr| attr.value.as_ref());

                    if let Some(url) = href {
                        if let Some(safe_url) = self.security_validator.sanitize_url(url)
                            && !safe_url.is_empty()
                        {
                            let url_capacity = self.resolved_url_capacity(safe_url)?;
                            with_reserved_working_set(output, ctx, url_capacity, |output, ctx| {
                                let resolved_url = self.resolve_url(safe_url);
                                append_char_with_context(output, '[', ctx)?;
                                append_link_label(output, &normalized_text, ctx)?;
                                append_str_with_context(output, "](", ctx)?;
                                append_link_destination(output, &resolved_url, ctx)?;
                                append_char_with_context(output, ')', ctx)
                            })?;
                        } else {
                            append_link_label(output, &normalized_text, ctx)?;
                        }
                    } else {
                        append_escaped_text_with_context(output, &normalized_text, ctx)?;
                    }
                } else {
                    append_escaped_text_with_context(output, &normalized_text, ctx)?;
                }
                Ok(())
            })
        })
    }

    /// Handle image elements.
    ///
    /// Outputs standard Markdown image syntax `![alt](src "title")`.
    /// When the URL is missing or blocked by URL sanitization, the `alt`
    /// text is still emitted so AI agents do not lose the description.
    pub(super) fn handle_image(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let src = attrs_borrowed
                .iter()
                .find(|attr| attr.name.local.as_ref() == "src")
                .map(|attr| attr.value.as_ref());
            let alt = attrs_borrowed
                .iter()
                .find(|attr| attr.name.local.as_ref() == "alt")
                .map(|attr| attr.value.as_ref())
                .unwrap_or_default();
            let title = attrs_borrowed
                .iter()
                .find(|attr| attr.name.local.as_ref() == "title")
                .map(|attr| attr.value.as_ref());

            let safe_url = src.and_then(|url| self.security_validator.sanitize_url(url));

            if let Some(url) = safe_url
                && !url.is_empty()
            {
                let url_capacity = self.resolved_url_capacity(url)?;
                with_reserved_working_set(output, &mut ctx, url_capacity, |output, ctx| {
                    let resolved_url = self.resolve_url(url);
                    append_image_with_context(output, alt, &resolved_url, title, ctx)
                })?;
            } else if !alt.trim().is_empty() {
                // URL missing or dangerous — preserve alt text for AI agents
                append_link_label(output, alt.trim(), &mut ctx)?;
            }
        }

        Ok(())
    }

    /// Handle inline code elements.
    pub(super) fn handle_inline_code(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        let stats = self.measure_code_content(node, depth, &mut ctx)?;
        let fence_len = stats.max_backtick_run.checked_add(1).ok_or_else(|| {
            ConversionError::MemoryLimit("inline code fence length overflow".into())
        })?;
        let padded = stats.first_byte == Some(b'`') || stats.last_byte == Some(b'`');

        append_repeated_char_with_context(output, '`', fence_len, &mut ctx)?;
        if padded {
            append_char_with_context(output, ' ', &mut ctx)?;
        }
        self.extract_code_content(node, output, depth, ctx.as_deref_mut())?;
        if padded {
            append_char_with_context(output, ' ', &mut ctx)?;
        }
        append_repeated_char_with_context(output, '`', fence_len, &mut ctx)?;
        Ok(())
    }

    /// Handle bold/strong elements with optional timeout context.
    pub(super) fn handle_bold_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        append_str_with_context(output, "**", &mut ctx)?;
        self.traverse_children(node, output, depth + 1, ctx.as_deref_mut())?;
        append_str_with_context(output, "**", &mut ctx)?;
        Ok(())
    }

    /// Handle italic/emphasis elements with optional timeout context.
    pub(super) fn handle_italic_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        append_char_with_context(output, '*', &mut ctx)?;
        self.traverse_children(node, output, depth + 1, ctx.as_deref_mut())?;
        append_char_with_context(output, '*', &mut ctx)?;
        Ok(())
    }

    /// Count the raw text bytes emitted by [`extract_text`] without
    /// allocating.  The count pre-reserves the temporary link-label source
    /// before the recursive extractor starts writing it.
    fn text_content_len(
        &self,
        node: &Handle,
        depth: usize,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<usize, ConversionError> {
        check_measurement_node(ctx)?;
        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        match node.data {
            NodeData::Text { ref contents } => {
                let text = contents.borrow();
                check_measurement_bytes(ctx, text.as_bytes())?;
                Ok(text.len())
            }
            NodeData::Element { ref name, .. } => {
                if matches!(name.local.as_ref(), "script" | "style" | "noscript") {
                    return Ok(0);
                }

                let child_depth = depth.checked_add(1).ok_or_else(|| {
                    ConversionError::MemoryLimit("text extraction depth overflow".into())
                })?;
                let mut total = 0usize;
                for child in node.children.borrow().iter() {
                    total = total
                        .checked_add(self.text_content_len(child, child_depth, ctx)?)
                        .ok_or_else(|| {
                            ConversionError::MemoryLimit(
                                "link text working-set size overflow".into(),
                            )
                        })?;
                }
                Ok(total)
            }
            _ => Ok(0),
        }
    }

    /// Measure code content before output writes begin.
    pub(super) fn measure_code_content(
        &self,
        node: &Handle,
        depth: usize,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<CodeContentStats, ConversionError> {
        let mut stats = CodeContentStats::default();
        self.measure_code_content_into(node, depth, &mut stats, ctx)?;
        Ok(stats)
    }

    fn measure_code_content_into(
        &self,
        node: &Handle,
        depth: usize,
        stats: &mut CodeContentStats,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        check_measurement_node(ctx)?;
        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        match node.data {
            NodeData::Text { ref contents } => {
                let text = contents.borrow();
                for (byte_index, byte) in text.as_ref().bytes().enumerate() {
                    check_measurement_checkpoint(ctx, byte_index)?;
                    if stats.first_byte.is_none() {
                        stats.first_byte = Some(byte);
                    }
                    stats.last_byte = Some(byte);
                    stats.len = stats.len.checked_add(1).ok_or_else(|| {
                        ConversionError::MemoryLimit("code content size overflow".into())
                    })?;
                    if byte == b'`' {
                        stats.current_backtick_run =
                            stats.current_backtick_run.checked_add(1).ok_or_else(|| {
                                ConversionError::MemoryLimit("code fence length overflow".into())
                            })?;
                        stats.max_backtick_run =
                            stats.max_backtick_run.max(stats.current_backtick_run);
                    } else {
                        stats.current_backtick_run = 0;
                    }
                }
            }
            NodeData::Element { ref name, .. } => {
                if matches!(name.local.as_ref(), "script" | "style" | "noscript") {
                    return Ok(());
                }

                let child_depth = depth.checked_add(1).ok_or_else(|| {
                    ConversionError::MemoryLimit("code extraction depth overflow".into())
                })?;
                for child in node.children.borrow().iter() {
                    self.measure_code_content_into(child, child_depth, stats, ctx)?;
                }
            }
            _ => {}
        }
        Ok(())
    }

    /// Extract code content from a node without any normalization.
    pub(super) fn extract_code_content(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;

        if let Some(ctx) = ctx.as_deref_mut() {
            ctx.increment_and_check()?;
        }
        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        match node.data {
            NodeData::Text { ref contents } => {
                let text = contents.borrow();
                append_str_with_context(output, text.as_ref(), &mut ctx)?;
            }
            NodeData::Element { ref name, .. } => {
                if matches!(name.local.as_ref(), "script" | "style" | "noscript") {
                    return Ok(());
                }

                for child in node.children.borrow().iter() {
                    let child_depth = depth.checked_add(1).ok_or_else(|| {
                        ConversionError::MemoryLimit("code extraction depth overflow".into())
                    })?;
                    self.extract_code_content(child, output, child_depth, ctx.as_deref_mut())?;
                }
            }
            _ => {}
        }
        Ok(())
    }

    /// Extract text content from a node and its descendants.
    pub(super) fn extract_text(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;

        if let Some(ctx) = ctx.as_deref_mut() {
            ctx.increment_and_check()?;
        }
        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        match node.data {
            NodeData::Text { ref contents } => {
                let text = contents.borrow();
                append_str_with_context(output, text.as_ref(), &mut ctx)?;
            }
            NodeData::Element { ref name, .. } => {
                if matches!(name.local.as_ref(), "script" | "style" | "noscript") {
                    return Ok(());
                }

                for child in node.children.borrow().iter() {
                    let child_depth = depth.checked_add(1).ok_or_else(|| {
                        ConversionError::MemoryLimit("text extraction depth overflow".into())
                    })?;
                    self.extract_text(child, output, child_depth, ctx.as_deref_mut())?;
                }
            }
            _ => {}
        }
        Ok(())
    }
}
