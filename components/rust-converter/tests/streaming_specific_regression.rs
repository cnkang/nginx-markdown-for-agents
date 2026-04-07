#![cfg(feature = "streaming")]

#[path = "known_differences.rs"]
mod known_differences;
#[path = "support/streaming_compare_support.rs"]
mod streaming_compare_support;
#[path = "streaming_test_support.rs"]
mod streaming_test_support;

use std::io::{Read, Write};

use flate2::Compression;
use flate2::read::{GzDecoder, ZlibDecoder};
use flate2::write::{GzEncoder, ZlibEncoder};
use known_differences::KnownDifferences;
use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::streaming::{MemoryBudget, StreamingConverter};
use streaming_compare_support::compare_or_known;
use streaming_test_support::{
    convert_full_buffer, convert_streaming_chunked, convert_streaming_single,
    default_streaming_budget, default_streaming_options, discover_html_fixtures,
    fixture_relative_name, known_differences_path,
};

#[cfg(feature = "incremental")]
use nginx_markdown_converter::incremental::IncrementalConverter;

struct ChunkedReader<'a> {
    data: &'a [u8],
    chunk_sizes: Vec<usize>,
    pos: usize,
    chunk_idx: usize,
    remaining_in_chunk: usize,
}

impl<'a> ChunkedReader<'a> {
    fn new(data: &'a [u8], chunk_sizes: &[usize]) -> Self {
        let mut normalized: Vec<usize> = chunk_sizes.iter().copied().filter(|s| *s > 0).collect();
        if normalized.is_empty() {
            normalized.push(data.len().max(1));
        }

        let remaining_in_chunk = normalized[0];
        Self {
            data,
            chunk_sizes: normalized,
            pos: 0,
            chunk_idx: 0,
            remaining_in_chunk,
        }
    }

    fn replenish_chunk(&mut self) {
        while self.remaining_in_chunk == 0 {
            self.chunk_idx = self.chunk_idx.saturating_add(1);
            self.remaining_in_chunk = self
                .chunk_sizes
                .get(self.chunk_idx)
                .copied()
                .unwrap_or_else(|| self.data.len().saturating_sub(self.pos));
            if self.remaining_in_chunk == 0 {
                break;
            }
        }
    }
}

impl Read for ChunkedReader<'_> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        if self.pos >= self.data.len() {
            return Ok(0);
        }

        if self.remaining_in_chunk == 0 {
            self.replenish_chunk();
        }

        let available = self.data.len().saturating_sub(self.pos);
        let to_copy = available.min(buf.len()).min(self.remaining_in_chunk.max(1));

        if to_copy == 0 {
            return Ok(0);
        }

        buf[..to_copy].copy_from_slice(&self.data[self.pos..self.pos + to_copy]);
        self.pos += to_copy;
        self.remaining_in_chunk = self.remaining_in_chunk.saturating_sub(to_copy);
        Ok(to_copy)
    }
}

fn compress_gzip(input: &[u8]) -> Vec<u8> {
    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder
        .write_all(input)
        .expect("write gzip payload into encoder");
    encoder.finish().expect("finish gzip encoder")
}

fn compress_deflate(input: &[u8]) -> Vec<u8> {
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder
        .write_all(input)
        .expect("write deflate payload into encoder");
    encoder.finish().expect("finish deflate encoder")
}

fn compress_brotli(input: &[u8]) -> Vec<u8> {
    let mut output = Vec::new();
    {
        let mut writer = brotli::CompressorWriter::new(&mut output, 4096, 5, 22);
        writer
            .write_all(input)
            .expect("write brotli payload into encoder");
    }
    output
}

fn convert_streaming_from_compressed_gzip(
    compressed: &[u8],
    split: &[usize],
) -> Result<String, ConversionError> {
    let reader = ChunkedReader::new(compressed, split);
    let mut decoder = GzDecoder::new(reader);
    convert_streaming_from_decoder(&mut decoder)
}

fn convert_streaming_from_compressed_deflate(
    compressed: &[u8],
    split: &[usize],
) -> Result<String, ConversionError> {
    let reader = ChunkedReader::new(compressed, split);
    let mut decoder = ZlibDecoder::new(reader);
    convert_streaming_from_decoder(&mut decoder)
}

fn convert_streaming_from_compressed_brotli(
    compressed: &[u8],
    split: &[usize],
) -> Result<String, ConversionError> {
    let reader = ChunkedReader::new(compressed, split);
    let mut decoder = brotli::Decompressor::new(reader, 4096);
    convert_streaming_from_decoder(&mut decoder)
}

fn convert_streaming_from_decoder(decoder: &mut dyn Read) -> Result<String, ConversionError> {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

    let mut output = Vec::new();
    // Intentionally odd-sized buffer to exercise boundary crossings in feed_chunk.
    let mut buf = [0u8; 257];

    loop {
        let read = decoder
            .read(&mut buf)
            .expect("decompress chunk while streaming");
        if read == 0 {
            break;
        }

        let chunk = conv.feed_chunk(&buf[..read])?;
        output.extend_from_slice(&chunk.markdown);
    }

    let final_result = conv.finalize()?;
    output.extend_from_slice(&final_result.final_markdown);
    Ok(String::from_utf8_lossy(&output).into_owned())
}

#[test]
fn charset_regression_corpus_and_mismatch_fixture() {
    let corpus = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/corpus");
    let known = KnownDifferences::from_file(&known_differences_path())
        .unwrap_or_else(|err| panic!("load known differences: {err}"));

    for fixture in discover_html_fixtures(&corpus.join("encoding")) {
        let html = std::fs::read(&fixture)
            .unwrap_or_else(|err| panic!("read fixture {}: {err}", fixture.display()));

        let full =
            convert_full_buffer(&html, None, default_streaming_options()).unwrap_or_else(|err| {
                panic!("full conversion failed for {}: {err}", fixture.display())
            });

        let streaming = convert_streaming_single(
            &html,
            Some("text/html; charset=UTF-8"),
            default_streaming_options(),
            default_streaming_budget(),
            None,
        );

        match streaming {
            Ok(run) => compare_or_known(
                &fixture_relative_name(&fixture),
                &full,
                &run.markdown,
                &known,
            )
            .unwrap_or_else(|err| panic!("{err}")),
            Err(ConversionError::StreamingFallback { .. }) => {}
            Err(err) => panic!(
                "streaming conversion failed for {}: {err}",
                fixture.display()
            ),
        }
    }

    let mismatch = corpus.join("streaming/charset-mismatch.html");
    let mismatch_html = std::fs::read(&mismatch)
        .unwrap_or_else(|err| panic!("read fixture {}: {err}", mismatch.display()));

    let full = convert_full_buffer(
        &mismatch_html,
        Some("text/html; charset=ISO-8859-1"),
        default_streaming_options(),
    )
    .unwrap_or_else(|err| panic!("full conversion failed for mismatch fixture: {err}"));

    let streaming = convert_streaming_single(
        &mismatch_html,
        Some("text/html; charset=ISO-8859-1"),
        default_streaming_options(),
        default_streaming_budget(),
        None,
    )
    .unwrap_or_else(|err| panic!("streaming conversion failed for mismatch fixture: {err}"));

    compare_or_known(
        &fixture_relative_name(&mismatch),
        &full,
        &streaming.markdown,
        &known,
    )
    .unwrap_or_else(|err| panic!("{err}"));
}

#[test]
fn compressed_input_regression_gzip_deflate_brotli() {
    let corpus =
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/corpus/streaming");
    let known = KnownDifferences::from_file(&known_differences_path())
        .unwrap_or_else(|err| panic!("load known differences: {err}"));
    let fixtures = [
        corpus.join("inline-spacing.html"),
        corpus.join("normalization-crlf.html"),
        corpus.join("flush-boundary-edge.html"),
    ];

    let split = [7, 11, 13, 17, 19, 23, 29, 31];

    for fixture in fixtures {
        let html = std::fs::read(&fixture)
            .unwrap_or_else(|err| panic!("read fixture {}: {err}", fixture.display()));

        let full =
            convert_full_buffer(&html, None, default_streaming_options()).unwrap_or_else(|err| {
                panic!("full conversion failed for {}: {err}", fixture.display())
            });

        let fixture_name = fixture_relative_name(&fixture);
        let gzip = convert_streaming_from_compressed_gzip(&compress_gzip(&html), &split)
            .unwrap_or_else(|err| panic!("gzip streaming failed for {}: {err}", fixture.display()));
        compare_or_known(&fixture_name, &full, &gzip, &known).unwrap_or_else(|err| panic!("{err}"));

        let deflate = convert_streaming_from_compressed_deflate(&compress_deflate(&html), &split)
            .unwrap_or_else(|err| {
                panic!("deflate streaming failed for {}: {err}", fixture.display())
            });
        compare_or_known(&fixture_name, &full, &deflate, &known)
            .unwrap_or_else(|err| panic!("{err}"));

        let brotli = convert_streaming_from_compressed_brotli(&compress_brotli(&html), &split)
            .unwrap_or_else(|err| {
                panic!("brotli streaming failed for {}: {err}", fixture.display())
            });
        compare_or_known(&fixture_name, &full, &brotli, &known)
            .unwrap_or_else(|err| panic!("{err}"));
    }
}

#[test]
fn streaming_boundary_conditions() {
    let empty = b"";
    let empty_result = convert_streaming_single(
        empty,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        default_streaming_budget(),
        None,
    );
    assert!(
        empty_result.is_err()
            || empty_result
                .as_ref()
                .is_ok_and(|run| run.markdown.is_empty()),
        "empty input should fail or yield empty markdown"
    );

    let single_byte = b"x";
    let single_byte_full = convert_full_buffer(
        single_byte,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
    )
    .expect("full-buffer conversion should succeed for single-byte input");
    let single_byte_result = convert_streaming_single(
        single_byte,
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        default_streaming_budget(),
        None,
    );
    match single_byte_result {
        Ok(run) => assert_eq!(
            run.markdown, single_byte_full,
            "single-byte streaming output mismatch with full-buffer output"
        ),
        Err(err) => panic!("single-byte streaming conversion failed: {err}"),
    }

    let flush_fixture = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/corpus/streaming/flush-boundary-edge.html");
    let flush_html = std::fs::read(&flush_fixture)
        .unwrap_or_else(|err| panic!("read fixture {}: {err}", flush_fixture.display()));

    let flush_chunked = convert_streaming_chunked(
        &flush_html,
        &[
            flush_html.len() / 2,
            flush_html.len() - (flush_html.len() / 2),
        ],
        Some("text/html; charset=UTF-8"),
        default_streaming_options(),
        default_streaming_budget(),
        None,
    );
    assert!(
        flush_chunked.is_ok()
            || matches!(
                flush_chunked,
                Err(ConversionError::StreamingFallback { .. })
            ),
        "flush-boundary fixture should either succeed or fallback"
    );

    let tiny_budget = MemoryBudget {
        total: 1024,
        state_stack: 256,
        output_buffer: 256,
        charset_sniff: 128,
        lookahead: 256,
    };
    let mut conv = StreamingConverter::new(default_streaming_options(), tiny_budget);
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
    let oversized = format!(
        "<!DOCTYPE html><html><body>{}</body></html>",
        "<p>x</p>".repeat(1024)
    );
    let err = conv.feed_chunk(oversized.as_bytes()).unwrap_err();
    assert!(
        matches!(
            err,
            ConversionError::BudgetExceeded { .. }
                | ConversionError::MemoryLimit(_)
                | ConversionError::StreamingFallback { .. }
        ),
        "expected streaming budget rejection, got {err:?}"
    );
}

#[cfg(feature = "incremental")]
#[test]
fn boundary_max_size_incremental_guard() {
    let mut conv = IncrementalConverter::with_max_buffer_size(default_streaming_options(), 64);
    let html = vec![b'a'; 128];
    let err = conv.feed_chunk(&html).unwrap_err();
    assert!(
        matches!(err, ConversionError::MemoryLimit(_)),
        "expected MemoryLimit from max_size guard, got {err:?}"
    );
}
