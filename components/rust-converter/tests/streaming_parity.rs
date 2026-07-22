//! Streaming vs. full-buffer output parity tests across the HTML fixture corpus.
//!
//! Systematically compares streaming conversion output (both single-chunk and
//! chunked modes) against full-buffer conversion for every HTML fixture in the
//! corpus. Divergences are classified as either known differences (pre-approved
//! in the known-differences TOML) or unexpected regressions. Mismatches are
//! accumulated in-memory and reported by the final assertion at the end of the
//! test run. A separate `#[ignore]` test (`bounded_memory_and_ttfb_evidence_pack`)
//! writes a JSON evidence pack for bounded-memory analysis, but that file does
//! not contain mismatch evidence. This module serves as the primary gate for
//! ensuring streaming conversion maintains output parity with the reference
//! full-buffer implementation.

#![cfg(feature = "streaming")]

#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Instant;

use nginx_markdown_converter::error::ConversionError;
use streaming_test_support::known_differences::KnownDifferences;

use streaming_test_support::{
    check_conversion_errors, check_output_comparison, convert_full_buffer,
    convert_streaming_chunked, convert_streaming_single, default_streaming_budget,
    default_streaming_options, discover_html_fixtures, evidence_output_path, fixture_relative_name,
    known_differences_path, normalize_whitespace_tokens, read_fixture, read_fixture_meta,
};

fn discover_fixtures(corpus_dir: &Path) -> Vec<std::path::PathBuf> {
    discover_html_fixtures(corpus_dir)
}

fn convert_full_buffer_entry(
    html: &[u8],
    content_type: Option<&str>,
) -> Result<String, ConversionError> {
    convert_full_buffer(html, content_type, default_streaming_options())
}

fn convert_streaming_single_entry(
    html: &[u8],
    content_type: Option<&str>,
    budget: nginx_markdown_converter::streaming::MemoryBudget,
) -> Result<String, ConversionError> {
    let run = convert_streaming_single(
        html,
        content_type,
        default_streaming_options(),
        budget,
        None,
    )?;
    Ok(run.markdown)
}

fn convert_streaming_chunked_entry(
    html: &[u8],
    chunk_sizes: &[usize],
    content_type: Option<&str>,
    budget: nginx_markdown_converter::streaming::MemoryBudget,
) -> Result<String, ConversionError> {
    let run = convert_streaming_chunked(
        html,
        chunk_sizes,
        content_type,
        default_streaming_options(),
        budget,
        None,
    )?;
    Ok(run.markdown)
}

fn assert_fixture_parity(path: &Path, known_diffs: &KnownDifferences) -> Result<(), String> {
    let fixture_name = fixture_relative_name(path);
    let meta = read_fixture_meta(path);
    let html = read_fixture(path);
    let content_type = meta.resolved_content_type();

    let full_buffer = convert_full_buffer_entry(&html, Some(&content_type))
        .map_err(|err| format!("{fixture_name}: full-buffer conversion failed: {err}"))?;

    let chunk_sizes = {
        let len = html.len();
        if len <= 3 {
            vec![1; len.max(1)]
        } else {
            vec![len / 3, len / 3, len - (2 * (len / 3))]
        }
    };

    let budget = if fixture_name.starts_with("large/") {
        nginx_markdown_converter::streaming::MemoryBudget {
            total: 256 * 1024 * 1024,
            state_stack: 4 * 1024 * 1024,
            output_buffer: 192 * 1024 * 1024,
            charset_sniff: 64 * 1024,
            lookahead: 4 * 1024 * 1024,
        }
    } else {
        default_streaming_budget()
    };

    let single = convert_streaming_single_entry(&html, Some(&content_type), budget.clone());
    let chunked = convert_streaming_chunked_entry(&html, &chunk_sizes, Some(&content_type), budget);

    let should_compare =
        check_conversion_errors(&fixture_name, &meta, &single, &chunked, known_diffs)?;

    if !should_compare {
        return Ok(());
    }

    let single = single.unwrap();
    let chunked = chunked.unwrap();

    check_output_comparison(
        &fixture_name,
        &full_buffer,
        &single,
        &chunked,
        &meta,
        known_diffs,
    )
}

fn generate_large_fixtures_if_needed() {
    let manifest = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let large_dir = manifest.join("../../tests/corpus/large");
    let has_large = discover_fixtures(&large_dir)
        .iter()
        .any(|path| path.extension().is_some_and(|ext| ext == "html"));

    if has_large {
        return;
    }

    let script = large_dir.join("generate-large-fixtures.sh");
    if !script.exists() {
        return;
    }

    let status = Command::new(&script)
        .status()
        .unwrap_or_else(|err| panic!("run {}: {err}", script.display()));
    assert!(status.success(), "{} failed", script.display());
}

#[test]
fn corpus_driven_differential_harness() {
    let manifest = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let corpus_dir = manifest.join("../../tests/corpus");
    let known = KnownDifferences::from_file(&known_differences_path())
        .unwrap_or_else(|err| panic!("load known differences: {err}"));

    let mut failures = Vec::new();

    for category in [
        "simple",
        "complex",
        "malformed",
        "encoding",
        "edge-cases",
        "streaming",
    ] {
        let dir = corpus_dir.join(category);
        let fixtures = discover_fixtures(&dir);
        assert!(
            !fixtures.is_empty(),
            "category {category} should have at least one fixture"
        );

        for fixture in fixtures {
            if let Err(err) = assert_fixture_parity(&fixture, &known) {
                failures.push(err);
            }
        }
    }

    assert!(
        failures.is_empty(),
        "differential harness failures:\n{}",
        failures.join("\n\n")
    );
}

#[test]
#[ignore = "large fixtures are generated and exercised in nightly/manual runs"]
fn large_fixture_differential_harness() {
    generate_large_fixtures_if_needed();

    let manifest = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let large_dir = manifest.join("../../tests/corpus/large");
    let known = KnownDifferences::from_file(&known_differences_path())
        .unwrap_or_else(|err| panic!("load known differences: {err}"));

    let fixtures = discover_fixtures(&large_dir);
    assert!(
        !fixtures.is_empty(),
        "large fixture directory should contain generated fixtures"
    );

    let mut failures = Vec::new();
    for fixture in fixtures {
        if let Err(err) = assert_fixture_parity(&fixture, &known) {
            failures.push(err);
        }
    }

    assert!(
        failures.is_empty(),
        "large fixture differential harness failures:\n{}",
        failures.join("\n\n")
    );
}

fn make_html_of_size(target_bytes: usize) -> Vec<u8> {
    let mut html = String::new();
    html.push_str("<!DOCTYPE html><html><body>\n");
    html.push_str("<p>bounded-memory-sentinel</p>\n");
    while html.len() < target_bytes {
        html.push_str("<p>streaming-bounded-memory-padding</p>\n");
    }
    html.push_str("</body></html>\n");
    html.into_bytes()
}

#[test]
fn latin1_fixture_uses_metadata_charset_across_conversion_paths() {
    let path = streaming_test_support::corpus_root().join("encoding/latin1.html");
    let html = read_fixture(&path);
    let meta = read_fixture_meta(&path);
    let content_type = meta.resolved_content_type();

    assert!(
        html.contains(&0xe9),
        "fixture must retain raw Latin-1 e acute"
    );
    assert!(
        std::str::from_utf8(&html).is_err(),
        "fixture must not be UTF-8"
    );
    let (decoded, _, had_errors) = encoding_rs::WINDOWS_1252.decode(&html);
    assert!(
        !had_errors,
        "Latin-1 fixture must decode without replacement"
    );
    assert!(decoded.contains("café") && decoded.contains("résumé"));

    let full = convert_full_buffer_entry(&html, Some(&content_type))
        .expect("full-buffer conversion must honor source-encoding metadata");
    assert!(full.contains("café") && full.contains("résumé"));

    let single =
        convert_streaming_single_entry(&html, Some(&content_type), default_streaming_budget())
            .expect("single-chunk streaming must honor source-encoding metadata");
    assert!(single.contains("café") && single.contains("résumé"));

    let e_acute = html
        .iter()
        .position(|byte| *byte == 0xe9)
        .expect("fixture contains a Latin-1 e acute");
    let chunks = vec![e_acute + 1, html.len() - (e_acute + 1)];
    let chunked = convert_streaming_chunked_entry(
        &html,
        &chunks,
        Some(&content_type),
        default_streaming_budget(),
    )
    .expect("chunked streaming must preserve a Latin-1 byte boundary");
    assert!(chunked.contains("café") && chunked.contains("résumé"));
    assert_eq!(
        normalize_whitespace_tokens(&full),
        normalize_whitespace_tokens(&single),
        "full-buffer and single streaming must preserve normalized parity"
    );
    assert_eq!(
        normalize_whitespace_tokens(&full),
        normalize_whitespace_tokens(&chunked),
        "full-buffer and chunked streaming must preserve normalized parity"
    );
}

#[test]
#[ignore = "evidence pack generation is expensive"]
fn bounded_memory_and_ttfb_evidence_pack() {
    // Keep a measurable safety margin between peak-memory and input growth.
    const PEAK_VS_INPUT_THRESHOLD: f64 = 0.9;

    let sizes = [
        1_024,
        10 * 1024,
        100 * 1024,
        1_024 * 1024,
        10 * 1024 * 1024,
        64 * 1024 * 1024,
    ];

    let mut evidence_rows = Vec::new();
    let mut peaks = Vec::new();

    for input_size in sizes {
        let html = make_html_of_size(input_size);

        let full_start = Instant::now();
        let _full = convert_full_buffer_entry(&html, Some("text/html; charset=UTF-8"))
            .unwrap_or_else(|err| panic!("full-buffer conversion failed for {input_size}: {err}"));
        let full_elapsed = full_start.elapsed();

        let budget = nginx_markdown_converter::streaming::MemoryBudget {
            total: 256 * 1024 * 1024,
            state_stack: 4 * 1024 * 1024,
            output_buffer: 192 * 1024 * 1024,
            charset_sniff: 64 * 1024,
            lookahead: 4 * 1024 * 1024,
        };
        let mut chunk_sizes = Vec::new();
        let mut remaining = html.len();
        while remaining > 0 {
            let take = remaining.min(4 * 1024);
            chunk_sizes.push(take);
            remaining -= take;
        }

        let run = convert_streaming_chunked(
            &html,
            &chunk_sizes,
            Some("text/html; charset=UTF-8"),
            default_streaming_options(),
            budget.clone(),
            None,
        )
        .unwrap_or_else(|err| panic!("streaming conversion failed for {input_size}: {err}"));

        assert!(
            run.stats.peak_memory_estimate > 0,
            "peak memory metric is zero/unpopulated for input size {} (budget {})",
            input_size,
            budget.total
        );

        assert!(
            run.stats.peak_memory_estimate <= budget.total,
            "peak memory {} exceeds budget {} for input size {}",
            run.stats.peak_memory_estimate,
            budget.total,
            input_size
        );

        peaks.push((input_size, run.stats.peak_memory_estimate));

        evidence_rows.push(serde_json::json!({
            "input_size_bytes": input_size,
            "full_buffer_total_ms": full_elapsed.as_secs_f64() * 1000.0,
            "streaming_total_ms": run.elapsed.as_secs_f64() * 1000.0,
            "streaming_ttfb_ms": run.first_non_empty_at.map(|d| d.as_secs_f64() * 1000.0),
            "peak_memory_estimate_bytes": run.stats.peak_memory_estimate,
            "chunks_processed": run.stats.chunks_processed,
        }));
    }

    let first_input = peaks.first().map(|(size, _)| *size as f64).unwrap_or(1.0);
    let first_peak = peaks
        .first()
        .map(|(_, peak)| *peak as f64)
        .unwrap_or(1.0)
        .max(1.0);
    let last_input = peaks
        .last()
        .map(|(size, _)| *size as f64)
        .unwrap_or(first_input);
    let last_peak = peaks
        .last()
        .map(|(_, peak)| *peak as f64)
        .unwrap_or(first_peak);

    let input_growth = (last_input / first_input).max(1.0);
    let peak_growth = (last_peak / first_peak).max(1.0);

    assert!(
        peak_growth < (input_growth * PEAK_VS_INPUT_THRESHOLD),
        "peak memory growth is too close to linear input growth: peak_growth={} input_growth={}",
        peak_growth,
        input_growth
    );

    let output_path = evidence_output_path();
    if let Some(parent) = output_path.parent() {
        fs::create_dir_all(parent)
            .unwrap_or_else(|err| panic!("create {}: {err}", parent.display()));
    }

    let payload = serde_json::json!({
        "schema": "streaming-evidence-v1",
        "generated_by": "components/rust-converter/tests/streaming_parity.rs",
        "rows": evidence_rows,
    });

    fs::write(
        &output_path,
        serde_json::to_vec_pretty(&payload).expect("serialize evidence payload"),
    )
    .unwrap_or_else(|err| panic!("write {}: {err}", output_path.display()));
}
