#![no_main]
use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::streaming::budget::MemoryBudget;
use nginx_markdown_converter::streaming::converter::StreamingConverter;

/// Fuzz input: first byte is the split point ratio (0-255), rest is HTML.
fuzz_target!(|data: &[u8]| {
    if data.len() < 2 {
        return;
    }
    let split_ratio = data[0] as usize;
    let html = &data[1..];
    let split_point = (html.len() * split_ratio) / 256;

    // Single-chunk conversion
    let single = {
        let mut conv = StreamingConverter::new(
            ConversionOptions::default(),
            MemoryBudget::default(),
        );
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
        let out = match conv.feed_chunk(html) {
            Ok(o) => o,
            Err(_) => return,
        };
        match conv.finalize() {
            Ok(r) => {
                let mut full = out.markdown;
                full.extend_from_slice(&r.final_markdown);
                full
            }
            Err(_) => return,
        }
    };

    // Chunked conversion
    let chunked = {
        let mut conv = StreamingConverter::new(
            ConversionOptions::default(),
            MemoryBudget::default(),
        );
        conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));
        let out1 = match conv.feed_chunk(&html[..split_point]) {
            Ok(o) => o,
            Err(_) => return,
        };
        let out2 = match conv.feed_chunk(&html[split_point..]) {
            Ok(o) => o,
            Err(_) => return,
        };
        match conv.finalize() {
            Ok(r) => {
                let mut full = out1.markdown;
                full.extend_from_slice(&out2.markdown);
                full.extend_from_slice(&r.final_markdown);
                full
            }
            Err(_) => return,
        }
    };

    assert_eq!(single, chunked, "Chunk split changed output");
});
