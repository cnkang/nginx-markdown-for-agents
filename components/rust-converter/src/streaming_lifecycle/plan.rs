//! Plan phase of the two-phase decision protocol.
//!
//! `plan(state, event, plan_context) → {plan_decision, first_frame}`
//!
//! Validates the Event Failure Policy and normalizes any required event
//! record into the ledger before producing the plan decision and first
//! transition frame.

use super::policy::{EventFailurePolicy, event_failure_policy};
use super::types::*;

/// Execute the plan phase of the streaming lifecycle state machine.
///
/// Validates the event's failure-record policy, normalizes required records
/// into the ledger, and returns the plan decision with the first frame.
///
/// # Errors
///
/// Returns `StateMachineError` if:
/// - A required failure record is missing
/// - A forbidden failure record is provided
/// - The (state, event) pair is not in the Plan table
pub fn plan(
    state: StreamingState,
    event: EventEnvelope,
    ctx: &PlanContext,
) -> Result<PlanResult, StateMachineError> {
    /* Phase 1: validate Event Failure Policy */
    let policy = event_failure_policy(event.kind);
    match policy {
        EventFailurePolicy::Required => {
            if event.failure_record.is_none() {
                return Err(StateMachineError::MissingRequiredFailureRecord { event: event.kind });
            }
        }
        EventFailurePolicy::Forbidden => {
            if event.failure_record.is_some() {
                return Err(StateMachineError::ForbiddenFailureRecord { event: event.kind });
            }
        }
        EventFailurePolicy::ReusePersisted => {
            /* RESUME_DRAIN reuses the persisted ledger; no new record validation */
        }
    }

    /* Phase 2: normalize required record into ledger */
    let mut ledger = ctx.failure_ledger.clone();
    if policy == EventFailurePolicy::Required {
        if let Some(ref record) = event.failure_record {
            if ledger.primary.is_none() {
                ledger.primary = Some(record.clone());
            } else {
                ledger.secondary = Some(record.clone());
            }
        }
    }

    /* Phase 3: look up the (state, event) pair in the Plan table */
    let transition = lookup_plan_transition(state, event.kind, ctx)?;

    /* Validate action/payload invariant */
    validate_action_payload(transition.action, &transition.action_payload)?;

    let plan_decision = PlanDecision {
        event: event.clone(),
        initial_action: transition.action,
        action_payload: transition.action_payload,
        reason: transition.reason.clone(),
        transition_id: transition.transition_id.clone(),
        failure_ledger: ledger.clone(),
    };

    let first_frame = TransitionFrame {
        transition_id: transition.transition_id.clone(),
        step_id: transition.transition_id.clone(),
        action: transition.action,
        action_payload: transition.action_payload,
        event: event.clone(),
        reason: transition.reason,
        failure_ledger: ledger,
    };

    Ok(PlanResult {
        plan_decision,
        first_frame,
    })
}

/// Validate that the action/payload pairing is legal.
fn validate_action_payload(
    action: Action,
    payload: &ActionPayload,
) -> Result<(), StateMachineError> {
    match action {
        Action::RejectStatus => {
            if payload.reject_status.is_none() {
                return Err(StateMachineError::ActionPayloadMismatch { action });
            }
        }
        _ => {
            if payload.reject_status.is_some() {
                return Err(StateMachineError::ActionPayloadMismatch { action });
            }
        }
    }
    Ok(())
}

/// Internal struct for plan table lookup results.
struct PlanTransition {
    action: Action,
    action_payload: ActionPayload,
    reason: String,
    transition_id: String,
}

/// Look up the authoritative Plan table for a (state, event) pair.
///
/// Returns the action, payload, reason, and transition ID for the given
/// state and event, taking into account context predicates.
///
/// The Plan table is the sole authority: every (state, event) pair not
/// present is rejected as an invariant violation.
fn lookup_plan_transition(
    state: StreamingState,
    event: EventKind,
    ctx: &PlanContext,
) -> Result<PlanTransition, StateMachineError> {
    /* Avoid glob imports to prevent ambiguity between
     * StreamingState::NotEligible/Passthrough and EventKind/Action variants */

    match (state, event) {
        /* PLAN-01: NOT_ELIGIBLE + ELIGIBLE → NONE */
        (StreamingState::NotEligible, EventKind::Eligible) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "eligible".to_string(),
            transition_id: "PLAN-01".to_string(),
        }),

        /* PLAN-02: NOT_ELIGIBLE + NOT_ELIGIBLE → PASSTHROUGH */
        (StreamingState::NotEligible, EventKind::NotEligible) => Ok(PlanTransition {
            action: Action::Passthrough,
            action_payload: ActionPayload::NULL,
            reason: "not_eligible".to_string(),
            transition_id: "PLAN-02".to_string(),
        }),

        /* PLAN-03: STREAMING_CANDIDATE + STREAMING_START → NONE */
        (StreamingState::StreamingCandidate, EventKind::StreamingStart) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "streaming_start".to_string(),
            transition_id: "PLAN-03".to_string(),
        }),

        /* PLAN-04: STREAMING_CANDIDATE + HARD_EXCLUDED → PASSTHROUGH */
        (StreamingState::StreamingCandidate, EventKind::HardExcluded) => Ok(PlanTransition {
            action: Action::Passthrough,
            action_payload: ActionPayload::NULL,
            reason: "hard_excluded".to_string(),
            transition_id: "PLAN-04".to_string(),
        }),

        /* PLAN-05: STREAMING_CANDIDATE + FULL_DOC_FEATURE → SWITCH_FULL_BUFFER */
        (StreamingState::StreamingCandidate, EventKind::FullDocFeature) => Ok(PlanTransition {
            action: Action::SwitchFullBuffer,
            action_payload: ActionPayload::NULL,
            reason: "full_doc_feature".to_string(),
            transition_id: "PLAN-05".to_string(),
        }),

        /* PLAN-06: STREAMING_CANDIDATE + STRICT_ETAG → SWITCH_FULL_BUFFER */
        (StreamingState::StreamingCandidate, EventKind::StrictEtag) => Ok(PlanTransition {
            action: Action::SwitchFullBuffer,
            action_payload: ActionPayload::NULL,
            reason: "strict_etag".to_string(),
            transition_id: "PLAN-06".to_string(),
        }),

        /* PLAN-07: STREAMING_CANDIDATE + AUTO_RISK → SWITCH_FULL_BUFFER */
        (StreamingState::StreamingCandidate, EventKind::AutoRisk) => Ok(PlanTransition {
            action: Action::SwitchFullBuffer,
            action_payload: ActionPayload::NULL,
            reason: "auto_risk".to_string(),
            transition_id: "PLAN-07".to_string(),
        }),

        /* PLAN-08: STREAMING_CANDIDATE + BUDGET_INIT_FAILURE → context-dependent */
        (StreamingState::StreamingCandidate, EventKind::BudgetInitFailure) => {
            plan_precommit_error_recovery(ctx, "budget_init_failure", "PLAN-08")
        }

        /* PLAN-09: PRE_COMMIT + REPLAY_OVERFLOW → NONE (state transition) */
        (StreamingState::PreCommit, EventKind::ReplayOverflow) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "replay_overflow".to_string(),
            transition_id: "PLAN-09".to_string(),
        }),

        /* PLAN-10: PRE_COMMIT + LOOK_BEHIND_OVERFLOW → NONE (state transition) */
        (StreamingState::PreCommit, EventKind::LookBehindOverflow) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "look_behind_overflow".to_string(),
            transition_id: "PLAN-10".to_string(),
        }),

        /* PLAN-11: PRE_COMMIT + PARSER_UNSUITABLE → context-dependent */
        (StreamingState::PreCommit, EventKind::ParserUnsuitable) => {
            plan_precommit_fallback(ctx, "parser_unsuitable", "PLAN-11")
        }

        /* PLAN-12: PRE_COMMIT + RESOURCE_LIMIT → context-dependent */
        (StreamingState::PreCommit, EventKind::ResourceLimit) => {
            plan_precommit_fallback(ctx, "resource_limit", "PLAN-12")
        }

        /* PLAN-13: PRE_COMMIT + ERROR → context-dependent */
        (StreamingState::PreCommit, EventKind::Error) => {
            plan_precommit_error_recovery(ctx, "error", "PLAN-13")
        }

        /* PLAN-14: PRE_COMMIT + COMMIT → COMMIT_HEADERS */
        (StreamingState::PreCommit, EventKind::Commit) => Ok(PlanTransition {
            action: Action::CommitHeaders,
            action_payload: ActionPayload::NULL,
            reason: "commit".to_string(),
            transition_id: "PLAN-14".to_string(),
        }),

        /* PLAN-15: PRE_COMMIT_REPLAY_UNAVAILABLE + ERROR → context-dependent */
        (StreamingState::PreCommitReplayUnavailable, EventKind::Error) => {
            plan_replay_unavailable_error(ctx, "PLAN-15")
        }

        /* PLAN-16: PRE_COMMIT_REPLAY_UNAVAILABLE + RESOURCE_LIMIT → context-dep */
        (StreamingState::PreCommitReplayUnavailable, EventKind::ResourceLimit) => {
            plan_replay_unavailable_error(ctx, "PLAN-16")
        }

        /* PLAN-17: PRE_COMMIT_REPLAY_UNAVAILABLE + COMMIT → COMMIT_HEADERS */
        (StreamingState::PreCommitReplayUnavailable, EventKind::Commit) => Ok(PlanTransition {
            action: Action::CommitHeaders,
            action_payload: ActionPayload::NULL,
            reason: "commit".to_string(),
            transition_id: "PLAN-17".to_string(),
        }),

        /* PLAN-18: PRE_COMMIT_REPLAY_UNAVAILABLE + PARSER_UNSUITABLE → ctx-dep */
        (StreamingState::PreCommitReplayUnavailable, EventKind::ParserUnsuitable) => {
            plan_replay_unavailable_error(ctx, "PLAN-18")
        }

        /* PLAN-19: COMMITTED + COMMIT (redundant commit) → invariant violation */
        (StreamingState::Committed, EventKind::Commit) => {
            Err(StateMachineError::InvariantViolation {
                message: "COMMIT in COMMITTED state".to_string(),
            })
        }

        /* PLAN-20: COMMITTED + ERROR → policy-dependent */
        (StreamingState::Committed, EventKind::Error) => plan_postcommit_error(ctx, "PLAN-20"),

        /* PLAN-21: COMMITTED + UPSTREAM_END → FINALIZE_CONVERTER */
        (StreamingState::Committed, EventKind::UpstreamEnd) => Ok(PlanTransition {
            action: Action::FinalizeConverter,
            action_payload: ActionPayload::NULL,
            reason: "upstream_end".to_string(),
            transition_id: "PLAN-21".to_string(),
        }),

        /* PLAN-22: COMMITTED + CLIENT_ABORT → NONE (ABORTED) */
        (StreamingState::Committed, EventKind::ClientAbort) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "client_abort".to_string(),
            transition_id: "PLAN-22".to_string(),
        }),

        /* PLAN-23: POST_COMMIT_SAFE_FINISH + CLIENT_ABORT → NONE (ABORTED) */
        (StreamingState::PostCommitSafeFinish, EventKind::ClientAbort) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "client_abort".to_string(),
            transition_id: "PLAN-23".to_string(),
        }),

        /* PLAN-24: POST_COMMIT_ABORT + CLIENT_ABORT → NONE (ABORTED) */
        (StreamingState::PostCommitAbort, EventKind::ClientAbort) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "client_abort".to_string(),
            transition_id: "PLAN-24".to_string(),
        }),

        /* PLAN-25: PENDING_CLOSING_OUTPUT + CLIENT_ABORT → NONE (ABORTED) */
        (StreamingState::PendingClosingOutput, EventKind::ClientAbort) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "client_abort".to_string(),
            transition_id: "PLAN-25".to_string(),
        }),

        /* PLAN-26: PENDING_TERMINAL + CLIENT_ABORT → NONE (ABORTED) */
        (StreamingState::PendingTerminal, EventKind::ClientAbort) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "client_abort".to_string(),
            transition_id: "PLAN-26".to_string(),
        }),

        /* PLAN-27: PENDING_ABORT_TERMINAL + CLIENT_ABORT → NONE (ABORTED) */
        (StreamingState::PendingAbortTerminal, EventKind::ClientAbort) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "client_abort".to_string(),
            transition_id: "PLAN-27".to_string(),
        }),

        /* PLAN-28: PENDING_CLOSING_OUTPUT + RESUME_DRAIN → RESUME_PENDING */
        (StreamingState::PendingClosingOutput, EventKind::ResumeDrain) => Ok(PlanTransition {
            action: Action::ResumePending,
            action_payload: ActionPayload::NULL,
            reason: "resume_drain".to_string(),
            transition_id: "PLAN-28".to_string(),
        }),

        /* PLAN-29: PENDING_TERMINAL + RESUME_DRAIN → RESUME_PENDING */
        (StreamingState::PendingTerminal, EventKind::ResumeDrain) => Ok(PlanTransition {
            action: Action::ResumePending,
            action_payload: ActionPayload::NULL,
            reason: "resume_drain".to_string(),
            transition_id: "PLAN-29".to_string(),
        }),

        /* PLAN-30: PENDING_ABORT_TERMINAL + RESUME_DRAIN → RESUME_PENDING */
        (StreamingState::PendingAbortTerminal, EventKind::ResumeDrain) => Ok(PlanTransition {
            action: Action::ResumePending,
            action_payload: ActionPayload::NULL,
            reason: "resume_drain".to_string(),
            transition_id: "PLAN-30".to_string(),
        }),

        /* PLAN-31: COMMITTED + UPSTREAM_END (alt: postcommit non-error finish)
         * This is handled by PLAN-21. Error after UPSTREAM_END is a separate
         * flow handled by apply_result chain. */

        /* PLAN-32: DONE + BODY_FILTER_REENTRY → NONE (idempotent) */
        (StreamingState::Done, EventKind::BodyFilterReentry) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "body_filter_reentry_after_terminal".to_string(),
            transition_id: "PLAN-32".to_string(),
        }),

        /* PLAN-33: ABORTED + BODY_FILTER_REENTRY → NONE (idempotent) */
        (StreamingState::Aborted, EventKind::BodyFilterReentry) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "body_filter_reentry_after_terminal".to_string(),
            transition_id: "PLAN-33".to_string(),
        }),

        /* PLAN-33b: FAILED_CLOSED + BODY_FILTER_REENTRY → NONE (idempotent) */
        (StreamingState::FailedClosed, EventKind::BodyFilterReentry) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "body_filter_reentry_after_terminal".to_string(),
            transition_id: "PLAN-33b".to_string(),
        }),

        /* PLAN-34..PLAN-48: CLEANUP in every state (15 rows) */
        (_, EventKind::Cleanup) => Ok(PlanTransition {
            action: Action::None,
            action_payload: ActionPayload::NULL,
            reason: "cleanup".to_string(),
            transition_id: format!("PLAN-{}", cleanup_plan_id(state)),
        }),

        /* All other (state, event) pairs are invalid */
        _ => Err(StateMachineError::InvalidTransition { state, event }),
    }
}

/// Map each state to its CLEANUP Plan ID (PLAN-34 through PLAN-48).
fn cleanup_plan_id(state: StreamingState) -> u8 {
    match state {
        StreamingState::NotEligible => 34,
        StreamingState::StreamingCandidate => 35,
        StreamingState::PreCommit => 36,
        StreamingState::PreCommitReplayUnavailable => 37,
        StreamingState::FullBufferFallback => 38,
        StreamingState::Passthrough => 39,
        StreamingState::Committed => 40,
        StreamingState::PostCommitSafeFinish => 41,
        StreamingState::PostCommitAbort => 42,
        StreamingState::PendingClosingOutput => 43,
        StreamingState::PendingTerminal => 44,
        StreamingState::PendingAbortTerminal => 45,
        StreamingState::Done => 46,
        StreamingState::Aborted => 47,
        StreamingState::FailedClosed => 48,
    }
}

/// PRE_COMMIT fallback logic for PARSER_UNSUITABLE and RESOURCE_LIMIT.
///
/// Truth table:
/// - full_input_reconstructible AND full_buffer_resources_allow → SWITCH_FULL_BUFFER
/// - Otherwise, PASS → PASS_HTML
/// - Otherwise, STATUS_xxx → REJECT_STATUS
fn plan_precommit_fallback(
    ctx: &PlanContext,
    reason: &str,
    plan_id: &str,
) -> Result<PlanTransition, StateMachineError> {
    if ctx.full_input_reconstructible && ctx.full_buffer_resources_allow {
        Ok(PlanTransition {
            action: Action::SwitchFullBuffer,
            action_payload: ActionPayload::NULL,
            reason: reason.to_string(),
            transition_id: plan_id.to_string(),
        })
    } else {
        match ctx.resolved_error_policy {
            ResolvedErrorPolicy::Pass => Ok(PlanTransition {
                action: Action::PassHtml,
                action_payload: ActionPayload::NULL,
                reason: reason.to_string(),
                transition_id: plan_id.to_string(),
            }),
            _ => {
                let status = resolve_reject_status(ctx.resolved_error_policy, reason);
                Ok(PlanTransition {
                    action: Action::RejectStatus,
                    action_payload: ActionPayload {
                        reject_status: status,
                    },
                    reason: reason.to_string(),
                    transition_id: plan_id.to_string(),
                })
            }
        }
    }
}

/// PRE_COMMIT error recovery (includes BUDGET_INIT_FAILURE in STREAMING_CANDIDATE).
fn plan_precommit_error_recovery(
    ctx: &PlanContext,
    reason: &str,
    plan_id: &str,
) -> Result<PlanTransition, StateMachineError> {
    if ctx.full_input_reconstructible && ctx.full_buffer_resources_allow {
        Ok(PlanTransition {
            action: Action::SwitchFullBuffer,
            action_payload: ActionPayload::NULL,
            reason: reason.to_string(),
            transition_id: plan_id.to_string(),
        })
    } else {
        match ctx.resolved_error_policy {
            ResolvedErrorPolicy::Pass => Ok(PlanTransition {
                action: Action::PassHtml,
                action_payload: ActionPayload::NULL,
                reason: reason.to_string(),
                transition_id: plan_id.to_string(),
            }),
            _ => {
                let status = resolve_reject_status(ctx.resolved_error_policy, reason);
                Ok(PlanTransition {
                    action: Action::RejectStatus,
                    action_payload: ActionPayload {
                        reject_status: status,
                    },
                    reason: reason.to_string(),
                    transition_id: plan_id.to_string(),
                })
            }
        }
    }
}

/// PRE_COMMIT_REPLAY_UNAVAILABLE + ERROR handling.
///
/// Truth table:
/// - full_input_reconstructible AND full_buffer_resources_allow → SWITCH_FULL_BUFFER
/// - PASS policy: fail_open_unavailable → REJECT_STATUS(502) (NOT PASS_HTML)
/// - STATUS_xxx → REJECT_STATUS
fn plan_replay_unavailable_error(
    ctx: &PlanContext,
    plan_id: &str,
) -> Result<PlanTransition, StateMachineError> {
    if ctx.full_input_reconstructible && ctx.full_buffer_resources_allow {
        Ok(PlanTransition {
            action: Action::SwitchFullBuffer,
            action_payload: ActionPayload::NULL,
            reason: "fail_open_unavailable".to_string(),
            transition_id: plan_id.to_string(),
        })
    } else {
        /* PASS with replay unavailable → REJECT_STATUS(502), NOT PASS_HTML */
        let reason = "fail_open_unavailable";
        let status = resolve_reject_status(ctx.resolved_error_policy, reason);
        Ok(PlanTransition {
            action: Action::RejectStatus,
            action_payload: ActionPayload {
                reject_status: status,
            },
            reason: reason.to_string(),
            transition_id: plan_id.to_string(),
        })
    }
}

/// Post-commit error handling (COMMITTED + ERROR).
///
/// - PASS → FINALIZE_CONVERTER (graceful finish attempt)
/// - Non-PASS → BEGIN_ABORT
fn plan_postcommit_error(
    ctx: &PlanContext,
    plan_id: &str,
) -> Result<PlanTransition, StateMachineError> {
    match ctx.resolved_error_policy {
        ResolvedErrorPolicy::Pass => Ok(PlanTransition {
            action: Action::FinalizeConverter,
            action_payload: ActionPayload::NULL,
            reason: "error_pass_finish".to_string(),
            transition_id: plan_id.to_string(),
        }),
        _ => Ok(PlanTransition {
            action: Action::BeginAbort,
            action_payload: ActionPayload::NULL,
            reason: "error_reject_abort".to_string(),
            transition_id: plan_id.to_string(),
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn default_ctx() -> PlanContext {
        PlanContext {
            resolved_error_policy: ResolvedErrorPolicy::Pass,
            full_input_reconstructible: true,
            full_buffer_resources_allow: true,
            failure_ledger: FailureLedger::empty(),
        }
    }

    fn error_event() -> EventEnvelope {
        EventEnvelope {
            kind: EventKind::Error,
            failure_record: Some(FailureRecord {
                stage: "streaming".to_string(),
                reason: "internal".to_string(),
                error_origin: ErrorOrigin::Internal,
                failure_site: None,
            }),
        }
    }

    #[test]
    fn plan_eligible_from_not_eligible() {
        let ctx = default_ctx();
        let event = EventEnvelope {
            kind: EventKind::Eligible,
            failure_record: None,
        };
        let result = plan(StreamingState::NotEligible, event, &ctx).unwrap();
        assert_eq!(result.first_frame.action, Action::None);
        assert_eq!(result.plan_decision.transition_id, "PLAN-01");
    }

    #[test]
    fn plan_rejects_missing_failure_record() {
        let ctx = default_ctx();
        let event = EventEnvelope {
            kind: EventKind::Error,
            failure_record: None,
        };
        let result = plan(StreamingState::PreCommit, event, &ctx);
        assert!(matches!(
            result,
            Err(StateMachineError::MissingRequiredFailureRecord { .. })
        ));
    }

    #[test]
    fn plan_rejects_forbidden_failure_record() {
        let ctx = default_ctx();
        let event = EventEnvelope {
            kind: EventKind::Eligible,
            failure_record: Some(FailureRecord {
                stage: "x".to_string(),
                reason: "x".to_string(),
                error_origin: ErrorOrigin::Internal,
                failure_site: None,
            }),
        };
        let result = plan(StreamingState::NotEligible, event, &ctx);
        assert!(matches!(
            result,
            Err(StateMachineError::ForbiddenFailureRecord { .. })
        ));
    }

    #[test]
    fn plan_precommit_error_with_full_buffer() {
        let ctx = default_ctx();
        let result = plan(StreamingState::PreCommit, error_event(), &ctx).unwrap();
        assert_eq!(result.first_frame.action, Action::SwitchFullBuffer);
    }

    #[test]
    fn plan_precommit_error_pass_no_full_buffer() {
        let mut ctx = default_ctx();
        ctx.full_buffer_resources_allow = false;
        let result = plan(StreamingState::PreCommit, error_event(), &ctx).unwrap();
        assert_eq!(result.first_frame.action, Action::PassHtml);
    }

    #[test]
    fn plan_precommit_error_reject_no_full_buffer() {
        let mut ctx = default_ctx();
        ctx.full_buffer_resources_allow = false;
        ctx.resolved_error_policy = ResolvedErrorPolicy::Status502;
        let result = plan(StreamingState::PreCommit, error_event(), &ctx).unwrap();
        assert_eq!(result.first_frame.action, Action::RejectStatus);
        assert_eq!(result.first_frame.action_payload.reject_status, Some(502));
    }

    #[test]
    fn plan_replay_unavailable_pass_not_pass_html() {
        let mut ctx = default_ctx();
        ctx.full_input_reconstructible = false;
        ctx.full_buffer_resources_allow = false;
        let result = plan(
            StreamingState::PreCommitReplayUnavailable,
            error_event(),
            &ctx,
        )
        .unwrap();
        /* PASS with replay unavailable → REJECT_STATUS(502), NOT PASS_HTML */
        assert_eq!(result.first_frame.action, Action::RejectStatus);
        assert_eq!(result.first_frame.action_payload.reject_status, Some(502));
    }

    #[test]
    fn plan_cleanup_available_in_all_states() {
        let ctx = default_ctx();
        let all_states = [
            StreamingState::NotEligible,
            StreamingState::StreamingCandidate,
            StreamingState::PreCommit,
            StreamingState::PreCommitReplayUnavailable,
            StreamingState::FullBufferFallback,
            StreamingState::Passthrough,
            StreamingState::Committed,
            StreamingState::PostCommitSafeFinish,
            StreamingState::PostCommitAbort,
            StreamingState::PendingClosingOutput,
            StreamingState::PendingTerminal,
            StreamingState::PendingAbortTerminal,
            StreamingState::Done,
            StreamingState::Aborted,
            StreamingState::FailedClosed,
        ];
        for state in &all_states {
            let event = EventEnvelope {
                kind: EventKind::Cleanup,
                failure_record: None,
            };
            let result = plan(*state, event, &ctx);
            assert!(result.is_ok(), "CLEANUP failed for state {:?}", state);
            let r = result.unwrap();
            assert_eq!(r.first_frame.action, Action::None);
        }
    }

    #[test]
    fn plan_body_filter_reentry_terminal_states() {
        let ctx = default_ctx();
        for state in [
            StreamingState::Done,
            StreamingState::Aborted,
            StreamingState::FailedClosed,
        ] {
            let event = EventEnvelope {
                kind: EventKind::BodyFilterReentry,
                failure_record: None,
            };
            let result = plan(state, event, &ctx).unwrap();
            assert_eq!(result.first_frame.action, Action::None);
        }
    }

    #[test]
    fn plan_client_abort_all_committed_states() {
        let ctx = default_ctx();
        let committed_states = [
            StreamingState::Committed,
            StreamingState::PostCommitSafeFinish,
            StreamingState::PostCommitAbort,
            StreamingState::PendingClosingOutput,
            StreamingState::PendingTerminal,
            StreamingState::PendingAbortTerminal,
        ];
        for state in &committed_states {
            let event = EventEnvelope {
                kind: EventKind::ClientAbort,
                failure_record: None,
            };
            let result = plan(*state, event, &ctx).unwrap();
            assert_eq!(
                result.first_frame.action,
                Action::None,
                "CLIENT_ABORT in {:?} should produce NONE",
                state
            );
        }
    }

    #[test]
    fn plan_invalid_transition_rejected() {
        let ctx = default_ctx();
        let event = EventEnvelope {
            kind: EventKind::Commit,
            failure_record: None,
        };
        let result = plan(StreamingState::Done, event, &ctx);
        assert!(matches!(
            result,
            Err(StateMachineError::InvalidTransition { .. })
        ));
    }
}
