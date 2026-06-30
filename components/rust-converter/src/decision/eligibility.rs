//! Pure response-eligibility decision (Rust-first, 0.9.0).
//!
//! Ports the pure determination logic from the C module
//! (`ngx_http_markdown_check_eligibility`) so that whether an upstream
//! response is eligible for Markdown conversion is decided by a single
//! source of truth in Rust. The C side becomes a thin wrapper that
//! marshals request fields and applies the result.
//!
//! # Design
//!
//! This is a pure function: the same input always produces the same
//! [`Eligibility`]. It performs no I/O and reads no global state. Inputs
//! are primitives and byte slices marshaled from `ngx_http_request_t` and
//! the module configuration by the caller.
//!
//! The decision order mirrors the C implementation exactly so the two
//! agree during the parity-test migration window:
//!
//! 1. filter disabled        -> `IneligibleConfig`
//! 2. method not GET/HEAD     -> `IneligibleMethod`
//! 3. status 206              -> `IneligibleRange`
//! 4. status not 200          -> `IneligibleStatus`
//! 5. Range header present     -> `IneligibleRange`
//! 6. unbounded streaming type -> `IneligibleStreaming`
//! 7. content-type not allowed -> `IneligibleContentType`
//! 8. size exceeds limit       -> `IneligibleSize`
//! 9. otherwise                -> `Eligible`

/// Result of the eligibility decision.
///
/// Mirrors `ngx_http_markdown_eligibility_t`. `IneligibleAuth` is part of
/// the C enum but is decided by a separate auth path, not by this function;
/// it is retained here only so the mapping is exhaustive.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Eligibility {
    /// Response is eligible for conversion.
    Eligible,
    /// Request method is not GET or HEAD.
    IneligibleMethod,
    /// Response status is not 200.
    IneligibleStatus,
    /// Response Content-Type is not in the allowlist.
    IneligibleContentType,
    /// Response size exceeds the configured limit.
    IneligibleSize,
    /// Response is an unbounded streaming type.
    IneligibleStreaming,
    /// Auth policy denies conversion (decided elsewhere).
    IneligibleAuth,
    /// Range request (Range header or 206 Partial Content).
    IneligibleRange,
    /// Conversion disabled by configuration for this request.
    IneligibleConfig,
}

impl Eligibility {
    /// Stable reason string for logging/metrics (matches the C strings).
    pub fn code(self) -> &'static str {
        match self {
            Eligibility::Eligible => "eligible",
            Eligibility::IneligibleMethod => "ineligible_method",
            Eligibility::IneligibleStatus => "ineligible_status",
            Eligibility::IneligibleContentType => "ineligible_content_type",
            Eligibility::IneligibleSize => "ineligible_size",
            Eligibility::IneligibleStreaming => "ineligible_streaming",
            Eligibility::IneligibleAuth => "ineligible_auth",
            Eligibility::IneligibleRange => "ineligible_range",
            Eligibility::IneligibleConfig => "ineligible_config",
        }
    }

    /// Whether this result permits conversion.
    pub fn is_eligible(self) -> bool {
        matches!(self, Eligibility::Eligible)
    }
}

/// Input snapshot for the eligibility decision.
///
/// All fields are copied from the live request and configuration by the
/// caller. Slices borrow caller-owned memory for the duration of the call.
#[derive(Debug, Clone, Copy)]
pub struct EligibilityInput<'a> {
    /// Whether `markdown_filter` is enabled for this request (caller-resolved).
    pub filter_enabled: bool,
    /// Whether the request method is GET or HEAD.
    pub method_get_or_head: bool,
    /// Response status code.
    pub status: u16,
    /// Whether the request carried a `Range` header.
    pub has_range_header: bool,
    /// Response `Content-Type` header value bytes (empty if absent).
    pub content_type: &'a [u8],
    /// Configured `markdown_content_types` allowlist; empty means the
    /// built-in default of `text/html` only.
    pub content_types: &'a [&'a [u8]],
    /// Configured `markdown_stream_types` unbounded-streaming exclusions.
    pub stream_types: &'a [&'a [u8]],
    /// Response `Content-Length`; negative means absent/unknown.
    pub content_length: i64,
    /// Effective full-buffer body limit in bytes; `0` means unlimited.
    pub body_limit: usize,
}

/// HTTP `Content-Type` boundary characters that terminate a type token:
/// end of string, parameter separator, or optional whitespace.
fn matches_type_prefix(content_type: &[u8], needle: &[u8]) -> bool {
    if content_type.len() < needle.len() {
        return false;
    }
    if !content_type[..needle.len()].eq_ignore_ascii_case(needle) {
        return false;
    }
    match content_type.get(needle.len()) {
        None => true,
        Some(&b';') | Some(&b' ') | Some(&b'\t') => true,
        Some(_) => false,
    }
}

/// Mirror of the C `is_streaming` check.
fn is_streaming(content_type: &[u8], stream_types: &[&[u8]]) -> bool {
    if content_type.is_empty() {
        return false;
    }
    if matches_type_prefix(content_type, b"text/event-stream") {
        return true;
    }
    stream_types
        .iter()
        .any(|t| matches_type_prefix(content_type, t))
}

/// Mirror of the C `check_content_type` check.
fn content_type_allowed(content_type: &[u8], allowlist: &[&[u8]]) -> bool {
    if content_type.is_empty() {
        return false;
    }
    if allowlist.is_empty() {
        return matches_type_prefix(content_type, b"text/html");
    }
    allowlist
        .iter()
        .any(|entry| matches_type_prefix(content_type, entry))
}

/// Mirror of the C `check_size_limit` check.
fn size_within_limit(content_length: i64, body_limit: usize) -> bool {
    if content_length < 0 {
        return true;
    }
    if body_limit == 0 {
        return true;
    }
    (content_length as u64) <= (body_limit as u64)
}

/// Decide whether an upstream response is eligible for conversion.
///
/// Pure function: same input -> same result. See the module docs for the
/// exact decision order, which mirrors the C implementation.
pub fn decide_eligibility(input: &EligibilityInput) -> Eligibility {
    if !input.filter_enabled {
        return Eligibility::IneligibleConfig;
    }

    if !input.method_get_or_head {
        return Eligibility::IneligibleMethod;
    }

    if input.status != 200 {
        if input.status == 206 {
            return Eligibility::IneligibleRange;
        }
        return Eligibility::IneligibleStatus;
    }

    if input.has_range_header {
        return Eligibility::IneligibleRange;
    }

    if is_streaming(input.content_type, input.stream_types) {
        return Eligibility::IneligibleStreaming;
    }

    if !content_type_allowed(input.content_type, input.content_types) {
        return Eligibility::IneligibleContentType;
    }

    if !size_within_limit(input.content_length, input.body_limit) {
        return Eligibility::IneligibleSize;
    }

    Eligibility::Eligible
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> EligibilityInput<'static> {
        EligibilityInput {
            filter_enabled: true,
            method_get_or_head: true,
            status: 200,
            has_range_header: false,
            content_type: b"text/html",
            content_types: &[],
            stream_types: &[],
            content_length: -1,
            body_limit: 10 * 1024 * 1024,
        }
    }

    #[test]
    fn eligible_happy_path() {
        assert_eq!(decide_eligibility(&base()), Eligibility::Eligible);
    }

    #[test]
    fn disabled_filter() {
        let mut i = base();
        i.filter_enabled = false;
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleConfig);
    }

    #[test]
    fn method_not_get_or_head() {
        let mut i = base();
        i.method_get_or_head = false;
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleMethod);
    }

    #[test]
    fn status_206_is_range() {
        let mut i = base();
        i.status = 206;
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleRange);
    }

    #[test]
    fn status_other_is_status() {
        let mut i = base();
        i.status = 500;
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleStatus);
    }

    #[test]
    fn range_header_is_range() {
        let mut i = base();
        i.has_range_header = true;
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleRange);
    }

    #[test]
    fn event_stream_is_streaming() {
        let mut i = base();
        i.content_type = b"text/event-stream";
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleStreaming);
    }

    #[test]
    fn configured_stream_type_is_streaming() {
        let mut i = base();
        i.content_type = b"application/x-ndjson";
        let types: [&[u8]; 1] = [b"application/x-ndjson"];
        i.stream_types = &types;
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleStreaming);
    }

    #[test]
    fn streaming_checked_before_content_type() {
        // text/event-stream is not in the html allowlist, but the streaming
        // check runs first and must win (matches C order).
        let mut i = base();
        i.content_type = b"text/event-stream; charset=utf-8";
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleStreaming);
    }

    #[test]
    fn non_html_content_type() {
        let mut i = base();
        i.content_type = b"application/json";
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleContentType);
    }

    #[test]
    fn empty_content_type_not_allowed() {
        let mut i = base();
        i.content_type = b"";
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleContentType);
    }

    #[test]
    fn html_with_charset_allowed() {
        let mut i = base();
        i.content_type = b"text/html; charset=utf-8";
        assert_eq!(decide_eligibility(&i), Eligibility::Eligible);
    }

    #[test]
    fn html_case_insensitive() {
        let mut i = base();
        i.content_type = b"TEXT/HTML";
        assert_eq!(decide_eligibility(&i), Eligibility::Eligible);
    }

    #[test]
    fn html_prefix_boundary_rejects_htmlx() {
        // "text/htmlx" must NOT match "text/html" (boundary char check).
        let mut i = base();
        i.content_type = b"text/htmlx";
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleContentType);
    }

    #[test]
    fn configured_allowlist_replaces_default() {
        let mut i = base();
        i.content_type = b"application/xhtml+xml";
        let allow: [&[u8]; 1] = [b"application/xhtml+xml"];
        i.content_types = &allow;
        assert_eq!(decide_eligibility(&i), Eligibility::Eligible);
        // text/html no longer eligible when an allowlist is configured.
        i.content_type = b"text/html";
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleContentType);
    }

    #[test]
    fn size_within_limit_ok() {
        let mut i = base();
        i.content_length = 1024;
        i.body_limit = 4096;
        assert_eq!(decide_eligibility(&i), Eligibility::Eligible);
    }

    #[test]
    fn size_exceeds_limit() {
        let mut i = base();
        i.content_length = 8192;
        i.body_limit = 4096;
        assert_eq!(decide_eligibility(&i), Eligibility::IneligibleSize);
    }

    #[test]
    fn size_unknown_passes() {
        let mut i = base();
        i.content_length = -1;
        i.body_limit = 1;
        assert_eq!(decide_eligibility(&i), Eligibility::Eligible);
    }

    #[test]
    fn size_unlimited_when_limit_zero() {
        let mut i = base();
        i.content_length = i64::MAX;
        i.body_limit = 0;
        assert_eq!(decide_eligibility(&i), Eligibility::Eligible);
    }

    #[test]
    fn codes_are_nonempty_unique_and_spaceless() {
        let all = [
            Eligibility::Eligible,
            Eligibility::IneligibleMethod,
            Eligibility::IneligibleStatus,
            Eligibility::IneligibleContentType,
            Eligibility::IneligibleSize,
            Eligibility::IneligibleStreaming,
            Eligibility::IneligibleAuth,
            Eligibility::IneligibleRange,
            Eligibility::IneligibleConfig,
        ];
        let codes: Vec<&str> = all.iter().map(|e| e.code()).collect();
        for c in &codes {
            assert!(!c.is_empty());
            assert!(!c.contains(' '));
        }
        for a in 0..codes.len() {
            for b in (a + 1)..codes.len() {
                assert_ne!(codes[a], codes[b]);
            }
        }
    }

    #[test]
    fn idempotent() {
        let i = base();
        assert_eq!(decide_eligibility(&i), decide_eligibility(&i));
    }
}
