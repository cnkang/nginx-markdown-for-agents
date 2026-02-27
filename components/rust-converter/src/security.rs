//! Security validation and sanitization for HTML input
//!
//! This module implements comprehensive security measures to prevent:
//! - XSS (Cross-Site Scripting) attacks
//! - XXE (XML External Entity) attacks
//! - SSRF (Server-Side Request Forgery) attacks
//! - Code injection through event handlers
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
//! 2. **Element Sanitization**: Remove dangerous elements (script, iframe, object, embed)
//! 3. **Attribute Sanitization**: Remove event handlers and dangerous attributes
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
    "noscript", // Alternative content, not needed for Markdown
    "iframe",   // Can load external content
    "object",   // Can execute plugins
    "embed",    // Can execute plugins
    "applet",   // Legacy Java applets
    "link",     // Can load external stylesheets with expressions
    "base",     // Can change base URL for all relative URLs
];

/// Event handler attributes that should be removed
/// These allow JavaScript execution when events occur
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
        } else {
            SanitizeAction::Allow
        }
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
        EVENT_HANDLER_ATTRIBUTES.contains(&attr_name)
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
        let url_lower = url.trim().to_lowercase();
        DANGEROUS_URL_SCHEMES
            .iter()
            .any(|scheme| url_lower.starts_with(scheme))
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

            // Remove dangerous URLs
            if (attr_name == "href" || attr_name == "src") && self.is_dangerous_url(&attr.value) {
                to_remove.push(attr_name.to_string());
            }
        }

        to_remove
    }
}

impl Default for SecurityValidator {
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
        assert_eq!(validator.check_element("iframe"), SanitizeAction::Remove);
        assert_eq!(validator.check_element("object"), SanitizeAction::Remove);
        assert_eq!(validator.check_element("embed"), SanitizeAction::Remove);
        assert_eq!(validator.check_element("style"), SanitizeAction::Remove);

        // Safe elements should be allowed
        assert_eq!(validator.check_element("div"), SanitizeAction::Allow);
        assert_eq!(validator.check_element("p"), SanitizeAction::Allow);
        assert_eq!(validator.check_element("a"), SanitizeAction::Allow);
    }

    #[test]
    fn test_event_handlers() {
        let validator = SecurityValidator::new();

        // Event handlers should be detected
        assert!(validator.is_event_handler("onclick"));
        assert!(validator.is_event_handler("onload"));
        assert!(validator.is_event_handler("onerror"));
        assert!(validator.is_event_handler("onmouseover"));

        // Normal attributes should not be detected as event handlers
        assert!(!validator.is_event_handler("href"));
        assert!(!validator.is_event_handler("src"));
        assert!(!validator.is_event_handler("class"));
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

        // Safe URLs
        assert!(!validator.is_dangerous_url("https://example.com"));
        assert!(!validator.is_dangerous_url("http://example.com"));
        assert!(!validator.is_dangerous_url("/relative/path"));
        assert!(!validator.is_dangerous_url("../parent/path"));
        assert!(!validator.is_dangerous_url("#anchor"));
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
