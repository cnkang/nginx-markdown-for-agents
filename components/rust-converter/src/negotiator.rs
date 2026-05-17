//! HTTP Accept header negotiation for markdown conversion.
//!
//! Implements content negotiation per RFC 7231 §5.3.2: parses the client
//! `Accept` header, extracts q-values for `text/markdown` and related MIME
//! types, and determines whether conversion should proceed based on the
//! client's expressed preferences.
//!
//! # Design
//!
//! The negotiator considers only the "available" variants relevant to this
//! module: `text/markdown` (conversion output) and `text/html` (original
//! upstream content). Other MIME types in the Accept header are ignored for
//! negotiation purposes but respected for wildcard matching.
//!
//! # Wildcard Behavior
//!
//! `*/*` in the Accept header is treated as `q=1.0` for both text/markdown
//! and text/html, unless `on_wildcard` is false (in which case `*/*` does
//! not imply markdown preference).
//!
//! # Examples
//!
//! ```
//! use nginx_markdown_converter::negotiator::{negotiate, NegotiationResult};
//!
//! let result = negotiate("text/markdown;q=0.9, text/html;q=0.8", true);
//! assert!(matches!(result, NegotiationResult::Convert));
//!
//! let result = negotiate("text/html", true);
//! assert!(matches!(result, NegotiationResult::Passthrough { .. }));
//! ```

/// Maximum number of Accept header entries to parse.
/// Prevents DoS from unreasonably long headers.
const MAX_ACCEPT_ENTRIES: usize = 64;

/// Maximum length of a single MIME type string.
const MAX_MIME_LEN: usize = 128;

/// Maximum length of the entire Accept header we will parse.
const MAX_HEADER_LEN: usize = 4096;

/// Result of Accept header negotiation.
#[derive(Debug, Clone, PartialEq)]
pub enum NegotiationResult {
    /// Client prefers text/markdown: proceed with conversion.
    Convert,

    /// Client does not prefer text/markdown: pass through original content.
    Passthrough {
        /// Human-readable reason for the passthrough decision.
        reason: PassthroughReason,
    },
}

/// Reason the negotiator decided not to convert.
#[derive(Debug, Clone, PartialEq)]
pub enum PassthroughReason {
    /// No Accept header was present.
    NoAcceptHeader,

    /// Accept header is present but text/markdown has lower q-value
    /// than text/html, or text/markdown is absent.
    LowerQValue,

    /// Client explicitly set text/markdown;q=0 (reject).
    ExplicitReject,

    /// Accept header is malformed; safe fallback is passthrough.
    MalformedHeader,
}

/// A single entry in the Accept header: MIME type + q-value.
#[derive(Debug, Clone)]
struct AcceptEntry {
    mime_type: String,
    q_value: u16, // q * 1000, e.g., q=0.9 → 900
}

/// Parse a q-value from a parameter string like "q=0.9".
///
/// Returns q * 1000 as u16 (range 0..=1000).
/// Returns None if the q-value is malformed.
fn parse_q_value(param: &str) -> Option<u16> {
    let param = param.trim();

    if !param.starts_with("q=") && !param.starts_with("Q=") {
        return None;
    }

    let value_str = &param[2..];

    if value_str.is_empty() {
        return None;
    }

    // Parse the decimal q-value.
    // Supported formats: "1", "1.0", "0.9", "0.123", "0", "0.0"
    if let Ok(v) = value_str.parse::<f32>() {
        if v >= 0.0 && v <= 1.0 {
            // Round to 3 decimal places (RFC 7231 limit).
            let scaled = (v * 1000.0).round() as u16;
            if scaled <= 1000 {
                return Some(scaled);
            }
        }
    }

    None
}

/// Normalize a MIME type: lowercase, trim whitespace.
fn normalize_mime(mime: &str) -> String {
    mime.trim().to_ascii_lowercase()
}

/// Parse the Accept header into a list of (mime_type, q_value) entries.
///
/// Per RFC 7231 §5.3.2, each entry is separated by comma,
/// and parameters (including q) are separated by semicolon.
/// The default q-value is 1.0 (1000) when not specified.
fn parse_accept_header(header: &str) -> Vec<AcceptEntry> {
    let mut entries = Vec::new();
    let mut count = 0;

    for entry_str in header.split(',') {
        if count >= MAX_ACCEPT_ENTRIES {
            break;
        }

        let entry_str = entry_str.trim();
        if entry_str.is_empty() {
            continue;
        }

        let mut parts = entry_str.split(';');
        let mime_part = match parts.next() {
            Some(m) => m.trim(),
            None => continue,
        };

        if mime_part.is_empty() || mime_part.len() > MAX_MIME_LEN {
            continue;
        }

        let mime_type = normalize_mime(mime_part);

        // Default q=1.0
        let mut q_value: u16 = 1000;

        for param in parts {
            let param = param.trim();
            if let Some(q) = parse_q_value(param) {
                q_value = q;
                break;
            }
        }

        entries.push(AcceptEntry { mime_type, q_value });
        count += 1;
    }

    entries
}

/// Determine whether to convert based on the parsed Accept entries.
///
/// Returns the q-value for text/markdown and text/html (0 if absent).
fn extract_q_values(entries: &[AcceptEntry], on_wildcard: bool) -> (u16, u16) {
    let mut markdown_q: u16 = 0;
    let mut html_q: u16 = 0;

    for entry in entries {
        match entry.mime_type.as_str() {
            "text/markdown" => {
                if entry.q_value > markdown_q {
                    markdown_q = entry.q_value;
                }
            }
            "text/html" => {
                if entry.q_value > html_q {
                    html_q = entry.q_value;
                }
            }
            "*/*" => {
                if on_wildcard {
                    if entry.q_value > markdown_q {
                        markdown_q = entry.q_value;
                    }
                    if entry.q_value > html_q {
                        html_q = entry.q_value;
                    }
                }
            }
            "text/*" => {
                // text/* matches both text/markdown and text/html
                if entry.q_value > markdown_q {
                    markdown_q = entry.q_value;
                }
                if entry.q_value > html_q {
                    html_q = entry.q_value;
                }
            }
            _ => {}
        }
    }

    (markdown_q, html_q)
}

/// Perform Accept header negotiation.
///
/// # Arguments
///
/// * `accept_header` - The raw Accept header value (may be empty or malformed).
/// * `on_wildcard` - Whether `*/*` in the Accept header implies markdown
///   preference (corresponds to `markdown_on_wildcard on`).
///
/// # Returns
///
/// * `NegotiationResult::Convert` if the client prefers text/markdown.
/// * `NegotiationResult::Passthrough` otherwise, with the reason.
///
/// # Examples
///
/// ```
/// use nginx_markdown_converter::negotiator::{negotiate, NegotiationResult, PassthroughReason};
///
/// // Explicit markdown preference
/// let r = negotiate("text/markdown", true);
/// assert_eq!(r, NegotiationResult::Convert);
///
/// // No Accept header
/// let r = negotiate("", true);
/// assert_eq!(r, NegotiationResult::Passthrough { reason: PassthroughReason::NoAcceptHeader });
///
/// // Explicit reject: text/markdown;q=0
/// let r = negotiate("text/markdown;q=0, text/html", true);
/// assert_eq!(r, NegotiationResult::Passthrough { reason: PassthroughReason::ExplicitReject });
/// ```
pub fn negotiate(accept_header: &str, on_wildcard: bool) -> NegotiationResult {
    // Empty or missing Accept header: passthrough.
    let trimmed = accept_header.trim();
    if trimmed.is_empty() {
        return NegotiationResult::Passthrough {
            reason: PassthroughReason::NoAcceptHeader,
        };
    }

    // Guard: reject unreasonably long headers.
    if trimmed.len() > MAX_HEADER_LEN {
        return NegotiationResult::Passthrough {
            reason: PassthroughReason::MalformedHeader,
        };
    }

    let entries = parse_accept_header(trimmed);

    if entries.is_empty() {
        return NegotiationResult::Passthrough {
            reason: PassthroughReason::MalformedHeader,
        };
    }

    let (markdown_q, html_q) = extract_q_values(&entries, on_wildcard);

    // Explicit reject: text/markdown;q=0
    if markdown_q == 0 {
        // Check if text/markdown was actually present with q=0
        for entry in &entries {
            if entry.mime_type == "text/markdown" && entry.q_value == 0 {
                return NegotiationResult::Passthrough {
                    reason: PassthroughReason::ExplicitReject,
                };
            }
        }
        // text/markdown not present at all, or only via wildcard with q=0
        return NegotiationResult::Passthrough {
            reason: PassthroughReason::LowerQValue,
        };
    }

    // text/markdown has higher or equal q-value than text/html: convert.
    // Equal q-values prefer the more specific type (text/markdown),
    // matching the principle that the client is willing to accept markdown.
    if markdown_q >= html_q {
        NegotiationResult::Convert
    } else {
        NegotiationResult::Passthrough {
            reason: PassthroughReason::LowerQValue,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_explicit_markdown_preference() {
        let r = negotiate("text/markdown", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_markdown_higher_q() {
        let r = negotiate("text/markdown;q=0.9, text/html;q=0.8", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_html_higher_q() {
        let r = negotiate("text/html;q=0.9, text/markdown;q=0.8", true);
        assert_eq!(
            r,
            NegotiationResult::Passthrough {
                reason: PassthroughReason::LowerQValue
            }
        );
    }

    #[test]
    fn test_no_accept_header() {
        let r = negotiate("", true);
        assert_eq!(
            r,
            NegotiationResult::Passthrough {
                reason: PassthroughReason::NoAcceptHeader
            }
        );
    }

    #[test]
    fn test_whitespace_only_header() {
        let r = negotiate("   ", true);
        assert_eq!(
            r,
            NegotiationResult::Passthrough {
                reason: PassthroughReason::NoAcceptHeader
            }
        );
    }

    #[test]
    fn test_explicit_reject_q_zero() {
        let r = negotiate("text/markdown;q=0, text/html", true);
        assert_eq!(
            r,
            NegotiationResult::Passthrough {
                reason: PassthroughReason::ExplicitReject
            }
        );
    }

    #[test]
    fn test_wildcard_on() {
        let r = negotiate("*/*", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_wildcard_off() {
        let r = negotiate("*/*", false);
        assert_eq!(
            r,
            NegotiationResult::Passthrough {
                reason: PassthroughReason::LowerQValue
            }
        );
    }

    #[test]
    fn test_text_wildcard() {
        let r = negotiate("text/*", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_default_q_is_one() {
        // text/markdown without explicit q → q=1.0
        // text/html without explicit q → q=1.0
        // Equal q-values prefer markdown (the module's purpose).
        let r = negotiate("text/html, text/markdown", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_markdown_only_in_accept() {
        let r = negotiate("text/markdown;q=1.0", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_html_only_in_accept() {
        let r = negotiate("text/html", true);
        assert_eq!(
            r,
            NegotiationResult::Passthrough {
                reason: PassthroughReason::LowerQValue
            }
        );
    }

    #[test]
    fn test_case_insensitive_mime() {
        let r = negotiate("Text/Markdown", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_case_insensitive_q_param() {
        let r = negotiate("text/markdown;Q=0.9, text/html;q=0.8", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_malformed_header_too_long() {
        let long_header = "text/markdown, ".repeat(500);
        let r = negotiate(&long_header, true);
        // Should not panic; may convert or passthrough depending on
        // what fits in MAX_ACCEPT_ENTRIES, but must not crash.
        let _ = r;
    }

    #[test]
    fn test_malformed_q_value_ignored() {
        // Invalid q-value falls back to default q=1.0
        let r = negotiate("text/markdown;q=invalid, text/html;q=0.5", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_q_value_boundary_zero() {
        let r = negotiate("text/markdown;q=0.0", true);
        assert_eq!(
            r,
            NegotiationResult::Passthrough {
                reason: PassthroughReason::ExplicitReject
            }
        );
    }

    #[test]
    fn test_q_value_boundary_one() {
        let r = negotiate("text/markdown;q=1.0", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_q_value_three_decimal_places() {
        // q=0.123 should be preserved
        let r = negotiate("text/markdown;q=0.123, text/html;q=0.100", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_unrelated_mime_types_ignored() {
        let r = negotiate("application/json;q=1.0, text/markdown;q=0.5, text/html;q=0.9", true);
        assert_eq!(
            r,
            NegotiationResult::Passthrough {
                reason: PassthroughReason::LowerQValue
            }
        );
    }

    #[test]
    fn test_multiple_markdown_entries_takes_highest() {
        let r = negotiate("text/markdown;q=0.3, text/markdown;q=0.9, text/html;q=0.8", true);
        assert_eq!(r, NegotiationResult::Convert);
    }

    #[test]
    fn test_empty_entry_in_header() {
        let r = negotiate("text/markdown,,text/html;q=0.5", true);
        assert_eq!(r, NegotiationResult::Convert);
    }
}
