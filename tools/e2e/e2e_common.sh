#!/usr/bin/env bash
#
# Shared E2E helper functions for URL construction and curl invocations.
#
# Provides centralized URL helpers to eliminate duplicated
# "http://127.0.0.1:${PORT}" patterns across E2E scripts.
#
# Contract:
#   - Callers must source this file.
#   - All functions print results to stdout; diagnostics to stderr.
#   - Functions return 0 on success.
#   - PORT must be set before calling URL-construction helpers.

# e2e_base_url — Construct the base URL from the PORT environment variable.
#
# Arguments:
#   (none; reads PORT from environment)
#
# Outputs:
#   Writes "http://127.0.0.1:${PORT}" to stdout.
#
# Returns:
#   0 always.
e2e_base_url() {
  printf 'http://127.0.0.1:%s' "${PORT:?PORT is not set}"
  return 0
}

# e2e_metrics_url — Construct the metrics endpoint URL from PORT.
#
# Arguments:
#   (none; reads PORT from environment)
#
# Outputs:
#   Writes "http://127.0.0.1:${PORT}/markdown-metrics" to stdout.
#
# Returns:
#   0 always.
e2e_metrics_url() {
  printf 'http://127.0.0.1:%s/markdown-metrics' "${PORT:?PORT is not set}"
  return 0
}

# e2e_curl_get — Perform a curl GET with standard E2E options.
#
# Wraps curl -sS with optional headers and max-time.  Additional
# curl flags may be passed before the URL argument.
#
# Arguments:
#   $1        — URL (required)
#   remaining — forwarded to curl before the URL (e.g. -D, -o, -H, --max-time)
#
# Outputs:
#   curl stdout and stderr are passed through.
#
# Returns:
#   curl exit status.
e2e_curl_get() {
  curl -sS "$@"
  return $?
}
