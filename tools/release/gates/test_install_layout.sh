#!/bin/bash
# ---------------------------------------------------------------------------
# test_install_layout.sh — Unit tests for check_install_layout.sh
#
# PURPOSE:
#   Validates that the install layout checker script has correct syntax,
#   handles usage/help correctly, and rejects invalid inputs with proper
#   exit codes.
#
# Validates: Requirements 11.4
#
# USAGE:
#   bash tools/release/gates/test_install_layout.sh
#
# EXIT CODES:
#   0  All tests pass
#   1  One or more tests failed
#
# NOTES:
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - AGENTS.md Rule 14: new helper must have corresponding tests
#   - AGENTS.md Rule 11: macOS bash 3.2 compatible
#   - AGENTS.md Rule 18: case has default; messages to stderr; explicit return
# ---------------------------------------------------------------------------

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK_SCRIPT="$SCRIPT_DIR/check_install_layout.sh"

PASS_COUNT=0
FAIL_COUNT=0
SEPARATOR='========================================================================'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Colors (if terminal supports them)
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    NC='\033[0m'
else
    GREEN=''
    RED=''
    NC=''
fi

pass() {
    local msg="$1"
    PASS_COUNT=$((PASS_COUNT + 1))
    printf "  ${GREEN}PASS${NC}: %s\n" "$msg"
    return 0
}

fail() {
    local msg="$1"
    local detail="${2:-}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf "  ${RED}FAIL${NC}: %s\n" "$msg" >&2
    if [[ -n "$detail" ]]; then
        printf "        Detail: %s\n" "$detail" >&2
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Pre-flight check
# ---------------------------------------------------------------------------

if [[ ! -f "$CHECK_SCRIPT" ]]; then
    printf '[ERROR] check_install_layout.sh not found at: %s\n' "$CHECK_SCRIPT" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

printf '%s\n' "$SEPARATOR" >&2
printf ' Unit Tests: check_install_layout.sh (install layout validator)\n' >&2
printf ' Validates: Requirements 11.4\n' >&2
printf '%s\n' "$SEPARATOR" >&2
printf '\n'

# --- Test 1: Script has valid bash syntax ---

printf 'Test group: Script integrity\n'

if bash -n "$CHECK_SCRIPT" 2>/dev/null; then
    pass "script has valid bash syntax (bash -n)"
else
    fail "script has valid bash syntax (bash -n)" "syntax check failed"
fi

printf '\n'

# --- Test 2: --help flag exits 0 ---

printf 'Test group: Usage and help\n'

local_exit=0
bash "$CHECK_SCRIPT" --help >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 0 ]]; then
    pass "--help flag exits 0"
else
    fail "--help flag exits 0" "expected exit 0, got exit $local_exit"
fi

# --- Test 3: No arguments exits 2 ---

local_exit=0
bash "$CHECK_SCRIPT" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 2 ]]; then
    pass "no arguments exits 2"
else
    fail "no arguments exits 2" "expected exit 2, got exit $local_exit"
fi

printf '\n'

# --- Test 4: Non-existent file exits 1 ---

printf 'Test group: Invalid inputs\n'

local_exit=0
bash "$CHECK_SCRIPT" "/tmp/nonexistent-pkg-xyz123.deb" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "non-existent .deb file exits 1"
else
    fail "non-existent .deb file exits 1" "expected exit 1, got exit $local_exit"
fi

# --- Test 5: Unsupported file type exits 1 ---

# Create a temporary file with unsupported extension
TMPFILE=""
cleanup() {
    if [[ -n "$TMPFILE" && -f "$TMPFILE" ]]; then
        rm -f "$TMPFILE"
    fi
    return 0
}
trap cleanup EXIT

TMPFILE="$(mktemp /tmp/test_layout_XXXXXX.tar.gz)"

local_exit=0
bash "$CHECK_SCRIPT" "$TMPFILE" >/dev/null 2>/dev/null || local_exit=$?
if [[ "$local_exit" -eq 1 ]]; then
    pass "unsupported file type (.tar.gz) exits 1"
else
    fail "unsupported file type (.tar.gz) exits 1" "expected exit 1, got exit $local_exit"
fi

printf '\n'

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf '%s\n' "$SEPARATOR"
printf ' Results: %d passed, %d failed\n' "$PASS_COUNT" "$FAIL_COUNT"
printf '%s\n' "$SEPARATOR"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    exit 1
fi

exit 0
