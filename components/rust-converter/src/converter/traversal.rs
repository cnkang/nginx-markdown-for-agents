//! DOM tree traversal for the Markdown converter.
//!
//! This module implements the depth-first, left-to-right traversal of the
//! html5ever DOM tree that drives the conversion process. Each node is
//! dispatched to the appropriate element handler based on its tag name.
//!
//! # Traversal Strategy
//!
//! The traversal is implemented as a recursive walk over the `RcDom` handle
//! tree. At each element node, the local tag name is matched against known
//! HTML element types and dispatched to the corresponding handler in
//! `blocks.rs`, `inline.rs`, or `tables.rs`. Unknown or unhandled elements
//! are traversed transparently (their children are processed, but the element
//! itself produces no direct output).
//!
//! # Timeout Integration
//!
//! The traversal supports cooperative timeout checking via
//! [`ConversionContext`]. Every 100 nodes, the elapsed time is checked
//! against the configured timeout. If exceeded, a
//! [`ConversionError::Timeout`] is returned immediately, unwinding the
//! recursive traversal.
//!
//! # Fast Path
//!
//! When [`ConversionContext::is_fast_path`] is `true`, the traversal skips
//! branches that the qualification scan has proven unreachable for simple
//! documents (form controls, embedded content, table/media processing),
//! reducing per-node overhead.
//!
//! # Text Node Handling
//!
//! [`write_normalized_text_node`] handles the subtleties of inter-node
//! whitespace: HTML parsing can split adjacent text into multiple nodes, so
//! this function reconstructs proper spacing to avoid accidental token
//! concatenation in the Markdown output.

use super::{ConversionContext, ConversionError, Handle, MarkdownConverter, NodeData, RcDom};

/// Append a string through the conversion budget when a context is present.
///
/// The context-aware path delegates to [`BudgetedMarkdownWriter`] so output
/// capacity is checked before `String` grows.  The context-free path is kept
/// for the unit-test helpers that exercise the renderer without a budget.
pub(super) fn append_str_with_context(
    output: &mut String,
    value: &str,
    ctx: &mut Option<&mut ConversionContext>,
) -> Result<(), ConversionError> {
    if let Some(context) = ctx.as_deref_mut() {
        let mut writer = context.budgeted_writer(output);
        writer.push_str(value)
    } else {
        output.push_str(value);
        Ok(())
    }
}

/// Append one character through the conversion budget when a context exists.
pub(super) fn append_char_with_context(
    output: &mut String,
    value: char,
    ctx: &mut Option<&mut ConversionContext>,
) -> Result<(), ConversionError> {
    if let Some(context) = ctx.as_deref_mut() {
        let mut writer = context.budgeted_writer(output);
        writer.push(value)
    } else {
        output.push(value);
        Ok(())
    }
}

/// Append ordinary Markdown text without materializing an escaped copy when
/// the conversion context is available.
pub(super) fn append_escaped_text_with_context(
    output: &mut String,
    text: &str,
    ctx: &mut Option<&mut ConversionContext>,
) -> Result<(), ConversionError> {
    if let Some(context) = ctx.as_deref_mut() {
        let mut state = crate::security::MarkdownTextEscapeState::default();
        context.append_escaped_text(output, text, &mut state)
    } else {
        output.push_str(&crate::security::escape_markdown_text(text));
        Ok(())
    }
}

/// Append a repeated character without allocating a temporary `String`.
pub(super) fn append_repeated_char_with_context(
    output: &mut String,
    value: char,
    count: usize,
    ctx: &mut Option<&mut ConversionContext>,
) -> Result<(), ConversionError> {
    for _ in 0..count {
        append_char_with_context(output, value, ctx)?;
    }
    Ok(())
}

/// Reserve a temporary allocation while the final output remains live.
///
/// `output.capacity()` is charged only for the duration of the reservation
/// check.  The temporary charge remains active while `f` runs, so a writer
/// growth or nested scratch allocation cannot exceed the same logical budget.
pub(super) fn with_reserved_working_set<F>(
    output: &mut String,
    ctx: &mut Option<&mut ConversionContext>,
    bytes: usize,
    f: F,
) -> Result<(), ConversionError>
where
    F: FnOnce(&mut String, &mut Option<&mut ConversionContext>) -> Result<(), ConversionError>,
{
    let output_capacity = output.capacity();
    if let Some(context) = ctx.as_deref_mut() {
        context.reserve_working_set_with_output(output_capacity, bytes)?;
        context.release_working_set(output_capacity);
    }

    let result = f(output, ctx);

    if let Some(context) = ctx.as_deref_mut() {
        context.release_working_set(bytes);
    }

    result
}

fn link_label_escape_capacity(label: &str) -> Result<usize, ConversionError> {
    // escape_link_label returns Cow::Owned when the label contains any
    // escapable character OR a newline/CR (which is replaced by a space),
    // and allocates s.len() + 8 in that case.  The working-set charge must
    // match the escaper's actual allocation, not a per-character count.
    let needs_owned = label.chars().any(|ch| {
        matches!(
            ch,
            '[' | ']' | '\\' | '<' | '>' | '*' | '_' | '`' | '~' | '\n' | '\r'
        )
    });
    if needs_owned {
        label.len().checked_add(8).ok_or_else(|| {
            ConversionError::MemoryLimit("link label working-set size overflow".into())
        })
    } else {
        Ok(0)
    }
}

/// Append a link label using the canonical security escaper.
pub(super) fn append_link_label(
    output: &mut String,
    label: &str,
    ctx: &mut Option<&mut ConversionContext>,
) -> Result<(), ConversionError> {
    let capacity = link_label_escape_capacity(label)?;
    with_reserved_working_set(output, ctx, capacity, |output, ctx| {
        let escaped = crate::security::escape_link_label(label);
        append_str_with_context(output, escaped.as_ref(), ctx)
    })
}

/// Append a Markdown link destination without allocating an escaped copy.
pub(super) fn append_link_destination(
    output: &mut String,
    url: &str,
    ctx: &mut Option<&mut ConversionContext>,
) -> Result<(), ConversionError> {
    let needs_angle_brackets = url.contains(' ')
        || url.contains('(')
        || url.contains(')')
        || url.contains('<')
        || url.contains('>');
    if !needs_angle_brackets {
        return append_str_with_context(output, url, ctx);
    }

    append_char_with_context(output, '<', ctx)?;
    for ch in url.chars() {
        match ch {
            '<' => append_str_with_context(output, "%3C", ctx)?,
            '>' => append_str_with_context(output, "%3E", ctx)?,
            _ => append_char_with_context(output, ch, ctx)?,
        }
    }
    append_char_with_context(output, '>', ctx)
}

/// Append a Markdown link title while escaping its two delimiter characters.
pub(super) fn append_markdown_title(
    output: &mut String,
    title: &str,
    ctx: &mut Option<&mut ConversionContext>,
) -> Result<(), ConversionError> {
    for ch in title.chars() {
        if matches!(ch, '\\' | '"') {
            append_char_with_context(output, '\\', ctx)?;
        }
        append_char_with_context(output, ch, ctx)?;
    }
    Ok(())
}

/// Append Markdown image syntax without materializing escaped fields.
pub(super) fn append_image_with_context(
    output: &mut String,
    alt: &str,
    url: &str,
    title: Option<&str>,
    ctx: &mut Option<&mut ConversionContext>,
) -> Result<(), ConversionError> {
    append_str_with_context(output, "![", ctx)?;
    append_link_label(output, alt, ctx)?;
    append_str_with_context(output, "](", ctx)?;
    append_link_destination(output, url, ctx)?;
    if let Some(title) = title.map(str::trim).filter(|title| !title.is_empty()) {
        append_str_with_context(output, " \"", ctx)?;
        append_markdown_title(output, title, ctx)?;
        append_char_with_context(output, '"', ctx)?;
    }
    append_char_with_context(output, ')', ctx)
}

impl MarkdownConverter {
    /// Validate DOM depth without using the recursive renderer stack.
    pub(super) fn validate_dom_depth(&self, dom: &RcDom) -> Result<(), ConversionError> {
        let mut pending = vec![(dom.document.clone(), 0usize)];
        while let Some((node, depth)) = pending.pop() {
            if !matches!(node.data, NodeData::Document) {
                self.security_validator
                    .validate_depth(depth)
                    .map_err(ConversionError::InvalidInput)?;
            }

            let child_depth = if matches!(node.data, NodeData::Document) {
                depth
            } else {
                depth.checked_add(1).ok_or_else(|| {
                    ConversionError::InvalidInput(
                        "HTML nesting depth arithmetic overflow".to_string(),
                    )
                })?
            };
            for child in node.children.borrow().iter().rev() {
                pending.push((child.clone(), child_depth));
            }
        }
        Ok(())
    }

    /// Normalize and append a text node while preserving meaningful spacing.
    ///
    /// HTML parsing can split adjacent text and whitespace into multiple nodes.
    /// This helper normalizes node-local content and then reconstructs inter-node
    /// spacing so Markdown tokens are not accidentally concatenated.
    pub(super) fn write_normalized_text_node(
        &self,
        text: &str,
        output: &mut String,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        let normalized_capacity = text.len();
        with_reserved_working_set(output, &mut ctx, normalized_capacity, |output, ctx| {
            let normalized = self.normalize_text(text);
            if normalized.is_empty() {
                if text.chars().all(char::is_whitespace)
                    && self.has_body_content(output)
                    && !output.ends_with(' ')
                {
                    append_char_with_context(output, ' ', ctx)?;
                }
                return Ok(());
            }

            if text.starts_with(char::is_whitespace)
                && self.has_body_content(output)
                && !output.ends_with(' ')
            {
                append_char_with_context(output, ' ', ctx)?;
            }

            // Escape incrementally: materializing the full escaped string first
            // let transient allocations exceed the conversion budget before the
            // final length check ran.  The budget-aware append fails before an
            // over-budget allocation happens.
            append_escaped_text_with_context(output, &normalized, ctx)?;

            if text.ends_with(char::is_whitespace) {
                append_char_with_context(output, ' ', ctx)?;
            }
            Ok(())
        })
    }

    /// Traverse all child nodes in source order.
    pub(super) fn traverse_children(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        // Reborrow `ctx` per iteration so each recursive call can consume an
        // independent mutable reference without moving the original Option.
        let mut ctx = ctx;
        for child in node.children.borrow().iter() {
            self.traverse_node_optional(child, output, depth, ctx.as_deref_mut())?;
        }

        Ok(())
    }

    /// Traverse through the timeout-aware path when a conversion context exists.
    pub(super) fn traverse_node_optional(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        // Dispatch traversal through the timeout-aware path only when context
        // is present, keeping the no-timeout path allocation-free.
        match ctx {
            Some(ctx) => {
                self.traverse_node_with_context(node, output, depth, ctx)?;
                ctx.check_output_budget(output.len())
            }
            None => self.traverse_node(node, output, depth),
        }
    }

    /// Internal element dispatcher shared by context and non-context entry points.
    fn append_escaped_control_text(
        output: &mut String,
        text: Option<&str>,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        if let Some(text) = text {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                append_escaped_text_with_context(output, trimmed, ctx)?;
                append_char_with_context(output, ' ', ctx)?;
            }
        }
        Ok(())
    }

    fn handle_void_form_control(
        &self,
        node: &Handle,
        output: &mut String,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let input_type = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "type")
                .map(|a| a.value.as_ref())
                .unwrap_or_default();
            let is_hidden = input_type.eq_ignore_ascii_case("hidden");
            let is_submit = input_type.eq_ignore_ascii_case("submit");
            let is_reset = input_type.eq_ignore_ascii_case("reset");
            let is_image = input_type.eq_ignore_ascii_case("image");

            if is_hidden || is_submit || is_reset || is_image {
                if (is_submit || is_reset)
                    && let Some(value) = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "value")
                        .map(|a| a.value.as_ref())
                {
                    Self::append_escaped_control_text(output, Some(value), &mut ctx)?;
                }
                return Ok(());
            }

            let text = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "aria-label")
                .or_else(|| {
                    attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "placeholder")
                })
                .or_else(|| {
                    attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "value")
                })
                .map(|a| a.value.as_ref());

            Self::append_escaped_control_text(output, text, &mut ctx)?;
        }
        Ok(())
    }

    fn handle_strip_element(
        &self,
        node: &Handle,
        tag_name: &str,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        if self.security_validator.is_embedded_content(tag_name)
            && let NodeData::Element { ref attrs, .. } = node.data
        {
            let attrs_borrowed = attrs.borrow();
            let url_attr = if tag_name == "object" { "data" } else { "src" };
            let url = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == url_attr)
                .map(|a| a.value.as_ref());
            let title = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "title")
                .map(|a| a.value.as_ref());

            if let Some(url) = url
                && let Some(safe_url) = self.security_validator.sanitize_url(url.trim())
            {
                let url_capacity = self.resolved_url_capacity(safe_url)?;
                with_reserved_working_set(output, &mut ctx, url_capacity, |output, ctx| {
                    let resolved_url = self.resolve_url(safe_url);
                    Self::emit_markdown_link(&[title], &resolved_url, output, ctx)
                })?;
            }
        }

        self.traverse_children(node, output, depth + 1, ctx)
    }

    fn dispatch_element(
        &self,
        node: &Handle,
        tag_name: &str,
        output: &mut String,
        depth: usize,
        mut ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        match tag_name {
            "h1" => self.handle_heading_with_context(node, 1, output, depth, ctx.as_deref_mut())?,
            "h2" => self.handle_heading_with_context(node, 2, output, depth, ctx.as_deref_mut())?,
            "h3" => self.handle_heading_with_context(node, 3, output, depth, ctx.as_deref_mut())?,
            "h4" => self.handle_heading_with_context(node, 4, output, depth, ctx.as_deref_mut())?,
            "h5" => self.handle_heading_with_context(node, 5, output, depth, ctx.as_deref_mut())?,
            "h6" => self.handle_heading_with_context(node, 6, output, depth, ctx.as_deref_mut())?,
            "p" => self.handle_paragraph_with_context(node, output, depth, ctx.as_deref_mut())?,
            "a" => self.handle_link_with_context(node, output, depth, ctx.as_deref_mut())?,
            "img" => self.handle_image(node, output, depth, ctx.as_deref_mut())?,
            "ul" => self.handle_list_with_context(node, output, 0, false, ctx.as_deref_mut())?,
            "ol" => self.handle_list_with_context(node, output, 0, true, ctx.as_deref_mut())?,
            "li" => self.handle_list_item_with_context(node, output, 0, ctx.as_deref_mut())?,
            "pre" => {
                self.handle_code_block_with_context(node, output, depth, ctx.as_deref_mut())?
            }
            "code" => self.handle_inline_code(node, output, depth, ctx.as_deref_mut())?,
            "strong" | "b" => {
                self.handle_bold_with_context(node, output, depth, ctx.as_deref_mut())?
            }
            "em" | "i" => {
                self.handle_italic_with_context(node, output, depth, ctx.as_deref_mut())?
            }
            "table" => self.handle_table_with_context(node, output, depth, ctx.as_deref_mut())?,
            "script" | "style" | "noscript" => {}
            "video" | "audio" => {
                self.extract_media_urls(node, tag_name, output, ctx.as_deref_mut())?;
                self.traverse_children(node, output, depth + 1, ctx.as_deref_mut())?;
            }
            "source" => self.extract_source_url(node, output, ctx.as_deref_mut())?,
            "track" => self.extract_track_url(node, output, ctx.as_deref_mut())?,
            "area" => self.extract_area_link(node, output, ctx.as_deref_mut())?,
            _ => self.traverse_children(node, output, depth + 1, ctx)?,
        }
        Ok(())
    }

    pub(super) fn handle_element_internal(
        &self,
        node: &Handle,
        tag_name: &str,
        output: &mut String,
        depth: usize,
        mut ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        use crate::security::SanitizeAction;

        // Early pruning: skip subtrees that produce no meaningful Markdown output.
        // This check runs before SecurityValidator to avoid unnecessary work for
        // elements that are always pruned (script/style/noscript) or optionally
        // pruned noise regions (nav/footer/aside when runtime or feature-enabled).
        match super::pruning::should_prune_with_config(tag_name, &self.options.prune_config) {
            super::pruning::PruneDecision::SkipChildren
            | super::pruning::PruneDecision::SkipSubtree => return Ok(()),
            super::pruning::PruneDecision::Traverse => {}
        }

        let sanitize_action = self.security_validator.check_element(tag_name);
        if matches!(sanitize_action, SanitizeAction::Remove) {
            return Ok(());
        }

        // Fast-path branch elimination: when the document qualified for the
        // fast path, form controls, embedded content, and strip-element
        // handling are unreachable (the qualification scan already confirmed
        // only fast-path-compatible elements are present). Skip the per-node
        // method calls and attribute inspection for these branches.
        let is_fast_path = ctx.as_ref().is_some_and(|c| c.is_fast_path);

        if !is_fast_path && self.security_validator.is_void_form_control(tag_name) {
            self.handle_void_form_control(node, output, ctx.as_deref_mut())?;
            return Ok(());
        }

        // Form container elements: strip the tag but traverse children so
        // their text content is preserved in the Markdown output.
        if !is_fast_path && matches!(sanitize_action, SanitizeAction::StripElement) {
            return self.handle_strip_element(node, tag_name, output, depth, ctx);
        }

        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        self.dispatch_element(node, tag_name, output, depth, ctx)
    }

    /// Traverse a DOM node and convert it to Markdown.
    pub(super) fn traverse_node(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        match node.data {
            NodeData::Document => self.traverse_children(node, output, depth, None)?,
            NodeData::Element { ref name, .. } => {
                self.handle_element(node, name.local.as_ref(), output, depth)?;
            }
            NodeData::Text { ref contents } => {
                let text = contents.borrow();
                self.write_normalized_text_node(text.as_ref(), output, None)?;
            }
            NodeData::Comment { .. }
            | NodeData::Doctype { .. }
            | NodeData::ProcessingInstruction { .. } => {}
        }

        Ok(())
    }

    /// Traverse a DOM node with timeout support.
    pub(super) fn traverse_node_with_context(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: &mut ConversionContext,
    ) -> Result<(), ConversionError> {
        ctx.increment_and_check()?;

        match node.data {
            NodeData::Document => self.traverse_children(node, output, depth, Some(ctx))?,
            NodeData::Element { ref name, .. } => {
                self.handle_element_with_context(node, name.local.as_ref(), output, depth, ctx)?;
            }
            NodeData::Text { ref contents } => {
                let text = contents.borrow();
                self.write_normalized_text_node(text.as_ref(), output, Some(ctx))?;
            }
            NodeData::Comment { .. }
            | NodeData::Doctype { .. }
            | NodeData::ProcessingInstruction { .. } => {}
        }

        ctx.check_output_budget(output.len())
    }

    /// Handle an HTML element and convert it to Markdown.
    pub(super) fn handle_element(
        &self,
        node: &Handle,
        tag_name: &str,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        self.handle_element_internal(node, tag_name, output, depth, None)
    }

    /// Handle an HTML element with timeout support.
    pub(super) fn handle_element_with_context(
        &self,
        node: &Handle,
        tag_name: &str,
        output: &mut String,
        depth: usize,
        ctx: &mut ConversionContext,
    ) -> Result<(), ConversionError> {
        self.handle_element_internal(node, tag_name, output, depth, Some(ctx))
    }

    /// Return an upper bound for the temporary `String` created by URL
    /// resolution.  Absolute and disabled-resolution paths copy only `url`;
    /// relative paths can include the base URL and one separator.
    pub(super) fn resolved_url_capacity(&self, url: &str) -> Result<usize, ConversionError> {
        if !self.options.resolve_relative_urls
            || self.options.base_url.is_none()
            || Self::has_absolute_uri_scheme(url)
        {
            return Ok(url.len());
        }

        self.options
            .base_url
            .as_ref()
            .and_then(|base| base.len().checked_add(url.len()))
            .and_then(|length| length.checked_add(1))
            .ok_or_else(|| ConversionError::MemoryLimit("resolved URL size overflow".into()))
    }

    /// Emit a Markdown link `[label](url)\n` into `output`.
    ///
    /// Centralizes the label-escape and URL-destination-escape logic shared
    /// by the embedded-content, media, source, track, and area emit sites.
    /// Both label and destination are escaped to prevent Markdown injection
    /// by the shared append helpers.
    ///
    /// # Arguments
    ///
    /// * `label_candidates` - Ordered slice of optional label strings; the
    ///   first non-empty trimmed value is used. This allows callers to
    ///   express a priority chain (e.g., `alt` text before `title` text
    ///   before a URL-derived fallback).
    /// * `safe_url` - Already-sanitized URL from `SecurityValidator`.
    /// * `output` - The output buffer to append to.
    fn emit_markdown_link(
        label_candidates: &[Option<&str>],
        safe_url: &str,
        output: &mut String,
        ctx: &mut Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let label = label_candidates
            .iter()
            .filter_map(|opt| opt.map(str::trim).filter(|s| !s.is_empty()))
            .next()
            .unwrap_or(safe_url);
        append_char_with_context(output, '[', ctx)?;
        append_link_label(output, label, ctx)?;
        append_str_with_context(output, "](", ctx)?;
        append_link_destination(output, safe_url, ctx)?;
        append_str_with_context(output, ")\n", ctx)
    }

    /// Extract `src` and `poster` URLs from `<video>` / `<audio>` elements
    /// as Markdown links so AI agents know what media was referenced.
    ///
    /// For `<video>` elements, both the `src` URL (as a `[title](url)` link)
    /// and the `poster` thumbnail URL (as a `![](url)` image) are emitted.
    /// For `<audio>` elements, only the `src` URL is emitted. All URLs pass
    /// through `SecurityValidator::sanitize_url` before emission.
    fn extract_media_urls(
        &self,
        node: &Handle,
        tag_name: &str,
        output: &mut String,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();

            let src = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "src")
                .map(|a| a.value.as_ref());
            let title = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "title")
                .map(|a| a.value.as_ref());

            if let Some(u) = src {
                let trimmed = u.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    let url_capacity = self.resolved_url_capacity(safe_url)?;
                    with_reserved_working_set(output, &mut ctx, url_capacity, |output, ctx| {
                        let resolved_url = self.resolve_url(safe_url);
                        Self::emit_markdown_link(&[title], &resolved_url, output, ctx)
                    })?;
                }
            }

            // video poster thumbnail
            if tag_name == "video"
                && let Some(poster) = attrs_borrowed
                    .iter()
                    .find(|a| a.name.local.as_ref() == "poster")
                    .map(|a| a.value.as_ref())
            {
                let trimmed = poster.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    let url_capacity = self.resolved_url_capacity(safe_url)?;
                    with_reserved_working_set(output, &mut ctx, url_capacity, |output, ctx| {
                        let resolved_url = self.resolve_url(safe_url);
                        append_image_with_context(output, "", &resolved_url, None, ctx)?;
                        append_char_with_context(output, '\n', ctx)
                    })?;
                }
            }
        }
        Ok(())
    }

    /// Extract `src` from a `<source>` element as a Markdown link.
    fn extract_source_url(
        &self,
        node: &Handle,
        output: &mut String,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let src = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "src")
                .map(|a| a.value.as_ref());

            if let Some(u) = src {
                let trimmed = u.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    // Use type attribute as context if available
                    let type_attr = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "type")
                        .map(|a| a.value.as_ref());
                    let url_capacity = self.resolved_url_capacity(safe_url)?;
                    with_reserved_working_set(output, &mut ctx, url_capacity, |output, ctx| {
                        let resolved_url = self.resolve_url(safe_url);
                        Self::emit_markdown_link(&[type_attr], &resolved_url, output, ctx)
                    })?;
                }
            }
        }
        Ok(())
    }

    /// Extract `src` and `label` from a `<track>` element as a Markdown link.
    fn extract_track_url(
        &self,
        node: &Handle,
        output: &mut String,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let src = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "src")
                .map(|a| a.value.as_ref());

            if let Some(u) = src {
                let trimmed = u.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    let label = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "label")
                        .map(|a| a.value.as_ref());
                    let url_capacity = self.resolved_url_capacity(safe_url)?;
                    with_reserved_working_set(output, &mut ctx, url_capacity, |output, ctx| {
                        let resolved_url = self.resolve_url(safe_url);
                        Self::emit_markdown_link(&[label], &resolved_url, output, ctx)
                    })?;
                }
            }
        }
        Ok(())
    }

    /// Extract `href` and `alt` from an `<area>` element as a Markdown link.
    fn extract_area_link(
        &self,
        node: &Handle,
        output: &mut String,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let href = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "href")
                .map(|a| a.value.as_ref());

            if let Some(u) = href {
                let trimmed = u.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    let alt = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "alt")
                        .map(|a| a.value.as_ref());
                    let title = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "title")
                        .map(|a| a.value.as_ref());
                    let url_capacity = self.resolved_url_capacity(safe_url)?;
                    with_reserved_working_set(output, &mut ctx, url_capacity, |output, ctx| {
                        let resolved_url = self.resolve_url(safe_url);
                        Self::emit_markdown_link(&[alt, title], &resolved_url, output, ctx)
                    })?;
                }
            }
        }
        Ok(())
    }
}
