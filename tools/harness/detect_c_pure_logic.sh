#!/bin/bash
#
# detect_c_pure_logic.sh — C-side Pure Logic Detection (FFI migration advisory)
#
# Scans C source files for functions that do NOT reference any NGINX API,
# indicating potential migration candidates to the Rust side per the
# Rust-first FFI Migration Contract (B01, ADR-0010).
#
# The authoritative migration decisions live in the manual audit
# (docs/architecture/FFI_MIGRATION_CONTRACT.md B01.2).  This detector is a
# fast first-pass signal, not the source of truth.
#
# Modes:
#   (default)   Advisory.  Prints candidates and a summary; always exits 0.
#               This is the mode wired into `make harness-security-checks`
#               so the signal is produced on every run without blocking CI.
#   --check     Strict.  Exits 1 when one or more candidates are found.
#               Intended for deliberate, targeted enforcement runs (e.g. when
#               a PR claims to have migrated all pure logic for a file); NOT
#               wired into the default gate because the contract permits a
#               curated backlog of known C-side pure-logic functions.
#
# Usage:
#   bash tools/harness/detect_c_pure_logic.sh [--check] [directory]
#     directory defaults to components/nginx-module/src
#
# Exit codes:
#   0 — advisory mode (always), or strict mode with no candidates
#   1 — strict mode (--check) with one or more candidates, or usage error
#
# Corresponds to task B01.5 in spec 27-v070_prod.
#

set -euo pipefail

STRICT=0
SRC_DIR=""

usage() {
    echo "Usage: $(basename "$0") [--check] [directory]" >&2
    return 0
}

# Parse arguments: an optional --check flag and an optional directory.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --check)
            STRICT=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            echo "  [pure-logic] Unknown option: $1" >&2
            usage
            exit 1
            ;;
        *)
            if [[ -n "$SRC_DIR" ]]; then
                echo "  [pure-logic] Unexpected extra argument: $1" >&2
                usage
                exit 1
            fi
            SRC_DIR="$1"
            shift
            ;;
    esac
done

if [[ -z "$SRC_DIR" ]]; then
    SRC_DIR="components/nginx-module/src"
fi

# Minimum function body size (lines) before a no-NGINX-API function is
# considered a migration candidate.  Trivial accessors/wrappers are ignored.
readonly MIN_BODY_LINES=5

# NGINX API substrings that mark a function as tied to NGINX internals.
# A function whose body contains NONE of these is a pure-logic candidate.
# Joined with "|" and matched as fixed substrings (via index()) inside awk,
# so no regex metacharacters are interpreted.
readonly NGINX_API_PATTERNS="ngx_palloc|ngx_pnalloc|ngx_pcalloc|ngx_pfree|ngx_pool_cleanup|ngx_log_error|ngx_log_debug|ngx_http_send_header|ngx_http_output_filter|ngx_http_finalize_request|ngx_http_next_|ngx_http_top_|ngx_alloc_chain_link|ngx_free_chain|ngx_chain_update_chains|ngx_list_push|ngx_hash_|ngx_shmtx_|ngx_slab_|ngx_rbtree_|ngx_event_|ngx_add_timer|ngx_del_timer|ngx_conf_|ngx_command_t|ngx_module_t|ngx_http_get_module_|ngx_http_set_ctx|ngx_snprintf|ngx_slprintf|ngx_sprintf|ngx_memcpy|ngx_memzero|ngx_cpymem|ngx_str_set|ngx_string|ngx_null_string|r->connection|r->headers_out|r->headers_in|r->pool|r->main|ngx_buf_t|ngx_chain_t"

if [[ ! -d "$SRC_DIR" ]]; then
    echo "  [pure-logic] Source directory not found: $SRC_DIR" >&2
    # Missing directory is not a failure in either mode (graceful skip).
    exit 0
fi

echo "=== C-side Pure Logic Detection (FFI migration advisory) ===" >&2
echo "Scanning: ${SRC_DIR}" >&2
echo "Purpose: Identify C functions that may be migration candidates to Rust" >&2
echo "" >&2

# Collect candidate source files (skip test/stub/generated files) into a
# NUL-delimited traversal so the single awk pass below stays bounded and fast
# regardless of the number of functions.  The previous per-line bash
# implementation spawned multiple subprocesses per source line and could run
# for minutes; a single awk pass is effectively instantaneous.  File order is
# not sorted (advisory output only), avoiding reliance on GNU-only `sort -z`.
candidate_files=()
while IFS= read -r -d '' file; do
    base="$(basename "$file")"
    case "$base" in
        test_*|stub_*|*_test.c|*ffi_layout_check*)
            continue
            ;;
        *)
            candidate_files+=("$file")
            ;;
    esac
done < <(find "$SRC_DIR" -name '*.c' -type f -print0 2>/dev/null)

candidates=0
total_functions=0

# Expand the file array safely under `set -u` even when empty (bash 3.2).
if [[ "${#candidate_files[@]}" -gt 0 ]]; then
    # awk emits human-readable CANDIDATE lines to stderr and a single
    # machine-readable "candidates total" summary line to stdout, which the
    # shell captures below.
    summary="$(
        awk -v patterns="$NGINX_API_PATTERNS" -v min_lines="$MIN_BODY_LINES" '
        BEGIN {
            np = split(patterns, pat, "|");
            in_func = 0; depth = 0; seen_open = 0;
            body = ""; fname = ""; bodylines = 0;
            total = 0; candidates = 0;
        }
        function reset_state() {
            in_func = 0; depth = 0; seen_open = 0;
            body = ""; fname = ""; bodylines = 0;
        }
        function evaluate(   i) {
            if (fname == "") { return; }
            total++;
            if (bodylines <= min_lines) { return; }
            for (i = 1; i <= np; i++) {
                if (index(body, pat[i]) > 0) { return; }
            }
            printf("  CANDIDATE %s::%s (%d lines)\n",
                   curfile, fname, bodylines) > "/dev/stderr";
            candidates++;
        }
        FNR == 1 {
            if (in_func) { reset_state(); }
            curfile = FILENAME;
            sub(/.*\//, "", curfile);
        }
        {
            line = $0;
            if (!in_func && line ~ /^[a-z_][a-z0-9_]*\(/) {
                fname = line;
                sub(/\(.*/, "", fname);
                in_func = 1; depth = 0; seen_open = 0;
                body = ""; bodylines = 0;
            }
            if (in_func) {
                body = body line "\n";
                bodylines++;
                tmp = line; no = gsub(/{/, "{", tmp);
                tmp = line; nc = gsub(/}/, "}", tmp);
                depth += no - nc;
                if (no > 0) { seen_open = 1; }
                if (!seen_open && line ~ /;[ \t]*$/) {
                    # Prototype/forward declaration, not a definition.
                    reset_state();
                    next;
                }
                if (seen_open && depth <= 0) {
                    evaluate();
                    reset_state();
                }
            }
        }
        END {
            printf("%d %d\n", candidates, total);
        }
        ' "${candidate_files[@]}"
    )"
    candidates="${summary%% *}"
    total_functions="${summary##* }"
fi

echo "" >&2
echo "=== Summary ===" >&2
echo "  Total functions scanned: ${total_functions}" >&2
echo "  Migration candidates:    ${candidates}" >&2
echo "" >&2

if [[ "$candidates" -gt 0 ]]; then
    echo "NOTE: Advisory findings. Consult" >&2
    echo "  docs/architecture/FFI_MIGRATION_CONTRACT.md for authoritative" >&2
    echo "  migration decisions (B01.2 manual audit)." >&2
else
    echo "No pure-logic migration candidates detected." >&2
fi

echo "" >&2

if [[ "$STRICT" -eq 1 ]]; then
    if [[ "$candidates" -gt 0 ]]; then
        echo "FAIL (--check): ${candidates} pure-logic candidate(s) found" >&2
        exit 1
    fi
    echo "PASS (--check): no pure-logic candidates" >&2
    exit 0
fi

echo "PASS (advisory — never blocks CI)" >&2
exit 0
