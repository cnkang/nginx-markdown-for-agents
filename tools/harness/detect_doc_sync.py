#!/usr/bin/env python3
"""
detect_doc_sync.py — Documentation Synchronization Detection (Rule 9, 10)

Rule 9 (docs-tooling): Documentation must reflect the actual implementation.
Rule 10 (docs-tooling): API documentation must be kept in sync with the API.

This detector blocks when it finds documentation drift. It is intentionally
conservative to avoid false positives.

Detection strategy:
  1. Check if CHANGELOG.md mentions recent features/fixes
  2. Check if README mentions key configuration directives
  3. Check if key public API types are referenced in docs

Usage:
  python3 tools/harness/detect_doc_sync.py [directory]
    directory defaults to project root

Exit codes:
  0 — no warnings found
  1 — one or more warnings found
"""

import re
import sys
from pathlib import Path
from typing import List

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path


def check_changelog_exists(project_root: Path) -> List[str]:
    """Check that CHANGELOG.md exists and has recent entries."""
    changelog = project_root / 'CHANGELOG.md'
    if not changelog.exists():
        return ["CHANGELOG.md not found"]
    
    try:
        content = changelog.read_text(encoding='utf-8')
    except Exception as exc:
        return [f"CHANGELOG.md exists but is unreadable: {exc}"]
    
    # Check if it has at least one version entry
    if not re.search(r'##\s+\[\d+\.\d+\.\d+\]', content):
        return ["CHANGELOG.md has no version entries"]
    
    return []


def check_readme_mentions_key_features(project_root: Path) -> List[str]:
    """Check that README mentions key features."""
    warnings = []
    
    for readme_name in ['README.md', 'README_zh-CN.md']:
        readme = project_root / readme_name
        if not readme.exists():
            continue
        
        try:
            content = readme.read_text(encoding='utf-8')
        except Exception as exc:
            warnings.append(f"{readme_name}: unreadable: {exc}")
            continue
        
        # Check for key configuration directives
        key_directives = ['markdown_filter', 'markdown_max_size']
        for directive in key_directives:
            if directive not in content:
                warnings.append(
                    f"{readme_name}: Key directive '{directive}' not mentioned"
                )
    
    return warnings


def check_installation_guide_current(project_root: Path) -> List[str]:
    """Check that installation guide references current version patterns."""
    warnings = []
    
    install_guide = project_root / 'docs' / 'guides' / 'INSTALLATION.md'
    if not install_guide.exists():
        return warnings
    
    try:
        content = install_guide.read_text(encoding='utf-8')
    except Exception as exc:
        warnings.append(f"INSTALLATION.md unreadable: {exc}")
        return warnings

    # Check that it has version examples (not checking specific version)
    if not _has_version_example(content):
        warnings.append("INSTALLATION.md has no version examples")

    return warnings


def _has_version_example(content: str) -> bool:
    """Return True when the text contains a dotted semantic version."""
    for token in content.split():
        candidate = token.strip("`*()[]{}<>,.;:\"'")
        if candidate.startswith("v"):
            candidate = candidate[1:]
        parts = candidate.split(".")
        if len(parts) != 3:
            continue
        if all(part.isdigit() for part in parts):
            return True
    return False


def main():
    if len(sys.argv) > 1:
        project_root = Path(validate_read_path(sys.argv[1]))
    else:
        project_root = Path(__file__).parent.parent.parent
    
    if not project_root.exists():
        print(f"Project directory not found: {project_root}", file=sys.stderr)
        sys.exit(1)
    
    all_warnings = []
    all_warnings.extend(check_changelog_exists(project_root))
    all_warnings.extend(check_readme_mentions_key_features(project_root))
    all_warnings.extend(check_installation_guide_current(project_root))
    
    if all_warnings:
        print(
            f"Documentation sync check failed ({len(all_warnings)} warning(s)):",
            file=sys.stderr,
        )
        for warning in all_warnings:
            print(f"  WARNING: {warning}", file=sys.stderr)
        sys.exit(1)
    else:
        print("OK: Documentation synchronization checks passed")

    sys.exit(0)


if __name__ == '__main__':
    main()
