//! Bounded decompression module.
//!
//! Provides safe, budget-limited decompression for gzip, deflate, and brotli
//! compressed data. The decompression operation terminates immediately when
//! the output exceeds the configured budget, preventing zip-bomb attacks.
//!
//! # Supported Formats
//!
//! - `0` = gzip (RFC 1952)
//! - `1` = deflate: zlib-wrapped (RFC 1950) by default, with raw RFC 1951
//!   fallback (sniffed heuristically)
//! - `2` = brotli (RFC 7932)
//!
//! # Error Categories
//!
//! Each decompression failure is classified into a distinct error category
//! so the C caller can take appropriate action (metrics, logging, fail-open):
//!
//! - [`DecompError::BudgetExceeded`] — output exceeded the configured limit
//! - [`DecompError::FormatError`] — input is not valid for the declared format
//! - [`DecompError::TruncatedInput`] — input stream ended prematurely
//! - [`DecompError::IoError`] — generic I/O error during decompression

use std::io::Read;

/// Compression format identifier passed from C.
///
/// Matches the generated FFI constants: gzip=0, deflate=1, Brotli=2.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Format {
    /// gzip (RFC 1952)
    Gzip = crate::ffi::abi::MARKDOWN_FORMAT_GZIP,
    /// deflate: zlib-wrapped (RFC 1950) by default, with raw RFC 1951 fallback
    Deflate = crate::ffi::abi::MARKDOWN_FORMAT_DEFLATE,
    /// brotli (RFC 7932)
    Brotli = crate::ffi::abi::MARKDOWN_FORMAT_BROTLI,
}

impl Format {
    /// Convert a raw u8 format code to a `Format` enum variant.
    ///
    /// Returns `None` for unrecognized format codes.
    pub fn from_u8(v: u8) -> Option<Self> {
        match v {
            crate::ffi::abi::MARKDOWN_FORMAT_GZIP => Some(Self::Gzip),
            crate::ffi::abi::MARKDOWN_FORMAT_DEFLATE => Some(Self::Deflate),
            crate::ffi::abi::MARKDOWN_FORMAT_BROTLI => Some(Self::Brotli),
            _ => None,
        }
    }
}

/// Error categories for bounded decompression.
///
/// Each variant maps to a distinct FFI error code so the C caller can
/// distinguish failure modes for metrics and logging.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecompError {
    /// Decompressed output exceeded the configured budget.
    BudgetExceeded,
    /// Input data is not valid for the declared compression format.
    FormatError(String),
    /// Input stream ended prematurely (incomplete compressed data).
    TruncatedInput(String),
    /// Generic I/O error during decompression.
    IoError(String),
    /// Decompressed output exceeded the per-layer expansion ratio.
    RatioExceeded,
}

impl DecompError {
    /// Return the FFI error category code for this error.
    ///
    /// These codes match the `FFIDecompResult.error_category` field and
    /// the DECOMP_CATEGORY_* constants in the C header:
    /// - 101 = budget_exceeded
    /// - 102 = format_error
    /// - 103 = truncated_input
    /// - 104 = io_error
    /// - 106 = ratio_exceeded
    pub fn error_category(&self) -> u32 {
        match self {
            Self::BudgetExceeded => crate::ffi::abi::DECOMP_CATEGORY_BUDGET_EXCEEDED,
            Self::FormatError(_) => crate::ffi::abi::DECOMP_CATEGORY_FORMAT_ERROR,
            Self::TruncatedInput(_) => crate::ffi::abi::DECOMP_CATEGORY_TRUNCATED_INPUT,
            Self::IoError(_) => crate::ffi::abi::DECOMP_CATEGORY_IO_ERROR,
            Self::RatioExceeded => crate::ffi::abi::DECOMP_CATEGORY_RATIO_EXCEEDED,
        }
    }
}

/// Result of a bounded decompression operation.
#[derive(Debug)]
pub struct DecompResult {
    /// Decompressed output bytes.
    pub output: Vec<u8>,
}

/// Decompress `input` using the specified `format` with a hard `budget` limit.
///
/// The function reads compressed data incrementally and stops immediately
/// when the decompressed output would exceed `budget` bytes.
///
/// # Arguments
///
/// * `input` - Compressed input bytes
/// * `format` - Compression format (gzip, deflate, or brotli)
/// * `budget` - Maximum allowed decompressed output size in bytes
///
/// # Returns
///
/// `Ok(DecompResult)` on success, or `Err(DecompError)` with a specific
/// error category on failure.
///
/// # Examples
///
/// ```
/// use nginx_markdown_converter::decompress::{decompress_bounded, Format};
///
/// // Decompress gzip data with a 1MB budget
/// let compressed = vec![/* gzip bytes */];
/// let result = decompress_bounded(&compressed, Format::Gzip, 1_048_576, 0);
/// ```
pub fn decompress_bounded(
    input: &[u8],
    format: Format,
    budget: usize,
    ratio: u64,
) -> Result<DecompResult, DecompError> {
    match format {
        Format::Gzip => decompress_gzip(input, budget, ratio),
        Format::Deflate => decompress_deflate(input, budget, ratio),
        Format::Brotli => decompress_brotli(input, budget, ratio),
    }
}

/// Read from a decoder into a budget-limited buffer.
///
/// Returns the filled buffer on success, or an appropriate `DecompError`
/// if the budget is exceeded or an I/O error occurs. When `ratio` is
/// non-zero, every non-empty compressed input is also capped at
/// `input_len * ratio`; exceeding that ceiling is classified as
/// `RatioExceeded` (distinct from the absolute-budget `BudgetExceeded`),
/// matching the multi-layer chain decoder semantics.
fn read_bounded<R: Read>(
    mut reader: R,
    budget: usize,
    input_len: usize,
    ratio: u64,
) -> Result<Vec<u8>, DecompError> {
    let mut output = Vec::new();
    let chunk_size = 8192.min(budget.saturating_add(1));
    let mut buf = vec![0u8; chunk_size];

    /* Ratio ceiling applies to every non-empty compressed input. */
    let ratio_cap = if ratio > 0 && input_len > 0 {
        usize::try_from((input_len as u64).saturating_mul(ratio)).unwrap_or(usize::MAX)
    } else {
        usize::MAX
    };

    loop {
        match reader.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                /* Saturating add: with an extreme budget (usize::MAX)
                 * output.len() + n could overflow on the check itself
                 *.  BudgetExceeded is returned either way. */
                if output.len().saturating_add(n) > budget {
                    return Err(DecompError::BudgetExceeded);
                }
                /* Keep the absolute budget as the first classification. */
                if output.len().saturating_add(n) > ratio_cap {
                    return Err(DecompError::RatioExceeded);
                }
                /* Reserve exactly the needed capacity to prevent Vec
                 * growth strategy from allocating beyond the budget. */
                let needed = output
                    .len()
                    .checked_add(n)
                    .ok_or(DecompError::BudgetExceeded)?;
                if output.capacity() < needed {
                    let additional = needed - output.len();
                    let max_reserve = budget.saturating_sub(output.len());
                    let reserve_amount = additional.min(max_reserve);
                    output
                        .try_reserve_exact(reserve_amount)
                        .map_err(|_| DecompError::BudgetExceeded)?;
                }
                output.extend_from_slice(&buf[..n]);
            }
            Err(e) => {
                return Err(classify_io_error(e));
            }
        }
    }

    Ok(output)
}

/// Classify a `std::io::Error` into the appropriate `DecompError` variant.
fn classify_io_error(e: std::io::Error) -> DecompError {
    let msg = e.to_string();
    match e.kind() {
        std::io::ErrorKind::InvalidData | std::io::ErrorKind::InvalidInput => {
            // Distinguish format errors from truncation by inspecting the
            // message.  "corrupt deflate stream" is data corruption (bad
            // block type / checksum), NOT truncation: classifying it as
            // TruncatedInput mislabels the telemetry and, more importantly,
            // defeats the raw-deflate retry (which only fires
            // on FormatError).  Genuine truncation surfaces as "unexpected
            // eof"/"truncat"/"premature".
            let lower = msg.to_lowercase();
            if lower.contains("truncat")
                || lower.contains("unexpected eof")
                || lower.contains("premature")
            {
                DecompError::TruncatedInput(msg)
            } else {
                DecompError::FormatError(msg)
            }
        }
        std::io::ErrorKind::UnexpectedEof => DecompError::TruncatedInput(msg),
        _ => DecompError::IoError(msg),
    }
}

/// Classify a flate2 in-memory decompression error into the FFI categories.
fn classify_deflate_error(e: flate2::DecompressError) -> DecompError {
    let msg = e.to_string();
    let lower = msg.to_lowercase();
    // Same policy as classify_io_error: "corrupt deflate stream" is a format
    // error (bad data), not truncation.
    if lower.contains("truncat") || lower.contains("unexpected eof") || lower.contains("premature")
    {
        DecompError::TruncatedInput(msg)
    } else {
        DecompError::FormatError(msg)
    }
}

/// Decompress gzip data with budget enforcement.
fn decompress_gzip(input: &[u8], budget: usize, ratio: u64) -> Result<DecompResult, DecompError> {
    use flate2::read::MultiGzDecoder;

    if input.is_empty() {
        return Err(DecompError::TruncatedInput(
            "empty input for gzip decompression".to_string(),
        ));
    }

    let decoder = MultiGzDecoder::new(input);
    let output = read_bounded(decoder, budget, input.len(), ratio)?;
    Ok(DecompResult { output })
}

/// True when `input` begins with a plausible zlib header (RFC 1950).
///
/// This is a heuristic, not a proof: a raw deflate stream (RFC 1951) whose
/// first byte carries non-zero alignment padding bits can legally begin with
/// `0x78 0x9c` (a stored block), which also satisfies the CMF/FLG check.
/// Callers therefore treat a wrapped-mode decode failure with zero output as
/// a signal to retry in raw mode (see [`decompress_deflate`]).  This mirrors
/// the C decompressor's sniffing decision (zlib header present -> MAX_WBITS,
/// otherwise -MAX_WBITS); the C full-buffer path applies the same
/// wrapped-then-raw retry, while the C streaming path documents the sniff as
/// heuristic without retry.
fn has_zlib_header(input: &[u8]) -> bool {
    if input.len() < 2 {
        return false;
    }
    let cmf = input[0];
    let flg = input[1];
    (cmf & 0x0F) == 8 && (cmf >> 4) <= 7 && ((u16::from(cmf) << 8) | u16::from(flg)) % 31 == 0
}

/// Decompress deflate data (zlib-wrapped RFC 1950 or raw RFC 1951) with
/// budget enforcement.
///
/// The deflate layer accepts both framings: zlib-wrapped (RFC 1950, the
/// HTTP-standard form) is selected when the input begins with a plausible
/// zlib header; otherwise the input is decoded as raw deflate (RFC 1951) for
/// support for servers that provide raw deflate.  Because the header
/// sniff is a heuristic (see [`has_zlib_header`]), a wrapped-mode decode that fails with
/// a format error before producing any output is retried as raw RFC 1951.
/// The retry is skipped once output has been produced (the framing was
/// effectively confirmed) or when the failure is truncation/budget/I/O, so
/// error classification stays intact.  This matches the C full-buffer
/// decompressor's FORMAT_ERROR fallback so the decoding paths accept the
/// same inputs.
fn decompress_deflate(
    input: &[u8],
    budget: usize,
    ratio: u64,
) -> Result<DecompResult, DecompError> {
    if input.is_empty() {
        return Err(DecompError::TruncatedInput(
            "empty input for deflate decompression".to_string(),
        ));
    }

    let mut output = Vec::new();
    let zlib_wrapped = has_zlib_header(input);

    match deflate_decode_into(input, budget, zlib_wrapped, ratio, &mut output) {
        Ok(()) => Ok(DecompResult { output }),
        Err(e) => retry_raw_deflate(input, budget, zlib_wrapped, ratio, output, e),
    }
}

fn retry_raw_deflate(
    input: &[u8],
    budget: usize,
    zlib_wrapped: bool,
    ratio: u64,
    mut output: Vec<u8>,
    err: DecompError,
) -> Result<DecompResult, DecompError> {
    let should_retry =
        zlib_wrapped && output.is_empty() && matches!(err, DecompError::FormatError(_));
    if !should_retry {
        return Err(err);
    }
    output.clear();
    deflate_decode_into(input, budget, false, ratio, &mut output)?;
    Ok(DecompResult { output })
}

/// Run a bounded flate2 decode (wrapped or raw) appending into `output`.
fn deflate_decode_into(
    input: &[u8],
    budget: usize,
    zlib_wrapped: bool,
    ratio: u64,
    output: &mut Vec<u8>,
) -> Result<(), DecompError> {
    use flate2::{Decompress, FlushDecompress, Status};

    let mut decoder = Decompress::new(zlib_wrapped);
    let chunk_size = 8192.min(budget.saturating_add(1)).max(1);
    let mut buf = vec![0u8; chunk_size];

    loop {
        let consumed = usize::try_from(decoder.total_in())
            .map_err(|_| DecompError::IoError("deflate input byte counter overflow".to_string()))?;
        if consumed > input.len() {
            return Err(DecompError::IoError(
                "deflate decoder consumed beyond input length".to_string(),
            ));
        }

        let before_in = decoder.total_in();
        let before_out = decoder.total_out();
        let flush = if consumed == input.len() {
            FlushDecompress::Finish
        } else {
            FlushDecompress::None
        };
        let status = decoder
            .decompress(&input[consumed..], &mut buf, flush)
            .map_err(classify_deflate_error)?;
        let consumed_now = decoder.total_in().saturating_sub(before_in);
        let produced_now = usize::try_from(decoder.total_out().saturating_sub(before_out))
            .map_err(|_| DecompError::BudgetExceeded)?;

        if produced_now > 0 {
            append_deflate_output(output, &buf[..produced_now], budget, ratio, input.len())?;
        }

        match status {
            Status::StreamEnd => return Ok(()),
            Status::Ok | Status::BufError => {
                if consumed_now == 0 && produced_now == 0 {
                    return Err(DecompError::TruncatedInput(
                        "deflate stream ended before final block".to_string(),
                    ));
                }
            }
        }
    }
}

/// Append `produced` bytes to `output` under the cumulative decompression
/// budget and per-layer ratio ceiling. Grows the buffer only when the
/// budget allows it.
fn append_deflate_output(
    output: &mut Vec<u8>,
    produced: &[u8],
    budget: usize,
    ratio: u64,
    input_len: usize,
) -> Result<(), DecompError> {
    let needed = output
        .len()
        .checked_add(produced.len())
        .ok_or(DecompError::BudgetExceeded)?;
    if needed > budget {
        return Err(DecompError::BudgetExceeded);
    }
    /* Ratio ceiling mirrors read_bounded and applies to every non-empty
     * compressed input on the deflate single-layer path. */
    if ratio > 0 && input_len > 0 {
        let ratio_cap =
            usize::try_from((input_len as u64).saturating_mul(ratio)).unwrap_or(usize::MAX);
        if needed > ratio_cap {
            return Err(DecompError::RatioExceeded);
        }
    }
    if output.capacity() < needed {
        output
            .try_reserve_exact(needed - output.len())
            .map_err(|_| DecompError::BudgetExceeded)?;
    }
    output.extend_from_slice(produced);
    Ok(())
}

/// Decompress brotli data with budget enforcement.
fn decompress_brotli(input: &[u8], budget: usize, ratio: u64) -> Result<DecompResult, DecompError> {
    if input.is_empty() {
        return Err(DecompError::TruncatedInput(
            "empty input for brotli decompression".to_string(),
        ));
    }

    let decoder = brotli::Decompressor::new(input, 4096);
    let output = read_bounded(decoder, budget, input.len(), ratio)?;
    Ok(DecompResult { output })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ffi::{MARKDOWN_FORMAT_BROTLI, MARKDOWN_FORMAT_DEFLATE, MARKDOWN_FORMAT_GZIP};

    /// Helper: compress data with gzip.
    fn gzip_compress(data: &[u8]) -> Vec<u8> {
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;

        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(data).unwrap();
        encoder.finish().unwrap()
    }

    /// Helper: compress data with deflate.
    fn deflate_compress(data: &[u8]) -> Vec<u8> {
        use flate2::Compression;
        use flate2::write::ZlibEncoder;
        use std::io::Write;

        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(data).unwrap();
        encoder.finish().unwrap()
    }

    /// Helper: compress data with raw deflate (RFC 1951, no zlib header).
    fn raw_deflate_compress(data: &[u8]) -> Vec<u8> {
        use flate2::Compression;
        use flate2::write::DeflateEncoder;
        use std::io::Write;

        let mut encoder = DeflateEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(data).unwrap();
        encoder.finish().unwrap()
    }

    /// Helper: compress data with brotli.
    fn brotli_compress(data: &[u8]) -> Vec<u8> {
        let mut output = Vec::new();
        let mut writer = brotli::CompressorWriter::new(&mut output, 4096, 6, 22);
        std::io::Write::write_all(&mut writer, data).unwrap();
        drop(writer);
        output
    }

    #[test]
    fn gzip_decompresses_within_budget() {
        let original = b"Hello, world! This is a test of bounded decompression.";
        let compressed = gzip_compress(original);
        let result = decompress_bounded(&compressed, Format::Gzip, 1024, 0).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn gzip_decompresses_all_concatenated_members() {
        let first = b"<html><body>";
        let second = b"joined</body></html>";
        let mut compressed = gzip_compress(first);
        compressed.extend_from_slice(&gzip_compress(second));

        let result = decompress_bounded(&compressed, Format::Gzip, 1024, 0).unwrap();

        assert_eq!(
            result.output,
            [first.as_slice(), second.as_slice()].concat()
        );
    }

    #[test]
    fn concatenated_gzip_budget_is_response_wide() {
        let first = b"first member";
        let second = b"second member";
        let mut compressed = gzip_compress(first);
        compressed.extend_from_slice(&gzip_compress(second));

        let result =
            decompress_bounded(&compressed, Format::Gzip, first.len() + second.len() - 1, 0);

        assert_eq!(result.unwrap_err(), DecompError::BudgetExceeded);
    }

    #[test]
    fn gzip_rejects_truncated_later_member() {
        let mut compressed = gzip_compress(b"complete first member");
        let mut second = gzip_compress(b"truncated second member");
        second.truncate(second.len() - 4);
        compressed.extend_from_slice(&second);

        let result = decompress_bounded(&compressed, Format::Gzip, 1024, 0);

        assert_eq!(result.unwrap_err().error_category(), 103);
    }

    #[test]
    fn deflate_decompresses_within_budget() {
        let original = b"Deflate test data for bounded decompression.";
        let compressed = deflate_compress(original);
        let result = decompress_bounded(&compressed, Format::Deflate, 1024, 0).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn deflate_large_output_decompresses_across_internal_chunks() {
        // Integer literals per AGENTS.md Rule 17 (byte-char literals confuse
        // lizard's brace counting).
        let original = vec![68u8; 65_536];
        let compressed = deflate_compress(&original);
        let result = decompress_bounded(&compressed, Format::Deflate, original.len(), 0).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn brotli_decompresses_within_budget() {
        let original = b"Brotli test data for bounded decompression.";
        let compressed = brotli_compress(original);
        let result = decompress_bounded(&compressed, Format::Brotli, 1024, 0).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn gzip_budget_exceeded() {
        // Create data larger than budget
        let original = vec![65u8; 10_000];
        let compressed = gzip_compress(&original);
        let result = decompress_bounded(&compressed, Format::Gzip, 100, 0);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err, DecompError::BudgetExceeded);
        assert_eq!(err.error_category(), 101);
    }

    #[test]
    fn deflate_budget_exceeded() {
        let original = vec![66u8; 10_000];
        let compressed = deflate_compress(&original);
        let result = decompress_bounded(&compressed, Format::Deflate, 100, 0);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err(), DecompError::BudgetExceeded);
    }

    #[test]
    fn brotli_budget_exceeded() {
        let original = vec![b'C'; 10_000];
        let compressed = brotli_compress(&original);
        let result = decompress_bounded(&compressed, Format::Brotli, 100, 0);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err(), DecompError::BudgetExceeded);
    }

    fn high_ratio_payload() -> Vec<u8> {
        vec![b'Z'; 64 * 1024]
    }

    #[test]
    fn gzip_and_brotli_high_ratio_within_budget() {
        let original = high_ratio_payload();
        let gzip = gzip_compress(&original);
        let brotli = brotli_compress(&original);

        let gzip_result = decompress_bounded(&gzip, Format::Gzip, original.len(), 0).unwrap();
        let brotli_result = decompress_bounded(&brotli, Format::Brotli, original.len(), 0).unwrap();

        assert_eq!(gzip_result.output, original);
        assert_eq!(brotli_result.output, original);
    }

    #[test]
    fn gzip_and_brotli_high_ratio_budget_exceeded() {
        let original = high_ratio_payload();
        let gzip = gzip_compress(&original);
        let brotli = brotli_compress(&original);

        let gzip_result = decompress_bounded(&gzip, Format::Gzip, 128, 0);
        let brotli_result = decompress_bounded(&brotli, Format::Brotli, 128, 0);

        assert_eq!(gzip_result.unwrap_err(), DecompError::BudgetExceeded);
        assert_eq!(brotli_result.unwrap_err(), DecompError::BudgetExceeded);
    }

    #[test]
    fn gzip_ratio_exceeded() {
        /* A wire body that expands far beyond input_len * ratio must be
         * rejected as RatioExceeded on the single-layer path, matching the
         * multi-layer chain semantics (ratio 2 caps output at 2x input).
         * Use a deterministic payload whose decompressed output far exceeds
         * 2x the encoded input. */
        let original: Vec<u8> = (0..4096u32).map(|i| (i % 251) as u8).collect();
        let compressed = gzip_compress(&original);
        let result = decompress_bounded(&compressed, Format::Gzip, 10 * 1024 * 1024, 2);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err, DecompError::RatioExceeded);
        assert_eq!(err.error_category(), 106);
    }

    #[test]
    fn deflate_ratio_exceeded() {
        let original: Vec<u8> = (0..4096u32).map(|i| (i % 251) as u8).collect();
        let compressed = deflate_compress(&original);
        let result = decompress_bounded(&compressed, Format::Deflate, 10 * 1024 * 1024, 2);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err, DecompError::RatioExceeded);
        assert_eq!(err.error_category(), 106);
    }

    #[test]
    fn brotli_ratio_exceeded() {
        let original: Vec<u8> = (0..4096u32).map(|i| (i % 251) as u8).collect();
        let compressed = brotli_compress(&original);
        let result = decompress_bounded(&compressed, Format::Brotli, 10 * 1024 * 1024, 2);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err, DecompError::RatioExceeded);
        assert_eq!(err.error_category(), 106);
    }

    #[test]
    fn ratio_enforced_for_small_input() {
        /* Small compressed inputs are subject to the same ratio ceiling. */
        let original = vec![b'T'; 100];
        let compressed = gzip_compress(&original);
        let result = decompress_bounded(&compressed, Format::Gzip, 10 * 1024, 2);
        assert_eq!(result.unwrap_err(), DecompError::RatioExceeded);
    }

    #[test]
    fn gzip_format_error_on_invalid_input() {
        let garbage = b"this is not gzip data at all";
        let result = decompress_bounded(garbage, Format::Gzip, 1024, 0);
        assert!(result.is_err());
        let err = result.unwrap_err();
        // Should be FormatError or TruncatedInput depending on how flate2 reports it
        assert!(
            err.error_category() == 102 || err.error_category() == 103,
            "Expected format_error(102) or truncated(103), got {}",
            err.error_category()
        );
    }

    #[test]
    fn deflate_format_error_on_invalid_input() {
        let garbage = b"\xff\xfe\xfd\xfc\xfb\xfa";
        let result = decompress_bounded(garbage, Format::Deflate, 1024, 0);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.error_category() == 102 || err.error_category() == 103,
            "Expected format_error(102) or truncated(103), got {}",
            err.error_category()
        );
    }

    #[test]
    fn deflate_accepts_raw_rfc1951_input() {
        let original = b"<html><body>raw deflate payload</body></html>";
        let raw = raw_deflate_compress(original);
        // Sanity: the raw stream must not carry a zlib header.
        assert!(
            !has_zlib_header(&raw),
            "raw deflate must not look zlib-wrapped"
        );
        let result = decompress_bounded(&raw, Format::Deflate, 4096, 0).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn deflate_still_accepts_zlib_wrapped_rfc1950_input() {
        let original = b"<html><body>standard zlib-wrapped payload</body></html>";
        let zlib = deflate_compress(original);
        assert!(
            has_zlib_header(&zlib),
            "zlib-wrapped must carry a zlib header"
        );
        let result = decompress_bounded(&zlib, Format::Deflate, 4096, 0).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn has_zlib_header_is_heuristic_for_raw_block_headers() {
        // Standard compressors zero the alignment padding bits of the first
        // stored-block header byte, so their raw output starts with 0x00-0x07
        // and cannot satisfy CMF == 8.  A non-compliant encoder may set those
        // padding bits, making the first byte 0x78 with BTYPE=00 stored —
        // which, followed by 0x9c, satisfies the CMF/FLG check.  The sniff is
        // therefore a heuristic: `78 9c` IS classified as zlib-wrapped here,
        // and decompress_deflate recovers via the raw retry (see
        // raw_deflate_with_zlib_like_prefix_is_retried_as_raw).
        // Standard-compressor raw output starts with 0x00-0x07 (padding bits
        // zeroed), so the sniff must reject those; 78 9c is classified as
        // zlib-wrapped and recovered by the raw retry.  Asserted one by one
        // (no `&[` slice-literal arrays — they confuse lizard's brace
        // counting, AGENTS.md Rule 17).
        assert!(!has_zlib_header(b"\x00abc"));
        assert!(!has_zlib_header(b"\x01abc"));
        assert!(!has_zlib_header(b"\x03abc"));
        assert!(has_zlib_header(b"\x78\x9c"));
        // Too-short input cannot be classified as zlib-wrapped.
        assert!(!has_zlib_header(b""));
        assert!(!has_zlib_header(b"\x78"));
    }

    #[test]
    fn raw_deflate_with_zlib_like_prefix_is_retried_as_raw() {
        // A legal raw RFC 1951 stored-block stream whose first bytes 78 9c
        // satisfy the zlib CMF/FLG check:
        //   0x78    = BFINAL=0, BTYPE=00 (stored), padding bits 0b01111
        //   9c 00   = LEN 156 (little-endian)
        //   63 ff   = NLEN = 0xff63 = 65535 - 156
        // followed by the 156-byte payload and a final empty stored block.
        // The heuristic sniff must not reject it: the wrapped decode fails
        // before producing any output and the raw retry must succeed.
        let payload = vec![65u8; 156];
        let mut stream = Vec::with_capacity(166);
        stream.push(0x78);
        stream.push(0x9c);
        stream.push(0x00);
        stream.push(0x63);
        stream.push(0xff);
        stream.extend_from_slice(&payload);
        stream.push(0x01);
        stream.push(0x00);
        stream.push(0x00);
        stream.push(0xff);
        stream.push(0xff);
        assert!(
            has_zlib_header(&stream),
            "fixture must satisfy the CMF/FLG sniff"
        );
        let result = decompress_bounded(&stream, Format::Deflate, 4096, 0).unwrap();
        assert_eq!(result.output, payload);
    }

    #[test]
    fn deflate_raw_truncated_input() {
        let original = b"payload that gets cut off mid-stream";
        let raw = raw_deflate_compress(original);
        let truncated = &raw[..raw.len() / 2];
        let result = decompress_bounded(truncated, Format::Deflate, 4096, 0);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.error_category() == 102 || err.error_category() == 103,
            "Expected format_error(102) or truncated(103), got {}",
            err.error_category()
        );
    }

    #[test]
    fn brotli_format_error_on_invalid_input() {
        let garbage = b"not brotli data";
        let result = decompress_bounded(garbage, Format::Brotli, 1024, 0);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.error_category() == 102
                || err.error_category() == 103
                || err.error_category() == 104,
            "Expected format/truncated/io error, got {}",
            err.error_category()
        );
    }

    #[test]
    fn gzip_truncated_input() {
        let original = b"Some data to compress";
        let compressed = gzip_compress(original);
        // Truncate the compressed data
        let truncated = &compressed[..compressed.len() / 2];
        let result = decompress_bounded(truncated, Format::Gzip, 1024, 0);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.error_category() == 102 || err.error_category() == 103,
            "Expected format_error(102) or truncated(103), got {}",
            err.error_category()
        );
    }

    #[test]
    fn deflate_truncated_input() {
        let original = vec![b'D'; 10_000];
        let compressed = deflate_compress(&original);
        let truncated = &compressed[..compressed.len() / 2];
        let result = decompress_bounded(truncated, Format::Deflate, 20_000, 0);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(
            err.error_category(),
            103,
            "Expected truncated(103), got {}",
            err.error_category()
        );
    }

    #[test]
    fn empty_input_returns_truncated() {
        let result = decompress_bounded(&[], Format::Gzip, 1024, 0);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().error_category(), 103);

        let result = decompress_bounded(&[], Format::Deflate, 1024, 0);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().error_category(), 103);

        let result = decompress_bounded(&[], Format::Brotli, 1024, 0);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().error_category(), 103);
    }

    #[test]
    fn format_from_u8_valid() {
        assert_eq!(Format::from_u8(MARKDOWN_FORMAT_GZIP), Some(Format::Gzip));
        assert_eq!(
            Format::from_u8(MARKDOWN_FORMAT_DEFLATE),
            Some(Format::Deflate)
        );
        assert_eq!(
            Format::from_u8(MARKDOWN_FORMAT_BROTLI),
            Some(Format::Brotli)
        );

        assert_eq!(Format::Gzip as u8, MARKDOWN_FORMAT_GZIP);
        assert_eq!(Format::Deflate as u8, MARKDOWN_FORMAT_DEFLATE);
        assert_eq!(Format::Brotli as u8, MARKDOWN_FORMAT_BROTLI);
    }

    #[test]
    fn format_from_u8_invalid() {
        assert_eq!(Format::from_u8(3), None);
        assert_eq!(Format::from_u8(255), None);
    }

    #[test]
    fn budget_exactly_at_limit() {
        // Data that decompresses to exactly the budget size should succeed
        let original = vec![b'X'; 100];
        let compressed = gzip_compress(&original);
        let result = decompress_bounded(&compressed, Format::Gzip, 100, 0).unwrap();
        assert_eq!(result.output.len(), 100);
    }

    #[test]
    fn budget_one_byte_over() {
        // Data that decompresses to budget+1 should fail
        let original = vec![b'Y'; 101];
        let compressed = gzip_compress(&original);
        let result = decompress_bounded(&compressed, Format::Gzip, 100, 0);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err(), DecompError::BudgetExceeded);
    }

    #[test]
    fn large_compression_ratio_within_budget() {
        // Highly compressible data (all zeros) — ratio >10x but within budget
        let original = vec![0u8; 50_000];
        let compressed = gzip_compress(&original);
        assert!(
            compressed.len() < 500,
            "Expected high compression ratio, got {} bytes",
            compressed.len()
        );
        let result = decompress_bounded(&compressed, Format::Gzip, 100_000, 0).unwrap();
        assert_eq!(result.output.len(), 50_000);
    }

    #[test]
    fn error_category_codes_are_distinct() {
        assert_eq!(DecompError::BudgetExceeded.error_category(), 101);
        assert_eq!(
            DecompError::FormatError("test".to_string()).error_category(),
            102
        );
        assert_eq!(
            DecompError::TruncatedInput("test".to_string()).error_category(),
            103
        );
        assert_eq!(
            DecompError::IoError("test".to_string()).error_category(),
            104
        );
    }

    /// Decompressing a gzip-compressed empty payload should succeed with
    /// zero-length output.  This validates that the decompressor does not
    /// treat a valid empty result as an error (regression test for F-02).
    #[test]
    fn gzip_empty_payload_decompresses_to_empty() {
        let compressed = gzip_compress(b"");
        let result = decompress_bounded(&compressed, Format::Gzip, 1024, 0).unwrap();
        assert_eq!(result.output.len(), 0);
    }

    /// Decompressing a deflate-compressed empty payload should succeed
    /// with zero-length output.
    #[test]
    fn deflate_empty_payload_decompresses_to_empty() {
        let compressed = deflate_compress(b"");
        let result = decompress_bounded(&compressed, Format::Deflate, 1024, 0).unwrap();
        assert_eq!(result.output.len(), 0);
    }

    /// Decompressing a brotli-compressed empty payload should succeed
    /// with zero-length output.
    #[test]
    fn brotli_empty_payload_decompresses_to_empty() {
        let compressed = brotli_compress(b"");
        let result = decompress_bounded(&compressed, Format::Brotli, 1024, 0).unwrap();
        assert_eq!(result.output.len(), 0);
    }
}
