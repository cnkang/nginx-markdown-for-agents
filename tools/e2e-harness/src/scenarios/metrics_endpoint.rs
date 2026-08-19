//! Metrics endpoint scenario for the frozen Prometheus text contract.
//!
//! The public endpoint emits only Prometheus text exposition format 0.0.4.
//! This scenario intentionally does not accept the removed JSON or legacy
//! plain-text response shapes as valid alternatives.

use crate::assertions;
use crate::http::{self, HttpResponse};
use crate::scenarios::common;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

const PROMETHEUS_ACCEPT: &str = "text/plain; version=0.0.4";
const METRIC_FAMILIES: [&str; 12] = [
    "nginx_markdown_requests_total",
    "nginx_markdown_conversion_attempts_total",
    "nginx_markdown_conversion_deliveries_total",
    "nginx_markdown_conversion_duration_seconds",
    "nginx_markdown_input_bytes_total",
    "nginx_markdown_output_bytes_total",
    "nginx_markdown_inflight_requests",
    "nginx_markdown_streaming_peak_memory_bytes",
    "nginx_markdown_streaming_events_total",
    "nginx_markdown_decompression_events_total",
    "nginx_markdown_dynconf_reloads_total",
    "nginx_markdown_build_info",
];

/// Run the metrics-endpoint scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    const SCENARIO: &str = "metrics-endpoint";
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    if let Err(report) = common::ensure_reuse_nginx_binary(&ctx, SCENARIO, start) {
        return Ok(report);
    }

    let metrics_url = format!("http://127.0.0.1:{}/markdown-metrics", ctx.port);
    let app_url = format!("http://127.0.0.1:{}/md/test.html", ctx.port);
    let mut prometheus_headers = HashMap::new();
    prometheus_headers.insert("Accept".to_string(), PROMETHEUS_ACCEPT.to_string());

    let initial = match required_get_with_headers(
        &metrics_url,
        &prometheus_headers,
        SCENARIO,
        "initial metrics request",
        start,
        &assertions,
    ) {
        Ok(response) => response,
        Err(report) => return Ok(report),
    };
    assert_prometheus_response("initial", &initial, &mut assertions);

    if common::try_get_with_headers(
        &app_url,
        &HashMap::from([(String::from("Accept"), String::from("text/markdown"))]),
        &mut assertions,
        "conversion_request",
    )
    .is_some()
    {
        if let Some(after_conversion) = common::try_get_with_headers(
            &metrics_url,
            &prometheus_headers,
            &mut assertions,
            "metrics_after_conversion",
        ) {
            assert_prometheus_response("after_conversion", &after_conversion, &mut assertions);
            assertions.push(assert_prometheus_sample(
                "conversion_requests_nonzero",
                "nginx_markdown_requests_total",
                "outcome=\"converted\"",
                &after_conversion.body,
            ));
        }
    }

    Ok(common::finalize_report(SCENARIO, start, assertions))
}

/// Send a required GET request and preserve earlier assertion context on failure.
fn required_get_with_headers(
    url: &str,
    headers: &HashMap<String, String>,
    scenario: &str,
    error_prefix: &str,
    start: std::time::Instant,
    assertions: &[AssertionResult],
) -> std::result::Result<HttpResponse, ScenarioReport> {
    match http::get_with_headers(url, headers) {
        Ok(response) => Ok(response),
        Err(error) => Err(ScenarioReport::failing(
            scenario,
            assertions.to_vec(),
            start.elapsed().as_millis() as u64,
            format!("{error_prefix}: {error}"),
        )),
    }
}

/// Assert the status, content type, and complete frozen family set.
fn assert_prometheus_response(
    phase: &str,
    response: &HttpResponse,
    assertions: &mut Vec<AssertionResult>,
) {
    assertions.push(assertions::assert_status(
        &format!("{phase}_status_200"),
        response.status,
        200,
    ));
    assertions.push(assertions::assert_header_pattern(
        &format!("{phase}_content_type"),
        "Content-Type",
        r"^text/plain(?:;|$)",
        &response.headers,
    ));
    assertions.push(assertions::assert_body_contains(
        &format!("{phase}_help_lines"),
        "# HELP nginx_markdown_",
        &response.body,
    ));
    assertions.push(assertions::assert_body_contains(
        &format!("{phase}_type_lines"),
        "# TYPE nginx_markdown_",
        &response.body,
    ));
    assertions.push(AssertionResult {
        name: format!("{phase}_nonempty_body"),
        passed: !response.body.is_empty(),
        expected: "non-empty body".to_string(),
        actual: if response.body.is_empty() {
            "empty body".to_string()
        } else {
            "non-empty body".to_string()
        },
        message: None,
    });

    for family in METRIC_FAMILIES {
        assertions.push(assertions::assert_body_contains(
            &format!("{phase}_family_{family}"),
            &format!("# HELP {family}"),
            &response.body,
        ));
    }
}

/// Assert that a Prometheus family has a positive sample with the requested labels.
fn assert_prometheus_sample(
    name: &str,
    family: &str,
    label_fragment: &str,
    body: &str,
) -> AssertionResult {
    let found = common::prometheus_samples(body, family)
        .iter()
        .any(|(labels, value)| labels.contains(label_fragment) && *value >= 1.0);
    AssertionResult {
        name: name.to_string(),
        passed: found,
        expected: format!("{family} with {label_fragment} has value >= 1"),
        actual: if found {
            "positive sample found".to_string()
        } else {
            "positive sample not found".to_string()
        },
        message: if found {
            None
        } else {
            Some(format!(
                "[FAIL] assertion={name} family={family} label={label_fragment}"
            ))
        },
    }
}
