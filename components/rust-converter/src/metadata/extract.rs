use std::cell::Ref;

use markup5ever_rcdom::{Handle, NodeData, RcDom};

use crate::error::ConversionError;

use super::{MetadataExtractor, PageMetadata};

impl MetadataExtractor {
    /// Extract page metadata from a parsed DOM tree.
    ///
    /// Extraction order is deterministic so repeated conversions over identical
    /// input yield stable front-matter output.
    pub fn extract(&self, dom: &RcDom) -> Result<PageMetadata, ConversionError> {
        let mut metadata = PageMetadata::new();

        metadata.title = self.find_title(dom);
        self.extract_meta_tags(dom, &mut metadata)?;

        if let Some(canonical) = self.find_canonical(dom) {
            metadata.url = Some(self.resolve_url(&canonical));
        } else {
            metadata.url = self.base_url.clone();
        }

        Ok(metadata)
    }

    /// Resolve document title from the first `<title>` element.
    fn find_title(&self, dom: &RcDom) -> Option<String> {
        self.find_element_text(dom, "title")
    }

    /// Resolve canonical URL from `<link rel="canonical">`.
    fn find_canonical(&self, dom: &RcDom) -> Option<String> {
        self.find_link_href(dom, "canonical")
    }

    /// Traverse the DOM and collect metadata from `<meta ...>` tags.
    fn extract_meta_tags(
        &self,
        dom: &RcDom,
        metadata: &mut PageMetadata,
    ) -> Result<(), ConversionError> {
        self.traverse_for_meta(&dom.document, metadata)
    }

    /// Depth-first traversal that processes metadata-relevant nodes.
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

                for child in node.children.borrow().iter() {
                    self.traverse_for_meta(child, metadata)?;
                }
            }
            NodeData::Document => {
                for child in node.children.borrow().iter() {
                    self.traverse_for_meta(child, metadata)?;
                }
            }
            _ => {}
        }

        Ok(())
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

        // Priority: og:title / twitter:title override <title> tag because
        // Open Graph metadata is typically more curated for sharing contexts.
        // Other fields (description, image, etc.) use first-wins semantics to
        // prefer the earliest source encountered during DOM traversal.
        match property.or(name).as_deref() {
            Some("og:title") | Some("twitter:title") => {
                metadata.title = Some(content);
            }
            Some("og:description") | Some("description") => {
                if metadata.description.is_none() {
                    metadata.description = Some(content);
                }
            }
            Some("og:image") | Some("twitter:image") => {
                if metadata.image.is_none() {
                    metadata.image = Some(self.resolve_url(&content));
                }
            }
            Some("og:url") => {
                if metadata.url.is_none() {
                    metadata.url = Some(content);
                }
            }
            Some("author") => {
                if metadata.author.is_none() {
                    metadata.author = Some(content);
                }
            }
            Some("article:published_time") => {
                if metadata.published.is_none() {
                    metadata.published = Some(content);
                }
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
    fn find_element_text(&self, dom: &RcDom, element_name: &str) -> Option<String> {
        self.find_element_text_recursive(&dom.document, element_name)
    }

    /// Recursive helper for `find_element_text`.
    fn find_element_text_recursive(&self, node: &Handle, element_name: &str) -> Option<String> {
        match node.data {
            NodeData::Element { ref name, .. } => {
                if name.local.as_ref() == element_name {
                    let mut text = String::new();
                    self.extract_text_content(node, &mut text);
                    return Some(text.trim().to_string());
                }

                for child in node.children.borrow().iter() {
                    if let Some(text) = self.find_element_text_recursive(child, element_name) {
                        return Some(text);
                    }
                }
            }
            NodeData::Document => {
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

    /// Locate a `<link>` tag by `rel` and return its `href` value.
    fn find_link_href(&self, dom: &RcDom, rel: &str) -> Option<String> {
        self.find_link_href_recursive(&dom.document, rel)
    }

    /// Recursive helper for `find_link_href`.
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

                for child in node.children.borrow().iter() {
                    if let Some(href) = self.find_link_href_recursive(child, rel) {
                        return Some(href);
                    }
                }
            }
            NodeData::Document => {
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

    /// Append all descendant text nodes into `output` in DOM order.
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
}
