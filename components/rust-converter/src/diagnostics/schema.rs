//! Diagnostics JSON output schema v1.
//!
//! Defines the top-level [`DiagnosticsSchema`] that mirrors the actual
//! sections emitted by the C diagnostics endpoint renderer
//! (`ngx_http_markdown_diagnostics.c` → `build_json`).
//!
//! The C renderer is the single source of truth for the diagnostics
//! endpoint output.  This Rust schema exists so the FFI export
//! `markdown_get_diagnostics_schema` can return a JSON description of
//! the endpoint contract for tooling and documentation.  Any structural
//! change to the C renderer must be reflected here in the same changeset.
//!
//! # Actual C endpoint sections (in emission order)
//!
//! 1. `schema_version` — always 1
//! 2. `config_snapshot` — dynconf snapshot object (may be empty)
//! 3. `recent_decisions` — array of decision entries
//! 4. `metrics_snapshot` — core metric counters
//! 5. `streaming_metrics` — streaming-specific counters (when streaming
//!    is compiled in)
//! 6. `dynconf_state` — dynamic config watcher state
//! 7. `streaming_config` — streaming configuration summary
//! 8. `profile` — active profile name and forced fields
//! 9. `effective_config` — resolved effective configuration
//!
//! # Schema Stability
//!
//! This schema is a v1 DRAFT for 0.9.0. It will be frozen at 1.0.0.
//! After freeze: additive-only changes (new optional fields), no removals.

use serde::Serialize;

/// Diagnostics JSON output schema v1.
///
/// This structure mirrors the top-level JSON returned by the
/// `/nginx-markdown/diagnostics` endpoint as rendered by the C code in
/// `ngx_http_markdown_diagnostics.c`.  The `schema_version` field is
/// always `1` for this version.
///
/// # Schema Stability
///
/// This schema is a v1 DRAFT for 0.9.0. It will be frozen at 1.0.0.
/// After freeze: additive-only changes (new optional fields), no removals.
#[derive(Debug, Clone, Serialize)]
pub struct DiagnosticsSchema {
    /// Schema version — always 1 for this version.
    pub schema_version: u32,
    /// Configuration snapshot section (dynconf snapshot).
    pub config_snapshot: ConfigSnapshot,
    /// Recent decision entries (ring buffer, newest-first).
    pub recent_decisions: Vec<RecentDecision>,
    /// Core metrics snapshot.
    pub metrics_snapshot: MetricsSnapshot,
    /// Dynamic configuration watcher state.
    pub dynconf_state: DynconfState,
    /// Streaming configuration summary.
    pub streaming_config: StreamingConfig,
    /// Active profile section.
    pub profile: ProfileSection,
    /// Resolved effective configuration.
    pub effective_config: EffectiveConfig,
}

/// Configuration snapshot (dynconf snapshot).
#[derive(Debug, Clone, Default, Serialize)]
pub struct ConfigSnapshot {
    /// Whether dynconf is enabled for this location.
    pub enabled: bool,
    /// Dynconf file path (empty when not configured).
    pub path: String,
}

/// A single recent decision entry from the ring buffer.
#[derive(Debug, Clone, Serialize)]
pub struct RecentDecision {
    /// Timestamp (ms since epoch or monotonic start).
    pub timestamp: i64,
    /// Numeric reason code.
    pub reason_code: i32,
    /// Human-readable reason code string.
    pub reason_code_str: String,
    /// Decision duration in milliseconds.
    pub duration_ms: i64,
}

/// Core metrics snapshot.
#[derive(Debug, Clone, Default, Serialize)]
pub struct MetricsSnapshot {
    /// Total conversions.
    pub conversions_total: u64,
    /// Total deliveries.
    pub delivery_total: u64,
    /// Total requests.
    pub requests_total: u64,
    /// Total fail-open deliveries.
    pub failopen_total: u64,
}

/// Dynamic configuration watcher state.
#[derive(Debug, Clone, Default, Serialize)]
pub struct DynconfState {
    /// Active config file mtime.
    pub active_mtime: String,
    /// Config version counter.
    pub config_version: u64,
    /// Last known good mtime.
    pub last_known_good_mtime: String,
    /// Whether last known good is valid.
    pub lkg_valid: bool,
}

/// Streaming configuration summary.
#[derive(Debug, Clone, Default, Serialize)]
pub struct StreamingConfig {
    /// Streaming policy (off, auto, force).
    pub policy: String,
    /// Streaming engine (off, on, auto).
    pub engine: String,
    /// Streaming threshold in bytes.
    pub threshold: u64,
    /// Streaming buffer budget in bytes.
    pub budget: u64,
    /// Whether streaming shadow mode is enabled.
    pub shadow: bool,
}

/// Active profile section.
#[derive(Debug, Clone, Default, Serialize)]
pub struct ProfileSection {
    /// Profile name (none, strict_cache, balanced, streaming_first).
    pub name: String,
    /// Whether the profile was explicitly set.
    pub explicit: bool,
}

/// Resolved effective configuration.
#[derive(Debug, Clone, Default, Serialize)]
pub struct EffectiveConfig {
    /// Accept policy (strict, wildcard).
    pub accept: String,
    /// Cache validation mode (off, ims_only, full).
    pub cache_validation: String,
    /// Streaming policy.
    pub streaming: String,
    /// Memory limit in bytes.
    pub limits_memory: u64,
    /// Timeout in milliseconds.
    pub limits_timeout: u64,
    /// Streaming buffer in bytes.
    pub limits_streaming_buffer: u64,
    /// Max inflight.
    pub limits_max_inflight: u32,
    /// Error policy (pass, fail_closed, status).
    pub error_policy: String,
    /// Diagnostics enabled.
    pub diagnostics: bool,
}

impl DiagnosticsSchema {
    /// Create a new schema with default values and `schema_version = 1`.
    pub fn new() -> Self {
        Self {
            schema_version: 1,
            config_snapshot: ConfigSnapshot::default(),
            recent_decisions: Vec::new(),
            metrics_snapshot: MetricsSnapshot::default(),
            dynconf_state: DynconfState::default(),
            streaming_config: StreamingConfig::default(),
            profile: ProfileSection::default(),
            effective_config: EffectiveConfig::default(),
        }
    }

    /// Serialize to JSON string.
    pub fn to_json(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string_pretty(self)
    }
}

impl Default for DiagnosticsSchema {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_schema_version_is_1() {
        let schema = DiagnosticsSchema::new();
        assert_eq!(schema.schema_version, 1);
    }

    #[test]
    fn test_serialize_default() {
        let schema = DiagnosticsSchema::default();
        let json = schema.to_json().expect("serialization should succeed");
        assert!(!json.is_empty());

        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");
        assert!(parsed.is_object());
    }

    #[test]
    fn test_schema_has_all_sections_matching_c_endpoint() {
        let schema = DiagnosticsSchema::default();
        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");
        let obj = parsed.as_object().expect("top-level should be an object");

        // Sections matching the C endpoint renderer output
        assert!(obj.contains_key("schema_version"));
        assert!(obj.contains_key("config_snapshot"));
        assert!(obj.contains_key("recent_decisions"));
        assert!(obj.contains_key("metrics_snapshot"));
        assert!(obj.contains_key("dynconf_state"));
        assert!(obj.contains_key("streaming_config"));
        assert!(obj.contains_key("profile"));
        assert!(obj.contains_key("effective_config"));
    }

    #[test]
    fn test_schema_field_names_snake_case() {
        let schema = DiagnosticsSchema::default();
        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");

        fn check_snake_case(value: &serde_json::Value, path: &str) {
            match value {
                serde_json::Value::Object(map) => {
                    for (key, val) in map {
                        let full_path = if path.is_empty() {
                            key.clone()
                        } else {
                            format!("{path}.{key}")
                        };
                        assert!(
                            key.chars()
                                .all(|c| c.is_ascii_lowercase() || c == '_' || c.is_ascii_digit()),
                            "key '{full_path}' is not snake_case"
                        );
                        check_snake_case(val, &full_path);
                    }
                }
                serde_json::Value::Array(arr) => {
                    for item in arr {
                        check_snake_case(item, path);
                    }
                }
                _ => {}
            }
        }

        check_snake_case(&parsed, "");
    }

    #[test]
    fn test_round_trip() {
        let mut schema = DiagnosticsSchema::new();
        schema.metrics_snapshot.conversions_total = 42;
        schema.metrics_snapshot.delivery_total = 40;
        schema.metrics_snapshot.requests_total = 50;
        schema.metrics_snapshot.failopen_total = 2;
        schema.profile.name = "balanced".to_string();
        schema.profile.explicit = true;
        schema.effective_config.accept = "strict".to_string();
        schema.effective_config.cache_validation = "ims_only".to_string();
        schema.effective_config.streaming = "auto".to_string();

        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");

        assert_eq!(parsed["schema_version"], 1);
        assert_eq!(parsed["metrics_snapshot"]["conversions_total"], 42);
        assert_eq!(parsed["metrics_snapshot"]["delivery_total"], 40);
        assert_eq!(parsed["profile"]["name"], "balanced");
        assert_eq!(parsed["effective_config"]["accept"], "strict");
        assert_eq!(parsed["effective_config"]["cache_validation"], "ims_only");
        assert_eq!(parsed["effective_config"]["streaming"], "auto");
    }
}
