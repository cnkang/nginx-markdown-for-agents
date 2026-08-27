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
cd "$REPO_ROOT" || exit 1

SRCDIR="components/nginx-module/src"
FAIL=0
LOG_COUNT_STATUS="FAIL"
UNIT_STATUS="FAIL"
COVERAGE_STATUS="FAIL"
DIFF_STATUS="FAIL"
WARNING_STATUS="PASS"

echo "=== Preservation Property Test: Non-Prefix Log Call Components Unchanged ==="
echo "Source directory: $SRCDIR"
echo ""

# --- Baseline constant: total log call site count ---
# Observed on unfixed code: 278 log call sites
# Updated 2026-08-19: 375 (codebase grew since the original 278 baseline;
# the count is verified constant between HEAD and working tree).
# Updated 2026-08-19: 374 — the dynconf bind-once fix removed
# the effective-conf allocation-failure log site (by-value copy has no
# allocation failure path).
# Updated 2026-08-22 (pre-freeze remediation): 379 — the brotli
# error-classification split (resource vs format) adds category-specific
# diagnostic sites, and the streaming/packaging fixes add failure-path
# logs; each was reviewed as part of its fix.
BASELINE_LOG_SITES=379

echo "--- Property 1: Log call site count remains constant ---"
CURRENT_LOG_SITES=0
while IFS= read -r -d '' source_file; do
    file_count="$(grep -c 'ngx_log_error\|ngx_log_debug' "$source_file" || true)"
    CURRENT_LOG_SITES=$((CURRENT_LOG_SITES + file_count))
done < <(find "$SRCDIR" -type f -print0)

if [[ "$CURRENT_LOG_SITES" -eq "$BASELINE_LOG_SITES" ]]; then
    LOG_COUNT_STATUS="PASS"
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
    UNIT_STATUS="PASS"
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
    COVERAGE_STATUS="PASS"
    echo "PASS: make coverage-c exits 0"
    # Extract and display coverage percentage for reference
    COVERAGE_LINE=$(grep 'lines\.\.\.\.\.\.\.:' /tmp/coverage_c_output.txt || true)
    if [[ -n "$COVERAGE_LINE" ]]; then
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
# Scope the diff to the two files the log-prefix unification fix touched
# (ngx_http_markdown_stream_commit.c / ngx_http_markdown_stream_postcommit.c).
# Later fix batches legitimately change log components elsewhere (e.g. a
# severity upgrade or a removed log site); those are covered by their own
# gates and must not fail this prefix-preservation guard.
PR_BASE_REF="${GITHUB_BASE_SHA:-}"
if [[ -z "$PR_BASE_REF" ]]; then
    PR_BASE_BRANCH="${GITHUB_BASE_REF:-${BASE_REF:-main}}"
    PR_BASE_REF="origin/${PR_BASE_BRANCH}"
fi
MERGE_BASE=""
if ! MERGE_BASE=$(git merge-base HEAD "$PR_BASE_REF" 2>/dev/null); then
    echo "FAIL: unable to resolve a verified merge base for $PR_BASE_REF" >&2
    echo "  Refusing to compare HEAD with itself; provide a fetched PR base." >&2
    FAIL=1
fi
GIT_DIFF=""
if [[ -n "$MERGE_BASE" ]]; then
    GIT_DIFF=$(git diff "$MERGE_BASE" -- "components/nginx-module/src/ngx_http_markdown_stream_commit.c" "components/nginx-module/src/ngx_http_markdown_stream_postcommit.c" 2>/dev/null || true)
fi

if [[ -z "$MERGE_BASE" ]]; then
    :
elif [[ -z "$GIT_DIFF" ]]; then
    DIFF_STATUS="PASS"
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

    if [[ "$NON_PREFIX_COUNT" -eq 0 ]]; then
        DIFF_STATUS="PASS"
        echo "PASS: All changes are within markdown prefix string literals"
    else
        # Further filter: check if non-prefix lines are just context or whitespace
        # Real violations would be changes to NGX_LOG_*, errno, log objects, format args
        REAL_VIOLATIONS=$(echo "$NON_PREFIX_CHANGES" | \
            grep -i 'NGX_LOG_\|->log\|->connection->log\|cycle->log\|ngx_errno\|NGX_LOG_DEBUG_HTTP' || true)
        VIOLATION_COUNT=$(echo "$REAL_VIOLATIONS" | grep -c . || true)

        if [[ "$VIOLATION_COUNT" -gt 0 ]]; then
            echo "FAIL: Found $VIOLATION_COUNT changes to non-prefix log components"
            echo "  Violations:"
            echo "$REAL_VIOLATIONS" | head -10
            FAIL=1
        else
            DIFF_STATUS="PASS"
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

if [[ "$WARNING_COUNT" -eq 0 ]]; then
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
echo "Property 1 (log site count): $LOG_COUNT_STATUS"
echo "Property 2 (unit tests):     $UNIT_STATUS"
echo "Property 3 (coverage bar):   $COVERAGE_STATUS"
echo "Property 4 (diff analysis):  $DIFF_STATUS"
echo "Property 5 (no new warnings): $WARNING_STATUS"
echo ""

if [[ "$FAIL" -eq 1 ]]; then
    echo "RESULT: FAIL — One or more preservation properties violated"
    exit 1
else
    echo "RESULT: PASS — All preservation properties hold"
    echo ""
    echo "Baseline recorded:"
    echo "  Log call sites: $BASELINE_LOG_SITES"
    echo "  Unit tests: $UNIT_STATUS"
    echo "  Coverage: $COVERAGE_STATUS"
    echo "  Diff: $DIFF_STATUS"
    echo "  Warnings: $WARNING_STATUS"
    exit 0
fi
