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
use crate::scenarios::common;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

/// Run the status-codes scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    const SCENARIO: &str = "status-codes";
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    if let Err(report) = common::ensure_reuse_nginx_binary(&ctx, SCENARIO, start) {
        return Ok(report);
    }

    let base_url = format!("http://127.0.0.1:{}", ctx.port);

    let mut headers = HashMap::new();
    headers.insert("Accept".to_string(), "text/markdown".to_string());

    // Cases 1-5,8: Error status code passthrough — no conversion, HTML content type
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
        if let Some(resp) = common::try_get_with_headers(
            &url,
            &headers,
            &mut assertions,
            &format!("case{status}_status_passthrough"),
        ) {
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
        }
    }

    // Case 6: 301 redirect handling
    let url_301 = format!("{base_url}/md/301");
    if let Some(resp) = common::try_get_with_headers(
        &url_301,
        &headers,
        &mut assertions,
        "case6_301_redirect_200",
    ) {
        let redirected_to_200 = resp.status == 200;
        assertions.push(AssertionResult {
            name: "case6_301_redirect_200".to_string(),
            passed: redirected_to_200,
            expected: "200 (after following redirect)".to_string(),
            actual: resp.status.to_string(),
            message: if redirected_to_200 {
                None
            } else {
                Some(format!(
                    "[FAIL] assertion=case6_301_redirect_200 expected=200 actual={}",
                    resp.status
                ))
            },
        });
    }

    // Case 7: 302 redirect handling
    let url_302 = format!("{base_url}/md/302");
    if let Some(resp) = common::try_get_with_headers(
        &url_302,
        &headers,
        &mut assertions,
        "case7_302_redirect_200",
    ) {
        let redirected_to_200 = resp.status == 200;
        assertions.push(AssertionResult {
            name: "case7_302_redirect_200".to_string(),
            passed: redirected_to_200,
            expected: "200 (after following redirect)".to_string(),
            actual: resp.status.to_string(),
            message: if redirected_to_200 {
                None
            } else {
                Some(format!(
                    "[FAIL] assertion=case7_302_redirect_200 expected=200 actual={}",
                    resp.status
                ))
            },
        });
    }

    Ok(common::finalize_report(SCENARIO, start, assertions))
}
