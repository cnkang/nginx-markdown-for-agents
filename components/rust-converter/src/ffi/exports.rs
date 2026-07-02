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
//! All non-trivial FFI exports and release helpers (`markdown_result_free`,
//! `markdown_converter_free`, `markdown_header_plan_free`,
//! `markdown_decompress_free`, etc.) are wrapped in `panic::catch_unwind` to
//! guarantee that no Rust panic can unwind across the FFI boundary.  Simpler
//! helpers (`markdown_options_init`, `markdown_*_result_init`) are panic-free
//! by construction (they only call `ptr::write`).
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
    DECIDE_BASE_URL_INVALID, DECIDE_BASE_URL_OK, FFIBaseUrlDecision, FFIBaseUrlInput,
    MarkdownTrustedProxies, TRUSTED_PROXIES_PUSH_INVALID_CIDR, TRUSTED_PROXIES_PUSH_NULL,
    TRUSTED_PROXIES_PUSH_OK,
};
use super::abi::{
    DECOMP_CATEGORY_INVALID_ARGS, DECOMP_CATEGORY_IO_ERROR, ERROR_INTERNAL, FFIAcceptResult,
    FFIConditionalResult, FFIDecisionResult, FFIDecompResult, FFIEligibilityInput, FFIHeaderEntry,
    FFIHeaderPlan, FFIHeaderPlanHandle, FFIStr, MarkdownConverterHandle, MarkdownOptions,
    MarkdownResult, NEGOTIATE_REASON_CONVERT, NEGOTIATE_REASON_EXPLICIT_REJECT,
    NEGOTIATE_REASON_LOWER_Q, NEGOTIATE_REASON_MALFORMED, NEGOTIATE_REASON_NO_ACCEPT,
};
use super::abi::{
    FFI_CONFIG_NOT_SET_U8, FFI_CONFIG_NOT_SET_U32, FFI_CONFIG_NOT_SET_U64, FFIConflict,
    FFIConflictLevel, FFIConflictList, FFIEffectiveConfig, FFIExplicitConfig,
};
use super::abi::{
    FFIConditionalDecision, FFIConditionalInput, FFIStreamingDecision, FFIStreamingInput,
    STREAMING_BLOCK_REASON_NONE,
};
use super::convert::convert_inner;
use super::memory::{free_buffer, reset_result, set_error_result, set_success_result};
use super::options::{required_bytes, required_ref};
use crate::decision::conditional::{
    CacheValidation, ConditionalInput, ConditionalOutcome, decide_conditional,
};
use crate::decision::eligibility::{Eligibility, EligibilityInput, decide_eligibility};
use crate::decision::streaming::{
    StreamingEngine, StreamingInput, StreamingPolicy, decide_streaming,
};
use crate::forwarded::{BaseUrlInput, decide_base_url, parse_cidr};

#[cfg(test)]
thread_local! {
    /// Test-only panic injection switch. When set to the matching tag,
    /// the corresponding FFI closure panics on the next call so tests
    /// can exercise the catch_unwind fail-open/fail-closed fallbacks.
    /// Not present in release builds — has zero production cost.
    static TEST_PANIC_TAG: std::cell::Cell<Option<&'static str>> =
        const { std::cell::Cell::new(None) };
}

#[cfg(test)]
fn test_should_panic(tag: &'static str) -> bool {
    TEST_PANIC_TAG.with(|c| c.get() == Some(tag))
}

#[cfg(test)]
fn set_test_panic(tag: Option<&'static str>) {
    TEST_PANIC_TAG.with(|c| c.set(tag));
}

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
    let _ = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // SAFETY: `result` was validated as non-NULL above.
        let result_ref = unsafe { &mut *result };
        free_buffer(&mut result_ref.markdown, &mut result_ref.markdown_len);
        free_buffer(&mut result_ref.etag, &mut result_ref.etag_len);
        free_buffer(&mut result_ref.error_message, &mut result_ref.error_len);
        result_ref.token_estimate = 0;
        result_ref.error_code = 0;
        result_ref.peak_memory_estimate = 0;
    }));
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

    let _ = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // SAFETY: `handle` was validated as non-NULL above and originated from `Box::into_raw`.
        unsafe { drop(Box::from_raw(handle)) };
    }));
}

/// Perform Accept header content negotiation.
///
/// Parses the client `Accept` header and determines whether the client
/// prefers `text/markdown` over `text/html`, using RFC 9110 §12.5.1
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

    // Defense-in-depth: negotiate() operates on borrowed &str and is panic-free
    // by design, but a panic must never unwind into C. On panic we default to
    // "do not convert" with MALFORMED reason (safe fail-open outcome).
    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
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
    }));

    if outcome.is_err() {
        result_ref.should_convert = 0;
        result_ref.reason = NEGOTIATE_REASON_MALFORMED;
    }
}

/// Decide whether an upstream response is eligible for Markdown conversion.
///
/// Single source of truth for the eligibility determination (method, status,
/// Range, unbounded streaming, Content-Type allowlist, size limit). The C
/// module marshals request/config fields into [`FFIEligibilityInput`] and
/// casts the returned `u8` directly to `ngx_http_markdown_eligibility_t`
/// (the codes match that enum's discriminants).
///
/// Returns `FFI_ELIGIBILITY_INELIGIBLE_CONFIG` (skip conversion, the safe
/// fail-open outcome) if `input` is NULL or on any caught panic.
///
/// # Safety
///
/// The caller must ensure that:
/// - `input` is NULL or points to a valid [`FFIEligibilityInput`]
/// - `content_type` points to `content_type_len` readable bytes (or is NULL
///   when `content_type_len == 0`)
/// - `content_types`/`stream_types` point to `*_count` readable [`FFIStr`]
///   entries (or are NULL when the count is 0), each `FFIStr.data` pointing to
///   `FFIStr.len` readable bytes (or NULL when `len == 0`)
/// - all referenced memory remains valid for the duration of the call
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_decide_eligibility(input: *const FFIEligibilityInput) -> u8 {
    if input.is_null() {
        return Eligibility::IneligibleConfig.ffi_code();
    }

    // Defense-in-depth: decide_eligibility is panic-free by construction, but a
    // panic must never unwind into C. On panic we default to "ineligible
    // (config)" which means skip conversion — the safe fail-open outcome.
    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        let inp = unsafe { &*input };

        let bytes = |ptr: *const u8, len: usize| -> &[u8] {
            if ptr.is_null() || len == 0 {
                &[]
            } else {
                unsafe { std::slice::from_raw_parts(ptr, len) }
            }
        };

        let ffi_strs = |ptr: *const FFIStr, count: usize| -> Vec<&[u8]> {
            if ptr.is_null() || count == 0 {
                return Vec::new();
            }
            let entries = unsafe { std::slice::from_raw_parts(ptr, count) };
            entries.iter().map(|e| bytes(e.data, e.len)).collect()
        };

        let content_type = bytes(inp.content_type, inp.content_type_len);
        let content_types = ffi_strs(inp.content_types, inp.content_types_count);
        let stream_types = ffi_strs(inp.stream_types, inp.stream_types_count);

        let einput = EligibilityInput {
            filter_enabled: inp.filter_enabled != 0,
            method_get_or_head: inp.method_get_or_head != 0,
            status: inp.status,
            has_range_header: inp.has_range_header != 0,
            content_type,
            content_types: &content_types,
            stream_types: &stream_types,
            content_length: inp.content_length,
            body_limit: inp.body_limit,
        };

        decide_eligibility(&einput).ffi_code()
    }));

    outcome.unwrap_or_else(|_| Eligibility::IneligibleConfig.ffi_code())
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
    // Defense-in-depth: evaluate_conditional only operates on borrowed &str and
    // is panic-free by design, but a panic must never unwind into C. On panic
    // we default to Proceed (result_code = 1), the safe/fail-open outcome that
    // delivers full content rather than a spurious 304.
    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        evaluate_conditional(inm, etag, ims, lm)
    }));
    match outcome {
        Ok(ConditionalResult::NotModified) => {
            result_ref.result_code = 0;
            result_ref.matched_etag_len = 0;
        }
        Ok(ConditionalResult::Proceed) | Err(_) => {
            result_ref.result_code = 1;
            result_ref.matched_etag_len = 0;
        }
    }
}

/// Decide the conditional-request outcome (spec 49): cache-validation mode,
/// `If-None-Match` over `If-Modified-Since` precedence, and `Range` /
/// `no-transform` bypass.
///
/// This is the Rust single source of truth wrapping
/// `crate::decision::conditional::decide_conditional`. The result is written
/// through the `out` output parameter.
///
/// On NULL `input`/`out` or on a caught panic, the output is left at the
/// fail-open default (proceed, no headers evaluated) so a spurious 304 is
/// never produced.
///
/// # Safety
///
/// The caller must ensure that:
/// - `input` is NULL or points to a readable `FFIConditionalInput` whose
///   byte pointers each reference `*_len` readable bytes (or are NULL when
///   the length is 0)
/// - `out` is NULL or points to writable storage for an
///   `FFIConditionalDecision`
/// - all referenced memory remains valid for the duration of the call
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_decide_conditional(
    input: *const FFIConditionalInput,
    out: *mut FFIConditionalDecision,
) {
    if out.is_null() {
        return;
    }
    let out_ref = unsafe { &mut *out };

    // Fail-open default written before the catch_unwind block: proceed with
    // no header evaluated. ConditionalReason::NoHeaders == 0,
    // ConditionalOutcome::Proceed == 1, ConditionalHeader::None == 0.
    out_ref.outcome = ConditionalOutcome::Proceed.as_u8();
    out_ref.reason = 0;
    out_ref.evaluated_header = 0;

    if input.is_null() {
        return;
    }

    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        let inp = unsafe { &*input };

        let cinput = ConditionalInput {
            cache_validation: CacheValidation::from_u8(inp.cache_validation),
            has_range: inp.has_range != 0,
            no_transform: inp.no_transform != 0,
            if_none_match: unsafe { optional_str(inp.if_none_match, inp.if_none_match_len) },
            entity_etag: unsafe { optional_str(inp.entity_etag, inp.entity_etag_len) },
            if_modified_since: unsafe {
                optional_str(inp.if_modified_since, inp.if_modified_since_len)
            },
            last_modified: unsafe { optional_str(inp.last_modified, inp.last_modified_len) },
        };

        decide_conditional(&cinput)
    }));

    if let Ok(decision) = outcome {
        out_ref.outcome = decision.outcome.as_u8();
        out_ref.reason = decision.reason.as_u8();
        out_ref.evaluated_header = decision.evaluated_header.as_u8();
    }
}

/// Decide whether the request may take the streaming path (spec 49).
///
/// This is the Rust single source of truth wrapping
/// `crate::decision::streaming::decide_streaming`. The result is written
/// through the `out` output parameter.
///
/// On NULL `input`/`out` or on a caught panic, the output is the safe
/// fallback: not eligible (the full-buffer path), with no block reason
/// reported (`STREAMING_BLOCK_REASON_NONE`).
///
/// # Safety
///
/// The caller must ensure that:
/// - `input` is NULL or points to a readable `FFIStreamingInput`
/// - `out` is NULL or points to writable storage for an
///   `FFIStreamingDecision`
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_decide_streaming(
    input: *const FFIStreamingInput,
    out: *mut FFIStreamingDecision,
) {
    if out.is_null() {
        return;
    }
    let out_ref = unsafe { &mut *out };

    // Fail-safe default: full-buffer (not eligible), no block reason.
    out_ref.eligible = 0;
    out_ref.block_reason = STREAMING_BLOCK_REASON_NONE;

    if input.is_null() {
        return;
    }

    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        let inp = unsafe { &*input };

        let sinput = StreamingInput {
            policy: StreamingPolicy::from_u8(inp.policy),
            engine: StreamingEngine::from_u8(inp.engine),
            cache_validation: CacheValidation::from_u8(inp.cache_validation),
            is_head: inp.is_head != 0,
            is_not_modified: inp.is_not_modified != 0,
            has_range: inp.has_range != 0,
            no_transform: inp.no_transform != 0,
            has_content_encoding: inp.has_content_encoding != 0,
            content_length_known: inp.content_length_known != 0,
            content_length: inp.content_length,
            streaming_threshold: inp.streaming_threshold,
        };

        decide_streaming(&sinput)
    }));

    if let Ok(decision) = outcome {
        out_ref.eligible = u8::from(decision.eligible);
        out_ref.block_reason = match decision.block_reason {
            Some(reason) => reason.as_u8(),
            None => STREAMING_BLOCK_REASON_NONE,
        };
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

    // SAFETY: `result` was validated as non-NULL above.
    let result_ref = unsafe { &mut *result };

    // Initialize the output to a safe fallback *before* the catch_unwind block
    // so that a panic anywhere in the decision path always leaves `result`
    // holding a defined, fail-open skip state instead of stale/uninitialized
    // bytes. The normal path below overwrites both fields on success.
    use crate::decision::reason_code::ReasonCode;
    result_ref.decision = 1; /* skip */
    result_ref.reason_code = ReasonCode::FfiPanic.discriminant() as u8;

    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| -> (u8, u8) {
        #[cfg(test)]
        if test_should_panic("make_decision") {
            panic!("test-injected panic in markdown_make_decision");
        }
        #[cfg(not(test))]
        let _ = ("make_decision",);
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
                /* Converted == ReasonCode::Converted (0). */
                (0, ReasonCode::Converted.discriminant() as u8)
            }
            Decision::Skip(reason) => {
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
                    SkipReason::FailDecompression => ReasonCode::DecompressionError,
                    SkipReason::ParseTimeout => ReasonCode::Timeout,
                    SkipReason::ParseBudgetExceeded => ReasonCode::BudgetExceeded,
                    SkipReason::NotEligible => ReasonCode::NotEligible,
                    SkipReason::Disabled => ReasonCode::Disabled,
                };
                /* The pre-conversion skip reasons all map to canonical
                 * discriminants in the 0..=15 range (the C uint8_t reason_code
                 * contract). ReasonCode is repr(u8) so the cast itself can never
                 * truncate; this assert guards the stronger documented invariant
                 * that decision results stay within the pre-conversion subset, so
                 * adding a high-valued post-conversion variant to this match arm
                 * is caught in debug builds. */
                debug_assert!(
                    canonical.discriminant() <= 15,
                    "decision ReasonCode discriminant {} exceeds the documented \
                     pre-conversion range (0..=15)",
                    canonical.discriminant()
                );
                (1, canonical.discriminant() as u8)
            }
        }
    }));

    if let Ok((decision, reason_code)) = outcome {
        /* Write both fields atomically after confirming no panic. */
        result_ref.decision = decision;
        result_ref.reason_code = reason_code;
    }
    /* else: panic caught — the fail-open skip + FfiPanic fallback
     * set above remains in place. Nothing else to write. */
}

/// Build a header plan for a successful Markdown conversion.
/// The returned plan contains Rust-owned buffers. The C caller must release
/// the plan via `markdown_header_plan_free`.
/// This function treats `result` as uninitialized output storage and never
/// reads its previous fields. Callers that already own a plan in the same
/// storage must call `markdown_header_plan_free` before rebuilding it.
/// # Safety
/// The caller must ensure that:
/// - `content_type` points to readable UTF-8 bytes of `content_type_len`
/// - `result` points to writable storage for a `FFIHeaderPlan`; its previous
///   bytes may be uninitialized, but any previously-owned plan must have been
///   released before this call
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

    /* `result` is a pure output parameter. Do not create a reference or read
     * any field before initialization: writable C storage may be uninitialized. */
    unsafe { ptr::write(result, std::mem::zeroed()) };
    let result_ref = unsafe { &mut *result };
    /* Centralize the empty-plan shape so construction and panic fallback
     * stay synchronized if FFIHeaderPlan gains fields. */
    reset_header_plan_to_empty(result_ref);

    // Defense-in-depth: header plan construction allocates and builds string
    // buffers. A panic must never unwind into C. On panic we leave the output
    // struct zeroed (count=0, entries=null, handle=null) — the safe empty plan.
    //
    // Atomic write after catch: the closure computes all return values as
    // local variables.  After catch_unwind returns Ok, fields are written
    // atomically so a panic cannot leave the output struct partially updated.
    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(
        || -> (*mut FFIHeaderPlanHandle, *const FFIHeaderEntry, usize) {
            let ct = if content_type.is_null() || content_type_len == 0 {
                "text/markdown; charset=utf-8"
            } else {
                std::str::from_utf8(unsafe {
                    std::slice::from_raw_parts(content_type, content_type_len)
                })
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
                    HeaderOp::DeleteAll { name } => {
                        let mut key_vec = name.as_bytes().to_vec();
                        key_vec.push(0); /* NUL-terminate per FFI contract */
                        let key_len = key_vec.len() - 1;
                        owned.key_storage.push(key_vec.into_boxed_slice());
                        let key = &owned.key_storage[owned.key_storage.len() - 1];
                        owned.entries.push(FFIHeaderEntry {
                            op_type: 3,
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

            let count = owned.entries.len();
            if count == 0 {
                (std::ptr::null_mut(), std::ptr::null(), 0)
            } else {
                let entries_ptr = owned.entries.as_ptr();
                let handle = Box::into_raw(Box::new(owned)) as *mut FFIHeaderPlanHandle;
                (handle, entries_ptr, count)
            }
        },
    ));

    match outcome {
        Ok((handle, entries, count)) => {
            result_ref.handle = handle;
            result_ref.entries = entries;
            result_ref.count = count;
        }
        Err(_) => {
            /* Panic caught — ensure a safe empty plan. */
            reset_header_plan_to_empty(result_ref);
        }
    }
}

/// Reset an `FFIHeaderPlan` to the safe empty shape (no entries, null handle).
///
/// Centralizes the zeroed-plan initialization used both at construction time
/// (before `markdown_build_header_plan` populates it) and on panic fallback,
/// so the two paths cannot drift apart if `FFIHeaderPlan` gains fields.
fn reset_header_plan_to_empty(plan: &mut FFIHeaderPlan) {
    plan.handle = std::ptr::null_mut();
    plan.entries = std::ptr::null();
    plan.count = 0;
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
    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| -> u8 {
        #[cfg(test)]
        if test_should_panic("validate_url") {
            panic!("test-injected panic in markdown_validate_url");
        }
        #[cfg(not(test))]
        let _ = ("validate_url",);
        if url.is_null() || url_len == 0 {
            return 1;
        }
        // SAFETY: caller guarantees `url` points to `url_len` readable bytes.
        let url_str = match std::str::from_utf8(unsafe { std::slice::from_raw_parts(url, url_len) })
        {
            Ok(s) => s,
            Err(_) => return 0,
        };

        use crate::security::validate_link_url;
        if validate_link_url(url_str).is_ok() {
            1
        } else {
            0
        }
    }));
    outcome.unwrap_or_default()
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
    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| -> u8 {
        #[cfg(test)]
        if test_should_panic("is_dangerous_url") {
            panic!("test-injected panic in markdown_is_dangerous_url");
        }
        #[cfg(not(test))]
        let _ = ("is_dangerous_url",);
        if url.is_null() || url_len == 0 {
            return 0;
        }
        // SAFETY: caller guarantees `url` points to `url_len` readable bytes.
        let url_str = match std::str::from_utf8(unsafe { std::slice::from_raw_parts(url, url_len) })
        {
            Ok(s) => s,
            Err(_) => return 1,
        };

        use crate::security::SecurityValidator;
        let validator = SecurityValidator::new();
        if validator.is_dangerous_url(url_str) {
            1
        } else {
            0
        }
    }));
    outcome.unwrap_or(1)
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

    // Defense-in-depth: parse_forwarded_headers operates on borrowed &str and
    // is panic-free by design, but a panic must never unwind into C. On panic
    // we return 0 (no bytes written), the safe default.
    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        let host_str = if x_forwarded_host.is_null() || host_len == 0 {
            None
        } else {
            std::str::from_utf8(unsafe { std::slice::from_raw_parts(x_forwarded_host, host_len) })
                .ok()
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
    }));

    outcome.unwrap_or_default()
}

/// Allocate a new, empty trusted-proxy CIDR set (spec 47).
///
/// The handle accumulates config-time-validated CIDRs via
/// `markdown_trusted_proxies_push` and is consumed at request time by
/// `markdown_decide_base_url`.
///
/// # Safety
///
/// Returns a raw pointer that must eventually be freed with
/// `markdown_trusted_proxies_free`.  Returns NULL only if allocation of the
/// handle panics and that panic is caught.
#[unsafe(no_mangle)]
pub extern "C" fn markdown_trusted_proxies_new() -> *mut MarkdownTrustedProxies {
    let result = panic::catch_unwind(|| Box::into_raw(Box::new(MarkdownTrustedProxies::new())));
    result.unwrap_or(ptr::null_mut())
}

/// Validate a CIDR string and append it to a trusted-proxy set (config time).
///
/// Returns `TRUSTED_PROXIES_PUSH_OK` (0) on success,
/// `TRUSTED_PROXIES_PUSH_INVALID_CIDR` (1) when the CIDR is malformed, or
/// `TRUSTED_PROXIES_PUSH_NULL` (2) when `handle` or `cidr` is NULL/empty.
///
/// # Safety
///
/// The caller must ensure that `handle` points to a live set created by
/// `markdown_trusted_proxies_new`, and that `cidr` either points to `cidr_len`
/// readable bytes or is NULL when `cidr_len == 0`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_trusted_proxies_push(
    handle: *mut MarkdownTrustedProxies,
    cidr: *const u8,
    cidr_len: usize,
) -> u8 {
    if handle.is_null() || cidr.is_null() || cidr_len == 0 {
        return TRUSTED_PROXIES_PUSH_NULL;
    }

    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // SAFETY: caller guarantees `cidr` points to `cidr_len` readable bytes.
        let bytes = unsafe { std::slice::from_raw_parts(cidr, cidr_len) };
        let Ok(text) = std::str::from_utf8(bytes) else {
            return TRUSTED_PROXIES_PUSH_INVALID_CIDR;
        };
        match parse_cidr(text) {
            Ok(parsed) => {
                // SAFETY: caller guarantees `handle` is a live set.
                let set = unsafe { &mut *handle };
                set.cidrs.push(parsed);
                TRUSTED_PROXIES_PUSH_OK
            }
            Err(_) => TRUSTED_PROXIES_PUSH_INVALID_CIDR,
        }
    }));

    outcome.unwrap_or(TRUSTED_PROXIES_PUSH_INVALID_CIDR)
}

/// Free a trusted-proxy set previously returned by
/// `markdown_trusted_proxies_new`.
///
/// # Safety
///
/// `handle` must either be NULL or a pointer previously returned by
/// `markdown_trusted_proxies_new` that has not already been freed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_trusted_proxies_free(handle: *mut MarkdownTrustedProxies) {
    if handle.is_null() {
        return;
    }
    let _ = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // SAFETY: `handle` was validated as non-NULL and is owned by Rust.
        unsafe { drop(Box::from_raw(handle)) };
    }));
}

/// Decide the trusted base URL for a request (spec 47 small API).
///
/// Marshals the borrowed request/config fields into the pure
/// [`decide_base_url`] decision, writes the chosen base URL into the
/// caller-provided `out_buf`, and fills `out` with the byte count, reason
/// code, and source.  The decision logic (CIDR matching, forwarded-header
/// precedence, multi-hop handling, host/proto validation, fallback) lives
/// entirely in Rust.
///
/// Returns `DECIDE_BASE_URL_OK` (0) on success, or `DECIDE_BASE_URL_INVALID`
/// (1) when `input`/`out`/`out_buf` is NULL, the output buffer is too small,
/// or a panic is caught (fail-safe).
///
/// # Safety
///
/// The caller must ensure that:
/// - `input` points to a valid `FFIBaseUrlInput`; every non-NULL byte field
///   points to its stated length of readable bytes, and `trusted` is NULL or
///   a live `MarkdownTrustedProxies` handle;
/// - `out_buf` points to at least `out_buf_cap` writable bytes;
/// - `out` points to writable storage for an `FFIBaseUrlDecision`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_decide_base_url(
    input: *const FFIBaseUrlInput,
    out_buf: *mut u8,
    out_buf_cap: usize,
    out: *mut FFIBaseUrlDecision,
) -> u8 {
    if input.is_null() || out.is_null() || out_buf.is_null() || out_buf_cap == 0 {
        return DECIDE_BASE_URL_INVALID;
    }

    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // SAFETY: caller guarantees `input` is a valid FFIBaseUrlInput.
        let inp = unsafe { &*input };

        let source_ip = unsafe { optional_str(inp.source_ip, inp.source_ip_len) }.unwrap_or("");
        let forwarded = unsafe { optional_str(inp.forwarded, inp.forwarded_len) };
        let x_forwarded_proto =
            unsafe { optional_str(inp.x_forwarded_proto, inp.x_forwarded_proto_len) };
        let x_forwarded_host =
            unsafe { optional_str(inp.x_forwarded_host, inp.x_forwarded_host_len) };
        let host = unsafe { optional_str(inp.host, inp.host_len) };

        let cidrs: &[crate::forwarded::Cidr] = if inp.trusted.is_null() {
            &[]
        } else {
            // SAFETY: caller guarantees `trusted` is a live handle when non-NULL.
            unsafe { &(*inp.trusted).cidrs }
        };

        let decision_input = BaseUrlInput {
            source_ip,
            is_unix_socket: inp.is_unix_socket != 0,
            trusted_configured: inp.trusted_configured != 0,
            forwarded,
            x_forwarded_proto,
            x_forwarded_host,
            host,
        };

        let decision = decide_base_url(&decision_input, cidrs);
        let bytes = decision.base_url.as_bytes();
        if bytes.len() > out_buf_cap {
            return DECIDE_BASE_URL_INVALID;
        }

        // SAFETY: out_buf has at least out_buf_cap >= bytes.len() writable bytes.
        unsafe { ptr::copy_nonoverlapping(bytes.as_ptr(), out_buf, bytes.len()) };

        // SAFETY: `out` was validated as non-NULL writable storage.
        unsafe {
            ptr::write(
                out,
                FFIBaseUrlDecision {
                    base_url_len: bytes.len(),
                    reason: decision.reason.as_u8(),
                    source: decision.source.as_u8(),
                },
            );
        }

        DECIDE_BASE_URL_OK
    }));

    outcome.unwrap_or(DECIDE_BASE_URL_INVALID)
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

    let _ = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // SAFETY: `plan` was validated as non-NULL above.
        let plan_ref = unsafe { &mut *plan };
        if !plan_ref.handle.is_null() {
            /* Save the old handle before clearing external fields */
            let old_handle = plan_ref.handle;
            /* Zero all externally-visible fields FIRST */
            plan_ref.handle = std::ptr::null_mut();
            plan_ref.entries = std::ptr::null();
            plan_ref.count = 0;
            /* Now drop — no external field points to freed memory */
            unsafe { drop(Box::from_raw(old_handle as *mut HeaderPlanOwned)) };
        }
    }));
}

/// Convert a C pointer + length pair to an optional UTF-8 `&str`.
///
/// Returns `None` if the pointer is null, the length is zero, or
/// the bytes are not valid UTF-8.
///
/// # Safety
///
/// - `ptr` must be either NULL or point to `len` consecutive,
///   properly initialized bytes.
/// - If `ptr` is non-NULL, the memory must remain valid for the
///   lifetime `'a`.
/// - `len` must not exceed `isize::MAX`.
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
    opts.generate_etag = 0;
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

    // Perform bounded decompression. Wrap in catch_unwind: the decoders
    // (flate2/brotli) run attacker-controlled bytes and, while they normally
    // return Err on bad input, a panic here would unwind into C (UB). On
    // panic we report a generic io_error category and emit no output.
    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        crate::decompress::decompress_bounded(input_slice, fmt, budget)
    }));
    match outcome {
        Ok(Ok(decomp_result)) => {
            let mut boxed = decomp_result.output.into_boxed_slice();
            result_ref.output_len = boxed.len();
            result_ref.error_category = 0;
            if boxed.is_empty() {
                // Empty result: follow FFI convention by returning NULL
                // so callers (and decompress_free) can skip the buffer.
                result_ref.output = ptr::null_mut();
            } else {
                // Transfer ownership to C caller.  Extract a plain *mut u8
                // via as_mut_ptr (thin pointer) then forget the Box so the
                // allocator keeps the backing memory alive.  This avoids
                // relying on Box<[u8]> fat-pointer layout details.
                let ptr = boxed.as_mut_ptr();
                std::mem::forget(boxed);
                result_ref.output = ptr;
            }
            0
        }
        Ok(Err(e)) => {
            let code = e.error_category();
            result_ref.error_category = code;
            code
        }
        Err(_) => {
            // Panic was caught; report io_error and emit no output.
            result_ref.output = ptr::null_mut();
            result_ref.output_len = 0;
            result_ref.error_category = DECOMP_CATEGORY_IO_ERROR;
            DECOMP_CATEGORY_IO_ERROR
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

    let _ = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // SAFETY: `result` was validated as non-NULL above.
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
    }));
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

// ─── Profile conflict detection FFI (spec 50, 0.9.0) ─────────────────────────

/// Detect configuration conflicts between a profile, explicit directives, and
/// the effective configuration.
///
/// This is the primary FFI entry point for `nginx -t` validation. The C side
/// calls this after computing the effective config via its own merge logic,
/// passing the profile selector, the explicitly-set directive flags, and the
/// fully-resolved effective config.
///
/// Returns an [`FFIConflictList`] that the caller must free with
/// [`markdown_free_conflicts`]. If no conflicts are detected, the returned
/// list has `count == 0` and `conflicts == NULL` (Rule 53).
///
/// On NULL input pointers or on a caught panic, returns an empty conflict list
/// (the safe fail-open outcome: no spurious errors reported).
///
/// # Safety
///
/// The caller must ensure that:
/// - `profile` is a valid `FFIProfile` discriminant (0–3)
/// - `explicit` is NULL or points to a readable `FFIExplicitConfig`
/// - `effective` is NULL or points to a readable `FFIEffectiveConfig`
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_detect_conflicts(
    profile: u8,
    explicit: *const FFIExplicitConfig,
    effective: *const FFIEffectiveConfig,
) -> FFIConflictList {
    let empty_list = || FFIConflictList {
        conflicts: ptr::null_mut(),
        count: 0,
    };

    // NULL input validation (Rule 46)
    if explicit.is_null() || effective.is_null() {
        return empty_list();
    }

    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // Convert FFIProfile discriminant to Option<Profile>
        let rust_profile = match profile {
            1 => Some(crate::config::profile::Profile::StrictCache),
            2 => Some(crate::config::profile::Profile::Balanced),
            3 => Some(crate::config::profile::Profile::StreamingFirst),
            _ => None, // 0 (None) or unknown
        };

        // SAFETY: validated non-NULL above.
        let ffi_explicit = unsafe { &*explicit };
        let ffi_effective = unsafe { &*effective };

        // Convert FFIExplicitConfig → ExplicitConfig
        use crate::config::merge::ExplicitConfig;
        use crate::config::profile::{AcceptMode, ErrorPolicy};

        let rust_explicit = ExplicitConfig {
            accept: if ffi_explicit.accept == FFI_CONFIG_NOT_SET_U8 {
                None
            } else {
                Some(AcceptMode::from_u8(ffi_explicit.accept))
            },
            cache_validation: if ffi_explicit.cache_validation == FFI_CONFIG_NOT_SET_U8 {
                None
            } else {
                Some(CacheValidation::from_u8(ffi_explicit.cache_validation))
            },
            streaming: if ffi_explicit.streaming == FFI_CONFIG_NOT_SET_U8 {
                None
            } else {
                Some(StreamingPolicy::from_u8(ffi_explicit.streaming))
            },
            limits_memory_bytes: if ffi_explicit.limits_memory_bytes == FFI_CONFIG_NOT_SET_U64 {
                None
            } else {
                Some(ffi_explicit.limits_memory_bytes)
            },
            limits_timeout_ms: if ffi_explicit.limits_timeout_ms == FFI_CONFIG_NOT_SET_U64 {
                None
            } else {
                Some(ffi_explicit.limits_timeout_ms)
            },
            limits_streaming_buffer_bytes: if ffi_explicit.limits_streaming_buffer_bytes
                == FFI_CONFIG_NOT_SET_U64
            {
                None
            } else {
                Some(ffi_explicit.limits_streaming_buffer_bytes)
            },
            limits_max_inflight: if ffi_explicit.limits_max_inflight == FFI_CONFIG_NOT_SET_U32 {
                None
            } else {
                Some(ffi_explicit.limits_max_inflight)
            },
            error_policy: if ffi_explicit.error_policy == FFI_CONFIG_NOT_SET_U8 {
                None
            } else {
                Some(ErrorPolicy::from_u8(ffi_explicit.error_policy))
            },
            diagnostics: if ffi_explicit.diagnostics == FFI_CONFIG_NOT_SET_U8 {
                None
            } else {
                Some(ffi_explicit.diagnostics != 0)
            },
        };

        // Convert FFIEffectiveConfig → EffectiveConfig
        use crate::config::merge::EffectiveConfig;

        let rust_effective = EffectiveConfig {
            accept: AcceptMode::from_u8(ffi_effective.accept),
            cache_validation: CacheValidation::from_u8(ffi_effective.cache_validation),
            streaming: StreamingPolicy::from_u8(ffi_effective.streaming),
            limits_memory_bytes: ffi_effective.limits_memory_bytes,
            limits_timeout_ms: ffi_effective.limits_timeout_ms,
            limits_streaming_buffer_bytes: ffi_effective.limits_streaming_buffer_bytes,
            limits_max_inflight: ffi_effective.limits_max_inflight,
            error_policy: ErrorPolicy::from_u8(ffi_effective.error_policy),
            diagnostics: ffi_effective.diagnostics != 0,
        };

        // Run conflict detection
        let conflicts = crate::config::conflict::detect_conflicts(
            rust_profile,
            &rust_explicit,
            &rust_effective,
        );

        if conflicts.is_empty() {
            return empty_list();
        }

        // Convert Vec<Conflict> → FFIConflictList
        let count = conflicts.len();

        // Each conflict's message is individually heap-allocated.
        // Freed one-by-one in markdown_free_conflicts.
        //
        // NOTE: If catch_unwind catches a panic after some messages have been
        // Box::into_raw'd but before mem::forget(boxed), those message buffers
        // would leak. This is acceptable because: (1) the closure is pure and
        // allocation-only — panics here are practically impossible, and (2) the
        // leak is bounded by conflict count (typically <5 short strings).
        let mut ffi_conflicts: Vec<FFIConflict> = Vec::with_capacity(count);
        for conflict in &conflicts {
            let level = match conflict.level {
                crate::config::conflict::ConflictLevel::Error => FFIConflictLevel::Error,
                crate::config::conflict::ConflictLevel::Warning => FFIConflictLevel::Warning,
            };
            // Allocate message bytes on the heap via Box
            let msg_bytes: Box<[u8]> = conflict.message.as_bytes().to_vec().into_boxed_slice();
            let msg_len = msg_bytes.len();
            let msg_ptr = Box::into_raw(msg_bytes) as *const u8;
            ffi_conflicts.push(FFIConflict {
                level,
                message: msg_ptr,
                message_len: msg_len,
            });
        }

        // Transfer the Vec<FFIConflict> to a heap allocation (Rule 53: fat-pointer safety)
        let mut boxed = ffi_conflicts.into_boxed_slice();
        let ptr = boxed.as_mut_ptr();
        std::mem::forget(boxed);

        FFIConflictList {
            conflicts: ptr,
            count,
        }
    }));

    outcome.unwrap_or_else(|_| empty_list())
}

/// Free a conflict list returned by `markdown_detect_conflicts`.
///
/// Releases all heap-allocated message buffers and the conflict array itself.
/// Calling with a zeroed/empty list (`count == 0`, `conflicts == NULL`) is a
/// safe no-op.
///
/// # Safety
///
/// The caller must ensure that `list` points to a valid `FFIConflictList`
/// previously returned by `markdown_detect_conflicts`, or is a zeroed struct.
/// The list must not be used after this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_free_conflicts(list: *mut FFIConflictList) {
    if list.is_null() {
        return;
    }

    let _ = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // SAFETY: validated non-NULL above.
        let list_ref = unsafe { &mut *list };

        if list_ref.conflicts.is_null() || list_ref.count == 0 {
            list_ref.conflicts = ptr::null_mut();
            list_ref.count = 0;
            return;
        }

        // Free each message buffer
        let conflicts_slice =
            unsafe { std::slice::from_raw_parts_mut(list_ref.conflicts, list_ref.count) };
        for conflict in conflicts_slice.iter() {
            if !conflict.message.is_null() && conflict.message_len > 0 {
                // Reconstruct the Box<[u8]> from the raw parts and drop it
                let msg_slice = unsafe {
                    std::slice::from_raw_parts_mut(
                        conflict.message as *mut u8,
                        conflict.message_len,
                    )
                };
                unsafe { drop(Box::from_raw(msg_slice)) };
            }
        }

        // Free the conflicts array itself
        let boxed = unsafe {
            Box::from_raw(std::ptr::slice_from_raw_parts_mut(
                list_ref.conflicts,
                list_ref.count,
            ))
        };
        drop(boxed);

        // Reset to safe state
        list_ref.conflicts = ptr::null_mut();
        list_ref.count = 0;
    }));
}

// ─── Error Classification FFI (spec 51) ──────────────────────────────────────

use super::abi::{
    FFI_ERROR_BEHAVIOR_PASS_THROUGH, FFI_ERROR_BEHAVIOR_RETURN_STATUS,
    FFI_ERROR_BEHAVIOR_TERMINATE, FFI_ERROR_POLICY_FAIL_CLOSED, FFI_ERROR_POLICY_PASS,
    FFI_ERROR_POLICY_STATUS, FFIErrorBehavior, FFIErrorPolicy,
};
use crate::error::classification::{
    ErrorBehavior, ErrorClass, ErrorPolicy, classify_error_code, decide_error_behavior,
    error_to_reason_code,
};

/// Decide error handling behavior for a given error class and policy (spec 51).
///
/// This is the FFI entry point for the unified error policy decision.
/// The C error handler calls this to determine what action to take.
///
/// # Parameters
///
/// - `error_class`: The `FFIErrorClass` discriminant identifying the error.
/// - `policy`: The `FFIErrorPolicy` derived from `markdown_error_policy`.
/// - `out`: Output pointer for the resulting `FFIErrorBehavior`.
///
/// # Returns
///
/// `0` on success, `1` if `out` is NULL or `error_class` is invalid.
///
/// # Safety
///
/// The caller must ensure that `out` is NULL or points to writable storage
/// for an `FFIErrorBehavior`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_decide_error_behavior(
    error_class: u8,
    policy: FFIErrorPolicy,
    out: *mut FFIErrorBehavior,
) -> u8 {
    if out.is_null() {
        return 1;
    }

    /* Fail-safe default: terminate connection (most conservative). */
    let out_ref = unsafe { &mut *out };
    out_ref.kind = FFI_ERROR_BEHAVIOR_TERMINATE;
    out_ref.status_code = 0;
    out_ref.forced = 1;

    let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        let class = match ErrorClass::from_discriminant(error_class) {
            Some(c) => c,
            None => return Err(()),
        };

        let rust_policy = match policy.kind {
            FFI_ERROR_POLICY_PASS => ErrorPolicy::Pass,
            FFI_ERROR_POLICY_STATUS => ErrorPolicy::Status(policy.status_code),
            FFI_ERROR_POLICY_FAIL_CLOSED => ErrorPolicy::FailClosed,
            _ => ErrorPolicy::Pass, /* unknown → fail-open default */
        };

        let behavior = decide_error_behavior(class, rust_policy);

        Ok(behavior)
    }));

    match result {
        Ok(Ok(behavior)) => {
            match behavior {
                ErrorBehavior::PassThrough => {
                    out_ref.kind = FFI_ERROR_BEHAVIOR_PASS_THROUGH;
                    out_ref.status_code = 0;
                    out_ref.forced = 0;
                }
                ErrorBehavior::ReturnStatus(code) => {
                    out_ref.kind = FFI_ERROR_BEHAVIOR_RETURN_STATUS;
                    out_ref.status_code = code;
                    out_ref.forced = 0;
                }
                ErrorBehavior::TerminateConnection => {
                    out_ref.kind = FFI_ERROR_BEHAVIOR_TERMINATE;
                    out_ref.status_code = 0;
                    out_ref.forced = 1;
                }
            }
            0
        }
        Ok(Err(())) => 1, /* invalid error_class discriminant */
        Err(_) => {
            /* Panic caught — fail-safe TerminateConnection already set. */
            0
        }
    }
}

/// Map an error class to its reason code discriminant (spec 51).
///
/// Returns the `ReasonCode` discriminant (u8) for the given error class.
/// Returns `u8::MAX` (255) if the error class is invalid.
///
/// # Safety
///
/// No pointer parameters; always safe to call.
#[unsafe(no_mangle)]
pub extern "C" fn markdown_error_to_reason_code(error_class: u8) -> u8 {
    let result = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        let class = match ErrorClass::from_discriminant(error_class) {
            Some(c) => c,
            None => return u8::MAX,
        };
        let reason = error_to_reason_code(class);
        reason.discriminant() as u8
    }));

    result.unwrap_or(u8::MAX)
}

/// Classify a raw FFI error code into its `ErrorClass` discriminant.
///
/// This is the FFI entry point that maps the raw `ERROR_*` constants
/// (defined in `markdown_converter.h`) to `ErrorClass` discriminants.
/// The C side calls this to delegate error classification to Rust
/// (single source of truth) instead of maintaining an independent switch.
///
/// Returns the `ErrorClass` discriminant (u8) for the given error code.
/// Unknown error codes return `ErrorClass::FfiPanic` discriminant (3).
///
/// # Safety
///
/// No pointer parameters; always safe to call. The function is panic-free
/// by construction (trivial match expression with no allocations).
#[unsafe(no_mangle)]
pub extern "C" fn markdown_classify_error_code(error_code: u32) -> u8 {
    classify_error_code(error_code) as u8
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
        assert_eq!(opts.generate_etag, 0);
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
    fn header_plan_build_initializes_uninitialized_output_storage() {
        let mut plan = std::mem::MaybeUninit::<FFIHeaderPlan>::uninit();
        unsafe {
            markdown_build_header_plan(std::ptr::null(), 0, 0, plan.as_mut_ptr());
            let mut plan = plan.assume_init();
            assert!(plan.count > 0);
            assert!(!plan.handle.is_null());
            assert!(!plan.entries.is_null());
            markdown_header_plan_free(&mut plan);
        }
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
    fn header_plan_delete_all_entry_shape() {
        let ct = b"text/markdown; charset=utf-8";
        let mut plan: FFIHeaderPlan = unsafe { std::mem::zeroed() };
        unsafe {
            markdown_build_header_plan(ct.as_ptr(), ct.len(), 1, &mut plan);
        }
        assert!(
            plan.count >= 3,
            "plan should have Content-Type + delete-all entries"
        );

        let entries = unsafe { std::slice::from_raw_parts(plan.entries, plan.count) };

        let delete_all_entries: Vec<&FFIHeaderEntry> =
            entries.iter().filter(|e| e.op_type == 3).collect();
        assert!(
            !delete_all_entries.is_empty(),
            "plan must contain at least one delete-all (op_type==3) entry"
        );

        for entry in &delete_all_entries {
            assert!(!entry.key.is_null(), "delete-all key must not be NULL");
            assert!(entry.key_len > 0, "delete-all key_len must be > 0");
            let name = unsafe { std::slice::from_raw_parts(entry.key, entry.key_len) };
            let name_str = std::str::from_utf8(name).unwrap();
            assert!(
                name_str == "Content-Encoding" || name_str == "Content-Length",
                "unexpected delete-all header: {name_str}"
            );
            let nul = unsafe { *entry.key.add(entry.key_len) };
            assert_eq!(nul, 0, "delete-all key must be NUL-terminated");
            assert!(entry.value.is_null(), "delete-all value must be NULL");
            assert_eq!(entry.value_len, 0, "delete-all value_len must be 0");
        }

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

    /* ---- spec 47: trusted proxies + decide_base_url FFI ---- */

    fn push_cidr(handle: *mut MarkdownTrustedProxies, cidr: &str) -> u8 {
        unsafe { markdown_trusted_proxies_push(handle, cidr.as_ptr(), cidr.len()) }
    }

    fn empty_base_url_input() -> FFIBaseUrlInput {
        FFIBaseUrlInput {
            source_ip: ptr::null(),
            source_ip_len: 0,
            trusted: ptr::null(),
            forwarded: ptr::null(),
            forwarded_len: 0,
            x_forwarded_proto: ptr::null(),
            x_forwarded_proto_len: 0,
            x_forwarded_host: ptr::null(),
            x_forwarded_host_len: 0,
            host: ptr::null(),
            host_len: 0,
            is_unix_socket: 0,
            trusted_configured: 0,
        }
    }

    fn empty_decision() -> FFIBaseUrlDecision {
        FFIBaseUrlDecision {
            base_url_len: 0,
            reason: 0xff,
            source: 0xff,
        }
    }

    #[test]
    fn trusted_proxies_push_validates_cidr() {
        let handle = markdown_trusted_proxies_new();
        assert!(!handle.is_null());
        assert_eq!(push_cidr(handle, "10.0.0.0/8"), TRUSTED_PROXIES_PUSH_OK);
        assert_eq!(push_cidr(handle, "2001:db8::/32"), TRUSTED_PROXIES_PUSH_OK);
        assert_eq!(
            push_cidr(handle, "not-a-cidr"),
            TRUSTED_PROXIES_PUSH_INVALID_CIDR
        );
        assert_eq!(
            push_cidr(handle, "10.0.0.0/33"),
            TRUSTED_PROXIES_PUSH_INVALID_CIDR
        );
        unsafe { markdown_trusted_proxies_free(handle) };
    }

    #[test]
    fn trusted_proxies_push_null_inputs() {
        // NULL handle.
        let rc =
            unsafe { markdown_trusted_proxies_push(ptr::null_mut(), b"10.0.0.0/8".as_ptr(), 10) };
        assert_eq!(rc, TRUSTED_PROXIES_PUSH_NULL);
        // NULL cidr / zero len.
        let handle = markdown_trusted_proxies_new();
        let rc = unsafe { markdown_trusted_proxies_push(handle, ptr::null(), 0) };
        assert_eq!(rc, TRUSTED_PROXIES_PUSH_NULL);
        unsafe { markdown_trusted_proxies_free(handle) };
    }

    #[test]
    fn decide_base_url_null_inputs_are_invalid() {
        let mut buf = [0u8; 64];
        let mut decision = empty_decision();
        // NULL input.
        let rc = unsafe {
            markdown_decide_base_url(ptr::null(), buf.as_mut_ptr(), buf.len(), &mut decision)
        };
        assert_eq!(rc, DECIDE_BASE_URL_INVALID);
        // NULL out.
        let input = empty_base_url_input();
        let rc = unsafe {
            markdown_decide_base_url(&input, buf.as_mut_ptr(), buf.len(), ptr::null_mut())
        };
        assert_eq!(rc, DECIDE_BASE_URL_INVALID);
        // NULL out_buf.
        let rc = unsafe { markdown_decide_base_url(&input, ptr::null_mut(), 0, &mut decision) };
        assert_eq!(rc, DECIDE_BASE_URL_INVALID);
    }

    #[test]
    fn decide_base_url_trusted_uses_forwarded() {
        let handle = markdown_trusted_proxies_new();
        assert_eq!(push_cidr(handle, "10.0.0.0/8"), TRUSTED_PROXIES_PUSH_OK);

        let src = b"10.1.2.3";
        let xfh = b"api.example.com";
        let xfp = b"https";
        let host = b"origin.example.com";
        let mut input = empty_base_url_input();
        input.source_ip = src.as_ptr();
        input.source_ip_len = src.len();
        input.trusted = handle;
        input.trusted_configured = 1;
        input.x_forwarded_host = xfh.as_ptr();
        input.x_forwarded_host_len = xfh.len();
        input.x_forwarded_proto = xfp.as_ptr();
        input.x_forwarded_proto_len = xfp.len();
        input.host = host.as_ptr();
        input.host_len = host.len();

        let mut buf = [0u8; 128];
        let mut decision = empty_decision();
        let rc =
            unsafe { markdown_decide_base_url(&input, buf.as_mut_ptr(), buf.len(), &mut decision) };
        assert_eq!(rc, DECIDE_BASE_URL_OK);
        let url = std::str::from_utf8(&buf[..decision.base_url_len]).unwrap();
        assert_eq!(url, "https://api.example.com");
        // BaseUrlReason::ForwardedHeaderTrusted == 0
        assert_eq!(decision.reason, 0);
        // BaseUrlSource::XForwarded == 1
        assert_eq!(decision.source, 1);

        unsafe { markdown_trusted_proxies_free(handle) };
    }

    #[test]
    fn decide_base_url_untrusted_source_ignores_forwarded() {
        let handle = markdown_trusted_proxies_new();
        assert_eq!(push_cidr(handle, "10.0.0.0/8"), TRUSTED_PROXIES_PUSH_OK);

        let src = b"203.0.113.7";
        let xfh = b"spoof.example.com";
        let host = b"origin.example.com";
        let mut input = empty_base_url_input();
        input.source_ip = src.as_ptr();
        input.source_ip_len = src.len();
        input.trusted = handle;
        input.trusted_configured = 1;
        input.x_forwarded_host = xfh.as_ptr();
        input.x_forwarded_host_len = xfh.len();
        input.host = host.as_ptr();
        input.host_len = host.len();

        let mut buf = [0u8; 128];
        let mut decision = empty_decision();
        let rc =
            unsafe { markdown_decide_base_url(&input, buf.as_mut_ptr(), buf.len(), &mut decision) };
        assert_eq!(rc, DECIDE_BASE_URL_OK);
        let url = std::str::from_utf8(&buf[..decision.base_url_len]).unwrap();
        assert_eq!(url, "http://origin.example.com");
        // BaseUrlReason::ForwardedHeaderUntrusted == 1
        assert_eq!(decision.reason, 1);
        // BaseUrlSource::Host == 2
        assert_eq!(decision.source, 2);

        unsafe { markdown_trusted_proxies_free(handle) };
    }

    #[test]
    fn decide_base_url_buffer_too_small() {
        let mut input = empty_base_url_input();
        let host = b"averylonghostname.example.com";
        input.host = host.as_ptr();
        input.host_len = host.len();
        let mut buf = [0u8; 4];
        let mut decision = empty_decision();
        let rc =
            unsafe { markdown_decide_base_url(&input, buf.as_mut_ptr(), buf.len(), &mut decision) };
        assert_eq!(rc, DECIDE_BASE_URL_INVALID);
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
    fn decompress_bounded_empty_output_returns_null() {
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;

        // Compress an empty payload — the decompressor succeeds but
        // produces a zero-length output.  The FFI layer must return
        // output=NULL + output_len=0 (not a dangling thin pointer) so
        // that decompress_free can safely skip the buffer.
        let original: Vec<u8> = Vec::new();
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(&original).unwrap();
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
        assert!(
            result.output.is_null(),
            "output must be NULL for empty result"
        );
        assert_eq!(result.output_len, 0);

        // Free must be a safe no-op on the NULL/zero-length buffer.
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

    #[test]
    fn make_decision_panic_fallback_writes_skip_and_ffi_error() {
        use crate::decision::reason_code::ReasonCode;

        let mut result: FFIDecisionResult = unsafe { std::mem::zeroed() };
        /* Poison the output to ensure the function overwrites stale bytes
         * rather than leaving them. decision=0x5a, reason_code=0x5a. */
        result.decision = 0x5a;
        result.reason_code = 0x5a;

        set_test_panic(Some("make_decision"));
        unsafe {
            markdown_make_decision(1, 1, 1, 1, 0, 1, 0, 0, &mut result);
        }
        set_test_panic(None);

        assert_eq!(
            result.decision, 1,
            "panic fallback must set decision=skip (1), got {}",
            result.decision
        );
        assert_eq!(
            result.reason_code,
            ReasonCode::FfiPanic.discriminant() as u8,
            "panic fallback must set reason_code=FfiPanic ({}), got {}",
            ReasonCode::FfiPanic.discriminant(),
            result.reason_code
        );
    }

    #[test]
    fn make_decision_normal_path_overwrites_fallback() {
        /* Disabled module → SkipReason::Disabled. Ensures the fallback
         * pre-init does not leak into the success path. */
        let mut result: FFIDecisionResult = unsafe { std::mem::zeroed() };
        unsafe {
            markdown_make_decision(0, 1, 0, 0, 0, 1, 0, 0, &mut result);
        }
        assert_eq!(result.decision, 1);
        assert_eq!(
            result.reason_code,
            crate::decision::reason_code::ReasonCode::Disabled.discriminant() as u8
        );
    }

    #[test]
    fn is_dangerous_url_panic_fallback_returns_one_fail_closed() {
        let url = b"https://example.com/";
        set_test_panic(Some("is_dangerous_url"));
        let rc = unsafe { markdown_is_dangerous_url(url.as_ptr(), url.len()) };
        set_test_panic(None);
        assert_eq!(
            rc, 1,
            "markdown_is_dangerous_url panic fallback must fail-closed (return 1)"
        );
    }

    #[test]
    fn validate_url_panic_fallback_returns_zero_unsafe() {
        /* Inject a panic inside markdown_validate_url to exercise the
         * catch_unwind Err branch. The documented contract is
         * panic -> 0 (fail-closed unsafe). */
        let url = b"https://example.com/";
        set_test_panic(Some("validate_url"));
        let rc = unsafe { markdown_validate_url(url.as_ptr(), url.len()) };
        set_test_panic(None);
        assert_eq!(
            rc, 0,
            "markdown_validate_url panic fallback must return 0 (fail-closed unsafe)"
        );
    }

    #[test]
    fn validate_url_normal_path_returns_one() {
        let url = b"https://example.com/";
        let rc = unsafe { markdown_validate_url(url.as_ptr(), url.len()) };
        assert_eq!(rc, 1, "valid URL should be marked safe (1)");
    }

    fn default_eligibility_input() -> FFIEligibilityInput {
        FFIEligibilityInput {
            filter_enabled: 1,
            method_get_or_head: 1,
            has_range_header: 0,
            status: 200,
            content_type: ptr::null(),
            content_type_len: 0,
            content_types: ptr::null(),
            content_types_count: 0,
            stream_types: ptr::null(),
            stream_types_count: 0,
            content_length: -1,
            body_limit: 10 * 1024 * 1024,
        }
    }

    #[test]
    fn decide_eligibility_null_input_is_config_skip() {
        let rc = unsafe { markdown_decide_eligibility(ptr::null()) };
        // 8 == IneligibleConfig (safe fail-open: skip conversion).
        assert_eq!(rc, 8);
    }

    #[test]
    fn decide_eligibility_html_eligible() {
        let ct = b"text/html";
        let mut input = default_eligibility_input();
        input.content_type = ct.as_ptr();
        input.content_type_len = ct.len();
        let rc = unsafe { markdown_decide_eligibility(&input) };
        assert_eq!(rc, 0, "text/html with defaults should be eligible");
    }

    #[test]
    fn decide_eligibility_non_html_content_type() {
        let ct = b"application/json";
        let mut input = default_eligibility_input();
        input.content_type = ct.as_ptr();
        input.content_type_len = ct.len();
        let rc = unsafe { markdown_decide_eligibility(&input) };
        assert_eq!(rc, 3, "non-html should be IneligibleContentType");
    }

    #[test]
    fn decide_eligibility_marshals_content_types_array() {
        let ct = b"application/xhtml+xml";
        let entry = FFIStr {
            data: ct.as_ptr(),
            len: ct.len(),
        };
        let allow = [entry];
        let mut input = default_eligibility_input();
        input.content_type = ct.as_ptr();
        input.content_type_len = ct.len();
        input.content_types = allow.as_ptr();
        input.content_types_count = allow.len();
        let rc = unsafe { markdown_decide_eligibility(&input) };
        assert_eq!(rc, 0, "configured allowlist entry should be eligible");
    }

    #[test]
    fn decide_eligibility_marshals_stream_types_array() {
        let ct = b"application/x-ndjson";
        let entry = FFIStr {
            data: ct.as_ptr(),
            len: ct.len(),
        };
        let stream = [entry];
        let mut input = default_eligibility_input();
        input.content_type = ct.as_ptr();
        input.content_type_len = ct.len();
        input.stream_types = stream.as_ptr();
        input.stream_types_count = stream.len();
        let rc = unsafe { markdown_decide_eligibility(&input) };
        assert_eq!(
            rc, 5,
            "configured stream type should be IneligibleStreaming"
        );
    }

    #[test]
    fn decide_eligibility_size_limit_exceeded() {
        let ct = b"text/html";
        let mut input = default_eligibility_input();
        input.content_type = ct.as_ptr();
        input.content_type_len = ct.len();
        input.content_length = 8192;
        input.body_limit = 4096;
        let rc = unsafe { markdown_decide_eligibility(&input) };
        assert_eq!(rc, 4, "oversized response should be IneligibleSize");
    }

    #[test]
    fn decide_eligibility_disabled_filter_is_config() {
        let mut input = default_eligibility_input();
        input.filter_enabled = 0;
        let rc = unsafe { markdown_decide_eligibility(&input) };
        assert_eq!(rc, 8, "disabled filter should be IneligibleConfig");
    }

    /* ---- markdown_decide_conditional (spec 49) ---- */

    fn empty_conditional_input() -> FFIConditionalInput {
        FFIConditionalInput {
            cache_validation: 2, /* full */
            has_range: 0,
            no_transform: 0,
            if_none_match: ptr::null(),
            if_none_match_len: 0,
            entity_etag: ptr::null(),
            entity_etag_len: 0,
            if_modified_since: ptr::null(),
            if_modified_since_len: 0,
            last_modified: ptr::null(),
            last_modified_len: 0,
        }
    }

    fn empty_conditional_decision() -> FFIConditionalDecision {
        FFIConditionalDecision {
            outcome: 7,
            reason: 7,
            evaluated_header: 7,
        }
    }

    #[test]
    fn decide_conditional_null_out_is_noop() {
        let input = empty_conditional_input();
        /* Must not panic / write through NULL. */
        unsafe { markdown_decide_conditional(&input, ptr::null_mut()) };
    }

    #[test]
    fn decide_conditional_null_input_fails_open_proceed() {
        let mut out = empty_conditional_decision();
        unsafe { markdown_decide_conditional(ptr::null(), &mut out) };
        /* Proceed (1), NoHeaders (0), header None (0). */
        assert_eq!(out.outcome, 1);
        assert_eq!(out.reason, 0);
        assert_eq!(out.evaluated_header, 0);
    }

    #[test]
    fn decide_conditional_empty_input_proceeds() {
        let input = empty_conditional_input();
        let mut out = empty_conditional_decision();
        unsafe { markdown_decide_conditional(&input, &mut out) };
        assert_eq!(out.outcome, 1); /* proceed */
        assert_eq!(out.reason, 0); /* conditional_no_headers */
        assert_eq!(out.evaluated_header, 0);
    }

    #[test]
    fn decide_conditional_inm_match_not_modified() {
        let inm = b"\"abc\"";
        let etag = b"\"abc\"";
        let mut input = empty_conditional_input();
        input.if_none_match = inm.as_ptr();
        input.if_none_match_len = inm.len();
        input.entity_etag = etag.as_ptr();
        input.entity_etag_len = etag.len();
        let mut out = empty_conditional_decision();
        unsafe { markdown_decide_conditional(&input, &mut out) };
        assert_eq!(out.outcome, 0); /* not_modified */
        assert_eq!(out.reason, 1); /* conditional_inm_evaluated */
        assert_eq!(out.evaluated_header, 1); /* if_none_match */
    }

    #[test]
    fn decide_conditional_range_bypass() {
        let mut input = empty_conditional_input();
        input.has_range = 1;
        let mut out = empty_conditional_decision();
        unsafe { markdown_decide_conditional(&input, &mut out) };
        assert_eq!(out.outcome, 2); /* bypass */
        assert_eq!(out.reason, 3); /* bypass_range_request */
    }

    #[test]
    fn decide_conditional_no_transform_bypass() {
        let mut input = empty_conditional_input();
        input.no_transform = 1;
        let mut out = empty_conditional_decision();
        unsafe { markdown_decide_conditional(&input, &mut out) };
        assert_eq!(out.outcome, 2); /* bypass */
        assert_eq!(out.reason, 4); /* bypass_no_transform */
    }

    #[test]
    fn decide_conditional_ims_only_ignores_inm() {
        let inm = b"\"abc\"";
        let etag = b"\"abc\"";
        let ims = b"Sun, 06 Nov 1994 08:49:37 GMT";
        let lm = b"Fri, 04 Nov 1994 08:49:37 GMT";
        let mut input = empty_conditional_input();
        input.cache_validation = 1; /* ims_only */
        input.if_none_match = inm.as_ptr();
        input.if_none_match_len = inm.len();
        input.entity_etag = etag.as_ptr();
        input.entity_etag_len = etag.len();
        input.if_modified_since = ims.as_ptr();
        input.if_modified_since_len = ims.len();
        input.last_modified = lm.as_ptr();
        input.last_modified_len = lm.len();
        let mut out = empty_conditional_decision();
        unsafe { markdown_decide_conditional(&input, &mut out) };
        /* IMS evaluated (not INM): lm earlier than ims -> not modified. */
        assert_eq!(out.outcome, 0); /* not_modified */
        assert_eq!(out.reason, 2); /* conditional_ims_evaluated */
        assert_eq!(out.evaluated_header, 2); /* if_modified_since */
    }

    /* ---- markdown_decide_streaming (spec 49) ---- */

    fn auto_streaming_input() -> FFIStreamingInput {
        FFIStreamingInput {
            policy: 1,           /* auto */
            engine: 1,           /* auto */
            cache_validation: 1, /* ims_only */
            is_head: 0,
            is_not_modified: 0,
            has_range: 0,
            no_transform: 0,
            has_content_encoding: 0,
            content_length_known: 1,
            content_length: 1024 * 1024,
            streaming_threshold: 256 * 1024,
        }
    }

    fn empty_streaming_decision() -> FFIStreamingDecision {
        FFIStreamingDecision {
            eligible: 7,
            block_reason: 7,
        }
    }

    #[test]
    fn decide_streaming_null_out_is_noop() {
        let input = auto_streaming_input();
        unsafe { markdown_decide_streaming(&input, ptr::null_mut()) };
    }

    #[test]
    fn decide_streaming_null_input_fails_safe_full_buffer() {
        let mut out = empty_streaming_decision();
        unsafe { markdown_decide_streaming(ptr::null(), &mut out) };
        assert_eq!(out.eligible, 0);
        assert_eq!(out.block_reason, STREAMING_BLOCK_REASON_NONE);
    }

    #[test]
    fn decide_streaming_auto_large_eligible() {
        let input = auto_streaming_input();
        let mut out = empty_streaming_decision();
        unsafe { markdown_decide_streaming(&input, &mut out) };
        assert_eq!(out.eligible, 1);
        assert_eq!(out.block_reason, STREAMING_BLOCK_REASON_NONE);
    }

    #[test]
    fn decide_streaming_full_cache_validation_blocks() {
        let mut input = auto_streaming_input();
        input.cache_validation = 2; /* full */
        let mut out = empty_streaming_decision();
        unsafe { markdown_decide_streaming(&input, &mut out) };
        assert_eq!(out.eligible, 0);
        assert_eq!(out.block_reason, 0); /* FullCacheValidation */
    }

    #[test]
    fn decide_streaming_small_body_blocks() {
        let mut input = auto_streaming_input();
        input.content_length = 1024;
        let mut out = empty_streaming_decision();
        unsafe { markdown_decide_streaming(&input, &mut out) };
        assert_eq!(out.eligible, 0);
        assert_eq!(out.block_reason, 6); /* SmallBody */
    }

    #[test]
    fn decide_streaming_head_blocks() {
        let mut input = auto_streaming_input();
        input.is_head = 1;
        let mut out = empty_streaming_decision();
        unsafe { markdown_decide_streaming(&input, &mut out) };
        assert_eq!(out.eligible, 0);
        assert_eq!(out.block_reason, 7); /* HeadRequest */
    }

    #[test]
    fn decide_streaming_force_streams_small() {
        let mut input = auto_streaming_input();
        input.policy = 2; /* force */
        input.engine = 2; /* on */
        input.content_length = 1;
        let mut out = empty_streaming_decision();
        unsafe { markdown_decide_streaming(&input, &mut out) };
        assert_eq!(out.eligible, 1);
    }
}
