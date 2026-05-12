//! HTTP request helpers for E2E scenario assertions.
//!
//! Wraps `reqwest::blocking` to provide simple GET/HEAD functions with
//! optional custom headers, returning an `HttpResponse` that can be
//! inspected by the assertion engine.

use anyhow::Result;
use std::collections::HashMap;

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
    let resp = reqwest::blocking::Client::new()
        .get(url)
        .timeout(std::time::Duration::from_secs(10))
        .send()?;
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
    let resp = reqwest::blocking::Client::new()
        .head(url)
        .timeout(std::time::Duration::from_secs(10))
        .send()?;
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
    let mut req = reqwest::blocking::Client::new()
        .get(url)
        .timeout(std::time::Duration::from_secs(10));
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
