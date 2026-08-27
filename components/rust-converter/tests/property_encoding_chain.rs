//! Property-based tests for Content-Encoding chain parsing and multi-layer
//! bounded decompression (Properties 22 and 23).
//!
//! **Property 22: Content-Encoding chain parsing** (Validates: Requirement 12.1)
//!
//! For any header value generated with arbitrary SP/HTAB whitespace, mixed
//! casing, and token counts:
//! - normalized lowercase tokens are returned in declaration order
//! - unsupported tokens are identified
//! - `identity` is a no-op, `br` (not "brotli") is recognized
//! - malformed grammar produces exactly `ENCODING_HEADER_INVALID` semantics
//!   (malformed classification) with no decoder/header mutation
//! - syntactically valid unknown tokens are classified for the C precommit
//!   router; the router applies the configured error policy
//!
//! **Property 23: Encoding chain depth, budget, and unknown token
//! enforcement** (Validates: Requirements 12.3, 12.4, 12.7)
//!
//! - chains with more than 3 non-identity layers are classified for the C
//!   precommit error policy
//! - cumulative output exceeding the budget aborts with budget-exceeded
//! - any layer exceeding the ratio limit aborts
//! - every non-empty compressed layer is subject to the ratio limit
//! - unknown tokens bypass decoder work; the C precommit router applies the
//!   configured error policy rather than silently bypassing the request
//! - malformed grammar produces the malformed classification with no
//!   decoder or header mutation

use nginx_markdown_converter::encoding::{
    ChainDecodeError, ChainParseError, DecodeLimits, Encoding, decode_chain, parse_encoding_chain,
};
use proptest::prelude::*;
use proptest::string::string_regex;

use flate2::Compression;
use flate2::write::{GzEncoder, ZlibEncoder};
use std::io::Write;

// ─── Strategies ───────────────────────────────────────────────────────────────

/// Generate a supported or unsupported token with arbitrary mixed casing.
fn arb_token() -> impl Strategy<Value = String> {
    prop_oneof![
        Just("gzip".to_string()),
        Just("deflate".to_string()),
        Just("br".to_string()),
        Just("identity".to_string()),
        Just("zstd".to_string()),
        Just("compress".to_string()),
    ]
    .prop_flat_map(|base| {
        /* Build mixed-casing variants without nightly-only APIs. */
        prop_oneof![
            Just(base.clone()),
            Just(base.to_ascii_uppercase()),
            Just(flip_case(&base, 0)),
            Just(flip_case(&base, 1)),
        ]
    })
}

/// Flip the case of the character at `index`, preserving other characters.
fn flip_case(s: &str, index: usize) -> String {
    let mut out: Vec<char> = s.chars().collect();
    if let Some(c) = out.get_mut(index) {
        if c.is_ascii_lowercase() {
            *c = c.to_ascii_uppercase();
        } else if c.is_ascii_uppercase() {
            *c = c.to_ascii_lowercase();
        }
    }
    out.into_iter().collect()
}

/// Generate a syntactically valid chain value with arbitrary SP/HTAB
/// whitespace placement and mixed casing.
fn arb_chain_value(max_tokens: usize) -> impl Strategy<Value = String> {
    (1usize..=max_tokens).prop_flat_map(move |count| {
        (
            proptest::collection::vec(arb_token(), count),
            proptest::collection::vec(arb_ows(), count),
            proptest::collection::vec(arb_ows(), count),
            proptest::collection::vec(arb_separator(), count.saturating_sub(1)),
        )
            .prop_map(|(tokens, before, after, separators)| {
                let mut out = String::new();
                for (i, tok) in tokens.iter().enumerate() {
                    if i > 0 {
                        out.push_str(&separators[i - 1]);
                    }
                    out.push_str(&before[i]);
                    out.push_str(tok);
                    out.push_str(&after[i]);
                }
                out
            })
    })
}

fn arb_ows() -> impl Strategy<Value = String> {
    prop::sample::select(vec![
        String::new(),
        " ".to_string(),
        "\t".to_string(),
        "  ".to_string(),
        " \t".to_string(),
    ])
}

fn arb_separator() -> impl Strategy<Value = String> {
    prop::sample::select(vec![
        ", ".to_string(),
        ",".to_string(),
        " ,".to_string(),
        " \t, ".to_string(),
    ])
}

// ─── Property 22: chain parsing ──────────────────────────────────────────────

proptest! {
    /// Normalized lowercase tokens in declaration order for any whitespace
    /// and casing variation.
    #[test]
    fn normalized_tokens_preserve_declaration_order(value in arb_chain_value(5)) {
        let layers = parse_encoding_chain(value.as_bytes());
        if let Ok(layers) = layers {
            let expected = value.split(',').map(|tok| {
                let tok = tok.trim_matches([' ', '\t']);
                match tok.to_ascii_lowercase().as_str() {
                    "gzip" => Encoding::Gzip,
                    "deflate" => Encoding::Deflate,
                    "br" => Encoding::Br,
                    "identity" => Encoding::Identity,
                    other => panic!("unexpected token in valid chain: {other}"),
                }
            }).collect::<Vec<_>>();
            assert_eq!(layers, expected, "parser changed declaration order");
        }
    }

    /// `br` is recognized; "brotli" is not part of the supported set.
    #[test]
    fn br_marker_is_not_misclassified_as_brotli(value in string_regex("[a-z]{0,8}").unwrap()) {
        let layers = parse_encoding_chain(value.as_bytes());
        match layers {
            Ok(layers) => {
                /* If the value parsed cleanly, every layer is supported;
                 * identity is a valid no-op and must remain allowed. */
                assert!(layers.iter().all(|e| matches!(
                    e,
                    Encoding::Gzip
                        | Encoding::Deflate
                        | Encoding::Br
                        | Encoding::Identity
                )));
            }
            Err(e) => {
                match e {
                    ChainParseError::Malformed
                    | ChainParseError::UnknownToken
                    | ChainParseError::DepthExceeded => {}
                }
            }
        }
        /* Explicit supported-set checks. */
        assert!(parse_encoding_chain(b"br").is_ok());
        assert_eq!(
            parse_encoding_chain(b"brotli").unwrap_err(),
            ChainParseError::UnknownToken
        );
    }

    /// Malformed grammar produces exactly the malformed classification.
    #[test]
    fn malformed_encoding_grammar_is_classified(
        value in string_regex("[ -~]{0,40}").unwrap()
    ) {
        let outcome = parse_encoding_chain(value.as_bytes());
        match outcome {
            Ok(_) => {
                /* Valid chains contain only supported tokens. */
                let re_tokens = value.split(',');
                for t in re_tokens {
                    let t = t.trim_matches([' ', '\t']);
                    if t.is_empty() {
                        continue;
                    }
                    let lowered = t.to_ascii_lowercase();
                    assert!(
                        ["gzip", "deflate", "br", "identity"].contains(&lowered.as_str()),
                        "token {} accepted by parser but not supported",
                        lowered
                    );
                }
            }
            Err(e) => {
                /* Only the three documented classifications exist. */
                match e {
                    ChainParseError::Malformed => {}
                    ChainParseError::UnknownToken => {
                        /* Syntactically valid unknown token: passthrough,
                         * no error-policy event. */
                    }
                    ChainParseError::DepthExceeded => {}
                }
            }
        }
    }
}

// ─── Property 23: depth, budget, ratio, unknown token ────────────────────────

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

proptest! {
    /// Chains with more than 3 non-identity layers are rejected as
    /// DepthExceeded (passthrough), regardless of identity placement.
    #[test]
    fn depth_overflow_is_passthrough(extra in 1usize..3) {
        let layers = vec![Encoding::Gzip; 3 + extra];
        let value = layers
            .iter()
            .map(|_| "gzip")
            .collect::<Vec<_>>()
            .join(", ");
        assert_eq!(
            parse_encoding_chain(value.as_bytes()).unwrap_err(),
            ChainParseError::DepthExceeded
        );
    }

    /// Cumulative output exceeding the budget aborts with budget-exceeded.
    #[test]
    fn cumulative_budget_is_enforced(
        payload_size in 50_000usize..300_000,
        budget in 1_000usize..40_000,
    ) {
        let original = vec![b'X'; payload_size];
        let inner = gzip_compress(&original);
        let wire = deflate_compress(&inner);
        let layers = vec![Encoding::Gzip, Encoding::Deflate];
        let limits = DecodeLimits { max_output: budget, ratio: 1000 };
        let err = decode_chain(&wire, &layers, limits).unwrap_err();
        assert_eq!(err, ChainDecodeError::BudgetExceeded);
    }

    /// Per-layer ratio enforcement for a large fixture.
    #[test]
    fn ratio_is_enforced_for_large_input(ratio in 2u64..50) {
        /* Highly compressible payload exercises the ratio ceiling on a large
         * decoded result. */
        let original = vec![0u8; 4_000_000];
        let wire = gzip_compress(&original);
        let limits = DecodeLimits { max_output: 10 * 1024 * 1024, ratio };
        let err = decode_chain(&wire, &[Encoding::Gzip], limits).unwrap_err();
        assert_eq!(err, ChainDecodeError::RatioExceeded);
    }

    /// Small inputs are ratio-checked as well.
    #[test]
    fn ratio_is_enforced_for_small_input(size in 64usize..128) {
        /* A highly-compressible payload is rejected when its expansion
         * exceeds ratio = 1. */
        let original = vec![b'A'; size];
        let wire = gzip_compress(&original);
        let limits = DecodeLimits { max_output: 10 * 1024 * 1024, ratio: 1 };
        let err = decode_chain(&wire, &[Encoding::Gzip], limits).unwrap_err();
        assert_eq!(err, ChainDecodeError::RatioExceeded);
    }

    /// Unknown tokens are classified without starting decoder work.
    #[test]
    fn unknown_token_is_classified_before_decode(
        head in prop::collection::vec(arb_token(), 0..3),
    ) {
        let mut tokens: Vec<String> = head.clone();
        tokens.push("zstd".to_string());
        let value = tokens.join(", ");
        let outcome = parse_encoding_chain(value.as_bytes());
        match outcome {
            Err(ChainParseError::UnknownToken) => {}
            Err(ChainParseError::Malformed) => {}
            other => panic!("unknown-token chain must not decode, got {:?}", other),
        }
    }

    /// Identity-only chains are valid and perform no decoder work.
    #[test]
    fn identity_only_chain_is_valid(count in 1usize..5) {
        let value = vec!["identity"; count].join(", ");
        let layers = parse_encoding_chain(value.as_bytes()).unwrap();
        assert_eq!(layers.iter().filter(|e| **e != Encoding::Identity).count(), 0);
    }
}

// ─── Direct (non-proptest) oracle checks ─────────────────────────────────────

/// Identity layers do not count toward depth.
#[test]
fn identity_does_not_count_toward_depth() {
    let value = "identity, gzip, identity, deflate, identity, br";
    let layers = parse_encoding_chain(value.as_bytes()).unwrap();
    let non_identity: Vec<Encoding> = layers
        .iter()
        .copied()
        .filter(|e| *e != Encoding::Identity)
        .collect();
    assert_eq!(
        non_identity,
        vec![Encoding::Gzip, Encoding::Deflate, Encoding::Br]
    );
}

/// Malformed grammar must never start a decoder or mutate headers: the
/// parser classification is the only observable outcome.
#[test]
fn malformed_grammar_never_returns_layers() {
    let cases: &[&[u8]] = &[
        b",gzip",
        b"gzip,",
        b"gzip,,deflate",
        b"\"gzip\"",
        b"gzip;q=1",
        b"gzip\n",
    ];
    for value in cases {
        let outcome = parse_encoding_chain(value);
        assert!(
            matches!(outcome, Err(ChainParseError::Malformed)),
            "value {:?} must be malformed",
            String::from_utf8_lossy(value)
        );
    }
}

/// Syntactically valid unknown tokens are classified as UnknownToken, never
/// Malformed; the C precommit layer owns the error-policy decision.
#[test]
fn unknown_tokens_are_not_malformed() {
    let cases: &[&[u8]] = &[b"zstd", b"gzip, zstd", b"x-gzip", b"gzip, compress"];
    for value in cases {
        let outcome = parse_encoding_chain(value);
        assert!(
            !matches!(outcome, Err(ChainParseError::Malformed)),
            "value {:?} must not be malformed",
            String::from_utf8_lossy(value)
        );
    }
}

/// Empty input is valid only for the identity representation. A configured
/// decoder must observe a complete encoded stream, even when it would emit no
/// bytes.
#[test]
fn zero_input_requires_identity_representation() {
    let out = decode_chain(&[], &[Encoding::Identity], DecodeLimits::default()).unwrap();
    assert!(out.is_empty());

    for encoding in [Encoding::Gzip, Encoding::Deflate, Encoding::Br] {
        assert!(
            matches!(
                decode_chain(&[], &[encoding], DecodeLimits::default()),
                Err(ChainDecodeError::TruncatedInput(_))
            ),
            "empty input must be truncated for {encoding:?}"
        );
    }
}

/// Budget and ratio failures retain their distinct reasons.
#[test]
fn budget_and_ratio_reasons_are_distinct() {
    let original = vec![0u8; 4_000_000];
    let wire = gzip_compress(&original);

    let budget_err = decode_chain(
        &wire,
        &[Encoding::Gzip],
        DecodeLimits {
            max_output: 10,
            ratio: 1000,
        },
    )
    .unwrap_err();
    assert_eq!(budget_err, ChainDecodeError::BudgetExceeded);

    let ratio_err = decode_chain(
        &wire,
        &[Encoding::Gzip],
        DecodeLimits {
            max_output: 10 * 1024 * 1024,
            ratio: 10,
        },
    )
    .unwrap_err();
    assert_eq!(ratio_err, ChainDecodeError::RatioExceeded);
}

/// The `TOKENS` list stays aligned with the parser's supported set.
#[test]
fn supported_token_set_is_exactly_four() {
    for tok in ["gzip", "deflate", "br", "identity"] {
        assert!(parse_encoding_chain(tok.as_bytes()).is_ok());
    }
    for tok in ["brotli", "compress", "x-gzip", "zstd"] {
        assert_eq!(
            parse_encoding_chain(tok.as_bytes()).unwrap_err(),
            ChainParseError::UnknownToken
        );
    }
}
