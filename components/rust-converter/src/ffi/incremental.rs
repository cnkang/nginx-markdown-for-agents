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
    ERROR_INTERNAL, ERROR_INVALID_INPUT, ERROR_SUCCESS, MarkdownOptions, MarkdownResult,
};
use super::memory::{free_buffer, reset_result, set_error_result};
use super::options::decode_options;

/// Opaque handle wrapping an [`IncrementalConverter`] for the C ABI.
pub struct IncrementalConverterHandle {
    inner: IncrementalConverter,
    generate_etag: bool,
    estimate_tokens: bool,
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
/// ```ignore
/// # use std::ptr;
/// use nginx_markdown_converter::ffi::{MarkdownOptions, markdown_incremental_new, markdown_incremental_free};
/// // Construct options appropriate for your environment.
/// let opts = MarkdownOptions::default();
/// let handle = unsafe { markdown_incremental_new(&opts) };
/// assert!(!handle.is_null());
/// // Either finalize to produce output or free when done without producing output.
/// unsafe { markdown_incremental_free(handle) };
/// ```ignore
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_incremental_new(
    options: *const MarkdownOptions,
) -> *mut IncrementalConverterHandle {
    let result = panic::catch_unwind(|| -> Result<*mut IncrementalConverterHandle, ()> {
        if options.is_null() {
            return Err(());
        }
        // SAFETY: caller guarantees `options` is valid and aligned.
        let opts_ref = unsafe { &*options };
        let decoded = decode_options(opts_ref).map_err(|e| {
            eprintln!("markdown_incremental_new: failed to decode options: {e}");
        })?;
        let mut converter = IncrementalConverter::new(decoded.conversion);
        converter.set_content_type(decoded.content_type.map(ToOwned::to_owned));
        converter.set_timeout(decoded.timeout);
        Ok(Box::into_raw(Box::new(IncrementalConverterHandle {
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
/// ```ignore
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

        match handle_ref.inner.feed_chunk(chunk) {
            Ok(()) => ERROR_SUCCESS,
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
/// ```ignore
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

    let panic_result = panic::catch_unwind(|| -> Result<(String, bool, bool), ConversionError> {
        // SAFETY: caller guarantees `handle` is a live, unconsumed pointer.
        // `Box::from_raw` takes ownership here — if the closure panics after
        // this point, the Box is dropped during unwinding, so the handle is
        // always freed regardless of success or panic.  The C caller must NOT
        // call `markdown_incremental_free` after this function returns.
        let boxed = unsafe { Box::from_raw(handle) };
        let generate_etag = boxed.generate_etag;
        let estimate_tokens = boxed.estimate_tokens;
        let markdown = boxed.inner.finalize()?;
        Ok((markdown, generate_etag, estimate_tokens))
    });

    match panic_result {
        Ok(Ok((markdown, generate_etag, estimate_tokens))) => {
            let token_estimate = if estimate_tokens {
                TokenEstimator::new().estimate(&markdown)
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
