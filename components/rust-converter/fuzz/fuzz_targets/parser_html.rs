#![no_main]

//! Fuzz HTML parsing and charset dispatch.
//!
//! Each input is parsed with no charset hint, an explicit UTF-8 hint, and a
//! Windows-1252 hint so parser and decoder error paths stay panic-free across
//! common production charset-selection branches.

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::parser::parse_html_with_charset;

fuzz_target!(|data: &[u8]| {
    let _ = parse_html_with_charset(data, None);
    let _ = parse_html_with_charset(data, Some("text/html; charset=UTF-8"));
    let _ = parse_html_with_charset(data, Some("text/html; charset=windows-1252"));
});
