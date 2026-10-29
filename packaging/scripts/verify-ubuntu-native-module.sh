#!/usr/bin/env bash
# verify-ubuntu-native-module.sh -- Load the module into the Ubuntu NGINX
# package and verify request handling.
#
# The script installs the module into the distro module directory, adds the
# modules-enabled snippet, serves a fixture page, and records every check in a
# JSON ledger. It restores the host configuration on exit, so a developer
# machine keeps its original NGINX state.
#
# Required test classes:
#   load                  nginx -t accepts the module
#   request_markdown      Accept: text/markdown returns Markdown
#   request_passthrough   Accept: text/html keeps HTML
#   lifecycle             HEAD and reload keep serving Markdown
#   negative_abi_rejected an incompatible module is refused
#
# Usage:
#   verify-ubuntu-native-module.sh --module PATH --out FILE [options]
#
# Options:
#   --module PATH   Module built by build-ubuntu-native-module.sh
#   --out FILE      JSON ledger path
#   --suite SUITE   Ubuntu VERSION_ID of the running system (24.04)
#   --port PORT     Test listener port (default 18080)
#   --incompatible PATH
#                   Module with a different binary compatibility signature;
#                   the run fails when this module loads
#   --provenance JSON
#                   Build information written by the build script; its fields
#                   are copied into the ledger
#   --help          Show this help
#
# Exit codes:
#   0  Every check passed
#   1  A check failed, or a required input is missing

set -euo pipefail

MODULE_PATH=""
LEDGER_PATH=""
SUITE=""
PORT="18080"
INCOMPATIBLE_PATH=""
PROVENANCE_PATH=""

usage() {
    sed -n '2,34p' "$0" | sed 's/^#[[:space:]]\{0,1\}//' >&2
    return 0
}

die() {
    printf 'ERROR: [verify-ubuntu-native-module] %s\n' "$1" >&2
    exit 1
}

info() {
    printf '[verify-ubuntu-native-module] %s\n' "$1" >&2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --module)
            [[ $# -ge 2 ]] || die "--module requires a value"
            MODULE_PATH="$2"
            shift 2
            ;;
        --out)
            [[ $# -ge 2 ]] || die "--out requires a value"
            LEDGER_PATH="$2"
            shift 2
            ;;
        --suite)
            [[ $# -ge 2 ]] || die "--suite requires a value"
            SUITE="$2"
            shift 2
            ;;
        --port)
            [[ $# -ge 2 ]] || die "--port requires a value"
            PORT="$2"
            shift 2
            ;;
        --incompatible)
            [[ $# -ge 2 ]] || die "--incompatible requires a value"
            INCOMPATIBLE_PATH="$2"
            shift 2
            ;;
        --provenance)
            [[ $# -ge 2 ]] || die "--provenance requires a value"
            PROVENANCE_PATH="$2"
            shift 2
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            usage
            die "unknown argument: $1"
            ;;
    esac
done

[[ -n "${MODULE_PATH}" ]] || die "--module is required"
[[ -f "${MODULE_PATH}" ]] || die "module not found: ${MODULE_PATH}"
[[ -n "${LEDGER_PATH}" ]] || die "--out is required"
if [[ -n "${INCOMPATIBLE_PATH}" && ! -f "${INCOMPATIBLE_PATH}" ]]; then
    die "incompatible module not found: ${INCOMPATIBLE_PATH}"
fi
if [[ -n "${PROVENANCE_PATH}" && ! -f "${PROVENANCE_PATH}" ]]; then
    die "provenance file not found: ${PROVENANCE_PATH}"
fi
[[ -r /etc/os-release ]] || die "/etc/os-release is missing"
# Read the field instead of sourcing the file: a sourced file is opaque to
# the release-workflow scanner, which then fails closed.
DISTRO_VERSION_ID="$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')"
if [[ -n "${SUITE}" && "${DISTRO_VERSION_ID}" != "${SUITE}" ]]; then
    die "host is Ubuntu ${DISTRO_VERSION_ID:-unknown} but --suite is ${SUITE}"
fi

case "$(uname -m)" in
    x86_64)
        ARCH="amd64"
        ;;
    aarch64)
        ARCH="arm64"
        ;;
    *)
        die "unsupported host architecture: $(uname -m)"
        ;;
esac

MODULE_DIR="/usr/lib/nginx/modules"
MODULES_ENABLED_DIR="/etc/nginx/modules-enabled"
ENABLED_LINK="${MODULES_ENABLED_DIR}/50-mod-markdown-verify.conf"
SITE_AVAILABLE="/etc/nginx/sites-available/markdown-verify"
SITE_ENABLED="/etc/nginx/sites-enabled/markdown-verify"
DOCUMENT_ROOT="/var/www/markdown-verify"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/verify-ubuntu-native.XXXXXX")"
LEDGER_PATH="$(cd "$(dirname "${LEDGER_PATH}")" && pwd)/$(basename "${LEDGER_PATH}")"

PASS=0
FAIL=0
record() {
    local name="$1"
    local status="$2"
    local detail="$3"
    if [[ "${status}" == "PASS" ]]; then
        PASS=$((PASS + 1))
    else
        FAIL=$((FAIL + 1))
    fi
    printf '%s\t%s\t%s\n' "${name}" "${status}" "${detail}" >>"${TEST_DIR}/ledger.tsv"
}

MODULE_INSTALLED="no"
SITE_CREATED="no"
MODULES_ENABLED_DIR_CREATED="no"
DEFAULT_SITE_MOVED="no"

cleanup() {
    nginx -s stop >/dev/null 2>&1 || true
    if [[ "${SITE_CREATED}" == "yes" ]]; then
        rm -f "${SITE_AVAILABLE}" "${SITE_ENABLED}"
    fi
    if [[ "${DEFAULT_SITE_MOVED}" == "yes" ]]; then
        ln -sfn /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
    fi
    rm -f "${ENABLED_LINK}"
    if [[ "${MODULES_ENABLED_DIR_CREATED}" == "yes" ]]; then
        rmdir "${MODULES_ENABLED_DIR}" 2>/dev/null || true
    fi
    if [[ "${MODULE_INSTALLED}" == "yes" ]]; then
        rm -f "${MODULE_DIR}/ngx_http_markdown_filter_module.so"
    fi
    if [[ -d "${DOCUMENT_ROOT}" ]]; then
        rm -rf "${DOCUMENT_ROOT}"
    fi
    rm -rf "${TEST_DIR}"
}
trap cleanup EXIT HUP INT TERM

# Refuse to run over a host that already carries any of the paths this script
# owns.  The cleanup trap removes what it created, and an ownership mistake
# would delete a real module or site configuration.
refuse_existing() {
    local path="$1"
    if [[ -e "${path}" || -L "${path}" ]]; then
        die "${path} already exists; run this script on a clean host or remove that path first"
    fi
}
refuse_existing "${MODULE_DIR}/ngx_http_markdown_filter_module.so"
refuse_existing "${ENABLED_LINK}"
refuse_existing "${SITE_AVAILABLE}"
refuse_existing "${SITE_ENABLED}"
refuse_existing "${DOCUMENT_ROOT}"
# The distro default site is a known package path, not a conflict: the script
# moves it aside and restores it in cleanup, tracked by DEFAULT_SITE_MOVED.

mkdir -p "${MODULE_DIR}" "${DOCUMENT_ROOT}"
MODULE_INSTALLED="yes"
install -m 0644 "${MODULE_PATH}" \
    "${MODULE_DIR}/ngx_http_markdown_filter_module.so"
if [[ ! -d "${MODULES_ENABLED_DIR}" ]]; then
    mkdir -p "${MODULES_ENABLED_DIR}"
    MODULES_ENABLED_DIR_CREATED="yes"
fi
printf 'load_module modules/ngx_http_markdown_filter_module.so;\n' >"${ENABLED_LINK}"

cat >"${DOCUMENT_ROOT}/index.html" <<'HTML'
<!doctype html>
<html><head><title>Ubuntu native smoke</title></head>
<body><h1>Ubuntu native smoke</h1><p>Module check.</p></body></html>
HTML

cat >"${SITE_AVAILABLE}" <<CONF
server {
    listen 127.0.0.1:${PORT} default_server;
    server_name _;
    root ${DOCUMENT_ROOT};
    index index.html;
    markdown_filter on;
    markdown_error_policy pass;
    location / { }
}
CONF
SITE_CREATED="yes"
if [[ -L /etc/nginx/sites-enabled/default || -f /etc/nginx/sites-enabled/default ]]; then
    rm -f /etc/nginx/sites-enabled/default
    DEFAULT_SITE_MOVED="yes"
fi
ln -sfn "${SITE_AVAILABLE}" "${SITE_ENABLED}"

if nginx -t >"${TEST_DIR}/nginx-t.log" 2>&1; then
    record load PASS "nginx -t"
else
    record load FAIL "nginx -t"
    cat "${TEST_DIR}/nginx-t.log" >&2
fi

nginx >"${TEST_DIR}/nginx-start.log" 2>&1 || cat "${TEST_DIR}/nginx-start.log" >&2
ready="no"
for _ in $(seq 1 40); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/"; then
        ready="yes"
        break
    fi
    sleep 0.25
done
if [[ "${ready}" != "yes" ]]; then
    record request_markdown FAIL "listener did not start"
    record request_passthrough FAIL "listener did not start"
    record lifecycle FAIL "listener did not start"
else
    code="$(curl -s -o "${TEST_DIR}/html.body" -w '%{http_code}' \
        -H 'Accept: text/html' "http://127.0.0.1:${PORT}/")"
    if [[ "${code}" == "200" ]] && grep -q '<h1>' "${TEST_DIR}/html.body"; then
        record request_passthrough PASS "HTTP ${code}"
    else
        record request_passthrough FAIL "HTTP ${code}"
    fi

    code="$(curl -s -D "${TEST_DIR}/markdown.headers" -o "${TEST_DIR}/markdown.body" \
        -w '%{http_code}' -H 'Accept: text/markdown' \
        "http://127.0.0.1:${PORT}/")"
    if [[ "${code}" == "200" ]] \
        && grep -qi '^content-type: text/markdown' "${TEST_DIR}/markdown.headers" \
        && grep -q '^# Ubuntu native smoke' "${TEST_DIR}/markdown.body" \
        && grep -qi '^vary:.*accept' "${TEST_DIR}/markdown.headers"; then
        record request_markdown PASS "HTTP ${code}"
    else
        record request_markdown FAIL "HTTP ${code}"
    fi

    head_code="$(curl -s -I -o "${TEST_DIR}/head.headers" -w '%{http_code}' \
        -H 'Accept: text/markdown' "http://127.0.0.1:${PORT}/")"
    reload_status="FAIL"
    if nginx -s reload >"${TEST_DIR}/nginx-reload.log" 2>&1; then
        sleep 0.7
        reload_code="$(curl -s -o "${TEST_DIR}/reload.body" -w '%{http_code}' \
            -H 'Accept: text/markdown' "http://127.0.0.1:${PORT}/")"
        if [[ "${reload_code}" == "200" ]] && grep -q '^# Ubuntu native smoke' "${TEST_DIR}/reload.body"; then
            reload_status="PASS"
        fi
    fi
    if [[ "${head_code}" == "200" ]] \
        && grep -qi '^content-type: text/markdown' "${TEST_DIR}/head.headers" \
        && [[ "${reload_status}" == "PASS" ]]; then
        record lifecycle PASS "HEAD ${head_code}, reload PASS"
    else
        record lifecycle FAIL "HEAD ${head_code}, reload ${reload_status}"
    fi
fi

if [[ -n "${INCOMPATIBLE_PATH}" ]]; then
    install -m 0644 "${INCOMPATIBLE_PATH}" \
        "${MODULE_DIR}/ngx_http_markdown_filter_module.so"
    if nginx -t >"${TEST_DIR}/nginx-t-incompatible.log" 2>&1; then
        record negative_abi_rejected FAIL "incompatible module loaded"
    elif grep -qi "not binary compatible" "${TEST_DIR}/nginx-t-incompatible.log"; then
        # Only the compatibility rejection proves the check is real.  Any
        # other failure (a broken configuration, a missing file) is not
        # evidence that NGINX compared module signatures.
        record negative_abi_rejected PASS "rejected: not binary compatible"
    else
        record negative_abi_rejected FAIL \
            "nginx -t failed for another reason: $(tail -1 "${TEST_DIR}/nginx-t-incompatible.log")"
    fi
    install -m 0644 "${MODULE_PATH}" \
        "${MODULE_DIR}/ngx_http_markdown_filter_module.so"
else
    record negative_abi_rejected FAIL "no incompatible module supplied"
fi

python3 - "${LEDGER_PATH}" "${TEST_DIR}/ledger.tsv" "${PROVENANCE_PATH}" \
    "${SUITE:-${DISTRO_VERSION_ID:-unknown}}" "${ARCH}" <<'PY'
import datetime
import json
import os
import sys

ledger_path, tests_path, provenance_path, suite, arch = sys.argv[1:]
tests = []
with open(tests_path, encoding="utf-8") as handle:
    for line in handle:
        name, status, detail = line.rstrip("\n").split("\t")
        tests.append({"name": name, "status": status, "detail": detail})
counts = {status: sum(1 for test in tests if test["status"] == status)
          for status in ("PASS", "FAIL", "SKIP")}

provenance = {}
if provenance_path:
    with open(provenance_path, encoding="utf-8") as handle:
        provenance = json.load(handle)

payload = {
    "schema_version": 1,
    "source_kind": "ubuntu-default-repo",
    "distro": f"ubuntu-{suite}",
    "distro_version": suite,
    "arch": arch,
    "libc": "glibc",
    "features": provenance.get("features", ["streaming", "prune_noise_regions"]),
    "tests": tests,
    "pass": counts["PASS"],
    "fail": counts["FAIL"],
    "skip": counts["SKIP"],
    "date": datetime.date.today().isoformat(),
}
for field in (
    "nginx_version",
    "nginx_package_revision",
    "abi_provide",
    "source_reference",
    "source_digest",
    "build_args_digest",
    "module_sha256",
    "rust_version",
):
    if field in provenance:
        payload[field] = provenance[field]

with open(ledger_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
print(json.dumps(payload, indent=2, sort_keys=True))
PY

if [[ "${FAIL}" -ne 0 ]]; then
    die "${FAIL} check(s) failed, ${PASS} passed"
fi
info "all ${PASS} checks passed; ledger ${LEDGER_PATH}"
