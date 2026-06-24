#!/usr/bin/env bash
#
# test_detect_cwe190_casts.sh - Unit tests for the CWE-190 detector.
#
# Validates that allowlist entries are parsed safely even when the regex
# contains POSIX character-class colons.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_cwe190_casts.sh"

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

printf 'Unit Tests: detect_cwe190_casts.sh\n'

if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/cwe190-detector.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT

src_dir="${tmp_dir}/components/nginx-module/src"
mkdir -p "${src_dir}"

cat >"${src_dir}/ngx_http_markdown_config_handlers_impl.h" <<'C'
void test(void) {
    if (raw>NGX_MAX_SIZE_T_VALUE) *out=(size_t)raw;
}
C

temp_detector="${tmp_dir}/detect_cwe190_casts.sh"
cp "${DETECTOR}" "${temp_detector}"
perl -0pi -e 's@\Qraw>NGX_MAX_SIZE_T_VALUE.*value=.size_t.raw\E@raw>NGX_MAX_SIZE_T_VALUE[[:space:]]*.*out=.size_t.raw@' \
    "${temp_detector}"

output_file="${tmp_dir}/guarded.out"
exit_code=0
(cd "${tmp_dir}" && bash "${temp_detector}" "${src_dir}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]] && grep -q 'allowlisted' "${output_file}"; then
    pass "tab-delimited allowlist preserves POSIX character classes -> exit 0"
else
    fail "tab-delimited allowlist preserves POSIX character classes -> exit 0" \
        "got exit ${exit_code}: $(cat "${output_file}")"
fi

printf '\n  Results: %d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
exit 0
