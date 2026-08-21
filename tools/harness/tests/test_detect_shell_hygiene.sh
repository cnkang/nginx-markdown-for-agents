#!/bin/bash
#
# test_detect_shell_hygiene.sh - Fixture tests for detect_shell_hygiene.sh
# pattern (f): $? inside negated conditional bodies (Rule 11/18, 6fcf1bb9).
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

# The detector exits non-zero when it finds violations, so capture output
# before inspecting it (pipefail would invert the pipeline status).
run_detector() {
    local fixture="$1"
    bash "${SCRIPT_DIR}/../detect_shell_hygiene.sh" "${fixture}" 2>&1 \
        || true
    return 0
}

assert_detector_flags() {
    local fixture="$1"
    local label="$2"
    local output
    output="$(run_detector "${fixture}")"
    if [[ "${output}" == *"ERROR"*"negated conditional"* ]]; then
        echo "  PASS: ${label} flagged"
    else
        echo "  FAIL: ${label} not flagged"
        failures=$((failures + 1))
    fi
    return 0
}

assert_detector_clean() {
    local fixture="$1"
    local label="$2"
    local output
    output="$(run_detector "${fixture}")"
    if [[ "${output}" == *"ERROR"*"negated conditional"* ]]; then
        echo "  FAIL: ${label} wrongly flagged"
        failures=$((failures + 1))
    else
        echo "  PASS: ${label} clean"
    fi
    return 0
}

# Fixture 1: multi-line dead branch (the 6fcf1bb9 defect shape).
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

if [[ "${failures}" -gt 0 ]]; then
    echo "FAILED: ${failures} case(s)" >&2
    exit 1
fi
echo "All detect_shell_hygiene pattern (f) tests passed."
exit 0
