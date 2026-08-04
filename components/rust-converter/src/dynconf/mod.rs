//! Dynamic configuration (dynconf) JSON parser and validator.
//!
//! This module implements RFC 8259 JSON parsing with schema validation,
//! duplicate-key detection, type validation, range checking, and canonical
//! SHA-256 digest computation for the dynconf file format.
//!
//! # Architecture
//!
//! The C module is responsible for:
//! - Timer management and file polling
//! - Safe file I/O with size cap enforcement (1 MiB)
//! - File identity tracking (device, inode, size, mtime)
//! - Request snapshot binding and NGINX pool/lifecycle integration
//! - Logging and applying the typed result from the Rust FFI call
//!
//! Rust (this module) is responsible for:
//! - JSON parsing (RFC 8259 compliant)
//! - Duplicate-key detection
//! - Schema validation (schema_version, known keys, types, ranges)
//! - Token budget and nesting depth enforcement
//! - Canonical normalized serialization for active_digest
//! - Both source_digest (SHA-256 over raw bytes) and active_digest computation
//!
//! # Digest Contract
//!
//! Two distinct digests are returned:
//!
//! - `source_digest`: SHA-256 over the raw file bytes exactly as received,
//!   before any parsing. Detects any byte-level change.
//! - `active_digest`: SHA-256 over a canonical UTF-8 JSON representation
//!   containing `schema_version` plus only explicitly present supported keys
//!   in fixed order with normalized typed values. Absent keys are NOT defaulted
//!   into the digest.
//!
//! The canonical key order is:
//! `schema_version`, `filter`, `prune_noise`, `log_verbosity`, `error_policy`, `streaming_buffer`

mod digest;
pub mod ffi;
mod parser;
mod schema;

pub use digest::compute_source_digest;
pub use parser::{DynconfParseError, DynconfParseErrorKind};
pub use schema::{DynconfValue, ErrorPolicy, FilterValue, LogVerbosity, PruneNoiseValue};

use digest::compute_active_digest;
use parser::parse_json_with_budget;
use schema::validate_dynconf;

/// Maximum allowed document size in bytes (1 MiB).
pub const MAX_DOCUMENT_SIZE: usize = 1_048_576;

/// Maximum allowed nesting depth for JSON structures.
pub const MAX_NESTING_DEPTH: usize = 8;

/// Maximum number of parse tokens allowed.
pub const MAX_TOKEN_BUDGET: usize = 10_000;

/// Result of a successful dynconf parse and validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DynconfResult {
    /// SHA-256 hex digest over the raw input bytes.
    pub source_digest: String,
    /// SHA-256 hex digest over the canonical normalized JSON.
    pub active_digest: String,
    /// Parsed filter value, if present in the input.
    pub filter: Option<FilterValue>,
    /// Parsed prune_noise value, if present in the input.
    pub prune_noise: Option<PruneNoiseValue>,
    /// Parsed log_verbosity value, if present in the input.
    pub log_verbosity: Option<LogVerbosity>,
    /// Parsed error_policy value, if present in the input.
    pub error_policy: Option<ErrorPolicy>,
    /// Parsed streaming_buffer value in bytes, if present in the input.
    pub streaming_buffer: Option<u64>,
}

/// Parse and validate a dynconf JSON document from raw bytes.
///
/// This is the main entry point for the dynconf parser. It performs:
/// 1. Size validation (≤ 1 MiB)
/// 2. SHA-256 source digest computation
/// 3. JSON parsing with token budget and depth limits
/// 4. Duplicate-key detection
/// 5. Schema validation (schema_version, known keys, types, ranges)
/// 6. Canonical active digest computation
///
/// # Arguments
///
/// * `raw_bytes` - The raw file content as read from disk
///
/// # Returns
///
/// * `Ok(DynconfResult)` - Successfully parsed and validated configuration
/// * `Err(DynconfParseError)` - Validation failure with diagnostic message
///
/// # Errors
///
/// Returns an error if:
/// - Document exceeds 1 MiB
/// - JSON is malformed (not valid RFC 8259)
/// - Token budget (10,000) is exceeded
/// - Nesting depth (8) is exceeded
/// - Duplicate keys are present
/// - `schema_version` is absent or not equal to 1
/// - Unknown keys are present
/// - Values have incorrect types
/// - Values are outside allowed ranges
pub fn parse_dynconf(raw_bytes: &[u8]) -> Result<DynconfResult, DynconfParseError> {
    // 1. Size check
    if raw_bytes.len() > MAX_DOCUMENT_SIZE {
        return Err(DynconfParseError::new(
            DynconfParseErrorKind::DocumentTooLarge,
            format!(
                "document size {} exceeds maximum {} bytes",
                raw_bytes.len(),
                MAX_DOCUMENT_SIZE
            ),
        ));
    }

    // 2. Source digest (SHA-256 over raw bytes)
    let source_digest = compute_source_digest(raw_bytes);

    // 3. Parse JSON with budget constraints
    let parsed = parse_json_with_budget(raw_bytes, MAX_NESTING_DEPTH, MAX_TOKEN_BUDGET)?;

    // 4. Validate against schema (includes duplicate-key detection done in step 3)
    let validated = validate_dynconf(&parsed)?;

    // 5. Compute active digest over canonical representation
    let active_digest = compute_active_digest(&validated);

    Ok(DynconfResult {
        source_digest,
        active_digest,
        filter: validated.filter,
        prune_noise: validated.prune_noise,
        log_verbosity: validated.log_verbosity,
        error_policy: validated.error_policy,
        streaming_buffer: validated.streaming_buffer,
    })
}

#[cfg(test)]
mod tests;
