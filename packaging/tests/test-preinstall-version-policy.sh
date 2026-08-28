#!/bin/bash
# test-preinstall-version-policy.sh — Unit test for preinstall.sh exact-version policy.
#
# Verifies the three version-policy scenarios WITHOUT installing anything:
#   PASS            - installed NGINX version == target (exact)
#   REJECT same-minor  - installed NGINX 1.26.4 vs target 1.26.3 (patch diff → fatal)
#   REJECT diff-minor  - installed NGINX 1.27.0 vs target 1.26.3 (minor diff → fatal)
#   REJECT unparseable - nginx -v output cannot prove ABI compatibility
#
# preinstall.sh reads `nginx -v` from PATH; this test injects a fake
# `nginx` executable that prints a configurable version, then invokes the
# real preinstall script with a baked-in target version.
#
# The script under test establishes its own trusted PATH via TRUSTED_PATH_ROOT.
# Tests redirect TRUSTED_PATH_ROOT into a sandbox filesystem tree so that the
# script resolves commands only within the sandbox — the host's real PATH and
# real nginx are unreachable.
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

##############################################################################
# Sandbox setup: build a mini filesystem tree that TRUSTED_PATH_ROOT points to.
# The script under test sets PATH="${TRUSTED_PATH_ROOT}/usr/sbin:..." so we
# must place fake nginx in ${FAKE_ROOT}/usr/sbin and symlink needed utilities
# into ${FAKE_ROOT}/usr/bin.
##############################################################################

FAKE_ROOT="$(mktemp -d)"
trap 'rm -rf "${FAKE_ROOT}"' EXIT

mkdir -p "${FAKE_ROOT}/usr/sbin"
mkdir -p "${FAKE_ROOT}/usr/bin"
mkdir -p "${FAKE_ROOT}/sbin"
mkdir -p "${FAKE_ROOT}/bin"

# Symlink real commands needed by preinstall.sh into the sandbox.
# Only link the fixed manifest — never recursive copy.
for cmd in cat sed readlink rm rmdir printf basename stat; do
    real_path="$(command -v "${cmd}" 2>/dev/null || true)"
    if [[ -n "${real_path}" ]]; then
        ln -sf "${real_path}" "${FAKE_ROOT}/usr/bin/${cmd}"
    fi
done

PASS_COUNT=0
FAIL_COUNT=0

pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "PASS: $1" >&2; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "FAIL: $1" >&2; }

# fake_nginx — install a fake nginx binary reporting $1 as its version.
fake_nginx() {
    local version="$1"
    cat > "${FAKE_ROOT}/usr/sbin/nginx" <<EOF
#!/bin/bash
echo "nginx version: nginx/${version}"
exit 0
EOF
    chmod +x "${FAKE_ROOT}/usr/sbin/nginx"
}

# run_preinstall — run the real preinstall script with target baked in and
# TRUSTED_PATH_ROOT redirected into the sandbox.
#
# Two sed substitutions:
#   1. Replace %%NGINX_VERSION%% with the target version
#   2. Replace TRUSTED_PATH_ROOT="" with TRUSTED_PATH_ROOT="${FAKE_ROOT}"
#
# After sed, ASSERT that the TRUSTED_PATH_ROOT substitution succeeded — if
# the script's variable assignment syntax ever changes, the test must fail
# loudly rather than silently testing an unhardened script.
run_preinstall() {
    local target="$1"
    local action="${2:-install}"
    local script
    script="$(sed -e "s|%%NGINX_VERSION%%|${target}|g" \
                  -e "s|^TRUSTED_PATH_ROOT=\"\"$|TRUSTED_PATH_ROOT=\"${FAKE_ROOT}\"|" \
                  "${PREINSTALL}")"

    # Assert the substitution landed
    if ! echo "${script}" | grep -q "TRUSTED_PATH_ROOT=\"${FAKE_ROOT}\""; then
        echo "FATAL: TRUSTED_PATH_ROOT substitution failed — test infrastructure broken" >&2
        exit 2
    fi

    bash -c "${script}" preinstall.sh "${action}" 2>/dev/null
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
rc=0
run_preinstall "1.26.3" || rc=$?
if [[ "$rc" -eq 0 ]]; then
    fail "same-minor patch diff (1.26.4 vs 1.26.3) must be REJECTED"
else
    if [[ "$rc" -eq 1 ]]; then
        pass "same-minor patch diff rejected with status 1"
    else
        fail "same-minor patch diff returned unexpected status $rc"
    fi
fi

# Scenario 3: different minor → fatal (exit 1)
fake_nginx "1.27.0"
rc=0
run_preinstall "1.26.3" || rc=$?
if [[ "$rc" -eq 0 ]]; then
    fail "different-minor (1.27.0 vs 1.26.3) must be REJECTED"
else
    if [[ "$rc" -eq 1 ]]; then
        pass "different-minor rejected with status 1"
    else
        fail "different-minor returned unexpected status $rc"
    fi
fi

# Scenario 4: major diff → fatal (exit 1)
fake_nginx "2.0.0"
rc=0
run_preinstall "1.26.3" || rc=$?
if [[ "$rc" -eq 0 ]]; then
    fail "major diff (2.0.0 vs 1.26.3) must be REJECTED"
else
    if [[ "$rc" -eq 1 ]]; then
        pass "major diff rejected with status 1"
    else
        fail "major diff returned unexpected status $rc"
    fi
fi

# Scenario 5: nginx not installed → proceed (exit 0).
# Since the trusted PATH only resolves within FAKE_ROOT, not placing nginx in
# the sandbox means the host's real nginx is unreachable — this is STRONGER
# than the old PATH-prefix approach.
rm -f "${FAKE_ROOT}/usr/sbin/nginx"
if run_preinstall "1.26.3"; then
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
# and exit 1 because an unparseable version cannot prove ABI compatibility.
fake_nginx "1.26.3"
if run_preinstall "1.26.3"; then
    pass "resolved-identity probe: matching fake binary proceeds"
else
    fail "matching fake binary should proceed via resolved identity"
fi

cat > "${FAKE_ROOT}/usr/sbin/nginx" <<'EOF'
#!/bin/bash
echo "nginx: corrupted -v output simulation" >&2
exit 0
EOF
chmod +x "${FAKE_ROOT}/usr/sbin/nginx"
if run_preinstall "1.26.3"; then
    fail "unparseable 'nginx -v' output must abort with exit 1"
else
    pass "unparseable 'nginx -v' output aborts with exit 1"
fi

# Scenario 8: executable failure must also abort rather than proceed without
# a version check.
cat > "${FAKE_ROOT}/usr/sbin/nginx" <<'EOF'
#!/bin/bash
echo "nginx: simulated execution failure" >&2
exit 1
EOF
chmod +x "${FAKE_ROOT}/usr/sbin/nginx"
if run_preinstall "1.26.3"; then
    fail "failed 'nginx -v' execution must abort with exit 1"
else
    pass "failed 'nginx -v' execution aborts with exit 1"
fi

# Scenario 9: an unrendered package template must never compare against the
# literal placeholder and proceed.
fake_nginx "1.26.3"
unresolved_script="$(sed -e "s|^TRUSTED_PATH_ROOT=\"\"$|TRUSTED_PATH_ROOT=\"${FAKE_ROOT}\"|" \
    "${PREINSTALL}")"
rc=0
bash -c "${unresolved_script}" preinstall.sh install 2>/dev/null || rc=$?
if [[ "${rc}" -eq 1 ]]; then
    pass "unresolved package target is rejected"
else
    fail "unresolved package target returned unexpected status ${rc}"
fi

# Scenario 10: negative control (T-B4) — simultaneous PATH injection AND
# TRUSTED_PATH_ROOT environment variable injection must both be ineffective.
#
# The script contains a literal `TRUSTED_PATH_ROOT=""` assignment that
# overwrites any inherited environment variable. We verify this by:
#   1. Creating an EVIL directory outside the sandbox with a fake nginx
#      that touches a MARKER file
#   2. Running with TRUSTED_PATH_ROOT="${EVIL}" in the environment
#   3. Asserting the MARKER does NOT exist (evil nginx was never executed)
#   4. Inspecting only the marker result.  The script's exit status is not
#      part of this negative-control assertion because the host PATH may lack
#      one of the commands used by the isolated script.
EVIL_DIR="$(mktemp -d)"
MARKER="${EVIL_DIR}/MARKER_SHOULD_NOT_EXIST"
mkdir -p "${EVIL_DIR}/usr/sbin"

cat > "${EVIL_DIR}/usr/sbin/nginx" <<EOF
#!/bin/bash
touch "${MARKER}"
echo "nginx version: nginx/1.26.3"
exit 0
EOF
chmod +x "${EVIL_DIR}/usr/sbin/nginx"

# Also place the evil nginx directly in EVIL_DIR for a bare PATH injection
cat > "${EVIL_DIR}/nginx" <<EOF
#!/bin/bash
touch "${MARKER}"
echo "nginx version: nginx/1.26.3"
exit 0
EOF
chmod +x "${EVIL_DIR}/nginx"

# Run preinstall WITHOUT our sandbox substitution — use the script as-is
# (only substitute %%NGINX_VERSION%%) so TRUSTED_PATH_ROOT="" stays literal.
# The attacker injects TRUSTED_PATH_ROOT via environment.
evil_script="$(sed "s|%%NGINX_VERSION%%|1.26.3|g" "${PREINSTALL}")"
if ! PATH="${EVIL_DIR}:${PATH}" TRUSTED_PATH_ROOT="${EVIL_DIR}" \
    bash -c "${evil_script}" preinstall.sh install 2>/dev/null; then
    echo "Scenario 10: preinstall returned nonzero; continuing with the " \
        "marker assertion" >&2
fi

if [[ -f "${MARKER}" ]]; then
    fail "Scenario 10: MARKER exists — evil nginx was executed despite trusted PATH"
else
    pass "Scenario 10: MARKER does not exist — evil nginx was NOT executed"
fi

rm -rf "${EVIL_DIR}"

echo "" >&2
echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed" >&2

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
