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

use super::*;

impl MarkdownConverter {
    /// Normalize and append a text node while preserving meaningful spacing.
    ///
    /// HTML parsing can split adjacent text and whitespace into multiple nodes.
    /// This helper normalizes node-local content and then reconstructs inter-node
    /// spacing so Markdown tokens are not accidentally concatenated.
    pub(super) fn write_normalized_text_node(&self, text: &str, output: &mut String) {
        let normalized = self.normalize_text(text);
        if normalized.is_empty() {
            if text.chars().all(char::is_whitespace)
                && self.has_body_content(output)
                && !output.ends_with(' ')
            {
                output.push(' ');
            }
            return;
        }

        if text.starts_with(char::is_whitespace)
            && self.has_body_content(output)
            && !output.ends_with(' ')
        {
            output.push(' ');
        }

        output.push_str(&crate::security::escape_markdown_text(&normalized));

        if text.ends_with(char::is_whitespace) {
            output.push(' ');
        }
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
    fn handle_void_form_control(&self, node: &Handle, output: &mut String) {
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let input_type = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "type")
                .map(|a| a.value.to_string())
                .unwrap_or_default()
                .to_lowercase();

            if matches!(input_type.as_str(), "hidden" | "submit" | "reset" | "image") {
                if matches!(input_type.as_str(), "submit" | "reset")
                    && let Some(value) = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "value")
                        .map(|a| a.value.to_string())
                {
                    let trimmed = value.trim();
                    if !trimmed.is_empty() {
                        output.push_str(&crate::security::escape_markdown_text(trimmed));
                        output.push(' ');
                    }
                }
                return;
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
                .map(|a| a.value.to_string());

            if let Some(text) = text {
                let trimmed = text.trim();
                if !trimmed.is_empty() {
                    output.push_str(&crate::security::escape_markdown_text(trimmed));
                    output.push(' ');
                }
            }
        }
    }

    fn handle_strip_element(
        &self,
        node: &Handle,
        tag_name: &str,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        if self.security_validator.is_embedded_content(tag_name) {
            let embedded = if let NodeData::Element { ref attrs, .. } = node.data {
                let attrs_borrowed = attrs.borrow();
                let url_attr = if tag_name == "object" { "data" } else { "src" };
                let url = attrs_borrowed
                    .iter()
                    .find(|a| a.name.local.as_ref() == url_attr)
                    .map(|a| a.value.to_string());
                let title = attrs_borrowed
                    .iter()
                    .find(|a| a.name.local.as_ref() == "title")
                    .map(|a| a.value.to_string());
                Some((url, title))
            } else {
                None
            };

            if let Some((url, title)) = embedded
                && let Some(url) = url
                && let Some(safe_url) = self.security_validator.sanitize_url(url.trim())
            {
                let resolved_url = self.resolve_url(safe_url);
                Self::emit_markdown_link(&[title.as_deref()], &resolved_url, &resolved_url, output);
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
            "img" => self.handle_image(node, output, depth)?,
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
                self.extract_media_urls(node, tag_name, output);
                self.traverse_children(node, output, depth + 1, ctx)?;
            }
            "source" => self.extract_source_url(node, output),
            "track" => self.extract_track_url(node, output),
            "area" => self.extract_area_link(node, output),
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
        ctx: Option<&mut ConversionContext>,
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
            self.handle_void_form_control(node, output);
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
                self.write_normalized_text_node(text.as_ref(), output);
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
                self.write_normalized_text_node(text.as_ref(), output);
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

    /// Escape a URL destination for use inside a Markdown link.
    ///
    /// If the URL contains characters that would break a bare `(url)`
    /// destination (spaces, parentheses, `<`, `>`), `escape_link_destination`
    /// wraps the URL in angle brackets with both `<` and `>` percent-encoded.
    /// Otherwise the URL is returned unchanged.
    ///
    /// # Arguments
    ///
    /// * `url` - The sanitized URL string to escape.
    ///
    /// # Returns
    ///
    /// A `String` safe for use as a Markdown link destination.
    pub(super) fn escape_link_destination(url: &str) -> String {
        if url.contains(' ')
            || url.contains('(')
            || url.contains(')')
            || url.contains('<')
            || url.contains('>')
        {
            /* Wrap in angle brackets; percent-encode '<' and '>' so they
             * do not break angle-bracket destination semantics. */
            let escaped = url.replace('<', "%3C").replace('>', "%3E");
            format!("<{}>", escaped)
        } else {
            url.to_string()
        }
    }

    /// Escape text for use inside Markdown link/image label brackets.
    ///
    /// Labels are enclosed in `[...]`; unescaped brackets or backslashes from
    /// attacker-controlled HTML text can break out of the label and inject a
    /// new Markdown destination.  Delegates to the canonical
    /// [`crate::security::escape_link_label`] so all label-escaping sites
    /// share a single implementation (AGENTS.md Rule 27).
    pub(super) fn escape_link_label(label: &str) -> String {
        crate::security::escape_link_label(label)
    }

    /// Emit a Markdown link `[label](url)\n` into `output`.
    ///
    /// Centralizes the label-escape and URL-destination-escape logic shared
    /// by the embedded-content, media, source, track, and area emit sites.
    /// Both label and destination are escaped to prevent Markdown injection
    /// (see [`escape_link_label`] and [`escape_link_destination`]).
    ///
    /// # Arguments
    ///
    /// * `label_candidates` - Ordered slice of optional label strings; the
    ///   first non-empty trimmed value is used. This allows callers to
    ///   express a priority chain (e.g., `alt` text before `title` text
    ///   before a URL-derived fallback).
    /// * `fallback_label` - Fallback label when all candidates are empty.
    /// * `safe_url` - Already-sanitized URL from `SecurityValidator`.
    /// * `output` - The output buffer to append to.
    fn emit_markdown_link(
        label_candidates: &[Option<&str>],
        fallback_label: &str,
        safe_url: &str,
        output: &mut String,
    ) {
        let label = label_candidates
            .iter()
            .filter_map(|opt| opt.map(|s| s.trim()).filter(|s| !s.is_empty()))
            .next()
            .unwrap_or(fallback_label);
        let escaped_label = Self::escape_link_label(label);
        let escaped_dest = Self::escape_link_destination(safe_url);
        output.push_str(&format!("[{}]({})", escaped_label, escaped_dest));
        output.push('\n');
    }

    /// Extract `src` and `poster` URLs from `<video>` / `<audio>` elements
    /// as Markdown links so AI agents know what media was referenced.
    ///
    /// For `<video>` elements, both the `src` URL (as a `[title](url)` link)
    /// and the `poster` thumbnail URL (as a `![](url)` image) are emitted.
    /// For `<audio>` elements, only the `src` URL is emitted. All URLs pass
    /// through `SecurityValidator::sanitize_url` before emission.
    fn extract_media_urls(&self, node: &Handle, tag_name: &str, output: &mut String) {
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();

            let src = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "src")
                .map(|a| a.value.to_string());
            let title = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "title")
                .map(|a| a.value.to_string());

            if let Some(u) = src {
                let trimmed = u.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    let resolved_url = self.resolve_url(safe_url);
                    Self::emit_markdown_link(
                        &[title.as_deref()],
                        &resolved_url,
                        &resolved_url,
                        output,
                    );
                }
            }

            // video poster thumbnail
            if tag_name == "video"
                && let Some(poster) = attrs_borrowed
                    .iter()
                    .find(|a| a.name.local.as_ref() == "poster")
                    .map(|a| a.value.to_string())
            {
                let trimmed = poster.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    let resolved_url = self.resolve_url(safe_url);
                    let escaped_dest = Self::escape_link_destination(&resolved_url);
                    output.push_str(&format!("![]({})", escaped_dest));
                    output.push('\n');
                }
            }
        }
    }

    /// Extract `src` from a `<source>` element as a Markdown link.
    fn extract_source_url(&self, node: &Handle, output: &mut String) {
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let src = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "src")
                .map(|a| a.value.to_string());

            if let Some(u) = src {
                let trimmed = u.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    // Use type attribute as context if available
                    let type_attr = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "type")
                        .map(|a| a.value.to_string());
                    let resolved_url = self.resolve_url(safe_url);
                    Self::emit_markdown_link(
                        &[type_attr.as_deref()],
                        &resolved_url,
                        &resolved_url,
                        output,
                    );
                }
            }
        }
    }

    /// Extract `src` and `label` from a `<track>` element as a Markdown link.
    fn extract_track_url(&self, node: &Handle, output: &mut String) {
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let src = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "src")
                .map(|a| a.value.to_string());

            if let Some(u) = src {
                let trimmed = u.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    let label = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "label")
                        .map(|a| a.value.to_string());
                    let resolved_url = self.resolve_url(safe_url);
                    Self::emit_markdown_link(
                        &[label.as_deref()],
                        &resolved_url,
                        &resolved_url,
                        output,
                    );
                }
            }
        }
    }

    /// Extract `href` and `alt` from an `<area>` element as a Markdown link.
    fn extract_area_link(&self, node: &Handle, output: &mut String) {
        if let NodeData::Element { ref attrs, .. } = node.data {
            let attrs_borrowed = attrs.borrow();
            let href = attrs_borrowed
                .iter()
                .find(|a| a.name.local.as_ref() == "href")
                .map(|a| a.value.to_string());

            if let Some(u) = href {
                let trimmed = u.trim();
                if let Some(safe_url) = self.security_validator.sanitize_url(trimmed) {
                    let alt = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "alt")
                        .map(|a| a.value.to_string());
                    let title = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "title")
                        .map(|a| a.value.to_string());
                    let resolved_url = self.resolve_url(safe_url);
                    Self::emit_markdown_link(
                        &[alt.as_deref(), title.as_deref()],
                        &resolved_url,
                        &resolved_url,
                        output,
                    );
                }
            }
        }
    }
}
