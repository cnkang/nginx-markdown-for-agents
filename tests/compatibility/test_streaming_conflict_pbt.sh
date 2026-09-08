#!/bin/bash
#
# Feature: pre-lts-convergence-092, Property 4: Explicit-streaming +
# full-cache-validation conflict fails load, including inheritance-formed
#
# Generate configuration pairs and drive the real nginx -t parser. The
# conflicting cases exercise both parent-to-child inheritance directions;
# control cases prove that auto/off/ims_only combinations do not acquire a
# spurious conflict.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NGINX_BIN="${NGINX_BIN:-}"
ITERATIONS="${PBT_ITERATIONS:-120}"
SEED="${PBT_SEED:-6204}"
DRY_RUN=0
KEEP_ARTIFACTS=0
TMPDIR_BASE=""
TESTS_RUN=0
TESTS_FAILED=0

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
    cat <<EOF >&2
Usage: $(basename "$0") [--nginx-bin PATH] [--iterations N] [--seed N] \\
    [--dry-run] [--keep-artifacts] [-h|--help]

Property 4: generated inherited streaming/cache pairs, >=100 iterations.
EOF
    return 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --nginx-bin)
            [[ $# -ge 2 ]] || { echo "ERROR: --nginx-bin requires a value" >&2; exit 2; }
            NGINX_BIN="$2"
            shift 2
            ;;
        --iterations)
            [[ $# -ge 2 ]] || { echo "ERROR: --iterations requires a value" >&2; exit 2; }
            ITERATIONS="$2"
            shift 2
            ;;
        --seed)
            [[ $# -ge 2 ]] || { echo "ERROR: --seed requires a value" >&2; exit 2; }
            SEED="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=1
            shift
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

if [[ ! "${ITERATIONS}" =~ ^[0-9]+$ || "${ITERATIONS}" -lt 100 ]]; then
    echo "ERROR: iterations must be an integer >= 100: ${ITERATIONS}" >&2
    exit 2
fi
if [[ ! "${SEED}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: seed must be a non-negative integer: ${SEED}" >&2
    exit 2
fi

canonicalize_existing_path() {
    local path="$1"
    local target=""
    local resolved_dir=""
    local hop=0

    if [[ "${path}" != /* || ! -f "${path}" || ! -x "${path}" ]]; then
        return 1
    fi
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

    if [[ -z "${NGINX_BIN}" ]]; then
        echo "ERROR: set NGINX_BIN to a module-enabled nginx binary" >&2
        return 1
    fi
    if ! resolved="$(canonicalize_existing_path "${NGINX_BIN}")"; then
        echo "ERROR: NGINX_BIN is not an executable absolute path: ${NGINX_BIN}" >&2
        return 1
    fi
    for trusted in "${TRUSTED_NGINX_PATHS[@]}"; do
        if resolved_trusted="$(canonicalize_existing_path "${trusted}" 2>/dev/null)" \
            && [[ "${resolved}" == "${resolved_trusted}" ]]; then
            match=1
            break
        fi
    done
    if [[ "${match}" -ne 1 ]]; then
        echo "ERROR: NGINX_BIN is outside the trusted executable allowlist: ${resolved}" >&2
        return 1
    fi
    NGINX_BIN="${resolved}"
    return 0
}

cleanup() {
    if [[ "${KEEP_ARTIFACTS}" -eq 0 && -n "${TMPDIR_BASE}" ]]; then
        rm -rf "${TMPDIR_BASE}"
    elif [[ -n "${TMPDIR_BASE}" ]]; then
        echo "Artifacts kept in: ${TMPDIR_BASE}" >&2
    fi
    return 0
}
trap cleanup EXIT

write_config() {
    local conf_file="$1"
    local variant="$2"
    local port=$((19940 + (variant % 10)))

    case "${variant}" in
        0)
            cat > "${conf_file}" <<EOF
worker_processes 1;
error_log /dev/null notice;
pid nginx.pid;
events { worker_connections 64; }
http {
    markdown_filter on;
    access_log off;
    server {
        listen 127.0.0.1:${port};
        markdown_streaming force;
        markdown_cache_validation ims_only;
        location / {
            markdown_cache_validation full;
            
        }
    }
}
EOF
            ;;
        1)
            cat > "${conf_file}" <<EOF
worker_processes 1;
error_log /dev/null notice;
pid nginx.pid;
events { worker_connections 64; }
http {
    markdown_filter on;
    access_log off;
    server {
        listen 127.0.0.1:${port};
        markdown_cache_validation full;
        location / {
            markdown_streaming force;
            
        }
    }
}
EOF
            ;;
        2)
            cat > "${conf_file}" <<EOF
worker_processes 1;
error_log /dev/null notice;
pid nginx.pid;
events { worker_connections 64; }
http {
    markdown_filter on;
    access_log off;
    server {
        listen 127.0.0.1:${port};
        markdown_streaming auto;
        markdown_cache_validation full;
        location / {  }
    }
}
EOF
            ;;
        3)
            cat > "${conf_file}" <<EOF
worker_processes 1;
error_log /dev/null notice;
pid nginx.pid;
events { worker_connections 64; }
http {
    markdown_filter on;
    access_log off;
    server {
        listen 127.0.0.1:${port};
        markdown_streaming off;
        markdown_cache_validation full;
        location / {  }
    }
}
EOF
            ;;
        4)
            cat > "${conf_file}" <<EOF
worker_processes 1;
error_log /dev/null notice;
pid nginx.pid;
events { worker_connections 64; }
http {
    markdown_filter on;
    access_log off;
    server {
        listen 127.0.0.1:${port};
        markdown_streaming force;
        markdown_cache_validation ims_only;
        location / {  }
    }
}
EOF
            ;;
        5)
            cat > "${conf_file}" <<EOF
worker_processes 1;
error_log /dev/null notice;
pid nginx.pid;
events { worker_connections 64; }
http {
    markdown_filter on;
    access_log off;
    server {
        listen 127.0.0.1:${port};
        markdown_streaming force;
        markdown_cache_validation off;
        location / {  }
    }
}
EOF
            ;;
        *)
            echo "ERROR: unknown generated variant ${variant}" >&2
            return 1
            ;;
    esac
    return 0
}

variant_expected_failure() {
    case "$1" in
        0|1) return 0 ;;
        *) return 1 ;;
    esac
}

run_iteration() {
    local idx="$1"
    local variant=$(( (idx + SEED) % 6 ))
    local conf_file="${TMPDIR_BASE}/pbt_${idx}.conf"
    local log_file="${TMPDIR_BASE}/pbt_${idx}.log"
    local expected=0
    local actual=0

    write_config "${conf_file}" "${variant}"
    TESTS_RUN=$((TESTS_RUN + 1))
    if variant_expected_failure "${variant}"; then
        expected=1
    fi

    if [[ "${DRY_RUN}" -eq 1 ]]; then
        if ! grep -q 'markdown_streaming' "${conf_file}" \
            || ! grep -q 'markdown_cache_validation' "${conf_file}"; then
            echo "FAIL: iteration ${idx} missing generated directives" >&2
            TESTS_FAILED=$((TESTS_FAILED + 1))
        fi
        return 0
    fi

    if "${NGINX_BIN}" -t -p "${TMPDIR_BASE}/" -c "${conf_file}" \
        >"${log_file}" 2>&1; then
        actual=0
    else
        actual=1
    fi
    if [[ "${actual}" -ne "${expected}" ]]; then
        echo "FAIL: iteration ${idx} variant ${variant} expected failure=${expected}" >&2
        tail -n 5 "${log_file}" >&2 || true
        TESTS_FAILED=$((TESTS_FAILED + 1))
    elif [[ "${expected}" -eq 1 ]] \
        && ! grep -Eiq 'markdown_streaming|cache_validation|conflict' "${log_file}"; then
        echo "FAIL: iteration ${idx} rejected without conflict guidance" >&2
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
    return 0
}

if [[ "${DRY_RUN}" -eq 0 ]]; then
    resolve_nginx_bin || exit 2
fi
TMPDIR_BASE="$(mktemp -d /tmp/streaming-conflict-pbt.XXXXXX)"

echo "Streaming/cache conflict PBT: iterations=${ITERATIONS} seed=${SEED}" >&2
if [[ "${DRY_RUN}" -eq 0 ]]; then
    echo "NGINX binary: ${NGINX_BIN}" >&2
fi

idx=0
while [[ "${idx}" -lt "${ITERATIONS}" ]]; do
    run_iteration "${idx}"
    idx=$((idx + 1))
done

if [[ "${TESTS_RUN}" -lt 100 || "${TESTS_FAILED}" -ne 0 ]]; then
    echo "FAIL: Property 4 failed (${TESTS_FAILED} failures of ${TESTS_RUN})" >&2
    exit 1
fi
echo "PASS: Property 4 held across ${TESTS_RUN} generated configs" >&2
exit 0
