#!/usr/bin/env bash
# detect_ci_supply_chain.sh — Audit GitHub Actions for supply chain safety
#
# Purpose: Verify all third-party GitHub Actions in workflow files are pinned
#          to immutable SHA references (40-char hex), not mutable version tags,
#          and reject network-to-shell execution in workflow script blocks.
#
# Arguments: None (scans .github/workflows/ relative to repo root)
#
# Output: Findings to stderr; exit 0 if clean, exit 1 if violations found.
#
# Exit behaviour:
#   0 — all actions pinned to SHA and no network-to-shell script execution
#   1 — one or more supply-chain violations found

set -euo pipefail

WORKFLOW_DIR=".github/workflows"
VIOLATIONS=0

if [[ ! -d "$WORKFLOW_DIR" ]]; then
    echo "  [supply-chain] No workflow directory found at $WORKFLOW_DIR" >&2
    exit 0
fi

check_network_to_shell() {
    local file="$1"
    local output
    local count

    output="$(awk -v file="$file" '
        function report() {
            printf "  [FAIL] Network-to-shell execution: %s:%d: %s\n",
                file, NR, $0
            violations++
        }

        {
            if ($0 ~ /(bash|sh)[[:space:]]*<\([[:space:]]*(curl|wget)/) {
                report()
                previous_network_line = 0
                next
            }

            if ($0 ~ /(curl|wget)[^|]*\|[[:space:]]*(sh|bash)([[:space:]]|$)/) {
                report()
                previous_network_line = 0
                next
            }

            if (previous_network_line != 0 && $0 ~ /^[[:space:]]*\|[[:space:]]*(sh|bash)([[:space:]]|$)/) {
                report()
                previous_network_line = 0
                next
            }

            previous_network_line = 0
            if ($0 ~ /(curl|wget).*\\[[:space:]]*$/) {
                previous_network_line = NR
            }
        }
    ' "$file")"

    if [[ -n "$output" ]]; then
        printf '%s\n' "$output" >&2
        count="$(printf '%s\n' "$output" | wc -l | tr -d '[:space:]')"
        VIOLATIONS=$((VIOLATIONS + count))
    fi
}

# Match 'uses: owner/repo@ref' lines where ref is NOT a 40-char hex SHA
while IFS= read -r line; do
    # Extract file and content
    file="${line%%:*}"
    content="${line#*:}"

    # Skip comments
    case "$content" in
        *"#"*uses:*) continue ;;
        *) ;;
    esac

    # Check if the ref after @ is a 40-char hex string
    ref="${content##*@}"
    # Trim trailing whitespace and comments
    ref="${ref%%#*}"
    ref="${ref%% *}"
    ref="$(echo "$ref" | tr -d '[:space:]')"

    if [[ ${#ref} -ne 40 ]]; then
        echo "  [FAIL] Mutable tag reference: $file: $content" >&2
        VIOLATIONS=$((VIOLATIONS + 1))
    else
        # Verify it's actually hex
        case "$ref" in
            *[!0-9a-fA-F]*)
                echo "  [FAIL] Non-SHA reference: $file: $content" >&2
                VIOLATIONS=$((VIOLATIONS + 1))
                ;;
            *)
                # Valid 40-char hex SHA — no action needed
                ;;
        esac
    fi
done < <(grep -rn 'uses:.*@' "$WORKFLOW_DIR" 2>/dev/null | grep -v '^[[:space:]]*#' || true)

while IFS= read -r workflow_file; do
    check_network_to_shell "$workflow_file"
done < <(find "$WORKFLOW_DIR" -type f \( -name '*.yml' -o -name '*.yaml' \) -print | sort)

if [[ "$VIOLATIONS" -gt 0 ]]; then
    echo "  [supply-chain] $VIOLATIONS supply-chain violation(s) found" >&2
    exit 1
fi

echo "  [supply-chain] GitHub Actions supply-chain checks passed" >&2
exit 0
