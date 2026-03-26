#!/bin/bash
#
# Comprehensive Corpus Validation Script
# Validates all HTML files in tests/corpus, verifies converter runtime,
# and checks fixture metadata sidecars.
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
META_ERRORS=0
SEPARATOR_LINE="========================================="

# Valid enum values
VALID_PAGE_TYPES="clean-article documentation nav-heavy boilerplate-heavy complex-common"
VALID_CONVERSION_RESULTS="converted skipped failed-open"
REQUIRED_META_FIELDS="fixture-id page-type expected-conversion-result input-size-bytes source-description failure-corpus"

echo "$SEPARATOR_LINE"
echo "Comprehensive Corpus Validation"
echo "$SEPARATOR_LINE"
echo ""

# Build the converter first
echo "Building Rust converter..."
cd "$ROOT/components/rust-converter"
cargo build --release --quiet 2>&1 | grep -v "warning:" || true
cd "$ROOT"

# Find all HTML files (exclude generated huge files)
HTML_FILES=$(find "$ROOT/tests/corpus" -name "*.html" -type f ! -name "generate-*" | sort)

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

# ---------------------------------------------------------------------------
# Phase 1: Validate corpus-version.json
# ---------------------------------------------------------------------------
echo "--- Corpus Version ---"
CORPUS_VERSION_FILE="$ROOT/tests/corpus/corpus-version.json"
if [[ ! -f "$CORPUS_VERSION_FILE" ]]; then
    echo -e "${RED}✗${NC} corpus-version.json not found"
    META_ERRORS=$((META_ERRORS + 1))
else
    VERSION=$(python3 -c "
import json, sys, re
try:
    data = json.load(open('$CORPUS_VERSION_FILE'))
    v = data.get('version', '')
    if not re.match(r'^\d+\.\d+\.\d+$', v):
        print('INVALID', file=sys.stderr)
        sys.exit(1)
    print(v)
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1)
    if [[ $? -eq 0 ]]; then
        echo -e "${GREEN}✓${NC} corpus-version.json: v${VERSION}"
    else
        echo -e "${RED}✗${NC} corpus-version.json: invalid semver"
        META_ERRORS=$((META_ERRORS + 1))
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# Phase 2: Validate HTML files and metadata sidecars
# ---------------------------------------------------------------------------
echo "--- HTML Files and Metadata ---"

# Track page types and failure corpus count
declare -A PAGE_TYPE_COUNT
for pt in $VALID_PAGE_TYPES; do
    PAGE_TYPE_COUNT[$pt]=0
done
FAILURE_CORPUS_COUNT=0

for html_file in $HTML_FILES; do
    TOTAL=$((TOTAL + 1))
    rel_path="${html_file#$ROOT/}"
    filename=$(basename "$html_file")

    if [[ ! -f "$html_file" ]] || [[ ! -s "$html_file" ]]; then
        echo -e "${RED}✗${NC} $rel_path (empty or missing)"
        FAILED=$((FAILED + 1))
        continue
    fi

    # Basic structure guardrail to catch obvious corpus corruption.
    if ! grep -qi "<html\|<body\|<h1\|<p\|<div\|<script\|<style" "$html_file"; then
        echo -e "${YELLOW}⚠${NC} $rel_path (no common HTML tags detected)"
    fi

    # Check for corresponding .meta.json sidecar
    meta_file="${html_file%.html}.meta.json"
    if [[ ! -f "$meta_file" ]]; then
        echo -e "${RED}✗${NC} $rel_path (missing .meta.json sidecar)"
        META_ERRORS=$((META_ERRORS + 1))
        FAILED=$((FAILED + 1))
        continue
    fi

    # Validate metadata sidecar content
    META_RESULT=$(python3 -c "
import json, sys

valid_page_types = {'clean-article', 'documentation', 'nav-heavy', 'boilerplate-heavy', 'complex-common'}
valid_results = {'converted', 'skipped', 'failed-open'}
required_fields = ['fixture-id', 'page-type', 'expected-conversion-result', 'input-size-bytes', 'source-description', 'failure-corpus']

try:
    with open('$meta_file') as f:
        data = json.load(f)

    errors = []
    for field in required_fields:
        if field not in data:
            errors.append(f'missing field: {field}')

    if 'page-type' in data and data['page-type'] not in valid_page_types:
        errors.append(f'invalid page-type: {data[\"page-type\"]}')

    if 'expected-conversion-result' in data and data['expected-conversion-result'] not in valid_results:
        errors.append(f'invalid expected-conversion-result: {data[\"expected-conversion-result\"]}')

    if 'input-size-bytes' in data and not isinstance(data['input-size-bytes'], int):
        errors.append(f'input-size-bytes must be integer')

    if 'failure-corpus' in data and not isinstance(data['failure-corpus'], bool):
        errors.append(f'failure-corpus must be boolean')

    if errors:
        print('ERRORS:' + ';'.join(errors))
        sys.exit(1)

    # Output page-type and failure-corpus for counting
    print(f'{data[\"page-type\"]}|{data[\"failure-corpus\"]}')
except Exception as e:
    print(f'PARSE_ERROR:{e}')
    sys.exit(1)
" 2>&1)

    if [[ $? -eq 0 ]]; then
        page_type=$(echo "$META_RESULT" | cut -d'|' -f1)
        is_failure=$(echo "$META_RESULT" | cut -d'|' -f2)

        if [[ -n "${PAGE_TYPE_COUNT[$page_type]+x}" ]]; then
            PAGE_TYPE_COUNT[$page_type]=$((${PAGE_TYPE_COUNT[$page_type]} + 1))
        fi

        if [[ "$is_failure" == "True" ]]; then
            FAILURE_CORPUS_COUNT=$((FAILURE_CORPUS_COUNT + 1))
        fi

        echo -e "${GREEN}✓${NC} $rel_path [${page_type}]"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}✗${NC} $rel_path metadata: $META_RESULT"
        META_ERRORS=$((META_ERRORS + 1))
        FAILED=$((FAILED + 1))
    fi
done

echo ""

# ---------------------------------------------------------------------------
# Phase 3: Coverage checks
# ---------------------------------------------------------------------------
echo "--- Coverage Checks ---"

# Check at least 2 fixtures per page type
for pt in $VALID_PAGE_TYPES; do
    count=${PAGE_TYPE_COUNT[$pt]}
    if [[ $count -lt 2 ]]; then
        echo -e "${RED}✗${NC} page-type '$pt': $count fixtures (minimum 2 required)"
        META_ERRORS=$((META_ERRORS + 1))
    else
        echo -e "${GREEN}✓${NC} page-type '$pt': $count fixtures"
    fi
done

# Check at least 3 failure corpus fixtures
if [[ $FAILURE_CORPUS_COUNT -lt 3 ]]; then
    echo -e "${RED}✗${NC} failure-corpus: $FAILURE_CORPUS_COUNT fixtures (minimum 3 required)"
    META_ERRORS=$((META_ERRORS + 1))
else
    echo -e "${GREEN}✓${NC} failure-corpus: $FAILURE_CORPUS_COUNT fixtures"
fi

echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "$SEPARATOR_LINE"
echo "Results:"
echo "  Total HTML files:  $TOTAL"
echo -e "  ${GREEN}Passed: $PASSED${NC}"
if [[ $FAILED -gt 0 ]]; then
    echo -e "  ${RED}Failed: $FAILED${NC}"
else
    echo -e "  ${GREEN}Failed: $FAILED${NC}"
fi
if [[ $META_ERRORS -gt 0 ]]; then
    echo -e "  ${RED}Metadata errors: $META_ERRORS${NC}"
else
    echo -e "  ${GREEN}Metadata errors: $META_ERRORS${NC}"
fi
echo "$SEPARATOR_LINE"

if [[ $FAILED -gt 0 ]] || [[ $META_ERRORS -gt 0 ]]; then
    exit 1
fi
