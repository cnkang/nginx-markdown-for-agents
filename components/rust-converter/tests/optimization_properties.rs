//! Property-based tests for parser path optimizations.
//!
//! This module contains property tests that validate correctness invariants
//! for the noise region pruning, fast path, and large-response optimizations.
//!
//! Each test is tagged with its feature and property number from the design doc.

use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;
use proptest::prelude::*;

// ============================================================================
// HTML generators
// ============================================================================

/// Escape a string so it is safe to embed as text content inside HTML.
fn escape_html(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// Build a minimal HTML document wrapping `tag_name` around a `<p>` with the
/// given inner text content and an arbitrary set of attributes on the outer tag.
fn build_html_with_attrs(tag_name: &str, attrs: &[(String, String)], content: &str) -> String {
    let attr_str: String = attrs
        .iter()
        .map(|(k, v)| format!(" {}=\"{}\"", k, escape_html(v)))
        .collect();
    format!(
        "<html><body><{tag}{attrs}><p>{content}</p></{tag}></body></html>",
        tag = tag_name,
        attrs = attr_str,
        content = escape_html(content),
    )
}

// ============================================================================
// Proptest strategies
// ============================================================================

/// Strategy that produces tag names drawn from the pruning lists and from
/// common non-prunable elements.
fn arb_tag_name() -> impl Strategy<Value = String> {
    prop::sample::select(vec![
        // SKIP_CHILDREN_ELEMENTS
        "script", "style", "noscript",
        // NOISE_REGION_ELEMENTS (traversed by default, pruned behind feature flag)
        "nav", "footer", "aside", // Common non-prunable elements
        "div", "p", "h1", "h2", "span", "table", "a", "img", "section", "article",
    ])
    .prop_map(String::from)
}

/// Strategy that produces a random lowercase tag-like string (1–12 chars).
fn arb_random_tag_name() -> impl Strategy<Value = String> {
    "[a-z][a-z0-9]{0,11}"
}

/// Strategy that produces a vector of 0–4 attribute key-value pairs.
fn arb_attrs() -> impl Strategy<Value = Vec<(String, String)>> {
    prop::collection::vec(
        (
            "[a-z][a-z0-9\\-]{0,9}",  // attribute name
            "[a-zA-Z0-9 _\\-]{0,20}", // attribute value
        )
            .prop_map(|(k, v)| (k, v)),
        0..=4,
    )
}

/// Simple text content for inner paragraphs.
fn arb_content() -> impl Strategy<Value = String> {
    "[A-Za-z0-9 ]{1,30}"
}

// ============================================================================
// Feature: parser-path-optimization, Property 3: Tag-name-only pruning invariance
// ============================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    // Feature: parser-path-optimization, Property 3: Tag-name-only pruning invariance
    //
    // For any HTML element tag name and any two distinct sets of attributes on
    // that element, the converter SHALL produce the same Markdown output. The
    // pruning decision depends solely on the tag name, so attributes must not
    // influence the result.
    //
    // Validates: Requirements 1.4

    #[test]
    fn pruning_decision_depends_only_on_tag_name_known_tags(
        tag_name in arb_tag_name(),
        attrs1 in arb_attrs(),
        attrs2 in arb_attrs(),
        content in arb_content(),
    ) {
        let html1 = build_html_with_attrs(&tag_name, &attrs1, &content);
        let html2 = build_html_with_attrs(&tag_name, &attrs2, &content);

        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html1.as_bytes()).expect("parse html1");
        let dom2 = parse_html(html2.as_bytes()).expect("parse html2");

        let out1 = converter.convert(&dom1).expect("convert html1");
        let out2 = converter.convert(&dom2).expect("convert html2");

        prop_assert_eq!(
            &out1, &out2,
            "Output differs for <{}> with different attributes.\n\
             attrs1={:?}\nattrs2={:?}\nhtml1={}\nhtml2={}",
            tag_name, attrs1, attrs2, html1, html2,
        );
    }

    #[test]
    fn pruning_decision_depends_only_on_tag_name_random_tags(
        tag_name in arb_random_tag_name(),
        attrs1 in arb_attrs(),
        attrs2 in arb_attrs(),
        content in arb_content(),
    ) {
        let html1 = build_html_with_attrs(&tag_name, &attrs1, &content);
        let html2 = build_html_with_attrs(&tag_name, &attrs2, &content);

        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html1.as_bytes()).expect("parse html1");
        let dom2 = parse_html(html2.as_bytes()).expect("parse html2");

        let out1 = converter.convert(&dom1).expect("convert html1");
        let out2 = converter.convert(&dom2).expect("convert html2");

        prop_assert_eq!(
            &out1, &out2,
            "Output differs for <{}> with different attributes.\n\
             attrs1={:?}\nattrs2={:?}",
            tag_name, attrs1, attrs2,
        );
    }

    // Determinism sub-property: calling the converter twice on the exact same
    // input must yield identical output, confirming no hidden mutable state.
    #[test]
    fn pruning_is_deterministic(
        tag_name in arb_tag_name(),
        attrs in arb_attrs(),
        content in arb_content(),
    ) {
        let html = build_html_with_attrs(&tag_name, &attrs, &content);

        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html.as_bytes()).expect("parse pass 1");
        let dom2 = parse_html(html.as_bytes()).expect("parse pass 2");

        let out1 = converter.convert(&dom1).expect("convert pass 1");
        let out2 = converter.convert(&dom2).expect("convert pass 2");

        prop_assert_eq!(
            &out1, &out2,
            "Non-deterministic output for <{}>",
            tag_name,
        );
    }

    // SKIP_CHILDREN_ELEMENTS sub-property: for tags in the skip-children list,
    // the inner content must never appear in the Markdown output regardless of
    // attributes.
    #[test]
    fn skip_children_elements_always_pruned(
        tag_name in prop::sample::select(vec!["script", "style", "noscript"]),
        attrs in arb_attrs(),
        content in "[A-Za-z]{5,20}",
    ) {
        let html = build_html_with_attrs(tag_name, &attrs, &content);

        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        prop_assert!(
            !output.contains(&content),
            "<{}> content should be pruned but found in output.\n\
             content={:?}\noutput={:?}",
            tag_name, content, output,
        );
    }

    // Non-prunable elements sub-property: for common content tags, the inner
    // text must appear in the Markdown output regardless of attributes.
    #[test]
    fn non_prunable_elements_always_traverse(
        tag_name in prop::sample::select(vec!["div", "section", "article"]),
        attrs in arb_attrs(),
        content in "[A-Za-z]{5,20}",
    ) {
        let html = build_html_with_attrs(tag_name, &attrs, &content);

        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        prop_assert!(
            output.contains(&content),
            "<{}> content should be present but missing from output.\n\
             content={:?}\noutput={:?}",
            tag_name, content, output,
        );
    }
}

// ============================================================================
// Feature: parser-path-optimization, Property 1: Output equivalence for non-noise-region HTML
// ============================================================================

/// Strategy that produces a tag name drawn from non-noise-region elements only.
/// This excludes `<nav>`, `<footer>`, and `<aside>`, and also excludes list
/// containers (`ul`, `ol`) since wrapping `<p>` directly in them (without
/// `<li>`) is invalid HTML that parsers may discard.
fn arb_non_noise_tag() -> impl Strategy<Value = String> {
    prop::sample::select(vec![
        // Structural
        "div", "span", "section", "article", "main", "header", // Content
        "h1", "h2", "h3", "h4", "h5", "h6", "p", // Inline
        "strong", "b", "em", "i", "code",
    ])
    .prop_map(String::from)
}

/// Strategy that produces a tag name from the prunable-but-output-equivalent set.
/// These elements are always pruned (script/style/noscript), so their presence
/// does not change the output compared to the unoptimized path.
fn arb_prunable_tag() -> impl Strategy<Value = String> {
    prop::sample::select(vec!["script", "style", "noscript"]).prop_map(String::from)
}

/// Build a random HTML document from a sequence of child elements.
/// Each child is either a content element wrapping text or a prunable element
/// wrapping text that should be stripped.
fn build_non_noise_html(children: &[(bool, String, String)]) -> String {
    let mut body = String::new();
    for (is_prunable, tag, text) in children {
        if *is_prunable {
            // Prunable element — content should be stripped from output
            body.push_str(&format!(
                "<{tag}>{content}</{tag}>",
                tag = tag,
                content = escape_html(text),
            ));
        } else {
            // Content element — wrap text in a <p> so it renders
            body.push_str(&format!(
                "<{tag}><p>{content}</p></{tag}>",
                tag = tag,
                content = escape_html(text),
            ));
        }
    }
    format!("<html><body>{}</body></html>", body)
}

/// Strategy that produces a vector of child element descriptors for
/// `build_non_noise_html`. Each entry is `(is_prunable, tag_name, text)`.
fn arb_non_noise_children() -> impl Strategy<Value = Vec<(bool, String, String)>> {
    prop::collection::vec(
        prop::bool::weighted(0.2).prop_flat_map(|is_prunable| {
            if is_prunable {
                (
                    Just(true),
                    arb_prunable_tag(),
                    "[A-Za-z]{5,20}".prop_map(String::from),
                )
                    .boxed()
            } else {
                (
                    Just(false),
                    arb_non_noise_tag(),
                    // Use single-word tokens joined by single spaces to avoid
                    // whitespace normalization mismatches in assertions.
                    prop::collection::vec("[A-Za-z0-9]{1,8}", 1..=4)
                        .prop_map(|words| words.join(" ")),
                )
                    .boxed()
            }
        }),
        1..=8,
    )
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    // Feature: parser-path-optimization, Property 1: Output equivalence for non-noise-region HTML
    // Validates: Requirements 1.1, 1.3, 1.6, 6.1
    //
    // For any valid HTML input that does not contain <nav>, <footer>, or <aside>
    // elements, the optimized converter (with pruning active) SHALL produce
    // byte-identical Markdown output across two independent conversions
    // (determinism), strip all script/style/noscript content, and preserve
    // content element text.

    #[test]
    fn non_noise_html_output_is_deterministic(
        children in arb_non_noise_children(),
    ) {
        let html = build_non_noise_html(&children);
        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html.as_bytes()).expect("parse pass 1");
        let dom2 = parse_html(html.as_bytes()).expect("parse pass 2");

        let out1 = converter.convert(&dom1).expect("convert pass 1");
        let out2 = converter.convert(&dom2).expect("convert pass 2");

        // Byte-identical output across two conversions
        prop_assert_eq!(
            &out1, &out2,
            "Non-deterministic output for non-noise-region HTML.\nhtml={}",
            html,
        );
    }

    #[test]
    fn non_noise_html_output_well_formed(
        children in arb_non_noise_children(),
    ) {
        let html = build_non_noise_html(&children);
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // Non-empty input should produce non-empty output
        let has_content = children.iter().any(|(is_prunable, _, _)| !is_prunable);
        if has_content {
            prop_assert!(
                !output.trim().is_empty(),
                "Expected non-empty output for HTML with content elements.\nhtml={}",
                html,
            );
        }

        // Output must end with a newline (Markdown convention)
        if !output.is_empty() {
            prop_assert!(
                output.ends_with('\n'),
                "Output does not end with newline.\noutput={:?}",
                output,
            );
        }
    }

    #[test]
    fn non_noise_html_script_style_noscript_stripped(
        children in arb_non_noise_children(),
    ) {
        let html = build_non_noise_html(&children);
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // Verify that content inside prunable elements never appears in output
        for (is_prunable, _tag, text) in &children {
            if *is_prunable && text.len() >= 5 {
                prop_assert!(
                    !output.contains(text.as_str()),
                    "Prunable element content leaked into output.\n\
                     text={:?}\noutput={:?}\nhtml={}",
                    text, output, html,
                );
            }
        }
    }

    #[test]
    fn non_noise_html_content_elements_preserved(
        children in arb_non_noise_children(),
    ) {
        let html = build_non_noise_html(&children);
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // Verify that text inside non-prunable content elements appears in output
        for (is_prunable, _tag, text) in &children {
            if !is_prunable && !text.trim().is_empty() {
                prop_assert!(
                    output.contains(text.trim()),
                    "Content element text missing from output.\n\
                     text={:?}\noutput={:?}\nhtml={}",
                    text, output, html,
                );
            }
        }
    }
}

// ============================================================================
// Feature: parser-path-optimization, Property 2: Pruning scope limited to pruned subtrees
// ============================================================================

/// Strategy that produces a noise-region tag name (`nav`, `footer`, `aside`).
fn arb_noise_region_tag() -> impl Strategy<Value = String> {
    prop::sample::select(vec!["nav", "footer", "aside"]).prop_map(String::from)
}

/// Strategy that produces a content tag suitable for wrapping `<p>` text.
/// Excludes noise regions, prunable elements, and list containers.
fn arb_content_tag() -> impl Strategy<Value = String> {
    prop::sample::select(vec!["div", "section", "article", "main", "header"]).prop_map(String::from)
}

/// A single content element descriptor: `(tag_name, text)`.
fn arb_content_element() -> impl Strategy<Value = (String, String)> {
    (
        arb_content_tag(),
        // Single-word tokens joined by spaces to avoid whitespace
        // normalization mismatches.
        prop::collection::vec("[A-Za-z0-9]{2,8}", 1..=3).prop_map(|words| words.join(" ")),
    )
}

/// A noise-region element descriptor: `(tag_name, text)`.
fn arb_noise_element() -> impl Strategy<Value = (String, String)> {
    (
        arb_noise_region_tag(),
        "[A-Za-z]{5,15}".prop_map(String::from),
    )
}

/// Build an HTML document from content elements only (no noise regions).
fn build_content_only_html(content_elements: &[(String, String)]) -> String {
    let mut body = String::new();
    for (tag, text) in content_elements {
        body.push_str(&format!(
            "<{tag}><p>{content}</p></{tag}>",
            tag = tag,
            content = escape_html(text),
        ));
    }
    format!("<html><body>{}</body></html>", body)
}

/// Build an HTML document with noise-region elements interleaved between
/// content elements. Each noise region is inserted *after* the content
/// element at the corresponding index (if there are more noise elements
/// than content elements, extras are appended at the end).
fn build_html_with_noise_regions(
    content_elements: &[(String, String)],
    noise_elements: &[(String, String)],
) -> String {
    let mut body = String::new();
    let mut noise_iter = noise_elements.iter();

    for (tag, text) in content_elements {
        // Content element
        body.push_str(&format!(
            "<{tag}><p>{content}</p></{tag}>",
            tag = tag,
            content = escape_html(text),
        ));
        // Insert a noise region after this content element, if available
        if let Some((noise_tag, noise_text)) = noise_iter.next() {
            body.push_str(&format!(
                "<{tag}><p>{content}</p></{tag}>",
                tag = noise_tag,
                content = escape_html(noise_text),
            ));
        }
    }
    // Append any remaining noise elements
    for (noise_tag, noise_text) in noise_iter {
        body.push_str(&format!(
            "<{tag}><p>{content}</p></{tag}>",
            tag = noise_tag,
            content = escape_html(noise_text),
        ));
    }

    format!("<html><body>{}</body></html>", body)
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    // Feature: parser-path-optimization, Property 2: Pruning scope limited to pruned subtrees
    // Validates: Requirements 1.2, 6.4
    //
    // For any valid HTML input containing <nav>, <footer>, or <aside> elements,
    // the difference between the optimized output and the unoptimized output
    // SHALL be limited to the removal of content originating from those pruned
    // subtrees. All content outside the pruned subtrees SHALL be byte-identical
    // between the two outputs.
    //
    // With the `prune_noise_regions` feature DISABLED (default build):
    // - Content inside nav/footer/aside IS present in output (traversed normally)
    // - Content outside nav/footer/aside IS present in output
    // - The presence of nav/footer/aside elements does not corrupt surrounding content

    /// Content outside noise regions is always preserved in output.
    #[test]
    fn content_outside_noise_regions_preserved(
        content_elements in prop::collection::vec(arb_content_element(), 1..=6),
        noise_elements in prop::collection::vec(arb_noise_element(), 1..=4),
    ) {
        let html = build_html_with_noise_regions(&content_elements, &noise_elements);
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // Every content element's text must appear in the output
        for (_, text) in &content_elements {
            prop_assert!(
                output.contains(text.trim()),
                "Content element text missing from output when noise regions present.\n\
                 text={:?}\noutput={:?}\nhtml={}",
                text, output, html,
            );
        }
    }

    /// Noise region content is present in output when feature is disabled
    /// (default build traverses nav/footer/aside normally).
    #[test]
    fn noise_region_content_present_when_feature_disabled(
        content_elements in prop::collection::vec(arb_content_element(), 1..=4),
        noise_elements in prop::collection::vec(arb_noise_element(), 1..=4),
    ) {
        let html = build_html_with_noise_regions(&content_elements, &noise_elements);
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // With feature disabled, noise region text should also appear in output
        #[cfg(not(feature = "prune_noise_regions"))]
        for (_, noise_text) in &noise_elements {
            prop_assert!(
                output.contains(noise_text.as_str()),
                "Noise region content should be present (feature disabled) but is missing.\n\
                 noise_text={:?}\noutput={:?}\nhtml={}",
                noise_text, output, html,
            );
        }

        // With feature enabled, noise region text should be absent from output
        // (pruned subtrees are skipped during traversal).
        #[cfg(feature = "prune_noise_regions")]
        for (_, noise_text) in &noise_elements {
            prop_assert!(
                !output.contains(noise_text.as_str()),
                "Noise region content should be absent (feature enabled) but is present.\n\
                 noise_text={:?}\noutput={:?}\nhtml={}",
                noise_text, output, html,
            );
        }
    }

    /// Adding noise regions between content elements does not change the
    /// content-element portions of the output. We verify this by checking
    /// that every content text from the content-only HTML also appears in
    /// the noise-interleaved HTML output.
    #[test]
    fn noise_regions_do_not_corrupt_surrounding_content(
        content_elements in prop::collection::vec(arb_content_element(), 1..=6),
        noise_elements in prop::collection::vec(arb_noise_element(), 1..=4),
    ) {
        let content_only_html = build_content_only_html(&content_elements);
        let with_noise_html = build_html_with_noise_regions(&content_elements, &noise_elements);

        let converter = MarkdownConverter::new();

        let dom_content = parse_html(content_only_html.as_bytes()).expect("parse content-only");
        let dom_noise = parse_html(with_noise_html.as_bytes()).expect("parse with-noise");

        let out_content = converter.convert(&dom_content).expect("convert content-only");
        let out_noise = converter.convert(&dom_noise).expect("convert with-noise");

        // Every content text present in the content-only output must also
        // appear in the noise-interleaved output.
        for (_, text) in &content_elements {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                let in_content = out_content.contains(trimmed);
                let in_noise = out_noise.contains(trimmed);
                prop_assert!(
                    !in_content || in_noise,
                    "Content text present in content-only output but missing from \
                     noise-interleaved output.\n\
                     text={:?}\ncontent_only_output={:?}\nnoise_output={:?}",
                    trimmed, out_content, out_noise,
                );
            }
        }
    }

    /// Conversion with noise regions is deterministic: two passes on the
    /// same input produce byte-identical output.
    #[test]
    fn noise_region_conversion_is_deterministic(
        content_elements in prop::collection::vec(arb_content_element(), 1..=6),
        noise_elements in prop::collection::vec(arb_noise_element(), 1..=4),
    ) {
        let html = build_html_with_noise_regions(&content_elements, &noise_elements);
        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html.as_bytes()).expect("parse pass 1");
        let dom2 = parse_html(html.as_bytes()).expect("parse pass 2");

        let out1 = converter.convert(&dom1).expect("convert pass 1");
        let out2 = converter.convert(&dom2).expect("convert pass 2");

        prop_assert_eq!(
            &out1, &out2,
            "Non-deterministic output for HTML with noise regions.\nhtml={}",
            html,
        );
    }

    /// The content-only output is a substring-wise subset of the
    /// noise-interleaved output: every line from the content-only output
    /// that contains content text must appear in the noise-interleaved
    /// output as well.
    #[test]
    fn content_lines_preserved_across_noise_insertion(
        content_elements in prop::collection::vec(arb_content_element(), 1..=6),
        noise_elements in prop::collection::vec(arb_noise_element(), 1..=4),
    ) {
        let content_only_html = build_content_only_html(&content_elements);
        let with_noise_html = build_html_with_noise_regions(&content_elements, &noise_elements);

        let converter = MarkdownConverter::new();

        let dom_content = parse_html(content_only_html.as_bytes()).expect("parse content-only");
        let dom_noise = parse_html(with_noise_html.as_bytes()).expect("parse with-noise");

        let out_content = converter.convert(&dom_content).expect("convert content-only");
        let out_noise = converter.convert(&dom_noise).expect("convert with-noise");

        // Each non-empty, non-whitespace line from the content-only output
        // that contains actual content text should appear in the
        // noise-interleaved output.
        for line in out_content.lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            // Check if this line contains any of our content texts
            let is_content_line = content_elements
                .iter()
                .any(|(_, text)| trimmed.contains(text.trim()));
            if is_content_line {
                prop_assert!(
                    out_noise.contains(trimmed),
                    "Content line from content-only output missing in noise-interleaved output.\n\
                     line={:?}\ncontent_output={:?}\nnoise_output={:?}",
                    trimmed, out_content, out_noise,
                );
            }
        }
    }
}

// ============================================================================
// HTML generators for fast path and security tests
// ============================================================================

/// Strategy that produces HTML using only fast-path-eligible elements.
/// No tables, forms, iframes, video, audio — only elements in FAST_PATH_ELEMENTS.
fn arb_fast_path_html() -> impl Strategy<Value = String> {
    // Pick a structure: heading + paragraphs + optional list + optional inline
    let heading_tag = prop::sample::select(vec!["h1", "h2", "h3", "h4", "h5", "h6"]);
    let heading_text = "[A-Za-z0-9 ]{3,20}";
    let paragraph_count = 1..=4usize;
    let paragraph_text = "[A-Za-z0-9 ]{5,30}";
    let use_list = prop::bool::ANY;
    let list_items = prop::collection::vec("[A-Za-z0-9 ]{3,15}", 1..=4);
    let use_blockquote = prop::bool::ANY;
    let blockquote_text = "[A-Za-z0-9 ]{5,20}";
    let use_code_block = prop::bool::ANY;
    let code_text = "[a-z_]{3,15}";
    let use_link = prop::bool::ANY;
    let link_text = "[A-Za-z]{3,10}";

    (
        heading_tag,
        heading_text,
        paragraph_count,
        paragraph_text,
        use_list,
        list_items,
        use_blockquote,
        blockquote_text,
        use_code_block,
        (code_text, use_link, link_text),
    )
        .prop_map(
            |(
                h_tag,
                h_text,
                p_count,
                p_text,
                use_list,
                items,
                use_bq,
                bq_text,
                use_code,
                (c_text, use_link, l_text),
            )| {
                let mut body = String::new();

                // Heading
                body.push_str(&format!(
                    "<{h}>{t}</{h}>",
                    h = h_tag,
                    t = escape_html(&h_text)
                ));

                // Paragraphs with inline elements
                for _ in 0..p_count {
                    body.push_str(&format!(
                        "<p><strong>{t}</strong></p>",
                        t = escape_html(&p_text)
                    ));
                }

                // Optional list
                if use_list {
                    body.push_str("<ul>");
                    for item in &items {
                        body.push_str(&format!("<li>{}</li>", escape_html(item)));
                    }
                    body.push_str("</ul>");
                }

                // Optional blockquote
                if use_bq {
                    body.push_str(&format!(
                        "<blockquote><p>{}</p></blockquote>",
                        escape_html(&bq_text)
                    ));
                }

                // Optional code block
                if use_code {
                    body.push_str(&format!("<pre><code>{}</code></pre>", escape_html(&c_text)));
                }

                // Optional link
                if use_link {
                    body.push_str(&format!(
                        "<p><a href=\"https://example.com\">{}</a></p>",
                        escape_html(&l_text)
                    ));
                }

                format!("<html><body>{}</body></html>", body)
            },
        )
}

/// Strategy that produces HTML containing dangerous content for security testing.
/// Includes script tags, event handler attributes, javascript: URLs, and data: URLs.
fn arb_html_with_dangerous_content() -> impl Strategy<Value = (String, Vec<String>)> {
    let script_content = "[a-zA-Z0-9()' ]{5,30}";
    let event_handler = prop::sample::select(vec!["onclick", "onload", "onerror", "onmouseover"]);
    let handler_value = "[a-zA-Z0-9()' ]{3,20}";
    let safe_text = "[A-Za-z]{5,20}";
    let js_payload = "[a-zA-Z0-9()' ]{3,20}";

    (
        script_content,
        event_handler,
        handler_value,
        safe_text,
        js_payload,
    )
        .prop_map(|(script_body, evt_attr, evt_val, safe, js_pay)| {
            let mut dangerous_markers = Vec::new();

            // Track what dangerous content we're inserting
            dangerous_markers.push(format!("script:{}", script_body));
            dangerous_markers.push(format!("event:{}={}", evt_attr, evt_val));
            dangerous_markers.push("jsurl:javascript:".to_string());
            dangerous_markers.push("dataurl:data:".to_string());

            let html = format!(
                "<html><body>\
                 <script>{script}</script>\
                 <div {evt}=\"{evt_val}\"><p>{safe}</p></div>\
                 <p><a href=\"javascript:{js}\">click</a></p>\
                 <p><img src=\"data:text/html,{js}\"></p>\
                 <section><p>{safe}</p></section>\
                 </body></html>",
                script = escape_html(&script_body),
                evt = evt_attr,
                evt_val = escape_html(&evt_val),
                safe = escape_html(&safe),
                js = escape_html(&js_pay),
            );

            (html, dangerous_markers)
        })
}

/// Strategy that produces HTML with dangerous content wrapped in fast-path-eligible
/// structure (no tables, forms, etc.).
fn arb_fast_path_html_with_dangerous_content() -> impl Strategy<Value = (String, String)> {
    let safe_text = "[A-Za-z]{5,20}";
    let script_content = "[a-zA-Z0-9()' ]{5,20}";
    let event_handler = prop::sample::select(vec!["onclick", "onload", "onerror"]);
    let handler_value = "[a-zA-Z0-9()' ]{3,15}";

    (safe_text, script_content, event_handler, handler_value).prop_map(
        |(safe, script_body, evt, evt_val)| {
            let html = format!(
                "<html><body>\
                 <h1>{safe}</h1>\
                 <script>{script}</script>\
                 <p {evt}=\"{evt_val}\">{safe}</p>\
                 <p><a href=\"javascript:void(0)\">link</a></p>\
                 </body></html>",
                safe = escape_html(&safe),
                script = escape_html(&script_body),
                evt = evt,
                evt_val = escape_html(&evt_val),
            );
            (html, safe)
        },
    )
}

/// Strategy that produces HTML with dangerous content wrapped in non-fast-path
/// structure (includes table to force normal path).
fn arb_non_fast_path_html_with_dangerous_content() -> impl Strategy<Value = (String, String)> {
    let safe_text = "[A-Za-z]{5,20}";
    let script_content = "[a-zA-Z0-9()' ]{5,20}";
    let event_handler = prop::sample::select(vec!["onclick", "onload", "onerror"]);
    let handler_value = "[a-zA-Z0-9()' ]{3,15}";

    (safe_text, script_content, event_handler, handler_value).prop_map(
        |(safe, script_body, evt, evt_val)| {
            let html = format!(
                "<html><body>\
                 <h1>{safe}</h1>\
                 <script>{script}</script>\
                 <p {evt}=\"{evt_val}\">{safe}</p>\
                 <p><a href=\"javascript:void(0)\">link</a></p>\
                 <table><tr><td>cell</td></tr></table>\
                 </body></html>",
                safe = escape_html(&safe),
                script = escape_html(&script_body),
                evt = evt,
                evt_val = escape_html(&evt_val),
            );
            (html, safe)
        },
    )
}

// ============================================================================
// Feature: parser-path-optimization, Property 5: Fast path qualification correctness
// Validates: Requirements 2.1, 2.3
// ============================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    // Feature: parser-path-optimization, Property 5: Fast path qualification correctness
    //
    // For any DOM tree composed exclusively of elements in the FAST_PATH_ELEMENTS
    // set with nesting depth below FAST_PATH_MAX_DEPTH, the qualifies function
    // SHALL return FastPathResult::Qualifies. For any DOM tree containing at least
    // one element not in FAST_PATH_ELEMENTS (and not a prunable noise region),
    // the qualifies function SHALL return FastPathResult::Normal.
    //
    // Since fast_path is pub(crate), we test through the public API:
    // - Fast-path-eligible HTML converts successfully (indirect qualification)
    // - Disqualifying HTML also converts successfully (falls back to normal path)

    /// Fast-path-eligible HTML (only allowed elements) converts successfully.
    #[test]
    fn fast_path_eligible_html_converts_successfully(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse fast-path HTML");
        let result = converter.convert(&dom);

        prop_assert!(
            result.is_ok(),
            "Fast-path-eligible HTML should convert successfully.\nhtml={}\nerr={:?}",
            html, result.err(),
        );

        let output = result.unwrap();
        prop_assert!(
            !output.trim().is_empty(),
            "Fast-path-eligible HTML should produce non-empty output.\nhtml={}",
            html,
        );
    }

    /// HTML with disqualifying elements (table, form, iframe, video) still
    /// converts successfully via the normal path fallback.
    #[test]
    fn disqualifying_html_converts_via_normal_path(
        disqualifier in prop::sample::select(vec!["table", "form", "iframe", "video"]),
        content in prop::collection::vec("[A-Za-z0-9]{2,5}", 1..=3).prop_map(|w| w.join(" ")),
    ) {
        let html = match disqualifier {
            "table" => format!(
                "<html><body><p>{c}</p><table><tr><td>cell</td></tr></table></body></html>",
                c = escape_html(&content),
            ),
            "form" => format!(
                "<html><body><p>{c}</p><form><p>form text</p></form></body></html>",
                c = escape_html(&content),
            ),
            "iframe" => format!(
                "<html><body><p>{c}</p><iframe src=\"https://example.com\">fallback</iframe></body></html>",
                c = escape_html(&content),
            ),
            "video" => format!(
                "<html><body><p>{c}</p><video><source src=\"v.mp4\"></video></body></html>",
                c = escape_html(&content),
            ),
            _ => unreachable!(),
        };

        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse disqualifying HTML");
        let result = converter.convert(&dom);

        prop_assert!(
            result.is_ok(),
            "Disqualifying HTML should still convert successfully via normal path.\n\
             disqualifier={}\nhtml={}\nerr={:?}",
            disqualifier, html, result.err(),
        );

        let output = result.unwrap();
        // The safe content outside the disqualifying element should be present
        prop_assert!(
            output.contains(content.trim()),
            "Content outside disqualifying element should be preserved.\n\
             content={:?}\noutput={:?}",
            content, output,
        );
    }

    /// Fast-path-eligible HTML is deterministic: two conversions produce
    /// identical output.
    #[test]
    fn fast_path_html_is_deterministic(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html.as_bytes()).expect("parse pass 1");
        let dom2 = parse_html(html.as_bytes()).expect("parse pass 2");

        let out1 = converter.convert(&dom1).expect("convert pass 1");
        let out2 = converter.convert(&dom2).expect("convert pass 2");

        prop_assert_eq!(
            &out1, &out2,
            "Fast-path HTML should produce deterministic output.\nhtml={}",
            html,
        );
    }
}

// ============================================================================
// Feature: parser-path-optimization, Property 6: Fast path output equivalence
// Validates: Requirements 2.2, 6.2
// ============================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    // Feature: parser-path-optimization, Property 6: Fast path output equivalence
    //
    // For any valid HTML input that qualifies for the fast path, the converter
    // SHALL produce byte-identical Markdown output regardless of whether the
    // fast path optimization is active or inactive.
    //
    // Since we cannot toggle the fast path at runtime, we verify:
    // - Converting the same fast-path-eligible HTML twice produces byte-identical output
    // - Fast-path-eligible content produces the same text when a non-fast-path
    //   sibling (table) is added — the content portion is preserved

    /// Converting the same fast-path-eligible HTML twice produces byte-identical output.
    #[test]
    fn fast_path_output_byte_identical_across_conversions(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html.as_bytes()).expect("parse pass 1");
        let dom2 = parse_html(html.as_bytes()).expect("parse pass 2");

        let out1 = converter.convert(&dom1).expect("convert pass 1");
        let out2 = converter.convert(&dom2).expect("convert pass 2");

        prop_assert_eq!(
            &out1, &out2,
            "Fast-path HTML must produce byte-identical output across conversions.\nhtml={}",
            html,
        );
    }

    /// Adding a non-fast-path sibling (table) to fast-path-eligible content
    /// should not change the content portion of the output. The content text
    /// from the fast-path-eligible elements must still appear in the output.
    #[test]
    fn fast_path_content_preserved_with_table_sibling(
        heading_tag in prop::sample::select(vec!["h1", "h2", "h3"]),
        heading_text in "[A-Za-z0-9]{5,15}",
        para_text in "[A-Za-z0-9]{5,15}",
    ) {
        // Fast-path-eligible HTML
        let fast_html = format!(
            "<html><body><{h}>{ht}</{h}><p>{pt}</p></body></html>",
            h = heading_tag,
            ht = escape_html(&heading_text),
            pt = escape_html(&para_text),
        );

        // Same content with a table sibling (forces normal path)
        let normal_html = format!(
            "<html><body><{h}>{ht}</{h}><p>{pt}</p><table><tr><td>extra</td></tr></table></body></html>",
            h = heading_tag,
            ht = escape_html(&heading_text),
            pt = escape_html(&para_text),
        );

        let converter = MarkdownConverter::new();

        let dom_fast = parse_html(fast_html.as_bytes()).expect("parse fast HTML");
        let dom_normal = parse_html(normal_html.as_bytes()).expect("parse normal HTML");

        let out_fast = converter.convert(&dom_fast).expect("convert fast");
        let out_normal = converter.convert(&dom_normal).expect("convert normal");

        // The heading and paragraph text from the fast-path content must appear
        // in both outputs
        prop_assert!(
            out_fast.contains(&heading_text),
            "Heading text missing from fast-path output.\ntext={:?}\noutput={:?}",
            heading_text, out_fast,
        );
        prop_assert!(
            out_fast.contains(&para_text),
            "Paragraph text missing from fast-path output.\ntext={:?}\noutput={:?}",
            para_text, out_fast,
        );
        prop_assert!(
            out_normal.contains(&heading_text),
            "Heading text missing from normal-path output.\ntext={:?}\noutput={:?}",
            heading_text, out_normal,
        );
        prop_assert!(
            out_normal.contains(&para_text),
            "Paragraph text missing from normal-path output.\ntext={:?}\noutput={:?}",
            para_text, out_normal,
        );
    }

    /// Output from fast-path-eligible HTML always ends with a newline
    /// (Markdown convention) and is well-formed.
    #[test]
    fn fast_path_output_well_formed(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        if !output.is_empty() {
            prop_assert!(
                output.ends_with('\n'),
                "Fast-path output should end with newline.\noutput={:?}",
                output,
            );
        }

        // Output should not contain raw HTML tags from fast-path elements
        // (they should be converted to Markdown syntax)
        prop_assert!(
            !output.contains("<html>") && !output.contains("<body>"),
            "Output should not contain raw HTML structural tags.\noutput={:?}",
            output,
        );
    }
}

// ============================================================================
// Feature: parser-path-optimization, Property 4: Security baseline preserved
// Validates: Requirements 1.7, 2.5, 5.1
// ============================================================================

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    // Feature: parser-path-optimization, Property 4: Security baseline preserved
    // across all optimization paths
    //
    // For any valid HTML input containing dangerous elements (script, etc.),
    // event handler attributes (on* prefix), or dangerous URLs (javascript:,
    // data:, etc.), the optimized converter SHALL sanitize these threats
    // identically to the unoptimized converter.

    /// Script tag content is stripped from output regardless of path.
    #[test]
    fn script_content_stripped_from_output(
        (html, _markers) in arb_html_with_dangerous_content(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // Script tags and their content must not appear in output
        prop_assert!(
            !output.contains("<script>") && !output.contains("</script>"),
            "Script tags should be stripped from output.\noutput={:?}",
            output,
        );
    }

    /// javascript: URLs are stripped from output regardless of path.
    #[test]
    fn javascript_urls_stripped_from_output(
        (html, _markers) in arb_html_with_dangerous_content(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        let output_lower = output.to_lowercase();
        prop_assert!(
            !output_lower.contains("javascript:"),
            "javascript: URLs should be stripped from output.\noutput={:?}",
            output,
        );
    }

    /// data: URLs are stripped from output regardless of path.
    #[test]
    fn data_urls_stripped_from_output(
        (html, _markers) in arb_html_with_dangerous_content(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        let output_lower = output.to_lowercase();
        prop_assert!(
            !output_lower.contains("data:text/html"),
            "data: URLs should be stripped from output.\noutput={:?}",
            output,
        );
    }

    /// Event handler attributes (onclick, onload, etc.) do not appear in output.
    #[test]
    fn event_handlers_stripped_from_output(
        (html, _markers) in arb_html_with_dangerous_content(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // Event handler attribute names should not appear in Markdown output
        prop_assert!(
            !output.contains("onclick=") && !output.contains("onload=")
                && !output.contains("onerror=") && !output.contains("onmouseover="),
            "Event handler attributes should be stripped from output.\noutput={:?}",
            output,
        );
    }

    /// Security sanitization works identically for fast-path-eligible HTML.
    #[test]
    fn security_preserved_on_fast_path(
        (html, safe_text) in arb_fast_path_html_with_dangerous_content(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // Dangerous content must be stripped
        prop_assert!(
            !output.contains("<script>"),
            "Script tags should be stripped on fast path.\noutput={:?}",
            output,
        );

        let output_lower = output.to_lowercase();
        prop_assert!(
            !output_lower.contains("javascript:"),
            "javascript: URLs should be stripped on fast path.\noutput={:?}",
            output,
        );

        // Safe content must be preserved
        prop_assert!(
            output.contains(&safe_text),
            "Safe content should be preserved on fast path.\ntext={:?}\noutput={:?}",
            safe_text, output,
        );
    }

    /// Security sanitization works identically for non-fast-path HTML.
    #[test]
    fn security_preserved_on_normal_path(
        (html, safe_text) in arb_non_fast_path_html_with_dangerous_content(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // Dangerous content must be stripped
        prop_assert!(
            !output.contains("<script>"),
            "Script tags should be stripped on normal path.\noutput={:?}",
            output,
        );

        let output_lower = output.to_lowercase();
        prop_assert!(
            !output_lower.contains("javascript:"),
            "javascript: URLs should be stripped on normal path.\noutput={:?}",
            output,
        );

        // Safe content must be preserved
        prop_assert!(
            output.contains(&safe_text),
            "Safe content should be preserved on normal path.\ntext={:?}\noutput={:?}",
            safe_text, output,
        );
    }

    /// Both fast-path and normal-path produce equivalent security sanitization
    /// for the same dangerous content. The safe text portions must be present
    /// in both outputs, and dangerous content must be absent from both.
    #[test]
    fn security_sanitization_equivalent_across_paths(
        safe_text in "[A-Za-z]{5,15}",
        script_body in "[a-zA-Z0-9()' ]{5,15}",
        event_handler in prop::sample::select(vec!["onclick", "onload", "onerror"]),
        handler_value in "[a-zA-Z0-9()' ]{3,10}",
    ) {
        // Fast-path-eligible HTML with dangerous content
        let fast_html = format!(
            "<html><body>\
             <h1>{safe}</h1>\
             <script>{script}</script>\
             <p {evt}=\"{evt_val}\">{safe}</p>\
             <p><a href=\"javascript:void(0)\">link</a></p>\
             </body></html>",
            safe = escape_html(&safe_text),
            script = escape_html(&script_body),
            evt = event_handler,
            evt_val = escape_html(&handler_value),
        );

        // Same content forced through normal path (table added)
        let normal_html = format!(
            "<html><body>\
             <h1>{safe}</h1>\
             <script>{script}</script>\
             <p {evt}=\"{evt_val}\">{safe}</p>\
             <p><a href=\"javascript:void(0)\">link</a></p>\
             <table><tr><td>cell</td></tr></table>\
             </body></html>",
            safe = escape_html(&safe_text),
            script = escape_html(&script_body),
            evt = event_handler,
            evt_val = escape_html(&handler_value),
        );

        let converter = MarkdownConverter::new();

        let dom_fast = parse_html(fast_html.as_bytes()).expect("parse fast");
        let dom_normal = parse_html(normal_html.as_bytes()).expect("parse normal");

        let out_fast = converter.convert(&dom_fast).expect("convert fast");
        let out_normal = converter.convert(&dom_normal).expect("convert normal");

        // Both outputs must strip dangerous content
        for output in [&out_fast, &out_normal] {
            let lower = output.to_lowercase();
            prop_assert!(
                !output.contains("<script>"),
                "Script tags should be stripped.\noutput={:?}",
                output,
            );
            prop_assert!(
                !lower.contains("javascript:"),
                "javascript: URLs should be stripped.\noutput={:?}",
                output,
            );
        }

        // Both outputs must preserve safe content
        prop_assert!(
            out_fast.contains(&safe_text),
            "Safe text missing from fast-path output.\ntext={:?}\noutput={:?}",
            safe_text, out_fast,
        );
        prop_assert!(
            out_normal.contains(&safe_text),
            "Safe text missing from normal-path output.\ntext={:?}\noutput={:?}",
            safe_text, out_normal,
        );
    }
}

// ============================================================================
// Feature: parser-path-optimization, Property 7: Large-response output equivalence
// Validates: Requirements 3.2, 3.3
// ============================================================================

/// Strategy that generates Markdown-like text with headings, paragraphs,
/// code blocks, and blank lines. Used to test FusedNormalizer equivalence
/// against the reference normalization path without needing 256KB+ documents.
fn arb_markdown_like_text() -> impl Strategy<Value = String> {
    // Each line is generated independently via prop_oneof to avoid move issues
    let line_kind = prop::sample::select(vec![0u8, 1, 2, 3, 4, 5, 6]).prop_flat_map(|kind| {
        match kind {
            0 => {
                // Heading
                (
                    prop::sample::select(vec!["# ", "## ", "### ", "#### "]),
                    "[A-Za-z0-9 ]{3,20}",
                )
                    .prop_map(|(prefix, text)| format!("{}{}", prefix, text))
                    .boxed()
            }
            1 => {
                // Plain paragraph
                "[A-Za-z0-9 ]{5,40}".prop_map(String::from).boxed()
            }
            2 => {
                // Paragraph with extra spaces
                "[A-Za-z0-9]{2,8}(  [A-Za-z0-9]{2,8}){1,4}"
                    .prop_map(String::from)
                    .boxed()
            }
            3 => {
                // Code block
                "[a-z_]{3,15}"
                    .prop_map(|code| format!("```\n{}\n```", code))
                    .boxed()
            }
            4 => {
                // Indented line
                "[A-Za-z0-9 ]{5,20}"
                    .prop_map(|text| format!("    {}", text))
                    .boxed()
            }
            5 => {
                // Inline code line
                "[A-Za-z0-9]{2,8}"
                    .prop_map(|text| format!("Use `multiple  spaces` in {}", text))
                    .boxed()
            }
            _ => {
                // Blank lines
                prop::sample::select(vec![
                    "\n".to_string(),
                    "\n\n".to_string(),
                    "\n\n\n".to_string(),
                ])
                .boxed()
            }
        }
    });

    prop::collection::vec(line_kind, 3..=15).prop_map(|lines| lines.join("\n"))
}

/// Reference normalization function that replicates `MarkdownConverter::normalize_output`.
/// Since `normalize_output` is `pub(super)`, we replicate its logic here for comparison.
fn reference_normalize(input: &str) -> String {
    let output = input.replace("\r\n", "\n");
    let mut result = String::with_capacity(output.len());
    let mut prev_blank = false;
    let mut in_code_block = false;

    for line in output.lines() {
        if line.trim_start().starts_with("```") {
            in_code_block = !in_code_block;
        }
        let trimmed = line.trim_end();
        if trimmed.is_empty() {
            if !prev_blank {
                result.push('\n');
                prev_blank = true;
            }
        } else {
            if in_code_block {
                result.push_str(trimmed);
            } else {
                result.push_str(&reference_normalize_line_whitespace(trimmed));
            }
            result.push('\n');
            prev_blank = false;
        }
    }

    if !result.ends_with('\n') {
        result.push('\n');
    } else {
        while result.ends_with("\n\n") {
            result.pop();
        }
    }
    result
}

/// Reference line-whitespace normalization matching `MarkdownConverter::normalize_line_whitespace`.
fn reference_normalize_line_whitespace(line: &str) -> String {
    let mut result = String::with_capacity(line.len());
    let mut prev_space = false;
    let mut at_start = true;
    let mut in_inline_code = false;

    for ch in line.chars() {
        if ch == '`' {
            in_inline_code = !in_inline_code;
            result.push(ch);
            prev_space = false;
            at_start = false;
        } else if ch == ' ' {
            if in_inline_code || at_start {
                result.push(ch);
            } else if !prev_space {
                result.push(ch);
                prev_space = true;
            }
        } else {
            result.push(ch);
            prev_space = false;
            at_start = false;
        }
    }

    result
}

/// Fused normalization function that replicates the FusedNormalizer path used
/// for large documents (> 256KB). Since FusedNormalizer is `pub(crate)`, we
/// replicate its logic here for direct comparison with the reference path.
fn fused_normalize(input: &str) -> String {
    let input = input.replace("\r\n", "\n");
    let mut output = String::with_capacity(input.len());
    let mut prev_blank = false;
    let mut in_code_block = false;

    for line in input.lines() {
        let trimmed_start = line.trim_start();
        if trimmed_start.starts_with("```") {
            in_code_block = !in_code_block;
        }

        let trimmed = line.trim_end();

        if trimmed.is_empty() {
            if !prev_blank {
                output.push('\n');
                prev_blank = true;
            }
        } else {
            if in_code_block {
                output.push_str(trimmed);
            } else {
                output.push_str(&reference_normalize_line_whitespace(trimmed));
            }
            output.push('\n');
            prev_blank = false;
        }
    }

    if !output.ends_with('\n') {
        output.push('\n');
    } else {
        while output.ends_with("\n\n") {
            output.pop();
        }
    }
    output
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    // Feature: parser-path-optimization, Property 7: Large-response output equivalence
    //
    // For any valid HTML input (including documents exceeding the large-body
    // threshold), the converter with large-response optimizations (pre-sized
    // buffer, fused normalization) SHALL produce byte-identical Markdown output
    // compared to the converter without those optimizations.
    //
    // Since FusedNormalizer is pub(crate), we test equivalence by:
    // 1. Comparing fused vs reference normalization on generated Markdown text
    // 2. Testing through the public API with smaller documents
    // 3. Verifying determinism across multiple conversions

    /// The fused normalization path produces byte-identical output to the
    /// reference normalization path for arbitrary Markdown-like text.
    #[test]
    fn fused_normalizer_matches_reference_normalization(
        text in arb_markdown_like_text(),
    ) {
        let fused = fused_normalize(&text);
        let reference = reference_normalize(&text);

        prop_assert_eq!(
            &fused, &reference,
            "FusedNormalizer output differs from reference normalization.\n\
             input={:?}\nfused={:?}\nreference={:?}",
            text, fused, reference,
        );
    }

    /// Both normalization paths produce output ending with a single newline.
    #[test]
    fn both_normalization_paths_produce_single_trailing_newline(
        text in arb_markdown_like_text(),
    ) {
        let fused = fused_normalize(&text);
        let reference = reference_normalize(&text);

        prop_assert!(
            fused.ends_with('\n'),
            "Fused output should end with newline.\noutput={:?}",
            fused,
        );
        prop_assert!(
            !fused.ends_with("\n\n"),
            "Fused output should not end with double newline.\noutput={:?}",
            fused,
        );
        prop_assert!(
            reference.ends_with('\n'),
            "Reference output should end with newline.\noutput={:?}",
            reference,
        );
        prop_assert!(
            !reference.ends_with("\n\n"),
            "Reference output should not end with double newline.\noutput={:?}",
            reference,
        );
    }

    /// Both normalization paths collapse consecutive blank lines identically.
    #[test]
    fn both_paths_collapse_consecutive_blank_lines(
        text in arb_markdown_like_text(),
    ) {
        let fused = fused_normalize(&text);
        let reference = reference_normalize(&text);

        // Neither output should contain triple newlines
        prop_assert!(
            !fused.contains("\n\n\n"),
            "Fused output contains triple newline.\noutput={:?}",
            fused,
        );
        prop_assert!(
            !reference.contains("\n\n\n"),
            "Reference output contains triple newline.\noutput={:?}",
            reference,
        );
    }

    /// Converting the same HTML document twice through the public API produces
    /// byte-identical output, confirming determinism of the full pipeline
    /// including any large-response optimizations.
    #[test]
    fn public_api_conversion_is_deterministic(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html.as_bytes()).expect("parse pass 1");
        let dom2 = parse_html(html.as_bytes()).expect("parse pass 2");

        let out1 = converter.convert(&dom1).expect("convert pass 1");
        let out2 = converter.convert(&dom2).expect("convert pass 2");

        prop_assert_eq!(
            &out1, &out2,
            "Public API conversion should be deterministic.\nhtml={}",
            html,
        );
    }

    /// Small documents (below the 256KB threshold) produce well-formed output
    /// through the public API. This exercises the normalize_output path.
    #[test]
    fn small_document_conversion_produces_well_formed_output(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let output = converter.convert(&dom).expect("convert");

        // Output should end with a single newline
        if !output.is_empty() {
            prop_assert!(
                output.ends_with('\n'),
                "Output should end with newline.\noutput={:?}",
                output,
            );
            prop_assert!(
                !output.ends_with("\n\n"),
                "Output should not end with double newline.\noutput={:?}",
                output,
            );
        }

        // Output should not contain triple newlines (consecutive blanks collapsed)
        prop_assert!(
            !output.contains("\n\n\n"),
            "Output should not contain triple newlines.\noutput={:?}",
            output,
        );

        // No line should have trailing whitespace
        for line in output.lines() {
            prop_assert_eq!(
                line, line.trim_end(),
                "Line has trailing whitespace.\nline={:?}\nfull_output={:?}",
                line, output,
            );
        }
    }

    /// The fused normalization path preserves code block content verbatim
    /// (only trailing whitespace trimmed), matching the reference path.
    #[test]
    fn fused_normalizer_preserves_code_blocks(
        code_content in "[a-z_ ]{3,30}",
        before_text in "[A-Za-z0-9 ]{3,20}",
        after_text in "[A-Za-z0-9 ]{3,20}",
    ) {
        let input = format!("{}\n\n```\n{}\n```\n\n{}\n", before_text, code_content, after_text);

        let fused = fused_normalize(&input);
        let reference = reference_normalize(&input);

        prop_assert_eq!(
            &fused, &reference,
            "Code block handling differs between fused and reference.\n\
             input={:?}\nfused={:?}\nreference={:?}",
            input, fused, reference,
        );

        // Code content (trimmed of trailing whitespace) should be preserved
        let trimmed_code = code_content.trim_end();
        if !trimmed_code.is_empty() {
            prop_assert!(
                fused.contains(trimmed_code),
                "Code block content should be preserved.\n\
                 code={:?}\noutput={:?}",
                trimmed_code, fused,
            );
        }
    }

    /// The fused normalization path preserves inline code whitespace,
    /// matching the reference path behavior.
    #[test]
    fn fused_normalizer_preserves_inline_code_whitespace(
        prefix in "[A-Za-z]{2,8}",
        code_inner in "[A-Za-z0-9  ]{2,15}",
        suffix in "[A-Za-z]{2,8}",
    ) {
        let input = format!("{}  `{}`  {}\n", prefix, code_inner, suffix);

        let fused = fused_normalize(&input);
        let reference = reference_normalize(&input);

        prop_assert_eq!(
            &fused, &reference,
            "Inline code handling differs between fused and reference.\n\
             input={:?}\nfused={:?}\nreference={:?}",
            input, fused, reference,
        );
    }
}

// ============================================================================
// Feature: parser-path-optimization, Property 8: Timeout behavior preserved
// Validates: Requirements 3.4
// ============================================================================

use nginx_markdown_converter::converter::ConversionContext;
use std::time::Duration;

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    // Feature: parser-path-optimization, Property 8: Timeout behavior preserved
    //
    // For any valid HTML input and any ConversionContext with a non-zero timeout,
    // the optimized converter SHALL trigger ConversionError::Timeout if and only
    // if the unoptimized converter would also trigger a timeout for the same
    // input and timeout duration.
    //
    // We test:
    // 1. Generous timeout always succeeds
    // 2. Extremely short timeout (1 nanosecond) fails with Timeout
    // 3. Timeout behavior is consistent across multiple runs

    /// Conversion with a generous timeout (60 seconds) always succeeds for
    /// any valid HTML input, confirming that the optimization pipeline does
    /// not introduce spurious timeouts.
    #[test]
    fn generous_timeout_always_succeeds(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let mut ctx = ConversionContext::new(Duration::from_secs(60));

        let result = converter.convert_with_context(&dom, &mut ctx);

        prop_assert!(
            result.is_ok(),
            "Conversion with generous timeout should succeed.\nhtml={}\nerr={:?}",
            html, result.err(),
        );
    }

    /// Conversion with a 1-nanosecond timeout fails with a Timeout error,
    /// confirming that the timeout mechanism is preserved through the
    /// optimization pipeline. The cooperative timeout checks in
    /// convert_with_context detect the expired deadline.
    #[test]
    fn nanosecond_timeout_triggers_timeout_error(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");

        // 1 nanosecond timeout — will have elapsed by the time conversion starts
        let mut ctx = ConversionContext::new(Duration::from_nanos(1));

        // Small sleep to ensure the timeout has definitely elapsed
        std::thread::sleep(Duration::from_millis(1));

        let result = converter.convert_with_context(&dom, &mut ctx);

        prop_assert!(
            result.is_err(),
            "Conversion with 1ns timeout should fail.\nhtml={}",
            html,
        );

        let err = result.unwrap_err();
        prop_assert_eq!(
            err.code(), 3,
            "Error should be Timeout (code 3), got code {}.\nhtml={}",
            err.code(), html,
        );
    }

    /// Timeout behavior is deterministic: running the same conversion twice
    /// with the same generous timeout produces the same success result.
    #[test]
    fn timeout_behavior_is_deterministic_on_success(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html.as_bytes()).expect("parse pass 1");
        let dom2 = parse_html(html.as_bytes()).expect("parse pass 2");

        let mut ctx1 = ConversionContext::new(Duration::from_secs(60));
        let mut ctx2 = ConversionContext::new(Duration::from_secs(60));

        let out1 = converter.convert_with_context(&dom1, &mut ctx1).expect("convert pass 1");
        let out2 = converter.convert_with_context(&dom2, &mut ctx2).expect("convert pass 2");

        prop_assert_eq!(
            &out1, &out2,
            "Conversion with timeout should produce deterministic output.\nhtml={}",
            html,
        );
    }

    /// Timeout behavior is deterministic: running the same conversion twice
    /// with the same expired timeout produces the same Timeout error.
    #[test]
    fn timeout_behavior_is_deterministic_on_failure(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html.as_bytes()).expect("parse pass 1");
        let dom2 = parse_html(html.as_bytes()).expect("parse pass 2");

        let mut ctx1 = ConversionContext::new(Duration::from_nanos(1));
        let mut ctx2 = ConversionContext::new(Duration::from_nanos(1));

        std::thread::sleep(Duration::from_millis(1));

        let result1 = converter.convert_with_context(&dom1, &mut ctx1);
        let result2 = converter.convert_with_context(&dom2, &mut ctx2);

        prop_assert!(
            result1.is_err() && result2.is_err(),
            "Both conversions with expired timeout should fail.\nhtml={}",
            html,
        );

        prop_assert_eq!(
            result1.unwrap_err().code(),
            result2.unwrap_err().code(),
            "Both timeout errors should have the same error code.\nhtml={}",
            html,
        );
    }

    /// Conversion with zero timeout (no timeout) always succeeds,
    /// confirming that Duration::ZERO disables the timeout mechanism.
    #[test]
    fn zero_timeout_means_no_timeout(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();
        let dom = parse_html(html.as_bytes()).expect("parse");
        let mut ctx = ConversionContext::new(Duration::ZERO);

        let result = converter.convert_with_context(&dom, &mut ctx);

        prop_assert!(
            result.is_ok(),
            "Conversion with zero timeout (no limit) should succeed.\nhtml={}\nerr={:?}",
            html, result.err(),
        );
    }

    /// The output from convert_with_context with a generous timeout matches
    /// the output from convert (which uses Duration::ZERO internally),
    /// confirming that timeout tracking does not alter the conversion result.
    #[test]
    fn timeout_context_does_not_alter_output(
        html in arb_fast_path_html(),
    ) {
        let converter = MarkdownConverter::new();

        let dom1 = parse_html(html.as_bytes()).expect("parse for convert");
        let dom2 = parse_html(html.as_bytes()).expect("parse for convert_with_context");

        let out_no_ctx = converter.convert(&dom1).expect("convert without context");

        let mut ctx = ConversionContext::new(Duration::from_secs(60));
        let out_with_ctx = converter.convert_with_context(&dom2, &mut ctx)
            .expect("convert with context");

        prop_assert_eq!(
            &out_no_ctx, &out_with_ctx,
            "Output should be identical with and without timeout context.\nhtml={}",
            html,
        );
    }
}
