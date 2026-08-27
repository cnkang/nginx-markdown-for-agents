//! Known differences registry between full-buffer and streaming conversion outputs.
//!
//! Provides data structures and TOML parsing for cataloging acceptable output
//! divergences between the full-buffer and streaming HTML-to-Markdown conversion
//! paths. Each known difference is classified by drift type (whitespace, entity
//! encoding, structural, etc.) and severity (benign, acceptable divergence, or
//! needs investigation). This module is shared across streaming test files to
//! determine whether an observed mismatch is a pre-approved known difference
//! or an unexpected regression.

#![allow(dead_code)]

use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Default)]
pub struct KnownDifferences {
    entries: Vec<KnownDifference>,
}

#[derive(Debug, Clone, Default)]
pub struct KnownDifference {
    pub id: String,
    pub description: String,
    pub trigger: String,
    pub reason: String,
    pub drift_type: DriftType,
    pub severity: DriftSeverity,
    pub acceptable: bool,
    pub fix_version: String,
    pub fixture_contains: Option<String>,
    pub full_buffer_snippet: Option<String>,
    pub streaming_snippet: Option<String>,
    pub diff_contains: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DriftType {
    WhitespaceOnly,
    OrderedListNumbering,
    EntityEncoding,
    Structural,
    Semantic,
    #[default]
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DriftSeverity {
    Benign,
    #[default]
    AcceptableDivergence,
    NeedsInvestigation,
}

#[derive(Debug, Clone)]
pub struct OutputDifference<'a> {
    pub full_buffer: &'a str,
    pub streaming: &'a str,
    pub diff: &'a str,
}

impl KnownDifferences {
    pub fn from_file(path: &Path) -> Result<Self, String> {
        if !path.exists() {
            return Ok(Self::default());
        }

        let raw = fs::read_to_string(path)
            .map_err(|err| format!("read known-differences file {}: {err}", path.display()))?;

        if raw.trim().is_empty() {
            return Ok(Self::default());
        }

        let table = raw
            .parse::<toml::Table>()
            .map_err(|err| format!("parse known-differences TOML {}: {err}", path.display()))?;

        let mut entries = Vec::new();
        if let Some(diff_array) = table.get("difference").and_then(toml::Value::as_array) {
            for table in diff_array {
                if let Some(table) = table.as_table() {
                    entries.push(KnownDifference {
                        id: string_field(table, "id"),
                        description: string_field(table, "description"),
                        trigger: string_field(table, "trigger"),
                        reason: string_field(table, "reason"),
                        drift_type: parse_drift_type(
                            table.get("drift_type").and_then(toml::Value::as_str),
                        ),
                        severity: parse_drift_severity(
                            table.get("severity").and_then(toml::Value::as_str),
                        ),
                        acceptable: table
                            .get("acceptable")
                            .and_then(toml::Value::as_bool)
                            .unwrap_or(false),
                        fix_version: string_field(table, "fix_version"),
                        fixture_contains: optional_string_field(table, "fixture_contains"),
                        full_buffer_snippet: optional_string_field(table, "full_buffer_snippet"),
                        streaming_snippet: optional_string_field(table, "streaming_snippet"),
                        diff_contains: optional_string_field(table, "diff_contains"),
                    });
                }
            }
        }

        Ok(Self { entries })
    }

    pub fn entries(&self) -> &[KnownDifference] {
        &self.entries
    }

    pub fn matches<'a>(
        &'a self,
        fixture_name: &str,
        output: &OutputDifference<'_>,
    ) -> Option<&'a KnownDifference> {
        self.entries
            .iter()
            .find(|entry| entry.accepts(fixture_name, output))
    }
}

impl KnownDifference {
    /// Whether every constraint of this entry accepts the fixture/output pair.
    ///
    /// Only acceptable entries participate, and each optional snippet
    /// constraint must pass for its respective engine-output view.
    fn accepts(&self, fixture_name: &str, output: &OutputDifference<'_>) -> bool {
        self.acceptable
            && self.allows_fixture(fixture_name)
            && self.allows_trigger(output)
            && self.allows_full_buffer_snippet(output)
            && self.allows_streaming_snippet(output)
            && self.allows_diff_snippet(output)
    }

    /// Optional fixture-name substring constraint.
    fn allows_fixture(&self, fixture_name: &str) -> bool {
        self.fixture_contains
            .as_deref()
            .is_none_or(|needle| fixture_name.contains(needle))
    }

    /// Trigger-snippet constraint checked against all engine-output views.
    fn allows_trigger(&self, output: &OutputDifference<'_>) -> bool {
        self.trigger.is_empty()
            || output.diff.contains(&self.trigger)
            || output.full_buffer.contains(&self.trigger)
            || output.streaming.contains(&self.trigger)
    }

    /// Optional full-buffer snippet constraint.
    fn allows_full_buffer_snippet(&self, output: &OutputDifference<'_>) -> bool {
        self.full_buffer_snippet
            .as_deref()
            .is_none_or(|needle| output.full_buffer.contains(needle))
    }

    /// Optional streaming snippet constraint.
    fn allows_streaming_snippet(&self, output: &OutputDifference<'_>) -> bool {
        self.streaming_snippet
            .as_deref()
            .is_none_or(|needle| output.streaming.contains(needle))
    }

    /// Optional diff snippet constraint.
    fn allows_diff_snippet(&self, output: &OutputDifference<'_>) -> bool {
        self.diff_contains
            .as_deref()
            .is_none_or(|needle| output.diff.contains(needle))
    }
}

fn string_field(table: &toml::map::Map<String, toml::Value>, key: &str) -> String {
    table
        .get(key)
        .and_then(toml::Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn optional_string_field(table: &toml::map::Map<String, toml::Value>, key: &str) -> Option<String> {
    table
        .get(key)
        .and_then(toml::Value::as_str)
        .map(ToOwned::to_owned)
}

fn parse_drift_type(raw: Option<&str>) -> DriftType {
    match raw.unwrap_or_default() {
        "whitespace_only" => DriftType::WhitespaceOnly,
        "ordered_list_numbering" => DriftType::OrderedListNumbering,
        "entity_encoding" => DriftType::EntityEncoding,
        "structural" => DriftType::Structural,
        "semantic" => DriftType::Semantic,
        _ => DriftType::Unknown,
    }
}

fn parse_drift_severity(raw: Option<&str>) -> DriftSeverity {
    match raw.unwrap_or_default() {
        "benign" => DriftSeverity::Benign,
        "needs_investigation" => DriftSeverity::NeedsInvestigation,
        _ => DriftSeverity::AcceptableDivergence,
    }
}

/// Verifies that the known-differences matcher correctly identifies entries by
/// fixture name and snippet patterns, and rejects non-matching fixtures.
#[test]
fn known_differences_matches_by_fixture_and_snippet() {
    let known = KnownDifferences {
        entries: vec![KnownDifference {
            id: "DIFF-TEST".to_string(),
            description: "test".to_string(),
            trigger: "collapse".to_string(),
            reason: "reason".to_string(),
            drift_type: DriftType::WhitespaceOnly,
            severity: DriftSeverity::Benign,
            acceptable: true,
            fix_version: "0.6.0".to_string(),
            fixture_contains: Some("streaming".to_string()),
            full_buffer_snippet: Some("A  B".to_string()),
            streaming_snippet: Some("A B".to_string()),
            diff_contains: Some("collapse".to_string()),
        }],
    };

    let out = OutputDifference {
        full_buffer: "A  B",
        streaming: "A B",
        diff: "collapse",
    };

    assert!(known.matches("streaming/example.html", &out).is_some());
    assert!(known.matches("simple/example.html", &out).is_none());
}

/// Verifies that drift type and severity enum parsers handle all expected
/// string values and default gracefully on unknown values.
#[test]
fn known_differences_parse_structured_fields() {
    assert_eq!(
        parse_drift_type(Some("ordered_list_numbering")),
        DriftType::OrderedListNumbering
    );
    assert_eq!(
        parse_drift_type(Some("unknown_new_value")),
        DriftType::Unknown
    );

    assert_eq!(parse_drift_severity(Some("benign")), DriftSeverity::Benign);
    assert_eq!(
        parse_drift_severity(Some("needs_investigation")),
        DriftSeverity::NeedsInvestigation
    );
    assert_eq!(
        parse_drift_severity(Some("unexpected")),
        DriftSeverity::AcceptableDivergence
    );
}
