//! Structural state machine for tracking HTML document context.
//!
//! Maintains a bounded stack of [`StructuralContext`] values that the
//! [`IncrementalEmitter`](super::emitter::IncrementalEmitter) uses to
//! produce correct Markdown syntax.

use crate::error::ConversionError;
use crate::streaming::budget::MemoryBudget;
use crate::streaming::types::StreamEvent;

/// Document structure context tracked on the state stack.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StructuralContext {
    /// Document root.
    Root,
    /// Heading level (h1-h6).
    Heading(u8),
    /// Paragraph.
    Paragraph,
    /// Ordered list with current item number.
    OrderedList(u32),
    /// Unordered list.
    UnorderedList,
    /// List item.
    ListItem,
    /// Code block with optional language identifier.
    CodeBlock(Option<String>),
    /// Inline code.
    InlineCode,
    /// Blockquote.
    Blockquote,
    /// Link with href.
    Link(String),
    /// Image with src and alt text.
    Image { src: String, alt: String },
    /// Bold text.
    Bold,
    /// Italic text.
    Italic,
    /// Table element (triggers fallback).
    Table,
}

/// Action produced by the state machine for the emitter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StateMachineAction {
    /// Push a new context and begin emitting for it.
    Enter(StructuralContext),
    /// Pop the current context and finalize its emission.
    Exit(StructuralContext),
    /// Emit text content within the current context.
    Text(String),
    /// A table was detected — signal pre-commit fallback.
    FallbackRequired,
    /// No action needed (e.g., for ignored events).
    None,
}

/// Structural state machine with bounded stack.
///
/// Tracks document structure context for the Markdown emitter. The stack
/// depth is bounded by [`MemoryBudget::state_stack`] to enforce the
/// bounded-memory contract.
pub struct StructuralStateMachine {
    /// Bounded context stack.
    stack: Vec<StructuralContext>,
    /// Memory budget used for stack enforcement.
    budget: MemoryBudget,
    /// Whether the previous block-level element needs a blank line separator.
    pub needs_block_separator: bool,
    /// Current list nesting depth (for indentation).
    pub list_depth: usize,
    /// Current blockquote nesting depth.
    pub blockquote_depth: usize,
    /// Whether we are inside the `<head>` region.
    pub in_head: bool,
    /// Whether we are inside a `<pre>` region.
    pub in_preformatted: bool,
    /// Current ordered list item counters per nesting level.
    ordered_list_counters: Vec<u32>,
}

impl StructuralStateMachine {
    /// Create a StructuralStateMachine with a maximum stack depth derived from `budget`.
    ///
    /// The `state_stack` byte allowance from `MemoryBudget` is enforced using
    /// an estimated 64 bytes per `StructuralContext` stack entry.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let budget = MemoryBudget { state_stack: 1024 };
    /// let sm = StructuralStateMachine::new(&budget);
    /// // new machine starts with an empty stack
    /// assert_eq!(sm.depth(), 0);
    /// ```
    pub fn new(budget: &MemoryBudget) -> Self {
        Self {
            stack: Vec::new(),
            budget: budget.clone(),
            needs_block_separator: false,
            list_depth: 0,
            blockquote_depth: 0,
            in_head: false,
            in_preformatted: false,
            ordered_list_counters: Vec::new(),
        }
    }

    /// Update the state machine with a stream event and produce the corresponding emitter action.
    ///
    /// Processes a sanitized `StreamEvent`, updating internal structural state (stack, nesting counters,
    /// and flags) as needed and returning a `StateMachineAction` that directs the emitter.
    ///
    /// # Returns
    ///
    /// `StateMachineAction` indicating how the emitter should proceed (`Enter`, `Exit`, `Text`,
    /// `FallbackRequired`, or `None`).
    ///
    /// # Errors
    ///
    /// Returns `ConversionError::BudgetExceeded` if pushing a new context would exceed the configured
    /// state stack budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use nginx_markdown_converter::streaming::state_machine::{StructuralStateMachine, StreamEvent, StateMachineAction};
    /// use nginx_markdown_converter::memory::MemoryBudget;
    ///
    /// let budget = MemoryBudget { state_stack: 1024 }; // example budget
    /// let mut sm = StructuralStateMachine::new(&budget);
    /// let action = sm.process_event(&StreamEvent::Text("hello".into())).unwrap();
    /// assert_eq!(action, StateMachineAction::Text("hello".into()));
    /// ```
    pub fn process_event(
        &mut self,
        event: &StreamEvent,
    ) -> Result<StateMachineAction, ConversionError> {
        match event {
            StreamEvent::StartTag {
                name,
                attrs,
                self_closing,
            } => self.handle_start_tag(name, attrs, *self_closing),
            StreamEvent::EndTag { name } => self.handle_end_tag(name),
            StreamEvent::Text(text) => Ok(StateMachineAction::Text(text.clone())),
            StreamEvent::Comment(_) | StreamEvent::Doctype | StreamEvent::ParseError(_) => {
                Ok(StateMachineAction::None)
            }
        }
    }

    /// Handle an HTML start tag by updating the structural state machine and producing an action for the emitter.
    ///
    /// Recognizes common structural and inline tags, updates nesting trackers (lists, blockquotes, preformatted/head), extracts
    /// attributes for links/images/ordered lists/code languages, and either pushes a corresponding `StructuralContext` or
    /// returns an immediate action. Table-related start tags produce `StateMachineAction::FallbackRequired`. Some tags
    /// (structural wrappers, unknown tags, and `head`) are ignored and produce `StateMachineAction::None`. A self-closing
    /// `img` produces an `Enter(Image { .. })` without pushing an additional inline context.
    ///
    /// # Errors
    /// Returns `ConversionError::BudgetExceeded` if pushing a new context would exceed the configured stack budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// use nginx_markdown_converter::streaming::state_machine::*;
    ///
    /// let budget = MemoryBudget::default();
    /// let mut sm = StructuralStateMachine::new(&budget);
    /// let action = sm.handle_start_tag("p", &[], false).unwrap();
    /// assert!(matches!(action, StateMachineAction::Enter(_)));
    /// ```
    fn handle_start_tag(
        &mut self,
        name: &str,
        attrs: &[(String, String)],
        _self_closing: bool,
    ) -> Result<StateMachineAction, ConversionError> {
        let ctx = match name {
            "h1" => StructuralContext::Heading(1),
            "h2" => StructuralContext::Heading(2),
            "h3" => StructuralContext::Heading(3),
            "h4" => StructuralContext::Heading(4),
            "h5" => StructuralContext::Heading(5),
            "h6" => StructuralContext::Heading(6),
            "p" => StructuralContext::Paragraph,
            "ol" => {
                let start = attrs
                    .iter()
                    .find(|(k, _)| k == "start")
                    .and_then(|(_, v)| v.parse::<u32>().ok())
                    .unwrap_or(1);
                self.list_depth = self.list_depth.saturating_add(1);
                self.ordered_list_counters.push(start);
                StructuralContext::OrderedList(start)
            }
            "ul" => {
                self.list_depth = self.list_depth.saturating_add(1);
                StructuralContext::UnorderedList
            }
            "li" => StructuralContext::ListItem,
            "pre" => {
                self.in_preformatted = true;
                StructuralContext::CodeBlock(None)
            }
            "code" => {
                // If parent is CodeBlock (pre>code), extract language from class
                if self.current_context() == Some(&StructuralContext::CodeBlock(None)) {
                    let lang = attrs.iter().find(|(k, _)| k == "class").and_then(|(_, v)| {
                        v.split_whitespace()
                            .find(|c| c.starts_with("language-"))
                            .map(|c| c.trim_start_matches("language-").to_string())
                    });
                    if lang.is_some() {
                        // Update the parent CodeBlock context with the language
                        if let Some(last) = self.stack.last_mut() {
                            *last = StructuralContext::CodeBlock(lang.clone());
                        }
                    }
                    // Don't push separate context for code inside pre
                    return Ok(StateMachineAction::None);
                }
                StructuralContext::InlineCode
            }
            "blockquote" => {
                self.blockquote_depth = self.blockquote_depth.saturating_add(1);
                StructuralContext::Blockquote
            }
            "a" => {
                let href = attrs
                    .iter()
                    .find(|(k, _)| k == "href")
                    .map(|(_, v)| v.clone())
                    .unwrap_or_default();
                StructuralContext::Link(href)
            }
            "img" => {
                let src = attrs
                    .iter()
                    .find(|(k, _)| k == "src")
                    .map(|(_, v)| v.clone())
                    .unwrap_or_default();
                let alt = attrs
                    .iter()
                    .find(|(k, _)| k == "alt")
                    .map(|(_, v)| v.clone())
                    .unwrap_or_default();
                let ctx = StructuralContext::Image { src, alt };
                // <img> is a void element — always emit Enter without
                // pushing to the stack, regardless of whether the HTML
                // uses XHTML-style self-closing (`<img />`). html5ever's
                // tokenizer sets self_closing only for the slash form,
                // but <img> never has a matching end tag either way.
                return Ok(StateMachineAction::Enter(ctx));
            }
            "strong" | "b" => StructuralContext::Bold,
            "em" | "i" => StructuralContext::Italic,
            "table" | "thead" | "tbody" | "tr" | "th" | "td" => {
                return Ok(StateMachineAction::FallbackRequired);
            }
            "head" => {
                self.in_head = true;
                return Ok(StateMachineAction::None);
            }
            // Structural wrappers and inline elements — pass through
            "html" | "body" | "div" | "span" | "section" | "article" | "main" | "header"
            | "footer" | "nav" | "aside" | "figure" | "figcaption" | "details" | "summary"
            | "mark" | "time" | "abbr" | "cite" | "dfn" | "sub" | "sup" | "small" | "del"
            | "ins" | "s" | "br" | "hr" | "wbr" => {
                // Implicit </head>: html5ever's tokenizer (not tree builder)
                // does not emit an EndTag("head") when <body> appears, so we
                // must clear in_head here to stop metadata extraction from
                // running on body content.
                if name == "body" {
                    self.in_head = false;
                }
                return Ok(StateMachineAction::None);
            }
            // Unknown elements — pass through
            _ => {
                return Ok(StateMachineAction::None);
            }
        };

        self.push_context(ctx.clone())?;
        Ok(StateMachineAction::Enter(ctx))
    }

    /// Process an HTML end tag, pop the corresponding structural context if present, and update
    /// internal nesting/tracking state accordingly.
    ///
    /// For matching contexts (e.g., headings, paragraphs, list items, pre/code, blockquotes,
    /// links, emphasis), this removes the nearest matching context from the stack, adjusts
    /// list or blockquote depths or the preformatted flag as appropriate, sets
    /// `needs_block_separator` for block-level elements, and returns `StateMachineAction::Exit`
    /// with the popped context. Special-case behavior:
    /// - `ol` and `ul`: decrement `list_depth`; `ol` also pops the ordered-list counter.
    /// - `head`: sets `in_head` to `false` and does not modify the stack.
    /// - Unknown or unmatched end tags produce `StateMachineAction::None`.
    ///
    /// # Returns
    ///
    /// `StateMachineAction::Exit(ctx)` when a matching context `ctx` was popped, `StateMachineAction::None` otherwise.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Illustrative usage; actual construction of `StructuralStateMachine` and pushing of
    /// // contexts is required for a non-None result.
    /// let mut sm = StructuralStateMachine::new(&MemoryBudget::default());
    /// let action = sm.handle_end_tag("p").unwrap();
    /// // If no matching `Paragraph` context was on the stack, this yields `StateMachineAction::None`.
    /// assert!(matches!(action, StateMachineAction::None));
    /// ```
    fn handle_end_tag(&mut self, name: &str) -> Result<StateMachineAction, ConversionError> {
        match name {
            "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "p" | "li" | "pre" | "blockquote" | "a"
            | "strong" | "b" | "em" | "i" | "code" => {
                if let Some(ctx) = self.pop_matching_context(name) {
                    // Update tracking state
                    match &ctx {
                        StructuralContext::OrderedList(_) | StructuralContext::UnorderedList => {
                            self.list_depth = self.list_depth.saturating_sub(1);
                        }
                        StructuralContext::Blockquote => {
                            self.blockquote_depth = self.blockquote_depth.saturating_sub(1);
                        }
                        StructuralContext::CodeBlock(_) => {
                            self.in_preformatted = false;
                        }
                        _ => {}
                    }
                    // Mark that we need a block separator after block-level elements
                    if is_block_level(name) {
                        self.needs_block_separator = true;
                    }
                    Ok(StateMachineAction::Exit(ctx))
                } else {
                    Ok(StateMachineAction::None)
                }
            }
            "ol" => {
                if let Some(ctx) = self.pop_matching_context(name) {
                    self.list_depth = self.list_depth.saturating_sub(1);
                    self.ordered_list_counters.pop();
                    self.needs_block_separator = true;
                    Ok(StateMachineAction::Exit(ctx))
                } else {
                    Ok(StateMachineAction::None)
                }
            }
            "ul" => {
                if let Some(ctx) = self.pop_matching_context(name) {
                    self.list_depth = self.list_depth.saturating_sub(1);
                    self.needs_block_separator = true;
                    Ok(StateMachineAction::Exit(ctx))
                } else {
                    Ok(StateMachineAction::None)
                }
            }
            "head" => {
                self.in_head = false;
                Ok(StateMachineAction::None)
            }
            _ => Ok(StateMachineAction::None),
        }
    }

    /// Pushes a `StructuralContext` onto the state's context stack while enforcing the configured maximum depth.
    ///
    /// If pushing another entry would exceed `budget.state_stack`, this returns
    /// `ConversionError::BudgetExceeded` with `stage` set to `"state_stack"` and
    /// `used`/`limit` reporting the estimated bytes consumed and allowed (each
    /// stack slot is accounted as 64 bytes).
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // assuming `machine` is a `StructuralStateMachine` created elsewhere:
    /// let _ = machine.push_context(StructuralContext::Paragraph);
    /// ```
    fn push_context(&mut self, ctx: StructuralContext) -> Result<(), ConversionError> {
        let current_stack_bytes = self.stack.len().saturating_mul(64);
        self.budget.check_state_stack(current_stack_bytes, 64)?;
        self.stack.push(ctx);
        Ok(())
    }

    /// Removes and returns the nearest (innermost) stacked `StructuralContext` that corresponds to the given HTML tag name.
    ///
    /// The search scans the stack from top to bottom and, if a matching context is found, removes it from the stack and returns it. If no matching context exists, returns `None`.
    ///
    /// # Returns
    ///
    /// The removed `StructuralContext` if a match was found, `None` otherwise.
    fn pop_matching_context(&mut self, tag_name: &str) -> Option<StructuralContext> {
        // Find the matching context from the top of the stack
        let pos = self
            .stack
            .iter()
            .rposition(|ctx| context_matches_tag(ctx, tag_name));
        if let Some(idx) = pos {
            if idx + 1 == self.stack.len() {
                self.stack.pop()
            } else {
                Some(self.stack.remove(idx))
            }
        } else {
            None
        }
    }

    /// Retrieve the current top structural context, if any.
    ///
    /// # Returns
    ///
    /// `Some(&StructuralContext)` with the innermost (top) context when the state stack is non-empty, `None` otherwise.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// # // Setup hidden helpers so the example focuses on `current_context`.
    /// # use nginx_markdown_converter::streaming::state_machine::{StructuralStateMachine, StructuralContext};
    /// # #[allow(dead_code)]
    /// # struct MemoryBudget { pub state_stack: usize }
    /// # impl MemoryBudget { fn new(bytes: usize) -> Self { MemoryBudget { state_stack: bytes } } }
    /// let sm = StructuralStateMachine::new(&MemoryBudget::new(1024));
    /// assert!(sm.current_context().is_none());
    /// ```
    pub fn current_context(&self) -> Option<&StructuralContext> {
        self.stack.last()
    }

    /// Get the next item number for the current ordered list and advance its counter.
    ///
    /// # Returns
    ///
    /// The current item number for the innermost ordered list; returns `1` if not inside any ordered list.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let mut sm = StructuralStateMachine::new(&MemoryBudget::default());
    /// // simulate an ordered list that starts at 3
    /// sm.ordered_list_counters.push(3);
    /// assert_eq!(sm.next_ordered_item_number(), 3);
    /// assert_eq!(sm.next_ordered_item_number(), 4);
    /// ```
    pub fn next_ordered_item_number(&mut self) -> u32 {
        if let Some(counter) = self.ordered_list_counters.last_mut() {
            let num = *counter;
            *counter = counter.saturating_add(1);
            num
        } else {
            1
        }
    }

    /// Determine whether any active structural context satisfies a predicate.
    ///
    /// The provided `check` predicate is applied to each context on the internal stack;
    /// the method returns `true` if at least one context makes the predicate evaluate to `true`.
    ///
    /// # Parameters
    ///
    /// - `check`: A predicate tested against each active `StructuralContext`.
    ///
    /// # Returns
    ///
    /// `true` if any stacked context satisfies `check`, `false` otherwise.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Check whether the state machine is currently inside a blockquote.
    /// let mut sm = StructuralStateMachine::new(&MemoryBudget::default());
    /// // ... events that may push Blockquote ...
    /// let inside_blockquote = sm.is_inside(&|ctx| matches!(ctx, StructuralContext::Blockquote));
    /// ```
    pub fn is_inside(&self, check: &dyn Fn(&StructuralContext) -> bool) -> bool {
        self.stack.iter().any(check)
    }

    /// Find the nearest enclosing list context (searching from stack top).
    ///
    /// Returns `Some(&StructuralContext)` for the innermost `OrderedList`
    /// or `UnorderedList`, or `None` if not inside any list.
    pub fn nearest_list_context(&self) -> Option<&StructuralContext> {
        self.stack.iter().rev().find(|ctx| {
            matches!(
                ctx,
                StructuralContext::OrderedList(_) | StructuralContext::UnorderedList
            )
        })
    }

    /// Auto-close all open structural contexts and return them.
    ///
    /// This will pop every context from the machine's stack, update internal
    /// nesting trackers (list and blockquote depths, `in_preformatted`) as each
    /// context is closed, clear any ordered-list counters, and collect the closed
    /// contexts in pop order.
    ///
    /// Returns the list of contexts that were auto-closed, in the order they were
    /// popped (innermost first).
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Given a state machine with some open contexts:
    /// // let mut sm = StructuralStateMachine::new(&memory_budget);
    /// // ... (push some contexts)
    /// let closed = sm.finalize();
    /// // `closed` contains the contexts that were closed, inner-most first.
    /// ```
    pub fn finalize(&mut self) -> Vec<StructuralContext> {
        let mut closed = Vec::new();
        while let Some(ctx) = self.stack.pop() {
            match &ctx {
                StructuralContext::OrderedList(_) | StructuralContext::UnorderedList => {
                    self.list_depth = self.list_depth.saturating_sub(1);
                }
                StructuralContext::Blockquote => {
                    self.blockquote_depth = self.blockquote_depth.saturating_sub(1);
                }
                StructuralContext::CodeBlock(_) => {
                    self.in_preformatted = false;
                }
                _ => {}
            }
            closed.push(ctx);
        }
        self.ordered_list_counters.clear();
        closed
    }

    /// Number of contexts currently on the state stack.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// // Create a machine with a sufficiently large memory budget (example only).
    /// let budget = crate::MemoryBudget { state_stack: 1024 };
    /// let sm = crate::streaming::state_machine::StructuralStateMachine::new(&budget);
    /// assert_eq!(sm.depth(), 0);
    /// ```
    pub fn depth(&self) -> usize {
        self.stack.len()
    }

    /// Estimated bytes consumed by the state stack (for working set tracking).
    pub fn stack_bytes_estimate(&self) -> usize {
        // Each StructuralContext is roughly 64 bytes.
        self.stack.len().saturating_mul(64)
    }
}

/// Determines whether an HTML tag name represents a block-level element.
///
/// # Returns
///
/// `true` if `tag` is a block-level element (`h1`–`h6`, `p`, `pre`, `blockquote`, `li`, `ol`, or `ul`), `false` otherwise.
///
/// # Examples
///
/// ```ignore
/// assert!(is_block_level("p"));
/// assert!(is_block_level("h3"));
/// assert!(!is_block_level("span"));
/// ```
fn is_block_level(tag: &str) -> bool {
    matches!(
        tag,
        "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "p" | "pre" | "blockquote" | "li" | "ol" | "ul"
    )
}

/// Determine whether a `StructuralContext` corresponds to the given HTML closing tag name.
///
/// The comparison is name-based (e.g., `Heading(1)` ↔ `"h1"`, `Paragraph` ↔ `"p"`, `OrderedList(_)` ↔ `"ol"`, etc.).
///
/// # Parameters
///
/// - `ctx`: the structural context to compare.
/// - `tag`: the lowercase HTML tag name to match against.
///
/// # Returns
///
/// `true` if the context corresponds to the provided tag name, `false` otherwise.
///
/// # Examples
///
/// ```ignore
/// let ctx = StructuralContext::Paragraph;
/// assert!(context_matches_tag(&ctx, "p"));
///
/// let h3 = StructuralContext::Heading(3);
/// assert!(context_matches_tag(&h3, "h3"));
///
/// let ol = StructuralContext::OrderedList(1);
/// assert!(context_matches_tag(&ol, "ol"));
///
/// assert!(!context_matches_tag(&h3, "h2"));
/// ```
fn context_matches_tag(ctx: &StructuralContext, tag: &str) -> bool {
    matches!(
        (ctx, tag),
        (StructuralContext::Heading(1), "h1")
            | (StructuralContext::Heading(2), "h2")
            | (StructuralContext::Heading(3), "h3")
            | (StructuralContext::Heading(4), "h4")
            | (StructuralContext::Heading(5), "h5")
            | (StructuralContext::Heading(6), "h6")
            | (StructuralContext::Paragraph, "p")
            | (StructuralContext::OrderedList(_), "ol")
            | (StructuralContext::UnorderedList, "ul")
            | (StructuralContext::ListItem, "li")
            | (StructuralContext::CodeBlock(_), "pre")
            | (StructuralContext::InlineCode, "code")
            | (StructuralContext::Blockquote, "blockquote")
            | (StructuralContext::Link(_), "a")
            | (StructuralContext::Bold, "strong" | "b")
            | (StructuralContext::Italic, "em" | "i")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Create a `StructuralStateMachine` initialized with the default memory budget.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let sm = default_sm();
    /// assert_eq!(sm.depth(), 0);
    /// ```
    fn default_sm() -> StructuralStateMachine {
        StructuralStateMachine::new(&MemoryBudget::default())
    }

    /// Create a `StreamEvent::StartTag` with the given tag name, no attributes, and `self_closing` set to `false`.
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

    /// Create a `StreamEvent::StartTag` for `name` with the provided attributes.
    ///
    /// `attrs` is a list of `(key, value)` attribute pairs which will be converted into owned `String`s.
    /// The returned event has `self_closing` set to `false`.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let ev = start_tag_with_attrs("a", vec![("href", "https://example.com")]);
    /// if let StreamEvent::StartTag { name, attrs, self_closing } = ev {
    ///     assert_eq!(name, "a");
    ///     assert_eq!(attrs, vec![("href".to_string(), "https://example.com".to_string())]);
    ///     assert!(!self_closing);
    /// } else {
    ///     panic!("expected StartTag");
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

    /// Create a `StreamEvent::EndTag` for the given tag name.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let ev = end_tag("p");
    /// match ev {
    ///     StreamEvent::EndTag { name } => assert_eq!(name, "p"),
    ///     _ => panic!("unexpected event"),
    /// }
    /// ```
    fn end_tag(name: &str) -> StreamEvent {
        StreamEvent::EndTag {
            name: name.to_string(),
        }
    }

    /// Create a `StreamEvent::Text` containing the given string.
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

    /// Verifies that an `h1` start tag enters a `Heading(1)` context and that the corresponding end tag exits it and requests a block separator.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let mut sm = default_sm();
    /// let action = sm.process_event(&start_tag("h1")).unwrap();
    /// assert_eq!(action, StateMachineAction::Enter(StructuralContext::Heading(1)));
    /// let action = sm.process_event(&end_tag("h1")).unwrap();
    /// assert_eq!(action, StateMachineAction::Exit(StructuralContext::Heading(1)));
    /// assert!(sm.needs_block_separator);
    /// ```
    #[test]
    fn test_heading_context() {
        let mut sm = default_sm();
        let action = sm.process_event(&start_tag("h1")).unwrap();
        assert_eq!(
            action,
            StateMachineAction::Enter(StructuralContext::Heading(1))
        );
        let action = sm.process_event(&end_tag("h1")).unwrap();
        assert_eq!(
            action,
            StateMachineAction::Exit(StructuralContext::Heading(1))
        );
        assert!(sm.needs_block_separator);
    }

    #[test]
    fn test_nested_list_depth() {
        let mut sm = default_sm();
        sm.process_event(&start_tag("ul")).unwrap();
        assert_eq!(sm.list_depth, 1);
        sm.process_event(&start_tag("li")).unwrap();
        sm.process_event(&start_tag("ul")).unwrap();
        assert_eq!(sm.list_depth, 2);
        sm.process_event(&end_tag("ul")).unwrap();
        assert_eq!(sm.list_depth, 1);
        sm.process_event(&end_tag("li")).unwrap();
        sm.process_event(&end_tag("ul")).unwrap();
        assert_eq!(sm.list_depth, 0);
    }

    #[test]
    fn test_unmatched_list_end_tag_does_not_mutate_state() {
        let mut sm = default_sm();

        sm.process_event(&start_tag("ol")).unwrap();
        assert_eq!(sm.list_depth, 1);
        assert_eq!(sm.next_ordered_item_number(), 1);

        let action = sm.process_event(&end_tag("ul")).unwrap();
        assert_eq!(action, StateMachineAction::None);
        assert_eq!(sm.list_depth, 1);
        assert_eq!(sm.next_ordered_item_number(), 2);
    }

    #[test]
    fn test_blockquote_depth() {
        let mut sm = default_sm();
        sm.process_event(&start_tag("blockquote")).unwrap();
        assert_eq!(sm.blockquote_depth, 1);
        sm.process_event(&start_tag("blockquote")).unwrap();
        assert_eq!(sm.blockquote_depth, 2);
        sm.process_event(&end_tag("blockquote")).unwrap();
        assert_eq!(sm.blockquote_depth, 1);
    }

    /// Verifies that a `pre` element containing a `code` element with a `language-*` class
    /// records the language on the `CodeBlock` context and marks the state machine as preformatted.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let mut sm = default_sm();
    /// sm.process_event(&start_tag("pre")).unwrap();
    /// sm.process_event(&start_tag_with_attrs(
    ///     "code",
    ///     vec![("class", "language-rust")],
    /// )).unwrap();
    /// assert!(sm.in_preformatted);
    /// assert_eq!(
    ///     sm.current_context(),
    ///     Some(&StructuralContext::CodeBlock(Some("rust".to_string())))
    /// );
    /// ```
    #[test]
    fn test_code_block_with_language() {
        let mut sm = default_sm();
        sm.process_event(&start_tag("pre")).unwrap();
        sm.process_event(&start_tag_with_attrs(
            "code",
            vec![("class", "language-rust")],
        ))
        .unwrap();
        assert!(sm.in_preformatted);
        // The CodeBlock context should now have the language
        assert_eq!(
            sm.current_context(),
            Some(&StructuralContext::CodeBlock(Some("rust".to_string())))
        );
    }

    #[test]
    fn test_table_triggers_fallback() {
        let mut sm = default_sm();
        let action = sm.process_event(&start_tag("table")).unwrap();
        assert_eq!(action, StateMachineAction::FallbackRequired);
    }

    #[test]
    fn test_stack_depth_exceeded() {
        let budget = MemoryBudget {
            // 16 * 64 = 1024 bytes → max depth 16.
            state_stack: 1024,
            ..MemoryBudget::default()
        };
        let mut sm = StructuralStateMachine::new(&budget);
        // Fill the stack to the limit with alternating bold/italic
        for _ in 0..8 {
            sm.process_event(&start_tag("strong")).unwrap();
            sm.process_event(&start_tag("em")).unwrap();
        }
        assert_eq!(sm.depth(), 16);
        // Next push should exceed the limit
        let err = sm.process_event(&start_tag("p")).unwrap_err();
        assert_eq!(err.code(), 6); // BudgetExceeded
    }

    #[test]
    fn test_state_stack_budget_respected_without_flooring() {
        let budget = MemoryBudget {
            state_stack: 64,
            ..MemoryBudget::default()
        };
        let mut sm = StructuralStateMachine::new(&budget);

        sm.process_event(&start_tag("strong")).unwrap();
        let err = sm.process_event(&start_tag("em")).unwrap_err();
        assert_eq!(err.code(), 6);
    }

    #[test]
    fn test_finalize_closes_unclosed() {
        let mut sm = default_sm();
        sm.process_event(&start_tag("p")).unwrap();
        sm.process_event(&start_tag("strong")).unwrap();
        let closed = sm.finalize();
        assert_eq!(closed.len(), 2);
        assert_eq!(sm.depth(), 0);
    }

    #[test]
    fn test_text_passthrough() {
        let mut sm = default_sm();
        let action = sm.process_event(&text("hello")).unwrap();
        assert_eq!(action, StateMachineAction::Text("hello".to_string()));
    }

    #[test]
    fn test_head_tracking() {
        let mut sm = default_sm();
        sm.process_event(&start_tag("head")).unwrap();
        assert!(sm.in_head);
        sm.process_event(&end_tag("head")).unwrap();
        assert!(!sm.in_head);
    }

    #[test]
    fn test_ordered_list_counter() {
        let mut sm = default_sm();
        sm.process_event(&start_tag("ol")).unwrap();
        assert_eq!(sm.next_ordered_item_number(), 1);
        assert_eq!(sm.next_ordered_item_number(), 2);
        assert_eq!(sm.next_ordered_item_number(), 3);
    }

    /// Verifies that an `ol` element with a `start` attribute initializes the ordered-list counter.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let mut sm = default_sm();
    /// sm.process_event(&start_tag_with_attrs("ol", vec![("start", "5")])).unwrap();
    /// assert_eq!(sm.next_ordered_item_number(), 5);
    /// assert_eq!(sm.next_ordered_item_number(), 6);
    /// ```
    #[test]
    fn test_ordered_list_custom_start() {
        let mut sm = default_sm();
        sm.process_event(&start_tag_with_attrs("ol", vec![("start", "5")]))
            .unwrap();
        assert_eq!(sm.next_ordered_item_number(), 5);
        assert_eq!(sm.next_ordered_item_number(), 6);
    }
}
