#!/bin/bash
#
# Error Policy Value Acceptance Test
#
# Validates `markdown_error_policy` value acceptance via `nginx -t`:
#   - Accepted without error: pass, fail_closed, status 429, status 503
#   - Rejected at nginx -t time: any other value (bad, status 418,
#     status 502, status 500, status 0, status 65536, status abc)
#
# This test uses `nginx -t` to validate configuration syntax acceptance.
# It does NOT require a running NGINX instance — only a compiled binary
# with the markdown module loaded.
#
# Requirements:
#   - NGINX compiled with the markdown filter module (set NGINX_BIN or have
#     nginx in PATH)
#   - macOS bash 3.2 compatible (Rule 11)
#
# Usage:
#   ./test_error_policy_values.sh [--nginx-bin PATH] [-h|--help]
#
# Exit codes:
#   0 - All value-acceptance expectations met
#   1 - One or more expectations violated
#   2 - Usage error or missing prerequisites
#

set -euo pipefail

NGINX_BIN="${NGINX_BIN:-}"
KEEP_ARTIFACTS=0

TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

TMPDIR_BASE=""

usage() {
  cat <<EOF >&2
Usage: $(basename "$0") [--nginx-bin PATH] [--keep-artifacts] [-h|--help]

Validate markdown_error_policy value acceptance with nginx -t.

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

resolve_nginx_bin() {
  if [[ -z "${NGINX_BIN}" ]]; then
    if command -v nginx >/dev/null 2>&1; then
      NGINX_BIN="$(command -v nginx)"
    else
      echo "ERROR: no nginx binary; set NGINX_BIN or add nginx to PATH" >&2
      return 1
    fi
  fi
  if [[ ! -x "${NGINX_BIN}" ]]; then
    echo "ERROR: NGINX_BIN is not executable: ${NGINX_BIN}" >&2
    return 1
  fi
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

# Write a config with the given error_policy value and run nginx -t.
#
# Arguments:
#   $1 - test ID
#   $2 - description
#   $3 - error policy value line (e.g. "markdown_error_policy pass;")
#   $4 - expected result: "accept" or "reject"
validate_policy_value() {
  local test_id="$1"
  local description="$2"
  local policy_line="$3"
  local expected="$4"

  local conf_file="${TMPDIR_BASE}/policy_${test_id}.conf"
  local log_file="${TMPDIR_BASE}/policy_${test_id}.log"

  cat > "${conf_file}" <<EOF
worker_processes 1;
error_log /dev/null crit;
pid ${TMPDIR_BASE}/policy_${test_id}.pid;

events { worker_connections 64; }

http {
    markdown_filter on;
EOF
  printf '    %s\n\n' "${policy_line}" >> "${conf_file}"
  cat >> "${conf_file}" <<EOF
    server {
        listen 127.0.0.1:19998;
        location / {
            return 200 'ok';
        }
    }
}
EOF

  log_test "${description}"

  if "${NGINX_BIN}" -t -c "${conf_file}" >"${log_file}" 2>&1; then
    if [[ "${expected}" == "accept" ]]; then
      log_pass
    else
      log_fail "expected rejection, but nginx -t accepted the value"
    fi
  else
    if [[ "${expected}" == "reject" ]] && grep -q 'markdown_error_policy' "${log_file}" 2>/dev/null; then
      log_pass
    elif [[ "${expected}" == "reject" ]]; then
      log_fail "expected rejection for markdown_error_policy, but nginx -t failed for another reason: $(tail -n 2 "${log_file}" 2>/dev/null || echo 'see log')"
    else
      log_fail "$(tail -n 2 "${log_file}" 2>/dev/null || echo 'see log')"
    fi
  fi
  return 0
}

# --- Main ---
resolve_nginx_bin || exit 2

TMPDIR_BASE="$(mktemp -d /tmp/error-policy-values.XXXXXX)"

cleanup_tmpdir() {
  if [[ "${KEEP_ARTIFACTS}" -eq 0 ]]; then
    rm -rf "${TMPDIR_BASE}"
  else
    echo "Artifacts kept in: ${TMPDIR_BASE}" >&2
  fi
}
trap cleanup_tmpdir EXIT

# Preflight: verify the resolved NGINX binary accepts a minimal
# markdown-module configuration before running any expectation, so an
# unavailable or unusable module cannot count as a passing rejection
# (preflight).
preflight_conf="${TMPDIR_BASE}/preflight.conf"
preflight_log="${TMPDIR_BASE}/preflight.log"
cat > "${preflight_conf}" <<EOF
worker_processes 1;
error_log /dev/null crit;
pid ${TMPDIR_BASE}/preflight.pid;

events { worker_connections 64; }

http {
    markdown_filter on;
    markdown_error_policy pass;
    server {
        listen 127.0.0.1:19998;
        location / {
            return 200 'ok';
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
echo " Error Policy Value Acceptance Test (Property 26)" >&2
echo " NGINX binary: ${NGINX_BIN}" >&2
echo " Temp dir: ${TMPDIR_BASE}" >&2
echo "==========================================================" >&2
echo "" >&2

echo "--- Accepted values ---" >&2
validate_policy_value 1 "error_policy pass" \
  "markdown_error_policy pass;" "accept"
validate_policy_value 2 "error_policy fail_closed" \
  "markdown_error_policy fail_closed;" "accept"
validate_policy_value 3 "error_policy status 429" \
  "markdown_error_policy status 429;" "accept"
validate_policy_value 4 "error_policy status 503" \
  "markdown_error_policy status 503;" "accept"

echo "--- Rejected values ---" >&2
validate_policy_value 5 "error_policy bad" \
  "markdown_error_policy bad;" "reject"
validate_policy_value 6 "error_policy status 418" \
  "markdown_error_policy status 418;" "reject"
validate_policy_value 7 "error_policy status 502" \
  "markdown_error_policy status 502;" "reject"
validate_policy_value 8 "error_policy status 500" \
  "markdown_error_policy status 500;" "reject"
validate_policy_value 9 "error_policy status 0" \
  "markdown_error_policy status 0;" "reject"
validate_policy_value 10 "error_policy status 65536" \
  "markdown_error_policy status 65536;" "reject"
validate_policy_value 11 "error_policy status abc" \
  "markdown_error_policy status abc;" "reject"

echo "" >&2
echo "==========================================================" >&2
echo " Results: ${TESTS_PASSED} passed, ${TESTS_FAILED} failed" >&2
echo "==========================================================" >&2

if [[ "${TESTS_FAILED}" -eq 0 ]]; then
  echo "PASS: all ${TESTS_RUN} error policy value expectations met" >&2
  exit 0
fi

echo "FAIL: ${TESTS_FAILED} of ${TESTS_RUN} error policy expectations violated" >&2
exit 1
