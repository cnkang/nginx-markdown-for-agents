#!/bin/bash
#
# C-side Pure Logic Detection (Advisory)
#
# Lightweight advisory tool that scans C source files for functions that
# do NOT call NGINX APIs, indicating potential migration candidates to
# the Rust side per the FFI Migration Contract (B01.1).
#
# This is an advisory-only tool — it does NOT block CI. The manual audit
# in B01.2 (docs/architecture/FFI_MIGRATION_CONTRACT.md) serves as the
# authoritative source for migration decisions.
#
# Usage: bash tools/harness/detect_c_pure_logic.sh [directory]
#   directory defaults to components/nginx-module/src
#
# Exit codes:
#   0 — always (advisory only, never blocks)
#
# Corresponds to task B01.5 in spec 27-v070_prod.
#

set -euo pipefail

readonly SRC_DIR="${1:-components/nginx-module/src}"
readonly SCRIPT_NAME="$(basename "$0")"

# NGINX API patterns that indicate a function is tied to NGINX internals.
# Functions that do NOT match any of these are potential pure-logic candidates.
readonly NGINX_API_PATTERNS=(
    'ngx_palloc'
    'ngx_pnalloc'
    'ngx_pcalloc'
    'ngx_pfree'
    'ngx_pool_cleanup'
    'ngx_log_error'
    'ngx_log_debug'
    'ngx_http_send_header'
    'ngx_http_output_filter'
    'ngx_http_finalize_request'
    'ngx_http_next_'
    'ngx_http_top_'
    'ngx_alloc_chain_link'
    'ngx_free_chain'
    'ngx_chain_update_chains'
    'ngx_list_push'
    'ngx_hash_'
    'ngx_shmtx_'
    'ngx_slab_'
    'ngx_rbtree_'
    'ngx_event_'
    'ngx_add_timer'
    'ngx_del_timer'
    'ngx_conf_'
    'ngx_command_t'
    'ngx_module_t'
    'ngx_http_get_module_'
    'ngx_http_set_ctx'
    'ngx_snprintf'
    'ngx_slprintf'
    'ngx_sprintf'
    'ngx_memcpy'
    'ngx_memzero'
    'ngx_cpymem'
    'ngx_str_set'
    'ngx_string'
    'ngx_null_string'
    'r->connection'
    'r->headers_out'
    'r->headers_in'
    'r->pool'
    'r->main'
    'ngx_buf_t'
    'ngx_chain_t'
)

# Files to skip (test helpers, stubs, generated headers)
readonly SKIP_PATTERNS=(
    'test_'
    'stub_'
    '_test.c'
    'ffi_layout_check'
)

candidates=0
total_functions=0

echo "=== C-side Pure Logic Detection (Advisory) ===" >&2
echo "Scanning: ${SRC_DIR}" >&2
echo "Purpose: Identify functions that may be migration candidates to Rust" >&2
echo "" >&2

# Build a combined grep pattern for NGINX API usage
nginx_pattern=""
for pat in "${NGINX_API_PATTERNS[@]}"; do
    if [[ -z "$nginx_pattern" ]]; then
        nginx_pattern="$pat"
    else
        nginx_pattern="${nginx_pattern}|${pat}"
    fi
done

# Find all C source files (skip test/stub files)
while IFS= read -r file; do
    if [[ -z "$file" ]]; then
        continue
    fi

    basename_file="$(basename "$file")"

    # Skip excluded files
    skip=0
    for pat in "${SKIP_PATTERNS[@]}"; do
        if [[ "$basename_file" == *"$pat"* ]]; then
            skip=1
            break
        fi
    done
    if [[ "$skip" -eq 1 ]]; then
        continue
    fi

    # Extract function definitions (return type on own line, name at col 0)
    # NGINX style: function name starts at column 0 after return type line
    current_func=""
    func_start=0
    func_body=""
    in_func=0
    brace_depth=0

    while IFS= read -r line_content; do
        # Detect function start: line starting with identifier followed by (
        if [[ "$in_func" -eq 0 ]] && echo "$line_content" | grep -qE '^[a-z_][a-z0-9_]*\(' 2>/dev/null; then
            current_func="$(echo "$line_content" | sed -E 's/\(.*//')"
            in_func=1
            brace_depth=0
            func_body=""
        fi

        if [[ "$in_func" -eq 1 ]]; then
            func_body="${func_body}${line_content}"$'\n'
            # Count braces
            open_braces="$(echo "$line_content" | tr -cd '{' | wc -c | tr -d ' ')"
            close_braces="$(echo "$line_content" | tr -cd '}' | wc -c | tr -d ' ')"
            brace_depth=$((brace_depth + open_braces - close_braces))

            if [[ "$brace_depth" -le 0 && "$open_braces" -gt 0 || ("$brace_depth" -eq 0 && -n "$func_body" && "$open_braces" -eq 0 && "$close_braces" -gt 0) ]]; then
                # Function ended
                if [[ -n "$current_func" && "$brace_depth" -le 0 ]]; then
                    total_functions=$((total_functions + 1))

                    # Check if function body uses any NGINX API
                    if ! echo "$func_body" | grep -qE "$nginx_pattern" 2>/dev/null; then
                        # Skip trivial functions (< 3 lines of body)
                        body_lines="$(echo "$func_body" | wc -l | tr -d ' ')"
                        if [[ "$body_lines" -gt 5 ]]; then
                            echo "  CANDIDATE ${basename_file}::${current_func} (${body_lines} lines)" >&2
                            candidates=$((candidates + 1))
                        fi
                    fi
                fi
                in_func=0
                current_func=""
                func_body=""
            fi
        fi
    done < "$file"

done < <(find "$SRC_DIR" -name '*.c' -type f 2>/dev/null | sort)

echo "" >&2
echo "=== Summary ===" >&2
echo "  Total functions scanned: ${total_functions}" >&2
echo "  Migration candidates:    ${candidates}" >&2
echo "" >&2

if [[ "$candidates" -gt 0 ]]; then
    echo "NOTE: These are advisory findings only. Consult" >&2
    echo "  docs/architecture/FFI_MIGRATION_CONTRACT.md for authoritative" >&2
    echo "  migration decisions (B01.2 manual audit)." >&2
else
    echo "No pure-logic migration candidates detected." >&2
fi

echo "" >&2
echo "PASS (advisory — never blocks CI)" >&2
exit 0
