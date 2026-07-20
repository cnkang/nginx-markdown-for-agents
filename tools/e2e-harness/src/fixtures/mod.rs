//! Fixture backends for E2E scenarios.
//!
//! Provides embedded HTTP servers that simulate upstream behavior
//! (conditional responses, caching, authentication) so scenarios
//! can exercise NGINX proxy interactions without external dependencies.

pub mod handlers;
pub mod http_backend;

use serde::{Deserialize, Serialize};

/// Specification for a fixture route.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RouteSpec {
    /// URL path to match (e.g. "/api/data").
    pub path: String,
    /// Behavior for this route.
    pub behavior: RouteBehavior,
}

/// Behavior specification for a fixture route.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum RouteBehavior {
    /// Return a fixed response with optional ETag.
    Fixed {
        status: u16,
        body: String,
        content_type: String,
        etag: Option<String>,
    },
    /// Conditional response (ETag / If-Modified-Since / If-None-Match).
    Conditional,
    /// Cache behavior simulation.
    Cache { max_age: u32, vary: Option<String> },
    /// Auth cookie detection.
    Auth { cookie_name: String },
    /// Brotli-compressed HTML streamed in deterministic wire chunks.
    Brotli {
        body: String,
        chunk_size: usize,
        fault: BrotliFault,
    },
}

/// Optional mutation applied after creating a valid Brotli stream.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub enum BrotliFault {
    /// Return a valid stream.
    #[default]
    None,
    /// Append bytes after the completed stream.
    TrailingData,
    /// Remove the final bytes from the stream.
    Truncated,
    /// Return deterministic bytes that are not a Brotli stream.
    Malformed,
}

/// Complete fixture specification.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct FixtureSpec {
    /// Optional explicit listen port for the embedded fixture.
    pub listen_port: Option<u16>,
    /// Routes to register.
    pub routes: Vec<RouteSpec>,
}

/// Response returned by a fixture route handler.
#[derive(Clone, Debug)]
pub struct FixtureResponse {
    /// HTTP status code.
    pub status: u16,
    /// Response headers.
    pub headers: Vec<(String, String)>,
    /// Response body.
    pub body: String,
}
