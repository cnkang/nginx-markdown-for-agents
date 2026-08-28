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

ffi_src_dir="${tmp_dir}/ffi-src"
mkdir -p "${ffi_src_dir}"
cat >"${ffi_src_dir}/ngx_http_markdown_conversion_impl.h" <<'C'
void test(void) {
    if (decision.base_url_len > out_cap) return;
    log((size_t) decision.base_url_len);
}
C

output_file="${tmp_dir}/ffi-width.out"
exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}" "${ffi_src_dir}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]] \
    && grep -q 'same-width cast after out_cap guard' "${output_file}" \
    && ! grep -q 'WARNING.*base_url_len' "${output_file}"
then
    pass "guarded uintptr_t FFI length is not reported as ssize_t -> exit 0"
else
    fail "guarded uintptr_t FFI length is not reported as ssize_t -> exit 0" \
        "got exit ${exit_code}: $(cat "${output_file}")"
fi

# ── Fixture: clean-1 (ngx_uint_t with overflow precheck → false positive on unfixed) ──
# Reproduces the ngx_http_markdown_conditional.c:700 false positive:
# count is ngx_uint_t (unsigned), and there is an overflow guard via
# ((size_t) -1) / sizeof(...) precheck.  The unfixed detector has no type
# awareness and cannot recognize the division precheck as a guard.

fixture_clean1_dir="${tmp_dir}/fixture-clean1/components/nginx-module/src"
mkdir -p "${fixture_clean1_dir}"

cat >"${fixture_clean1_dir}/test_clean1.c" <<'C'
#include <stddef.h>
typedef unsigned int ngx_uint_t;
typedef int ngx_int_t;
#define NGX_ERROR -1

static int
snapshot_list(void *pool, void *parts, int count_arg)
{
    ngx_uint_t count = (ngx_uint_t) count_arg;

    if ((size_t) count > ((size_t) -1) / sizeof(void *)) {
        return NGX_ERROR;
    }

    size_t alloc_size = (size_t) count * sizeof(void *);
    return 0;
}
C

output_file="${tmp_dir}/clean1.out"
exit_code=0
(cd "${tmp_dir}/fixture-clean1" && bash "${DETECTOR}" "${fixture_clean1_dir}") >"${output_file}" 2>&1 || exit_code=$?
# After the type-awareness fix, the detector correctly identifies count as
# ngx_uint_t (unsigned) and skips the guard check — no WARNING expected.
if [[ "${exit_code}" -eq 0 ]] && ! grep -q 'WARNING' "${output_file}" \
    && grep -q 'unsigned source type' "${output_file}"; then
    pass "clean-1: detector correctly identifies unsigned ngx_uint_t (no false positive)"
else
    if grep -q 'WARNING' "${output_file}"; then
        fail "clean-1: detector still emits false positive WARNING on unsigned ngx_uint_t" \
            "got exit ${exit_code}: $(cat "${output_file}")"
    else
        fail "clean-1: unexpected detector behavior" \
            "got exit ${exit_code}: $(cat "${output_file}")"
    fi
fi

# ── Fixture: clean-2 (cross-function same-name variable) ──
# func_a has ssize_t count (signed), func_b has ngx_uint_t count (unsigned).
# The unfixed detector has no function-scope boundary, so seeing 'ssize_t count'
# anywhere in the file may affect the judgment of func_b's (size_t) count.

fixture_clean2_dir="${tmp_dir}/fixture-clean2/components/nginx-module/src"
mkdir -p "${fixture_clean2_dir}"

cat >"${fixture_clean2_dir}/test_clean2.c" <<'C'
typedef long ssize_t;
typedef unsigned int ngx_uint_t;

static int func_a(ssize_t count) {
    return (int) count;
}

static int func_b(ngx_uint_t count) {
    size_t val = (size_t) count;
    return (int) val;
}
C

output_file="${tmp_dir}/clean2.out"
exit_code=0
(cd "${tmp_dir}/fixture-clean2" && bash "${DETECTOR}" "${fixture_clean2_dir}") >"${output_file}" 2>&1 || exit_code=$?
# After the type-awareness fix with function-scope boundaries, the detector
# correctly identifies func_b's count as ngx_uint_t (unsigned) within its
# own function window, independent of func_a's ssize_t count.
if [[ "${exit_code}" -eq 0 ]] && ! grep -q 'WARNING' "${output_file}" \
    && grep -q 'unsigned source type' "${output_file}"; then
    pass "clean-2: detector correctly scopes type to function boundary (no false positive)"
else
    if grep -q 'WARNING' "${output_file}"; then
        fail "clean-2: detector still emits false positive on func_b's unsigned count" \
            "got exit ${exit_code}: $(cat "${output_file}")"
    else
        fail "clean-2: unexpected detector behavior" \
            "got exit ${exit_code}: $(cat "${output_file}")"
    fi
fi

# ── Fixture: clean-3 (nearest declaration in a nested scope) ──
# The outer parameter and the inner shadow use the same identifier.  The cast
# is inside the inner scope, so its nearest declaration is the unsigned one.

fixture_clean3_dir="${tmp_dir}/fixture-clean3/components/nginx-module/src"
mkdir -p "${fixture_clean3_dir}"

cat >"${fixture_clean3_dir}/test_clean3.c" <<'C'
typedef unsigned int ngx_uint_t;
typedef int ngx_int_t;

static size_t
shadowed_value(ngx_int_t value)
{
    {
        ngx_uint_t value = 1;
        return (size_t) value;
    }
}
C

output_file="${tmp_dir}/clean3.out"
exit_code=0
(cd "${tmp_dir}/fixture-clean3" && bash "${DETECTOR}" "${fixture_clean3_dir}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]] && ! grep -q 'WARNING' "${output_file}" \
    && grep -q 'unsigned source type' "${output_file}"; then
    pass "clean-3: detector uses the nearest nested-scope declaration"
else
    fail "clean-3: nearest nested-scope declaration was not selected" \
        "got exit ${exit_code}: $(cat "${output_file}")"
fi

# ── Fixture: clean-4 (pointer declaration with an unsigned base type) ──
# A declaration such as `size_t *foo` must still classify the cast source as
# unsigned.  The old declaration matcher stopped at the asterisk and treated
# this known type as unknown, producing a false-positive warning.

fixture_clean4_dir="${tmp_dir}/fixture-clean4/components/nginx-module/src"
mkdir -p "${fixture_clean4_dir}"

cat >"${fixture_clean4_dir}/test_clean4.c" <<'C'
typedef unsigned long size_t;

static size_t
pointer_decl(size_t *foo)
{
    return (size_t) foo;
}
C

output_file="${tmp_dir}/clean4.out"
exit_code=0
(cd "${tmp_dir}/fixture-clean4" && bash "${DETECTOR}" "${fixture_clean4_dir}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]] && ! grep -q 'WARNING' "${output_file}" \
    && grep -q 'unsigned source type' "${output_file}"; then
    pass "clean-4: detector recognizes pointer declarations with unsigned base type"
else
    fail "clean-4: pointer declaration was not classified as unsigned" \
        "got exit ${exit_code}: $(cat "${output_file}")"
fi

# ── Fixture: adversarial-wide-and-multiple (do not hide a risky cast) ──
# A line may contain both a signed and a safe cast.  The detector must inspect
# every cast rather than letting the final safe cast suppress the warning.
# uint64_t and uintptr_t also remain fail-closed for narrower size_t targets.

fixture_adv_multi_dir="${tmp_dir}/fixture-adv-multi/components/nginx-module/src"
mkdir -p "${fixture_adv_multi_dir}"

cat >"${fixture_adv_multi_dir}/test_adv_multi.c" <<'C'
typedef int ngx_int_t;
typedef unsigned int ngx_uint_t;
typedef unsigned long long uint64_t;
typedef unsigned long uintptr_t;

static size_t
multiple_casts(ngx_int_t signed_value, ngx_uint_t safe_value,
               uint64_t wide_value, uintptr_t pointer_value)
{
    return (size_t) signed_value + (size_t) safe_value
        + (size_t) wide_value + (size_t) pointer_value;
}
C

output_file="${tmp_dir}/adv-multi.out"
exit_code=0
(cd "${tmp_dir}/fixture-adv-multi" && bash "${DETECTOR}" "${fixture_adv_multi_dir}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]] \
    && grep -q 'WARNING' "${output_file}"; then
    pass "adversarial-wide-and-multiple: every cast is audited fail-closed"
else
    fail "adversarial-wide-and-multiple: risky cast was hidden by a later safe cast" \
        "got exit ${exit_code}: $(cat "${output_file}")"
fi

# ── Fixture: adversarial-1 (true positive — signed without guard) ──
# ngx_int_t n without any guard → must produce WARNING.

fixture_adv1_dir="${tmp_dir}/fixture-adv1/components/nginx-module/src"
mkdir -p "${fixture_adv1_dir}"

cat >"${fixture_adv1_dir}/test_adv1.c" <<'C'
typedef int ngx_int_t;

static size_t
dangerous_cast(ngx_int_t n)
{
    return (size_t) n * sizeof(void *);
}
C

output_file="${tmp_dir}/adv1.out"
exit_code=0
(cd "${tmp_dir}/fixture-adv1" && bash "${DETECTOR}" "${fixture_adv1_dir}") >"${output_file}" 2>&1 || exit_code=$?
if grep -q 'WARNING' "${output_file}"; then
    pass "adversarial-1: detector correctly warns on signed ngx_int_t without guard"
else
    fail "adversarial-1: expected WARNING for unguarded signed→size_t cast" \
        "got exit ${exit_code}: $(cat "${output_file}")"
fi

# ── Fixture: adversarial-2 (NGX_OK keyword collision) ──
# ngx_int_t n with only an unrelated `return NGX_OK` in the 8-line window.
# The FIXED detector requires comparison context (== NGX_OK or != NGX_OK)
# to treat NGX_OK as a guard, so a bare `return NGX_OK;` is correctly
# ignored and the cast is flagged as WARNING (true positive).

fixture_adv2_dir="${tmp_dir}/fixture-adv2/components/nginx-module/src"
mkdir -p "${fixture_adv2_dir}"

cat >"${fixture_adv2_dir}/test_adv2.c" <<'C'
typedef int ngx_int_t;
#define NGX_OK 0

static int
misleading_guard(ngx_int_t n)
{
    if (n == 42) {
        return NGX_OK;
    }
    size_t val = (size_t) n;
    return (int) val;
}
C

output_file="${tmp_dir}/adv2.out"
exit_code=0
(cd "${tmp_dir}/fixture-adv2" && bash "${DETECTOR}" "${fixture_adv2_dir}") >"${output_file}" 2>&1 || exit_code=$?
# After the NGX_OK tightening fix: bare `return NGX_OK;` is no longer
# treated as a guard.  The detector correctly emits WARNING (true positive).
if grep -q 'WARNING' "${output_file}"; then
    pass "adversarial-2: fixed detector correctly warns on NGX_OK keyword collision (true positive)"
else
    fail "adversarial-2: expected WARNING after NGX_OK tightening but got OK" \
        "got exit ${exit_code}: $(cat "${output_file}")"
fi

# ── Fixture: adversarial-3 (unknown type — struct field, fail-closed) ──
# obj->field has unknown type (no local declaration in window).
# Detector must remain fail-closed: WARNING expected.

fixture_adv3_dir="${tmp_dir}/fixture-adv3/components/nginx-module/src"
mkdir -p "${fixture_adv3_dir}"

cat >"${fixture_adv3_dir}/test_adv3.c" <<'C'
struct request {
    long field;
};

static size_t
unknown_source(struct request *obj)
{
    return (size_t) obj->field;
}
C

output_file="${tmp_dir}/adv3.out"
exit_code=0
(cd "${tmp_dir}/fixture-adv3" && bash "${DETECTOR}" "${fixture_adv3_dir}") >"${output_file}" 2>&1 || exit_code=$?
if grep -q 'WARNING' "${output_file}"; then
    pass "adversarial-3: detector correctly warns on unknown-type struct field (fail-closed)"
else
    fail "adversarial-3: expected WARNING for unknown-type source (fail-closed)" \
        "got exit ${exit_code}: $(cat "${output_file}")"
fi

printf '\n  Results: %d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
exit 0
