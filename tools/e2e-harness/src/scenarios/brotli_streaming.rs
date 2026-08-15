//! Native Brotli decompression coverage through the real NGINX module.

use crate::fixtures::{BrotliFault, FixtureSpec, RouteBehavior, RouteSpec};
use crate::http::HttpResponse;
use crate::scenarios::common;
use crate::scenarios::{AssertionResult, ScenarioContext, ScenarioReport};
use anyhow::{Context, Result};
use socket2::SockRef;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{Shutdown, TcpStream};
use std::time::{Duration, Instant};

const SCENARIO: &str = "brotli-streaming";
const SMALL_END: &str = "BROTLI_SMALL_STREAM_END";
const LARGE_END: &str = "BROTLI_LARGE_STREAM_END";
const PRESSURE_END: &str = "BROTLI_PRESSURE_STREAM_END";
const SLOW_READER_BUFFER_SIZE: usize = 16 * 1024;

/// Return deterministic Brotli upstream routes for this scenario.
pub fn fixture_spec(listen_port: u16) -> FixtureSpec {
    let small = html_document("Brotli Small", SMALL_END, 1);
    let large = html_document("Brotli Large", LARGE_END, 10_000);
    let pressure = html_document("Brotli Pressure", PRESSURE_END, 150_000);
    FixtureSpec {
        listen_port: Some(listen_port),
        routes: vec![
            brotli_route("/small-brotli", small.clone(), 16 * 1024, BrotliFault::None),
            brotli_route("/large-brotli", large, 16 * 1024, BrotliFault::None),
            brotli_route("/tiny-brotli", small.clone(), 1, BrotliFault::None),
            brotli_route("/pressure-brotli", pressure, 4096, BrotliFault::None),
            brotli_route(
                "/trailing-brotli",
                small.clone(),
                4096,
                BrotliFault::TrailingData,
            ),
            brotli_route(
                "/truncated-brotli",
                small.clone(),
                4096,
                BrotliFault::Truncated,
            ),
            brotli_route("/malformed-brotli", small, 4096, BrotliFault::Malformed),
        ],
    }
}

fn brotli_route(path: &str, body: String, chunk_size: usize, fault: BrotliFault) -> RouteSpec {
    RouteSpec {
        path: path.to_string(),
        behavior: RouteBehavior::Brotli {
            body,
            chunk_size,
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
        body.push_str("<p>brotli-stream-data-0123456789abcdef-repeat</p>\n");
    }
    body.push_str(&format!("<p>{end_token}</p></body></html>"));
    body
}

/// Run the Brotli streaming scenario against a module-enabled NGINX binary.
pub fn run(ctx: ScenarioContext) -> Result<ScenarioReport> {
    let start = Instant::now();
    if let Err(report) = common::ensure_reuse_nginx_binary(&ctx, SCENARIO, start) {
        return Ok(report);
    }

    let mut assertions = Vec::new();
    let base_url = format!("http://127.0.0.1:{}", ctx.port);
    let metrics_before = metric_value(&base_url, "decompression_brotli_success")?;
    let streaming_attempts_before = metric_value(&base_url, "conversion_attempts_streaming")?;
    let small = request_markdown(&base_url, "/streaming/small-brotli")?;
    append_converted_assertions(
        &mut assertions,
        "small",
        &small,
        "# Brotli Small",
        SMALL_END,
        20,
    );

    let tiny = request_markdown(&base_url, "/streaming/tiny-brotli")?;
    append_converted_assertions(
        &mut assertions,
        "tiny_chunks",
        &tiny,
        "# Brotli Small",
        SMALL_END,
        20,
    );
    push_assertion(
        &mut assertions,
        "tiny_chunks_match",
        small.body == tiny.body,
        "byte-identical Markdown",
        format!("small={} tiny={}", small.body.len(), tiny.body.len()),
    );

    let large = request_markdown(&base_url, "/streaming/large-brotli")?;
    append_converted_assertions(
        &mut assertions,
        "large",
        &large,
        "# Brotli Large",
        LARGE_END,
        100_000,
    );
    let metrics_after = metric_value(&base_url, "decompression_brotli_success")?;
    push_assertion(
        &mut assertions,
        "streaming_metric_delta",
        metrics_after >= metrics_before + 3,
        "at least three successful Brotli decompressions",
        format!("before={metrics_before} after={metrics_after}"),
    );
    let streaming_attempts_after = metric_value(&base_url, "conversion_attempts_streaming")?;
    push_assertion(
        &mut assertions,
        "streaming_attempt_metric_delta",
        streaming_attempts_after >= streaming_attempts_before + 3,
        "at least three streaming conversion attempts",
        format!("before={streaming_attempts_before} after={streaming_attempts_after}"),
    );

    append_fullbuffer_assertions(&base_url, &mut assertions)?;
    append_configuration_gate_assertions(&base_url, &mut assertions)?;
    append_fault_assertions(&base_url, &mut assertions)?;
    append_backpressure_assertions(&ctx, &base_url, &mut assertions)?;

    Ok(common::finalize_report(SCENARIO, start, assertions))
}

fn append_configuration_gate_assertions(
    base_url: &str,
    assertions: &mut Vec<AssertionResult>,
) -> Result<()> {
    for (name, path) in [
        ("auto_decompress_off", "/auto-decompress-off/small-brotli"),
        ("budget_exceeded", "/tight-budget/large-brotli"),
    ] {
        let response = request_markdown(base_url, path)?;
        let encoding = common::header_value(&response.headers, "Content-Encoding");
        push_assertion(
            assertions,
            name,
            response.status == 200 && encoding.eq_ignore_ascii_case("br"),
            "original Brotli response preserved by the configured gate",
            format!("status={} content_encoding={encoding}", response.status),
        );
    }
    Ok(())
}

fn request_markdown(base_url: &str, path: &str) -> Result<HttpResponse> {
    let mut headers = HashMap::new();
    headers.insert("Accept".to_string(), "text/markdown".to_string());
    crate::http::get_with_headers(&format!("{base_url}{path}"), &headers)
        .with_context(|| format!("request failed for {path}"))
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
        "Content-Encoding absent",
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

fn append_fullbuffer_assertions(
    base_url: &str,
    assertions: &mut Vec<AssertionResult>,
) -> Result<()> {
    let before = metric_value(base_url, "decompression_brotli_success")?;
    let attempts_before = metric_value(base_url, "conversion_attempts_full_buffer")?;
    for (name, path) in [
        ("cache_full", "/cache-full/small-brotli"),
        ("streaming_off", "/non-streaming/small-brotli"),
    ] {
        let response = request_markdown(base_url, path)?;
        append_converted_assertions(assertions, name, &response, "# Brotli Small", SMALL_END, 20);
    }
    let after = metric_value(base_url, "decompression_brotli_success")?;
    push_assertion(
        assertions,
        "fullbuffer_metric_delta",
        after >= before + 2,
        "two additional successful Brotli decompressions",
        format!("before={before} after={after}"),
    );
    let attempts_after = metric_value(base_url, "conversion_attempts_full_buffer")?;
    push_assertion(
        assertions,
        "fullbuffer_attempt_metric_delta",
        attempts_after >= attempts_before + 2,
        "two full-buffer conversion attempts",
        format!("before={attempts_before} after={attempts_after}"),
    );
    Ok(())
}

fn append_fault_assertions(base_url: &str, assertions: &mut Vec<AssertionResult>) -> Result<()> {
    for (name, path) in [
        ("trailing_data", "/streaming/trailing-brotli"),
        ("truncated_input", "/streaming/truncated-brotli"),
        ("malformed_input", "/streaming/malformed-brotli"),
    ] {
        let response = request_markdown(base_url, path)?;
        let encoding = common::header_value(&response.headers, "Content-Encoding");
        push_assertion(
            assertions,
            &format!("{name}_handled"),
            response.status == 200
                && encoding.eq_ignore_ascii_case("br")
                && !response.body.is_empty(),
            "non-empty original Brotli response preserved by fail-open",
            format!(
                "status={} content_encoding={encoding} bytes={}",
                response.status,
                response.body.len()
            ),
        );
    }
    let health = request_markdown(base_url, "/streaming/small-brotli")?;
    push_assertion(
        assertions,
        "worker_survives_faults",
        health.status == 200 && common::markdown_token_present(&health.body, SMALL_END),
        "subsequent conversion succeeds",
        format!("status={} bytes={}", health.status, health.body.len()),
    );
    Ok(())
}

fn append_backpressure_assertions(
    ctx: &ScenarioContext,
    base_url: &str,
    assertions: &mut Vec<AssertionResult>,
) -> Result<()> {
    let before = metric_value(base_url, "streaming_events")?;
    let raw = slow_read_response(ctx.port, ctx.timeout)?;
    let after = metric_value(base_url, "streaming_events")?;
    push_assertion(
        assertions,
        "slow_reader_body_complete",
        common::markdown_token_bytes_present(&raw, PRESSURE_END),
        "complete response contains the pressure end token",
        format!("wire_bytes={}", raw.len()),
    );
    push_assertion(
        assertions,
        "backpressure_metric_delta",
        after > before,
        "streaming lifecycle events increase",
        format!("before={before} after={after}"),
    );
    Ok(())
}

fn slow_read_response(port: u16, timeout: Duration) -> Result<Vec<u8>> {
    let mut stream =
        TcpStream::connect(("127.0.0.1", port)).context("failed to connect slow-reader socket")?;
    stream.set_read_timeout(Some(timeout))?;
    stream.set_write_timeout(Some(timeout))?;
    SockRef::from(&stream).set_recv_buffer_size(SLOW_READER_BUFFER_SIZE)?;
    stream.write_all(
        b"GET /streaming/pressure-brotli HTTP/1.1\r\n\
Host: 127.0.0.1\r\n\
Accept: text/markdown\r\n\
Connection: close\r\n\r\n",
    )?;
    stream.flush()?;
    std::thread::sleep(Duration::from_millis(750));

    let deadline = Instant::now() + timeout;
    let mut response = Vec::new();
    let mut chunk = [0_u8; SLOW_READER_BUFFER_SIZE];
    while Instant::now() < deadline {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(read) => {
                response.extend_from_slice(&chunk[..read]);
                std::thread::sleep(Duration::from_millis(1));
            }
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) =>
            {
                continue;
            }
            Err(error) => return Err(error).context("slow-reader receive failed"),
        }
    }
    let _ = stream.shutdown(Shutdown::Both);
    Ok(response)
}

fn metric_value(base_url: &str, name: &str) -> Result<u64> {
    let mut headers = HashMap::new();
    headers.insert(
        "Accept".to_string(),
        "text/plain; version=0.0.4".to_string(),
    );
    let response =
        crate::http::get_with_headers(&format!("{base_url}/markdown-metrics"), &headers)?;
    let (family, label) = match name {
        "decompression_brotli_success" => (
            "nginx_markdown_decompression_events_total",
            Some("encoding=\"brotli\",outcome=\"success\""),
        ),
        "conversion_attempts_streaming" => (
            "nginx_markdown_conversion_attempts_total",
            Some("engine=\"streaming\""),
        ),
        "conversion_attempts_full_buffer" => (
            "nginx_markdown_conversion_attempts_total",
            Some("engine=\"full_buffer\""),
        ),
        "streaming_events" => ("nginx_markdown_streaming_events_total", None),
        other => return Err(anyhow::anyhow!("unsupported metric name: {other}")),
    };
    let mut matched_samples = 0_u64;
    let value = response
        .body
        .lines()
        .filter(|line| line.starts_with(family) && !line.starts_with('#'))
        // Match required labels independently: each required fragment must
        // appear in the sample's label set.  Relying on one combined label
        // string breaks if the renderer orders labels differently.
        .filter(|line| {
            label.map_or(true, |required| {
                required.split(',').all(|part| line.contains(part.trim()))
            })
        })
        // The sample value is the field immediately following the metric
        // identifier (family + label set); never the last whitespace field,
        // which may be a trailing artifact.
        .filter_map(|line| line.split_whitespace().nth(1))
        .filter_map(|sample| {
            let value = sample.parse::<f64>().ok()?;
            if !value.is_finite() || value < 0.0 {
                return None;
            }
            matched_samples += 1;
            Some(value)
        })
        .sum::<f64>();
    if matched_samples > 0 {
        Ok(value as u64)
    } else {
        // Prometheus may omit an optional sample on a fresh endpoint. Treat
        // absent or unusable optional samples as zero for delta checks.
        Ok(0)
    }
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
