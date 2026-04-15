use nginx_markdown_converter::converter::ConversionOptions;
use nginx_markdown_converter::error::ConversionError;
use nginx_markdown_converter::streaming::budget::MemoryBudget;
use nginx_markdown_converter::streaming::converter::StreamingConverter;

pub fn normalize_ascii_whitespace(bytes: &[u8]) -> Vec<u8> {
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

pub fn split_sizes_from_seed(seed: &[u8], html_len: usize) -> Vec<usize> {
    if html_len == 0 {
        return vec![1];
    }

    let mut splits = Vec::with_capacity(seed.len().min(64) + 1);
    let mut consumed = 0usize;

    for (idx, &byte) in seed.iter().take(64).enumerate() {
        let remaining = html_len.saturating_sub(consumed);
        if remaining == 0 {
            break;
        }

        let raw = if idx % 8 == 0 {
            usize::from(byte).saturating_mul(8).max(1)
        } else {
            usize::from(byte % 32).max(1)
        };
        let size = raw.min(remaining);
        splits.push(size);
        consumed += size;
    }

    if consumed < html_len {
        splits.push(html_len - consumed);
    }

    splits
}

pub fn convert(html: &[u8], splits: &[usize]) -> Result<Vec<u8>, ConversionError> {
    let mut conv = StreamingConverter::new(ConversionOptions::default(), MemoryBudget::default());
    conv.set_content_type(Some("text/html; charset=UTF-8".to_string()));

    let mut out = Vec::new();
    let mut cursor = 0usize;

    for &split in splits {
        if cursor >= html.len() {
            break;
        }
        let end = cursor.saturating_add(split.max(1)).min(html.len());
        let chunk = conv.feed_chunk(&html[cursor..end])?;
        out.extend_from_slice(&chunk.markdown);
        cursor = end;
    }

    if cursor < html.len() {
        let chunk = conv.feed_chunk(&html[cursor..])?;
        out.extend_from_slice(&chunk.markdown);
    }

    let result = conv.finalize()?;
    out.extend_from_slice(&result.final_markdown);
    Ok(out)
}
