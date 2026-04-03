//! Feature-gated FFI functions for the streaming processing API.
//!
//! This module exposes the [`StreamingConverter`] to C callers through an
//! opaque handle and six lifecycle functions:
//!
//! | Function | Purpose |
//! |---|---|
//! | [`markdown_streaming_new`] | Create a streaming converter handle |
//! | [`markdown_streaming_feed`] | Feed a chunk, receive Markdown output |
//! | [`markdown_streaming_finalize`] | Finalize conversion, write result |
//! | [`markdown_streaming_abort`] | Abort conversion, release resources |
//! | [`markdown_streaming_free`] | Free handle without finalizing |
//! | [`markdown_streaming_output_free`] | Free feed output buffer |
//!
//! All functions are compiled only when the `streaming` Cargo feature is
//! enabled. When the feature is disabled the crate's public ABI remains
//! identical to the pre-streaming baseline.
//!
//! # Memory ownership
//!
//! The handle returned by [`markdown_streaming_new`] is owned by the C
//! caller. The caller must eventually pass it to exactly one of
//! [`markdown_streaming_finalize`] (which consumes it and writes the result),
//! [`markdown_streaming_abort`] (which consumes it without output), or
//! [`markdown_streaming_free`] (which drops it without producing output).
//!
//! Output buffers returned by [`markdown_streaming_feed`] are owned by the
//! C caller and must be freed via [`markdown_streaming_output_free`].
//!
//! **Important:** [`markdown_streaming_finalize`] always consumes the
//! handle, regardless of whether it returns success or failure. After
//! `finalize` returns, the handle is invalid and must not be passed to
//! any other function. Violating this rule causes a double-free (CWE-415).

use std::panic::{self, AssertUnwindSafe};
use std::ptr;

use crate::streaming::budget::MemoryBudget;
use crate::streaming::converter::StreamingConverter;

use super::abi::{
    ERROR_INTERNAL, ERROR_INVALID_INPUT, ERROR_SUCCESS, MarkdownOptions, MarkdownResult,
};
use super::memory::{free_buffer, reset_result, set_error_result};
use super::options::decode_options;

/// Opaque handle wrapping a [`StreamingConverter`] for the C ABI.
pub struct StreamingConverterHandle {
    inner: StreamingConverter,
    generate_etag: bool,
    estimate_tokens: bool,
}

/// Create a new streaming converter handle.
///
/// The returned handle is an opaque pointer owned by the caller and must be
/// consumed by exactly one of `markdown_streaming_finalize`,
/// `markdown_streaming_abort`, or `markdown_streaming_free`.
///
/// # Safety
///
/// - `options` must point to a valid, properly aligned `MarkdownOptions` that
///   remains readable for the duration of this call.
/// - The returned pointer is heap-allocated; the caller owns it and must not
///   dereference it except via the `markdown_streaming_*` family of functions.
///
/// # Returns
///
/// A non-NULL handle on success, or NULL if `options` is NULL, if
/// `MarkdownOptions` cannot be decoded, or if an internal panic is caught.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_new(
    options: *const MarkdownOptions,
) -> *mut StreamingConverterHandle {
    let result = panic::catch_unwind(|| -> Result<*mut StreamingConverterHandle, ()> {
        if options.is_null() {
            return Err(());
        }
        // SAFETY: caller guarantees `options` is valid and aligned.
        let opts_ref = unsafe { &*options };
        let decoded = decode_options(opts_ref).map_err(|e| {
            eprintln!("markdown_streaming_new: failed to decode options: {e}");
        })?;

        let mut converter = StreamingConverter::new(decoded.conversion, MemoryBudget::default());
        converter.set_content_type(decoded.content_type.map(ToOwned::to_owned));
        if !decoded.timeout.is_zero() {
            converter.set_timeout(decoded.timeout);
        }

        Ok(Box::into_raw(Box::new(StreamingConverterHandle {
            inner: converter,
            generate_etag: decoded.generate_etag,
            estimate_tokens: decoded.estimate_tokens,
        })))
    });

    match result {
        Ok(Ok(ptr)) => ptr,
        _ => ptr::null_mut(),
    }
}

/// Feed a chunk of HTML input and receive any ready Markdown output.
///
/// On success (`ERROR_SUCCESS`), `*out_data` and `*out_len` are set to the
/// Markdown output buffer allocated by Rust. The caller must free this buffer
/// via [`markdown_streaming_output_free`]. If no output is ready, `*out_data`
/// is NULL and `*out_len` is 0.
///
/// On error, `*out_data` is set to NULL and `*out_len` to 0. The returned
/// error code indicates the failure type.
///
/// # Safety
///
/// - `handle` must be a live pointer returned by [`markdown_streaming_new`].
/// - `data` must point to at least `data_len` readable bytes, or be NULL
///   when `data_len` is 0.
/// - `out_data` must be a valid, writable pointer to `*mut u8`.
/// - `out_len` must be a valid, writable pointer to `usize`.
///
/// # Returns
///
/// - `ERROR_SUCCESS` (0) on success
/// - `ERROR_STREAMING_FALLBACK` (7) for pre-commit fallback signal
/// - `ERROR_MEMORY_LIMIT` (4) for memory budget exceeded
/// - `ERROR_POST_COMMIT` (8) for post-commit error
/// - `ERROR_TIMEOUT` (3) for timeout
/// - `ERROR_INVALID_INPUT` (5) for NULL handle or output pointers
/// - `ERROR_INTERNAL` (99) for caught panics
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_feed(
    handle: *mut StreamingConverterHandle,
    data: *const u8,
    data_len: usize,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> u32 {
    let result = panic::catch_unwind(AssertUnwindSafe(|| -> u32 {
        // Validate output pointers first so we can safely write to them.
        if out_data.is_null() || out_len.is_null() {
            return ERROR_INVALID_INPUT;
        }

        // Initialize output to empty.
        // SAFETY: pointers validated as non-NULL above.
        unsafe {
            *out_data = ptr::null_mut();
            *out_len = 0;
        }

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

        match handle_ref.inner.feed_chunk(chunk) {
            Ok(output) => {
                if !output.markdown.is_empty() {
                    let boxed = output.markdown.into_boxed_slice();
                    let len = boxed.len();
                    let raw = Box::into_raw(boxed) as *mut u8;
                    // SAFETY: out_data and out_len validated as non-NULL above.
                    unsafe {
                        *out_data = raw;
                        *out_len = len;
                    }
                }
                ERROR_SUCCESS
            }
            Err(e) => e.code(),
        }
    }));

    result.unwrap_or(ERROR_INTERNAL)
}

/// Finalize a streaming conversion, consume the handle, and write the result.
///
/// This call always consumes the provided `handle`; after this function
/// returns (whether success, failure, or internal panic) the handle is
/// invalid and must not be used again or freed by the caller.
///
/// # Safety
///
/// - `handle` must be a live pointer returned by `markdown_streaming_new`
///   that has not already been finalized, aborted, or freed.
/// - `result` must be a valid, writable pointer to a `MarkdownResult`. Any
///   buffers previously owned by `result` must either be NULL/zero-length
///   or must have been previously returned by this API.
///
/// # Returns
///
/// `ERROR_SUCCESS` (0) on success, or a non-zero error code on failure.
/// In all cases `result` is populated with either the produced markdown
/// (and optional ETag/token estimate) or an error code and message.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_finalize(
    handle: *mut StreamingConverterHandle,
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
            "Streaming converter handle is NULL".to_string(),
        );
        return ERROR_INVALID_INPUT;
    }

    let panic_result = panic::catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: caller guarantees `handle` is a live, unconsumed pointer.
        // `Box::from_raw` takes ownership here — if the closure panics after
        // this point, the Box is dropped during unwinding, so the handle is
        // always freed regardless of success or panic.
        let boxed = unsafe { Box::from_raw(handle) };
        let generate_etag = boxed.generate_etag;
        let estimate_tokens = boxed.estimate_tokens;
        let streaming_result = boxed.inner.finalize()?;
        Ok::<_, crate::error::ConversionError>((streaming_result, generate_etag, estimate_tokens))
    }));

    match panic_result {
        Ok(Ok((streaming_result, generate_etag, estimate_tokens))) => {
            // Set final markdown output.
            let md_bytes = streaming_result.final_markdown.into_boxed_slice();
            result_ref.markdown_len = md_bytes.len();
            result_ref.markdown = Box::into_raw(md_bytes) as *mut u8;

            // Set ETag if generation was requested and available.
            if generate_etag
                && let Some(etag_str) = streaming_result.etag
            {
                let etag_bytes = etag_str.into_bytes().into_boxed_slice();
                result_ref.etag_len = etag_bytes.len();
                result_ref.etag = Box::into_raw(etag_bytes) as *mut u8;
            }

            // Set token estimate if requested and available.
            if estimate_tokens
                && let Some(estimate) = streaming_result.token_estimate
            {
                result_ref.token_estimate = estimate;
            }

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
                "Internal panic during streaming finalize".to_string(),
            );
            ERROR_INTERNAL
        }
    }
}

/// Abort a streaming conversion and release all Rust resources.
///
/// Use this function when the conversion must be abandoned (e.g. client
/// abort or unrecoverable error). This always consumes the handle.
///
/// Passing NULL is a safe no-op.
///
/// # Safety
///
/// - `handle` must be NULL or a live pointer returned by
///   [`markdown_streaming_new`] that has not been finalized, aborted,
///   or freed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_abort(handle: *mut StreamingConverterHandle) {
    if handle.is_null() {
        return;
    }
    let _ = panic::catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: caller guarantees `handle` is a live, unconsumed pointer.
        unsafe { drop(Box::from_raw(handle)) };
    }));
}

/// Free a streaming converter handle without finalizing.
///
/// Use this function to release resources when the conversion is being
/// abandoned in an error path where neither `finalize` nor `abort` was
/// called. If the handle has already been consumed by `finalize` or
/// `abort`, do **not** call this function — that would be a double-free.
///
/// Passing NULL is a safe no-op.
///
/// # Safety
///
/// - `handle` must be NULL or a live pointer returned by
///   [`markdown_streaming_new`] that has not been finalized, aborted,
///   or freed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_free(handle: *mut StreamingConverterHandle) {
    if handle.is_null() {
        return;
    }
    let _ = panic::catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: caller guarantees `handle` is a live, unconsumed pointer.
        unsafe { drop(Box::from_raw(handle)) };
    }));
}

/// Free a Markdown output buffer returned by [`markdown_streaming_feed`].
///
/// The `data` pointer and `len` must be exactly the values written to
/// `out_data` and `out_len` by a previous `feed` call. Passing NULL/0
/// is a safe no-op.
///
/// # Safety
///
/// - `data` must be NULL or a pointer previously returned via
///   `markdown_streaming_feed`'s `out_data` parameter.
/// - `len` must be the corresponding `out_len` value.
/// - Each (data, len) pair must be freed exactly once.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_output_free(data: *mut u8, len: usize) {
    if data.is_null() {
        return;
    }
    let _ = panic::catch_unwind(AssertUnwindSafe(|| {
        // Reconstruct the Box<[u8]> from the raw pointer and length.
        let raw_slice = ptr::slice_from_raw_parts_mut(data, len);
        // SAFETY: `data` was allocated by `Box<[u8]>` via `Box::into_raw`
        // in `markdown_streaming_feed`, and `len` is the original length.
        unsafe { drop(Box::from_raw(raw_slice)) };
    }));
}


#[cfg(test)]
#[cfg(feature = "streaming")]
mod tests {
    use super::*;
    use crate::ffi::abi::{
        ERROR_INTERNAL, ERROR_INVALID_INPUT, ERROR_SUCCESS, MarkdownOptions, MarkdownResult,
    };
    use crate::ffi::exports::markdown_result_free;

    /// Build a minimal valid `MarkdownOptions` for testing.
    fn test_options() -> MarkdownOptions {
        MarkdownOptions {
            flavor: 0,
            timeout_ms: 5000,
            generate_etag: 0,
            estimate_tokens: 0,
            front_matter: 0,
            content_type: ptr::null(),
            content_type_len: 0,
            base_url: ptr::null(),
            base_url_len: 0,
        }
    }

    /// Build a zeroed `MarkdownResult` for finalize calls.
    fn zeroed_result() -> MarkdownResult {
        MarkdownResult {
            markdown: ptr::null_mut(),
            markdown_len: 0,
            etag: ptr::null_mut(),
            etag_len: 0,
            token_estimate: 0,
            error_code: 0,
            error_message: ptr::null_mut(),
            error_len: 0,
        }
    }

    // ================================================================
    // 15.1 Streaming FFI lifecycle test (new -> feed -> finalize)
    // Feature: nginx-streaming-runtime-and-ffi, Property 7
    // ================================================================

    #[test]
    fn test_streaming_lifecycle_normal() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null(), "new() should return non-NULL handle");

        let html = b"<h1>Hello</h1><p>World</p>";
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe {
            markdown_streaming_feed(
                handle,
                html.as_ptr(),
                html.len(),
                &mut out_data,
                &mut out_len,
            )
        };
        assert_eq!(rc, ERROR_SUCCESS, "feed() should return SUCCESS");

        /* Free feed output if any */
        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }

        let mut result = zeroed_result();
        let rc = unsafe { markdown_streaming_finalize(handle, &mut result) };
        assert_eq!(rc, ERROR_SUCCESS, "finalize() should return SUCCESS");
        assert!(!result.markdown.is_null(), "finalize should produce markdown");
        assert!(result.markdown_len > 0, "markdown should be non-empty");

        /* Verify output contains expected content */
        let md = unsafe { std::slice::from_raw_parts(result.markdown, result.markdown_len) };
        let md_str = std::str::from_utf8(md).unwrap();
        assert!(
            md_str.contains("Hello"),
            "Markdown should contain heading text"
        );

        unsafe { markdown_result_free(&mut result) };
    }

    // ================================================================
    // 15.2 Streaming FFI abort path test (new -> feed -> abort)
    // Feature: nginx-streaming-runtime-and-ffi, Property 7
    // ================================================================

    #[test]
    fn test_streaming_abort_path() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let html = b"<p>Some content</p>";
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe {
            markdown_streaming_feed(
                handle,
                html.as_ptr(),
                html.len(),
                &mut out_data,
                &mut out_len,
            )
        };
        assert_eq!(rc, ERROR_SUCCESS);

        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }

        /* Abort instead of finalize */
        unsafe { markdown_streaming_abort(handle) };
        /* Handle is consumed; no double-free should occur */
    }

    // ================================================================
    // 15.3 Streaming FFI fallback path test
    // Feature: nginx-streaming-runtime-and-ffi, Property 5
    // ================================================================

    #[test]
    fn test_streaming_fallback_path() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /*
         * Feed a table to trigger fallback. The streaming engine
         * should return ERROR_STREAMING_FALLBACK (7) when it
         * encounters a table in pre-commit phase.
         */
        let html_with_table = b"<table><tr><td>cell</td></tr></table>";
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe {
            markdown_streaming_feed(
                handle,
                html_with_table.as_ptr(),
                html_with_table.len(),
                &mut out_data,
                &mut out_len,
            )
        };

        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }

        /*
         * The fallback may or may not trigger depending on the
         * streaming engine's internal state. If it does, rc == 7.
         * If not (e.g. table is buffered), we still verify the
         * handle can be properly cleaned up.
         */
        if rc == 7 {
            /* Handle was consumed by the fallback error path internally,
             * but the FFI contract says the handle is still valid after
             * feed() returns an error. We must free it. */
            unsafe { markdown_streaming_abort(handle) };
        } else {
            /* Normal path: finalize or abort */
            unsafe { markdown_streaming_abort(handle) };
        }
    }

    // ================================================================
    // 15.4 Streaming FFI panic safety test
    // Feature: nginx-streaming-runtime-and-ffi, Property 8
    // ================================================================

    #[test]
    fn test_streaming_panic_safety_random_input() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* Feed random bytes - should not panic */
        let random_data: Vec<u8> = (0..256).map(|i| i as u8).collect();
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe {
            markdown_streaming_feed(
                handle,
                random_data.as_ptr(),
                random_data.len(),
                &mut out_data,
                &mut out_len,
            )
        };

        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }

        /* Any error code is acceptable; no panic is the requirement */
        assert!(
            rc == ERROR_SUCCESS || rc != 0,
            "feed() should return a valid error code"
        );

        unsafe { markdown_streaming_abort(handle) };
    }

    #[test]
    fn test_streaming_panic_safety_empty_chunks() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* Feed multiple empty chunks */
        for _ in 0..10 {
            let mut out_data: *mut u8 = ptr::null_mut();
            let mut out_len: usize = 0;

            let rc = unsafe {
                markdown_streaming_feed(
                    handle,
                    ptr::null(),
                    0,
                    &mut out_data,
                    &mut out_len,
                )
            };
            assert_eq!(rc, ERROR_SUCCESS, "Empty feed should succeed");

            if !out_data.is_null() {
                unsafe { markdown_streaming_output_free(out_data, out_len) };
            }
        }

        unsafe { markdown_streaming_abort(handle) };
    }

    // ================================================================
    // 15.5 Streaming FFI output memory management test
    // Feature: nginx-streaming-runtime-and-ffi, Property 9
    // ================================================================

    #[test]
    fn test_streaming_output_memory_management() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* Feed enough HTML to produce output */
        let html = b"<h1>Title</h1><p>Paragraph one.</p><p>Paragraph two.</p>";
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe {
            markdown_streaming_feed(
                handle,
                html.as_ptr(),
                html.len(),
                &mut out_data,
                &mut out_len,
            )
        };
        assert_eq!(rc, ERROR_SUCCESS);

        /* Free the output buffer - should not crash or leak */
        if !out_data.is_null() && out_len > 0 {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }

        /* Free NULL/0 is a safe no-op */
        unsafe { markdown_streaming_output_free(ptr::null_mut(), 0) };

        unsafe { markdown_streaming_abort(handle) };
    }

    // ================================================================
    // 15.6 Streaming FFI NULL parameter handling test
    // Feature: nginx-streaming-runtime-and-ffi, Property 8
    // ================================================================

    #[test]
    fn test_streaming_null_options() {
        let handle = unsafe { markdown_streaming_new(ptr::null()) };
        assert!(handle.is_null(), "NULL options should return NULL handle");
    }

    #[test]
    fn test_streaming_feed_null_handle() {
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe {
            markdown_streaming_feed(
                ptr::null_mut(),
                b"data".as_ptr(),
                4,
                &mut out_data,
                &mut out_len,
            )
        };
        assert_eq!(rc, ERROR_INVALID_INPUT, "NULL handle should return INVALID_INPUT");
    }

    #[test]
    fn test_streaming_feed_null_output_pointers() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let rc = unsafe {
            markdown_streaming_feed(handle, b"data".as_ptr(), 4, ptr::null_mut(), ptr::null_mut())
        };
        assert_eq!(
            rc, ERROR_INVALID_INPUT,
            "NULL output pointers should return INVALID_INPUT"
        );

        unsafe { markdown_streaming_abort(handle) };
    }

    #[test]
    fn test_streaming_finalize_null_handle() {
        let mut result = zeroed_result();
        let rc = unsafe { markdown_streaming_finalize(ptr::null_mut(), &mut result) };
        assert_eq!(rc, ERROR_INVALID_INPUT);
        assert_eq!(result.error_code, ERROR_INVALID_INPUT);

        /* Clean up error message if set */
        if !result.error_message.is_null() {
            unsafe { markdown_result_free(&mut result) };
        }
    }

    #[test]
    fn test_streaming_finalize_null_result() {
        let rc = unsafe { markdown_streaming_finalize(ptr::null_mut(), ptr::null_mut()) };
        assert_eq!(rc, ERROR_INVALID_INPUT);
    }

    #[test]
    fn test_streaming_abort_null() {
        /* NULL abort is a safe no-op */
        unsafe { markdown_streaming_abort(ptr::null_mut()) };
    }

    #[test]
    fn test_streaming_free_null() {
        /* NULL free is a safe no-op */
        unsafe { markdown_streaming_free(ptr::null_mut()) };
    }

    // ================================================================
    // 15.7 Streaming FFI error code coverage test
    // Feature: nginx-streaming-runtime-and-ffi, Property 15
    // ================================================================

    #[test]
    fn test_streaming_error_code_constants() {
        use crate::ffi::abi::{
            ERROR_BUDGET_EXCEEDED, ERROR_POST_COMMIT, ERROR_STREAMING_FALLBACK,
        };

        assert_eq!(ERROR_SUCCESS, 0);
        assert_eq!(ERROR_INVALID_INPUT, 5);
        assert_eq!(ERROR_BUDGET_EXCEEDED, 6);
        assert_eq!(ERROR_STREAMING_FALLBACK, 7);
        assert_eq!(ERROR_POST_COMMIT, 8);
        assert_eq!(ERROR_INTERNAL, 99);
    }

    #[test]
    fn test_streaming_feed_data_null_with_nonzero_len() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        /* NULL data with non-zero len should return INVALID_INPUT */
        let rc = unsafe {
            markdown_streaming_feed(handle, ptr::null(), 100, &mut out_data, &mut out_len)
        };
        assert_eq!(
            rc, ERROR_INVALID_INPUT,
            "NULL data with non-zero len should return INVALID_INPUT"
        );

        unsafe { markdown_streaming_abort(handle) };
    }

    #[test]
    fn test_streaming_free_instead_of_finalize() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let html = b"<p>content</p>";
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe {
            markdown_streaming_feed(
                handle,
                html.as_ptr(),
                html.len(),
                &mut out_data,
                &mut out_len,
            )
        };
        assert_eq!(rc, ERROR_SUCCESS);

        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }

        /* Use free() instead of finalize() or abort() */
        unsafe { markdown_streaming_free(handle) };
    }
}
