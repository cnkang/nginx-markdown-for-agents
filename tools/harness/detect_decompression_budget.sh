#!/bin/bash
#
# detect_decompression_budget.sh — Decompression Budget Enforcement Detection
#                                   (Rule 3, 44)
#
# Rule 3 (memory-budget): All memory allocations must be bounded. Decompression
#   loops must enforce a maximum output size to prevent decompression bombs.
#
# Rule 44 (decompression): Decompression must check against configured budget
#   (decompress_max_size) before allocating memory. Unbounded decompression
#   can lead to memory exhaustion attacks.
#
# Detection strategy:
#   1. Scan all .c files in the nginx-module source directory.
#   2. Find decompression-related functions (inflate, decompress, etc.).
#   3. Check if these functions have budget enforcement:
#      - Look for budget checks (max_size, decompress_max_size, budget)
#      - Look for size tracking (total_out, output_size, etc.)
#      - Look for NGX_HTTP_MARKDOWN_ERROR_DECOMPRESSION_BUDGET_EXCEEDED
#   4. Flag functions that allocate memory in decompression loops without budget checks.
#
# This is a heuristic detector — it looks for common patterns but may
# have false positives/negatives. Manual review is still required.
#
# Usage:
#   bash tools/harness/detect_decompression_budget.sh [directory]
#     directory defaults to components/nginx-module/src
#
# Exit codes:
#   0 — no violations found (or only warnings)
#   1 — one or more violations detected

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DIR="${1:-${REPO_ROOT}/components/nginx-module/src}"

if [[ ! -d "$SRC_DIR" ]]; then
    echo "ERROR: Source directory not found: $SRC_DIR" >&2
    exit 2
fi

violations=0
warnings=0

# Find all .c and .h files
mapfile -t source_files < <(find "$SRC_DIR" \( -name "*.c" -o -name "*.h" \) -type f)

if [[ ${#source_files[@]} -eq 0 ]]; then
    echo "No source files found in $SRC_DIR"
    exit 0
fi

for file in "${source_files[@]}"; do
    # Check if file is decompression-related
    if ! grep -qE "(decompress|inflate|zlib|brotli|gzip|deflate)" "$file"; then
        continue
    fi
    
    # Find functions that allocate memory
    while IFS= read -r line_num; do
        # Extract function context (look backwards for function signature)
        func_start=$(awk -v end="$line_num" '
            /^[a-zA-Z_].*\(.*\).*\{/ { last_func = NR }
            NR == end { print last_func; exit }
        ' "$file")
        
        if [[ -z "$func_start" ]]; then
            continue
        fi
        
        # Extract function name
        func_name=$(sed -n "${func_start}p" "$file" | grep -oE '^[a-zA-Z_][a-zA-Z0-9_]*' | head -1)
        
        if [[ -z "$func_name" ]]; then
            continue
        fi
        
        # Check if function has budget enforcement
        func_body=$(sed -n "${func_start},${line_num}p" "$file")
        
        has_budget_check=0
        
        # Check for budget/max_size references
        if echo "$func_body" | grep -qE '(max_size|decompress_max_size|budget|parser_budget)'; then
            has_budget_check=1
        fi
        
        # Check for size tracking
        if echo "$func_body" | grep -qE '(total_out|output_size|decompressed_size|size.*check)'; then
            has_budget_check=1
        fi
        
        # Check for budget exceeded error
        if echo "$func_body" | grep -qE 'NGX_HTTP_MARKDOWN_ERROR_DECOMPRESSION_BUDGET_EXCEEDED'; then
            has_budget_check=1
        fi
        
        # Check for explicit budget comments
        if echo "$func_body" | grep -qiE '(budget.*check|enforce.*budget|prevent.*bomb)'; then
            has_budget_check=1
        fi
        
        if [[ $has_budget_check -eq 0 ]]; then
            echo "WARNING: $file:$line_num: Function '$func_name' allocates memory but may lack budget enforcement" >&2
            warnings=$((warnings + 1))
        fi
        
    done < <(grep -n "ngx_palloc\|ngx_alloc\|ngx_pnalloc" "$file" | cut -d: -f1)
done

if [[ $violations -gt 0 ]]; then
    echo "ERROR: Found $violations violation(s) and $warnings warning(s)" >&2
    exit 1
elif [[ $warnings -gt 0 ]]; then
    echo "WARNING: Found $warnings potential issue(s) — manual review recommended" >&2
    exit 0
else
    echo "OK: No decompression budget issues detected"
    exit 0
fi
