#!/usr/bin/env bash
#
# test_detect_hardcoded_http_status.sh - Unit tests for hardcoded HTTP status detector.
#
# Validates that reject paths use conf->error_status instead of hardcoded 502.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="bash ${SCRIPT_DIR}/../detect_hardcoded_http_status.sh"

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

printf 'Unit Tests: detect_hardcoded_http_status.sh\n'

# Create temp fixture directory
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/hardcoded-status.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT
src_dir="${tmp_dir}/src"
mkdir -p "${src_dir}"

# Test 1: Clean - using conf->error_status -> PASS
cat >"${src_dir}/clean.c" <<'C'
ngx_int_t
ngx_http_markdown_reject_or_fail_open(ngx_conf_t *cf)
{
    if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
        return (ngx_int_t) conf->error_status;
    }
    return NGX_OK;
}
C

output_file="${tmp_dir}/clean.out"
${DETECTOR} "${src_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "clean: using conf->error_status passes"
else
    fail "clean: using conf->error_status passes" "exit code ${exit_code}"
    cat "${output_file}" >&2
fi

# Test 2: Hardcoded 502 in reject path -> WARN
cat >"${src_dir}/reject.c" <<'C'
ngx_int_t
ngx_http_markdown_reject_or_fail_open(ngx_conf_t *cf)
{
    if (action == REJECT_502) {
        return NGX_HTTP_BAD_GATEWAY;
    }
    return NGX_OK;
}
C

output_file="${tmp_dir}/reject.out"
${DETECTOR} "${src_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    # Advisory mode: exit 0 but with WARN output
    if grep -q "WARN" "${output_file}"; then
        pass "hardcoded 502 in reject path detected (advisory)"
    else
        fail "hardcoded 502 in reject path detected" "no WARN in output"
        cat "${output_file}" >&2
    fi
else
    fail "hardcoded 502 in reject path detected" "unexpected exit code ${exit_code}"
    cat "${output_file}" >&2
fi

# Remove reject.c for next test
rm -f "${src_dir}/reject.c"

# Test 3: Empty directory -> PASS
empty_dir="${tmp_dir}/empty"
mkdir -p "${empty_dir}"
${DETECTOR} "${empty_dir}" >"${output_file}" 2>&1
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