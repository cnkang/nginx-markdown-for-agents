use super::MarkdownConverter;
use crate::parser::parse_html;
use crate::security::escape_markdown_text;
use proptest::prelude::*;

fn convert_html_for_test(html: &str) -> String {
    let dom = parse_html(html.as_bytes()).expect("Parse failed");
    MarkdownConverter::new()
        .convert(&dom)
        .expect("Conversion failed")
}

fn select_entity(selector: u8, options: &[&str]) -> String {
    options[(selector as usize) % options.len()].to_string()
}

fn encode_entity_char(ch: char, selector: u8) -> String {
    match ch {
        '&' => select_entity(selector, &["&amp;", "&#38;", "&#x26;"]),
        '<' => select_entity(selector, &["&lt;", "&#60;", "&#x3C;"]),
        '>' => select_entity(selector, &["&gt;", "&#62;", "&#x3E;"]),
        '"' => select_entity(selector, &["&quot;", "&#34;", "&#x22;"]),
        '\'' => select_entity(selector, &["&#39;", "&#x27;"]),
        'A' => select_entity(selector, &["A", "&#65;", "&#x41;"]),
        '€' => select_entity(selector, &["&#8364;", "&#x20AC;"]),
        '中' => select_entity(selector, &["&#20013;", "&#x4E2D;"]),
        _ => ch.to_string(),
    }
}

proptest! {
    #[test]
    fn prop_html_entities_decode_to_expected_text(
        symbols in prop::collection::vec((0usize..8usize, any::<u8>()), 1..40),
    ) {
        let alphabet = ['&', '<', '>', '"', '\'', 'A', '€', '中'];
        let mut encoded = String::new();
        let mut expected = String::new();

        for (index, selector) in symbols {
            let character = alphabet[index];
            encoded.push_str(&encode_entity_char(character, selector));
            expected.push(character);
        }

        let markdown = convert_html_for_test(&format!("<p>{}</p>", encoded));
        let expected_markdown = escape_markdown_text(&expected);
        prop_assert!(
            markdown.contains(&expected_markdown),
            "Decoded entities should preserve source text.\nInput: {:?}\nMarkdown: {:?}",
            encoded,
            markdown
        );
    }
}
