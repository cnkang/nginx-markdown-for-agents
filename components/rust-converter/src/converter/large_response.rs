//! Large-response path optimization: buffer estimation and fused normalizer.
//!
//! This module provides two capabilities for optimizing large HTML-to-Markdown
//! conversions:
//!
//! 1. **Buffer estimation** — pre-allocates the output `String` based on input
//!    size, avoiding repeated reallocations during traversal.
//! 2. **Fused normalizer** — normalizes output incrementally as lines are
//!    appended, eliminating the need for a separate full-pass normalization step
//!    after traversal completes.

use super::normalize::{closes_fence, opens_fence};

/// Threshold multiplier for pre-allocating the output buffer.
/// For large documents, we estimate output at ~40% of input size
/// (HTML-to-Markdown typically reduces size by 60–85%).
const OUTPUT_SIZE_ESTIMATE_FACTOR: f64 = 0.4;

/// Minimum pre-allocation size in bytes (4 KB).
const MIN_CAPACITY: usize = 4096;

/// Maximum pre-allocation size in bytes (4 MB).
const MAX_CAPACITY: usize = 4 * 1024 * 1024;

/// Estimate output buffer capacity based on a byte-size hint.
///
/// Used in two contexts:
/// - **Traversal pre-allocation**: called with the input HTML size to size the
///   traversal output buffer before DOM walking begins.
/// - **Fused normalizer pre-allocation**: called with the traversal output
///   length to size the normalizer's buffer before the single-pass
///   normalization step.
///
/// In both cases the 0.4 factor and \[4 KB, 4 MB\] clamp apply identically.
///
/// # Arguments
///
/// * `input_size_bytes` - Size hint in bytes (input HTML size or intermediate
///   output size, depending on the call site).
///
/// # Returns
///
/// Recommended initial capacity for the output `String`, clamped to
/// the range \[4 KB, 4 MB\].
pub(crate) fn estimate_output_capacity(input_size_bytes: usize) -> usize {
    let estimate = (input_size_bytes as f64 * OUTPUT_SIZE_ESTIMATE_FACTOR) as usize;
    estimate.clamp(MIN_CAPACITY, MAX_CAPACITY)
}

/// Fused normalization writer that normalizes output incrementally
/// during traversal, avoiding a separate full-pass normalization step.
///
/// Tracks state needed to collapse consecutive blank lines, trim trailing
/// whitespace, and normalize inline whitespace as content is appended.
///
/// The output produced by `FusedNormalizer` is identical to the output of
/// `MarkdownConverter::normalize_output` for the same input.
pub(crate) struct FusedNormalizer {
    /// The output buffer being built.
    output: String,
    /// Whether the previous line was blank (for collapsing consecutive blanks).
    prev_blank: bool,
    /// Character and length of the active opening fence, or `None` when
    /// outside a fenced code block. A closing fence must use the same
    /// character, have at least the opening length, and contain nothing else
    /// (after trimming), matching CommonMark semantics.
    active_fence: Option<(u8, usize)>,
}

impl FusedNormalizer {
    /// Create a new fused normalizer with the given initial capacity.
    ///
    /// # Arguments
    ///
    /// * `capacity` - Initial capacity for the output buffer.
    pub(crate) fn new(capacity: usize) -> Self {
        Self {
            output: String::with_capacity(capacity),
            prev_blank: false,
            active_fence: None,
        }
    }

    /// Append a line of content, applying normalization rules inline.
    ///
    /// This replicates the per-line logic of `normalize_output`:
    /// - Tracks code-block state via active fence length (CommonMark semantics).
    /// - Collapses consecutive blank lines to a single blank line.
    /// - Trims trailing whitespace.
    /// - Normalizes inline whitespace for non-code-block lines (collapses
    ///   multiple spaces while preserving leading indentation and inline code).
    pub(crate) fn push_line(&mut self, line: &str) {
        /* Strip a trailing \r so that CRLF input is handled without a
         * separate full-string replace() allocation. */
        let line = line.strip_suffix('\r').unwrap_or(line);

        let trimmed_start = line.trim_start();
        let fence = super::normalize::measure_fence(line);
        let fence_info = fence
            .and_then(|(_, len)| trimmed_start.get(len..))
            .unwrap_or("");
        let is_opening_fence = self.active_fence.is_none() && opens_fence(fence, fence_info);
        let is_closing_fence = closes_fence(self.active_fence, fence, fence_info);

        if is_opening_fence || is_closing_fence {
            if is_opening_fence {
                self.active_fence = fence;
            } else {
                self.active_fence = None;
            }
            self.output.push_str(line.trim_end());
            self.output.push('\n');
            self.prev_blank = false;
            return;
        }

        if self.active_fence.is_some() {
            self.output.push_str(line);
            self.output.push('\n');
            self.prev_blank = false;
            return;
        }

        let trimmed = line.trim_end();

        if trimmed.is_empty() {
            if !self.prev_blank {
                self.output.push('\n');
                self.prev_blank = true;
            }
        } else {
            let normalized = super::normalize::normalize_line_whitespace(trimmed);
            self.output.push_str(&normalized);
            self.output.push('\n');
            self.prev_blank = false;
        }
    }

    /// Append raw content without line-level normalization.
    ///
    /// Used for content that has already been normalized or should be
    /// preserved verbatim.
    #[cfg(test)]
    pub(crate) fn push_raw(&mut self, content: &str) {
        self.output.push_str(content);
    }

    /// Finalize the output, ensuring a single trailing newline.
    ///
    /// Consumes the normalizer and returns the completed output string.
    pub(crate) fn finalize(mut self) -> String {
        if !self.output.ends_with('\n') {
            self.output.push('\n');
        } else {
            while self.output.ends_with("\n\n") {
                self.output.pop();
            }
        }
        self.output
    }
}

#[cfg(test)]
mod tests {
    use super::super::normalize::normalize_line_whitespace;
    use super::*;

    // ---------------------------------------------------------------
    // estimate_output_capacity tests
    // ---------------------------------------------------------------

    #[test]
    fn estimate_capacity_small_input_returns_minimum() {
        // 100 bytes * 0.4 = 40, clamped to 4096
        assert_eq!(estimate_output_capacity(100), MIN_CAPACITY);
    }

    #[test]
    fn estimate_capacity_very_large_input_returns_maximum() {
        // 100 MB * 0.4 = 40 MB, clamped to 4 MB
        let hundred_mb = 100 * 1024 * 1024;
        assert_eq!(estimate_output_capacity(hundred_mb), MAX_CAPACITY);
    }

    #[test]
    fn estimate_capacity_medium_input_returns_proportional() {
        // 100 KB * 0.4 = ~40 KB
        let hundred_kb = 100 * 1024;
        let expected = (hundred_kb as f64 * OUTPUT_SIZE_ESTIMATE_FACTOR) as usize;
        assert_eq!(estimate_output_capacity(hundred_kb), expected);
        // Sanity: the proportional value is within bounds
        assert!(expected > MIN_CAPACITY);
        assert!(expected < MAX_CAPACITY);
    }

    #[test]
    fn estimate_capacity_zero_input_returns_minimum() {
        assert_eq!(estimate_output_capacity(0), MIN_CAPACITY);
    }

    #[test]
    fn estimate_capacity_at_min_boundary() {
        // Find the input size where estimate == MIN_CAPACITY exactly
        // 4096 / 0.4 = 10240
        let boundary = (MIN_CAPACITY as f64 / OUTPUT_SIZE_ESTIMATE_FACTOR) as usize;
        assert_eq!(estimate_output_capacity(boundary), MIN_CAPACITY);
    }

    #[test]
    fn estimate_capacity_at_max_boundary() {
        // Find the input size where estimate == MAX_CAPACITY exactly
        // 4 * 1024 * 1024 / 0.4 = 10485760
        let boundary = (MAX_CAPACITY as f64 / OUTPUT_SIZE_ESTIMATE_FACTOR) as usize;
        assert_eq!(estimate_output_capacity(boundary), MAX_CAPACITY);
    }

    // ---------------------------------------------------------------
    // FusedNormalizer tests
    // ---------------------------------------------------------------

    /// Helper: run input through FusedNormalizer line-by-line and return result.
    /// Uses split('\n') to match the production code path in convert_with_context,
    /// relying on push_line's internal \r stripping for CRLF handling.
    fn normalize_via_fused(input: &str) -> String {
        let mut normalizer = FusedNormalizer::new(input.len());
        for line in input.split('\n') {
            normalizer.push_line(line);
        }
        normalizer.finalize()
    }

    /// Reference implementation matching `MarkdownConverter::normalize_output`.
    ///
    /// Kept as an independent mirror of the production algorithm: the fence
    /// predicates below are deliberately re-derived here instead of shared,
    /// so a production regression cannot silently fold into the reference.
    fn normalize_reference(input: &str) -> String {
        let output = input.replace("\r\n", "\n");
        let mut result = String::with_capacity(output.len());
        let mut prev_blank = false;
        let mut active_fence: Option<(u8, usize)> = None;

        for line in output.lines() {
            let fence = crate::converter::normalize::measure_fence(line);
            let fence_info = fence
                .and_then(|(_, len)| line.trim_start().get(len..))
                .unwrap_or("");

            if active_fence.is_none() && reference_opens_fence(fence, fence_info) {
                active_fence = fence;
                push_reference_fence_boundary(&mut result, line);
                prev_blank = false;
                continue;
            }
            if reference_closes_fence(active_fence, fence, fence_info) {
                active_fence = None;
                push_reference_fence_boundary(&mut result, line);
                prev_blank = false;
                continue;
            }

            prev_blank =
                push_reference_body_line(&mut result, line, active_fence.is_some(), prev_blank);
        }

        fix_reference_trailing_newlines(result)
    }

    /// Whether a fence marker opens a new block when none is active:
    /// three or more marker characters and no backtick in a backtick
    /// fence's info string.
    fn reference_opens_fence(fence: Option<(u8, usize)>, fence_info: &str) -> bool {
        match fence {
            Some((marker, len)) => {
                let ch = marker as char;
                len >= 3 && (ch == '~' || !fence_info.contains('`'))
            }
            None => false,
        }
    }

    /// Whether a fence marker closes the active block: same character, at
    /// least the opening length, nothing else on the line.
    fn reference_closes_fence(
        active_fence: Option<(u8, usize)>,
        fence: Option<(u8, usize)>,
        fence_info: &str,
    ) -> bool {
        match (active_fence, fence) {
            (Some((active_marker, active_len)), Some((marker, len))) => {
                let active_ch = active_marker as char;
                let ch = marker as char;
                active_ch == ch && len >= active_len && fence_info.trim().is_empty()
            }
            _ => false,
        }
    }

    /// Append a fence boundary line verbatim apart from trailing whitespace.
    fn push_reference_fence_boundary(result: &mut String, line: &str) {
        result.push_str(line.trim_end());
        result.push('\n');
    }

    /// Append one reference-normalized body line and return the blank-run state.
    ///
    /// Lines inside a code block are preserved verbatim; outside, trailing
    /// whitespace is stripped, whitespace runs collapse, and consecutive
    /// blank lines reduce to a single newline.
    fn push_reference_body_line(
        result: &mut String,
        line: &str,
        in_code_block: bool,
        prev_blank: bool,
    ) -> bool {
        if in_code_block {
            result.push_str(line);
            result.push('\n');
            return false;
        }
        let trimmed = line.trim_end();
        if trimmed.is_empty() {
            if !prev_blank {
                result.push('\n');
            }
            return true;
        }
        result.push_str(&normalize_line_whitespace(trimmed));
        result.push('\n');
        false
    }

    fn fix_reference_trailing_newlines(mut result: String) -> String {
        if !result.ends_with('\n') {
            result.push('\n');
        } else if result.ends_with("\n\n") {
            while result.ends_with("\n\n") {
                result.pop();
            }
        }
        result
    }

    #[test]
    fn fused_simple_text_with_blank_lines() {
        let input = "Hello world\n\nSecond paragraph\n";
        assert_eq!(normalize_via_fused(input), normalize_reference(input));
    }

    #[test]
    fn fused_code_blocks_preserved() {
        let input = "Before\n\n```\nfn main() {\n    let x = 1;\n}\n```\n\nAfter\n";
        let fused = normalize_via_fused(input);
        let reference = normalize_reference(input);
        assert_eq!(fused, reference);
        // Code block content should be preserved as-is (only trailing whitespace trimmed)
        assert!(fused.contains("    let x = 1;"));
    }

    #[test]
    fn fused_consecutive_blank_lines_collapsed() {
        let input = "Line one\n\n\n\n\nLine two\n";
        let fused = normalize_via_fused(input);
        let reference = normalize_reference(input);
        assert_eq!(fused, reference);
        // Should have exactly one blank line between content
        assert!(!fused.contains("\n\n\n"));
    }

    #[test]
    fn fused_trailing_whitespace_trimmed() {
        let input = "Hello   \nWorld   \n";
        let fused = normalize_via_fused(input);
        let reference = normalize_reference(input);
        assert_eq!(fused, reference);
        // No trailing spaces on any line
        for line in fused.lines() {
            assert_eq!(line, line.trim_end());
        }
    }

    #[test]
    fn fused_mixed_content() {
        let input = concat!(
            "# Heading\n",
            "\n",
            "Some  text   with   extra   spaces\n",
            "\n",
            "```python\n",
            "def  foo():\n",
            "    pass\n",
            "```\n",
            "\n",
            "\n",
            "\n",
            "- List  item  one\n",
            "- List  item  two\n",
            "\n",
            "   Indented  paragraph\n",
        );
        assert_eq!(normalize_via_fused(input), normalize_reference(input));
    }

    #[test]
    fn fused_inline_code_whitespace_preserved() {
        let input = "Use  `multiple  spaces`  in  code\n";
        let fused = normalize_via_fused(input);
        let reference = normalize_reference(input);
        assert_eq!(fused, reference);
        // Spaces inside backticks are preserved
        assert!(fused.contains("`multiple  spaces`"));
    }

    #[test]
    fn fused_push_raw_appends_verbatim() {
        let mut normalizer = FusedNormalizer::new(64);
        normalizer.push_raw("raw content");
        normalizer.push_line("normal line");
        let result = normalizer.finalize();
        assert!(result.starts_with("raw content"));
    }

    #[test]
    fn fused_empty_input() {
        let input = "";
        assert_eq!(normalize_via_fused(input), normalize_reference(input));
    }

    #[test]
    fn fused_only_blank_lines() {
        let input = "\n\n\n";
        assert_eq!(normalize_via_fused(input), normalize_reference(input));
    }

    #[test]
    fn fused_crlf_handling() {
        let input = "Hello\r\nWorld\r\n";
        assert_eq!(normalize_via_fused(input), normalize_reference(input));
    }

    #[test]
    fn fused_crlf_stripped_inline_without_replace() {
        /* Verify that push_line handles \r directly, so the caller does not
         * need to pre-process the input with replace("\r\n", "\n"). */
        let input = "Line one\r\n\r\nLine two\r\n";
        let mut normalizer = FusedNormalizer::new(input.len());
        for line in input.split('\n') {
            normalizer.push_line(line);
        }
        let result = normalizer.finalize();
        assert_eq!(result, normalize_reference(input));
        assert!(!result.contains('\r'));
    }

    #[test]
    fn fused_leading_indentation_preserved() {
        let input = "    indented line\n      more indented\n";
        let fused = normalize_via_fused(input);
        let reference = normalize_reference(input);
        assert_eq!(fused, reference);
        // Leading spaces should be preserved
        assert!(fused.contains("    indented line"));
    }

    #[test]
    fn fused_finalize_ensures_single_trailing_newline() {
        let mut normalizer = FusedNormalizer::new(64);
        normalizer.push_line("Hello");
        let result = normalizer.finalize();
        assert!(result.ends_with('\n'));
        assert!(!result.ends_with("\n\n"));
    }

    #[test]
    fn fused_finalize_trims_double_trailing_newline() {
        // Simulate content that ends with a blank line
        let input = "Hello\n\n";
        let result = normalize_via_fused(input);
        assert!(result.ends_with('\n'));
        assert!(!result.ends_with("\n\n"));
    }

    /// Regression: a four-backtick fenced block containing a literal ```
    /// line must preserve raw content inside the outer fence.  Both the
    /// fused normalizer and the reference implementation must agree.
    #[test]
    fn fused_four_backtick_fence_with_inner_triple() {
        let input = concat!(
            "````\n",
            "```\n",
            "raw blank line follows\n",
            "\n",
            "trailing spaces   \n",
            "```\n",
            "````\n",
        );
        let fused = normalize_via_fused(input);
        let reference = normalize_reference(input);
        assert_eq!(fused, reference);
        /* Inner ``` lines are content, not fence boundaries. */
        assert!(
            fused.contains("```\nraw blank line follows\n\ntrailing spaces"),
            "Inner ``` and raw content must be preserved.\nGot: {fused}"
        );
    }
}
