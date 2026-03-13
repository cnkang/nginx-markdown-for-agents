use super::*;

impl MarkdownConverter {
    /// Handle anchor (link) elements.
    pub(super) fn handle_link(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
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
        for child in node.children.borrow().iter() {
            self.extract_text(child, &mut link_text)?;
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
    pub(super) fn handle_image(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
    ) -> Result<(), ConversionError> {
        let (src, alt) = if let NodeData::Element { ref attrs, .. } = node.data {
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
            (src, alt)
        } else {
            (None, String::new())
        };

        if let Some(url) = src
            && let Some(safe_url) = self.security_validator.sanitize_url(&url)
        {
            output.push_str("![");
            output.push_str(&alt);
            output.push_str("](");
            output.push_str(safe_url);
            output.push(')');
        }

        Ok(())
    }

    /// Handle inline code elements.
    pub(super) fn handle_inline_code(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
    ) -> Result<(), ConversionError> {
        let mut code_content = String::new();
        self.extract_code_content(node, &mut code_content)?;
        output.push('`');
        output.push_str(&code_content);
        output.push('`');
        Ok(())
    }

    /// Handle bold/strong elements.
    pub(super) fn handle_bold(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        output.push_str("**");
        for child in node.children.borrow().iter() {
            self.traverse_node(child, output, depth + 1)?;
        }
        output.push_str("**");
        Ok(())
    }

    /// Handle italic/emphasis elements.
    pub(super) fn handle_italic(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        output.push('*');
        for child in node.children.borrow().iter() {
            self.traverse_node(child, output, depth + 1)?;
        }
        output.push('*');
        Ok(())
    }

    /// Extract code content from a node without any normalization.
    pub(super) fn extract_code_content(
        &self,
        node: &Handle,
        output: &mut String,
    ) -> Result<(), ConversionError> {
        match node.data {
            NodeData::Text { ref contents } => output.push_str(&contents.borrow()),
            NodeData::Element { .. } => {
                for child in node.children.borrow().iter() {
                    self.extract_code_content(child, output)?;
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
    ) -> Result<(), ConversionError> {
        match node.data {
            NodeData::Text { ref contents } => output.push_str(&contents.borrow()),
            NodeData::Element { .. } => {
                for child in node.children.borrow().iter() {
                    self.extract_text(child, output)?;
                }
            }
            _ => {}
        }
        Ok(())
    }
}
