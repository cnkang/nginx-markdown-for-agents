#![no_main]

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::streaming::budget::MemoryBudget;
use nginx_markdown_converter::streaming::converter::StreamingConverter;

fuzz_target!(|data: &[u8]| {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

    let _ = conv.feed_chunk(data);
    let _ = conv.finalize();
});
