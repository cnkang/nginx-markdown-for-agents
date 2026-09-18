#!/usr/bin/env bash
# Native qualification: upstream response trailers must not survive
# markdown conversion.
#
# The fixture runs the real NGINX binary and module.  A Python upstream
# supplies a chunked HTML response that carries `Trailer: Digest` and an
# actual trailer field.  The converted (text/markdown) response must not
# expose the trailer end-to-end: no stale Trailer/Digest family headers and
# no trailing header bytes on the wire.  A raw sanity pass proves the
# upstream fixture really emits the trailer, so the check cannot pass
# vacuously.

set -euo pipefail

PORT="${PORT:-18119}"
NGINX_BIN="${NGINX_BIN:-}"
KEEP_ARTIFACTS=0
BUILDROOT=""
RUNTIME=""
NGINX_EXECUTABLE=""
LOAD_MODULE_LINE=""
UPSTREAM_PID=""
PASS_COUNT=0
FAIL_COUNT=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NATIVE_BUILD_HELPER="${WORKSPACE_ROOT}/tools/lib/nginx_markdown_native_build.sh"

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-bin PATH] [--port PORT]

Run the upstream-trailer native qualification.  The NGINX binary must
include the markdown module.
EOF
    return 0
}

pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf 'PASS: %s\n' "$1" >&2
    return 0
}

fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf 'FAIL: %s\n' "$1" >&2
    return 0
}

cleanup_runtime() {
    local rc=$?

    if [[ -n "${NGINX_EXECUTABLE}" && -n "${RUNTIME}" ]]; then
        "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf \
            -s stop >/dev/null 2>&1 || true
    fi
    if [[ -n "${UPSTREAM_PID}" ]]; then
        kill "${UPSTREAM_PID}" >/dev/null 2>&1 || true
        wait "${UPSTREAM_PID}" >/dev/null 2>&1 || true
    fi

    if [[ "${rc}" -ne 0 && -n "${RUNTIME}" && -f "${RUNTIME}/logs/error.log" ]]; then
        sed -n '1,80p' "${RUNTIME}/logs/error.log" >&2 || true
    fi

    if [[ "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
        case "${BUILDROOT}" in
            "${TMPDIR:-/tmp}"/nginx-trailers-e2e.*)
                rm -rf "${BUILDROOT}"
                ;;
            *)
                :
                ;;
        esac
    fi

    return "${rc}"
}
trap cleanup_runtime EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-artifacts)
            KEEP_ARTIFACTS=1
            shift
            ;;
        --nginx-bin)
            [[ $# -ge 2 ]] || { echo "--nginx-bin requires a value" >&2; exit 2; }
            NGINX_BIN="$2"
            shift 2
            ;;
        --port)
            [[ $# -ge 2 ]] || { echo "--port requires a value" >&2; exit 2; }
            PORT="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            exit 2
            ;;
    esac
done

if [[ -z "${NGINX_BIN}" ]]; then
    echo "NGINX_BIN is required for native qualification" >&2
    exit 2
fi
if [[ ! -x "${NGINX_BIN}" ]]; then
    echo "NGINX_BIN is not executable: ${NGINX_BIN}" >&2
    exit 2
fi

# proxy_pass_trailers requires NGINX 1.27.2 or newer; older supported
# binaries cannot load this fixture configuration at all.
nginx_version_raw="$("${NGINX_BIN}" -v 2>&1 || true)"
nginx_version_num="$(printf '%s' "${nginx_version_raw}" | sed -n 's/.*nginx\/\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p')"
if [[ -z "${nginx_version_num}" ]]; then
    echo "ERROR: cannot determine the NGINX version from: ${nginx_version_raw}" >&2
    exit 2
fi
version_at_least() {
    local want="$1"
    if awk -v have="${nginx_version_num}" -v want="${want}" 'BEGIN {
            split(have, h, "."); split(want, w, ".");
            for (i = 1; i <= 3; i++) {
                if ((h[i] + 0) > (w[i] + 0)) exit 0;
                if ((h[i] + 0) < (w[i] + 0)) exit 1;
            }
            exit 0;
        }'; then
        return 0
    fi
    return 1
}
if ! version_at_least "1.27.2"; then
    echo "SKIP: upstream-trailer qualification requires NGINX >= 1.27.2; this binary is ${nginx_version_num}"
    exit 0
fi
if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65534 )); then
    echo "PORT must be an integer between 1024 and 65534 (UPSTREAM_PORT=PORT+1): ${PORT}" >&2
    exit 2
fi

BUILDROOT="$(mktemp -d "${TMPDIR:-/tmp}/nginx-trailers-e2e.XXXXXX")"
RUNTIME="${BUILDROOT}/runtime"
UPSTREAM_PORT=$((PORT + 1))
mkdir -p "${RUNTIME}/conf" "${RUNTIME}/html" "${RUNTIME}/logs"

LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
NGINX_EXECUTABLE="${NGINX_BIN}"

cat > "${BUILDROOT}/upstream.py" <<'PY'
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HTML = (
    b"<!doctype html><html><body>"
    b"<h1>Trailer probe heading</h1>"
    b"<p>trailer-probe-marker paragraph</p>"
    b"</body></html>"
)
TRAILER_VALUE = "sha-256=:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=:"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        request_path = urlsplit(self.path).path
        if request_path == "/html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(HTML)))
            self.end_headers()
            self.wfile.write(HTML)
            return
        if request_path == "/trailers":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Trailer", "Digest")
            self.end_headers()
            # One chunk with the HTML document, then the trailer section.
            self.wfile.write(b"%x\r\n" % len(HTML))
            self.wfile.write(HTML)
            self.wfile.write(b"\r\n0\r\nDigest: " + TRAILER_VALUE.encode("ascii") + b"\r\n\r\n")
            self.wfile.flush()
            return
        self.send_error(404)

    def log_message(self, _format, *_args):
        return


server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["UPSTREAM_PORT"])), Handler)
server.serve_forever()
PY

UPSTREAM_PORT="${UPSTREAM_PORT}" python3 "${BUILDROOT}/upstream.py" &
UPSTREAM_PID=$!

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 1;
error_log logs/error.log notice;
pid logs/nginx.pid;

events { worker_connections 128; }

http {
    default_type application/octet-stream;
    access_log off;
    upstream trailer_backend {
        server 127.0.0.1:${UPSTREAM_PORT};
    }

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        location = /html {
            proxy_pass http://trailer_backend/html;
        }

        location = /trailers-raw {
            proxy_pass http://trailer_backend/trailers;
            # Forward upstream trailers to the client on the raw path so the
            # sanity pass can prove the fixture really emits them.
            proxy_http_version 1.1;
            proxy_set_header Connection "te";
            proxy_set_header TE "trailers";
            proxy_pass_trailers on;
        }

        location = /trailers-md {
            proxy_pass http://trailer_backend/trailers;
            proxy_http_version 1.1;
            proxy_set_header Connection "te";
            proxy_set_header TE "trailers";
            proxy_pass_trailers on;
            proxy_set_header Accept "text/markdown";
            markdown_filter on;
            markdown_accept force;
            markdown_streaming off;
        }
    }
}
EOF

"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -t >/dev/null 2>&1 || {
    echo "FAIL: nginx -t rejected the fixture configuration" >&2
    "${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -t >&2 || true
    exit 1
}
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf

ready=0
for _ in $(seq 1 50); do
    if curl -sf "http://127.0.0.1:${PORT}/html" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.1
done
if [[ "${ready}" -ne 1 ]]; then
    fail "fixture did not become ready"
    exit 1
fi

# --- Sanity: the upstream really emits the trailer on the raw path ---

raw_headers="${BUILDROOT}/raw-headers.txt"
raw_body="${BUILDROOT}/raw-body.bin"
if curl -sf --raw -D "${raw_headers}" -o "${raw_body}" \
    "http://127.0.0.1:${PORT}/trailers-raw" \
    && grep -qi '^Trailer: *Digest' "${raw_headers}" \
    && grep -q 'Digest: sha-256=' "${raw_body}"; then
    pass "raw path proves the upstream emits Trailer: Digest and a trailer field"
else
    fail "raw path does not show the upstream trailer (fixture defect)"
fi

# --- Conversion: stale trailer metadata must not survive ---

md_headers="${BUILDROOT}/md-headers.txt"
md_body="${BUILDROOT}/md-body.bin"
md_status=0
curl -sf --raw -D "${md_headers}" -o "${md_body}" \
    -H 'Accept: text/markdown' \
    "http://127.0.0.1:${PORT}/trailers-md" || md_status=$?

if [[ "${md_status}" -ne 0 ]]; then
    fail "converted request failed (curl rc=${md_status})"
    exit 1
fi
if ! grep -qi '^Content-Type: *text/markdown' "${md_headers}"; then
    fail "converted response is missing the text/markdown content type"
    exit 1
fi
pass "converted request succeeds"

if grep -qiE '^(trailer|digest|content-digest|repr-digest|content-md5):' "${md_headers}"; then
    fail "converted response still carries trailer/digest family headers"
else
    pass "converted headers carry no Trailer/Digest family fields"
fi

if grep -qai 'digest' "${md_body}"; then
    fail "converted wire bytes still contain a trailer field"
else
    pass "converted wire bytes contain no trailer bytes"
fi

if grep -q 'trailer-probe-marker' "${md_body}"; then
    pass "converted body carries the document content"
else
    fail "converted body lost the document content"
fi

printf '\n  Results: %d passed, %d failed\n' "${PASS_COUNT}" "${FAIL_COUNT}" >&2
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
exit 0
