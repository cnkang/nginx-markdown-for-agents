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
//! # Safety
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

        if url.starts_with("http://") || url.starts_with("https://") || url.starts_with("//") {
            return url.to_string();
        }

        let Some(ref base) = self.base_url else {
            return url.to_string();
        };

        if !self.is_valid_base_url(base) {
            return url.to_string();
        }

        if url.starts_with('/') {
            return format!("{}{}", self.get_origin(base), url);
        }

        let base_dir = self.get_base_directory(base);
        format!("{}/{}", base_dir.trim_end_matches('/'), url)
    }

    /// Validate whether `url` has an absolute HTTP(S)-style base form.
    ///
    /// # Arguments
    ///
    /// * `url` - URL string to validate
    ///
    /// # Returns
    ///
    /// `true` if the URL starts with `http://` or `https://`.
    fn is_valid_base_url(&self, url: &str) -> bool {
        url.starts_with("http://") || url.starts_with("https://")
    }

    /// Extract scheme + authority origin from an absolute URL string.
    ///
    /// For `https://example.com/path/page`, returns `https://example.com`.
    /// If the URL has no path component after the authority, the full URL
    /// is returned.
    ///
    /// # Arguments
    ///
    /// * `url` - An absolute URL with `http://` or `https://` scheme
    ///
    /// # Returns
    ///
    /// The origin portion (scheme + authority) of the URL.
    fn get_origin(&self, url: &str) -> String {
        let after_scheme = if let Some(stripped) = url.strip_prefix("https://") {
            stripped
        } else if let Some(stripped) = url.strip_prefix("http://") {
            stripped
        } else {
            return url.to_string();
        };

        if let Some(pos) = after_scheme.find('/') {
            let scheme_len = if url.starts_with("https://") { 8 } else { 7 };
            url[..scheme_len + pos].to_string()
        } else {
            url.to_string()
        }
    }

    /// Return the directory prefix used for relative-path resolution.
    ///
    /// For `https://example.com/dir/page`, returns `https://example.com/dir`.
    /// Trailing slashes are stripped before the last path segment is removed.
    /// If the URL has no path after the authority, the full URL is returned.
    ///
    /// # Arguments
    ///
    /// * `url` - An absolute URL string
    ///
    /// # Returns
    ///
    /// The URL with the last path segment removed, suitable for joining
    /// with a relative path.
    fn get_base_directory(&self, url: &str) -> String {
        let trimmed = url.trim_end_matches('/');

        if let Some(pos) = trimmed.rfind('/') {
            if pos > 0 && trimmed.chars().nth(pos - 1) == Some('/') {
                return trimmed.to_string();
            }
            trimmed[..pos].to_string()
        } else {
            trimmed.to_string()
        }
    }
}
