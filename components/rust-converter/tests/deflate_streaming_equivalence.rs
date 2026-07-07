//! Property test for deflate streaming decompression equivalence.
//!
//! **Validates: Requirements 4.8, 4.9**
//!
//! Property 6: Gzip Wrapper Handling Produces Correct Raw Deflate
//!
//! For 0.9.1, gzip streaming is DEFERRED. This property test applies ONLY to
//! raw deflate payloads. It verifies that decompressing raw deflate data via
//! the full-buffer path (`decompress_bounded` with `Format::Deflate`) produces
//! byte-identical output to incremental chunk-by-chunk decompression (the
//! streaming decompression approach using `flate2::Decompress`).
//!
//! This confirms the equivalence guarantee required by the streaming
//! decompression routing design: when raw deflate data is processed
//! incrementally (as it would be in the streaming path), the output matches
//! the full-buffer decompression result exactly.

use flate2::write::DeflateEncoder;
use flate2::{Compression, Decompress, FlushDecompress, Status};
use nginx_markdown_converter::decompress::{decompress_bounded, Format};
use proptest::prelude::*;
use std::io::Write;

// ============================================================================
// Helpers
// ============================================================================

/// Compress data using raw deflate (no gzip/zlib wrapper).
fn deflate_compress(data: &[u8]) -> Vec<u8> {
    let mut encoder = DeflateEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(data).expect("deflate encode");
    encoder.finish().expect("deflate finish")
}

/// Decompress raw deflate data incrementally in chunks of `chunk_size`.
///
/// This simulates the streaming decompression path where compressed data
/// arrives in arbitrary-sized chunks and is decompressed incrementally
/// using the stateful `flate2::Decompress` API.
///
/// Returns the fully decompressed output or an error description.
fn decompress_deflate_streaming(
    compressed: &[u8],
    chunk_size: usize,
    budget: usize,
) -> Result<Vec<u8>, String> {
    let chunk_size = chunk_size.max(1); // ensure at least 1 byte per chunk
    let mut decoder = Decompress::new(false); // false = raw deflate (no zlib header)
    let mut output = Vec::new();
    let out_chunk = 8192.min(budget.saturating_add(1)).max(1);
    let mut buf = vec![0u8; out_chunk];

    // Feed compressed data in chunks
    let mut offset = 0;
    loop {
        let end = (offset + chunk_size).min(compressed.len());
        let input_slice = &compressed[offset..end];

        let flush = if end == compressed.len() {
            FlushDecompress::Finish
        } else {
            FlushDecompress::None
        };

        let before_in = decoder.total_in();
        let before_out = decoder.total_out();

        let status = decoder
            .decompress(input_slice, &mut buf, flush)
            .map_err(|e| format!("deflate error: {e}"))?;

        let consumed_now = (decoder.total_in() - before_in) as usize;
        let produced_now = (decoder.total_out() - before_out) as usize;

        if produced_now > 0 {
            if output.len() + produced_now > budget {
                return Err("budget exceeded".to_string());
            }
            output.extend_from_slice(&buf[..produced_now]);
        }

        match status {
            Status::StreamEnd => return Ok(output),
            Status::Ok | Status::BufError => {
                // Advance offset by consumed bytes
                offset += consumed_now;
                if consumed_now == 0 && produced_now == 0 {
                    if offset >= compressed.len() {
                        return Err(
                            "deflate stream ended before final block (streaming)".to_string()
                        );
                    }
                }
            }
        }
    }
}

// ============================================================================
// Proptest strategies
// ============================================================================

/// Strategy that generates random byte payloads of varying sizes.
/// These are the "original" data that will be compressed into raw deflate.
fn arb_payload() -> impl Strategy<Value = Vec<u8>> {
    prop::collection::vec(any::<u8>(), 1..=4096)
}

/// Strategy for chunk sizes used in the streaming path.
/// Covers very small chunks (1 byte), typical sizes, and larger chunks.
fn arb_chunk_size() -> impl Strategy<Value = usize> {
    prop_oneof![
        1..=3usize,      // very small chunks (boundary stress)
        4..=64usize,     // small chunks
        65..=512usize,   // medium chunks
        513..=4096usize, // large chunks (may exceed compressed size)
    ]
}

/// Strategy for the decompression budget (always generous enough to hold output).
fn arb_budget() -> impl Strategy<Value = usize> {
    // Budget between 4KB and 1MB — always larger than our max payload (4KB)
    4096..=1_048_576usize
}

// ============================================================================
// Property 6: Deflate Streaming Equivalence
// ============================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(200))]

    /// **Validates: Requirements 4.8, 4.9**
    ///
    /// Property 6: For any valid raw deflate payload, verify that incremental
    /// (streaming) decompression produces byte-identical output to full-buffer
    /// decompression via `decompress_bounded(_, Format::Deflate, _)`.
    ///
    /// This confirms that when raw deflate data is processed chunk-by-chunk
    /// (as it would be in the streaming decompression path), the result is
    /// identical to processing it in one shot (full-buffer path).
    #[test]
    fn streaming_deflate_equivalent_to_fullbuffer(
        payload in arb_payload(),
        chunk_size in arb_chunk_size(),
        budget in arb_budget(),
    ) {
        // Compress the original payload into raw deflate
        let compressed = deflate_compress(&payload);

        // Full-buffer path: decompress in one shot
        let fullbuffer_result = decompress_bounded(&compressed, Format::Deflate, budget);

        // Streaming path: decompress in chunks
        let streaming_result = decompress_deflate_streaming(&compressed, chunk_size, budget);

        // Both paths must produce the same outcome
        match (&fullbuffer_result, &streaming_result) {
            (Ok(fb_result), Ok(st_output)) => {
                // Both succeeded — outputs must be byte-identical
                prop_assert_eq!(
                    &fb_result.output, st_output,
                    "Full-buffer and streaming decompression produced different output.\n\
                     payload_len={}, compressed_len={}, chunk_size={}, budget={}\n\
                     fullbuffer_len={}, streaming_len={}",
                    payload.len(), compressed.len(), chunk_size, budget,
                    fb_result.output.len(), st_output.len(),
                );
                // Output must match the original payload
                prop_assert_eq!(
                    &fb_result.output, &payload,
                    "Decompressed output does not match original payload.\n\
                     payload_len={}, output_len={}",
                    payload.len(), fb_result.output.len(),
                );
            }
            (Err(_), Err(_)) => {
                // Both failed — acceptable (e.g., budget too small)
                // The specific error messages may differ, but both paths agree
                // that decompression cannot succeed under these constraints.
            }
            (Ok(fb_result), Err(st_err)) => {
                // Full-buffer succeeded but streaming failed — NOT acceptable
                prop_assert!(
                    false,
                    "Full-buffer succeeded but streaming failed.\n\
                     payload_len={}, compressed_len={}, chunk_size={}, budget={}\n\
                     fullbuffer_output_len={}, streaming_error={:?}",
                    payload.len(), compressed.len(), chunk_size, budget,
                    fb_result.output.len(), st_err,
                );
            }
            (Err(fb_err), Ok(st_output)) => {
                // Streaming succeeded but full-buffer failed — NOT acceptable
                prop_assert!(
                    false,
                    "Streaming succeeded but full-buffer failed.\n\
                     payload_len={}, compressed_len={}, chunk_size={}, budget={}\n\
                     streaming_output_len={}, fullbuffer_error={:?}",
                    payload.len(), compressed.len(), chunk_size, budget,
                    st_output.len(), fb_err,
                );
            }
        }
    }

    /// Determinism sub-property: running the streaming decompressor twice
    /// on the same input with the same chunk size produces identical output.
    #[test]
    fn streaming_deflate_is_deterministic(
        payload in arb_payload(),
        chunk_size in arb_chunk_size(),
    ) {
        let compressed = deflate_compress(&payload);
        let budget = payload.len() + 1024; // generous budget

        let result1 = decompress_deflate_streaming(&compressed, chunk_size, budget);
        let result2 = decompress_deflate_streaming(&compressed, chunk_size, budget);

        match (&result1, &result2) {
            (Ok(out1), Ok(out2)) => {
                prop_assert_eq!(
                    out1, out2,
                    "Non-deterministic streaming decompression output.\n\
                     payload_len={}, chunk_size={}",
                    payload.len(), chunk_size,
                );
            }
            (Err(e1), Err(e2)) => {
                // Both failed the same way — acceptable
                prop_assert_eq!(
                    e1, e2,
                    "Non-deterministic streaming decompression errors.\n\
                     err1={:?}, err2={:?}",
                    e1, e2,
                );
            }
            _ => {
                prop_assert!(
                    false,
                    "Non-deterministic results: one succeeded, one failed.\n\
                     result1={:?}, result2={:?}",
                    result1.is_ok(), result2.is_ok(),
                );
            }
        }
    }

    /// Chunk-size independence: for any valid raw deflate payload, the
    /// decompressed output is identical regardless of how the compressed
    /// data is chunked during streaming processing.
    #[test]
    fn streaming_deflate_output_independent_of_chunk_size(
        payload in arb_payload(),
        chunk_size_a in arb_chunk_size(),
        chunk_size_b in arb_chunk_size(),
    ) {
        let compressed = deflate_compress(&payload);
        let budget = payload.len() + 1024; // generous budget

        let result_a = decompress_deflate_streaming(&compressed, chunk_size_a, budget);
        let result_b = decompress_deflate_streaming(&compressed, chunk_size_b, budget);

        match (&result_a, &result_b) {
            (Ok(out_a), Ok(out_b)) => {
                prop_assert_eq!(
                    out_a, out_b,
                    "Streaming output differs by chunk size.\n\
                     payload_len={}, chunk_a={}, chunk_b={}\n\
                     out_a_len={}, out_b_len={}",
                    payload.len(), chunk_size_a, chunk_size_b,
                    out_a.len(), out_b.len(),
                );
            }
            (Err(_), Err(_)) => {
                // Both failed — acceptable (budget constraint)
            }
            _ => {
                prop_assert!(
                    false,
                    "Chunk size affects success/failure.\n\
                     chunk_a={}, chunk_b={}, result_a_ok={}, result_b_ok={}",
                    chunk_size_a, chunk_size_b,
                    result_a.is_ok(), result_b.is_ok(),
                );
            }
        }
    }
}
