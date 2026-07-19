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
//! # Primary Entry Points
//!
//! The full set of `#[unsafe(no_mangle)] pub extern "C"` exports in this
//! module includes converter lifecycle (`markdown_converter_new`/`_free`),
//! `markdown_convert`, result lifecycle (`markdown_result_init`/`_free`),
//! accept negotiation (`markdown_negotiate_accept`), eligibility and
//! conditional decisions (`markdown_decide_eligibility`,
//! `markdown_decide_conditional`), header plan build/free/init,
//! trusted-proxy list management (`markdown_trusted_proxies_*`),
//! base URL decision (`markdown_decide_base_url`), bounded decompression
//! (`markdown_decompress_bounded`, `markdown_decompress_free`,
//! `markdown_decomp_result_init`), conflict detection/release
//! (`markdown_detect_conflicts`, `markdown_free_conflicts`), option/result
//! init helpers (`markdown_options_init`), and
//! error classification (`markdown_classify_error_code`).  Incremental
//! and streaming FFI exports live in `ffi/incremental.rs` and
//! `ffi/streaming.rs`.  The table below lists the primary entry points;
//! see the per-function documentation for the complete list.
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
    FFIDecompResult, FFIEligibilityInput, FFIHeaderEntry, FFIHeaderPlan, FFIHeaderPlanHandle,
    FFIStr, MARKDOWN_ABI_VERSION, MarkdownConverterHandle, MarkdownOptions, MarkdownResult,
    NEGOTIATE_REASON_CONVERT, NEGOTIATE_REASON_EXPLICIT_REJECT, NEGOTIATE_REASON_LOWER_Q,
    NEGOTIATE_REASON_MALFORMED, NEGOTIATE_REASON_NO_ACCEPT,
};
use super::abi::{
    FFI_CONFIG_NOT_SET_U8, FFI_CONFIG_NOT_SET_U32, FFI_CONFIG_NOT_SET_U64, FFIConflict,
    FFIConflictLevel, FFIConflictList, FFIEffectiveConfig, FFIExplicitConfig,
};
use super::abi::{FFIConditionalDecision, FFIConditionalInput};
use super::convert::convert_inner;
use super::memory::{free_buffer, reset_result, set_error_result, set_success_result};
use super::options::{required_bytes, required_ref};
use crate::decision::conditional::{
    CacheValidation, ConditionalInput, ConditionalOutcome, decide_conditional,
};
use crate::decision::eligibility::{Eligibility, EligibilityInput, decide_eligibility};
use crate::decision::streaming::StreamingPolicy;
use crate::forwarded::{BaseUrlInput, BaseUrlReason, BaseUrlSource, decide_base_url, parse_cidr};

#[cfg(test)]
thread_local! {
    static TEST_PANIC_TAG: std::cell::Cell<Option<&'static str>> =
        const { std::cell::Cell::new(None) };
    static TEST_CONFLICT_MESSAGES_LIVE: std::cell::Cell<usize> =
        const { std::cell::Cell::new(0) };
}

#[cfg(test)]
fn set_test_panic(tag: Option<&'static str>) {
    TEST_PANIC_TAG.with(|current| current.set(tag));
}

#[cfg(test)]
fn test_should_panic(tag: &'static str) -> bool {
    TEST_PANIC_TAG.with(|current| {
        if current.get() == Some(tag) {
            current.set(None);
            true
        } else {
            false
        }
    })
}

#[cfg(test)]
fn test_conflict_message_created() {
    TEST_CONFLICT_MESSAGES_LIVE.with(|live| live.set(live.get() + 1));
}

#[cfg(test)]
fn test_conflict_message_dropped() {
    TEST_CONFLICT_MESSAGES_LIVE.with(|live| live.set(live.get() - 1));
}

#[cfg(test)]
fn test_conflict_messages_live() -> usize {
    TEST_CONFLICT_MESSAGES_LIVE.with(std::cell::Cell::get)
}

struct HeaderPlanOwned {
    entries: Vec<FFIHeaderEntry>,
    key_storage: Vec<Box<[u8]>>,
    value_storage: Vec<Box<[u8]>>,
}

struct PendingConflictMessage {
    level: FFIConflictLevel,
    bytes: Option<Box<[u8]>>,
}

impl Drop for PendingConflictMessage {
    fn drop(&mut self) {
        #[cfg(test)]
        if self.bytes.is_some() {
            test_conflict_message_dropped();
        }
    }
}

/// Return the bundled Rust/C boundary version.
///
/// This accessor is intentionally trivial and panic-free. The NGINX module
/// calls it during preconfiguration and refuses to parse directives or install
/// filters when the returned value differs from the generated-header
/// expectation.
#[unsafe(no_mangle)]
pub extern "C" fn markdown_abi_version() -> u32 {
    MARKDOWN_ABI_VERSION
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
/// `MarkdownResult` previously initialized by `markdown_convert()`,
/// `markdown_incremental_finalize()`, or `markdown_streaming_finalize()`.
/// Passing the same result twice is allowed because the function resets
/// pointers to NULL.
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
    if !out.is_null() {
        unsafe {
            ptr::write(
                out,
                FFIBaseUrlDecision {
                    base_url_len: 0,
                    reason: BaseUrlReason::FallbackToDefault.as_u8(),
                    source: BaseUrlSource::Default.as_u8(),
                },
            );
        }
    }

    if input.is_null() || out.is_null() || out_buf.is_null() || out_buf_cap == 0 {
        return DECIDE_BASE_URL_INVALID;
    }

    let outcome = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        #[cfg(test)]
        if test_should_panic("decide_base_url") {
            panic!("test-injected panic in markdown_decide_base_url");
        }

        // SAFETY: caller guarantees `input` is a valid FFIBaseUrlInput.
        let inp = unsafe { &*input };

        let source_ip = unsafe { optional_str(inp.source_ip, inp.source_ip_len) }.unwrap_or("");
        let forwarded = unsafe { optional_str(inp.forwarded, inp.forwarded_len) };
        let x_forwarded_proto =
            unsafe { optional_str(inp.x_forwarded_proto, inp.x_forwarded_proto_len) };
        let x_forwarded_host =
            unsafe { optional_str(inp.x_forwarded_host, inp.x_forwarded_host_len) };
        let host = unsafe { optional_str(inp.host, inp.host_len) };
        let direct_scheme = unsafe { optional_str(inp.direct_scheme, inp.direct_scheme_len) };

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
            direct_scheme,
        };

        let decision = decide_base_url(&decision_input, cidrs);
        if decision.base_url.len() > out_buf_cap {
            return None;
        }

        Some((
            decision.base_url,
            decision.reason.as_u8(),
            decision.source.as_u8(),
        ))
    }));

    let Ok(Some((base_url, reason, source))) = outcome else {
        return DECIDE_BASE_URL_INVALID;
    };

    let bytes = base_url.as_bytes();
    unsafe { ptr::copy_nonoverlapping(bytes.as_ptr(), out_buf, bytes.len()) };
    unsafe {
        ptr::write(
            out,
            FFIBaseUrlDecision {
                base_url_len: bytes.len(),
                reason,
                source,
            },
        );
    }

    DECIDE_BASE_URL_OK
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
/// sets every field in the current ABI layout to a valid default. Any future
/// layout change still requires an ABI version increment. After calling this
/// function, the caller may override individual fields as needed.
///
/// Default values:
/// - `flavor`: 0 (CommonMark)
/// - `timeout_ms`: 5000 (5 seconds)
/// - `generate_etag`: 0 (disabled — ETag generation is a config-layer
///   decision; the init helper only provides a safe ABI baseline)
/// - `estimate_tokens`: 0 (disabled)
/// - `front_matter`: 0 (disabled)
/// - `content_type` / `base_url` / selectors: NULL/0
/// - `streaming_budget`: 0 (use engine default)
/// - `prune_noise`: 0 (disabled)
/// - `memory_budget`: 0 (use per-engine defaults)
/// - `llm_provider`: 0 (default)
/// - `chars_per_token_fixed`: 0 (use default ratio)
/// - `parse_timeout_ms`: 0 (fall back to `timeout_ms`)
/// - `parser_memory_budget`: 0 (no per-handle limit; use engine default)
/// - `flush_threshold`: 0 (use default streaming flush threshold)
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
/// to guarantee all fields in the current ABI layout start in a valid zero
/// state. This helper does not make differently sized struct versions
/// interoperable.
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

        // Keep every message under Rust ownership until the message array and
        // FFI array are both complete. A panic during either allocation phase
        // therefore drops all pending boxes instead of leaking buffers.
        let mut pending = Vec::with_capacity(count);
        for conflict in &conflicts {
            let level = match conflict.level {
                crate::config::conflict::ConflictLevel::Error => FFIConflictLevel::Error,
                crate::config::conflict::ConflictLevel::Warning => FFIConflictLevel::Warning,
            };
            pending.push(PendingConflictMessage {
                level,
                bytes: Some(conflict.message.as_bytes().to_vec().into_boxed_slice()),
            });
            #[cfg(test)]
            test_conflict_message_created();
            #[cfg(test)]
            if test_should_panic("detect_conflicts_after_message") {
                panic!("test-injected panic while conflict messages remain guarded");
            }
        }

        let ffi_conflicts = pending
            .iter()
            .map(|message| {
                let bytes = message.bytes.as_deref().unwrap_or_default();
                FFIConflict {
                    level: message.level,
                    message: if bytes.is_empty() {
                        ptr::null()
                    } else {
                        bytes.as_ptr()
                    },
                    message_len: bytes.len(),
                }
            })
            .collect::<Vec<_>>();
        let mut boxed_conflicts = ffi_conflicts.into_boxed_slice();
        let conflicts_ptr = boxed_conflicts.as_mut_ptr();

        // No allocation or fallible conversion remains beyond this point.
        // Transfer the message backing stores and then the FFI array together.
        for message in &mut pending {
            if let Some(bytes) = message.bytes.take() {
                if bytes.is_empty() {
                    drop(bytes);
                    #[cfg(test)]
                    test_conflict_message_dropped();
                } else {
                    std::mem::forget(bytes);
                }
            }
        }
        std::mem::forget(boxed_conflicts);

        FFIConflictList {
            conflicts: conflicts_ptr,
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
                #[cfg(test)]
                test_conflict_message_dropped();
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

use crate::error::classification::classify_error_code;

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
    fn abi_version_matches_generated_header_owner() {
        assert_eq!(markdown_abi_version(), MARKDOWN_ABI_VERSION);
    }

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
            direct_scheme: ptr::null(),
            direct_scheme_len: 0,
        }
    }

    fn empty_decision() -> FFIBaseUrlDecision {
        FFIBaseUrlDecision {
            base_url_len: 0,
            reason: 0xff,
            source: 0xff,
        }
    }

    fn assert_safe_base_url_default(decision: &FFIBaseUrlDecision) {
        use crate::forwarded::{BaseUrlReason, BaseUrlSource};

        assert_eq!(decision.base_url_len, 0);
        assert_eq!(decision.reason, BaseUrlReason::FallbackToDefault.as_u8());
        assert_eq!(decision.source, BaseUrlSource::Default.as_u8());
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
        assert_safe_base_url_default(&decision);
        // NULL out.
        let input = empty_base_url_input();
        let rc = unsafe {
            markdown_decide_base_url(&input, buf.as_mut_ptr(), buf.len(), ptr::null_mut())
        };
        assert_eq!(rc, DECIDE_BASE_URL_INVALID);
        // NULL out_buf.
        let rc = unsafe { markdown_decide_base_url(&input, ptr::null_mut(), 0, &mut decision) };
        assert_eq!(rc, DECIDE_BASE_URL_INVALID);
        assert_safe_base_url_default(&decision);
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
        assert_safe_base_url_default(&decision);
    }

    #[test]
    fn decide_base_url_panic_preserves_safe_default() {
        let input = empty_base_url_input();
        let mut buf = [0u8; 64];
        let mut decision = empty_decision();

        set_test_panic(Some("decide_base_url"));
        let rc =
            unsafe { markdown_decide_base_url(&input, buf.as_mut_ptr(), buf.len(), &mut decision) };

        assert_eq!(rc, DECIDE_BASE_URL_INVALID);
        assert_safe_base_url_default(&decision);
    }

    #[test]
    fn detect_conflicts_panic_releases_pending_messages() {
        let explicit = FFIExplicitConfig {
            accept: FFI_CONFIG_NOT_SET_U8,
            cache_validation: 2,
            streaming: 0,
            limits_memory_bytes: FFI_CONFIG_NOT_SET_U64,
            limits_timeout_ms: FFI_CONFIG_NOT_SET_U64,
            limits_streaming_buffer_bytes: FFI_CONFIG_NOT_SET_U64,
            limits_max_inflight: FFI_CONFIG_NOT_SET_U32,
            error_policy: FFI_CONFIG_NOT_SET_U8,
            diagnostics: FFI_CONFIG_NOT_SET_U8,
        };
        let effective = FFIEffectiveConfig {
            accept: 0,
            cache_validation: 2,
            streaming: 0,
            limits_memory_bytes: 0,
            limits_timeout_ms: 0,
            limits_streaming_buffer_bytes: 0,
            limits_max_inflight: 0,
            error_policy: 0,
            diagnostics: 0,
        };

        assert_eq!(test_conflict_messages_live(), 0);
        set_test_panic(Some("detect_conflicts_after_message"));
        let list = unsafe { markdown_detect_conflicts(3, &explicit, &effective) };

        assert!(list.conflicts.is_null());
        assert_eq!(list.count, 0);
        assert_eq!(
            test_conflict_messages_live(),
            0,
            "message ownership must remain guarded until the full list is ready"
        );
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
    fn decompress_bounded_gzip_concatenated_members() {
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;

        fn gzip_member(payload: &[u8]) -> Vec<u8> {
            let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
            encoder.write_all(payload).unwrap();
            encoder.finish().unwrap()
        }

        let first = b"first FFI member";
        let second = b" and second FFI member";
        let mut compressed = gzip_member(first);
        compressed.extend_from_slice(&gzip_member(second));

        let mut result: FFIDecompResult = unsafe { std::mem::zeroed() };
        let rc = unsafe {
            markdown_decompress_bounded(compressed.as_ptr(), compressed.len(), 0, 1024, &mut result)
        };

        assert_eq!(rc, 0, "Expected success (0), got {rc}");
        assert_eq!(result.error_category, 0);
        assert!(!result.output.is_null());
        assert_eq!(result.output_len, first.len() + second.len());
        let output = unsafe { std::slice::from_raw_parts(result.output, result.output_len) };
        assert_eq!(output, [first.as_slice(), second.as_slice()].concat());

        unsafe { markdown_decompress_free(&mut result) };
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
}
