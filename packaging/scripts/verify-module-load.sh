#!/usr/bin/env bash
# verify-module-load.sh — Verify that a dynamic module is loaded by NGINX.
#
# The caller supplies the NGINX executable and module path.  The positive
# configuration must load the module and parse its directive; the negative
# configuration must omit load_module and fail on that directive.  A plain
# verify_module_load confirms that an NGINX module loads successfully and is required for parsing its directive.

verify_module_load() {
    local nginx_bin="${1:-}"
    local module_path="${2:-}"
    local temp_dir=""
    local positive_conf=""
    local negative_conf=""

    if [[ -z "${nginx_bin}" || -z "${module_path}" ]]; then
        echo "verify_module_load: NGINX executable and module path are required" >&2
        return 1
    fi
    if ! command -v "${nginx_bin}" >/dev/null 2>&1; then
        echo "verify_module_load: NGINX executable not found: ${nginx_bin}" >&2
        return 1
    fi
    if [[ ! -f "${module_path}" ]]; then
        echo "verify_module_load: module not found: ${module_path}" >&2
        return 1
    fi

    if ! temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/nginx-module-load.XXXXXX")"; then
        echo "verify_module_load: could not create temporary directory" >&2
        return 1
    fi
    positive_conf="${temp_dir}/positive.conf"
    negative_conf="${temp_dir}/negative.conf"

    cat > "${positive_conf}" <<CONF
load_module "${module_path}";
daemon off;
worker_processes 1;
events { worker_connections 64; }
http {
    markdown_filter on;
    server {
        listen 127.0.0.1:19999;
        location / { return 200 "ok"; }
    }
}
CONF

    if ! "${nginx_bin}" -t -c "${positive_conf}" >/dev/null 2>&1; then
        echo "verify_module_load: positive load_module configuration failed" >&2
        rm -rf "${temp_dir}"
        return 1
    fi

    cat > "${negative_conf}" <<CONF
daemon off;
worker_processes 1;
events { worker_connections 64; }
http {
    markdown_filter on;
    server {
        listen 127.0.0.1:19999;
        location / { return 200 "ok"; }
    }
}
CONF

    if "${nginx_bin}" -t -c "${negative_conf}" >/dev/null 2>&1; then
        echo "verify_module_load: directive parsed without load_module" >&2
        rm -rf "${temp_dir}"
        return 1
    fi

    rm -rf "${temp_dir}"
    return 0
}
