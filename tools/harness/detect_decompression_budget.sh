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
#   0 — no violations found
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

strip_c_comments() {
    local source_file="$1"

    awk '
        BEGIN { in_block = 0 }
        {
            line = $0
            output = ""
            i = 1
            in_string = 0
            while (i <= length(line)) {
                ch = substr(line, i, 1)
                pair = substr(line, i, 2)

                if (in_block) {
                    end = index(substr(line, i), "*/")
                    if (end == 0) {
                        i = length(line) + 1
                    } else {
                        i += end + 1
                        in_block = 0
                    }
                    continue
                }

                if (in_string) {
                    if (ch == "\\") {
                        i += 2
                    } else {
                        if (ch == "\"") {
                            in_string = 0
                        }
                        i++
                    }
                    continue
                }

                if (pair == "/*") {
                    in_block = 1
                    i += 2
                    continue
                }
                if (pair == "//") {
                    break
                }
                if (ch == "\"") {
                    in_string = 1
                    i++
                    continue
                }

                output = output ch
                i++
            }
            print output
        }
    ' "$source_file"
    return 0
}

# Find all .c and .h files
source_files=()
while IFS= read -r -d '' file; do
    source_files+=("$file")
done < <(find "$SRC_DIR" \( -name "*.c" -o -name "*.h" \) -type f -print0)

if [[ ${#source_files[@]} -eq 0 ]]; then
    echo "No source files found in $SRC_DIR" >&2
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
        func_start=$(strip_c_comments "$file" | awk -v end="$line_num" '
            /^[a-zA-Z_].*\(.*\).*\{/ {
                last_func = NR
                pending_func = 0
            }
            /^[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]*\(.*\)[[:space:]]*\{/ {
                last_func = NR
                pending_func = 0
            }
            /^[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]*\(/ {
                pending_func = NR
            }
            /^[[:space:]]*\{/ {
                if (pending_func > 0) {
                    last_func = pending_func
                    pending_func = 0
                }
            }
            NR == end && !printed { print last_func; printed = 1 }
        ')

        if [[ -z "$func_start" ]]; then
            continue
        fi

        # Extract function name
        func_name=$(sed -n "${func_start}p" "$file" | grep -oE '^[a-zA-Z_][a-zA-Z0-9_]*' | head -1)

        if [[ -z "$func_name" ]]; then
            continue
        fi

        # Only inspect functions whose names identify a decompression path.
        # A source file may also contain configuration, headers, or response
        # helpers that allocate ordinary request-lifetime objects.
        if ! printf '%s\n' "$func_name" \
            | grep -qE '(decompress|decomp|inflate|deflate|gzip|brotli|zlib|compressed|compression)'; then
            continue
        fi

        # This helper transfers an already budget-checked heap buffer into
        # pool memory; the pool allocation is not decompression growth.
        if [[ "$func_name" == "ngx_http_markdown_streaming_decomp_finalize_buf" ]]; then
            continue
        fi

        # Check if function has budget enforcement
        func_body=$(strip_c_comments "$file" | sed -n "${func_start},${line_num}p")

        has_budget_check=0

        # Check for budget/max_size references
        if echo "$func_body" | grep -qE '(max_size|max_output|decompress_max_size|budget|parser_budget|reserve|workspace|remaining|limit|counter|chain_size|input_size|combined_len|probe|check_limit|NGX_HTTP_MARKDOWN_DECOMP_)'; then
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
            echo "ERROR: $file:$line_num: Function '$func_name' allocates memory but may lack budget enforcement" >&2
            violations=$((violations + 1))
        fi

    done < <(
        strip_c_comments "$file" \
            | grep -nE '(^|[^[:alnum:]_])ngx_(alloc|pnalloc)[[:space:]]*\(' \
            | cut -d: -f1
    )
done

if [[ $violations -gt 0 ]]; then
    echo "ERROR: Found $violations violation(s)" >&2
    exit 1
else
    echo "OK: No decompression budget issues detected"
    exit 0
fi
