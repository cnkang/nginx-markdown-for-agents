#!/usr/bin/env python3
"""check_source_encoding.py — strict UTF-8 audit for SonarCloud source scope.

Scans all tracked files (git ls-files -z) matching a text-extension whitelist
and verifies that each file decodes as valid UTF-8.  Intentionally non-UTF-8
fixtures must be declared in ``tools/sonar/encoding_exceptions.json``; the
script validates that every exception:

  * points to an existing path,
  * cannot be decoded as UTF-8,
  * can be decoded with the declared encoding,
  * has a documented ``reason``.

The script exits non-zero when any accidental invalid encoding is found or when
an exception manifest entry is stale/invalid.

Usage:
    python3 tools/sonar/check_source_encoding.py [--include-generated]
    python3 tools/sonar/check_source_encoding.py --include-exceptions --include-generated

Exit codes:
    0 — all tracked text files are valid UTF-8 (or valid declared exceptions)
    1 — invalid UTF-8 or stale exception found
    2 — usage / manifest read error
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Extensions that SonarCloud language sensors will attempt to parse as UTF-8.
TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".c", ".h", ".rs", ".py", ".sh", ".html", ".htm", ".json", ".toml",
    ".yaml", ".yml", ".xml", ".md", ".txt", ".conf", ".properties",
})

# Sonar's configured scope contains a small number of extensionless text files.
EXPLICIT_TEXT_PATHS: frozenset[Path] = frozenset({
    Path("components/nginx-module/config"),
    Path("Makefile"),
})

# Source roots configured in .sonarcloud.properties.  The generated-file audit
# recurses these directories after build/coverage preparation.
SONAR_ROOTS: list[Path] = [
    Path("components/nginx-module/src"),
    Path("components/nginx-module/config"),
    Path("components/nginx-module/tests"),
    Path("tests"),
    Path("tools"),
]

SKIP_DIR_PARTS: frozenset[str] = frozenset({
    ".git", "target", "__pycache__", ".scannerwork", "node_modules",
    "venv", ".venv", ".pytest_cache", ".mypy_cache",
})

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST: Path = Path("tools/sonar/encoding_exceptions.json")


def _resolve_repository_path(path: Path, description: str) -> Path:
    """Resolve ``path`` and reject paths outside the repository before use."""
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"{description} must be repository-relative: {path}")
    candidate = REPO_ROOT / path
    resolved = candidate.resolve()
    if not resolved.is_relative_to(REPO_ROOT.resolve()):
        raise RuntimeError(f"{description} must remain within the repository: {path}")
    return resolved


def _run_git_ls_files() -> list[Path]:
    """Return all git-tracked file paths from the repository root."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(p) for p in result.stdout.split("\0")[:-1] if p]


def _validate_manifest_entry(rel: object, info: object) -> None:
    """Validate one manifest key and its encoding contract."""
    if not isinstance(rel, str) or not rel:
        raise RuntimeError("Exception manifest paths must be non-empty strings")
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise RuntimeError(f"Exception path must be repository-relative: {rel}")
    if not isinstance(info, dict):
        raise RuntimeError(f"Exception entry {rel} must be an object")
    for key in ("encoding", "reason"):
        value = info.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"Exception entry {rel} has invalid '{key}'")
    target = _resolve_repository_path(rel_path, f"Exception path {rel}")
    if not target.is_file():
        raise RuntimeError(f"Exception entry {rel} must name an existing regular file")


def _load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    manifest_path = _resolve_repository_path(path, "Exception manifest")
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_bytes())
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"Cannot read exception manifest {manifest_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Exception manifest {manifest_path} must be a JSON object")
    for rel, info in data.items():
        _validate_manifest_entry(rel, info)
    return data


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS or path in EXPLICIT_TEXT_PATHS


def _should_skip_path(path: Path) -> bool:
    if any(part in SKIP_DIR_PARTS for part in path.parts):
        return True
    # Brotli fixtures are binary by design.
    if path.suffix.lower() == ".br":
        return True
    return False


def _context_hex(data: bytes, offset: int, width: int = 16) -> str:
    start = max(0, offset - width)
    end = min(len(data), offset + width)
    return data[start:end].hex()


def _validate_file(
    abs_path: Path,
    manifest: dict[str, dict[str, Any]],
    failures: list[str],
    rel: str | None = None,
) -> None:
    """Validate a single file's encoding.  Appends any failures."""
    if rel is None:
        try:
            rel = str(abs_path.relative_to(REPO_ROOT))
        except ValueError:
            rel = str(abs_path)
    try:
        data = abs_path.read_bytes()
    except OSError as exc:
        failures.append(f"INVALID_UTF8 {rel} byte=-1: read error: {exc}")
        return
    if b"\0" in data:
        nul_offset = data.index(b"\0")
        failures.append(f"INVALID_UTF8 {rel} byte={nul_offset}: binary NUL byte")
        return

    exc_info = manifest.get(rel)
    if exc_info is not None:
        declared = exc_info.get("encoding", "unknown")
        try:
            data.decode(declared, errors="strict")
            return
        except UnicodeDecodeError as exc:
            failures.append(
                f"INVALID_UTF8 {rel} byte={exc.start}: "
                f"declared {declared} decode failed: {exc.reason} "
                f"context={_context_hex(data, exc.start)}"
            )
            return
        except LookupError as exc:
            failures.append(
                f"INVALID_UTF8 {rel} byte=-1: unknown declared encoding {declared}: {exc}"
            )
            return

    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        failures.append(
            f"INVALID_UTF8 {rel} byte={exc.start}: {exc.reason} "
            f"context={_context_hex(data, exc.start)}"
        )


def _audit_tracked(manifest: dict[str, dict[str, Any]]) -> tuple[int, int]:
    """Audit git-tracked text files.  Returns (exit_code, checked_count)."""
    files = _run_git_ls_files()
    text_files = [
        p for p in files
        if _is_text_file(p) and not _should_skip_path(p)
    ]
    failures: list[str] = []

    for path in text_files:
        try:
            abs_path = _resolve_repository_path(path, "Tracked path")
        except RuntimeError as exc:
            failures.append(str(exc))
            continue
        _validate_file(abs_path, manifest, failures, rel=str(path))

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1, len(text_files)
    print(f"OK: {len(text_files)} tracked text files are valid UTF-8 or listed exceptions.")
    return 0, len(text_files)


def _generated_files_under(root: Path) -> list[Path]:
    """Return auditable files below one configured Sonar root."""
    abs_root = _resolve_repository_path(root, "Sonar root")
    if not abs_root.exists():
        return []
    if abs_root.is_file():
        return [abs_root]
    files: list[Path] = []
    for path in abs_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT)
        if not _should_skip_path(rel) and _is_text_file(rel):
            files.append(path)
    return files


def _audit_files(files: list[Path], manifest: dict[str, dict[str, Any]]) -> list[str]:
    """Validate a collection of already repository-contained files."""
    failures: list[str] = []
    for path in files:
        _validate_file(path, manifest, failures)
    return failures


def _audit_generated(manifest: dict[str, dict[str, Any]]) -> tuple[int, int]:
    """Audit files under Sonar roots (includes generated files)."""
    files = [path for root in SONAR_ROOTS for path in _generated_files_under(root)]

    failures = _audit_files(files, manifest)

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1, len(files)
    print(f"OK: {len(files)} files under Sonar roots are valid UTF-8 or listed exceptions.")
    return 0, len(files)


def _audit_manifest(manifest: dict[str, dict[str, Any]]) -> list[str]:
    """Verify every exception entry is valid and points to an existing file."""
    failures: list[str] = []
    for rel, info in manifest.items():
        try:
            path = _resolve_repository_path(Path(rel), f"Exception path {rel}")
        except RuntimeError as exc:
            failures.append(str(exc))
            continue
        if not path.exists():
            failures.append(f"STALE_EXCEPTION {rel}: path does not exist")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            failures.append(f"STALE_EXCEPTION {rel}: read error: {exc}")
            continue
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            utf8_valid = False
        else:
            utf8_valid = True
        if utf8_valid:
            failures.append(
                f"STALE_EXCEPTION {rel}: file is valid UTF-8, exception is unnecessary"
            )
            continue
        declared = info.get("encoding", "unknown")
        try:
            data.decode(declared, errors="strict")
        except (UnicodeDecodeError, LookupError) as exc:
            failures.append(
                f"STALE_EXCEPTION {rel}: cannot decode with declared {declared}: {exc}"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Path to exception manifest JSON",
    )
    parser.add_argument(
        "--include-generated",
        action="store_true",
        help="Also audit files under Sonar roots after build/coverage prep",
    )
    parser.add_argument(
        "--include-exceptions",
        action="store_true",
        help="Validate exception entries (default: exceptions are trusted)",
    )
    args = parser.parse_args(argv)

    try:
        manifest = _load_manifest(args.manifest)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.include_exceptions:
        failures = _audit_manifest(manifest)
        if failures:
            print("\n".join(failures), file=sys.stderr)
            return 1

    rc, _ = _audit_tracked(manifest)
    if rc != 0:
        return rc

    if args.include_generated:
        rc, _ = _audit_generated(manifest)
    return rc


if __name__ == "__main__":
    sys.exit(main())
