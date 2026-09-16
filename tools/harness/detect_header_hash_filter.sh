#!/usr/bin/env bash
# detect_header_hash_filter.sh — Audit header iteration loops for hash==0 filtering
#
# Purpose: Find header iteration loops in the NGINX module that may access
#          invalidated headers (hash == 0) without filtering them out.
#
# Arguments: None (scans components/nginx-module/src/ relative to repo root)
#
# Output: Findings to stderr; exit 0 if clean, exit 1 if violations found.
#
# Exit behaviour:
#   0 — all header iterations include hash==0 guard (or are allowlisted)
#   1 — one or more non-allowlisted iterations lack the guard

set -euo pipefail

SRC_DIR="components/nginx-module/src"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "${SCRIPT_DIR}/collect_files.sh"

# ── Allowlist ──
#
# Format: "filename:justification"
# Each entry exempts a file from violation reporting. The filename is
# matched as a substring of the full path. The justification explains
# why the file is safe without the hash==0 guard.
readonly ALLOWLIST=(
    # Generic find-by-name helper; callers filter results and the function
    # returns the first match including invalidated entries only when the
    # caller's key comparison naturally excludes hash==0 entries via
    # key.len/key.data checks. Tracked for future hardening (Rule 40).
    "ngx_http_markdown_headers_impl.h:generic header lookup; key.len/data checks implicitly skip most invalidated entries; tracked for Rule 40 hardening"
    # Scans Cache-Control headers in headers_out; the is_cache_control_header
    # helper checks key.len and key.data which implicitly skips invalidated
    # entries whose key.len is typically zeroed. Tracked for Rule 40.
    "ngx_http_markdown_auth.c:Cache-Control scan; key.len check in helper implicitly skips invalidated entries; tracked for Rule 40 hardening"
)

if [[ ! -d "$SRC_DIR" ]]; then
    echo "  [header-hash] Source directory not found: $SRC_DIR" >&2
    exit 2
fi

# ── Scanning ──
#
# Find files that iterate over header list parts (part->nelts or part.nelts)
# and check if they also contain hash == 0 filtering nearby.

# Collect violation files into an array (safe empty expansion under set -u)
VIOLATION_FILES=()
VIOLATION_COUNT=0
file_list="$(mktemp "${TMPDIR:-/tmp}/header-hash-files.XXXXXX")" || {
    echo "  [header-hash] Cannot create the source file list" >&2
    exit 2
}
trap 'rm -f "$file_list"' EXIT
if ! harness_collect_find0 "$file_list" "$SRC_DIR" \( -name '*.c' -o -name '*.h' \) -type f 2>/dev/null; then
    echo "  [header-hash] Cannot enumerate source files in $SRC_DIR" >&2
    exit 2
fi

while IFS= read -r -d '' file; do
    # Check if this file has header iteration patterns combined with header types
    # and lacks hash == 0 filtering
    if grep -q 'part->nelts\|part\.nelts' "$file" 2>/dev/null \
        && grep -q 'headers\|ngx_table_elt_t' "$file" 2>/dev/null \
        && ! grep -q 'hash == 0\|hash==0\|\.hash[[:space:]]*==[[:space:]]*0\|->hash[[:space:]]*==[[:space:]]*0' "$file" 2>/dev/null; then
        VIOLATION_FILES+=("$file")
        VIOLATION_COUNT=$((VIOLATION_COUNT + 1))
    fi
done < "$file_list"

# ── Filter against allowlist ──

REAL_VIOLATIONS=0
ALLOWLISTED_COUNT=0

for file in ${VIOLATION_FILES[@]+"${VIOLATION_FILES[@]}"}; do
    allowed=0
    for entry in ${ALLOWLIST[@]+"${ALLOWLIST[@]}"}; do
        allowfile="${entry%%:*}"
        if [[ "$file" == *"$allowfile"* ]]; then
            allowed=1
            break
        fi
    done
    if [[ "$allowed" -eq 0 ]]; then
        echo "  [header-hash] VIOLATION: Header iteration without hash==0 filter: $file" >&2
        REAL_VIOLATIONS=$((REAL_VIOLATIONS + 1))
    else
        echo "  [header-hash] ALLOWLISTED: $file" >&2
        ALLOWLISTED_COUNT=$((ALLOWLISTED_COUNT + 1))
    fi
done

# ── Verdict ──

if [[ "$REAL_VIOLATIONS" -gt 0 ]]; then
    echo "  [header-hash] FAIL: $REAL_VIOLATIONS non-allowlisted violation(s) found" >&2
    exit 1
fi

if [[ "$VIOLATION_COUNT" -gt 0 ]]; then
    echo "  [header-hash] PASS: all findings are allowlisted ($ALLOWLISTED_COUNT file(s))" >&2
else
    echo "  [header-hash] PASS: all header iteration files include hash==0 filtering" >&2
fi

exit 0
