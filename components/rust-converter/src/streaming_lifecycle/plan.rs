//! Plan phase of the two-phase decision protocol.
//!
//! `plan(state, event, plan_context) → {plan_decision, first_frame}`
//!
//! Validates the Event Failure Policy and normalizes any required event
//! record into the ledger before producing the plan decision and first
//! transition frame.

use super::policy::{EventFailurePolicy, event_failure_policy};
use super::types::{
    Action, ActionPayload, EventEnvelope, EventKind, PlanContext, PlanDecision, PlanResult,
    ResolvedErrorPolicy, StateMachineError, StreamingState, TransitionFrame, resolve_reject_status,
};

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
    if policy == EventFailurePolicy::Required
        && let Some(ref record) = event.failure_record
    {
        if ledger.primary.is_none() {
            ledger.primary = Some(record.clone());
        } else {
            ledger.secondary = Some(record.clone());
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
    if event == EventKind::Cleanup {
        return plan_action(
            Action::None,
            "cleanup",
            &format!("PLAN-{}", cleanup_plan_id(state)),
        );
    }

    match state {
        StreamingState::NotEligible => lookup_not_eligible(event),
        StreamingState::StreamingCandidate => lookup_streaming_candidate(event, ctx),
        StreamingState::PreCommit => lookup_pre_commit(event, ctx),
        StreamingState::PreCommitReplayUnavailable => lookup_replay_unavailable(event, ctx),
        StreamingState::Committed => lookup_committed(event, ctx),
        StreamingState::PostCommitSafeFinish
        | StreamingState::PostCommitAbort
        | StreamingState::PendingClosingOutput
        | StreamingState::PendingTerminal
        | StreamingState::PendingAbortTerminal => lookup_pending(state, event),
        StreamingState::Done | StreamingState::Aborted | StreamingState::FailedClosed => {
            lookup_terminal(state, event)
        }
        _ => invalid_transition(state, event),
    }
}

fn plan_action(
    action: Action,
    reason: &str,
    transition_id: &str,
) -> Result<PlanTransition, StateMachineError> {
    Ok(PlanTransition {
        action,
        action_payload: ActionPayload::NULL,
        reason: reason.to_string(),
        transition_id: transition_id.to_string(),
    })
}

fn invalid_transition(
    state: StreamingState,
    event: EventKind,
) -> Result<PlanTransition, StateMachineError> {
    Err(StateMachineError::InvalidTransition { state, event })
}

fn lookup_not_eligible(event: EventKind) -> Result<PlanTransition, StateMachineError> {
    match event {
        EventKind::Eligible => plan_action(Action::None, "eligible", "PLAN-01"),
        EventKind::NotEligible => plan_action(Action::Passthrough, "not_eligible", "PLAN-02"),
        _ => invalid_transition(StreamingState::NotEligible, event),
    }
}

fn lookup_streaming_candidate(
    event: EventKind,
    ctx: &PlanContext,
) -> Result<PlanTransition, StateMachineError> {
    match event {
        EventKind::StreamingStart => plan_action(Action::None, "streaming_start", "PLAN-03"),
        EventKind::HardExcluded => plan_action(Action::Passthrough, "hard_excluded", "PLAN-04"),
        EventKind::FullDocFeature => {
            plan_action(Action::SwitchFullBuffer, "full_doc_feature", "PLAN-05")
        }
        EventKind::StrictEtag => plan_action(Action::SwitchFullBuffer, "strict_etag", "PLAN-06"),
        EventKind::AutoRisk => plan_action(Action::SwitchFullBuffer, "auto_risk", "PLAN-07"),
        EventKind::BudgetInitFailure => {
            plan_precommit_error_recovery(ctx, "budget_init_failure", "PLAN-08")
        }
        _ => invalid_transition(StreamingState::StreamingCandidate, event),
    }
}

fn lookup_pre_commit(
    event: EventKind,
    ctx: &PlanContext,
) -> Result<PlanTransition, StateMachineError> {
    match event {
        EventKind::ReplayOverflow => plan_action(Action::None, "replay_overflow", "PLAN-09"),
        EventKind::LookBehindOverflow => {
            plan_action(Action::None, "look_behind_overflow", "PLAN-10")
        }
        EventKind::ParserUnsuitable => plan_precommit_fallback(ctx, "parser_unsuitable", "PLAN-11"),
        EventKind::ResourceLimit => plan_precommit_fallback(ctx, "resource_limit", "PLAN-12"),
        EventKind::Error => plan_precommit_error_recovery(ctx, "error", "PLAN-13"),
        EventKind::Commit => plan_action(Action::CommitHeaders, "commit", "PLAN-14"),
        EventKind::UpstreamEnd => Err(StateMachineError::InvariantViolation {
            message: concat!(
                "UPSTREAM_END before header commit - the model has no ",
                "pre-commit upstream-end path (P3-3); commit headers ",
                "first (PLAN-14), then finalize (PLAN-21)"
            )
            .to_string(),
        }),
        _ => invalid_transition(StreamingState::PreCommit, event),
    }
}

fn lookup_replay_unavailable(
    event: EventKind,
    ctx: &PlanContext,
) -> Result<PlanTransition, StateMachineError> {
    match event {
        EventKind::Error => plan_replay_unavailable_error(ctx, "PLAN-15"),
        EventKind::ResourceLimit => plan_replay_unavailable_error(ctx, "PLAN-16"),
        EventKind::Commit => plan_action(Action::CommitHeaders, "commit", "PLAN-17"),
        EventKind::ParserUnsuitable => plan_replay_unavailable_error(ctx, "PLAN-18"),
        _ => invalid_transition(StreamingState::PreCommitReplayUnavailable, event),
    }
}

fn lookup_committed(
    event: EventKind,
    ctx: &PlanContext,
) -> Result<PlanTransition, StateMachineError> {
    match event {
        EventKind::Commit => Err(StateMachineError::InvariantViolation {
            message: "COMMIT in COMMITTED state".to_string(),
        }),
        EventKind::Error => plan_postcommit_error(ctx, "PLAN-20"),
        EventKind::UpstreamEnd => plan_action(Action::FinalizeConverter, "upstream_end", "PLAN-21"),
        EventKind::ClientAbort => plan_action(Action::None, "client_abort", "PLAN-22"),
        _ => invalid_transition(StreamingState::Committed, event),
    }
}

fn lookup_pending(
    state: StreamingState,
    event: EventKind,
) -> Result<PlanTransition, StateMachineError> {
    let transition_id = match (state, event) {
        (StreamingState::PostCommitSafeFinish, EventKind::ClientAbort) => "PLAN-23",
        (StreamingState::PostCommitAbort, EventKind::ClientAbort) => "PLAN-24",
        (StreamingState::PendingClosingOutput, EventKind::ClientAbort) => "PLAN-25",
        (StreamingState::PendingTerminal, EventKind::ClientAbort) => "PLAN-26",
        (StreamingState::PendingAbortTerminal, EventKind::ClientAbort) => "PLAN-27",
        (StreamingState::PendingClosingOutput, EventKind::ResumeDrain) => "PLAN-28",
        (StreamingState::PendingTerminal, EventKind::ResumeDrain) => "PLAN-29",
        (StreamingState::PendingAbortTerminal, EventKind::ResumeDrain) => "PLAN-30",
        _ => return invalid_transition(state, event),
    };
    let (action, reason) = match event {
        EventKind::ClientAbort => (Action::None, "client_abort"),
        EventKind::ResumeDrain => (Action::ResumePending, "resume_drain"),
        _ => return invalid_transition(state, event),
    };
    plan_action(action, reason, transition_id)
}

fn lookup_terminal(
    state: StreamingState,
    event: EventKind,
) -> Result<PlanTransition, StateMachineError> {
    if event != EventKind::BodyFilterReentry {
        return invalid_transition(state, event);
    }
    let transition_id = match state {
        StreamingState::Done => "PLAN-32",
        StreamingState::Aborted => "PLAN-33",
        StreamingState::FailedClosed => "PLAN-33b",
        _ => return invalid_transition(state, event),
    };
    plan_action(
        Action::None,
        "body_filter_reentry_after_terminal",
        transition_id,
    )
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
    use super::super::types::{ErrorOrigin, FailureLedger, FailureRecord};
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
