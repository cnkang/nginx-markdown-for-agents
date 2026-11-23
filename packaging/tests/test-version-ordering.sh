#!/usr/bin/env bash
# test-version-ordering.sh — Prove the RC package version mapping with the
# tools each package family actually uses.
#
# The mapping in packaging/scripts/release_version.py is only useful if real
# package tooling agrees with it.  This test therefore checks:
#
#   1. the mapping table for the 0.9.4 -> rc.1 -> rc.2 -> 1.0.0 sequence,
#   2. Debian ordering with `dpkg --compare-versions`,
#   3. Debian install-level upgrade order with `dpkg -i` inside a container,
#   4. RPM ordering with `rpm.vercmp` inside a container.
#
# A missing tool is reported as NOT_RUN, never as a pass.  With
# --require-all, NOT_RUN becomes a failure so a CI job that has Docker cannot
# silently skip the ordering proof.
#
# Usage:
#   bash packaging/tests/test-version-ordering.sh
#   bash packaging/tests/test-version-ordering.sh --require-all
#
# Exit codes: 0 all runnable checks passed, 1 a check failed (or a required
# check could not run), 2 usage error.

set -uo pipefail

PASS_COUNT=0
FAIL_COUNT=0
NOT_RUN_COUNT=0
REQUIRE_ALL=0

usage() {
    printf 'Usage: %s [--require-all]\n' "${0##*/}" >&2
    printf '  --require-all   Treat a check that could not run as a failure\n' >&2
    return 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --require-all)
            REQUIRE_ALL=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'ERROR: unknown argument: %s\n' "$1" >&2
            usage
            exit 2
            ;;
    esac
    shift
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MAPPER="${REPO_ROOT}/packaging/scripts/release_version.py"
NGINX_VERSION="1.28.3"

pass() { PASS_COUNT=$((PASS_COUNT + 1)); printf 'PASS: %s\n' "$1" >&2; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); printf 'FAIL: %s\n' "$1" >&2; }
not_run() { NOT_RUN_COUNT=$((NOT_RUN_COUNT + 1)); printf 'NOT_RUN: %s\n' "$1" >&2; }

matrix_field() {
    # $1 = version, $2 = field
    python3 "${MAPPER}" --version "$1" --nginx-version "${NGINX_VERSION}" --field "$2"
}

check_expected() {
    # $1 = description, $2 = expected, $3 = actual
    if [[ "$2" == "$3" ]]; then
        pass "$1"
    else
        fail "$1 (expected '$2', got '$3')"
    fi
}

##############################################################################
# 1. Mapping table
##############################################################################

check_expected "0.9.4 DEB version" "0.9.4" "$(matrix_field 0.9.4 deb_version)"
check_expected "0.9.4 RPM release" "nginx${NGINX_VERSION}.1" \
    "$(matrix_field 0.9.4 rpm_release)"
check_expected "1.0.0-rc.1 DEB version" "1.0.0~rc.1" \
    "$(matrix_field 1.0.0-rc.1 deb_version)"
check_expected "1.0.0-rc.1 RPM version" "1.0.0~rc.1" \
    "$(matrix_field 1.0.0-rc.1 rpm_version)"
check_expected "1.0.0-rc.1 RPM release" "nginx${NGINX_VERSION}.1" \
    "$(matrix_field 1.0.0-rc.1 rpm_release)"
check_expected "1.0.0-rc.2 DEB version" "1.0.0~rc.2" \
    "$(matrix_field 1.0.0-rc.2 deb_version)"
check_expected "1.0.0 DEB version" "1.0.0" "$(matrix_field 1.0.0 deb_version)"
check_expected "1.0.0 RPM release" "nginx${NGINX_VERSION}.1" \
    "$(matrix_field 1.0.0 rpm_release)"

# Distinct SemVer prereleases must not collapse onto one package version.
RC_DOT="$(matrix_field 1.0.0-rc.1 deb_version)"
RC_PLAIN="$(matrix_field 1.0.0-rc1 deb_version)"
RC_UPPER="$(matrix_field 1.0.0-RC.1 deb_version)"
if [[ "${RC_DOT}" != "${RC_PLAIN}" && "${RC_DOT}" != "${RC_UPPER}" && "${RC_PLAIN}" != "${RC_UPPER}" ]]; then
    pass "distinct prerelease identifiers keep distinct package versions"
else
    fail "prerelease identifiers collided: ${RC_DOT} ${RC_PLAIN} ${RC_UPPER}"
fi

for rejected in "1.0" "01.0.0" "1.0.0-" "1.0.0-rc..1" "1.0.0+build" "1.0.0-rc.01"; do
    if python3 "${MAPPER}" --version "${rejected}" >/dev/null 2>&1; then
        fail "mapper accepted an invalid version: ${rejected}"
    else
        pass "mapper rejected an invalid version: ${rejected}"
    fi
done

DEB_094="0.9.4-nginx${NGINX_VERSION}"
DEB_RC1="$(matrix_field 1.0.0-rc.1 deb_version)-nginx${NGINX_VERSION}"
DEB_RC2="$(matrix_field 1.0.0-rc.2 deb_version)-nginx${NGINX_VERSION}"
DEB_GA="1.0.0-nginx${NGINX_VERSION}"

##############################################################################
# 2. Debian ordering with dpkg
##############################################################################

if command -v dpkg >/dev/null 2>&1; then
    check_dpkg_lt() {
        # $1 = description, $2 = lower, $3 = higher
        if dpkg --compare-versions "$2" lt "$3"; then
            pass "$1"
        else
            fail "$1 ($2 is not lower than $3)"
        fi
    }
    check_dpkg_lt "dpkg: 0.9.4 < 1.0.0~rc.1" "${DEB_094}" "${DEB_RC1}"
    check_dpkg_lt "dpkg: 1.0.0~rc.1 < 1.0.0~rc.2" "${DEB_RC1}" "${DEB_RC2}"
    check_dpkg_lt "dpkg: 1.0.0~rc.2 < 1.0.0" "${DEB_RC2}" "${DEB_GA}"
    check_dpkg_lt "dpkg: 1.0.0~alpha.2 < 1.0.0~alpha.10" \
        "1.0.0~alpha.2-nginx${NGINX_VERSION}" "1.0.0~alpha.10-nginx${NGINX_VERSION}"
    # Known limit: a mixed identifier such as `alpha10` compares digit runs
    # numerically in dpkg, while SemVer compares it lexically (`alpha10` <
    # `alpha2`).  The check pins the package-manager behavior so the limit is
    # explicit; spell numeric sequences with a dot to get SemVer precedence.
    if dpkg --compare-versions "1.0.0~alpha10-nginx${NGINX_VERSION}" gt \
        "1.0.0~alpha2-nginx${NGINX_VERSION}"; then
        pass "dpkg: mixed identifier alpha10 sorts after alpha2 (documented SemVer limit)"
    else
        fail "dpkg: expected the documented mixed-identifier ordering"
    fi
    # The trap this mapping exists for: the plain hyphen form sorts ABOVE the
    # final release, so a naive mapping would make RC the newest version.
    if dpkg --compare-versions "1.0.0-rc1-nginx${NGINX_VERSION}" gt "${DEB_GA}"; then
        pass "dpkg: the naive '-rc1' form sorts above 1.0.0 (documents why '~' is used)"
    else
        fail "dpkg: expected the naive '-rc1' form to sort above 1.0.0"
    fi
else
    not_run "dpkg is not installed; Debian ordering not verified"
fi

##############################################################################
# 3. Debian install-level upgrade order (container)
##############################################################################

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    deb_smoke="$(
        docker run --rm -e "DEB_RC1=${DEB_RC1}" -e "DEB_RC2=${DEB_RC2}" \
            -e "DEB_GA=${DEB_GA}" ubuntu:24.04 bash -c '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
build_deb() {
    local version="$1" dir="$2"
    rm -rf "$dir"
    mkdir -p "$dir/DEBIAN"
    {
        printf "Package: nginx-markdown-version-probe\n"
        printf "Version: %s\n" "$version"
        printf "Architecture: all\n"
        printf "Maintainer: release test <release@example.invalid>\n"
        printf "Description: version ordering probe\n"
    } > "$dir/DEBIAN/control"
    dpkg-deb --build --root-owner-group "$dir" >/dev/null
}
build_deb "$DEB_RC1" /tmp/rc1
build_deb "$DEB_RC2" /tmp/rc2
build_deb "$DEB_GA" /tmp/ga
dpkg -i /tmp/rc1.deb >/dev/null 2>&1
dpkg -i /tmp/rc2.deb >/dev/null 2>&1
dpkg -i /tmp/ga.deb >/dev/null 2>&1
installed="$(dpkg-query -W -f="\${Version}" nginx-markdown-version-probe)"
if [[ "$installed" != "$DEB_GA" ]]; then
    echo "INSTALLED_VERSION_MISMATCH:$installed"
    exit 1
fi
# dpkg installs a downgrade when asked directly, so the ordering claim is
# checked with the comparator the package manager uses for upgrade decisions.
if dpkg --compare-versions "$installed" gt "$DEB_RC1"; then
    echo "INSTALL_UPGRADE_OK"
else
    echo "ORDER_COMPARATOR_DISAGREES"
    exit 1
fi
' 2>&1)"
    case "${deb_smoke}" in
        *INSTALL_UPGRADE_OK*)
            pass "dpkg install order: rc1 -> rc2 -> 1.0.0 upgrades and the comparator agrees"
            ;;
        *)
            fail "dpkg install order failed: ${deb_smoke}"
            ;;
    esac
else
    not_run "docker is not available; Debian install-level upgrade not verified"
fi

##############################################################################
# 4. RPM ordering with rpm.vercmp (container)
##############################################################################

RPM_094="0.9.4-nginx${NGINX_VERSION}.1"
RPM_RC1="$(matrix_field 1.0.0-rc.1 rpm_version)-$(matrix_field 1.0.0-rc.1 rpm_release)"
RPM_RC2="$(matrix_field 1.0.0-rc.2 rpm_version)-$(matrix_field 1.0.0-rc.2 rpm_release)"
RPM_GA="1.0.0-nginx${NGINX_VERSION}.1"

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    rpm_smoke="$(
        docker run --rm -e "RPM_094=${RPM_094}" -e "RPM_RC1=${RPM_RC1}" \
            -e "RPM_RC2=${RPM_RC2}" -e "RPM_GA=${RPM_GA}" \
            -e "NGINX_VERSION=${NGINX_VERSION}" rockylinux:9 bash -c '
set -euo pipefail
cmp() {
    rpm --eval "%{lua:print(rpm.vercmp(\"$1\", \"$2\"))}"
}
rc1_vs_094="$(cmp "$RPM_RC1" "$RPM_094")"
rc1_vs_rc2="$(cmp "$RPM_RC1" "$RPM_RC2")"
rc2_vs_ga="$(cmp "$RPM_RC2" "$RPM_GA")"
alpha_numeric="$(cmp "1.0.0~alpha.2-nginx${NGINX_VERSION}.1" "1.0.0~alpha.10-nginx${NGINX_VERSION}.1")"
alpha_mixed="$(cmp "1.0.0~alpha10-nginx${NGINX_VERSION}.1" "1.0.0~alpha2-nginx${NGINX_VERSION}.1")"
# A Release-first prerelease marker such as 0.rc1 would sort above the
# nginx<ver>.1 release, because rpmvercmp ranks a numeric segment above a
# letter segment.  Assert the naive shape really is wrong so the mapping
# cannot silently regress to it.
naive="$(cmp "$(printf "%s-0.rc1.nginx%s" "1.0.0" "$NGINX_VERSION")" "$RPM_GA")"
echo "RC1_VS_094=$rc1_vs_094 RC1_VS_RC2=$rc1_vs_rc2 RC2_VS_GA=$rc2_vs_ga NAIVE=$naive ALPHA_NUMERIC=$alpha_numeric ALPHA_MIXED=$alpha_mixed"
' 2>&1)"
    if [[ "${rpm_smoke}" == *"RC1_VS_094=1 RC1_VS_RC2=-1 RC2_VS_GA=-1 NAIVE=1 ALPHA_NUMERIC=-1 ALPHA_MIXED=1"* ]]; then
        pass "rpmvercmp: 0.9.4 < rc.1 < rc.2 < 1.0.0, alpha.2 < alpha.10, mixed identifier pinned"
    else
        fail "rpmvercmp ordering failed: ${rpm_smoke}"
    fi
else
    not_run "docker is not available; RPM ordering not verified"
fi

##############################################################################
# Summary
##############################################################################

printf '\nResults: %d passed, %d failed, %d not run\n' \
    "${PASS_COUNT}" "${FAIL_COUNT}" "${NOT_RUN_COUNT}" >&2

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
if [[ "${REQUIRE_ALL}" -eq 1 && "${NOT_RUN_COUNT}" -gt 0 ]]; then
    printf 'FAIL: --require-all was requested but %d check(s) did not run\n' \
        "${NOT_RUN_COUNT}" >&2
    exit 1
fi
exit 0
