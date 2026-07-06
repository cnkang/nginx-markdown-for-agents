//! Feature-gated FFI functions for the streaming processing API.
//!
//! This module exposes the [`StreamingConverter`] to C callers through an
//! opaque handle and nine lifecycle functions:
//!
//! | Function | Purpose |
//! |---|---|
//! | [`markdown_streaming_new`] | Create a streaming converter handle |
//! | [`markdown_streaming_feed`] | Feed a chunk, receive Markdown output |
//! | [`markdown_streaming_finish`] | Signal EOF, flush output, consume handle (lightweight) |
//! | [`markdown_streaming_finalize`] | Finalize conversion, write full result struct |
//! | [`markdown_streaming_safe_finish`] | Post-commit safe finish: close open structures |
//! | [`markdown_streaming_abort`] | Abort conversion, release resources |
//! | [`markdown_streaming_free`] | Free handle without finalizing |
//! | [`markdown_streaming_output_free`] | Free feed/finish output buffer |
//! | [`markdown_streaming_reason`] | Get fallback/error reason string |
//!
//! All functions are compiled only when the `streaming` Cargo feature is
//! enabled. When the feature is disabled the crate's public ABI remains
//! identical to the pre-streaming baseline.
//!
//! # Memory ownership
//!
//! The handle returned by [`markdown_streaming_new`] is owned by the C
//! caller. The caller must eventually pass it to exactly one of
//! [`markdown_streaming_finish`] (which consumes it and returns flushed output),
//! [`markdown_streaming_finalize`] (which consumes it and writes the full result),
//! [`markdown_streaming_safe_finish`] (which consumes it and attempts post-commit closure),
//! [`markdown_streaming_abort`] (which consumes it without output), or
//! [`markdown_streaming_free`] (which drops it without producing output).
//!
//! **Important:** Both [`markdown_streaming_finish`] and
//! [`markdown_streaming_finalize`] always consume the handle, regardless
//! of whether they return success or failure. After either returns, the
//! handle is invalid and must not be passed to any other function.
//! Violating this rule causes a double-free (CWE-415).
//!
//! # Output buffer ownership contract
//!
//! Output buffers returned by [`markdown_streaming_feed`] and
//! [`markdown_streaming_finish`] are Rust-allocated heap memory. The
//! following rules govern their lifetime:
//!
//! 1. **Ownership transfer:** When `feed` or `finish` returns
//!    `ERROR_SUCCESS` with non-NULL `out_data`, ownership of the buffer
//!    transfers to the C caller. Rust retains no reference to it.
//!
//! 2. **Required free path:** The C caller MUST release the buffer by
//!    calling [`markdown_streaming_output_free`] with the exact `(data, len)`
//!    pair returned by `feed` or `finish`. No other deallocation method is
//!    valid.
//!
//! 3. **Forbidden allocator pairing:** C MUST NOT free these buffers with
//!    `ngx_pfree`, `free()`, or any allocator other than
//!    [`markdown_streaming_output_free`]. The buffers are allocated by the
//!    Rust global allocator; using a mismatched deallocator (libc `free`,
//!    NGINX pool free, etc.) is undefined behaviour.
//!
//! 4. **Single-free rule:** Each `(data, len)` pair MUST be freed exactly
//!    once. Double-free is undefined behaviour (CWE-415).
//!
//! 5. **No pointer retention across calls:** C MUST NOT retain or
//!    dereference the output buffer pointer after calling
//!    [`markdown_streaming_output_free`]. After free, the pointer is
//!    dangling.
//!
//! 6. **Typical NGINX integration pattern:** C copies the output bytes
//!    into an NGINX pool-owned chain buffer immediately after a successful
//!    `feed`/`finish` call, then frees the Rust buffer before the next
//!    FFI call. This ensures Rust-owned memory is short-lived and does not
//!    outlive the FFI call boundary.
//!
//! 7. **NULL safety:** Passing `(NULL, 0)` to `output_free` is a safe
//!    no-op, which simplifies error-path cleanup.
//!
//! 8. **Lifetime:** The output buffer remains valid from the moment
//!    `feed`/`finish` returns until `output_free` is called. Rust does
//!    not invalidate or reuse the memory between calls.

use std::ffi::{CString, c_char};
use std::panic::{self, AssertUnwindSafe};
use std::ptr;

use crate::streaming::budget::MemoryBudget;
use crate::streaming::converter::StreamingConverter;

use super::abi::{
    ERROR_INTERNAL, ERROR_INVALID_INPUT, ERROR_SUCCESS, MarkdownOptions, MarkdownResult,
};
use super::memory::{free_buffer, reset_result, set_error_result};
use super::options::decode_options;

const DEFAULT_FLUSH_THRESHOLD: usize = 16 * 1024;

fn effective_flush_threshold(raw: u32) -> usize {
    if raw == 0 {
        DEFAULT_FLUSH_THRESHOLD
    } else {
        raw as usize
    }
}

/// Configuration options for the streaming converter, passed from C to Rust.
///
/// This `#[repr(C)]` struct is the purpose-built configuration interface for
/// the streaming (incremental) conversion path. C callers populate this struct
/// and pass a pointer to [`markdown_streaming_new`] (or a future overload that
/// accepts `StreamingOptions` directly) to create a converter instance.
///
/// # Memory Budget
///
/// The `streaming_budget` field controls the total memory ceiling for the
/// streaming converter. When non-zero it overrides the compiled-in default
/// (2 MiB). Peak working-set memory is bounded to `O(streaming_budget)`,
/// not `O(input_size)`, for supported content.
///
/// # Flush Behaviour
///
/// `flush_threshold` controls the minimum number of accumulated Markdown bytes
/// before the converter flushes output. A threshold of 0 means "use the
/// default threshold" so zero-initialized FFI callers keep stable batching
/// semantics. Larger values reduce per-chunk FFI call overhead at the cost of
/// slightly delayed output.
///
/// # Pointer Fields
///
/// Pointer/length pairs (`base_url`, `content_type`) are borrowed from the
/// C caller for the duration of the `markdown_streaming_new` call only. Rust
/// copies any values it needs to retain. The caller must not free these
/// buffers until `markdown_streaming_new` returns.
///
/// # ABI Stability
///
/// This streaming ABI intentionally keeps its own `#[repr(C)]` layout instead
/// of embedding or aliasing `MarkdownOptions`. Repeated fields are kept so the
/// streaming and full-buffer FFI contracts remain independently clear and
/// stable for C callers. When adding shared semantics, update both structs,
/// generated headers, layout tests, and docs together; save structural reuse
/// for a future breaking ABI version.
///
/// Adding fields to this struct is a breaking ABI change. Both copies of
/// `markdown_converter.h` (in `components/rust-converter/include/` and
/// `components/nginx-module/src/`) must be updated in the same change set.
/// See AGENTS.md Rule 15.
#[repr(C)]
pub struct StreamingOptions {
    /// Markdown flavor selector.
    ///
    /// - `0` = CommonMark (default)
    /// - `1` = GitHub Flavored Markdown (GFM)
    /// - `2` = MDX (experimental)
    /// - `3` = Org-mode (experimental)
    ///
    /// Other values are rejected during option decoding.
    pub flavor: u32,

    /// Cooperative conversion timeout in milliseconds.
    ///
    /// The streaming converter checks the deadline after processing each
    /// chunk. If the elapsed wall-clock time exceeds this value, `feed`
    /// returns `ERROR_TIMEOUT`. A value of `0` disables the timeout.
    pub timeout_ms: u32,

    /// Total streaming memory budget in bytes (0 = use compiled-in default).
    ///
    /// Controls the peak memory ceiling for the streaming path. The
    /// converter distributes this budget across its internal subsystems
    /// (token buffer, nesting stack, pending output, charset sniff).
    /// Exceeding this budget causes `feed` to return `ERROR_BUDGET_EXCEEDED`.
    ///
    /// Populated from `markdown_limits streaming_buffer=<size>` (Config V2).
    pub streaming_budget: u64,

    /// Flush threshold in bytes (0 = use default threshold).
    ///
    /// Controls the minimum number of accumulated output bytes before
    /// `feed` returns non-empty output to the caller. A value of 0 means
    /// "use the default threshold" so zero-initialized callers do not
    /// accidentally opt into lowest-latency flushing.
    ///
    /// Larger values allow the emitter to batch output across multiple
    /// elements, reducing FFI round-trip frequency at the cost of slightly
    /// delayed delivery to the downstream filter chain.
    pub flush_threshold: u32,

    /// Non-zero when Rust should generate a streaming Markdown-variant ETag.
    ///
    /// When enabled, the converter maintains an incremental BLAKE3 hash
    /// over emitted Markdown bytes. The final ETag is available after
    /// `markdown_streaming_finalize` completes successfully.
    pub generate_etag: u8,

    /// Non-zero when Rust should estimate token count for the output.
    ///
    /// Token estimation uses a character-counting heuristic with the
    /// configured `chars_per_token` ratio. The estimate is available
    /// in `MarkdownResult.token_estimate` after finalize.
    pub estimate_tokens: u8,

    /// Non-zero when Rust should extract YAML front matter metadata.
    ///
    /// Enables extraction of title, description, canonical URL and other
    /// metadata from `<head>` during streaming. Front matter is prepended
    /// to the final Markdown output on finalize.
    pub front_matter: u8,

    /// Non-zero when noise region pruning is enabled.
    ///
    /// When enabled, structural HTML regions matching the configured prune
    /// selectors (e.g. `<nav>`, `<footer>`, `<aside>`) are excluded from
    /// the Markdown output. Protection selectors override prune selectors.
    pub prune_noise: u8,

    /// Borrowed Content-Type bytes from the C caller (charset detection hint).
    ///
    /// Must either be NULL with `content_type_len == 0` or point to
    /// `content_type_len` readable bytes for the duration of the FFI call.
    /// Rust copies the value if needed. Used for charset sniffing when the
    /// upstream response declares a non-UTF-8 encoding.
    pub content_type: *const u8,

    /// Length in bytes of the `content_type` field.
    pub content_type_len: usize,

    /// Borrowed base URL for resolving relative links in the HTML.
    ///
    /// Must either be NULL with `base_url_len == 0` or point to
    /// `base_url_len` readable UTF-8 bytes for the duration of the FFI call.
    /// Rust copies the value for internal use. When set, relative URLs in
    /// `<a href>`, `<img src>`, and similar attributes are resolved against
    /// this base before emission.
    pub base_url: *const u8,

    /// Length in bytes of the `base_url` field.
    pub base_url_len: usize,

    /// Borrowed prune selector string (space-separated tag names to prune).
    ///
    /// Example: `"nav footer aside"`. Must either be NULL with
    /// `prune_selector_len == 0` or point to `prune_selector_len` readable
    /// UTF-8 bytes. Rust copies the value for internal use.
    pub prune_selectors: *const u8,

    /// Length in bytes of the `prune_selectors` field.
    pub prune_selector_len: usize,

    /// Borrowed protection selector string (space-separated tag names to protect).
    ///
    /// Elements matching both a prune selector and a protection selector are
    /// kept (protection wins). Must either be NULL with
    /// `prune_protection_selector_len == 0` or point to
    /// `prune_protection_selector_len` readable UTF-8 bytes.
    pub prune_protection_selectors: *const u8,

    /// Length in bytes of the `prune_protection_selectors` field.
    pub prune_protection_selector_len: usize,

    /// LLM provider for token estimation.
    ///
    /// - `0` = default (4.0 chars/token)
    /// - `1` = OpenAI GPT
    /// - `2` = Anthropic Claude
    /// - `3` = Google Gemini
    /// - `4` = Meta Llama
    ///
    /// Overridden by `chars_per_token_fixed` when that field is non-zero.
    pub llm_provider: u8,

    /// Explicit chars-per-token ratio (fixed-point: raw = value / 10.0).
    ///
    /// When non-zero, overrides both the default 4.0 and the provider-specific
    /// ratio. Non-zero `u8` values represent raw ratios 0.1-25.5 chars/token;
    /// decoded values below 1.0 are clamped to the effective minimum 1.0.
    /// Value `0` means "use default or provider ratio".
    pub chars_per_token_fixed: u8,
}

/// Opaque handle wrapping a [`StreamingConverter`] for the C ABI.
pub struct StreamingConverterHandle {
    inner: StreamingConverter,
    generate_etag: bool,
    estimate_tokens: bool,
    /// NUL-terminated reason string from the last `feed` or `finish` call
    /// that returned a fallback or error code. Rust owns the buffer. The
    /// pointer returned by `markdown_streaming_reason` is valid only until the
    /// next `feed`, `finish`, `abort`, or handle-free operation.
    last_reason: Option<CString>,
}

fn budget_from_streaming_total(streaming_budget: u64) -> MemoryBudget {
    if streaming_budget > 0 {
        // Saturate rather than wrap when usize is narrower than u64 (e.g. on
        // 32-bit targets); on 64-bit targets this is an identity conversion.
        // Wrapping here would silently shrink the configured budget.
        let total = usize::try_from(streaming_budget).unwrap_or(usize::MAX);
        MemoryBudget::for_total(total)
    } else {
        MemoryBudget::default()
    }
}

fn markdown_streaming_new_impl(
    options: *const MarkdownOptions,
) -> Result<*mut StreamingConverterHandle, u32> {
    if options.is_null() {
        return Err(ERROR_INVALID_INPUT);
    }

    // SAFETY: caller guarantees `options` is valid and aligned.
    let opts_ref = unsafe { &*options };
    let decoded = decode_options(opts_ref).map_err(|err| err.code())?;

    let budget = budget_from_streaming_total(decoded.streaming_budget);

    let mut converter = StreamingConverter::with_chars_per_token(
        decoded.conversion,
        budget,
        decoded.chars_per_token,
    );
    converter.set_content_type(decoded.content_type.map(ToOwned::to_owned));
    if !decoded.timeout.is_zero() {
        converter.set_timeout(decoded.timeout);
    }
    if decoded.parser_memory_budget > 0 {
        converter.set_parser_budget(decoded.parser_memory_budget);
    }
    converter.set_flush_threshold(effective_flush_threshold(opts_ref.flush_threshold));

    Ok(Box::into_raw(Box::new(StreamingConverterHandle {
        inner: converter,
        generate_etag: decoded.generate_etag,
        estimate_tokens: decoded.estimate_tokens,
        last_reason: None,
    })))
}

/// Create a new streaming converter handle and return an explicit status code.
///
/// This API is the recommended constructor for C callers that need actionable
/// failure classification. On success, `*out_handle` receives a non-NULL handle.
/// On error, `*out_handle` is set to NULL and the function returns an error code.
///
/// # Safety
///
/// - `out_handle` must be a valid, writable pointer to `*mut StreamingConverterHandle`.
/// - `options` must point to a valid, properly aligned `MarkdownOptions` that
///   remains readable for the duration of this call.
///
/// # Returns
///
/// - `ERROR_SUCCESS` (0) on success
/// - `ERROR_INVALID_INPUT` (5) for NULL pointers or invalid options
/// - `ERROR_INTERNAL` (99) for caught panics
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_new_with_code(
    options: *const MarkdownOptions,
    out_handle: *mut *mut StreamingConverterHandle,
) -> u32 {
    if out_handle.is_null() {
        return ERROR_INVALID_INPUT;
    }

    // SAFETY: validated as non-NULL above.
    unsafe { *out_handle = ptr::null_mut() };

    let result = panic::catch_unwind(|| markdown_streaming_new_impl(options));

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
    let mut handle = ptr::null_mut();
    let rc = unsafe { markdown_streaming_new_with_code(options, &mut handle) };
    if rc == ERROR_SUCCESS {
        handle
    } else {
        ptr::null_mut()
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
/// - `ERROR_BUDGET_EXCEEDED` (6) for memory budget exceeded
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
                handle_ref.last_reason = None;
                if !output.markdown.is_empty() {
                    let boxed = output.markdown.into_boxed_slice();
                    let (raw, len) = crate::ffi::memory::leak_boxed_slice_to_raw(boxed);
                    // SAFETY: out_data and out_len validated as non-NULL above.
                    unsafe {
                        *out_data = raw;
                        *out_len = len;
                    }
                }
                ERROR_SUCCESS
            }
            Err(e) => {
                let code = e.code();
                // Store the error description as a NUL-terminated C string
                // so C callers can retrieve it via markdown_streaming_reason.
                let msg = e.to_string();
                handle_ref.last_reason = CString::new(msg).ok();
                code
            }
        }
    }));

    result.unwrap_or(ERROR_INTERNAL)
}

/// Signal end-of-input to the streaming converter, flush remaining output,
/// and consume the handle.
///
/// This is a lightweight finish path for C callers that only need the final
/// flushed Markdown bytes and a status code — without the full
/// [`MarkdownResult`] metadata (ETag, token estimate, peak memory) that
/// [`markdown_streaming_finalize`] provides.
///
/// On success (`ERROR_SUCCESS`), `*out_data` and `*out_len` are set to the
/// final Markdown output buffer allocated by Rust. The caller must free this
/// buffer via [`markdown_streaming_output_free`]. If the final flush produces
/// no additional output, `*out_data` is NULL and `*out_len` is 0.
///
/// This call always consumes the provided `handle` when validation passes
/// (handle is non-NULL and output pointers are non-NULL); after this function
/// returns successfully the handle is invalid and must not be passed to any
/// other `markdown_streaming_*` function. If validation fails (NULL handle or
/// NULL output pointers), `ERROR_INVALID_INPUT` is returned and the handle is
/// NOT consumed — the caller remains responsible for freeing or aborting it.
/// Violating this rule causes a double-free (CWE-415).
///
/// # Safety
///
/// - `handle` must be a live pointer returned by [`markdown_streaming_new`]
///   that has not already been finalized, aborted, freed, or finished.
/// - `out_data` must be a valid, writable pointer to `*mut u8`.
/// - `out_len` must be a valid, writable pointer to `usize`.
///
/// # Returns
///
/// - `ERROR_SUCCESS` (0) — conversion completed normally, output available
/// - `ERROR_STREAMING_FALLBACK` (7) — unsupported content detected (pre-commit)
/// - `ERROR_POST_COMMIT` (8) — error after partial output committed
/// - `ERROR_TIMEOUT` (3) — cooperative timeout exceeded during flush
/// - `ERROR_BUDGET_EXCEEDED` (6) — memory budget exceeded during flush
/// - `ERROR_INVALID_INPUT` (5) — NULL handle or output pointers
/// - `ERROR_INTERNAL` (99) — caught panic
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_finish(
    handle: *mut StreamingConverterHandle,
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

        // SAFETY: caller guarantees `handle` is a live, unconsumed pointer.
        // `Box::from_raw` takes ownership — the handle is always freed
        // regardless of success or panic (Box drops during unwinding).
        let boxed = unsafe { Box::from_raw(handle) };

        // Finalize the inner converter: signals EOF, flushes tokenizer,
        // closes unclosed contexts, and returns the final markdown.
        match boxed.inner.finalize() {
            Ok(streaming_result) => {
                let final_md = streaming_result.final_markdown;
                if !final_md.is_empty() {
                    let boxed_md = final_md.into_boxed_slice();
                    let (raw, len) = crate::ffi::memory::leak_boxed_slice_to_raw(boxed_md);
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
/// This call consumes the provided `handle` when validation passes (both
/// `handle` and `result` are non-NULL); after successful consumption the
/// handle is invalid and must not be used again or freed by the caller.
/// If validation fails (NULL `handle` or NULL `result`), `ERROR_INVALID_INPUT`
/// is returned and the handle is NOT consumed — the caller remains responsible
/// for freeing or aborting it.
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
            let (md_ptr, md_len) = crate::ffi::memory::leak_boxed_slice_to_raw(md_bytes);
            result_ref.markdown_len = md_len;
            result_ref.markdown = md_ptr;

            // Set ETag if generation was requested and available.
            if generate_etag && let Some(etag_str) = streaming_result.etag {
                let etag_bytes = etag_str.into_bytes().into_boxed_slice();
                let (etag_ptr, etag_len) = crate::ffi::memory::leak_boxed_slice_to_raw(etag_bytes);
                result_ref.etag_len = etag_len;
                result_ref.etag = etag_ptr;
            }

            // Set token estimate if requested and available.
            if estimate_tokens && let Some(estimate) = streaming_result.token_estimate {
                result_ref.token_estimate = estimate;
            }

            // Set peak memory estimate from streaming stats.
            result_ref.peak_memory_estimate = streaming_result.stats.peak_memory_estimate;

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

/// Attempt a post-commit safe finish: close all open Markdown structures
/// without emitting raw HTML, then consume the handle.
///
/// This function is designed to be called after `markdown_streaming_feed`
/// returns `ERROR_POST_COMMIT` (8). It attempts to gracefully close all
/// open Markdown constructs (headings, paragraphs, lists, code blocks,
/// blockquotes, inline formatting) so that the already-emitted output
/// forms valid Markdown.
///
/// On success (`POST_COMMIT_SAFE_FINISH` = 3), `*out_data` and `*out_len`
/// are set to the Markdown closure bytes (e.g., closing fences, trailing
/// newlines). The caller must free this buffer via
/// [`markdown_streaming_output_free`]. The caller should append these
/// bytes after the already-committed output.
///
/// On failure (`POST_COMMIT_ABORT` = 4), structures could not be safely
/// closed. The output buffer is set to NULL/0. The caller must abort and
/// discard or truncate the partial output. **C MUST NOT infer or synthesize
/// Markdown closure for Rust-owned parser/emitter state** (safety invariant: C must not infer Markdown closure).
///
/// This call consumes the handle after validation succeeds (the handle and
/// output pointers are non-NULL). If validation fails, `ERROR_INVALID_INPUT`
/// is returned and the handle is not consumed; the caller remains responsible
/// for aborting or freeing it. All other return codes consume the handle. In
/// particular, a caught panic (`ERROR_INTERNAL`) can only occur after
/// `Box::from_raw` has taken ownership, so the handle is consumed in that case.
///
/// # Safety
///
/// - `handle` must be a live pointer returned by [`markdown_streaming_new`]
///   that has not already been finalized, aborted, freed, or finished.
/// - `out_data` must be a valid, writable pointer to `*mut u8`.
/// - `out_len` must be a valid, writable pointer to `usize`.
///
/// # Returns
///
/// - `POST_COMMIT_SAFE_FINISH` (3) — all open structures were closed
///   successfully; output buffer contains closure Markdown.
/// - `POST_COMMIT_ABORT` (4) — safe finish failed; output is NULL/0.
///   Caller must abort (discard partial output).
/// - `ERROR_INVALID_INPUT` (5) — NULL handle or output pointers.
/// - `ERROR_INTERNAL` (99) — caught panic.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_safe_finish(
    handle: *mut StreamingConverterHandle,
    out_data: *mut *mut u8,
    out_len: *mut usize,
) -> u32 {
    use super::abi::{POST_COMMIT_ABORT, POST_COMMIT_SAFE_FINISH};

    let result = panic::catch_unwind(AssertUnwindSafe(|| -> u32 {
        // Validate output pointers first.
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

        // SAFETY: caller guarantees `handle` is a live, unconsumed pointer.
        // `Box::from_raw` takes ownership — the handle is always freed
        // regardless of success or failure.
        let boxed = unsafe { Box::from_raw(handle) };

        // Attempt safe finish on the inner converter.
        match boxed.inner.safe_finish() {
            Ok(closing_bytes) => {
                if !closing_bytes.is_empty() {
                    let boxed_md = closing_bytes.into_boxed_slice();
                    let (raw, len) = crate::ffi::memory::leak_boxed_slice_to_raw(boxed_md);
                    // SAFETY: out_data and out_len validated as non-NULL above.
                    unsafe {
                        *out_data = raw;
                        *out_len = len;
                    }
                }
                POST_COMMIT_SAFE_FINISH
            }
            Err(_) => {
                // Safe finish failed — structures cannot be closed gracefully.
                // Caller must abort. Handle is already consumed (boxed dropped).
                POST_COMMIT_ABORT
            }
        }
    }));

    result.unwrap_or(ERROR_INTERNAL)
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
/// This delegates to [`markdown_streaming_abort`] which has identical
/// semantics (both consume the handle by dropping it).
///
/// # Safety
///
/// - `handle` must be NULL or a live pointer returned by
///   [`markdown_streaming_new`] that has not been finalized, aborted,
///   or freed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_free(handle: *mut StreamingConverterHandle) {
    // Identical semantics to abort — delegate to avoid duplication.
    unsafe { markdown_streaming_abort(handle) };
}

/// Free a Markdown output buffer returned by [`markdown_streaming_feed`]
/// or [`markdown_streaming_finish`].
///
/// This is the **only** valid way to release output buffers produced by the
/// streaming FFI. The buffer is allocated by the Rust global allocator; C
/// callers MUST NOT free it with `free()`, `ngx_pfree()`, or any other
/// deallocator — doing so is undefined behaviour due to allocator mismatch.
///
/// The `data` pointer and `len` must be exactly the values written to
/// `out_data` and `out_len` by a previous `feed` or `finish` call.
/// Passing `(NULL, 0)` is a safe no-op, which simplifies error-path cleanup.
///
/// # Typical usage pattern (NGINX integration)
///
/// ```text
/// // C pseudo-code: copy into NGINX chain, then free Rust buffer
/// rc = markdown_streaming_feed(handle, chunk, len, &out_data, &out_len);
/// if (rc == 0 && out_data != NULL) {
///     ngx_memcpy(pool_buf->last, out_data, out_len);
///     pool_buf->last += out_len;
///     markdown_streaming_output_free(out_data, out_len);
/// }
/// ```
///
/// # Safety
///
/// - `data` must be NULL or a pointer previously returned via
///   `markdown_streaming_feed`'s or `markdown_streaming_finish`'s
///   `out_data` parameter.
/// - `len` must be the corresponding `out_len` value from the same call.
/// - Each `(data, len)` pair must be freed exactly once. Double-free is
///   undefined behaviour (CWE-415).
/// - After this call, `data` is a dangling pointer and must not be
///   dereferenced.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_output_free(data: *mut u8, len: usize) {
    if data.is_null() {
        return;
    }
    let _ = panic::catch_unwind(AssertUnwindSafe(|| {
        // Reconstruct the Box<[u8]> from the raw pointer and length.
        let raw_slice = ptr::slice_from_raw_parts_mut(data, len);
        // SAFETY: `data` was allocated by `Box<[u8]>` and transferred via
        // `leak_boxed_slice_to_raw` (`as_mut_ptr` + `mem::forget`) in
        // `markdown_streaming_feed`, and `len` is the original length.
        unsafe { drop(Box::from_raw(raw_slice)) };
    }));
}

/// Return the NUL-terminated reason string from the last `feed` or `finish`
/// call that signalled fallback or error.
///
/// `handle` must be a live streaming handle. The returned pointer is owned by
/// Rust and valid only until the next `feed`, `finish`, `abort`, or handle-free
/// call on the same handle (whichever comes first). The C caller must not free
/// or modify the returned pointer. If C needs to preserve the reason across
/// calls, it must copy the string immediately into an NGINX pool or another
/// caller-owned buffer. Using a reason pointer after handle release is invalid.
///
/// Returns NULL when no reason is available (i.e. the last call returned
/// `ERROR_SUCCESS` or the handle is NULL).
///
/// # Safety
///
/// - `handle` must be NULL or a live pointer returned by
///   [`markdown_streaming_new`] that has not been finalized, aborted,
///   or freed.
/// - The returned `*const c_char` must not be used after the next `feed`,
///   `finish`, `abort`, or `free` call on the same handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_streaming_reason(
    handle: *const StreamingConverterHandle,
) -> *const c_char {
    if handle.is_null() {
        return ptr::null();
    }

    let outcome = panic::catch_unwind(AssertUnwindSafe(|| -> *const c_char {
        // SAFETY: caller guarantees `handle` is a live pointer from `_new`.
        let handle_ref = unsafe { &*handle };
        match &handle_ref.last_reason {
            Some(cstr) => cstr.as_ptr(),
            None => ptr::null(),
        }
    }));
    match outcome {
        Ok(p) => p,
        Err(_) => ptr::null(),
    }
}

#[cfg(test)]
#[cfg(feature = "streaming")]
mod tests {
    use super::*;
    use crate::ffi::abi::{
        ERROR_BUDGET_EXCEEDED, ERROR_INTERNAL, ERROR_INVALID_INPUT, ERROR_POST_COMMIT,
        ERROR_STREAMING_FALLBACK, ERROR_SUCCESS, ERROR_TIMEOUT, MarkdownOptions, MarkdownResult,
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
            streaming_budget: 0,
            prune_noise: 1,
            prune_selectors: ptr::null(),
            prune_selector_len: 0,
            prune_protection_selectors: ptr::null(),
            prune_protection_selector_len: 0,
            memory_budget: 0,
            llm_provider: 0,
            chars_per_token_fixed: 0,
            parse_timeout_ms: 0,
            parser_memory_budget: 0,
            flush_threshold: 0,
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
            peak_memory_estimate: 0,
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
        assert!(
            !result.markdown.is_null(),
            "finalize should produce markdown"
        );
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

        let _rc = unsafe {
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
         *
         * feed() may return ERROR_STREAMING_FALLBACK (7); regardless,
         * the FFI contract requires freeing any out_data and then
         * calling markdown_streaming_abort(handle) to clean up.
         */
        unsafe { markdown_streaming_abort(handle) };
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
            rc == ERROR_SUCCESS
                || rc == ERROR_INVALID_INPUT
                || rc == ERROR_INTERNAL
                || rc == ERROR_STREAMING_FALLBACK
                || rc == ERROR_BUDGET_EXCEEDED
                || rc == ERROR_POST_COMMIT
                || rc == ERROR_TIMEOUT,
            "feed() should return a known error code, got: {}",
            rc
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
                markdown_streaming_feed(handle, ptr::null(), 0, &mut out_data, &mut out_len)
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
    fn test_streaming_new_with_code_success() {
        let opts = test_options();
        let mut handle: *mut StreamingConverterHandle = ptr::null_mut();
        let rc = unsafe { markdown_streaming_new_with_code(&opts, &mut handle) };
        assert_eq!(rc, ERROR_SUCCESS);
        assert!(!handle.is_null());
        unsafe { markdown_streaming_abort(handle) };
    }

    #[test]
    fn test_streaming_new_with_code_null_options() {
        let mut handle: *mut StreamingConverterHandle = ptr::null_mut();
        let rc = unsafe { markdown_streaming_new_with_code(ptr::null(), &mut handle) };
        assert_eq!(rc, ERROR_INVALID_INPUT);
        assert!(handle.is_null(), "error path must keep out handle NULL");
    }

    #[test]
    fn test_streaming_new_with_code_null_out_handle() {
        let opts = test_options();
        let rc = unsafe { markdown_streaming_new_with_code(&opts, ptr::null_mut()) };
        assert_eq!(rc, ERROR_INVALID_INPUT);
    }

    #[test]
    fn test_streaming_new_with_code_decoding_error() {
        let opts = MarkdownOptions {
            content_type: ptr::null(),
            content_type_len: 1,
            ..test_options()
        };
        let mut handle: *mut StreamingConverterHandle = ptr::null_mut();
        let rc = unsafe { markdown_streaming_new_with_code(&opts, &mut handle) };
        assert_eq!(rc, ERROR_INVALID_INPUT);
        assert!(handle.is_null(), "decode failure must not allocate handle");
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
        assert_eq!(
            rc, ERROR_INVALID_INPUT,
            "NULL handle should return INVALID_INPUT"
        );
    }

    #[test]
    fn test_streaming_feed_null_output_pointers() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let rc = unsafe {
            markdown_streaming_feed(
                handle,
                b"data".as_ptr(),
                4,
                ptr::null_mut(),
                ptr::null_mut(),
            )
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
        use crate::ffi::abi::{ERROR_BUDGET_EXCEEDED, ERROR_POST_COMMIT, ERROR_STREAMING_FALLBACK};

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

    #[test]
    fn test_budget_from_streaming_total_scales_down() {
        let budget = budget_from_streaming_total(64 * 1024);
        let sum =
            budget.state_stack + budget.output_buffer + budget.charset_sniff + budget.lookahead;

        assert_eq!(budget.total, 64 * 1024);
        assert_eq!(sum, budget.total);
    }

    // ================================================================
    // StreamingOptions layout tests
    // ================================================================

    #[test]
    fn test_streaming_options_is_repr_c() {
        use std::mem::{align_of, size_of};

        /* Verify the struct has predictable C layout (no Rust reordering) */
        assert_eq!(align_of::<StreamingOptions>(), 8);
        /* Size depends on field count and padding; just verify it is stable */
        assert!(
            size_of::<StreamingOptions>() > 0,
            "StreamingOptions must have non-zero size"
        );
    }

    #[test]
    fn test_streaming_options_field_offsets() {
        use std::mem::offset_of;

        /* Verify field ordering matches the declared layout */
        assert_eq!(offset_of!(StreamingOptions, flavor), 0);
        assert_eq!(offset_of!(StreamingOptions, timeout_ms), 4);
        assert_eq!(offset_of!(StreamingOptions, streaming_budget), 8);
        assert_eq!(offset_of!(StreamingOptions, flush_threshold), 16);
        assert_eq!(offset_of!(StreamingOptions, generate_etag), 20);
        assert_eq!(offset_of!(StreamingOptions, estimate_tokens), 21);
        assert_eq!(offset_of!(StreamingOptions, front_matter), 22);
        assert_eq!(offset_of!(StreamingOptions, prune_noise), 23);
        assert_eq!(offset_of!(StreamingOptions, content_type), 24);
        assert_eq!(offset_of!(StreamingOptions, content_type_len), 32);
        assert_eq!(offset_of!(StreamingOptions, base_url), 40);
        assert_eq!(offset_of!(StreamingOptions, base_url_len), 48);
        assert_eq!(offset_of!(StreamingOptions, prune_selectors), 56);
        assert_eq!(offset_of!(StreamingOptions, prune_selector_len), 64);
        assert_eq!(offset_of!(StreamingOptions, prune_protection_selectors), 72);
        assert_eq!(
            offset_of!(StreamingOptions, prune_protection_selector_len),
            80
        );
        assert_eq!(offset_of!(StreamingOptions, llm_provider), 88);
        assert_eq!(offset_of!(StreamingOptions, chars_per_token_fixed), 89);
    }

    #[test]
    fn test_streaming_options_size() {
        use std::mem::size_of;

        /* 96 bytes: 4+4+8+4+(1*4)+ptr+usize+ptr+usize+ptr+usize+ptr+usize+1+1+padding */
        assert_eq!(size_of::<StreamingOptions>(), 96);
    }

    #[test]
    fn test_streaming_options_default_zeroed() {
        /* Verify that a zeroed struct produces valid "use defaults" semantics */
        let opts: StreamingOptions = unsafe { std::mem::zeroed() };
        assert_eq!(opts.flavor, 0); /* CommonMark */
        assert_eq!(opts.timeout_ms, 0); /* no timeout */
        assert_eq!(opts.streaming_budget, 0); /* use default */
        assert_eq!(opts.flush_threshold, 0); /* use default */
        assert_eq!(opts.generate_etag, 0); /* disabled */
        assert_eq!(opts.estimate_tokens, 0); /* disabled */
        assert_eq!(opts.front_matter, 0); /* disabled */
        assert_eq!(opts.prune_noise, 0); /* disabled */
        assert!(opts.content_type.is_null());
        assert_eq!(opts.content_type_len, 0);
        assert!(opts.base_url.is_null());
        assert_eq!(opts.base_url_len, 0);
        assert!(opts.prune_selectors.is_null());
        assert_eq!(opts.prune_selector_len, 0);
        assert!(opts.prune_protection_selectors.is_null());
        assert_eq!(opts.prune_protection_selector_len, 0);
        assert_eq!(opts.llm_provider, 0); /* default */
        assert_eq!(opts.chars_per_token_fixed, 0); /* use default */
    }

    #[test]
    fn test_effective_flush_threshold_uses_default_for_zero() {
        assert_eq!(effective_flush_threshold(0), DEFAULT_FLUSH_THRESHOLD);
        assert_eq!(effective_flush_threshold(512), 512);
    }

    // ================================================================
    // Streaming FFI finish function tests
    // (signal EOF to close the stream)
    // ================================================================

    #[test]
    fn test_streaming_finish_lifecycle() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

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
        assert_eq!(rc, ERROR_SUCCESS);

        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }

        /* Call finish to signal EOF and get final flushed output */
        let mut finish_data: *mut u8 = ptr::null_mut();
        let mut finish_len: usize = 0;

        let rc = unsafe { markdown_streaming_finish(handle, &mut finish_data, &mut finish_len) };
        assert_eq!(rc, ERROR_SUCCESS, "finish() should return SUCCESS");

        /* Verify finish produced some output (final flush) */
        if !finish_data.is_null() && finish_len > 0 {
            let md = unsafe { std::slice::from_raw_parts(finish_data, finish_len) };
            let md_str = std::str::from_utf8(md).unwrap();
            assert!(
                md_str.contains("Hello") || md_str.contains("World"),
                "Finish output should contain converted content"
            );
            unsafe { markdown_streaming_output_free(finish_data, finish_len) };
        }

        /* Handle is consumed — no free/abort needed */
    }

    #[test]
    fn test_streaming_finish_null_handle() {
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe { markdown_streaming_finish(ptr::null_mut(), &mut out_data, &mut out_len) };
        assert_eq!(
            rc, ERROR_INVALID_INPUT,
            "NULL handle should return INVALID_INPUT"
        );
    }

    #[test]
    fn test_streaming_finish_null_output_pointers() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let rc = unsafe { markdown_streaming_finish(handle, ptr::null_mut(), ptr::null_mut()) };
        assert_eq!(
            rc, ERROR_INVALID_INPUT,
            "NULL output pointers should return INVALID_INPUT"
        );

        /* Handle was NOT consumed (NULL output check is before Box::from_raw) */
        unsafe { markdown_streaming_abort(handle) };
    }

    #[test]
    fn test_streaming_finish_empty_input() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* Finish immediately without feeding any data */
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe { markdown_streaming_finish(handle, &mut out_data, &mut out_len) };
        assert_eq!(rc, ERROR_SUCCESS, "finish() with no input should succeed");

        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }
        /* Handle is consumed */
    }

    // ================================================================
    // Streaming FFI reason function tests
    // (finalize returns budget consumption stats)
    // ================================================================

    #[test]
    fn test_streaming_reason_null_handle() {
        let reason = unsafe { markdown_streaming_reason(ptr::null()) };
        assert!(reason.is_null(), "NULL handle should return NULL reason");
    }

    #[test]
    fn test_streaming_reason_after_success() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* After creation, no reason is set */
        let reason = unsafe { markdown_streaming_reason(handle) };
        assert!(reason.is_null(), "reason should be NULL before any error");

        let html = b"<p>hello</p>";
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

        /* After successful feed, reason should be NULL */
        let reason = unsafe { markdown_streaming_reason(handle) };
        assert!(reason.is_null(), "reason should be NULL after SUCCESS");

        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }
        unsafe { markdown_streaming_abort(handle) };
    }

    #[test]
    fn test_streaming_reason_after_fallback() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* Feed a table to trigger fallback */
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

        if rc == ERROR_STREAMING_FALLBACK {
            /* Reason should be non-NULL and contain descriptive text */
            let reason = unsafe { markdown_streaming_reason(handle) };
            assert!(
                !reason.is_null(),
                "reason should be non-NULL after fallback"
            );
            let cstr = unsafe { std::ffi::CStr::from_ptr(reason) };
            let reason_str = cstr.to_str().unwrap();
            assert!(!reason_str.is_empty(), "reason string should be non-empty");
            assert!(
                reason_str.contains("table") || reason_str.contains("fallback"),
                "reason should mention the cause: got '{}'",
                reason_str
            );
        }

        unsafe { markdown_streaming_abort(handle) };
    }

    // ================================================================
    // Output buffer ownership contract tests
    // (feed chunk size and cumulative budget enforcement)
    //
    // These tests validate the output buffer ownership, lifetime, and
    // free rules documented in the module-level "Output buffer ownership
    // contract" section.
    // ================================================================

    /// Validates: Requirements 1.5 — output_free correctly releases feed output.
    ///
    /// Verifies that `markdown_streaming_output_free` can safely release
    /// a buffer returned by `markdown_streaming_feed`. The buffer contents
    /// are readable between the feed return and the free call (lifetime rule).
    #[test]
    fn test_output_free_releases_feed_buffer() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* Feed enough content to guarantee non-empty output */
        let html = b"<h1>Ownership</h1><p>Buffer is Rust-owned until freed.</p>";
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

        /* If output was produced, verify it is readable before free */
        if !out_data.is_null() {
            assert!(out_len > 0, "non-NULL output must have non-zero length");

            /* Read the buffer to prove it's valid between feed and free */
            let slice = unsafe { std::slice::from_raw_parts(out_data, out_len) };
            assert!(
                std::str::from_utf8(slice).is_ok(),
                "output should be valid UTF-8"
            );

            /* Free via the documented Rust free function */
            unsafe { markdown_streaming_output_free(out_data, out_len) };
            /* After this point, out_data is dangling — no dereference allowed */
        }

        unsafe { markdown_streaming_abort(handle) };
    }

    /// Validates: Requirements 1.5 — output_free correctly releases finish output.
    ///
    /// Verifies that `markdown_streaming_output_free` works for buffers
    /// returned by `markdown_streaming_finish` (not just `feed`).
    #[test]
    fn test_output_free_releases_finish_buffer() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let html = b"<p>Content for finish test.</p>";
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

        /* Free feed output if any */
        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }

        /* Now finish — this also returns output via the same contract */
        let mut finish_data: *mut u8 = ptr::null_mut();
        let mut finish_len: usize = 0;

        let rc = unsafe { markdown_streaming_finish(handle, &mut finish_data, &mut finish_len) };
        assert_eq!(rc, ERROR_SUCCESS);

        /* Finish output uses the same ownership contract as feed output */
        if !finish_data.is_null() {
            assert!(finish_len > 0);
            let slice = unsafe { std::slice::from_raw_parts(finish_data, finish_len) };
            assert!(std::str::from_utf8(slice).is_ok());

            /* Free via the same output_free function */
            unsafe { markdown_streaming_output_free(finish_data, finish_len) };
        }
        /* Handle consumed by finish — no abort needed */
    }

    /// Validates: Requirements 1.5 — NULL/0 free is a safe no-op.
    ///
    /// The contract states that `(NULL, 0)` is a safe no-op for
    /// error-path cleanup convenience.
    #[test]
    fn test_output_free_null_is_noop() {
        /* Multiple NULL frees must not panic or crash */
        unsafe { markdown_streaming_output_free(ptr::null_mut(), 0) };
        unsafe { markdown_streaming_output_free(ptr::null_mut(), 0) };
        unsafe { markdown_streaming_output_free(ptr::null_mut(), 0) };
    }

    /// Validates: Requirements 1.5 — multiple independent buffers can be freed.
    ///
    /// When multiple `feed` calls each produce output, each buffer is
    /// independently freeable without interfering with others.
    #[test]
    fn test_output_free_multiple_independent_buffers() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let chunks: &[&[u8]] = &[
            b"<h1>First</h1>",
            b"<p>Second paragraph.</p>",
            b"<p>Third paragraph.</p>",
        ];

        let mut buffers: Vec<(*mut u8, usize)> = Vec::new();

        for chunk in chunks {
            let mut out_data: *mut u8 = ptr::null_mut();
            let mut out_len: usize = 0;

            let rc = unsafe {
                markdown_streaming_feed(
                    handle,
                    chunk.as_ptr(),
                    chunk.len(),
                    &mut out_data,
                    &mut out_len,
                )
            };
            assert_eq!(rc, ERROR_SUCCESS);

            if !out_data.is_null() {
                buffers.push((out_data, out_len));
            }
        }

        /* Free all collected buffers — each is independently valid */
        for (data, len) in buffers {
            unsafe { markdown_streaming_output_free(data, len) };
        }

        unsafe { markdown_streaming_abort(handle) };
    }

    /// Validates: Requirements 1.6 — output buffer does not outlive the free call.
    ///
    /// Demonstrates the correct pattern: read buffer contents, then free.
    /// After free, no further access is made (this is a compile-time
    /// discipline enforced by documentation, not by the type system across
    /// FFI boundaries).
    #[test]
    fn test_output_buffer_lifetime_read_then_free() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let html = b"<h1>Lifetime</h1><p>Read then free pattern.</p>";
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
            /* Step 1: Read (simulating memcpy into NGINX pool buffer) */
            let copied: Vec<u8> = unsafe { std::slice::from_raw_parts(out_data, out_len) }.to_vec();

            /* Step 2: Free the Rust buffer immediately after copy */
            unsafe { markdown_streaming_output_free(out_data, out_len) };

            /* Step 3: The copied data remains valid (owned by Vec now) */
            assert!(!copied.is_empty());
            assert!(std::str::from_utf8(&copied).is_ok());
        }

        unsafe { markdown_streaming_abort(handle) };
    }

    // ================================================================
    // Post-commit safe-finish API tests
    // (finalize error handling and safety invariant)
    //
    // These tests validate the post-commit safe-finish/abort API that
    // allows C to handle errors after output has been committed.
    // ================================================================

    /// Validates: Requirements 1.7 — safe finish closes open structures.
    ///
    /// Feeds enough HTML to commit output, then calls safe_finish to
    /// verify that open Markdown structures are gracefully closed and
    /// the return code is POST_COMMIT_SAFE_FINISH (3).
    #[test]
    fn test_safe_finish_closes_open_structures() {
        use crate::ffi::abi::POST_COMMIT_SAFE_FINISH;

        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* Feed content that produces output (transitions to PostCommit) */
        let html = b"<h1>Title</h1><p>Paragraph content here.</p>";
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

        /* Call safe_finish — should succeed even when called on a healthy converter */
        let mut finish_data: *mut u8 = ptr::null_mut();
        let mut finish_len: usize = 0;

        let rc =
            unsafe { markdown_streaming_safe_finish(handle, &mut finish_data, &mut finish_len) };
        assert_eq!(
            rc, POST_COMMIT_SAFE_FINISH,
            "safe_finish should return POST_COMMIT_SAFE_FINISH (3)"
        );

        /* Output may be empty or contain closing bytes — either is valid */
        if !finish_data.is_null() && finish_len > 0 {
            let md = unsafe { std::slice::from_raw_parts(finish_data, finish_len) };
            /* Closing bytes must be valid UTF-8 */
            assert!(
                std::str::from_utf8(md).is_ok(),
                "safe_finish output must be valid UTF-8"
            );
            unsafe { markdown_streaming_output_free(finish_data, finish_len) };
        }

        /* Handle is consumed — no free/abort needed */
    }

    /// Validates: Requirements 1.8 — NULL handle returns INVALID_INPUT.
    #[test]
    fn test_safe_finish_null_handle() {
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc =
            unsafe { markdown_streaming_safe_finish(ptr::null_mut(), &mut out_data, &mut out_len) };
        assert_eq!(
            rc, ERROR_INVALID_INPUT,
            "NULL handle should return INVALID_INPUT"
        );
    }

    /// Validates: Requirements 1.8 — NULL output pointers return INVALID_INPUT.
    #[test]
    fn test_safe_finish_null_output_pointers() {
        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        let rc =
            unsafe { markdown_streaming_safe_finish(handle, ptr::null_mut(), ptr::null_mut()) };
        assert_eq!(
            rc, ERROR_INVALID_INPUT,
            "NULL output pointers should return INVALID_INPUT"
        );

        /* Handle was NOT consumed (NULL check is before Box::from_raw) */
        unsafe { markdown_streaming_abort(handle) };
    }

    /// Validates: Requirements 1.7 — safe finish on empty converter succeeds.
    #[test]
    fn test_safe_finish_empty_converter() {
        use crate::ffi::abi::POST_COMMIT_SAFE_FINISH;

        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* Call safe_finish immediately without feeding any data */
        let mut out_data: *mut u8 = ptr::null_mut();
        let mut out_len: usize = 0;

        let rc = unsafe { markdown_streaming_safe_finish(handle, &mut out_data, &mut out_len) };
        assert_eq!(
            rc, POST_COMMIT_SAFE_FINISH,
            "safe_finish on empty converter should succeed"
        );

        if !out_data.is_null() {
            unsafe { markdown_streaming_output_free(out_data, out_len) };
        }
        /* Handle consumed */
    }

    /// Validates: Requirements 1.7 — safe finish with open code block.
    ///
    /// Feeds partial code block HTML (opening but not closing), then
    /// calls safe_finish to verify the code fence is properly closed.
    #[test]
    fn test_safe_finish_with_open_code_block() {
        use crate::ffi::abi::POST_COMMIT_SAFE_FINISH;

        let opts = test_options();
        let handle = unsafe { markdown_streaming_new(&opts) };
        assert!(!handle.is_null());

        /* Feed a code block opening without the closing tag */
        let html = b"<pre><code>fn main() {\n    println!(\"hello\");\n";
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

        /* Call safe_finish — should close the code fence */
        let mut finish_data: *mut u8 = ptr::null_mut();
        let mut finish_len: usize = 0;

        let rc =
            unsafe { markdown_streaming_safe_finish(handle, &mut finish_data, &mut finish_len) };
        assert_eq!(
            rc, POST_COMMIT_SAFE_FINISH,
            "safe_finish with open code block should succeed"
        );

        /* Output should contain closing fence (```) */
        if !finish_data.is_null() && finish_len > 0 {
            let md = unsafe { std::slice::from_raw_parts(finish_data, finish_len) };
            let md_str = std::str::from_utf8(md).unwrap();
            assert!(
                md_str.contains("```"),
                "safe_finish should produce closing code fence, got: '{}'",
                md_str
            );
            unsafe { markdown_streaming_output_free(finish_data, finish_len) };
        }
    }

    /// Validates: Requirements 1.7, 1.8 — return code constants are correct.
    #[test]
    fn test_safe_finish_return_code_constants() {
        use crate::ffi::abi::{POST_COMMIT_ABORT, POST_COMMIT_SAFE_FINISH};

        assert_eq!(POST_COMMIT_SAFE_FINISH, 3);
        assert_eq!(POST_COMMIT_ABORT, 4);
    }
}
