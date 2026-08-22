#!/bin/bash
# test-preinstall-version-policy.sh — Unit test for preinstall.sh exact-version policy.
#
# Verifies the three version-policy scenarios WITHOUT installing anything:
#   PASS            - installed NGINX version == target (exact)
#   REJECT same-minor  - installed NGINX 1.26.4 vs target 1.26.3 (patch diff → fatal)
#   REJECT diff-minor  - installed NGINX 1.27.0 vs target 1.26.3 (minor diff → fatal)
#
# preinstall.sh reads `nginx -v` from PATH; this test injects a fake
# `nginx` executable that prints a configurable version, then invokes the
# real preinstall script with a baked-in target version.
#
# Usage:
#   ./test-preinstall-version-policy.sh
#
# Exit codes:
#   0 — all scenarios pass
#   1 — one or more scenarios failed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREINSTALL="${SCRIPT_DIR}/../nfpm/scripts/preinstall.sh"
FAKE_BIN_DIR="$(mktemp -d)"
trap 'rm -rf "${FAKE_BIN_DIR}"' EXIT

PASS_COUNT=0
FAIL_COUNT=0

pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "PASS: $1" >&2; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "FAIL: $1" >&2; }

# fake_nginx — install a fake nginx binary reporting $1 as its version.
fake_nginx() {
    local version="$1"
    cat > "${FAKE_BIN_DIR}/nginx" <<EOF
#!/bin/bash
echo "nginx version: nginx/${version}"
exit 0
EOF
    chmod +x "${FAKE_BIN_DIR}/nginx"
}

# run_preinstall — run the real preinstall script with target baked in.
# preinstall.sh reads the lifecycle action as $1; `bash -c` treats the word
# after the script string as $0 (the process name), so pass a placeholder
# name first and the action as the real first argument.
run_preinstall() {
    local target="$1"
    local action="${2:-install}"
    local script
    script="$(sed "s|%%NGINX_VERSION%%|${target}|g" "${PREINSTALL}")"
    PATH="${FAKE_BIN_DIR}:${PATH}" bash -c "${script}" preinstall.sh "${action}" 2>/dev/null
    return $?
}

echo "========================================" >&2
echo "preinstall exact-version policy tests" >&2
echo "========================================" >&2

# Scenario 1: exact match → install proceeds (exit 0)
fake_nginx "1.26.3"
if run_preinstall "1.26.3"; then
    pass "exact match (1.26.3 == 1.26.3) proceeds"
else
    fail "exact match should proceed (exit 0)"
fi

# Scenario 2: same minor, different patch → fatal (exit 1)
fake_nginx "1.26.4"
if run_preinstall "1.26.3"; then
    fail "same-minor patch diff (1.26.4 vs 1.26.3) must be REJECTED"
else
    pass "same-minor patch diff rejected"
fi

# Scenario 3: different minor → fatal (exit 1)
fake_nginx "1.27.0"
if run_preinstall "1.26.3"; then
    fail "different-minor (1.27.0 vs 1.26.3) must be REJECTED"
else
    pass "different-minor rejected"
fi

# Scenario 4: major diff → fatal (exit 1)
fake_nginx "2.0.0"
if run_preinstall "1.26.3"; then
    fail "major diff (2.0.0 vs 1.26.3) must be REJECTED"
else
    pass "major diff rejected"
fi

# Scenario 5: nginx not installed → proceed (exit 0).
# Restrict PATH to the fake dir only so an inherited system nginx on a
# developer/CI machine cannot satisfy the check and mask this scenario.
rm -f "${FAKE_BIN_DIR}/nginx"
if PATH="${FAKE_BIN_DIR}:/usr/bin:/bin" run_preinstall "1.26.3"; then
    pass "nginx not installed → proceed (dependency will handle it)"
else
    fail "missing nginx should proceed"
fi

# Scenario 6: lifecycle action args accepted
fake_nginx "1.26.3"
for action in install upgrade 1 2 abort-upgrade abort-remove abort-deconfigure; do
    if run_preinstall "1.26.3" "${action}"; then
        pass "lifecycle action '${action}' accepted (exit 0)"
    else
        fail "lifecycle action '${action}' should be accepted"
    fi
done

# Scenario 7: executable-trust — discovery and version probe must use the
# SAME resolved identity.  Plant a fake nginx that reports a MATCHING version;
# if the script re-resolved a bare `nginx` from a caller-influenced PATH for
# the probe, a second shadowing binary could bypass or corrupt the check.
# Here: single fake binary, matching version, must proceed; then a tampered
# probe (fake binary stops printing the version line) must take the warn path
# and still exit 0 — never command-not-found (127).
fake_nginx "1.26.3"
if run_preinstall "1.26.3"; then
    pass "resolved-identity probe: matching fake binary proceeds"
else
    fail "matching fake binary should proceed via resolved identity"
fi

cat > "${FAKE_BIN_DIR}/nginx" <<'EOF'
#!/bin/bash
echo "nginx: corrupted -v output simulation" >&2
exit 0
EOF
chmod +x "${FAKE_BIN_DIR}/nginx"
if PATH="${FAKE_BIN_DIR}:/usr/bin:/bin" run_preinstall "1.26.3"; then
    rc_probe=0
else
    rc_probe=$?
fi
if [[ "${rc_probe}" == "0" ]]; then
    pass "unparseable 'nginx -v' output takes the warn path with exit 0 (no command-not-found)"
else
    fail "unparseable 'nginx -v' output should warn and exit 0, got rc=${rc_probe}"
fi

echo "" >&2
echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed" >&2

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
