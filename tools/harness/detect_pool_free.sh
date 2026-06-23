#!/bin/bash
#
# detect_pool_free.sh — Pool-Allocation/ngx_free Mismatch Detection (Rule 43)
#
# Rule 43 (memory-budget): A buffer allocated with ngx_palloc / ngx_pcalloc /
# ngx_pnalloc (pool allocation) must NEVER be released with ngx_free.
# ngx_free calls the system free(), which expects a heap pointer from
# malloc/ngx_alloc — passing a pool-internal pointer is undefined behavior.
#
# The resizable buffer backing store (ctx->buffer.data, decomp workspace,
# scratch) must use ngx_alloc/ngx_free exclusively; never pool-allocate
# then ngx_free.  Conversely, normal request-lifetime pool memory must
# NOT be explicitly released — there is no valid use of ngx_pfree for
# ordinary pool allocations; just let the pool be destroyed with the
# request.
#
# Detection strategy:
#   For each .c / .h file in the scan directory, find lines that assign an
#   lvalue from ngx_palloc / ngx_pcalloc / ngx_pnalloc.  The lvalue is
#   normalized to a canonical form covering: plain identifiers
#   (``buf``), pointer/struct field access (``ctx->buffer.data``,
#   ``obj.field``), and dereferenced-pointer fields (``(*ptr).field``).
#   Then search for ngx_free(<same-lvalue>) calls on the same lvalue
#   within the same function scope (brace tracking).  Matching calls
#   are reported as ERROR and cause exit 1.
#
# Allowlist mechanism (comment-based):
#   A source comment containing "allow-pool-free" on the same line as the
#   ngx_free call or on the preceding line suppresses the finding.
#   This is intended for rare, documented exceptions (e.g. a wrapper
#   that conditionally re-allocates with ngx_alloc before freeing).
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
is_allowlisted() {
    local file="$1"
    local line_num="$2"

    local same_line
    same_line=$(sed -n "${line_num}p" "$file" 2>/dev/null || true)
    if echo "$same_line" | grep -qE '(//|/\*).*allow-pool-free'; then
        return 0
    fi

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
while IFS= read -r src_file; do
    [[ -z "$src_file" ]] && continue

    if ! grep -qI '' "$src_file" 2>/dev/null; then
        continue
    fi

    # awk function-scoped analysis.  The key normalization is:
    #   - pool allocation LHS: capture the full lvalue expression to the
    #     left of "= ngx_palloc|ngx_pcalloc|ngx_pnalloc".  The lvalue may
    #     be "buf", "ctx->buffer.data", "obj.field", or "(*ptr).field".
    #   - ngx_free argument: capture the full expression inside
    #     ngx_free(...), stripping leading whitespace, "&", "*", "(", ")".
    #   - Both sides are reduced to a canonical token form so that
    #     "ctx->buffer.data" on the alloc side matches the same string
    #     on the free side.
    while IFS=: read -r free_line free_var; do
        [[ -z "$free_line" ]] && continue

        if is_allowlisted "$src_file" "$free_line"; then
            echo "  ALLOWED ${src_file}:${free_line} — ngx_free(${free_var}) on pool-allocated pointer (allow-pool-free)" >&2
            continue
        fi

        echo "  ERROR   ${src_file}:${free_line} — ngx_free(${free_var}) called on pointer allocated with ngx_palloc/ngx_pcalloc/ngx_pnalloc (Rule 43: do not explicitly free pool memory; if a resizable heap buffer is intended, use ngx_alloc/ngx_free consistently)" >&2
        violations=$((violations + 1))
    done < <(awk '
        function normalize_lvalue(s,   parts, lhs, t, ch, out, i, n, depth, start, n2) {
            # s is a line like "    ctx->buffer.data = ngx_palloc(...);"
            # or "    ngx_free(ctx->buffer.data);"
            # We want the lvalue / argument expression, normalized to
            # the canonical token sequence used on both sides.
            # Split on "=" for alloc side.
            n = split(s, parts, "=")
            if (n < 2) return ""
            lhs = parts[1]
            # Strip trailing whitespace
            sub(/[[:space:]]+$/, "", lhs)
            # Strip leading whitespace
            sub(/^[[:space:]]+/, "", lhs)
            # Strip a leading type cast or qualifier words that may
            # precede the lvalue: e.g. "u_char *buf =" or
            # "ngx_buf_t *b =".  We keep only the final token sequence
            # after the last space-separated word that is NOT a
            # pointer/field symbol.  Approach: walk from the end,
            # collecting identifier chars, "->", ".", "*", "(", ")"
            # back to the start of the lvalue expression.
            # Heuristic: the lvalue starts after the last whitespace
            # that is not inside parentheses.
            # Find the last whitespace at paren-depth 0:
            depth = 0
            start = 1
            n2 = length(lhs)
            for (i = n2; i >= 1; i--) {
                ch = substr(lhs, i, 1)
                if (ch == ")") depth++
                if (ch == "(") depth--
                if (depth == 0 && ch == " " || ch == "\t") {
                    start = i + 1
                    break
                }
            }
            out = substr(lhs, start)
            # Strip any trailing "*" that belongs to a type declaration
            # rather than the lvalue (e.g. "u_char *buf" -> "buf",
            # but "(*ptr).field" keeps its parens).
            # Only strip a leading "*" if there is no "(" in the
            # expression (so "(*ptr).field" is preserved).
            if (index(out, "(") == 0) {
                sub(/^[[:space:]]*\*/, "", out)
            }
            return out
        }
        function normalize_free_arg(s,   rest, t, i, n, ch, depth, endidx) {
            # s is a line containing ngx_free(...).  Extract the
            # expression inside the parentheses and normalize it.
            # Strip everything up to and including "ngx_free(" (with
            # optional whitespace).
            rest = s
            sub(/.*ngx_free[[:space:]]*\([[:space:]]*/, "", rest)
            # rest now starts with the argument expression; find the
            # matching close paren.
            depth = 1
            n = length(rest)
            endidx = n
            for (i = 1; i <= n; i++) {
                ch = substr(rest, i, 1)
                if (ch == "(") depth++
                if (ch == ")") {
                    depth--
                    if (depth == 0) { endidx = i - 1; break }
                }
            }
            t = substr(rest, 1, endidx)
            # Strip leading "&" or "*" (address-of or deref of the arg
            # itself — we want the underlying lvalue).  Preserve
            # internal "(*ptr).field" parens.
            sub(/^[[:space:]]*[&*][[:space:]]*/, "", t)
            return t
        }
        BEGIN {
            depth = 0
            func_start = 0
            delete pool_vars
            pool_count = 0
        }
        {
            line = $0
            open_braces = gsub(/{/, "{", line)
            close_braces = gsub(/}/, "}", line)

            will_close = (depth + open_braces - close_braces == 0 && depth > 0)

            # Skip comment-only lines but still update brace depth
            if (line ~ /^[[:space:]]*(\/\*|\*|\/\/)/) {
                depth += open_braces - close_braces
                next
            }

            # Detect pool allocation: <lvalue> = ngx_palloc|ngx_pcalloc|ngx_pnalloc(...)
            is_pool_alloc = 0
            if (line ~ /ngx_palloc[[:space:]]*\(/ || line ~ /ngx_pcalloc[[:space:]]*\(/ || line ~ /ngx_pnalloc[[:space:]]*\(/) {
                # Only treat as alloc if this is an assignment (has "=")
                # and the alloc call is on the right-hand side.
                if (index(line, "=") > 0 && depth > 0) {
                    # Quick check: ensure the alloc call appears after "="
                    eqpos = index(line, "=")
                    allocpos = match(line, /ngx_p[acn]+[[:alpha:]]*\(/)
                    if (allocpos > eqpos || allocpos == eqpos + 0) {
                        varname = normalize_lvalue(line)
                        # Validate it is a plausible lvalue
                        if (varname ~ /^[a-zA-Z0-9_*().>[-]+$/ && length(varname) > 0) {
                            pool_vars[varname] = func_start
                            is_pool_alloc = 1
                        }
                    }
                }
            }

            # Reassignment tracking: if this is an assignment whose RHS
            # is NOT a pool allocator, drop the lvalue from pool_vars so
            # a later ngx_free is not mis-attributed to the earlier pool
            # allocation.  This handles the "pool then heap" reassignment
            # pattern where freeing the heap pointer is correct.
            if (!is_pool_alloc && index(line, "=") > 0 && depth > 0) {
                # Only consider simple single-"=" assignments (avoid
                # "==" comparisons by checking the char after "=").
                eqpos = index(line, "=")
                after = substr(line, eqpos, 2)
                if (after !~ /^==/) {
                    varname = normalize_lvalue(line)
                    if (varname != "" && varname in pool_vars) {
                        # RHS is not a pool allocator we recognized
                        # above; drop the tracking.
                        delete pool_vars[varname]
                    }
                }
            }

            # Detect ngx_free(<arg>) calls
            if (line ~ /ngx_free[[:space:]]*\(/) {
                varname = normalize_free_arg(line)
                if (varname != "" && varname in pool_vars) {
                    print NR ":" varname
                }
            }

            depth += open_braces - close_braces

            if (will_close) {
                delete pool_vars
                func_start = 0
            }
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
    echo "FAIL: ${violations} pool-free mismatch(es) found — do not explicitly free pool memory with ngx_free; if a resizable heap buffer is intended, allocate with ngx_alloc and free with ngx_free consistently (Rule 43)." >&2
    exit 1
fi

echo "PASS: No ngx_free calls on pool-allocated pointers." >&2
exit 0