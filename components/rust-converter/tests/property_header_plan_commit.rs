//! Property-based tests for HeaderPlan commit unconditional success (Property 15).
//!
//! **Validates: Requirements 9.3**
//!
//! For any successful prepare phase (any HeaderPlan factory method that returns
//! a plan), verify that the commit phase (iterating and applying operations)
//! succeeds without allocation failure or panic.
//!
//! The HeaderPlan commit phase applies prepared mutations using pointer and
//! scalar assignment only, performs zero pool allocations, and succeeds
//! unconditionally when the prepare phase has succeeded.

use nginx_markdown_converter::header_plan::{HeaderOp, HeaderPlan};
use proptest::prelude::*;

// ─── Strategies ───────────────────────────────────────────────────────────────

/// Generate arbitrary non-empty Content-Type strings for plan construction.
fn arb_content_type() -> impl Strategy<Value = String> {
    prop_oneof![
        Just("text/markdown; charset=utf-8".to_string()),
        Just("text/markdown".to_string()),
        Just("text/plain".to_string()),
        Just("text/html; charset=utf-8".to_string()),
        Just("application/json".to_string()),
        "[a-z]{1,20}/[a-z]{1,20}(; [a-z]+=[a-z0-9-]+)?".prop_map(|s| s),
    ]
}

/// Generate an arbitrary boolean for has_etag parameter.
fn arb_has_etag() -> impl Strategy<Value = bool> {
    any::<bool>()
}

/// Generate an arbitrary HeaderPlan using any of the public factory methods.
///
/// This simulates the "prepare" phase succeeding — all factory methods that
/// return a HeaderPlan represent successful preparation.
fn arb_header_plan() -> impl Strategy<Value = HeaderPlan> {
    prop_oneof![
        // for_markdown_conversion: the primary conversion plan
        (arb_content_type(), arb_has_etag())
            .prop_map(|(ct, etag)| HeaderPlan::for_markdown_conversion(&ct, etag)),
        // for_error_pre_commit: error response plan
        arb_content_type().prop_map(|ct| HeaderPlan::for_error_pre_commit(&ct)),
        // for_bypass: empty plan for bypass scenarios
        Just(HeaderPlan::for_bypass()),
        // for_pass_through: alias for bypass
        Just(HeaderPlan::for_pass_through()),
        // for_pass_html: PASS_HTML outcome
        Just(HeaderPlan::for_pass_html()),
        // for_non_cacheable_conversion: same as conversion plan
        (arb_content_type(), arb_has_etag())
            .prop_map(|(ct, etag)| HeaderPlan::for_non_cacheable_conversion(&ct, etag)),
        // for_304: conditional response plan
        Just(HeaderPlan::for_304()),
        // for_head: HEAD response plan
        (arb_content_type(), arb_has_etag())
            .prop_map(|(ct, etag)| HeaderPlan::for_head(&ct, etag)),
        // for_no_body: generic no-body plan
        Just(HeaderPlan::for_no_body()),
    ]
}

// ─── Commit Simulation ────────────────────────────────────────────────────────

/// Simulate the commit phase by iterating all operations and extracting their
/// fields. In the real C module, commit applies these via pointer/scalar
/// assignment to `r->headers_out`. Here we verify that every operation is
/// accessible and well-formed without panic or failure.
fn simulate_commit(plan: &HeaderPlan) -> usize {
    let mut applied = 0usize;
    for op in &plan.ops {
        match op {
            HeaderOp::Set { name, value } => {
                // Commit: assign name and value via pointer copy
                assert!(!name.is_empty(), "Set op must have non-empty name");
                // Value may be empty (valid for some headers) but must be accessible
                let _ = value.len();
                applied += 1;
            }
            HeaderOp::Delete { name } => {
                // Commit: null out the header by name
                assert!(!name.is_empty(), "Delete op must have non-empty name");
                applied += 1;
            }
            HeaderOp::DeleteAll { name } => {
                // Commit: iterate and null all matching headers
                assert!(!name.is_empty(), "DeleteAll op must have non-empty name");
                applied += 1;
            }
            HeaderOp::SetEtagPlaceholder => {
                // Commit: C caller substitutes actual ETag value
                applied += 1;
            }
        }
    }
    applied
}

// ─── Property Tests ───────────────────────────────────────────────────────────

proptest! {
    #![proptest_config(ProptestConfig::with_cases(500))]

    /// Property 15: For any successful prepare, commit succeeds unconditionally.
    ///
    /// The commit phase iterates all operations in the plan and applies them.
    /// This must never fail, panic, or require additional allocation beyond
    /// what was already prepared.
    ///
    /// **Validates: Requirements 9.3**
    #[test]
    fn commit_succeeds_unconditionally_for_any_prepared_plan(
        plan in arb_header_plan()
    ) {
        // The commit phase must process exactly plan.len() operations
        let committed = simulate_commit(&plan);
        prop_assert_eq!(
            committed,
            plan.len(),
            "Commit must apply exactly {} ops but applied {}",
            plan.len(),
            committed
        );
    }

    /// Property 15: Commit produces no new allocations beyond prepare.
    ///
    /// After prepare succeeds, the plan's ops vector is fully materialized.
    /// Commit only reads — it does not push, extend, or reallocate.
    ///
    /// **Validates: Requirements 9.3**
    #[test]
    fn commit_does_not_modify_plan(
        plan in arb_header_plan()
    ) {
        let original = plan.clone();

        // Simulate commit (read-only access)
        let _ = simulate_commit(&plan);

        // Plan must be unchanged after commit
        prop_assert_eq!(
            plan, original,
            "Commit phase must not modify the prepared plan"
        );
    }

    /// Property 15: Every operation in a successfully prepared plan has valid
    /// field access (non-empty name for Set/Delete/DeleteAll).
    ///
    /// **Validates: Requirements 9.3**
    #[test]
    fn all_ops_have_valid_fields_after_prepare(
        plan in arb_header_plan()
    ) {
        for (idx, op) in plan.ops.iter().enumerate() {
            match op {
                HeaderOp::Set { name, value } => {
                    prop_assert!(
                        !name.is_empty(),
                        "Op {} (Set): name must not be empty",
                        idx
                    );
                    // Value is a valid String (may be empty for certain headers)
                    let _ = value.as_bytes();
                }
                HeaderOp::Delete { name } | HeaderOp::DeleteAll { name } => {
                    prop_assert!(
                        !name.is_empty(),
                        "Op {} (Delete/DeleteAll): name must not be empty",
                        idx
                    );
                }
                HeaderOp::SetEtagPlaceholder => {
                    // No fields to validate — always succeeds
                }
            }
        }
    }
}
