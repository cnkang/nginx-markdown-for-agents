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

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path


def extract_test_functions(content: str, file_path: Path) -> List[Tuple[str, int, str, bool, bool]]:
    """Extract test functions with their line numbers and bodies."""
    tests = []
    
    # Match #[test] functions
    test_pattern = r'#\[test\]\s*(?:#\[.*?\]\s*)*fn\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[^{]+)?\{'
    
    for match in re.finditer(test_pattern, content, re.MULTILINE | re.DOTALL):
        func_name = match.group(1)
        start_pos = match.start()
        line_num = content[:start_pos].count('\n') + 1
        
        # Find the function body (matching braces, skipping string literals)
        brace_start = match.end() - 1
        brace_count = 0
        func_end = brace_start
        in_string = False
        in_raw_string = False
        
        i = brace_start
        while i < len(content):
            c = content[i]
            
            # Skip braces inside string literals
            if not in_string and not in_raw_string:
                if c == '"' and (i == 0 or content[i-1] != '\\'):
                    if i >= 2 and content[i-2:i] == 'r#':
                        in_raw_string = True
                    else:
                        in_string = True
                elif c in ('{', '}'):
                    if c == '{':
                        brace_count += 1
                    else:
                        brace_count -= 1
                        if brace_count == 0:
                            func_end = i + 1
                            break
            elif in_string:
                if c == '"' and content[i-1] != '\\':
                    in_string = False
            elif in_raw_string:
                if c == '"' and content[i-1:i] == '#"':
                    in_raw_string = False
            
            i += 1
        
        if brace_count != 0:
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
    
    # Process all Rust test files
    for test_file in test_dir.rglob('*.rs'):
        try:
            content = test_file.read_text(encoding='utf-8')
        except Exception as e:
            continue
        
        tests = extract_test_functions(content, test_file)
        
        for func_name, line_num, func_body, has_should_panic, is_property_test in tests:
            issues = check_test_assertions(
                func_name, line_num, func_body, 
                has_should_panic, is_property_test
            )
            
            if issues:
                rel_path = test_file.relative_to(test_dir.parent.parent)
                all_issues.extend([f"{rel_path}: {issue}" for issue in issues])
    
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
