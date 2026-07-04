#!/usr/bin/env python3
"""Verify that no stale 0.8.0 directives/symbols have leaked into 0.9.0.
This gate is designed to stop the 'forgot to update directive' pattern.
"""
import sys
import subprocess
from pathlib import Path

STALE_SYMBOLS = [
    "markdown_on_wildcard",
    "markdown_max_size",
    "markdown_timeout",
    "markdown_memory_budget",
    "markdown_streaming_budget",
]

def _find_repo_root(start: Path) -> Path:
    """Determine the repository root by walking up the directory tree."""
    for candidate in (start, *start.parents):
        if (candidate / ".git").is_dir():
            return candidate
    for candidate in start.parents:
        if candidate.name == "tools":
            return candidate.parent
    raise RuntimeError(f"Unable to determine repository root starting from {start}")

def main():
    repo = _find_repo_root(Path(__file__).resolve())
    findings = []
    errors = []
    
    # Use git ls-files to limit scope to tracked sources and avoid noise
    try:
        files_proc = subprocess.run(
            ["git", "ls-files"],
            cwd=repo, capture_output=True, text=True, check=True
        )
        tracked_files = files_proc.stdout.splitlines()
    except Exception as e:
        print(f"Error listing tracked files: {e}", file=sys.stderr)
        sys.exit(1)

    # Exclude paths for explanation purposes
    exclude_paths = ["MIGRATION-0.9.md", "docs/harness/rules/"]
    
    for symbol in STALE_SYMBOLS:
        # ponytail: pure-python search over tracked files to avoid grep dependency/noise
        for f_rel in tracked_files:
            if any(excl in f_rel for excl in exclude_paths):
                continue
            
            f_path = repo / f_rel
            try:
                # Only read text files
                if f_path.is_file():
                    content = f_path.read_text(errors='ignore')
                    if symbol in content:
                        # Simple line extraction for reporting
                        for i, line in enumerate(content.splitlines(), 1):
                            if symbol in line:
                                findings.append(f"{f_rel}:{i}:{line.strip()}")
            except Exception as e:
                errors.append(f"Error reading {f_rel}: {e}")
            
    if errors:
        for err in errors:
            print(err, file=sys.stderr)
        sys.exit(1)

    if findings:
        print("STALE SYMBOLS DETECTED:")
        for f in findings:
            print(f)
        sys.exit(1)
    
    print("No stale 0.8 symbols found.")
    sys.exit(0)

if __name__ == "__main__":
    main()
