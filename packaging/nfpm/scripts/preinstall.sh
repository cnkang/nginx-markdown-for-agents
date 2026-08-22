#!/bin/bash
# preinstall.sh — nFPM pre-installation script for nginx-module-markdown-for-agents.
#
# Validates that the installed NGINX version exactly matches the version this
# module package was compiled against. NGINX dynamic modules require exact
# version matching; the core module loader rejects a module whose
# ngx_module_t.version differs from nginx_version before any signature or
# --with-compat consideration. Loading a module compiled for a different
# NGINX version causes immediate load failures or segfaults.
#
# Usage:
#   preinstall.sh [install|upgrade|1|2]
#
# Exit codes:
#   0  NGINX version matches exactly or NGINX is not yet installed
#   1  NGINX version mismatch (any full-version difference, including patch) — abort installation

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
        # Resolve NGINX ONCE and reuse the same absolute identity for the
        # existence check and the version probe.  A root-run maintainer
        # script must not re-resolve a bare "nginx" from PATH after the
        # discovery step: PATH is caller-controlled, so discovery and use
        # could land on different executables (executable-trust invariant).
        NGINX_BIN="$(command -v nginx 2>/dev/null || true)"
        if [[ -z "${NGINX_BIN}" ]]; then
            info "NGINX not yet installed; package dependency will handle installation."
            exit 0
        fi

        # Extract installed NGINX version from nginx -v output
        # Format: "nginx version: nginx/1.26.3"
        INSTALLED_NGINX_VERSION="$("${NGINX_BIN}" -v 2>&1 | sed -n 's|.*nginx/||p')"

        if [[ -z "${INSTALLED_NGINX_VERSION}" ]]; then
            warn "Could not determine installed NGINX version from 'nginx -v'."
            warn "Proceeding with installation — verify ABI compatibility manually."
            exit 0
        fi

        # NGINX dynamic modules require an EXACT version match: the core
        # loader compares ngx_module_t.version against nginx_version and
        # rejects any difference (including patch-level) before signature
        # checks.  --with-compat does not bypass this version check.
        if [[ "${INSTALLED_NGINX_VERSION}" != "${TARGET_NGINX_VERSION}" ]]; then
            info "============================================================"
            info "ERROR: NGINX version mismatch detected."
            info ""
            info "  Installed NGINX: ${INSTALLED_NGINX_VERSION}"
            info "  Module compiled for: ${TARGET_NGINX_VERSION}"
            info ""
            info "NGINX dynamic modules require the EXACT NGINX version they"
            info "were compiled against. Any difference, including a patch"
            info "release (e.g. 1.26.3 vs 1.26.4), causes the NGINX loader to"
            info "reject the module."
            info ""
            info "Please install the module package matching your NGINX"
            info "version, or upgrade/downgrade NGINX to ${TARGET_NGINX_VERSION}."
            info "============================================================"
            exit 1
        fi

        info "NGINX version ${INSTALLED_NGINX_VERSION} exactly matches module target ${TARGET_NGINX_VERSION}. Proceeding."
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
