//! Merge logic for effective configuration (spec 50, 0.9.0).
//!
//! Implements the three-layer merge order:
//!
//! ```text
//! effective = builtin_defaults
//!           → apply profile defaults (if a profile is active)
//!           → apply explicit directives (user overrides win)
//! ```
//!
//! # Types
//!
//! - [`BuiltinDefaults`] — the Config V2 built-in defaults (used when no
//!   profile is specified; also the starting point for all merges).
//! - [`ExplicitConfig`] — Option-wrapped fields representing explicitly-set
//!   directive values. `None` means "not explicitly set by user".
//! - [`EffectiveConfig`] — the fully-resolved configuration with all fields
//!   concrete (no Options). This is what the runtime uses.
//!
//! # No-profile behavior
//!
//! When no profile is specified, the built-in Config V2 defaults apply
//! directly (0.9.0 breaking release; no fallback to 0.8.x behavior per
//! spec 45).

use crate::config::profile::{AcceptMode, ErrorPolicy, Profile, ProfileDefaults};
use crate::decision::conditional::CacheValidation;
use crate::decision::streaming::StreamingPolicy;

/// Config V2 built-in defaults (spec 45).
///
/// These are the defaults when no profile is active and no explicit
/// directives are set. They match the values documented in the profile
/// inventory (section 4.2, "Built-in (no profile)" column).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BuiltinDefaults {
    /// Accept negotiation: `strict`.
    pub accept: AcceptMode,
    /// Cache validation: `ims_only`.
    pub cache_validation: CacheValidation,
    /// Streaming policy: `auto`.
    pub streaming: StreamingPolicy,
    /// Memory limit: 10 MiB.
    pub limits_memory_bytes: u64,
    /// Timeout: 5000 ms.
    pub limits_timeout_ms: u64,
    /// Streaming buffer: 2 MiB.
    pub limits_streaming_buffer_bytes: u64,
    /// Max inflight: 64.
    pub limits_max_inflight: u32,
    /// Error policy: `pass`.
    pub error_policy: ErrorPolicy,
    /// Diagnostics: off.
    pub diagnostics: bool,
}

impl Default for BuiltinDefaults {
    fn default() -> Self {
        Self::new()
    }
}

impl BuiltinDefaults {
    /// Construct the canonical built-in defaults.
    pub const fn new() -> Self {
        Self {
            accept: AcceptMode::Strict,
            cache_validation: CacheValidation::ImsOnly,
            streaming: StreamingPolicy::Auto,
            limits_memory_bytes: 10 * 1024 * 1024, // 10 MiB
            limits_timeout_ms: 5000,               // 5 seconds
            limits_streaming_buffer_bytes: 2 * 1024 * 1024, // 2 MiB
            limits_max_inflight: 64,
            error_policy: ErrorPolicy::Pass,
            diagnostics: false,
        }
    }
}

/// Explicit user-specified directive values.
///
/// Each field is `Option<T>` — `None` means the user did not explicitly
/// set this directive. Only `Some` fields participate in the final merge
/// (they override profile defaults or built-in defaults).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ExplicitConfig {
    /// `markdown_accept` directive value.
    pub accept: Option<AcceptMode>,
    /// `markdown_cache_validation` directive value.
    pub cache_validation: Option<CacheValidation>,
    /// `markdown_streaming` directive value.
    pub streaming: Option<StreamingPolicy>,
    /// `markdown_limits memory=` value in bytes.
    pub limits_memory_bytes: Option<u64>,
    /// `markdown_limits timeout=` value in milliseconds.
    pub limits_timeout_ms: Option<u64>,
    /// `markdown_limits streaming_buffer=` value in bytes.
    pub limits_streaming_buffer_bytes: Option<u64>,
    /// `markdown_limits max_inflight=` value.
    pub limits_max_inflight: Option<u32>,
    /// `markdown_error_policy` directive value.
    pub error_policy: Option<ErrorPolicy>,
    /// `markdown_diagnostics` directive value.
    pub diagnostics: Option<bool>,
}

/// Fully-resolved effective configuration.
///
/// All fields are concrete — no Options. This struct is the runtime
/// configuration after the three-layer merge is complete.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EffectiveConfig {
    /// Effective accept negotiation mode.
    pub accept: AcceptMode,
    /// Effective cache-validation mode.
    pub cache_validation: CacheValidation,
    /// Effective streaming policy.
    pub streaming: StreamingPolicy,
    /// Effective memory limit in bytes.
    pub limits_memory_bytes: u64,
    /// Effective timeout in milliseconds.
    pub limits_timeout_ms: u64,
    /// Effective streaming buffer size in bytes.
    pub limits_streaming_buffer_bytes: u64,
    /// Effective max inflight conversions.
    pub limits_max_inflight: u32,
    /// Effective error policy.
    pub error_policy: ErrorPolicy,
    /// Effective diagnostics enabled state.
    pub diagnostics: bool,
}

impl From<&BuiltinDefaults> for EffectiveConfig {
    fn from(b: &BuiltinDefaults) -> Self {
        Self {
            accept: b.accept,
            cache_validation: b.cache_validation,
            streaming: b.streaming,
            limits_memory_bytes: b.limits_memory_bytes,
            limits_timeout_ms: b.limits_timeout_ms,
            limits_streaming_buffer_bytes: b.limits_streaming_buffer_bytes,
            limits_max_inflight: b.limits_max_inflight,
            error_policy: b.error_policy,
            diagnostics: b.diagnostics,
        }
    }
}

impl EffectiveConfig {
    /// Apply profile defaults, overriding the current (built-in) values.
    ///
    /// All profile-defined fields are applied. The streaming buffer uses
    /// `0` when the profile sets `None` (streaming off → buffer irrelevant).
    pub fn apply_profile_defaults(&mut self, defaults: &ProfileDefaults) {
        self.accept = defaults.accept;
        self.cache_validation = defaults.cache_validation;
        self.streaming = defaults.streaming;
        self.limits_memory_bytes = defaults.limits_memory_bytes;
        self.limits_timeout_ms = defaults.limits_timeout_ms;
        self.limits_streaming_buffer_bytes = defaults.limits_streaming_buffer_bytes.unwrap_or(0);
        self.limits_max_inflight = defaults.limits_max_inflight;
        self.error_policy = defaults.error_policy;
        self.diagnostics = defaults.diagnostics;
    }

    /// Apply explicit user overrides. Only `Some` fields are written;
    /// `None` fields leave the current value unchanged.
    pub fn apply_explicit(&mut self, explicit: &ExplicitConfig) {
        if let Some(v) = explicit.accept {
            self.accept = v;
        }
        if let Some(v) = explicit.cache_validation {
            self.cache_validation = v;
        }
        if let Some(v) = explicit.streaming {
            self.streaming = v;
        }
        if let Some(v) = explicit.limits_memory_bytes {
            self.limits_memory_bytes = v;
        }
        if let Some(v) = explicit.limits_timeout_ms {
            self.limits_timeout_ms = v;
        }
        if let Some(v) = explicit.limits_streaming_buffer_bytes {
            self.limits_streaming_buffer_bytes = v;
        }
        if let Some(v) = explicit.limits_max_inflight {
            self.limits_max_inflight = v;
        }
        if let Some(v) = explicit.error_policy {
            self.error_policy = v;
        }
        if let Some(v) = explicit.diagnostics {
            self.diagnostics = v;
        }
    }
}

/// Compute the effective configuration by applying the three-layer merge.
///
/// Merge order: `builtin → profile defaults → explicit overrides`.
///
/// When `profile` is `None`, the built-in Config V2 defaults apply directly
/// (no profile layer). This is the 0.9.0 behavior when no `markdown_profile`
/// directive is present.
///
/// # Arguments
///
/// * `profile` — active profile (if any)
/// * `explicit` — explicitly-set user directives
///
/// # Returns
///
/// The fully-resolved [`EffectiveConfig`].
pub fn merge_config(profile: Option<Profile>, explicit: &ExplicitConfig) -> EffectiveConfig {
    let builtin = BuiltinDefaults::new();
    let mut effective = EffectiveConfig::from(&builtin);

    if let Some(p) = profile {
        let profile_defaults = p.defaults();
        effective.apply_profile_defaults(&profile_defaults);
    }

    effective.apply_explicit(explicit);
    effective
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn no_profile_no_explicit_uses_builtins() {
        let eff = merge_config(None, &ExplicitConfig::default());
        let builtin = BuiltinDefaults::new();
        assert_eq!(eff.accept, builtin.accept);
        assert_eq!(eff.cache_validation, builtin.cache_validation);
        assert_eq!(eff.streaming, builtin.streaming);
        assert_eq!(eff.limits_memory_bytes, builtin.limits_memory_bytes);
        assert_eq!(eff.limits_timeout_ms, builtin.limits_timeout_ms);
        assert_eq!(
            eff.limits_streaming_buffer_bytes,
            builtin.limits_streaming_buffer_bytes
        );
        assert_eq!(eff.limits_max_inflight, builtin.limits_max_inflight);
        assert_eq!(eff.error_policy, builtin.error_policy);
        assert_eq!(eff.diagnostics, builtin.diagnostics);
    }

    #[test]
    fn profile_overrides_builtins() {
        let eff = merge_config(Some(Profile::StrictCache), &ExplicitConfig::default());
        // strict_cache changes cache_validation to Full and streaming to Off
        assert_eq!(eff.cache_validation, CacheValidation::Full);
        assert_eq!(eff.streaming, StreamingPolicy::Off);
        // Memory is 8m (profile) not 10m (builtin)
        assert_eq!(eff.limits_memory_bytes, 8 * 1024 * 1024);
        // Timeout is 2s (profile) not 5s (builtin)
        assert_eq!(eff.limits_timeout_ms, 2000);
    }

    #[test]
    fn explicit_overrides_profile() {
        let explicit = ExplicitConfig {
            limits_memory_bytes: Some(16 * 1024 * 1024),
            diagnostics: Some(true),
            ..ExplicitConfig::default()
        };
        let eff = merge_config(Some(Profile::Balanced), &explicit);
        // Explicit memory wins over profile's 8m
        assert_eq!(eff.limits_memory_bytes, 16 * 1024 * 1024);
        // Explicit diagnostics wins
        assert!(eff.diagnostics);
        // Non-explicit fields still come from the profile
        assert_eq!(eff.cache_validation, CacheValidation::ImsOnly);
        assert_eq!(eff.streaming, StreamingPolicy::Auto);
    }

    #[test]
    fn explicit_overrides_builtin_without_profile() {
        let explicit = ExplicitConfig {
            accept: Some(AcceptMode::Wildcard),
            limits_timeout_ms: Some(10_000),
            ..ExplicitConfig::default()
        };
        let eff = merge_config(None, &explicit);
        assert_eq!(eff.accept, AcceptMode::Wildcard);
        assert_eq!(eff.limits_timeout_ms, 10_000);
        // Non-explicit fields come from builtins
        assert_eq!(eff.cache_validation, CacheValidation::ImsOnly);
        assert_eq!(eff.limits_memory_bytes, 10 * 1024 * 1024);
    }

    #[test]
    fn streaming_first_profile_values() {
        let eff = merge_config(Some(Profile::StreamingFirst), &ExplicitConfig::default());
        assert_eq!(eff.accept, AcceptMode::Wildcard);
        assert_eq!(eff.cache_validation, CacheValidation::Off);
        assert_eq!(eff.streaming, StreamingPolicy::Force);
        assert_eq!(eff.limits_streaming_buffer_bytes, 256 * 1024);
    }

    #[test]
    fn strict_cache_streaming_buffer_is_zero() {
        let eff = merge_config(Some(Profile::StrictCache), &ExplicitConfig::default());
        // streaming is off, so streaming_buffer is set to 0
        assert_eq!(eff.limits_streaming_buffer_bytes, 0);
    }

    #[test]
    fn merge_order_explicit_wins_all() {
        // All explicit fields set — must override everything
        let explicit = ExplicitConfig {
            accept: Some(AcceptMode::Force),
            cache_validation: Some(CacheValidation::Off),
            streaming: Some(StreamingPolicy::Force),
            limits_memory_bytes: Some(1),
            limits_timeout_ms: Some(1),
            limits_streaming_buffer_bytes: Some(1),
            limits_max_inflight: Some(1),
            error_policy: Some(ErrorPolicy::FailClosed),
            diagnostics: Some(true),
        };
        let eff = merge_config(Some(Profile::StrictCache), &explicit);
        assert_eq!(eff.accept, AcceptMode::Force);
        assert_eq!(eff.cache_validation, CacheValidation::Off);
        assert_eq!(eff.streaming, StreamingPolicy::Force);
        assert_eq!(eff.limits_memory_bytes, 1);
        assert_eq!(eff.limits_timeout_ms, 1);
        assert_eq!(eff.limits_streaming_buffer_bytes, 1);
        assert_eq!(eff.limits_max_inflight, 1);
        assert_eq!(eff.error_policy, ErrorPolicy::FailClosed);
        assert!(eff.diagnostics);
    }

    #[test]
    fn builtin_defaults_match_inventory() {
        let b = BuiltinDefaults::new();
        assert_eq!(b.accept, AcceptMode::Strict);
        assert_eq!(b.cache_validation, CacheValidation::ImsOnly);
        assert_eq!(b.streaming, StreamingPolicy::Auto);
        assert_eq!(b.limits_memory_bytes, 10 * 1024 * 1024);
        assert_eq!(b.limits_timeout_ms, 5000);
        assert_eq!(b.limits_streaming_buffer_bytes, 2 * 1024 * 1024);
        assert_eq!(b.limits_max_inflight, 64);
        assert_eq!(b.error_policy, ErrorPolicy::Pass);
        assert!(!b.diagnostics);
    }

    #[test]
    fn effective_config_from_builtin() {
        let b = BuiltinDefaults::new();
        let eff = EffectiveConfig::from(&b);
        assert_eq!(eff.accept, b.accept);
        assert_eq!(eff.cache_validation, b.cache_validation);
        assert_eq!(eff.streaming, b.streaming);
        assert_eq!(eff.limits_memory_bytes, b.limits_memory_bytes);
        assert_eq!(eff.limits_timeout_ms, b.limits_timeout_ms);
        assert_eq!(
            eff.limits_streaming_buffer_bytes,
            b.limits_streaming_buffer_bytes
        );
        assert_eq!(eff.limits_max_inflight, b.limits_max_inflight);
        assert_eq!(eff.error_policy, b.error_policy);
        assert_eq!(eff.diagnostics, b.diagnostics);
    }
}
