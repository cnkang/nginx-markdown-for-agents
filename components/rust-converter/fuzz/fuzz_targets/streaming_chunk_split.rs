#![no_main]

use libfuzzer_sys::fuzz_target;
mod streaming_utils;

use streaming_utils::{convert, normalize_ascii_whitespace, split_sizes_from_seed};

fuzz_target!(|data: &[u8]| {
    if data.len() < 2 {
        return;
    }

    let html = &data[1..];
    let split_sizes = split_sizes_from_seed(data, html.len());

    let single = match convert(html, &[html.len().max(1)]) {
        Some(single) => single,
        None => return,
    };

    let split = match convert(html, &split_sizes) {
        Some(split) => split,
        None => return,
    };

    if single != split {
        assert_eq!(
            normalize_ascii_whitespace(&single),
            normalize_ascii_whitespace(&split),
            "streaming chunk split changed output beyond whitespace drift"
        );
    }
});
