#!/usr/bin/env bash
#
# test_detect_volatile_atomic.sh - Unit tests for the volatile/atomic detector.
#
# Validates Rule 42 detector coverage: volatile with and without
# justification comments, plus direct __atomic_* usage.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_volatile_atomic.sh"

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

printf 'Unit Tests: detect_volatile_atomic.sh\n'

# Test 1: Syntax check
if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

# Create temp source directory
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/volatile-detector.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT
src_dir="${tmp_dir}/components/nginx-module/src"
mkdir -p "${src_dir}"

# Test 2: Clean file (no volatile) -> PASS
cat >"${src_dir}/clean.c" <<'C'
#include <stdio.h>
int main(void) { return 0; }
C

output_file="${tmp_dir}/clean.out"
exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "no volatile -> exit 0"
else
    fail "no volatile -> exit 0" "got exit ${exit_code}"
fi

# Test 3: volatile WITH justification -> PASS
cat >"${src_dir}/justified.c" <<'C'
/* single-threaded event loop: compiler barrier only */
volatile int flag = 0;
C

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "justified volatile -> exit 0"
else
    fail "justified volatile -> exit 0" "got exit ${exit_code}"
fi

# Test 4: volatile WITHOUT justification -> FAIL
cat >"${src_dir}/unjustified.c" <<'C'
int foo(void) {
    volatile int x = 42;
    return x;
}
C
rm -f "${src_dir}/justified.c"

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -ne 0 ]] && grep -q 'VIOLATION' "${output_file}"; then
    pass "unjustified volatile -> exit 1 + VIOLATION"
else
    fail "unjustified volatile -> exit 1 + VIOLATION" "got exit ${exit_code}"
fi

# Test 5: direct __atomic_* usage -> FAIL
rm -f "${src_dir}/unjustified.c"
cat >"${src_dir}/atomic.c" <<'C'
void publish(int *dst, int *src) {
    __atomic_store(dst, src, __ATOMIC_RELEASE);
}
C

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -ne 0 ]] && grep -q '__atomic_' "${output_file}"; then
    pass "direct __atomic_* -> exit 1 + VIOLATION"
else
    fail "direct __atomic_* -> exit 1 + VIOLATION" "got exit ${exit_code}"
fi

# Summary
printf '\n  Results: %d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
exit 0
