//! Post-commit delivery matrix tests for the streaming lifecycle state machine.
//!
//! Covers Groups 1-7, FINALIZE_CONVERTER failure paths (PLAN-20 vs PLAN-31),
//! CLIENT_ABORT contract (unified across 6 committed/pending states), CLEANUP
//! contract (all 15 states), and hard invariants.
//!
//! _Requirements: 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 11.10, 11.11, 11.12,
//! 11.13, 11.14, 11.16, 11.18_

#[cfg(feature = "streaming")]
mod delivery_matrix {
    use nginx_markdown_converter::streaming_lifecycle::*;

    // --- Test helpers ---

    fn make_frame(action: Action, transition_id: &str) -> TransitionFrame {
        TransitionFrame {
            transition_id: transition_id.to_string(),
            step_id: transition_id.to_string(),
            action,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::UpstreamEnd,
                failure_record: None,
            },
            reason: "test_reason".to_string(),
            failure_ledger: FailureLedger::empty(),
        }
    }

    fn make_frame_with_ledger(
        action: Action,
        transition_id: &str,
        ledger: FailureLedger,
    ) -> TransitionFrame {
        TransitionFrame {
            transition_id: transition_id.to_string(),
            step_id: transition_id.to_string(),
            action,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::UpstreamEnd,
                failure_record: None,
            },
            reason: "test_reason".to_string(),
            failure_ledger: ledger,
        }
    }

    fn make_error_frame(transition_id: &str) -> TransitionFrame {
        TransitionFrame {
            transition_id: transition_id.to_string(),
            step_id: transition_id.to_string(),
            action: Action::FinalizeConverter,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::Error,
                failure_record: Some(FailureRecord {
                    stage: "streaming".to_string(),
                    reason: "conversion_error".to_string(),
                    error_origin: ErrorOrigin::Internal,
                    failure_site: None,
                }),
            },
            reason: "conversion_error".to_string(),
            failure_ledger: FailureLedger {
                primary: Some(FailureRecord {
                    stage: "streaming".to_string(),
                    reason: "conversion_error".to_string(),
                    error_origin: ErrorOrigin::Internal,
                    failure_site: None,
                }),
                secondary: None,
                delivery: None,
                ledger_stored: false,
                ledger_emitted: false,
            },
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

    fn ledger_with_primary() -> FailureLedger {
        FailureLedger {
            primary: Some(FailureRecord {
                stage: "streaming".to_string(),
                reason: "original_error".to_string(),
                error_origin: ErrorOrigin::Internal,
                failure_site: None,
            }),
            secondary: None,
            delivery: None,
            ledger_stored: false,
            ledger_emitted: false,
        }
    }

    // ===================================================================
    // GROUP 1: FINALIZE_CONVERTER success, zero closing bytes → SEND_TERMINAL
    // ===================================================================

    #[test]
    fn group1_finalize_ok_no_closing_then_terminal_ok() {
        // FINALIZE_CONVERTER success, no closing bytes → next_frame=SEND_TERMINAL
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ok_outcome(); // produced_closing_bytes = false
        let result =
            apply_result(StreamingState::Committed, &frame, &outcome, &ctx_usable()).unwrap();
        assert_eq!(result.new_state, StreamingState::PostCommitSafeFinish);
        let next = result.next_frame.unwrap();
        assert_eq!(next.action, Action::SendTerminal);

        // Now apply the terminal send with NGX_OK
        let term_result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &next,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(term_result.new_state, StreamingState::Done);
        assert!(term_result.next_frame.is_none());
        // Verify latch_terminal_sent
        assert!(
            term_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
        // Verify EMIT_FAILURE_LEDGER
        assert!(
            term_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::EmitFailureLedger)
        );
    }

    #[test]
    fn group1_finalize_ok_no_closing_then_terminal_done() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ok_outcome();
        let result =
            apply_result(StreamingState::Committed, &frame, &outcome, &ctx_usable()).unwrap();
        let next = result.next_frame.unwrap();

        // Terminal send returns NGX_DONE
        let term_result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &next,
            &done_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(term_result.new_state, StreamingState::Done);
        assert!(
            term_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    #[test]
    fn group1_finalize_ok_no_closing_then_terminal_again() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ok_outcome();
        let result =
            apply_result(StreamingState::Committed, &frame, &outcome, &ctx_usable()).unwrap();
        let next = result.next_frame.unwrap();

        // Terminal send returns NGX_AGAIN
        let term_result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &next,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(term_result.new_state, StreamingState::PendingTerminal);
        assert!(term_result.next_frame.is_none());
        // No latch on NGX_AGAIN
        assert!(
            !term_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    #[test]
    fn group1_finalize_ok_no_closing_then_terminal_error() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ok_outcome();
        let result =
            apply_result(StreamingState::Committed, &frame, &outcome, &ctx_usable()).unwrap();
        let next = result.next_frame.unwrap();

        // Terminal send returns definitive NGX_ERROR
        let term_result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &next,
            &error_outcome(FailureSite::TerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(term_result.new_state, StreamingState::Aborted);
        assert!(term_result.next_frame.is_none());
        // SetSafeFinishTerminalSendFailed side effect
        assert!(
            term_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::SetSafeFinishTerminalSendFailed)
        );
        // No retry via abort path
        assert!(
            !term_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::RecordPostcommitAbort)
        );
    }

    // ===================================================================
    // GROUP 2: FINALIZE_CONVERTER success, closing bytes → SEND_CLOSING_OUTPUT
    // ===================================================================

    #[test]
    fn group2_finalize_ok_with_closing_bytes_then_close_ok() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ActionOutcome {
            ngx_result: NgxResult::Ok,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: true,
            pending_kind: None,
        };
        let result =
            apply_result(StreamingState::Committed, &frame, &outcome, &ctx_usable()).unwrap();
        assert_eq!(result.new_state, StreamingState::PostCommitSafeFinish);
        let next = result.next_frame.unwrap();
        assert_eq!(next.action, Action::SendClosingOutput);

        // Closing output send OK → next_frame=SEND_TERMINAL
        let close_result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &next,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(close_result.new_state, StreamingState::PostCommitSafeFinish);
        let term_frame = close_result.next_frame.unwrap();
        assert_eq!(term_frame.action, Action::SendTerminal);
    }

    #[test]
    fn group2_finalize_ok_with_closing_bytes_then_close_again() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ActionOutcome {
            ngx_result: NgxResult::Ok,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: true,
            pending_kind: None,
        };
        let result =
            apply_result(StreamingState::Committed, &frame, &outcome, &ctx_usable()).unwrap();
        let next = result.next_frame.unwrap();

        // Closing output send NGX_AGAIN → PENDING_CLOSING_OUTPUT
        let close_result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &next,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(close_result.new_state, StreamingState::PendingClosingOutput);
        assert!(close_result.next_frame.is_none());
    }

    #[test]
    fn group2_finalize_ok_with_closing_bytes_then_close_error() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ActionOutcome {
            ngx_result: NgxResult::Ok,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: true,
            pending_kind: None,
        };
        let result =
            apply_result(StreamingState::Committed, &frame, &outcome, &ctx_usable()).unwrap();
        let next = result.next_frame.unwrap();

        // Closing output send definitive NGX_ERROR → output-loss abort
        let close_result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &next,
            &error_outcome(FailureSite::ClosingOutput),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(close_result.new_state, StreamingState::Aborted);
        assert!(close_result.next_frame.is_none());
        assert!(
            close_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::SetSafeFinishOutputLoss)
        );
        assert!(
            close_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::StoreFailureLedger)
        );
        assert!(
            close_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::EmitFailureLedger)
        );
    }

    // ===================================================================
    // GROUP 3: After SEND_CLOSING_OUTPUT success → SEND_TERMINAL
    // ===================================================================

    #[test]
    fn group3_after_close_success_terminal_ok() {
        // Simulate: after SEND_CLOSING_OUTPUT returns OK → SEND_TERMINAL
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let finalize_outcome = ActionOutcome {
            ngx_result: NgxResult::Ok,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: true,
            pending_kind: None,
        };
        let r1 = apply_result(
            StreamingState::Committed,
            &frame,
            &finalize_outcome,
            &ctx_usable(),
        )
        .unwrap();
        let close_frame = r1.next_frame.unwrap();

        // Closing send OK
        let r2 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &close_frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        let term_frame = r2.next_frame.unwrap();
        assert_eq!(term_frame.action, Action::SendTerminal);

        // Terminal send OK
        let r3 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &term_frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r3.new_state, StreamingState::Done);
        assert!(
            r3.side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    #[test]
    fn group3_after_close_success_terminal_again() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let finalize_outcome = ActionOutcome {
            ngx_result: NgxResult::Ok,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: true,
            pending_kind: None,
        };
        let r1 = apply_result(
            StreamingState::Committed,
            &frame,
            &finalize_outcome,
            &ctx_usable(),
        )
        .unwrap();
        let close_frame = r1.next_frame.unwrap();
        let r2 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &close_frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        let term_frame = r2.next_frame.unwrap();

        // Terminal send NGX_AGAIN
        let r3 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &term_frame,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r3.new_state, StreamingState::PendingTerminal);
        assert!(r3.next_frame.is_none());
        assert!(
            !r3.side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    #[test]
    fn group3_after_close_success_terminal_error() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let finalize_outcome = ActionOutcome {
            ngx_result: NgxResult::Ok,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: true,
            pending_kind: None,
        };
        let r1 = apply_result(
            StreamingState::Committed,
            &frame,
            &finalize_outcome,
            &ctx_usable(),
        )
        .unwrap();
        let close_frame = r1.next_frame.unwrap();
        let r2 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &close_frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        let term_frame = r2.next_frame.unwrap();

        // Terminal send definitive NGX_ERROR
        let r3 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &term_frame,
            &error_outcome(FailureSite::TerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r3.new_state, StreamingState::Aborted);
        assert!(
            r3.side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::SetSafeFinishTerminalSendFailed)
        );
    }

    // ===================================================================
    // GROUP 4: PENDING_CLOSING_OUTPUT → RESUME_PENDING
    // ===================================================================

    #[test]
    fn group4_resume_pending_closing_ok() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::PendingClosingOutput,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PostCommitSafeFinish);
        let next = result.next_frame.unwrap();
        assert_eq!(next.action, Action::SendTerminal);
    }

    #[test]
    fn group4_resume_pending_closing_again() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::PendingClosingOutput,
            &frame,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PendingClosingOutput);
        assert!(result.next_frame.is_none());
    }

    #[test]
    fn group4_resume_pending_closing_error() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::PendingClosingOutput,
            &frame,
            &error_outcome(FailureSite::PendingResume),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::SetSafeFinishOutputLoss)
        );
    }

    // ===================================================================
    // GROUP 5: PENDING_TERMINAL → RESUME_PENDING
    // ===================================================================

    #[test]
    fn group5_resume_pending_terminal_ok() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::PendingTerminal,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
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
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::EmitFailureLedger)
        );
    }

    #[test]
    fn group5_resume_pending_terminal_again() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::PendingTerminal,
            &frame,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PendingTerminal);
        assert!(result.next_frame.is_none());
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    #[test]
    fn group5_resume_pending_terminal_error() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::PendingTerminal,
            &frame,
            &error_outcome(FailureSite::PendingResume),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        // No retry - direct abort
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    // ===================================================================
    // GROUP 6: BEGIN_ABORT → SEND_ABORT_TERMINAL
    // ===================================================================

    #[test]
    fn group6_begin_abort_ok_then_send_abort_terminal_ok() {
        let frame = make_frame(Action::BeginAbort, "PLAN-20");
        let result = apply_result(
            StreamingState::PostCommitAbort,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PostCommitAbort);
        let next = result.next_frame.unwrap();
        assert_eq!(next.action, Action::SendAbortTerminal);
        // RecordPostcommitAbort side effect
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::RecordPostcommitAbort)
        );

        // Send abort terminal OK → DONE + latch
        let abort_result = apply_result(
            StreamingState::PostCommitAbort,
            &next,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(abort_result.new_state, StreamingState::Done);
        assert!(abort_result.next_frame.is_none());
        assert!(
            abort_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
        assert!(
            abort_result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::EmitFailureLedger)
        );
    }

    #[test]
    fn group6_send_abort_terminal_again() {
        let frame = make_frame(Action::SendAbortTerminal, "PLAN-20");
        let result = apply_result(
            StreamingState::PostCommitAbort,
            &frame,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PendingAbortTerminal);
        assert!(result.next_frame.is_none());
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    #[test]
    fn group6_send_abort_terminal_error() {
        let frame = make_frame(Action::SendAbortTerminal, "PLAN-20");
        let result = apply_result(
            StreamingState::PostCommitAbort,
            &frame,
            &error_outcome(FailureSite::AbortTerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        // No retry
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
        // Store and emit
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::StoreFailureLedger)
        );
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::EmitFailureLedger)
        );
    }

    // ===================================================================
    // GROUP 7: PENDING_ABORT_TERMINAL → RESUME_PENDING
    // ===================================================================

    #[test]
    fn group7_resume_pending_abort_terminal_ok() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::PendingAbortTerminal,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
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
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::EmitFailureLedger)
        );
    }

    #[test]
    fn group7_resume_pending_abort_terminal_again() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::PendingAbortTerminal,
            &frame,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PendingAbortTerminal);
        assert!(result.next_frame.is_none());
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    #[test]
    fn group7_resume_pending_abort_terminal_error() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::PendingAbortTerminal,
            &frame,
            &error_outcome(FailureSite::AbortTerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        // No retry
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::StoreFailureLedger)
        );
    }

    // ===================================================================
    // FINALIZE_CONVERTER FAILURE PATH: PLAN-20 vs PLAN-31 distinction
    // ===================================================================

    #[test]
    fn finalize_failure_plan20_updates_secondary_when_primary_exists() {
        // PLAN-20: ERROR event → primary already exists from the event
        // Finalize failure updates secondary
        let frame = make_error_frame("PLAN-20");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PostCommitAbort);
        // Pre-effect updates secondary (primary already exists)
        assert!(result.failure_updates.pre_effect.secondary_update.is_some());
        assert!(result.failure_updates.pre_effect.primary_update.is_none());
        // Next frame is BEGIN_ABORT
        let next = result.next_frame.unwrap();
        assert_eq!(next.action, Action::BeginAbort);
    }

    #[test]
    fn finalize_failure_plan31_promotes_primary_when_no_primary() {
        // PLAN-31: UPSTREAM_END event → no prior failure, primary is null
        // Finalize failure promotes to primary
        let frame = make_frame(Action::FinalizeConverter, "PLAN-31");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::PostCommitAbort);
        // Pre-effect promotes to primary (no existing primary)
        assert!(result.failure_updates.pre_effect.primary_update.is_some());
        assert!(result.failure_updates.pre_effect.secondary_update.is_none());
        // Next frame is BEGIN_ABORT
        let next = result.next_frame.unwrap();
        assert_eq!(next.action, Action::BeginAbort);
    }

    #[test]
    fn finalize_failure_downstream_usable_stores_ledger() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-31");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &ctx_usable(),
        )
        .unwrap();
        // Store ledger side effect
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::StoreFailureLedger)
        );
        // Post-effect latch names the store command
        assert!(
            result
                .failure_updates
                .post_effect
                .set_ledger_stored_after
                .is_some()
        );
    }

    #[test]
    fn finalize_failure_downstream_unusable_direct_aborted() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-31");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &ctx_unusable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        // Both store and emit
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::StoreFailureLedger)
        );
        assert!(
            result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::EmitFailureLedger)
        );
    }

    #[test]
    fn finalize_failure_plan20_downstream_unusable_direct_aborted() {
        let frame = make_error_frame("PLAN-20");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &ctx_unusable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        // Secondary update (primary exists)
        assert!(result.failure_updates.pre_effect.secondary_update.is_some());
        assert!(result.failure_updates.pre_effect.primary_update.is_none());
    }

    #[test]
    fn finalize_converter_again_is_invariant_violation() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ActionOutcome {
            ngx_result: NgxResult::Again,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: false,
            pending_kind: None,
        };
        let result = apply_result(StreamingState::Committed, &frame, &outcome, &ctx_usable());
        assert!(result.is_err());
        match result.unwrap_err() {
            StateMachineError::InvariantViolation { message } => {
                assert!(message.contains("FINALIZE_CONVERTER"));
            }
            other => panic!("unexpected error: {:?}", other),
        }
    }

    // ===================================================================
    // CLIENT_ABORT CONTRACT: Unified across 6 committed/pending states
    // ===================================================================

    fn client_abort_frame(plan_id: &str) -> TransitionFrame {
        TransitionFrame {
            transition_id: plan_id.to_string(),
            step_id: plan_id.to_string(),
            action: Action::None,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::ClientAbort,
                failure_record: None,
            },
            reason: "client_abort".to_string(),
            failure_ledger: FailureLedger::empty(),
        }
    }

    #[test]
    fn client_abort_committed_plan22() {
        let frame = client_abort_frame("PLAN-22");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        // Emit + clear, no FailureRecord, no latch
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
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
        // No FailureRecord created
        assert_eq!(result.failure_updates, FailureUpdates::NONE);
    }

    #[test]
    fn client_abort_post_commit_safe_finish_plan23() {
        let frame = client_abort_frame("PLAN-23");
        let result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        verify_client_abort_side_effects(&result);
    }

    #[test]
    fn client_abort_post_commit_abort_plan24() {
        let frame = client_abort_frame("PLAN-24");
        let result = apply_result(
            StreamingState::PostCommitAbort,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        verify_client_abort_side_effects(&result);
    }

    #[test]
    fn client_abort_pending_closing_output_plan25() {
        let frame = client_abort_frame("PLAN-25");
        let result = apply_result(
            StreamingState::PendingClosingOutput,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        verify_client_abort_side_effects(&result);
    }

    #[test]
    fn client_abort_pending_terminal_plan26() {
        let frame = client_abort_frame("PLAN-26");
        let result = apply_result(
            StreamingState::PendingTerminal,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        verify_client_abort_side_effects(&result);
    }

    #[test]
    fn client_abort_pending_abort_terminal_plan27() {
        let frame = client_abort_frame("PLAN-27");
        let result = apply_result(
            StreamingState::PendingAbortTerminal,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        verify_client_abort_side_effects(&result);
    }

    /// Verify common CLIENT_ABORT side effect contract.
    fn verify_client_abort_side_effects(result: &ApplyResult) {
        // Emit + clear
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
        // Never latch terminal
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
        // Never BEGIN_ABORT
        assert!(result.next_frame.is_none());
        // No FailureRecord creation
        assert_eq!(result.failure_updates, FailureUpdates::NONE);
    }

    // ===================================================================
    // CLEANUP CONTRACT: All 15 states
    // ===================================================================

    fn cleanup_frame(plan_id: &str) -> TransitionFrame {
        TransitionFrame {
            transition_id: plan_id.to_string(),
            step_id: plan_id.to_string(),
            action: Action::None,
            action_payload: ActionPayload::NULL,
            event: EventEnvelope {
                kind: EventKind::Cleanup,
                failure_record: None,
            },
            reason: "cleanup".to_string(),
            failure_ledger: FailureLedger::empty(),
        }
    }

    /// All non-FULL_BUFFER_FALLBACK states: emit + clear, state preserved
    #[test]
    fn cleanup_all_non_full_buffer_states_emit_and_clear() {
        let states = [
            (StreamingState::NotEligible, "PLAN-34"),
            (StreamingState::StreamingCandidate, "PLAN-35"),
            (StreamingState::PreCommit, "PLAN-36"),
            (StreamingState::PreCommitReplayUnavailable, "PLAN-37"),
            (StreamingState::Passthrough, "PLAN-39"),
            (StreamingState::Committed, "PLAN-40"),
            (StreamingState::PostCommitSafeFinish, "PLAN-41"),
            (StreamingState::PostCommitAbort, "PLAN-42"),
            (StreamingState::PendingClosingOutput, "PLAN-43"),
            (StreamingState::PendingTerminal, "PLAN-44"),
            (StreamingState::PendingAbortTerminal, "PLAN-45"),
            (StreamingState::Done, "PLAN-46"),
            (StreamingState::Aborted, "PLAN-47"),
            (StreamingState::FailedClosed, "PLAN-48"),
        ];

        for (state, plan_id) in states {
            let frame = cleanup_frame(plan_id);
            let result = apply_result(state, &frame, &ok_outcome(), &ctx_usable()).unwrap();
            // State preserved
            assert_eq!(
                result.new_state, state,
                "cleanup should preserve state {:?}",
                state
            );
            assert!(result.next_frame.is_none());
            // Has both emit and clear
            assert!(
                result
                    .side_effects
                    .iter()
                    .any(|c| c.kind == SideEffectKind::EmitFailureLedger),
                "cleanup in {:?} must emit",
                state
            );
            assert!(
                result
                    .side_effects
                    .iter()
                    .any(|c| c.kind == SideEffectKind::ClearInflightAndPending),
                "cleanup in {:?} must clear",
                state
            );
            // Emit has OwnerIsStreaming predicate
            let emit_cmd = result
                .side_effects
                .iter()
                .find(|c| c.kind == SideEffectKind::EmitFailureLedger)
                .unwrap();
            assert_eq!(emit_cmd.execute_if, OwnerPredicate::OwnerIsStreaming);
            assert_eq!(
                emit_cmd.owner_transition,
                Some(OwnerTransition::StreamingToReleased)
            );
            // No latch, no downstream send, no BEGIN_ABORT
            assert!(
                !result
                    .side_effects
                    .iter()
                    .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
            );
        }
    }

    /// FULL_BUFFER_FALLBACK: clear only, no emit
    #[test]
    fn cleanup_full_buffer_fallback_clear_only() {
        let frame = cleanup_frame("PLAN-38");
        let result = apply_result(
            StreamingState::FullBufferFallback,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(result.new_state, StreamingState::FullBufferFallback);
        assert!(result.next_frame.is_none());
        // Only clear, NOT emit
        assert_eq!(result.side_effects.len(), 1);
        assert_eq!(
            result.side_effects[0].kind,
            SideEffectKind::ClearInflightAndPending
        );
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::EmitFailureLedger)
        );
    }

    // ===================================================================
    // HARD INVARIANTS
    // ===================================================================

    /// Invariant: terminal send failure never retried through abort path
    #[test]
    fn invariant_terminal_send_failure_never_retried() {
        let frame = make_frame(Action::SendTerminal, "PLAN-21");
        let result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &error_outcome(FailureSite::TerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        // Goes directly to ABORTED, not to PostCommitAbort/BeginAbort
        assert_eq!(result.new_state, StreamingState::Aborted);
        assert!(result.next_frame.is_none());
        // Verify no BEGIN_ABORT in the chain
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::RecordPostcommitAbort)
        );
    }

    /// Invariant: terminal-sent latch only after NGX_OK/NGX_DONE
    #[test]
    fn invariant_latch_only_after_ok_or_done() {
        // NGX_AGAIN: no latch
        let frame = make_frame(Action::SendTerminal, "PLAN-21");
        let result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert!(
            !result
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );

        // NGX_ERROR: no latch
        let result2 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &error_outcome(FailureSite::TerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        assert!(
            !result2
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );

        // NGX_OK: HAS latch
        let result3 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert!(
            result3
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    /// Invariant: NGX_AGAIN transitions to pending states, never plain COMMITTED
    #[test]
    fn invariant_again_transitions_to_pending() {
        // SendTerminal + AGAIN → PendingTerminal
        let frame = make_frame(Action::SendTerminal, "PLAN-21");
        let r = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r.new_state, StreamingState::PendingTerminal);

        // SendClosingOutput + AGAIN → PendingClosingOutput
        let close_frame = make_frame(Action::SendClosingOutput, "PLAN-21");
        let r2 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &close_frame,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r2.new_state, StreamingState::PendingClosingOutput);

        // SendAbortTerminal + AGAIN → PendingAbortTerminal
        let abort_frame = make_frame(Action::SendAbortTerminal, "PLAN-20");
        let r3 = apply_result(
            StreamingState::PostCommitAbort,
            &abort_frame,
            &again_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r3.new_state, StreamingState::PendingAbortTerminal);
    }

    /// Invariant: safe-finish Rust-side failure (converter_finalize) distinct
    /// from terminal downstream send failure (terminal_send)
    #[test]
    fn invariant_finalize_vs_terminal_send_distinct_paths() {
        // Converter finalize failure → PostCommitAbort + BeginAbort chain
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let r_finalize = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r_finalize.new_state, StreamingState::PostCommitAbort);
        assert!(r_finalize.next_frame.is_some());
        assert_eq!(
            r_finalize.next_frame.as_ref().unwrap().action,
            Action::BeginAbort
        );

        // Terminal send failure → direct ABORTED, no chain
        let term_frame = make_frame(Action::SendTerminal, "PLAN-21");
        let r_terminal = apply_result(
            StreamingState::PostCommitSafeFinish,
            &term_frame,
            &error_outcome(FailureSite::TerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r_terminal.new_state, StreamingState::Aborted);
        assert!(r_terminal.next_frame.is_none());
    }

    /// Invariant: closing-output loss distinct from terminal-only send failure
    #[test]
    fn invariant_closing_output_loss_vs_terminal_send() {
        // Closing output error → SetSafeFinishOutputLoss
        let close_frame = make_frame(Action::SendClosingOutput, "PLAN-21");
        let r_close = apply_result(
            StreamingState::PostCommitSafeFinish,
            &close_frame,
            &error_outcome(FailureSite::ClosingOutput),
            &ctx_usable(),
        )
        .unwrap();
        assert!(
            r_close
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::SetSafeFinishOutputLoss)
        );
        assert!(
            !r_close
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::SetSafeFinishTerminalSendFailed)
        );

        // Terminal send error → SetSafeFinishTerminalSendFailed
        let term_frame = make_frame(Action::SendTerminal, "PLAN-21");
        let r_term = apply_result(
            StreamingState::PostCommitSafeFinish,
            &term_frame,
            &error_outcome(FailureSite::TerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        assert!(
            r_term
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::SetSafeFinishTerminalSendFailed)
        );
        assert!(
            !r_term
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::SetSafeFinishOutputLoss)
        );
    }

    /// Invariant: FailureLedger promote-or-delivery rules
    #[test]
    fn invariant_ledger_promotion_when_primary_null() {
        // Terminal send error with empty ledger → promotes to primary
        let frame = make_frame(Action::SendTerminal, "PLAN-31");
        let result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &error_outcome(FailureSite::TerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        // Primary is promoted (was null)
        assert!(result.failure_updates.pre_effect.primary_update.is_some());
        assert!(result.failure_updates.pre_effect.delivery_update.is_none());
    }

    #[test]
    fn invariant_ledger_delivery_when_primary_exists() {
        // Terminal send error with existing primary → goes to delivery
        let ledger = ledger_with_primary();
        let frame = make_frame_with_ledger(Action::SendTerminal, "PLAN-20", ledger);
        let result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &error_outcome(FailureSite::TerminalSend),
            &ctx_usable(),
        )
        .unwrap();
        // Delivery is updated (primary exists, failure_site is terminal_send)
        assert!(result.failure_updates.pre_effect.delivery_update.is_some());
        assert!(result.failure_updates.pre_effect.primary_update.is_none());
    }

    #[test]
    fn invariant_ledger_secondary_for_finalize_when_primary_exists() {
        // FINALIZE_CONVERTER error with existing primary → secondary
        let ledger = ledger_with_primary();
        let frame = make_frame_with_ledger(Action::FinalizeConverter, "PLAN-20", ledger);
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &ctx_usable(),
        )
        .unwrap();
        assert!(result.failure_updates.pre_effect.secondary_update.is_some());
        assert!(result.failure_updates.pre_effect.primary_update.is_none());
        assert!(result.failure_updates.pre_effect.delivery_update.is_none());
    }

    /// Invariant: body-filter re-entry after terminal is idempotent
    #[test]
    fn invariant_body_filter_reentry_idempotent() {
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
            let result = apply_result(state, &frame, &ok_outcome(), &ctx_usable()).unwrap();
            assert_eq!(result.new_state, state);
            assert!(result.next_frame.is_none());
            assert!(result.side_effects.is_empty());
            assert_eq!(result.failure_updates, FailureUpdates::NONE);
        }
    }

    /// Invariant: no post-commit state modifies status/headers or restores HTML
    /// (verified structurally: no side effects that modify headers exist)
    #[test]
    fn invariant_no_header_modification_in_post_commit() {
        // All post-commit actions produce only state machine side effects,
        // never header-modifying actions. Verify by checking that terminal
        // send actions only produce lifecycle side effects.
        let frame = make_frame(Action::SendTerminal, "PLAN-21");
        let result = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        // Side effects are only: LatchTerminalSent, EmitFailureLedger
        for cmd in &result.side_effects {
            assert!(matches!(
                cmd.kind,
                SideEffectKind::LatchTerminalSent
                    | SideEffectKind::EmitFailureLedger
                    | SideEffectKind::ClearInflightAndPending
                    | SideEffectKind::SetSafeFinishOutputLoss
                    | SideEffectKind::SetSafeFinishTerminalSendFailed
                    | SideEffectKind::StoreFailureLedger
                    | SideEffectKind::RecordPostcommitAbort
                    | SideEffectKind::TransferFailureToFullBuffer
            ));
        }
    }

    /// Invariant: cleanup at terminal returns formal inflight to zero
    #[test]
    fn invariant_cleanup_clears_inflight_and_pending() {
        for state in [
            StreamingState::Done,
            StreamingState::Aborted,
            StreamingState::FailedClosed,
            StreamingState::Committed,
            StreamingState::PendingTerminal,
            StreamingState::PendingClosingOutput,
            StreamingState::PendingAbortTerminal,
        ] {
            let frame = cleanup_frame("PLAN-40");
            let result = apply_result(state, &frame, &ok_outcome(), &ctx_usable()).unwrap();
            assert!(
                result
                    .side_effects
                    .iter()
                    .any(|c| c.kind == SideEffectKind::ClearInflightAndPending),
                "cleanup in {:?} must clear inflight",
                state
            );
        }
    }

    /// Invariant: CLIENT_ABORT emit predicate is OwnerIsStreaming
    #[test]
    fn invariant_client_abort_emit_owner_predicate() {
        let frame = client_abort_frame("PLAN-22");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        let emit = result
            .side_effects
            .iter()
            .find(|c| c.kind == SideEffectKind::EmitFailureLedger)
            .unwrap();
        assert_eq!(emit.execute_if, OwnerPredicate::OwnerIsStreaming);
        assert_eq!(
            emit.owner_transition,
            Some(OwnerTransition::StreamingToReleased)
        );
    }

    /// Invariant: emit payload is always UNEMITTED_SLOTS with DEFAULT disposition
    /// for CLIENT_ABORT and CLEANUP
    #[test]
    fn invariant_emit_payload_unemitted_slots() {
        // CLIENT_ABORT
        let frame = client_abort_frame("PLAN-22");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        let emit = result
            .side_effects
            .iter()
            .find(|c| c.kind == SideEffectKind::EmitFailureLedger)
            .unwrap();
        match &emit.payload {
            SideEffectPayload::EmitFailureLedger(payload) => {
                assert_eq!(payload.telemetry_scope, TelemetryScope::UnemittedSlots);
                assert_eq!(payload.disposition, EmitFailureLedgerDisposition::Default);
            }
            _ => panic!("Expected EmitFailureLedger payload"),
        }

        // CLEANUP
        let cframe = cleanup_frame("PLAN-40");
        let r2 = apply_result(
            StreamingState::Committed,
            &cframe,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        let emit2 = r2
            .side_effects
            .iter()
            .find(|c| c.kind == SideEffectKind::EmitFailureLedger)
            .unwrap();
        match &emit2.payload {
            SideEffectPayload::EmitFailureLedger(payload) => {
                assert_eq!(payload.telemetry_scope, TelemetryScope::UnemittedSlots);
                assert_eq!(payload.disposition, EmitFailureLedgerDisposition::Default);
            }
            _ => panic!("Expected EmitFailureLedger payload"),
        }
    }

    /// Full run-to-completion chain: FINALIZE_CONVERTER (closing bytes) →
    /// SEND_CLOSING_OUTPUT → SEND_TERMINAL
    #[test]
    fn full_chain_finalize_close_terminal() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-21");
        let outcome = ActionOutcome {
            ngx_result: NgxResult::Ok,
            failure_site: None,
            error_origin: None,
            produced_closing_bytes: true,
            pending_kind: None,
        };

        // Step 1: FINALIZE_CONVERTER → PostCommitSafeFinish + SendClosingOutput
        let r1 = apply_result(StreamingState::Committed, &frame, &outcome, &ctx_usable()).unwrap();
        assert_eq!(r1.new_state, StreamingState::PostCommitSafeFinish);
        let f2 = r1.next_frame.unwrap();
        assert_eq!(f2.action, Action::SendClosingOutput);

        // Step 2: SEND_CLOSING_OUTPUT → PostCommitSafeFinish + SendTerminal
        let r2 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &f2,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r2.new_state, StreamingState::PostCommitSafeFinish);
        let f3 = r2.next_frame.unwrap();
        assert_eq!(f3.action, Action::SendTerminal);

        // Step 3: SEND_TERMINAL → Done + latch + emit
        let r3 = apply_result(
            StreamingState::PostCommitSafeFinish,
            &f3,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r3.new_state, StreamingState::Done);
        assert!(r3.next_frame.is_none());
        assert!(
            r3.side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    /// Full abort chain: FINALIZE_CONVERTER failure (downstream usable) →
    /// BEGIN_ABORT → SEND_ABORT_TERMINAL
    #[test]
    fn full_chain_finalize_failure_abort() {
        let frame = make_frame(Action::FinalizeConverter, "PLAN-31");

        // Step 1: FINALIZE_CONVERTER error → PostCommitAbort + BeginAbort
        let r1 = apply_result(
            StreamingState::Committed,
            &frame,
            &error_outcome(FailureSite::ConverterFinalize),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r1.new_state, StreamingState::PostCommitAbort);
        let f2 = r1.next_frame.unwrap();
        assert_eq!(f2.action, Action::BeginAbort);

        // Step 2: BEGIN_ABORT OK → PostCommitAbort + SendAbortTerminal
        let r2 = apply_result(
            StreamingState::PostCommitAbort,
            &f2,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r2.new_state, StreamingState::PostCommitAbort);
        let f3 = r2.next_frame.unwrap();
        assert_eq!(f3.action, Action::SendAbortTerminal);
        assert!(
            r2.side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::RecordPostcommitAbort)
        );

        // Step 3: SEND_ABORT_TERMINAL OK → Done + latch + emit
        let r3 = apply_result(
            StreamingState::PostCommitAbort,
            &f3,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r3.new_state, StreamingState::Done);
        assert!(r3.next_frame.is_none());
        assert!(
            r3.side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    /// Verify NGX_DONE produces the same results as NGX_OK for terminal sends
    #[test]
    fn ngx_done_equivalent_to_ngx_ok_for_terminal_actions() {
        let frame = make_frame(Action::SendTerminal, "PLAN-21");

        let r_ok = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        let r_done = apply_result(
            StreamingState::PostCommitSafeFinish,
            &frame,
            &done_outcome(),
            &ctx_usable(),
        )
        .unwrap();

        assert_eq!(r_ok.new_state, r_done.new_state);
        assert_eq!(r_ok.next_frame.is_none(), r_done.next_frame.is_none());
        assert_eq!(r_ok.side_effects.len(), r_done.side_effects.len());
    }

    /// Verify abort terminal NGX_DONE equivalent
    #[test]
    fn ngx_done_abort_terminal() {
        let frame = make_frame(Action::SendAbortTerminal, "PLAN-20");
        let r_done = apply_result(
            StreamingState::PostCommitAbort,
            &frame,
            &done_outcome(),
            &ctx_usable(),
        )
        .unwrap();
        assert_eq!(r_done.new_state, StreamingState::Done);
        assert!(
            r_done
                .side_effects
                .iter()
                .any(|c| c.kind == SideEffectKind::LatchTerminalSent)
        );
    }

    /// Verify RESUME_PENDING in non-pending state is an error
    #[test]
    fn resume_pending_in_non_pending_state_is_error() {
        let frame = make_frame(Action::ResumePending, "PLAN-RES");
        let result = apply_result(
            StreamingState::Committed,
            &frame,
            &ok_outcome(),
            &ctx_usable(),
        );
        assert!(result.is_err());
    }
}
