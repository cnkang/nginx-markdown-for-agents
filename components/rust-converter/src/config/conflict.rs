//! Conflict detection for profile/directive combinations (spec 50, 0.9.0).
//!
//! Runs at `nginx -t` time to detect incompatible configuration before the
//! server starts. Two categories of conflicts are checked:
//!
//! 1. **Profile forced-field conflicts** — an explicit directive contradicts
//!    a value that the active profile *forces*. Always an error.
//! 2. **General conflict rules** — field combinations that are semantically
//!    incompatible regardless of profile. May be error or warning depending
//!    on severity.
//!
//! # Duplicate Profile Detection
//!
//! A duplicate `markdown_profile` directive in the same NGINX context is
//! detected at C-side directive parse time: the handler returns
//! `NGX_CONF_ERROR` when `profile` is already set. The Rust function
//! [`detect_conflicts`] therefore receives at most one profile and does not
//! need to check for duplicates.

use crate::config::merge::{EffectiveConfig, ExplicitConfig};
use crate::config::profile::{ErrorPolicy, ForcedField, Profile};
use crate::decision::conditional::CacheValidation;
use crate::decision::streaming::StreamingPolicy;

/// Severity level for a detected configuration conflict.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConflictLevel {
    /// Hard error — `nginx -t` must fail.
    Error,
    /// Advisory warning — logged but does not block startup.
    Warning,
}

/// A single detected configuration conflict.
///
/// Contains the severity level and a human-readable message describing
/// the incompatibility and suggesting corrective action.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Conflict {
    /// Severity: error blocks startup, warning is advisory.
    pub level: ConflictLevel,
    /// Human-readable description of the conflict.
    pub message: String,
}

impl Conflict {
    /// Create an error-level conflict.
    pub fn error(msg: impl Into<String>) -> Self {
        Self {
            level: ConflictLevel::Error,
            message: msg.into(),
        }
    }

    /// Create a warning-level conflict.
    pub fn warning(msg: impl Into<String>) -> Self {
        Self {
            level: ConflictLevel::Warning,
            message: msg.into(),
        }
    }
}

/// Detect configuration conflicts at `nginx -t` time.
///
/// Checks two categories:
///
/// 1. **Profile forced-field conflicts** — if a profile is active and the
///    user explicitly set a directive that contradicts a forced field, an
///    error is emitted.
/// 2. **General conflict rules** — field-value combinations in the effective
///    config that are semantically incompatible, regardless of how they were
///    set (profile, explicit, or builtin).
///
/// # Arguments
///
/// * `profile` — the active profile, if any. At most one profile can be
///   active per NGINX context (duplicate detection happens at the C parse
///   layer).
/// * `explicit` — the explicitly-set directive values.
/// * `effective` — the fully-resolved effective configuration after merge.
///
/// # Returns
///
/// A vector of conflicts (may be empty if the configuration is clean).
pub fn detect_conflicts(
    profile: Option<Profile>,
    explicit: &ExplicitConfig,
    effective: &EffectiveConfig,
) -> Vec<Conflict> {
    let mut conflicts = Vec::new();

    // --- 1. Profile forced-field conflicts ---
    if let Some(p) = profile {
        for forced in p.forced_fields() {
            match forced {
                ForcedField::Streaming(forced_value) => {
                    if let Some(explicit_value) = explicit.streaming
                        && explicit_value != *forced_value
                    {
                        conflicts.push(Conflict::error(format!(
                            "profile {p:?} forces streaming={forced_value:?}, \
                             but explicit directive sets streaming={explicit_value:?}",
                        )));
                    }
                }
                ForcedField::CacheValidation(forced_value) => {
                    if let Some(explicit_value) = explicit.cache_validation
                        && explicit_value != *forced_value
                    {
                        conflicts.push(Conflict::error(format!(
                            "profile {p:?} forces cache_validation={forced_value:?}, \
                             but explicit directive sets cache_validation={explicit_value:?}",
                        )));
                    }
                }
            }
        }
    }

    // --- 2. General conflict rules ---

    // Rule 1: cache_validation=Full + streaming=Force → Error
    // Full ETag requires the entire response buffered; streaming force
    // cannot generate an ETag.
    if effective.cache_validation == CacheValidation::Full
        && effective.streaming == StreamingPolicy::Force
    {
        conflicts.push(Conflict::error(
            "mutually exclusive: full ETag requires buffered, \
             streaming force cannot generate ETag",
        ));
    }

    // Rule 2: cache_validation=Full + streaming=Auto → Warning
    // Streaming will be blocked at runtime; the operator likely wants
    // ims_only instead.
    if effective.cache_validation == CacheValidation::Full
        && effective.streaming == StreamingPolicy::Auto
    {
        conflicts.push(Conflict::warning(
            "streaming blocked at runtime; consider ims_only",
        ));
    }

    // Rule 3: error_policy=FailClosed + streaming=Force → Warning
    // After streaming commit, errors cannot reliably produce an error
    // status code since bytes are already in flight.
    if effective.error_policy == ErrorPolicy::FailClosed
        && effective.streaming == StreamingPolicy::Force
    {
        conflicts.push(Conflict::warning(
            "fail_closed with streaming force: post-commit errors \
             cannot reliably return error status",
        ));
    }

    // Rule 4: max_inflight == 0 → Error
    // Zero inflight means no conversions can ever execute.
    if effective.limits_max_inflight == 0 {
        conflicts.push(Conflict::error("max_inflight must be > 0"));
    }

    conflicts
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::merge::{EffectiveConfig, ExplicitConfig, merge_config};
    use crate::config::profile::{ErrorPolicy, Profile};
    use crate::decision::conditional::CacheValidation;
    use crate::decision::streaming::StreamingPolicy;

    /// Helper: build effective config from profile + explicit for test
    /// convenience.
    fn effective(profile: Option<Profile>, explicit: &ExplicitConfig) -> EffectiveConfig {
        merge_config(profile, explicit)
    }

    #[test]
    fn no_conflicts_when_no_profile_and_compatible_config() {
        let explicit = ExplicitConfig {
            cache_validation: Some(CacheValidation::ImsOnly),
            streaming: Some(StreamingPolicy::Auto),
            ..ExplicitConfig::default()
        };
        let eff = effective(None, &explicit);
        let conflicts = detect_conflicts(None, &explicit, &eff);
        assert!(conflicts.is_empty());
    }

    #[test]
    fn forced_field_conflict_strict_cache_streaming_auto() {
        // strict_cache forces streaming=Off; explicit sets Auto → Error
        let explicit = ExplicitConfig {
            streaming: Some(StreamingPolicy::Auto),
            ..ExplicitConfig::default()
        };
        let eff = effective(Some(Profile::StrictCache), &explicit);
        let conflicts = detect_conflicts(Some(Profile::StrictCache), &explicit, &eff);
        // Forced-field error present
        let forced_errors: Vec<_> = conflicts
            .iter()
            .filter(|c| c.level == ConflictLevel::Error && c.message.contains("forces"))
            .collect();
        assert_eq!(forced_errors.len(), 1);
        assert!(forced_errors[0].message.contains("streaming"));
    }

    #[test]
    fn forced_field_conflict_streaming_first_cache_validation_full() {
        // streaming_first forces cache_validation=Off; explicit sets Full → Error
        let explicit = ExplicitConfig {
            cache_validation: Some(CacheValidation::Full),
            ..ExplicitConfig::default()
        };
        let eff = effective(Some(Profile::StreamingFirst), &explicit);
        let conflicts = detect_conflicts(Some(Profile::StreamingFirst), &explicit, &eff);
        // Should have forced-field error + possibly general conflict
        let forced_errors: Vec<_> = conflicts
            .iter()
            .filter(|c| c.level == ConflictLevel::Error && c.message.contains("cache_validation"))
            .collect();
        assert_eq!(forced_errors.len(), 1);
    }

    #[test]
    fn forced_field_conflict_streaming_first_streaming_off() {
        // streaming_first forces streaming=Force; explicit sets Off → Error
        let explicit = ExplicitConfig {
            streaming: Some(StreamingPolicy::Off),
            ..ExplicitConfig::default()
        };
        let eff = effective(Some(Profile::StreamingFirst), &explicit);
        let conflicts = detect_conflicts(Some(Profile::StreamingFirst), &explicit, &eff);
        let forced_errors: Vec<_> = conflicts
            .iter()
            .filter(|c| c.level == ConflictLevel::Error && c.message.contains("streaming"))
            .collect();
        assert_eq!(forced_errors.len(), 1);
    }

    #[test]
    fn no_forced_conflict_when_explicit_matches_forced_value() {
        // strict_cache forces streaming=Off; explicit also sets Off → no conflict
        let explicit = ExplicitConfig {
            streaming: Some(StreamingPolicy::Off),
            ..ExplicitConfig::default()
        };
        let eff = effective(Some(Profile::StrictCache), &explicit);
        let conflicts = detect_conflicts(Some(Profile::StrictCache), &explicit, &eff);
        // No forced-field conflicts (may have general conflicts depending on
        // effective values, but not forced-field ones)
        let forced_conflicts: Vec<_> = conflicts
            .iter()
            .filter(|c| c.message.contains("forces"))
            .collect();
        assert!(forced_conflicts.is_empty());
    }

    #[test]
    fn general_full_plus_force_is_error() {
        let explicit = ExplicitConfig {
            cache_validation: Some(CacheValidation::Full),
            streaming: Some(StreamingPolicy::Force),
            ..ExplicitConfig::default()
        };
        let eff = effective(None, &explicit);
        let conflicts = detect_conflicts(None, &explicit, &eff);
        let errors: Vec<_> = conflicts
            .iter()
            .filter(|c| c.level == ConflictLevel::Error && c.message.contains("mutually exclusive"))
            .collect();
        assert_eq!(errors.len(), 1);
    }

    #[test]
    fn general_full_plus_auto_is_warning() {
        // Builtin defaults are cache_validation=Full + streaming=Auto, so
        // this triggers with no explicit config and no profile.
        let explicit = ExplicitConfig::default();
        let eff = effective(None, &explicit);
        let conflicts = detect_conflicts(None, &explicit, &eff);
        let warnings: Vec<_> = conflicts
            .iter()
            .filter(|c| {
                c.level == ConflictLevel::Warning && c.message.contains("streaming blocked")
            })
            .collect();
        assert_eq!(warnings.len(), 1);
    }

    #[test]
    fn general_fail_closed_plus_force_is_warning() {
        let explicit = ExplicitConfig {
            error_policy: Some(ErrorPolicy::FailClosed),
            streaming: Some(StreamingPolicy::Force),
            cache_validation: Some(CacheValidation::Off),
            ..ExplicitConfig::default()
        };
        let eff = effective(None, &explicit);
        let conflicts = detect_conflicts(None, &explicit, &eff);
        let warnings: Vec<_> = conflicts
            .iter()
            .filter(|c| c.level == ConflictLevel::Warning && c.message.contains("fail_closed"))
            .collect();
        assert_eq!(warnings.len(), 1);
    }

    #[test]
    fn general_max_inflight_zero_is_error() {
        let explicit = ExplicitConfig {
            limits_max_inflight: Some(0),
            cache_validation: Some(CacheValidation::ImsOnly),
            ..ExplicitConfig::default()
        };
        let eff = effective(None, &explicit);
        let conflicts = detect_conflicts(None, &explicit, &eff);
        let errors: Vec<_> = conflicts
            .iter()
            .filter(|c| c.level == ConflictLevel::Error && c.message.contains("max_inflight"))
            .collect();
        assert_eq!(errors.len(), 1);
    }

    #[test]
    fn balanced_profile_has_no_forced_conflicts() {
        // Balanced has no forced fields, so explicit overrides never trigger
        // forced-field conflicts.
        let explicit = ExplicitConfig {
            cache_validation: Some(CacheValidation::Full),
            streaming: Some(StreamingPolicy::Force),
            ..ExplicitConfig::default()
        };
        let eff = effective(Some(Profile::Balanced), &explicit);
        let conflicts = detect_conflicts(Some(Profile::Balanced), &explicit, &eff);
        let forced_conflicts: Vec<_> = conflicts
            .iter()
            .filter(|c| c.message.contains("forces"))
            .collect();
        assert!(forced_conflicts.is_empty());
        // General conflicts still apply (Full + Force → error)
        let general_errors: Vec<_> = conflicts
            .iter()
            .filter(|c| c.level == ConflictLevel::Error)
            .collect();
        assert!(!general_errors.is_empty());
    }

    #[test]
    fn multiple_conflicts_detected_simultaneously() {
        // streaming_first + explicit cache_validation=Full + explicit streaming=Off
        // → two forced-field errors + possibly general conflicts
        let explicit = ExplicitConfig {
            cache_validation: Some(CacheValidation::Full),
            streaming: Some(StreamingPolicy::Off),
            ..ExplicitConfig::default()
        };
        let eff = effective(Some(Profile::StreamingFirst), &explicit);
        let conflicts = detect_conflicts(Some(Profile::StreamingFirst), &explicit, &eff);
        // At least 2 forced-field errors (cache_validation + streaming)
        let forced_errors: Vec<_> = conflicts
            .iter()
            .filter(|c| c.level == ConflictLevel::Error && c.message.contains("forces"))
            .collect();
        assert!(forced_errors.len() >= 2);
    }

    #[test]
    fn no_profile_no_general_conflicts_with_balanced_like_config() {
        // ImsOnly + Auto → no general conflicts
        let explicit = ExplicitConfig {
            cache_validation: Some(CacheValidation::ImsOnly),
            streaming: Some(StreamingPolicy::Auto),
            ..ExplicitConfig::default()
        };
        let eff = effective(None, &explicit);
        let conflicts = detect_conflicts(None, &explicit, &eff);
        assert!(conflicts.is_empty());
    }
}
