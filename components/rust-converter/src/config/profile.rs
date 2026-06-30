//! Profile definitions and defaults (spec 50, 0.9.0).
//!
//! Three production profiles cover the most common deployment scenarios:
//!
//! - **`strict_cache`** — CDN/caching proxy: full conditional request support,
//!   no streaming. Streaming is *forced* off (conflict → error).
//! - **`balanced`** — recommended default: strict negotiation, IMS-only caching
//!   (no ETag overhead), streaming on auto. No forced fields.
//! - **`streaming_first`** — AI agent workloads: aggressive streaming, no
//!   caching overhead, wildcard Accept. Cache validation *forced* off and
//!   streaming *forced* on (conflict → error).
//!
//! # Forced Fields
//!
//! A profile may *force* certain fields to specific values. An explicit
//! directive that contradicts a forced field is a configuration error
//! detected at `nginx -t` time.

use crate::decision::conditional::CacheValidation;
use crate::decision::streaming::StreamingPolicy;

/// Accept-header negotiation mode (Config V2, spec 45).
///
/// Controls how the module evaluates the `Accept` request header to decide
/// whether to convert.
///
/// Discriminants are frozen for the 1.0 stability contract.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AcceptMode {
    /// `strict` — require explicit `text/markdown` preference (q > text/html).
    Strict = 0,
    /// `wildcard` — accept `*/*` as sufficient (lenient for AI agents).
    Wildcard = 1,
    /// `force` — always convert regardless of Accept header.
    Force = 2,
}

impl AcceptMode {
    /// Construct from the FFI `u8`; unknown values fall back to `Strict`.
    pub fn from_u8(value: u8) -> Self {
        match value {
            1 => AcceptMode::Wildcard,
            2 => AcceptMode::Force,
            _ => AcceptMode::Strict,
        }
    }

    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// Error-handling policy (Config V2, spec 45).
///
/// Controls behavior when the conversion pipeline encounters a fatal error.
///
/// Discriminants are frozen for the 1.0 stability contract.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorPolicy {
    /// `pass` — deliver the upstream response unmodified (fail-open).
    Pass = 0,
    /// `fail_closed` — return an error status to the client.
    FailClosed = 1,
}

impl ErrorPolicy {
    /// Construct from the FFI `u8`; unknown values fall back to `Pass`.
    pub fn from_u8(value: u8) -> Self {
        match value {
            1 => ErrorPolicy::FailClosed,
            _ => ErrorPolicy::Pass,
        }
    }

    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// Production profile selector.
///
/// Each variant maps to a complete set of [`ProfileDefaults`] and a list of
/// [`ForcedField`]s that cannot be overridden by explicit directives.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Profile {
    /// CDN/caching proxy: full conditional requests, no streaming.
    StrictCache,
    /// Recommended default: IMS-only caching, streaming on auto.
    Balanced,
    /// AI agent workloads: aggressive streaming, no caching overhead.
    StreamingFirst,
}

/// Profile-expanded default values for all profile-relevant fields.
///
/// These values override the built-in defaults when a profile is active.
/// Explicit user directives may further override non-forced fields.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProfileDefaults {
    /// Accept-header negotiation mode.
    pub accept: AcceptMode,
    /// Cache-validation mode.
    pub cache_validation: CacheValidation,
    /// Streaming enablement policy.
    pub streaming: StreamingPolicy,
    /// Memory limit in bytes.
    pub limits_memory_bytes: u64,
    /// Timeout in milliseconds.
    pub limits_timeout_ms: u64,
    /// Streaming buffer size in bytes (`None` when streaming is off).
    pub limits_streaming_buffer_bytes: Option<u64>,
    /// Maximum concurrent in-flight conversions.
    pub limits_max_inflight: u32,
    /// Error-handling policy.
    pub error_policy: ErrorPolicy,
    /// Whether diagnostics endpoint is enabled.
    pub diagnostics: bool,
}

/// A field that a profile *forces* to a specific value.
///
/// Explicit directives that set a different value for a forced field produce
/// a configuration error at `nginx -t` time.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ForcedField {
    /// Profile forces a specific streaming policy.
    Streaming(StreamingPolicy),
    /// Profile forces a specific cache-validation mode.
    CacheValidation(CacheValidation),
}

impl Profile {
    /// Expand the profile into its default field values.
    ///
    /// All three profiles share the same resource limits (8 MiB memory,
    /// 2 s timeout, 64 max-inflight) and operational defaults (error_policy
    /// = pass, diagnostics = off). They differ in accept mode, caching,
    /// and streaming behavior.
    pub fn defaults(self) -> ProfileDefaults {
        match self {
            Profile::StrictCache => ProfileDefaults {
                accept: AcceptMode::Strict,
                cache_validation: CacheValidation::Full,
                streaming: StreamingPolicy::Off,
                limits_memory_bytes: 8 * 1024 * 1024,
                limits_timeout_ms: 2000,
                limits_streaming_buffer_bytes: None,
                limits_max_inflight: 64,
                error_policy: ErrorPolicy::Pass,
                diagnostics: false,
            },
            Profile::Balanced => ProfileDefaults {
                accept: AcceptMode::Strict,
                cache_validation: CacheValidation::ImsOnly,
                streaming: StreamingPolicy::Auto,
                limits_memory_bytes: 8 * 1024 * 1024,
                limits_timeout_ms: 2000,
                limits_streaming_buffer_bytes: Some(256 * 1024),
                limits_max_inflight: 64,
                error_policy: ErrorPolicy::Pass,
                diagnostics: false,
            },
            Profile::StreamingFirst => ProfileDefaults {
                accept: AcceptMode::Wildcard,
                cache_validation: CacheValidation::Off,
                streaming: StreamingPolicy::Force,
                limits_memory_bytes: 8 * 1024 * 1024,
                limits_timeout_ms: 2000,
                limits_streaming_buffer_bytes: Some(256 * 1024),
                limits_max_inflight: 64,
                error_policy: ErrorPolicy::Pass,
                diagnostics: false,
            },
        }
    }

    /// Return the fields that this profile *forces* to specific values.
    ///
    /// An explicit directive that sets a different value for any forced field
    /// produces a configuration error. `balanced` has no forced fields — all
    /// its defaults can be freely overridden.
    pub fn forced_fields(self) -> &'static [ForcedField] {
        match self {
            Profile::StrictCache => &[ForcedField::Streaming(StreamingPolicy::Off)],
            Profile::StreamingFirst => &[
                ForcedField::CacheValidation(CacheValidation::Off),
                ForcedField::Streaming(StreamingPolicy::Force),
            ],
            Profile::Balanced => &[],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strict_cache_defaults() {
        let d = Profile::StrictCache.defaults();
        assert_eq!(d.accept, AcceptMode::Strict);
        assert_eq!(d.cache_validation, CacheValidation::Full);
        assert_eq!(d.streaming, StreamingPolicy::Off);
        assert_eq!(d.limits_memory_bytes, 8 * 1024 * 1024);
        assert_eq!(d.limits_timeout_ms, 2000);
        assert_eq!(d.limits_streaming_buffer_bytes, None);
        assert_eq!(d.limits_max_inflight, 64);
        assert_eq!(d.error_policy, ErrorPolicy::Pass);
        assert!(!d.diagnostics);
    }

    #[test]
    fn balanced_defaults() {
        let d = Profile::Balanced.defaults();
        assert_eq!(d.accept, AcceptMode::Strict);
        assert_eq!(d.cache_validation, CacheValidation::ImsOnly);
        assert_eq!(d.streaming, StreamingPolicy::Auto);
        assert_eq!(d.limits_memory_bytes, 8 * 1024 * 1024);
        assert_eq!(d.limits_timeout_ms, 2000);
        assert_eq!(d.limits_streaming_buffer_bytes, Some(256 * 1024));
        assert_eq!(d.limits_max_inflight, 64);
        assert_eq!(d.error_policy, ErrorPolicy::Pass);
        assert!(!d.diagnostics);
    }

    #[test]
    fn streaming_first_defaults() {
        let d = Profile::StreamingFirst.defaults();
        assert_eq!(d.accept, AcceptMode::Wildcard);
        assert_eq!(d.cache_validation, CacheValidation::Off);
        assert_eq!(d.streaming, StreamingPolicy::Force);
        assert_eq!(d.limits_memory_bytes, 8 * 1024 * 1024);
        assert_eq!(d.limits_timeout_ms, 2000);
        assert_eq!(d.limits_streaming_buffer_bytes, Some(256 * 1024));
        assert_eq!(d.limits_max_inflight, 64);
        assert_eq!(d.error_policy, ErrorPolicy::Pass);
        assert!(!d.diagnostics);
    }

    #[test]
    fn strict_cache_forced_fields() {
        let forced = Profile::StrictCache.forced_fields();
        assert_eq!(forced.len(), 1);
        assert_eq!(forced[0], ForcedField::Streaming(StreamingPolicy::Off));
    }

    #[test]
    fn streaming_first_forced_fields() {
        let forced = Profile::StreamingFirst.forced_fields();
        assert_eq!(forced.len(), 2);
        assert_eq!(
            forced[0],
            ForcedField::CacheValidation(CacheValidation::Off)
        );
        assert_eq!(forced[1], ForcedField::Streaming(StreamingPolicy::Force));
    }

    #[test]
    fn balanced_no_forced_fields() {
        let forced = Profile::Balanced.forced_fields();
        assert!(forced.is_empty());
    }

    #[test]
    fn accept_mode_from_u8_roundtrip() {
        for m in [AcceptMode::Strict, AcceptMode::Wildcard, AcceptMode::Force] {
            assert_eq!(AcceptMode::from_u8(m.as_u8()), m);
        }
        assert_eq!(AcceptMode::from_u8(99), AcceptMode::Strict);
    }

    #[test]
    fn error_policy_from_u8_roundtrip() {
        for p in [ErrorPolicy::Pass, ErrorPolicy::FailClosed] {
            assert_eq!(ErrorPolicy::from_u8(p.as_u8()), p);
        }
        assert_eq!(ErrorPolicy::from_u8(99), ErrorPolicy::Pass);
    }

    #[test]
    fn all_profiles_have_consistent_resource_limits() {
        for profile in [
            Profile::StrictCache,
            Profile::Balanced,
            Profile::StreamingFirst,
        ] {
            let d = profile.defaults();
            assert_eq!(d.limits_memory_bytes, 8 * 1024 * 1024);
            assert_eq!(d.limits_timeout_ms, 2000);
            assert_eq!(d.limits_max_inflight, 64);
            assert_eq!(d.error_policy, ErrorPolicy::Pass);
            assert!(!d.diagnostics);
        }
    }
}
