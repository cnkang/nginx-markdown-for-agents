#!/bin/bash
# ubuntu-postinstall.sh — enable the Ubuntu-native module snippet.
#
# The package installs the snippet in /usr/share/nginx/modules-available. This
# script links it into /etc/nginx/modules-enabled, which the Ubuntu nginx.conf
# includes at the main context. The link is created only when the directory
# exists, so a host without nginx-common keeps a clean package state.
#
# The script never reloads or restarts NGINX. The operator decides when to
# apply the configuration change.
#
# Exit codes:
#   0  Success, including the "nothing to do" paths

set -euo pipefail

# Executable-trust invariant: establish a trusted PATH before any command
# resolution, so a caller-controlled environment cannot influence which
# binaries run.
TRUSTED_PATH_ROOT=""
PATH="${TRUSTED_PATH_ROOT}/usr/sbin:${TRUSTED_PATH_ROOT}/usr/bin:${TRUSTED_PATH_ROOT}/sbin:${TRUSTED_PATH_ROOT}/bin"
export PATH

MODULES_AVAILABLE="/usr/share/nginx/modules-available/mod-markdown.conf"
MODULES_ENABLED_DIR="/etc/nginx/modules-enabled"
MODULES_ENABLED_LINK="${MODULES_ENABLED_DIR}/50-mod-markdown.conf"

info() {
    printf '[nginx-module-markdown-ubuntu] %s\n' "$1" >&2
}

case "${1:-configure}" in
    configure|1|2)
        if [[ ! -f "${MODULES_AVAILABLE}" ]]; then
            info "module snippet is missing; leaving the configuration unchanged"
            exit 0
        fi
        if [[ ! -d "${MODULES_ENABLED_DIR}" ]]; then
            info "nginx-common is not configured; run 'nginx -t' after you enable the module"
            exit 0
        fi
        # Skip an existing entry of any kind, including a dangling symlink:
        # the link is present, so the operator's state stays as it is.
        if [[ ! -e "${MODULES_ENABLED_LINK}" && ! -L "${MODULES_ENABLED_LINK}" ]]; then
            ln -s "${MODULES_AVAILABLE}" "${MODULES_ENABLED_LINK}"
        fi
        info "module enabled through ${MODULES_ENABLED_LINK}"
        info "run 'nginx -t' before you reload NGINX"
        ;;
    *)
        # Package managers pass lifecycle arguments this package does not use.
        # An unknown argument is not an installation failure.
        exit 0
        ;;
esac
