#!/usr/bin/env bash
# nginx-markdown-doctor — installation diagnostic tool
# Usage: nginx-markdown-doctor [--json] [--module-path PATH] [--nginx-bin PATH]
#
# Performs basic installation health checks for nginx-markdown-for-agents.
# Exit codes: 0 = all pass, 1 = at least one failure, 2 = usage error
#
# 0.9.0 minimal scope: nginx version, module .so existence, config syntax.
# See docs/guides/doctor.md for full documentation.

set -euo pipefail

##############################################################################
# Constants
##############################################################################

readonly DOCTOR_VERSION="0.9.0"
readonly SCHEMA_VERSION=1
readonly MODULE_FILENAME="ngx_http_markdown_filter_module.so"

# Default module search paths (common locations)
readonly -a DEFAULT_MODULE_PATHS=(
    "/usr/lib/nginx/modules"
    "/usr/lib64/nginx/modules"
    "/usr/local/lib/nginx/modules"
    "/opt/homebrew/lib/nginx/modules"
    "/usr/local/libexec/nginx"
)

##############################################################################
# Globals
##############################################################################

OUTPUT_JSON=0
MODULE_PATH=""
NGINX_BIN="__unset__"

# Result accumulators
CHECKS_JSON=""
CHECKS_HUMAN=""
TOTAL=0
PASSED=0
FAILED=0
WARNINGS=0
SKIPPED=0

##############################################################################
# Helpers
##############################################################################

usage() {
    cat >&2 <<'EOF'
Usage: nginx-markdown-doctor [OPTIONS]

Options:
  --json              Output JSON instead of human-readable text
  --module-path PATH  Explicit path to directory containing the module .so
  --nginx-bin PATH    Explicit path to nginx binary (empty string to skip)
  -h, --help          Show this help message

Exit codes:
  0  All checks passed
  1  At least one check failed
  2  Usage error
EOF
}

# Print a message to stderr
msg() {
    printf '%s\n' "$*" >&2
}

# Emit a check result.
# Arguments: name status message [details_json]
emit_check() {
    local name="$1" status="$2" message="$3"
    local details="${4:-}"

    TOTAL=$((TOTAL + 1))
    case "$status" in
        pass) PASSED=$((PASSED + 1)) ;;
        fail) FAILED=$((FAILED + 1)) ;;
        warn) WARNINGS=$((WARNINGS + 1)) ;;
        skip) SKIPPED=$((SKIPPED + 1)) ;;
        *) msg "BUG: unknown status '$status'"; return 1 ;;
    esac

    # Build JSON entry
    local json_entry
    if [[ -n "$details" ]]; then
        json_entry=$(printf '{"name":"%s","status":"%s","message":"%s","details":%s}' \
            "$name" "$status" "$message" "$details")
    else
        json_entry=$(printf '{"name":"%s","status":"%s","message":"%s"}' \
            "$name" "$status" "$message")
    fi

    if [[ -z "$CHECKS_JSON" ]]; then
        CHECKS_JSON="$json_entry"
    else
        CHECKS_JSON="${CHECKS_JSON},${json_entry}"
    fi

    # Build human-readable entry
    local symbol
    case "$status" in
        pass) symbol="✓" ;;
        fail) symbol="✗" ;;
        warn) symbol="⚠" ;;
        skip) symbol="○" ;;
        *)    symbol="?" ;;
    esac
    local human_line="  ${symbol} [${status}] ${name}: ${message}"
    if [[ -z "$CHECKS_HUMAN" ]]; then
        CHECKS_HUMAN="$human_line"
    else
        CHECKS_HUMAN="${CHECKS_HUMAN}
${human_line}"
    fi
}

# JSON-escape a string value (minimal: escape backslash, double-quote, newlines)
json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/}"
    printf '%s' "$s"
}

##############################################################################
# Checks
##############################################################################

# Check 1: nginx version detection
check_nginx_version() {
    local nginx_bin
    if [[ "$NGINX_BIN" == "__unset__" ]]; then
        nginx_bin="nginx"
    else
        nginx_bin="$NGINX_BIN"
    fi

    # If nginx_bin is empty string, skip
    if [[ -z "$nginx_bin" ]]; then
        emit_check "nginx_version" "skip" "nginx binary not specified (--nginx-bin empty)"
        return
    fi

    # Check if binary exists / is executable
    if ! command -v "$nginx_bin" >/dev/null 2>&1; then
        emit_check "nginx_version" "skip" "nginx binary not found in PATH" \
            '{"binary":"'"$(json_escape "$nginx_bin")"'"}'
        return
    fi

    # Run nginx -v (version goes to stderr)
    local version_output
    version_output=$("$nginx_bin" -v 2>&1) || true

    # Parse version: "nginx version: nginx/1.28.0" or similar
    local version=""
    if [[ "$version_output" =~ nginx/([0-9]+\.[0-9]+\.[0-9]+) ]]; then
        version="${BASH_REMATCH[1]}"
    fi

    if [[ -z "$version" ]]; then
        emit_check "nginx_version" "fail" "could not parse nginx version" \
            '{"raw_output":"'"$(json_escape "$version_output")"'"}'
        return
    fi

    emit_check "nginx_version" "pass" "nginx version ${version} detected" \
        '{"version":"'"$version"'"}'
}

# Check 2: module .so file existence
check_module_exists() {
    local search_path="$MODULE_PATH"
    local found_path=""

    if [[ -n "$search_path" ]]; then
        # Explicit path provided
        local candidate="${search_path}/${MODULE_FILENAME}"
        if [[ -f "$candidate" ]]; then
            found_path="$candidate"
        fi
    else
        # Search default paths
        local p
        for p in "${DEFAULT_MODULE_PATHS[@]}"; do
            local candidate="${p}/${MODULE_FILENAME}"
            if [[ -f "$candidate" ]]; then
                found_path="$candidate"
                break
            fi
        done
    fi

    if [[ -n "$found_path" ]]; then
        emit_check "module_exists" "pass" "module found at ${found_path}" \
            '{"path":"'"$(json_escape "$found_path")"'"}'
    else
        local searched
        if [[ -n "$search_path" ]]; then
            searched="$search_path"
        else
            searched="default paths"
        fi
        emit_check "module_exists" "warn" "module ${MODULE_FILENAME} not found (searched: ${searched})" \
            '{"searched":"'"$(json_escape "$searched")"'","filename":"'"$MODULE_FILENAME"'"}'
    fi
}

# Check 3: basic config syntax validation
check_config_valid() {
    local nginx_bin
    if [[ "$NGINX_BIN" == "__unset__" ]]; then
        nginx_bin="nginx"
    else
        nginx_bin="$NGINX_BIN"
    fi

    # If nginx_bin is empty string, skip
    if [[ -z "$nginx_bin" ]]; then
        emit_check "config_valid" "skip" "nginx binary not specified (--nginx-bin empty)"
        return
    fi

    # Check if binary exists / is executable
    if ! command -v "$nginx_bin" >/dev/null 2>&1; then
        emit_check "config_valid" "skip" "nginx binary not found in PATH"
        return
    fi

    # Create a minimal test config
    local tmp_conf
    tmp_conf=$(mktemp "${TMPDIR:-/tmp}/doctor-nginx-XXXXXX.conf") || {
        emit_check "config_valid" "fail" "could not create temp config file"
        return
    }

    # Write minimal config that tests basic module loading
    cat > "$tmp_conf" <<'CONF'
daemon off;
worker_processes 1;
events { worker_connections 64; }
http {
    server {
        listen 127.0.0.1:19999;
        location / { return 200 "ok"; }
    }
}
CONF

    # Run nginx -t with the minimal config
    local test_output
    local test_rc=0
    test_output=$("$nginx_bin" -t -c "$tmp_conf" 2>&1) || test_rc=$?

    # Clean up temp file
    rm -f "$tmp_conf"

    if [[ $test_rc -eq 0 ]]; then
        emit_check "config_valid" "pass" "nginx config syntax OK (minimal test config)"
    else
        emit_check "config_valid" "fail" "nginx -t failed (exit ${test_rc})" \
            '{"exit_code":'"$test_rc"',"output":"'"$(json_escape "$test_output")"'"}'
    fi
}

##############################################################################
# Output
##############################################################################

output_json() {
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%S+00:00")

    printf '{"schema_version":%d,"tool_version":"%s","timestamp":"%s","checks":[%s],"summary":{"total":%d,"passed":%d,"failed":%d,"warnings":%d,"skipped":%d}}\n' \
        "$SCHEMA_VERSION" "$DOCTOR_VERSION" "$timestamp" \
        "$CHECKS_JSON" \
        "$TOTAL" "$PASSED" "$FAILED" "$WARNINGS" "$SKIPPED"
}

output_human() {
    printf '%s\n' "nginx-markdown-doctor v${DOCTOR_VERSION}" >&2
    printf '%s\n' "─────────────────────────────────" >&2
    # Checks output goes to stdout for consistency
    printf '%s\n' "$CHECKS_HUMAN"
    printf '%s\n' "─────────────────────────────────" >&2
    printf '%s\n' "Summary: ${PASSED} passed, ${FAILED} failed, ${WARNINGS} warnings, ${SKIPPED} skipped (${TOTAL} total)" >&2
}

##############################################################################
# Main
##############################################################################

main() {
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --json)
                OUTPUT_JSON=1
                shift
                ;;
            --module-path)
                if [[ $# -lt 2 ]]; then
                    msg "error: --module-path requires a value"
                    usage
                    exit 2
                fi
                MODULE_PATH="$2"
                shift 2
                ;;
            --nginx-bin)
                if [[ $# -lt 2 ]]; then
                    msg "error: --nginx-bin requires a value"
                    usage
                    exit 2
                fi
                NGINX_BIN="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                msg "error: unknown option '$1'"
                usage
                exit 2
                ;;
        esac
    done

    # Run checks
    check_nginx_version
    check_module_exists
    check_config_valid

    # Output results
    if [[ $OUTPUT_JSON -eq 1 ]]; then
        output_json
    else
        output_human
    fi

    # Exit code
    if [[ $FAILED -gt 0 ]]; then
        exit 1
    fi
    exit 0
}

main "$@"
