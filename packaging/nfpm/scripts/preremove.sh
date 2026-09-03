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

%%TRUSTED_EXEC_PRELUDE%%

##############################################################################
# Constants
##############################################################################

MODULE_REFERENCE_PATTERN='^[[:space:]]*load_module[[:space:]]+"?[^;]*ngx_http_markdown_filter_module\.so"?[[:space:]]*;'
FORCE_REMOVE_SENTINEL="/etc/nginx/markdown-module-force-remove"
# One-shot, content-bound forced-removal acknowledgement: the sentinel file
# must contain exactly this token (operator-written when acknowledging a
# forced removal) and is consumed on use, so a stale sentinel from a prior
# install/remove cycle can never silently re-authorize a later removal.
FORCE_REMOVE_TOKEN='nginx-markdown-module force-remove v1'

##############################################################################
# Helpers
##############################################################################

info() {
    printf '[preremove] %s\n' "$1" >&2
}

# force_remove_acknowledged returns 0 when the sentinel file content
# matches the required token byte-for-byte with no trailing newline.
# A newline-terminated file, a longer file, an empty file, or an
# unreadable file is a non-match (the guard stays active).
force_remove_acknowledged() {
    local line
    local read_status

    [[ -f "$FORCE_REMOVE_SENTINEL" ]] || return 1
    IFS= read -r line < "$FORCE_REMOVE_SENTINEL" 2>/dev/null
    read_status=$?
    # read returns 0 when the line ended with a newline (reject: the
    # token must not carry a trailing newline), 1 at EOF without a
    # newline (the exact-token shape), and 2 on read failure.
    [[ "$read_status" -eq 0 ]] && return 1
    [[ "$read_status" -eq 2 || -z "$line" ]] && return 1
    [[ "$line" == "$FORCE_REMOVE_TOKEN" ]]
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
    local standard_config_present=0

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
    standard_config_present=0
    for config_path in /etc/nginx/nginx.conf \
        /etc/nginx/conf.d/*.conf /etc/nginx/modules-enabled/*.conf; do
        if [[ -f "$config_path" ]]; then
            standard_config_present=1
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

    if [[ "$standard_config_present" -eq 0 ]]; then
        info "No trusted NGINX executable and no standard configuration files found; allowing clean removal."
        return 1
    fi

    info "Standard NGINX configuration files contain no module reference."
    # Without the executable, the complete include graph and active
    # configuration cannot be verified. The persistent force-removal sentinel
    # is the explicit operator path for an out-of-band verification.
    info "The complete NGINX include graph could not be verified."
    info "Create ${FORCE_REMOVE_SENTINEL} to acknowledge forced removal."
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
                if force_remove_acknowledged; then
                    info "WARNING: forced removal acknowledged (${FORCE_REMOVE_SENTINEL} present); skipping active-configuration verification."
                    info "WARNING: one-shot sentinel consumed; a future removal requires a fresh acknowledgement."
                    rm -f "$FORCE_REMOVE_SENTINEL" || {
                        info "ERROR: could not remove ${FORCE_REMOVE_SENTINEL}; delete it manually and retry removal."
                        return 1
                    }
                    return 0
                fi
                info "WARNING: ${FORCE_REMOVE_SENTINEL} exists but its content does not match the required token; ignoring it."
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
