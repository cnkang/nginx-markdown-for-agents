#!/bin/bash
# test-deb-package.sh — Verify .deb package structure and contents.
#
# Usage: ./test-deb-package.sh <path-to-deb-file>
#
# Validates:
#   - dpkg-deb --info reports required metadata fields
#   - dpkg-deb --contents includes the .so module file
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — usage error

set -e

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "PASS: $1" >&2
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $1" >&2
}

usage() {
    echo "Usage: $0 <path-to-deb-file>" >&2
    exit 2
}

case "${1:-}" in
    -h|--help)
        usage
        ;;
    "")
        echo "Error: no .deb file path provided" >&2
        usage
        ;;
    *)
        DEB_FILE="$1"
        ;;
esac

if [ ! -f "$DEB_FILE" ]; then
    echo "Error: file not found: $DEB_FILE" >&2
    exit 2
fi

# --- dpkg-deb --info checks ---

INFO_OUTPUT=$(dpkg-deb --info "$DEB_FILE" 2>&1) || {
    fail "dpkg-deb --info failed"
    echo "$INFO_OUTPUT" >&2
    exit 1
}

# Required fields in package info
REQUIRED_FIELDS="Package Version Architecture Maintainer Description"

for field in $REQUIRED_FIELDS; do
    if echo "$INFO_OUTPUT" | grep -qi "^ *${field}:"; then
        pass "info contains field: $field"
    else
        fail "info missing field: $field"
    fi
done

# --- dpkg-deb --contents checks ---

CONTENTS_OUTPUT=$(dpkg-deb --contents "$DEB_FILE" 2>&1) || {
    fail "dpkg-deb --contents failed"
    echo "$CONTENTS_OUTPUT" >&2
    exit 1
}

# Verify .so file is included
if echo "$CONTENTS_OUTPUT" | grep -q '\.so'; then
    pass "contents includes .so module file"
else
    fail "contents missing .so module file"
fi

# --- Summary ---

echo "" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
