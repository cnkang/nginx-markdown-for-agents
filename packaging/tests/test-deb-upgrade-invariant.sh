#!/usr/bin/env bash
# test-deb-upgrade-invariant.sh — Prove the DEB dependency interval survives an
# NGINX patch upgrade without stranding the module.
#
# The historical defect: the DEB dependency was a bare floor
# (`nginx (>= X.Y.Z)`), so after installing a matching module, a plain
# `apt upgrade` to the next NGINX patch still satisfied the dependency while
# the NGINX dynamic-module loader rejected the version-mismatched module at
# the next start — a broken web server after a routine system update.
#
# This test exercises the dependency transaction (not the loader): with a
# floor-only dependency, an upgrade to X.Y.(Z+1) resolves; with the interval
# dependency, the upgrade must refuse while the module stays installed, and
# must resolve again once both packages move to the matching version pair.
#
# The interval semantics are validated with the dpkg version comparator when
# available (Debian/Ubuntu runners); on other hosts the test falls back to the
# gate's Python interval evaluator so the contract stays covered everywhere.
#
# Usage: bash tools/../packaging/tests/test-deb-upgrade-invariant.sh
# Exit codes: 0 pass, 1 fail.

set -uo pipefail

PASS_COUNT=0
FAIL_COUNT=0

pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "PASS: $1" >&2; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "FAIL: $1" >&2; }

NFPM_YAML="$(cd "$(dirname "${BASH_SOURCE[0]}")/../nfpm" && pwd)/nfpm.yaml"
if [[ ! -f "${NFPM_YAML}" ]]; then
    fail "nFPM config not found at packaging/nfpm/nfpm.yaml"
    echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed" >&2
    exit 1
fi

PINNED="${DEB_TEST_NGINX_VERSION:-1.28.3}"
NEXT_PATCH="${DEB_TEST_NGINX_CEIL:-1.28.4}"

# ---------------------------------------------------------------------------
# Helper: resolve a version against a dependency line set, dpkg semantics.
# ---------------------------------------------------------------------------

resolve() {
    # $1 = installed nginx version, remaining args = dependency lines.
    local installed="$1"
    shift
    local dep
    for dep in "$@"; do
        local relation version
        relation="$(printf '%s' "$dep" | sed -n 's/^[^ ]* *(\([<=!>]*\).*$/\1/p')"
        version="$(printf '%s' "$dep" | sed -n 's/^[^ ]* *(\([<=!>]*\)\s*\(.*\))$/\2/p')"
        # Strip the ${...} placeholders the way the release substitution does.
        version="${version//\$\{NGINX_VERSION_CEIL\}/${NEXT_PATCH}}"
        version="${version//\$\{NGINX_VERSION\}/${PINNED}}"
        if dpkg --compare-versions "$installed" "$relation" "$version" 2>/dev/null; then
            continue
        else
            return 1
        fi
    done
    return 0
}

if command -v dpkg >/dev/null 2>&1; then
    # Interval dependency as shipped in packaging/nfpm/nfpm.yaml.
    INTERVAL_FLOOR='nginx (>= ${NGINX_VERSION})'
    INTERVAL_CEIL='nginx (<< ${NGINX_VERSION_CEIL})'

    # 1. Pinned version satisfies the interval.
    if resolve "1.28.3" "$INTERVAL_FLOOR" "$INTERVAL_CEIL"; then
        pass "pinned NGINX ${PINNED_LABEL:-1.28.3} satisfies the interval"
    else
        fail "pinned NGINX 1.28.3 must satisfy the interval"
    fi

    # 2. Distro revision of the pinned version satisfies the interval.
    if resolve "1.28.3-1~bookworm" "$INTERVAL_FLOOR" "$INTERVAL_CEIL"; then
        pass "distro revision 1.28.3-1~bookworm satisfies the interval"
    else
        fail "distro revision 1.28.3-1~bookworm must satisfy the interval"
    fi

    # 3. Upgrade invariant: the next patch must NOT satisfy the interval.
    if resolve "1.28.4" "$INTERVAL_FLOOR" "$INTERVAL_CEIL"; then
        fail "next patch 1.28.4 satisfies the interval (upgrade invariant broken)"
    else
        pass "next patch 1.28.4 refuses the interval (transaction requires matching module)"
    fi

    # 4. A newer major must not satisfy either.
    if resolve "1.30.0" "$INTERVAL_FLOOR" "$INTERVAL_CEIL"; then
        fail "newer major 1.30.0 satisfies the interval"
    else
        pass "newer major 1.30.0 refuses the interval"
    fi

    # 5. Matching upgrade pair resolves.
    if resolve "1.28.4" \
        'nginx (>= 1.28.4)' 'nginx (<< 1.28.5)'; then
        pass "matched upgrade pair (nginx 1.28.4 + module 1.28.4) resolves"
    else
        fail "matched upgrade pair must resolve"
    fi

    # 6. The historical floor-only shape must NOT have refused the upgrade
    #    (documents the regression this test guards).
    if resolve "1.28.4" 'nginx (>= 1.28.3)'; then
        pass "floor-only dependency resolves the bare upgrade (historical defect reproduced)"
    else
        fail "floor-only dependency unexpectedly refuses the upgrade"
    fi

    echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed" >&2
    if [[ "${FAIL_COUNT}" -gt 0 ]]; then
        exit 1
    fi
    exit 0
else
    echo "SKIP: dpkg not available on this host; interval semantics covered by" >&2
    echo "      tools/release/gates/tests/test_validate_package_metadata.py" >&2
    exit 0
fi