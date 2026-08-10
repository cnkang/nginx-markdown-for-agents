//! Shared scenario helpers to keep per-scenario code focused on assertions.

use crate::http::{self, HttpResponse};
use crate::runtime::RuntimeMode;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use std::collections::HashMap;
use std::time::Instant;

/// Ensure the scenario runs in reuse mode and the configured NGINX binary exists.
pub fn ensure_reuse_nginx_binary(
    ctx: &ScenarioContext,
    scenario_name: &str,
    start: Instant,
) -> Result<(), ScenarioReport> {
    let nginx_bin = match &ctx.mode {
        RuntimeMode::Reuse(path) => path,
        RuntimeMode::Bootstrap => {
            return Err(ScenarioReport::failing(
                scenario_name,
                vec![],
                start.elapsed().as_millis() as u64,
                "Bootstrap mode not yet supported".to_string(),
            ));
        }
    };

    if !nginx_bin.exists() {
        return Err(skipped_report(
            start,
            scenario_name,
            "NGINX binary not found",
        ));
    }

    Ok(())
}

/// Create a skipped scenario report.
pub fn skipped_report(start: Instant, scenario_name: &str, reason: &str) -> ScenarioReport {
    ScenarioReport {
        name: scenario_name.to_string(),
        passed: false,
        assertions: vec![],
        elapsed_ms: start.elapsed().as_millis() as u64,
        failure_message: Some(format!("SKIPPED: {reason}")),
    }
}

/// Finalize a scenario report from assertion outcomes.
pub fn finalize_report(
    scenario_name: &str,
    start: Instant,
    assertions: Vec<AssertionResult>,
) -> ScenarioReport {
    let passed = assertions.iter().all(|a| a.passed);
    ScenarioReport {
        name: scenario_name.to_string(),
        passed,
        assertions,
        elapsed_ms: start.elapsed().as_millis() as u64,
        failure_message: if passed {
            None
        } else {
            Some(format!("{scenario_name} failed"))
        },
    }
}

/// Create a failed assertion result for a request error.
pub fn failed_request_assertion(name: &str, reason: &str) -> AssertionResult {
    AssertionResult {
        name: name.to_string(),
        passed: false,
        expected: "request succeeds".to_string(),
        actual: format!("request failed: {reason}"),
        message: Some(format!("[FAIL] assertion={name} {reason}")),
    }
}

/// Send GET with headers and append a failed assertion on error.
pub fn try_get_with_headers(
    url: &str,
    headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
    failure_assertion_name: &str,
) -> Option<HttpResponse> {
    match http::get_with_headers(url, headers) {
        Ok(resp) => Some(resp),
        Err(e) => {
            assertions.push(failed_request_assertion(
                failure_assertion_name,
                &e.to_string(),
            ));
            None
        }
    }
}

/// Send HEAD and append a failed assertion on error.
pub fn try_head(
    url: &str,
    assertions: &mut Vec<AssertionResult>,
    failure_assertion_name: &str,
) -> Option<HttpResponse> {
    match http::head(url) {
        Ok(resp) => Some(resp),
        Err(e) => {
            assertions.push(failed_request_assertion(
                failure_assertion_name,
                &e.to_string(),
            ));
            None
        }
    }
}

/// Extract a header value as a string, returning empty string if absent.
pub fn header_value(headers: &reqwest::header::HeaderMap, name: &str) -> String {
    headers
        .get(name)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string()
}

/// Check a converted Markdown body for a fixture token.
///
/// The converter escapes underscores in ordinary text to prevent accidental
/// Markdown emphasis.  Keep accepting the raw form as well so this assertion
/// remains compatible with fixtures that emit a token in a non-Markdown body.
pub fn markdown_token_present(body: &str, token: &str) -> bool {
    if body.contains(token) {
        return true;
    }

    let escaped_token = token.replace('_', r"\_");
    body.contains(&escaped_token)
}

/// Check a wire response for a fixture token in raw or Markdown-escaped form.
pub fn markdown_token_bytes_present(body: &[u8], token: &str) -> bool {
    let raw_token = token.as_bytes();
    if body
        .windows(raw_token.len())
        .any(|window| window == raw_token)
    {
        return true;
    }

    let escaped_token = token.replace('_', r"\_");
    body.windows(escaped_token.len())
        .any(|window| window == escaped_token.as_bytes())
}

#[cfg(test)]
mod tests {
    use super::{markdown_token_bytes_present, markdown_token_present};

    const TOKEN: &str = "BROTLI_SMALL_STREAM_END";

    #[test]
    fn markdown_token_present_accepts_raw_and_escaped_forms() {
        assert!(markdown_token_present(
            "prefix BROTLI_SMALL_STREAM_END",
            TOKEN
        ));
        assert!(markdown_token_present(
            r"prefix BROTLI\_SMALL\_STREAM\_END",
            TOKEN
        ));
        assert!(!markdown_token_present(
            "prefix BROTLI-SMALL-STREAM-END",
            TOKEN
        ));
    }

    #[test]
    fn markdown_token_bytes_present_accepts_raw_and_escaped_forms() {
        assert!(markdown_token_bytes_present(
            b"chunk BROTLI_SMALL_STREAM_END chunk",
            TOKEN
        ));
        assert!(markdown_token_bytes_present(
            br"chunk BROTLI\_SMALL\_STREAM\_END chunk",
            TOKEN
        ));
        assert!(!markdown_token_bytes_present(
            b"chunk without marker",
            TOKEN
        ));
    }
}
