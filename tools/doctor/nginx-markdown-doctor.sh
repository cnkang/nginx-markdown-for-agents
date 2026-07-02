#!/usr/bin/env bash
# nginx-markdown-doctor — installation diagnostic tool
# Usage: nginx-markdown-doctor [--json] [--module-path PATH] [--nginx-bin PATH]
#
# Performs basic installation health checks for nginx-markdown-for-agents.
# Exit codes: 0 = all pass, 1 = at least one failure, 2 = usage error
#
# 0.9.0 minimal scope: nginx version, module .so existence, config syntax.
# 0.9.1 extended: configure args, module signature, Rust linkage, OS/arch/libc,
#   package type, artifact recommendation, remediation hints.
# See docs/guides/doctor.md for full documentation.

set -euo pipefail

##############################################################################
# Constants
##############################################################################

readonly DOCTOR_VERSION="0.9.1"
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

# Shared state across checks
FOUND_MODULE_PATH=""
DETECTED_OS=""
DETECTED_ARCH=""
DETECTED_LIBC=""
DETECTED_LIBC_VERSION=""
DETECTED_PKG_TYPE=""
RECOMMENDATION_JSON=""

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
# Arguments: name status message [details_json] [hint]
emit_check() {
    local name="$1" status="$2" message="$3"
    local details="${4:-}"
    local hint="${5:-}"

    TOTAL=$((TOTAL + 1))
    case "$status" in
        pass) PASSED=$((PASSED + 1)) ;;
        fail) FAILED=$((FAILED + 1)) ;;
        warn) WARNINGS=$((WARNINGS + 1)) ;;
        skip) SKIPPED=$((SKIPPED + 1)) ;;
        *) msg "BUG: unknown status '$status'"; return 1 ;;
    esac

    # Build JSON entry
    local json_entry escaped_name escaped_status escaped_message
    escaped_name=$(json_escape "$name")
    escaped_status=$(json_escape "$status")
    escaped_message=$(json_escape "$message")
    if [[ -n "$details" && -n "$hint" ]]; then
        json_entry=$(printf '{"name":"%s","status":"%s","message":"%s","details":%s,"hint":"%s"}' \
            "$escaped_name" "$escaped_status" "$escaped_message" "$details" "$(json_escape "$hint")")
    elif [[ -n "$details" ]]; then
        json_entry=$(printf '{"name":"%s","status":"%s","message":"%s","details":%s}' \
            "$escaped_name" "$escaped_status" "$escaped_message" "$details")
    elif [[ -n "$hint" ]]; then
        json_entry=$(printf '{"name":"%s","status":"%s","message":"%s","hint":"%s"}' \
            "$escaped_name" "$escaped_status" "$escaped_message" "$(json_escape "$hint")")
    else
        json_entry=$(printf '{"name":"%s","status":"%s","message":"%s"}' \
            "$escaped_name" "$escaped_status" "$escaped_message")
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
    if [[ -n "$hint" ]]; then
        human_line="${human_line}
      → ${hint}"
    fi
    if [[ -z "$CHECKS_HUMAN" ]]; then
        CHECKS_HUMAN="$human_line"
    else
        CHECKS_HUMAN="${CHECKS_HUMAN}
${human_line}"
    fi
}

# JSON-escape a string value.
json_escape() {
    local s="$1"
    local out="" ch ord i
    local old_lc_all="${LC_ALL-__unset__}"

    LC_ALL=C
    for ((i = 0; i < ${#s}; i++)); do
        ch="${s:i:1}"
        case "$ch" in
            "\\") out="${out}\\\\"
                ;;
            '"') out="${out}\\\""
                ;;
            $'\b') out="${out}\\b"
                ;;
            $'\f') out="${out}\\f"
                ;;
            $'\n') out="${out}\\n"
                ;;
            $'\r') out="${out}\\r"
                ;;
            $'\t') out="${out}\\t"
                ;;
            *)
                ord=$(printf '%d' "'$ch")
                if [[ "$ord" -lt 32 ]]; then
                    out="${out}$(printf '\\u%04x' "$ord")"
                else
                    out="${out}${ch}"
                fi
                ;;
        esac
    done

    if [[ "$old_lc_all" == "__unset__" ]]; then
        unset LC_ALL
    else
        LC_ALL="$old_lc_all"
    fi
    printf '%s' "$out"
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
        FOUND_MODULE_PATH="$found_path"
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
            '{"searched":"'"$(json_escape "$searched")"'","filename":"'"$MODULE_FILENAME"'"}' \
            "Download the module from GitHub Releases or install via package"
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
    if ! cat > "$tmp_conf" <<'CONF'
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
    then
        rm -f "$tmp_conf"
        emit_check "config_valid" "fail" "could not write temp config file"
        return
    fi

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
            '{"exit_code":'"$test_rc"',"output":"'"$(json_escape "$test_output")"'"}' \
            "Check nginx error log for details or run nginx -t manually"
    fi
}

##############################################################################
# Extended Checks (0.9.1)
##############################################################################

# Check 4: configure arguments (--with-compat)
check_configure_args() {
    local nginx_bin
    if [[ "$NGINX_BIN" == "__unset__" ]]; then
        nginx_bin="nginx"
    else
        nginx_bin="$NGINX_BIN"
    fi

    if [[ -z "$nginx_bin" ]]; then
        emit_check "configure_args" "skip" "nginx binary not specified (--nginx-bin empty)"
        return
    fi

    if ! command -v "$nginx_bin" >/dev/null 2>&1; then
        emit_check "configure_args" "skip" "nginx binary not found in PATH"
        return
    fi

    # nginx -V outputs to stderr
    local v_output
    v_output=$("$nginx_bin" -V 2>&1) || true

    # Extract configure arguments line
    local configure_line=""
    configure_line=$(printf '%s\n' "$v_output" | grep -i "configure arguments:" || true)

    if [[ -z "$configure_line" ]]; then
        emit_check "configure_args" "skip" "could not extract configure arguments" \
            '{"raw_output":"'"$(json_escape "$v_output")"'"}'
        return
    fi

    local has_compat=0
    if printf '%s' "$configure_line" | grep -q -- '--with-compat'; then
        has_compat=1
    fi

    local escaped_line
    escaped_line=$(json_escape "$configure_line")

    if [[ $has_compat -eq 1 ]]; then
        emit_check "configure_args" "pass" "nginx built with --with-compat" \
            '{"configure_line":"'"$escaped_line"'","with_compat":true}'
    else
        emit_check "configure_args" "warn" "nginx not built with --with-compat (required for dynamic modules)" \
            '{"configure_line":"'"$escaped_line"'","with_compat":false}' \
            "Rebuild nginx with --with-compat or use the official nginx.org packages"
    fi
}

# Check 5: dynamic module signature
check_module_signature() {
    if [[ -z "$FOUND_MODULE_PATH" ]]; then
        emit_check "module_signature" "skip" "module .so not found, cannot check signature"
        return
    fi

    # Check if nm is available
    if ! command -v nm >/dev/null 2>&1; then
        emit_check "module_signature" "skip" "nm not available (install binutils)" \
            '' \
            "Install binutils to enable module signature verification"
        return
    fi

    # Look for the module symbol
    local nm_output
    nm_output=$(nm -D "$FOUND_MODULE_PATH" 2>/dev/null || nm "$FOUND_MODULE_PATH" 2>/dev/null || true)

    if [[ -z "$nm_output" ]]; then
        emit_check "module_signature" "skip" "could not read symbols from module"
        return
    fi

    if printf '%s\n' "$nm_output" | grep -q "ngx_http_markdown_filter_module"; then
        emit_check "module_signature" "pass" "ngx_http_markdown_filter_module symbol found" \
            '{"symbol":"ngx_http_markdown_filter_module","found":true}'
    else
        emit_check "module_signature" "fail" "ngx_http_markdown_filter_module symbol not found" \
            '{"symbol":"ngx_http_markdown_filter_module","found":false}' \
            "The module file may be corrupt or built for a different nginx version"
    fi
}

# Check 6: Rust converter linkage
check_rust_linkage() {
    if [[ -z "$FOUND_MODULE_PATH" ]]; then
        emit_check "rust_linkage" "skip" "module .so not found, cannot check Rust linkage"
        return
    fi

    # Check if nm is available
    if ! command -v nm >/dev/null 2>&1; then
        emit_check "rust_linkage" "skip" "nm not available (install binutils)"
        return
    fi

    local nm_output
    nm_output=$(nm -D "$FOUND_MODULE_PATH" 2>/dev/null || nm "$FOUND_MODULE_PATH" 2>/dev/null || true)

    if [[ -z "$nm_output" ]]; then
        emit_check "rust_linkage" "skip" "could not read symbols from module"
        return
    fi

    # Look for known Rust FFI exports
    local rust_symbols=""
    local found_count=0

    local sym
    for sym in markdown_convert markdown_converter_new markdown_converter_free markdown_result_free markdown_negotiate_accept markdown_decide_eligibility; do
        if printf '%s\n' "$nm_output" | grep -qw "$sym"; then
            found_count=$((found_count + 1))
            if [[ -z "$rust_symbols" ]]; then
                rust_symbols="\"$sym\""
            else
                rust_symbols="${rust_symbols},\"$sym\""
            fi
        fi
    done

    if [[ $found_count -gt 0 ]]; then
        emit_check "rust_linkage" "pass" "Rust FFI symbols found (${found_count} exports)" \
            '{"found_count":'"$found_count"',"symbols":['"$rust_symbols"']}'
    else
        emit_check "rust_linkage" "warn" "no Rust FFI symbols found in module" \
            '{"found_count":0}' \
            "Module may be missing Rust converter linkage or built without FFI exports"
    fi
}

# Check 7: OS/arch/libc detection
check_os_arch() {
    # Detect OS
    local os_name
    os_name=$(uname -s 2>/dev/null || printf 'unknown')

    case "$os_name" in
        Linux)  DETECTED_OS="linux" ;;
        Darwin) DETECTED_OS="macos" ;;
        FreeBSD) DETECTED_OS="freebsd" ;;
        *)      DETECTED_OS="unknown" ;;
    esac

    # Detect architecture
    local arch
    arch=$(uname -m 2>/dev/null || printf 'unknown')

    case "$arch" in
        x86_64|amd64)   DETECTED_ARCH="x86_64" ;;
        aarch64|arm64)  DETECTED_ARCH="aarch64" ;;
        armv7l)         DETECTED_ARCH="armv7" ;;
        *)              DETECTED_ARCH="$arch" ;;
    esac

    # Detect libc (Linux only)
    DETECTED_LIBC=""
    DETECTED_LIBC_VERSION=""
    if [[ "$DETECTED_OS" == "linux" ]]; then
        # Try ldd --version first
        local ldd_output
        ldd_output=$(ldd --version 2>&1 || true)
        if printf '%s\n' "$ldd_output" | grep -qi "glibc\|gnu libc\|GLIBC"; then
            DETECTED_LIBC="glibc"
            local ver
            ver=$(printf '%s\n' "$ldd_output" | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)
            if [[ -n "$ver" ]]; then
                DETECTED_LIBC_VERSION="$ver"
            fi
        elif printf '%s\n' "$ldd_output" | grep -qi "musl"; then
            DETECTED_LIBC="musl"
            local ver
            ver=$(printf '%s\n' "$ldd_output" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
            if [[ -n "$ver" ]]; then
                DETECTED_LIBC_VERSION="$ver"
            fi
        else
            # Fallback: check for musl via ldd binary itself
            if command -v ldd >/dev/null 2>&1 && file "$(command -v ldd)" 2>/dev/null | grep -qi "musl"; then
                DETECTED_LIBC="musl"
            elif [[ -f /lib/libc.so.6 ]]; then
                DETECTED_LIBC="glibc"
                local lib_output
                lib_output=$(/lib/libc.so.6 2>&1 || true)
                local ver
                ver=$(printf '%s\n' "$lib_output" | grep -oE 'version [0-9]+\.[0-9]+' | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)
                if [[ -n "$ver" ]]; then
                    DETECTED_LIBC_VERSION="$ver"
                fi
            fi
        fi
    fi

    # Build details
    local details_libc=""
    if [[ -n "$DETECTED_LIBC" ]]; then
        details_libc='"libc":"'"$DETECTED_LIBC"'"'
        if [[ -n "$DETECTED_LIBC_VERSION" ]]; then
            details_libc="${details_libc}"',"libc_version":"'"$DETECTED_LIBC_VERSION"'"'
        fi
    fi

    local details_json
    if [[ -n "$details_libc" ]]; then
        details_json='{"os":"'"$DETECTED_OS"'","arch":"'"$DETECTED_ARCH"'",'"$details_libc"'}'
    else
        details_json='{"os":"'"$DETECTED_OS"'","arch":"'"$DETECTED_ARCH"'"}'
    fi

    emit_check "os_arch" "pass" "${DETECTED_OS}/${DETECTED_ARCH}${DETECTED_LIBC:+ (${DETECTED_LIBC}${DETECTED_LIBC_VERSION:+ ${DETECTED_LIBC_VERSION}})}" \
        "$details_json"
}

# Check 8: package type detection
check_package_type() {
    DETECTED_PKG_TYPE=""
    local pkg_version=""

    # Check dpkg (Debian/Ubuntu)
    if command -v dpkg >/dev/null 2>&1; then
        local dpkg_out
        dpkg_out=$(dpkg -s nginx 2>/dev/null || true)
        if printf '%s\n' "$dpkg_out" | grep -q "^Status:.*installed"; then
            DETECTED_PKG_TYPE="apt"
            pkg_version=$(printf '%s\n' "$dpkg_out" | grep "^Version:" | sed 's/^Version: *//' || true)
        fi
    fi

    # Check rpm (CentOS/RHEL/Fedora)
    if [[ -z "$DETECTED_PKG_TYPE" ]] && command -v rpm >/dev/null 2>&1; then
        local rpm_out
        rpm_out=$(rpm -q nginx 2>/dev/null || true)
        if [[ "$rpm_out" != *"not installed"* && "$rpm_out" != *"package nginx is not"* ]]; then
            DETECTED_PKG_TYPE="yum"
            pkg_version="$rpm_out"
        fi
    fi

    # Check homebrew (macOS)
    if [[ -z "$DETECTED_PKG_TYPE" ]] && command -v brew >/dev/null 2>&1; then
        local brew_out
        brew_out=$(brew list --versions nginx 2>/dev/null || true)
        if [[ -n "$brew_out" ]]; then
            DETECTED_PKG_TYPE="homebrew"
            pkg_version=$(printf '%s' "$brew_out" | sed 's/^nginx *//' || true)
        fi
    fi

    # Check if running in Docker
    if [[ -z "$DETECTED_PKG_TYPE" ]]; then
        if [[ -f /.dockerenv ]] || grep -q "docker\|containerd" /proc/1/cgroup 2>/dev/null; then
            DETECTED_PKG_TYPE="docker"
        fi
    fi

    # Check if built from source (nginx -V shows --prefix but no package)
    if [[ -z "$DETECTED_PKG_TYPE" ]]; then
        local nginx_bin
        if [[ "$NGINX_BIN" == "__unset__" ]]; then
            nginx_bin="nginx"
        else
            nginx_bin="$NGINX_BIN"
        fi
        if [[ -n "$nginx_bin" ]] && command -v "$nginx_bin" >/dev/null 2>&1; then
            local v_out
            v_out=$("$nginx_bin" -V 2>&1 || true)
            if printf '%s\n' "$v_out" | grep -q "configure arguments:"; then
                DETECTED_PKG_TYPE="source"
            fi
        fi
    fi

    if [[ -n "$DETECTED_PKG_TYPE" ]]; then
        local details_json
        if [[ -n "$pkg_version" ]]; then
            details_json='{"type":"'"$DETECTED_PKG_TYPE"'","version":"'"$(json_escape "$pkg_version")"'"}'
        else
            details_json='{"type":"'"$DETECTED_PKG_TYPE"'"}'
        fi
        emit_check "package_type" "pass" "nginx installed via ${DETECTED_PKG_TYPE}${pkg_version:+ (${pkg_version})}" \
            "$details_json"
    else
        emit_check "package_type" "skip" "could not detect nginx installation method" \
            '{"type":"unknown"}'
    fi
}

# Recommendation: suggest release artifact based on detected environment
recommend_artifact() {
    # Only recommend if we have OS and arch info
    if [[ -z "$DETECTED_OS" || -z "$DETECTED_ARCH" ]]; then
        return
    fi

    local artifact_name=""
    local artifact_suffix=""

    case "$DETECTED_OS" in
        linux)
            artifact_suffix="linux-${DETECTED_ARCH}"
            if [[ "$DETECTED_LIBC" == "musl" ]]; then
                artifact_suffix="${artifact_suffix}-musl"
            elif [[ -n "$DETECTED_LIBC_VERSION" ]]; then
                artifact_suffix="${artifact_suffix}-glibc${DETECTED_LIBC_VERSION}"
            fi
            ;;
        macos)
            artifact_suffix="macos-${DETECTED_ARCH}"
            ;;
        freebsd)
            artifact_suffix="freebsd-${DETECTED_ARCH}"
            ;;
        *)
            return
            ;;
    esac

    artifact_name="nginx-markdown-module-${artifact_suffix}.tar.gz"
    RECOMMENDATION_JSON='{"artifact":"'"$(json_escape "$artifact_name")"'","os":"'"$DETECTED_OS"'","arch":"'"$DETECTED_ARCH"'"'
    if [[ -n "$DETECTED_LIBC" ]]; then
        RECOMMENDATION_JSON="${RECOMMENDATION_JSON}"',"libc":"'"$DETECTED_LIBC"'"'
    fi
    if [[ -n "$DETECTED_LIBC_VERSION" ]]; then
        RECOMMENDATION_JSON="${RECOMMENDATION_JSON}"',"libc_version":"'"$DETECTED_LIBC_VERSION"'"'
    fi
    RECOMMENDATION_JSON="${RECOMMENDATION_JSON}"'}'
}

##############################################################################
# Output
##############################################################################

output_json() {
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%S+00:00")

    local rec_field=""
    if [[ -n "$RECOMMENDATION_JSON" ]]; then
        rec_field=',"recommendation":'"$RECOMMENDATION_JSON"
    fi

    printf '{"schema_version":%d,"tool_version":"%s","timestamp":"%s","checks":[%s],"summary":{"total":%d,"passed":%d,"failed":%d,"warnings":%d,"skipped":%d}%s}\n' \
        "$SCHEMA_VERSION" "$DOCTOR_VERSION" "$timestamp" \
        "$CHECKS_JSON" \
        "$TOTAL" "$PASSED" "$FAILED" "$WARNINGS" "$SKIPPED" \
        "$rec_field"
}

output_human() {
    printf '%s\n' "nginx-markdown-doctor v${DOCTOR_VERSION}" >&2
    printf '%s\n' "─────────────────────────────────" >&2
    # Checks output goes to stdout for consistency
    printf '%s\n' "$CHECKS_HUMAN"
    if [[ -n "$RECOMMENDATION_JSON" ]]; then
        # Extract artifact name from JSON for human display
        local artifact
        artifact=$(printf '%s' "$RECOMMENDATION_JSON" | grep -oE '"artifact":"[^"]*"' | sed 's/"artifact":"//;s/"$//' || true)
        if [[ -n "$artifact" ]]; then
            printf '\n%s\n' "  Recommended artifact: ${artifact}"
        fi
    fi
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
    check_configure_args
    check_module_signature
    check_rust_linkage
    check_os_arch
    check_package_type
    recommend_artifact

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
