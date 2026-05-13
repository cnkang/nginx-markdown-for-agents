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
use crate::scenarios::common;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

/// Upstream ETag value used in the fixture server.
const UPSTREAM_ETAG: &str = "\"upstream-auth-etag-001\"";

/// Run the auth-cache scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    const SCENARIO: &str = "auth-cache";
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    if let Err(report) = common::ensure_reuse_nginx_binary(&ctx, SCENARIO, start) {
        return Ok(report);
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

    case1_auth_private(&md_url, &auth_headers, &mut assertions);
    case2_noauth_public(&md_url, &markdown_headers, &mut assertions);
    case3_deny_policy(&md_deny_url, &auth_headers, &mut assertions);
    case4_nonmatching_cookie(&md_url, &nonmatching_cookie_headers, &mut assertions);
    case5_auth_fail_open_cache_control(&md_url, &auth_headers, &mut assertions);
    case6_nonauth_etag_replacement(&md_url, &markdown_headers, &mut assertions);
    case7_vary_cookie(&md_url, &auth_headers, &mut assertions);

    Ok(common::finalize_report(SCENARIO, start, assertions))
}

/// Case 1: authenticated request and assert `Cache-Control` contains `private`.
///
/// Uses `url`, `headers`, and `assertions` to append status and cache-control checks.
fn case1_auth_private(
    url: &str,
    headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) {
    if let Some(resp) =
        common::try_get_with_headers(url, headers, assertions, "case1_cache_control_private")
    {
        assertions.push(assertions::assert_status(
            "case1_auth_status_200",
            resp.status,
            200,
        ));
        let cc_value = common::header_value(&resp.headers, "Cache-Control");
        let has_private = cc_value.contains("private");
        assertions.push(AssertionResult {
            name: "case1_cache_control_private".to_string(),
            passed: has_private,
            expected: "Cache-Control contains private".to_string(),
            actual: cc_value,
            message: None,
        });
    }
}

/// Case 2: request without auth cookie and assert `Cache-Control` contains `public`.
///
/// Uses `url`, `headers`, and `assertions` to append status and header assertions.
fn case2_noauth_public(
    url: &str,
    headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) {
    if let Some(resp) =
        common::try_get_with_headers(url, headers, assertions, "case2_cache_control_public")
    {
        assertions.push(assertions::assert_status(
            "case2_noauth_status_200",
            resp.status,
            200,
        ));
        let cc_value = common::header_value(&resp.headers, "Cache-Control");
        let has_public = cc_value.contains("public");
        assertions.push(AssertionResult {
            name: "case2_cache_control_public".to_string(),
            passed: has_public,
            expected: "Cache-Control contains public".to_string(),
            actual: cc_value,
            message: None,
        });
    }
}

/// Case 3: request deny-policy location and assert passthrough `text/html` response.
///
/// Uses `deny_url`, `auth_headers`, and appends status/content-type assertions.
fn case3_deny_policy(
    deny_url: &str,
    auth_headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) {
    if let Some(resp) = common::try_get_with_headers(
        deny_url,
        auth_headers,
        assertions,
        "case3_deny_content_type_html",
    ) {
        assertions.push(assertions::assert_status(
            "case3_deny_status_200",
            resp.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case3_deny_content_type_html",
            "Content-Type",
            "text/html",
            &resp.headers,
        ));
    }
}

/// Case 4: request with non-matching cookie and assert markdown conversion remains enabled.
///
/// Uses `url`, `headers`, and appends status and `Content-Type: text/markdown` checks.
fn case4_nonmatching_cookie(
    url: &str,
    headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) {
    if let Some(resp) = common::try_get_with_headers(
        url,
        headers,
        assertions,
        "case4_nonmatch_cookie_markdown_ct",
    ) {
        assertions.push(assertions::assert_status(
            "case4_nonmatch_cookie_status_200",
            resp.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case4_nonmatch_cookie_markdown_ct",
            "Content-Type",
            "text/markdown",
            &resp.headers,
        ));
    }
}

/// Case 5: auth fail-open behavior, asserting `Cache-Control` header is still present.
///
/// Uses `url`, `auth_headers`, and appends header-presence assertion details.
fn case5_auth_fail_open_cache_control(
    url: &str,
    auth_headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) {
    if let Some(resp) = common::try_get_with_headers(
        url,
        auth_headers,
        assertions,
        "case5_auth_preserves_cache_control",
    ) {
        let auth_cc = common::header_value(&resp.headers, "Cache-Control");
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
    }
}

/// Case 6: non-auth ETag behavior, asserting ETag presence and replacement from upstream.
///
/// Uses `url`, `headers`, and appends ETag presence/difference assertions.
fn case6_nonauth_etag_replacement(
    url: &str,
    headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) {
    if let Some(resp) =
        common::try_get_with_headers(url, headers, assertions, "case6_etag_differs_from_upstream")
    {
        let md_etag = common::header_value(&resp.headers, "ETag");
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
    }
}

/// Case 7: auth response Vary behavior, asserting `Vary` contains `Cookie`.
///
/// Uses `url`, `auth_headers`, and appends the Vary-token assertion result.
fn case7_vary_cookie(
    url: &str,
    auth_headers: &HashMap<String, String>,
    assertions: &mut Vec<AssertionResult>,
) {
    if let Some(resp) =
        common::try_get_with_headers(url, auth_headers, assertions, "case7_vary_cookie")
    {
        let vary_value = common::header_value(&resp.headers, "Vary");
        let has_vary_cookie = vary_value
            .split(',')
            .any(|token| token.trim().eq_ignore_ascii_case("Cookie"));
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
    }
}
