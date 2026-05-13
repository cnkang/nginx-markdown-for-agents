//! Artifact directory management and JSON report writing.
//!
//! Responsible for:
//! - creating the artifact directory tree per invocation;
//! - writing `invocation.json` metadata;
//! - appending scenario reports to a JSON report file;
//! - cleaning up artifacts on success unless `--keep-artifacts` is set.

use crate::scenarios::ScenarioReport;
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::Path;

/// Top-level JSON report written when `--json-report` is specified.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct SuiteReport {
    /// Whether the overall suite passed.
    pub suite_passed: bool,
    /// Total number of scenarios executed.
    pub total_scenarios: usize,
    /// Number of passing scenarios.
    pub passed: usize,
    /// Number of failing scenarios.
    pub failed: usize,
    /// Total elapsed wall-clock time in milliseconds.
    pub elapsed_ms: u64,
    /// Per-scenario reports.
    pub scenarios: Vec<ScenarioReport>,
}

impl SuiteReport {
    /// Create an empty suite report.
    pub fn new() -> Self {
        SuiteReport {
            suite_passed: true,
            total_scenarios: 0,
            passed: 0,
            failed: 0,
            elapsed_ms: 0,
            scenarios: Vec::new(),
        }
    }

    /// Add a scenario report and update aggregate counters.
    pub fn add(&mut self, report: ScenarioReport) {
        if report.passed {
            self.passed += 1;
        } else {
            self.failed += 1;
        }
        self.elapsed_ms += report.elapsed_ms;
        self.total_scenarios += 1;
        self.scenarios.push(report);
        self.suite_passed = self.failed == 0;
    }
}

/// Append a scenario report to a JSON report file.
///
/// If the file does not exist, a new `SuiteReport` is created.
/// If the file exists, its current content is parsed, the report is
/// added, and the file is overwritten.
///
/// # Arguments
///
/// * `path` - Destination file path.
/// * `report` - Scenario report to append.
pub fn append_report(path: &Path, report: &ScenarioReport) -> Result<()> {
    let mut suite = if path.exists() {
        let content = std::fs::read_to_string(path)?;
        serde_json::from_str::<SuiteReport>(&content).with_context(|| {
            format!(
                "failed to parse existing suite report JSON at {}",
                path.display()
            )
        })?
    } else {
        SuiteReport::new()
    };
    suite.add(report.clone());
    let json = serde_json::to_string_pretty(&suite)?;
    std::fs::write(path, json)?;
    Ok(())
}

/// Create the artifact directory tree for a scenario invocation.
///
/// # Arguments
///
/// * `artifact_dir` - Root artifact directory.
/// * `scenario_name` - Scenario name for the subdirectory.
///
/// # Returns
///
/// The path to the scenario-specific artifact subdirectory.
pub fn create_artifact_tree(
    artifact_dir: &Path,
    scenario_name: &str,
) -> Result<std::path::PathBuf> {
    let scenario_dir = artifact_dir.join("scenarios").join(scenario_name);
    std::fs::create_dir_all(&scenario_dir)?;
    Ok(scenario_dir)
}

/// Write `invocation.json` metadata to the artifact directory.
///
/// # Arguments
///
/// * `artifact_dir` - Root artifact directory.
/// * `scenario_name` - Scenario name.
/// * `nginx_bin` - Path to the NGINX binary used.
/// * `port` - NGINX listener port.
/// * `upstream_port` - Upstream fixture port.
pub fn write_invocation_json(
    artifact_dir: &Path,
    scenario_name: &str,
    nginx_bin: &Path,
    port: u16,
    upstream_port: u16,
) -> Result<()> {
    #[derive(Serialize)]
    struct Invocation {
        scenario: String,
        nginx_bin: String,
        port: u16,
        upstream_port: u16,
        timestamp: String,
    }

    let invocation = Invocation {
        scenario: scenario_name.to_string(),
        nginx_bin: nginx_bin.to_string_lossy().to_string(),
        port,
        upstream_port,
        timestamp: epoch_secs_string(),
    };

    let json = serde_json::to_string_pretty(&invocation)?;
    let path = artifact_dir.join("invocation.json");
    std::fs::write(path, json)?;
    Ok(())
}

/// Clean up artifacts on success when `--keep-artifacts` is not set.
///
/// Removes the entire artifact directory tree.
///
/// # Arguments
///
/// * `artifact_dir` - Root artifact directory to remove.
/// * `keep_artifacts` - Whether to retain artifacts.
/// * `passed` - Whether the scenario passed.
pub fn cleanup_artifacts(artifact_dir: &Path, keep_artifacts: bool, passed: bool) -> Result<()> {
    if !keep_artifacts && passed && artifact_dir.exists() {
        std::fs::remove_dir_all(artifact_dir)?;
    }
    Ok(())
}

/// Return the current time as epoch seconds (UTC) encoded as a string.
fn epoch_secs_string() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    duration.as_secs().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_suite_report_new() {
        let report = SuiteReport::new();
        assert_eq!(report.total_scenarios, 0);
        assert_eq!(report.passed, 0);
        assert_eq!(report.failed, 0);
        assert!(report.suite_passed);
    }

    #[test]
    fn test_suite_report_add_passing() {
        let mut report = SuiteReport::new();
        let scenario = ScenarioReport::passing("test", vec![], 100);
        report.add(scenario);
        assert_eq!(report.total_scenarios, 1);
        assert_eq!(report.passed, 1);
        assert_eq!(report.failed, 0);
        assert!(report.suite_passed);
    }

    #[test]
    fn test_suite_report_add_failing() {
        let mut report = SuiteReport::new();
        let scenario = ScenarioReport::failing("test", vec![], 50, "assertion failed".to_string());
        report.add(scenario);
        assert_eq!(report.total_scenarios, 1);
        assert_eq!(report.passed, 0);
        assert_eq!(report.failed, 1);
        assert!(!report.suite_passed);
    }

    #[test]
    fn test_append_report_creates_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("report.json");
        let scenario = ScenarioReport::passing("test", vec![], 10);
        append_report(&path, &scenario).unwrap();
        assert!(path.exists());
        let content = std::fs::read_to_string(&path).unwrap();
        let parsed: SuiteReport = serde_json::from_str(&content).unwrap();
        assert_eq!(parsed.total_scenarios, 1);
        assert!(parsed.suite_passed);
    }

    #[test]
    fn test_cleanup_artifacts_on_success_without_keep() {
        let dir = tempfile::tempdir().unwrap();
        let artifact_dir = dir.path().join("artifacts");
        std::fs::create_dir_all(&artifact_dir).unwrap();
        cleanup_artifacts(&artifact_dir, false, true).unwrap();
        assert!(!artifact_dir.exists());
    }

    #[test]
    fn test_cleanup_artifacts_on_failure_always_keeps() {
        let dir = tempfile::tempdir().unwrap();
        let artifact_dir = dir.path().join("artifacts");
        std::fs::create_dir_all(&artifact_dir).unwrap();
        cleanup_artifacts(&artifact_dir, false, false).unwrap();
        assert!(artifact_dir.exists());
    }

    #[test]
    fn test_cleanup_artifacts_keep_flag() {
        let dir = tempfile::tempdir().unwrap();
        let artifact_dir = dir.path().join("artifacts");
        std::fs::create_dir_all(&artifact_dir).unwrap();
        cleanup_artifacts(&artifact_dir, true, true).unwrap();
        assert!(artifact_dir.exists());
    }
}
