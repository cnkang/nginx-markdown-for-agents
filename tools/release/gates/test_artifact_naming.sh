#!/bin/bash
# ---------------------------------------------------------------------------
# test_artifact_naming.sh — Unit tests for check_artifact_naming.sh
#
# PURPOSE:
#   Validates that the artifact naming checker correctly accepts valid
#   filenames and rejects invalid ones per the naming convention defined
#   in spec 31 (0.7.0 Release Package Compatibility).
#
# Validates: Requirements 4.5, 11.4
#
# USAGE:
#   bash tools/release/gates/test_artifact_naming.sh
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
CHECK_SCRIPT="$SCRIPT_DIR/check_artifact_naming.sh"

PASS_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Colors (if terminal supports them)
if [ -t 1 ]; then
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
    if [ -n "$detail" ]; then
        printf "        Detail: %s\n" "$detail" >&2
    fi
    return 0
}

# assert_exit_code runs the check script with the given filename and
# verifies the exit code matches the expected value.
# Arguments: $1=test_name, $2=filename, $3=expected_exit_code
assert_exit_code() {
    local test_name="$1"
    local filename="$2"
    local expected="$3"

    local actual
    bash "$CHECK_SCRIPT" "$filename" >/dev/null 2>/dev/null
    actual=$?

    if [ "$actual" -eq "$expected" ]; then
        pass "$test_name (exit $actual)"
    else
        fail "$test_name" "expected exit $expected, got exit $actual"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Pre-flight check
# ---------------------------------------------------------------------------

if [ ! -f "$CHECK_SCRIPT" ]; then
    printf '[ERROR] check_artifact_naming.sh not found at: %s\n' "$CHECK_SCRIPT" >&2
    exit 1
fi

if [ ! -x "$CHECK_SCRIPT" ] && ! bash -n "$CHECK_SCRIPT" 2>/dev/null; then
    printf '[ERROR] check_artifact_naming.sh has syntax errors\n' >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

printf '========================================================================\n' >&2
printf ' Unit Tests: check_artifact_naming.sh (artifact naming validator)\n' >&2
printf ' Validates: Requirements 4.5, 11.4\n' >&2
printf '========================================================================\n' >&2
printf '\n'

# --- Valid filenames (should exit 0) ---

printf 'Test group: Valid filenames (expect exit 0)\n'

assert_exit_code \
    "valid DEB amd64" \
    "nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_amd64.deb" \
    0

assert_exit_code \
    "valid DEB arm64" \
    "nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_arm64.deb" \
    0

assert_exit_code \
    "valid RPM x86_64" \
    "nginx-module-markdown-for-agents-0.7.0-nginx1.26.3-1.x86_64.rpm" \
    0

assert_exit_code \
    "valid RPM aarch64" \
    "nginx-module-markdown-for-agents-0.7.0-nginx1.26.3-1.aarch64.rpm" \
    0

assert_exit_code \
    "valid DEB with different version" \
    "nginx-module-markdown-for-agents_1.2.3_nginx-1.27.0_amd64.deb" \
    0

assert_exit_code \
    "valid RPM with different version" \
    "nginx-module-markdown-for-agents-1.2.3-nginx1.27.0-1.x86_64.rpm" \
    0

printf '\n'

# --- Invalid filenames (should exit 1) ---

printf 'Test group: Invalid filenames (expect exit 1)\n'

assert_exit_code \
    "missing NGINX version in DEB" \
    "nginx-module-markdown-for-agents_0.7.0_amd64.deb" \
    1

assert_exit_code \
    "generic name (no version binding)" \
    "nginx-module-markdown_0.7.0_amd64.deb" \
    1

assert_exit_code \
    "missing NGINX version in RPM" \
    "nginx-module-markdown-for-agents-0.7.0-1.x86_64.rpm" \
    1

assert_exit_code \
    "wrong project name in DEB" \
    "nginx-markdown_0.7.0_nginx-1.26.3_amd64.deb" \
    1

assert_exit_code \
    "unsupported DEB architecture" \
    "nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_i386.deb" \
    1

assert_exit_code \
    "unsupported RPM architecture" \
    "nginx-module-markdown-for-agents-0.7.0-nginx1.26.3-1.i686.rpm" \
    1

assert_exit_code \
    "unrecognized extension" \
    "nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_amd64.tar.gz" \
    1

assert_exit_code \
    "DEB with RPM naming style" \
    "nginx-module-markdown-for-agents-0.7.0-nginx1.26.3-1.amd64.deb" \
    1

assert_exit_code \
    "RPM with DEB naming style" \
    "nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_x86_64.rpm" \
    1

printf '\n'

# --- Usage / help (should exit 0 or 2) ---

printf 'Test group: Usage and help\n'

assert_exit_code \
    "--help flag exits 0" \
    "--help" \
    0

# No arguments → exit 2
local_exit=0
bash "$CHECK_SCRIPT" >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 2 ]; then
    pass "no arguments exits 2"
else
    fail "no arguments exits 2" "expected exit 2, got exit $local_exit"
fi

printf '\n'

# --- Multiple filenames (mixed valid/invalid → exit 1) ---

printf 'Test group: Multiple filenames\n'

local_exit=0
bash "$CHECK_SCRIPT" \
    "nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_amd64.deb" \
    "nginx-module-markdown-for-agents-0.7.0-nginx1.26.3-1.x86_64.rpm" \
    >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 0 ]; then
    pass "multiple valid filenames exits 0"
else
    fail "multiple valid filenames exits 0" "expected exit 0, got exit $local_exit"
fi

local_exit=0
bash "$CHECK_SCRIPT" \
    "nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_amd64.deb" \
    "bad-name.deb" \
    >/dev/null 2>/dev/null || local_exit=$?
if [ "$local_exit" -eq 1 ]; then
    pass "mixed valid+invalid filenames exits 1"
else
    fail "mixed valid+invalid filenames exits 1" "expected exit 1, got exit $local_exit"
fi

printf '\n'

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf '========================================================================\n'
printf ' Results: %d passed, %d failed\n' "$PASS_COUNT" "$FAIL_COUNT"
printf '========================================================================\n'

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi

exit 0
