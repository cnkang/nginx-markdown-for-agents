#!/usr/bin/env bash
#
# test_detect_ci_supply_chain.sh - Unit tests for the CI supply-chain detector.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECTOR="${SCRIPT_DIR}/../detect_ci_supply_chain.sh"

PASS_COUNT=0
FAIL_COUNT=0

pass() {
    local msg="$1"

    PASS_COUNT=$((PASS_COUNT + 1))
    printf '  PASS: %s\n' "$msg"
    return 0
}

fail() {
    local msg="$1"
    local detail="${2:-}"

    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf '  FAIL: %s\n' "$msg" >&2
    if [[ -n "$detail" ]]; then
        printf '        Detail: %s\n' "$detail" >&2
    fi
    return 0
}

printf 'Unit Tests: detect_ci_supply_chain.sh\n'

if bash -n "$DETECTOR" 2>/dev/null; then
    pass "detector has valid bash syntax"
else
    fail "detector has valid bash syntax" "bash -n failed"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/ci-supply-chain.XXXXXX")" || {
    fail "create temp fixture directory" "mktemp failed"
    exit 1
}
trap 'rm -rf "$tmp_dir"' EXIT

workflow_dir="${tmp_dir}/.github/workflows"
mkdir -p "$workflow_dir"
output_file="${tmp_dir}/detector.out"

cat >"${workflow_dir}/clean.yml" <<'YAML'
name: clean
on: workflow_dispatch
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
      - run: |
          curl --proto '=https' --tlsv1.2 -fsSLo tool https://example.test/tool
          chmod 0755 tool
YAML

exit_code=0
(cd "$tmp_dir" && bash "$DETECTOR") >"$output_file" 2>&1 || exit_code=$?
if [[ "$exit_code" -eq 0 ]]; then
    pass "pinned action and file download -> exit 0"
else
    fail "pinned action and file download -> exit 0" "got exit ${exit_code}"
fi

cat >"${workflow_dir}/curl-pipe.yml" <<'YAML'
name: curl-pipe
on: workflow_dispatch
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
      - run: |
          curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
            | sh -s -- -y
YAML

exit_code=0
(cd "$tmp_dir" && bash "$DETECTOR") >"$output_file" 2>&1 || exit_code=$?
if [[ "$exit_code" -ne 0 ]] &&
    grep -q 'Network-to-shell execution' "$output_file"; then
    pass "multiline curl pipe to sh -> exit nonzero"
else
    fail "multiline curl pipe to sh -> exit nonzero" "got exit ${exit_code}"
fi

rm -f "${workflow_dir}/curl-pipe.yml"
cat >"${workflow_dir}/process-substitution.yml" <<'YAML'
name: process-substitution
on: workflow_dispatch
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd
      - run: bash <(curl -fsSL https://example.test/install.sh)
YAML

exit_code=0
(cd "$tmp_dir" && bash "$DETECTOR") >"$output_file" 2>&1 || exit_code=$?
if [[ "$exit_code" -ne 0 ]] &&
    grep -q 'Network-to-shell execution' "$output_file"; then
    pass "bash process substitution from curl -> exit nonzero"
else
    fail "bash process substitution from curl -> exit nonzero" "got exit ${exit_code}"
fi

if [[ "$FAIL_COUNT" -ne 0 ]]; then
    printf '\n%d test(s) failed\n' "$FAIL_COUNT" >&2
    exit 1
fi

printf '\nAll %d tests passed\n' "$PASS_COUNT"
exit 0
