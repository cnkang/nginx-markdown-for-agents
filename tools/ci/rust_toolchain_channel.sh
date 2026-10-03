#!/usr/bin/env bash
# Print the Rust channel pinned by rust-toolchain.toml.
#
# rust-toolchain.toml is the single source of truth for the stable toolchain.
# `dtolnay/rust-toolchain` cannot read it -- the `toolchain` input is required
# and the action drives `rustup toolchain install` and `rustup default` from
# that input alone -- so every workflow resolved the channel by hand and a bump
# in one file could silently leave the workflows on another. This script is the
# single read both the workflows and the drift guard use.
#
# Only a stable release is accepted. The fuzz path pins its own dated nightly
# and deliberately does not come through here, so a manifest that resolves to a
# nightly -- or to anything else that is not a plain version -- is a mistake
# worth failing on: it would build the whole stable pipeline on the wrong
# toolchain, quietly.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFEST="${REPO_ROOT}/rust-toolchain.toml"

if [[ ! -f "${MANIFEST}" ]]; then
    echo "ERROR: rust-toolchain.toml not found at ${MANIFEST}" >&2
    exit 1
fi

# TOML allows single or double quotes, and either may be followed by a comment,
# so accept both forms rather than only the one this repository happens to use.
# `head -1` is applied to sed's own input rather than piped into it: a pipeline
# into `head` can hand sed a SIGPIPE under `pipefail` if the file ever grows
# past the point sed has written.
channel="$(sed -nE \
    "s/^[[:space:]]*channel[[:space:]]*=[[:space:]]*['\"]([^'\"]+)['\"].*/\\1/p" \
    "${MANIFEST}")"

if [[ -z "${channel}" ]]; then
    echo "ERROR: no channel pinned in ${MANIFEST}" >&2
    exit 1
fi

if [[ ! "${channel}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: ${MANIFEST} pins '${channel}', which is not a stable version" >&2
    echo "       expected MAJOR.MINOR.PATCH; the nightly fuzz path pins its own" >&2
    exit 1
fi

printf '%s\n' "${channel}"