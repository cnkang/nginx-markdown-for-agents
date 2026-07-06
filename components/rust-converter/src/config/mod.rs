//! Configuration profiles and merge logic (spec 50, 0.9.0).
//!
//! This module implements the three production profiles (`strict_cache`,
//! `balanced`, `streaming_first`) and the merge-order logic that computes
//! the effective configuration from built-in defaults, profile defaults,
//! and explicit user directives.
//!
//! # Merge Order
//!
//! ```text
//! effective = builtin_defaults
//!           → apply profile defaults (if a profile is active)
//!           → apply explicit directives (user overrides)
//! ```
//!
//! Explicit directives always win. Profile defaults override built-in
//! defaults. When no profile is specified the built-in Config V2 defaults
//! apply directly (0.9.0 breaking release; no fallback to 0.8.x behavior).

pub mod conflict;
pub mod merge;
pub mod profile;

pub use conflict::{Conflict, ConflictLevel, detect_conflicts};
pub use merge::{BuiltinDefaults, EffectiveConfig, ExplicitConfig, merge_config};
pub use profile::{ForcedField, Profile, ProfileDefaults};
