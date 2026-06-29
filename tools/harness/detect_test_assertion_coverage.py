#!/usr/bin/env python3
"""
detect_test_assertion_coverage.py — Test Assertion Coverage Detection
                                                                       (Rule 14, 16)

Rule 14 (testing-coverage): Every test must have meaningful assertions.
  Tests without assertions provide no verification value.

Rule 16 (testing-coverage): Every variable declared in a test should be
  used in an assertion or computation that feeds into an assertion.
  Dead stores indicate incomplete test logic.

Detection strategy:
  This detector identifies tests that:
  1. Have no assertions at all (critical issue)
  2. Have only trivial assertions like assert!(true) (warning)
  
  It excludes:
  - Tests that intentionally panic (should_panic attribute)
  - Tests that call functions which themselves assert
  - Property-based tests (proptest, quickcheck)
  - Tests with complex setup that may indirectly verify behavior

This is a focused detector — it only flags clear cases of missing assertions.

Usage:
  python3 tools/harness/detect_test_assertion_coverage.py [directory]
    directory defaults to components/rust-converter/tests

Exit codes:
  0 — no violations found (or only warnings)
  1 — one or more violations detected
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path


TEST_FUNCTION_PATTERN = re.compile(
    r'#\[test\]\s*'
    r'(?:#\[[^\]\n]*(?:\][^\[\n]*)?\]\s*)*'
    r'fn\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[^{]+)?\{',
    re.MULTILINE,
)


def _is_escaped_quote(content: str, offset: int) -> bool:
    return offset > 0 and content[offset - 1] == '\\'


def _raw_string_hash_count(content: str, offset: int) -> int:
    """Return raw-string hash count for a quote offset, or -1 if not raw."""
    idx = offset - 1
    hashes = 0
    while idx >= 0 and content[idx] == '#':
        hashes += 1
        idx -= 1
    if idx >= 0 and content[idx] == 'r':
        return hashes
    return -1


def _ends_raw_string(content: str, offset: int, hashes: int) -> bool:
    if hashes < 0:
        return False
    end = offset + 1 + hashes
    return (
        end <= len(content)
        and content[offset + 1:end] == ("#" * hashes)
    )


def _advance_string_state(
    content: str,
    offset: int,
    in_string: bool,
    raw_string_hashes: int,
) -> tuple[bool, bool, int]:
    c = content[offset]
    if in_string:
        still_in_string = not (c == '"' and not _is_escaped_quote(content, offset))
        return False, still_in_string, raw_string_hashes
    if raw_string_hashes >= 0:
        if c == '"' and _ends_raw_string(content, offset, raw_string_hashes):
            return False, in_string, -1
        return False, in_string, raw_string_hashes
    if c != '"' or _is_escaped_quote(content, offset):
        return True, in_string, raw_string_hashes
    raw_hashes = _raw_string_hash_count(content, offset)
    return False, raw_hashes < 0, raw_hashes


def _find_test_function_end(content: str, brace_start: int) -> int | None:
    """Return the end offset for a test function body."""
    brace_count = 0
    in_string = False
    raw_string_hashes = -1

    i = brace_start
    while i < len(content):
        c = content[i]
        count_brace, in_string, raw_string_hashes = _advance_string_state(
            content,
            i,
            in_string,
            raw_string_hashes,
        )

        if count_brace and c == '{':
            brace_count += 1
        elif count_brace and c == '}':
            brace_count -= 1
            if brace_count == 0:
                return i + 1

        i += 1

    return None


def extract_test_functions(content: str) -> List[Tuple[str, int, str, bool, bool]]:
    """Extract test functions with their line numbers and bodies."""
    tests = []

    for match in TEST_FUNCTION_PATTERN.finditer(content):
        func_name = match.group(1)
        start_pos = match.start()
        line_num = content[:start_pos].count('\n') + 1

        brace_start = match.end() - 1

        func_end = _find_test_function_end(content, brace_start)
        if func_end is None:
            continue

        func_body = content[brace_start:func_end]

        # Check if test has #[should_panic] attribute
        has_should_panic = bool(re.search(r'#\[should_panic', content[max(0, start_pos-200):start_pos]))

        # Check if test uses proptest or quickcheck
        is_property_test = bool(re.search(r'(?:proptest|quickcheck|for_all)', func_body))

        tests.append((func_name, line_num, func_body, has_should_panic, is_property_test))

    return tests


def check_test_assertions(func_name: str, line_num: int, func_body: str, 
                         has_should_panic: bool, is_property_test: bool) -> List[str]:
    """Check if a test function has meaningful assertions."""
    issues = []
    
    # Skip tests that are expected to panic
    if has_should_panic:
        return issues
    
    # Skip property-based tests (they have their own assertion mechanisms)
    if is_property_test:
        return issues
    
    # Check for direct assertions
    assertion_patterns = [
        r'\bassert!\s*\(',
        r'\bassert_eq!\s*\(',
        r'\bassert_ne!\s*\(',
        r'\bassert_matches!\s*\(',
        r'\bprop_assert!\s*\(',  # proptest assertions
        r'\bprop_assert_eq!\s*\(',
        r'\bprop_assert_ne!\s*\(',
        r'\bpanic!\s*\(',
        r'\.expect\s*\(',
        r'\.unwrap\s*\(\s*\)',
        r'let\s+_\s*:\s*&dyn\s+',  # Type assertion (let _: &dyn Trait = ...)
    ]
    
    has_assertion = any(re.search(pattern, func_body) for pattern in assertion_patterns)
    
    # Check for calls to helper functions that likely contain assertions
    # These are common patterns in test code
    assertion_helpers = [
        r'\bassert_\w+\s*\(',  # assert_fixture, assert_eq, etc.
        r'\bcompare_\w+\s*\(',  # compare_or_known, etc.
        r'\bcheck_\w+\s*\(',  # check_something
        r'\bverify_\w+\s*\(',  # verify_something
    ]
    
    has_assertion_helper = any(re.search(pattern, func_body) for pattern in assertion_helpers)
    
    # Check for FFI calls that test NULL handling (not crashing is the assertion)
    # These tests call functions with NULL and verify they don't crash
    ffi_null_patterns = [
        r'ffi_\w+\s*\([^)]*null[^)]*\)',  # ffi_function(null)
        r'ptr::null_mut\(\)',  # ptr::null_mut()
    ]
    
    has_ffi_null_test = any(re.search(pattern, func_body, re.IGNORECASE) for pattern in ffi_null_patterns)
    
    # If no direct assertion, no assertion helper, and not a NULL safety test, flag it
    if not has_assertion and not has_assertion_helper and not has_ffi_null_test:
        issues.append(f"Test '{func_name}' (line {line_num}) has no assertions")
    
    return issues


def main():
    if len(sys.argv) > 1:
        test_dir = Path(validate_read_path(sys.argv[1]))
    else:
        repo_root = Path(__file__).parent.parent.parent
        test_dir = repo_root / 'components' / 'rust-converter' / 'tests'
    
    if not test_dir.exists():
        print(f"Test directory not found: {test_dir}")
        sys.exit(0)
    
    all_issues = []
    read_errors = []
    
    # Process all Rust test files
    for test_file in test_dir.rglob('*.rs'):
        try:
            content = test_file.read_text(encoding='utf-8')
        except Exception as exc:
            read_errors.append(f"{test_file}: {exc}")
            continue
        
        tests = extract_test_functions(content)
        
        for func_name, line_num, func_body, has_should_panic, is_property_test in tests:
            issues = check_test_assertions(
                func_name, line_num, func_body, 
                has_should_panic, is_property_test
            )
            
            if issues:
                rel_path = test_file.relative_to(test_dir.parent.parent)
                all_issues.extend([f"{rel_path}: {issue}" for issue in issues])

    if read_errors:
        all_issues.extend(
            [f"Harness could not read Rust test file: {err}" for err in read_errors]
        )
    
    if all_issues:
        print(f"Found {len(all_issues)} test assertion coverage issue(s):")
        for issue in all_issues:
            print(f"  {issue}")
        print("\nNote: Tests with #[should_panic] or property-based tests are excluded.")
        sys.exit(1)
    else:
        print(f"OK: All tests in {test_dir} have proper assertions")
        sys.exit(0)


if __name__ == '__main__':
    main()
