#!/bin/bash
#
# Removed-Directive / Removed-Value Rejection Test (LTS-R008, LTS-R010)
#
# Validates the frozen 0.9.2 removal contract with `nginx -t`:
#
#   - The five removed directives are no longer registered: `nginx -t` fails
#     with NGINX's standard `unknown directive` error naming the directive,
#     and the oracle asserts exactly that wording (no module-specific
#     migration text exists for them).
#     - markdown_dynamic_config
#     - markdown_dynamic_config_path
#     - markdown_dynconf_dry_run
#     - markdown_prune_selectors
#     - markdown_prune_protection_selectors
#
#   - `markdown_accept wildcard` (a removed VALUE of a retained directive)
#     still answers with an explicit migration message naming the removed
#     value; the oracle matches that stable phrase.
#
# A negative control places an ordinary unknown directive under a path that
# CONTAINS "removed" and requires the same standard-unknown contract, proving
# the oracle does not pass just because a path echoes a marker word.
#
# This test uses `nginx -t` to validate configuration acceptance. It does NOT
# require a running NGINX instance — only a compiled binary with the markdown
# module loaded.
#
# TEST-FIRST NOTE: On code where these directives/values are still accepted
# (before the removal in the paired implementation tasks), every expectation
# here fails because `nginx -t` still exits zero. That failure is the correct
# test-first signal; the test passes only once removal + migration handlers
# land.
#
# Requirements:
#   - NGINX compiled with the markdown filter module (set NGINX_BIN or have
#     nginx in PATH)
#   - macOS bash 3.2 compatible (Rule 11)
#
# Usage:
#   ./test_removed_directives.sh [--nginx-bin PATH] [--keep-artifacts] [-h]
#
# Exit codes:
#   0 - Every removed item rejected with the expected standard/migration error
#   1 - One or more removed items accepted, or rejected without the expected
#       error wording
#   2 - Usage error or missing prerequisites
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NGINX_BIN="${NGINX_BIN:-}"
KEEP_ARTIFACTS=0

# Only execute a binary from the repository's canonical test build or from
# fixed system installation locations.  A PATH entry supplied by a caller is
# not trusted merely because it is executable.
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

# Standard error required for the five removed directives: NGINX reports an
# unknown directive, and the message must name the directive that was used.
STANDARD_UNKNOWN_MARKER='unknown directive'

# The removed *value* (markdown_accept wildcard) keeps a migration message;
# match its stable phrase rather than loose words so an unrelated failure
# cannot satisfy the oracle.
WILDCARD_MIGRATION_MARKER='markdown_accept wildcard.*was removed in 0\.9\.2'

TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

TMPDIR_BASE=""

usage() {
  cat <<EOF >&2
Usage: $(basename "$0") [--nginx-bin PATH] [--keep-artifacts] [-h|--help]

Validate removed directive/value rejection with nginx -t (LTS-R008, LTS-R010).

Options:
  --nginx-bin PATH     Path to NGINX binary with markdown module
  --keep-artifacts     Keep temporary config files after run
  -h, --help           Show this help message

Environment:
  NGINX_BIN            Alternative to --nginx-bin flag
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

log_test() {
  local desc="$1"
  TESTS_RUN=$((TESTS_RUN + 1))
  printf "  [%03d] %s ... " "${TESTS_RUN}" "${desc}" >&2
  return 0
}

log_pass() {
  TESTS_PASSED=$((TESTS_PASSED + 1))
  echo "ok" >&2
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

# Write a config containing the given http-context line(s) and run nginx -t.
# Returns non-zero when nginx -t unexpectedly accepts the configuration.
#
#   $1 - conf file path
#   $2 - log file path
#   $3 - pid file path
#   $4 - the http-context config line(s) exercising the removed item
run_rejection_probe() {
  local conf_file="$1"
  local log_file="$2"
  local pid_file="$3"
  local http_line="$4"

  cat > "${conf_file}" <<EOF
worker_processes 1;
error_log /dev/null crit;
pid ${pid_file};

events { worker_connections 64; }

http {
    markdown_filter on;
EOF
  printf '    %s\n\n' "${http_line}" >> "${conf_file}"
  cat >> "${conf_file}" <<EOF
    server {
        listen 127.0.0.1:19998;
        location / {
            empty_gif;
        }
    }
}
EOF

  if "${NGINX_BIN}" -t -c "${conf_file}" >"${log_file}" 2>&1; then
    return 1
  fi
  return 0
}

# Standard-unknown oracle for the five removed directives: nginx -t must
# fail, name the directive, and use NGINX's standard `unknown directive`
# wording. The marker match is case-insensitive so it survives capitalization
# changes, while the directive name pins which failure actually happened.
#
#   $1 - test ID
#   $2 - description
#   $3 - an ERE naming the directive that must appear in the error
#   $4 - the http-context config line exercising the removed directive
#   $5 - optional subdirectory under TMPDIR_BASE (used by the path control)
expect_standard_unknown_rejection() {
  local test_id="$1"
  local description="$2"
  local directive_token="$3"
  local http_line="$4"
  local subdir="${5:-}"

  local case_dir="${TMPDIR_BASE}"
  if [[ -n "${subdir}" ]]; then
    case_dir="${TMPDIR_BASE}/${subdir}"
    mkdir -p "${case_dir}"
  fi
  local conf_file="${case_dir}/case_${test_id}.conf"
  local log_file="${case_dir}/case_${test_id}.log"
  local pid_file="${case_dir}/case_${test_id}.pid"

  log_test "${description}"

  if ! run_rejection_probe "${conf_file}" "${log_file}" "${pid_file}" "${http_line}"; then
    log_fail "expected nginx -t to reject the removed directive, but it was accepted"
    return 0
  fi
  if ! grep -Eq "${directive_token}" "${log_file}" 2>/dev/null; then
    log_fail "nginx -t failed but the error did not name '${directive_token}': $(tail -n 2 "${log_file}" 2>/dev/null || echo 'see log')"
    return 0
  fi
  if ! grep -Eqi "${STANDARD_UNKNOWN_MARKER}" "${log_file}" 2>/dev/null; then
    log_fail "nginx -t rejected '${directive_token}' without the standard '${STANDARD_UNKNOWN_MARKER}' wording: $(tail -n 2 "${log_file}" 2>/dev/null || echo 'see log')"
    return 0
  fi

  log_pass
  return 0
}

# Migration oracle for a removed VALUE of a retained directive: the error
# must name the removed item and carry the explicit migration phrase.
expect_migration_rejection() {
  local test_id="$1"
  local description="$2"
  local removed_token="$3"
  local http_line="$4"

  local conf_file="${TMPDIR_BASE}/case_${test_id}.conf"
  local log_file="${TMPDIR_BASE}/case_${test_id}.log"
  local pid_file="${TMPDIR_BASE}/case_${test_id}.pid"

  log_test "${description}"

  if ! run_rejection_probe "${conf_file}" "${log_file}" "${pid_file}" "${http_line}"; then
    log_fail "expected nginx -t to reject the removed item, but it was accepted"
    return 0
  fi
  if ! grep -Eq "${removed_token}" "${log_file}" 2>/dev/null; then
    log_fail "nginx -t failed but the error did not name '${removed_token}': $(tail -n 2 "${log_file}" 2>/dev/null || echo 'see log')"
    return 0
  fi
  if ! grep -Eq "${WILDCARD_MIGRATION_MARKER}" "${log_file}" 2>/dev/null; then
    log_fail "nginx -t rejected '${removed_token}' without the migration phrase: $(tail -n 2 "${log_file}" 2>/dev/null || echo 'see log')"
    return 0
  fi

  log_pass
  return 0
}

# --- Main ---
resolve_nginx_bin || exit 2

TMPDIR_BASE="$(mktemp -d /tmp/md-compat.XXXXXX)"

cleanup_tmpdir() {
  if [[ "${KEEP_ARTIFACTS}" -eq 0 ]]; then
    rm -rf "${TMPDIR_BASE}"
  else
    echo "Artifacts kept in: ${TMPDIR_BASE}" >&2
  fi
}
trap cleanup_tmpdir EXIT

# Preflight: verify the resolved NGINX binary accepts a minimal markdown-module
# configuration before running any expectation, so an unavailable or unusable
# module cannot count as a passing rejection (preflight).
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

echo "==========================================================" >&2
echo " Removed-Directive / Removed-Value Rejection Test (Property 14)" >&2
echo " NGINX binary: ${NGINX_BIN}" >&2
echo " Temp dir: ${TMPDIR_BASE}" >&2
echo "==========================================================" >&2
echo "" >&2

echo "--- Removed dynconf directives (LTS-R008) ---" >&2
expect_standard_unknown_rejection 1 "markdown_dynamic_config rejected as unknown directive" \
  "markdown_dynamic_config" \
  "markdown_dynamic_config on;"
expect_standard_unknown_rejection 2 "markdown_dynamic_config_path rejected as unknown directive" \
  "markdown_dynamic_config_path" \
  "markdown_dynamic_config_path /etc/nginx/markdown_dynamic.conf;"
expect_standard_unknown_rejection 3 "markdown_dynconf_dry_run rejected as unknown directive" \
  "markdown_dynconf_dry_run" \
  "markdown_dynconf_dry_run on;"

echo "--- Removed custom-selector directives (LTS-R008, LTS-R009) ---" >&2
expect_standard_unknown_rejection 4 "markdown_prune_selectors rejected as unknown directive" \
  "markdown_prune_selectors" \
  "markdown_prune_selectors \"nav footer aside\";"
expect_standard_unknown_rejection 5 "markdown_prune_protection_selectors rejected as unknown directive" \
  "markdown_prune_protection_selectors" \
  "markdown_prune_protection_selectors \"nav\";"

echo "--- Development-line retirements (0.9.2) ---" >&2
expect_standard_unknown_rejection 6 "markdown_profile rejected as unknown directive" \
  "markdown_profile" \
  "markdown_profile balanced;"
expect_standard_unknown_rejection 7 "markdown_streaming_zero_copy rejected as unknown directive" \
  "markdown_streaming_zero_copy" \
  "markdown_streaming_zero_copy on;"

echo "--- Removed markdown_accept value (LTS-R010) ---" >&2
expect_migration_rejection 6 "markdown_accept wildcard value rejected with migration" \
  "markdown_accept|wildcard" \
  "markdown_accept wildcard;"

echo "--- Negative control: the standard-unknown oracle ignores the pathname ---" >&2
expect_standard_unknown_rejection 7 "ordinary unknown directive under a 'removed' path" \
  "markdown_zz_unknown_control" \
  "markdown_zz_unknown_control on;" \
  "removed-control"

echo "" >&2
echo "==========================================================" >&2
echo " Results: ${TESTS_PASSED} passed, ${TESTS_FAILED} failed" >&2
echo "==========================================================" >&2

if [[ "${TESTS_FAILED}" -eq 0 ]]; then
  echo "PASS: all ${TESTS_RUN} removed-item rejection expectations met" >&2
  exit 0
fi

echo "FAIL: ${TESTS_FAILED} of ${TESTS_RUN} removed-item rejection expectations violated" >&2
exit 1
