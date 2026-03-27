//! Noise region early pruning for DOM traversal.
//!
//! This module provides the pruning decision logic used during DOM traversal
//! to skip subtrees that produce no meaningful Markdown output. Elements like
//! `<script>`, `<style>`, and `<noscript>` are always skipped (their children
//! are never visited). Semantic noise regions (`<nav>`, `<footer>`, `<aside>`)
//! are optionally prunable behind the `prune_noise_regions` feature flag.
//!
//! Pruning operates at the traversal layer — the `RcDom` tree is never mutated.
//! The converter's `handle_element_internal` calls [`should_prune`] to decide
//! whether to traverse, skip children, or skip the entire subtree.

/// Elements whose children are always skipped during traversal.
///
/// These elements never produce meaningful Markdown content and are already
/// handled as no-ops in the existing converter. Pruning them here avoids
/// visiting their child nodes entirely.
const SKIP_CHILDREN_ELEMENTS: &[&str] = &["script", "style", "noscript"];

/// Semantic noise region elements that can optionally be pruned.
///
/// When the `prune_noise_regions` feature is enabled, these elements and their
/// entire subtrees are skipped during traversal. By default (feature disabled),
/// they are traversed normally.
const NOISE_REGION_ELEMENTS: &[&str] = &["nav", "footer", "aside"];

/// Whether noise region pruning is active.
///
/// This is `true` when the `prune_noise_regions` feature flag is enabled,
/// and `false` otherwise. In the 0.4.0 release the feature is disabled by
/// default — only `script`/`style`/`noscript` pruning is active.
#[cfg(feature = "prune_noise_regions")]
const PRUNE_NOISE_REGIONS: bool = true;

/// Whether noise region pruning is active (default: disabled).
#[cfg(not(feature = "prune_noise_regions"))]
const PRUNE_NOISE_REGIONS: bool = false;

/// Decision for how to handle a DOM subtree during traversal.
///
/// Returned by [`should_prune`] to tell the traversal layer whether to
/// continue into a node's children, skip only the children, or skip the
/// entire subtree.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum PruneDecision {
    /// Continue traversal into this node's children.
    Traverse,
    /// Skip this node's children but do not skip the node itself.
    SkipChildren,
    /// Skip this node and its entire subtree.
    SkipSubtree,
}

/// Determine the pruning decision for an HTML element by tag name.
///
/// # Arguments
///
/// * `tag_name` - The lowercase HTML tag name to evaluate.
///
/// # Returns
///
/// - [`PruneDecision::SkipChildren`] for `script`, `style`, `noscript`.
/// - [`PruneDecision::SkipSubtree`] for `nav`, `footer`, `aside` when the
///   `prune_noise_regions` feature is enabled.
/// - [`PruneDecision::Traverse`] for all other elements.
pub(crate) fn should_prune(tag_name: &str) -> PruneDecision {
    if SKIP_CHILDREN_ELEMENTS.contains(&tag_name) {
        return PruneDecision::SkipChildren;
    }

    if PRUNE_NOISE_REGIONS && NOISE_REGION_ELEMENTS.contains(&tag_name) {
        return PruneDecision::SkipSubtree;
    }

    PruneDecision::Traverse
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn script_returns_skip_children() {
        assert_eq!(should_prune("script"), PruneDecision::SkipChildren);
    }

    #[test]
    fn style_returns_skip_children() {
        assert_eq!(should_prune("style"), PruneDecision::SkipChildren);
    }

    #[test]
    fn noscript_returns_skip_children() {
        assert_eq!(should_prune("noscript"), PruneDecision::SkipChildren);
    }

    // With the `prune_noise_regions` feature disabled (the default), noise
    // region elements are traversed normally.

    #[test]
    fn nav_returns_traverse_when_feature_disabled() {
        // Feature is disabled by default in test builds.
        assert_eq!(should_prune("nav"), PruneDecision::Traverse);
    }

    #[test]
    fn footer_returns_traverse_when_feature_disabled() {
        assert_eq!(should_prune("footer"), PruneDecision::Traverse);
    }

    #[test]
    fn aside_returns_traverse_when_feature_disabled() {
        assert_eq!(should_prune("aside"), PruneDecision::Traverse);
    }

    // Non-prunable elements always traverse.

    #[test]
    fn div_returns_traverse() {
        assert_eq!(should_prune("div"), PruneDecision::Traverse);
    }

    #[test]
    fn p_returns_traverse() {
        assert_eq!(should_prune("p"), PruneDecision::Traverse);
    }

    #[test]
    fn h1_returns_traverse() {
        assert_eq!(should_prune("h1"), PruneDecision::Traverse);
    }

    #[test]
    fn span_returns_traverse() {
        assert_eq!(should_prune("span"), PruneDecision::Traverse);
    }

    #[test]
    fn table_returns_traverse() {
        assert_eq!(should_prune("table"), PruneDecision::Traverse);
    }

    #[test]
    fn empty_string_returns_traverse() {
        assert_eq!(should_prune(""), PruneDecision::Traverse);
    }
}
