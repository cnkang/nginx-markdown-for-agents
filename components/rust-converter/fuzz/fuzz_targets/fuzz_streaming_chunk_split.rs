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

    let single_res = convert(html, &[html.len().max(1)]);
    let chunked_res = convert(html, &split_sizes);

    assert_eq!(
        single_res.is_err(),
        chunked_res.is_err(),
        "Chunk split changed success/error parity: split_sizes={split_sizes:?}, html={html:?}"
    );

    if let (Ok(single), Ok(chunked)) = (single_res, chunked_res)
        && single != chunked
    {
        assert_eq!(
            normalize_ascii_whitespace(&single),
            normalize_ascii_whitespace(&chunked),
            "Chunk split changed output beyond whitespace drift"
        );
    }
});
