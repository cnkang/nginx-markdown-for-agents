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
    ERROR_INTERNAL, FFIAcceptResult, FFIConditionalResult, FFIDecisionResult, FFIHeaderEntry,
    FFIHeaderPlan, MarkdownConverterHandle, MarkdownOptions, MarkdownResult,
    NEGOTIATE_REASON_CONVERT, NEGOTIATE_REASON_EXPLICIT_REJECT, NEGOTIATE_REASON_LOWER_Q,
    NEGOTIATE_REASON_MALFORMED, NEGOTIATE_REASON_NO_ACCEPT,
};
use super::convert::convert_inner;
use super::memory::{free_buffer, reset_result, set_error_result, set_success_result};
use super::options::{required_bytes, required_ref};

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
        match std::str::from_utf8(unsafe { std::slice::from_raw_parts(accept_header, accept_header_len) }) {
            Ok(s) => s,
            Err(_) => {
                result_ref.should_convert = 0;
                result_ref.reason = NEGOTIATE_REASON_MALFORMED;
                return;
            }
        }
    };

    let wildcard = on_wildcard != 0;

    use crate::negotiator::{negotiate, NegotiationResult, PassthroughReason};
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

    use crate::conditional::{evaluate_conditional, ConditionalResult};
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

    use crate::decision::{make_decision, Decision, DecisionContext, SkipReason};
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
            result_ref.reason_code = 0;
        }
        Decision::Skip(reason) => {
            result_ref.decision = 1;
            result_ref.reason_code = match reason {
                SkipReason::SkipAccept => 1,
                SkipReason::SkipNoAccept => 2,
                SkipReason::SkipConditional => 3,
                SkipReason::FailDecompression => 4,
                SkipReason::ParseTimeout => 5,
                SkipReason::ParseBudgetExceeded => 6,
                SkipReason::NotEligible => 7,
                SkipReason::Disabled => 8,
            };
        }
    }
}

/// Build a header plan for a successful Markdown conversion.
///
/// The returned plan contains borrowed pointers into Rust-managed memory.
/// The plan is valid only until the next FFI call that modifies the
/// converter state. The C caller must copy any values it needs to retain.
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

    let ct = if content_type.is_null() || content_type_len == 0 {
        "text/markdown; charset=utf-8"
    } else {
        match std::str::from_utf8(unsafe { std::slice::from_raw_parts(content_type, content_type_len) }) {
            Ok(s) => s,
            Err(_) => "text/markdown; charset=utf-8",
        }
    };

    use crate::header_plan::{HeaderOp, HeaderPlan};
    let plan = HeaderPlan::for_markdown_conversion(ct, has_etag != 0);

    let entries: Vec<_> = plan
        .ops
        .iter()
        .map(|op| match op {
            HeaderOp::Set { name, value } => FFIHeaderEntry {
                op_type: 0,
                key: name.as_ptr(),
                key_len: name.len(),
                value: value.as_ptr(),
                value_len: value.len(),
            },
            HeaderOp::Delete { name } => FFIHeaderEntry {
                op_type: 1,
                key: name.as_ptr(),
                key_len: name.len(),
                value: std::ptr::null(),
                value_len: 0,
            },
        })
        .collect();

    if entries.is_empty() {
        result_ref.entries = std::ptr::null();
        result_ref.count = 0;
    } else {
        result_ref.entries = entries.as_ptr();
        result_ref.count = entries.len();
    }

    std::mem::forget(entries);
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
pub unsafe extern "C" fn markdown_validate_url(
    url: *const u8,
    url_len: usize,
) -> u8 {
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
pub unsafe extern "C" fn markdown_is_dangerous_url(
    url: *const u8,
    url_len: usize,
) -> u8 {
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

    let plan_ref = unsafe { &*plan };
    if !plan_ref.entries.is_null() && plan_ref.count > 0 {
        let entries_vec = unsafe {
            Vec::from_raw_parts(
                plan_ref.entries as *mut FFIHeaderEntry,
                plan_ref.count,
                plan_ref.count,
            )
        };
        drop(entries_vec);
    }
}

unsafe fn optional_str(ptr: *const u8, len: usize) -> Option<&'static str> {
    if ptr.is_null() || len == 0 {
        return None;
    }
    std::str::from_utf8(unsafe { std::slice::from_raw_parts(ptr, len) })
        .ok()
        .map(|s| {
            let ptr = s.as_ptr();
            let len = s.len();
            unsafe { std::str::from_utf8_unchecked(std::slice::from_raw_parts(ptr, len)) }
        })
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
