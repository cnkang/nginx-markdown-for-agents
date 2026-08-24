//! Core type definitions for the streaming lifecycle state machine.
//!
//! Contains the 15 states, 19 events, 12 actions, structured ActionOutcome,
//! FailureSite, ErrorOrigin, PendingKind, and all supporting protocol types.

/// Streaming lifecycle state (15 named states).
///
/// Discriminants are frozen for the stability contract.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum StreamingState {
    /* Pre-commit (6) */
    /// Eligibility gate rejected the response before streaming evaluation.
    NotEligible = 0,
    /// Eligible response under streaming path evaluation.
    StreamingCandidate = 1,
    /// Streaming active, headers not committed, replay buffer available.
    PreCommit = 2,
    /// Still pre-commit, but replay is no longer possible (overflow).
    PreCommitReplayUnavailable = 3,
    /// Exit/routing state: control transfers to full-buffer engine.
    FullBufferFallback = 4,
    /// Exit/routing state: upstream response delivered unchanged.
    Passthrough = 5,

    /* Committed (1) */
    /// Post-header-commit streaming: headers sent, converted body in flight.
    Committed = 6,

    /* Action-phase (2) */
    /// Safe-finish action in progress.
    PostCommitSafeFinish = 7,
    /// Protocol-safe abort action in progress.
    PostCommitAbort = 8,

    /* Pending (3) */
    /// Closing Markdown chain returned NGX_AGAIN, awaiting resume.
    PendingClosingOutput = 9,
    /// Terminal chain returned NGX_AGAIN, awaiting resume.
    PendingTerminal = 10,
    /// Abort terminal chain returned NGX_AGAIN, awaiting resume.
    PendingAbortTerminal = 11,

    /* Terminal (3) */
    /// Terminal: terminal output accepted by downstream.
    Done = 12,
    /// Terminal: request aborted after commit, no clean terminal delivery.
    Aborted = 13,
    /// Terminal: conversion rejected by error policy before commit.
    FailedClosed = 14,
}

impl StreamingState {
    /// Whether this state is a terminal state.
    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            StreamingState::Done | StreamingState::Aborted | StreamingState::FailedClosed
        )
    }

    /// Whether this state is a pre-commit state.
    pub fn is_pre_commit(self) -> bool {
        matches!(
            self,
            StreamingState::NotEligible
                | StreamingState::StreamingCandidate
                | StreamingState::PreCommit
                | StreamingState::PreCommitReplayUnavailable
                | StreamingState::FullBufferFallback
                | StreamingState::Passthrough
        )
    }

    /// Whether this state is a streaming-submachine exit/routing state.
    pub fn is_exit_routing(self) -> bool {
        matches!(
            self,
            StreamingState::FullBufferFallback | StreamingState::Passthrough
        )
    }

    /// Whether this is a pending state (awaiting resume).
    pub fn is_pending(self) -> bool {
        matches!(
            self,
            StreamingState::PendingClosingOutput
                | StreamingState::PendingTerminal
                | StreamingState::PendingAbortTerminal
        )
    }

    /// Whether this is a committed or formal-pending state (CLIENT_ABORT target).
    pub fn is_committed_or_pending(self) -> bool {
        matches!(
            self,
            StreamingState::Committed
                | StreamingState::PostCommitSafeFinish
                | StreamingState::PostCommitAbort
                | StreamingState::PendingClosingOutput
                | StreamingState::PendingTerminal
                | StreamingState::PendingAbortTerminal
        )
    }
}

/// Streaming lifecycle event (19 events).
///
/// These are the ONLY values that appear in the Event column of the Plan table.
/// Actions never appear as events.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EventKind {
    /// Response passes eligibility and content-type gates.
    Eligible = 0,
    /// Response fails eligibility.
    NotEligible = 1,
    /// Streaming path selected and incremental converter started.
    StreamingStart = 2,
    /// Incremental parser cannot handle the document shape.
    ParserUnsuitable = 3,
    /// Built-in hard exclusion (text/event-stream, etc.).
    HardExcluded = 4,
    /// A whole-document feature is required.
    FullDocFeature = 5,
    /// Streaming budget or replay buffer could not be initialized.
    BudgetInitFailure = 6,
    /// Pre-commit replay buffer exceeded its configured size.
    ReplayOverflow = 7,
    /// Memory or time budget exceeded during streaming.
    ResourceLimit = 8,
    /// Strict validator mode requires full-buffer representation.
    StrictEtag = 9,
    /// Look-behind window exceeded, replay guarantee lost.
    LookBehindOverflow = 10,
    /// Auto-mode risk heuristic declines streaming.
    AutoRisk = 11,
    /// HeaderPlan preparation completed; header commit requested.
    Commit = 12,
    /// Failure raised during streaming or finalization.
    Error = 13,
    /// Upstream end-of-stream detected (last_buf).
    UpstreamEnd = 14,
    /// Write event resumes a downstream-owned pending chain.
    ResumeDrain = 15,
    /// Client closed or reset the connection.
    ClientAbort = 16,
    /// Body filter re-entered after a terminal state was reached.
    BodyFilterReentry = 17,
    /// Request cleanup handler runs.
    Cleanup = 18,
}

/// Streaming lifecycle action (12 actions).
///
/// SAFE_FINISH is decomposed into FINALIZE_CONVERTER → SEND_CLOSING_OUTPUT → SEND_TERMINAL.
/// ABORT is decomposed into BEGIN_ABORT → SEND_ABORT_TERMINAL.
/// Normal per-chunk streaming feed is NOT a formal action.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Action {
    /// No module action; state bookkeeping only.
    None = 0,
    /// Deliver the original upstream HTML unchanged (fail-open).
    PassHtml = 1,
    /// Emit the resolved reject status (502, 429, or 503).
    RejectStatus = 2,
    /// Commit the prepared HeaderPlan and send response headers.
    CommitHeaders = 3,
    /// Abandon streaming and route through full-buffer conversion.
    SwitchFullBuffer = 4,
    /// Finalize the Rust converter handle.
    FinalizeConverter = 5,
    /// Send closing Markdown bytes to downstream.
    SendClosingOutput = 6,
    /// Send the terminal chain (last_buf=1) to downstream.
    SendTerminal = 7,
    /// Initiate a protocol-safe abort of the committed response.
    BeginAbort = 8,
    /// Send the abort terminal chain (last_buf=1, no content).
    SendAbortTerminal = 9,
    /// Resume a downstream-owned pending chain.
    ResumePending = 10,
    /// Bypass conversion entirely for this response.
    Passthrough = 11,
}

/// NGINX return code semantics in the ActionOutcome.
#[repr(i8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum NgxResult {
    /// Success.
    Ok = 0,
    /// Request complete (e.g., subrequest finalized).
    Done = 1,
    /// Suspend and resume (backpressure).
    Again = 2,
    /// Definitive error (unrecoverable for this action).
    Error = -1,
}

/// Where in the streaming pipeline the failure occurred (5 sites).
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FailureSite {
    /// Rust cannot finalize, handle consumed.
    ConverterFinalize = 0,
    /// Markdown produced but undeliverable.
    ClosingOutput = 1,
    /// Terminal chain send failed definitively.
    TerminalSend = 2,
    /// Abort terminal chain send failed definitively.
    AbortTerminalSend = 3,
    /// Resume of a pending chain failed definitively.
    PendingResume = 4,
}

/// What caused the failure (8 canonical origins, operator-visible).
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorOrigin {
    /// Pool or heap allocation failed.
    Allocation = 0,
    /// Downstream filter rejected output.
    Downstream = 1,
    /// Impossible state reached.
    Invariant = 2,
    /// Input format error.
    Format = 3,
    /// Truncated input or incomplete data.
    Truncated = 4,
    /// Wall-clock time budget exceeded.
    Timeout = 5,
    /// Memory budget exceeded.
    MemoryBudget = 6,
    /// Internal error not otherwise classified.
    Internal = 7,
}

/// Kind of pending chain when ngx_result is NGX_AGAIN (3 kinds).
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PendingKind {
    /// Closing Markdown bytes chain returned NGX_AGAIN.
    ClosingMarkdown = 0,
    /// Terminal last_buf chain returned NGX_AGAIN.
    Terminal = 1,
    /// Abort terminal last_buf chain returned NGX_AGAIN.
    AbortTerminal = 2,
}

/// Structured ActionOutcome replacing the former scalar Action_Result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionOutcome {
    /// The NGINX return code from the performed action.
    pub ngx_result: NgxResult,
    /// Where in the pipeline the failure occurred (null for non-failures).
    pub failure_site: Option<FailureSite>,
    /// What caused the failure (null for non-failures).
    pub error_origin: Option<ErrorOrigin>,
    /// Whether FINALIZE_CONVERTER produced closing Markdown bytes.
    pub produced_closing_bytes: bool,
    /// The kind of pending chain when ngx_result is NGX_AGAIN.
    pub pending_kind: Option<PendingKind>,
}

/// Resolved error policy, mapped once from `markdown_error_policy`.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ResolvedErrorPolicy {
    /// `pass` — fail-open.
    Pass = 0,
    /// `fail_closed` → status 502.
    Status502 = 1,
    /// `status 429` → status 429.
    Status429 = 2,
    /// `status 503` → status 503.
    Status503 = 3,
}

/// Action payload carried by each frame.
///
/// Only REJECT_STATUS may carry a non-null `reject_status`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ActionPayload {
    /// The resolved reject status (502, 429, or 503), or None for non-reject actions.
    pub reject_status: Option<u16>,
}

impl ActionPayload {
    /// Null payload (for all actions except REJECT_STATUS).
    pub const NULL: Self = ActionPayload {
        reject_status: None,
    };
}

/// Resolve the reject status from the error policy and reason.
///
/// Returns 502 for STATUS_502, 429 for STATUS_429, 503 for STATUS_503,
/// 502 for PASS with reason `fail_open_unavailable` or `resource_limit`,
/// and None otherwise.
pub fn resolve_reject_status(policy: ResolvedErrorPolicy, reason: &str) -> Option<u16> {
    match policy {
        ResolvedErrorPolicy::Status502 => Some(502),
        ResolvedErrorPolicy::Status429 => Some(429),
        ResolvedErrorPolicy::Status503 => Some(503),
        ResolvedErrorPolicy::Pass => {
            if reason == "fail_open_unavailable" || reason == "resource_limit" {
                Some(502)
            } else {
                None
            }
        }
    }
}

/// Event envelope: the typed event with optional failure record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EventEnvelope {
    /// The event kind.
    pub kind: EventKind,
    /// Optional failure record (required for some events, forbidden for others).
    pub failure_record: Option<FailureRecord>,
}

/// Failure record capturing where and why a failure happened.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FailureRecord {
    /// The stage in the pipeline (e.g., "streaming", "decompression").
    pub stage: String,
    /// The reason code (key in reason_registry.toml).
    pub reason: String,
    /// The error origin.
    pub error_origin: ErrorOrigin,
    /// The failure site (null for event-level failures).
    pub failure_site: Option<FailureSite>,
}

/// Request-scoped failure ledger.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FailureLedger {
    /// Primary failure record (first definitive failure).
    pub primary: Option<FailureRecord>,
    /// Secondary failure record (subsequent non-delivery failure).
    pub secondary: Option<FailureRecord>,
    /// Delivery failure record (closing_output/terminal_send/abort_terminal_send/pending_resume).
    pub delivery: Option<FailureRecord>,
    /// Whether the ledger has been stored (request-local snapshot).
    pub ledger_stored: bool,
    /// Whether the ledger has been emitted (telemetry).
    pub ledger_emitted: bool,
}

impl FailureLedger {
    /// Create an empty failure ledger.
    pub fn empty() -> Self {
        FailureLedger {
            primary: None,
            secondary: None,
            delivery: None,
            ledger_stored: false,
            ledger_emitted: false,
        }
    }

    /// Whether the ledger has any populated slot.
    pub fn is_populated(&self) -> bool {
        self.primary.is_some() || self.secondary.is_some() || self.delivery.is_some()
    }

    /// Return a copy of this ledger with the pre-effect slot updates applied.
    ///
    /// The apply protocol (apply.rs module doc) requires the caller to apply
    /// pre-effect updates before constructing the next transition frame, so
    /// frames handed to the next apply step carry the freshly recorded
    /// failure rather than a stale ledger.
    pub fn apply_pre_effect(&self, pre_effect: &PreEffect) -> FailureLedger {
        let mut next = self.clone();
        if let Some(record) = &pre_effect.primary_update {
            next.primary = Some(record.clone());
        }
        if let Some(record) = &pre_effect.secondary_update {
            next.secondary = Some(record.clone());
        }
        if let Some(record) = &pre_effect.delivery_update {
            next.delivery = Some(record.clone());
        }
        next
    }
}

/// Failure updates returned by apply_result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FailureUpdates {
    /// Pre-effect slot updates (applied first).
    pub pre_effect: PreEffect,
    /// Post-effect latch updates (applied after command success).
    pub post_effect: PostEffect,
}

impl FailureUpdates {
    /// No-op failure updates.
    pub const NONE: Self = FailureUpdates {
        pre_effect: PreEffect {
            primary_update: None,
            secondary_update: None,
            delivery_update: None,
        },
        post_effect: PostEffect {
            set_ledger_stored_after: None,
            set_ledger_stored_if_nonempty_after: None,
            set_ledger_emitted_if_unemitted_after: None,
        },
    };
}

/// Pre-effect slot updates.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreEffect {
    /// Promote or set the primary failure record.
    pub primary_update: Option<FailureRecord>,
    /// Set the secondary failure record.
    pub secondary_update: Option<FailureRecord>,
    /// Set the delivery failure record.
    pub delivery_update: Option<FailureRecord>,
}

/// Post-effect latch updates.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PostEffect {
    /// Set ledger_stored=true after this command ID returns EXECUTED.
    pub set_ledger_stored_after: Option<CommandId>,
    /// Set ledger_stored=true after this command ID returns EXECUTED, only if ledger is nonempty.
    pub set_ledger_stored_if_nonempty_after: Option<CommandId>,
    /// Set ledger_emitted=true after this command ID returns EXECUTED, only if not already emitted.
    pub set_ledger_emitted_if_unemitted_after: Option<CommandId>,
}

/// Unique command identifier.
pub type CommandId = String;

/// Owner predicate for side-effect command execution.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum OwnerPredicate {
    /// Always execute.
    Always = 0,
    /// Execute only if owner is STREAMING.
    OwnerIsStreaming = 1,
    /// Execute only if owner is FULL_BUFFER.
    OwnerIsFullBuffer = 2,
}

/// Owner transition that a command may perform.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum OwnerTransition {
    /// STREAMING → FULL_BUFFER.
    StreamingToFullBuffer = 0,
    /// STREAMING → RELEASED.
    StreamingToReleased = 1,
    /// FULL_BUFFER → RELEASED.
    FullBufferToReleased = 2,
}

/// Failure ledger owner (request-level).
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FailureLedgerOwner {
    /// Initial owner — streaming submachine.
    Streaming = 0,
    /// After handoff to full-buffer engine.
    FullBuffer = 1,
    /// Terminal — lifecycle complete.
    Released = 2,
}

/// Side effect kind (8 frozen effects).
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SideEffectKind {
    /// Set the terminal-sent flag.
    LatchTerminalSent = 0,
    /// Record safe-finish output loss.
    SetSafeFinishOutputLoss = 1,
    /// Record safe-finish terminal send failure.
    SetSafeFinishTerminalSendFailed = 2,
    /// Store the updated ledger in request-local context.
    StoreFailureLedger = 3,
    /// Emit the failure ledger (telemetry).
    EmitFailureLedger = 4,
    /// Record the postcommit abort metric (one-shot).
    RecordPostcommitAbort = 5,
    /// Clear formal inflight counter and pending state.
    ClearInflightAndPending = 6,
    /// Transfer failure ledger ownership to full-buffer.
    TransferFailureToFullBuffer = 7,
}

/// Emit failure ledger disposition.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EmitFailureLedgerDisposition {
    /// Default: preserves existing slot dispositions.
    Default = 0,
    /// Full-buffer recovered the failure.
    RecoveredByFullBuffer = 1,
    /// Full-buffer conversion failure.
    FullBufferConversionFailure = 2,
    /// Full-buffer delivery failure.
    FullBufferDeliveryFailure = 3,
}

/// Payload for emit_failure_ledger command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EmitFailureLedgerPayload {
    /// Always UNEMITTED_SLOTS.
    pub telemetry_scope: TelemetryScope,
    /// The disposition for the emission.
    pub disposition: EmitFailureLedgerDisposition,
}

/// Telemetry scope (frozen).
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TelemetryScope {
    /// Emit only unemitted slots.
    UnemittedSlots = 0,
}

/// Transfer mode for transfer_failure_to_full_buffer.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TransferMode {
    /// Permits empty ledger, ownership transfer only.
    RoutingOwnership = 0,
    /// Requires non-null primary, transfers complete ledger.
    FailureRecovery = 1,
}

/// Side effect command payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SideEffectPayload {
    /// No payload.
    None,
    /// Updated ledger snapshot.
    UpdatedLedger,
    /// Emit failure ledger payload.
    EmitFailureLedger(EmitFailureLedgerPayload),
    /// Transfer mode (routing or failure recovery).
    TransferMode(TransferMode),
}

/// A side-effect command in the returned command list.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SideEffectCommand {
    /// Unique command identifier (CMD_A### namespace).
    pub command_id: CommandId,
    /// The side effect kind.
    pub kind: SideEffectKind,
    /// Execution predicate based on owner.
    pub execute_if: OwnerPredicate,
    /// Command payload.
    pub payload: SideEffectPayload,
    /// Optional owner transition on EXECUTED.
    pub owner_transition: Option<OwnerTransition>,
}

/// Outcome of executing a side-effect command.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SideEffectCommandOutcome {
    /// Command was executed.
    Executed = 0,
    /// Command skipped due to owner mismatch.
    SkippedOwnerMismatch = 1,
}

/// Full-buffer completion kind.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FullBufferCompletionKind {
    /// Conversion succeeded.
    Success = 0,
    /// Conversion failed.
    ConversionFailure = 1,
    /// Delivery to downstream failed.
    DeliveryFailure = 2,
    /// Client aborted.
    ClientAbort = 3,
    /// Cleanup lifecycle.
    Cleanup = 4,
}

/// Context for `plan()` — carries only frozen fields.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanContext {
    /// The resolved error policy.
    pub resolved_error_policy: ResolvedErrorPolicy,
    /// Whether full input can be reconstructed (replay available).
    pub full_input_reconstructible: bool,
    /// Whether full-buffer resources are available.
    pub full_buffer_resources_allow: bool,
    /// The current failure ledger.
    pub failure_ledger: FailureLedger,
}

/// Context for `apply_result()` — carries only downstream_usable.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TransitionContext {
    /// Whether the downstream filter chain is usable.
    pub downstream_usable: bool,
}

/// Plan decision returned by `plan()`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanDecision {
    /// The validated event envelope.
    pub event: EventEnvelope,
    /// The initial action for this transition.
    pub initial_action: Action,
    /// Action payload (reject_status for REJECT_STATUS, null otherwise).
    pub action_payload: ActionPayload,
    /// The reason string for this transition.
    pub reason: String,
    /// The transition ID minted by the Plan table.
    pub transition_id: String,
    /// The normalized failure ledger after event-policy validation.
    pub failure_ledger: FailureLedger,
}

/// Transition frame: carries current step identity and immutable execution data.
///
/// Returned as `first_frame` by `plan()` and as `next_frame` by `apply_result()`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransitionFrame {
    /// The root transition ID (from Plan table).
    pub transition_id: String,
    /// Unique step ID (root steps use transition_id, chain steps add suffixes).
    pub step_id: String,
    /// The action for this step.
    pub action: Action,
    /// Action payload for this step.
    pub action_payload: ActionPayload,
    /// The validated event that initiated this transition.
    pub event: EventEnvelope,
    /// The reason string.
    pub reason: String,
    /// The failure ledger state at this step.
    pub failure_ledger: FailureLedger,
}

/// Result of `plan()`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanResult {
    /// The plan decision.
    pub plan_decision: PlanDecision,
    /// The first transition frame.
    pub first_frame: TransitionFrame,
}

/// Result of `apply_result()`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApplyResult {
    /// The new state after this transition step.
    pub new_state: StreamingState,
    /// The next frame in the run-to-completion chain (None = chain terminates).
    pub next_frame: Option<TransitionFrame>,
    /// Ordered list of side-effect commands to execute.
    pub side_effects: Vec<SideEffectCommand>,
    /// Failure updates (pre-effect and post-effect).
    pub failure_updates: FailureUpdates,
}

/// Error type for state machine protocol violations.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StateMachineError {
    /// A required failure record is missing for this event.
    MissingRequiredFailureRecord { event: EventKind },
    /// A forbidden failure record was provided for this event.
    ForbiddenFailureRecord { event: EventKind },
    /// The (state, event) pair is not in the Plan table.
    InvalidTransition {
        state: StreamingState,
        event: EventKind,
    },
    /// Invariant violation.
    InvariantViolation { message: String },
    /// Action/payload mismatch.
    ActionPayloadMismatch { action: Action },
}
