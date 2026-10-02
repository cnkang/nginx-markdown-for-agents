//! Regression tests for the HTML-to-Markdown converter.
//!
//! Each test in this module addresses a specific bug or edge case that was
//! previously mishandled. They serve as permanent guards against regressions
//! in inline code fencing, whitespace handling, link formatting, and nested
//! list structure.
//!
//! When adding a new regression test, include a comment referencing the
//! original issue or commit that introduced the fix.

use nginx_markdown_converter::converter::{ConversionOptions, MarkdownConverter, MarkdownFlavor};
use nginx_markdown_converter::parser::parse_html;

fn convert_html(html: &[u8]) -> String {
    convert_html_with_options(html, ConversionOptions::default())
}

fn convert_html_with_options(html: &[u8], options: ConversionOptions) -> String {
    let dom = parse_html(html).expect("Parse failed");
    let converter = MarkdownConverter::with_options(options);
    converter.convert(&dom).expect("Conversion failed")
}

/// Verifies that inline code containing triple backticks is fenced with a longer
/// backtick sequence to avoid breaking the Markdown structure.
#[test]
fn inline_code_should_use_a_fence_longer_than_embedded_backticks() {
    let result = convert_html(b"<p><code>value ``` with ticks</code></p>");

    assert!(result.trim().contains("````value ``` with ticks````"));
}

/// Verifies that inline code gets padding on both sides when its payload
/// begins with a backtick, so the fence cannot be confused with the payload.
#[test]
fn inline_code_starting_with_backtick_is_padded_on_both_sides() {
    let result = convert_html(b"<p><code>`value</code></p>");

    assert!(result.trim().contains("`` `value ``"), "got: {result:?}");
}

/// Verifies that inline code gets padding on both sides when its payload ends
/// with a backtick.
#[test]
fn inline_code_ending_with_backtick_is_padded_on_both_sides() {
    let result = convert_html(b"<p><code>value`</code></p>");

    assert!(result.trim().contains("`` value` ``"), "got: {result:?}");
}

/// Verifies that an all-backtick inline-code payload is also separated from
/// both fences.
#[test]
fn inline_code_made_only_of_backticks_is_padded_on_both_sides() {
    let result = convert_html(b"<p><code>```</code></p>");

    assert!(result.trim().contains("```` ``` ````"), "got: {result:?}");
}

/// Ensures that whitespace-only text nodes between inline elements preserve
/// word separation in the Markdown output instead of being silently dropped.
#[test]
fn whitespace_only_nodes_should_preserve_word_separation() {
    let result = convert_html(b"<p>Hello<span> </span>world</p>");

    assert!(result.contains("Hello world"));
    assert!(!result.contains("Helloworld"));
}

/// Validates that removed children (e.g. `<script>`) inside a link's text are
/// skipped during link text extraction, preventing XSS payloads from appearing
/// in the Markdown link label.
#[test]
fn link_text_extraction_should_skip_removed_children() {
    let result = convert_html(
        b"<p><a href=\"https://example.com\">safe<script>alert(1)</script> text</a></p>",
    );

    assert!(result.contains("[safe text](https://example.com)"));
    assert!(!result.contains("alert"));
}

/// Headings nested inside container elements (`<div>`, `<section>`, `<article>`)
/// must preserve their Markdown level regardless of container nesting depth.
/// Regression guard for semantic fidelity.
#[test]
fn headings_inside_containers_preserve_level() {
    // Heading inside <div>
    let result = convert_html(b"<div><h1>Div Title</h1></div>");
    assert!(
        result.contains("# Div Title"),
        "h1 inside <div> should produce '# ': {result:?}"
    );

    // Heading inside <section>
    let result = convert_html(b"<section><h2>Section Title</h2></section>");
    assert!(
        result.contains("## Section Title"),
        "h2 inside <section> should produce '## ': {result:?}"
    );

    // Heading inside <article>
    let result = convert_html(b"<article><h3>Article Title</h3></article>");
    assert!(
        result.contains("### Article Title"),
        "h3 inside <article> should produce '### ': {result:?}"
    );

    // Deeply nested containers
    let result =
        convert_html(b"<div><section><article><h4>Deep Title</h4></article></section></div>");
    assert!(
        result.contains("#### Deep Title"),
        "h4 inside nested containers should produce '#### ': {result:?}"
    );
}

/// Checks that nested lists do not double-indent pre-rendered children when
/// using GFM flavor, ensuring correct Markdown list nesting.
#[test]
fn nested_lists_should_not_double_indent_pre_rendered_children() {
    let result = convert_html_with_options(
        b"<ul><li>Parent<ul><li>Child</li></ul></li></ul>",
        ConversionOptions {
            flavor: MarkdownFlavor::GitHubFlavoredMarkdown,
            ..Default::default()
        },
    );

    assert!(result.contains("\n  - Child"));
    assert!(!result.contains("\n    - Child"));
}

#[test]
fn link_url_with_angle_brackets_uses_shared_destination_escape() {
    // The full-buffer path must use the same destination representation as the
    // streaming emitter for Markdown-sensitive URL delimiters.
    let result = convert_html(br#"<a href="https://example.com/path?a=1&lt;b=2&gt;c=3">link</a>"#);
    assert!(
        result.contains(r"<https://example.com/path?a=1\<b=2\>c=3>"),
        "expected shared escaped destination wrapping, got: {result}"
    );
}

/// A `<textarea>` nested in an anchor or code block must not leak its prefilled
/// default text.
///
/// The ordinary traversal suppresses form-state child text, but the link-label
/// and code-body extractors are separate walks that only excluded
/// script/style/noscript, so a nested textarea's default became the label or
/// the code body -- which SECURITY_MODEL.md forbids for AI-facing output.
#[test]
fn nested_textarea_defaults_are_suppressed_in_link_and_code_extractors() {
    let in_link = convert_html(
        b"<p><a href=\"/x\"><textarea aria-label=\"Name\">SECRET-PREFILL</textarea></a></p>",
    );
    assert!(
        !in_link.contains("SECRET-PREFILL"),
        "textarea default leaked into a link label: {in_link:?}"
    );
    // The approved descriptive text still comes through.
    assert!(
        in_link.contains("Name"),
        "the approved aria-label description was dropped: {in_link:?}"
    );

    let in_code = convert_html(
        b"<pre><code><textarea placeholder=\"Hint\">SECRET-CODE</textarea></code></pre>",
    );
    assert!(
        !in_code.contains("SECRET-CODE"),
        "textarea default leaked into a code body: {in_code:?}"
    );
}

/// The form-state guard must suppress prefilled STATE without swallowing
/// page-provided CHOICE labels.
///
/// `<option>`, `<optgroup>` and `<datalist>` hold labels the page offers to the
/// reader, which `FORM_ELEMENTS` and SECURITY_MODEL.md both describe as visible
/// content that remains. Treating them as form state made the extractors drop
/// those labels inside an `<a>` or `<pre>` while the ordinary traversal kept
/// them, so the two paths disagreed on the same markup.
#[test]
fn option_labels_survive_the_link_and_code_extractors() {
    let in_link =
        convert_html(b"<p><a href=\"/x\"><select><option>RED CHOICE</option></select></a></p>");
    assert!(
        in_link.contains("RED CHOICE"),
        "an option label is page content, not form state, and must survive: {in_link:?}"
    );

    let in_code =
        convert_html(b"<pre><code><select><option>RED CHOICE</option></select></code></pre>");
    assert!(
        in_code.contains("RED CHOICE"),
        "the same label must survive inside a code body: {in_code:?}"
    );

    // The ordinary traversal is the reference: the two must agree.
    let bare = convert_html(b"<p><select><option>RED CHOICE</option></select></p>");
    assert!(bare.contains("RED CHOICE"), "{bare:?}");
}

/// A textarea outside those containers keeps the existing suppression, so the
/// new guard must not change the ordinary path.
#[test]
fn a_bare_textarea_default_is_still_suppressed() {
    let out = convert_html(b"<p><textarea>SECRET-BARE</textarea></p>");
    assert!(
        !out.contains("SECRET-BARE"),
        "the ordinary traversal regressed: {out:?}"
    );
}
