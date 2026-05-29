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
# Environment (baked into package at build time via nFPM env expansion):
#   TARGET_NGINX_VERSION is derived from the package filename/metadata.
#
# Exit codes:
#   0  NGINX version matches or NGINX is not yet installed (dependency
#      resolution will handle installation)
#   1  NGINX version mismatch — abort installation

set -e

##############################################################################
# Constants — target NGINX version this module was compiled for
##############################################################################

# This is expanded by nFPM at package build time from the NGINX_VERSION env var.
TARGET_NGINX_VERSION="${NGINX_VERSION:-unknown}"

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
        INSTALLED_VERSION="$(nginx -v 2>&1 | sed -n 's|.*nginx/||p')"

        if [[ -z "${INSTALLED_VERSION}" ]]; then
            warn "Could not determine installed NGINX version from 'nginx -v'."
            warn "Proceeding with installation — verify ABI compatibility manually."
            exit 0
        fi

        # Extract major.minor from both versions for branch comparison
        INSTALLED_MINOR="${INSTALLED_VERSION%.*}"
        TARGET_MINOR="${TARGET_NGINX_VERSION%.*}"

        if [[ "${INSTALLED_MINOR}" != "${TARGET_MINOR}" ]]; then
            info "============================================================"
            info "ERROR: NGINX version mismatch detected."
            info ""
            info "  Installed NGINX: ${INSTALLED_VERSION} (branch ${INSTALLED_MINOR}.x)"
            info "  Module compiled for: ${TARGET_NGINX_VERSION} (branch ${TARGET_MINOR}.x)"
            info ""
            info "NGINX dynamic modules require ABI compatibility with the"
            info "exact NGINX version branch they were compiled against."
            info ""
            info "Please install the module package matching your NGINX version,"
            info "or upgrade/downgrade NGINX to the ${TARGET_MINOR}.x branch."
            info "============================================================"
            exit 1
        fi

        # Same minor branch — check exact patch version match
        if [[ "${INSTALLED_VERSION}" != "${TARGET_NGINX_VERSION}" ]]; then
            warn "Installed NGINX ${INSTALLED_VERSION} differs from module target ${TARGET_NGINX_VERSION}."
            warn "Same branch (${TARGET_MINOR}.x) — proceeding, but exact match is recommended."
        else
            info "NGINX version ${INSTALLED_VERSION} matches module target. Proceeding."
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
