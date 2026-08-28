#!/bin/bash
# Regression Test: Non-Canonical Log Prefix Detection
#
# **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9,
#              1.10, 1.11, 1.12, 1.14, 1.15, 1.16**
#
# Property 1: Bug Condition - All log call sites in components/nginx-module/src/
# must use the canonical prefix "markdown:" exactly. Any other prefix variant
# (space-separated qualifiers, underscore-separated qualifiers, compound forms)
# constitutes a bug.
#
# The test exits nonzero whenever non-canonical prefixes are present.
# A clean source tree therefore produces a passing result.
#
# Run:
#   bash tests/property/test_log_prefix_canonical.sh
#
# Exit codes:
#   0 = PASS (all prefixes are canonical)
#   1 = FAIL (non-canonical prefixes found — bug confirmed)

set -euo pipefail

SRCDIR="components/nginx-module/src"
FAIL=0
TOTAL_NON_CANONICAL=0

# Navigate to repo root (script may be invoked from any directory)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

echo "=== Regression Test: Non-Canonical Log Prefix Detection ==="
echo "Source directory: $SRCDIR"
echo ""

grep_space_prefixes() {
    local source_file="$1"
    grep -nH -E '"markdown [a-z]' "$source_file" || true
    return 0
}

grep_space_prefix_count() {
    local source_file="$1"
    grep -c -E '"markdown [a-z]' "$source_file" || true
    return 0
}

grep_underscore_prefixes() {
    local source_file="$1"
    grep -nH -E '"markdown_metrics:|"markdown_dynamic_config_path:|"markdown_stream_types:|"markdown_content_types:|"markdown_auth_cookies:' "$source_file" || true
    return 0
}

grep_underscore_prefix_count() {
    local source_file="$1"
    grep -c -E '"markdown_metrics:|"markdown_dynamic_config_path:|"markdown_stream_types:|"markdown_content_types:|"markdown_auth_cookies:' "$source_file" || true
    return 0
}

# --- Check 1: Space-separated non-canonical prefixes ---
# Pattern: "markdown <word>" where <word> is NOT followed by ":"
# This catches "markdown filter:", "markdown streaming:", etc.
echo "--- Check 1: Space-separated non-canonical prefixes ---"
SPACE_PREFIXES=""
SPACE_COUNT=0
while IFS= read -r -d '' source_file; do
    file_matches="$(grep_space_prefixes "$source_file")"
    if [[ -n "$file_matches" ]]; then
        if [[ -n "$SPACE_PREFIXES" ]]; then
            SPACE_PREFIXES+=$'\n'
        fi
        SPACE_PREFIXES+="$file_matches"
        file_count="$(grep_space_prefix_count "$source_file")"
        SPACE_COUNT=$((SPACE_COUNT + file_count))
    fi
done < <(find "$SRCDIR" -type f -print0)

if [[ "$SPACE_COUNT" -gt 0 ]]; then
    echo "FAIL: Found $SPACE_COUNT log sites with space-separated non-canonical prefixes"
    echo ""
    # Group by prefix variant
    echo "Breakdown by prefix variant:"
    echo "$SPACE_PREFIXES" | sed -n 's/.*\("markdown [^"]*:\).*/\1/p' | sort | uniq -c | sort -rn
    echo ""
    TOTAL_NON_CANONICAL=$((TOTAL_NON_CANONICAL + SPACE_COUNT))
    FAIL=1
else
    echo "PASS: No space-separated non-canonical prefixes found"
fi
echo ""

# --- Check 2: Underscore-separated non-canonical log prefixes ---
# These are specific known non-canonical underscore-prefixed log messages:
#   "markdown_metrics:", "markdown_dynamic_config_path:",
#   "markdown_stream_types:", "markdown_content_types:", "markdown_auth_cookies:"
# We search for these exact prefix patterns in log format strings.
echo "--- Check 2: Underscore-separated log prefixes ---"
UNDERSCORE_PREFIXES=""
UNDERSCORE_COUNT=0
while IFS= read -r -d '' source_file; do
    file_matches="$(grep_underscore_prefixes "$source_file")"
    if [[ -n "$file_matches" ]]; then
        if [[ -n "$UNDERSCORE_PREFIXES" ]]; then
            UNDERSCORE_PREFIXES+=$'\n'
        fi
        UNDERSCORE_PREFIXES+="$file_matches"
        file_count="$(grep_underscore_prefix_count "$source_file")"
        UNDERSCORE_COUNT=$((UNDERSCORE_COUNT + file_count))
    fi
done < <(find "$SRCDIR" -type f -print0)

if [[ "$UNDERSCORE_COUNT" -gt 0 ]]; then
    echo "FAIL: Found $UNDERSCORE_COUNT log sites with underscore-separated prefixes"
    echo ""
    echo "Breakdown by prefix variant:"
    echo "$UNDERSCORE_PREFIXES" | sed -n 's/.*\("markdown_[^"]*:\).*/\1/p' | sort | uniq -c | sort -rn
    echo ""
    TOTAL_NON_CANONICAL=$((TOTAL_NON_CANONICAL + UNDERSCORE_COUNT))
    FAIL=1
else
    echo "PASS: No underscore-separated non-canonical log prefixes found"
fi
echo ""

# --- Summary ---
echo "=== SUMMARY ==="
echo "Total non-canonical log prefix sites: $TOTAL_NON_CANONICAL"
echo ""

if [[ "$FAIL" -eq 1 ]]; then
    echo "RESULT: FAIL — Non-canonical log prefixes detected (bug confirmed)"
    echo ""
    echo "Expected: All log call sites use exactly \"markdown:\" as prefix"
    echo "Actual: $TOTAL_NON_CANONICAL sites use non-canonical prefix variants"
    echo ""
    echo "File distribution of non-canonical prefixes:"
    {
        while IFS= read -r -d '' source_file; do
            grep_space_prefixes "$source_file" | grep -v '"markdown:"' || true
            grep_underscore_prefixes "$source_file" || true
        done < <(find "$SRCDIR" -type f -print0)
    } | cut -d: -f1 | sort | uniq -c | sort -rn
    exit 1
else
    echo "RESULT: PASS — All log prefixes use canonical \"markdown:\" format"
    exit 0
fi
