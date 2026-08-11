//! Multi-layer Content-Encoding chain coverage.
//!
//! Validates Requirement 12:
//! - 2-layer and 3-layer chains decode correctly through bounded full-buffer
//!   decoding for every supported encoding combination (gzip, deflate, br,
//!   identity)
//! - single-layer chains stream when the streaming engine is selected
//! - malformed grammar produces the `ENCODING_HEADER_INVALID` outcome
//!   (PASS returns the original encoded response unchanged; fail_closed
//!   returns the resolved reject status)
//! - syntactically valid unknown tokens route through the configured error
//!   policy (pass forwards the original response; fail_closed rejects)
//! - more than three non-identity layers route through the configured error
//!   policy

use crate::fixtures::{EncodingFault, EncodingLayer, FixtureSpec, RouteBehavior, RouteSpec};
use crate::http::HttpResponse;
use crate::scenarios::common;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::{Context, Result};
use std::collections::HashMap;
use std::time::Instant;

const SCENARIO: &str = "encoding-chain";
const SMALL_TITLE: &str = "Chain Small";
const SMALL_END: &str = "CHAIN_SMALL_STREAM_END";
const MEDIUM_TITLE: &str = "Chain Medium";
const MEDIUM_END: &str = "CHAIN_MEDIUM_STREAM_END";

/// Return deterministic encoding-chain upstream routes.
pub fn fixture_spec(listen_port: u16) -> FixtureSpec {
    let small = html_document(SMALL_TITLE, SMALL_END, 3);
    let medium = html_document(MEDIUM_TITLE, MEDIUM_END, 200);
    FixtureSpec {
        listen_port: Some(listen_port),
        routes: vec![
            chain_route(
                "/2-gzip-deflate",
                small.clone(),
                vec![EncodingLayer::Gzip, EncodingLayer::Deflate],
                EncodingFault::None,
            ),
            chain_route(
                "/2-deflate-gzip",
                small.clone(),
                vec![EncodingLayer::Deflate, EncodingLayer::Gzip],
                EncodingFault::None,
            ),
            chain_route(
                "/2-gzip-br",
                small.clone(),
                vec![EncodingLayer::Gzip, EncodingLayer::Br],
                EncodingFault::None,
            ),
            chain_route(
                "/2-br-deflate",
                small.clone(),
                vec![EncodingLayer::Br, EncodingLayer::Deflate],
                EncodingFault::None,
            ),
            chain_route(
                "/2-identity-gzip",
                small.clone(),
                vec![EncodingLayer::Identity, EncodingLayer::Gzip],
                EncodingFault::None,
            ),
            chain_route(
                "/3-gzip-deflate-br",
                small.clone(),
                vec![EncodingLayer::Gzip, EncodingLayer::Deflate, EncodingLayer::Br],
                EncodingFault::None,
            ),
            chain_route(
                "/3-br-deflate-gzip",
                small.clone(),
                vec![EncodingLayer::Br, EncodingLayer::Deflate, EncodingLayer::Gzip],
                EncodingFault::None,
            ),
            chain_route(
                "/3-identity-gzip-deflate",
                small.clone(),
                vec![
                    EncodingLayer::Identity,
                    EncodingLayer::Gzip,
                    EncodingLayer::Deflate,
                ],
                EncodingFault::None,
            ),
            chain_route(
                "/single-gzip",
                small.clone(),
                vec![EncodingLayer::Gzip],
                EncodingFault::None,
            ),
            chain_route(
                "/single-br",
                small.clone(),
                vec![EncodingLayer::Br],
                EncodingFault::None,
            ),
            chain_route(
                "/medium-2-deflate-gzip",
                medium,
                vec![EncodingLayer::Deflate, EncodingLayer::Gzip],
                EncodingFault::None,
            ),
            chain_route(
                "/malformed-grammar",
                small.clone(),
                vec![EncodingLayer::Gzip, EncodingLayer::Deflate],
                EncodingFault::MalformedGrammar,
            ),
            chain_route(
                "/unknown-token",
                small.clone(),
                vec![EncodingLayer::Gzip],
                EncodingFault::UnknownToken,
            ),
            chain_route(
                "/unknown-token-baseline",
                small.clone(),
                vec![EncodingLayer::Gzip],
                EncodingFault::UnknownToken,
            ),
            chain_route(
                "/depth-overflow",
                small.clone(),
                vec![EncodingLayer::Gzip],
                EncodingFault::DepthOverflow,
            ),
            chain_route(
                "/depth-overflow-baseline",
                small.clone(),
                vec![
                    EncodingLayer::Gzip,
                    EncodingLayer::Gzip,
                    EncodingLayer::Gzip,
                    EncodingLayer::Gzip,
                ],
                EncodingFault::None,
            ),
            chain_route(
                "/truncated",
                small.clone(),
                vec![EncodingLayer::Gzip],
                EncodingFault::Truncated,
            ),
        ],
    }
}

fn chain_route(
    path: &str,
    body: String,
    chain: Vec<EncodingLayer>,
    fault: EncodingFault,
) -> RouteSpec {
    RouteSpec {
        path: path.to_string(),
        behavior: RouteBehavior::EncodingChain {
            body,
            chain,
            fault,
        },
    }
}

fn html_document(title: &str, end_token: &str, paragraphs: usize) -> String {
    let mut body = format!(
        "<!doctype html><html><head><meta charset=\"UTF-8\"><title>{title}</title>\
         </head><body><h1>{title}</h1>"
    );
    for _ in 0..paragraphs {
        body.push_str("<p>encoding-chain-data-0123456789abcdef-repeat</p>\n");
    }
    body.push_str(&format!("<p>{end_token}</p></body></html>"));
    body
}

/// Run the encoding-chain scenario against a module-enabled NGINX binary.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = Instant::now();
    if let Err(report) = common::ensure_reuse_nginx_binary(&ctx, SCENARIO, start) {
        return Ok(report);
    }

    let mut assertions = Vec::new();
    let base_url = format!("http://127.0.0.1:{}", ctx.port);

    /* 2-layer chains decode via bounded full-buffer (Requirement 12.6). */
    for (name, path) in [
        ("gzip_deflate", "/chain/2-gzip-deflate"),
        ("deflate_gzip", "/chain/2-deflate-gzip"),
        ("gzip_br", "/chain/2-gzip-br"),
        ("br_deflate", "/chain/2-br-deflate"),
        ("identity_gzip", "/chain/2-identity-gzip"),
    ] {
        let response = request_markdown(&base_url, path)?;
        append_converted_assertions(
            &mut assertions,
            name,
            &response,
            "# Chain Small",
            SMALL_END,
            40,
        );
    }

    /* 3-layer chains decode for both orderings. */
    for (name, path) in [
        ("three_gzip_deflate_br", "/chain/3-gzip-deflate-br"),
        ("three_br_deflate_gzip", "/chain/3-br-deflate-gzip"),
        ("three_identity_gzip_deflate", "/chain/3-identity-gzip-deflate"),
    ] {
        let response = request_markdown(&base_url, path)?;
        append_converted_assertions(
            &mut assertions,
            name,
            &response,
            "# Chain Small",
            SMALL_END,
            40,
        );
    }

    /* Large 2-layer payload: bounded full-buffer decode correctness. */
    let medium = request_markdown(&base_url, "/chain/medium-2-deflate-gzip")?;
    append_converted_assertions(
        &mut assertions,
        "medium_two_layer",
        &medium,
        "# Chain Medium",
        MEDIUM_END,
        2_000,
    );

    /* Single-layer chains route through the streaming engine when selected
     * (Requirement 12.5). */
    for (name, path) in [
        ("stream_gzip", "/chain-stream/single-gzip"),
        ("stream_br", "/chain-stream/single-br"),
    ] {
        let response = request_markdown(&base_url, path)?;
        append_converted_assertions(
            &mut assertions,
            name,
            &response,
            "# Chain Small",
            SMALL_END,
            40,
        );
    }

    /* Malformed grammar with PASS policy: the original encoded response is
     * returned unchanged, no decoder runs (Requirement 12.1). */
    let malformed = request_raw(&base_url, "/chain/malformed-grammar")?;
    push_assertion(
        &mut assertions,
        "malformed_pass_original_response",
        malformed.status == 200 && malformed.headers.contains_key("Content-Encoding"),
        "original encoded response preserved under PASS",
        format!("status={} headers={:?}", malformed.status, malformed.headers),
    );

    /* Malformed grammar with fail_closed policy: resolved reject status
     * (502). */
    let malformed_closed = request_raw(&base_url, "/chain-fail-closed/malformed-grammar")?;
    push_assertion(
        &mut assertions,
        "malformed_fail_closed_status",
        malformed_closed.status == 502,
        "fail_closed emits the resolved reject status",
        format!("status={}", malformed_closed.status),
    );

    /* Unknown token: route through the configured error policy
     * (Requirement 12.7). PASS forwards the original response; fail_closed
     * returns the resolved reject status. */
    let unknown_pass = request_raw(&base_url, "/chain/unknown-token")?;
    let unknown_baseline = request_raw(&base_url, "/chain-raw/unknown-token-baseline")?;
    push_assertion(
        &mut assertions,
        "unknown_pass",
        same_wire_response(&unknown_pass, &unknown_baseline),
        "unknown-token PASS preserves the upstream wire response",
        format!(
            "pass_status={} baseline_status={} pass_headers={:?} baseline_headers={:?}",
            unknown_pass.status,
            unknown_baseline.status,
            unknown_pass.headers,
            unknown_baseline.headers
        ),
    );
    let unknown_closed = request_raw(&base_url, "/chain-fail-closed/unknown-token")?;
    push_assertion(
        &mut assertions,
        "unknown_closed",
        unknown_closed.status == 502,
        "unknown-token fail_closed emits the resolved reject status",
        format!("status={}", unknown_closed.status),
    );

    /* Depth overflow (4+ non-identity layers) follows the same configured
     * error policy as an unknown token. */
    let depth_pass = request_raw(&base_url, "/chain/depth-overflow")?;
    let depth_baseline = request_raw(&base_url, "/chain-raw/depth-overflow-baseline")?;
    push_assertion(
        &mut assertions,
        "depth_pass",
        same_wire_response(&depth_pass, &depth_baseline),
        "depth-overflow PASS preserves the upstream wire response",
        format!(
            "pass_status={} baseline_status={} pass_headers={:?} baseline_headers={:?}",
            depth_pass.status, depth_baseline.status, depth_pass.headers, depth_baseline.headers
        ),
    );
    let depth_closed = request_raw(&base_url, "/chain-fail-closed/depth-overflow")?;
    push_assertion(
        &mut assertions,
        "depth_closed",
        depth_closed.status == 502,
        "depth-overflow fail_closed emits the resolved reject status",
        format!("status={}", depth_closed.status),
    );

    /* Truncated outer layer: full-buffer decode fails cleanly and the
     * PASS policy returns the original response. */
    let truncated = request_raw(&base_url, "/chain/truncated")?;
    push_assertion(
        &mut assertions,
        "truncated_pass_original_response",
        truncated.status == 200,
        "truncated stream degrades to the original response under PASS",
        format!("status={} bytes={}", truncated.status, truncated.body.len()),
    );

    Ok(common::finalize_report(SCENARIO, start, assertions))
}

fn request_markdown(base_url: &str, path: &str) -> Result<HttpResponse> {
    let mut headers = HashMap::new();
    headers.insert("Accept".to_string(), "text/markdown".to_string());
    crate::http::get_with_headers(&format!("{base_url}{path}"), &headers)
        .with_context(|| format!("request failed for {path}"))
}

fn request_raw(base_url: &str, path: &str) -> Result<HttpResponse> {
    crate::http::get(&format!("{base_url}{path}"))
        .with_context(|| format!("request failed for {path}"))
}

fn same_wire_response(left: &HttpResponse, right: &HttpResponse) -> bool {
    left.status == right.status
        && left.body == right.body
        && left.headers.get("Content-Encoding") == right.headers.get("Content-Encoding")
        && left.headers.get("Content-Type") == right.headers.get("Content-Type")
}

fn append_converted_assertions(
    assertions: &mut Vec<AssertionResult>,
    prefix: &str,
    response: &HttpResponse,
    heading: &str,
    end_token: &str,
    minimum_size: usize,
) {
    let content_type = common::header_value(&response.headers, "Content-Type");
    push_assertion(
        assertions,
        &format!("{prefix}_status"),
        response.status == 200,
        "HTTP 200",
        response.status.to_string(),
    );
    push_assertion(
        assertions,
        &format!("{prefix}_content_type"),
        content_type.starts_with("text/markdown"),
        "text/markdown",
        content_type,
    );
    push_assertion(
        assertions,
        &format!("{prefix}_encoding_removed"),
        !response.headers.contains_key("Content-Encoding"),
        "Content-Encoding absent after decode",
        format!("headers={:?}", response.headers),
    );
    push_assertion(
        assertions,
        &format!("{prefix}_body_complete"),
        response.body.contains(heading)
            && common::markdown_token_present(&response.body, end_token)
            && response.body.len() >= minimum_size,
        format!("heading, end token, and at least {minimum_size} bytes"),
        format!("body_bytes={}", response.body.len()),
    );
}

fn push_assertion(
    assertions: &mut Vec<AssertionResult>,
    name: &str,
    passed: bool,
    expected: impl Into<String>,
    actual: impl Into<String>,
) {
    assertions.push(AssertionResult {
        name: name.to_string(),
        passed,
        expected: expected.into(),
        actual: actual.into(),
        message: (!passed).then(|| format!("[FAIL] assertion={name}")),
    });
}
