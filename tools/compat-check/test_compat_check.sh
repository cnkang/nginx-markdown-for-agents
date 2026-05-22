#!/bin/bash
# ---------------------------------------------------------------------------
# test_compat_check.sh — Unit tests for nginx-markdown-compat-check.sh
#
# PURPOSE:
#   Validates the compat-check helper produces correct output and exit codes
#   for various NGINX environments by mocking nginx -v / nginx -V / uname.
#
# USAGE:
#   bash tools/compat-check/test_compat_check.sh
#
# EXIT CODES:
#   0  All tests passed
#   1  One or more tests failed
#
# NOTES:
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - Uses mock binaries via PATH manipulation
#   - AGENTS.md Rule 14: new helper must have corresponding tests
# ---------------------------------------------------------------------------

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HELPER_SCRIPT="$SCRIPT_DIR/nginx-markdown-compat-check.sh"

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Temp directory for mock binaries
MOCK_DIR=""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

setup_mock_dir() {
    MOCK_DIR="$(mktemp -d)"
}

cleanup_mock_dir() {
    if [ -n "$MOCK_DIR" ] && [ -d "$MOCK_DIR" ]; then
        rm -rf "$MOCK_DIR"
    fi
    MOCK_DIR=""
}

# create_mock_nginx — create a mock nginx binary
# Arguments:
#   $1 = version string for nginx -v (e.g. "nginx version: nginx/1.26.3")
#   $2 = config output for nginx -V (multi-line)
create_mock_nginx() {
    local version_line="$1"
    local config_output="$2"

    cat > "$MOCK_DIR/nginx" <<MOCKEOF
#!/bin/bash
case "\$1" in
    -v)
        printf '%s\n' "$version_line" >&2
        ;;
    -V)
        printf '%s\n' "$config_output" >&2
        ;;
    *)
        printf '%s\n' "$version_line" >&2
        ;;
esac
exit 0
MOCKEOF
    chmod +x "$MOCK_DIR/nginx"
}

# create_mock_uname — create a mock uname binary
# Arguments:
#   $1 = architecture to report for uname -m
create_mock_uname() {
    local arch="$1"

    cat > "$MOCK_DIR/uname" <<MOCKEOF
#!/bin/bash
case "\$1" in
    -m)
        printf '%s\n' "$arch"
        ;;
    *)
        # Fall through to real uname for other flags
        /usr/bin/uname "\$@"
        ;;
esac
exit 0
MOCKEOF
    chmod +x "$MOCK_DIR/uname"
}

# run_helper — run the compat-check helper with mocked PATH
# Arguments: any extra args to pass to the helper
# Sets: HELPER_EXIT, HELPER_STDOUT, HELPER_STDERR
run_helper() {
    local old_path="$PATH"
    HELPER_STDOUT=""
    HELPER_STDERR=""
    HELPER_EXIT=0

    # Prepend mock dir to PATH so mock binaries are found first
    PATH="$MOCK_DIR:$old_path"
    export PATH

    # Capture stdout and stderr separately
    local tmp_stdout tmp_stderr
    tmp_stdout="$(mktemp)"
    tmp_stderr="$(mktemp)"

    set +e
    bash "$HELPER_SCRIPT" "$@" >"$tmp_stdout" 2>"$tmp_stderr"
    HELPER_EXIT=$?
    set -e

    HELPER_STDOUT="$(cat "$tmp_stdout")"
    HELPER_STDERR="$(cat "$tmp_stderr")"

    rm -f "$tmp_stdout" "$tmp_stderr"

    # Restore PATH
    PATH="$old_path"
    export PATH
}

# assert_exit_code — check exit code
# Arguments: $1 = expected, $2 = test description
assert_exit_code() {
    local expected="$1"
    local desc="$2"

    if [ "$HELPER_EXIT" -ne "$expected" ]; then
        printf '  FAIL: %s — expected exit code %d, got %d\n' "$desc" "$expected" "$HELPER_EXIT" >&2
        return 1
    fi
    return 0
}

# assert_stdout_contains — check stdout contains a string
# Arguments: $1 = expected substring, $2 = test description
assert_stdout_contains() {
    local expected="$1"
    local desc="$2"

    if ! printf '%s\n' "$HELPER_STDOUT" | grep -qF "$expected"; then
        printf '  FAIL: %s — stdout missing: %s\n' "$desc" "$expected" >&2
        printf '  Actual stdout: %s\n' "$HELPER_STDOUT" >&2
        return 1
    fi
    return 0
}

# assert_stderr_contains — check stderr contains a string
# Arguments: $1 = expected substring, $2 = test description
assert_stderr_contains() {
    local expected="$1"
    local desc="$2"

    if ! printf '%s\n' "$HELPER_STDERR" | grep -qF "$expected"; then
        printf '  FAIL: %s — stderr missing: %s\n' "$desc" "$expected" >&2
        printf '  Actual stderr: %s\n' "$HELPER_STDERR" >&2
        return 1
    fi
    return 0
}

# run_test — execute a test function and track results
# Arguments: $1 = test function name
run_test() {
    local test_fn="$1"
    local test_name="${test_fn#test_}"

    TESTS_RUN=$((TESTS_RUN + 1))
    printf 'TEST: %s ... ' "$test_name" >&2

    setup_mock_dir

    local result=0
    "$test_fn" || result=$?

    cleanup_mock_dir

    if [ "$result" -eq 0 ]; then
        TESTS_PASSED=$((TESTS_PASSED + 1))
        printf 'PASS\n' >&2
    else
        TESTS_FAILED=$((TESTS_FAILED + 1))
        printf 'FAIL\n' >&2
    fi
}

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

# Test 1: nginx 1.26.3 + amd64 + with-compat → STATUS=supported, exit 0
test_supported_nginx_1263_amd64_with_compat() {
    local version_line="nginx version: nginx/1.26.3"
    local config_output="nginx version: nginx/1.26.3
built by gcc 12.2.0 (Debian 12.2.0-14)
built with OpenSSL 3.0.11 19 Sep 2023
TLS SNI support enabled
configure arguments: --prefix=/etc/nginx --with-compat --with-http_ssl_module"

    create_mock_nginx "$version_line" "$config_output"
    create_mock_uname "x86_64"

    run_helper

    local failed=0
    assert_exit_code 0 "exit code" || failed=1
    assert_stdout_contains "STATUS=supported" "status" || failed=1
    assert_stdout_contains "NGINX_VERSION=1.26.3" "version" || failed=1
    assert_stdout_contains "ARCH=x86_64" "arch" || failed=1
    assert_stdout_contains "WITH_COMPAT=yes" "with-compat" || failed=1
    assert_stdout_contains "NGINX_SOURCE=nginx.org" "source" || failed=1
    assert_stdout_contains "EXPECTED_PACKAGE=nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_amd64.deb" "package name" || failed=1

    return $failed
}

# Test 2: nginx 1.25.0 + amd64 → STATUS=unsupported, exit 1
test_unsupported_nginx_1250_amd64() {
    local version_line="nginx version: nginx/1.25.0"
    local config_output="nginx version: nginx/1.25.0
built by gcc 12.2.0 (Debian 12.2.0-14)
configure arguments: --prefix=/etc/nginx --with-compat --with-http_ssl_module"

    create_mock_nginx "$version_line" "$config_output"
    create_mock_uname "x86_64"

    run_helper

    local failed=0
    assert_exit_code 1 "exit code" || failed=1
    assert_stdout_contains "STATUS=unsupported" "status" || failed=1
    assert_stdout_contains "NGINX_VERSION=1.25.0" "version" || failed=1
    assert_stderr_contains "UNSUPPORTED" "stderr guidance" || failed=1

    return $failed
}

# Test 3: nginx not found → error message, exit 2
test_nginx_not_found() {
    # Do not create a mock nginx binary — leave MOCK_DIR empty
    create_mock_uname "x86_64"

    # Remove any nginx from the mock PATH by ensuring MOCK_DIR has no nginx
    # and we override PATH to only include MOCK_DIR + minimal system paths
    local old_path="$PATH"
    HELPER_STDOUT=""
    HELPER_STDERR=""
    HELPER_EXIT=0

    # Use a PATH that definitely has no nginx
    PATH="$MOCK_DIR:/usr/bin:/bin"
    export PATH

    local tmp_stdout tmp_stderr
    tmp_stdout="$(mktemp)"
    tmp_stderr="$(mktemp)"

    set +e
    bash "$HELPER_SCRIPT" >"$tmp_stdout" 2>"$tmp_stderr"
    HELPER_EXIT=$?
    set -e

    HELPER_STDOUT="$(cat "$tmp_stdout")"
    HELPER_STDERR="$(cat "$tmp_stderr")"

    rm -f "$tmp_stdout" "$tmp_stderr"

    PATH="$old_path"
    export PATH

    local failed=0
    assert_exit_code 2 "exit code" || failed=1
    assert_stderr_contains "nginx not found" "error message" || failed=1
    assert_stdout_contains "STATUS=unknown" "status" || failed=1

    return $failed
}

# Test 4: distro nginx (Ubuntu patch marker) → STATUS=unsupported + guidance
test_distro_nginx_ubuntu() {
    local version_line="nginx version: nginx/1.26.3 (Ubuntu)"
    local config_output="nginx version: nginx/1.26.3 (Ubuntu)
built by gcc 12.2.0 (Ubuntu 12.2.0-14ubuntu2)
built with OpenSSL 3.0.10 1 Aug 2023
configure arguments: --prefix=/usr/share/nginx --with-cc-opt=-g -O2 -fstack-protector-strong --with-compat"

    create_mock_nginx "$version_line" "$config_output"
    create_mock_uname "x86_64"

    run_helper

    local failed=0
    assert_exit_code 1 "exit code" || failed=1
    assert_stdout_contains "STATUS=unsupported" "status" || failed=1
    assert_stdout_contains "NGINX_SOURCE=distro" "source" || failed=1
    assert_stderr_contains "distribution package" "guidance" || failed=1
    assert_stderr_contains "build from source" "build guidance" || failed=1

    return $failed
}

# Test 5: arm64 + stable → STATUS=supported, correct package name
test_supported_arm64_stable() {
    local version_line="nginx version: nginx/1.26.3"
    local config_output="nginx version: nginx/1.26.3
built by gcc 12.2.0 (Debian 12.2.0-14)
configure arguments: --prefix=/etc/nginx --with-compat --with-http_ssl_module"

    create_mock_nginx "$version_line" "$config_output"
    create_mock_uname "aarch64"

    run_helper

    local failed=0
    assert_exit_code 0 "exit code" || failed=1
    assert_stdout_contains "STATUS=supported" "status" || failed=1
    assert_stdout_contains "ARCH=aarch64" "arch" || failed=1
    assert_stdout_contains "EXPECTED_PACKAGE=nginx-module-markdown-for-agents_0.7.0_nginx-1.26.3_arm64.deb" "package name" || failed=1

    return $failed
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    printf '=== compat-check helper unit tests ===\n' >&2
    printf '\n' >&2

    run_test test_supported_nginx_1263_amd64_with_compat
    run_test test_unsupported_nginx_1250_amd64
    run_test test_nginx_not_found
    run_test test_distro_nginx_ubuntu
    run_test test_supported_arm64_stable

    printf '\n' >&2
    printf '=== Results: %d/%d passed, %d failed ===\n' \
        "$TESTS_PASSED" "$TESTS_RUN" "$TESTS_FAILED" >&2

    if [ "$TESTS_FAILED" -gt 0 ]; then
        return 1
    fi
    return 0
}

main "$@"
