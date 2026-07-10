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
//! 5. `streaming_metrics` — streaming-specific counters (when streaming compiled in)
//! 6. `dynconf_state` — dynamic config watcher state
//! 7. `streaming_config` — streaming configuration summary
//! 8. `profile` — profile name as a top-level STRING field
//! 9. `overridden_fields` — top-level array of explicit-override field names
//! 10. `forced_fields` — top-level array of profile-forced field names
//! 11. `effective_config` — resolved effective configuration object
//!
//! # Schema Stability
//!
//! This schema is a v1 DRAFT for 0.9.0. It will be frozen at 1.0.0.
//! After freeze: additive-only changes (new optional fields), no removals.

use std::collections::BTreeMap;

use serde::Serialize;

/// Diagnostics JSON output schema v1.
///
/// This structure mirrors the top-level JSON returned by the
/// `/nginx-markdown/diagnostics` endpoint as rendered by the C code in
/// `ngx_http_markdown_diagnostics.c`.  The `schema_version` field is
/// always `1` for this version.
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
    /// Streaming metrics (when streaming is compiled in).
    pub streaming_metrics: StreamingMetrics,
    /// Dynamic configuration watcher state.
    pub dynconf_state: DynconfState,
    /// Streaming configuration summary.
    pub streaming_config: StreamingConfig,
    /// Active profile name (string, not object — matches C endpoint).
    pub profile: String,
    /// Fields explicitly overridden by the operator (top-level array).
    pub overridden_fields: Vec<String>,
    /// Fields forced by the active profile (top-level array).
    pub forced_fields: Vec<String>,
    /// Resolved effective configuration.
    pub effective_config: EffectiveConfig,
}

/// Configuration snapshot (dynconf snapshot).
pub type ConfigSnapshot = BTreeMap<String, serde_json::Value>;

/// A single recent decision entry from the ring buffer.
#[derive(Debug, Clone, Serialize)]
pub struct RecentDecision {
    /// Timestamp (ms since epoch or monotonic start).
    pub timestamp: i64,
    /// Numeric reason code.
    pub reason_code: i32,
    /// Human-readable reason code string.
    pub reason_code_str: Option<String>,
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
    /// Total overload rejections.
    pub overload_total: u64,
    /// Total backpressure events.
    pub backpressure_total: u64,
}

/// Streaming metrics (when streaming is compiled in).
#[derive(Debug, Clone, Default, Serialize)]
pub struct StreamingMetrics {
    /// Total streaming requests.
    pub requests_total: u64,
    /// Total streaming successes.
    pub succeeded_total: u64,
    /// Total streaming failures.
    pub failed_total: u64,
    /// Total streaming fallbacks.
    pub fallback_total: u64,
    /// Total streaming candidates.
    pub candidate_total: u64,
    /// Total output bytes.
    pub output_bytes_total: u64,
    /// Engine chose streaming.
    pub engine_choice_streaming: u64,
    /// Engine chose full buffer.
    pub engine_choice_full_buffer: u64,
}

/// Dynamic configuration watcher state.
#[derive(Debug, Clone, Default, Serialize)]
pub struct DynconfState {
    /// Active config file mtime.
    pub active_mtime: i64,
    /// Config version counter.
    pub config_version: u64,
    /// Last known good mtime.
    pub last_known_good_mtime: i64,
    /// Whether last known good is valid.
    pub lkg_valid: bool,
}

/// Streaming configuration summary.
#[derive(Debug, Clone, Default, Serialize)]
pub struct StreamingConfig {
    /// Streaming engine (off, on, auto).
    pub engine: String,
    /// Whether the engine came from configuration or the default.
    pub engine_source: String,
    /// Streaming error handling policy (pass or reject).
    pub on_error: String,
    /// Streaming threshold in bytes.
    pub threshold: u64,
    /// Bytes retained before the response is committed downstream.
    pub precommit_buffer: u64,
    /// Minimum output flush size in bytes.
    pub flush_min: u64,
    /// Whether the threshold was explicitly configured.
    pub threshold_explicit: bool,
}

/// Resolved effective configuration.
#[derive(Debug, Clone, Default, Serialize)]
pub struct EffectiveConfig {
    /// Accept policy (strict, wildcard, force).
    pub accept: String,
    /// Cache validation mode (off, ims_only, full).
    pub cache_validation: String,
    /// Streaming policy.
    pub streaming: String,
    /// Memory limit in bytes.
    pub limits_memory_bytes: u64,
    /// Timeout in milliseconds.
    pub limits_timeout_ms: u64,
    /// Streaming buffer in bytes.
    pub limits_streaming_buffer_bytes: u64,
    /// Max inflight.
    pub limits_max_inflight: u64,
    /// Error policy (pass, fail_closed, status).
    pub error_policy: String,
}

impl DiagnosticsSchema {
    /// Create a new schema with default values and `schema_version = 1`.
    pub fn new() -> Self {
        Self {
            schema_version: 1,
            config_snapshot: ConfigSnapshot::default(),
            recent_decisions: Vec::new(),
            metrics_snapshot: MetricsSnapshot::default(),
            streaming_metrics: StreamingMetrics::default(),
            dynconf_state: DynconfState::default(),
            streaming_config: StreamingConfig::default(),
            profile: String::new(),
            overridden_fields: Vec::new(),
            forced_fields: Vec::new(),
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
        assert!(obj.contains_key("streaming_metrics"));
        assert!(obj.contains_key("dynconf_state"));
        assert!(obj.contains_key("streaming_config"));
        assert!(obj.contains_key("profile"));
        assert!(obj.contains_key("overridden_fields"));
        assert!(obj.contains_key("forced_fields"));
        assert!(obj.contains_key("effective_config"));
    }

    #[test]
    fn test_profile_is_string_not_object() {
        let schema = DiagnosticsSchema::default();
        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");
        // C endpoint emits profile as a string, not an object
        assert!(
            parsed["profile"].is_string(),
            "profile should be a string (matches C endpoint)"
        );
        // overridden_fields and forced_fields are top-level arrays
        assert!(parsed["overridden_fields"].is_array());
        assert!(parsed["forced_fields"].is_array());
    }

    #[test]
    fn test_metrics_snapshot_has_overload_and_backpressure() {
        let schema = DiagnosticsSchema::default();
        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");
        let ms = &parsed["metrics_snapshot"];
        assert!(ms["overload_total"].is_u64());
        assert!(ms["backpressure_total"].is_u64());
    }

    #[test]
    fn test_streaming_metrics_has_all_fields() {
        let schema = DiagnosticsSchema::default();
        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");
        let sm = &parsed["streaming_metrics"];
        assert!(sm["requests_total"].is_u64());
        assert!(sm["succeeded_total"].is_u64());
        assert!(sm["failed_total"].is_u64());
        assert!(sm["fallback_total"].is_u64());
        assert!(sm["candidate_total"].is_u64());
        assert!(sm["output_bytes_total"].is_u64());
        assert!(sm["engine_choice_streaming"].is_u64());
        assert!(sm["engine_choice_full_buffer"].is_u64());
    }

    #[test]
    fn test_effective_config_has_correct_field_names() {
        let schema = DiagnosticsSchema::default();
        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");
        let ec = &parsed["effective_config"];
        // C endpoint uses limits_memory_bytes, limits_timeout_ms etc.
        assert!(ec["limits_memory_bytes"].is_u64());
        assert!(ec["limits_timeout_ms"].is_u64());
        assert!(ec["limits_streaming_buffer_bytes"].is_u64());
        assert!(ec["limits_max_inflight"].is_u64());
        assert!(ec["error_policy"].is_string());
    }

    #[test]
    fn test_streaming_config_matches_c_endpoint_shape() {
        let parsed: serde_json::Value = serde_json::from_str(
            &DiagnosticsSchema::default()
                .to_json()
                .expect("serialization should succeed"),
        )
        .expect("output should be valid JSON");
        let sc = parsed["streaming_config"]
            .as_object()
            .expect("streaming_config should be an object");

        let expected = [
            "engine",
            "engine_source",
            "on_error",
            "threshold",
            "precommit_buffer",
            "flush_min",
            "threshold_explicit",
        ];
        assert_eq!(sc.len(), expected.len());
        for key in expected {
            assert!(sc.contains_key(key), "missing C endpoint key: {key}");
        }
        assert!(sc["engine"].is_string());
        assert!(sc["engine_source"].is_string());
        assert!(sc["on_error"].is_string());
        assert!(sc["threshold"].is_u64());
        assert!(sc["precommit_buffer"].is_u64());
        assert!(sc["flush_min"].is_u64());
        assert!(sc["threshold_explicit"].is_boolean());
    }

    #[test]
    fn test_dynconf_state_mtimes_match_numeric_c_output() {
        let parsed: serde_json::Value = serde_json::from_str(
            &DiagnosticsSchema::default()
                .to_json()
                .expect("serialization should succeed"),
        )
        .expect("output should be valid JSON");
        let state = &parsed["dynconf_state"];
        assert!(state["active_mtime"].is_i64());
        assert!(state["config_version"].is_u64());
        assert!(state["last_known_good_mtime"].is_i64());
        assert!(state["lkg_valid"].is_boolean());
    }

    #[test]
    fn test_unknown_reason_serializes_as_null_like_c_endpoint() {
        let decision = RecentDecision {
            timestamp: 1,
            reason_code: -1,
            reason_code_str: None,
            duration_ms: 2,
        };
        let value = serde_json::to_value(decision).expect("serialization succeeds");
        assert!(value["reason_code_str"].is_null());
    }

    #[test]
    fn test_config_snapshot_accepts_dynconf_keys() {
        let mut schema = DiagnosticsSchema::default();
        schema.config_snapshot.insert(
            "markdown_limits_memory".to_string(),
            serde_json::json!(8_388_608),
        );
        let value: serde_json::Value =
            serde_json::from_str(&schema.to_json().expect("serialization succeeds"))
                .expect("valid JSON");
        assert_eq!(
            value["config_snapshot"]["markdown_limits_memory"],
            8_388_608,
        );
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
        schema.metrics_snapshot.overload_total = 5;
        schema.metrics_snapshot.backpressure_total = 3;
        schema.streaming_metrics.requests_total = 100;
        schema.streaming_metrics.fallback_total = 10;
        schema.profile = "balanced".to_string();
        schema.overridden_fields = vec!["streaming".to_string()];
        schema.forced_fields = vec!["cache_validation".to_string()];
        schema.effective_config.accept = "strict".to_string();
        schema.effective_config.error_policy = "pass".to_string();

        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");

        assert_eq!(parsed["schema_version"], 1);
        assert_eq!(parsed["metrics_snapshot"]["conversions_total"], 42);
        assert_eq!(parsed["metrics_snapshot"]["overload_total"], 5);
        assert_eq!(parsed["streaming_metrics"]["requests_total"], 100);
        assert_eq!(parsed["profile"], "balanced");
        assert_eq!(parsed["overridden_fields"][0], "streaming");
        assert_eq!(parsed["effective_config"]["accept"], "strict");
    }
}
