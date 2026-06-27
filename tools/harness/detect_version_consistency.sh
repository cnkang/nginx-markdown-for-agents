#!/usr/bin/env bash
# detect_version_consistency.sh — Rule: Version consistency across all artifacts
#
# Checks that version numbers are consistent across:
# - Main Cargo.toml (source of truth)
# - Helm Chart.yaml (version and appVersion)
# - Internal Cargo.toml dependencies (fuzz, corpus tools)
#
# Exit codes:
#   0 - All versions consistent
#   1 - Version mismatch detected
#   2 - Script error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ERRORS=0

log_error() {
    local msg="$1"
    echo -e "${RED}ERROR:${NC} ${msg}" >&2
    ERRORS=$((ERRORS + 1))
    return 0
}

log_pass() {
    local msg="$1"
    echo -e "${GREEN}PASS:${NC} ${msg}"
    return 0
}

log_info() {
    local msg="$1"
    echo -e "${YELLOW}INFO:${NC} ${msg}" >&2
    return 0
}

# Extract version from main Cargo.toml: version = "0.8.3" -> 0.8.3
get_cargo_version() {
    local cargo_file="$1"
    [[ -f "$cargo_file" ]] || { echo ""; return; }
    sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$cargo_file" | head -1
}

# Extract version from Chart.yaml: version: 0.8.3 -> 0.8.3
get_chart_version() {
    local chart_file="$1"
    [[ -f "$chart_file" ]] || { echo ""; return; }
    sed -n 's/^version:[[:space:]]*\([^[:space:]]*\).*/\1/p' "$chart_file" | head -1
}

# Extract appVersion from Chart.yaml: appVersion: "0.8.3" -> 0.8.3
get_chart_appversion() {
    local chart_file="$1"
    [[ -f "$chart_file" ]] || { echo ""; return; }
    sed -n 's/^appVersion:[[:space:]]*//p' "$chart_file" | head -1 | tr -d '"' | tr -d "'"
}

# Extract dependency version from Cargo.toml:
#   nginx-markdown-converter = { version = "0.8.3", path = ".." } -> 0.8.3
get_dependency_version() {
    local cargo_file="$1"
    local dep_name="$2"
    [[ -f "$cargo_file" ]] || { echo ""; return; }
    grep -E "^${dep_name}[[:space:]]*=" "$cargo_file" \
        | sed -n 's/.*version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
        | head -1
}

main() {
    log_info "Checking version consistency..."

    # 1. Source of truth: main Cargo.toml
    local main_cargo="${PROJECT_ROOT}/components/rust-converter/Cargo.toml"
    local expected
    expected=$(get_cargo_version "$main_cargo")

    if [[ -z "$expected" ]]; then
        log_error "Cannot read version from $main_cargo"
        exit 2
    fi
    log_pass "Main Cargo.toml version: $expected"

    # 2. Helm Chart.yaml
    local chart_file="${PROJECT_ROOT}/charts/nginx-markdown/Chart.yaml"
    if [[ -f "$chart_file" ]]; then
        local chart_ver
        chart_ver=$(get_chart_version "$chart_file")
        if [[ "$chart_ver" != "$expected" ]]; then
            log_error "Chart.yaml version: '$chart_ver' (expected '$expected')"
        else
            log_pass "Chart.yaml version: $chart_ver"
        fi

        local chart_app
        chart_app=$(get_chart_appversion "$chart_file")
        if [[ "$chart_app" != "$expected" ]]; then
            log_error "Chart.yaml appVersion: '$chart_app' (expected '$expected')"
        else
            log_pass "Chart.yaml appVersion: $chart_app"
        fi
    else
        log_info "Chart.yaml not found (skipping)"
    fi

    # 3. Internal Cargo.toml dependencies
    local fuzz_cargo="${PROJECT_ROOT}/components/rust-converter/fuzz/Cargo.toml"
    if [[ -f "$fuzz_cargo" ]]; then
        local fuzz_ver
        fuzz_ver=$(get_dependency_version "$fuzz_cargo" "nginx-markdown-converter")
        if [[ -n "$fuzz_ver" && "$fuzz_ver" != "$expected" ]]; then
            log_error "fuzz/Cargo.toml dep version: '$fuzz_ver' (expected '$expected')"
        elif [[ -n "$fuzz_ver" ]]; then
            log_pass "fuzz/Cargo.toml dep version: $fuzz_ver"
        fi
    fi

    local corpus_cargo="${PROJECT_ROOT}/tools/corpus/test-corpus-conversion/Cargo.toml"
    if [[ -f "$corpus_cargo" ]]; then
        local corpus_ver
        corpus_ver=$(get_dependency_version "$corpus_cargo" "nginx-markdown-converter")
        if [[ -n "$corpus_ver" && "$corpus_ver" != "$expected" ]]; then
            log_error "corpus/Cargo.toml dep version: '$corpus_ver' (expected '$expected')"
        elif [[ -n "$corpus_ver" ]]; then
            log_pass "corpus/Cargo.toml dep version: $corpus_ver"
        fi
    fi

    # 4. Homebrew formula (informational — workflow rewrites url/sha256 at publish time)
    local formula_file="${PROJECT_ROOT}/packaging/homebrew/nginx-markdown-module.rb"
    if [[ -f "$formula_file" ]]; then
        local formula_ver
        formula_ver=$(grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' "$formula_file" | head -1 | sed 's/^v//')
        if [[ -n "$formula_ver" ]]; then
            log_info "Homebrew formula: $formula_ver (intentionally previous; updated by publish workflow)"
        fi
    fi

    # Summary
    echo ""
    if [[ $ERRORS -eq 0 ]]; then
        log_pass "All version checks passed"
        return 0
    else
        log_error "Found $ERRORS version inconsistency(ies)"
        return 1
    fi
}

main "$@"
