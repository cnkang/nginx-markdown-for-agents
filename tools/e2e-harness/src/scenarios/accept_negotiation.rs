//! Accept negotiation scenario — Accept header content negotiation.
//!
//! Migrated from `tools/e2e/verify_accept_negotiation_e2e.sh`.
//! Validates critical content-negotiation paths:
//! 1. Accept: text/markdown triggers conversion (text/markdown response)
//! 2. Accept: text/html returns original HTML (no conversion)
//! 3. No Accept header returns original HTML (default behavior)
//! 4. Accept: */* with markdown_on_wildcard on triggers conversion
//! 5. Accept: */* with markdown_on_wildcard off does NOT trigger conversion
//! 6. Vary: Accept header present in converted responses
//! 7. Non-HTML Content-Type from upstream is not converted
//! 8. text/plain Content-Type is not converted

use crate::assertions;
use crate::http;
use crate::scenarios::common;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

/// Run the accept-negotiation scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    const SCENARIO: &str = "accept-negotiation";
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    if let Err(report) = common::ensure_reuse_nginx_binary(&ctx, SCENARIO, start) {
        return Ok(report);
    }

    let base_url = format!("http://127.0.0.1:{}", ctx.port);
    let md_html_url = format!("{base_url}/md/html");
    let no_wildcard_html_url = format!("{base_url}/no-wildcard/html");
    let md_json_url = format!("{base_url}/md/json");
    let md_plain_url = format!("{base_url}/md/plain");

    let mut markdown_headers = HashMap::new();
    markdown_headers.insert("Accept".to_string(), "text/markdown".to_string());

    let mut html_headers = HashMap::new();
    html_headers.insert("Accept".to_string(), "text/html".to_string());

    let mut wildcard_headers = HashMap::new();
    wildcard_headers.insert("Accept".to_string(), "*/*".to_string());

    let no_accept_headers = HashMap::new();

    // Case 1: Accept: text/markdown triggers conversion
    let resp1 = match http::get_with_headers(&md_html_url, &markdown_headers) {
        Ok(r) => r,
        Err(e) => {
            return Ok(ScenarioReport::failing(
                SCENARIO,
                assertions,
                start.elapsed().as_millis() as u64,
                format!("Case 1: failed to connect: {e}"),
            ));
        }
    };
    assertions.push(assertions::assert_status(
        "case1_markdown_accept_status_200",
        resp1.status,
        200,
    ));
    assertions.push(assertions::assert_header_pattern(
        "case1_markdown_accept_ct",
        "Content-Type",
        "text/markdown",
        &resp1.headers,
    ));
    assertions.push(assertions::assert_body_contains(
        "case1_markdown_accept_has_heading",
        "# ",
        &resp1.body,
    ));

    // Case 2: Accept: text/html returns original HTML
    if let Some(resp2) = common::try_get_with_headers(
        &md_html_url,
        &html_headers,
        &mut assertions,
        "case2_html_accept_conn_error",
    ) {
        assertions.push(assertions::assert_status(
            "case2_html_accept_status_200",
            resp2.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case2_html_accept_ct",
            "Content-Type",
            "text/html",
            &resp2.headers,
        ));
        assertions.push(assertions::assert_body_contains(
            "case2_html_accept_has_h1",
            "<h1>",
            &resp2.body,
        ));
    }

    // Case 3: No Accept header returns original HTML (default)
    if let Some(resp3) = common::try_get_with_headers(
        &md_html_url,
        &no_accept_headers,
        &mut assertions,
        "case3_no_accept_conn_error",
    ) {
        assertions.push(assertions::assert_status(
            "case3_no_accept_status_200",
            resp3.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case3_no_accept_ct_html",
            "Content-Type",
            "text/html",
            &resp3.headers,
        ));
    }

    // Case 4: Accept: */* with markdown_on_wildcard on triggers conversion
    if let Some(resp4) = common::try_get_with_headers(
        &md_html_url,
        &wildcard_headers,
        &mut assertions,
        "case4_wildcard_on_conn_error",
    ) {
        assertions.push(assertions::assert_status(
            "case4_wildcard_on_status_200",
            resp4.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case4_wildcard_on_ct_markdown",
            "Content-Type",
            "text/markdown",
            &resp4.headers,
        ));
    }

    // Case 5: Accept: */* with markdown_on_wildcard off does NOT convert
    if let Some(resp5) = common::try_get_with_headers(
        &no_wildcard_html_url,
        &wildcard_headers,
        &mut assertions,
        "case5_wildcard_off_conn_error",
    ) {
        assertions.push(assertions::assert_status(
            "case5_wildcard_off_status_200",
            resp5.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case5_wildcard_off_ct_html",
            "Content-Type",
            "text/html",
            &resp5.headers,
        ));
    }

    // Case 6: Vary: Accept header present in converted responses (reuse case 1 resp)
    let vary_has_accept = resp1
        .headers
        .get("Vary")
        .and_then(|v| v.to_str().ok())
        .map(|s| {
            s.split(',')
                .any(|token| token.trim().eq_ignore_ascii_case("Accept"))
        })
        .unwrap_or(false);
    assertions.push(AssertionResult {
        name: "case6_vary_accept_present".to_string(),
        passed: vary_has_accept,
        expected: "Vary header contains Accept".to_string(),
        actual: if vary_has_accept {
            "contains Accept".to_string()
        } else {
            "does not contain Accept".to_string()
        },
        message: None,
    });

    // Case 7: Non-HTML Content-Type (application/json) is not converted
    if let Some(resp7) = common::try_get_with_headers(
        &md_json_url,
        &markdown_headers,
        &mut assertions,
        "case7_json_conn_error",
    ) {
        assertions.push(assertions::assert_status(
            "case7_json_not_converted_status_200",
            resp7.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case7_json_ct_preserved",
            "Content-Type",
            "application/json",
            &resp7.headers,
        ));
    }

    // Case 8: text/plain Content-Type is not converted
    if let Some(resp8) = common::try_get_with_headers(
        &md_plain_url,
        &markdown_headers,
        &mut assertions,
        "case8_plain_conn_error",
    ) {
        assertions.push(assertions::assert_status(
            "case8_plain_not_converted_status_200",
            resp8.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case8_plain_ct_preserved",
            "Content-Type",
            "text/plain",
            &resp8.headers,
        ));
    }

    Ok(common::finalize_report(SCENARIO, start, assertions))
}
