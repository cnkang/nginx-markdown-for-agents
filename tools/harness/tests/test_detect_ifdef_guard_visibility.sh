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

# Test 4: Multiline call to a guarded function outside the guard -> FAIL
# The call spans lines (``fn(\n arg\n);``); it must be detected as an
# out-of-guard reference, not misclassified as a split-line definition
# (regression for the split_definition_pattern fix).
cat >"${src_dir}/multi_call.c" <<'C'
#include "header.h"

void use_multiline_outside_guard(void) {
    ngx_http_markdown_reason_guarded(
        NULL
    );
}
C

output_file="${tmp_dir}/multicall.out"
${DETECTOR} "${src_dir}/header.h" "${src_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] \
   && grep -q "ngx_http_markdown_reason_guarded" "${output_file}"; then
    pass "bad: multiline call outside guard detected"
else
    fail "bad: multiline call outside guard detected" \
        "expected exit 1 with reference, got ${exit_code}"
    cat "${output_file}" >&2
fi

# Test 5: Guarded-only definition behind an unguarded declaration, called
# outside the guard -> FAIL (definition-collection regression fixture)
cat >"${src_dir}/def_header.h" <<'H'
const ngx_str_t *ngx_http_markdown_reason_guarded_def(void);
H

cat >"${src_dir}/def_impl.c" <<'C'
#include "def_header.h"

#ifdef MARKDOWN_STREAMING_ENABLED
const ngx_str_t *ngx_http_markdown_reason_guarded_def(void) {
    return NULL;
}
#endif
C

cat >"${src_dir}/def_call.c" <<'C'
#include "def_header.h"

void use_guarded_def(void) {
    const ngx_str_t *r = ngx_http_markdown_reason_guarded_def();
}
C

output_file="${tmp_dir}/def.out"
${DETECTOR} "${src_dir}/def_header.h" "${src_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] \
   && grep -q "ngx_http_markdown_reason_guarded_def" "${output_file}"; then
    pass "bad: guarded-only definition (unguarded declaration) referenced outside guard detected"
else
    fail "bad: guarded-only definition (unguarded declaration) referenced outside guard detected" \
        "expected exit 1 with reference, got ${exit_code}"
    cat "${output_file}" >&2
fi

rm -f "${src_dir}/def_impl.c" "${src_dir}/def_call.c" "${src_dir}/def_header.h"

# Test 6: Guarded definition plus an equivalent feature-disabled definition,
# called outside the guard -> PASS (feature-disabled definition available)
cat >"${src_dir}/dual_header.h" <<'H'
const ngx_str_t *ngx_http_markdown_reason_dual_def(void);
H

cat >"${src_dir}/dual_impl.c" <<'C'
#include "dual_header.h"

#ifdef MARKDOWN_STREAMING_ENABLED
const ngx_str_t *ngx_http_markdown_reason_dual_def(void) {
    return NULL;
}
#else
const ngx_str_t *ngx_http_markdown_reason_dual_def(void) {
    return NULL;
}
#endif
C

cat >"${src_dir}/dual_call.c" <<'C'
#include "dual_header.h"

void use_dual_def(void) {
    const ngx_str_t *r = ngx_http_markdown_reason_dual_def();
}
C

output_file="${tmp_dir}/dual.out"
${DETECTOR} "${src_dir}/dual_header.h" "${src_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "clean: guarded definition with feature-disabled twin referenced outside guard"
else
    fail "clean: guarded definition with feature-disabled twin referenced outside guard" \
        "expected exit 0, got ${exit_code}"
    cat "${output_file}" >&2
fi

# Remove the dual fixtures so later tests scan a clean directory (same
# cleanup performed after Test 5).
rm -f "${src_dir}/dual_impl.c" "${src_dir}/dual_call.c" "${src_dir}/dual_header.h"

# Test 7: nginx-style definition whose signature closes on its own line and
# the opening brace follows on the next line, called outside the guard ->
# FAIL (split-signature definition-collection regression fixture).
cat >"${src_dir}/split_header.h" <<'H'
const ngx_str_t *ngx_http_markdown_reason_split_def(void);
H

cat >"${src_dir}/split_impl.c" <<'C'
#include "split_header.h"

#ifdef MARKDOWN_STREAMING_ENABLED
const ngx_str_t *
ngx_http_markdown_reason_split_def(void)
{
    return NULL;
}
#endif
C

cat >"${src_dir}/split_call.c" <<'C'
#include "split_header.h"

void use_split_def(void) {
    const ngx_str_t *r = ngx_http_markdown_reason_split_def();
}
C

output_file="${tmp_dir}/split.out"
${DETECTOR} "${src_dir}/split_header.h" "${src_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] \
   && grep -q "ngx_http_markdown_reason_split_def" "${output_file}"; then
    pass "bad: split-signature guarded definition referenced outside guard detected"
else
    fail "bad: split-signature guarded definition referenced outside guard detected" \
        "expected exit 1 with reference, got ${exit_code}"
    cat "${output_file}" >&2
fi

rm -f "${src_dir}/split_impl.c" "${src_dir}/split_call.c" "${src_dir}/split_header.h"

printf '\n%d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    exit 1
fi
exit 0