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
    /// Stripped element (iframe/form etc) — tags stripped, content kept.
    StrippedElement,
    /// Skipped element (script etc) — all content skipped.
    SkippedElement,
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
    /// Maximum stack depth derived from the memory budget.
    max_stack_depth: usize,
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
    /// Create a new state machine with stack depth derived from the budget.
    ///
    /// # Arguments
    ///
    /// * `budget` - Memory budget; `state_stack` bytes are divided by an
    ///   estimated 64 bytes per context entry to derive the max depth.
    pub fn new(budget: &MemoryBudget) -> Self {
        // Each StructuralContext is roughly 64 bytes.
        let max_depth = budget.state_stack / 64;
        Self {
            stack: Vec::new(),
            max_stack_depth: max_depth.max(16), // minimum 16 levels
            needs_block_separator: false,
            list_depth: 0,
            blockquote_depth: 0,
            in_head: false,
            in_preformatted: false,
            ordered_list_counters: Vec::new(),
        }
    }

    /// Process a stream event and return the action for the emitter.
    ///
    /// # Arguments
    ///
    /// * `event` - The sanitized stream event to process.
    ///
    /// # Returns
    ///
    /// A [`StateMachineAction`] telling the emitter what to do.
    ///
    /// # Errors
    ///
    /// Returns [`ConversionError::BudgetExceeded`] if the state stack
    /// would exceed its bounded depth.
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

    fn handle_start_tag(
        &mut self,
        name: &str,
        attrs: &[(String, String)],
        self_closing: bool,
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
                if self_closing {
                    return Ok(StateMachineAction::Enter(ctx));
                }
                ctx
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
                self.list_depth = self.list_depth.saturating_sub(1);
                self.ordered_list_counters.pop();
                if let Some(ctx) = self.pop_matching_context(name) {
                    self.needs_block_separator = true;
                    Ok(StateMachineAction::Exit(ctx))
                } else {
                    Ok(StateMachineAction::None)
                }
            }
            "ul" => {
                self.list_depth = self.list_depth.saturating_sub(1);
                if let Some(ctx) = self.pop_matching_context(name) {
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

    fn push_context(&mut self, ctx: StructuralContext) -> Result<(), ConversionError> {
        if self.stack.len() >= self.max_stack_depth {
            return Err(ConversionError::BudgetExceeded {
                stage: "state_stack".to_string(),
                used: self.stack.len().saturating_mul(64),
                limit: self.max_stack_depth.saturating_mul(64),
            });
        }
        self.stack.push(ctx);
        Ok(())
    }

    fn pop_matching_context(&mut self, tag_name: &str) -> Option<StructuralContext> {
        // Find the matching context from the top of the stack
        let pos = self
            .stack
            .iter()
            .rposition(|ctx| context_matches_tag(ctx, tag_name));
        if let Some(idx) = pos {
            Some(self.stack.remove(idx))
        } else {
            None
        }
    }

    /// Get the current (top) context.
    pub fn current_context(&self) -> Option<&StructuralContext> {
        self.stack.last()
    }

    /// Get the current ordered list item number and increment it.
    pub fn next_ordered_item_number(&mut self) -> u32 {
        if let Some(counter) = self.ordered_list_counters.last_mut() {
            let num = *counter;
            *counter = counter.saturating_add(1);
            num
        } else {
            1
        }
    }

    /// Check if we are inside a specific context type.
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

    /// Finalize: auto-close all unclosed contexts.
    ///
    /// Returns the list of contexts that were auto-closed, in the order
    /// they were popped (innermost first).
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

    /// Current stack depth.
    pub fn depth(&self) -> usize {
        self.stack.len()
    }

    /// Estimated bytes consumed by the state stack (for working set tracking).
    pub fn stack_bytes_estimate(&self) -> usize {
        // Each StructuralContext is roughly 64 bytes.
        self.stack.len().saturating_mul(64)
    }
}

/// Check if a tag name corresponds to a block-level element.
fn is_block_level(tag: &str) -> bool {
    matches!(
        tag,
        "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "p" | "pre" | "blockquote" | "li" | "ol" | "ul"
    )
}

/// Check if a [`StructuralContext`] matches a closing tag name.
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

    fn default_sm() -> StructuralStateMachine {
        StructuralStateMachine::new(&MemoryBudget::default())
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

    fn end_tag(name: &str) -> StreamEvent {
        StreamEvent::EndTag {
            name: name.to_string(),
        }
    }

    fn text(s: &str) -> StreamEvent {
        StreamEvent::Text(s.to_string())
    }

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
    fn test_blockquote_depth() {
        let mut sm = default_sm();
        sm.process_event(&start_tag("blockquote")).unwrap();
        assert_eq!(sm.blockquote_depth, 1);
        sm.process_event(&start_tag("blockquote")).unwrap();
        assert_eq!(sm.blockquote_depth, 2);
        sm.process_event(&end_tag("blockquote")).unwrap();
        assert_eq!(sm.blockquote_depth, 1);
    }

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
            // 16 * 64 = 1024 bytes → max depth 16 (the minimum)
            state_stack: 1024,
            ..MemoryBudget::default()
        };
        let mut sm = StructuralStateMachine::new(&budget);
        assert_eq!(sm.max_stack_depth, 16);
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

    #[test]
    fn test_ordered_list_custom_start() {
        let mut sm = default_sm();
        sm.process_event(&start_tag_with_attrs("ol", vec![("start", "5")]))
            .unwrap();
        assert_eq!(sm.next_ordered_item_number(), 5);
        assert_eq!(sm.next_ordered_item_number(), 6);
    }
}
