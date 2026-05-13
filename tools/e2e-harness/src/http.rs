//! HTTP request helpers for E2E scenario assertions.
//!
//! Wraps `reqwest::blocking` to provide simple GET/HEAD functions with
//! optional custom headers, returning an `HttpResponse` that can be
//! inspected by the assertion engine.

use anyhow::Result;
use std::collections::HashMap;
use std::sync::OnceLock;
use std::time::Duration;

/// Captured HTTP response for scenario assertions.
#[derive(Debug)]
pub struct HttpResponse {
    /// HTTP status code.
    pub status: u16,
    /// Response headers.
    pub headers: reqwest::header::HeaderMap,
    /// Response body as UTF-8 text.
    pub body: String,
}

fn shared_client() -> &'static reqwest::blocking::Client {
    static CLIENT: OnceLock<reqwest::blocking::Client> = OnceLock::new();
    CLIENT.get_or_init(|| {
        reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(10))
            .build()
            .expect("failed to build shared reqwest blocking client")
    })
}

/// Send a GET request and return the response.
///
/// # Arguments
///
/// * `url` - Fully qualified URL.
///
/// # Returns
///
/// An `HttpResponse` on success.
pub fn get(url: &str) -> Result<HttpResponse> {
    let resp = shared_client().get(url).send()?;
    Ok(to_http_response(resp))
}

/// Send a HEAD request and return the response.
///
/// # Arguments
///
/// * `url` - Fully qualified URL.
///
/// # Returns
///
/// An `HttpResponse` with an empty body on success.
pub fn head(url: &str) -> Result<HttpResponse> {
    let resp = shared_client().head(url).send()?;
    Ok(to_http_response(resp))
}

/// Send a GET request with custom headers and return the response.
///
/// # Arguments
///
/// * `url` - Fully qualified URL.
/// * `headers` - Header name-value pairs to include.
///
/// # Returns
///
/// An `HttpResponse` on success.
pub fn get_with_headers(url: &str, headers: &HashMap<String, String>) -> Result<HttpResponse> {
    let mut req = shared_client().get(url);
    for (key, value) in headers {
        req = req.header(key.as_str(), value.as_str());
    }
    let resp = req.send()?;
    Ok(to_http_response(resp))
}

/// Convert a `reqwest::blocking::Response` into our `HttpResponse`.
fn to_http_response(resp: reqwest::blocking::Response) -> HttpResponse {
    let status = resp.status().as_u16();
    let headers = resp.headers().clone();
    let body = resp.text().unwrap_or_default();
    HttpResponse {
        status,
        headers,
        body,
    }
}
