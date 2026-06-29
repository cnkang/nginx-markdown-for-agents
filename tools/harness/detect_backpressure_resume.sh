#!/bin/bash
#
# detect_backpressure_resume.sh — Backpressure Resume Pattern Detection (v6)
#                                  (Rule 1, 2, 38, 47, 51, 52)
#
# Detects functions that return NGX_AGAIN without proper state preservation.
#
# Usage:
#   bash tools/harness/detect_backpressure_resume.sh [directory]
#     directory defaults to components/nginx-module/src
#
# Exit codes:
#   0 — no violations found
#   1 — one or more violations detected

set -eu

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DIR="${1:-${REPO_ROOT}/components/nginx-module/src}"

if [[ ! -d "$SRC_DIR" ]]; then
    echo "ERROR: Source directory not found: $SRC_DIR" >&2
    exit 2
fi

tmp_violations=$(mktemp)
trap 'rm -f "$tmp_violations"' EXIT

while IFS= read -r -d '' file; do
    grep -n "return.*NGX_AGAIN" "$file" 2>/dev/null | \
        while IFS=: read -r line_num content; do
        [[ -z "$line_num" ]] && continue
        [[ -z "$content" ]] && continue

        # Skip comment-only lines, documentation, and case labels
        echo "$content" | grep -qE '^[[:space:]]*//|^[[:space:]]*\*|@return|@param' && continue
        echo "$content" | grep -qE 'case.*:' && continue

        block_start=$((line_num - 30))
        [[ $block_start -lt 1 ]] && block_start=1

        surrounding_code=$(sed -n "${block_start},${line_num}p" "$file")

        # Skip decompression helper functions
        if echo "$surrounding_code" | grep -qE 'z_stream|avail_in|avail_out'; then
            continue
        fi

        # Check for ANY state-save pattern
        has_save=0

        if echo "$surrounding_code" | grep -qE 'pending_output|pending_has_data|save_pending|resume_pending'; then
            has_save=1
        fi
        if echo "$surrounding_code" | grep -qE 'fullbuffer\.(save|pending)'; then
            has_save=1
        fi
        if echo "$surrounding_code" | grep -qE 'buffered[[:space:]]+[|&]=|NGX_HTTP_MARKDOWN_BUFFERED'; then
            has_save=1
        fi
        if echo "$surrounding_code" | grep -qE 'main_terminal_sent|pending_output_bytes'; then
            has_save=1
        fi

        if [[ $has_save -ne 0 ]]; then
            continue
        fi

        # Extract function name
        func_name=$(echo "$surrounding_code" | grep -oE 'ngx_http_markdown_[a-zA-Z_0-9]+' | tail -1)
        if [[ -z "$func_name" ]]; then
            func_name=$(echo "$surrounding_code" | grep -oE '^[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]+\b[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]*\(' | sed 's/.* //;s/(//' | tail -1)
        fi
        [[ -z "$func_name" ]] && continue

        # Skip utility/pass-through functions
        if echo "$func_name" | grep -qE 'handle_|_result[^s]|helper|_log$|_free_'; then
            continue
        fi

        # Must use ctx/state (streaming state machine) to be a violation
        body_start=$(echo "$surrounding_code" | grep -n '^[[:space:]]*{' | tail -1 | cut -d: -f1)
        if [[ -n "$body_start" ]]; then
            body_code=$(echo "$surrounding_code" | sed -n "${body_start},\$p")
        else
            body_code="$surrounding_code"
        fi
        if ! echo "$body_code" | grep -qE '\b(ctx|state)\b'; then
            continue
        fi

        echo "VIOLATION: $file:$line_num — '$func_name' returns NGX_AGAIN without state save" >> "$tmp_violations"
    done
done < <(find "$SRC_DIR" -name "*.c" -type f -print0)

violations=$(wc -l < "$tmp_violations" | tr -d '[:space:]')

if [[ "$violations" -gt 0 ]]; then
    cat "$tmp_violations" >&2
    echo "ERROR: Found $violations backpressure violation(s)" >&2
    exit 1
else
    echo "OK: No backpressure resume issues detected"
    exit 0
fi
