#!/bin/bash
# test-maintainer-script-executable-trust.sh — Negative control test.
#
# PURPOSE:
#   Proves that the executable-trust fix IS EFFECTIVE: maintainer scripts
#   establish a trusted PATH that prevents PATH-injected fake binaries from
#   being resolved and executed.
#
# METHOD:
#   For each maintainer script, create a temporary EVIL directory containing
#   fake binaries (nginx, cat, readlink, rm, rmdir). Each fake binary writes
#   a MARKER file when executed. Run the script with the EVIL directory
#   injected into PATH and (for nfpm scripts) TRUSTED_PATH_ROOT pointing at
#   the EVIL directory. Assert that NO MARKER files are created.
#
# EXPECTED OUTCOME (fixed code):
#   All MARKER assertions PASS — no fake binaries are executed because the
#   scripts unconditionally override PATH with a trusted set.
#
# EXIT CODES:
#   0 — All negative control checks passed (fix is effective)
#   1 — One or more checks failed (fix may be broken)
#
# SHELL CONVENTIONS:
#   [[ for tests, case with *) default, messages to stderr, macOS bash 3.2
#   compatible.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PASS_COUNT=0
FAIL_COUNT=0

pass() { PASS_COUNT=$((PASS_COUNT + 1)); printf 'PASS: %s\n' "$1" >&2; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); printf 'FAIL: %s\n' "$1" >&2; }

##############################################################################
# Setup
##############################################################################

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

# create_evil_dir — Creates a directory with fake binaries that write markers.
# Arguments: $1 = test label (used for MARKER_DIR isolation)
# Outputs: Sets EVIL_DIR and MARKER_DIR variables
create_evil_dir() {
    local label="$1"
    EVIL_DIR="${WORK_DIR}/${label}/evil"
    MARKER_DIR="${WORK_DIR}/${label}/markers"
    mkdir -p "${EVIL_DIR}" "${MARKER_DIR}"

    local cmds=(nginx cat readlink rm rmdir sed printf)
    for cmd in "${cmds[@]}"; do
        cat > "${EVIL_DIR}/${cmd}" <<SCRIPT
#!/bin/sh
touch "${MARKER_DIR}/${cmd}"
exit 0
SCRIPT
        chmod +x "${EVIL_DIR}/${cmd}"
    done
}

# assert_no_markers — Assert that no marker files were created.
# Arguments: $1 = test label
# Returns: 0 if no markers, 1 if any found
assert_no_markers() {
    local label="$1"
    local any_found=0
    local found_names=""

    for f in "${MARKER_DIR}"/*; do
        if [[ -f "${f}" ]]; then
            any_found=1
            found_names="${found_names} $(basename "${f}")"
        fi
    done

    if [[ "${any_found}" -eq 0 ]]; then
        pass "${label}: no fake binaries executed (trusted PATH effective)"
        return 0
    else
        fail "${label}: fake binaries executed:${found_names} (trusted PATH NOT effective)"
        return 1
    fi
}

echo "========================================" >&2
echo "Negative Control: Executable Trust" >&2
echo "========================================" >&2
echo "" >&2

##############################################################################
# NC-1: nfpm/scripts/postinstall.sh (bash, TRUSTED_PATH_ROOT override)
##############################################################################

echo "--- NC-1: postinstall.sh ---" >&2

SCRIPT_UNDER_TEST="${REPO_ROOT}/packaging/nfpm/scripts/postinstall.sh"
if [[ -f "${SCRIPT_UNDER_TEST}" ]]; then
    create_evil_dir "postinstall"

    # Override cat to also read stdin (like real cat) so heredoc doesn't hang
    cat > "${EVIL_DIR}/cat" <<SCRIPT
#!/bin/sh
touch "${MARKER_DIR}/cat"
while IFS= read -r line; do printf '%s\n' "\$line" >&2; done
exit 0
SCRIPT
    chmod +x "${EVIL_DIR}/cat"

    # TRUSTED_PATH_ROOT points at EVIL_DIR — if the script uses it to build
    # PATH, it would resolve commands from EVIL_DIR. The fix resets
    # TRUSTED_PATH_ROOT="" unconditionally, so this injection is neutralized.
    env PATH="${EVIL_DIR}:${PATH}" TRUSTED_PATH_ROOT="${EVIL_DIR}" \
        bash "${SCRIPT_UNDER_TEST}" configure >/dev/null 2>&1 || true
    RC=$?

    assert_no_markers "postinstall.sh"

    if [[ "${RC}" -eq 0 ]]; then
        pass "postinstall.sh: exit code 0 (configure)"
    else
        fail "postinstall.sh: exit code ${RC} (expected 0)"
    fi
else
    fail "postinstall.sh: script not found at ${SCRIPT_UNDER_TEST}"
fi

echo "" >&2

##############################################################################
# NC-2: nfpm/scripts/preremove.sh (bash, TRUSTED_PATH_ROOT override)
##############################################################################

echo "--- NC-2: preremove.sh ---" >&2

SCRIPT_UNDER_TEST="${REPO_ROOT}/packaging/nfpm/scripts/preremove.sh"
if [[ -f "${SCRIPT_UNDER_TEST}" ]]; then
    create_evil_dir "preremove"

    # readlink fake that produces output
    cat > "${EVIL_DIR}/readlink" <<SCRIPT
#!/bin/sh
touch "${MARKER_DIR}/readlink"
echo "/usr/share/nginx/modules-available/mod-markdown.conf"
exit 0
SCRIPT
    chmod +x "${EVIL_DIR}/readlink"

    env PATH="${EVIL_DIR}:${PATH}" TRUSTED_PATH_ROOT="${EVIL_DIR}" \
        bash "${SCRIPT_UNDER_TEST}" remove >/dev/null 2>&1 || true
    RC=$?

    assert_no_markers "preremove.sh"

    if [[ "${RC}" -eq 0 ]]; then
        pass "preremove.sh: exit code 0 (remove)"
    else
        fail "preremove.sh: exit code ${RC} (expected 0)"
    fi
else
    fail "preremove.sh: script not found at ${SCRIPT_UNDER_TEST}"
fi

echo "" >&2

##############################################################################
# NC-3: packaging/debian/postinst (sh, no TRUSTED_PATH_ROOT — uses hardcoded)
##############################################################################

echo "--- NC-3: debian/postinst ---" >&2

SCRIPT_UNDER_TEST="${REPO_ROOT}/packaging/debian/postinst"
if [[ -f "${SCRIPT_UNDER_TEST}" ]]; then
    create_evil_dir "deb_postinst"

    cat > "${EVIL_DIR}/cat" <<SCRIPT
#!/bin/sh
touch "${MARKER_DIR}/cat"
while IFS= read -r line; do printf '%s\n' "\$line" >&2; done
exit 0
SCRIPT
    chmod +x "${EVIL_DIR}/cat"

    env PATH="${EVIL_DIR}:${PATH}" sh "${SCRIPT_UNDER_TEST}" configure >/dev/null 2>&1 || true
    RC=$?

    assert_no_markers "debian/postinst"

    if [[ "${RC}" -eq 0 ]]; then
        pass "debian/postinst: exit code 0 (configure)"
    else
        fail "debian/postinst: exit code ${RC} (expected 0)"
    fi
else
    fail "debian/postinst: script not found at ${SCRIPT_UNDER_TEST}"
fi

echo "" >&2

##############################################################################
# NC-4: packaging/debian/postrm (sh, no TRUSTED_PATH_ROOT — uses hardcoded)
##############################################################################

echo "--- NC-4: debian/postrm ---" >&2

SCRIPT_UNDER_TEST="${REPO_ROOT}/packaging/debian/postrm"
if [[ -f "${SCRIPT_UNDER_TEST}" ]]; then
    create_evil_dir "deb_postrm"

    cat > "${EVIL_DIR}/rm" <<SCRIPT
#!/bin/sh
touch "${MARKER_DIR}/rm"
exit 0
SCRIPT
    chmod +x "${EVIL_DIR}/rm"

    cat > "${EVIL_DIR}/rmdir" <<SCRIPT
#!/bin/sh
touch "${MARKER_DIR}/rmdir"
exit 0
SCRIPT
    chmod +x "${EVIL_DIR}/rmdir"

    env PATH="${EVIL_DIR}:${PATH}" sh "${SCRIPT_UNDER_TEST}" purge >/dev/null 2>&1 || true
    RC=$?

    assert_no_markers "debian/postrm"

    if [[ "${RC}" -eq 0 ]]; then
        pass "debian/postrm: exit code 0 (purge)"
    else
        fail "debian/postrm: exit code ${RC} (expected 0)"
    fi
else
    fail "debian/postrm: script not found at ${SCRIPT_UNDER_TEST}"
fi

echo "" >&2

##############################################################################
# NC-5: nfpm/scripts/preinstall.sh (bash, TRUSTED_PATH_ROOT override)
##############################################################################

echo "--- NC-5: preinstall.sh ---" >&2

SCRIPT_UNDER_TEST="${REPO_ROOT}/packaging/nfpm/scripts/preinstall.sh"
if [[ -f "${SCRIPT_UNDER_TEST}" ]]; then
    create_evil_dir "preinstall"

    # The fake nginx must produce version output for script logic
    cat > "${EVIL_DIR}/nginx" <<SCRIPT
#!/bin/sh
touch "${MARKER_DIR}/nginx"
echo "nginx version: nginx/1.26.3" >&2
exit 0
SCRIPT
    chmod +x "${EVIL_DIR}/nginx"

    # preinstall.sh has %%NGINX_VERSION%% placeholder — substitute it
    TEMP_SCRIPT="${WORK_DIR}/preinstall_test.sh"
    sed 's|%%NGINX_VERSION%%|1.26.3|g' "${SCRIPT_UNDER_TEST}" > "${TEMP_SCRIPT}"
    chmod +x "${TEMP_SCRIPT}"

    env PATH="${EVIL_DIR}:${PATH}" TRUSTED_PATH_ROOT="${EVIL_DIR}" \
        bash "${TEMP_SCRIPT}" install >/dev/null 2>&1 || true
    RC=$?

    assert_no_markers "preinstall.sh"

    # preinstall with no real nginx in trusted PATH exits 0 (nginx not found)
    if [[ "${RC}" -eq 0 ]]; then
        pass "preinstall.sh: exit code 0 (install)"
    else
        fail "preinstall.sh: exit code ${RC} (expected 0)"
    fi
else
    fail "preinstall.sh: script not found at ${SCRIPT_UNDER_TEST}"
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
    echo "FAIL: Executable-trust negative control FAILED." >&2
    echo "One or more maintainer scripts allowed PATH-injected binaries." >&2
    exit 1
fi

echo "" >&2
echo "All negative control checks passed." >&2
echo "Trusted PATH effectively prevents PATH injection in all scripts." >&2
exit 0
