#!/usr/bin/env bash
# detect_finalize_return.sh — Audit ngx_http_finalize_request usage for immediate return
#
# Purpose: Find calls to ngx_http_finalize_request() that are not immediately
#          followed by a return statement, which risks double-finalize bugs.
#
# Arguments: None (scans components/nginx-module/src/ relative to repo root)
#
# Output: Findings to stderr; exit 0 if clean, exit 1 if violations found.
#
# Exit behaviour:
#   0 — all finalize calls are followed by return
#   1 — one or more finalize calls lack immediate return

set -euo pipefail

SRC_DIR="components/nginx-module/src"
VIOLATIONS=0

if [ ! -d "$SRC_DIR" ]; then
    echo "  [finalize-return] Source directory not found: $SRC_DIR" >&2
    exit 0
fi

# For each file containing ngx_http_finalize_request, check that the next
# non-blank, non-comment line after the call is a return statement or closing brace
while IFS= read -r file; do
    # Get line numbers of finalize calls (skip lines that are comments)
    while IFS=: read -r lineno content; do
        # Skip lines that are inside comments
        stripped="${content#"${content%%[![:space:]]*}"}"
        case "$stripped" in
            "/*"*|"*"*|"//"*) continue ;;
        esac
        # Skip lines that don't actually call the function (e.g., just mention it)
        case "$content" in
            *"ngx_http_finalize_request("*) ;;
            *) continue ;;
        esac
        # Read the next few non-blank lines after the finalize call
        next_meaningful=""
        scan_start=$((lineno + 1))
        scan_end=$((lineno + 5))
        while IFS= read -r nextline; do
            # Strip leading whitespace
            stripped="${nextline#"${nextline%%[![:space:]]*}"}"
            # Skip blank lines and pure comment lines
            case "$stripped" in
                "") continue ;;
                "/*"*) continue ;;
                "*"*) continue ;;
                "///"*) continue ;;
                "//"*) continue ;;
            esac
            next_meaningful="$stripped"
            break
        done < <(sed -n "${scan_start},${scan_end}p" "$file" 2>/dev/null)

        # Check if next meaningful line is return or closing brace
        case "$next_meaningful" in
            return*|"}"*|ngx_log_debug*)
                # OK — finalize followed by return, end of block, or debug log
                ;;
            "")
                # Could not determine — skip (might be end of file)
                ;;
            *)
                echo "  [WARN] $file:$lineno: finalize_request not followed by return" >&2
                echo "         Next: $next_meaningful" >&2
                VIOLATIONS=$((VIOLATIONS + 1))
                ;;
        esac
    done < <(grep -n 'ngx_http_finalize_request' "$file" 2>/dev/null | grep -v '^\s*/\*\|^\s*\*\|^\s*//' || true)
done < <(grep -rl 'ngx_http_finalize_request' "$SRC_DIR" 2>/dev/null || true)

if [ "$VIOLATIONS" -gt 0 ]; then
    echo "  [finalize-return] $VIOLATIONS call(s) to ngx_http_finalize_request not followed by return" >&2
    echo "  [finalize-return] Risk of double-finalize — manual review required" >&2
    exit 1
fi

echo "  [finalize-return] All ngx_http_finalize_request calls followed by return" >&2
exit 0
