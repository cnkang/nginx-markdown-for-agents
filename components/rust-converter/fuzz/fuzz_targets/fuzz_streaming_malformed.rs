#![no_main]

//! Fuzz malformed streaming input and teardown.
//!
//! Random bytes are fed in repeated small chunks so the converter exercises
//! cross-boundary tokenization, error transitions, and `finalize()` cleanup
//! after early feed errors without panicking.

use libfuzzer_sys::fuzz_target;
#[allow(dead_code)]
mod streaming_utils;

use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::streaming::budget::MemoryBudget;
use nginx_markdown_converter::streaming::converter::StreamingConverter;

// Feed random bytes as multiple small chunks to stress cross-boundary handling.
fuzz_target!(|data: &[u8]| {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

    let split_sizes = streaming_utils::split_sizes_from_seed(data, data.len());
    let mut cursor = 0usize;

    // Feed seeded variable-sized chunks to exercise multi-boundary handling.
    // Continue on error so finalize() always runs, exercising teardown
    // and error-path behaviour.
    for split in split_sizes {
        if cursor >= data.len() {
            break;
        }
        let end = cursor.saturating_add(split.max(1)).min(data.len());
        if conv.feed_chunk(&data[cursor..end]).is_err() {
            break;
        }
        cursor = end;
    }
    let _ = conv.finalize();
});
