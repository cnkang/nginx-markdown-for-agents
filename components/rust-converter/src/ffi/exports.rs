//! Public FFI export functions callable from C.
//!
//! This module contains the `#[unsafe(no_mangle)] extern "C"` functions that
//! form the public C API of the conversion library.
//!
//! `markdown_convert` is the primary entry point and wraps the internal
//! conversion logic with:
//!
//! 1. **Input validation** — NULL pointer checks via `required_ref`/`required_bytes`
//! 2. **Panic catching** — `panic::catch_unwind` to prevent Rust panics from
//!    unwinding into C frames (undefined behavior per the FFI contract)
//! 3. **Result marshalling** — conversion of Rust `Result` into C `MarkdownResult`
//!
//! The remaining exports are lifecycle helpers:
//! - `markdown_converter_new` — safe constructor that allocates a converter
//!   handle (uses `catch_unwind` but does not perform input validation or
//!   result marshalling)
//! - `markdown_converter_free` / `markdown_result_free` — release functions
//!   that deallocate handles and result buffers without conversion or
//!   panic-catching
//!
//! # Exported Functions
//!
//! | Function | Purpose |
//! |----------|---------|
//! | `markdown_converter_new` | Allocate a converter handle |
//! | `markdown_convert` | Full HTML-to-Markdown conversion |
//! | `markdown_converter_free` | Release a converter handle |
//! | `markdown_result_free` | Release conversion result buffers |
//!
//! # Safety
//!
//! All functions in this module are `unsafe` (either explicitly or by
//! accepting raw pointers). Each function's `# Safety` doc section
//! specifies the preconditions the C caller must uphold.

use std::panic;
use std::ptr;

use crate::error::ConversionError;

use super::abi::{
    DECOMP_CATEGORY_INVALID_ARGS, ERROR_INTERNAL, FFIAcceptResult, FFIConditionalResult,
    FFIDecisionResult, FFIDecompResult, FFIHeaderEntry, FFIHeaderPlan, FFIHeaderPlanHandle,
    MarkdownConverterHandle, MarkdownOptions, MarkdownResult, NEGOTIATE_REASON_CONVERT,
    NEGOTIATE_REASON_EXPLICIT_REJECT, NEGOTIATE_REASON_LOWER_Q, NEGOTIATE_REASON_MALFORMED,
    NEGOTIATE_REASON_NO_ACCEPT,
};
use super::convert::convert_inner;
use super::memory::{free_buffer, reset_result, set_error_result, set_success_result};
use super::options::{required_bytes, required_ref};

struct HeaderPlanOwned {
    entries: Vec<FFIHeaderEntry>,
    key_storage: Vec<Box<[u8]>>,
    value_storage: Vec<Box<[u8]>>,
}

/// Allocate a new converter handle for use across multiple FFI calls.
///
/// # Safety
///
/// Returns a raw pointer that must eventually be freed with
/// `markdown_converter_free()`.
///
/// # Returns
///
/// Returns a non-NULL handle on success. Returns NULL only when
/// `MarkdownConverterHandle::new()` panics and that panic is caught by
/// `catch_unwind()`. On stable Rust, allocator out-of-memory aborts the process
/// instead of unwinding, so OOM is not caught here and does not produce NULL.
#[unsafe(no_mangle)]
pub extern "C" fn markdown_converter_new() -> *mut MarkdownConverterHandle {
    let result = panic::catch_unwind(|| Box::into_raw(Box::new(MarkdownConverterHandle::new())));
    match result {
        Ok(ptr) => ptr,
        Err(_) => ptr::null_mut(),
    }
}

/// Convert an HTML byte buffer into Markdown using the provided converter handle.
///
/// # Safety
///
/// The caller must ensure that:
/// - `handle` points to a live converter created by `markdown_converter_new()`
/// - `options` points to a valid `MarkdownOptions`
/// - `result` points to writable storage for a `MarkdownResult` whose owned
///   buffers are either NULL/zero-length or were previously returned by this API
/// - `html` either points to `html_len` readable bytes or is NULL when `html_len == 0`
/// - `result` is not concurrently mutated while this function is executing
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_convert(
    handle: *mut MarkdownConverterHandle,
    html: *const u8,
    html_len: usize,
    options: *const MarkdownOptions,
    result: *mut MarkdownResult,
) {
    if result.is_null() {
        return;
    }

    // SAFETY: `result` was validated as non-NULL above.
    let result_ref = unsafe { &mut *result };
    free_buffer(&mut result_ref.markdown, &mut result_ref.markdown_len);
    free_buffer(&mut result_ref.etag, &mut result_ref.etag_len);
    free_buffer(&mut result_ref.error_message, &mut result_ref.error_len);
    reset_result(result_ref);

    let panic_result = panic::catch_unwind(|| -> Result<_, ConversionError> {
        // SAFETY: Pointers originate from the C caller who upholds the FFI
        // contract documented in this function's # Safety section.
        let handle_ref = unsafe { required_ref(handle.cast_const(), "Converter handle") }?;
        let options_ref = unsafe { required_ref(options, "Options") }?;
        let html_slice = unsafe { required_bytes(html, html_len, "HTML") }?;
        convert_inner(handle_ref, html_slice, options_ref)
    });

    match panic_result {
        Ok(Ok(output)) => set_success_result(result_ref, output),
        Ok(Err(err)) => set_error_result(result_ref, err.code(), err.to_string()),
        Err(_) => set_error_result(
            result_ref,
            ERROR_INTERNAL,
            "Internal panic during conversion".to_string(),
        ),
    }
}

/// Release buffers owned by a `MarkdownResult` and reset it to the empty state.
///
/// # Safety
///
/// The caller must ensure that `result` either is NULL or points to a valid
/// `MarkdownResult` previously initialized by `markdown_convert()`. Passing the
/// same result twice is allowed because the function resets pointers to NULL.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_result_free(result: *mut MarkdownResult) {
    if result.is_null() {
        return;
    }

    // SAFETY: `result` was validated as non-NULL above.
    let result_ref = unsafe { &mut *result };
    free_buffer(&mut result_ref.markdown, &mut result_ref.markdown_len);
    free_buffer(&mut result_ref.etag, &mut result_ref.etag_len);
    free_buffer(&mut result_ref.error_message, &mut result_ref.error_len);
    result_ref.token_estimate = 0;
    result_ref.error_code = 0;
    result_ref.peak_memory_estimate = 0;
}

/// Destroy a converter handle previously returned by `markdown_converter_new()`.
///
/// # Safety
///
/// The caller must ensure that `handle` either is NULL or was returned by
/// `markdown_converter_new()` and has not already been freed. The handle must
/// not be used after this call returns.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_converter_free(handle: *mut MarkdownConverterHandle) {
    if handle.is_null() {
        return;
    }

    // SAFETY: `handle` was validated as non-NULL above and originated from `Box::into_raw`.
    unsafe { drop(Box::from_raw(handle)) };
}

/// Perform Accept header content negotiation.
///
/// Parses the client `Accept` header and determines whether the client
/// prefers `text/markdown` over `text/html`, using RFC 7231 §5.3.2
/// q-value comparison.
///
/// # Parameters
///
/// - `on_wildcard`: Controls wildcard (all-types MIME) handling.
///   - `0` (NEGOTIATE_WILDCARD_STRICT): wildcards do NOT match text/markdown;
///     only explicit `text/markdown` triggers conversion.
///   - `1` (NEGOTIATE_WILDCARD_ALLOW): wildcards match text/markdown,
///     so a wildcard Accept header will trigger conversion.
///
/// # Safety
///
/// The caller must ensure that:
/// - `accept_header` either points to `accept_header_len` readable bytes
///   or is NULL when `accept_header_len == 0`
/// - `result` points to writable storage for a `FFIAcceptResult`
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_negotiate_accept(
    accept_header: *const u8,
    accept_header_len: usize,
    on_wildcard: u8,
    result: *mut FFIAcceptResult,
) {
    if result.is_null() {
        return;
    }

    let result_ref = unsafe { &mut *result };

    let header_str = if accept_header.is_null() || accept_header_len == 0 {
        ""
    } else {
        match std::str::from_utf8(unsafe {
            std::slice::from_raw_parts(accept_header, accept_header_len)
        }) {
            Ok(s) => s,
            Err(_) => {
                result_ref.should_convert = 0;
                result_ref.reason = NEGOTIATE_REASON_MALFORMED;
                return;
            }
        }
    };

    let wildcard = on_wildcard != 0;

    use crate::negotiator::{NegotiationResult, PassthroughReason, negotiate};
    match negotiate(header_str, wildcard) {
        NegotiationResult::Convert => {
            result_ref.should_convert = 1;
            result_ref.reason = NEGOTIATE_REASON_CONVERT;
        }
        NegotiationResult::Passthrough { reason } => {
            result_ref.should_convert = 0;
            result_ref.reason = match reason {
                PassthroughReason::NoAcceptHeader => NEGOTIATE_REASON_NO_ACCEPT,
                PassthroughReason::LowerQValue => NEGOTIATE_REASON_LOWER_Q,
                PassthroughReason::ExplicitReject => NEGOTIATE_REASON_EXPLICIT_REJECT,
                PassthroughReason::MalformedHeader => NEGOTIATE_REASON_MALFORMED,
            };
        }
    }
}

/// Evaluate HTTP conditional request headers (If-None-Match / If-Modified-Since).
///
/// Returns the conditional result through the `result` output parameter.
///
/// # Safety
///
/// The caller must ensure that:
/// - All string pointers either point to readable UTF-8 bytes of the given length
///   or are NULL when the corresponding length is 0
/// - `result` points to writable storage for a `FFIConditionalResult`
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_check_conditional(
    if_none_match: *const u8,
    if_none_match_len: usize,
    entity_etag: *const u8,
    entity_etag_len: usize,
    if_modified_since: *const u8,
    if_modified_since_len: usize,
    last_modified: *const u8,
    last_modified_len: usize,
    result: *mut FFIConditionalResult,
) {
    if result.is_null() {
        return;
    }

    let result_ref = unsafe { &mut *result };

    let inm = unsafe { optional_str(if_none_match, if_none_match_len) };
    let etag = unsafe { optional_str(entity_etag, entity_etag_len) };
    let ims = unsafe { optional_str(if_modified_since, if_modified_since_len) };
    let lm = unsafe { optional_str(last_modified, last_modified_len) };

    use crate::conditional::{ConditionalResult, evaluate_conditional};
    match evaluate_conditional(inm, etag, ims, lm) {
        ConditionalResult::NotModified => {
            result_ref.result_code = 0;
            result_ref.matched_etag_len = 0;
        }
        ConditionalResult::Proceed => {
            result_ref.result_code = 1;
            result_ref.matched_etag_len = 0;
        }
    }
}

/// Evaluate the conversion decision engine.
///
/// Returns the decision through the `result` output parameter.
///
/// # Safety
///
/// The caller must ensure that `result` points to writable storage
/// for a `FFIDecisionResult`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_make_decision(
    enabled: u8,
    eligible: u8,
    accept_prefers_markdown: u8,
    accept_header_present: u8,
    conditional_not_modified: u8,
    decompression_ok: u8,
    parse_timed_out: u8,
    parse_budget_exceeded: u8,
    result: *mut FFIDecisionResult,
) {
    if result.is_null() {
        return;
    }

    let result_ref = unsafe { &mut *result };

    use crate::decision::reason_code::ReasonCode;
    use crate::decision::{Decision, DecisionContext, SkipReason, make_decision};
    let ctx = DecisionContext {
        enabled: enabled != 0,
        eligible: eligible != 0,
        accept_prefers_markdown: accept_prefers_markdown != 0,
        accept_header_present: accept_header_present != 0,
        conditional_not_modified: conditional_not_modified != 0,
        decompression_ok: decompression_ok != 0,
        parse_timed_out: parse_timed_out != 0,
        parse_budget_exceeded: parse_budget_exceeded != 0,
    };

    match make_decision(&ctx) {
        Decision::Convert => {
            result_ref.decision = 0;
            /* Converted == ReasonCode::Converted (0). */
            result_ref.reason_code = ReasonCode::Converted.discriminant() as u8;
        }
        Decision::Skip(reason) => {
            result_ref.decision = 1;
            /* Map the pre-conversion SkipReason onto the canonical
             * ReasonCode discriminants (the single source of truth in
             * decision::reason_code). This lets C callers feed
             * reason_code directly into markdown_reason_code_str() /
             * markdown_reason_code_metric_key() without a second
             * translation table. Post-conversion failure categories
             * (streaming/decompression codes) are reported through
             * separate FFI paths (streaming result codes,
             * FFIDecompResult.error_category), not this decision result.
             * All mapped discriminants are <= 15, so they fit in the
             * uint8_t reason_code field. */
            let canonical = match reason {
                SkipReason::SkipAccept => ReasonCode::SkippedAccept,
                SkipReason::SkipNoAccept => ReasonCode::SkippedNoAccept,
                SkipReason::SkipConditional => ReasonCode::SkippedConditional,
                SkipReason::FailDecompression => ReasonCode::FailedDecompression,
                SkipReason::ParseTimeout => ReasonCode::ParseTimeout,
                SkipReason::ParseBudgetExceeded => ReasonCode::ParseBudgetExceeded,
                SkipReason::NotEligible => ReasonCode::NotEligible,
                SkipReason::Disabled => ReasonCode::Disabled,
            };
            result_ref.reason_code = canonical.discriminant() as u8;
        }
    }
}

/// Build a header plan for a successful Markdown conversion.
///
/// The returned plan contains Rust-owned buffers. The C caller must release
/// the plan via `markdown_header_plan_free`.
///
/// # Safety
///
/// The caller must ensure that:
/// - `content_type` points to readable UTF-8 bytes of `content_type_len`
/// - `result` points to writable storage for a `FFIHeaderPlan`
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_build_header_plan(
    content_type: *const u8,
    content_type_len: usize,
    has_etag: u8,
    result: *mut FFIHeaderPlan,
) {
    if result.is_null() {
        return;
    }

    let result_ref = unsafe { &mut *result };

    /* Save and release any previously-held plan to avoid leaking the old handle.
     * Zero the result struct BEFORE dropping the old handle so that no field
     * ever points into freed memory (eliminates dangling-pointer window). */
    let old_handle = result_ref.handle;
    unsafe { ptr::write(result_ref, std::mem::zeroed()) };
    if !old_handle.is_null() {
        unsafe { drop(Box::from_raw(old_handle as *mut HeaderPlanOwned)) };
    }

    let ct = if content_type.is_null() || content_type_len == 0 {
        "text/markdown; charset=utf-8"
    } else {
        std::str::from_utf8(unsafe { std::slice::from_raw_parts(content_type, content_type_len) })
            .unwrap_or("text/markdown; charset=utf-8")
    };

    use crate::header_plan::{HeaderOp, HeaderPlan};
    let plan = HeaderPlan::for_markdown_conversion(ct, has_etag != 0);

    let mut owned = HeaderPlanOwned {
        entries: Vec::with_capacity(plan.ops.len()),
        key_storage: Vec::with_capacity(plan.ops.len()),
        value_storage: Vec::with_capacity(plan.ops.len()),
    };

    for op in &plan.ops {
        match op {
            HeaderOp::Set { name, value } => {
                let mut key_vec = name.as_bytes().to_vec();
                key_vec.push(0); /* NUL-terminate per FFI contract */
                let mut val_vec = value.as_bytes().to_vec();
                val_vec.push(0); /* NUL-terminate per FFI contract */
                let key_len = key_vec.len() - 1; /* exclude NUL from len */
                let val_len = val_vec.len() - 1;
                owned.key_storage.push(key_vec.into_boxed_slice());
                owned.value_storage.push(val_vec.into_boxed_slice());
                let key = &owned.key_storage[owned.key_storage.len() - 1];
                let val = &owned.value_storage[owned.value_storage.len() - 1];
                owned.entries.push(FFIHeaderEntry {
                    op_type: 0,
                    key: key.as_ptr(),
                    key_len,
                    value: val.as_ptr(),
                    value_len: val_len,
                });
            }
            HeaderOp::Delete { name } => {
                let mut key_vec = name.as_bytes().to_vec();
                key_vec.push(0); /* NUL-terminate per FFI contract */
                let key_len = key_vec.len() - 1;
                owned.key_storage.push(key_vec.into_boxed_slice());
                let key = &owned.key_storage[owned.key_storage.len() - 1];
                owned.entries.push(FFIHeaderEntry {
                    op_type: 1,
                    key: key.as_ptr(),
                    key_len,
                    value: std::ptr::null(),
                    value_len: 0,
                });
            }
            HeaderOp::SetEtagPlaceholder => {
                owned.entries.push(FFIHeaderEntry {
                    op_type: 2,
                    key: std::ptr::null(),
                    key_len: 0,
                    value: std::ptr::null(),
                    value_len: 0,
                });
            }
        }
    }

    if owned.entries.is_empty() {
        result_ref.handle = std::ptr::null_mut();
        result_ref.entries = std::ptr::null();
        result_ref.count = 0;
    } else {
        result_ref.entries = owned.entries.as_ptr();
        result_ref.count = owned.entries.len();
        result_ref.handle = Box::into_raw(Box::new(owned)) as *mut FFIHeaderPlanHandle;
    }
}

/// Validate a URL for use in Markdown link destinations.
///
/// Returns 1 if the URL is safe, 0 if it contains control characters
/// or dangerous schemes.
///
/// # Safety
///
/// The caller must ensure that `url` either points to `url_len` readable
/// bytes or is NULL when `url_len == 0`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_validate_url(url: *const u8, url_len: usize) -> u8 {
    let url_str = if url.is_null() || url_len == 0 {
        return 1;
    } else {
        match std::str::from_utf8(unsafe { std::slice::from_raw_parts(url, url_len) }) {
            Ok(s) => s,
            Err(_) => return 0,
        }
    };

    use crate::security::validate_link_url;
    if validate_link_url(url_str).is_ok() {
        1
    } else {
        0
    }
}

/// Check if a URL uses a dangerous scheme (javascript:, data:, etc.).
///
/// Returns 1 if dangerous, 0 if safe.
///
/// # Safety
///
/// The caller must ensure that `url` either points to `url_len` readable
/// bytes or is NULL when `url_len == 0`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_is_dangerous_url(url: *const u8, url_len: usize) -> u8 {
    let url_str = if url.is_null() || url_len == 0 {
        return 0;
    } else {
        match std::str::from_utf8(unsafe { std::slice::from_raw_parts(url, url_len) }) {
            Ok(s) => s,
            Err(_) => return 1,
        }
    };

    use crate::security::SecurityValidator;
    let validator = SecurityValidator::new();
    if validator.is_dangerous_url(url_str) {
        1
    } else {
        0
    }
}

/// Build a base URL from X-Forwarded-Host and X-Forwarded-Proto headers.
///
/// Parses the forwarded headers and constructs a validated base URL
/// (e.g., "https://api.example.com"). The result is written into the
/// caller-provided buffer. Returns the number of bytes written, or 0
/// if the headers are absent, empty, or contain invalid characters.
///
/// # Safety
///
/// The caller must ensure that:
/// - `x_forwarded_host` either points to `host_len` readable bytes or is NULL
/// - `x_forwarded_proto` either points to `proto_len` readable bytes or is NULL
/// - `out_buf` points to at least `out_buf_cap` writable bytes
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_build_base_url(
    x_forwarded_host: *const u8,
    host_len: usize,
    x_forwarded_proto: *const u8,
    proto_len: usize,
    out_buf: *mut u8,
    out_buf_cap: usize,
) -> usize {
    if out_buf.is_null() || out_buf_cap == 0 {
        return 0;
    }

    let host_str = if x_forwarded_host.is_null() || host_len == 0 {
        None
    } else {
        std::str::from_utf8(unsafe { std::slice::from_raw_parts(x_forwarded_host, host_len) }).ok()
    };

    let proto_str = if x_forwarded_proto.is_null() || proto_len == 0 {
        None
    } else {
        std::str::from_utf8(unsafe { std::slice::from_raw_parts(x_forwarded_proto, proto_len) })
            .ok()
    };

    use crate::security::parse_forwarded_headers;
    match parse_forwarded_headers(host_str, proto_str) {
        Some((scheme, host)) => {
            let url = format!("{scheme}://{host}");
            let bytes = url.as_bytes();
            if bytes.len() > out_buf_cap {
                return 0;
            }
            unsafe { std::ptr::copy_nonoverlapping(bytes.as_ptr(), out_buf, bytes.len()) };
            bytes.len()
        }
        None => 0,
    }
}

/// Release a header plan previously returned by `markdown_build_header_plan`.
///
/// # Safety
///
/// The caller must ensure that `plan` either is NULL or points to a valid
/// `FFIHeaderPlan` previously returned by `markdown_build_header_plan`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_header_plan_free(plan: *mut FFIHeaderPlan) {
    if plan.is_null() {
        return;
    }

    let plan_ref = unsafe { &mut *plan };
    if !plan_ref.handle.is_null() {
        unsafe { drop(Box::from_raw(plan_ref.handle as *mut HeaderPlanOwned)) };
    }
    plan_ref.handle = std::ptr::null_mut();
    plan_ref.entries = std::ptr::null();
    plan_ref.count = 0;
}

unsafe fn optional_str<'a>(ptr: *const u8, len: usize) -> Option<&'a str> {
    if ptr.is_null() || len == 0 {
        return None;
    }
    std::str::from_utf8(unsafe { std::slice::from_raw_parts(ptr, len) }).ok()
}

/// Initialize a `MarkdownOptions` struct with sensible defaults.
///
/// C callers **MUST** use this function instead of `memset(&opts, 0, sizeof(opts))`
/// or literal struct initialization (`MarkdownOptions opts = {0}`). The helper
/// guarantees that all fields — including any future tail-appended fields — are
/// set to valid defaults. After calling this function, the caller may override
/// individual fields as needed.
///
/// Default values:
/// - `flavor`: 0 (CommonMark)
/// - `timeout_ms`: 5000 (5 seconds)
/// - `generate_etag`: 1 (enabled)
/// - `estimate_tokens`: 0 (disabled)
/// - `front_matter`: 0 (disabled)
/// - `content_type` / `base_url` / selectors: NULL/0
/// - `streaming_budget`: 0 (use engine default)
/// - `prune_noise`: 0 (disabled)
/// - `memory_budget`: 0 (use per-engine defaults)
/// - `llm_provider`: 0 (default)
/// - `chars_per_token_fixed`: 0 (use default ratio)
///
/// # Safety
///
/// The caller must ensure that `result` points to writable storage
/// for a `MarkdownOptions`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_options_init(result: *mut MarkdownOptions) {
    if result.is_null() {
        return;
    }
    /* Zero all fields first to ensure a clean baseline */
    unsafe { ptr::write(result, std::mem::zeroed()) };
    /* Set sensible non-zero defaults */
    let opts = unsafe { &mut *result };
    opts.timeout_ms = 5000;
    opts.generate_etag = 1;
}

/// Zero-initialize a MarkdownResult struct.
///
/// The C caller should use this instead of manual = {0} or memset
/// to guarantee all fields (including any future tail-appended fields)
/// start in a valid zero state.
///
/// # Safety
///
/// The caller must ensure that `result` points to writable storage
/// for a `MarkdownResult`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_result_init(result: *mut MarkdownResult) {
    if result.is_null() {
        return;
    }
    unsafe { ptr::write(result, std::mem::zeroed()) };
}

/// Zero-initialize an FFIAcceptResult struct.
///
/// # Safety
///
/// The caller must ensure that `result` points to writable storage
/// for an `FFIAcceptResult`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_accept_result_init(result: *mut FFIAcceptResult) {
    if result.is_null() {
        return;
    }
    unsafe { ptr::write(result, std::mem::zeroed()) };
}

/// Zero-initialize an FFIConditionalResult struct.
///
/// # Safety
///
/// The caller must ensure that `result` points to writable storage
/// for an `FFIConditionalResult`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_conditional_result_init(result: *mut FFIConditionalResult) {
    if result.is_null() {
        return;
    }
    unsafe { ptr::write(result, std::mem::zeroed()) };
}

/// Zero-initialize an FFIDecisionResult struct.
///
/// # Safety
///
/// The caller must ensure that `result` points to writable storage
/// for an `FFIDecisionResult`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_decision_result_init(result: *mut FFIDecisionResult) {
    if result.is_null() {
        return;
    }
    unsafe { ptr::write(result, std::mem::zeroed()) };
}

/// Zero-initialize an FFIHeaderPlan struct.
///
/// # Safety
///
/// The caller must ensure that `result` points to writable storage
/// for an `FFIHeaderPlan`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_header_plan_init(result: *mut FFIHeaderPlan) {
    if result.is_null() {
        return;
    }
    unsafe { ptr::write(result, std::mem::zeroed()) };
}

/// Perform bounded decompression of compressed input data.
///
/// Decompresses the input using the specified format (gzip, deflate, or brotli)
/// with a hard budget limit on the output size. If the decompressed output
/// would exceed `budget` bytes, decompression is terminated immediately and
/// `BudgetExceeded` is returned.
///
/// On success, the `result` struct is populated with a Rust-owned output buffer.
/// The C caller **must** free this buffer via [`markdown_decompress_free`].
///
/// # Format Codes
///
/// - `0` = gzip (RFC 1952)
/// - `1` = deflate (RFC 1951)
/// - `2` = brotli (RFC 7932)
///
/// # Return Value
///
/// Returns `0` on success. On failure, returns the error category code:
/// - `101` = budget_exceeded (`DECOMP_CATEGORY_BUDGET_EXCEEDED`)
/// - `102` = format_error (`DECOMP_CATEGORY_FORMAT_ERROR`)
/// - `103` = truncated_input (`DECOMP_CATEGORY_TRUNCATED_INPUT`)
/// - `104` = io_error (`DECOMP_CATEGORY_IO_ERROR`)
/// - `105` = invalid arguments — NULL pointers, unknown format (`DECOMP_CATEGORY_INVALID_ARGS`)
///
/// # Safety
///
/// The caller must ensure that:
/// - `input` points to at least `input_len` readable bytes; if `input` is
///   NULL, then `input_len` **must** be 0 (NULL with non-zero `input_len`
///   returns `DECOMP_CATEGORY_INVALID_ARGS`)
/// - `result` points to writable storage for an `FFIDecompResult`
/// - The output buffer in `result` is freed via `markdown_decompress_free`
///   after use (only when return value is 0)
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_decompress_bounded(
    input: *const u8,
    input_len: usize,
    format: u8,
    budget: usize,
    result: *mut FFIDecompResult,
) -> u32 {
    if result.is_null() {
        return DECOMP_CATEGORY_INVALID_ARGS;
    }

    let result_ref = unsafe { &mut *result };
    // Initialize result to safe defaults
    result_ref.output = ptr::null_mut();
    result_ref.output_len = 0;
    result_ref.error_category = 0;

    // Validate format
    let fmt = match crate::decompress::Format::from_u8(format) {
        Some(f) => f,
        None => {
            result_ref.error_category = DECOMP_CATEGORY_INVALID_ARGS;
            return DECOMP_CATEGORY_INVALID_ARGS;
        }
    };

    // Validate input pointer/length consistency:
    //   - NULL with non-zero length is invalid arguments
    //   - NULL with zero length is valid (empty input)
    //   - non-NULL with zero length is valid (empty input)
    //   - non-NULL with non-zero length is valid (normal input)
    let input_slice = if input.is_null() {
        if input_len != 0 {
            result_ref.error_category = DECOMP_CATEGORY_INVALID_ARGS;
            return DECOMP_CATEGORY_INVALID_ARGS;
        }
        &[]
    } else if input_len == 0 {
        &[]
    } else {
        unsafe { std::slice::from_raw_parts(input, input_len) }
    };

    // Perform bounded decompression
    match crate::decompress::decompress_bounded(input_slice, fmt, budget) {
        Ok(decomp_result) => {
            let mut boxed = decomp_result.output.into_boxed_slice();
            result_ref.output_len = boxed.len();
            result_ref.output = boxed.as_mut_ptr();
            result_ref.error_category = 0;
            // Prevent deallocation — C caller owns via markdown_decompress_free
            std::mem::forget(boxed);
            0
        }
        Err(e) => {
            let code = e.error_category();
            result_ref.error_category = code;
            code
        }
    }
}

/// Release the output buffer from a successful `markdown_decompress_bounded` call.
///
/// After calling this function, the `result` struct is reset to a safe zero
/// state. Calling this on a result with a NULL output pointer is a no-op.
///
/// # Safety
///
/// The caller must ensure that:
/// - `result` either is NULL or points to a valid `FFIDecompResult` previously
///   populated by `markdown_decompress_bounded`
/// - The same result is not freed twice (the function resets pointers to NULL
///   after freeing, so double-free is safe but wasteful)
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_decompress_free(result: *mut FFIDecompResult) {
    if result.is_null() {
        return;
    }

    let result_ref = unsafe { &mut *result };
    if !result_ref.output.is_null() && result_ref.output_len > 0 {
        // Reconstruct the Box<[u8]> from the raw parts and drop it
        let slice =
            unsafe { std::slice::from_raw_parts_mut(result_ref.output, result_ref.output_len) };
        unsafe { drop(Box::from_raw(slice)) };
    }
    result_ref.output = ptr::null_mut();
    result_ref.output_len = 0;
    result_ref.error_category = 0;
}

/// Zero-initialize an FFIDecompResult struct.
///
/// # Safety
///
/// The caller must ensure that `result` points to writable storage
/// for an `FFIDecompResult`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_decomp_result_init(result: *mut FFIDecompResult) {
    if result.is_null() {
        return;
    }
    unsafe { ptr::write(result, std::mem::zeroed()) };
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn options_init_sets_defaults() {
        let mut opts: MarkdownOptions = unsafe { std::mem::zeroed() };
        unsafe { markdown_options_init(&mut opts) };

        /* Verify zeroed fields */
        assert_eq!(opts.flavor, 0);
        assert_eq!(opts.estimate_tokens, 0);
        assert_eq!(opts.front_matter, 0);
        assert!(opts.content_type.is_null());
        assert_eq!(opts.content_type_len, 0);
        assert!(opts.base_url.is_null());
        assert_eq!(opts.base_url_len, 0);
        assert_eq!(opts.streaming_budget, 0);
        assert_eq!(opts.prune_noise, 0);
        assert!(opts.prune_selectors.is_null());
        assert_eq!(opts.prune_selector_len, 0);
        assert!(opts.prune_protection_selectors.is_null());
        assert_eq!(opts.prune_protection_selector_len, 0);
        assert_eq!(opts.memory_budget, 0);
        assert_eq!(opts.llm_provider, 0);
        assert_eq!(opts.chars_per_token_fixed, 0);
        assert_eq!(opts.parse_timeout_ms, 0);
        assert_eq!(opts.parser_memory_budget, 0);

        /* Verify non-zero defaults */
        assert_eq!(opts.timeout_ms, 5000);
        assert_eq!(opts.generate_etag, 1);
    }

    #[test]
    fn options_init_null_is_safe() {
        /* Should not panic or crash */
        unsafe { markdown_options_init(std::ptr::null_mut()) };
    }

    #[test]
    fn header_plan_build_and_free_empty_content_type() {
        let mut plan: FFIHeaderPlan = unsafe { std::mem::zeroed() };
        unsafe {
            markdown_build_header_plan(std::ptr::null(), 0, 0, &mut plan);
        }
        assert!(plan.count > 0);
        assert!(!plan.handle.is_null());
        assert!(!plan.entries.is_null());
        unsafe { markdown_header_plan_free(&mut plan) };
        assert!(plan.handle.is_null());
        assert!(plan.entries.is_null());
        assert_eq!(plan.count, 0);
    }

    #[test]
    fn header_plan_build_multiple_entries() {
        let ct = b"text/html; charset=utf-8";
        let mut plan: FFIHeaderPlan = unsafe { std::mem::zeroed() };
        unsafe {
            markdown_build_header_plan(ct.as_ptr(), ct.len(), 1, &mut plan);
        }
        assert!(plan.count >= 2);
        let first = unsafe { &*plan.entries };
        assert!(first.key_len > 0);
        assert!(!first.key.is_null());
        unsafe { markdown_header_plan_free(&mut plan) };
    }

    #[test]
    fn header_plan_invalid_utf8_fallback() {
        let invalid = [0xff, 0xfe, 0xfd];
        let mut plan: FFIHeaderPlan = unsafe { std::mem::zeroed() };
        unsafe {
            markdown_build_header_plan(invalid.as_ptr(), invalid.len(), 0, &mut plan);
        }
        assert!(plan.count > 0);
        unsafe { markdown_header_plan_free(&mut plan) };
    }

    #[test]
    fn build_base_url_from_forwarded_headers() {
        let host = b"api.example.com";
        let proto = b"https";
        let mut buf = [0u8; 256];
        let len = unsafe {
            markdown_build_base_url(
                host.as_ptr(),
                host.len(),
                proto.as_ptr(),
                proto.len(),
                buf.as_mut_ptr(),
                buf.len(),
            )
        };
        assert!(len > 0);
        let url = std::str::from_utf8(&buf[..len]).unwrap();
        assert_eq!(url, "https://api.example.com");
    }

    #[test]
    fn build_base_url_null_host_returns_zero() {
        let proto = b"https";
        let mut buf = [0u8; 256];
        let len = unsafe {
            markdown_build_base_url(
                std::ptr::null(),
                0,
                proto.as_ptr(),
                proto.len(),
                buf.as_mut_ptr(),
                buf.len(),
            )
        };
        assert_eq!(len, 0);
    }

    #[test]
    fn build_base_url_control_chars_rejected() {
        let host = b"evil.com\r\ninjection";
        let proto = b"https";
        let mut buf = [0u8; 256];
        let len = unsafe {
            markdown_build_base_url(
                host.as_ptr(),
                host.len(),
                proto.as_ptr(),
                proto.len(),
                buf.as_mut_ptr(),
                buf.len(),
            )
        };
        assert_eq!(len, 0, "Control characters in host should be rejected");
    }

    #[test]
    fn build_base_url_default_proto() {
        let host = b"example.com";
        let mut buf = [0u8; 256];
        let len = unsafe {
            markdown_build_base_url(
                host.as_ptr(),
                host.len(),
                std::ptr::null(),
                0,
                buf.as_mut_ptr(),
                buf.len(),
            )
        };
        assert!(len > 0);
        let url = std::str::from_utf8(&buf[..len]).unwrap();
        assert_eq!(url, "https://example.com");
    }

    #[test]
    fn decompress_bounded_gzip_success() {
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;

        let original = b"Hello from FFI decompression test!";
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(original).unwrap();
        let compressed = encoder.finish().unwrap();

        let mut result: FFIDecompResult = unsafe { std::mem::zeroed() };
        let rc = unsafe {
            markdown_decompress_bounded(
                compressed.as_ptr(),
                compressed.len(),
                0, // gzip
                1024,
                &mut result,
            )
        };
        assert_eq!(rc, 0, "Expected success (0), got {rc}");
        assert_eq!(result.error_category, 0);
        assert!(!result.output.is_null());
        assert_eq!(result.output_len, original.len());

        let output = unsafe { std::slice::from_raw_parts(result.output, result.output_len) };
        assert_eq!(output, original);

        // Free the result
        unsafe { markdown_decompress_free(&mut result) };
        assert!(result.output.is_null());
        assert_eq!(result.output_len, 0);
    }

    #[test]
    fn decompress_bounded_budget_exceeded() {
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;

        let original = vec![b'X'; 10_000];
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(&original).unwrap();
        let compressed = encoder.finish().unwrap();

        let mut result: FFIDecompResult = unsafe { std::mem::zeroed() };
        let rc = unsafe {
            markdown_decompress_bounded(
                compressed.as_ptr(),
                compressed.len(),
                0,   // gzip
                100, // budget too small
                &mut result,
            )
        };
        assert_eq!(rc, 101, "Expected budget_exceeded (101), got {rc}");
        assert_eq!(result.error_category, 101);
        assert!(result.output.is_null());
        assert_eq!(result.output_len, 0);
    }

    #[test]
    fn decompress_bounded_invalid_format() {
        let data = b"some data";
        let mut result: FFIDecompResult = unsafe { std::mem::zeroed() };
        let rc = unsafe {
            markdown_decompress_bounded(
                data.as_ptr(),
                data.len(),
                99, // invalid format
                1024,
                &mut result,
            )
        };
        assert_eq!(
            rc, 105,
            "Expected invalid_args (105) for unknown format, got {rc}"
        );
        assert_eq!(result.error_category, 105);
    }

    #[test]
    fn decompress_bounded_null_result_returns_invalid_args() {
        let data = b"some data";
        let rc = unsafe {
            markdown_decompress_bounded(data.as_ptr(), data.len(), 0, 1024, std::ptr::null_mut())
        };
        assert_eq!(rc, 105);
    }

    #[test]
    fn decompress_bounded_empty_input_returns_truncated() {
        let mut result: FFIDecompResult = unsafe { std::mem::zeroed() };
        let rc = unsafe { markdown_decompress_bounded(std::ptr::null(), 0, 0, 1024, &mut result) };
        assert_eq!(
            rc, 103,
            "Expected truncated_input (103) for empty input, got {rc}"
        );
        assert_eq!(result.error_category, 103);
    }

    #[test]
    fn decompress_bounded_null_with_nonzero_len_returns_invalid_args() {
        let mut result: FFIDecompResult = unsafe { std::mem::zeroed() };
        let rc = unsafe { markdown_decompress_bounded(std::ptr::null(), 10, 0, 1024, &mut result) };
        assert_eq!(
            rc, 105,
            "Expected invalid_args (105) for NULL input with non-zero length, got {rc}"
        );
        assert_eq!(result.error_category, 105);
    }

    #[test]
    fn decompress_free_null_is_safe() {
        // Should not panic or crash
        unsafe { markdown_decompress_free(std::ptr::null_mut()) };
    }

    #[test]
    fn decompress_free_zeroed_result_is_safe() {
        let mut result: FFIDecompResult = unsafe { std::mem::zeroed() };
        // Freeing a zeroed result (output is NULL) should be a no-op
        unsafe { markdown_decompress_free(&mut result) };
        assert!(result.output.is_null());
    }

    #[test]
    fn decomp_result_init_zeroes_all_fields() {
        let mut result = FFIDecompResult {
            output: 0x1234 as *mut u8,
            output_len: 999,
            error_category: 42,
        };
        unsafe { markdown_decomp_result_init(&mut result) };
        assert!(result.output.is_null());
        assert_eq!(result.output_len, 0);
        assert_eq!(result.error_category, 0);
    }
}
