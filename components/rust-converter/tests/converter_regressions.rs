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

#[test]
fn inline_code_should_use_a_fence_longer_than_embedded_backticks() {
    let result = convert_html(b"<p><code>value ``` with ticks</code></p>");

    assert!(result.trim().contains("````value ``` with ticks````"));
}

#[test]
fn whitespace_only_nodes_should_preserve_word_separation() {
    let result = convert_html(b"<p>Hello<span> </span>world</p>");

    assert!(result.contains("Hello world"));
    assert!(!result.contains("Helloworld"));
}

#[test]
fn link_text_extraction_should_skip_removed_children() {
    let result = convert_html(
        b"<p><a href=\"https://example.com\">safe<script>alert(1)</script> text</a></p>",
    );

    assert!(result.contains("[safe text](https://example.com)"));
    assert!(!result.contains("alert"));
}

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
