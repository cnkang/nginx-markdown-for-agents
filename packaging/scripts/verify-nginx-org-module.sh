#!/usr/bin/env bash
# verify-nginx-org-module.sh -- Load a release module into the official
# nginx.org build for the same version and verify request handling.
#
# Run this script INSIDE a container or host that already carries the
# nginx.org binary for the requested version (for example the official
# `nginx:<version>` or `nginx:<version>-alpine` image).  The script copies the
# module into the nginx module directory, writes a self-contained
# configuration, and records every check in a JSON ledger.
#
# The configuration is written to a private prefix instead of the image
# default, so the script never edits a file it did not create.  An exit trap
# stops the NGINX it started and removes that prefix on every exit path, so a
# failed request or an interrupt cannot leave a listener behind.
#
# Required test classes:
#   load                  nginx -t accepts the module
#   request_markdown      Accept: text/markdown returns Markdown
#   request_passthrough   Accept: text/html keeps HTML
#   lifecycle             HEAD and reload keep serving Markdown
#
# Usage:
#   verify-nginx-org-module.sh --module PATH --nginx-version V --arch ARCH \
#       --out FILE [--libc glibc|musl] [--port PORT]
#
# Exit codes:
#   0  Every check passed
#   1  A check failed, or a required input is missing

set -euo pipefail

MODULE_PATH=""
NGINX_VERSION=""
ARCH=""
LIBC="glibc"
LEDGER_PATH=""
PORT="18080"

usage() {
    sed -n '2,26p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: [verify-nginx-org-module] %s\n' "$1" >&2
    exit 1
}

info() {
    printf '[verify-nginx-org-module] %s\n' "$1" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --module) [[ $# -ge 2 ]] || die "--module requires a value"; MODULE_PATH="$2"; shift 2 ;;
        --nginx-version) [[ $# -ge 2 ]] || die "--nginx-version requires a value"; NGINX_VERSION="$2"; shift 2 ;;
        --arch) [[ $# -ge 2 ]] || die "--arch requires a value"; ARCH="$2"; shift 2 ;;
        --libc) [[ $# -ge 2 ]] || die "--libc requires a value"; LIBC="$2"; shift 2 ;;
        --out) [[ $# -ge 2 ]] || die "--out requires a value"; LEDGER_PATH="$2"; shift 2 ;;
        --port) [[ $# -ge 2 ]] || die "--port requires a value"; PORT="$2"; shift 2 ;;
        --help) usage; exit 0 ;;
        *) usage; die "unknown argument: $1" ;;
    esac
done

[[ -n "${MODULE_PATH}" ]] || die "--module is required"
[[ -f "${MODULE_PATH}" ]] || die "module not found: ${MODULE_PATH}"
[[ -n "${NGINX_VERSION}" ]] || die "--nginx-version is required"
[[ -n "${ARCH}" ]] || die "--arch is required"
[[ -n "${LEDGER_PATH}" ]] || die "--out is required"
case "${ARCH}" in amd64|arm64) ;; *) die "--arch must be amd64 or arm64" ;; esac
case "${LIBC}" in glibc|musl) ;; *) die "--libc must be glibc or musl" ;; esac

# The host binary must be the version the row names.  A load test against a
# different build would prove nothing about the matrix row.
INSTALLED_VERSION="$(nginx -v 2>&1 | sed -n 's#^nginx version: nginx/##p')"
[[ "${INSTALLED_VERSION}" == "${NGINX_VERSION}" ]] || \
    die "host nginx is ${INSTALLED_VERSION:-unknown}, expected ${NGINX_VERSION}"
# --with-compat is what makes a module built against the release tarball
# loadable into the nginx.org binary.  Refuse a host that lacks it: the run
# would test a different compatibility contract.
nginx -V 2>&1 | tr ' ' '\n' | grep -qx -- '--with-compat' || \
    die "host nginx was not built with --with-compat"

PREFIX="$(mktemp -d "${TMPDIR:-/tmp}/verify-nginx-org.XXXXXX")"

# Under set -e a failed curl or an interrupt exits before the normal quit.
# The pid file exists only while the NGINX this script started is running.
cleanup() {
    if [[ -f "${PREFIX}/logs/nginx.pid" ]]; then
        nginx -c "${PREFIX}/nginx.conf" -p "${PREFIX}" -s quit >/dev/null 2>&1 || true
    fi
    rm -rf "${PREFIX}"
    return 0
}
trap cleanup EXIT
# A signal must end the run; exiting here runs the EXIT trap exactly once.
trap 'exit 1' HUP INT TERM

# mktemp creates 0700.  The official nginx images run worker processes as an
# unprivileged user, so a 0700 prefix makes every document unreadable and the
# whole request phase fails for a reason unrelated to the module.
chmod 755 "${PREFIX}"
mkdir -p "${PREFIX}/logs" "${PREFIX}/www" "${PREFIX}/modules"
chmod 755 "${PREFIX}/logs" "${PREFIX}/www" "${PREFIX}/modules"

MODULE_BASENAME="ngx_http_markdown_filter_module.so"
install -m 0644 "${MODULE_PATH}" "${PREFIX}/modules/${MODULE_BASENAME}"

cat >"${PREFIX}/www/index.html" <<'HTML'
<!doctype html>
<html><head><title>nginx.org smoke</title></head>
<body><h1>nginx.org smoke</h1><p>Module check.</p></body></html>
HTML

cat >"${PREFIX}/nginx.conf" <<CONF
worker_processes 1;
error_log ${PREFIX}/logs/error.log notice;
pid ${PREFIX}/logs/nginx.pid;
load_module ${PREFIX}/modules/${MODULE_BASENAME};

events { worker_connections 64; }

http {
    access_log off;
    client_body_temp_path ${PREFIX}/logs/client_body;
    proxy_temp_path ${PREFIX}/logs/proxy;
    fastcgi_temp_path ${PREFIX}/logs/fastcgi;
    uwsgi_temp_path ${PREFIX}/logs/uwsgi;
    scgi_temp_path ${PREFIX}/logs/scgi;

    server {
        listen 127.0.0.1:${PORT} default_server;
        server_name _;
        root ${PREFIX}/www;
        index index.html;
        markdown_filter on;
        markdown_error_policy pass;
        location / { }
    }
}
CONF

PASS=0
FAIL=0
record() {
    local name="$1" status="$2" detail="$3"
    if [[ "${status}" == "PASS" ]]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + 1)); fi
    printf '%s\t%s\t%s\n' "${name}" "${status}" "${detail}" >>"${PREFIX}/ledger.tsv"
}

NGINX=(nginx -c "${PREFIX}/nginx.conf" -p "${PREFIX}")

if "${NGINX[@]}" -t >"${PREFIX}/nginx-t.log" 2>&1; then
    record load PASS "nginx -t"
else
    record load FAIL "nginx -t"
    cat "${PREFIX}/nginx-t.log" >&2
fi

"${NGINX[@]}" >"${PREFIX}/nginx-start.log" 2>&1 || cat "${PREFIX}/nginx-start.log" >&2
ready="no"
for _ in $(seq 1 40); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/"; then ready="yes"; break; fi
    sleep 0.25
done

if [[ "${ready}" != "yes" ]]; then
    record request_passthrough FAIL "listener did not start"
    record request_markdown FAIL "listener did not start"
    record lifecycle FAIL "listener did not start"
else
    code="$(curl -s -o "${PREFIX}/html.body" -w '%{http_code}' \
        -H 'Accept: text/html' "http://127.0.0.1:${PORT}/")"
    if [[ "${code}" == "200" ]] && grep -q '<h1>' "${PREFIX}/html.body"; then
        record request_passthrough PASS "HTTP ${code}"
    else
        record request_passthrough FAIL "HTTP ${code}"
    fi

    code="$(curl -s -D "${PREFIX}/markdown.headers" -o "${PREFIX}/markdown.body" \
        -w '%{http_code}' -H 'Accept: text/markdown' "http://127.0.0.1:${PORT}/")"
    if [[ "${code}" == "200" ]] \
        && grep -qi '^content-type: text/markdown' "${PREFIX}/markdown.headers" \
        && grep -q '^# nginx.org smoke' "${PREFIX}/markdown.body" \
        && grep -qi '^vary:.*accept' "${PREFIX}/markdown.headers"; then
        record request_markdown PASS "HTTP ${code}"
    else
        record request_markdown FAIL "HTTP ${code}"
    fi

    head_code="$(curl -s -I -o "${PREFIX}/head.headers" -w '%{http_code}' \
        -H 'Accept: text/markdown' "http://127.0.0.1:${PORT}/")"
    reload_status="FAIL"
    if "${NGINX[@]}" -s reload; then
        sleep 0.7
        reload_code="$(curl -s -o "${PREFIX}/reload.body" -w '%{http_code}' \
            -H 'Accept: text/markdown' "http://127.0.0.1:${PORT}/")"
        if [[ "${reload_code}" == "200" ]] && grep -q '^# nginx.org smoke' "${PREFIX}/reload.body"; then
            reload_status="PASS"
        fi
    fi
    if [[ "${head_code}" == "200" ]] \
        && grep -qi '^content-type: text/markdown' "${PREFIX}/head.headers" \
        && [[ "${reload_status}" == "PASS" ]]; then
        record lifecycle PASS "HEAD ${head_code}, reload PASS"
    else
        record lifecycle FAIL "HEAD ${head_code}, reload ${reload_status}"
    fi
fi

"${NGINX[@]}" -s quit >/dev/null 2>&1 || true

# Linkage check.  The official nginx.org artifact must bundle the decoder:
# a DT_NEEDED entry for libbrotli* would let a host package reintroduce
# CVE-2026-111497.  This reads the dynamic section, not the file text.
if command -v readelf >/dev/null 2>&1; then
    if readelf -d "${MODULE_PATH}" | grep -Eq 'NEEDED.*libbrotli(dec|common)\.so'; then
        record static_brotli FAIL "module depends on a shared Brotli library"
    else
        record static_brotli PASS "no libbrotli NEEDED entry"
    fi
else
    record static_brotli SKIP "readelf is not available on this host"
fi

python3 - "${LEDGER_PATH}" "${PREFIX}/ledger.tsv" "${NGINX_VERSION}" \
    "${LIBC}" "${ARCH}" "${MODULE_PATH}" <<'PY'
import hashlib
import json
import pathlib
import sys

ledger_path, tests_path, version, libc, arch, module_path = sys.argv[1:]
tests = []
with open(tests_path, encoding="utf-8") as handle:
    for line in handle:
        name, status, detail = line.rstrip("\n").split("\t")
        tests.append({"name": name, "status": status, "detail": detail})
counts = {status: sum(1 for test in tests if test["status"] == status)
          for status in ("PASS", "FAIL", "SKIP")}
digest = hashlib.sha256(pathlib.Path(module_path).read_bytes()).hexdigest()
payload = {
    "schema_version": 1,
    "source_kind": "nginx.org",
    "nginx_version": version,
    "libc": libc,
    "arch": arch,
    "module_sha256": digest,
    "tests": tests,
    "pass": counts["PASS"],
    "fail": counts["FAIL"],
    "skip": counts["SKIP"],
}
pathlib.Path(ledger_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print(json.dumps(payload, indent=2, sort_keys=True))
PY

if [[ "${FAIL}" -ne 0 ]]; then
    die "${FAIL} check(s) failed, ${PASS} passed"
fi
info "all ${PASS} checks passed; ledger ${LEDGER_PATH}"
