#!/bin/bash
#
# detect_uninitialized_stack_struct.sh — Stack Struct Whole-Initialization Audit
#                                  (c-safety / testing-coverage rule)
#
# Scans for stack-allocated struct variables of types that MUST be fully
# initialized before use, where the declaration is only partially assigned
# (field-by-field) with no whole-struct memset / zero-init / init-helper.
#
# Background: a test declared `ngx_conf_t cf;` and assigned only
# cf.pool / cf.log.  The uninitialized cf.ctx carried stack garbage that
# bypassed the production NULL guard, dereferencing a wild pointer
# (SIGSEGV under GCC -O0; Apple clang and -O2 masked it by chance).
#
# Types requiring whole initialization are registered below (config-holder
# structs with runtime validity implied by zeroing).  The detector reports
# any stack declaration of a registered type whose following lines show
# partial field assignment without a preceding or following
# memset/zero-init/helper call.
#
# This detector is intentionally conservative (advisory for now): it reports
# candidate sites for human review instead of hard-blocking, to avoid false
# positives on structs that are intentionally zeroed elsewhere.
#
# Usage:
#   bash tools/harness/detect_uninitialized_stack_struct.sh [directory]
#     directory defaults to components/nginx-module (src + tests)
#
# Exit codes:
#   0 — no candidate violations found (or advisory mode)
#   1 — one or more candidate violations found (advisory report)
#   2 — usage/argument error

set -eu

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCAN_DIR="${1:-${REPO_ROOT}/components/nginx-module}"

if [[ ! -d "$SCAN_DIR" ]]; then
    echo "ERROR: Scan directory not found: $SCAN_DIR" >&2
    exit 2
fi

# Types that must be whole-initialized (zeroed or init-helper) when declared
# on the stack.  Extend as new config-holder / state structs are added.
WHOLE_INIT_TYPES=(
    ngx_conf_t
    ngx_http_markdown_conf_t
    ngx_http_markdown_main_conf_t
    ngx_http_markdown_loc_conf_t
)

tmp_violations=$(mktemp)
trap 'rm -f "$tmp_violations"' EXIT

while IFS= read -r -d '' file; do
    case "$file" in
        *.c) ;;
        *) continue ;;
    esac

    for type in "${WHOLE_INIT_TYPES[@]}"; do
        # Stack declaration: TYPE name;  or  TYPE name = ...;  or TYPE *name
        grep -nE "^[[:space:]]*${type}[[:space:]]+[a-zA-Z_][a-zA-Z0-9_]*[[:space:]]*(=|;)" "$file" 2>/dev/null | \
            while IFS=: read -r line_num content; do
            [[ -z "$line_num" ]] && continue
            [[ -z "$content" ]] && continue

            # Skip comments
            echo "$content" | grep -qE '^[[:space:]]*//|^[[:space:]]*\*' && continue

            # Skip pointer declarations (wildcard/ref params handled elsewhere)
            if echo "$content" | grep -qE "${type}[[:space:]]+\*"; then
                continue
            fi

            # Skip declarations with inline whole-init (= {0}, = { ... }, memset
            # on the same line, or a helper call).
            if echo "$content" | grep -qE '=\s*\{|=\s*0;|memset|memzero|_init\('; then
                continue
            fi

            var_name=$(echo "$content" | sed -E "s/^[[:space:]]*${type}[[:space:]]+([a-zA-Z_][a-zA-Z0-9_]*).*/\1/")
            [[ -z "$var_name" ]] && continue

            # Look at the following 15 lines for whole-init or field access
            window_start=$((line_num + 1))
            window_end=$((line_num + 15))
            tail_code=$(sed -n "${window_start},${window_end}p" "$file")

            # Whole-init present?
            if echo "$tail_code" | grep -qE "memset[[:space:]]*\(&${var_name}|memzero[[:space:]]*\(&${var_name}|${var_name}[[:space:]]*=[[:space:]]*\{|${var_name}[[:space:]]*=[[:space:]]*0;|_init[[:space:]]*\(&${var_name}|init_[a-z_]+[[:space:]]*\([^;]*&${var_name}"; then
                continue
            fi

            # No whole-init: if the var is used with field access (partial
            # assignment pattern) in the window, report it.
            if echo "$tail_code" | grep -qE "${var_name}\.[a-zA-Z_]+[[:space:]]*="; then
                echo "CANDIDATE: $file:$line_num — stack ${type} '${var_name}' assigned field-by-field without whole-struct initialization (memset/zero-init/helper); uninitialized members may carry stack garbage past NULL guards" >> "$tmp_violations"
            fi
        done
    done
done < <(find "$SCAN_DIR" \( -path '*/tests/*' -o -path '*/src/*' \) -type f -print0)

violations=$(wc -l < "$tmp_violations" | tr -d '[:space:]')

if [[ "$violations" -gt 0 ]]; then
    cat "$tmp_violations" >&2
    echo "NOTE: Found $violations candidate(s) for manual review (advisory detector)" >&2
    exit 1
else
    echo "OK: No partially-initialized stack struct candidates detected"
    exit 0
fi
