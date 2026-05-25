#!/usr/bin/env bash
# detect_ci_supply_chain.sh — Audit GitHub Actions for supply chain safety
#
# Purpose: Verify all third-party GitHub Actions in workflow files are pinned
#          to immutable SHA references (40-char hex), not mutable version tags.
#
# Arguments: None (scans .github/workflows/ relative to repo root)
#
# Output: Findings to stderr; exit 0 if clean, exit 1 if violations found.
#
# Exit behaviour:
#   0 — all actions pinned to SHA
#   1 — one or more actions use mutable tag references

set -euo pipefail

WORKFLOW_DIR=".github/workflows"
VIOLATIONS=0

if [ ! -d "$WORKFLOW_DIR" ]; then
    echo "  [supply-chain] No workflow directory found at $WORKFLOW_DIR" >&2
    exit 0
fi

# Match 'uses: owner/repo@ref' lines where ref is NOT a 40-char hex SHA
while IFS= read -r line; do
    # Extract file and content
    file="${line%%:*}"
    content="${line#*:}"

    # Skip comments
    case "$content" in
        *"#"*uses:*) continue ;;
    esac

    # Check if the ref after @ is a 40-char hex string
    ref="${content##*@}"
    # Trim trailing whitespace and comments
    ref="${ref%%#*}"
    ref="${ref%% *}"
    ref="$(echo "$ref" | tr -d '[:space:]')"

    if [ ${#ref} -ne 40 ]; then
        echo "  [FAIL] Mutable tag reference: $file: $content" >&2
        VIOLATIONS=$((VIOLATIONS + 1))
    else
        # Verify it's actually hex
        case "$ref" in
            *[!0-9a-fA-F]*)
                echo "  [FAIL] Non-SHA reference: $file: $content" >&2
                VIOLATIONS=$((VIOLATIONS + 1))
                ;;
        esac
    fi
done < <(grep -rn 'uses:.*@' "$WORKFLOW_DIR" 2>/dev/null | grep -v '^\s*#' || true)

if [ "$VIOLATIONS" -gt 0 ]; then
    echo "  [supply-chain] $VIOLATIONS action(s) not pinned to SHA" >&2
    exit 1
fi

echo "  [supply-chain] All GitHub Actions pinned to immutable SHA references" >&2
exit 0
