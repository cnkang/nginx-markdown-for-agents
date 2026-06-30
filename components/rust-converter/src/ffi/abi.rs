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

/// Post-commit safe finish: return code from `markdown_streaming_safe_finish`.
///
/// Indicates that all open Markdown structures were successfully closed.
/// The output buffer contains the closing Markdown text (fences, newlines, etc.)
/// that the C caller should append after the already-committed output.
///
/// Corresponds to design doc FFI Return Code 3 (Post-commit safe finish required
/// was handled successfully).
#[cfg(feature = "streaming")]
pub const POST_COMMIT_SAFE_FINISH: u32 = 3;

/// Post-commit abort required: return code from `markdown_streaming_safe_finish`.
///
/// Indicates that the open Markdown structures could NOT be safely closed
/// (e.g., emitter budget exceeded during closure attempt). The C caller must
/// abort and discard or truncate the partial output. C MUST NOT infer or
/// synthesize Markdown closure for Rust-owned parser/emitter state.
///
/// Corresponds to design doc FFI Return Code 4 (Post-commit abort required).
#[cfg(feature = "streaming")]
pub const POST_COMMIT_ABORT: u32 = 4;

/// Decompression budget exceeded (decompressed output exceeds decompress_max_size).
pub const ERROR_DECOMPRESSION_BUDGET_EXCEEDED: u32 = 9;
/// Parse timeout: HTML parsing exceeded the configured deadline.
pub const ERROR_PARSE_TIMEOUT: u32 = 10;
/// Parse budget exceeded: parser memory allocation exceeded parser_memory_budget.
pub const ERROR_PARSE_BUDGET_EXCEEDED: u32 = 11;
/// Decompression format error (invalid or corrupt compressed data).
#[allow(dead_code)]
pub const ERROR_DECOMPRESSION_FORMAT_ERROR: u32 = 12;
/// Decompression truncated input (incomplete compressed stream).
#[allow(dead_code)]
pub const ERROR_DECOMPRESSION_TRUNCATED_INPUT: u32 = 13;
/// Decompression I/O error (unexpected failure during decompression).
#[allow(dead_code)]
pub const ERROR_DECOMPRESSION_IO_ERROR: u32 = 14;
/// Internal error (unexpected condition, panic caught).
pub const ERROR_INTERNAL: u32 = 99;

/// Decompression error category: budget exceeded.
///
/// These constants are used in `FFIDecompResult.error_category` and as
/// the return value of `markdown_decompress_bounded`. They occupy a
/// separate namespace from `ERROR_*` (which are for `MarkdownResult.error_code`).
/// Values start at 101 to avoid numeric overlap with the ERROR_* range (0-99).
#[allow(dead_code)]
pub const DECOMP_CATEGORY_BUDGET_EXCEEDED: u32 = 101;
/// Decompression error category: invalid compression format.
pub const DECOMP_CATEGORY_FORMAT_ERROR: u32 = 102;
/// Decompression error category: truncated input stream.
#[allow(dead_code)]
pub const DECOMP_CATEGORY_TRUNCATED_INPUT: u32 = 103;
/// Decompression error category: I/O error during decompression.
#[allow(dead_code)]
pub const DECOMP_CATEGORY_IO_ERROR: u32 = 104;
/// Decompression error category: invalid arguments (NULL pointers, unknown format).
pub const DECOMP_CATEGORY_INVALID_ARGS: u32 = 105;

/// Conversion options passed from C to Rust.
///
/// This full-buffer ABI keeps its own explicit `#[repr(C)]` layout instead of
/// sharing fields with `StreamingOptions`. The duplicate option fields are an
/// intentional FFI stability tradeoff: full-buffer and streaming callers each
/// get a clear, independently versioned C contract. Any shared semantic change
/// must update both structs, generated headers, layout tests, and docs in the
/// same change set; structural reuse belongs in a future breaking ABI version.
#[repr(C)]
pub struct MarkdownOptions {
    /// Markdown flavor selector.
    ///
    /// `0` selects CommonMark-compatible output, `1` selects the GFM
    /// extension set, `2` selects experimental MDX-oriented behavior, and `3`
    /// selects experimental Org-mode-oriented behavior. Other values are
    /// rejected during option decoding.
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
    /// When non-zero, NGINX may use this value to derive full-buffer
    /// max_size when no explicit markdown_max_size is set. Rust
    /// currently enforces this budget only for streaming/incremental
    /// paths; full-buffer relies on NGINX-side buffering limits.
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
    ///
    /// # Type rationale
    ///
    /// `u8` is intentional for FFI ABI compactness (single byte, no padding).
    /// Valid range: 1–255, representing ratios 0.1–25.5 chars/token.
    /// Value 0 means "use default".  All practical LLM tokenizer ratios
    /// (GPT-4: ~3.5, Claude: ~4.0, Gemini: ~3.8) fit within this range.
    pub chars_per_token_fixed: u8,
    /// Parse-specific timeout in milliseconds (0 = use `timeout_ms` fallback).
    ///
    /// When non-zero, the HTML parser uses this deadline instead of the
    /// general `timeout_ms` field.  This allows operators to set a tighter
    /// parse-phase budget while keeping a longer overall conversion timeout.
    /// Populated from the `markdown_parse_timeout` NGINX directive.
    pub parse_timeout_ms: u32,
    /// Parser memory budget in bytes (0 = unlimited).
    ///
    /// When non-zero, the HTML parser is constrained to this memory
    /// allocation ceiling.  Exceeding the budget produces
    /// `ERROR_PARSE_BUDGET_EXCEEDED`.
    /// Populated from the `markdown_parser_budget` NGINX directive.
    pub parser_memory_budget: u64,
    /// Streaming flush threshold in bytes (0 = use default threshold).
    ///
    /// Controls the minimum number of accumulated output bytes before
    /// the streaming emitter returns non-empty output to the C caller.
    /// Populated from the `markdown_stream_flush_min` NGINX directive.
    pub flush_threshold: u32,
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
}

impl MarkdownConverterHandle {
    /// Build a reusable converter handle for repeated FFI calls.
    pub(crate) fn new() -> Self {
        Self {
            etag_generator: ETagGenerator::new(),
        }
    }
}

/// Internal conversion result before it is projected into the C ABI struct.
///
/// Holds the three outputs that the FFI boundary exposes: the converted
/// Markdown bytes, an optional BLAKE3-based ETag, and a heuristic token
/// estimate.  This struct is `pub(crate)` because it is consumed only by
/// the FFI conversion entry point, which projects it into
/// [`MarkdownResult`] for the C caller.
pub(crate) struct ConversionOutput {
    /// Converted Markdown content (owned bytes, copied into FFI-allocated memory on export).
    pub(crate) markdown: Box<[u8]>,
    /// BLAKE3-based ETag for conditional request support, or `None` if ETag generation is disabled.
    pub(crate) etag: Option<Box<[u8]>>,
    /// Heuristic token count estimate for LLM context-window budgeting.
    pub(crate) token_estimate: u32,
}

/// Result of Accept header content negotiation.
///
/// Returned by `markdown_negotiate_accept()` for the C caller to decide
/// whether to proceed with HTML-to-Markdown conversion.
///
/// # Fields
///
/// - `should_convert`: 1 if the client prefers text/markdown, 0 otherwise.
/// - `reason`: Numeric reason code for the decision.
///   - 0: Convert (text/markdown preferred)
///   - 1: No Accept header present
///   - 2: text/markdown has lower q-value than text/html
///   - 3: text/markdown;q=0 explicit reject
///   - 4: Malformed Accept header
#[repr(C)]
pub struct FFIAcceptResult {
    /// 1 if conversion should proceed, 0 otherwise.
    pub should_convert: u8,
    /// Reason code for the negotiation decision.
    pub reason: u8,
}

/// Reason code: client prefers text/markdown, proceed with conversion.
pub const NEGOTIATE_REASON_CONVERT: u8 = 0;
/// Reason code: no Accept header was present.
pub const NEGOTIATE_REASON_NO_ACCEPT: u8 = 1;
/// Reason code: text/markdown has lower q-value than text/html.
pub const NEGOTIATE_REASON_LOWER_Q: u8 = 2;
/// Reason code: client explicitly set text/markdown;q=0.
pub const NEGOTIATE_REASON_EXPLICIT_REJECT: u8 = 3;
/// Reason code: Accept header is malformed.
pub const NEGOTIATE_REASON_MALFORMED: u8 = 4;

/// Wildcard mode: strict — wildcard MIME type does NOT match text/markdown.
#[allow(dead_code)]
pub const NEGOTIATE_WILDCARD_STRICT: u8 = 0;
/// Wildcard mode: allow — wildcard MIME type matches text/markdown.
#[allow(dead_code)]
pub const NEGOTIATE_WILDCARD_ALLOW: u8 = 1;

/// Borrowed byte string passed across the FFI boundary.
///
/// Points to caller-owned memory that must remain valid for the duration of
/// the call. `data` may be NULL only when `len == 0`.
#[repr(C)]
pub struct FFIStr {
    /// Pointer to the first byte (may be NULL when `len == 0`).
    pub data: *const u8,
    /// Number of bytes.
    pub len: usize,
}

/// Input snapshot for `markdown_decide_eligibility`.
///
/// All fields are marshaled from `ngx_http_request_t` and the module
/// configuration by the C caller. Array fields (`content_types`,
/// `stream_types`) point to caller-owned arrays of [`FFIStr`] valid for the
/// duration of the call; a NULL pointer with count 0 means "not configured".
#[repr(C)]
pub struct FFIEligibilityInput {
    /// 1 if `markdown_filter` is enabled for this request, else 0.
    pub filter_enabled: u8,
    /// 1 if the request method is GET or HEAD, else 0.
    pub method_get_or_head: u8,
    /// 1 if the request carried a `Range` header, else 0.
    pub has_range_header: u8,
    /// Response status code.
    pub status: u16,
    /// Response `Content-Type` value bytes (NULL/0 if absent).
    pub content_type: *const u8,
    /// Length of `content_type`.
    pub content_type_len: usize,
    /// Configured `markdown_content_types` allowlist (NULL/0 = default html).
    pub content_types: *const FFIStr,
    /// Number of entries in `content_types`.
    pub content_types_count: usize,
    /// Configured `markdown_stream_types` exclusions (NULL/0 = none).
    pub stream_types: *const FFIStr,
    /// Number of entries in `stream_types`.
    pub stream_types_count: usize,
    /// Response `Content-Length`; negative means absent/unknown.
    pub content_length: i64,
    /// Effective full-buffer body limit in bytes; 0 means unlimited.
    pub body_limit: usize,
}

/// Opaque Rust-owned set of config-time-validated trusted-proxy CIDRs
/// (spec 47).
///
/// The C side creates one handle per `markdown_trusted_proxies` directive via
/// `markdown_trusted_proxies_new`, appends each CIDR with
/// `markdown_trusted_proxies_push` (which validates at config time), and frees
/// it with `markdown_trusted_proxies_free` (registered as an NGINX pool
/// cleanup handler).  CIDR parsing happens once here; request-time matching is
/// pure bitwise prefix comparison.  This type is intentionally **not**
/// `#[repr(C)]` — cbindgen emits it as an opaque struct so the C side only
/// ever holds a pointer and never inspects the layout.
pub struct MarkdownTrustedProxies {
    pub(crate) cidrs: Vec<crate::forwarded::Cidr>,
}

impl MarkdownTrustedProxies {
    /// Create an empty trusted-proxy set.
    pub(crate) fn new() -> Self {
        Self { cidrs: Vec::new() }
    }
}

impl Default for MarkdownTrustedProxies {
    fn default() -> Self {
        Self::new()
    }
}

/// `markdown_trusted_proxies_push` result: CIDR parsed and stored.
pub const TRUSTED_PROXIES_PUSH_OK: u8 = 0;
/// `markdown_trusted_proxies_push` result: CIDR string was invalid.
pub const TRUSTED_PROXIES_PUSH_INVALID_CIDR: u8 = 1;
/// `markdown_trusted_proxies_push` result: NULL handle or input.
pub const TRUSTED_PROXIES_PUSH_NULL: u8 = 2;

/// `markdown_decide_base_url` result: decision written successfully.
pub const DECIDE_BASE_URL_OK: u8 = 0;
/// `markdown_decide_base_url` result: NULL input/output or buffer too small.
pub const DECIDE_BASE_URL_INVALID: u8 = 1;

/// Input snapshot for `markdown_decide_base_url` (spec 47).
///
/// All byte fields are borrowed from the C caller for the duration of the
/// call (NULL with length 0 means "absent").  `trusted` is a borrowed
/// pointer to a [`MarkdownTrustedProxies`] handle (may be NULL when trust is
/// not configured).  This struct intentionally exposes neither
/// `ngx_http_request_t *` nor any NGINX pool.
#[repr(C)]
pub struct FFIBaseUrlInput {
    /// Textual source IP (`r->connection->addr_text`), realip/PROXY resolved.
    pub source_ip: *const u8,
    /// Length of `source_ip`.
    pub source_ip_len: usize,
    /// Borrowed handle of config-time-validated trusted CIDRs (may be NULL).
    pub trusted: *const MarkdownTrustedProxies,
    /// `Forwarded` header value bytes (RFC 7239), NULL/0 if absent.
    pub forwarded: *const u8,
    /// Length of `forwarded`.
    pub forwarded_len: usize,
    /// `X-Forwarded-Proto` header value bytes, NULL/0 if absent.
    pub x_forwarded_proto: *const u8,
    /// Length of `x_forwarded_proto`.
    pub x_forwarded_proto_len: usize,
    /// `X-Forwarded-Host` header value bytes, NULL/0 if absent.
    pub x_forwarded_host: *const u8,
    /// Length of `x_forwarded_host`.
    pub x_forwarded_host_len: usize,
    /// `Host` header value bytes, NULL/0 if absent.
    pub host: *const u8,
    /// Length of `host`.
    pub host_len: usize,
    /// 1 if the source is a Unix-domain socket peer (forces untrusted).
    pub is_unix_socket: u8,
    /// 1 if `markdown_trusted_proxies` was configured (even as `off`).
    pub trusted_configured: u8,
}

/// Result of `markdown_decide_base_url` (spec 47).
///
/// `base_url_len` bytes are written into the caller-provided output buffer.
/// `reason` is a `BaseUrlReason` discriminant; `source` is a `BaseUrlSource`
/// discriminant.
#[repr(C)]
pub struct FFIBaseUrlDecision {
    /// Bytes written into the caller's output buffer.
    pub base_url_len: usize,
    /// `BaseUrlReason` discriminant.
    pub reason: u8,
    /// `BaseUrlSource` discriminant.
    pub source: u8,
}

/// Result of a conditional request check (If-None-Match / If-Modified-Since).
///
/// Returned by `markdown_check_conditional` FFI function.
///
/// Fields:
/// - `result_code`: 0 = not modified (send 304), 1 = proceed (no match or no conditional headers)
/// - `matched_etag_len`: Length of the matched ETag value (reserved, currently always 0)
#[repr(C)]
pub struct FFIConditionalResult {
    /// 0 = not_modified (send 304), 1 = proceed (modified or no conditional headers)
    pub result_code: u8,
    /// Length of matched ETag value (reserved for future use, currently 0).
    pub matched_etag_len: u32,
}

/// Result of a decision engine evaluation.
///
/// Returned by `markdown_make_decision` FFI function.
///
/// Fields:
/// - `decision`: 0 = convert, 1 = skip
/// - `reason_code`: Canonical `ReasonCode` discriminant for the decision
///   (e.g. `Converted` = 0, `SkippedAccept` = 1, `NotEligible` = 14,
///   `Disabled` = 15). The value can be passed directly to
///   `markdown_reason_code_str()` / `markdown_reason_code_metric_key()`.
#[repr(C)]
pub struct FFIDecisionResult {
    /// 0 = convert, 1 = skip
    pub decision: u8,
    /// Canonical `ReasonCode` discriminant for the decision
    /// (`Converted` = 0 when `decision` == 0).
    pub reason_code: u8,
}

/// Input snapshot for `markdown_decide_conditional` (spec 49).
///
/// All byte fields are borrowed from the C caller for the duration of the
/// call (NULL with length 0 means "absent"). This struct exposes neither
/// `ngx_http_request_t *` nor any NGINX pool; the C side marshals request
/// headers, response metadata, and the effective `cache_validation` mode.
#[repr(C)]
pub struct FFIConditionalInput {
    /// Effective `markdown_cache_validation` mode: 0 = off, 1 = ims_only,
    /// 2 = full. Unknown values fall back to ims_only (safe default).
    pub cache_validation: u8,
    /// 1 if the request carried a `Range` header, else 0.
    pub has_range: u8,
    /// 1 if request or response carried `Cache-Control: no-transform`.
    pub no_transform: u8,
    /// `If-None-Match` header value bytes (NULL/0 if absent).
    pub if_none_match: *const u8,
    /// Length of `if_none_match`.
    pub if_none_match_len: usize,
    /// Transformed-representation entity ETag bytes (NULL/0 if none; always
    /// absent on the streaming path).
    pub entity_etag: *const u8,
    /// Length of `entity_etag`.
    pub entity_etag_len: usize,
    /// `If-Modified-Since` header value bytes (NULL/0 if absent).
    pub if_modified_since: *const u8,
    /// Length of `if_modified_since`.
    pub if_modified_since_len: usize,
    /// Preserved upstream `Last-Modified` value bytes (NULL/0 if absent).
    pub last_modified: *const u8,
    /// Length of `last_modified`.
    pub last_modified_len: usize,
}

/// Result of `markdown_decide_conditional` (spec 49).
#[repr(C)]
pub struct FFIConditionalDecision {
    /// `ConditionalOutcome` discriminant: 0 = not_modified (send 304),
    /// 1 = proceed, 2 = bypass (deliver upstream unmodified).
    pub outcome: u8,
    /// `ConditionalReason` discriminant (spec 53 alignment).
    pub reason: u8,
    /// `ConditionalHeader` discriminant: 0 = none, 1 = if_none_match,
    /// 2 = if_modified_since.
    pub evaluated_header: u8,
}

/// Input snapshot for `markdown_decide_streaming` (spec 49).
///
/// This struct exposes neither `ngx_http_request_t *` nor any NGINX pool;
/// the C side marshals the policy/engine selectors, the effective
/// `cache_validation` mode, request/response flags, and sizing fields.
#[repr(C)]
pub struct FFIStreamingInput {
    /// `markdown_streaming` policy: 0 = off, 1 = auto, 2 = force.
    pub policy: u8,
    /// `markdown_streaming_engine`: 0 = off, 1 = auto, 2 = on.
    pub engine: u8,
    /// Effective `markdown_cache_validation` mode: 0 = off, 1 = ims_only,
    /// 2 = full.
    pub cache_validation: u8,
    /// 1 if the request method is `HEAD`, else 0.
    pub is_head: u8,
    /// 1 if the conditional decision yielded `304 Not Modified`, else 0.
    pub is_not_modified: u8,
    /// 1 if the request carried a `Range` header, else 0.
    pub has_range: u8,
    /// 1 if request or response carried `Cache-Control: no-transform`.
    pub no_transform: u8,
    /// 1 if the upstream response carried a `Content-Encoding`, else 0.
    pub has_content_encoding: u8,
    /// 1 if the upstream `Content-Length` is known, else 0.
    pub content_length_known: u8,
    /// Upstream `Content-Length` in bytes (meaningful when known).
    pub content_length: u64,
    /// `markdown_stream_threshold` in bytes (auto-mode trigger).
    pub streaming_threshold: u64,
}

/// `markdown_decide_streaming` sentinel: no block reason (request is
/// streaming-eligible).
pub const STREAMING_BLOCK_REASON_NONE: u8 = 255;

/// Result of `markdown_decide_streaming` (spec 49).
#[repr(C)]
pub struct FFIStreamingDecision {
    /// 1 if the request may take the streaming path, else 0.
    pub eligible: u8,
    /// `StreamingBlockReason` discriminant when `eligible == 0`, or
    /// `STREAMING_BLOCK_REASON_NONE` (255) when `eligible == 1`.
    pub block_reason: u8,
}

/// A single header operation in a header plan.
///
/// Fields:
/// - `op_type`: 0 = set, 1 = delete, 2 = set-etag-placeholder, 3 = delete-all
/// - `key`: Pointer to header name (NUL-terminated, borrowed from plan; NULL for set-etag-placeholder)
/// - `key_len`: Length of header name
/// - `value`: Pointer to header value (NUL-terminated, borrowed from plan; NULL for delete, delete-all, and set-etag-placeholder)
/// - `value_len`: Length of header value (0 for delete, delete-all, and set-etag-placeholder)
///
/// For op_type == 2 (set-etag-placeholder), the C caller must substitute
/// the actual ETag value from MarkdownResult.etag instead of reading
/// key/value from the entry. This avoids the fragile empty-string
/// placeholder contract.
#[repr(C)]
pub struct FFIHeaderEntry {
    /// 0 = set, 1 = delete, 2 = set-etag-placeholder, 3 = delete-all
    pub op_type: u8,
    /// Pointer to header name (borrowed).
    pub key: *const u8,
    /// Length of header name.
    pub key_len: usize,
    /// Pointer to header value (NULL for delete).
    pub value: *const u8,
    /// Length of header value.
    pub value_len: usize,
}

/// Result of a bounded decompression operation.
///
/// Returned by decompression FFI functions to communicate the output buffer,
/// its length, and any error category to the C caller.
///
/// # Error Categories
///
/// - `0` = success (output is valid decompressed data)
/// - `DECOMP_CATEGORY_BUDGET_EXCEEDED` (101) = decompressed output exceeded the configured limit
/// - `DECOMP_CATEGORY_FORMAT_ERROR` (102) = input is not valid gzip/deflate
/// - `DECOMP_CATEGORY_TRUNCATED_INPUT` (103) = input stream ended prematurely
/// - `DECOMP_CATEGORY_IO_ERROR` (104) = I/O error during decompression
/// - `DECOMP_CATEGORY_INVALID_ARGS` (105) = invalid arguments (NULL pointers, unknown format)
///
/// # Memory Ownership
///
/// When `error_category == 0`, the `output` pointer is Rust-owned and must be
/// freed via the corresponding cleanup function. When `error_category != 0`,
/// `output` is NULL and `output_len` is 0.
#[repr(C)]
pub struct FFIDecompResult {
    /// Pointer to decompressed output buffer (Rust-owned, NULL on error).
    pub output: *mut u8,
    /// Length of decompressed output in bytes (0 on error).
    pub output_len: usize,
    /// Error category: 0=success, 101..105 for DECOMP_CATEGORY_* errors.
    /// See DECOMP_CATEGORY_BUDGET_EXCEEDED through DECOMP_CATEGORY_INVALID_ARGS.
    pub error_category: u32,
}

/// Opaque Rust-owned handle that keeps header-plan backing storage alive.
#[repr(C)]
pub struct FFIHeaderPlanHandle {
    _private: [u8; 0],
}

/// Header plan: an ordered list of header operations for atomic application.
///
/// Fields:
/// - `handle`: Opaque plan handle owned by Rust
/// - `entries`: Pointer to array of FFIHeaderEntry
/// - `count`: Number of entries in the plan
#[repr(C)]
pub struct FFIHeaderPlan {
    /// Opaque Rust-owned handle. Free with `markdown_header_plan_free`.
    pub handle: *mut FFIHeaderPlanHandle,
    /// Pointer to header entry array.
    pub entries: *const FFIHeaderEntry,
    /// Number of entries.
    pub count: usize,
}

// ─── Profile FFI types (spec 50, 0.9.0) ──────────────────────────────────────

/// FFI-safe profile selector.
///
/// Maps to the Rust `Profile` enum. The C side stores this as a `u8` field
/// in the location config struct and passes it to `markdown_detect_conflicts`.
///
/// Discriminants are frozen for the 1.0 stability contract.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FFIProfile {
    /// No profile active — use Config V2 built-in defaults.
    None = 0,
    /// CDN/caching proxy: full conditional requests, no streaming.
    StrictCache = 1,
    /// Recommended default: IMS-only caching, streaming on auto.
    Balanced = 2,
    /// AI agent workloads: aggressive streaming, no caching overhead.
    StreamingFirst = 3,
}

/// Severity level for a detected configuration conflict (FFI-safe).
///
/// Mirrors `crate::config::conflict::ConflictLevel` with a stable `#[repr(u8)]`
/// layout for the C side.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FFIConflictLevel {
    /// Hard error — `nginx -t` must fail.
    Error = 0,
    /// Advisory warning — logged but does not block startup.
    Warning = 1,
}

/// A single detected configuration conflict (FFI-safe).
///
/// Contains the severity level and a pointer to a UTF-8 message describing
/// the incompatibility. The message bytes are owned by the containing
/// [`FFIConflictList`] and remain valid until `markdown_free_conflicts`
/// is called.
#[repr(C)]
pub struct FFIConflict {
    /// Severity: error blocks startup, warning is advisory.
    pub level: FFIConflictLevel,
    /// Pointer to UTF-8 message bytes (NOT NUL-terminated).
    pub message: *const u8,
    /// Length of the message in bytes.
    pub message_len: usize,
}

/// List of detected conflicts returned from `markdown_detect_conflicts`.
///
/// The caller must free this with `markdown_free_conflicts`. When `count == 0`,
/// `conflicts` is NULL (Rule 53: empty results return NULL).
#[repr(C)]
pub struct FFIConflictList {
    /// Pointer to array of `FFIConflict` entries (NULL when `count == 0`).
    pub conflicts: *mut FFIConflict,
    /// Number of entries in the array.
    pub count: usize,
}

/// Explicit user-set configuration flags for conflict detection (FFI-safe).
///
/// Each field uses a sentinel value to indicate "not explicitly set":
/// - Enum fields: `255` means not set (valid discriminants are 0..3).
/// - Integer fields: `u64::MAX` / `u32::MAX` means not set.
/// - Boolean fields: `255` means not set (0 = false, 1 = true).
///
/// The C side populates only fields that the user explicitly configured via
/// `markdown_*` directives; all other fields remain at their sentinel values.
#[repr(C)]
pub struct FFIExplicitConfig {
    /// `markdown_accept`: 0=strict, 1=wildcard, 2=force; 255=not set.
    pub accept: u8,
    /// `markdown_cache_validation`: 0=off, 1=ims_only, 2=full; 255=not set.
    pub cache_validation: u8,
    /// `markdown_streaming`: 0=off, 1=auto, 2=force; 255=not set.
    pub streaming: u8,
    /// `markdown_limits memory=` in bytes; `u64::MAX`=not set.
    pub limits_memory_bytes: u64,
    /// `markdown_limits timeout=` in milliseconds; `u64::MAX`=not set.
    pub limits_timeout_ms: u64,
    /// `markdown_limits streaming_buffer=` in bytes; `u64::MAX`=not set.
    pub limits_streaming_buffer_bytes: u64,
    /// `markdown_limits max_inflight=`; `u32::MAX`=not set.
    pub limits_max_inflight: u32,
    /// `markdown_error_policy`: 0=pass, 1=fail_closed; 255=not set.
    pub error_policy: u8,
    /// `markdown_diagnostics`: 0=off, 1=on; 255=not set.
    pub diagnostics: u8,
}

/// Effective configuration after merge (FFI-safe).
///
/// All fields are concrete (no sentinels). This is the fully-resolved
/// configuration the C side uses at runtime.
#[repr(C)]
pub struct FFIEffectiveConfig {
    /// Effective accept mode: 0=strict, 1=wildcard, 2=force.
    pub accept: u8,
    /// Effective cache_validation: 0=off, 1=ims_only, 2=full.
    pub cache_validation: u8,
    /// Effective streaming: 0=off, 1=auto, 2=force.
    pub streaming: u8,
    /// Effective memory limit in bytes.
    pub limits_memory_bytes: u64,
    /// Effective timeout in milliseconds.
    pub limits_timeout_ms: u64,
    /// Effective streaming buffer size in bytes.
    pub limits_streaming_buffer_bytes: u64,
    /// Effective max inflight conversions.
    pub limits_max_inflight: u32,
    /// Effective error policy: 0=pass, 1=fail_closed.
    pub error_policy: u8,
    /// Effective diagnostics: 0=off, 1=on.
    pub diagnostics: u8,
}

/// Sentinel value for "not set" in FFI enum fields (u8).
pub const FFI_CONFIG_NOT_SET_U8: u8 = 255;
/// Sentinel value for "not set" in FFI u64 fields.
pub const FFI_CONFIG_NOT_SET_U64: u64 = u64::MAX;
/// Sentinel value for "not set" in FFI u32 fields.
pub const FFI_CONFIG_NOT_SET_U32: u32 = u32::MAX;

/// Profile discriminant: no profile active (use built-in defaults).
pub const FFI_PROFILE_NONE: u8 = 0;
/// Profile discriminant: `strict_cache` (CDN/caching proxy).
pub const FFI_PROFILE_STRICT_CACHE: u8 = 1;
/// Profile discriminant: `balanced` (recommended default).
pub const FFI_PROFILE_BALANCED: u8 = 2;
/// Profile discriminant: `streaming_first` (AI agent workloads).
pub const FFI_PROFILE_STREAMING_FIRST: u8 = 3;

#[cfg(test)]
mod layout_tests {
    use super::*;

    #[test]
    fn test_markdown_options_layout() {
        use std::mem::{align_of, offset_of, size_of};

        assert_eq!(size_of::<MarkdownOptions>(), 128);
        assert_eq!(align_of::<MarkdownOptions>(), 8);

        assert_eq!(offset_of!(MarkdownOptions, flavor), 0);
        assert_eq!(offset_of!(MarkdownOptions, timeout_ms), 4);
        assert_eq!(offset_of!(MarkdownOptions, generate_etag), 8);
        assert_eq!(offset_of!(MarkdownOptions, estimate_tokens), 9);
        assert_eq!(offset_of!(MarkdownOptions, front_matter), 10);
        assert_eq!(offset_of!(MarkdownOptions, content_type), 16);
        assert_eq!(offset_of!(MarkdownOptions, content_type_len), 24);
        assert_eq!(offset_of!(MarkdownOptions, base_url), 32);
        assert_eq!(offset_of!(MarkdownOptions, base_url_len), 40);
        assert_eq!(offset_of!(MarkdownOptions, streaming_budget), 48);
        assert_eq!(offset_of!(MarkdownOptions, prune_noise), 56);
        assert_eq!(offset_of!(MarkdownOptions, prune_selectors), 64);
        assert_eq!(offset_of!(MarkdownOptions, prune_selector_len), 72);
        assert_eq!(offset_of!(MarkdownOptions, prune_protection_selectors), 80);
        assert_eq!(
            offset_of!(MarkdownOptions, prune_protection_selector_len),
            88
        );
        assert_eq!(offset_of!(MarkdownOptions, memory_budget), 96);
        assert_eq!(offset_of!(MarkdownOptions, llm_provider), 104);
        assert_eq!(offset_of!(MarkdownOptions, chars_per_token_fixed), 105);
        assert_eq!(offset_of!(MarkdownOptions, parse_timeout_ms), 108);
        assert_eq!(offset_of!(MarkdownOptions, parser_memory_budget), 112);
        assert_eq!(offset_of!(MarkdownOptions, flush_threshold), 120);
    }

    #[test]
    fn test_markdown_result_layout() {
        use std::mem::{align_of, offset_of, size_of};

        assert_eq!(size_of::<MarkdownResult>(), 64);
        assert_eq!(align_of::<MarkdownResult>(), 8);

        assert_eq!(offset_of!(MarkdownResult, markdown), 0);
        assert_eq!(offset_of!(MarkdownResult, markdown_len), 8);
        assert_eq!(offset_of!(MarkdownResult, etag), 16);
        assert_eq!(offset_of!(MarkdownResult, etag_len), 24);
        assert_eq!(offset_of!(MarkdownResult, token_estimate), 32);
        assert_eq!(offset_of!(MarkdownResult, error_code), 36);
        assert_eq!(offset_of!(MarkdownResult, error_message), 40);
        assert_eq!(offset_of!(MarkdownResult, error_len), 48);
        assert_eq!(offset_of!(MarkdownResult, peak_memory_estimate), 56);
    }

    #[test]
    fn test_ffi_accept_result_layout() {
        use std::mem::{align_of, size_of};

        assert_eq!(size_of::<FFIAcceptResult>(), 2);
        assert_eq!(align_of::<FFIAcceptResult>(), 1);
    }

    #[test]
    fn test_ffi_conditional_result_layout() {
        use std::mem::{align_of, size_of};

        assert_eq!(size_of::<FFIConditionalResult>(), 8);
        assert_eq!(align_of::<FFIConditionalResult>(), 4);
    }

    #[test]
    fn test_ffi_decision_result_layout() {
        use std::mem::{align_of, size_of};

        assert_eq!(size_of::<FFIDecisionResult>(), 2);
        assert_eq!(align_of::<FFIDecisionResult>(), 1);
    }

    #[test]
    fn test_ffi_header_entry_layout() {
        use std::mem::{align_of, offset_of, size_of};

        assert_eq!(size_of::<FFIHeaderEntry>(), 40);
        assert_eq!(align_of::<FFIHeaderEntry>(), 8);

        assert_eq!(offset_of!(FFIHeaderEntry, op_type), 0);
        assert_eq!(offset_of!(FFIHeaderEntry, key), 8);
        assert_eq!(offset_of!(FFIHeaderEntry, key_len), 16);
        assert_eq!(offset_of!(FFIHeaderEntry, value), 24);
        assert_eq!(offset_of!(FFIHeaderEntry, value_len), 32);
    }

    #[test]
    fn test_ffi_header_plan_layout() {
        use std::mem::{align_of, offset_of, size_of};

        assert_eq!(size_of::<FFIHeaderPlan>(), 24);
        assert_eq!(align_of::<FFIHeaderPlan>(), 8);

        assert_eq!(offset_of!(FFIHeaderPlan, handle), 0);
        assert_eq!(offset_of!(FFIHeaderPlan, entries), 8);
        assert_eq!(offset_of!(FFIHeaderPlan, count), 16);
    }

    #[test]
    fn test_ffi_decomp_result_layout() {
        use std::mem::{align_of, offset_of, size_of};

        assert_eq!(size_of::<FFIDecompResult>(), 24);
        assert_eq!(align_of::<FFIDecompResult>(), 8);

        assert_eq!(offset_of!(FFIDecompResult, output), 0);
        assert_eq!(offset_of!(FFIDecompResult, output_len), 8);
        assert_eq!(offset_of!(FFIDecompResult, error_category), 16);
    }

    #[test]
    fn test_ffi_base_url_input_layout() {
        use std::mem::{align_of, offset_of, size_of};

        assert_eq!(size_of::<FFIBaseUrlInput>(), 96);
        assert_eq!(align_of::<FFIBaseUrlInput>(), 8);

        assert_eq!(offset_of!(FFIBaseUrlInput, source_ip), 0);
        assert_eq!(offset_of!(FFIBaseUrlInput, source_ip_len), 8);
        assert_eq!(offset_of!(FFIBaseUrlInput, trusted), 16);
        assert_eq!(offset_of!(FFIBaseUrlInput, forwarded), 24);
        assert_eq!(offset_of!(FFIBaseUrlInput, forwarded_len), 32);
        assert_eq!(offset_of!(FFIBaseUrlInput, x_forwarded_proto), 40);
        assert_eq!(offset_of!(FFIBaseUrlInput, x_forwarded_proto_len), 48);
        assert_eq!(offset_of!(FFIBaseUrlInput, x_forwarded_host), 56);
        assert_eq!(offset_of!(FFIBaseUrlInput, x_forwarded_host_len), 64);
        assert_eq!(offset_of!(FFIBaseUrlInput, host), 72);
        assert_eq!(offset_of!(FFIBaseUrlInput, host_len), 80);
        assert_eq!(offset_of!(FFIBaseUrlInput, is_unix_socket), 88);
        assert_eq!(offset_of!(FFIBaseUrlInput, trusted_configured), 89);
    }

    #[test]
    fn test_ffi_base_url_decision_layout() {
        use std::mem::{align_of, offset_of, size_of};

        assert_eq!(size_of::<FFIBaseUrlDecision>(), 16);
        assert_eq!(align_of::<FFIBaseUrlDecision>(), 8);

        assert_eq!(offset_of!(FFIBaseUrlDecision, base_url_len), 0);
        assert_eq!(offset_of!(FFIBaseUrlDecision, reason), 8);
        assert_eq!(offset_of!(FFIBaseUrlDecision, source), 9);
    }

    #[test]
    fn test_error_codes_distinct() {
        let codes = [
            ERROR_SUCCESS,
            ERROR_PARSE,
            ERROR_ENCODING,
            ERROR_TIMEOUT,
            ERROR_MEMORY_LIMIT,
            ERROR_INVALID_INPUT,
            ERROR_DECOMPRESSION_BUDGET_EXCEEDED,
            ERROR_PARSE_TIMEOUT,
            ERROR_PARSE_BUDGET_EXCEEDED,
            ERROR_DECOMPRESSION_FORMAT_ERROR,
            ERROR_DECOMPRESSION_TRUNCATED_INPUT,
            ERROR_DECOMPRESSION_IO_ERROR,
            ERROR_INTERNAL,
        ];

        for i in 0..codes.len() {
            for j in (i + 1)..codes.len() {
                assert_ne!(
                    codes[i], codes[j],
                    "Error codes {} and {} collide",
                    codes[i], codes[j]
                );
            }
        }
    }

    #[test]
    fn test_negotiate_reason_codes_distinct() {
        let reasons = [
            NEGOTIATE_REASON_CONVERT,
            NEGOTIATE_REASON_NO_ACCEPT,
            NEGOTIATE_REASON_LOWER_Q,
            NEGOTIATE_REASON_EXPLICIT_REJECT,
            NEGOTIATE_REASON_MALFORMED,
        ];

        for i in 0..reasons.len() {
            for j in (i + 1)..reasons.len() {
                assert_ne!(
                    reasons[i], reasons[j],
                    "Reason codes {} and {} collide",
                    reasons[i], reasons[j]
                );
            }
        }
    }

    /// [A04.7] FFI mapping closure test: Rust ERROR_* constant count == C define count.
    ///
    /// Ensures no Rust error variant is missing a C-side define.
    /// The expected count (10 for non-streaming, 13 with streaming) must be
    /// updated whenever a new error code is added to either side.
    #[test]
    fn test_error_code_count_matches_c_defines() {
        // Non-streaming error codes defined in this module
        let base_codes: &[u32] = &[
            ERROR_SUCCESS,
            ERROR_PARSE,
            ERROR_ENCODING,
            ERROR_TIMEOUT,
            ERROR_MEMORY_LIMIT,
            ERROR_INVALID_INPUT,
            ERROR_DECOMPRESSION_BUDGET_EXCEEDED,
            ERROR_PARSE_TIMEOUT,
            ERROR_PARSE_BUDGET_EXCEEDED,
            ERROR_DECOMPRESSION_FORMAT_ERROR,
            ERROR_DECOMPRESSION_TRUNCATED_INPUT,
            ERROR_DECOMPRESSION_IO_ERROR,
            ERROR_INTERNAL,
        ];

        // Expected count of non-streaming ERROR_* constants in ffi/abi.rs
        let expected_base_count: usize = 13;
        assert_eq!(
            base_codes.len(),
            expected_base_count,
            "Rust base error code count ({}) must match C #define count ({}). \
             If you added a new error code, update both this array and the C header.",
            base_codes.len(),
            expected_base_count
        );

        // With streaming feature, 3 additional codes exist (6, 7, 8)
        #[cfg(feature = "streaming")]
        {
            let streaming_codes: &[u32] = &[
                ERROR_BUDGET_EXCEEDED,
                ERROR_STREAMING_FALLBACK,
                ERROR_POST_COMMIT,
            ];
            let total = base_codes.len() + streaming_codes.len();
            let expected_total: usize = 16;
            assert_eq!(
                total, expected_total,
                "Total error code count with streaming ({total}) must match expected ({expected_total})"
            );
        }
    }

    #[test]
    fn test_negotiate_reason_count_matches_c_defines() {
        let rust_reason_count = 5;
        let reasons = [
            NEGOTIATE_REASON_CONVERT,
            NEGOTIATE_REASON_NO_ACCEPT,
            NEGOTIATE_REASON_LOWER_Q,
            NEGOTIATE_REASON_EXPLICIT_REJECT,
            NEGOTIATE_REASON_MALFORMED,
        ];
        assert_eq!(
            reasons.len(),
            rust_reason_count,
            "Rust negotiate reason count ({}) must match C #define count ({})",
            reasons.len(),
            rust_reason_count
        );
    }

    #[test]
    fn test_skip_reason_count_matches_decision_export() {
        use crate::decision::SkipReason;
        let skip_reasons = [
            SkipReason::SkipAccept,
            SkipReason::SkipNoAccept,
            SkipReason::SkipConditional,
            SkipReason::FailDecompression,
            SkipReason::ParseTimeout,
            SkipReason::ParseBudgetExceeded,
            SkipReason::NotEligible,
            SkipReason::Disabled,
        ];
        assert_eq!(
            skip_reasons.len(),
            8,
            "SkipReason variant count ({}) must match FFI export reason_code range (1..=8)",
            skip_reasons.len()
        );
    }
}
