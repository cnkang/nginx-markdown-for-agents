//! Conditional-request decision (RFC 7232 / 7234, spec 49, 0.9.0).
//!
//! This module is the Rust **single source of truth** for the conditional
//! request decision. It wraps the lower-level RFC 7232 primitive in
//! [`crate::conditional::evaluate_conditional`] with three additional
//! concerns that previously lived as scattered branches in the C module:
//!
//! 1. **`markdown_cache_validation` mode** (`off` / `ims_only` / `full`)
//!    — controls *which* conditional headers are honored.
//! 2. **`If-None-Match` over `If-Modified-Since` precedence** (RFC 7232 §6)
//!    — when both are present in `full` mode, only `If-None-Match` is
//!    evaluated; `If-Modified-Since` is ignored.
//! 3. **Transformation bypass** for `Range` requests (RFC 7233) and
//!    `Cache-Control: no-transform` (RFC 7234 §5.2.1.6) — these never
//!    produce a `304`; they bypass conversion entirely.
//!
//! # Design
//!
//! [`decide_conditional`] is a pure function: the same input always yields
//! the same [`ConditionalDecision`]. It performs no I/O and reads no global
//! state. The C caller marshals request headers, response metadata, and the
//! effective `cache_validation` mode across the narrow FFI boundary
//! (`markdown_decide_conditional`); it never passes `ngx_http_request_t *`
//! or a pool.
//!
//! # ETag note
//!
//! `entity_etag` is the **transformed-representation** ETag, which only
//! exists on the full-buffer path under `cache_validation = full`. On the
//! streaming path no ETag is generated (headers are committed before the
//! transformed body is known), so the caller passes `None` and
//! `If-None-Match` can never match — see spec 49 Requirement 1.5.
//!
//! Validates: spec 49 Requirements 1.1, 1.8, 1.9, 2.1, 3.x.

/// Effective `markdown_cache_validation` mode (Config V2, spec 45/49).
///
/// Discriminants are the FFI source of truth and are frozen for the 1.0
/// stability contract: add new modes, never renumber.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CacheValidation {
    /// `off` — no conditional handling, no ETag; always proceed.
    Off = 0,
    /// `ims_only` — honor `If-Modified-Since` only; no ETag, ignore
    /// `If-None-Match`.
    ImsOnly = 1,
    /// `full` — honor `If-None-Match` + `If-Modified-Since` and generate a
    /// transformed-representation ETag (full-buffer path only).
    Full = 2,
}

impl CacheValidation {
    /// Construct from the FFI `u8` discriminant; unknown values fall back to
    /// the safe `ImsOnly` default (the `balanced` profile default).
    pub fn from_u8(value: u8) -> Self {
        match value {
            0 => CacheValidation::Off,
            2 => CacheValidation::Full,
            _ => CacheValidation::ImsOnly,
        }
    }

    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// Which conditional header the decision actually evaluated.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConditionalHeader {
    /// No conditional header was evaluated.
    None = 0,
    /// `If-None-Match` was evaluated (took precedence in `full` mode).
    IfNoneMatch = 1,
    /// `If-Modified-Since` was evaluated.
    IfModifiedSince = 2,
}

impl ConditionalHeader {
    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// Top-level outcome of the conditional decision.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConditionalOutcome {
    /// Respond `304 Not Modified` (no body).
    NotModified = 0,
    /// Proceed with the normal (converted) response.
    Proceed = 1,
    /// Bypass conversion entirely and deliver the upstream response
    /// unmodified (`Range` / `no-transform`).
    Bypass = 2,
}

impl ConditionalOutcome {
    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// Reason code for the conditional decision (spec 53 registry alignment).
///
/// String forms are lower_snake_case and match the spec 53 reason-code
/// names. Discriminants are frozen for the 1.0 stability contract.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConditionalReason {
    /// `conditional_no_headers` — no conditional header present (or
    /// `cache_validation = off`); proceed normally.
    NoHeaders = 0,
    /// `conditional_inm_evaluated` — `If-None-Match` was evaluated.
    InmEvaluated = 1,
    /// `conditional_ims_evaluated` — `If-Modified-Since` was evaluated.
    ImsEvaluated = 2,
    /// `bypass_range_request` — `Range` request bypasses conversion.
    BypassRange = 3,
    /// `bypass_no_transform` — `Cache-Control: no-transform` bypasses
    /// conversion.
    BypassNoTransform = 4,
}

impl ConditionalReason {
    /// Stable lower_snake_case reason string (spec 53 alignment).
    pub fn as_str(self) -> &'static str {
        match self {
            ConditionalReason::NoHeaders => "conditional_no_headers",
            ConditionalReason::InmEvaluated => "conditional_inm_evaluated",
            ConditionalReason::ImsEvaluated => "conditional_ims_evaluated",
            ConditionalReason::BypassRange => "bypass_range_request",
            ConditionalReason::BypassNoTransform => "bypass_no_transform",
        }
    }

    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// Result of [`decide_conditional`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConditionalDecision {
    /// Top-level outcome.
    pub outcome: ConditionalOutcome,
    /// Reason code (spec 53 alignment).
    pub reason: ConditionalReason,
    /// Which conditional header was evaluated (if any).
    pub evaluated_header: ConditionalHeader,
}

/// Input snapshot for [`decide_conditional`].
///
/// All slices borrow caller-owned memory for the duration of the call;
/// `None` means the header/value is absent.
#[derive(Debug, Clone, Copy)]
pub struct ConditionalInput<'a> {
    /// Effective `markdown_cache_validation` mode.
    pub cache_validation: CacheValidation,
    /// Whether the request carried a `Range` header.
    pub has_range: bool,
    /// Whether request or response carried `Cache-Control: no-transform`.
    pub no_transform: bool,
    /// `If-None-Match` header value (transformed-representation comparison).
    pub if_none_match: Option<&'a str>,
    /// Transformed-representation entity ETag (full-buffer + `full` only).
    pub entity_etag: Option<&'a str>,
    /// `If-Modified-Since` header value.
    pub if_modified_since: Option<&'a str>,
    /// Preserved upstream `Last-Modified` value.
    pub last_modified: Option<&'a str>,
}

/// Decide the conditional-request outcome.
///
/// Pure function: same input → same result. See the module docs for the
/// exact precedence and bypass rules.
///
/// # Decision order
///
/// 1. `Range` present → `Bypass` (`bypass_range_request`).
/// 2. `no-transform` present → `Bypass` (`bypass_no_transform`).
/// 3. `cache_validation = off` → `Proceed` (`conditional_no_headers`).
/// 4. `cache_validation = ims_only` → evaluate `If-Modified-Since` only,
///    ignoring `If-None-Match`.
/// 5. `cache_validation = full` → evaluate `If-None-Match` if present
///    (ignoring `If-Modified-Since`, RFC 7232 §6); otherwise evaluate
///    `If-Modified-Since`.
pub fn decide_conditional(input: &ConditionalInput) -> ConditionalDecision {
    use crate::conditional::evaluate_conditional;

    // 1. Range bypass (RFC 7233): never a 304, never a conversion.
    if input.has_range {
        return ConditionalDecision {
            outcome: ConditionalOutcome::Bypass,
            reason: ConditionalReason::BypassRange,
            evaluated_header: ConditionalHeader::None,
        };
    }

    // 2. Cache-Control: no-transform bypass (RFC 7234 §5.2.1.6).
    if input.no_transform {
        return ConditionalDecision {
            outcome: ConditionalOutcome::Bypass,
            reason: ConditionalReason::BypassNoTransform,
            evaluated_header: ConditionalHeader::None,
        };
    }

    match input.cache_validation {
        // 3. off: never evaluate conditional headers.
        CacheValidation::Off => ConditionalDecision {
            outcome: ConditionalOutcome::Proceed,
            reason: ConditionalReason::NoHeaders,
            evaluated_header: ConditionalHeader::None,
        },

        // 4. ims_only: honor If-Modified-Since only; ignore If-None-Match.
        CacheValidation::ImsOnly => {
            if input.if_modified_since.is_some() {
                let result =
                    evaluate_conditional(None, None, input.if_modified_since, input.last_modified);
                ConditionalDecision {
                    outcome: outcome_from(result),
                    reason: ConditionalReason::ImsEvaluated,
                    evaluated_header: ConditionalHeader::IfModifiedSince,
                }
            } else {
                ConditionalDecision {
                    outcome: ConditionalOutcome::Proceed,
                    reason: ConditionalReason::NoHeaders,
                    evaluated_header: ConditionalHeader::None,
                }
            }
        }

        // 5. full: If-None-Match takes precedence over If-Modified-Since.
        CacheValidation::Full => {
            if input.if_none_match.is_some() {
                // Evaluate If-None-Match only (ignore IMS per RFC 7232 §6).
                let result =
                    evaluate_conditional(input.if_none_match, input.entity_etag, None, None);
                ConditionalDecision {
                    outcome: outcome_from(result),
                    reason: ConditionalReason::InmEvaluated,
                    evaluated_header: ConditionalHeader::IfNoneMatch,
                }
            } else if input.if_modified_since.is_some() {
                let result =
                    evaluate_conditional(None, None, input.if_modified_since, input.last_modified);
                ConditionalDecision {
                    outcome: outcome_from(result),
                    reason: ConditionalReason::ImsEvaluated,
                    evaluated_header: ConditionalHeader::IfModifiedSince,
                }
            } else {
                ConditionalDecision {
                    outcome: ConditionalOutcome::Proceed,
                    reason: ConditionalReason::NoHeaders,
                    evaluated_header: ConditionalHeader::None,
                }
            }
        }
    }
}

/// Map the low-level RFC 7232 result onto the top-level outcome.
fn outcome_from(result: crate::conditional::ConditionalResult) -> ConditionalOutcome {
    match result {
        crate::conditional::ConditionalResult::NotModified => ConditionalOutcome::NotModified,
        crate::conditional::ConditionalResult::Proceed => ConditionalOutcome::Proceed,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> ConditionalInput<'static> {
        ConditionalInput {
            cache_validation: CacheValidation::Full,
            has_range: false,
            no_transform: false,
            if_none_match: None,
            entity_etag: None,
            if_modified_since: None,
            last_modified: None,
        }
    }

    #[test]
    fn range_bypasses_everything() {
        let mut i = base();
        i.has_range = true;
        // Even a matching INM must not produce a 304 when Range is present.
        i.if_none_match = Some("\"abc\"");
        i.entity_etag = Some("\"abc\"");
        let d = decide_conditional(&i);
        assert_eq!(d.outcome, ConditionalOutcome::Bypass);
        assert_eq!(d.reason, ConditionalReason::BypassRange);
        assert_eq!(d.evaluated_header, ConditionalHeader::None);
    }

    #[test]
    fn no_transform_bypasses_everything() {
        let mut i = base();
        i.no_transform = true;
        i.if_modified_since = Some("Sun, 06 Nov 1994 08:49:37 GMT");
        i.last_modified = Some("Fri, 04 Nov 1994 08:49:37 GMT");
        let d = decide_conditional(&i);
        assert_eq!(d.outcome, ConditionalOutcome::Bypass);
        assert_eq!(d.reason, ConditionalReason::BypassNoTransform);
    }

    #[test]
    fn range_takes_precedence_over_no_transform() {
        let mut i = base();
        i.has_range = true;
        i.no_transform = true;
        let d = decide_conditional(&i);
        assert_eq!(d.reason, ConditionalReason::BypassRange);
    }

    #[test]
    fn off_always_proceeds() {
        let mut i = base();
        i.cache_validation = CacheValidation::Off;
        // Present + matching INM must still proceed in off mode.
        i.if_none_match = Some("\"abc\"");
        i.entity_etag = Some("\"abc\"");
        i.if_modified_since = Some("Sun, 06 Nov 1994 08:49:37 GMT");
        i.last_modified = Some("Fri, 04 Nov 1994 08:49:37 GMT");
        let d = decide_conditional(&i);
        assert_eq!(d.outcome, ConditionalOutcome::Proceed);
        assert_eq!(d.reason, ConditionalReason::NoHeaders);
        assert_eq!(d.evaluated_header, ConditionalHeader::None);
    }

    #[test]
    fn ims_only_ignores_if_none_match() {
        let mut i = base();
        i.cache_validation = CacheValidation::ImsOnly;
        // A matching INM is present but must be ignored in ims_only mode.
        i.if_none_match = Some("\"abc\"");
        i.entity_etag = Some("\"abc\"");
        // IMS indicates not modified (lm earlier than ims).
        i.if_modified_since = Some("Sun, 06 Nov 1994 08:49:37 GMT");
        i.last_modified = Some("Fri, 04 Nov 1994 08:49:37 GMT");
        let d = decide_conditional(&i);
        assert_eq!(d.outcome, ConditionalOutcome::NotModified);
        assert_eq!(d.reason, ConditionalReason::ImsEvaluated);
        assert_eq!(d.evaluated_header, ConditionalHeader::IfModifiedSince);
    }

    #[test]
    fn ims_only_no_ims_header_proceeds() {
        let mut i = base();
        i.cache_validation = CacheValidation::ImsOnly;
        i.if_none_match = Some("\"abc\"");
        i.entity_etag = Some("\"abc\"");
        let d = decide_conditional(&i);
        assert_eq!(d.outcome, ConditionalOutcome::Proceed);
        assert_eq!(d.reason, ConditionalReason::NoHeaders);
    }

    #[test]
    fn full_inm_takes_precedence_over_ims() {
        let mut i = base();
        i.cache_validation = CacheValidation::Full;
        // INM does NOT match -> Proceed, and IMS (which would say
        // NotModified) must be ignored because INM took precedence.
        i.if_none_match = Some("\"different\"");
        i.entity_etag = Some("\"abc\"");
        i.if_modified_since = Some("Sun, 06 Nov 1994 08:49:37 GMT");
        i.last_modified = Some("Fri, 04 Nov 1994 08:49:37 GMT");
        let d = decide_conditional(&i);
        assert_eq!(d.outcome, ConditionalOutcome::Proceed);
        assert_eq!(d.reason, ConditionalReason::InmEvaluated);
        assert_eq!(d.evaluated_header, ConditionalHeader::IfNoneMatch);
    }

    #[test]
    fn full_inm_match_not_modified() {
        let mut i = base();
        i.if_none_match = Some("\"abc\"");
        i.entity_etag = Some("\"abc\"");
        let d = decide_conditional(&i);
        assert_eq!(d.outcome, ConditionalOutcome::NotModified);
        assert_eq!(d.evaluated_header, ConditionalHeader::IfNoneMatch);
    }

    #[test]
    fn full_ims_only_when_no_inm() {
        let mut i = base();
        i.if_modified_since = Some("Sun, 06 Nov 1994 08:49:37 GMT");
        i.last_modified = Some("Fri, 04 Nov 1994 08:49:37 GMT");
        let d = decide_conditional(&i);
        assert_eq!(d.outcome, ConditionalOutcome::NotModified);
        assert_eq!(d.reason, ConditionalReason::ImsEvaluated);
        assert_eq!(d.evaluated_header, ConditionalHeader::IfModifiedSince);
    }

    #[test]
    fn full_no_headers_proceeds() {
        let d = decide_conditional(&base());
        assert_eq!(d.outcome, ConditionalOutcome::Proceed);
        assert_eq!(d.reason, ConditionalReason::NoHeaders);
    }

    #[test]
    fn streaming_path_no_etag_inm_cannot_match() {
        // On the streaming path the caller passes entity_etag = None, so a
        // client If-None-Match can never match -> Proceed.
        let mut i = base();
        i.if_none_match = Some("\"abc\"");
        i.entity_etag = None;
        let d = decide_conditional(&i);
        assert_eq!(d.outcome, ConditionalOutcome::Proceed);
        assert_eq!(d.evaluated_header, ConditionalHeader::IfNoneMatch);
    }

    #[test]
    fn cache_validation_from_u8_roundtrip_and_fallback() {
        assert_eq!(CacheValidation::from_u8(0), CacheValidation::Off);
        assert_eq!(CacheValidation::from_u8(1), CacheValidation::ImsOnly);
        assert_eq!(CacheValidation::from_u8(2), CacheValidation::Full);
        // Unknown -> safe ImsOnly default.
        assert_eq!(CacheValidation::from_u8(99), CacheValidation::ImsOnly);
        for m in [
            CacheValidation::Off,
            CacheValidation::ImsOnly,
            CacheValidation::Full,
        ] {
            assert_eq!(CacheValidation::from_u8(m.as_u8()), m);
        }
    }

    #[test]
    fn reason_strings_are_lower_snake_case_and_unique() {
        let all = [
            ConditionalReason::NoHeaders,
            ConditionalReason::InmEvaluated,
            ConditionalReason::ImsEvaluated,
            ConditionalReason::BypassRange,
            ConditionalReason::BypassNoTransform,
        ];
        let mut seen = std::collections::HashSet::new();
        for r in all {
            let s = r.as_str();
            assert!(!s.is_empty());
            for ch in s.chars() {
                assert!(
                    ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '_',
                    "reason '{}' has invalid char '{}'",
                    s,
                    ch
                );
            }
            assert!(seen.insert(s), "duplicate reason string '{}'", s);
        }
    }

    #[test]
    fn idempotent() {
        let i = base();
        assert_eq!(decide_conditional(&i), decide_conditional(&i));
    }
}
