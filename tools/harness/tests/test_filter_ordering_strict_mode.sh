#!/usr/bin/env bash

set -e

# Report a failed assertion with its captured context before aborting.
check() {
    local label="$1" condition="$2" context="${3:-}"
    if eval "${condition}"; then
        return 0
    fi
    printf 'FAIL: %s (condition: %s)\n' "${label}" "${condition}" >&2
    if [[ -n "${context}" ]]; then
        printf 'context:\n%s\n' "${context}" >&2
    fi
    exit 1
}

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
FILTER_ORDERING_SCRIPT="${WORKSPACE_ROOT}/tests/e2e/filter_ordering_test.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/filter-ordering-strict.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT
FAKE_CURL_DIR="${TEST_ROOT}/bin"
FAKE_CURL="${FAKE_CURL_DIR}/curl"
PLAIN_STATE="${TEST_ROOT}/plain-cache-state"
STRICT_STATE="${TEST_ROOT}/strict-cache-state"

mkdir -p "${FAKE_CURL_DIR}"
printf '%s\n' \
    '#!/usr/bin/env bash' \
    'args="$*"' \
    'if [[ "$args" != *"-D"* ]]; then exit 0; fi' \
    'cache_status=""' \
    'upstream_encoding=""' \
    'if [[ "$args" == *"/gunzip"* ]]; then' \
    '    upstream_encoding=$'"'"'X-Upstream-Content-Encoding: gzip\r\n'"'" \
    'elif [[ "$args" == *"cb="* ]]; then' \
    '    if [[ -e "${FILTER_ORDERING_FAKE_CACHE_STATE}" ]]; then' \
    '        cache_status=$'"'"'X-Cache-Status: HIT\r\n'"'" \
    '    else' \
    '        : > "${FILTER_ORDERING_FAKE_CACHE_STATE}"' \
    '        cache_status=$'"'"'X-Cache-Status: MISS\r\n'"'" \
    '    fi' \
    'fi' \
    'printf '\''HTTP/1.1 200 OK\r\nContent-Type: text/markdown\r\n%s%s\r\n'\'' "${upstream_encoding}" "${cache_status}"' \
    > "${FAKE_CURL}"
chmod +x "${FAKE_CURL}"

set +e
plain_output="$(
    PATH="${FAKE_CURL_DIR}:${PATH}" \
    FILTER_ORDERING_FAKE_CACHE_STATE="${PLAIN_STATE}" \
    NGINX_URL=http://fixture TEST_PATH=/markdown GUNZIP_TEST_PATH=/gunzip \
    bash "${FILTER_ORDERING_SCRIPT}" 2>&1
)"
plain_status=$?
strict_output="$(
    PATH="${FAKE_CURL_DIR}:${PATH}" \
    FILTER_ORDERING_FAKE_CACHE_STATE="${STRICT_STATE}" \
    NGINX_URL=http://fixture TEST_PATH=/markdown GUNZIP_TEST_PATH=/gunzip \
    REQUIRE_FILTER_ORDERING_ALL=1 bash "${FILTER_ORDERING_SCRIPT}" 2>&1
)"
strict_status=$?
set -e

check "plain-mode run exits 0" "[[ \"\${plain_status}\" -eq 0 ]]" "${plain_output}"
check "strict-mode run exits 1" "[[ \"\${strict_status}\" -eq 1 ]]" "${strict_output}"
check "plain-mode output reports 2 skipped" \
    "[[ \"\${plain_output}\" == *\"2 skipped\"* ]]" "${plain_output}"
check "strict-mode output reports the filter-ordering skip" \
    "[[ \"\${strict_output}\" == *\"Required filter-ordering assertion skipped\"* ]]" "${strict_output}"

echo "filter-ordering strict-mode regression passed"
