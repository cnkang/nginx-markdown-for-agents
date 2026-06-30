//! Streaming-eligibility decision (spec 49, 0.9.0).
//!
//! This module is the Rust **single source of truth** for whether a request
//! may take the streaming path. It replaces the scattered C eligibility
//! branches in `ngx_http_markdown_streaming_impl.h` with one pure function,
//! [`decide_streaming`], and emits a [`StreamingBlockReason`] for diagnostics
//! whenever streaming is not chosen.
//!
//! # Two orthogonal selectors
//!
//! Per the frozen 0.9.0 config grammar these are **distinct** and must not be
//! conflated:
//!
//! * [`StreamingPolicy`] — `markdown_streaming off|auto|force` — the
//!   *enablement* selector (whether streaming is attempted at all).
//! * [`StreamingEngine`] — `markdown_streaming_engine off|auto|on` — the
//!   *implementation* selector (which streaming backend is used).
//!
//! # Decision order
//!
//! Hard blocks are checked first; they hold regardless of `policy`
//! (including `force`) because they describe conditions under which the
//! streaming path cannot produce a correct response:
//!
//! 1. `304 Not Modified` response → [`StreamingBlockReason::NotModified`]
//! 2. `HEAD` request             → [`StreamingBlockReason::HeadRequest`]
//! 3. `Range` request            → [`StreamingBlockReason::RangeRequest`]
//! 4. `no-transform`             → [`StreamingBlockReason::NoTransform`]
//! 5. `cache_validation = full`  → [`StreamingBlockReason::FullCacheValidation`]
//! 6. engine `off` **or** policy `off` → [`StreamingBlockReason::EngineOff`]
//! 7. response `Content-Encoding`→ [`StreamingBlockReason::ContentEncoding`]
//!
//! After the hard blocks, the policy decides:
//!
//! * `force` → eligible (stream regardless of size).
//! * `auto`  → needs a known `Content-Length` at or above the streaming
//!   threshold: unknown length → [`StreamingBlockReason::ContentLengthUnknown`],
//!   below threshold → [`StreamingBlockReason::SmallBody`], otherwise
//!   eligible.
//!
//! The safe fallback for every block is the full-buffer path, which (under
//! `cache_validation = full`) preserves complete cache-validation semantics.
//! For `cache_validation = full` + `markdown_streaming auto` this realizes
//! the runtime block-and-fall-back required by spec 49 Requirement 3.4 /
//! frozen contract (`nginx -t` warning; runtime blocks streaming with reason
//! `streaming_block_full_cache_validation`).
//!
//! Validates: spec 49 Requirements 2.1, 3.2, 3.4, 3.5.

use super::conditional::CacheValidation;

/// `markdown_streaming` policy — the streaming *enablement* selector.
///
/// Discriminants are frozen for the 1.0 stability contract.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StreamingPolicy {
    /// `off` — never stream.
    Off = 0,
    /// `auto` — stream large responses, full-buffer small ones.
    Auto = 1,
    /// `force` — always stream (subject to the hard blocks above).
    Force = 2,
}

impl StreamingPolicy {
    /// Construct from the FFI `u8`; unknown values fall back to the safe
    /// `Auto` default (the `balanced` profile default).
    pub fn from_u8(value: u8) -> Self {
        match value {
            0 => StreamingPolicy::Off,
            2 => StreamingPolicy::Force,
            _ => StreamingPolicy::Auto,
        }
    }

    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// `markdown_streaming_engine` — the streaming *implementation* selector.
///
/// Discriminants match `NGX_HTTP_MARKDOWN_STREAM_ENGINE_*` in the C header.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StreamingEngine {
    /// `off` — no streaming backend selected.
    Off = 0,
    /// `auto` — backend chosen per request.
    Auto = 1,
    /// `on` — streaming backend enabled.
    On = 2,
}

impl StreamingEngine {
    /// Construct from the FFI `u8`; unknown values fall back to `Auto`.
    pub fn from_u8(value: u8) -> Self {
        match value {
            0 => StreamingEngine::Off,
            2 => StreamingEngine::On,
            _ => StreamingEngine::Auto,
        }
    }

    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }
}

/// Reason a request did not take the streaming path (spec 49 Requirement
/// 3.5, spec 53 registry alignment).
///
/// String forms are lower_snake_case `streaming_block_*` and match the spec
/// 53 reason-code names. Discriminants are frozen for the 1.0 stability
/// contract: add variants, never renumber or rename.
///
/// # Mapping note
///
/// `markdown_streaming off` (policy) and `markdown_streaming_engine off`
/// (implementation) both surface as [`StreamingBlockReason::EngineOff`]
/// because the frozen 9-variant enum has no dedicated policy-off variant.
/// Adding a distinct `streaming_block_policy_off` is an additive,
/// post-1.0 change that requires spec 53 sign-off; until then both
/// "no streaming backend available" conditions share this code.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum StreamingBlockReason {
    /// `streaming_block_full_cache_validation` — `cache_validation = full`
    /// forces the full-buffer path so a transformed ETag can be generated.
    FullCacheValidation = 0,
    /// `streaming_block_content_encoding` — upstream `Content-Encoding`
    /// must be decompressed before conversion (no streaming pipeline).
    ContentEncoding = 1,
    /// `streaming_block_content_length_unknown` — `auto` mode cannot size
    /// the response without a `Content-Length`.
    ContentLengthUnknown = 2,
    /// `streaming_block_range_request` — `Range` request bypasses conversion.
    RangeRequest = 3,
    /// `streaming_block_no_transform` — `Cache-Control: no-transform`
    /// bypasses conversion.
    NoTransform = 4,
    /// `streaming_block_engine_off` — no streaming backend (policy `off`
    /// or engine `off`).
    EngineOff = 5,
    /// `streaming_block_small_body` — `auto` mode response below the
    /// streaming threshold.
    SmallBody = 6,
    /// `streaming_block_head_request` — `HEAD` request: header decisions
    /// only, no body to stream.
    HeadRequest = 7,
    /// `streaming_block_304_response` — `304 Not Modified` carries no body.
    NotModified = 8,
}

impl StreamingBlockReason {
    /// Stable lower_snake_case reason string (spec 53 alignment).
    pub fn as_str(self) -> &'static str {
        match self {
            StreamingBlockReason::FullCacheValidation => "streaming_block_full_cache_validation",
            StreamingBlockReason::ContentEncoding => "streaming_block_content_encoding",
            StreamingBlockReason::ContentLengthUnknown => "streaming_block_content_length_unknown",
            StreamingBlockReason::RangeRequest => "streaming_block_range_request",
            StreamingBlockReason::NoTransform => "streaming_block_no_transform",
            StreamingBlockReason::EngineOff => "streaming_block_engine_off",
            StreamingBlockReason::SmallBody => "streaming_block_small_body",
            StreamingBlockReason::HeadRequest => "streaming_block_head_request",
            StreamingBlockReason::NotModified => "streaming_block_304_response",
        }
    }

    /// Stable `u8` discriminant for the FFI boundary.
    pub fn as_u8(self) -> u8 {
        self as u8
    }

    /// All variants, for exhaustive iteration in tests.
    pub const ALL: [StreamingBlockReason; 9] = [
        StreamingBlockReason::FullCacheValidation,
        StreamingBlockReason::ContentEncoding,
        StreamingBlockReason::ContentLengthUnknown,
        StreamingBlockReason::RangeRequest,
        StreamingBlockReason::NoTransform,
        StreamingBlockReason::EngineOff,
        StreamingBlockReason::SmallBody,
        StreamingBlockReason::HeadRequest,
        StreamingBlockReason::NotModified,
    ];
}

/// Result of [`decide_streaming`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StreamingDecision {
    /// Whether the request may take the streaming path.
    pub eligible: bool,
    /// Reason streaming was not chosen (`None` when `eligible`).
    pub block_reason: Option<StreamingBlockReason>,
}

/// Input snapshot for [`decide_streaming`].
#[derive(Debug, Clone, Copy)]
pub struct StreamingInput {
    /// `markdown_streaming` enablement policy.
    pub policy: StreamingPolicy,
    /// `markdown_streaming_engine` implementation selector.
    pub engine: StreamingEngine,
    /// Effective `markdown_cache_validation` mode.
    pub cache_validation: CacheValidation,
    /// Whether the request method is `HEAD`.
    pub is_head: bool,
    /// Whether the conditional decision yielded `304 Not Modified`.
    pub is_not_modified: bool,
    /// Whether the request carried a `Range` header.
    pub has_range: bool,
    /// Whether request or response carried `Cache-Control: no-transform`.
    pub no_transform: bool,
    /// Whether the upstream response carried a `Content-Encoding`.
    pub has_content_encoding: bool,
    /// Whether the upstream `Content-Length` is known.
    pub content_length_known: bool,
    /// Upstream `Content-Length` in bytes (meaningful when known).
    pub content_length: u64,
    /// `markdown_stream_threshold` in bytes (auto-mode trigger).
    pub streaming_threshold: u64,
}

/// Decide whether the request may take the streaming path.
///
/// Pure function: same input → same result. See the module docs for the
/// exact decision order.
pub fn decide_streaming(input: &StreamingInput) -> StreamingDecision {
    let block = |reason: StreamingBlockReason| StreamingDecision {
        eligible: false,
        block_reason: Some(reason),
    };

    // --- Hard blocks (hold even under policy = force) ---

    // 1. 304 Not Modified carries no body.
    if input.is_not_modified {
        return block(StreamingBlockReason::NotModified);
    }

    // 2. HEAD: header decisions only, no body to stream.
    if input.is_head {
        return block(StreamingBlockReason::HeadRequest);
    }

    // 3. Range request bypasses conversion.
    if input.has_range {
        return block(StreamingBlockReason::RangeRequest);
    }

    // 4. Cache-Control: no-transform bypasses conversion.
    if input.no_transform {
        return block(StreamingBlockReason::NoTransform);
    }

    // 5. cache_validation = full forces full-buffer to produce a
    //    transformed ETag (runtime block + fall-back for full + auto).
    if input.cache_validation == CacheValidation::Full {
        return block(StreamingBlockReason::FullCacheValidation);
    }

    // 6. No streaming backend available: policy off or engine off.
    if input.policy == StreamingPolicy::Off || input.engine == StreamingEngine::Off {
        return block(StreamingBlockReason::EngineOff);
    }

    // 7. Content-Encoding must be decompressed before conversion; 0.9.0
    //    has no streaming decompress + streaming convert pipeline.
    if input.has_content_encoding {
        return block(StreamingBlockReason::ContentEncoding);
    }

    // --- Policy decides ---

    // force: always stream after the hard blocks.
    if input.policy == StreamingPolicy::Force {
        return StreamingDecision {
            eligible: true,
            block_reason: None,
        };
    }

    // auto: need a known Content-Length at or above the threshold.
    if !input.content_length_known {
        return block(StreamingBlockReason::ContentLengthUnknown);
    }
    if input.content_length < input.streaming_threshold {
        return block(StreamingBlockReason::SmallBody);
    }

    StreamingDecision {
        eligible: true,
        block_reason: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> StreamingInput {
        StreamingInput {
            policy: StreamingPolicy::Auto,
            engine: StreamingEngine::Auto,
            cache_validation: CacheValidation::ImsOnly,
            is_head: false,
            is_not_modified: false,
            has_range: false,
            no_transform: false,
            has_content_encoding: false,
            content_length_known: true,
            content_length: 1024 * 1024,
            streaming_threshold: 256 * 1024,
        }
    }

    #[test]
    fn auto_large_body_eligible() {
        let d = decide_streaming(&base());
        assert!(d.eligible);
        assert_eq!(d.block_reason, None);
    }

    #[test]
    fn auto_small_body_blocks() {
        let mut i = base();
        i.content_length = 1024;
        let d = decide_streaming(&i);
        assert!(!d.eligible);
        assert_eq!(d.block_reason, Some(StreamingBlockReason::SmallBody));
    }

    #[test]
    fn auto_unknown_length_blocks() {
        let mut i = base();
        i.content_length_known = false;
        let d = decide_streaming(&i);
        assert_eq!(
            d.block_reason,
            Some(StreamingBlockReason::ContentLengthUnknown)
        );
    }

    #[test]
    fn force_streams_small_body() {
        let mut i = base();
        i.policy = StreamingPolicy::Force;
        i.engine = StreamingEngine::On;
        i.content_length = 1;
        let d = decide_streaming(&i);
        assert!(d.eligible);
    }

    #[test]
    fn force_streams_unknown_length() {
        let mut i = base();
        i.policy = StreamingPolicy::Force;
        i.engine = StreamingEngine::On;
        i.content_length_known = false;
        let d = decide_streaming(&i);
        assert!(d.eligible);
    }

    #[test]
    fn policy_off_blocks_engine_off() {
        let mut i = base();
        i.policy = StreamingPolicy::Off;
        let d = decide_streaming(&i);
        assert_eq!(d.block_reason, Some(StreamingBlockReason::EngineOff));
    }

    #[test]
    fn engine_off_blocks_engine_off() {
        let mut i = base();
        i.engine = StreamingEngine::Off;
        let d = decide_streaming(&i);
        assert_eq!(d.block_reason, Some(StreamingBlockReason::EngineOff));
    }

    #[test]
    fn full_cache_validation_blocks_even_under_force() {
        let mut i = base();
        i.policy = StreamingPolicy::Force;
        i.engine = StreamingEngine::On;
        i.cache_validation = CacheValidation::Full;
        let d = decide_streaming(&i);
        assert!(!d.eligible);
        assert_eq!(
            d.block_reason,
            Some(StreamingBlockReason::FullCacheValidation)
        );
    }

    #[test]
    fn content_encoding_blocks_even_under_force() {
        let mut i = base();
        i.policy = StreamingPolicy::Force;
        i.engine = StreamingEngine::On;
        i.has_content_encoding = true;
        let d = decide_streaming(&i);
        assert_eq!(d.block_reason, Some(StreamingBlockReason::ContentEncoding));
    }

    #[test]
    fn range_blocks_even_under_force() {
        let mut i = base();
        i.policy = StreamingPolicy::Force;
        i.engine = StreamingEngine::On;
        i.has_range = true;
        let d = decide_streaming(&i);
        assert_eq!(d.block_reason, Some(StreamingBlockReason::RangeRequest));
    }

    #[test]
    fn no_transform_blocks_even_under_force() {
        let mut i = base();
        i.policy = StreamingPolicy::Force;
        i.engine = StreamingEngine::On;
        i.no_transform = true;
        let d = decide_streaming(&i);
        assert_eq!(d.block_reason, Some(StreamingBlockReason::NoTransform));
    }

    #[test]
    fn head_blocks() {
        let mut i = base();
        i.is_head = true;
        let d = decide_streaming(&i);
        assert_eq!(d.block_reason, Some(StreamingBlockReason::HeadRequest));
    }

    #[test]
    fn not_modified_blocks_first() {
        let mut i = base();
        i.is_not_modified = true;
        // Also HEAD + range set; 304 must win (checked first).
        i.is_head = true;
        i.has_range = true;
        let d = decide_streaming(&i);
        assert_eq!(d.block_reason, Some(StreamingBlockReason::NotModified));
    }

    #[test]
    fn full_cache_validation_full_auto_runtime_block() {
        // The frozen full + auto contract: nginx -t warns, runtime blocks
        // streaming with full_cache_validation and falls back to full-buffer.
        let mut i = base();
        i.policy = StreamingPolicy::Auto;
        i.engine = StreamingEngine::Auto;
        i.cache_validation = CacheValidation::Full;
        i.content_length = 10 * 1024 * 1024; /* large enough to stream */
        let d = decide_streaming(&i);
        assert!(!d.eligible);
        assert_eq!(
            d.block_reason,
            Some(StreamingBlockReason::FullCacheValidation)
        );
    }

    #[test]
    fn each_block_reason_is_reachable() {
        use std::collections::HashSet;
        let mut seen: HashSet<StreamingBlockReason> = HashSet::new();

        // NotModified
        let mut i = base();
        i.is_not_modified = true;
        seen.insert(decide_streaming(&i).block_reason.unwrap());

        // HeadRequest
        let mut i = base();
        i.is_head = true;
        seen.insert(decide_streaming(&i).block_reason.unwrap());

        // RangeRequest
        let mut i = base();
        i.has_range = true;
        seen.insert(decide_streaming(&i).block_reason.unwrap());

        // NoTransform
        let mut i = base();
        i.no_transform = true;
        seen.insert(decide_streaming(&i).block_reason.unwrap());

        // FullCacheValidation
        let mut i = base();
        i.cache_validation = CacheValidation::Full;
        seen.insert(decide_streaming(&i).block_reason.unwrap());

        // EngineOff
        let mut i = base();
        i.engine = StreamingEngine::Off;
        seen.insert(decide_streaming(&i).block_reason.unwrap());

        // ContentEncoding
        let mut i = base();
        i.has_content_encoding = true;
        seen.insert(decide_streaming(&i).block_reason.unwrap());

        // ContentLengthUnknown
        let mut i = base();
        i.content_length_known = false;
        seen.insert(decide_streaming(&i).block_reason.unwrap());

        // SmallBody
        let mut i = base();
        i.content_length = 1;
        seen.insert(decide_streaming(&i).block_reason.unwrap());

        assert_eq!(
            seen.len(),
            StreamingBlockReason::ALL.len(),
            "every StreamingBlockReason variant must be reachable"
        );
    }

    #[test]
    fn block_reason_strings_unique_and_prefixed() {
        use std::collections::HashSet;
        let mut seen = HashSet::new();
        for r in StreamingBlockReason::ALL {
            let s = r.as_str();
            assert!(
                s.starts_with("streaming_block_"),
                "'{}' missing streaming_block_ prefix",
                s
            );
            for ch in s.chars() {
                assert!(
                    ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '_',
                    "'{}' has invalid char '{}'",
                    s,
                    ch
                );
            }
            assert!(seen.insert(s), "duplicate block reason string '{}'", s);
        }
    }

    #[test]
    fn enum_from_u8_roundtrips() {
        for p in [
            StreamingPolicy::Off,
            StreamingPolicy::Auto,
            StreamingPolicy::Force,
        ] {
            assert_eq!(StreamingPolicy::from_u8(p.as_u8()), p);
        }
        assert_eq!(StreamingPolicy::from_u8(99), StreamingPolicy::Auto);
        for e in [
            StreamingEngine::Off,
            StreamingEngine::Auto,
            StreamingEngine::On,
        ] {
            assert_eq!(StreamingEngine::from_u8(e.as_u8()), e);
        }
        assert_eq!(StreamingEngine::from_u8(99), StreamingEngine::Auto);
    }

    #[test]
    fn idempotent() {
        let i = base();
        assert_eq!(decide_streaming(&i), decide_streaming(&i));
    }
}
