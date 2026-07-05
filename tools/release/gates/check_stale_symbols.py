#!/usr/bin/env python3
"""Verify that no stale 0.8.0 directives/symbols have leaked into 0.9.0.
This gate is designed to stop the 'forgot to update directive' pattern.
"""
import shutil
import sys
import subprocess
from pathlib import Path
from typing import Optional

STALE_SYMBOLS = [
    "markdown_on_wildcard",
    "markdown_max_size",
    "markdown_timeout",
    "markdown_memory_budget",
    "markdown_streaming_budget",
    "markdown_on_error",
    "markdown_streaming_on_error",
    "markdown_etag",
    "markdown_etag_policy",
    "markdown_conditional_requests",
    "markdown_trust_forwarded_headers",
    "markdown_forwarded_headers",
    "markdown_large_body_threshold",
]

# ponytail: check naked fields in harness docs, but exclude risk-packs to avoid noise
STALE_FIELDS = {
    "docs/harness/rules/": ["memory_budget", "streaming_budget"],
}

# Paths allowed to contain stale symbols (migration docs, changelogs, ADRs)
WHITELIST_PATH_PREFIXES = (
    "docs/guides/MIGRATION-",
    "CHANGELOG.md",
    "docs/architecture/ADR/",
)

SCAN_PATH_PREFIXES = (
    ".github/workflows/",
    "docs/harness/",
    "docs/release/",
    "docs/guides/",
    "docs/operations/",
    "examples/production/",
    "charts/",
    "tools/e2e/",
    "tests/e2e/",
)
# ponytail: exclude script itself from scanning
# (implicitly handled by SCAN_PATH_PREFIXES not including tools/)

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


def _git_cmd() -> list[str]:
    """Return git command with a resolved absolute path."""
    path = shutil.which("git")
    return [path] if path else ["git"]


def _list_tracked_files(repo: Path) -> tuple[Optional[list[str]], str]:
    """Return tracked files or an error message."""
    try:
        files_proc = subprocess.run(
            _git_cmd() + ["ls-files"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
        return files_proc.stdout.splitlines(), ""
    except subprocess.TimeoutExpired:
        return None, f"Error listing tracked files: git ls-files timed out after {GIT_TIMEOUT_SECONDS}s"
    except Exception as e:
        return None, f"Error listing tracked files with git: {e}"


def _should_scan_file(path: str) -> bool:
    """Return whether a tracked path is part of the 0.9 release gate surface."""
    if not path.startswith(SCAN_PATH_PREFIXES):
        return False
    # Allow migration docs and changelogs to reference old directive names
    for wl in WHITELIST_PATH_PREFIXES:
        if path.startswith(wl):
            return False
    return True


def _find_symbol_leaks(path: str, lines: list[str], content: str) -> list[str]:
    """Return stale full-symbol findings in one file."""
    findings = []

    for symbol in STALE_SYMBOLS:
        if symbol not in content:
            continue
        for i, line in enumerate(lines, 1):
            if symbol in line:
                findings.append(f"{path}:{i}:{line.strip()}")

    return findings


def _find_field_leaks(path: str, lines: list[str], content: str) -> list[str]:
    """Return stale naked-field findings in one file."""
    findings = []

    for prefix, fields in STALE_FIELDS.items():
        if not path.startswith(prefix):
            continue
        for field in fields:
            if field not in content:
                continue
            for i, line in enumerate(lines, 1):
                if field in line:
                    findings.append(f"{path}:{i}:{line.strip()} (stale field)")

    return findings


def _scan_tracked_file(repo: Path, path: str) -> tuple[list[str], str]:
    """Return stale-symbol findings or a read error for one tracked file."""
    f_path = repo / path
    if not f_path.is_file():
        return [], ""

    try:
        content = f_path.read_text(encoding='utf-8')
    except Exception as e:
        return [], f"Error reading {path}: {e}"

    lines = content.splitlines()
    findings = _find_symbol_leaks(path, lines, content)
    findings.extend(_find_field_leaks(path, lines, content))
    return findings, ""


def _scan_tracked_files(repo: Path, tracked_files: list[str]) -> tuple[list[str], list[str]]:
    """Return all stale-symbol findings and read errors."""
    findings = []
    errors = []

    for f_rel in tracked_files:
        if not _should_scan_file(f_rel):
            continue

        file_findings, error = _scan_tracked_file(repo, f_rel)
        findings.extend(file_findings)
        if error:
            errors.append(error)

    return findings, errors


def run_stale_symbol_check(repo: Optional[Path] = None) -> tuple[int, str, str]:
    """Return exit code, stdout, and stderr for the stale-symbol gate."""
    if repo is None:
        repo = _find_repo_root(Path(__file__).resolve())

    # Use git ls-files to limit scope to tracked sources and avoid noise.
    tracked_files, error = _list_tracked_files(repo)
    if error:
        return 1, "", error

    # Pure-Python search over tracked files avoids grep dependency/noise.
    findings, errors = _scan_tracked_files(repo, tracked_files or [])

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
