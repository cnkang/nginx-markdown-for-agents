#!/bin/bash
#
# test_detect_ffi_struct_init.sh — Unit tests for the FFI init detector.
#
# Validates Rule 15 detector coverage for both explicit struct declarations
# and typedef alias declarations.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_ffi_struct_init.sh"

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

make_src_dir() {
    local tmp_dir

    tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ffi-init-detector.XXXXXX")" || return 1
    mkdir -p "${tmp_dir}/src" || return 1
    printf '%s\n' "${tmp_dir}"
    return 0
}

run_detector() {
    local src_dir="$1"
    local output_file="$2"

    bash "${DETECTOR}" "${src_dir}" >"${output_file}" 2>&1
    return $?
}

printf 'Unit Tests: detect_ffi_struct_init.sh\n'

if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

tmp_dir="$(make_src_dir)" || {
    fail "create temp fixture directory" "mktemp or mkdir failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT
src_dir="${tmp_dir}/src"

cat >"${src_dir}/struct_violation.c" <<'C'
void f(void) {
    struct MarkdownResult result;
    ngx_memzero(&result, sizeof(result));
}
C

output_file="${tmp_dir}/struct.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -ne 0 ]] && grep -q "MarkdownResult variable 'result'" "${output_file}"; then
    pass "detects struct-name declaration with sizeof(var) zeroing"
else
    fail "detects struct-name declaration with sizeof(var) zeroing" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

rm -f "${src_dir}/struct_violation.c"
cat >"${src_dir}/typedef_violation.c" <<'C'
void f(void) {
    MarkdownResult result;
    ngx_memzero(&result, sizeof(result));
}
C

output_file="${tmp_dir}/typedef.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -ne 0 ]] && grep -q "MarkdownResult variable 'result'" "${output_file}"; then
    pass "detects typedef alias declaration with sizeof(var) zeroing"
else
    fail "detects typedef alias declaration with sizeof(var) zeroing" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

rm -f "${src_dir}/typedef_violation.c"
cat >"${src_dir}/clean_helper.c" <<'C'
void f(void) {
    MarkdownResult result;
    markdown_result_init(&result);
}
C
cat >"${src_dir}/comment_only.h" <<'C'
/*
 * C callers MUST use markdown_options_init() instead of
 * memset(&opts, 0, sizeof(opts)).
 */
void f(void) {
    MarkdownOptions opts;
    markdown_options_init(&opts);
}
C

output_file="${tmp_dir}/clean.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "allows typedef alias declaration initialized through helper"
else
    fail "allows typedef alias declaration initialized through helper" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0
