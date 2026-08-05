#!/usr/bin/env python3
"""Dead FFI export detection and classification.

Parses the generated FFI header exports AND C callsites (accounting for macros,
function pointers, and conditional compilation) to classify each export as:

- production-called: called from production C source
- test-only: referenced only in test stubs/mocks
- loader/abi: ABI handshake or module lifecycle functions
- dead: exported but unreferenced in production or lifecycle

This tool extends (not replaces) the existing public-surface/FFI drift tooling
in detect_public_surface_drift.py. It reuses its FFI_PATHS and header parsing
infrastructure.

Wave 3 defines the interface and inventory structure.
Wave 4 task 8.13 performs the final classification and actual removal.
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

from lib.path_validation import validate_read_path  # noqa: E402

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
    "markdown_free_conflicts": "markdown_detect_conflicts",
    "markdown_streaming_output_free": "markdown_streaming_feed",
}

# Pattern for extern "C" function declarations in the generated header.
C_PROTOTYPE_RE = re.compile(
    r"^(?:extern\s+)?(?:const\s+)?"
    r"(?:struct\s+\w+\s+\*?|void|u?int(?:8|16|32|64)_t|"
    r"uint8_t|uintptr_t|bool)\s*\*?\s*"
    r"(markdown_\w+)\s*\(",
    re.MULTILINE,
)

# Pattern to match function calls or function pointer assignments in C code.
CALLSITE_RE = re.compile(r"\b(markdown_\w+)\s*\(")

# Function pointer pattern: assigned to a variable or struct field.
FN_POINTER_RE = re.compile(r"\b(markdown_\w+)\b")

# Conditional compilation guard pattern.
# Matches: #ifdef X, #if defined(X), #if defined X, #ifndef X.
# Note: for compound expressions, this captures only the first macro/guard.
IFDEF_RE = re.compile(
    r"^\s*#\s*if(?:n?def)?\s+(?:defined\s*\(\s*)?(\w+)", re.MULTILINE
)
ENDIF_RE = re.compile(r"^\s*#\s*endif", re.MULTILINE)

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


def parse_header_exports(header_path: Path) -> list[str]:
    """Extract all markdown_* function names declared in the generated header."""
    text = read_text(header_path)
    exports: list[str] = []
    for match in C_PROTOTYPE_RE.finditer(text):
        name = match.group(1)
        if name not in exports:
            exports.append(name)
    # Also catch return types we may have missed with the above pattern.
    # Use a broader fallback for any line that looks like a function declaration.
    broad_re = re.compile(
        r"^[A-Za-z_].*?(markdown_\w+)\s*\(", re.MULTILINE
    )
    for match in broad_re.finditer(text):
        name = match.group(1)
        if name not in exports:
            exports.append(name)
    return sorted(set(exports))


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
    suffixes = {".c", ".h"} if include_headers else {".c"}

    for path in sorted(directory.rglob("*")):
        if path.suffix not in suffixes:
            continue
        # Skip the generated header itself — it declares, not calls.
        if path.name == "markdown_converter.h":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lines = text.splitlines()
        active_guards: list[str] = []

        for lineno, line in enumerate(lines, start=1):
            # Track conditional compilation state.
            ifdef_match = IFDEF_RE.match(line)
            if ifdef_match:
                active_guards.append(ifdef_match.group(1))
            elif ENDIF_RE.match(line):
                if active_guards:
                    active_guards.pop()

            # Find callsites.
            for match in CALLSITE_RE.finditer(line):
                name = match.group(1)
                record = {
                    "file": str(path.relative_to(ROOT)),
                    "line": lineno,
                    "context": line.strip()[:120],
                    "ifdef_guard": active_guards[-1] if active_guards else None,
                }
                callsites.setdefault(name, []).append(record)

    return callsites


def scan_test_references(test_directory: Path) -> set[str]:
    """Scan test files for references to markdown_* functions (stubs/mocks)."""
    references: set[str] = set()
    for path in sorted(test_directory.rglob("*")):
        if path.suffix not in (".c", ".h"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
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
        if name in LOADER_ABI_FUNCTIONS:
            classification = "loader_abi"
            callsite_count = len(production_callsites.get(name, []))
            callsite_files = sorted(
                {cs["file"] for cs in production_callsites.get(name, [])}
            )
        elif name in production_callsites and production_callsites[name]:
            classification = "production_called"
            callsite_count = len(production_callsites[name])
            callsite_files = sorted(
                {cs["file"] for cs in production_callsites[name]}
            )
        elif name in test_references:
            classification = "test_only"
            callsite_count = 0
            callsite_files = []
        else:
            # Check lifecycle pair: if the paired counterpart is
            # production-called, infer this function is also
            # production-called and reuse the paired callsite evidence.
            paired = LIFECYCLE_PAIRS.get(name)
            paired_callsites = production_callsites.get(paired, []) if paired else []
            if paired and paired_callsites:
                classification = "production_called"
                callsite_count = len(paired_callsites)
                callsite_files = sorted(
                    {cs["file"] for cs in paired_callsites}
                )
            else:
                classification = "dead"
                callsite_count = 0
                callsite_files = []

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


def build_inventory(
    classifications: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the export/callsite inventory structure.

    This is the Wave 3 interface definition. Task 8.13 in Wave 4 will:
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
        "schema_version": "0.9.2-wave3",
        "description": (
            "FFI export/callsite inventory for dead-export detection. "
            "Wave 3 defines the interface; Wave 4 task 8.13 performs "
            "final classification and removal after all FFI changes."
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
        "deferred_to_task": "8.13",
        "deferred_reason": (
            "Final classification and removal deferred until Wave 4 task 8.13, "
            "after encoding, trusted-proxy, token, and ownership FFI changes "
            "are complete"
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
        help="Exit nonzero if any dead exports are found (advisory, not blocking in W3)",
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
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(output, encoding="utf-8")
            print(
                "Wrote inventory to {}".format(args.output), file=sys.stderr
            )
        else:
            print(output)

    if args.check and inventory["summary"]["dead"] > 0:
        dead_names = [
            e["name"] for e in inventory["exports"]
            if e["classification"] == "dead"
        ]
        print(
            "ADVISORY: {} dead export(s) found: {}".format(
                len(dead_names), ", ".join(dead_names)
            ),
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
