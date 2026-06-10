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

/// Minimum allowed chars-per-token ratio.
/// Values below this produce misleadingly large token estimates.
const CHARS_PER_TOKEN_MIN: f32 = 1.0;
/// Maximum allowed chars-per-token ratio.
/// Values above this produce misleadingly small token estimates.
const CHARS_PER_TOKEN_MAX: f32 = 100.0;

/// Clamp a chars-per-token value to the allowed range.
///
/// Non-positive inputs default to 4.0 (English text heuristic).
/// Positive inputs are clamped to [CHARS_PER_TOKEN_MIN, CHARS_PER_TOKEN_MAX].
pub(crate) fn clamp_chars_per_token(raw: f32) -> f32 {
    if raw <= 0.0 {
        4.0
    } else {
        raw.clamp(CHARS_PER_TOKEN_MIN, CHARS_PER_TOKEN_MAX)
    }
}

pub(crate) struct DecodedOptions<'a> {
    pub(crate) content_type: Option<&'a str>,
    /// General timeout used by streaming/incremental paths.
    /// The full-buffer path uses `parse_timeout` instead.
    #[allow(dead_code)]
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
    /// Unified memory budget (bytes).  Currently enforced only by the
    /// streaming and incremental paths.  The full-buffer path relies
    /// on the NGINX-side `max_size` limit instead; see the FFI
    /// header contract for details.
    #[allow(dead_code)]
    pub(crate) memory_budget: u64,
    #[allow(dead_code)]
    pub(crate) llm_provider: LlmProvider,
    /// Raw chars-per-token from FFI options (before normalization).
    /// Retained for diagnostics/logging; all estimation paths use
    /// [`effective_chars_per_token`](Self::effective_chars_per_token).
    #[allow(dead_code)]
    pub(crate) chars_per_token: f32,
    /// Normalized chars-per-token clamped to a sane range [1.0, 100.0].
    /// All token estimation paths (full-buffer, streaming, incremental)
    /// must use this value to avoid divergent behavior when the raw
    /// FFI `chars_per_token_fixed` decodes to a non-positive or
    /// pathological value.
    pub(crate) effective_chars_per_token: f32,
    /// Parse-specific timeout.  When non-zero, the parser uses this
    /// deadline instead of the general `timeout`.  Falls back to
    /// `timeout` when zero.
    pub(crate) parse_timeout: Duration,
    /// Parser memory budget in bytes (0 = unlimited).
    pub(crate) parser_memory_budget: u64,
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
///     llm_provider: 0,
///     chars_per_token_fixed: 0,
///     parse_timeout_ms: 0,
///     parser_memory_budget: 0,
///     flush_threshold: 0,
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
        0 => MarkdownFlavor::CommonMark,
        1 => MarkdownFlavor::GitHubFlavoredMarkdown,
        2 => MarkdownFlavor::Mdx,
        3 => MarkdownFlavor::OrgMode,
        _ => {
            return Err(ConversionError::InvalidInput(format!(
                "invalid markdown flavor: {} (must be 0-3)",
                options.flavor
            )));
        }
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

    let raw_cpt = if options.chars_per_token_fixed > 0 {
        options.chars_per_token_fixed as f32 / 10.0
    } else {
        LlmProvider::from_ffi(options.llm_provider).chars_per_token()
    };

    /* Resolve parse_timeout: prefer parse_timeout_ms, fall back to timeout_ms */
    let parse_timeout = if options.parse_timeout_ms > 0 {
        Duration::from_millis(u64::from(options.parse_timeout_ms))
    } else {
        timeout
    };

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
        chars_per_token: raw_cpt,
        effective_chars_per_token: clamp_chars_per_token(raw_cpt),
        parse_timeout,
        parser_memory_budget: options.parser_memory_budget,
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
