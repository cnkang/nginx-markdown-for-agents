#!/usr/bin/env bash
# verify_diagnostics_access_phase_e2e.sh — Verify native NGINX access-phase
# directives correctly restrict the diagnostics and metrics content handlers.
#
# This is a reusable generic gate that consumes ordinary repository inputs
# (NGINX binary, module shared object, fixture root). It NEVER reads
# .kiro/specs/, never encodes Spec/Wave/version-specific conclusions.
#
# Schema: diagnostics-access-phase-e2e.v1
# Failure semantics: nonzero exit on any unexpected HTTP status code
# SKIP semantics: exit 0 with result=SKIP when NGINX_BIN or NGINX_MODULE_SO
#   are unavailable on the current platform
#
# Required inputs:
#   NGINX_BIN       — path to a compiled nginx binary
#   NGINX_MODULE_SO — path to the built ngx_http_markdown_filter_module .so
#   FIXTURE_ROOT    — path to a directory containing test fixture HTML
#
# Optional:
#   PORT            — listen port (default: 18092)
#   RESULT_FILE     — path to write JSON result (default: stdout summary only)
#
# Usage:
#   NGINX_BIN=/path/to/nginx NGINX_MODULE_SO=/path/to/module.so \
#     FIXTURE_ROOT=/path/to/fixtures tools/e2e/verify_diagnostics_access_phase_e2e.sh
#
# Exit codes:
#   0 — all access-phase assertions passed (or SKIP)
#   1 — at least one assertion failed
#   2 — usage/prerequisite error
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ── SKIP gate ──────────────────────────────────────────────────────
if [[ -z "${NGINX_BIN:-}" ]] || [[ -z "${NGINX_MODULE_SO:-}" ]]; then
  echo "SKIP: NGINX_BIN or NGINX_MODULE_SO not set; cannot run native E2E" >&2
  if [[ -n "${RESULT_FILE:-}" ]]; then
    mkdir -p "$(dirname "${RESULT_FILE}")"
    cat > "${RESULT_FILE}" <<EOF
{
  "schema_version": "diagnostics-access-phase-e2e.v1",
  "result": "SKIP",
  "skip_reason": "nginx_binary_unavailable",
  "skip_detail": "NGINX_BIN or NGINX_MODULE_SO environment variables not set"
}
EOF
  fi
  exit 0
fi

if [[ ! -x "${NGINX_BIN}" ]]; then
  echo "SKIP: NGINX_BIN (${NGINX_BIN}) is not executable on this host" >&2
  if [[ -n "${RESULT_FILE:-}" ]]; then
    mkdir -p "$(dirname "${RESULT_FILE}")"
    cat > "${RESULT_FILE}" <<EOF
{
  "schema_version": "diagnostics-access-phase-e2e.v1",
  "result": "SKIP",
  "skip_reason": "nginx_binary_not_executable",
  "skip_detail": "NGINX_BIN=${NGINX_BIN} is not executable on this platform"
}
EOF
  fi
  exit 0
fi

if [[ -z "${FIXTURE_ROOT:-}" ]]; then
  # Use a default fixture if available
  if [[ -d "${WORKSPACE_ROOT}/tests/corpus/simple" ]]; then
    FIXTURE_ROOT="${WORKSPACE_ROOT}/tests/corpus/simple"
  else
    echo "ERROR: FIXTURE_ROOT not set and no default fixture found" >&2
    exit 2
  fi
fi

PORT="${PORT:-18092}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/access-phase-e2e.XXXXXX")"
RESULT_TSV="${WORK}/results.tsv"
NGINX_PID=""
AUTH_USER="e2e-user"
AUTH_PASSWORD="e2e-${PORT}"

# ── Cleanup ────────────────────────────────────────────────────────
# shellcheck disable=SC2329
cleanup() {
  if [[ -n "${NGINX_PID:-}" ]]; then
    kill "${NGINX_PID}" 2>/dev/null || true
    wait "${NGINX_PID}" 2>/dev/null || true
  fi
  rm -rf "${WORK}"
  return 0
}
trap cleanup EXIT INT TERM

SOURCE_SHA="$(cd "${WORKSPACE_ROOT}" && git rev-parse HEAD 2>/dev/null || echo "unknown")"

sha256_file() {
  local path="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}" 2>/dev/null | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" 2>/dev/null | awk '{print $1}'
  else
    echo "unknown"
  fi
  return 0
}

MODULE_SHA="$(sha256_file "${NGINX_MODULE_SO}")"

stop_case_nginx() {
  if [[ -n "${NGINX_PID:-}" ]]; then
    kill "${NGINX_PID}" 2>/dev/null || true
    wait "${NGINX_PID}" 2>/dev/null || true
    NGINX_PID=""
  fi
  return 0
}

# ── Run a single access-policy case ───────────────────────────────
run_case() {
  local policy="$1"
  local prefix="${WORK}/${policy}"
  mkdir -p "${prefix}/conf" "${prefix}/logs" "${prefix}/temp"

  local ACCESS=""
  case "${policy}" in
    allow_deny)
      ACCESS='allow 127.0.0.1; deny all;'
      ;;
    auth_basic)
      ACCESS="auth_basic \"e2e-test\"; auth_basic_user_file ${prefix}/conf/passwd;"
      printf '%s\n' "${AUTH_USER}:{PLAIN}${AUTH_PASSWORD}" > "${prefix}/conf/passwd"
      ;;
    satisfy_any)
      ACCESS="satisfy any; allow 127.0.0.1; deny all; auth_basic \"e2e-test\"; auth_basic_user_file ${prefix}/conf/passwd;"
      printf '%s\n' "${AUTH_USER}:{PLAIN}${AUTH_PASSWORD}" > "${prefix}/conf/passwd"
      ;;
    *)
      echo "ERROR: unknown policy: ${policy}" >&2
      stop_case_nginx
      return 2
      ;;
  esac

  cat > "${prefix}/conf/nginx.conf" <<EOF
load_module ${NGINX_MODULE_SO};
pid ${prefix}/nginx.pid;
error_log ${prefix}/logs/error.log notice;
events { worker_connections 64; }
http {
    access_log off;
    server {
        listen 127.0.0.1:${PORT};
        location /fixture {
            root ${FIXTURE_ROOT};
        }
        location /diagnostics {
            markdown_diagnostics on;
            ${ACCESS}
        }
        location /metrics {
            markdown_metrics;
            ${ACCESS}
        }
    }
}
EOF

  # Validate config
  if ! "${NGINX_BIN}" -t -p "${prefix}" -c conf/nginx.conf > "${prefix}/config-test.log" 2>&1; then
    echo "FAIL: nginx -t failed for policy=${policy}" >&2
    cat "${prefix}/config-test.log" >&2
    stop_case_nginx
    return 1
  fi

  local config_digest
  config_digest="$(sha256_file "${prefix}/conf/nginx.conf")"

  # Start nginx
  "${NGINX_BIN}" -p "${prefix}" -c conf/nginx.conf -g 'daemon off;' > "${prefix}/runtime.log" 2>&1 &
  NGINX_PID="$!"

  # Wait for readiness
  local ready=0
  local i=0
  while [[ "${i}" -lt 30 ]]; do
    local ready_status
    ready_status="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/diagnostics" 2>/dev/null || echo "000")"
    if [[ "${ready_status}" == "200" ]] || [[ "${ready_status}" == "401" ]] || [[ "${ready_status}" == "403" ]]; then
      ready=1
      break
    fi
    i=$((i + 1))
    sleep 1
  done

  if [[ "${ready}" -ne 1 ]]; then
    echo "FAIL: nginx did not become ready for policy=${policy}" >&2
    stop_case_nginx
    return 1
  fi

  local fail_count=0
  local loopback_alias_available=1
  local alias_probe
  alias_probe="$(curl -sS --interface 127.0.0.2 -o /dev/null -w '%{http_code}' \
    "http://127.0.0.1:${PORT}/diagnostics" 2>/dev/null || echo "000")"
  if [[ "${alias_probe}" == "000" ]]; then
    local primary_probe
    primary_probe="$(curl -sS -o /dev/null -w '%{http_code}' \
      "http://127.0.0.1:${PORT}/diagnostics" 2>/dev/null || echo "000")"
    if [[ "${primary_probe}" != "000" ]]; then
      loopback_alias_available=0
      echo "SKIP: 127.0.0.2 is unavailable; skipping allow_deny unauthorized probes" >&2
    fi
  fi

  # Test each handler with GET and HEAD
  for handler in diagnostics metrics; do
    for method in GET HEAD; do
      local method_flag=""
      if [[ "${method}" == "HEAD" ]]; then
        method_flag="-I"
      fi

      local expected_auth expected_unauth
      local auth_requires_credentials=0
      case "${policy}" in
        allow_deny)
          expected_auth=200; expected_unauth=403
          ;;
        auth_basic)
          expected_auth=200; expected_unauth=401
          auth_requires_credentials=1
          ;;
        satisfy_any)
          expected_auth=200; expected_unauth=401
          ;;
        *)
          echo "FAIL: unsupported access policy=${policy}" >&2
          stop_case_nginx
          return 1
          ;;
      esac

      # Authorized request
      local auth_status
      local -a curl_cmd=(curl -sS)
      if [[ -n "${method_flag}" ]]; then
        curl_cmd+=("${method_flag}")
      fi
      if [[ "${auth_requires_credentials}" -eq 1 ]]; then
        curl_cmd+=(-u "${AUTH_USER}:${AUTH_PASSWORD}")
      fi
      curl_cmd+=(-o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/${handler}")
      auth_status="$( "${curl_cmd[@]}" 2>/dev/null)" || auth_status=000

      if [[ "${auth_status}" != "${expected_auth}" ]]; then
        echo "FAIL: ${policy}/${handler}/${method}/authorized: expected=${expected_auth} actual=${auth_status}" >&2
        fail_count=$((fail_count + 1))
      fi
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${policy}" "${handler}" "${method}" "authorized" "${expected_auth}" "${auth_status}" "${config_digest}" >> "${RESULT_TSV}"

      # Unauthorized request.  127.0.0.2 stays on loopback but is outside the
      # allowlist, so the deny branch is exercised without external network
      # dependencies.
      # For auth_basic: omit credentials
      # For satisfy_any: omit credentials (deny all applies but satisfy any means auth can override)
      local unauth_status
      case "${policy}" in
        allow_deny)
          if [[ "${loopback_alias_available}" -eq 0 ]]; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
              "${policy}" "${handler}" "${method}" "unauthorized_skipped" \
              "${expected_unauth}" "skipped" "${config_digest}" >> "${RESULT_TSV}"
            continue
          fi
          if ! unauth_status="$(curl -sS --interface 127.0.0.2 ${method_flag} \
            -o /dev/null -w '%{http_code}' \
            "http://127.0.0.1:${PORT}/${handler}" 2>/dev/null)"; then
            unauth_status=000
          fi
          if [[ "${unauth_status}" != "${expected_unauth}" ]]; then
            echo "FAIL: ${policy}/${handler}/${method}/unauthorized: expected=${expected_unauth} actual=${unauth_status}" >&2
            fail_count=$((fail_count + 1))
          fi
          printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "${policy}" "${handler}" "${method}" "unauthorized" "${expected_unauth}" "${unauth_status}" "${config_digest}" >> "${RESULT_TSV}"
          ;;
        auth_basic)
          unauth_status="$(curl -sS ${method_flag} -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/${handler}" 2>/dev/null)" || unauth_status=000
          if [[ "${unauth_status}" != "${expected_unauth}" ]]; then
            echo "FAIL: ${policy}/${handler}/${method}/unauthorized: expected=${expected_unauth} actual=${unauth_status}" >&2
            fail_count=$((fail_count + 1))
          fi
          printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "${policy}" "${handler}" "${method}" "unauthorized" "${expected_unauth}" "${unauth_status}" "${config_digest}" >> "${RESULT_TSV}"
          ;;
        satisfy_any)
          # satisfy any: 127.0.0.1 is allowed, so even without credentials it passes
          # Omitting credentials verifies that the allow rule alone is sufficient.
          unauth_status="$(curl -sS ${method_flag} \
            -o /dev/null -w '%{http_code}' \
            "http://127.0.0.1:${PORT}/${handler}" 2>/dev/null)" || unauth_status=000
          # satisfy any: allow 127.0.0.1 passes without credentials.
          # This confirms that at least one of the access checks (allow) passed
          printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "${policy}" "${handler}" "${method}" "satisfy_any_allow_wins" "200" "${unauth_status}" "${config_digest}" >> "${RESULT_TSV}"
          if [[ "${unauth_status}" != "200" ]]; then
            echo "FAIL: ${policy}/${handler}/${method}/satisfy_any_allow_wins: expected=200 actual=${unauth_status}" >&2
            fail_count=$((fail_count + 1))
          fi
          # A client on 127.0.0.2 stays on loopback but is outside the
          # allowlist, so the deny branch applies: satisfy_any cannot fall
          # back to auth_basic without credentials, and the unauthenticated
          # request is rejected with the expected_unauth status.
          if [[ "${loopback_alias_available}" -eq 0 ]]; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
              "${policy}" "${handler}" "${method}" "unauthorized_skipped" \
              "${expected_unauth}" "skipped" "${config_digest}" >> "${RESULT_TSV}"
            continue
          fi
          if ! outside_status="$(curl -sS --interface 127.0.0.2 ${method_flag} \
            -o /dev/null -w '%{http_code}' \
            "http://127.0.0.1:${PORT}/${handler}" 2>/dev/null)"; then
            outside_status=000
          fi
          if [[ "${outside_status}" != "${expected_unauth}" ]]; then
            echo "FAIL: ${policy}/${handler}/${method}/unauthorized: expected=${expected_unauth} actual=${outside_status}" >&2
            fail_count=$((fail_count + 1))
          fi
          printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "${policy}" "${handler}" "${method}" "unauthorized" "${expected_unauth}" "${outside_status}" "${config_digest}" >> "${RESULT_TSV}"
          ;;
        *)
          echo "FAIL: unsupported access policy=${policy}" >&2
          stop_case_nginx
          return 1
          ;;
      esac
    done
  done

  stop_case_nginx

  return "${fail_count}"
}

# ── Execute all policy cases ───────────────────────────────────────
total_failures=0

run_case allow_deny || total_failures=$((total_failures + $?))
run_case auth_basic || total_failures=$((total_failures + $?))
run_case satisfy_any || total_failures=$((total_failures + $?))

# ── Report result ──────────────────────────────────────────────────
if [[ "${total_failures}" -eq 0 ]]; then
  result="PASS"
  echo "PASS: All diagnostics access-phase assertions passed" >&2
else
  result="FAIL"
  echo "FAIL: ${total_failures} access-phase assertion(s) failed" >&2
fi

if [[ -n "${RESULT_FILE:-}" ]]; then
  mkdir -p "$(dirname "${RESULT_FILE}")"
  # Build results array from TSV
  results_json="[]"
  if [[ -f "${RESULT_TSV}" ]]; then
    results_json="$(python3 -c "
import json, sys
rows = []
for line in open(sys.argv[1]).read().splitlines():
    parts = line.split('\t')
    if len(parts) >= 7:
        rows.append({
            'policy': parts[0], 'handler': parts[1], 'method': parts[2],
            'identity': parts[3], 'expected': parts[4], 'actual': parts[5],
            'config_digest': 'sha256:' + parts[6]
        })
print(json.dumps(rows))
" "${RESULT_TSV}" 2>/dev/null || echo "[]")"
  fi

  cat > "${RESULT_FILE}" <<EOF
{
  "schema_version": "diagnostics-access-phase-e2e.v1",
  "source_sha": "${SOURCE_SHA}",
  "nginx_binary": "${NGINX_BIN}",
  "module_sha256": "sha256:${MODULE_SHA}",
  "policies_tested": ["allow_deny", "auth_basic", "satisfy_any"],
  "handlers_tested": ["diagnostics", "metrics"],
  "results": ${results_json},
  "result": "${result}"
}
EOF
fi

if [[ "${total_failures}" -gt 0 ]]; then
  exit 1
fi
exit 0
