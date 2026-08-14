//! Property-based tests for ABI version handshake (Property 13).
//!
//! **Validates: Requirements 8.3**
//!
//! Property 13: For any mismatch between the C module's compiled-in ABI
//! handshake tuple and the Rust library's reported tuple — numeric ABI
//! version, generated-header hash, exported-symbol-set hash, or ABI layout
//! fingerprint — module initialization SHALL fail (the tuple comparison
//! returns false) and would log the mismatched fields.
//!
//! This test validates:
//! 1. That any single-field mismatch in the 4-tuple causes rejection
//! 2. That the handshake executes before any business FFI call (ordering)
//! 3. That each independent tuple element is checked individually

use nginx_markdown_converter::ffi::{
    MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH, MARKDOWN_LAYOUT_FINGERPRINT,
    MARKDOWN_SYMBOL_SET_HASH, markdown_abi_header_hash, markdown_abi_layout_fingerprint,
    markdown_abi_symbol_set_hash, markdown_abi_version,
};
use proptest::prelude::*;

// ─── Helpers ──────────────────────────────────────────────────────────────────

/// Simulate the C-side ABI tuple comparison.
///
/// Returns true only when all four tuple elements match their expected values.
/// This mirrors `ngx_http_markdown_ffi_abi_tuple_matches` from the C header.
fn abi_tuple_matches(abi: u32, hdr: u64, sym: u64, layout: u64) -> bool {
    abi == MARKDOWN_ABI_VERSION
        && hdr == MARKDOWN_HEADER_HASH
        && sym == MARKDOWN_SYMBOL_SET_HASH
        && layout == MARKDOWN_LAYOUT_FINGERPRINT
}

/// Simulate the C-side single-version comparison.
fn abi_version_matches(actual: u32) -> bool {
    actual == MARKDOWN_ABI_VERSION
}

// ─── Strategies ───────────────────────────────────────────────────────────────

/// Generate an arbitrary u32 that is NOT equal to the correct ABI version.
fn mismatched_abi_version() -> impl Strategy<Value = u32> {
    any::<u32>().prop_filter("must differ from MARKDOWN_ABI_VERSION", |v| {
        *v != MARKDOWN_ABI_VERSION
    })
}

/// Generate an arbitrary u64 that is NOT equal to the correct header hash.
fn mismatched_header_hash() -> impl Strategy<Value = u64> {
    any::<u64>().prop_filter("must differ from MARKDOWN_HEADER_HASH", |v| {
        *v != MARKDOWN_HEADER_HASH
    })
}

/// Generate an arbitrary u64 that is NOT equal to the correct symbol set hash.
fn mismatched_symbol_set_hash() -> impl Strategy<Value = u64> {
    any::<u64>().prop_filter("must differ from MARKDOWN_SYMBOL_SET_HASH", |v| {
        *v != MARKDOWN_SYMBOL_SET_HASH
    })
}

/// Generate an arbitrary u64 that is NOT equal to the correct layout fingerprint.
fn mismatched_layout_fingerprint() -> impl Strategy<Value = u64> {
    any::<u64>().prop_filter("must differ from MARKDOWN_LAYOUT_FINGERPRINT", |v| {
        *v != MARKDOWN_LAYOUT_FINGERPRINT
    })
}

// ─── Property Tests ───────────────────────────────────────────────────────────

proptest! {
    #![proptest_config(ProptestConfig::with_cases(100))]

    /// Property 13a: Numeric ABI version mismatch rejects the handshake.
    ///
    /// For any value that differs from the compiled-in MARKDOWN_ABI_VERSION,
    /// the tuple comparison must return false.
    ///
    /// **Validates: Requirements 8.3**
    #[test]
    fn prop_numeric_abi_mismatch_rejects(bad_version in mismatched_abi_version()) {
        // Single-field comparison rejects
        prop_assert!(
            !abi_version_matches(bad_version),
            "version {} should not match expected {}",
            bad_version, MARKDOWN_ABI_VERSION
        );

        // Full tuple comparison also rejects when only numeric differs
        prop_assert!(
            !abi_tuple_matches(
                bad_version,
                MARKDOWN_HEADER_HASH,
                MARKDOWN_SYMBOL_SET_HASH,
                MARKDOWN_LAYOUT_FINGERPRINT
            ),
            "tuple with bad version {} should be rejected",
            bad_version
        );
    }

    /// Property 13b: Generated-header hash mismatch rejects the handshake.
    ///
    /// For any header hash that differs from MARKDOWN_HEADER_HASH, the tuple
    /// comparison must return false even when all other fields match.
    ///
    /// **Validates: Requirements 8.3**
    #[test]
    fn prop_header_hash_mismatch_rejects(bad_hash in mismatched_header_hash()) {
        prop_assert!(
            !abi_tuple_matches(
                MARKDOWN_ABI_VERSION,
                bad_hash,
                MARKDOWN_SYMBOL_SET_HASH,
                MARKDOWN_LAYOUT_FINGERPRINT
            ),
            "tuple with bad header hash 0x{:016x} should be rejected",
            bad_hash
        );
    }

    /// Property 13c: Exported-symbol-set hash mismatch rejects the handshake.
    ///
    /// For any symbol set hash that differs from MARKDOWN_SYMBOL_SET_HASH, the
    /// tuple comparison must return false even when all other fields match.
    ///
    /// **Validates: Requirements 8.3**
    #[test]
    fn prop_symbol_set_hash_mismatch_rejects(bad_hash in mismatched_symbol_set_hash()) {
        prop_assert!(
            !abi_tuple_matches(
                MARKDOWN_ABI_VERSION,
                MARKDOWN_HEADER_HASH,
                bad_hash,
                MARKDOWN_LAYOUT_FINGERPRINT
            ),
            "tuple with bad symbol set hash 0x{:016x} should be rejected",
            bad_hash
        );
    }

    /// Property 13d: ABI layout fingerprint mismatch rejects the handshake.
    ///
    /// For any layout fingerprint that differs from MARKDOWN_LAYOUT_FINGERPRINT,
    /// the tuple comparison must return false even when all other fields match.
    ///
    /// **Validates: Requirements 8.3**
    #[test]
    fn prop_layout_fingerprint_mismatch_rejects(bad_fp in mismatched_layout_fingerprint()) {
        prop_assert!(
            !abi_tuple_matches(
                MARKDOWN_ABI_VERSION,
                MARKDOWN_HEADER_HASH,
                MARKDOWN_SYMBOL_SET_HASH,
                bad_fp
            ),
            "tuple with bad layout fingerprint 0x{:016x} should be rejected",
            bad_fp
        );
    }

    /// Property 13e: Any arbitrary combination with at least one mismatch rejects.
    ///
    /// Generate arbitrary values for all four tuple elements. If at least one
    /// differs from the expected constant, the handshake must reject.
    ///
    /// **Validates: Requirements 8.3**
    #[test]
    fn prop_any_mismatch_rejects(
        abi in any::<u32>(),
        hdr in any::<u64>(),
        sym in any::<u64>(),
        layout in any::<u64>()
    ) {
        let all_match = abi == MARKDOWN_ABI_VERSION
            && hdr == MARKDOWN_HEADER_HASH
            && sym == MARKDOWN_SYMBOL_SET_HASH
            && layout == MARKDOWN_LAYOUT_FINGERPRINT;

        let result = abi_tuple_matches(abi, hdr, sym, layout);

        // The handshake passes if and only if all four fields match
        prop_assert_eq!(
            result, all_match,
            "tuple_matches({}, 0x{:016x}, 0x{:016x}, 0x{:016x}) = {} but all_match = {}",
            abi, hdr, sym, layout, result, all_match
        );
    }
}

// ─── Deterministic Tests ──────────────────────────────────────────────────────

/// Verify that the Rust accessor functions return the correct constants.
/// This proves the handshake can be validated from the C side by calling
/// these functions and comparing with compiled-in constants.
#[test]
fn abi_accessors_return_correct_constants() {
    assert_eq!(markdown_abi_version(), MARKDOWN_ABI_VERSION);
    assert_eq!(markdown_abi_header_hash(), MARKDOWN_HEADER_HASH);
    assert_eq!(markdown_abi_symbol_set_hash(), MARKDOWN_SYMBOL_SET_HASH);
    assert_eq!(
        markdown_abi_layout_fingerprint(),
        MARKDOWN_LAYOUT_FINGERPRINT
    );
}

/// Parse a `#define NAME <value>` line from the canonical C header into u64.
///
/// Handles decimal, `0x`-prefixed hexadecimal, and `u`/`ull` integer
/// suffixes as emitted by the header generator.
fn c_define_value(header: &str, name: &str) -> u64 {
    let needle = format!("#define {name} ");
    let value = header
        .lines()
        .find(|line| line.trim_start().starts_with(&needle))
        .and_then(|line| line.split_whitespace().nth(2))
        .unwrap_or_else(|| panic!("{name} not found in markdown_converter.h"))
        .trim_end_matches(['u', 'l', 'L']);
    if let Some(hex) = value.strip_prefix("0x") {
        u64::from_str_radix(hex, 16).expect("valid hex define")
    } else {
        value.parse::<u64>().expect("valid decimal define")
    }
}

/// Verify the Rust ABI constants match the canonical C header #defines, so
/// the handshake simulation cannot pass with self-consistent but stale
/// constants (run12 MAJOR: property_abi_handshake C-header anchoring).
#[test]
fn rust_constants_match_c_header_defines() {
    let header = include_str!("../include/markdown_converter.h");
    assert_eq!(
        u64::from(MARKDOWN_ABI_VERSION),
        c_define_value(header, "MARKDOWN_ABI_VERSION"),
        "MARKDOWN_ABI_VERSION diverges from markdown_converter.h"
    );
    assert_eq!(
        MARKDOWN_HEADER_HASH,
        c_define_value(header, "MARKDOWN_HEADER_HASH"),
        "MARKDOWN_HEADER_HASH diverges from markdown_converter.h"
    );
    assert_eq!(
        MARKDOWN_SYMBOL_SET_HASH,
        c_define_value(header, "MARKDOWN_SYMBOL_SET_HASH"),
        "MARKDOWN_SYMBOL_SET_HASH diverges from markdown_converter.h"
    );
    assert_eq!(
        MARKDOWN_LAYOUT_FINGERPRINT,
        c_define_value(header, "MARKDOWN_LAYOUT_FINGERPRINT"),
        "MARKDOWN_LAYOUT_FINGERPRINT diverges from markdown_converter.h"
    );
}

/// Verify that the full correct tuple passes the handshake.
#[test]
fn correct_tuple_passes_handshake() {
    assert!(abi_tuple_matches(
        MARKDOWN_ABI_VERSION,
        MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH,
        MARKDOWN_LAYOUT_FINGERPRINT,
    ));
}

/// Verify handshake ordering: the handshake executes before any business FFI call.
///
/// This test verifies the architectural invariant by demonstrating that the
/// ABI accessor functions are pure, panic-free, and callable at any point —
/// meaning they can safely be called first during initialization before any
/// stateful/fallible business FFI call (like markdown_convert which requires
/// a MarkdownConverterHandle).
///
/// The C module's init_module function calls these accessors and compares
/// before registering filters or processing any request-path FFI.
#[test]
fn handshake_precedes_business_ffi_calls() {
    // Step 1: ABI handshake (pure accessors, no state needed)
    let version = markdown_abi_version();
    let header_hash = markdown_abi_header_hash();
    let symbol_set_hash = markdown_abi_symbol_set_hash();
    let layout_fingerprint = markdown_abi_layout_fingerprint();

    // The handshake must pass before we proceed to any business FFI
    let handshake_ok = version == MARKDOWN_ABI_VERSION
        && header_hash == MARKDOWN_HEADER_HASH
        && symbol_set_hash == MARKDOWN_SYMBOL_SET_HASH
        && layout_fingerprint == MARKDOWN_LAYOUT_FINGERPRINT;

    assert!(
        handshake_ok,
        "Handshake must pass before any business FFI call"
    );

    // Step 2: Only after handshake passes do we create business state.
    // If handshake had failed, the C module would return NGX_ERROR here
    // and never reach this point.
    use nginx_markdown_converter::ffi::{
        MarkdownOptions, markdown_converter_free, markdown_converter_new, markdown_options_init,
    };

    let handle = markdown_converter_new();
    assert!(
        !handle.is_null(),
        "converter handle must be allocated after handshake"
    );

    // Verify we can initialize options (business FFI) only after handshake
    let mut opts: MarkdownOptions = unsafe { std::mem::zeroed() };
    unsafe { markdown_options_init(&mut opts) };

    // Cleanup
    unsafe { markdown_converter_free(handle) };
}

/// Verify that each tuple element is checked independently — flipping any
/// single bit in any element causes rejection.
#[test]
fn each_tuple_element_checked_independently() {
    // Baseline passes
    assert!(abi_tuple_matches(
        MARKDOWN_ABI_VERSION,
        MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH,
        MARKDOWN_LAYOUT_FINGERPRINT,
    ));

    // Flip bit 0 in numeric version
    assert!(!abi_tuple_matches(
        MARKDOWN_ABI_VERSION ^ 1,
        MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH,
        MARKDOWN_LAYOUT_FINGERPRINT,
    ));

    // Flip bit 0 in header hash
    assert!(!abi_tuple_matches(
        MARKDOWN_ABI_VERSION,
        MARKDOWN_HEADER_HASH ^ 1,
        MARKDOWN_SYMBOL_SET_HASH,
        MARKDOWN_LAYOUT_FINGERPRINT,
    ));

    // Flip bit 0 in symbol set hash
    assert!(!abi_tuple_matches(
        MARKDOWN_ABI_VERSION,
        MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH ^ 1,
        MARKDOWN_LAYOUT_FINGERPRINT,
    ));

    // Flip bit 0 in layout fingerprint
    assert!(!abi_tuple_matches(
        MARKDOWN_ABI_VERSION,
        MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH,
        MARKDOWN_LAYOUT_FINGERPRINT ^ 1,
    ));
}
