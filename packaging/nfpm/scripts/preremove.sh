#!/bin/bash
# preremove.sh — nFPM pre-removal script for nginx-module-markdown-for-agents.
#
# Refuses final removal while an active NGINX configuration still loads the
# module.  Removing the shared object first would turn a valid configuration
# into a broken NGINX service.  The operator must disable the load_module line,
# run nginx -t, and retry; this script never edits configuration.
#
# Usage:
#   preremove.sh [remove|upgrade|1|2]
#
# Exit codes:
#   0  Upgrade or removal is safe to continue
#   1  Final removal is blocked or the active configuration could not be read

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
    local root_prefix

    # Prefix matching must use the SAME physical spelling that
    # canonicalize_path() produces: cd -P/pwd -P rewrite symlinked
    # prefixes on some systems (for example /var -> /private/var), so a
    # logical trusted-root prefix would never match the resolved target.
    # If the root itself cannot be entered nothing beneath it can be a
    # usable executable; fall back to the literal spelling.
    if [[ -n "${TRUSTED_PATH_ROOT}" ]]; then
        phys_root="$(cd -P -- "${TRUSTED_PATH_ROOT}" 2>/dev/null && pwd -P)" \
            || phys_root="${TRUSTED_PATH_ROOT}"
    else
        # An empty prefix denotes the real filesystem root.  `cd ""` enters
        # HOME in Bash, which would make every normal /usr/bin/nginx path
        # appear outside the trusted package-maintainer boundary.
        phys_root="/"
    fi

    root_prefix="${phys_root%/}"
    for root in \
        "${root_prefix}/usr/sbin" \
        "${root_prefix}/usr/bin" \
        "${root_prefix}/sbin" \
        "${root_prefix}/bin"; do
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
    # The candidate may traverse a standard system symlink such as
    # /usr/sbin -> /usr/bin.  Validate the canonical physical path instead
    # of rejecting the symlink's mode bits (which are conventionally 0777).
    is_secure_path "$resolved" || return 1
    printf '%s\n' "$resolved"
    return 0
}

##############################################################################
# Constants
##############################################################################

MODULE_REFERENCE_PATTERN='^[[:space:]]*load_module[[:space:]]+"?[^;]*ngx_http_markdown_filter_module\.so"?[[:space:]]*;'
FORCE_REMOVE_SENTINEL="/etc/nginx/markdown-module-force-remove"

##############################################################################
# Helpers
##############################################################################

info() {
    printf '[preremove] %s\n' "$1" >&2
}

configuration_loads_module() {
    local config_path
    local grep_status=0

    [[ -f "$1" ]] || return 1
    grep -E -q "$MODULE_REFERENCE_PATTERN" "$1" || grep_status=$?
    if [[ "$grep_status" -eq 0 ]]; then
        return 0
    fi
    if [[ "$grep_status" -gt 1 ]]; then
        return 2
    fi
    return 1
}

check_active_configuration() {
    local nginx_bin
    local nginx_candidate
    local nginx_dump
    local nginx_status=0
    local config_status=0

    nginx_candidate="$(command -v nginx 2>/dev/null || true)"
    if [[ -n "$nginx_candidate" ]]; then
        if ! nginx_bin="$(resolve_trusted_nginx "$nginx_candidate")"; then
            info "Unable to verify the nginx executable against the trusted package-maintainer boundary."
            return 2
        fi
        nginx_dump="$("$nginx_bin" -T 2>&1)" || nginx_status=$?
        # Do not use grep -q here: with pipefail, grep can exit after the
        # matching line and make printf report SIGPIPE, turning a real match
        # into a false negative for sufficiently large nginx -T output.
        if [[ "$nginx_status" -ne 0 ]]; then
            info "Unable to inspect the active NGINX configuration with the trusted binary."
            return 2
        fi
        if printf '%s\n' "$nginx_dump" \
            | grep -E "$MODULE_REFERENCE_PATTERN" >/dev/null; then
            return 0
        fi
        return 1
    fi

    # Without a trusted NGINX executable there is no reliable way to identify
    # the active configuration or expand its include graph.  Inspect the
    # fixed standard files for an immediate positive match, but do not treat
    # a clean result as proof that a custom executable/configuration is safe.
    # The persistent force-removal sentinel is the explicit operator path for
    # hosts where the include graph was verified out of band.
    for config_path in /etc/nginx/nginx.conf \
        /etc/nginx/conf.d/*.conf /etc/nginx/modules-enabled/*.conf; do
        if [[ -f "$config_path" ]]; then
            config_status=0
            configuration_loads_module "$config_path" || config_status=$?
            if [[ "$config_status" -eq 0 ]]; then
                return 0
            fi
            if [[ "$config_status" -eq 2 ]]; then
                info "Unable to read NGINX configuration entry '${config_path}'."
                return 2
            fi
        fi
    done

    # A missing executable, an absent standard file, or an unobserved include
    # path is unverifiable rather than evidence that no module is loaded.
    return 2
}

##############################################################################
main() {
    local action="${1:-remove}"
    local check_status

    case "$action" in
        upgrade|1|deconfigure|failed-upgrade|abort-upgrade|abort-remove|abort-deconfigure)
            info "Package upgrade/abort/deconfigure lifecycle: leaving NGINX configuration untouched."
            return 0
            ;;
        remove|0)
            if [[ -f "$FORCE_REMOVE_SENTINEL" ]]; then
                info "WARNING: forced removal acknowledged (${FORCE_REMOVE_SENTINEL} present); skipping active-configuration verification."
                info "WARNING: delete $FORCE_REMOVE_SENTINEL after removal to re-enable the guard."
                return 0
            fi
            ;;
        *)
            info "Unknown lifecycle action '${action}'; refusing final removal until it is explicit."
            return 1
            ;;
    esac

    info "Checking whether active NGINX configuration loads the module."
    check_status=0
    check_active_configuration || check_status=$?
    if [[ "$check_status" -eq 0 ]]; then
        info "Refusing removal: active NGINX configuration still contains a load_module directive for the module."
        info "Remove that directive, run 'nginx -t', then retry package removal."
        info "No NGINX configuration was modified automatically."
        return 1
    fi
    if [[ "$check_status" -eq 2 ]]; then
        info "Refusing removal because active NGINX configuration could not be verified."
        info "Disable the module explicitly, run 'nginx -t', then retry package removal."
        return 1
    fi

    info "No active NGINX module load was found; removal may continue."
    return 0
}

main "$@"
exit 0
