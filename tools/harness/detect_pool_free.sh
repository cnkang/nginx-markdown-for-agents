#!/bin/bash
#
# detect_pool_free.sh — Pool-Allocation/ngx_free Mismatch Detection (Rule 43)
#
# Rule 43 (memory-budget): A buffer allocated with ngx_palloc / ngx_pcalloc /
# ngx_pnalloc (pool allocation) must NEVER be released with ngx_free.
# ngx_free calls the system free(), which expects a heap pointer from
# malloc/ngx_alloc — passing a pool-internal pointer is undefined behavior.
#
# Detection strategy:
#   For each .c / .h file in the scan directory, find lines that assign a
#   variable from ngx_palloc / ngx_pcalloc / ngx_pnalloc, then search for
#   ngx_free(<varname>) calls on those same variables within the same
#   function scope (delimited by brace tracking).  Matching calls are
#   reported as ERROR and cause exit 1.
#
# Allowlist mechanism (comment-based):
#   A source comment containing "allow-pool-free" on the same line as the
#   ngx_free call or on the preceding line suppresses the finding.
#   This is intended for rare, documented exceptions (e.g. a wrapper that
#   conditionally re-allocates with ngx_alloc before freeing).
#
# Compatibility: macOS bash 3.2 (Rule 11), [[ ]] tests (Rule 18),
# POSIX ERE via grep -E (Rule 41).
#
# Usage:
#   bash tools/harness/detect_pool_free.sh [directory]
#     directory defaults to components/nginx-module/src
#
# Exit codes:
#   0 — no violations found
#   1 — one or more violations detected

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DIR="${1:-${REPO_ROOT}/components/nginx-module/src}"

violations=0

echo "=== Pool-Free Mismatch Detection (Rule 43) ===" >&2
echo "Scanning: ${SRC_DIR}" >&2
echo "" >&2

# ── Helper: check whether a comment suppresses this ngx_free call ──
#   Looks for "allow-pool-free" in a comment on the same line or the
#   preceding line.  Returns 0 (true) if suppressed, 1 otherwise.
is_allowlisted() {
    local file="$1"
    local line_num="$2"

    # Same-line check: the comment marker co-located with ngx_free
    local same_line
    same_line=$(sed -n "${line_num}p" "$file" 2>/dev/null || true)
    if echo "$same_line" | grep -qE '(//|/\*).*allow-pool-free'; then
        return 0
    fi

    # Preceding-line check: marker on the line before ngx_free
    local prev_line=$((line_num - 1))
    if [[ "$prev_line" -gt 0 ]]; then
        local prev
        prev=$(sed -n "${prev_line}p" "$file" 2>/dev/null || true)
        if echo "$prev" | grep -qE '(//|/\*).*allow-pool-free'; then
            return 0
        fi
    fi

    return 1
}

# ── Main scan loop ──
# Find all C source files; use find for traversal.
while IFS= read -r src_file; do
    [[ -z "$src_file" ]] && continue

    # Skip binary files (grep -qI checks for text)
    if ! grep -qI '' "$src_file" 2>/dev/null; then
        continue
    fi

    # Use awk to do function-scoped analysis:
    #   1. Track brace depth to know which function we're in.
    #   2. When we see "var = ngx_palloc|ngx_pcalloc|ngx_pnalloc", record
    #      (varname, function_start_line) in an associative structure.
    #   3. When we see "ngx_free(var)", check if that var was pool-allocated
    #      in the same function scope.
    #   4. Output: free_line:varname for each violation.
    #
    # We output violations as "lineno:varname" lines, then do the
    # allowlist check in bash.
    while IFS=: read -r free_line free_var; do
        [[ -z "$free_line" ]] && continue

        # Check allowlist
        if is_allowlisted "$src_file" "$free_line"; then
            echo "  ALLOWED ${src_file}:${free_line} — ngx_free(${free_var}) on pool-allocated pointer (allow-pool-free)" >&2
            continue
        fi

        echo "  ERROR   ${src_file}:${free_line} — ngx_free(${free_var}) called on pointer allocated with ngx_palloc/ngx_pcalloc/ngx_pnalloc (pool memory must not be ngx_free'd per Rule 43)" >&2
        violations=$((violations + 1))
    done < <(awk '
        BEGIN {
            depth = 0
            func_start = 0
            # Clear pool_vars array
            delete pool_vars
            pool_count = 0
        }
        {
            line = $0
            # Track brace depth to detect function boundaries
            # Count braces on this line
            open_braces = gsub(/{/, "{", line)
            close_braces = gsub(/}/, "}", line)

            # Before updating depth, check if we are closing a function
            # (depth will go to 0)
            will_close = (depth + open_braces - close_braces == 0 && depth > 0)

            # Detect pool allocation: var = ngx_palloc|ngx_pcalloc|ngx_pnalloc
            if (line ~ /ngx_palloc|ngx_pcalloc|ngx_pnalloc/) {
                # Skip comment lines
                if (line ~ /^[[:space:]]*(\/\*|\*|\/\/)/) {
                    # still process braces
                    depth += open_braces - close_braces
                    next
                }
                # Extract variable name: the identifier before "="
                # Strip leading whitespace
                ltrim = line
                sub(/^[[:space:]]+/, "", ltrim)
                # Get part before "="
                split(ltrim, parts, "=")
                lhs = parts[1]
                # Remove any cast or pointer dereference, get last identifier
                gsub(/[[:space:]]+$/, "", lhs)
                # Extract last identifier from lhs
                varname = lhs
                # Remove everything before the last identifier
                while (match(varname, /[a-zA-Z_][a-zA-Z0-9_]*$/)) {
                    varname = substr(varname, RSTART, RLENGTH)
                    break
                }
                # Validate it is a clean identifier
                if (varname ~ /^[a-zA-Z_][a-zA-Z0-9_]*$/ && depth > 0) {
                    pool_vars[varname] = func_start
                }
            }

            # Detect ngx_free(var) calls
            if (line ~ /ngx_free[[:space:]]*\(/) {
                # Skip comment lines
                if (line ~ /^[[:space:]]*(\/\*|\*|\/\/)/) {
                    depth += open_braces - close_braces
                    next
                }
                # Extract the variable name inside ngx_free(...)
                # Pattern: ngx_free(varname) or ngx_free(&varname)
                rest = line
                sub(/.*ngx_free[[:space:]]*\([[:space:]]*[&*]?[[:space:]]*/, "", rest)
                # Now rest starts with the variable name
                varname = rest
                # Extract leading identifier
                if (match(varname, /^[a-zA-Z_][a-zA-Z0-9_]*/)) {
                    varname = substr(varname, RSTART, RLENGTH)
                } else {
                    varname = ""
                }
                if (varname != "" && varname in pool_vars) {
                    print NR ":" varname
                }
            }

            # Update depth
            depth += open_braces - close_braces

            # If we just closed a function, reset pool_vars
            if (will_close) {
                delete pool_vars
                func_start = 0
            }
            # If we just entered a function (depth went from 0 to 1),
            # record the function start line
            if (depth == 1 && open_braces > 0) {
                func_start = NR
            }
        }
    ' "$src_file" 2>/dev/null || true)

done < <(find "$SRC_DIR" -type f \( -name '*.c' -o -name '*.h' \) 2>/dev/null | sort)

echo "" >&2
echo "=== Summary ===" >&2
echo "  Violations: ${violations}" >&2
echo "" >&2

if [[ "$violations" -gt 0 ]]; then
    echo "FAIL: ${violations} pool-free mismatch(es) found — use ngx_pfree for pool-allocated memory, or ngx_alloc/ngx_free for heap memory." >&2
    exit 1
fi

echo "PASS: No ngx_free calls on pool-allocated pointers." >&2
exit 0