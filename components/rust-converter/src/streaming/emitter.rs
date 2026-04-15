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
    /// Memory budget used for output-buffer enforcement.
    budget: MemoryBudget,
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
    /// Whether link_text has been truncated due to exceeding the budget.
    /// When set, further appends are skipped but the link is still closed
    /// properly on Exit(Link).
    link_text_overflow: bool,
    /// Number of flush points produced.
    flush_count: u32,
    /// Language for the current code block (may be updated after enter).
    code_fence_lang: Option<String>,
    /// Whether the opening code fence has been emitted.
    code_fence_emitted: bool,
}

impl IncrementalEmitter {
    /// Create an IncrementalEmitter configured from the provided memory budget.
    ///
    /// The emitter's pending buffer limit is set from `budget.output_buffer`. Other
    /// formatting and tracking state are initialized to their empty/default values.
    ///
    /// # Arguments
    ///
    /// * `budget` - Memory budget whose `output_buffer` value bounds the emitter's pending buffer.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let budget = MemoryBudget { output_buffer: 1024 };
    /// let mut emitter = IncrementalEmitter::new(&budget);
    /// assert_eq!(emitter.pending_bytes(), 0);
    /// assert_eq!(emitter.flushed_bytes(), 0);
    /// ```
    pub fn new(budget: &MemoryBudget) -> Self {
        Self {
            budget: budget.clone(),
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
            link_text_overflow: false,
            flush_count: 0,
            code_fence_lang: None,
            code_fence_emitted: false,
        }
    }

    /// Dispatches a `StateMachineAction` to the corresponding handler and emits the resulting Markdown fragments.
    ///
    /// This forwards `Enter`, `Exit`, and `Text` actions to `handle_enter`, `handle_exit`, and `handle_text` respectively; `FallbackRequired` and `None` are ignored.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` if writing the emitted bytes would exceed the emitter's configured output buffer budget.
    ///
    /// # Parameters
    ///
    /// - `action`: action produced by the structural state machine to process.
    /// - `sm`: mutable reference to the structural state machine used for contextual queries during emission.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Process a no-op action; requires a `StructuralStateMachine` instance for context.
    /// let mut emitter = IncrementalEmitter::new(&MemoryBudget::default());
    /// let mut sm = super::state_machine::StructuralStateMachine::default();
    /// emitter.process_action(&StateMachineAction::None, &mut sm).unwrap();
    /// ```
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

    /// Take and return the emitter's ready (flushed) output buffer, leaving it empty.
    ///
    /// # Returns
    ///
    /// `Vec<u8>` containing all bytes that were in the emitter's flushed buffer; the emitter's
    /// internal flushed buffer is cleared.
    pub fn take_flushed(&mut self) -> Vec<u8> {
        std::mem::take(&mut self.flushed)
    }

    /// Current count of emitted flush points.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Construct an emitter (replace with your actual constructor as needed)
    /// let mut emitter = IncrementalEmitter::new(&MemoryBudget::default());
    /// assert_eq!(emitter.flush_count(), 0);
    /// ```
    ///
    /// # Returns
    ///
    /// The number of flush points emitted so far.
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

    /// Finalizes the emitter and returns the fully normalized output.
    ///
    /// Closes any open code block if present, moves pending bytes to the ready buffer, removes extra trailing newlines so at most one final `\n` remains, and returns the complete output bytes.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` if flushing pending bytes to the ready buffer would exceed the configured output buffer limit.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Construct a budget and emitter, write or process actions, then finalize:
    /// let mut emitter = IncrementalEmitter::new(&budget);
    /// // ... emit content ...
    /// let output = emitter.finalize().unwrap();
    /// assert!(!output.is_empty() || output.is_empty()); // trivial usage example
    /// if !output.is_empty() {
    ///     assert!(output.ends_with(b"\n"));
    /// }
    /// ```
    pub fn finalize(&mut self) -> Result<Vec<u8>, ConversionError> {
        // Close any open code block
        if self.in_code_block {
            if !self.code_fence_emitted {
                self.write_raw(b"```\n")?;
            }
            if !self.last_was_newline {
                self.write_raw(b"\n")?;
            }
            self.write_raw(b"```\n")?;
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

    /// Emit the opening Markdown syntax for `ctx` and update the emitter's internal formatting state.
    ///
    /// This method writes the appropriate opening tokens (heading hashes, list prefixes, backticks,
    /// image syntax, emphasis markers, etc.), starts or records block contexts (paragraphs, lists,
    /// blockquotes), and sets up deferred behaviors (for example, deferring the emission of a code
    /// fence until code text arrives for `CodeBlock`). It may modify the provided `StructuralStateMachine`
    ///-related fields such as list and blockquote depth.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` if emitting the required bytes would exceed the emitter's
    /// configured output buffer budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let mut emitter = IncrementalEmitter::new(&budget);
    /// let mut sm = StructuralStateMachine::new();
    /// // Emit a level-2 heading start ("## ")
    /// emitter.handle_enter(&StructuralContext::Heading(2), &mut sm).unwrap();
    /// ```
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
                if self.in_link {
                    self.append_link_text("`");
                } else {
                    self.write_str("`")?;
                }
            }
            StructuralContext::Blockquote => {
                self.emit_block_separator()?;
                self.blockquote_depth = sm.blockquote_depth;
                self.write_blockquote_prefix()?;
            }
            StructuralContext::Link(_) => {
                self.in_link = true;
                self.link_text.clear();
                self.link_text_overflow = false;
            }
            StructuralContext::Image { src, alt } => {
                self.write_str(&format!("![{}]({})", alt, src))?;
            }
            StructuralContext::Bold => {
                if self.in_link {
                    self.append_link_text("**");
                } else {
                    self.write_str("**")?;
                }
            }
            StructuralContext::Italic => {
                if self.in_link {
                    self.append_link_text("*");
                } else {
                    self.write_str("*")?;
                }
            }
            _ => {}
        }
        Ok(())
    }

    // ── Exit handlers ───────────────────────────────────────────────

    /// Handle closing a structural context by emitting the appropriate Markdown closing
    /// syntax, updating internal formatting state, and triggering flushes at block
    /// boundaries when required.
    ///
    /// This writes any necessary trailing characters (newlines, closing fences or
    /// delimiters), updates flags like `last_was_newline`, `needs_block_separator`,
    /// and buffer/list/blockquote depths, and may invoke a flush which can return
    /// `ConversionError::BudgetExceeded` if output buffers would exceed the memory
    /// budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Typical use (types and construction omitted for brevity):
    /// // let mut emitter = IncrementalEmitter::new(&budget);
    /// // let mut sm = StructuralStateMachine::new();
    /// // emitter.handle_exit(&StructuralContext::Paragraph, &mut sm).unwrap();
    /// ```
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
            }
            StructuralContext::Paragraph => {
                self.write_str("\n")?;
                self.last_was_newline = true;
                self.needs_block_separator = true;
            }
            StructuralContext::OrderedList(_) | StructuralContext::UnorderedList => {
                self.list_depth = sm.list_depth;
                self.needs_block_separator = true;
            }
            StructuralContext::ListItem => {
                // Ensure newline after list item content
                if !self.last_was_newline {
                    self.write_str("\n")?;
                    self.last_was_newline = true;
                }
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
            }
            StructuralContext::InlineCode => {
                if self.in_link {
                    self.append_link_text("`");
                } else {
                    self.write_str("`")?;
                }
            }
            StructuralContext::Blockquote => {
                self.blockquote_depth = sm.blockquote_depth;
                self.needs_block_separator = true;
            }
            StructuralContext::Link(href) => {
                self.in_link = false;
                let text = std::mem::take(&mut self.link_text);
                if !text.trim().is_empty() {
                    if href.trim().is_empty() {
                        self.write_str(&text)?;
                    } else {
                        self.write_str(&format!("[{}]({})", text, href))?;
                    }
                }
            }
            StructuralContext::Image { .. } => {
                // Image is fully emitted on Enter (self-closing style)
            }
            StructuralContext::Bold => {
                if self.in_link {
                    self.append_link_text("**");
                } else {
                    self.write_str("**")?;
                }
            }
            StructuralContext::Italic => {
                if self.in_link {
                    self.append_link_text("*");
                } else {
                    self.write_str("*")?;
                }
            }
            _ => {}
        }
        if let Some(tag) = flush_tag_for_context(ctx)
            && is_flush_tag(tag)
        {
            self.trigger_flush()?;
        }
        Ok(())
    }

    // ── Text handler ────────────────────────────────────────────────

    /// Process a text event from the structural state machine and emit the corresponding Markdown fragment.
    ///
    /// If a link is being built, appends `text` to the internal link buffer and emits nothing. If inside a code block,
    /// ensures the deferred opening fence is emitted, writes `text` verbatim (preserving whitespace), and updates
    /// newline tracking. Otherwise, normalizes whitespace in `text` (collapsing runs and preserving leading/trailing
    /// single-space semantics), emits the normalized string if non-empty, and updates newline tracking.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` if emitting the text (or emitting a deferred code fence) would exceed
    /// the configured output buffer budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use nginx_markdown_converter::streaming::emitter::IncrementalEmitter;
    /// use nginx_markdown_converter::streaming::state_machine::StructuralStateMachine;
    /// use nginx_markdown_converter::streaming::MemoryBudget;
    ///
    /// let mut emitter = IncrementalEmitter::new(&MemoryBudget::default());
    /// let mut sm = StructuralStateMachine::new();
    ///
    /// // Normalized emission (multiple spaces collapsed)
    /// emitter.handle_text("Hello   world", &mut sm).unwrap();
    ///
    /// // Inside a code block, whitespace is preserved
    /// // (assume earlier Enter(CodeBlock) was processed)
    /// emitter.handle_text("  code line\n", &mut sm).unwrap();
    /// ```
    fn handle_text(
        &mut self,
        text: &str,
        sm: &mut super::state_machine::StructuralStateMachine,
    ) -> Result<(), ConversionError> {
        if self.in_link {
            self.append_link_text(text);
            return Ok(());
        }

        if self.in_code_block {
            // Emit deferred code fence if not yet emitted
            self.emit_code_fence_if_needed(sm)?;
            // Preserve content inside code blocks verbatim (no
            // blank-line collapsing or whitespace normalization).
            // When inside a blockquote, prepend "> " prefix to each
            // line so the code block renders correctly in Markdown.
            if self.blockquote_depth > 0 {
                self.write_raw_with_blockquote_prefix(text.as_bytes())?;
            } else {
                self.write_raw(text.as_bytes())?;
            }
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

    /// Emit the deferred opening fenced code block using the current language from the state machine.
    ///
    /// This writes an opening triple-backtick fence (` ``` `) to the pending buffer, including the
    /// language identifier if available from the current `StructuralContext` or the emitter's stored
    /// `code_fence_lang`, and marks the fence as emitted. If the fence was already emitted this is a
    /// no-op.
    ///
    /// Returns an error if writing the fence would exceed the configured output buffer budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Construct a state machine and emitter (helpers shown for clarity; actual constructors
    /// // in tests create appropriate MemoryBudget and StructuralStateMachine instances).
    /// let mut sm = super::state_machine::StructuralStateMachine::new();
    /// let budget = super::MemoryBudget::default();
    /// let mut emitter = super::IncrementalEmitter::new(&budget);
    ///
    /// // Simulate entering a code block with language "rust".
    /// sm.push_context(super::state_machine::StructuralContext::CodeBlock(Some("rust".into())));
    /// emitter.code_fence_lang = None; // no stored fallback
    ///
    /// emitter.emit_code_fence_if_needed(&sm).unwrap();
    /// let out = String::from_utf8(emitter.take_flushed()).unwrap();
    /// assert_eq!(out, "```rust\n");
    /// ```
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
                    None
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

    /// Emit a single blank line when a block-level separator is required, then clear the separator flag.
    ///
    /// This writes a newline into the pending output buffer only if `needs_block_separator` is set,
    /// resets `consecutive_blank_lines` to zero when a newline is written, and always clears
    /// `needs_block_separator`.
    ///
    /// # Returns
    ///
    /// `Ok(())` on success; `ConversionError::BudgetExceeded` if writing the separator would exceed the
    /// configured output buffer budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Illustrative example: ensure a block separator is emitted when requested.
    /// # use nginx_markdown_converter::streaming::emitter::{IncrementalEmitter, MemoryBudget};
    /// # use nginx_markdown_converter::streaming::emitter::ConversionError;
    /// let budget = MemoryBudget { output_buffer: 1024 };
    /// let mut emitter = IncrementalEmitter::new(&budget);
    /// emitter.needs_block_separator = true;
    /// emitter.emit_block_separator().expect("should emit separator");
    /// let out = emitter.take_flushed();
    /// // A single newline should be present in flushed output (after flush).
    /// assert!(out.contains(&b'\n'));
    /// ```
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

    /// Write blockquote `> ` prefix markers for the current depth.
    fn write_blockquote_prefix(&mut self) -> Result<(), ConversionError> {
        let prefix_size = 2 * self.blockquote_depth;
        self.check_buffer_budget(prefix_size)?;
        for _ in 0..self.blockquote_depth {
            self.buffer.extend_from_slice(b"> ");
        }
        Ok(())
    }

    /// Write the Markdown list item prefix for the current list context, including
    /// indentation and the appropriate marker (numbered or `-`).
    ///
    /// The method uses `sm.list_depth` to compute indentation (two spaces per level
    /// beyond the first) and `sm.nearest_list_context()` to choose between an
    /// ordered marker (`{n}. `) or an unordered marker (`- `). The prefix is
    /// emitted to the emitter's pending buffer and subject to the emitter's
    /// budget checks.
    ///
    /// # Parameters
    ///
    /// - `sm`: the structural state machine whose `list_depth`, nearest list
    ///   context, and ordered-item numbering determine the prefix contents.
    ///
    /// # Returns
    ///
    /// `Ok(())` on success, `Err(ConversionError::BudgetExceeded)` if emitting the
    /// prefix would exceed the configured output buffer budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// # use nginx_markdown_converter::streaming::{emitter::IncrementalEmitter, state_machine::StructuralStateMachine};
    /// # use nginx_markdown_converter::streaming::ConversionError;
    /// # use nginx_markdown_converter::MemoryBudget;
    /// // Create emitter and state machine (constructors elided for brevity).
    /// let budget = MemoryBudget { output_buffer: 1024 };
    /// let mut emitter = IncrementalEmitter::new(&budget);
    /// let mut sm = StructuralStateMachine::new();
    ///
    /// // Simulate an unordered list item at depth 1
    /// sm.list_depth = 1;
    /// // nearest_list_context returns None or UnorderedList -> prefix will be "- "
    /// emitter.emit_list_prefix(&mut sm).unwrap();
    /// let flushed = emitter.take_flushed();
    /// assert!(std::str::from_utf8(&flushed).unwrap().starts_with("- "));
    /// ```
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

    /// Append text to `link_text`, respecting the output buffer budget.
    ///
    /// When the accumulated link text would exceed `max_buffer_size`,
    /// the overflow flag is set and further appends are dropped.
    /// A truncation marker is appended when possible so output drift is
    /// operator-visible. The link is still closed on `Exit(Link)`.
    fn append_link_text(&mut self, s: &str) {
        const LINK_TRUNCATION_MARKER: &str = "...";

        if self.link_text_overflow {
            return;
        }
        if self.link_text.len().saturating_add(s.len()) > self.max_buffer_size {
            if self.max_buffer_size > 0 {
                let marker_room = self.max_buffer_size.min(LINK_TRUNCATION_MARKER.len());
                let keep = self.max_buffer_size.saturating_sub(marker_room);
                let safe_keep = self.link_text.floor_char_boundary(keep);
                if self.link_text.len() > safe_keep {
                    self.link_text.truncate(safe_keep);
                }
                self.link_text
                    .push_str(&LINK_TRUNCATION_MARKER[..marker_room]);
            }
            self.link_text_overflow = true;
            return;
        }
        self.link_text.push_str(s);
    }

    /// Writes a string into the pending output buffer while applying output normalization.
    ///
    /// This function converts CRLF (`\r\n`) to LF, compresses consecutive blank lines so that
    /// at most one blank line is emitted in a run, and updates internal newline/blank-line
    /// state (`last_was_newline`, `consecutive_blank_lines`). It checks the pending-buffer
    /// budget before writing and returns `ConversionError::BudgetExceeded` if the write would
    /// exceed the configured output buffer limit.
    ///
    /// # Examples
    ///
    /// ```rust,ignore
    /// // Assuming `emitter` is a mutable IncrementalEmitter:
    /// // - "\r\n" is normalized to "\n"
    /// // - multiple consecutive "\n\n\n" are compressed to at most one blank line
    /// emitter.write_str("line1\r\n\r\n\r\nline2\n")?;
    /// ```
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
                    // Prepend blockquote markers on the new line
                    if self.blockquote_depth > 0 {
                        let prefix_size = 2 * self.blockquote_depth;
                        self.check_buffer_budget(prefix_size)?;
                        for _ in 0..self.blockquote_depth {
                            self.buffer.extend_from_slice(b"> ");
                        }
                    }
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
    fn write_raw(&mut self, bytes: &[u8]) -> Result<(), ConversionError> {
        self.check_buffer_budget(bytes.len())?;
        self.buffer.extend_from_slice(bytes);
        self.last_was_newline = bytes.last().copied() == Some(b'\n');
        Ok(())
    }

    /// Write raw bytes with blockquote `> ` prefix on each new line.
    ///
    /// Used for code block content inside blockquotes so each line
    /// gets the correct `> ` prefix per nesting depth.
    fn write_raw_with_blockquote_prefix(&mut self, bytes: &[u8]) -> Result<(), ConversionError> {
        let prefix_per_line = 2 * self.blockquote_depth;
        /* Worst case: every byte is a newline, each needing a prefix */
        let max_extra = bytes.iter().filter(|&&b| b == b'\n').count() * prefix_per_line;
        self.check_buffer_budget(bytes.len() + max_extra)?;

        for &b in bytes {
            self.buffer.push(b);
            if b == b'\n' {
                for _ in 0..self.blockquote_depth {
                    self.buffer.extend_from_slice(b"> ");
                }
            }
        }
        self.last_was_newline = bytes.last().copied() == Some(b'\n');
        Ok(())
    }

    /// Check that adding `additional` bytes won't exceed the buffer budget.
    fn check_buffer_budget(&self, additional: usize) -> Result<(), ConversionError> {
        self.budget
            .check_output_buffer(self.buffer.len(), additional)
    }

    /// Flushes pending output into the ready/flushed buffer and records a flush point.
    ///
    /// Attempts to move any pending bytes into the ready buffer while respecting the configured
    /// output budget; on success increments the emitter's flush counter.
    ///
    /// # Returns
    ///
    /// `Ok(())` on success, `Err(ConversionError::BudgetExceeded)` if moving pending bytes would
    /// exceed the output buffer budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let mut emitter = IncrementalEmitter::new(&budget);
    /// // after writing content into the emitter's pending buffer:
    /// emitter.trigger_flush().unwrap();
    /// assert_eq!(emitter.flush_count(), 1);
    /// ```
    fn trigger_flush(&mut self) -> Result<(), ConversionError> {
        self.flush_to_ready()?;
        self.flush_count = self.flush_count.saturating_add(1);
        Ok(())
    }

    /// Moves pending bytes into the ready (flushed) buffer after applying trailing-whitespace removal to each pending line.
    ///
    /// This performs a budget check for the ready buffer and appends the normalized bytes to `flushed`. If the append would cause the ready buffer to exceed the configured `max_buffer_size`, no bytes are moved and an error is returned.
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` if the resulting flushed buffer size would exceed the output buffer budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // given a mutable `emitter: IncrementalEmitter` with pending data
    /// emitter.flush_to_ready().unwrap();
    /// ```
    fn flush_to_ready(&mut self) -> Result<(), ConversionError> {
        if self.buffer.is_empty() {
            return Ok(());
        }
        let output = if self.in_code_block {
            /* Skip trailing-whitespace normalization inside code blocks */
            std::mem::take(&mut self.buffer)
        } else {
            let normalized = normalize_pending(&self.buffer);
            self.buffer.clear();
            normalized
        };
        let new_flushed_size = self.flushed.len().saturating_add(output.len());
        if new_flushed_size > self.max_buffer_size {
            return Err(ConversionError::BudgetExceeded {
                stage: "output_buffer (ready)".to_string(),
                used: new_flushed_size,
                limit: self.max_buffer_size,
            });
        }
        self.flushed.extend_from_slice(&output);
        Ok(())
    }
}

/// Collapse internal whitespace runs to single spaces while preserving whether the
/// chunk begins or ends with whitespace.
///
/// This function is intended for normalizing streaming text chunks so that joining
/// adjacent chunks preserves spacing at chunk boundaries:
/// - An empty input returns an empty `String`.
/// - An input that is entirely whitespace returns a single space `" "`.
/// - Internal runs of one or more whitespace characters are replaced by a single space.
/// - If the original text starts with any whitespace, the result begins with a single space.
/// - If the original text ends with any whitespace, the result ends with a single space.
///
/// # Examples
///
/// ```ignore
/// assert_eq!(normalize_text(""), "");
/// // internal collapse, preserve trailing space
/// assert_eq!(normalize_text("a   b "), "a b ");
/// // preserve leading and trailing single-space markers
/// assert_eq!(normalize_text("  a\tb\n"), " a b ");
/// // all-whitespace becomes a single space
/// assert_eq!(normalize_text("   \t\n"), " ");
/// ```
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

/// Remove trailing whitespace from each line in the pending buffer and preserve line breaks.
///
/// Decodes `bytes` lossily to UTF-8, trims trailing ASCII whitespace from every line segment, and
/// returns a new `Vec<u8>` containing the normalized bytes. Line separators are preserved: internal
/// newlines remain, and a final trailing newline is preserved if and only if the original `bytes`
/// ended with `\n`.
///
/// # Examples
///
/// ```ignore
/// let input = b"line1  \r\nline2\t\nlast   ";
/// let out = normalize_pending(input);
/// // CR (`\r`) may have been converted during earlier writes; this function preserves `\n`
/// // structure and trims trailing spaces and tabs on each line.
/// assert_eq!(out, b"line1\nline2\nlast");
///
/// let input2 = b"keep\n";
/// let out2 = normalize_pending(input2);
/// assert_eq!(out2, b"keep\n");
/// ```
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

/// Determines whether an HTML tag should trigger a flush boundary.
///
/// The function checks if `tag` is in the emitter's allowlist of block-level tags
/// whose closing should cause the incremental emitter to flush pending output.
///
/// # Examples
///
/// ```ignore
/// assert!(is_flush_tag("p"));    // paragraph
/// assert!(is_flush_tag("h1"));   // heading
/// assert!(!is_flush_tag("span")); // inline element
/// ```
///
/// `true` if `tag` is a block-level flush trigger, `false` otherwise.
pub fn is_flush_tag(tag: &str) -> bool {
    FLUSH_TAGS.contains(&tag)
}

fn flush_tag_for_context(ctx: &StructuralContext) -> Option<&'static str> {
    match ctx {
        StructuralContext::Heading(1) => Some("h1"),
        StructuralContext::Heading(2) => Some("h2"),
        StructuralContext::Heading(3) => Some("h3"),
        StructuralContext::Heading(4) => Some("h4"),
        StructuralContext::Heading(5) => Some("h5"),
        StructuralContext::Heading(6) => Some("h6"),
        StructuralContext::Paragraph => Some("p"),
        StructuralContext::ListItem => Some("li"),
        StructuralContext::CodeBlock(_) => Some("pre"),
        StructuralContext::Blockquote => Some("blockquote"),
        StructuralContext::OrderedList(_) => Some("ol"),
        StructuralContext::UnorderedList => Some("ul"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::streaming::budget::MemoryBudget;
    use crate::streaming::state_machine::{StateMachineAction, StructuralStateMachine};
    use crate::streaming::types::StreamEvent;

    /// Create a default `IncrementalEmitter` and `StructuralStateMachine` pair.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let (mut emitter, mut sm) = make_pair();
    /// // emitter and sm are ready for use in tests or examples, e.g.:
    /// // emitter.process_action(&StateMachineAction::None, &mut sm).unwrap();
    /// ```
    fn make_pair() -> (IncrementalEmitter, StructuralStateMachine) {
        let budget = MemoryBudget::default();
        (
            IncrementalEmitter::new(&budget),
            StructuralStateMachine::new(&budget),
        )
    }

    /// Emit a sequence of `StreamEvent` through the state machine and incremental emitter to produce the final Markdown output.
    ///
    /// Feeds each event into the `StructuralStateMachine` and the `IncrementalEmitter`, collects any already-flushed fragments, finalizes the emitter, and concatenates both buffers into a single UTF-8 `String`.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Construct a sequence of `StreamEvent` for a simple document, then emit:
    /// // let events: Vec<StreamEvent> = vec![ /* events */ ];
    /// // let output = emit_html(&events);
    /// // assert!(output.ends_with('\n'));
    /// ```
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

    /// Create a `StartTag` `StreamEvent` for the given tag name with no attributes and not self-closing.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let ev = start_tag("p");
    /// match ev {
    ///     StreamEvent::StartTag { name, attrs, self_closing } => {
    ///         assert_eq!(name, "p");
    ///         assert!(attrs.is_empty());
    ///         assert!(!self_closing);
    ///     }
    ///     _ => panic!("expected StartTag"),
    /// }
    /// ```
    fn start_tag(name: &str) -> StreamEvent {
        StreamEvent::StartTag {
            name: name.to_string(),
            attrs: vec![],
            self_closing: false,
        }
    }

    /// Creates a non-self-closing `StreamEvent::StartTag` with the provided element name and attributes.
    ///
    /// The attribute pairs are converted to owned `String`s for the event payload.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let ev = start_tag_with_attrs("div", vec![("class", "note"), ("id", "n1")]);
    /// match ev {
    ///     StreamEvent::StartTag { name, attrs, self_closing } => {
    ///         assert_eq!(name, "div");
    ///         assert_eq!(self_closing, false);
    ///         assert_eq!(attrs.get("class").map(|s| s.as_str()), Some("note"));
    ///         assert_eq!(attrs.get("id").map(|s| s.as_str()), Some("n1"));
    ///     }
    ///     _ => panic!("unexpected event variant"),
    /// }
    /// ```
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

    /// Constructs a self-closing `StartTag` `StreamEvent` with the given tag name and attributes.
    ///
    /// The attribute pairs are converted to owned `String`s and attached to the event; the event's
    /// `self_closing` flag is set to `true`.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let ev = self_closing_tag("br", vec![("class", "x")]);
    /// match ev {
    ///     StreamEvent::StartTag { name, attrs, self_closing } => {
    ///         assert_eq!(name, "br");
    ///         assert!(self_closing);
    ///         assert_eq!(attrs.get("class").map(|s| s.as_str()), Some("x"));
    ///     }
    ///     _ => panic!("expected StartTag"),
    /// }
    /// ```
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

    /// Constructs a `StreamEvent::EndTag` for the given tag name.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let ev = end_tag("p");
    /// match ev {
    ///     StreamEvent::EndTag { name } => assert_eq!(name, "p"),
    ///     _ => panic!("expected EndTag"),
    /// }
    /// ```
    fn end_tag(name: &str) -> StreamEvent {
        StreamEvent::EndTag {
            name: name.to_string(),
        }
    }

    /// Creates a `StreamEvent::Text` containing the given string.
    ///
    /// # Returns
    ///
    /// A `StreamEvent::Text` whose payload is `s` converted to a `String`.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let ev = text("hello");
    /// assert_eq!(ev, StreamEvent::Text("hello".to_string()));
    /// ```
    fn text(s: &str) -> StreamEvent {
        StreamEvent::Text(s.to_string())
    }

    // ── Heading tests ───────────────────────────────────────────────

    #[test]
    fn test_heading_h1() {
        let output = emit_html(&[start_tag("h1"), text("Hello"), end_tag("h1")]);
        assert_eq!(output, "# Hello\n");
    }

    /// Verifies that an `h2` HTML element is converted to a level-2 Markdown heading.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let output = emit_html(&[start_tag("h2"), text("World"), end_tag("h2")]);
    /// assert_eq!(output, "## World\n");
    /// ```
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

    #[test]
    fn test_link_with_empty_href_emits_plain_text() {
        let output = emit_html(&[
            start_tag("p"),
            start_tag_with_attrs("a", vec![("href", "")]),
            text("Not a link"),
            end_tag("a"),
            end_tag("p"),
        ]);
        assert!(
            output.contains("Not a link"),
            "link text should be preserved, got: {}",
            output
        );
        assert!(
            !output.contains("[Not a link]()"),
            "empty href must not emit markdown link, got: {}",
            output
        );
    }

    #[test]
    fn test_link_text_overflow_appends_marker() {
        let budget = MemoryBudget {
            total: 1024,
            state_stack: 256,
            output_buffer: 16,
            charset_sniff: 16,
            lookahead: 64,
        };
        let mut emitter = IncrementalEmitter::new(&budget);

        emitter.append_link_text("this text is");
        emitter.append_link_text(" intentionally very long");

        assert!(emitter.link_text_overflow, "overflow flag should be set");
        assert_eq!(emitter.link_text, "this text is...");
    }

    #[test]
    fn test_link_text_overflow_multibyte_chars() {
        let budget = MemoryBudget {
            total: 1024,
            state_stack: 256,
            output_buffer: 12,
            charset_sniff: 16,
            lookahead: 64,
        };
        let mut emitter = IncrementalEmitter::new(&budget);

        emitter.append_link_text("こんにちは世界");
        assert!(emitter.link_text_overflow, "overflow flag should be set");
        assert!(
            std::str::from_utf8(emitter.link_text.as_bytes()).is_ok(),
            "link_text should be valid UTF-8 after truncation"
        );
        assert!(
            emitter.link_text.ends_with("..."),
            "link_text should end with truncation marker"
        );
    }

    // ── Image tests ─────────────────────────────────────────────────

    /// Verifies that an HTML `<img>` inside a paragraph is emitted as Markdown image syntax.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let output = emit_html(&[
    ///     start_tag("p"),
    ///     self_closing_tag("img", vec![("src", "pic.png"), ("alt", "A picture")]),
    ///     end_tag("p"),
    /// ]);
    /// assert!(output.contains("![A picture](pic.png)"));
    /// ```
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
        /* Verify blockquote marker is present */
        assert!(
            output.lines().any(|line| line.starts_with("> ")),
            "Expected blockquote prefix '> ' in output, got: {}",
            output
        );
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

    /// Verifies the emitter produces a flush when a paragraph is closed.
    ///
    /// Feeds a paragraph start, text, and paragraph end through the state machine and
    /// asserts that the emitter's flushed buffer is non-empty after processing the closing tag.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let (mut emitter, mut sm) = make_pair();
    /// let events = vec![start_tag("p"), text("Text"), end_tag("p")];
    /// for ev in &events {
    ///     let action = sm.process_event(ev).unwrap();
    ///     emitter.process_action(&action, &mut sm).unwrap();
    /// }
    /// let flushed = emitter.take_flushed();
    /// assert!(!flushed.is_empty());
    /// ```
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
