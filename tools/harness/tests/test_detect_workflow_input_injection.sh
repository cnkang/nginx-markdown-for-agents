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

# Test 3: Command output directly in run block -> FAIL
cat >"${wf_dir}/command-output.yml" <<'Y'
name: command-output
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - id: resolve
        run: echo 'command=make test' >> "$GITHUB_OUTPUT"
      - name: Run
        run: ${{ steps.resolve.outputs.command }}
Y

${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "command output interpolation detected"
else
    fail "command output interpolation detected" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi

rm -f "${wf_dir}/command-output.yml"

# Test 4: Empty workflows dir -> PASS
empty_dir="${tmp_dir}/empty-wf"
mkdir -p "${empty_dir}"
${DETECTOR} "${empty_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "empty workflows dir passes"
else
    fail "empty workflows dir passes" "exit code ${exit_code}"
fi


# Test 5: Block scalar with trailing comment and indentation indicators -> FAIL
cat >"${wf_dir}/scalar-comment.yml" <<'Y'
name: scalar-comment
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Build
        run: >- # trailing comment hides the block
          PKG_VERSION="${{ inputs.version }}"
          echo "Building ${PKG_VERSION}"
Y

${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "block scalar with trailing comment detected"
else
    fail "block scalar with trailing comment detected" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/scalar-comment.yml"

# Test 6: Indentation-indicator block scalar (run: |2 and run: |-2) -> FAIL
cat >"${wf_dir}/indent-indicator.yml" <<'Y'
name: indent-indicator
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Build
        run: |2
          PKG_VERSION="${{ inputs.version }}"
          echo "Building ${PKG_VERSION}"
Y

${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "indentation-indicator block scalar detected"
else
    fail "indentation-indicator block scalar detected" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/indent-indicator.yml"

# Test 6b: Chomping-then-indentation indicator order (run: |-2) -> FAIL
cat >"${wf_dir}/indent-indicator-2.yml" <<'Y'
name: indent-indicator-2
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Build
        run: |-2
          PKG_VERSION="${{ inputs.version }}"
          echo "Building ${PKG_VERSION}"
Y

${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "chomping+indentation indicator order detected"
else
    fail "chomping+indentation indicator order detected" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/indent-indicator-2.yml"

# Test 7: Bracket-notation step outputs in run block -> FAIL
cat >"${wf_dir}/bracket-output.yml" <<'Y'
name: bracket-output
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - id: resolve
        run: echo 'result=make test' >> "$GITHUB_OUTPUT"
      - name: Run
        run: echo "${{ steps['resolve'].outputs['result'] }}"
Y

${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "bracket-notation step output interpolation detected"
else
    fail "bracket-notation step output interpolation detected" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/bracket-output.yml"

# Test 8: Index-form event selectors in run block -> FAIL (both selector
# forms must be caught; the index form bypasses a dot-only pattern)
cat >"${wf_dir}/index-event.yml" <<'Y'
name: index-event
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Run
        run: |
          echo "${{ github.event['pull_request'].head.ref }}"
          echo "${{ github.event.pull_request['head']['ref'] }}"
          echo "${{ github.head_ref }}"
Y

${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "index-form event selector interpolation detected"
else
    fail "index-form event selector interpolation detected" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/index-event.yml"

# Test 9: Each index-form selector variant must be caught independently
# (a combined fixture could pass with only one variant detected).
check_index_variant() {
    local name="$1"
    local expr="$2"
    cat >"${wf_dir}/single-index.yml" <<Y
name: single-index
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Run
        run: echo "\${{ ${expr} }}"
Y
    ${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
    local rc=$?
    if [[ ${rc} -eq 1 ]] && grep -Fq "${expr}" "${output_file}"; then
        pass "${name} detected"
    else
        fail "${name} detected" "expected exit 1 + diagnostic, got ${rc}"
        cat "${output_file}" >&2
    fi
    rm -f "${wf_dir}/single-index.yml"
    return 0
}

check_index_variant "event['pull_request'] bracket event" "github.event['pull_request'].head.ref"
check_index_variant "event.pull_request['head'] bracket field" "github.event.pull_request['head']['ref']"
check_index_variant "github.head_ref alias" "github.head_ref"
check_index_variant "github.ref_name alias" "github.ref_name"

# Test 10: Bracket-form benign step outputs (allowlisted selector) must NOT
# be flagged: dot-form and bracket-form selectors normalize to the same
# bare identifier before the allowlist comparison.
cat >"${wf_dir}/bracket-benign.yml" <<'Y'
name: bracket-benign
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - id: meta
        run: echo 'version=1.2.3' >> "$GITHUB_OUTPUT"
      - name: Use
        run: echo "${{ steps['meta'].outputs['version'] }}"
Y
${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "bracket-form benign output selector (version) not flagged"
else
    fail "bracket-form benign output selector (version) not flagged" "expected exit 0, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/bracket-benign.yml"

# Test 11: Dot-form benign step output must also pass (regression guard).
cat >"${wf_dir}/dot-benign.yml" <<'Y'
name: dot-benign
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - id: meta
        run: echo 'version=1.2.3' >> "$GITHUB_OUTPUT"
      - name: Use
        run: echo "${{ steps.meta.outputs.version }}"
Y
${DETECTOR} "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "dot-form benign output selector (version) not flagged"
else
    fail "dot-form benign output selector (version) not flagged" "expected exit 0, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/dot-benign.yml"

printf '\n%d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    exit 1
fi
exit 0
