use super::*;

impl MarkdownConverter {
    /// Handle heading elements (h1-h6).
    pub(super) fn handle_heading(
        &self,
        node: &Handle,
        level: usize,
        output: &mut String,
        depth: usize,
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
        for child in node.children.borrow().iter() {
            self.traverse_node(child, output, depth + 1)?;
        }

        let heading_content = output[start_len..].to_string();
        let normalized = self.normalize_text(&heading_content);
        output.truncate(start_len);
        output.push_str(&normalized);
        output.push_str("\n\n");

        Ok(())
    }

    /// Handle paragraph elements.
    pub(super) fn handle_paragraph(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        let start_len = output.len();
        for child in node.children.borrow().iter() {
            self.traverse_node(child, output, depth + 1)?;
        }

        if output.len() > start_len {
            output.push_str("\n\n");
        }

        Ok(())
    }

    /// Handle list elements (ul/ol).
    pub(super) fn handle_list(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ordered: bool,
    ) -> Result<(), ConversionError> {
        if !output.is_empty() && !output.ends_with("\n\n") {
            if output.ends_with('\n') {
                output.push('\n');
            } else {
                output.push_str("\n\n");
            }
        }

        for child in node.children.borrow().iter() {
            if let NodeData::Element { ref name, .. } = child.data
                && name.local.as_ref() == "li"
            {
                self.handle_list_item_with_marker(child, output, depth, ordered)?;
            }
        }

        if !output.ends_with("\n\n") {
            output.push('\n');
        }

        Ok(())
    }

    /// Handle list item elements (li).
    pub(super) fn handle_list_item(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
    ) -> Result<(), ConversionError> {
        self.handle_list_item_with_marker(node, output, depth, false)
    }

    /// Handle list item elements with specific marker type.
    pub(super) fn handle_list_item_with_marker(
        &self,
        node: &Handle,
        output: &mut String,
        depth: usize,
        ordered: bool,
    ) -> Result<(), ConversionError> {
        for _ in 0..depth {
            output.push_str("  ");
        }

        if ordered {
            output.push_str("1. ");
        } else {
            output.push_str("- ");
        }

        let start_len = output.len();
        for child in node.children.borrow().iter() {
            match child.data {
                NodeData::Element { ref name, .. } => {
                    let tag_name = name.local.as_ref();
                    if tag_name == "ul" {
                        if output.len() > start_len && !output.ends_with('\n') {
                            output.push('\n');
                        }
                        self.handle_list(child, output, depth + 1, false)?;
                    } else if tag_name == "ol" {
                        if output.len() > start_len && !output.ends_with('\n') {
                            output.push('\n');
                        }
                        self.handle_list(child, output, depth + 1, true)?;
                    } else {
                        self.traverse_node(child, output, depth + 1)?;
                    }
                }
                _ => self.traverse_node(child, output, depth + 1)?,
            }
        }

        if !output.ends_with('\n') {
            output.push('\n');
        }

        Ok(())
    }

    /// Handle code block elements (pre/code).
    pub(super) fn handle_code_block(
        &self,
        node: &Handle,
        output: &mut String,
        _depth: usize,
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
                                language = lang.to_string();
                                break;
                            } else if let Some(lang) = class.strip_prefix("lang-") {
                                language = lang.to_string();
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

        output.push_str("```");
        if !language.is_empty() {
            output.push_str(&language);
        }
        output.push('\n');

        self.extract_code_content(node, output)?;

        if !output.ends_with('\n') {
            output.push('\n');
        }
        output.push_str("```");
        output.push('\n');
        output.push('\n');

        Ok(())
    }
}
