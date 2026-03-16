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

use std::panic;
use std::ptr;

use crate::incremental::IncrementalConverter;

use super::abi::{
    ERROR_INTERNAL, ERROR_INVALID_INPUT, ERROR_SUCCESS, MarkdownOptions, MarkdownResult,
};
use super::memory::{free_buffer, reset_result, set_error_result};
use super::options::decode_options;

/// Opaque handle wrapping an [`IncrementalConverter`] for the C ABI.
pub struct IncrementalConverterHandle {
    inner: IncrementalConverter,
}

/// Creates a new incremental converter and returns an opaque handle.
///
/// The returned handle must be freed by passing it to
/// [`markdown_incremental_finalize`] or [`markdown_incremental_free`].
///
/// # Safety
///
/// * `options` must point to a valid, properly aligned [`MarkdownOptions`]
///   that remains readable for the duration of this call.
/// * The returned pointer is heap-allocated; the caller owns it and must
///   not dereference it except through the `markdown_incremental_*` family
///   of functions.
///
/// # Returns
///
/// A non-NULL handle on success, or NULL if `options` is NULL or an
/// internal panic is caught.
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
        let decoded = decode_options(opts_ref).map_err(|_| ())?;
        let converter = IncrementalConverter::new(decoded.conversion);
        Ok(Box::into_raw(Box::new(IncrementalConverterHandle {
            inner: converter,
        })))
    });

    match result {
        Ok(Ok(ptr)) => ptr,
        _ => ptr::null_mut(),
    }
}

/// Feeds a chunk of input data into the incremental converter.
///
/// Chunks are buffered internally and concatenated during
/// [`markdown_incremental_finalize`].  An empty chunk (`data_len == 0`)
/// is accepted as a no-op.
///
/// # Safety
///
/// * `handle` must be a live pointer returned by [`markdown_incremental_new`].
/// * `data` must point to at least `data_len` readable bytes, or be NULL
///   when `data_len` is 0.
///
/// # Returns
///
/// `ERROR_SUCCESS` (0) on success, or a non-zero error code on failure.
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

/// Finalizes the incremental conversion and writes the result.
///
/// This function **consumes** the handle — the caller must not use it
/// after this call returns.  On success the Markdown output is written
/// into `result`; on failure `result` carries an error code and message.
///
/// # Safety
///
/// * `handle` must be a live pointer returned by [`markdown_incremental_new`]
///   that has not already been finalized or freed.
/// * `result` must point to a writable [`MarkdownResult`] whose owned
///   buffers are either NULL/zero-length or were previously returned by
///   this API.
///
/// # Returns
///
/// `ERROR_SUCCESS` (0) on success, or a non-zero error code on failure.
/// In both cases `result` is populated accordingly.
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

    let panic_result = panic::catch_unwind(|| {
        // SAFETY: caller guarantees `handle` is a live, unconsumed pointer.
        // `Box::from_raw` takes ownership here — if the closure panics after
        // this point, the Box is dropped during unwinding, so the handle is
        // always freed regardless of success or panic.
        let boxed = unsafe { Box::from_raw(handle) };
        boxed.inner.finalize()
    });

    match panic_result {
        Ok(Ok(markdown)) => {
            let md_bytes = markdown.into_bytes().into_boxed_slice();
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
/// this function.
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
