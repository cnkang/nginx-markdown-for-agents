//! Diagnostics JSON schema v1 for the `/nginx-markdown/diagnostics` endpoint.

pub mod schema;

pub use schema::{
    ConfigSnapshot, DiagnosticsSchema, DynconfState, EffectiveConfig, MetricsSnapshot,
    RecentDecision, StreamingConfig, StreamingMetrics,
};
