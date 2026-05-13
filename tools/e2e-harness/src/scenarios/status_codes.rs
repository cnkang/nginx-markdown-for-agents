//! Status codes scenario — HTTP status code passthrough and redirect behavior.
//!
//! Migrated from `tools/e2e/verify_status_codes_e2e.sh`.
//! Validates that non-2xx status codes from upstream are properly handled:
//! 1. 403 Forbidden — no conversion, passthrough HTML
//! 2. 404 Not Found — no conversion, passthrough HTML
//! 3. 500 Internal Server Error — no conversion, passthrough
//! 4. 502 Bad Gateway — no conversion, passthrough
//! 5. 503 Service Unavailable — no conversion, passthrough
//! 6. 301 Redirect — not converted, follows redirect
//! 7. 302 Redirect — not converted, follows redirect
//! 8. 410 Gone — no conversion, passthrough

use crate::assertions;
use crate::http;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

/// Run the status-codes scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    let nginx_bin = match &ctx.mode {
        crate::runtime::RuntimeMode::Reuse(path) => path.clone(),
        crate::runtime::RuntimeMode::Bootstrap => {
            return Ok(ScenarioReport::failing(
                "status-codes",
                assertions,
                start.elapsed().as_millis() as u64,
                "Bootstrap mode not yet supported".to_string(),
            ));
        }
    };

    if !nginx_bin.exists() {
        return Ok(skipped_report(start, "NGINX binary not found"));
    }

    let base_url = format!("http://127.0.0.1:{}", ctx.port);

    let mut headers = HashMap::new();
    headers.insert("Accept".to_string(), "text/markdown".to_string());

    // Cases 1-6,8: Error status code passthrough — no conversion, HTML content type
    let error_codes: &[(u16, &str)] = &[
        (403, "403"),
        (404, "404"),
        (500, "500"),
        (502, "502"),
        (503, "503"),
        (410, "410"),
    ];

    for &(status, path) in error_codes {
        let url = format!("{base_url}/md/{path}");
        if let Ok(resp) = http::get_with_headers(&url, &headers) {
            assertions.push(assertions::assert_status(
                &format!("case{status}_status_passthrough"),
                resp.status,
                status,
            ));
            assertions.push(assertions::assert_header_pattern(
                &format!("case{status}_content_type_html"),
                "Content-Type",
                "text/html",
                &resp.headers,
            ));
        } else {
            assertions.push(failed_assertion(
                &format!("case{status}_status_passthrough"),
                "request failed",
            ));
        }
    }

    // Case 7: 301 redirect handling
    let url_301 = format!("{base_url}/md/301");
    if let Ok(resp) = http::get_with_headers(&url_301, &headers) {
        let redirected_to_200 = resp.status == 200;
        assertions.push(AssertionResult {
            name: "case7_301_redirect_200".to_string(),
            passed: redirected_to_200,
            expected: "200 (after following redirect)".to_string(),
            actual: resp.status.to_string(),
            message: if redirected_to_200 {
                None
            } else {
                Some(format!(
                    "[FAIL] assertion=case7_301_redirect_200 expected=200 actual={}",
                    resp.status
                ))
            },
        });
    } else {
        assertions.push(failed_assertion("case7_301_redirect_200", "request failed"));
    }

    // Case 8 (302 in the shell is case 8): 302 redirect handling
    let url_302 = format!("{base_url}/md/302");
    if let Ok(resp) = http::get_with_headers(&url_302, &headers) {
        let redirected_to_200 = resp.status == 200;
        assertions.push(AssertionResult {
            name: "case8_302_redirect_200".to_string(),
            passed: redirected_to_200,
            expected: "200 (after following redirect)".to_string(),
            actual: resp.status.to_string(),
            message: if redirected_to_200 {
                None
            } else {
                Some(format!(
                    "[FAIL] assertion=case8_302_redirect_200 expected=200 actual={}",
                    resp.status
                ))
            },
        });
    } else {
        assertions.push(failed_assertion("case8_302_redirect_200", "request failed"));
    }

    let elapsed = start.elapsed().as_millis() as u64;
    let passed = assertions.iter().all(|a| a.passed);
    Ok(ScenarioReport {
        name: "status-codes".to_string(),
        passed,
        assertions,
        elapsed_ms: elapsed,
        failure_message: if passed {
            None
        } else {
            Some("status-codes failed".to_string())
        },
    })
}

/// Create a skipped scenario report with a reason message.
fn skipped_report(start: std::time::Instant, reason: &str) -> ScenarioReport {
    ScenarioReport {
        name: "status-codes".to_string(),
        passed: false,
        assertions: vec![],
        elapsed_ms: start.elapsed().as_millis() as u64,
        failure_message: Some(format!("SKIPPED: {reason}")),
    }
}

/// Create a failed assertion result for an HTTP request error.
fn failed_assertion(name: &str, reason: &str) -> AssertionResult {
    AssertionResult {
        name: name.to_string(),
        passed: false,
        expected: "request succeeds".to_string(),
        actual: format!("request failed: {reason}"),
        message: Some(format!("[FAIL] assertion={name} {reason}")),
    }
}
