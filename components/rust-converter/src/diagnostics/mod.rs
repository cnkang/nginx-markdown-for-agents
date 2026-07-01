//! Diagnostics JSON schema v1 for the `/nginx-markdown/diagnostics` endpoint.
//!
//! This module defines the structured output contract for the diagnostics
//! endpoint. The schema is versioned (`schema_version: 1`) and will be frozen
//! at the 1.0.0 release. After freeze: additive-only changes (new optional
//! fields), no removals.

pub mod schema;

pub use schema::{
    ConditionalDiagnostics, DecisionDiagnostics, DiagnosticsSchema, ErrorDiagnostics,
    EtagDiagnostics, InflightDiagnostics, StreamingDiagnostics,
};
