use super::MarkdownConverter;
use crate::parser::parse_html;
use proptest::prelude::*;

fn convert_html_for_test(html: &str) -> String {
    let dom = parse_html(html.as_bytes()).expect("Parse failed");
    MarkdownConverter::new()
        .convert(&dom)
        .expect("Conversion failed")
}

fn encode_entity_char(ch: char, selector: u8) -> String {
    match ch {
        '&' => match selector % 3 {
            0 => "&amp;".to_string(),
            1 => "&#38;".to_string(),
            _ => "&#x26;".to_string(),
        },
        '<' => match selector % 3 {
            0 => "&lt;".to_string(),
            1 => "&#60;".to_string(),
            _ => "&#x3C;".to_string(),
        },
        '>' => match selector % 3 {
            0 => "&gt;".to_string(),
            1 => "&#62;".to_string(),
            _ => "&#x3E;".to_string(),
        },
        '"' => match selector % 3 {
            0 => "&quot;".to_string(),
            1 => "&#34;".to_string(),
            _ => "&#x22;".to_string(),
        },
        '\'' => match selector % 2 {
            0 => "&#39;".to_string(),
            _ => "&#x27;".to_string(),
        },
        'A' => match selector % 3 {
            0 => "A".to_string(),
            1 => "&#65;".to_string(),
            _ => "&#x41;".to_string(),
        },
        '€' => match selector % 2 {
            0 => "&#8364;".to_string(),
            _ => "&#x20AC;".to_string(),
        },
        '中' => match selector % 2 {
            0 => "&#20013;".to_string(),
            _ => "&#x4E2D;".to_string(),
        },
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
        prop_assert!(
            markdown.contains(&expected),
            "Decoded entities should preserve source text.\nInput: {:?}\nMarkdown: {:?}",
            encoded,
            markdown
        );
    }
}
