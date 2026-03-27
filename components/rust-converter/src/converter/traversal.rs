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

        output.push_str(&normalized);

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
            Some(ctx) => self.traverse_node_with_context(node, output, depth, ctx),
            None => self.traverse_node(node, output, depth),
        }
    }

    /// Internal element dispatcher shared by context and non-context entry points.
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
        // pruned noise regions (nav/footer/aside when feature-enabled).
        match super::pruning::should_prune(tag_name) {
            super::pruning::PruneDecision::SkipChildren
            | super::pruning::PruneDecision::SkipSubtree => return Ok(()),
            super::pruning::PruneDecision::Traverse => {}
        }

        let sanitize_action = self.security_validator.check_element(tag_name);
        if matches!(sanitize_action, SanitizeAction::Remove) {
            return Ok(());
        }

        // Void form controls (<input>): extract descriptive text from attributes
        // (placeholder, value, aria-label) so AI agents see the semantic content
        // without raw HTML leaking into the Markdown output.
        if self.security_validator.is_void_form_control(tag_name) {
            if let NodeData::Element { ref attrs, .. } = node.data {
                let attrs_borrowed = attrs.borrow();
                let input_type = attrs_borrowed
                    .iter()
                    .find(|a| a.name.local.as_ref() == "type")
                    .map(|a| a.value.to_string())
                    .unwrap_or_default()
                    .to_lowercase();

                // Skip hidden and submit-like inputs that carry no user-visible text
                if matches!(input_type.as_str(), "hidden" | "submit" | "reset" | "image") {
                    // For submit/reset, extract value as button-like text
                    if matches!(input_type.as_str(), "submit" | "reset")
                        && let Some(val) = attrs_borrowed
                            .iter()
                            .find(|a| a.name.local.as_ref() == "value")
                            .map(|a| a.value.to_string())
                    {
                        let trimmed = val.trim();
                        if !trimmed.is_empty() {
                            output.push_str(trimmed);
                            output.push(' ');
                        }
                    }
                    return Ok(());
                }

                // Extract the most descriptive text: aria-label > placeholder > value
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

                if let Some(t) = text {
                    let trimmed = t.trim();
                    if !trimmed.is_empty() {
                        output.push_str(trimmed);
                        output.push(' ');
                    }
                }
            }
            return Ok(());
        }

        // Form container elements: strip the tag but traverse children so
        // their text content is preserved in the Markdown output.
        if matches!(sanitize_action, SanitizeAction::StripElement) {
            self.security_validator
                .validate_depth(depth)
                .map_err(ConversionError::InvalidInput)?;

            // Embedded content elements (<iframe>, <object>): extract the
            // src/data URL as a Markdown link so AI agents know what was
            // embedded, then traverse fallback child content.
            if self.security_validator.is_embedded_content(tag_name)
                && let NodeData::Element { ref attrs, .. } = node.data
            {
                let attrs_borrowed = attrs.borrow();
                // iframe uses "src", object uses "data"
                let url_attr = if tag_name == "object" { "data" } else { "src" };
                let url = attrs_borrowed
                    .iter()
                    .find(|a| a.name.local.as_ref() == url_attr)
                    .map(|a| a.value.to_string());
                let title = attrs_borrowed
                    .iter()
                    .find(|a| a.name.local.as_ref() == "title")
                    .map(|a| a.value.to_string());

                if let Some(u) = url {
                    let trimmed_url = u.trim();
                    if !trimmed_url.is_empty()
                        && !self.security_validator.is_dangerous_url(trimmed_url)
                    {
                        let label = title
                            .as_deref()
                            .map(|t| t.trim())
                            .filter(|t| !t.is_empty())
                            .unwrap_or(trimmed_url);
                        output.push_str(&format!("[{}]({})", label, trimmed_url));
                        output.push('\n');
                    }
                }
            }

            return self.traverse_children(node, output, depth + 1, ctx);
        }

        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        // All branches share one mutable timeout context, so we reborrow it for
        // each handler call to satisfy the borrow checker and keep call sites tidy.
        let mut ctx = ctx;
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
            // List indentation must track list nesting, not arbitrary DOM depth.
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
            // Media elements: extract src/poster URLs as links, then traverse
            // fallback children so AI agents see both the resource URL and any
            // alternative text the author provided.
            "video" | "audio" => {
                self.extract_media_urls(node, tag_name, output);
                self.traverse_children(node, output, depth + 1, ctx)?;
            }
            // Void media children: extract src as a link so the resource URL
            // is not silently lost.
            "source" => self.extract_source_url(node, output),
            "track" => self.extract_track_url(node, output),
            // Image map areas: extract as [alt](href) links.
            "area" => self.extract_area_link(node, output),
            _ => self.traverse_children(node, output, depth + 1, ctx)?,
        }

        Ok(())
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

        Ok(())
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

    /// Extract `src` and `poster` URLs from `<video>` / `<audio>` elements
    /// as Markdown links so AI agents know what media was referenced.
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
                if !trimmed.is_empty() && !self.security_validator.is_dangerous_url(trimmed) {
                    let label = title
                        .as_deref()
                        .map(|t| t.trim())
                        .filter(|t| !t.is_empty())
                        .unwrap_or(trimmed);
                    output.push_str(&format!("[{}]({})", label, trimmed));
                    output.push('\n');
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
                if !trimmed.is_empty() && !self.security_validator.is_dangerous_url(trimmed) {
                    output.push_str(&format!("![]({})", trimmed));
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
                if !trimmed.is_empty() && !self.security_validator.is_dangerous_url(trimmed) {
                    // Use type attribute as context if available
                    let type_attr = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "type")
                        .map(|a| a.value.to_string());
                    let label = type_attr
                        .as_deref()
                        .map(|t| t.trim())
                        .filter(|t| !t.is_empty())
                        .unwrap_or(trimmed);
                    output.push_str(&format!("[{}]({})", label, trimmed));
                    output.push('\n');
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
                if !trimmed.is_empty() && !self.security_validator.is_dangerous_url(trimmed) {
                    let label = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "label")
                        .map(|a| a.value.to_string());
                    let display = label
                        .as_deref()
                        .map(|t| t.trim())
                        .filter(|t| !t.is_empty())
                        .unwrap_or(trimmed);
                    output.push_str(&format!("[{}]({})", display, trimmed));
                    output.push('\n');
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
                if !trimmed.is_empty() && !self.security_validator.is_dangerous_url(trimmed) {
                    let alt = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "alt")
                        .map(|a| a.value.to_string());
                    let title = attrs_borrowed
                        .iter()
                        .find(|a| a.name.local.as_ref() == "title")
                        .map(|a| a.value.to_string());
                    let display = alt
                        .as_deref()
                        .map(|t| t.trim())
                        .filter(|t| !t.is_empty())
                        .or_else(|| title.as_deref().map(|t| t.trim()).filter(|t| !t.is_empty()))
                        .unwrap_or(trimmed);
                    output.push_str(&format!("[{}]({})", display, trimmed));
                    output.push('\n');
                }
            }
        }
    }
}
