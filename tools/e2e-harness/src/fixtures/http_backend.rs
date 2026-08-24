//! Embedded async HTTP server for fixture backends.
//!
//! Uses `axum` to serve deterministic upstream responses with real
//! status/header/body semantics.

#![allow(dead_code)]

use crate::fixtures::{
    BrotliFault, EncodingFault, EncodingLayer, FixtureResponse, FixtureSpec, RouteBehavior,
};
use anyhow::{Context, Result};
use axum::Router;
use axum::body::{Body, Bytes};
use axum::extract::{OriginalUri, State};
use axum::http::{HeaderMap, HeaderValue, Method, StatusCode};
use axum::response::IntoResponse;
use axum::routing::get;
use std::net::SocketAddr;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use tokio::sync::oneshot;

struct FixtureState {
    total_requests: AtomicU64,
    converted_total: AtomicU64,
}

impl FixtureState {
    fn new() -> Self {
        Self {
            total_requests: AtomicU64::new(0),
            converted_total: AtomicU64::new(0),
        }
    }
}

/// Handle to a running fixture backend.
pub struct FixtureHandle {
    /// Address the fixture is listening on.
    pub addr: SocketAddr,
    shutdown_tx: Option<oneshot::Sender<()>>,
}

impl FixtureHandle {
    /// Stop the fixture backend gracefully.
    pub async fn stop(&mut self) {
        if let Some(tx) = self.shutdown_tx.take() {
            let _ = tx.send(());
        }
    }
}

impl Drop for FixtureHandle {
    fn drop(&mut self) {
        if let Some(tx) = self.shutdown_tx.take() {
            let _ = tx.send(());
        }
    }
}

/// Start a fixture backend from a specification.
pub async fn start_fixture(spec: FixtureSpec) -> Result<FixtureHandle> {
    let (shutdown_tx, shutdown_rx) = oneshot::channel::<()>();

    let listen_addr = match spec.listen_port {
        Some(port) => format!("127.0.0.1:{port}"),
        None => "127.0.0.1:0".to_string(),
    };
    let listener = tokio::net::TcpListener::bind(&listen_addr)
        .await
        .with_context(|| format!("failed to bind fixture backend at {listen_addr}"))?;
    let addr = listener.local_addr()?;

    let state = Arc::new(FixtureState::new());
    let app = build_router(spec, state);

    tokio::spawn(async move {
        let _ = axum::serve(listener, app)
            .with_graceful_shutdown(async {
                let _ = shutdown_rx.await;
            })
            .await;
    });

    Ok(FixtureHandle {
        addr,
        shutdown_tx: Some(shutdown_tx),
    })
}

fn build_router(spec: FixtureSpec, state: Arc<FixtureState>) -> Router {
    let mut router = Router::new();
    for route in spec.routes {
        let behavior: Arc<RouteBehavior> = Arc::new(route.behavior);
        router = router.route(
            &route.path,
            get(move |method: Method, headers: HeaderMap| {
                let b = behavior.clone();
                async move { behavior_response(method, headers, b) }
            }),
        );
    }

    router
        .route(
            "/{*path}",
            get(
                |State(state): State<Arc<FixtureState>>,
                 method: Method,
                 headers: HeaderMap,
                 uri: OriginalUri| async move {
                    let path = uri.path().to_string();
                    scenario_response(state, method, headers, path)
                },
            ),
        )
        .with_state(state)
}

fn behavior_response(
    method: Method,
    headers: HeaderMap,
    behavior: Arc<RouteBehavior>,
) -> axum::response::Response {
    match &*behavior {
        RouteBehavior::Fixed {
            status,
            body,
            content_type,
            etag,
        } => {
            let mut fixture_headers = vec![("Content-Type".to_string(), content_type.clone())];
            if let Some(value) = etag {
                fixture_headers.push(("ETag".to_string(), value.clone()));
            }
            to_response(
                FixtureResponse {
                    status: *status,
                    headers: fixture_headers,
                    body: body.clone(),
                },
                &method,
            )
        }
        RouteBehavior::Conditional => {
            let etag = "\"fixture-conditional-etag\"";
            let if_none_match = header_value(&headers, "If-None-Match");
            let if_modified_since = header_value(&headers, "If-Modified-Since");
            let resp = crate::fixtures::handlers::conditional_handler(
                etag,
                if_none_match.as_deref(),
                if_modified_since.as_deref(),
                "Wed, 01 Jan 2025 00:00:00 GMT",
                "<h1>fixture conditional</h1>",
            );
            to_response(resp, &method)
        }
        RouteBehavior::Cache { max_age, vary } => {
            let resp = crate::fixtures::handlers::cache_handler(
                *max_age,
                vary.as_deref(),
                "<h1>fixture cache</h1>",
            );
            to_response(resp, &method)
        }
        RouteBehavior::Auth { cookie_name } => {
            let has_cookie = header_value(&headers, "Cookie")
                .map(|v| v.contains(cookie_name))
                .unwrap_or(false);
            let resp = crate::fixtures::handlers::auth_handler(cookie_name, has_cookie, "auth");
            to_response(resp, &method)
        }
        RouteBehavior::Brotli {
            body,
            chunk_size,
            fault,
        } => brotli_response(method, body, *chunk_size, fault),
        RouteBehavior::EncodingChain { body, chain, fault } => {
            encoding_chain_response(method, body, chain, fault)
        }
    }
}

/// Serve a multi-layer Content-Encoding chain response.
///
/// The body is compressed by applying the chain in application order
/// (first layer first, so the last layer is the outermost encoding), and
/// the `Content-Encoding` header lists the layer tokens in application
/// order.  `identity` layers perform no compression and no decompression.
fn encoding_chain_response(
    method: Method,
    source: &str,
    chain: &[EncodingLayer],
    fault: &EncodingFault,
) -> axum::response::Response {
    let (mut bytes, tokens) = match encoding_chain_fixture_bytes(source, chain, fault) {
        Ok(pair) => pair,
        Err(error) => {
            return plain_response(
                method,
                500,
                "text/plain",
                &format!("fixture chain compression failed: {error}"),
            );
        }
    };
    if matches!(fault, EncodingFault::Truncated) && bytes.len() > 8 {
        bytes.truncate(bytes.len() - 8);
    }
    let encoding_value = tokens.join(", ");
    let body = if method == Method::HEAD {
        Body::empty()
    } else {
        Body::from(bytes)
    };

    axum::response::Response::builder()
        .status(StatusCode::OK)
        .header("Content-Type", "text/html; charset=UTF-8")
        .header("Content-Encoding", encoding_value)
        .body(body)
        .unwrap_or_else(|error| {
            plain_response(
                Method::GET,
                500,
                "text/plain",
                &format!("fixture chain response failed: {error}"),
            )
        })
}

/// Produce the wire bytes and the Content-Encoding token list for a chain.
fn encoding_chain_fixture_bytes(
    source: &str,
    chain: &[EncodingLayer],
    fault: &EncodingFault,
) -> Result<(Vec<u8>, Vec<String>)> {
    let mut tokens: Vec<String> = Vec::with_capacity(chain.len());
    let mut bytes = source.as_bytes().to_vec();
    for layer in chain {
        match layer {
            EncodingLayer::Identity => tokens.push("identity".to_string()),
            EncodingLayer::Gzip => {
                tokens.push("gzip".to_string());
                bytes = gzip_bytes(&bytes)?;
            }
            EncodingLayer::Deflate => {
                tokens.push("deflate".to_string());
                bytes = deflate_bytes(&bytes)?;
            }
            EncodingLayer::Br => {
                tokens.push("br".to_string());
                bytes = brotli_bytes(&bytes)?;
            }
        }
    }
    match fault {
        EncodingFault::MalformedGrammar => {
            /* Replace the separator between the first two tokens with a
             * malformed grammar: "gzip,,deflate". */
            let joined = tokens.join(", ");
            let broken = if tokens.len() >= 2 {
                joined.replace(", ", ",,")
            } else {
                format!("{joined},,")
            };
            return Ok((bytes, vec![broken]));
        }
        EncodingFault::UnknownToken => {
            tokens.push("zstd".to_string());
        }
        EncodingFault::DepthOverflow => {
            for _ in 0..3 {
                tokens.push("gzip".to_string());
                bytes = gzip_bytes(&bytes)?;
            }
        }
        EncodingFault::EmptyWire => {
            /* Empty-input contract: the wire body is empty while the
             * declared chain stays intact — the chain decoder treats it
             * as a legal empty payload, not truncation. */
            bytes = Vec::new();
        }
        EncodingFault::None | EncodingFault::Truncated => {}
    }
    Ok((bytes, tokens))
}

fn gzip_bytes(data: &[u8]) -> Result<Vec<u8>> {
    use flate2::Compression;
    use flate2::write::GzEncoder;

    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder
        .write_all(data)
        .context("gzip fixture compression failed")?;
    encoder.finish().context("gzip fixture finish failed")
}

fn deflate_bytes(data: &[u8]) -> Result<Vec<u8>> {
    use flate2::Compression;
    use flate2::write::ZlibEncoder;

    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder
        .write_all(data)
        .context("deflate fixture compression failed")?;
    encoder.finish().context("deflate fixture finish failed")
}

fn brotli_bytes(data: &[u8]) -> Result<Vec<u8>> {
    let mut output = Vec::new();
    {
        let mut writer = brotli::CompressorWriter::new(&mut output, 4096, 5, 22);
        writer
            .write_all(data)
            .context("brotli fixture compression failed")?;
    }
    Ok(output)
}

fn brotli_response(
    method: Method,
    source: &str,
    chunk_size: usize,
    fault: &BrotliFault,
) -> axum::response::Response {
    let compressed = match brotli_fixture_bytes(source, fault) {
        Ok(bytes) => bytes,
        Err(error) => {
            return plain_response(
                method,
                500,
                "text/plain",
                &format!("fixture compression failed: {error}"),
            );
        }
    };
    let chunks = compressed
        .chunks(chunk_size.max(1))
        .map(|chunk| Ok::<Bytes, Infallible>(Bytes::copy_from_slice(chunk)))
        .collect::<Vec<_>>();
    let body = if method == Method::HEAD {
        Body::empty()
    } else {
        Body::from_stream(stream::iter(chunks))
    };

    axum::response::Response::builder()
        .status(StatusCode::OK)
        .header("Content-Type", "text/html; charset=UTF-8")
        .header("Content-Encoding", "br")
        .body(body)
        .unwrap_or_else(|error| {
            plain_response(
                Method::GET,
                500,
                "text/plain",
                &format!("fixture response failed: {error}"),
            )
        })
}

fn brotli_fixture_bytes(source: &str, fault: &BrotliFault) -> Result<Vec<u8>> {
    if matches!(fault, BrotliFault::Malformed) {
        return Ok(vec![0xff; 64]);
    }

    let mut compressed = Vec::new();
    {
        let mut writer = brotli::CompressorWriter::new(&mut compressed, 4096, 5, 22);
        writer
            .write_all(source.as_bytes())
            .context("failed to compress Brotli fixture")?;
    }

    match fault {
        BrotliFault::None | BrotliFault::Malformed => {}
        BrotliFault::TrailingData => compressed.extend_from_slice(b"trailing-data"),
        BrotliFault::Truncated => {
            let retained = compressed.len().saturating_sub(3);
            compressed.truncate(retained);
        }
    }
    Ok(compressed)
}

fn scenario_response(
    state: Arc<FixtureState>,
    method: Method,
    headers: HeaderMap,
    path: String,
) -> axum::response::Response {
    if path == "/metrics" {
        // The metrics route reports the request counters; it must not
        // increment the counter it is reporting (that would skew the
        // served totals by one per scrape).
        return metrics_response(state, method, headers);
    }
    state.total_requests.fetch_add(1, Ordering::Relaxed);

    if path == "/md/redirect-target" {
        return html_response(method, 200, "<h1>redirect target</h1>", true, None, None);
    }
    if let Some(code) = path.strip_prefix("/md/")
        && let Ok(status_code) = code.parse::<u16>()
    {
        return status_code_response(method, status_code);
    }
    if path == "/md/json" {
        return plain_response(method, 200, "application/json", "{\"ok\":true}");
    }
    if path == "/md/plain" {
        return plain_response(method, 200, "text/plain", "plain");
    }
    if path == "/md/test.html" {
        state.converted_total.fetch_add(1, Ordering::Relaxed);
        /* Serve HTML so the module performs the conversion and records
         * outcome="converted" in nginx_markdown_requests_total. */
        return html_response(
            method,
            200,
            "<html><body><h1>metrics converted</h1></body></html>",
            true,
            Some("\"converted-test-etag\""),
            Some("Accept"),
        );
    }
    if path == "/no-wildcard/html" {
        return html_or_markdown_by_accept(
            &state,
            method,
            &headers,
            "<h1>wildcard off</h1>",
            false,
            false,
            false,
        );
    }
    if path == "/md-deny/html" {
        return html_or_markdown_by_accept(
            &state,
            method,
            &headers,
            "<h1>deny mode</h1>",
            false,
            true,
            true,
        );
    }
    if path == "/md/html" {
        return md_html_response(state, method, headers);
    }
    plain_response(method, 404, "text/plain", "not found")
}

/// Conditional/cache-aware response for `/md/html`.
///
/// ETag / If-None-Match logic:
/// - INM `*` matches any entity (returns 304).
/// - Strong ETag match (exact string) returns 304.
/// - Weak ETag match (`W/"<etag>"`) returns 304.
///
/// If-Modified-Since: a sentinel date containing "2030" triggers 304.
///
/// 304 early return: sends headers (ETag, Vary) but no body,
/// preserving cache metadata for downstream.
///
/// Auth-based cache policy:
/// - Authenticated (Cookie contains `session_user=`): Cache-Control `private, max-age=0`, Vary `Cookie`.
/// - Unauthenticated: Cache-Control `public, max-age=60`, Vary `Accept`.
///
/// Conversion happens before cache metadata is applied: the
/// `html_or_markdown_by_accept` call produces the response body,
/// then `into_response_with_cache` appends Cache-Control/ETag/Vary.
fn md_html_response(
    state: Arc<FixtureState>,
    method: Method,
    headers: HeaderMap,
) -> axum::response::Response {
    let etag = "\"converted-etag-12345\"";
    let if_none_match = header_value(&headers, "If-None-Match").unwrap_or_default();
    let if_modified_since = header_value(&headers, "If-Modified-Since").unwrap_or_default();
    let cookie = header_value(&headers, "Cookie").unwrap_or_default();
    let is_auth = cookie.contains("session_user=");

    let inm_matches =
        if_none_match == "*" || if_none_match == etag || if_none_match == format!("W/{etag}");
    let ims_not_modified = if_modified_since.contains("2030");
    if inm_matches || ims_not_modified {
        return plain_response_with_headers(
            method,
            304,
            "",
            vec![
                ("ETag".to_string(), etag.to_string()),
                ("Vary".to_string(), "Accept".to_string()),
            ],
            "",
        );
    }

    let cc = if is_auth {
        "private, max-age=0"
    } else {
        "public, max-age=60"
    };
    let vary = if is_auth {
        Some("Cookie")
    } else {
        Some("Accept")
    };

    /* HEAD requests: the upstream serves its source representation
     * (HTML) exactly like it would for GET — the conversion module is
     * responsible for rewriting the representation headers.  Returning
     * Markdown here would make the fixture mimic a converter that does
     * not exist upstream, and the module would pass the already-Markdown
     * response through untouched. */
    if method == Method::HEAD {
        return html_response(method, 200, "<h1>fixture html</h1>", true, Some(etag), vary)
            .into_response_with_cache(cc, etag, vary);
    }

    html_or_markdown_by_accept(
        &state,
        method,
        &headers,
        "<h1>fixture html</h1>",
        true,
        false,
        is_auth,
    )
    .into_response_with_cache(cc, etag, vary)
}

trait IntoResponseWithCache {
    fn into_response_with_cache(
        self,
        cache_control: &str,
        etag: &str,
        vary: Option<&str>,
    ) -> axum::response::Response;
}

impl IntoResponseWithCache for axum::response::Response {
    fn into_response_with_cache(
        mut self,
        cache_control: &str,
        etag: &str,
        vary: Option<&str>,
    ) -> axum::response::Response {
        let headers = self.headers_mut();
        headers.insert("Cache-Control", header_value_or_empty(cache_control));
        headers.insert("ETag", header_value_or_empty(etag));
        if let Some(v) = vary {
            headers.insert("Vary", header_value_or_empty(v));
        }
        self
    }
}

/// Content-negotiation response: return Markdown or HTML based on Accept header.
///
/// Expected Accept formats:
/// - `text/markdown` → converted Markdown response
/// - `text/html` or empty → original HTML response
/// - `*/*` → depends on `wildcard_converts` flag
///
/// `wildcard_requested` is true when Accept contains `*/*`.
/// `wildcard_converts` controls whether `*/*` triggers conversion.
/// Their interaction: conversion happens only when both are true.
///
/// HTML precedence: explicit `text/html` or empty Accept always
/// returns HTML, even if `text/markdown` is also present.
///
/// `should_convert` invariant: conversion occurs when
///   `!force_html && (markdown_requested || (wildcard_requested && wildcard_converts)) && !html_requested`
///
/// Vary header selection: `Cookie` when `is_auth`, else `Accept`.
/// Cookie is used for Vary in auth contexts because the auth state
/// (presence of session cookie) varies independently of Accept.
fn html_or_markdown_by_accept(
    state: &Arc<FixtureState>,
    method: Method,
    headers: &HeaderMap,
    html_body: &str,
    wildcard_converts: bool,
    force_html: bool,
    is_auth: bool,
) -> axum::response::Response {
    let accept = header_value(headers, "Accept").unwrap_or_default();
    let markdown_requested = accept.contains("text/markdown");
    let wildcard_requested = accept.contains("*/*");
    let html_requested = accept.contains("text/html") || accept.is_empty();

    let should_convert = !force_html
        && (markdown_requested || (wildcard_requested && wildcard_converts))
        && !html_requested;
    if should_convert {
        state.converted_total.fetch_add(1, Ordering::Relaxed);
        let vary = if is_auth {
            Some("Cookie")
        } else {
            Some("Accept")
        };
        markdown_response(
            method,
            200,
            "# converted markdown",
            true,
            Some("\"converted-etag-12345\""),
            vary,
            None,
        )
    } else {
        html_response(
            method,
            200,
            html_body,
            true,
            Some("\"converted-etag-12345\""),
            None,
        )
    }
}

fn status_code_response(method: Method, status_code: u16) -> axum::response::Response {
    match status_code {
        301 | 302 => plain_response_with_headers(
            method,
            status_code,
            "text/html",
            vec![("Location".to_string(), "/md/redirect-target".to_string())],
            "<h1>redirect</h1>",
        ),
        403 | 404 | 410 | 500 | 502 | 503 => {
            html_response(method, status_code, "<h1>error</h1>", true, None, None)
        }
        _ => plain_response(method, 404, "text/plain", "unsupported status path"),
    }
}

fn metrics_response(
    state: Arc<FixtureState>,
    method: Method,
    _headers: HeaderMap,
) -> axum::response::Response {
    let total = state.total_requests.load(Ordering::Relaxed);
    let converted = state.converted_total.load(Ordering::Relaxed);
    let skipped = total.saturating_sub(converted);
    let body = format!(
        "# HELP nginx_markdown_requests_total Requests entering the decision chain\n\
# TYPE nginx_markdown_requests_total counter\n\
nginx_markdown_requests_total{{outcome=\"converted\",stage=\"delivery\",reason=\"converted\"}} {}\n\
nginx_markdown_requests_total{{outcome=\"skipped\",stage=\"eligibility\",reason=\"not_eligible\"}} {}\n\
# HELP nginx_markdown_conversion_attempts_total Conversion attempts\n\
# TYPE nginx_markdown_conversion_attempts_total counter\n\
nginx_markdown_conversion_attempts_total{{engine=\"full_buffer\"}} {}\n\
# HELP nginx_markdown_conversion_deliveries_total Successful converted deliveries\n\
# TYPE nginx_markdown_conversion_deliveries_total counter\n\
nginx_markdown_conversion_deliveries_total{{engine=\"full_buffer\"}} {}\n\
# HELP nginx_markdown_conversion_duration_seconds Conversion duration\n\
# TYPE nginx_markdown_conversion_duration_seconds histogram\n\
nginx_markdown_conversion_duration_seconds_bucket{{engine=\"full_buffer\",le=\"+Inf\"}} {}\n\
nginx_markdown_conversion_duration_seconds_sum{{engine=\"full_buffer\"}} 0\n\
nginx_markdown_conversion_duration_seconds_count{{engine=\"full_buffer\"}} {}\n\
# HELP nginx_markdown_input_bytes_total Input bytes\n\
# TYPE nginx_markdown_input_bytes_total counter\n\
nginx_markdown_input_bytes_total 1024\n\
# HELP nginx_markdown_output_bytes_total Output bytes\n\
# TYPE nginx_markdown_output_bytes_total counter\n\
nginx_markdown_output_bytes_total 1024\n\
# HELP nginx_markdown_inflight_requests In-flight conversions\n\
# TYPE nginx_markdown_inflight_requests gauge\n\
nginx_markdown_inflight_requests 0\n\
# HELP nginx_markdown_streaming_events_total Streaming lifecycle events\n\
# TYPE nginx_markdown_streaming_events_total counter\n\
nginx_markdown_streaming_events_total{{transition=\"commit\",reason=\"converted\"}} 0\n\
# HELP nginx_markdown_decompression_events_total Decompression events\n\
# TYPE nginx_markdown_decompression_events_total counter\n\
nginx_markdown_decompression_events_total{{encoding=\"gzip\",outcome=\"success\",reason=\"ok\"}} 0\n\
# HELP nginx_markdown_dynconf_reloads_total Dynamic configuration reloads\n\
# TYPE nginx_markdown_dynconf_reloads_total counter\n\
nginx_markdown_dynconf_reloads_total{{outcome=\"success\",reason=\"ok\"}} 0\n\
# HELP nginx_markdown_build_info Build information\n\
# TYPE nginx_markdown_build_info gauge\n\
nginx_markdown_build_info{{version=\"test\",nginx_version=\"test\",features=\"\"}} 1\n",
        converted, skipped, converted, converted, converted, converted,
    );
    plain_response(
        method,
        200,
        "text/plain; version=0.0.4; charset=utf-8",
        &body,
    )
}

fn header_value(headers: &HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|v| v.to_str().ok())
        .map(|v| v.to_string())
}

fn markdown_response(
    method: Method,
    status: u16,
    body: &str,
    include_ct: bool,
    etag: Option<&str>,
    vary: Option<&str>,
    cache_control: Option<&str>,
) -> axum::response::Response {
    let mut headers = Vec::new();
    if include_ct {
        headers.push(("Content-Type".to_string(), "text/markdown".to_string()));
    }
    if let Some(value) = etag {
        headers.push(("ETag".to_string(), value.to_string()));
    }
    if let Some(value) = vary {
        headers.push(("Vary".to_string(), value.to_string()));
    }
    if let Some(value) = cache_control {
        headers.push(("Cache-Control".to_string(), value.to_string()));
    }
    plain_response_with_headers(method, status, "", headers, body)
}

fn html_response(
    method: Method,
    status: u16,
    body: &str,
    include_ct: bool,
    etag: Option<&str>,
    vary: Option<&str>,
) -> axum::response::Response {
    let mut headers = Vec::new();
    if include_ct {
        headers.push(("Content-Type".to_string(), "text/html".to_string()));
    }
    if let Some(value) = etag {
        headers.push(("ETag".to_string(), value.to_string()));
    }
    if let Some(value) = vary {
        headers.push(("Vary".to_string(), value.to_string()));
    }
    plain_response_with_headers(method, status, "", headers, body)
}

fn plain_response(
    method: Method,
    status: u16,
    content_type: &str,
    body: &str,
) -> axum::response::Response {
    plain_response_with_headers(method, status, content_type, Vec::new(), body)
}

fn plain_response_with_headers(
    method: Method,
    status: u16,
    content_type: &str,
    mut extra_headers: Vec<(String, String)>,
    body: &str,
) -> axum::response::Response {
    if !content_type.is_empty() {
        extra_headers.push(("Content-Type".to_string(), content_type.to_string()));
    }
    let mut headers = HeaderMap::new();
    for (name, value) in extra_headers {
        if let (Ok(name), Ok(value)) = (
            axum::http::header::HeaderName::from_bytes(name.as_bytes()),
            HeaderValue::from_str(&value),
        ) {
            headers.insert(name, value);
        }
    }
    let status_code = StatusCode::from_u16(status).unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let response_body = if method == Method::HEAD {
        String::new()
    } else {
        body.to_string()
    };
    (status_code, headers, response_body).into_response()
}

fn to_response(resp: FixtureResponse, method: &Method) -> axum::response::Response {
    plain_response_with_headers(method.clone(), resp.status, "", resp.headers, &resp.body)
}

fn header_value_or_empty(value: &str) -> HeaderValue {
    HeaderValue::from_str(value).unwrap_or_else(|_| HeaderValue::from_static(""))
}
use futures_util::stream;
use std::convert::Infallible;
use std::io::Write;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chain_tokens_and_wire_bytes_match_application_order() {
        let source = "<html><body>chain fixture</body></html>";
        let chain = vec![
            EncodingLayer::Gzip,
            EncodingLayer::Deflate,
            EncodingLayer::Br,
        ];
        let (bytes, tokens) =
            encoding_chain_fixture_bytes(source, &chain, &EncodingFault::None).unwrap();
        assert_eq!(tokens, vec!["gzip", "deflate", "br"]);
        /* The wire body is the outer (br) layer: decompressing br must
         * reveal deflate(gzip(source)). */
        let mut out = Vec::new();
        {
            let mut reader = brotli::Decompressor::new(bytes.as_slice(), 4096);
            std::io::Read::read_to_end(&mut reader, &mut out).unwrap();
        }
        let mut deflated = Vec::new();
        {
            let mut decoder = flate2::read::ZlibDecoder::new(out.as_slice());
            std::io::Read::read_to_end(&mut decoder, &mut deflated).unwrap();
        }
        let mut gunzipped = Vec::new();
        {
            let mut decoder = flate2::read::MultiGzDecoder::new(deflated.as_slice());
            std::io::Read::read_to_end(&mut decoder, &mut gunzipped).unwrap();
        }
        assert_eq!(gunzipped, source.as_bytes());
    }

    #[test]
    fn identity_layers_are_transparent() {
        let source = "<html><body>identity</body></html>";
        let chain = vec![EncodingLayer::Identity, EncodingLayer::Gzip];
        let (bytes, tokens) =
            encoding_chain_fixture_bytes(source, &chain, &EncodingFault::None).unwrap();
        assert_eq!(tokens, vec!["identity", "gzip"]);
        let mut out = Vec::new();
        {
            let mut decoder = flate2::read::MultiGzDecoder::new(bytes.as_slice());
            std::io::Read::read_to_end(&mut decoder, &mut out).unwrap();
        }
        assert_eq!(out, source.as_bytes());
    }

    #[test]
    fn malformed_grammar_fault_breaks_the_encoding_value() {
        let source = "<html><body>malformed</body></html>";
        let chain = vec![EncodingLayer::Gzip, EncodingLayer::Deflate];
        let (bytes, tokens) =
            encoding_chain_fixture_bytes(source, &chain, &EncodingFault::MalformedGrammar).unwrap();
        assert!(!bytes.is_empty());
        assert_eq!(tokens.len(), 1);
        assert_eq!(tokens[0], "gzip,,deflate");
    }

    #[test]
    fn unknown_token_fault_appends_unsupported_token() {
        let source = "<html><body>unknown</body></html>";
        let chain = vec![EncodingLayer::Gzip];
        let (_, tokens) =
            encoding_chain_fixture_bytes(source, &chain, &EncodingFault::UnknownToken).unwrap();
        assert_eq!(tokens, vec!["gzip", "zstd"]);
    }

    #[test]
    fn depth_overflow_fault_exceeds_three_layers() {
        let source = "<html><body>depth</body></html>";
        let chain = vec![EncodingLayer::Gzip];
        let (bytes, tokens) =
            encoding_chain_fixture_bytes(source, &chain, &EncodingFault::DepthOverflow).unwrap();
        /* 1 base layer + 3 fault layers = 4 non-identity layers > 3. */
        assert_eq!(tokens.len(), 4);
        let mut out = Vec::new();
        {
            let mut decoder = flate2::read::MultiGzDecoder::new(bytes.as_slice());
            std::io::Read::read_to_end(&mut decoder, &mut out).unwrap();
        }
        assert!(!out.is_empty());
    }
}
