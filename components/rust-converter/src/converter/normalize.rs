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
    let mut fence_len: usize = 0;
    let mut chars = line.char_indices().peekable();

    while let Some((_, ch)) = chars.next() {
        if ch == '`' {
            let run_len = consume_backtick_run(&mut chars);
            if !in_inline_code {
                in_inline_code = true;
                fence_len = run_len;
            } else if run_len == fence_len {
                in_inline_code = false;
                fence_len = 0;
            }
            for _ in 0..run_len {
                result.push('`');
            }
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

fn consume_backtick_run(chars: &mut std::iter::Peekable<std::str::CharIndices>) -> usize {
    let mut run_len = 1;
    while let Some(&(_, next)) = chars.peek() {
        if next != '`' {
            break;
        }
        chars.next();
        run_len += 1;
    }
    run_len
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
        let mut active_fence_len: Option<usize> = None;

        for line in output.lines() {
            let fence_len = measure_fence_len(line);
            let is_opening_fence = active_fence_len.is_none() && fence_len >= 3;
            let is_closing_fence = active_fence_len
                .map(|len| fence_len >= len && line.trim_start()[fence_len..].trim().is_empty())
                .unwrap_or(false);

            if is_opening_fence || is_closing_fence {
                if is_opening_fence {
                    active_fence_len = Some(fence_len);
                } else {
                    active_fence_len = None;
                }
                result.push_str(line.trim_end());
                result.push('\n');
                prev_blank = false;
                continue;
            }

            if active_fence_len.is_some() {
                result.push_str(line);
                result.push('\n');
                prev_blank = false;
                continue;
            }

            let trimmed = line.trim_end();
            if trimmed.is_empty() {
                if !prev_blank {
                    result.push('\n');
                    prev_blank = true;
                }
            } else {
                let normalized = normalize_line_whitespace(trimmed);
                result.push_str(&normalized);
                result.push('\n');
                prev_blank = false;
            }
        }

        fix_trailing_newlines(result)
    }
}

/// Count the leading whitespace of `line` in Markdown columns.
///
/// CommonMark indentation counts columns, not bytes: a tab advances to
/// the next four-column tab stop.  For example `\t` reaches column 4,
/// ` \t` reaches column 4, and `\t\t` reaches column 8.
fn leading_indent_columns(line: &str) -> usize {
    let mut columns = 0usize;
    for byte in line.bytes() {
        match byte {
            b' ' => columns += 1,
            b'\t' => columns = (columns / 4 + 1) * 4,
            _ => break,
        }
    }
    columns
}

fn measure_fence_len(line: &str) -> usize {
    // CommonMark: a fence must be indented at most 3 spaces.  A line
    // indented 4+ columns is an indented code block, not a fence, so
    // count the backtick run only when the leading indentation is
    // within the fence limit.  A leading tab advances to column 4 (or
    // beyond), so a tab-indented fence is never recognized.
    if leading_indent_columns(line) > 3 {
        return 0;
    }
    line.trim_start().bytes().take_while(|&b| b == b'`').count()
}

fn fix_trailing_newlines(mut result: String) -> String {
    if !result.ends_with('\n') {
        result.push('\n');
    } else if result.ends_with("\n\n") {
        while result.ends_with("\n\n") {
            result.pop();
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fence_requires_at_most_three_space_indent() {
        // CommonMark: 4+ space indent is an indented code block, not a
        // fence.  A 4-space-indented backtick run must NOT open a fence.
        assert_eq!(measure_fence_len("    ```"), 0);
        assert_eq!(measure_fence_len("   ```"), 3);
        assert_eq!(measure_fence_len("  ```"), 3);
        assert_eq!(measure_fence_len(" ```"), 3);
        assert_eq!(measure_fence_len("```"), 3);
        assert_eq!(measure_fence_len("    ````"), 0);
    }

    #[test]
    fn fence_indent_counts_columns_with_tabs() {
        // CommonMark indentation counts columns, not bytes.  A tab
        // advances to the next four-column tab stop, so any leading tab
        // reaches column 4 or beyond and the backtick run is indented
        // code, never a fence.
        assert_eq!(measure_fence_len("\t```"), 0, "tab reaches column 4");
        assert_eq!(measure_fence_len(" \t```"), 0, "space+tab reaches column 4");
        assert_eq!(measure_fence_len("  \t```"), 0, "2 spaces+tab reaches column 4");
        assert_eq!(measure_fence_len("   \t```"), 0, "3 spaces+tab reaches column 4");
        assert_eq!(measure_fence_len("\t\t```"), 0, "tab+tab reaches column 8");
        // A tab inside the 3-column limit cannot exist: the first tab
        // always jumps to column 4.  Pure-space prefixes stay bounded.
        assert_eq!(measure_fence_len(" ```"), 3);
        assert_eq!(measure_fence_len("  ```"), 3);
        assert_eq!(measure_fence_len("   ```"), 3);
    }

    #[test]
    fn normalize_output_tab_indented_backticks_are_code_not_fence() {
        // A tab-indented backtick run is indented code content (column 4);
        // the normalizer must not enter fence mode and must collapse its
        // internal whitespace like any other non-fence line.
        let converter = MarkdownConverter::with_options(Default::default());
        let out = converter.normalize_output("\t```\n\ta  b\n\t```\n".to_string());
        assert!(
            out.contains("a b"),
            "tab-indented code content normalized: {out:?}"
        );
        assert!(
            !out.contains("a  b"),
            "no fence mode entered (double space would be preserved): {out:?}"
        );
    }

    #[test]
    fn normalize_output_does_not_treat_indented_backticks_as_fence() {
        // A 4-space-indented ``` line is indented code content; the
        // normalizer must not enter fence mode and must collapse its
        // internal whitespace like any other non-fence line.
        let converter = MarkdownConverter::with_options(Default::default());
        let out = converter.normalize_output("    ```\n    a  b\n    ```\n".to_string());
        // Indented code content is normalized (spaces collapsed) and no
        // fence state is entered.  In fence mode the double space would
        // be preserved verbatim.
        assert!(
            out.contains("a b"),
            "indented code content normalized: {out:?}"
        );
        assert!(
            !out.contains("a  b"),
            "no fence mode entered (double space would be preserved): {out:?}"
        );
    }
}
