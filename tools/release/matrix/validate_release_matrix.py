#!/usr/bin/env python3
"""Canonical release-matrix validation gate.

Validates that the sole repository-owned release matrix
(`docs/releases/release-matrix.json`, schema version 1) is:

1. Schema-conformant against the immutable
   `schemas/release-matrix.schema.json` (draft 2020-12).
2. Canonical after one normalization pass through
   `tools/release/matrix/normalize_matrix.py` — i.e. it does not rely on
   legacy aliases (`nginx`, `os_type`, `arch`), does not carry the legacy
   top-level `matrix` array, and contains no dropped legacy metadata keys.
3. Bound to the final frozen FFI ABI version (`MARKDOWN_ABI_VERSION` in the
   generated `components/rust-converter/include/markdown_converter.h`) and
   to the official build feature manifest digest
   (`artifacts/release/0.9.2/official-build-feature-manifest.json`) for
   every entry.

Failure semantics are fail-closed: any schema violation, alias usage,
digest/ABI mismatch, or unreadable input is an ERROR and exits 1.

Exit codes:
    0 - matrix is canonical, schema-conformant, and fully bound
    1 - any check failed
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MATRIX_PATH = REPO_ROOT / "docs" / "releases" / "release-matrix.json"
SCHEMA_PATH = REPO_ROOT / "schemas" / "release-matrix.schema.json"
NORMALIZE_PATH = (
    REPO_ROOT / "tools" / "release" / "matrix" / "normalize_matrix.py"
)
FEATURE_MANIFEST_PATH = (
    REPO_ROOT / "artifacts" / "release" / "0.9.2"
    / "official-build-feature-manifest.json"
)
ABI_HEADER_PATH = (
    REPO_ROOT / "components" / "rust-converter" / "include"
    / "markdown_converter.h"
)

LEGACY_ENTRY_KEYS = {"nginx", "os_type", "arch"}
DROPPED_LEGACY_KEYS = {
    "nginx_channel",
    "test_level",
    "support_tier",
    "release_blocking",
    "owner_workflow",
    "managed_by",
}


def _load_json(path: pathlib.Path, label: str) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(f"ERROR: {label} unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"ERROR: {label} top level must be an object")
    return raw


def feature_manifest_digest() -> str:
    """Official feature-manifest digest.

    The digest convention for official artifacts is the SHA-256
    of the canonical UTF-8 JSON serialization of the manifest content
    (sorted keys, compact separators), NOT the raw file-byte digest. The
    canonical-performance-environment, performance-baseline-approval, and
    final-ffi-freeze artifacts all bind this canonical-content digest.
    """
    if not FEATURE_MANIFEST_PATH.is_file():
        raise SystemExit(
            f"ERROR: official feature manifest missing: {FEATURE_MANIFEST_PATH}"
        )
    try:
        doc = json.loads(FEATURE_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SystemExit(
            f"ERROR: official feature manifest unreadable: {exc}"
        ) from exc
    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def frozen_abi_version() -> int:
    if not ABI_HEADER_PATH.is_file():
        raise SystemExit(f"ERROR: ABI header missing: {ABI_HEADER_PATH}")
    match = re.search(
        r"#define\s+MARKDOWN_ABI_VERSION\s+(\d+)",
        ABI_HEADER_PATH.read_text(encoding="utf-8"),
    )
    if not match:
        raise SystemExit(
            f"ERROR: MARKDOWN_ABI_VERSION not found in {ABI_HEADER_PATH}"
        )
    return int(match.group(1))


def run_normalization() -> dict:
    try:
        proc = subprocess.run(
            [sys.executable, str(NORMALIZE_PATH), str(MATRIX_PATH)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"ERROR: matrix normalization could not complete: {exc}") from exc
    if proc.returncode != 0:
        raise SystemExit(
            "ERROR: matrix normalization failed (fail closed): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    try:
        normalized = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"ERROR: matrix normalization returned invalid JSON: {exc}"
        ) from exc
    if not isinstance(normalized, dict):
        raise SystemExit("ERROR: matrix normalization returned a non-object")
    return normalized


def validate_schema(matrix: dict) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise SystemExit(
            "ERROR: jsonschema required (pip install -r requirements-dev.txt)"
        ) from exc

    schema = _load_json(SCHEMA_PATH, "release-matrix schema")
    try:
        jsonschema.validate(instance=matrix, schema=schema)
    except jsonschema.ValidationError as exc:
        raise SystemExit(
            f"ERROR: matrix schema validation failed: {exc.message}"
        ) from exc


def check_canonical_identity(matrix: dict) -> None:
    """The canonical document must not use legacy aliases at all.

    Normalization folds aliases away, so alias detection must run on the
    raw (already schema-validated) document: any legacy top-level `matrix`
    array, any legacy entry key (`nginx`, `os_type`, `arch`), or any
    dropped legacy metadata key fails closed.
    """
    errors = []
    if "matrix" in matrix:
        errors.append("top-level legacy 'matrix' array present (use 'entries')")
    for index, entry in enumerate(matrix.get("entries", [])):
        if not isinstance(entry, dict):
            errors.append(f"entry {index}: not an object")
            continue
        for alias in LEGACY_ENTRY_KEYS:
            if alias in entry:
                errors.append(f"entry {index}: legacy alias {alias!r} must not be used")
        for key in DROPPED_LEGACY_KEYS:
            if key in entry:
                errors.append(f"entry {index}: dropped legacy key {key!r} present")
    if errors:
        raise SystemExit("ERROR: matrix is not canonical:\n  " + "\n  ".join(errors))


def check_bindings(normalized: dict) -> None:
    expected_digest = feature_manifest_digest()
    expected_abi = frozen_abi_version()
    errors = []
    for index, entry in enumerate(normalized.get("entries", [])):
        digest = entry.get("feature_manifest_digest")
        abi = entry.get("abi_version")
        if digest != expected_digest:
            errors.append(
                f"entry {index}: feature_manifest_digest {digest!r} != "
                f"official {expected_digest!r}"
            )
        if abi != expected_abi:
            errors.append(
                f"entry {index}: abi_version {abi!r} != frozen ABI {expected_abi!r}"
            )
    if errors:
        raise SystemExit("ERROR: matrix binding drift:\n  " + "\n  ".join(errors))


def main() -> int:
    if not MATRIX_PATH.is_file():
        print(f"ERROR: matrix missing: {MATRIX_PATH}", file=sys.stderr)
        return 1

    matrix = _load_json(MATRIX_PATH, "release matrix")
    validate_schema(matrix)
    check_canonical_identity(matrix)
    normalized = run_normalization()
    check_bindings(normalized)

    digest = "sha256:" + hashlib.sha256(MATRIX_PATH.read_bytes()).hexdigest()
    print(f"release-matrix digest: {digest}")
    print(f"release-matrix entries: {len(normalized.get('entries', []))}")
    print(f"feature-manifest digest binding: {feature_manifest_digest()}")
    print(f"abi_version binding: {frozen_abi_version()}")
    print(
        "PASS: docs/releases/release-matrix.json is canonical, "
        "schema-conformant, and ABI/feature-bound"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
