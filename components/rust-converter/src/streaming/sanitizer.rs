//! Streaming security sanitizer for the token pipeline.
//!
//! Provides security validation equivalent to [`SecurityValidator`](crate::security::SecurityValidator)
//! but operating on [`StreamEvent`] tokens instead of DOM nodes.
//!
//! # Security Guarantees
//!
//! - Dangerous elements (script, style, etc.) and all their children are removed
//! - Embedded content elements (iframe, object, embed) have tags stripped but
//!   child content preserved; src/data URLs are extracted
//! - Form elements have tags stripped but child content preserved
//! - Event handler attributes (on*) are removed
//! - Dangerous URL schemes (javascript:, data:, etc.) are blocked
//! - Nesting depth is limited to prevent stack exhaustion

use crate::streaming::types::StreamEvent;

/// Elements whose entire subtree is removed (content discarded).
const DANGEROUS_ELEMENTS: &[&str] = &["script", "style", "noscript", "applet", "link", "base"];

/// Embedded content elements: tags stripped, src/data URL extracted.
const EMBEDDED_CONTENT_ELEMENTS: &[&str] = &["iframe", "object", "embed"];

/// Form elements: tags stripped, child text preserved.
const FORM_ELEMENTS: &[&str] = &[
    "form", "button", "select", "textarea", "fieldset", "legend", "label", "option", "optgroup",
    "datalist", "output",
];

/// Void form controls that have no children.
const VOID_FORM_CONTROLS: &[&str] = &["input"];

/// URL schemes that are blocked for security.
const DANGEROUS_URL_SCHEMES: &[&str] = &["javascript:", "data:", "vbscript:", "file:", "about:"];

/// Maximum nesting depth (matches SecurityValidator).
const MAX_NESTING_DEPTH: usize = 1000;

/// Decision made by the sanitizer for a given event.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SanitizeDecision {
    /// Pass the event through unchanged.
    Pass(StreamEvent),
    /// Pass the event with modified attributes (dangerous ones removed).
    PassModified(StreamEvent),
    /// Skip this event entirely (dangerous element or its children).
    Skip,
    /// The event signals a nesting depth violation.
    DepthExceeded,
}

/// Streaming security sanitizer.
///
/// Processes [`StreamEvent`] tokens and filters out dangerous content,
/// maintaining state to track skip/strip regions across multiple events.
pub struct StreamingSanitizer {
    /// Nesting depth of dangerous elements being skipped.
    /// When > 0, all events are suppressed.
    skip_depth: usize,
    /// Stack of element names that entered strip mode (tags removed, content kept).
    /// Using a stack instead of a simple counter ensures mismatched end tags
    /// (e.g., `<iframe>...</form>`) don't corrupt the strip state.
    strip_stack: Vec<String>,
    /// Current HTML element nesting depth.
    nesting_depth: usize,
    /// Maximum allowed nesting depth.
    max_nesting_depth: usize,
}

impl StreamingSanitizer {
    /// Create a new sanitizer with default settings.
    pub fn new() -> Self {
        Self {
            skip_depth: 0,
            strip_stack: Vec::new(),
            nesting_depth: 0,
            max_nesting_depth: MAX_NESTING_DEPTH,
        }
    }

    /// Create a sanitizer with a custom maximum nesting depth.
    pub fn with_max_depth(max_depth: usize) -> Self {
        Self {
            max_nesting_depth: max_depth,
            ..Self::new()
        }
    }

    /// Process a single stream event and return the sanitization decision.
    ///
    /// # Arguments
    ///
    /// * `event` - The stream event to process
    ///
    /// # Returns
    ///
    /// A [`SanitizeDecision`] indicating how the event should be handled.
    pub fn process_event(&mut self, event: StreamEvent) -> SanitizeDecision {
        match &event {
            StreamEvent::StartTag {
                name,
                attrs,
                self_closing,
            } => {
                let tag = name.as_str();

                // If we're inside a dangerous element, track nesting but skip everything.
                //
                // Design decision: `skip_depth` is an O(1) counter, not a stack.
                // Elements nested inside a dangerous element are fully discarded
                // (SanitizeDecision::Skip), so they never reach the emitter and
                // don't need to be tracked by `nesting_depth`. Only `skip_depth`
                // is incremented here so we know when the matching end tag of the
                // outermost dangerous element closes the skip region. This means
                // an attacker cannot bypass the nesting depth limit by deeply
                // nesting inside a `<script>` — the nested content is harmless
                // because it is never processed.
                if self.skip_depth > 0 {
                    if !self_closing {
                        self.skip_depth = self.skip_depth.saturating_add(1);
                    }
                    return SanitizeDecision::Skip;
                }

                // Check nesting depth for elements that pass through to the emitter.
                if !self_closing {
                    self.nesting_depth = self.nesting_depth.saturating_add(1);
                    if self.nesting_depth > self.max_nesting_depth {
                        return SanitizeDecision::DepthExceeded;
                    }
                }

                // Dangerous elements: skip entire subtree
                if DANGEROUS_ELEMENTS.contains(&tag) {
                    if !self_closing {
                        self.skip_depth = 1;
                    }
                    return SanitizeDecision::Skip;
                }

                // Embedded content elements: strip tag, extract URL
                if EMBEDDED_CONTENT_ELEMENTS.contains(&tag) {
                    if !self_closing {
                        // Track which element started strip mode so mismatched
                        // end tags (e.g., <iframe>...</form>) don't corrupt state.
                        self.strip_stack.push(tag.to_string());
                    }
                    // Extract src or data URL for the caller to use
                    let url = attrs
                        .iter()
                        .find(|(k, _)| k == "src" || k == "data")
                        .map(|(_, v)| v.clone());
                    if let Some(url_val) = url
                        && !is_dangerous_url(&url_val)
                    {
                        // Return a text event with the URL as a markdown link placeholder
                        return SanitizeDecision::PassModified(StreamEvent::Text(format!(
                            "[{}]({})",
                            tag, url_val
                        )));
                    }
                    return SanitizeDecision::Skip;
                }

                // Form elements: strip tag, keep content
                if FORM_ELEMENTS.contains(&tag) || VOID_FORM_CONTROLS.contains(&tag) {
                    if !self_closing && !VOID_FORM_CONTROLS.contains(&tag) {
                        self.strip_stack.push(tag.to_string());
                    }
                    return SanitizeDecision::Skip;
                }

                // Clean attributes: remove event handlers and dangerous URLs
                let cleaned_attrs = sanitize_attributes(attrs);
                if cleaned_attrs.len() != attrs.len() {
                    return SanitizeDecision::PassModified(StreamEvent::StartTag {
                        name: name.clone(),
                        attrs: cleaned_attrs,
                        self_closing: *self_closing,
                    });
                }

                SanitizeDecision::Pass(event)
            }

            StreamEvent::EndTag { name } => {
                let tag = name.as_str();

                // Track skip depth. When the outermost dangerous element's
                // end tag is reached (skip_depth drops to 0), we also
                // decrement nesting_depth by 1 to account for the dangerous
                // element itself (which was counted when it was opened).
                if self.skip_depth > 0 {
                    self.skip_depth = self.skip_depth.saturating_sub(1);
                    if self.skip_depth == 0 {
                        self.nesting_depth = self.nesting_depth.saturating_sub(1);
                    }
                    return SanitizeDecision::Skip;
                }

                self.nesting_depth = self.nesting_depth.saturating_sub(1);

                // Strip stack tracking for embedded/form elements.
                // Only pop if the end tag matches the most recent strip entry,
                // preventing mismatched tags from corrupting the strip state.
                if EMBEDDED_CONTENT_ELEMENTS.contains(&tag) || FORM_ELEMENTS.contains(&tag) {
                    if self.strip_stack.last().is_some_and(|t| t == tag) {
                        self.strip_stack.pop();
                    }
                    return SanitizeDecision::Skip;
                }

                SanitizeDecision::Pass(event)
            }

            StreamEvent::Text(_) | StreamEvent::Comment(_) => {
                if self.skip_depth > 0 {
                    return SanitizeDecision::Skip;
                }
                SanitizeDecision::Pass(event)
            }

            StreamEvent::Doctype | StreamEvent::ParseError(_) => SanitizeDecision::Pass(event),
        }
    }

    /// Whether the sanitizer is currently inside a dangerous element.
    pub fn is_skipping(&self) -> bool {
        self.skip_depth > 0
    }

    /// Whether the sanitizer is currently stripping tags.
    pub fn is_stripping(&self) -> bool {
        !self.strip_stack.is_empty()
    }

    /// Current nesting depth.
    pub fn nesting_depth(&self) -> usize {
        self.nesting_depth
    }
}

impl Default for StreamingSanitizer {
    fn default() -> Self {
        Self::new()
    }
}

/// Check if a URL uses a dangerous scheme.
fn is_dangerous_url(url: &str) -> bool {
    let lower = url.trim().to_lowercase();
    DANGEROUS_URL_SCHEMES
        .iter()
        .any(|scheme| lower.starts_with(scheme))
}

/// Remove event handler attributes and dangerous URLs from an attribute list.
fn sanitize_attributes(attrs: &[(String, String)]) -> Vec<(String, String)> {
    attrs
        .iter()
        .filter(|(name, value)| {
            // Remove event handlers (on* prefix, but not bare "on")
            if name.len() > 2 && name.starts_with("on") {
                return false;
            }
            // Remove dangerous URLs from href/src
            if (name == "href" || name == "src") && is_dangerous_url(value) {
                return false;
            }
            true
        })
        .cloned()
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    fn start_tag(name: &str, attrs: Vec<(&str, &str)>) -> StreamEvent {
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

    // --- Dangerous element removal ---

    #[test]
    fn test_script_element_removed() {
        let mut san = StreamingSanitizer::new();
        assert_eq!(
            san.process_event(start_tag("script", vec![])),
            SanitizeDecision::Skip
        );
        assert!(san.is_skipping());
        assert_eq!(
            san.process_event(text("alert('xss')")),
            SanitizeDecision::Skip
        );
        assert_eq!(san.process_event(end_tag("script")), SanitizeDecision::Skip);
        assert!(!san.is_skipping());
    }

    #[test]
    fn test_style_element_removed() {
        let mut san = StreamingSanitizer::new();
        assert_eq!(
            san.process_event(start_tag("style", vec![])),
            SanitizeDecision::Skip
        );
        assert_eq!(
            san.process_event(text("body { color: red }")),
            SanitizeDecision::Skip
        );
        assert_eq!(san.process_event(end_tag("style")), SanitizeDecision::Skip);
    }

    #[test]
    fn test_all_dangerous_elements() {
        for elem in &["script", "style", "noscript", "applet", "link", "base"] {
            let mut san = StreamingSanitizer::new();
            assert_eq!(
                san.process_event(start_tag(elem, vec![])),
                SanitizeDecision::Skip,
                "Expected {} to be skipped",
                elem
            );
        }
    }

    #[test]
    fn test_nested_dangerous_elements() {
        let mut san = StreamingSanitizer::new();
        assert_eq!(
            san.process_event(start_tag("script", vec![])),
            SanitizeDecision::Skip
        );
        // Nested element inside script
        assert_eq!(
            san.process_event(start_tag("div", vec![])),
            SanitizeDecision::Skip
        );
        assert_eq!(san.process_event(text("nested")), SanitizeDecision::Skip);
        assert_eq!(san.process_event(end_tag("div")), SanitizeDecision::Skip);
        assert!(san.is_skipping()); // Still inside script
        assert_eq!(san.process_event(end_tag("script")), SanitizeDecision::Skip);
        assert!(!san.is_skipping());
    }

    // --- Embedded content elements ---

    #[test]
    fn test_iframe_stripped_with_url() {
        let mut san = StreamingSanitizer::new();
        let decision = san.process_event(start_tag("iframe", vec![("src", "https://example.com")]));
        match decision {
            SanitizeDecision::PassModified(StreamEvent::Text(t)) => {
                assert!(t.contains("https://example.com"));
            }
            _ => panic!("Expected PassModified with URL, got {:?}", decision),
        }
        // Child text should pass through (strip mode)
        let child = san.process_event(text("fallback text"));
        assert!(matches!(child, SanitizeDecision::Pass(_)));
        assert_eq!(san.process_event(end_tag("iframe")), SanitizeDecision::Skip);
    }

    #[test]
    fn test_iframe_dangerous_url_blocked() {
        let mut san = StreamingSanitizer::new();
        let decision = san.process_event(start_tag("iframe", vec![("src", "javascript:alert(1)")]));
        assert_eq!(decision, SanitizeDecision::Skip);
    }

    #[test]
    fn test_object_data_url_extracted() {
        let mut san = StreamingSanitizer::new();
        let decision = san.process_event(start_tag(
            "object",
            vec![("data", "https://example.com/doc.pdf")],
        ));
        match decision {
            SanitizeDecision::PassModified(StreamEvent::Text(t)) => {
                assert!(t.contains("https://example.com/doc.pdf"));
            }
            _ => panic!("Expected PassModified with URL, got {:?}", decision),
        }
    }

    // --- Form elements ---

    #[test]
    fn test_form_elements_stripped() {
        for elem in &[
            "form", "button", "select", "textarea", "fieldset", "label", "option",
        ] {
            let mut san = StreamingSanitizer::new();
            assert_eq!(
                san.process_event(start_tag(elem, vec![])),
                SanitizeDecision::Skip,
                "Expected {} tag to be stripped",
                elem
            );
            // Child text should pass through
            let child = san.process_event(text("form text"));
            assert!(
                matches!(child, SanitizeDecision::Pass(_)),
                "Expected child text of {} to pass",
                elem
            );
        }
    }

    // --- Event handler attributes ---

    #[test]
    fn test_event_handler_removed() {
        let mut san = StreamingSanitizer::new();
        let decision = san.process_event(start_tag(
            "div",
            vec![("onclick", "alert(1)"), ("class", "safe")],
        ));
        match decision {
            SanitizeDecision::PassModified(StreamEvent::StartTag { attrs, .. }) => {
                assert!(
                    !attrs.iter().any(|(k, _)| k == "onclick"),
                    "onclick should be removed"
                );
                assert!(
                    attrs.iter().any(|(k, _)| k == "class"),
                    "class should be kept"
                );
            }
            _ => panic!("Expected PassModified, got {:?}", decision),
        }
    }

    #[test]
    fn test_bare_on_not_removed() {
        let mut san = StreamingSanitizer::new();
        let decision = san.process_event(start_tag("div", vec![("on", "value")]));
        // "on" (2 chars) should NOT be treated as event handler
        assert!(matches!(decision, SanitizeDecision::Pass(_)));
    }

    // --- Dangerous URL schemes ---

    #[test]
    fn test_dangerous_url_in_href_removed() {
        let mut san = StreamingSanitizer::new();
        let decision = san.process_event(start_tag("a", vec![("href", "javascript:alert(1)")]));
        match decision {
            SanitizeDecision::PassModified(StreamEvent::StartTag { attrs, .. }) => {
                assert!(
                    !attrs.iter().any(|(k, _)| k == "href"),
                    "dangerous href should be removed"
                );
            }
            _ => panic!("Expected PassModified, got {:?}", decision),
        }
    }

    #[test]
    fn test_safe_url_preserved() {
        let mut san = StreamingSanitizer::new();
        let decision = san.process_event(start_tag("a", vec![("href", "https://example.com")]));
        assert!(matches!(decision, SanitizeDecision::Pass(_)));
    }

    #[test]
    fn test_all_dangerous_schemes() {
        for scheme in &["javascript:", "data:", "vbscript:", "file:", "about:"] {
            let url = format!("{}test", scheme);
            assert!(
                is_dangerous_url(&url),
                "Expected {} to be dangerous",
                scheme
            );
        }
    }

    #[test]
    fn test_dangerous_url_case_insensitive() {
        assert!(is_dangerous_url("JavaScript:alert(1)"));
        assert!(is_dangerous_url("JAVASCRIPT:alert(1)"));
    }

    // --- Nesting depth ---

    #[test]
    fn test_nesting_depth_exceeded() {
        let mut san = StreamingSanitizer::with_max_depth(3);
        assert!(matches!(
            san.process_event(start_tag("div", vec![])),
            SanitizeDecision::Pass(_)
        ));
        assert!(matches!(
            san.process_event(start_tag("div", vec![])),
            SanitizeDecision::Pass(_)
        ));
        assert!(matches!(
            san.process_event(start_tag("div", vec![])),
            SanitizeDecision::Pass(_)
        ));
        // 4th level should exceed depth 3
        assert_eq!(
            san.process_event(start_tag("div", vec![])),
            SanitizeDecision::DepthExceeded
        );
    }

    // --- Normal elements pass through ---

    #[test]
    fn test_safe_elements_pass() {
        let mut san = StreamingSanitizer::new();
        assert!(matches!(
            san.process_event(start_tag("p", vec![])),
            SanitizeDecision::Pass(_)
        ));
        assert!(matches!(
            san.process_event(text("hello")),
            SanitizeDecision::Pass(_)
        ));
        assert!(matches!(
            san.process_event(end_tag("p")),
            SanitizeDecision::Pass(_)
        ));
    }

    // --- Mismatched tag resilience ---

    /// Verify that deep nesting inside a dangerous element does not affect
    /// `nesting_depth` and cannot bypass the depth limit. This documents
    /// the design decision explained in the `process_event` comments:
    /// content inside dangerous elements is fully discarded, so its
    /// nesting depth is irrelevant to security.
    #[test]
    fn test_deep_nesting_inside_dangerous_element_is_safe() {
        let mut san = StreamingSanitizer::with_max_depth(5);

        // Open a script element (nesting_depth goes to 1)
        assert_eq!(
            san.process_event(start_tag("script", vec![])),
            SanitizeDecision::Skip
        );
        assert!(san.is_skipping());
        assert_eq!(san.nesting_depth(), 1);

        // Nest 100 elements inside the script — all skipped, nesting_depth
        // stays at 1 because these elements never reach the emitter.
        for _ in 0..100 {
            assert_eq!(
                san.process_event(start_tag("div", vec![])),
                SanitizeDecision::Skip
            );
        }
        // nesting_depth should still be 1 (only the script itself)
        assert_eq!(
            san.nesting_depth(),
            1,
            "nesting_depth should not increase for elements inside a dangerous element"
        );

        // Close all 100 inner elements
        for _ in 0..100 {
            assert_eq!(san.process_event(end_tag("div")), SanitizeDecision::Skip);
        }
        assert!(san.is_skipping(), "still inside script");

        // Close the script
        assert_eq!(san.process_event(end_tag("script")), SanitizeDecision::Skip);
        assert!(!san.is_skipping());
        assert_eq!(san.nesting_depth(), 0);

        // After exiting the dangerous element, the depth limit still works
        // normally for subsequent elements.
        for _ in 0..5 {
            assert!(matches!(
                san.process_event(start_tag("div", vec![])),
                SanitizeDecision::Pass(_)
            ));
        }
        // 6th level should exceed depth 5
        assert_eq!(
            san.process_event(start_tag("div", vec![])),
            SanitizeDecision::DepthExceeded
        );
    }

    #[test]
    fn test_mismatched_end_tag_does_not_corrupt_strip_state() {
        // Regression: <iframe>...</form> should not decrement the strip
        // stack because </form> doesn't match the <iframe> that started it.
        let mut san = StreamingSanitizer::new();
        let decision = san.process_event(start_tag("iframe", vec![("src", "https://example.com")]));
        // iframe enters strip mode
        assert!(
            matches!(decision, SanitizeDecision::PassModified(_)),
            "iframe should be stripped with URL"
        );
        assert!(san.is_stripping());

        // Mismatched </form> should NOT exit strip mode
        assert_eq!(san.process_event(end_tag("form")), SanitizeDecision::Skip);
        assert!(
            san.is_stripping(),
            "mismatched </form> must not exit iframe strip mode"
        );

        // Correct </iframe> should exit strip mode
        assert_eq!(san.process_event(end_tag("iframe")), SanitizeDecision::Skip);
        assert!(
            !san.is_stripping(),
            "matching </iframe> should exit strip mode"
        );
    }

    // --- Property-based tests ---
    // Feature: rust-streaming-engine-core, Property 4: Streaming Sanitizer Security Equivalence

    fn arb_dangerous_element() -> impl Strategy<Value = String> {
        prop::sample::select(vec![
            "script".to_string(),
            "style".to_string(),
            "noscript".to_string(),
            "applet".to_string(),
            "link".to_string(),
            "base".to_string(),
        ])
    }

    fn arb_safe_element() -> impl Strategy<Value = String> {
        prop::sample::select(vec![
            "div".to_string(),
            "p".to_string(),
            "span".to_string(),
            "h1".to_string(),
            "a".to_string(),
            "ul".to_string(),
        ])
    }

    proptest! {
        /// **Validates: Requirements 3.1, 3.6**
        ///
        /// All dangerous elements must be skipped regardless of which one is used.
        #[test]
        fn prop_dangerous_elements_always_skipped(elem in arb_dangerous_element()) {
            let mut san = StreamingSanitizer::new();
            let decision = san.process_event(StreamEvent::StartTag {
                name: elem.clone(),
                attrs: vec![],
                self_closing: false,
            });
            prop_assert_eq!(decision, SanitizeDecision::Skip,
                "Dangerous element '{}' should be skipped", elem);
            prop_assert!(san.is_skipping());
        }

        /// **Validates: Requirements 3.1**
        ///
        /// Safe elements must always pass through the sanitizer.
        #[test]
        fn prop_safe_elements_always_pass(elem in arb_safe_element()) {
            let mut san = StreamingSanitizer::new();
            let decision = san.process_event(StreamEvent::StartTag {
                name: elem.clone(),
                attrs: vec![],
                self_closing: false,
            });
            prop_assert!(matches!(decision, SanitizeDecision::Pass(_)),
                "Safe element '{}' should pass", elem);
        }

        /// **Validates: Requirements 3.2**
        ///
        /// Event handler attributes (on* prefix) must always be removed.
        #[test]
        fn prop_event_handlers_always_removed(
            handler in prop::sample::select(vec![
                "onclick".to_string(), "onload".to_string(), "onerror".to_string(),
                "onmouseover".to_string(), "onfocus".to_string(),
            ])
        ) {
            let mut san = StreamingSanitizer::new();
            let decision = san.process_event(StreamEvent::StartTag {
                name: "div".to_string(),
                attrs: vec![(handler.clone(), "alert(1)".to_string())],
                self_closing: false,
            });
            match decision {
                SanitizeDecision::PassModified(StreamEvent::StartTag { attrs, .. }) => {
                    prop_assert!(!attrs.iter().any(|(k, _)| k == &handler),
                        "Event handler '{}' should be removed", handler);
                }
                _ => prop_assert!(false, "Expected PassModified for event handler removal"),
            }
        }

        /// **Validates: Requirements 3.3**
        ///
        /// Dangerous URL schemes must always be detected as dangerous.
        #[test]
        fn prop_dangerous_urls_always_blocked(
            scheme in prop::sample::select(vec![
                "javascript:".to_string(), "data:".to_string(), "vbscript:".to_string(),
                "file:".to_string(), "about:".to_string(),
            ]),
            suffix in "[a-z]{1,20}",
        ) {
            let url = format!("{}{}", scheme, suffix);
            prop_assert!(is_dangerous_url(&url), "URL '{}' should be dangerous", url);
        }
    }
}
