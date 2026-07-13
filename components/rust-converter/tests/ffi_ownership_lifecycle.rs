//! Property test for FFI ownership lifecycle round-trip.
//!
//! **Validates: Requirements 2.1, 2.4, 2.5**
//!
//! Verifies that for any valid buffer size N > 0, the sequence:
//!   alloc (Box<[u8]>) → transfer (as_mut_ptr + mem::forget) →
//!   markdown_streaming_output_free(ptr, len)
//! deallocates exactly once without error, and that the pool cleanup
//! handler becomes a no-op (via `freed` flag) when explicit free was
//! already called.

#![cfg(feature = "streaming")]

use nginx_markdown_converter::ffi::markdown_streaming_output_free;
use proptest::prelude::*;
use std::mem;
use std::sync::atomic::{AtomicBool, Ordering};

// ============================================================================
// Property 1: FFI Ownership Lifecycle Round-Trip
// ============================================================================

/// Simulates the pool cleanup handler's `freed` flag behavior.
///
/// In the C module, the cleanup structure contains a `freed:1` bitfield.
/// When explicit free is called first, the cleanup handler checks this flag
/// and becomes a no-op. This struct models that logic for testing.
struct PoolCleanupSimulator {
    rust_ptr: *mut u8,
    rust_len: usize,
    freed: AtomicBool,
}

impl PoolCleanupSimulator {
    /// Create a new cleanup simulator tracking the given Rust-owned buffer.
    fn new(ptr: *mut u8, len: usize) -> Self {
        Self {
            rust_ptr: ptr,
            rust_len: len,
            freed: AtomicBool::new(false),
        }
    }

    /// Simulate explicit free (e.g., on NGX_OK delivery or error path).
    /// Sets the `freed` flag and calls the Rust free function.
    fn explicit_free(&self) {
        if !self.freed.swap(true, Ordering::SeqCst) {
            /* First free — call the real FFI free function */
            unsafe { markdown_streaming_output_free(self.rust_ptr, self.rust_len) };
        }
    }

    /// Simulate pool cleanup handler invocation.
    /// If `freed` is already set, this is a no-op (double-free prevention).
    fn pool_cleanup(&self) {
        if !self.freed.swap(true, Ordering::SeqCst) {
            /* Pool cleanup is the first to free — call the real FFI free */
            unsafe { markdown_streaming_output_free(self.rust_ptr, self.rust_len) };
        }
        /* Otherwise: no-op, buffer was already freed explicitly */
    }

    /// Returns whether the buffer has been freed.
    fn is_freed(&self) -> bool {
        self.freed.load(Ordering::SeqCst)
    }
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(256))]

    /// **Property 1: FFI Ownership Lifecycle Round-Trip**
    ///
    /// For any valid buffer size N > 0, allocating a Box<[u8]> of size N,
    /// transferring ownership via as_mut_ptr() + mem::forget(), then calling
    /// markdown_streaming_output_free(ptr, N) deallocates exactly once
    /// without error.
    ///
    /// **Validates: Requirements 2.1, 2.4, 2.5**
    #[test]
    fn ffi_ownership_round_trip(size in 1usize..=1_048_576) {
        /* Step 1: Allocate a Rust-owned buffer */
        let mut buf: Box<[u8]> = vec![0xABu8; size].into_boxed_slice();
        let ptr = buf.as_mut_ptr();
        let len = buf.len();
        prop_assert_eq!(len, size);
        prop_assert!(!ptr.is_null());

        /* Step 2: Transfer ownership (simulates Rust producing output) */
        mem::forget(buf);

        /* Step 3: Free via the FFI function (simulates C calling free) */
        unsafe { markdown_streaming_output_free(ptr, len); }

        /* No crash, no double-free, no leak = success */
    }

    /// **Property 1 (variant): Pool cleanup becomes no-op after explicit free**
    ///
    /// For any valid buffer size, if explicit free is called first (setting
    /// the `freed` flag), the subsequent pool cleanup handler invocation
    /// must be a no-op (no double-free).
    ///
    /// **Validates: Requirements 2.4, 2.5**
    #[test]
    fn pool_cleanup_noop_after_explicit_free(size in 1usize..=1_048_576) {
        /* Allocate and transfer ownership */
        let mut buf: Box<[u8]> = vec![0xCDu8; size].into_boxed_slice();
        let ptr = buf.as_mut_ptr();
        let len = buf.len();
        mem::forget(buf);

        /* Create cleanup simulator (models ngx_http_markdown_rust_buf_cleanup_t) */
        let cleanup = PoolCleanupSimulator::new(ptr, len);

        /* Explicit free called first (e.g., on successful delivery) */
        cleanup.explicit_free();
        prop_assert!(cleanup.is_freed(), "freed flag must be set after explicit free");

        /* Pool cleanup fires later during pool destroy — must be no-op */
        cleanup.pool_cleanup();
        /* No crash = double-free prevented by freed flag */
    }

    /// **Property 1 (variant): Pool cleanup as sole free path**
    ///
    /// For any valid buffer size, if explicit free was NOT called, pool
    /// cleanup must successfully free the buffer (safety net path).
    ///
    /// **Validates: Requirements 2.4, 2.5**
    #[test]
    fn pool_cleanup_frees_when_no_explicit_free(size in 1usize..=1_048_576) {
        /* Allocate and transfer ownership */
        let mut buf: Box<[u8]> = vec![0xEFu8; size].into_boxed_slice();
        let ptr = buf.as_mut_ptr();
        let len = buf.len();
        mem::forget(buf);

        /* Create cleanup simulator */
        let cleanup = PoolCleanupSimulator::new(ptr, len);

        /* No explicit free — pool cleanup is the sole free path */
        prop_assert!(!cleanup.is_freed(), "freed flag should be false initially");
        cleanup.pool_cleanup();
        prop_assert!(cleanup.is_freed(), "freed flag must be set after pool cleanup");

        /* No crash, no leak = success */
    }
}

// ============================================================================
// Deterministic edge-case tests (complement the property tests)
// ============================================================================

/// Verify specific buffer sizes that exercise allocation boundaries.
#[test]
fn ffi_ownership_deterministic_sizes() {
    let sizes: &[usize] = &[
        1, 2, 7, 8, 15, 16, 63, 64, 127, 128, 255, 256, 1023, 1024, 4096, 65535, 65536, 1_048_576,
    ];

    for &size in sizes {
        let mut buf: Box<[u8]> = vec![0x42u8; size].into_boxed_slice();
        let ptr = buf.as_mut_ptr();
        let len = buf.len();
        assert_eq!(len, size);
        assert!(!ptr.is_null());
        mem::forget(buf);
        unsafe {
            markdown_streaming_output_free(ptr, len);
        }
    }
}

/// Verify that NULL pointer free is a safe no-op (Requirement 2.2).
#[test]
fn ffi_null_pointer_free_is_noop() {
    unsafe {
        markdown_streaming_output_free(std::ptr::null_mut(), 0);
        markdown_streaming_output_free(std::ptr::null_mut(), 1024);
        markdown_streaming_output_free(std::ptr::null_mut(), usize::MAX);
    }
    /* No crash = success */
}

/// Verify that multiple sequential alloc/free cycles don't leak.
#[test]
fn ffi_ownership_sequential_cycles() {
    for _ in 0..1000 {
        let mut buf: Box<[u8]> = vec![0u8; 4096].into_boxed_slice();
        let ptr = buf.as_mut_ptr();
        let len = buf.len();
        assert_eq!(len, 4096);
        assert!(!ptr.is_null());
        mem::forget(buf);
        unsafe {
            markdown_streaming_output_free(ptr, len);
        }
    }
}
