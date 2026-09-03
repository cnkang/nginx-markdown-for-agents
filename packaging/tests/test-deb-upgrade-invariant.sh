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
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ ! -f "${NFPM_YAML}" ]]; then
    fail "nFPM config not found at packaging/nfpm/nfpm.yaml"
    echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed" >&2
    exit 1
fi

PINNED="${DEB_TEST_NGINX_VERSION:-1.28.3}"
NEXT_PATCH="${DEB_TEST_NGINX_CEIL:-1.28.4}"

# Read the actual nFPM dependency entries instead of duplicating their
# operators/placeholders in this regression test. The release validator owns
# the small YAML block parser, so this test and the release gate cannot drift
# on which dependency is being exercised.
load_interval_dependencies() {
    local dependencies

    if ! dependencies="$(PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
        python3 - "${NFPM_YAML}" <<'PY'
from pathlib import Path
import sys

from tools.release.gates.validate_package_metadata import (
    _parse_nfpm_deb_depends,
    _parse_nginx_dep_constraints,
)

content = Path(sys.argv[1]).read_text(encoding="utf-8")
constraints = _parse_nginx_dep_constraints(_parse_nfpm_deb_depends(content))
floor = constraints.get(">=")
ceil = constraints.get("<<")
if floor is None or ceil is None:
    raise SystemExit("nFPM DEB dependency interval is incomplete")
print(f"nginx (>= {floor})")
print(f"nginx (<< {ceil})")
PY
    )"; then
        fail "could not parse the DEB dependency interval from nFPM config"
        return 1
    fi

    INTERVAL_FLOOR="$(printf '%s\n' "${dependencies}" | sed -n '1p')"
    INTERVAL_CEIL="$(printf '%s\n' "${dependencies}" | sed -n '2p')"
    if [[ -z "${INTERVAL_FLOOR}" || -z "${INTERVAL_CEIL}" ]]; then
        fail "nFPM DEB dependency interval parser returned empty values"
        return 1
    fi
    return 0
}

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
        version="$(printf '%s' "$dep" | sed -n 's/^[^ ]* *(\([<=!>]*\)[[:space:]]*\(.*\))$/\2/p')"
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

if ! load_interval_dependencies; then
    echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed" >&2
    exit 1
fi

if command -v dpkg >/dev/null 2>&1 \
    && [[ "${DEB_TEST_FORCE_PYTHON:-0}" != "1" ]]; then
    # The interval is read from packaging/nfpm/nfpm.yaml above.
    next_minor="$(printf '%s\n' "${PINNED}" \
        | awk -F. '{print $1 "." ($2 + 1) ".0"}')"
    next_next_patch="$(printf '%s\n' "${NEXT_PATCH}" \
        | awk -F. '{print $1 "." $2 "." ($3 + 1)}')"

    # 1. Pinned version satisfies the interval.
    if resolve "${PINNED}" "$INTERVAL_FLOOR" "$INTERVAL_CEIL"; then
        pass "pinned NGINX ${PINNED} satisfies the interval"
    else
        fail "pinned NGINX ${PINNED} must satisfy the interval"
    fi

    # 2. Distro revision of the pinned version satisfies the interval.
    if resolve "${PINNED}-1~bookworm" "$INTERVAL_FLOOR" "$INTERVAL_CEIL"; then
        pass "distro revision ${PINNED}-1~bookworm satisfies the interval"
    else
        fail "distro revision ${PINNED}-1~bookworm must satisfy the interval"
    fi

    # 3. Upgrade invariant: the next patch must NOT satisfy the interval.
    if resolve "${NEXT_PATCH}" "$INTERVAL_FLOOR" "$INTERVAL_CEIL"; then
        fail "next patch ${NEXT_PATCH} satisfies the interval (upgrade invariant broken)"
    else
        pass "next patch ${NEXT_PATCH} refuses the interval (transaction requires matching module)"
    fi

    # 4. A newer minor must not satisfy either.
    if resolve "${next_minor}" "$INTERVAL_FLOOR" "$INTERVAL_CEIL"; then
        fail "newer minor ${next_minor} satisfies the interval"
    else
        pass "newer minor ${next_minor} refuses the interval"
    fi

    # 5. Matching upgrade pair resolves.
    if resolve "${NEXT_PATCH}" \
        "nginx (>= ${NEXT_PATCH})" "nginx (<< ${next_next_patch})"; then
        pass "matched upgrade pair (nginx ${NEXT_PATCH} + module ${NEXT_PATCH}) resolves"
    else
        fail "matched upgrade pair must resolve"
    fi

    # 6. The historical floor-only shape must NOT have refused the upgrade
    #    (documents the regression this test guards).
    if resolve "${NEXT_PATCH}" "nginx (>= ${PINNED})"; then
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
    # Use the same comparator and the actual parsed nFPM entries on hosts
    # without dpkg. This is a real fallback, not a successful skip.
    if ! PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
        python3 - "${NFPM_YAML}" "${PINNED}" "${NEXT_PATCH}" <<'PY'
from pathlib import Path
import sys

from tools.release.gates.validate_package_metadata import (
    _dpkg_version_satisfies,
    _dpkg_version_satisfies_interval,
    _parse_nfpm_deb_depends,
    _parse_nginx_dep_constraints,
)

path = Path(sys.argv[1])
pinned = sys.argv[2]
next_patch = sys.argv[3]
passed = 0
failed = 0
content = path.read_text(encoding="utf-8")
constraints = _parse_nginx_dep_constraints(_parse_nfpm_deb_depends(content))
floor = constraints.get(">=")
ceil = constraints.get("<<")
if floor is None or ceil is None:
    raise SystemExit("nFPM DEB dependency interval is incomplete")
floor = floor.replace("${NGINX_VERSION}", pinned)
ceil = ceil.replace("${NGINX_VERSION_CEIL}", next_patch)

major, minor, _patch = (int(part) for part in pinned.split("."))
next_minor = f"{major}.{minor + 1}.0"
next_patch_prefix, next_patch_number = next_patch.rsplit(".", 1)
next_next_patch = f"{next_patch_prefix}.{int(next_patch_number) + 1}"
checks = [
    (pinned, True, f"pinned NGINX {pinned} satisfies the interval"),
    (
        f"{pinned}-1~bookworm",
        True,
        f"distro revision {pinned}-1~bookworm satisfies the interval",
    ),
    (
        next_patch,
        False,
        f"next patch {next_patch} refuses the interval (transaction requires matching module)",
    ),
    (next_minor, False, f"newer minor {next_minor} refuses the interval"),
    (
        next_patch,
        True,
        f"matched upgrade pair (nginx {next_patch} + module {next_patch}) resolves",
    ),
    (
        next_patch,
        True,
        "floor-only dependency resolves the bare upgrade (historical defect reproduced)",
    ),
]

for index, (candidate, expected, label) in enumerate(checks):
    if index == 4:
        actual = _dpkg_version_satisfies_interval(
            candidate, next_patch, next_next_patch
        )
    elif index == 5:
        actual = _dpkg_version_satisfies(candidate, ">=", pinned)
    else:
        actual = _dpkg_version_satisfies_interval(candidate, floor, ceil)
    if actual != expected:
        failed += 1
        print(f"FAIL: {label} (expected {expected}, got {actual})", file=sys.stderr)
    else:
        passed += 1
        print(f"PASS: {label}")

print(f"Results: {passed} passed, {failed} failed", file=sys.stderr)
sys.exit(0 if failed == 0 else 1)
PY
    then
        exit 1
    fi
    exit 0
fi
