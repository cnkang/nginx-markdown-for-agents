#!/bin/bash
#
# test_detect_c_pure_logic.sh — Unit tests for the C pure-logic detector.
#
# Validates B01.5 detector behaviour: pure-logic candidate detection,
# NGINX-API exclusion, trivial-function and test/stub skipping, advisory vs
# strict (--check) exit codes, and bounded (non-hanging) execution.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_c_pure_logic.sh"

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

    tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/pure-logic-detector.XXXXXX")" || return 1
    mkdir -p "${tmp_dir}/src" || return 1
    printf '%s\n' "${tmp_dir}"
    return 0
}

# Run the detector. Args: src_dir output_file [extra-flag]
run_detector() {
    local src_dir="$1"
    local output_file="$2"
    local flag="${3:-}"

    if [[ -n "${flag}" ]]; then
        bash "${DETECTOR}" "${flag}" "${src_dir}" >"${output_file}" 2>&1
        return $?
    fi
    bash "${DETECTOR}" "${src_dir}" >"${output_file}" 2>&1
    return $?
}

printf 'Unit Tests: detect_c_pure_logic.sh\n'

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

# A pure-logic function: no NGINX API, > MIN_BODY_LINES (5) lines of body.
cat >"${src_dir}/pure.c" <<'C'
ngx_int_t
pure_classifier(int code)
{
    int result = 0;
    if (code > 100) {
        result = 1;
    } else {
        result = 2;
    }
    result = result + code;
    return result;
}
C

# A function that uses an NGINX API — must NOT be a candidate.
cat >"${src_dir}/glued.c" <<'C'
ngx_int_t
glued_handler(ngx_http_request_t *r)
{
    u_char *p;
    p = ngx_palloc(r->pool, 16);
    if (p == NULL) {
        return -1;
    }
    return 0;
}
C

# A trivial pure function (body <= 5 lines) — must NOT be a candidate.
cat >"${src_dir}/trivial.c" <<'C'
int
trivial_add(int a)
{
    return a + 1;
}
C

# Advisory mode: exit 0, pure_classifier flagged, others not.
output_file="${tmp_dir}/advisory.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "advisory mode exits 0"
else
    fail "advisory mode exits 0" "exit=${exit_code}"
fi

if grep -q "pure.c::pure_classifier" "${output_file}"; then
    pass "flags pure-logic function with no NGINX API"
else
    fail "flags pure-logic function with no NGINX API" \
        "output=$(tr '\n' ' ' <"${output_file}")"
fi

if ! grep -q "glued.c::glued_handler" "${output_file}"; then
    pass "ignores function that calls an NGINX API"
else
    fail "ignores function that calls an NGINX API" \
        "glued_handler should not be a candidate"
fi

if ! grep -q "trivial.c::trivial_add" "${output_file}"; then
    pass "ignores trivial (short) function"
else
    fail "ignores trivial (short) function" \
        "trivial_add should be below the size threshold"
fi

# Strict mode: candidates present -> exit 1.
output_file="${tmp_dir}/strict.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" "--check" || exit_code=$?
if [[ "${exit_code}" -eq 1 ]]; then
    pass "strict --check exits 1 when candidates exist"
else
    fail "strict --check exits 1 when candidates exist" "exit=${exit_code}"
fi

# test_/stub_ files must be skipped even in strict mode.
clean_dir="${tmp_dir}/clean"
mkdir -p "${clean_dir}"
cat >"${clean_dir}/test_pure.c" <<'C'
ngx_int_t
test_pure_helper(int code)
{
    int result = 0;
    if (code > 100) {
        result = 1;
    }
    result = result + code;
    result = result * 2;
    return result;
}
C
cat >"${clean_dir}/stub_pure.c" <<'C'
ngx_int_t
stub_pure_helper(int code)
{
    int result = 0;
    if (code > 100) {
        result = 1;
    }
    result = result + code;
    result = result * 2;
    return result;
}
C

output_file="${tmp_dir}/clean.out"
exit_code=0
run_detector "${clean_dir}" "${output_file}" "--check" || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "strict --check exits 0 when only test/stub files present"
else
    fail "strict --check exits 0 when only test/stub files present" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

# Missing directory is a graceful skip (exit 0), never a hang or failure.
output_file="${tmp_dir}/missing.out"
exit_code=0
run_detector "${tmp_dir}/does-not-exist" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "missing directory is a graceful skip (exit 0)"
else
    fail "missing directory is a graceful skip (exit 0)" "exit=${exit_code}"
fi

# Unknown option is a usage error (exit 1).
output_file="${tmp_dir}/badopt.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" "--bogus" || exit_code=$?
if [[ "${exit_code}" -eq 1 ]]; then
    pass "unknown option is a usage error (exit 1)"
else
    fail "unknown option is a usage error (exit 1)" "exit=${exit_code}"
fi

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0
