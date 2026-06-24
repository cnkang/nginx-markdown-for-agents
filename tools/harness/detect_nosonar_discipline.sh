#!/usr/bin/env bash
# detect_nosonar_discipline.sh — Audit NOSONAR annotations for reason+rule ref
#
# Purpose: Rule 24 enforcement.  Bare /* NOSONAR */ without a reason is
#          forbidden — it does not document why the suppression is safe and
#          makes future audits impossible.  Every NOSONAR annotation must
#          include a human-readable reason (and ideally a Rule reference).
#
# Arguments: None (scans components/nginx-module/src/ relative to repo root)
#
# Output: Findings to stderr; exit 0 if clean, exit 1 if violations found.
#
# Exit behaviour:
#   0 — all NOSONAR annotations include a reason
#   1 — one or more bare NOSONAR annotations found

set -euo pipefail

SRC_DIR="components/nginx-module/src"

if [[ ! -d "$SRC_DIR" ]]; then
    echo "  [nosonar] Source directory not found: $SRC_DIR" >&2
    exit 0
fi

VIOLATIONS=0
CHECKED=0

# ── Scanning ──
#
# For each line containing NOSONAR, check that the annotation includes
# a reason after the colon:  /* NOSONAR: <reason> ... */
# A bare /* NOSONAR */ without a colon+reason is a violation.

while IFS=: read -r file lineno content; do
    CHECKED=$((CHECKED + 1))

    # Check if the NOSONAR annotation has a reason (text after "NOSONAR:")
    # Any non-empty text after the colon is accepted.
    if printf '%s' "$content" | grep -qE 'NOSONAR:[[:space:]]*[^[:space:]]'; then
        : # OK — has a reason
    else
        echo "  [nosonar] VIOLATION: $file:$lineno — bare NOSONAR without reason (Rule 24 requires /* NOSONAR: <reason> */)" >&2
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done < <(grep -rn 'NOSONAR' "$SRC_DIR" 2>/dev/null || true)

# ── Verdict ──

if [[ "$VIOLATIONS" -gt 0 ]]; then
    echo "  [nosonar] FAIL: $VIOLATIONS bare NOSONAR annotation(s) found in $CHECKED total" >&2
    exit 1
fi

echo "  [nosonar] PASS: $CHECKED NOSONAR annotation(s) checked, all include a reason" >&2
exit 0
