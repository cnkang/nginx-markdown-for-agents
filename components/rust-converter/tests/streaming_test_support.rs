//! Shared test support utilities for streaming conversion tests.
//!
//! Provides common helper functions and data structures used across all
//! streaming test modules, including: fixture discovery and loading, full-buffer
//! and streaming conversion wrappers, chunking strategies (single-byte,
//! tag-boundary, UTF-8 mid-character), fixture metadata parsing, known
//! differences loading, whitespace normalization for comparison, and evidence
//! output serialization. This module is imported via `#[path]` by the various
//! streaming test files and is not compiled as a standalone test binary.

#![cfg(feature = "streaming")]
#![allow(dead_code)]

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter};
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::parser::parse_html_with_charset;
use nginx_markdown_converter::streaming::{MemoryBudget, StreamingConverter, StreamingStats};

#[cfg(feature = "incremental")]
use nginx_markdown_converter::incremental::IncrementalConverter;

/// Metadata extracted from a fixture's `.meta.json` sidecar file, used to
/// configure expected behavior and known differences for streaming tests.
#[derive(Debug, Clone, Default)]
pub struct FixtureMeta {
    /// Whether the fixture is expected to trigger streaming fallback.
    pub expected_fallback: bool,
    /// Fixture-scoped known-difference identifiers from the sidecar metadata.
    pub known_diff_ids: Vec<String>,
    /// High-risk structures used to explain why the fixture is in the corpus.
    pub high_risk_structures: Vec<String>,
    /// Declared byte encoding for fixtures without an explicit Content-Type.
    pub source_encoding: Option<String>,
    /// Explicit Content-Type used by every conversion path for this fixture.
    pub content_type: Option<String>,
}

impl FixtureMeta {
    /// Resolve the per-fixture Content-Type, keeping UTF-8 as the corpus default.
    pub fn resolved_content_type(&self) -> String {
        self.content_type.clone().unwrap_or_else(|| {
            self.source_encoding
                .as_ref()
                .map(|encoding| format!("text/html; charset={encoding}"))
                .unwrap_or_else(|| "text/html; charset=UTF-8".to_string())
        })
    }
}

/// Result of a streaming conversion run, including the concatenated Markdown
/// output, runtime statistics, and timing information.
#[derive(Debug, Clone)]
pub struct StreamingRun {
    /// Concatenated Markdown emitted by all feed calls plus finalize.
    pub markdown: String,
    /// Runtime statistics returned by the streaming converter.
    pub stats: StreamingStats,
    /// Elapsed time when the first non-empty Markdown chunk was observed.
    pub first_non_empty_at: Option<Duration>,
    /// Total feed plus finalize wall-clock duration measured by the helper.
    pub elapsed: Duration,
}

/// Returns the path to the test corpus root directory.
pub fn corpus_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/corpus")
}

/// Returns the path to the known-differences TOML file.
pub fn known_differences_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/streaming/known-differences.toml")
}

/// Returns the path where streaming evidence JSON is written.
pub fn evidence_output_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/streaming-evidence.json")
}

/// Recursively discover all `.html` fixture files under the given directory.
pub fn discover_html_fixtures(dir: &Path) -> Vec<PathBuf> {
    let mut fixtures = Vec::new();
    discover_recursive(dir, &mut fixtures);
    fixtures.sort();
    fixtures
}

fn discover_recursive(dir: &Path, fixtures: &mut Vec<PathBuf>) {
    let entries = match fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };

    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            discover_recursive(&path, fixtures);
            continue;
        }

        if path.extension().is_some_and(|ext| ext == "html") {
            fixtures.push(path);
        }
    }
}

/// Returns the fixture path relative to the corpus root as a normalized string.
pub fn fixture_relative_name(path: &Path) -> String {
    let root = corpus_root();
    let rel = path.strip_prefix(root).unwrap_or(path);
    rel.to_string_lossy().replace('\\', "/")
}

/// Read the raw bytes of a fixture file, panicking on I/O errors.
pub fn read_fixture(path: &Path) -> Vec<u8> {
    fs::read(path).unwrap_or_else(|err| panic!("read fixture {}: {err}", path.display()))
}

/// Read and parse the `.meta.json` sidecar file for a fixture, returning
/// default metadata if no sidecar exists.
pub fn read_fixture_meta(path: &Path) -> FixtureMeta {
    let legacy_meta = PathBuf::from(format!("{}.meta.json", path.with_extension("").display()));
    let direct_meta = PathBuf::from(format!("{}.meta.json", path.display()));
    let meta_path = if legacy_meta.exists() {
        legacy_meta
    } else if direct_meta.exists() {
        direct_meta
    } else {
        return FixtureMeta::default();
    };

    let value: serde_json::Value = serde_json::from_slice(
        &fs::read(&meta_path).unwrap_or_else(|err| panic!("read {}: {err}", meta_path.display())),
    )
    .unwrap_or_else(|err| panic!("parse {}: {err}", meta_path.display()));

    let root = value
        .as_object()
        .unwrap_or_else(|| panic!("{}: metadata root must be an object", meta_path.display()));
    let notes = root.get("streaming_notes").map(|notes| {
        notes
            .as_object()
            .unwrap_or_else(|| panic!("{}: streaming_notes must be an object", meta_path.display()))
    });

    let expected_fallback = match notes.and_then(|notes| notes.get("expected_fallback")) {
        Some(value) => value.as_bool().unwrap_or_else(|| {
            panic!(
                "{}: expected_fallback must be a boolean",
                meta_path.display()
            )
        }),
        None => false,
    };

    let parse_string_array = |key: &str| match notes.and_then(|notes| notes.get(key)) {
        Some(value) => value
            .as_array()
            .unwrap_or_else(|| panic!("{}: {key} must be an array", meta_path.display()))
            .iter()
            .map(|item| {
                item.as_str()
                    .filter(|item| !item.is_empty())
                    .unwrap_or_else(|| {
                        panic!(
                            "{}: {key} entries must be non-empty strings",
                            meta_path.display()
                        )
                    })
                    .to_owned()
            })
            .collect(),
        None => Vec::new(),
    };
    let parse_optional_string = |key: &str| {
        root.get(key).map(|value| {
            value
                .as_str()
                .filter(|value| !value.trim().is_empty())
                .unwrap_or_else(|| {
                    panic!("{}: {key} must be a non-empty string", meta_path.display())
                })
                .to_owned()
        })
    };

    FixtureMeta {
        expected_fallback,
        known_diff_ids: parse_string_array("known_diff_ids"),
        high_risk_structures: parse_string_array("high_risk_structures"),
        source_encoding: parse_optional_string("source-encoding"),
        content_type: parse_optional_string("content-type"),
    }
}

/// Normalize whitespace by splitting on whitespace and rejoining with single
/// spaces, used for comparison when whitespace-only drift is tolerated.
pub fn normalize_whitespace_tokens(input: &str) -> String {
    input.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Produce a human-readable summary of the first difference between two strings,
/// or `"<identical>"` if they are equal.
pub fn diff_summary(lhs: &str, rhs: &str) -> String {
    if lhs == rhs {
        return "<identical>".to_string();
    }

    let left_lines: Vec<&str> = lhs.lines().collect();
    let right_lines: Vec<&str> = rhs.lines().collect();
    let shared = left_lines.len().min(right_lines.len());

    for idx in 0..shared {
        if left_lines[idx] != right_lines[idx] {
            return format!(
                "line {} differs\n- {}\n+ {}",
                idx + 1,
                left_lines[idx],
                right_lines[idx]
            );
        }
    }

    format!(
        "line count differs (lhs={}, rhs={})",
        left_lines.len(),
        right_lines.len()
    )
}

/// Convert HTML to Markdown using the full-buffer (batch) conversion path.
pub fn convert_full_buffer(
    html: &[u8],
    content_type: Option<&str>,
    options: ConversionOptions,
) -> Result<String, ConversionError> {
    let dom = parse_html_with_charset(html, content_type)?;
    MarkdownConverter::with_options(options).convert(&dom)
}

/// Convert HTML to Markdown using the incremental conversion path (single feed).
#[cfg(feature = "incremental")]
pub fn convert_incremental(
    html: &[u8],
    content_type: Option<&str>,
    options: ConversionOptions,
) -> Result<String, ConversionError> {
    let mut conv = IncrementalConverter::new(options);
    if let Some(content_type) = content_type {
        conv.set_content_type(Some(content_type.to_string()));
    }
    conv.feed_chunk(html)?;
    conv.finalize()
}

/// Convert HTML through the streaming path as a single chunk, delegating to
/// `convert_streaming_chunked` with the full HTML length as the chunk size.
pub fn convert_streaming_single(
    html: &[u8],
    content_type: Option<&str>,
    options: ConversionOptions,
    budget: MemoryBudget,
    timeout: Option<Duration>,
) -> Result<StreamingRun, ConversionError> {
    convert_streaming_chunked(
        html,
        &[html.len().max(1)],
        content_type,
        options,
        budget,
        timeout,
    )
}

/// Convert HTML through the streaming path using an explicit chunk schedule.
///
/// This is the shared production-like test harness for streaming parity tests:
/// it applies content type, timeout, budget, and options once, feeds chunks in
/// order, records first-output timing, and appends finalize output. Supplying
/// chunk sizes that split tags or multibyte UTF-8 characters lets tests verify
/// boundary handling without duplicating converter orchestration logic.
pub fn convert_streaming_chunked(
    html: &[u8],
    chunk_sizes: &[usize],
    content_type: Option<&str>,
    options: ConversionOptions,
    budget: MemoryBudget,
    timeout: Option<Duration>,
) -> Result<StreamingRun, ConversionError> {
    let mut conv = StreamingConverter::new(options, budget);
    if let Some(content_type) = content_type {
        conv.set_content_type(Some(content_type.to_string()));
    }
    if let Some(timeout) = timeout {
        conv.set_timeout(timeout);
    }

    let mut out = Vec::new();
    let mut cursor = 0usize;
    let mut first_non_empty_at = None;
    let start = Instant::now();

    for &chunk_size in chunk_sizes {
        if cursor >= html.len() {
            break;
        }
        let size = chunk_size.max(1);
        let end = cursor.saturating_add(size).min(html.len());

        let chunk = conv.feed_chunk(&html[cursor..end])?;
        if first_non_empty_at.is_none() && !chunk.markdown.is_empty() {
            first_non_empty_at = Some(start.elapsed());
        }
        out.extend_from_slice(&chunk.markdown);
        cursor = end;
    }

    if cursor < html.len() {
        let chunk = conv.feed_chunk(&html[cursor..])?;
        if first_non_empty_at.is_none() && !chunk.markdown.is_empty() {
            first_non_empty_at = Some(start.elapsed());
        }
        out.extend_from_slice(&chunk.markdown);
    }

    let result = conv.finalize()?;
    if first_non_empty_at.is_none() && !result.final_markdown.is_empty() {
        first_non_empty_at = Some(start.elapsed());
    }
    out.extend_from_slice(&result.final_markdown);

    let markdown = String::from_utf8(out).map_err(|err| {
        ConversionError::EncodingError(format!("streaming emitted invalid UTF-8: {err}"))
    })?;

    Ok(StreamingRun {
        markdown,
        stats: result.stats,
        first_non_empty_at,
        elapsed: start.elapsed(),
    })
}

/// Generate chunk sizes that feed one byte at a time, exercising every byte
/// boundary in the input.
pub fn single_byte_chunks(len: usize) -> Vec<usize> {
    vec![1; len]
}

/// Convert absolute split positions into per-feed chunk sizes.
///
/// Positions are sorted, de-duplicated, and clamped inside `(0, len)`, which
/// keeps boundary strategies deterministic even when generated positions repeat.
pub fn positions_to_sizes(len: usize, mut split_positions: Vec<usize>) -> Vec<usize> {
    if len == 0 {
        return Vec::new();
    }

    split_positions.sort_unstable();
    split_positions.dedup();
    split_positions.retain(|pos| *pos > 0 && *pos < len);

    let mut sizes = Vec::new();
    let mut previous = 0usize;

    for position in split_positions {
        sizes.push(position - previous);
        previous = position;
    }

    sizes.push(len - previous);
    sizes
}

/// Build chunk sizes that split immediately after tag boundary bytes.
///
/// This exercises tokenizer state transitions around `<` and `>` so streaming
/// tests cover partial tag and adjacent text paths, not only text-only chunks.
pub fn tag_boundary_chunks(html: &[u8]) -> Vec<usize> {
    let mut positions = Vec::new();
    for (idx, byte) in html.iter().enumerate() {
        if (*byte == b'<' || *byte == b'>') && idx + 1 < html.len() {
            positions.push(idx + 1);
        }
    }
    positions_to_sizes(html.len(), positions)
}

/// Build chunk sizes that split inside multibyte UTF-8 characters.
///
/// The strategy verifies the streaming UTF-8 tail buffer: split bytes must be
/// preserved and reassembled before text reaches lossy string conversion.
pub fn utf8_mid_char_chunks(html: &[u8]) -> Vec<usize> {
    let text = match std::str::from_utf8(html) {
        Ok(text) => text,
        Err(_) => return positions_to_sizes(html.len(), vec![html.len() / 2]),
    };

    let mut positions = Vec::new();
    for (idx, ch) in text.char_indices() {
        let width = ch.len_utf8();
        if width > 1 && idx + 1 < html.len() {
            positions.push(idx + 1);
        }
    }

    if positions.is_empty() {
        positions.push(html.len() / 2);
    }

    positions_to_sizes(html.len(), positions)
}

/// Return default conversion options for streaming tests.
pub fn default_streaming_options() -> ConversionOptions {
    ConversionOptions::default()
}

/// Return default memory budget for streaming tests.
pub fn default_streaming_budget() -> MemoryBudget {
    MemoryBudget::default()
}
