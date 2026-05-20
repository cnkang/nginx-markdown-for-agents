#![no_main]

//! Fuzz target for URL validation with arbitrary/malformed input.
//!
//! Takes arbitrary bytes as input and attempts to validate them as a URL
//! using `validate_link_url`. Verifies no panics occur on any input,
//! including non-UTF-8 sequences and control characters.

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::security::validate_link_url;

fuzz_target!(|data: &[u8]| {
    /* Try interpreting arbitrary bytes as a UTF-8 string for URL validation. */
    if let Ok(url_str) = std::str::from_utf8(data) {
        let _ = validate_link_url(url_str);
    }

    /* Also test lossy conversion to cover more code paths. */
    let lossy = String::from_utf8_lossy(data);
    let _ = validate_link_url(&lossy);
});
