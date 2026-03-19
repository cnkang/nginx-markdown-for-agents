//! Integration test for Measurement Report completeness.
//!
//! **Validates: Requirements 1.6, 10.1**
//!
//! Property 2 (Report Completeness): The *real* `perf_baseline` binary's
//! JSON output SHALL contain an entry for every Sample_Tier defined in
//! `metrics-schema.json`, and each tier entry SHALL contain a value for
//! every core metric.
//!
//! This test invokes the actual release binary with `--json-output` for
//! each tier individually, then validates the resulting JSON.  It does NOT
//! mirror the report structure in test code — any schema drift in the
//! production binary will be caught.

use std::path::PathBuf;
use std::process::Command;

use serde_json::Value;

// ---------------------------------------------------------------------------
// Constants derived from perf/metrics-schema.json
// ---------------------------------------------------------------------------

/// Canonical sample-tier CLI names accepted by the binary.
const TIER_CLI_NAMES: &[&str] = &["small", "medium", "medium-front-matter", "large-1m"];

/// Legacy `--single` alias that should still map to the canonical `large-1m` tier.
const LEGACY_SINGLE_ALIAS_LARGE: &str = "large";

/// Expected JSON tier keys (tier_key mapping: "large-1m" CLI → "large-1m" key,
/// others are identity).
const EXPECTED_TIER_KEYS: &[&str] = &["small", "medium", "medium-front-matter", "large-1m"];

/// Core per-tier metrics that MUST be present.
const CORE_METRICS: &[&str] = &[
    "p50_ms",
    "p95_ms",
    "p99_ms",
    "peak_memory_bytes",
    "req_per_s",
    "input_mb_per_s",
];

/// Required top-level fields in a Measurement Report.
const TOP_LEVEL_FIELDS: &[&str] = &[
    "schema_version",
    "report_type",
    "timestamp",
    "git_commit",
    "platform",
    "tiers",
];

/// Stage breakdown keys.
const STAGE_KEYS: &[&str] = &["parse_pct", "convert_pct", "etag_pct", "token_pct"];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Finds the repository root by ascending two directory levels from this crate's manifest directory.
/// If either parent does not exist, returns the manifest directory itself.
///
/// # Returns
///
/// PathBuf pointing to the repository root, or the crate manifest directory if traversal fails.
///
/// # Examples
///
/// ```
/// let root = repo_root();
/// assert!(root.exists());
/// ```
fn repo_root() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or(manifest_dir)
}

/// Locate the `perf_baseline` example binary, preferring the release build and falling back to the debug build.
///
/// # Examples
///
/// ```no_run
/// let path = binary_path();
/// println!("{}", path.display());
/// ```
fn binary_path() -> PathBuf {
    let base = std::env::var("CARGO_TARGET_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target"));
    let release = base.join("release/examples/perf_baseline");
    if release.exists() {
        return release;
    }
    let debug = base.join("debug/examples/perf_baseline");
    if debug.exists() {
        return debug;
    }
    panic!(
        "perf_baseline binary not found (searched under {}).\n\
         CARGO_TARGET_DIR={:?}, CARGO_MANIFEST_DIR={}\n\
         Run `cargo build --release --example perf_baseline` first.",
        base.display(),
        std::env::var("CARGO_TARGET_DIR").ok(),
        env!("CARGO_MANIFEST_DIR"),
    );
}

/// Execute the compiled perf_baseline binary for a single tier and return its parsed JSON report.
///
/// The function invokes the perf_baseline binary with `--single <tier>` and `--json-output` pointing
/// to a temporary file, asserts the process succeeds, reads the file, and parses its contents as JSON.
///
/// # Returns
///
/// `serde_json::Value` containing the parsed measurement report for the requested tier.
///
/// # Examples
///
/// ```no_run
/// let report = run_binary_for_tier("small");
/// assert!(report.get("tiers").is_some());
/// ```
fn run_binary_for_tier(tier: &str) -> Value {
    let bin = binary_path();
    let tmp = std::env::temp_dir().join(format!(
        "perf_completeness_{tier}_{}.json",
        std::process::id()
    ));

    let output = Command::new(&bin)
        .arg("--single")
        .arg(tier)
        .arg("--json-output")
        .arg(&tmp)
        .current_dir(repo_root())
        .output()
        .unwrap_or_else(|e| panic!("Failed to run {}: {e}", bin.display()));

    assert!(
        output.status.success(),
        "perf_baseline --single {tier} failed:\nstdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );

    let content = std::fs::read_to_string(&tmp)
        .unwrap_or_else(|e| panic!("Failed to read {}: {e}", tmp.display()));
    serde_json::from_str(&content)
        .unwrap_or_else(|e| panic!("Invalid JSON in {}: {e}", tmp.display()))
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

/// Validates that a Measurement Report JSON produced for each tier contains the required
/// top-level fields, the expected tier key, all core metrics as numeric values, a
/// stage_breakdown object with the expected keys, and non-empty `timestamp` and `platform` strings.
///
/// # Examples
///
/// ```
/// // Run this integration test via `cargo test --test perf_report_completeness`.
/// ```
#[test]
fn report_contains_all_tiers_and_core_metrics() {
    for (cli_name, expected_key) in TIER_CLI_NAMES.iter().zip(EXPECTED_TIER_KEYS.iter()) {
        let report = run_binary_for_tier(cli_name);

        // 1. Top-level fields exist.
        for &field in TOP_LEVEL_FIELDS {
            assert!(
                report.get(field).is_some(),
                "[{cli_name}] Missing top-level field: {field}"
            );
        }

        // 2. schema_version and report_type.
        assert_eq!(
            report["schema_version"].as_str().unwrap(),
            "1.0.0",
            "[{cli_name}] Wrong schema_version"
        );
        assert_eq!(
            report["report_type"].as_str().unwrap(),
            "measurement",
            "[{cli_name}] Wrong report_type"
        );

        // 3. The expected tier key is present.
        let tiers_obj = report["tiers"]
            .as_object()
            .unwrap_or_else(|| panic!("[{cli_name}] tiers is not a JSON object"));
        assert!(
            tiers_obj.contains_key(*expected_key),
            "[{cli_name}] Missing tier key: {expected_key}. Found: {:?}",
            tiers_obj.keys().collect::<Vec<_>>()
        );

        // 4. Core metrics present and numeric.
        let tier = &tiers_obj[*expected_key];
        for &metric in CORE_METRICS {
            let val = tier.get(metric);
            assert!(
                val.is_some(),
                "[{cli_name}] Tier '{expected_key}' missing metric: {metric}"
            );
            assert!(
                val.unwrap().is_number(),
                "[{cli_name}] Tier '{expected_key}' metric '{metric}' is not a number"
            );
        }

        // 5. stage_breakdown with expected keys.
        let breakdown = tier.get("stage_breakdown").unwrap_or_else(|| {
            panic!("[{cli_name}] Tier '{expected_key}' missing stage_breakdown")
        });
        let breakdown_obj = breakdown
            .as_object()
            .unwrap_or_else(|| panic!("[{cli_name}] stage_breakdown is not a JSON object"));
        for &sk in STAGE_KEYS {
            assert!(
                breakdown_obj.contains_key(sk),
                "[{cli_name}] stage_breakdown missing key: {sk}"
            );
        }

        // 6. Non-empty string fields.
        for &field in &["timestamp", "platform"] {
            let val = report[field].as_str().unwrap_or("");
            assert!(
                !val.is_empty(),
                "[{cli_name}] Top-level field '{field}' should be a non-empty string"
            );
        }
    }
}

/// Validate that a full run (no `--single`) produces a single report
/// containing ALL expected tier keys simultaneously.
///
/// This guards against regressions where the full-run code path in `main()`
/// silently drops a tier — something the per-tier `--single` test above
/// would not catch.
#[test]
fn full_run_report_contains_all_tiers() {
    let bin = binary_path();
    let tmp = std::env::temp_dir().join(format!(
        "perf_completeness_full_run_{}.json",
        std::process::id()
    ));

    let output = Command::new(&bin)
        .arg("--json-output")
        .arg(&tmp)
        .current_dir(repo_root())
        .output()
        .unwrap_or_else(|e| panic!("Failed to run {}: {e}", bin.display()));

    assert!(
        output.status.success(),
        "perf_baseline (full run) failed:\nstdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );

    let content = std::fs::read_to_string(&tmp)
        .unwrap_or_else(|e| panic!("Failed to read {}: {e}", tmp.display()));
    let report: serde_json::Value = serde_json::from_str(&content)
        .unwrap_or_else(|e| panic!("Invalid JSON in {}: {e}", tmp.display()));

    // Top-level structure.
    for &field in TOP_LEVEL_FIELDS {
        assert!(
            report.get(field).is_some(),
            "[full-run] Missing top-level field: {field}"
        );
    }

    let tiers_obj = report["tiers"]
        .as_object()
        .expect("[full-run] tiers is not a JSON object");

    // ALL expected tier keys must be present in the same report.
    for &expected_key in EXPECTED_TIER_KEYS {
        assert!(
            tiers_obj.contains_key(expected_key),
            "[full-run] Missing tier key: {expected_key}. Found: {:?}",
            tiers_obj.keys().collect::<Vec<_>>()
        );

        let tier = &tiers_obj[expected_key];

        // Core metrics present and numeric.
        for &metric in CORE_METRICS {
            let val = tier.get(metric);
            assert!(
                val.is_some(),
                "[full-run] Tier '{expected_key}' missing metric: {metric}"
            );
            assert!(
                val.unwrap().is_number(),
                "[full-run] Tier '{expected_key}' metric '{metric}' is not a number"
            );
        }

        // Stage breakdown present with expected keys.
        let breakdown = tier
            .get("stage_breakdown")
            .unwrap_or_else(|| panic!("[full-run] Tier '{expected_key}' missing stage_breakdown"));
        let breakdown_obj = breakdown.as_object().unwrap_or_else(|| {
            panic!("[full-run] Tier '{expected_key}' stage_breakdown is not a JSON object")
        });
        for &sk in STAGE_KEYS {
            assert!(
                breakdown_obj.contains_key(sk),
                "[full-run] Tier '{expected_key}' stage_breakdown missing key: {sk}"
            );
        }
    }

    // Exact tier count — no extra unexpected tiers.
    assert_eq!(
        tiers_obj.len(),
        EXPECTED_TIER_KEYS.len(),
        "[full-run] Expected {} tiers, found {}. Keys: {:?}",
        EXPECTED_TIER_KEYS.len(),
        tiers_obj.len(),
        tiers_obj.keys().collect::<Vec<_>>()
    );
}

/// Verifies that using the legacy CLI alias `"large"` produces a report that exposes the canonical `"large-1m"` tier and not the legacy key.
///
/// Confirms the report's `tiers` object contains `"large-1m"`, does not contain `"large"`, and that the `"large-1m"` tier includes all required core metrics as numeric values.
///
/// # Examples
///
/// ```
/// // Runs the perf_baseline binary for the legacy alias and asserts the canonical tier is present.
/// let report = run_binary_for_tier("large");
/// let tiers = report["tiers"].as_object().unwrap();
/// assert!(tiers.contains_key("large-1m"));
/// assert!(!tiers.contains_key("large"));
/// ```
#[test]
fn legacy_single_large_alias_maps_to_large_1m_tier() {
    let report = run_binary_for_tier(LEGACY_SINGLE_ALIAS_LARGE);
    let tiers_obj = report["tiers"]
        .as_object()
        .expect("[legacy-large] tiers is not a JSON object");

    assert!(
        tiers_obj.contains_key("large-1m"),
        "[legacy-large] Missing canonical tier key 'large-1m'. Found: {:?}",
        tiers_obj.keys().collect::<Vec<_>>()
    );
    assert!(
        !tiers_obj.contains_key(LEGACY_SINGLE_ALIAS_LARGE),
        "[legacy-large] Report should not expose the legacy tier key 'large'"
    );

    let large_tier = &tiers_obj["large-1m"];
    for &metric in CORE_METRICS {
        let value = large_tier.get(metric);
        assert!(
            value.is_some(),
            "[legacy-large] Tier 'large-1m' missing metric: {metric}"
        );
        assert!(
            value.unwrap().is_number(),
            "[legacy-large] Tier 'large-1m' metric '{metric}' is not a number"
        );
    }
}
