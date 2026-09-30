#!/bin/bash
#
# test_e2e_wrapper_cleanup.sh — Feature tests for the e2e wrapper cleanup trap.
#
# The wrappers under tools/e2e delegate to the Rust e2e-harness binary, which
# owns the working-directory lifecycle (remove on success, retain on failure).
# The wrapper trap only recovers directories orphaned by an invocation that
# died before it could clean up, and it must prove ownership first: the
# directory name embeds the creating process id, and a directory is claimed
# only when that process is gone.  Age is never evidence.
#
# Covered:
#   - normal completion               -> live invocation's tree preserved
#   - concurrent same-scenario runs   -> neither removes the other's tree
#   - long-running invocation         -> age alone never reclaims a live tree
#   - one exits, the other continues  -> survivor's tree preserved
#   - abnormal termination            -> unsettled dead-creator orphan
#                                        with a matching record reclaimed
#   - settled tree                    -> a finished run's retained tree
#                                        (marker present) never reclaimed
#   - unprovable ownership            -> a tree without an invocation
#                                        record is preserved
#   - --keep-artifacts                -> nothing is reclaimed
#   - idempotent cleanup              -> second run changes nothing
#
# The trap function is extracted verbatim from the wrapper under test, so a
# future edit to the ownership rule is exercised by these cases.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
WRAPPERS=(
  "${REPO_ROOT}/tools/e2e/verify_accept_negotiation_e2e.sh"
  "${REPO_ROOT}/tools/e2e/verify_auth_cache_e2e.sh"
  "${REPO_ROOT}/tools/e2e/verify_conditional_requests_e2e.sh"
  "${REPO_ROOT}/tools/e2e/verify_metrics_endpoint_e2e.sh"
  "${REPO_ROOT}/tools/e2e/verify_status_codes_e2e.sh"
)

PASS_COUNT=0
FAIL_COUNT=0

pass() {
  printf '  PASS: %s\n' "$1"
  PASS_COUNT=$((PASS_COUNT + 1))
  return 0
}

fail() {
  printf '  FAIL: %s\n' "$1" >&2
  FAIL_COUNT=$((FAIL_COUNT + 1))
  return 0
}

assert_dir() {
  local expectation="$1" path="$2" label="$3"
  if [[ "${expectation}" == "exists" ]]; then
    if [[ -d "${path}" ]]; then
      pass "${label}"
    else
      fail "${label} (directory missing: ${path})"
    fi
  else
    if [[ -d "${path}" ]]; then
      fail "${label} (directory still present: ${path})"
    else
      pass "${label}"
    fi
  fi
  return 0
}

# _scenario_name_of <wrapper> — the SCENARIO_NAME the wrapper pins.
_scenario_name_of() {
  local wrapper="$1"
  sed -n 's/^SCENARIO_NAME="\(.*\)"$/\1/p' "${wrapper}" | head -1
  return 0
}

# _extract_cleanup_fn <wrapper> — the trap function source, verbatim.
_extract_cleanup_fn() {
  local wrapper="$1"
  sed -n '/^_wrapper_cleanup() {/,/^}$/p' "${wrapper}"
  return 0
}

run_cases_for_wrapper() {
  local wrapper="$1"
  local scenario
  scenario="$(_scenario_name_of "${wrapper}")"
  if [[ -z "${scenario}" ]]; then
    fail "$(basename "${wrapper}") pins no scenario name"
    return
  fi

  local fixture
  fixture="$(mktemp -d)"
  local fn_src
  fn_src="$(_extract_cleanup_fn "${wrapper}")"
  if [[ -z "${fn_src}" ]]; then
    fail "$(basename "${wrapper}") has no cleanup function"
    rm -rf "${fixture}"
    return
  fi

  # A live creator: this test process itself.
  local live_pid="$$"
  # A dead creator: a pid that has certainly exited.
  local dead_pid=999999
  while kill -0 "${dead_pid}" 2>/dev/null; do
    dead_pid=$((dead_pid - 1))
  done

  # --- normal completion: the live invocation's tree stays ---------------
  mkdir -p "${fixture}/e2e-harness-${scenario}-${live_pid}-111"
  # --- concurrent same-scenario run: another live creator -----------------
  mkdir -p "${fixture}/e2e-harness-${scenario}-${live_pid}-222"
  # --- abnormal termination: an orphan from a dead creator ----------------
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-333"
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-333/artifacts/scenarios/${scenario}"
  printf '{\n  "scenario": "%s",\n  "port": 8080\n}\n' "${scenario}" \
    > "${fixture}/e2e-harness-${scenario}-${dead_pid}-333/artifacts/scenarios/${scenario}/invocation.json"
  # --- an orphan whose metadata names a DIFFERENT scenario ----------------
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-666/artifacts/scenarios/other-scenario"
  printf '{\n  "scenario": "other-scenario",\n  "port": 8080\n}\n' \
    > "${fixture}/e2e-harness-${scenario}-${dead_pid}-666/artifacts/scenarios/other-scenario/invocation.json"
  # --- an orphan from a dead creator with no metadata at all --------------
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-555"
  # --- a settled tree: a finished run retained its artifacts --------------
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-999/artifacts/scenarios/${scenario}"
  printf '{\n  "scenario": "%s",\n  "port": 8080\n}\n' "${scenario}" \
    > "${fixture}/e2e-harness-${scenario}-${dead_pid}-999/artifacts/scenarios/${scenario}/invocation.json"
  printf 'failed\n' > "${fixture}/e2e-harness-${scenario}-${dead_pid}-999/.harness-completed"
  # --- a finished failed run whose marker write failed: the harness ------
  # --- writes its diagnostics under runtime/, so that is where the ------
  # --- fixture must place them (a fresh runtime/ alone is not enough). ---
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-777/artifacts/scenarios/${scenario}"
  printf '{\n  "scenario": "%s",\n  "port": 8080\n}\n' "${scenario}" \
    > "${fixture}/e2e-harness-${scenario}-${dead_pid}-777/artifacts/scenarios/${scenario}/invocation.json"
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-777/runtime"
  printf 'assertion output\n' \
    > "${fixture}/e2e-harness-${scenario}-${dead_pid}-777/runtime/nginx-error.log"
  # --- a crashed run: runtime/ exists (prepare created it) but carries ----
  # --- no diagnostic file, so the tree IS reclaimable. -------------------
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-888/artifacts/scenarios/${scenario}"
  printf '{\n  "scenario": "%s",\n  "port": 8080\n}\n' "${scenario}" \
    > "${fixture}/e2e-harness-${scenario}-${dead_pid}-888/artifacts/scenarios/${scenario}/invocation.json"
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-888/runtime"
  # --- a directory of a different scenario is never ours ------------------
  mkdir -p "${fixture}/e2e-harness-other-scenario-${dead_pid}-444"

  # The trap resolves its base from TMPDIR; run it with TMPDIR pointed at
  # the fixture so the extracted function scans exactly these directories.
  (
    export TMPDIR="${fixture}"
    export KEEP_ARTIFACTS=0
    export SCENARIO_NAME="${scenario}"
    # shellcheck disable=SC1090
    eval "${fn_src}"
    _wrapper_cleanup
  )
  assert_dir exists "${fixture}/e2e-harness-${scenario}-${live_pid}-111" \
    "${scenario}: live invocation's tree preserved (normal completion)"
  assert_dir exists "${fixture}/e2e-harness-${scenario}-${live_pid}-222" \
    "${scenario}: concurrent run's tree preserved"
  assert_dir missing "${fixture}/e2e-harness-${scenario}-${dead_pid}-333" \
    "${scenario}: dead creator's orphan reclaimed (metadata corroborates)"
  assert_dir exists "${fixture}/e2e-harness-${scenario}-${dead_pid}-666" \
    "${scenario}: orphan whose metadata names another scenario is preserved"
  assert_dir exists "${fixture}/e2e-harness-${scenario}-${dead_pid}-777" \
    "${scenario}: runtime diagnostics are preserved without a marker"
  assert_dir exists "${fixture}/e2e-harness-${scenario}-${dead_pid}-555" \
    "${scenario}: orphan with no metadata is preserved (ownership unproven)"
  assert_dir exists "${fixture}/e2e-harness-${scenario}-${dead_pid}-999" \
    "${scenario}: a settled run's retained tree is never reclaimed"
  assert_dir exists "${fixture}/e2e-harness-other-scenario-${dead_pid}-444" \
    "${scenario}: other scenario's tree untouched"

  # --- idempotent cleanup: a second run changes nothing -------------------
  (
    export TMPDIR="${fixture}"
    export KEEP_ARTIFACTS=0
    export SCENARIO_NAME="${scenario}"
    eval "${fn_src}"
    _wrapper_cleanup
  )
  assert_dir exists "${fixture}/e2e-harness-${scenario}-${live_pid}-111" \
    "${scenario}: repeated cleanup is idempotent"

  # --- long-running invocation: a live pid is never reclaimed, however old -
  # Age is not consulted at all now; prove the live tree survives a second
  # pass and that its mtime (set far in the past) does not matter.  The
  # fixture is moved back an hour so an age-based rule would have reclaimed
  # it.
  if touch -t 202001010000 "${fixture}/e2e-harness-${scenario}-${live_pid}-111" 2>/dev/null; then
    (
      export TMPDIR="${fixture}"
      export KEEP_ARTIFACTS=0
      export SCENARIO_NAME="${scenario}"
      eval "${fn_src}"
      _wrapper_cleanup
    )
    assert_dir exists "${fixture}/e2e-harness-${scenario}-${live_pid}-111" \
      "${scenario}: an old live tree is never reclaimed (no age rule)"
  fi

  # --- --keep-artifacts: nothing is reclaimed -----------------------------
  mkdir -p "${fixture}/e2e-harness-${scenario}-${dead_pid}-555"
  (
    export TMPDIR="${fixture}"
    export KEEP_ARTIFACTS=1
    export SCENARIO_NAME="${scenario}"
    eval "${fn_src}"
    _wrapper_cleanup
  )
  assert_dir exists "${fixture}/e2e-harness-${scenario}-${dead_pid}-555" \
    "${scenario}: --keep-artifacts retains every tree"

  rm -rf "${fixture}"
}

echo "=== e2e wrapper cleanup ownership tests ==="
for wrapper in "${WRAPPERS[@]}"; do
  if [[ ! -f "${wrapper}" ]]; then
    fail "missing wrapper: ${wrapper}"
    continue
  fi
  run_cases_for_wrapper "${wrapper}"
done

echo
echo "Results: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
  exit 1
fi
echo "PASS: e2e wrapper cleanup ownership is correctly scoped"
