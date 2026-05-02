//! FFI option decoding and input validation helpers.
//!
//! This module translates C `MarkdownOptions` structs into Rust
//! [`ConversionOptions`] and provides pointer/slice validation helpers
//! used by all FFI export entry points.
//!
//! # Option Decoding
//!
//! [`decode_options`] reads the C struct fields and converts them:
//! - `flavor` u32 → [`MarkdownFlavor`] enum
//! - `timeout_ms` u32 → `Duration`
//! - `generate_etag` u8 → `bool`
//! - `estimate_tokens` u8 → `bool`
//! - `content_type` pointer/len → `Option<&str>` (charset detection hint)
//! - `base_url` pointer/len → `Option<String>` (URL resolution base)
//! - `streaming_budget` u64 → passed through for streaming configuration
//!
//! # Validation Helpers
//!
//! - [`required_ref`] — convert a non-NULL C pointer to a Rust reference
//! - [`required_bytes`] — convert a C pointer/length pair to a byte slice
//!
//! Both return `ConversionError::InvalidInput` on NULL pointers, providing
//! a named error message for diagnostics.

use std::slice;
use std::time::Duration;

use crate::converter::{ConversionOptions, MarkdownFlavor};
use crate::error::ConversionError;
use crate::llm_adapter::LlmProvider;

use super::abi::MarkdownOptions;

pub(crate) struct DecodedOptions<'a> {
    pub(crate) content_type: Option<&'a str>,
    pub(crate) timeout: Duration,
    pub(crate) generate_etag: bool,
    pub(crate) estimate_tokens: bool,
    pub(crate) conversion: ConversionOptions,
    #[allow(dead_code)]
    pub(crate) streaming_budget: u64,
    #[allow(dead_code)]
    pub(crate) prune_noise: bool,
    #[allow(dead_code)]
    pub(crate) prune_selectors: Option<&'a str>,
    #[allow(dead_code)]
    pub(crate) prune_protection_selectors: Option<&'a str>,
    #[allow(dead_code)]
    pub(crate) memory_budget: u64,
    #[allow(dead_code)]
    pub(crate) llm_provider: LlmProvider,
    #[allow(dead_code)]
    pub(crate) chars_per_token: f32,
}

/// Convert a required raw pointer from C into a Rust reference.
///
/// # Safety
///
/// The caller must ensure that `ptr` is non-NULL, properly aligned, and
/// points to a valid `T` that will remain live for the returned lifetime `'a`.
pub(crate) unsafe fn required_ref<'a, T>(
    ptr: *const T,
    name: &str,
) -> Result<&'a T, ConversionError> {
    if ptr.is_null() {
        return Err(ConversionError::InvalidInput(format!(
            "{name} pointer is NULL"
        )));
    }

    // SAFETY: Caller provided a non-NULL pointer and accepts the FFI contract
    // that this points to a valid, properly aligned value.
    Ok(unsafe { &*ptr })
}

/// Convert a required pointer/length byte pair into a borrowed slice.
///
/// # Safety
///
/// The caller must ensure that when `len > 0`, `ptr` is non-NULL, properly
/// aligned, and points to at least `len` readable bytes that remain valid
/// for the returned lifetime `'a`.
pub(crate) unsafe fn required_bytes<'a>(
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

/// Decode an optional pointer/length UTF-8 field from ABI options.
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

    match std::str::from_utf8(bytes) {
        Ok(s) => Ok(Some(s)),
        Err(_) => Err(ConversionError::InvalidInput(format!(
            "{field_name} contains invalid UTF-8"
        ))),
    }
}

/// Decode C ABI option fields into strongly-typed Rust conversion settings.
///
/// Returns an error when pointer/length ABI fields are invalid (produces `ConversionError::InvalidInput`).
///
/// # Examples
///
/// ```ignore
/// use std::ptr;
///
/// // Construct a minimal `MarkdownOptions` matching the C ABI layout.
/// let opts = MarkdownOptions {
///     content_type: ptr::null(),
///     content_type_len: 0,
///     base_url: ptr::null(),
///     base_url_len: 0,
///     flavor: 0,
///     front_matter: 0,
///     timeout_ms: 0,
///     generate_etag: 1,
///     estimate_tokens: 0,
///     streaming_budget: 0,
///     prune_noise: 1,
///     prune_selectors: std::ptr::null(),
///     prune_selector_len: 0,
///     prune_protection_selectors: std::ptr::null(),
///     prune_protection_selector_len: 0,
///     memory_budget: 0,
/// };
///
/// let decoded = decode_options(&opts).unwrap();
/// assert!(decoded.generate_etag);
/// ```
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
        2 => MarkdownFlavor::Mdx,
        3 => MarkdownFlavor::OrgMode,
        _ => MarkdownFlavor::CommonMark,
    };

    let include_front_matter = options.front_matter != 0;
    let generate_etag = options.generate_etag != 0;
    let resolve_relative_urls = base_url.is_some();
    let timeout = if options.timeout_ms > 0 {
        Duration::from_millis(u64::from(options.timeout_ms))
    } else {
        Duration::ZERO
    };

    let prune_noise = options.prune_noise != 0;
    let prune_selectors = optional_utf8(
        options.prune_selectors,
        options.prune_selector_len,
        "prune_selectors",
    )?;
    let prune_protection_selectors = optional_utf8(
        options.prune_protection_selectors,
        options.prune_protection_selector_len,
        "prune_protection_selectors",
    )?;

    Ok(DecodedOptions {
        content_type,
        timeout,
        generate_etag,
        estimate_tokens: options.estimate_tokens != 0,
        streaming_budget: options.streaming_budget,
        prune_noise,
        prune_selectors,
        prune_protection_selectors,
        memory_budget: options.memory_budget,
        llm_provider: LlmProvider::from_ffi(options.llm_provider),
        chars_per_token: if options.chars_per_token_fixed > 0 {
            options.chars_per_token_fixed as f32 / 10.0
        } else {
            LlmProvider::from_ffi(options.llm_provider).chars_per_token()
        },
        conversion: ConversionOptions {
            flavor,
            include_front_matter,
            extract_metadata: include_front_matter || generate_etag,
            simplify_navigation: true,
            preserve_tables: true,
            base_url,
            resolve_relative_urls,
            prune_config: crate::converter::pruning::PruneConfig::from_ffi(
                prune_noise,
                prune_selectors,
                prune_protection_selectors,
            ),
        },
    })
}
