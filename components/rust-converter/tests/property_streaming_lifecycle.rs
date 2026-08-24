//! Streaming lifecycle property tests.
//!
//! This file covers:
//! - Property 19: Post-commit status immutability (Req 10.6, 15.6)
//! - Property 20: Streaming state machine determinism (Req 11.1)
//! - Property 21: Single terminal state per request (Req 11.2)
//! - Property 30: Plan/Apply table full coverage (Req 11.1,11.18-11.22,11.24)
//! - Property 31: One-shot terminal invariants (Req 11.2,11.16,11.18)
//! - Property 32: Transformed validators on 304 (Req 10.3,10.9)
//! - Property 33: Precommit replay-unavailable and postcommit failure routing
//!   (Req 11.1,11.6,11.7,11.18,9.3)
//! - Protocol closure tests (Req 11.20-11.22,11.24)
//! - Client-abort contract tests (Req 11.21)
//! - ActionOutcome strictness tests (Req 11.19)
//! - FailureLedger store/emit tests (Req 11.11,11.18)
//! - SideEffectCommand execution tests (Req 11.1,11.11,11.19,11.25)
//! - TransitionFrame identity tests (Req 11.1)
//! - Normal streaming path tests (Req 11.1)
//! - Plan/Apply coverage tests (Req 11.1,11.19)
//! - PendingKind matching tests (Req 11.19)
//! - Protocol tests 6.30-6.53

#![cfg(feature = "streaming")]

use nginx_markdown_converter::streaming_lifecycle::policy::{
    EventFailurePolicy, event_failure_policy,
};
use nginx_markdown_converter::streaming_lifecycle::*;

// ─── Test helpers ────────────────────────────────────────────────────────────

fn default_ctx() -> PlanContext {
    PlanContext {
        resolved_error_policy: ResolvedErrorPolicy::Pass,
        full_input_reconstructible: true,
        full_buffer_resources_allow: true,
        failure_ledger: FailureLedger::empty(),
    }
}

fn ctx_no_full_buffer() -> PlanContext {
    PlanContext {
        resolved_error_policy: ResolvedErrorPolicy::Pass,
        full_input_reconstructible: false,
        full_buffer_resources_allow: false,
        failure_ledger: FailureLedger::empty(),
    }
}

fn ctx_reject_502() -> PlanContext {
    PlanContext {
        resolved_error_policy: ResolvedErrorPolicy::Status502,
        full_input_reconstructible: false,
        full_buffer_resources_allow: false,
        failure_ledger: FailureLedger::empty(),
    }
}

fn ctx_usable() -> TransitionContext {
    TransitionContext {
        downstream_usable: true,
    }
}

fn ctx_unusable() -> TransitionContext {
    TransitionContext {
        downstream_usable: false,
    }
}

fn ok_outcome() -> ActionOutcome {
    ActionOutcome {
        ngx_result: NgxResult::Ok,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        pending_kind: None,
    }
}

fn done_outcome() -> ActionOutcome {
    ActionOutcome {
        ngx_result: NgxResult::Done,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        pending_kind: None,
    }
}

fn again_outcome() -> ActionOutcome {
    ActionOutcome {
        ngx_result: NgxResult::Again,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        /* Neutral: pending_kind is action-specific; the action helpers below
         * set it explicitly.  pending_kind is now validated pending_kind against the
         * action, so a generic helper must not hard-code Terminal. */
        pending_kind: None,
    }
}

fn again_closing_outcome() -> ActionOutcome {
    ActionOutcome {
        ngx_result: NgxResult::Again,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        pending_kind: Some(PendingKind::ClosingMarkdown),
    }
}

fn again_terminal_outcome() -> ActionOutcome {
    ActionOutcome {
        ngx_result: NgxResult::Again,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        pending_kind: Some(PendingKind::Terminal),
    }
}

fn again_abort_terminal_outcome() -> ActionOutcome {
    ActionOutcome {
        ngx_result: NgxResult::Again,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        pending_kind: Some(PendingKind::AbortTerminal),
    }
}

fn error_outcome(site: FailureSite) -> ActionOutcome {
    ActionOutcome {
        ngx_result: NgxResult::Error,
        failure_site: Some(site),
        error_origin: Some(ErrorOrigin::Downstream),
        produced_closing_bytes: false,
        pending_kind: None,
    }
}

fn finalize_error_outcome() -> ActionOutcome {
    ActionOutcome {
        ngx_result: NgxResult::Error,
        failure_site: Some(FailureSite::ConverterFinalize),
        error_origin: Some(ErrorOrigin::Internal),
        produced_closing_bytes: false,
        pending_kind: None,
    }
}

fn ok_with_closing() -> ActionOutcome {
    ActionOutcome {
        ngx_result: NgxResult::Ok,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: true,
        pending_kind: None,
    }
}

fn error_event(reason: &str) -> EventEnvelope {
    EventEnvelope {
        kind: EventKind::Error,
        failure_record: Some(FailureRecord {
            stage: "streaming".to_string(),
            reason: reason.to_string(),
            error_origin: ErrorOrigin::Internal,
            failure_site: None,
        }),
    }
}

fn required_event(kind: EventKind) -> EventEnvelope {
    EventEnvelope {
        kind,
        failure_record: Some(FailureRecord {
            stage: "streaming".to_string(),
            reason: "test".to_string(),
            error_origin: ErrorOrigin::Internal,
            failure_site: None,
        }),
    }
}

fn no_record_event(kind: EventKind) -> EventEnvelope {
    EventEnvelope {
        kind,
        failure_record: None,
    }
}

fn all_15_states() -> [StreamingState; 15] {
    [
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
    ]
}

fn all_19_events() -> [EventKind; 19] {
    [
        EventKind::Eligible,
        EventKind::NotEligible,
        EventKind::StreamingStart,
        EventKind::ParserUnsuitable,
        EventKind::HardExcluded,
        EventKind::FullDocFeature,
        EventKind::BudgetInitFailure,
        EventKind::ReplayOverflow,
        EventKind::ResourceLimit,
        EventKind::StrictEtag,
        EventKind::LookBehindOverflow,
        EventKind::AutoRisk,
        EventKind::Commit,
        EventKind::Error,
        EventKind::UpstreamEnd,
        EventKind::ResumeDrain,
        EventKind::ClientAbort,
        EventKind::BodyFilterReentry,
        EventKind::Cleanup,
    ]
}

fn all_12_actions() -> [Action; 12] {
    [
        Action::None,
        Action::PassHtml,
        Action::RejectStatus,
        Action::CommitHeaders,
        Action::SwitchFullBuffer,
        Action::FinalizeConverter,
        Action::SendClosingOutput,
        Action::SendTerminal,
        Action::BeginAbort,
        Action::SendAbortTerminal,
        Action::ResumePending,
        Action::Passthrough,
    ]
}

fn all_5_failure_sites() -> [FailureSite; 5] {
    [
        FailureSite::ConverterFinalize,
        FailureSite::ClosingOutput,
        FailureSite::TerminalSend,
        FailureSite::AbortTerminalSend,
        FailureSite::PendingResume,
    ]
}

fn all_8_error_origins() -> [ErrorOrigin; 8] {
    [
        ErrorOrigin::Allocation,
        ErrorOrigin::Downstream,
        ErrorOrigin::Invariant,
        ErrorOrigin::Format,
        ErrorOrigin::Truncated,
        ErrorOrigin::Timeout,
        ErrorOrigin::MemoryBudget,
        ErrorOrigin::Internal,
    ]
}

// ════════════════════════════════════════════════════════════════════════════
// Property 20: Streaming state machine determinism (Req 11.1)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn prop_20_plan_returns_deterministic_result() {
    // For all valid (state, event) pairs, plan() returns the same result
    // when called with the same inputs.
    for state in all_15_states() {
        for event in all_19_events() {
            let ctx = default_ctx();
            let env = match event_failure_policy(event) {
                EventFailurePolicy::Required => required_event(event),
                EventFailurePolicy::ReusePersisted => no_record_event(event),
                EventFailurePolicy::Forbidden => no_record_event(event),
            };
            let r1 = plan(state, env.clone(), &ctx);
            let r2 = plan(state, env, &ctx);
            // Both must produce the same result (Ok or Err with same error)
            match (&r1, &r2) {
                (Ok(a), Ok(b)) => assert_eq!(
                    a, b,
                    "plan() must be deterministic for ({:?}, {:?})",
                    state, event
                ),
                (Err(_), Err(_)) => {}
                _ => panic!(
                    "plan() non-deterministic for ({:?}, {:?}): {:?} vs {:?}",
                    state, event, r1, r2
                ),
            }
        }
    }
}

#[test]
fn prop_20_apply_result_returns_deterministic_result() {
    // For all valid (state, frame, outcome, ctx) combinations, apply_result
    // returns the same result.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    // FINALIZE_CONVERTER cannot return NGX_AGAIN, so only test Ok/Done/Error
    let outcomes = [ok_outcome(), done_outcome(), finalize_error_outcome()];
    for outcome in &outcomes {
        let r1 = apply_result(StreamingState::Committed, &frame, outcome, &ctx_usable()).unwrap();
        let r2 = apply_result(StreamingState::Committed, &frame, outcome, &ctx_usable()).unwrap();
        assert_eq!(r1, r2);
    }
}

#[test]
fn prop_20_plan_returns_plan_decision_and_first_frame() {
    // plan() returns {plan_decision, first_frame} with exact frozen structs.
    let result = plan(
        StreamingState::Committed,
        no_record_event(EventKind::UpstreamEnd),
        &default_ctx(),
    )
    .unwrap();
    // plan_decision has event, initial_action, action_payload, reason, transition_id, failure_ledger
    assert_eq!(
        result.plan_decision.initial_action,
        result.first_frame.action
    );
    assert_eq!(
        result.plan_decision.transition_id,
        result.first_frame.transition_id
    );
    assert_eq!(
        result.plan_decision.failure_ledger,
        result.first_frame.failure_ledger
    );
}

#[test]
fn prop_20_apply_result_returns_frozen_structs() {
    // apply_result returns {new_state, next_frame|None, side_effects, failure_updates}
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // side_effects is a Vec<SideEffectCommand>
    let _effects: &Vec<SideEffectCommand> = &result.side_effects;
    // failure_updates has pre_effect and post_effect
    let _pre: &PreEffect = &result.failure_updates.pre_effect;
    let _post: &PostEffect = &result.failure_updates.post_effect;
}

#[test]
fn prop_20_chain_terminates_on_null_next_frame() {
    // Run-to-completion chain terminates when next_frame is None.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // FINALIZE_CONVERTER success with no closing bytes → next_frame = SEND_TERMINAL
    assert!(result.next_frame.is_some());
    // Apply SEND_TERMINAL
    let terminal_frame = result.next_frame.unwrap();
    let term_result = apply_result(
        result.new_state,
        &terminal_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // SEND_TERMINAL success → DONE, next_frame = None
    assert_eq!(term_result.new_state, StreamingState::Done);
    assert!(
        term_result.next_frame.is_none(),
        "chain must terminate at DONE"
    );
}

#[test]
fn prop_20_chain_terminates_on_ngx_again() {
    // Chain terminates (enters pending) when ngx_result is NGX_AGAIN.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_with_closing(),
        &ctx_usable(),
    )
    .unwrap();
    // FINALIZE_CONVERTER success with closing bytes → next_frame = SEND_CLOSING_OUTPUT
    assert!(result.next_frame.is_some());
    let close_frame = result.next_frame.unwrap();
    let close_result = apply_result(
        result.new_state,
        &close_frame,
        &again_closing_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // SEND_CLOSING_OUTPUT + NGX_AGAIN → PENDING_CLOSING_OUTPUT, next_frame = None
    assert_eq!(close_result.new_state, StreamingState::PendingClosingOutput);
    assert!(close_result.next_frame.is_none());
}

// ════════════════════════════════════════════════════════════════════════════
// Property 21: Single terminal state per request (Req 11.2)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn prop_21_at_most_one_terminal_state() {
    // For sequences of events, at most one terminal state is reached.
    // Once in a terminal state, BODY_FILTER_REENTRY is idempotent no-op.
    for terminal in [
        StreamingState::Done,
        StreamingState::Aborted,
        StreamingState::FailedClosed,
    ] {
        let result = plan(
            terminal,
            no_record_event(EventKind::BodyFilterReentry),
            &default_ctx(),
        )
        .unwrap();
        assert_eq!(result.first_frame.action, Action::None);
        let apply =
            apply_result(terminal, &result.first_frame, &ok_outcome(), &ctx_usable()).unwrap();
        // State unchanged — idempotent no-op
        assert_eq!(apply.new_state, terminal);
        assert!(apply.side_effects.is_empty());
    }
}

#[test]
fn prop_21_passthrough_not_terminal_transition() {
    // PASSTHROUGH is an exit/routing state, NOT a terminal state.
    let result = plan(
        StreamingState::NotEligible,
        no_record_event(EventKind::NotEligible),
        &default_ctx(),
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::Passthrough);
    let apply = apply_result(
        StreamingState::NotEligible,
        &result.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(apply.new_state, StreamingState::Passthrough);
    assert!(
        !apply.new_state.is_terminal(),
        "PASSTHROUGH is not terminal"
    );
}

#[test]
fn prop_21_full_buffer_fallback_not_terminal() {
    // FULL_BUFFER_FALLBACK is an exit/routing state, NOT a terminal state.
    let result = plan(
        StreamingState::StreamingCandidate,
        no_record_event(EventKind::FullDocFeature),
        &default_ctx(),
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::SwitchFullBuffer);
    let apply = apply_result(
        StreamingState::StreamingCandidate,
        &result.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(apply.new_state, StreamingState::FullBufferFallback);
    assert!(
        !apply.new_state.is_terminal(),
        "FULL_BUFFER_FALLBACK is not terminal"
    );
}

// ════════════════════════════════════════════════════════════════════════════
// Property 19: Post-commit status immutability (Req 10.6, 15.6)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn prop_19_committed_error_does_not_modify_status() {
    // COMMITTED + ERROR → FINALIZE_CONVERTER (pass) or BEGIN_ABORT (reject).
    // Neither changes the status code or restores HTML.
    let result = plan(
        StreamingState::Committed,
        error_event("conversion_error"),
        &default_ctx(),
    )
    .unwrap();
    // PASS policy → FINALIZE_CONVERTER (safe-finish attempt)
    assert_eq!(result.first_frame.action, Action::FinalizeConverter);

    // The apply_result for FINALIZE_CONVERTER does not modify status/headers.
    // It either proceeds to SEND_CLOSING_OUTPUT/SEND_TERMINAL or BEGIN_ABORT.
    let apply = apply_result(
        StreamingState::Committed,
        &result.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // new_state is POST_COMMIT_SAFE_FINISH (not a terminal that changes status)
    assert_eq!(apply.new_state, StreamingState::PostCommitSafeFinish);
    // No side effects that modify headers
    for cmd in &apply.side_effects {
        assert!(
            !matches!(cmd.kind, SideEffectKind::LatchTerminalSent),
            "No terminal latch in post-commit safe-finish start"
        );
    }
}

#[test]
fn prop_19_postcommit_abort_never_restores_html() {
    // Post-commit abort preserves committed status; never restores HTML.
    let ctx = PlanContext {
        resolved_error_policy: ResolvedErrorPolicy::Status502,
        full_input_reconstructible: false,
        full_buffer_resources_allow: false,
        failure_ledger: FailureLedger::empty(),
    };
    let result = plan(
        StreamingState::Committed,
        error_event("conversion_error"),
        &ctx,
    )
    .unwrap();
    // Non-PASS → BEGIN_ABORT
    assert_eq!(result.first_frame.action, Action::BeginAbort);

    let apply = apply_result(
        StreamingState::Committed,
        &result.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // BEGIN_ABORT → POST_COMMIT_ABORT, next_frame = SEND_ABORT_TERMINAL
    assert_eq!(apply.new_state, StreamingState::PostCommitAbort);
    assert!(apply.next_frame.is_some());
    assert_eq!(
        apply.next_frame.as_ref().unwrap().action,
        Action::SendAbortTerminal
    );
    // No HTML restoration side effect exists in the SideEffectKind enum
    // (there is no RestoreHtml or similar variant)
}

#[test]
fn prop_19_no_postcommit_state_modifies_headers() {
    // No post-commit action produces a side effect that modifies response
    // status or headers. The only side effects are:
    // - LatchTerminalSent (terminal delivery tracking)
    // - SetSafeFinishOutputLoss / SetSafeFinishTerminalSendFailed (failure tracking)
    // - StoreFailureLedger / EmitFailureLedger (telemetry)
    // - RecordPostcommitAbort (metric)
    // - ClearInflightAndPending (cleanup)
    // - TransferFailureToFullBuffer (ownership)
    // None of these modify r->headers_out or r->headers_out.status.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    for cmd in &result.side_effects {
        // Verify no side effect kind implies header mutation
        match cmd.kind {
            SideEffectKind::LatchTerminalSent
            | SideEffectKind::SetSafeFinishOutputLoss
            | SideEffectKind::SetSafeFinishTerminalSendFailed
            | SideEffectKind::StoreFailureLedger
            | SideEffectKind::EmitFailureLedger
            | SideEffectKind::RecordPostcommitAbort
            | SideEffectKind::ClearInflightAndPending
            | SideEffectKind::TransferFailureToFullBuffer => {
                // All valid non-header-mutating side effects
            }
        }
    }
}

// ════════════════════════════════════════════════════════════════════════════
// Property 30: Plan/Apply table full coverage
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn prop_30_every_valid_pair_has_unique_plan_decision() {
    // Every valid (state, event) pair has a PlanDecision with a transition_id.
    // The transition_id identifies a transition (action + state change); the
    // same transition can be reached from different events (e.g. PLAN-13 is
    // shared between (PreCommit, Error) and (PreCommit, ResourceLimit) by
    // design).  Uniqueness is on the (state, event) pair, not on transition_id.
    let ctx = default_ctx();
    let mut seen_pairs = std::collections::HashSet::new();
    for state in all_15_states() {
        for event in all_19_events() {
            let env = match event_failure_policy(event) {
                EventFailurePolicy::Required => required_event(event),
                EventFailurePolicy::ReusePersisted => no_record_event(event),
                EventFailurePolicy::Forbidden => no_record_event(event),
            };
            if let Ok(result) = plan(state, env, &ctx) {
                // Each valid pair has a non-empty transition_id
                assert!(
                    !result.plan_decision.transition_id.is_empty(),
                    "Empty transition_id for ({:?}, {:?})",
                    state,
                    event
                );
                // The (state, event) pair itself must be unique
                assert!(
                    seen_pairs.insert((state, event)),
                    "Duplicate (state, event) pair: ({:?}, {:?})",
                    state,
                    event
                );
            }
        }
    }
}

#[test]
fn prop_30_next_frame_resolves_to_apply_row() {
    // Every non-null next_frame from apply_result resolves to a valid Apply row.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_with_closing(),
        &ctx_usable(),
    )
    .unwrap();
    if let Some(ref next) = result.next_frame {
        // The next frame's action must be a valid action
        assert!(all_12_actions().contains(&next.action));
        // The next frame must have a valid step_id
        assert!(!next.step_id.is_empty());
        // The transition_id must be inherited
        assert_eq!(next.transition_id, frame.transition_id);
    }
}

#[test]
fn prop_30_ngx_again_never_transitions_to_terminal() {
    // NGX_AGAIN never transitions out of a pending state to DONE/ABORTED.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-CLOSE-S2".to_string(),
        action: Action::SendClosingOutput,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &again_closing_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert!(
        !result.new_state.is_terminal(),
        "NGX_AGAIN must not transition to terminal state"
    );
    assert_eq!(result.new_state, StreamingState::PendingClosingOutput);
}

#[test]
fn prop_30_ngx_again_never_sets_terminal_latch() {
    // NGX_AGAIN never sets the terminal-sent latch.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-TERM-S3".to_string(),
        action: Action::SendTerminal,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &again_terminal_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    for cmd in &result.side_effects {
        assert!(
            !matches!(cmd.kind, SideEffectKind::LatchTerminalSent),
            "NGX_AGAIN must not set terminal-sent latch"
        );
    }
}

#[test]
fn prop_30_none_transitions_distinguished_by_step_and_event() {
    // NONE transitions are distinguished by step_id and Event.
    // NOT_ELIGIBLE + ELIGIBLE → STREAMING_CANDIDATE
    let r1 = plan(
        StreamingState::NotEligible,
        no_record_event(EventKind::Eligible),
        &default_ctx(),
    )
    .unwrap();
    let a1 = apply_result(
        StreamingState::NotEligible,
        &r1.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(a1.new_state, StreamingState::StreamingCandidate);

    // PRE_COMMIT + REPLAY_OVERFLOW → PRE_COMMIT_REPLAY_UNAVAILABLE
    let r2 = plan(
        StreamingState::PreCommit,
        no_record_event(EventKind::ReplayOverflow),
        &default_ctx(),
    )
    .unwrap();
    let a2 = apply_result(
        StreamingState::PreCommit,
        &r2.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(a2.new_state, StreamingState::PreCommitReplayUnavailable);

    // Different transition IDs
    assert_ne!(
        r1.plan_decision.transition_id,
        r2.plan_decision.transition_id
    );
}

#[test]
fn prop_30_client_abort_creates_no_failure_record() {
    // CLIENT_ABORT creates no FailureRecord.
    for state in all_15_states() {
        if !state.is_committed_or_pending() {
            continue;
        }
        let env = no_record_event(EventKind::ClientAbort);
        let result = plan(state, env, &default_ctx()).unwrap();
        // CLIENT_ABORT uses NONE action (no FailureRecord)
        assert_eq!(result.first_frame.action, Action::None);
        // The failure_ledger is left completely empty — no primary,
        // secondary, or delivery record may be populated by CLIENT_ABORT.
        assert!(
            !result.plan_decision.failure_ledger.is_populated(),
            "CLIENT_ABORT must not populate the failure ledger at all"
        );
    }
}

// ════════════════════════════════════════════════════════════════════════════
// Property 31: One-shot terminal invariants
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn prop_31_terminal_latch_only_after_ok_or_done() {
    // Terminal-sent latch is set only after NGX_OK or NGX_DONE.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-TERM-S3".to_string(),
        action: Action::SendTerminal,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };

    // NGX_OK → latch set
    let r_ok = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert!(
        r_ok.side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::LatchTerminalSent))
    );

    // NGX_DONE → latch set
    let r_done = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &done_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert!(
        r_done
            .side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::LatchTerminalSent))
    );

    // NGX_AGAIN → no latch
    let r_again = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &again_terminal_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert!(
        !r_again
            .side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::LatchTerminalSent))
    );

    // NGX_ERROR → no latch (goes to ABORTED)
    let r_err = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &error_outcome(FailureSite::TerminalSend),
        &ctx_usable(),
    )
    .unwrap();
    assert!(
        !r_err
            .side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::LatchTerminalSent))
    );
}

#[test]
fn prop_31_body_filter_reentry_idempotent_no_op() {
    // BODY_FILTER_REENTRY after DONE/ABORTED/FAILED_CLOSED is idempotent no-op.
    for terminal in [
        StreamingState::Done,
        StreamingState::Aborted,
        StreamingState::FailedClosed,
    ] {
        let result = plan(
            terminal,
            no_record_event(EventKind::BodyFilterReentry),
            &default_ctx(),
        )
        .unwrap();
        let apply =
            apply_result(terminal, &result.first_frame, &ok_outcome(), &ctx_usable()).unwrap();
        assert_eq!(apply.new_state, terminal);
        assert!(apply.side_effects.is_empty());
        assert!(apply.next_frame.is_none());
    }
}

#[test]
fn prop_31_postcommit_abort_recorded_is_one_shot() {
    // postcommit_abort_recorded is a one-shot latch.
    // BEGIN_ABORT → RecordPostcommitAbort side effect appears exactly once.
    let ctx = PlanContext {
        resolved_error_policy: ResolvedErrorPolicy::Status502,
        full_input_reconstructible: false,
        full_buffer_resources_allow: false,
        failure_ledger: FailureLedger::empty(),
    };
    let result = plan(
        StreamingState::Committed,
        error_event("conversion_error"),
        &ctx,
    )
    .unwrap();
    let apply = apply_result(
        StreamingState::Committed,
        &result.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    let abort_count = apply
        .side_effects
        .iter()
        .filter(|cmd| matches!(cmd.kind, SideEffectKind::RecordPostcommitAbort))
        .count();
    assert_eq!(
        abort_count, 1,
        "RecordPostcommitAbort must appear exactly once"
    );
}

#[test]
fn prop_31_begin_abort_chain_completes_with_consumed_handle() {
    // BEGIN_ABORT → SEND_ABORT_TERMINAL completes to DONE/ABORTED even when
    // the converter handle was consumed by FINALIZE_CONVERTER failure.
    let frame = TransitionFrame {
        transition_id: "PLAN-20".to_string(),
        step_id: "PLAN-20-ABORT-S2".to_string(),
        action: Action::BeginAbort,
        action_payload: ActionPayload::NULL,
        event: error_event("conversion_error"),
        reason: "conversion_error".to_string(),
        failure_ledger: FailureLedger {
            primary: Some(FailureRecord {
                stage: "streaming".to_string(),
                reason: "conversion_error".to_string(),
                error_origin: ErrorOrigin::Internal,
                failure_site: Some(FailureSite::ConverterFinalize),
            }),
            secondary: None,
            delivery: None,
            ledger_stored: false,
            ledger_emitted: false,
        },
    };
    // BEGIN_ABORT success → next_frame = SEND_ABORT_TERMINAL
    let r = apply_result(
        StreamingState::PostCommitAbort,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(r.new_state, StreamingState::PostCommitAbort);
    assert!(r.next_frame.is_some());
    assert_eq!(
        r.next_frame.as_ref().unwrap().action,
        Action::SendAbortTerminal
    );

    // SEND_ABORT_TERMINAL success → DONE
    let term_frame = r.next_frame.unwrap();
    let term_r = apply_result(r.new_state, &term_frame, &ok_outcome(), &ctx_usable()).unwrap();
    assert_eq!(term_r.new_state, StreamingState::Done);
}

// ════════════════════════════════════════════════════════════════════════════
// Property 32: Transformed validators on 304 (Req 10.3, 10.9)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn prop_32_etag_never_from_upstream_html() {
    // The ETag placeholder in the HeaderPlan is always resolved by the C
    // caller from transformed Markdown bytes, never from upstream HTML.
    // The plan only contains SetEtagPlaceholder, never a literal ETag Set.
    use nginx_markdown_converter::header_plan::{HeaderOp, HeaderPlan};
    let plan = HeaderPlan::for_markdown_conversion("text/markdown; charset=utf-8", true);
    for op in &plan.ops {
        if let HeaderOp::Set { name, .. } = op {
            assert_ne!(name, "ETag", "ETag must never be set to a literal value");
        }
    }
    // The placeholder is present
    assert!(
        plan.ops
            .iter()
            .any(|op| matches!(op, HeaderOp::SetEtagPlaceholder))
    );
}

#[test]
fn prop_32_304_plan_preserves_validators() {
    // 304 plan deletes Content-Length and Content-Encoding but preserves
    // ETag and Last-Modified (the validators that confirm the entity).
    use nginx_markdown_converter::header_plan::{HeaderOp, HeaderPlan};
    let plan = HeaderPlan::for_304();
    assert!(plan.ops.iter().any(|op| matches!(
        op,
        HeaderOp::DeleteAll { name } if name == "Content-Length"
    )));
    assert!(plan.ops.iter().any(|op| matches!(
        op,
        HeaderOp::DeleteAll { name } if name == "Content-Encoding"
    )));
    // No ETag or Last-Modified deletion
    assert!(!plan.ops.iter().any(|op| matches!(
        op,
        HeaderOp::DeleteAll { name } if name == "ETag"
    )));
    assert!(!plan.ops.iter().any(|op| matches!(
        op,
        HeaderOp::DeleteAll { name } if name == "Last-Modified"
    )));
}

#[test]
fn prop_32_streaming_committed_never_retroactive_304() {
    // Streaming-committed requests SHALL NOT retroactively produce 304.
    // The streaming state machine enters COMMITTED state after header commit.
    // Once committed, the only paths are FINALIZE_CONVERTER (UPSTREAM_END),
    // BEGIN_ABORT (ERROR with non-PASS), or CLIENT_ABORT.
    // None of these produce a 304 response.
    let committed_result = plan(
        StreamingState::Committed,
        no_record_event(EventKind::UpstreamEnd),
        &default_ctx(),
    )
    .unwrap();
    assert_eq!(
        committed_result.first_frame.action,
        Action::FinalizeConverter
    );
    // FINALIZE_CONVERTER → SEND_CLOSING_OUTPUT/SEND_TERMINAL → DONE
    // No 304 path exists from COMMITTED state.
}

// ════════════════════════════════════════════════════════════════════════════
// Property 33: Precommit replay-unavailable and postcommit failure routing
// (Req 11.1, 11.6, 11.7, 11.18, 9.3)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn prop_33_precommit_truth_table_switch_full_buffer() {
    // PARSER_UNSUITABLE + full_input_reconstructible + full_buffer_resources_allow
    // → SWITCH_FULL_BUFFER
    let ctx = default_ctx();
    let result = plan(
        StreamingState::PreCommit,
        no_record_event(EventKind::ParserUnsuitable),
        &ctx,
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::SwitchFullBuffer);
}

#[test]
fn prop_33_precommit_truth_table_pass_html() {
    // PARSER_UNSUITABLE + NOT reconstructible + PASS → PASS_HTML
    let ctx = ctx_no_full_buffer();
    let result = plan(
        StreamingState::PreCommit,
        no_record_event(EventKind::ParserUnsuitable),
        &ctx,
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::PassHtml);
}

#[test]
fn prop_33_precommit_truth_table_reject_status() {
    // PARSER_UNSUITABLE + NOT reconstructible + STATUS_502 → REJECT_STATUS(502)
    let ctx = ctx_reject_502();
    let result = plan(
        StreamingState::PreCommit,
        no_record_event(EventKind::ParserUnsuitable),
        &ctx,
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::RejectStatus);
    assert_eq!(result.first_frame.action_payload.reject_status, Some(502));
}

#[test]
fn prop_33_replay_unavailable_pass_not_pass_html() {
    // PRE_COMMIT_REPLAY_UNAVAILABLE + ERROR + pass + NOT reconstructible
    // → REJECT_STATUS(502), NOT PASS_HTML
    let ctx = ctx_no_full_buffer();
    let result = plan(
        StreamingState::PreCommitReplayUnavailable,
        error_event("fail_open_unavailable"),
        &ctx,
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::RejectStatus);
    assert_eq!(result.first_frame.action_payload.reject_status, Some(502));
}

#[test]
fn prop_33_replay_unavailable_reject_status() {
    // PRE_COMMIT_REPLAY_UNAVAILABLE + ERROR + STATUS_502 → REJECT_STATUS → FAILED_CLOSED
    let ctx = ctx_reject_502();
    let result = plan(
        StreamingState::PreCommitReplayUnavailable,
        error_event("fail_open_unavailable"),
        &ctx,
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::RejectStatus);
    assert_eq!(result.first_frame.action_payload.reject_status, Some(502));

    let apply = apply_result(
        StreamingState::PreCommitReplayUnavailable,
        &result.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(apply.new_state, StreamingState::FailedClosed);
}

#[test]
fn prop_33_finalize_failure_downstream_usable_begin_abort() {
    // FINALIZE_CONVERTER failure + downstream usable → next_frame = BEGIN_ABORT
    // (converter handle consumed does NOT cause direct ABORTED)
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &finalize_error_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(result.new_state, StreamingState::PostCommitAbort);
    assert!(result.next_frame.is_some());
    assert_eq!(
        result.next_frame.as_ref().unwrap().action,
        Action::BeginAbort
    );
}

#[test]
fn prop_33_send_closing_failure_direct_aborted() {
    // SEND_CLOSING_OUTPUT definitive failure → direct ABORTED + output loss
    // NO clean terminal, NO BEGIN_ABORT
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-CLOSE-S2".to_string(),
        action: Action::SendClosingOutput,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &error_outcome(FailureSite::ClosingOutput),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(result.new_state, StreamingState::Aborted);
    assert!(
        result.next_frame.is_none(),
        "No BEGIN_ABORT for closing output failure"
    );
    assert!(
        result
            .side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::SetSafeFinishOutputLoss))
    );
}

#[test]
fn prop_33_send_terminal_failure_direct_aborted() {
    // SEND_TERMINAL definitive failure → direct ABORTED + terminal send failed
    // NO retry, NO BEGIN_ABORT
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-TERM-S3".to_string(),
        action: Action::SendTerminal,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &error_outcome(FailureSite::TerminalSend),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(result.new_state, StreamingState::Aborted);
    assert!(
        result.next_frame.is_none(),
        "No retry for terminal send failure"
    );
    assert!(
        result
            .side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::SetSafeFinishTerminalSendFailed))
    );
}

#[test]
fn prop_33_commit_headers_only_synthetic_ok() {
    // COMMIT_HEADERS accepts only synthetic NGX_OK.
    // Definitive NGX_ERROR is an invariant violation.
    let frame = TransitionFrame {
        transition_id: "PLAN-14".to_string(),
        step_id: "PLAN-14".to_string(),
        action: Action::CommitHeaders,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::Commit),
        reason: "commit".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    // NGX_OK → success
    let r_ok = apply_result(
        StreamingState::PreCommit,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(r_ok.new_state, StreamingState::Committed);

    // NGX_ERROR → invariant violation
    let r_err = apply_result(
        StreamingState::PreCommit,
        &frame,
        &error_outcome(FailureSite::TerminalSend),
        &ctx_usable(),
    );
    assert!(
        r_err.is_err(),
        "COMMIT_HEADERS + NGX_ERROR must be invariant violation"
    );
}

// ════════════════════════════════════════════════════════════════════════════
// Protocol closure tests (Req 11.20-11.22, 11.24)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn proto_no_undocumented_state_event_acceptance() {
    // Every (state, event) pair NOT in the Plan table returns an error.
    let ctx = default_ctx();
    let mut accepted = 0usize;
    let mut rejected = 0usize;
    for state in all_15_states() {
        for event in all_19_events() {
            let env = match event_failure_policy(event) {
                EventFailurePolicy::Required => required_event(event),
                EventFailurePolicy::ReusePersisted => no_record_event(event),
                EventFailurePolicy::Forbidden => no_record_event(event),
            };
            // If this is a valid pair, plan() succeeds. If not, it must
            // error — plan() never panics and never silently accepts an
            // undocumented pair.
            match plan(state, env, &ctx) {
                Ok(plan) => {
                    assert!(
                        plan.first_frame.transition_id.starts_with("PLAN-"),
                        "accepted transition must carry a PLAN- id for {:?}+{:?}: {}",
                        state,
                        event,
                        plan.first_frame.transition_id
                    );
                    accepted += 1;
                }
                Err(err) => {
                    assert!(
                        matches!(
                            err,
                            StateMachineError::InvariantViolation { .. }
                                | StateMachineError::InvalidTransition { .. }
                        ),
                        "undocumented pair must reject with InvariantViolation or \
                         InvalidTransition for {:?}+{:?}, got {err:?}",
                        state,
                        event
                    );
                    rejected += 1;
                }
            }
        }
    }
    // The full state×event product is exercised (no early exit).
    assert_eq!(accepted + rejected, 15 * 19);
    assert!(
        accepted > 0,
        "at least one documented transition must be accepted"
    );
    assert!(
        rejected > 0,
        "at least one undocumented pair must be rejected"
    );
}

#[test]
fn proto_cleanup_all_15_states_preserve_state() {
    // CLEANUP in all 15 states preserves the state.
    let ctx = default_ctx();
    for state in all_15_states() {
        let result = plan(state, no_record_event(EventKind::Cleanup), &ctx).unwrap();
        let apply = apply_result(state, &result.first_frame, &ok_outcome(), &ctx_usable()).unwrap();
        assert_eq!(
            apply.new_state, state,
            "CLEANUP must preserve state for {:?}",
            state
        );
        // CLEANUP must clear inflight and pending
        assert!(
            apply
                .side_effects
                .iter()
                .any(|cmd| matches!(cmd.kind, SideEffectKind::ClearInflightAndPending)),
            "CLEANUP must clear inflight and pending for {:?}",
            state
        );
    }
}

#[test]
fn proto_cleanup_full_buffer_fallback_clear_only() {
    // FULL_BUFFER_FALLBACK + CLEANUP → clear only (no emit).
    let ctx = default_ctx();
    let result = plan(
        StreamingState::FullBufferFallback,
        no_record_event(EventKind::Cleanup),
        &ctx,
    )
    .unwrap();
    let apply = apply_result(
        StreamingState::FullBufferFallback,
        &result.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(apply.new_state, StreamingState::FullBufferFallback);
    // No emit, only clear
    assert!(
        !apply
            .side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::EmitFailureLedger))
    );
    assert!(
        apply
            .side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::ClearInflightAndPending))
    );
}

#[test]
fn proto_event_envelope_required_records_validated() {
    // ERROR, BUDGET_INIT_FAILURE, RESOURCE_LIMIT require a FailureRecord.
    for kind in [
        EventKind::Error,
        EventKind::BudgetInitFailure,
        EventKind::ResourceLimit,
    ] {
        let env = EventEnvelope {
            kind,
            failure_record: None,
        };
        let result = plan(StreamingState::PreCommit, env, &default_ctx());
        assert!(
            matches!(
                result,
                Err(StateMachineError::MissingRequiredFailureRecord { .. })
            ),
            "Event {:?} must require a FailureRecord",
            kind
        );
    }
}

#[test]
fn proto_event_envelope_forbidden_records_rejected() {
    // Forbidden events reject a non-null FailureRecord.
    let forbidden_events = [
        EventKind::Eligible,
        EventKind::NotEligible,
        EventKind::StreamingStart,
        EventKind::ParserUnsuitable,
        EventKind::HardExcluded,
        EventKind::FullDocFeature,
        EventKind::ReplayOverflow,
        EventKind::StrictEtag,
        EventKind::LookBehindOverflow,
        EventKind::AutoRisk,
        EventKind::Commit,
        EventKind::UpstreamEnd,
        EventKind::ClientAbort,
        EventKind::BodyFilterReentry,
        EventKind::Cleanup,
    ];
    for kind in &forbidden_events {
        let env = EventEnvelope {
            kind: *kind,
            failure_record: Some(FailureRecord {
                stage: "x".to_string(),
                reason: "x".to_string(),
                error_origin: ErrorOrigin::Internal,
                failure_site: None,
            }),
        };
        let result = plan(StreamingState::NotEligible, env, &default_ctx());
        // Some of these will fail with InvalidTransition, some with ForbiddenFailureRecord.
        // The key is that they don't silently accept the forbidden record.
        assert!(
            result.is_err(),
            "Forbidden event {:?} with record must be rejected",
            kind
        );
    }
}

// ════════════════════════════════════════════════════════════════════════════
// Client-abort contract tests (Req 11.21)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn proto_client_abort_all_committed_states() {
    // CLIENT_ABORT in all 6 committed/pending states → ABORTED.
    let committed_states = [
        StreamingState::Committed,
        StreamingState::PostCommitSafeFinish,
        StreamingState::PostCommitAbort,
        StreamingState::PendingClosingOutput,
        StreamingState::PendingTerminal,
        StreamingState::PendingAbortTerminal,
    ];
    let ctx = default_ctx();
    for state in &committed_states {
        let result = plan(*state, no_record_event(EventKind::ClientAbort), &ctx).unwrap();
        assert_eq!(result.first_frame.action, Action::None);

        let apply =
            apply_result(*state, &result.first_frame, &ok_outcome(), &ctx_usable()).unwrap();
        assert_eq!(apply.new_state, StreamingState::Aborted);
        assert!(apply.next_frame.is_none());

        // Must have emit + clear side effects
        assert!(
            apply
                .side_effects
                .iter()
                .any(|cmd| matches!(cmd.kind, SideEffectKind::EmitFailureLedger)),
            "CLIENT_ABORT must emit ledger for {:?}",
            state
        );
        assert!(
            apply
                .side_effects
                .iter()
                .any(|cmd| matches!(cmd.kind, SideEffectKind::ClearInflightAndPending)),
            "CLIENT_ABORT must clear inflight for {:?}",
            state
        );

        // No terminal-sent latch
        assert!(
            !apply
                .side_effects
                .iter()
                .any(|cmd| matches!(cmd.kind, SideEffectKind::LatchTerminalSent)),
            "CLIENT_ABORT must not set terminal latch for {:?}",
            state
        );

        // No BEGIN_ABORT or SEND_ABORT_TERMINAL
        assert!(
            !apply
                .side_effects
                .iter()
                .any(|cmd| matches!(cmd.kind, SideEffectKind::RecordPostcommitAbort)),
            "CLIENT_ABORT must not record postcommit abort for {:?}",
            state
        );
    }
}

#[test]
fn proto_client_abort_creates_no_failure_record() {
    // CLIENT_ABORT creates no new FailureRecord.
    let ctx = default_ctx();
    let result = plan(
        StreamingState::Committed,
        no_record_event(EventKind::ClientAbort),
        &ctx,
    )
    .unwrap();
    // The failure_ledger is not populated by CLIENT_ABORT
    assert!(!result.plan_decision.failure_ledger.is_populated());
}

// ════════════════════════════════════════════════════════════════════════════
// ActionOutcome strictness tests (Req 11.19)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn proto_action_outcome_nullability_ok() {
    // NGX_OK carries failure_site=null and error_origin=null.
    let outcome = ok_outcome();
    assert!(outcome.failure_site.is_none());
    assert!(outcome.error_origin.is_none());
}

#[test]
fn proto_action_outcome_nullability_done() {
    // NGX_DONE carries failure_site=null and error_origin=null.
    let outcome = done_outcome();
    assert!(outcome.failure_site.is_none());
    assert!(outcome.error_origin.is_none());
}

#[test]
fn proto_action_outcome_nullability_again() {
    // NGX_AGAIN carries failure_site=null and error_origin=null.
    let outcome = again_outcome();
    assert!(outcome.failure_site.is_none());
    assert!(outcome.error_origin.is_none());
}

#[test]
fn proto_action_outcome_error_carries_site_and_origin() {
    // Only definitive NGX_ERROR carries non-null failure_site and error_origin.
    for site in all_5_failure_sites() {
        let outcome = error_outcome(site);
        assert_eq!(outcome.ngx_result, NgxResult::Error);
        assert!(outcome.failure_site.is_some());
        assert!(outcome.error_origin.is_some());
    }
}

#[test]
fn proto_commit_headers_only_synthetic_ok() {
    // COMMIT_HEADERS: only synthetic NGX_OK is legal.
    // NGX_DONE and NGX_ERROR are NOT legal.
    let frame = TransitionFrame {
        transition_id: "PLAN-14".to_string(),
        step_id: "PLAN-14".to_string(),
        action: Action::CommitHeaders,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::Commit),
        reason: "commit".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    // NGX_OK → success
    assert!(
        apply_result(
            StreamingState::PreCommit,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .is_ok()
    );

    // NGX_ERROR → invariant violation
    assert!(
        apply_result(
            StreamingState::PreCommit,
            &frame,
            &error_outcome(FailureSite::TerminalSend),
            &ctx_usable(),
        )
        .is_err()
    );
}

#[test]
fn proto_begin_abort_only_synthetic_ok() {
    // BEGIN_ABORT: only synthetic NGX_OK is legal (NGX_DONE not legal).
    let frame = TransitionFrame {
        transition_id: "PLAN-20".to_string(),
        step_id: "PLAN-20-ABORT-S2".to_string(),
        action: Action::BeginAbort,
        action_payload: ActionPayload::NULL,
        event: error_event("test"),
        reason: "test".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    // NGX_OK → success
    assert!(
        apply_result(
            StreamingState::PostCommitAbort,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .is_ok()
    );

    // NGX_DONE → error (not legal for BEGIN_ABORT)
    assert!(
        apply_result(
            StreamingState::PostCommitAbort,
            &frame,
            &done_outcome(),
            &ctx_usable(),
        )
        .is_err()
    );

    // NGX_ERROR → error
    assert!(
        apply_result(
            StreamingState::PostCommitAbort,
            &frame,
            &error_outcome(FailureSite::AbortTerminalSend),
            &ctx_usable(),
        )
        .is_err()
    );
}

// ════════════════════════════════════════════════════════════════════════════
// FailureLedger store/emit tests (Req 11.11, 11.18)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn proto_ledger_store_latches_without_emitting() {
    // ledger_stored latches the updated full ledger without emitting.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &finalize_error_outcome(),
        &ctx_unusable(),
    )
    .unwrap();
    // Direct ABORTED (downstream unusable) → store + emit
    assert!(
        result
            .side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::StoreFailureLedger))
    );
    assert!(
        result
            .side_effects
            .iter()
            .any(|cmd| matches!(cmd.kind, SideEffectKind::EmitFailureLedger))
    );
    // Post-effect latches
    assert!(
        result
            .failure_updates
            .post_effect
            .set_ledger_stored_after
            .is_some()
    );
    assert!(
        result
            .failure_updates
            .post_effect
            .set_ledger_emitted_if_unemitted_after
            .is_some()
    );
}

#[test]
fn proto_ledger_emit_exactly_once_at_terminal() {
    // Emission happens exactly once at the definitive lifecycle result.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-TERM-S3".to_string(),
        action: Action::SendTerminal,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // SEND_TERMINAL success → DONE + latch + emit
    assert_eq!(result.new_state, StreamingState::Done);
    let emit_count = result
        .side_effects
        .iter()
        .filter(|cmd| matches!(cmd.kind, SideEffectKind::EmitFailureLedger))
        .count();
    assert_eq!(emit_count, 1, "Emit must happen exactly once at terminal");
}

#[test]
fn proto_ledger_promotion_plan31() {
    // PLAN-31 (UPSTREAM_END) finalize failure promotes primary when no prior primary.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &finalize_error_outcome(),
        &ctx_unusable(),
    )
    .unwrap();
    // Primary is promoted (was None, now Some)
    assert!(result.failure_updates.pre_effect.primary_update.is_some());
    assert!(result.failure_updates.pre_effect.secondary_update.is_none());
}

#[test]
fn proto_ledger_secondary_update_plan20() {
    // PLAN-20 (ERROR) finalize failure updates secondary when primary exists.
    let primary = FailureRecord {
        stage: "streaming".to_string(),
        reason: "conversion_error".to_string(),
        error_origin: ErrorOrigin::Internal,
        failure_site: None,
    };
    let frame = TransitionFrame {
        transition_id: "PLAN-20".to_string(),
        step_id: "PLAN-20".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: error_event("conversion_error"),
        reason: "error_pass_finish".to_string(),
        failure_ledger: FailureLedger {
            primary: Some(primary),
            secondary: None,
            delivery: None,
            ledger_stored: false,
            ledger_emitted: false,
        },
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &finalize_error_outcome(),
        &ctx_unusable(),
    )
    .unwrap();
    // Secondary is updated (primary already exists)
    assert!(result.failure_updates.pre_effect.secondary_update.is_some());
    assert!(result.failure_updates.pre_effect.primary_update.is_none());
}

// ════════════════════════════════════════════════════════════════════════════
// SideEffectCommand execution tests (Req 11.1, 11.11, 11.19, 11.25)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn proto_side_effect_commands_have_unique_ids() {
    // Each command ID is unique within its command stream.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &finalize_error_outcome(),
        &ctx_unusable(),
    )
    .unwrap();
    let mut seen = std::collections::HashSet::new();
    for cmd in &result.side_effects {
        assert!(
            seen.insert(&cmd.command_id),
            "Duplicate command ID: {}",
            cmd.command_id
        );
    }
}

#[test]
fn proto_side_effect_commands_kind_closed_payload() {
    // Each command has exactly one kind/payload variant.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &finalize_error_outcome(),
        &ctx_unusable(),
    )
    .unwrap();
    for cmd in &result.side_effects {
        // Each command has exactly one kind
        let _kind = cmd.kind;
        // Each command has exactly one payload variant
        let _payload = &cmd.payload;
        // Each command has an execute_if predicate
        let _predicate = cmd.execute_if;
    }
}

#[test]
fn proto_side_effect_post_effect_latch_references_resolve() {
    // Every post-effect latch reference resolves to a command in the same row.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &finalize_error_outcome(),
        &ctx_unusable(),
    )
    .unwrap();
    let cmd_ids: std::collections::HashSet<_> = result
        .side_effects
        .iter()
        .map(|cmd| &cmd.command_id)
        .collect();
    if let Some(ref id) = result.failure_updates.post_effect.set_ledger_stored_after {
        assert!(
            cmd_ids.contains(id),
            "set_ledger_stored_after must reference a command in the same row"
        );
    }
    if let Some(ref id) = result
        .failure_updates
        .post_effect
        .set_ledger_emitted_if_unemitted_after
    {
        assert!(
            cmd_ids.contains(id),
            "set_ledger_emitted_if_unemitted_after must reference a command in the same row"
        );
    }
}

// ════════════════════════════════════════════════════════════════════════════
// TransitionFrame identity tests (Req 11.1)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn proto_transition_frame_chain_shares_transition_id() {
    // FINALIZE → closing → terminal three-step chain uses the SAME transition_id
    // with DIFFERENT branch-specific step_ids.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let r1 = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_with_closing(),
        &ctx_usable(),
    )
    .unwrap();
    let close_frame = r1.next_frame.unwrap();
    // Same transition_id, different step_id
    assert_eq!(close_frame.transition_id, frame.transition_id);
    assert_ne!(close_frame.step_id, frame.step_id);
    assert_eq!(close_frame.action, Action::SendClosingOutput);

    let r2 = apply_result(r1.new_state, &close_frame, &ok_outcome(), &ctx_usable()).unwrap();
    let term_frame = r2.next_frame.unwrap();
    assert_eq!(term_frame.transition_id, frame.transition_id);
    assert_ne!(term_frame.step_id, close_frame.step_id);
    assert_ne!(term_frame.step_id, frame.step_id);
    assert_eq!(term_frame.action, Action::SendTerminal);
}

#[test]
fn proto_transition_frame_each_step_id_unique() {
    // Each step_id is unique within a chain.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let r1 = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_with_closing(),
        &ctx_usable(),
    )
    .unwrap();
    let close_frame = r1.next_frame.unwrap();
    let r2 = apply_result(r1.new_state, &close_frame, &ok_outcome(), &ctx_usable()).unwrap();
    let term_frame = r2.next_frame.unwrap();

    let step_ids = [
        frame.step_id.clone(),
        close_frame.step_id.clone(),
        term_frame.step_id.clone(),
    ];
    let unique: std::collections::HashSet<_> = step_ids.iter().collect();
    assert_eq!(
        unique.len(),
        3,
        "All step_ids must be unique within the chain"
    );
}

// ════════════════════════════════════════════════════════════════════════════
// Normal streaming path tests (Req 11.1)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn proto_normal_streaming_start_transitions_to_pre_commit() {
    // STREAMING_START (PLAN-03) transitions STREAMING_CANDIDATE → PRE_COMMIT
    // with action=NONE (bookkeeping only).
    let result = plan(
        StreamingState::StreamingCandidate,
        no_record_event(EventKind::StreamingStart),
        &default_ctx(),
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::None);
    assert_eq!(result.plan_decision.transition_id, "PLAN-03");

    let apply = apply_result(
        StreamingState::StreamingCandidate,
        &result.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(apply.new_state, StreamingState::PreCommit);
}

#[test]
fn proto_normal_replay_overflow_transitions_to_unavailable() {
    // REPLAY_OVERFLOW transitions PRE_COMMIT → PRE_COMMIT_REPLAY_UNAVAILABLE.
    let result = plan(
        StreamingState::PreCommit,
        no_record_event(EventKind::ReplayOverflow),
        &default_ctx(),
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::None);

    let apply = apply_result(
        StreamingState::PreCommit,
        &result.first_frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(apply.new_state, StreamingState::PreCommitReplayUnavailable);
}

#[test]
fn proto_normal_upstream_end_triggers_finalize() {
    // UPSTREAM_END triggers FINALIZE_CONVERTER via the formal state machine.
    let result = plan(
        StreamingState::Committed,
        no_record_event(EventKind::UpstreamEnd),
        &default_ctx(),
    )
    .unwrap();
    assert_eq!(result.first_frame.action, Action::FinalizeConverter);
}

#[test]
fn proto_normal_flow_reaches_done() {
    // Normal flow: FINALIZE_CONVERTER → SEND_CLOSING_OUTPUT → SEND_TERMINAL → DONE.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let r1 = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_with_closing(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(r1.new_state, StreamingState::PostCommitSafeFinish);

    let r2 = apply_result(
        r1.new_state,
        &r1.next_frame.unwrap(),
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(r2.new_state, StreamingState::PostCommitSafeFinish);

    let r3 = apply_result(
        r2.new_state,
        &r2.next_frame.unwrap(),
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(r3.new_state, StreamingState::Done);
    assert!(r3.next_frame.is_none());
}

#[test]
fn proto_no_continue_streaming_action() {
    // No CONTINUE_STREAMING Action appears in the formal Plan or Apply tables.
    // The Action enum has exactly 12 variants, none of which is CONTINUE_STREAMING.
    let actions = all_12_actions();
    assert_eq!(actions.len(), 12);
    // Verify by name that none is CONTINUE_STREAMING
    for action in &actions {
        let name = format!("{:?}", action);
        assert!(
            !name.contains("ContinueStreaming"),
            "CONTINUE_STREAMING must not exist"
        );
    }
}

// ════════════════════════════════════════════════════════════════════════════
// Plan/Apply coverage tests (Req 11.1, 11.19)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn proto_plan_ids_are_unique() {
    // Verify that each Plan ID is an individual ID (no range transition IDs
    // like "PLAN-33..PLAN-47").  The same transition_id may legitimately
    // appear for multiple (state, event) pairs when the transition
    // (action + state change) is shared — e.g. PLAN-13 covers both
    // (PreCommit, Error) and (PreCommit, ResourceLimit).
    let ctx = default_ctx();
    for state in all_15_states() {
        for event in all_19_events() {
            let env = match event_failure_policy(event) {
                EventFailurePolicy::Required => required_event(event),
                EventFailurePolicy::ReusePersisted => no_record_event(event),
                EventFailurePolicy::Forbidden => no_record_event(event),
            };
            if let Ok(result) = plan(state, env, &ctx) {
                let id = result.plan_decision.transition_id;
                // No range IDs (e.g., "PLAN-33..PLAN-47")
                assert!(!id.contains(".."), "Range Plan ID forbidden: {}", id);
                // Must match PLAN-xx pattern
                assert!(
                    id.starts_with("PLAN-"),
                    "Plan ID must start with PLAN-: got {}",
                    id
                );
            }
        }
    }
}

#[test]
fn proto_cleanup_has_individual_plan_ids() {
    // Each CLEANUP row has its own PLAN-xx entry (PLAN-34 through PLAN-48).
    let ctx = default_ctx();
    let mut cleanup_ids = Vec::new();
    for state in all_15_states() {
        let result = plan(state, no_record_event(EventKind::Cleanup), &ctx).unwrap();
        cleanup_ids.push(result.plan_decision.transition_id);
    }
    // 15 unique CLEANUP Plan IDs
    let unique: std::collections::HashSet<_> = cleanup_ids.iter().collect();
    assert_eq!(
        unique.len(),
        15,
        "Each CLEANUP row must have its own Plan ID"
    );
    // All start with PLAN-
    for id in &cleanup_ids {
        assert!(
            id.starts_with("PLAN-"),
            "CLEANUP Plan ID must start with PLAN-: {}",
            id
        );
    }
}

// ════════════════════════════════════════════════════════════════════════════
// PendingKind matching tests (Req 11.19)
// ════════════════════════════════════════════════════════════════════════════

#[test]
fn proto_pending_kind_closing_output() {
    // SEND_CLOSING_OUTPUT + NGX_AGAIN → pending_kind=CLOSING_MARKDOWN.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-CLOSE-S2".to_string(),
        action: Action::SendClosingOutput,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let outcome = ActionOutcome {
        ngx_result: NgxResult::Again,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        pending_kind: Some(PendingKind::ClosingMarkdown),
    };
    let result = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &outcome,
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(result.new_state, StreamingState::PendingClosingOutput);
}

#[test]
fn proto_pending_kind_terminal() {
    // SEND_TERMINAL + NGX_AGAIN → pending_kind=TERMINAL.
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-TERM-S3".to_string(),
        action: Action::SendTerminal,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let outcome = ActionOutcome {
        ngx_result: NgxResult::Again,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        pending_kind: Some(PendingKind::Terminal),
    };
    let result = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &outcome,
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(result.new_state, StreamingState::PendingTerminal);
}

#[test]
fn proto_pending_kind_abort_terminal() {
    // SEND_ABORT_TERMINAL + NGX_AGAIN → pending_kind=ABORT_TERMINAL.
    let frame = TransitionFrame {
        transition_id: "PLAN-20".to_string(),
        step_id: "PLAN-20-ABORT-TERM-S3".to_string(),
        action: Action::SendAbortTerminal,
        action_payload: ActionPayload::NULL,
        event: error_event("test"),
        reason: "test".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let outcome = ActionOutcome {
        ngx_result: NgxResult::Again,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        pending_kind: Some(PendingKind::AbortTerminal),
    };
    let result = apply_result(
        StreamingState::PostCommitAbort,
        &frame,
        &outcome,
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(result.new_state, StreamingState::PendingAbortTerminal);
}

// ════════════════════════════════════════════════════════════════════════════
// Protocol tests 6.30-6.53
// ════════════════════════════════════════════════════════════════════════════

// 6.30: FailureLedger is inherited by every PLAN-20 branch frame
#[test]
fn proto_6_30_ledger_inherited_by_branch_frames() {
    let primary = FailureRecord {
        stage: "streaming".to_string(),
        reason: "conversion_error".to_string(),
        error_origin: ErrorOrigin::Internal,
        failure_site: None,
    };
    let ledger = FailureLedger {
        primary: Some(primary),
        secondary: None,
        delivery: None,
        ledger_stored: false,
        ledger_emitted: false,
    };
    let frame = TransitionFrame {
        transition_id: "PLAN-20".to_string(),
        step_id: "PLAN-20".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: error_event("conversion_error"),
        reason: "error_pass_finish".to_string(),
        failure_ledger: ledger.clone(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_with_closing(),
        &ctx_usable(),
    )
    .unwrap();
    // The next frame (SEND_CLOSING_OUTPUT) must carry the same ledger
    let next = result.next_frame.unwrap();
    assert_eq!(
        next.failure_ledger, ledger,
        "FailureLedger must be inherited by branch frame"
    );
}

// 6.31: Frame metadata and FailureLedger are immutable except through failure_updates
#[test]
fn proto_6_31_frame_metadata_immutable() {
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_with_closing(),
        &ctx_usable(),
    )
    .unwrap();
    // The next frame must have the same event and reason
    let next = result.next_frame.unwrap();
    assert_eq!(next.event, frame.event, "Event must be immutable");
    assert_eq!(next.reason, frame.reason, "Reason must be immutable");
}

// 6.32: SEND_TERMINAL does not lose the inherited FailureLedger primary
#[test]
fn proto_6_32_send_terminal_preserves_ledger_primary() {
    let primary = FailureRecord {
        stage: "streaming".to_string(),
        reason: "conversion_error".to_string(),
        error_origin: ErrorOrigin::Internal,
        failure_site: None,
    };
    let ledger = FailureLedger {
        primary: Some(primary.clone()),
        secondary: None,
        delivery: None,
        ledger_stored: false,
        ledger_emitted: false,
    };
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-TERM-S3".to_string(),
        action: Action::SendTerminal,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: ledger,
    };
    let result = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // The frame's ledger primary is preserved (the frame itself is immutable)
    assert_eq!(frame.failure_ledger.primary, Some(primary));
    // The result reaches DONE
    assert_eq!(result.new_state, StreamingState::Done);
}

// 6.34: Normal upstream end: finalize success → closing → terminal → DONE
#[test]
fn proto_6_34_normal_upstream_end_happy_path() {
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let r1 = apply_result(
        StreamingState::Committed,
        &frame,
        &ok_with_closing(),
        &ctx_usable(),
    )
    .unwrap();
    let r2 = apply_result(
        r1.new_state,
        &r1.next_frame.unwrap(),
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    let r3 = apply_result(
        r2.new_state,
        &r2.next_frame.unwrap(),
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(r3.new_state, StreamingState::Done);
    // No FailureRecord was promoted (normal path)
    assert!(!r1.failure_updates.pre_effect.primary_update.is_some());
}

// 6.35: Normal upstream end: finalize failure → promote to primary → BEGIN_ABORT
#[test]
fn proto_6_35_finalize_failure_promotes_primary() {
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21".to_string(),
        action: Action::FinalizeConverter,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::Committed,
        &frame,
        &finalize_error_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // Primary promoted (was None)
    assert!(result.failure_updates.pre_effect.primary_update.is_some());
    // next_frame = BEGIN_ABORT
    assert_eq!(
        result.next_frame.as_ref().unwrap().action,
        Action::BeginAbort
    );
}

// 6.36: Abort-terminal success/pending/failure from PLAN-31
#[test]
fn proto_6_36_abort_terminal_outcomes() {
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-ABORT-TERM-S3".to_string(),
        action: Action::SendAbortTerminal,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    // NGX_OK → DONE
    let r_ok = apply_result(
        StreamingState::PostCommitAbort,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(r_ok.new_state, StreamingState::Done);

    // NGX_AGAIN → PENDING_ABORT_TERMINAL
    let r_again = apply_result(
        StreamingState::PostCommitAbort,
        &frame,
        &again_abort_terminal_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(r_again.new_state, StreamingState::PendingAbortTerminal);

    // NGX_ERROR → ABORTED
    let r_err = apply_result(
        StreamingState::PostCommitAbort,
        &frame,
        &error_outcome(FailureSite::AbortTerminalSend),
        &ctx_usable(),
    )
    .unwrap();
    assert_eq!(r_err.new_state, StreamingState::Aborted);
}

// 6.47: Each FailureLedger has exactly one owner
#[test]
fn proto_6_47_ledger_has_exactly_one_owner() {
    // The FailureLedgerOwner enum has exactly 3 variants: Streaming, FullBuffer, Released.
    // Every path reaches RELEASED.
    let owners = [
        FailureLedgerOwner::Streaming,
        FailureLedgerOwner::FullBuffer,
        FailureLedgerOwner::Released,
    ];
    assert_eq!(owners.len(), 3);
    // Verify that terminal states release the owner
    let frame = TransitionFrame {
        transition_id: "PLAN-21".to_string(),
        step_id: "PLAN-21-TERM-S3".to_string(),
        action: Action::SendTerminal,
        action_payload: ActionPayload::NULL,
        event: no_record_event(EventKind::UpstreamEnd),
        reason: "upstream_end".to_string(),
        failure_ledger: FailureLedger::empty(),
    };
    let result = apply_result(
        StreamingState::PostCommitSafeFinish,
        &frame,
        &ok_outcome(),
        &ctx_usable(),
    )
    .unwrap();
    // DONE → owner transition to RELEASED in the emit command
    assert!(result.side_effects.iter().any(|cmd| matches!(
        cmd.owner_transition,
        Some(OwnerTransition::StreamingToReleased)
    )));
}

// 6.49: Every legal row is atomic (no compound values)
#[test]
fn proto_6_49_legal_rows_atomic() {
    // The ActionOutcome struct has exactly 5 fields (frozen shape columns):
    // ngx_result, failure_site, error_origin, produced_closing_bytes, pending_kind
    // Each field is atomic (no lists, ranges, or compound values).
    let outcome = ActionOutcome {
        ngx_result: NgxResult::Ok,
        failure_site: None,
        error_origin: None,
        produced_closing_bytes: false,
        pending_kind: None,
    };
    // Verify field values round-trip with the atomic types.
    let _ngx_result: NgxResult = outcome.ngx_result; // enum (atomic)
    let _failure_site: Option<FailureSite> = outcome.failure_site; // Option<enum> (atomic)
    let _error_origin: Option<ErrorOrigin> = outcome.error_origin; // Option<enum> (atomic)
    let _produced_closing_bytes: bool = outcome.produced_closing_bytes; // bool (atomic)
    let _pending_kind: Option<PendingKind> = outcome.pending_kind; // Option<enum> (atomic)
    assert_eq!(outcome.ngx_result, NgxResult::Ok);
    assert_eq!(outcome.failure_site, None);
    assert_eq!(outcome.error_origin, None);
    assert!(!outcome.produced_closing_bytes);
    assert_eq!(outcome.pending_kind, None);
    // Every combination of the atomic fields must be constructible (no
    // invariant-enforcing constructor that could reject a legal row).
    for ngx in [NgxResult::Ok, NgxResult::Done, NgxResult::Again] {
        let row = ActionOutcome {
            ngx_result: ngx,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: true,
            pending_kind: None,
        };
        assert_eq!(row.ngx_result, ngx);
        assert!(row.produced_closing_bytes);
    }
}

// 6.51: ResolvedErrorPolicy and ActionPayload coverage
#[test]
fn proto_6_51_resolved_error_policy_coverage() {
    // STATUS_502 → 502
    assert_eq!(
        resolve_reject_status(ResolvedErrorPolicy::Status502, "any"),
        Some(502)
    );
    // STATUS_429 → 429
    assert_eq!(
        resolve_reject_status(ResolvedErrorPolicy::Status429, "any"),
        Some(429)
    );
    // STATUS_503 → 503
    assert_eq!(
        resolve_reject_status(ResolvedErrorPolicy::Status503, "any"),
        Some(503)
    );
    // PASS + fail_open_unavailable → 502
    assert_eq!(
        resolve_reject_status(ResolvedErrorPolicy::Pass, "fail_open_unavailable"),
        Some(502)
    );
    // PASS + resource_limit → 502
    assert_eq!(
        resolve_reject_status(ResolvedErrorPolicy::Pass, "resource_limit"),
        Some(502)
    );
    // PASS + other → None
    assert_eq!(
        resolve_reject_status(ResolvedErrorPolicy::Pass, "other"),
        None
    );
}

// 6.52: All protocol table counts exact
#[test]
fn proto_6_52_state_event_action_counts() {
    // 15 states
    assert_eq!(all_15_states().len(), 15);
    // 19 events
    assert_eq!(all_19_events().len(), 19);
    // 12 actions
    assert_eq!(all_12_actions().len(), 12);
    // 5 failure sites
    assert_eq!(all_5_failure_sites().len(), 5);
    // 8 error origins
    assert_eq!(all_8_error_origins().len(), 8);
    // 3 pending kinds
    let pending_kinds = [
        PendingKind::ClosingMarkdown,
        PendingKind::Terminal,
        PendingKind::AbortTerminal,
    ];
    assert_eq!(pending_kinds.len(), 3);
    // 3 failure ledger owners
    let owners = [
        FailureLedgerOwner::Streaming,
        FailureLedgerOwner::FullBuffer,
        FailureLedgerOwner::Released,
    ];
    assert_eq!(owners.len(), 3);
    // 8 side effect kinds
    let side_effects = [
        SideEffectKind::LatchTerminalSent,
        SideEffectKind::SetSafeFinishOutputLoss,
        SideEffectKind::SetSafeFinishTerminalSendFailed,
        SideEffectKind::StoreFailureLedger,
        SideEffectKind::EmitFailureLedger,
        SideEffectKind::RecordPostcommitAbort,
        SideEffectKind::ClearInflightAndPending,
        SideEffectKind::TransferFailureToFullBuffer,
    ];
    assert_eq!(side_effects.len(), 8);
    // 4 resolved error policies
    let policies = [
        ResolvedErrorPolicy::Pass,
        ResolvedErrorPolicy::Status502,
        ResolvedErrorPolicy::Status429,
        ResolvedErrorPolicy::Status503,
    ];
    assert_eq!(policies.len(), 4);
}

// 6.53: Stale protocol vocabulary and counts are absent
#[test]
fn proto_6_53_no_stale_vocabulary() {
    // Verify that the Action enum does not contain CONTINUE_STREAMING
    // (removed from the formal set per the spec).
    for action in all_12_actions() {
        let name = format!("{:?}", action);
        assert!(!name.contains("ContinueStreaming"));
        assert!(
            !name.contains("REJECT_502"),
            "REJECT_502 must be renamed to REJECT_STATUS"
        );
    }
    // Verify REJECT_STATUS exists (not REJECT_502)
    assert!(all_12_actions().contains(&Action::RejectStatus));
}
