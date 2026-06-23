#!/bin/bash
#
# test_detect_pool_free.sh — Unit tests for the pool-free mismatch detector.
#
# Validates Rule 43 detector coverage: simple var, struct/pointer field
# lvalues, reassignment, allowlist suppression, and correct non-firing
# on heap (ngx_alloc) buffers.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_pool_free.sh"

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

printf 'Unit Tests: detect_pool_free.sh\n'

if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/pool-free-detector.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT
src_dir="${tmp_dir}/src"
mkdir -p "${src_dir}"

# ── Fixture 1: pool var → ngx_free(var) — VIOLATION ──
cat >"${src_dir}/pool_var.c" <<'C'
void f(void) {
    u_char *buf = ngx_palloc(pool, 128);
    if (buf == NULL) { return; }
    ngx_free(buf);
}
C
out="${tmp_dir}/pool_var.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -ne 0 ]] && grep -qF "ngx_free(buf)" "${out}"; then
    pass "detects pool-allocated var freed with ngx_free"
else
    fail "detects pool-allocated var freed with ngx_free" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/pool_var.c"

# ── Fixture 2: pool field → ngx_free(ctx->buffer.data) — VIOLATION ──
cat >"${src_dir}/pool_field.c" <<'C'
void f(void) {
    ctx->buffer.data = ngx_palloc(pool, 256);
    ngx_free(ctx->buffer.data);
}
C
out="${tmp_dir}/pool_field.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -ne 0 ]] && grep -qF "ngx_free(ctx->buffer.data)" "${out}"; then
    pass "detects pool-allocated pointer field freed with ngx_free"
else
    fail "detects pool-allocated pointer field freed with ngx_free" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/pool_field.c"

# ── Fixture 3: heap var (ngx_alloc) → ngx_free — NO VIOLATION ──
cat >"${src_dir}/heap_var.c" <<'C'
void f(void) {
    u_char *buf = ngx_alloc(128, log);
    if (buf == NULL) { return; }
    ngx_free(buf);
}
C
out="${tmp_dir}/heap_var.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]]; then
    pass "does not flag ngx_alloc'd buffer freed with ngx_free"
else
    fail "does not flag ngx_alloc'd buffer freed with ngx_free" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/heap_var.c"

# ── Fixture 4: reassignment pool→heap — NO VIOLATION ──
# var first pool-allocated, then reassigned to ngx_alloc; freeing the
# heap pointer is correct.
cat >"${src_dir}/reassign.c" <<'C'
void f(void) {
    u_char *buf = ngx_palloc(pool, 64);
    if (buf == NULL) { return; }
    /* ... use pool buf ... */
    buf = ngx_alloc(256, log);
    if (buf == NULL) { return; }
    ngx_free(buf);
}
C
out="${tmp_dir}/reassign.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]]; then
    pass "does not flag reassignment from pool to heap before ngx_free"
else
    fail "does not flag reassignment from pool to heap before ngx_free" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/reassign.c"

# ── Fixture 5: allow-pool-free comment suppresses — ALLOWED ──
cat >"${src_dir}/allowlist.c" <<'C'
void f(void) {
    u_char *buf = ngx_palloc(pool, 128);
    /* allow-pool-free: documented exception */
    ngx_free(buf);
}
C
out="${tmp_dir}/allowlist.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -eq 0 ]] && grep -q "ALLOWED" "${out}"; then
    pass "allow-pool-free comment suppresses violation"
else
    fail "allow-pool-free comment suppresses violation" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/allowlist.c"

# ── Fixture 6: (*ptr).field lvalue — VIOLATION ──
cat >"${src_dir}/deref_field.c" <<'C'
void f(void) {
    (*ptr).data = ngx_palloc(pool, 128);
    ngx_free((*ptr).data);
}
C
out="${tmp_dir}/deref_field.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -ne 0 ]] && grep -qF "ngx_free((*ptr).data)" "${out}"; then
    pass "detects pool-allocated (*ptr).field freed with ngx_free"
else
    fail "detects pool-allocated (*ptr).field freed with ngx_free" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/deref_field.c"

# ── Fixture 7: numeric lvalue buf2 → ngx_free(buf2) — VIOLATION ──
cat >"${src_dir}/numeric_var.c" <<'C'
void f(void) {
    u_char *buf2 = ngx_palloc(pool, 128);
    if (buf2 == NULL) { return; }
    ngx_free(buf2);
}
C
out="${tmp_dir}/numeric_var.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -ne 0 ]] && grep -qF "ngx_free(buf2)" "${out}"; then
    pass "detects numeric-lvalue pool-allocated var freed with ngx_free"
else
    fail "detects numeric-lvalue pool-allocated var freed with ngx_free" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/numeric_var.c"

# ── Fixture 8: numeric struct lvalue ctx2->data → ngx_free — VIOLATION ──
cat >"${src_dir}/numeric_struct.c" <<'C'
void f(void) {
    ctx2->data = ngx_palloc(pool, 256);
    ngx_free(ctx2->data);
}
C
out="${tmp_dir}/numeric_struct.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if [[ "${rc}" -ne 0 ]] && grep -qF "ngx_free(ctx2->data)" "${out}"; then
    pass "detects numeric-struct-lvalue pool-allocated pointer freed with ngx_free"
else
    fail "detects numeric-struct-lvalue pool-allocated pointer freed with ngx_free" \
        "exit=${rc}; output=$(tr '\n' ' ' <"${out}")"
fi
rm -f "${src_dir}/numeric_struct.c"

# ── Fixture 9: failure message does not recommend ngx_pfree ──
cat >"${src_dir}/msg_check.c" <<'C'
void f(void) {
    u_char *buf = ngx_palloc(pool, 128);
    ngx_free(buf);
}
C
out="${tmp_dir}/msg_check.out"
rc=0
run_detector "${src_dir}" "${out}" || rc=$?
if grep -q "ngx_pfree" "${out}"; then
    fail "failure message must not recommend ngx_pfree" \
        "message recommends ngx_pfree which is wrong per Rule 43"
else
    pass "failure message does not recommend ngx_pfree"
fi
rm -f "${src_dir}/msg_check.c"

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0