//! Metrics endpoint scenario — JSON / plain-text / Prometheus exposition format.
//!
//! Migrated from `tools/e2e/verify_metrics_endpoint_e2e.sh`.
//! Validates metrics-endpoint paths:
//! 1. JSON format via Accept: application/json
//! 2. Plain-text format (default, no Accept override)
//! 3. Prometheus exposition format via Accept: text/plain
//! 4. Metrics endpoint returns 200 with non-empty body
//! 5. JSON metrics contain required top-level keys
//! 6. Prometheus metrics contain expected metric family prefixes
//! 7. After a conversion, request counters are non-zero
//! 8. Invalid Accept header returns plain-text fallback

use crate::assertions;
use crate::http;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::Result;
use std::collections::HashMap;

/// Run the metrics-endpoint scenario.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = std::time::Instant::now();
    let mut assertions = Vec::new();

    let nginx_bin = match &ctx.mode {
        crate::runtime::RuntimeMode::Reuse(path) => path.clone(),
        crate::runtime::RuntimeMode::Bootstrap => {
            return Ok(ScenarioReport::failing(
                "metrics-endpoint",
                assertions,
                start.elapsed().as_millis() as u64,
                "Bootstrap mode not yet supported".to_string(),
            ));
        }
    };

    if !nginx_bin.exists() {
        return Ok(skipped_report(start, "NGINX binary not found"));
    }

    let metrics_port = ctx.upstream_port;
    let metrics_url = format!("http://127.0.0.1:{metrics_port}/metrics");
    let app_url = format!("http://127.0.0.1:{}/md/test.html", ctx.port);

    let mut json_headers = HashMap::new();
    json_headers.insert("Accept".to_string(), "application/json".to_string());

    let mut prometheus_headers = HashMap::new();
    prometheus_headers.insert("Accept".to_string(), "text/plain".to_string());

    let mut markdown_headers = HashMap::new();
    markdown_headers.insert("Accept".to_string(), "text/markdown".to_string());

    let mut invalid_accept_headers = HashMap::new();
    invalid_accept_headers.insert("Accept".to_string(), "application/xml".to_string());

    // Case 1: JSON format via Accept: application/json
    let resp1 = match http::get_with_headers(&metrics_url, &json_headers) {
        Ok(r) => r,
        Err(e) => {
            return Ok(ScenarioReport::failing(
                "metrics-endpoint",
                assertions,
                start.elapsed().as_millis() as u64,
                format!("Case 1: failed to connect to metrics endpoint: {e}"),
            ));
        }
    };
    assertions.push(assertions::assert_status(
        "case1_json_status_200",
        resp1.status,
        200,
    ));
    assertions.push(assertions::assert_header_pattern(
        "case1_json_content_type",
        "Content-Type",
        "application/json",
        &resp1.headers,
    ));
    let json_valid = serde_json::from_str::<serde_json::Value>(&resp1.body).is_ok();
    assertions.push(AssertionResult {
        name: "case1_json_valid".to_string(),
        passed: json_valid,
        expected: "valid JSON".to_string(),
        actual: if json_valid {
            "valid JSON".to_string()
        } else {
            "invalid JSON".to_string()
        },
        message: if json_valid {
            None
        } else {
            Some("[FAIL] assertion=case1_json_valid metrics body is not valid JSON".to_string())
        },
    });

    // Case 2: Plain-text format (default, no Accept override)
    let resp2 = match http::get(&metrics_url) {
        Ok(r) => r,
        Err(e) => {
            return Ok(ScenarioReport::failing(
                "metrics-endpoint",
                assertions,
                start.elapsed().as_millis() as u64,
                format!("Case 2: failed to connect to metrics endpoint: {e}"),
            ));
        }
    };
    assertions.push(assertions::assert_status(
        "case2_plaintext_status_200",
        resp2.status,
        200,
    ));
    assertions.push(assertions::assert_header_pattern(
        "case2_plaintext_content_type",
        "Content-Type",
        "text/plain",
        &resp2.headers,
    ));
    assertions.push(AssertionResult {
        name: "case2_plaintext_nonempty_body".to_string(),
        passed: !resp2.body.is_empty(),
        expected: "non-empty body".to_string(),
        actual: if resp2.body.is_empty() {
            "empty body".to_string()
        } else {
            "non-empty body".to_string()
        },
        message: if resp2.body.is_empty() {
            Some(
                "[FAIL] assertion=case2_plaintext_nonempty_body response body is empty".to_string(),
            )
        } else {
            None
        },
    });

    // Case 3: Prometheus exposition format
    let resp3 = match http::get_with_headers(&metrics_url, &prometheus_headers) {
        Ok(r) => r,
        Err(e) => {
            return Ok(ScenarioReport::failing(
                "metrics-endpoint",
                assertions,
                start.elapsed().as_millis() as u64,
                format!("Case 3: failed to connect to metrics endpoint: {e}"),
            ));
        }
    };
    assertions.push(assertions::assert_status(
        "case3_prometheus_status_200",
        resp3.status,
        200,
    ));
    let has_prometheus_help = resp3.body.contains("# HELP nginx_markdown");
    let has_prometheus_type = resp3.body.contains("# TYPE nginx_markdown");
    assertions.push(AssertionResult {
        name: "case3_prometheus_help_prefix".to_string(),
        passed: has_prometheus_help,
        expected: "body contains # HELP nginx_markdown".to_string(),
        actual: if has_prometheus_help {
            "found".to_string()
        } else {
            "not found".to_string()
        },
        message: None,
    });
    assertions.push(AssertionResult {
        name: "case3_prometheus_type_prefix".to_string(),
        passed: has_prometheus_type,
        expected: "body contains # TYPE nginx_markdown".to_string(),
        actual: if has_prometheus_type {
            "found".to_string()
        } else {
            "not found".to_string()
        },
        message: None,
    });

    // Case 4: Non-empty metrics body for all formats
    assertions.push(AssertionResult {
        name: "case4_json_nonempty".to_string(),
        passed: !resp1.body.is_empty(),
        expected: "non-empty".to_string(),
        actual: if resp1.body.is_empty() {
            "empty".to_string()
        } else {
            "non-empty".to_string()
        },
        message: None,
    });
    assertions.push(AssertionResult {
        name: "case4_plaintext_nonempty".to_string(),
        passed: !resp2.body.is_empty(),
        expected: "non-empty".to_string(),
        actual: if resp2.body.is_empty() {
            "empty".to_string()
        } else {
            "non-empty".to_string()
        },
        message: None,
    });
    assertions.push(AssertionResult {
        name: "case4_prometheus_nonempty".to_string(),
        passed: !resp3.body.is_empty(),
        expected: "non-empty".to_string(),
        actual: if resp3.body.is_empty() {
            "empty".to_string()
        } else {
            "non-empty".to_string()
        },
        message: None,
    });

    // Case 5: JSON metrics contain required top-level keys
    if let Ok(data) = serde_json::from_str::<serde_json::Value>(&resp1.body) {
        let required_keys = ["total_requests", "converted_total", "skipped_total"];
        for key in &required_keys {
            let has_key = data.get(key).is_some();
            assertions.push(AssertionResult {
                name: format!("case5_json_key_{key}"),
                passed: has_key,
                expected: format!("key '{key}' present"),
                actual: if has_key {
                    "present".to_string()
                } else {
                    "absent".to_string()
                },
                message: None,
            });
        }
    } else {
        assertions.push(failed_assertion("case5_json_parse", "JSON parse failed"));
    }

    // Case 6: Prometheus metrics contain expected metric family prefixes
    assertions.push(AssertionResult {
        name: "case6_prometheus_help_lines".to_string(),
        passed: has_prometheus_help,
        expected: "HELP lines with nginx_markdown prefix".to_string(),
        actual: if has_prometheus_help {
            "found".to_string()
        } else {
            "not found".to_string()
        },
        message: None,
    });
    assertions.push(AssertionResult {
        name: "case6_prometheus_type_lines".to_string(),
        passed: has_prometheus_type,
        expected: "TYPE lines with nginx_markdown prefix".to_string(),
        actual: if has_prometheus_type {
            "found".to_string()
        } else {
            "not found".to_string()
        },
        message: None,
    });

    // Case 7: After a conversion, request counters are non-zero
    if let Ok(_conv_resp) = http::get_with_headers(&app_url, &markdown_headers) {
        if let Ok(resp7) = http::get_with_headers(&metrics_url, &json_headers) {
            if let Ok(data7) = serde_json::from_str::<serde_json::Value>(&resp7.body) {
                let total_req = data7
                    .get("total_requests")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                let converted = data7
                    .get("converted_total")
                    .and_then(|v| v.as_u64())
                    .unwrap_or(0);
                assertions.push(AssertionResult {
                    name: "case7_total_requests_nonzero".to_string(),
                    passed: total_req >= 1,
                    expected: ">= 1".to_string(),
                    actual: total_req.to_string(),
                    message: None,
                });
                assertions.push(AssertionResult {
                    name: "case7_converted_total_nonzero".to_string(),
                    passed: converted >= 1,
                    expected: ">= 1".to_string(),
                    actual: converted.to_string(),
                    message: None,
                });
            } else {
                assertions.push(failed_assertion("case7_json_parse", "JSON parse failed"));
            }
        } else {
            assertions.push(failed_assertion("case7_metrics_fetch", "request failed"));
        }
    } else {
        assertions.push(failed_assertion(
            "case7_conversion_request",
            "conversion request failed",
        ));
    }

    // Case 8: Invalid Accept header returns plain-text fallback
    if let Ok(resp8) = http::get_with_headers(&metrics_url, &invalid_accept_headers) {
        assertions.push(assertions::assert_status(
            "case8_invalid_accept_status_200",
            resp8.status,
            200,
        ));
        assertions.push(assertions::assert_header_pattern(
            "case8_invalid_accept_content_type",
            "Content-Type",
            "text/plain",
            &resp8.headers,
        ));
    } else {
        assertions.push(failed_assertion("case8_invalid_accept", "request failed"));
    }

    let elapsed = start.elapsed().as_millis() as u64;
    let passed = assertions.iter().all(|a| a.passed);
    Ok(ScenarioReport {
        name: "metrics-endpoint".to_string(),
        passed,
        assertions,
        elapsed_ms: elapsed,
        failure_message: if passed {
            None
        } else {
            Some("metrics-endpoint failed".to_string())
        },
    })
}

/// Create a skipped scenario report.
fn skipped_report(start: std::time::Instant, reason: &str) -> ScenarioReport {
    ScenarioReport {
        name: "metrics-endpoint".to_string(),
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
