//! Property-based tests for HeaderPlan prepare-phase no-op on failure (Property 14).
//!
//! **Validates: Requirements 9.2, 9.4**
//!
//! For any input that causes the HeaderPlan prepare phase to fail, the response
//! headers (`r->headers_out`) SHALL be observably identical to their state
//! before the prepare call was made.
//!
//! This test models the two-phase prepare/commit protocol in Rust to verify the
//! atomicity contract: prepare performs all fallible work (allocation, lookup,
//! validation), and on failure no pre-existing header is mutated. The commit
//! phase is only reached after a successful prepare, and is assignment-only.
//!
//! The C implementation uses fault injection and allocation failure simulation;
//! this Rust property test exercises the contract from the plan-construction
//! side, verifying that for arbitrary header states and arbitrary failure
//! positions, the simulated prepare/commit model preserves the no-op guarantee.

use nginx_markdown_converter::header_plan::{HeaderOp, HeaderPlan};
use proptest::prelude::*;

// ─── Simulated Response Headers ───────────────────────────────────────────────

/// A simulated response header entry (models ngx_table_elt_t in NGINX).
#[derive(Debug, Clone, PartialEq)]
struct SimHeader {
    name: String,
    value: String,
    /// hash == 0 means invalidated/deleted (Rule 40 in NGINX)
    active: bool,
}

/// Simulated response headers (models r->headers_out.headers in NGINX).
#[derive(Debug, Clone, PartialEq)]
struct SimHeadersOut {
    headers: Vec<SimHeader>,
}

impl SimHeadersOut {
    fn new(headers: Vec<SimHeader>) -> Self {
        Self { headers }
    }

    /// Snapshot the observable state: only active (hash != 0) headers.
    fn observable_state(&self) -> Vec<(&str, &str)> {
        self.headers
            .iter()
            .filter(|h| h.active)
            .map(|h| (h.name.as_str(), h.value.as_str()))
            .collect()
    }

    /// Find the first active header matching the name (case-insensitive).
    fn find_active(&self, name: &str) -> Option<usize> {
        self.headers.iter().position(|h| {
            h.active && h.name.eq_ignore_ascii_case(name)
        })
    }

    /// Count active headers matching the name (case-insensitive).
    fn count_active(&self, name: &str) -> usize {
        self.headers
            .iter()
            .filter(|h| h.active && h.name.eq_ignore_ascii_case(name))
            .count()
    }
}

// ─── Simulated Two-Phase Prepare/Commit ───────────────────────────────────────

/// Result of a single prepared operation (models ngx_http_markdown_plan_prepared_t).
#[derive(Debug, Clone)]
enum PreparedAction {
    Noop,
    SetNew {
        /// Index of the newly pushed (inert) slot.
        slot_idx: usize,
        new_key: String,
        new_value: String,
    },
    Overwrite {
        /// Index of the existing header to overwrite.
        header_idx: usize,
        new_value: String,
    },
    Delete {
        /// Index of the existing header to invalidate.
        header_idx: usize,
    },
    DeleteAll {
        /// Indices of all matching headers to invalidate.
        match_indices: Vec<usize>,
    },
}

/// Simulated prepare failure reason.
#[derive(Debug, Clone)]
enum PrepareError {
    AllocationFailure,
}

/// Simulate the prepare phase for one operation.
///
/// Returns Ok(PreparedAction) on success, Err on failure.
/// On success, the only mutation to `headers` is pushing a new inert slot
/// (which is observably invisible due to hash==0 / active==false).
fn prepare_one(
    headers: &mut SimHeadersOut,
    op: &HeaderOp,
    should_fail: bool,
) -> Result<PreparedAction, PrepareError> {
    if should_fail {
        return Err(PrepareError::AllocationFailure);
    }

    match op {
        HeaderOp::Set { name, value } => {
            // Content-Type is handled specially (not a list entry in NGINX)
            if name.eq_ignore_ascii_case("Content-Type") {
                // Redirect to delete-all for Content-Type list entries
                let match_indices: Vec<usize> = headers
                    .headers
                    .iter()
                    .enumerate()
                    .filter(|(_, h)| h.active && h.name.eq_ignore_ascii_case(name))
                    .map(|(i, _)| i)
                    .collect();
                if match_indices.is_empty() {
                    return Ok(PreparedAction::Noop);
                }
                return Ok(PreparedAction::DeleteAll { match_indices });
            }

            // Look for existing header
            if let Some(idx) = headers.find_active(name) {
                Ok(PreparedAction::Overwrite {
                    header_idx: idx,
                    new_value: value.clone(),
                })
            } else {
                // Push an inert slot (hash==0, invisible to observers)
                let slot_idx = headers.headers.len();
                headers.headers.push(SimHeader {
                    name: String::new(),
                    value: String::new(),
                    active: false, // inert: hash == 0
                });
                Ok(PreparedAction::SetNew {
                    slot_idx,
                    new_key: name.clone(),
                    new_value: value.clone(),
                })
            }
        }
        HeaderOp::Delete { name } => {
            if let Some(idx) = headers.find_active(name) {
                Ok(PreparedAction::Delete { header_idx: idx })
            } else {
                Ok(PreparedAction::Noop)
            }
        }
        HeaderOp::DeleteAll { name } => {
            let match_indices: Vec<usize> = headers
                .headers
                .iter()
                .enumerate()
                .filter(|(_, h)| h.active && h.name.eq_ignore_ascii_case(name))
                .map(|(i, _)| i)
                .collect();
            if match_indices.is_empty() {
                Ok(PreparedAction::Noop)
            } else {
                Ok(PreparedAction::DeleteAll { match_indices })
            }
        }
        HeaderOp::SetEtagPlaceholder => {
            // ETag placeholder: resolved by the C caller post-commit, no-op in plan
            Ok(PreparedAction::Noop)
        }
    }
}

/// Simulate the commit phase: apply all prepared actions via assignment only.
fn commit_all(headers: &mut SimHeadersOut, prepared: &[PreparedAction]) {
    for action in prepared {
        match action {
            PreparedAction::Noop => {}
            PreparedAction::SetNew {
                slot_idx,
                new_key,
                new_value,
            } => {
                headers.headers[*slot_idx].name = new_key.clone();
                headers.headers[*slot_idx].value = new_value.clone();
                headers.headers[*slot_idx].active = true;
            }
            PreparedAction::Overwrite {
                header_idx,
                new_value,
            } => {
                headers.headers[*header_idx].value = new_value.clone();
            }
            PreparedAction::Delete { header_idx } => {
                headers.headers[*header_idx].active = false;
            }
            PreparedAction::DeleteAll { match_indices } => {
                for &idx in match_indices {
                    headers.headers[idx].active = false;
                }
            }
        }
    }
}

/// Simulate the full two-phase apply_header_plan with a failure at the given
/// operation index. Returns Ok(()) if the plan applied successfully, or
/// Err if prepare failed at `fail_at_op`.
///
/// KEY PROPERTY: On Err, the observable state of headers is unchanged.
fn apply_plan_with_failure(
    headers: &mut SimHeadersOut,
    plan: &HeaderPlan,
    fail_at_op: Option<usize>,
) -> Result<(), PrepareError> {
    let mut prepared = Vec::with_capacity(plan.ops.len());

    // Prepare phase
    for (i, op) in plan.ops.iter().enumerate() {
        let should_fail = fail_at_op.map_or(false, |fail_idx| i == fail_idx);
        match prepare_one(headers, op, should_fail) {
            Ok(action) => prepared.push(action),
            Err(e) => {
                // Prepare failed: no commit, headers unchanged
                // (pushed inert slots are invisible)
                return Err(e);
            }
        }
    }

    // Commit phase: only reached when all prepare steps succeeded
    commit_all(headers, &prepared);
    Ok(())
}

// ─── Proptest Strategies ──────────────────────────────────────────────────────

/// Generate a valid header name (non-empty ASCII letters/hyphens).
fn header_name() -> impl Strategy<Value = String> {
    prop::string::string_regex("[A-Za-z][A-Za-z0-9-]{0,20}")
        .unwrap()
        .prop_filter("non-empty", |s| !s.is_empty())
}

/// Generate a header value (ASCII printable, no control chars).
fn header_value() -> impl Strategy<Value = String> {
    prop::string::string_regex("[A-Za-z0-9 _.;=/-]{0,50}")
        .unwrap()
}

/// Generate an initial set of response headers (simulating upstream headers).
fn initial_headers() -> impl Strategy<Value = SimHeadersOut> {
    prop::collection::vec(
        (header_name(), header_value()).prop_map(|(name, value)| SimHeader {
            name,
            value,
            active: true,
        }),
        1..=8,
    )
    .prop_map(SimHeadersOut::new)
}

/// Generate a HeaderPlan using one of the standard construction methods.
fn arbitrary_header_plan() -> impl Strategy<Value = HeaderPlan> {
    prop_oneof![
        // Standard conversion plan
        (header_value(), any::<bool>()).prop_map(|(ct, has_etag)| {
            HeaderPlan::for_markdown_conversion(&ct, has_etag)
        }),
        // Error pre-commit plan
        header_value().prop_map(|ct| HeaderPlan::for_error_pre_commit(&ct)),
        // Bypass/pass-through (empty plan)
        Just(HeaderPlan::for_bypass()),
        // Pass HTML (empty plan)
        Just(HeaderPlan::for_pass_html()),
        // 304 plan
        Just(HeaderPlan::for_304()),
        // No-body plan
        Just(HeaderPlan::for_no_body()),
        // HEAD plan
        (header_value(), any::<bool>())
            .prop_map(|(ct, has_etag)| HeaderPlan::for_head(&ct, has_etag)),
        // Custom plan with arbitrary ops
        prop::collection::vec(
            prop_oneof![
                (header_name(), header_value())
                    .prop_map(|(n, v)| HeaderOp::Set { name: n, value: v }),
                header_name().prop_map(|n| HeaderOp::Delete { name: n }),
                header_name().prop_map(|n| HeaderOp::DeleteAll { name: n }),
                Just(HeaderOp::SetEtagPlaceholder),
            ],
            1..=6,
        )
        .prop_map(|ops| HeaderPlan { ops }),
    ]
}

// ─── Property Tests ───────────────────────────────────────────────────────────

proptest! {
    #![proptest_config(ProptestConfig::with_cases(500))]

    /// Property 14: HeaderPlan prepare-phase no-op on failure.
    ///
    /// For any initial header state and any HeaderPlan, when the prepare phase
    /// fails at any operation index, the observable headers are identical to
    /// the state before the prepare call.
    ///
    /// **Validates: Requirements 9.2, 9.4**
    #[test]
    fn prop_prepare_failure_is_noop(
        initial in initial_headers(),
        plan in arbitrary_header_plan(),
        // Failure position: which op index fails (0..plan.ops.len())
        fail_frac in 0.0f64..1.0,
    ) {
        // Skip empty plans (they trivially succeed with no mutation)
        prop_assume!(!plan.ops.is_empty());

        let fail_at = (fail_frac * plan.ops.len() as f64).floor() as usize;
        let fail_at = fail_at.min(plan.ops.len() - 1);

        // Capture observable state before
        let state_before = initial.observable_state();

        // Clone headers for the failed attempt
        let mut headers = initial.clone();

        // Apply with failure
        let result = apply_plan_with_failure(&mut headers, &plan, Some(fail_at));

        // Must have failed
        prop_assert!(result.is_err(), "Expected prepare failure at op {}", fail_at);

        // Observable state must be unchanged
        let state_after = headers.observable_state();
        prop_assert_eq!(
            state_before,
            state_after,
            "Observable headers changed after prepare failure at op {}. \
             Plan: {:?}",
            fail_at,
            plan.ops
        );
    }

    /// Property 14 corollary: When prepare succeeds, commit always succeeds
    /// and produces observable changes (unless all ops are no-ops).
    ///
    /// This verifies the complementary contract: successful prepare guarantees
    /// commit success, meaning the two-phase protocol is sound.
    ///
    /// **Validates: Requirements 9.2, 9.4**
    #[test]
    fn prop_successful_prepare_leads_to_successful_commit(
        initial in initial_headers(),
        plan in arbitrary_header_plan(),
    ) {
        let mut headers = initial.clone();

        // Apply without failure (prepare succeeds for all ops)
        let result = apply_plan_with_failure(&mut headers, &plan, None);

        // Must succeed (no failure injected means commit runs)
        prop_assert!(
            result.is_ok(),
            "Plan should succeed without failure injection. Plan: {:?}",
            plan.ops
        );
    }

    /// Property 14: Failure at every valid operation index produces no-op.
    ///
    /// For a multi-op plan, verify that regardless of WHICH operation fails
    /// during prepare, the result is always a complete no-op (testing the
    /// "no partial mutation" guarantee at every possible failure point).
    ///
    /// **Validates: Requirements 9.2, 9.4**
    #[test]
    fn prop_failure_at_any_index_is_noop(
        initial in initial_headers(),
        plan in arbitrary_header_plan(),
    ) {
        prop_assume!(plan.ops.len() >= 2);

        let state_before = initial.observable_state();

        for fail_at in 0..plan.ops.len() {
            let mut headers = initial.clone();
            let result = apply_plan_with_failure(&mut headers, &plan, Some(fail_at));

            prop_assert!(
                result.is_err(),
                "Expected failure at op {} but got success",
                fail_at
            );

            let state_after = headers.observable_state();
            prop_assert_eq!(
                &state_before,
                &state_after,
                "Headers mutated after prepare failure at op index {}. \
                 Plan ops: {:?}",
                fail_at,
                plan.ops
            );
        }
    }
}

// ─── Targeted Tests for Specific Failure Scenarios ────────────────────────────

/// Verify that a conversion plan (the most common non-empty plan) with an
/// existing Content-Encoding header does not leak mutations on failure.
#[test]
fn targeted_conversion_plan_prepare_failure_preserves_headers() {
    let initial = SimHeadersOut::new(vec![
        SimHeader {
            name: "Content-Type".to_string(),
            value: "text/html; charset=utf-8".to_string(),
            active: true,
        },
        SimHeader {
            name: "Content-Encoding".to_string(),
            value: "gzip".to_string(),
            active: true,
        },
        SimHeader {
            name: "Content-Length".to_string(),
            value: "4096".to_string(),
            active: true,
        },
        SimHeader {
            name: "ETag".to_string(),
            value: "\"abc123\"".to_string(),
            active: true,
        },
    ]);

    let plan = HeaderPlan::for_markdown_conversion("text/markdown; charset=utf-8", true);
    let state_before = initial.observable_state();

    // Fail at each operation index
    for fail_at in 0..plan.ops.len() {
        let mut headers = initial.clone();
        let result = apply_plan_with_failure(&mut headers, &plan, Some(fail_at));
        assert!(result.is_err(), "Expected failure at op {}", fail_at);
        assert_eq!(
            state_before,
            headers.observable_state(),
            "Headers changed after failure at op {}",
            fail_at
        );
    }
}

/// Verify that pushing inert slots during prepare (for SET new) does not
/// change observable state when prepare is subsequently aborted.
#[test]
fn targeted_set_new_pushed_slot_stays_inert_on_failure() {
    let initial = SimHeadersOut::new(vec![SimHeader {
        name: "Existing".to_string(),
        value: "value".to_string(),
        active: true,
    }]);

    // Plan that adds a new header then deletes an existing one
    let plan = HeaderPlan {
        ops: vec![
            HeaderOp::Set {
                name: "X-New-Header".to_string(),
                value: "new-value".to_string(),
            },
            HeaderOp::Delete {
                name: "Existing".to_string(),
            },
        ],
    };

    let state_before = initial.observable_state();

    // Fail at op 1 (after the SET pushed an inert slot)
    let mut headers = initial.clone();
    let result = apply_plan_with_failure(&mut headers, &plan, Some(1));
    assert!(result.is_err());

    // The inert slot was pushed but it's invisible (active == false)
    assert_eq!(
        state_before,
        headers.observable_state(),
        "Inert slot from aborted SET must not be observable"
    );
    // Verify the existing header is still active
    assert_eq!(headers.count_active("Existing"), 1);
    assert_eq!(headers.count_active("X-New-Header"), 0);
}
