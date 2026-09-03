#!/bin/bash
# Render the nFPM template with the immutable NGINX build version.
#
# Injects the shared trusted-executable prelude into maintainer script
# templates (%%TRUSTED_EXEC_PRELUDE%% placeholder) so preinstall and
# preremove stay single-source while the packaged copies remain
# self-contained, then substitutes %%NGINX_VERSION%%.

set -euo pipefail

usage() {
    printf 'Usage: %s SOURCE OUTPUT NGINX_VERSION\n' "$0" >&2
    return 2
}

if [[ $# -ne 3 ]]; then
    usage
    exit 2
fi

SOURCE_PATH="$1"
OUTPUT_PATH="$2"
NGINX_VERSION="$3"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRELUDE_PATH="${SCRIPT_DIR}/shared-prelude.sh"

if [[ ! -f "$PRELUDE_PATH" ]]; then
    printf 'ERROR: shared prelude not found: %s\n' "$PRELUDE_PATH" >&2
    exit 1
fi

if [[ "$SOURCE_PATH" == "$OUTPUT_PATH" ]] \
    || [[ -e "$SOURCE_PATH" && -e "$OUTPUT_PATH" \
        && "$SOURCE_PATH" -ef "$OUTPUT_PATH" ]]; then
    printf 'ERROR: SOURCE and OUTPUT must be different paths: %s\n' "$SOURCE_PATH" >&2
    exit 1
fi

if [[ ! -f "$SOURCE_PATH" ]]; then
    printf 'ERROR: nFPM template not found: %s\n' "$SOURCE_PATH" >&2
    exit 1
fi
if [[ ! "$NGINX_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'ERROR: invalid NGINX version: %s\n' "$NGINX_VERSION" >&2
    exit 1
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"
if grep -Fq '%%TRUSTED_EXEC_PRELUDE%%' "$SOURCE_PATH"; then
    # Inject the prelude first (buffered from its own file so embedded
    # newlines survive), then stamp the NGINX version.
    awk 'NR==FNR { prelude = prelude $0 ORS; next }
         /^%%TRUSTED_EXEC_PRELUDE%%$/ { printf "%s", prelude; next }
         { print }' "$PRELUDE_PATH" "$SOURCE_PATH" \
        | sed "s/%%NGINX_VERSION%%/${NGINX_VERSION}/g" \
        > "$OUTPUT_PATH"
else
    sed "s/%%NGINX_VERSION%%/${NGINX_VERSION}/g" "$SOURCE_PATH" \
        > "$OUTPUT_PATH"
fi
if grep -Fq '%%NGINX_VERSION%%' "$OUTPUT_PATH"; then
    printf 'ERROR: nFPM template still contains an unresolved version placeholder\n' >&2
    exit 1
fi
chmod 0644 "$OUTPUT_PATH"
