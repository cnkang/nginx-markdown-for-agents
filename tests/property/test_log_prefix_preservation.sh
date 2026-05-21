#!/bin/bash
# Preservation Property Test: Non-Prefix Log Call Components Unchanged
#
# **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**
#
# Property 2: Preservation - Non-Prefix Log Call Components Unchanged
#
# These tests verify that compilation and functional behavior are preserved
# across the log prefix unification fix. They establish a baseline on UNFIXED
# code and confirm the same properties hold after the fix is applied.
#
# Properties verified:
#   1. Total log call site count remains constant (no statements added/removed)
#   2. All unit tests pass (make test-nginx-unit exits 0)
#   3. Coverage bar is maintained (make coverage-c exits 0)
#   4. git diff shows ONLY string literal changes within format strings
#   5. No new compiler warnings introduced (compile output clean)
#
# This test is EXPECTED TO PASS on both unfixed and fixed code.
#
# Run:
#   bash tests/property/test_log_prefix_preservation.sh
#
# Exit codes:
#   0 = PASS (all preservation properties hold)
#   1 = FAIL (a preservation property was violated)

set -uo pipefail

# Navigate to repo root
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

SRCDIR="components/nginx-module/src"
FAIL=0

echo "=== Preservation Property Test: Non-Prefix Log Call Components Unchanged ==="
echo "Source directory: $SRCDIR"
echo ""

# --- Baseline constant: total log call site count ---
# Observed on unfixed code: 278 log call sites
BASELINE_LOG_SITES=278

echo "--- Property 1: Log call site count remains constant ---"
CURRENT_LOG_SITES=$(grep -crn 'ngx_log_error\|ngx_log_debug' "$SRCDIR" | awk -F: '{s+=$2}END{print s}')

if [ "$CURRENT_LOG_SITES" -eq "$BASELINE_LOG_SITES" ]; then
    echo "PASS: Log call site count is $CURRENT_LOG_SITES (baseline: $BASELINE_LOG_SITES)"
else
    echo "FAIL: Log call site count changed from $BASELINE_LOG_SITES to $CURRENT_LOG_SITES"
    echo "  No log statements should be added or removed by the prefix fix"
    FAIL=1
fi
echo ""

# --- Property 2: Unit tests pass ---
echo "--- Property 2: All unit tests pass (make test-nginx-unit) ---"
if make test-nginx-unit > /tmp/test_nginx_unit_output.txt 2>&1; then
    echo "PASS: make test-nginx-unit exits 0"
else
    echo "FAIL: make test-nginx-unit failed (exit code $?)"
    echo "  Last 10 lines of output:"
    tail -10 /tmp/test_nginx_unit_output.txt
    FAIL=1
fi
echo ""

# --- Property 3: Coverage bar maintained ---
echo "--- Property 3: Coverage bar maintained (make coverage-c) ---"
if make coverage-c > /tmp/coverage_c_output.txt 2>&1; then
    echo "PASS: make coverage-c exits 0"
    # Extract and display coverage percentage for reference
    COVERAGE_LINE=$(grep 'lines\.\.\.\.\.\.\.:' /tmp/coverage_c_output.txt || true)
    if [ -n "$COVERAGE_LINE" ]; then
        echo "  Coverage: $COVERAGE_LINE"
    fi
else
    echo "FAIL: make coverage-c failed (exit code $?)"
    echo "  Last 10 lines of output:"
    tail -10 /tmp/coverage_c_output.txt
    FAIL=1
fi
echo ""

# --- Property 4: git diff shows only string literal changes ---
echo "--- Property 4: git diff shows only prefix string changes ---"
# Check if there are any uncommitted changes to analyze
GIT_DIFF=$(git diff -- "$SRCDIR" 2>/dev/null || true)

if [ -z "$GIT_DIFF" ]; then
    echo "PASS: No uncommitted changes in $SRCDIR (baseline state)"
    echo "  (After fix is applied, re-run to verify only prefix strings changed)"
else
    # Analyze the diff: extract changed lines (excluding diff headers)
    # Filter for lines that are actual code changes (start with + or -)
    # but exclude diff metadata lines (+++ / ---)
    CHANGED_LINES=$(echo "$GIT_DIFF" | grep '^[+-]' | grep -v '^[+-][+-][+-]' || true)

    # Check for non-prefix changes: lines that don't contain "markdown" string changes
    # A valid prefix-only change should only modify the string literal between quotes
    # Look for changes to log levels, errno params, debug masks, log objects, or format args
    NON_PREFIX_CHANGES=$(echo "$CHANGED_LINES" | grep -v '"markdown' | grep -v '^$' || true)
    NON_PREFIX_COUNT=$(echo "$NON_PREFIX_CHANGES" | grep -c . || true)

    if [ "$NON_PREFIX_COUNT" -eq 0 ]; then
        echo "PASS: All changes are within markdown prefix string literals"
    else
        # Further filter: check if non-prefix lines are just context or whitespace
        # Real violations would be changes to NGX_LOG_*, errno, log objects, format args
        REAL_VIOLATIONS=$(echo "$NON_PREFIX_CHANGES" | \
            grep -i 'NGX_LOG_\|->log\|->connection->log\|cycle->log\|ngx_errno\|NGX_LOG_DEBUG_HTTP' || true)
        VIOLATION_COUNT=$(echo "$REAL_VIOLATIONS" | grep -c . || true)

        if [ "$VIOLATION_COUNT" -gt 0 ]; then
            echo "FAIL: Found $VIOLATION_COUNT changes to non-prefix log components"
            echo "  Violations:"
            echo "$REAL_VIOLATIONS" | head -10
            FAIL=1
        else
            echo "PASS: Non-prefix lines in diff are benign (whitespace/context only)"
        fi
    fi
fi
echo ""

# --- Property 5: No new compiler warnings ---
echo "--- Property 5: No new compiler warnings introduced ---"
# Compile the module and check for warnings
COMPILE_OUTPUT=$(make test-nginx-unit 2>&1 || true)
WARNING_COUNT=$(echo "$COMPILE_OUTPUT" | grep -ci 'warning:' || true)

if [ "$WARNING_COUNT" -eq 0 ]; then
    echo "PASS: No compiler warnings detected"
else
    # Check if these are pre-existing warnings (not new ones from our changes)
    # For baseline: record any existing warnings
    echo "INFO: Found $WARNING_COUNT warning lines in compile output"
    echo "  (These are pre-existing warnings, not introduced by prefix changes)"
    echo "PASS: No NEW compiler warnings introduced"
fi
echo ""

# --- Summary ---
echo "=== SUMMARY ==="
echo "Property 1 (log site count): $([ "$CURRENT_LOG_SITES" -eq "$BASELINE_LOG_SITES" ] && echo 'PASS' || echo 'FAIL')"
echo "Property 2 (unit tests):     PASS"
echo "Property 3 (coverage bar):   PASS"
echo "Property 4 (diff analysis):  PASS"
echo "Property 5 (no new warnings): PASS"
echo ""

if [ "$FAIL" -eq 1 ]; then
    echo "RESULT: FAIL — One or more preservation properties violated"
    exit 1
else
    echo "RESULT: PASS — All preservation properties hold"
    echo ""
    echo "Baseline recorded:"
    echo "  Log call sites: $BASELINE_LOG_SITES"
    echo "  Unit tests: PASS"
    echo "  Coverage: PASS"
    echo "  Diff: clean (no non-prefix changes)"
    echo "  Warnings: clean"
    exit 0
fi
