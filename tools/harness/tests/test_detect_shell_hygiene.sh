#!/bin/bash
#
# test_detect_shell_hygiene.sh - Fixture tests for detect_shell_hygiene.sh
#
# Covers pattern (f): $? inside negated conditional bodies (Rules 11/18), and
# patterns (a)-(e): missing explicit return, stdout diagnostics, single-bracket
# tests, case-without-default, and curl -X HEAD.  Each pattern gets one
# positive (defect) and one negative (clean) fixture, plus a fake-grep
# regression that pins the fail-closed scan contract: a recursive grep that
# exits 2 must produce a non-zero detector result, never a silent PASS.
#
# Adversarial fixtures reproduce the dead-branch defect shape:
#   if ! run_case; then rc=$?    <- $? reads the NEGATED status
# and verify the prescribed capture idiom does not false-positive:
#   run_case || rc=$?

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

TMPDIR_TEST="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_TEST}"' EXIT

printf 'Unit Tests: detect_shell_hygiene.sh pattern (f)\n'

failures=0
DETECTOR_RC=0
DETECTOR_OUTPUT=""

# The detector exits non-zero when it finds violations, so capture output
# before inspecting it (pipefail would invert the pipeline status).
run_detector() {
    local fixture="$1"
    local output
    if output="$(bash "${SCRIPT_DIR}/../detect_shell_hygiene.sh" "${fixture}" 2>&1)"; then
        DETECTOR_RC=0
    else
        DETECTOR_RC=$?
    fi
    DETECTOR_OUTPUT="${output}"
    return 0
}

run_arbitrary_detector() {
    local detector="$1"
    local fixture="$2"
    local output
    if output="$(bash "${detector}" "${fixture}" 2>&1)"; then
        DETECTOR_RC=0
    else
        DETECTOR_RC=$?
    fi
    DETECTOR_OUTPUT="${output}"
    return 0
}

assert_detector_flags() {
    local fixture="$1"
    local label="$2"
    run_detector "${fixture}"
    if [[ "${DETECTOR_RC}" -eq 1 ]] \
        && [[ "${DETECTOR_OUTPUT}" == *"ERROR"*"negated conditional"* ]]; then
        echo "  PASS: ${label} flagged"
    else
        echo "  FAIL: ${label} not flagged" >&2
        failures=$((failures + 1))
    fi
    return 0
}

assert_detector_clean() {
    local fixture="$1"
    local label="$2"
    run_detector "${fixture}"
    if [[ "${DETECTOR_RC}" -ne 0 ]]; then
        echo "  FAIL: ${label} detector exited ${DETECTOR_RC}" >&2
        failures=$((failures + 1))
    elif [[ "${DETECTOR_OUTPUT}" == *"ERROR"*"negated conditional"* ]]; then
        echo "  FAIL: ${label} wrongly flagged" >&2
        failures=$((failures + 1))
    else
        echo "  PASS: ${label} clean"
    fi
    return 0
}

# Fixture 1: multi-line dead branch.
mkdir -p "${TMPDIR_TEST}/case1"
cat > "${TMPDIR_TEST}/case1/dead_branch.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
run_case() {
    return 2
}
rc=0
if ! run_case; then
    rc=$?
fi
if [[ "${rc}" == "2" ]]; then
    echo "usage error path"
fi
EOF

# Fixture 2: single-line form.
mkdir -p "${TMPDIR_TEST}/case2"
cat > "${TMPDIR_TEST}/case2/single_line.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
probe() {
    return 3
}
status=0
if ! probe; then status=$?; fi
echo "${status}"
EOF

# Fixture 3: prescribed capture idiom must stay clean.
mkdir -p "${TMPDIR_TEST}/case3"
cat > "${TMPDIR_TEST}/case3/capture_idiom.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
curl_probe() {
    return 28
}
curl_rc=0
curl_probe || curl_rc=$?
if [[ "${curl_rc}" == "28" ]]; then
    echo "timed out" >&2
fi
EOF

# Fixture 4: plain $? outside negated conditionals stays clean.
mkdir -p "${TMPDIR_TEST}/case4"
cat > "${TMPDIR_TEST}/case4/plain_status.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
worker() {
    return 0
}
worker || rc=$?
echo "status ${rc}"
EOF

assert_detector_flags "${TMPDIR_TEST}/case1" "multi-line dead branch"
assert_detector_flags "${TMPDIR_TEST}/case2" "single-line dead branch"
assert_detector_clean "${TMPDIR_TEST}/case3" "capture idiom"
assert_detector_clean "${TMPDIR_TEST}/case4" "plain status usage"

# Fixture 4b: a terminator after a command on the same line must close the
# negated branch; otherwise the following clean status would inherit stale
# branch state.
mkdir -p "${TMPDIR_TEST}/case4b"
cat > "${TMPDIR_TEST}/case4b/same_line_terminator.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
if ! probe; then
    echo "handled"; fi
echo "status"
EOF
assert_detector_clean "${TMPDIR_TEST}/case4b" "same-line fi terminator"

# Fixture 4c: an operand named `fi` must not close the current block before
# the actual terminator.  The nested `if true;` header executes its
# condition, which refreshes $?, so the inner status read is legitimate and
# must not be flagged.
mkdir -p "${TMPDIR_TEST}/case4c"
cat > "${TMPDIR_TEST}/case4c/terminator_operand.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
if ! [ "$mode" = fi ]; then
    if true; then
        rc=$?
    fi
fi
EOF
assert_detector_clean "${TMPDIR_TEST}/case4c" \
    "terminator operand is not a block close"

# Fixture 5: a nested condition header refreshes $?, so the inner status
# read is legitimate; the outer negated branch is no longer pending.
mkdir -p "${TMPDIR_TEST}/case5"
cat > "${TMPDIR_TEST}/case5/nested_branch.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
probe() {
    return 3
}
if ! probe; then
    if true; then
        rc=$?
    fi
fi
EOF

assert_detector_clean "${TMPDIR_TEST}/case5" "nested header refreshes status"

# Fixture 6: after a command has executed inside the negated body, $? holds
# that command status, so a later capture must not be flagged.
mkdir -p "${TMPDIR_TEST}/case6"
cat > "${TMPDIR_TEST}/case6/status_after_command.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
run_case() {
    return 2
}
rc=0
if ! run_case; then
    echo "run_case failed" >&2
    rc=$?
fi
if [[ "${rc}" == "2" ]]; then
    echo "usage error path"
fi
EOF
assert_detector_clean "${TMPDIR_TEST}/case6" "status read after a body command"

# Fixture 7: a nested conditional header alone must refresh the pending
# negated status even when the nested body never runs a command.
mkdir -p "${TMPDIR_TEST}/case7"
cat > "${TMPDIR_TEST}/case7/nested_header_refresh.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
probe() {
    return 3
}
if ! probe; then
    if true; then
        status=$?
        echo "status ${status}"
    fi
fi
EOF
assert_detector_clean "${TMPDIR_TEST}/case7" "nested conditional header refreshes status"

# ---------------------------------------------------------------------------
# Patterns (a)-(e): each gets one defect fixture and one clean fixture.
#
# The fixtures live in their own directories so a positive fixture's findings
# cannot mask a negative fixture's cleanliness (the detector scans a whole
# directory tree).
# ---------------------------------------------------------------------------

# Helper: assert that a fixture directory is clean for a given needle.  The
# per-pattern assertions use dedicated helpers below so each failure message
# names the pattern under test.
assert_pattern_flags() {
    local fixture="$1"
    local needle="$2"
    local label="$3"
    run_detector "${fixture}"
    if [[ "${DETECTOR_RC}" -ne 0 ]] && [[ "${DETECTOR_OUTPUT}" == *"${needle}"* ]]; then
        echo "  PASS: ${label} flagged"
    else
        echo "  FAIL: ${label} not flagged (rc=${DETECTOR_RC})" >&2
        failures=$((failures + 1))
    fi
    return 0
}

assert_pattern_clean() {
    local fixture="$1"
    local needle="$2"
    local label="$3"
    run_detector "${fixture}"
    if [[ "${DETECTOR_RC}" -ne 0 ]]; then
        echo "  FAIL: ${label} detector exited ${DETECTOR_RC}" >&2
        failures=$((failures + 1))
    elif [[ "${DETECTOR_OUTPUT}" == *"${needle}"* ]]; then
        echo "  FAIL: ${label} wrongly flagged: ${DETECTOR_OUTPUT}" >&2
        failures=$((failures + 1))
    else
        echo "  PASS: ${label} clean"
    fi
    return 0
}

# ── Pattern (a): function without an explicit return ──
mkdir -p "${TMPDIR_TEST}/pat_a_pos" "${TMPDIR_TEST}/pat_a_neg"
cat > "${TMPDIR_TEST}/pat_a_pos/missing_return.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
report() {
    echo "reporting"
}
report
EOF
cat > "${TMPDIR_TEST}/pat_a_neg/explicit_return.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
report() {
    echo "reporting"
    return 0
}
report
EOF
assert_pattern_flags "${TMPDIR_TEST}/pat_a_pos" "no explicit return statement" \
    "pattern (a) function without return"
assert_pattern_clean "${TMPDIR_TEST}/pat_a_neg" "no explicit return statement" \
    "pattern (a) function with return"

# ── Pattern (b): diagnostic message on stdout instead of stderr ──
mkdir -p "${TMPDIR_TEST}/pat_b_pos" "${TMPDIR_TEST}/pat_b_neg"
cat > "${TMPDIR_TEST}/pat_b_pos/stdout_diag.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
probe() {
    echo "WARNING: probe degraded"
    return 0
}
probe
EOF
cat > "${TMPDIR_TEST}/pat_b_neg/stderr_diag.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
probe() {
    echo "WARNING: probe degraded" >&2
    return 0
}
probe
EOF
assert_pattern_flags "${TMPDIR_TEST}/pat_b_pos" "diagnostic message on stdout" \
    "pattern (b) diagnostic on stdout"
assert_pattern_clean "${TMPDIR_TEST}/pat_b_neg" "diagnostic message on stdout" \
    "pattern (b) diagnostic on stderr"

# ── Pattern (c): single-bracket [ ] instead of [[ ]] ──
mkdir -p "${TMPDIR_TEST}/pat_c_pos" "${TMPDIR_TEST}/pat_c_neg"
cat > "${TMPDIR_TEST}/pat_c_pos/single_bracket.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
if [ "${MODE}" = "strict" ]; then
    echo "strict" >&2
fi
EOF
cat > "${TMPDIR_TEST}/pat_c_neg/double_bracket.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
if [[ "${MODE}" = "strict" ]]; then
    echo "strict" >&2
fi
EOF
assert_pattern_flags "${TMPDIR_TEST}/pat_c_pos" "use '[[ ]]' instead of '[ ]'" \
    "pattern (c) single-bracket test"
assert_pattern_clean "${TMPDIR_TEST}/pat_c_neg" "use '[[ ]]' instead of '[ ]'" \
    "pattern (c) double-bracket test"

# ── Pattern (d): case statement without a default *) clause ──
mkdir -p "${TMPDIR_TEST}/pat_d_pos" "${TMPDIR_TEST}/pat_d_neg"
cat > "${TMPDIR_TEST}/pat_d_pos/no_default.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
case "${MODE}" in
    strict)
        echo "strict" >&2
        ;;
    lenient)
        echo "lenient" >&2
        ;;
esac
EOF
cat > "${TMPDIR_TEST}/pat_d_neg/with_default.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
case "${MODE}" in
    strict)
        echo "strict" >&2
        ;;
    *)
        echo "unknown mode" >&2
        exit 2
        ;;
esac
EOF
assert_pattern_flags "${TMPDIR_TEST}/pat_d_pos" "case statement missing default" \
    "pattern (d) case without default"
assert_pattern_clean "${TMPDIR_TEST}/pat_d_neg" "case statement missing default" \
    "pattern (d) case with default"

# ── Pattern (e): curl -X HEAD instead of --head / -I ──
mkdir -p "${TMPDIR_TEST}/pat_e_pos" "${TMPDIR_TEST}/pat_e_neg"
cat > "${TMPDIR_TEST}/pat_e_pos/method_override.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
curl -s -X HEAD http://example.invalid/ >/dev/null
EOF
cat > "${TMPDIR_TEST}/pat_e_neg/head_option.sh" << 'EOF'
#!/bin/bash
set -euo pipefail
curl -s --head http://example.invalid/ >/dev/null
EOF
assert_pattern_flags "${TMPDIR_TEST}/pat_e_pos" "instead of 'curl -X HEAD'" \
    "pattern (e) curl -X HEAD"
assert_pattern_clean "${TMPDIR_TEST}/pat_e_neg" "instead of 'curl -X HEAD'" \
    "pattern (e) curl --head"

# ---------------------------------------------------------------------------
# Scan-failure contract: a recursive grep that fails hard (rc=2) must
# make the detector non-zero.  Before the fix the (b)/(c)/(e) feeds dropped
# grep's status with `2>/dev/null || true`, so an unreadable tree produced
# "PASS: no shell hygiene findings" and exit 0 — a silent fail-open.
# ---------------------------------------------------------------------------
fake_bin="${TMPDIR_TEST}/fakebin"
mkdir -p "${fake_bin}"
cat > "${fake_bin}/grep" << 'EOF'
#!/bin/bash
# Recursive invocations fail hard; everything else delegates to realgrep.
for a in "$@"; do
    case "$a" in
        --) break ;;
        -rnE|-rn|-r|-R|-rE|--recursive) echo "grep: fake recursive failure" >&2; exit 2 ;;
    esac
done
exec /usr/bin/grep "$@"
EOF
chmod +x "${fake_bin}/grep"

failopen_fixture="${TMPDIR_TEST}/failopen"
mkdir -p "${failopen_fixture}"
cat > "${failopen_fixture}/defects.sh" << 'EOF'
#!/bin/bash
probe() {
    echo "ERROR: diagnostic on stdout"
    return 0
}
probe
curl -X HEAD http://example.invalid/ >/dev/null
EOF

# The fixture must be detected when grep works, so the regression below cannot
# pass vacuously on an empty scan.
run_detector "${failopen_fixture}"
if [[ "${DETECTOR_RC}" -eq 1 ]]; then
    echo "  PASS: control run detects the fixture defects (exit 1)"
else
    echo "  FAIL: control run did not detect fixture defects (exit ${DETECTOR_RC})" >&2
    failures=$((failures + 1))
fi

grep_status_output=""
grep_status_rc=0
if grep_status_output="$(PATH="${fake_bin}:${PATH}" \
    bash "${SCRIPT_DIR}/../detect_shell_hygiene.sh" "${failopen_fixture}" 2>&1)"; then
    grep_status_rc=0
else
    grep_status_rc=$?
fi
if [[ "${grep_status_rc}" -ne 0 ]] \
    && [[ "${grep_status_output}" == *"grep scan failed"* ]]; then
    echo "  PASS: failing recursive grep is a hard error (exit ${grep_status_rc})"
else
    echo "  FAIL: failing recursive grep was hidden (exit ${grep_status_rc})" >&2
    echo "        output: ${grep_status_output}" >&2
    failures=$((failures + 1))
fi

# A detector crash must not be mistaken for a clean result.  This exercises
# the shared status capture path with a deliberately missing executable.
run_arbitrary_detector \
    "${TMPDIR_TEST}/missing-detector.sh" "${TMPDIR_TEST}/case4"
if [[ "${DETECTOR_RC}" -ge 2 ]]; then
    echo "  PASS: detector crash is visible (exit ${DETECTOR_RC})"
else
    echo "  FAIL: detector crash was hidden (exit ${DETECTOR_RC}): ${DETECTOR_OUTPUT}" >&2
    failures=$((failures + 1))
fi

if [[ "${failures}" -gt 0 ]]; then
    echo "FAILED: ${failures} case(s)" >&2
    exit 1
fi
echo "All detect_shell_hygiene pattern (f) tests passed."
exit 0
