//! Diagnostics JSON output schema v1.
//!
//! Defines the top-level [`DiagnosticsSchema`] and its sub-sections that form
//! the contract for the `/nginx-markdown/diagnostics` endpoint response body.
//!
//! # Schema Stability
//!
//! This schema is a v1 DRAFT for 0.9.0. It will be frozen at 1.0.0.
//! After freeze: additive-only changes (new optional fields), no removals.

use serde::Serialize;

/// Diagnostics JSON output schema v1.
///
/// This is the top-level structure returned by the `/nginx-markdown/diagnostics`
/// endpoint. The `schema_version` field is always `1` for this version.
///
/// # Schema Stability
///
/// This schema is a v1 DRAFT for 0.9.0. It will be frozen at 1.0.0.
/// After freeze: additive-only changes (new optional fields), no removals.
#[derive(Debug, Clone, Serialize)]
pub struct DiagnosticsSchema {
    /// Schema version — always 1 for this version.
    pub schema_version: u32,
    /// Decision diagnostics section.
    pub decision: DecisionDiagnostics,
    /// Inflight guard diagnostics section.
    pub inflight: InflightDiagnostics,
    /// Error diagnostics section.
    pub error: ErrorDiagnostics,
    /// Streaming diagnostics section.
    pub streaming: StreamingDiagnostics,
    /// Conditional request diagnostics section.
    pub conditional: ConditionalDiagnostics,
    /// ETag diagnostics section.
    pub etag: EtagDiagnostics,
}

/// Decision diagnostics — last decision state.
#[derive(Debug, Clone, Default, Serialize)]
pub struct DecisionDiagnostics {
    /// Last reason code string (from `ReasonCode::as_str()`).
    pub last_reason: String,
    /// Active profile name.
    pub profile: String,
    /// Processing path mode (full_buffer or streaming).
    pub path_mode: String,
    /// Cache validation mode (off, ims_only, full).
    pub cache_validation: String,
    /// Total decisions made.
    pub total_decisions: u64,
}

/// Inflight guard diagnostics.
#[derive(Debug, Clone, Default, Serialize)]
pub struct InflightDiagnostics {
    /// Current inflight count.
    pub current: u32,
    /// Configured maximum.
    pub max: u32,
    /// High watermark (peak since last reset).
    pub high_watermark: u32,
    /// Total overload rejections.
    pub overload_total: u64,
}

/// Error diagnostics.
#[derive(Debug, Clone, Default, Serialize)]
pub struct ErrorDiagnostics {
    /// Total errors.
    pub total: u64,
    /// Total failed-open deliveries.
    pub failed_open_total: u64,
    /// Total failed-closed rejections.
    pub failed_closed_total: u64,
    /// Last error reason code string.
    pub last_error_reason: String,
}

/// Streaming diagnostics.
#[derive(Debug, Clone, Default, Serialize)]
pub struct StreamingDiagnostics {
    /// Whether streaming is eligible for this location.
    pub eligible: bool,
    /// If blocked, the reason.
    pub block_reason: String,
    /// Total streaming conversions.
    pub streaming_total: u64,
    /// Total streaming fallbacks.
    pub fallback_total: u64,
}

/// Conditional request diagnostics.
#[derive(Debug, Clone, Default, Serialize)]
pub struct ConditionalDiagnostics {
    /// Last evaluated conditional header type (if-none-match, if-modified-since).
    pub evaluated_header: String,
    /// Last result (not_modified, proceed, skipped).
    pub result: String,
    /// Active cache validation mode.
    pub cache_validation_mode: String,
}

/// ETag diagnostics.
#[derive(Debug, Clone, Default, Serialize)]
pub struct EtagDiagnostics {
    /// ETag generation policy (off, weak, strong).
    pub policy: String,
    /// Whether an ETag was generated for the last conversion.
    pub generated: bool,
    /// If not generated, the reason.
    pub reason: String,
}

impl DiagnosticsSchema {
    /// Create a new schema with default values and `schema_version = 1`.
    pub fn new() -> Self {
        Self {
            schema_version: 1,
            decision: DecisionDiagnostics::default(),
            inflight: InflightDiagnostics::default(),
            error: ErrorDiagnostics::default(),
            streaming: StreamingDiagnostics::default(),
            conditional: ConditionalDiagnostics::default(),
            etag: EtagDiagnostics::default(),
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

        // Verify it parses as valid JSON
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");
        assert!(parsed.is_object());
    }

    #[test]
    fn test_schema_has_all_sections() {
        let schema = DiagnosticsSchema::default();
        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");
        let obj = parsed.as_object().expect("top-level should be an object");

        // All 6 diagnostic sections plus schema_version
        assert!(obj.contains_key("schema_version"));
        assert!(obj.contains_key("decision"));
        assert!(obj.contains_key("inflight"));
        assert!(obj.contains_key("error"));
        assert!(obj.contains_key("streaming"));
        assert!(obj.contains_key("conditional"));
        assert!(obj.contains_key("etag"));
        assert_eq!(obj.len(), 7, "expected 7 top-level keys");
    }

    #[test]
    fn test_schema_field_names_snake_case() {
        let schema = DiagnosticsSchema::default();
        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");

        // Recursively check all keys are snake_case
        fn check_snake_case(value: &serde_json::Value, path: &str) {
            match value {
                serde_json::Value::Object(map) => {
                    for (key, val) in map {
                        let full_path = if path.is_empty() {
                            key.clone()
                        } else {
                            format!("{path}.{key}")
                        };
                        // snake_case: lowercase, may contain underscores and digits
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
    fn test_inflight_diagnostics_default() {
        let inflight = InflightDiagnostics::default();
        assert_eq!(inflight.current, 0);
        assert_eq!(inflight.max, 0);
        assert_eq!(inflight.high_watermark, 0);
        assert_eq!(inflight.overload_total, 0);
    }

    #[test]
    fn test_round_trip() {
        let mut schema = DiagnosticsSchema::new();
        schema.decision.last_reason = "converted".to_string();
        schema.decision.profile = "default".to_string();
        schema.decision.path_mode = "full_buffer".to_string();
        schema.decision.cache_validation = "full".to_string();
        schema.decision.total_decisions = 42;
        schema.inflight.current = 5;
        schema.inflight.max = 100;
        schema.inflight.high_watermark = 12;
        schema.inflight.overload_total = 3;
        schema.error.total = 7;
        schema.error.failed_open_total = 2;
        schema.error.failed_closed_total = 1;
        schema.error.last_error_reason = "decompression_error".to_string();
        schema.streaming.eligible = true;
        schema.streaming.block_reason = String::new();
        schema.streaming.streaming_total = 100;
        schema.streaming.fallback_total = 5;
        schema.conditional.evaluated_header = "if-none-match".to_string();
        schema.conditional.result = "not_modified".to_string();
        schema.conditional.cache_validation_mode = "full".to_string();
        schema.etag.policy = "weak".to_string();
        schema.etag.generated = true;
        schema.etag.reason = String::new();

        let json = schema.to_json().expect("serialization should succeed");
        let parsed: serde_json::Value =
            serde_json::from_str(&json).expect("output should be valid JSON");

        // Verify key fields survived serialization
        assert_eq!(parsed["schema_version"], 1);
        assert_eq!(parsed["decision"]["last_reason"], "converted");
        assert_eq!(parsed["decision"]["total_decisions"], 42);
        assert_eq!(parsed["inflight"]["current"], 5);
        assert_eq!(parsed["inflight"]["high_watermark"], 12);
        assert_eq!(parsed["error"]["total"], 7);
        assert_eq!(parsed["error"]["last_error_reason"], "decompression_error");
        assert_eq!(parsed["streaming"]["eligible"], true);
        assert_eq!(parsed["streaming"]["streaming_total"], 100);
        assert_eq!(parsed["conditional"]["evaluated_header"], "if-none-match");
        assert_eq!(parsed["conditional"]["result"], "not_modified");
        assert_eq!(parsed["etag"]["policy"], "weak");
        assert_eq!(parsed["etag"]["generated"], true);
    }
}
