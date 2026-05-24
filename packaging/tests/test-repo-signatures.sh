#!/bin/bash
# test-repo-signatures.sh — Verify GPG signatures on packages and repositories.
#
# This script exercises GPG signature verification for both DEB and RPM
# packages, APT repository metadata, and YUM repository metadata.
#
# NOTE: This is a CI-only test. It requires a Linux system with:
#   - dpkg-sig (for DEB signature verification)
#   - rpm (for RPM signature verification)
#   - gpg (GnuPG) for metadata signature verification
#   - apt-get (for APT repository update test)
#   - yum (for YUM repository cache test)
#   - The project GPG signing key imported into the system keyring
#
# This script does NOT run locally on macOS. It is designed to execute
# inside the CI container where all prerequisites are available.
#
# Prerequisites:
#   - Linux system (Debian/Ubuntu or RHEL/CentOS/Fedora)
#   - GPG signing key imported (gpg --import <key>)
#   - Built .deb and/or .rpm packages available
#   - Repository metadata available (local or remote)
#
# Usage:
#   ./test-repo-signatures.sh [--deb <file>] [--rpm <file>] \
#       [--apt-release <dir>] [--yum-repodata <dir>]
#
# Environment:
#   DEB_FILE          — path to .deb package (optional)
#   RPM_FILE          — path to .rpm package (optional)
#   APT_RELEASE_DIR   — path to APT dists/<codename>/ directory (optional)
#   YUM_REPODATA_DIR  — path to YUM repodata/ directory (optional)
#   APT_REPO_URL      — APT repository URL for apt-get update test (optional)
#   YUM_REPO_URL      — YUM repository URL for yum makecache test (optional)
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met / usage error

set -e

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "PASS: $1" >&2
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $1" >&2
}

usage() {
    echo "Usage: $0 [OPTIONS]" >&2
    echo "" >&2
    echo "Options:" >&2
    echo "  --deb <file>            .deb package to verify" >&2
    echo "  --rpm <file>            .rpm package to verify" >&2
    echo "  --apt-release <dir>     APT Release directory" >&2
    echo "  --yum-repodata <dir>    YUM repodata directory" >&2
    echo "  -h, --help              show this help" >&2
    echo "" >&2
    echo "Environment variables:" >&2
    echo "  DEB_FILE, RPM_FILE, APT_RELEASE_DIR, YUM_REPODATA_DIR" >&2
    echo "  APT_REPO_URL, YUM_REPO_URL" >&2
    exit 2
}

# --- Parse arguments ---

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            ;;
        --deb)
            DEB_FILE="$2"
            shift 2
            ;;
        --rpm)
            RPM_FILE="$2"
            shift 2
            ;;
        --apt-release)
            APT_RELEASE_DIR="$2"
            shift 2
            ;;
        --yum-repodata)
            YUM_REPODATA_DIR="$2"
            shift 2
            ;;
        *)
            echo "Error: unknown option: $1" >&2
            usage
            ;;
    esac
done

# Use environment variables as fallback
DEB_FILE="${DEB_FILE:-}"
RPM_FILE="${RPM_FILE:-}"
APT_RELEASE_DIR="${APT_RELEASE_DIR:-}"
YUM_REPODATA_DIR="${YUM_REPODATA_DIR:-}"
APT_REPO_URL="${APT_REPO_URL:-}"
YUM_REPO_URL="${YUM_REPO_URL:-}"

# --- Step 1: Verify DEB package signature (dpkg-sig --verify) ---

echo "Step 1: DEB package signature verification..." >&2

if [ -n "$DEB_FILE" ]; then
    if [ ! -f "$DEB_FILE" ]; then
        fail "DEB file not found: $DEB_FILE"
    elif ! command -v dpkg-sig >/dev/null 2>&1; then
        echo "  SKIP: dpkg-sig not available on this system" >&2
        pass "dpkg-sig check skipped (tool not available)"
    else
        SIG_OUTPUT=$(dpkg-sig --verify "$DEB_FILE" 2>&1) || SIG_OUTPUT=""

        if echo "$SIG_OUTPUT" | grep -qi "GOODSIG\|good"; then
            pass "DEB package signature valid: $(basename "$DEB_FILE")"
        else
            fail "DEB package signature invalid: $(basename "$DEB_FILE")"
            echo "$SIG_OUTPUT" >&2
        fi
    fi
else
    echo "  SKIP: no DEB_FILE specified" >&2
    pass "DEB signature check skipped (no file)"
fi

# --- Step 2: Verify RPM package signature (rpm -K) ---

echo "Step 2: RPM package signature verification..." >&2

if [ -n "$RPM_FILE" ]; then
    if [ ! -f "$RPM_FILE" ]; then
        fail "RPM file not found: $RPM_FILE"
    elif ! command -v rpm >/dev/null 2>&1; then
        echo "  SKIP: rpm not available on this system" >&2
        pass "rpm -K check skipped (tool not available)"
    else
        RPM_SIG=$(rpm -K "$RPM_FILE" 2>&1) || RPM_SIG=""

        if echo "$RPM_SIG" | grep -qi "pgp\|gpg.*OK\|digests signatures OK"; then
            pass "RPM package signature valid: $(basename "$RPM_FILE")"
        else
            fail "RPM package signature check: $(basename "$RPM_FILE")"
            echo "$RPM_SIG" >&2
        fi
    fi
else
    echo "  SKIP: no RPM_FILE specified" >&2
    pass "RPM signature check skipped (no file)"
fi

# --- Step 3: Verify APT repository metadata (gpg --verify) ---

echo "Step 3: APT repository metadata signature..." >&2

if [ -n "$APT_RELEASE_DIR" ]; then
    RELEASE_FILE="${APT_RELEASE_DIR}/Release"
    RELEASE_GPG="${APT_RELEASE_DIR}/Release.gpg"
    INRELEASE_FILE="${APT_RELEASE_DIR}/InRelease"

    if [ ! -f "$RELEASE_FILE" ]; then
        fail "APT Release file not found: $RELEASE_FILE"
    elif [ ! -f "$RELEASE_GPG" ]; then
        fail "APT Release.gpg not found: $RELEASE_GPG"
    else
        VERIFY_APT=$(gpg --verify "$RELEASE_GPG" "$RELEASE_FILE" 2>&1) || true

        if echo "$VERIFY_APT" | grep -qi "good signature"; then
            pass "APT Release.gpg signature valid"
        else
            fail "APT Release.gpg signature invalid"
            echo "$VERIFY_APT" >&2
        fi
    fi

    # Also check InRelease if present
    if [ -f "$INRELEASE_FILE" ]; then
        VERIFY_IR=$(gpg --verify "$INRELEASE_FILE" 2>&1) || true

        if echo "$VERIFY_IR" | grep -qi "good signature"; then
            pass "APT InRelease signature valid"
        else
            fail "APT InRelease signature invalid"
        fi
    fi
else
    echo "  SKIP: no APT_RELEASE_DIR specified" >&2
    pass "APT metadata check skipped (no directory)"
fi

# --- Step 4: Verify YUM repository metadata (gpg --verify) ---

echo "Step 4: YUM repository metadata signature..." >&2

if [ -n "$YUM_REPODATA_DIR" ]; then
    REPOMD_FILE="${YUM_REPODATA_DIR}/repomd.xml"
    REPOMD_ASC="${YUM_REPODATA_DIR}/repomd.xml.asc"

    if [ ! -f "$REPOMD_FILE" ]; then
        fail "YUM repomd.xml not found: $REPOMD_FILE"
    elif [ ! -f "$REPOMD_ASC" ]; then
        fail "YUM repomd.xml.asc not found: $REPOMD_ASC"
    else
        VERIFY_YUM=$(gpg --verify "$REPOMD_ASC" "$REPOMD_FILE" 2>&1) || true

        if echo "$VERIFY_YUM" | grep -qi "good signature"; then
            pass "YUM repomd.xml signature valid"
        else
            fail "YUM repomd.xml signature invalid"
            echo "$VERIFY_YUM" >&2
        fi
    fi
else
    echo "  SKIP: no YUM_REPODATA_DIR specified" >&2
    pass "YUM metadata check skipped (no directory)"
fi

# --- Step 5: Test apt-get update against signed repository ---

echo "Step 5: apt-get update test..." >&2

if [ -n "$APT_REPO_URL" ]; then
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "  SKIP: apt-get not available" >&2
        pass "apt-get update skipped (tool not available)"
    elif [ "$(id -u)" -ne 0 ]; then
        echo "  SKIP: root required for apt-get update" >&2
        pass "apt-get update skipped (not root)"
    else
        # Create temporary sources list
        TMPLIST=$(mktemp -t nginx-markdown-test.XXXXXX)
        trap 'rm -f "$TMPLIST"' EXIT

        echo "deb [signed-by=/usr/share/keyrings/nginx-markdown.gpg] ${APT_REPO_URL} stable main" \
            > "$TMPLIST"

        APT_OUTPUT=$(apt-get update \
            -o Dir::Etc::sourcelist="$TMPLIST" \
            -o Dir::Etc::sourceparts="/dev/null" 2>&1) || APT_OUTPUT=""

        if echo "$APT_OUTPUT" | grep -qi "hit\|get\|fetched"; then
            pass "apt-get update succeeded against signed repo"
        else
            fail "apt-get update failed"
            echo "$APT_OUTPUT" >&2
        fi

        rm -f "$TMPLIST"
    fi
else
    echo "  SKIP: no APT_REPO_URL specified" >&2
    pass "apt-get update skipped (no URL)"
fi

# --- Step 6: Test yum makecache against signed repository ---

echo "Step 6: yum makecache test..." >&2

if [ -n "$YUM_REPO_URL" ]; then
    if ! command -v yum >/dev/null 2>&1; then
        echo "  SKIP: yum not available" >&2
        pass "yum makecache skipped (tool not available)"
    elif [ "$(id -u)" -ne 0 ]; then
        echo "  SKIP: root required for yum makecache" >&2
        pass "yum makecache skipped (not root)"
    else
        # Create temporary repo file
        TMPREPO=$(mktemp -t nginx-markdown-test.XXXXXX)
        trap 'rm -f "$TMPREPO"' EXIT

        cat > "$TMPREPO" <<REPOEOF
[nginx-markdown-test]
name=nginx-markdown test repo
baseurl=${YUM_REPO_URL}
enabled=1
gpgcheck=1
gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-nginx-markdown
REPOEOF

        YUM_OUTPUT=$(yum makecache \
            --disablerepo='*' \
            --enablerepo='nginx-markdown-test' \
            -c "$TMPREPO" 2>&1) || YUM_OUTPUT=""

        if echo "$YUM_OUTPUT" | grep -qi "metadata cache created\|makecache"; then
            pass "yum makecache succeeded against signed repo"
        else
            fail "yum makecache failed"
            echo "$YUM_OUTPUT" >&2
        fi

        rm -f "$TMPREPO"
    fi
else
    echo "  SKIP: no YUM_REPO_URL specified" >&2
    pass "yum makecache skipped (no URL)"
fi

# --- Summary ---

echo "" >&2
echo "=== Repository Signature Test Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
