//! Schema validation for dynconf JSON documents.
//!
//! Validates parsed JSON against the dynconf schema:
//! - `schema_version` must be present and equal to 1
//! - Only known keys are accepted (filter, prune_noise, log_verbosity,
//!   error_policy, streaming_buffer)
//! - Each value must match its expected type and allowed values/ranges
//! - No additional properties are permitted

use super::parser::{DynconfParseError, DynconfParseErrorKind, JsonValue};

/// Filter field values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FilterValue {
    /// Conversion enabled.
    On,
    /// Conversion disabled.
    Off,
}

impl FilterValue {
    /// Returns the canonical string representation.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::On => "on",
            Self::Off => "off",
        }
    }
}

/// Prune noise field values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PruneNoiseValue {
    /// Pruning enabled.
    On,
    /// Pruning disabled.
    Off,
}

impl PruneNoiseValue {
    /// Returns the canonical string representation.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::On => "on",
            Self::Off => "off",
        }
    }
}

/// Log verbosity levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogVerbosity {
    /// Only error messages.
    Error,
    /// Warnings and above.
    Warn,
    /// Informational and above.
    Info,
    /// Debug and above (most verbose).
    Debug,
}

impl LogVerbosity {
    /// Returns the canonical string representation.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Error => "error",
            Self::Warn => "warn",
            Self::Info => "info",
            Self::Debug => "debug",
        }
    }
}

/// Error policy values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorPolicy {
    /// Pass through on error (fail-open).
    Pass,
    /// Fail closed on error.
    FailClosed,
    /// Return HTTP 429 on error.
    Status429,
    /// Return HTTP 503 on error.
    Status503,
}

impl ErrorPolicy {
    /// Returns the canonical string representation.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Pass => "pass",
            Self::FailClosed => "fail_closed",
            Self::Status429 => "status 429",
            Self::Status503 => "status 503",
        }
    }
}

/// The validated dynconf value set.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DynconfValue {
    /// Filter value, if explicitly present.
    pub filter: Option<FilterValue>,
    /// Prune noise value, if explicitly present.
    pub prune_noise: Option<PruneNoiseValue>,
    /// Log verbosity, if explicitly present.
    pub log_verbosity: Option<LogVerbosity>,
    /// Error policy, if explicitly present.
    pub error_policy: Option<ErrorPolicy>,
    /// Streaming buffer size in bytes, if explicitly present.
    pub streaming_buffer: Option<u64>,
}

/// The complete set of known dynconf keys (excluding schema_version).
const KNOWN_KEYS: &[&str] = &[
    "schema_version",
    "filter",
    "prune_noise",
    "log_verbosity",
    "error_policy",
    "streaming_buffer",
];

/// Minimum streaming_buffer value in bytes (64 KiB).
const STREAMING_BUFFER_MIN: u64 = 65_536;

/// Maximum streaming_buffer value in bytes (1 GiB).
const STREAMING_BUFFER_MAX: u64 = 1_073_741_824;

/// Validate a parsed JSON value against the dynconf schema.
///
/// # Arguments
///
/// * `value` - The parsed top-level JSON object
///
/// # Returns
///
/// * `Ok(DynconfValue)` - The validated and typed configuration
/// * `Err(DynconfParseError)` - The first validation failure encountered
pub fn validate_dynconf(value: &JsonValue) -> Result<DynconfValue, DynconfParseError> {
    let entries = object_entries(value)?;
    validate_known_keys(entries)?;
    validate_schema_version(entries)?;
    let mut result = DynconfValue {
        filter: None,
        prune_noise: None,
        log_verbosity: None,
        error_policy: None,
        streaming_buffer: None,
    };

    validate_optional_fields(entries, &mut result)?;
    Ok(result)
}

fn object_entries(value: &JsonValue) -> Result<&[(String, JsonValue)], DynconfParseError> {
    match value {
        JsonValue::Object(entries) => Ok(entries),
        _ => Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidType,
            "top-level value must be a JSON object".to_string(),
        )),
    }
}

fn validate_known_keys(entries: &[(String, JsonValue)]) -> Result<(), DynconfParseError> {
    for (key, _) in entries {
        if !KNOWN_KEYS.contains(&key.as_str()) {
            return Err(DynconfParseError::new(
                DynconfParseErrorKind::UnknownKey,
                format!("unknown key '{}'", key),
            ));
        }
    }
    Ok(())
}

fn validate_schema_version(entries: &[(String, JsonValue)]) -> Result<(), DynconfParseError> {
    let schema_version_entry = entries.iter().find(|(key, _)| key == "schema_version");
    match schema_version_entry {
        None => Err(DynconfParseError::new(
            DynconfParseErrorKind::MissingSchemaVersion,
            "required field 'schema_version' is missing".to_string(),
        )),
        Some((_, JsonValue::Number(value, raw))) => {
            if raw.contains('.') || raw.contains('e') || raw.contains('E') {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidSchemaVersion,
                    format!("schema_version must be integer 1, got '{}'", raw),
                ));
            }
            if *value != 1.0 {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidSchemaVersion,
                    format!("schema_version must be 1, got {}", raw),
                ));
            }
            let trimmed = raw.trim_start_matches('-');
            if trimmed.len() > 1 && trimmed.starts_with('0') {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidSchemaVersion,
                    format!(
                        "schema_version must be 1 without leading zeros, got '{}'",
                        raw
                    ),
                ));
            }
            Ok(())
        }
        Some((_, _)) => Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidSchemaVersion,
            "schema_version must be an integer".to_string(),
        )),
    }
}

fn validate_optional_fields(
    entries: &[(String, JsonValue)],
    result: &mut DynconfValue,
) -> Result<(), DynconfParseError> {
    for (key, val) in entries {
        match key.as_str() {
            "schema_version" => { /* already validated */ }
            "filter" => {
                result.filter = Some(validate_filter(val)?);
            }
            "prune_noise" => {
                result.prune_noise = Some(validate_prune_noise(val)?);
            }
            "log_verbosity" => {
                result.log_verbosity = Some(validate_log_verbosity(val)?);
            }
            "error_policy" => {
                result.error_policy = Some(validate_error_policy(val)?);
            }
            "streaming_buffer" => {
                result.streaming_buffer = Some(validate_streaming_buffer(val)?);
            }
            _ => unreachable!("unknown keys already rejected above"),
        }
    }
    Ok(())
}

fn validate_filter(value: &JsonValue) -> Result<FilterValue, DynconfParseError> {
    match value {
        JsonValue::String(s) => match s.as_str() {
            "on" => Ok(FilterValue::On),
            "off" => Ok(FilterValue::Off),
            _ => Err(DynconfParseError::new(
                DynconfParseErrorKind::ValueOutOfRange,
                format!("filter must be \"on\" or \"off\", got \"{}\"", s),
            )),
        },
        _ => Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidType,
            "filter must be a string".to_string(),
        )),
    }
}

fn validate_prune_noise(value: &JsonValue) -> Result<PruneNoiseValue, DynconfParseError> {
    match value {
        JsonValue::String(s) => match s.as_str() {
            "on" => Ok(PruneNoiseValue::On),
            "off" => Ok(PruneNoiseValue::Off),
            _ => Err(DynconfParseError::new(
                DynconfParseErrorKind::ValueOutOfRange,
                format!("prune_noise must be \"on\" or \"off\", got \"{}\"", s),
            )),
        },
        _ => Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidType,
            "prune_noise must be a string".to_string(),
        )),
    }
}

fn validate_log_verbosity(value: &JsonValue) -> Result<LogVerbosity, DynconfParseError> {
    match value {
        JsonValue::String(s) => match s.as_str() {
            "error" => Ok(LogVerbosity::Error),
            "warn" => Ok(LogVerbosity::Warn),
            "info" => Ok(LogVerbosity::Info),
            "debug" => Ok(LogVerbosity::Debug),
            _ => Err(DynconfParseError::new(
                DynconfParseErrorKind::ValueOutOfRange,
                format!(
                    "log_verbosity must be one of \"error\", \"warn\", \"info\", \"debug\", got \"{}\"",
                    s
                ),
            )),
        },
        _ => Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidType,
            "log_verbosity must be a string".to_string(),
        )),
    }
}

fn validate_error_policy(value: &JsonValue) -> Result<ErrorPolicy, DynconfParseError> {
    match value {
        JsonValue::String(s) => match s.as_str() {
            "pass" => Ok(ErrorPolicy::Pass),
            "fail_closed" => Ok(ErrorPolicy::FailClosed),
            "status 429" => Ok(ErrorPolicy::Status429),
            "status 503" => Ok(ErrorPolicy::Status503),
            _ => Err(DynconfParseError::new(
                DynconfParseErrorKind::ValueOutOfRange,
                format!(
                    "error_policy must be one of \"pass\", \"fail_closed\", \"status 429\", \"status 503\", got \"{}\"",
                    s
                ),
            )),
        },
        _ => Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidType,
            "error_policy must be a string".to_string(),
        )),
    }
}

fn validate_streaming_buffer(value: &JsonValue) -> Result<u64, DynconfParseError> {
    match value {
        JsonValue::Number(_val, raw) => {
            // Must be an integer (no decimal point, no exponent)
            if raw.contains('.') || raw.contains('e') || raw.contains('E') {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::InvalidType,
                    format!("streaming_buffer must be an integer, got '{}'", raw),
                ));
            }

            // Reject leading zeros (e.g., "065536")
            let check = raw.trim_start_matches('-');
            if check.len() > 1 && check.starts_with('0') {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::ValueOutOfRange,
                    format!(
                        "streaming_buffer must not have leading zeros, got '{}'",
                        raw
                    ),
                ));
            }

            // Parse as i64 first to catch negatives
            let parsed: i64 = raw.parse().map_err(|_| {
                DynconfParseError::new(
                    DynconfParseErrorKind::ValueOutOfRange,
                    format!("streaming_buffer value '{}' is not a valid integer", raw),
                )
            })?;

            if parsed < 0 {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::ValueOutOfRange,
                    format!("streaming_buffer must be positive, got {}", parsed),
                ));
            }

            let val_u64 = parsed as u64;
            if val_u64 < STREAMING_BUFFER_MIN {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::ValueOutOfRange,
                    format!(
                        "streaming_buffer {} is below minimum {}",
                        val_u64, STREAMING_BUFFER_MIN
                    ),
                ));
            }
            if val_u64 > STREAMING_BUFFER_MAX {
                return Err(DynconfParseError::new(
                    DynconfParseErrorKind::ValueOutOfRange,
                    format!(
                        "streaming_buffer {} exceeds maximum {}",
                        val_u64, STREAMING_BUFFER_MAX
                    ),
                ));
            }

            Ok(val_u64)
        }
        JsonValue::String(_) => Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidType,
            "streaming_buffer must be an integer, not a string; size strings are not accepted"
                .to_string(),
        )),
        _ => Err(DynconfParseError::new(
            DynconfParseErrorKind::InvalidType,
            "streaming_buffer must be an integer".to_string(),
        )),
    }
}
