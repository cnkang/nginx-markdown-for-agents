use super::*;

impl MarkdownConverter {
    pub(super) fn write_normalized_text_node(&self, text: &str, output: &mut String) {
        let normalized = self.normalize_text(text);
        if normalized.is_empty() {
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

    pub(super) fn traverse_children(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        let mut ctx = ctx;
        for child in node.children.borrow().iter() {
            self.traverse_node_optional(child, output, depth, ctx.as_deref_mut())?;
        }

        Ok(())
    }

    pub(super) fn traverse_node_optional(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ctx: Option<&mut ConversionContext>,
    ) -> Result<(), ConversionError> {
        match ctx {
            Some(ctx) => self.traverse_node_with_context(node, output, depth, ctx),
            None => self.traverse_node(node, output, depth),
        }
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

        let sanitize_action = self.security_validator.check_element(tag_name);
        if matches!(sanitize_action, SanitizeAction::Remove) {
            return Ok(());
        }

        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

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
            "code" => self.handle_inline_code(node, output, depth)?,
            "strong" | "b" => {
                self.handle_bold_with_context(node, output, depth, ctx.as_deref_mut())?
            }
            "em" | "i" => {
                self.handle_italic_with_context(node, output, depth, ctx.as_deref_mut())?
            }
            "table" => self.handle_table_with_context(node, output, depth, ctx.as_deref_mut())?,
            "script" | "style" | "noscript" => {}
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
}
