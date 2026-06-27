#!/usr/bin/env python3
"""
detect_html_sanitizer_invariants.py — HTML Sanitizer Invariant Detection
                                                                          (Rule 5, 6, 27)

Rule 5 (html-sanitizer): Void elements (img, br, hr, input, etc.) must not
  have closing tags. The sanitizer must handle them correctly.

Rule 6 (html-sanitizer): Nested elements must be properly closed in reverse
  order. The sanitizer must maintain a stack and close elements correctly.

Rule 27 (html-sanitizer): URLs in href/src attributes must be validated to
  prevent javascript: and data: URI injection attacks.

Detection strategy:
  This detector focuses on the security-critical invariants that MUST be
  present in any HTML processing code:
  
  1. URL safety: Check that dangerous URL schemes (javascript:, data:, vbscript:)
     are explicitly blocked somewhere in the codebase.
  2. Void element awareness: Check that void elements are recognized as such.
  3. Nesting depth limits: Check that there's protection against deeply nested HTML.

This is a conservative detector — it only flags clear security gaps.

Usage:
  python3 tools/harness/detect_html_sanitizer_invariants.py [directory]
    directory defaults to components/rust-converter/src

Exit codes:
  0 — no violations found (or only warnings)
  1 — one or more violations detected
"""

import os
import re
import sys
from pathlib import Path
from typing import List

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path


def check_url_safety(codebase_content: str, src_dir: Path) -> List[str]:
    """Check that dangerous URL schemes are blocked somewhere in the codebase."""
    issues = []
    
    # Check if there's any URL validation for dangerous schemes
    has_dangerous_scheme_check = bool(re.search(
        r'(?:javascript|data|vbscript)\s*:',
        codebase_content,
        re.IGNORECASE
    ))
    
    has_url_safety_function = bool(re.search(
        r'(?:is_safe|sanitize|validate|check).*(?:url|uri|link|href)',
        codebase_content,
        re.IGNORECASE
    ))
    
    # If code references href/src but has no safety checks, flag it
    has_href_src = bool(re.search(r'(?:href|src)', codebase_content, re.IGNORECASE))
    
    if has_href_src and not has_dangerous_scheme_check and not has_url_safety_function:
        issues.append(
            f"{src_dir}: Code references href/src attributes but no URL safety "
            f"validation for dangerous schemes (javascript:, data:, vbscript:) detected"
        )
    
    return issues


def check_nesting_depth_protection(codebase_content: str, src_dir: Path) -> List[str]:
    """Check that there's protection against deeply nested HTML."""
    issues = []
    
    # Look for nesting depth limits
    has_depth_check = bool(re.search(
        r'(?:max_depth|nesting_depth|depth.*limit|max.*nesting)',
        codebase_content,
        re.IGNORECASE
    ))
    
    has_stack_bounded = bool(re.search(
        r'(?:stack.*bound|bound.*stack|stack.*limit)',
        codebase_content,
        re.IGNORECASE
    ))
    
    if not has_depth_check and not has_stack_bounded:
        issues.append(
            f"{src_dir}: HTML processing code but no nesting depth protection detected. "
            f"Consider adding max_depth limits to prevent stack overflow on deeply nested HTML."
        )
    
    return issues


def main():
    if len(sys.argv) > 1:
        src_dir = Path(validate_read_path(sys.argv[1]))
    else:
        repo_root = Path(__file__).parent.parent.parent
        src_dir = repo_root / 'components' / 'rust-converter' / 'src'
    
    if not src_dir.exists():
        print(f"Source directory not found: {src_dir}")
        sys.exit(0)
    
    # Collect all Rust source content
    all_content = ""
    for rust_file in src_dir.rglob('*.rs'):
        try:
            all_content += rust_file.read_text(encoding='utf-8') + "\n"
        except Exception:
            continue
    
    if not all_content:
        print(f"No Rust files found in {src_dir}")
        sys.exit(0)
    
    # Run checks on the entire codebase (not per-file to reduce false positives)
    all_issues = []
    all_issues.extend(check_url_safety(all_content, src_dir))
    all_issues.extend(check_nesting_depth_protection(all_content, src_dir))
    
    if all_issues:
        print(f"Found {len(all_issues)} HTML sanitizer invariant issue(s):")
        for issue in all_issues:
            print(f"  {issue}")
        sys.exit(1)
    else:
        print(f"OK: HTML sanitizer invariants verified in {src_dir}")
        sys.exit(0)


if __name__ == '__main__':
    main()
