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
    pub fn new() -> Self {
        Self::default()
    }
}

/// Metadata extractor with optional URL resolution.
pub struct MetadataExtractor {
    base_url: Option<String>,
    resolve_urls: bool,
}

impl MetadataExtractor {
    pub fn new(base_url: Option<String>, resolve_urls: bool) -> Self {
        Self {
            base_url,
            resolve_urls,
        }
    }
}
