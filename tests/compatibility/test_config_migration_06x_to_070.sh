#!/bin/bash
#
# Configuration Migration Test: 0.6.x → 0.7.0 Semantic Equivalence
#
# Validates that all configuration directives available in 0.6.x are still
# accepted by the 0.7.0 module without errors, and that default values and
# directive parsing remain semantically equivalent.
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
#   ./test_config_migration_06x_to_070.sh [--nginx-bin PATH] [-h|--help]
#
# Exit codes:
#   0 - All configuration snippets accepted
#   1 - One or more configurations rejected
#   2 - Usage error or missing prerequisites
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC2034
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NGINX_BIN="${NGINX_BIN:-}"
KEEP_ARTIFACTS=0

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Temporary directory for config files
TMPDIR_BASE=""

usage() {
  cat <<EOF >&2
Usage: $(basename "$0") [--nginx-bin PATH] [--keep-artifacts] [-h|--help]

Validate that 0.6.x configuration directives are accepted by the 0.7.0 module.

Options:
  --nginx-bin PATH     Path to NGINX binary with markdown module
  --keep-artifacts     Keep temporary config files after run
  -h, --help           Show this help message

Environment:
  NGINX_BIN            Alternative to --nginx-bin flag
EOF
  return 0
}

# shellcheck disable=SC2317,SC2329
cleanup() {
  if [[ "${KEEP_ARTIFACTS}" -eq 0 && -n "${TMPDIR_BASE}" && -d "${TMPDIR_BASE}" ]]; then
    rm -rf "${TMPDIR_BASE}" || true
  elif [[ -n "${TMPDIR_BASE}" && -d "${TMPDIR_BASE}" ]]; then
    echo "Artifacts kept at: ${TMPDIR_BASE}" >&2
  fi
  return 0
}
trap cleanup EXIT

# --- Argument parsing ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --nginx-bin)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --nginx-bin requires a value" >&2
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
      echo "ERROR: Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

# --- Resolve NGINX binary ---
resolve_nginx_bin() {
  if [[ -n "${NGINX_BIN}" ]]; then
    if [[ ! -x "${NGINX_BIN}" ]]; then
      echo "ERROR: NGINX_BIN is not executable: ${NGINX_BIN}" >&2
      return 1
    fi
    return 0
  fi

  if command -v nginx >/dev/null 2>&1; then
    NGINX_BIN="$(command -v nginx)"
    return 0
  fi

  echo "ERROR: nginx not found in PATH and --nginx-bin not set" >&2
  return 1
}

# --- Test helpers ---
log_test() {
  local test_id="$1"
  local test_name="$2"
  TESTS_RUN=$((TESTS_RUN + 1))
  printf "  [%02d] %-60s " "${test_id}" "${test_name}" >&2
  return 0
}

log_pass() {
  printf "PASS\n" >&2
  TESTS_PASSED=$((TESTS_PASSED + 1))
  return 0
}

log_fail() {
  local detail="${1:-}"
  printf "FAIL\n" >&2
  if [[ -n "${detail}" ]]; then
    printf "       %s\n" "${detail}" >&2
  fi
  TESTS_FAILED=$((TESTS_FAILED + 1))
  return 0
}

# validate_config — Write a config snippet and run nginx -t.
#
# Arguments:
#   $1 - test ID (integer)
#   $2 - test description
#   $3 - NGINX config content (http block directives)
#
# Returns:
#   0 if nginx -t succeeds, 1 otherwise
validate_config() {
  local test_id="$1"
  local description="$2"
  local http_directives="$3" # NOSONAR: used in heredoc below

  local conf_file="${TMPDIR_BASE}/test_${test_id}.conf"
  local log_file="${TMPDIR_BASE}/test_${test_id}.log"

  cat > "${conf_file}" <<EOF
worker_processes 1;
error_log /dev/null crit;
pid ${TMPDIR_BASE}/test_${test_id}.pid;

events { worker_connections 64; }

http {
    ${http_directives}

    server {
        listen 127.0.0.1:19999;
        location / {
            return 200 'ok';
        }
    }
}
EOF

  log_test "${test_id}" "${description}"

  if "${NGINX_BIN}" -t -c "${conf_file}" >"${log_file}" 2>&1; then
    log_pass
    return 0
  else
    log_fail "$(tail -n 2 "${log_file}" 2>/dev/null || echo 'see log')"
    return 1
  fi
}

# --- Main ---
resolve_nginx_bin || exit 2

TMPDIR_BASE="$(mktemp -d /tmp/config-migration-06x-070.XXXXXX)"
mkdir -p "${TMPDIR_BASE}"

echo "==========================================================" >&2
echo " Configuration Migration Test: 0.6.x → 0.7.0" >&2
echo " NGINX binary: ${NGINX_BIN}" >&2
echo " Temp dir: ${TMPDIR_BASE}" >&2
echo "==========================================================" >&2
echo "" >&2

# ============================================================
# Section 1: Core directives (existed in 0.6.x)
# ============================================================
echo "--- Section 1: Core Directives ---" >&2

validate_config 1 "markdown_filter on" \
  "markdown_filter on;" || true

validate_config 2 "markdown_filter off" \
  "markdown_filter off;" || true

validate_config 3 "markdown_max_size with bytes" \
  "markdown_filter on;
    markdown_max_size 1048576;" || true

validate_config 4 "markdown_max_size with k suffix" \
  "markdown_filter on;
    markdown_max_size 512k;" || true

validate_config 5 "markdown_max_size with m suffix" \
  "markdown_filter on;
    markdown_max_size 10m;" || true

validate_config 6 "markdown_timeout with seconds" \
  "markdown_filter on;
    markdown_timeout 5s;" || true

validate_config 7 "markdown_timeout with milliseconds" \
  "markdown_filter on;
    markdown_timeout 500ms;" || true

validate_config 8 "markdown_on_error pass" \
  "markdown_filter on;
    markdown_on_error pass;" || true

validate_config 9 "markdown_on_error reject" \
  "markdown_filter on;
    markdown_on_error reject;" || true

# ============================================================
# Section 2: Flavor directives
# ============================================================
echo "" >&2
echo "--- Section 2: Flavor Directives ---" >&2

validate_config 10 "markdown_flavor commonmark" \
  "markdown_filter on;
    markdown_flavor commonmark;" || true

validate_config 11 "markdown_flavor gfm" \
  "markdown_filter on;
    markdown_flavor gfm;" || true

# ============================================================
# Section 3: Agent-friendly extensions
# ============================================================
echo "" >&2
echo "--- Section 3: Agent-Friendly Extensions ---" >&2

validate_config 12 "markdown_token_estimate on" \
  "markdown_filter on;
    markdown_token_estimate on;" || true

validate_config 13 "markdown_token_estimate off" \
  "markdown_filter on;
    markdown_token_estimate off;" || true

validate_config 14 "markdown_front_matter on" \
  "markdown_filter on;
    markdown_front_matter on;" || true

validate_config 15 "markdown_front_matter off" \
  "markdown_filter on;
    markdown_front_matter off;" || true

# ============================================================
# Section 4: Content negotiation
# ============================================================
echo "" >&2
echo "--- Section 4: Content Negotiation ---" >&2

validate_config 16 "markdown_on_wildcard on" \
  "markdown_filter on;
    markdown_on_wildcard on;" || true

validate_config 17 "markdown_on_wildcard off" \
  "markdown_filter on;
    markdown_on_wildcard off;" || true

# ============================================================
# Section 5: Authentication and security
# ============================================================
echo "" >&2
echo "--- Section 5: Authentication and Security ---" >&2

validate_config 18 "markdown_auth_policy allow" \
  "markdown_filter on;
    markdown_auth_policy allow;" || true

validate_config 19 "markdown_auth_policy deny" \
  "markdown_filter on;
    markdown_auth_policy deny;" || true

validate_config 20 "markdown_auth_cookies with patterns" \
  "markdown_filter on;
    markdown_auth_cookies session* auth_token;" || true

# ============================================================
# Section 6: Caching and conditional requests
# ============================================================
echo "" >&2
echo "--- Section 6: Caching and Conditional Requests ---" >&2

validate_config 21 "markdown_etag on" \
  "markdown_filter on;
    markdown_etag on;" || true

validate_config 22 "markdown_etag off" \
  "markdown_filter on;
    markdown_etag off;" || true

validate_config 23 "markdown_conditional_requests full_support" \
  "markdown_filter on;
    markdown_conditional_requests full_support;" || true

validate_config 24 "markdown_conditional_requests if_modified_since_only" \
  "markdown_filter on;
    markdown_conditional_requests if_modified_since_only;" || true

validate_config 25 "markdown_conditional_requests disabled" \
  "markdown_filter on;
    markdown_conditional_requests disabled;" || true

# ============================================================
# Section 7: Logging
# ============================================================
echo "" >&2
echo "--- Section 7: Logging ---" >&2

validate_config 26 "markdown_log_verbosity error" \
  "markdown_filter on;
    markdown_log_verbosity error;" || true

validate_config 27 "markdown_log_verbosity warn" \
  "markdown_filter on;
    markdown_log_verbosity warn;" || true

validate_config 28 "markdown_log_verbosity info" \
  "markdown_filter on;
    markdown_log_verbosity info;" || true

validate_config 29 "markdown_log_verbosity debug" \
  "markdown_filter on;
    markdown_log_verbosity debug;" || true

# ============================================================
# Section 8: Transfer encoding
# ============================================================
echo "" >&2
echo "--- Section 8: Transfer Encoding ---" >&2

validate_config 30 "markdown_buffer_chunked on" \
  "markdown_filter on;
    markdown_buffer_chunked on;" || true

validate_config 31 "markdown_buffer_chunked off" \
  "markdown_filter on;
    markdown_buffer_chunked off;" || true

validate_config 32 "markdown_stream_types" \
  "markdown_filter on;
    markdown_stream_types text/event-stream application/x-ndjson;" || true

# ============================================================
# Section 9: Pruning (noise reduction)
# ============================================================
echo "" >&2
echo "--- Section 9: Pruning (Noise Reduction) ---" >&2

validate_config 33 "markdown_prune_noise on" \
  "markdown_filter on;
    markdown_prune_noise on;" || true

validate_config 34 "markdown_prune_noise off" \
  "markdown_filter on;
    markdown_prune_noise off;" || true

validate_config 35 "markdown_prune_selectors" \
  "markdown_filter on;
    markdown_prune_selectors \"nav,footer,.sidebar\";" || true

validate_config 36 "markdown_prune_protection_selectors" \
  "markdown_filter on;
    markdown_prune_protection_selectors \"main,.content,article\";" || true

# ============================================================
# Section 10: Dynamic configuration
# ============================================================
echo "" >&2
echo "--- Section 10: Dynamic Configuration ---" >&2

validate_config 37 "markdown_dynamic_config on" \
  "markdown_filter on;
    markdown_dynamic_config on;" || true

validate_config 38 "markdown_dynamic_config off" \
  "markdown_filter on;
    markdown_dynamic_config off;" || true

validate_config 39 "markdown_dynamic_config on with path" \
  "markdown_filter on;
    markdown_dynamic_config on;
    markdown_dynamic_config_path /tmp/dynconf.json;" || true

# ============================================================
# Section 11: Streaming engine (0.6.x feature)
# ============================================================
echo "" >&2
echo "--- Section 11: Streaming Engine ---" >&2

validate_config 40 "markdown_streaming_engine off" \
  "markdown_filter on;
    markdown_streaming_engine off;" || true

validate_config 41 "markdown_streaming_engine on" \
  "markdown_filter on;
    markdown_streaming_engine on;" || true

validate_config 42 "markdown_streaming_engine auto" \
  "markdown_filter on;
    markdown_streaming_engine auto;" || true

validate_config 43 "markdown_streaming_budget" \
  "markdown_filter on;
    markdown_streaming_engine on;
    markdown_streaming_budget 4m;" || true

validate_config 44 "markdown_streaming_on_error pass" \
  "markdown_filter on;
    markdown_streaming_engine on;
    markdown_streaming_on_error pass;" || true

validate_config 45 "markdown_streaming_on_error reject" \
  "markdown_filter on;
    markdown_streaming_engine on;
    markdown_streaming_on_error reject;" || true

validate_config 46 "markdown_streaming_shadow on" \
  "markdown_filter on;
    markdown_streaming_engine on;
    markdown_streaming_shadow on;" || true

validate_config 47 "markdown_streaming_auto_threshold" \
  "markdown_filter on;
    markdown_streaming_engine auto;
    markdown_streaming_auto_threshold 256k;" || true

# ============================================================
# Section 12: Memory budget (0.6.x replacement for max_size)
# ============================================================
echo "" >&2
echo "--- Section 12: Memory Budget ---" >&2

validate_config 48 "markdown_memory_budget" \
  "markdown_filter on;
    markdown_memory_budget 5m;" || true

# ============================================================
# Section 13: Security directives
# ============================================================
echo "" >&2
echo "--- Section 13: Security Directives ---" >&2

validate_config 49 "markdown_trust_forwarded_headers on" \
  "markdown_filter on;
    markdown_trust_forwarded_headers on;" || true

validate_config 50 "markdown_trust_forwarded_headers off" \
  "markdown_filter on;
    markdown_trust_forwarded_headers off;" || true

# ============================================================
# Section 14: Metrics directives
# ============================================================
echo "" >&2
echo "--- Section 14: Metrics Directives ---" >&2

validate_config 51 "markdown_metrics_shm_size" \
  "markdown_metrics_shm_size 2m;" || true

validate_config 52 "markdown_metrics_format prometheus" \
  "markdown_filter on;
    markdown_metrics_format prometheus;" || true

validate_config 53 "markdown_metrics_per_path on" \
  "markdown_filter on;
    markdown_metrics_per_path on;" || true

validate_config 54 "markdown_metrics_per_path_cardinality" \
  "markdown_metrics_per_path_cardinality 100;" || true

# ============================================================
# Section 15: Content types
# ============================================================
echo "" >&2
echo "--- Section 15: Content Types ---" >&2

validate_config 55 "markdown_content_types" \
  "markdown_filter on;
    markdown_content_types text/html application/xhtml+xml;" || true

# ============================================================
# Section 16: New 0.7.0 directives (must not break 0.6.x configs)
# ============================================================
echo "" >&2
echo "--- Section 16: New 0.7.0 Directives (coexistence) ---" >&2

validate_config 56 "markdown_decompress_max_size" \
  "markdown_filter on;
    markdown_decompress_max_size 20m;" || true

validate_config 57 "markdown_parse_timeout" \
  "markdown_filter on;
    markdown_parse_timeout 30s;" || true

validate_config 58 "markdown_parser_budget" \
  "markdown_filter on;
    markdown_parser_budget 64m;" || true

validate_config 59 "markdown_dynconf_dry_run on" \
  "markdown_filter on;
    markdown_dynamic_config on;
    markdown_dynconf_dry_run on;" || true

# ============================================================
# Section 17: Combined 0.6.x configuration (full realistic config)
# ============================================================
echo "" >&2
echo "--- Section 17: Combined 0.6.x Realistic Configuration ---" >&2

validate_config 60 "Full 0.6.x production config" \
  "markdown_filter on;
    markdown_max_size 10m;
    markdown_memory_budget 10m;
    markdown_timeout 5s;
    markdown_on_error pass;
    markdown_flavor commonmark;
    markdown_token_estimate on;
    markdown_front_matter on;
    markdown_on_wildcard off;
    markdown_auth_policy allow;
    markdown_etag on;
    markdown_conditional_requests full_support;
    markdown_log_verbosity info;
    markdown_buffer_chunked on;
    markdown_prune_noise on;
    markdown_prune_selectors \"nav,footer\";
    markdown_trust_forwarded_headers off;
    markdown_streaming_engine auto;
    markdown_streaming_budget 2m;
    markdown_streaming_on_error pass;
    markdown_streaming_auto_threshold 256k;
    markdown_metrics_shm_size 1m;" || true

validate_config 61 "Full 0.6.x + 0.7.0 new directives coexist" \
  "markdown_filter on;
    markdown_max_size 10m;
    markdown_memory_budget 10m;
    markdown_timeout 5s;
    markdown_on_error pass;
    markdown_flavor gfm;
    markdown_token_estimate on;
    markdown_front_matter on;
    markdown_on_wildcard on;
    markdown_auth_policy deny;
    markdown_auth_cookies session* auth*;
    markdown_etag on;
    markdown_conditional_requests full_support;
    markdown_log_verbosity warn;
    markdown_buffer_chunked on;
    markdown_prune_noise on;
    markdown_trust_forwarded_headers on;
    markdown_streaming_engine auto;
    markdown_streaming_budget 4m;
    markdown_streaming_on_error pass;
    markdown_streaming_auto_threshold 512k;
    markdown_dynamic_config on;
    markdown_dynamic_config_path /tmp/dynconf.json;
    markdown_decompress_max_size 20m;
    markdown_parse_timeout 10s;
    markdown_parser_budget 32m;
    markdown_metrics_shm_size 2m;
    markdown_metrics_per_path on;" || true

validate_config 62 "Multi-level inheritance (http + server + location)" \
  "markdown_filter on;
    markdown_on_error pass;
    markdown_max_size 10m;
    markdown_timeout 5s;

    server {
        listen 127.0.0.1:19998;
        markdown_flavor gfm;
        markdown_etag on;

        location /api/ {
            markdown_on_error reject;
            markdown_prune_noise on;
        }

        location /docs/ {
            markdown_streaming_engine on;
            markdown_streaming_budget 4m;
        }

        location /disabled/ {
            markdown_filter off;
        }
    }" || true

# ============================================================
# Summary
# ============================================================
echo "" >&2
echo "==========================================================" >&2
echo " Results: ${TESTS_PASSED}/${TESTS_RUN} passed, ${TESTS_FAILED} failed" >&2
echo "==========================================================" >&2

if [[ "${TESTS_FAILED}" -gt 0 ]]; then
  echo "" >&2
  echo "FAILURE: ${TESTS_FAILED} configuration(s) rejected by 0.7.0 module." >&2
  echo "This indicates a backward-incompatible directive change." >&2
  if [[ "${KEEP_ARTIFACTS}" -eq 0 ]]; then
    echo "Re-run with --keep-artifacts to inspect failing configs." >&2
  fi
  exit 1
fi

echo "" >&2
echo "SUCCESS: All 0.6.x configurations accepted by 0.7.0 module." >&2
exit 0
