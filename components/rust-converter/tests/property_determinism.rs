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

/// HTML documents for the determinism properties.
///
/// Half the shapes are fixed literals covering constructs whose output is
/// sensitive to converter state (tables, comments, pre/code, images); the rest
/// are generated from structured fragments so the property explores text,
/// nesting, and attribute variation instead of replaying the same ten strings.
///
/// The generated arm stays bounded on purpose: at most three content blocks,
/// each a short token run, so the number of distinct documents grows
/// combinatorially while every case stays small enough to convert thousands of
/// times in a test run.
fn arb_html() -> impl Strategy<Value = String> {
    let block = prop_oneof![
        /* text paragraph */
        prop::collection::vec("[A-Za-z0-9]{1,6}", 1..=3)
            .prop_map(|words| format!("<p>{}</p>", words.join(" "))),
        /* heading at a random level */
        (
            prop::sample::select(vec!["h1", "h2", "h3"]),
            "[A-Za-z0-9]{1,6}",
        )
            .prop_map(|(tag, text)| format!("<{tag}>{text}</{tag}>")),
        /* list with one to three items */
        prop::collection::vec("[A-Za-z0-9]{1,6}", 1..=3).prop_map(|items| format!(
            "<ul>{}</ul>",
            items
                .iter()
                .map(|item| format!("<li>{item}</li>"))
                .collect::<String>()
        )),
        /* blockquote nesting a paragraph */
        "[A-Za-z0-9]{1,6}".prop_map(|text| format!("<blockquote><p>{text}</p></blockquote>")),
        /* inline emphasis and strong inside a paragraph */
        ("[A-Za-z0-9]{1,6}", "[A-Za-z0-9]{1,6}")
            .prop_map(|(a, b)| format!("<p>plain <em>{a}</em> and <strong>{b}</strong></p>")),
        /* a link with a relative href and a fixed label */
        "[A-Za-z0-9]{1,6}".prop_map(|slug| format!("<a href=\"/{slug}\">link</a>")),
        /* image with a fixed source and generated alt text */
        "[A-Za-z0-9]{1,6}".prop_map(|alt| format!("<img src=\"/img.png\" alt=\"{alt}\">")),
        /* a small table with fixed cells */
        ("[A-Za-z0-9]{1,4}", "[A-Za-z0-9]{1,4}")
            .prop_map(|(a, b)| format!("<table><tr><td>{a}</td><td>{b}</td></tr></table>")),
    ];

    prop_oneof![
        1 => prop::collection::vec(block.clone(), 1..=3).prop_map(|blocks| {
            format!("<html><body>{}</body></html>", blocks.concat())
        }),
        1 => Just("<html><body><h1>Title</h1><p>Paragraph</p></body></html>".to_string()),
        1 => Just("<html><body><ul><li>one</li><li>two</li></ul></body></html>".to_string()),
        1 => Just(
            "<html><head><title>t</title></head><body><a href=\"/x\">link</a></body></html>"
                .to_string()
        ),
        1 => Just("<html><body><p>line1</p>\n<p>line2</p></body></html>".to_string()),
        1 => Just("<html><body><blockquote><p>quoted</p></blockquote></body></html>".to_string()),
        1 => Just("<html><body><pre><code>fn main() {}</code></pre></body></html>".to_string()),
        1 => Just("<html><body><img src=\"/img.png\" alt=\"alt text\"></body></html>".to_string()),
        1 => Just("<html><body><table><tr><td>a</td><td>b</td></tr></table></body></html>".to_string()),
        1 => Just(
            "<html><body>plain text with <em>emphasis</em> and <strong>bold</strong></body></html>"
                .to_string()
        ),
        1 => Just("<html><body><!-- comment --><p>after comment</p></body></html>".to_string()),
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
