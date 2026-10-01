#!/usr/bin/env python3
"""Reason code generator — reads reason_registry.toml and produces artifacts.

This standalone tool is the single code-generation entry point for all
reason-code-derived artifacts. It reads the declarative registry and writes:
  - Rust enum + metadata (reason_code.rs)
  - Generated C header (markdown_reason_meta.h)
  - Count/hash manifest JSON (reason-registry-report.json)
  - Generated-artifacts listing (generated-reason-artifacts.json)

Usage:
  python3 tools/reason-codegen/generate.py [--check]

Flags:
  --check   Compare generated output with checked-in files; exit 1 on drift.
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = REPO_ROOT / "components" / "rust-converter" / "reason_registry.toml"

if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
from lib.executable_validation import resolve_approved_executable  # noqa: E402

# Output paths
RUST_OUTPUT = (
    REPO_ROOT / "components" / "rust-converter" / "src" / "decision" / "reason_code.rs"
)
MANIFEST_OUTPUT = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2" / "reason-registry-report.json"
)
LISTING_OUTPUT = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2" / "generated-reason-artifacts.json"
)
C_HEADER_OUTPUT = (
    REPO_ROOT / "components" / "nginx-module" / "src" / "markdown_reason_meta.h"
)

RUST_DOC_LINE = "    ///"
RUST_DOC_EXAMPLES = "    /// # Examples"
RUST_DOC_CODE_FENCE = "    /// ```"
RUST_DOC_REASON_USE = (
    "    /// use nginx_markdown_converter::decision::reason_code::ReasonCode;"
)
RUST_MATCH_SELF = "        match self {"
RUST_NO_MANGLE = "#[unsafe(no_mangle)]"
RUST_REASON_AS_STR = "            let s = rc.as_str();"
RUST_OUT_LEN_GUARD = "            if !out_len.is_null() {"
RUST_TEST_ATTRIBUTE = "    #[test]"
RUST_ALL_LOOP = "        for rc in &ALL {"
REASON_KEY_RE = re.compile(r"^[a-z](?:[a-z0-9]|_(?=[a-z0-9]))*$")
VALID_STAGES = frozenset({
    "eligibility", "decompression", "parsing", "conversion",
    "precommit", "postcommit", "delivery",
})
VALID_ERROR_ORIGINS = frozenset({
    "allocation", "downstream", "invariant", "format", "truncated",
    "timeout", "memory_budget", "internal", "none",
})

VALID_OUTCOMES = frozenset({
    "converted", "skipped", "failed_open", "failed_closed", "aborted",
})
REASON_REQUIRED_FIELDS = frozenset({
    "discriminant", "key", "default_stage", "allowed_origins",
    "operator_visible", "outcome", "default_origin",
})


def _validate_discriminant(
    index: int,
    discriminant: object,
    key: object,
    seen: dict[int, str],
) -> list[str]:
    """Validate and record one reason discriminant."""
    if (
        isinstance(discriminant, bool)
        or not isinstance(discriminant, int)
        or not 0 <= discriminant <= 255
    ):
        return [f"reasons[{index}] discriminant must be an integer in 0..255"]
    if discriminant in seen:
        return [
            f"duplicate discriminant {discriminant}: "
            f"{key!r} conflicts with {seen[discriminant]!r}"
        ]
    seen[discriminant] = str(key)
    return []


def _validate_reason_key(
    index: int, key: object, discriminant: object, seen: dict[str, object]
) -> list[str]:
    """Validate and record one reason key."""
    if not isinstance(key, str) or REASON_KEY_RE.fullmatch(key) is None:
        return [f"reasons[{index}] key must match lowercase snake_case"]
    if key in seen:
        return [
            f"duplicate reason key {key!r}: discriminants "
            f"{seen[key]} and {discriminant}"
        ]
    seen[key] = discriminant
    return []


def _validate_allowed_origins(index: int, origins: object) -> list[str]:
    """Validate the allowed_origins array for one reason entry."""
    if not isinstance(origins, list):
        return [f"reasons[{index}] allowed_origins must be an array"]
    invalid_origins = [
        origin
        for origin in origins
        if not isinstance(origin, str) or origin not in VALID_ERROR_ORIGINS
    ]
    if invalid_origins:
        return [
            f"reasons[{index}] invalid allowed_origins: {invalid_origins!r}"
        ]
    return []


def _validate_default_origin(
    index: int, default_origin: object, origins: list
) -> list[str]:
    """Validate default_origin is a known origin and is reachable."""
    if not isinstance(default_origin, str) or default_origin not in VALID_ERROR_ORIGINS:
        return [f"reasons[{index}] default_origin {default_origin!r} is invalid"]
    if default_origin != "none" and default_origin not in origins:
        return [
            f"reasons[{index}] default_origin {default_origin!r} "
            f"must be in allowed_origins or 'none'"
        ]
    return []


def _validate_reason_metadata(index: int, entry: dict) -> list[str]:
    """Validate stage, origins, and visibility metadata for one reason."""
    errors: list[str] = []
    stage = entry["default_stage"]
    if not isinstance(stage, str) or stage not in VALID_STAGES:
        errors.append(f"reasons[{index}] default_stage {stage!r} is invalid")

    origins = entry["allowed_origins"]
    if not isinstance(origins, list):
        errors.append(f"reasons[{index}] allowed_origins must be an array")
        # A malformed non-list value must not reach membership checks or
        # default-origin validation (which would iterate or probe it).
        # Record the error above and continue with a safe empty-origin
        # collection so the remaining metadata is still validated and the
        # ERROR output path is preserved.
        origins = []

    errors.extend(_validate_allowed_origins(index, origins))

    if not isinstance(entry["operator_visible"], bool):
        errors.append(f"reasons[{index}] operator_visible must be boolean")

    outcome = entry.get("outcome")
    if not isinstance(outcome, str) or outcome not in VALID_OUTCOMES:
        errors.append(f"reasons[{index}] outcome {outcome!r} is invalid")

    errors.extend(_validate_default_origin(index, entry.get("default_origin"), origins))
    return errors


def _validate_reason_entry(
    index: int,
    entry: object,
    seen_discriminants: dict[int, str],
    seen_keys: dict[str, object],
) -> list[str]:
    """Validate one registry entry and update duplicate trackers."""
    if not isinstance(entry, dict):
        return [f"reasons[{index}] must be a table"]
    missing = REASON_REQUIRED_FIELDS - set(entry)
    if missing:
        return [f"reasons[{index}] missing fields: {sorted(missing)}"]

    discriminant = entry["discriminant"]
    key = entry["key"]
    errors = _validate_discriminant(
        index, discriminant, key, seen_discriminants
    )
    errors.extend(_validate_reason_key(index, key, discriminant, seen_keys))
    errors.extend(_validate_reason_metadata(index, entry))
    return errors


def _validate_reasons(reasons: object) -> list[str]:
    """Validate registry entries before sorting or generating artifacts."""
    if not isinstance(reasons, list) or not reasons:
        return ["reasons must be a non-empty array"]

    errors: list[str] = []
    seen_discriminants: dict[int, str] = {}
    seen_keys: dict[str, object] = {}
    for index, entry in enumerate(reasons):
        errors.extend(
            _validate_reason_entry(
                index, entry, seen_discriminants, seen_keys
            )
        )
    expected = set(range(len(reasons)))
    actual = set(seen_discriminants)
    if not errors and actual != expected:
        errors.append(
            "reason discriminants must be contiguous 0..count-1: "
            f"expected={sorted(expected)!r}, actual={sorted(actual)!r}"
        )
    return errors


def load_registry():
    """Load and validate the reason registry TOML."""
    raw_bytes = REGISTRY_PATH.read_bytes()
    data = tomllib.loads(raw_bytes.decode("utf-8"))
    reasons = data.get("reasons", [])
    errors = _validate_reasons(reasons)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    # Sort by discriminant for deterministic output
    reasons.sort(key=lambda r: r["discriminant"])
    return data, reasons, raw_bytes


def source_hash(raw_bytes: bytes) -> str:
    """Compute SHA-256 hex digest of the TOML source bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()


def snake_to_pascal(s: str) -> str:
    """Convert snake_case to PascalCase for Rust enum variant names."""
    return "".join(word.capitalize() for word in s.split("_"))


def snake_to_upper(s: str) -> str:
    """Convert snake_case key to UPPER_SNAKE for C #define constants."""
    return s.upper()


# Metric family classification: maps keys to metric families
METRIC_FAMILIES = {
    "markdown_conversions_total": ["converted"],
    "markdown_skipped_total": [
        "skipped_accept",
        "skipped_no_accept",
        "skipped_conditional",
        "skipped_accept_reject",
        "not_eligible",
        "disabled",
        "bypass_no_transform",
        "overload",
    ],
    "markdown_errors_total": [
        "decompression_error",
        "decompression_budget_exceeded",
        "decompression_format_error",
        "decompression_truncated_input",
        "decompression_io_error",
        "timeout",
        "budget_exceeded",
        "replay_error",
        "ffi_panic",
        "conversion_error",
        "memory_budget_exceeded",
        "header_plan_apply_error",
        "streaming_mid_flight_error",
        "encoding_header_invalid",
    ],
    "markdown_failed_open_total": ["failed_open"],
    "markdown_failed_closed_total": ["failed_closed"],
}


def get_metric_family(key: str) -> str:
    """Return the Prometheus metric family for a given reason key."""
    for family, members in METRIC_FAMILIES.items():
        if key in members:
            return family
    # Fail closed: an unregistered reason key would otherwise ship silently
    # under the wrong Prometheus family with no gate signal.
    raise ValueError(f"unknown reason key {key!r}: no metric family registered")


# Log callsite descriptions for the generated Rust projection.
LOG_CALLSITES = {
    "converted": "body_filter: after successful conversion and downstream NGX_OK",
    "skipped_accept": "header_filter: Accept negotiation determined text/html preferred",
    "skipped_no_accept": "header_filter: no Accept header present",
    "skipped_conditional": "header_filter: conditional request matched (304)",
    "decompression_error": "body_filter: decompression error",
    "decompression_budget_exceeded": "body_filter: decompression output exceeded budget",
    "decompression_format_error": "body_filter: invalid compression format",
    "decompression_truncated_input": "body_filter: truncated compressed input",
    "decompression_io_error": "body_filter: decompression I/O error",
    "timeout": "body_filter: timeout",
    "budget_exceeded": "body_filter: budget exceeded",
    "replay_error": "body_filter: replay error",
    "skipped_accept_reject": "header_filter: Accept explicitly rejects text/markdown (q=0)",
    "ffi_panic": "body_filter: FFI panic",
    "not_eligible": "header_filter: response not eligible (method/status/content-type)",
    "disabled": "header_filter: module disabled for this location",
    "failed_open": "body_filter: fail-open path triggered",
    "failed_closed": "body_filter: fail-closed path triggered",
    "conversion_error": "body_filter: conversion error",
    "memory_budget_exceeded": "body_filter: memory budget exceeded",
    "overload": "header_filter: inflight guard overload",
    "header_plan_apply_error": "header_filter: header plan apply error",
    "streaming_mid_flight_error": "body_filter: streaming mid-flight error",
    "bypass_no_transform": "header_filter: no-transform bypass",
    "encoding_header_invalid": "body_filter: malformed Content-Encoding grammar",
}


def generate_do_not_edit_header(source_hash_hex: str, lang: str = "rust") -> str:
    """Generate a DO NOT EDIT header comment."""
    if lang == "rust":
        return (
            f"// DO NOT EDIT — generated by tools/reason-codegen/generate.py\n"
            f"// Source: components/rust-converter/reason_registry.toml\n"
            f"// Source SHA-256: {source_hash_hex}\n"
            f"//\n"
            f"// Regenerate with: python3 tools/reason-codegen/generate.py\n"
        )
    else:  # C
        return (
            f"/*\n"
            f" * DO NOT EDIT — generated by tools/reason-codegen/generate.py\n"
            f" * Source: components/rust-converter/reason_registry.toml\n"
            f" * Source SHA-256: {source_hash_hex}\n"
            f" *\n"
            f" * Regenerate with: python3 tools/reason-codegen/generate.py\n"
            f" */\n"
        )


def generate_rust(reasons, hash_hex: str) -> str:
    """Generate the Rust reason_code.rs content."""
    count = len(reasons)
    lines = []
    lines.extend([
        generate_do_not_edit_header(hash_hex, "rust"),
        "",
        "//! Generated reason-code projection for the declarative registry.",
        "//!",
        "//! The canonical source is `reason_registry.toml`; this module defines",
        "//! the [`ReasonCode`] enum projected from that registry. It represents",
        "//! every possible outcome of the module's conversion decision chain.",
        "//! C code accesses these values through FFI, and all metrics, logging, and",
        "//! documentation use the generated projections.",
        "//!",
        "//! # FFI Boundary",
        "//!",
        "//! The enum uses `#[repr(u8)]` so the compiler guarantees all discriminants",
        "//! fit in a single byte, matching the C reason-code accessors.",
        "//! Each variant has a stable numeric discriminant that must not change once",
        "//! assigned.",
        "",
    ])

    # Count constant
    lines.extend([
        "/// Total number of reason code variants.",
        "///",
        "/// This constant is used by the closure test to verify that all variants",
        "/// are accounted for in the `ALL` array. Update this when adding variants.",
        f"pub const REASON_CODE_COUNT: usize = {count};",
        "",
    ])

    # Compile-time guard
    lines.extend([
        "/// Compile-time guard: all discriminants must fit in a `u8` because the",
        "/// FFI boundary transports reason-code discriminants as `u8`.",
        "/// If the enum grows beyond 256 variants this assertion will fail the build.",
        "const _: () = assert!(",
        '    REASON_CODE_COUNT <= 256,',
        '    "ReasonCode discriminant range exceeds the u8 FFI transport"',
        ");",
        "",
    ])

    return "\n".join(lines)


def generate_rust_enum(reasons) -> str:
    """Generate the enum definition portion."""
    lines = []
    lines.extend([
        "/// Generated reason code enum projected from `reason_registry.toml`.",
        "///",
        "/// Every conversion decision path produces exactly one `ReasonCode`.",
        "/// The numeric discriminants are stable and must not be reordered.",
        "///",
        "/// # Repr",
        "///",
        "/// Uses `#[repr(u8)]` so the compiler guarantees all discriminants fit in",
        "/// a single byte. The enum is never passed directly across FFI; only its",
        "/// discriminant value is transported as `u8`.",
        "#[repr(u8)]",
        "#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]",
        "pub enum ReasonCode {",
    ])

    for r in reasons:
        variant = snake_to_pascal(r["key"])
        disc = r["discriminant"]
        lines.extend([
            f"    /// Reason: {r['key']} (stage: {r['default_stage']})",
            f"    {variant} = {disc},",
            "",
        ])

    lines.extend([
        "}",
        "",
    ])
    return "\n".join(lines)


def generate_rust_test_module() -> str:
    """Generate the test module include."""
    lines = []
    lines.extend([
        "#[cfg(test)]",
        '#[path = "reason_code_complexity_tests.rs"]',
        "mod reason_code_complexity_tests;",
        "",
    ])
    return "\n".join(lines)


def generate_rust_all_array(reasons) -> str:
    """Generate the ALL constant array."""
    lines = []
    lines.extend([
        "/// Array of all reason code variants for exhaustive iteration.",
        "///",
        "/// This array must contain every variant of [`ReasonCode`] exactly once.",
        "/// The closure test verifies this invariant.",
        "///",
        "/// cbindgen:ignore",
        "pub const ALL: [ReasonCode; REASON_CODE_COUNT] = [",
    ])
    for r in reasons:
        variant = snake_to_pascal(r["key"])
        lines.append(f"    ReasonCode::{variant},")
    lines.extend([
        "];",
        "",
    ])
    return "\n".join(lines)


def _append_rust_as_str(lines, reasons):
    """Append the Rust `as_str` method."""
    lines.append("impl ReasonCode {")

    # as_str
    lines.extend([
        "    /// Return the lowercase snake_case string representation.",
        RUST_DOC_LINE,
        "    /// This string is used in structured logs, diagnostics endpoints,",
        "    /// and as the label value in Prometheus metrics.",
        RUST_DOC_LINE,
        RUST_DOC_EXAMPLES,
        RUST_DOC_LINE,
        RUST_DOC_CODE_FENCE,
        RUST_DOC_REASON_USE,
        RUST_DOC_LINE,
        '    /// assert_eq!(ReasonCode::Converted.as_str(), "converted");',
        '    /// assert_eq!(ReasonCode::Timeout.as_str(), "timeout");',
        RUST_DOC_CODE_FENCE,
        "    pub fn as_str(self) -> &'static str {",
        RUST_MATCH_SELF,
    ])
    for r in reasons:
        variant = snake_to_pascal(r["key"])
        lines.append(f'            ReasonCode::{variant} => "{r["key"]}",')
    lines.extend([
        "        }",
        "    }",
        "",
    ])


def _append_rust_metric_key(lines, reasons):
    """Append the Rust `metric_key` method."""

    # metric_key
    lines.extend([
        "    /// Return the Prometheus metric key name for this reason code.",
        RUST_DOC_LINE,
        RUST_DOC_EXAMPLES,
        RUST_DOC_LINE,
        RUST_DOC_CODE_FENCE,
        RUST_DOC_REASON_USE,
        RUST_DOC_LINE,
        "    /// assert_eq!(",
        '    ///     ReasonCode::Converted.metric_key(),',
        '    ///     "markdown_conversions_total"',
        "    /// );",
        RUST_DOC_CODE_FENCE,
        "    pub fn metric_key(self) -> &'static str {",
        RUST_MATCH_SELF,
    ])

    # Emit one arm per registry entry. The generator fails closed when a
    # reason is absent from METRIC_FAMILIES: get_metric_family raises
    # ValueError and aborts generation instead of an error-family fallback.
    for reason in reasons:
        variant = snake_to_pascal(reason["key"])
        family = get_metric_family(reason["key"])
        lines.append(f'            ReasonCode::{variant} => "{family}",')

    lines.extend([
        "        }",
        "    }",
        "",
    ])


def generate_rust_impl(reasons) -> str:
    """Generate the impl ReasonCode block with its first two methods."""
    lines = []
    _append_rust_as_str(lines, reasons)
    _append_rust_metric_key(lines, reasons)

    return "\n".join(lines)


def generate_rust_impl_continued(reasons) -> str:
    """
    Generate Rust implementations for log-callsite descriptions, discriminant conversion, and reverse discriminant lookup.

    Parameters:
        reasons: Registry entries used to generate the reason-code mappings and examples.

    Returns:
        The generated Rust implementation as a string.
    """
    lines = []

    # log_callsite
    lines.extend([
        "    /// Return the expected `log_decision()` callsite description.",
        RUST_DOC_LINE,
        RUST_DOC_EXAMPLES,
        RUST_DOC_LINE,
        RUST_DOC_CODE_FENCE,
        RUST_DOC_REASON_USE,
        RUST_DOC_LINE,
        "    /// assert_eq!(",
        "    ///     ReasonCode::Converted.log_callsite(),",
        '    ///     "body_filter: after successful conversion and downstream NGX_OK"',
        "    /// );",
        RUST_DOC_CODE_FENCE,
        "    pub fn log_callsite(self) -> &'static str {",
        RUST_MATCH_SELF,
    ])
    for r in reasons:
        variant = snake_to_pascal(r["key"])
        if r["key"] not in LOG_CALLSITES:
            # Fail closed: an unregistered key would otherwise ship with a
            # synthesized body_filter callsite that misstates the phase.
            raise ValueError(
                f"unknown reason key {r['key']!r}: no log callsite registered"
            )
        callsite = LOG_CALLSITES[r["key"]]
        lines.extend([
            f'            ReasonCode::{variant} => {{',
            f'                "{callsite}"',
            "            }",
        ])
    lines.extend([
        "        }",
        "    }",
        "",
    ])

    # discriminant
    lines.extend([
        "    /// Return the numeric discriminant value for FFI transport.",
        RUST_DOC_LINE,
        RUST_DOC_EXAMPLES,
        RUST_DOC_LINE,
        RUST_DOC_CODE_FENCE,
        RUST_DOC_REASON_USE,
        RUST_DOC_LINE,
    ])
    # The doctest assertions use discriminant values derived from the
    # reasons registry data (looked up by key) rather than hardcoded
    # constants, so renumbering a registry entry cannot silently stale
    # the generated documentation.
    converted_disc = next(
        (r["discriminant"] for r in reasons if r["key"] == "converted"), 0
    )
    timeout_disc = next(
        (r["discriminant"] for r in reasons if r["key"] == "timeout"), 9
    )
    invalid_disc = max((r["discriminant"] for r in reasons), default=-1) + 1
    lines.extend([
        f"    /// assert_eq!(ReasonCode::Converted.discriminant(), {converted_disc});",
        f"    /// assert_eq!(ReasonCode::Timeout.discriminant(), {timeout_disc});",
        RUST_DOC_CODE_FENCE,
        "    pub fn discriminant(self) -> u32 {",
        "        self as u32",
        "    }",
        "",
    ])

    # from_discriminant
    lines.extend([
        "    /// Attempt to construct a `ReasonCode` from its numeric discriminant.",
        RUST_DOC_LINE,
        "    /// Returns `None` if the value does not correspond to a known variant.",
        RUST_DOC_LINE,
        RUST_DOC_EXAMPLES,
        RUST_DOC_LINE,
        RUST_DOC_CODE_FENCE,
        RUST_DOC_REASON_USE,
        RUST_DOC_LINE,
        f"    /// assert_eq!(ReasonCode::from_discriminant({converted_disc}), "
        "Some(ReasonCode::Converted));",
        f"    /// assert_eq!(ReasonCode::from_discriminant({invalid_disc}), None);",
        RUST_DOC_CODE_FENCE,
        "    pub fn from_discriminant(value: u32) -> Option<Self> {",
        "        match value {",
    ])
    for r in reasons:
        variant = snake_to_pascal(r["key"])
        lines.append(f"            {r['discriminant']} => Some(ReasonCode::{variant}),")
    lines.extend([
        "            _ => None,",
        "        }",
        "    }",
        "}",
        "",
    ])

    return "\n".join(lines)


def generate_rust_ffi() -> str:
    """Generate the FFI functions at the end of the Rust file."""
    lines = []

    # markdown_reason_code_str
    lines.extend([
        "/// Get the string representation of a reason code by its numeric value.",
        "///",
        "/// Returns a pointer to a static string and writes the length to `out_len`.",
        "/// Returns NULL if the discriminant is invalid.",
        "///",
        "/// # Safety",
        "///",
        "/// The caller must ensure that `out_len` either is NULL or points to",
        "/// writable storage for a `usize`.",
        RUST_NO_MANGLE,
        "pub unsafe extern \"C\" fn markdown_reason_code_str(code: u32, out_len: *mut usize) -> *const u8 {",
        "    match ReasonCode::from_discriminant(code) {",
        "        Some(rc) => {",
        RUST_REASON_AS_STR,
        RUST_OUT_LEN_GUARD,
        "                unsafe { *out_len = s.len() };",
        "            }",
        "            s.as_ptr()",
        "        }",
        "        None => {",
        RUST_OUT_LEN_GUARD,
        "                unsafe { *out_len = 0 };",
        "            }",
        "            std::ptr::null()",
        "        }",
        "    }",
        "}",
        "",
    ])

    # markdown_reason_code_metric_key
    lines.extend([
        "/// Get the Prometheus metric key for a reason code by its numeric value.",
        "///",
        "/// Returns a pointer to a static string and writes the length to `out_len`.",
        "/// Returns NULL if the discriminant is invalid.",
        "///",
        "/// # Safety",
        "///",
        "/// The caller must ensure that `out_len` either is NULL or points to",
        "/// writable storage for a `usize`.",
        RUST_NO_MANGLE,
        "pub unsafe extern \"C\" fn markdown_reason_code_metric_key(",
        "    code: u32,",
        "    out_len: *mut usize,",
        ") -> *const u8 {",
        "    match ReasonCode::from_discriminant(code) {",
        "        Some(rc) => {",
        "            let s = rc.metric_key();",
        RUST_OUT_LEN_GUARD,
        "                unsafe { *out_len = s.len() };",
        "            }",
        "            s.as_ptr()",
        "        }",
        "        None => {",
        RUST_OUT_LEN_GUARD,
        "                unsafe { *out_len = 0 };",
        "            }",
        "            std::ptr::null()",
        "        }",
        "    }",
        "}",
        "",
    ])

    # markdown_reason_code_count
    lines.extend([
        "/// Return the total number of defined reason codes.",
        "///",
        "/// C callers can use this to verify they handle all variants.",
        RUST_NO_MANGLE,
        "pub extern \"C\" fn markdown_reason_code_count() -> u32 {",
        "    REASON_CODE_COUNT as u32",
        "}",
        "",
    ])

    return "\n".join(lines)


def generate_rust_tests() -> str:
    """Generate the test module for the Rust file."""
    lines = []
    lines.extend([
        "#[cfg(test)]",
        "mod tests {",
        "    use super::*;",
        "    use std::collections::HashSet;",
        "",
        "    /// Verify that ALL array length matches REASON_CODE_COUNT.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_all_array_length_matches_count() {",
        "        assert_eq!(",
        "            ALL.len(),",
        "            REASON_CODE_COUNT,",
        '            "ALL array length ({}) must equal REASON_CODE_COUNT ({})",',
        "            ALL.len(),",
        "            REASON_CODE_COUNT",
        "        );",
        "    }",
        "",
        "    /// Verify that every variant in ALL has a unique discriminant.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_discriminants_unique() {",
        "        let mut seen = HashSet::new();",
        RUST_ALL_LOOP,
        "            let d = rc.discriminant();",
        '            assert!(seen.insert(d), "Duplicate discriminant {} for {:?}", d, rc);',
        "        }",
        "    }",
        "",
        "    /// Verify that every variant in ALL has a unique string representation.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_strings_unique() {",
        "        let mut seen = HashSet::new();",
        RUST_ALL_LOOP,
        RUST_REASON_AS_STR,
        '            assert!(seen.insert(s), "Duplicate string \'{}\' for {:?}", s, rc);',
        "        }",
        "    }",
        "",
        "    /// Verify that all string representations are lowercase snake_case.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_strings_are_lowercase_snake_case() {",
        '        let re = regex::Regex::new(r"^[a-z][a-z0-9_]*$").unwrap();',
        RUST_ALL_LOOP,
        RUST_REASON_AS_STR,
        '            assert!(!s.is_empty(), "{:?} has empty string", rc);',
        "            assert!(",
        "                re.is_match(s),",
        '                "String \'{}\' for {:?} does not match lowercase snake_case pattern",',
        "                s,",
        "                rc",
        "            );",
        "        }",
        "    }",
        "",
    ])

    return "\n".join(lines)


def generate_rust_tests_continued() -> str:
    """Generate remaining test functions."""
    lines = []
    lines.extend([
        "    /// Verify that exactly 5 unified metric families are used.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_metric_keys_unified_families() {",
        "        let mut families: HashSet<&str> = HashSet::new();",
        RUST_ALL_LOOP,
        "            families.insert(rc.metric_key());",
        "        }",
        "        assert_eq!(",
        "            families.len(),",
        "            5,",
        '            "Expected exactly 5 unified metric families, got {:?}",',
        "            families",
        "        );",
        "    }",
        "",
        "    /// Verify round-trip: discriminant -> from_discriminant -> same variant.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_from_discriminant_roundtrip() {",
        RUST_ALL_LOOP,
        "            let d = rc.discriminant();",
        "            let recovered = ReasonCode::from_discriminant(d);",
        "            assert_eq!(recovered, Some(*rc));",
        "        }",
        "    }",
        "",
        "    /// Verify that from_discriminant returns None for invalid values.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_from_discriminant_invalid() {",
        "        assert_eq!(ReasonCode::from_discriminant(255), None);",
        "        assert_eq!(ReasonCode::from_discriminant(u32::MAX), None);",
        "    }",
        "",
        "    /// Closure test: verify discriminant range is contiguous 0..COUNT-1.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_discriminant_range_contiguous() {",
        "        let mut discriminants: Vec<u32> = ALL.iter().map(|rc| rc.discriminant()).collect();",
        "        discriminants.sort();",
        "        for (i, d) in discriminants.iter().enumerate() {",
        '            assert_eq!(*d, i as u32, "Expected discriminant {} at index {}", i, i);',
        "        }",
        "    }",
        "",
        "    /// FFI function test: markdown_reason_code_str returns correct data.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_ffi_reason_code_str() {",
        RUST_ALL_LOOP,
        "            let mut len: usize = 0;",
        "            let ptr = unsafe { markdown_reason_code_str(rc.discriminant(), &mut len) };",
        '            assert!(!ptr.is_null(), "NULL returned for {:?}", rc);',
        "            assert_eq!(len, rc.as_str().len());",
        "            let slice = unsafe { std::slice::from_raw_parts(ptr, len) };",
        "            let s = std::str::from_utf8(slice).unwrap();",
        "            assert_eq!(s, rc.as_str());",
        "        }",
        "    }",
        "",
        "    /// FFI function test: markdown_reason_code_count returns correct value.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_ffi_reason_code_count() {",
        "        assert_eq!(markdown_reason_code_count(), REASON_CODE_COUNT as u32);",
        "    }",
        "",
        "    /// Verify the enum size is suitable for FFI (repr(u8) single-byte).",
        RUST_TEST_ATTRIBUTE,
        "    fn test_enum_size_for_ffi() {",
        "        assert_eq!(std::mem::size_of::<ReasonCode>(), 1);",
        "        assert_eq!(std::mem::align_of::<ReasonCode>(), 1);",
        "    }",
        "",
        "    /// Verify that every variant has a non-empty log_callsite().",
        RUST_TEST_ATTRIBUTE,
        "    fn test_log_callsite_non_empty() {",
        RUST_ALL_LOOP,
        '            assert!(!rc.log_callsite().is_empty(), "{:?} has empty log_callsite", rc);',
        "        }",
        "    }",
        "",
        "    /// Verify that log_callsite() descriptions indicate a valid filter phase.",
        RUST_TEST_ATTRIBUTE,
        "    fn test_log_callsite_has_valid_phase() {",
        RUST_ALL_LOOP,
        "            let callsite = rc.log_callsite();",
        "            assert!(",
        '                callsite.starts_with("header_filter:") || callsite.starts_with("body_filter:"),',
        '                "{:?} log_callsite must start with header_filter: or body_filter:", rc',
        "            );",
        "        }",
        "    }",
        "}",
        "",
    ])

    return "\n".join(lines)


def generate_manifest(reasons, hash_hex: str) -> dict:
    """Generate the count/hash manifest."""
    min_disc = min(r["discriminant"] for r in reasons)
    max_disc = max(r["discriminant"] for r in reasons)
    return {
        "schema_version": 1,
        "generator": "tools/reason-codegen/generate.py",
        "source": "components/rust-converter/reason_registry.toml",
        "source_sha256": hash_hex,
        "total_count": len(reasons),
        "discriminant_range": {"min": min_disc, "max": max_disc},
        "metric_families": sorted(METRIC_FAMILIES.keys()),
        "stages": sorted({r["default_stage"] for r in reasons}),
        "outcomes": sorted({r["outcome"] for r in reasons}),
    }


def generate_listing(hash_hex: str) -> dict:
    """Generate the listing of generated artifacts."""
    return {
        "schema_version": 1,
        "generator": "tools/reason-codegen/generate.py",
        "source": "components/rust-converter/reason_registry.toml",
        "source_sha256": hash_hex,
        "generated_artifacts": [
            {
                "path": "components/rust-converter/src/decision/reason_code.rs",
                "description": "Rust enum with all metadata and FFI exports",
            },
            {
                "path": "components/nginx-module/src/markdown_reason_meta.h",
                "description": "C reason metadata table for diagnostics",
            },
            {
                "path": "artifacts/release/0.9.2/reason-registry-report.json",
                "description": "Count/hash manifest for drift detection",
            },
        ],
    }


def build_full_rust(reasons, hash_hex: str) -> str:
    """Assemble the complete Rust file content."""
    parts = []
    parts.extend([
        generate_rust(reasons, hash_hex),
        generate_rust_enum(reasons),
        generate_rust_test_module(),
        generate_rust_all_array(reasons),
        generate_rust_impl(reasons),
        generate_rust_impl_continued(reasons),
        generate_rust_ffi(),
        generate_rust_tests(),
        generate_rust_tests_continued(),
    ])
    return "\n".join(parts)


def format_rust_source(content: str) -> str:
    """Canonicalize generated Rust with the repository toolchain formatter."""
    rustfmt = resolve_approved_executable("rustfmt")
    if rustfmt is None:
        raise RuntimeError(
            "rustfmt is unavailable or is not under an approved executable root"
        )
    result = subprocess.run(
        [rustfmt, "--emit", "stdout"],
        input=content,
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "rustfmt failed while formatting generated reason_code.rs: "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def write_if_changed(path: Path, content: str) -> bool:
    """Write content to path only if it differs from existing. Returns True if written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return False
    path.write_text(content, encoding="utf-8")
    return True


def check_drift(path: Path, content: str) -> bool:
    """Check if generated content matches checked-in file. Returns True if OK."""
    if not path.exists():
        print(f"  MISSING: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return False
    existing = path.read_text(encoding="utf-8")
    if existing != content:
        print(f"  DRIFT: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
        return False
    print(f"  OK: {path.relative_to(REPO_ROOT)}")
    return True


def generate_c_header(reasons, hash_hex: str) -> str:
    """Generate the C reason metadata header from the registry."""
    lines = [
        "/*",
        " * Generated by tools/reason-codegen/generate.py — DO NOT EDIT.",
        f" * Source: components/rust-converter/reason_registry.toml (SHA-256: {hash_hex[:16]}...)",
        " *",
        " * This file provides generated reason metadata and stable discriminants",
        " * consumed by the C diagnostics renderer and reason accessors. The TOML",
        " * registry remains the only source; this header replaces former",
        " * hand-maintained diagnostics reason tables.",
        " */",
        "#ifndef MARKDOWN_REASON_META_H",
        "#define MARKDOWN_REASON_META_H",
        "",
        "#include <ngx_config.h>",
        "#include <ngx_core.h>",
        "",
        "typedef struct {",
        "    const char    *key;",
        "    const char    *outcome;",
        "    const char    *stage;",
        "    const char    *error_origin;",
        "} markdown_reason_meta_t;",
        "",
        f"#define MARKDOWN_REASON_META_COUNT {len(reasons)}",
        "",
        "/* Stable discriminants projected from the canonical registry. */",
    ]

    for reason in reasons:
        lines.append(
            f"#define MARKDOWN_REASON_CODE_{reason['key'].upper()} "
            f"{reason['discriminant']}"
        )

    lines.extend([
        "",
        "/*",
        " * Reason metadata table (index = discriminant).",
        " * Entry MARKDOWN_REASON_META_COUNT is the unknown sentinel.",
        " */",
        "#ifdef MARKDOWN_REASON_META_DEFINE",
        "const markdown_reason_meta_t",
        "    markdown_reason_meta[MARKDOWN_REASON_META_COUNT + 1] = {",
    ])

    for r in reasons:
        disc = r["discriminant"]
        key = r["key"]
        outcome = r["outcome"]
        stage = r["default_stage"]
        origin = r["default_origin"]
        lines.append(
            f'    [{disc}] = {{ "{key}", "{outcome}", "{stage}", "{origin}" }},'
        )

    # Unknown sentinel
    lines.append(
        f'    [{len(reasons)}] = {{ "unknown", "failed_closed", "delivery", "internal" }},'
    )

    lines.extend([
        "};",
        "#else",
        "extern const markdown_reason_meta_t",
        "    markdown_reason_meta[MARKDOWN_REASON_META_COUNT + 1];",
        "#endif",
        "",
    ])
    lines.extend([
        "#endif /* MARKDOWN_REASON_META_H */",
        "",
    ])

    return "\n".join(lines)


def _build_generated_outputs(reasons, hash_hex: str):
    """Build the complete path/content set for generated artifacts."""
    rust_content = format_rust_source(build_full_rust(reasons, hash_hex))
    manifest_content = json.dumps(
        generate_manifest(reasons, hash_hex), indent=2, ensure_ascii=False
    ) + "\n"
    listing_content = json.dumps(
        generate_listing(hash_hex), indent=2, ensure_ascii=False
    ) + "\n"
    c_header_content = generate_c_header(reasons, hash_hex)
    return [
        (RUST_OUTPUT, rust_content),
        (C_HEADER_OUTPUT, c_header_content),
        (MANIFEST_OUTPUT, manifest_content),
        (LISTING_OUTPUT, listing_content),
    ]


def _check_generated_outputs(outputs):
    """Check every generated artifact and return a process status."""
    print("\nDrift check mode:")
    # Materialize every per-artifact result before computing the overall
    # status so a single drifted artifact cannot short-circuit the check
    # and hide drift in the remaining artifacts.
    results: list[bool] = []
    for path, content in outputs:
        results.append(check_drift(path, content))
    all_ok = all(results)
    if not all_ok:
        print(
            "\nERROR: Generated files are out of date. "
            "Run: python3 tools/reason-codegen/generate.py",
            file=sys.stderr,
        )
        return 1
    print("\nAll generated files are up to date.")
    return 0


def _write_generated_outputs(outputs):
    """Write changed generated artifacts and report the result."""
    files_written = [
        str(path.relative_to(REPO_ROOT))
        for path, content in outputs
        if write_if_changed(path, content)
    ]
    if not files_written:
        print("\nAll files already up to date.")
        return
    print(f"\nWrote {len(files_written)} file(s):")
    for path in files_written:
        print(f"  {path}")


def main():
    """Main entry point."""
    check_mode = "--check" in sys.argv

    # Load registry
    _, reasons, raw_bytes = load_registry()
    hash_hex = source_hash(raw_bytes)

    print(f"Reason registry: {len(reasons)} entries, SHA-256: {hash_hex[:16]}...")
    outputs = _build_generated_outputs(reasons, hash_hex)
    if check_mode:
        return _check_generated_outputs(outputs)
    _write_generated_outputs(outputs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
