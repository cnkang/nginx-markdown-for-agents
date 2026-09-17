#!/bin/bash
#
# test_detect_header_hash_filter.sh — Fixture tests for detect_header_hash_filter.sh.
#
# The detector takes NO arguments and scans the relative path
# components/nginx-module/src, so each fixture root must contain that
# tree and the detector must run with cwd set to the fixture root.
#
# POSITIVE: header part iteration (part->nelts) paired with
# ngx_table_elt_t and no hash==0 filter is flagged (exit 1).
# NEGATIVE: adding a hash==0 guard stays clean; a non-header part scan
# is out of scope; an allowlisted filename is reported ALLOWLISTED; a
# missing source directory exits 2.
#
# Fixtures are built under mktemp -d; nothing is written inside the repo.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_header_hash_filter.sh"

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

printf 'Unit Tests: detect_header_hash_filter.sh\n'

if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/header-hash-filter.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT

# ── POSITIVE: header iteration without hash==0 filter ──
pos_root="${tmp_dir}/positive"
pos_dir="${pos_root}/components/nginx-module/src"
mkdir -p "${pos_dir}"
cat >"${pos_dir}/iteration.c" <<'EOF'
static void
ngx_http_markdown_walk_header_parts(ngx_http_request_t *r)
{
    ngx_table_elt_t *h = part->elts;

    for (ngx_uint_t i = 0; i < part->nelts; i++) {
        ngx_http_markdown_emit(h[i].key.data, h[i].key.len);
    }
}
EOF

run_detector "${pos_root}"
if [[ "${DETECTOR_RC}" -eq 1 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"VIOLATION"*"Header iteration without hash==0 filter"* ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"[header-hash] FAIL: 1 non-allowlisted violation(s) found"* ]]; then
    pass "flags header iteration without hash==0 filter (exit 1, violation marker)"
else
    fail "flags header iteration without hash==0 filter (exit 1, violation marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── NEGATIVE: same iteration with a hash==0 guard ──
neg_root="${tmp_dir}/negative"
neg_dir="${neg_root}/components/nginx-module/src"
mkdir -p "${neg_dir}"
cat >"${neg_dir}/iteration.c" <<'EOF'
static void
ngx_http_markdown_walk_header_parts(ngx_http_request_t *r)
{
    ngx_table_elt_t *h = part->elts;

    for (ngx_uint_t i = 0; i < part->nelts; i++) {
        if (h[i].hash == 0) {
            continue;
        }

        ngx_http_markdown_emit(h[i].key.data, h[i].key.len);
    }
}
EOF

run_detector "${neg_root}"
if [[ "${DETECTOR_RC}" -eq 0 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"[header-hash] PASS: all header iteration files include hash==0 filtering"* ]]; then
    pass "clean when hash==0 guard is present (exit 0, clean marker)"
else
    fail "clean when hash==0 guard is present (exit 0, clean marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── NEGATIVE: part->nelts scan without header types is out of scope ──
neg2_root="${tmp_dir}/negative-nonheader"
neg2_dir="${neg2_root}/components/nginx-module/src"
mkdir -p "${neg2_dir}"
cat >"${neg2_dir}/parts.c" <<'EOF'
void
ngx_http_markdown_walk_parts(ngx_http_request_t *r)
{
    ngx_uint_t i;

    for (i = 0; i < part->nelts; i++) {
        ngx_http_markdown_emit_index(i);
    }
}
EOF

run_detector "${neg2_root}"
if [[ "${DETECTOR_RC}" -eq 0 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"[header-hash] PASS: all header iteration files include hash==0 filtering"* ]]; then
    pass "ignores part->nelts scans without header types (exit 0, clean marker)"
else
    fail "ignores part->nelts scans without header types (exit 0, clean marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── NEGATIVE: allowlisted filename is exempt ──
allow_root="${tmp_dir}/allowlisted"
allow_dir="${allow_root}/components/nginx-module/src"
mkdir -p "${allow_dir}"
cat >"${allow_dir}/ngx_http_markdown_auth.c" <<'EOF'
static void
ngx_http_markdown_scan_cache_control(ngx_http_request_t *r)
{
    ngx_table_elt_t *h = part->elts;

    for (ngx_uint_t i = 0; i < part->nelts; i++) {
        ngx_http_markdown_emit(h[i].key.data, h[i].key.len);
    }
}
EOF

run_detector "${allow_root}"
if [[ "${DETECTOR_RC}" -eq 0 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"[header-hash] ALLOWLISTED"* ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"[header-hash] PASS: all findings are allowlisted (1 file(s))"* ]]; then
    pass "allowlists ngx_http_markdown_auth.c (exit 0, ALLOWLISTED marker)"
else
    fail "allowlists ngx_http_markdown_auth.c (exit 0, ALLOWLISTED marker)" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

# ── Contract: missing source directory exits 2 ──
mkdir -p "${tmp_dir}/empty"
run_detector "${tmp_dir}/empty"
if [[ "${DETECTOR_RC}" -eq 2 ]] \
    && [[ "${DETECTOR_OUTPUT}" == *"Source directory not found"* ]]; then
    pass "missing source directory exits 2"
else
    fail "missing source directory exits 2" \
        "exit=${DETECTOR_RC}; output=$(printf '%s' "${DETECTOR_OUTPUT}" | tr '\n' ' ')"
fi

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0
