//! Security validation and sanitization for HTML input
//!
//! This module implements comprehensive security measures to prevent:
//! - XSS (Cross-Site Scripting) attacks
//! - XXE (XML External Entity) attacks
//! - SSRF (Server-Side Request Forgery) attacks
//! - Code injection through event handlers (prefix-based `on*` detection per OWASP/DOMPurify)
//!
//! # Threat Model
//!
//! The primary threat is **untrusted HTML input** from upstream servers.
//! This HTML may contain:
//! - Malicious scripts (`<script>` tags)
//! - Event handlers (onclick, onload, etc.)
//! - External entity references (XXE)
//! - External resource URLs (SSRF)
//! - JavaScript URLs (javascript:)
//! - Data URLs with executable content
//!
//! # Defense Layers
//!
//! 1. **Input Validation**: Validate HTML structure and size before processing
//! 2. **Element Sanitization**: Remove dangerous elements (script, style, etc.);
//!    strip safe container tags while applying the form-control privacy policy
//!    and preserving safe embedded-content fallback text
//! 3. **Attribute Sanitization**: Remove event handlers (`on*` prefix match) and dangerous attributes
//! 4. **URL Sanitization**: Block javascript:, data:, and external URLs
//! 5. **Entity Safety**: html5ever prevents XXE by default (no external entity resolution)
//!
//! # Requirements
//!
//! Validates: NFR-03.1, NFR-03.2, NFR-03.3, NFR-03.4

use html5ever::Attribute;
use std::borrow::Cow;
use std::cell::Ref;

/// Maximum allowed nesting depth for HTML elements
/// Prevents stack overflow from deeply nested structures
const MAX_NESTING_DEPTH: usize = 1000;

/// Dangerous HTML elements that should be removed entirely
const DANGEROUS_ELEMENTS: &[&str] = &[
    "script",   // JavaScript execution
    "style",    // CSS injection (can contain expressions)
    "noscript", // Alternative content, usually redundant with main content
    "applet",   // Legacy Java applets — extinct, no modern usage
    "link",     // Can load external stylesheets with expressions
    "base",     // Can change base URL for all relative URLs
];

/// Embedded content elements whose tags are stripped but whose fallback child
/// content is preserved. The `src` / `data` attribute is extracted as a
/// Markdown link so AI agents know what was embedded.
const EMBEDDED_CONTENT_ELEMENTS: &[&str] = &[
    "iframe", // Fallback text between tags can be meaningful
    "object", // Fallback content (e.g. download links, descriptions) is often useful
    "embed",  // Void element — no children, but src URL is valuable context
];

/// Form-related elements whose tags are stripped before Markdown conversion.
/// Labels, button captions, option labels, and visible output text remain useful
/// page content, while control values are handled by the shared input policy.
const FORM_ELEMENTS: &[&str] = &[
    "form",     // Container — children often hold descriptive text
    "button",   // Caption text is useful context
    "select",   // Contains <option> text
    "textarea", // Placeholder/label may be useful; default text is user data
    "fieldset", // Groups related controls with a <legend>
    "legend",   // Label for a <fieldset>
    "label",    // Descriptive text for a control
    "option",   // Display label remains; the value attribute does not
    "optgroup", // Group label for options
    "datalist", // Display labels remain; suggestion values do not
    "output",   // Visible calculation result text remains
];

/// Normalize an input type for the shared value-privacy policy.
///
/// HTML input types are ASCII case-insensitive. Whitespace is intentionally
/// preserved: `type=" button "` is not the exact `button` type and therefore
/// must not receive the button value fallback.
pub(crate) fn normalize_input_type(raw_type: Option<&str>) -> String {
    raw_type.unwrap_or("text").to_ascii_lowercase()
}

/// Return whether an exact normalized input type may use its `value` as a
/// description fallback.
///
/// Only `button` is allowed. All other input types treat `value` as submitted
/// or prefilled user data rather than page-visible descriptive text.
pub(crate) fn input_type_allows_value_fallback(normalized_type: &str) -> bool {
    normalized_type == "button"
}

/// Return whether an exact normalized input type must emit no description.
///
/// Hidden, image, and password controls are fully suppressed. In particular,
/// password controls do not expose even their accessible label or placeholder.
pub(crate) fn input_type_is_suppressed(normalized_type: &str) -> bool {
    matches!(normalized_type, "hidden" | "image" | "password")
}

/// Select descriptive text for an input-like control using the shared privacy
/// policy.
///
/// Empty or whitespace-only attributes do not block the next fallback. Submit
/// and reset controls use `value` as their button caption; only an exact
/// `button` type may otherwise use `value`. The iterator owns no data, so the
/// returned slice remains borrowed from the caller's attribute storage.
pub(crate) fn select_input_control_text<'a, I>(normalized_type: &str, attrs: I) -> Option<&'a str>
where
    I: IntoIterator<Item = (&'a str, &'a str)>,
{
    if input_type_is_suppressed(normalized_type) {
        return None;
    }

    let mut aria_label = None;
    let mut placeholder = None;
    let mut value = None;

    for (name, attr_value) in attrs {
        if attr_value.trim().is_empty() {
            continue;
        }

        match name {
            "aria-label" if aria_label.is_none() => aria_label = Some(attr_value),
            "placeholder" if placeholder.is_none() => placeholder = Some(attr_value),
            "value" if value.is_none() => value = Some(attr_value),
            _ => {}
        }
    }

    if matches!(normalized_type, "submit" | "reset") {
        return value;
    }

    aria_label.or(placeholder).or_else(|| {
        input_type_allows_value_fallback(normalized_type)
            .then_some(value)
            .flatten()
    })
}

/// HTML attributes whose values can navigate to or load a URL.
///
/// Keep this list shared by both attribute decision paths. Checking only
/// `href` and `src` leaves less common but still active attributes such as
/// `formaction`, `ping`, and `poster` outside the URL policy.
pub(crate) const URL_ATTRIBUTES: &[&str] = &[
    "href",
    "src",
    "action",
    "formaction",
    "longdesc",
    "dynsrc",
    "lowsrc",
    "manifest",
    "poster",
    "cite",
    "ping",
    "data",
    "codebase",
];

/// Known event handler attributes (reference list for documentation/auditing).
/// Detection uses prefix matching (`on*`) instead of this list — see
/// `SecurityValidator::is_event_handler()`.
#[allow(dead_code)]
const EVENT_HANDLER_ATTRIBUTES: &[&str] = &[
    "onclick",
    "ondblclick",
    "onmousedown",
    "onmouseup",
    "onmouseover",
    "onmousemove",
    "onmouseout",
    "onmouseenter",
    "onmouseleave",
    "onkeydown",
    "onkeypress",
    "onkeyup",
    "onload",
    "onunload",
    "onabort",
    "onerror",
    "onresize",
    "onscroll",
    "onselect",
    "onchange",
    "onsubmit",
    "onreset",
    "onfocus",
    "onblur",
    "oninput",
    "oncontextmenu",
    "ondrag",
    "ondragend",
    "ondragenter",
    "ondragleave",
    "ondragover",
    "ondragstart",
    "ondrop",
    "onwheel",
    "oncopy",
    "oncut",
    "onpaste",
    "onanimationstart",
    "onanimationend",
    "onanimationiteration",
    "ontransitionend",
];

/// Dangerous URL schemes that should be blocked
const DANGEROUS_URL_SCHEMES: &[&str] = &[
    "javascript:", // JavaScript execution
    "data:",       // Can contain executable content
    "vbscript:",   // VBScript execution (legacy IE)
    "file:",       // Local file access (SSRF)
    "about:",      // Browser internal URLs
];

/// Escape untrusted text before emitting it as ordinary Markdown content.
///
/// Ordinary text is not a Markdown label, so it can contain syntax that
/// changes the document structure or introduces raw HTML. Escape inline
/// delimiters and link/HTML brackets unconditionally. Block markers are
/// escaped only at the beginning of a line so prose such as `a-b` remains
/// readable while `- item`, `# heading`, and `1. item` stay text.
///
/// Stateful context used while escaping ordinary Markdown text fragments.
///
/// Streaming tokenizers can split one logical text node across arbitrary
/// chunks. Keeping the line-prefix state across those fragments prevents a
/// punctuation character in the middle of a source line from being treated
/// as a block marker merely because it arrived in a new chunk.  The same
/// state protects line-leading GFM table bars and setext underline markers.
#[derive(Clone, Copy)]
pub(crate) struct MarkdownTextEscapeState {
    line_prefix: bool,
    indent: usize,
    ordered_digits: bool,
}

impl Default for MarkdownTextEscapeState {
    fn default() -> Self {
        Self {
            line_prefix: true,
            indent: 0,
            ordered_digits: false,
        }
    }
}

impl MarkdownTextEscapeState {
    pub(crate) fn advance(&mut self, ch: char) {
        if ch == '\n' {
            self.line_prefix = true;
            self.indent = 0;
            self.ordered_digits = false;
        } else if self.line_prefix && ch == ' ' && self.indent < 4 {
            self.indent += 1;
        } else if self.line_prefix && ch.is_ascii_digit() {
            self.ordered_digits = true;
        } else {
            self.line_prefix = false;
            self.ordered_digits = false;
        }
    }
}

/// Escape ordinary Markdown text while preserving state across fragments.
pub(crate) fn escape_markdown_text_with_state(
    s: &str,
    state: &mut MarkdownTextEscapeState,
) -> String {
    let mut out = String::with_capacity(s.len().saturating_add(8));

    for ch in s.chars() {
        if requires_escape(ch, state) {
            out.push('\\');
        }
        out.push(ch);
        state.advance(ch);
    }

    out
}

/// Action to take when sanitizing an element
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SanitizeAction {
    /// Allow the element as-is
    Allow,
    /// Remove the element and all its children
    Remove,
    /// Strip the element tag while applying the element-specific content policy.
    /// Labels, option labels, and visible output text remain eligible for
    /// extraction; privacy-sensitive control defaults do not.
    StripElement,
    /// Strip dangerous attributes but keep the element
    StripAttributes,
    /// Strip dangerous URL from href/src attribute
    StripUrl,
}

/// Security validator for HTML input
///
/// Provides methods to validate and sanitize HTML content before conversion.
pub struct SecurityValidator {
    /// Maximum allowed nesting depth
    max_depth: usize,
}

impl SecurityValidator {
    /// Create a new security validator with default settings
    pub fn new() -> Self {
        Self {
            max_depth: MAX_NESTING_DEPTH,
        }
    }

    /// Create a security validator with custom maximum depth
    pub fn with_max_depth(max_depth: usize) -> Self {
        Self { max_depth }
    }

    /// Check if an element should be sanitized
    ///
    /// # Arguments
    ///
    /// * `tag_name` - The HTML tag name (e.g., "script", "div")
    ///
    /// # Returns
    ///
    /// Returns the appropriate `SanitizeAction` for the element.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::security::{SecurityValidator, SanitizeAction};
    ///
    /// let validator = SecurityValidator::new();
    /// assert_eq!(validator.check_element("script"), SanitizeAction::Remove);
    /// assert_eq!(validator.check_element("div"), SanitizeAction::Allow);
    /// ```
    pub fn check_element(&self, tag_name: &str) -> SanitizeAction {
        if DANGEROUS_ELEMENTS.contains(&tag_name) {
            SanitizeAction::Remove
        } else if FORM_ELEMENTS.contains(&tag_name) || EMBEDDED_CONTENT_ELEMENTS.contains(&tag_name)
        {
            SanitizeAction::StripElement
        } else {
            SanitizeAction::Allow
        }
    }

    /// Check if an element is a void form control whose descriptive text
    /// lives in attributes rather than child nodes (e.g., `<input>`).
    ///
    /// Returns `true` for elements where `extract_form_control_text()` should
    /// be called instead of traversing children.
    pub fn is_void_form_control(&self, tag_name: &str) -> bool {
        tag_name == "input"
    }

    /// Check if an element is an embedded content element (`<iframe>`, `<object>`)
    /// whose `src`/`data` attribute should be extracted as a Markdown link
    /// alongside any fallback child text.
    pub fn is_embedded_content(&self, tag_name: &str) -> bool {
        EMBEDDED_CONTENT_ELEMENTS.contains(&tag_name)
    }

    /// Check if an attribute is a dangerous event handler
    ///
    /// # Arguments
    ///
    /// * `attr_name` - The attribute name (e.g., "onclick", "href")
    ///
    /// # Returns
    ///
    /// Returns `true` if the attribute is an event handler that should be removed.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::security::SecurityValidator;
    ///
    /// let validator = SecurityValidator::new();
    /// assert!(validator.is_event_handler("onclick"));
    /// assert!(validator.is_event_handler("onload"));
    /// assert!(!validator.is_event_handler("href"));
    /// ```
    pub fn is_event_handler(&self, attr_name: &str) -> bool {
        // The frozen safety contract treats every non-empty `on*` attribute as
        // an event handler. Keep the length guard so the bare `on` token is not
        // stripped accidentally; the prefix rule catches future event names
        // without maintaining an incomplete allowlist.
        attr_name.starts_with("on") && attr_name.len() > 2
    }

    /// Check if a URL uses a dangerous scheme
    ///
    /// # Arguments
    ///
    /// * `url` - The URL to check
    ///
    /// # Returns
    ///
    /// Returns `true` if the URL uses a dangerous scheme (javascript:, data:, etc.)
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::security::SecurityValidator;
    ///
    /// let validator = SecurityValidator::new();
    /// assert!(validator.is_dangerous_url("javascript:alert('xss')"));
    /// assert!(validator.is_dangerous_url("data:text/html,<script>alert('xss')</script>"));
    /// assert!(!validator.is_dangerous_url("https://example.com"));
    /// assert!(!validator.is_dangerous_url("/relative/path"));
    /// ```
    pub fn is_dangerous_url(&self, url: &str) -> bool {
        is_dangerous_url_value(url)
    }

    #[cfg(test)]
    fn contains_percent_encoded_control(url: &str) -> bool {
        contains_percent_encoded_control(url)
    }

    /// Check if attributes contain event handlers or dangerous URLs
    ///
    /// # Arguments
    ///
    /// * `attrs` - Reference to the element's attributes
    ///
    /// # Returns
    ///
    /// Returns the appropriate `SanitizeAction` based on attribute analysis.
    pub fn check_attributes(&self, attrs: &Ref<Vec<Attribute>>) -> SanitizeAction {
        for attr in attrs.iter() {
            let attr_name = attr.name.local.as_ref();

            // Check for event handlers
            if self.is_event_handler(attr_name) {
                return SanitizeAction::StripAttributes;
            }

            // Inline CSS can contain script/navigation vectors and obfuscation.
            if attr_name == "style" {
                return SanitizeAction::StripAttributes;
            }

            // Check every URL-bearing attribute for dangerous schemes.
            if URL_ATTRIBUTES.contains(&attr_name) && self.is_dangerous_url(&attr.value) {
                return SanitizeAction::StripUrl;
            }
        }

        SanitizeAction::Allow
    }

    /// Validate nesting depth to prevent stack overflow
    ///
    /// # Arguments
    ///
    /// * `depth` - Current nesting depth
    ///
    /// # Returns
    ///
    /// Returns `Ok(())` if depth is acceptable, `Err` if too deep.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::security::SecurityValidator;
    ///
    /// let validator = SecurityValidator::with_max_depth(100);
    /// assert!(validator.validate_depth(50).is_ok());
    /// assert!(validator.validate_depth(150).is_err());
    /// ```
    pub fn validate_depth(&self, depth: usize) -> Result<(), String> {
        if depth > self.max_depth {
            Err(format!(
                "HTML nesting depth {} exceeds maximum allowed depth {}",
                depth, self.max_depth
            ))
        } else {
            Ok(())
        }
    }

    /// Sanitize a URL by removing dangerous schemes
    ///
    /// # Arguments
    ///
    /// * `url` - The URL to sanitize
    ///
    /// # Returns
    ///
    /// Returns `None` if the URL is dangerous. Safe URLs are returned in their
    /// canonical, outer-whitespace-trimmed form, including an empty string
    /// when the caller supplied an empty destination. Control characters are
    /// rejected before trimming so an attacker cannot hide one at either edge
    /// of the value.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::security::SecurityValidator;
    ///
    /// let validator = SecurityValidator::new();
    /// assert_eq!(validator.sanitize_url("javascript:alert('xss')"), None);
    /// assert_eq!(validator.sanitize_url("https://example.com"), Some("https://example.com"));
    /// ```
    pub fn sanitize_url<'a>(&self, url: &'a str) -> Option<&'a str> {
        sanitize_url_value(url)
    }

    /// Get a list of attributes to remove from an element
    ///
    /// # Arguments
    ///
    /// * `attrs` - Reference to the element's attributes
    ///
    /// # Returns
    ///
    /// Returns a vector of attribute names that should be removed.
    pub fn get_attributes_to_remove(&self, attrs: &Ref<Vec<Attribute>>) -> Vec<String> {
        let mut to_remove = Vec::new();

        for attr in attrs.iter() {
            let attr_name = attr.name.local.as_ref();

            // Remove event handlers
            if self.is_event_handler(attr_name) {
                to_remove.push(attr_name.to_string());
            }

            // Remove inline style attributes for defense-in-depth.
            if attr_name == "style" {
                to_remove.push(attr_name.to_string());
            }

            // Remove dangerous URLs from every URL-bearing attribute.
            if URL_ATTRIBUTES.contains(&attr_name) && self.is_dangerous_url(&attr.value) {
                to_remove.push(attr_name.to_string());
            }
        }

        to_remove
    }
}

impl Default for SecurityValidator {
    /// Build a validator with secure default limits and policy.
    fn default() -> Self {
        Self::new()
    }
}

/// Check if html5ever prevents XXE attacks
///
/// html5ever is an HTML5 parser, not an XML parser. HTML5 does not support
/// external entity references, so XXE attacks are not possible by design.
///
/// This function documents this security property for auditing purposes.
///
/// # XXE Prevention
///
/// The html5ever parser:
/// - Does NOT resolve external entities (HTML5 spec doesn't support them)
/// - Does NOT process DOCTYPE declarations for entity definitions
/// - Does NOT load external DTDs
/// - Treats entity references as text content, not executable directives
///
/// # Requirements
///
/// Validates: NFR-03.4 (Prevent XXE attacks)
pub fn xxe_prevention_documentation() -> &'static str {
    "html5ever is an HTML5 parser that does not support XML external entities. \
     HTML5 does not have a concept of external entities, so XXE attacks are \
     prevented by design. DOCTYPE declarations are parsed but not processed \
     for entity definitions."
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    #[test]
    fn test_dangerous_elements() {
        let validator = SecurityValidator::new();

        // Dangerous elements should be removed
        assert_eq!(validator.check_element("script"), SanitizeAction::Remove);
        assert_eq!(validator.check_element("style"), SanitizeAction::Remove);

        // Embedded content elements: tags stripped, fallback text + src link preserved
        assert_eq!(
            validator.check_element("iframe"),
            SanitizeAction::StripElement
        );
        assert_eq!(
            validator.check_element("object"),
            SanitizeAction::StripElement
        );
        assert_eq!(
            validator.check_element("embed"),
            SanitizeAction::StripElement
        );
        assert!(validator.is_embedded_content("iframe"));
        assert!(validator.is_embedded_content("object"));
        assert!(validator.is_embedded_content("embed"));
        assert!(!validator.is_embedded_content("div"));

        // Form elements should be stripped (tag removed, policy decides content)
        assert_eq!(
            validator.check_element("form"),
            SanitizeAction::StripElement
        );
        assert_eq!(
            validator.check_element("button"),
            SanitizeAction::StripElement
        );
        assert_eq!(
            validator.check_element("select"),
            SanitizeAction::StripElement
        );
        assert_eq!(
            validator.check_element("textarea"),
            SanitizeAction::StripElement
        );
        assert_eq!(
            validator.check_element("fieldset"),
            SanitizeAction::StripElement
        );
        assert_eq!(
            validator.check_element("label"),
            SanitizeAction::StripElement
        );
        assert_eq!(
            validator.check_element("option"),
            SanitizeAction::StripElement
        );

        // Void form controls are detected separately
        assert!(validator.is_void_form_control("input"));
        assert!(!validator.is_void_form_control("button"));

        // Safe elements should be allowed
        assert_eq!(validator.check_element("div"), SanitizeAction::Allow);
        assert_eq!(validator.check_element("p"), SanitizeAction::Allow);
        assert_eq!(validator.check_element("a"), SanitizeAction::Allow);
    }

    #[test]
    fn test_event_handlers() {
        let validator = SecurityValidator::new();

        // Classic event handlers should be detected
        assert!(validator.is_event_handler("onclick"));
        assert!(validator.is_event_handler("onload"));
        assert!(validator.is_event_handler("onerror"));
        assert!(validator.is_event_handler("onmouseover"));

        // Previously-missed handlers now caught by prefix matching
        assert!(validator.is_event_handler("onpointerdown"));
        assert!(validator.is_event_handler("onpointerup"));
        assert!(validator.is_event_handler("ontouchstart"));
        assert!(validator.is_event_handler("ontouchend"));
        assert!(validator.is_event_handler("ongotpointercapture"));
        assert!(validator.is_event_handler("onlostpointercapture"));
        assert!(validator.is_event_handler("onbeforeinput"));
        assert!(validator.is_event_handler("onformdata"));
        assert!(validator.is_event_handler("onsecuritypolicyviolation"));
        assert!(validator.is_event_handler("onslotchange"));

        // Future/unknown event handlers are also caught
        assert!(validator.is_event_handler("onfutureevent"));

        // Normal attributes should not be detected as event handlers
        assert!(!validator.is_event_handler("href"));
        assert!(!validator.is_event_handler("src"));
        assert!(!validator.is_event_handler("class"));

        // Edge case: bare "on" is not an event handler
        assert!(!validator.is_event_handler("on"));
    }

    #[test]
    fn test_dangerous_urls() {
        let validator = SecurityValidator::new();

        // Dangerous URL schemes
        assert!(validator.is_dangerous_url("javascript:alert('xss')"));
        assert!(validator.is_dangerous_url("JavaScript:alert('xss')")); // Case insensitive
        assert!(validator.is_dangerous_url("data:text/html,<script>alert('xss')</script>"));
        assert!(validator.is_dangerous_url("vbscript:msgbox('xss')"));
        assert!(validator.is_dangerous_url("file:///etc/passwd"));
        assert!(validator.is_dangerous_url("javascript:\u{0000}alert('xss')"));
        assert!(validator.is_dangerous_url("java\u{0009}script:alert('xss')"));

        // Safe URLs
        assert!(!validator.is_dangerous_url("https://example.com"));
        assert!(!validator.is_dangerous_url("http://example.com"));
        assert!(!validator.is_dangerous_url("/relative/path"));
        assert!(!validator.is_dangerous_url("../parent/path"));
        assert!(!validator.is_dangerous_url("#anchor"));
    }

    #[test]
    fn test_percent_encoded_control_characters_are_dangerous() {
        let validator = SecurityValidator::new();

        for url in [
            "https://example.com/%00",
            "https://example.com/%7F",
            "https://example.com/%0a",
            "https://example.com/%0A",
            "https://example.com/%7f",
            "%00",
            "a%00",
            "a%7Fb",
        ] {
            assert!(
                validator.is_dangerous_url(url),
                "percent-encoded control should be rejected: {url}"
            );
            assert!(
                SecurityValidator::contains_percent_encoded_control(url),
                "helper should detect percent-encoded control: {url}"
            );
        }
    }

    #[test]
    fn test_malformed_percent_triplets_are_not_control_characters() {
        let validator = SecurityValidator::new();

        for url in [
            "https://example.com/%0",
            "https://example.com/%xG",
            "https://example.com/%G0",
            "https://example.com/%20",
            "https://example.com/abc%",
            "https://example.com/abc%4",
            "https://example.com/abc%41",
            "%",
        ] {
            assert!(
                !validator.is_dangerous_url(url),
                "non-control or malformed triplet should not be dangerous: {url}"
            );
            assert!(
                !SecurityValidator::contains_percent_encoded_control(url),
                "helper should ignore non-control or malformed triplet: {url}"
            );
        }
    }

    #[test]
    fn test_depth_validation() {
        let validator = SecurityValidator::with_max_depth(100);

        assert!(validator.validate_depth(50).is_ok());
        assert!(validator.validate_depth(100).is_ok());
        assert!(validator.validate_depth(101).is_err());
        assert!(validator.validate_depth(1000).is_err());
    }

    #[test]
    fn test_sanitize_url() {
        let validator = SecurityValidator::new();

        // Dangerous URLs should return None
        assert_eq!(validator.sanitize_url("javascript:alert('xss')"), None);
        assert_eq!(validator.sanitize_url("data:text/html,<script>"), None);

        // Safe URLs should be returned as-is
        assert_eq!(
            validator.sanitize_url("https://example.com"),
            Some("https://example.com")
        );
        assert_eq!(validator.sanitize_url("/path"), Some("/path"));
    }

    #[test]
    fn sanitize_url_trims_safe_outer_whitespace_but_rejects_controls() {
        let validator = SecurityValidator::new();

        assert_eq!(
            validator.sanitize_url("  https://example.com/path  "),
            Some("https://example.com/path")
        );
        for url in [
            "\thttps://example.com/path",
            "https://example.com/pa\th",
            "https://example.com/path\nnext",
            "https://example.com/path\rnext",
        ] {
            assert_eq!(validator.sanitize_url(url), None, "control URL: {url:?}");
        }
    }

    #[test]
    fn markdown_destination_escaping_is_shared_and_capacity_exact() {
        let url = r"https://example.com/a\\b<c>d (x)";
        let escaped = escape_markdown_destination(url);

        assert_eq!(escaped.as_ref(), r"<https://example.com/a\\\\b\<c\>d (x)>");
        assert_eq!(
            markdown_destination_escaped_capacity(url),
            Some(escaped.len())
        );
        assert_eq!(markdown_destination_escaped_capacity("safe"), Some(0));
    }

    #[test]
    fn test_xxe_prevention_documentation() {
        let doc = xxe_prevention_documentation();
        assert!(doc.contains("html5ever"));
        assert!(doc.contains("XXE"));
        assert!(doc.contains("external entities"));
    }

    proptest! {
       /// Property 30: Input Validation (dangerous URL schemes are rejected)
       /// Validates: NFR-03.4
       #[test]
       fn prop_dangerous_url_schemes_are_rejected(
           leading_ws in "[ \\t\\n\\r]{0,3}",
           payload in "[A-Za-z0-9_/?=&:%#.-]{0,64}",
           uppercase in any::<bool>(),
       ) {
           let validator = SecurityValidator::new();
           let schemes = ["javascript:", "data:", "vbscript:", "file:", "about:"];

           for scheme in schemes {
               let scheme_variant = if uppercase {
                   scheme.to_uppercase()
               } else {
                   scheme.to_string()
               };
               let candidate = format!("{leading_ws}{scheme_variant}{payload}");

               prop_assert!(
                   validator.is_dangerous_url(&candidate),
                   "Dangerous scheme should be detected regardless of case/leading whitespace: {candidate}"
               );
                prop_assert_eq!(
                    validator.sanitize_url(&candidate),
                    None,
                    "Dangerous scheme should be removed by sanitize_url"
                );
            }
        }
    }
}

/// Reject URLs containing C0 control characters (U+0000–U+001F)
/// except HT (U+0009), LF (U+000A), CR (U+000D) which are
/// permitted in HTTP header values per RFC 7230 §3.2.
///
/// Returns true if the URL contains disallowed control characters.
pub fn url_contains_control_chars(url: &str) -> bool {
    url.bytes()
        .any(|b| b != b'\t' && b != b'\n' && b != b'\r' && b < 0x20)
}

/// Reject URLs containing any C0 control characters (U+0000–U+001F)
/// for Markdown link destinations. Unlike `url_contains_control_chars`,
/// this also rejects HT, LF, CR which are valid in HTTP headers but
/// enable Markdown injection (line breaking, header smuggling) when
/// embedded in link destinations.
///
/// Returns true if the URL contains any C0 control character.
pub fn link_url_contains_control_chars(url: &str) -> bool {
    url.bytes().any(|b| b < 0x20)
}

/// Reject host values containing characters invalid for HTTP Host
/// headers.  Per RFC 7230 §5.4, a host must not contain path
/// separators, backslashes, spaces, commas, or control characters.
/// This prevents path-traversal injection (e.g. `../`) and header
/// smuggling through the X-Forwarded-Host value.
///
/// Returns true if the host contains invalid characters.
pub fn host_contains_invalid_chars(host: &str) -> bool {
    host.bytes()
        .any(|b| b < 0x20 || b == 0x7F || b == b'/' || b == b'\\' || b == b' ' || b == b',')
}

/// Validate a URL for use in Markdown link destinations.
///
/// Returns Ok(()) if the URL is safe, Err(reason) if it contains
/// control characters or other dangerous content.
pub fn validate_link_url(url: &str) -> Result<(), &'static str> {
    if link_url_contains_control_chars(url) {
        return Err("URL contains control characters");
    }

    let validator = SecurityValidator::new();
    if validator.sanitize_url(url).is_none() {
        return Err("URL has dangerous scheme");
    }

    Ok(())
}

/// Apply the URL scheme policy shared by full-buffer and streaming paths.
///
/// URL schemes are ASCII by definition. Rejecting a non-ASCII scheme prefix
/// prevents Unicode confusables or replacement characters from being treated
/// as an unvalidated scheme after a lossy decode. Raw overlong UTF-8 cannot
/// become a Rust `str`; the FFI/parser boundary rejects malformed UTF-8 before
/// this helper is reached.
pub(crate) fn is_dangerous_url_value(url: &str) -> bool {
    if url.chars().any(|ch| ch == '\0' || ch.is_control()) {
        return true;
    }
    let trimmed = url.trim();
    if contains_percent_encoded_control(trimmed) {
        return true;
    }
    /* A colon is a scheme separator only when it precedes any '/', '?',
     * or '#' delimiter: colons inside relative paths or query strings
     * (e.g. "/docs/fa:q" or "?x=a:b") must not be treated as a scheme
     * separator. */
    let scheme_zone_end = trimmed.find(['/', '?', '#']).unwrap_or(trimmed.len());
    if let Some(colon) = trimmed[..scheme_zone_end].find(':') {
        let scheme = &trimmed[..colon];
        if !scheme.is_empty() && scheme.bytes().any(|byte| byte >= 0x80) {
            return true;
        }
    }

    let url_lower = trimmed.to_ascii_lowercase();
    DANGEROUS_URL_SCHEMES
        .iter()
        .any(|scheme| url_lower.starts_with(scheme))
}

/// Return a safe URL in the canonical form shared by both converter paths.
///
/// Outer ordinary whitespace is presentation noise and is removed. All
/// control characters, including TAB, CR, and LF, are rejected before that
/// normalization; percent-encoded controls remain rejected as well. Empty
/// destinations remain valid after canonicalization so existing callers keep
/// their empty-destination behavior.
pub(crate) fn sanitize_url_value(url: &str) -> Option<&str> {
    if is_dangerous_url_value(url) {
        return None;
    }

    Some(url.trim())
}

/// Return the exact temporary capacity needed by [`escape_markdown_destination`].
///
/// `Some(0)` means that the input can be borrowed without an escaped copy;
/// it is also the valid result for an empty input. Callers must therefore use
/// the value as a no-allocation sentinel rather than as the input length.
pub(crate) fn markdown_destination_escaped_capacity(url: &str) -> Option<usize> {
    let needs_escape = url
        .chars()
        .any(|ch| matches!(ch, ' ' | '(' | ')' | '<' | '>' | '\\' | '\n' | '\r' | '\t'));
    if !needs_escape {
        return Some(0);
    }

    let mut capacity = 2usize;
    for ch in url.chars() {
        let additional = match ch {
            '<' | '>' | '\\' | '\n' | '\r' | '\t' => 2,
            _ => ch.len_utf8(),
        };
        capacity = capacity.checked_add(additional)?;
    }
    Some(capacity)
}

/// Escape a URL for use as a Markdown link or image destination.
///
/// Destinations containing Markdown-sensitive whitespace, delimiters, or
/// control-like line characters are enclosed in angle brackets. Delimiters
/// are backslash-escaped inside the wrapper. Sanitized production URLs do not
/// contain raw controls, but escaping them here keeps this helper safe for
/// every direct caller and makes the full-buffer and streaming emitters share
/// one canonical representation.
pub(crate) fn escape_markdown_destination(url: &str) -> Cow<'_, str> {
    let capacity =
        markdown_destination_escaped_capacity(url).unwrap_or_else(|| url.len().saturating_add(4));
    if capacity == 0 {
        return Cow::Borrowed(url);
    }

    let mut escaped = String::with_capacity(capacity);
    escaped.push('<');
    for ch in url.chars() {
        match ch {
            '<' => escaped.push_str("\\<"),
            '>' => escaped.push_str("\\>"),
            '\\' => escaped.push_str("\\\\"),
            '\n' => escaped.push_str("\\n"),
            '\r' => escaped.push_str("\\r"),
            '\t' => escaped.push_str("\\t"),
            _ => escaped.push(ch),
        }
    }
    escaped.push('>');
    Cow::Owned(escaped)
}

fn contains_percent_encoded_control(url: &str) -> bool {
    url.as_bytes().windows(3).any(|window| {
        window[0] == b'%'
            && hex_value(window[1])
                .zip(hex_value(window[2]))
                .is_some_and(|(high, low)| {
                    let value = (high << 4) | low;
                    value < 0x20 || value == 0x7f
                })
    })
}

fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

/// Parse X-Forwarded-Host and X-Forwarded-Proto headers to
/// construct an effective base URL.
///
/// Returns (scheme, host) or None if headers are absent/empty.
pub fn parse_forwarded_headers(
    x_forwarded_host: Option<&str>,
    x_forwarded_proto: Option<&str>,
) -> Option<(String, String)> {
    let host = x_forwarded_host?.trim();
    if host.is_empty() {
        return None;
    }

    if host_contains_invalid_chars(host) {
        return None;
    }

    /* Additional defense: reject C0 control characters (including HT/LF/CR)
     * in the host value using the same check applied to link URLs.
     * host_contains_invalid_chars already rejects b < 0x20, but
     * link_url_contains_control_chars is called explicitly here so that
     * any future divergence between the two checks cannot silently
     * allow control characters through the forwarded-host path.
     *
     * HT/LF/CR are valid in HTTP header field values (RFC 7230 §3.2)
     * but are rejected in link destinations because they enable
     * Markdown injection (line breaking, header smuggling) and
     * URL scheme obfuscation.  The forwarded host is used to
     * construct link URLs, so it must meet the stricter link
     * destination character set. */
    if link_url_contains_control_chars(host) {
        return None;
    }

    let scheme = match x_forwarded_proto {
        Some(p) => {
            let p = p.trim();
            if p.is_empty() || link_url_contains_control_chars(p) {
                "https".to_string()
            } else {
                p.to_ascii_lowercase()
            }
        }
        None => "https".to_string(),
    };

    if scheme != "http" && scheme != "https" {
        return None;
    }

    Some((scheme, host.to_string()))
}

#[derive(Clone, Copy)]
enum LinkLabelEscapeAction {
    Copy,
    Escape,
    ReplaceWithSpace,
}

fn link_label_escape_action(ch: char) -> LinkLabelEscapeAction {
    match ch {
        '[' | ']' | '\\' | '<' | '>' | '*' | '_' | '`' | '~' => LinkLabelEscapeAction::Escape,
        '\n' | '\r' => LinkLabelEscapeAction::ReplaceWithSpace,
        _ => LinkLabelEscapeAction::Copy,
    }
}

/// Return the exact temporary capacity needed by [`escape_link_label`].
///
/// A label without transformations stays borrowed and needs no temporary
/// allocation. A transformed label receives its exact escaped byte length,
/// so callers can reserve and charge the same amount as the escaper.
pub(crate) fn link_label_escaped_capacity(s: &str) -> Option<usize> {
    let mut capacity = 0usize;
    let mut needs_owned = false;

    for ch in s.chars() {
        let action = link_label_escape_action(ch);
        let byte_len = match action {
            LinkLabelEscapeAction::Copy => ch.len_utf8(),
            LinkLabelEscapeAction::Escape => ch.len_utf8().checked_add(1)?,
            LinkLabelEscapeAction::ReplaceWithSpace => 1,
        };
        capacity = capacity.checked_add(byte_len)?;
        needs_owned |= !matches!(action, LinkLabelEscapeAction::Copy);
    }

    if needs_owned { Some(capacity) } else { Some(0) }
}

/// Escape a string for safe use as a Markdown link label.
///
/// Per CommonMark §4.7, link labels may contain backslash escapes.
/// Escape Markdown inline delimiters (`*`, `_`, `` ` ``, `~`), brackets,
/// backslashes, and angle brackets. Newlines are replaced with spaces to
/// prevent injection via line breaks within link labels.
///
/// This is the single canonical label-escaping implementation; the streaming
/// emitter and the full-buffer traversal both delegate here so the escaping
/// rule cannot drift between emission sites (AGENTS.md Rule 27).
pub fn escape_link_label<'a>(s: &'a str) -> Cow<'a, str> {
    let capacity = link_label_escaped_capacity(s).unwrap_or(s.len());
    if capacity == 0 {
        return Cow::Borrowed(s);
    }

    let first_escape = s
        .char_indices()
        .find(|(_, ch)| !matches!(link_label_escape_action(*ch), LinkLabelEscapeAction::Copy));
    let Some((first_index, _)) = first_escape else {
        return Cow::Borrowed(s);
    };

    let mut out = String::with_capacity(capacity);
    out.push_str(&s[..first_index]);
    for ch in s[first_index..].chars() {
        match link_label_escape_action(ch) {
            LinkLabelEscapeAction::Escape => {
                out.push('\\');
                out.push(ch);
            }
            LinkLabelEscapeAction::ReplaceWithSpace => out.push(' '),
            LinkLabelEscapeAction::Copy => out.push(ch),
        }
    }
    Cow::Owned(out)
}

/// Escape ordinary Markdown text using a fresh line-prefix state.
pub fn escape_markdown_text(s: &str) -> String {
    let mut state = MarkdownTextEscapeState::default();
    escape_markdown_text_with_state(s, &mut state)
}

/// Escape one character into `out`, advancing `state`.
///
/// Incremental companion to [`escape_markdown_text_with_state`]: the escape
/// decisions are identical, but the caller controls the destination buffer so
/// budget-aware writers can append without materializing a full escaped copy
/// of the source text first.
pub(crate) fn escaped_char_len(ch: char, state: &MarkdownTextEscapeState) -> usize {
    ch.len_utf8() + usize::from(requires_escape(ch, state))
}

pub(crate) fn push_escaped_char(out: &mut String, ch: char, state: &mut MarkdownTextEscapeState) {
    if requires_escape(ch, state) {
        out.push('\\');
    }
    out.push(ch);
    state.advance(ch);
}

fn requires_escape(ch: char, state: &MarkdownTextEscapeState) -> bool {
    let block_marker = state.line_prefix
        && state.indent <= 3
        && (matches!(ch, '#' | '>' | '+' | '-' | '!' | '|' | '=')
            || (matches!(ch, '.' | ')') && state.ordered_digits));
    let inline_delimiter = matches!(ch, '\\' | '`' | '*' | '_' | '[' | ']' | '<' | '>' | '~');
    block_marker || inline_delimiter
}

#[cfg(test)]
mod url_validation_tests {
    use super::*;

    #[test]
    fn test_url_no_control_chars() {
        assert!(!url_contains_control_chars("https://example.com/path"));
    }

    #[test]
    fn test_url_with_null_byte() {
        assert!(url_contains_control_chars("https://example.com/\0path"));
    }

    #[test]
    fn test_url_with_ctrl_char() {
        assert!(url_contains_control_chars("https://example.com/\x01path"));
    }

    #[test]
    fn test_url_with_tab_allowed() {
        assert!(!url_contains_control_chars("https://example.com/\tpath"));
    }

    #[test]
    fn test_link_url_tab_rejected() {
        assert!(link_url_contains_control_chars(
            "https://example.com/\tpath"
        ));
    }

    #[test]
    fn test_link_url_newline_rejected() {
        assert!(link_url_contains_control_chars(
            "https://example.com/\npath"
        ));
    }

    #[test]
    fn test_link_url_cr_rejected() {
        assert!(link_url_contains_control_chars(
            "https://example.com/\rpath"
        ));
    }

    #[test]
    fn test_link_url_no_control_chars() {
        assert!(!link_url_contains_control_chars("https://example.com/path"));
    }

    #[test]
    fn test_validate_link_url_safe() {
        assert!(validate_link_url("https://example.com/path").is_ok());
    }

    #[test]
    fn test_validate_link_url_control_chars() {
        assert!(validate_link_url("https://example.com/\0path").is_err());
    }

    #[test]
    fn test_validate_link_url_tab_rejected() {
        assert!(validate_link_url("https://example.com/\tpath").is_err());
    }

    #[test]
    fn test_validate_link_url_newline_rejected() {
        assert!(validate_link_url("https://example.com/\npath").is_err());
    }

    #[test]
    fn test_validate_link_url_cr_rejected() {
        assert!(validate_link_url("https://example.com/\rpath").is_err());
    }

    #[test]
    fn test_parse_forwarded_headers_both() {
        let r = parse_forwarded_headers(Some("api.example.com"), Some("https"));
        assert_eq!(
            r,
            Some(("https".to_string(), "api.example.com".to_string()))
        );
    }

    #[test]
    fn test_parse_forwarded_headers_no_host() {
        let r = parse_forwarded_headers(None, Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_default_proto() {
        let r = parse_forwarded_headers(Some("host"), None);
        assert_eq!(r, Some(("https".to_string(), "host".to_string())));
    }

    #[test]
    fn test_parse_forwarded_headers_host_with_newline_rejected() {
        let r = parse_forwarded_headers(Some("host\r\nX-Malicious: injected"), Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_host_with_tab_rejected() {
        let r = parse_forwarded_headers(Some("host\tmalicious"), Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_host_link_url_control_char_tab() {
        assert!(link_url_contains_control_chars("host\tmalicious"));
        let r = parse_forwarded_headers(Some("host\tmalicious"), Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_host_link_url_control_char_newline() {
        assert!(link_url_contains_control_chars("host\nmalicious"));
        let r = parse_forwarded_headers(Some("host\nmalicious"), Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_host_link_url_control_char_cr() {
        assert!(link_url_contains_control_chars("host\rmalicious"));
        let r = parse_forwarded_headers(Some("host\rmalicious"), Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_proto_with_control_rejected() {
        let r = parse_forwarded_headers(Some("host"), Some("http\nsinjection"));
        assert_eq!(r, Some(("https".to_string(), "host".to_string())));
    }

    #[test]
    fn test_parse_forwarded_headers_path_traversal_rejected() {
        let r = parse_forwarded_headers(Some("evil.com/../etc/passwd"), Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_backslash_rejected() {
        let r = parse_forwarded_headers(Some("evil.com\\@good.com"), Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_space_rejected() {
        let r = parse_forwarded_headers(Some("evil.com malicious.com"), Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_comma_first_hop() {
        let r = parse_forwarded_headers(Some("first-hop, second-hop"), Some("https"));
        assert!(r.is_none());
    }

    #[test]
    fn test_parse_forwarded_headers_valid_host_with_port() {
        let r = parse_forwarded_headers(Some("api.example.com:8080"), Some("https"));
        assert_eq!(
            r,
            Some(("https".to_string(), "api.example.com:8080".to_string()))
        );
    }

    #[test]
    fn test_host_contains_invalid_chars() {
        assert!(host_contains_invalid_chars("evil.com/../etc"));
        assert!(host_contains_invalid_chars("host\\slash"));
        assert!(host_contains_invalid_chars("host space"));
        assert!(host_contains_invalid_chars("first,second"));
        assert!(host_contains_invalid_chars("host\x00null"));
        assert!(host_contains_invalid_chars("host\x7Fdel"));
        assert!(!host_contains_invalid_chars("api.example.com"));
        assert!(!host_contains_invalid_chars("api.example.com:8080"));
        assert!(!host_contains_invalid_chars("[::1]"));
        assert!(!host_contains_invalid_chars("[::1]:8080"));
    }

    #[test]
    fn test_escape_link_label() {
        assert_eq!(escape_link_label("foo [bar] baz"), r"foo \[bar\] baz");
        assert_eq!(escape_link_label("<tag>"), r"\<tag\>");
        assert_eq!(
            escape_link_label("*bold* _italic_ `code` ~strike~"),
            r"\*bold\* \_italic\_ \`code\` \~strike\~"
        );
        assert_eq!(escape_link_label("a\nb"), "a b");
        assert_eq!(escape_link_label("a\rb"), "a b");
    }

    #[test]
    fn test_escape_link_label_capacity_matches_all_escapable_bytes() {
        let label = "[]\\<>*_`~\n";
        let expected = r"\[\]\\\<\>\*\_\`\~ ";

        assert_eq!(link_label_escaped_capacity(label), Some(expected.len()));
        let escaped = escape_link_label(label);
        assert_eq!(escaped.as_ref(), expected);
        match escaped {
            Cow::Owned(value) => assert!(value.capacity() >= expected.len()),
            Cow::Borrowed(_) => panic!("an escaped label must own its output"),
        }
    }

    #[test]
    fn test_plain_link_label_needs_no_temporary_capacity() {
        assert_eq!(link_label_escaped_capacity("plain label"), Some(0));
        assert!(matches!(escape_link_label("plain label"), Cow::Borrowed(_)));
    }

    #[test]
    fn url_attribute_policy_covers_navigation_and_embedded_attributes() {
        for attribute in [
            "href",
            "src",
            "action",
            "formaction",
            "longdesc",
            "dynsrc",
            "lowsrc",
            "manifest",
            "poster",
            "cite",
            "ping",
            "data",
            "codebase",
        ] {
            assert!(URL_ATTRIBUTES.contains(&attribute));
        }
    }

    #[test]
    fn test_escape_markdown_text_blocks_active_syntax() {
        assert_eq!(
            escape_markdown_text(r"[link](javascript:alert(1)) <tag> *em* `code`"),
            r"\[link\](javascript:alert(1)) \<tag\> \*em\* \`code\`"
        );
        assert_eq!(
            escape_markdown_text("- item\n# heading\n1. item"),
            "\\- item\n\\# heading\n1\\. item"
        );
        assert_eq!(escape_markdown_text("a-b"), "a-b");
    }

    #[test]
    fn test_escape_markdown_text_blocks_table_and_setext_markers() {
        assert_eq!(escape_markdown_text("| cell |"), r"\| cell |");
        assert_eq!(escape_markdown_text("heading\n===="), "heading\n\\====");

        let mut state = MarkdownTextEscapeState::default();
        assert_eq!(
            escape_markdown_text_with_state("heading\n=", &mut state),
            "heading\n\\="
        );
        assert_eq!(escape_markdown_text_with_state("===", &mut state), "===");
    }
}
