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
    /// Constructs an empty PageMetadata with all fields unset.
    ///
    /// # Examples
    ///
    /// ```
    /// let meta = PageMetadata::new();
    /// assert_eq!(meta, PageMetadata::default());
    /// assert!(meta.title.is_none() && meta.description.is_none());
    /// ```
    pub fn new() -> Self {
        Self::default()
    }

    /// Estimate the total byte length of all populated metadata fields.
    ///
    /// Counts the UTF-8 byte length of each `Some(String)` field and returns their saturating sum.
    ///
    /// # Examples
    ///
    /// ```
    /// let meta = PageMetadata {
    ///     title: Some("a".into()),
    ///     description: Some("bc".into()),
    ///     ..Default::default()
    /// };
    /// assert_eq!(meta.bytes_estimate(), 3);
    /// ```
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
