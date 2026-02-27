//! Character encoding detection and handling
//!
//! This module implements the charset detection cascade as specified in
//! Requirements FR-05.1, FR-05.2, and FR-05.3.
//!
//! # Detection Cascade
//!
//! The charset detection follows a three-level cascade:
//!
//! 1. **Content-Type Header**: Check for charset parameter in Content-Type header
//! 2. **HTML Meta Tags**: Parse HTML for `<meta charset>` or `<meta http-equiv="Content-Type">`
//! 3. **Default to UTF-8**: If both fail, use UTF-8 as the default encoding
//!
//! # Examples
//!
//! ```rust
//! use nginx_markdown_converter::charset::detect_charset;
//!
//! // Detect from Content-Type header
//! let charset = detect_charset(Some("text/html; charset=ISO-8859-1"), b"<html>...</html>");
//! assert_eq!(charset, "ISO-8859-1");
//!
//! // Detect from HTML meta tag
//! let html = b"<html><head><meta charset=\"UTF-8\"></head></html>";
//! let charset = detect_charset(None, html);
//! assert_eq!(charset, "UTF-8");
//!
//! // Default to UTF-8
//! let charset = detect_charset(None, b"<html><body>No charset</body></html>");
//! assert_eq!(charset, "UTF-8");
//! ```

use regex::Regex;
use std::sync::OnceLock;

/// Default charset when detection fails
const DEFAULT_CHARSET: &str = "UTF-8";

/// Maximum bytes to scan for meta charset tags (first 1024 bytes)
const META_SCAN_LIMIT: usize = 1024;

/// Detect character encoding using the three-level cascade
///
/// This function implements the charset detection cascade specified in
/// Requirements FR-05.1, FR-05.2, and FR-05.3:
///
/// 1. Check Content-Type header charset parameter (FR-05.1)
/// 2. Check HTML meta charset tags (FR-05.2)
/// 3. Default to UTF-8 (FR-05.3)
///
/// # Arguments
///
/// * `content_type` - Optional Content-Type header value (e.g., "text/html; charset=UTF-8")
/// * `html` - HTML content bytes to scan for meta charset tags
///
/// # Returns
///
/// Returns the detected charset as a string. Always returns a valid charset,
/// defaulting to "UTF-8" if detection fails.
///
/// # Examples
///
/// ```rust
/// use nginx_markdown_converter::charset::detect_charset;
///
/// // Priority 1: Content-Type header
/// let charset = detect_charset(
///     Some("text/html; charset=ISO-8859-1"),
///     b"<html>...</html>"
/// );
/// assert_eq!(charset, "ISO-8859-1");
///
/// // Priority 2: HTML meta tag
/// let html = b"<html><head><meta charset=\"UTF-8\"></head></html>";
/// let charset = detect_charset(None, html);
/// assert_eq!(charset, "UTF-8");
///
/// // Priority 3: Default to UTF-8
/// let charset = detect_charset(None, b"<html><body>No charset</body></html>");
/// assert_eq!(charset, "UTF-8");
/// ```
///
/// # Charset Normalization
///
/// The function normalizes charset names to uppercase for consistency:
/// - "utf-8" → "UTF-8"
/// - "iso-8859-1" → "ISO-8859-1"
/// - "windows-1252" → "WINDOWS-1252"
pub fn detect_charset(content_type: Option<&str>, html: &[u8]) -> String {
    // Level 1: Check Content-Type header charset parameter (FR-05.1)
    if let Some(ct) = content_type
        && let Some(charset) = extract_charset_from_content_type(ct)
    {
        return normalize_charset(&charset);
    }

    // Level 2: Check HTML meta charset tags (FR-05.2)
    if let Some(charset) = extract_charset_from_html(html) {
        return normalize_charset(&charset);
    }

    // Level 3: Default to UTF-8 (FR-05.3)
    DEFAULT_CHARSET.to_string()
}

/// Extract charset from Content-Type header
///
/// Parses the Content-Type header for a charset parameter.
///
/// # Supported Formats
///
/// - `text/html; charset=UTF-8`
/// - `text/html; charset="UTF-8"`
/// - `text/html;charset=UTF-8` (no space)
/// - `text/html; charset=UTF-8; boundary=...` (multiple parameters)
///
/// # Arguments
///
/// * `content_type` - Content-Type header value
///
/// # Returns
///
/// Returns `Some(charset)` if found, `None` otherwise.
///
/// # Examples
///
/// ```rust
/// use nginx_markdown_converter::charset::extract_charset_from_content_type;
///
/// assert_eq!(
///     extract_charset_from_content_type("text/html; charset=UTF-8"),
///     Some("UTF-8".to_string())
/// );
///
/// assert_eq!(
///     extract_charset_from_content_type("text/html; charset=\"ISO-8859-1\""),
///     Some("ISO-8859-1".to_string())
/// );
///
/// assert_eq!(
///     extract_charset_from_content_type("text/html"),
///     None
/// );
/// ```
pub fn extract_charset_from_content_type(content_type: &str) -> Option<String> {
    // Regex pattern to match charset parameter
    // Matches: charset=VALUE or charset="VALUE"
    static CHARSET_REGEX: OnceLock<Option<Regex>> = OnceLock::new();
    let regex =
        CHARSET_REGEX.get_or_init(|| Regex::new(r#"(?i)charset\s*=\s*"?([^";,\s]+)"?"#).ok());
    let regex = regex.as_ref()?;

    regex
        .captures(content_type)
        .and_then(|caps| caps.get(1))
        .map(|m| m.as_str().to_string())
}

/// Extract charset from HTML meta tags
///
/// Scans the HTML content for charset declarations in meta tags.
///
/// # Supported Formats
///
/// - HTML5: `<meta charset="UTF-8">`
/// - HTML4: `<meta http-equiv="Content-Type" content="text/html; charset=UTF-8">`
///
/// # Arguments
///
/// * `html` - HTML content bytes to scan
///
/// # Returns
///
/// Returns `Some(charset)` if found, `None` otherwise.
///
/// # Performance
///
/// Only scans the first 1024 bytes of HTML for performance.
/// Meta charset tags should appear in the `<head>` section early in the document.
///
/// # Examples
///
/// ```rust
/// use nginx_markdown_converter::charset::extract_charset_from_html;
///
/// // HTML5 meta charset
/// let html = b"<html><head><meta charset=\"UTF-8\"></head></html>";
/// assert_eq!(extract_charset_from_html(html), Some("UTF-8".to_string()));
///
/// // HTML4 meta http-equiv
/// let html = b"<meta http-equiv=\"Content-Type\" content=\"text/html; charset=ISO-8859-1\">";
/// assert_eq!(extract_charset_from_html(html), Some("ISO-8859-1".to_string()));
///
/// // No charset found
/// let html = b"<html><body>No charset</body></html>";
/// assert_eq!(extract_charset_from_html(html), None);
/// ```
pub fn extract_charset_from_html(html: &[u8]) -> Option<String> {
    // Only scan the first META_SCAN_LIMIT bytes for performance
    let scan_limit = std::cmp::min(html.len(), META_SCAN_LIMIT);
    let html_prefix = &html[..scan_limit];

    // Convert to string for regex matching (lossy conversion is OK for meta tag detection)
    let html_str = String::from_utf8_lossy(html_prefix);

    // Try HTML5 meta charset format first
    static HTML5_REGEX: OnceLock<Option<Regex>> = OnceLock::new();
    let html5_regex =
        HTML5_REGEX.get_or_init(|| Regex::new(r#"(?i)<meta\s+charset\s*=\s*"?([^";>\s]+)"?"#).ok());
    let html5_regex = html5_regex.as_ref()?;

    if let Some(caps) = html5_regex.captures(&html_str)
        && let Some(m) = caps.get(1)
    {
        return Some(m.as_str().to_string());
    }

    // Try HTML4 meta http-equiv format
    static HTML4_REGEX: OnceLock<Option<Regex>> = OnceLock::new();
    let html4_regex = HTML4_REGEX.get_or_init(|| {
        Regex::new(
            r#"(?i)<meta\s+http-equiv\s*=\s*"?Content-Type"?\s+content\s*=\s*"?[^">]*charset\s*=\s*([^";>\s]+)"?"#,
        )
        .ok()
    });
    let html4_regex = html4_regex.as_ref()?;

    if let Some(caps) = html4_regex.captures(&html_str)
        && let Some(m) = caps.get(1)
    {
        return Some(m.as_str().to_string());
    }

    None
}

/// Normalize charset name to uppercase
///
/// Converts charset names to uppercase for consistency.
///
/// # Arguments
///
/// * `charset` - Charset name to normalize
///
/// # Returns
///
/// Returns the normalized charset name in uppercase.
///
/// # Examples
///
/// ```rust
/// use nginx_markdown_converter::charset::normalize_charset;
///
/// assert_eq!(normalize_charset("utf-8"), "UTF-8");
/// assert_eq!(normalize_charset("ISO-8859-1"), "ISO-8859-1");
/// assert_eq!(normalize_charset("windows-1252"), "WINDOWS-1252");
/// ```
pub fn normalize_charset(charset: &str) -> String {
    charset.to_uppercase()
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    // ============================================================================
    // Unit Tests for Content-Type Charset Extraction
    // ============================================================================

    #[test]
    fn test_extract_charset_from_content_type_basic() {
        assert_eq!(
            extract_charset_from_content_type("text/html; charset=UTF-8"),
            Some("UTF-8".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_quoted() {
        assert_eq!(
            extract_charset_from_content_type("text/html; charset=\"UTF-8\""),
            Some("UTF-8".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_no_space() {
        assert_eq!(
            extract_charset_from_content_type("text/html;charset=UTF-8"),
            Some("UTF-8".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_multiple_params() {
        assert_eq!(
            extract_charset_from_content_type("text/html; charset=UTF-8; boundary=something"),
            Some("UTF-8".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_case_insensitive() {
        assert_eq!(
            extract_charset_from_content_type("text/html; CHARSET=UTF-8"),
            Some("UTF-8".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_iso_8859_1() {
        assert_eq!(
            extract_charset_from_content_type("text/html; charset=ISO-8859-1"),
            Some("ISO-8859-1".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_windows_1252() {
        assert_eq!(
            extract_charset_from_content_type("text/html; charset=windows-1252"),
            Some("windows-1252".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_no_charset() {
        assert_eq!(extract_charset_from_content_type("text/html"), None);
    }

    #[test]
    fn test_extract_charset_from_content_type_empty() {
        assert_eq!(extract_charset_from_content_type(""), None);
    }

    // ============================================================================
    // Unit Tests for HTML Meta Charset Extraction
    // ============================================================================

    #[test]
    fn test_extract_charset_from_html_html5_format() {
        let html = b"<html><head><meta charset=\"UTF-8\"></head></html>";
        assert_eq!(extract_charset_from_html(html), Some("UTF-8".to_string()));
    }

    #[test]
    fn test_extract_charset_from_html_html5_no_quotes() {
        let html = b"<html><head><meta charset=UTF-8></head></html>";
        assert_eq!(extract_charset_from_html(html), Some("UTF-8".to_string()));
    }

    #[test]
    fn test_extract_charset_from_html_html4_format() {
        let html = b"<meta http-equiv=\"Content-Type\" content=\"text/html; charset=ISO-8859-1\">";
        assert_eq!(
            extract_charset_from_html(html),
            Some("ISO-8859-1".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_html_html4_no_quotes() {
        let html = b"<meta http-equiv=Content-Type content=\"text/html; charset=UTF-8\">";
        assert_eq!(extract_charset_from_html(html), Some("UTF-8".to_string()));
    }

    #[test]
    fn test_extract_charset_from_html_case_insensitive() {
        let html = b"<html><head><META CHARSET=\"UTF-8\"></head></html>";
        assert_eq!(extract_charset_from_html(html), Some("UTF-8".to_string()));
    }

    #[test]
    fn test_extract_charset_from_html_with_whitespace() {
        let html = b"<html><head><meta   charset  =  \"UTF-8\"  ></head></html>";
        assert_eq!(extract_charset_from_html(html), Some("UTF-8".to_string()));
    }

    #[test]
    fn test_extract_charset_from_html_no_charset() {
        let html = b"<html><head><title>Test</title></head></html>";
        assert_eq!(extract_charset_from_html(html), None);
    }

    #[test]
    fn test_extract_charset_from_html_empty() {
        let html = b"";
        assert_eq!(extract_charset_from_html(html), None);
    }

    #[test]
    fn test_extract_charset_from_html_beyond_scan_limit() {
        // Create HTML with charset beyond scan limit
        let mut html = vec![b' '; META_SCAN_LIMIT + 100];
        let charset_tag = b"<meta charset=\"UTF-8\">";
        html.extend_from_slice(charset_tag);

        // Should not find charset beyond scan limit
        assert_eq!(extract_charset_from_html(&html), None);
    }

    // ============================================================================
    // Unit Tests for Charset Detection Cascade
    // ============================================================================

    #[test]
    fn test_detect_charset_priority_content_type() {
        // Content-Type should take priority over HTML meta tag
        let html = b"<html><head><meta charset=\"ISO-8859-1\"></head></html>";
        let charset = detect_charset(Some("text/html; charset=UTF-8"), html);
        assert_eq!(charset, "UTF-8");
    }

    #[test]
    fn test_detect_charset_fallback_to_html_meta() {
        // Should use HTML meta tag when Content-Type has no charset
        let html = b"<html><head><meta charset=\"ISO-8859-1\"></head></html>";
        let charset = detect_charset(Some("text/html"), html);
        assert_eq!(charset, "ISO-8859-1");
    }

    #[test]
    fn test_detect_charset_fallback_to_default() {
        // Should default to UTF-8 when both fail
        let html = b"<html><head><title>No charset</title></head></html>";
        let charset = detect_charset(None, html);
        assert_eq!(charset, "UTF-8");
    }

    #[test]
    fn test_detect_charset_normalization() {
        // Should normalize charset to uppercase
        let charset = detect_charset(Some("text/html; charset=utf-8"), b"");
        assert_eq!(charset, "UTF-8");
    }

    #[test]
    fn test_detect_charset_empty_content_type() {
        // Empty Content-Type should fall back to HTML meta
        let html = b"<html><head><meta charset=\"UTF-8\"></head></html>";
        let charset = detect_charset(Some(""), html);
        assert_eq!(charset, "UTF-8");
    }

    #[test]
    fn test_detect_charset_various_charsets() {
        // Test various charset names
        let charsets = vec![
            "UTF-8",
            "ISO-8859-1",
            "ISO-8859-15",
            "windows-1252",
            "GB2312",
            "Big5",
            "Shift_JIS",
            "EUC-KR",
        ];

        for cs in charsets {
            let content_type = format!("text/html; charset={}", cs);
            let detected = detect_charset(Some(&content_type), b"");
            assert_eq!(detected, cs.to_uppercase());
        }
    }

    // ============================================================================
    // Unit Tests for Charset Normalization
    // ============================================================================

    #[test]
    fn test_normalize_charset_lowercase() {
        assert_eq!(normalize_charset("utf-8"), "UTF-8");
    }

    #[test]
    fn test_normalize_charset_uppercase() {
        assert_eq!(normalize_charset("UTF-8"), "UTF-8");
    }

    #[test]
    fn test_normalize_charset_mixed_case() {
        assert_eq!(normalize_charset("Utf-8"), "UTF-8");
    }

    #[test]
    fn test_normalize_charset_iso() {
        assert_eq!(normalize_charset("iso-8859-1"), "ISO-8859-1");
    }

    #[test]
    fn test_normalize_charset_windows() {
        assert_eq!(normalize_charset("windows-1252"), "WINDOWS-1252");
    }

    // ============================================================================
    // Property-Based Tests
    // ============================================================================

    proptest! {
        /// Property 14: Charset Detection Cascade
        /// Validates: FR-05.1, FR-05.2, FR-05.3
        #[test]
        fn prop_detect_charset_content_type_has_priority_over_html_meta(
            header_charset in prop::sample::select(vec!["utf-8", "iso-8859-1", "windows-1252", "shift_jis", "gb2312"]),
            meta_charset in prop::sample::select(vec!["UTF-8", "ISO-8859-1", "WINDOWS-1252", "SHIFT_JIS", "GB2312"]),
        ) {
            prop_assume!(header_charset.to_uppercase() != meta_charset.to_uppercase());

            let content_type = format!("text/html; charset={header_charset}");
            let html = format!(r#"<html><head><meta charset="{meta_charset}"></head><body>x</body></html>"#);

            let detected = detect_charset(Some(&content_type), html.as_bytes());
            prop_assert_eq!(detected, header_charset.to_uppercase());
        }

        #[test]
        fn prop_detect_charset_falls_back_to_html_meta_when_header_has_no_charset(
            meta_charset in prop::sample::select(vec!["utf-8", "iso-8859-1", "windows-1252", "shift_jis", "big5"]),
            use_html4_syntax in any::<bool>(),
        ) {
            let html = if use_html4_syntax {
                format!(
                    r#"<html><head><meta http-equiv="Content-Type" content="text/html; charset={}"></head></html>"#,
                    meta_charset
                )
            } else {
                format!(r#"<html><head><meta charset="{}"></head></html>"#, meta_charset)
            };

            let detected = detect_charset(Some("text/html"), html.as_bytes());
            prop_assert_eq!(detected, meta_charset.to_uppercase());
        }
    }
}
