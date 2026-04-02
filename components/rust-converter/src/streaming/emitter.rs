//! Incremental Markdown emitter for the streaming pipeline.
//!
//! Receives [`StateMachineAction`] values from the
//! [`StructuralStateMachine`](super::state_machine::StructuralStateMachine)
//! and produces Markdown output incrementally. Flush points are triggered
//! at block-level element boundaries so each flushed fragment is
//! syntactically complete.
//!
//! # Output Normalization
//!
//! The emitter applies the same normalization as the full-buffer engine:
//! - CRLF → LF conversion
//! - Consecutive blank-line compression (at most one blank line)
//! - Trailing whitespace removal per line
//! - Single trailing newline on finalize

use crate::error::ConversionError;
use crate::streaming::budget::MemoryBudget;
use crate::streaming::state_machine::{StateMachineAction, StructuralContext};

/// Block-level tags whose closing triggers a flush point.
const FLUSH_TAGS: &[&str] = &[
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "li",
    "pre",
    "blockquote",
    "ul",
    "ol",
];

/// Incremental Markdown emitter with bounded output buffer.
///
/// The emitter accumulates pending Markdown bytes in `buffer` and moves
/// them to `flushed` at each flush point. The caller retrieves ready
/// output via [`take_flushed`](Self::take_flushed).
pub struct IncrementalEmitter {
    /// Pending output bytes (bounded by `max_buffer_size`).
    buffer: Vec<u8>,
    /// Maximum size of the pending buffer (from [`MemoryBudget`]).
    max_buffer_size: usize,
    /// Ready-to-deliver output bytes.
    flushed: Vec<u8>,
    /// Whether we are currently inside a code block (`<pre>`).
    in_code_block: bool,
    /// Count of consecutive blank lines emitted (for compression).
    consecutive_blank_lines: u32,
    /// Whether the last byte written was a newline.
    last_was_newline: bool,
    /// Current list nesting depth (for indentation).
    list_depth: usize,
    /// Current blockquote nesting depth.
    blockquote_depth: usize,
    /// Whether a block separator (blank line) is needed before the next block.
    needs_block_separator: bool,
    /// Accumulated link text for the current `[text](url)` span.
    link_text: String,
    /// Whether we are currently collecting link text.
    in_link: bool,
    /// Number of flush points produced.
    flush_count: u32,
    /// Language for the current code block (may be updated after enter).
    code_fence_lang: Option<String>,
    /// Whether the opening code fence has been emitted.
    code_fence_emitted: bool,
}

impl IncrementalEmitter {
    /// Create a new emitter with buffer size derived from the budget.
    ///
    /// # Arguments
    ///
    /// * `budget` - Memory budget; `output_buffer` sets the pending buffer limit.
    pub fn new(budget: &MemoryBudget) -> Self {
        Self {
            buffer: Vec::new(),
            max_buffer_size: budget.output_buffer,
            flushed: Vec::new(),
            in_code_block: false,
            consecutive_blank_lines: 0,
            last_was_newline: false,
            list_depth: 0,
            blockquote_depth: 0,
            needs_block_separator: false,
            link_text: String::new(),
            in_link: false,
            flush_count: 0,
            code_fence_lang: None,
            code_fence_emitted: false,
        }
    }

    /// Process a state machine action and emit corresponding Markdown.
    ///
    /// # Arguments
    ///
    /// * `action` - The action produced by the structural state machine.
    /// * `sm` - Reference to the state machine for context queries.
    ///
    /// # Errors
    ///
    /// Returns [`ConversionError::BudgetExceeded`] if the pending buffer
    /// would exceed its bounded size.
    pub fn process_action(
        &mut self,
        action: &StateMachineAction,
        sm: &mut super::state_machine::StructuralStateMachine,
    ) -> Result<(), ConversionError> {
        match action {
            StateMachineAction::Enter(ctx) => self.handle_enter(ctx, sm),
            StateMachineAction::Exit(ctx) => self.handle_exit(ctx, sm),
            StateMachineAction::Text(text) => self.handle_text(text, sm),
            StateMachineAction::FallbackRequired | StateMachineAction::None => Ok(()),
        }
    }

    /// Take all flushed (ready) output bytes, leaving the internal
    /// flushed buffer empty.
    pub fn take_flushed(&mut self) -> Vec<u8> {
        std::mem::take(&mut self.flushed)
    }

    /// Number of flush points produced so far.
    pub fn flush_count(&self) -> u32 {
        self.flush_count
    }

    /// Current size of the pending (not yet flushed) buffer in bytes.
    pub fn pending_bytes(&self) -> usize {
        self.buffer.len()
    }

    /// Current size of the flushed (ready to deliver) buffer in bytes.
    pub fn flushed_bytes(&self) -> usize {
        self.flushed.len()
    }

    /// Finalize the emitter: flush all remaining pending bytes.
    ///
    /// Ensures the output ends with exactly one newline.
    ///
    /// # Errors
    ///
    /// Returns [`ConversionError::BudgetExceeded`] if the final flush
    /// would exceed the ready-buffer budget.
    pub fn finalize(&mut self) -> Result<Vec<u8>, ConversionError> {
        // Close any open code block
        if self.in_code_block {
            if !self.code_fence_emitted {
                self.write_raw(b"```\n");
            }
            if !self.last_was_newline {
                self.write_raw(b"\n");
            }
            self.write_raw(b"```\n");
            self.in_code_block = false;
        }
        // Move pending buffer to flushed (checked — bounded-memory contract
        // applies even in the terminal flush).
        self.flush_to_ready()?;
        let mut output = self.take_flushed();
        // Ensure single trailing newline
        while output.ends_with(b"\n\n") {
            output.pop();
        }
        if !output.is_empty() && !output.ends_with(b"\n") {
            output.push(b'\n');
        }
        Ok(output)
    }

    // ── Enter handlers ──────────────────────────────────────────────

    fn handle_enter(
        &mut self,
        ctx: &StructuralContext,
        sm: &mut super::state_machine::StructuralStateMachine,
    ) -> Result<(), ConversionError> {
        match ctx {
            StructuralContext::Heading(level) => {
                self.emit_block_separator()?;
                let hashes = "#".repeat(*level as usize);
                self.write_str(&format!("{} ", hashes))?;
            }
            StructuralContext::Paragraph => {
                self.emit_block_separator()?;
            }
            StructuralContext::OrderedList(_) => {
                if sm.list_depth <= 1 {
                    self.emit_block_separator()?;
                }
                self.list_depth = sm.list_depth;
            }
            StructuralContext::UnorderedList => {
                if sm.list_depth <= 1 {
                    self.emit_block_separator()?;
                }
                self.list_depth = sm.list_depth;
            }
            StructuralContext::ListItem => {
                self.emit_list_prefix(sm)?;
            }
            StructuralContext::CodeBlock(lang) => {
                self.emit_block_separator()?;
                // Defer fence emission — the language may be updated by
                // a nested <code class="language-*"> tag. We mark that
                // we are in a code block and emit the opening fence
                // when the first text arrives or on exit.
                self.in_code_block = true;
                self.code_fence_lang = lang.clone();
                self.code_fence_emitted = false;
            }
            StructuralContext::InlineCode => {
                self.write_str("`")?;
            }
            StructuralContext::Blockquote => {
                self.emit_block_separator()?;
                self.blockquote_depth = sm.blockquote_depth;
            }
            StructuralContext::Link(_) => {
                self.in_link = true;
                self.link_text.clear();
            }
            StructuralContext::Image { src, alt } => {
                self.write_str(&format!("![{}]({})", alt, src))?;
            }
            StructuralContext::Bold => {
                self.write_str("**")?;
            }
            StructuralContext::Italic => {
                self.write_str("*")?;
            }
            _ => {}
        }
        Ok(())
    }

    // ── Exit handlers ───────────────────────────────────────────────

    fn handle_exit(
        &mut self,
        ctx: &StructuralContext,
        sm: &mut super::state_machine::StructuralStateMachine,
    ) -> Result<(), ConversionError> {
        match ctx {
            StructuralContext::Heading(_) => {
                self.write_str("\n")?;
                self.last_was_newline = true;
                self.needs_block_separator = true;
                self.trigger_flush()?;
            }
            StructuralContext::Paragraph => {
                self.write_str("\n")?;
                self.last_was_newline = true;
                self.needs_block_separator = true;
                self.trigger_flush()?;
            }
            StructuralContext::OrderedList(_) | StructuralContext::UnorderedList => {
                self.list_depth = sm.list_depth;
                self.needs_block_separator = true;
                self.trigger_flush()?;
            }
            StructuralContext::ListItem => {
                // Ensure newline after list item content
                if !self.last_was_newline {
                    self.write_str("\n")?;
                    self.last_was_newline = true;
                }
                self.trigger_flush()?;
            }
            StructuralContext::CodeBlock(_) => {
                // Emit deferred code fence if not yet emitted (empty code block)
                self.emit_code_fence_if_needed(sm)?;
                // Ensure newline before closing fence
                if !self.last_was_newline {
                    self.write_str("\n")?;
                }
                self.write_str("```\n")?;
                self.in_code_block = false;
                self.code_fence_emitted = false;
                self.code_fence_lang = None;
                self.last_was_newline = true;
                self.needs_block_separator = true;
                self.trigger_flush()?;
            }
            StructuralContext::InlineCode => {
                self.write_str("`")?;
            }
            StructuralContext::Blockquote => {
                self.blockquote_depth = sm.blockquote_depth;
                self.needs_block_separator = true;
                self.trigger_flush()?;
            }
            StructuralContext::Link(href) => {
                self.in_link = false;
                let text = std::mem::take(&mut self.link_text);
                self.write_str(&format!("[{}]({})", text, href))?;
            }
            StructuralContext::Image { .. } => {
                // Image is fully emitted on Enter (self-closing style)
            }
            StructuralContext::Bold => {
                self.write_str("**")?;
            }
            StructuralContext::Italic => {
                self.write_str("*")?;
            }
            _ => {}
        }
        Ok(())
    }

    // ── Text handler ────────────────────────────────────────────────

    fn handle_text(
        &mut self,
        text: &str,
        sm: &mut super::state_machine::StructuralStateMachine,
    ) -> Result<(), ConversionError> {
        if self.in_link {
            self.link_text.push_str(text);
            return Ok(());
        }

        if self.in_code_block {
            // Emit deferred code fence if not yet emitted
            self.emit_code_fence_if_needed(sm)?;
            // Preserve whitespace inside code blocks
            self.write_str(text)?;
            self.last_was_newline = text.ends_with('\n');
            return Ok(());
        }

        // Normalize text: collapse whitespace
        let normalized = normalize_text(text);
        if normalized.is_empty() {
            return Ok(());
        }

        self.write_str(&normalized)?;
        self.last_was_newline = normalized.ends_with('\n');
        Ok(())
    }

    // ── Internal helpers ────────────────────────────────────────────

    /// Emit the deferred opening code fence, reading the current language
    /// from the state machine (which may have been updated by a nested
    /// `<code class="language-*">` tag).
    fn emit_code_fence_if_needed(
        &mut self,
        sm: &super::state_machine::StructuralStateMachine,
    ) -> Result<(), ConversionError> {
        if self.code_fence_emitted {
            return Ok(());
        }
        // Check if the state machine has updated the language
        let lang = sm
            .current_context()
            .and_then(|ctx| {
                if let StructuralContext::CodeBlock(l) = ctx {
                    l.clone()
                } else {
                    self.code_fence_lang.clone()
                }
            })
            .or_else(|| self.code_fence_lang.clone());

        let fence = match lang {
            Some(l) => format!("```{}\n", l),
            None => "```\n".to_string(),
        };
        self.write_str(&fence)?;
        self.code_fence_emitted = true;
        Ok(())
    }

    /// Emit a block separator (blank line) if needed.
    fn emit_block_separator(&mut self) -> Result<(), ConversionError> {
        if self.needs_block_separator {
            // Write a blank line to separate blocks. The write_str
            // normalization handles consecutive blank line compression.
            self.write_str("\n")?;
            self.consecutive_blank_lines = 0;
        }
        self.needs_block_separator = false;
        Ok(())
    }

    /// Emit the list item prefix with correct indentation.
    fn emit_list_prefix(
        &mut self,
        sm: &mut super::state_machine::StructuralStateMachine,
    ) -> Result<(), ConversionError> {
        // Indentation: 2 spaces per nesting level beyond the first
        let indent_level = if sm.list_depth > 1 {
            sm.list_depth.saturating_sub(1)
        } else {
            0
        };
        let indent = "  ".repeat(indent_level);

        // Use the nearest enclosing list to decide the marker style.
        // This correctly handles mixed nesting like <ol><li><ul><li>
        // where the inner <li> should use `-` (unordered), not `1.`.
        let prefix = if matches!(
            sm.nearest_list_context(),
            Some(StructuralContext::OrderedList(_))
        ) {
            let num = sm.next_ordered_item_number();
            format!("{}{}. ", indent, num)
        } else {
            format!("{}- ", indent)
        };

        self.write_str(&prefix)?;
        Ok(())
    }

    /// Write normalized bytes to the pending buffer.
    fn write_str(&mut self, s: &str) -> Result<(), ConversionError> {
        let bytes = s.as_bytes();
        self.check_buffer_budget(bytes.len())?;
        // Normalize CRLF → LF as we write
        for &b in bytes {
            if b == b'\r' {
                // Skip \r; the following \n (if any) will be written
                continue;
            }
            if b == b'\n' {
                if self.consecutive_blank_lines < 1 || !self.last_was_newline {
                    self.buffer.push(b);
                    if self.last_was_newline {
                        self.consecutive_blank_lines =
                            self.consecutive_blank_lines.saturating_add(1);
                    } else {
                        self.consecutive_blank_lines = 0;
                    }
                    self.last_was_newline = true;
                }
            } else {
                self.buffer.push(b);
                self.last_was_newline = false;
                self.consecutive_blank_lines = 0;
            }
        }
        Ok(())
    }

    /// Write raw bytes without normalization (used for code block fences).
    fn write_raw(&mut self, bytes: &[u8]) {
        self.buffer.extend_from_slice(bytes);
        self.last_was_newline = bytes.last().copied() == Some(b'\n');
    }

    /// Check that adding `additional` bytes won't exceed the buffer budget.
    fn check_buffer_budget(&self, additional: usize) -> Result<(), ConversionError> {
        let new_size = self.buffer.len().saturating_add(additional);
        if new_size > self.max_buffer_size {
            return Err(ConversionError::BudgetExceeded {
                stage: "output_buffer".to_string(),
                used: new_size,
                limit: self.max_buffer_size,
            });
        }
        Ok(())
    }

    /// Move pending buffer contents to the flushed output.
    fn trigger_flush(&mut self) -> Result<(), ConversionError> {
        self.flush_to_ready()?;
        self.flush_count = self.flush_count.saturating_add(1);
        Ok(())
    }

    /// Transfer pending buffer to flushed, applying trailing whitespace removal.
    ///
    /// Both `buffer` (pending) and `flushed` (ready) are bounded by
    /// `max_buffer_size` to enforce the bounded-memory contract. If a
    /// single `feed_chunk` call triggers many flush points, the ready
    /// buffer is checked before each append.
    fn flush_to_ready(&mut self) -> Result<(), ConversionError> {
        if self.buffer.is_empty() {
            return Ok(());
        }
        let normalized = normalize_pending(&self.buffer);
        let new_flushed_size = self.flushed.len().saturating_add(normalized.len());
        if new_flushed_size > self.max_buffer_size {
            return Err(ConversionError::BudgetExceeded {
                stage: "output_buffer (ready)".to_string(),
                used: new_flushed_size,
                limit: self.max_buffer_size,
            });
        }
        self.flushed.extend_from_slice(&normalized);
        self.buffer.clear();
        Ok(())
    }
}

/// Normalize text content: collapse whitespace runs into single spaces,
/// trim leading/trailing whitespace.
fn normalize_text(text: &str) -> String {
    // Preserve leading/trailing whitespace to maintain correctness when
    // text is split across chunk boundaries (Property 2: chunk split
    // invariance). Only collapse internal whitespace runs to single spaces.
    if text.is_empty() {
        return String::new();
    }
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.is_empty() {
        // Text is entirely whitespace — preserve as a single space
        // to maintain inter-token spacing across chunk boundaries.
        return " ".to_string();
    }
    let has_leading_ws = text.starts_with(|c: char| c.is_whitespace());
    let has_trailing_ws = text.ends_with(|c: char| c.is_whitespace());
    let mut result = String::new();
    if has_leading_ws {
        result.push(' ');
    }
    result.push_str(&words.join(" "));
    if has_trailing_ws {
        result.push(' ');
    }
    result
}

/// Normalize pending buffer bytes: remove trailing whitespace from each line.
fn normalize_pending(bytes: &[u8]) -> Vec<u8> {
    let text = String::from_utf8_lossy(bytes);
    let mut result = Vec::with_capacity(bytes.len());
    let mut lines = text.split('\n').peekable();
    while let Some(line) = lines.next() {
        let trimmed = line.trim_end();
        result.extend_from_slice(trimmed.as_bytes());
        // Add newline separator if there are more lines, or if the
        // original input ended with a newline and this is the last
        // (empty) segment produced by the split.
        if lines.peek().is_some() || (bytes.ends_with(b"\n") && !trimmed.is_empty()) {
            result.push(b'\n');
        }
    }
    // If the original ended with \n and the last split segment was empty,
    // we need to preserve that trailing newline.
    if bytes.ends_with(b"\n") && !result.ends_with(b"\n") {
        result.push(b'\n');
    }
    result
}

/// Check if a tag name is a block-level flush trigger.
pub fn is_flush_tag(tag: &str) -> bool {
    FLUSH_TAGS.contains(&tag)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::streaming::budget::MemoryBudget;
    use crate::streaming::state_machine::{StateMachineAction, StructuralStateMachine};
    use crate::streaming::types::StreamEvent;

    /// Helper: create a default emitter and state machine pair.
    fn make_pair() -> (IncrementalEmitter, StructuralStateMachine) {
        let budget = MemoryBudget::default();
        (
            IncrementalEmitter::new(&budget),
            StructuralStateMachine::new(&budget),
        )
    }

    /// Helper: feed a sequence of StreamEvents through the state machine
    /// and emitter, returning the concatenated flushed + finalized output
    /// as a UTF-8 string.
    fn emit_html(events: &[StreamEvent]) -> String {
        let (mut emitter, mut sm) = make_pair();
        for ev in events {
            let action = sm.process_event(ev).expect("sm process_event");
            emitter
                .process_action(&action, &mut sm)
                .expect("emitter process_action");
        }
        let mut output = emitter.take_flushed();
        output.extend_from_slice(&emitter.finalize().expect("emitter finalize"));
        String::from_utf8(output).expect("valid utf8")
    }

    fn start_tag(name: &str) -> StreamEvent {
        StreamEvent::StartTag {
            name: name.to_string(),
            attrs: vec![],
            self_closing: false,
        }
    }

    fn start_tag_with_attrs(name: &str, attrs: Vec<(&str, &str)>) -> StreamEvent {
        StreamEvent::StartTag {
            name: name.to_string(),
            attrs: attrs
                .into_iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            self_closing: false,
        }
    }

    fn self_closing_tag(name: &str, attrs: Vec<(&str, &str)>) -> StreamEvent {
        StreamEvent::StartTag {
            name: name.to_string(),
            attrs: attrs
                .into_iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
            self_closing: true,
        }
    }

    fn end_tag(name: &str) -> StreamEvent {
        StreamEvent::EndTag {
            name: name.to_string(),
        }
    }

    fn text(s: &str) -> StreamEvent {
        StreamEvent::Text(s.to_string())
    }

    // ── Heading tests ───────────────────────────────────────────────

    #[test]
    fn test_heading_h1() {
        let output = emit_html(&[start_tag("h1"), text("Hello"), end_tag("h1")]);
        assert_eq!(output, "# Hello\n");
    }

    #[test]
    fn test_heading_h2() {
        let output = emit_html(&[start_tag("h2"), text("World"), end_tag("h2")]);
        assert_eq!(output, "## World\n");
    }

    #[test]
    fn test_heading_h3() {
        let output = emit_html(&[start_tag("h3"), text("Sub"), end_tag("h3")]);
        assert_eq!(output, "### Sub\n");
    }

    #[test]
    fn test_heading_all_levels() {
        for level in 1..=6u8 {
            let tag = format!("h{}", level);
            let output = emit_html(&[start_tag(&tag), text("Title"), end_tag(&tag)]);
            let expected = format!("{} Title\n", "#".repeat(level as usize));
            assert_eq!(output, expected, "h{} mismatch", level);
        }
    }

    // ── Paragraph tests ─────────────────────────────────────────────

    #[test]
    fn test_paragraph() {
        let output = emit_html(&[start_tag("p"), text("Hello world"), end_tag("p")]);
        assert_eq!(output, "Hello world\n");
    }

    #[test]
    fn test_two_paragraphs() {
        let output = emit_html(&[
            start_tag("p"),
            text("First"),
            end_tag("p"),
            start_tag("p"),
            text("Second"),
            end_tag("p"),
        ]);
        assert_eq!(output, "First\n\nSecond\n");
    }

    // ── Unordered list tests ────────────────────────────────────────

    #[test]
    fn test_unordered_list() {
        let output = emit_html(&[
            start_tag("ul"),
            start_tag("li"),
            text("Item 1"),
            end_tag("li"),
            start_tag("li"),
            text("Item 2"),
            end_tag("li"),
            end_tag("ul"),
        ]);
        assert!(output.contains("- Item 1"), "got: {}", output);
        assert!(output.contains("- Item 2"), "got: {}", output);
    }

    // ── Ordered list tests ──────────────────────────────────────────

    #[test]
    fn test_ordered_list() {
        let output = emit_html(&[
            start_tag("ol"),
            start_tag("li"),
            text("First"),
            end_tag("li"),
            start_tag("li"),
            text("Second"),
            end_tag("li"),
            end_tag("ol"),
        ]);
        assert!(output.contains("1. First"), "got: {}", output);
        assert!(output.contains("2. Second"), "got: {}", output);
    }

    // ── Mixed nested list tests ─────────────────────────────────────

    /// Regression: `<ol><li><ul><li>` inner items must use `-` (unordered),
    /// not `1.` (ordered). The marker is determined by the nearest enclosing
    /// list, not any ancestor list.
    #[test]
    fn test_ol_containing_ul_uses_correct_markers() {
        let output = emit_html(&[
            start_tag("ol"),
            start_tag("li"),
            text("Ordered item"),
            start_tag("ul"),
            start_tag("li"),
            text("Unordered child"),
            end_tag("li"),
            end_tag("ul"),
            end_tag("li"),
            end_tag("ol"),
        ]);
        assert!(
            output.contains("1. Ordered item"),
            "outer ol item should use numbered marker, got: {}",
            output
        );
        assert!(
            output.contains("- Unordered child"),
            "inner ul item should use dash marker, got: {}",
            output
        );
        // Must NOT contain a numbered marker for the inner item
        assert!(
            !output.contains("1. Unordered child") && !output.contains("2. Unordered child"),
            "inner ul item must not use numbered marker, got: {}",
            output
        );
    }

    /// Regression: `<ul><li><ol><li>` inner items must use `1.` (ordered).
    #[test]
    fn test_ul_containing_ol_uses_correct_markers() {
        let output = emit_html(&[
            start_tag("ul"),
            start_tag("li"),
            text("Unordered item"),
            start_tag("ol"),
            start_tag("li"),
            text("Ordered child"),
            end_tag("li"),
            end_tag("ol"),
            end_tag("li"),
            end_tag("ul"),
        ]);
        assert!(
            output.contains("- Unordered item"),
            "outer ul item should use dash marker, got: {}",
            output
        );
        assert!(
            output.contains("1. Ordered child"),
            "inner ol item should use numbered marker, got: {}",
            output
        );
    }

    // ── Link tests ──────────────────────────────────────────────────

    #[test]
    fn test_link() {
        let output = emit_html(&[
            start_tag("p"),
            start_tag_with_attrs("a", vec![("href", "https://example.com")]),
            text("Click here"),
            end_tag("a"),
            end_tag("p"),
        ]);
        assert!(
            output.contains("[Click here](https://example.com)"),
            "got: {}",
            output
        );
    }

    // ── Image tests ─────────────────────────────────────────────────

    #[test]
    fn test_image() {
        let output = emit_html(&[
            start_tag("p"),
            self_closing_tag("img", vec![("src", "pic.png"), ("alt", "A picture")]),
            end_tag("p"),
        ]);
        assert!(output.contains("![A picture](pic.png)"), "got: {}", output);
    }

    // ── Code block tests ────────────────────────────────────────────

    #[test]
    fn test_code_block_no_language() {
        let output = emit_html(&[
            start_tag("pre"),
            start_tag("code"),
            text("let x = 1;"),
            end_tag("code"),
            end_tag("pre"),
        ]);
        assert!(output.contains("```\n"), "got: {}", output);
        assert!(output.contains("let x = 1;"), "got: {}", output);
    }

    #[test]
    fn test_code_block_with_language() {
        let output = emit_html(&[
            start_tag("pre"),
            start_tag_with_attrs("code", vec![("class", "language-rust")]),
            text("fn main() {}"),
            end_tag("code"),
            end_tag("pre"),
        ]);
        assert!(output.contains("```rust\n"), "got: {}", output);
        assert!(output.contains("fn main() {}"), "got: {}", output);
    }

    // ── Inline code tests ───────────────────────────────────────────

    #[test]
    fn test_inline_code() {
        let output = emit_html(&[
            start_tag("p"),
            text("Use "),
            start_tag("code"),
            text("println!"),
            end_tag("code"),
            text(" macro"),
            end_tag("p"),
        ]);
        assert!(output.contains("`println!`"), "got: {}", output);
    }

    // ── Blockquote tests ────────────────────────────────────────────

    #[test]
    fn test_blockquote() {
        let output = emit_html(&[
            start_tag("blockquote"),
            start_tag("p"),
            text("Quoted text"),
            end_tag("p"),
            end_tag("blockquote"),
        ]);
        assert!(output.contains("Quoted text"), "got: {}", output);
    }

    // ── Bold / Italic tests ─────────────────────────────────────────

    #[test]
    fn test_bold() {
        let output = emit_html(&[
            start_tag("p"),
            start_tag("strong"),
            text("bold"),
            end_tag("strong"),
            end_tag("p"),
        ]);
        assert!(output.contains("**bold**"), "got: {}", output);
    }

    #[test]
    fn test_italic() {
        let output = emit_html(&[
            start_tag("p"),
            start_tag("em"),
            text("italic"),
            end_tag("em"),
            end_tag("p"),
        ]);
        assert!(output.contains("*italic*"), "got: {}", output);
    }

    #[test]
    fn test_bold_italic_nested() {
        let output = emit_html(&[
            start_tag("p"),
            start_tag("strong"),
            start_tag("em"),
            text("both"),
            end_tag("em"),
            end_tag("strong"),
            end_tag("p"),
        ]);
        assert!(output.contains("***both***"), "got: {}", output);
    }

    // ── Flush point tests ───────────────────────────────────────────

    #[test]
    fn test_flush_at_heading_end() {
        let (mut emitter, mut sm) = make_pair();
        let events = vec![start_tag("h1"), text("Title"), end_tag("h1")];
        for ev in &events {
            let action = sm.process_event(ev).unwrap();
            emitter.process_action(&action, &mut sm).unwrap();
        }
        let flushed = emitter.take_flushed();
        assert!(!flushed.is_empty(), "should flush after heading");
        assert!(emitter.flush_count() >= 1);
    }

    #[test]
    fn test_flush_at_paragraph_end() {
        let (mut emitter, mut sm) = make_pair();
        let events = vec![start_tag("p"), text("Text"), end_tag("p")];
        for ev in &events {
            let action = sm.process_event(ev).unwrap();
            emitter.process_action(&action, &mut sm).unwrap();
        }
        let flushed = emitter.take_flushed();
        assert!(!flushed.is_empty(), "should flush after paragraph");
    }

    #[test]
    fn test_flush_at_list_item_end() {
        let (mut emitter, mut sm) = make_pair();
        let events = vec![
            start_tag("ul"),
            start_tag("li"),
            text("Item"),
            end_tag("li"),
        ];
        for ev in &events {
            let action = sm.process_event(ev).unwrap();
            emitter.process_action(&action, &mut sm).unwrap();
        }
        let flushed = emitter.take_flushed();
        assert!(!flushed.is_empty(), "should flush after list item");
    }

    #[test]
    fn test_flush_at_code_block_end() {
        let (mut emitter, mut sm) = make_pair();
        let events = vec![
            start_tag("pre"),
            start_tag("code"),
            text("code"),
            end_tag("code"),
            end_tag("pre"),
        ];
        for ev in &events {
            let action = sm.process_event(ev).unwrap();
            emitter.process_action(&action, &mut sm).unwrap();
        }
        let flushed = emitter.take_flushed();
        assert!(!flushed.is_empty(), "should flush after code block");
    }

    // ── Output normalization tests ──────────────────────────────────

    #[test]
    fn test_crlf_to_lf() {
        let (mut emitter, mut sm) = make_pair();
        let action = StateMachineAction::Text("hello\r\nworld".to_string());
        // Wrap in a paragraph context
        let enter = sm.process_event(&start_tag("p")).unwrap();
        emitter.process_action(&enter, &mut sm).unwrap();
        emitter.process_action(&action, &mut sm).unwrap();
        let exit = sm.process_event(&end_tag("p")).unwrap();
        emitter.process_action(&exit, &mut sm).unwrap();
        let output = emitter.take_flushed();
        let s = String::from_utf8(output).unwrap();
        assert!(!s.contains('\r'), "CRLF should be converted to LF: {}", s);
    }

    #[test]
    fn test_consecutive_blank_line_compression() {
        let output = emit_html(&[
            start_tag("p"),
            text("A"),
            end_tag("p"),
            start_tag("p"),
            text("B"),
            end_tag("p"),
            start_tag("p"),
            text("C"),
            end_tag("p"),
        ]);
        // Should not have more than one consecutive blank line
        assert!(
            !output.contains("\n\n\n"),
            "should compress consecutive blank lines: {:?}",
            output
        );
    }

    #[test]
    fn test_trailing_whitespace_removal() {
        let (mut emitter, mut sm) = make_pair();
        let enter = sm.process_event(&start_tag("p")).unwrap();
        emitter.process_action(&enter, &mut sm).unwrap();
        // Manually write text with trailing spaces
        let action = StateMachineAction::Text("hello   ".to_string());
        emitter.process_action(&action, &mut sm).unwrap();
        let exit = sm.process_event(&end_tag("p")).unwrap();
        emitter.process_action(&exit, &mut sm).unwrap();
        let output = emitter.take_flushed();
        let s = String::from_utf8(output).unwrap();
        // Lines should not have trailing spaces
        for line in s.lines() {
            assert_eq!(
                line,
                line.trim_end(),
                "trailing whitespace should be removed"
            );
        }
    }

    // ── Embedded content URL extraction ─────────────────────────────

    #[test]
    fn test_embedded_iframe_url() {
        // The sanitizer converts iframe to text with URL; emitter just passes text through
        let output = emit_html(&[
            start_tag("p"),
            text("[iframe](https://example.com)"),
            end_tag("p"),
        ]);
        assert!(
            output.contains("[iframe](https://example.com)"),
            "got: {}",
            output
        );
    }

    // ── Buffer overflow test ────────────────────────────────────────

    #[test]
    fn test_buffer_overflow_returns_budget_exceeded() {
        let budget = MemoryBudget {
            output_buffer: 32, // Very small buffer
            ..MemoryBudget::default()
        };
        let mut emitter = IncrementalEmitter::new(&budget);
        let mut sm = StructuralStateMachine::new(&MemoryBudget::default());

        let enter = sm.process_event(&start_tag("p")).unwrap();
        emitter.process_action(&enter, &mut sm).unwrap();

        // Try to write more than 32 bytes
        let big_text = StateMachineAction::Text("A".repeat(64));
        let result = emitter.process_action(&big_text, &mut sm);
        assert!(result.is_err(), "should return BudgetExceeded");
        let err = result.unwrap_err();
        assert_eq!(err.code(), 6, "error code should be BudgetExceeded (6)");
    }

    /// Regression: the flushed (ready) buffer must also be bounded.
    /// A single feed_chunk with many flush points should not grow the
    /// ready buffer without limit.
    #[test]
    fn test_flushed_buffer_bounded_by_budget() {
        let budget = MemoryBudget {
            output_buffer: 64, // Small budget for both pending and ready
            ..MemoryBudget::default()
        };
        let mut emitter = IncrementalEmitter::new(&budget);
        let mut sm = StructuralStateMachine::new(&MemoryBudget::default());

        // Generate many paragraphs that each flush. Each paragraph
        // produces ~15 bytes ("Some text here\n"), so after ~5 flushes
        // the ready buffer should exceed 64 bytes.
        let mut hit_budget = false;
        for i in 0..20 {
            let enter = sm.process_event(&start_tag("p"));
            if enter.is_err() {
                break;
            }
            let enter = enter.unwrap();
            if let Err(e) = emitter.process_action(&enter, &mut sm) {
                hit_budget = true;
                assert_eq!(e.code(), 6);
                break;
            }

            let text_action = StateMachineAction::Text(format!("Paragraph {}", i));
            if let Err(e) = emitter.process_action(&text_action, &mut sm) {
                hit_budget = true;
                assert_eq!(e.code(), 6);
                break;
            }

            let exit = sm.process_event(&end_tag("p"));
            if exit.is_err() {
                break;
            }
            let exit = exit.unwrap();
            if let Err(e) = emitter.process_action(&exit, &mut sm) {
                hit_budget = true;
                assert_eq!(e.code(), 6);
                break;
            }
        }
        assert!(
            hit_budget,
            "should have hit BudgetExceeded on the ready buffer; \
             flushed_bytes={}, pending_bytes={}",
            emitter.flushed_bytes(),
            emitter.pending_bytes(),
        );
    }

    // ── Finalize tests ──────────────────────────────────────────────

    #[test]
    fn test_finalize_single_trailing_newline() {
        let output = emit_html(&[start_tag("p"), text("Hello"), end_tag("p")]);
        assert!(output.ends_with('\n'), "should end with newline");
        assert!(
            !output.ends_with("\n\n"),
            "should not end with double newline"
        );
    }

    #[test]
    fn test_finalize_empty_input() {
        let (mut emitter, _sm) = make_pair();
        let output = emitter.finalize().unwrap();
        assert!(output.is_empty(), "empty input should produce empty output");
    }

    /// Regression: finalize() must enforce the ready-buffer budget even
    /// in the terminal flush. When flushed + pending would exceed the
    /// limit, finalize should return BudgetExceeded.
    #[test]
    fn test_finalize_enforces_ready_buffer_budget() {
        let budget = MemoryBudget {
            output_buffer: 48, // Small budget
            ..MemoryBudget::default()
        };
        let mut emitter = IncrementalEmitter::new(&budget);
        let mut sm = StructuralStateMachine::new(&MemoryBudget::default());

        // Step 1: emit a paragraph that flushes ~20 bytes to the ready buffer
        let enter = sm.process_event(&start_tag("p")).unwrap();
        emitter.process_action(&enter, &mut sm).unwrap();
        let t = StateMachineAction::Text("First paragraph.".to_string());
        emitter.process_action(&t, &mut sm).unwrap();
        let exit = sm.process_event(&end_tag("p")).unwrap();
        emitter.process_action(&exit, &mut sm).unwrap();
        // Ready buffer now has ~20 bytes from the flush

        // Step 2: start a new paragraph but do NOT close it, so text
        // stays in the pending buffer
        let enter2 = sm.process_event(&start_tag("p")).unwrap();
        emitter.process_action(&enter2, &mut sm).unwrap();
        let t2 =
            StateMachineAction::Text("Second paragraph with enough text to exceed.".to_string());
        emitter.process_action(&t2, &mut sm).unwrap();
        // Pending buffer now has ~45 bytes, ready buffer has ~20 bytes

        assert!(
            emitter.flushed_bytes() > 0,
            "ready buffer should be non-empty before finalize"
        );
        assert!(
            emitter.pending_bytes() > 0,
            "pending buffer should be non-empty before finalize"
        );

        // Step 3: finalize should fail because flushed + pending > 48
        let result = emitter.finalize();
        assert!(
            result.is_err(),
            "finalize should return BudgetExceeded when flushed ({}) + pending ({}) > budget (48)",
            emitter.flushed_bytes(),
            emitter.pending_bytes(),
        );
        assert_eq!(result.unwrap_err().code(), 6);
    }

    // ── Heading + paragraph separation ──────────────────────────────

    #[test]
    fn test_heading_then_paragraph_separated() {
        let output = emit_html(&[
            start_tag("h1"),
            text("Title"),
            end_tag("h1"),
            start_tag("p"),
            text("Body"),
            end_tag("p"),
        ]);
        assert!(
            output.contains("Title\n\nBody"),
            "heading and paragraph should be separated by blank line: {:?}",
            output
        );
    }

    // ── Regression tests for normalize_text edge cases ──────────────
    // These cover bugs found by Property 2 (chunk split invariance):
    // 1. Trailing whitespace was stripped, losing spaces before inline tags
    // 2. Whitespace-only text returned empty, dropping inter-token spacing

    #[test]
    fn test_normalize_text_preserves_trailing_whitespace() {
        // Regression: "Text with " before <strong> must keep trailing space
        let output = emit_html(&[
            start_tag("p"),
            text("Text with "),
            start_tag("strong"),
            text("bold"),
            end_tag("strong"),
            end_tag("p"),
        ]);
        assert!(
            output.contains("Text with **bold**"),
            "trailing space before inline tag must be preserved: {:?}",
            output
        );
    }

    #[test]
    fn test_normalize_text_preserves_leading_whitespace() {
        // Regression: " after" following </strong> must keep leading space
        let output = emit_html(&[
            start_tag("p"),
            start_tag("strong"),
            text("bold"),
            end_tag("strong"),
            text(" after"),
            end_tag("p"),
        ]);
        assert!(
            output.contains("**bold** after"),
            "leading space after inline tag must be preserved: {:?}",
            output
        );
    }

    #[test]
    fn test_normalize_text_whitespace_only_preserved_as_space() {
        // Regression: whitespace-only text between tags must become a single
        // space, not be dropped entirely.
        let output = emit_html(&[
            start_tag("p"),
            start_tag("strong"),
            text("a"),
            end_tag("strong"),
            text(" "),
            start_tag("em"),
            text("b"),
            end_tag("em"),
            end_tag("p"),
        ]);
        assert!(
            output.contains("**a** *b*"),
            "whitespace-only text between inline tags must produce a space: {:?}",
            output
        );
    }

    #[test]
    fn test_normalize_text_collapses_internal_whitespace() {
        let output = emit_html(&[
            start_tag("p"),
            text("multiple   spaces   inside"),
            end_tag("p"),
        ]);
        assert!(
            output.contains("multiple spaces inside"),
            "internal whitespace runs must collapse to single space: {:?}",
            output
        );
    }
}
