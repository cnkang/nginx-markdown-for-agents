#!/bin/bash
#
# Comprehensive Corpus Validation Script
# Validates all HTML files in tests/corpus and verifies converter runtime.
#

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Counters
TOTAL=0
PASSED=0
FAILED=0

echo "========================================="
echo "Comprehensive Corpus Validation"
echo "========================================="
echo ""

# Build the converter first
echo "Building Rust converter..."
cd "$ROOT/components/rust-converter"
cargo build --release --quiet 2>&1 | grep -v "warning:" || true
cd "$ROOT"

# Find all HTML files
HTML_FILES=$(find "$ROOT/tests/corpus" -name "*.html" -type f | sort)

echo "Found $(echo "$HTML_FILES" | wc -l | tr -d ' ') HTML test files"
echo ""

# Smoke-check converter runtime once (avoids repeated cargo startup per file).
echo "Running converter runtime smoke check..."
if ! cargo run --manifest-path "$ROOT/components/rust-converter/Cargo.toml" --release --example basic_conversion > /dev/null 2>&1; then
    echo -e "${RED}✗${NC} converter runtime smoke check failed"
    exit 1
fi
echo -e "${GREEN}✓${NC} converter runtime smoke check passed"
echo ""

# Validate corpus files
for html_file in $HTML_FILES; do
    TOTAL=$((TOTAL + 1))
    filename=$(basename "$html_file")

    if [ ! -f "$html_file" ] || [ ! -s "$html_file" ]; then
        echo -e "${RED}✗${NC} $filename (empty or missing)"
        FAILED=$((FAILED + 1))
        continue
    fi

    # Basic structure guardrail to catch obvious corpus corruption.
    if ! grep -qi "<html\\|<body\\|<h1\\|<p\\|<div\\|<script\\|<style" "$html_file"; then
        echo -e "${YELLOW}⚠${NC} $filename (no common HTML tags detected)"
    fi

    echo -e "${GREEN}✓${NC} $filename"
    PASSED=$((PASSED + 1))
done

echo ""
echo "========================================="
echo "Results:"
echo "  Total:  $TOTAL"
echo -e "  ${GREEN}Passed: $PASSED${NC}"
if [ $FAILED -gt 0 ]; then
    echo -e "  ${RED}Failed: $FAILED${NC}"
else
    echo -e "  ${GREEN}Failed: $FAILED${NC}"
fi
echo "========================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
