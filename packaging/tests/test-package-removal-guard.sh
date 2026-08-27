#!/bin/bash
# Exercise the package-removal guard without changing a host NGINX install.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SCRIPT="${SCRIPT_DIR}/../nfpm/scripts/preremove.sh"
FAKE_ROOT="$(mktemp -d)"
RUN_SCRIPT="${FAKE_ROOT}/preremove.sh"
trap 'rm -rf "${FAKE_ROOT}"' EXIT

mkdir -p "${FAKE_ROOT}/usr/sbin" "${FAKE_ROOT}/usr/bin" \
    "${FAKE_ROOT}/sbin" "${FAKE_ROOT}/bin"

# Link the fixed utility manifest the guard needs for its trusted-path
# security checks (path canonicalization plus ownership/mode inspection).
for util in grep readlink stat; do
    real_path="$(command -v "${util}" 2>/dev/null || true)"
    if [[ -n "${real_path}" ]]; then
        ln -sf "${real_path}" "${FAKE_ROOT}/usr/bin/${util}"
    fi
done

sed -e "s|^TRUSTED_PATH_ROOT=\"\"$|TRUSTED_PATH_ROOT=\"${FAKE_ROOT}\"|" \
    -e "s|/etc/nginx|${FAKE_ROOT}/etc/nginx|g" \
    "${SOURCE_SCRIPT}" > "${RUN_SCRIPT}"
if ! grep -F -q "TRUSTED_PATH_ROOT=\"${FAKE_ROOT}\"" "${RUN_SCRIPT}"; then
    printf 'FAIL: test sandbox was not wired into the guard\n' >&2
    exit 1
fi
chmod 0755 "${RUN_SCRIPT}"

write_fake_nginx() {
    local mode="$1"
    case "$mode" in
        reference)
            cat > "${FAKE_ROOT}/usr/sbin/nginx" <<'EOF'
#!/bin/bash
printf 'load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;\n'
exit 0
EOF
            ;;
        clear)
            cat > "${FAKE_ROOT}/usr/sbin/nginx" <<'EOF'
#!/bin/bash
printf 'http { server { listen 80; } }\n'
exit 0
EOF
            ;;
        unreadable)
            cat > "${FAKE_ROOT}/usr/sbin/nginx" <<'EOF'
#!/bin/bash
printf 'nginx: configuration could not be tested\n' >&2
exit 1
EOF
            ;;
        *)
            printf 'FAIL: unsupported fake NGINX mode: %s\n' "$mode" >&2
            exit 1
            ;;
    esac
    chmod 0755 "${FAKE_ROOT}/usr/sbin/nginx"
}

run_case() {
    local mode="$1"
    local action="$2"
    local expected_status="$3"
    local output
    local status=0

    write_fake_nginx "$mode"
    output="$(${RUN_SCRIPT} "${action}" 2>&1)" || status=$?
    if [[ "$status" -ne "$expected_status" ]]; then
        printf 'FAIL: %s/%s returned %s, expected %s\n%s\n' \
            "$mode" "$action" "$status" "$expected_status" "$output" >&2
        exit 1
    fi
    printf 'PASS: %s/%s returned %s\n' "$mode" "$action" "$status" >&2
    printf '%s\n' "$output"
}

reference_output="$(run_case reference remove 1)"
if ! printf '%s\n' "$reference_output" | grep -F -q "nginx -t"; then
    printf 'FAIL: blocked removal did not explain the nginx -t retry\n' >&2
    exit 1
fi
if ! printf '%s\n' "$reference_output" | grep -F -q "No NGINX configuration was modified automatically"; then
    printf 'FAIL: blocked removal did not document the no-edit contract\n' >&2
    exit 1
fi

run_case reference upgrade 0 >/dev/null
run_case reference deconfigure 0 >/dev/null
run_case reference failed-upgrade 0 >/dev/null
run_case clear remove 0 >/dev/null
run_case unreadable remove 1 >/dev/null

rm -f "${FAKE_ROOT}/usr/sbin/nginx"
mkdir -p "${FAKE_ROOT}/etc/nginx/conf.d"
printf 'events {}\n' > "${FAKE_ROOT}/etc/nginx/nginx.conf"
run_case_output="$(${RUN_SCRIPT} remove 2>&1)" || run_case_status=$?
run_case_status="${run_case_status:-0}"
if [[ "${run_case_status}" -ne 0 ]]; then
    printf 'FAIL: no-nginx fallback rejected a readable config without module\n%s\n' \
        "${run_case_output}" >&2
    exit 1
fi
printf 'PASS: no-nginx fallback allows a readable config without module\n' >&2

printf 'PASS: package removal guard lifecycle scenarios\n' >&2
