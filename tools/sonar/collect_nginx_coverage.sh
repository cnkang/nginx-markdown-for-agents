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
readonly HDR_IF_MODIFIED_SINCE_FUTURE='If-Modified-Since: Thu, 01 Jan 2099 00:00:00 GMT'

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
  #
  # Repeat the same category twice to suppress message spam while still
  # producing the report.
  args="--ignore-errors inconsistent,inconsistent"

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
    --with-cc-opt="--coverage -O0 -g -DNGX_HTTP_MARKDOWN_NO_RUST_DECOMPRESS" \
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
    gzip_http_version 1.0;

    # ── Primary server: full feature set ────────────────────────────
    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        # Enable gzip for responses to exercise decompression when proxied
        gzip on;
        # text/html is nginx's default gzip type, so listing it here only
        # triggers duplicate MIME type warnings during config parsing.
        gzip_types text/plain application/xhtml+xml;
        gzip_min_length 0;

        location / {
            root html;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_etag on;
            markdown_otel on;
            markdown_otel_tracing on;
            markdown_conditional_requests full_support;
            markdown_log_verbosity debug;
            markdown_token_estimate on;
            markdown_on_error pass;
            markdown_max_size 1m;
            markdown_timeout 5000;
            markdown_trust_forwarded_headers on;
            markdown_dynamic_config on;
            markdown_dynamic_config_path ${RUNTIME}/conf/markdown-dynconf.conf;
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

        # ETag disabled with full_support conditional requests; exercises the
        # branch where If-None-Match is present but ETag comparison cannot
        # proceed because markdown_etag is off.
        location /no-etag {
            root html;
            markdown_filter on;
            markdown_etag off;
            markdown_conditional_requests full_support;
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

        location /diagnostics {
            markdown_diagnostics on;
            markdown_diagnostics_allow 127.0.0.1;
            markdown_diagnostics_allow 127.0.0.1/24;
            markdown_diagnostics_allow ::1;
        }

        location /diagnostics-forbidden {
            markdown_diagnostics on;
            markdown_diagnostics_allow 10.0.0.0/8;
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

        # Proxy to backend that advertises gzip without valid gzip payload
        # (exercises decompression error + fail-open handling)
        location /proxy-fake-gzip {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/fake-gzip/;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_log_verbosity debug;
            markdown_on_error pass;
        }

        # Proxy to backend that advertises brotli without valid brotli payload
        # (exercises unsupported/invalid compressed-stream handling)
        location /proxy-fake-br {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/fake-br/;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_log_verbosity debug;
            markdown_on_error pass;
        }

        # Proxy to backend that advertises deflate without valid deflate payload
        # (exercises deflate decompression error handling)
        location /proxy-fake-deflate {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/fake-deflate/;
            markdown_filter on;
            markdown_on_wildcard on;
            markdown_log_verbosity debug;
            markdown_on_error pass;
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

        # Proxy to backend with Cache-Control: private (exercises has_private no-op path)
        location /proxy-private-cc {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/with-private-cc/;
            markdown_filter on;
            markdown_auth_policy allow;
            markdown_auth_cookies "session*" "*_logged_in";
        }

        # Proxy to backend with Cache-Control: private, no-store (preserve no-store)
        location /proxy-private-nostore-cc {
            proxy_pass http://127.0.0.1:${BACKEND_PORT}/with-private-nostore-cc/;
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
        gzip_types text/plain;
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

        # Serve with Cache-Control: private for CC has_private path testing
        location /with-private-cc/ {
            alias html/;
            add_header Cache-Control "private, max-age=60";
        }

        # Serve with Cache-Control: private, no-store for CC preserve testing
        location /with-private-nostore-cc/ {
            alias html/;
            add_header Cache-Control "private, no-store";
        }

        # Deliberately mislabeled encodings for decompression negative-path coverage.
        # Each serves plain HTML with a Content-Encoding header that does not
        # match the actual body, triggering decompression error handling.
        location /fake-gzip/ {
            alias html/;
            add_header Content-Encoding "gzip" always;
        }

        # Brotli encoding advertised but unsupported/mismatched payload.
        location /fake-br/ {
            alias html/;
            add_header Content-Encoding "br" always;
        }

        # Deflate encoding advertised but body is not deflate-compressed.
        location /fake-deflate/ {
            alias html/;
            add_header Content-Encoding "deflate" always;
        }
    }
}
EOF

# ── Create test HTML fixtures ───────────────────────────────────────

# Create subdirectories for location blocks that use root html
for subdir in auth auth-public-cc auth-allow reject-error ims-only no-conditional no-etag \
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
for subdir in auth auth-public-cc auth-allow reject-error ims-only no-conditional no-etag \
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

cat > "${RUNTIME}/conf/markdown-dynconf.conf" <<'EOF'
markdown_filter=on
prune_noise=on
log_verbosity=debug
streaming_budget=4k
memory_budget=8m
EOF

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

# OTel trace context parsing path.
curl -sS -H "${ACCEPT_MARKDOWN}" \
  -H 'traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  otel traceparent request: HTTP %{http_code}\n"

# Dynconf watcher/reload path.
cat > "${RUNTIME}/conf/markdown-dynconf.conf" <<'EOF'
markdown_filter=on
prune_noise=off
log_verbosity=info
streaming_budget=2k
memory_budget=4m
EOF
sleep 2
curl -sS -H "${ACCEPT_MARKDOWN}" "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  dynconf reload trigger: HTTP %{http_code}\n"

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
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${HDR_IF_MODIFIED_SINCE_FUTURE}" \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  conditional IMS: HTTP %{http_code}\n"

# If-None-Match to IMS-only location (bypass)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "etag"' \
  "http://127.0.0.1:${PORT}/ims-only/index.html" -o /dev/null -w "  INM ims-only bypass: HTTP %{http_code}\n"

# If-None-Match to disabled conditional location (bypass)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "etag"' \
  "http://127.0.0.1:${PORT}/no-conditional/index.html" -o /dev/null -w "  INM disabled bypass: HTTP %{http_code}\n"

# If-None-Match with ETag disabled (branch: cannot compare)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "etag-disabled"' \
  "http://127.0.0.1:${PORT}/no-etag/index.html" -o /dev/null -w "  INM etag-off bypass: HTTP %{http_code}\n"

# Malformed If-None-Match (unterminated quote)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "unterminated' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM malformed quote: HTTP %{http_code}\n"

# Weak/quoted ETag token handling
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: W/"some-etag-value"' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM weak validator: HTTP %{http_code}\n"

# Unquoted multi-token ETag parsing
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: etagA, etagB' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM unquoted multi: HTTP %{http_code}\n"

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

# Proxy with invalid/mislabeled compressed payloads
# These exercise the decompression error/fail-open paths: the backend
# advertises Content-Encoding but serves plain HTML, so the decompressor
# must detect the mismatch and fall back to pass-through or error.
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/proxy-fake-gzip/index.html" \
  -o /dev/null -w "  fake gzip payload: HTTP %{http_code}\n" || true
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/proxy-fake-br/index.html" \
  -o /dev/null -w "  fake br payload: HTTP %{http_code}\n" || true
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/proxy-fake-deflate/index.html" \
  -o /dev/null -w "  fake deflate payload: HTTP %{http_code}\n" || true

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
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${HDR_IF_MODIFIED_SINCE_FUTURE}" \
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

# ── Diagnostics scenarios ───────────────────────────────────────────
curl -sS "http://127.0.0.1:${PORT}/diagnostics" -o /dev/null -w "  diagnostics endpoint: HTTP %{http_code}\n"
curl -sS "http://127.0.0.1:${PORT}/diagnostics-forbidden" -o /dev/null -w "  diagnostics forbidden: HTTP %{http_code}\n"
curl -sS -X POST "http://127.0.0.1:${PORT}/diagnostics" -o /dev/null -w "  diagnostics POST method: HTTP %{http_code}\n"

# Multi-part request headers (exercises ngx_http_markdown_find_request_header multi-part traversal)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  -H "X-H1: 1" -H "X-H2: 2" -H "X-H3: 3" -H "X-H4: 4" -H "X-H5: 5" \
  -H "X-H6: 6" -H "X-H7: 7" -H "X-H8: 8" -H "X-H9: 9" -H "X-H10: 10" \
  -H "X-H11: 11" -H "X-H12: 12" -H "X-H13: 13" -H "X-H14: 14" -H "X-H15: 15" \
  -H "X-H16: 16" -H "X-H17: 17" -H "X-H18: 18" -H "X-H19: 19" -H "X-H20: 20" \
  -H "X-H21: 21" -H "X-H22: 22" -H "X-H23: 23" -H "X-H24: 24" -H "X-H25: 25" \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  multi-part headers: HTTP %{http_code}\n"

# ── Extended auth Cache-Control scenarios (coverage for ngx_http_markdown_auth.c) ──

# Auth + no existing CC header on auth-public-cc (exercises add-private path when no CC exists)
# First request without expires header to establish baseline
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Authorization: Bearer TOKEN_CC' \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth bearer no-cc: HTTP %{http_code}\n"

# Auth + cookie with session prefix + no CC (add-private path)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Cookie: session_new=xyz' \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth session cookie no-cc: HTTP %{http_code}\n"

# Auth + CC already has private (should be no-op)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  -H 'Cache-Control: private, max-age=60' \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth cc-already-private: HTTP %{http_code}\n"

# Auth + CC no-store (preserve, never downgrade)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  -H 'Cache-Control: no-store' \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth cc-nostore-preserve: HTTP %{http_code}\n"

# Auth + CC private, no-store (preserve no-store)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  -H 'Cache-Control: private, no-store' \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth cc-private-nostore: HTTP %{http_code}\n"

# Auth + multiple CC headers (exercises scan of multiple Cache-Control entries)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  -H 'Cache-Control: public, max-age=3600' \
  -H 'Cache-Control: must-revalidate' \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth multi-cc: HTTP %{http_code}\n"

# Auth + CC public, max-age (strip public, append private)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  -H 'Cache-Control: public, max-age=3600' \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth cc-public-rewrite: HTTP %{http_code}\n"

# Proxy + auth + CC public from upstream (exercises strip-public-and-append-private)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/proxy-public-cc/large.html" -o /dev/null -w "  proxy auth cc-public large: HTTP %{http_code}\n"

# Proxy + auth + CC no-store from upstream (exercises preserve-nostore)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/proxy-nostore-cc/large.html" -o /dev/null -w "  proxy auth cc-nostore large: HTTP %{http_code}\n"

# Auth + no cookies, no authorization (not authenticated → no CC modification)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/auth-allow/index.html" -o /dev/null -w "  auth-allow unauthenticated: HTTP %{http_code}\n"

# Proxy + auth + CC private from upstream (exercises has_private no-op path)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/proxy-private-cc/index.html" -o /dev/null -w "  proxy auth cc-private: HTTP %{http_code}\n"

# Proxy + auth + CC private,no-store from upstream (exercises preserve no-store)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${AUTH_COOKIE_SESSION}" \
  "http://127.0.0.1:${PORT}/proxy-private-nostore-cc/index.html" -o /dev/null -w "  proxy auth cc-private-nostore: HTTP %{http_code}\n"

# Auth + PHPSESSID cookie (exact match for default pattern)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Cookie: PHPSESSID=abc123' \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth PHPSESSID cookie: HTTP %{http_code}\n"

# Auth + empty cookie header (edge case: no cookie names)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'Cookie: ' \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth empty cookie: HTTP %{http_code}\n"

# Auth + multiple Cookie headers (exercises cookie header chain iteration)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  -H 'Cookie: tracking=abc' -H 'Cookie: session_xyz=val' \
  "http://127.0.0.1:${PORT}/auth/index.html" -o /dev/null -w "  auth multi-cookie-headers: HTTP %{http_code}\n"

# ── Extended conditional request scenarios (coverage for ngx_http_markdown_conditional.c) ──

# Conditional: If-None-Match + If-Modified-Since together (both present)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  -H 'If-None-Match: "combined-etag"' \
  -H "${HDR_IF_MODIFIED_SINCE_FUTURE}" \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM+IMS combined: HTTP %{http_code}\n"

# Conditional: If-None-Match with empty value (should be treated as absent)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: ' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM empty value: HTTP %{http_code}\n"

# Conditional: If-None-Match to ims-only (should skip INM, return 200)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "any-etag"' \
  "http://127.0.0.1:${PORT}/ims-only/index.html" -o /dev/null -w "  ims-only with INM: HTTP %{http_code}\n"

# Conditional: If-Modified-Since to ims-only (should exercise IMS path)
curl -sS -H "${ACCEPT_MARKDOWN}" -H "${HDR_IF_MODIFIED_SINCE_FUTURE}" \
  "http://127.0.0.1:${PORT}/ims-only/index.html" -o /dev/null -w "  ims-only with IMS future: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-Modified-Since: Mon, 01 Jan 2020 00:00:00 GMT' \
  "http://127.0.0.1:${PORT}/ims-only/index.html" -o /dev/null -w "  ims-only with IMS past: HTTP %{http_code}\n"

# Conditional: disabled mode + If-None-Match (should skip entirely)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "disabled-etag"' \
  "http://127.0.0.1:${PORT}/no-conditional/index.html" -o /dev/null -w "  disabled-conditional INM: HTTP %{http_code}\n"

# Conditional: If-None-Match with multiple comma-separated ETags
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: "etag1", "etag2", "etag3"' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM triple etag: HTTP %{http_code}\n"

# Conditional: If-None-Match with W/ weak prefix + quoted
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: W/"weak-etag-value"' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  INM weak quoted: HTTP %{http_code}\n"

# Conditional: If-None-Match wildcard to no-etag (etag off, should bypass)
curl -sS -H "${ACCEPT_MARKDOWN}" -H 'If-None-Match: *' \
  "http://127.0.0.1:${PORT}/no-etag/index.html" -o /dev/null -w "  no-etag wildcard INM: HTTP %{http_code}\n"

# ── Extended Accept negotiation scenarios (coverage for ngx_http_markdown_accept.c) ──

# Accept: text/markdown;q=1.0, text/html;q=0.5 (markdown preferred)
curl -sS -H 'Accept: text/markdown;q=1.0, text/html;q=0.5' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  accept md preferred: HTTP %{http_code}\n"

# Accept: text/* (subtype wildcard with on_wildcard on)
curl -sS -H 'Accept: text/*' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  accept text/* wildcard: HTTP %{http_code}\n"

# Accept: */* with wildcard off (no-wildcard location)
curl -sS -H 'Accept: */*' \
  "http://127.0.0.1:${PORT}/no-wildcard/index.html" -o /dev/null -w "  accept */* no-wildcard: HTTP %{http_code}\n"

# Accept: text/html;q=0.5, */*;q=0.1 (wildcard with low q)
curl -sS -H 'Accept: text/html;q=0.5, */*;q=0.1' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  accept html+wildcard-lowq: HTTP %{http_code}\n"

# Accept: text/markdown;q=0.001 (very low q but > 0)
curl -sS -H 'Accept: text/markdown;q=0.001' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  accept md very-low-q: HTTP %{http_code}\n"

# Accept: application/json (non-HTML, non-markdown → skip)
curl -sS -H 'Accept: application/json' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  accept application/json: HTTP %{http_code}\n"

# Accept: empty string (should be treated as no Accept)
curl -sS -H 'Accept: ' \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  accept empty: HTTP %{http_code}\n"

# ── Extended Prometheus scenarios (coverage for ngx_http_markdown_prometheus_impl.h) ──

# Prometheus: Accept text/plain; version=0.0.4 (explicit openmetrics)
curl -sS -H 'Accept: text/plain; version=0.0.4' \
  "http://127.0.0.1:${PORT}/metrics-prometheus" -o /dev/null -w "  prometheus openmetrics: HTTP %{http_code}\n"

# Prometheus: Accept openmetrics-text (v1.0.0)
curl -sS -H 'Accept: application/openmetrics-text; version=1.0.0' \
  "http://127.0.0.1:${PORT}/metrics-prometheus" -o /dev/null -w "  prometheus openmetrics-v1: HTTP %{http_code}\n"

# Prometheus: no Accept header (default text/plain for prometheus format)
curl -sS "http://127.0.0.1:${PORT}/metrics-prometheus" -o /dev/null -w "  prometheus no-accept: HTTP %{http_code}\n"

# Prometheus: after streaming conversion (populates streaming metrics)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming/index.html" -o /dev/null -w "  pre-streaming convert: HTTP %{http_code}\n"
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/streaming/large.html" -o /dev/null -w "  pre-streaming large: HTTP %{http_code}\n"
curl -sS "http://127.0.0.1:${PORT}/metrics-prometheus" -o /dev/null -w "  prometheus after streaming: HTTP %{http_code}\n"

# Prometheus: Accept application/json on prometheus endpoint (json override)
curl -sS -H 'Accept: application/json' \
  "http://127.0.0.1:${PORT}/metrics-prometheus" -o /dev/null -w "  prometheus json override: HTTP %{http_code}\n"

# ── Extended error/reason code scenarios (coverage for ngx_http_markdown_error.c, reason.c) ──

# Conversion that succeeds (exercises ELIGIBLE_CONVERTED reason code)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/index.html" -o /dev/null -w "  success conversion: HTTP %{http_code}\n"

# Conversion with GFM flavor (exercises CT_ROUTE_DEFAULT reason code)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/gfm/large.html" -o /dev/null -w "  gfm large conversion: HTTP %{http_code}\n"

# Conversion with CommonMark flavor
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/commonmark/large.html" -o /dev/null -w "  commonmark large: HTTP %{http_code}\n"

# Size-limit rejection (exercises SKIP_SIZE reason code)
curl -sS -H "${ACCEPT_MARKDOWN}" \
  "http://127.0.0.1:${PORT}/small-limit/large.html" -o /dev/null -w "  size-limit reject: HTTP %{http_code}\n"

# IPv6 loopback probe (coverage-only):
# - Purpose: exercise sockaddr_in6 branch in metrics_impl.h.
# - Scope: ::1 loopback only; no remote host or routable interface.
# - Data sensitivity: test fixture traffic only, no credentials/tokens/secrets.
# - Transport: clear-text HTTP is intentional for local e2e coverage and is
#   not reused by production configs or deployment scripts.
if [[ -n "${IPV6_LISTEN}" ]]; then
    curl -sS -6 "http://[::1]:${BACKEND_PORT}/" -o /dev/null \
      -w "  IPv6 backend: HTTP %{http_code}\n" >&2 || true  # SONAR_NOTE — localhost-only coverage test
fi

echo "==> Stopping NGINX (flush gcov data)"
"${RUNTIME}/sbin/nginx" -p "${RUNTIME}" -c conf/nginx.conf -s stop
sleep 2

# Capture the NGINX binary path before e2e sub-scripts so each can
# reuse the same streaming-enabled binary via NGINX_BIN env var,
# avoiding redundant rebuilds inside each sub-script.
REUSE_NGINX_BIN="${RUNTIME}/sbin/nginx"

run_wrapper_with_nginx_bin_fallback() {
  local label="$1"
  shift
  if env NGINX_BIN="${REUSE_NGINX_BIN}" bash "$@"; then
    return 0
  fi
  echo "  WARNING: ${label} failed with reused nginx binary; retrying with self-build" >&2
  bash "$@"
  return $?
}

echo "==> Running extended streaming failure/cache e2e coverage"
if ! env NGINX_BIN="${REUSE_NGINX_BIN}" \
  bash "${WORKSPACE_ROOT}/tools/e2e/verify_streaming_failure_cache_e2e.sh" \
  --nginx-bin "${RUNTIME}/sbin/nginx" \
  --port 18296 \
  --upstream-port 19296 \
  --markdown-max-size 1m; then
    echo "  WARNING: streaming failure/cache e2e coverage run failed; continuing" >&2
fi

echo "==> Running streaming e2e coverage"
if ! env NGINX_BIN="${REUSE_NGINX_BIN}" \
  bash "${WORKSPACE_ROOT}/tools/e2e/verify_streaming_e2e.sh" \
  --nginx-bin "${RUNTIME}/sbin/nginx"; then
    echo "  WARNING: streaming e2e coverage run failed; continuing" >&2
fi

echo "==> Running chunked streaming e2e coverage (smoke)"
# Keep 10m so streaming gzip/deflate fixtures can validate decompression
# behavior instead of tripping full-buffer size fail-open at 1m.
if ! env NGINX_BIN="${REUSE_NGINX_BIN}" \
    bash "${WORKSPACE_ROOT}/tools/e2e/verify_chunked_streaming_native_e2e.sh" \
    --profile smoke \
    --port 18294 \
    --upstream-port 19294 \
    --markdown-max-size 10m; then
    echo "  WARNING: chunked streaming e2e coverage run failed; continuing" >&2
fi

echo "==> Running large markdown response e2e coverage"
if ! env NGINX_BIN="${REUSE_NGINX_BIN}" \
    bash "${WORKSPACE_ROOT}/tools/e2e/verify_large_markdown_response_e2e.sh" \
    --port 18291; then
    echo "  WARNING: large markdown e2e coverage run failed; continuing" >&2
fi

echo "==> Running proxy TLS backend e2e coverage"
if ! env NGINX_BIN="${REUSE_NGINX_BIN}" \
    bash "${WORKSPACE_ROOT}/tools/e2e/verify_proxy_tls_backend_e2e.sh" \
    --port 18289 \
    --backend-port 19289; then
    echo "  WARNING: proxy TLS backend e2e coverage run failed; continuing" >&2
fi

echo "==> Running auth-cache e2e coverage"
if ! run_wrapper_with_nginx_bin_fallback "auth-cache e2e coverage" \
    "${WORKSPACE_ROOT}/tools/e2e/verify_auth_cache_e2e.sh" \
    --port 18285 \
    --upstream-port 19285; then
    echo "  WARNING: auth-cache e2e coverage run failed; continuing" >&2
fi

echo "==> Running conditional-requests e2e coverage"
if ! run_wrapper_with_nginx_bin_fallback "conditional-requests e2e coverage" \
    "${WORKSPACE_ROOT}/tools/e2e/verify_conditional_requests_e2e.sh" \
    --port 18286 \
    --upstream-port 19286; then
    echo "  WARNING: conditional-requests e2e coverage run failed; continuing" >&2
fi

echo "==> Running metrics-endpoint e2e coverage"
if ! run_wrapper_with_nginx_bin_fallback "metrics-endpoint e2e coverage" \
    "${WORKSPACE_ROOT}/tools/e2e/verify_metrics_endpoint_e2e.sh" \
    --port 18287 \
    --upstream-port 19287; then
    echo "  WARNING: metrics-endpoint e2e coverage run failed; continuing" >&2
fi

echo "==> Running status-codes e2e coverage"
if ! run_wrapper_with_nginx_bin_fallback "status-codes e2e coverage" \
    "${WORKSPACE_ROOT}/tools/e2e/verify_status_codes_e2e.sh" \
    --port 18288 \
    --upstream-port 19288; then
    echo "  WARNING: status-codes e2e coverage run failed; continuing" >&2
fi

echo "==> Running error-handling e2e coverage" >&2
if ! run_wrapper_with_nginx_bin_fallback "error-handling e2e coverage" \
    "${WORKSPACE_ROOT}/tools/e2e/verify_error_handling_e2e.sh" \
    --port 18283; then
    echo "  WARNING: error-handling e2e coverage run failed; continuing" >&2
fi

echo "==> Running huge-body native e2e coverage (skip 1GB GET)"
# RUN_1G_GET=0 and --skip-1g-get avoid the slow 1GB GET test in CI;
# the remaining huge-body sub-tests still cover oversize and fail-open.
if ! env NGINX_BIN="${REUSE_NGINX_BIN}" RUN_1G_GET=0 \
    bash "${WORKSPACE_ROOT}/tools/e2e/verify_huge_body_native_e2e.sh" \
    --port 18292 \
    --skip-1g-get; then
    echo "  WARNING: huge-body native e2e coverage run failed; continuing" >&2
fi

echo "==> Running huge-body allowed native e2e coverage (skip 1GB GET)"
# --markdown-max-size 1536m allows bodies up to 1.5 GiB, exercising the
# allowed-huge-body path where conversion proceeds instead of fail-open.
if ! env NGINX_BIN="${REUSE_NGINX_BIN}" RUN_1G_GET=0 \
    bash "${WORKSPACE_ROOT}/tools/e2e/verify_huge_body_allowed_native_e2e.sh" \
    --port 18293 \
    --skip-1g-get \
    --markdown-max-size 1536m; then
    echo "  WARNING: huge-body allowed native e2e coverage run failed; continuing" >&2
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
  --rc geninfo_unexecuted_blocks=1 \
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
# NOTE: These thresholds are checked against E2E coverage only.
# The combined (e2e + unit) report is checked by the Makefile
# coverage-c target after merging both data sources.  Thresholds
# here are informational for e2e-only coverage; the authoritative
# advisory check runs against the merged report.
echo "==> E2E-only coverage summary (unit tests merged later by Makefile)"
# shellcheck disable=SC2086
lcov --summary "${OUTPUT_LCOV}" --rc branch_coverage=1 \
  ${LCOV_IGNORE} 2>&1 | grep -E 'lines|functions|branches' || true
