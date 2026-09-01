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

use super::MarkdownConverter;

/// Space-collapsing state for one line being normalized.
///
/// Tracks whether any visible character was emitted yet (leading-indent
/// mode), whether the previous emitted character was a collapsible space,
/// and the open inline-code span delimiter.
#[derive(Debug)]
struct LineWhitespaceState {
    /// The last emitted character was a collapsible space.
    prev_space: bool,
    /// No visible character emitted yet; spaces are leading indentation.
    at_start: bool,
    /// Currently inside a backtick-delimited inline code span.
    in_inline_code: bool,
    /// Delimiter length of the current inline-code span.
    span_delim: usize,
}

impl LineWhitespaceState {
    fn new() -> Self {
        Self {
            prev_space: false,
            at_start: true,
            in_inline_code: false,
            span_delim: 0,
        }
    }

    /// Apply a consumed backtick run to the inline-code state.
    ///
    /// An unmatched run opens a span with that delimiter length; only a run
    /// of the same length closes it, matching CommonMark code-span rules.
    fn toggle_backtick_run(&mut self, run_len: usize) {
        if !self.in_inline_code {
            self.in_inline_code = true;
            self.span_delim = run_len;
        } else if run_len == self.span_delim {
            self.in_inline_code = false;
            self.span_delim = 0;
        }
    }

    /// Record an emitted visible character: collapse tracking restarts and
    /// leading-indentation mode ends.
    fn note_visible_char(&mut self) {
        self.prev_space = false;
        self.at_start = false;
    }

    /// Consume a literal space and report whether it survives normalization.
    ///
    /// Spaces stay verbatim inside inline code spans and in leading
    /// indentation. Outside code spans, consecutive spaces collapse: the
    /// first separating space survives and marks the position as separated
    /// so followers drop.
    fn keeps_space_verbatim(&mut self) -> bool {
        if self.in_inline_code || self.at_start {
            return true;
        }
        if self.prev_space {
            return false;
        }
        self.prev_space = true;
        true
    }
}

/// Normalize whitespace within a single line for the allocating test
/// reference implementation.
///
/// Collapses runs of multiple spaces into a single space, while preserving:
/// - Leading indentation (spaces at the start of the line).
/// - Content inside inline code spans (backtick-delimited).
///
/// Production paths use [`normalize_line_whitespace_into`] so a large line
/// does not require a second temporary `String`.
#[cfg(test)]
pub(crate) fn normalize_line_whitespace(line: &str) -> String {
    let mut result = String::with_capacity(line.len());
    normalize_line_whitespace_into(line, &mut result);
    result
}

/// Normalize one line directly into an existing output buffer.
///
/// Keeping the canonical state machine separate from the allocating wrapper
/// lets the large-response and full-buffer normalizers avoid a per-line
/// temporary `String`.  The emitted bytes are identical to
/// [`normalize_line_whitespace`].
pub(crate) fn normalize_line_whitespace_into(line: &str, result: &mut String) {
    let mut spacing = LineWhitespaceState::new();
    let mut chars = line.char_indices().peekable();

    while let Some((_, ch)) = chars.next() {
        match ch {
            '`' => {
                let run_len = consume_backtick_run(&mut chars);
                spacing.toggle_backtick_run(run_len);
                for _ in 0..run_len {
                    result.push('`');
                }
                spacing.note_visible_char();
            }
            ' ' => {
                if spacing.keeps_space_verbatim() {
                    result.push(ch);
                }
            }
            _ => {
                result.push(ch);
                spacing.note_visible_char();
            }
        }
    }
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
        let mut normalized = String::with_capacity(text.len());
        for word in text.split_whitespace() {
            if !normalized.is_empty() {
                normalized.push(' ');
            }
            normalized.push_str(word);
        }
        normalized
    }

    /// Normalizes Markdown output for deterministic generation while preserving fenced-code content.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let converter = MarkdownConverter::default();
    /// let normalized = converter.normalize_output("Title  \r\n\r\n\r\nBody".into());
    ///
    /// assert_eq!(normalized, "Title\n\nBody\n");
    /// ```
    ///
    /// # Returns
    ///
    /// The normalized Markdown text with consistent line endings, whitespace, blank lines, and one trailing newline.
    pub(super) fn normalize_output(&self, output: String) -> String {
        let output = if output.contains("\r\n") {
            output.replace("\r\n", "\n")
        } else {
            output
        };

        let mut result = String::with_capacity(output.len());
        let mut prev_blank = false;
        let mut active_fence: Option<(u8, usize)> = None;

        for line in output.lines() {
            let fence = measure_fence(line);
            let fence_line = line.trim_start_matches([' ', '\t']);
            let fence_info = fence
                .and_then(|(_, len)| fence_line.get(len..))
                .unwrap_or("");

            if active_fence.is_none() && opens_fence(fence, fence_info) {
                active_fence = fence;
                result.push_str(line.trim_end());
                result.push('\n');
                prev_blank = false;
                continue;
            }
            if closes_fence(active_fence, fence, fence_info) {
                active_fence = None;
                result.push_str(line.trim_end());
                result.push('\n');
                prev_blank = false;
                continue;
            }
            if active_fence.is_some() {
                result.push_str(line);
                result.push('\n');
                prev_blank = false;
                continue;
            }

            prev_blank = append_non_code_line(&mut result, line, prev_blank);
        }

        fix_trailing_newlines(result)
    }
}

/// Whether a fence marker with no fence already active opens a new block.
///
/// CommonMark rules: at least three marker characters are required, and a
/// backtick fence additionally rejects backticks inside its info string
/// (tilde fences accept any info string here).
pub(crate) fn opens_fence(fence: Option<(u8, usize)>, fence_info: &str) -> bool {
    matches!(
        fence,
        Some((ch, len)) if len >= 3 && (ch == b'~' || !fence_info.contains('`'))
    )
}

/// Whether a fence marker closes the currently active fenced block.
///
/// A closing fence must reuse the opening character, reach at least the
/// opening length, and carry nothing besides whitespace on the line.
pub(crate) fn closes_fence(
    active_fence: Option<(u8, usize)>,
    fence: Option<(u8, usize)>,
    fence_info: &str,
) -> bool {
    match (active_fence, fence) {
        (Some((active_ch, active_len)), Some((ch, len))) => {
            ch == active_ch && len >= active_len && fence_info.trim().is_empty()
        }
        _ => false,
    }
}

/// Append one line that sits outside every fenced code block.
///
/// Trailing whitespace is removed, consecutive blank lines collapse to a
/// single newline, and content lines run through
/// [`normalize_line_whitespace`]. Returns whether the appended line was
/// blank so callers can keep their blank-run state updated.
fn append_non_code_line(result: &mut String, line: &str, prev_blank: bool) -> bool {
    let trimmed = line.trim_end();
    if trimmed.is_empty() {
        if !prev_blank {
            result.push('\n');
        }
        return true;
    }
    normalize_line_whitespace_into(trimmed, result);
    result.push('\n');
    false
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
            b' ' => columns = columns.saturating_add(1),
            b'\t' => {
                columns = columns
                    .saturating_div(4)
                    .saturating_add(1)
                    .saturating_mul(4)
            }
            _ => break,
        }
    }
    columns
}

/// Measures the fence marker and run of a Markdown fence when its indentation
/// is valid. The returned byte identifies a backtick or tilde fence.
///
/// # Examples
///
/// ```ignore
/// assert_eq!(measure_fence("```rust"), Some((b'`', 3)));
/// assert_eq!(measure_fence("   ~~~~"), Some((b'~', 4)));
/// assert_eq!(measure_fence("    ```"), None);
/// ```
///
/// Returns `None` when the line is not a validly indented fence marker.
pub(crate) fn measure_fence(line: &str) -> Option<(u8, usize)> {
    // CommonMark: a fence must be indented at most 3 spaces. A line
    // indented 4+ columns is an indented code block, not a fence.
    if leading_indent_columns(line) > 3 {
        return None;
    }

    let marker = line.trim_start_matches([' ', '\t']).as_bytes();
    let ch = *marker.first()?;
    if ch != b'`' && ch != b'~' {
        return None;
    }

    Some((ch, marker.iter().take_while(|&&byte| byte == ch).count()))
}

/// Returns the fence marker length, or zero when no marker is present.
#[cfg(test)]
pub(crate) fn measure_fence_len(line: &str) -> usize {
    measure_fence(line).map(|(_, len)| len).unwrap_or(0)
}

/// Ensures a string ends with exactly one newline.
///
/// # Examples
///
/// ```ignore
/// let result = fix_trailing_newlines("text\n\n".to_owned());
/// assert_eq!(result, "text\n");
/// ```
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
    fn backtick_run_toggles_inline_code_state() {
        let mut state = LineWhitespaceState::new();
        assert!(!state.in_inline_code);
        state.toggle_backtick_run(2);
        assert!(state.in_inline_code);
        assert_eq!(state.span_delim, 2);
        // A mismatched run neither closes nor re-opens the current span.
        state.toggle_backtick_run(1);
        assert!(state.in_inline_code);
        assert_eq!(state.span_delim, 2);
        state.toggle_backtick_run(2);
        assert!(!state.in_inline_code);
        assert_eq!(state.span_delim, 0);
        state.toggle_backtick_run(1);
        assert!(state.in_inline_code);
    }

    #[test]
    fn space_keeps_leading_runs_and_first_separator_only() {
        let mut state = LineWhitespaceState::new();
        // Leading indentation: every space is preserved and never marks a
        // separation point.
        assert!(state.keeps_space_verbatim());
        assert!(state.keeps_space_verbatim());
        assert!(!state.prev_space);

        state.note_visible_char();
        assert!(state.keeps_space_verbatim());
        assert!(state.prev_space, "separating space arms collapse tracking");
        assert!(!state.keeps_space_verbatim(), "second space collapses");
        assert!(state.prev_space, "dropped spaces keep collapse armed");

        // Inside an inline code span spaces are always verbatim and never
        // participate in collapse tracking.
        state.note_visible_char();
        state.toggle_backtick_run(1);
        assert!(state.keeps_space_verbatim());
        assert!(state.keeps_space_verbatim());
        assert!(!state.prev_space);
    }

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
    fn leading_indent_columns_saturates_on_extreme_prefix() {
        // A pathological line of spaces must count columns without
        // overflowing in debug builds; the counter saturates at
        // usize::MAX. Exercise the loop with a large (but finite)
        // prefix instead of materializing a multi-gigabyte string.
        let long_prefix = " ".repeat(4 * 1024 * 1024); // 4 MiB of spaces
        assert_eq!(leading_indent_columns(&long_prefix), 4 * 1024 * 1024);
        assert_eq!(leading_indent_columns("\t```"), 4);
        assert_eq!(leading_indent_columns(""), 0);
        assert_eq!(leading_indent_columns("  \tx"), 4);
    }

    #[test]
    fn fence_indent_counts_columns_with_tabs() {
        // CommonMark indentation counts columns, not bytes.  A tab
        // advances to the next four-column tab stop, so any leading tab
        // reaches column 4 or beyond and the backtick run is indented
        // code, never a fence.
        assert_eq!(measure_fence_len("\t```"), 0, "tab reaches column 4");
        assert_eq!(measure_fence_len(" \t```"), 0, "space+tab reaches column 4");
        assert_eq!(
            measure_fence_len("  \t```"),
            0,
            "2 spaces+tab reaches column 4"
        );
        assert_eq!(
            measure_fence_len("   \t```"),
            0,
            "3 spaces+tab reaches column 4"
        );
        assert_eq!(measure_fence_len("\t\t```"), 0, "tab+tab reaches column 8");
        // A tab inside the 3-column limit cannot exist: the first tab
        // always jumps to column 4.  Pure-space prefixes stay bounded.
        assert_eq!(measure_fence_len(" ```"), 3);
        assert_eq!(measure_fence_len("  ```"), 3);
        assert_eq!(measure_fence_len("   ```"), 3);
        assert_eq!(measure_fence("~~~lang"), Some((b'~', 3)));
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
    fn normalize_output_rejects_backtick_in_fence_info() {
        let converter = MarkdownConverter::with_options(Default::default());
        let out = converter.normalize_output("```rust`\na  b\n```\n".to_string());

        assert!(
            !out.contains("a  b"),
            "a backtick in the info string must not open a fence: {out:?}"
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
