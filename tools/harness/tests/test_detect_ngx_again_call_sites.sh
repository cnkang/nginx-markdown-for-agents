#!/bin/bash
#
# test_detect_ngx_again_call_sites.sh — Unit tests for the NGX_AGAIN
# call-site branch detector.
#
# Validates that the detector:
#   - flags a call site that folds NGX_AGAIN into the error path
#     (rc != NGX_OK && rc != NGX_AGAIN is the correct pattern)
#   - flags an immediate return after the call without NGX_AGAIN mention
#   - accepts a call site with an explicit NGX_AGAIN branch
#   - ignores header declarations and the API definition itself

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_ngx_again_call_sites.sh"

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

    tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ngx-again-callsite.XXXXXX")" || return 1
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

printf 'Unit Tests: detect_ngx_again_call_sites.sh\n'

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

# 1. Violation: NGX_AGAIN folded into error path (rc != NGX_OK).
cat >"${src_dir}/fold_error.c" <<'C'
static ngx_int_t
caller(ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_forward_headers(r, ctx);
    if (rc != NGX_OK) {
        return rc;
    }

    return ngx_http_next_body_filter(r, NULL);
}
C

output_file="${tmp_dir}/fold.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -ne 0 ]] && grep -q "forward_headers" "${output_file}"; then
    pass "flags NGX_AGAIN folded into error path"
else
    fail "flags NGX_AGAIN folded into error path" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

# 2. Violation: immediate return after call without NGX_AGAIN mention.
rm -f "${src_dir}/fold_error.c"
cat >"${src_dir}/immediate_return.c" <<'C'
static ngx_int_t
caller(ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_streaming_commit(r, ctx);
    return rc;
}
C

output_file="${tmp_dir}/immed.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -ne 0 ]] && grep -q "streaming_commit" "${output_file}"; then
    pass "flags immediate return without NGX_AGAIN branch"
else
    fail "flags immediate return without NGX_AGAIN branch" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

# 3. Clean: explicit NGX_AGAIN branch (rc != NGX_OK && rc != NGX_AGAIN).
rm -f "${src_dir}/immediate_return.c"
cat >"${src_dir}/clean_branch.c" <<'C'
static ngx_int_t
caller(ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t  rc;

    rc = ngx_http_markdown_forward_headers(r, ctx);
    if (rc != NGX_OK && rc != NGX_AGAIN) {
        return rc;
    }

    return ngx_http_next_body_filter(r, NULL);
}
C

output_file="${tmp_dir}/clean.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "accepts explicit NGX_AGAIN branch"
else
    fail "accepts explicit NGX_AGAIN branch" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

# 4. Clean: header declaration and definition are ignored.
rm -f "${src_dir}/clean_branch.c"
cat >"${src_dir}/defs.c" <<'C'
ngx_int_t ngx_http_markdown_forward_headers(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);

ngx_int_t
ngx_http_markdown_forward_headers(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    return ngx_http_next_header_filter(r);
}
C

output_file="${tmp_dir}/defs.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "ignores declaration and definition"
else
    fail "ignores declaration and definition" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0
