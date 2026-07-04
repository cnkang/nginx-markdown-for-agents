#!/usr/bin/env python3
"""Verify that no stale 0.8.0 directives/symbols have leaked into 0.9.0.
This gate is designed to stop the 'forgot to update directive' pattern.
"""
import sys
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

STALE_SYMBOLS = [
    "markdown_on_wildcard",
    "markdown_max_size",
    "markdown_timeout",
    "markdown_memory_budget",
    "markdown_streaming_budget",
]

SCAN_PATH_PREFIXES = (
    ".github/workflows/",
    "docs/harness/",
    "docs/release/",
    "examples/production/",
)

GIT_TIMEOUT_SECONDS = 15


def _find_repo_root(start: Path) -> Path:
    """Determine the repository root by walking up the directory tree."""
    for candidate in (start, *start.parents):
        if (candidate / ".git").is_dir():
            return candidate
    for candidate in start.parents:
        if candidate.name == "tools":
            return candidate.parent
    raise RuntimeError(f"Unable to determine repository root starting from {start}")


def run_stale_symbol_check(repo: Optional[Path] = None) -> tuple[int, str, str]:
    """Return exit code, stdout, and stderr for the stale-symbol gate."""
    if repo is None:
        repo = _find_repo_root(Path(__file__).resolve())
    findings = []
    errors = []

    # Use git ls-files to limit scope to tracked sources and avoid noise
    git_bin = shutil.which("git")
    if git_bin is None:
        return 1, "", "Error listing tracked files: git executable not found"
    if not os.access(git_bin, os.X_OK):
        return 1, "", f"Error listing tracked files: git is not executable: {git_bin}"

    try:
        files_proc = subprocess.run(
            [git_bin, "ls-files"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        tracked_files = files_proc.stdout.splitlines()
    except subprocess.TimeoutExpired:
        return (
            1,
            "",
            "Error listing tracked files: git ls-files timed out "
            f"after {GIT_TIMEOUT_SECONDS}s",
        )
    except Exception as e:
        return 1, "", f"Error listing tracked files with git: {e}"

    # Pure-Python search over tracked files avoids grep dependency/noise.
    for f_rel in tracked_files:
        if not f_rel.startswith(SCAN_PATH_PREFIXES):
            continue

        f_path = repo / f_rel
        try:
            # Only read text files.
            if not f_path.is_file():
                continue
            content = f_path.read_text(encoding='utf-8')
            lines = content.splitlines()
            for symbol in STALE_SYMBOLS:
                if symbol not in content:
                    continue
                # Simple line extraction for reporting.
                for i, line in enumerate(lines, 1):
                    if symbol in line:
                        findings.append(f"{f_rel}:{i}:{line.strip()}")
        except Exception as e:
            errors.append(f"Error reading {f_rel}: {e}")

    if errors:
        return 1, "", "\n".join(errors)

    if findings:
        return 1, "STALE SYMBOLS DETECTED:\n" + "\n".join(findings), ""

    return 0, "No stale 0.8 symbols found.", ""


def main():
    exit_code, stdout, stderr = run_stale_symbol_check()
    if stdout:
        print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)
    sys.exit(exit_code)

if __name__ == "__main__":
    main()
