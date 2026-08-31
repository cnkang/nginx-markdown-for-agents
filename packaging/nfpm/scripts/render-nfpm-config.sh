#!/bin/bash
# Render the nFPM template with the immutable NGINX build version.

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
sed "s/%%NGINX_VERSION%%/${NGINX_VERSION}/g" "$SOURCE_PATH" \
    > "$OUTPUT_PATH"
if grep -F -q '%%NGINX_VERSION%%' "$OUTPUT_PATH"; then
    printf 'ERROR: nFPM template still contains an unresolved version placeholder\n' >&2
    exit 1
fi
chmod 0644 "$OUTPUT_PATH"
