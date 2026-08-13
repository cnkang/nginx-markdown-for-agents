//! Property-based tests for output determinism (Property 25).
//!
//! **Validates: Requirements 13.4**
//!
//! For pairs of conversions with identical effective inputs, verify
//! byte-identical Markdown output.

use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;
use proptest::prelude::*;

/// Build a converter with fixed options so the effective input tuple is
/// identical across runs.
fn converter() -> MarkdownConverter {
    MarkdownConverter::default()
}

fn arb_html() -> impl Strategy<Value = String> {
    prop_oneof![
        Just("<html><body><h1>Title</h1><p>Paragraph</p></body></html>".to_string()),
        Just("<html><body><ul><li>one</li><li>two</li></ul></body></html>".to_string()),
        Just(
            "<html><head><title>t</title></head><body><a href=\"/x\">link</a></body></html>"
                .to_string()
        ),
        Just("<html><body><p>line1</p>\n<p>line2</p></body></html>".to_string()),
        Just("<html><body><blockquote><p>quoted</p></blockquote></body></html>".to_string()),
        Just("<html><body><pre><code>fn main() {}</code></pre></body></html>".to_string()),
        Just("<html><body><img src=\"/img.png\" alt=\"alt text\"></body></html>".to_string()),
        Just("<html><body><table><tr><td>a</td><td>b</td></tr></table></body></html>".to_string()),
        Just(
            "<html><body>plain text with <em>emphasis</em> and <strong>bold</strong></body></html>"
                .to_string()
        ),
        Just("<html><body><!-- comment --><p>after comment</p></body></html>".to_string()),
    ]
}

fn convert(html: &str) -> String {
    let dom = parse_html(html.as_bytes()).expect("fixture HTML must parse");
    let conv = converter();
    conv.convert(&dom).expect("fixture HTML must convert")
}

proptest! {
    /// Identical effective inputs produce byte-identical output across
    /// repeated conversions in fresh converter instances.
    #[test]
    fn p25_identical_inputs_byte_identical(html in arb_html()) {
        let first = convert(&html);
        let second = convert(&html);
        let third = convert(&html);
        assert_eq!(first, second);
        assert_eq!(second, third);
    }

    /// Different effective inputs are allowed to produce different output
    /// (the determinism contract does not promise cross-input stability).
    #[test]
    fn p25_different_inputs_do_not_share_state(
        html_a in arb_html(),
        html_b in arb_html(),
    ) {
        let dom_a = parse_html(html_a.as_bytes()).expect("fixture HTML must parse");
        let dom_b = parse_html(html_b.as_bytes()).expect("fixture HTML must parse");
        let conv = converter();
        let out_a = conv.convert(&dom_a).expect("fixture HTML must convert");
        let _out_b = conv.convert(&dom_b).expect("fixture HTML must convert");
        /* Converting A again on the SAME converter instance (after B) must
         * not change A's output — the converter carries no cross-input state. */
        let out_a_again = conv.convert(&dom_a).expect("fixture HTML must convert");
        assert_eq!(out_a, out_a_again);
    }
}

/// Determinism holds for large documents: 200 conversions of the same
/// document must all agree.
#[test]
fn p25_repeated_large_conversion_is_stable() {
    let html = format!(
        "<html><body>{}</body></html>",
        (0..200)
            .map(|i| format!("<p>paragraph {i} with <em>emphasis</em></p>"))
            .collect::<String>()
    );
    let expected = convert(&html);
    for _ in 0..200 {
        assert_eq!(convert(&html), expected);
    }
}
