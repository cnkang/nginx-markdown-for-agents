#!/usr/bin/env bash
# Collect C module coverage by building NGINX with --coverage and running
# a lightweight e2e smoke test.  Produces an lcov report at the path
# specified by --output (default: coverage/c-coverage.lcov).
#
# Prerequisites: curl, tar, make, cargo, python3, lcov, gcc with gcov
#
# Usage:
#   tools/sonar/collect_nginx_coverage.sh [--output PATH] [--keep-artifacts]
set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"
# shellcheck disable=SC1090
source "${NATIVE_BUILD_HELPER}"
markdown_ensure_native_apple_silicon "${BASH_SOURCE[0]}" "$@"

NGINX_VERSION="stable"
OUTPUT_LCOV="${WORKSPACE_ROOT}/coverage/c-coverage.lcov"
KEEP_ARTIFACTS=0
BUILDROOT=""
RUNTIME=""
PORT=18199
BACKEND_PORT=18200

# ── Repeated curl header constants (shelldre:S1192) ─────────────────
readonly ACCEPT_MARKDOWN='Accept: text/markdown'
readonly AUTH_COOKIE_SESSION='Cookie: session_id=abc123'
readonly HDR_X_FORWARDED_PROTO_HTTPS='X-Forwarded-Proto: https'

usage() {
  cat >&2 <<EOF
Usage: $(basename "$0") [--output PATH] [--keep-artifacts] [--nginx-version VER]

Build NGINX with gcov instrumentation, run a smoke e2e scenario, and
collect an lcov coverage report for the C module source files.

Options:
  --output PATH          Output lcov file (default: coverage/c-coverage.lcov)
  --keep-artifacts       Do not delete the build directory on success
  --nginx-version VER    NGINX version (default: stable)
  --help                 Show this help
EOF
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT_LCOV="$2"
      shift 2
      ;;
    --keep-artifacts)
      KEEP_ARTIFACTS=1
      shift
      ;;
    --nginx-version)
      NGINX_VERSION="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

resolve_nginx_version() {
  # Resolve an NGINX version string from a channel name or pass through.
  #
  # Arguments:
  #   $1 - requested version: "stable", "mainline", or a literal version
  #
  # Output:
  #   Prints the resolved version number (e.g. "1.28.2") to stdout.
  #
  # Exit:
  #   1 if the channel page cannot be fetched or the version pattern is not found.
  local requested="$1"
  local page version

  case "${requested}" in
    stable|mainline)
      page="$(curl --proto '=https' --tlsv1.2 -fsSL https://nginx.org/en/download.html)"
      version="$(
        NGINX_DOWNLOAD_HTML="${page}" CHANNEL="${requested}" python3 - <<'PY'
import os, re
html = os.environ.get("NGINX_DOWNLOAD_HTML", "")
channel = os.environ.get("CHANNEL", "")
if channel == "mainline":
    pattern = r"Mainline version.*?nginx-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"
elif channel == "stable":
    pattern = r"Stable version.*?nginx-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"
else:
    raise SystemExit(1)
match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
if not match:
    raise SystemExit(1)
print(match.group(1))
PY
      )"
      if [[ -z "${version}" ]]; then
        echo "Failed to resolve ${requested} NGINX version" >&2
        exit 1
      fi
      printf '%s\n' "${version}"
      ;;
    *)
      printf '%s\n' "${requested}"
      ;;
  esac
  return 0
}

# ── Platform-aware lcov error policy ────────────────────────────────
# Apple Clang's gcov has known version-mismatch and processing quirks
# that do not occur with gcc on Linux.  We tolerate those on macOS but
# keep the Linux CI path strict.
build_lcov_ignore_args() {
  # Build platform-aware lcov error-suppression flags.
  #
  # Apple Clang's gcov has known version-mismatch and processing quirks
  # that do not occur with gcc on Linux.  We tolerate those on macOS but
  # keep the Linux CI path strict.
  #
  # Output:
  #   Prints space-separated --ignore-errors flags to stdout.
  local args=""
  # inconsistent: lcov limitation with branch tracking on multi-condition
  # if/else-if chains — line coverage is correct, only branch counts are
  # affected.  Known issue: https://github.com/linux-test-project/lcov/issues/296
  args="--ignore-errors inconsistent"

  if [[ "$(uname -s)" == "Darwin" ]]; then
    # mismatch: Apple Clang gcov produces version-mismatched .gcno/.gcda
    # gcov: Apple Clang gcov fails to process some NGINX core .gcda files
    args="${args} --ignore-errors mismatch --ignore-errors gcov"
  fi

  printf '%s' "${args}"
  return 0
}

cleanup() {
  # EXIT trap handler: stop NGINX and optionally remove build artifacts.
  #
  # Behaviour:
  #   - Sends a graceful stop signal to NGINX if it is still running.
  #   - Removes BUILDROOT on success unless --keep-artifacts was passed.
  #   - Prints the artifact path to stderr when artifacts are retained.
  local rc=$?
  if [[ -n "${RUNTIME:-}" ]] && [[ -f "${RUNTIME}/logs/nginx.pid" ]]; then
    local pid
    pid="$(cat "${RUNTIME}/logs/nginx.pid" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      "${RUNTIME}/sbin/nginx" -p "${RUNTIME}" -c conf/nginx.conf -s stop 2>/dev/null || true
      sleep 1
    fi
  fi

  if [[ $rc -eq 0 && "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT:-}" && -d "${BUILDROOT}" ]]; then
    rm -rf "${BUILDROOT}"
  elif [[ -n "${BUILDROOT:-}" && -d "${BUILDROOT}" ]]; then
    echo "Build artifacts kept at: ${BUILDROOT}" >&2
  fi
  return 0
}
trap cleanup EXIT

for cmd in curl tar make python3 lcov gcov; do
  markdown_need_cmd "${cmd}"
done

RUST_TARGET="$(markdown_detect_rust_target)"
NGINX_VERSION="$(resolve_nginx_version "${NGINX_VERSION}")"
BUILDROOT="$(mktemp -d /tmp/nginx-coverage-build.XXXXXX)"
RUNTIME="${BUILDROOT}/runtime"

umask 022
chmod 755 "${BUILDROOT}"
mkdir -p "${RUNTIME}/conf" "${RUNTIME}/html" "${RUNTIME}/logs"

echo "==> Building Rust converter (${RUST_TARGET}) with streaming feature"
markdown_prepare_rust_converter_release "${WORKSPACE_ROOT}" "${RUST_TARGET}" --features streaming

echo "==> Downloading NGINX ${NGINX_VERSION}"
curl --proto '=https' --tlsv1.2 -fsSL \
  "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz" \
  -o "${BUILDROOT}/nginx.tar.gz"
tar -xzf "${BUILDROOT}/nginx.tar.gz" -C "${BUILDROOT}" --strip-components=1

echo "==> Configuring NGINX with --coverage"
(
  cd "${BUILDROOT}"
  ./configure \
    --without-http_rewrite_module \
    --with-http_ssl_module \
    --prefix="${RUNTIME}" \
    --add-module="${WORKSPACE_ROOT}/components/nginx-module" \
    --with-cc-opt="--coverage -O0 -g" \
    --with-ld-opt="--coverage"
)

echo "==> Building and installing NGINX"
(
  cd "${BUILDROOT}"
  make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
  make install
)

# ── Detect IPv6 loopback support ─────────────────────────────────────
# Some CI runners disable IPv6; only emit the [::1] listen directive
# when the loopback interface actually supports it.  Detection uses
# filesystem checks only — no network probes required.
IPV6_LISTEN=""
if [[ -f /proc/net/if_inet6 ]] \
   || ip -6 addr show dev lo >/dev/null 2>&1 \
   || ifconfig lo0 inet6 >/dev/null 2>&1; then
    IPV6_LISTEN="listen [::1]:${BACKEND_PORT};"
fi

# ── Write a comprehensive nginx.conf for coverage ───────────────────
# This config exercises as many module code paths as possible:
# - Multiple server blocks with different directive combinations
# - Auth policy with cookie patterns
# - Conditional request support (ETag + If-Modified-Since)
# - Prometheus metrics format
# - Error handling (on_error pass/reject)
# - Various log verbosity levels
# - Token estimation
# - Large body threshold
cat > "${RUNTIME}/conf/nginx.conf" <<EOF
worker_processes  1;
error_log  logs/error.log debug;
pid        logs/nginx.pid;

events { worker_connections 128; }

http {
    include       mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout  5;

    # ── Primary server: full feature set ────────────────────────────
    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        # Enable gzip for responses to exercise decompression when proxied
        gzip on;
        gzip_types text/html text/plain application/xhtml+xml;
        gzip_min_length 0;

        location / {
            root html;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_conditional_requests full_support;
            markdown_log_verbosity debug;
            markdown_token_estimate on;
            markdown_on_error pass;
            markdown_max_size 1m;
            markdown_timeout 5000;
            markdown_trust_forwarded_headers on;
        }

        location /auth {
            root html;
            markdown_filter on;
            markdown_auth_policy deny;
            markdown_auth_cookies "session*" "*_logged_in";
            markdown_log_verbosity info;
        }

        # Auth location with Cache-Control: public set by upstream
        # (simulated via expires directive which sets CC before filters)
        # auth_policy=allow means authenticated requests ARE converted,
        # triggering the Cache-Control modification path
        location /auth-public-cc {
            root html;
            markdown_filter on;
            markdown_auth_policy allow;
            markdown_auth_cookies "session*" "*_logged_in";
            expires 1h;
        }

        # Auth location with allow policy but no CC header (exercises add-private path)
        location /auth-allow {
            root html;
            markdown_filter on;
            markdown_auth_policy allow;
            markdown_auth_cookies "session*" "*_logged_in";
        }

        location /reject-error {
            root html;
            markdown_filter on;
            markdown_on_error reject;
            markdown_log_verbosity warn;
        }

        location /ims-only {
            root html;
            markdown_filter on;
            markdown_conditional_requests if_modified_since_only;
        }

        location /no-conditional {
            root html;
            markdown_filter on;
            markdown_conditional_requests disabled;
        }

        location /no-wildcard {
            root html;
            markdown_filter on;
            markdown_on_wildcard off;
        }

        location /disabled {
            root html;
            markdown_filter off;
        }

        location /metrics {
            markdown_metrics;
        }

        location /metrics-prometheus {
            markdown_metrics;
            markdown_metrics_format prometheus;
        }

        location /gfm {
            root html;
            markdown_filter on;
            markdown_flavor gfm;
        }

        location /commonmark {
            root html;
            markdown_filter on;
            markdown_flavor commonmark;
        }

        location /small-limit {
            root html;
            markdown_filter on;
            markdown_max_size 100;
        }

        location /log-error {
            root html;
            markdown_filter on;
            markdown_log_verbosity error;
        }

        location /metrics-auto {
            markdown_metrics;
            markdown_metrics_format auto;
        }

        # Proxy to compressed backend (exercises decompression path)
        location /proxy-gzip {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/;
            proxy_set_header Accept-Encoding gzip;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_log_verbosity debug;
            markdown_on_error pass;
            markdown_max_size 1m;
        }

        # Streaming + gzip proxy (exercises streaming decompression path)
        location /streaming-proxy-gzip {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/;
            proxy_set_header Accept-Encoding gzip;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_streaming_on_error pass;
            markdown_log_verbosity debug;
            markdown_on_error pass;
            markdown_max_size 1m;
        }

        # Streaming + gzip proxy + tiny budget + reject
        location /streaming-proxy-gzip-reject {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/;
            proxy_set_header Accept-Encoding gzip;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_streaming_budget 1;
            markdown_streaming_on_error reject;
            markdown_log_verbosity debug;
            markdown_on_error pass;
            markdown_max_size 1m;
        }

        # Proxy to backend with Cache-Control: public (exercises strip-public CC path)
        location /proxy-public-cc {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/with-public-cc/;
            markdown_filter on;
            markdown_auth_policy allow;
            markdown_auth_cookies "session*" "*_logged_in";
        }

        # Proxy to backend with Cache-Control: no-store (exercises preserve-nostore path)
        location /proxy-nostore-cc {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/with-nostore-cc/;
            markdown_filter on;
            markdown_auth_policy allow;
            markdown_auth_cookies "session*" "*_logged_in";
        }

        # ── Streaming engine locations (requires --features streaming) ──
        location /streaming {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_log_verbosity debug;
        }

        location /streaming-auto {
            root html;
            markdown_filter on;
            markdown_streaming_engine auto;
            markdown_conditional_requests disabled;
            markdown_log_verbosity debug;
        }

        # Streaming engine selected by request arg (on/off/auto/invalid)
        location /streaming-variable {
            root html;
            markdown_filter on;
            markdown_streaming_engine \$arg_engine;
            markdown_conditional_requests disabled;
            markdown_log_verbosity debug;
        }

        location /streaming-fullsupport {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests full_support;
            markdown_log_verbosity debug;
        }

        location /streaming-ims-only {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests if_modified_since_only;
            markdown_log_verbosity debug;
        }

        # Streaming with tiny budget to trigger budget-exceeded failure
        location /streaming-tiny-budget {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_streaming_budget 1;
            markdown_log_verbosity debug;
            markdown_on_error pass;
        }

        location /streaming-reject-budget {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_streaming_budget 1;
            markdown_streaming_on_error reject;
            markdown_log_verbosity debug;
            markdown_on_error pass;
        }

        location /no-forwarded-trust {
            root html;
            markdown_filter on;
            markdown_trust_forwarded_headers off;
            markdown_log_verbosity debug;
        }

        # Streaming with ETag + token estimate (exercises finalize metadata paths)
        location /streaming-etag-tokens {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_etag on;
            markdown_token_estimate on;
            markdown_log_verbosity debug;
        }

        # Streaming with front_matter enabled (exercises conversion options)
        location /streaming-front-matter {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_front_matter on;
            markdown_log_verbosity debug;
        }

        # Streaming with GFM flavor (exercises prepare_conversion_options flavor path)
        location /streaming-gfm {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_flavor gfm;
            markdown_log_verbosity debug;
        }

        # Streaming with large budget (exercises normal streaming finalize)
        location /streaming-large-budget {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_streaming_budget 10m;
            markdown_etag on;
            markdown_token_estimate on;
            markdown_log_verbosity debug;
            markdown_on_error pass;
            markdown_max_size 10m;
        }

        # Streaming with on_error reject (exercises reject policy in streaming init)
        location /streaming-on-error-reject {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_on_error reject;
            markdown_log_verbosity debug;
        }

        # Streaming + gzip proxy with large budget (exercises multi-chunk decomp)
        location /streaming-proxy-gzip-large {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/;
            proxy_set_header Accept-Encoding gzip;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_streaming_budget 10m;
            markdown_streaming_on_error pass;
            markdown_etag on;
            markdown_token_estimate on;
            markdown_log_verbosity debug;
            markdown_on_error pass;
            markdown_max_size 10m;
        }

        # Streaming with warn verbosity (exercises verbosity gating)
        location /streaming-warn {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_log_verbosity warn;
        }

        # Streaming with error verbosity (exercises verbosity gating)
        location /streaming-error-verbosity {
            root html;
            markdown_filter on;
            markdown_streaming_engine on;
            markdown_conditional_requests disabled;
            markdown_log_verbosity error;
        }

        # Front matter + token estimate + base_url (exercises conversion_impl options)
        location /full-options {
            root html;
            markdown_filter on;
            markdown_front_matter on;
            markdown_token_estimate on;
            markdown_etag on;
            markdown_flavor gfm;
            markdown_trust_forwarded_headers on;
            markdown_log_verbosity debug;
            markdown_on_error pass;
        }
    }

    # ── Backend server: serves gzip-compressed HTML ─────────────────
    # This backend always compresses responses so the markdown filter's
    # decompression path (detect_compression_type + decompress_gzip) is
    # exercised when the proxy location forwards requests here.
    server {
        listen 127.0.0.1:${BACKEND_PORT};
        ${IPV6_LISTEN}
        server_name backend;

        gzip on;
        gzip_types text/html text/plain;
        gzip_min_length 0;

        location / {
            root html;
        }

        # Serve with Cache-Control: public for CC rewriting tests
        location /with-public-cc/ {
            alias html/;
            add_header Cache-Control "public, max-age=3600";
        }

        # Serve with Cache-Control: no-store for CC preservation tests
        location /with-nostore-cc/ {
            alias html/;
            add_header Cache-Control "no-store";
        }
    }
}
EOF

# ── Create test HTML fixtures ───────────────────────────────────────

# Create subdirectories for location blocks that use root html
for subdir in auth auth-public-cc auth-allow reject-error ims-only no-conditional \
              no-wildcard disabled gfm commonmark small-limit log-error \
              streaming streaming-auto streaming-tiny-budget \
              streaming-variable streaming-fullsupport streaming-ims-only \
              streaming-reject-budget no-forwarded-trust \
              streaming-etag-tokens streaming-front-matter streaming-gfm \
              streaming-large-budget streaming-on-error-reject \
              streaming-warn streaming-error-verbosity full-options; do
  mkdir -p "${RUNTIME}/html/${subdir}"
done

cat > "${RUNTIME}/html/index.html" <<'HTML'
<!doctype html>
<html>
  <head><title>Coverage Test</title></head>
  <body>
    <h1>Hello Coverage</h1>
    <p>This page exercises the markdown filter module.</p>
    <ul><li>Item 1</li><li>Item 2</li></ul>
    <a href="https://example.com">Link</a>
    <img src="image.png" alt="test image">
  </body>
</html>
HTML

# Copy index.html to all subdirectories so location blocks serve 200.
# Note: small-limit is intentionally excluded — it only receives large.html
# (below) so the size-limit rejection test exercises the over-limit path.
for subdir in auth auth-public-cc auth-allow reject-error ims-only no-conditional \
              no-wildcard disabled gfm commonmark log-error \
              streaming streaming-auto streaming-tiny-budget \
              streaming-variable streaming-fullsupport streaming-ims-only \
              streaming-reject-budget no-forwarded-trust \
              streaming-etag-tokens streaming-front-matter streaming-gfm \
              streaming-large-budget streaming-on-error-reject \
              streaming-warn streaming-error-verbosity full-options; do
  cp "${RUNTIME}/html/index.html" "${RUNTIME}/html/${subdir}/index.html"
done

cat > "${RUNTIME}/html/large.html" <<'HTML'
<!doctype html>
<html>
  <head><title>Large Page</title></head>
  <body>
HTML
for i in $(seq 1 50); do
  printf '    <h2>Section %d</h2>\n    <p>Paragraph %d with some content.</p>\n' "$i" "$i" >> "${RUNTIME}/html/large.html"
done
cat >> "${RUNTIME}/html/large.html" <<'HTML'
  </body>
</html>
HTML

# Copy large.html to small-limit for size-limit rejection test
cp "${RUNTIME}/html/large.html" "${RUNTIME}/html/small-limit/large.html"

# Copy large.html to streaming locations for multi-chunk and budget tests
for subdir in streaming streaming-large-budget streaming-etag-tokens \
              streaming-gfm streaming-front-matter streaming-on-error-reject \
              streaming-warn streaming-error-verbosity full-options; do
  cp "${RUNTIME}/html/large.html" "${RUNTIME}/html/${subdir}/large.html"
done

# Create table.html for streaming fallback testing (table triggers fallback)
cat > "${RUNTIME}/html/streaming/table.html" <<'HTML'
<!doctype html>
<html>
  <head><title>Table Page</title></head>
  <body>
    <h1>Page with Table</h1>
    <p>This page contains a table that triggers streaming fallback.</p>
    <table>
      <tr><th>Name</th><th>Value</th></tr>
      <tr><td>Alpha</td><td>100</td></tr>
      <tr><td>Beta</td><td>200</td></tr>
    </table>
    <p>Content after table.</p>
  </body>
</html>
HTML
cp "${RUNTIME}/html/streaming/table.html" "${RUNTIME}/html/streaming-large-budget/table.html"
cp "${RUNTIME}/html/streaming/table.html" "${RUNTIME}/html/streaming-on-error-reject/table.html"

echo "==> Starting NGINX on 127.0.0.1:${PORT}"
"${RUNTIME}/sbin/nginx" -p "${RUNTIME}" -c conf/nginx.conf
sleep 1

echo "==> Running coverage smoke scenario"

# ── Basic conversion scenarios ───────────────────────────────────────

# Request with markdown Accept header (exercises filter + conversion)
curl -sS -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  markdown request: HTTP %{http_code}\n"

# Request without markdown Accept (exercises passthrough/eligibility)
curl -sS -H 'Accept: text/html' "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  html request: HTTP %{http_code}\n"

# Wildcard Accept
curl -sS -H 'Accept: */*' "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  wildcard request: HTTP %{http_code}\n"

# HEAD request
curl -sS -I -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  HEAD request: HTTP %{http_code}\n"

# Large page (chain accumulation)
curl -sS -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/large.html" -o /dev/null -w "  large page: HTTP %{http_code}\n"

# ── Auth detection scenarios (Req 2) ────────────────────────────────

# Bearer token auth (dummy placeholder — exercises auth detection code path)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Authorization: Bearer DUMMY_TEST_TOKEN' \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth bearer: HTTP %{http_code}\n"

# Cookie prefix match (session*)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth cookie prefix: HTTP %{http_code}\n"

# Cookie suffix match (*_logged_in)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Cookie: wordpress_logged_in=val' \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth cookie suffix: HTTP %{http_code}\n"

# No auth credentials (negative detection)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth none: HTTP %{http_code}\n"

# Non-matching cookie
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Cookie: tracking=xyz' \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth non-matching cookie: HTTP %{http_code}\n"

# Multi-cookie header (exercises cookie parsing: skip whitespace, read name, iterate)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Cookie: tracking=xyz; session_abc=val1; other=val2' \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth multi-cookie: HTTP %{http_code}\n"

# Auth with Cache-Control: public upstream (exercises strip-public-and-append-private)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/auth-public-cc/index.html" -o /dev/null -w "  auth strip-public CC: HTTP %{http_code}\n"

# Auth suffix match with Cache-Control: public (exercises CC rewriting + suffix match)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Cookie: wordpress_logged_in=val' \
  "http://127.0.0.1:${PORT}/auth-public-cc/index.html" -o /dev/null -w "  auth suffix + CC rewrite: HTTP %{http_code}\n"

# Auth-allow with cookie (exercises add-private CC path — no existing CC header)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth-allow add-private: HTTP %{http_code}\n"

# Auth-allow with bearer (exercises is_authenticated + CC modification)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Authorization: Bearer DUMMY_TEST_TOKEN' \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth-allow bearer: HTTP %{http_code}\n"

# Auth-allow with CC expires (exercises append-private to existing CC)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/auth-public-cc/index.html" -o /dev/null -w "  auth-allow CC append: HTTP %{http_code}\n"

# Proxy with CC: public + auth cookie (exercises strip-public-and-append-private)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/proxy-public-cc/index.html" -o /dev/null -w "  proxy CC public strip: HTTP %{http_code}\n"

# Proxy with CC: no-store + auth cookie (exercises preserve-nostore path)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/proxy-nostore-cc/index.html" -o /dev/null -w "  proxy CC nostore preserve: HTTP %{http_code}\n"

# Proxy with CC: public + suffix cookie (exercises CC rewriting + suffix match)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Cookie: wordpress_logged_in=val' \
  "http://127.0.0.1:${PORT}/proxy-public-cc/index.html" -o /dev/null -w "  proxy CC public suffix: HTTP %{http_code}\n"

# ── Conditional request scenarios (Req 3) ───────────────────────────

# If-None-Match wildcard
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: *' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM wildcard: HTTP %{http_code}\n"

# If-None-Match quoted etag
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "some-etag-value"' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM quoted: HTTP %{http_code}\n"

# If-None-Match multi-etag
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "etag1", "etag2"' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM multi: HTTP %{http_code}\n"

# If-Modified-Since
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-Modified-Since: Thu, 01 Jan 2099 00:00:00 GMT' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  conditional IMS: HTTP %{http_code}\n"

# If-None-Match to IMS-only location (bypass)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "etag"' \
  "http://127.0.0.1:${PORT}/ims-only/index.html" -o /dev/null -w "  INM ims-only bypass: HTTP %{http_code}\n"

# If-None-Match to disabled conditional location (bypass)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "etag"' \
  "http://127.0.0.1:${PORT}/no-conditional/index.html" -o /dev/null -w "  INM disabled bypass: HTTP %{http_code}\n"

# ── Error path scenarios (Req 4) ────────────────────────────────────

# Non-existent page (404 passthrough)
curl -sS -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/nonexistent.html" -o /dev/null -w "  404 path: HTTP %{http_code}\n" || true

# Reject-error path
curl -sS -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/reject-error/index.html" -o /dev/null -w "  reject-error: HTTP %{http_code}\n"

# POST request (method ineligibility)
curl -sS -X POST -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  POST method: HTTP %{http_code}\n" || true

# Range header (range skip)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Range: bytes=0-100' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  range skip: HTTP %{http_code}\n"

# ── Accept header diversity scenarios (Req 6) ──────────────────────

# Subtype wildcard
curl -sS -H 'Accept: text/*' "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  text/* subtype wildcard: HTTP %{http_code}\n"

# Q-value sorting
curl -sS -H 'Accept: text/html;q=0.9, text/markdown;q=1.0' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  q-value sorting: HTTP %{http_code}\n"

# Explicit rejection (q=0)
curl -sS -H 'Accept: text/markdown;q=0' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  q=0 rejection: HTTP %{http_code}\n"

# No markdown match
curl -sS -H 'Accept: text/html' "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  no markdown match: HTTP %{http_code}\n"

# Multi-entry tie-break
curl -sS -H 'Accept: text/markdown, text/html' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  multi-entry tie-break: HTTP %{http_code}\n"

# Wildcard-disabled rejection
curl -sS -H 'Accept: */*' "http://127.0.0.1:${PORT}/no-wildcard/index.html" -o /dev/null -w "  wildcard disabled: HTTP %{http_code}\n"

# ── Body filter, headers, and flavor scenarios (Req 7, 8) ──────────

# GFM flavor
curl -sS -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/gfm/index.html" -o /dev/null -w "  GFM flavor: HTTP %{http_code}\n"

# CommonMark flavor
curl -sS -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/commonmark/index.html" -o /dev/null -w "  CommonMark flavor: HTTP %{http_code}\n"

# Size-limit rejection (small-limit with large file)
curl -sS -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/small-limit/large.html" -o /dev/null -w "  size-limit rejection: HTTP %{http_code}\n"

# Auth request triggering conversion (Cache-Control modification)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth conversion (Cache-Control): HTTP %{http_code}\n"

# ── Decompression scenarios (exercises ngx_http_markdown_decompression.c) ──

# Proxy to gzip backend: exercises detect_compression_type + decompress_gzip
# The backend server compresses the response, and the markdown filter
# decompresses it before converting HTML to Markdown.
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/proxy-gzip/index.html" -o /dev/null -w "  gzip proxy decompression: HTTP %{http_code}\n"

# Proxy gzip with large file (exercises chain_size + chain_to_buffer)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/proxy-gzip/large.html" -o /dev/null -w "  gzip proxy large: HTTP %{http_code}\n"

# Streaming + gzip proxy (exercises streaming_decomp_impl.h)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-proxy-gzip/index.html" -o /dev/null -w "  streaming gzip proxy: HTTP %{http_code}\n"

# Streaming + gzip proxy + reject on pre-commit failure
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-proxy-gzip-reject/large.html" \
  -o /dev/null -w "  streaming gzip reject: HTTP %{http_code}\n" || true

# ── X-Forwarded header scenarios (exercises conversion_impl.h header iteration) ──

# X-Forwarded-Proto + X-Forwarded-Host (exercises find_request_header_value + const_strncasecmp)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  -H "${HDR_X_FORWARDED_PROTO_HTTPS}" -H 'X-Forwarded-Host: example.com' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  X-Forwarded headers: HTTP %{http_code}\n"

# Only X-Forwarded-Proto (partial proxy headers — exercises fallback path)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${HDR_X_FORWARDED_PROTO_HTTPS}" \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  X-Forwarded-Proto only: HTTP %{http_code}\n"

# Forwarded headers ignored when trust_forwarded_headers=off
curl -sS -H "${ACCEPT_MARKDOWN}" \
  -H "${HDR_X_FORWARDED_PROTO_HTTPS}" -H 'X-Forwarded-Host: ignored.example' \
  "http://127.0.0.1:${PORT}/no-forwarded-trust/index.html" -o /dev/null -w "  X-Forwarded trust disabled: HTTP %{http_code}\n"

# ── Streaming engine scenarios (exercises streaming code paths) ─────

# Streaming conversion (exercises streaming path selection + reason codes)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming/index.html" -o /dev/null -w "  streaming convert: HTTP %{http_code}\n"

# Streaming auto mode (exercises auto engine selection)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-auto/index.html" -o /dev/null -w "  streaming auto: HTTP %{http_code}\n"

# Streaming with non-markdown Accept (exercises streaming skip path)
curl -sS -H 'Accept: text/html' \
  "http://127.0.0.1:${PORT}/streaming/index.html" -o /dev/null -w "  streaming skip: HTTP %{http_code}\n"

# Streaming with tiny budget (exercises budget-exceeded failure + fallback)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-tiny-budget/index.html" -o /dev/null -w "  streaming budget fail: HTTP %{http_code}\n"

# Streaming tiny budget + reject branch
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-reject-budget/index.html" -o /dev/null -w "  streaming budget reject: HTTP %{http_code}\n" || true

# Streaming HEAD and Range requests
curl -sS -I -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming/index.html" -o /dev/null -w "  streaming HEAD: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Range: bytes=0-100' \
  "http://127.0.0.1:${PORT}/streaming/index.html" -o /dev/null -w "  streaming Range: HTTP %{http_code}\n"

# Streaming conditional modes
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "etag-value"' \
  "http://127.0.0.1:${PORT}/streaming-fullsupport/index.html" -o /dev/null -w "  streaming full_support INM: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-Modified-Since: Thu, 01 Jan 2099 00:00:00 GMT' \
  "http://127.0.0.1:${PORT}/streaming-ims-only/index.html" -o /dev/null -w "  streaming ims-only IMS: HTTP %{http_code}\n"

# Streaming engine variable selection (on/off/auto/invalid)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-variable/index.html?engine=on" -o /dev/null -w "  streaming variable on: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-variable/index.html?engine=off" -o /dev/null -w "  streaming variable off: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-variable/index.html?engine=auto" -o /dev/null -w "  streaming variable auto: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-variable/index.html?engine=bad" -o /dev/null -w "  streaming variable invalid: HTTP %{http_code}\n"

# ── Extended streaming scenarios (exercises streaming_impl.h deeper paths) ──

# Streaming with large content (exercises multi-chunk feed, commit boundary, finalize)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming/large.html" -o /dev/null -w "  streaming large: HTTP %{http_code}\n"

# Streaming with table content (exercises fallback to full-buffer path)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming/table.html" -o /dev/null -w "  streaming table fallback: HTTP %{http_code}\n"

# Streaming with ETag + token estimate (exercises finalize metadata paths)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-etag-tokens/index.html" -o /dev/null -w "  streaming etag+tokens: HTTP %{http_code}\n"

# Streaming with ETag + token estimate + large content
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-etag-tokens/large.html" -o /dev/null -w "  streaming etag+tokens large: HTTP %{http_code}\n"

# Streaming with front_matter (exercises conversion options front_matter path)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-front-matter/index.html" -o /dev/null -w "  streaming front-matter: HTTP %{http_code}\n"

# Streaming with GFM flavor (exercises prepare_conversion_options flavor=1)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-gfm/index.html" -o /dev/null -w "  streaming GFM: HTTP %{http_code}\n"

# Streaming with large budget + large content (exercises normal finalize path)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-large-budget/index.html" -o /dev/null -w "  streaming large budget: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-large-budget/large.html" -o /dev/null -w "  streaming large budget+large: HTTP %{http_code}\n"

# Streaming with table + large budget (exercises fallback with prebuffer transfer)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-large-budget/table.html" -o /dev/null -w "  streaming large budget table: HTTP %{http_code}\n"

# Streaming with on_error reject (exercises reject policy in streaming init)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-on-error-reject/index.html" -o /dev/null -w "  streaming on_error reject: HTTP %{http_code}\n"

# Streaming with table + on_error reject (exercises fallback + reject)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-on-error-reject/table.html" -o /dev/null -w "  streaming table+reject: HTTP %{http_code}\n"

# Streaming with warn verbosity (exercises verbosity gating — non-failure suppressed)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-warn/index.html" -o /dev/null -w "  streaming warn verbosity: HTTP %{http_code}\n"

# Streaming with error verbosity (exercises verbosity gating — only errors)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-error-verbosity/index.html" -o /dev/null -w "  streaming error verbosity: HTTP %{http_code}\n"

# ── Extended streaming decompression scenarios ──────────────────────

# Streaming + gzip proxy with large content (exercises inflate_loop, expand_buf)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-proxy-gzip/large.html" -o /dev/null -w "  streaming gzip large: HTTP %{http_code}\n"

# Streaming + gzip proxy with large budget (exercises full decomp + convert path)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-proxy-gzip-large/index.html" -o /dev/null -w "  streaming gzip large-budget: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-proxy-gzip-large/large.html" -o /dev/null -w "  streaming gzip large-budget+large: HTTP %{http_code}\n"

# ── Extended conversion_impl.h scenarios ────────────────────────────

# Full options: front_matter + token_estimate + etag + gfm + forwarded headers
curl -sS -H "${ACCEPT_MARKDOWN}" \
  -H "${HDR_X_FORWARDED_PROTO_HTTPS}" -H 'X-Forwarded-Host: cdn.example.com' \
  "http://127.0.0.1:${PORT}/full-options/index.html" -o /dev/null -w "  full-options: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" \
  -H "${HDR_X_FORWARDED_PROTO_HTTPS}" -H 'X-Forwarded-Host: cdn.example.com' \
  "http://127.0.0.1:${PORT}/full-options/large.html" -o /dev/null -w "  full-options large: HTTP %{http_code}\n"

# Full options without forwarded headers (exercises direct request base_url)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/full-options/index.html" -o /dev/null -w "  full-options direct: HTTP %{http_code}\n"

# ── Extended reason code scenarios ──────────────────────────────────

# Streaming success (exercises STREAMING_CONVERT reason code)
# Already covered by streaming/index.html above

# Streaming budget fail with warn verbosity (exercises reason code + verbosity)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming-warn/large.html" -o /dev/null -w "  streaming warn+large: HTTP %{http_code}\n"

# Disabled location (exercises SKIP_CONFIG reason code)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/disabled/index.html" -o /dev/null -w "  disabled config: HTTP %{http_code}\n"

# Log-error verbosity (exercises error-only verbosity gating)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/log-error/index.html" -o /dev/null -w "  log-error verbosity: HTTP %{http_code}\n"

# ── Metrics scenarios (Req 5) — after conversion scenarios ──────────

# Prometheus format
curl -sS "http://127.0.0.1:${PORT}/metrics-prometheus" -o /dev/null -w "  metrics prometheus: HTTP %{http_code}\n"
curl -sS -H 'Accept: text/plain; version=0.0.4' \
  "http://127.0.0.1:${PORT}/metrics-prometheus" -o /dev/null -w "  metrics prometheus accept-v0.0.4: HTTP %{http_code}\n"

# Auto format (plain text)
curl -sS -H 'Accept: text/plain' "http://127.0.0.1:${PORT}/metrics-auto" -o /dev/null -w "  metrics auto text: HTTP %{http_code}\n"

# Auto format (JSON)
curl -sS -H 'Accept: application/json' "http://127.0.0.1:${PORT}/metrics-auto" -o /dev/null -w "  metrics auto json: HTTP %{http_code}\n"

# Default metrics endpoint
curl -sS "http://127.0.0.1:${PORT}/metrics" -o /dev/null -w "  metrics default: HTTP %{http_code}\n"

# IPv6 metrics request (exercises sockaddr_in6 branch in metrics_impl.h)
# Only attempt when IPv6 loopback is available on this host.
# Clear-text HTTP to localhost is intentional — this is a local coverage
# test, not a production data path.
if [[ -n "${IPV6_LISTEN}" ]]; then
    curl -sS -6 "http://[::1]:${BACKEND_PORT}/" -o /dev/null -w "  IPv6 backend: HTTP %{http_code}\n" 2>/dev/null || true  # NOSONAR — localhost-only coverage test
fi

echo "==> Stopping NGINX (flush gcov data)"
"${RUNTIME}/sbin/nginx" -p "${RUNTIME}" -c conf/nginx.conf -s stop
sleep 2

echo "==> Running extended streaming failure/cache e2e coverage"
"${WORKSPACE_ROOT}/tools/e2e/verify_streaming_failure_cache_e2e.sh" \
  --nginx-bin "${RUNTIME}/sbin/nginx" \
  --port 18296 \
  --upstream-port 19296 \
  --markdown-max-size 1m

echo "==> Running streaming e2e coverage"
if ! bash "${WORKSPACE_ROOT}/tools/e2e/verify_streaming_e2e.sh" \
  --nginx-bin "${RUNTIME}/sbin/nginx"; then
    echo "  WARNING: streaming e2e coverage run failed; continuing" >&2
fi

echo "==> Running chunked streaming e2e coverage (smoke)"
if ! NGINX_BIN="${RUNTIME}/sbin/nginx" \
  bash "${WORKSPACE_ROOT}/tools/e2e/verify_chunked_streaming_native_e2e.sh" \
    --profile smoke \
    --port 18294 \
    --upstream-port 19294 \
    --markdown-max-size 1m; then
    echo "  WARNING: chunked streaming e2e coverage run failed; continuing" >&2
fi

echo "==> Running large markdown response e2e coverage"
if ! NGINX_BIN="${RUNTIME}/sbin/nginx" \
  bash "${WORKSPACE_ROOT}/tools/e2e/verify_large_markdown_response_e2e.sh" \
    --port 18291; then
    echo "  WARNING: large markdown e2e coverage run failed; continuing" >&2
fi

echo "==> Running proxy TLS backend e2e coverage"
if ! NGINX_BIN="${RUNTIME}/sbin/nginx" \
  bash "${WORKSPACE_ROOT}/tools/e2e/verify_proxy_tls_backend_e2e.sh" \
    --port 18289 \
    --backend-port 19289; then
    echo "  WARNING: proxy TLS backend e2e coverage run failed; continuing" >&2
fi

# ── Collect gcov/lcov coverage ──────────────────────────────────────
echo "==> Collecting coverage data"
OUTPUT_DIR="$(dirname "${OUTPUT_LCOV}")"
mkdir -p "${OUTPUT_DIR}"

LCOV_IGNORE="$(build_lcov_ignore_args)"

# Capture raw coverage from all compiled objects.
# shellcheck disable=SC2086
lcov --capture \
  --directory "${BUILDROOT}/objs" \
  --output-file "${OUTPUT_DIR}/c-e2e-raw.lcov" \
  --rc branch_coverage=1 \
  ${LCOV_IGNORE}

# Extract only our module source files; NGINX core files are discarded.
# --ignore-errors unused is expected here: we intentionally discard the
# majority of captured files (NGINX core) and keep only our module.
# shellcheck disable=SC2086
lcov --extract "${OUTPUT_DIR}/c-e2e-raw.lcov" \
  "*/components/nginx-module/src/*" \
  --output-file "${OUTPUT_LCOV}" \
  --rc branch_coverage=1 \
  --ignore-errors unused \
  ${LCOV_IGNORE}

rm -f "${OUTPUT_DIR}/c-e2e-raw.lcov"

echo "==> C module e2e coverage report: ${OUTPUT_LCOV}"
# shellcheck disable=SC2086
lcov --summary "${OUTPUT_LCOV}" --rc branch_coverage=1 \
  ${LCOV_IGNORE} 2>&1 | grep -E 'lines|functions|branches' || true

# ── Advisory per-file coverage threshold checks ────────────────────
# These thresholds are advisory (warnings only, not blocking).
# The lcov report is always produced regardless of coverage level.
echo "==> Checking advisory coverage thresholds"
# shellcheck disable=SC2086
lcov --list "${OUTPUT_LCOV}" --rc branch_coverage=1 \
  ${LCOV_IGNORE} 2>/dev/null | while IFS= read -r line; do
  # Extract file path and line coverage percentage.
  # lcov --list output is pipe-delimited; the Lines rate is in the second
  # pipe-delimited column (e.g. "60.0%   38").  Extract the percentage
  # from that column rather than using $NF which may pick up branch counts.
  file="$(echo "${line}" | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $1); print $1}')"
  pct="$(echo "${line}" | awk -F'|' '{gsub(/%/, "", $2); gsub(/^[ \t]+/, "", $2); split($2, a, " "); print a[1]}')"
  # Remove trailing % if present
  pct="${pct%\%}"

  # Skip non-data lines
  case "${file}" in
    ngx_http_markdown_*) ;;
    *) continue ;;
  esac

  # Advisory thresholds per file (Req 13)
  threshold=""
  case "${file}" in
    ngx_http_markdown_auth.c)                threshold=60 ;;
    ngx_http_markdown_conditional.c)         threshold=40 ;;
    ngx_http_markdown_error.c)               threshold=50 ;;
    ngx_http_markdown_prometheus_impl.h)     threshold=50 ;;
    ngx_http_markdown_reason.c)              threshold=50 ;;
    ngx_http_markdown_accept.c)              threshold=70 ;;
    ngx_http_markdown_config_handlers_impl.h) threshold=40 ;;
    *) ;;
  esac

  if [[ -n "${threshold}" ]] && [[ -n "${pct}" ]]; then
    # Compare as integers (truncate decimal); skip non-numeric values
    pct_int="${pct%%.*}"
    if [[ -n "${pct_int}" ]] && [[ "${pct_int}" =~ ^[0-9]+$ ]] \
       && [[ "${pct_int}" -lt "${threshold}" ]]; then
      echo "  WARNING: ${file} line coverage ${pct}% below advisory threshold ${threshold}%" >&2
    fi
  fi
done || true

# Aggregate line coverage advisory check (80% minimum)
# shellcheck disable=SC2086
aggregate="$(lcov --summary "${OUTPUT_LCOV}" --rc branch_coverage=1 \
  ${LCOV_IGNORE} 2>&1 | awk '/lines\.\.\.\./{print $2}' || true)"
if [[ -n "${aggregate}" ]]; then
  aggregate_int="${aggregate%%.*}"
  if [[ -n "${aggregate_int}" ]] && [[ "${aggregate_int}" -lt 80 ]]; then
    echo "  WARNING: Aggregate line coverage ${aggregate}% below 80% minimum" >&2
  else
    echo "  Aggregate line coverage: ${aggregate}% (meets 80% minimum)"
  fi
fi
