#![no_main]

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::parser::parse_html_with_charset;

fuzz_target!(|data: &[u8]| {
    let _ = parse_html_with_charset(data, None);
    let _ = parse_html_with_charset(data, Some("text/html; charset=UTF-8"));
    let _ = parse_html_with_charset(data, Some("text/html; charset=windows-1252"));
});
