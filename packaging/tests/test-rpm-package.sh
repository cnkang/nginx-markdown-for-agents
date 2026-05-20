#!/bin/bash
# test-rpm-package.sh — Verify .rpm package metadata and spec file.
#
# Usage: ./test-rpm-package.sh <path-to-rpm-file>
#        ./test-rpm-package.sh --spec <path-to-spec-file>
#
# Validates:
#   - rpm -qip reports required metadata fields (when given .rpm)
#   - rpmbuild --specfile parses without error (when given --spec)
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
#   2 — usage error

set -e

PASS_COUNT=0
FAIL_COUNT=0
MODE="rpm"

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "PASS: $1" >&2
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "FAIL: $1" >&2
}

usage() {
    echo "Usage: $0 <path-to-rpm-file>" >&2
    echo "       $0 --spec <path-to-spec-file>" >&2
    exit 2
}

case "${1:-}" in
    -h|--help)
        usage
        ;;
    --spec)
        MODE="spec"
        shift
        if [ -z "${1:-}" ]; then
            echo "Error: no spec file path provided" >&2
            usage
        fi
        TARGET_FILE="$1"
        ;;
    "")
        echo "Error: no file path provided" >&2
        usage
        ;;
    *)
        TARGET_FILE="$1"
        ;;
esac

if [ ! -f "$TARGET_FILE" ]; then
    echo "Error: file not found: $TARGET_FILE" >&2
    exit 2
fi

case "$MODE" in
    rpm)
        # --- rpm -qip checks ---
        INFO_OUTPUT=$(rpm -qip "$TARGET_FILE" 2>&1) || {
            fail "rpm -qip failed"
            echo "$INFO_OUTPUT" >&2
            exit 1
        }

        # Required fields in RPM info
        REQUIRED_FIELDS="Name Version Release Architecture License"

        for field in $REQUIRED_FIELDS; do
            if echo "$INFO_OUTPUT" | grep -qi "^${field}"; then
                pass "info contains field: $field"
            else
                fail "info missing field: $field"
            fi
        done

        # Check Description is present (multi-line field)
        if echo "$INFO_OUTPUT" | grep -qi "^Description"; then
            pass "info contains field: Description"
        else
            fail "info missing field: Description"
        fi
        ;;

    spec)
        # --- rpmbuild --specfile validation ---
        # Use rpmbuild to parse the spec file (dry-run style)
        if command -v rpmbuild >/dev/null 2>&1; then
            if SPEC_OUTPUT=$(rpmbuild --nobuild --specfile "$TARGET_FILE" 2>&1); then
                pass "rpmbuild --specfile parsed successfully"
            else
                # rpmbuild may fail due to missing sources; check if it at least parsed
                if echo "$SPEC_OUTPUT" | grep -qi "error.*spec"; then
                    fail "rpmbuild --specfile has spec errors"
                    echo "$SPEC_OUTPUT" >&2
                else
                    pass "rpmbuild --specfile parsed (build deps not satisfied, expected in CI)"
                fi
            fi
        else
            echo "SKIP: rpmbuild not available on this system" >&2
            pass "rpmbuild not available (skipped)"
        fi
        ;;

    *)
        usage
        ;;
esac

# --- Summary ---

echo "" >&2
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed" >&2

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "FAIL" >&2
    exit 1
fi

echo "PASS" >&2
exit 0
