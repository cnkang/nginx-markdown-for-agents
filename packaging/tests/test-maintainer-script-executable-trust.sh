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
#   a MARKER file when executed. The test runs a copy of each script with only
#   its fixed package paths redirected to a temporary fixture tree, so cleanup
#   branches execute without touching host /etc/nginx. It then injects the
#   EVIL directory through PATH and TRUSTED_PATH_ROOT, and asserts that NO
#   EVIL marker files are created.
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

pass() { PASS_COUNT=$((PASS_COUNT + 1)); printf 'PASS: %s\n' "$1" >&2; return 0; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); printf 'FAIL: %s\n' "$1" >&2; return 0; }

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
    TRUSTED_MARKER_DIR="${WORK_DIR}/${label}/trusted-markers"
    mkdir -p "${EVIL_DIR}" "${MARKER_DIR}" "${TRUSTED_MARKER_DIR}"

    local cmds=(nginx cat grep readlink rm rmdir sed printf stat)
    for cmd in "${cmds[@]}"; do
        cat > "${EVIL_DIR}/${cmd}" <<SCRIPT
#!/bin/sh
touch "${MARKER_DIR}/${cmd}"
exit 0
SCRIPT
        chmod +x "${EVIL_DIR}/${cmd}"
    done
}

# link_trusted_command — Link a host utility into the temporary trusted PATH.
# link_trusted_command links an available executable into the sandbox trusted command directory.
link_trusted_command() {
    local command_name="$1"
    local command_path=""

    if ! command_path="$(command -v "${command_name}")"; then
        fail "sandbox: trusted command not found: ${command_name}"
        return 1
    fi
    if [[ -z "${command_path}" || ! -x "${command_path}" ]]; then
        fail "sandbox: trusted command not found: ${command_name}"
        return 1
    fi

    ln -s "${command_path}" "${SANDBOX_ROOT}/usr/bin/${command_name}"
    return 0
}

# prepare_sandboxed_script — Copy a maintainer script into a temporary root.
# The copy preserves the unconditional PATH overwrite while redirecting its
# fixed paths to a fixture. It never executes the production script directly.
# Arguments: $1 = source script, $2 = fixture label, $3 = nfpm
# Outputs: Sets SANDBOX_ROOT and SCRIPT_UNDER_TEST variables
prepare_sandboxed_script() {
    local source_script="$1"
    local label="$2"
    local script_family="$3"
    local script_dir=""
    local command_name=""

    SANDBOX_ROOT="${WORK_DIR}/${label}/root"
    script_dir="${WORK_DIR}/${label}/script"
    SCRIPT_UNDER_TEST="${script_dir}/${source_script##*/}"

    mkdir -p "${SANDBOX_ROOT}/usr/bin" \
        "${SANDBOX_ROOT}/sbin" \
        "${SANDBOX_ROOT}/bin" \
        "${SANDBOX_ROOT}/etc/nginx/modules-enabled" \
        "${SANDBOX_ROOT}/etc/nginx/modules-available" \
        "${SANDBOX_ROOT}/usr/share/nginx/modules-available" \
        "${script_dir}"
    # Match common Linux layouts where /usr/sbin is a symlink to /usr/bin.
    # The trust check must validate the canonical target, not the symlink's
    # conventional 0777 mode bits.
    ln -s "${SANDBOX_ROOT}/usr/bin" "${SANDBOX_ROOT}/usr/sbin"

    for command_name in cat grep readlink rm rmdir sed stat; do
        link_trusted_command "${command_name}" || return 1
    done

    case "${script_family}" in
        nfpm)
            local rewritten_script
            rewritten_script="$(sed \
                -e "s|TRUSTED_PATH_ROOT=\"\"|TRUSTED_PATH_ROOT=\"${SANDBOX_ROOT}\"|" \
                -e "s|/etc/nginx|${SANDBOX_ROOT}/etc/nginx|g" \
                -e "s|/usr/share/nginx|${SANDBOX_ROOT}/usr/share/nginx|g" \
                -e 's|%%NGINX_VERSION%%|1.26.3|g' \
                "${source_script}")"
            if ! printf '%s\n' "$rewritten_script" | grep -Fq \
                "TRUSTED_PATH_ROOT=\"${SANDBOX_ROOT}\""; then
                fail "sandbox: TRUSTED_PATH_ROOT substitution did not match"
                return 1
            fi
            printf '%s\n' "$rewritten_script" > "${SCRIPT_UNDER_TEST}"
            ;;
        *)
            fail "sandbox: unknown script family: ${script_family}"
            return 1
            ;;
    esac

    chmod +x "${SCRIPT_UNDER_TEST}"
    return 0
}

# create_trusted_nginx — Provide a safe fixture nginx for preinstall.sh.
create_trusted_nginx() {
    cat > "${SANDBOX_ROOT}/usr/sbin/nginx" <<SCRIPT
#!/bin/sh
: > "${TRUSTED_MARKER_DIR}/nginx"
printf '%s\\n' 'nginx version: nginx/1.26.3' >&2
exit 0
SCRIPT
    chmod +x "${SANDBOX_ROOT}/usr/sbin/nginx"
    return 0
}

# run_sandboxed_script — Run a fixture copy and retain its real exit code.
# Arguments: $1 = shell interpreter, remaining arguments = lifecycle action
run_sandboxed_script() {
    local shell_interpreter="$1"
    shift

    RC=0
    env PATH="${EVIL_DIR}:${PATH}" TRUSTED_PATH_ROOT="${EVIL_DIR}" \
        "${shell_interpreter}" "${SCRIPT_UNDER_TEST}" "$@" >/dev/null 2>&1 || RC=$?
    return 0
}

# assert_no_markers — Assert that no marker files were created.
# Arguments: $1 = test label
# assert_no_markers verifies that no fake binaries executed and records the result without changing the test's control flow.
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
    else
        fail "${label}: fake binaries executed:${found_names} (trusted PATH NOT effective)"
    fi
    return 0
}

# assert_path_present — Assert that a package cleanup path remains because the
# package does not own operator-managed module configuration.
# assert_path_present verifies that a sandbox path exists or is a symbolic link and records the result under the provided label.
assert_path_present() {
    local label="$1"
    local path="$2"

    if [[ -e "${path}" || -L "${path}" ]]; then
        pass "${label}: sandbox path preserved"
    else
        fail "${label}: sandbox path was removed unexpectedly"
    fi
    return 0
}

# assert_default_trusted_root — Verify that an empty prefix denotes the real
# filesystem root rather than the caller's HOME directory.  Production
# maintainer scripts use the empty prefix on installed hosts; this contract is
# intentionally checked separately from the temporary-root PATH-injection
# fixtures below.
# Arguments: $1 = maintainer script containing path_is_trusted_root
assert_default_trusted_root() {
    local source_script="$1"
    local function_source=""

    if [[ ! -f "${source_script}" ]]; then
        fail "default trusted root: script not found: ${source_script}"
        return 0
    fi

    function_source="$(sed -n '/^path_is_trusted_root()/,/^}/p' \
        "${source_script}")"
    if [[ -z "${function_source}" ]]; then
        fail "default trusted root: helper not found in ${source_script}"
        return 0
    fi

    if TRUSTED_PATH_ROOT="" bash -c \
        "${function_source}
path_is_trusted_root /usr/sbin/nginx"; then
        pass "default trusted root: ${source_script##*/} accepts /usr/sbin"
    else
        fail "default trusted root: ${source_script##*/} rejected /usr/sbin"
    fi

    if TRUSTED_PATH_ROOT="" bash -c \
        "${function_source}
path_is_trusted_root /home/evil/nginx"; then
        fail "default trusted root: ${source_script##*/} accepted /home/evil"
    else
        pass "default trusted root: ${source_script##*/} rejects /home/evil"
    fi
    return 0
}

echo "========================================" >&2
echo "Negative Control: Executable Trust" >&2
echo "========================================" >&2
echo "" >&2

assert_default_trusted_root \
    "${REPO_ROOT}/packaging/nfpm/scripts/preinstall.sh"
assert_default_trusted_root \
    "${REPO_ROOT}/packaging/nfpm/scripts/preremove.sh"
echo "" >&2

##############################################################################
# NC-1: nfpm/scripts/postinstall.sh (bash, TRUSTED_PATH_ROOT override)
##############################################################################

echo "--- NC-1: postinstall.sh ---" >&2

SOURCE_SCRIPT="${REPO_ROOT}/packaging/nfpm/scripts/postinstall.sh"
if [[ -f "${SOURCE_SCRIPT}" ]]; then
    create_evil_dir "postinstall"
    prepare_sandboxed_script "${SOURCE_SCRIPT}" "postinstall" "nfpm"
    run_sandboxed_script bash configure

    assert_no_markers "postinstall.sh"

    if [[ "${RC}" -eq 0 ]]; then
        pass "postinstall.sh: exit code 0 (configure)"
    else
        fail "postinstall.sh: exit code ${RC} (expected 0)"
    fi
else
    fail "postinstall.sh: script not found at ${SOURCE_SCRIPT}"
fi

echo "" >&2

##############################################################################
# NC-2: nfpm/scripts/preremove.sh (bash, TRUSTED_PATH_ROOT override)
##############################################################################

echo "--- NC-2: preremove.sh ---" >&2

SOURCE_SCRIPT="${REPO_ROOT}/packaging/nfpm/scripts/preremove.sh"
if [[ -f "${SOURCE_SCRIPT}" ]]; then
    create_evil_dir "preremove"
    prepare_sandboxed_script "${SOURCE_SCRIPT}" "preremove" "nfpm"
    touch "${SANDBOX_ROOT}/usr/share/nginx/modules-available/mod-markdown.conf"
    ln -s "${SANDBOX_ROOT}/usr/share/nginx/modules-available/mod-markdown.conf" \
        "${SANDBOX_ROOT}/etc/nginx/modules-enabled/50-mod-markdown.conf"
    run_sandboxed_script bash remove

    assert_no_markers "preremove.sh"
    assert_path_present "preremove.sh symlink" \
        "${SANDBOX_ROOT}/etc/nginx/modules-enabled/50-mod-markdown.conf"

    # The sandbox deliberately has no trusted nginx binary.  A clean scan of
    # the fixed paths cannot prove that an unobserved include graph is safe,
    # so the fail-closed removal guard must refuse the transaction while
    # leaving the operator-managed symlink untouched.
    if [[ "${RC}" -eq 1 ]]; then
        pass "preremove.sh: exit code 1 (unverifiable removal blocked)"
    else
        fail "preremove.sh: exit code ${RC} (expected 1)"
    fi

    echo "--- NC-2b: preremove.sh preserves an operator path ---" >&2
    create_evil_dir "preremove-regular-file"
    prepare_sandboxed_script "${SOURCE_SCRIPT}" "preremove-regular-file" "nfpm"
    touch "${SANDBOX_ROOT}/etc/nginx/modules-enabled/50-mod-markdown.conf"
    run_sandboxed_script bash upgrade

    assert_no_markers "preremove.sh regular file"
    assert_path_present "preremove.sh regular file" \
        "${SANDBOX_ROOT}/etc/nginx/modules-enabled/50-mod-markdown.conf"
    if [[ "${RC}" -eq 0 ]]; then
        pass "preremove.sh: upgrade preserves operator file"
    else
        fail "preremove.sh: upgrade exited ${RC}"
    fi
else
    fail "preremove.sh: script not found at ${SOURCE_SCRIPT}"
fi

echo "" >&2

##############################################################################
# NC-3: nfpm/scripts/preinstall.sh (bash, TRUSTED_PATH_ROOT override)
##############################################################################

echo "--- NC-3: preinstall.sh ---" >&2

SOURCE_SCRIPT="${REPO_ROOT}/packaging/nfpm/scripts/preinstall.sh"
if [[ -f "${SOURCE_SCRIPT}" ]]; then
    create_evil_dir "preinstall"
    prepare_sandboxed_script "${SOURCE_SCRIPT}" "preinstall" "nfpm"
    create_trusted_nginx
    run_sandboxed_script bash install

    assert_no_markers "preinstall.sh"

    if [[ -f "${TRUSTED_MARKER_DIR}/nginx" ]]; then
        pass "preinstall.sh: fixture nginx ran from trusted PATH"
    else
        fail "preinstall.sh: fixture nginx was not resolved from trusted PATH"
    fi

    # The fixture nginx reports the substituted package version and exits 0.
    if [[ "${RC}" -eq 0 ]]; then
        pass "preinstall.sh: exit code 0 (install)"
    else
        fail "preinstall.sh: exit code ${RC} (expected 0)"
    fi
else
    fail "preinstall.sh: script not found at ${SOURCE_SCRIPT}"
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
