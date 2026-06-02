#!/usr/bin/env bash
# detect_volatile_atomic.sh - Audit C module volatile/atomic visibility usage
#
# Purpose: Rule 42 enforcement. Find volatile declarations or casts in the
#          C module that lack an adjacent justification comment, and direct
#          __atomic_* builtin usage that must be routed through reviewed
#          scalar/pointer publication helpers instead of ad hoc call sites.
#
# Arguments: None (scans components/nginx-module/src/ relative to repo root)
#
# Output: Findings to stderr; exit 0 if clean, exit 1 if violations found.
#
# Exit behaviour:
#   0 - no unjustified volatile usages and no direct __atomic_* usage
#   1 - one or more violations found

set -euo pipefail

SRC_DIR="components/nginx-module/src"

if [[ ! -d "$SRC_DIR" ]]; then
    echo "  [volatile-atomic] Source directory not found: $SRC_DIR" >&2
    exit 0
fi

VIOLATIONS=0

while IFS=: read -r file lineno _rest; do
    # Check if the volatile keyword has a justification comment within 3 lines
    context=$(sed -n "$((lineno > 3 ? lineno - 3 : 1)),$((lineno + 3))p" "$file" 2>/dev/null)
    if ! printf '%s\n' "$context" | grep -qiE 'single.?thread|compiler.?barrier|event.?loop|intentional'; then
        echo "  [volatile-atomic] VIOLATION: $file:$lineno - volatile without justification comment" >&2
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done < <(grep -rnE '(^|[^a-zA-Z_])volatile([^a-zA-Z_]|$)' "$SRC_DIR" 2>/dev/null || true)

while IFS=: read -r file lineno _rest; do
    echo "  [volatile-atomic] VIOLATION: $file:$lineno - direct __atomic_* usage requires reviewed scalar/pointer publication helper" >&2
    VIOLATIONS=$((VIOLATIONS + 1))
done < <(grep -rnE '__atomic_[a-zA-Z_]+' "$SRC_DIR" 2>/dev/null || true)

if [[ "$VIOLATIONS" -gt 0 ]]; then
    echo "  [volatile-atomic] FAIL: $VIOLATIONS violation(s)" >&2
    exit 1
fi

echo "  [volatile-atomic] PASS: no unjustified volatile or direct __atomic_* usages found" >&2
exit 0
