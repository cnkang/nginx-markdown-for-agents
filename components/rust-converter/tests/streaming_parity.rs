#![cfg(feature = "streaming")]

#[path = "known_differences.rs"]
mod known_differences;
#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::Instant;

use known_differences::{KnownDifferences, OutputDifference};
use nginx_markdown_converter::error::ConversionError;
use streaming_test_support::{
    convert_full_buffer, convert_streaming_chunked, convert_streaming_single,
    default_streaming_budget, default_streaming_options, discover_html_fixtures,
    evidence_output_path, fixture_relative_name, known_differences_path,
    normalize_whitespace_tokens, read_fixture, read_fixture_meta,
};

#[derive(Debug)]
enum ComparisonResult {
    Identical,
    KnownDifference {
        diff_id: String,
        description: String,
    },
    Divergence {
        full_buffer_output: String,
        streaming_output: String,
        diff: String,
    },
}

fn discover_fixtures(corpus_dir: &Path) -> Vec<std::path::PathBuf> {
    discover_html_fixtures(corpus_dir)
}

fn convert_full_buffer_entry(html: &[u8]) -> Result<String, ConversionError> {
    convert_full_buffer(
        html,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
    )
}

fn convert_streaming_single_entry(
    html: &[u8],
    budget: nginx_markdown_converter::streaming::MemoryBudget,
) -> Result<String, ConversionError> {
    let run = convert_streaming_single(
        html,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        budget,
        None,
    )?;
    Ok(run.markdown)
}

fn convert_streaming_chunked_entry(
    html: &[u8],
    chunk_sizes: &[usize],
    budget: nginx_markdown_converter::streaming::MemoryBudget,
) -> Result<String, ConversionError> {
    let run = convert_streaming_chunked(
        html,
        chunk_sizes,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        budget,
        None,
    )?;
    Ok(run.markdown)
}

fn compare_outputs(
    fixture_name: &str,
    full_buffer: &str,
    streaming: &str,
    known_diffs: &KnownDifferences,
) -> ComparisonResult {
    if full_buffer == streaming {
        return ComparisonResult::Identical;
    }

    let mut diff = unified_diff_summary(full_buffer, streaming);
    if normalize_whitespace_tokens(full_buffer) == normalize_whitespace_tokens(streaming) {
        diff = format!("whitespace-only-parity-drift\n{diff}");
    }

    let output = OutputDifference {
        full_buffer,
        streaming,
        diff: &diff,
    };

    if let Some(entry) = known_diffs.matches(fixture_name, &output) {
        return ComparisonResult::KnownDifference {
            diff_id: entry.id.clone(),
            description: entry.description.clone(),
        };
    }

    ComparisonResult::Divergence {
        full_buffer_output: full_buffer.to_string(),
        streaming_output: streaming.to_string(),
        diff,
    }
}

fn unified_diff_summary(expected: &str, actual: &str) -> String {
    const MAX_DIFF_LINES: usize = 3;

    if expected == actual {
        return "<identical>".to_string();
    }

    let lhs: Vec<&str> = expected.lines().collect();
    let rhs: Vec<&str> = actual.lines().collect();
    let shared = lhs.len().min(rhs.len());
    let mut diffs = Vec::new();

    for idx in 0..shared {
        if lhs[idx] != rhs[idx] {
            diffs.push(format!(
                "line {} differs\n- {}\n+ {}",
                idx + 1,
                lhs[idx],
                rhs[idx]
            ));
            if diffs.len() == MAX_DIFF_LINES {
                break;
            }
        }
    }

    if !diffs.is_empty() {
        return diffs.join("\n");
    }

    if lhs.len() != rhs.len() {
        return format!(
            "line count differs (full-buffer={}, streaming={})",
            lhs.len(),
            rhs.len()
        );
    }

    "outputs differ but first mismatch not localized".to_string()
}

fn is_known_runtime_difference(
    fixture_name: &str,
    marker: &str,
    detail: &str,
    known_diffs: &KnownDifferences,
) -> bool {
    let diff = format!("{marker}\n{detail}");
    let output = OutputDifference {
        full_buffer: "",
        streaming: "",
        diff: &diff,
    };
    known_diffs.matches(fixture_name, &output).is_some()
}

fn assert_fixture_parity(path: &Path, known_diffs: &KnownDifferences) -> Result<(), String> {
    let fixture_name = fixture_relative_name(path);
    let meta = read_fixture_meta(path);
    let html = read_fixture(path);

    let full_buffer = convert_full_buffer_entry(&html)
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

    let single = convert_streaming_single_entry(&html, budget.clone());
    let chunked = convert_streaming_chunked_entry(&html, &chunk_sizes, budget);

    match (&single, &chunked) {
        (
            Err(ConversionError::StreamingFallback { .. }),
            Err(ConversionError::StreamingFallback { .. }),
        ) => {
            if !meta.expected_fallback {
                if is_known_runtime_difference(
                    &fixture_name,
                    "streaming-fallback",
                    "both-single-and-chunked",
                    known_diffs,
                ) {
                    return Ok(());
                }
                return Err(format!(
                    "{fixture_name}: unexpected fallback (meta expected_fallback=false)"
                ));
            }
            return Ok(());
        }
        (Err(err), _) if !matches!(err, ConversionError::StreamingFallback { .. }) => {
            if is_known_runtime_difference(
                &fixture_name,
                "streaming-error",
                &err.to_string(),
                known_diffs,
            ) {
                return Ok(());
            }
            return Err(format!("{fixture_name}: streaming single failed: {err}"));
        }
        (_, Err(err)) if !matches!(err, ConversionError::StreamingFallback { .. }) => {
            if is_known_runtime_difference(
                &fixture_name,
                "streaming-error",
                &err.to_string(),
                known_diffs,
            ) {
                return Ok(());
            }
            return Err(format!("{fixture_name}: streaming chunked failed: {err}"));
        }
        (Err(ConversionError::StreamingFallback { .. }), Ok(_))
        | (Ok(_), Err(ConversionError::StreamingFallback { .. })) => {
            if is_known_runtime_difference(
                &fixture_name,
                "streaming-fallback-mismatch",
                "single-vs-chunked",
                known_diffs,
            ) {
                return Ok(());
            }
            return Err(format!(
                "{fixture_name}: single/chunked fallback behavior mismatch"
            ));
        }
        _ => {}
    }

    if meta.expected_fallback {
        return Err(format!(
            "{fixture_name}: expected fallback but streaming produced Markdown"
        ));
    }

    let single = single.unwrap_or_else(|_| unreachable!());
    let chunked = chunked.unwrap_or_else(|_| unreachable!());

    for (label, streaming_output) in [("single", single), ("chunked", chunked)] {
        match compare_outputs(&fixture_name, &full_buffer, &streaming_output, known_diffs) {
            ComparisonResult::Identical => {}
            ComparisonResult::KnownDifference {
                diff_id,
                description,
            } => {
                if !meta.known_diff_ids.iter().any(|id| id == &diff_id) {
                    return Err(format!(
                        "{fixture_name}: matched known diff {diff_id} ({description}) but fixture metadata does not list it"
                    ));
                }
            }
            ComparisonResult::Divergence {
                full_buffer_output,
                streaming_output,
                diff,
            } => {
                return Err(format!(
                    "{fixture_name}: {label} output divergence\n{diff}\n--- full-buffer ---\n{full_buffer_output}\n--- streaming ---\n{streaming_output}"
                ));
            }
        }
    }

    Ok(())
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
        let _full = convert_full_buffer_entry(&html)
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
