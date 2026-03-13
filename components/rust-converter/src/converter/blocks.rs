use super::*;

impl MarkdownConverter {
    fn format_list_item_lines(
        &self,
        output: &mut String,
        content: &str,
        depth: usize,
        ordered: bool,
    ) {
        let base_indent = "  ".repeat(depth);
        let marker = if ordered { "1. " } else { "- " };
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
            } else if !line.is_empty() {
                output.push_str(&continuation_indent);
            }

            output.push_str(line);
            output.push('\n');
        }
    }

    fn longest_backtick_run(&self, content: &str) -> usize {
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

    fn choose_code_fence(&self, content: &str) -> String {
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
        _ctx: Option<&mut ConversionContext>,
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

        let mut code_content = String::new();
        self.extract_code_content(node, &mut code_content)?;
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
}
