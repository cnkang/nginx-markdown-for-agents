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
#   - GPG_KEY_URL environment variable (default: checked-in project public key)
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPG_KEY_URL="${GPG_KEY_URL:-file://${SCRIPT_DIR}/../nginx-markdown-for-agents-release.asc}"
REPO_BASE_URL="${REPO_BASE_URL:-https://packages.nginx-markdown.dev}"
EXPECTED_FINGERPRINT="${EXPECTED_FINGERPRINT:-15C792438EAA762B421E60D21E8D41E7D19A8A75}"
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
VERIFY_RUN_COUNT=0

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "PASS: $1" >&2
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $1" >&2
}

skip() {
    SKIP_COUNT=$((SKIP_COUNT + 1))
    echo "SKIP: $1" >&2
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
    echo "  GPG_KEY_URL          — URL to GPG public key (default: checked-in file)" >&2
    echo "  REPO_BASE_URL        — base URL of package repository" >&2
    echo "  EXPECTED_FINGERPRINT — expected GPG signing-subkey fingerprint (default: published)" >&2
    echo "  ALLOW_UNPUBLISHED=1  — pre-release opt-in: all-skip runs exit 0; a" >&2
    echo "                         release run must not set this" >&2
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

# Directory holding built RPM artifacts. Override with RPM_ARTIFACT_DIR when
# the packages were produced somewhere else.
RPM_ARTIFACT_DIR="${RPM_ARTIFACT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/dist}"
RPM_DIR=$(mktemp -d)
trap 'rm -rf "$RPM_DIR"' EXIT

# Run gpg against an isolated, empty home so the temporary keyring below is
# actually used. On hosts where GnuPG enables use-keyboxd, the
# --keyring/--no-default-keyring options are silently ignored and a bare
# invocation would import into the user's real key store.
GNUPGHOME="${RPM_DIR}/gnupg"
export GNUPGHOME
mkdir -m 700 -p "$GNUPGHOME"

# --- Step 1: Download and verify GPG key ---

echo "Step 1: Downloading GPG key..." >&2

KEY_FILE="${RPM_DIR}/signing-key.asc"

if [[ "$GPG_KEY_URL" == file://* ]]; then
    if cp "${GPG_KEY_URL#file://}" "$KEY_FILE" 2>/dev/null; then
        pass "GPG key loaded from local file ${GPG_KEY_URL#file://}"
    else
        fail "failed to read GPG key from local file ${GPG_KEY_URL#file://}"
        echo "Set GPG_KEY_URL to a valid key URL or provide a local key." >&2
    fi
elif curl -sf -o "$KEY_FILE" "$GPG_KEY_URL" 2>/dev/null; then
    pass "GPG key downloaded from $GPG_KEY_URL"
else
    fail "failed to download GPG key from $GPG_KEY_URL"
    echo "Set GPG_KEY_URL to a valid key URL or provide a local key." >&2
    # Create a placeholder for offline testing
    echo "--- PLACEHOLDER: key download failed (offline?) ---" >&2
fi

# Import key into temporary keyring
KEYRING="${RPM_DIR}/keyring.gpg"

if [[ -f "$KEY_FILE" ]] && [ -s "$KEY_FILE" ]; then
    GPG_IMPORT=$(gpg --no-default-keyring --keyring "$KEYRING" \
        --import "$KEY_FILE" 2>&1) || true

    # Confirm the import by listing the keyring instead of matching gpg's
    # localized human-readable output ("imported"/"not changed" wording
    # is translated on non-English locales). Both the human-readable and
    # --with-colons listing styles start the key record with "pub".
    if gpg --no-default-keyring --keyring "$KEYRING" \
        --list-keys 2>/dev/null | grep -q '^pub'; then
        pass "GPG key imported into test keyring"
    else
        fail "GPG key import failed"
        echo "$GPG_IMPORT" >&2
    fi

    # Verify fingerprint if expected value provided
    if [[ -n "$EXPECTED_FINGERPRINT" ]]; then
        # Select a signing-capable subkey: field 12 must contain "s",
        # and field 2 must not mark the subkey revoked/expired/invalid/
        # disabled.  The fingerprint is the fpr record that follows the
        # matching sub record.
        KEY_FP=$(gpg --no-default-keyring --keyring "$KEYRING" \
            --with-colons --with-subkey-fingerprint --list-keys 2>/dev/null \
            | awk -F: '
                $1 == "sub" {
                    want = ($12 ~ /s/ && $2 !~ /^[reid]/) ? 1 : 0
                    next
                }
                $1 == "fpr" && want { print $10; exit }')
        if [[ "$KEY_FP" = "$EXPECTED_FINGERPRINT" ]]; then
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

if [[ "$MODE" = "apt" ]] || [ "$MODE" = "both" ]; then
    echo "" >&2
    echo "Step 2: APT repository signature verification..." >&2

    APT_RELEASE_URL="${REPO_BASE_URL}/dists/stable/Release"
    APT_RELEASE_GPG_URL="${REPO_BASE_URL}/dists/stable/Release.gpg"
    APT_INRELEASE_URL="${REPO_BASE_URL}/dists/stable/InRelease"

    RELEASE_FILE="${RPM_DIR}/Release"
    RELEASE_GPG="${RPM_DIR}/Release.gpg"

    # Try to download Release and Release.gpg
    if curl -sf -o "$RELEASE_FILE" "$APT_RELEASE_URL" 2>/dev/null; then
        pass "APT Release file downloaded"

        if curl -sf -o "$RELEASE_GPG" "$APT_RELEASE_GPG_URL" 2>/dev/null; then
            pass "APT Release.gpg downloaded"

            # Verify signature
            VERIFY_OUTPUT=$(gpg --no-default-keyring --keyring "$KEYRING" \
                --verify "$RELEASE_GPG" "$RELEASE_FILE" 2>&1) || true
            VERIFY_RUN_COUNT=$((VERIFY_RUN_COUNT + 1))

            if echo "$VERIFY_OUTPUT" | grep -qi "good signature"; then
                pass "APT Release.gpg signature VALID"
            else
                fail "APT Release.gpg signature INVALID"
                echo "$VERIFY_OUTPUT" >&2
            fi
        else
            skip "APT Release.gpg not available (repo may not be published yet)"
        fi
    else
        skip "APT Release file not available at $APT_RELEASE_URL"
        echo "Repository may not be published yet. This is expected pre-release." >&2
    fi

    # Check InRelease (combined signed file)
    INRELEASE_FILE="${RPM_DIR}/InRelease"
    if curl -sf -o "$INRELEASE_FILE" "$APT_INRELEASE_URL" 2>/dev/null; then
        VERIFY_IR=$(gpg --no-default-keyring --keyring "$KEYRING" \
            --verify "$INRELEASE_FILE" 2>&1) || true
        VERIFY_RUN_COUNT=$((VERIFY_RUN_COUNT + 1))

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

if [[ "$MODE" = "yum" ]] || [ "$MODE" = "both" ]; then
    echo "" >&2
    echo "Step 3: YUM repository signature verification..." >&2

    YUM_REPOMD_URL="${REPO_BASE_URL}/rpm/repodata/repomd.xml"
    YUM_REPOMD_ASC_URL="${REPO_BASE_URL}/rpm/repodata/repomd.xml.asc"

    REPOMD_FILE="${RPM_DIR}/repomd.xml"
    REPOMD_ASC="${RPM_DIR}/repomd.xml.asc"

    if curl -sf -o "$REPOMD_FILE" "$YUM_REPOMD_URL" 2>/dev/null; then
        pass "YUM repomd.xml downloaded"

        if curl -sf -o "$REPOMD_ASC" "$YUM_REPOMD_ASC_URL" 2>/dev/null; then
            pass "YUM repomd.xml.asc downloaded"

            VERIFY_YUM=$(gpg --no-default-keyring --keyring "$KEYRING" \
                --verify "$REPOMD_ASC" "$REPOMD_FILE" 2>&1) || true
            VERIFY_RUN_COUNT=$((VERIFY_RUN_COUNT + 1))

            if echo "$VERIFY_YUM" | grep -qi "good signature"; then
                pass "YUM repomd.xml signature VALID"
            else
                fail "YUM repomd.xml signature INVALID"
                echo "$VERIFY_YUM" >&2
            fi
        else
            skip "YUM repomd.xml.asc not available (repo may not be published yet)"
        fi
    else
        skip "YUM repomd.xml not available at $YUM_REPOMD_URL"
        echo "Repository may not be published yet. This is expected pre-release." >&2
    fi

    # Verify RPM package signature if an .rpm file is available
    if command -v rpm >/dev/null 2>&1; then
        rpm_files_found=0
        while IFS= read -r -d '' rpm_file; do
            rpm_files_found=1
                RPM_SIG=""
                RPM_RC=0
                RPM_SIG=$(rpm -K "$rpm_file" 2>&1) || RPM_RC=$?
                VERIFY_RUN_COUNT=$((VERIFY_RUN_COUNT + 1))
                if [[ "$RPM_RC" -eq 0 ]] \
                    && ! echo "$RPM_SIG" | grep -qi "NOT OK" \
                    && echo "$RPM_SIG" | grep -qi "pgp\|gpg.*OK\|digests signatures OK"; then
                    pass "RPM package signature valid: $(basename "$rpm_file")"
                else
                    fail "RPM package signature check: $(basename "$rpm_file")"
                    echo "$RPM_SIG" >&2
                fi
        # Built RPMs land in dist/ (see .github/workflows/release-rpm.yml), not
        # in this test's temporary directory, so search the artifact directory.
        done < <(find "${RPM_ARTIFACT_DIR}" -maxdepth 1 -type f -name "*.rpm" \
                     -print0 2>/dev/null)
        if [[ "$rpm_files_found" -eq 0 ]]; then
            pass "no local .rpm files to verify (expected in CI)"
        fi
    else
        pass "rpm command not available (APT-only system)"
    fi
fi

# --- Summary ---

echo "" >&2
echo "=== GPG Verification Results ===" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed, $VERIFY_RUN_COUNT signature verifications executed" >&2

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "FAIL" >&2
    exit 1
fi

if [[ "$VERIFY_RUN_COUNT" -eq 0 ]]; then
    if [[ "${ALLOW_UNPUBLISHED:-0}" = "1" && "$SKIP_COUNT" -gt 0 ]]; then
        echo "SKIP-ONLY PASS: no signature verification ran; ALLOW_UNPUBLISHED=1 accepted the unpublished-repository skips" >&2
        exit 0
    fi
    if [[ "$SKIP_COUNT" -gt 0 ]]; then
        echo "FAIL: every signature verification was skipped (repository not published). A release run must verify at least one signature; set ALLOW_UNPUBLISHED=1 only for pre-release runs against an unpublished repository." >&2
    else
        echo "FAIL: no signature verification executed" >&2
    fi
    exit 1
fi

echo "PASS" >&2
exit 0
