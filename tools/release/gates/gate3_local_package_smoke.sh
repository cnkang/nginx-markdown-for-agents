#!/bin/bash
# ---------------------------------------------------------------------------
# gate3_local_package_smoke.sh — Local Docker-based package build + smoke test
#
# PURPOSE:
#   Validates Gate 3 (Package Distribution) locally by building the module
#   in Docker, generating DEB/RPM packages, verifying install layout, and
#   running smoke tests in target distribution containers.
#
# PREREQUISITES:
#   - Docker (docker or podman)
#   - Internet access (to pull base images and nginx.org packages)
#
# USAGE:
#   gate3_local_package_smoke.sh [--skip-smoke] [--arch ARCH]
#
# OPTIONS:
#   --skip-smoke    Skip container smoke tests (only build + layout check)
#   --arch ARCH     Target architecture (default: auto-detect from host)
#   -h, --help      Show this help message
#
# EXIT CODES:
#   0   All checks passed
#   1   One or more checks failed
#   2   Usage error
#   3   Prerequisites missing (prints install instructions)
#
# ENVIRONMENT:
#   NGINX_VERSION       Override target NGINX version (default: 1.26.3)
#   PKG_VERSION         Override package version (default: from Cargo.toml)
#   DOCKER              Override docker binary (default: auto-detect)
#   GATE3_SMOKE_IMAGES  Comma-separated list of smoke test images
#                       (default: debian:12,ubuntu:24.04,almalinux:9)
#
# NOTES:
#   - macOS bash 3.2 compatible
#   - First run pulls images and builds from source (~5-10 min)
#   - Subsequent runs use Docker layer cache (~1-2 min)
# ---------------------------------------------------------------------------

set -euo pipefail

##############################################################################
# Constants
##############################################################################

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
readonly SCRIPT_DIR PROJECT_ROOT

readonly DEFAULT_NGINX_VERSION="1.26.3"
readonly DEFAULT_SMOKE_IMAGES="debian:12,ubuntu:24.04,almalinux:9"
readonly BUILD_IMAGE_TAG="nginx-markdown-gate3-build:local"
readonly NFPM_VERSION="2.46.3"

##############################################################################
# Helpers
##############################################################################

info() {
    local msg="$1"
    printf '[gate3] %s\n' "$msg" >&2
    return 0
}

pass() {
    local msg="$1"
    printf '[PASS]  %s\n' "$msg" >&2
    return 0
}

fail() {
    local msg="$1"
    printf '[FAIL]  %s\n' "$msg" >&2
    return 0
}

die() {
    local msg="$1"
    printf '[FATAL] %s\n' "$msg" >&2
    exit 1
}

usage() {
    sed -n '3,30p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

##############################################################################
# Architecture detection
##############################################################################

# detect_arch — set ARCH, RPM_ARCH, DOCKER_PLATFORM, and NFPM_HOST_SUFFIX
# based on the host machine architecture (uname -m).
# Supports override via the --arch flag or ARCH env var.
# On macOS under Rosetta, uname -m may report x86_64 even on Apple Silicon;
# use sysctl hw.optional.arm64 to detect the true hardware.
detect_arch() {
    local host_arch

    # Detect true hardware arch on macOS (Rosetta-proof)
    if [[ "$(uname -s)" == "Darwin" ]] && [[ "$(sysctl -n hw.optional.arm64 2>/dev/null)" == "1" ]]; then
        host_arch="arm64"
    else
        host_arch="$(uname -m)"
    fi

    case "$host_arch" in
        x86_64)
            ARCH="${ARCH:-amd64}"
            ;;
        arm64|aarch64)
            ARCH="${ARCH:-arm64}"
            ;;
        *)
            die "Unsupported host architecture: $host_arch"
            ;;
    esac

    # Derived architecture names used by Docker, RPM, and nFPM download URLs
    case "$ARCH" in
        amd64)
            DOCKER_PLATFORM="linux/amd64"
            RPM_ARCH="x86_64"
            NFPM_HOST_SUFFIX="Linux_x86_64"
            ;;
        arm64)
            DOCKER_PLATFORM="linux/arm64"
            RPM_ARCH="aarch64"
            NFPM_HOST_SUFFIX="Linux_arm64"
            ;;
        *)
            die "Unsupported target architecture: $ARCH"
            ;;
    esac

    info "Target arch: ${ARCH} (docker=${DOCKER_PLATFORM}, rpm=${RPM_ARCH})"
    return 0
}

##############################################################################
# Prerequisite checks
##############################################################################

check_docker() {
    local docker_bin="${DOCKER:-}"

    if [[ -z "$docker_bin" ]]; then
        if command -v docker >/dev/null 2>&1; then
            docker_bin="docker"
        elif command -v podman >/dev/null 2>&1; then
            docker_bin="podman"
        fi
    fi

    if [[ -z "$docker_bin" ]]; then
        printf '\n' >&2
        printf '╔══════════════════════════════════════════════════════════════╗\n' >&2
        printf '║  Gate 3 requires Docker (or Podman) to run locally.        ║\n' >&2
        printf '║                                                            ║\n' >&2
        printf '║  Install Docker:                                           ║\n' >&2
        printf '║    macOS:  brew install --cask docker                       ║\n' >&2
        printf '║    Linux:  https://docs.docker.com/engine/install/          ║\n' >&2
        printf '║                                                            ║\n' >&2
        printf '║  Or install Podman:                                        ║\n' >&2
        printf '║    macOS:  brew install podman                              ║\n' >&2
        printf '║    Linux:  https://podman.io/docs/installation              ║\n' >&2
        printf '╚══════════════════════════════════════════════════════════════╝\n' >&2
        exit 3
    fi

    # Verify Docker daemon is running
    if ! "$docker_bin" info >/dev/null 2>&1; then
        printf '\n' >&2
        printf '[gate3] Docker/Podman found (%s) but daemon is not running.\n' "$docker_bin" >&2
        printf '[gate3] Start Docker Desktop or run: sudo systemctl start docker\n' >&2
        exit 3
    fi

    DOCKER="$docker_bin"
    info "Using container runtime: ${DOCKER}"
    return 0
}

##############################################################################
# Argument parsing
##############################################################################

SKIP_SMOKE=0
ARCH=""

parse_args() {
    while [[ $# -gt 0 ]]; do
        local arg="$1"
        case "$arg" in
            --skip-smoke)
                SKIP_SMOKE=1
                shift
                ;;
            --arch)
                [[ $# -ge 2 ]] || die "--arch requires a value"
                ARCH="$2"
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown option: $arg"
                ;;
        esac
    done
    return 0
}

##############################################################################
# Build stage
##############################################################################

build_module() {
    local nginx_version="${NGINX_VERSION:-${DEFAULT_NGINX_VERSION}}"
    local pkg_version="${PKG_VERSION:-}"

    # Derive version from Cargo.toml if not set
    if [[ -z "$pkg_version" ]]; then
        pkg_version="$(grep '^version' "${PROJECT_ROOT}/components/rust-converter/Cargo.toml" \
            | head -1 | sed 's/.*"\(.*\)".*/\1/')"
    fi

    info "Building module: version=${pkg_version}, nginx=${nginx_version}, arch=${ARCH} (${DOCKER_PLATFORM})"

    # Create build Dockerfile
    local dockerfile
    dockerfile="$(mktemp "${TMPDIR:-/tmp}/gate3-dockerfile.XXXXXX")"

    cat > "$dockerfile" <<DOCKERFILE
FROM almalinux:9 AS builder
RUN dnf install -y \\
    gcc make ca-certificates curl-minimal pcre2-devel zlib-devel openssl-devel \\
    tar gzip findutils which \\
    && dnf clean all

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:\${PATH}"

# Detect native target triple for Rust build
RUN RUST_NATIVE_TARGET=\$(rustc -vV | grep '^host:' | awk '{print \$2}') \\
    && echo "\${RUST_NATIVE_TARGET}" > /tmp/rust_target.txt

# Download NGINX source
ARG NGINX_VERSION=${nginx_version}
RUN curl -fsSL -o /tmp/nginx.tar.gz "https://nginx.org/download/nginx-\${NGINX_VERSION}.tar.gz" \\
    && tar xzf /tmp/nginx.tar.gz -C /tmp

# Copy project source
COPY . /src
WORKDIR /src

# Build Rust converter with explicit target so output path matches NGINX config detection
RUN RUST_TARGET=\$(cat /tmp/rust_target.txt) \\
    && cd components/rust-converter \\
    && cargo build --release --target "\${RUST_TARGET}"

# Build NGINX module (config auto-detects target triple from uname)
RUN cd /tmp/nginx-\${NGINX_VERSION} \\
    && ./configure --with-compat --add-dynamic-module=/src/components/nginx-module \\
    && make modules

# Copy output
RUN mkdir -p /output \\
    && cp /tmp/nginx-\${NGINX_VERSION}/objs/ngx_http_markdown_filter_module.so /output/

FROM ubuntu:24.04 AS packager
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates rpm \\
    && rm -rf /var/lib/apt/lists/*

# Install nFPM
ARG NFPM_VERSION=${NFPM_VERSION}
ARG NFPM_HOST_SUFFIX=${NFPM_HOST_SUFFIX}
RUN curl -fsSL -o /tmp/nfpm.tar.gz \\
    "https://github.com/goreleaser/nfpm/releases/download/v\${NFPM_VERSION}/nfpm_\${NFPM_VERSION}_\${NFPM_HOST_SUFFIX}.tar.gz" \\
    && tar xzf /tmp/nfpm.tar.gz -C /usr/local/bin nfpm \\
    && chmod 755 /usr/local/bin/nfpm

COPY . /src
COPY --from=builder /output/ngx_http_markdown_filter_module.so /src/build/
WORKDIR /src

ARG PKG_VERSION=${pkg_version}
ARG NGINX_VERSION=${nginx_version}
ARG NFPM_ARCH=${ARCH}
ARG RPM_ARCH=${RPM_ARCH}

ENV PKG_VERSION=\${PKG_VERSION}
ENV NGINX_VERSION=\${NGINX_VERSION}
ENV NFPM_ARCH=\${NFPM_ARCH}

RUN mkdir -p /dist \\
    && nfpm package --config packaging/nfpm/nfpm.yaml --packager deb \\
       --target "/dist/nginx-module-markdown-for-agents_\${PKG_VERSION}_nginx-\${NGINX_VERSION}_\${NFPM_ARCH}.deb" \\
    && nfpm package --config packaging/nfpm/nfpm.yaml --packager rpm \\
       --target "/dist/nginx-module-markdown-for-agents-\${PKG_VERSION}-nginx\${NGINX_VERSION}-1.\${RPM_ARCH}.rpm"
DOCKERFILE

    # Build
    "$DOCKER" build \
        --platform "${DOCKER_PLATFORM}" \
        -f "$dockerfile" \
        -t "${BUILD_IMAGE_TAG}" \
        --target packager \
        "${PROJECT_ROOT}" >&2 \
        || { rm -f "$dockerfile"; die "Docker build failed"; }

    rm -f "$dockerfile"

    # Extract packages
    local dist_dir="${PROJECT_ROOT}/dist"
    mkdir -p "$dist_dir"
    local container_id
    container_id="$("$DOCKER" create "${BUILD_IMAGE_TAG}" true)"
    "$DOCKER" cp "${container_id}:/dist/." "$dist_dir/" >&2
    "$DOCKER" rm -f "$container_id" >/dev/null 2>&1

    info "Packages generated in ${dist_dir}/"
    ls -la "$dist_dir/"*.deb "$dist_dir/"*.rpm 2>/dev/null >&2 || true
    return 0
}

##############################################################################
# Layout validation
##############################################################################

validate_layout() {
    local dist_dir="${PROJECT_ROOT}/dist"
    local layout_script="${SCRIPT_DIR}/check_install_layout.sh"

    if [[ ! -x "$layout_script" ]]; then
        chmod +x "$layout_script"
    fi

    info "Running install layout validation (in container for rpm support)..."

    # Run layout check inside a container that has both dpkg-deb and rpm.
    # Globs must expand inside the container (where /dist is mounted), so
    # wrap in bash -c with single quotes to prevent host-side expansion.
    if "$DOCKER" run --rm \
        --platform "${DOCKER_PLATFORM}" \
        -v "${dist_dir}:/dist:ro" \
        -v "${layout_script}:/check_install_layout.sh:ro" \
        "${BUILD_IMAGE_TAG}" \
        bash -c 'bash /check_install_layout.sh /dist/*.deb /dist/*.rpm' \
        >&2 2>&1; then
        pass "Install layout validation passed"
        return 0
    fi

    fail "Install layout validation failed"
    return 1
}

##############################################################################
# Smoke tests
##############################################################################

run_smoke_tests() {
    local nginx_version="${NGINX_VERSION:-${DEFAULT_NGINX_VERSION}}"
    local smoke_images="${GATE3_SMOKE_IMAGES:-${DEFAULT_SMOKE_IMAGES}}"
    local dist_dir="${PROJECT_ROOT}/dist"
    local had_failure=0

    info "Running smoke tests in containers..."

    # Split comma-separated images into array
    local old_ifs="$IFS"
    IFS=','
    local images_arr=()
    for img in $smoke_images; do
        images_arr+=("$img")
    done
    IFS="$old_ifs"

    for image in "${images_arr[@]}"; do
        local pkg_format
        case "$image" in
            *ubuntu*|*debian*)
                pkg_format="deb"
                ;;
            *alma*|*centos*|*rocky*|*amazon*)
                pkg_format="rpm"
                ;;
            *)
                info "Skipping unknown image: $image"
                continue
                ;;
        esac

        local pkg_file
        local pkg_pattern
        case "$pkg_format" in
            deb)
                pkg_pattern="*_${ARCH}.deb"
                ;;
            rpm)
                pkg_pattern="*-1.${RPM_ARCH}.rpm"
                ;;
            *)
                die "Unsupported package format: $pkg_format"
                ;;
        esac
        pkg_file="$(find "$dist_dir" -name "$pkg_pattern" -print -quit 2>/dev/null || true)"
        if [[ -z "$pkg_file" ]]; then
            fail "No ${pkg_pattern} package found for ${image}"
            had_failure=1
            continue
        fi

        info "Smoke test: ${image} (${pkg_format})"
        local pkg_basename
        pkg_basename="$(basename "$pkg_file")"

        if "$DOCKER" run --rm \
            --platform "${DOCKER_PLATFORM}" \
            -v "${PROJECT_ROOT}/packaging/scripts:/scripts:ro" \
            -v "${pkg_file}:/pkg/${pkg_basename}:ro" \
            "$image" \
            bash /scripts/smoke-test-basic.sh "/pkg/${pkg_basename}" "${nginx_version}" \
            >&2 2>&1; then
            pass "Smoke test passed: ${image}"
        else
            fail "Smoke test failed: ${image}"
            had_failure=1
        fi
    done

    return "$had_failure"
}

##############################################################################
# Main
##############################################################################

main() {
    parse_args "$@"
    check_docker
    detect_arch

    local had_failure=0

    # Stage 1: Build module and generate packages
    build_module || had_failure=1

    # Stage 2: Validate install layout
    if [[ "$had_failure" -eq 0 ]]; then
        validate_layout || had_failure=1
    fi

    # Stage 3: Smoke tests (optional)
    if [[ "$had_failure" -eq 0 ]] && [[ "$SKIP_SMOKE" -eq 0 ]]; then
        run_smoke_tests || had_failure=1
    elif [[ "$SKIP_SMOKE" -eq 1 ]]; then
        info "Smoke tests skipped (--skip-smoke)"
    fi

    # Summary
    printf '\n' >&2
    if [[ "$had_failure" -eq 0 ]]; then
        pass "Gate 3 local validation PASSED"
        return 0
    else
        fail "Gate 3 local validation FAILED"
        return 1
    fi
}

main "$@"
