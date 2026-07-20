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
//! 2. **Element Sanitization**: Remove dangerous elements (script, style, etc.); strip tags but preserve content for form and embedded content elements (iframe, object, embed)
//! 3. **Attribute Sanitization**: Remove event handlers (`on*` prefix match) and dangerous attributes
//! 4. **URL Sanitization**: Block javascript:, data:, and external URLs
//! 5. **Entity Safety**: html5ever prevents XXE by default (no external entity resolution)
//!
//! # Requirements
//!
//! Validates: NFR-03.1, NFR-03.2, NFR-03.3, NFR-03.4

use html5ever::Attribute;
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

/// Form-related elements whose tags are stripped but whose child content is
/// preserved for Markdown conversion. These elements may carry meaningful text
/// (labels, button captions, option lists) that AI agents benefit from seeing,
/// but the raw HTML tags must not leak into the Markdown output.
const FORM_ELEMENTS: &[&str] = &[
    "form",     // Container — children often hold descriptive text
    "button",   // Caption text is useful context
    "select",   // Contains <option> text
    "textarea", // May contain default/placeholder text
    "fieldset", // Groups related controls with a <legend>
    "legend",   // Label for a <fieldset>
    "label",    // Descriptive text for a control
    "option",   // Individual choice text inside <select>
    "optgroup", // Group label for options
    "datalist", // Suggestion list
    "output",   // Calculation result text
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

/// Action to take when sanitizing an element
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SanitizeAction {
    /// Allow the element as-is
    Allow,
    /// Remove the element and all its children
    Remove,
    /// Strip the element tag but keep child content for text extraction.
    /// Used for form-related elements whose text is meaningful but whose
    /// HTML structure must not leak into Markdown output.
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
        // Prefix-based detection following OWASP / DOMPurify conventions.
        // The HTML spec reserves the "on*" attribute namespace exclusively
        // for event handlers — no legitimate attribute starts with "on"
        // without being an event handler.
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
        let trimmed = url.trim();
        if trimmed.chars().any(|ch| ch == '\0' || ch.is_control()) {
            return true;
        }
        if Self::contains_percent_encoded_control(trimmed) {
            return true;
        }

        let url_lower = trimmed.to_ascii_lowercase();
        DANGEROUS_URL_SCHEMES
            .iter()
            .any(|scheme| url_lower.starts_with(scheme))
    }

    fn contains_percent_encoded_control(url: &str) -> bool {
        let bytes = url.as_bytes();
        for window in bytes.windows(3) {
            if window[0] != b'%' {
                continue;
            }
            let Some(high) = Self::hex_value(window[1]) else {
                continue;
            };
            let Some(low) = Self::hex_value(window[2]) else {
                continue;
            };
            let value = (high << 4) | low;
            if value < 0x20 || value == 0x7f {
                return true;
            }
        }
        false
    }

    fn hex_value(byte: u8) -> Option<u8> {
        match byte {
            b'0'..=b'9' => Some(byte - b'0'),
            b'a'..=b'f' => Some(byte - b'a' + 10),
            b'A'..=b'F' => Some(byte - b'A' + 10),
            _ => None,
        }
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

            // Check for dangerous URLs in href and src attributes
            if (attr_name == "href" || attr_name == "src") && self.is_dangerous_url(&attr.value) {
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
    /// Returns `None` if the URL is dangerous, `Some(url)` if safe.
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
        if self.is_dangerous_url(url) {
            None
        } else {
            Some(url)
        }
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

            // Remove dangerous URLs
            if (attr_name == "href" || attr_name == "src") && self.is_dangerous_url(&attr.value) {
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

        // Form elements should be stripped (tag removed, children kept)
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

/// Escape a string for safe use as a Markdown link label.
///
/// Per CommonMark §4.7, link labels may contain backslash escapes.
/// Escape: `[`, `]`, `\`. Newlines are replaced with spaces to prevent
/// injection via line breaks within link labels.
///
/// This is the single canonical label-escaping implementation; the streaming
/// emitter and the full-buffer traversal both delegate here so the escaping
/// rule cannot drift between emission sites (AGENTS.md Rule 27).
pub fn escape_link_label(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 8);
    for ch in s.chars() {
        match ch {
            '[' | ']' | '\\' => {
                out.push('\\');
                out.push(ch);
            }
            '\n' | '\r' => out.push(' '),
            _ => out.push(ch),
        }
    }
    out
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
        assert_eq!(escape_link_label("a\nb"), "a b");
        assert_eq!(escape_link_label("a\rb"), "a b");
    }
}
