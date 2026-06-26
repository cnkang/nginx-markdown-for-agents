#!/bin/bash
#
# test_detect_ffi_fat_pointer_transfer.sh — Unit tests for the
# fat-pointer transfer detector (Rule 53 — ffi-crosslang).
#
# Validates that the detector correctly:
#   - Flags Box::into_raw(boxed_slice) as *mut u8
#   - Flags Box::into_raw(var_name) as *mut u8 where var is a slice
#   - Exempts Box::into_raw(Box::new(...)) single-object handles
#   - Exempts doc-comment / SAFETY-comment references to Box::into_raw
#   - Passes when no violations exist

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_ffi_fat_pointer_transfer.sh"

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
    bash "${DETECTOR}" "${src_dir}" >"${output_file}" 2>&1
    return $?
}

make_ffi_dir() {
    local parent="$1"
    local dir="${parent}/ffi"
    mkdir -p "${dir}"
    printf '%s' "${dir}"
    return 0
}

printf 'Unit Tests: detect_ffi_fat_pointer_transfer.sh\n'

# Syntax check
if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

BASE_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ffi-fatptr-detector.XXXXXX")" || {
    fail "create base temp directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${BASE_TMP}"' EXIT

# ── Test 1: Allowed single-object handle ──
td="${BASE_TMP}/t1"
ffi="$(make_ffi_dir "${td}")"
cat >"${ffi}/handles.rs" <<'RUST'
#[unsafe(no_mangle)]
pub extern "C" fn create_handle() -> *mut Handle {
    Box::into_raw(Box::new(Handle::new()))
}
RUST
out="${td}/out.txt"
rc=0
run_detector "${ffi}" "${out}" || rc=$?
if [[ $rc -eq 0 ]]; then
    pass "Box::into_raw(Box::new(...)) single-object handle is exempt"
else
    fail "Box::into_raw(Box::new(...)) single-object handle is exempt" \
         "got exit $rc, output: $(cat "${out}")"
fi

# ── Test 2: SAFETY/doc comment referencing Box::into_raw ──
td="${BASE_TMP}/t2"
ffi="$(make_ffi_dir "${td}")"
cat >"${ffi}/doc.rs" <<'RUST'
//! Module doc referencing Box::into_raw for context.
//!
//! SAFETY: `raw` was allocated by `Box<[u8]>` via `Box::into_raw`.
/// Transfer ownership to C.  Do not use Box::into_raw for slices.
pub fn do_stuff() {}
RUST
out="${td}/out.txt"
rc=0
run_detector "${ffi}" "${out}" || rc=$?
if [[ $rc -eq 0 ]]; then
    pass "doc/SAFETY comments referencing Box::into_raw are exempt"
else
    fail "doc/SAFETY comments referencing Box::into_raw are exempt" \
         "got exit $rc, output: $(cat "${out}")"
fi

# ── Test 3: VIOLATION — Box::into_raw(boxed) as *mut u8 ──
td="${BASE_TMP}/t3"
ffi="$(make_ffi_dir "${td}")"
cat >"${ffi}/violation.rs" <<'RUST'
pub unsafe fn transfer_slice() {
    let boxed = data.into_boxed_slice();
    let raw = Box::into_raw(boxed) as *mut u8;
    *out_data = raw;
}
RUST
out="${td}/out.txt"
rc=0
run_detector "${ffi}" "${out}" || rc=$?
if [[ $rc -eq 1 ]]; then
    pass "Box::into_raw(boxed) as *mut u8 is detected as violation"
else
    fail "Box::into_raw(boxed) as *mut u8 is detected as violation" \
         "got exit $rc, output: $(cat "${out}")"
fi
if grep -q 'VIOLATION:' "${out}"; then
    pass "violation produces VIOLATION: marker in output"
else
    fail "violation produces VIOLATION: marker in output" \
         "output: $(cat "${out}")"
fi

# ── Test 4: VIOLATION — Box::into_raw(slice_var) as *mut u8 (md_bytes/etag) ──
td="${BASE_TMP}/t4"
ffi="$(make_ffi_dir "${td}")"
cat >"${ffi}/finalize.rs" <<'RUST'
pub unsafe fn finalize() {
    let md_bytes = result.markdown.into_boxed_slice();
    let etag_bytes = etag_str.into_bytes().into_boxed_slice();
    result_ref.markdown = Box::into_raw(md_bytes) as *mut u8;
    result_ref.etag = Box::into_raw(etag_bytes) as *mut u8;
}
RUST
out="${td}/out.txt"
rc=0
run_detector "${ffi}" "${out}" || rc=$?
if [[ $rc -eq 1 ]]; then
    pass "Box::into_raw(slice_var) as *mut u8 (md_bytes/etag_bytes) detected"
    count=$(grep -c 'VIOLATION:' "${out}" 2>/dev/null || true)
    if [[ "${count}" -eq 2 ]]; then
        pass "2 violations detected for 2 slice transfers"
    else
        fail "2 violations detected for 2 slice transfers" \
             "expected 2, got ${count}. output: $(cat "${out}")"
    fi
else
    fail "Box::into_raw(slice_var) as *mut u8 (md_bytes/etag_bytes) detected" \
         "got exit $rc, output: $(cat "${out}")"
fi

# ── Test 5: mixed allowed and violating patterns ──
td="${BASE_TMP}/t5"
ffi="$(make_ffi_dir "${td}")"
cat >"${ffi}/mixed.rs" <<'RUST'
/// Allowed: single-object handle
pub fn create() -> *mut Handle {
    Box::into_raw(Box::new(Handle::new()))
}

/// Allowed: SAFETY comment reference
// SAFETY: originates from Box::into_raw

/// Violation: slice transfer
pub unsafe fn feed() {
    let boxed = output.markdown.into_boxed_slice();
    let raw = Box::into_raw(boxed) as *mut u8;
    *out = raw;
}
RUST
out="${td}/out.txt"
rc=0
run_detector "${ffi}" "${out}" || rc=$?
if [[ $rc -eq 1 ]]; then
    count=$(grep -c 'VIOLATION:' "${out}" 2>/dev/null || true)
    if [[ "${count}" -eq 1 ]]; then
        pass "mixed file: exactly 1 violation (handle + doc + 1 slice)"
    else
        fail "mixed file: exactly 1 violation" \
             "expected 1, got ${count}. output: $(cat "${out}")"
    fi
else
    fail "mixed file: exit non-zero on violation" \
         "got exit $rc, output: $(cat "${out}")"
fi

# ── Test 6: clean file — no violations (using helper) ──
td="${BASE_TMP}/t6"
ffi="$(make_ffi_dir "${td}")"
cat >"${ffi}/clean.rs" <<'RUST'
pub unsafe fn transfer() {
    use crate::ffi::memory::leak_boxed_slice_to_raw;
    let boxed = data.into_boxed_slice();
    let (ptr, len) = leak_boxed_slice_to_raw(boxed);
    *out_data = ptr;
    *out_len = len;
}
RUST
out="${td}/out.txt"
rc=0
run_detector "${ffi}" "${out}" || rc=$?
if [[ $rc -eq 0 ]]; then
    pass "clean file using leak_boxed_slice_to_raw passes"
else
    fail "clean file using leak_boxed_slice_to_raw passes" \
         "got exit $rc, output: $(cat "${out}")"
fi

# ── Summary ──
echo ""
echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    exit 1
fi
