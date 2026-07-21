#!/bin/bash
# test_detect_regex_safety.sh — CLI smoke test for detect_regex_safety.py
# Rule 10 (parser-regex): Validates detector CLI contract.
# macOS Bash 3.2 compatible — no associative arrays, no [[ =~ ]].

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
DETECTOR="${REPO_ROOT}/tools/harness/detect_regex_safety.py"

FAILURES=0

fail() {
    echo "FAIL: $1" >&2
    FAILURES=$((FAILURES + 1))
}

pass() {
    echo "PASS: $1"
}

# Test 1: Default scan on repository passes
echo "Test 1: Default scan on repository..."
if python3 "${DETECTOR}" 2>&1 | grep -q "OK: regex safety check passed"; then
    pass "default scan"
else
    fail "default scan did not pass"
fi

# Test 2: Strict mode on repository passes (0 ERROR findings)
echo "Test 2: Strict mode on repository..."
if python3 "${DETECTOR}" --strict 2>&1 | grep -q "OK: regex safety check passed"; then
    pass "strict mode"
else
    fail "strict mode did not pass"
fi

# Test 3: JSON output is valid JSON
echo "Test 3: JSON output..."
if python3 "${DETECTOR}" --format json 2>/dev/null | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
    pass "json output"
else
    fail "json output not parseable"
fi

# Test 4: Invalid path returns non-zero
echo "Test 4: Invalid path returns non-zero..."
python3 "${DETECTOR}" --path /nonexistent/path 2>/dev/null
RC=$?
if [ "$RC" -ne 0 ]; then
    pass "invalid path non-zero (rc=$RC)"
else
    fail "invalid path returned zero"
fi

# Test 5: Suppression contract — valid suppression suppresses finding
echo "Test 5: Suppression contract..."
TMPDIR_TEST="$(mktemp -d)"
cat > "${TMPDIR_TEST}/test_suppress.py" <<'PYEOF'
import re
# nosec:regex-safety -- trusted generated token, max_input_bytes=64
p = re.compile(r'(a+)+b')
PYEOF
if python3 "${DETECTOR}" --path "${TMPDIR_TEST}" --strict 2>&1 | grep -q "OK"; then
    pass "valid suppression"
else
    fail "valid suppression did not suppress"
fi

# Test 6: Invalid suppression (no justification) produces error
echo "Test 6: Invalid suppression without justification..."
cat > "${TMPDIR_TEST}/test_bad_suppress.py" <<'PYEOF'
import re
# nosec:regex-safety
p = re.compile(r'(a+)+b')
PYEOF
if python3 "${DETECTOR}" --path "${TMPDIR_TEST}" --strict 2>&1 | grep -q "PARSE_ERROR"; then
    pass "invalid suppression detected"
else
    fail "invalid suppression not detected"
fi

# Test 7: Path with spaces works
echo "Test 7: Path with spaces..."
SPACED_DIR="${TMPDIR_TEST}/with space"
mkdir -p "${SPACED_DIR}"
cat > "${SPACED_DIR}/test.py" <<'PYEOF'
import re
p = re.compile(r'^[a-z]+$')
PYEOF
if python3 "${DETECTOR}" --path "${SPACED_DIR}" 2>&1 | grep -q "OK"; then
    pass "path with spaces"
else
    fail "path with spaces failed"
fi

# Cleanup
rm -rf "${TMPDIR_TEST}"

echo "---"
if [ "$FAILURES" -eq 0 ]; then
    echo "All smoke tests passed."
    exit 0
else
    echo "${FAILURES} smoke test(s) failed." >&2
    exit 1
fi