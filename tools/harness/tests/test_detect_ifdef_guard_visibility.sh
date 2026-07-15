#!/usr/bin/env bash
#
# test_detect_ifdef_guard_visibility.sh - Unit tests for #ifdef guard visibility.
#
# Validates that functions declared inside #ifdef MARKDOWN_STREAMING_ENABLED
# are not referenced outside that guard.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="bash ${SCRIPT_DIR}/../detect_ifdef_guard_visibility.sh"

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

printf 'Unit Tests: detect_ifdef_guard_visibility.sh\n'

# Create temp fixture directory
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ifdef-guard.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT
src_dir="${tmp_dir}/src"
mkdir -p "${src_dir}"

# Test 1: Clean - guarded function only referenced inside guard -> PASS
cat >"${src_dir}/header.h" <<'H'
#ifdef MARKDOWN_STREAMING_ENABLED
const ngx_str_t *ngx_http_markdown_reason_guarded(void);
#endif
const ngx_str_t *ngx_http_markdown_reason_safe(void);
H

cat >"${src_dir}/impl.c" <<'C'
#include "header.h"

#ifdef MARKDOWN_STREAMING_ENABLED
void use_guarded(void) {
    const ngx_str_t *r = ngx_http_markdown_reason_guarded();
}
#endif

void use_safe(void) {
    const ngx_str_t *r = ngx_http_markdown_reason_safe();
}
C

output_file="${tmp_dir}/clean.out"
${DETECTOR} "${src_dir}/header.h" "${src_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "clean: guarded function only used inside guard"
else
    fail "clean: guarded function only used inside guard" "exit code ${exit_code}"
    cat "${output_file}" >&2
fi

# Test 2: Guarded function referenced outside guard -> FAIL
cat >"${src_dir}/bad.c" <<'C'
#include "header.h"

void use_outside_guard(void) {
    const ngx_str_t *r = ngx_http_markdown_reason_guarded();
}
C

output_file="${tmp_dir}/bad.out"
${DETECTOR} "${src_dir}/header.h" "${src_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "bad: guarded function used outside guard detected"
else
    fail "bad: guarded function used outside guard detected" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi

# Remove the bad file for next test
rm -f "${src_dir}/bad.c"

# Test 3: No guarded functions -> PASS
cat >"${src_dir}/no_guard.h" <<'H'
const ngx_str_t *ngx_http_markdown_reason_all(void);
H

output_file="${tmp_dir}/noguard.out"
${DETECTOR} "${src_dir}/no_guard.h" "${src_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "no guarded functions passes"
else
    fail "no guarded functions passes" "exit code ${exit_code}"
    cat "${output_file}" >&2
fi

printf '\n%d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    exit 1
fi
exit 0