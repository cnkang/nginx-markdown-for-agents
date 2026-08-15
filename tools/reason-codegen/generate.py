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
LEGACY_KEY_RE = re.compile(r"^[A-Z](?:[A-Z0-9]|_(?=[A-Z0-9]))*$")
VALID_STAGES = frozenset({
    "eligibility", "decompression", "parsing", "conversion",
    "precommit", "postcommit", "delivery", "dynconf",
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


def _validate_legacy_keys(index: int, entry: dict) -> list[str]:
    """Validate optional compatibility aliases on one reason entry."""
    aliases = entry.get("legacy_keys", [])
    if not isinstance(aliases, list):
        return [f"reasons[{index}] legacy_keys must be an array"]
    invalid = [
        alias
        for alias in aliases
        if not isinstance(alias, str)
        or LEGACY_KEY_RE.fullmatch(alias) is None
    ]
    if invalid:
        return [
            f"reasons[{index}] legacy_keys contains invalid values: {invalid!r}"
        ]
    if len(set(aliases)) != len(aliases):
        return [f"reasons[{index}] legacy_keys contains duplicates"]
    return []


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
    errors.extend(_validate_legacy_keys(index, entry))
    return errors


def _validate_reasons(reasons: object) -> list[str]:
    """Validate registry entries before sorting or generating artifacts."""
    if not isinstance(reasons, list) or not reasons:
        return ["reasons must be a non-empty array"]

    errors: list[str] = []
    seen_discriminants: dict[int, str] = {}
    seen_keys: dict[str, object] = {}
    seen_legacy_keys: dict[str, int] = {}
    for index, entry in enumerate(reasons):
        errors.extend(
            _validate_reason_entry(
                index, entry, seen_discriminants, seen_keys
            )
        )
        if isinstance(entry, dict):
            for alias in entry.get("legacy_keys", []):
                if alias in seen_legacy_keys:
                    errors.append(
                        f"duplicate legacy reason key {alias!r}: "
                        f"entries {seen_legacy_keys[alias]} and {index}"
                    )
                else:
                    seen_legacy_keys[alias] = index

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
        "invalid_dynconf",
        "degraded_snapshot",
        "overload",
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
    "invalid_dynconf": "header_filter: invalid dynconf",
    "degraded_snapshot": "header_filter: degraded dynconf snapshot",
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
    lines.append(generate_do_not_edit_header(hash_hex, "rust"))
    lines.append("")
    lines.append("//! Generated reason-code projection for the declarative registry.")
    lines.append("//!")
    lines.append("//! The canonical source is `reason_registry.toml`; this module defines")
    lines.append("//! the [`ReasonCode`] enum projected from that registry. It represents")
    lines.append("//! every possible outcome of the module's conversion decision chain.")
    lines.append("//! C code accesses these values through FFI, and all metrics, logging, and")
    lines.append("//! documentation use the generated projections.")
    lines.append("//!")
    lines.append("//! # FFI Boundary")
    lines.append("//!")
    lines.append("//! The enum uses `#[repr(u8)]` so the compiler guarantees all discriminants")
    lines.append("//! fit in a single byte, matching the C reason-code accessors.")
    lines.append("//! Each variant has a stable numeric discriminant that must not change once")
    lines.append("//! assigned.")
    lines.append("")

    # Count constant
    lines.append("/// Total number of reason code variants.")
    lines.append("///")
    lines.append("/// This constant is used by the closure test to verify that all variants")
    lines.append("/// are accounted for in the `ALL` array. Update this when adding variants.")
    lines.append(f"pub const REASON_CODE_COUNT: usize = {count};")
    lines.append("")

    # Compile-time guard
    lines.append("/// Compile-time guard: all discriminants must fit in a `u8` because the")
    lines.append("/// FFI boundary transports reason-code discriminants as `u8`.")
    lines.append("/// If the enum grows beyond 256 variants this assertion will fail the build.")
    lines.append("const _: () = assert!(")
    lines.append('    REASON_CODE_COUNT <= 256,')
    lines.append('    "ReasonCode discriminant range exceeds the u8 FFI transport"')
    lines.append(");")
    lines.append("")

    return "\n".join(lines)


def generate_rust_enum(reasons) -> str:
    """Generate the enum definition portion."""
    lines = []
    lines.append("/// Generated reason code enum projected from `reason_registry.toml`.")
    lines.append("///")
    lines.append("/// Every conversion decision path produces exactly one `ReasonCode`.")
    lines.append("/// The numeric discriminants are stable and must not be reordered.")
    lines.append("///")
    lines.append("/// # Repr")
    lines.append("///")
    lines.append("/// Uses `#[repr(u8)]` so the compiler guarantees all discriminants fit in")
    lines.append("/// a single byte. The enum is never passed directly across FFI; only its")
    lines.append("/// discriminant value is transported as `u8`.")
    lines.append("#[repr(u8)]")
    lines.append("#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]")
    lines.append("pub enum ReasonCode {")

    for r in reasons:
        variant = snake_to_pascal(r["key"])
        disc = r["discriminant"]
        lines.append(f"    /// Reason: {r['key']} (stage: {r['default_stage']})")
        lines.append(f"    {variant} = {disc},")
        lines.append("")

    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def generate_rust_test_module() -> str:
    """Generate the test module include."""
    lines = []
    lines.append("#[cfg(test)]")
    lines.append('#[path = "reason_code_complexity_tests.rs"]')
    lines.append("mod reason_code_complexity_tests;")
    lines.append("")
    return "\n".join(lines)


def generate_rust_all_array(reasons) -> str:
    """Generate the ALL constant array."""
    lines = []
    lines.append("/// Array of all reason code variants for exhaustive iteration.")
    lines.append("///")
    lines.append("/// This array must contain every variant of [`ReasonCode`] exactly once.")
    lines.append("/// The closure test verifies this invariant.")
    lines.append("///")
    lines.append("/// cbindgen:ignore")
    lines.append("pub const ALL: [ReasonCode; REASON_CODE_COUNT] = [")
    for r in reasons:
        variant = snake_to_pascal(r["key"])
        lines.append(f"    ReasonCode::{variant},")
    lines.append("];")
    lines.append("")
    return "\n".join(lines)


def _append_rust_as_str(lines, reasons):
    """Append the Rust `as_str` method."""
    lines.append("impl ReasonCode {")

    # as_str
    lines.append("    /// Return the lowercase snake_case string representation.")
    lines.append(RUST_DOC_LINE)
    lines.append("    /// This string is used in structured logs, diagnostics endpoints,")
    lines.append("    /// and as the label value in Prometheus metrics.")
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_EXAMPLES)
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append(RUST_DOC_REASON_USE)
    lines.append(RUST_DOC_LINE)
    lines.append('    /// assert_eq!(ReasonCode::Converted.as_str(), "converted");')
    lines.append('    /// assert_eq!(ReasonCode::Timeout.as_str(), "timeout");')
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append("    pub fn as_str(self) -> &'static str {")
    lines.append(RUST_MATCH_SELF)
    for r in reasons:
        variant = snake_to_pascal(r["key"])
        lines.append(f'            ReasonCode::{variant} => "{r["key"]}",')
    lines.append("        }")
    lines.append("    }")
    lines.append("")


def _append_rust_metric_key(lines, reasons):
    """Append the Rust `metric_key` method."""

    # metric_key
    lines.append("    /// Return the Prometheus metric key name for this reason code.")
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_EXAMPLES)
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append(RUST_DOC_REASON_USE)
    lines.append(RUST_DOC_LINE)
    lines.append("    /// assert_eq!(")
    lines.append('    ///     ReasonCode::Converted.metric_key(),')
    lines.append('    ///     "markdown_conversions_total"')
    lines.append("    /// );")
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append("    pub fn metric_key(self) -> &'static str {")
    lines.append(RUST_MATCH_SELF)

    # Emit one arm per registry entry. The generator fails closed when a
    # reason is absent from METRIC_FAMILIES: get_metric_family raises
    # ValueError and aborts generation instead of an error-family fallback.
    for reason in reasons:
        variant = snake_to_pascal(reason["key"])
        family = get_metric_family(reason["key"])
        lines.append(f'            ReasonCode::{variant} => "{family}",')

    lines.append("        }")
    lines.append("    }")
    lines.append("")


def generate_rust_impl(reasons) -> str:
    """Generate the impl ReasonCode block with its first two methods."""
    lines = []
    _append_rust_as_str(lines, reasons)
    _append_rust_metric_key(lines, reasons)

    return "\n".join(lines)


def generate_rust_impl_continued(reasons) -> str:
    """Generate log_callsite, discriminant, from_discriminant methods."""
    lines = []

    # log_callsite
    lines.append("    /// Return the expected `log_decision()` callsite description.")
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_EXAMPLES)
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append(RUST_DOC_REASON_USE)
    lines.append(RUST_DOC_LINE)
    lines.append("    /// assert_eq!(")
    lines.append("    ///     ReasonCode::Converted.log_callsite(),")
    lines.append('    ///     "body_filter: after successful conversion and downstream NGX_OK"')
    lines.append("    /// );")
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append("    pub fn log_callsite(self) -> &'static str {")
    lines.append(RUST_MATCH_SELF)
    for r in reasons:
        variant = snake_to_pascal(r["key"])
        callsite = LOG_CALLSITES.get(r["key"], f"body_filter: {r['key']}")
        lines.append(f'            ReasonCode::{variant} => {{')
        lines.append(f'                "{callsite}"')
        lines.append("            }")
    lines.append("        }")
    lines.append("    }")
    lines.append("")

    # discriminant
    lines.append("    /// Return the numeric discriminant value for FFI transport.")
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_EXAMPLES)
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append(RUST_DOC_REASON_USE)
    lines.append(RUST_DOC_LINE)
    lines.append("    /// assert_eq!(ReasonCode::Converted.discriminant(), 0);")
    lines.append("    /// assert_eq!(ReasonCode::Timeout.discriminant(), 9);")
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append("    pub fn discriminant(self) -> u32 {")
    lines.append("        self as u32")
    lines.append("    }")
    lines.append("")

    # from_discriminant
    lines.append("    /// Attempt to construct a `ReasonCode` from its numeric discriminant.")
    lines.append(RUST_DOC_LINE)
    lines.append("    /// Returns `None` if the value does not correspond to a known variant.")
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_EXAMPLES)
    lines.append(RUST_DOC_LINE)
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append(RUST_DOC_REASON_USE)
    lines.append(RUST_DOC_LINE)
    lines.append("    /// assert_eq!(ReasonCode::from_discriminant(0), Some(ReasonCode::Converted));")
    lines.append("    /// assert_eq!(ReasonCode::from_discriminant(255), None);")
    lines.append(RUST_DOC_CODE_FENCE)
    lines.append("    pub fn from_discriminant(value: u32) -> Option<Self> {")
    lines.append("        match value {")
    for r in reasons:
        variant = snake_to_pascal(r["key"])
        lines.append(f"            {r['discriminant']} => Some(ReasonCode::{variant}),")
    lines.append("            _ => None,")
    lines.append("        }")
    lines.append("    }")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def generate_rust_ffi() -> str:
    """Generate the FFI functions at the end of the Rust file."""
    lines = []

    # markdown_reason_code_str
    lines.append("/// Get the string representation of a reason code by its numeric value.")
    lines.append("///")
    lines.append("/// Returns a pointer to a static string and writes the length to `out_len`.")
    lines.append("/// Returns NULL if the discriminant is invalid.")
    lines.append("///")
    lines.append("/// # Safety")
    lines.append("///")
    lines.append("/// The caller must ensure that `out_len` either is NULL or points to")
    lines.append("/// writable storage for a `usize`.")
    lines.append(RUST_NO_MANGLE)
    lines.append("pub unsafe extern \"C\" fn markdown_reason_code_str(code: u32, out_len: *mut usize) -> *const u8 {")
    lines.append("    match ReasonCode::from_discriminant(code) {")
    lines.append("        Some(rc) => {")
    lines.append(RUST_REASON_AS_STR)
    lines.append(RUST_OUT_LEN_GUARD)
    lines.append("                unsafe { *out_len = s.len() };")
    lines.append("            }")
    lines.append("            s.as_ptr()")
    lines.append("        }")
    lines.append("        None => {")
    lines.append(RUST_OUT_LEN_GUARD)
    lines.append("                unsafe { *out_len = 0 };")
    lines.append("            }")
    lines.append("            std::ptr::null()")
    lines.append("        }")
    lines.append("    }")
    lines.append("}")
    lines.append("")

    # markdown_reason_code_metric_key
    lines.append("/// Get the Prometheus metric key for a reason code by its numeric value.")
    lines.append("///")
    lines.append("/// Returns a pointer to a static string and writes the length to `out_len`.")
    lines.append("/// Returns NULL if the discriminant is invalid.")
    lines.append("///")
    lines.append("/// # Safety")
    lines.append("///")
    lines.append("/// The caller must ensure that `out_len` either is NULL or points to")
    lines.append("/// writable storage for a `usize`.")
    lines.append(RUST_NO_MANGLE)
    lines.append("pub unsafe extern \"C\" fn markdown_reason_code_metric_key(")
    lines.append("    code: u32,")
    lines.append("    out_len: *mut usize,")
    lines.append(") -> *const u8 {")
    lines.append("    match ReasonCode::from_discriminant(code) {")
    lines.append("        Some(rc) => {")
    lines.append("            let s = rc.metric_key();")
    lines.append(RUST_OUT_LEN_GUARD)
    lines.append("                unsafe { *out_len = s.len() };")
    lines.append("            }")
    lines.append("            s.as_ptr()")
    lines.append("        }")
    lines.append("        None => {")
    lines.append(RUST_OUT_LEN_GUARD)
    lines.append("                unsafe { *out_len = 0 };")
    lines.append("            }")
    lines.append("            std::ptr::null()")
    lines.append("        }")
    lines.append("    }")
    lines.append("}")
    lines.append("")

    # markdown_reason_code_count
    lines.append("/// Return the total number of defined reason codes.")
    lines.append("///")
    lines.append("/// C callers can use this to verify they handle all variants.")
    lines.append(RUST_NO_MANGLE)
    lines.append("pub extern \"C\" fn markdown_reason_code_count() -> u32 {")
    lines.append("    REASON_CODE_COUNT as u32")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


def generate_rust_tests() -> str:
    """Generate the test module for the Rust file."""
    lines = []
    lines.append("#[cfg(test)]")
    lines.append("mod tests {")
    lines.append("    use super::*;")
    lines.append("    use std::collections::HashSet;")
    lines.append("")
    lines.append("    /// Verify that ALL array length matches REASON_CODE_COUNT.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_all_array_length_matches_count() {")
    lines.append("        assert_eq!(")
    lines.append("            ALL.len(),")
    lines.append("            REASON_CODE_COUNT,")
    lines.append('            "ALL array length ({}) must equal REASON_CODE_COUNT ({})",')
    lines.append("            ALL.len(),")
    lines.append("            REASON_CODE_COUNT")
    lines.append("        );")
    lines.append("    }")
    lines.append("")
    lines.append("    /// Verify that every variant in ALL has a unique discriminant.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_discriminants_unique() {")
    lines.append("        let mut seen = HashSet::new();")
    lines.append(RUST_ALL_LOOP)
    lines.append("            let d = rc.discriminant();")
    lines.append('            assert!(seen.insert(d), "Duplicate discriminant {} for {:?}", d, rc);')
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    /// Verify that every variant in ALL has a unique string representation.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_strings_unique() {")
    lines.append("        let mut seen = HashSet::new();")
    lines.append(RUST_ALL_LOOP)
    lines.append(RUST_REASON_AS_STR)
    lines.append('            assert!(seen.insert(s), "Duplicate string \'{}\' for {:?}", s, rc);')
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    /// Verify that all string representations are lowercase snake_case.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_strings_are_lowercase_snake_case() {")
    lines.append('        let re = regex::Regex::new(r"^[a-z][a-z0-9_]*$").unwrap();')
    lines.append(RUST_ALL_LOOP)
    lines.append(RUST_REASON_AS_STR)
    lines.append('            assert!(!s.is_empty(), "{:?} has empty string", rc);')
    lines.append("            assert!(")
    lines.append("                re.is_match(s),")
    lines.append('                "String \'{}\' for {:?} does not match lowercase snake_case pattern",')
    lines.append("                s,")
    lines.append("                rc")
    lines.append("            );")
    lines.append("        }")
    lines.append("    }")
    lines.append("")

    return "\n".join(lines)


def generate_rust_tests_continued() -> str:
    """Generate remaining test functions."""
    lines = []
    lines.append("    /// Verify that exactly 5 unified metric families are used.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_metric_keys_unified_families() {")
    lines.append("        let mut families: HashSet<&str> = HashSet::new();")
    lines.append(RUST_ALL_LOOP)
    lines.append("            families.insert(rc.metric_key());")
    lines.append("        }")
    lines.append("        assert_eq!(")
    lines.append("            families.len(),")
    lines.append("            5,")
    lines.append('            "Expected exactly 5 unified metric families, got {:?}",')
    lines.append("            families")
    lines.append("        );")
    lines.append("    }")
    lines.append("")
    lines.append("    /// Verify round-trip: discriminant -> from_discriminant -> same variant.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_from_discriminant_roundtrip() {")
    lines.append(RUST_ALL_LOOP)
    lines.append("            let d = rc.discriminant();")
    lines.append("            let recovered = ReasonCode::from_discriminant(d);")
    lines.append("            assert_eq!(recovered, Some(*rc));")
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    /// Verify that from_discriminant returns None for invalid values.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_from_discriminant_invalid() {")
    lines.append("        assert_eq!(ReasonCode::from_discriminant(255), None);")
    lines.append("        assert_eq!(ReasonCode::from_discriminant(u32::MAX), None);")
    lines.append("    }")
    lines.append("")
    lines.append("    /// Closure test: verify discriminant range is contiguous 0..COUNT-1.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_discriminant_range_contiguous() {")
    lines.append("        let mut discriminants: Vec<u32> = ALL.iter().map(|rc| rc.discriminant()).collect();")
    lines.append("        discriminants.sort();")
    lines.append("        for (i, d) in discriminants.iter().enumerate() {")
    lines.append('            assert_eq!(*d, i as u32, "Expected discriminant {} at index {}", i, i);')
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    /// FFI function test: markdown_reason_code_str returns correct data.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_ffi_reason_code_str() {")
    lines.append(RUST_ALL_LOOP)
    lines.append("            let mut len: usize = 0;")
    lines.append("            let ptr = unsafe { markdown_reason_code_str(rc.discriminant(), &mut len) };")
    lines.append('            assert!(!ptr.is_null(), "NULL returned for {:?}", rc);')
    lines.append("            assert_eq!(len, rc.as_str().len());")
    lines.append("            let slice = unsafe { std::slice::from_raw_parts(ptr, len) };")
    lines.append("            let s = std::str::from_utf8(slice).unwrap();")
    lines.append("            assert_eq!(s, rc.as_str());")
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    /// FFI function test: markdown_reason_code_count returns correct value.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_ffi_reason_code_count() {")
    lines.append("        assert_eq!(markdown_reason_code_count(), REASON_CODE_COUNT as u32);")
    lines.append("    }")
    lines.append("")
    lines.append("    /// Verify the enum size is suitable for FFI (repr(u8) single-byte).")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_enum_size_for_ffi() {")
    lines.append("        assert_eq!(std::mem::size_of::<ReasonCode>(), 1);")
    lines.append("        assert_eq!(std::mem::align_of::<ReasonCode>(), 1);")
    lines.append("    }")
    lines.append("")
    lines.append("    /// Verify that every variant has a non-empty log_callsite().")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_log_callsite_non_empty() {")
    lines.append(RUST_ALL_LOOP)
    lines.append('            assert!(!rc.log_callsite().is_empty(), "{:?} has empty log_callsite", rc);')
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    /// Verify that log_callsite() descriptions indicate a valid filter phase.")
    lines.append(RUST_TEST_ATTRIBUTE)
    lines.append("    fn test_log_callsite_has_valid_phase() {")
    lines.append(RUST_ALL_LOOP)
    lines.append("            let callsite = rc.log_callsite();")
    lines.append("            assert!(")
    lines.append('                callsite.starts_with("header_filter:") || callsite.starts_with("body_filter:"),')
    lines.append('                "{:?} log_callsite must start with header_filter: or body_filter:", rc')
    lines.append("            );")
    lines.append("        }")
    lines.append("    }")
    lines.append("}")
    lines.append("")

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
    parts.append(generate_rust(reasons, hash_hex))
    parts.append(generate_rust_enum(reasons))
    parts.append(generate_rust_test_module())
    parts.append(generate_rust_all_array(reasons))
    parts.append(generate_rust_impl(reasons))
    parts.append(generate_rust_impl_continued(reasons))
    parts.append(generate_rust_ffi())
    parts.append(generate_rust_tests())
    parts.append(generate_rust_tests_continued())
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
    aliases = [
        (alias, reason["discriminant"])
        for reason in reasons
        for alias in reason.get("legacy_keys", [])
    ]
    lines = [
        "/*",
        " * Generated by tools/reason-codegen/generate.py — DO NOT EDIT.",
        f" * Source: components/rust-converter/reason_registry.toml (SHA-256: {hash_hex[:16]}...)",
        " *",
        " * This file provides generated reason metadata and stable discriminants",
        " * consumed by the C diagnostics renderer and reason accessors. The TOML",
        " * registry remains the only source; this header replaces former",
        " * hand-maintained diagnostics reason and compatibility tables.",
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
        "/*",
        " * Legacy uppercase aliases retained for diagnostics compatibility.",
        " * This table is generated from each reason's legacy_keys field.",
        " */",
        "typedef struct {",
        "    const char    *key;",
        "    ngx_int_t      code;",
        "} markdown_reason_alias_t;",
        "",
        f"#define MARKDOWN_REASON_ALIAS_COUNT {len(aliases)}",
        "",
        "#ifdef MARKDOWN_REASON_META_DEFINE",
        "const markdown_reason_alias_t",
        "    markdown_reason_aliases[MARKDOWN_REASON_ALIAS_COUNT > 0 ? MARKDOWN_REASON_ALIAS_COUNT : 1] = {",
    ])

    if aliases:
        for alias, code in aliases:
            lines.append(f'    {{ "{alias}", {code} }},')
    else:
        # A C99/C11 array of size 1 needs one initializer element; emit a
        # zero-initialized sentinel for the reserved element.
        lines.append('    { "", 0 },')

    lines.extend([
        "};",
        "#else",
        "extern const markdown_reason_alias_t",
        (
            "    markdown_reason_aliases[MARKDOWN_REASON_ALIAS_COUNT > 0 ? "
            + "MARKDOWN_REASON_ALIAS_COUNT : 1];"
        ),
        "#endif",
        "",
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
    all_ok = all(check_drift(path, content) for path, content in outputs)
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
