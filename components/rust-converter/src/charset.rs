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
    charset_from_content_value(content_type.as_bytes())
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
/// One step of the meta-tag scan: continue at the given position, stop, or
/// return a found charset.
enum MetaScanStep {
    Continue(usize),
    Stop,
    Found(String),
}

fn scan_meta_comment(html: &[u8], pos: usize) -> MetaScanStep {
    match find_subslice(&html[pos + 4..], b"-->") {
        Some(end) => MetaScanStep::Continue(pos + 4 + end + 3),
        None => MetaScanStep::Stop,
    }
}

fn scan_meta_tag(html: &[u8], pos: usize) -> MetaScanStep {
    if pos + 1 >= html.len() {
        return MetaScanStep::Stop;
    }
    if let Some(next) = skip_closing_or_declaration(html, pos) {
        return MetaScanStep::Continue(next);
    }

    let mut tag_end = pos + 1;
    while tag_end < html.len() && is_html_name_byte(html[tag_end]) {
        tag_end += 1;
    }
    let tag_name = &html[pos + 1..tag_end];
    if !tag_name.eq_ignore_ascii_case(b"meta") {
        return match find_tag_end(html, tag_end) {
            Some(next_pos) => MetaScanStep::Continue(next_pos),
            None => MetaScanStep::Stop,
        };
    }

    // Parse attributes until '>' and evaluate the charset declaration.
    let (attrs, next_pos, closed) = parse_meta_attributes(html, tag_end);
    if !closed {
        return MetaScanStep::Stop;
    }
    if let Some(charset) = charset_from_meta_attrs(&attrs)
        && encoding_rs::Encoding::for_label(charset.as_bytes()).is_some()
    {
        return MetaScanStep::Found(charset);
    }
    MetaScanStep::Continue(next_pos)
}

/// Process one position in the HTML prefix: skip comments and non-meta tags,
/// and extract a charset from a `<meta>` element if present.
fn scan_one_meta(html: &[u8], pos: usize) -> MetaScanStep {
    // Skip HTML comments so a commented-out meta tag is not treated as
    // a real declaration.
    if html[pos..].starts_with(b"<!--") {
        return scan_meta_comment(html, pos);
    }

    let Some(lt) = find_byte(&html[pos..], b'<') else {
        return MetaScanStep::Stop;
    };
    scan_meta_tag(html, pos + lt)
}

/// Skip a closing tag (`</...>`) or declaration (`<!...>`), including
/// comments.  Returns the position after the skipped element, or `None`
/// when the element is unterminated or `pos` does not start one.
fn skip_closing_or_declaration(html: &[u8], pos: usize) -> Option<usize> {
    if html[pos + 1] == b'/' {
        find_byte(&html[pos..], b'>').map(|gt| pos + gt + 1)
    } else if html[pos + 1] == b'!' {
        if html[pos..].starts_with(b"<!--") {
            find_subslice(&html[pos + 4..], b"-->").map(|end| pos + 4 + end + 3)
        } else {
            find_byte(&html[pos..], b'>').map(|gt| pos + gt + 1)
        }
    } else {
        None
    }
}

pub fn extract_charset_from_html(html: &[u8]) -> Option<String> {
    // Only scan the first META_SCAN_LIMIT bytes for performance.
    let scan_limit = std::cmp::min(html.len(), META_SCAN_LIMIT);
    let html_prefix = &html[..scan_limit];

    // Deterministic byte-level prescanner: locate `<meta ...>` elements,
    // skip HTML comments, and parse attributes in any order with
    // case-insensitive names.  This replaces the previous regex approach,
    // which required `charset` to be the first attribute and could match
    // inside comments.
    let mut pos = 0usize;
    while pos < html_prefix.len() {
        match scan_one_meta(html_prefix, pos) {
            MetaScanStep::Continue(next) => pos = next,
            MetaScanStep::Stop => break,
            MetaScanStep::Found(charset) => return Some(charset),
        }
    }

    None
}

/// A parsed meta attribute list: `(name, value)` pairs.
type MetaAttrs = Vec<(Vec<u8>, Vec<u8>)>;

/// Parse the attribute list of a `<meta ...>` element starting at `attr_pos`.
///
/// Returns `(attributes, position_after_'>', closed)` where `closed` is
/// false when the scan limit was reached before the tag closed.
fn parse_meta_attributes(html_prefix: &[u8], mut attr_pos: usize) -> (MetaAttrs, usize, bool) {
    let mut attrs: MetaAttrs = Vec::new();
    while attr_pos < html_prefix.len() {
        // Skip whitespace.
        skip_html_space(html_prefix, &mut attr_pos);
        if attr_pos >= html_prefix.len() {
            break;
        }
        if html_prefix[attr_pos] == b'>' {
            return (attrs, attr_pos + 1, true);
        }
        if html_prefix[attr_pos] == b'/'
            && attr_pos + 1 < html_prefix.len()
            && html_prefix[attr_pos + 1] == b'>'
        {
            return (attrs, attr_pos + 2, true);
        }

        // Attribute name.
        let name_start = attr_pos;
        while attr_pos < html_prefix.len() && is_html_name_byte(html_prefix[attr_pos]) {
            attr_pos += 1;
        }
        let name = html_prefix[name_start..attr_pos].to_vec();
        if name.is_empty() {
            attr_pos += 1;
            continue;
        }

        // Optional '=' and quoted/unquoted value.
        let value = parse_attr_value(html_prefix, &mut attr_pos);
        attrs.push((name, value));
    }
    (attrs, attr_pos, false)
}

/// Parse the value of an attribute (quoted or bare) starting after the
/// attribute name; advances `attr_pos` past the value.
fn parse_attr_value(html_prefix: &[u8], attr_pos: &mut usize) -> Vec<u8> {
    let mut vp = *attr_pos;
    skip_html_space(html_prefix, &mut vp);
    if vp < html_prefix.len() && html_prefix[vp] == b'=' {
        vp += 1;
        skip_html_space(html_prefix, &mut vp);
        if vp < html_prefix.len() && (html_prefix[vp] == b'"' || html_prefix[vp] == b'\'') {
            let quote = html_prefix[vp];
            vp += 1;
            let value = scan_quoted_value(html_prefix, &mut vp, quote);
            *attr_pos = vp;
            return value;
        }
        let value = scan_bare_value(html_prefix, &mut vp);
        *attr_pos = vp;
        return value;
    }
    *attr_pos = vp;
    Vec::new()
}

/// Scan a quoted attribute value, advancing `vp` past the closing quote.
fn scan_quoted_value(html: &[u8], vp: &mut usize, quote: u8) -> Vec<u8> {
    let mut value = Vec::new();
    while *vp < html.len() && html[*vp] != quote {
        value.push(html[*vp]);
        *vp += 1;
    }
    if *vp < html.len() {
        *vp += 1; // closing quote
    }
    value
}

/// Scan a bare (unquoted) attribute value, advancing `vp` to the next
/// whitespace or `>`.
fn scan_bare_value(html: &[u8], vp: &mut usize) -> Vec<u8> {
    let mut value = Vec::new();
    while *vp < html.len() && !is_html_space(html[*vp]) && html[*vp] != b'>' {
        value.push(html[*vp]);
        *vp += 1;
    }
    value
}

/// Return the charset declared by a `<meta>` element's attributes, or None.
///
/// Supports both forms with attributes in any order:
///   - HTML5: `charset="..."` (any attribute position)
///   - HTML4: `http-equiv="Content-Type"` + `content="text/html; charset=..."`
fn charset_from_meta_attrs(attrs: &[(Vec<u8>, Vec<u8>)]) -> Option<String> {
    let mut charset_attr: Option<&[u8]> = None;
    let mut http_equiv: Option<&[u8]> = None;
    let mut content: Option<&[u8]> = None;

    for (name, value) in attrs {
        if name.eq_ignore_ascii_case(b"charset") {
            charset_attr = Some(value);
        } else if name.eq_ignore_ascii_case(b"http-equiv") {
            http_equiv = Some(value);
        } else if name.eq_ignore_ascii_case(b"content") {
            content = Some(value);
        }
    }

    // HTML5 form: charset attribute anywhere.
    if let Some(cs) = charset_attr
        && !cs.is_empty()
    {
        return Some(String::from_utf8_lossy(cs).into_owned());
    }

    // HTML4 form: http-equiv="Content-Type" with content="...; charset=...".
    if let Some(he) = http_equiv
        && he.eq_ignore_ascii_case(b"Content-Type")
        && let Some(ct) = content
        && let Some(cs) = charset_from_content_value(ct)
    {
        return Some(cs);
    }

    None
}

/// Extract the charset parameter from a `content="text/html; charset=..."`
/// value using a bounded scan (no regex).
fn charset_from_content_value(content: &[u8]) -> Option<String> {
    let mut i = 0usize;

    while i < content.len() {
        // Skip whitespace and ';'.
        skip_space_and_semicolon(content, &mut i);
        if i >= content.len() {
            break;
        }

        // Parameter name.
        let name_start = i;
        while i < content.len() && is_html_name_byte(content[i]) {
            i += 1;
        }
        let name = &content[name_start..i];

        // Skip whitespace and '='.
        skip_html_space(content, &mut i);
        if i >= content.len() || content[i] != b'=' {
            // No '=' after the parameter name (e.g. "text/html" where the
            // '/' terminates "text").  Advance past this parameter so the
            // scan cannot stall on the same position.
            skip_to_semicolon(content, &mut i);
            continue;
        }
        i += 1;
        skip_html_space(content, &mut i);

        // Value (quoted or bare).
        let value = charset_scan_param_value(content, &mut i);
        if name.eq_ignore_ascii_case(b"charset") && !value.is_empty() {
            return Some(String::from_utf8_lossy(&value).into_owned());
        }
    }

    None
}

/// Advance `i` past consecutive whitespace and ';' bytes.
fn skip_space_and_semicolon(content: &[u8], i: &mut usize) {
    while *i < content.len() && (is_html_space(content[*i]) || content[*i] == b';') {
        *i += 1;
    }
}

/// Advance `i` to the next ';' or end of buffer.
fn skip_to_semicolon(content: &[u8], i: &mut usize) {
    while *i < content.len() && content[*i] != b';' {
        *i += 1;
    }
}

/// Read the value of one content-type parameter at `i`.
///
/// Advances `i` past the value.  Quoted values terminate at the closing
/// quote; bare values terminate at whitespace or `;`.
fn charset_scan_param_value(content: &[u8], i: &mut usize) -> Vec<u8> {
    let mut value: Vec<u8> = Vec::new();

    if *i < content.len() && (content[*i] == b'"' || content[*i] == b'\'') {
        let quote = content[*i];
        *i += 1;
        while *i < content.len() && content[*i] != quote {
            value.push(content[*i]);
            *i += 1;
        }
        if *i < content.len() {
            *i += 1;
        }
    } else {
        while *i < content.len() && !is_html_space(content[*i]) && content[*i] != b';' {
            value.push(content[*i]);
            *i += 1;
        }
    }

    value
}

fn find_byte(haystack: &[u8], needle: u8) -> Option<usize> {
    haystack.iter().position(|&b| b == needle)
}

/// Find the end of a start tag without treating `>` inside a quoted
/// attribute value as the tag terminator.
fn find_tag_end(html: &[u8], mut pos: usize) -> Option<usize> {
    let mut quote = None;

    while pos < html.len() {
        match quote {
            Some(delimiter) if html[pos] == delimiter => quote = None,
            Some(_) => {}
            None if html[pos] == b'\'' || html[pos] == b'"' => quote = Some(html[pos]),
            None if html[pos] == b'>' => return Some(pos + 1),
            None => {}
        }
        pos += 1;
    }

    None
}

fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    haystack.windows(needle.len()).position(|w| w == needle)
}

fn is_html_name_byte(b: u8) -> bool {
    // Numeric byte values (45='-', 95='_', 58=':') instead of b'X' literals:
    // the lizard parser miscounts braces in byte-char literals (Rule 17).
    b.is_ascii_alphanumeric() || b == 45u8 || b == 95u8 || b == 58u8
}

fn is_html_space(b: u8) -> bool {
    b == b' ' || b == b'\t' || b == b'\n' || b == b'\r' || b == b'\x0c'
}

/// Advance `pos` past consecutive HTML whitespace bytes.
fn skip_html_space(html: &[u8], pos: &mut usize) {
    while *pos < html.len() && is_html_space(html[*pos]) {
        *pos += 1;
    }
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

    // Negative cases: a parameter only counts as the charset declaration when
    // its name is exactly "charset" (parameter-boundary anchored).  Names that
    // merely contain "charset" as a substring must not hijack detection.
    #[test]
    fn test_extract_charset_from_content_type_ignores_x_charset_param() {
        assert_eq!(
            extract_charset_from_content_type("text/html; x-charset=windows-1252"),
            None
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_ignores_notcharset_param() {
        assert_eq!(
            extract_charset_from_content_type("text/html; notcharset=iso-8859-1"),
            None
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_ignores_charset_inside_quoted_value() {
        assert_eq!(
            extract_charset_from_content_type("text/html; foo=\"charset=windows-1252\""),
            None
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_ignores_charsetx_prefix_param() {
        assert_eq!(
            extract_charset_from_content_type("text/html; charsetx=utf-8"),
            None
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_ignores_empty_charset_value() {
        assert_eq!(
            extract_charset_from_content_type("text/html; charset="),
            None
        );
        assert_eq!(
            extract_charset_from_content_type("text/html; charset=\"\""),
            None
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_ows_around_parameter() {
        assert_eq!(
            extract_charset_from_content_type("text/html ;\tcharset =  UTF-8 "),
            Some("UTF-8".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_duplicate_param_first_wins() {
        assert_eq!(
            extract_charset_from_content_type("text/html; charset=ISO-8859-1; charset=UTF-8"),
            Some("ISO-8859-1".to_string())
        );
    }

    #[test]
    fn test_extract_charset_from_content_type_negative_param_then_real_charset() {
        assert_eq!(
            extract_charset_from_content_type("text/html; x-charset=windows-1252; charset=utf-8"),
            Some("utf-8".to_string())
        );
    }

    #[test]
    fn test_detect_charset_rejects_non_charset_parameter_names() {
        // The hijacking negative cases must fall through to the meta/default
        // levels instead of misreading the forged parameter.
        assert_eq!(
            detect_charset(
                Some("text/html; x-charset=windows-1252"),
                b"<html><head><meta charset=\"UTF-8\"></head></html>"
            ),
            "UTF-8"
        );
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
    // Regression tests: deterministic prescanner (review MEDIUM-1)
    // ============================================================================

    #[test]
    fn test_meta_charset_not_first_attribute() {
        // charset is not the first attribute; the old regex missed this.
        let html = b"<meta id=\"encoding\" charset=\"windows-1252\">";
        assert_eq!(
            extract_charset_from_html(html),
            Some("windows-1252".to_string())
        );
    }

    #[test]
    fn test_meta_charset_after_other_attributes() {
        let html = b"<meta charset=windows-1252 id=x>";
        assert_eq!(
            extract_charset_from_html(html),
            Some("windows-1252".to_string())
        );
    }

    #[test]
    fn test_meta_charset_in_comment_ignored() {
        // A commented-out meta tag must not be treated as a declaration.
        let html = b"<!-- <meta charset=\"utf-8\"> --><meta charset=\"windows-1252\">";
        assert_eq!(
            extract_charset_from_html(html),
            Some("windows-1252".to_string())
        );
    }

    #[test]
    fn test_meta_charset_inside_non_meta_attribute_ignored() {
        let html = b"<div data=\"<meta charset=ISO-8859-1>\">text</div>";
        assert_eq!(extract_charset_from_html(html), None);
    }

    #[test]
    fn test_meta_charset_only_in_comment_returns_none() {
        let html = b"<!-- <meta charset=\"utf-8\"> -->";
        assert_eq!(extract_charset_from_html(html), None);
    }

    #[test]
    fn test_meta_http_equiv_attr_order_swapped() {
        // content before http-equiv: the old regex required http-equiv first.
        let html =
            b"<meta content=\"text/html; charset=windows-1252\" http-equiv=\"Content-Type\">";
        assert_eq!(
            extract_charset_from_html(html),
            Some("windows-1252".to_string())
        );
    }

    #[test]
    fn test_meta_http_equiv_content_charset_any_position() {
        let html = b"<meta http-equiv=\"Content-Type\" content=\"text/html; charset=ISO-8859-1\">";
        assert_eq!(
            extract_charset_from_html(html),
            Some("ISO-8859-1".to_string())
        );
    }

    #[test]
    fn test_meta_charset_single_quoted() {
        let html = b"<meta charset='UTF-8'>";
        assert_eq!(extract_charset_from_html(html), Some("UTF-8".to_string()));
    }

    #[test]
    fn test_meta_charset_uppercase_tag_and_attr() {
        let html = b"<META CHARSET=\"UTF-8\">";
        assert_eq!(extract_charset_from_html(html), Some("UTF-8".to_string()));
    }

    #[test]
    fn test_meta_charset_self_closing() {
        let html = b"<meta charset=\"UTF-8\" />";
        assert_eq!(extract_charset_from_html(html), Some("UTF-8".to_string()));
    }

    #[test]
    fn test_meta_charset_windows_1252_byte_payload() {
        // Real Windows-1252 byte payload (0xE9 = é in cp1252).
        let html =
            b"<html><head><meta id=x charset=windows-1252></head><body>caf\xE9</body></html>";
        assert_eq!(
            extract_charset_from_html(html),
            Some("windows-1252".to_string())
        );
    }

    #[test]
    fn test_meta_charset_iso_8859_1_byte_payload() {
        let html = b"<html><head><meta charset=\"ISO-8859-1\"></head><body>caf\xE9</body></html>";
        assert_eq!(
            extract_charset_from_html(html),
            Some("ISO-8859-1".to_string())
        );
    }

    #[test]
    fn test_meta_charset_after_comment_and_other_tags() {
        let html = b"<html><head><!-- <meta charset=utf-8> --><title>x</title><meta charset=windows-1252></head></html>";
        assert_eq!(
            extract_charset_from_html(html),
            Some("windows-1252".to_string())
        );
    }

    #[test]
    fn test_meta_charset_skips_unsupported_label_and_continues() {
        // A bogus charset label must not terminate the scan: the next
        // supported declaration wins.
        let html = b"<html><head><meta charset=bogus><meta charset=utf-8></head></html>";
        assert_eq!(extract_charset_from_html(html), Some("utf-8".to_string()));
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
