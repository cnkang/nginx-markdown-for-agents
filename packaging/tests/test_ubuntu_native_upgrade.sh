#!/usr/bin/env bash
# Exercise real Ubuntu package removal without touching the host system.
# Run only inside a disposable Docker container with an explicit mutation opt-in.
set -euo pipefail

# Resolve a supplied DEB before APT consumes it. APT treats a bare relative
# path such as dist/module.deb as a package name rather than a local file.
resolve_package_file() {
    if [[ ! -f "$1" ]]; then
        echo 'DEB file does not exist' >&2
        return 2
    fi
    realpath -- "$1"
    return $?
}

case "${1:-}" in
    --deb)
        [[ $# -eq 2 ]] || { echo 'usage: test_ubuntu_native_upgrade.sh --deb FILE' >&2; exit 2; }
        package_file="$2"
        ;;
    *)
        echo 'usage: test_ubuntu_native_upgrade.sh --deb FILE' >&2
        exit 2
        ;;
esac

if [[ ! -f /.dockerenv || "${PACKAGE_TEST_ALLOW_SYSTEM_MUTATION:-}" != 1 || "$EUID" -ne 0 ]]; then
    echo 'Run only as root in a disposable Docker container with PACKAGE_TEST_ALLOW_SYSTEM_MUTATION=1' >&2
    exit 2
fi
package_file="$(resolve_package_file "$package_file")"
service_policy_status=0
if [[ -x /usr/sbin/policy-rc.d ]]; then
    /usr/sbin/policy-rc.d nginx start || service_policy_status=$?
fi
[[ "$service_policy_status" -eq 101 ]] || {
    echo 'Container policy-rc.d must deny service starts with status 101' >&2
    exit 2
}
package_name="$(dpkg-deb -f "$package_file" Package)"
[[ "$package_name" == nginx-module-markdown-for-agents-ubuntu ]] || {
    echo 'Expected the Ubuntu-native module package' >&2
    exit 2
}
if dpkg-query -W -f='${Status}' "$package_name" 2>/dev/null | grep -q 'install ok installed'; then
    echo 'Refusing to replace a pre-existing module package' >&2
    exit 2
fi
for module_path in \
    /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so \
    /usr/share/nginx/modules-available/mod-markdown.conf \
    /etc/nginx/modules-enabled/50-mod-markdown.conf; do
    if [[ -e "$module_path" || -L "$module_path" ]]; then
        echo "Refusing to replace a pre-existing module path: $module_path" >&2
        exit 2
    fi
done
# Inspect procfs with shell builtins so minimal Ubuntu images need no procps.
# Refuse any existing NGINX process before changing system packages.
nginx_process_exists() {
    local process_file process_name
    for process_file in /proc/[0-9]*/comm; do
        [[ -r "$process_file" ]] || continue
        IFS= read -r process_name < "$process_file" || continue
        if [[ "$process_name" == nginx ]]; then
            return 0
        fi
    done
    return 1
}
if nginx_process_exists; then
    echo 'Refusing to modify packages while an NGINX process exists' >&2
    exit 2
fi

test_dir="$(mktemp -d /tmp/markdown-upgrade.XXXXXX)"
# These are the only test files. No NGINX process starts during this test.
cleanup() {
    rm -f "$test_dir/nginx.conf" "$test_dir/without-module.conf" "$test_dir/negative.log"
    rmdir "$test_dir"
    return $?
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

apt-get -s install nginx "$package_file"
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx "$package_file"
dpkg-query -W -f='${Package} ${Version} ${Provides} ${Depends}\n' nginx "$package_name"
[[ -d /etc/nginx/modules-enabled ]] || {
    echo 'Module installation did not create modules-enabled' >&2
    exit 1
}
[[ -L /etc/nginx/modules-enabled/50-mod-markdown.conf ]] || {
    echo 'Module installation did not create the enablement symlink' >&2
    exit 1
}
cat > "$test_dir/nginx.conf" <<'CONFIG'
error_log stderr;
load_module /usr/lib/nginx/modules/ngx_http_markdown_filter_module.so;
events {}
http {
    server {
        listen 127.0.0.1:19093;
        location / { markdown_filter on; }
    }
}
CONFIG
nginx -t -p "$test_dir/" -c "$test_dir/nginx.conf"
apt-get -s upgrade
apt-get -s remove "$package_name"
DEBIAN_FRONTEND=noninteractive apt-get remove -y "$package_name"
sed '/^load_module /d' "$test_dir/nginx.conf" > "$test_dir/without-module.conf"
if nginx -t -p "$test_dir/" -c "$test_dir/without-module.conf" > "$test_dir/negative.log" 2>&1; then
    echo 'NGINX unexpectedly accepted a directive without its module' >&2
    exit 1
fi
grep -F 'unknown directive "markdown_filter"' "$test_dir/negative.log"
sed '/location .*markdown_filter/d' "$test_dir/without-module.conf" > "$test_dir/nginx.conf"
nginx -t -p "$test_dir/" -c "$test_dir/nginx.conf"
echo 'PASS: package removal requires explicit removal of module directives'
