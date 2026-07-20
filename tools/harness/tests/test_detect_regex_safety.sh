#!/usr/bin/env bash
#
# test_detect_regex_safety.sh - Unit tests for regex safety detector.
#
# Rule 10: Validates detection of regex patterns prone to catastrophic
# backtracking (ReDoS) and ensures safe patterns are not flagged.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="python3 ${SCRIPT_DIR}/../detect_regex_safety.py"

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

printf 'Unit Tests: detect_regex_safety.py\n'

# Create temp base directory
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/regex-safety.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT

# ============================================================================
# Test 1: Nested quantifier (a+)+ -> WARN
# ============================================================================
fixture_dir="${tmp_dir}/t1"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/bad.py" <<'PY'
import re
pattern = re.compile(r"(a+)+b")
PY

output_file="${tmp_dir}/test1.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
if grep -q "nested quantifier" "${output_file}"; then
    pass "nested quantifier (a+)+ detected"
else
    fail "nested quantifier (a+)+ detected"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 2: Safe nested quantifier with literal separator (-[a-z]+)* -> OK
# ============================================================================
fixture_dir="${tmp_dir}/t2"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/safe.py" <<'PY'
import re
pattern = re.compile(r"^[a-z](-[a-z]+)*$")
PY

output_file="${tmp_dir}/test2.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
if grep -q "OK: no unsafe regex" "${output_file}"; then
    pass "safe nested quantifier with literal separator not flagged"
else
    fail "safe nested quantifier with literal separator not flagged"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 3: Adjacent identical quantifiers .*.* -> WARN
# ============================================================================
fixture_dir="${tmp_dir}/t3"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/bad.py" <<'PY'
import re
pattern = re.compile(r"(.*)(.*)end")
PY

output_file="${tmp_dir}/test3.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
if grep -q "adjacent" "${output_file}"; then
    pass "adjacent overlapping quantifiers .*.* detected"
else
    fail "adjacent overlapping quantifiers .*.* detected"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 4: Safe adjacent quantifiers with required literal \\s*:\\s* -> OK
# ============================================================================
fixture_dir="${tmp_dir}/t4"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/safe.py" <<'PY'
import re
pattern = re.compile(r"lines\.\s*:\s*(\d+)")
PY

output_file="${tmp_dir}/test4.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
if grep -q "OK: no unsafe regex" "${output_file}"; then
    pass "safe adjacent quantifiers with literal separator not flagged"
else
    fail "safe adjacent quantifiers with literal separator not flagged"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 5: Backreference after .* -> WARN
# ============================================================================
fixture_dir="${tmp_dir}/t5"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/bad.py" <<'PY'
import re
pattern = re.compile(r"(.*?)\n\s*\1", re.DOTALL)
PY

output_file="${tmp_dir}/test5.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
if grep -q "backreference after" "${output_file}"; then
    pass "backreference after .*? detected"
else
    fail "backreference after .*? detected"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 6: Star inside repeated group (.*,)+ -> WARN
# ============================================================================
fixture_dir="${tmp_dir}/t6"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/bad.py" <<'PY'
import re
pattern = re.compile(r"(.*,)+end")
PY

output_file="${tmp_dir}/test6.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
if grep -qE "nested quantifier|unbounded repetition" "${output_file}"; then
    pass "star inside repeated group detected"
else
    fail "star inside repeated group detected"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 7: Overlapping alternation (foo|foobar)+ -> WARN
# ============================================================================
fixture_dir="${tmp_dir}/t7"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/bad.py" <<'PY'
import re
pattern = re.compile(r"(foo|foobar)+end")
PY

output_file="${tmp_dir}/test7.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
if grep -q "overlapping alternation" "${output_file}"; then
    pass "overlapping alternation with quantifier detected"
else
    fail "overlapping alternation with quantifier detected"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 8: Suppression comment nosec:regex-safety -> OK
# ============================================================================
fixture_dir="${tmp_dir}/t8"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/suppressed.py" <<'PY'
import re
# nosec:regex-safety — intentional for matching raw strings
pattern = re.compile(r"(a+)+b")
PY

output_file="${tmp_dir}/test8.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
if grep -q "OK: no unsafe regex" "${output_file}"; then
    pass "nosec:regex-safety suppression respected"
else
    fail "nosec:regex-safety suppression respected"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 9: --strict mode exits 1 on findings
# ============================================================================
fixture_dir="${tmp_dir}/t9"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/bad.py" <<'PY'
import re
pattern = re.compile(r"(a+)+b")
PY

output_file="${tmp_dir}/test9.out"
${DETECTOR} "${fixture_dir}" --strict >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] && grep -q "FAIL:" "${output_file}"; then
    pass "--strict mode exits 1 on findings"
else
    fail "--strict mode exits 1 on findings" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 10: Clean file with simple regex -> OK
# ============================================================================
fixture_dir="${tmp_dir}/t10"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/clean.py" <<'PY'
import re
name_re = re.compile(r"^[a-z][a-z0-9_]*$")
number_re = re.compile(r"\d+")
word_re = re.compile(r"\w+")
PY

output_file="${tmp_dir}/test10.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]] && grep -q "OK: no unsafe regex" "${output_file}"; then
    pass "clean file with safe regexes -> no findings"
else
    fail "clean file with safe regexes -> no findings" "exit=${exit_code}"
    cat "${output_file}" >&2
fi

# ============================================================================
# Test 11: Shell grep -E with overlapping pattern -> detected
# ============================================================================
fixture_dir="${tmp_dir}/t11"
mkdir -p "${fixture_dir}"
cat >"${fixture_dir}/bad.sh" <<'SH'
#!/usr/bin/env bash
grep -E "(a+)+match" file.txt
SH

output_file="${tmp_dir}/test11.out"
${DETECTOR} "${fixture_dir}" >"${output_file}" 2>&1
if grep -q "nested quantifier" "${output_file}"; then
    pass "shell grep -E with dangerous pattern detected"
else
    fail "shell grep -E with dangerous pattern detected"
    cat "${output_file}" >&2
fi

# ============================================================================
# Summary
# ============================================================================
printf '\n'
total=$((PASS_COUNT + FAIL_COUNT))
printf 'Results: %d/%d passed\n' "${PASS_COUNT}" "${total}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    printf 'FAIL: %d test(s) failed\n' "${FAIL_COUNT}" >&2
    exit 1
fi
printf 'All tests passed.\n'
exit 0
