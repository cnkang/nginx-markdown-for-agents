//! Bounded decompression module.
//!
//! Provides safe, budget-limited decompression for gzip, deflate, and brotli
//! compressed data. The decompression operation terminates immediately when
//! the output exceeds the configured budget, preventing zip-bomb attacks.
//!
//! # Supported Formats
//!
//! - `0` = gzip (RFC 1952)
//! - `1` = deflate (RFC 1951)
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
/// Matches the FFI contract: 0=gzip, 1=deflate, 2=brotli.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Format {
    /// gzip (RFC 1952)
    Gzip = 0,
    /// raw deflate (RFC 1951)
    Deflate = 1,
    /// brotli (RFC 7932)
    Brotli = 2,
}

impl Format {
    /// Convert a raw u8 format code to a `Format` enum variant.
    ///
    /// Returns `None` for unrecognized format codes.
    pub fn from_u8(v: u8) -> Option<Self> {
        match v {
            0 => Some(Self::Gzip),
            1 => Some(Self::Deflate),
            2 => Some(Self::Brotli),
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
    pub fn error_category(&self) -> u32 {
        match self {
            Self::BudgetExceeded => crate::ffi::abi::DECOMP_CATEGORY_BUDGET_EXCEEDED,
            Self::FormatError(_) => crate::ffi::abi::DECOMP_CATEGORY_FORMAT_ERROR,
            Self::TruncatedInput(_) => crate::ffi::abi::DECOMP_CATEGORY_TRUNCATED_INPUT,
            Self::IoError(_) => crate::ffi::abi::DECOMP_CATEGORY_IO_ERROR,
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
/// let result = decompress_bounded(&compressed, Format::Gzip, 1_048_576);
/// ```
pub fn decompress_bounded(
    input: &[u8],
    format: Format,
    budget: usize,
) -> Result<DecompResult, DecompError> {
    match format {
        Format::Gzip => decompress_gzip(input, budget),
        Format::Deflate => decompress_deflate(input, budget),
        Format::Brotli => decompress_brotli(input, budget),
    }
}

/// Read from a decoder into a budget-limited buffer.
///
/// Returns the filled buffer on success, or an appropriate `DecompError`
/// if the budget is exceeded or an I/O error occurs.
fn read_bounded<R: Read>(mut reader: R, budget: usize) -> Result<Vec<u8>, DecompError> {
    let mut output = Vec::new();
    let chunk_size = 8192.min(budget.saturating_add(1));
    let mut buf = vec![0u8; chunk_size];

    loop {
        match reader.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                if output.len() + n > budget {
                    return Err(DecompError::BudgetExceeded);
                }
                /* Reserve exactly the needed capacity to prevent Vec
                 * growth strategy from allocating beyond the budget. */
                let needed = output.len() + n;
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
            // Distinguish format errors from truncation by inspecting the message
            let lower = msg.to_lowercase();
            if lower.contains("truncat")
                || lower.contains("unexpected eof")
                || lower.contains("premature")
                || lower.contains("corrupt deflate stream")
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
    if lower.contains("truncat")
        || lower.contains("unexpected eof")
        || lower.contains("premature")
        || lower.contains("corrupt deflate stream")
    {
        DecompError::TruncatedInput(msg)
    } else {
        DecompError::FormatError(msg)
    }
}

/// Decompress gzip data with budget enforcement.
fn decompress_gzip(input: &[u8], budget: usize) -> Result<DecompResult, DecompError> {
    use flate2::read::GzDecoder;

    if input.is_empty() {
        return Err(DecompError::TruncatedInput(
            "empty input for gzip decompression".to_string(),
        ));
    }

    let decoder = GzDecoder::new(input);
    let output = read_bounded(decoder, budget)?;
    Ok(DecompResult { output })
}

/// Decompress raw deflate data with budget enforcement.
fn decompress_deflate(input: &[u8], budget: usize) -> Result<DecompResult, DecompError> {
    use flate2::{Decompress, FlushDecompress, Status};

    if input.is_empty() {
        return Err(DecompError::TruncatedInput(
            "empty input for deflate decompression".to_string(),
        ));
    }

    let mut decoder = Decompress::new(false);
    let mut output = Vec::new();
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
            let needed = output
                .len()
                .checked_add(produced_now)
                .ok_or(DecompError::BudgetExceeded)?;
            if needed > budget {
                return Err(DecompError::BudgetExceeded);
            }
            if output.capacity() < needed {
                output
                    .try_reserve_exact(needed - output.len())
                    .map_err(|_| DecompError::BudgetExceeded)?;
            }
            output.extend_from_slice(&buf[..produced_now]);
        }

        match status {
            Status::StreamEnd => return Ok(DecompResult { output }),
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

/// Decompress brotli data with budget enforcement.
fn decompress_brotli(input: &[u8], budget: usize) -> Result<DecompResult, DecompError> {
    if input.is_empty() {
        return Err(DecompError::TruncatedInput(
            "empty input for brotli decompression".to_string(),
        ));
    }

    let decoder = brotli::Decompressor::new(input, 4096);
    let output = read_bounded(decoder, budget)?;
    Ok(DecompResult { output })
}

#[cfg(test)]
mod tests {
    use super::*;

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
        let result = decompress_bounded(&compressed, Format::Gzip, 1024).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn deflate_decompresses_within_budget() {
        let original = b"Deflate test data for bounded decompression.";
        let compressed = deflate_compress(original);
        let result = decompress_bounded(&compressed, Format::Deflate, 1024).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn deflate_large_output_decompresses_across_internal_chunks() {
        let original = vec![b'D'; 65_536];
        let compressed = deflate_compress(&original);
        let result = decompress_bounded(&compressed, Format::Deflate, original.len()).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn brotli_decompresses_within_budget() {
        let original = b"Brotli test data for bounded decompression.";
        let compressed = brotli_compress(original);
        let result = decompress_bounded(&compressed, Format::Brotli, 1024).unwrap();
        assert_eq!(result.output, original);
    }

    #[test]
    fn gzip_budget_exceeded() {
        // Create data larger than budget
        let original = vec![b'A'; 10_000];
        let compressed = gzip_compress(&original);
        let result = decompress_bounded(&compressed, Format::Gzip, 100);
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err, DecompError::BudgetExceeded);
        assert_eq!(err.error_category(), 101);
    }

    #[test]
    fn deflate_budget_exceeded() {
        let original = vec![b'B'; 10_000];
        let compressed = deflate_compress(&original);
        let result = decompress_bounded(&compressed, Format::Deflate, 100);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err(), DecompError::BudgetExceeded);
    }

    #[test]
    fn brotli_budget_exceeded() {
        let original = vec![b'C'; 10_000];
        let compressed = brotli_compress(&original);
        let result = decompress_bounded(&compressed, Format::Brotli, 100);
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

        let gzip_result = decompress_bounded(&gzip, Format::Gzip, original.len()).unwrap();
        let brotli_result = decompress_bounded(&brotli, Format::Brotli, original.len()).unwrap();

        assert_eq!(gzip_result.output, original);
        assert_eq!(brotli_result.output, original);
    }

    #[test]
    fn gzip_and_brotli_high_ratio_budget_exceeded() {
        let original = high_ratio_payload();
        let gzip = gzip_compress(&original);
        let brotli = brotli_compress(&original);

        let gzip_result = decompress_bounded(&gzip, Format::Gzip, 128);
        let brotli_result = decompress_bounded(&brotli, Format::Brotli, 128);

        assert_eq!(gzip_result.unwrap_err(), DecompError::BudgetExceeded);
        assert_eq!(brotli_result.unwrap_err(), DecompError::BudgetExceeded);
    }

    #[test]
    fn gzip_format_error_on_invalid_input() {
        let garbage = b"this is not gzip data at all";
        let result = decompress_bounded(garbage, Format::Gzip, 1024);
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
        let result = decompress_bounded(garbage, Format::Deflate, 1024);
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
        let result = decompress_bounded(garbage, Format::Brotli, 1024);
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
        let result = decompress_bounded(truncated, Format::Gzip, 1024);
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
        let result = decompress_bounded(truncated, Format::Deflate, 20_000);
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
        let result = decompress_bounded(&[], Format::Gzip, 1024);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().error_category(), 103);

        let result = decompress_bounded(&[], Format::Deflate, 1024);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().error_category(), 103);

        let result = decompress_bounded(&[], Format::Brotli, 1024);
        assert!(result.is_err());
        assert_eq!(result.unwrap_err().error_category(), 103);
    }

    #[test]
    fn format_from_u8_valid() {
        assert_eq!(Format::from_u8(0), Some(Format::Gzip));
        assert_eq!(Format::from_u8(1), Some(Format::Deflate));
        assert_eq!(Format::from_u8(2), Some(Format::Brotli));
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
        let result = decompress_bounded(&compressed, Format::Gzip, 100).unwrap();
        assert_eq!(result.output.len(), 100);
    }

    #[test]
    fn budget_one_byte_over() {
        // Data that decompresses to budget+1 should fail
        let original = vec![b'Y'; 101];
        let compressed = gzip_compress(&original);
        let result = decompress_bounded(&compressed, Format::Gzip, 100);
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
        let result = decompress_bounded(&compressed, Format::Gzip, 100_000).unwrap();
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
        let result = decompress_bounded(&compressed, Format::Gzip, 1024).unwrap();
        assert_eq!(result.output.len(), 0);
    }

    /// Decompressing a deflate-compressed empty payload should succeed
    /// with zero-length output.
    #[test]
    fn deflate_empty_payload_decompresses_to_empty() {
        let compressed = deflate_compress(b"");
        let result = decompress_bounded(&compressed, Format::Deflate, 1024).unwrap();
        assert_eq!(result.output.len(), 0);
    }

    /// Decompressing a brotli-compressed empty payload should succeed
    /// with zero-length output.
    #[test]
    fn brotli_empty_payload_decompresses_to_empty() {
        let compressed = brotli_compress(b"");
        let result = decompress_bounded(&compressed, Format::Brotli, 1024).unwrap();
        assert_eq!(result.output.len(), 0);
    }
}
