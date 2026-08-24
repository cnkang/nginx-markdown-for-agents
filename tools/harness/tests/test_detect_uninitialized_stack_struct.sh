#!/bin/bash
#
# test_detect_uninitialized_stack_struct.sh — Unit tests for the stack-struct
# whole-initialization detector.
#
# Validates that the detector:
#   - flags a stack ngx_conf_t declared and assigned field-by-field
#     (the class of bug that caused a SIGSEGV under GCC -O0 when cf.ctx
#     carried stack garbage past the production NULL guard)
#   - accepts a stack ngx_conf_t zeroed with memset(&cf, 0, sizeof(cf))
#   - accepts a stack struct initialized through an init_* helper
#   - ignores pointer declarations and header prototypes

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_uninitialized_stack_struct.sh"

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

    tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/uninit-struct.XXXXXX")" || return 1
    mkdir -p "${tmp_dir}/src" "${tmp_dir}/tests" || return 1
    printf '%s\n' "${tmp_dir}"
    return 0
}

run_detector() {
    local src_dir="$1"
    local output_file="$2"

    # The detector is advisory by default (exit 0 on candidates); the unit
    # test exercises the fail-closed path, so run with --strict.
    bash "${DETECTOR}" --strict "${src_dir}" >"${output_file}" 2>&1
    return $?
}

printf 'Unit Tests: detect_uninitialized_stack_struct.sh\n'

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

# 1. Violation: partial field assignment without whole-init.
cat >"${src_dir}/partial.c" <<'C'
static void
test_partial_init(void)
{
    ngx_conf_t  cf;

    cf.pool = &g_pool;
    cf.log = &g_log;
}
C

output_file="${tmp_dir}/partial.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -ne 0 ]] && grep -q "ngx_conf_t 'cf'" "${output_file}"; then
    pass "flags partial field assignment without whole-init"
else
    fail "flags partial field assignment without whole-init" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

# 2. Clean: memset whole-init.
rm -f "${src_dir}/partial.c"
cat >"${src_dir}/memset_ok.c" <<'C'
static void
test_memset_init(void)
{
    ngx_conf_t  cf;

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;
}
C

output_file="${tmp_dir}/memset.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "accepts memset whole-init"
else
    fail "accepts memset whole-init" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

# 3. Clean: init helper.
rm -f "${src_dir}/memset_ok.c"
cat >"${src_dir}/helper_ok.c" <<'C'
static void
test_helper_init(void)
{
    ngx_http_markdown_conf_t  conf;

    init_conf(&conf);
    conf.max_size = 10 * 1024 * 1024;
}
C

output_file="${tmp_dir}/helper.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "accepts init_* helper initialization"
else
    fail "accepts init_* helper initialization" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

# 4. Clean: pointer declaration and header prototypes ignored.
rm -f "${src_dir}/helper_ok.c"
cat >"${src_dir}/decls.h" <<'C'
ngx_int_t ngx_http_markdown_forward_headers(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);
C
cat >"${src_dir}/pointer.c" <<'C'
static void
test_pointer(void)
{
    ngx_conf_t  *cf;

    cf = ngx_pcalloc(g_pool, sizeof(ngx_conf_t));
}
C

output_file="${tmp_dir}/decls.out"
exit_code=0
run_detector "${src_dir}" "${output_file}" || exit_code=$?
if [[ "${exit_code}" -eq 0 ]]; then
    pass "ignores pointer declarations and prototypes"
else
    fail "ignores pointer declarations and prototypes" \
        "exit=${exit_code}; output=$(tr '\n' ' ' <"${output_file}")"
fi

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    printf '\nFAIL: %s test(s) failed.\n' "${FAIL_COUNT}" >&2
    exit 1
fi

printf '\nPASS: %s test(s) passed.\n' "${PASS_COUNT}"
exit 0
