#![no_main]

//! Fuzz target for the Content-Encoding chain parser.
//!
//! Feeds arbitrary bytes as a `Content-Encoding` header value into
//! [`nginx_markdown_converter::encoding::parse_encoding_chain`].
//!
//! Invariants checked:
//! - No panics on any input; `Malformed`, `UnknownToken`, and
//!   `DepthExceeded` are the only reject classifications and never produce a
//!   layer list (the `Err` arms are exhaustive at compile time).
//! - On `Ok`, every layer is one of {gzip, deflate, br, identity} (exhaustive
//!   match is a compile-time oracle) and the non-identity layer count never
//!   exceeds `MAX_DECODER_DEPTH`.

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::encoding::{
    ChainParseError, Encoding, MAX_DECODER_DEPTH, parse_encoding_chain,
};

fuzz_target!(|data: &[u8]| {
    match parse_encoding_chain(data) {
        Err(ChainParseError::Malformed) => {
            /* Malformed grammar: no decoder layer list may be produced. */
        }
        Err(ChainParseError::UnknownToken | ChainParseError::DepthExceeded) => {
            /* Capability-bypass classifications: syntactically valid but
             * rejected chains are a legitimate outcome. */
        }
        Ok(layers) => {
            /* Every accepted layer must be one of the four supported
             * encodings; the exhaustive match proves it at compile time. */
            for enc in &layers {
                match enc {
                    Encoding::Gzip | Encoding::Deflate | Encoding::Br | Encoding::Identity => {}
                }
            }
            /* Depth invariant: non-identity layers are bounded. */
            let non_identity = layers.iter().filter(|e| **e != Encoding::Identity).count();
            assert!(
                non_identity <= MAX_DECODER_DEPTH,
                "{} non-identity layers exceed MAX_DECODER_DEPTH ({})",
                non_identity,
                MAX_DECODER_DEPTH
            );
        }
    }
});
