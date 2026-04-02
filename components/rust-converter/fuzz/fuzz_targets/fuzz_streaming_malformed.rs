#![no_main]
use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::streaming::budget::MemoryBudget;
use nginx_markdown_converter::streaming::converter::StreamingConverter;

/// Feed random bytes as multiple small chunks to stress cross-boundary handling.
fuzz_target!(|data: &[u8]| {
    let mut conv = StreamingConverter::new(
        ConversionOptions::default(),
        MemoryBudget::default(),
    );
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

    // Feed in 64-byte chunks to exercise cross-boundary token handling.
    // Continue on error so finalize() always runs, exercising teardown
    // and error-path behaviour.
    for chunk in data.chunks(64) {
        if conv.feed_chunk(chunk).is_err() {
            break;
        }
    }
    let _ = conv.finalize();
});
