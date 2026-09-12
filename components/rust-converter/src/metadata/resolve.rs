//! URL resolution for metadata extraction.
//!
//! This module provides URL resolution logic that converts relative URLs
//! found in HTML metadata (canonical links, Open Graph images, etc.) into
//! absolute URLs using a configured base URL. This is essential for AI
//! agents that need actionable URLs regardless of the source page's
//! relative linking structure.
//!
//! # Resolution Rules
//!
//! | Input URL | Base URL | Result |
//! |----------|----------|--------|
//! | `https://example.com/page` | any | `https://example.com/page` (already absolute) |
//! | `//example.com/page` | any | `//example.com/page` (protocol-relative) |
//! | `/path/to/page` | `https://host/` | `https://host/path/to/page` |
//! | `relative/path` | `https://host/dir/` | `https://host/dir/relative/path` |
//! | empty string | any | empty string (no resolution) |
//!
//! # Validation
//!
//! Base URL validation requires an `http://` or `https://` scheme. Invalid
//! base URLs cause the original relative URL to be returned unchanged,
//! rather than producing a malformed absolute URL.

use super::MetadataExtractor;

impl MetadataExtractor {
    /// Resolve relative URL to absolute URL.
    ///
    /// If URL resolution is disabled (`resolve_urls == false`) or the URL is
    /// empty, the input is returned unchanged. Already-absolute URLs (with
    /// `http://`, `https://`, or `//` prefix) are also returned unchanged.
    ///
    /// For relative URLs, the base URL's origin (scheme + authority) or
    /// directory prefix is used to construct the absolute form.
    ///
    /// # Arguments
    ///
    /// * `url` - The URL to resolve (may be relative, absolute, or empty)
    ///
    /// # Returns
    ///
    /// The resolved absolute URL, or the original URL if resolution is not
    /// possible or not enabled.
    pub fn resolve_url(&self, url: &str) -> String {
        if !self.resolve_urls || url.is_empty() {
            return url.to_string();
        }

        let Some(base) = self.base_url.as_deref() else {
            return url.to_string();
        };

        // One shared resolver serves the body emitters and every metadata
        // field, so the same reference cannot resolve differently by path.
        crate::url_resolve::resolve_reference(base, url).unwrap_or_else(|| url.to_string())
    }
}
