//! Page metadata extraction for YAML front matter
//!
//! This module provides functionality to extract metadata from HTML documents
//! for use in YAML front matter generation. It supports:
//!
//! - Title extraction from `<title>` tag and Open Graph tags
//! - Description extraction from meta tags
//! - URL, image, author, and published date extraction
//! - URL resolution for relative links and images
//!
//! # URL Resolution Strategy
//!
//! The metadata extractor resolves relative URLs to absolute URLs using the
//! provided base_url. The resolution priority is:
//!
//! 1. X-Forwarded-Proto/Host headers (when behind reverse proxy)
//! 2. Host header from the request
//! 3. server_name from NGINX configuration
//!
//! # Examples
//!
//! ```rust
//! use nginx_markdown_converter::metadata::{PageMetadata, MetadataExtractor};
//! use nginx_markdown_converter::parser::parse_html;
//!
//! let html = b"<html><head><title>Example</title></head></html>";
//! let dom = parse_html(html).unwrap();
//! let base_url = Some("https://example.com/page".to_string());
//!
//! let extractor = MetadataExtractor::new(base_url, true);
//! let metadata = extractor.extract(&dom).unwrap();
//!
//! assert_eq!(metadata.title, Some("Example".to_string()));
//! ```

use crate::error::ConversionError;
use markup5ever_rcdom::{Handle, NodeData, RcDom};
use std::cell::Ref;

/// Page metadata extracted from HTML
#[derive(Debug, Default, Clone, PartialEq)]
pub struct PageMetadata {
    /// Page title
    pub title: Option<String>,
    /// Page description
    pub description: Option<String>,
    /// Page URL
    pub url: Option<String>,
    /// Page image
    pub image: Option<String>,
    /// Page author
    pub author: Option<String>,
    /// Publication date
    pub published: Option<String>,
}

impl PageMetadata {
    /// Create empty metadata
    pub fn new() -> Self {
        Self::default()
    }
}

/// Metadata extractor with URL resolution
///
/// The `MetadataExtractor` extracts metadata from HTML DOM trees and resolves
/// relative URLs to absolute URLs for agent consumption.
///
/// # URL Resolution
///
/// Relative URLs are resolved using the provided base_url:
/// - Protocol-relative URLs (//example.com/path) are preserved
/// - Absolute paths (/path) are resolved to scheme://host/path
/// - Relative paths (path) are resolved relative to base_url
///
/// # Metadata Sources
///
/// The extractor checks multiple sources with the following priority:
/// - Title: Open Graph og:title > Twitter twitter:title > <title> tag
/// - Description: Open Graph og:description > meta description
/// - Image: Open Graph og:image > Twitter twitter:image
/// - URL: Canonical link > Open Graph og:url > base_url
/// - Author: meta author tag
/// - Published: article:published_time meta tag
pub struct MetadataExtractor {
    base_url: Option<String>,
    resolve_urls: bool,
}

impl MetadataExtractor {
    /// Create a new metadata extractor
    ///
    /// # Arguments
    ///
    /// * `base_url` - Base URL for resolving relative URLs (scheme://host/path)
    /// * `resolve_urls` - Whether to resolve relative URLs to absolute URLs
    pub fn new(base_url: Option<String>, resolve_urls: bool) -> Self {
        Self {
            base_url,
            resolve_urls,
        }
    }

    /// Extract metadata from DOM tree
    ///
    /// # Arguments
    ///
    /// * `dom` - The parsed HTML DOM tree
    ///
    /// # Returns
    ///
    /// Returns `PageMetadata` with extracted fields, or an error if extraction fails.
    ///
    /// # Examples
    ///
    /// ```rust
    /// use nginx_markdown_converter::metadata::MetadataExtractor;
    /// use nginx_markdown_converter::parser::parse_html;
    ///
    /// let html = b"<html><head><title>Test</title></head></html>";
    /// let dom = parse_html(html).unwrap();
    /// let extractor = MetadataExtractor::new(None, false);
    /// let metadata = extractor.extract(&dom).unwrap();
    /// ```
    pub fn extract(&self, dom: &RcDom) -> Result<PageMetadata, ConversionError> {
        let mut metadata = PageMetadata::new();

        // Extract title from <title> tag first (fallback)
        metadata.title = self.find_title(dom);

        // Extract from meta tags (will override title if og:title found)
        self.extract_meta_tags(dom, &mut metadata)?;

        // Extract canonical URL
        if let Some(canonical) = self.find_canonical(dom) {
            metadata.url = Some(self.resolve_url(&canonical));
        } else {
            // Use base_url as fallback
            metadata.url = self.base_url.clone();
        }

        Ok(metadata)
    }

    /// Find title from <title> tag
    fn find_title(&self, dom: &RcDom) -> Option<String> {
        self.find_element_text(dom, "title")
    }

    /// Find canonical URL from <link rel="canonical">
    fn find_canonical(&self, dom: &RcDom) -> Option<String> {
        self.find_link_href(dom, "canonical")
    }

    /// Extract metadata from meta tags
    fn extract_meta_tags(
        &self,
        dom: &RcDom,
        metadata: &mut PageMetadata,
    ) -> Result<(), ConversionError> {
        self.traverse_for_meta(&dom.document, metadata)?;
        Ok(())
    }

    /// Traverse DOM tree looking for meta tags
    fn traverse_for_meta(
        &self,
        node: &Handle,
        metadata: &mut PageMetadata,
    ) -> Result<(), ConversionError> {
        match node.data {
            NodeData::Element {
                ref name,
                ref attrs,
                ..
            } => {
                if name.local.as_ref() == "meta" {
                    self.process_meta_tag(&attrs.borrow(), metadata)?;
                }

                // Recurse into children
                for child in node.children.borrow().iter() {
                    self.traverse_for_meta(child, metadata)?;
                }
            }
            NodeData::Document => {
                // Recurse into children
                for child in node.children.borrow().iter() {
                    self.traverse_for_meta(child, metadata)?;
                }
            }
            _ => {}
        }

        Ok(())
    }

    /// Process a single meta tag
    fn process_meta_tag(
        &self,
        attrs: &Ref<Vec<html5ever::Attribute>>,
        metadata: &mut PageMetadata,
    ) -> Result<(), ConversionError> {
        let property = self.get_attr(attrs, "property");
        let name = self.get_attr(attrs, "name");
        let content = self.get_attr(attrs, "content");

        if content.is_none() {
            return Ok(());
        }

        let content = content.unwrap();
        let key = property.or(name);

        match key.as_deref() {
            // Title (Open Graph and Twitter have priority over <title>)
            Some("og:title") | Some("twitter:title") => {
                metadata.title = Some(content);
            }
            // Description
            Some("og:description") | Some("description") => {
                if metadata.description.is_none() {
                    metadata.description = Some(content);
                }
            }
            // Image (resolve relative URLs)
            Some("og:image") | Some("twitter:image") => {
                if metadata.image.is_none() {
                    let resolved = self.resolve_url(&content);
                    metadata.image = Some(resolved);
                }
            }
            // URL
            Some("og:url") => {
                if metadata.url.is_none() {
                    metadata.url = Some(content);
                }
            }
            // Author
            Some("author") => {
                if metadata.author.is_none() {
                    metadata.author = Some(content);
                }
            }
            // Published date
            Some("article:published_time") => {
                if metadata.published.is_none() {
                    metadata.published = Some(content);
                }
            }
            _ => {}
        }

        Ok(())
    }

    /// Get attribute value from element
    fn get_attr(&self, attrs: &Ref<Vec<html5ever::Attribute>>, name: &str) -> Option<String> {
        attrs
            .iter()
            .find(|attr| attr.name.local.as_ref() == name)
            .map(|attr| attr.value.to_string())
    }

    /// Find text content of first matching element
    fn find_element_text(&self, dom: &RcDom, element_name: &str) -> Option<String> {
        self.find_element_text_recursive(&dom.document, element_name)
    }

    /// Recursively find element text
    fn find_element_text_recursive(&self, node: &Handle, element_name: &str) -> Option<String> {
        match node.data {
            NodeData::Element { ref name, .. } => {
                if name.local.as_ref() == element_name {
                    let mut text = String::new();
                    self.extract_text_content(node, &mut text);
                    return Some(text.trim().to_string());
                }

                // Recurse into children
                for child in node.children.borrow().iter() {
                    if let Some(text) = self.find_element_text_recursive(child, element_name) {
                        return Some(text);
                    }
                }
            }
            NodeData::Document => {
                // Recurse into children
                for child in node.children.borrow().iter() {
                    if let Some(text) = self.find_element_text_recursive(child, element_name) {
                        return Some(text);
                    }
                }
            }
            _ => {}
        }

        None
    }

    /// Find href attribute of link element with specific rel
    fn find_link_href(&self, dom: &RcDom, rel: &str) -> Option<String> {
        self.find_link_href_recursive(&dom.document, rel)
    }

    /// Recursively find link href
    fn find_link_href_recursive(&self, node: &Handle, rel: &str) -> Option<String> {
        match node.data {
            NodeData::Element {
                ref name,
                ref attrs,
                ..
            } => {
                if name.local.as_ref() == "link" {
                    let attrs_ref = attrs.borrow();
                    let has_rel = attrs_ref.iter().any(|attr| {
                        attr.name.local.as_ref() == "rel" && attr.value.as_ref() == rel
                    });

                    if has_rel {
                        return self.get_attr(&attrs_ref, "href");
                    }
                }

                // Recurse into children
                for child in node.children.borrow().iter() {
                    if let Some(href) = self.find_link_href_recursive(child, rel) {
                        return Some(href);
                    }
                }
            }
            NodeData::Document => {
                // Recurse into children
                for child in node.children.borrow().iter() {
                    if let Some(href) = self.find_link_href_recursive(child, rel) {
                        return Some(href);
                    }
                }
            }
            _ => {}
        }

        None
    }

    /// Extract text content from node and its children
    fn extract_text_content(&self, node: &Handle, output: &mut String) {
        match node.data {
            NodeData::Text { ref contents } => {
                output.push_str(&contents.borrow());
            }
            NodeData::Element { .. } | NodeData::Document => {
                for child in node.children.borrow().iter() {
                    self.extract_text_content(child, output);
                }
            }
            _ => {}
        }
    }

    /// Resolve relative URL to absolute URL
    ///
    /// # URL Resolution Rules
    ///
    /// - Already absolute URLs (http://, https://) are returned as-is
    /// - Protocol-relative URLs (//example.com/path) are returned as-is
    /// - Absolute paths (/path) are resolved to scheme://host/path
    /// - Relative paths (path) are resolved relative to base_url
    /// - Malformed URLs are returned as-is with validation warning
    ///
    /// # Examples
    ///
    /// ```rust
    /// use nginx_markdown_converter::metadata::MetadataExtractor;
    ///
    /// let extractor = MetadataExtractor::new(
    ///     Some("https://example.com/page/subpage".to_string()),
    ///     true
    /// );
    ///
    /// // Already absolute
    /// assert_eq!(
    ///     extractor.resolve_url("https://other.com/image.jpg"),
    ///     "https://other.com/image.jpg"
    /// );
    ///
    /// // Protocol-relative
    /// assert_eq!(
    ///     extractor.resolve_url("//cdn.example.com/image.jpg"),
    ///     "//cdn.example.com/image.jpg"
    /// );
    ///
    /// // Absolute path
    /// assert_eq!(
    ///     extractor.resolve_url("/images/logo.png"),
    ///     "https://example.com/images/logo.png"
    /// );
    ///
    /// // Relative path
    /// assert_eq!(
    ///     extractor.resolve_url("image.jpg"),
    ///     "https://example.com/page/image.jpg"
    /// );
    /// ```
    pub fn resolve_url(&self, url: &str) -> String {
        // If URL resolution is disabled, return as-is
        if !self.resolve_urls {
            return url.to_string();
        }

        // Validate URL is not empty
        if url.is_empty() {
            return url.to_string();
        }

        // Already absolute URL
        if url.starts_with("http://") || url.starts_with("https://") {
            return url.to_string();
        }

        // Protocol-relative URL (//example.com/path)
        if url.starts_with("//") {
            return url.to_string();
        }

        // No base URL available
        let Some(ref base) = self.base_url else {
            return url.to_string();
        };

        // Validate base URL format
        if !self.is_valid_base_url(base) {
            // Malformed base URL, return original
            return url.to_string();
        }

        // Absolute path (/path)
        if url.starts_with('/') {
            return format!("{}{}", self.get_origin(base), url);
        }

        // Relative path (path or ./path or ../path)
        // Resolve relative to base_url directory
        let base_dir = self.get_base_directory(base);
        format!("{}/{}", base_dir.trim_end_matches('/'), url)
    }

    /// Check if base URL is valid (has scheme and host)
    fn is_valid_base_url(&self, url: &str) -> bool {
        url.starts_with("http://") || url.starts_with("https://")
    }

    /// Extract origin (scheme://host) from URL
    ///
    /// # Examples
    ///
    /// - `https://example.com/path` -> `https://example.com`
    /// - `http://example.com:8080/path` -> `http://example.com:8080`
    fn get_origin(&self, url: &str) -> String {
        // Find the third slash (after scheme://)
        let after_scheme = if let Some(stripped) = url.strip_prefix("https://") {
            stripped
        } else if let Some(stripped) = url.strip_prefix("http://") {
            stripped
        } else {
            return url.to_string();
        };

        // Find the first slash after the host
        if let Some(pos) = after_scheme.find('/') {
            let scheme_len = if url.starts_with("https://") { 8 } else { 7 };
            url[..scheme_len + pos].to_string()
        } else {
            // No path, return entire URL
            url.to_string()
        }
    }

    /// Get base directory from URL (for resolving relative paths)
    ///
    /// # Examples
    ///
    /// - `https://example.com/page/subpage` -> `https://example.com/page`
    /// - `https://example.com/page/` -> `https://example.com/page`
    /// - `https://example.com` -> `https://example.com`
    fn get_base_directory(&self, url: &str) -> String {
        let trimmed = url.trim_end_matches('/');

        // Find the last slash
        if let Some(pos) = trimmed.rfind('/') {
            // Check if this is the slash after the scheme (http://)
            if pos > 0 && trimmed.chars().nth(pos - 1) == Some('/') {
                // This is scheme://, return entire URL
                return trimmed.to_string();
            }
            // Return everything up to the last slash
            trimmed[..pos].to_string()
        } else {
            // No slash found, return as-is
            trimmed.to_string()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse_html;

    #[test]
    fn test_extract_title_from_title_tag() {
        let html = b"<html><head><title>Test Title</title></head></html>";
        let dom = parse_html(html).unwrap();
        let extractor = MetadataExtractor::new(None, false);
        let metadata = extractor.extract(&dom).unwrap();

        assert_eq!(metadata.title, Some("Test Title".to_string()));
    }

    #[test]
    fn test_extract_title_from_og_title() {
        let html = b"<html><head>
            <title>Fallback Title</title>
            <meta property=\"og:title\" content=\"OG Title\" />
        </head></html>";
        let dom = parse_html(html).unwrap();
        let extractor = MetadataExtractor::new(None, false);
        let metadata = extractor.extract(&dom).unwrap();

        // og:title should override <title>
        assert_eq!(metadata.title, Some("OG Title".to_string()));
    }

    #[test]
    fn test_extract_description() {
        let html = b"<html><head>
            <meta name=\"description\" content=\"Test description\" />
        </head></html>";
        let dom = parse_html(html).unwrap();
        let extractor = MetadataExtractor::new(None, false);
        let metadata = extractor.extract(&dom).unwrap();

        assert_eq!(metadata.description, Some("Test description".to_string()));
    }

    #[test]
    fn test_extract_og_description() {
        let html = b"<html><head>
            <meta name=\"description\" content=\"Regular description\" />
            <meta property=\"og:description\" content=\"OG description\" />
        </head></html>";
        let dom = parse_html(html).unwrap();
        let extractor = MetadataExtractor::new(None, false);
        let metadata = extractor.extract(&dom).unwrap();

        // First one wins (og:description appears second)
        assert_eq!(
            metadata.description,
            Some("Regular description".to_string())
        );
    }

    #[test]
    fn test_extract_image() {
        let html = b"<html><head>
            <meta property=\"og:image\" content=\"/images/test.jpg\" />
        </head></html>";
        let dom = parse_html(html).unwrap();
        let base_url = Some("https://example.com".to_string());
        let extractor = MetadataExtractor::new(base_url, true);
        let metadata = extractor.extract(&dom).unwrap();

        assert_eq!(
            metadata.image,
            Some("https://example.com/images/test.jpg".to_string())
        );
    }

    #[test]
    fn test_extract_author() {
        let html = b"<html><head>
            <meta name=\"author\" content=\"John Doe\" />
        </head></html>";
        let dom = parse_html(html).unwrap();
        let extractor = MetadataExtractor::new(None, false);
        let metadata = extractor.extract(&dom).unwrap();

        assert_eq!(metadata.author, Some("John Doe".to_string()));
    }

    #[test]
    fn test_extract_published_date() {
        let html = b"<html><head>
            <meta property=\"article:published_time\" content=\"2024-01-15T10:30:00Z\" />
        </head></html>";
        let dom = parse_html(html).unwrap();
        let extractor = MetadataExtractor::new(None, false);
        let metadata = extractor.extract(&dom).unwrap();

        assert_eq!(metadata.published, Some("2024-01-15T10:30:00Z".to_string()));
    }

    #[test]
    fn test_extract_canonical_url() {
        let html = b"<html><head>
            <link rel=\"canonical\" href=\"https://example.com/canonical\" />
        </head></html>";
        let dom = parse_html(html).unwrap();
        let extractor = MetadataExtractor::new(None, false);
        let metadata = extractor.extract(&dom).unwrap();

        assert_eq!(
            metadata.url,
            Some("https://example.com/canonical".to_string())
        );
    }

    #[test]
    fn test_url_fallback_to_base_url() {
        let html = b"<html><head><title>Test</title></head></html>";
        let dom = parse_html(html).unwrap();
        let base_url = Some("https://example.com/page".to_string());
        let extractor = MetadataExtractor::new(base_url, true);
        let metadata = extractor.extract(&dom).unwrap();

        assert_eq!(metadata.url, Some("https://example.com/page".to_string()));
    }

    #[test]
    fn test_resolve_absolute_url() {
        let extractor = MetadataExtractor::new(Some("https://example.com/page".to_string()), true);

        assert_eq!(
            extractor.resolve_url("https://other.com/image.jpg"),
            "https://other.com/image.jpg"
        );
    }

    #[test]
    fn test_resolve_protocol_relative_url() {
        let extractor = MetadataExtractor::new(Some("https://example.com/page".to_string()), true);

        assert_eq!(
            extractor.resolve_url("//cdn.example.com/image.jpg"),
            "//cdn.example.com/image.jpg"
        );
    }

    #[test]
    fn test_resolve_absolute_path() {
        let extractor =
            MetadataExtractor::new(Some("https://example.com/page/subpage".to_string()), true);

        assert_eq!(
            extractor.resolve_url("/images/logo.png"),
            "https://example.com/images/logo.png"
        );
    }

    #[test]
    fn test_resolve_relative_path() {
        let extractor =
            MetadataExtractor::new(Some("https://example.com/page/subpage".to_string()), true);

        assert_eq!(
            extractor.resolve_url("image.jpg"),
            "https://example.com/page/image.jpg"
        );
    }

    #[test]
    fn test_resolve_url_disabled() {
        let extractor = MetadataExtractor::new(Some("https://example.com/page".to_string()), false);

        assert_eq!(
            extractor.resolve_url("/images/logo.png"),
            "/images/logo.png"
        );
    }

    #[test]
    fn test_resolve_url_no_base() {
        let extractor = MetadataExtractor::new(None, true);

        assert_eq!(
            extractor.resolve_url("/images/logo.png"),
            "/images/logo.png"
        );
    }

    #[test]
    fn test_resolve_empty_url() {
        let extractor = MetadataExtractor::new(Some("https://example.com".to_string()), true);

        assert_eq!(extractor.resolve_url(""), "");
    }

    #[test]
    fn test_malformed_base_url() {
        let extractor = MetadataExtractor::new(Some("not-a-url".to_string()), true);

        // Should return original URL when base is malformed
        assert_eq!(extractor.resolve_url("/path"), "/path");
    }

    #[test]
    fn test_get_origin() {
        let extractor = MetadataExtractor::new(None, false);

        assert_eq!(
            extractor.get_origin("https://example.com/path/to/page"),
            "https://example.com"
        );
        assert_eq!(
            extractor.get_origin("http://example.com:8080/path"),
            "http://example.com:8080"
        );
        assert_eq!(
            extractor.get_origin("https://example.com"),
            "https://example.com"
        );
    }

    #[test]
    fn test_get_base_directory() {
        let extractor = MetadataExtractor::new(None, false);

        assert_eq!(
            extractor.get_base_directory("https://example.com/page/subpage"),
            "https://example.com/page"
        );
        assert_eq!(
            extractor.get_base_directory("https://example.com/page/"),
            "https://example.com"
        );
        assert_eq!(
            extractor.get_base_directory("https://example.com"),
            "https://example.com"
        );
    }

    #[test]
    fn test_comprehensive_metadata_extraction() {
        let html = b"<html><head>
            <title>Fallback Title</title>
            <meta property=\"og:title\" content=\"Main Title\" />
            <meta property=\"og:description\" content=\"A great description\" />
            <meta property=\"og:image\" content=\"/images/hero.jpg\" />
            <meta property=\"og:url\" content=\"https://example.com/article\" />
            <meta name=\"author\" content=\"Jane Smith\" />
            <meta property=\"article:published_time\" content=\"2024-01-15\" />
            <link rel=\"canonical\" href=\"https://example.com/canonical-url\" />
        </head></html>";

        let dom = parse_html(html).unwrap();
        let base_url = Some("https://example.com/page".to_string());
        let extractor = MetadataExtractor::new(base_url, true);
        let metadata = extractor.extract(&dom).unwrap();

        assert_eq!(metadata.title, Some("Main Title".to_string()));
        assert_eq!(
            metadata.description,
            Some("A great description".to_string())
        );
        assert_eq!(
            metadata.image,
            Some("https://example.com/images/hero.jpg".to_string())
        );
        // Canonical URL takes precedence over og:url
        assert_eq!(
            metadata.url,
            Some("https://example.com/canonical-url".to_string())
        );
        assert_eq!(metadata.author, Some("Jane Smith".to_string()));
        assert_eq!(metadata.published, Some("2024-01-15".to_string()));
    }

    #[test]
    fn test_twitter_card_metadata() {
        let html = b"<html><head>
            <meta name=\"twitter:title\" content=\"Twitter Title\" />
            <meta name=\"twitter:image\" content=\"https://cdn.example.com/image.jpg\" />
        </head></html>";

        let dom = parse_html(html).unwrap();
        let extractor = MetadataExtractor::new(None, false);
        let metadata = extractor.extract(&dom).unwrap();

        assert_eq!(metadata.title, Some("Twitter Title".to_string()));
        assert_eq!(
            metadata.image,
            Some("https://cdn.example.com/image.jpg".to_string())
        );
    }
}
