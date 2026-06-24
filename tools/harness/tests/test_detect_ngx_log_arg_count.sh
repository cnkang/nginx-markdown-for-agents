#!/usr/bin/env bash
#
# test_detect_ngx_log_arg_count.sh - Unit tests for the ngx_log arg count detector.
#
# Validates Rule 8 detector: ngx_log_debugN/errorN suffix digit must match
# the number of format-string arguments.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_ngx_log_arg_count.sh"

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

printf 'Unit Tests: detect_ngx_log_arg_count.sh\n'

# Test 1: Syntax check
if bash -n "${DETECTOR}" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

# Create temp source directory
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ngx-log-detector.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT
src_dir="${tmp_dir}/components/nginx-module/src"
mkdir -p "${src_dir}"

# Test 2: Clean file (matching args) -> PASS
cat >"${src_dir}/clean.c" <<'C'
void test(void) {
    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: clean message");
    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: value=%d", val);
    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: %s and %s", a, b);
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: %V %V %V", &a, &b, &c);
}
C

output_file="${tmp_dir}/clean.out"
exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "matching arg counts -> exit 0"
else
    fail "matching arg counts -> exit 0" "got exit ${exit_code}: $(cat "${output_file}")"
fi

# Test 3: Mismatch (debug2 with 1 arg) -> FAIL
cat >"${src_dir}/mismatch.c" <<'C'
void test(void) {
    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: only %s here", arg1);
}
C
rm -f "${src_dir}/clean.c"

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -ne 0 ]] && grep -q 'VIOLATION' "${output_file}"; then
    pass "debug2 with 1 arg -> exit 1 + VIOLATION"
else
    fail "debug2 with 1 arg -> exit 1 + VIOLATION" "got exit ${exit_code}: $(cat "${output_file}")"
fi

# Test 4: NGINX-style %V and %uz specifiers counted correctly
cat >"${src_dir}/nginx_fmt.c" <<'C'
void test(void) {
    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: etag: \"%V\"", &r->headers_out.etag->value);
    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: size=%uz, count=%ui", size, count);
}
C
rm -f "${src_dir}/mismatch.c"

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "NGINX %V and %uz specifiers -> exit 0"
else
    fail "NGINX %V and %uz specifiers -> exit 0" "got exit ${exit_code}: $(cat "${output_file}")"
fi

# Test 5: %*s consumes 2 args (width + string)
cat >"${src_dir}/star_width.c" <<'C'
void test(void) {
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: value=\"%*s\", len=%uz",
                   len, ptr, len);
}
C
rm -f "${src_dir}/nginx_fmt.c"

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "%*s (2 args) + %uz (1 arg) = 3 args with debug3 -> exit 0"
else
    fail "%*s (2 args) + %uz (1 arg) = 3 args with debug3 -> exit 0" "got exit ${exit_code}: $(cat "${output_file}")"
fi

# Test 6: Macro definition (not a call) -> skipped
cat >"${src_dir}/macro.c" <<'C'
#define NGX_HTTP_MARKDOWN_LOG_DEBUG1(level, log, err, fmt, arg) \
    do {                                                        \
        ngx_log_debug1((level), (log), (err), (fmt), (arg));    \
    } while (0)
C
rm -f "${src_dir}/star_width.c"

exit_code=0
(cd "${tmp_dir}" && bash "${DETECTOR}") >"${output_file}" 2>&1 || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "macro definition with (fmt) param -> exit 0 (skipped)"
else
    fail "macro definition with (fmt) param -> exit 0 (skipped)" "got exit ${exit_code}: $(cat "${output_file}")"
fi

# Summary
printf '\n  Results: %d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
exit 0