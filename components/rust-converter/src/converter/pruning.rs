//! Noise region early pruning for DOM traversal.
//!
//! This module provides the pruning decision logic used during DOM traversal
//! to skip subtrees that produce no meaningful Markdown output. Elements like
//! `<script>`, `<style>`, and `<noscript>` are always skipped (their children
//! are never visited). Semantic noise regions (`<nav>`, `<footer>`, `<aside>`)
//! are optionally prunable behind the `prune_noise_regions` feature flag.
//!
//! In v0.6.0, pruning is default-enabled at runtime via `PruneConfig` and
//! the `markdown_prune_noise` NGINX directive. The compile-time feature flag
//! remains for conditional compilation of pruning-extended logic.
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

/// Default noise region selectors used when no custom selectors are provided.
///
/// These CSS-selectors-like tag names match structural HTML regions that
/// typically have no value for AI agent consumption.
const DEFAULT_NOISE_REGION_ELEMENTS: &[&str] = &["nav", "footer", "aside"];

/// Semantic core elements that are never pruned, even if an operator
/// explicitly includes them in prune selectors. This hard-coded protection
/// prevents data loss where `<main>` or `<article>` regions (the primary
/// content) are accidentally removed.
const ALWAYS_PROTECTED_ELEMENTS: &[&str] = &["main", "article"];

/// Runtime configuration for noise region pruning.
///
/// Constructed from FFI options and passed to `should_prune_with_config`.
/// When `enabled` is false, only always-skipped elements (script/style/noscript)
/// are pruned. When `enabled` is true, noise region elements matching
/// `selectors` are also pruned, unless they match `protection_selectors`.
#[derive(Debug, Clone)]
pub struct PruneConfig {
    /// Whether noise region pruning is enabled at runtime.
    pub(crate) enabled: bool,
    /// Tag names to prune (replaces defaults when non-empty).
    pub(crate) selectors: Vec<String>,
    /// Tag names to protect from pruning (protection wins over prune).
    pub(crate) protection_selectors: Vec<String>,
}

impl PruneConfig {
    /// Create a default PruneConfig with pruning enabled and default selectors.
    pub fn default_enabled() -> Self {
        Self {
            enabled: true,
            selectors: DEFAULT_NOISE_REGION_ELEMENTS
                .iter()
                .map(|s| (*s).to_owned())
                .collect(),
            protection_selectors: Vec::new(),
        }
    }

    /// Create a PruneConfig with pruning disabled.
    pub fn disabled() -> Self {
        Self {
            enabled: false,
            selectors: Vec::new(),
            protection_selectors: Vec::new(),
        }
    }

    /// Create a PruneConfig from FFI option fields.
    ///
    /// When `prune_noise` is false, returns a disabled config.
    /// When `prune_noise` is true and `selectors_str` is Some, parses the
    /// space-separated selector string. When `selectors_str` is None, uses
    /// default selectors.
    #[allow(dead_code)]
    pub(crate) fn from_ffi(
        prune_noise: bool,
        selectors_str: Option<&str>,
        protection_selectors_str: Option<&str>,
    ) -> Self {
        if !prune_noise {
            return Self::disabled();
        }

        let selectors = match selectors_str {
            Some(s) if !s.is_empty() => s.split_whitespace().map(|tok| tok.to_owned()).collect(),
            _ => DEFAULT_NOISE_REGION_ELEMENTS
                .iter()
                .map(|s| (*s).to_owned())
                .collect(),
        };

        let protection_selectors = match protection_selectors_str {
            Some(s) if !s.is_empty() => s.split_whitespace().map(|tok| tok.to_owned()).collect(),
            _ => Vec::new(),
        };

        Self {
            enabled: true,
            selectors,
            protection_selectors,
        }
    }
}

/// Whether the compile-time `prune_noise_regions` feature is enabled.
#[cfg(feature = "prune_noise_regions")]
#[allow(dead_code)]
const PRUNE_NOISE_REGIONS_COMPILE: bool = true;

/// Whether the compile-time `prune_noise_regions` feature is enabled.
#[cfg(not(feature = "prune_noise_regions"))]
#[allow(dead_code)]
const PRUNE_NOISE_REGIONS_COMPILE: bool = false;

/// Decision for how to handle a DOM subtree during traversal.
///
/// Returned by [`should_prune`] to tell the traversal layer whether to
/// continue into a node's children, skip only the children, or skip
/// the entire subtree.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PruneDecision {
    /// Continue traversal into this node's children.
    Traverse,
    /// Skip this node's children but do not skip the node itself.
    SkipChildren,
    /// Skip this node and its entire subtree.
    SkipSubtree,
}

/// Determine the pruning decision for an HTML element by tag name.
///
/// Uses the compile-time feature flag for backward compatibility.
/// For runtime-configurable pruning, use [`should_prune_with_config`].
#[allow(dead_code)]
pub(crate) fn should_prune(tag_name: &str) -> PruneDecision {
    if SKIP_CHILDREN_ELEMENTS.contains(&tag_name) {
        return PruneDecision::SkipChildren;
    }

    if PRUNE_NOISE_REGIONS_COMPILE && DEFAULT_NOISE_REGION_ELEMENTS.contains(&tag_name) {
        return PruneDecision::SkipSubtree;
    }

    PruneDecision::Traverse
}

/// Determine the pruning decision for an HTML element using runtime config.
///
/// # Arguments
///
/// * `tag_name` - The lowercase HTML tag name to evaluate.
/// * `config` - Runtime pruning configuration.
///
/// # Returns
///
/// - [`PruneDecision::SkipChildren`] for `script`, `style`, `noscript`.
/// - [`PruneDecision::SkipSubtree`] for elements matching prune selectors
///   (when enabled and not protected).
/// - [`PruneDecision::Traverse`] for all other elements.
pub(crate) fn should_prune_with_config(tag_name: &str, config: &PruneConfig) -> PruneDecision {
    if SKIP_CHILDREN_ELEMENTS.contains(&tag_name) {
        return PruneDecision::SkipChildren;
    }

    if config.enabled {
        /* Hard-coded semantic core protection: main and article are never
         * pruned, regardless of operator configuration. This prevents
         * accidental removal of primary content regions. */
        if ALWAYS_PROTECTED_ELEMENTS.contains(&tag_name) {
            return PruneDecision::Traverse;
        }
        if config.protection_selectors.iter().any(|s| s == tag_name) {
            return PruneDecision::Traverse;
        }
        if config.selectors.iter().any(|s| s == tag_name) {
            return PruneDecision::SkipSubtree;
        }
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

    #[test]
    fn div_returns_traverse() {
        assert_eq!(should_prune("div"), PruneDecision::Traverse);
    }

    #[test]
    fn p_returns_traverse() {
        assert_eq!(should_prune("p"), PruneDecision::Traverse);
    }

    #[test]
    fn empty_string_returns_traverse() {
        assert_eq!(should_prune(""), PruneDecision::Traverse);
    }

    // Runtime-configurable pruning tests

    #[test]
    fn config_disabled_traverses_noise_regions() {
        let config = PruneConfig::disabled();
        assert_eq!(
            should_prune_with_config("nav", &config),
            PruneDecision::Traverse
        );
        assert_eq!(
            should_prune_with_config("footer", &config),
            PruneDecision::Traverse
        );
    }

    #[test]
    fn config_enabled_prunes_default_noise_regions() {
        let config = PruneConfig::default_enabled();
        assert_eq!(
            should_prune_with_config("nav", &config),
            PruneDecision::SkipSubtree
        );
        assert_eq!(
            should_prune_with_config("footer", &config),
            PruneDecision::SkipSubtree
        );
        assert_eq!(
            should_prune_with_config("aside", &config),
            PruneDecision::SkipSubtree
        );
    }

    #[test]
    fn config_custom_selectors() {
        let config = PruneConfig::from_ffi(true, Some("sidebar ad-slot"), None);
        assert_eq!(
            should_prune_with_config("sidebar", &config),
            PruneDecision::SkipSubtree
        );
        assert_eq!(
            should_prune_with_config("ad-slot", &config),
            PruneDecision::SkipSubtree
        );
        assert_eq!(
            should_prune_with_config("nav", &config),
            PruneDecision::Traverse
        );
    }

    #[test]
    fn config_protection_overrides_prune() {
        let config = PruneConfig::from_ffi(true, Some("nav footer aside"), Some("footer"));
        assert_eq!(
            should_prune_with_config("nav", &config),
            PruneDecision::SkipSubtree
        );
        assert_eq!(
            should_prune_with_config("footer", &config),
            PruneDecision::Traverse
        );
    }

    #[test]
    fn config_from_ffi_disabled() {
        let config = PruneConfig::from_ffi(false, Some("nav footer"), None);
        assert!(!config.enabled);
        assert_eq!(
            should_prune_with_config("nav", &config),
            PruneDecision::Traverse
        );
    }

    #[test]
    fn always_protected_main_and_article() {
        let config = PruneConfig::from_ffi(true, Some("main article nav"), None);
        assert_eq!(
            should_prune_with_config("main", &config),
            PruneDecision::Traverse
        );
        assert_eq!(
            should_prune_with_config("article", &config),
            PruneDecision::Traverse
        );
        assert_eq!(
            should_prune_with_config("nav", &config),
            PruneDecision::SkipSubtree
        );
    }

    #[test]
    fn always_protected_even_with_explicit_prune() {
        let config = PruneConfig::default_enabled();
        assert_eq!(
            should_prune_with_config("main", &config),
            PruneDecision::Traverse
        );
        assert_eq!(
            should_prune_with_config("article", &config),
            PruneDecision::Traverse
        );
    }

    // Compile-time feature flag tests

    #[cfg(feature = "prune_noise_regions")]
    #[test]
    fn nav_returns_skip_subtree_when_feature_enabled() {
        assert_eq!(should_prune("nav"), PruneDecision::SkipSubtree);
    }

    #[cfg(not(feature = "prune_noise_regions"))]
    #[test]
    fn nav_returns_traverse_when_feature_disabled() {
        assert_eq!(should_prune("nav"), PruneDecision::Traverse);
    }
}
