use nginx_markdown_converter::converter::MarkdownConverter;
use nginx_markdown_converter::parser::parse_html;
use std::env;
use std::fs;
use std::process;

/// Exit code indicating the input was ineligible for conversion (skipped).
///
/// This is returned when the input file does not appear to contain HTML
/// content (no recognizable HTML tags found), making it ineligible for
/// HTML-to-Markdown conversion.
const EXIT_SKIPPED: i32 = 2;

/// Minimum set of tag prefixes that indicate the file contains HTML.
/// Case-insensitive check against the raw bytes.
const HTML_TAG_MARKERS: &[&[u8]] = &[
    b"<html",
    b"<head",
    b"<body",
    b"<div",
    b"<p>",
    b"<p ",
    b"<h1",
    b"<h2",
    b"<h3",
    b"<span",
    b"<table",
    b"<script",
    b"<style",
    b"<!doctype",
];

/// Check whether the raw bytes contain at least one recognizable HTML tag.
fn looks_like_html(content: &[u8]) -> bool {
    let lower: Vec<u8> = content.iter().map(|b| b.to_ascii_lowercase()).collect();
    HTML_TAG_MARKERS
        .iter()
        .any(|marker| lower.windows(marker.len()).any(|w| w == *marker))
}

fn main() {
    let args: Vec<String> = env::args().collect();

    if args.len() < 2 {
        eprintln!("Usage: {} <html_file>", args[0]);
        process::exit(1);
    }

    let filename = &args[1];

    let html = match fs::read(filename) {
        Ok(content) => content,
        Err(e) => {
            eprintln!("Error reading file {}: {}", filename, e);
            process::exit(1);
        }
    };

    // Auto-detect ineligible input: if the file contains no recognizable
    // HTML tags, exit with code 2 (skipped) rather than attempting conversion.
    if !looks_like_html(&html) {
        eprintln!("Skipped: no HTML tags detected in {}", filename);
        process::exit(EXIT_SKIPPED);
    }

    let dom = match parse_html(&html) {
        Ok(dom) => dom,
        Err(e) => {
            eprintln!("Error parsing HTML: {}", e);
            process::exit(1);
        }
    };

    let converter = MarkdownConverter::new();
    let markdown = match converter.convert(&dom) {
        Ok(md) => md,
        Err(e) => {
            eprintln!("Error converting to Markdown: {}", e);
            process::exit(1);
        }
    };

    println!("{}", markdown);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn html_with_doctype_is_detected() {
        assert!(looks_like_html(
            b"<!DOCTYPE html><html><body>hi</body></html>"
        ));
    }

    #[test]
    fn html_with_body_tag_is_detected() {
        assert!(looks_like_html(b"<body><p>hello</p></body>"));
    }

    #[test]
    fn html_with_div_is_detected() {
        assert!(looks_like_html(b"<div class=\"x\">content</div>"));
    }

    #[test]
    fn html_with_heading_is_detected() {
        assert!(looks_like_html(b"<h1>Title</h1>"));
    }

    #[test]
    fn html_with_paragraph_is_detected() {
        assert!(looks_like_html(b"<p>Some text</p>"));
    }

    #[test]
    fn html_with_paragraph_attrs_is_detected() {
        assert!(looks_like_html(b"<p class=\"intro\">Some text</p>"));
    }

    #[test]
    fn html_case_insensitive() {
        assert!(looks_like_html(b"<HTML><BODY><P>hi</P></BODY></HTML>"));
    }

    #[test]
    fn html_mixed_case() {
        assert!(looks_like_html(b"<Html><Body>content</Body></Html>"));
    }

    #[test]
    fn plain_text_is_not_html() {
        assert!(!looks_like_html(b"This is just plain text with no tags."));
    }

    #[test]
    fn json_is_not_html() {
        assert!(!looks_like_html(b"{\"key\": \"value\", \"num\": 42}"));
    }

    #[test]
    fn empty_input_is_not_html() {
        assert!(!looks_like_html(b""));
    }

    #[test]
    fn xml_without_html_tags_is_not_html() {
        assert!(!looks_like_html(
            b"<?xml version=\"1.0\"?><root><item>data</item></root>"
        ));
    }

    #[test]
    fn markdown_is_not_html() {
        assert!(!looks_like_html(
            b"# Title\n\nSome **bold** text and a [link](url)."
        ));
    }

    #[test]
    fn script_tag_is_detected() {
        assert!(looks_like_html(b"<script>alert('hi')</script>"));
    }

    #[test]
    fn style_tag_is_detected() {
        assert!(looks_like_html(b"<style>body { color: red; }</style>"));
    }

    #[test]
    fn table_tag_is_detected() {
        assert!(looks_like_html(b"<table><tr><td>cell</td></tr></table>"));
    }

    #[test]
    fn span_tag_is_detected() {
        assert!(looks_like_html(b"<span>inline</span>"));
    }

    #[test]
    fn head_tag_is_detected() {
        assert!(looks_like_html(b"<head><title>Page</title></head>"));
    }
}
