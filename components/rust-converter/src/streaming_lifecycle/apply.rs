//! Apply phase of the two-phase decision protocol.
//!
//! `apply_result(state, transition_frame, action_outcome, transition_context)`
//!     `→ {new_state, next_frame | null, side_effects, failure_updates}`
//!
//! Advances state based on structured ActionOutcome. The caller:
//! 1. Applies pre-effect slot updates
//! 2. Executes closed commands in list order
//! 3. Records EXECUTED or SKIPPED_OWNER_MISMATCH
//! 4. Applies post-effect latches after command success
//! 5. Commits new_state and constructs next_frame
//!
//! Action handlers are grouped by lifecycle responsibility:
//! - bookkeeping: `apply_none`, `apply_pass_html`, `apply_reject_status`,
//!   and `apply_passthrough`
//! - pre-commit routing: `apply_commit_headers` and
//!   `apply_switch_full_buffer`
//! - terminal output: `apply_finalize_converter`,
//!   `apply_send_closing_output`, and `apply_send_terminal`
//! - failure and resume: `apply_begin_abort`, `apply_send_abort_terminal`,
//!   and `apply_resume_pending`

use super::types::{
    Action, ActionOutcome, ActionPayload, ApplyResult, EmitFailureLedgerDisposition,
    EmitFailureLedgerPayload, ErrorOrigin, EventKind, FailureLedger, FailureRecord, FailureSite,
    FailureUpdates, NgxResult, OwnerPredicate, OwnerTransition, PostEffect, PreEffect,
    SideEffectCommand, SideEffectKind, SideEffectPayload, StateMachineError, StreamingState,
    TelemetryScope, TransferMode, TransitionContext, TransitionFrame,
};

/// Execute the apply phase of the streaming lifecycle state machine.
///
/// Given the current state, the transition frame (from plan or a previous
/// apply_result), the action outcome, and the transition context, returns
/// the new state, optional next frame, side effects, and failure updates.
pub fn apply_result(
    state: StreamingState,
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
    ctx: &TransitionContext,
) -> Result<ApplyResult, StateMachineError> {
    match frame.action {
        Action::None => apply_none(state, frame, outcome),
        Action::PassHtml => apply_pass_html(state, frame),
        Action::RejectStatus => apply_reject_status(state, frame),
        Action::CommitHeaders => apply_commit_headers(state, frame, outcome),
        Action::SwitchFullBuffer => apply_switch_full_buffer(state, frame),
        Action::FinalizeConverter => apply_finalize_converter(state, frame, outcome, ctx),
        Action::SendClosingOutput => apply_send_closing_output(state, frame, outcome),
        Action::SendTerminal => apply_send_terminal(state, frame, outcome),
        Action::BeginAbort => apply_begin_abort(state, frame, outcome, ctx),
        Action::SendAbortTerminal => apply_send_abort_terminal(state, frame, outcome),
        Action::ResumePending => apply_resume_pending(state, frame, outcome),
        Action::Passthrough => apply_passthrough(state, frame),
    }
}

/// NONE action: state bookkeeping transitions.
///
/// Distinguished by step_id/event per the protocol closure contract.
fn apply_none(
    state: StreamingState,
    frame: &TransitionFrame,
    _outcome: &ActionOutcome,
) -> Result<ApplyResult, StateMachineError> {
    let event_kind = frame.event.kind;

    match (state, event_kind) {
        /* NOT_ELIGIBLE + ELIGIBLE → STREAMING_CANDIDATE */
        (StreamingState::NotEligible, EventKind::Eligible) => Ok(ApplyResult {
            new_state: StreamingState::StreamingCandidate,
            next_frame: None,
            side_effects: vec![],
            failure_updates: FailureUpdates::NONE,
        }),

        /* STREAMING_CANDIDATE + STREAMING_START → PRE_COMMIT */
        (StreamingState::StreamingCandidate, EventKind::StreamingStart) => Ok(ApplyResult {
            new_state: StreamingState::PreCommit,
            next_frame: None,
            side_effects: vec![],
            failure_updates: FailureUpdates::NONE,
        }),

        /* PRE_COMMIT + REPLAY_OVERFLOW → PRE_COMMIT_REPLAY_UNAVAILABLE */
        (StreamingState::PreCommit, EventKind::ReplayOverflow) => Ok(ApplyResult {
            new_state: StreamingState::PreCommitReplayUnavailable,
            next_frame: None,
            side_effects: vec![],
            failure_updates: FailureUpdates::NONE,
        }),

        /* PRE_COMMIT + LOOK_BEHIND_OVERFLOW → PRE_COMMIT_REPLAY_UNAVAILABLE */
        (StreamingState::PreCommit, EventKind::LookBehindOverflow) => Ok(ApplyResult {
            new_state: StreamingState::PreCommitReplayUnavailable,
            next_frame: None,
            side_effects: vec![],
            failure_updates: FailureUpdates::NONE,
        }),

        /* DONE/ABORTED/FAILED_CLOSED + BODY_FILTER_REENTRY → idempotent no-op */
        (s, EventKind::BodyFilterReentry) if s.is_terminal() => Ok(ApplyResult {
            new_state: s,
            next_frame: None,
            side_effects: vec![],
            failure_updates: FailureUpdates::NONE,
        }),

        /* CLIENT_ABORT in committed/pending states → ABORTED + emit + clear */
        (s, EventKind::ClientAbort) if s.is_committed_or_pending() => {
            let cmd_id = format!("CMD_A{}", frame.transition_id.replace("PLAN-", ""));
            Ok(ApplyResult {
                new_state: StreamingState::Aborted,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: cmd_id.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                    SideEffectCommand {
                        command_id: format!("{}-CLR", cmd_id),
                        kind: SideEffectKind::ClearInflightAndPending,
                        execute_if: OwnerPredicate::Always,
                        payload: SideEffectPayload::None,
                        owner_transition: None,
                    },
                ],
                failure_updates: FailureUpdates::NONE,
            })
        }

        /* CLEANUP in FULL_BUFFER_FALLBACK → clear only (no emit) */
        (StreamingState::FullBufferFallback, EventKind::Cleanup) => Ok(ApplyResult {
            new_state: StreamingState::FullBufferFallback,
            next_frame: None,
            side_effects: vec![SideEffectCommand {
                command_id: format!("CMD_A{}-CLR", frame.transition_id.replace("PLAN-", "")),
                kind: SideEffectKind::ClearInflightAndPending,
                execute_if: OwnerPredicate::Always,
                payload: SideEffectPayload::None,
                owner_transition: None,
            }],
            failure_updates: FailureUpdates::NONE,
        }),

        /* CLEANUP in all other states → emit + clear (state preserved) */
        (s, EventKind::Cleanup) => {
            let cmd_id = format!("CMD_A{}", frame.transition_id.replace("PLAN-", ""));
            Ok(ApplyResult {
                new_state: s,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: cmd_id.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                    SideEffectCommand {
                        command_id: format!("{}-CLR", cmd_id),
                        kind: SideEffectKind::ClearInflightAndPending,
                        execute_if: OwnerPredicate::Always,
                        payload: SideEffectPayload::None,
                        owner_transition: None,
                    },
                ],
                failure_updates: FailureUpdates::NONE,
            })
        }

        _ => Err(StateMachineError::InvariantViolation {
            message: format!(
                "NONE action with unexpected (state={:?}, event={:?})",
                state, event_kind
            ),
        }),
    }
}

/// PASS_HTML: deliver original upstream HTML unchanged.
fn apply_pass_html(
    _state: StreamingState,
    frame: &TransitionFrame,
) -> Result<ApplyResult, StateMachineError> {
    Ok(ApplyResult {
        new_state: StreamingState::Passthrough,
        next_frame: None,
        side_effects: vec![SideEffectCommand {
            command_id: format!("CMD_A{}-CLR", frame.transition_id.replace("PLAN-", "")),
            kind: SideEffectKind::ClearInflightAndPending,
            execute_if: OwnerPredicate::Always,
            payload: SideEffectPayload::None,
            owner_transition: None,
        }],
        failure_updates: FailureUpdates::NONE,
    })
}

/// REJECT_STATUS: emit the resolved reject status and stop.
fn apply_reject_status(
    _state: StreamingState,
    frame: &TransitionFrame,
) -> Result<ApplyResult, StateMachineError> {
    Ok(ApplyResult {
        new_state: StreamingState::FailedClosed,
        next_frame: None,
        side_effects: vec![SideEffectCommand {
            command_id: format!("CMD_A{}-CLR", frame.transition_id.replace("PLAN-", "")),
            kind: SideEffectKind::ClearInflightAndPending,
            execute_if: OwnerPredicate::Always,
            payload: SideEffectPayload::None,
            owner_transition: None,
        }],
        failure_updates: FailureUpdates::NONE,
    })
}

/// COMMIT_HEADERS: commit the HeaderPlan. Legal outcome is only synthetic NGX_OK.
fn apply_commit_headers(
    _state: StreamingState,
    _frame: &TransitionFrame,
    outcome: &ActionOutcome,
) -> Result<ApplyResult, StateMachineError> {
    if outcome.ngx_result != NgxResult::Ok {
        return Err(StateMachineError::InvariantViolation {
            message: "COMMIT_HEADERS must have synthetic NGX_OK outcome".to_string(),
        });
    }
    Ok(ApplyResult {
        new_state: StreamingState::Committed,
        next_frame: None,
        side_effects: vec![],
        failure_updates: FailureUpdates::NONE,
    })
}

/// SWITCH_FULL_BUFFER: abandon streaming, route to full-buffer.
fn apply_switch_full_buffer(
    _state: StreamingState,
    frame: &TransitionFrame,
) -> Result<ApplyResult, StateMachineError> {
    let cmd_id = format!("CMD_A{}-XFER", frame.transition_id.replace("PLAN-", ""));
    let mode = if frame.failure_ledger.primary.is_some() {
        TransferMode::FailureRecovery
    } else {
        TransferMode::RoutingOwnership
    };
    Ok(ApplyResult {
        new_state: StreamingState::FullBufferFallback,
        next_frame: None,
        side_effects: vec![
            SideEffectCommand {
                command_id: cmd_id.clone(),
                kind: SideEffectKind::TransferFailureToFullBuffer,
                execute_if: OwnerPredicate::OwnerIsStreaming,
                payload: SideEffectPayload::TransferMode(mode),
                owner_transition: Some(OwnerTransition::StreamingToFullBuffer),
            },
            SideEffectCommand {
                command_id: format!("{}-CLR", cmd_id),
                kind: SideEffectKind::ClearInflightAndPending,
                execute_if: OwnerPredicate::Always,
                payload: SideEffectPayload::None,
                owner_transition: None,
            },
        ],
        failure_updates: if mode == TransferMode::FailureRecovery {
            FailureUpdates {
                pre_effect: PreEffect {
                    primary_update: None,
                    secondary_update: None,
                    delivery_update: None,
                },
                post_effect: PostEffect {
                    set_ledger_stored_after: None,
                    set_ledger_stored_if_nonempty_after: Some(cmd_id),
                    set_ledger_emitted_if_unemitted_after: None,
                },
            }
        } else {
            FailureUpdates::NONE
        },
    })
}

/// PASSTHROUGH: bypass conversion entirely.
fn apply_passthrough(
    _state: StreamingState,
    frame: &TransitionFrame,
) -> Result<ApplyResult, StateMachineError> {
    Ok(ApplyResult {
        new_state: StreamingState::Passthrough,
        next_frame: None,
        side_effects: vec![SideEffectCommand {
            command_id: format!("CMD_A{}-CLR", frame.transition_id.replace("PLAN-", "")),
            kind: SideEffectKind::ClearInflightAndPending,
            execute_if: OwnerPredicate::Always,
            payload: SideEffectPayload::None,
            owner_transition: None,
        }],
        failure_updates: FailureUpdates::NONE,
    })
}

/// FINALIZE_CONVERTER: finalize the Rust converter handle.
///
/// Success path branches on produced_closing_bytes:
/// - Zero closing bytes → next_frame=SEND_TERMINAL
/// - Closing bytes present → next_frame=SEND_CLOSING_OUTPUT
///
/// Failure (definitive NGX_ERROR, failure_site=converter_finalize):
/// - downstream usable → next_frame=BEGIN_ABORT
/// - downstream NOT usable → direct ABORTED + store/emit
fn apply_finalize_converter(
    _state: StreamingState,
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
    ctx: &TransitionContext,
) -> Result<ApplyResult, StateMachineError> {
    match outcome.ngx_result {
        NgxResult::Ok | NgxResult::Done => {
            let new_state = StreamingState::PostCommitSafeFinish;
            if outcome.produced_closing_bytes {
                /* Closing bytes → next step is SEND_CLOSING_OUTPUT */
                let next = TransitionFrame {
                    transition_id: frame.transition_id.clone(),
                    step_id: format!("{}-CLOSE-S2", frame.transition_id),
                    action: Action::SendClosingOutput,
                    action_payload: ActionPayload::NULL,
                    event: frame.event.clone(),
                    reason: frame.reason.clone(),
                    failure_ledger: frame.failure_ledger.clone(),
                };
                Ok(ApplyResult {
                    new_state,
                    next_frame: Some(next),
                    side_effects: vec![],
                    failure_updates: FailureUpdates::NONE,
                })
            } else {
                /* No closing bytes → next step is SEND_TERMINAL */
                let next = TransitionFrame {
                    transition_id: frame.transition_id.clone(),
                    step_id: format!("{}-TERM-S2", frame.transition_id),
                    action: Action::SendTerminal,
                    action_payload: ActionPayload::NULL,
                    event: frame.event.clone(),
                    reason: frame.reason.clone(),
                    failure_ledger: frame.failure_ledger.clone(),
                };
                Ok(ApplyResult {
                    new_state,
                    next_frame: Some(next),
                    side_effects: vec![],
                    failure_updates: FailureUpdates::NONE,
                })
            }
        }
        NgxResult::Error => {
            /* FINALIZE_CONVERTER failure */
            let record = FailureRecord {
                stage: "streaming".to_string(),
                reason: frame.reason.clone(),
                error_origin: outcome.error_origin.unwrap_or(ErrorOrigin::Internal),
                failure_site: Some(FailureSite::ConverterFinalize),
            };
            let pre_effect = compute_finalize_failure_updates(&frame.failure_ledger, &record);

            if ctx.downstream_usable {
                /* downstream usable → BEGIN_ABORT chain */
                let next = TransitionFrame {
                    transition_id: frame.transition_id.clone(),
                    step_id: format!("{}-ABORT-S2", frame.transition_id),
                    action: Action::BeginAbort,
                    action_payload: ActionPayload::NULL,
                    event: frame.event.clone(),
                    reason: frame.reason.clone(),
                    failure_ledger: frame.failure_ledger.clone(),
                };
                let cmd_store = format!("CMD_A{}-STORE", frame.transition_id.replace("PLAN-", ""));
                Ok(ApplyResult {
                    new_state: StreamingState::PostCommitAbort,
                    next_frame: Some(next),
                    side_effects: vec![SideEffectCommand {
                        command_id: cmd_store.clone(),
                        kind: SideEffectKind::StoreFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::UpdatedLedger,
                        owner_transition: None,
                    }],
                    failure_updates: FailureUpdates {
                        pre_effect,
                        post_effect: PostEffect {
                            set_ledger_stored_after: Some(cmd_store),
                            set_ledger_stored_if_nonempty_after: None,
                            set_ledger_emitted_if_unemitted_after: None,
                        },
                    },
                })
            } else {
                /* downstream NOT usable → direct ABORTED */
                let cmd_store = format!("CMD_A{}-STORE", frame.transition_id.replace("PLAN-", ""));
                let cmd_emit = format!("CMD_A{}-EMIT", frame.transition_id.replace("PLAN-", ""));
                Ok(ApplyResult {
                    new_state: StreamingState::Aborted,
                    next_frame: None,
                    side_effects: vec![
                        SideEffectCommand {
                            command_id: cmd_store.clone(),
                            kind: SideEffectKind::StoreFailureLedger,
                            execute_if: OwnerPredicate::OwnerIsStreaming,
                            payload: SideEffectPayload::UpdatedLedger,
                            owner_transition: None,
                        },
                        SideEffectCommand {
                            command_id: cmd_emit.clone(),
                            kind: SideEffectKind::EmitFailureLedger,
                            execute_if: OwnerPredicate::OwnerIsStreaming,
                            payload: SideEffectPayload::EmitFailureLedger(
                                EmitFailureLedgerPayload {
                                    telemetry_scope: TelemetryScope::UnemittedSlots,
                                    disposition: EmitFailureLedgerDisposition::Default,
                                },
                            ),
                            owner_transition: Some(OwnerTransition::StreamingToReleased),
                        },
                    ],
                    failure_updates: FailureUpdates {
                        pre_effect,
                        post_effect: PostEffect {
                            set_ledger_stored_after: Some(cmd_store),
                            set_ledger_stored_if_nonempty_after: None,
                            set_ledger_emitted_if_unemitted_after: Some(cmd_emit),
                        },
                    },
                })
            }
        }
        NgxResult::Again => Err(StateMachineError::InvariantViolation {
            message: "FINALIZE_CONVERTER cannot return NGX_AGAIN".to_string(),
        }),
    }
}

/// Compute failure updates for FINALIZE_CONVERTER failure.
///
/// PLAN-20 (ERROR event): finalize failure → secondary (primary already exists).
/// PLAN-21 (UPSTREAM_END): finalize failure → primary (no prior failure).
fn compute_finalize_failure_updates(ledger: &FailureLedger, record: &FailureRecord) -> PreEffect {
    if ledger.primary.is_none() {
        PreEffect {
            primary_update: Some(record.clone()),
            secondary_update: None,
            delivery_update: None,
        }
    } else {
        PreEffect {
            primary_update: None,
            secondary_update: Some(record.clone()),
            delivery_update: None,
        }
    }
}

/// SEND_CLOSING_OUTPUT: send closing Markdown bytes downstream.
fn apply_send_closing_output(
    _state: StreamingState,
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
) -> Result<ApplyResult, StateMachineError> {
    match outcome.ngx_result {
        NgxResult::Ok | NgxResult::Done => {
            /* Success → next step is SEND_TERMINAL */
            let next = TransitionFrame {
                transition_id: frame.transition_id.clone(),
                step_id: format!("{}-TERM-S3", frame.transition_id),
                action: Action::SendTerminal,
                action_payload: ActionPayload::NULL,
                event: frame.event.clone(),
                reason: frame.reason.clone(),
                failure_ledger: frame.failure_ledger.clone(),
            };
            Ok(ApplyResult {
                new_state: StreamingState::PostCommitSafeFinish,
                next_frame: Some(next),
                side_effects: vec![],
                failure_updates: FailureUpdates::NONE,
            })
        }
        NgxResult::Again => {
            /* NGX_AGAIN → PENDING_CLOSING_OUTPUT */
            Ok(ApplyResult {
                new_state: StreamingState::PendingClosingOutput,
                next_frame: None,
                side_effects: vec![],
                failure_updates: FailureUpdates::NONE,
            })
        }
        NgxResult::Error => {
            /* Definitive error → output-loss hard abort */
            let record = FailureRecord {
                stage: "streaming".to_string(),
                reason: frame.reason.clone(),
                error_origin: outcome.error_origin.unwrap_or(ErrorOrigin::Downstream),
                failure_site: Some(FailureSite::ClosingOutput),
            };
            let pre_effect = promote_or_delivery(&frame.failure_ledger, &record);
            let cmd_store = format!(
                "CMD_A{}-CLOSE-STORE",
                frame.transition_id.replace("PLAN-", "")
            );
            let cmd_emit = format!(
                "CMD_A{}-CLOSE-EMIT",
                frame.transition_id.replace("PLAN-", "")
            );
            Ok(ApplyResult {
                new_state: StreamingState::Aborted,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: format!(
                            "CMD_A{}-CLOSE-LOSS",
                            frame.transition_id.replace("PLAN-", "")
                        ),
                        kind: SideEffectKind::SetSafeFinishOutputLoss,
                        execute_if: OwnerPredicate::Always,
                        payload: SideEffectPayload::None,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_store.clone(),
                        kind: SideEffectKind::StoreFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::UpdatedLedger,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_emit.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                ],
                failure_updates: FailureUpdates {
                    pre_effect,
                    post_effect: PostEffect {
                        set_ledger_stored_after: Some(cmd_store),
                        set_ledger_stored_if_nonempty_after: None,
                        set_ledger_emitted_if_unemitted_after: Some(cmd_emit),
                    },
                },
            })
        }
    }
}

/// SEND_TERMINAL: send the terminal chain (last_buf=1) downstream.
fn apply_send_terminal(
    _state: StreamingState,
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
) -> Result<ApplyResult, StateMachineError> {
    match outcome.ngx_result {
        NgxResult::Ok | NgxResult::Done => {
            /* Success → DONE + latch + emit */
            let cmd_latch = format!(
                "CMD_A{}-TERM-LATCH",
                frame.transition_id.replace("PLAN-", "")
            );
            let cmd_emit = format!(
                "CMD_A{}-TERM-EMIT",
                frame.transition_id.replace("PLAN-", "")
            );
            Ok(ApplyResult {
                new_state: StreamingState::Done,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: cmd_latch,
                        kind: SideEffectKind::LatchTerminalSent,
                        execute_if: OwnerPredicate::Always,
                        payload: SideEffectPayload::None,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_emit.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                ],
                failure_updates: FailureUpdates {
                    pre_effect: PreEffect {
                        primary_update: None,
                        secondary_update: None,
                        delivery_update: None,
                    },
                    post_effect: PostEffect {
                        set_ledger_stored_after: None,
                        set_ledger_stored_if_nonempty_after: None,
                        set_ledger_emitted_if_unemitted_after: Some(cmd_emit),
                    },
                },
            })
        }
        NgxResult::Again => {
            /* NGX_AGAIN → PENDING_TERMINAL, no latch */
            Ok(ApplyResult {
                new_state: StreamingState::PendingTerminal,
                next_frame: None,
                side_effects: vec![],
                failure_updates: FailureUpdates::NONE,
            })
        }
        NgxResult::Error => {
            /* Definitive error → ABORTED + terminal send failed latch */
            let record = FailureRecord {
                stage: "streaming".to_string(),
                reason: frame.reason.clone(),
                error_origin: outcome.error_origin.unwrap_or(ErrorOrigin::Downstream),
                failure_site: Some(FailureSite::TerminalSend),
            };
            let pre_effect = promote_or_delivery(&frame.failure_ledger, &record);
            let cmd_store = format!(
                "CMD_A{}-TERM-STORE",
                frame.transition_id.replace("PLAN-", "")
            );
            let cmd_emit = format!(
                "CMD_A{}-TERM-FAIL-EMIT",
                frame.transition_id.replace("PLAN-", "")
            );
            Ok(ApplyResult {
                new_state: StreamingState::Aborted,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: format!(
                            "CMD_A{}-TERM-FAIL",
                            frame.transition_id.replace("PLAN-", "")
                        ),
                        kind: SideEffectKind::SetSafeFinishTerminalSendFailed,
                        execute_if: OwnerPredicate::Always,
                        payload: SideEffectPayload::None,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_store.clone(),
                        kind: SideEffectKind::StoreFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::UpdatedLedger,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_emit.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                ],
                failure_updates: FailureUpdates {
                    pre_effect,
                    post_effect: PostEffect {
                        set_ledger_stored_after: Some(cmd_store),
                        set_ledger_stored_if_nonempty_after: None,
                        set_ledger_emitted_if_unemitted_after: Some(cmd_emit),
                    },
                },
            })
        }
    }
}

/// BEGIN_ABORT: initiate a protocol-safe abort.
fn apply_begin_abort(
    _state: StreamingState,
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
    _ctx: &TransitionContext,
) -> Result<ApplyResult, StateMachineError> {
    match outcome.ngx_result {
        NgxResult::Ok => {
            /* Setup complete → next step is SEND_ABORT_TERMINAL.
             * BEGIN_ABORT is a synthetic setup step: only NGX_OK is legal.
             * NGX_DONE is rejected because there is no async suspension in
             * the abort setup path. */
            let next = TransitionFrame {
                transition_id: frame.transition_id.clone(),
                step_id: format!("{}-ABORT-TERM-S3", frame.transition_id),
                action: Action::SendAbortTerminal,
                action_payload: ActionPayload::NULL,
                event: frame.event.clone(),
                reason: frame.reason.clone(),
                failure_ledger: frame.failure_ledger.clone(),
            };
            Ok(ApplyResult {
                new_state: StreamingState::PostCommitAbort,
                next_frame: Some(next),
                side_effects: vec![SideEffectCommand {
                    command_id: format!(
                        "CMD_A{}-ABORT-REC",
                        frame.transition_id.replace("PLAN-", "")
                    ),
                    kind: SideEffectKind::RecordPostcommitAbort,
                    execute_if: OwnerPredicate::Always,
                    payload: SideEffectPayload::None,
                    owner_transition: None,
                }],
                failure_updates: FailureUpdates::NONE,
            })
        }
        _ => Err(StateMachineError::InvariantViolation {
            message: "BEGIN_ABORT must complete with NGX_OK".to_string(),
        }),
    }
}

/// SEND_ABORT_TERMINAL: send the abort terminal chain (last_buf=1, no content).
fn apply_send_abort_terminal(
    state: StreamingState,
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
) -> Result<ApplyResult, StateMachineError> {
    apply_abort_terminal_with_prefix(state, frame, outcome, "ATERM")
}

fn apply_abort_terminal_with_prefix(
    _state: StreamingState,
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
    command_prefix: &str,
) -> Result<ApplyResult, StateMachineError> {
    match outcome.ngx_result {
        NgxResult::Ok | NgxResult::Done => {
            /* Success → DONE + latch + emit */
            let cmd_latch = format!(
                "CMD_A{}-{}-LATCH",
                frame.transition_id.replace("PLAN-", ""),
                command_prefix,
            );
            let cmd_emit = format!(
                "CMD_A{}-{}-EMIT",
                frame.transition_id.replace("PLAN-", ""),
                command_prefix,
            );
            Ok(ApplyResult {
                new_state: StreamingState::Done,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: cmd_latch,
                        kind: SideEffectKind::LatchTerminalSent,
                        execute_if: OwnerPredicate::Always,
                        payload: SideEffectPayload::None,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_emit.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                ],
                failure_updates: FailureUpdates {
                    pre_effect: PreEffect {
                        primary_update: None,
                        secondary_update: None,
                        delivery_update: None,
                    },
                    post_effect: PostEffect {
                        set_ledger_stored_after: None,
                        set_ledger_stored_if_nonempty_after: None,
                        set_ledger_emitted_if_unemitted_after: Some(cmd_emit),
                    },
                },
            })
        }
        NgxResult::Again => {
            /* NGX_AGAIN → PENDING_ABORT_TERMINAL, no latch */
            Ok(ApplyResult {
                new_state: StreamingState::PendingAbortTerminal,
                next_frame: None,
                side_effects: vec![],
                failure_updates: FailureUpdates::NONE,
            })
        }
        NgxResult::Error => {
            /* Definitive error → ABORTED, no retry */
            let record = FailureRecord {
                stage: "streaming".to_string(),
                reason: frame.reason.clone(),
                error_origin: outcome.error_origin.unwrap_or(ErrorOrigin::Downstream),
                failure_site: Some(FailureSite::AbortTerminalSend),
            };
            let pre_effect = promote_or_delivery(&frame.failure_ledger, &record);
            let cmd_store = format!(
                "CMD_A{}-{}-STORE",
                frame.transition_id.replace("PLAN-", ""),
                command_prefix,
            );
            let cmd_emit = format!(
                "CMD_A{}-{}-FAIL-EMIT",
                frame.transition_id.replace("PLAN-", ""),
                command_prefix,
            );
            Ok(ApplyResult {
                new_state: StreamingState::Aborted,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: cmd_store.clone(),
                        kind: SideEffectKind::StoreFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::UpdatedLedger,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_emit.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                ],
                failure_updates: FailureUpdates {
                    pre_effect,
                    post_effect: PostEffect {
                        set_ledger_stored_after: Some(cmd_store),
                        set_ledger_stored_if_nonempty_after: None,
                        set_ledger_emitted_if_unemitted_after: Some(cmd_emit),
                    },
                },
            })
        }
    }
}

/// RESUME_PENDING: resume a downstream-owned pending chain.
///
/// Behavior depends on current pending state:
/// - PENDING_CLOSING_OUTPUT + OK/Done → next_frame=SEND_TERMINAL
/// - PENDING_CLOSING_OUTPUT + Again → remain
/// - PENDING_CLOSING_OUTPUT + Error → output-loss abort
/// - PENDING_TERMINAL + OK/Done → DONE + latch + emit
/// - PENDING_TERMINAL + Again → remain
/// - PENDING_TERMINAL + Error → ABORTED, no retry
/// - PENDING_ABORT_TERMINAL + OK/Done → DONE + latch + emit
/// - PENDING_ABORT_TERMINAL + Again → remain
/// - PENDING_ABORT_TERMINAL + Error → ABORTED, no retry
fn apply_resume_pending(
    state: StreamingState,
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
) -> Result<ApplyResult, StateMachineError> {
    match state {
        StreamingState::PendingClosingOutput => apply_resume_closing_output(frame, outcome),
        StreamingState::PendingTerminal => apply_resume_terminal(frame, outcome),
        StreamingState::PendingAbortTerminal => apply_resume_abort_terminal(frame, outcome),
        _ => Err(StateMachineError::InvariantViolation {
            message: format!("RESUME_PENDING in non-pending state {:?}", state),
        }),
    }
}

/// Resume PENDING_CLOSING_OUTPUT.
fn apply_resume_closing_output(
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
) -> Result<ApplyResult, StateMachineError> {
    match outcome.ngx_result {
        NgxResult::Ok | NgxResult::Done => {
            /* Success → proceed to SEND_TERMINAL */
            let next = TransitionFrame {
                transition_id: frame.transition_id.clone(),
                step_id: format!("{}-RESUME-TERM", frame.transition_id),
                action: Action::SendTerminal,
                action_payload: ActionPayload::NULL,
                event: frame.event.clone(),
                reason: frame.reason.clone(),
                failure_ledger: frame.failure_ledger.clone(),
            };
            Ok(ApplyResult {
                new_state: StreamingState::PostCommitSafeFinish,
                next_frame: Some(next),
                side_effects: vec![],
                failure_updates: FailureUpdates::NONE,
            })
        }
        NgxResult::Again => Ok(ApplyResult {
            new_state: StreamingState::PendingClosingOutput,
            next_frame: None,
            side_effects: vec![],
            failure_updates: FailureUpdates::NONE,
        }),
        NgxResult::Error => {
            /* Output loss → ABORTED */
            let record = FailureRecord {
                stage: "streaming".to_string(),
                reason: frame.reason.clone(),
                error_origin: outcome.error_origin.unwrap_or(ErrorOrigin::Downstream),
                failure_site: Some(FailureSite::PendingResume),
            };
            let pre_effect = promote_or_delivery(&frame.failure_ledger, &record);
            let cmd_store = format!(
                "CMD_A{}-RCLOSE-STORE",
                frame.transition_id.replace("PLAN-", "")
            );
            let cmd_emit = format!(
                "CMD_A{}-RCLOSE-EMIT",
                frame.transition_id.replace("PLAN-", "")
            );
            Ok(ApplyResult {
                new_state: StreamingState::Aborted,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: format!(
                            "CMD_A{}-RCLOSE-LOSS",
                            frame.transition_id.replace("PLAN-", "")
                        ),
                        kind: SideEffectKind::SetSafeFinishOutputLoss,
                        execute_if: OwnerPredicate::Always,
                        payload: SideEffectPayload::None,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_store.clone(),
                        kind: SideEffectKind::StoreFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::UpdatedLedger,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_emit.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                ],
                failure_updates: FailureUpdates {
                    pre_effect,
                    post_effect: PostEffect {
                        set_ledger_stored_after: Some(cmd_store),
                        set_ledger_stored_if_nonempty_after: None,
                        set_ledger_emitted_if_unemitted_after: Some(cmd_emit),
                    },
                },
            })
        }
    }
}

/// Resume PENDING_TERMINAL.
fn apply_resume_terminal(
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
) -> Result<ApplyResult, StateMachineError> {
    match outcome.ngx_result {
        NgxResult::Ok | NgxResult::Done => {
            /* Success → DONE + latch + emit */
            let cmd_latch = format!(
                "CMD_A{}-RTERM-LATCH",
                frame.transition_id.replace("PLAN-", "")
            );
            let cmd_emit = format!(
                "CMD_A{}-RTERM-EMIT",
                frame.transition_id.replace("PLAN-", "")
            );
            Ok(ApplyResult {
                new_state: StreamingState::Done,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: cmd_latch,
                        kind: SideEffectKind::LatchTerminalSent,
                        execute_if: OwnerPredicate::Always,
                        payload: SideEffectPayload::None,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_emit.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                ],
                failure_updates: FailureUpdates {
                    pre_effect: PreEffect {
                        primary_update: None,
                        secondary_update: None,
                        delivery_update: None,
                    },
                    post_effect: PostEffect {
                        set_ledger_stored_after: None,
                        set_ledger_stored_if_nonempty_after: None,
                        set_ledger_emitted_if_unemitted_after: Some(cmd_emit),
                    },
                },
            })
        }
        NgxResult::Again => Ok(ApplyResult {
            new_state: StreamingState::PendingTerminal,
            next_frame: None,
            side_effects: vec![],
            failure_updates: FailureUpdates::NONE,
        }),
        NgxResult::Error => {
            /* Definitive error → ABORTED, no retry */
            let record = FailureRecord {
                stage: "streaming".to_string(),
                reason: frame.reason.clone(),
                error_origin: outcome.error_origin.unwrap_or(ErrorOrigin::Downstream),
                failure_site: Some(FailureSite::PendingResume),
            };
            let pre_effect = promote_or_delivery(&frame.failure_ledger, &record);
            let cmd_store = format!(
                "CMD_A{}-RTERM-STORE",
                frame.transition_id.replace("PLAN-", "")
            );
            let cmd_emit = format!(
                "CMD_A{}-RTERM-FAIL-EMIT",
                frame.transition_id.replace("PLAN-", "")
            );
            Ok(ApplyResult {
                new_state: StreamingState::Aborted,
                next_frame: None,
                side_effects: vec![
                    SideEffectCommand {
                        command_id: cmd_store.clone(),
                        kind: SideEffectKind::StoreFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::UpdatedLedger,
                        owner_transition: None,
                    },
                    SideEffectCommand {
                        command_id: cmd_emit.clone(),
                        kind: SideEffectKind::EmitFailureLedger,
                        execute_if: OwnerPredicate::OwnerIsStreaming,
                        payload: SideEffectPayload::EmitFailureLedger(EmitFailureLedgerPayload {
                            telemetry_scope: TelemetryScope::UnemittedSlots,
                            disposition: EmitFailureLedgerDisposition::Default,
                        }),
                        owner_transition: Some(OwnerTransition::StreamingToReleased),
                    },
                ],
                failure_updates: FailureUpdates {
                    pre_effect,
                    post_effect: PostEffect {
                        set_ledger_stored_after: Some(cmd_store),
                        set_ledger_stored_if_nonempty_after: None,
                        set_ledger_emitted_if_unemitted_after: Some(cmd_emit),
                    },
                },
            })
        }
    }
}

/// Resume PENDING_ABORT_TERMINAL.
fn apply_resume_abort_terminal(
    frame: &TransitionFrame,
    outcome: &ActionOutcome,
) -> Result<ApplyResult, StateMachineError> {
    /* Symmetric with SEND_ABORT_TERMINAL outcomes */
    apply_abort_terminal_with_prefix(
        StreamingState::PendingAbortTerminal,
        frame,
        outcome,
        "RATERM",
    )
}

/// Promote-or-delivery helper for failure record placement.
///
/// If primary is null → promote to primary.
/// If failure_site is closing_output/terminal_send/abort_terminal_send/pending_resume
///   → delivery.
/// Otherwise → secondary.
fn promote_or_delivery(ledger: &FailureLedger, record: &FailureRecord) -> PreEffect {
    if ledger.primary.is_none() {
        PreEffect {
            primary_update: Some(record.clone()),
            secondary_update: None,
            delivery_update: None,
        }
    } else {
        match record.failure_site {
            Some(
                FailureSite::ClosingOutput
                | FailureSite::TerminalSend
                | FailureSite::AbortTerminalSend
                | FailureSite::PendingResume,
            ) => PreEffect {
                primary_update: None,
                secondary_update: None,
                delivery_update: Some(record.clone()),
            },
            _ => PreEffect {
                primary_update: None,
                secondary_update: Some(record.clone()),
                delivery_update: None,
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::super::types::{EventEnvelope, PendingKind};
    use super::*;

    fn simple_frame(action: Action, transition_id: &str) -> TransitionFrame {
        TransitionFrame {
            transition_id: transition_id.to_string(),
            step_id: transition_id.to_string(),
            action,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::UpstreamEnd,
                failure_record: None,
            },
            reason: "test".to_string(),
            failure_ledger: FailureLedger::empty(),
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

    fn again_outcome() -> ActionOutcome {
        ActionOutcome {
            ngx_result: NgxResult::Again,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: false,
            pending_kind: Some(PendingKind::Terminal),
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

    #[test]
    fn none_eligible_transitions_to_streaming_candidate() {
        let frame = TransitionFrame {
            transition_id: "PLAN-01".to_string(),
            step_id: "PLAN-01".to_string(),
            action: Action::None,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::Eligible,
                failure_record: None,
            },
            reason: "eligible".to_string(),
            failure_ledger: FailureLedger::empty(),
        };
        let result = apply_result(
            StreamingState::NotEligible,
            &frame,
            &ok_outcome(),
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::StreamingCandidate);
        assert!(result.next_frame.is_none());
        assert!(result.side_effects.is_empty());
    }

    #[test]
    fn send_terminal_ok_done_with_latch() {
        let frame = simple_frame(Action::SendTerminal, "PLAN-21");
        let result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &ok_outcome(),
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Done);
        assert!(result.next_frame.is_none());
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    #[test]
    fn send_terminal_again_no_latch() {
        let frame = simple_frame(Action::SendTerminal, "PLAN-21");
        let result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &again_outcome(),
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PendingTerminal);
        assert!(result.side_effects.is_empty());
    }

    #[test]
    fn send_terminal_error_aborted() {
        let frame = simple_frame(Action::SendTerminal, "PLAN-21");
        let result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &error_outcome(FailureSite::TerminalSend),
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::SetSafeFinishTerminalSendFailed)
        );
    }

    #[test]
    fn finalize_converter_ok_with_closing_bytes() {
        let frame = simple_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ActionOutcome {
            ngx_result: NgxResult::Ok,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: true,
            pending_kind: None,
        };
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &outcome,
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PostCommitSafeFinish);
        let next = result.next_frame.unwrap();
        assert_eq!(next.action, Action::SendClosingOutput);
    }

    #[test]
    fn finalize_converter_ok_without_closing_bytes() {
        let frame = simple_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ActionOutcome {
            ngx_result: NgxResult::Ok,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: false,
            pending_kind: None,
        };
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &outcome,
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PostCommitSafeFinish);
        let next = result.next_frame.unwrap();
        assert_eq!(next.action, Action::SendTerminal);
    }

    #[test]
    fn finalize_converter_error_downstream_usable() {
        let frame = simple_frame(Action::FinalizeConverter, "PLAN-21");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PostCommitAbort);
        let next = result.next_frame.unwrap();
        assert_eq!(next.action, Action::BeginAbort);
    }

    #[test]
    fn finalize_converter_error_downstream_unusable() {
        let frame = simple_frame(Action::FinalizeConverter, "PLAN-21");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &TransitionContext {
                downstream_usable: false,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
    }

    #[test]
    fn client_abort_in_committed() {
        let frame = TransitionFrame {
            transition_id: "PLAN-22".to_string(),
            step_id: "PLAN-22".to_string(),
            action: Action::None,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::ClientAbort,
                failure_record: None,
            },
            reason: "client_abort".to_string(),
            failure_ledger: FailureLedger::empty(),
        };
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &ok_outcome(),
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::EmitFailureLedger)
        );
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::ClearInflightAndPending)
        );
    }

    #[test]
    fn cleanup_full_buffer_fallback_no_emit() {
        let frame = TransitionFrame {
            transition_id: "PLAN-38".to_string(),
            step_id: "PLAN-38".to_string(),
            action: Action::None,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::Cleanup,
                failure_record: None,
            },
            reason: "cleanup".to_string(),
            failure_ledger: FailureLedger::empty(),
        };
        let result = apply_result(
            StreamingState::FullBufferFallback,
            &frame,
            &ok_outcome(),
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::FullBufferFallback);
        /* Only clear, no emit */
        assert_eq!(result.side_effects.len(), 1);
        assert_eq!(
            result.side_effects[0].kind,
            SideEffectKind::ClearInflightAndPending
        );
    }

    #[test]
    fn cleanup_committed_emits_and_clears() {
        let frame = TransitionFrame {
            transition_id: "PLAN-40".to_string(),
            step_id: "PLAN-40".to_string(),
            action: Action::None,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::Cleanup,
                failure_record: None,
            },
            reason: "cleanup".to_string(),
            failure_ledger: FailureLedger::empty(),
        };
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &ok_outcome(),
            &TransitionContext {
                downstream_usable: true,
            },
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Committed);
        assert_eq!(result.side_effects.len(), 2);
        assert_eq!(
            result.side_effects[0].kind,
            SideEffectKind::EmitFailureLedger
        );
        assert_eq!(
            result.side_effects[1].kind,
            SideEffectKind::ClearInflightAndPending
        );
    }

    #[test]
    fn body_filter_reentry_terminal_idempotent() {
        for state in [
            StreamingState::Done,
            StreamingState::Aborted,
            StreamingState::FailedClosed,
        ] {
            let frame = TransitionFrame {
                transition_id: "PLAN-32".to_string(),
                step_id: "PLAN-32".to_string(),
                action: Action::None,
                action_payload: ActionPayload::NULL,
                event: EventEnvelope {
                    kind: EventKind::BodyFilterReentry,
                    failure_record: None,
                },
                reason: "reentry".to_string(),
                failure_ledger: FailureLedger::empty(),
            };
            let result = apply_result(
                state,
                &frame,
                &ok_outcome(),
                &TransitionContext {
                    downstream_usable: true,
                },
            )
            .unwrap();
            assert_eq!(result.new_state, state);
            assert!(result.side_effects.is_empty());
        }
    }

    #[test]
    fn resume_abort_terminal_uses_distinct_command_namespace() {
        let frame = simple_frame(Action::ResumePending, "PLAN-30");

        for outcome in [ok_outcome(), error_outcome(FailureSite::PendingResume)] {
            let result = apply_result(
                StreamingState::PendingAbortTerminal,
                &frame,
                &outcome,
                &TransitionContext {
                    downstream_usable: true,
                },
            )
            .unwrap();

            assert!(
                result
                    .side_effects
                    .iter()
                    .all(|command| command.command_id.contains("-RATERM-")
                        && !command.command_id.contains("-ATERM-"))
            );
        }
    }
}
