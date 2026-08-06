#!/usr/bin/env bash
#
# Example configuration nginx -t verification.
#
# Validates that every example configuration and the 0.9.2 migration-guide
# nginx examples produce the expected `nginx -t` result against a
# module-enabled NGINX binary.
#
# Coverage:
#   - examples/nginx-configs/*.conf            (must pass)
#   - examples/production/*.conf               (must pass)
#   - examples/kubernetes/manifest/markdown-configmap.yaml
#     (the `data` section is extracted into a sandbox config; must pass)
#   - ```nginx code blocks from docs/guides/MIGRATION-0.9.2.md
#     ("# BEFORE (0.9.1)" blocks reference removed directives and are
#     expected to FAIL with "unknown directive"; all other blocks,
#     including "# AFTER (0.9.2)" examples, must pass)
#
# Requirements:
#   - NGINX_BIN: path to a module-enabled NGINX binary (required; the
#     script exits 2 when unset)
#   - MODULE_SO (optional): path to the module .so. When set, `load_module`
#     lines in the examples are redirected to it and configs without a
#     `load_module` line are tested with `-g "load_module <MODULE_SO>;"`.
#     When unset, NGINX_BIN must already have the module loaded (static
#     build or default module path).
#   - macOS bash 3.2 compatible (Rule 11); POSIX ERE sed (Rule 41).
#
# Sandbox adaptations applied to the tested copies only (originals are
# never modified): listen 80 -> 18180, listen 443 ssl http2 -> listen
# 18180, ssl_certificate(_key) lines commented, /var/log/nginx and
# /var/run/nginx paths redirected into the sandbox prefix, proxy_cache_path
# redirected to the sandbox prefix, /etc/nginx/mime.types include redirected
# to the sandbox mime.types.
#
# Exit codes:
#   0 - all expected outcomes observed
#   1 - one or more unexpected nginx -t results
#   2 - usage or prerequisite error
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

NGINX_BIN="${NGINX_BIN:-}"
MODULE_SO="${MODULE_SO:-}"
KEEP_ARTIFACTS=0

RUNTIME_DIR=""
TEST_COUNT=0
PASS_COUNT=0
FAIL_COUNT=0
FAILED_DETAILS=()

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") [--keep-artifacts] [-h|--help]

Validate example configs and migration-guide examples with nginx -t.

Environment:
  NGINX_BIN   Path to a module-enabled NGINX binary (required)
  MODULE_SO   Path to the module .so (optional; see script header)

Options:
  --keep-artifacts  Keep the sandbox prefix after the run
  -h, --help        Show this help message
EOF
  return 0
}

# --- Argument parsing (Rule 18: case with default branch) ---
while [[ $# -gt 0 ]]; do
  case "$1" in
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

# --- Prerequisites (Rule 33: fixed canonical executables only) ---
if [[ -z "${NGINX_BIN}" ]]; then
  echo "ERROR: NGINX_BIN is not set. Point it at a module-enabled nginx binary." >&2
  echo "  Example: NGINX_BIN=/usr/local/nginx/sbin/nginx tools/e2e/verify_examples_nginx_t.sh" >&2
  exit 2
fi
if [[ ! -x "${NGINX_BIN}" ]]; then
  echo "ERROR: NGINX_BIN is not executable: ${NGINX_BIN}" >&2
  exit 2
fi
if [[ -n "${MODULE_SO}" && ! -f "${MODULE_SO}" ]]; then
  echo "ERROR: MODULE_SO does not exist: ${MODULE_SO}" >&2
  exit 2
fi

# GNU sed and BSD sed use different in-place-edit arguments. Keep the
# verification wrapper runnable on both the Linux CI runner and macOS hosts.
sed_in_place() {
  local expression="$1"
  local target="$2"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' -E "${expression}" "${target}"
  else
    sed -i -E "${expression}" "${target}"
  fi
  return 0
}

# --- Runtime prefix ---
RUNTIME_DIR="$(mktemp -d "${TMPDIR:-/tmp}/nginx-markdown-config-test.XXXXXX")"
mkdir -p "${RUNTIME_DIR}/logs"
mkdir -p "${RUNTIME_DIR}/cache/proxy"

if [[ "${KEEP_ARTIFACTS}" -eq 0 ]]; then
  trap 'rm -rf -- "${RUNTIME_DIR}"' EXIT
fi

# Minimal mime.types so `include mime.types;` resolves inside the sandbox.
cat > "${RUNTIME_DIR}/mime.types" <<'EOF'
types {
    text/html html htm;
    text/markdown md markdown;
    text/plain txt;
    text/css css;
    application/json json;
    application/javascript js;
    image/svg+xml svg;
}
EOF

# Minimal fastcgi_params for the PHP-FPM example.
cat > "${RUNTIME_DIR}/fastcgi_params" <<'EOF'
fastcgi_param  SCRIPT_FILENAME    $document_root$fastcgi_script_name;
fastcgi_param  QUERY_STRING       $query_string;
fastcgi_param  REQUEST_METHOD     $request_method;
fastcgi_param  CONTENT_TYPE       $content_type;
fastcgi_param  CONTENT_LENGTH     $content_length;
fastcgi_param  SCRIPT_NAME        $fastcgi_script_name;
fastcgi_param  REQUEST_URI        $request_uri;
fastcgi_param  DOCUMENT_URI       $document_uri;
fastcgi_param  DOCUMENT_ROOT      $document_root;
fastcgi_param  SERVER_PROTOCOL    $server_protocol;
fastcgi_param  REMOTE_ADDR        $remote_addr;
fastcgi_param  REMOTE_PORT        $remote_port;
fastcgi_param  SERVER_ADDR        $server_addr;
fastcgi_param  SERVER_PORT        $server_port;
fastcgi_param  SERVER_NAME        $server_name;
EOF

# --- Sandbox adaptation (applies to the tested copy only) ---
sandbox_conf() {
  local src="$1"
  local dst="$2"
  local event_type="epoll"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    event_type="kqueue"
  fi
  sed -E \
    -e 's/^([[:space:]]*)listen[[:space:]]+80([[:space:];])/\1listen 18180\2/' \
    -e 's/^([[:space:]]*)listen[[:space:]]+443[[:space:]]+ssl[[:space:]]+http2;/\1listen 18180;/' \
    -e '/^[[:space:]]*ssl_certificate_key[[:space:]]/s/^/#/' \
    -e '/^[[:space:]]*ssl_certificate[[:space:]]/s/^/#/' \
    -e 's|error_log /var/log/nginx/error.log|error_log logs/error.log|' \
    -e 's|access_log /var/log/nginx/access.log|access_log logs/access.log|' \
    -e 's|pid /var/run/nginx.pid|pid logs/nginx.pid|' \
    -e 's|include[[:space:]]+/etc/nginx/mime.types|include mime.types|' \
    -e 's|proxy_cache_path /var/cache/nginx/proxy|proxy_cache_path cache/proxy|' \
    -e "s|^([[:space:]]*)use[[:space:]]+epoll;|\\1use ${event_type};|" \
    -e 's|proxy_pass[[:space:]]+http://backend;|proxy_pass http://127.0.0.1:8080;|' \
    -e 's|server[[:space:]]+backend1.example.com:8080;|server 127.0.0.1:8081;|' \
    -e 's|server[[:space:]]+backend2.example.com:8080;|server 127.0.0.1:8082;|' \
    -e 's|server[[:space:]]+backend1:8080|server 127.0.0.1:8081|' \
    -e 's|server[[:space:]]+backend2:8080|server 127.0.0.1:8082|' \
    -e '/^[[:space:]]*ssl_protocols[[:space:]]/s/^/#/' \
    -e '/^[[:space:]]*ssl_ciphers[[:space:]]/s/^/#/' \
    -e '/^[[:space:]]*ssl_prefer_server_ciphers[[:space:]]/s/^/#/' \
    -e '/^[[:space:]]*ssl_session_cache[[:space:]]/s/^/#/' \
    -e '/^[[:space:]]*ssl_session_timeout[[:space:]]/s/^/#/' \
    "${src}" > "${dst}"
  if [[ -n "${MODULE_SO}" ]]; then
    sed_in_place "s|^([[:space:]]*)load_module[[:space:]]+[^;]*;|\1load_module ${MODULE_SO};|" "${dst}"
  else
    sed_in_place '/^[[:space:]]*load_module[[:space:]]/s/^/#/' "${dst}"
  fi
  return 0
}

# has_load_module — 0 if the file contains an active load_module line.
has_load_module() {
  grep -qE '^[[:space:]]*load_module[[:space:]]' "$1"
  return $?
}

# run_nginx_t — run nginx -t against a sandboxed config.
#
# Arguments:
#   $1 - sandboxed config path
#   $2 - log path for nginx output
#   $3 - "1" when the config itself already loads the module (no -g flag)
#
# Returns the nginx -t exit status.
run_nginx_t() {
  local conf="$1"
  local log="$2"
  local handle_in_conf="$3"
  if [[ -n "${MODULE_SO}" && "${handle_in_conf}" != "1" ]]; then
    "${NGINX_BIN}" -t -p "${RUNTIME_DIR}/" \
      -g "load_module ${MODULE_SO};" -c "${conf}" > "${log}" 2>&1
  else
    "${NGINX_BIN}" -t -p "${RUNTIME_DIR}/" -c "${conf}" > "${log}" 2>&1
  fi
  return $?
}

# prepare_file_conf — sandbox a source config for testing.
#
# Arguments:
#   $1 - source config path
#
# Sets PREPARED_CONF (sandboxed path) and PREPARED_HANDLE (module
# handling decision) for the next check_conf call.
PREPARED_CONF=""
PREPARED_HANDLE=0
prepare_file_conf() {
  local src="$1"
  PREPARED_CONF="${RUNTIME_DIR}/check_$((TEST_COUNT + 1)).conf"
  PREPARED_HANDLE=0
  sandbox_conf "${src}" "${PREPARED_CONF}"
  if has_load_module "${src}"; then
    PREPARED_HANDLE=1
  fi
  return 0
}

# check_conf — validate the prepared config against an expected outcome.
#
# Arguments:
#   $1 - label (shown in output)
#   $2 - expected outcome: pass|fail
#
# Uses PREPARED_CONF and PREPARED_HANDLE set by prepare_file_conf or by
# the generated-config callers.
check_conf() {
  local label="$1"
  local expect="$2"
  local log rc

  TEST_COUNT=$((TEST_COUNT + 1))
  log="${RUNTIME_DIR}/check_${TEST_COUNT}.log"

  run_nginx_t "${PREPARED_CONF}" "${log}" "${PREPARED_HANDLE}"
  rc=$?

  if [[ "${expect}" == "fail" ]]; then
    if [[ "${rc}" -eq 0 ]]; then
      echo "  [FAIL] ${label} (expected rejection, but nginx -t accepted it)" >&2
      FAIL_COUNT=$((FAIL_COUNT + 1))
      FAILED_DETAILS+=("${label}: expected 'unknown directive' rejection")
      return 1
    fi
    if grep -qi "unknown directive" "${log}" 2>/dev/null; then
      echo "  [PASS] ${label} (rejected as expected: unknown directive)" >&2
      PASS_COUNT=$((PASS_COUNT + 1))
      return 0
    fi
    echo "  [FAIL] ${label} (rejected for an unexpected reason; see below)" >&2
    tail -n 3 "${log}" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_DETAILS+=("${label}: rejected without 'unknown directive'")
    return 1
  fi

  if [[ "${rc}" -eq 0 ]]; then
    echo "  [PASS] ${label}" >&2
    PASS_COUNT=$((PASS_COUNT + 1))
    return 0
  fi
  echo "  [FAIL] ${label} (nginx -t failed; see below)" >&2
  tail -n 3 "${log}" >&2
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_DETAILS+=("${label}: nginx -t failed")
  return 1
}

echo "==========================================================" >&2
echo " Example and Migration-Guide nginx -t verification" >&2
echo " NGINX_BIN: ${NGINX_BIN}" >&2
if [[ -n "${MODULE_SO}" ]]; then
  echo " MODULE_SO: ${MODULE_SO}" >&2
fi
echo "==========================================================" >&2

# ============================================================
# Section 1: examples/nginx-configs/*.conf
# ============================================================
echo "--- Section 1: examples/nginx-configs ---" >&2
if compgen -G "${WORKSPACE_ROOT}/examples/nginx-configs/*.conf" > /dev/null; then
  for conf in "${WORKSPACE_ROOT}"/examples/nginx-configs/*.conf; do
    prepare_file_conf "${conf}"
    check_conf "nginx-configs/$(basename "${conf}")" pass || true
  done
else
  echo "ERROR: no example configs found in examples/nginx-configs/" >&2
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_DETAILS+=("examples/nginx-configs/ missing *.conf files")
fi

# ============================================================
# Section 2: examples/production/*.conf
# ============================================================
echo "--- Section 2: examples/production ---" >&2
if compgen -G "${WORKSPACE_ROOT}/examples/production/*.conf" > /dev/null; then
  for conf in "${WORKSPACE_ROOT}"/examples/production/*.conf; do
    prepare_file_conf "${conf}"
    check_conf "production/$(basename "${conf}")" pass || true
  done
else
  echo "ERROR: no example configs found in examples/production/" >&2
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_DETAILS+=("examples/production/ missing *.conf files")
fi

# ============================================================
# Section 3: examples/kubernetes/manifest/markdown-configmap.yaml
# ============================================================
echo "--- Section 3: Kubernetes configmap ---" >&2
CONFIGMAP_YAML="${WORKSPACE_ROOT}/examples/kubernetes/manifest/markdown-configmap.yaml"
if [[ -f "${CONFIGMAP_YAML}" ]]; then
  CM_MAIN="${RUNTIME_DIR}/configmap_main.conf"
  CM_HTTP="${RUNTIME_DIR}/configmap_http.conf"
  : > "${CM_MAIN}"
  : > "${CM_HTTP}"
  # Extract the 4-space-indented `data` content lines of both block scalars.
  awk '/^    / { print substr($0, 5) }' "${CONFIGMAP_YAML}" \
    | while IFS= read -r line; do
        case "$line" in
          load_module*)
            printf '%s\n' "${line}" >> "${CM_MAIN}"
            ;;
          *)
            printf '%s\n' "${line}" >> "${CM_HTTP}"
            ;;
        esac
      done
  PREPARED_CONF="${RUNTIME_DIR}/configmap_test.conf"
  PREPARED_HANDLE=0
  {
    echo "worker_processes 1;"
    echo "error_log logs/error.log crit;"
    echo "pid logs/nginx.pid;"
    cat "${CM_MAIN}"
    echo "events { worker_connections 64; }"
    echo "http {"
    cat "${CM_HTTP}"
    echo "    server {"
    echo "        listen 18180;"
    echo "        location / { return 200 'ok'; }"
    echo "    }"
    echo "}"
  } > "${PREPARED_CONF}"
  if [[ -n "${MODULE_SO}" ]]; then
    sed_in_place "s|^([[:space:]]*)load_module[[:space:]]+[^;]*;|\1load_module ${MODULE_SO};|" \
      "${PREPARED_CONF}"
    if has_load_module "${PREPARED_CONF}"; then
      PREPARED_HANDLE=1
    fi
  else
    sed_in_place '/^[[:space:]]*load_module[[:space:]]/s/^/#/' \
      "${PREPARED_CONF}"
  fi
  check_conf "kubernetes/configmap (data section)" pass || true
else
  echo "ERROR: missing ${CONFIGMAP_YAML}" >&2
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_DETAILS+=("missing examples/kubernetes/manifest/markdown-configmap.yaml")
fi

# ============================================================
# Section 4: docs/guides/MIGRATION-0.9.2.md nginx code blocks
# ============================================================
echo "--- Section 4: MIGRATION-0.9.2.md nginx examples ---" >&2
MIGRATION_FILE="${WORKSPACE_ROOT}/docs/guides/MIGRATION-0.9.2.md"
BLOCK_DIR="${RUNTIME_DIR}/migration_blocks"
MANIFEST="${RUNTIME_DIR}/migration_manifest.txt"
if [[ -f "${MIGRATION_FILE}" ]]; then
  mkdir -p "${BLOCK_DIR}"
  python3 - "${MIGRATION_FILE}" "${BLOCK_DIR}" > "${MANIFEST}" <<'PY'
import os
import re
import sys

src, outdir = sys.argv[1], sys.argv[2]

with open(src, "r", encoding="utf-8") as f:
    lines = f.read().splitlines()

blocks = []
current = None
for line in lines:
    if line.startswith("```nginx"):
        current = []
    elif line.startswith("```"):
        if current is not None:
            blocks.append(current)
        current = None
    elif current is not None:
        current.append(line)

count = 0
for block in blocks:
    body = "\n".join(block).strip()
    if not body:
        continue
    count += 1
    name = "block_%03d.conf" % count
    with open(os.path.join(outdir, name), "w", encoding="utf-8") as f:
        f.write(body + "\n")
    expect = "fail" if re.search(r"(?m)^\s*#\s*BEFORE\b", body) else "pass"
    sys.stdout.write("%s\t%s\tblock %d\n" % (name, expect, count))
PY
  if [[ ! -s "${MANIFEST}" ]]; then
    echo "ERROR: no nginx code blocks extracted from ${MIGRATION_FILE}" >&2
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_DETAILS+=("no nginx code blocks extracted from MIGRATION-0.9.2.md")
  else
    while IFS=$'\t' read -r block_name expect label; do
      PREPARED_CONF="${RUNTIME_DIR}/migration_${block_name}"
      PREPARED_HANDLE=0
      {
        echo "worker_processes 1;"
        echo "error_log logs/error.log crit;"
        echo "pid logs/nginx.pid;"
        echo "events { worker_connections 64; }"
        echo "http {"
        echo "    server {"
        echo "        listen 18180;"
        cat "${BLOCK_DIR}/${block_name}"
        echo "    }"
        echo "}"
      } > "${PREPARED_CONF}"
      check_conf "migration-guide ${label}" "${expect}" || true
    done < "${MANIFEST}"
  fi
else
  echo "ERROR: missing ${MIGRATION_FILE}" >&2
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_DETAILS+=("missing docs/guides/MIGRATION-0.9.2.md")
fi

# ============================================================
# Summary
# ============================================================
echo "==========================================================" >&2
echo " Results: ${PASS_COUNT}/${TEST_COUNT} passed, ${FAIL_COUNT} failed" >&2
echo "==========================================================" >&2

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
  for detail in "${FAILED_DETAILS[@]}"; do
    echo "  FAILED: ${detail}" >&2
  done
  if [[ "${KEEP_ARTIFACTS}" -eq 0 ]]; then
    echo "Re-run with --keep-artifacts to inspect the sandbox prefix." >&2
  fi
  exit 1
fi

echo "SUCCESS: all example configs and migration-guide examples produced the expected nginx -t outcome." >&2
exit 0
