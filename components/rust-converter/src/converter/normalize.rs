use super::*;

impl MarkdownConverter {
    /// Normalize text content.
    pub(super) fn normalize_text(&self, text: &str) -> String {
        let words: Vec<&str> = text.split_whitespace().collect();
        words.join(" ")
    }

    /// Normalize final output for deterministic Markdown generation.
    pub(super) fn normalize_output(&self, output: String) -> String {
        let output = output.replace("\r\n", "\n");

        let mut result = String::with_capacity(output.len());
        let mut prev_blank = false;
        let mut in_code_block = false;

        for line in output.lines() {
            if line.trim_start().starts_with("```") {
                in_code_block = !in_code_block;
            }

            let trimmed = line.trim_end();

            if trimmed.is_empty() {
                if !prev_blank {
                    result.push('\n');
                    prev_blank = true;
                }
            } else {
                if in_code_block {
                    result.push_str(trimmed);
                } else {
                    let normalized = self.normalize_line_whitespace(trimmed);
                    result.push_str(&normalized);
                }
                result.push('\n');
                prev_blank = false;
            }
        }

        if !result.ends_with('\n') {
            result.push('\n');
        } else if result.ends_with("\n\n") {
            while result.ends_with("\n\n") {
                result.pop();
            }
        }

        result
    }

    /// Normalize whitespace within a single line.
    pub(super) fn normalize_line_whitespace(&self, line: &str) -> String {
        let mut result = String::with_capacity(line.len());
        let mut prev_space = false;
        let mut at_start = true;
        let mut in_inline_code = false;

        for ch in line.chars() {
            if ch == '`' {
                in_inline_code = !in_inline_code;
                result.push(ch);
                prev_space = false;
                at_start = false;
            } else if ch == ' ' {
                if in_inline_code || at_start {
                    result.push(ch);
                } else if !prev_space {
                    result.push(ch);
                    prev_space = true;
                }
            } else {
                result.push(ch);
                prev_space = false;
                at_start = false;
            }
        }

        result
    }
}
