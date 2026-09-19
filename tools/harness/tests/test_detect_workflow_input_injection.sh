#!/usr/bin/env bash
#
# test_detect_workflow_input_injection.sh - Unit tests for workflow input injection.
#
# Validates that GitHub Actions inputs are not directly interpolated in run blocks.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR=(bash "${SCRIPT_DIR}/../detect_workflow_input_injection.sh")

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
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
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
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
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

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
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
"${DETECTOR[@]}" "${empty_dir}" >"${output_file}" 2>&1
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

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
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

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
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

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
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

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
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

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
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
    "${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
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
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "bracket-form benign output selector (version) not flagged"
else
    fail "bracket-form benign output selector (version) not flagged" "expected exit 0, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/bracket-benign.yml"

# Test 11: a benign output followed by a command-bearing output on the same
# run line must report the second interpolation too.
cat >"${wf_dir}/multiple-outputs.yml" <<'Y'
name: multiple-outputs
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - id: meta
        run: echo 'version=1.2.3' >> "$GITHUB_OUTPUT"
      - id: resolve
        run: echo 'command=make test' >> "$GITHUB_OUTPUT"
      - name: Run
        run: echo "${{ steps.meta.outputs.version }}" && ${{ steps.resolve.outputs.command }}
Y

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] \
    && grep -q 'multiple-outputs.yml' "${output_file}" \
    && grep -Fq 'steps.resolve.outputs.command' "${output_file}"; then
    pass "all step-output interpolations on one line are audited"
else
    fail "all step-output interpolations on one line are audited" \
        "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/multiple-outputs.yml"

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
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "dot-form benign output selector (version) not flagged"
else
    fail "dot-form benign output selector (version) not flagged" "expected exit 0, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/dot-benign.yml"

# Test 12: Reusable-workflow `with:` values are not shell source and must not
# inherit the run block state from the preceding step/job.
cat >"${wf_dir}/reusable-with.yml" <<'Y'
name: reusable-with
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Prepare
        run: echo "ready"
  reusable:
    uses: ./.github/workflows/official.yml
    with:
      module_ref: ${{ github.ref_name }}
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "reusable-workflow with values are not treated as shell source"
else
    fail "reusable-workflow with values are not treated as shell source" "expected exit 0, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/reusable-with.yml"

# Test 13: A blank line inside a run block must not clear run state: the
# interpolation after the blank line is still shell source and must be
# detected (regression: blank lines used to terminate the block early).
cat >"${wf_dir}/blank-line-run.yml" <<'Y'
name: blank-line-run
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Build
        run: |
          echo "first line"

          PKG_VERSION="${{ inputs.version }}"
          echo "Building ${PKG_VERSION}"
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] && grep -Fq 'inputs.version' "${output_file}"; then
    pass "blank line inside run block does not clear run state"
else
    fail "blank line inside run block does not clear run state" "expected exit 1 + diagnostic, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/blank-line-run.yml"

# Test 14: Direct inputs.* interpolation inside an action's command-bearing
# with: input (args:) is a command-injection surface: the value reaches the
# action's command line exactly like a run block.
cat >"${wf_dir}/with-command.yml" <<'Y'
name: with-command
on:
  workflow_dispatch:
    inputs:
      pull_request_number:
        description: 'PR number'
        required: false
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Scan
        uses: SonarSource/sonarqube-scan-action@0000000000000000000000000000000000000000
        env:
          SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}
        with:
          args: >-
            -Dsonar.projectKey=example
            -Dsonar.pullrequest.key=${{ inputs.pull_request_number }}
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] && grep -Fq "with:" "${output_file}"; then
    pass "inputs.* in an action with: command input detected"
else
    fail "inputs.* in an action with: command input detected" "expected exit 1 + diagnostic, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/with-command.yml"

# Test 15: github.event.inputs.* in a with: command input is flagged too.
cat >"${wf_dir}/with-event-input.yml" <<'Y'
name: with-event-input
on:
  workflow_dispatch:
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Scan
        uses: example/action@0000000000000000000000000000000000000000
        with:
          command: ${{ github.event.inputs.command }}
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] && grep -Fq "github.event.inputs" "${output_file}"; then
    pass "github.event.inputs.* in an action with: command input detected"
else
    fail "github.event.inputs.* in an action with: command input detected" "expected exit 1 + diagnostic, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/with-event-input.yml"

# Test 15a: bracket-form inputs.* in a with: command input is flagged too
# (a dot-only pattern lets inputs['x'] evade the detector).
cat >"${wf_dir}/with-bracket-input.yml" <<'Y'
name: with-bracket-input
on:
  workflow_dispatch:
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Scan
        uses: example/action@0000000000000000000000000000000000000000
        with:
          args: -Dsonar.pullrequest.key=${{ inputs['pull_request_number'] }}
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] && grep -Fq "inputs" "${output_file}"; then
    pass "bracket-form inputs.* in a with: command input detected"
else
    fail "bracket-form inputs.* in a with: command input detected" "expected exit 1 + diagnostic, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/with-bracket-input.yml"

# Test 15b: bracket-form github.event.inputs.* in a with: command input.
cat >"${wf_dir}/with-bracket-event-input.yml" <<'Y'
name: with-bracket-event-input
on:
  workflow_dispatch:
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Scan
        uses: example/action@0000000000000000000000000000000000000000
        with:
          command: ${{ github.event.inputs['command'] }}
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] && grep -Fq "github.event.inputs" "${output_file}"; then
    pass "bracket-form github.event.inputs.* in a with: command input detected"
else
    fail "bracket-form github.event.inputs.* in a with: command input detected" "expected exit 1 + diagnostic, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/with-bracket-event-input.yml"

# Test 15c: bracket-form inputs.* inside a run block is flagged.
cat >"${wf_dir}/run-bracket-input.yml" <<'Y'
name: run-bracket-input
on:
  workflow_dispatch:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Build
        run: |
          PKG_VERSION="${{ inputs['version'] }}"
          echo "Building ${PKG_VERSION}"
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] && grep -Fq "inputs" "${output_file}"; then
    pass "bracket-form inputs.* in a run block detected"
else
    fail "bracket-form inputs.* in a run block detected" "expected exit 1 + diagnostic, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/run-bracket-input.yml"

# Test 16: structured with: inputs (ref:, python-version:) are not command
# lines; interpolating dispatch inputs there must not be reported as command
# injection (the workflow validates them before the scan step consumes them).
cat >"${wf_dir}/with-structured.yml" <<'Y'
name: with-structured
on:
  workflow_dispatch:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@0000000000000000000000000000000000000000
        with:
          ref: ${{ github.event.inputs.ref || github.ref }}
      - name: Setup
        uses: actions/setup-python@0000000000000000000000000000000000000000
        with:
          python-version: '3.14.6'
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "structured with: inputs are not treated as command lines"
else
    fail "structured with: inputs are not treated as command lines" "expected exit 0, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/with-structured.yml"

# Test 17: a reusable-workflow job's with: is wiring; module_ref/sha style
# inputs there must stay clean (the job-level with: indent is shallower than
# the step list indent).
cat >"${wf_dir}/job-with-wiring.yml" <<'Y'
name: job-with-wiring
on:
  workflow_dispatch:
jobs:
  call:
    uses: ./.github/workflows/official.yml
    with:
      module_ref: ${{ inputs.module_ref }}
      module_sha: ${{ github.event.inputs.module_sha }}
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 0 ]]; then
    pass "reusable-workflow job with: wiring stays clean"
else
    fail "reusable-workflow job with: wiring stays clean" "expected exit 0, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/job-with-wiring.yml"

# Test 18: flow-style with: mapping -> FAIL (cannot be statically validated)
cat >"${wf_dir}/flow-with.yml" <<'Y'
name: flow-with
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@abc123
        with: { args: "${{ inputs.command }}" }
Y

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]]; then
    pass "flow-style with: mapping is rejected as unvalidatable"
else
    fail "flow-style with: mapping is rejected as unvalidatable" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi

rm -f "${wf_dir}/flow-with.yml"

# Test 20: quoted flow-style keys (YAML-equivalent to with:) -> FAIL
cat >"${wf_dir}/flow-with-quoted.yml" <<'Y'
name: flow-with-quoted
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@abc123
        "with": { args: "${{ inputs.command }}" }
      - uses: actions/github-script@def456
        'with': { args: "${{ inputs.command }}" }
Y

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] \
    && grep -q "flow-style 'with:' mapping cannot be statically validated" "${output_file}"; then
    pass "quoted flow-style with: keys are rejected as unvalidatable"
else
    fail "quoted flow-style with: keys are rejected" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi

rm -f "${wf_dir}/flow-with-quoted.yml"

# Test 21: quoted block-style keys still track command inputs -> FAIL
cat >"${wf_dir}/block-with-quoted.yml" <<'Y'
name: block-with-quoted
on:
  workflow_dispatch:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@abc123
        'with':
          args: "${{ inputs.command }}"
      - uses: actions/setup-node@def456
        "with":
          node-version: 20
Y

"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] \
    && grep -q "directly interpolated in an action 'with:' command input" "${output_file}"; then
    pass "quoted block-style with: keys still track command inputs"
else
    fail "quoted block-style with: keys still track command inputs" "expected exit 1, got ${exit_code}"
    cat "${output_file}" >&2
fi

rm -f "${wf_dir}/block-with-quoted.yml"

# Test 20: a key-shaped line inside a block scalar must not reset the tracked
# with: key -- the scalar content is opaque text, so both the branch-shaped
# line itself and a later command input stay judged under the real key.
cat >"${wf_dir}/with-scalar-keys.yml" <<'Y'
name: with-scalar-keys
on:
  workflow_dispatch:
    inputs:
      branch:
        description: 'branch'
        required: false
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - name: Scan
        uses: example/action@0000000000000000000000000000000000000000
        with:
          args: >-
            branch: ${{ inputs.branch }}
            -Dsonar.pullrequest.key=${{ inputs.branch }}
Y
"${DETECTOR[@]}" "${wf_dir}" >"${output_file}" 2>&1
exit_code=$?
if [[ ${exit_code} -eq 1 ]] && grep -Fq "with:" "${output_file}"; then
    pass "key-shaped lines inside a block scalar cannot hide command inputs"
else
    fail "key-shaped lines inside a block scalar cannot hide command inputs" "expected exit 1 + diagnostic, got ${exit_code}"
    cat "${output_file}" >&2
fi
rm -f "${wf_dir}/with-scalar-keys.yml"

# Test 19: no arguments must succeed under bash 3.2 with set -euo pipefail
# (the bare "$@" list is treated as unset there; the ${1+"$@"} guard keeps
# the default workflow directory in use and the run clean).
no_args_output=""
no_args_rc=0
if no_args_output="$(cd "${SCRIPT_DIR}/../.." && "${DETECTOR[@]}" 2>&1)"; then
    no_args_rc=0
else
    no_args_rc=$?
fi
if [[ ${no_args_rc} -eq 0 ]]; then
    pass "no-argument invocation succeeds (default workflows dir)"
else
    fail "no-argument invocation succeeds" "expected exit 0, got ${no_args_rc}: ${no_args_output}"
fi

printf '\n%d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
    exit 1
fi
exit 0
