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

#[derive(Debug, Clone, Default)]
pub struct FixtureMeta {
    pub expected_fallback: bool,
    pub known_diff_ids: Vec<String>,
    pub high_risk_structures: Vec<String>,
}

#[derive(Debug, Clone)]
pub struct StreamingRun {
    pub markdown: String,
    pub stats: StreamingStats,
    pub first_non_empty_at: Option<Duration>,
    pub elapsed: Duration,
}

pub fn corpus_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/corpus")
}

pub fn known_differences_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/streaming/known-differences.toml")
}

pub fn evidence_output_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("target/streaming-evidence.json")
}

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

pub fn fixture_relative_name(path: &Path) -> String {
    let root = corpus_root();
    let rel = path.strip_prefix(root).unwrap_or(path);
    rel.to_string_lossy().replace('\\', "/")
}

pub fn read_fixture(path: &Path) -> Vec<u8> {
    fs::read(path).unwrap_or_else(|err| panic!("read fixture {}: {err}", path.display()))
}

pub fn read_fixture_meta(path: &Path) -> FixtureMeta {
    let meta_path = PathBuf::from(format!("{}.meta.json", path.with_extension("").display()));
    if !meta_path.exists() {
        return FixtureMeta::default();
    }

    let value: serde_json::Value = serde_json::from_slice(
        &fs::read(&meta_path).unwrap_or_else(|err| panic!("read {}: {err}", meta_path.display())),
    )
    .unwrap_or_else(|err| panic!("parse {}: {err}", meta_path.display()));

    let notes = value
        .get("streaming_notes")
        .and_then(serde_json::Value::as_object);

    let expected_fallback = notes
        .and_then(|notes| notes.get("expected_fallback"))
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false);

    let known_diff_ids = notes
        .and_then(|notes| notes.get("known_diff_ids"))
        .and_then(serde_json::Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(serde_json::Value::as_str)
                .map(ToOwned::to_owned)
                .collect()
        })
        .unwrap_or_default();

    let high_risk_structures = notes
        .and_then(|notes| notes.get("high_risk_structures"))
        .and_then(serde_json::Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(serde_json::Value::as_str)
                .map(ToOwned::to_owned)
                .collect()
        })
        .unwrap_or_default();

    FixtureMeta {
        expected_fallback,
        known_diff_ids,
        high_risk_structures,
    }
}

pub fn convert_full_buffer(
    html: &[u8],
    content_type: Option<&str>,
    options: ConversionOptions,
) -> Result<String, ConversionError> {
    let dom = parse_html_with_charset(html, content_type)?;
    MarkdownConverter::with_options(options).convert(&dom)
}

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

    Ok(StreamingRun {
        markdown: String::from_utf8_lossy(&out).into_owned(),
        stats: result.stats,
        first_non_empty_at,
        elapsed: start.elapsed(),
    })
}

pub fn single_byte_chunks(len: usize) -> Vec<usize> {
    vec![1; len]
}

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

pub fn tag_boundary_chunks(html: &[u8]) -> Vec<usize> {
    let mut positions = Vec::new();
    for (idx, byte) in html.iter().enumerate() {
        if (*byte == b'<' || *byte == b'>') && idx + 1 < html.len() {
            positions.push(idx + 1);
        }
    }
    positions_to_sizes(html.len(), positions)
}

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

pub fn default_streaming_options() -> ConversionOptions {
    ConversionOptions::default()
}

pub fn default_streaming_budget() -> MemoryBudget {
    MemoryBudget::default()
}
