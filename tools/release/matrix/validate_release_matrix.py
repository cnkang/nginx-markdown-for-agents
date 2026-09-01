#!/usr/bin/env python3
"""Release-contract matrix projection validation gate.

Validates that the generated release-contract projection
(`docs/releases/release-matrix.json`, schema version 1) is:

1. Schema-conformant against the immutable
   `schemas/release-matrix.schema.json` (draft 2020-12).
2. Canonical after one normalization pass through
   `tools/release/matrix/normalize_matrix.py` — i.e. it does not rely on
   legacy aliases (`nginx`, `os_type`, `arch`), does not carry the legacy
   top-level `matrix` array, and contains no dropped legacy metadata keys.
3. Bound to the final frozen FFI ABI version (`MARKDOWN_ABI_VERSION` in the
   generated `components/rust-converter/include/markdown_converter.h`) and
   to the official build feature manifest digest in the active release
   artifact directory for every entry.
4. Generated from the sole manually maintained policy matrix
   (`tools/release-matrix.json`) without projection drift.

Failure semantics are fail-closed: any schema violation, alias usage,
digest/ABI mismatch, or unreadable input is an ERROR and exits 1.

Exit codes:
    0 - matrix is canonical, schema-conformant, and fully bound
    1 - any check failed
"""

from __future__ import annotations

import hashlib
import importlib.util as _importlib_util
import json
import pathlib
import re
import subprocess
import sys

try:
    from .normalize_matrix import RELEASE_VERSION
except ImportError:
    _norm_path = pathlib.Path(__file__).resolve().parent / "normalize_matrix.py"
    if not _norm_path.is_file():
        raise SystemExit(
            f"ERROR: matrix normalizer module is missing: {_norm_path}"
        )
    _norm_spec = _importlib_util.spec_from_file_location(
        "normalize_matrix_standalone", str(_norm_path)
    )
    if _norm_spec is None or _norm_spec.loader is None:
        raise SystemExit(
            f"ERROR: unable to load matrix normalizer module: {_norm_path}"
        )
    _norm_mod = _importlib_util.module_from_spec(_norm_spec)
    try:
        _norm_spec.loader.exec_module(_norm_mod)
    except Exception as exc:
        raise SystemExit(
            f"ERROR: matrix normalizer module failed to load: {exc}"
        ) from exc
    RELEASE_VERSION = getattr(_norm_mod, "RELEASE_VERSION", None)
    if not isinstance(RELEASE_VERSION, str) or not RELEASE_VERSION:
        raise SystemExit(
            "ERROR: matrix normalizer does not export RELEASE_VERSION"
        )

try:
    from .generate_release_contract_matrix import build_projection
except ImportError:
    _projection_path = (
        pathlib.Path(__file__).resolve().parent
        / "generate_release_contract_matrix.py"
    )
    if not _projection_path.is_file():
        raise SystemExit(
            f"ERROR: release matrix projection module is missing: {_projection_path}"
        )
    _projection_spec = _importlib_util.spec_from_file_location(
        "generate_release_contract_matrix_standalone", str(_projection_path)
    )
    if _projection_spec is None or _projection_spec.loader is None:
        raise SystemExit(
            f"ERROR: unable to load release matrix projection module: {_projection_path}"
        )
    _projection_mod = _importlib_util.module_from_spec(_projection_spec)
    try:
        _projection_spec.loader.exec_module(_projection_mod)
    except Exception as exc:
        raise SystemExit(
            f"ERROR: release matrix projection module failed to load: {exc}"
        ) from exc
    build_projection = getattr(_projection_mod, "build_projection", None)
    if not callable(build_projection):
        raise SystemExit(
            "ERROR: release matrix projection module does not export "
            "build_projection"
        )

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MATRIX_PATH = REPO_ROOT / "docs" / "releases" / "release-matrix.json"
SOURCE_MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"
SCHEMA_PATH = REPO_ROOT / "schemas" / "release-matrix.schema.json"
NORMALIZE_PATH = (
    REPO_ROOT / "tools" / "release" / "matrix" / "normalize_matrix.py"
)
FEATURE_MANIFEST_PATH = (
    REPO_ROOT / "artifacts" / "release" / RELEASE_VERSION
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
    entries = matrix.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("entries collection is absent or empty")
        raise SystemExit("ERROR: matrix is not canonical:\n  " + "\n  ".join(errors))
    for index, entry in enumerate(entries):
        _check_canonical_entry(entry, index, errors)
    if errors:
        raise SystemExit("ERROR: matrix is not canonical:\n  " + "\n  ".join(errors))


def _check_canonical_entry(entry: dict, index: int, errors: list) -> None:
    """Validate one canonical matrix entry against the frozen entry shape."""
    if not isinstance(entry, dict):
        errors.append(f"entry {index}: not an object")
        return
    for alias in LEGACY_ENTRY_KEYS:
        if alias in entry:
            errors.append(f"entry {index}: legacy alias {alias!r} must not be used")
    for key in DROPPED_LEGACY_KEYS:
        if key in entry:
            errors.append(f"entry {index}: dropped legacy key {key!r} present")


def check_bindings(normalized: dict) -> None:
    expected_digest = feature_manifest_digest()
    expected_abi = frozen_abi_version()
    errors = []
    entries = normalized.get("entries")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("ERROR: normalized matrix has no entries to validate")
    for index, entry in enumerate(entries):
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


def check_source_bool_fields(source: dict) -> None:
    """Validate policy-matrix boolean metadata that normalization drops.

    ``release_blocking`` is dropped during normalization and consumed later
    through strict ``is True`` checks; a non-boolean value would therefore
    silently disable release blocking instead of failing validation.
    """
    entries = source.get("entries", [])
    if not isinstance(entries, list):
        raise SystemExit(
            "ERROR: policy matrix 'entries' must be a list, "
            f"got {type(entries).__name__}"
        )
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SystemExit(
                f"ERROR: policy matrix entry {index} must be an object, "
                f"got {type(entry).__name__}"
            )
        if "release_blocking" not in entry:
            continue  # omission is allowed; only present non-bools fail
        value = entry["release_blocking"]
        if not isinstance(value, bool):
            raise SystemExit(
                f"ERROR: policy matrix entry {index}: release_blocking must "
                f"be a boolean, got {type(value).__name__}"
            )


def check_source_projection(matrix: dict) -> None:
    """Reject a release-contract document that is not generated from policy."""
    source = _load_json(SOURCE_MATRIX_PATH, "policy release matrix")
    try:
        expected = build_projection(source)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            f"ERROR: policy matrix cannot produce a release-contract projection: {exc}"
        ) from exc
    if matrix != expected:
        raise SystemExit(
            "ERROR: release-contract projection drifted from "
            "tools/release-matrix.json; run "
            "tools/release/matrix/generate_release_contract_matrix.py --write"
        )


def main() -> int:
    if not MATRIX_PATH.is_file():
        print(f"ERROR: matrix missing: {MATRIX_PATH}", file=sys.stderr)
        return 1

    matrix = _load_json(MATRIX_PATH, "release matrix")
    validate_schema(matrix)
    check_canonical_identity(matrix)
    normalized = run_normalization()
    if matrix != normalized:
        raise SystemExit(
            "ERROR: matrix differs from its canonical normalization; "
            "remove legacy aliases and dropped metadata"
        )
    check_bindings(normalized)
    source = _load_json(SOURCE_MATRIX_PATH, "policy release matrix")
    check_source_bool_fields(source)
    check_source_projection(matrix)

    digest = "sha256:" + hashlib.sha256(MATRIX_PATH.read_bytes()).hexdigest()
    print(f"release-matrix digest: {digest}")
    print(f"release-matrix entries: {len(normalized.get('entries', []))}")
    print(f"feature-manifest digest binding: {feature_manifest_digest()}")
    print(f"abi_version binding: {frozen_abi_version()}")
    print(
        "PASS: docs/releases/release-matrix.json is a current, "
        "schema-conformant, ABI/feature-bound release-contract projection"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
