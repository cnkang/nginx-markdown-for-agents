#!/bin/bash
# preremove.sh — nFPM pre-removal script for nginx-module-markdown-for-agents.
#
# Cleans up the module configuration symlink from /etc/nginx/modules-enabled/
# (Debian/Ubuntu pattern) before the package files are removed.
#
# Usage:
#   preremove.sh
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

    # Remove the modules-enabled symlink if it exists (Debian/Ubuntu pattern)
    if [ -L "${SYMLINK_PATH}" ]; then
        info "Removing module symlink: ${SYMLINK_PATH}"
        rm -f "${SYMLINK_PATH}"
    elif [ -f "${SYMLINK_PATH}" ]; then
        # Handle case where it's a regular file instead of a symlink
        info "Removing module config file: ${SYMLINK_PATH}"
        rm -f "${SYMLINK_PATH}"
    else
        info "No module symlink found at ${SYMLINK_PATH} (nothing to clean)"
    fi

    info "Pre-removal cleanup complete"
    return 0
}

main "$@"
exit 0
