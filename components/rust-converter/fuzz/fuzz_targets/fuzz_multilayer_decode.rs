#![no_main]

//! Fuzz target for multi-layer Content-Encoding decoding.
//!
//! Derives a 1-3 layer encoding chain and a wire payload from arbitrary bytes,
//! then drives [`nginx_markdown_converter::encoding::decode_chain`] with fixed
//! limits (1 MiB cumulative output budget, 100:1 per-layer ratio). Payload
//! construction mixes raw random bytes (format-error path), valid
//! gzip/deflate/brotli streams (success path), high-compression expansion
//! bombs (ratio/budget path), and mid-stream truncation (truncation path).
//!
//! Input model:
//! - byte 0: layer count - 1 (1..=3 layers, application order)
//! - bytes 1..: per-layer encoding selectors (0=gzip, 1=deflate, 2=br)
//! - next byte: payload kind (0=raw, 1=gzip, 2=deflate, 3=br, 4=expansion
//!   bomb, 5=truncated gzip)
//! - remaining bytes: payload source
//!
//! Invariants checked:
//! - No panics on any input.
//! - On success, the decoded output never exceeds the 1 MiB budget.
//! - Every error classifies into one of the five `ChainDecodeError` variants
//!   (exhaustive match, compile-time oracle).
//! - Single-layer round trips: a valid compressed stream that decodes
//!   successfully reproduces the original payload.

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::encoding::{ChainDecodeError, DecodeLimits, Encoding, decode_chain};
use std::io::Write;

/// Cumulative output budget shared by the decoder and the oracle.
const MAX_OUTPUT: usize = 1024 * 1024;

/// Per-layer expansion ratio.
const RATIO: u64 = 100;

/// Map a selector byte onto a compression format.
fn pick_encoding(seed: u8) -> Encoding {
    match seed % 3 {
        0 => Encoding::Gzip,
        1 => Encoding::Deflate,
        _ => Encoding::Br,
    }
}

/// Derive a 1-3 layer chain (application order) from the leading bytes.
fn pick_layers(data: &[u8]) -> Vec<Encoding> {
    let count = 1 + (data[0] as usize) % 3;
    (0..count).map(|i| pick_encoding(data[1 + i])).collect()
}

/// Compress a payload with gzip.
fn gzip_compress(data: &[u8]) -> Vec<u8> {
    let mut enc = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
    enc.write_all(data).unwrap();
    enc.finish().unwrap()
}

/// Compress a payload with zlib-wrapped deflate.
fn deflate_compress(data: &[u8]) -> Vec<u8> {
    let mut enc = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
    enc.write_all(data).unwrap();
    enc.finish().unwrap()
}

/// Compress a payload with brotli.
fn brotli_compress(data: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut enc = brotli::CompressorWriter::new(&mut out, 4096, 6, 22);
    enc.write_all(data).unwrap();
    drop(enc);
    out
}

/// Build a wire payload from the payload-kind selector, source bytes, and
/// the derived layer chain.
///
/// `layers` is in application order (layers[0] is applied first, i.e. the
/// innermost encoding).  `decode_chain` decodes from the outermost layer
/// (last declared) inward, so the wire payload must nest the encodings in
/// the same order: compress `src` with layers[0] first, then wrap that
/// result with layers[1], and so on.
///
/// Returns the wire payload plus, for the single-format round-trip oracle,
/// the original uncompressed bytes when they are recoverable.
fn build_payload(kind: u8, src: &[u8], layers: &[Encoding]) -> (Vec<u8>, Option<Vec<u8>>) {
    let single = match kind % 6 {
        0 => (src.to_vec(), None),
        1 => (gzip_compress(src), Some(src.to_vec())),
        2 => (deflate_compress(src), Some(src.to_vec())),
        3 => (brotli_compress(src), Some(src.to_vec())),
        4 => {
            /* Expansion bomb: highly repetitive data whose decoded size
             * always exceeds MAX_OUTPUT, so the single-layer case exercises
             * the absolute budget path. */
            let size = MAX_OUTPUT
                + u32::from_le_bytes([
                    src.first().copied().unwrap_or(0),
                    src.get(1).copied().unwrap_or(0),
                    0,
                    0,
                ]) as usize
                    % 900_000;
            let rep = src.get(2).copied().unwrap_or(b'0');
            (gzip_compress(&vec![rep; size]), None)
        }
        _ => {
            /* Truncated gzip stream: a mid-stream cut exercises the
             * TruncatedInput/IoError classifications. */
            let gz = gzip_compress(src);
            let cut = gz.len() / 2;
            (gz[..cut].to_vec(), None)
        }
    };

    /* Multi-layer chains: wrap the source with EVERY layer in application
     * order (layers[0] innermost, last layer outermost), matching how
     * decode_chain unwinds from the outermost encoding inward.  The
     * single-layer oracle is only meaningful for the single-layer success
     * kinds, so it is dropped for multi-layer wires. */
    if layers.len() <= 1 {
        return single;
    }

    let mut wire = src.to_vec();
    for enc in layers {
        wire = match enc {
            Encoding::Gzip => gzip_compress(&wire),
            Encoding::Deflate => deflate_compress(&wire),
            Encoding::Br => brotli_compress(&wire),
            Encoding::Identity => wire,
        };
    }
    (wire, None)
}

fuzz_target!(|data: &[u8]| {
    /* Need 1 + layer_count selector bytes, the kind byte, and at least one
     * payload byte; layer_count is at most 3. */
    if data.len() < 6 {
        return;
    }
    let layers = pick_layers(data);
    let kind = data[1 + layers.len()];
    let src = &data[2 + layers.len()..];
    let (wire, original) = build_payload(kind, src, &layers);

    let limits = DecodeLimits {
        max_output: MAX_OUTPUT,
        ratio: RATIO,
    };
    match decode_chain(&wire, &layers, limits) {
        Ok(out) => {
            /* Budget invariant: decoded output never exceeds the limit. */
            assert!(
                out.len() <= MAX_OUTPUT,
                "decoded output {} exceeded budget {}",
                out.len(),
                MAX_OUTPUT
            );
            /* Round-trip oracle: a single valid compressed layer must
             * reproduce its original payload exactly.  The oracle applies
             * only when the payload format matches the layer encoding: the
             * kind byte and the encoding byte are independently derived, so
             * a mismatched pair must not be compared.  For a Deflate layer
             * the oracle applies only to the zlib-wrapped deflate case
             * (which carries a checksum and therefore a canonical byte
             * stream); a raw RFC 1951 stream has no checksum, so its bytes
             * may legitimately classify as a format or truncation error
             * without panicking. */
            if layers.len() == 1
                && let Some(orig) = original
            {
                let format_matches = match kind % 6 {
                    1 => layers[0] == Encoding::Gzip,
                    2 => layers[0] == Encoding::Deflate,
                    _ => layers[0] == Encoding::Br,
                };
                if format_matches {
                    assert_eq!(out, orig, "single-layer round trip mismatch");
                }
            }
        }
        Err(err) => {
            /* Exhaustive classification oracle: every error must be one of
             * the five declared variants (compile-time exhaustive). */
            match err {
                ChainDecodeError::BudgetExceeded
                | ChainDecodeError::RatioExceeded
                | ChainDecodeError::FormatError(_)
                | ChainDecodeError::TruncatedInput(_)
                | ChainDecodeError::IoError(_) => {}
            }
        }
    }
});
