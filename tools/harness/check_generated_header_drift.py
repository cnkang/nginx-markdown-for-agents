#!/usr/bin/env python3
"""Fail when the committed cbindgen header copies drift from the generator.

`make copy-headers` regenerates `markdown_converter.h` with the pinned
cbindgen, normalizes it, and keeps a copy in both committed locations.  A
bare `git diff` on those files only catches local edits: when both committed
copies are stale but identical, it reports nothing.  This check regenerates
the header into the gitignored `target/` workspace and compares the result
against the working-tree copies and the HEAD-committed copies, so a stale
pair cannot pass.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))  # noqa: E402  (script-mode import support)

from tools.harness.normalize_cbindgen_header import normalize_text  # noqa: E402

RUST_DIR = REPO_ROOT / "components" / "rust-converter"
RUST_HEADER_REL = "components/rust-converter/include/markdown_converter.h"
NGINX_HEADER_REL = "components/nginx-module/src/markdown_converter.h"
RUST_HEADER = REPO_ROOT / RUST_HEADER_REL
NGINX_HEADER = REPO_ROOT / NGINX_HEADER_REL
PINNED_CBINDGEN = "cbindgen 0.29.4"
TARGET_DIR = RUST_DIR / "target"
RECOVERY = "run 'make copy-headers' and commit both header copies"


def _found_version() -> str:
    """Describe the cbindgen on PATH for diagnostics."""
    cbindgen = shutil.which("cbindgen")
    if cbindgen is None:
        return "none"
    result = subprocess.run(
        [cbindgen, "--version"], capture_output=True, text=True, check=False
    )
    return (result.stdout or result.stderr).strip() or "unknown"


def _cbindgen_path() -> str | None:
    """Return the pinned cbindgen, or None when the version does not match."""
    cbindgen = shutil.which("cbindgen")
    if cbindgen is None:
        return None
    try:
        result = subprocess.run(
            [cbindgen, "--version"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    if result.returncode != 0 or result.stdout.strip() != PINNED_CBINDGEN:
        return None
    return cbindgen


def generate_header(cbindgen: str) -> str:
    """Regenerate the header into an owned workspace and return its text.

    The output lives in a per-run directory under the gitignored `target/`
    workspace, so neither the tracked copies nor any pre-existing content
    there is touched; only the directory this run created is removed.
    """
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    output_dir = Path(
        tempfile.mkdtemp(dir=TARGET_DIR, prefix="header-drift-check-")
    )
    try:
        output_path = output_dir / "markdown_converter.h"
        try:
            result = subprocess.run(
                [
                    cbindgen,
                    "--quiet",
                    "--config",
                    "cbindgen.toml",
                    "--crate",
                    "nginx-markdown-converter",
                    "--output",
                    str(output_path),
                ],
                cwd=RUST_DIR,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise RuntimeError(f"unable to run cbindgen: {exc}") from exc
        if result.returncode != 0:
            diagnostics = (result.stdout + "\n" + result.stderr).strip()
            raise RuntimeError(f"cbindgen failed: {diagnostics}")
        if not output_path.is_file():
            raise RuntimeError("cbindgen produced no output header")
        return output_path.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


def _git_show(relative: str) -> str | None:
    """Return the HEAD-committed copy, or None when it cannot be read."""
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"HEAD:{relative}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def collect_drift_errors(
    generated_text: str,
    worktree_rust: str,
    worktree_nginx: str,
    head_rust: str | None,
    head_nginx: str | None,
) -> list[str]:
    """Return one message per committed copy that drifts from the generator."""
    normalized = normalize_text(generated_text)
    errors: list[str] = []
    if worktree_rust != worktree_nginx:
        errors.append(
            "the two committed header copies differ from each other; " + RECOVERY
        )
    if normalized != worktree_rust:
        errors.append(
            "generated header differs from the working tree copy; " + RECOVERY
        )
    for label, head in ((RUST_HEADER_REL, head_rust), (NGINX_HEADER_REL, head_nginx)):
        if head is not None and head != normalized:
            errors.append(
                f"committed copy {label} differs from the freshly generated "
                "header; " + RECOVERY
            )
    return errors


def main(argv: list[str]) -> int:
    """Report any committed header copy that differs from a fresh generation."""
    _ = argv
    cbindgen = _cbindgen_path()
    if cbindgen is None:
        print(
            "ERROR: cbindgen 0.29.4 required (found: "
            f"{_found_version()}). The generated header is committed and "
            "fingerprinted; versions differ and produce a different header. "
            "Install with: cargo install cbindgen --version 0.29.4 --locked",
            file=sys.stderr,
        )
        return 1
    try:
        worktree_rust = RUST_HEADER.read_text(encoding="utf-8")
        worktree_nginx = NGINX_HEADER.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot read a committed header copy: {exc}", file=sys.stderr)
        return 1
    try:
        generated = generate_header(cbindgen)
        errors = collect_drift_errors(
            generated,
            worktree_rust,
            worktree_nginx,
            _git_show(RUST_HEADER_REL),
            _git_show(NGINX_HEADER_REL),
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("OK: committed header copies match a freshly generated header")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
