#!/usr/bin/env bash
# Scan exactly the Git-tracked worktree content for secrets.

set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
scan_root="$(mktemp -d "${TMPDIR:-/tmp}/nginx-markdown-gitleaks.XXXXXX")"
file_list="$(mktemp "${TMPDIR:-/tmp}/nginx-markdown-gitleaks-files.XXXXXX")"

cleanup() {
    rm -rf "$scan_root"
    rm -f "$file_list"
}

trap cleanup EXIT

cd "$repo_root"
git ls-files -z | while IFS= read -r -d '' tracked_path; do
    if [[ -e "$tracked_path" || -L "$tracked_path" ]]; then
        printf '%s\0' "$tracked_path"
    fi
done > "$file_list"

if [[ ! -s "$file_list" ]]; then
    echo "ERROR: no Git-tracked files found for gitleaks scan" >&2
    exit 1
fi

# Both bsdtar (macOS) and GNU tar support NUL-delimited -T input.  Reading
# files from the worktree, rather than HEAD, includes tracked local edits while
# excluding ignored adapter state and other non-release files.
tar --null -T "$file_list" -cf - | tar -xf - -C "$scan_root"

if [[ -z "$(ls -A "$scan_root")" ]]; then
    echo "ERROR: gitleaks scan root is empty after extraction" >&2
    exit 1
fi

cd "$scan_root"
gitleaks detect \
    --source . \
    --no-git \
    --redact \
    --config "$repo_root/.gitleaks.toml" \
    --verbose
