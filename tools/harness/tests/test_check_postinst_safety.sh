#!/bin/bash
# test_check_postinst_safety.sh — Fixture tests for check_postinst_safety.sh gate.
#
# PURPOSE:
#   Validates that the check_postinst_safety.sh gate correctly detects
#   trusted-PATH violations through structural analysis, using synthetic
#   fixture scripts covering the 4 core detection scenarios, plus a
#   structural assertion that the gate's default target set includes
#   all nfpm scripts.
#
# FIXTURE SCENARIOS:
#   1. No PATH assignment → VIOLATION (missing unconditional trusted PATH)
#   2. Self-referencing PATH ($PATH:/x) → VIOLATION
#   3. PATH after first command (cat before PATH=) → VIOLATION
#   4. Correct script (PATH= without self-ref before any command) → 0 violations
#   5. Default target set includes all 3 nfpm scripts
#
# EXIT CODES:
#   0 — All fixture assertions passed
#   1 — One or more assertions failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

GATE_SCRIPT="${REPO_ROOT}/tools/release/gates/check_postinst_safety.sh"

PASS_COUNT=0
FAIL_COUNT=0

pass() { PASS_COUNT=$((PASS_COUNT + 1)); printf 'PASS: %s\n' "$1" >&2; return 0; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); printf 'FAIL: %s\n' "$1" >&2; return 0; }

##############################################################################
# Setup
##############################################################################

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

if [[ ! -f "${GATE_SCRIPT}" ]]; then
    fail "check_postinst_safety.sh not found at ${GATE_SCRIPT}"
    echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed" >&2
    exit 1
fi

echo "========================================" >&2
echo "Fixture Tests: check_postinst_safety.sh" >&2
echo "========================================" >&2
echo "" >&2

##############################################################################
# Fixture 1: No PATH assignment → VIOLATION
##############################################################################

echo "--- Fixture 1: No PATH assignment ---" >&2

FIXTURE_1="${WORK_DIR}/fixture_no_path.sh"
cat > "${FIXTURE_1}" <<'SCRIPT'
#!/bin/sh
set -e
cat /dev/null
echo "hello" >&2
exit 0
SCRIPT
chmod +x "${FIXTURE_1}"

RC=0
OUTPUT="$(bash "${GATE_SCRIPT}" "${FIXTURE_1}" 2>&1)" || RC=$?

if [[ "${RC}" -ne 0 ]]; then
    pass "Fixture 1: gate returned non-zero (violation detected)"
else
    fail "Fixture 1: gate returned 0 (expected violation)"
fi

if printf '%s\n' "${OUTPUT}" | grep -q "missing unconditional trusted PATH"; then
    pass "Fixture 1: correct violation message (missing unconditional trusted PATH)"
else
    fail "Fixture 1: expected 'missing unconditional trusted PATH' in output"
fi

echo "" >&2

##############################################################################
# Fixture 2: Self-referencing PATH → VIOLATION
##############################################################################

echo "--- Fixture 2: Self-referencing PATH ---" >&2

FIXTURE_2="${WORK_DIR}/fixture_self_ref_path.sh"
cat > "${FIXTURE_2}" <<'SCRIPT'
#!/bin/sh
set -e
PATH="$PATH:/usr/local/bin"
export PATH
cat /dev/null
exit 0
SCRIPT
chmod +x "${FIXTURE_2}"

RC=0
OUTPUT="$(bash "${GATE_SCRIPT}" "${FIXTURE_2}" 2>&1)" || RC=$?

if [[ "${RC}" -ne 0 ]]; then
    pass "Fixture 2: gate returned non-zero (violation detected)"
else
    fail "Fixture 2: gate returned 0 (expected violation)"
fi

# Self-referencing PATH is not unconditional, so gate should report missing
if printf '%s\n' "${OUTPUT}" | grep -qi "VIOLATION"; then
    pass "Fixture 2: VIOLATION reported for self-referencing PATH"
else
    fail "Fixture 2: no VIOLATION reported"
fi

echo "" >&2

##############################################################################
# Fixture 3: PATH after first command → VIOLATION
##############################################################################

echo "--- Fixture 3: PATH after first command ---" >&2

FIXTURE_3="${WORK_DIR}/fixture_path_after_cmd.sh"
cat > "${FIXTURE_3}" <<'SCRIPT'
#!/bin/sh
set -e
cat /dev/null
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
exit 0
SCRIPT
chmod +x "${FIXTURE_3}"

RC=0
OUTPUT="$(bash "${GATE_SCRIPT}" "${FIXTURE_3}" 2>&1)" || RC=$?

if [[ "${RC}" -ne 0 ]]; then
    pass "Fixture 3: gate returned non-zero (violation detected)"
else
    fail "Fixture 3: gate returned 0 (expected violation)"
fi

if printf '%s\n' "${OUTPUT}" | grep -qi "before trusted PATH"; then
    pass "Fixture 3: correct violation (external command before trusted PATH)"
else
    # May also report as missing if cat triggers before PATH is found
    if printf '%s\n' "${OUTPUT}" | grep -qi "VIOLATION"; then
        pass "Fixture 3: VIOLATION reported (command before PATH)"
    else
        fail "Fixture 3: no VIOLATION reported"
    fi
fi

echo "" >&2

##############################################################################
# Fixture 4: Correct script → 0 violations
##############################################################################

echo "--- Fixture 4: Correct script (clean) ---" >&2

FIXTURE_4="${WORK_DIR}/fixture_correct.sh"
cat > "${FIXTURE_4}" <<'SCRIPT'
#!/bin/sh
set -e
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
cat /dev/null
echo "done" >&2
exit 0
SCRIPT
chmod +x "${FIXTURE_4}"

RC=0
OUTPUT="$(bash "${GATE_SCRIPT}" "${FIXTURE_4}" 2>&1)" || RC=$?

if [[ "${RC}" -eq 0 ]]; then
    pass "Fixture 4: gate returned 0 (no violations)"
else
    fail "Fixture 4: gate returned ${RC} (expected 0)"
fi

VIOLATION_COUNT="$(printf '%s\n' "${OUTPUT}" | grep -c '^\[VIOLATION\]' || true)"
VIOLATION_COUNT="${VIOLATION_COUNT//[[:space:]]/}"
if [[ "${VIOLATION_COUNT}" -eq 0 ]]; then
    pass "Fixture 4: zero VIOLATION lines emitted"
else
    fail "Fixture 4: ${VIOLATION_COUNT} VIOLATION lines found (expected 0)"
fi

echo "" >&2

##############################################################################
# Fixture 5: Default target set includes all 3 nfpm scripts
##############################################################################

echo "--- Fixture 5: Default target set coverage ---" >&2

RC=0
OUTPUT="$(cd "${REPO_ROOT}" && bash "${GATE_SCRIPT}" 2>&1)" || RC=$?

NFPM_SCRIPTS_FOUND=0
NFPM_SCRIPTS_MISSING=""

for script_name in "nfpm/scripts/preinstall.sh" "nfpm/scripts/postinstall.sh" "nfpm/scripts/preremove.sh"; do
    if printf '%s\n' "${OUTPUT}" | grep -q "${script_name}"; then
        NFPM_SCRIPTS_FOUND=$((NFPM_SCRIPTS_FOUND + 1))
    else
        NFPM_SCRIPTS_MISSING="${NFPM_SCRIPTS_MISSING} ${script_name}"
    fi
done

if [[ "${NFPM_SCRIPTS_FOUND}" -eq 3 ]]; then
    pass "Fixture 5: default target set includes all 3 nfpm scripts"
else
    fail "Fixture 5: missing nfpm scripts in default set:${NFPM_SCRIPTS_MISSING}"
fi

echo "" >&2

##############################################################################
# Summary
##############################################################################

echo "========================================" >&2
echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed" >&2
echo "========================================" >&2

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    echo "" >&2
    echo "FAIL: One or more fixture assertions failed." >&2
    exit 1
fi

echo "" >&2
echo "All fixture assertions passed." >&2
exit 0
