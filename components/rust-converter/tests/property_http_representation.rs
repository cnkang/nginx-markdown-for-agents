//! Property-based tests for the HTTP representation contract.
//!
//! Property 16: HTTP 200 conversion header contract (Task 6.8, Req 10.1)
//! Property 17: HEAD response contract (Task 6.9, Req 10.2)
//! Property 18: Bypass scenarios passthrough (Task 6.10, Req 10.4, 10.5)
//!
//! These properties verify the Rust-side `HeaderPlan` invariants that the
//! C module relies on for HTTP representation correctness. The C-side
//! `Vary: Accept` append/dedupe and body suppression are verified by
//! `protocol_correctness_test.c` and the E2E suite.

use nginx_markdown_converter::decision::conditional::{
    CacheValidation, ConditionalInput, ConditionalOutcome, ConditionalReason, decide_conditional,
};
use nginx_markdown_converter::header_plan::{HeaderOp, HeaderPlan};
use proptest::prelude::*;

const MARKDOWN_CT: &str = "text/markdown; charset=utf-8";

// ─── Helpers ─────────────────────────────────────────────────────────────────

fn plan_sets_to(plan: &HeaderPlan, name: &str, value: &str) -> bool {
    plan.ops.iter().any(|op| match op {
        HeaderOp::Set { name: n, value: v } => n == name && v == value,
        _ => false,
    })
}

fn plan_deletes_all(plan: &HeaderPlan, name: &str) -> bool {
    plan.ops.iter().any(|op| match op {
        HeaderOp::DeleteAll { name: n } => n == name,
        _ => false,
    })
}

fn plan_has_etag_placeholder(plan: &HeaderPlan) -> bool {
    plan.ops
        .iter()
        .any(|op| matches!(op, HeaderOp::SetEtagPlaceholder))
}

fn arb_opt_etag() -> impl Strategy<Value = Option<String>> {
    prop_oneof![
        Just(None),
        Just(Some("\"abc123\"".to_string())),
        Just(Some("\"transformed-etag\"".to_string())),
        Just(Some("\"upstream-html-etag\"".to_string())),
    ]
}

// ─── Properties 16, 17, 18 ───────────────────────────────────────────────────
//
// Property 16: HTTP 200 conversion header contract (Req 10.1)
// Property 17: HEAD response contract (Req 10.2)
// Property 18: Bypass scenarios passthrough (Req 10.4, 10.5)

proptest! {
    #![proptest_config(ProptestConfig::with_cases(64))]

    // ── Property 16: HTTP 200 conversion header contract ──

    #[test]
    fn prop_16a_200_conversion_sets_content_type_and_deletes_encoding(
        has_etag in any::<bool>(),
    ) {
        let plan = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, has_etag);
        prop_assert!(plan_sets_to(&plan, "Content-Type", MARKDOWN_CT));
        prop_assert!(plan_deletes_all(&plan, "Content-Encoding"));
    }

    #[test]
    fn prop_16b_200_conversion_deletes_content_length(
        has_etag in any::<bool>(),
    ) {
        let plan = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, has_etag);
        prop_assert!(plan_deletes_all(&plan, "Content-Length"));
    }

    #[test]
    fn prop_16c_200_conversion_etag_iff_has_etag(
        has_etag in any::<bool>(),
    ) {
        let plan = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, has_etag);
        prop_assert_eq!(plan_has_etag_placeholder(&plan), has_etag);
    }

    #[test]
    fn prop_16d_200_conversion_plan_nonempty(
        has_etag in any::<bool>(),
    ) {
        let plan = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, has_etag);
        prop_assert!(plan.len() >= 3);
    }

    // ── Property 17: HEAD response contract ──

    #[test]
    fn prop_17a_head_plan_equals_get_plan(
        has_etag in any::<bool>(),
    ) {
        let plan_head = HeaderPlan::for_head(MARKDOWN_CT, has_etag);
        let plan_get = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, has_etag);
        prop_assert_eq!(plan_head, plan_get);
    }

    #[test]
    fn prop_17d_head_never_reuses_upstream_etag(
        has_etag in any::<bool>(),
    ) {
        let plan = HeaderPlan::for_head(MARKDOWN_CT, has_etag);
        let has_literal_etag = plan.ops.iter().any(|op| matches!(
            op,
            HeaderOp::Set { name: n, .. } if n == "ETag"
        ));
        prop_assert!(!has_literal_etag);
    }

    // ── Property 18: Bypass scenarios passthrough ──

    #[test]
    fn prop_18a_range_request_bypass_passthrough(
        if_none_match in arb_opt_etag(),
        entity_etag in arb_opt_etag(),
    ) {
        let input = ConditionalInput {
            cache_validation: CacheValidation::Full,
            has_range: true,
            no_transform: false,
            if_none_match: if_none_match.as_deref(),
            entity_etag: entity_etag.as_deref(),
            if_modified_since: None,
            last_modified: None,
        };
        let decision = decide_conditional(&input);
        prop_assert_eq!(decision.outcome, ConditionalOutcome::Bypass);
        prop_assert_eq!(decision.reason, ConditionalReason::BypassRange);
        let plan = HeaderPlan::for_bypass();
        prop_assert!(plan.is_empty());
    }

    #[test]
    fn prop_18b_no_transform_bypass_passthrough(
        if_none_match in arb_opt_etag(),
        entity_etag in arb_opt_etag(),
    ) {
        let input = ConditionalInput {
            cache_validation: CacheValidation::Full,
            has_range: false,
            no_transform: true,
            if_none_match: if_none_match.as_deref(),
            entity_etag: entity_etag.as_deref(),
            if_modified_since: None,
            last_modified: None,
        };
        let decision = decide_conditional(&input);
        prop_assert_eq!(decision.outcome, ConditionalOutcome::Bypass);
        prop_assert_eq!(decision.reason, ConditionalReason::BypassNoTransform);
        let plan = HeaderPlan::for_bypass();
        prop_assert!(plan.is_empty());
    }
}

// ─── Deterministic tests (no proptest needed) ────────────────────────────────

#[test]
fn prop_17b_head_streaming_omits_length_and_etag() {
    let plan = HeaderPlan::for_head(MARKDOWN_CT, false);
    assert!(plan_deletes_all(&plan, "Content-Length"));
    assert!(!plan_has_etag_placeholder(&plan));
}

#[test]
fn prop_17c_head_fullbuffer_may_emit_etag() {
    let plan = HeaderPlan::for_head(MARKDOWN_CT, true);
    assert!(plan_has_etag_placeholder(&plan));
    assert!(plan_deletes_all(&plan, "Content-Length"));
}

#[test]
fn prop_18c_range_precedence_over_no_transform() {
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
    assert_eq!(decision.outcome, ConditionalOutcome::Bypass);
    assert_eq!(decision.reason, ConditionalReason::BypassRange);
}

#[test]
fn prop_18d_all_bypass_plans_empty() {
    let bypass = HeaderPlan::for_bypass();
    let pass_through = HeaderPlan::for_pass_through();
    let pass_html = HeaderPlan::for_pass_html();
    assert!(bypass.is_empty());
    assert!(pass_through.is_empty());
    assert!(pass_html.is_empty());
    assert_eq!(bypass, pass_through);
    assert_eq!(bypass, pass_html);
}

// ─── Targeted regression tests ───────────────────────────────────────────────

#[test]
fn regression_200_full_buffer_plan_structure() {
    let plan = HeaderPlan::for_markdown_conversion(MARKDOWN_CT, true);
    assert_eq!(plan.len(), 4);
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
    assert_eq!(
        plan.ops[2],
        HeaderOp::DeleteAll {
            name: "Content-Length".to_string(),
        }
    );
    assert_eq!(plan.ops[3], HeaderOp::SetEtagPlaceholder);
}

#[test]
fn regression_head_streaming_no_etag_no_length() {
    let plan = HeaderPlan::for_head(MARKDOWN_CT, false);
    assert!(!plan_has_etag_placeholder(&plan));
    assert!(plan_deletes_all(&plan, "Content-Length"));
    assert!(plan_sets_to(&plan, "Content-Type", MARKDOWN_CT));
}

#[test]
fn regression_bypass_preserves_all_headers() {
    let plan = HeaderPlan::for_bypass();
    assert!(plan.is_empty());
    assert!(!plan.ops.iter().any(|op| matches!(
        op,
        HeaderOp::Set { name: n, .. } if n == "Content-Type"
    )));
    assert!(!plan_deletes_all(&plan, "Content-Encoding"));
    assert!(!plan_deletes_all(&plan, "Content-Length"));
}
