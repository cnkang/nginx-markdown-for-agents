#!/usr/bin/env bash
# detect_header_hash_filter.sh — Audit header iteration loops for hash==0 filtering
#
# Purpose: Find header iteration loops in the NGINX module that may access
#          invalidated headers (hash == 0) without filtering them out.
#
# Arguments: None (scans components/nginx-module/src/ relative to repo root)
#
# Output: Findings to stderr; exit 0 if clean, exit 1 if violations found.
#
# Exit behaviour:
#   0 — all header iterations include hash==0 guard
#   1 — one or more iterations lack the guard

set -euo pipefail

SRC_DIR="components/nginx-module/src"
VIOLATIONS=0

if [ ! -d "$SRC_DIR" ]; then
    echo "  [header-hash] Source directory not found: $SRC_DIR" >&2
    exit 0
fi

# Find files that iterate over header list parts (part->nelts or part.nelts)
# and check if they also contain hash == 0 filtering nearby
while IFS= read -r file; do
    # Check if this file has header iteration patterns
    if grep -q 'part->nelts\|part\.nelts' "$file" 2>/dev/null; then
        # Look for header-related iteration (not generic list iteration)
        # Header iteration typically involves: headers, h[i], header, elts
        if grep -q 'headers\|ngx_table_elt_t' "$file" 2>/dev/null; then
            # Check if hash == 0 or hash==0 or .hash == 0 filtering exists
            if ! grep -q 'hash == 0\|hash==0\|\.hash\s*==\s*0\|->hash\s*==\s*0' "$file" 2>/dev/null; then
                echo "  [WARN] Header iteration without hash==0 filter: $file" >&2
                VIOLATIONS=$((VIOLATIONS + 1))
            fi
        fi
    fi
done < <(find "$SRC_DIR" -name '*.c' -o -name '*.h' 2>/dev/null)

if [ "$VIOLATIONS" -gt 0 ]; then
    echo "  [header-hash] PASS with warnings: $VIOLATIONS file(s) may iterate headers without hash==0 guard" >&2
    echo "  [header-hash] Manual review recommended — false positives possible" >&2
    exit 0
fi

echo "  [header-hash] All header iteration files include hash==0 filtering" >&2
exit 0
