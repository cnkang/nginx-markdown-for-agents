#![no_main]
use libfuzzer_sys::fuzz_target;
mod streaming_utils;

use streaming_utils::{convert, normalize_ascii_whitespace, split_sizes_from_seed};

// Fuzz input: first byte and body bytes derive chunk sequence and HTML.
fuzz_target!(|data: &[u8]| {
    if data.len() < 2 {
        return;
    }
    let html = &data[1..];
    let split_sizes = split_sizes_from_seed(data, html.len());

    // Single-chunk conversion
    let single = match convert(html, &[html.len().max(1)]) {
        Some(single) => single,
        None => return,
    };

    // Chunked conversion
    let chunked = match convert(html, &split_sizes) {
        Some(chunked) => chunked,
        None => return,
    };

    if single != chunked {
        assert_eq!(
            normalize_ascii_whitespace(&single),
            normalize_ascii_whitespace(&chunked),
            "Chunk split changed output beyond whitespace drift"
        );
    }
});
