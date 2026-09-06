# shellcheck shell=bash
# shared-prelude.sh — Trusted-executable contract shared by nFPM maintainer scripts.
#
# Single source of truth for the executable-trust invariant.  The nFPM build
# pipeline injects this whole file into preinstall/preremove templates at build
# time (render-nfpm-config.sh replaces the placeholder marker), so the
# packaged maintainer scripts stay self-contained while the repo keeps one
# canonical copy.  Never edit copies by hand: edit this file and re-render.
#
# This file is intentionally rendered into scripts and read by tests; it
# carries no shebang, defines no main path, and relies on the host script
# for `set -euo pipefail`.

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
    # canonicalize_path() produces: `cd -P`/`pwd -P` rewrite symlinked
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
