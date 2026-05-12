//! Auth cache scenario — auth cookie detection, Cache-Control, ETag, Vary behavior.
//!
//! Migrated from `tools/e2e/verify_auth_cache_e2e.sh`.
//! Validates critical auth/cache paths:
//! 1. Auth request with Cookie: session=abc gets Cache-Control: private
//! 2. Non-auth request retains upstream Cache-Control: public
//! 3. markdown_auth_policy deny rejects conversion for auth requests
//! 4. markdown_auth_cookies pattern matching (session_* regex)
//! 5. Auth fail-open preserves Cache-Control from upstream
//! 6. Non-auth ETag replacement
//! 7. Vary: Cookie in auth response

use crate::assertions;
use crate::http;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

/// Upstream ETag value used in the fixture server.
const UPSTREAM_ETAG: &str = "\"upstream-auth-etag-001\"";

/// Run the auth-cache scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    let nginx_bin = match &ctx.mode {
        crate::runtime::RuntimeMode::Reuse(path) => path.clone(),
        crate::runtime::RuntimeMode::Bootstrap => {
            return Ok(ScenarioReport::failing(
                "auth-cache",
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
    let md_url = format!("{base_url}/md/html");
    let md_deny_url = format!("{base_url}/md-deny/html");

    let mut markdown_headers = HashMap::new();
    markdown_headers.insert("Accept".to_string(), "text/markdown".to_string());

    let mut auth_headers = HashMap::new();
    auth_headers.insert("Accept".to_string(), "text/markdown".to_string());
    auth_headers.insert("Cookie".to_string(), "session_user=abc123".to_string());

    let mut nonmatching_cookie_headers = HashMap::new();
    nonmatching_cookie_headers.insert("Accept".to_string(), "text/markdown".to_string());
    nonmatching_cookie_headers.insert("Cookie".to_string(), "preferences=dark".to_string());

    // Case 1: Auth request with Cookie gets Cache-Control: private
    if let Ok(resp1) = http::get_with_headers(&md_url, &auth_headers) {
        assertions.push(assertions::assert_status(
            "case1_auth_status_200",
            resp1.status,
            200,
        ));
        let cc_value = header_value(&resp1.headers, "Cache-Control");
        let has_private = cc_value.contains("private");
        assertions.push(AssertionResult {
            name: "case1_cache_control_private".to_string(),
            passed: has_private,
            expected: "Cache-Control contains private".to_string(),
            actual: cc_value,
            message: None,
        });
    } else {
        assertions.push(failed_assertion(
            "case1_cache_control_private",
            "request failed",
        ));
    }

    // Case 2: Non-auth request retains Cache-Control: public
    if let Ok(resp2) = http::get_with_headers(&md_url, &markdown_headers) {
        assertions.push(assertions::assert_status(
            "case2_noauth_status_200",
            resp2.status,
            200,
        ));
        let cc_value = header_value(&resp2.headers, "Cache-Control");
        let has_public = cc_value.contains("public");
        assertions.push(AssertionResult {
            name: "case2_cache_control_public".to_string(),
            passed: has_public,
            expected: "Cache-Control contains public".to_string(),
            actual: cc_value,
            message: None,
        });
    } else {
        assertions.push(failed_assertion(
            "case2_cache_control_public",
            "request failed",
        ));
    }

    // Case 3: auth_policy deny rejects conversion for auth requests
    if let Ok(resp3) = http::get_with_headers(&md_deny_url, &auth_headers) {
        assertions.push(assertions::assert_status(
            "case3_deny_status_200",
            resp3.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case3_deny_content_type_html",
            "Content-Type",
            "text/html",
            &resp3.headers,
        ));
    } else {
        assertions.push(failed_assertion(
            "case3_deny_content_type_html",
            "request failed",
        ));
    }

    // Case 4: auth_cookies pattern matching — non-matching cookie
    if let Ok(resp4) = http::get_with_headers(&md_url, &nonmatching_cookie_headers) {
        assertions.push(assertions::assert_status(
            "case4_nonmatch_cookie_status_200",
            resp4.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case4_nonmatch_cookie_markdown_ct",
            "Content-Type",
            "text/markdown",
            &resp4.headers,
        ));
    } else {
        assertions.push(failed_assertion(
            "case4_nonmatch_cookie_markdown_ct",
            "request failed",
        ));
    }

    // Case 5: Auth fail-open preserves upstream Cache-Control
    if let Ok(resp5) = http::get_with_headers(&md_url, &auth_headers) {
        let auth_cc = header_value(&resp5.headers, "Cache-Control");
        let has_cc = !auth_cc.is_empty();
        assertions.push(AssertionResult {
            name: "case5_auth_preserves_cache_control".to_string(),
            passed: has_cc,
            expected: "Cache-Control header present".to_string(),
            actual: if has_cc {
                auth_cc.clone()
            } else {
                "absent".to_string()
            },
            message: None,
        });
    } else {
        assertions.push(failed_assertion(
            "case5_auth_preserves_cache_control",
            "request failed",
        ));
    }

    // Case 6: Non-auth ETag replacement
    if let Ok(resp6) = http::get_with_headers(&md_url, &markdown_headers) {
        let md_etag = header_value(&resp6.headers, "ETag");
        let etag_present = !md_etag.is_empty();
        assertions.push(AssertionResult {
            name: "case6_etag_present".to_string(),
            passed: etag_present,
            expected: "ETag header present".to_string(),
            actual: if etag_present {
                md_etag.clone()
            } else {
                "absent".to_string()
            },
            message: None,
        });
        let etag_differs = md_etag != UPSTREAM_ETAG;
        assertions.push(AssertionResult {
            name: "case6_etag_differs_from_upstream".to_string(),
            passed: etag_differs,
            expected: format!("ETag != {UPSTREAM_ETAG}"),
            actual: md_etag,
            message: None,
        });
    } else {
        assertions.push(failed_assertion(
            "case6_etag_differs_from_upstream",
            "request failed",
        ));
    }

    // Case 7: Vary: Cookie in auth response
    if let Ok(resp7) = http::get_with_headers(&md_url, &auth_headers) {
        let vary_value = header_value(&resp7.headers, "Vary");
        let has_vary_cookie = vary_value.contains("Cookie");
        assertions.push(AssertionResult {
            name: "case7_vary_cookie".to_string(),
            passed: has_vary_cookie,
            expected: "Vary header contains Cookie".to_string(),
            actual: if vary_value.is_empty() {
                "Vary absent".to_string()
            } else {
                vary_value
            },
            message: None,
        });
    } else {
        assertions.push(failed_assertion("case7_vary_cookie", "request failed"));
    }

    let elapsed = start.elapsed().as_millis() as u64;
    let passed = assertions.iter().all(|a| a.passed);
    Ok(ScenarioReport {
        name: "auth-cache".to_string(),
        passed,
        assertions,
        elapsed_ms: elapsed,
        failure_message: if passed {
            None
        } else {
            Some("auth-cache failed".to_string())
        },
    })
}

/// Extract a header value as a string, returning empty string if absent.
fn header_value(headers: &reqwest::header::HeaderMap, name: &str) -> String {
    headers
        .get(name)
        .and_then(|v: &reqwest::header::HeaderValue| v.to_str().ok())
        .unwrap_or("")
        .to_string()
}

/// Create a skipped scenario report.
fn skipped_report(start: std::time::Instant, reason: &str) -> ScenarioReport {
    ScenarioReport {
        name: "auth-cache".to_string(),
        passed: false,
        assertions: vec![],
        elapsed_ms: start.elapsed().as_millis() as u64,
        failure_message: Some(format!("SKIPPED: {reason}")),
    }
}

/// Create a failed assertion result for a request error.
fn failed_assertion(name: &str, reason: &str) -> AssertionResult {
    AssertionResult {
        name: name.to_string(),
        passed: false,
        expected: "request succeeds".to_string(),
        actual: format!("request failed: {reason}"),
        message: Some(format!("[FAIL] assertion={name} {reason}")),
    }
}
