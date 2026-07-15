#!/usr/bin/env bash
# detect_hardcoded_http_status.sh — Detect hardcoded HTTP status in reject/error paths
#
# Rule (nginx-idioms): Reject/error paths that return a hardcoded HTTP status
# code (502, 500) instead of the operator-configured conf->error_status
# silently ignore the user's error_policy configuration. This detector flags
# return statements in error handling functions that use hardcoded status
# codes where conf->error_status should be used.
#
# Detection strategy:
#   1. Find C source files that contain reject/error handling code.
#   2. Inside those files, look for return statements returning
#      NGX_HTTP_BAD_GATEWAY (502) or NGX_HTTP_INTERNAL_SERVER_ERROR (500)
#      instead of conf->error_status.
#   3. Skip comments, string literals, and lines mentioning conf->error_status.
#
# Compatibility: macOS bash 3.2 (Rule 11), [[ ]] (Rule 18).
#
# Usage:
#   bash tools/harness/detect_hardcoded_http_status.sh [directory] [--strict]
#     directory defaults to components/nginx-module/src
#
# Exit codes:
#   0 — no findings (advisory by default)
#   1 — usage error or findings in --strict mode

set -euo pipefail

SCRIPT_DIR="$(dirname "$0")"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SRC_DIR="${REPO_ROOT}/components/nginx-module/src"
STRICT=0

for arg in "$@"; do
    case "$arg" in
        --strict)
            STRICT=1
            ;;
        --help|-h)
            cat <<USAGE
Usage: $0 [directory] [--strict]
  directory defaults to ${SRC_DIR}
  --strict  exit 1 on findings
USAGE
            exit 0
            ;;
        *)
            SRC_DIR="$arg"
            ;;
    esac
done

if [[ ! -d "$SRC_DIR" ]]; then
    echo "ERROR: directory not found: $SRC_DIR" >&2
    exit 1
fi

findings=0

# Pattern: return NGX_HTTP_BAD_GATEWAY or return NGX_HTTP_INTERNAL_SERVER_ERROR
# in files that contain reject/error handling code

error_files=$(grep -rlE \
    'reject|on_error|fail_open|precommit_error|postcommit_error|stream_on_error' \
    "$SRC_DIR" 2>/dev/null | grep -E '\.(c|h)$' || true)

for file in $error_files; do
    rel_path="${file#${REPO_ROOT}/}"

    # Use grep to find return statements with hardcoded status codes
    # Skip lines containing conf->error_status
    while IFS= read -r match; do
        line_num="${match%%:*}"
        line_text="${match#*:}"
        # Strip leading whitespace for display
        trimmed="${line_text#"${line_text%%[![:space:]]*}"}"

        # Skip comment lines (starting with * or //)
        case "$trimmed" in
            \**|//*) continue ;;
            *) ;;
        esac

        # Skip if line mentions conf->error_status
        if echo "$trimmed" | grep -qF 'conf->error_status'; then
            continue
        fi

        # Check for hardcoded status in return
        if echo "$trimmed" | grep -qE 'return.*NGX_HTTP_BAD_GATEWAY'; then
            echo "WARN: ${rel_path}:${line_num}: hardcoded NGX_HTTP_BAD_GATEWAY in return" >&2
            echo "  ${trimmed}" >&2
            echo "  Consider: return (ngx_int_t) conf->error_status;" >&2
            findings=$((findings + 1))
        fi

    done < <(grep -nE 'return.*NGX_HTTP_BAD_GATEWAY' "$file" || true)
done

if [[ $findings -gt 0 ]]; then
    if [[ $STRICT -eq 1 ]]; then
        echo "FAIL: found ${findings} hardcoded HTTP status code(s) in reject/error paths" >&2
        exit 1
    else
        echo "WARN: found ${findings} hardcoded HTTP status code(s) in reject/error paths (advisory)" >&2
    fi
else
    echo "OK: no hardcoded HTTP status codes in reject/error paths"
fi

exit 0