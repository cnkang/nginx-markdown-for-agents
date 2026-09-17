#!/bin/bash
#
# test_detect_backpressure_resume.sh — Fixture tests for detect_backpressure_resume.sh.
#
# POSITIVE: a streaming helper returning NGX_AGAIN with no state-save
# pattern in the preceding 30-line window is flagged (exit 1).
# NEGATIVE: the same shape guarded by a state-save pattern
# (pending_output_bytes, fullbuffer.pending) stays clean, a ctx-less
# utility is out of scope, and a missing source directory exits 2.
#
# Fixtures are built under mktemp -d; nothing is written inside the repo.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_backpressure_resume.sh"

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

DETECTOR_RC=0
DETECTOR_OUTPUT=""

run_detector() {
    local src_dir="$1"
    local output
    if output="$(bash "${DETECTOR}" "${src_dir}" 2>&1)"; then
        DETECTOR_RC=0
    else
        DETECTOR_RC=$?
    fi
    DETECTOR_OUTPUT="${output}"
    return 0
}

printf 'Unit Tests: detect_backpressure_resume.sh\n'

if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/backpressure-resume.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT

# ── POSITIVE: return NGX_AGAIN with no state save ──
pos_dir="${tmp_dir}/positive/src"
mkdir -p "${pos_dir}"
cat >"${pos_dir}/flush.c" <<'EOF'
ngx_int_t
ngx_http_markdown_flush_ctx(ngx_http_markdown_ctx_t *ctx)
{
    ctx->inflight = 0;
    return NGX_AGAIN;
}
EOF

run_detector "${pos_dir}"
if [[ "${DETECTOR_RC}" -eq 1 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"without state save"* ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"ERROR: Found 1 backpressure violation(s)"* ]]; then
    pass "flags return NGX_AGAIN with no state-save pattern (exit 1, violation marker)"
else
    fail "flags return NGX_AGAIN with no state-save pattern (exit 1, violation marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── NEGATIVE: pending_output_bytes state save ──
neg1_dir="${tmp_dir}/negative-pending/src"
mkdir -p "${neg1_dir}"
cat >"${neg1_dir}/flush.c" <<'EOF'
ngx_int_t
ngx_http_markdown_flush_ctx(ngx_http_markdown_ctx_t *ctx)
{
    ctx->inflight = 0;
    ctx->pending_output_bytes = 0;
    return NGX_AGAIN;
}
EOF

run_detector "${neg1_dir}"
if [[ "${DETECTOR_RC}" -eq 0 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"OK: No backpressure resume issues detected"* ]]; then
    pass "clean with pending_output_bytes state save (exit 0, clean marker)"
else
    fail "clean with pending_output_bytes state save (exit 0, clean marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── NEGATIVE: fullbuffer.pending state save ──
neg2_dir="${tmp_dir}/negative-fullbuffer/src"
mkdir -p "${neg2_dir}"
cat >"${neg2_dir}/flush.c" <<'EOF'
ngx_int_t
ngx_http_markdown_flush_ctx(ngx_http_markdown_ctx_t *ctx)
{
    ctx->fullbuffer.pending = 1;
    return NGX_AGAIN;
}
EOF

run_detector "${neg2_dir}"
if [[ "${DETECTOR_RC}" -eq 0 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"OK: No backpressure resume issues detected"* ]]; then
    pass "clean with fullbuffer.pending state save (exit 0, clean marker)"
else
    fail "clean with fullbuffer.pending state save (exit 0, clean marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── NEGATIVE: utility without ctx/state is out of scope ──
neg3_dir="${tmp_dir}/negative-utility/src"
mkdir -p "${neg3_dir}"
cat >"${neg3_dir}/gate.c" <<'EOF'
ngx_int_t
ngx_http_markdown_is_ready(ngx_int_t flag)
{
    if (flag == 0) {
        return NGX_AGAIN;
    }
    return NGX_OK;
}
EOF

run_detector "${neg3_dir}"
if [[ "${DETECTOR_RC}" -eq 0 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"OK: No backpressure resume issues detected"* ]]; then
    pass "ignores NGX_AGAIN return in a non-ctx utility (exit 0, clean marker)"
else
    fail "ignores NGX_AGAIN return in a non-ctx utility (exit 0, clean marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── Contract: missing source directory exits 2 ──
run_detector "${tmp_dir}/does-not-exist"
if [[ "${DETECTOR_RC}" -eq 2 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"Source directory not found"* ]]; then
    pass "missing source directory exits 2"
else
    fail "missing source directory exits 2" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# Abort path: a scratch-file failure exits 2 without leaking temp files.
stub_dir="$(mktemp -d "${TMPDIR:-/tmp}/bp-stub.XXXXXX")"
abort_tmp="$(mktemp -d "${TMPDIR:-/tmp}/bp-abort.XXXXXX")"
abort_src="$(mktemp -d "${TMPDIR:-/tmp}/bp-asrc.XXXXXX")"
mkdir -p "${abort_src}/src"
real_mktemp="$(command -v mktemp)"
cat >"${stub_dir}/mktemp" <<'STUB'
#!/bin/bash
if [[ "${1:-}" == *backpressure-files* ]]; then
    exit 1
fi
if [[ $# -eq 0 ]]; then
    exec "${STUB_REAL_MKTEMP:?}" "${TMPDIR:?}/tmp.XXXXXX"
fi
exec "${STUB_REAL_MKTEMP:?}" "$@"
STUB
chmod +x "${stub_dir}/mktemp"

exit_code=0
STUB_REAL_MKTEMP="${real_mktemp}" PATH="${stub_dir}:${PATH}" TMPDIR="${abort_tmp}" \
    bash "${DETECTOR}" "${abort_src}" >"${abort_tmp}/detector.out" 2>&1 || exit_code=$?
leaked="$(find "${abort_tmp}" -type f -name 'tmp.*' | wc -l | tr -d ' ')"
if [[ "${exit_code}" -eq 2 ]] \
    && grep -q 'cannot create the file list' "${abort_tmp}/detector.out" \
    && [[ "${leaked}" -eq 0 ]]; then
    pass "a scratch-file failure exits 2 without leaking temp files"
else
    fail "a scratch-file failure exits 2 without leaking temp files" \
        "exit=${exit_code}; leaked=${leaked}"
fi
rm -rf "${stub_dir}" "${abort_tmp}" "${abort_src}"

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0
