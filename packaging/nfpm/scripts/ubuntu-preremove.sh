#!/bin/bash
# ubuntu-preremove.sh — remove the modules-enabled link before the package
# files are deleted.
#
# A dangling link in /etc/nginx/modules-enabled makes `nginx -t` fail with a
# missing module, so the link must go before the module file does. The script
# removes the link only when it points at this package's snippet.
#
# Exit codes:
#   0  Success, including the "nothing to do" paths

set -euo pipefail

TRUSTED_PATH_ROOT=""
PATH="${TRUSTED_PATH_ROOT}/usr/sbin:${TRUSTED_PATH_ROOT}/usr/bin:${TRUSTED_PATH_ROOT}/sbin:${TRUSTED_PATH_ROOT}/bin"
export PATH

MODULES_AVAILABLE="/usr/share/nginx/modules-available/mod-markdown.conf"
MODULES_ENABLED_LINK="/etc/nginx/modules-enabled/50-mod-markdown.conf"

case "${1:-remove}" in
    remove|upgrade|deconfigure|0|1)
        if [[ -L "${MODULES_ENABLED_LINK}" ]]; then
            target="$(readlink "${MODULES_ENABLED_LINK}")"
            if [[ "${target}" == "${MODULES_AVAILABLE}" ]]; then
                rm -f "${MODULES_ENABLED_LINK}"
            fi
        fi
        ;;
    *)
        exit 0
        ;;
esac
