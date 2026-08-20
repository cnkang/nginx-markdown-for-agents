//! Content-Encoding chain parsing and multi-layer bounded decompression.
//!
//! This module implements the 0.9.2 Content-Encoding chain contract:
//!
//! - Parses the Content-Encoding header value as a comma-separated list of
//!   encoding tokens, trimming only leading/trailing SP and HTAB.
//! - Rejects malformed grammar (leading/trailing/consecutive commas, empty
//!   tokens, quoted tokens, token parameters, control characters, and tokens
//!   over 128 bytes) as `ChainParseError::Malformed`.
//! - Compares tokens case-insensitively against `{gzip, deflate, br,
//!   identity}`; `identity` is a no-op layer removed from the effective
//!   decoder list.
//! - Enforces a maximum effective decoder depth of 3 non-identity layers.
//! - Decodes supported chains in reverse application order with a cumulative
//!   absolute output budget and a per-layer expansion ratio limit, both using
//!   overflow-safe integer arithmetic.
//!
//! All parsing and validation is pure (no I/O); bounded full-buffer decoding
//! is provided for multi-layer chains. Single-layer streaming decompression
//! remains owned by the C module.

/// Maximum effective decoder depth: non-identity layers.
pub const MAX_DECODER_DEPTH: usize = 3;

/// Maximum accepted token length in bytes.
pub const MAX_TOKEN_LEN: usize = 128;

/// Historical compatibility value for the former ratio activation threshold.
/// Ratio enforcement now applies to every non-empty compressed layer; the
/// value is retained for downstream source compatibility only.
pub const RATIO_ACTIVATION_THRESHOLD: usize = 256;

/// Content-Encoding token.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Encoding {
    /// gzip (RFC 1952)
    Gzip = 0,
    /// zlib-wrapped deflate (RFC 1950 carrying RFC 1951 data)
    Deflate = 1,
    /// brotli (RFC 7932)
    Br = 2,
    /// identity: validated no-op layer
    Identity = 3,
}

impl Encoding {
    /// Match a token case-insensitively against the supported set.
    pub fn from_token(token: &[u8]) -> Option<Self> {
        if token.eq_ignore_ascii_case(b"gzip") {
            Some(Self::Gzip)
        } else if token.eq_ignore_ascii_case(b"deflate") {
            Some(Self::Deflate)
        } else if token.eq_ignore_ascii_case(b"br") {
            Some(Self::Br)
        } else if token.eq_ignore_ascii_case(b"identity") {
            Some(Self::Identity)
        } else {
            None
        }
    }
}

/// Chain parse failure classification.
///
/// - `Malformed` maps to the canonical `ENCODING_HEADER_INVALID` reason with
///   `stage=decompression` and `error_origin=format` during outer precommit
///   routing; no decoder starts and no response header is mutated.
/// - `UnknownToken` and `DepthExceeded` are parser classifications. The C
///   precommit router sends both through the configured error policy before
///   any decoder starts; only an explicit `pass` policy forwards the original
///   response unchanged.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChainParseError {
    /// Malformed grammar (comma error, empty/quoted token, token parameter,
    /// control character, or overlong token).
    Malformed,
    /// Syntactically valid token outside the supported set.
    UnknownToken,
    /// More than `MAX_DECODER_DEPTH` non-identity layers.
    DepthExceeded,
}

/// Skip optional whitespace (SP/HTAB).
fn skip_ows(value: &[u8], mut i: usize) -> usize {
    while i < value.len() && (value[i] == b' ' || value[i] == b'\t') {
        i += 1;
    }
    i
}

/// Scan a token starting at `token_start`, returning the token-end index or
/// `None` when the grammar is malformed.
///
/// SP/HTAB terminate the token only when followed by a comma or the end of
/// the value; SP/HTAB inside a token and any non-tchar character are
/// malformed.
fn scan_token(value: &[u8], token_start: usize) -> Option<usize> {
    let mut i = token_start;
    while i < value.len() {
        let c = value[i];
        match c {
            b',' => return Some(i),
            b' ' | b'\t' => return token_end_after_ows(value, i),
            c if !is_tchar(c) => return None,
            _ => i += 1,
        }
    }
    Some(i)
}

fn token_end_after_ows(value: &[u8], whitespace_start: usize) -> Option<usize> {
    let mut i = whitespace_start;
    while i < value.len() && (value[i] == b' ' || value[i] == b'\t') {
        i += 1;
    }
    (i == value.len() || value[i] == b',').then_some(whitespace_start)
}

fn parse_encoding_token(
    value: &[u8],
    token_start: usize,
) -> Result<(Encoding, usize), ChainParseError> {
    let token_end = scan_token(value, token_start).ok_or(ChainParseError::Malformed)?;
    let token = &value[token_start..token_end];
    if token.is_empty() || token.len() > MAX_TOKEN_LEN {
        return Err(ChainParseError::Malformed);
    }
    let encoding = Encoding::from_token(token).ok_or(ChainParseError::UnknownToken)?;
    Ok((encoding, token_end))
}

/// Parse a concatenated Content-Encoding header value into encoding layers
/// in application (declaration) order.
///
/// Multiple Content-Encoding header fields are expected to be concatenated in
/// received field order by the caller; this function treats the value as a
/// single comma-separated grammar.
///
/// On success the returned vector preserves application order and includes
/// `Identity` layers. Callers remove `Identity` before decoding.
pub fn parse_encoding_chain(value: &[u8]) -> Result<Vec<Encoding>, ChainParseError> {
    let mut layers = Vec::new();
    let mut non_identity = 0usize;
    let mut i = skip_ows(value, 0);

    if i == value.len() {
        /* A Content-Encoding header whose value is empty or OWS-only is an
         * empty token, which is malformed grammar. */
        return Err(ChainParseError::Malformed);
    }

    loop {
        let (encoding, token_end) = parse_encoding_token(value, i)?;
        layers.push(encoding);
        if encoding != Encoding::Identity {
            non_identity += 1;
        }

        /* Consume trailing OWS and expect either a comma or the end. */
        i = skip_ows(value, token_end);
        if i == value.len() {
            break;
        }
        if value[i] != b',' {
            return Err(ChainParseError::Malformed);
        }
        i += 1;

        /* Consume leading OWS of the next token. */
        i = skip_ows(value, i);
        if i == value.len() {
            /* Trailing comma. */
            return Err(ChainParseError::Malformed);
        }
    }

    if non_identity > MAX_DECODER_DEPTH {
        return Err(ChainParseError::DepthExceeded);
    }

    Ok(layers)
}

/// RFC 7230 token character: tchar.
fn is_tchar(c: u8) -> bool {
    matches!(
        c,
        b'!' | b'#'
            | b'$'
            | b'%'
            | b'&'
            | b'\''
            | b'*'
            | b'+'
            | b'-'
            | b'.'
            | b'^'
            | b'_'
            | b'`'
            | b'|'
            | b'~'
            | b'0'..=b'9'
            | b'a'..=b'z'
            | b'A'..=b'Z'
    )
}

/// Multi-layer decode failure classification.
///
/// Budget and ratio exceedances map to `RESOURCE_LIMIT` in precommit and to
/// `COMMITTED + ERROR` with `reason=resource_limit` and
/// `error_origin=memory_budget` after commit. Format and truncation failures
/// keep their distinct decompression reasons.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ChainDecodeError {
    /// Cumulative output exceeded the configured `decompressed_size` budget.
    BudgetExceeded,
    /// A non-identity layer's decoded output exceeded its expansion ratio.
    RatioExceeded,
    /// Input is not valid for the declared format.
    FormatError(String),
    /// Input stream ended prematurely.
    TruncatedInput(String),
    /// Generic I/O error during decompression.
    IoError(String),
}

/// Effective decode limits for a chain.
#[derive(Debug, Clone, Copy)]
pub struct DecodeLimits {
    /// Cumulative absolute output budget in bytes (`decompressed_size`).
    pub max_output: usize,
    /// Per-layer expansion ratio (`decompression_ratio`, e.g. 100 = 100:1).
    pub ratio: u64,
}

impl Default for DecodeLimits {
    fn default() -> Self {
        Self {
            max_output: 10 * 1024 * 1024,
            ratio: 100,
        }
    }
}

/// Decode a multi-layer chain in reverse application order.
///
/// `layers` must be in application order (as returned by
/// [`parse_encoding_chain`]) and must contain no `Identity` entries
/// (callers strip them after validation). `identity` layers perform no
/// decoder work and consume neither ratio nor output budget.
///
/// The cumulative output budget applies across every non-identity
/// intermediate output. The per-layer ratio check applies to every non-empty
/// compressed input; zero compressed input is accepted for the initial decoder
/// only. An empty intermediate output cannot feed another decoder layer.
///
/// **Empty-input contract (empty-input):** an empty wire body (`encoded` is empty)
/// is a legal empty payload regardless of the declared chain — there is
/// nothing to decode, so the call succeeds with an empty output instead of
/// classifying the empty input as truncation. This intentionally differs
/// from the single-format decompressors (`decompress_gzip`/`decompress_deflate`/
/// `decompress_brotli`), which classify an empty compressed input as
/// `TruncatedInput`; the chain decoder treats a zero-byte body as "no
/// content" per HTTP semantics. Callers that need strict single-format
/// truncation semantics must use the single-format entry points directly.
pub fn decode_chain(
    encoded: &[u8],
    layers: &[Encoding],
    limits: DecodeLimits,
) -> Result<Vec<u8>, ChainDecodeError> {
    /* The initial layer borrows the caller's compressed buffer instead of
     * cloning it; ownership transfers only after the first decoder layer
     * produces its owned output (avoids the
     * full-input copy that made peak memory unpredictable). */
    let mut current: std::borrow::Cow<'_, [u8]> = std::borrow::Cow::Borrowed(encoded);
    let mut cumulative = 0usize;
    let mut has_decoded_layer = false;

    /* An empty body is a legal empty payload: there is nothing to decode,
     * so return success immediately instead of classifying the empty
     * input as truncation on a later layer (empty-input). */
    if current.is_empty() {
        return Ok(Vec::new());
    }

    /* Decode from the outermost layer (last declared) inward. */
    for enc in layers.iter().rev() {
        if *enc == Encoding::Identity {
            continue;
        }
        if has_decoded_layer && current.is_empty() {
            return Err(ChainDecodeError::TruncatedInput(
                "decoder input is empty after a prior layer".to_string(),
            ));
        }
        let input_len = current.len();
        let remaining_budget = limits
            .max_output
            .checked_sub(cumulative)
            .ok_or(ChainDecodeError::BudgetExceeded)?;
        /* Pre-decoder ratio ceiling: bound the decompression work itself,
         * not only the accepted result. Every non-empty compressed layer is
         * allowed at most input_len * ratio bytes of output; hitting that
         * ceiling is classified as RatioExceeded while the absolute
         * cumulative ceiling keeps BudgetExceeded. The post-decode
         * validate_decoded_layer() below remains as a belt-and-suspenders
         * check with identical semantics. */
        let ratio_budget = if limits.ratio > 0 && input_len > 0 {
            usize::try_from((input_len as u64).saturating_mul(limits.ratio)).unwrap_or(usize::MAX)
        } else {
            usize::MAX
        };
        let layer_budget = remaining_budget.min(ratio_budget);
        let ratio_capped = layer_budget < remaining_budget;
        let layer_limits = DecodeLimits {
            max_output: layer_budget,
            ratio: limits.ratio,
        };
        let decoded = match decode_layer(&current, *enc, layer_limits) {
            Ok(decoded) => decoded,
            Err(ChainDecodeError::BudgetExceeded) if ratio_capped => {
                /* The decoder hit the ratio-derived ceiling before the
                 * absolute budget: report the ratio violation, not a
                 * generic budget error, so metrics/observability keep the
                 * distinction. */
                return Err(ChainDecodeError::RatioExceeded);
            }
            Err(e) => return Err(e),
        };
        validate_decoded_layer(input_len, decoded.len(), &mut cumulative, limits)?;
        current = std::borrow::Cow::Owned(decoded);
        has_decoded_layer = true;
    }

    Ok(current.into_owned())
}

fn decode_layer(
    input: &[u8],
    encoding: Encoding,
    limits: DecodeLimits,
) -> Result<Vec<u8>, ChainDecodeError> {
    if input.is_empty() {
        return Ok(Vec::new());
    }
    let format = match encoding {
        Encoding::Gzip => crate::decompress::Format::Gzip,
        Encoding::Deflate => crate::decompress::Format::Deflate,
        Encoding::Br => crate::decompress::Format::Brotli,
        Encoding::Identity => unreachable!("identity is skipped above"),
    };
    crate::decompress::decompress_bounded(input, format, limits.max_output, limits.ratio)
        .map_err(map_decomp_error)
        .map(|result| result.output)
}

/// Enforce the per-layer expansion ratio on a decoded layer's output and
/// accumulate the cumulative output budget.
///
/// Ratio semantics: the per-layer ratio ceiling applies to every non-empty
/// compressed `input_len`; there is no small-input exemption. (Zero-length
/// input is accepted for the initial decoder only; a non-empty decoded output
/// from an empty input is rejected as a ratio violation.)
fn validate_decoded_layer(
    input_len: usize,
    decoded_len: usize,
    cumulative: &mut usize,
    limits: DecodeLimits,
) -> Result<(), ChainDecodeError> {
    if limits.ratio > 0 && input_len > 0 {
        let allowed = (input_len as u64).saturating_mul(limits.ratio);
        if (decoded_len as u64) > allowed {
            return Err(ChainDecodeError::RatioExceeded);
        }
    } else if input_len == 0 && decoded_len != 0 {
        return Err(ChainDecodeError::RatioExceeded);
    }

    *cumulative = cumulative
        .checked_add(decoded_len)
        .ok_or(ChainDecodeError::BudgetExceeded)?;
    if *cumulative > limits.max_output {
        return Err(ChainDecodeError::BudgetExceeded);
    }
    Ok(())
}

/// Map a single-layer decompression error to the chain classification.
fn map_decomp_error(e: crate::decompress::DecompError) -> ChainDecodeError {
    match e {
        crate::decompress::DecompError::BudgetExceeded => ChainDecodeError::BudgetExceeded,
        crate::decompress::DecompError::FormatError(msg) => ChainDecodeError::FormatError(msg),
        crate::decompress::DecompError::TruncatedInput(msg) => {
            ChainDecodeError::TruncatedInput(msg)
        }
        crate::decompress::DecompError::IoError(msg) => ChainDecodeError::IoError(msg),
        crate::decompress::DecompError::RatioExceeded => ChainDecodeError::RatioExceeded,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decompress::{Format, decompress_bounded};
    use flate2::Compression;
    use flate2::write::{GzEncoder, ZlibEncoder};
    use std::io::Write;

    fn gzip_compress(data: &[u8]) -> Vec<u8> {
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(data).unwrap();
        encoder.finish().unwrap()
    }

    fn deflate_compress(data: &[u8]) -> Vec<u8> {
        let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(data).unwrap();
        encoder.finish().unwrap()
    }

    fn brotli_compress(data: &[u8]) -> Vec<u8> {
        let mut output = Vec::new();
        let mut writer = brotli::CompressorWriter::new(&mut output, 4096, 6, 22);
        std::io::Write::write_all(&mut writer, data).unwrap();
        drop(writer);
        output
    }

    fn strip_identity(layers: &[Encoding]) -> Vec<Encoding> {
        layers
            .iter()
            .copied()
            .filter(|e| *e != Encoding::Identity)
            .collect()
    }

    #[test]
    fn parses_single_token() {
        let layers = parse_encoding_chain(b"gzip").unwrap();
        assert_eq!(layers, vec![Encoding::Gzip]);
    }

    #[test]
    fn parses_case_insensitive() {
        let layers = parse_encoding_chain(b"GZIP, DeFlaTe, BR").unwrap();
        assert_eq!(
            layers,
            vec![Encoding::Gzip, Encoding::Deflate, Encoding::Br]
        );
    }

    #[test]
    fn trims_only_sp_htab() {
        let layers = parse_encoding_chain(b" \tgzip\t, deflate ").unwrap();
        assert_eq!(layers, vec![Encoding::Gzip, Encoding::Deflate]);
    }

    #[test]
    fn identity_is_valid_noop() {
        let layers = parse_encoding_chain(b"identity").unwrap();
        assert_eq!(layers, vec![Encoding::Identity]);
        let layers = parse_encoding_chain(b"gzip, identity").unwrap();
        assert_eq!(layers, vec![Encoding::Gzip, Encoding::Identity]);
    }

    #[test]
    fn br_not_brotli() {
        assert!(parse_encoding_chain(b"br").is_ok());
        assert!(parse_encoding_chain(b"brotli").is_err());
        assert_eq!(
            parse_encoding_chain(b"brotli").unwrap_err(),
            ChainParseError::UnknownToken
        );
    }

    #[test]
    fn leading_comma_malformed() {
        assert_eq!(
            parse_encoding_chain(b",gzip").unwrap_err(),
            ChainParseError::Malformed
        );
    }

    #[test]
    fn trailing_comma_malformed() {
        assert_eq!(
            parse_encoding_chain(b"gzip,").unwrap_err(),
            ChainParseError::Malformed
        );
    }

    #[test]
    fn consecutive_commas_malformed() {
        assert_eq!(
            parse_encoding_chain(b"gzip,,deflate").unwrap_err(),
            ChainParseError::Malformed
        );
    }

    #[test]
    fn empty_token_malformed() {
        assert_eq!(
            parse_encoding_chain(b"").unwrap_err(),
            ChainParseError::Malformed
        );
        assert_eq!(
            parse_encoding_chain(b"  \t ").unwrap_err(),
            ChainParseError::Malformed
        );
    }

    #[test]
    fn quoted_token_malformed() {
        assert_eq!(
            parse_encoding_chain(b"\"gzip\"").unwrap_err(),
            ChainParseError::Malformed
        );
    }

    #[test]
    fn token_parameter_malformed() {
        assert_eq!(
            parse_encoding_chain(b"gzip;q=1").unwrap_err(),
            ChainParseError::Malformed
        );
        assert_eq!(
            parse_encoding_chain(b"gzip=1").unwrap_err(),
            ChainParseError::Malformed
        );
    }

    #[test]
    fn control_character_malformed() {
        assert_eq!(
            parse_encoding_chain(b"gzip\n").unwrap_err(),
            ChainParseError::Malformed
        );
        assert_eq!(
            parse_encoding_chain(b"gzi\x00p").unwrap_err(),
            ChainParseError::Malformed
        );
    }

    #[test]
    fn overlong_token_malformed() {
        let mut token = b"g".repeat(129);
        token.push(112); /* 'p' — avoid confusing the Rust lizard parser. */
        assert_eq!(
            parse_encoding_chain(&token).unwrap_err(),
            ChainParseError::Malformed
        );
    }

    #[test]
    fn unknown_token() {
        assert_eq!(
            parse_encoding_chain(b"gzip, zstd").unwrap_err(),
            ChainParseError::UnknownToken
        );
    }

    #[test]
    fn depth_exceeded() {
        assert_eq!(
            parse_encoding_chain(b"gzip, deflate, br, gzip").unwrap_err(),
            ChainParseError::DepthExceeded
        );
        /* identity does not count toward depth: 3 non-identity + 2 identity
         * layers remain valid. */
        let layers = parse_encoding_chain(b"identity, gzip, identity, deflate, br").unwrap();
        assert_eq!(
            strip_identity(&layers),
            vec![Encoding::Gzip, Encoding::Deflate, Encoding::Br]
        );
    }

    #[test]
    fn max_depth_valid() {
        let layers = parse_encoding_chain(b"gzip, deflate, br").unwrap();
        assert_eq!(
            layers,
            vec![Encoding::Gzip, Encoding::Deflate, Encoding::Br]
        );
    }

    #[test]
    fn tab_inside_token_malformed() {
        assert_eq!(
            parse_encoding_chain(b"gzip\tdeflate").unwrap_err(),
            ChainParseError::Malformed
        );
    }

    #[test]
    fn decode_single_gzip_layer() {
        let original = b"<html><body>hello chain</body></html>";
        let compressed = gzip_compress(original);
        let out = decode_chain(&compressed, &[Encoding::Gzip], DecodeLimits::default()).unwrap();
        assert_eq!(out, original);
    }

    #[test]
    fn decode_two_layers_reverse_order() {
        let original = b"<html><body>two layer chain</body></html>";
        /* Application order [Gzip, Deflate]: gzip applied first (innermost),
         * deflate applied second (outermost); wire = deflate(gzip(content)).
         * Decoding runs in reverse: deflate (outermost) first, then gzip. */
        let inner = gzip_compress(original);
        let outer = deflate_compress(&inner);
        let layers = vec![Encoding::Gzip, Encoding::Deflate];
        let out = decode_chain(&outer, &layers, DecodeLimits::default()).unwrap();
        assert_eq!(out, original);
    }

    #[test]
    fn decode_three_layers() {
        let original = b"<html><body>three layer chain</body></html>";
        /* Application order [Gzip, Deflate, Br]: wire = br(deflate(gzip(
         * content))); decode br first, then deflate, then gzip. */
        let a = gzip_compress(original);
        let b = deflate_compress(&a);
        let outer = brotli_compress(&b);
        let layers = vec![Encoding::Gzip, Encoding::Deflate, Encoding::Br];
        let out = decode_chain(&outer, &layers, DecodeLimits::default()).unwrap();
        assert_eq!(out, original);
    }

    #[test]
    fn identity_layer_is_transparent() {
        let original = b"<html><body>identity test</body></html>";
        let compressed = gzip_compress(original);
        let layers = vec![Encoding::Gzip, Encoding::Identity];
        let out = decode_chain(&compressed, &layers, DecodeLimits::default()).unwrap();
        assert_eq!(out, original);
    }

    #[test]
    fn cumulative_budget_across_layers() {
        let original = vec![b'X'; 200_000];
        /* Wire = deflate(gzip(content)) for application order
         * [Gzip, Deflate]; each intermediate output is ~200000 bytes. */
        let inner = gzip_compress(&original);
        let outer = deflate_compress(&inner);
        let layers = vec![Encoding::Gzip, Encoding::Deflate];
        /* Budget below each intermediate output must fail. */
        let limits = DecodeLimits {
            max_output: 100_000,
            ratio: 10_000,
        };
        let err = decode_chain(&outer, &layers, limits).unwrap_err();
        assert_eq!(err, ChainDecodeError::BudgetExceeded);
    }

    #[test]
    fn decode_chain_empty_input_is_empty_payload() {
        /* empty-input: an empty body with declared encodings is a legal empty
         * payload — decode_chain returns an empty result instead of
         * misclassifying it as truncation. */
        let layers = vec![Encoding::Gzip, Encoding::Deflate];
        let out = decode_chain(b"", &layers, DecodeLimits::default()).unwrap();
        assert!(
            out.is_empty(),
            "empty input must decode to an empty payload"
        );
    }

    #[test]
    fn ratio_exceeded_above_threshold() {
        /* Highly compressible payload: ratio far above 100:1, with a
         * compressed size above the historical fixture threshold. */
        let original = vec![0u8; 1_000_000];
        let compressed = gzip_compress(&original);
        assert!(
            compressed.len() >= 256,
            "expected compressed size above legacy fixture threshold, got {}",
            compressed.len()
        );
        let limits = DecodeLimits {
            max_output: 10 * 1024 * 1024,
            ratio: 10,
        };
        let err = decode_chain(&compressed, &[Encoding::Gzip], limits).unwrap_err();
        assert_eq!(err, ChainDecodeError::RatioExceeded);
    }

    #[test]
    fn ratio_enforced_for_small_input() {
        let original = vec![0u8; 100_000];
        let compressed = gzip_compress(&original);
        assert!(compressed.len() < RATIO_ACTIVATION_THRESHOLD);
        /* A small compressed input is still subject to the configured ratio;
         * ratio=1 must reject the highly expanding layer. */
        let limits = DecodeLimits {
            max_output: 10 * 1024 * 1024,
            ratio: 1,
        };
        let err = decode_chain(&compressed, &[Encoding::Gzip], limits).unwrap_err();
        assert_eq!(err, ChainDecodeError::RatioExceeded);
    }

    #[test]
    fn ratio_ok_within_limits() {
        let original = vec![0u8; 1_000_000];
        let compressed = gzip_compress(&original);
        assert!(compressed.len() >= 256);
        let limits = DecodeLimits {
            max_output: 10 * 1024 * 1024,
            ratio: 5000,
        };
        let out = decode_chain(&compressed, &[Encoding::Gzip], limits).unwrap();
        assert_eq!(out.len(), original.len());
    }

    #[test]
    fn zero_input_zero_output() {
        /* Empty compressed input is a valid empty decode (no decoder work). */
        let out = decode_chain(&[], &[Encoding::Gzip], DecodeLimits::default()).unwrap();
        assert!(out.is_empty());
    }

    #[test]
    fn empty_intermediate_layer_is_truncated() {
        let compressed = gzip_compress(&[]);
        let err = decode_chain(
            &compressed,
            &[Encoding::Deflate, Encoding::Gzip],
            DecodeLimits::default(),
        )
        .unwrap_err();
        match err {
            ChainDecodeError::TruncatedInput(_) => {}
            other => panic!("expected truncated input, got {:?}", other),
        }
    }

    #[test]
    fn format_error_classified() {
        let garbage = b"this is not gzip data at all";
        let err = decode_chain(garbage, &[Encoding::Gzip], DecodeLimits::default()).unwrap_err();
        match err {
            ChainDecodeError::FormatError(_) | ChainDecodeError::TruncatedInput(_) => {}
            other => panic!("expected format/truncated error, got {:?}", other),
        }
    }

    #[test]
    fn truncated_stream_classified() {
        let original = vec![b'D'; 10_000];
        let compressed = gzip_compress(&original);
        let truncated = &compressed[..compressed.len() / 2];
        let err = decode_chain(truncated, &[Encoding::Gzip], DecodeLimits::default()).unwrap_err();
        match err {
            ChainDecodeError::TruncatedInput(_) => {}
            other => panic!("expected truncated error, got {:?}", other),
        }
    }

    #[test]
    fn gzip_multi_member_accumulates_in_layer() {
        let first = vec![b'A'; 60_000];
        let second = vec![b'B'; 60_000];
        let mut compressed = gzip_compress(&first);
        compressed.extend_from_slice(&gzip_compress(&second));
        let limits = DecodeLimits {
            max_output: 100_000,
            ratio: 1000,
        };
        let err = decode_chain(&compressed, &[Encoding::Gzip], limits).unwrap_err();
        assert_eq!(err, ChainDecodeError::BudgetExceeded);
    }

    #[test]
    fn chain_decode_matches_single_decode() {
        let original = b"<html><body>parity</body></html>";
        let compressed = gzip_compress(original);
        let single = decompress_bounded(&compressed, Format::Gzip, 10 * 1024 * 1024, 0)
            .unwrap()
            .output;
        let chained =
            decode_chain(&compressed, &[Encoding::Gzip], DecodeLimits::default()).unwrap();
        assert_eq!(chained, single);
    }

    #[test]
    fn identity_only_chain_returns_input_unchanged() {
        /* decode(identity, input) == input.  With no
         * effective decoder layers the chain decoder returns the input
         * unchanged. */
        let input = b"abc";
        let out = decode_chain(input, &[], DecodeLimits::default()).unwrap();
        assert_eq!(out, input);
    }

    #[test]
    fn identity_bracketed_chain_decodes() {
        /* "identity, gzip, identity": identity layers are stripped after
         * parse; decode_chain receives only [Gzip] and decodes the wire
         * payload. */
        let original = b"<html><body>bracketed identity</body></html>";
        let compressed = gzip_compress(original);
        let parsed = parse_encoding_chain(b"identity, gzip, identity").unwrap();
        let layers = strip_identity(&parsed);
        assert_eq!(layers, vec![Encoding::Gzip]);
        let out = decode_chain(&compressed, &layers, DecodeLimits::default()).unwrap();
        assert_eq!(out, original);
    }

    #[test]
    fn ratio_ceiling_classified_before_absolute_budget() {
        /* With the ratio ceiling binding far below the
         * absolute budget, hitting it must classify as RatioExceeded, not
         * BudgetExceeded — proving the ceiling bounds decoder work, not
         * just the accepted result. */
        let original = vec![0u8; 1_000_000];
        let compressed = gzip_compress(&original);
        assert!(compressed.len() >= RATIO_ACTIVATION_THRESHOLD);
        let limits = DecodeLimits {
            max_output: usize::MAX, // absolute budget effectively unbounded
            ratio: 1,               // ratio ceiling ≈ compressed_len bytes
        };
        let err = decode_chain(&compressed, &[Encoding::Gzip], limits).unwrap_err();
        assert_eq!(err, ChainDecodeError::RatioExceeded);
    }

    #[test]
    fn absolute_budget_keeps_budget_exceeded_despite_high_ratio() {
        /* The cumulative absolute budget still classifies as BudgetExceeded
         * when the ratio ceiling is not the binding constraint (the two
         * classifications must stay distinct). */
        let original = vec![0u8; 200_000];
        let compressed = gzip_compress(&original);
        assert!(compressed.len() > 0);
        let limits = DecodeLimits {
            max_output: 100_000,
            ratio: 10_000,
        };
        let err = decode_chain(&compressed, &[Encoding::Gzip], limits).unwrap_err();
        assert_eq!(err, ChainDecodeError::BudgetExceeded);
    }
}
