#!/bin/bash
# preremove.sh — nFPM pre-removal script for nginx-module-markdown-for-agents.
#
# Cleans up module configuration created by the package or its documented
# enablement flow before the package files are removed.
#
# The package itself never creates /etc/nginx state (postinstall only
# prints instructions), so this script must be conservative: it only
# removes a symlink at the well-known modules-enabled path when the
# symlink's target is EXACTLY this module's configuration file.  A
# regular file at that path is operator-owned configuration and is never
# deleted.  This mirrors the package's supported enablement flow without
# risking destructive removal of user configuration.
#
# Usage:
#   preremove.sh [remove|upgrade|1|2]
#
# Exit codes:
#   0  Always (removal must not fail due to cleanup issues)

set -e

##############################################################################
# Constants
##############################################################################

MODULES_ENABLED_DIR="/etc/nginx/modules-enabled"
SYMLINK_NAME="50-mod-markdown.conf"
SYMLINK_PATH="${MODULES_ENABLED_DIR}/${SYMLINK_NAME}"
MODULES_AVAILABLE_CONF="/usr/share/nginx/modules-available/mod-markdown.conf"

##############################################################################
# Helpers
##############################################################################

info() {
    printf '[preremove] %s\n' "$1" >&2
}

##############################################################################
# Main
##############################################################################

main() {
    info "Preparing to remove nginx-module-markdown-for-agents"

    # Remove the modules-enabled symlink ONLY when it points exactly at
    # this module's configuration file.  A symlink to any other target is
    # operator-owned and left untouched; a regular file is operator-owned
    # configuration and never deleted.
    if [ -L "${SYMLINK_PATH}" ]; then
        TARGET="$(readlink "${SYMLINK_PATH}" 2>/dev/null || true)"
        if [ "${TARGET}" = "${MODULES_AVAILABLE_CONF}" ]; then
            info "Removing module symlink: ${SYMLINK_PATH} -> ${TARGET}"
            rm -f "${SYMLINK_PATH}"
        else
            info "Not removing ${SYMLINK_PATH}: symlink target ${TARGET} is not this module's config (${MODULES_AVAILABLE_CONF})"
        fi
    elif [ -e "${SYMLINK_PATH}" ]; then
        info "Not removing ${SYMLINK_PATH}: regular file is operator-owned configuration"
    else
        info "No module symlink found at ${SYMLINK_PATH} (nothing to clean)"
    fi

    info "Pre-removal cleanup complete"
    return 0
}

main "$@"
exit 0
