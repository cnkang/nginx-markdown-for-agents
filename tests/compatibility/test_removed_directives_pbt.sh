#!/bin/bash
#
# Property 14 (property-based layer): Every removed directive or value causes an
# explicit `nginx -t` failure with migration guidance (LTS-R008, LTS-R010).
#
# Feature: pre-lts-convergence-092, Property 14: Every removed directive or
# value causes an explicit `nginx -t` failure with migration guidance
#
# This is the property-based-testing (PBT) layer that complements the fixed
# example harness in tests/compatibility/test_removed_directives.sh (task 4.1).
# The example harness pins one canonical config per removed item; this layer
# quantifies universally over the *input space* of a removed reference:
# randomized valid argument shapes, randomized valid placements/context, and
# randomized surrounding-config noise. For each of >=100 generated
# configurations that reference a removed directive/value, the property asserts:
#
#   (1) `nginx -t` exits non-zero (the removed item is never silently accepted),
#   and
#   (2) the `nginx -t` output names the removed item AND signals a migration.
#
# Removed directives (Property 14; Requirements 8.1, 8.2, 10.2):
#   - markdown_dynamic_config
#   - markdown_dynamic_config_path
#   - markdown_dynconf_dry_run
#   - markdown_prune_selectors
#   - markdown_prune_protection_selectors
# Removed directive value:
#   - markdown_accept wildcard
#
# The config properties (design Property 4, 14) drive a generated-config harness
# that invokes REAL `nginx -t` rather than approximating with text matching, so
# behavior is confirmed on a real parser. See design "Testing Strategy /
# Dual approach".
#
# TEST-FIRST NOTE: On a binary where these directives/values are still accepted
# (before the removal + migration handlers of tasks 4.2/9/10 land), every
# iteration fails because `nginx -t` still exits zero. That failure is the
# correct test-first signal; the property passes only once removal + migration
# handlers exist.
#
# Requirements:
#   - NGINX compiled with the markdown filter module (set NGINX_BIN or have
#     nginx in PATH). When no module-enabled binary is available, use
#     --dry-run to author/verify the generated configs and iteration structure
#     without invoking nginx (the real `nginx -t` assertions are then deferred
#     to a module-enabled host).
#   - macOS bash 3.2 compatible (Rule 11); [[ ]] conditionals, stderr
#     diagnostics, explicit returns, default case (Rule 18).
#
# Usage:
#   ./test_removed_directives_pbt.sh [--nginx-bin PATH] [--iterations N] \
#       [--seed N] [--dry-run] [--keep-artifacts] [-h|--help]
#
# Exit codes:
#   0 - All generated iterations rejected with a migration message (or, under
#       --dry-run, all generated configs are well-formed and the iteration
#       count invariant holds)
#   1 - One or more iterations accepted a removed item, or rejected it without
#       a migration message
#   2 - Usage error or missing prerequisites
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NGINX_BIN="${NGINX_BIN:-}"
KEEP_ARTIFACTS=0
DRY_RUN=0

# Property iteration budget. The design mandates >=100 iterations per property;
# ITERATIONS is the total number of generated-config trials across all removed
# items and is validated against MIN_ITERATIONS below.
MIN_ITERATIONS=100
ITERATIONS="${PBT_ITERATIONS:-120}"

# Deterministic-by-default seed so a failing counterexample is reproducible.
# Override with --seed for a fresh exploration; the chosen seed is always
# printed so any discovered failure can be replayed.
SEED="${PBT_SEED:-1492}"

# Only execute a binary from the repository's canonical test build or from
# fixed system installation locations. A PATH entry supplied by a caller is not
# trusted merely because it is executable. (Mirrors the example harness.)
TRUSTED_NGINX_PATHS=(
  "${REPO_ROOT}/components/nginx-module/tests/build/nginx"
  "${REPO_ROOT}/build/nginx"
  "/usr/bin/nginx"
  "/usr/sbin/nginx"
  "/usr/local/bin/nginx"
  "/usr/local/sbin/nginx"
  "/opt/homebrew/bin/nginx"
  "/opt/homebrew/sbin/nginx"
)

# A removed item is considered rejected "with migration guidance" when the
# nginx -t error output signals a migration. Matching the migration signal (not
# only the directive name) prevents an unrelated syntax error that happens to
# mention the directive from counting as a valid migration rejection.
MIGRATION_MARKERS='removed|migrat|no longer|static config'

TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

TMPDIR_BASE=""

usage() {
  cat <<EOF >&2
Usage: $(basename "$0") [--nginx-bin PATH] [--iterations N] [--seed N] \\
    [--dry-run] [--keep-artifacts] [-h|--help]

Property 14 (PBT layer): every removed directive/value fails nginx -t with
migration guidance, over >=${MIN_ITERATIONS} randomized generated configs.

Options:
  --nginx-bin PATH     Path to NGINX binary with markdown module
  --iterations N       Total generated-config trials (default ${ITERATIONS};
                       must be >= ${MIN_ITERATIONS})
  --seed N             PRNG seed for reproducible generation (default ${SEED})
  --dry-run            Generate + structurally validate configs without running
                       nginx (real nginx -t assertions deferred)
  --keep-artifacts     Keep temporary config files after run
  -h, --help           Show this help message

Environment:
  NGINX_BIN            Alternative to --nginx-bin flag
  PBT_ITERATIONS       Alternative to --iterations
  PBT_SEED             Alternative to --seed
EOF
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --nginx-bin)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --nginx-bin requires a value" >&2
        usage
        exit 2
      fi
      NGINX_BIN="$2"
      shift 2
      ;;
    --iterations)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --iterations requires a value" >&2
        usage
        exit 2
      fi
      ITERATIONS="$2"
      shift 2
      ;;
    --seed)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --seed requires a value" >&2
        usage
        exit 2
      fi
      SEED="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --keep-artifacts)
      KEEP_ARTIFACTS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

# Validate the iteration budget up front. A PBT run that silently drops below
# the mandated iteration count would masquerade as a passing property; fail
# closed instead (Rule 20 — verify shape before declaring pass).
if [[ ! "${ITERATIONS}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --iterations must be a non-negative integer: ${ITERATIONS}" >&2
  exit 2
fi
if [[ "${ITERATIONS}" -lt "${MIN_ITERATIONS}" ]]; then
  echo "ERROR: iterations (${ITERATIONS}) is below the mandated minimum ${MIN_ITERATIONS}" >&2
  exit 2
fi
if [[ ! "${SEED}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --seed must be a non-negative integer: ${SEED}" >&2
  exit 2
fi

canonicalize_existing_nginx_path() {
  local path="$1"
  local target=""
  local resolved_dir=""
  local hop=0

  if [[ "${path}" != /* || ! -f "${path}" || ! -x "${path}" ]]; then
    return 1
  fi
  while [[ -L "${path}" && "${hop}" -lt 40 ]]; do
    target="$(readlink "${path}")" || return 1
    if [[ "${target}" == /* ]]; then
      path="${target}"
    else
      path="$(dirname "${path}")/${target}"
    fi
    hop=$((hop + 1))
  done
  if [[ -L "${path}" ]]; then
    return 1
  fi
  resolved_dir="$(cd -P "$(dirname "${path}")" 2>/dev/null && pwd)" || return 1
  [[ -n "${resolved_dir}" ]] || return 1
  path="${resolved_dir}/$(basename "${path}")"
  [[ -f "${path}" && -x "${path}" ]] || return 1
  printf '%s\n' "${path}"
  return 0
}

resolve_nginx_bin() {
  local trusted_path=""
  local resolved_path=""
  local resolved_trusted_path=""
  local trusted_match=0

  if [[ -z "${NGINX_BIN}" ]]; then
    if command -v nginx >/dev/null 2>&1; then
      NGINX_BIN="$(command -v nginx)"
    else
      echo "ERROR: no nginx binary; set NGINX_BIN or add nginx to PATH" >&2
      return 1
    fi
  fi
  if [[ "${NGINX_BIN}" != /* ]]; then
    echo "ERROR: NGINX_BIN must be an absolute path: ${NGINX_BIN}" >&2
    return 1
  fi
  if ! resolved_path="$(canonicalize_existing_nginx_path "${NGINX_BIN}")"; then
    echo "ERROR: cannot canonicalize executable NGINX_BIN: ${NGINX_BIN}" >&2
    return 1
  fi

  for trusted_path in "${TRUSTED_NGINX_PATHS[@]}"; do
    if resolved_trusted_path="$(canonicalize_existing_nginx_path "${trusted_path}" \
      2>/dev/null)" && [[ "${resolved_path}" == "${resolved_trusted_path}" ]]; then
      trusted_match=1
      break
    fi
  done
  if [[ "${trusted_match}" -ne 1 ]]; then
    echo "ERROR: NGINX_BIN is outside the trusted executable allowlist: ${resolved_path}" >&2
    return 1
  fi
  NGINX_BIN="${resolved_path}"
  return 0
}

# --- Deterministic PRNG ------------------------------------------------------
#
# bash 3.2 has $RANDOM but its seeding across subshells is unreliable and not
# reproducible enough for a replayable counterexample. Use a small explicit
# linear congruential generator (glibc constants) driven by a single mutable
# state variable so the whole run is a pure function of SEED.
#
# CRITICAL: the PRNG mutates PRNG_STATE, which must persist across draws. In
# bash a command substitution `$(...)` runs in a SUBSHELL, so a generator that
# echoed its value would advance the PRNG only inside a throwaway subshell and
# every iteration would draw identically. To keep state, these helpers return
# their result via an out-parameter global (PRNG_RESULT / PRNG_PICK) and are
# called directly (never inside `$(...)`).
PRNG_STATE=0
PRNG_RESULT=0
PRNG_PICK=""

prng_seed() {
  local seed="$1"
  PRNG_STATE=$(( seed & 0x7fffffff ))
  return 0
}

# prng_next MODULO -> sets PRNG_RESULT to an integer in [0, MODULO)
prng_next() {
  local modulo="$1"
  # LCG: state = (state * 1103515245 + 12345) mod 2^31
  PRNG_STATE=$(( (PRNG_STATE * 1103515245 + 12345) & 0x7fffffff ))
  if [[ "${modulo}" -le 0 ]]; then
    PRNG_RESULT=0
    return 0
  fi
  PRNG_RESULT=$(( PRNG_STATE % modulo ))
  return 0
}

# prng_pick ELEM... -> sets PRNG_PICK to one element of the argument list.
prng_pick() {
  local count=$#
  prng_next "${count}"
  # Shift to the chosen positional argument (1-based).
  shift "${PRNG_RESULT}"
  PRNG_PICK="$1"
  return 0
}

# --- Generators --------------------------------------------------------------
#
# A "removed reference" is drawn from the 6 removed items. For each, the
# generator produces a valid *argument shape* (the module removed the item, so
# any well-formed argument that a legacy config could carry must still be
# rejected). Whitespace, quoting, and the enclosing context are randomized so
# the property is not accidentally tied to one canonical spelling.

# Random horizontal whitespace run (spaces / tabs), length 1..3 -> GEN_WS.
# Kept nonempty so the directive token stays separated from its argument.
GEN_WS=""
gen_ws() {
  local n=0
  local i=0
  GEN_WS=""
  prng_next 3
  n=$((PRNG_RESULT + 1))
  for (( i = 0; i < n; i++ )); do
    prng_pick ' ' '	' ' '
    GEN_WS="${GEN_WS}${PRNG_PICK}"
  done
  return 0
}

# Random leading indentation for a directive line, 0..8 spaces -> GEN_INDENT.
GEN_INDENT=""
gen_indent() {
  local n=0
  local i=0
  GEN_INDENT=""
  prng_next 9
  n="${PRNG_RESULT}"
  for (( i = 0; i < n; i++ )); do
    GEN_INDENT="${GEN_INDENT} "
  done
  return 0
}

# Generate a random single-token selector-ish argument (letters only) for the
# prune directives, length 3..8 -> GEN_SELECTOR.
GEN_SELECTOR=""
gen_selector_token() {
  local n=0
  local i=0
  GEN_SELECTOR=""
  prng_next 6
  n=$((PRNG_RESULT + 3))
  for (( i = 0; i < n; i++ )); do
    prng_pick a b c d e f g h n o p q r s t u v
    GEN_SELECTOR="${GEN_SELECTOR}${PRNG_PICK}"
  done
  return 0
}

# Generate a directive line for a chosen removed item + PRNG-varied argument
# shape. Sets two out-parameter globals:
#   GEN_LINE  - the http-context directive line (semicolon-terminated)
#   GEN_TOKEN - an ERE that must appear in the nginx -t error to confirm the
#               failure named the removed item (alternation allowed, e.g.
#               "markdown_accept|wildcard")
# Called directly (never in `$(...)`) so PRNG state persists (see PRNG note).
GEN_LINE=""
GEN_TOKEN=""
gen_removed_line() {
  local which=""
  local ws=""
  local ws2=""
  local arg=""
  local sel_a=""
  local sel_b=""
  local sel_c=""

  prng_pick \
    markdown_dynamic_config \
    markdown_dynamic_config_path \
    markdown_dynconf_dry_run \
    markdown_prune_selectors \
    markdown_prune_protection_selectors \
    markdown_accept_wildcard
  which="${PRNG_PICK}"

  gen_ws
  ws="${GEN_WS}"

  case "${which}" in
    markdown_dynamic_config)
      prng_pick on off ON Off
      arg="${PRNG_PICK}"
      GEN_LINE="markdown_dynamic_config${ws}${arg};"
      GEN_TOKEN="markdown_dynamic_config"
      ;;
    markdown_dynamic_config_path)
      prng_pick \
        /etc/nginx/markdown_dynamic.conf \
        /tmp/md.conf \
        conf/markdown_dynamic.conf \
        ./dynamic.conf
      arg="${PRNG_PICK}"
      GEN_LINE="markdown_dynamic_config_path${ws}${arg};"
      GEN_TOKEN="markdown_dynamic_config_path"
      ;;
    markdown_dynconf_dry_run)
      prng_pick on off ON Off
      arg="${PRNG_PICK}"
      GEN_LINE="markdown_dynconf_dry_run${ws}${arg};"
      GEN_TOKEN="markdown_dynconf_dry_run"
      ;;
    markdown_prune_selectors)
      # 1..3 selector tokens, optionally quoted as a single argument.
      gen_selector_token; sel_a="${GEN_SELECTOR}"
      gen_selector_token; sel_b="${GEN_SELECTOR}"
      gen_selector_token; sel_c="${GEN_SELECTOR}"
      gen_ws; ws2="${GEN_WS}"
      prng_next 3
      case "${PRNG_RESULT}" in
        0) arg="${sel_a}" ;;
        1) arg="\"${sel_a}${ws2}${sel_b}\"" ;;
        *) arg="\"${sel_a}${ws2}${sel_b}${ws2}${sel_c}\"" ;;
      esac
      GEN_LINE="markdown_prune_selectors${ws}${arg};"
      GEN_TOKEN="markdown_prune_selectors"
      ;;
    markdown_prune_protection_selectors)
      gen_selector_token; sel_a="${GEN_SELECTOR}"
      gen_selector_token; sel_b="${GEN_SELECTOR}"
      gen_ws; ws2="${GEN_WS}"
      prng_next 2
      case "${PRNG_RESULT}" in
        0) arg="${sel_a}" ;;
        *) arg="\"${sel_a}${ws2}${sel_b}\"" ;;
      esac
      GEN_LINE="markdown_prune_protection_selectors${ws}${arg};"
      GEN_TOKEN="markdown_prune_protection_selectors"
      ;;
    markdown_accept_wildcard)
      # Removed *value* of a retained directive: markdown_accept wildcard.
      GEN_LINE="markdown_accept${ws}wildcard;"
      GEN_TOKEN="markdown_accept|wildcard"
      ;;
    *)
      # Defensive default (Rule 18): treat an unexpected draw as a failure so a
      # generator regression cannot silently reduce coverage.
      echo "ERROR: generator produced unknown removed item: ${which}" >&2
      return 1
      ;;
  esac
  return 0
}

# Build a syntactically valid nginx config that embeds the removed directive
# line at a randomized placement (http, server, or location context — all
# contexts where the markdown directives are legal for a legacy config), plus
# a randomized amount of innocuous surrounding config, and write it to $1.
#   $1 - destination conf path
#   $2 - the http-context directive line to embed (with indentation applied by
#        the caller's placement choice)
#   $3 - the chosen placement: http | server | location
write_generated_config() {
  local conf_file="$1"
  local directive_line="$2"
  local placement="$3"
  local indent=""
  local extra_locations=0
  local i=0

  gen_indent
  indent="${GEN_INDENT}"
  prng_next 3
  extra_locations="${PRNG_RESULT}"

  {
    echo 'worker_processes 1;'
    echo 'error_log /dev/null crit;'
    printf 'pid %s.pid;\n' "${conf_file}"
    echo ''
    echo 'events { worker_connections 64; }'
    echo ''
    echo 'http {'
    echo '    markdown_filter on;'
    if [[ "${placement}" == "http" ]]; then
      printf '%s%s\n' "${indent}" "${directive_line}"
    fi
    echo '    server {'
    echo '        listen 127.0.0.1:19998;'
    if [[ "${placement}" == "server" ]]; then
      printf '%s%s\n' "${indent}" "${directive_line}"
    fi
    echo '        location / {'
    if [[ "${placement}" == "location" ]]; then
      printf '%s%s\n' "${indent}" "${directive_line}"
    fi
    echo "            empty_gif;"
    echo '        }'
    # Randomized innocuous extra locations so the removed line is surrounded by
    # varied, valid config rather than always appearing in isolation.
    for (( i = 0; i < extra_locations; i++ )); do
      printf '        location /extra%d/ {\n' "${i}"
      echo "            empty_gif;"
      echo '        }'
    done
    echo '    }'
    echo '}'
  } > "${conf_file}"
  return 0
}

log_fail() {
  local message="${1:-}"
  TESTS_FAILED=$((TESTS_FAILED + 1))
  echo "FAILED" >&2
  if [[ -n "${message}" ]]; then
    echo "       ${message}" >&2
  fi
  return 0
}

# Run one property iteration: generate a config referencing a removed item and
# assert the property holds. Under --dry-run, assert only that the config was
# generated and contains the removed token (structure/authoring check); the
# real nginx -t assertion is deferred.
#   $1 - iteration index
run_iteration() {
  local idx="$1"
  local directive_line=""
  local removed_token=""
  local placement=""
  local conf_file="${TMPDIR_BASE}/pbt_${idx}.conf"
  local log_file="${TMPDIR_BASE}/pbt_${idx}.log"

  if ! gen_removed_line; then
    log_fail "iteration ${idx}: generator error"
    return 0
  fi
  directive_line="${GEN_LINE}"
  removed_token="${GEN_TOKEN}"
  prng_pick http server location
  placement="${PRNG_PICK}"

  write_generated_config "${conf_file}" "${directive_line}" "${placement}"

  TESTS_RUN=$((TESTS_RUN + 1))

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    # Authoring/structure check: the removed directive token must appear in the
    # generated config, and the config must be non-trivial (has an http block).
    if ! grep -q 'http {' "${conf_file}" 2>/dev/null; then
      log_fail "iteration ${idx}: generated config missing http block"
      return 0
    fi
    # The token ERE may be alternation (markdown_accept|wildcard); confirm at
    # least one alternative is present in the emitted directive line.
    if ! printf '%s\n' "${directive_line}" | grep -Eq "${removed_token}" 2>/dev/null; then
      log_fail "iteration ${idx}: directive line does not contain removed token '${removed_token}': ${directive_line}"
      return 0
    fi
    TESTS_PASSED=$((TESTS_PASSED + 1))
    return 0
  fi

  # Real nginx -t property assertion.
  if "${NGINX_BIN}" -t -c "${conf_file}" >"${log_file}" 2>&1; then
    log_fail "iteration ${idx} [${placement}] '${directive_line}': nginx -t accepted a removed item (counterexample; replay with --seed ${SEED})"
    return 0
  fi
  if ! grep -Eq "${removed_token}" "${log_file}" 2>/dev/null; then
    log_fail "iteration ${idx} [${placement}] '${directive_line}': nginx -t failed but did not name '${removed_token}': $(tail -n 2 "${log_file}" 2>/dev/null || echo 'see log')"
    return 0
  fi
  if ! grep -Eiq "${MIGRATION_MARKERS}" "${log_file}" 2>/dev/null; then
    log_fail "iteration ${idx} [${placement}] '${directive_line}': rejected without a migration message: $(tail -n 2 "${log_file}" 2>/dev/null || echo 'see log')"
    return 0
  fi
  TESTS_PASSED=$((TESTS_PASSED + 1))
  return 0
}

# --- Main --------------------------------------------------------------------

if [[ "${DRY_RUN}" -ne 1 ]]; then
  resolve_nginx_bin || exit 2
fi

TMPDIR_BASE="$(mktemp -d /tmp/removed-directives-pbt.XXXXXX)"

cleanup_tmpdir() {
  if [[ "${KEEP_ARTIFACTS}" -eq 0 ]]; then
    rm -rf "${TMPDIR_BASE}"
  else
    echo "Artifacts kept in: ${TMPDIR_BASE}" >&2
  fi
}
trap cleanup_tmpdir EXIT

if [[ "${DRY_RUN}" -ne 1 ]]; then
  # Preflight: verify the resolved NGINX binary accepts a minimal markdown
  # config before running any iteration, so an unavailable/unusable module
  # cannot count as passing rejections (preflight).
  preflight_conf="${TMPDIR_BASE}/preflight.conf"
  preflight_log="${TMPDIR_BASE}/preflight.log"
  cat > "${preflight_conf}" <<EOF
worker_processes 1;
error_log /dev/null crit;
pid ${TMPDIR_BASE}/preflight.pid;

events { worker_connections 64; }

http {
    markdown_filter on;
    markdown_accept strict;
    server {
        listen 127.0.0.1:19998;
        location / {
            empty_gif;
        }
    }
}
EOF
  if ! "${NGINX_BIN}" -t -c "${preflight_conf}" >"${preflight_log}" 2>&1; then
    echo "ERROR: NGINX binary '${NGINX_BIN}' cannot load the markdown module:" >&2
    tail -n 5 "${preflight_log}" >&2
    exit 2
  fi
fi

echo "==========================================================" >&2
echo " Removed-Directive/Value Rejection PBT (Property 14)" >&2
if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo " Mode: DRY-RUN (config authoring/structure; real nginx -t deferred)" >&2
else
  echo " Mode: LIVE (real nginx -t)" >&2
  echo " NGINX binary: ${NGINX_BIN}" >&2
fi
echo " Iterations: ${ITERATIONS} (min ${MIN_ITERATIONS})" >&2
echo " Seed: ${SEED}" >&2
echo " Temp dir: ${TMPDIR_BASE}" >&2
echo "==========================================================" >&2
echo "" >&2

prng_seed "${SEED}"

iter=0
while [[ "${iter}" -lt "${ITERATIONS}" ]]; do
  run_iteration "${iter}"
  iter=$((iter + 1))
done

echo "" >&2
echo "==========================================================" >&2
echo " Results: ${TESTS_PASSED} passed, ${TESTS_FAILED} failed (of ${TESTS_RUN})" >&2
echo "==========================================================" >&2

# Iteration-count invariant: the property must have actually executed at least
# MIN_ITERATIONS trials. A run that produced fewer would be a non-property and
# must not be reported as a pass (Rule 20).
if [[ "${TESTS_RUN}" -lt "${MIN_ITERATIONS}" ]]; then
  echo "FAIL: only ${TESTS_RUN} iterations executed; property requires >= ${MIN_ITERATIONS}" >&2
  exit 1
fi

if [[ "${TESTS_FAILED}" -eq 0 ]]; then
  echo "PASS: Property 14 held across all ${TESTS_RUN} generated configs (seed ${SEED})" >&2
  exit 0
fi

echo "FAIL: Property 14 violated in ${TESTS_FAILED} of ${TESTS_RUN} generated configs (replay with --seed ${SEED})" >&2
exit 1
