#!/usr/bin/env bash
# Native qualification for SSI, auth_request, filter ordering, and internal
# redirects. The fixture uses the real NGINX binary and module; the Python
# server only supplies deterministic HTML and gzip upstream responses.

set -euo pipefail

PORT="${PORT:-18099}"
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
FILTER_ORDERING_SCRIPT="${WORKSPACE_ROOT}/tests/e2e/filter_ordering_test.sh"
SUBREQUEST_SSI_SCRIPT="${WORKSPACE_ROOT}/tests/e2e/subrequest_ssi_test.sh"

# shellcheck source=tools/lib/nginx_markdown_native_build.sh
source "${NATIVE_BUILD_HELPER}"

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") [--keep-artifacts] [--nginx-bin PATH] [--port PORT]

Run the blocking native SSI/auth_request and filter-ordering qualification.
The NGINX binary must include the markdown module.
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
        sed -n '1,120p' "${RUNTIME}/logs/error.log" >&2 || true
    fi

    if [[ "${KEEP_ARTIFACTS}" -eq 0 && -n "${BUILDROOT}" && -d "${BUILDROOT}" ]]; then
        case "${BUILDROOT}" in
            "${TMPDIR:-/tmp}"/nginx-native-e2e.*)
                rm -rf "${BUILDROOT}"
                ;;
            *)
                echo "Refusing to remove unexpected path: ${BUILDROOT}" >&2
                ;;
        esac
    elif [[ "${KEEP_ARTIFACTS}" -eq 1 && -n "${BUILDROOT}" ]]; then
        echo "Native E2E artifacts kept at: ${BUILDROOT}" >&2
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
if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65533 )); then
    echo "PORT must be an integer between 1024 and 65533: ${PORT}" >&2
    exit 2
fi

BUILDROOT="$(mktemp -d "${TMPDIR:-/tmp}/nginx-native-e2e.XXXXXX")"
RUNTIME="${BUILDROOT}/runtime"
UPSTREAM_PORT=$((PORT + 1))
mkdir -p "${RUNTIME}/conf" "${RUNTIME}/html/auth-protected" \
    "${RUNTIME}/logs" "${RUNTIME}/cache"

LOAD_MODULE_LINE="$(markdown_prepare_runtime_reuse "${NGINX_BIN}" "${RUNTIME}")"
NGINX_EXECUTABLE="${NGINX_BIN}"

cat > "${BUILDROOT}/upstream.py" <<'PY'
import gzip
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HTML = b"<!doctype html><html><body><h1>Proxy HTML</h1><p>proxy body</p></body></html>"
GZIP_HTML = gzip.compress(HTML, mtime=0)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        request_path = urlsplit(self.path).path
        if request_path == "/gzip-html":
            body = GZIP_HTML
            encoding = "gzip"
        elif request_path in ("/html", "/plain-html"):
            body = HTML
            encoding = ""
        else:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Cache-Control", "public, max-age=60")
        self.send_header("Content-Length", str(len(body)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


server = ThreadingHTTPServer(("127.0.0.1", int(os.environ["UPSTREAM_PORT"])), Handler)
server.serve_forever()
PY

cat > "${RUNTIME}/html/frag.md" <<'EOF'
<h1>SSI fragment</h1><p>converted subrequest content</p>
EOF
cat > "${RUNTIME}/html/page.ssi" <<'EOF'
<!doctype html><html><body><!--# include virtual="/frag.md" --></body></html>
EOF
cat > "${RUNTIME}/html/ready.txt" <<'EOF'
native fixture ready
EOF
cat > "${RUNTIME}/html/auth-check.html" <<'EOF'
<h1>Auth check</h1><p>authorized</p>
EOF
cat > "${RUNTIME}/html/auth-protected/protected.html" <<'EOF'
<h1>Protected page</h1><p>authorized content</p>
EOF
cat > "${RUNTIME}/html/error-markdown.html" <<'EOF'
<h1>Error redirect</h1><p>converted error target</p>
EOF
cat > "${RUNTIME}/html/named-markdown.html" <<'EOF'
<h1>Named location</h1><p>converted named target</p>
EOF
cat > "${RUNTIME}/html/internal-target.html" <<'EOF'
<h1>Internal redirect</h1><p>converted internal target</p>
EOF

cat > "${RUNTIME}/conf/nginx.conf" <<EOF
${LOAD_MODULE_LINE}worker_processes 1;
error_log logs/error.log notice;
pid logs/nginx.pid;

events { worker_connections 128; }

http {
    include mime.types;
    default_type application/octet-stream;
    root html;
    sendfile off;
    gzip on;
    gzip_min_length 1;
    gzip_proxied any;
    gzip_types text/markdown;
    proxy_cache_path cache keys_zone=markdown_cache:1m max_size=8m inactive=1m use_temp_path=off;
    upstream markdown_backend {
        server 127.0.0.1:${UPSTREAM_PORT};
    }
    markdown_metrics_shm_size 128k;

    server {
        listen 127.0.0.1:${PORT};
        server_name localhost;

        location = / {
            default_type text/plain;
            try_files /ready.txt =404;
        }

        location = /markdown {
            proxy_pass http://markdown_backend/html;
            proxy_cache markdown_cache;
            proxy_cache_key "\$uri|\$args|\$http_accept_encoding";
            add_header X-Cache-Status \$upstream_cache_status always;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming off;
        }

        location = /gunzip-markdown {
            proxy_pass http://markdown_backend/gzip-html;
            proxy_set_header Accept "text/markdown";
            add_header X-Upstream-Content-Encoding \$upstream_http_content_encoding always;
            gunzip on;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming off;
        }

        location = /page.ssi {
            root html;
            types { }
            default_type text/html;
            ssi on;
            # Keep the SSI template intact; its fragment subrequest is the
            # representation that this fixture verifies independently.
            markdown_filter off;
        }

        location = /frag.md {
            root html;
            types { }
            default_type text/html;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming off;
        }

        location = /auth-check {
            internal;
            default_type text/html;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming off;
            try_files /auth-check.html =404;
        }

        location = /auth-protected/ {
            auth_request /auth-check;
            default_type text/html;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming off;
            try_files /auth-protected/protected.html =404;
        }

        # Entry locations deliberately disable conversion. Each target
        # location enables it, proving that an internal redirect uses the new
        # effective configuration and commits one representation only.
        location = /error-entry {
            markdown_filter off;
            error_page 404 = @error_markdown;
            try_files /missing-native-e2e-file =404;
        }

        location = /named-entry {
            markdown_filter off;
            try_files /missing-native-e2e-file @named_markdown;
        }

        location = /internal-entry {
            markdown_filter off;
            error_page 404 = /internal-target;
            try_files /missing-native-e2e-file =404;
        }

        location @error_markdown {
            default_type text/html;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming off;
            root html;
            try_files /error-markdown.html =404;
        }

        location @named_markdown {
            default_type text/html;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming off;
            root html;
            try_files /named-markdown.html =404;
        }

        location = /internal-target {
            default_type text/html;
            markdown_filter on;
            markdown_accept wildcard;
            markdown_streaming off;
            root html;
            try_files /internal-target.html =404;
        }

        location = /markdown-metrics {
            markdown_metrics;
        }

        location = /diagnostics {
            markdown_diagnostics on;
        }
    }
}
EOF

UPSTREAM_PORT="${UPSTREAM_PORT}" python3 -u "${BUILDROOT}/upstream.py" \
    >"${RUNTIME}/logs/upstream.log" 2>&1 &
UPSTREAM_PID=$!

for _attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if curl -fsS "http://127.0.0.1:${UPSTREAM_PORT}/html" >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done
if ! curl -fsS "http://127.0.0.1:${UPSTREAM_PORT}/html" >/dev/null; then
    echo "Native upstream fixture did not become ready" >&2
    sed -n '1,120p' "${RUNTIME}/logs/upstream.log" >&2 || true
    exit 1
fi

"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf -t
"${NGINX_EXECUTABLE}" -p "${RUNTIME}" -c conf/nginx.conf

BASE_URL="http://127.0.0.1:${PORT}"
for _attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if curl -fsS "${BASE_URL}/" >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done
if ! curl -fsS "${BASE_URL}/" >/dev/null; then
    echo "Native NGINX fixture did not become ready" >&2
    exit 1
fi

echo "=== Blocking filter-ordering qualification ===" >&2
NGINX_URL="${BASE_URL}" TEST_PATH=/markdown GUNZIP_TEST_PATH=/gunzip-markdown \
    METRICS_PATH=/markdown-metrics \
    REQUIRE_FILTER_ORDERING_ALL="${REQUIRE_FILTER_ORDERING_ALL:-1}" \
    REQUIRE_BROTLI_FILTER="${REQUIRE_BROTLI_FILTER:-0}" \
    bash "${FILTER_ORDERING_SCRIPT}"

echo "=== Blocking SSI/auth_request qualification ===" >&2
NGINX_URL="${BASE_URL}" PAGE_PATH=/page.ssi FRAG_PATH=/frag.md \
    AUTH_PAGE_PATH=/auth-protected/ METRICS_PATH=/markdown-metrics \
    EXPECT_SSI_WRAPPER=1 \
    REQUIRE_AUTH_SUBREQUEST="${REQUIRE_AUTH_SUBREQUEST:-1}" \
    bash "${SUBREQUEST_SSI_SCRIPT}"

metrics_snapshot() {
    curl -fsS "${BASE_URL}/markdown-metrics"
    return 0
}

metric_sum() {
    local metrics="$1" prefix="$2"
    awk -v prefix="${prefix}" '
        index($0, prefix) == 1 { total += $NF; found = 1 }
        END { print found ? total : 0 }
    ' <<<"${metrics}"
    return 0
}

converted_attempts() {
    metric_sum "$1" 'nginx_markdown_conversion_attempts_total{engine="'
}

converted_terminals() {
    metric_sum "$1" 'nginx_markdown_requests_total{outcome="converted",stage="conversion",reason="converted"}'
}

runtime_value() {
    local key="$1"
    curl -fsS "${BASE_URL}/diagnostics" | python3 -c '
import json
import sys

data = json.load(sys.stdin)
print(data["runtime"][sys.argv[1]])
' "${key}"
}

run_load_tools() {
    if [[ "${RUN_LOAD_TOOLS:-0}" != "1" ]]; then
        return 0
    fi
    command -v hey >/dev/null 2>&1 || {
        echo "RUN_LOAD_TOOLS=1 requires hey" >&2
        return 1
    }
    command -v ab >/dev/null 2>&1 || {
        echo "RUN_LOAD_TOOLS=1 requires ab" >&2
        return 1
    }

    hey -n 20 -c 4 -H 'Accept: text/markdown' \
        "${BASE_URL}/markdown" >/dev/null
    ab -n 20 -c 4 -H 'Accept: text/markdown' \
        "${BASE_URL}/markdown" >/dev/null

    local inflight pending
    inflight="$(runtime_value inflight)"
    pending="$(runtime_value pending_output)"
    if [[ "${inflight}" == "0" && "${pending}" == "0" ]]; then
        pass "hey and ab load drained inflight and pending output"
    else
        fail "hey and ab load left runtime state (inflight=${inflight}, pending_output=${pending})"
    fi
    return 0
}

run_redirect_case() {
    local path="$1" label="$2"
    local before after attempts_before attempts_after terminals_before terminals_after
    local header_before header_after header_attempts_before header_attempts_after
    local header_terminals_before header_terminals_after
    local body headers content_type_count inflight pending

    before="$(metrics_snapshot)"
    attempts_before="$(converted_attempts "${before}")"
    terminals_before="$(converted_terminals "${before}")"
    if ! body="$(curl -fsS -H 'Accept: text/markdown' "${BASE_URL}${path}")"; then
        fail "${label}: request failed"
        return 0
    fi
    if echo "${body}" | grep -qE '^# |^[-*] '; then
        pass "${label}: destination location converted the response"
    else
        fail "${label}: destination response was not converted"
    fi

    after="$(metrics_snapshot)"
    attempts_after="$(converted_attempts "${after}")"
    terminals_after="$(converted_terminals "${after}")"

    header_before="$(metrics_snapshot)"
    header_attempts_before="$(converted_attempts "${header_before}")"
    header_terminals_before="$(converted_terminals "${header_before}")"

    headers="$(curl -fsS -D - -o /dev/null -H 'Accept: text/markdown' "${BASE_URL}${path}")"
    content_type_count="$(printf '%s\n' "${headers}" | tr -d '\r' | grep -ic '^Content-Type:' || true)"
    if [[ "${content_type_count}" -eq 1 ]]; then
        pass "${label}: header filter committed one Content-Type"
    else
        fail "${label}: expected one Content-Type header, got ${content_type_count}"
    fi

    header_after="$(metrics_snapshot)"
    header_attempts_after="$(converted_attempts "${header_after}")"
    header_terminals_after="$(converted_terminals "${header_after}")"
    if [[ $((header_attempts_after - header_attempts_before)) -eq 1 ]]; then
        pass "${label}: header request recorded exactly one conversion attempt"
    else
        fail "${label}: header request conversion-attempt delta was ${header_attempts_after}-${header_attempts_before}"
    fi
    if [[ $((header_terminals_after - header_terminals_before)) -eq 1 ]]; then
        pass "${label}: header request recorded exactly one terminal outcome"
    else
        fail "${label}: header request terminal delta was ${header_terminals_after}-${header_terminals_before}"
    fi

    if [[ $((attempts_after - attempts_before)) -eq 1 ]]; then
        pass "${label}: exactly one conversion attempt"
    else
        fail "${label}: expected one conversion attempt, delta=${attempts_after}-${attempts_before}"
    fi
    if [[ $((terminals_after - terminals_before)) -eq 1 ]]; then
        pass "${label}: exactly one converted terminal outcome"
    else
        fail "${label}: expected one converted terminal outcome, delta=${terminals_after}-${terminals_before}"
    fi

    inflight="$(runtime_value inflight)"
    pending="$(runtime_value pending_output)"
    if [[ "${inflight}" == "0" && "${pending}" == "0" ]]; then
        pass "${label}: inflight and pending output returned to zero"
    else
        fail "${label}: runtime state not drained (inflight=${inflight}, pending_output=${pending})"
    fi
    return 0
}

echo "=== Internal redirect/error_page/named-location qualification ===" >&2
run_load_tools
run_redirect_case /error-entry "error_page named redirect"
run_redirect_case /named-entry "try_files named location"
run_redirect_case /internal-entry "error_page internal redirect"

echo "Native subrequest/filter-ordering E2E: ${PASS_COUNT} local assertions, ${FAIL_COUNT} failures" >&2
if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    exit 1
fi
exit 0
