#!/usr/bin/env bash
#
# test_detect_workflow_input_injection.sh - Unit tests for workflow input injection.
#
# Validates that GitHub Actions inputs are not directly interpolated in run blocks.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="bash ${SCRIPT_DIR}/../detect_workflow_input_injection.sh"

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    local msg="$1"
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '  PASS: %s\n' "${msg}"
    return 0
}

fail() {
    local msg="$1"
    local detail="${2:-}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '  FAIL: %s\n' "${msg}" >&2
    if [[ -n "${detail}" ]]; then
        printf '        Detail: %s\n' "${detail}" >&2
    fi
    return 0
}

printf 'Unit Tests: detect_workflow_input_injection.sh\n'

# Create temp fixture directory
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/wf-injection.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "${tmp_dir}"' EXIT
wf_dir="${tmp_dir}/.github/workflows"
mkdir -p "${wf_dir}"

# Test 1: Clean workflow - input routed through env -> PASS
cat >"${wf_dir}/clean.yml" <<'Y'
name: clean
on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Release version'
        required: true
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Validate
        env:
          INPUT_VERSION: ${{ inputs.version }}
        run: |
          version="${INPUT_VERSION}"
          if [[ ! "${version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "ERROR: invalid version" >&2
            exit 1
          fi
Y

output_file="${tmp_dir}/clean.out"
${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "clean workflow (env-routed input) passes"
else
    fail "clean workflow (env-routed input) passes" "exit code ${exit_code}"
    cat "${output_file}" >&2
fi

# Test 2: Vulnerable workflow - input directly in run block -> FAIL
cat >"${wf_dir}/vulnerable.yml" <<'Y'
name: vulnerable
on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Release version'
        required: true
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Build
        run: |
          PKG_VERSION="${{ inputs.version }}"
          echo "Building ${PKG_VERSION}"
Y

output_file="${tmp_dir}/vuln.out"
${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "vulnerable workflow (direct input interpolation) detected"
else
    fail "vulnerable workflow (direct input interpolation) detected" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi

# Remove vulnerable file for next test
rm -f "${wf_dir}/vulnerable.yml"

# Test 3: Empty workflows dir -> PASS
empty_dir="${tmp_dir}/empty-wf"
mkdir -p "${empty_dir}"
${DETECTOR} "${empty_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "empty workflows dir passes"
else
    fail "empty workflows dir passes" "exit code ${exit_code}"
fi

printf '\n%d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    exit 1
fi
exit 0