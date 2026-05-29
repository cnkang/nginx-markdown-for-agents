#!/bin/bash
# preinstall.sh — nFPM pre-installation script for nginx-module-markdown-for-agents.
#
# Validates that the installed NGINX version is ABI-compatible with this
# module package. NGINX dynamic modules require exact version matching;
# loading a module compiled for a different NGINX version causes segfaults
# or immediate load failures.
#
# Usage:
#   preinstall.sh [install|upgrade|1|2]
#
# Exit codes:
#   0  NGINX version matches or NGINX is not yet installed
#   1  NGINX major.minor branch mismatch — abort installation

set -e

##############################################################################
# Constants — target NGINX version baked in at package build time.
# nFPM expands ${NGINX_VERSION} from the build environment into the script
# content stored inside the package, so this is a literal string at install
# time, not a runtime env lookup.
##############################################################################

TARGET_NGINX_VERSION="%%NGINX_VERSION%%"

##############################################################################
# Helpers
##############################################################################

info() {
    printf '[nginx-module-markdown] %s\n' "$1" >&2
    return 0
}

warn() {
    printf '[nginx-module-markdown] WARNING: %s\n' "$1" >&2
    return 0
}

##############################################################################
# Main
##############################################################################

ACTION="${1:-install}"

case "$ACTION" in
    install|upgrade|1|2)
        # Check if nginx is already installed
        if ! command -v nginx >/dev/null 2>&1; then
            info "NGINX not yet installed; package dependency will handle installation."
            exit 0
        fi

        # Extract installed NGINX version from nginx -v output
        # Format: "nginx version: nginx/1.26.3"
        INSTALLED_NGINX_VERSION="$(nginx -v 2>&1 | sed -n 's|.*nginx/||p')"

        if [[ -z "${INSTALLED_NGINX_VERSION}" ]]; then
            warn "Could not determine installed NGINX version from 'nginx -v'."
            warn "Proceeding with installation — verify ABI compatibility manually."
            exit 0
        fi

        # Decompose versions into branch (major.minor) and patch
        TARGET_BRANCH="${TARGET_NGINX_VERSION%.*}"
        TARGET_PATCH="${TARGET_NGINX_VERSION##*.}"
        INSTALLED_BRANCH="${INSTALLED_NGINX_VERSION%.*}"
        INSTALLED_PATCH="${INSTALLED_NGINX_VERSION##*.}"

        # Branch mismatch is fatal — different ABI
        if [[ "${INSTALLED_BRANCH}" != "${TARGET_BRANCH}" ]]; then
            info "============================================================"
            info "ERROR: NGINX version branch mismatch detected."
            info ""
            info "  Installed NGINX: ${INSTALLED_NGINX_VERSION} (branch ${INSTALLED_BRANCH}.x)"
            info "  Module compiled for: ${TARGET_NGINX_VERSION} (branch ${TARGET_BRANCH}.x)"
            info ""
            info "NGINX dynamic modules require ABI compatibility with the"
            info "exact NGINX version branch they were compiled against."
            info ""
            info "Please install the module package matching your NGINX version,"
            info "or upgrade/downgrade NGINX to the ${TARGET_BRANCH}.x branch."
            info "============================================================"
            exit 1
        fi

        # Same branch — patch mismatch is a warning (proceed but inform)
        if [[ "${INSTALLED_PATCH}" != "${TARGET_PATCH}" ]]; then
            warn "Installed NGINX ${INSTALLED_NGINX_VERSION} patch differs from module target ${TARGET_NGINX_VERSION}."
            warn "Same branch (${TARGET_BRANCH}.x) — proceeding. Exact match recommended."
        else
            info "NGINX version ${INSTALLED_NGINX_VERSION} matches module target. Proceeding."
        fi
        ;;
    abort-upgrade|abort-remove|abort-deconfigure)
        # dpkg-specific lifecycle events — no action needed
        ;;
    *)
        # Accept unknown lifecycle arguments without failing (Rule 13: package
        # maintainer scripts must accept lifecycle arguments from all target
        # package managers)
        info "preinstall called with argument: $ACTION"
        ;;
esac

exit 0
