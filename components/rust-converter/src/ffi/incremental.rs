//! Feature-gated FFI functions for the incremental processing API.
//!
//! This module exposes the [`IncrementalConverter`] to C callers through an
//! opaque handle and four lifecycle functions:
//!
//! | Function | Purpose |
//! |---|---|
//! | [`markdown_incremental_new`] | Create a converter handle |
//! | [`markdown_incremental_feed`] | Feed a chunk of input data |
//! | [`markdown_incremental_finalize`] | Finalize conversion, write result |
//! | [`markdown_incremental_free`] | Free the handle without finalizing |
//!
//! All functions are compiled only when the `incremental` Cargo feature is
//! enabled.  When the feature is disabled the crate's public ABI remains
//! identical to the pre-incremental baseline.
//!
//! # Memory ownership
//!
//! The handle returned by [`markdown_incremental_new`] is owned by the C
//! caller.  The caller must eventually pass it to either
//! [`markdown_incremental_finalize`] (which consumes it) **or**
//! [`markdown_incremental_free`] (which drops it without producing output).
//! Passing the same handle to both is undefined behavior.
//!
//! **Important:** [`markdown_incremental_finalize`] always consumes the
//! handle, regardless of whether it returns success or failure.  After
//! `finalize` returns, the handle is invalid and must not be passed to
//! [`markdown_incremental_free`] or any other function.  Violating this
//! rule causes a double-free (CWE-415).

use std::panic;
use std::ptr;

use crate::error::ConversionError;
use crate::etag_generator::ETagGenerator;
use crate::incremental::IncrementalConverter;
use crate::token_estimator::TokenEstimator;

use super::abi::{
    ERROR_INTERNAL, ERROR_INVALID_INPUT, ERROR_PARSE_BUDGET_EXCEEDED, ERROR_SUCCESS,
    MarkdownOptions, MarkdownResult,
};
use super::convert::{estimate_parser_working_set, max_input_for_parser_budget};
use super::memory::{free_buffer, reset_result, set_error_result};
use super::options::decode_options;

/// Opaque handle wrapping an [`IncrementalConverter`] for the C ABI.
///
/// The handle is produced by [`markdown_incremental_new`] and consumed by
/// either [`markdown_incremental_finalize`] (which produces output) or
/// [`markdown_incremental_free`] (which drops without output).  The C caller
/// owns the handle for its entire lifetime.
///
/// # Fields (FFI boundary semantics)
///
/// * `inner` — The [`IncrementalConverter`] instance that accumulates input
///   chunks and performs the conversion.  Owned by the handle; moved into
///   on construction and consumed on finalization.
/// * `generate_etag` — Whether to compute and emit a BLAKE3-based ETag in
///   the final result.  Set once at construction from `MarkdownOptions`;
///   immutable thereafter.
/// * `estimate_tokens` — Whether to run LLM token-count estimation in the
///   final result.  Set once at construction from `MarkdownOptions`.
/// * `chars_per_token` — The average characters-per-token ratio used by
///   [`TokenEstimator`] when `estimate_tokens` is true.  Stored as `f32`
///   with typical values 1.0–4.0 (CJK–English).  This value is the
///   **normalized** form (clamped to `[1.0, 100.0]` by
///   [`clamp_chars_per_token`](super::options::clamp_chars_per_token))
///   rather than the raw FFI input, so all token-estimation paths behave
///   consistently.
pub struct IncrementalConverterHandle {
    inner: IncrementalConverter,
    parser_memory_budget: u64,
    buffered_input_bytes: usize,
    buffered_tag_openers: usize,
    generate_etag: bool,
    estimate_tokens: bool,
    chars_per_token: f32,
}

/// Create a new incremental converter handle for incremental Markdown processing.
///
/// The returned handle is an opaque pointer owned by the caller and must be
/// either finalized with `markdown_incremental_finalize` or freed with
/// `markdown_incremental_free`.
///
/// # Safety
///
/// - `options` must point to a valid, properly aligned `MarkdownOptions` that
///   remains readable for the duration of this call.
/// - The returned pointer is heap-allocated; the caller owns it and must not
///   dereference it except via the `markdown_incremental_*` family of functions.
///
/// # Returns
///
/// A non-NULL handle on success, or NULL if `options` is NULL, if `MarkdownOptions`
/// cannot be decoded, or if an internal panic is caught.
///
/// # Examples
///
/// ```no_run
/// use nginx_markdown_converter::ffi::{MarkdownOptions, markdown_incremental_new, markdown_incremental_free};
/// // Construct and fully initialize MarkdownOptions for your environment.
/// let opts = MarkdownOptions {
///     flavor: 0,
///     timeout_ms: 0,
///     generate_etag: 0,
///     estimate_tokens: 0,
///     front_matter: 0,
///     content_type: std::ptr::null(),
///     content_type_len: 0,
///     base_url: std::ptr::null(),
///     base_url_len: 0,
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
/// let handle = unsafe { markdown_incremental_new(&opts) };
/// assert!(!handle.is_null());
/// // Either finalize to produce output or free when done without producing output.
/// unsafe { markdown_incremental_free(handle) };
/// ```
/// Create a new incremental converter handle and return an explicit status code.
///
/// This API is the recommended constructor for C callers that need actionable
/// failure classification. On success, `*out_handle` receives a non-NULL handle.
/// On error, `*out_handle` is set to NULL and the function returns an error code.
///
/// Unlike [`markdown_incremental_new`], this function does not write to stderr,
/// making it suitable for use as a library function within NGINX where all
/// diagnostics should go through `ngx_log_error()`.
///
/// # Safety
///
/// - `out_handle` must be a valid, writable pointer to
///   `*mut IncrementalConverterHandle`.
/// - `options` must point to a valid, properly aligned `MarkdownOptions` that
///   remains readable for the duration of this call.
///
/// # Returns
///
/// - `ERROR_SUCCESS` (0) on success
/// - `ERROR_INVALID_INPUT` (5) for NULL pointers or invalid options
/// - `ERROR_INTERNAL` (99) for caught panics
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_incremental_new_with_code(
    options: *const MarkdownOptions,
    out_handle: *mut *mut IncrementalConverterHandle,
) -> u32 {
    if out_handle.is_null() {
        return ERROR_INVALID_INPUT;
    }

    // SAFETY: validated as non-NULL above.
    unsafe { *out_handle = ptr::null_mut() };

    let result = panic::catch_unwind(|| {
        if options.is_null() {
            return Err(ERROR_INVALID_INPUT);
        }

        // SAFETY: caller guarantees `options` is valid and aligned.
        let opts_ref = unsafe { &*options };
        let decoded = decode_options(opts_ref).map_err(|err| err.code())?;

        let max_buffer_size = max_input_for_parser_budget(decoded.parser_memory_budget);
        let mut converter = if decoded.parser_memory_budget == 0 {
            IncrementalConverter::new(decoded.conversion)
        } else {
            IncrementalConverter::with_max_buffer_size(decoded.conversion, max_buffer_size)
        };
        converter.set_content_type(decoded.content_type.map(ToOwned::to_owned));
        converter.set_timeout(decoded.parse_timeout);
        Ok(Box::into_raw(Box::new(IncrementalConverterHandle {
            inner: converter,
            parser_memory_budget: decoded.parser_memory_budget,
            buffered_input_bytes: 0,
            buffered_tag_openers: 0,
            generate_etag: decoded.generate_etag,
            estimate_tokens: decoded.estimate_tokens,
            chars_per_token: decoded.effective_chars_per_token,
        })))
    });

    match result {
        Ok(Ok(handle)) => {
            // SAFETY: validated as non-NULL above.
            unsafe { *out_handle = handle };
            ERROR_SUCCESS
        }
        Ok(Err(code)) => code,
        Err(_) => ERROR_INTERNAL,
    }
}

/// Convenience wrapper around [`markdown_incremental_new_with_code`] that
/// returns only the handle pointer.
///
/// On failure, returns NULL. For actionable error classification, prefer
/// [`markdown_incremental_new_with_code`].
///
/// # Safety
///
/// - `options` must point to a valid, properly aligned `MarkdownOptions` that
///   remains readable for the duration of this call, or be NULL.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_incremental_new(
    options: *const MarkdownOptions,
) -> *mut IncrementalConverterHandle {
    let mut handle: *mut IncrementalConverterHandle = ptr::null_mut();
    let _rc = unsafe { markdown_incremental_new_with_code(options, &mut handle) };
    handle
}

/// Buffers a provided input chunk for later conversion.
///
/// Accepts an empty chunk (`data_len == 0`) as a no-op.
///
/// # Safety
///
/// * `handle` must be a live pointer returned by [`markdown_incremental_new`].
/// * `data` must point to at least `data_len` readable bytes, or be NULL when `data_len` is 0`.
///
/// # Returns
///
/// `ERROR_SUCCESS` (0) on success, or a non-zero error code on failure.
///
/// # Examples
///
/// ```no_run
/// use std::ptr;
/// use nginx_markdown_converter::ffi::{markdown_incremental_new, markdown_incremental_feed, markdown_incremental_free};
/// unsafe {
///     let options = ptr::null(); // populate as needed
///     let handle = markdown_incremental_new(options);
///     if !handle.is_null() {
///         // feed a chunk
///         let _ = markdown_incremental_feed(handle, b"hello".as_ptr(), 5);
///         // finalize or free the handle as appropriate
///         markdown_incremental_free(handle);
///     }
/// }
/// ```
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_incremental_feed(
    handle: *mut IncrementalConverterHandle,
    data: *const u8,
    data_len: usize,
) -> u32 {
    let result = panic::catch_unwind(|| -> u32 {
        if handle.is_null() {
            return ERROR_INVALID_INPUT;
        }
        // SAFETY: caller guarantees `handle` is a live pointer from `_new`.
        let handle_ref = unsafe { &mut *handle };

        let chunk: &[u8] = if data_len == 0 {
            &[]
        } else if data.is_null() {
            return ERROR_INVALID_INPUT;
        } else {
            // SAFETY: caller guarantees `data` points to `data_len` bytes.
            unsafe { std::slice::from_raw_parts(data, data_len) }
        };

        let next_input_bytes = match handle_ref.buffered_input_bytes.checked_add(chunk.len()) {
            Some(value) => value,
            None => return ERROR_PARSE_BUDGET_EXCEEDED,
        };
        let chunk_tag_openers = chunk.iter().filter(|byte| **byte == b'<').count();
        let next_tag_openers = match handle_ref
            .buffered_tag_openers
            .checked_add(chunk_tag_openers)
        {
            Some(value) => value,
            None => return ERROR_PARSE_BUDGET_EXCEEDED,
        };

        if handle_ref.parser_memory_budget > 0
            && estimate_parser_working_set(next_input_bytes, next_tag_openers)
                > handle_ref.parser_memory_budget
        {
            return ERROR_PARSE_BUDGET_EXCEEDED;
        }

        match handle_ref.inner.feed_chunk(chunk) {
            Ok(()) => {
                handle_ref.buffered_input_bytes = next_input_bytes;
                handle_ref.buffered_tag_openers = next_tag_openers;
                ERROR_SUCCESS
            }
            Err(e) => e.code(),
        }
    });

    result.unwrap_or(ERROR_INTERNAL)
}

/// Finalize an incremental converter, consume its handle, and write the conversion output or error into `result`.
///
/// The call always consumes the provided `handle`; after this function returns (whether success, failure,
/// or internal panic) the handle is invalid and must not be used again or freed by the caller.
///
/// # Safety
///
/// - `handle` must be a live pointer returned by `markdown_incremental_new` that has not already been
///   finalized or freed. This function takes ownership of the handle and will free it during execution.
/// - `result` must be a valid, writable pointer to a `MarkdownResult`. Any buffers previously owned by
///   `result` must either be NULL/zero-length or must have been previously returned by this API.
///
/// # Returns
///
/// `ERROR_SUCCESS` (0) on success, or a non-zero error code on failure. In all cases `result` is populated
/// with either the produced markdown (and optional ETag/token estimate) or an error code and message.
///
/// # Examples
///
/// ```no_run
/// use std::ptr::null_mut;
/// use nginx_markdown_converter::ffi::{markdown_incremental_finalize, ERROR_INVALID_INPUT};
///
/// // Passing NULL result pointer is invalid and returns ERROR_INVALID_INPUT.
/// let code = unsafe { markdown_incremental_finalize(null_mut(), null_mut()) };
/// assert_eq!(code, ERROR_INVALID_INPUT);
/// ```
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_incremental_finalize(
    handle: *mut IncrementalConverterHandle,
    result: *mut MarkdownResult,
) -> u32 {
    if result.is_null() {
        return ERROR_INVALID_INPUT;
    }

    // SAFETY: `result` was validated as non-NULL above.
    let result_ref = unsafe { &mut *result };
    free_buffer(&mut result_ref.markdown, &mut result_ref.markdown_len);
    free_buffer(&mut result_ref.etag, &mut result_ref.etag_len);
    free_buffer(&mut result_ref.error_message, &mut result_ref.error_len);
    reset_result(result_ref);

    if handle.is_null() {
        set_error_result(
            result_ref,
            ERROR_INVALID_INPUT,
            "Incremental converter handle is NULL".to_string(),
        );
        return ERROR_INVALID_INPUT;
    }

    let panic_result =
        panic::catch_unwind(|| -> Result<(String, bool, bool, f32), ConversionError> {
            // SAFETY: caller guarantees `handle` is a live, unconsumed pointer.
            // `Box::from_raw` takes ownership here — if the closure panics after
            // this point, the Box is dropped during unwinding, so the handle is
            // always freed regardless of success or panic.  The C caller must NOT
            // call `markdown_incremental_free` after this function returns.
            let boxed = unsafe { Box::from_raw(handle) };
            let generate_etag = boxed.generate_etag;
            let estimate_tokens = boxed.estimate_tokens;
            let chars_per_token = boxed.chars_per_token;
            let markdown = boxed.inner.finalize()?;
            Ok((markdown, generate_etag, estimate_tokens, chars_per_token))
        });

    match panic_result {
        Ok(Ok((markdown, generate_etag, estimate_tokens, chars_per_token))) => {
            let token_estimate = if estimate_tokens {
                TokenEstimator::with_chars_per_token(chars_per_token).estimate(&markdown)
            } else {
                0
            };

            let md_bytes = markdown.into_bytes().into_boxed_slice();

            if generate_etag {
                let etag = ETagGenerator::new()
                    .generate(md_bytes.as_ref())
                    .into_bytes()
                    .into_boxed_slice();
                result_ref.etag_len = etag.len();
                result_ref.etag = Box::into_raw(etag) as *mut u8;
            }

            result_ref.token_estimate = token_estimate;
            result_ref.markdown_len = md_bytes.len();
            result_ref.markdown = Box::into_raw(md_bytes) as *mut u8;
            result_ref.error_code = ERROR_SUCCESS;
            ERROR_SUCCESS
        }
        Ok(Err(err)) => {
            let code = err.code();
            set_error_result(result_ref, code, err.to_string());
            code
        }
        Err(_) => {
            set_error_result(
                result_ref,
                ERROR_INTERNAL,
                "Internal panic during incremental finalize".to_string(),
            );
            ERROR_INTERNAL
        }
    }
}

/// Frees an incremental converter handle without finalizing.
///
/// Use this function to release resources when the conversion is being
/// abandoned (e.g. on error or cancellation).  If the handle has already
/// been consumed by [`markdown_incremental_finalize`], do **not** call
/// this function — `finalize` always consumes the handle regardless of
/// its return code, and calling `free` afterwards is a double-free.
///
/// Passing NULL is a safe no-op.
///
/// # Safety
///
/// * `handle` must be NULL or a live pointer returned by
///   [`markdown_incremental_new`] that has not been finalized or freed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_incremental_free(handle: *mut IncrementalConverterHandle) {
    if handle.is_null() {
        return;
    }
    // SAFETY: caller guarantees `handle` is a live, unconsumed pointer.
    unsafe { drop(Box::from_raw(handle)) };
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ffi::exports::{markdown_options_init, markdown_result_free};

    fn options() -> MarkdownOptions {
        let mut options = unsafe { std::mem::zeroed() };
        unsafe { markdown_options_init(&mut options) };
        options
    }

    #[test]
    fn constructor_applies_parser_budget_to_amplified_markup() {
        let mut options = options();
        options.parser_memory_budget = 32 * 1024;
        let handle = unsafe { markdown_incremental_new(&options) };
        assert!(!handle.is_null());

        let dense_markup = b"<i></i>".repeat(32);
        let code =
            unsafe { markdown_incremental_feed(handle, dense_markup.as_ptr(), dense_markup.len()) };
        assert_eq!(code, ERROR_PARSE_BUDGET_EXCEEDED);

        unsafe { markdown_incremental_free(handle) };
    }

    #[test]
    fn constructor_uses_parse_timeout_for_finalize() {
        let mut options = options();
        options.timeout_ms = 60_000;
        options.parse_timeout_ms = 1;
        let handle = unsafe { markdown_incremental_new(&options) };
        assert!(!handle.is_null());

        let html = b"<div>text</div>".repeat(20_000);
        assert_eq!(
            unsafe { markdown_incremental_feed(handle, html.as_ptr(), html.len()) },
            ERROR_SUCCESS
        );

        let mut result: MarkdownResult = unsafe { std::mem::zeroed() };
        let code = unsafe { markdown_incremental_finalize(handle, &mut result) };
        assert_eq!(code, crate::ffi::ERROR_TIMEOUT);
        unsafe { markdown_result_free(&mut result) };
    }
}
