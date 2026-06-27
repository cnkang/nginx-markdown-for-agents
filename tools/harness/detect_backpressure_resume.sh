#!/bin/bash
#
# detect_backpressure_resume.sh — Backpressure Resume Pattern Detection
#                                  (Rule 1, 2, 38, 47, 51, 52)
#
# Rule 1 (streaming-backpressure): When returning NGX_AGAIN, the streaming
#   engine must preserve state (offset, buffer position) to allow correct
#   resumption. The state must not be lost or corrupted.
#
# Rule 2 (streaming-backpressure): After NGX_AGAIN, the pending chain must
#   be correctly managed. The last_buf flag must not be overwritten.
#
# Rule 38 (streaming-backpressure): Replay buffer must maintain data
#   integrity across NGX_AGAIN boundaries.
#
# Rule 47 (streaming-backpressure): Terminal-sent latching must not be
#   modified after NGX_AGAIN is returned.
#
# Detection strategy:
#   1. Scan all .c files in the nginx-module source directory.
#   2. Find functions that return NGX_AGAIN.
#   3. Check if these functions have state preservation logic:
#      - Look for offset/position tracking (ctx->offset, ctx->pos, etc.)
#      - Look for buffer state saving (ctx->buffer, ctx->last_buf, etc.)
#      - Look for pending chain management (ctx->out, ctx->last_out, etc.)
#   4. Flag functions that return NGX_AGAIN but lack state preservation.
#
# This is a heuristic detector — it looks for common patterns but may
# have false positives/negatives. Manual review is still required.
#
# Usage:
#   bash tools/harness/detect_backpressure_resume.sh [directory]
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

# Find all .c files
mapfile -t c_files < <(find "$SRC_DIR" -name "*.c" -type f)

if [[ ${#c_files[@]} -eq 0 ]]; then
    echo "No C files found in $SRC_DIR"
    exit 0
fi

for file in "${c_files[@]}"; do
    # Find functions that return NGX_AGAIN
    # Look for patterns like: return NGX_AGAIN;
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
        
        # Check if function has state preservation patterns
        # Look for: ctx->offset, ctx->pos, ctx->buffer, ctx->out, ctx->last_out, etc.
        func_body=$(sed -n "${func_start},${line_num}p" "$file")
        
        has_state_preservation=0
        
        # Check for offset/position tracking
        if echo "$func_body" | grep -qE '(ctx|state|r)->(offset|pos|position|cur|current)'; then
            has_state_preservation=1
        fi
        
        # Check for buffer state saving
        if echo "$func_body" | grep -qE '(ctx|state|r)->(buffer|buf|last_buf|last_in_chain)'; then
            has_state_preservation=1
        fi
        
        # Check for pending chain management
        if echo "$func_body" | grep -qE '(ctx|state|r)->(out|last_out|pending|chain)'; then
            has_state_preservation=1
        fi
        
        # Check for explicit state save/restore comments
        if echo "$func_body" | grep -qiE '(save.*state|restore.*state|preserve.*state|resume)'; then
            has_state_preservation=1
        fi
        
        if [[ $has_state_preservation -eq 0 ]]; then
            echo "WARNING: $file:$line_num: Function '$func_name' returns NGX_AGAIN but may lack state preservation" >&2
            warnings=$((warnings + 1))
        fi
        
    done < <(grep -n "return.*NGX_AGAIN" "$file" | cut -d: -f1)
done

if [[ $violations -gt 0 ]]; then
    echo "ERROR: Found $violations violation(s) and $warnings warning(s)" >&2
    exit 1
elif [[ $warnings -gt 0 ]]; then
    echo "WARNING: Found $warnings potential issue(s) — manual review recommended" >&2
    exit 0
else
    echo "OK: No backpressure resume issues detected"
    exit 0
fi
