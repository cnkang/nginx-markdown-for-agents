//! Metrics label validation and normalization for the observability schema v1.
//!
//! This module enforces the label whitelist and normalization rules that
//! prevent high-cardinality label explosion in Prometheus metrics output.

pub mod labels;

pub use labels::{
    ALL_LABELS, MetricLabel, is_label_allowed, is_label_blocked, normalize_label_value,
};
