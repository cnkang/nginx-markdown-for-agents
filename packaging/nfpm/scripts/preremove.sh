#!/bin/bash
# preremove.sh — nFPM pre-removal script for nginx-module-markdown-for-agents.
#
# Preserves any operator-managed module configuration before the package files
# are removed.
#
# The package itself never creates /etc/nginx state (postinstall only prints
# instructions), so this script must not remove anything from that tree.
# Operators may have created the documented symlink, or may have replaced it
# with a regular file; both forms remain operator-owned across remove and
# upgrade operations.
#
# Usage:
#   preremove.sh [remove|upgrade|1|2]
#
# Exit codes:
#   0  Always (removal must not fail due to cleanup issues)

set -e

##############################################################################
# Executable-trust invariant: establish trusted PATH before any command
# resolution. The literal empty assignment ensures a caller-controlled
# environment variable of the same name cannot influence the resolved set.
# Tests rewrite this literal only in a temporary fixture copy.
##############################################################################

TRUSTED_PATH_ROOT=""
PATH="${TRUSTED_PATH_ROOT}/usr/sbin:${TRUSTED_PATH_ROOT}/usr/bin:${TRUSTED_PATH_ROOT}/sbin:${TRUSTED_PATH_ROOT}/bin"
export PATH

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

    # This package never owns the enablement path.  Leave both symlinks and
    # regular files untouched, including a symlink to the package's own
    # configuration file.
    if [[ -L "${SYMLINK_PATH}" ]]; then
        info "Leaving operator-owned module symlink in place: ${SYMLINK_PATH}"
    elif [[ -e "${SYMLINK_PATH}" ]]; then
        info "Leaving operator-owned module configuration in place: ${SYMLINK_PATH}"
    else
        info "No module configuration found at ${SYMLINK_PATH}"
    fi

    info "Pre-removal cleanup complete"
    return 0
}

main "$@"
exit 0
