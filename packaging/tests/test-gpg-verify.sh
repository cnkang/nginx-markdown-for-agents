#!/bin/bash
# test-gpg-verify.sh — Verify GPG signature for APT and YUM repositories.
#
# This script documents and exercises GPG signature verification for
# both APT (Debian/Ubuntu) and YUM (RHEL/CentOS/Fedora) repository
# configurations.
#
# Prerequisites:
#   - gpg (GnuPG) installed
#   - For APT: apt-key or gpg keyring available
#   - For YUM: rpm --import available
#   - Network access to repository URL (or local repo mirror)
#   - GPG_KEY_URL environment variable (default: project signing key URL)
#
# Usage:
#   ./test-gpg-verify.sh [apt|yum|both]
#
# Test Scenario:
#   1. Download and verify the GPG public key
#   2. For APT: verify Release.gpg signature against Release file
#   3. For YUM: verify repomd.xml.asc signature
#   4. Verify key fingerprint matches expected value
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — prerequisites not met / usage error

set -e

GPG_KEY_URL="${GPG_KEY_URL:-https://packages.nginx-markdown.dev/gpg-key.asc}"
REPO_BASE_URL="${REPO_BASE_URL:-https://packages.nginx-markdown.dev}"
EXPECTED_FINGERPRINT="${EXPECTED_FINGERPRINT:-}"
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
    echo "Usage: $0 [apt|yum|both]" >&2
    echo "" >&2
    echo "Options:" >&2
    echo "  apt   — verify APT repository signatures only" >&2
    echo "  yum   — verify YUM repository signatures only" >&2
    echo "  both  — verify both (default)" >&2
    echo "" >&2
    echo "Environment:" >&2
    echo "  GPG_KEY_URL          — URL to GPG public key" >&2
    echo "  REPO_BASE_URL        — base URL of package repository" >&2
    echo "  EXPECTED_FINGERPRINT — expected GPG key fingerprint" >&2
    exit 2
}

check_prerequisites() {
    if ! command -v gpg >/dev/null 2>&1; then
        echo "Error: gpg (GnuPG) is required" >&2
        exit 2
    fi

    if ! command -v curl >/dev/null 2>&1; then
        echo "Error: curl is required" >&2
        exit 2
    fi
}

# Parse mode argument
case "${1:-both}" in
    -h|--help)
        usage
        ;;
    apt)
        MODE="apt"
        ;;
    yum)
        MODE="yum"
        ;;
    both)
        MODE="both"
        ;;
    *)
        echo "Error: unknown mode: $1" >&2
        usage
        ;;
esac

check_prerequisites

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# --- Step 1: Download and verify GPG key ---

echo "Step 1: Downloading GPG key..." >&2

KEY_FILE="${TMPDIR}/signing-key.asc"

if curl -sf -o "$KEY_FILE" "$GPG_KEY_URL" 2>/dev/null; then
    pass "GPG key downloaded from $GPG_KEY_URL"
else
    fail "failed to download GPG key from $GPG_KEY_URL"
    echo "Set GPG_KEY_URL to a valid key URL or provide a local key." >&2
    # Create a placeholder for offline testing
    echo "--- PLACEHOLDER: key download failed (offline?) ---" >&2
fi

# Import key into temporary keyring
KEYRING="${TMPDIR}/keyring.gpg"

if [ -f "$KEY_FILE" ] && [ -s "$KEY_FILE" ]; then
    GPG_IMPORT=$(gpg --no-default-keyring --keyring "$KEYRING" \
        --import "$KEY_FILE" 2>&1) || true

    if echo "$GPG_IMPORT" | grep -qi "imported\|not changed"; then
        pass "GPG key imported into test keyring"
    else
        fail "GPG key import failed"
        echo "$GPG_IMPORT" >&2
    fi

    # Verify fingerprint if expected value provided
    if [ -n "$EXPECTED_FINGERPRINT" ]; then
        KEY_FP=$(gpg --no-default-keyring --keyring "$KEYRING" \
            --fingerprint 2>/dev/null | grep -o '[A-F0-9 ]\{40,\}' | tr -d ' ' | head -1)
        if [ "$KEY_FP" = "$EXPECTED_FINGERPRINT" ]; then
            pass "key fingerprint matches expected value"
        else
            fail "key fingerprint mismatch (got: $KEY_FP)"
        fi
    else
        pass "fingerprint check skipped (EXPECTED_FINGERPRINT not set)"
    fi
else
    fail "GPG key file empty or missing"
fi

# --- Step 2: APT repository verification ---

if [ "$MODE" = "apt" ] || [ "$MODE" = "both" ]; then
    echo "" >&2
    echo "Step 2: APT repository signature verification..." >&2

    APT_RELEASE_URL="${REPO_BASE_URL}/dists/stable/Release"
    APT_RELEASE_GPG_URL="${REPO_BASE_URL}/dists/stable/Release.gpg"
    APT_INRELEASE_URL="${REPO_BASE_URL}/dists/stable/InRelease"

    RELEASE_FILE="${TMPDIR}/Release"
    RELEASE_GPG="${TMPDIR}/Release.gpg"

    # Try to download Release and Release.gpg
    if curl -sf -o "$RELEASE_FILE" "$APT_RELEASE_URL" 2>/dev/null; then
        pass "APT Release file downloaded"

        if curl -sf -o "$RELEASE_GPG" "$APT_RELEASE_GPG_URL" 2>/dev/null; then
            pass "APT Release.gpg downloaded"

            # Verify signature
            VERIFY_OUTPUT=$(gpg --no-default-keyring --keyring "$KEYRING" \
                --verify "$RELEASE_GPG" "$RELEASE_FILE" 2>&1) || true

            if echo "$VERIFY_OUTPUT" | grep -qi "good signature"; then
                pass "APT Release.gpg signature VALID"
            else
                fail "APT Release.gpg signature INVALID"
                echo "$VERIFY_OUTPUT" >&2
            fi
        else
            fail "APT Release.gpg not available (repo may not be published yet)"
        fi
    else
        fail "APT Release file not available at $APT_RELEASE_URL"
        echo "Repository may not be published yet. This is expected pre-release." >&2
    fi

    # Check InRelease (combined signed file)
    INRELEASE_FILE="${TMPDIR}/InRelease"
    if curl -sf -o "$INRELEASE_FILE" "$APT_INRELEASE_URL" 2>/dev/null; then
        VERIFY_IR=$(gpg --no-default-keyring --keyring "$KEYRING" \
            --verify "$INRELEASE_FILE" 2>&1) || true

        if echo "$VERIFY_IR" | grep -qi "good signature"; then
            pass "APT InRelease signature VALID"
        else
            fail "APT InRelease signature INVALID"
        fi
    else
        pass "APT InRelease not available (Release.gpg used instead)"
    fi
fi

# --- Step 3: YUM repository verification ---

if [ "$MODE" = "yum" ] || [ "$MODE" = "both" ]; then
    echo "" >&2
    echo "Step 3: YUM repository signature verification..." >&2

    YUM_REPOMD_URL="${REPO_BASE_URL}/rpm/repodata/repomd.xml"
    YUM_REPOMD_ASC_URL="${REPO_BASE_URL}/rpm/repodata/repomd.xml.asc"

    REPOMD_FILE="${TMPDIR}/repomd.xml"
    REPOMD_ASC="${TMPDIR}/repomd.xml.asc"

    if curl -sf -o "$REPOMD_FILE" "$YUM_REPOMD_URL" 2>/dev/null; then
        pass "YUM repomd.xml downloaded"

        if curl -sf -o "$REPOMD_ASC" "$YUM_REPOMD_ASC_URL" 2>/dev/null; then
            pass "YUM repomd.xml.asc downloaded"

            VERIFY_YUM=$(gpg --no-default-keyring --keyring "$KEYRING" \
                --verify "$REPOMD_ASC" "$REPOMD_FILE" 2>&1) || true

            if echo "$VERIFY_YUM" | grep -qi "good signature"; then
                pass "YUM repomd.xml signature VALID"
            else
                fail "YUM repomd.xml signature INVALID"
                echo "$VERIFY_YUM" >&2
            fi
        else
            fail "YUM repomd.xml.asc not available (repo may not be published yet)"
        fi
    else
        fail "YUM repomd.xml not available at $YUM_REPOMD_URL"
        echo "Repository may not be published yet. This is expected pre-release." >&2
    fi

    # Verify RPM package signature if an .rpm file is available
    if command -v rpm >/dev/null 2>&1; then
        RPM_FILES=$(find "${TMPDIR}" -name "*.rpm" 2>/dev/null)
        if [ -n "$RPM_FILES" ]; then
            for rpm_file in $RPM_FILES; do
                RPM_SIG=$(rpm -K "$rpm_file" 2>&1) || true
                if echo "$RPM_SIG" | grep -qi "pgp\|gpg.*OK"; then
                    pass "RPM package signature valid: $(basename "$rpm_file")"
                else
                    fail "RPM package signature check: $(basename "$rpm_file")"
                fi
            done
        else
            pass "no local .rpm files to verify (expected in CI)"
        fi
    else
        pass "rpm command not available (APT-only system)"
    fi
fi

# --- Summary ---

echo "" >&2
echo "=== GPG Verification Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
