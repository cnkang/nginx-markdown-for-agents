//! HTTP Representation Contract verification tests.
//!
//! Validates the 16-scenario HTTP representation truth table
//! from the Design document (Task 6.7, Requirements 10.1-10.6, 10.8-10.10, 15.3).
//!
//! These tests verify the Rust-side contract for header plan construction
//! and conditional request evaluation. The C module's `Vary: Accept`
//! handling and body delivery are verified by protocol_correctness_test.c.

use nginx_markdown_converter::decision::conditional::{
    CacheValidation, ConditionalInput, ConditionalOutcome, ConditionalReason, decide_conditional,
};
use nginx_markdown_converter::header_plan::{HeaderOp, HeaderPlan};

const MARKDOWN_CT: &str = "text/markdown; charset=utf-8";

/* ══════════════════════════════════════════════════════════════════
 * Scenario 1: normal converted 200 full-buffer
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_01_200_fullbuffer_with_etag() {
    let plan = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, true);

    // Content-Type set to text/markdown; charset=utf-8
    assert_eq!(
        plan.ops[0],
        HeaderOp::Set {
            name: "Content-Type".to_string(),
            value: MARKDOWN_CT.to_string(),
        }
    );

    // Content-Encoding deleted (response is decompressed)
    assert_eq!(
        plan.ops[1],
        HeaderOp::DeleteAll {
            name: "Content-Encoding".to_string(),
        }
    );

    // Content-Length deleted (C caller sets post-conversion)
    assert_eq!(
        plan.ops[2],
        HeaderOp::DeleteAll {
            name: "Content-Length".to_string(),
        }
    );

    // ETag placeholder for transformed ETag (cache_validation full)
    assert_eq!(plan.ops[3], HeaderOp::SetEtagPlaceholder);
    assert_eq!(plan.len(), 4);
}

#[test]
fn scenario_01_200_fullbuffer_without_etag() {
    let plan = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, false);

    assert_eq!(plan.len(), 3);
    // No ETag placeholder when cache_validation is not full
    assert!(
        !plan
            .ops
            .iter()
            .any(|op| matches!(op, HeaderOp::SetEtagPlaceholder))
    );
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 2: normal converted 200 streaming
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_02_200_streaming() {
    // Streaming path never has ETag (headers committed before body known)
    let plan = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, false);

    assert_eq!(plan.len(), 3);
    assert_eq!(
        plan.ops[0],
        HeaderOp::Set {
            name: "Content-Type".to_string(),
            value: MARKDOWN_CT.to_string(),
        }
    );
    assert_eq!(
        plan.ops[1],
        HeaderOp::DeleteAll {
            name: "Content-Encoding".to_string(),
        }
    );
    // Content-Length deleted for streaming (chunked transfer)
    assert_eq!(
        plan.ops[2],
        HeaderOp::DeleteAll {
            name: "Content-Length".to_string(),
        }
    );
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 3: precommit reject 429/502/503
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_03_precommit_reject() {
    let plan = HeaderPlan::for_error_pre_commit("text/plain");

    // Sets Content-Type for error body
    assert_eq!(
        plan.ops[0],
        HeaderOp::Set {
            name: "Content-Type".to_string(),
            value: "text/plain".to_string(),
        }
    );
    // Removes Content-Length (error body sets its own)
    assert_eq!(
        plan.ops[1],
        HeaderOp::DeleteAll {
            name: "Content-Length".to_string(),
        }
    );
    // Removes ETag (error response has no entity)
    assert_eq!(
        plan.ops[2],
        HeaderOp::DeleteAll {
            name: "ETag".to_string(),
        }
    );
    // Removes Content-Encoding
    assert_eq!(
        plan.ops[3],
        HeaderOp::DeleteAll {
            name: "Content-Encoding".to_string(),
        }
    );
    assert_eq!(plan.len(), 4);
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 7: HEAD full-buffer
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_07_head_fullbuffer() {
    let plan_head = HeaderPlan::for_head(MARKDOWN_CT, true);
    let plan_get = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, true);

    // HEAD plan is identical to GET conversion plan
    // (body suppression is handled by NGINX, not the plan)
    assert_eq!(plan_head, plan_get);
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 8: HEAD streaming/unknown-length
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_08_head_streaming() {
    // Streaming HEAD: no ETag (can't compute without full GET)
    let plan = HeaderPlan::for_head(MARKDOWN_CT, false);

    assert_eq!(plan.len(), 3);
    // Content-Length deleted (omit: unknowable without hidden GET)
    assert!(plan.ops.contains(&HeaderOp::DeleteAll {
        name: "Content-Length".to_string()
    }));
    // No ETag placeholder
    assert!(
        !plan
            .ops
            .iter()
            .any(|op| matches!(op, HeaderOp::SetEtagPlaceholder))
    );
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 9: IMS-only 304
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_09_ims_only_304() {
    let input = ConditionalInput {
        cache_validation: CacheValidation::ImsOnly,
        has_range: false,
        no_transform: false,
        if_none_match: None,
        entity_etag: None,
        if_modified_since: Some("Sat, 01 Jan 2000 00:00:00 GMT"),
        last_modified: Some("Sat, 01 Jan 2000 00:00:00 GMT"),
    };

    let decision = decide_conditional(&input);

    // IMS-only: evaluates If-Modified-Since only
    assert_eq!(decision.outcome, ConditionalOutcome::NotModified);
    assert_eq!(decision.reason, ConditionalReason::ImsEvaluated);

    // 304 plan: delete Content-Length and Content-Encoding
    let plan = HeaderPlan::for_304();
    assert_eq!(plan.len(), 2);
    assert!(plan.ops.contains(&HeaderOp::DeleteAll {
        name: "Content-Length".to_string()
    }));
    assert!(plan.ops.contains(&HeaderOp::DeleteAll {
        name: "Content-Encoding".to_string()
    }));
}

#[test]
fn scenario_09_ims_only_ignores_if_none_match() {
    // IMS-only mode must IGNORE If-None-Match even if present
    let input = ConditionalInput {
        cache_validation: CacheValidation::ImsOnly,
        has_range: false,
        no_transform: false,
        if_none_match: Some("\"abc123\""),
        entity_etag: Some("\"abc123\""),
        if_modified_since: None,
        last_modified: Some("Sat, 01 Jan 2000 00:00:00 GMT"),
    };

    let decision = decide_conditional(&input);

    // No If-Modified-Since present → Proceed (not 304)
    assert_eq!(decision.outcome, ConditionalOutcome::Proceed);
    assert_eq!(decision.reason, ConditionalReason::NoHeaders);
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 10: full-validation 304
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_10_full_validation_304_inm_match() {
    // Full mode: If-None-Match evaluated AFTER conversion produces ETag
    let input = ConditionalInput {
        cache_validation: CacheValidation::Full,
        has_range: false,
        no_transform: false,
        if_none_match: Some("\"transformed-etag-abc\""),
        entity_etag: Some("\"transformed-etag-abc\""),
        if_modified_since: None,
        last_modified: None,
    };

    let decision = decide_conditional(&input);

    assert_eq!(decision.outcome, ConditionalOutcome::NotModified);
    assert_eq!(decision.reason, ConditionalReason::InmEvaluated);
}

#[test]
fn scenario_10_full_validation_304_inm_mismatch() {
    // Mismatch: If-None-Match does not match → Proceed
    let input = ConditionalInput {
        cache_validation: CacheValidation::Full,
        has_range: false,
        no_transform: false,
        if_none_match: Some("\"old-html-etag\""),
        entity_etag: Some("\"transformed-markdown-etag\""),
        if_modified_since: None,
        last_modified: None,
    };

    let decision = decide_conditional(&input);

    assert_eq!(decision.outcome, ConditionalOutcome::Proceed);
    assert_eq!(decision.reason, ConditionalReason::InmEvaluated);
}

#[test]
fn scenario_10_full_inm_takes_precedence_over_ims() {
    // RFC 7232 §6: If-None-Match present → ignore If-Modified-Since
    let input = ConditionalInput {
        cache_validation: CacheValidation::Full,
        has_range: false,
        no_transform: false,
        if_none_match: Some("\"etag-abc\""),
        entity_etag: Some("\"etag-abc\""),
        if_modified_since: Some("Sat, 01 Jan 2099 00:00:00 GMT"),
        last_modified: Some("Sat, 01 Jan 2000 00:00:00 GMT"),
    };

    let decision = decide_conditional(&input);

    // If-None-Match matches → 304 (IMS ignored per RFC 7232)
    assert_eq!(decision.outcome, ConditionalOutcome::NotModified);
    assert_eq!(decision.reason, ConditionalReason::InmEvaluated);
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 11: fail-open HTML
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_11_failopen_html() {
    let plan = HeaderPlan::for_pass_through();
    // Empty plan: all original headers preserved
    assert!(plan.is_empty());
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 12: Accept-nonmatching skipped response
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_12_accept_skip() {
    let plan = HeaderPlan::for_bypass();
    // Empty plan: original response preserved
    assert!(plan.is_empty());
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 13: precommit PASS_HTML after decoder/parser failure
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_13_pass_html() {
    let plan = HeaderPlan::for_pass_html();

    // PASS_HTML produces empty plan: no mutation of Content-Encoding,
    // Content-Type, Content-Length, validators, or body bytes
    assert!(plan.is_empty());
    assert_eq!(plan, HeaderPlan::for_bypass());
    assert_eq!(plan, HeaderPlan::for_pass_through());
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 15: non-cacheable response
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_15_non_cacheable() {
    // Non-cacheable conversion uses the same plan as normal conversion
    // (Vary: Accept omission is a C-module responsibility)
    let plan = HeaderPlan::for_non_cacheable_conversion(MARKDOWN_CT, false);
    let normal = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, false);
    assert_eq!(plan, normal);
}

/* ══════════════════════════════════════════════════════════════════
 * Scenario 16: 206, no-transform, or encoding-capability bypass
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn scenario_16_range_bypass() {
    let input = ConditionalInput {
        cache_validation: CacheValidation::Full,
        has_range: true,
        no_transform: false,
        if_none_match: Some("\"etag\""),
        entity_etag: Some("\"etag\""),
        if_modified_since: None,
        last_modified: None,
    };

    let decision = decide_conditional(&input);

    // Range request → Bypass (not 304, not Proceed)
    assert_eq!(decision.outcome, ConditionalOutcome::Bypass);
    assert_eq!(decision.reason, ConditionalReason::BypassRange);
}

#[test]
fn scenario_16_no_transform_bypass() {
    let input = ConditionalInput {
        cache_validation: CacheValidation::Full,
        has_range: false,
        no_transform: true,
        if_none_match: Some("\"etag\""),
        entity_etag: Some("\"etag\""),
        if_modified_since: None,
        last_modified: None,
    };

    let decision = decide_conditional(&input);

    // no-transform → Bypass
    assert_eq!(decision.outcome, ConditionalOutcome::Bypass);
    assert_eq!(decision.reason, ConditionalReason::BypassNoTransform);
}

#[test]
fn scenario_16_range_takes_precedence_over_no_transform() {
    let input = ConditionalInput {
        cache_validation: CacheValidation::Full,
        has_range: true,
        no_transform: true,
        if_none_match: None,
        entity_etag: None,
        if_modified_since: None,
        last_modified: None,
    };

    let decision = decide_conditional(&input);

    // Range takes precedence
    assert_eq!(decision.outcome, ConditionalOutcome::Bypass);
    assert_eq!(decision.reason, ConditionalReason::BypassRange);
}

/* ══════════════════════════════════════════════════════════════════
 * Streaming/cache_validation relationship
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn streaming_cache_validation_full_requires_fullbuffer() {
    // cache_validation=full → always Proceed (conditional evaluated
    // AFTER full-buffer conversion, not during path selection)
    let input = ConditionalInput {
        cache_validation: CacheValidation::Full,
        has_range: false,
        no_transform: false,
        if_none_match: None,
        entity_etag: None,
        if_modified_since: None,
        last_modified: None,
    };

    let decision = decide_conditional(&input);
    assert_eq!(decision.outcome, ConditionalOutcome::Proceed);
    assert_eq!(decision.reason, ConditionalReason::NoHeaders);
}

#[test]
fn cache_validation_off_never_304() {
    let input = ConditionalInput {
        cache_validation: CacheValidation::Off,
        has_range: false,
        no_transform: false,
        if_none_match: Some("\"matching-etag\""),
        entity_etag: Some("\"matching-etag\""),
        if_modified_since: Some("Sat, 01 Jan 2000 00:00:00 GMT"),
        last_modified: Some("Sat, 01 Jan 2000 00:00:00 GMT"),
    };

    let decision = decide_conditional(&input);

    // Off mode: never produces 304 regardless of headers present
    assert_eq!(decision.outcome, ConditionalOutcome::Proceed);
    assert_eq!(decision.reason, ConditionalReason::NoHeaders);
}

/* ══════════════════════════════════════════════════════════════════
 * ETag invariant: never reuse upstream HTML ETag
 * ══════════════════════════════════════════════════════════════════ */

#[test]
fn etag_never_upstream_html() {
    // The entity_etag in the conditional decision is ALWAYS the
    // transformed representation ETag (computed from Markdown bytes),
    // NEVER the upstream HTML ETag. This test verifies that a
    // mismatch between If-None-Match (client-sent, from old HTML ETag)
    // and entity_etag (transformed) correctly results in Proceed.
    let input = ConditionalInput {
        cache_validation: CacheValidation::Full,
        has_range: false,
        no_transform: false,
        if_none_match: Some("\"upstream-html-etag-from-origin\""),
        entity_etag: Some("\"transformed-markdown-etag-completely-different\""),
        if_modified_since: None,
        last_modified: None,
    };

    let decision = decide_conditional(&input);

    // Different ETags → not modified check fails → Proceed
    assert_eq!(decision.outcome, ConditionalOutcome::Proceed);
}
