//! DOM traversal for HTML metadata extraction.
//!
//! This module implements the metadata extraction logic that pulls structured
//! page metadata from an HTML DOM tree. The extraction is used to populate
//! YAML front matter when `include_front_matter` and `extract_metadata` are
//! both enabled.
//!
//! # Extraction Order
//!
//! Extraction follows a deterministic order so that repeated conversions over
//! identical input yield stable front-matter output:
//!
//! 1. `<title>` element text → `title` field
//! 2. `<meta>` tags (name=description, og:*, twitter:*) → corresponding fields
//! 3. `<link rel="canonical">` → `url` field (resolved to absolute if base URL set)
//!
//! # Supported Meta Tags
//!
//! | Meta Name/Property | Target Field | Semantics |
//! |-------------------|-------------|-----------|
//! | `description` | `description` | First-wins (generic fallback) |
//! | `og:title` | `title` | Overrides `<title>` |
//! | `og:description` | `description` | Overrides generic `description` |
//! | `og:image` | `image` | First-wins |
//! | `og:url` | `url` | First-wins |
//! | `twitter:title` | `title` | Overrides `<title>` |
//! | `twitter:description` | `description` | Overrides generic `description` |
//! | `twitter:image` | `image` | First-wins |
//! | `author` | `author` | First-wins |
//! | `article:published_time` | `published` | First-wins |
//!
//! **Override rule:** `og:*` and `twitter:*` tags for `title` and `description`
//! always override the generic field, even if already set by `<title>` or
//! `<meta name="description">`.  All other fields use first-wins semantics.

use std::cell::Ref;

use markup5ever_rcdom::{Handle, NodeData, RcDom};

use crate::converter::ConversionContext;
use crate::error::ConversionError;

use super::{MetadataExtractor, PageMetadata};

const MAX_METADATA_DEPTH: usize = 1000;

impl MetadataExtractor {
    /// Extract page metadata from a parsed DOM tree.
    ///
    /// Extraction order is deterministic so repeated conversions over identical
    /// input yield stable front-matter output.
    pub fn extract(&self, dom: &RcDom) -> Result<PageMetadata, ConversionError> {
        let mut ctx = ConversionContext::new(std::time::Duration::ZERO);
        self.extract_with_context(dom, &mut ctx)
    }

    /// Extract page metadata while sharing the conversion timeout and node
    /// accounting used by the main DOM traversal.
    pub(crate) fn extract_with_context(
        &self,
        dom: &RcDom,
        ctx: &mut ConversionContext,
    ) -> Result<PageMetadata, ConversionError> {
        ctx.check_timeout()?;
        let mut metadata = PageMetadata::new();

        metadata.title = self.find_title(dom, ctx)?;
        let canonical = self.extract_meta_tags_and_canonical(dom, &mut metadata, ctx)?;

        if let Some(canonical) = canonical {
            metadata.url = Some(self.resolve_url(&canonical));
        } else {
            metadata.url = self.base_url.clone();
        }

        ctx.check_timeout()?;
        Ok(metadata)
    }

    /// Resolve document title from the first `<title>` element.
    fn find_title(
        &self,
        dom: &RcDom,
        ctx: &mut ConversionContext,
    ) -> Result<Option<String>, ConversionError> {
        self.find_element_text(dom, "title", ctx)
    }

    /// Traverse the DOM once to collect meta tags and the first canonical URL.
    fn extract_meta_tags_and_canonical(
        &self,
        dom: &RcDom,
        metadata: &mut PageMetadata,
        ctx: &mut ConversionContext,
    ) -> Result<Option<String>, ConversionError> {
        let mut canonical = None;
        let mut stack = vec![(dom.document.clone(), 0usize)];

        while let Some((node, depth)) = stack.pop() {
            Self::check_depth(depth)?;
            ctx.increment_and_check()?;

            match node.data {
                NodeData::Element {
                    ref name,
                    ref attrs,
                    ..
                } => {
                    if name.local.as_ref() == "meta" {
                        self.process_meta_tag(&attrs.borrow(), metadata)?;
                    } else if name.local.as_ref() == "link" && canonical.is_none() {
                        let attrs_ref = attrs.borrow();
                        let has_canonical_rel = attrs_ref.iter().any(|attr| {
                            attr.name.local.as_ref() == "rel"
                                && attr
                                    .value
                                    .split_ascii_whitespace()
                                    .any(|token| token.eq_ignore_ascii_case("canonical"))
                        });
                        if has_canonical_rel {
                            canonical = self.get_attr(&attrs_ref, "href");
                        }
                    }

                    for child in node.children.borrow().iter().rev() {
                        stack.push((child.clone(), depth + 1));
                    }
                }
                NodeData::Document => {
                    for child in node.children.borrow().iter().rev() {
                        stack.push((child.clone(), depth + 1));
                    }
                }
                _ => {}
            }
        }

        Ok(canonical)
    }

    /// Merge one meta tag into the accumulated metadata structure.
    fn process_meta_tag(
        &self,
        attrs: &Ref<Vec<html5ever::Attribute>>,
        metadata: &mut PageMetadata,
    ) -> Result<(), ConversionError> {
        let property = self.get_attr(attrs, "property");
        let name = self.get_attr(attrs, "name");
        let Some(content) = self.get_attr(attrs, "content") else {
            return Ok(());
        };

        /*
         * Open Graph / Twitter sharing metadata should override generic
         * document metadata when both are present, because it is typically
         * more curated for external consumption. Fallback fields still use
         * first-wins semantics to preserve the earliest generic source.
         */
        match property.or(name).as_deref() {
            Some("og:title") | Some("twitter:title") => {
                metadata.title = Some(content);
            }
            Some("og:description") | Some("twitter:description") => {
                metadata.description = Some(content);
            }
            Some("description") if metadata.description.is_none() => {
                metadata.description = Some(content);
            }
            Some("og:image") | Some("twitter:image") if metadata.image.is_none() => {
                metadata.image = Some(self.resolve_url(&content));
            }
            Some("og:url") if metadata.url.is_none() => {
                metadata.url = Some(content);
            }
            Some("author") if metadata.author.is_none() => {
                metadata.author = Some(content);
            }
            Some("article:published_time") if metadata.published.is_none() => {
                metadata.published = Some(content);
            }
            _ => {}
        }

        Ok(())
    }

    /// Return a tag attribute value by name when present.
    fn get_attr(&self, attrs: &Ref<Vec<html5ever::Attribute>>, name: &str) -> Option<String> {
        attrs
            .iter()
            .find(|attr| attr.name.local.as_ref() == name)
            .map(|attr| attr.value.to_string())
    }

    /// Locate the first matching element and return its text content.
    fn find_element_text(
        &self,
        dom: &RcDom,
        element_name: &str,
        ctx: &mut ConversionContext,
    ) -> Result<Option<String>, ConversionError> {
        let mut stack = vec![(dom.document.clone(), 0usize)];

        while let Some((node, depth)) = stack.pop() {
            Self::check_depth(depth)?;
            ctx.increment_and_check()?;

            match node.data {
                NodeData::Element { ref name, .. } => {
                    if name.local.as_ref() == element_name {
                        return Self::extract_text_content(&node, depth, ctx)
                            .map(|text| Some(text.trim().to_string()));
                    }

                    for child in node.children.borrow().iter().rev() {
                        stack.push((child.clone(), depth + 1));
                    }
                }
                NodeData::Document => {
                    for child in node.children.borrow().iter().rev() {
                        stack.push((child.clone(), depth + 1));
                    }
                }
                _ => {}
            }
        }

        Ok(None)
    }

    /// Append all descendant text nodes into `output` in DOM order.
    fn extract_text_content(
        node: &Handle,
        initial_depth: usize,
        ctx: &mut ConversionContext,
    ) -> Result<String, ConversionError> {
        let mut output = String::new();
        let mut stack = vec![(node.clone(), initial_depth)];

        while let Some((current, depth)) = stack.pop() {
            Self::check_depth(depth)?;
            ctx.increment_and_check()?;

            match current.data {
                NodeData::Text { ref contents } => {
                    output.push_str(&contents.borrow());
                }
                NodeData::Element { .. } | NodeData::Document => {
                    for child in current.children.borrow().iter().rev() {
                        stack.push((child.clone(), depth + 1));
                    }
                }
                _ => {}
            }
        }

        Ok(output)
    }

    fn check_depth(depth: usize) -> Result<(), ConversionError> {
        if depth > MAX_METADATA_DEPTH {
            return Err(ConversionError::InvalidInput(format!(
                "metadata nesting depth exceeds {MAX_METADATA_DEPTH}"
            )));
        }

        Ok(())
    }
}
