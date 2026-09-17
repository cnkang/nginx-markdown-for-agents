#!/bin/bash
#
# test_detect_finalize_return.sh — Fixture tests for detect_finalize_return.sh.
#
# The detector takes NO arguments and scans the relative path
# components/nginx-module/src, so each fixture root must contain that
# tree and the detector must run with cwd set to the fixture root.
#
# POSITIVE: ngx_http_finalize_request followed by another call (no
# immediate return) is flagged (exit 1).
# NEGATIVE: followed by return, and followed by a comment then return,
# stay clean (exit 0).
#
# Fixtures are built under mktemp -d; nothing is written inside the repo.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_finalize_return.sh"

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

# The detector resolves SRC_DIR relative to the cwd, so run it inside a
# subshell that cd's into the fixture root first.
run_detector() {
    local cwd="$1"
    local output
    if output="$(cd "${cwd}" && bash "${DETECTOR}" 2>&1)"; then
        DETECTOR_RC=0
    else
        DETECTOR_RC=$?
    fi
    DETECTOR_OUTPUT="${output}"
    return 0
}

printf 'Unit Tests: detect_finalize_return.sh\n'

if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/finalize-return.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT

# ── POSITIVE: finalize followed by a cleanup call ──
pos_root="${tmp_dir}/positive"
pos_dir="${pos_root}/components/nginx-module/src"
mkdir -p "${pos_dir}"
cat >"${pos_dir}/finalize.c" <<'EOF'
void
ngx_http_markdown_complete(ngx_http_request_t *r, ngx_int_t rc)
{
    ngx_http_finalize_request(r, rc);
    ngx_http_markdown_cleanup(r);
}
EOF

run_detector "${pos_root}"
if [[ "${DETECTOR_RC}" -eq 1 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"[WARN]"*"finalize_request not followed by return"* ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"[finalize-return] 1 call(s) to ngx_http_finalize_request not followed by return"* ]]; then
    pass "flags finalize followed by another call (exit 1, violation marker)"
else
    fail "flags finalize followed by another call (exit 1, violation marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── NEGATIVE: finalize followed by return ──
neg_root="${tmp_dir}/negative"
neg_dir="${neg_root}/components/nginx-module/src"
mkdir -p "${neg_dir}"
cat >"${neg_dir}/finalize.c" <<'EOF'
ngx_int_t
ngx_http_markdown_complete(ngx_http_request_t *r, ngx_int_t rc)
{
    ngx_http_finalize_request(r, rc);
    return rc;
}
EOF

run_detector "${neg_root}"
if [[ "${DETECTOR_RC}" -eq 0 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"[finalize-return] All ngx_http_finalize_request calls followed by return"* ]]; then
    pass "clean when finalize is followed by return (exit 0, clean marker)"
else
    fail "clean when finalize is followed by return (exit 0, clean marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── NEGATIVE: comment between finalize and return is skipped ──
neg2_root="${tmp_dir}/negative-comment"
neg2_dir="${neg2_root}/components/nginx-module/src"
mkdir -p "${neg2_dir}"
cat >"${neg2_dir}/finalize.c" <<'EOF'
ngx_int_t
ngx_http_markdown_complete(ngx_http_request_t *r, ngx_int_t rc)
{
    ngx_http_finalize_request(r, rc);
    /* let the caller observe the return code */
    return rc;
}
EOF

run_detector "${neg2_root}"
if [[ "${DETECTOR_RC}" -eq 0 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"[finalize-return] All ngx_http_finalize_request calls followed by return"* ]]; then
    pass "treats comment-then-return as clean (exit 0, clean marker)"
else
    fail "treats comment-then-return as clean (exit 0, clean marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0
