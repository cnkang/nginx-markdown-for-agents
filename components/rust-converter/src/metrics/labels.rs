//! Metric label validation and normalization.
//!
//! Defines a repository-internal Rust label policy and helpers for proposed
//! Rust-owned metrics. The production NGINX Prometheus renderer is implemented
//! in C and has its own bounded label set, including an explicitly enabled and
//! cardinality-capped `path` label. This module must not be treated as the
//! production wire-label registry.

/// Allowed label keys for the internal Rust metrics model.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum MetricLabel {
    /// The reason code for the decision (from `ReasonCode::as_str()`).
    Reason,
    /// The active profile name (balanced, strict_cache, streaming_first).
    Profile,
    /// The request processing path mode (full_buffer, streaming).
    PathMode,
    /// The cache validation setting (off, ims_only, full).
    CacheValidation,
}

impl MetricLabel {
    /// Returns the Prometheus label key name as a static string slice.
    ///
    /// # Examples
    ///
    /// ```
    /// use nginx_markdown_converter::metrics::MetricLabel;
    ///
    /// assert_eq!(MetricLabel::Reason.as_str(), "reason");
    /// assert_eq!(MetricLabel::PathMode.as_str(), "path_mode");
    /// ```
    pub fn as_str(&self) -> &'static str {
        match self {
            MetricLabel::Reason => "reason",
            MetricLabel::Profile => "profile",
            MetricLabel::PathMode => "path_mode",
            MetricLabel::CacheValidation => "cache_validation",
        }
    }
}

/// All defined metric label variants.
pub const ALL_LABELS: [MetricLabel; 4] = [
    MetricLabel::Reason,
    MetricLabel::Profile,
    MetricLabel::PathMode,
    MetricLabel::CacheValidation,
];

/// High-cardinality labels forbidden in the internal Rust metrics model.
///
/// These labels would create unbounded time series and are explicitly blocked.
const BLOCKED_LABELS: &[&str] = &[
    "url",
    "path",
    "uri",
    "host",
    "ip",
    "client_ip",
    "remote_addr",
    "user_agent",
    "ua",
    "request_id",
    "trace_id",
    "session_id",
];

/// Returns `true` if the key is in the internal Rust whitelist.
///
/// Matching is case-insensitive.
///
/// # Arguments
///
/// * `label` - The label key string to check.
///
/// # Examples
///
/// ```
/// use nginx_markdown_converter::metrics::is_label_allowed;
///
/// assert!(is_label_allowed("reason"));
/// assert!(is_label_allowed("PROFILE"));
/// assert!(!is_label_allowed("url"));
/// ```
pub fn is_label_allowed(label: &str) -> bool {
    let lower = label.to_ascii_lowercase();
    ALL_LABELS.iter().any(|l| l.as_str() == lower)
}

/// Returns `true` if the given label key is explicitly blocked as
/// high-cardinality.
///
/// Matching is case-insensitive.
///
/// # Arguments
///
/// * `label` - The label key string to check.
///
/// # Examples
///
/// ```
/// use nginx_markdown_converter::metrics::is_label_blocked;
///
/// assert!(is_label_blocked("url"));
/// assert!(is_label_blocked("User_Agent"));
/// assert!(!is_label_blocked("reason"));
/// ```
pub fn is_label_blocked(label: &str) -> bool {
    let lower = label.to_ascii_lowercase();
    BLOCKED_LABELS.contains(&lower.as_str())
}

/// Normalizes a label value to lowercase snake_case.
///
/// Applies the following transformations in order:
/// 1. Replace hyphens and spaces with underscores.
/// 2. Convert to lowercase.
/// 3. Remove non-alphanumeric/non-underscore characters.
/// 4. Collapse consecutive underscores.
/// 5. Trim leading/trailing underscores.
///
/// # Arguments
///
/// * `value` - The raw label value string.
///
/// # Returns
///
/// A normalized `String` in lowercase snake_case.
///
/// # Examples
///
/// ```
/// use nginx_markdown_converter::metrics::normalize_label_value;
///
/// assert_eq!(normalize_label_value("Full-Buffer"), "full_buffer");
/// assert_eq!(normalize_label_value("  streaming FIRST  "), "streaming_first");
/// ```
pub fn normalize_label_value(value: &str) -> String {
    // Step 1: Replace hyphens and spaces with underscores
    let replaced: String = value
        .chars()
        .map(|c| match c {
            '-' | ' ' => '_',
            _ => c,
        })
        .collect();

    // Step 2: Convert to lowercase
    let lowered = replaced.to_lowercase();

    // Step 3: Remove non-alphanumeric/non-underscore characters
    let filtered: String = lowered
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == '_')
        .collect();

    // Step 4: Collapse consecutive underscores
    let mut collapsed = String::with_capacity(filtered.len());
    let mut prev_underscore = false;
    for c in filtered.chars() {
        if c == '_' {
            if !prev_underscore {
                collapsed.push(c);
            }
            prev_underscore = true;
        } else {
            collapsed.push(c);
            prev_underscore = false;
        }
    }

    // Step 5: Trim leading/trailing underscores
    collapsed.trim_matches('_').to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── test_allowed_labels ──────────────────────────────────────────────

    #[test]
    fn test_allowed_labels_all_four_allowed() {
        assert!(is_label_allowed("reason"));
        assert!(is_label_allowed("profile"));
        assert!(is_label_allowed("path_mode"));
        assert!(is_label_allowed("cache_validation"));
    }

    #[test]
    fn test_allowed_labels_case_insensitive() {
        assert!(is_label_allowed("Reason"));
        assert!(is_label_allowed("PROFILE"));
        assert!(is_label_allowed("Path_Mode"));
        assert!(is_label_allowed("CACHE_VALIDATION"));
    }

    #[test]
    fn test_allowed_labels_rejects_unknown() {
        assert!(!is_label_allowed("url"));
        assert!(!is_label_allowed("method"));
        assert!(!is_label_allowed("status"));
        assert!(!is_label_allowed(""));
        assert!(!is_label_allowed("reasons"));
    }

    // ── test_blocked_labels ──────────────────────────────────────────────

    #[test]
    fn test_blocked_labels_all_blocked() {
        for label in BLOCKED_LABELS {
            assert!(is_label_blocked(label), "expected '{label}' to be blocked");
        }
    }

    #[test]
    fn test_blocked_labels_case_insensitive() {
        assert!(is_label_blocked("URL"));
        assert!(is_label_blocked("User_Agent"));
        assert!(is_label_blocked("CLIENT_IP"));
        assert!(is_label_blocked("Remote_Addr"));
    }

    #[test]
    fn test_blocked_labels_allows_valid() {
        assert!(!is_label_blocked("reason"));
        assert!(!is_label_blocked("profile"));
        assert!(!is_label_blocked("path_mode"));
        assert!(!is_label_blocked("cache_validation"));
    }

    // ── test_normalize_label_value ───────────────────────────────────────

    #[test]
    fn test_normalize_label_value_hyphens() {
        assert_eq!(normalize_label_value("full-buffer"), "full_buffer");
        assert_eq!(normalize_label_value("Full-Buffer"), "full_buffer");
    }

    #[test]
    fn test_normalize_label_value_spaces() {
        assert_eq!(normalize_label_value("streaming first"), "streaming_first");
        assert_eq!(
            normalize_label_value("  streaming  first  "),
            "streaming_first"
        );
    }

    #[test]
    fn test_normalize_label_value_uppercase() {
        assert_eq!(normalize_label_value("CONVERTED"), "converted");
        assert_eq!(normalize_label_value("SkippedAccept"), "skippedaccept");
    }

    #[test]
    fn test_normalize_label_value_special_chars() {
        assert_eq!(normalize_label_value("foo@bar!baz"), "foobarbaz");
        assert_eq!(normalize_label_value("hello...world"), "helloworld");
        assert_eq!(normalize_label_value("a--b__c"), "a_b_c");
    }

    #[test]
    fn test_normalize_label_value_empty() {
        assert_eq!(normalize_label_value(""), "");
        assert_eq!(normalize_label_value("___"), "");
        assert_eq!(normalize_label_value("---"), "");
    }

    #[test]
    fn test_normalize_label_value_already_normalized() {
        assert_eq!(normalize_label_value("converted"), "converted");
        assert_eq!(normalize_label_value("path_mode"), "path_mode");
    }

    // ── test_no_high_cardinality_labels ──────────────────────────────────

    #[test]
    fn test_no_high_cardinality_labels() {
        let high_cardinality = ["url", "path", "ip", "user_agent", "ua", "request_id"];
        for label in &high_cardinality {
            assert!(is_label_blocked(label), "expected '{label}' to be blocked");
            assert!(
                !is_label_allowed(label),
                "expected '{label}' to NOT be allowed"
            );
        }
    }

    // ── test_label_as_str ────────────────────────────────────────────────

    #[test]
    fn test_label_as_str() {
        assert_eq!(MetricLabel::Reason.as_str(), "reason");
        assert_eq!(MetricLabel::Profile.as_str(), "profile");
        assert_eq!(MetricLabel::PathMode.as_str(), "path_mode");
        assert_eq!(MetricLabel::CacheValidation.as_str(), "cache_validation");
    }

    #[test]
    fn test_all_labels_count() {
        assert_eq!(ALL_LABELS.len(), 4);
    }

    #[test]
    fn test_all_labels_as_str_unique() {
        let mut seen = std::collections::HashSet::new();
        for label in &ALL_LABELS {
            assert!(
                seen.insert(label.as_str()),
                "duplicate as_str() value: {}",
                label.as_str()
            );
        }
    }
}
