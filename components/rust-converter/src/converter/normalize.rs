//! Output normalization for deterministic Markdown generation.
//!
//! This module provides whitespace and structural normalization that ensures
//! the converter produces deterministic, well-formed Markdown output suitable
//! for reliable ETag generation and caching.
//!
//! # Normalization Rules
//!
//! 1. **CRLF → LF**: All Windows-style line endings are converted to Unix-style.
//! 2. **Blank line collapse**: Runs of 3+ consecutive newlines are collapsed to
//!    exactly 2 (one blank line between blocks).
//! 3. **Trailing whitespace removal**: Spaces and tabs at end of lines are stripped.
//! 4. **Consecutive space collapse**: Runs of multiple spaces are collapsed to one,
//!    except inside inline code spans (`` ` ``) and code blocks (` ``` `).
//! 5. **List indentation preservation**: Leading spaces for nested list items are
//!    kept intact (2-space indentation per nesting level).
//! 6. **Single trailing newline**: Output always ends with exactly one `\n`.
//!
//! # Two Normalization Paths
//!
//! - **Small documents** use [`MarkdownConverter::normalize_output`], which
//!   performs a full two-pass normalization (CRLF replacement + line-by-line
//!   whitespace collapse).
//! - **Large documents** (output > 256 KB) use [`FusedNormalizer`] from the
//!   `large_response` module, which fuses CRLF normalization and whitespace
//!   collapse into a single pass to avoid allocating a second full-size string.

use super::*;

/// Normalize whitespace within a single line.
///
/// Collapses runs of multiple spaces into a single space, while preserving:
/// - Leading indentation (spaces at the start of the line).
/// - Content inside inline code spans (backtick-delimited).
///
/// This is the single canonical implementation used by both
/// `MarkdownConverter::normalize_output` (small-document path) and
/// `FusedNormalizer::push_line` (large-document path).
pub(crate) fn normalize_line_whitespace(line: &str) -> String {
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
                    let normalized = normalize_line_whitespace(trimmed);
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
}
