#!/usr/bin/env python3
"""Dead FFI export detection and classification.

Parses the generated FFI header exports AND C callsites (accounting for macros,
function pointers, and conditional compilation) to classify each export as:

- production-called: called from production C source
- test-only: referenced only in test stubs/mocks
- loader/abi: ABI handshake or module lifecycle functions
- dead: exported but unreferenced in production or lifecycle

This tool is complementary to detect_public_surface_drift.py but is an
independent implementation: it defines its own source/header paths and
parsing logic rather than importing the drift tooling.

The inventory defines the interface and classification structure. The
inventory reports candidates; removal is decided per release review.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from lib.path_validation import validate_read_path, validate_write_path_within_root  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

MODULE_SRC = ROOT / "components" / "nginx-module" / "src"
MODULE_TESTS = ROOT / "components" / "nginx-module" / "tests"
FFI_HEADER = MODULE_SRC / "markdown_converter.h"

# Loader/ABI functions that are part of the module handshake lifecycle.
# These are never dead even if they only appear in the lifecycle init path.
LOADER_ABI_FUNCTIONS = frozenset({
    "markdown_abi_version",
    "markdown_abi_header_hash",
    "markdown_abi_symbol_set_hash",
    "markdown_abi_layout_fingerprint",
})

# Related function pairs used for production-call inference.
#
# If a paired counterpart is production-called, the other side is also
# considered production-called even when it has no direct callsite in
# the scanned sources.  These are not strictly init/free pairs; they
# include functional counterparts such as init→convert, parse→free,
# and action-policy→handler mappings.
LIFECYCLE_PAIRS = {
    "markdown_converter_new": "markdown_converter_free",
    "markdown_converter_free": "markdown_converter_new",
    "markdown_result_init": "markdown_result_free",
    "markdown_result_free": "markdown_result_init",
    "markdown_options_init": "markdown_convert",
    "markdown_header_plan_init": "markdown_header_plan_free",
    "markdown_header_plan_free": "markdown_header_plan_init",
    "markdown_decomp_result_init": "markdown_decompress_bounded",
    "markdown_decompress_free": "markdown_decompress_bounded",
    "markdown_dynconf_result_init": "markdown_dynconf_parse",
    "markdown_dynconf_result_free": "markdown_dynconf_parse",
    "markdown_trusted_proxies_new": "markdown_trusted_proxies_push",
    "markdown_trusted_proxies_free": "markdown_trusted_proxies_push",
    "markdown_streaming_output_free": "markdown_streaming_feed",
}

# Pattern for extern "C" function declarations in the generated header.
C_PROTOTYPE_RE = re.compile(
    r"^(?:extern\s+)?(?:const\s+)?"
    r"(?:struct\s+\w+\s+\*?|void|u?int(?:8|16|32|64)_t|"
    r"uintptr_t|bool)\s*\*?\s*"
    r"(markdown_\w+)\s*\(",
    re.MULTILINE,
)

# Pattern to match function calls or function pointer assignments in C code.
CALLSITE_RE = re.compile(r"\b(markdown_\w+)\s*\(")

# C declarations/definitions are references, not production callsites. Keep
# this deliberately line-anchored so a call embedded in an expression is not
# filtered out. The generated header and project wrapper headers both use
# these return-type spellings.
DECLARATION_LINE_RE = re.compile(
    r"^\s*(?:(?:static|inline|extern|const|struct\s+\w+|"
    r"void|u?int(?:8|16|32|64)_t|uintptr_t|bool)\s+)+"
    r"(?:\w+\s+\*\s*)?markdown_\w+\s*\("
)

IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")
HEADER_CONTROL_KEYWORDS = frozenset(
    {"if", "for", "while", "switch", "return"}
)

# Function pointer pattern: assigned to a variable or struct field.
FN_POINTER_RE = re.compile(r"\b(markdown_\w+)\b")

# Conditional compilation guard patterns.  A leading ``!`` records an
# ``#ifndef``/else branch so callers can distinguish the actual compilation
# branch instead of treating every guard as a positive feature requirement.
# The expression capture is a single line character class rather than a lazy
# dot paired with trailing whitespace, keeping scans linear for long guards.
IFDEF_RE = re.compile(
    r"^[ \t]*#[ \t]*(if|ifdef|ifndef)[ \t]+([^\r\n]*)$"
)
ELSE_RE = re.compile(r"^\s*#\s*else\b", re.MULTILINE)
ELIF_RE = re.compile(r"^[ \t]*#[ \t]*elif[ \t]+([^\r\n]*)$")
ENDIF_RE = re.compile(r"^\s*#\s*endif\b", re.MULTILINE)

# Guards that are header-include guards and should not be tracked as feature
# guards for the purpose of conditional compilation classification.
HEADER_INCLUDE_GUARDS = frozenset({
    "NGINX_MARKDOWN_CONVERTER_H",
    "NGX_HTTP_MARKDOWN_FILTER_MODULE_H",
})


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def read_text(path: Path) -> str:
    """Read a file with path validation."""
    validated = validate_read_path(str(path), purpose="FFI dead export audit")
    return validated.read_text(encoding="utf-8")


def _header_declaration_name(line: str) -> str | None:
    """Return a function name from a header declaration line, if present."""
    stripped = line.strip()
    if not stripped or stripped.split(None, 1)[0] in HEADER_CONTROL_KEYWORDS:
        return None
    prefix, separator, _ = stripped.partition("(")
    if not separator:
        return None
    tokens = prefix.split()
    if len(tokens) < 2:
        return None
    name = tokens[-1]
    if not name.startswith("markdown_") or IDENTIFIER_RE.fullmatch(name) is None:
        return None
    if any(token != "*" and IDENTIFIER_RE.fullmatch(token) is None
           for token in tokens[:-1]):
        return None
    return name


def parse_header_exports(header_path: Path) -> list[str]:
    """Extract all markdown_* function names declared in the generated header."""
    text = read_text(header_path)
    return sorted(set(_prototype_exports(text) + _line_exports(text)))


def _prototype_exports(text: str) -> list[str]:
    """Extract declarations matched by the generated-header prototype regex."""
    return [match.group(1) for match in C_PROTOTYPE_RE.finditer(text)]


def _line_exports(text: str) -> list[str]:
    """Extract declarations that need the line-oriented fallback parser."""
    exports: list[str] = []
    for line in text.splitlines():
        name = _line_export_name(line)
        if name is not None:
            exports.append(name)
    return exports


def _line_export_name(line: str) -> str | None:
    """Return an export name from one non-comment declaration line."""
    # Also catch return types missed by the narrow prototype expression, but
    # only on declaration lines. Documentation comments contain many examples
    # such as `markdown_convert(...)` and must never become exports.
    if line.lstrip().startswith(("/*", "*", "//")):
        return None
    if DECLARATION_LINE_RE.match(line):
        match = CALLSITE_RE.search(line)
        return match.group(1) if match else None
    return _header_declaration_name(line)


def scan_c_callsites(
    directory: Path,
    include_headers: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Scan C source files for callsites of markdown_* functions.

    Returns a mapping from function name to a list of callsite records:
    {
        "file": relative path,
        "line": line number,
        "context": surrounding text snippet,
        "ifdef_guard": conditional compilation guard or None,
    }
    """
    callsites: dict[str, list[dict[str, Any]]] = {}
    validated_directory = validate_read_path(
        directory, purpose="FFI callsite source directory"
    )
    suffixes = {".c", ".h"} if include_headers else {".c"}

    for path in sorted(validated_directory.rglob("*")):
        if path.suffix in suffixes and path.name != "markdown_converter.h":
            _scan_c_call_file(path, callsites)

    return callsites


def _scan_c_call_file(
    path: Path, callsites: dict[str, list[dict[str, Any]]]
) -> None:
    """Scan one C-family source file and append its callsites."""
    text = _read_text(path)
    if text is None:
        return

    active_guards: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        _update_guard_stack(line, active_guards)
        if _is_non_callsite_line(line):
            continue
        for match in CALLSITE_RE.finditer(line):
            _record_callsite(callsites, match, path, lineno, line, active_guards)


def _is_non_callsite_line(line: str) -> bool:
    """Return whether a source line is a comment or declaration."""
    stripped = line.lstrip()
    return stripped.startswith(("/*", "*", "//")) or bool(
        DECLARATION_LINE_RE.match(line)
    )


def _read_text(path: Path) -> str | None:
    """Read a text file, returning None when unreadable."""
    try:
        validated_path = validate_read_path(path, purpose="FFI callsite source")
        return validated_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _update_guard_stack(line: str, active_guards: list[str]) -> None:
    """Track conditional compilation state for one source line."""
    if ENDIF_RE.match(line):
        if active_guards:
            active_guards.pop()
        return

    elif_match = ELIF_RE.match(line)
    if elif_match:
        if active_guards:
            active_guards[-1] = _guard_token(elif_match.group(1))
        return

    if ELSE_RE.match(line):
        if active_guards:
            current = active_guards[-1]
            active_guards[-1] = (
                current[1:] if current.startswith("!") else f"!{current}"
            )
        return

    ifdef_match = IFDEF_RE.match(line)
    if ifdef_match:
        directive, expression = ifdef_match.groups()
        active_guards.append(
            _guard_token(expression, negate=directive == "ifndef")
        )


def _guard_token(expression: str, negate: bool = False) -> str:
    """Return a compact, polarity-preserving label for a preprocessor guard."""
    stripped = expression.strip()
    match = None
    if stripped.startswith("defined"):
        match = IDENTIFIER_RE.match(
            stripped[len("defined") :].lstrip(" (")
        )
    if match:
        name = match.group(0)
    else:
        token = re.match(r"[A-Za-z0-9_]+", stripped)
        name = token.group(0) if token else ""
    if not name:
        name = expression.strip().split()[0] if expression.strip() else "unknown"
    return f"!{name}" if negate else name


def _record_callsite(
    callsites: dict[str, list[dict[str, Any]]],
    match: re.Match[str],
    path: Path,
    lineno: int,
    line: str,
    active_guards: list[str],
) -> None:
    """Append one callsite record for a regex match."""
    name = match.group(1)
    record = {
        "file": str(path.relative_to(ROOT)),
        "line": lineno,
        "context": line.strip()[:120],
        "ifdef_guard": active_guards[-1] if active_guards else None,
    }
    callsites.setdefault(name, []).append(record)


def scan_test_references(test_directory: Path) -> set[str]:
    """Scan test files for references to markdown_* functions (stubs/mocks)."""
    references: set[str] = set()
    validated_directory = validate_read_path(
        test_directory, purpose="FFI test reference directory"
    )
    for path in sorted(validated_directory.rglob("*")):
        if path.suffix not in (".c", ".h"):
            continue
        try:
            validated_path = validate_read_path(
                path, purpose="FFI test reference source"
            )
            text = validated_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in FN_POINTER_RE.finditer(text):
            name = match.group(1)
            if name.startswith("markdown_"):
                references.add(name)
    return references


def classify_exports(
    exports: list[str],
    production_callsites: dict[str, list[dict[str, Any]]],
    test_references: set[str],
) -> list[dict[str, Any]]:
    """Classify each export into one of four categories.

    Categories:
    - loader_abi: ABI handshake and module lifecycle
    - production_called: called from production C source
    - test_only: referenced only in test stubs/mocks, not production
    - dead: no production or test references (candidate for removal)

    Returns a list of classification records.
    """
    results: list[dict[str, Any]] = []

    for name in exports:
        classification, callsite_count, callsite_files = _classify_one(
            name, production_callsites, test_references
        )
        record: dict[str, Any] = {
            "name": name,
            "classification": classification,
            "production_callsite_count": callsite_count,
            "callsite_files": callsite_files,
            "in_test_stubs": name in test_references,
            "conditional_guards": sorted(
                {
                    cs["ifdef_guard"]
                    for cs in production_callsites.get(name, [])
                    if cs.get("ifdef_guard")
                    and cs["ifdef_guard"] not in HEADER_INCLUDE_GUARDS
                }
            ),
        }
        results.append(record)

    return results


def _classify_one(
    name: str,
    production_callsites: dict[str, list[dict[str, Any]]],
    test_references: set[str],
) -> tuple[str, int, list[str]]:
    """Classify a single export name into (classification, count, files)."""
    if name in LOADER_ABI_FUNCTIONS:
        return _callsite_classification("loader_abi", production_callsites.get(name, []))

    if name in production_callsites and production_callsites[name]:
        return _callsite_classification(
            "production_called", production_callsites[name]
        )

    if name in test_references:
        return "test_only", 0, []

    # Lifecycle pair: if the paired counterpart is production-called, infer
    # this function is also production-called and reuse the paired evidence.
    paired = LIFECYCLE_PAIRS.get(name)
    paired_callsites = production_callsites.get(paired, []) if paired else []
    if paired and paired_callsites:
        return _callsite_classification("production_called", paired_callsites)

    return "dead", 0, []


def _callsite_classification(
    classification: str,
    callsites: list[dict[str, Any]],
) -> tuple[str, int, list[str]]:
    """Build the classification triple from a callsite list."""
    count = len(callsites)
    files = sorted({cs["file"] for cs in callsites})
    return classification, count, files


def build_inventory(
    classifications: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the export/callsite inventory structure.

    This is the interface definition. The post-change audit will:
    1. Consume this inventory structure
    2. Perform final classification after all FFI changes are complete
    3. Remove confirmed dead exports
    4. Regenerate the header
    """
    summary = {
        "production_called": 0,
        "loader_abi": 0,
        "test_only": 0,
        "dead": 0,
    }
    for entry in classifications:
        summary[entry["classification"]] += 1

    return {
        "schema_version": "0.9.2-ffi-inventory",
        "description": (
            "FFI export/callsite inventory for dead-export detection. "
            "Final classification and removal happen after all associated "
            "FFI changes are complete."
        ),
        "audit_tool": "tools/harness/detect_ffi_dead_exports.py",
        "generated_header": str(
            FFI_HEADER.relative_to(ROOT)
        ),
        "source_directory": str(MODULE_SRC.relative_to(ROOT)),
        "test_directory": str(MODULE_TESTS.relative_to(ROOT)),
        "classification_categories": {
            "production_called": (
                "Called from production C source code"
            ),
            "loader_abi": (
                "ABI handshake or module lifecycle (never dead)"
            ),
            "test_only": (
                "Referenced only in test stubs/mocks, not production"
            ),
            "dead": (
                "No production or test-only references; candidate for removal"
            ),
        },
        "deferred_to": "post-change audit",
        "deferred_reason": (
            "Final classification and removal are deferred until encoding, "
            "trusted-proxy, token, and ownership FFI changes are complete"
        ),
        "summary": summary,
        "total_exports": len(classifications),
        "exports": classifications,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_audit() -> dict[str, Any]:
    """Execute the full dead-export audit and return the inventory."""
    if not FFI_HEADER.exists():
        raise FileNotFoundError(
            "Generated FFI header not found: {}".format(FFI_HEADER)
        )

    exports = parse_header_exports(FFI_HEADER)
    production_callsites = scan_c_callsites(MODULE_SRC)
    test_references = scan_test_references(MODULE_TESTS)
    classifications = classify_exports(exports, production_callsites, test_references)
    return build_inventory(classifications)


def main() -> int:
    """Run the dead-export audit and optionally write the inventory artifact."""
    parser = argparse.ArgumentParser(
        description="Detect dead FFI exports by parsing header and C callsites"
    )
    parser.add_argument(
        "--output", "-o",
        help="Write inventory JSON to this path (default: stdout)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit nonzero when dead exports are found; suitable for CI gates",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print only the summary counts",
    )
    args = parser.parse_args()

    try:
        inventory = run_audit()
    except (OSError, ValueError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1

    if args.summary:
        summary = inventory["summary"]
        print("FFI Export Classification Summary:")
        print("  production_called: {}".format(summary["production_called"]))
        print("  loader_abi:        {}".format(summary["loader_abi"]))
        print("  test_only:         {}".format(summary["test_only"]))
        print("  dead:              {}".format(summary["dead"]))
        print("  total:             {}".format(inventory["total_exports"]))
    else:
        output = json.dumps(inventory, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            validated_output = validate_write_path_within_root(
                args.output, ROOT, purpose="FFI dead export inventory"
            )
            validated_output.parent.mkdir(parents=True, exist_ok=True)
            validated_output.write_text(output, encoding="utf-8")
            print(
                "Wrote inventory to {}".format(validated_output), file=sys.stderr
            )
        else:
            print(output)

    if args.check and inventory["summary"]["dead"] > 0:
        dead_names = [
            e["name"] for e in inventory["exports"]
            if e["classification"] == "dead"
        ]
        print(
            "FAILURE: {} dead export(s) found: {}".format(
                len(dead_names), ", ".join(dead_names)
            ),
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
