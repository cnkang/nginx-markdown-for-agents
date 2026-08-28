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
#   1  NGINX is unparseable/unusable or its version mismatches the target —
#      abort installation

set -euo pipefail

##############################################################################
# Executable-trust invariant: establish trusted PATH before any command
# resolution. The literal empty assignment ensures a caller-controlled
# environment variable of the same name cannot influence the resolved set.
# Tests rewrite this literal only in a temporary fixture copy.
##############################################################################

TRUSTED_PATH_ROOT=""
PATH="${TRUSTED_PATH_ROOT}/usr/sbin:${TRUSTED_PATH_ROOT}/usr/bin:${TRUSTED_PATH_ROOT}/sbin:${TRUSTED_PATH_ROOT}/bin"
export PATH
READLINK_BIN="$(command -v readlink 2>/dev/null || true)"
STAT_BIN="$(command -v stat 2>/dev/null || true)"

##############################################################################
# Trusted executable contract
##############################################################################

path_is_trusted_root() {
    local path="$1"
    local root
    local phys_root

    # Prefix matching must use the SAME physical spelling that
    # canonicalize_path() produces: `cd -P`/`pwd -P` rewrite symlinked
    # prefixes on some systems (for example /var -> /private/var), so a
    # logical trusted-root prefix would never match the resolved target.
    # If the root itself cannot be entered nothing beneath it can be a
    # usable executable; fall back to the literal spelling.
    phys_root="$(cd -P -- "${TRUSTED_PATH_ROOT}" 2>/dev/null && pwd -P)" \
        || phys_root="${TRUSTED_PATH_ROOT}"

    for root in \
        "${phys_root}/usr/sbin" \
        "${phys_root}/usr/bin" \
        "${phys_root}/sbin" \
        "${phys_root}/bin"; do
        case "$path" in
            "$root"/*)
                return 0
                ;;
            *)
                ;;
        esac
    done
    return 1
}

canonicalize_path() {
    local path="$1"
    local target=""
    local dir=""
    local file=""
    local hops=0

    # The absolute path requirement stands, but an external readlink is
    # only needed once a symlink is actually encountered: resolving a
    # plain executable through cd -P/pwd -P stays a shell builtin here.
    # A missing readlink therefore cannot silently skip link resolution;
    # it simply disallows traversing symlinks at all.
    [[ -n "$path" && "$path" = /* ]] || return 1
    while [[ -L "$path" ]]; do
        [[ -n "$READLINK_BIN" ]] || return 1
        hops=$((hops + 1))
        [[ "$hops" -le 40 ]] || return 1
        if ! target="$("$READLINK_BIN" "$path" 2>/dev/null)"; then
            return 1
        fi
        [[ -n "$target" ]] || return 1
        case "$target" in
            /*)
                path="$target"
                ;;
            *)
                dir="${path%/*}"
                [[ -n "$dir" ]] || dir="/"
                path="${dir}/${target}"
                ;;
        esac
    done

    dir="${path%/*}"
    [[ -n "$dir" ]] || dir="/"
    file="${path##*/}"
    [[ -n "$file" && -d "$dir" ]] || return 1
    if ! dir="$(cd -P -- "$dir" 2>/dev/null && pwd -P)"; then
        return 1
    fi
    printf '%s/%s\n' "${dir%/}" "$file"
    return 0
}

is_secure_path() {
    local path="$1"
    local current="/"
    local remainder=""
    local component=""
    local owner=""
    local mode=""

    [[ "$path" = /* && -n "$STAT_BIN" ]] || return 1
    remainder="${path#/}"
    while [[ -n "$remainder" ]]; do
        component="${remainder%%/*}"
        if [[ "$remainder" == */* ]]; then
            remainder="${remainder#*/}"
        else
            remainder=""
        fi
        [[ -n "$component" ]] || continue
        current="${current%/}/${component}"
        [[ -e "$current" ]] || return 1
        if ! owner="$("$STAT_BIN" -c '%u' "$current" 2>/dev/null)"; then
            owner="$("$STAT_BIN" -f '%u' "$current" 2>/dev/null)" || return 1
        fi
        if [[ "$EUID" -eq 0 ]]; then
            # A root-run maintainer script must traverse root-owned system
            # prefixes only; any other owner could replace the resolved
            # executable between check and use.
            [[ "$owner" == "0" ]] || return 1
        else
            # Outside a real install transaction the invoking user may own
            # staged trees, and legitimate system ancestors stay root-owned;
            # both are trusted principals. Any third-party owner is not.
            [[ "$owner" == "$EUID" || "$owner" == "0" ]] || return 1
        fi
        if ! mode="$("$STAT_BIN" -c '%a' "$current" 2>/dev/null)"; then
            mode="$("$STAT_BIN" -f '%Lp' "$current" 2>/dev/null)" || return 1
        fi
        [[ "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
        # No component may be group- or other-writable, except the classic
        # sticky, other-writable transit directories such as /tmp (mode
        # 1777): the sticky bit restricts removal and replacement of
        # entries to their owner, so traversal stays safe.
        if (( (8#$mode & 8#22) != 0 )); then
            if (( (8#$mode & 8#2) != 0 && (8#$mode & 8#1000) != 0 )); then
                :
            else
                return 1
            fi
        fi
    done
    return 0
}

resolve_trusted_nginx() {
    local candidate="$1"
    local resolved=""

    [[ -n "$candidate" && "$candidate" = /* ]] || return 1
    [[ -f "$candidate" && -x "$candidate" ]] || return 1
    resolved="$(canonicalize_path "$candidate")" || return 1
    [[ -f "$resolved" && -x "$resolved" ]] || return 1
    # The prefix check runs on the canonicalized identity only: a raw PATH
    # spelling can carry symlinked prefixes that no longer exist in the
    # resolved form, while trust and every safety property must hold for
    # the exact executable that will be executed.
    path_is_trusted_root "$resolved" || return 1
    is_secure_path "$candidate" || return 1
    is_secure_path "$resolved" || return 1
    printf '%s\n' "$resolved"
    return 0
}

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
        if [[ -z "${TARGET_NGINX_VERSION}" \
            || ! "${TARGET_NGINX_VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            warn "Package has no resolved NGINX target version; refusing installation."
            exit 1
        fi

        # Resolve NGINX ONCE and reuse the same absolute identity for the
        # existence check and the version probe.  A root-run maintainer
        # script must not re-resolve a bare "nginx" from PATH after the
        # discovery step: PATH is caller-controlled, so discovery and use
        # could land on different executables (executable-trust invariant).
        NGINX_CANDIDATE="$(command -v nginx 2>/dev/null || true)"
        if [[ -z "${NGINX_CANDIDATE}" ]]; then
            info "NGINX not found in trusted PATH (/usr/sbin:/usr/bin:/sbin:/bin); package dependency will handle installation."
            exit 0
        fi
        if ! NGINX_BIN="$(resolve_trusted_nginx "$NGINX_CANDIDATE")"; then
            warn "Resolved nginx executable is outside the trusted package-maintainer boundary; refusing installation."
            exit 1
        fi

        # Extract installed NGINX version from nginx -v output.  A failed
        # executable or an output format that cannot be parsed is unsafe for
        # a version-bound dynamic module and must abort installation.
        NGINX_VERSION_OUTPUT=""
        if ! NGINX_VERSION_OUTPUT="$("${NGINX_BIN}" -v 2>&1)"; then
            warn "Unable to execute '${NGINX_BIN} -v'; refusing installation."
            exit 1
        fi
        INSTALLED_NGINX_VERSION="$(printf '%s\n' "${NGINX_VERSION_OUTPUT}" \
            | sed -n 's|.*nginx/||p')"

        if [[ -z "${INSTALLED_NGINX_VERSION}" ]]; then
            warn "Could not determine installed NGINX version from 'nginx -v'."
            warn "Refusing installation because ABI compatibility cannot be verified."
            exit 1
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
