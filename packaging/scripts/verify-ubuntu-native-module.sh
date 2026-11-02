#!/usr/bin/env bash
# verify-ubuntu-native-module.sh -- Load the module into the Ubuntu NGINX
# package and verify request handling.
#
# The script uses an isolated prefix, configuration, PID, and fixture root.
# It never edits host configuration or signals the host NGINX service.
# Every check is recorded in a JSON ledger.
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
DISTRO_ID="$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')"
[[ "${DISTRO_ID}" == "ubuntu" ]] || die "this verifier requires Ubuntu"
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

NGINX_BIN="/usr/sbin/nginx"
[[ -x "${NGINX_BIN}" ]] || die "Ubuntu NGINX binary is missing"
# Bind validation to the installed distro package, never a PATH replacement.
python3 - "${NGINX_BIN}" "${PROVENANCE_PATH}" "${MODULE_PATH}" <<'PY'
import hashlib
import json
import subprocess
import sys

binary, provenance_path, module_path = sys.argv[1:]
ownership = subprocess.check_output(["dpkg-query", "-S", binary], text=True)
owners = [line.rsplit(": ", 1)[0] for line in ownership.splitlines()
          if line.endswith(": " + binary)]
if len(owners) != 1:
    raise SystemExit("NGINX must have one installed Ubuntu package owner")
revision = subprocess.check_output(
    ["dpkg-query", "-W", "-f=${Version}", owners[0]], text=True).strip()
if "ubuntu" not in revision:
    raise SystemExit("NGINX package revision is not an Ubuntu revision")
version = revision.split(":")[-1].split("-", 1)[0]
reported = subprocess.check_output([binary, "-v"], stderr=subprocess.STDOUT,
                                   text=True).strip()
if reported != f"nginx version: nginx/{version} (Ubuntu)":
    raise SystemExit("NGINX binary version does not match its Ubuntu package")
if provenance_path:
    with open(provenance_path, encoding="utf-8") as handle:
        provenance = json.load(handle)
    if (provenance.get("nginx_package_revision") != revision
            or provenance.get("nginx_version") != version):
        raise SystemExit("installed NGINX revision differs from build provenance")
    with open(module_path, "rb") as handle:
        module_digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if provenance.get("module_sha256") != module_digest:
        raise SystemExit("module digest differs from build provenance")
PY

[[ "${PORT}" =~ ^[0-9]+$ ]] || die "--port must be an integer"
[[ ${#PORT} -le 5 ]] && [[ "${PORT}" -ge 1024 && "${PORT}" -le 65535 ]] \
    || die "--port must be between 1024 and 65535"
MODULE_PATH="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${MODULE_PATH}")"
if [[ -n "${INCOMPATIBLE_PATH}" ]]; then
    INCOMPATIBLE_PATH="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${INCOMPATIBLE_PATH}")"
fi
python3 - "${PORT}" <<'PY'
import socket
import sys
with socket.socket() as listener:
    listener.bind(("127.0.0.1", int(sys.argv[1])))
PY
LEDGER_PATH="$(cd "$(dirname "${LEDGER_PATH}")" && pwd)/$(basename "${LEDGER_PATH}")"
TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/verify-ubuntu-native.XXXXXX")"
DOCUMENT_ROOT="${TEST_DIR}/html"
CONFIG_PATH="${TEST_DIR}/nginx.conf"
VERIFY_TOKEN="$(basename "${TEST_DIR}")"
NGINX_PID=""

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
    return 0
}

# Bash retains job ownership independently of PID reuse after a child exits.
owned_nginx_running() {
    local running_jobs
    running_jobs="$(jobs -pr)"
    [[ -n "${NGINX_PID}" ]] && grep -Fxq "${NGINX_PID}" <<<"${running_jobs}"
    return $?
}

cleanup() {
    if [[ -n "${NGINX_PID}" ]]; then
        if owned_nginx_running; then
            kill -TERM "${NGINX_PID}" 2>/dev/null || true
        fi
        wait "${NGINX_PID}" 2>/dev/null || true
    fi
    rm -rf "${TEST_DIR}"
    return 0
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Reject configuration metacharacters before embedding absolute paths.
for config_value in "${MODULE_PATH}" "${INCOMPATIBLE_PATH}" "${TEST_DIR}"; do
    case "${config_value}" in
        *'"'*|*\\*|*'$'*|*$'\n'*|*$'\r'*|*$'\t'*)
            die "path contains unsupported NGINX configuration characters"
            ;;
        *) ;;
    esac
done
mkdir -p "${DOCUMENT_ROOT}"
# Root-run distro workers must traverse the isolated fixture directory.
chmod 755 "${TEST_DIR}" "${DOCUMENT_ROOT}"

cat >"${DOCUMENT_ROOT}/index.html" <<'HTML'
<!doctype html>
<html><head><title>Ubuntu native smoke</title></head>
<body><h1>Ubuntu native smoke</h1><p>Module check.</p></body></html>
HTML

cat >"${CONFIG_PATH}" <<CONF
load_module "${MODULE_PATH}";
daemon off;
pid "${TEST_DIR}/nginx.pid";
error_log "${TEST_DIR}/error.log";
events { worker_connections 64; }
http {
    access_log "${TEST_DIR}/access.log";
    client_body_temp_path "${TEST_DIR}/client_temp";
    proxy_temp_path "${TEST_DIR}/proxy_temp";
    fastcgi_temp_path "${TEST_DIR}/fastcgi_temp";
    uwsgi_temp_path "${TEST_DIR}/uwsgi_temp";
    scgi_temp_path "${TEST_DIR}/scgi_temp";
server {
    listen 127.0.0.1:${PORT} default_server;
    server_name _;
    root "${DOCUMENT_ROOT}";
    index index.html;
    markdown_filter on;
    markdown_error_policy pass;
    add_header X-Markdown-Verify "${VERIFY_TOKEN}" always;
    location / { }
}
}
CONF

if "${NGINX_BIN}" -e "${TEST_DIR}/error.log" -p "${TEST_DIR}/" -c "${CONFIG_PATH}" -t >"${TEST_DIR}/nginx-t.log" 2>&1; then
    record load PASS "nginx -t"
else
    record load FAIL "nginx -t"
    cat "${TEST_DIR}/nginx-t.log" >&2
fi

ready="no"
if [[ "${FAIL}" -eq 0 ]]; then
    "${NGINX_BIN}" -e "${TEST_DIR}/error.log" -p "${TEST_DIR}/" -c "${CONFIG_PATH}" >"${TEST_DIR}/nginx-start.log" 2>&1 &
    NGINX_PID=$!
    for _ in $(seq 1 40); do
        if ! owned_nginx_running; then
            cat "${TEST_DIR}/nginx-start.log" >&2
            break
        fi
        if curl --max-time 5 -fsS -D "${TEST_DIR}/ready.headers" -o /dev/null \
            "http://127.0.0.1:${PORT}/" \
            && grep -Fiq "X-Markdown-Verify: ${VERIFY_TOKEN}" "${TEST_DIR}/ready.headers" \
            && owned_nginx_running; then
            ready="yes"
            break
        fi
        sleep 0.25
    done
fi
if [[ "${ready}" != "yes" ]]; then
    record request_markdown FAIL "listener did not start"
    record request_passthrough FAIL "listener did not start"
    record lifecycle FAIL "listener did not start"
else
    code="$(curl --max-time 5 -s -o "${TEST_DIR}/html.body" -w '%{http_code}' \
        -H 'Accept: text/html' "http://127.0.0.1:${PORT}/" || true)"
    if [[ "${code}" == "200" ]] && grep -q '<h1>' "${TEST_DIR}/html.body"; then
        record request_passthrough PASS "HTTP ${code}"
    else
        record request_passthrough FAIL "HTTP ${code}"
    fi

    code="$(curl --max-time 5 -s -D "${TEST_DIR}/markdown.headers" -o "${TEST_DIR}/markdown.body" \
        -w '%{http_code}' -H 'Accept: text/markdown' \
        "http://127.0.0.1:${PORT}/" || true)"
    if [[ "${code}" == "200" ]] \
        && grep -qi '^content-type: text/markdown' "${TEST_DIR}/markdown.headers" \
        && grep -q '^# Ubuntu native smoke' "${TEST_DIR}/markdown.body" \
        && grep -qi '^vary:.*accept' "${TEST_DIR}/markdown.headers"; then
        record request_markdown PASS "HTTP ${code}"
    else
        record request_markdown FAIL "HTTP ${code}"
    fi

    head_code="$(curl --max-time 5 -s -I -o "${TEST_DIR}/head.headers" -w '%{http_code}' \
        -H 'Accept: text/markdown' "http://127.0.0.1:${PORT}/" || true)"
    reload_status="FAIL"
    if owned_nginx_running && kill -HUP "${NGINX_PID}"; then
        sleep 0.7
        reload_code="$(curl --max-time 5 -s -o "${TEST_DIR}/reload.body" -w '%{http_code}' \
            -H 'Accept: text/markdown' "http://127.0.0.1:${PORT}/" || true)"
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
    # A distinct config references the negative control directly. Never replace
    # the loaded module or any system module file.
    printf 'load_module "%s";\npid "%s/nginx.pid";\nerror_log "%s/error.log";\nevents {}\n' \
        "${INCOMPATIBLE_PATH}" "${TEST_DIR}" "${TEST_DIR}" \
        >"${TEST_DIR}/incompatible.conf"
    if "${NGINX_BIN}" -e "${TEST_DIR}/error.log" -p "${TEST_DIR}/" -c "${TEST_DIR}/incompatible.conf" -t \
        >"${TEST_DIR}/nginx-t-incompatible.log" 2>&1; then
        record negative_abi_rejected FAIL "incompatible module loaded"
    elif grep -Fq "module \"${INCOMPATIBLE_PATH}\" is not binary compatible" \
        "${TEST_DIR}/nginx-t-incompatible.log"; then
        # Only the compatibility rejection proves the check is real.  Any
        # other failure (a broken configuration, a missing file) is not
        # evidence that NGINX compared module signatures.
        record negative_abi_rejected PASS "rejected: not binary compatible"
    else
        record negative_abi_rejected FAIL \
            "nginx -t failed for another reason: $(tail -1 "${TEST_DIR}/nginx-t-incompatible.log")"
    fi
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
    "brotli_version",
    "brotli_linkage",
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
