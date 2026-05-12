//! Assertion engine for E2E scenario verification.
//!
//! Provides six assertion functions that produce `AssertionResult` values
//! with structured diagnostics on failure:
//!
//! - `assert_status` — HTTP status code check
//! - `assert_header_present` — Header existence check
//! - `assert_header_value` — Header value exact match
//! - `assert_header_pattern` — Header value regex match
//! - `assert_body_contains` — Body substring check
//! - `assert_body_matches` — Body regex match

use crate::scenarios::AssertionResult;
use regex::Regex;

/// Check that an HTTP status code matches the expected value.
///
/// # Arguments
///
/// * `name` - Assertion name.
/// * `actual` - Received status code.
/// * `expected` - Expected status code.
pub fn assert_status(name: &str, actual: u16, expected: u16) -> AssertionResult {
    let passed = actual == expected;
    AssertionResult {
        name: name.to_string(),
        passed,
        expected: expected.to_string(),
        actual: actual.to_string(),
        message: if passed {
            None
        } else {
            Some(format!(
                "[FAIL] assertion={name} expected={expected} actual={actual}"
            ))
        },
    }
}

/// Check that a header with the given name is present.
///
/// # Arguments
///
/// * `name` - Assertion name.
/// * `header_name` - HTTP header name to look for.
/// * `headers` - Header map to search.
pub fn assert_header_present(
    name: &str,
    header_name: &str,
    headers: &reqwest::header::HeaderMap,
) -> AssertionResult {
    let passed = headers.get(header_name).is_some();
    AssertionResult {
        name: name.to_string(),
        passed,
        expected: format!("header '{header_name}' present"),
        actual: if passed {
            format!("header '{header_name}' present")
        } else {
            format!("header '{header_name}' absent")
        },
        message: if passed {
            None
        } else {
            Some(format!(
                "[FAIL] assertion={name} header={header_name} expected=present actual=absent"
            ))
        },
    }
}

/// Check that a header value exactly matches the expected string.
///
/// # Arguments
///
/// * `name` - Assertion name.
/// * `header_name` - HTTP header name.
/// * `expected` - Expected header value.
/// * `headers` - Header map to search.
pub fn assert_header_value(
    name: &str,
    header_name: &str,
    expected: &str,
    headers: &reqwest::header::HeaderMap,
) -> AssertionResult {
    let actual_value = headers
        .get(header_name)
        .and_then(|v: &reqwest::header::HeaderValue| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    let passed = actual_value == expected;
    AssertionResult {
        name: name.to_string(),
        passed,
        expected: expected.to_string(),
        actual: actual_value.clone(),
        message: if passed {
            None
        } else {
            Some(format!(
                "[FAIL] assertion={name} header={header_name} expected='{expected}' actual='{actual_value}'"
            ))
        },
    }
}

/// Check that a header value matches a regex pattern.
///
/// # Arguments
///
/// * `name` - Assertion name.
/// * `header_name` - HTTP header name.
/// * `pattern` - Regex pattern string.
/// * `headers` - Header map to search.
pub fn assert_header_pattern(
    name: &str,
    header_name: &str,
    pattern: &str,
    headers: &reqwest::header::HeaderMap,
) -> AssertionResult {
    let actual_value = headers
        .get(header_name)
        .and_then(|v: &reqwest::header::HeaderValue| v.to_str().ok())
        .unwrap_or("")
        .to_string();
    let re = Regex::new(pattern).unwrap_or_else(|_| Regex::new("$^").unwrap());
    let passed = re.is_match(&actual_value);
    AssertionResult {
        name: name.to_string(),
        passed,
        expected: format!("matches /{pattern}/"),
        actual: actual_value.clone(),
        message: if passed {
            None
        } else {
            Some(format!(
                "[FAIL] assertion={name} header={header_name} pattern=/{pattern}/ actual='{actual_value}'"
            ))
        },
    }
}

/// Check that the response body contains a substring.
///
/// # Arguments
///
/// * `name` - Assertion name.
/// * `needle` - Substring to search for.
/// * `body` - Response body text.
pub fn assert_body_contains(name: &str, needle: &str, body: &str) -> AssertionResult {
    let passed = body.contains(needle);
    AssertionResult {
        name: name.to_string(),
        passed,
        expected: format!("body contains '{needle}'"),
        actual: if passed {
            format!("body contains '{needle}'")
        } else {
            "substring not found".to_string()
        },
        message: if passed {
            None
        } else {
            Some(format!(
                "[FAIL] assertion={name} needle='{needle}' not found in body"
            ))
        },
    }
}

/// Check that the response body matches a regex pattern.
///
/// # Arguments
///
/// * `name` - Assertion name.
/// * `pattern` - Regex pattern string.
/// * `body` - Response body text.
pub fn assert_body_matches(name: &str, pattern: &str, body: &str) -> AssertionResult {
    let re = Regex::new(pattern).unwrap_or_else(|_| Regex::new("$^").unwrap());
    let passed = re.is_match(body);
    AssertionResult {
        name: name.to_string(),
        passed,
        expected: format!("body matches /{pattern}/"),
        actual: if passed {
            format!("body matches /{pattern}/")
        } else {
            "pattern not matched".to_string()
        },
        message: if passed {
            None
        } else {
            Some(format!(
                "[FAIL] assertion={name} pattern=/{pattern}/ not matched in body"
            ))
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_assert_status_pass() {
        let r = assert_status("status_ok", 200, 200);
        assert!(r.passed);
        assert_eq!(r.expected, "200");
        assert_eq!(r.actual, "200");
    }

    #[test]
    fn test_assert_status_fail() {
        let r = assert_status("status_ok", 500, 200);
        assert!(!r.passed);
        assert!(r.message.is_some());
        assert!(r.message.as_ref().unwrap().contains("assertion=status_ok"));
    }

    #[test]
    fn test_assert_body_contains_pass() {
        let r = assert_body_contains("has_hello", "hello", "hello world");
        assert!(r.passed);
    }

    #[test]
    fn test_assert_body_contains_fail() {
        let r = assert_body_contains("has_hello", "hello", "goodbye world");
        assert!(!r.passed);
        assert!(r.message.is_some());
    }

    #[test]
    fn test_assert_body_matches_pass() {
        let r = assert_body_matches("is_json", r#"^\{.*\}$"#, r#"{"key":"value"}"#);
        assert!(r.passed);
    }

    #[test]
    fn test_assert_body_matches_fail() {
        let r = assert_body_matches("is_json", r#"^\{.*\}$"#, "not json");
        assert!(!r.passed);
    }
}
