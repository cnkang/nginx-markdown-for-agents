//! Page metadata extraction for YAML front matter
//!
//! This module provides functionality to extract metadata from HTML documents
//! for use in YAML front matter generation. It supports:
//!
//! - Title extraction from `<title>` tag and Open Graph tags
//! - Description extraction from meta tags
//! - URL, image, author, and published date extraction
//! - URL resolution for relative links and images

mod extract;
mod resolve;
#[cfg(test)]
mod tests;

/// Page metadata extracted from HTML.
#[derive(Debug, Default, Clone, PartialEq)]
pub struct PageMetadata {
    pub title: Option<String>,
    pub description: Option<String>,
    pub url: Option<String>,
    pub image: Option<String>,
    pub author: Option<String>,
    pub published: Option<String>,
}

impl PageMetadata {
    /// Create an empty metadata container.
    pub fn new() -> Self {
        Self::default()
    }

    /// Estimate the in-memory byte size of populated metadata fields.
    ///
    /// Used by the streaming converter to track the working set
    /// contribution of extracted head metadata.
    pub fn bytes_estimate(&self) -> usize {
        let mut total = 0usize;
        if let Some(ref s) = self.title {
            total = total.saturating_add(s.len());
        }
        if let Some(ref s) = self.description {
            total = total.saturating_add(s.len());
        }
        if let Some(ref s) = self.url {
            total = total.saturating_add(s.len());
        }
        if let Some(ref s) = self.image {
            total = total.saturating_add(s.len());
        }
        if let Some(ref s) = self.author {
            total = total.saturating_add(s.len());
        }
        if let Some(ref s) = self.published {
            total = total.saturating_add(s.len());
        }
        total
    }
}

/// Metadata extractor with optional URL resolution.
pub struct MetadataExtractor {
    base_url: Option<String>,
    resolve_urls: bool,
}

impl MetadataExtractor {
    /// Create a metadata extractor.
    ///
    /// `base_url` is used for canonical fallback and optional relative URL
    /// resolution; `resolve_urls` toggles whether relative references are
    /// rewritten to absolute URLs.
    pub fn new(base_url: Option<String>, resolve_urls: bool) -> Self {
        Self {
            base_url,
            resolve_urls,
        }
    }
}
