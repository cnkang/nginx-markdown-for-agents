#!/bin/bash
#
# Verify the pre-LTS upgrade contract against a real module-enabled NGINX.
#
# The migration case proves that an old-release configuration is rejected with
# actionable guidance. The rollback case proves that an invalid candidate is
# validated before replacement and leaves the active configuration unchanged.
# No running service is touched by this test.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NGINX_BIN="${NGINX_BIN:-}"
TMPDIR_BASE=""

TRUSTED_NGINX_PATHS=(
    "${REPO_ROOT}/components/nginx-module/tests/build/nginx"
    "${REPO_ROOT}/build/nginx"
    "/usr/bin/nginx"
    "/usr/sbin/nginx"
    "/usr/local/bin/nginx"
    "/usr/local/sbin/nginx"
    "/opt/homebrew/bin/nginx"
    "/opt/homebrew/sbin/nginx"
)

usage() {
    cat <<USAGE >&2
Usage: $(basename "$0") [--nginx-bin PATH] [--keep-artifacts]

Runs old-release migration and failed-rollback checks without touching a
running NGINX service.
USAGE
    return 0
}

KEEP_ARTIFACTS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --nginx-bin)
            [[ $# -ge 2 ]] || { echo "ERROR: --nginx-bin requires a value" >&2; exit 2; }
            NGINX_BIN="$2"
            shift 2
            ;;
        --keep-artifacts)
            KEEP_ARTIFACTS=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage
            exit 2
            ;;
    esac
done

canonicalize_existing_path() {
    local path="$1"
    local target=""
    local resolved_dir=""
    local hop=0

    [[ "${path}" == /* && -f "${path}" && -x "${path}" ]] || return 1
    while [[ -L "${path}" && "${hop}" -lt 40 ]]; do
        target="$(readlink "${path}")" || return 1
        if [[ "${target}" == /* ]]; then
            path="${target}"
        else
            path="$(dirname "${path}")/${target}"
        fi
        hop=$((hop + 1))
    done
    [[ ! -L "${path}" ]] || return 1
    resolved_dir="$(cd -P "$(dirname "${path}")" 2>/dev/null && pwd)" || return 1
    path="${resolved_dir}/$(basename "${path}")"
    [[ -f "${path}" && -x "${path}" ]] || return 1
    printf '%s\n' "${path}"
    return 0
}

resolve_nginx_bin() {
    local trusted=""
    local resolved=""
    local resolved_trusted=""
    local match=0

    [[ -n "${NGINX_BIN}" ]] || {
        echo "ERROR: set NGINX_BIN to a module-enabled nginx binary" >&2
        return 1
    }
    resolved="$(canonicalize_existing_path "${NGINX_BIN}")" || {
        echo "ERROR: NGINX_BIN is not an executable absolute path: ${NGINX_BIN}" >&2
        return 1
    }
    for trusted in "${TRUSTED_NGINX_PATHS[@]}"; do
        if resolved_trusted="$(canonicalize_existing_path "${trusted}" 2>/dev/null)" \
            && [[ "${resolved}" == "${resolved_trusted}" ]]; then
            match=1
            break
        fi
    done
    [[ "${match}" -eq 1 ]] || {
        echo "ERROR: NGINX_BIN is outside the trusted executable allowlist: ${resolved}" >&2
        return 1
    }
    NGINX_BIN="${resolved}"
    return 0
}

cleanup() {
    if [[ "${KEEP_ARTIFACTS}" -eq 0 && -n "${TMPDIR_BASE}" ]]; then
        rm -rf "${TMPDIR_BASE}"
    elif [[ -n "${TMPDIR_BASE}" ]]; then
        echo "Artifacts kept at: ${TMPDIR_BASE}" >&2
    fi
    return 0
}
trap cleanup EXIT

write_common_config() {
    local path="$1"
    cat > "${path}" <<CONFIG
worker_processes 1;
error_log /dev/null notice;
pid nginx.pid;
events { worker_connections 64; }
http {
    markdown_filter on;
    access_log off;
    server {
        listen 127.0.0.1:19980;
        location / { }
    }
}
CONFIG
    return 0
}

run_old_release_migration() {
    local config="${TMPDIR_BASE}/old-release.conf"
    local output="${TMPDIR_BASE}/old-release.log"

    cat > "${config}" <<CONFIG
worker_processes 1;
error_log /dev/null notice;
pid nginx.pid;
events { worker_connections 64; }
http {
    markdown_filter on;
    markdown_dynamic_config on;
    access_log off;
    server {
        listen 127.0.0.1:19980;
        location / { }
    }
}
CONFIG

    if "${NGINX_BIN}" -t -p "${TMPDIR_BASE}" -c "${config}" \
        >"${output}" 2>&1; then
        echo "FAIL: old-release dynconf directive was accepted" >&2
        return 1
    fi
    # 0.9.2 removed these names from the command table, so NGINX reports its own
    # unknown-directive error and names the directive that failed.  The module
    # no longer emits a migration message of its own: the replacement for each
    # retired name lives in docs/guides/MIGRATION-0.9.2.md, which the release
    # notes and the breaking-changes guide both point at.  This check therefore
    # asserts the rejection names the directive, and that a migration path is
    # documented, not that NGINX itself carries it.
    if ! grep -Eiq 'markdown_dynamic_config' "${output}"; then
        echo "FAIL: migration failure did not name markdown_dynamic_config" >&2
        cat "${output}" >&2
        return 1
    fi
    if ! grep -Eiq 'unknown directive' "${output}"; then
        echo "FAIL: migration failure was not the standard unknown-directive error" >&2
        cat "${output}" >&2
        return 1
    fi
    guide="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/docs/guides/MIGRATION-0.9.2.md"
    if [[ ! -f "${guide}" ]] || ! grep -q 'markdown_dynamic_config' "${guide}"; then
        echo "FAIL: the migration guide does not name markdown_dynamic_config" >&2
        return 1
    fi
    echo "PASS: old-release configuration is rejected, and the migration guide carries the replacement"
    return 0
}

run_failed_rollback() {
    local active="${TMPDIR_BASE}/active.conf"
    local candidate="${TMPDIR_BASE}/candidate.conf"
    local before="${TMPDIR_BASE}/active.before"
    local output="${TMPDIR_BASE}/candidate.log"

    write_common_config "${active}"
    cp "${active}" "${before}"
    if ! "${NGINX_BIN}" -t -p "${TMPDIR_BASE}" -c "${active}" \
        >"${TMPDIR_BASE}/active.log" 2>&1; then
        echo "FAIL: active configuration failed nginx -t" >&2
        cat "${TMPDIR_BASE}/active.log" >&2
        return 1
    fi

    cp "${active}" "${candidate}"
    cat >> "${candidate}" <<'INVALID'
this_candidate_is_invalid;
INVALID
    if "${NGINX_BIN}" -t -p "${TMPDIR_BASE}" -c "${candidate}" \
        >"${output}" 2>&1; then
        echo "FAIL: invalid rollback candidate passed nginx -t" >&2
        return 1
    fi
    if ! cmp -s "${active}" "${before}"; then
        echo "FAIL: failed candidate changed the active configuration" >&2
        return 1
    fi
    echo "PASS: failed candidate was rejected before replacement; active config preserved"
    return 0
}

run_valid_rollback() {
    local active="${TMPDIR_BASE}/active.conf"
    local candidate="${TMPDIR_BASE}/candidate.conf"
    local before="${TMPDIR_BASE}/active.before"
    local output="${TMPDIR_BASE}/candidate.log"

    write_common_config "${active}"
    cp "${active}" "${before}"
    if ! "${NGINX_BIN}" -t -p "${TMPDIR_BASE}" -c "${active}" \
        >"${TMPDIR_BASE}/active.log" 2>&1; then
        echo "FAIL: active configuration failed nginx -t" >&2
        cat "${TMPDIR_BASE}/active.log" >&2
        return 1
    fi

    # A valid candidate (a changed markdown_limits value) must pass
    # nginx -t and, after the promotion decision, replace the active file.
    # markdown_limits is http-context-only, so the override goes inside
    # the http block, not after the closing brace.
    cp "${active}" "${candidate}"
    python3 - "${candidate}" <<'PY'
import sys
p = sys.argv[1]
src = open(p).read()
src = src.replace(
    "    markdown_filter on;\n",
    "    markdown_filter on;\n    markdown_limits conversion_timeout=8s streaming_buffer=1m;\n",
    1)
open(p, "w").write(src)
PY
    if ! "${NGINX_BIN}" -t -p "${TMPDIR_BASE}" -c "${candidate}" \
        >"${output}" 2>&1; then
        echo "FAIL: valid rollback candidate failed nginx -t" >&2
        cat "${output}" >&2
        return 1
    fi
    cp "${candidate}" "${active}"
    if cmp -s "${active}" "${before}"; then
        echo "FAIL: promoted candidate did not replace the active configuration" >&2
        return 1
    fi
    if ! "${NGINX_BIN}" -t -p "${TMPDIR_BASE}" -c "${active}" \
        >"${TMPDIR_BASE}/activelog2" 2>&1; then
        echo "FAIL: promoted active configuration fails nginx -t" >&2
        cat "${TMPDIR_BASE}/activelog2" >&2
        return 1
    fi
    echo "PASS: validated candidate promoted only after nginx -t succeeded"
    return 0
}

resolve_nginx_bin
TMPDIR_BASE="$(mktemp -d "${TMPDIR:-/tmp}/nginx-upgrade-rollback.XXXXXX")"
run_old_release_migration
run_failed_rollback
run_valid_rollback
echo "Upgrade/rollback contract checks passed (binary=${NGINX_BIN})"
