//! Status codes scenario — HTTP status code passthrough and redirect behavior.
//!
//! Migrated from `tools/e2e/verify_status_codes_e2e.sh`.
//! Validates that non-2xx status codes from upstream are properly handled:
//! 1. 403 Forbidden — no conversion, passthrough HTML
//! 2. 404 Not Found — no conversion, passthrough HTML
//! 3. 500 Internal Server Error — no conversion, passthrough
//! 4. 502 Bad Gateway — no conversion, passthrough
//! 5. 503 Service Unavailable — no conversion, passthrough
//! 6. 301 Redirect — not converted, preserves redirect status and Location
//! 7. 302 Redirect — not converted, preserves redirect status and Location
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

    // Case 6: 301 redirect handling. The shared client deliberately does not
    // follow redirects so the upstream status and Location header are tested.
    let url_301 = format!("{base_url}/md/301");
    if let Some(resp) = common::try_get_with_headers(
        &url_301,
        &headers,
        &mut assertions,
        "case6_301_redirect_301",
    ) {
        let preserved_redirect = resp.status == 301;
        assertions.push(AssertionResult {
            name: "case6_301_redirect_301".to_string(),
            passed: preserved_redirect,
            expected: "301 (without following redirect)".to_string(),
            actual: resp.status.to_string(),
            message: if preserved_redirect {
                None
            } else {
                Some(format!(
                    "[FAIL] assertion=case6_301_redirect_301 expected=301 actual={}",
                    resp.status
                ))
            },
        });
        assertions.push(assertions::assert_header_present(
            "case6_301_location",
            "Location",
            &resp.headers,
        ));
    }

    // Case 7: 302 redirect handling
    let url_302 = format!("{base_url}/md/302");
    if let Some(resp) = common::try_get_with_headers(
        &url_302,
        &headers,
        &mut assertions,
        "case7_302_redirect_302",
    ) {
        let preserved_redirect = resp.status == 302;
        assertions.push(AssertionResult {
            name: "case7_302_redirect_302".to_string(),
            passed: preserved_redirect,
            expected: "302 (without following redirect)".to_string(),
            actual: resp.status.to_string(),
            message: if preserved_redirect {
                None
            } else {
                Some(format!(
                    "[FAIL] assertion=case7_302_redirect_302 expected=302 actual={}",
                    resp.status
                ))
            },
        });
        assertions.push(assertions::assert_header_present(
            "case7_302_location",
            "Location",
            &resp.headers,
        ));
    }

    Ok(common::finalize_report(SCENARIO, start, assertions))
}
