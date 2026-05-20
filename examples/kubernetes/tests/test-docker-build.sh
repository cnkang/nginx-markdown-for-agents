#!/bin/bash
# ---------------------------------------------------------------------------
# test-docker-build.sh — Docker build validation for Ingress Controller image
#
# PURPOSE:
#   Validates that the custom NGINX Ingress Controller image builds
#   successfully and that the markdown filter module is correctly loaded.
#   Intended for manual execution or CI when Docker is available.
#
# REQUIREMENTS:
#   - REQ-0700-K8S-002: Ingress/Controller custom image build
#   - Design reference: design.md §12.1
#
# USAGE:
#   ./test-docker-build.sh [OPTIONS]
#
# OPTIONS:
#   -d, --dockerfile PATH   Path to Dockerfile (default: ../Dockerfile.ingress)
#   -t, --tag TAG           Image tag to use (default: nginx-markdown-test:latest)
#   -c, --context PATH      Docker build context (default: repository root)
#   --no-cleanup            Keep the built image after test (default: remove)
#   -h, --help              Show this help message
#
# EXAMPLES:
#   # Run with defaults:
#   ./test-docker-build.sh
#
#   # Custom Dockerfile and tag:
#   ./test-docker-build.sh --dockerfile /path/to/Dockerfile.ingress --tag my-test:v1
#
#   # Keep image for further inspection:
#   ./test-docker-build.sh --no-cleanup
#
# EXIT CODES:
#   0  All checks passed
#   1  One or more checks failed
#   2  Usage error or missing prerequisites
#
# CHECKS PERFORMED:
#   1. Docker image builds without errors
#   2. nginx -V reports the markdown module loaded
#   3. Module .so file exists at /usr/lib/nginx/modules/
#   4. load_module configuration snippet exists
#
# NOTES:
#   - Requires: docker (or compatible runtime like podman)
#   - macOS bash 3.2 compatible (no bash 4+ features)
#   - Messages to stderr; only final PASS/FAIL summary to stdout
#
# SEE ALSO:
#   - examples/kubernetes/Dockerfile.ingress
#   - docs/guides/KUBERNETES_DEPLOYMENT.md
# ---------------------------------------------------------------------------

set -e

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKERFILE="${SCRIPT_DIR}/../Dockerfile.ingress"
IMAGE_TAG="nginx-markdown-test:latest"
BUILD_CONTEXT=""
CLEANUP="yes"
PASS_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

usage() {
    sed -n '/^# USAGE:/,/^# EXIT CODES:/{ /^# EXIT CODES:/d; s/^# \{0,3\}//; p; }' "$0" >&2
    return 0
}

log_info() {
    printf '[INFO]  %s\n' "$1" >&2
}

log_pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '[PASS]  %s\n' "$1" >&2
}

log_fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '[FAIL]  %s\n' "$1" >&2
}

log_error() {
    printf '[ERROR] %s\n' "$1" >&2
}

cleanup() {
    if [ "$CLEANUP" = "yes" ] && [ -n "$IMAGE_TAG" ]; then
        log_info "Cleaning up: removing image $IMAGE_TAG"
        docker rmi "$IMAGE_TAG" >/dev/null 2>&1 || true
    fi
}

check_prerequisites() {
    if ! command -v docker >/dev/null 2>&1; then
        log_error "docker is required but not found in PATH"
        log_error "Install Docker: https://docs.docker.com/get-docker/"
        return 2
    fi

    if ! docker info >/dev/null 2>&1; then
        log_error "Docker daemon is not running or not accessible"
        return 2
    fi

    if [ ! -f "$DOCKERFILE" ]; then
        log_error "Dockerfile not found: $DOCKERFILE"
        return 2
    fi

    return 0
}

# Detect repository root by walking up from script directory
detect_repo_root() {
    local dir="$SCRIPT_DIR"
    while [ "$dir" != "/" ]; do
        if [ -f "$dir/Makefile" ] && [ -d "$dir/components" ]; then
            printf '%s' "$dir"
            return 0
        fi
        dir="$(dirname "$dir")"
    done
    # Fallback: three levels up from tests/
    printf '%s' "$(cd "$SCRIPT_DIR/../../.." && pwd)"
    return 0
}

# ---------------------------------------------------------------------------
# Test functions
# ---------------------------------------------------------------------------

test_docker_build() {
    log_info "Test: Docker image builds successfully"
    log_info "  Dockerfile: $DOCKERFILE"
    log_info "  Context:    $BUILD_CONTEXT"
    log_info "  Tag:        $IMAGE_TAG"

    local build_output
    local build_rc

    build_output="$(docker build \
        -f "$DOCKERFILE" \
        -t "$IMAGE_TAG" \
        "$BUILD_CONTEXT" 2>&1)" || build_rc=$?

    if [ "${build_rc:-0}" -ne 0 ]; then
        log_fail "Docker build failed (exit code $build_rc)"
        printf '%s\n' "$build_output" | tail -20 >&2
        return 1
    fi

    log_pass "Docker image built successfully: $IMAGE_TAG"
    return 0
}

test_nginx_version() {
    log_info "Test: nginx -V reports markdown module"

    local nginx_v_output
    local run_rc

    # nginx -V outputs to stderr, capture both streams
    nginx_v_output="$(docker run --rm "$IMAGE_TAG" nginx -V 2>&1)" || run_rc=$?

    if [ "${run_rc:-0}" -ne 0 ]; then
        log_fail "nginx -V failed inside container (exit code $run_rc)"
        printf '%s\n' "$nginx_v_output" | tail -10 >&2
        return 1
    fi

    # Check for the module in the configure arguments or loaded modules
    case "$nginx_v_output" in
        *markdown*|*ngx_http_markdown*|*nginx-markdown*)
            log_pass "nginx -V output references markdown module"
            ;;
        *)
            # Module may be dynamic (not in configure args) — check if nginx starts
            log_info "  Module not in configure args (expected for dynamic modules)"
            log_info "  Verifying nginx can start with module loaded..."

            local test_output
            test_output="$(docker run --rm "$IMAGE_TAG" nginx -t 2>&1)" || run_rc=$?

            if [ "${run_rc:-0}" -ne 0 ]; then
                log_fail "nginx -t failed — module may not load correctly"
                printf '%s\n' "$test_output" | tail -10 >&2
                return 1
            fi

            case "$test_output" in
                *"syntax is ok"*|*"test is successful"*)
                    log_pass "nginx -t passes (module loads without error)"
                    ;;
                *)
                    log_fail "nginx -t output unexpected: $test_output"
                    return 1
                    ;;
            esac
            ;;
    esac

    return 0
}

test_module_file_exists() {
    log_info "Test: Module .so file exists at expected path"

    local expected_path="/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so"
    local check_output
    local check_rc

    check_output="$(docker run --rm "$IMAGE_TAG" \
        ls -la "$expected_path" 2>&1)" || check_rc=$?

    if [ "${check_rc:-0}" -ne 0 ]; then
        log_fail "Module file not found at $expected_path"
        printf '%s\n' "$check_output" >&2
        return 1
    fi

    # Verify it's a regular file with non-zero size
    local file_size
    file_size="$(docker run --rm "$IMAGE_TAG" \
        stat -c '%s' "$expected_path" 2>/dev/null || \
        docker run --rm "$IMAGE_TAG" \
        stat -f '%z' "$expected_path" 2>/dev/null)" || true

    if [ -n "$file_size" ] && [ "$file_size" -gt 0 ] 2>/dev/null; then
        log_pass "Module file exists: $expected_path ($file_size bytes)"
    else
        log_pass "Module file exists: $expected_path"
    fi

    return 0
}

test_load_module_snippet() {
    log_info "Test: load_module configuration snippet exists"

    local snippet_path="/etc/nginx/modules/10-mod-markdown.conf"
    local snippet_content
    local check_rc

    snippet_content="$(docker run --rm "$IMAGE_TAG" \
        cat "$snippet_path" 2>&1)" || check_rc=$?

    if [ "${check_rc:-0}" -ne 0 ]; then
        log_fail "load_module snippet not found at $snippet_path"
        return 1
    fi

    # Verify snippet contains load_module directive
    case "$snippet_content" in
        *load_module*ngx_http_markdown_filter_module*)
            log_pass "load_module snippet correctly references markdown module"
            ;;
        *load_module*)
            log_pass "load_module snippet exists (references a module)"
            ;;
        *)
            log_fail "load_module snippet does not contain load_module directive"
            printf '  Content: %s\n' "$snippet_content" >&2
            return 1
            ;;
    esac

    return 0
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            -d|--dockerfile)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                DOCKERFILE="$2"
                shift 2
                ;;
            -t|--tag)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                IMAGE_TAG="$2"
                shift 2
                ;;
            -c|--context)
                if [ $# -lt 2 ]; then
                    log_error "Option $1 requires an argument"
                    return 2
                fi
                BUILD_CONTEXT="$2"
                shift 2
                ;;
            --no-cleanup)
                CLEANUP="no"
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            -*)
                log_error "Unknown option: $1"
                usage
                return 2
                ;;
            *)
                log_error "Unexpected argument: $1"
                usage
                return 2
                ;;
        esac
    done

    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    parse_args "$@" || exit $?

    # Resolve build context default (repository root)
    if [ -z "$BUILD_CONTEXT" ]; then
        BUILD_CONTEXT="$(detect_repo_root)"
    fi

    check_prerequisites || exit $?

    trap cleanup EXIT

    printf '=%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2
    log_info "nginx-markdown-for-agents Docker Build Test"
    printf '=%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2

    # Step 1: Build the image
    if ! test_docker_build; then
        log_error "Build failed — skipping remaining checks"
        printf '\n' >&2
        printf 'RESULT: FAIL (build error)\n'
        return 1
    fi

    printf '\n' >&2

    # Step 2-4: Validate the built image
    test_nginx_version || true
    test_module_file_exists || true
    test_load_module_snippet || true

    # Summary
    printf '\n' >&2
    printf -- '-%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2
    log_info "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
    printf -- '-%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 >&2
    printf '\n' >&2

    if [ "$FAIL_COUNT" -gt 0 ]; then
        printf 'RESULT: FAIL (%d/%d checks passed)\n' "$PASS_COUNT" "$((PASS_COUNT + FAIL_COUNT))"
        return 1
    fi

    printf 'RESULT: PASS (%d/%d checks passed)\n' "$PASS_COUNT" "$PASS_COUNT"
    return 0
}

main "$@"
