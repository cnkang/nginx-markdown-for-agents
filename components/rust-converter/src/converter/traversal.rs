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
        match ctx {
            Some(ctx) => {
                for child in node.children.borrow().iter() {
                    self.traverse_node_with_context(child, output, depth, ctx)?;
                }
            }
            None => {
                for child in node.children.borrow().iter() {
                    self.traverse_node(child, output, depth)?;
                }
            }
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

        let sanitize_action = self.security_validator.check_element(tag_name);
        if matches!(sanitize_action, SanitizeAction::Remove) {
            return Ok(());
        }

        self.security_validator
            .validate_depth(depth)
            .map_err(ConversionError::InvalidInput)?;

        match tag_name {
            "h1" => self.handle_heading(node, 1, output, depth)?,
            "h2" => self.handle_heading(node, 2, output, depth)?,
            "h3" => self.handle_heading(node, 3, output, depth)?,
            "h4" => self.handle_heading(node, 4, output, depth)?,
            "h5" => self.handle_heading(node, 5, output, depth)?,
            "h6" => self.handle_heading(node, 6, output, depth)?,
            "p" => self.handle_paragraph(node, output, depth)?,
            "a" => self.handle_link(node, output, depth)?,
            "img" => self.handle_image(node, output, depth)?,
            "ul" => self.handle_list(node, output, 0, false)?,
            "ol" => self.handle_list(node, output, 0, true)?,
            "li" => self.handle_list_item(node, output, 0)?,
            "pre" => self.handle_code_block(node, output, depth)?,
            "code" => self.handle_inline_code(node, output, depth)?,
            "strong" | "b" => self.handle_bold(node, output, depth)?,
            "em" | "i" => self.handle_italic(node, output, depth)?,
            "table" => self.handle_table(node, output, depth)?,
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
