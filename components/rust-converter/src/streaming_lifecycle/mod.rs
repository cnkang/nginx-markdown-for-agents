//! Streaming lifecycle state machine (executable specification).
//!
//! Implements the two-phase decision protocol for the streaming conversion
//! lifecycle. This module governs the formal states, events, actions, and
//! run-to-completion chains that manage pending delivery and terminal
//! completion for streaming requests.
//!
//! # Architecture
//!
//! The model separates five type families:
//! - **State** (15): request lifecycle position
//! - **Event** (19): external triggers delivered to `plan()`
//! - **Action** (12): what the module performs
//! - **ActionOutcome**: structured result of performing an action
//! - **FailureSite/ErrorOrigin**: where and why a failure occurred
//!
//! The two-phase protocol:
//! 1. `plan(state, event, plan_context) → {plan_decision, first_frame}`
//! 2. `apply_result(state, transition_frame, action_outcome, transition_context)
//!     → {new_state, next_frame | null, side_effects, failure_updates}`
//!
//! Normal per-chunk streaming feed is NOT a formal action — it is driven by
//! the outer streaming engine (NGINX body filter loop). This module handles
//! only lifecycle boundary events.
//!
//! The production request path remains in the NGINX module. This Rust module
//! is intentionally not called from that path; model, property, and delivery
//! matrix tests execute it as the authoritative lifecycle specification and
//! parity reference for the production state transitions.

pub mod apply;
pub mod plan;
pub mod policy;
pub mod types;

pub use apply::apply_result;
pub use plan::plan;
pub use types::*;
