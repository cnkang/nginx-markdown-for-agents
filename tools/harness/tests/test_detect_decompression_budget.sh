#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DETECTOR="${REPO_ROOT}/tools/harness/detect_decompression_budget.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/decompression-budget-test.XXXXXX")"

cleanup() {
    rm -rf "$TEST_ROOT"
    return 0
}
trap cleanup EXIT

fail() {
    local message="$1"

    printf 'FAIL: %s\n' "$message" >&2
    exit 1
}

run_detector() {
    local fixture_dir="$1"
    local output_file="$2"
    local rc=0

    bash "$DETECTOR" "$fixture_dir" >"$output_file" 2>&1 || rc=$?
    return "$rc"
}

clean_dir="${TEST_ROOT}/clean"
unsafe_dir="${TEST_ROOT}/unsafe"
comment_dir="${TEST_ROOT}/comments"
mkdir -p "$clean_dir" "$unsafe_dir" "$comment_dir"

printf '%s\n' \
    'static ngx_int_t' \
    'ngx_http_markdown_decompress_bounded(size_t output_len,' \
    '    size_t max_output)' \
    '{' \
    '    if (output_len > max_output) {' \
    '        return -1;' \
    '    }' \
    '    return ngx_alloc(output_len, NULL) != NULL ? 0 : -1;' \
    '}' >"${clean_dir}/clean.c"

printf '%s\n' \
    'static ngx_int_t' \
    'ngx_http_markdown_decompress_unbounded(size_t output_len)' \
    '{' \
    '    return ngx_alloc(output_len, NULL) != NULL ? 0 : -1;' \
    '}' >"${unsafe_dir}/unsafe.c"

printf '%s\n' \
    '/* ngx_http_markdown_decompress_comment_only() uses ngx_alloc(size). */' \
    'static ngx_int_t' \
    'ngx_http_markdown_decompress_comment_only(void)' \
    '{' \
    '    return 0;' \
    '}' >"${comment_dir}/comments.c"

clean_output="${TEST_ROOT}/clean.out"
if ! run_detector "$clean_dir" "$clean_output"; then
    fail "budgeted allocation fixture was rejected"
fi

unsafe_output="${TEST_ROOT}/unsafe.out"
unsafe_rc=0
run_detector "$unsafe_dir" "$unsafe_output" || unsafe_rc=$?
if [[ "$unsafe_rc" -ne 1 ]]; then
    fail "unbounded allocation fixture did not fail with status 1"
fi
if ! grep -q "ngx_http_markdown_decompress_unbounded" "$unsafe_output"; then
    fail "unbounded allocation fixture did not identify its function"
fi

comment_output="${TEST_ROOT}/comments.out"
if ! run_detector "$comment_dir" "$comment_output"; then
    fail "comment-only allocation text was treated as executable code"
fi

printf 'OK: decompression budget detector regression cases passed\n'
exit 0
