//! Fast path qualification for structurally simple documents.
//!
//! This module provides a lightweight pre-pass that determines whether a parsed
//! DOM tree is structurally simple enough to benefit from fast-path optimizations
//! during conversion. A document qualifies when every element node uses a tag in
//! the [`FAST_PATH_ELEMENTS`] allow-list (or is prunable via
//! [`super::pruning::should_prune`]), and the maximum nesting depth stays below
//! [`FAST_PATH_MAX_DEPTH`].
//!
//! Documents containing tables, forms, embedded content, or media elements do
//! not qualify and fall back to the normal conversion path.

use markup5ever_rcdom::{Handle, NodeData, RcDom};

use super::pruning::{PruneDecision, should_prune};

/// Maximum nesting depth for fast-path qualification.
///
/// Documents whose DOM tree exceeds this depth are routed through the normal
/// conversion path. The threshold is intentionally generous — most
/// well-structured pages nest far below 15 levels.
pub(crate) const FAST_PATH_MAX_DEPTH: usize = 15;

/// Element tags supported by the fast path.
///
/// Only documents composed entirely of these tags (plus prunable elements like
/// `script`/`style`/`noscript`) qualify for the fast path. Tags not in this
/// list — such as `table`, `form`, `video`, `iframe` — disqualify the document.
const FAST_PATH_ELEMENTS: &[&str] = &[
    "html",
    "head",
    "body",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "br",
    "hr",
    "ul",
    "ol",
    "li",
    "a",
    "img",
    "strong",
    "b",
    "em",
    "i",
    "code",
    "pre",
    "blockquote",
    "div",
    "span",
    "section",
    "article",
    "main",
    "header",
];

/// Result of fast-path qualification scan.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FastPathResult {
    /// Document qualifies for the fast path.
    Qualifies,
    /// Document does not qualify — use normal path.
    Normal,
}

/// Check whether a DOM tree qualifies for the fast path.
///
/// Walks the entire DOM tree and verifies:
/// 1. All element nodes use tags in [`FAST_PATH_ELEMENTS`] or are prunable
///    (via [`super::pruning::should_prune`] returning non-`Traverse`).
/// 2. Maximum nesting depth is below [`FAST_PATH_MAX_DEPTH`].
/// 3. No table, form, embedded content, or media elements are present.
///
/// # Arguments
///
/// * `dom` - The parsed DOM tree.
///
/// # Returns
///
/// [`FastPathResult::Qualifies`] if the document can use the fast path,
/// [`FastPathResult::Normal`] otherwise.
pub(crate) fn qualifies(dom: &RcDom) -> FastPathResult {
    if check_node(&dom.document, 0) {
        FastPathResult::Qualifies
    } else {
        FastPathResult::Normal
    }
}

/// Recursively check whether a single node and all its descendants qualify.
///
/// Returns `true` when the subtree rooted at `node` is fast-path compatible,
/// `false` otherwise.
fn check_node(node: &Handle, depth: usize) -> bool {
    if depth > FAST_PATH_MAX_DEPTH {
        return false;
    }

    match &node.data {
        // Non-element nodes are always fine.
        NodeData::Document
        | NodeData::Text { .. }
        | NodeData::Comment { .. }
        | NodeData::Doctype { .. }
        | NodeData::ProcessingInstruction { .. } => {}

        NodeData::Element { name, .. } => {
            let tag = name.local.as_ref();

            // Prunable elements are acceptable — their subtrees are skipped
            // during traversal, so we don't need to inspect children.
            if should_prune(tag) != PruneDecision::Traverse {
                return true;
            }

            // If the tag is not in the fast-path allow-list, disqualify.
            if !FAST_PATH_ELEMENTS.contains(&tag) {
                return false;
            }
        }
    }

    // Recursively check all children.
    for child in node.children.borrow().iter() {
        if !check_node(child, depth + 1) {
            return false;
        }
    }

    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parser::parse_html;

    /// Helper: parse an HTML string into an `RcDom`.
    fn parse(html: &str) -> RcDom {
        parse_html(html.as_bytes()).expect("test HTML should parse")
    }

    #[test]
    fn simple_paragraph_qualifies() {
        let dom = parse("<html><body><p>Hello world</p></body></html>");
        assert_eq!(qualifies(&dom), FastPathResult::Qualifies);
    }

    #[test]
    fn table_does_not_qualify() {
        let dom = parse("<html><body><table><tr><td>cell</td></tr></table></body></html>");
        assert_eq!(qualifies(&dom), FastPathResult::Normal);
    }

    #[test]
    fn form_does_not_qualify() {
        let dom = parse("<html><body><form><input></form></body></html>");
        assert_eq!(qualifies(&dom), FastPathResult::Normal);
    }

    #[test]
    fn depth_at_max_qualifies() {
        // html5ever produces: Document(0) > html(1) > body(2) > div(3) > …
        // A <p> inside N divs sits at depth 3+N, and its text child at 4+N.
        // We need the deepest node (text) at exactly FAST_PATH_MAX_DEPTH (15),
        // so N = FAST_PATH_MAX_DEPTH - 4 = 11.
        let explicit_divs = FAST_PATH_MAX_DEPTH - 4;
        let open: String = "<div>".repeat(explicit_divs);
        let close: String = "</div>".repeat(explicit_divs);
        let html = format!("<html><body>{open}<p>deep</p>{close}</body></html>");
        let dom = parse(&html);
        assert_eq!(qualifies(&dom), FastPathResult::Qualifies);
    }

    #[test]
    fn depth_exceeding_max_does_not_qualify() {
        // One more div than the at-max test pushes the text node to depth 16.
        let explicit_divs = FAST_PATH_MAX_DEPTH - 3;
        let open: String = "<div>".repeat(explicit_divs);
        let close: String = "</div>".repeat(explicit_divs);
        let html = format!("<html><body>{open}<p>too deep</p>{close}</body></html>");
        let dom = parse(&html);
        assert_eq!(qualifies(&dom), FastPathResult::Normal);
    }

    #[test]
    fn prunable_elements_qualify() {
        // script and style are prunable — their presence should not
        // disqualify the document.
        let dom = parse(
            "<html><body>\
             <script>alert('x')</script>\
             <style>body{color:red}</style>\
             <noscript>no js</noscript>\
             <p>Content</p>\
             </body></html>",
        );
        assert_eq!(qualifies(&dom), FastPathResult::Qualifies);
    }

    #[test]
    fn empty_document_qualifies() {
        let dom = parse("<html><body></body></html>");
        assert_eq!(qualifies(&dom), FastPathResult::Qualifies);
    }

    #[test]
    fn text_only_document_qualifies() {
        // html5ever wraps bare text in <html><head></head><body>…</body></html>
        let dom = parse("Just some text");
        assert_eq!(qualifies(&dom), FastPathResult::Qualifies);
    }
}
