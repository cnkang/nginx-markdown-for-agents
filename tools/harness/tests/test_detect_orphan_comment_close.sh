#!/usr/bin/env bash
#
# test_detect_orphan_comment_close.sh - Unit tests for orphan */ detector.
#
# Validates the detector that catches missing /* openings that leave
# bare */ and break C compilation.

set -uo pipefail
# NOTE: intentionally NOT using set -e because we need to capture non-zero
# exit codes from the detector without aborting the test script.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="python3 ${SCRIPT_DIR}/../detect_orphan_comment_close.py"

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    local msg="$1"
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '  PASS: %s\n' "${msg}"
    return 0
}

fail() {
    local msg="$1"
    local detail="${2:-}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '  FAIL: %s\n' "${msg}" >&2
    if [[ -n "${detail}" ]]; then
        printf '        Detail: %s\n' "${detail}" >&2
    fi
    return 0
}

printf 'Unit Tests: detect_orphan_comment_close.py\n'

# Create temp fixture base
tmp_base="$(mktemp -d "${TMPDIR:-/tmp}/orphan-detector.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_base}"' EXIT

# Test 1: Clean file (no orphans) -> PASS
src_dir="${tmp_base}/clean"
mkdir -p "${src_dir}"
cat >"${src_dir}/test.c" <<'C'
/* This is a valid comment */
void foo(void) {
    int x = 1; /* inline comment */
    /*
     * Multi-line comment
     */
    x = 2;
}
C

${DETECTOR} "${src_dir}" >"${tmp_base}/clean.out" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "clean file passes"
else
    fail "clean file passes" "exit code ${exit_code}"
    cat "${tmp_base}/clean.out" >&2
fi

# Test 2: File with orphan */ -> FAIL
src_dir="${tmp_base}/orphan"
mkdir -p "${src_dir}"
cat >"${src_dir}/test.c" <<'C'
/*
 * Valid comment block
 */
void bar(void) {
    int y = 2;

    * This is an orphan */
    y = 3;
}
C

${DETECTOR} "${src_dir}" >"${tmp_base}/orphan.out" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "orphan */ detected"
else
    fail "orphan */ detected" "expected exit 1, got ${exit_code}"
    cat "${tmp_base}/orphan.out" >&2
fi

# Test 3: String containing */ -> PASS (not an orphan)
src_dir="${tmp_base}/string"
mkdir -p "${src_dir}"
cat >"${src_dir}/test.c" <<'C'
void baz(void) {
    const char *s = "text */ more";
    int z = 3;
}
C

${DETECTOR} "${src_dir}" >"${tmp_base}/string.out" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "string with */ passes (not orphan)"
else
    fail "string with */ passes (not orphan)" "exit code ${exit_code}"
    cat "${tmp_base}/string.out" >&2
fi

# Test 4: Empty directory -> PASS
src_dir="${tmp_base}/empty"
mkdir -p "${src_dir}"
${DETECTOR} "${src_dir}" >"${tmp_base}/empty.out" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "empty directory passes"
else
    fail "empty directory passes" "exit code ${exit_code}"
fi

printf '\n%d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    exit 1
fi
exit 0