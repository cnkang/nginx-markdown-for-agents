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
//! # Title Escaping
//!
//! [`escape_markdown_title`] applies minimal escaping for link title attributes
//! (backslash and double-quote) to produce valid Markdown link syntax.

use super::*;

impl MarkdownConverter {
    /// Escape backslash and double-quote characters in a Markdown link title.
    ///
    /// Markdown link titles are enclosed in double quotes, so backslashes and
    /// quotes inside the title must be escaped to produce valid syntax.
    ///
    /// # Arguments
    ///
    /// * `title` - Raw title string to escape
    ///
    /// # Returns
    ///
    /// Escaped title safe for use inside `"..."` in a Markdown link.
    fn escape_markdown_title(title: &str) -> String {
        title.replace('\\', "\\\\").replace('"', "\\\"")
    }

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
        let href = if let NodeData::Element { ref attrs, .. } = node.data {
            attrs
                .borrow()
                .iter()
                .find(|attr| attr.name.local.as_ref() == "href")
                .map(|attr| attr.value.to_string())
        } else {
            None
        };

        let mut link_text = String::new();
        let mut ctx = ctx;
        for child in node.children.borrow().iter() {
            self.extract_text(child, &mut link_text, depth + 1, ctx.as_deref_mut())?;
        }
        let normalized_text = self.normalize_text(&link_text);

        if let Some(url) = href {
            if let Some(safe_url) = self.security_validator.sanitize_url(&url) {
                if !normalized_text.is_empty() {
                    output.push('[');
                    output.push_str(&normalized_text);
                    output.push_str("](");
                    output.push_str(safe_url);
                    output.push(')');
                }
            } else if !normalized_text.is_empty() {
                output.push_str(&normalized_text);
            }
        } else if !normalized_text.is_empty() {
            output.push_str(&normalized_text);
        }

        Ok(())
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
    ) -> Result<(), ConversionError> {
        let (src, alt, title) = if let NodeData::Element { ref attrs, .. } = node.data {
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
            let title = attrs_borrowed
                .iter()
                .find(|attr| attr.name.local.as_ref() == "title")
                .map(|attr| attr.value.to_string());
            (src, alt, title)
        } else {
            (None, String::new(), None)
        };

        let safe_url = src
            .as_deref()
            .and_then(|u| self.security_validator.sanitize_url(u));

        if let Some(url) = safe_url {
            output.push_str("![");
            output.push_str(&alt);
            output.push_str("](");
            output.push_str(url);
            if let Some(ref t) = title {
                let trimmed = t.trim();
                if !trimmed.is_empty() {
                    output.push_str(" \"");
                    output.push_str(&Self::escape_markdown_title(trimmed));
                    output.push('"');
                }
            }
            output.push(')');
        } else if !alt.trim().is_empty() {
            // URL missing or dangerous — preserve alt text for AI agents
            output.push_str(alt.trim());
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
        let mut code_content = String::new();
        self.extract_code_content(node, &mut code_content, depth, ctx)?;
        let fence = "`".repeat(self.longest_backtick_run(&code_content) + 1);
        output.push_str(&fence);
        output.push_str(&code_content);
        output.push_str(&fence);
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
        output.push_str("**");
        self.traverse_children(node, output, depth + 1, ctx)?;
        output.push_str("**");
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
        output.push('*');
        self.traverse_children(node, output, depth + 1, ctx)?;
        output.push('*');
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
            NodeData::Text { ref contents } => output.push_str(&contents.borrow()),
            NodeData::Element { ref name, .. } => {
                if matches!(name.local.as_ref(), "script" | "style" | "noscript") {
                    return Ok(());
                }

                for child in node.children.borrow().iter() {
                    self.extract_code_content(child, output, depth + 1, ctx.as_deref_mut())?;
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
            NodeData::Text { ref contents } => output.push_str(&contents.borrow()),
            NodeData::Element { ref name, .. } => {
                if matches!(name.local.as_ref(), "script" | "style" | "noscript") {
                    return Ok(());
                }

                for child in node.children.borrow().iter() {
                    self.extract_text(child, output, depth + 1, ctx.as_deref_mut())?;
                }
            }
            _ => {}
        }
        Ok(())
    }
}
