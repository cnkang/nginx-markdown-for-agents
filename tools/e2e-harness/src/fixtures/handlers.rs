//! Built-in fixture handlers for common E2E patterns.
//!
//! These handlers simulate upstream behaviors that NGINX interacts with
//! in production: conditional requests, caching, and authentication.

use crate::fixtures::FixtureResponse;
use httpdate::parse_http_date;

/// Handler for conditional request behavior (ETag / If-Modified-Since / If-None-Match).
///
/// # Arguments
///
/// * `etag` - ETag value to serve.
/// * `if_none_match` - Value of the `If-None-Match` request header.
/// * `if_modified_since` - Value of the `If-Modified-Since` request header.
/// * `last_modified` - `Last-Modified` value for the resource.
/// * `body` - Response body when the resource is served.
///
/// # Returns
///
/// A `FixtureResponse` appropriate for the conditional headers.
pub fn conditional_handler(
    etag: &str,
    if_none_match: Option<&str>,
    if_modified_since: Option<&str>,
    last_modified: &str,
    body: &str,
) -> FixtureResponse {
    if let Some(inm) = if_none_match {
        let normalized_etag = normalize_etag(etag);
        let matches_etag = inm == "*"
            || inm
                .split(',')
                .any(|v| normalize_etag(v.trim()) == normalized_etag);
        if matches_etag {
            return FixtureResponse {
                status: 304,
                headers: vec![("ETag".to_string(), etag.to_string())],
                body: String::new(),
            };
        }
    }

    if let Some(ims) = if_modified_since
        && let (Ok(ims_time), Ok(last_modified_time)) =
            (parse_http_date(ims), parse_http_date(last_modified))
        && ims_time >= last_modified_time
    {
        return FixtureResponse {
            status: 304,
            headers: vec![
                ("ETag".to_string(), etag.to_string()),
                ("Last-Modified".to_string(), last_modified.to_string()),
            ],
            body: String::new(),
        };
    }

    FixtureResponse {
        status: 200,
        headers: vec![
            ("ETag".to_string(), etag.to_string()),
            ("Last-Modified".to_string(), last_modified.to_string()),
            ("Content-Type".to_string(), "text/html".to_string()),
        ],
        body: body.to_string(),
    }
}

fn normalize_etag(tag: &str) -> &str {
    tag.trim()
        .strip_prefix("W/")
        .unwrap_or(tag.trim())
        .trim_matches('"')
}

/// Handler for cache behavior simulation.
///
/// # Arguments
///
/// * `max_age` - `Cache-Control: max-age` value.
/// * `vary` - Optional `Vary` header value.
/// * `body` - Response body.
///
/// # Returns
///
/// A `FixtureResponse` with cache headers.
pub fn cache_handler(max_age: u32, vary: Option<&str>, body: &str) -> FixtureResponse {
    let mut headers = vec![
        (
            "Cache-Control".to_string(),
            format!("public, max-age={max_age}"),
        ),
        ("Content-Type".to_string(), "text/html".to_string()),
    ];
    if let Some(v) = vary {
        headers.push(("Vary".to_string(), v.to_string()));
    }
    FixtureResponse {
        status: 200,
        headers,
        body: body.to_string(),
    }
}

/// Handler for authentication cookie detection.
///
/// # Arguments
///
/// * `cookie_name` - Name of the auth cookie to detect.
/// * `has_cookie` - Whether the cookie is present in the request.
/// * `body` - Response body.
///
/// # Returns
///
/// A `FixtureResponse` indicating authentication state.
pub fn auth_handler(cookie_name: &str, has_cookie: bool, body: &str) -> FixtureResponse {
    let status = if has_cookie { 200 } else { 401 };
    let auth_header = if has_cookie {
        "authenticated"
    } else {
        "unauthenticated"
    };
    FixtureResponse {
        status,
        headers: vec![
            ("X-Auth-State".to_string(), auth_header.to_string()),
            ("X-Cookie-Name".to_string(), cookie_name.to_string()),
        ],
        body: body.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_conditional_handler_not_modified_etag() {
        let resp = conditional_handler(
            "\"abc123\"",
            Some("\"abc123\""),
            None,
            "Wed, 01 Jan 2025 00:00:00 GMT",
            "<html>content</html>",
        );
        assert_eq!(resp.status, 304);
        assert!(resp.body.is_empty());
    }

    #[test]
    fn test_conditional_handler_full_response() {
        let resp = conditional_handler(
            "\"abc123\"",
            None,
            None,
            "Wed, 01 Jan 2025 00:00:00 GMT",
            "<html>content</html>",
        );
        assert_eq!(resp.status, 200);
        assert_eq!(resp.body, "<html>content</html>");
    }

    #[test]
    fn test_conditional_handler_wildcard_if_none_match() {
        let resp = conditional_handler(
            "\"abc123\"",
            Some("*"),
            None,
            "Wed, 01 Jan 2025 00:00:00 GMT",
            "<html>content</html>",
        );
        assert_eq!(resp.status, 304);
    }

    #[test]
    fn test_conditional_handler_weak_if_none_match() {
        let resp = conditional_handler(
            "\"abc123\"",
            Some("W/\"abc123\""),
            None,
            "Wed, 01 Jan 2025 00:00:00 GMT",
            "<html>content</html>",
        );
        assert_eq!(resp.status, 304);
    }

    #[test]
    fn test_conditional_handler_ims_future_not_modified() {
        let resp = conditional_handler(
            "\"abc123\"",
            None,
            Some("Tue, 01 Jan 2030 00:00:00 GMT"),
            "Wed, 01 Jan 2025 00:00:00 GMT",
            "<html>content</html>",
        );
        assert_eq!(resp.status, 304);
    }

    #[test]
    fn test_conditional_handler_ims_past_modified() {
        let resp = conditional_handler(
            "\"abc123\"",
            None,
            Some("Wed, 01 Jan 2020 00:00:00 GMT"),
            "Wed, 01 Jan 2025 00:00:00 GMT",
            "<html>content</html>",
        );
        assert_eq!(resp.status, 200);
    }

    #[test]
    fn test_cache_handler() {
        let resp = cache_handler(3600, Some("Accept"), "body");
        assert_eq!(resp.status, 200);
        assert!(
            resp.headers
                .iter()
                .any(|(k, v)| k == "Cache-Control" && v.contains("3600"))
        );
        assert!(
            resp.headers
                .iter()
                .any(|(k, v)| k == "Vary" && v == "Accept")
        );
    }

    #[test]
    fn test_auth_handler_with_cookie() {
        let resp = auth_handler("session", true, "ok");
        assert_eq!(resp.status, 200);
    }

    #[test]
    fn test_auth_handler_without_cookie() {
        let resp = auth_handler("session", false, "");
        assert_eq!(resp.status, 401);
    }
}
