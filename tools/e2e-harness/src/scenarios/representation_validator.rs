//! Representation-validator scenario for conditional Markdown negotiation.
//!
//! The fixture deliberately returns a source `304` when it sees the source
//! validator values.  A Markdown request must suppress those values before
//! the proxy or cache evaluates them, then evaluate the captured values only
//! against the converted representation.

use crate::assertions;
use crate::http;
use crate::scenarios::common;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

const SCENARIO: &str = "representation-validator-e2e";
const SOURCE_ETAG: &str = "\"converted-etag-12345\"";
const SOURCE_IMS: &str = "Mon, 01 Jan 2030 00:00:00 GMT";
const PROXY_PATH: &str = "/representation-proxy";

fn proxy_url(base_url: &str) -> String {
    format!("{base_url}{PROXY_PATH}")
}

fn markdown_headers() -> HashMap<String, String> {
    let mut headers = HashMap::new();
    headers.insert("Accept".to_string(), "text/markdown".to_string());
    headers
}

fn push_markdown_shape(
    assertions: &mut Vec<AssertionResult>,
    prefix: &str,
    response: &http::HttpResponse,
    expected_status: u16,
) {
    assertions.push(assertions::assert_status(
        &format!("{prefix}_status"),
        response.status,
        expected_status,
    ));
    let content_type = common::header_value(&response.headers, "content-type");
    assertions.push(AssertionResult {
        name: format!("{prefix}_content_type"),
        passed: content_type.starts_with("text/markdown"),
        expected: "text/markdown".to_string(),
        actual: content_type,
        message: None,
    });
}

fn append_source_validator_cases(base_url: &str, assertions: &mut Vec<AssertionResult>) {
    let url = proxy_url(base_url);

    let mut ims_headers = markdown_headers();
    ims_headers.insert("If-Modified-Since".to_string(), SOURCE_IMS.to_string());
    if let Some(response) =
        common::try_get_with_headers(&url, &ims_headers, assertions, "source_lm_match_request")
    {
        push_markdown_shape(assertions, "source_lm_match", &response, 200);
    }

    let mut etag_headers = markdown_headers();
    etag_headers.insert("If-None-Match".to_string(), SOURCE_ETAG.to_string());
    if let Some(response) =
        common::try_get_with_headers(&url, &etag_headers, assertions, "source_etag_match_request")
    {
        push_markdown_shape(assertions, "source_etag_match", &response, 200);
    }
}

fn append_markdown_validator_cases(base_url: &str, assertions: &mut Vec<AssertionResult>) {
    let url = proxy_url(base_url);
    let headers = markdown_headers();
    let response = match common::try_get_with_headers(
        &url,
        &headers,
        assertions,
        "markdown_validator_initial_request",
    ) {
        Some(response) => response,
        None => return,
    };

    push_markdown_shape(assertions, "markdown_validator_initial", &response, 200);
    assertions.push(assertions::assert_header_present(
        "markdown_validator_etag_present",
        "ETag",
        &response.headers,
    ));
    let markdown_etag = common::header_value(&response.headers, "etag");

    if !markdown_etag.is_empty() {
        let mut match_headers = headers.clone();
        match_headers.insert("If-None-Match".to_string(), markdown_etag);
        if let Some(match_response) = common::try_get_with_headers(
            &url,
            &match_headers,
            assertions,
            "markdown_etag_match_request",
        ) {
            assertions.push(assertions::assert_status(
                "markdown_etag_match_304",
                match_response.status,
                304,
            ));
        }
    }

    let mut unknown_headers = headers;
    unknown_headers.insert(
        "If-None-Match".to_string(),
        "\"unknown-markdown-etag\"".to_string(),
    );
    if let Some(unknown_response) = common::try_get_with_headers(
        &url,
        &unknown_headers,
        assertions,
        "unknown_markdown_etag_request",
    ) {
        push_markdown_shape(assertions, "unknown_markdown_etag", &unknown_response, 200);
    }
}

fn append_cache_cases(base_url: &str, assertions: &mut Vec<AssertionResult>) {
    let url = format!("{base_url}/representation-cache");
    let headers = markdown_headers();
    let first = match common::try_get_with_headers(
        &url,
        &headers,
        assertions,
        "proxy_cache_miss_request",
    ) {
        Some(response) => response,
        None => return,
    };
    push_markdown_shape(assertions, "proxy_cache_miss", &first, 200);
    assertions.push(assertions::assert_header_value(
        "proxy_cache_miss_marker",
        "X-Proxy-Cache",
        "MISS",
        &first.headers,
    ));

    let mut hit_headers = headers;
    hit_headers.insert("If-Modified-Since".to_string(), SOURCE_IMS.to_string());
    if let Some(second) =
        common::try_get_with_headers(&url, &hit_headers, assertions, "proxy_cache_hit_request")
    {
        push_markdown_shape(assertions, "proxy_cache_hit", &second, 200);
        assertions.push(assertions::assert_header_value(
            "proxy_cache_hit_marker",
            "X-Proxy-Cache",
            "HIT",
            &second.headers,
        ));
    }
}

fn append_head_case(base_url: &str, assertions: &mut Vec<AssertionResult>) {
    let mut headers = markdown_headers();
    headers.insert("If-Modified-Since".to_string(), SOURCE_IMS.to_string());
    let url = proxy_url(base_url);
    if let Some(response) =
        common::try_head_with_headers(&url, &headers, assertions, "head_representation_request")
    {
        push_markdown_shape(assertions, "head_representation", &response, 200);
    }
}

fn append_authenticated_case(base_url: &str, assertions: &mut Vec<AssertionResult>) {
    let mut headers = markdown_headers();
    headers.insert("Cookie".to_string(), "session_user=alice".to_string());
    headers.insert("If-None-Match".to_string(), SOURCE_ETAG.to_string());
    let url = proxy_url(base_url);
    if let Some(response) = common::try_get_with_headers(
        &url,
        &headers,
        assertions,
        "authenticated_representation_request",
    ) {
        push_markdown_shape(assertions, "authenticated_representation", &response, 200);
        let cache_control = common::header_value(&response.headers, "cache-control");
        assertions.push(AssertionResult {
            name: "authenticated_representation_private_cache".to_string(),
            passed: cache_control.contains("private"),
            expected: "Cache-Control contains private".to_string(),
            actual: cache_control,
            message: None,
        });
    }
}

fn append_vary_case(base_url: &str, assertions: &mut Vec<AssertionResult>) {
    let url = proxy_url(base_url);
    let headers = markdown_headers();
    if let Some(response) =
        common::try_get_with_headers(&url, &headers, assertions, "vary_accept_request")
    {
        push_markdown_shape(assertions, "vary_accept", &response, 200);
        let vary = common::header_value(&response.headers, "vary");
        assertions.push(AssertionResult {
            name: "vary_accept_present".to_string(),
            passed: vary
                .split(',')
                .any(|token| token.trim().eq_ignore_ascii_case("accept")),
            expected: "Vary contains Accept".to_string(),
            actual: vary,
            message: None,
        });
    }
}

fn append_source_passthrough_case(base_url: &str, assertions: &mut Vec<AssertionResult>) {
    let mut headers = HashMap::new();
    headers.insert("Accept".to_string(), "text/html".to_string());
    headers.insert("If-Modified-Since".to_string(), SOURCE_IMS.to_string());
    let url = format!("{base_url}/representation-source-only");
    if let Some(response) =
        common::try_get_with_headers(&url, &headers, assertions, "source_passthrough_request")
    {
        assertions.push(assertions::assert_status(
            "source_passthrough_304",
            response.status,
            304,
        ));
    }
}

fn append_internal_redirect_case(base_url: &str, assertions: &mut Vec<AssertionResult>) {
    // Case A: source IMS must not produce a 304 for the converted
    // representation after the internal redirect; the request is answered
    // with a fresh 200 Markdown body.
    let mut headers = markdown_headers();
    headers.insert("If-Modified-Since".to_string(), SOURCE_IMS.to_string());
    let url = format!("{base_url}/representation-internal");
    if let Some(response) =
        common::try_get_with_headers(&url, &headers, assertions, "internal_redirect_request")
    {
        push_markdown_shape(assertions, "internal_redirect", &response, 200);
    }

    // Case B: fetch the converted Markdown ETag via a first (unconditional)
    // request through the internal-redirect entry, then send it back as
    // If-None-Match.  A 304 on the second request proves the client
    // validators survive the internal redirect (re-captured after the
    // redirect cleared the module context) AND that the converted
    // representation's own ETag is honored.
    let plain = markdown_headers();
    let first_url = format!("{base_url}/representation-internal");
    let converted_etag = if let Some(response) = common::try_get_with_headers(
        &first_url,
        &plain,
        assertions,
        "internal_redirect_etag_fetch_request",
    ) {
        let etag = common::header_value(&response.headers, "etag");
        if etag.is_empty() {
            assertions.push(AssertionResult {
                name: "internal_redirect_etag_present".to_string(),
                passed: false,
                expected: "converted response carries an ETag".to_string(),
                actual: "no etag header".to_string(),
                message: None,
            });
        }
        etag
    } else {
        String::new()
    };

    if !converted_etag.is_empty() {
        let mut etag_headers = markdown_headers();
        etag_headers.insert("If-None-Match".to_string(), converted_etag);
        let url = format!("{base_url}/representation-internal");
        if let Some(response) = common::try_get_with_headers(
            &url,
            &etag_headers,
            assertions,
            "internal_redirect_etag_request",
        ) {
            assertions.push(assertions::assert_status(
                "internal_redirect_etag_matches",
                response.status,
                304,
            ));
        }
    }
}

fn append_subrequest_case(base_url: &str, assertions: &mut Vec<AssertionResult>) {
    let mut headers = markdown_headers();
    headers.insert("If-Modified-Since".to_string(), SOURCE_IMS.to_string());
    let url = format!("{base_url}/representation-subrequest");
    if let Some(response) = common::try_get_with_headers(
        &url,
        &headers,
        assertions,
        "subrequest_representation_request",
    ) {
        push_markdown_shape(assertions, "subrequest_representation", &response, 200);
        assertions.push(AssertionResult {
            name: "subrequest_auth_check_succeeded".to_string(),
            passed: common::header_value(&response.headers, "x-representation-subrequest") == "200",
            expected: "X-Representation-Subrequest: 200".to_string(),
            actual: common::header_value(&response.headers, "x-representation-subrequest"),
            message: None,
        });
    }
}

/// Run the representation-validator scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    if let Err(report) = common::ensure_reuse_nginx_binary(&ctx, SCENARIO, start) {
        return Ok(report);
    }

    let base_url = format!("http://127.0.0.1:{}", ctx.port);
    append_source_validator_cases(&base_url, &mut assertions);
    append_markdown_validator_cases(&base_url, &mut assertions);
    append_cache_cases(&base_url, &mut assertions);
    append_head_case(&base_url, &mut assertions);
    append_authenticated_case(&base_url, &mut assertions);
    append_vary_case(&base_url, &mut assertions);
    append_source_passthrough_case(&base_url, &mut assertions);
    append_internal_redirect_case(&base_url, &mut assertions);
    append_subrequest_case(&base_url, &mut assertions);

    Ok(common::finalize_report(SCENARIO, start, assertions))
}
