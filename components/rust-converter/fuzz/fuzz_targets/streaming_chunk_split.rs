#![no_main]

use libfuzzer_sys::fuzz_target;
use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::streaming::budget::MemoryBudget;
use nginx_markdown_converter::streaming::converter::StreamingConverter;

fn normalize_ascii_whitespace(bytes: &[u8]) -> Vec<u8> {
    let mut normalized = Vec::with_capacity(bytes.len());
    let mut pending_space = false;

    for &byte in bytes {
        if byte.is_ascii_whitespace() {
            pending_space = true;
            continue;
        }

        if pending_space && !normalized.is_empty() {
            normalized.push(b' ');
        }
        pending_space = false;
        normalized.push(byte);
    }

    normalized
}

fn convert(html: &[u8], splits: &[usize]) -> Option<Vec<u8>> {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

    let mut out = Vec::new();
    let mut cursor = 0usize;

    for &split in splits {
        if cursor >= html.len() {
            break;
        }
        let end = cursor.saturating_add(split.max(1)).min(html.len());
        let chunk = conv.feed_chunk(&html[cursor..end]).ok()?;
        out.extend_from_slice(&chunk.markdown);
        cursor = end;
    }

    if cursor < html.len() {
        let chunk = conv.feed_chunk(&html[cursor..]).ok()?;
        out.extend_from_slice(&chunk.markdown);
    }

    let result = conv.finalize().ok()?;
    out.extend_from_slice(&result.final_markdown);
    Some(out)
}

fuzz_target!(|data: &[u8]| {
    if data.len() < 2 {
        return;
    }

    let split_ratio = usize::from(data[0]);
    let html = &data[1..];
    let split_point = (html.len() * split_ratio) / 256;

    let single = match convert(html, &[html.len().max(1)]) {
        Some(single) => single,
        None => return,
    };

    let split = match convert(html, &[split_point.max(1), html.len()]) {
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
