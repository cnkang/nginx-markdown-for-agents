#!/usr/bin/env bash
#
# test_detect_nosonar_discipline.sh - Unit tests for the NOSONAR discipline detector.
#
# Validates Rule 24 detector: NOSONAR annotations must include a reason.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_nosonar_discipline.sh"

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

printf 'Unit Tests: detect_nosonar_discipline.sh\n'

# Test 1: Syntax check
if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

# Create temp source directory
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/nosonar-detector.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT
src_dir="${tmp_dir}/components/nginx-module/src"
mkdir -p "${src_dir}"

# Test 2: Clean file (NOSONAR with reason) -> PASS
cat >"${src_dir}/clean.c" <<'C'
void test(void) {
    p = accessor(code, &len);
    out_str->data = (u_char *) p; /* NOSONAR: ngx_str_t.data is u_char* per NGINX API */
}
C

output_file="${tmp_dir}/clean.out"
exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "NOSONAR with reason -> exit 0"
else
    fail "NOSONAR with reason -> exit 0" "got exit ${exit_code}: $(cat "${output_file}")"
fi

# Test 3: Bare NOSONAR without reason -> FAIL
cat >"${src_dir}/bare.c" <<'C'
void test(void) {
    p = (u_char *) accessor(code, &len); /* NOSONAR */
}
C
rm -f "${src_dir}/clean.c"

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -ne 0 ]] && grep -q 'VIOLATION' "${output_file}"; then
    pass "bare NOSONAR -> exit 1 + VIOLATION"
else
    fail "bare NOSONAR -> exit 1 + VIOLATION" "got exit ${exit_code}: $(cat "${output_file}")"
fi

# Test 4: NOSONAR with Rule reference -> PASS
cat >"${src_dir}/rule_ref.c" <<'C'
static ngx_int_t visitor(ngx_table_elt_t *h, void *ctx)
    /* NOSONAR: h type dictated by callback typedef (Rule 24) */
{
    return NGX_OK;
}
C
rm -f "${src_dir}/bare.c"

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "NOSONAR with Rule reference -> exit 0"
else
    fail "NOSONAR with Rule reference -> exit 0" "got exit ${exit_code}: $(cat "${output_file}")"
fi

# Test 5: No NOSONAR at all -> PASS
cat >"${src_dir}/none.c" <<'C'
int main(void) { return 0; }
C
rm -f "${src_dir}/rule_ref.c"

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "no NOSONAR -> exit 0"
else
    fail "no NOSONAR -> exit 0" "got exit ${exit_code}: $(cat "${output_file}")"
fi

# Summary
printf '\n  Results: %d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
exit 0