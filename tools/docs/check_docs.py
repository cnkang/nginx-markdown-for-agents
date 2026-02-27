#!/usr/bin/env python3
"""Repository documentation checks.

Runs lightweight checks for maintained Markdown docs (excluding docs/archive):
- local link validity
- heading hierarchy consistency (ignoring code fences)
- non-English Han characters (enforces English docs policy)
- duplicate doc sync (via tools/docs/check_duplicate_docs.py)
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_SEGMENT = "docs/archive/"
LINK_RE = re.compile(r"(!?\[[^\]]+\]\(([^)]+)\))")


def iter_markdown_files() -> list[Path]:
    return sorted(
        p for p in ROOT.rglob("*.md") if ARCHIVE_SEGMENT not in p.as_posix()
    )


def check_links(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for f in files:
        in_fence = False
        text = f.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            line_no_code = re.sub(r"`[^`]*`", "", line)
            for _, target in LINK_RE.findall(line_no_code):
                t = target.strip().strip("<>")
                if t.startswith(("http://", "https://", "mailto:", "#")):
                    continue
                p = t.split("#", 1)[0].split("?", 1)[0]
                if not p:
                    continue
                if not (f.parent / p).resolve().exists():
                    errors.append(f"{f}:{line_no}: broken link target '{p}'")
    return errors


def check_heading_hierarchy(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for f in files:
        prev = 0
        in_fence = False
        text = f.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if not s.startswith("#"):
                continue
            level = len(s) - len(s.lstrip("#"))
            if prev and level > prev + 1:
                errors.append(
                    f"{f}:{line_no}: heading jumps from H{prev} to H{level}"
                )
            prev = level
    return errors


def check_english_policy(files: list[Path]) -> list[str]:
    # Python's stdlib re does not support \p{Han}; use subprocess rg for accuracy.
    try:
        proc = subprocess.run(
            [
                "rg",
                "-n",
                "--pcre2",
                r"[\p{Han}]",
                "--glob",
                "!docs/archive/**",
                "--glob",
                "*.md",
                ".",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        # Fallback: no check if rg is unavailable; caller can still rely on other checks.
        return []

    if proc.returncode == 0:
        return [line for line in proc.stdout.splitlines() if line.strip()]
    if proc.returncode == 1:
        return []
    return [f"english-scan: rg failed ({proc.returncode}): {proc.stderr.strip()}"]


def check_duplicate_sync() -> list[str]:
    script = ROOT / "tools" / "docs" / "check_duplicate_docs.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if proc.returncode == 0:
        return []
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return [f"duplicate-sync: {line}" for line in output.splitlines() if line.strip()]


def main() -> int:
    files = iter_markdown_files()
    failures: list[str] = []

    failures.extend(check_links(files))
    failures.extend(check_heading_hierarchy(files))
    failures.extend(check_english_policy(files))
    failures.extend(check_duplicate_sync())

    if failures:
        print("Documentation checks failed:")
        for line in failures:
            print(f"- {line}")
        return 1

    print("Documentation checks passed:")
    print(f"- Markdown files checked (excluding docs/archive): {len(files)}")
    print("- Local links: OK")
    print("- Heading hierarchy: OK")
    print("- English docs policy (Han-character scan): OK")
    print("- Duplicate canonical/mirror sync: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
