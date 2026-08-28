#!/bin/bash
#
# test_detect_shell_hygiene.sh - Fixture tests for detect_shell_hygiene.sh
# pattern (f): $? inside negated conditional bodies (Rules 11/18).
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
# the actual terminator.  The nested clean branch remains under the outer
# negated branch and therefore its status read must still be flagged.
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
assert_detector_flags "${TMPDIR_TEST}/case4c" \
    "terminator operand is not a block close"

# Fixture 5: nested control flow must retain the outer negated branch.
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

assert_detector_flags "${TMPDIR_TEST}/case5" "nested negated branch"

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
