//! Conditional requests scenario — ETag / If-None-Match / If-Modified-Since.
//!
//! Migrated from `tools/e2e/verify_conditional_requests_e2e.sh`.
//! Exercises NGINX conditional request handling for Markdown conversion.
//!
//! Test cases:
//! 1. Converted response contains ETag header
//! 2. ETag differs from upstream original ETag
//! 3. If-None-Match match returns 304
//! 4. If-None-Match non-match returns 200
//! 5. If-Modified-Since future returns 304
//! 6. If-Modified-Since past returns 200
//! 7. Weak ETag (W/"") match returns 304
//! 8. Wildcard If-None-Match: * returns 304
//! 9. 304 response contains Vary: Accept
//! 10. HEAD request returns same ETag as GET

use crate::assertions;
use crate::http;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

/// Upstream ETag value used in the fixture server.
const UPSTREAM_ETAG: &str = "\"upstream-original-etag-12345\"";

/// Run the conditional-requests scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    let nginx_bin = match &ctx.mode {
        crate::runtime::RuntimeMode::Reuse(path) => path.clone(),
        crate::runtime::RuntimeMode::Bootstrap => {
            return Ok(ScenarioReport::failing(
                "conditional-requests",
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
    let url = format!("{base_url}/md/html");

    let mut headers = HashMap::new();
    headers.insert("Accept".to_string(), "text/markdown".to_string());

    // Case 1: Converted response contains ETag header
    let resp = match http::get_with_headers(&url, &headers) {
        Ok(r) => r,
        Err(e) => {
            return Ok(ScenarioReport::failing(
                "conditional-requests",
                assertions,
                start.elapsed().as_millis() as u64,
                format!("Failed to connect to NGINX: {e}"),
            ));
        }
    };

    assertions.push(assertions::assert_status(
        "case1_status_200",
        resp.status,
        200,
    ));
    assertions.push(assertions::assert_header_present(
        "case1_etag_present",
        "ETag",
        &resp.headers,
    ));

    let response_etag = resp
        .headers
        .get("ETag")
        .and_then(|v: &reqwest::header::HeaderValue| v.to_str().ok())
        .unwrap_or("")
        .to_string();

    // Case 2: ETag differs from upstream original ETag
    let etag_differs = response_etag != UPSTREAM_ETAG;
    assertions.push(AssertionResult {
        name: "case2_etag_differs_from_upstream".to_string(),
        passed: etag_differs,
        expected: format!("ETag != {UPSTREAM_ETAG}"),
        actual: response_etag.clone(),
        message: if etag_differs {
            None
        } else {
            Some(format!(
                "[FAIL] assertion=case2 ETag matches upstream: {response_etag}"
            ))
        },
    });

    // Case 3: If-None-Match match returns 304
    let mut inm_headers = headers.clone();
    inm_headers.insert("If-None-Match".to_string(), response_etag.clone());
    if let Ok(resp3) = http::get_with_headers(&url, &inm_headers) {
        assertions.push(assertions::assert_status(
            "case3_inm_match_304",
            resp3.status,
            304,
        ));
        // Case 9: 304 response contains Vary: Accept
        let vary_check = resp3.headers.get("Vary").is_some();
        assertions.push(AssertionResult {
            name: "case9_vary_accept_in_304".to_string(),
            passed: vary_check,
            expected: "Vary header present".to_string(),
            actual: if vary_check {
                "present".to_string()
            } else {
                "absent".to_string()
            },
            message: None,
        });
    } else {
        assertions.push(failed_assertion("case3_inm_match_304", "request failed"));
    }

    // Case 4: If-None-Match non-match returns 200
    let mut inm_nomatch_headers = headers.clone();
    inm_nomatch_headers.insert(
        "If-None-Match".to_string(),
        "\"non-matching-etag-99999\"".to_string(),
    );
    if let Ok(resp4) = http::get_with_headers(&url, &inm_nomatch_headers) {
        assertions.push(assertions::assert_status(
            "case4_inm_nomatch_200",
            resp4.status,
            200,
        ));
    } else {
        assertions.push(failed_assertion("case4_inm_nomatch_200", "request failed"));
    }

    // Case 5: If-Modified-Since future returns 304
    let mut ims_future_headers = headers.clone();
    ims_future_headers.insert(
        "If-Modified-Since".to_string(),
        "Mon, 01 Jan 2030 00:00:00 GMT".to_string(),
    );
    if let Ok(resp5) = http::get_with_headers(&url, &ims_future_headers) {
        assertions.push(assertions::assert_status(
            "case5_ims_future_304",
            resp5.status,
            304,
        ));
    } else {
        assertions.push(failed_assertion("case5_ims_future_304", "request failed"));
    }

    // Case 6: If-Modified-Since past returns 200
    let mut ims_past_headers = headers.clone();
    ims_past_headers.insert(
        "If-Modified-Since".to_string(),
        "Mon, 01 Jan 2020 00:00:00 GMT".to_string(),
    );
    if let Ok(resp6) = http::get_with_headers(&url, &ims_past_headers) {
        assertions.push(assertions::assert_status(
            "case6_ims_past_200",
            resp6.status,
            200,
        ));
    } else {
        assertions.push(failed_assertion("case6_ims_past_200", "request failed"));
    }

    // Case 7: Weak ETag (W/"") match returns 304
    let weak_etag = format!("W/{response_etag}");
    let mut weak_headers = headers.clone();
    weak_headers.insert("If-None-Match".to_string(), weak_etag);
    if let Ok(resp7) = http::get_with_headers(&url, &weak_headers) {
        assertions.push(assertions::assert_status(
            "case7_weak_etag_304",
            resp7.status,
            304,
        ));
    } else {
        assertions.push(failed_assertion("case7_weak_etag_304", "request failed"));
    }

    // Case 8: Wildcard If-None-Match: * returns 304
    let mut wildcard_headers = headers.clone();
    wildcard_headers.insert("If-None-Match".to_string(), "*".to_string());
    if let Ok(resp8) = http::get_with_headers(&url, &wildcard_headers) {
        assertions.push(assertions::assert_status(
            "case8_wildcard_inm_304",
            resp8.status,
            304,
        ));
    } else {
        assertions.push(failed_assertion("case8_wildcard_inm_304", "request failed"));
    }

    // Case 10: HEAD request returns same ETag as GET
    if let Ok(resp10) = http::head(&url) {
        assertions.push(assertions::assert_status(
            "case10_head_200",
            resp10.status,
            200,
        ));
        let head_etag = resp10
            .headers
            .get("ETag")
            .and_then(|v: &reqwest::header::HeaderValue| v.to_str().ok())
            .unwrap_or("")
            .to_string();
        let etag_matches = head_etag == response_etag;
        assertions.push(AssertionResult {
            name: "case10_head_etag_matches_get".to_string(),
            passed: etag_matches,
            expected: response_etag.clone(),
            actual: head_etag,
            message: None,
        });
    } else {
        assertions.push(failed_assertion(
            "case10_head_etag_matches_get",
            "request failed",
        ));
    }

    let elapsed = start.elapsed().as_millis() as u64;
    let passed = assertions.iter().all(|a| a.passed);
    Ok(ScenarioReport {
        name: "conditional-requests".to_string(),
        passed,
        assertions,
        elapsed_ms: elapsed,
        failure_message: if passed {
            None
        } else {
            Some("conditional-requests failed".to_string())
        },
    })
}

/// Create a skipped scenario report.
fn skipped_report(start: std::time::Instant, reason: &str) -> ScenarioReport {
    ScenarioReport {
        name: "conditional-requests".to_string(),
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
