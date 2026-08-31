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
//! 5. If-Modified-Since future returns 200 (converted representation has
//!    no Last-Modified; source IMS never validates the transformed body)
//! 6. If-Modified-Since past returns 200
//! 7. Weak ETag (W/"") match returns 304
//! 8. Wildcard If-None-Match: * returns 304
//! 9. 304 response contains Vary: Accept
//! 10. HEAD request describes the Markdown representation (Content-Type
//!     text/markdown, Vary: Accept, no fabricated Content-Length/ETag)

use crate::assertions;
use crate::http;
use crate::scenarios::common;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

/// Upstream ETag value used in the fixture server.
const UPSTREAM_ETAG: &str = "\"upstream-original-etag-12345\"";

fn header_contains_token_case_insensitive(
    headers: &reqwest::header::HeaderMap,
    header_name: &str,
    token: &str,
) -> bool {
    headers
        .get(header_name)
        .and_then(|v| v.to_str().ok())
        .map(|s| {
            s.split(',')
                .any(|part| part.trim().eq_ignore_ascii_case(token))
        })
        .unwrap_or(false)
}

fn append_initial_get_cases(
    url: &str,
    headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) -> std::result::Result<String, String> {
    let resp = match http::get_with_headers(url, headers) {
        Ok(response) => response,
        Err(error) => return Err(format!("Failed to connect to NGINX: {error}")),
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
        .and_then(|value: &reqwest::header::HeaderValue| value.to_str().ok())
        .unwrap_or("")
        .to_string();
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

    Ok(response_etag)
}

fn append_if_none_match_cases(
    url: &str,
    headers: &HashMap<String, String>,
    response_etag: &str,
    assertions: &mut Vec<AssertionResult>,
) {
    let mut inm_headers = headers.clone();
    inm_headers.insert("If-None-Match".to_string(), response_etag.to_string());
    if let Some(response) =
        common::try_get_with_headers(url, &inm_headers, assertions, "case3_inm_match_304")
    {
        assertions.push(assertions::assert_status(
            "case3_inm_match_304",
            response.status,
            304,
        ));
        let vary_check =
            header_contains_token_case_insensitive(&response.headers, "Vary", "Accept");
        assertions.push(AssertionResult {
            name: "case9_vary_accept_in_304".to_string(),
            passed: vary_check,
            expected: "Vary contains Accept".to_string(),
            actual: if vary_check {
                "contains Accept".to_string()
            } else {
                "does not contain Accept".to_string()
            },
            message: None,
        });
    }

    let mut nomatch_headers = headers.clone();
    nomatch_headers.insert(
        "If-None-Match".to_string(),
        "\"non-matching-etag-99999\"".to_string(),
    );
    if let Some(response) =
        common::try_get_with_headers(url, &nomatch_headers, assertions, "case4_inm_nomatch_200")
    {
        assertions.push(assertions::assert_status(
            "case4_inm_nomatch_200",
            response.status,
            200,
        ));
    }

    let weak_etag = format!("W/{response_etag}");
    let mut weak_headers = headers.clone();
    weak_headers.insert("If-None-Match".to_string(), weak_etag);
    if let Some(response) =
        common::try_get_with_headers(url, &weak_headers, assertions, "case7_weak_etag_304")
    {
        assertions.push(assertions::assert_status(
            "case7_weak_etag_304",
            response.status,
            304,
        ));
    }

    let mut wildcard_headers = headers.clone();
    wildcard_headers.insert("If-None-Match".to_string(), "*".to_string());
    if let Some(response) =
        common::try_get_with_headers(url, &wildcard_headers, assertions, "case8_wildcard_inm_304")
    {
        assertions.push(assertions::assert_status(
            "case8_wildcard_inm_304",
            response.status,
            304,
        ));
    }
}

fn append_if_modified_since_cases(
    url: &str,
    headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) {
    // A converted Markdown representation never validates against the
    // source If-Modified-Since (CACHE_AWARE_RESPONSES.md: "Source
    // If-Modified-Since does not validate the transformed body").  The
    // module clears Last-Modified on the converted response, so even a
    // future-dated IMS must return a fresh 200 with the full body.
    let mut future_headers = headers.clone();
    future_headers.insert(
        "If-Modified-Since".to_string(),
        "Mon, 01 Jan 2030 00:00:00 GMT".to_string(),
    );
    if let Some(response) =
        common::try_get_with_headers(url, &future_headers, assertions, "case5_ims_future_200")
    {
        assertions.push(assertions::assert_status(
            "case5_ims_future_200",
            response.status,
            200,
        ));
    }

    let mut past_headers = headers.clone();
    past_headers.insert(
        "If-Modified-Since".to_string(),
        "Mon, 01 Jan 2020 00:00:00 GMT".to_string(),
    );
    if let Some(response) =
        common::try_get_with_headers(url, &past_headers, assertions, "case6_ims_past_200")
    {
        assertions.push(assertions::assert_status(
            "case6_ims_past_200",
            response.status,
            200,
        ));
    }
}

fn append_absent_header_case(
    headers: &reqwest::header::HeaderMap,
    assertions: &mut Vec<AssertionResult>,
    header_name: &str,
    assertion_name: &str,
    expected: &str,
) {
    let passed = !headers.contains_key(header_name);
    let value = common::header_value(headers, header_name);
    assertions.push(AssertionResult {
        name: assertion_name.to_string(),
        passed,
        expected: expected.to_string(),
        actual: if passed { "absent".to_string() } else { value },
        message: None,
    });
}

fn append_head_case(
    url: &str,
    headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) {
    if let Some(response) =
        common::try_head_with_headers(url, headers, assertions, "case10_head_200")
    {
        assertions.push(assertions::assert_status(
            "case10_head_200",
            response.status,
            200,
        ));

        let content_type = common::header_value(&response.headers, "content-type");
        assertions.push(AssertionResult {
            name: "case10_head_content_type_markdown".to_string(),
            passed: content_type.starts_with("text/markdown"),
            expected: "text/markdown".to_string(),
            actual: content_type,
            message: None,
        });

        let vary_check =
            header_contains_token_case_insensitive(&response.headers, "Vary", "Accept");
        assertions.push(AssertionResult {
            name: "case10_head_vary_accept".to_string(),
            passed: vary_check,
            expected: "Vary contains Accept".to_string(),
            actual: if vary_check {
                "contains Accept".to_string()
            } else {
                "does not contain Accept".to_string()
            },
            message: None,
        });

        append_absent_header_case(
            &response.headers,
            assertions,
            "etag",
            "case10_head_no_etag",
            "no ETag (body-derived, not fabricatable)",
        );
        append_absent_header_case(
            &response.headers,
            assertions,
            "content-length",
            "case10_head_no_content_length",
            "no Content-Length (body-derived, not fabricatable)",
        );
        append_absent_header_case(
            &response.headers,
            assertions,
            "content-encoding",
            "case10_head_no_content_encoding",
            "no Content-Encoding (HTML body encoding)",
        );
        append_absent_header_case(
            &response.headers,
            assertions,
            "last-modified",
            "case10_head_no_last_modified",
            "no Last-Modified (HTML mtime)",
        );
    }
}

/// Run the conditional-requests scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    const SCENARIO: &str = "conditional-requests";
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    if let Err(report) = common::ensure_reuse_nginx_binary(&ctx, SCENARIO, start) {
        return Ok(report);
    }

    let base_url = format!("http://127.0.0.1:{}", ctx.port);
    let url = format!("{base_url}/md/html");

    let mut headers = HashMap::new();
    headers.insert("Accept".to_string(), "text/markdown".to_string());

    let response_etag = match append_initial_get_cases(&url, &headers, &mut assertions) {
        Ok(etag) => etag,
        Err(message) => {
            return Ok(ScenarioReport::failing(
                SCENARIO,
                assertions,
                start.elapsed().as_millis() as u64,
                message,
            ));
        }
    };

    append_if_none_match_cases(&url, &headers, &response_etag, &mut assertions);
    append_if_modified_since_cases(&url, &headers, &mut assertions);

    append_head_case(&url, &headers, &mut assertions);

    Ok(common::finalize_report(SCENARIO, start, assertions))
}
