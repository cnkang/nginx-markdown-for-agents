use std::slice;
use std::time::Duration;

use crate::converter::{ConversionOptions, MarkdownFlavor};
use crate::error::ConversionError;

use super::abi::MarkdownOptions;

pub(crate) struct DecodedOptions<'a> {
    pub(crate) content_type: Option<&'a str>,
    pub(crate) timeout: Duration,
    pub(crate) generate_etag: bool,
    pub(crate) estimate_tokens: bool,
    pub(crate) conversion: ConversionOptions,
}

pub(crate) fn required_ref<'a, T>(ptr: *const T, name: &str) -> Result<&'a T, ConversionError> {
    if ptr.is_null() {
        return Err(ConversionError::InvalidInput(format!(
            "{name} pointer is NULL"
        )));
    }

    // SAFETY: Caller provided a non-NULL pointer and accepts the FFI contract
    // that this points to a valid, properly aligned value.
    Ok(unsafe { &*ptr })
}

pub(crate) fn required_bytes<'a>(
    ptr: *const u8,
    len: usize,
    name: &str,
) -> Result<&'a [u8], ConversionError> {
    if len == 0 {
        return Ok(&[]);
    }

    if ptr.is_null() {
        return Err(ConversionError::InvalidInput(format!(
            "{name} pointer is NULL"
        )));
    }

    // SAFETY: Pointer was validated as non-NULL above; caller guarantees `len`
    // bytes are valid and readable for the duration of this call.
    Ok(unsafe { slice::from_raw_parts(ptr, len) })
}

fn optional_utf8<'a>(
    ptr: *const u8,
    len: usize,
    field_name: &str,
) -> Result<Option<&'a str>, ConversionError> {
    if len == 0 {
        return Ok(None);
    }

    if ptr.is_null() {
        return Err(ConversionError::InvalidInput(format!(
            "{field_name}_len > 0 with NULL {field_name} pointer"
        )));
    }

    // SAFETY: Pointer is non-NULL and caller guarantees `len` readable bytes.
    let bytes = unsafe { slice::from_raw_parts(ptr, len) };

    Ok(std::str::from_utf8(bytes).ok())
}

pub(crate) fn decode_options(
    options: &MarkdownOptions,
) -> Result<DecodedOptions<'_>, ConversionError> {
    let content_type = optional_utf8(
        options.content_type,
        options.content_type_len,
        "content_type",
    )?;
    let base_url =
        optional_utf8(options.base_url, options.base_url_len, "base_url")?.map(ToOwned::to_owned);

    let flavor = match options.flavor {
        1 => MarkdownFlavor::GitHubFlavoredMarkdown,
        _ => MarkdownFlavor::CommonMark,
    };

    let include_front_matter = options.front_matter != 0;
    let resolve_relative_urls = base_url.is_some();
    let timeout = if options.timeout_ms > 0 {
        Duration::from_millis(u64::from(options.timeout_ms))
    } else {
        Duration::ZERO
    };

    Ok(DecodedOptions {
        content_type,
        timeout,
        generate_etag: options.generate_etag != 0,
        estimate_tokens: options.estimate_tokens != 0,
        conversion: ConversionOptions {
            flavor,
            include_front_matter,
            extract_metadata: include_front_matter,
            simplify_navigation: true,
            preserve_tables: true,
            base_url,
            resolve_relative_urls,
        },
    })
}
