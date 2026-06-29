#!/bin/bash
#
# test_detect_ffi_panic_safety.sh — Unit tests for the FFI panic-safety detector.
#
# Validates Rule 15 detector coverage for all six classification
# categories: direct_catch, delegated_catch, unsafe_business_logic,
# safe_init_helper, safe_static_lookup, free_helper_without_catch —
# plus strict-mode gating and the #[no_mangle] / #[unsafe(no_mangle)]
# dual recognition.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_ffi_panic_safety.sh"

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

run_detector() {
    local src_dir="$1"
    local output_file="$2"
    local strict="${3:-}"
    if [[ -n "${strict}" ]]; then
        bash "${DETECTOR}" "${src_dir}" --strict >"${output_file}" 2>&1
    else
        bash "${DETECTOR}" "${src_dir}" >"${output_file}" 2>&1
    fi
    return $?
}

printf 'Unit Tests: detect_ffi_panic_safety.sh\n'

if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ffi-panic-detector.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT
src_dir="${tmp_dir}/src"
mkdir -p "${src_dir}"

# ── Fixture: direct_catch (#[unsafe(no_mangle)] + catch_unwind) ──
cat >"${src_dir}/direct.rs" <<'RUST'
#[unsafe(no_mangle)]
pub extern "C" fn md_direct(x: u32) -> u32 {
    let result = std::panic::catch_unwind(|| {
        x + 1
    });
    match result {
        Ok(v) => v,
        Err(_) => 0,
    }
}
RUST

out="${tmp_dir}/direct.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]] && grep -q "direct_catch" "${out}"; then
    pass "classifies #[unsafe(no_mangle)] + catch_unwind as direct_catch"
else
    fail "classifies #[unsafe(no_mangle)] + catch_unwind as direct_catch" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/direct.rs"

# ── Fixture: direct_catch (#[no_mangle] legacy form) ──
cat >"${src_dir}/legacy.rs" <<'RUST'
#[no_mangle]
pub extern "C" fn md_legacy(x: u32) -> u32 {
    let result = std::panic::catch_unwind(|| {
        x + 2
    });
    match result {
        Ok(v) => v,
        Err(_) => 0,
    }
}
RUST

out="${tmp_dir}/legacy.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]] && grep -q "direct_catch" "${out}"; then
    pass "recognizes legacy #[no_mangle] form as direct_catch"
else
    fail "recognizes legacy #[no_mangle] form as direct_catch" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/legacy.rs"

# ── Fixture: delegated_catch (calls a sibling with catch_unwind) ──
cat >"${src_dir}/delegated.rs" <<'RUST'
#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_inner_with_code(x: u32) -> u32 {
    let result = std::panic::catch_unwind(|| x + 1);
    match result { Ok(v) => v, Err(_) => 0 }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_outer(x: u32) -> u32 {
    let r = unsafe { md_inner_with_code(x) };
    r
}
RUST

out="${tmp_dir}/delegated.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]] && grep -q "md_outer: delegated_catch" "${out}"; then
    pass "classifies delegation to catch_unwind sibling as delegated_catch"
else
    fail "classifies delegation to catch_unwind sibling as delegated_catch" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/delegated.rs"

# ── Fixture: delegated_catch NEGATIVE — callee has NO catch ──
cat >"${src_dir}/delegated_bad.rs" <<'RUST'
#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_inner_nocatch(x: u32) -> u32 {
    x + 1
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_outer_bad(x: u32) -> u32 {
    let r = unsafe { md_inner_nocatch(x) };
    r
}
RUST

out="${tmp_dir}/delegated_bad.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
# md_outer_bad must NOT be classified as delegated_catch because the
# callee has no catch_unwind. It should be unknown (no business
# logic keywords).
if grep -q "md_outer_bad: delegated_catch" "${out}"; then
    fail "does not classify delegation to no-catch sibling as delegated_catch" \
        "false positive: md_outer_bad marked delegated_catch"
else
    pass "does not classify delegation to no-catch sibling as delegated_catch"
fi
rm -f "${src_dir}/delegated_bad.rs"

# ── Fixture: safe_init_helper (NULL check + zeroed write) ──
cat >"${src_dir}/init.rs" <<'RUST'
#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_init(opts: *mut Options) {
    if opts.is_null() {
        return;
    }
    unsafe { std::ptr::write(opts, std::mem::zeroed()) };
}
RUST

out="${tmp_dir}/init.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]] && grep -q "md_init: safe_init_helper" "${out}"; then
    pass "classifies NULL check + ptr::write(zeroed) as safe_init_helper"
else
    fail "classifies NULL check + ptr::write(zeroed) as safe_init_helper" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/init.rs"

# ── Fixture: safe_static_lookup (reads static/const field, returns it) ──
cat >"${src_dir}/static_lookup.rs" <<'RUST'
const TOTAL_COUNT: u32 = 18;

#[unsafe(no_mangle)]
pub extern "C" fn md_reason_code_count() -> u32 {
    TOTAL_COUNT
}
RUST

out="${tmp_dir}/static.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]] && grep -q "md_reason_code_count: safe_static_lookup" "${out}"; then
    pass "classifies static const read as safe_static_lookup"
else
    fail "classifies static const read as safe_static_lookup" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/static_lookup.rs"

# ── Fixture: unsafe_business_logic (parsing without catch_unwind) ──
cat >"${src_dir}/business.rs" <<'RUST'
#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_validate(url: *const u8, len: usize) -> u8 {
    let s = unsafe { std::slice::from_raw_parts(url, len) };
    match std::str::from_utf8(s) {
        Ok(text) => if text.contains("x") { 1 } else { 0 },
        Err(_) => 0,
    }
}
RUST

out="${tmp_dir}/business.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if grep -q "md_validate: unsafe_business_logic" "${out}"; then
    pass "classifies from_utf8 + slice::from_raw_parts as unsafe_business_logic"
else
    fail "classifies from_utf8 + slice::from_raw_parts as unsafe_business_logic" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/business.rs"

# ── Fixture: free_helper_without_catch (strict mode → REVIEW/exit 1) ──
cat >"${src_dir}/free.rs" <<'RUST'
#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_free(handle: *mut Handle) {
    if handle.is_null() {
        return;
    }
    unsafe { drop(Box::from_raw(handle)) };
}
RUST

out="${tmp_dir}/free.out"
rc=0
run_detector "${src_dir}" "${out}" --strict || rc=$?
if [[ "${rc}" -ne 0 ]] && grep -q "md_free.*free_helper_without_catch" "${out}"; then
    pass "strict mode flags free_helper_without_catch and exits 1"
else
    fail "strict mode flags free_helper_without_catch and exits 1" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/free.rs"

# ── Fixture: free_helper_with_catch (catch_unwind wrapping drop) ──
cat >"${src_dir}/free_catch.rs" <<'RUST'
#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_free_with_catch(handle: *mut Handle) {
    if handle.is_null() {
        return;
    }
    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        unsafe { drop(Box::from_raw(handle)) };
    }));
}
RUST

out="${tmp_dir}/free_catch.out"
rc=0
run_detector "${src_dir}" "${out}" --strict || rc=$?
# A *_free helper with catch_unwind wrapping drop should be classified
# as direct_catch (or at least NOT flagged as free_helper_without_catch).
if [[ "${rc}" -eq 0 ]] && ! grep -q "free_helper_without_catch" "${out}"; then
    pass "strict mode treats catch_unwind-wrapped free helper as safe (not free_helper_without_catch)"
else
    fail "strict mode treats catch_unwind-wrapped free helper as safe (not free_helper_without_catch)" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/free_catch.rs"

# ── Fixture: free_helper advisory mode → OK (no exit 1) ──
cat >"${src_dir}/free2.rs" <<'RUST'
#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_free2(handle: *mut Handle) {
    if handle.is_null() {
        return;
    }
    unsafe { drop(Box::from_raw(handle)) };
}
RUST

out="${tmp_dir}/free2.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]] && grep -q "md_free2: free_helper" "${out}"; then
    pass "advisory mode classifies free_helper as OK (no exit 1)"
else
    fail "advisory mode classifies free_helper as OK (no exit 1)" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/free2.rs"

# ── Fixture: unsafe_business_logic strict mode → exit 1 ──
cat >"${src_dir}/biz2.rs" <<'RUST'
#[unsafe(no_mangle)]
pub unsafe extern "C" fn md_is_dangerous(url: *const u8, len: usize) -> u8 {
    let s = unsafe { std::slice::from_raw_parts(url, len) };
    match std::str::from_utf8(s) {
        Ok(t) => if t.contains("javascript:") { 1 } else { 0 },
        Err(_) => 1,
    }
}
RUST

out="${tmp_dir}/biz2.out"
rc=0
run_detector "${src_dir}" "${out}" --strict || rc=$?
if [[ "${rc}" -ne 0 ]] && grep -q "md_is_dangerous: unsafe_business_logic" "${out}"; then
    pass "strict mode flags unsafe_business_logic and exits 1"
else
    fail "strict mode flags unsafe_business_logic and exits 1" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/biz2.rs"

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0