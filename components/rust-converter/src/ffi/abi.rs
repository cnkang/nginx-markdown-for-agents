//! FFI ABI type definitions and error code constants.
//!
//! This module defines the C-compatible (`#[repr(C)]`) structs and constants
//! that form the stable ABI boundary between the NGINX C module and the Rust
//! conversion engine. Every type defined here is shared across the FFI
//! boundary and must maintain layout compatibility with the corresponding
//! C declarations in `markdown_converter.h`.
//!
//! # Error Codes
//!
//! Error codes are returned in the `error_code` field of [`MarkdownResult`].
//! The `ERROR_SUCCESS` (0) value indicates no error; all other values
//! indicate a specific failure mode. Streaming-specific error codes
//! (`ERROR_BUDGET_EXCEEDED`, `ERROR_STREAMING_FALLBACK`, `ERROR_POST_COMMIT`)
//! are feature-gated behind the `streaming` feature.
//!
//! # Struct Layout Stability
//!
//! **Adding fields to any `#[repr(C)]` struct is a breaking ABI change.**
//! When a field is added, both copies of `markdown_converter.h` (in
//! `components/rust-converter/include/` and `components/nginx-module/src/`)
//! must be updated in the same change set. See AGENTS.md Rule 15 for the
//! complete FFI struct synchronization checklist.
//!
//! # Memory Ownership
//!
//! - Pointer fields in result structs (`markdown`, `etag`, `error_message`)
//!   are owned by Rust and must be freed via `markdown_result_free()`.
//! - Pointer fields in option structs (`content_type`, `base_url`) are
//!   borrowed from the C caller for the duration of the FFI call only.

use crate::etag_generator::ETagGenerator;
use crate::token_estimator::TokenEstimator;

/// Success - no error occurred.
pub const ERROR_SUCCESS: u32 = 0;
/// HTML parsing failed (malformed HTML, invalid structure).
pub const ERROR_PARSE: u32 = 1;
/// Character encoding error (invalid UTF-8, unsupported charset).
pub const ERROR_ENCODING: u32 = 2;
/// Conversion timeout exceeded.
pub const ERROR_TIMEOUT: u32 = 3;
/// Memory limit exceeded during conversion.
pub const ERROR_MEMORY_LIMIT: u32 = 4;
/// Invalid input data (NULL pointers, invalid parameters).
pub const ERROR_INVALID_INPUT: u32 = 5;
/// Memory budget exceeded during streaming conversion.
#[cfg(feature = "streaming")]
pub const ERROR_BUDGET_EXCEEDED: u32 = 6;
/// Streaming engine requests fallback to full-buffer conversion (Pre-Commit only).
#[cfg(feature = "streaming")]
pub const ERROR_STREAMING_FALLBACK: u32 = 7;
/// Error after the streaming engine has already emitted partial output (Post-Commit).
#[cfg(feature = "streaming")]
pub const ERROR_POST_COMMIT: u32 = 8;
/// Internal error (unexpected condition, panic caught).
pub const ERROR_INTERNAL: u32 = 99;

/// Conversion options passed from C to Rust.
#[repr(C)]
pub struct MarkdownOptions {
    /// Markdown flavor selector.
    ///
    /// `0` selects CommonMark-compatible output and `1` selects the GFM
    /// extension set. Other values are rejected during option decoding.
    pub flavor: u32,
    /// Cooperative conversion timeout in milliseconds.
    ///
    /// `0` disables the deadline. The value is copied from C at call entry and
    /// is not retained after the FFI call returns.
    pub timeout_ms: u32,
    /// Non-zero when Rust should generate a Markdown-variant ETag.
    pub generate_etag: u8,
    /// Non-zero when Rust should estimate token count/savings.
    pub estimate_tokens: u8,
    /// Non-zero when Rust should extract YAML front matter metadata.
    pub front_matter: u8,
    /// Borrowed Content-Type bytes from the C caller.
    ///
    /// Must either be NULL with `content_type_len == 0` or point to
    /// `content_type_len` readable bytes for the duration of the FFI call.
    /// Rust never takes ownership of this buffer.
    pub content_type: *const u8,
    /// Length in bytes of [`MarkdownOptions::content_type`].
    pub content_type_len: usize,
    /// Borrowed base URL bytes used for resolving relative links.
    ///
    /// Must either be NULL with `base_url_len == 0` or point to
    /// `base_url_len` readable UTF-8 bytes for the duration of the FFI call.
    /// Rust copies any value it needs to retain.
    pub base_url: *const u8,
    /// Length in bytes of [`MarkdownOptions::base_url`].
    pub base_url_len: usize,
    /// Streaming memory budget in bytes (0 = use default).
    ///
    /// When non-zero, the streaming converter uses this value as the
    /// total memory budget instead of the compiled-in default (2 MiB).
    /// Populated from the `markdown_streaming_budget` NGINX directive.
    pub streaming_budget: u64,
    /// Non-zero when noise region pruning is enabled at runtime.
    ///
    /// When non-zero, structural HTML regions matching the prune selectors
    /// are excluded from output. Protection selectors override prune selectors.
    /// Populated from the `markdown_prune_noise` NGINX directive.
    pub prune_noise: u32,
    /// Borrowed prune selector string from the C caller.
    ///
    /// Space-separated tag names for regions to prune (e.g., "nav footer aside").
    /// Must either be NULL with `prune_selectors_len == 0` or point to
    /// `prune_selector_len` readable UTF-8 bytes for the duration of the FFI call.
    /// Rust never takes ownership of this buffer.
    pub prune_selectors: *const u8,
    /// Length in bytes of [`MarkdownOptions::prune_selectors`].
    pub prune_selector_len: usize,
    /// Borrowed protection selector string from the C caller.
    ///
    /// Space-separated tag names for regions to protect from pruning.
    /// An element matching both a prune selector and a protection selector
    /// is kept (protection wins). Must either be NULL with
    /// `prune_protection_selector_len == 0` or point to
    /// `prune_protection_selector_len` readable UTF-8 bytes.
    pub prune_protection_selectors: *const u8,
    /// Length in bytes of [`MarkdownOptions::prune_protection_selectors`].
    pub prune_protection_selector_len: usize,
    /// Unified memory budget in bytes (0 = use per-engine defaults).
    ///
    /// When non-zero, this value overrides the default budget for both
    /// streaming and full-buffer engines, unless a per-engine explicit
    /// budget is set. Priority: per-engine explicit > unified > default.
    /// Populated from the `markdown_memory_budget` NGINX directive.
    pub memory_budget: u64,
    /// LLM provider for token estimation (0=default, 1=openai-gpt, 2=anthropic-claude,
    /// 3=google-gemini, 4=meta-llama).
    ///
    /// When non-zero and `estimate_tokens` is enabled, the provider's
    /// characteristic chars-per-token ratio overrides the default 4.0.
    /// Populated from the `markdown_llm_provider` NGINX directive.
    pub llm_provider: u8,
    /// Explicit chars-per-token ratio for token estimation (0.0 = use default/provider).
    ///
    /// When non-zero, this value overrides both the default 4.0 and the
    /// provider-specific ratio.  Stored as a fixed-point value: actual
    /// ratio = `chars_per_token_fixed / 10.0` (e.g., 38 = 3.8 chars/token).
    /// Populated from the `markdown_chars_per_token` NGINX directive.
    pub chars_per_token_fixed: u8,
}

/// Conversion result returned from Rust to C.
#[repr(C)]
pub struct MarkdownResult {
    /// Rust-owned Markdown output buffer.
    ///
    /// Valid only when `error_code == ERROR_SUCCESS`. The C caller must release
    /// it with `markdown_result_free()` and must not mutate it.
    pub markdown: *mut u8,
    /// Length in bytes of [`MarkdownResult::markdown`].
    pub markdown_len: usize,
    /// Rust-owned ETag bytes, or NULL when ETag generation was disabled.
    ///
    /// Valid only when `etag_len > 0` and released by `markdown_result_free()`.
    pub etag: *mut u8,
    /// Length in bytes of [`MarkdownResult::etag`].
    pub etag_len: usize,
    /// Estimated token count for successful conversions.
    ///
    /// `0` means token estimation was disabled or no estimate was produced.
    pub token_estimate: u32,
    /// FFI error code; `ERROR_SUCCESS` means the output fields are valid.
    pub error_code: u32,
    /// Rust-owned UTF-8 diagnostic message for non-success results.
    ///
    /// The pointer may be NULL when no diagnostic is available. Release through
    /// `markdown_result_free()` together with the rest of the result.
    pub error_message: *mut u8,
    /// Length in bytes of [`MarkdownResult::error_message`].
    pub error_len: usize,
    /// Peak working-set memory estimate during streaming conversion (bytes).
    ///
    /// This is derived from converter-owned resident state and is not
    /// a process RSS/high-water-mark measurement.
    /// Populated by `markdown_streaming_finalize` from
    /// `StreamingStats.peak_memory_estimate`.
    pub peak_memory_estimate: usize,
}

/// Opaque handle to a reusable Rust converter instance shared across FFI calls.
///
/// This struct holds stateful components (ETag generator, token estimator) that
/// are reused across multiple conversions to avoid re-initialization overhead.
/// The handle is created via `markdown_converter_new()` and freed via
/// `markdown_converter_free()` on the C side.
///
/// # Thread Safety
///
/// This handle is NOT thread-safe. The caller must ensure exclusive access
/// when using a handle across multiple FFI calls.
pub struct MarkdownConverterHandle {
    pub(crate) etag_generator: ETagGenerator,
    pub(crate) token_estimator: TokenEstimator,
}

impl MarkdownConverterHandle {
    /// Build a reusable converter handle for repeated FFI calls.
    pub(crate) fn new() -> Self {
        Self {
            etag_generator: ETagGenerator::new(),
            token_estimator: TokenEstimator::new(),
        }
    }
}

/// Internal conversion result before it is projected into the C ABI struct.
pub(crate) struct ConversionOutput {
    pub(crate) markdown: Box<[u8]>,
    pub(crate) etag: Option<Box<[u8]>>,
    pub(crate) token_estimate: u32,
}
