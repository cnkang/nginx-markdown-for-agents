//! Diagnostics schema FFI exports.
//!
//! Provides C-callable functions to obtain the diagnostics JSON schema v1
//! string. The Rust side owns the allocated memory; callers must release it
//! via [`markdown_free_diagnostics`].
//!
//! # Panic Safety
//!
//! Both exported functions wrap their fallible core in
//! [`std::panic::catch_unwind`] so a Rust panic can never unwind across the
//! FFI boundary (AGENTS.md Rule 15).

use std::panic;
use std::ptr;

use crate::diagnostics::DiagnosticsSchema;

/// Get a default diagnostics JSON schema v1 string.
///
/// Returns a pointer to a heap-allocated JSON byte buffer and writes the
/// byte length (excluding any NUL terminator) to `out_len`. The returned
/// buffer is NUL-terminated for convenience but `out_len` does NOT include
/// the terminator.
///
/// On failure (NULL `out_len`, serialization error, or caught panic) returns
/// NULL and sets `*out_len = 0`.
///
/// The caller must free the returned buffer by calling
/// [`markdown_free_diagnostics`] with the same pointer and length.
///
/// # Safety
///
/// The caller must ensure that `out_len` either is NULL (in which case the
/// function returns NULL immediately) or points to writable storage for a
/// `usize`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_get_diagnostics_schema(out_len: *mut usize) -> *mut u8 {
    if out_len.is_null() {
        return ptr::null_mut();
    }

    let result = panic::catch_unwind(|| {
        let schema = DiagnosticsSchema::default();
        schema.to_json()
    });

    match result {
        Ok(Ok(json_string)) => {
            let bytes = json_string.into_bytes();
            let len = bytes.len();

            // Allocate with trailing NUL for C convenience.
            let mut buf = bytes.into_boxed_slice().into_vec();
            buf.push(0); // NUL terminator

            let ptr = buf.as_mut_ptr();
            std::mem::forget(buf);

            // SAFETY: `out_len` was validated as non-NULL above.
            unsafe { *out_len = len };
            ptr
        }
        Ok(Err(_)) | Err(_) => {
            // SAFETY: `out_len` was validated as non-NULL above.
            unsafe { *out_len = 0 };
            ptr::null_mut()
        }
    }
}

/// Free a diagnostics JSON string previously returned by
/// [`markdown_get_diagnostics_schema`].
///
/// # Safety
///
/// The caller must ensure that:
/// - `ptr` was returned by `markdown_get_diagnostics_schema` and has not
///   already been freed
/// - `len` is the value written to `out_len` by that call
///
/// After this call, `ptr` is invalid and must not be dereferenced.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn markdown_free_diagnostics(ptr: *mut u8, len: usize) {
    if ptr.is_null() {
        return;
    }

    // Reconstruct the Vec that was forgotten in markdown_get_diagnostics_schema.
    // The original Vec had `len` content bytes + 1 NUL byte = capacity len+1.
    let _ = panic::catch_unwind(panic::AssertUnwindSafe(|| {
        // SAFETY: The pointer and capacity originate from a Vec that was
        // forgotten in `markdown_get_diagnostics_schema`. The Vec had
        // capacity = len + 1 (content bytes + NUL terminator).
        let _ = unsafe { Vec::from_raw_parts(ptr, len + 1, len + 1) };
    }));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_get_diagnostics_schema_returns_valid_json() {
        let mut len: usize = 0;
        let ptr = unsafe { markdown_get_diagnostics_schema(&mut len) };
        assert!(!ptr.is_null(), "should return non-NULL pointer");
        assert!(len > 0, "length should be > 0");

        // Verify it's valid UTF-8 JSON
        let slice = unsafe { std::slice::from_raw_parts(ptr, len) };
        let json_str = std::str::from_utf8(slice).expect("should be valid UTF-8");
        let parsed: serde_json::Value =
            serde_json::from_str(json_str).expect("should be valid JSON");
        assert_eq!(parsed["schema_version"], 1);
        assert!(parsed["decision"].is_object());
        assert!(parsed["inflight"].is_object());
        assert!(parsed["error"].is_object());
        assert!(parsed["streaming"].is_object());
        assert!(parsed["conditional"].is_object());
        assert!(parsed["etag"].is_object());

        // Clean up
        unsafe { markdown_free_diagnostics(ptr, len) };
    }

    #[test]
    fn test_get_diagnostics_schema_null_out_len() {
        let ptr = unsafe { markdown_get_diagnostics_schema(ptr::null_mut()) };
        assert!(ptr.is_null(), "should return NULL when out_len is NULL");
    }

    #[test]
    fn test_free_diagnostics_null_ptr() {
        // Should not crash
        unsafe { markdown_free_diagnostics(ptr::null_mut(), 0) };
    }

    #[test]
    fn test_get_diagnostics_schema_nul_terminated() {
        let mut len: usize = 0;
        let ptr = unsafe { markdown_get_diagnostics_schema(&mut len) };
        assert!(!ptr.is_null());

        // Check that the byte after the content is NUL
        let nul_byte = unsafe { *ptr.add(len) };
        assert_eq!(nul_byte, 0, "buffer should be NUL-terminated");

        unsafe { markdown_free_diagnostics(ptr, len) };
    }
}
