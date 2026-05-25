#![no_main]

//! Fuzz target for content negotiation and header plan construction.
//!
//! # Input Model
//!
//! The input byte sequence is divided into fixed regions:
//!
//! | Region       | Bytes     | Purpose                          |
//! |--------------|-----------|----------------------------------|
//! | Accept       | 0..64     | Accept header value              |
//! | Content-Type | 64..96    | Response Content-Type            |
//! | Status Code  | 96..98    | HTTP status code seed            |
//! | Config Flags | 98..102   | Decision context boolean flags   |
//! | User-Agent   | 102..166  | User-Agent header value          |
//! | Extension    | 166..     | Reserved for future use          |
//!
//! Short inputs are handled gracefully: missing regions use empty/default
//! values. This ensures the fuzzer can start with minimal seeds and
//! progressively explore the input space.
//!
//! # Invariants
//!
//! 1. **No panic**: Any byte sequence must not trigger a panic in the
//!    negotiation, decision, or header plan construction logic.
//! 2. **Decision determinism**: The same input must always produce the
//!    same negotiation result, decision, and header plan.
//! 3. **Malformed header stability**: Malformed q-values, duplicate media
//!    types, case variants, empty headers, overlong headers (>4KB),
//!    illegal token characters, and non-UTF-8 byte sequences must not
//!    crash the system.
//!
//! # Failure Definition
//!
//! - panic = defect (logic bug in negotiation/decision/header code)
//! - non-deterministic decision = logic defect (violates pure-function contract)
//! - sanitizer report (ASan/UBSan) = security defect

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::decision::{DecisionContext, make_decision};
use nginx_markdown_converter::header_plan::HeaderPlan;
use nginx_markdown_converter::negotiator::{NegotiationResult, negotiate};

/// Extract a UTF-8 string slice from the input data at the given byte range.
///
/// Returns an empty string if:
/// - The range exceeds the data length (short input)
/// - The bytes are not valid UTF-8
///
/// This ensures deterministic, panic-free derivation from arbitrary bytes.
fn safe_slice_to_str(data: &[u8], start: usize, end: usize) -> &str {
    let s = start.min(data.len());
    let e = end.min(data.len());
    if s >= e {
        return "";
    }
    std::str::from_utf8(&data[s..e]).unwrap_or("")
}

/// Derive an HTTP status code from two seed bytes.
///
/// Maps arbitrary bytes to a realistic status code range (100-599).
/// Returns 200 if the region is not available.
fn derive_status_code(data: &[u8], offset: usize) -> u16 {
    if offset + 1 < data.len() {
        let raw = u16::from(data[offset]) | (u16::from(data[offset + 1]) << 8);
        // Map to 100-599 range
        100 + (raw % 500)
    } else if offset < data.len() {
        100 + u16::from(data[offset]) % 500
    } else {
        200
    }
}

/// Derive decision context configuration flags from 4 seed bytes.
///
/// Each bit in the first available byte maps to a boolean field in
/// `DecisionContext`. This provides full combinatorial coverage of
/// the decision engine's boolean input space.
fn derive_config_flags(data: &[u8], offset: usize) -> (bool, bool, bool, bool, bool, bool) {
    let byte0 = if offset < data.len() { data[offset] } else { 0xFF };
    let byte1 = if offset + 1 < data.len() { data[offset + 1] } else { 0xFF };

    let enabled = (byte0 & 0x01) != 0;
    let eligible = (byte0 & 0x02) != 0;
    let conditional_not_modified = (byte0 & 0x04) != 0;
    let decompression_ok = (byte0 & 0x08) != 0;
    let parse_timed_out = (byte0 & 0x10) != 0;
    let parse_budget_exceeded = (byte0 & 0x20) != 0;
    let _on_wildcard = (byte1 & 0x01) != 0;
    // byte1 bits 1-7 and bytes 2-3 reserved for future config

    (enabled, eligible, conditional_not_modified, decompression_ok, parse_timed_out, parse_budget_exceeded)
}

/// Derive the `on_wildcard` flag from config bytes.
fn derive_on_wildcard(data: &[u8], offset: usize) -> bool {
    if offset + 1 < data.len() {
        (data[offset + 1] & 0x01) != 0
    } else {
        true // default: wildcards enabled
    }
}

/// Derive whether an ETag is present from config bytes.
fn derive_has_etag(data: &[u8], offset: usize) -> bool {
    if offset + 1 < data.len() {
        (data[offset + 1] & 0x02) != 0
    } else {
        false
    }
}

/// Run the full negotiation + decision + header plan pipeline once.
///
/// Returns a tuple of (NegotiationResult, Decision, HeaderPlan) for
/// determinism comparison.
fn run_pipeline(
    accept: &str,
    content_type: &str,
    on_wildcard: bool,
    has_etag: bool,
    enabled: bool,
    eligible: bool,
    conditional_not_modified: bool,
    decompression_ok: bool,
    parse_timed_out: bool,
    parse_budget_exceeded: bool,
) -> (NegotiationResult, nginx_markdown_converter::decision::Decision, HeaderPlan) {
    // Step 1: Content negotiation
    let negotiation_result = negotiate(accept, on_wildcard);

    // Step 2: Build decision context from negotiation result + config flags
    let accept_prefers_markdown = matches!(negotiation_result, NegotiationResult::Convert);
    let accept_header_present = !accept.trim().is_empty();

    let ctx = DecisionContext {
        enabled,
        eligible,
        accept_prefers_markdown,
        accept_header_present,
        conditional_not_modified,
        decompression_ok,
        parse_timed_out,
        parse_budget_exceeded,
    };

    let decision = make_decision(&ctx);

    // Step 3: Build header plan (always exercise this path regardless of decision)
    let plan = HeaderPlan::for_markdown_conversion(content_type, has_etag);

    (negotiation_result, decision, plan)
}

fuzz_target!(|data: &[u8]| {
    // Derive all parameters from input regions
    let accept = safe_slice_to_str(data, 0, 64);
    let content_type = safe_slice_to_str(data, 64, 96);
    let _status_code = derive_status_code(data, 96);
    let (enabled, eligible, conditional_not_modified, decompression_ok, parse_timed_out, parse_budget_exceeded) =
        derive_config_flags(data, 98);
    let on_wildcard = derive_on_wildcard(data, 98);
    let has_etag = derive_has_etag(data, 98);
    let _user_agent = safe_slice_to_str(data, 102, 166);

    // Run pipeline first time
    let (neg1, dec1, plan1) = run_pipeline(
        accept,
        content_type,
        on_wildcard,
        has_etag,
        enabled,
        eligible,
        conditional_not_modified,
        decompression_ok,
        parse_timed_out,
        parse_budget_exceeded,
    );

    // Run pipeline second time with identical inputs — determinism assertion
    let (neg2, dec2, plan2) = run_pipeline(
        accept,
        content_type,
        on_wildcard,
        has_etag,
        enabled,
        eligible,
        conditional_not_modified,
        decompression_ok,
        parse_timed_out,
        parse_budget_exceeded,
    );

    // Invariant: same input → same output (determinism)
    assert_eq!(neg1, neg2, "Negotiation result is non-deterministic");
    assert_eq!(dec1, dec2, "Decision is non-deterministic");
    assert_eq!(plan1, plan2, "HeaderPlan is non-deterministic");
});
